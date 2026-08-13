# ==============================================================================
# |   B-Fix Smart Bot - النسخة المحدثة النهائية (قسم شحن الأدوات والبوكسات)    |
# ==============================================================================

import os
import sqlite3
import logging
import asyncio
import warnings
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, MessageHandler, filters
)
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", category=PTBUserWarning)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"B-Fix Bot is ALIVE and RUNNING 24/7!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ================= (1) الإعدادات =================
BOT_TOKEN = "8299192931:AAHkXI_BLyoAp8TvrSCU9i_CnoDSyDFbTGA"  # ⬅️ ضع التوكن هنا
ADMIN_ID = 8218627841  # ⬅️ ضع الآيدي الخاص بك كرقْم

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
DB_NAME = "bfix_store.db"

COLOR_PRIMARY = "🔵"
COLOR_SUCCESS = "🟢"
COLOR_DANGER = "🔴"
COLOR_WARNING = "⚠️"
COLOR_INFO = "ℹ️"
COLOR_ACTION = "✨"

(ADMIN_USER_ID, ADMIN_AMOUNT, ADMIN_SEARCH, ADMIN_BROADCAST, ADMIN_SRV_CATEGORY,
 ADMIN_SRV_NAME, ADMIN_SRV_DESC, ADMIN_SRV_PRICE, ADMIN_SRV_DURATION, 
 ADMIN_NEW_PRICE, ADMIN_CARD_CODE, ADMIN_CARD_AMOUNT, ADMIN_STOCK_KEY,
 ADMIN_MAINTENANCE_TEXT, WAITING_USER_EMAIL, WAITING_RENTAL_CREDENTIALS) = range(16)

# ================= (2) قاعدة البيانات =================
def db_execute(query, params=()):
    with sqlite3.connect(DB_NAME, timeout=30) as conn:
        conn.execute(query, params)
        conn.commit()

def db_fetch_one(query, params=()):
    with sqlite3.connect(DB_NAME, timeout=30) as conn:
        return conn.execute(query, params).fetchone()

def db_fetch_all(query, params=()):
    with sqlite3.connect(DB_NAME, timeout=30) as conn:
        return conn.execute(query, params).fetchall()

def init_db():
    with sqlite3.connect(DB_NAME, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, balance REAL DEFAULT 0.0, join_date TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, price REAL, duration TEXT, category TEXT DEFAULT 'digital', quantity INTEGER DEFAULT 0, file_id TEXT)''')
        try: conn.execute("ALTER TABLE services ADD COLUMN file_id TEXT")
        except: pass
        conn.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, service_id INTEGER, status TEXT, order_date TEXT, custom_data TEXT)''')
        try: conn.execute("ALTER TABLE orders ADD COLUMN custom_data TEXT")
        except: pass
        conn.execute('''CREATE TABLE IF NOT EXISTS cards (code TEXT PRIMARY KEY, amount REAL, is_used INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS product_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER, key_text TEXT, is_sold INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_mode (id INTEGER PRIMARY KEY AUTOINCREMENT, is_active INTEGER DEFAULT 0, custom_message TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS last_broadcast (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)''')
        
        chk_main = conn.execute("SELECT id FROM maintenance_mode WHERE id = 1").fetchone()
        if not chk_main:
            conn.execute("INSERT INTO maintenance_mode (id, is_active) VALUES (1, 0)")
        conn.commit()
    print("\n✅ تم الاتصال بقاعدة البيانات بنجاح!")

def add_user_if_not_exists(user_id, name):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, balance, join_date) VALUES (?, ?, ?, ?)", 
                   (user_id, name, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def is_bot_under_maintenance():
    status = db_fetch_one("SELECT is_active FROM maintenance_mode WHERE id = 1")
    return status[0] == 1 if status else False

def get_maintenance_message():
    msg = db_fetch_one("SELECT custom_message FROM maintenance_mode WHERE id = 1")
    return msg[0] if msg and msg[0] else None

async def check_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin=False):
    if is_bot_under_maintenance() and not is_admin:
        custom_msg = get_maintenance_message()
        final_text = f"⚙️ **{custom_msg}** 🛠️" if custom_msg else "⚠️ **نعتذر منكم، البوت قيد الصيانة حالياً.** 🛠️\n\nيرجى المحاولة لاحقاً."
        if update.message: await update.message.reply_text(final_text, parse_mode='Markdown')
        elif update.callback_query: await update.callback_query.answer("⚙️ البوت تحت الصيانة", show_alert=True)
        return False
    return True

# ================= (3) واجهة العميل والأقسام =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_maintenance(update, context): return
    user = update.effective_user
    add_user_if_not_exists(user.id, user.first_name)
    
    text = (
        "✨ ━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━ ✨\n\n"
        f"👋 أهلاً بك يا [{user.first_name}](tg://user?id={user.id})\n"
        "في متجرك الآلي للخدمات والاشتراكات الرقمية 🚀\n\n"
        "🛒 ❲ اختر القسم المطلوب ❳ 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = [
        [InlineKeyboardButton("⚡ شحن الأدوات والبوكسات 🛠️", callback_data="show_cat_digital"),
         InlineKeyboardButton("🔵 الاشتراكات 🚀", callback_data="show_cat_subscriptions")],
        [InlineKeyboardButton("🔧 خدمة إيجار الأدوات 🛠️", callback_data="show_cat_rentals")],
        [InlineKeyboardButton("💎 عروض VIP الماسي ⭐", callback_data="show_cat_vip"),
         InlineKeyboardButton("🎁 عروض مجانية حصرية 🆓", callback_data="show_cat_free")],
        [InlineKeyboardButton("ℹ️ سجل طلباتي 🔄", callback_data="my_orders"),
         InlineKeyboardButton("⚡ حسابي ⚡", callback_data="my_profile")],
        [InlineKeyboardButton("🔵 شحن بكود", callback_data="charge_account"),
         InlineKeyboardButton("🔵 تغذية حسابك", callback_data="fund_account")],
        [InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK),
         InlineKeyboardButton("🛠️ الدعم", url=SUPPORT_LINK)],
        [InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='Markdown')

async def main_buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_maintenance(update, context): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "my_profile":
        user_info = db_fetch_one("SELECT name, balance FROM users WHERE user_id = ?", (user_id,))
        text = f"👤 **ملفك الشخصي:**\n\n▪️ **الاسم:** {user_info[0]}\n▪️ **الآيدي:** `{user_id}`\n▪️ **الرصيد:** `{user_info[1]}` $"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 تغذية حسابك", callback_data="fund_account")], [InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "charge_account":
        await query.message.edit_text("💳 أرسل **كود البطاقة** الآن (أو أرسل /cancel للإلغاء):", parse_mode='Markdown')
        context.user_data['waiting_card'] = True

    elif data == "fund_account":
        payment_text = (
            "💎 **━━━━━ ❲ بوابات الدفع الإلكتروني المعتمدة ❳ ━━━━━** 💎\n\n"
            "🌟 أهلاً بك عزيزي العميل. يرجى اختيار وسيلة الدفع المناسبة لك من القائمة أدناه لعرض تفاصيل الحساب المخصص بدقة 👇\n\n"
            "🔒 **نظام آلي فوري وموثوق 100%**"
        )
        payment_keyboard = [
            [InlineKeyboardButton("🔹 محفظة جيب", callback_data="pay_jeep"),
             InlineKeyboardButton("🔹 جوالي", callback_data="pay_jawali")],
            [InlineKeyboardButton("🔹 وان كاش", callback_data="pay_onecash"),
             InlineKeyboardButton("🏦 بنك الكريمي", callback_data="pay_kuraimi")],
            [InlineKeyboardButton("🟡 Binance ID", callback_data="pay_binance"),
             InlineKeyboardButton("💳 VISA Card", callback_data="pay_visa")],
            [InlineKeyboardButton("🟢 شحن عبر كود بطاقة", callback_data="charge_account")],
            [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية 🔄", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_text, reply_markup=InlineKeyboardMarkup(payment_keyboard), parse_mode='Markdown')

    elif data == "pay_jeep":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – محفظة جيب ❳ ━━━━━** 💎\n\n"
            "📱 **رقم الحساب المعتمد:**\n"
            "`580300`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل وإتمام الطلب:**\n"
            "1️⃣ قم بالتحويل إلى الرقم الموضح أعلاه بالمبلغ المطلوبة.\n"
            "2️⃣ خذ لقطة شاشة (إشعار) التحويل.\n"
            "3️⃣ تواصل معنا عبر زر الدعم أو واتساب وأرسل الإشعار مع اسمك لتتم تعبئة رصيدك فوراً.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "pay_jawali":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – محفظة جوالي ❳ ━━━━━** 💎\n\n"
            "📱 **رقم الحساب المعتمد:**\n"
            "`777728478`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل وإتمام الطلب:**\n"
            "1️⃣ قم بالتحويل إلى الرقم الموضح أعلاه بالمبلغ المطلوب.\n"
            "2️⃣ أرسل إشعار التحويل عبر واتساب مع ذكر اسمك ورصيدك المطلوب.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "pay_onecash":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – محفظة وان كاش ❳ ━━━━━** 💎\n\n"
            "📱 **رقم الحساب المعتمد:**\n"
            "`178109713`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل وإتمام الطلب:**\n"
            "1️⃣ قم بالتحويل إلى الرقم الموضح أعلاه بالمبلغ المطلوب.\n"
            "2️⃣ أرسل الإشعار عبر واتساب لتتم معالجة طلبك وشحن رصيدك فوراً.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "pay_kuraimi":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – بنك الكريمي ❳ ━━━━━** 💎\n\n"
            "🏦 **حسابات بنك الكريمي المعتمدة:**\n\n"
            "🇾🇪 **ريال يمني:** `3204168937`\n"
            "🇸🇦 **ريال سعودي:** `3204433991`\n"
            "💵 **دولار أمريكي:** `3191718649`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل:**\n"
            "قم بالتحويل بالعملة المناسبة وأرسل سند التحويل عبر واتساب لتفعيل رصيدك.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "pay_binance":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – Binance Pay ❳ ━━━━━** 💎\n\n"
            "🟡 **Binance ID المعتمد:**\n"
            "`1063050653`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل:**\n"
            "قم بإتمام التحويل عبر باينانس وأرسل (Order ID) مع إشعار التحويل عبر واتساب لتحديث رصيدك فوراً.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "pay_visa":
        msg = (
            "💎 **━━━━━ ❲ تفاصيل الدفع – بطاقة VISA ❳ ━━━━━** 💎\n\n"
            "💳 **رقم البطاقة / الحساب المعتمد:**\n"
            "`4909800019663092`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ **تعليمات التحويل:**\n"
            "قم بإتمام العملية وأرسل التفاصيل وسند الدفع عبر واتساب ليتم اعتماد رصيدك.\n\n"
            "🔒 **معاملة آمنة ومحمية بالكامل**"
        )
        await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")], [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        if category == "digital": title = "⚡ خدمات شحن الأدوات والبوكسات الفخمة 🛠️"
        elif category == "subscriptions": title = "🔵 الاشتراكات الرقمية 🚀"
        elif category == "rentals": title = "🔧 خدمة إيجار الأدوات 🛠️"
        elif category == "vip": title = "💎 عروض VIP الماسي ⭐"
        elif category == "free": title = "🎁 العروض المجانية الحصرية 🆓"
        else: title = "🛒 قائمة الخدمات"
        
        services = db_fetch_all("SELECT id, name, price FROM services WHERE category = ?", (category,))
        if not services:
            await query.message.edit_text(f"🚧 لا توجد خدمات في قسم {title} حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))
            return
            
        keyboard = []
        for srv in services:
            stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv[0],))[0]
            status = "🟢 تتوفر" if (stock > 0 or category == "free" or category == "rentals") else "🔴 نفدت"
            keyboard.append([InlineKeyboardButton(f"▪️ {srv[1]} - {srv[2]}$ ({status})", callback_data=f"srv_{srv[0]}")])
        keyboard.append([InlineKeyboardButton("🔴 رجوع للقائمة الرئيسية", callback_data="main_menu")])
        await query.message.edit_text(f"📑 **{title}:**\n\n👇 اختر الخدمة المطلوبة:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("srv_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT id, name, description, price, duration, category FROM services WHERE id = ?", (srv_id,))
        stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
        text = f"📌 **الخدمة/الأداة:** {srv[1]}\n📝 **الوصف:** {srv[2]}\n⏳ **المدة:** {srv[4]}\n💵 **السعر:** `{srv[3]}` $\n📦 **الكمية المتوفرة:** {stock}"
        
        keyboard = [[InlineKeyboardButton("🟢 شراء / طلب الآن ⚡", callback_data=f"buy_{srv[0]}")], [InlineKeyboardButton("🔴 رجوع للقسم", callback_data=f"show_cat_{srv[5]}")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("buy_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT price, name, category, file_id FROM services WHERE id = ?", (srv_id,))
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user_id,))
        
        cat = srv[2]
        price = srv[0]
        
        if cat == "free" or price == 0:
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅ مجاني', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await query.message.edit_text("🎁 **إليك طلبك المجاني الفوري:**", parse_mode='Markdown')
            
            if srv[3]:
                try:
                    await context.bot.send_document(chat_id=user_id, document=srv[3], caption=f"🎁 طلبك المجاني: {srv[1]}")
                except:
                    try:
                        await context.bot.send_photo(chat_id=user_id, photo=srv[3], caption=f"🎁 طلبك المجاني: {srv[1]}")
                    except:
                        await context.bot.send_message(chat_id=user_id, text=f"🎁 تفاصيل طلبك المجاني:\n{srv[3]}")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"✅ تم تسليم العرض المجاني بنجاح لـ {srv[1]}")
            return

        if user_info[0] < price:
            await query.answer("❌ رصيدك غير كافٍ لإتمام عملية الشراء!", show_alert=True)
            return

        if cat == "rentals":
            context.user_data['pending_rental_srv_id'] = srv_id
            context.user_data['pending_rental_price'] = price
            context.user_data['pending_rental_name'] = srv[1]
            
            await query.message.edit_text(
                "⏳ **طلب إيجار قيد التجهيز**\n\n"
                "يرجى الانتظار لمدة (5 إلى 10 دقائق) ريثما نقوم بتجهيز اليوزر والباسورد الخاص بأداة الإيجار وإرساله لك هنا عبر البوت.\n\n"
                "✍️ أرسل لنا الآن أي ملاحظة أو اضغط /cancel للإلغاء:",
                parse_mode='Markdown'
            )
            context.user_data['waiting_rental_note'] = True
            return

        if cat in ["digital", "subscriptions", "vip"]:
            context.user_data['pending_srv_id'] = srv_id
            context.user_data['pending_price'] = price
            context.user_data['pending_name'] = srv[1]
            
            await query.message.edit_text(
                "📧 **تفعيل الشحن والاشتراك**\n\n"
                "يرجى إرسال **الإيميل الخاص بك** المسجل في موقع الأداة/البوكس لكي نقوم بالشحن وتفعيل الاشتراك عليه فوراً:\n"
                "(أو أرسل /cancel للإلغاء)",
                parse_mode='Markdown'
            )
            context.user_data['waiting_email_input'] = True
            return

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        if not orders: text = "📦 لا توجد طلبات سابقة."
        else: text = "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        await query.message.edit_text("🤖 متجر B-Fix الذكي لشحن الأدوات والبوكسات والاشتراكات الرقمية.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))

    elif data == "main_menu":
        await start_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if context.user_data.get('waiting_email_input'):
        email = update.message.text.strip()
        srv_id = context.user_data.get('pending_srv_id')
        price = context.user_data.get('pending_price')
        srv_name = context.user_data.get('pending_name')
        
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ. تم إلغاء الطلب.")
            context.user_data.clear()
            return
            
        new_balance = user_info[0] - price
        db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user.id))
        db_execute("INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد التفعيل الشحن ⏳', ?, ?)", 
                   (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Email: {email}"))
        
        context.user_data.clear()
        await update.message.reply_text(
            "✅ **تم إرسال طلب الشحن بنجاح!**\n\n"
            f"📧 الإيميل المرسل: `{email}`\n"
            "⏳ جاري تنفيذ الشحن والتفعيل على حسابك. سنرسل لك إشعاراً فور اكتمال الطلب!",
            parse_mode='Markdown'
        )
        
        admin_alert = (
            f"🔔 **طلب شحن أداة/بوكس جديد يتطلب تنفيذ!**\n\n"
            f"👤 العميل: {user_info[1]}\n"
            f"🆔 الآيدي: `{user.id}`\n"
            f"🛒 الأداة/الخدمة: {srv_name}\n"
            f"📧 الإيميل المطلوب شحنه وتفعيله:\n`{email}`"
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_alert, parse_mode='Markdown')
        return

    if context.user_data.get('waiting_rental_note'):
        note = update.message.text.strip()
        srv_id = context.user_data.get('pending_rental_srv_id')
        price = context.user_data.get('pending_rental_price')
        srv_name = context.user_data.get('pending_rental_name')
        
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ للإيجار. تم إلغاء الطلب.")
            context.user_data.clear()
            return
            
        new_balance = user_info[0] - price
        db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user.id))
        
        db_execute_and_get_cursor(
            "INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد تجهيز الإيجار ⏳', ?, ?)", 
            (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Note: {note}")
        )
        
        context.user_data.clear()
        await update.message.reply_text(
            "⏳ **تم استلام طلب الإيجار بنجاح!**\n\n"
            "يرجى الانتظار من 5 إلى 10 دقائق ريثما يتم تجهيز بيانات الحساب (Username & Password) وإرسالها لك هنا.",
            parse_mode='Markdown'
        )
        
        admin_alert = (
            f"🔧 **طلب إيجار أداة جديد!**\n\n"
            f"👤 العميل: {user_info[1]}\n"
            f"🆔 الآيدي: `{user.id}`\n"
            f"🛠️ الأداة: {srv_name}\n"
            f"📝 ملاحظة العميل: {note}\n\n"
            "👇 أرسل يوزر وباسورد الإيجار لهذا العميل من خلال لوحة التحكم أو بالرد على رسالته."
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_alert, parse_mode='Markdown')
        return

    if user.id != ADMIN_ID:
        if not await check_maintenance(update, context): return
        
    if context.user_data.get('waiting_card'):
        code = update.message.text.strip()
        card = db_fetch_one("SELECT amount, is_used FROM cards WHERE code = ?", (code,))
        if not card or card[1] == 1:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم مسبقاً. أرسل كوداً آخر أو /cancel للإلغاء.")
            return
            
        db_execute("UPDATE cards SET is_used = 1 WHERE code = ?", (code,))
        db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (card[0], user.id))
        new_balance = db_fetch_one("SELECT balance FROM users WHERE user_id = ?", (user.id,))[0]
        
        context.user_data['waiting_card'] = False
        await update.message.reply_text(f"✅ **تم الشحن بنجاح!**\n💰 القيمة: `{card[0]}` $\n💵 رصيدك الجديد: `{new_balance}` $", parse_mode='Markdown')
        return

def db_execute_and_get_cursor(query, params=()):
    with sqlite3.connect(DB_NAME, timeout=30) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

# ================= (4) لوحة تحكم المشرف =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return 
    m_status = "🟢 (مفعل)" if is_bot_under_maintenance() else "🔴 (معطل)"
    text = "👑 **لوحة تحكم المطور والمدير المتقدمة**"
    
    keyboard = [
        [InlineKeyboardButton("🟢 إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("🔴 خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎟 كود شحن", callback_data="adm_new_card"), InlineKeyboardButton("🔎 بحث عن مستخدم", callback_data="adm_search")],
        [InlineKeyboardButton("🛠️ إدارة الخدمات والأقسام والأكواد", callback_data="adm_srv_menu")],
        [InlineKeyboardButton("📢 إشعار جماعي", callback_data="adm_broadcast"), InlineKeyboardButton("🗑️ حذف آخر إشعار", callback_data="adm_del_broadcast")],
        [InlineKeyboardButton(f"⚙️ صيانة البوت: {m_status}", callback_data="adm_toggle_main")],
        [InlineKeyboardButton("📊 إحصائيات المتجر", callback_data="adm_stats")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='Markdown')

async def admin_menus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return
    await query.answer()
    data = query.data

    if data == "adm_main":
        await admin_panel(update, context)
    elif data == "adm_toggle_main":
        current = is_bot_under_maintenance()
        if current:
            db_execute("UPDATE maintenance_mode SET is_active = 0 WHERE id = 1", ())
            await admin_panel(update, context)
        else:
            db_execute("UPDATE maintenance_mode SET is_active = 1 WHERE id = 1", ())
            await admin_panel(update, context)
    elif data == "adm_stats":
        u_count = db_fetch_one("SELECT COUNT(*) FROM users")[0]
        o_count = db_fetch_one("SELECT COUNT(*) FROM orders")[0]
        text = f"📊 **إحصائيات المتجر:**\n👥 المستخدمين: `{u_count}`\n📦 الطلبات: `{o_count}`"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")]]), parse_mode='Markdown')
    elif data == "adm_srv_menu":
        keyboard = [
            [InlineKeyboardButton("🟢 إضافة خدمة أو أداة جديدة (لكافة الأقسام)", callback_data="adm_add_srv")],
            [InlineKeyboardButton("🔵 إضافة أكواد مخزون", callback_data="adm_stock_list")],
            [InlineKeyboardButton("🔴 حذف خدمة", callback_data="adm_del_srv_list")],
            [InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")]
        ]
        await query.message.edit_text("📦 **إدارة الخدمات والأقسام:**", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "adm_del_srv_list":
        services = db_fetch_all("SELECT id, name, category FROM services")
        if not services:
            await query.message.edit_text("لا توجد خدمات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))
            return
        keyboard = [[InlineKeyboardButton(f"🗑️ حذف: {s[1]} ({s[2]})", callback_data=f"delsrv_{s[0]}")] for s in services]
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")])
        await query.message.edit_text("اختر الخدمة لحذفها:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("delsrv_"):
        srv_id = int(data.split("_")[1])
        db_execute("DELETE FROM services WHERE id = ?", (srv_id,))
        await query.message.edit_text("✅ تم الحذف بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))

# ================= (5) معالجات المحادثة الإدارية =================
async def admin_conv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data

    if data in ["adm_add_bal", "adm_sub_bal"]:
        context.user_data['action'] = data 
        await query.message.edit_text("✍️ أرسل آيدي (ID) المستخدم:\n(أرسل /cancel للإلغاء)")
        return ADMIN_USER_ID
    elif data == "adm_search":
        await query.message.edit_text("🔍 أرسل آيدي (ID) المستخدم للبحث:")
        return ADMIN_SEARCH
    elif data == "adm_broadcast":
        await query.message.edit_text("📢 أرسل نص الإشعار الجماعي:")
        return ADMIN_BROADCAST
    elif data == "adm_add_srv":
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ خدمات شحن الأدوات والبوكسات", callback_data="cat_digital")],
            [InlineKeyboardButton("🔵 الاشتراكات الرقمية", callback_data="cat_subscriptions")],
            [InlineKeyboardButton("🔧 خدمة إيجار الأدوات", callback_data="cat_rentals")],
            [InlineKeyboardButton("💎 عروض VIP الماسي ⭐", callback_data="cat_vip")],
            [InlineKeyboardButton("🎁 العروض المجانية (ملفات/صور/فيديو)", callback_data="cat_free")],
            [InlineKeyboardButton("🔴 إلغاء", callback_data="cat_cancel")]
        ])
        await query.message.edit_text("➕ **اختر القسم المخصص لإضافة الخدمة أو العرض:**", reply_markup=markup)
        return ADMIN_SRV_CATEGORY
    elif data == "adm_new_card":
        await query.message.edit_text("🎟 أرسل كود البطاقة الجديد:")
        return ADMIN_CARD_CODE

async def adm_rx_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cat_cancel":
        await query.message.edit_text("🚫 تم الإلغاء.")
        return ConversationHandler.END
        
    context.user_data['s_cat'] = query.data.replace("cat_", "")
    await query.message.edit_text("📝 أرسل الآن **اسم** الخدمة أو العرض:")
    return ADMIN_SRV_NAME

async def adm_rx_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['target'] = int(update.message.text)
    await update.message.reply_text("✍️ أرسل المبلغ:")
    return ADMIN_AMOUNT

async def adm_rx_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amt = float(update.message.text)
    if context.user_data['action'] == "adm_sub_bal": amt = -amt
    db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, context.user_data['target']))
    await update.message.reply_text("✅ تم تحديث الرصيد بنجاح.")
    return ConversationHandler.END

async def adm_rx_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db_fetch_one("SELECT name, balance, join_date FROM users WHERE user_id = ?", (int(update.message.text),))
    if user: await update.message.reply_text(f"👤 الاسم: {user[0]}\n💰 الرصيد: {user[1]}$\n📅 الانضمام: {user[2]}")
    else: await update.message.reply_text("❌ غير موجود.")
    return ConversationHandler.END

async def adm_rx_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    for u in db_fetch_all("SELECT user_id FROM users"):
        try: await context.bot.send_message(chat_id=u[0], text=f"📢 {content}")
        except: pass
    await update.message.reply_text("✅ تم إرسال الإشعار.")
    return ConversationHandler.END

async def adm_rx_srvname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_name'] = update.message.text
    await update.message.reply_text("📝 أرسل وصفاً مختصراً للخدمة أو العرض:")
    return ADMIN_SRV_DESC

async def adm_rx_srvdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_desc'] = update.message.text
    if context.user_data.get('s_cat') == "free":
        await update.message.reply_text("🎁 **قسم العروض المجانية:**\n\nأرسل الآن **الملف أو الصورة أو الفيديو أو رابط/نص العرض المجاني** ليتم إرساله للعميل فوراً عند طلبه:")
        return ADMIN_SRV_DURATION
    else:
        await update.message.reply_text("💵 أرسل السعر بالأرقام:")
        return ADMIN_SRV_PRICE

async def adm_rx_srvprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_price'] = float(update.message.text)
    await update.message.reply_text("⏳ أرسل المدة (مثال: فوري، شهر، يوم):")
    return ADMIN_SRV_DURATION

async def adm_rx_srvdur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = context.user_data.get('s_cat')
    if cat == "free":
        file_id_or_text = ""
        if update.message.document: file_id_or_text = update.message.document.file_id
        elif update.message.photo: file_id_or_text = update.message.photo[-1].file_id
        elif update.message.video: file_id_or_text = update.message.video.file_id
        else: file_id_or_text = update.message.text
        
        db_execute("INSERT INTO services (name, description, price, duration, category, quantity, file_id) VALUES (?, ?, 0, 'فوري 🆓', 'free', 999, ?)", 
                   (context.user_data['s_name'], context.user_data['s_desc'], file_id_or_text))
        await update.message.reply_text("✅ تمت إضافة العرض المجاني مع مرفقاته بنجاح!")
        return ConversationHandler.END
    else:
        duration = update.message.text
        db_execute("INSERT INTO services (name, description, price, duration, category, quantity) VALUES (?, ?, ?, ?, ?, 0)", 
                   (context.user_data['s_name'], context.user_data['s_desc'], context.user_data['s_price'], duration, cat))
        await update.message.reply_text("✅ تمت إضافة الخدمة بنجاح!")
        return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message: await update.message.reply_text("🚫 تم الإلغاء.")
    return ConversationHandler.END

def main():
    init_db()
    try: urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook").read()
    except: pass
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_conv_start, pattern="^(adm_add_bal|adm_sub_bal|adm_search|adm_broadcast|adm_add_srv|adm_new_card)$")],
        states={
            ADMIN_SRV_CATEGORY: [CallbackQueryHandler(adm_rx_category, pattern="^cat_")],
            ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_userid)],
            ADMIN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_amount)],
            ADMIN_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_search)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_broadcast)],
            ADMIN_SRV_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvname)],
            ADMIN_SRV_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvdesc)],
            ADMIN_SRV_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvprice)],
            ADMIN_SRV_DURATION: [MessageHandler(filters.ALL & ~filters.COMMAND, adm_rx_srvdur)],
            ADMIN_CARD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("💵 أرسل القيمة:"))],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True
    ))
    
    app.add_handler(CallbackQueryHandler(admin_menus_handler, pattern="^(adm_|delsrv_)"))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))

    print("\n✅ البوت يعمل بكامل الخصائص والميزات الجديدة بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main()
    except KeyboardInterrupt:
        print("\nتم إيقاف البوت.")
