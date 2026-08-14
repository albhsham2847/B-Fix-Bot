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
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2 import pool

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
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

WHATSAPP_LINK = os.getenv(
    "WHATSAPP_LINK",
    "https://iwtsp.com/967777728478",
).strip()
SUPPORT_LINK = os.getenv(
    "SUPPORT_LINK",
    "https://t.me/bfixSoftware",
).strip()
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
            await update.callback_query.answer(
                f"⚙️ {text}", show_alert=True
            )
        elif update.message:
            await update.message.reply_text(f"⚙️ {text}")
        return False

    if get_user(user.id) and get_user(user.id)[3]:
        if update.message:
            await update.message.reply_text("🚫 تم إيقاف حسابك. تواصل مع الإدارة.")
        return False

    return True


async def subscription_ok(update):
    user = update.effective_user
    if not user or is_admin(user.id):
        return True

    channels = db_execute(
        "SELECT id,name,link,chat_id FROM forced_channels WHERE active=TRUE ORDER BY id",
        fetchall=True,
    )
    if not channels:
        return True

    missing = []
    for cid, name, link, chat_id in channels:
        if not chat_id:
            # Without a Telegram chat_id we can still show the channel button,
            # but cannot truthfully verify membership.
            continue
        try:
            member = await update.get_bot().get_chat_member(chat_id, user.id)
            status = getattr(member, "status", "")
            if status not in ("member", "administrator", "creator"):
                missing.append((name, link))
        except Exception:
            # If verification fails, require the user to use the channel link.
            missing.append((name, link))

    if not missing:
        return True

    keyboard = [
        [InlineKeyboardButton(f"📢 {name}", url=link)]
        for name, link in missing
    ]
    keyboard.append(
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")]
    )

    text = (
        "🔒 **الاشتراك مطلوب أولًا**\n\n"
        "للاستفادة من متجر B-Fix، اشترك في القنوات المطلوبة ثم اضغط "
        "«تحقق من الاشتراك»."
    )

    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            await update.callback_query.answer(
                "⚠️ اشترك في القنوات المطلوبة أولًا.",
                show_alert=True,
            )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
    return False


def button_text(key, fallback):
    row = db_execute(
        "SELECT btn_text FROM custom_buttons WHERE btn_key=%s",
        (key,),
        fetch=True,
    )
    return row[0] if row else fallback


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(button_text("cat_digital", "⚡ الأدوات والبوكسات"), callback_data="cat:digital"),
            InlineKeyboardButton(button_text("cat_subscriptions", "🔵 الاشتراكات"), callback_data="cat:subscriptions"),
        ],
        [
            InlineKeyboardButton(button_text("cat_rentals", "🔧 إيجار الأدوات والبوكسات"), callback_data="cat:rentals"),
        ],
        [
            InlineKeyboardButton(button_text("cat_vip", "💎 عروض VIP"), callback_data="cat:vip"),
            InlineKeyboardButton(button_text("cat_free", "🎁 عروض مجانية"), callback_data="cat:free"),
        ],
        [
            InlineKeyboardButton(button_text("my_orders", "📦 طلباتي"), callback_data="orders"),
            InlineKeyboardButton(button_text("my_profile", "👤 حسابي"), callback_data="profile"),
        ],
        [
            InlineKeyboardButton(button_text("fund_account", "💰 تغذية حسابك"), callback_data="fund"),
            InlineKeyboardButton(button_text("support", "🆘 الدعم"), callback_data="support"),
        ],
        [
            InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK),
            InlineKeyboardButton("🛠️ قناة/دعم تيليجرام", url=SUPPORT_LINK),
        ],
        [InlineKeyboardButton("ℹ️ عن المتجر", callback_data="about")],
    ])


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
    if not await subscription_ok(update):
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
        if await subscription_ok(update):
            await q.answer("✅ تم التحقق بنجاح.", show_alert=True)
            await start(update, context)
        return

    if not await allowed(update, context):
        return
    if not await subscription_ok(update):
        return

    user_id = q.from_user.id

    if data == "main":
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
                [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
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

async def show_category(update, category):
    cat = db_execute(
        "SELECT title,description FROM categories WHERE key=%s AND active=TRUE",
        (category,),
        fetch=True,
    )
    if not cat:
        await send_or_edit(
            update,
            "❌ هذا القسم غير متاح.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔴 الرئيسية", callback_data="main")]]),
        )
        return

    services = db_execute(
        """
        SELECT id,name,price,delivery_mode,needs_email,needs_phone
        FROM services
        WHERE category_key=%s AND active=TRUE
        ORDER BY id DESC
        """,
        (category,),
        fetchall=True,
    )

    if not services:
        await send_or_edit(
            update,
            f"📂 **{cat[0]}**\n\n🚧 لا توجد خدمات مضافة حاليًا.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔴 الرئيسية", callback_data="main")]]),
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

    keyboard.append([InlineKeyboardButton("🔴 الرئيسية", callback_data="main")])
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
        FROM services WHERE id=%s AND active=TRUE
        """,
        (sid,),
        fetch=True,
    )
    if not srv:
        await send_or_edit(
            update,
            "❌ الخدمة غير موجودة.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔴 الرئيسية", callback_data="main")]]),
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
        [InlineKeyboardButton("🟢 طلب الخدمة الآن", callback_data=f"buy:{sid}")],
        [InlineKeyboardButton("🔴 رجوع للقسم", callback_data=f"cat:{srv[1]}")],
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
            [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
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
        keyboard = [[InlineKeyboardButton("🔴 الرئيسية", callback_data="main")]]
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
        keyboard.append([InlineKeyboardButton("🔴 الرئيسية", callback_data="main")])

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
        [InlineKeyboardButton("🔴 الطلبات", callback_data="orders")],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def payment_methods(update):
    rows = db_execute(
        """
        SELECT key,title FROM payment_methods
        WHERE active=TRUE ORDER BY sort_order,id
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
    keyboard.append([InlineKeyboardButton("🔴 الرئيسية", callback_data="main")])

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

    text = f"💳 **{row[0]}**\n\n{row[1]}\n\n⚡ بعد التحويل أرسل الإثبات للدعم."
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)],
            [InlineKeyboardButton("🔵 طرق الدفع", callback_data="fund")],
            [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
        ]),
    )


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
            [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
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
        FROM services WHERE id=%s AND active=TRUE
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
                [InlineKeyboardButton("🚫 إلغاء", callback_data="main")]
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if srv[10]:
        await update.callback_query.message.edit_text(
            "📱 أرسل رقم الهاتف المطلوب في الطلب:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 إلغاء", callback_data="main")]
            ]),
        )
        return

    if srv[9]:
        await update.callback_query.message.edit_text(
            "📝 أرسل ملاحظتك للطلب.\n\n"
            "إذا لم تكن لديك ملاحظة، أرسل: لا يوجد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 إلغاء", callback_data="main")]
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
                        InlineKeyboardMarkup([[InlineKeyboardButton("🔴 الرئيسية", callback_data="main")]]),
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
                [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
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
            [InlineKeyboardButton("🔴 الرئيسية", callback_data="main")],
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
        "🎟️ **شحن بكود بطاقة**\n\nأرسل كود البطاقة الآن أو /cancel للإلغاء.",
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
            InlineKeyboardButton("📦 الخدمات", callback_data="adm:services"),
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
            InlineKeyboardButton("🎛️ الأزرار", callback_data="adm:buttons"),
        ],
        [
            InlineKeyboardButton("💳 طرق الدفع", callback_data="adm:payments"),
            InlineKeyboardButton("📣 إشعار جماعي", callback_data="adm:broadcast"),
        ],
        [
            InlineKeyboardButton("⚙️ الصيانة", callback_data="adm:maintenance"),
            InlineKeyboardButton("📝 سجل العمليات", callback_data="adm:logs"),
        ],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("🚫 غير مصرح.", show_alert=True)
        return

    data = q.data[4:]

    if data == "services":
        await admin_services(update)
    elif data == "service_menu":
        await admin_service_menu(update)
    elif data == "add_service":
        await admin_add_service_prompt(update, context)
    elif data == "stats":
        await admin_stats(update)
    elif data == "orders":
        await admin_orders(update)
    elif data.startswith("order:"):
        await admin_order_page(update, int(data.split(":")[1]))
    elif data == "users":
        await admin_users(update)
    elif data == "inventory":
        await admin_inventory(update)
    elif data == "cards":
        await admin_cards(update)
    elif data == "new_card":
        context.user_data["admin_state"] = "new_card_code"
        await q.message.edit_text("🎟️ أرسل كود البطاقة الجديدة:")
    elif data == "channels":
        await admin_channels(update)
    elif data == "add_channel":
        context.user_data["admin_state"] = "channel_name"
        await q.message.edit_text(
            "📢 أرسل اسم القناة.\n\nبعده سأطلب رابط القناة ثم chat_id للتحقق الحقيقي."
        )
    elif data == "buttons":
        await admin_buttons(update)
    elif data.startswith("editbutton:"):
        key = data.split(":", 1)[1]
        context.user_data["admin_state"] = "button_text"
        context.user_data["edit_button_key"] = key
        await q.message.edit_text("✏️ أرسل النص الجديد للزر:")
    elif data == "payments":
        await admin_payments(update)
    elif data == "maintenance":
        await admin_maintenance(update)
    elif data == "toggle_maintenance":
        set_setting("maintenance", "0" if maintenance_active() else "1")
        await admin_maintenance(update)
    elif data == "broadcast":
        context.user_data["admin_state"] = "broadcast"
        await q.message.edit_text("📣 أرسل نص الإشعار الجماعي:")
    elif data == "logs":
        await admin_logs(update)
    elif data.startswith("delete_service:"):
        sid = int(data.split(":")[1])
        db_execute("DELETE FROM services WHERE id=%s", (sid,))
        await q.answer("✅ تم حذف الخدمة.", show_alert=True)
        await admin_services(update)
    elif data.startswith("delete_channel:"):
        cid = int(data.split(":")[1])
        db_execute("DELETE FROM forced_channels WHERE id=%s", (cid,))
        await q.answer("✅ تم حذف القناة.", show_alert=True)
        await admin_channels(update)
    elif data.startswith("clear_inventory:"):
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
            "يمكنك أيضًا استخدام === للفصل بين الأكواد."
        )


async def admin_services(update):
    rows = db_execute(
        """
        SELECT id,name,category_key,price,delivery_mode,active
        FROM services ORDER BY id DESC
        """,
        fetchall=True,
    )
    text = "📦 **إدارة الخدمات**\n\n"
    keyboard = [
        [InlineKeyboardButton("➕ إضافة خدمة", callback_data="adm:add_service")]
    ]
    if not rows:
        text += "لا توجد خدمات."
    else:
        for sid, name, cat, price, mode, active in rows:
            text += f"▪️ #{sid} {name} — {money(price)}$ — {cat}\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ حذف #{sid} {name}",
                    callback_data=f"adm:delete_service:{sid}",
                ),
                InlineKeyboardButton(
                    f"📦 مخزون #{sid}",
                    callback_data=f"adm:addstock:{sid}",
                ),
            ])
    keyboard.append([InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")])
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_service_menu(update):
    await admin_services(update)


async def admin_add_service_prompt(update, context):
    context.user_data["admin_state"] = "service_name"
    await update.callback_query.message.edit_text(
        "➕ **إضافة خدمة جديدة**\n\n"
        "أرسل اسم الخدمة.\n"
        "مثال: TSM Tool"
    )


async def admin_stats(update):
    users = db_execute("SELECT COUNT(*) FROM users", fetch=True)[0]
    orders = db_execute("SELECT COUNT(*) FROM orders", fetch=True)[0]
    services = db_execute("SELECT COUNT(*) FROM services WHERE active=TRUE", fetch=True)[0]
    stock = db_execute("SELECT COUNT(*) FROM inventory WHERE is_sold=FALSE", fetch=True)[0]
    revenue = db_execute(
        "SELECT COALESCE(SUM(total),0) FROM orders WHERE status NOT LIKE 'ملغي%'",
        fetch=True,
    )[0]
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
        InlineKeyboardMarkup([[InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")]]),
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
    keyboard.append([InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")])
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
        InlineKeyboardMarkup([[InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")]]),
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
    keyboard.append([InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")])
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
        [InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")],
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_channels(update):
    rows = db_execute(
        "SELECT id,name,link,chat_id,active FROM forced_channels ORDER BY id",
        fetchall=True,
    )
    text = "📢 **قنوات الاشتراك الإجباري**\n\n"
    keyboard = [[InlineKeyboardButton("➕ إضافة قناة", callback_data="adm:add_channel")]]
    for cid, name, link, chat_id, active in rows:
        text += f"▪️ {name} — {'🟢' if active else '🔴'}\n"
        text += f"   chat_id: {chat_id or 'غير مضبوط'}\n"
        keyboard.append([
            InlineKeyboardButton("🗑️ حذف", callback_data=f"adm:delete_channel:{cid}")
        ])
    if not rows:
        text += "لا توجد قنوات."
    keyboard.append([InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")])
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_buttons(update):
    rows = db_execute(
        "SELECT btn_key,btn_text FROM custom_buttons ORDER BY btn_key",
        fetchall=True,
    )
    text = "🎛️ **تعديل أزرار الواجهة**\n\n"
    keyboard = []
    for key, label in rows:
        text += f"▪️ `{key}` → {label}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ تعديل {label[:30]}",
                callback_data=f"adm:editbutton:{key}",
            )
        ])
    keyboard.append([InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")])
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
        InlineKeyboardMarkup([[InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")]]),
    )


async def admin_maintenance(update):
    status = "🟢 مفعل" if maintenance_active() else "🔴 معطل"
    text = (
        "⚙️ **وضع الصيانة**\n\n"
        f"الحالة الحالية: {status}\n\n"
        "عند التفعيل، يستطيع المشرف استخدام البوت بينما تظهر رسالة الصيانة للعملاء."
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تغيير الحالة", callback_data="adm:toggle_maintenance")],
            [InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")],
        ]),
    )


async def admin_logs(update):
    rows = db_execute(
        """
        SELECT action,details,created_at
        FROM operation_log ORDER BY id DESC LIMIT 25
        """,
        fetchall=True,
    )
    text = "📝 **آخر العمليات**\n\n"
    for action, details, created in rows:
        text += f"▪️ {created}\n{action}: {details}\n\n"
    await send_or_edit(
        update,
        text or "لا توجد عمليات.",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔴 لوحة المشرف", callback_data="adm:home")]]),
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
            InlineKeyboardButton("❌ إلغاء", callback_data=f"cancelorder:{oid}"),
        ],
        [
            InlineKeyboardButton("✅ إنهاء الطلب", callback_data=f"done:{oid}"),
        ],
        [InlineKeyboardButton("📋 الطلبات", callback_data="adm:orders")],
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
        "Password: ...\n\n"
        "أرسل /cancel للإلغاء.",
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
    row = db_execute(
        "SELECT user_id,total,status FROM orders WHERE id=%s",
        (oid,),
        fetch=True,
    )
    if not row:
        return
    if str(row[2]).startswith("مكتمل"):
        await update.callback_query.answer("لا يمكن إلغاء طلب مكتمل.", show_alert=True)
        return

    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET balance=balance+%s WHERE user_id=%s",
                (row[1], row[0]),
            )
            cur.execute(
                """
                UPDATE orders SET status='ملغي ❌',updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (oid,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)

    await context.bot.send_message(
        row[0],
        f"⚠️ **تم إلغاء طلبك #{oid}**\n\n"
        f"💰 تمت إعادة مبلغ **{money(row[1])}$** إلى رصيدك.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.callback_query.answer("✅ تم الإلغاء وإعادة الرصيد.", show_alert=True)
    await admin_order_page(update, oid)


async def admin_approve_order(update, context, oid):
    # Kept as a dedicated action for future moderation flows.
    db_execute(
        "UPDATE orders SET status='قيد التنفيذ ⏳',updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (oid,),
    )
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

    if state == "service_name":
        context.user_data["service_name"] = text
        context.user_data["admin_state"] = "service_category"
        await update.message.reply_text(
            "📂 أرسل القسم:\n"
            "digital = الأدوات والبوكسات\n"
            "subscriptions = الاشتراكات\n"
            "rentals = الإيجار\n"
            "vip = VIP\n"
            "free = المجاني"
        )
        return

    if state == "service_category":
        if text not in {"digital", "subscriptions", "rentals", "vip", "free"}:
            await update.message.reply_text("❌ القسم غير صحيح.")
            return
        context.user_data["service_category"] = text
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
                needs_note,needs_phone
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
        context.user_data["channel_link"] = text
        context.user_data["admin_state"] = "channel_chat_id"
        await update.message.reply_text(
            "🆔 أرسل chat_id للقناة للتحقق الحقيقي من الاشتراك.\n"
            "مثال: -1001234567890\n"
            "إذا تركته فارغًا لن يستطيع البوت التحقق آليًا."
        )
        return

    if state == "channel_chat_id":
        d = context.user_data
        db_execute(
            """
            INSERT INTO forced_channels(name,link,chat_id)
            VALUES(%s,%s,%s)
            """,
            (d["channel_name"], d["channel_link"], text),
        )
        await update.message.reply_text("✅ تمت إضافة القناة.")
        context.user_data.clear()
        return

    if state == "button_text":
        key = context.user_data["edit_button_key"]
        db_execute(
            "UPDATE custom_buttons SET btn_text=%s WHERE btn_key=%s",
            (text, key),
        )
        await update.message.reply_text("✅ تم تحديث الزر.")
        context.user_data.clear()
        return

    if state == "broadcast":
        rows = db_execute("SELECT user_id FROM users WHERE is_blocked=FALSE", fetchall=True)
        sent = failed = 0
        for (uid,) in rows:
            try:
                await context.bot.send_message(
                    uid,
                    f"📢 **إشعار من الإدارة**\n\n{text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                sent += 1
            except Exception:
                failed += 1
        db_execute(
            "INSERT INTO broadcasts(content,sent_count,failed_count) VALUES(%s,%s,%s)",
            (text, sent, failed),
        )
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
            pattern=r"^(check_sub|main|cat:|service:|buy:|profile|orders|order:|fund|pay:|support|about|admin|adm:|approve:|reject:|deliver:|done:|cancelorder:)",
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

    # Non-text media is intentionally handled as customer input only when an
    # admin is not waiting for text. Free-service upload administration can be
    # extended with this same state machine without changing the DB model.
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
