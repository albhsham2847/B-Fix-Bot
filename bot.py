# -*- coding: utf-8 -*-
"""
B-Fix Smart Bot - Production v2
Single-file Telegram bot for Render + PostgreSQL.

Required environment variables on Render:
BOT_TOKEN
ADMIN_ID
DATABASE_URL

Optional:
WHATSAPP_LINK
SUPPORT_LINK
BOT_NAME

IMPORTANT:
- Never commit BOT_TOKEN or DATABASE_URL to GitHub.
- The database schema is created automatically.
- This file is intentionally self-contained; requirements.txt is still recommended
  for Render so the Python dependencies are installed:
    python-telegram-bot>=21,<23
    psycopg2-binary>=2.9,<3
"""

import os
import logging
import asyncio
import threading
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2 import pool

from telegram import (
    Update,
    InlineKeyboardButton as TelegramInlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bfix")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# روابط موحدة: استخدمها في جميع أزرار الدعم والتواصل ولا تكرر الروابط داخل الواجهات.
WHATSAPP_LINK = os.getenv(
    "WHATSAPP_LINK",
    "https://wa.me/967777728478",
).strip()
ADMIN_CONTACT_LINK = os.getenv(
    "ADMIN_CONTACT_LINK",
    "https://t.me/bfixSoftware",
).strip()
SUPPORT_LINK = os.getenv("SUPPORT_LINK", ADMIN_CONTACT_LINK).strip()
FORCED_CHANNEL_LINK = os.getenv(
    "FORCED_CHANNEL_LINK",
    "https://t.me/+0QKwgEMQwHg2Y2U0",
).strip()
# ضع chat_id للقناة في Render فقط لتفعيل تحقق الاشتراك الحقيقي؛ رابط دعوة وحده لا يكفي للتحقق.
FORCED_CHANNEL_CHAT_ID = os.getenv("FORCED_CHANNEL_CHAT_ID", "").strip()
BOT_NAME = os.getenv("BOT_NAME", "𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing.")

# ---------------------------------------------------------------------------
# Render health server
# ---------------------------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"B-Fix Smart Bot is alive.")

    def log_message(self, fmt, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server listening on %s", port)
    server.serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

DB_POOL = None


def init_pool():
    global DB_POOL
    DB_POOL = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=8,
        dsn=DATABASE_URL,
    )


def db_execute(query, params=(), fetch=False, fetchall=False):
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            result = None
            if fetchall:
                result = cur.fetchall()
            elif fetch:
                result = cur.fetchone()
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)


def init_db():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            username TEXT DEFAULT '',
            balance NUMERIC(14,2) NOT NULL DEFAULT 0,
            join_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_blocked BOOLEAN NOT NULL DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS categories (
            key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            category_key TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            subscription_duration TEXT DEFAULT '',
            activation_time TEXT DEFAULT '',
            price NUMERIC(14,2) NOT NULL DEFAULT 0,
            delivery_mode TEXT NOT NULL DEFAULT 'manual',
            needs_email BOOLEAN NOT NULL DEFAULT FALSE,
            needs_note BOOLEAN NOT NULL DEFAULT FALSE,
            needs_phone BOOLEAN NOT NULL DEFAULT FALSE,
            file_id TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id SERIAL PRIMARY KEY,
            service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            code_text TEXT NOT NULL,
            is_sold BOOLEAN NOT NULL DEFAULT FALSE,
            sold_to BIGINT,
            sold_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cards (
            code TEXT PRIMARY KEY,
            amount NUMERIC(14,2) NOT NULL,
            is_used BOOLEAN NOT NULL DEFAULT FALSE,
            used_by BIGINT,
            used_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            total NUMERIC(14,2) NOT NULL DEFAULT 0,
            customer_email TEXT DEFAULT '',
            customer_note TEXT DEFAULT '',
            customer_phone TEXT DEFAULT '',
            admin_note TEXT DEFAULT '',
            delivered_text TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forced_channels (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            link TEXT NOT NULL,
            chat_id TEXT DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS custom_buttons (
            btn_key TEXT PRIMARY KEY,
            btn_text TEXT NOT NULL,
            btn_action TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS payment_methods (
            key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            details TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS operation_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            admin_id BIGINT,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topup_receipts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
            payment_key TEXT NOT NULL,
            payment_title TEXT NOT NULL,
            receipt_number TEXT DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'قيد المراجعة ⏳',
            requested_amount NUMERIC(14,2),
            approved_amount NUMERIC(14,2),
            admin_note TEXT DEFAULT '',
            reviewed_by BIGINT,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]

    for sql in statements:
        db_execute(sql)

    # ترقية غير مدمرة لقواعد البيانات المنشأة بإصدار أقدم.
    # نضيف الأعمدة التي يعتمد عليها هذا الإصدار فقط؛ لا نحذف صفوفاً أو جداول.
    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance NUMERIC(14,2) NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS join_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS category_key TEXT DEFAULT 'digital'",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS subscription_duration TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS activation_time TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS price NUMERIC(14,2) NOT NULL DEFAULT 0",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS delivery_mode TEXT NOT NULL DEFAULT 'manual'",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS needs_email BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS needs_note BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS needs_phone BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS file_id TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
        "UPDATE services SET active=TRUE WHERE active IS NULL",
        "ALTER TABLE services ALTER COLUMN active SET DEFAULT TRUE",
        "UPDATE categories SET active=TRUE WHERE active IS NULL",
        "ALTER TABLE categories ALTER COLUMN active SET DEFAULT TRUE",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS is_sold BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS sold_to BIGINT",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS sold_at TIMESTAMP",
        "ALTER TABLE cards ADD COLUMN IF NOT EXISTS is_used BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE cards ADD COLUMN IF NOT EXISTS used_by BIGINT",
        "ALTER TABLE cards ADD COLUMN IF NOT EXISTS used_at TIMESTAMP",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'قيد المراجعة'",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total NUMERIC(14,2) NOT NULL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_note TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS admin_note TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_text TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS receipt_number TEXT DEFAULT ''",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS photo_file_id TEXT DEFAULT ''",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'قيد المراجعة ⏳'",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS requested_amount NUMERIC(14,2)",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS approved_amount NUMERIC(14,2)",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS admin_note TEXT DEFAULT ''",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS reviewed_by BIGINT",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
        "ALTER TABLE topup_receipts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        # توافق قواعد البيانات السابقة لشاشة قنوات الاشتراك الإجباري.
        "ALTER TABLE forced_channels ADD COLUMN IF NOT EXISTS id BIGSERIAL",
        "ALTER TABLE forced_channels ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'قناة'",
        "ALTER TABLE forced_channels ADD COLUMN IF NOT EXISTS link TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE forced_channels ADD COLUMN IF NOT EXISTS chat_id TEXT DEFAULT ''",
        "ALTER TABLE forced_channels ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
        "UPDATE forced_channels SET id=DEFAULT WHERE id IS NULL",
        "UPDATE forced_channels SET active=TRUE WHERE active IS NULL",
        # توافق سجل العمليات مع القواعد القديمة من دون حذف أي سجل موجود.
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS id BIGSERIAL",
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS user_id BIGINT",
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS admin_id BIGINT",
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS details TEXT DEFAULT ''",
        "ALTER TABLE operation_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "UPDATE operation_log SET id=DEFAULT WHERE id IS NULL",
        # إعدادات واجهة العميل: تعديل النص والإجراء والإخفاء بدون حذف الصفوف.
        "ALTER TABLE custom_buttons ADD COLUMN IF NOT EXISTS btn_action TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE custom_buttons ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE",
        "UPDATE custom_buttons SET is_visible=TRUE WHERE is_visible IS NULL",
    ]
    for sql in migrations:
        db_execute(sql)
    log.info("Database schema verified and safely upgraded.")

    categories = [
        ("digital", "⚡ الأدوات والبوكسات", "اشتراكات الأدوات والبوكسات التي تحتاج تفعيلًا.", 1),
        ("subscriptions", "🔵 الاشتراكات", "اشتراكات فورية وأكواد مخزون.", 2),
        ("rentals", "🔧 إيجار الأدوات والبوكسات", "إيجار أدوات مع تسليم بيانات الدخول.", 3),
        ("vip", "💎 عروض VIP", "خدمات VIP وطلبات خاصة.", 4),
        ("free", "🎁 عروض مجانية", "ملفات وصور وفيديو ونصوص مجانية.", 5),
    ]
    for row in categories:
        db_execute(
            """
            INSERT INTO categories(key,title,description,sort_order)
            VALUES(%s,%s,%s,%s)
            ON CONFLICT(key) DO NOTHING
            """,
            row,
        )

    buttons = [
        ("cat_digital", "⚡ الأدوات والبوكسات", "cat:digital"),
        ("cat_subscriptions", "🔵 الاشتراكات", "cat:subscriptions"),
        ("cat_rentals", "🔧 إيجار الأدوات والبوكسات", "cat:rentals"),
        ("cat_vip", "💎 عروض VIP", "cat:vip"),
        ("cat_free", "🎁 عروض مجانية", "cat:free"),
        ("my_orders", "📦 طلباتي", "orders"),
        ("my_profile", "👤 حسابي", "profile"),
        ("fund_account", "💰 تغذية حسابك", "fund"),
        ("support", "🆘 الدعم", "support"),
        ("channel_link", "📢 قناة البوت", FORCED_CHANNEL_LINK),
        ("whatsapp_link", "🌐 واتساب", WHATSAPP_LINK),
        ("about", "ℹ️ عن المتجر", "about"),
    ]
    for row in buttons:
        db_execute(
            """
            INSERT INTO custom_buttons(btn_key,btn_text,btn_action)
            VALUES(%s,%s,%s)
            ON CONFLICT(btn_key) DO NOTHING
            """,
            row,
        )

    settings = [
        ("maintenance", "0"),
        ("maintenance_message", "البوت قيد الصيانة حاليًا. يرجى المحاولة لاحقًا."),
    ]
    for row in settings:
        db_execute(
            """
            INSERT INTO settings(key,value)
            VALUES(%s,%s)
            ON CONFLICT(key) DO NOTHING
            """,
            row,
        )

    # لا يتم إدراج قناة الاشتراك تلقائياً إلا عند تزويد chat_id صالح؛
    # رابط الدعوة وحده لا يمكن لـ Telegram استخدامه للتحقق من العضوية.
    if valid_chat_id(FORCED_CHANNEL_CHAT_ID):
        exists = db_execute(
            "SELECT id FROM forced_channels WHERE chat_id=%s LIMIT 1",
            (FORCED_CHANNEL_CHAT_ID,),
            fetch=True,
        )
        if not exists:
            db_execute(
                "INSERT INTO forced_channels(name,link,chat_id) VALUES(%s,%s,%s)",
                ("📢 قناة B-Fix", FORCED_CHANNEL_LINK, FORCED_CHANNEL_CHAT_ID),
            )

    # Payment methods from the previous bot.
    payments = [
        ("jeep", "🔹 محفظة جيب", "📱 رقم الحساب المعتمد:\n580300\n\nقم بالتحويل ثم أرسل السند للدعم عبر واتساب."),
        ("jawali", "🔹 جوالي", "📱 رقم الحساب المعتمد:\n777728478\n\nقم بالتحويل ثم أرسل السند للدعم عبر واتساب."),
        ("onecash", "🔹 وان كاش", "📱 رقم الحساب المعتمد:\n178109713\n\nقم بالتحويل ثم أرسل السند للدعم عبر واتساب."),
        ("kuraimi", "🏦 بنك الكريمي", "🇾🇪 يمني: 3204168937\n🇸🇦 سعودي: 3204433991\n💵 دولار: 3191718649\n\nأرسل السند للدعم عبر واتساب."),
        ("binance", "🟡 Binance ID", "🟡 Binance ID:\n1063050653\n\nبعد التحويل أرسل الإثبات للدعم."),
        ("visa", "💳 VISA Card", "💳 رقم البطاقة:\n4909800019663092\n\nبعد الدفع أرسل الإثبات للدعم."),
    ]
    for i, row in enumerate(payments):
        db_execute(
            """
            INSERT INTO payment_methods(key,title,details,sort_order)
            VALUES(%s,%s,%s,%s)
            ON CONFLICT(key) DO NOTHING
            """,
            (*row, i + 1),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now():
    return datetime.now()


def money(value):
    return f"{Decimal(str(value)):.2f}"


AUTO_MAINTENANCE_MESSAGE = (
    "نعتذر، المتجر تحت الصيانة حاليًا لتحسين الخدمة. "
    "يرجى المحاولة لاحقًا، وشكرًا لتفهمك."
)


def styled_button(style, *args, **kwargs):
    """Create a styled button with a safe fallback for older Telegram libraries."""
    plain_kwargs = dict(kwargs)
    styled_kwargs = dict(kwargs)
    styled_kwargs["style"] = style
    try:
        return TelegramInlineKeyboardButton(*args, **styled_kwargs)
    except TypeError:
        return TelegramInlineKeyboardButton(*args, **plain_kwargs)


def green_button(*args, **kwargs):
    """Green button for normal actions in all supported clients."""
    return styled_button("success", *args, **kwargs)


def red_button(*args, **kwargs):
    """Red button for back, cancel, and destructive actions in all supported clients."""
    return styled_button("danger", *args, **kwargs)


def admin_navigation_rows(back_callback="adm:home", back_label="لوحة المشرف"):
    """Return red navigation rows for every administrative screen."""
    rows = []
    if back_callback and back_callback != "adm:home":
        rows.append([red_button(f"↩️ {back_label}", callback_data=back_callback)])
    rows.append([
        red_button("🔴 لوحة المشرف", callback_data="adm:home"),
        red_button("🏠 الرئيسية", callback_data="main"),
    ])
    return rows


# Route all ordinary buttons through the unified green constructor. Navigation,
# cancellation, and destructive actions call red_button explicitly.
InlineKeyboardButton = green_button


def add_user(user):
    db_execute(
        """
        INSERT INTO users(user_id,name,username)
        VALUES(%s,%s,%s)
        ON CONFLICT(user_id)
        DO UPDATE SET name=EXCLUDED.name, username=EXCLUDED.username
        """,
        (user.id, user.first_name or "", user.username or ""),
    )


def get_user(user_id):
    return db_execute(
        "SELECT user_id,name,balance,is_blocked FROM users WHERE user_id=%s",
        (user_id,),
        fetch=True,
    )


def get_setting(key, default=""):
    row = db_execute("SELECT value FROM settings WHERE key=%s", (key,), fetch=True)
    return row[0] if row else default


def set_setting(key, value):
    db_execute(
        """
        INSERT INTO settings(key,value) VALUES(%s,%s)
        ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
        """,
        (key, str(value)),
    )


def log_operation(user_id, action, details="", admin_id=None):
    db_execute(
        """
        INSERT INTO operation_log(user_id,admin_id,action,details)
        VALUES(%s,%s,%s,%s)
        """,
        (user_id, admin_id, action, details),
    )


OPERATION_LABELS = {
    "topup_receipt_submitted": "إرسال سند تحويل",
    "topup_approved": "قبول سند وشحن الرصيد",
    "topup_rejected": "رفض سند تحويل",
    "stock_purchase": "شراء خدمة وتسليم فوري",
    "manual_order": "إنشاء طلب يحتاج تنفيذًا",
    "order_cancelled": "إلغاء طلب وإعادة الرصيد",
    "admin_delivery": "تسليم طلب من الإدارة",
    "broadcast_sent": "إرسال إشعار جماعي",
}

OPERATION_DETAIL_LABELS = {
    "order": "رقم الطلب",
    "service": "رقم الخدمة",
    "price": "السعر",
    "receipt": "رقم السند",
    "payment": "وسيلة الدفع",
    "amount": "مبلغ الشحن",
    "refund": "المبلغ المُعاد",
    "note": "ملاحظة الإدارة",
    "sent": "الإرسال الناجح",
    "failed": "الإرسال الفاشل",
}


def arabic_operation_type(action):
    return OPERATION_LABELS.get(action, f"عملية نظام: {action or 'غير محددة'}")


def arabic_operation_details(details):
    """Convert the compact audit data into readable Arabic without changing stored records."""
    details = (details or "").strip()
    if not details:
        return "لا توجد تفاصيل إضافية."
    rendered = []
    for item in details.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            label = OPERATION_DETAIL_LABELS.get(key.strip().lower(), key.strip())
            rendered.append(f"• {label}: {value.strip() or '—'}")
        else:
            rendered.append(f"• {item}")
    return "\n".join(rendered) if rendered else "لا توجد تفاصيل إضافية."


def format_operation_time(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "غير متاح")


def maintenance_active():
    return get_setting("maintenance", "0") == "1"


def is_admin(user_id):
    return user_id == ADMIN_ID


async def allowed(update, context, admin_ok=False):
    user = update.effective_user
    if not user:
        return False

    if is_admin(user.id) and admin_ok:
        return True

    if maintenance_active() and not is_admin(user.id):
        text = get_setting(
            "maintenance_message",
            "البوت قيد الصيانة حاليًا. يرجى المحاولة لاحقًا.",
        )
        if update.callback_query:
            await update.callback_query.answer(f"⚙️ {text}", show_alert=True)
        elif update.message:
            await update.message.reply_text(f"⚙️ {text}")
        return False

    record = get_user(user.id)
    if record and record[3]:
        if update.message:
            await update.message.reply_text("🚫 تم إيقاف حسابك. تواصل مع الإدارة.")
        return False

    return True


def valid_chat_id(value):
    value = (value or "").strip()
    return bool(re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,32}", value))


def normalized_chat_id(value):
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() else value


def member_is_active(member):
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator", "owner"}:
        return True
    # العضو المقيّد قد يبقى عضواً فعلياً في القناة.
    return status == "restricted" and bool(getattr(member, "is_member", False))


async def subscription_ok(update, context):
    """Forced subscription is disabled: every customer can use the store directly."""
    return True


def button_config(key, fallback_text, fallback_action):
    row = db_execute(
        "SELECT btn_text,btn_action,COALESCE(is_visible,TRUE) FROM custom_buttons WHERE btn_key=%s",
        (key,),
        fetch=True,
    )
    if not row:
        return fallback_text, fallback_action, True
    text = row[0] if len(row) > 0 else fallback_text
    action = row[1] if len(row) > 1 else fallback_action
    visible = row[2] if len(row) > 2 else True
    return text or fallback_text, action or fallback_action, bool(visible)


def button_text(key, fallback):
    return button_config(key, fallback, "")[0]


def configured_main_button(key, fallback_text, fallback_action):
    text, action, visible = button_config(key, fallback_text, fallback_action)
    if not visible:
        return None
    if action.startswith(("https://", "http://")):
        return green_button(text, url=action)
    return green_button(text, callback_data=action)


def add_button_row(keyboard, *buttons):
    row = [button for button in buttons if button is not None]
    if row:
        keyboard.append(row)


ALLOWED_CUSTOM_BUTTON_CALLBACKS = {
    "cat:digital", "cat:subscriptions", "cat:rentals", "cat:vip", "cat:free",
    "orders", "profile", "fund", "support", "about",
}


def valid_custom_button_action(action):
    action = (action or "").strip()
    return action in ALLOWED_CUSTOM_BUTTON_CALLBACKS or action.startswith(("https://", "http://"))


def visible_main_button(key, fallback_text, callback_data=None, url=None):
    """Keep fixed core routes intact while allowing only their label/visibility to be managed."""
    text, _stored_action, visible = button_config(key, fallback_text, callback_data or url or "")
    if not visible:
        return None
    if url:
        return green_button(text, url=url)
    return green_button(text, callback_data=callback_data)


def main_keyboard():
    # لا تعتمد إجراءات الأقسام على قيمة قابلة للتعديل في قاعدة البيانات؛
    # لذلك تبقى الأزرار الأساسية عاملة حتى لو كانت بيانات الإصدارات القديمة ناقصة.
    keyboard = []
    add_button_row(
        keyboard,
        visible_main_button("cat_digital", "⚡ الأدوات والبوكسات", callback_data="cat:digital"),
        visible_main_button("cat_subscriptions", "🔵 الاشتراكات", callback_data="cat:subscriptions"),
    )
    add_button_row(keyboard, visible_main_button("cat_rentals", "🔧 إيجار الأدوات والبوكسات", callback_data="cat:rentals"))
    add_button_row(
        keyboard,
        visible_main_button("cat_vip", "💎 عروض VIP", callback_data="cat:vip"),
        visible_main_button("cat_free", "🎁 عروض مجانية", callback_data="cat:free"),
    )
    add_button_row(
        keyboard,
        visible_main_button("my_orders", "📦 طلباتي", callback_data="orders"),
        visible_main_button("my_profile", "👤 حسابي", callback_data="profile"),
    )
    add_button_row(
        keyboard,
        visible_main_button("fund_account", "💰 تغذية حسابك", callback_data="fund"),
        visible_main_button("support", "🆘 الدعم", callback_data="support"),
    )
    add_button_row(
        keyboard,
        visible_main_button("channel_link", "📢 قناة البوت", url=FORCED_CHANNEL_LINK),
        visible_main_button("whatsapp_link", "🌐 واتساب", url=WHATSAPP_LINK),
    )
    add_button_row(keyboard, visible_main_button("about", "ℹ️ عن المتجر", callback_data="about"))
    return InlineKeyboardMarkup(keyboard)


async def send_or_edit(update, text, markup=None):
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update, context, admin_ok=True):
        return
    if not await subscription_ok(update, context):
        return

    user = update.effective_user
    add_user(user)

    text = (
        f"✨ ━━━━━ ❲ {BOT_NAME} ❳ ━━━━━ ✨\n\n"
        f"👋 أهلًا بك يا [{user.first_name}](tg://user?id={user.id})\n\n"
        "🛒 **متجر الخدمات الرقمية الاحترافي**\n"
        "⚡ أدوات وبوكسات\n"
        "🔵 اشتراكات فورية\n"
        "🔧 إيجار أدوات\n"
        "💎 خدمات VIP\n"
        "🎁 عروض مجانية\n\n"
        "اختر القسم المطلوب من القائمة 👇"
    )
    await send_or_edit(update, text, main_keyboard())


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "check_sub":
        if await subscription_ok(update, context):
            await q.answer("✅ تم التحقق بنجاح.", show_alert=True)
            await start(update, context)
        return

    if not await allowed(update, context):
        return
    if not await subscription_ok(update, context):
        return

    user_id = q.from_user.id

    if data == "main":
        context.user_data.clear()
        await start(update, context)
        return

    if data.startswith("flowcancel:"):
        context.user_data.clear()
        destination = data.split(":", 1)[1]
        if destination == "fund":
            await payment_methods(update)
        else:
            await start(update, context)
        return

    if data.startswith("cat:"):
        await show_category(update, data.split(":", 1)[1])
        return

    if data.startswith("service:"):
        await show_service(update, int(data.split(":")[1]))
        return

    if data.startswith("buy:"):
        await begin_order(update, context, int(data.split(":")[1]))
        return

    if data == "profile":
        await profile(update)
        return

    if data == "orders":
        await orders_page(update)
        return

    if data.startswith("order:"):
        await order_page(update, int(data.split(":")[1]))
        return

    if data == "fund":
        await payment_methods(update)
        return

    if data.startswith("pay:"):
        await payment_detail(update, data.split(":")[1])
        return

    if data.startswith("receipt_start:"):
        await receipt_start(update, context, data.split(":", 1)[1])
        return

    if data == "support":
        await support_page(update)
        return

    if data == "about":
        await send_or_edit(
            update,
            "🌟 **B-Fix Software**\n\n"
            "متجر آلي للخدمات الرقمية، الأدوات والبوكسات، الاشتراكات، "
            "الإيجارات، عروض VIP والعروض المجانية.\n\n"
            "🔒 تتم متابعة الطلبات من خلال نظام طلبات وسجل عمليات.\n"
            "🛠️ الدعم متاح عبر القنوات الرسمية.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK)],
                [InlineKeyboardButton("🛠️ الدعم", url=SUPPORT_LINK)],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
        return

    if data == "admin":
        await admin_panel(update, context)
        return

    if data.startswith("adm:"):
        await admin_callback(update, context)
        return

    if data.startswith("approve:"):
        await admin_approve_order(update, context, int(data.split(":")[1]))
        return

    if data.startswith("reject:"):
        await admin_reject_order(update, context, int(data.split(":")[1]))
        return

    if data.startswith("deliver:"):
        await admin_start_delivery(update, context, int(data.split(":")[1]))
        return

    if data.startswith("done:"):
        await admin_finish_delivery(update, context, int(data.split(":")[1]))
        return

    if data.startswith("cancelorder:"):
        await admin_cancel_order(update, context, int(data.split(":")[1]))
        return

    if data.startswith("carduse:"):
        return


# ---------------------------------------------------------------------------
# Customer sections
# ---------------------------------------------------------------------------

CATEGORY_INFO = {
    "digital": ("⚡ الأدوات والبوكسات", "أدوات وبوكسات رقمية وخدمات تفعيل."),
    "subscriptions": ("🔵 الاشتراكات", "اشتراكات وخدمات دورية."),
    "rentals": ("🔧 إيجار الأدوات", "خدمات إيجار الأدوات والبوكسات."),
    "vip": ("💎 عروض VIP", "خدمات وعروض VIP."),
    "free": ("🎁 العروض المجانية", "العروض والخدمات المجانية."),
}


async def show_category(update, category):
    cat = CATEGORY_INFO.get(category)
    if not cat:
        await send_or_edit(
            update,
            "❌ هذا القسم غير متاح.",
            InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
        )
        return

    try:
        services = db_execute(
            """
            SELECT id,name,price,delivery_mode,needs_email,needs_phone
            FROM services
            WHERE LOWER(TRIM(COALESCE(category_key,'')))=%s
              AND COALESCE(active,TRUE)=TRUE
            ORDER BY id DESC
            """,
            (category,),
            fetchall=True,
        )
    except Exception as exc:
        log.exception("Unable to load category %s: %s", category, exc)
        await send_or_edit(
            update,
            "⚠️ تعذر تحميل خدمات هذا القسم مؤقتاً. جرّب مرة أخرى بعد اكتمال إعادة النشر.",
            InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
        )
        return

    if not services:
        await send_or_edit(
            update,
            f"📂 **{cat[0]}**\n\n🚧 لا توجد خدمات مضافة حاليًا.",
            InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
        )
        return

    keyboard = []
    for sid, name, price, mode, needs_email, needs_phone in services:
        stock = db_execute(
            "SELECT COUNT(*) FROM inventory WHERE service_id=%s AND is_sold=FALSE",
            (sid,),
            fetch=True,
        )[0]
        if mode == "stock":
            status = f"🟢 {stock}" if stock else "🔴 نفد المخزون"
        else:
            status = "🟢 متاح"
        keyboard.append([
            InlineKeyboardButton(
                f"▪️ {name} | {money(price)}$ | {status}",
                callback_data=f"service:{sid}",
            )
        ])

    keyboard.append([red_button("🏠 الرئيسية", callback_data="main")])
    await send_or_edit(
        update,
        f"📑 **{cat[0]}**\n\n{cat[1]}\n\n👇 اختر الخدمة:",
        InlineKeyboardMarkup(keyboard),
    )


async def show_service(update, sid):
    srv = db_execute(
        """
        SELECT id,category_key,name,description,subscription_duration,
               activation_time,price,delivery_mode,needs_email,needs_note,
               needs_phone
        FROM services WHERE id=%s AND COALESCE(active,TRUE)=TRUE
        """,
        (sid,),
        fetch=True,
    )
    if not srv:
        await send_or_edit(
            update,
            "❌ الخدمة غير موجودة.",
            InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
        )
        return

    stock = db_execute(
        "SELECT COUNT(*) FROM inventory WHERE service_id=%s AND is_sold=FALSE",
        (sid,),
        fetch=True,
    )[0]

    text = (
        f"📌 **{srv[2]}**\n\n"
        f"📝 **الوصف:** {srv[3] or 'غير محدد'}\n"
        f"⏳ **مدة الاشتراك:** {srv[4] or 'حسب الخدمة'}\n"
        f"⚡ **مدة التفعيل/التسليم:** {srv[5] or 'حسب الخدمة'}\n"
        f"💵 **السعر:** {money(srv[6])}$\n"
    )
    if srv[7] == "stock":
        text += f"📦 **المخزون المتوفر:** {stock}\n"
    if srv[8]:
        text += "📧 **يتطلب إيميل العميل.**\n"
    if srv[9]:
        text += "📝 **يمكنك إرسال ملاحظة مع الطلب.**\n"
    if srv[10]:
        text += "📱 **يتطلب رقمًا مع الطلب.**\n"

    if srv[1] == "rentals":
        text += (
            "\n⚠️ **ملاحظة:** سيتم إرسال الإيميل والباسورد الخاصين بك "
            "خلال 5 إلى 10 دقائق. ويُحتسب وقت الإيجار من وقت استلامك للطلب."
        )

    keyboard = [
        [green_button("🟢 طلب الخدمة الآن", callback_data=f"buy:{sid}")],
        [red_button("↩️ رجوع للقسم", callback_data=f"cat:{srv[1]}")],
        [red_button("🏠 الرئيسية", callback_data="main")],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


# ---------------------------------------------------------------------------
# Customer profile/orders/support/payments
# ---------------------------------------------------------------------------

async def profile(update):
    user = get_user(update.effective_user.id)
    text = (
        "👤 **حسابي**\n\n"
        f"▪️ الاسم: {user[1]}\n"
        f"▪️ الآيدي: `{user[0]}`\n"
        f"▪️ الرصيد: **{money(user[2])}$**\n"
        f"▪️ حالة الحساب: {'🟢 فعال' if not user[3] else '🔴 موقوف'}"
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 تغذية الحساب", callback_data="fund")],
            [InlineKeyboardButton("📦 طلباتي", callback_data="orders")],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )


async def orders_page(update):
    rows = db_execute(
        """
        SELECT o.id,COALESCE(s.name,'خدمة محذوفة'),o.status,o.total,o.created_at
        FROM orders o LEFT JOIN services s ON s.id=o.service_id
        WHERE o.user_id=%s
        ORDER BY o.id DESC LIMIT 15
        """,
        (update.effective_user.id,),
        fetchall=True,
    )
    if not rows:
        text = "📦 **طلباتي**\n\nلا توجد طلبات حتى الآن."
        keyboard = [[red_button("🏠 الرئيسية", callback_data="main")]]
    else:
        text = "📦 **آخر الطلبات:**\n\n"
        keyboard = []
        for oid, name, status, total, created in rows:
            text += f"▪️ #{oid} — {name}\n   {status} — {money(total)}$\n\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"🔎 تفاصيل الطلب #{oid}",
                    callback_data=f"order:{oid}",
                )
            ])
        keyboard.append([red_button("🏠 الرئيسية", callback_data="main")])

    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def order_page(update, oid):
    row = db_execute(
        """
        SELECT o.id,s.name,o.status,o.total,o.customer_email,
               o.customer_note,o.customer_phone,o.admin_note,
               o.delivered_text,o.created_at
        FROM orders o LEFT JOIN services s ON s.id=o.service_id
        WHERE o.id=%s AND o.user_id=%s
        """,
        (oid, update.effective_user.id),
        fetch=True,
    )
    if not row:
        await update.callback_query.answer("❌ الطلب غير موجود.", show_alert=True)
        return

    text = (
        f"📦 **الطلب #{row[0]}**\n\n"
        f"🛒 الخدمة: {row[1] or 'غير متاحة'}\n"
        f"📌 الحالة: {row[2]}\n"
        f"💵 المبلغ: {money(row[3])}$\n"
        f"📧 الإيميل: {row[4] or '—'}\n"
        f"📱 الرقم: {row[6] or '—'}\n"
        f"📝 الملاحظة: {row[5] or '—'}\n"
        f"⏰ التاريخ: {row[9]}\n"
    )
    if row[7]:
        text += f"\n📣 **ملاحظة الإدارة:** {row[7]}\n"
    if row[8]:
        text += f"\n🎁 **التسليم:**\n{row[8]}\n"

    keyboard = [
        [InlineKeyboardButton("🆘 لم أستلم طلبي / تواصل مع الإدارة", url=WHATSAPP_LINK)],
            [red_button("↩️ الطلبات", callback_data="orders")],
            [red_button("🏠 الرئيسية", callback_data="main")],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def payment_methods(update):
    rows = db_execute(
        """
        SELECT key,title FROM payment_methods
        WHERE active=TRUE ORDER BY sort_order,key
        """,
        fetchall=True,
    )
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"pay:{key}")]
        for key, title in rows
    ]
    keyboard.append([
        InlineKeyboardButton("🟢 شحن عبر كود بطاقة", callback_data="customer_card")
    ])
    keyboard.append([red_button("🏠 الرئيسية", callback_data="main")])

    await send_or_edit(
        update,
        "💰 **تغذية حسابك**\n\n"
        "اختر وسيلة الدفع المناسبة لك لعرض التفاصيل:",
        InlineKeyboardMarkup(keyboard),
    )


async def payment_detail(update, key):
    row = db_execute(
        "SELECT title,details FROM payment_methods WHERE key=%s AND active=TRUE",
        (key,),
        fetch=True,
    )
    if not row:
        await update.callback_query.answer("❌ طريقة الدفع غير متاحة.", show_alert=True)
        return

    text = (
        f"💳 **{row[0]}**\n\n{row[1]}\n\n"
        "بعد التحويل اضغط الزر أدناه لإرسال رقم السند أو صورة التحويل مباشرة إلى الإدارة."
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [green_button("📤 إرسال سند التحويل", callback_data=f"receipt_start:{key}")],
            [red_button("↩️ طرق الدفع", callback_data="fund")],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )


async def receipt_start(update, context, payment_key):
    row = db_execute(
        "SELECT key,title FROM payment_methods WHERE key=%s AND active=TRUE",
        (payment_key,),
        fetch=True,
    )
    if not row:
        await update.callback_query.answer("❌ طريقة الدفع غير متاحة.", show_alert=True)
        return

    context.user_data.clear()
    context.user_data["waiting_topup_receipt"] = True
    context.user_data["topup_payment_key"] = row[0]
    context.user_data["topup_payment_title"] = row[1]
    await update.callback_query.message.edit_text(
        f"📤 **إرسال سند تحويل — {row[1]}**\n\n"
        "أرسل الآن **رقم سند التحويل** كنص، أو أرسل **صورة السند** مباشرة.\n\n"
        "يمكنك كتابة مبلغ التحويل في نص الرسالة أو في وصف الصورة لتسهيل مراجعة الإدارة.\n\n"
        "⚠️ لا ترسل كلمة المرور أو رموز التحقق أو أي معلومات بطاقتك السرية.\n\n"
        "استخدم زر الإلغاء أدناه أو أرسل /cancel للإلغاء.",
        reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data="flowcancel:fund")]]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def submit_topup_receipt(update, context, receipt_number="", photo_file_id=""):
    user = update.effective_user
    add_user(user)
    payment_key = context.user_data.get("topup_payment_key")
    payment_title = context.user_data.get("topup_payment_title")
    if not payment_key or not payment_title:
        await update.message.reply_text("❌ انتهت جلسة إرسال السند. اختر طريقة الدفع مجددًا.")
        context.user_data.clear()
        return

    row = db_execute(
        """
        INSERT INTO topup_receipts(user_id,payment_key,payment_title,receipt_number,photo_file_id)
        VALUES(%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (user.id, payment_key, payment_title, receipt_number[:500], photo_file_id),
        fetch=True,
    )
    receipt_id = row[0]
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ **تم إرسال سند التحويل للإدارة بنجاح**\n\n"
        f"🧾 رقم المراجعة: **#{receipt_id}**\n"
        f"💳 وسيلة الدفع: {payment_title}\n\n"
        "سيتم مراجعة طلبك والتأكد من صحة التحويل. عند قبول السند سيتم شحن حسابك تلقائيًا، "
        "وسيصلك إشعار بالقبول أو الرفض.",
        parse_mode=ParseMode.MARKDOWN,
    )

    admin_text = (
        f"💰 **سند تحويل جديد #{receipt_id}**\n\n"
        f"👤 العميل: {user.full_name}\n"
        f"🆔 `{user.id}`\n"
        f"🔗 @{user.username or '—'}\n"
        f"💳 الوسيلة: {payment_title}\n"
        f"🧾 رقم السند/الوصف: {receipt_number or 'صورة سند فقط'}\n\n"
        "راجعه ثم حدّد مبلغ الشحن عند الموافقة."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ قبول وشحن الرصيد", callback_data=f"adm:approve_topup:{receipt_id}")],
        [InlineKeyboardButton("❌ رفض السند", callback_data=f"adm:reject_topup:{receipt_id}")],
        [InlineKeyboardButton("📄 تفاصيل السند", callback_data=f"adm:topup:{receipt_id}")],
    ])
    if photo_file_id:
        await context.bot.send_photo(
            ADMIN_ID,
            photo_file_id,
            caption=admin_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
        )
    else:
        await context.bot.send_message(
            ADMIN_ID, admin_text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup
        )
    log_operation(user.id, "topup_receipt_submitted", f"receipt={receipt_id};payment={payment_key}")


async def receipt_photo_handler(update, context):
    if not context.user_data.get("waiting_topup_receipt"):
        return
    if not await allowed(update, context):
        return
    photo_file_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()
    await submit_topup_receipt(update, context, receipt_number=caption, photo_file_id=photo_file_id)


async def support_page(update):
    await send_or_edit(
        update,
        "🆘 **الدعم الفني والإدارة**\n\n"
        "إذا كان لديك مشكلة في طلب أو الدفع أو التفعيل، "
        "تواصل معنا عبر القنوات الرسمية.\n\n"
        "📌 عند التواصل أرسل رقم الطلب إن وجد.",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK)],
            [InlineKeyboardButton("🛠️ الدعم على تيليجرام", url=SUPPORT_LINK)],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )


# ---------------------------------------------------------------------------
# Customer order workflow
# ---------------------------------------------------------------------------

WAIT_EMAIL, WAIT_NOTE, WAIT_PHONE, WAIT_CARD = range(4)

async def begin_order(update, context, sid):
    uid = update.effective_user.id
    srv = db_execute(
        """
        SELECT id,category_key,name,description,subscription_duration,
               activation_time,price,delivery_mode,needs_email,needs_note,
               needs_phone,file_id
        FROM services WHERE id=%s AND COALESCE(active,TRUE)=TRUE
        """,
        (sid,),
        fetch=True,
    )
    if not srv:
        await update.callback_query.answer("❌ الخدمة غير موجودة.", show_alert=True)
        return

    if srv[7] == "stock":
        item = db_execute(
            "SELECT id FROM inventory WHERE service_id=%s AND is_sold=FALSE LIMIT 1",
            (sid,),
            fetch=True,
        )
        if not item:
            await update.callback_query.answer("❌ نفد المخزون.", show_alert=True)
            return

    user = get_user(uid)
    price = Decimal(str(srv[6]))
    if Decimal(str(user[2])) < price:
        await update.callback_query.answer(
            "❌ رصيدك غير كافٍ. قم بتغذية حسابك أولًا.",
            show_alert=True,
        )
        return

    context.user_data.clear()
    context.user_data["order_service_id"] = sid
    context.user_data["order_service_name"] = srv[2]
    context.user_data["order_price"] = str(price)
    context.user_data["order_email_required"] = srv[8]
    context.user_data["order_note_required"] = srv[9]
    context.user_data["order_phone_required"] = srv[10]
    context.user_data["order_category"] = srv[1]

    if srv[8]:
        await update.callback_query.message.edit_text(
            f"📧 **طلب {srv[2]}**\n\n"
            "أرسل الإيميل الذي سجلت به في موقع الخدمة:\n\n"
            "⚠️ تأكد من صحة الإيميل قبل الإرسال.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="flowcancel:main")]
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if srv[10]:
        await update.callback_query.message.edit_text(
            "📱 أرسل رقم الهاتف المطلوب في الطلب:",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="flowcancel:main")]
            ]),
        )
        return

    if srv[9]:
        await update.callback_query.message.edit_text(
            "📝 أرسل ملاحظتك للطلب.\n\n"
            "إذا لم تكن لديك ملاحظة، أرسل: لا يوجد",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="flowcancel:main")]
            ]),
        )
        return

    await finalize_customer_order(update, context)


async def finalize_customer_order(update, context):
    uid = update.effective_user.id
    sid = int(context.user_data["order_service_id"])
    price = Decimal(context.user_data["order_price"])
    category = context.user_data["order_category"]
    email = context.user_data.get("order_email", "")
    note = context.user_data.get("order_note", "")
    phone = context.user_data.get("order_phone", "")

    # Lock one inventory row before charging for stock delivery.
    inventory_row = None
    if db_execute(
        "SELECT delivery_mode FROM services WHERE id=%s",
        (sid,),
        fetch=True,
    )[0] == "stock":
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id,code_text FROM inventory
                    WHERE service_id=%s AND is_sold=FALSE
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                    """,
                    (sid,),
                )
                inventory_row = cur.fetchone()
                if not inventory_row:
                    conn.rollback()
                    await send_or_edit(
                        update,
                        "❌ نفد المخزون قبل إتمام العملية.",
                        InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
                    )
                    return

                cur.execute(
                    "SELECT balance FROM users WHERE user_id=%s FOR UPDATE",
                    (uid,),
                )
                balance = cur.fetchone()[0]
                if Decimal(str(balance)) < price:
                    conn.rollback()
                    await send_or_edit(
                        update,
                        "❌ رصيدك غير كافٍ.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("💰 تغذية الحساب", callback_data="fund")]]),
                    )
                    return

                cur.execute(
                    "UPDATE users SET balance=balance-%s WHERE user_id=%s",
                    (price, uid),
                )
                cur.execute(
                    """
                    UPDATE inventory
                    SET is_sold=TRUE,sold_to=%s,sold_at=CURRENT_TIMESTAMP
                    WHERE id=%s
                    """,
                    (uid, inventory_row[0]),
                )
                cur.execute(
                    """
                    INSERT INTO orders(
                        user_id,service_id,status,total,customer_email,
                        customer_note,customer_phone,delivered_text
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        uid, sid, "مكتمل ✅ - تم التسليم",
                        price, email, note, phone, inventory_row[1],
                    ),
                )
                oid = cur.fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            DB_POOL.putconn(conn)

        log_operation(uid, "stock_purchase", f"order={oid};service={sid};price={price}")
        await send_or_edit(
            update,
            f"🎉 **تم تنفيذ طلبك بنجاح!**\n\n"
            f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
            f"💵 المبلغ: {money(price)}$\n\n"
            "🎁 **بيانات الاشتراك/الكود:**\n"
            f"`{inventory_row[1]}`\n\n"
            "📦 تم تسجيل الطلب في سجل طلباتك.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
        await context.bot.send_message(
            ADMIN_ID,
            f"🔔 **طلب فوري جديد #{oid}**\n\n"
            f"👤 العميل: {update.effective_user.full_name}\n"
            f"🆔 `{uid}`\n"
            f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
            f"💵 السعر: {money(price)}$\n"
            f"📧 الإيميل: {email or '—'}\n"
            f"📱 الرقم: {phone or '—'}\n"
            f"📝 الملاحظة: {note or '—'}",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return

    # Manual order: create first, then charge atomically.
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT balance FROM users WHERE user_id=%s FOR UPDATE",
                (uid,),
            )
            balance = cur.fetchone()[0]
            if Decimal(str(balance)) < price:
                conn.rollback()
                await send_or_edit(
                    update,
                    "❌ رصيدك غير كافٍ.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("💰 تغذية الحساب", callback_data="fund")]]),
                )
                return

            cur.execute(
                "UPDATE users SET balance=balance-%s WHERE user_id=%s",
                (price, uid),
            )
            status = "قيد التنفيذ ⏳"
            cur.execute(
                """
                INSERT INTO orders(
                    user_id,service_id,status,total,customer_email,
                    customer_note,customer_phone
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (uid, sid, status, price, email, note, phone),
            )
            oid = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)

    log_operation(uid, "manual_order", f"order={oid};service={sid};price={price}")

    await send_or_edit(
        update,
        f"✅ **تم استلام طلبك بنجاح**\n\n"
        f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
        f"🆔 رقم الطلب: **#{oid}**\n"
        f"💵 المبلغ: {money(price)}$\n\n"
        "⏳ سيتم تنفيذ وتفعيل طلبك خلال أقل وقت ممكن.\n"
        "📌 يمكنك متابعة حالة الطلب من «طلباتي».\n\n"
        "🆘 إذا لم تستلم طلبك، استخدم زر التواصل مع الإدارة من تفاصيل الطلب.",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 متابعة الطلب", callback_data=f"order:{oid}")],
            [InlineKeyboardButton("🆘 تواصل مع الإدارة", url=WHATSAPP_LINK)],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"🔔 **طلب جديد يحتاج تنفيذًا #{oid}**\n\n"
        f"👤 العميل: {update.effective_user.full_name}\n"
        f"🆔 `{uid}`\n"
        f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
        f"💵 السعر: {money(price)}$\n"
        f"📧 الإيميل: {email or '—'}\n"
        f"📱 الرقم: {phone or '—'}\n"
        f"📝 الملاحظة: {note or '—'}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 فتح الطلب", callback_data=f"adm:order:{oid}")],
            [InlineKeyboardButton("📨 تجهيز التسليم", callback_data=f"deliver:{oid}")],
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.clear()


async def customer_text(update, context):
    if not await allowed(update, context):
        return

    # Card top-up.
    if context.user_data.get("waiting_topup_receipt"):
        receipt_number = update.message.text.strip()
        if not receipt_number:
            await update.message.reply_text("❌ أرسل رقم السند أو صورة التحويل.")
            return
        await submit_topup_receipt(update, context, receipt_number=receipt_number)
        return

    if context.user_data.get("waiting_card"):
        code = update.message.text.strip()
        row = db_execute(
            """
            SELECT amount,is_used FROM cards WHERE code=%s
            """,
            (code,),
            fetch=True,
        )
        if not row or row[1]:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم.")
            return

        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cards
                    SET is_used=TRUE,used_by=%s,used_at=CURRENT_TIMESTAMP
                    WHERE code=%s AND is_used=FALSE
                    RETURNING amount
                    """,
                    (update.effective_user.id, code),
                )
                result = cur.fetchone()
                if not result:
                    conn.rollback()
                    await update.message.reply_text("❌ الكود مستخدم أو غير متاح.")
                    return
                cur.execute(
                    "UPDATE users SET balance=balance+%s WHERE user_id=%s",
                    (result[0], update.effective_user.id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            DB_POOL.putconn(conn)

        balance = get_user(update.effective_user.id)[2]
        context.user_data.pop("waiting_card", None)
        await update.message.reply_text(
            f"✅ **تم شحن حسابك بنجاح**\n\n"
            f"💰 قيمة البطاقة: {money(result[0])}$\n"
            f"💵 رصيدك الجديد: {money(balance)}$",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if "order_service_id" not in context.user_data:
        return

    if context.user_data.get("order_email_required") and "order_email" not in context.user_data:
        email = update.message.text.strip()
        if "@" not in email or "." not in email:
            await update.message.reply_text("❌ أرسل إيميلًا صحيحًا من فضلك.")
            return
        context.user_data["order_email"] = email
        if context.user_data.get("order_phone_required"):
            await update.message.reply_text("📱 أرسل رقم الهاتف المطلوب:")
            return
        if context.user_data.get("order_note_required"):
            await update.message.reply_text("📝 أرسل ملاحظتك للطلب، أو اكتب: لا يوجد")
            return
        await finalize_customer_order(update, context)
        return

    if context.user_data.get("order_phone_required") and "order_phone" not in context.user_data:
        context.user_data["order_phone"] = update.message.text.strip()
        if context.user_data.get("order_note_required"):
            await update.message.reply_text("📝 أرسل ملاحظتك للطلب، أو اكتب: لا يوجد")
            return
        await finalize_customer_order(update, context)
        return

    if context.user_data.get("order_note_required") and "order_note" not in context.user_data:
        note = update.message.text.strip()
        context.user_data["order_note"] = "" if note == "لا يوجد" else note
        await finalize_customer_order(update, context)
        return


async def customer_card_start(update, context):
    if not await allowed(update, context):
        return ConversationHandler.END
    await update.callback_query.message.edit_text(
        "🎟️ **شحن بكود بطاقة**\n\nأرسل كود البطاقة الآن أو استخدم زر الإلغاء أدناه.",
        reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data="flowcancel:fund")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["waiting_card"] = True
    return WAIT_CARD


async def customer_card_message(update, context):
    await customer_text(update, context)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin UI
# ---------------------------------------------------------------------------

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        return

    maintenance = "🟢 مفعل" if maintenance_active() else "🔴 معطل"
    text = (
        "👑 **لوحة تحكم B-Fix**\n\n"
        f"⚙️ الصيانة: {maintenance}\n\n"
        "من هنا يمكنك إدارة الخدمات والمخزون والطلبات والعملاء والقنوات "
        "والبطاقات والأزرار والإشعارات."
    )
    keyboard = [
        [
            InlineKeyboardButton("🛠️ تعديل الخدمات", callback_data="adm:services"),
            InlineKeyboardButton("📊 الإحصائيات", callback_data="adm:stats"),
        ],
        [
            InlineKeyboardButton("📋 الطلبات", callback_data="adm:orders"),
            InlineKeyboardButton("👥 العملاء", callback_data="adm:users"),
        ],
        [
            InlineKeyboardButton("📦 المخزون", callback_data="adm:inventory"),
            InlineKeyboardButton("🎟️ البطاقات", callback_data="adm:cards"),
        ],
        [
            InlineKeyboardButton("📢 القنوات", callback_data="adm:channels"),
            InlineKeyboardButton("🎛️ أزرار الشاشة", callback_data="adm:buttons"),
        ],
        [
            InlineKeyboardButton("💳 طرق الدفع", callback_data="adm:payments"),
            InlineKeyboardButton("🧾 سندات التحويل", callback_data="adm:topups"),
        ],
        [
            InlineKeyboardButton("📣 إشعار جماعي", callback_data="adm:broadcast"),
        ],
        [
            InlineKeyboardButton("⚙️ الصيانة", callback_data="adm:maintenance"),
            InlineKeyboardButton("📝 سجل العمليات", callback_data="adm:logs"),
        ],
        [red_button("🏠 الرئيسية", callback_data="main")],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("🚫 غير مصرح.", show_alert=True)
        return

    data = q.data[4:]

    if data == "home":
        context.user_data.clear()
        await admin_panel(update, context)
    elif data == "services":
        context.user_data.clear()
        await admin_services(update)
    elif data == "service_menu":
        await admin_service_menu(update)
    elif data.startswith("service:"):
        await admin_service_detail(update, int(data.split(":", 1)[1]))
    elif data.startswith("serviceedit:"):
        _prefix, sid, field = data.split(":", 2)
        prompts = {
            "name": "✏️ أرسل الاسم الجديد للخدمة:",
            "description": "📝 أرسل الوصف الجديد للخدمة:",
            "price": "💵 أرسل السعر الجديد بالأرقام فقط، مثال: 5.50",
            "duration": "⏳ أرسل مدة الاشتراك الجديدة، أو اكتب: غير محدد",
            "activation": "⚡ أرسل مدة التفعيل أو التسليم الجديدة، أو اكتب: غير محدد",
        }
        if field not in prompts:
            await q.answer("❌ حقل غير صالح.", show_alert=True)
            return
        context.user_data.clear()
        context.user_data.update({
            "admin_state": "service_edit_field",
            "service_edit_id": int(sid),
            "service_edit_field": field,
        })
        await q.message.edit_text(
            prompts[field],
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data=f"adm:service:{sid}")],
                *admin_navigation_rows("adm:services", "الخدمات"),
            ]),
        )
    elif data.startswith("service_category:"):
        _prefix, sid, category = data.split(":", 2)
        if category not in CATEGORY_INFO:
            await q.answer("❌ قسم غير صالح.", show_alert=True)
            return
        db_execute("UPDATE services SET category_key=%s WHERE id=%s", (category, int(sid)))
        log_operation(None, "service_category_updated", f"service={sid};category={category}", ADMIN_ID)
        await admin_service_detail(update, int(sid))
    elif data.startswith("service_mode:"):
        _prefix, sid, mode = data.split(":", 2)
        if mode not in {"manual", "stock"}:
            await q.answer("❌ طريقة تسليم غير صالحة.", show_alert=True)
            return
        db_execute("UPDATE services SET delivery_mode=%s WHERE id=%s", (mode, int(sid)))
        log_operation(None, "service_mode_updated", f"service={sid};mode={mode}", ADMIN_ID)
        await admin_service_detail(update, int(sid))
    elif data.startswith("service_toggle:"):
        _prefix, sid, field = data.split(":", 2)
        if field not in {"needs_email", "needs_note", "needs_phone", "active"}:
            await q.answer("❌ إعداد غير صالح.", show_alert=True)
            return
        db_execute(
            f"UPDATE services SET {field}=NOT COALESCE({field},FALSE) WHERE id=%s",
            (int(sid),),
        )
        log_operation(None, "service_setting_toggled", f"service={sid};setting={field}", ADMIN_ID)
        await admin_service_detail(update, int(sid))
    elif data == "add_service":
        await admin_add_service_prompt(update, context)
    elif data.startswith("newservice_category:"):
        category = data.split(":", 1)[1]
        if category not in {"digital", "subscriptions", "rentals", "vip", "free"}:
            await q.answer("❌ قسم غير صحيح.", show_alert=True)
            return
        context.user_data["service_category"] = category
        context.user_data["admin_state"] = "service_name"
        await q.message.edit_text(
            "🛒 أرسل **اسم الخدمة** الآن.\n\nمثال: TSM Tool",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء إضافة الخدمة", callback_data="adm:services")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "stats":
        await admin_stats(update)
    elif data == "orders":
        context.user_data.clear()
        await admin_orders(update)
    elif data.startswith("order:"):
        await admin_order_page(update, int(data.split(":")[1]))
    elif data == "users":
        await admin_users(update)
    elif data == "inventory":
        context.user_data.clear()
        await admin_inventory(update)
    elif data == "cards":
        context.user_data.clear()
        await admin_cards(update)
    elif data == "new_card":
        context.user_data.clear()
        context.user_data["admin_state"] = "new_card_code"
        await q.message.edit_text(
            "🎟️ أرسل كود البطاقة الجديدة:",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:cards")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "channels":
        context.user_data.clear()
        await admin_channels(update)
    elif data == "test_channels":
        await admin_test_channels(update, context)
    elif data == "add_channel":
        context.user_data.clear()
        context.user_data["admin_state"] = "channel_name"
        await q.message.edit_text(
            "📢 أرسل اسم القناة الاختيارية.\n\nبعده سأطلب رابطها فقط؛ لا يوجد تحقق اشتراك أو chat_id.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:channels")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "buttons":
        context.user_data.clear()
        await admin_buttons(update)
    elif data.startswith("editbutton:"):
        key = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["admin_state"] = "button_text"
        context.user_data["edit_button_key"] = key
        await q.message.edit_text(
            "✏️ أرسل النص الجديد للزر:",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:buttons")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("editbuttonaction:"):
        key = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["admin_state"] = "button_action"
        context.user_data["edit_button_key"] = key
        await q.message.edit_text(
            "🔗 أرسل الإجراء الجديد للزر.\n\n"
            "يمكنك إرسال رابط يبدأ بـ https:// أو أحد الإجراءات التالية:\n"
            "cat:digital, cat:subscriptions, cat:rentals, cat:vip, cat:free, orders, profile, fund, support, about",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:buttons")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("togglebutton:"):
        key = data.split(":", 1)[1]
        db_execute(
            "UPDATE custom_buttons SET is_visible=NOT COALESCE(is_visible,TRUE) WHERE btn_key=%s",
            (key,),
        )
        log_operation(None, "button_visibility_toggled", f"button={key}", ADMIN_ID)
        await admin_buttons(update)
    elif data == "payments":
        context.user_data.clear()
        await admin_payments(update)
    elif data == "topups":
        context.user_data.clear()
        await admin_topup_receipts(update)
    elif data.startswith("topup:"):
        await admin_topup_page(update, context, int(data.split(":")[1]))
    elif data.startswith("approve_topup:"):
        rid = int(data.split(":")[1])
        context.user_data["admin_state"] = "topup_approve_amount"
        context.user_data["topup_receipt_id"] = rid
        await q.message.reply_text(
            f"✅ أرسل مبلغ الشحن لسند التحويل #{rid}.\n\n"
            "سيُضاف هذا المبلغ إلى رصيد العميل عند التأكيد.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data=f"adm:topup:{rid}")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("reject_topup:"):
        rid = int(data.split(":")[1])
        context.user_data["admin_state"] = "topup_reject_note"
        context.user_data["topup_receipt_id"] = rid
        await q.message.reply_text(
            f"❌ أرسل سبب رفض سند التحويل #{rid}، أو اكتب: بدون ملاحظة",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data=f"adm:topup:{rid}")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "maintenance":
        context.user_data.clear()
        await admin_maintenance(update)
    elif data == "maintenance_auto":
        set_setting("maintenance_message", AUTO_MAINTENANCE_MESSAGE)
        set_setting("maintenance", "1")
        await q.answer("✅ تم تشغيل الصيانة بالرسالة التلقائية.", show_alert=True)
        await admin_maintenance(update)
    elif data == "maintenance_custom":
        context.user_data["admin_state"] = "maintenance_custom_message"
        await q.message.reply_text(
            "✍️ أرسل رسالة الصيانة الخاصة التي ستظهر للعملاء.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:maintenance")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "maintenance_off":
        set_setting("maintenance", "0")
        await q.answer("✅ تم إيقاف وضع الصيانة.", show_alert=True)
        await admin_maintenance(update)
    elif data == "toggle_maintenance":
        set_setting("maintenance", "0" if maintenance_active() else "1")
        await admin_maintenance(update)
    elif data == "broadcast":
        context.user_data.clear()
        context.user_data["admin_state"] = "broadcast"
        await q.message.edit_text(
            "📣 أرسل نص الإشعار الجماعي:",
            reply_markup=InlineKeyboardMarkup(admin_navigation_rows()),
        )
    elif data == "logs":
        await admin_logs(update)
    elif data.startswith("delete_service:"):
        sid = int(data.split(":")[1])
        await q.message.edit_text(
            "⚠️ **تأكيد أرشفة الخدمة**\n\n"
            "لن تظهر الخدمة للعملاء، لكن المخزون والطلبات سيبقيان محفوظين.\n"
            "هل تريد المتابعة؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [green_button("✅ نعم، أرشف الخدمة", callback_data=f"adm:confirm_archive_service:{sid}")],
                [red_button("↩️ إلغاء", callback_data="adm:services")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("confirm_archive_service:"):
        sid = int(data.split(":")[1])
        db_execute("UPDATE services SET active=FALSE WHERE id=%s", (sid,))
        await q.answer("✅ تمت أرشفة الخدمة مع الاحتفاظ بالمخزون والطلبات.", show_alert=True)
        await admin_services(update)
    elif data.startswith("delete_channel:"):
        cid = int(data.split(":")[1])
        await q.message.edit_text(
            "⚠️ **تأكيد حذف القناة**\n\n"
            "سيتوقف الاشتراك الإجباري لهذه القناة فوراً. هل تريد الحذف؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [green_button("✅ نعم، احذف القناة", callback_data=f"adm:confirm_delete_channel:{cid}")],
                [red_button("↩️ إلغاء", callback_data="adm:channels")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("confirm_delete_channel:"):
        cid = int(data.split(":")[1])
        db_execute("DELETE FROM forced_channels WHERE id=%s", (cid,))
        await q.answer("✅ تم حذف القناة.", show_alert=True)
        await admin_channels(update)
    elif data.startswith("clear_inventory:"):
        sid = int(data.split(":")[1])
        await q.message.edit_text(
            "⚠️ **تأكيد مسح المخزون**\n\n"
            "سيُحذف فقط المخزون غير المباع لهذه الخدمة، ولا يمكن التراجع عن ذلك.\n"
            "هل تريد المتابعة؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [green_button("✅ نعم، امسح غير المباع", callback_data=f"adm:confirm_clear_inventory:{sid}")],
                [red_button("↩️ إلغاء", callback_data="adm:inventory")],
                *admin_navigation_rows(),
            ]),
        )
    elif data.startswith("confirm_clear_inventory:"):
        sid = int(data.split(":")[1])
        db_execute(
            "DELETE FROM inventory WHERE service_id=%s AND is_sold=FALSE",
            (sid,),
        )
        await q.answer("✅ تم حذف المخزون غير المباع.", show_alert=True)
        await admin_inventory(update)
    elif data.startswith("addstock:"):
        sid = int(data.split(":")[1])
        context.user_data["admin_state"] = "stock"
        context.user_data["stock_service_id"] = sid
        await q.message.edit_text(
            "📦 أرسل الأكواد، كل كود في سطر مستقل.\n"
            "يمكنك أيضًا استخدام === للفصل بين الأكواد.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:inventory")],
                *admin_navigation_rows(),
            ]),
        )


async def admin_services(update):
    rows = db_execute(
        """
        SELECT id,name,category_key,price,delivery_mode,COALESCE(active,TRUE)
        FROM services ORDER BY id DESC
        """,
        fetchall=True,
    )
    text = (
        "🛠️ **إدارة الخدمات**\n\n"
        "اضغط اسم أي خدمة لفتح لوحة تفاصيلها وتعديل بياناتها.\n\n"
    )
    keyboard = [[green_button("➕ إضافة خدمة", callback_data="adm:add_service")]]
    if not rows:
        text += "لا توجد خدمات مضافة حتى الآن."
    else:
        for sid, name, cat, price, mode, active in rows:
            status = "🟢 ظاهرة" if active else "🔴 مؤرشفة"
            text += f"▪️ #{sid} {name} — {money(price)}$ — {status}\n"
            keyboard.append([
                green_button(f"📦 #{sid} {name[:45]}", callback_data=f"adm:service:{sid}")
            ])
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_service_menu(update):
    await admin_services(update)


async def admin_service_detail(update, sid):
    row = db_execute(
        """
        SELECT id,name,category_key,description,subscription_duration,activation_time,
               price,delivery_mode,needs_email,needs_note,needs_phone,COALESCE(active,TRUE),file_id
        FROM services WHERE id=%s
        """,
        (sid,),
        fetch=True,
    )
    if not row:
        await send_or_edit(
            update,
            "❌ الخدمة غير موجودة أو تم حذفها سابقاً.",
            InlineKeyboardMarkup(admin_navigation_rows("adm:services", "الخدمات")),
        )
        return
    stock = db_execute(
        "SELECT COUNT(*) FROM inventory WHERE service_id=%s AND COALESCE(is_sold,FALSE)=FALSE",
        (sid,),
        fetch=True,
    )[0]
    status = "🟢 ظاهرة للعميل" if row[11] else "🔴 مؤرشفة ومخفية"
    mode = "📦 تسليم من المخزون" if row[7] == "stock" else "🛠️ تنفيذ يدوي"
    text = (
        f"🧩 **تفاصيل الخدمة #{row[0]}**\n\n"
        f"📌 الاسم: **{row[1]}**\n"
        f"📂 القسم: `{row[2]}`\n"
        f"📝 الوصف: {row[3] or 'غير محدد'}\n"
        f"⏳ مدة الاشتراك: {row[4] or 'غير محددة'}\n"
        f"⚡ مدة التفعيل: {row[5] or 'غير محددة'}\n"
        f"💵 السعر: **{money(row[6])}$**\n"
        f"🚚 طريقة التسليم: {mode}\n"
        f"📦 الأكواد المتاحة: {stock}\n"
        f"📧 طلب إيميل: {'نعم ✅' if row[8] else 'لا ❌'}\n"
        f"📝 طلب ملاحظة: {'نعم ✅' if row[9] else 'لا ❌'}\n"
        f"📱 طلب رقم هاتف: {'نعم ✅' if row[10] else 'لا ❌'}\n"
        f"📁 ملف مرفق: {'موجود ✅' if row[12] else 'لا يوجد'}\n"
        f"👁️ الحالة: {status}"
    )
    keyboard = [
        [green_button("✏️ تعديل الاسم", callback_data=f"adm:serviceedit:{sid}:name"),
         green_button("📝 تعديل الوصف", callback_data=f"adm:serviceedit:{sid}:description")],
        [green_button("💵 تعديل السعر", callback_data=f"adm:serviceedit:{sid}:price"),
         green_button("⏳ تعديل المدة", callback_data=f"adm:serviceedit:{sid}:duration")],
        [green_button("⚡ تعديل التفعيل", callback_data=f"adm:serviceedit:{sid}:activation"),
         green_button("📦 إضافة أكواد", callback_data=f"adm:addstock:{sid}")],
        [green_button("⚡ الأدوات", callback_data=f"adm:service_category:{sid}:digital"),
         green_button("🔵 الاشتراكات", callback_data=f"adm:service_category:{sid}:subscriptions")],
        [green_button("🔧 الإيجار", callback_data=f"adm:service_category:{sid}:rentals"),
         green_button("💎 VIP", callback_data=f"adm:service_category:{sid}:vip"),
         green_button("🎁 مجاني", callback_data=f"adm:service_category:{sid}:free")],
        [green_button("🛠️ يدوي", callback_data=f"adm:service_mode:{sid}:manual"),
         green_button("📦 مخزون", callback_data=f"adm:service_mode:{sid}:stock")],
        [green_button("📧 تبديل الإيميل", callback_data=f"adm:service_toggle:{sid}:needs_email"),
         green_button("📝 تبديل الملاحظة", callback_data=f"adm:service_toggle:{sid}:needs_note")],
        [green_button("📱 تبديل الهاتف", callback_data=f"adm:service_toggle:{sid}:needs_phone"),
         red_button("👁️ إخفاء/إظهار", callback_data=f"adm:service_toggle:{sid}:active")],
        [red_button("🗑️ أرشفة الخدمة", callback_data=f"adm:delete_service:{sid}")],
        [red_button("↩️ كل الخدمات", callback_data="adm:services")],
        *admin_navigation_rows(),
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_add_service_prompt(update, context):
    context.user_data.clear()
    context.user_data["admin_state"] = "service_category_button"
    await update.callback_query.message.edit_text(
        "➕ **إضافة خدمة جديدة**\n\n"
        "اختر القسم أولًا من الأزرار التالية:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [green_button("⚡ الأدوات والبوكسات", callback_data="adm:newservice_category:digital")],
            [green_button("🔵 الاشتراكات", callback_data="adm:newservice_category:subscriptions")],
            [green_button("🔧 إيجار الأدوات", callback_data="adm:newservice_category:rentals")],
            [green_button("💎 عروض VIP", callback_data="adm:newservice_category:vip")],
            [green_button("🎁 عروض مجانية", callback_data="adm:newservice_category:free")],
            [red_button("🚫 إلغاء", callback_data="adm:services")],
            *admin_navigation_rows(),
        ]),
    )


async def admin_stats(update):
    try:
        users = db_execute("SELECT COUNT(*) FROM users", fetch=True)[0]
        orders = db_execute("SELECT COUNT(*) FROM orders", fetch=True)[0]
        services = db_execute("SELECT COUNT(*) FROM services WHERE COALESCE(active,TRUE)=TRUE", fetch=True)[0]
        stock = db_execute("SELECT COUNT(*) FROM inventory WHERE COALESCE(is_sold,FALSE)=FALSE", fetch=True)[0]
        revenue = db_execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE COALESCE(status,'') NOT LIKE 'ملغي%'",
            fetch=True,
        )[0]
    except Exception as exc:
        log.exception("Unable to load admin statistics: %s", exc)
        await send_or_edit(
            update,
            "⚠️ تعذر تحميل الإحصائيات مؤقتاً. أعد نشر آخر ملف bot.py لتشغيل ترقية قاعدة البيانات ثم جرّب مجدداً.",
            InlineKeyboardMarkup(admin_navigation_rows()),
        )
        return
    text = (
        "📊 **إحصائيات المتجر**\n\n"
        f"👥 العملاء: {users}\n"
        f"📦 الطلبات: {orders}\n"
        f"🛒 الخدمات النشطة: {services}\n"
        f"🔑 المخزون المتوفر: {stock}\n"
        f"💰 إجمالي المبيعات المسجلة: {money(revenue)}$"
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup(admin_navigation_rows()),
    )


async def admin_orders(update):
    rows = db_execute(
        """
        SELECT o.id,COALESCE(s.name,'محذوف'),o.status,o.total,
               u.name,o.user_id
        FROM orders o
        LEFT JOIN services s ON s.id=o.service_id
        LEFT JOIN users u ON u.user_id=o.user_id
        ORDER BY o.id DESC LIMIT 30
        """,
        fetchall=True,
    )
    text = "📋 **آخر الطلبات**\n\n"
    keyboard = []
    for oid, name, status, total, uname, uid in rows:
        text += f"#{oid} — {name} — {status} — {money(total)}$\n"
        keyboard.append([
            InlineKeyboardButton(
                f"📄 الطلب #{oid}",
                callback_data=f"adm:order:{oid}",
            )
        ])
    if not rows:
        text += "لا توجد طلبات."
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_users(update):
    rows = db_execute(
        """
        SELECT user_id,name,balance,join_date,is_blocked
        FROM users ORDER BY join_date DESC LIMIT 30
        """,
        fetchall=True,
    )
    text = "👥 **آخر العملاء**\n\n"
    for uid, name, balance, joined, blocked in rows:
        text += f"▪️ {name} | `{uid}` | {money(balance)}$ | {'🚫' if blocked else '🟢'}\n"
    await send_or_edit(
        update,
        text or "لا يوجد عملاء.",
        InlineKeyboardMarkup(admin_navigation_rows()),
    )


async def admin_inventory(update):
    rows = db_execute(
        """
        SELECT s.id,s.name,
               COUNT(i.id) FILTER (WHERE i.is_sold=FALSE),
               COUNT(i.id) FILTER (WHERE i.is_sold=TRUE)
        FROM services s
        LEFT JOIN inventory i ON i.service_id=s.id
        GROUP BY s.id,s.name
        ORDER BY s.id DESC
        """,
        fetchall=True,
    )
    text = "📦 **المخزون**\n\n"
    keyboard = []
    for sid, name, available, sold in rows:
        text += f"▪️ {name}: 🟢 {available} | 🛒 مباع {sold}\n"
        keyboard.append([
            InlineKeyboardButton("➕ إضافة أكواد", callback_data=f"adm:addstock:{sid}"),
            InlineKeyboardButton("🗑️ مسح غير المباع", callback_data=f"adm:clear_inventory:{sid}"),
        ])
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_cards(update):
    rows = db_execute(
        """
        SELECT is_used,COUNT(*),COALESCE(SUM(amount),0)
        FROM cards GROUP BY is_used
        """,
        fetchall=True,
    )
    unused_count = unused_value = used_count = 0
    for used, count, value in rows:
        if used:
            used_count, _ = count, value
        else:
            unused_count, unused_value = count, value

    text = (
        "🎟️ **بطاقات الشحن**\n\n"
        f"🟢 غير مستخدمة: {unused_count} بطاقة — {money(unused_value)}$\n"
        f"🔴 مستخدمة: {used_count}\n"
    )
    keyboard = [
        [InlineKeyboardButton("➕ إنشاء بطاقة", callback_data="adm:new_card")],
        *admin_navigation_rows(),
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_topup_receipts(update):
    rows = db_execute(
        """
        SELECT r.id,u.name,r.payment_title,r.status,r.receipt_number,r.created_at
        FROM topup_receipts r
        LEFT JOIN users u ON u.user_id=r.user_id
        ORDER BY r.id DESC LIMIT 30
        """,
        fetchall=True,
    )
    text = "🧾 **آخر سندات التحويل**\n\n"
    keyboard = []
    for rid, name, payment_title, status, receipt_number, created_at in rows:
        text += f"#{rid} — {name or 'عميل'} — {payment_title}\n{status}\n\n"
        keyboard.append([InlineKeyboardButton(f"📄 فتح السند #{rid}", callback_data=f"adm:topup:{rid}")])
    if not rows:
        text += "لا توجد سندات تحويل حتى الآن."
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_topup_page(update, context, receipt_id):
    try:
        row = db_execute(
            """
            SELECT r.id,r.user_id,u.name,u.username,r.payment_title,r.receipt_number,
                   r.photo_file_id,r.status,r.requested_amount,r.approved_amount,
                   r.admin_note,r.created_at
            FROM topup_receipts r
            LEFT JOIN users u ON u.user_id=r.user_id
            WHERE r.id=%s
            """,
            (receipt_id,),
            fetch=True,
        )
    except Exception as exc:
        log.exception("Unable to open top-up receipt %s: %s", receipt_id, exc)
        await send_or_edit(
            update,
            "⚠️ تعذر فتح تفاصيل السند مؤقتاً. أعد نشر آخر ملف bot.py ثم جرّب مرة أخرى.",
            InlineKeyboardMarkup([
                [red_button("↩️ كل السندات", callback_data="adm:topups")],
                *admin_navigation_rows(),
            ]),
        )
        return
    if not row:
        await update.callback_query.answer("❌ السند غير موجود أو تم حذفه.", show_alert=True)
        return

    text = (
        f"🧾 **سند التحويل #{row[0]}**\n\n"
        f"👤 العميل: {row[2] or '—'}\n"
        f"🆔 `{row[1]}`\n"
        f"🔗 @{row[3] or '—'}\n"
        f"💳 الوسيلة: {row[4]}\n"
        f"🔢 رقم السند/الوصف: {row[5] or '—'}\n"
        f"📌 الحالة: {row[7]}\n"
        f"💵 مبلغ العميل: {money(row[8]) if row[8] is not None else 'غير محدد'}$\n"
        f"💰 مبلغ الشحن المعتمد: {money(row[9]) if row[9] is not None else '—'}$\n"
        f"📝 ملاحظة الإدارة: {row[10] or '—'}\n"
        f"⏰ أُرسل: {row[11]}"
    )
    keyboard = []
    if str(row[7]).startswith("قيد المراجعة"):
        keyboard.extend([
            [InlineKeyboardButton("✅ قبول وشحن الرصيد", callback_data=f"adm:approve_topup:{receipt_id}")],
            [InlineKeyboardButton("❌ رفض السند", callback_data=f"adm:reject_topup:{receipt_id}")],
        ])
    keyboard.append([red_button("↩️ كل السندات", callback_data="adm:topups")])
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))
    if row[6]:
        try:
            await context.bot.send_photo(
                ADMIN_ID,
                row[6],
                caption=f"🧾 صورة سند التحويل #{receipt_id}",
            )
        except TelegramError as exc:
            # تظل تفاصيل السند مفتوحة حتى إذا كانت صورة قديمة غير متاحة في Telegram.
            log.warning("Receipt #%s details opened but photo could not be resent: %s", receipt_id, exc)


async def approve_topup_receipt(context, receipt_id, amount):
    conn = DB_POOL.getconn()
    approved = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id,payment_title,status FROM topup_receipts
                WHERE id=%s FOR UPDATE
                """,
                (receipt_id,),
            )
            row = cur.fetchone()
            if not row or not str(row[2]).startswith("قيد المراجعة"):
                conn.rollback()
                return None
            cur.execute(
                "UPDATE users SET balance=balance+%s WHERE user_id=%s",
                (amount, row[0]),
            )
            cur.execute(
                """
                UPDATE topup_receipts
                SET status='مقبول ✅',approved_amount=%s,reviewed_by=%s,
                    reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (amount, ADMIN_ID, receipt_id),
            )
            approved = (row[0], row[1])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)
    return approved


async def reject_topup_receipt(context, receipt_id, note):
    conn = DB_POOL.getconn()
    rejected_user_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id,status FROM topup_receipts WHERE id=%s FOR UPDATE",
                (receipt_id,),
            )
            row = cur.fetchone()
            if not row or not str(row[1]).startswith("قيد المراجعة"):
                conn.rollback()
                return None
            cur.execute(
                """
                UPDATE topup_receipts
                SET status='مرفوض ❌',admin_note=%s,reviewed_by=%s,
                    reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (note, ADMIN_ID, receipt_id),
            )
            rejected_user_id = row[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)
    return rejected_user_id


async def admin_channels(update):
    try:
        rows = db_execute(
            "SELECT id,name,link,chat_id,active FROM forced_channels ORDER BY id",
            fetchall=True,
        )
    except Exception as exc:
        log.exception("Unable to open forced-channel administration: %s", exc)
        await send_or_edit(
            update,
            "⚠️ تعذر فتح شاشة القنوات مؤقتاً. أعد نشر آخر ملف bot.py لتشغيل ترقية قاعدة البيانات، ثم جرّب مجدداً.",
            InlineKeyboardMarkup(admin_navigation_rows()),
        )
        return
    text = (
        "📢 **قناة المتجر — اختيارية**\n\n"
        "لا يوجد اشتراك إجباري في هذا البوت؛ العميل يستطيع استخدام جميع الأقسام مباشرة.\n"
        "القنوات المعروضة هنا تُستخدم كرابط اختياري فقط.\n\n"
    )
    keyboard = [
        [InlineKeyboardButton("➕ إضافة رابط قناة اختياري", callback_data="adm:add_channel")],
    ]
    for cid, name, link, chat_id, active in rows:
        text += f"▪️ {name} — {'🟢 ظاهر كرابط اختياري' if active else '🔴 مخفي'}\n"
        text += f"   الرابط: {link or 'غير مضبوط'}\n"
        keyboard.append([
            InlineKeyboardButton("🗑️ حذف", callback_data=f"adm:delete_channel:{cid}")
        ])
    if not rows:
        text += "لا توجد قنوات."
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_test_channels(update, context):
    await send_or_edit(
        update,
        "ℹ️ **الاشتراك الإجباري معطّل**\n\n"
        "لا يحتاج العميل للانضمام إلى أي قناة، ولا يحتاج البوت إلى صلاحيات مشرف للتحقق من العضوية. "
        "يمكنك الاحتفاظ برابط قناة المتجر كخيار اختياري فقط.",
        InlineKeyboardMarkup(admin_navigation_rows("adm:channels", "القناة")),
    )


async def admin_buttons(update):
    rows = db_execute(
        "SELECT btn_key,btn_text,btn_action,COALESCE(is_visible,TRUE) FROM custom_buttons ORDER BY btn_key",
        fetchall=True,
    )
    text = (
        "🎛️ **إدارة أزرار الواجهة**\n\n"
        "يمكنك تعديل النص، تغيير الإجراء أو الرابط، وإخفاء الزر أو إظهاره. "
        "الإخفاء قابل للاسترجاع ولا يحذف أي بيانات.\n\n"
    )
    keyboard = []
    for key, label, action, visible in rows:
        status = "🟢 ظاهر" if visible else "🔴 مخفي"
        text += f"▪️ `{key}` — {status}\n   النص: {label}\n   الإجراء: {action or 'غير مضبوط'}\n\n"
        keyboard.append([
            green_button("✏️ تعديل النص", callback_data=f"adm:editbutton:{key}"),
            green_button("🔗 تعديل الإجراء", callback_data=f"adm:editbuttonaction:{key}"),
        ])
        keyboard.append([
            red_button("🙈 إخفاء الزر", callback_data=f"adm:togglebutton:{key}")
            if visible else green_button("👁️ إظهار الزر", callback_data=f"adm:togglebutton:{key}")
        ])
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_payments(update):
    rows = db_execute(
        "SELECT key,title,active FROM payment_methods ORDER BY sort_order",
        fetchall=True,
    )
    text = "💳 **طرق الدفع**\n\n"
    for key, title, active in rows:
        text += f"▪️ {title} — {'🟢' if active else '🔴'}\n"
    text += "\nيمكن تعديل تفاصيل طرق الدفع مباشرة من قاعدة البيانات أو إضافة شاشة الإدارة لاحقًا."
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup(admin_navigation_rows()),
    )


async def admin_maintenance(update):
    status = "🟢 مفعل" if maintenance_active() else "🔴 معطل"
    current_message = get_setting("maintenance_message", AUTO_MAINTENANCE_MESSAGE)
    text = (
        "⚙️ **وضع الصيانة**\n\n"
        f"الحالة الحالية: {status}\n\n"
        f"📝 الرسالة الحالية:\n{current_message}\n\n"
        "اختر رسالة تلقائية أو اكتب رسالة خاصة، ثم يتم تفعيل الصيانة."
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [green_button("⚡ تشغيل برسالة تلقائية", callback_data="adm:maintenance_auto")],
            [green_button("✍️ تشغيل برسالة خاصة", callback_data="adm:maintenance_custom")],
            [red_button("⛔ إيقاف وضع الصيانة", callback_data="adm:maintenance_off")],
            *admin_navigation_rows(),
        ]),
    )


async def admin_logs(update):
    try:
        rows = db_execute(
            """
            SELECT id,user_id,admin_id,action,details,created_at
            FROM operation_log ORDER BY id DESC LIMIT 15
            """,
            fetchall=True,
        )
    except Exception as exc:
        log.exception("Unable to open operation log: %s", exc)
        await send_or_edit(
            update,
            "⚠️ تعذر فتح سجل العمليات مؤقتاً. أعد نشر آخر ملف bot.py لتشغيل ترقية قاعدة البيانات، ثم جرّب مجدداً.",
            InlineKeyboardMarkup(admin_navigation_rows()),
        )
        return

    if not rows:
        text = (
            "📝 **سجل العمليات**\n\n"
            "لا توجد عمليات مسجلة حتى الآن.\n\n"
            "يعرض هذا القسم عمليات الشحن والطلبات والتسليم والإلغاء والإشعارات الجماعية."
        )
    else:
        blocks = []
        for operation_id, user_id, admin_id, action, details, created in rows:
            parties = []
            if user_id:
                parties.append(f"👤 العميل: `{user_id}`")
            if admin_id:
                parties.append(f"👑 المدير: `{admin_id}`")
            party_text = "\n".join(parties) if parties else "👤 الجهة: النظام"
            blocks.append(
                f"🆔 **العملية #{operation_id}**\n"
                f"🗂️ النوع: **{arabic_operation_type(action)}**\n"
                f"{party_text}\n"
                f"⏰ الوقت: {format_operation_time(created)}\n"
                f"📌 التفاصيل:\n{arabic_operation_details(details)}"
            )
        text = "📝 **سجل العمليات — آخر 15 عملية**\n\n" + "\n\n────────────\n\n".join(blocks)

    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [green_button("🔄 تحديث السجل", callback_data="adm:logs")],
            *admin_navigation_rows(),
        ]),
    )


# ---------------------------------------------------------------------------
# Admin order handling
# ---------------------------------------------------------------------------

async def admin_order_page(update, oid):
    row = db_execute(
        """
        SELECT o.id,o.user_id,u.name,u.username,s.name,o.status,o.total,
               o.customer_email,o.customer_phone,o.customer_note,
               o.admin_note,o.delivered_text
        FROM orders o
        LEFT JOIN users u ON u.user_id=o.user_id
        LEFT JOIN services s ON s.id=o.service_id
        WHERE o.id=%s
        """,
        (oid,),
        fetch=True,
    )
    if not row:
        await update.callback_query.answer("❌ الطلب غير موجود.", show_alert=True)
        return

    text = (
        f"📄 **الطلب #{row[0]}**\n\n"
        f"👤 العميل: {row[2]}\n"
        f"🆔 `{row[1]}`\n"
        f"🔗 @{row[3] or '—'}\n"
        f"🛒 الخدمة: {row[4] or '—'}\n"
        f"📌 الحالة: {row[5]}\n"
        f"💵 السعر: {money(row[6])}$\n"
        f"📧 الإيميل: {row[7] or '—'}\n"
        f"📱 الرقم: {row[8] or '—'}\n"
        f"📝 الملاحظة: {row[9] or '—'}\n"
        f"📣 ملاحظة الإدارة: {row[10] or '—'}\n"
        f"🎁 التسليم: {row[11] or '—'}"
    )

    keyboard = [
        [
            InlineKeyboardButton("📨 بدء التسليم", callback_data=f"deliver:{oid}"),
            red_button("❌ إلغاء الطلب", callback_data=f"cancelorder:{oid}"),
        ],
        [
            InlineKeyboardButton("✅ إنهاء الطلب", callback_data=f"done:{oid}"),
        ],
        [red_button("↩️ الطلبات", callback_data="adm:orders")],
        *admin_navigation_rows(),
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_start_delivery(update, context, oid):
    if not is_admin(update.effective_user.id):
        return
    row = db_execute(
        """
        SELECT o.user_id,s.name,o.status
        FROM orders o LEFT JOIN services s ON s.id=o.service_id
        WHERE o.id=%s
        """,
        (oid,),
        fetch=True,
    )
    if not row:
        await update.callback_query.answer("❌ غير موجود.", show_alert=True)
        return

    context.user_data["admin_delivery_order"] = oid
    context.user_data["admin_state"] = "delivery"
    await update.callback_query.message.edit_text(
        f"📨 **تسليم الطلب #{oid}**\n\n"
        f"الخدمة: {row[1]}\n\n"
        "أرسل رسالة التسليم للعميل.\n"
        "يمكنك وضع الإيميل والباسورد أو الكود أو أي تفاصيل مطلوبة.\n\n"
        "مثال:\n"
        "تم تفعيل اشتراكك بنجاح.\n"
        "Email: ...\n"
        "Password: ...",
        reply_markup=InlineKeyboardMarkup([
            [red_button("🚫 إلغاء", callback_data=f"adm:order:{oid}")],
            *admin_navigation_rows(),
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def admin_finish_delivery(update, context, oid):
    if not is_admin(update.effective_user.id):
        return
    row = db_execute(
        "SELECT user_id FROM orders WHERE id=%s",
        (oid,),
        fetch=True,
    )
    if not row:
        return

    db_execute(
        """
        UPDATE orders
        SET status='مكتمل ✅',updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
        """,
        (oid,),
    )
    await context.bot.send_message(
        row[0],
        f"🎉 **تم إكمال طلبك #{oid} بنجاح**\n\n"
        "يمكنك الآن استخدام الخدمة أو بيانات الدخول التي تم إرسالها لك.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],
            [InlineKeyboardButton("🆘 الدعم", url=WHATSAPP_LINK)],
        ]),
    )
    await update.callback_query.answer("✅ تم إنهاء الطلب.", show_alert=True)
    await admin_order_page(update, oid)


async def admin_cancel_order(update, context, oid):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("🚫 غير مصرح.", show_alert=True)
        return
    # نقفل الطلب أولاً؛ بهذه الطريقة لا يمكن لزرتين متتاليتين إعادة الرصيد مرتين.
    conn = DB_POOL.getconn()
    cancelled = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id,total,status FROM orders WHERE id=%s FOR UPDATE",
                (oid,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                await update.callback_query.answer("الطلب غير موجود.", show_alert=True)
                return
            if str(row[2]).startswith("مكتمل") or str(row[2]).startswith("ملغي"):
                conn.rollback()
                await update.callback_query.answer("لا يمكن إلغاء هذا الطلب أو إعادة رصيده مرة أخرى.", show_alert=True)
                return

            cur.execute(
                "UPDATE users SET balance=balance+%s WHERE user_id=%s",
                (row[1], row[0]),
            )
            cur.execute(
                "UPDATE orders SET status='ملغي ❌',updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (oid,),
            )
            cancelled = row
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)

    await context.bot.send_message(
        cancelled[0],
        f"⚠️ **تم إلغاء طلبك #{oid}**\n\n"
        f"💰 تمت إعادة مبلغ **{money(cancelled[1])}$** إلى رصيدك.",
        parse_mode=ParseMode.MARKDOWN,
    )
    log_operation(cancelled[0], "order_cancelled", f"order={oid};refund={cancelled[1]}", ADMIN_ID)
    await update.callback_query.answer("✅ تم الإلغاء وإعادة الرصيد مرة واحدة.", show_alert=True)
    await admin_order_page(update, oid)


async def admin_approve_order(update, context, oid):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("🚫 غير مصرح.", show_alert=True)
        return
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM orders WHERE id=%s FOR UPDATE", (oid,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                await update.callback_query.answer("❌ الطلب غير موجود.", show_alert=True)
                return
            if str(row[0]).startswith("مكتمل") or str(row[0]).startswith("ملغي"):
                conn.rollback()
                await update.callback_query.answer("⚠️ لا يمكن اعتماد طلب مكتمل أو ملغي.", show_alert=True)
                return
            cur.execute(
                "UPDATE orders SET status='قيد التنفيذ ⏳',updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (oid,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)
    await update.callback_query.answer("✅ تم اعتماد الطلب.", show_alert=True)
    await admin_order_page(update, oid)


# ---------------------------------------------------------------------------
# Admin text workflow
# ---------------------------------------------------------------------------

async def admin_text(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    state = context.user_data.get("admin_state")
    text = update.message.text.strip()

    if state == "maintenance_custom_message":
        if not text:
            await update.message.reply_text("❌ رسالة الصيانة لا يمكن أن تكون فارغة.")
            return
        set_setting("maintenance_message", text[:1500])
        set_setting("maintenance", "1")
        context.user_data.clear()
        await update.message.reply_text("✅ تم تشغيل وضع الصيانة برسالتك الخاصة.")
        return

    if state == "topup_approve_amount":
        try:
            amount = Decimal(text)
            if amount <= 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ أرسل مبلغ شحن رقميًا أكبر من صفر.")
            return
        receipt_id = context.user_data.get("topup_receipt_id")
        approved = await approve_topup_receipt(context, receipt_id, amount)
        if not approved:
            context.user_data.clear()
            await update.message.reply_text("⚠️ لا يمكن اعتماد هذا السند؛ قد يكون عولج مسبقًا.")
            return
        user_id, payment_title = approved
        balance = get_user(user_id)[2]
        try:
            await context.bot.send_message(
                user_id,
                f"🎉 **تم قبول سند التحويل #{receipt_id}**\n\n"
                f"💳 وسيلة الدفع: {payment_title}\n"
                f"💰 تم شحن: **{money(amount)}$**\n"
                f"💵 رصيدك الجديد: **{money(balance)}$**\n\n"
                "شكرًا لاستخدامك متجر B-Fix.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as exc:
            log.warning("Top-up approval notification failed for user %s: %s", user_id, exc)
        log_operation(user_id, "topup_approved", f"receipt={receipt_id};amount={money(amount)}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم قبول السند #{receipt_id} وشحن {money(amount)}$ للعميل.")
        return

    if state == "topup_reject_note":
        receipt_id = context.user_data.get("topup_receipt_id")
        note = "" if text == "بدون ملاحظة" else text[:1000]
        user_id = await reject_topup_receipt(context, receipt_id, note)
        if not user_id:
            context.user_data.clear()
            await update.message.reply_text("⚠️ لا يمكن رفض هذا السند؛ قد يكون عولج مسبقًا.")
            return
        try:
            await context.bot.send_message(
                user_id,
                f"❌ **تم رفض سند التحويل #{receipt_id}**\n\n"
                f"📝 السبب: {note or 'لم تُضف الإدارة ملاحظة.'}\n\n"
                "يمكنك مراجعة بيانات التحويل أو التواصل مع الدعم عند الحاجة.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as exc:
            log.warning("Top-up rejection notification failed for user %s: %s", user_id, exc)
        log_operation(user_id, "topup_rejected", f"receipt={receipt_id};note={note}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم رفض السند #{receipt_id} وإشعار العميل.")
        return

    if state == "service_edit_field":
        sid = context.user_data.get("service_edit_id")
        field = context.user_data.get("service_edit_field")
        columns = {
            "name": "name",
            "description": "description",
            "duration": "subscription_duration",
            "activation": "activation_time",
        }
        if field == "price":
            try:
                value = Decimal(text)
                if value < 0:
                    raise InvalidOperation
            except Exception:
                await update.message.reply_text("❌ أرسل سعراً رقمياً صحيحاً يساوي صفراً أو أكبر.")
                return
            column = "price"
        else:
            if not text or len(text) > (120 if field == "name" else 1800):
                await update.message.reply_text("❌ القيمة فارغة أو أطول من الحد المسموح.")
                return
            value = text
            column = columns.get(field)
        if not sid or not column:
            context.user_data.clear()
            await update.message.reply_text("⚠️ انتهت جلسة التعديل. افتح الخدمة من جديد.")
            return
        db_execute(f"UPDATE services SET {column}=%s WHERE id=%s", (value, sid))
        log_operation(None, "service_detail_updated", f"service={sid};field={column}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text("✅ تم حفظ التعديل بنجاح.")
        return

    if state == "service_name":
        context.user_data["service_name"] = text
        context.user_data["admin_state"] = "service_description"
        await update.message.reply_text("📝 أرسل وصف الخدمة:")
        return

    if state == "service_category":
        category = text.strip().lower()
        if category not in {"digital", "subscriptions", "rentals", "vip", "free"}:
            await update.message.reply_text("❌ القسم غير صحيح. استخدم digital أو subscriptions أو rentals أو vip أو free.")
            return
        context.user_data["service_category"] = category
        context.user_data["admin_state"] = "service_description"
        await update.message.reply_text("📝 أرسل وصف الخدمة:")
        return

    if state == "service_description":
        context.user_data["service_description"] = text
        context.user_data["admin_state"] = "service_duration"
        await update.message.reply_text("⏳ أرسل مدة الاشتراك:")
        return

    if state == "service_duration":
        context.user_data["service_duration"] = text
        context.user_data["admin_state"] = "service_activation"
        await update.message.reply_text("⚡ أرسل مدة التفعيل/التسليم:")
        return

    if state == "service_activation":
        context.user_data["service_activation"] = text
        context.user_data["admin_state"] = "service_price"
        await update.message.reply_text("💵 أرسل السعر بالدولار:")
        return

    if state == "service_price":
        try:
            price = Decimal(text)
            if price < 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ أرسل سعرًا رقميًا صحيحًا.")
            return
        context.user_data["service_price"] = str(price)
        context.user_data["admin_state"] = "service_mode"
        await update.message.reply_text(
            "🚚 أرسل نوع التسليم:\n"
            "stock = تسليم فوري من المخزون\n"
            "manual = يحتاج تنفيذًا من الإدارة"
        )
        return

    if state == "service_mode":
        if text not in {"stock", "manual"}:
            await update.message.reply_text("❌ اكتب stock أو manual.")
            return
        context.user_data["service_mode"] = text
        context.user_data["admin_state"] = "service_options"
        await update.message.reply_text(
            "⚙️ أرسل خيارات الخدمة في سطر واحد، مثال:\n"
            "email,note\n\n"
            "الخيارات المتاحة: email,note,phone\n"
            "أو اكتب none"
        )
        return

    if state == "service_options":
        options = set() if text.lower() == "none" else {
            x.strip().lower() for x in text.split(",")
        }
        bad = options - {"email", "note", "phone"}
        if bad:
            await update.message.reply_text("❌ خيار غير معروف.")
            return

        d = context.user_data
        db_execute(
            """
            INSERT INTO services(
                category_key,name,description,subscription_duration,
                activation_time,price,delivery_mode,needs_email,
                needs_note,needs_phone,active
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            """,
            (
                d["service_category"],
                d["service_name"],
                d["service_description"],
                d["service_duration"],
                d["service_activation"],
                d["service_price"],
                d["service_mode"],
                "email" in options,
                "note" in options,
                "phone" in options,
            ),
        )
        await update.message.reply_text(
            "✅ **تمت إضافة الخدمة بالكامل.**\n\n"
            f"🛒 {d['service_name']}\n"
            f"💵 {d['service_price']}$\n"
            f"📂 {d['service_category']}\n"
            f"🚚 {d['service_mode']}",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return

    if state == "stock":
        sid = context.user_data["stock_service_id"]
        codes = [x.strip() for x in text.replace("===", "\n").splitlines() if x.strip()]
        for code in codes:
            db_execute(
                "INSERT INTO inventory(service_id,code_text) VALUES(%s,%s)",
                (sid, code),
            )
        await update.message.reply_text(
            f"✅ تمت إضافة **{len(codes)}** كود إلى المخزون.",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return

    if state == "new_card_code":
        context.user_data["card_code"] = text
        context.user_data["admin_state"] = "new_card_amount"
        await update.message.reply_text("💵 أرسل قيمة البطاقة:")
        return

    if state == "new_card_amount":
        try:
            amount = Decimal(text)
            if amount <= 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ قيمة غير صحيحة.")
            return
        try:
            db_execute(
                "INSERT INTO cards(code,amount) VALUES(%s,%s)",
                (context.user_data["card_code"], amount),
            )
        except Exception:
            await update.message.reply_text("❌ الكود موجود مسبقًا.")
            return
        await update.message.reply_text(
            f"✅ تم إنشاء بطاقة شحن بقيمة {money(amount)}$."
        )
        context.user_data.clear()
        return

    if state == "channel_name":
        context.user_data["channel_name"] = text
        context.user_data["admin_state"] = "channel_link"
        await update.message.reply_text("🔗 أرسل رابط القناة:")
        return

    if state == "channel_link":
        if not text.startswith(("https://t.me/", "http://t.me/", "t.me/")):
            await update.message.reply_text("❌ أرسل رابط قناة تيليجرام صحيحاً يبدأ بـ https://t.me/")
            return
        d = context.user_data
        db_execute(
            """
            INSERT INTO forced_channels(name,link,chat_id)
            VALUES(%s,%s,'')
            """,
            (d["channel_name"], text),
        )
        await update.message.reply_text("✅ تمت إضافة رابط القناة الاختياري. لن يُطلب من العملاء الاشتراك فيها.")
        context.user_data.clear()
        return

    if state == "button_text":
        key = context.user_data["edit_button_key"]
        if not text or len(text) > 64:
            await update.message.reply_text("❌ نص الزر يجب أن يكون بين 1 و64 حرفاً.")
            return
        db_execute(
            "UPDATE custom_buttons SET btn_text=%s WHERE btn_key=%s",
            (text, key),
        )
        log_operation(None, "button_text_updated", f"button={key}", ADMIN_ID)
        await update.message.reply_text("✅ تم تحديث نص الزر. افتح إدارة الأزرار لمراجعة النتيجة.")
        context.user_data.clear()
        return

    if state == "button_action":
        key = context.user_data["edit_button_key"]
        action = text.strip()
        if not valid_custom_button_action(action):
            await update.message.reply_text(
                "❌ الإجراء غير صالح. أرسل رابط https:// أو أحد الإجراءات المعروضة في رسالة التعليمات."
            )
            return
        db_execute(
            "UPDATE custom_buttons SET btn_action=%s WHERE btn_key=%s",
            (action, key),
        )
        log_operation(None, "button_action_updated", f"button={key};action={action}", ADMIN_ID)
        await update.message.reply_text("✅ تم تحديث إجراء الزر. افتح إدارة الأزرار لمراجعة النتيجة.")
        context.user_data.clear()
        return

    if state == "broadcast":
        if not text or len(text) > 3500:
            await update.message.reply_text("❌ الإشعار يجب أن يكون بين 1 و3500 حرفاً.")
            return
        rows = db_execute("SELECT user_id FROM users WHERE is_blocked=FALSE", fetchall=True)
        sent = failed = 0
        for (uid,) in rows:
            delivered = False
            for attempt in range(2):
                try:
                    await context.bot.send_message(uid, f"📢 إشعار من الإدارة\n\n{text}")
                    delivered = True
                    break
                except RetryAfter as exc:
                    if attempt == 1:
                        failed += 1
                        log.warning("Broadcast rate limit persisted for user %s", uid)
                        break
                    wait_for = getattr(exc, "retry_after", 1)
                    if hasattr(wait_for, "total_seconds"):
                        wait_for = wait_for.total_seconds()
                    await asyncio.sleep(float(wait_for) + 0.5)
                except Forbidden:
                    failed += 1
                    log.info("Broadcast skipped blocked/unavailable user %s", uid)
                    break
                except TelegramError as exc:
                    log.warning("Broadcast failed for user %s: %s", uid, exc)
                    if attempt == 1:
                        failed += 1
                except Exception as exc:
                    log.exception("Unexpected broadcast failure for user %s: %s", uid, exc)
                    failed += 1
                    break
            if delivered:
                sent += 1
            await asyncio.sleep(0.04)
        db_execute(
            "INSERT INTO broadcasts(content,sent_count,failed_count) VALUES(%s,%s,%s)",
            (text, sent, failed),
        )
        log_operation(ADMIN_ID, "broadcast_sent", f"sent={sent};failed={failed}", ADMIN_ID)
        await update.message.reply_text(
            f"✅ انتهى الإرسال.\n🟢 نجح: {sent}\n🔴 فشل: {failed}"
        )
        context.user_data.clear()
        return

    if state == "delivery":
        oid = context.user_data["admin_delivery_order"]
        row = db_execute(
            "SELECT user_id FROM orders WHERE id=%s",
            (oid,),
            fetch=True,
        )
        if not row:
            context.user_data.clear()
            await update.message.reply_text("❌ الطلب غير موجود.")
            return

        db_execute(
            """
            UPDATE orders
            SET status='تم التسليم 📩',
                delivered_text=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (text, oid),
        )

        await context.bot.send_message(
            row[0],
            f"🎉 **تم تفعيل/تسليم طلبك #{oid} بنجاح**\n\n"
            f"{text}\n\n"
            "🔐 يمكنك الآن تسجيل الدخول واستخدام الخدمة.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],
                [InlineKeyboardButton("🆘 الدعم", url=WHATSAPP_LINK)],
            ]),
        )
        await update.message.reply_text(
            f"✅ تم إرسال رسالة التسليم للعميل للطلب #{oid}."
        )
        log_operation(row[0], "admin_delivery", f"order={oid}", ADMIN_ID)
        context.user_data.clear()
        return


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def admin_command(update, context):
    if is_admin(update.effective_user.id):
        await admin_panel(update, context)


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("🚫 تم إلغاء العملية.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def build_application():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel))

    # Customer card flow.
    application.add_handler(
        CallbackQueryHandler(
            customer_card_start,
            pattern=r"^customer_card$",
        )
    )

    # Main callbacks.
    application.add_handler(
        CallbackQueryHandler(
            callback_router,
            pattern=r"^(check_sub|main|flowcancel:|cat:|service:|buy:|profile|orders|order:|fund|pay:|receipt_start:|support|about|admin|adm:|approve:|reject:|deliver:|done:|cancelorder:)",
        )
    )

    # Admin/customer text router.
    async def text_router(update, context):
        if update.effective_user.id == ADMIN_ID and context.user_data.get("admin_state"):
            await admin_text(update, context)
            return
        await customer_text(update, context)

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )
    application.add_handler(MessageHandler(filters.PHOTO, receipt_photo_handler))

    return application


def main():
    init_pool()
    init_db()
    app = build_application()

    log.info("B-Fix Production v2 starting...")
    log.info("Admin ID: %s", ADMIN_ID)
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot stopped.")
    finally:
        if DB_POOL:
            DB_POOL.closeall()
