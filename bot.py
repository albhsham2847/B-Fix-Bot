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
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2 import pool

from telegram import (
    Update,
    MessageEntity,
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
# Optional external reseller API. Keep the secret in Render Environment Variables.
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL", "").strip()
EXTERNAL_API_KEY = os.getenv("EXTERNAL_API_KEY", "").strip()
# Prodseller/SMM provider selector. Keep it in Render to support multiple provider endpoints safely.
EXTERNAL_API_PROVIDER = os.getenv("EXTERNAL_API_PROVIDER", "1").strip()
# Purchase-channel settings. No customer information is ever published to this channel.
PURCHASE_CHANNEL_CHAT_ID = os.getenv("PURCHASE_CHANNEL_CHAT_ID", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
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
# External reseller API
# ---------------------------------------------------------------------------

def external_api_configured():
    return bool(EXTERNAL_API_URL and EXTERNAL_API_KEY)


def external_api_call(action, **fields):
    """Call the configured SMM-compatible provider without exposing its key in logs or messages."""
    if not external_api_configured():
        raise RuntimeError("External API is not configured. Set EXTERNAL_API_URL and EXTERNAL_API_KEY in Render.")
    if not EXTERNAL_API_URL.startswith("https://"):
        raise RuntimeError("External API URL must use HTTPS.")
    payload = {"key": EXTERNAL_API_KEY, "action": action}
    # The supplier specification requires provider=1 to select its service catalogue.
    # Purchase and status actions remain in the standard SMM format specified by the provider.
    if EXTERNAL_API_PROVIDER and action == "services":
        payload["provider"] = EXTERNAL_API_PROVIDER
    payload.update({k: v for k, v in fields.items() if v is not None and v != ""})
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(EXTERNAL_API_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # HTTPError can include a JSON body with the supplier's real rejection reason.
        # Read only structured error fields; never expose raw HTML or echoed request data.
        try:
            error_raw = exc.read().decode("utf-8", errors="replace")[:4096]
            error_payload = json.loads(error_raw)
            detail = external_error_summary(error_payload)
        except Exception:
            detail = "الخادم لم يرسل رسالة تشخيص آمنة قابلة للعرض"
        raise RuntimeError(f"External API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"External API connection failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("External API returned invalid JSON.") from exc


def external_error_summary(response):
    """Return a short display-safe provider error without including request secrets."""
    if not isinstance(response, dict):
        return "استجابة غير متوقعة من المزود"
    value = provider_value(
        response,
        "error", "message", "detail", "description", "error_message",
        default="",
    )
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    value = str(value or "").strip()
    # Keep Telegram Markdown presentation safe and limit untrusted provider text.
    value = re.sub(r"[\*_`\[\]]", "", value)
    return value[:300] or "لم يعُد المزود برقم طلب"


def external_services_list():
    data = external_api_call("services")
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("services", "data", "result"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def external_add_order(provider_service_id, input_data=""):
    data = external_api_call("add", service=provider_service_id, input=input_data)
    return data


def external_order_status(provider_order_id):
    return external_api_call("status", order=provider_order_id)


def provider_value(item, *keys, default=""):
    if not isinstance(item, dict):
        return default
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return default

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
        CREATE TABLE IF NOT EXISTS free_offer_claims (
            user_id BIGINT PRIMARY KEY,
            service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
            order_id INTEGER,
            claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS file_kind TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS pre_purchase_message TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS service_content_text TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS service_content_link TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS service_photo_file_id TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS service_document_file_id TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS name_entities_json TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS free_content_text TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS free_content_link TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS free_photo_file_id TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS free_document_file_id TEXT DEFAULT ''",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS customer_request_prompt TEXT DEFAULT ''",
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
        "ALTER TABLE custom_buttons ADD COLUMN IF NOT EXISTS icon_custom_emoji_id TEXT DEFAULT ''",
        "UPDATE custom_buttons SET is_visible=TRUE WHERE is_visible IS NULL",
        # External reseller services: additive-only; existing services/orders are preserved.
        """CREATE TABLE IF NOT EXISTS external_service_map (
            service_id INTEGER PRIMARY KEY REFERENCES services(id) ON DELETE CASCADE,
            provider_service_id TEXT NOT NULL UNIQUE,
            provider_price NUMERIC(14,2) NOT NULL DEFAULT 0,
            provider_name TEXT DEFAULT '',
            provider_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_order_id TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_status TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_refunded BOOLEAN NOT NULL DEFAULT FALSE",
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


def custom_emoji_entities_from_message(message):
    """Serialize only Custom Emoji entities from an admin service-name message."""
    raw_text = getattr(message, "text", "") or ""
    entities = []
    for entity in getattr(message, "entities", None) or []:
        if getattr(entity, "type", "") != "custom_emoji":
            continue
        emoji_id = getattr(entity, "custom_emoji_id", None)
        if not emoji_id:
            continue
        entities.append({
            "type": "custom_emoji",
            "offset": int(entity.offset),
            "length": int(entity.length),
            "custom_emoji_id": str(emoji_id),
        })
    return json.dumps(entities, ensure_ascii=False) if entities else ""


def custom_emoji_entities_from_json(value):
    """Build MessageEntity objects; malformed historical values simply fall back to text."""
    if not value:
        return []
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        result = []
        for item in raw if isinstance(raw, list) else []:
            if item.get("type") != "custom_emoji" or not item.get("custom_emoji_id"):
                continue
            result.append(MessageEntity(
                type="custom_emoji",
                offset=int(item["offset"]),
                length=int(item["length"]),
                custom_emoji_id=str(item["custom_emoji_id"]),
            ))
        return result
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return []

def now():
    return datetime.now()


def money(value):
    return f"{Decimal(str(value)):.2f}"


AUTO_MAINTENANCE_MESSAGE = (
    "نعتذر، المتجر تحت الصيانة حاليًا لتحسين الخدمة. "
    "يرجى المحاولة لاحقًا، وشكرًا لتفهمك."
)


def styled_button(style, *args, **kwargs):
    """Create a styled button and optionally pass a Telegram custom-emoji icon."""
    icon_id = kwargs.pop("icon_custom_emoji_id", "") or ""
    api_kwargs = dict(kwargs.pop("api_kwargs", {}) or {})
    if icon_id:
        api_kwargs["icon_custom_emoji_id"] = str(icon_id)
    plain_kwargs = dict(kwargs)
    styled_kwargs = dict(kwargs)
    styled_kwargs["style"] = style
    if api_kwargs:
        styled_kwargs["api_kwargs"] = api_kwargs
    try:
        return TelegramInlineKeyboardButton(*args, **styled_kwargs)
    except TypeError:
        # Older clients retain the normal button and ignore the optional icon.
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
        "SELECT btn_text,btn_action,COALESCE(is_visible,TRUE),COALESCE(icon_custom_emoji_id,'') FROM custom_buttons WHERE btn_key=%s",
        (key,),
        fetch=True,
    )
    if not row:
        return fallback_text, fallback_action, True
    text = row[0] if len(row) > 0 else fallback_text
    action = row[1] if len(row) > 1 else fallback_action
    visible = row[2] if len(row) > 2 else True
    icon_id = row[3] if len(row) > 3 else ""
    return text or fallback_text, action or fallback_action, bool(visible), icon_id or ""


def button_text(key, fallback):
    return button_config(key, fallback, "")[0]


def configured_main_button(key, fallback_text, fallback_action):
    text, action, visible, icon_id = button_config(key, fallback_text, fallback_action)
    if not visible:
        return None
    if action.startswith(("https://", "http://")):
        return green_button(text, url=action, icon_custom_emoji_id=icon_id)
    return green_button(text, callback_data=action, icon_custom_emoji_id=icon_id)



def plain_button_from_custom(button):
    """Remove only optional Telegram custom-emoji/style fields from a button."""
    data = button.to_dict() if hasattr(button, "to_dict") else {}
    data.pop("icon_custom_emoji_id", None)
    data.pop("style", None)
    allowed = {
        "text", "url", "callback_data", "web_app", "login_url",
        "switch_inline_query", "switch_inline_query_current_chat",
        "callback_game", "pay", "copy_text",
    }
    kwargs = {key: value for key, value in data.items() if key in allowed}
    return TelegramInlineKeyboardButton(**kwargs)


def plain_markup_from_custom(markup):
    """Return a normal inline keyboard; return original markup if conversion is unnecessary."""
    if not markup or not getattr(markup, "inline_keyboard", None):
        return markup
    rows = []
    for row in markup.inline_keyboard:
        rows.append([plain_button_from_custom(button) for button in row])
    return InlineKeyboardMarkup(rows)

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
    """Keep fixed core routes intact while allowing label, visibility, and optional icon."""
    text, _stored_action, visible, icon_id = button_config(key, fallback_text, callback_data or url or "")
    if not visible:
        return None
    if url:
        return green_button(text, url=url, icon_custom_emoji_id=icon_id)
    return green_button(text, callback_data=callback_data, icon_custom_emoji_id=icon_id)


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


async def send_or_edit(update, text, markup=None, entities=None):
    # Telegram does not allow combining custom entities with parse_mode reliably.
    message_kwargs = {
        "reply_markup": markup,
        "disable_web_page_preview": True,
    }
    if entities:
        message_kwargs["entities"] = entities
    else:
        message_kwargs["parse_mode"] = ParseMode.MARKDOWN

    async def send_message(method, kwargs):
        try:
            await method(text, **kwargs)
            return True
        except Exception as exc:
            log.warning("Telegram message attempt failed; trying plain fallback: %s", exc)
            return False

    if update.callback_query:
        method = update.callback_query.message.edit_text
        if await send_message(method, message_kwargs):
            return
        fallback_kwargs = dict(message_kwargs)
        fallback_kwargs["reply_markup"] = plain_markup_from_custom(markup)
        if entities:
            # A client that rejects the entity still receives the service text.
            fallback_kwargs.pop("entities", None)
        if await send_message(method, fallback_kwargs):
            return
        await update.callback_query.message.reply_text(text, **fallback_kwargs)
    else:
        method = update.message.reply_text
        if await send_message(method, message_kwargs):
            return
        fallback_kwargs = dict(message_kwargs)
        fallback_kwargs["reply_markup"] = plain_markup_from_custom(markup)
        if entities:
            fallback_kwargs.pop("entities", None)
        await method(text, **fallback_kwargs)


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

    # Channel posts use /start service_<id> deep links; open the requested service directly.
    payload = context.args[0] if context.args else ""
    if payload.startswith("service_") and payload[8:].isdigit():
        await show_service(update, int(payload[8:]))
        return

    text = (
        f"✦ ━━━━━━ ❲ **{BOT_NAME}** ❳ ━━━━━━ ✦\n\n"
        f"أهلاً بك يا [{user.first_name}](tg://user?id={user.id}) في وجهتك للخدمات الرقمية المختارة.\n\n"
        "⚡ **الأدوات والبوكسات**\n"
        "للمبرمجين وفنيي السوفت وير: أدوات مساعدة، بوكسات رقمية وخدمات تفعيل.\n\n"
        "🔵 **الاشتراكات والتفعيلات**\n"
        "خدمات الذكاء الاصطناعي والتطبيقات والمواقع التي تحتاج اشتراكاً أو دفعاً إلكترونياً.\n\n"
        "🔧 **إيجار الأدوات**\n"
        "اطلب الأداة التي تحتاجها لفترة محددة من دون دفع مبالغ مرتفعة في الاشتراكات الكاملة.\n\n"
        "💎 **خدمات VIP المخصصة**\n"
        "بوتات ومواقع وتطبيقات وتصاميم سوشال ميديا، وحلول أمن سيبراني مصرح بها.\n\n"
        "🎁 **العروض المجانية**\n"
        "هدايا رقمية تصل تلقائياً بالنص والرابط والصورة والملف. للعميل عرض واحد كل 6 ساعات.\n\n"
        "اختر القسم المناسب من القائمة أدناه."
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

    if data.startswith("free_offer_timer:"):
        await free_offer_timer(update, context, int(data.split(":", 1)[1]))
        return

    if data.startswith("cat:"):
        await show_category(update, data.split(":", 1)[1])
        return

    if data.startswith("service:"):
        await show_service(update, int(data.split(":")[1]))
        return

    if data.startswith("buyconfirm:"):
        if not context.user_data.get("order_confirm_required"):
            await q.answer("⚠️ ابدأ الطلب من صفحة الخدمة أولاً.", show_alert=True)
            return
        context.user_data.pop("order_confirm_required", None)
        await finalize_customer_order(update, context)
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
            "✦ **B-Fix Software | متجر الخدمات الرقمية** ✦\n\n"
            "نقدّم أدوات وبوكسات للمبرمجين وفنيي السوفت وير، واشتراكات وتفعيلات لخدمات "
            "الذكاء الاصطناعي والتطبيقات والمواقع، وإيجار أدوات لفترات مرنة.\n\n"
            "كما نوفر خدمات VIP مخصصة لتطوير البوتات والمواقع والتطبيقات وتصاميم السوشال ميديا "
            "وحلول الأمن السيبراني المصرح بها، إضافة إلى عروض مجانية تُسلّم تلقائياً.\n\n"
            "🔒 جميع الطلبات تُتابع عبر نظام منظم وسجل عمليات واضح.",
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
    "digital": (
        "⚡ الأدوات والبوكسات",
        "حلول رقمية للمبرمجين وفنيي السوفت وير: أدوات مساعدة، بوكسات وخدمات تفعيل منظمة.",
    ),
    "subscriptions": (
        "🔵 الاشتراكات والتفعيلات",
        "خدمات الذكاء الاصطناعي والتطبيقات والمواقع التي تحتاج اشتراكاً أو دفعاً إلكترونياً وتفعيلًا احترافياً.",
    ),
    "rentals": (
        "🔧 إيجار الأدوات",
        "اطلب الأداة المناسبة للمهمة التي تحتاجها لفترة محددة، من دون تحمّل تكلفة الاشتراكات الباهظة.",
    ),
    "vip": (
        "💎 خدمات VIP المخصصة",
        "طلبات راقية ومخصصة: تطوير بوتات ومواقع وتطبيقات، تصاميم سوشال ميديا، وحلول أمن سيبراني مصرح بها.",
    ),
    "free": (
        "🎁 العروض المجانية",
        "هدايا رقمية تُسلّم تلقائياً بالنص والرابط والصورة والملف. يحق للعميل استلام عرض واحد كل 6 ساعات.",
    ),
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
        elif mode == "external":
            status = "🟢 API"
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
               needs_phone,pre_purchase_message,name_entities_json
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

    duration_label = "مدة الإيجار" if srv[1] == "rentals" else "مدة الاشتراك"
    activation_label = "وقت تسليم بيانات الإيجار" if srv[1] == "rentals" else "مدة التفعيل/التسليم"
    service_entities = custom_emoji_entities_from_json(srv[12] if len(srv) > 12 else "")
    if service_entities:
        text = (
            f"{srv[2]}\n\n"
            f"📝 الوصف: {srv[3] or 'غير محدد'}\n"
        f"⏳ **{duration_label}:** {srv[4] or 'حسب الخدمة'}\n"
        f"⚡ **{activation_label}:** {srv[5] or 'حسب الخدمة'}\n"
            f"💵 السعر: {money(srv[6])}$\n"
        )
    else:
        text = (
            f"📌 **{srv[2]}**\n\n"
            f"📝 **الوصف:** {srv[3] or 'غير محدد'}\n"
            f"⏳ **{duration_label}:** {srv[4] or 'حسب الخدمة'}\n"
            f"⚡ **{activation_label}:** {srv[5] or 'حسب الخدمة'}\n"
            f"💵 **السعر:** {money(srv[6])}$\n"
        )
    if srv[11]:
        text += f"\nℹ️ **تعليمات قبل الطلب:**\n{srv[11]}\n"
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
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard), entities=service_entities)


# ---------------------------------------------------------------------------
# Customer profile/orders/support/payments
# ---------------------------------------------------------------------------

async def profile(update):
    user = get_user(update.effective_user.id)
    uid = update.effective_user.id
    spent = safe_stat_value(
        "SELECT COALESCE(SUM(total),0) FROM orders WHERE user_id=%s AND COALESCE(status,'') NOT LIKE 'ملغي%'",
        params=(uid,),
    )
    approved = safe_stat_value(
        "SELECT COUNT(*) FROM orders WHERE user_id=%s AND status LIKE 'مكتمل%'",
        params=(uid,),
    )
    rejected = safe_stat_value(
        "SELECT COUNT(*) FROM orders WHERE user_id=%s AND status LIKE 'ملغي%'",
        params=(uid,),
    )
    text = (
        "👤 **حسابي**\n\n"
        f"▪️ الاسم: {user[1]}\n"
        f"▪️ الآيدي: `{user[0]}`\n"
        f"▪️ الرصيد الحالي: **{money(user[2])}$**\n"
        f"▪️ الرصيد المصروف: **{money(spent)}$**\n"
        f"▪️ عمليات الشراء المقبولة: **{approved}**\n"
        f"▪️ العمليات الملغاة/المرفوضة: **{rejected}**\n"
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


def create_free_offer_service(data, document_file_id=""):
    """Persist an optional-content free offer at a zero price and return its service id."""
    row = db_execute(
        """
        INSERT INTO services(
            category_key,name,description,subscription_duration,activation_time,
            price,delivery_mode,needs_email,needs_note,needs_phone,active,
            free_content_text,free_content_link,free_photo_file_id,free_document_file_id,name_entities_json
        ) VALUES('free',%s,%s,'','',0,'manual',FALSE,FALSE,FALSE,TRUE,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            data["service_name"], data["service_description"],
            data.get("free_content_text", ""), data.get("free_content_link", ""),
            data.get("free_photo_file_id", ""), document_file_id,
            data.get("service_name_entities_json", ""),
        ),
        fetch=True,
    )
    return row[0]


async def finish_free_offer_creation(update, context, document_file_id=""):
    sid = create_free_offer_service(context.user_data, document_file_id)
    log_operation(None, "free_offer_created", f"service={sid}", ADMIN_ID)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ تمت إضافة العرض المجاني #{sid}.\n\n"
        "سيُسلّم تلقائياً للعميل أي نص أو رابط أو صورة أو ملف قمت بإرفاقه."
    )


async def finish_regular_service_creation(update, context, photo_file_id="", document_file_id=""):
    """Create a paid service and persist only optional content supplied by the administrator."""
    d = context.user_data
    options = set(d.get("service_options", []))
    row = db_execute(
        """
        INSERT INTO services(
            category_key,name,description,subscription_duration,activation_time,price,delivery_mode,
            needs_email,needs_note,needs_phone,pre_purchase_message,service_content_text,service_content_link,
            service_photo_file_id,service_document_file_id,file_id,file_kind,customer_request_prompt,name_entities_json,active
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        RETURNING id
        """,
        (
            d["service_category"], d["service_name"], d["service_description"],
            d["service_duration"], d["service_activation"], d["service_price"], d["service_mode"],
            "email" in options, "note" in options, "phone" in options,
            d.get("service_pre_message", ""), d.get("service_content_text", ""), d.get("service_content_link", ""),
            photo_file_id, document_file_id,
            # Keep legacy media columns populated as a safe fallback for previously existing delivery paths.
            photo_file_id or document_file_id,
            "photo" if photo_file_id else ("document" if document_file_id else ""),
            d.get("service_request_prompt", ""),
            d.get("service_name_entities_json", ""),
        ),
        fetch=True,
    )
    sid = row[0]
    log_operation(None, "service_created", f"service={sid};category={d['service_category']}", ADMIN_ID)
    name, price, category = d["service_name"], d["service_price"], d["service_category"]
    context.user_data.clear()
    await update.message.reply_text(
        "✅ **تمت إضافة الخدمة بالكامل.**\n\n"
        f"🛒 {name}\n💵 {price}$\n📂 {category}\n\n"
        "تم حفظ الملاحظة قبل الشراء والنص والرابط والصورة والملف عند توفرها. "
        "لا تصل محتويات التسليم المدفوعة إلى العميل إلا بعد نجاح الدفع.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def service_media_handler(update, context):
    """Save optional Telegram media for free offers and paid-service delivery."""
    if update.effective_user.id != ADMIN_ID:
        return False
    state = context.user_data.get("admin_state")
    message = update.message

    if state == "free_offer_photo":
        if not message.photo:
            await update.message.reply_text("❌ أرسل الصورة كصورة تيليجرام، أو أرسل علامة - لتخطيها.")
            return True
        context.user_data["free_photo_file_id"] = message.photo[-1].file_id
        context.user_data["admin_state"] = "free_offer_document"
        await update.message.reply_text("📎 تم حفظ الصورة. أرسل الآن ملف العرض، أو أرسل علامة - لتخطيه وإنشاء العرض.")
        return True

    if state == "free_offer_document":
        if not message.document:
            await update.message.reply_text("❌ أرسل الملف كمستند تيليجرام، أو أرسل علامة - لتخطيه وإنشاء العرض.")
            return True
        await finish_free_offer_creation(update, context, message.document.file_id)
        return True

    if state == "service_add_photo":
        if not message.photo:
            await update.message.reply_text("❌ أرسل الصورة كصورة تيليجرام، أو أرسل - لتخطيها.")
            return True
        context.user_data["service_add_photo_file_id"] = message.photo[-1].file_id
        context.user_data["admin_state"] = "service_add_document"
        await update.message.reply_text("📎 تم حفظ الصورة. أرسل الآن ملف الخدمة، أو أرسل - لإنشاء الخدمة.")
        return True

    if state == "service_add_document":
        if not message.document:
            await update.message.reply_text("❌ أرسل الملف كمستند تيليجرام، أو أرسل - لإنشاء الخدمة.")
            return True
        await finish_regular_service_creation(
            update,
            context,
            context.user_data.get("service_add_photo_file_id", ""),
            message.document.file_id,
        )
        return True

    if state == "service_edit_paid_photo":
        if not message.photo:
            await update.message.reply_text("❌ أرسل الصورة كصورة تيليجرام.")
            return True
        sid = context.user_data.get("service_file_id")
        db_execute(
            "UPDATE services SET service_photo_file_id=%s,file_id=%s,file_kind='photo' WHERE id=%s",
            (message.photo[-1].file_id, message.photo[-1].file_id, sid),
        )
        log_operation(None, "service_paid_photo_updated", f"service={sid}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text("✅ تم حفظ صورة الخدمة للتسليم التلقائي بعد الدفع.")
        return True

    if state == "service_edit_paid_document":
        if not message.document:
            await update.message.reply_text("❌ أرسل الملف كمستند تيليجرام.")
            return True
        sid = context.user_data.get("service_file_id")
        db_execute(
            "UPDATE services SET service_document_file_id=%s,file_id=%s,file_kind='document' WHERE id=%s",
            (message.document.file_id, message.document.file_id, sid),
        )
        log_operation(None, "service_paid_document_updated", f"service={sid}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text("✅ تم حفظ ملف الخدمة للتسليم التلقائي بعد الدفع.")
        return True

    if state != "service_file":
        return False
    sid = context.user_data.get("service_file_id")
    if message.photo:
        file_id, file_kind = message.photo[-1].file_id, "photo"
        db_execute(
            "UPDATE services SET file_id=%s,file_kind=%s,service_photo_file_id=%s WHERE id=%s",
            (file_id, file_kind, file_id, sid),
        )
    elif message.document:
        file_id, file_kind = message.document.file_id, "document"
        db_execute(
            "UPDATE services SET file_id=%s,file_kind=%s,service_document_file_id=%s WHERE id=%s",
            (file_id, file_kind, file_id, sid),
        )
    else:
        return False
    log_operation(None, "service_media_updated", f"service={sid};kind={file_kind}", ADMIN_ID)
    context.user_data.clear()
    await update.message.reply_text("✅ تم حفظ المرفق للخدمة. سيتم تسليمه تلقائياً بعد نجاح شراء الخدمة.")
    return True


async def receipt_photo_handler(update, context):
    if await service_media_handler(update, context):
        return
    if not context.user_data.get("waiting_topup_receipt"):
        return
    if not await allowed(update, context):
        return
    photo_file_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()
    await submit_topup_receipt(update, context, receipt_number=caption, photo_file_id=photo_file_id)


async def document_handler(update, context):
    await service_media_handler(update, context)


async def deliver_service_media(bot, user_id, file_id, file_kind, caption):
    if file_kind == "photo":
        await bot.send_photo(user_id, file_id, caption=caption)
    else:
        await bot.send_document(user_id, file_id, caption=caption)



async def deliver_paid_service_attachment(context, uid, sid, service_name, order_id):
    """Deliver paid text/link and any photo/document only after the order has committed."""
    row = db_execute(
        """
        SELECT service_content_text,service_content_link,service_photo_file_id,service_document_file_id,file_id,file_kind
        FROM services WHERE id=%s
        """,
        (sid,),
        fetch=True,
    )
    if not row:
        return False
    content, link, photo_id, document_id, legacy_file_id, legacy_file_kind = row
    content, link = (content or "").strip(), (link or "").strip()
    photo_id, document_id = (photo_id or "").strip(), (document_id or "").strip()
    try:
        delivered = False
        if content or link:
            message = f"📦 **محتوى الخدمة: {service_name}**"
            if content:
                message += f"\n\n{content}"
            if link:
                message += f"\n\n🔗 الرابط: {link}"
            await context.bot.send_message(uid, message, parse_mode=ParseMode.MARKDOWN)
            delivered = True
        if photo_id:
            await context.bot.send_photo(uid, photo_id, caption=f"🖼️ مرفق خدمة: {service_name}")
            delivered = True
        if document_id:
            await context.bot.send_document(uid, document_id, caption=f"📎 مرفق خدمة: {service_name}")
            delivered = True
        # Compatibility fallback for services created before the new separate photo/document fields.
        if not photo_id and not document_id and legacy_file_id and legacy_file_kind:
            await deliver_service_media(context.bot, uid, legacy_file_id, legacy_file_kind, f"📎 مرفق الخدمة: {service_name}")
            delivered = True
        if delivered:
            log_operation(uid, "paid_service_content_delivered", f"order={order_id};service={sid}")
        return delivered
    except TelegramError as exc:
        log.warning("Paid service content delivery failed for order %s: %s", order_id, exc)
        return False


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

async def purchase_channel_bot_username(bot):
    """Return the configured username, or resolve it at runtime without persisting a secret."""
    if BOT_USERNAME:
        return BOT_USERNAME
    try:
        profile = await bot.get_me()
        return (profile.username or "").lstrip("@")
    except TelegramError as exc:
        log.warning("Could not resolve bot username for purchase channel: %s", exc)
        return ""


async def publish_purchase_to_channel(context, sid, service_name, price, order_id, status):
    """Publish only store-level purchase facts after a successful commit; never publish customer data."""
    if not PURCHASE_CHANNEL_CHAT_ID:
        return False
    try:
        channel_id = int(PURCHASE_CHANNEL_CHAT_ID)
    except (TypeError, ValueError):
        log.warning("PURCHASE_CHANNEL_CHAT_ID is invalid; channel notification skipped.")
        return False

    try:
        target_chat = await context.bot.get_chat(channel_id)
        target_type = str(getattr(target_chat, "type", "")).lower()
        if target_type != "channel":
            log.warning(
                "PURCHASE_CHANNEL_CHAT_ID points to %s, not a channel; post skipped for order %s.",
                target_type or "unknown", order_id,
            )
            log_operation(None, "purchase_channel_invalid_target", f"order={order_id};type={target_type or 'unknown'}", ADMIN_ID)
            return False
    except TelegramError as exc:
        log.warning("Purchase channel validation failed for order %s: %s", order_id, exc)
        log_operation(None, "purchase_channel_validation_failed", f"order={order_id};error={str(exc)[:300]}", ADMIN_ID)
        return False

    username = await purchase_channel_bot_username(context.bot)
    if not username:
        log.warning("Bot username unavailable; purchase channel notification skipped for order %s.", order_id)
        return False

    entry_url = f"https://t.me/{username}"
    purchase_url = f"{entry_url}?start=service_{sid}"
    text = (
        "🛍️ طلب جديد في متجر B-Fix\n\n"
        f"🛒 الخدمة: {service_name}\n"
        f"💵 السعر: {money(price)}$\n"
        f"🆔 رقم الطلب: #{order_id}\n"
        f"✅ الحالة: {status}\n\n"
        "استخدم الأزرار أدناه للدخول إلى البوت أو فتح صفحة الخدمة."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دخول البوت", url=entry_url)],
        [InlineKeyboardButton("🛒 شراء الخدمة", url=purchase_url)],
    ])
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        log_operation(None, "purchase_channel_published", f"order={order_id};service={sid};status={status}", ADMIN_ID)
        return True
    except TelegramError as exc:
        # A post-notification failure must never change a completed charge/order.
        log.warning("Purchase channel publish failed for order %s: %s", order_id, exc)
        log_operation(None, "purchase_channel_publish_failed", f"order={order_id};service={sid};error={str(exc)[:300]}", ADMIN_ID)
        return False


async def begin_order(update, context, sid):
    uid = update.effective_user.id
    srv = db_execute(
        """
        SELECT id,category_key,name,description,subscription_duration,
               activation_time,price,delivery_mode,needs_email,needs_note,
               needs_phone,file_id,file_kind,pre_purchase_message,service_content_text,service_content_link,
               service_photo_file_id,service_document_file_id,
               free_content_text,free_content_link,free_photo_file_id,free_document_file_id,
               customer_request_prompt
        FROM services WHERE id=%s AND COALESCE(active,TRUE)=TRUE
        """,
        (sid,),
        fetch=True,
    )
    if not srv:
        await update.callback_query.answer("❌ الخدمة غير موجودة.", show_alert=True)
        return

    if srv[1] == "free":
        await claim_free_offer(update, context, srv)
        return

    if srv[7] == "stock":
        item = db_execute(
            "SELECT id FROM inventory WHERE service_id=%s AND is_sold=FALSE LIMIT 1",
            (sid,),
            fetch=True,
        )
        if not item:
            await show_out_of_stock(update, uid, sid)
            return

    user = get_user(uid)
    price = Decimal(str(srv[6]))
    if Decimal(str(user[2])) < price:
        await show_insufficient_balance(update, uid, sid, price, user[2])
        return

    context.user_data.clear()
    context.user_data["order_service_id"] = sid
    context.user_data["order_service_name"] = srv[2]
    context.user_data["order_price"] = str(price)
    context.user_data["order_email_required"] = srv[8]
    context.user_data["order_note_required"] = srv[9]
    context.user_data["order_phone_required"] = srv[10]
    context.user_data["order_category"] = srv[1]
    context.user_data["order_file_id"] = srv[11] or ""
    context.user_data["order_file_kind"] = srv[12] or ""
    context.user_data["order_pre_purchase_message"] = srv[13] or ""
    context.user_data["order_customer_request_prompt"] = srv[22] or ""

    if srv[8]:
        await update.callback_query.message.edit_text(
            f"📧 طلب {srv[2]}\n\n"
            f"{(srv[13] or '').strip() + chr(10) + chr(10) if (srv[13] or '').strip() else ''}"
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

    if srv[9] or context.user_data.get("order_customer_request_prompt"):
        prompt = context.user_data.get("order_customer_request_prompt") or "📝 أرسل ملاحظتك للطلب.\n\nإذا لم تكن لديك ملاحظة، أرسل: لا يوجد"
        await update.callback_query.message.edit_text(
            prompt,
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="flowcancel:main")]
            ]),
        )
        return

    await request_order_confirmation(update, context)


def format_remaining_time(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def free_offer_remaining_seconds(user_id):
    row = db_execute(
        """
        SELECT EXTRACT(EPOCH FROM (claimed_at + INTERVAL '6 hours' - CURRENT_TIMESTAMP))
        FROM free_offer_claims WHERE user_id=%s
        """,
        (user_id,),
        fetch=True,
    )
    if not row or row[0] is None:
        return 0
    return max(0, int(float(row[0])))


async def show_free_offer_wait(update, user_id, sid):
    remaining = free_offer_remaining_seconds(user_id)
    if remaining <= 0:
        await send_or_edit(
            update,
            "✅ **انتهت مدة الانتظار.**\n\nيمكنك الآن اختيار عرض مجاني جديد.",
            InlineKeyboardMarkup([
                [green_button("🎁 عرض العروض المجانية", callback_data="cat:free")],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
        return False
    await send_or_edit(
        update,
        "🎁 **العروض المجانية متاحة كل 6 ساعات**\n\n"
        "لقد استلمت عرضك المجاني الأخير بالفعل.\n"
        f"⏳ الوقت المتبقي: **{format_remaining_time(remaining)}**\n\n"
        "يمكنك تحديث العداد في أي وقت.",
        InlineKeyboardMarkup([
            [green_button("🔄 تحديث الوقت", callback_data=f"free_offer_timer:{sid}")],
            [red_button("↩️ العروض المجانية", callback_data="cat:free")],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )
    return True


async def free_offer_timer(update, context, sid):
    if update.effective_user.id == ADMIN_ID:
        await send_or_edit(
            update,
            "👑 **وضع المدير**\n\nأنت معفى من قيد العروض المجانية ويمكنك الاستلام للاختبار في أي وقت.",
            InlineKeyboardMarkup([
                [green_button("🎁 عرض العروض المجانية", callback_data="cat:free")],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
        return
    await show_free_offer_wait(update, update.effective_user.id, sid)


async def show_insufficient_balance(update, user_id, sid, price, balance):
    price = Decimal(str(price))
    balance = Decimal(str(balance))
    shortage = max(Decimal("0"), price - balance)
    log_operation(user_id, "purchase_blocked_insufficient_balance", f"service={sid};price={money(price)};balance={money(balance)};shortage={money(shortage)}")
    await send_or_edit(
        update,
        "❌ **رصيدك غير كافٍ لإتمام الطلب**\n\n"
        f"💵 سعر الخدمة: **{money(price)}$**\n"
        f"💰 رصيدك الحالي: **{money(balance)}$**\n"
        f"⚠️ المبلغ الناقص: **{money(shortage)}$**\n\n"
        "اشحن حسابك ثم عُد لإتمام الشراء.",
        InlineKeyboardMarkup([
            [green_button("💰 تغذية حسابك", callback_data="fund")],
            [red_button("↩️ رجوع للخدمة", callback_data=f"service:{sid}")],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )


async def show_out_of_stock(update, user_id, sid):
    log_operation(user_id, "purchase_blocked_out_of_stock", f"service={sid}")
    await send_or_edit(
        update,
        "📦 **نفدت الكمية من المخزون**\n\n"
        "هذه الخدمة غير متاحة للتسليم الفوري حالياً. لم يُخصم أي مبلغ من رصيدك ولم يُنشأ أي طلب. "
        "يمكنك المحاولة لاحقاً بعد إضافة كمية جديدة.",
        InlineKeyboardMarkup([
            [red_button("↩️ رجوع للخدمة", callback_data=f"service:{sid}")],
            [red_button("🏠 الرئيسية", callback_data="main")],
        ]),
    )


async def claim_free_offer(update, context, srv):
    """Deliver one free offer every six hours for customers; the configured manager is exempt."""
    uid, sid = update.effective_user.id, srv[0]
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            if uid == ADMIN_ID:
                cur.execute(
                    """
                    INSERT INTO free_offer_claims(user_id,service_id,claimed_at)
                    VALUES(%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE
                    SET service_id=EXCLUDED.service_id,order_id=NULL,claimed_at=CURRENT_TIMESTAMP
                    RETURNING user_id
                    """,
                    (uid, sid),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO free_offer_claims(user_id,service_id,claimed_at)
                    VALUES(%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE
                    SET service_id=EXCLUDED.service_id,order_id=NULL,claimed_at=CURRENT_TIMESTAMP
                    WHERE free_offer_claims.claimed_at <= CURRENT_TIMESTAMP - INTERVAL '6 hours'
                    RETURNING user_id
                    """,
                    (uid, sid),
                )
            if not cur.fetchone():
                conn.rollback()
                await show_free_offer_wait(update, uid, sid)
                return
            cur.execute(
                """
                INSERT INTO orders(user_id,service_id,status,total,delivered_text)
                VALUES(%s,%s,'قيد التسليم ⏳',0,'جارٍ تسليم محتوى العرض المجاني')
                RETURNING id
                """,
                (uid, sid),
            )
            oid = cur.fetchone()[0]
            cur.execute("UPDATE free_offer_claims SET order_id=%s WHERE user_id=%s", (oid, uid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)

    content = (srv[18] or "").strip()
    link = (srv[19] or "").strip()
    photo_id = (srv[20] or "").strip()
    document_id = (srv[21] or "").strip()
    try:
        if content or link:
            message = f"🎁 **{srv[2]}**\n\n{content}"
            if link:
                message += f"\n\n🔗 الرابط: {link}"
            await context.bot.send_message(uid, message, parse_mode=ParseMode.MARKDOWN)
        if photo_id:
            await context.bot.send_photo(uid, photo_id, caption=f"🖼️ صورة عرض: {srv[2]}")
        if document_id:
            await context.bot.send_document(uid, document_id, caption=f"📎 ملف عرض: {srv[2]}")
        db_execute(
            "UPDATE orders SET status='مكتمل ✅ - تم تسليم العرض',delivered_text=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            ("تم تسليم النص والرابط والصورة والملف تلقائياً.", oid),
        )
        log_operation(uid, "free_offer_claimed", f"order={oid};service={sid};admin_exempt={uid == ADMIN_ID}")
        timing_line = "👑 أنت معفى من القيد بصفتك المدير." if uid == ADMIN_ID else "⏳ يمكنك استلام عرض جديد بعد **6 ساعات** من الآن."
        await send_or_edit(
            update,
            "✅ **تم استلام العرض المجاني بنجاح**\n\n"
            "أرسل البوت النص والرابط والصورة والملف في رسائل منفصلة عند توفرها.\n"
            f"{timing_line}",
            InlineKeyboardMarkup([
                [green_button("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],
                [green_button("🔄 تحديث الوقت", callback_data=f"free_offer_timer:{sid}")],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
    except TelegramError as exc:
        log.warning("Free offer delivery failed for order %s: %s", oid, exc)
        await send_or_edit(
            update,
            "⚠️ تم تسجيل طلب العرض المجاني، لكن تعذر تسليم أحد المرفقات تلقائياً. ستراجعه الإدارة.",
            InlineKeyboardMarkup([
                [green_button("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],
                [red_button("🏠 الرئيسية", callback_data="main")],
            ]),
        )
    await context.bot.send_message(
        ADMIN_ID,
        f"🎁 **مطالبة عرض مجاني #{oid}**\n\n👤 {update.effective_user.full_name}\n🆔 `{uid}`\n🛒 {srv[2]}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[green_button("📄 فتح الطلب", callback_data=f"adm:order:{oid}")]]),
    )


async def request_order_confirmation(update, context):
    """Show only the optional pre-purchase note before the final balance charge."""
    guidance = (context.user_data.get("order_pre_purchase_message") or "").strip()
    if not guidance:
        await finalize_customer_order(update, context)
        return
    context.user_data["order_confirm_required"] = True
    await send_or_edit(
        update,
        f"ℹ️ **ملاحظة قبل شراء الخدمة**\n\n{guidance}\n\n"
        "بعد مراجعة الملاحظة اضغط «متابعة الطلب» ليتم خصم الرصيد وإنشاء الطلب. "
        "النص والرابط والمرفقات الخاصة بالخدمة تُرسل بعد نجاح الشراء فقط.",
        InlineKeyboardMarkup([
            [green_button("✅ متابعة الطلب", callback_data="buyconfirm:continue")],
            [red_button("🚫 إلغاء", callback_data="flowcancel:main")],
        ]),
    )


async def finalize_customer_order(update, context):
    uid = update.effective_user.id
    sid = int(context.user_data["order_service_id"])
    price = Decimal(context.user_data["order_price"])
    category = context.user_data["order_category"]
    email = context.user_data.get("order_email", "")
    note = context.user_data.get("order_note", "")
    phone = context.user_data.get("order_phone", "")

    # External provider order: use B-Fix sale price, never the provider price.
    service_mode = db_execute("SELECT delivery_mode FROM services WHERE id=%s", (sid,), fetch=True)[0]
    if service_mode == "external":
        mapping = db_execute(
            "SELECT provider_service_id FROM external_service_map WHERE service_id=%s", (sid,), fetch=True
        )
        if not mapping:
            await send_or_edit(update, "❌ إعداد المورد لهذه الخدمة غير مكتمل.", InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]))
            return
        provider_service_id = mapping[0]
        conn = DB_POOL.getconn()
        oid = None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT balance FROM users WHERE user_id=%s FOR UPDATE", (uid,))
                row = cur.fetchone()
                if not row or Decimal(str(row[0])) < price:
                    current_balance = row[0] if row else Decimal("0")
                    conn.rollback()
                    await show_insufficient_balance(update, uid, sid, price, current_balance)
                    return
                cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (price, uid))
                cur.execute(
                    """INSERT INTO orders(user_id,service_id,status,total,customer_email,customer_note,customer_phone)
                       VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (uid, sid, "جارٍ الإرسال للمورد ⏳", price, email, note, phone),
                )
                oid = cur.fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            DB_POOL.putconn(conn)

        provider_input = "\n".join(x for x in [f"Email: {email}" if email else "", f"Phone: {phone}" if phone else "", note] if x)
        try:
            result = external_add_order(provider_service_id, provider_input)
            provider_order_id = provider_value(result, "order", "order_id", "id")
            provider_status = str(provider_value(result, "status", default="submitted"))
            if not provider_order_id:
                raise RuntimeError(f"المزود رفض الطلب: {external_error_summary(result)}")
            db_execute(
                "UPDATE orders SET provider_order_id=%s,provider_status=%s,status='قيد التنفيذ ⏳',updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (str(provider_order_id), provider_status, oid),
            )
        except Exception as exc:
            log.exception("External order failed: %s", exc)
            conn = DB_POOL.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT status,total,user_id FROM orders WHERE id=%s FOR UPDATE", (oid,))
                    current = cur.fetchone()
                    if current and not str(current[0]).startswith("ملغي") and not str(current[0]).startswith("مكتمل"):
                        cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (price, uid))
                        cur.execute("UPDATE orders SET status='ملغي ❌ - فشل المورد',provider_status=%s,provider_refunded=TRUE,updated_at=CURRENT_TIMESTAMP WHERE id=%s", (str(exc)[:500], oid))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                DB_POOL.putconn(conn)
            reason = re.sub(r"[\*_`\[\]]", "", str(exc))[:300]
            await send_or_edit(
                update,
                f"❌ تعذر تنفيذ الطلب لدى المورد.\n\n"
                f"⚠️ السبب: {reason}\n"
                f"💰 تمت إعادة {money(price)}$ إلى رصيدك.",
                InlineKeyboardMarkup([[red_button("🏠 الرئيسية", callback_data="main")]]),
            )
            return

        await deliver_paid_service_attachment(context, uid, sid, context.user_data["order_service_name"], oid)
        await publish_purchase_to_channel(
            context, sid, context.user_data["order_service_name"], price, oid,
            f"تم إرساله للمزود — {provider_status}",
        )
        log_operation(uid, "external_purchase", f"order={oid};service={sid};provider_order={provider_order_id};price={money(price)}")
        await send_or_edit(update, f"✅ **تم إرسال طلبك للمورد بنجاح**\n\n🛒 الخدمة: {context.user_data['order_service_name']}\n💵 المبلغ: {money(price)}$\n🆔 رقم الطلب: `{provider_order_id}`\n\n⏳ الحالة: {provider_status}", InlineKeyboardMarkup([[InlineKeyboardButton("📦 تفاصيل الطلب", callback_data=f"order:{oid}")],[red_button("🏠 الرئيسية", callback_data="main")]]), parse_mode=ParseMode.MARKDOWN)
        return

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
                    await show_out_of_stock(update, uid, sid)
                    return

                cur.execute(
                    "SELECT balance FROM users WHERE user_id=%s FOR UPDATE",
                    (uid,),
                )
                balance = cur.fetchone()[0]
                if Decimal(str(balance)) < price:
                    conn.rollback()
                    await show_insufficient_balance(update, uid, sid, price, balance)
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

        await deliver_paid_service_attachment(context, uid, sid, context.user_data["order_service_name"], oid)
        await publish_purchase_to_channel(
            context, sid, context.user_data["order_service_name"], price, oid,
            "تم التسليم الفوري",
        )
        log_operation(uid, "stock_purchase", f"order={oid};service={sid};price={price}")
        await send_or_edit(
            update,
            f"🎉 **تم تنفيذ طلبك بنجاح!**\n\n"
            f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
            f"💵 المبلغ: {money(price)}$\n\n"
            "🎁 **بيانات التسليم/الكود:**\n"
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
                await show_insufficient_balance(update, uid, sid, price, balance)
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

    await publish_purchase_to_channel(
        context, sid, context.user_data["order_service_name"], price, oid,
        "قيد التنفيذ",
    )
    log_operation(uid, "manual_order", f"order={oid};service={sid};price={price}")

    auto_delivered = False
    file_id = context.user_data.get("order_file_id", "")
    file_kind = context.user_data.get("order_file_kind", "")
    if category == "free" and file_id and file_kind:
        try:
            await deliver_service_media(
                context.bot,
                uid,
                file_id,
                file_kind,
                f"🎁 عرض مجاني: {context.user_data['order_service_name']}",
            )
            db_execute(
                "UPDATE orders SET status='مكتمل ✅ - تم تسليم الملف',delivered_text=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                ("تم تسليم المرفق تلقائياً.", oid),
            )
            log_operation(uid, "free_media_delivered", f"order={oid};service={sid}")
            auto_delivered = True
        except TelegramError as exc:
            log.warning("Automatic free media delivery failed for order %s: %s", oid, exc)

    paid_content_delivered = False
    if category != "free":
        paid_content_delivered = await deliver_paid_service_attachment(
            context, uid, sid, context.user_data["order_service_name"], oid
        )

    if auto_delivered:
        order_message = "🎁 تم تسليم ملف العرض المجاني تلقائياً.\n📌 يمكنك متابعة العملية من «طلباتي»."
    else:
        order_message = (
            ("📎 تم تسليم النص أو الرابط أو المرفق الخاص بالخدمة تلقائياً.\n" if paid_content_delivered else "")
            + "⏳ سيتم تنفيذ وتفعيل طلبك خلال أقل وقت ممكن.\n"
            "📌 يمكنك متابعة حالة الطلب من «طلباتي».\n\n"
            "🆘 إذا لم تستلم طلبك، استخدم زر التواصل مع الإدارة من تفاصيل الطلب."
        )
    await send_or_edit(
        update,
        f"✅ **تم استلام طلبك بنجاح**\n\n"
        f"🛒 الخدمة: {context.user_data['order_service_name']}\n"
        f"🆔 رقم الطلب: **#{oid}**\n"
        f"💵 المبلغ: {money(price)}$\n\n"
        f"{order_message}",
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
        if context.user_data.get("order_note_required") or context.user_data.get("order_customer_request_prompt"):
            await update.message.reply_text(
                context.user_data.get("order_customer_request_prompt") or "📝 أرسل ملاحظتك للطلب، أو اكتب: لا يوجد"
            )
            return
        await request_order_confirmation(update, context)
        return

    if context.user_data.get("order_phone_required") and "order_phone" not in context.user_data:
        context.user_data["order_phone"] = update.message.text.strip()
        if context.user_data.get("order_note_required") or context.user_data.get("order_customer_request_prompt"):
            await update.message.reply_text(
                context.user_data.get("order_customer_request_prompt") or "📝 أرسل ملاحظتك للطلب، أو اكتب: لا يوجد"
            )
            return
        await request_order_confirmation(update, context)
        return

    if (context.user_data.get("order_note_required") or context.user_data.get("order_customer_request_prompt")) and "order_note" not in context.user_data:
        note = update.message.text.strip()
        context.user_data["order_note"] = "" if note == "لا يوجد" else note
        await request_order_confirmation(update, context)
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
            InlineKeyboardButton("💰 شحن رصيد عميل", callback_data="adm:credit_user"),
            InlineKeyboardButton("➖ خصم رصيد عميل", callback_data="adm:debit_user"),
        ],
        [
            InlineKeyboardButton("🛒 الخدمات الخارجية", callback_data="adm:external"),
            InlineKeyboardButton("🧾 سندات التحويل", callback_data="adm:topups"),
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
            InlineKeyboardButton("📣 اختبار قناة المشتريات", callback_data="adm:test_purchase_channel"),
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
        await admin_services(update)
    elif data.startswith("services_by_category:"):
        category = data.split(":", 1)[1]
        await admin_services_by_category(update, category)
    elif data.startswith("add_service_in_category:"):
        category = data.split(":", 1)[1]
        await admin_add_service_prompt(update, context, preset_category=category)
    elif data.startswith("service:"):
        await admin_service_detail(update, int(data.split(":", 1)[1]))
    elif data.startswith("serviceedit:"):
        _prefix, sid, field = data.split(":", 2)
        prompts = {
            "name": "✏️ أرسل الاسم الجديد للخدمة:",
            "description": "📝 أرسل الوصف الجديد للخدمة:",
            "emoji": "🎞️ أرسل اسم الخدمة أو رمز Custom Emoji المتحرك في رسالة واحدة، أو أرسل - لمسحه.",
            "price": "💵 أرسل السعر الجديد بالأرقام فقط، مثال: 5.50",
            "duration": "⏳ أرسل مدة الاشتراك الجديدة، أو اكتب: غير محدد",
            "activation": "⚡ أرسل مدة التفعيل أو التسليم الجديدة، أو اكتب: غير محدد",
            "pre_message": "💬 أرسل الملاحظة التي تظهر للعميل قبل شراء هذه الخدمة، أو أرسل - لمسحها.",
            "content_text": "📝 أرسل النص الذي يُسلّم للعميل بعد نجاح الشراء، أو أرسل - لمسحه.",
            "content_link": "🔗 أرسل الرابط الذي يُسلّم للعميل بعد نجاح الشراء، أو أرسل - لمسحه.",
            "request_prompt": "🧾 أرسل ما تريد أن يكتبه العميل لتنفيذ الخدمة، أو أرسل - لمسحه.",
        }
        if field not in prompts:
            await q.answer("❌ حقل غير صالح.", show_alert=True)
            return
        context.user_data.clear()
        context.user_data.update({
            "admin_state": "service_edit_emoji" if field == "emoji" else "service_edit_field",
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
    elif data.startswith("attachfile:"):
        sid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data["admin_state"] = "service_file"
        context.user_data["service_file_id"] = sid
        await q.message.edit_text(
            "📎 أرسل الآن صورة أو ملفاً لتسليمه تلقائياً مع هذه الخدمة.\n\n"
            "يفضّل استخدام هذا الخيار لخدمات وعروض القسم المجاني. لإلغاء العملية اضغط الزر أدناه.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data=f"adm:service:{sid}")],
                *admin_navigation_rows("adm:services", "الخدمات"),
            ]),
        )
    elif data.startswith("attachpaidphoto:"):
        sid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data.update({"admin_state": "service_edit_paid_photo", "service_file_id": sid})
        await q.message.edit_text(
            "🖼️ أرسل الآن الصورة التي ستصل للعميل تلقائياً بعد نجاح الشراء.",
            reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data=f"adm:service:{sid}")], *admin_navigation_rows("adm:services", "الخدمات")]),
        )
    elif data.startswith("attachpaiddocument:"):
        sid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data.update({"admin_state": "service_edit_paid_document", "service_file_id": sid})
        await q.message.edit_text(
            "📄 أرسل الآن الملف الذي سيصل للعميل تلقائياً بعد نجاح الشراء.",
            reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data=f"adm:service:{sid}")], *admin_navigation_rows("adm:services", "الخدمات")]),
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
        if mode not in {"manual", "stock", "external"}:
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
        context.user_data.clear()
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
    elif data == "credit_user":
        context.user_data.clear()
        context.user_data["admin_state"] = "credit_user_id"
        await q.message.edit_text(
            "💰 أرسل آيدي العميل الذي تريد شحن رصيده.\n\nلن يتم الشحن إلا بعد التحقق من العميل ثم إدخال المبلغ.",
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data="adm:home")],
                *admin_navigation_rows(),
            ]),
        )
    elif data == "external":
        context.user_data.clear()
        await admin_external_services(update, context, 0)
    elif data.startswith("external_page:"):
        await admin_external_services(update, context, int(data.split(":", 1)[1]))
    elif data.startswith("external_add:"):
        provider_id = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["admin_state"] = "external_price"
        context.user_data["external_provider_id"] = provider_id
        await q.message.edit_text("💵 أرسل سعر البيع الذي تريده لهذه الخدمة في B-Fix (مثال: 5.00):", reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data="adm:external")], *admin_navigation_rows()]))
    elif data.startswith("external_status:"):
        oid = int(data.split(":", 1)[1])
        row = db_execute("SELECT provider_order_id FROM orders WHERE id=%s", (oid,), fetch=True)
        if not row or not row[0]:
            await q.answer("❌ لا يوجد رقم طلب مورد.", show_alert=True)
            return
        try:
            result = external_order_status(row[0])
            status = str(provider_value(result, "status", default="غير معروف"))
            db_execute("UPDATE orders SET provider_status=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s", (status, oid))
            await q.answer(f"الحالة: {status}", show_alert=True)
            await admin_order_page(update, oid)
        except Exception as exc:
            await q.answer("❌ تعذر تحديث الحالة.", show_alert=True)
    elif data == "debit_user":
        context.user_data.clear()
        context.user_data["admin_state"] = "debit_user_id"
        await q.message.edit_text("➖ أرسل آيدي العميل الذي تريد خصم الرصيد منه.", reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data="adm:home")], *admin_navigation_rows()]))
    elif data.startswith("external_add_direct:"):
        provider_id = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["admin_state"] = "external_price"
        context.user_data["external_provider_id"] = provider_id
        await q.message.edit_text("💵 أرسل سعر البيع لهذه الخدمة:", reply_markup=InlineKeyboardMarkup([[red_button("🚫 إلغاء", callback_data="adm:external")], *admin_navigation_rows()]))
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
    elif data == "test_purchase_channel":
        await admin_test_purchase_channel(update, context)
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
    elif data.startswith("editbuttonemoji:"):
        key = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data["admin_state"] = "button_emoji"
        context.user_data["edit_button_key"] = key
        await q.message.edit_text(
            "🎞️ أرسل Custom Emoji متحركاً وحده من لوحة الإيموجيات الخاصة، أو أرسل - لمسحه.",
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


async def admin_external_services(update, context, page=0):
    if not external_api_configured():
        await send_or_edit(
            update,
            "🛒 **الخدمات الخارجية**\n\n⚠️ لم يتم إعداد API المورد بعد. أضف EXTERNAL_API_URL وEXTERNAL_API_KEY وEXTERNAL_API_PROVIDER في Render فقط.",
            InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
        )
        return
    try:
        items = external_services_list()
    except Exception as exc:
        log.exception("Unable to fetch external services: %s", exc)
        await send_or_edit(update, "❌ تعذر جلب خدمات المورد حاليًا. تأكد من إعدادات API والاتصال.", InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]))
        return
    page_size = 10
    start = max(0, page) * page_size
    chunk = items[start:start + page_size]
    text = f"🛒 **الخدمات الخارجية**\n\nعدد الخدمات التي أعادها المورد: **{len(items)}**\nالصفحة: {page + 1}\n\n"
    keyboard = []
    for item in chunk:
        pid = str(provider_value(item, "service", "id", "service_id"))
        name = str(provider_value(item, "name", "title", default="خدمة بدون اسم"))
        price = provider_value(item, "price", "rate", default="—")
        if not pid:
            continue
        keyboard.append([green_button(f"➕ {name[:38]} | ${price}", callback_data=f"adm:external_add:{pid}")])
    if page > 0:
        keyboard.append([InlineKeyboardButton("⬅️ السابق", callback_data=f"adm:external_page:{page-1}")])
    if start + page_size < len(items):
        keyboard.append([InlineKeyboardButton("➡️ التالي", callback_data=f"adm:external_page:{page+1}")])
    keyboard.append([red_button("↩️ لوحة المشرف", callback_data="adm:home")])
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_services(update):
    """Show an admin-only category selector instead of a mixed list of all services."""
    counts = {
        category: db_execute(
            """
            SELECT COUNT(*) FROM services
            WHERE LOWER(TRIM(COALESCE(category_key,'')))=%s
            """,
            (category,),
            fetch=True,
        )[0]
        for category in CATEGORY_INFO
    }
    text = (
        "🛠️ **إدارة الخدمات حسب القسم**\n\n"
        "اختر القسم أولاً، ثم تظهر خدمات ذلك القسم فقط. "
        "يمكنك إضافة خدمة جديدة مباشرة داخل القسم المختار.\n"
    )
    keyboard = []
    for category, (title, _description) in CATEGORY_INFO.items():
        keyboard.append([
            green_button(
                f"{title} — {counts[category]} خدمة",
                callback_data=f"adm:services_by_category:{category}",
            )
        ])
    keyboard.extend(admin_navigation_rows())
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_services_by_category(update, category):
    """Show only services belonging to the manager-selected category."""
    cat = CATEGORY_INFO.get(category)
    if not cat:
        await send_or_edit(
            update,
            "❌ القسم غير متاح.",
            InlineKeyboardMarkup([[red_button("↩️ الأقسام", callback_data="adm:services")]]),
        )
        return
    rows = db_execute(
        """
        SELECT id,name,price,delivery_mode,COALESCE(active,TRUE)
        FROM services
        WHERE LOWER(TRIM(COALESCE(category_key,'')))=%s
        ORDER BY id DESC
        """,
        (category,),
        fetchall=True,
    )
    text = (
        f"🛠️ **إدارة قسم: {cat[0]}**\n\n"
        "تظهر هنا خدمات هذا القسم فقط. اضغط اسم الخدمة لتعديلها.\n\n"
    )
    keyboard = [[green_button("➕ إضافة خدمة داخل هذا القسم", callback_data=f"adm:add_service_in_category:{category}")]]
    if not rows:
        text += "لا توجد خدمات مضافة في هذا القسم حتى الآن."
    else:
        for sid, name, price, mode, active in rows:
            status = "🟢 ظاهرة" if active else "🔴 مؤرشفة"
            mode_label = "📦 مخزون" if mode == "stock" else ("🌐 خارجي" if mode == "external" else "🛠️ يدوي")
            text += f"▪️ #{sid} {name} — {money(price)}$ — {mode_label} — {status}\n"
            keyboard.append([green_button(f"📦 #{sid} {name[:45]}", callback_data=f"adm:service:{sid}")])
    keyboard.extend([
        [red_button("↩️ الأقسام", callback_data="adm:services")],
        *admin_navigation_rows(),
    ])
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_service_menu(update):
    await admin_services(update)


async def admin_service_detail(update, sid):
    row = db_execute(
        """
        SELECT id,name,category_key,description,subscription_duration,activation_time,
               price,delivery_mode,needs_email,needs_note,needs_phone,COALESCE(active,TRUE),file_id,
               file_kind,pre_purchase_message,service_content_text,service_content_link,
               service_photo_file_id,service_document_file_id,customer_request_prompt,name_entities_json
        FROM services WHERE id=%s
        """,
        (sid,),
        fetch=True,
    )
    if not row:
        await send_or_edit(
            update,
            "❌ الخدمة غير موجودة أو تم حذفها سابقاً.",
            InlineKeyboardMarkup([
                [red_button("↩️ الأقسام", callback_data="adm:services")],
                *admin_navigation_rows(),
            ]),
        )
        return
    stock = db_execute(
        "SELECT COUNT(*) FROM inventory WHERE service_id=%s AND COALESCE(is_sold,FALSE)=FALSE",
        (sid,),
        fetch=True,
    )[0]
    status = "🟢 ظاهرة للعميل" if row[11] else "🔴 مؤرشفة ومخفية"
    mode = "📦 تسليم من المخزون" if row[7] == "stock" else ("🌐 API خارجي" if row[7] == "external" else "🛠️ تنفيذ يدوي")
    provider_row = db_execute("SELECT provider_service_id,provider_price FROM external_service_map WHERE service_id=%s", (sid,), fetch=True)
    provider_line = f"🌐 Service ID المورد: `{provider_row[0]}`\n💰 تكلفة المورد: **{money(provider_row[1])}$**\n" if provider_row else ""
    text = (
        f"🧩 **تفاصيل الخدمة #{row[0]}**\n\n"
        f"📌 الاسم: **{row[1]}**\n"
        f"📂 القسم: `{row[2]}`\n"
        f"📝 الوصف: {row[3] or 'غير محدد'}\n"
        f"⏳ {'مدة الإيجار' if row[2] == 'rentals' else 'مدة الاشتراك'}: {row[4] or 'غير محددة'}\n"
        f"⚡ {'وقت تسليم بيانات الإيجار' if row[2] == 'rentals' else 'مدة التفعيل'}: {row[5] or 'غير محددة'}\n"
        f"💵 السعر: **{money(row[6])}$**\n"
        f"{provider_line}"
        f"🚚 طريقة التسليم: {mode}\n"
        f"📦 الأكواد المتاحة: {stock}\n"
        f"📧 طلب إيميل: {'نعم ✅' if row[8] else 'لا ❌'}\n"
        f"📝 طلب ملاحظة: {'نعم ✅' if row[9] else 'لا ❌'}\n"
        f"📱 طلب رقم هاتف: {'نعم ✅' if row[10] else 'لا ❌'}\n"
        f"📁 ملف مرفق: {'موجود ✅' if row[12] else 'لا يوجد'}\n"
        f"📎 نوع المرفق القديم: {row[13] or '—'}\n"
        f"💬 ملاحظة قبل الشراء: {row[14] or 'لا توجد'}\n"
        f"📝 نص يُسلّم بعد الدفع: {row[15] or 'لا يوجد'}\n"
        f"🔗 رابط يُسلّم بعد الدفع: {row[16] or 'لا يوجد'}\n"
        f"🖼️ صورة تُسلّم بعد الدفع: {'موجودة ✅' if row[17] else 'لا توجد'}\n"
        f"📄 ملف يُسلّم بعد الدفع: {'موجود ✅' if row[18] else 'لا يوجد'}\n"
        f"🧾 تفاصيل مطلوبة من العميل: {row[19] or 'لا توجد'}\n"
        f"👁️ الحالة: {status}"
    )
    keyboard = [
        [green_button("✏️ تعديل الاسم", callback_data=f"adm:serviceedit:{sid}:name"),
         green_button("📝 تعديل الوصف", callback_data=f"adm:serviceedit:{sid}:description")],
        [green_button("🎞️ الإيموجي المتحرك", callback_data=f"adm:serviceedit:{sid}:emoji")],
        [green_button("💵 تعديل السعر", callback_data=f"adm:serviceedit:{sid}:price"),
         green_button("⏳ تعديل المدة", callback_data=f"adm:serviceedit:{sid}:duration")],
        [green_button("⚡ تعديل التفعيل", callback_data=f"adm:serviceedit:{sid}:activation"),
         green_button("💬 ملاحظة قبل الشراء", callback_data=f"adm:serviceedit:{sid}:pre_message")],
        [green_button("📝 تعديل النص بعد الدفع", callback_data=f"adm:serviceedit:{sid}:content_text"),
         green_button("🔗 تعديل الرابط بعد الدفع", callback_data=f"adm:serviceedit:{sid}:content_link")],
        [green_button("🧾 تفاصيل مطلوبة من العميل", callback_data=f"adm:serviceedit:{sid}:request_prompt")],
        [green_button("🖼️ رفع/استبدال صورة", callback_data=f"adm:attachpaidphoto:{sid}"),
         green_button("📄 رفع/استبدال ملف", callback_data=f"adm:attachpaiddocument:{sid}")],
        [green_button("📦 إضافة أكواد", callback_data=f"adm:addstock:{sid}"),
         green_button("📎 مرفق توافق قديم", callback_data=f"adm:attachfile:{sid}")],
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
        [red_button("↩️ خدمات هذا القسم", callback_data=f"adm:services_by_category:{row[2]}")],
        [red_button("↩️ كل الأقسام", callback_data="adm:services")],
        *admin_navigation_rows(),
    ]
    await send_or_edit(update, text, InlineKeyboardMarkup(keyboard))


async def admin_add_service_prompt(update, context, preset_category=None):
    if preset_category in CATEGORY_INFO:
        context.user_data.clear()
        context.user_data["service_category"] = preset_category
        context.user_data["admin_state"] = "service_name"
        await update.callback_query.message.edit_text(
            f"➕ **إضافة خدمة إلى قسم {CATEGORY_INFO[preset_category][0]}**\n\n"
            "أرسل **اسم الخدمة** الآن.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [red_button("🚫 إلغاء", callback_data=f"adm:services_by_category:{preset_category}")],
                *admin_navigation_rows(),
            ]),
        )
        return

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


def safe_stat_value(query, default=0, params=()):
    """Return a statistic without making the admin panel unavailable on legacy schemas."""
    try:
        row = db_execute(query, params, fetch=True)
        return row[0] if row and row[0] is not None else default
    except Exception as exc:
        log.warning("Statistics query skipped: %s | %s", query.splitlines()[0][:80], exc)
        return default


async def admin_stats(update):
    users = safe_stat_value("SELECT COUNT(*) FROM users")
    orders = safe_stat_value("SELECT COUNT(*) FROM orders")
    services = safe_stat_value("SELECT COUNT(*) FROM services")
    stock = safe_stat_value("SELECT COUNT(*) FROM inventory")
    revenue = safe_stat_value("SELECT COALESCE(SUM(total),0) FROM orders")
    approved = safe_stat_value("SELECT COUNT(*) FROM orders WHERE status LIKE 'مكتمل%'")
    pending = safe_stat_value("SELECT COUNT(*) FROM orders WHERE status LIKE 'قيد التنفيذ%'")
    text = (
        "📊 **إحصائيات المتجر**\n\n"
        f"👥 العملاء: {users}\n"
        f"📦 جميع الطلبات: {orders}\n"
        f"✅ الطلبات المكتملة: {approved}\n"
        f"⏳ الطلبات قيد التنفيذ: {pending}\n"
        f"🛒 الخدمات المسجلة: {services}\n"
        f"🔑 عناصر المخزون المسجلة: {stock}\n"
        f"💰 إجمالي المبيعات المسجلة: {money(revenue)}$"
    )
    await send_or_edit(
        update,
        text,
        InlineKeyboardMarkup([
            [green_button("🔄 تحديث الإحصائيات", callback_data="adm:stats")],
            *admin_navigation_rows(),
        ]),
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
    """Show recent customers without assuming optional legacy columns exist."""
    try:
        rows = db_execute(
            """
            SELECT
                u.user_id,
                COALESCE(NULLIF(to_jsonb(u)->>'name',''),'عميل بدون اسم') AS name,
                COALESCE(to_jsonb(u)->>'username','') AS username,
                COALESCE(NULLIF(to_jsonb(u)->>'balance','')::NUMERIC,0) AS balance,
                COALESCE(to_jsonb(u)->>'is_blocked','false') AS is_blocked
            FROM users u
            ORDER BY u.user_id DESC
            LIMIT 25
            """,
            fetchall=True,
        )
    except Exception as exc:
        log.exception("Unable to load admin users list: %s", exc)
        await send_or_edit(
            update,
            "⚠️ تعذر تحميل قائمة العملاء مؤقتاً. أعد نشر آخر ملف `bot.py` ثم جرّب مجدداً.",
            InlineKeyboardMarkup(admin_navigation_rows()),
        )
        return

    if not rows:
        await send_or_edit(
            update,
            "👥 **العملاء**\n\nلا يوجد عملاء مسجلون حتى الآن.",
            InlineKeyboardMarkup(admin_navigation_rows()),
        )
        return

    lines = ["👥 **آخر 25 عميلاً**", ""]
    for uid, name, username, balance, blocked in rows:
        # This screen uses Markdown. Remove only markup control characters from user-provided names.
        clean_name = re.sub(r"[\*_`\[\]]", "", str(name or "عميل بدون اسم"))[:60]
        clean_username = re.sub(r"[\*_`\[\]]", "", str(username or "")).strip().lstrip("@")[:32]
        status = "🚫 محظور" if str(blocked).strip().lower() in {"true", "t", "1", "yes"} else "🟢 نشط"
        username_line = f" | @{clean_username}" if clean_username else ""
        lines.append(f"▪️ {clean_name} | `{uid}` | {money(balance)}$ | {status}{username_line}")

    await send_or_edit(
        update,
        "\n".join(lines),
        InlineKeyboardMarkup([
            [green_button("🔄 تحديث العملاء", callback_data="adm:users")],
            *admin_navigation_rows(),
        ]),
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


async def admin_test_purchase_channel(update, context):
    """Read-only validation for the configured purchase channel and bot posting permission."""
    if not PURCHASE_CHANNEL_CHAT_ID:
        await send_or_edit(
            update,
            "⚠️ لم يتم إعداد `PURCHASE_CHANNEL_CHAT_ID` في Render بعد.",
            InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
        )
        return
    try:
        channel_id = int(PURCHASE_CHANNEL_CHAT_ID)
    except (TypeError, ValueError):
        await send_or_edit(
            update,
            "❌ قيمة `PURCHASE_CHANNEL_CHAT_ID` غير صحيحة. استخدم آيدي قناة بصيغة `-100...`.",
            InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
        )
        return
    try:
        chat = await context.bot.get_chat(channel_id)
        chat_type = str(getattr(chat, "type", "")).lower()
        if chat_type != "channel":
            await send_or_edit(
                update,
                "❌ آيدي المشتريات الحالي يشير إلى **قروب** أو هدف غير قناة.\n\n"
                "ضع آيدي القناة نفسها في `PURCHASE_CHANNEL_CHAT_ID` داخل Render، وليس آيدي مجموعة النقاش.",
                InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
            )
            return
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(channel_id, me.id)
        status_obj = getattr(member, "status", "")
        status = str(getattr(status_obj, "value", status_obj)).lower()
        is_admin = status in {"administrator", "creator", "owner"}
        can_post = status in {"creator", "owner"} or bool(getattr(member, "can_post_messages", False))
        if not is_admin or not can_post:
            await send_or_edit(
                update,
                f"⚠️ تم العثور على القناة: **{getattr(chat, 'title', 'بدون اسم')}**، "
                "لكن البوت ليس مشرفاً بصلاحية نشر الرسائل.\n\n"
                "أضفه مشرفاً وفعّل صلاحية **نشر الرسائل** ثم أعد الاختبار.",
                InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
            )
            return
        await send_or_edit(
            update,
            f"✅ **قناة المشتريات جاهزة**\n\n"
            f"📢 القناة: {getattr(chat, 'title', 'بدون اسم')}\n"
            f"🆔 الآيدي: `{channel_id}`\n"
            "🤖 البوت مشرف ولديه صلاحية نشر الرسائل.\n\n"
            "لن يُنشر أي اختبار الآن؛ ستصل منشورات الشراء للقناة بعد نجاح الطلبات المدفوعة.",
            InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
        )
    except TelegramError as exc:
        log.warning("Purchase channel admin test failed: %s", exc)
        await send_or_edit(
            update,
            "❌ تعذر الوصول إلى قناة المشتريات. تأكد من الآيدي السالب، ومن أن البوت مشرف في القناة.",
            InlineKeyboardMarkup([[red_button("↩️ لوحة المشرف", callback_data="adm:home")]]),
        )


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
        "SELECT btn_key,btn_text,btn_action,COALESCE(is_visible,TRUE),COALESCE(icon_custom_emoji_id,'') FROM custom_buttons ORDER BY btn_key",
        fetchall=True,
    )
    text = (
        "🎛️ **إدارة أزرار الواجهة**\n\n"
        "يمكنك تعديل النص، تغيير الإجراء أو الرابط، وإخفاء الزر أو إظهاره. "
        "الإخفاء قابل للاسترجاع ولا يحذف أي بيانات.\n\n"
    )
    keyboard = []
    for key, label, action, visible, icon_id in rows:
        status = "🟢 ظاهر" if visible else "🔴 مخفي"
        icon_status = "مخصص ✅" if icon_id else "عادي"
        text += f"▪️ `{key}` — {status}\n   النص: {label}\n   الإجراء: {action or 'غير مضبوط'}\n   الأيقونة: {icon_status}\n\n"
        keyboard.append([
            green_button("✏️ تعديل النص", callback_data=f"adm:editbutton:{key}"),
            green_button("🔗 تعديل الإجراء", callback_data=f"adm:editbuttonaction:{key}"),
        ])
        keyboard.append([
            green_button("🎞️ تخصيص الإيموجي", callback_data=f"adm:editbuttonemoji:{key}"),
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

async def credit_user_balance(user_id, amount):
    """Manually credit an existing customer once within a locked database transaction."""
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name,balance FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None
            cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, user_id))
            new_balance = Decimal(str(row[1])) + Decimal(str(amount))
        conn.commit()
        return row[0], new_balance
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)


async def admin_text(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    state = context.user_data.get("admin_state")
    text = update.message.text.strip()

    if state == "debit_user_id":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ أرسل آيدي تيليجرام رقمي صحيح للعميل.")
            return
        row = get_user(int(text))
        if not row:
            await update.message.reply_text("❌ لا يوجد عميل بهذا الآيدي.")
            return
        context.user_data["debit_target_user_id"] = int(text)
        context.user_data["admin_state"] = "debit_user_amount"
        await update.message.reply_text(f"👤 العميل: {row[1]}\n💵 الرصيد الحالي: {money(row[2])}$\n\nأرسل مبلغ الخصم:")
        return

    if state == "debit_user_amount":
        try:
            amount = Decimal(text)
            if amount <= 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ أرسل مبلغًا رقميًا أكبر من صفر.")
            return
        target = context.user_data.get("debit_target_user_id")
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name,balance FROM users WHERE user_id=%s FOR UPDATE", (target,))
                row = cur.fetchone()
                if not row:
                    conn.rollback(); await update.message.reply_text("❌ العميل غير موجود."); return
                balance = Decimal(str(row[1]))
                if balance < amount:
                    conn.rollback(); await update.message.reply_text(f"❌ الرصيد غير كافٍ. الرصيد الحالي: {money(balance)}$"); return
                cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (amount, target))
                new_balance = balance - amount
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            DB_POOL.putconn(conn)
        log_operation(target, "admin_manual_debit", f"amount={money(amount)};new_balance={money(new_balance)}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم خصم {money(amount)}$ من حساب {row[0]}.\n💵 الرصيد الجديد: {money(new_balance)}$")
        try:
            await context.bot.send_message(target, f"➖ **تم تعديل رصيد حسابك من الإدارة**\n\nالمبلغ المخصوم: **{money(amount)}$**\nرصيدك الجديد: **{money(new_balance)}$**", parse_mode=ParseMode.MARKDOWN)
        except TelegramError:
            pass
        await admin_panel(update, context)
        return

    if state == "external_price":
        try:
            sale_price = Decimal(text)
            if sale_price < 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ أرسل سعرًا رقميًا صحيحًا (مثال: 5.00).")
            return
        pid = context.user_data.get("external_provider_id")
        try:
            items = external_services_list()
            item = next((x for x in items if str(provider_value(x, "service", "id", "service_id")) == str(pid)), None)
        except Exception:
            item = None
        if not item:
            await update.message.reply_text("❌ لم أجد الخدمة في قائمة المورد.")
            context.user_data.clear(); return
        name = str(provider_value(item, "name", "title", default="خدمة خارجية"))
        provider_price = Decimal(str(provider_value(item, "price", "rate", default="0") or "0"))
        existing = db_execute("SELECT service_id FROM external_service_map WHERE provider_service_id=%s", (pid,), fetch=True)
        if existing:
            db_execute("UPDATE services SET price=%s,name=%s,active=TRUE WHERE id=%s", (sale_price, name, existing[0]))
            db_execute("UPDATE external_service_map SET provider_price=%s,provider_name=%s,provider_active=TRUE,last_synced_at=CURRENT_TIMESTAMP WHERE service_id=%s", (provider_price, name, existing[0]))
            sid = existing[0]
        else:
            row = db_execute("INSERT INTO services(category_key,name,description,price,delivery_mode,active) VALUES('subscriptions',%s,%s,%s,'external',TRUE) RETURNING id", (name, f"خدمة خارجية من المورد\nService ID: {pid}", sale_price), fetch=True)
            sid = row[0]
            db_execute("INSERT INTO external_service_map(service_id,provider_service_id,provider_price,provider_name) VALUES(%s,%s,%s,%s)", (sid,pid,provider_price,name))
        log_operation(None, "external_service_added", f"service={sid};provider={pid};provider_price={provider_price};sale_price={sale_price}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تمت إضافة الخدمة الخارجية\n\n📌 {name}\n🆔 {pid}\n💰 سعر المورد: ${provider_price}\n💵 سعر B-Fix: ${sale_price}")
        await admin_external_services(update, context, 0)
        return

    if state == "credit_user_id":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ أرسل آيدي تيليجرام رقمي صحيح للعميل.")
            return
        row = get_user(int(text))
        if not row:
            await update.message.reply_text("❌ لا يوجد عميل بهذا الآيدي. يجب أن يبدأ العميل البوت مرة واحدة أولاً.")
            return
        context.user_data["credit_target_user_id"] = int(text)
        context.user_data["admin_state"] = "credit_user_amount"
        await update.message.reply_text(
            f"👤 العميل: {row[1]}\n💵 رصيده الحالي: {money(row[2])}$\n\nأرسل الآن مبلغ الشحن بالأرقام فقط."
        )
        return

    if state == "credit_user_amount":
        try:
            amount = Decimal(text)
            if amount <= 0:
                raise InvalidOperation
        except Exception:
            await update.message.reply_text("❌ أرسل مبلغاً رقمياً أكبر من صفر.")
            return
        target_user_id = context.user_data.get("credit_target_user_id")
        credited = await credit_user_balance(target_user_id, amount)
        if not credited:
            context.user_data.clear()
            await update.message.reply_text("❌ العميل غير موجود أو تعذر الشحن.")
            return
        name, new_balance = credited
        log_operation(target_user_id, "admin_manual_credit", f"amount={money(amount)}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ تم شحن حساب {name} بمبلغ {money(amount)}$.\n💵 الرصيد الجديد: {money(new_balance)}$"
        )
        try:
            await context.bot.send_message(
                target_user_id,
                f"💰 **تم شحن رصيد حسابك من الإدارة**\n\n"
                f"المبلغ المضاف: **{money(amount)}$**\n"
                f"رصيدك الجديد: **{money(new_balance)}$**",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as exc:
            log.warning("Manual credit notification failed for %s: %s", target_user_id, exc)
        return

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

    if state == "service_edit_emoji":
        sid = context.user_data.get("service_edit_id")
        if not sid:
            context.user_data.clear()
            await update.message.reply_text("⚠️ انتهت جلسة التعديل. افتح الخدمة من جديد.")
            return
        if text == "-":
            entity_json = ""
        else:
            entity_json = custom_emoji_entities_from_message(update.message)
            if not entity_json:
                await update.message.reply_text("❌ أرسل رسالة تحتوي Custom Emoji متحركاً، أو أرسل - لمسحه.")
                return
        db_execute("UPDATE services SET name_entities_json=%s WHERE id=%s", (entity_json, sid))
        log_operation(None, "service_custom_emoji_updated", f"service={sid};cleared={not bool(entity_json)}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text("✅ تم تحديث الإيموجي المتحرك للخدمة.")
        return

    if state == "service_edit_field":
        sid = context.user_data.get("service_edit_id")
        field = context.user_data.get("service_edit_field")
        columns = {
            "name": "name",
            "description": "description",
            "duration": "subscription_duration",
            "activation": "activation_time",
            "pre_message": "pre_purchase_message",
            "content_text": "service_content_text",
            "content_link": "service_content_link",
            "request_prompt": "customer_request_prompt",
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
            if text == "-" and field in {"pre_message", "content_text", "content_link", "request_prompt"}:
                value = ""
            elif not text or len(text) > (120 if field == "name" else 3500):
                await update.message.reply_text("❌ القيمة فارغة أو أطول من الحد المسموح.")
                return
            else:
                value = text
            if field == "name":
                entity_json = custom_emoji_entities_from_message(update.message)
            if field == "content_link" and value and not (value.startswith("https://") or value.startswith("http://") or value.startswith("tg://")):
                await update.message.reply_text("❌ أرسل رابطاً يبدأ بـ https:// أو http:// أو tg://، أو أرسل - لمسحه.")
                return
            column = columns.get(field)
        if not sid or not column:
            context.user_data.clear()
            await update.message.reply_text("⚠️ انتهت جلسة التعديل. افتح الخدمة من جديد.")
            return
        db_execute(f"UPDATE services SET {column}=%s WHERE id=%s", (value, sid))
        if field == "name":
            db_execute("UPDATE services SET name_entities_json=%s WHERE id=%s", (entity_json, sid))
        log_operation(None, "service_detail_updated", f"service={sid};field={column}", ADMIN_ID)
        context.user_data.clear()
        await update.message.reply_text("✅ تم حفظ التعديل بنجاح.")
        return

    if state == "service_name":
        raw_name = (update.message.text or "").strip()
        if not raw_name or len(raw_name) > 120:
            await update.message.reply_text("❌ أرسل اسماً صحيحاً لا يزيد عن 120 حرفاً.")
            return
        context.user_data["service_name"] = raw_name
        context.user_data["service_name_entities_json"] = custom_emoji_entities_from_message(update.message)
        if context.user_data.get("service_category") == "free":
            context.user_data["admin_state"] = "free_offer_description"
            await update.message.reply_text("📝 أرسل وصفاً مختصراً للعرض المجاني:")
        else:
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

    if state == "free_offer_description":
        context.user_data["service_description"] = text[:1800]
        context.user_data["admin_state"] = "free_offer_text"
        await update.message.reply_text("📝 أرسل النص الذي سيصل للعميل مع العرض المجاني:")
        return

    if state == "free_offer_text":
        context.user_data["free_content_text"] = "" if text.strip() == "-" else text[:3500]
        context.user_data["admin_state"] = "free_offer_link"
        await update.message.reply_text("🔗 أرسل رابط العرض أو الموقع، أو أرسل علامة: - لتخطيه.")
        return

    if state == "free_offer_link":
        link = "" if text.strip() == "-" else text.strip()
        if link and not (link.startswith("https://") or link.startswith("http://") or link.startswith("tg://")):
            await update.message.reply_text("❌ أرسل رابطاً يبدأ بـ https:// أو http://، أو أرسل - إذا لا يوجد رابط.")
            return
        context.user_data["free_content_link"] = link
        context.user_data["admin_state"] = "free_offer_photo"
        await update.message.reply_text("🖼️ أرسل صورة العرض، أو أرسل علامة: - لتخطي الصورة.", parse_mode=ParseMode.MARKDOWN)
        return

    if state == "free_offer_photo":
        if text.strip() != "-":
            await update.message.reply_text("❌ أرسل الصورة كصورة تيليجرام، أو أرسل علامة: - لتخطيها.")
            return
        context.user_data["free_photo_file_id"] = ""
        context.user_data["admin_state"] = "free_offer_document"
        await update.message.reply_text("📎 أرسل ملف العرض، أو أرسل علامة: - لتخطيه وإنشاء العرض.")
        return

    if state == "free_offer_document":
        if text.strip() != "-":
            await update.message.reply_text("❌ أرسل الملف كمستند تيليجرام، أو أرسل علامة: - لتخطيه وإنشاء العرض.")
            return
        await finish_free_offer_creation(update, context)
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
        d["service_options"] = sorted(options)
        if d.get("service_category") in {"subscriptions", "vip"}:
            d["admin_state"] = "service_request_prompt"
            label = "الاشتراك" if d.get("service_category") == "subscriptions" else "خدمة VIP"
            await update.message.reply_text(
                f"🧾 أرسل الرسالة التي تطلب بها تفاصيل العميل لتنفيذ {label}، أو أرسل - لتخطيها.\n\n"
                "مثال: أرسل الإيميل المسجل، رقم الهاتف، أو اكتب تفاصيل طلبك."
            )
            return
        d["admin_state"] = "service_pre_message"
        await update.message.reply_text("💬 أرسل ملاحظة تظهر للعميل قبل الشراء، أو أرسل - لتخطيها.")
        return

    if state == "service_request_prompt":
        prompt = "" if text.strip() == "-" else text.strip()[:1800]
        if text.strip() != "-" and not prompt:
            await update.message.reply_text("❌ أرسل رسالة واضحة أو أرسل - لتخطيها.")
            return
        context.user_data["service_request_prompt"] = prompt
        context.user_data["admin_state"] = "service_pre_message"
        await update.message.reply_text("💬 أرسل ملاحظة تظهر للعميل قبل الشراء، أو أرسل - لتخطيها.")
        return

    if state == "service_pre_message":
        context.user_data["service_pre_message"] = "" if text == "-" else text[:1800]
        context.user_data["admin_state"] = "service_content_text"
        await update.message.reply_text("📝 أرسل النص الذي يُسلّم للعميل بعد نجاح الشراء، أو أرسل - لتخطيه.")
        return

    if state == "service_content_text":
        context.user_data["service_content_text"] = "" if text == "-" else text[:3500]
        context.user_data["admin_state"] = "service_content_link"
        await update.message.reply_text("🔗 أرسل الرابط الذي يُسلّم للعميل بعد نجاح الشراء، أو أرسل - لتخطيه.")
        return

    if state == "service_content_link":
        link = "" if text == "-" else text.strip()
        if link and not (link.startswith("https://") or link.startswith("http://") or link.startswith("tg://")):
            await update.message.reply_text("❌ أرسل رابطاً يبدأ بـ https:// أو http:// أو tg://، أو أرسل - لتخطيه.")
            return
        context.user_data["service_content_link"] = link
        context.user_data["admin_state"] = "service_add_photo"
        await update.message.reply_text("🖼️ أرسل صورة تُسلّم للعميل بعد نجاح الشراء، أو أرسل - لتخطيها.")
        return

    if state == "service_add_photo":
        if text != "-":
            await update.message.reply_text("❌ أرسل الصورة كصورة تيليجرام، أو أرسل - لتخطيها.")
            return
        context.user_data["service_add_photo_file_id"] = ""
        context.user_data["admin_state"] = "service_add_document"
        await update.message.reply_text("📄 أرسل ملفاً يُسلّم للعميل بعد نجاح الشراء، أو أرسل - لإنشاء الخدمة.")
        return

    if state == "service_add_document":
        if text != "-":
            await update.message.reply_text("❌ أرسل الملف كمستند تيليجرام، أو أرسل - لإنشاء الخدمة.")
            return
        await finish_regular_service_creation(
            update, context, context.user_data.get("service_add_photo_file_id", ""), ""
        )
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

    if state == "button_emoji":
        key = context.user_data["edit_button_key"]
        if text == "-":
            icon_id = ""
        else:
            entities = custom_emoji_entities_from_message(update.message)
            if not entities:
                await update.message.reply_text("❌ أرسل Custom Emoji متحركاً من لوحة Telegram، أو أرسل - لمسحه.")
                return
            try:
                parsed = json.loads(entities)
                icon_id = str(parsed[0]["custom_emoji_id"])
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                await update.message.reply_text("❌ تعذر قراءة Custom Emoji. أرسله وحده مرة أخرى.")
                return
        db_execute("UPDATE custom_buttons SET icon_custom_emoji_id=%s WHERE btn_key=%s", (icon_id, key))
        log_operation(None, "button_custom_emoji_updated", f"button={key};cleared={not bool(icon_id)}", ADMIN_ID)
        await update.message.reply_text("✅ تم تحديث إيموجي الزر الرئيسي.")
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
            pattern=r"^(check_sub|main|flowcancel:|free_offer_timer:|cat:|service:|buyconfirm:|buy:|profile|orders|order:|fund|pay:|receipt_start:|support|about|admin|adm:|approve:|reject:|deliver:|done:|cancelorder:)",
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
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))

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
