import os
import logging
import asyncio
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters
)

# ============================================================
# B-Fix Smart Bot - Professional PostgreSQL Edition
# Secrets are loaded ONLY from environment variables.
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bfix")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WHATSAPP_LINK = os.getenv("WHATSAPP_LINK", "")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "")

if not BOT_TOKEN or not DATABASE_URL or not ADMIN_ID:
    raise RuntimeError(
        "Missing BOT_TOKEN, DATABASE_URL or ADMIN_ID environment variables."
    )

# ---------------- Health server ----------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"B-Fix Bot is alive.")
    def log_message(self, fmt, *args):
        return

def run_health_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ---------------- Database ----------------

POOL = ThreadedConnectionPool(
    minconn=1,
    maxconn=int(os.getenv("DB_POOL_SIZE", "8")),
    dsn=DATABASE_URL,
)

def db_execute(sql, params=(), fetchone=False, fetchall=False):
    conn = POOL.getconn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetchone:
                    return cur.fetchone()
                if fetchall:
                    return cur.fetchall()
        return None
    finally:
        POOL.putconn(conn)

def db_transaction(callback):
    conn = POOL.getconn()
    try:
        with conn:
            with conn.cursor() as cur:
                result = callback(cur)
            return result
    finally:
        POOL.putconn(conn)

def init_db():
    db_execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            balance NUMERIC(18,2) NOT NULL DEFAULT 0,
            join_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price NUMERIC(18,2) NOT NULL DEFAULT 0,
            duration TEXT NOT NULL DEFAULT 'فوري',
            category TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            file_id TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
            service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            order_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            custom_data TEXT
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS product_keys (
            id SERIAL PRIMARY KEY,
            service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            key_text TEXT NOT NULL,
            is_sold BOOLEAN NOT NULL DEFAULT FALSE,
            sold_to BIGINT,
            sold_at TIMESTAMPTZ
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS cards (
            code TEXT PRIMARY KEY,
            amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
            is_used BOOLEAN NOT NULL DEFAULT FALSE,
            used_by BIGINT,
            used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS maintenance_mode (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            custom_message TEXT
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS forced_channels (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            link TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS custom_buttons (
            btn_key TEXT PRIMARY KEY,
            btn_text TEXT NOT NULL,
            btn_action TEXT NOT NULL
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS balance_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            amount NUMERIC(18,2) NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            admin_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db_execute("""
        INSERT INTO maintenance_mode(id, is_active)
        VALUES (1, FALSE)
        ON CONFLICT (id) DO NOTHING
    """)

    defaults = [
        ("cat_digital", "⚡ شحن الأدوات والبوكسات 🛠️", "show_cat_digital"),
        ("cat_subscriptions", "🔵 الاشتراكات 🚀", "show_cat_subscriptions"),
        ("cat_rentals", "🔧 خدمة إيجار الأدوات 🛠️", "show_cat_rentals"),
        ("cat_vip", "💎 عروض VIP الماسي ⭐", "show_cat_vip"),
        ("cat_free", "🎁 عروض مجانية حصرية 🆓", "show_cat_free"),
        ("my_orders", "ℹ️ سجل طلباتي 🔄", "my_orders"),
        ("my_profile", "⚡ حسابي ⚡", "my_profile"),
        ("charge_acc", "🔵 شحن بكود", "charge_account"),
        ("fund_acc", "🔵 تغذية حسابك", "fund_account"),
    ]
    for row in defaults:
        db_execute("""
            INSERT INTO custom_buttons(btn_key, btn_text, btn_action)
            VALUES (%s,%s,%s)
            ON CONFLICT (btn_key) DO NOTHING
        """, row)
    log.info("Database initialized.")

# ---------------- Helpers ----------------

def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def add_user(user_id, name):
    db_execute("""
        INSERT INTO users(user_id,name)
        VALUES(%s,%s)
        ON CONFLICT(user_id) DO UPDATE SET name=EXCLUDED.name
    """, (user_id, name or ""))

def maintenance_status():
    row = db_execute(
        "SELECT is_active FROM maintenance_mode WHERE id=1",
        fetchone=True,
    )
    return bool(row and row[0])

def maintenance_message():
    row = db_execute(
        "SELECT custom_message FROM maintenance_mode WHERE id=1",
        fetchone=True,
    )
    return row[0] if row and row[0] else "⚠️ البوت قيد الصيانة حالياً. يرجى المحاولة لاحقاً."

async def guard(update, context, admin=False):
    if maintenance_status() and not admin:
        msg = f"⚙️ {maintenance_message()} 🛠️"
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer("⚙️ البوت تحت الصيانة", show_alert=True)
        return False
    return True

async def is_subscribed(bot, user_id):
    if user_id == ADMIN_ID:
        return True

    channels = db_execute(
        "SELECT name,chat_id,link FROM forced_channels WHERE active=TRUE",
        fetchall=True,
    )
    for _, chat_id, _ in channels:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            ):
                return False
            if getattr(member, "is_member", True) is False:
                return False
        except Exception as exc:
            log.warning("Subscription check failed for %s: %s", chat_id, exc)
            return False
    return True

async def enforce_subscription(update, context):
    user = update.effective_user
    if await is_subscribed(context.bot, user.id):
        return True

    channels = db_execute(
        "SELECT name,link FROM forced_channels WHERE active=TRUE",
        fetchall=True,
    )
    keyboard = [
        [InlineKeyboardButton(f"📢 {name}", url=link)]
        for name, link in channels
    ]
    keyboard.append([
        InlineKeyboardButton("✅ تحقق من الاشتراك 🔄", callback_data="check_sub")
    ])
    text = (
        "⚠️ عذراً عزيزي العميل!\n\n"
        "🔒 يجب الاشتراك في القنوات المطلوبة أولاً ثم الضغط على "
        "«تحقق من الاشتراك»."
    )
    markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    elif update.callback_query:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=markup)
        except Exception:
            await update.callback_query.answer("❌ لم يكتمل الاشتراك.", show_alert=True)
    return False

def get_button(key, fallback_text, fallback_action):
    row = db_execute(
        "SELECT btn_text,btn_action FROM custom_buttons WHERE btn_key=%s",
        (key,), fetchone=True
    )
    return row if row else (fallback_text, fallback_action)

def main_markup():
    b = {
        k: get_button(k, t, a)
        for k, t, a in [
            ("cat_digital","⚡ شحن الأدوات والبوكسات 🛠️","show_cat_digital"),
            ("cat_subscriptions","🔵 الاشتراكات 🚀","show_cat_subscriptions"),
            ("cat_rentals","🔧 خدمة إيجار الأدوات 🛠️","show_cat_rentals"),
            ("cat_vip","💎 عروض VIP الماسي ⭐","show_cat_vip"),
            ("cat_free","🎁 عروض مجانية حصرية 🆓","show_cat_free"),
            ("my_orders","ℹ️ سجل طلباتي 🔄","my_orders"),
            ("my_profile","⚡ حسابي ⚡","my_profile"),
            ("charge_acc","🔵 شحن بكود","charge_account"),
            ("fund_acc","🔵 تغذية حسابك","fund_account"),
        ]
    }
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b["cat_digital"][0], callback_data=b["cat_digital"][1]),
         InlineKeyboardButton(b["cat_subscriptions"][0], callback_data=b["cat_subscriptions"][1])],
        [InlineKeyboardButton(b["cat_rentals"][0], callback_data=b["cat_rentals"][1])],
        [InlineKeyboardButton(b["cat_vip"][0], callback_data=b["cat_vip"][1]),
         InlineKeyboardButton(b["cat_free"][0], callback_data=b["cat_free"][1])],
        [InlineKeyboardButton(b["my_orders"][0], callback_data=b["my_orders"][1]),
         InlineKeyboardButton(b["my_profile"][0], callback_data=b["my_profile"][1])],
        [InlineKeyboardButton(b["charge_acc"][0], callback_data=b["charge_acc"][1]),
         InlineKeyboardButton(b["fund_acc"][0], callback_data=b["fund_acc"][1])],
        [InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK)] if WHATSAPP_LINK else [],
        [InlineKeyboardButton("🛠️ الدعم", url=SUPPORT_LINK)] if SUPPORT_LINK else [],
        [InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")],
    ])

async def show_main(update, context):
    user = update.effective_user
    if not await guard(update, context, user.id == ADMIN_ID):
        return
    if not await enforce_subscription(update, context):
        return
    add_user(user.id, user.first_name or "")
    text = (
        "✨ ━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━ ✨\n\n"
        f"👋 أهلاً بك يا {user.first_name or 'عميلنا'}\n"
        "🛒 اختر القسم المطلوب من القائمة أدناه 👇"
    )
    markup = main_markup()
    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await update.callback_query.message.edit_text(text, reply_markup=markup)

# ---------------- Purchase transaction ----------------

def purchase_key_transaction(user_id, service_id, price):
    def tx(cur):
        cur.execute(
            "SELECT balance FROM users WHERE user_id=%s FOR UPDATE",
            (user_id,)
        )
        user = cur.fetchone()
        if not user:
            return ("missing_user", None)

        balance = Decimal(user[0])
        price = Decimal(price)

        cur.execute("""
            SELECT id,key_text
            FROM product_keys
            WHERE service_id=%s AND is_sold=FALSE
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """, (service_id,))
        key = cur.fetchone()

        if not key:
            return ("no_stock", None)

        if balance < price:
            return ("no_balance", None)

        new_balance = balance - price
        cur.execute(
            "UPDATE users SET balance=%s WHERE user_id=%s",
            (new_balance, user_id)
        )
        cur.execute("""
            UPDATE product_keys
            SET is_sold=TRUE,sold_to=%s,sold_at=NOW()
            WHERE id=%s
        """, (user_id, key[0]))
        cur.execute("""
            INSERT INTO orders(user_id,service_id,status,custom_data)
            VALUES(%s,%s,'مكتمل ✅',%s)
            RETURNING id
        """, (user_id, service_id, "Delivered automatically"))
        order_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO balance_transactions(user_id,amount,reason)
            VALUES(%s,%s,%s)
        """, (user_id, -price, f"شراء الخدمة #{service_id}"))
        return ("ok", (key[1], new_balance, order_id))
    return db_transaction(tx)

# ---------------- Client handlers ----------------

async def start_command(update, context):
    await show_main(update, context)

async def client_callback(update, context):
    q = update.callback_query
    user_id = q.from_user.id
    data = q.data
    await q.answer()

    if data == "check_sub":
        if await is_subscribed(context.bot, user_id):
            await q.answer("✅ تم التحقق بنجاح!", show_alert=True)
            await show_main(update, context)
        else:
            await q.answer("❌ لم تشترك في جميع القنوات المطلوبة.", show_alert=True)
        return

    if not await guard(update, context, user_id == ADMIN_ID):
        return
    if not await enforce_subscription(update, context):
        return

    if data == "main_menu":
        await show_main(update, context)
        return

    if data == "my_profile":
        row = db_execute(
            "SELECT name,balance,join_date FROM users WHERE user_id=%s",
            (user_id,), fetchone=True
        )
        text = (
            f"👤 ملفك الشخصي\n\n"
            f"▪️ الاسم: {row[0]}\n"
            f"▪️ الآيدي: {user_id}\n"
            f"▪️ الرصيد: {row[1]} $\n"
            f"▪️ الانضمام: {row[2]}"
        )
        await q.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 تغذية الحساب", callback_data="fund_account")],
                [InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]
            ])
        )
        return

    if data == "charge_account":
        context.user_data["waiting_card"] = True
        await q.message.edit_text("💳 أرسل كود البطاقة الآن أو /cancel للإلغاء:")
        return

    if data == "fund_account":
        await q.message.edit_text(
            "💎 اختر وسيلة الدفع:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔹 محفظة جيب", callback_data="pay_jeep"),
                 InlineKeyboardButton("🔹 جوالي", callback_data="pay_jawali")],
                [InlineKeyboardButton("🔹 وان كاش", callback_data="pay_onecash"),
                 InlineKeyboardButton("🏦 بنك الكريمي", callback_data="pay_kuraimi")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
            ])
        )
        return

    payment_texts = {
        "pay_jeep": "💎 محفظة جيب\n\n📱 رقم الحساب: 580300",
        "pay_jawali": "💎 محفظة جوالي\n\n📱 رقم الحساب: 777728478",
        "pay_onecash": "💎 وان كاش\n\n📱 رقم الحساب: 178109713",
        "pay_kuraimi": "💎 بنك الكريمي\n\n🇾🇪 يمني: 3204168937\n🇸🇦 سعودي: 3204433991\n💵 دولار: 3191718649",
    }
    if data in payment_texts:
        buttons = []
        if WHATSAPP_LINK:
            buttons.append([InlineKeyboardButton("🟢 إرسال السند عبر واتساب", url=WHATSAPP_LINK)])
        buttons.append([InlineKeyboardButton("🔵 طرق الدفع", callback_data="fund_account")])
        buttons.append([InlineKeyboardButton("🔴 الرئيسية", callback_data="main_menu")])
        await q.message.edit_text(payment_texts[data], reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("show_cat_"):
        category = data.removeprefix("show_cat_")
        titles = {
            "digital": "⚡ خدمات شحن الأدوات والبوكسات",
            "subscriptions": "🔵 الاشتراكات الرقمية",
            "rentals": "🔧 إيجار الأدوات",
            "vip": "💎 عروض VIP",
            "free": "🎁 العروض المجانية",
        }
        rows = db_execute("""
            SELECT id,name,price
            FROM services
            WHERE category=%s AND active=TRUE
            ORDER BY id DESC
        """, (category,), fetchall=True)
        keyboard = []
        for sid, name, price in rows:
            stock = db_execute(
                "SELECT COUNT(*) FROM product_keys WHERE service_id=%s AND is_sold=FALSE",
                (sid,), fetchone=True
            )[0]
            status = "🟢 متوفر" if category in ("rentals","free") or stock > 0 else "🔴 نفد"
            keyboard.append([
                InlineKeyboardButton(
                    f"▪️ {name} - {price}$ ({status})",
                    callback_data=f"srv_{sid}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")])
        await q.message.edit_text(
            f"📑 {titles.get(category,'الخدمات')}\n\n👇 اختر الخدمة:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("srv_"):
        sid = int(data.split("_",1)[1])
        srv = db_execute("""
            SELECT id,name,description,price,duration,category
            FROM services WHERE id=%s AND active=TRUE
        """, (sid,), fetchone=True)
        if not srv:
            await q.message.edit_text("❌ الخدمة غير موجودة.")
            return
        stock = db_execute(
            "SELECT COUNT(*) FROM product_keys WHERE service_id=%s AND is_sold=FALSE",
            (sid,), fetchone=True
        )[0]
        text = (
            f"📌 {srv[1]}\n\n"
            f"📝 {srv[2]}\n"
            f"⏳ المدة: {srv[4]}\n"
            f"💵 السعر: {srv[3]} $\n"
            f"📦 المخزون: {stock}"
        )
        await q.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 شراء / طلب الآن", callback_data=f"buy_{sid}")],
                [InlineKeyboardButton("🔴 رجوع للقسم", callback_data=f"show_cat_{srv[5]}")]
            ])
        )
        return

    if data.startswith("buy_"):
        sid = int(data.split("_",1)[1])
        srv = db_execute("""
            SELECT id,name,description,price,duration,category,file_id
            FROM services WHERE id=%s AND active=TRUE
        """, (sid,), fetchone=True)
        if not srv:
            await q.message.edit_text("❌ الخدمة غير موجودة.")
            return

        price = Decimal(srv[3])
        category = srv[5]

        if category == "free" or price == 0:
            db_execute("""
                INSERT INTO orders(user_id,service_id,status,custom_data)
                VALUES(%s,%s,'مكتمل ✅ مجاني','Free delivery')
            """, (user_id, sid))
            await q.message.edit_text("🎁 تم تسجيل طلبك المجاني.")
            if srv[6]:
                try:
                    if category == "free":
                        await context.bot.send_document(user_id, srv[6], caption=f"🎁 {srv[1]}")
                except Exception:
                    await context.bot.send_message(user_id, f"🎁 {srv[1]}\n{srv[6]}")
            return

        if category in ("digital","subscriptions","vip"):
            result = purchase_key_transaction(user_id, sid, price)
            status, payload = result
            if status == "no_balance":
                await q.answer("❌ رصيدك غير كافٍ.", show_alert=True)
                return
            if status == "no_stock":
                await q.answer("❌ نفدت الكمية حالياً.", show_alert=True)
                return
            if status != "ok":
                await q.answer("❌ تعذر إتمام العملية.", show_alert=True)
                return

            key_text, new_balance, order_id = payload
            await q.message.edit_text(
                f"✅ تمت العملية بنجاح!\n\n📦 رقم الطلب: #{order_id}\n💰 الرصيد المتبقي: {new_balance}$"
            )
            await context.bot.send_message(user_id, f"🎁 بيانات طلبك:\n\n{key_text}")
            return

        if category == "rentals":
            context.user_data.update({
                "rental_sid": sid,
                "rental_price": price,
                "rental_name": srv[1],
                "waiting_rental_note": True
            })
            await q.message.edit_text(
                f"🔧 طلب إيجار: {srv[1]}\n💵 السعر: {price}$\n\n"
                "أرسل ملاحظتك أو بياناتك المطلوبة للخدمة:"
            )
            return

    if data == "my_orders":
        rows = db_execute("""
            SELECT COALESCE(s.name,'خدمة محذوفة'),o.status,o.order_date,o.id
            FROM orders o LEFT JOIN services s ON s.id=o.service_id
            WHERE o.user_id=%s ORDER BY o.id DESC LIMIT 10
        """, (user_id,), fetchall=True)
        if not rows:
            text = "📦 لا توجد طلبات."
        else:
            text = "📦 آخر الطلبات:\n\n" + "\n\n".join(
                f"▪️ #{r[3]} — {r[0]}\nالحالة: {r[1]}\nالتاريخ: {r[2]}"
                for r in rows
            )
        await q.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 الرئيسية", callback_data="main_menu")]
            ])
        )
        return

    if data == "bot_info":
        await q.message.edit_text(
            "🌟 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞\n\n"
            "🤖 متجر آلي للخدمات الرقمية وإدارة الطلبات والمخزون.\n\n"
            "⚡ شراء تلقائي\n🔐 مخزون آمن\n💳 شحن بالبطاقات\n"
            "📢 اشتراك إجباري\n👑 لوحة إدارة\n📊 سجل عمليات",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 الرئيسية", callback_data="main_menu")]
            ])
        )

# ---------------- Client text ----------------

async def client_text(update, context):
    user = update.effective_user
    if user.id == ADMIN_ID:
        return

    if not await guard(update, context):
        return
    if not await enforce_subscription(update, context):
        return

    if context.user_data.get("waiting_card"):
        code = update.message.text.strip()

        def tx(cur):
            cur.execute("""
                SELECT amount,is_used
                FROM cards WHERE code=%s
                FOR UPDATE
            """, (code,))
            card = cur.fetchone()
            if not card or card[1]:
                return None
            cur.execute("""
                UPDATE cards
                SET is_used=TRUE,used_by=%s,used_at=NOW()
                WHERE code=%s
            """, (user.id, code))
            cur.execute("""
                UPDATE users SET balance=balance+%s WHERE user_id=%s
                RETURNING balance
            """, (card[0], user.id))
            balance = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO balance_transactions(user_id,amount,reason)
                VALUES(%s,%s,%s)
            """, (user.id, card[0], f"بطاقة شحن {code}"))
            return card[0], balance

        result = db_transaction(tx)
        if not result:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم.")
        else:
            amount, balance = result
            await update.message.reply_text(
                f"✅ تم الشحن بنجاح!\n💰 القيمة: {amount}$\n💵 الرصيد الجديد: {balance}$"
            )
        context.user_data.pop("waiting_card", None)
        return

    if context.user_data.get("waiting_rental_note"):
        sid = context.user_data["rental_sid"]
        price = Decimal(context.user_data["rental_price"])
        note = update.message.text.strip()

        def tx(cur):
            cur.execute(
                "SELECT balance,name FROM users WHERE user_id=%s FOR UPDATE",
                (user.id,)
            )
            row = cur.fetchone()
            if not row or Decimal(row[0]) < price:
                return None
            cur.execute(
                "UPDATE users SET balance=balance-%s WHERE user_id=%s RETURNING balance",
                (price, user.id)
            )
            balance = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO orders(user_id,service_id,status,custom_data)
                VALUES(%s,%s,'قيد تجهيز الإيجار ⏳',%s)
                RETURNING id
            """, (user.id, sid, note))
            return row[1], balance, cur.fetchone()[0]

        result = db_transaction(tx)
        if not result:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
        else:
            name, balance, order_id = result
            await update.message.reply_text(
                f"⏳ تم استلام طلب الإيجار #{order_id}.\n"
                "سيتم التواصل معك بعد تجهيز الطلب."
            )
            await context.bot.send_message(
                ADMIN_ID,
                f"🔧 طلب إيجار جديد #{order_id}\n"
                f"👤 {name}\n🆔 {user.id}\n"
                f"🛠️ {context.user_data['rental_name']}\n"
                f"📝 {note}"
            )
        context.user_data.clear()

# ---------------- Admin panel ----------------

def admin_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 إضافة رصيد", callback_data="adm_add_bal"),
         InlineKeyboardButton("🔴 خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎟️ إنشاء بطاقة", callback_data="adm_new_card"),
         InlineKeyboardButton("🔎 مستخدم", callback_data="adm_search")],
        [InlineKeyboardButton("📢 القنوات الإجبارية", callback_data="adm_channels")],
        [InlineKeyboardButton("🎛️ أزرار القائمة", callback_data="adm_buttons")],
        [InlineKeyboardButton("🛠️ الخدمات والمخزون", callback_data="adm_services")],
        [InlineKeyboardButton("📢 إشعار جماعي", callback_data="adm_broadcast")],
        [InlineKeyboardButton("⚙️ الصيانة", callback_data="adm_maintenance")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
    ])

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("👑 لوحة تحكم B-Fix", reply_markup=admin_markup())

async def admin_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return
    await q.answer()
    data = q.data

    if data == "adm_main":
        await q.message.edit_text("👑 لوحة تحكم B-Fix", reply_markup=admin_markup())
        return

    if data in ("adm_add_bal","adm_sub_bal"):
        context.user_data["admin_action"] = data
        await q.message.edit_text("✍️ أرسل آيدي المستخدم:")
        context.user_data["admin_wait"] = "balance_user"
        return

    if data == "adm_new_card":
        context.user_data["admin_wait"] = "card_code"
        await q.message.edit_text("🎟️ أرسل كود البطاقة:")
        return

    if data == "adm_search":
        context.user_data["admin_wait"] = "search_user"
        await q.message.edit_text("🔎 أرسل آيدي المستخدم:")
        return

    if data == "adm_broadcast":
        context.user_data["admin_wait"] = "broadcast"
        await q.message.edit_text("📢 أرسل نص الإشعار الجماعي:")
        return

    if data == "adm_maintenance":
        active = maintenance_status()
        db_execute(
            "UPDATE maintenance_mode SET is_active=%s WHERE id=1",
            (not active,)
        )
        await q.message.edit_text(
            f"⚙️ الصيانة الآن: {'🟢 مفعلة' if not active else '🔴 معطلة'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="adm_main")]
            ])
        )
        return

    if data == "adm_stats":
        users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
        orders = db_execute("SELECT COUNT(*) FROM orders", fetchone=True)[0]
        services = db_execute(
            "SELECT COUNT(*) FROM services WHERE active=TRUE", fetchone=True
        )[0]
        stock = db_execute(
            "SELECT COUNT(*) FROM product_keys WHERE is_sold=FALSE", fetchone=True
        )[0]
        await q.message.edit_text(
            f"📊 الإحصائيات\n\n👥 المستخدمون: {users}\n"
            f"📦 الطلبات: {orders}\n🛠️ الخدمات: {services}\n"
            f"🔑 المخزون: {stock}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]
            ])
        )
        return

    if data == "adm_services":
        await q.message.edit_text(
            "🛠️ إدارة الخدمات",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ إضافة خدمة", callback_data="adm_add_service")],
                [InlineKeyboardButton("➕ إضافة مخزون", callback_data="adm_stock")],
                [InlineKeyboardButton("💵 تعديل سعر", callback_data="adm_price")],
                [InlineKeyboardButton("🗑️ حذف خدمة", callback_data="adm_delete_service")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]
            ])
        )
        return

    if data in ("adm_stock","adm_price","adm_delete_service"):
        rows = db_execute(
            "SELECT id,name,category FROM services WHERE active=TRUE ORDER BY id DESC",
            fetchall=True
        )
        prefix = {
            "adm_stock": "stock_",
            "adm_price": "price_",
            "adm_delete_service": "delete_"
        }[data]
        buttons = [
            [InlineKeyboardButton(f"{r[1]} ({r[2]})", callback_data=f"{prefix}{r[0]}")]
            for r in rows
        ]
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_services")])
        await q.message.edit_text("اختر الخدمة:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("delete_"):
        sid = int(data.split("_")[1])
        db_execute("UPDATE services SET active=FALSE WHERE id=%s", (sid,))
        await q.message.edit_text(
            "✅ تم تعطيل الخدمة.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="adm_services")]
            ])
        )
        return

    if data == "adm_channels":
        rows = db_execute(
            "SELECT id,name,chat_id,link FROM forced_channels WHERE active=TRUE",
            fetchall=True
        )
        buttons = [[InlineKeyboardButton("➕ إضافة قناة", callback_data="add_channel")]]
        text = "📢 القنوات الإجبارية\n\n"
        for r in rows:
            text += f"▪️ {r[1]} — {r[2]}\n"
            buttons.append([
                InlineKeyboardButton(f"🗑️ حذف {r[1]}", callback_data=f"del_channel_{r[0]}")
            ])
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")])
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "add_channel":
        context.user_data["admin_wait"] = "channel_name"
        await q.message.edit_text("✍️ أرسل اسم القناة:")
        return

    if data.startswith("del_channel_"):
        cid = int(data.split("_")[-1])
        db_execute("UPDATE forced_channels SET active=FALSE WHERE id=%s", (cid,))
        await q.answer("✅ تم حذف القناة.", show_alert=True)
        await q.message.edit_text("📢 تم حذف القناة.", reply_markup=admin_markup())
        return

    if data == "adm_buttons":
        rows = db_execute(
            "SELECT btn_key,btn_text FROM custom_buttons ORDER BY btn_key",
            fetchall=True
        )
        buttons = [
            [InlineKeyboardButton(f"✏️ {r[1]}", callback_data=f"edit_button_{r[0]}")]
            for r in rows
        ]
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")])
        await q.message.edit_text(
            "🎛️ اختر الزر لتعديل نصه:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("edit_button_"):
        key = data.removeprefix("edit_button_")
        context.user_data["edit_button_key"] = key
        context.user_data["admin_wait"] = "button_text"
        await q.message.edit_text("✍️ أرسل النص الجديد للزر:")
        return

    if data.startswith("stock_"):
        sid = int(data.split("_")[1])
        context.user_data["stock_sid"] = sid
        context.user_data["admin_wait"] = "stock_keys"
        await q.message.edit_text(
            "🔑 أرسل الأكواد مفصولة بـ ===\nمثال:\nKEY-1===KEY-2===KEY-3"
        )
        return

    if data.startswith("price_"):
        sid = int(data.split("_")[1])
        context.user_data["price_sid"] = sid
        context.user_data["admin_wait"] = "service_price"
        await q.message.edit_text("💵 أرسل السعر الجديد:")
        return

    if data == "adm_add_service":
        context.user_data["admin_wait"] = "service_name"
        await q.message.edit_text("📝 أرسل اسم الخدمة:")
        return

# ---------------- Admin text workflow ----------------

async def admin_text(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    wait = context.user_data.get("admin_wait")
    text = update.message.text.strip()

    if wait == "balance_user":
        if not text.isdigit():
            await update.message.reply_text("❌ الآيدي يجب أن يكون رقماً.")
            return
        context.user_data["balance_user"] = int(text)
        context.user_data["admin_wait"] = "balance_amount"
        await update.message.reply_text("💵 أرسل المبلغ:")
        return

    if wait == "balance_amount":
        try:
            amount = Decimal(text)
        except InvalidOperation:
            await update.message.reply_text("❌ مبلغ غير صحيح.")
            return
        uid = context.user_data["balance_user"]
        if context.user_data["admin_action"] == "adm_sub_bal":
            amount = -abs(amount)
        else:
            amount = abs(amount)

        def tx(cur):
            cur.execute(
                "SELECT balance FROM users WHERE user_id=%s FOR UPDATE",
                (uid,)
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                "UPDATE users SET balance=balance+%s WHERE user_id=%s",
                (amount, uid)
            )
            cur.execute("""
                INSERT INTO balance_transactions(user_id,amount,reason)
                VALUES(%s,%s,%s)
            """, (uid, amount, "تعديل رصيد بواسطة الإدارة"))
            cur.execute("""
                INSERT INTO audit_log(admin_id,action,details)
                VALUES(%s,%s,%s)
            """, (ADMIN_ID, "balance_change", f"user={uid}, amount={amount}"))
            return True

        ok = db_transaction(tx)
        await update.message.reply_text(
            "✅ تم تحديث الرصيد." if ok else "❌ المستخدم غير موجود."
        )
        context.user_data.clear()
        return

    if wait == "card_code":
        context.user_data["card_code"] = text
        context.user_data["admin_wait"] = "card_amount"
        await update.message.reply_text("💵 أرسل قيمة البطاقة:")
        return

    if wait == "card_amount":
        try:
            amount = Decimal(text)
            if amount <= 0:
                raise InvalidOperation
        except InvalidOperation:
            await update.message.reply_text("❌ قيمة غير صحيحة.")
            return
        code = context.user_data["card_code"]
        try:
            db_execute(
                "INSERT INTO cards(code,amount) VALUES(%s,%s)",
                (code, amount)
            )
            await update.message.reply_text(
                f"✅ تم إنشاء البطاقة.\n🎟️ الكود: `{code}`\n💰 القيمة: {amount}$",
                parse_mode="Markdown"
            )
        except psycopg2.errors.UniqueViolation:
            await update.message.reply_text("❌ هذا الكود موجود مسبقاً.")
        context.user_data.clear()
        return

    if wait == "search_user":
        if not text.isdigit():
            await update.message.reply_text("❌ آيدي غير صحيح.")
            return
        row = db_execute("""
            SELECT name,balance,join_date FROM users WHERE user_id=%s
        """, (int(text),), fetchone=True)
        if not row:
            await update.message.reply_text("❌ المستخدم غير موجود.")
        else:
            await update.message.reply_text(
                f"👤 {row[0]}\n💰 الرصيد: {row[1]}$\n📅 {row[2]}"
            )
        context.user_data.clear()
        return

    if wait == "broadcast":
        rows = db_execute("SELECT user_id FROM users", fetchall=True)
        sent = failed = 0
        for (uid,) in rows:
            try:
                await context.bot.send_message(uid, f"📢 إشعار الإدارة:\n\n{text}")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        db_execute(
            "INSERT INTO broadcasts(content,sent_count,failed_count) VALUES(%s,%s,%s)",
            (text, sent, failed)
        )
        await update.message.reply_text(
            f"✅ انتهى الإرسال.\n🟢 نجح: {sent}\n🔴 فشل: {failed}"
        )
        context.user_data.clear()
        return

    if wait == "button_text":
        key = context.user_data["edit_button_key"]
        db_execute(
            "UPDATE custom_buttons SET btn_text=%s WHERE btn_key=%s",
            (text, key)
        )
        await update.message.reply_text("✅ تم تعديل الزر.")
        context.user_data.clear()
        return

    if wait == "channel_name":
        context.user_data["channel_name"] = text
        context.user_data["admin_wait"] = "channel_chat_id"
        await update.message.reply_text(
            "🆔 أرسل Chat ID للقناة (مثال: -1001234567890):"
        )
        return

    if wait == "channel_chat_id":
        context.user_data["channel_chat_id"] = text
        context.user_data["admin_wait"] = "channel_link"
        await update.message.reply_text("🔗 أرسل رابط القناة:")
        return

    if wait == "channel_link":
        db_execute("""
            INSERT INTO forced_channels(name,chat_id,link)
            VALUES(%s,%s,%s)
        """, (
            context.user_data["channel_name"],
            context.user_data["channel_chat_id"],
            text
        ))
        await update.message.reply_text("✅ تمت إضافة القناة.")
        context.user_data.clear()
        return

    if wait == "stock_keys":
        sid = context.user_data["stock_sid"]
        keys = [x.strip() for x in text.split("===") if x.strip()]
        def tx(cur):
            for key in keys:
                cur.execute(
                    "INSERT INTO product_keys(service_id,key_text) VALUES(%s,%s)",
                    (sid, key)
                )
            cur.execute("""
                UPDATE services
                SET quantity=(
                    SELECT COUNT(*) FROM product_keys
                    WHERE service_id=%s AND is_sold=FALSE
                )
                WHERE id=%s
            """, (sid, sid))
        db_transaction(tx)
        await update.message.reply_text(f"✅ تمت إضافة {len(keys)} أكواد.")
        context.user_data.clear()
        return

    if wait == "service_price":
        try:
            price = Decimal(text)
            if price < 0:
                raise InvalidOperation
        except InvalidOperation:
            await update.message.reply_text("❌ سعر غير صحيح.")
            return
        sid = context.user_data["price_sid"]
        db_execute("UPDATE services SET price=%s WHERE id=%s", (price, sid))
        await update.message.reply_text("✅ تم تعديل السعر.")
        context.user_data.clear()
        return

    if wait == "service_name":
        context.user_data["service_name"] = text
        context.user_data["admin_wait"] = "service_description"
        await update.message.reply_text("📝 أرسل الوصف:")
        return

    if wait == "service_description":
        context.user_data["service_description"] = text
        context.user_data["admin_wait"] = "service_category"
        await update.message.reply_text(
            "📂 أرسل القسم:\n"
            "digital / subscriptions / rentals / vip / free"
        )
        return

    if wait == "service_category":
        if text not in ("digital","subscriptions","rentals","vip","free"):
            await update.message.reply_text("❌ قسم غير صحيح.")
            return
        context.user_data["service_category"] = text
        context.user_data["admin_wait"] = "service_price"
        await update.message.reply_text("💵 أرسل السعر:")
        return

    if wait == "service_price":
        try:
            price = Decimal(text)
            if price < 0:
                raise InvalidOperation
        except InvalidOperation:
            await update.message.reply_text("❌ سعر غير صحيح.")
            return
        db_execute("""
            INSERT INTO services(name,description,price,duration,category)
            VALUES(%s,%s,%s,'فوري',%s)
        """, (
            context.user_data["service_name"],
            context.user_data["service_description"],
            price,
            context.user_data["service_category"],
        ))
        await update.message.reply_text("✅ تمت إضافة الخدمة.")
        context.user_data.clear()
        return

# ---------------- Cancel ----------------

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("🚫 تم الإلغاء.")
    return ConversationHandler.END

# ---------------- Main ----------------

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))

    # Admin callback router
    app.add_handler(CallbackQueryHandler(
        admin_callback,
        pattern=r"^(adm_|add_channel$|del_channel_|edit_button_|stock_|price_|delete_)"
    ))

    # Client callback router
    app.add_handler(CallbackQueryHandler(client_callback))

    # Admin text must run before client text
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        admin_text
    ), group=0)

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        client_text
    ), group=1)

    app.add_handler(CommandHandler("cancel", cancel))

    log.info("B-Fix Smart Bot is running.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

