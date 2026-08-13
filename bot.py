# ==============================================================================
# |   B-Fix Smart Bot - النسخة السحابية المحسنة والسريعة (Neon + Fast Updates) |
# ==============================================================================

import os
import logging
import asyncio
import warnings
import threading
import urllib.request
import psycopg2
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, MessageHandler, filters
)
from telegram.warnings import PTBUserWarning

load_dotenv()

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
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
CHANNEL_LINK = "https://t.me/+0QKwgEMQwHg2Y2U0"

# حالات المحادثة الإدارية
(ADMIN_USER_ID, ADMIN_AMOUNT, ADMIN_SEARCH, ADMIN_BROADCAST, ADMIN_SRV_CATEGORY,
 ADMIN_SRV_NAME, ADMIN_SRV_DESC, ADMIN_SRV_PRICE, ADMIN_SRV_DURATION, 
 ADMIN_NEW_PRICE, ADMIN_CARD_CODE, ADMIN_CARD_AMOUNT, ADMIN_STOCK_CHOICE,
 ADMIN_STOCK_KEY, ADMIN_MAINTENANCE_TEXT, WAITING_USER_EMAIL, WAITING_RENTAL_CREDENTIALS,
 ADMIN_MSG_TARGET_ID, ADMIN_MSG_CONTENT) = range(19)

# ================= (2) نظام قاعدة البيانات السحابية (PostgreSQL / Neon) =================
def db_execute(query, params=()):
    pg_query = query.replace("?", "%s")
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute(pg_query, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()

def db_fetch_one(query, params=()):
    pg_query = query.replace("?", "%s")
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute(pg_query, params)
        result = cursor.fetchone()
        return result
    finally:
        cursor.close()
        conn.close()

def db_fetch_all(query, params=()):
    pg_query = query.replace("?", "%s")
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute(pg_query, params)
        result = cursor.fetchall()
        return result
    finally:
        cursor.close()
        conn.close()

def init_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, name TEXT, username TEXT, balance REAL DEFAULT 0.0, join_date TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS services (id SERIAL PRIMARY KEY, name TEXT, description TEXT, price REAL, duration TEXT, category TEXT DEFAULT 'digital', quantity INTEGER DEFAULT 0, file_id TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, user_id BIGINT, service_id INTEGER, status TEXT, order_date TEXT, custom_data TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cards (code TEXT PRIMARY KEY, amount REAL, is_used INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_keys (id SERIAL PRIMARY KEY, service_id INTEGER, key_text TEXT, is_sold INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_mode (id SERIAL PRIMARY KEY, is_active INTEGER DEFAULT 0, custom_message TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS last_broadcast (id SERIAL PRIMARY KEY, content TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS forced_channels (id SERIAL PRIMARY KEY, name TEXT, link TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS custom_buttons (btn_key TEXT PRIMARY KEY, btn_text TEXT, btn_action TEXT)''')
        
        conn.commit()
        
        cursor.execute("SELECT id FROM maintenance_mode WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO maintenance_mode (id, is_active) VALUES (1, 0)")
            conn.commit()
            
        default_btns = [
            ("cat_digital", "⚡ شحن الأدوات والبوكسات 🛠️", "show_cat_digital"),
            ("cat_subscriptions", "🔵 الاشتراكات 🚀", "show_cat_subscriptions"),
            ("cat_rentals", "🔧 خدمة إيجار الأدوات 🛠️", "show_cat_rentals"),
            ("cat_vip", "💎 عروض VIP الماسي ⭐", "show_cat_vip"),
            ("cat_free", "🎁 عروض مجانية حصرية 🆓", "show_cat_free"),
            ("my_orders", "ℹ️ سجل طلباتي 🔄", "my_orders"),
            ("my_profile", "⚡ حسابي ⚡", "my_profile"),
            ("charge_acc", "🔵 شحن بكود", "charge_account"),
            ("fund_acc", "🔵 تغذية حسابك", "fund_account")
        ]
        for key, text, action in default_btns:
            cursor.execute("INSERT INTO custom_buttons (btn_key, btn_text, btn_action) VALUES (%s, %s, %s) ON CONFLICT (btn_key) DO NOTHING", (key, text, action))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    print("\n✅ تم الاتصال بقاعدة بيانات Neon السحابية بنجاح!")

def add_user_if_not_exists(user_id, name, username):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, username, balance, join_date) VALUES (?, ?, ?, ?, ?)", 
                   (user_id, name, username, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def is_bot_under_maintenance():
    status = db_fetch_one("SELECT is_active FROM maintenance_mode WHERE id = 1")
    return status[0] == 1 if status else False

async def check_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin=False):
    if is_bot_under_maintenance() and not is_admin:
        if update.message: await update.message.reply_text("⚠️ البوت قيد الصيانة حالياً.", parse_mode='Markdown')
        elif update.callback_query: await update.callback_query.answer("⚙️ البوت تحت الصيانة", show_alert=True)
        return False
    return True

async def enforce_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID or context.user_data.get('is_subscribed', False): return True

    channels = db_fetch_all("SELECT name, link FROM forced_channels")
    keyboard = []
    if channels:
        for name, link in channels:
            keyboard.append([InlineKeyboardButton(f"📢 {name}", url=link)])
    else:
        keyboard.append([InlineKeyboardButton("📢 اشترك في القناة الرسمية 🔔", url=CHANNEL_LINK)])
        
    keyboard.append([InlineKeyboardButton("✅ تحقق من الاشتراك 🔄", callback_data="check_sub")])
    warning_text = "⚠️ **عذراً، يجب عليك أولاً الاشتراك في القنوات الرسمية أدناه لتتمكن من استخدام البوت:**"
    markup = InlineKeyboardMarkup(keyboard)
    
    if update.message: await update.message.reply_text(warning_text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query:
        try: await update.callback_query.message.edit_text(warning_text, reply_markup=markup, parse_mode='Markdown')
        except: await update.callback_query.answer("⚠️ يرجى الاشتراك في القنوات أولاً!", show_alert=True)
    return False

# ================= (3) واجهة العميل والأقسام =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_maintenance(update, context, is_admin=(user.id == ADMIN_ID)): return
    if not await enforce_subscription(update, context): return
    
    username = f"@{user.username}" if user.username else "لا يوجد"
    add_user_if_not_exists(user.id, user.first_name, username)
    
    b_dig = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('cat_digital',))
    b_sub = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('cat_subscriptions',))
    b_ren = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('cat_rentals',))
    b_vip = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('cat_vip',))
    b_fre = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('cat_free',))
    b_ord = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('my_orders',))
    b_pro = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('my_profile',))
    b_chg = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('charge_acc',))
    b_fnd = db_fetch_one("SELECT btn_text, btn_action FROM custom_buttons WHERE btn_key = ?", ('fund_acc',))

    text = (
        "✨ ━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━ ✨\n\n"
        f"👋 أهلاً بك يا [{user.first_name}](tg://user?id={user.id})\n"
        "في متجرك الآلي المتطور لشحن الأدوات والبوكسات والاشتراكات الرقمية 🚀\n\n"
        "🛒 ❲ اختر القسم المطلوب من القائمة أدناه ❳ 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = [
        [InlineKeyboardButton(b_dig[0], callback_data=b_dig[1]), InlineKeyboardButton(b_sub[0], callback_data=b_sub[1])],
        [InlineKeyboardButton(b_ren[0], callback_data=b_ren[1])],
        [InlineKeyboardButton(b_vip[0], callback_data=b_vip[1]), InlineKeyboardButton(b_fre[0], callback_data=b_fre[1])],
        [InlineKeyboardButton(b_ord[0], callback_data=b_ord[1]), InlineKeyboardButton(b_pro[0], callback_data=b_pro[1])],
        [InlineKeyboardButton(b_chg[0], callback_data=b_chg[1]), InlineKeyboardButton(b_fnd[0], callback_data=b_fnd[1])],
        [InlineKeyboardButton("🌐 واتساب", url=WHATSAPP_LINK), InlineKeyboardButton("🛠️ الدعم", url=SUPPORT_LINK)],
        [InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='Markdown')

async def main_buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.callback_query
    data = query.data

    if data == "check_sub":
        context.user_data['is_subscribed'] = True
        await query.answer("✅ تم التحقق بنجاح!", show_alert=True)
        await start_command(update, context)
        return

    if not await check_maintenance(update, context, is_admin=(user_id == ADMIN_ID)): return
    if not await enforce_subscription(update, context): return
    
    await query.answer()

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
            "🌟 يرجى اختيار وسيلة الدفع المناسبة لك من القائمة أدناه 👇"
        )
        payment_keyboard = [
            [InlineKeyboardButton("🔹 محفظة جيب", callback_data="pay_jeep"), InlineKeyboardButton("🔹 جوالي", callback_data="pay_jawali")],
            [InlineKeyboardButton("🔹 وان كاش", callback_data="pay_onecash"), InlineKeyboardButton("🏦 بنك الكريمي", callback_data="pay_kuraimi")],
            [InlineKeyboardButton("🟡 Binance ID", callback_data="pay_binance"), InlineKeyboardButton("💳 VISA Card", callback_data="pay_visa")],
            [InlineKeyboardButton("🟢 شحن عبر كود بطاقة", callback_data="charge_account")],
            [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية 🔄", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_text, reply_markup=InlineKeyboardMarkup(payment_keyboard), parse_mode='Markdown')

    elif data in ["pay_jeep", "pay_jawali", "pay_onecash", "pay_kuraimi", "pay_binance", "pay_visa"]:
        details = {
            "pay_jeep": "💎 **محفظة جيب:**\n`580300`",
            "pay_jawali": "💎 **محفظة جوالي:**\n`777728478`",
            "pay_onecash": "💎 **وان كاش:**\n`178109713`",
            "pay_kuraimi": "🏦 **الكريمي:**\nيمني: `3204168937`\nسعودي: `3204433991`\nدولار: `3191718649`",
            "pay_binance": "🟡 **Binance ID:**\n`1063050653`",
            "pay_visa": "💳 **VISA:**\n`4909800019663092`"
        }
        await query.message.edit_text(details[data], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        titles = {"digital": "⚡ خدمات شحن الأدوات والبوكسات 🛠️", "subscriptions": "🔵 الاشتراكات الرقمية 🚀", "rentals": "🔧 خدمة إيجار الأدوات 🛠️", "vip": "💎 عروض VIP الماسي ⭐", "free": "🎁 العروض المجانية الحصرية 🆓"}
        title = titles.get(category, "🛒 قائمة الخدمات")
        
        services = db_fetch_all("SELECT id, name, price FROM services WHERE category = ?", (category,))
        if not services:
            await query.message.edit_text(f"🚧 لا توجد خدمات في قسم {title} حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))
            return
            
        keyboard = []
        for srv in services:
            stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv[0],))[0]
            status = "🟢 تتوفر" if (stock > 0 or category in ["free", "rentals"]) else "🔴 نفدت"
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
        
        # 🔴 الحل الجذري لمشكلة عدم ظهور تنبيه الرصيد غير الكافي
        if user_info[0] < price and cat != "free":
            await query.message.edit_text(
                f"❌ **عذراً، رصيدك غير كافٍ لإتمام الشراء!**\n\n"
                f"💰 رصيدك الحالي: `{user_info[0]}` $\n"
                f"💵 سعر الخدمة المطلوب: `{price}` $\n"
                f"⚠️ العجز لديك: `{price - user_info[0]}` $\n\n"
                f"يرجى تغذية حسابك أولاً للاستفادة من الخدمات.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🟢 تغذية رصيد الحساب", callback_data="fund_account")],
                    [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]
                ]),
                parse_mode='Markdown'
            )
            return

        if cat == "free" or price == 0:
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅ مجاني', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await query.message.edit_text("🎁 **إليك طلبك المجاني الفوري:**", parse_mode='Markdown')
            if srv[3]:
                try: await context.bot.send_document(chat_id=user_id, document=srv[3], caption=f"🎁 طلبك المجاني: {srv[1]}")
                except:
                    try: await context.bot.send_photo(chat_id=user_id, photo=srv[3], caption=f"🎁 طلبك المجاني: {srv[1]}")
                    except: await context.bot.send_message(chat_id=user_id, text=f"🎁 تفاصيل طلبك المجاني:\n{srv[3]}")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"✅ تم تسليم العرض المجاني بنجاح لـ {srv[1]}")
            return

        stock_key = db_fetch_one("SELECT id, key_text FROM product_keys WHERE service_id = ? AND is_sold = 0 LIMIT 1", (srv_id,))
        if not stock_key and cat not in ["rentals", "digital", "subscriptions", "vip"]:
            await query.message.edit_text("❌ **عذراً، نفدت الكمية لهذه الخدمة حالياً!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))
            return

        if cat == "rentals":
            context.user_data['pending_rental_srv_id'] = srv_id
            context.user_data['pending_rental_price'] = price
            context.user_data['pending_rental_name'] = srv[1]
            await query.message.edit_text("⏳ **طلب إيجار قيد التجهيز**\n\nأرسل لنا الآن أي ملاحظة للبوت أو اضغط /cancel للإلغاء:", parse_mode='Markdown')
            context.user_data['waiting_rental_note'] = True
            return

        if cat in ["digital", "subscriptions", "vip"]:
            if stock_key:
                new_balance = user_info[0] - price
                db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
                db_execute("UPDATE product_keys SET is_sold = 1 WHERE id = ?", (stock_key[0],))
                db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                
                await query.message.edit_text("✅ **تم الشراء بنجاح!**\n\n🎁 تفاصيل اشتراكك في الرسالة التالية 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]))
                await context.bot.send_message(chat_id=user_id, text=stock_key[1])
                return

            context.user_data['pending_srv_id'] = srv_id
            context.user_data['pending_price'] = price
            context.user_data['pending_name'] = srv[1]
            await query.message.edit_text("📧 **تفعيل الشحن والاشتراك**\n\nيرجى إرسال **الإيميل الخاص بك** لتفعيل الاشتراك عليه فوراً:", parse_mode='Markdown')
            context.user_data['waiting_email_input'] = True
            return

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        text = "📦 لا توجد طلبات سابقة." if not orders else "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        info_msg = "🌟 **━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━** 🌟\n\n🤖 نظام إدارة متجرك الآلي المتطور للخدمات الرقمية."
        await query.message.edit_text(info_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "main_menu":
        await start_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        if not await check_maintenance(update, context): return
        if not await enforce_subscription(update, context): return

    if context.user_data.get('waiting_email_input'):
        email = update.message.text.strip()
        srv_id = context.user_data.get('pending_srv_id')
        price = context.user_data.get('pending_price')
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
            context.user_data.clear()
            return
            
        db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user.id))
        db_execute("INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد التفعيل ⏳', ?, ?)", 
                   (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Email: {email}"))
        context.user_data.clear()
        await update.message.reply_text("✅ **تم إرسال الطلب بنجاح!**\n⏳ جاري التنفيذ والتفعيل على حسابك.", parse_mode='Markdown')
        return
        
    if context.user_data.get('waiting_card'):
        code = update.message.text.strip()
        card = db_fetch_one("SELECT amount, is_used FROM cards WHERE code = ?", (code,))
        if not card or card[1] == 1:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم مسبقاً.")
            return
        db_execute("UPDATE cards SET is_used = 1 WHERE code = ?", (code,))
        db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (card[0], user.id))
        new_balance = db_fetch_one("SELECT balance FROM users WHERE user_id = ?", (user.id,))[0]
        context.user_data['waiting_card'] = False
        await update.message.reply_text(f"✅ **تم الشحن بنجاح!**\n💰 القيمة: `{card[0]}` $\n💵 رصيدك الجديد: `{new_balance}` $", parse_mode='Markdown')
        return

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message: await update.message.reply_text("🚫 تم الإلغاء.")
    return ConversationHandler.END

def main():
    init_db()
    try: urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook").read()
    except: pass
        
    # تحسين السرعة عبر تسريع التعامل مع الطلبات المتزامنة
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))

    print("\n🚀 البوت يعمل الآن بسرعة فائقة ومتصل بقاعدة بيانات Neon السحابية!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
