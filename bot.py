"""B-Fix Store Bot v2 — نسخة محسنة وآمنة نسبياً لبوت متجر تيليجرام."""
import asyncio
import html
import logging
import os
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# لا تضع التوكن أو بيانات الدفع في هذا الملف. أضفها كمتغيرات بيئية عند التشغيل.
BOT_TOKEN = os.getenv("8299192931:AAHkXI_BLyoAp8TvrSCU9i_CnoDSyDFbTGA", "").strip()
ADMIN_ID = int(os.getenv("8218627841", "0") or 0)
DB_NAME = os.getenv("DB_NAME", "bfix_store.db")
WHATSAPP_LINK = os.getenv("WHATSAPP_LINK", "https://t.me/bfixSoftware")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/bfixSoftware")
PAYMENT_DETAILS = os.getenv(
    "PAYMENT_DETAILS",
    "تواصل مع الدعم للحصول على بيانات الدفع المحدثة، ثم أرسل إشعار التحويل مع الاسم والمبلغ ووسيلة الدفع.",
)
ENABLE_HEALTHCHECK = os.getenv("ENABLE_HEALTHCHECK", "false").lower() == "true"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger("bfix_store")

(
    ADMIN_USER_ID, ADMIN_AMOUNT, ADMIN_SEARCH, ADMIN_BROADCAST, ADMIN_SRV_CATEGORY,
    ADMIN_SRV_NAME, ADMIN_SRV_DESC, ADMIN_SRV_PRICE, ADMIN_SRV_DURATION,
    ADMIN_NEW_PRICE, ADMIN_CARD_CODE, ADMIN_CARD_AMOUNT, ADMIN_STOCK_QUANTITY,
    ADMIN_STOCK_KEY,
) = range(14)
(
    ADMIN_MAINTENANCE_MESSAGE, ADMIN_BROADCAST_TEXT, ADMIN_EDIT_FIELD,
    ADMIN_EDIT_VALUE, ADMIN_MANUAL_QUESTION, ADMIN_MANUAL_WAIT,
    ADMIN_MANUAL_COMPLETE, ADMIN_ORDER_MESSAGE,
) = range(14, 22)
USER_MANUAL_RESPONSE = 50

MAX_NAME = 80
MAX_DESCRIPTION = 1000
MAX_CODE = 120
MAX_STOCK_TEXT = 4000
MAX_STOCK_QUANTITY = 100
MAX_BROADCAST_LENGTH = 3500
MAX_ORDER_MESSAGE_LENGTH = 3500


# ========================= قاعدة البيانات =========================
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db_connect():
    conn = sqlite3.connect(DB_NAME, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def db_one(query, params=()):
    with db_connect() as conn:
        return conn.execute(query, params).fetchone()


def db_all(query, params=()):
    with db_connect() as conn:
        return conn.execute(query, params).fetchall()


def db_exec(query, params=()):
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(query, params)
        conn.commit()


def ensure_column(conn, table, column, declaration):
    fields = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in fields:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def init_db():
    """إنشاء القاعدة وترقية الجداول القديمة دون حذف بياناتها."""
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, balance REAL NOT NULL DEFAULT 0,
            balance_cents INTEGER NOT NULL DEFAULT 0, join_date TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0, price_cents INTEGER NOT NULL DEFAULT 0,
            duration TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'digital',
            is_active INTEGER NOT NULL DEFAULT 1)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
            product_key_id INTEGER, price_cents INTEGER, status TEXT NOT NULL, order_date TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cards (
            code TEXT PRIMARY KEY, amount REAL NOT NULL DEFAULT 0, amount_cents INTEGER,
            is_used INTEGER NOT NULL DEFAULT 0, used_by INTEGER, used_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS product_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER NOT NULL,
            key_text TEXT NOT NULL, is_sold INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL, transaction_type TEXT NOT NULL,
            reference TEXT, balance_after_cents INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS free_claims (
            user_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL, claimed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, service_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent', created_at TEXT NOT NULL,
            deleted_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS broadcast_deliveries (
            broadcast_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            message_id INTEGER, status TEXT NOT NULL, deleted_at TEXT,
            PRIMARY KEY (broadcast_id, user_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS manual_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER UNIQUE,
            user_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
            request_data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'بانتظار المراجعة',
            admin_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            completed_at TEXT)""")

        # ترقية من نسخة المستخدم الأصلية.
        ensure_column(conn, "users", "balance_cents", "INTEGER")
        ensure_column(conn, "services", "price_cents", "INTEGER")
        ensure_column(conn, "services", "is_active", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, "services", "execution_mode", "TEXT NOT NULL DEFAULT 'auto'")
        ensure_column(conn, "services", "request_prompt", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "services", "wait_message", "TEXT NOT NULL DEFAULT 'تم استلام طلبك، يرجى الانتظار.'")
        ensure_column(conn, "services", "completion_prompt", "TEXT NOT NULL DEFAULT 'تم تنفيذ طلبك بنجاح.'")
        ensure_column(conn, "orders", "product_key_id", "INTEGER")
        ensure_column(conn, "orders", "price_cents", "INTEGER")
        ensure_column(conn, "cards", "amount_cents", "INTEGER")
        ensure_column(conn, "cards", "used_by", "INTEGER")
        ensure_column(conn, "cards", "used_at", "TEXT")
        ensure_column(conn, "manual_orders", "order_id", "INTEGER")
        conn.execute("UPDATE users SET balance_cents = CAST(ROUND(COALESCE(balance,0)*100) AS INTEGER) WHERE balance_cents IS NULL")
        conn.execute("UPDATE services SET price_cents = CAST(ROUND(COALESCE(price,0)*100) AS INTEGER) WHERE price_cents IS NULL")
        conn.execute("UPDATE cards SET amount_cents = CAST(ROUND(COALESCE(amount,0)*100) AS INTEGER) WHERE amount_cents IS NULL")
        conn.execute("""UPDATE orders SET price_cents = COALESCE(
            (SELECT price_cents FROM services WHERE services.id=orders.service_id),0)
            WHERE price_cents IS NULL""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_services_category_active ON services(category,is_active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stock ON product_keys(service_id,is_sold)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_free_claims_service ON free_claims(service_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_broadcast ON broadcast_deliveries(broadcast_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_orders_status ON manual_orders(status,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_orders_user ON manual_orders(user_id,id DESC)")
        conn.execute("INSERT OR IGNORE INTO bot_settings (setting_key,setting_value,updated_at) VALUES ('maintenance_enabled','0',?)", (now_text(),))
        conn.execute("INSERT OR IGNORE INTO bot_settings (setting_key,setting_value,updated_at) VALUES ('maintenance_message','نعتذر، البوت تحت الصيانة حالياً. يرجى المحاولة لاحقاً.',?)", (now_text(),))
        conn.commit()


def cents_text(value):
    return f"{Decimal(int(value or 0))/Decimal(100):.2f}"


def parse_cents(text):
    try:
        amount = Decimal(text.strip().replace(",", "."))
        if not amount.is_finite() or amount <= 0:
            return None
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(amount * 100)
    except InvalidOperation:
        return None


def clean(text, maximum):
    text = text.strip()
    return text if text and len(text) <= maximum else None


def esc(value):
    return html.escape(str(value), quote=False)


def styled_button(text, callback_data=None, url=None, style=None):
    """ينشئ زرّاً مع لون Telegram قياسي اختياري؛ لا يدعم تيليجرام ألواناً مخصصة."""
    kwargs = {}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style in {"primary", "success", "danger"}:
        kwargs["api_kwargs"] = {"style": {f"bg_{style}": True}}
    return InlineKeyboardButton(text, **kwargs)


def is_admin(user_id):
    return ADMIN_ID > 0 and user_id == ADMIN_ID


def upsert_user(user_id, name):
    name = clean(name or "عميل", MAX_NAME) or "عميل"
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO users (user_id,name,balance,balance_cents,join_date)
            VALUES (?,?,0,0,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name""",
            (user_id, name, now_text()))
        conn.commit()


def get_setting(setting_key, default=""):
    row = db_one("SELECT setting_value FROM bot_settings WHERE setting_key=?", (setting_key,))
    return row["setting_value"] if row else default


def set_setting(setting_key, value):
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO bot_settings (setting_key,setting_value,updated_at)
            VALUES (?,?,?) ON CONFLICT(setting_key) DO UPDATE SET
            setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
            (setting_key, str(value), now_text()))
        conn.commit()


def maintenance_enabled():
    return get_setting("maintenance_enabled", "0") == "1"


def maintenance_message():
    return get_setting("maintenance_message", "نعتذر، البوت تحت الصيانة حالياً. يرجى المحاولة لاحقاً.")


def get_service(service_id, active=True):
    sql = """SELECT id,name,description,price_cents,duration,category,is_active,
        execution_mode,request_prompt,wait_message,completion_prompt
        FROM services WHERE id=?"""
    if active:
        sql += " AND is_active=1"
    return db_one(sql, (service_id,))


def stock_count(service_id):
    row = db_one("SELECT COUNT(*) AS total FROM product_keys WHERE service_id=? AND is_sold=0", (service_id,))
    return int(row["total"])


def redeem_card(user_id, code):
    """شحن ذري: لا يمكن استعمال الكود مرتين حتى مع وصول رسالتين متوازيتين."""
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        card = conn.execute("SELECT amount_cents FROM cards WHERE code=? AND is_used=0", (code,)).fetchone()
        user = conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not card or not user or not card["amount_cents"] or int(card["amount_cents"]) <= 0:
            conn.rollback()
            return False, "الكود غير صحيح أو مستخدم.", None
        amount = int(card["amount_cents"])
        change = conn.execute("""UPDATE cards SET is_used=1,used_by=?,used_at=?
            WHERE code=? AND is_used=0""", (user_id, now_text(), code))
        if change.rowcount != 1:
            conn.rollback()
            return False, "تم استخدام الكود للتو. أرسل كوداً آخر.", None
        balance = int(user["balance_cents"] or 0) + amount
        conn.execute("UPDATE users SET balance_cents=?,balance=? WHERE user_id=?", (balance, balance/100, user_id))
        conn.execute("""INSERT INTO transactions
            (user_id,amount_cents,transaction_type,reference,balance_after_cents,created_at)
            VALUES (?,?,'card_redeem',?,?,?)""", (user_id, amount, code, balance, now_text()))
        conn.commit()
        return True, "تم الشحن بنجاح.", balance
    except sqlite3.Error:
        conn.rollback()
        logger.exception("فشل شحن بطاقة")
        return False, "تعذر شحن الكود مؤقتاً.", None
    finally:
        conn.close()


def purchase(user_id, service_id):
    """يحجز المفتاح ويسجل الطلب ذرّياً؛ العرض المجاني متاح مرة واحدة لكل عميل."""
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        service = conn.execute(
            "SELECT id,name,price_cents,category FROM services WHERE id=? AND is_active=1",
            (service_id,),
        ).fetchone()
        user = conn.execute("SELECT name,balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not service or not user:
            conn.rollback()
            return False, "الخدمة لم تعد متاحة.", None

        price = int(service["price_cents"] or 0)
        is_free_offer = service["category"] == "free_offers"
        if (is_free_offer and price != 0) or (not is_free_offer and price <= 0):
            conn.rollback()
            return False, "إعداد سعر هذه الخدمة غير صالح. تواصل مع الدعم.", None
        if is_free_offer and conn.execute(
            "SELECT 1 FROM free_claims WHERE user_id=? AND service_id=?", (user_id, service_id)
        ).fetchone():
            conn.rollback()
            return False, "استلمت هذا العرض المجاني من قبل.", None
        if not is_free_offer and int(user["balance_cents"] or 0) < price:
            conn.rollback()
            return False, "رصيدك غير كافٍ لإتمام العملية.", None

        product = conn.execute("""SELECT id,key_text FROM product_keys
            WHERE service_id=? AND is_sold=0 ORDER BY id LIMIT 1""", (service_id,)).fetchone()
        if not product:
            conn.rollback()
            return False, "نفدت الكمية لهذه الخدمة. حاول لاحقاً.", None
        reserved = conn.execute("UPDATE product_keys SET is_sold=1 WHERE id=? AND is_sold=0", (product["id"],))
        if reserved.rowcount != 1:
            conn.rollback()
            return False, "تم استلام آخر كود للتو؛ أعد المحاولة.", None

        balance = int(user["balance_cents"] or 0) - price
        if price:
            debit = conn.execute("""UPDATE users SET balance_cents=?,balance=?
                WHERE user_id=? AND balance_cents>=?""", (balance, balance/100, user_id, price))
            if debit.rowcount != 1:
                conn.rollback()
                return False, "تعذر التحقق من الرصيد. أعد المحاولة.", None
        order = conn.execute("""INSERT INTO orders
            (user_id,service_id,product_key_id,price_cents,status,order_date)
            VALUES (?,?,?,?, 'مكتمل',?)""", (user_id, service_id, product["id"], price, now_text()))
        if is_free_offer:
            conn.execute("""INSERT INTO free_claims (user_id,service_id,order_id,claimed_at)
                VALUES (?,?,?,?)""", (user_id, service_id, order.lastrowid, now_text()))
            transaction_type, amount = "free_claim", 0
        else:
            transaction_type, amount = "purchase", -price
        conn.execute("""INSERT INTO transactions
            (user_id,amount_cents,transaction_type,reference,balance_after_cents,created_at)
            VALUES (?,?,?,?,?,?)""", (user_id, amount, transaction_type, f"order:{order.lastrowid}", balance, now_text()))
        conn.commit()
        return True, "تم استلام العرض المجاني بنجاح." if is_free_offer else "تم الشراء بنجاح.", {
            "order_id": order.lastrowid, "name": service["name"], "price": price,
            "key": product["key_text"], "balance": balance, "user_name": user["name"],
            "is_free_offer": is_free_offer,
        }
    except sqlite3.Error:
        conn.rollback()
        logger.exception("فشل طلب الخدمة %s", service_id)
        return False, "حدث خطأ مؤقت أثناء إتمام الطلب.", None
    finally:
        conn.close()


def create_manual_order(user_id, service_id, request_data):
    """يحفظ طلباً يحتاج تدخلاً يدوياً، ويخصم السعر مرة واحدة ضمن معاملة واحدة."""
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        service = conn.execute("""SELECT id,name,price_cents,category,execution_mode
            FROM services WHERE id=? AND is_active=1""", (service_id,)).fetchone()
        user = conn.execute("SELECT name,balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not service or not user or service["execution_mode"] != "manual":
            conn.rollback()
            return False, "الخدمة لم تعد متاحة للطلب اليدوي.", None
        price = int(service["price_cents"] or 0)
        is_free_offer = service["category"] == "free_offers"
        if (is_free_offer and price != 0) or (not is_free_offer and price <= 0):
            conn.rollback()
            return False, "إعداد سعر الخدمة غير صالح.", None
        if is_free_offer and conn.execute("SELECT 1 FROM free_claims WHERE user_id=? AND service_id=?", (user_id, service_id)).fetchone():
            conn.rollback()
            return False, "استلمت هذا العرض المجاني من قبل.", None
        if not is_free_offer and int(user["balance_cents"] or 0) < price:
            conn.rollback()
            return False, "رصيدك غير كافٍ لإتمام الطلب.", None
        balance = int(user["balance_cents"] or 0) - price
        if price:
            debit = conn.execute("""UPDATE users SET balance_cents=?,balance=?
                WHERE user_id=? AND balance_cents>=?""", (balance, balance/100, user_id, price))
            if debit.rowcount != 1:
                conn.rollback()
                return False, "تعذر التحقق من الرصيد. أعد المحاولة.", None
        order = conn.execute("""INSERT INTO orders
            (user_id,service_id,product_key_id,price_cents,status,order_date)
            VALUES (?,?,NULL,?,'بانتظار التنفيذ',?)""", (user_id, service_id, price, now_text()))
        manual = conn.execute("""INSERT INTO manual_orders
            (order_id,user_id,service_id,request_data,status,created_at,updated_at)
            VALUES (?,?,?,?,'بانتظار المراجعة',?,?)""",
            (order.lastrowid, user_id, service_id, request_data, now_text(), now_text()))
        if is_free_offer:
            conn.execute("INSERT INTO free_claims (user_id,service_id,order_id,claimed_at) VALUES (?,?,?,?)",
                (user_id, service_id, order.lastrowid, now_text()))
            transaction_type, amount = "free_manual_claim", 0
        else:
            transaction_type, amount = "manual_order", -price
        conn.execute("""INSERT INTO transactions
            (user_id,amount_cents,transaction_type,reference,balance_after_cents,created_at)
            VALUES (?,?,?,?,?,?)""", (user_id, amount, transaction_type, f"order:{order.lastrowid}", balance, now_text()))
        conn.commit()
        return True, "تم تسجيل طلبك بنجاح.", {
            "manual_id": manual.lastrowid, "order_id": order.lastrowid, "service_name": service["name"],
            "price": price, "balance": balance, "user_name": user["name"], "is_free_offer": is_free_offer,
        }
    except sqlite3.Error:
        conn.rollback()
        logger.exception("فشل إنشاء طلب يدوي")
        return False, "حدث خطأ مؤقت أثناء تسجيل الطلب.", None
    finally:
        conn.close()


def get_manual_order(manual_id):
    return db_one("""SELECT m.id,m.order_id,m.user_id,m.service_id,m.request_data,m.status,m.admin_note,
        m.created_at,m.updated_at,s.name AS service_name,s.wait_message,s.completion_prompt,u.name AS user_name
        FROM manual_orders m JOIN services s ON s.id=m.service_id JOIN users u ON u.user_id=m.user_id
        WHERE m.id=?""", (manual_id,))


def update_manual_order(manual_id, status, admin_note=None, completed=False):
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT order_id FROM manual_orders WHERE id=?", (manual_id,)).fetchone()
        if not row:
            conn.rollback()
            return False
        completed_at = now_text() if completed else None
        conn.execute("""UPDATE manual_orders SET status=?,admin_note=?,updated_at=?,
            completed_at=COALESCE(?,completed_at) WHERE id=?""",
            (status, admin_note, now_text(), completed_at, manual_id))
        order_status = "مكتمل" if completed else status
        conn.execute("UPDATE orders SET status=? WHERE id=?", (order_status, row["order_id"]))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.exception("فشل تحديث الطلب اليدوي")
        return False
    finally:
        conn.close()


def adjust_balance(user_id, delta, action):
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            conn.rollback()
            return False, "المستخدم غير موجود."
        balance = int(user["balance_cents"] or 0) + delta
        if balance < 0:
            conn.rollback()
            return False, "لا يمكن أن يصبح الرصيد سالباً."
        conn.execute("UPDATE users SET balance_cents=?,balance=? WHERE user_id=?", (balance,balance/100,user_id))
        conn.execute("""INSERT INTO transactions
            (user_id,amount_cents,transaction_type,reference,balance_after_cents,created_at)
            VALUES (?,?,?,'admin_adjustment',?,?)""", (user_id,delta,action,balance,now_text()))
        conn.commit()
        return True, cents_text(balance)
    except sqlite3.Error:
        conn.rollback()
        logger.exception("فشل تعديل الرصيد")
        return False, "خطأ في قاعدة البيانات."
    finally:
        conn.close()


# ========================= واجهة العميل =========================
def main_keyboard():
    return InlineKeyboardMarkup([
        [styled_button("الخدمات الرقمية ✨", callback_data="show_cat_digital", style="success"),
         styled_button("الاشتراكات 🚀", callback_data="show_cat_subscriptions", style="success")],
        [styled_button("العروض المجانية 🎁", callback_data="show_cat_free_offers", style="primary"),
         styled_button("عروض VIP 👑", callback_data="show_cat_vip", style="primary")],
        [styled_button("خدمة إيجار الأدوات 🛠️", callback_data="show_cat_rentals", style="success")],
        [styled_button("سجل طلباتي 🔄", callback_data="my_orders", style="primary"),
         styled_button("حسابي ⚡", callback_data="my_profile", style="primary")],
        [styled_button("شحن الرصيد بكود", callback_data="charge_account", style="success"),
         styled_button("تغذية حسابك", callback_data="fund_account", style="success")],
        [styled_button("واتساب 🌐", url=WHATSAPP_LINK, style="primary"),
         styled_button("الدعم 🛠️", url=SUPPORT_LINK, style="primary")],
        [styled_button("معلومات البوت ℹ️", callback_data="bot_info", style="danger")],
    ])


async def send_maintenance_notice(update, context):
    text = (
        "<b>⚙️ البوت تحت الصيانة المؤقتة</b>\n\n"
        f"{esc(maintenance_message())}\n\n"
        "<i>نعمل على تحسين الخدمة وسنعود قريباً. شكراً لصبرك.</i>"
    )
    if update.callback_query and update.callback_query.message:
        await update.callback_query.answer("البوت تحت الصيانة حالياً.", show_alert=True)
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML)
    elif update.effective_message:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def show_main(update, context):
    user = update.effective_user
    if not user:
        return
    upsert_user(user.id, user.first_name)
    if maintenance_enabled() and not is_admin(user.id):
        await send_maintenance_notice(update, context)
        return
    context.user_data.pop("waiting_card", None)
    context.user_data.pop("manual_service_id", None)
    text = f"<b>مرحباً {esc(user.first_name)}،</b>\n\nأهلاً بك في متجر B-Fix للخدمات والاشتراكات الرقمية. اختر القسم المطلوب من القائمة."
    if update.message:
        await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def start(update, context):
    await show_main(update, context)


async def main_handler(update, context):
    query = update.callback_query
    if not query or not query.message:
        return
    user_id = query.from_user.id
    upsert_user(user_id, query.from_user.first_name)
    data = query.data or ""
    if maintenance_enabled() and not is_admin(user_id):
        await send_maintenance_notice(update, context)
        return
    await query.answer()
    if data != "charge_account":
        context.user_data.pop("waiting_card", None)

    if data == "main_menu":
        await show_main(update, context)
    elif data == "my_profile":
        user = db_one("SELECT name,balance_cents FROM users WHERE user_id=?", (user_id,))
        await query.message.edit_text(
            f"<b>ملفك الشخصي</b>\n\nالاسم: {esc(user['name'])}\nالمعرف: <code>{user_id}</code>\nالرصيد: <b>{cents_text(user['balance_cents'])} $</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("تغذية الحساب", callback_data="fund_account")], [InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]),
            parse_mode=ParseMode.HTML)
    elif data == "charge_account":
        context.user_data["waiting_card"] = True
        await query.message.edit_text("أرسل <b>كود بطاقة الشحن</b> الآن، أو /cancel للإلغاء.", parse_mode=ParseMode.HTML)
    elif data == "fund_account":
        await query.message.edit_text("<b>تغذية الحساب</b>\n\nاعرض تفاصيل الدفع، وبعد التحويل أرسل الإشعار إلى الدعم لاعتماد الرصيد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("عرض تفاصيل الدفع", callback_data="pay_details")], [InlineKeyboardButton("شحن بكود", callback_data="charge_account")], [InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
    elif data == "pay_details":
        await query.message.edit_text(f"<b>تفاصيل الدفع</b>\n\n{esc(PAYMENT_DETAILS)}\n\nبعد التحويل، أرسل الإشعار للدعم مع الاسم والمبلغ ووسيلة الدفع.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("التواصل مع الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("رجوع", callback_data="fund_account")]]), parse_mode=ParseMode.HTML)
    elif data.startswith("show_cat_"):
        category = data.removeprefix("show_cat_")
        titles = {
            "digital": "الخدمات الرقمية",
            "subscriptions": "الاشتراكات",
            "free_offers": "العروض المجانية",
            "vip": "عروض VIP",
            "rentals": "إيجار الأدوات",
        }
        if category not in titles:
            await query.answer("قسم غير صالح.", show_alert=True)
            return
        services = db_all("SELECT id,name,price_cents,execution_mode FROM services WHERE category=? AND is_active=1 ORDER BY id DESC", (category,))
        if not services:
            await query.message.edit_text(f"لا توجد خدمات متاحة في قسم <b>{titles[category]}</b> حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("القائمة الرئيسية",callback_data="main_menu")]]),parse_mode=ParseMode.HTML)
            return
        buttons = []
        for service in services:
            quantity = stock_count(service["id"])
            status = "حسب الطلب" if service["execution_mode"] == "manual" else (f"متوفر: {quantity}" if quantity else "نفد")
            price_label = "مجاني" if category == "free_offers" else f"{cents_text(service['price_cents'])}$"
            button_style = "primary" if category in {"free_offers", "vip"} else "success"
            buttons.append([styled_button(f"{str(service['name'])[:30]} | {price_label} | {status}", callback_data=f"srv_{service['id']}", style=button_style)])
        buttons.append([styled_button("القائمة الرئيسية", callback_data="main_menu", style="danger")])
        await query.message.edit_text(f"<b>{titles[category]}</b>\n\nاختر خدمة لعرض التفاصيل.",reply_markup=InlineKeyboardMarkup(buttons),parse_mode=ParseMode.HTML)
    elif data.startswith("srv_"):
        try:
            service_id = int(data.removeprefix("srv_"))
        except ValueError:
            await query.answer("الخدمة غير صالحة.", show_alert=True)
            return
        service = get_service(service_id)
        if not service:
            await query.answer("الخدمة لم تعد متاحة.",show_alert=True)
            return
        is_free_offer = service["category"] == "free_offers"
        is_manual = service["execution_mode"] == "manual"
        price_label = "مجاني" if is_free_offer else f"{cents_text(service['price_cents'])}$"
        action_label = "إرسال الطلب ⚡" if is_manual else ("استلام العرض المجاني 🎁" if is_free_offer else "شراء الآن ⚡")
        action_style = "primary" if is_free_offer or is_manual else "success"
        available_label = "حسب الطلب" if is_manual else str(stock_count(service_id))
        await query.message.edit_text(
            f"<b>الخدمة:</b> {esc(service['name'])}\n\n<b>الوصف:</b> {esc(service['description'])}\n<b>المدة:</b> {esc(service['duration'])}\n<b>السعر:</b> {price_label}\n<b>الكمية المتوفرة:</b> {available_label}",
            reply_markup=InlineKeyboardMarkup([[styled_button(action_label, callback_data=f"buy_{service_id}", style=action_style)],[styled_button("رجوع للقسم", callback_data=f"show_cat_{service['category']}", style="danger")]]),parse_mode=ParseMode.HTML)
    elif data.startswith("buy_"):
        try:
            service_id = int(data.removeprefix("buy_"))
        except ValueError:
            await query.answer("الخدمة غير صالحة.",show_alert=True)
            return
        requested_service = get_service(service_id)
        if not requested_service:
            await query.answer("الخدمة لم تعد متاحة.", show_alert=True)
            return
        if requested_service["execution_mode"] == "manual":
            context.user_data["manual_service_id"] = service_id
            prompt = requested_service["request_prompt"] or "أرسل البيانات المطلوبة لتنفيذ الخدمة، مثل رقم الهاتف أو اسم المستخدم."
            await query.message.edit_text(
                f"<b>طلب خدمة: {esc(requested_service['name'])}</b>\n\n{esc(prompt)}\n\nأرسل البيانات في رسالة واحدة أو /cancel للإلغاء.",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, message, data_out = purchase(user_id, service_id)
        if not ok:
            await query.answer(message,show_alert=True)
            return
        try:
            await context.bot.send_message(user_id, f"تم تسليم معلومات خدمتك. احتفظ بها في مكان آمن:\n\n{data_out['key']}")
            await query.message.edit_text(
                f"<b>{'تم استلام العرض المجاني والتسليم بنجاح.' if data_out['is_free_offer'] else 'تم الشراء والتسليم بنجاح.'}</b>\n\nرقم الطلب: <code>{data_out['order_id']}</code>\nالخدمة: {esc(data_out['name'])}\nالقيمة: {'مجاني' if data_out['is_free_offer'] else cents_text(data_out['price']) + '$'}\nرصيدك المتبقي: {cents_text(data_out['balance'])}$",
                reply_markup=InlineKeyboardMarkup([[styled_button("القائمة الرئيسية",callback_data="main_menu",style="danger")]]),parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.exception("تم تسجيل الطلب لكن فشل تسليم الرسالة")
            await query.message.edit_text(f"تم تسجيل طلبك رقم <code>{data_out['order_id']}</code>، لكن تعذر التسليم الآلي. تواصل مع الدعم وأرسل رقم الطلب.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("الدعم",url=SUPPORT_LINK)]]),parse_mode=ParseMode.HTML)
        if ADMIN_ID:
            try:
                await context.bot.send_message(ADMIN_ID, f"<b>بيع آلي جديد</b>\nرقم الطلب: <code>{data_out['order_id']}</code>\nالعميل: {esc(data_out['user_name'])} (<code>{user_id}</code>)\nالخدمة: {esc(data_out['name'])}\nالمبلغ: {cents_text(data_out['price'])}$",parse_mode=ParseMode.HTML)
            except TelegramError:
                logger.exception("تعذر إرسال إشعار البيع للمدير")
    elif data == "my_orders":
        orders = db_all("""SELECT s.name,o.status,o.order_date,o.price_cents FROM orders o
            LEFT JOIN services s ON s.id=o.service_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 10""",(user_id,))
        text = "لا توجد طلبات مسجلة." if not orders else "<b>آخر 10 طلبات</b>\n\n"+"\n\n".join(f"• <b>{esc(row['name'] or 'خدمة مؤرشفة')}</b>\nالحالة: {esc(row['status'])} | المبلغ: {cents_text(row['price_cents'])}$\nالتاريخ: {esc(row['order_date'])}" for row in orders)
        await query.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("القائمة الرئيسية",callback_data="main_menu")]]),parse_mode=ParseMode.HTML)
    elif data == "bot_info":
        await query.message.edit_text("متجر B-Fix للخدمات والاشتراكات الرقمية مع تسليم آلي للمنتجات المتوفرة.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع",callback_data="main_menu")]]))


async def text_handler(update, context):
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    upsert_user(user_id, update.effective_user.first_name)
    if maintenance_enabled() and not is_admin(user_id):
        await send_maintenance_notice(update, context)
        return
    manual_service_id = context.user_data.get("manual_service_id")
    if isinstance(manual_service_id, int):
        request_data = clean(update.message.text, MAX_ORDER_MESSAGE_LENGTH)
        if not request_data:
            await update.message.reply_text("أرسل بيانات صالحة في رسالة واحدة أو /cancel للإلغاء.")
            return
        ok, message, order_data = create_manual_order(user_id, manual_service_id, request_data)
        if not ok:
            await update.message.reply_text(message)
            return
        context.user_data.pop("manual_service_id", None)
        service = get_service(manual_service_id)
        wait_text = service["wait_message"] or "تم استلام طلبك، يرجى الانتظار."
        await update.message.reply_text(
            f"<b>تم تسجيل طلبك بنجاح.</b>\n\nرقم الطلب: <code>{order_data['order_id']}</code>\nالخدمة: {esc(order_data['service_name'])}\n{esc(wait_text)}",
            parse_mode=ParseMode.HTML,
        )
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"<b>طلب يدوي جديد</b>\nرقم الطلب: <code>{order_data['order_id']}</code>\nالعميل: {esc(order_data['user_name'])} (<code>{user_id}</code>)\nالخدمة: {esc(order_data['service_name'])}\nالبيانات: {esc(request_data)}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                logger.exception("تعذر إرسال إشعار الطلب اليدوي للمدير")
        return
    if not context.user_data.get("waiting_card"):
        await update.message.reply_text("استخدم /start لعرض قائمة الخدمات.")
        return
    code = clean(update.message.text, MAX_CODE)
    if not code:
        await update.message.reply_text("أرسل كوداً صالحاً أو /cancel للإلغاء.")
        return
    ok, message, balance = redeem_card(user_id, code)
    if not ok:
        await update.message.reply_text(f"{message} أرسل كوداً آخر أو /cancel للإلغاء.")
        return
    context.user_data.pop("waiting_card", None)
    await update.message.reply_text(f"<b>{message}</b>\nرصيدك الجديد: <b>{cents_text(balance)}$</b>",parse_mode=ParseMode.HTML)


# ========================= لوحة الإدارة =========================
def admin_keyboard():
    return InlineKeyboardMarkup([
        [styled_button("إضافة رصيد",callback_data="adm_add_bal",style="success"),styled_button("خصم رصيد",callback_data="adm_sub_bal",style="danger")],
        [styled_button("إنشاء كود شحن",callback_data="adm_new_card",style="success"),styled_button("بحث",callback_data="adm_search",style="primary")],
        [styled_button("إدارة الخدمات والمخزون",callback_data="adm_srv_menu",style="primary")],
        [styled_button("إشعار جماعي جديد",callback_data="adm_broadcast",style="primary"),styled_button("إدارة الإشعارات",callback_data="adm_broadcasts",style="danger")],
        [styled_button("طلبات تحتاج تنفيذ",callback_data="adm_orders",style="success"),styled_button("وضع الصيانة",callback_data="adm_maintenance",style="danger")],
        [styled_button("إحصائيات",callback_data="adm_stats",style="primary")],
    ])


async def admin_panel(update, context):
    if not update.effective_user or not is_admin(update.effective_user.id):
        if update.message:
            await update.message.reply_text("غير مصرح لك باستخدام هذه اللوحة.")
        return
    if update.message:
        await update.message.reply_text("<b>لوحة إدارة متجر B-Fix</b>",reply_markup=admin_keyboard(),parse_mode=ParseMode.HTML)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text("<b>لوحة إدارة متجر B-Fix</b>",reply_markup=admin_keyboard(),parse_mode=ParseMode.HTML)


def services_keyboard(services, prefix, back="adm_srv_menu"):
    buttons = [[styled_button(str(row["name"])[:45],callback_data=f"{prefix}{row['id']}",style="primary")] for row in services]
    buttons.append([styled_button("رجوع",callback_data=back,style="danger")])
    return InlineKeyboardMarkup(buttons)


def service_manage_keyboard(service_id):
    return InlineKeyboardMarkup([
        [styled_button("تعديل الاسم",callback_data=f"editname_{service_id}",style="primary"),styled_button("تعديل الوصف",callback_data=f"editdesc_{service_id}",style="primary")],
        [styled_button("تعديل السعر",callback_data=f"editprice_{service_id}",style="success"),styled_button("تعديل المدة",callback_data=f"editduration_{service_id}",style="primary")],
        [styled_button("تغيير القسم",callback_data=f"editcat_{service_id}",style="primary"),styled_button("طريقة التنفيذ",callback_data=f"editmode_{service_id}",style="success")],
        [styled_button("إعداد طلب يدوي",callback_data=f"manualsetup_{service_id}",style="success")],
        [styled_button("أرشفة الخدمة",callback_data=f"archive_{service_id}",style="danger")],
        [styled_button("رجوع للخدمات",callback_data="adm_service_list",style="danger")],
    ])


async def admin_menu_handler(update, context):
    query = update.callback_query
    if not query or not query.message:
        return
    if not is_admin(query.from_user.id):
        await query.answer("غير مصرح لك.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    if data == "adm_main":
        await admin_panel(update, context)
    elif data == "adm_stats":
        users = db_one("SELECT COUNT(*) AS n FROM users")["n"]
        orders = db_one("SELECT COUNT(*) AS n FROM orders")["n"]
        stock = db_one("SELECT COUNT(*) AS n FROM product_keys WHERE is_sold=0")["n"]
        manual_pending = db_one("SELECT COUNT(*) AS n FROM manual_orders WHERE status!='مكتمل'")["n"]
        revenue = db_one("SELECT COALESCE(SUM(price_cents),0) AS n FROM orders")["n"]
        await query.message.edit_text(f"<b>إحصائيات المتجر</b>\n\nالمستخدمون: <b>{users}</b>\nالطلبات: <b>{orders}</b>\nطلبات يدوية معلقة: <b>{manual_pending}</b>\nالأكواد المتوفرة: <b>{stock}</b>\nإجمالي المبيعات: <b>{cents_text(revenue)}$</b>",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع",callback_data="adm_main",style="danger")]]),parse_mode=ParseMode.HTML)
    elif data == "adm_maintenance":
        enabled = maintenance_enabled()
        status = "مفعّل ⛔" if enabled else "متوقف ✅"
        await query.message.edit_text(f"<b>وضع الصيانة</b>\n\nالحالة الحالية: <b>{status}</b>\n\nالرسالة للعملاء:\n{esc(maintenance_message())}",reply_markup=InlineKeyboardMarkup([[styled_button("إيقاف وضع الصيانة",callback_data="maint_off",style="success")] if enabled else [styled_button("تفعيل وضع الصيانة",callback_data="maint_on",style="danger")],[styled_button("تعديل رسالة الصيانة",callback_data="maint_edit",style="primary")],[styled_button("رجوع",callback_data="adm_main",style="danger")]]),parse_mode=ParseMode.HTML)
    elif data in {"maint_on", "maint_off"}:
        set_setting("maintenance_enabled", "1" if data == "maint_on" else "0")
        await query.message.edit_text("تم تحديث وضع الصيانة.",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع لإعدادات الصيانة",callback_data="adm_maintenance",style="primary")]]))
    elif data == "adm_srv_menu":
        await query.message.edit_text("<b>إدارة الخدمات والمخزون</b>",reply_markup=InlineKeyboardMarkup([[styled_button("إضافة خدمة",callback_data="adm_add_srv",style="success")],[styled_button("كل الخدمات وتعديلها",callback_data="adm_service_list",style="primary")],[styled_button("إضافة أكواد",callback_data="adm_stock_list",style="success")],[styled_button("استعادة خدمة مؤرشفة",callback_data="adm_restore_list",style="primary")],[styled_button("رجوع",callback_data="adm_main",style="danger")]]),parse_mode=ParseMode.HTML)
    elif data in {"adm_service_list", "adm_stock_list", "adm_restore_list"}:
        active = 0 if data == "adm_restore_list" else 1
        services = db_all("SELECT id,name FROM services WHERE is_active=? ORDER BY id DESC",(active,))
        if not services:
            await query.message.edit_text("لا توجد خدمات مطابقة.",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع",callback_data="adm_srv_menu",style="danger")]]))
            return
        prefix = {"adm_service_list":"srvmanage_", "adm_stock_list":"addstock_", "adm_restore_list":"restore_"}[data]
        await query.message.edit_text("اختر الخدمة:",reply_markup=services_keyboard(services,prefix))
    elif data.startswith("srvmanage_"):
        service_id = data.removeprefix("srvmanage_")
        service = get_service(int(service_id), active=False) if service_id.isdigit() else None
        if not service:
            await query.answer("الخدمة غير موجودة.",show_alert=True)
            return
        await query.message.edit_text(f"<b>إدارة الخدمة</b>\n\nالاسم: {esc(service['name'])}\nالقسم: {esc(service['category'])}\nالسعر: {cents_text(service['price_cents'])}$\nطريقة التنفيذ: {esc(service['execution_mode'])}",reply_markup=service_manage_keyboard(int(service_id)),parse_mode=ParseMode.HTML)
    elif data.startswith("editcat_"):
        service_id = data.removeprefix("editcat_")
        if service_id.isdigit():
            await query.message.edit_text("اختر القسم الجديد:",reply_markup=InlineKeyboardMarkup([[styled_button("خدمات رقمية",callback_data=f"setcat_{service_id}_digital",style="success")],[styled_button("اشتراكات",callback_data=f"setcat_{service_id}_subscriptions",style="success")],[styled_button("عروض مجانية",callback_data=f"setcat_{service_id}_free_offers",style="primary")],[styled_button("عروض VIP",callback_data=f"setcat_{service_id}_vip",style="primary")],[styled_button("إيجار الأدوات",callback_data=f"setcat_{service_id}_rentals",style="success")]]))
    elif data.startswith("setcat_"):
        _, service_id, category = data.split("_", 2)
        if service_id.isdigit() and category in {"digital","subscriptions","free_offers","vip","rentals"}:
            price = 0 if category == "free_offers" else None
            if price is None:
                db_exec("UPDATE services SET category=? WHERE id=?",(category,int(service_id)))
            else:
                db_exec("UPDATE services SET category=?,price=0,price_cents=0 WHERE id=?",(category,int(service_id)))
            await query.message.edit_text("تم تغيير القسم بنجاح.",reply_markup=service_manage_keyboard(int(service_id)))
    elif data.startswith("editmode_"):
        service_id = data.removeprefix("editmode_")
        if service_id.isdigit():
            await query.message.edit_text("اختر طريقة التنفيذ:",reply_markup=InlineKeyboardMarkup([[styled_button("تسليم آلي من المخزون",callback_data=f"setmode_{service_id}_auto",style="success")],[styled_button("طلب بيانات وتنفيذ يدوي",callback_data=f"setmode_{service_id}_manual",style="primary")]]))
    elif data.startswith("setmode_"):
        _, service_id, mode = data.split("_", 2)
        if service_id.isdigit() and mode in {"auto","manual"}:
            db_exec("UPDATE services SET execution_mode=? WHERE id=?",(mode,int(service_id)))
            note = "يمكنك الآن إعداد سؤال العميل ورسائل المتابعة." if mode == "manual" else "ستعتمد الخدمة على مخزون الأكواد للتسليم الآلي."
            await query.message.edit_text(f"تم تغيير طريقة التنفيذ إلى {mode}.\n{note}",reply_markup=service_manage_keyboard(int(service_id)))
    elif data.startswith("archive_"):
        service_id = data.removeprefix("archive_")
        if service_id.isdigit():
            db_exec("UPDATE services SET is_active=0 WHERE id=?",(int(service_id),))
            await query.message.edit_text("تمت أرشفة الخدمة بأمان. بقيت الطلبات والأكواد محفوظة.",reply_markup=InlineKeyboardMarkup([[styled_button("إدارة الخدمات",callback_data="adm_srv_menu",style="primary")]]))
    elif data.startswith("restore_"):
        service_id = data.removeprefix("restore_")
        if service_id.isdigit():
            db_exec("UPDATE services SET is_active=1 WHERE id=?",(int(service_id),))
            await query.message.edit_text("تمت استعادة الخدمة.",reply_markup=InlineKeyboardMarkup([[styled_button("إدارة الخدمات",callback_data="adm_srv_menu",style="primary")]]))
    elif data == "adm_orders":
        orders = db_all("""SELECT m.id,s.name,u.name,m.status FROM manual_orders m
            JOIN services s ON s.id=m.service_id JOIN users u ON u.user_id=m.user_id
            WHERE m.status!='مكتمل' ORDER BY m.id DESC LIMIT 30""")
        if not orders:
            await query.message.edit_text("لا توجد طلبات يدوية معلقة.",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع",callback_data="adm_main",style="danger")]]))
            return
        buttons = [[styled_button(f"#{row['id']} | {str(row['name'])[:20]} | {str(row['status'])[:15]}",callback_data=f"manualord_{row['id']}",style="primary")] for row in orders]
        buttons.append([styled_button("رجوع",callback_data="adm_main",style="danger")])
        await query.message.edit_text("<b>الطلبات التي تحتاج تنفيذ</b>",reply_markup=InlineKeyboardMarkup(buttons),parse_mode=ParseMode.HTML)
    elif data.startswith("manualord_"):
        manual_id = data.removeprefix("manualord_")
        order = get_manual_order(int(manual_id)) if manual_id.isdigit() else None
        if not order:
            await query.answer("الطلب غير موجود.",show_alert=True)
            return
        text = f"<b>طلب يدوي #{order['order_id']}</b>\n\nالعميل: {esc(order['user_name'])} (<code>{order['user_id']}</code>)\nالخدمة: {esc(order['service_name'])}\nالحالة: {esc(order['status'])}\n\n<b>بيانات العميل:</b>\n{esc(order['request_data'])}"
        kb = InlineKeyboardMarkup([[styled_button("إرسال تحديث للعميل",callback_data=f"ordmsg_{manual_id}",style="primary")],[styled_button("إتمام الطلب وإشعار العميل",callback_data=f"orddone_{manual_id}",style="success")],[styled_button("رجوع للطلبات",callback_data="adm_orders",style="danger")]])
        await query.message.edit_text(text,reply_markup=kb,parse_mode=ParseMode.HTML)
    elif data.startswith("orddone_"):
        manual_id = data.removeprefix("orddone_")
        order = get_manual_order(int(manual_id)) if manual_id.isdigit() else None
        if not order or not update_manual_order(int(manual_id), "مكتمل", order["completion_prompt"], completed=True):
            await query.answer("تعذر تحديث الطلب.",show_alert=True)
            return
        try:
            await context.bot.send_message(order["user_id"],f"<b>تم تنفيذ طلبك بنجاح.</b>\n\nرقم الطلب: <code>{order['order_id']}</code>\n{esc(order['completion_prompt'])}",parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.exception("تعذر إشعار العميل بإتمام الطلب")
        await query.message.edit_text("تم إكمال الطلب وإرسال الإشعار للعميل.",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع للطلبات",callback_data="adm_orders",style="primary")]]))
    elif data == "adm_broadcasts":
        broadcasts = db_all("SELECT id,text,created_at,status FROM broadcasts ORDER BY id DESC LIMIT 20")
        if not broadcasts:
            await query.message.edit_text("لا توجد إشعارات محفوظة.",reply_markup=InlineKeyboardMarkup([[styled_button("رجوع",callback_data="adm_main",style="danger")]]))
            return
        buttons = [[styled_button(f"#{row['id']} | {str(row['text'])[:28]}",callback_data=f"bcast_{row['id']}",style="primary")] for row in broadcasts]
        buttons.append([styled_button("رجوع",callback_data="adm_main",style="danger")])
    
