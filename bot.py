# ==============================================================================
# |   B-Fix Smart Bot - النسخة السحابية المحدثة (Neon + Dotenv + Users Info)     |
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

# تحميل متغيرات البيئة من ملف .env
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

# ================= (1) الإعدادات وقراءة متغيرات البيئة =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
CHANNEL_LINK = "https://t.me/+0QKwgEMQwHg2Y2U0"

COLOR_PRIMARY = "🔵"
COLOR_SUCCESS = "🟢"
COLOR_DANGER = "🔴"
COLOR_WARNING = "⚠️"
COLOR_INFO = "ℹ️"
COLOR_ACTION = "✨"

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
        # جدول المستخدمين المحدث ليشمل username
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY, 
                            name TEXT, 
                            username TEXT, 
                            balance REAL DEFAULT 0.0, 
                            join_date TEXT
                          )''')
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
    print("\n✅ تم الاتصال بقاعدة بيانات Neon السحابية بنجاح وتم فحص الجداول!")

def add_user_if_not_exists(user_id, name, username):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, username, balance, join_date) VALUES (?, ?, ?, ?, ?)", 
                   (user_id, name, username, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

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

# ================= (2.5) فحص قنوات الاشتراك الإجباري =================
async def enforce_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID: return True
    if context.user_data.get('is_subscribed', False): return True

    channels = db_fetch_all("SELECT name, link FROM forced_channels")
    keyboard = []
    if channels:
        for name, link in channels:
            keyboard.append([InlineKeyboardButton(f"📢 {name}", url=link)])
    else:
        keyboard.append([InlineKeyboardButton("📢 اشترك في القناة الرسمية 🔔", url=CHANNEL_LINK)])
        
    keyboard.append([InlineKeyboardButton("✅ تحقق من الاشتراك 🔄", callback_data="check_sub")])
    
    warning_text = (
        "⚠️ **عذراً عزيزي العميل!**\n\n"
        "🔒 لكي تتمكن من استخدام متجر **B-Fix Software** والاستفادة من الخدمات، يجب عليك أولاً الاشتراك في القنوات الإعلانية والرسمية أدناه 👇"
    )
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
    
    # حفظ المستخدم في قاعدة بيانات Neon تلقائياً (الآيدي، الاسم، اليوزر، وتاريخ الانضمام)
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
        await query.answer("✅ تم التحقق بنجاح! أهلاً بك في المتجر.", show_alert=True)
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
            "🌟 أهلاً بك عزيزي العميل. يرجى اختيار وسيلة الدفع المناسبة لك من القائمة أدناه لعرض تفاصيل الحساب المخصص بدقة 👇"
        )
        payment_keyboard = [
            [InlineKeyboardButton("🔹 محفظة جيب", callback_data="pay_jeep"), InlineKeyboardButton("🔹 جوالي", callback_data="pay_jawali")],
            [InlineKeyboardButton("🔹 وان كاش", callback_data="pay_onecash"), InlineKeyboardButton("🏦 بنك الكريمي", callback_data="pay_kuraimi")],
            [InlineKeyboardButton("🟡 Binance ID", callback_data="pay_binance"), InlineKeyboardButton("💳 VISA Card", callback_data="pay_visa")],
            [InlineKeyboardButton("🟢 شحن عبر كود بطاقة", callback_data="charge_account")],
            [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية 🔄", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_text, reply_markup=InlineKeyboardMarkup(payment_keyboard), parse_mode='Markdown')

    elif data == "pay_jeep":
        await query.message.edit_text("💎 **محفظة جيب:**\n`580300`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))
    elif data == "pay_jawali":
        await query.message.edit_text("💎 **محفظة جوالي:**\n`777728478`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))
    elif data == "pay_onecash":
        await query.message.edit_text("💎 **وان كاش:**\n`178109713`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))
    elif data == "pay_kuraimi":
        await query.message.edit_text("🏦 **الكريمي:**\nيمني: `3204168937`\nسعودي: `3204433991`\nدولار: `3191718649`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))
    elif data == "pay_binance":
        await query.message.edit_text("🟡 **Binance ID:**\n`1063050653`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))
    elif data == "pay_visa":
        await query.message.edit_text("💳 **VISA:**\n`4909800019663092`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]))

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        if category == "digital": title = "⚡ خدمات شحن الأدوات والبوكسات 🛠️"
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

                if user_info[0] < price:
            await query.message.edit_text(
                f"❌ **عذراً، رصيدك غير كافٍ لإتمام الشراء!**\n\n"
                f"💰 رصيدك الحالي: `{user_info[0]}` $\n"
                f"💵 سعر الخدمة: `{price}` $\n"
                f"⚠️ العجز لديك: `{price - user_info[0]}` $\n\n"
                f"يرجى تغذية حسابك أولاً للاستفادة من الخدمات.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🟢 تغذية رصيد الحساب", callback_data="fund_account")],
                    [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]
                ]),
                parse_mode='Markdown'
            )
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
            await query.message.edit_text("📧 **تفعيل الشحن والاشتراك**\n\nيرجى إرسال **الإيميل الخاص بك** المسجل في الموقع لتفعيل الاشتراك عليه فوراً:", parse_mode='Markdown')
            context.user_data['waiting_email_input'] = True
            return

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        if not orders: text = "📦 لا توجد طلبات سابقة."
        else: text = "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        info_msg = (
            "🌟 **━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━** 🌟\n\n"
            "🤖 **نظام إدارة متجرك الآلي المتطور للخدمات الرقمية وصيانة الهواتف الذكية.**\n\n"
            "✨ **مميزاتنا الحصرية:**\n"
            "⚡ شحن فوري وآمن لكافة الأدوات والبوكسات العالمية.\n"
            "🔧 خدمات إيجار الأدوات الاحترافية مع دعم فني مستمر.\n"
            "💎 اشتراكات VIP وعروض مجانية حصرية ومتجددة باستمرار.\n"
            "💳 بوابات دفع متعددة وآمنة وموثوقة 100%.\n\n"
            "🔒 **نعمل على خدمتكم على مدار الساعة 24/7 بكل احترافية وموثوقية.**"
        )
        await query.message.edit_text(info_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown', disable_web_page_preview=True)

    elif data == "main_menu":
        await start_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id == ADMIN_ID and context.user_data.get('waiting_msg_id'):
        if not update.message.text.isdigit():
            await update.message.reply_text("❌ الآيدي يجب أن يكون أرقاماً فقط.")
            return
        context.user_data['target_user_id'] = int(update.message.text)
        context.user_data['waiting_msg_id'] = False
        context.user_data['waiting_msg_content'] = True
        await update.message.reply_text("✍️ أرسل الآن نص الرسالة للعميل:")
        return

    if user.id == ADMIN_ID and context.user_data.get('waiting_msg_content'):
        target_id = context.user_data.get('target_user_id')
        msg_text = update.message.text
        context.user_data.clear()
        try:
            await context.bot.send_message(chat_id=target_id, text=f"🔔 **إشعار من الإدارة:**\n\n{msg_text}", parse_mode='Markdown')
            await update.message.reply_text(f"✅ تم الإرسال للعميل بالآيدي: `{target_id}`", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الإرسال: `{e}`", parse_mode='Markdown')
        return

    if user.id != ADMIN_ID:
        if not await check_maintenance(update, context): return
        if not await enforce_subscription(update, context): return

    if context.user_data.get('waiting_email_input'):
        email = update.message.text.strip()
        srv_id = context.user_data.get('pending_srv_id')
        price = context.user_data.get('pending_price')
        srv_name = context.user_data.get('pending_name')
        
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
            context.user_data.clear()
            return
            
        new_balance = user_info[0] - price
        db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user.id))
        db_execute("INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد التفعيل ⏳', ?, ?)", 
                   (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Email: {email}"))
        
        context.user_data.clear()
        await update.message.reply_text("✅ **تم إرسال الطلب بنجاح!**\n⏳ جاري التنفيذ والتفعيل على حسابك.", parse_mode='Markdown')
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 **طلب جديد للتفعيل!**\n👤 العميل: {user_info[1]}\n🆔 `{user.id}`\n🛒 {srv_name}\n📧 `{email}`", parse_mode='Markdown')
        return

    if context.user_data.get('waiting_rental_note'):
        note = update.message.text.strip()
        srv_id = context.user_data.get('pending_rental_srv_id')
        price = context.user_data.get('pending_rental_price')
        srv_name = context.user_data.get('pending_rental_name')
        
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
            context.user_data.clear()
            return
            
        new_balance = user_info[0] - price
        db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user.id))
        db_execute("INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد تجهيز الإيجار ⏳', ?, ?)", 
                   (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Note: {note}"))
        
        context.user_data.clear()
        await update.message.reply_text("⏳ **تم استلام طلب الإيجار!**\nانتظر 5-10 دقائق ليتم إرسال بيانات الحساب.", parse_mode='Markdown')
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔧 **طلب إيجار جديد!**\n👤 {user_info[1]}\n🆔 `{user.id}`\n🛠️ {srv_name}\n📝 {note}", parse_mode='Markdown')
        return
        
    if context.user_data.get('waiting_card'):
        code = update.message.text.strip()
        card = db_fetch_one("SELECT amount, is_used FROM cards WHERE code = ?", (code,))
        if not card or card[1] == 1:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم.")
            return
        db_execute("UPDATE cards SET is_used = 1 WHERE code = ?", (code,))
        db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (card[0], user.id))
        new_balance = db_fetch_one("SELECT balance FROM users WHERE user_id = ?", (user.id,))[0]
        context.user_data['waiting_card'] = False
        await update.message.reply_text(f"✅ **تم الشحن بنجاح!**\n💰 القيمة: `{card[0]}` $\n💵 رصيدك الجديد: `{new_balance}` $", parse_mode='Markdown')
        return

# ================= (4) لوحة تحكم المشرف =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return 
    m_status = "🟢 (مفعل)" if is_bot_under_maintenance() else "🔴 (معطل)"
    text = "👑 **لوحة تحكم المطور والمدير المتقدمة**"
    
    keyboard = [
        [InlineKeyboardButton("🟢 إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("🔴 خصم رصيد", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎟 كود شحن", callback_data="adm_new_card"), InlineKeyboardButton("🔎 بحث عن مستخدم", callback_data="adm_search")],
        [InlineKeyboardButton("💬 مراسلة عميل عبر الآيدي ✉️", callback_data="adm_send_msg_id")],
        [InlineKeyboardButton("📢 إدارة قنوات الاشتراك الإجباري", callback_data="adm_channels_menu")],
        [InlineKeyboardButton("🎛️ إدارة وتعديل أزرار القائمة", callback_data="adm_buttons_menu")],
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
    elif data == "adm_channels_menu":
        channels = db_fetch_all("SELECT id, name, link FROM forced_channels")
        ch_text = "📢 **قنوات الاشتراك الإجباري الحالية:**\n\n"
        keyboard = [[InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="adm_add_channel")]]
        if channels:
            for ch_id, ch_name, ch_link in channels:
                ch_text += f"▪️ {ch_name} | [رابط]({ch_link})\n"
                keyboard.append([InlineKeyboardButton(f"🗑️ حذف: {ch_name}", callback_data=f"delch_{ch_id}")])
        else:
            ch_text += "لا توجد قنوات مضافة حالياً (سيتم استخدام القناة الافتراضية)."
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")])
        await query.message.edit_text(ch_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown', disable_web_page_preview=True)
    elif data == "adm_add_channel":
        await query.message.edit_text("✍️ أرسل الآن **اسم القناة** (مثال: قناة B-Fix الرسمية):")
        context.user_data['waiting_ch_name'] = True
        return
    elif data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        db_execute("DELETE FROM forced_channels WHERE id = ?", (ch_id,))
        await query.answer("✅ تم حذف القناة بنجاح.", show_alert=True)
        await admin_menus_handler(update, context)
    elif data == "adm_buttons_menu":
        buttons = db_fetch_all("SELECT btn_key, btn_text FROM custom_buttons")
        keyboard = [[InlineKeyboardButton(f"✏️ تعديل: {b[1]}", callback_data=f"editbtn_{b[0]}")] for b in buttons]
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")])
        await query.message.edit_text("🎛️ **اختر الزر الذي تريد تعديل نصه:**", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("editbtn_"):
        bkey = data.split("_", 1)[1]
        context.user_data['edit_btn_key'] = bkey
        await query.message.edit_text("✍️ أرسل الآن **النص الجديد** للزر (مع الرموز التعبيرية إن أردت):\n(أرسل /cancel للإلغاء)")
        return
    elif data == "adm_send_msg_id":
        await query.message.edit_text("✍️ أرسل **آيدي (ID) العميل** المراد مراسلته:")
        context.user_data['waiting_msg_id'] = True
        return
    elif data == "adm_toggle_main":
        current = is_bot_under_maintenance()
        if current: db_execute("UPDATE maintenance_mode SET is_active = 0 WHERE id = 1")
        else: db_execute("UPDATE maintenance_mode SET is_active = 1 WHERE id = 1")
        await admin_panel(update, context)
    elif data == "adm_stats":
        u_count = db_fetch_one("SELECT COUNT(*) FROM users")[0]
        o_count = db_fetch_one("SELECT COUNT(*) FROM orders")[0]
        await query.message.edit_text(f"📊 **إحصائيات المتجر:**\n👥 المستخدمين: `{u_count}`\n📦 الطلبات: `{o_count}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")]]))
    elif data == "adm_srv_menu":
        keyboard = [
            [InlineKeyboardButton("🟢 إضافة خدمة جديدة", callback_data="adm_add_srv")],
            [InlineKeyboardButton("🔵 إضافة أكواد / كميات لمخزون خدمة", callback_data="adm_stock_list")],
            [InlineKeyboardButton("✏️ تعديل سعر خدمة", callback_data="adm_edit_prc_list")],
            [InlineKeyboardButton("🔴 حذف خدمة", callback_data="adm_del_srv_list")],
            [InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")]
        ]
        await query.message.edit_text("📦 **إدارة الخدمات والأقسام والأكواد:**", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data in ["adm_del_srv_list", "adm_edit_prc_list", "adm_stock_list"]:
        services = db_fetch_all("SELECT id, name, category FROM services")
        if not services:
            await query.message.edit_text("لا توجد خدمات مضافة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))
            return
        if data == "adm_del_srv_list": pref, action = "delsrv_", "حذف"
        elif data == "adm_edit_prc_list": pref, action = "editprc_", "تعديل سعر"
        else: pref, action = "addstock_", "إضافة أكواد لـ"

        keyboard = [[InlineKeyboardButton(f"▪️ {s[1]} ({s[2]})", callback_data=f"{pref}{s[0]}")] for s in services]
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")])
        await query.message.edit_text(f"اختر الخدمة لـ {action}:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("delsrv_"):
        srv_id = int(data.split("_")[1])
        db_execute("DELETE FROM services WHERE id = ?", (srv_id,))
        db_execute("DELETE FROM product_keys WHERE service_id = ?", (srv_id,))
        await query.message.edit_text("✅ تم حذف الخدمة وجميع أكوادها بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))

# ================= (5) معالجات المحادثة الإدارية =================
async def admin_conv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data

    if data in ["adm_add_bal", "adm_sub_bal"]:
        context.user_data['action'] = data 
        await query.message.edit_text("✍️ أرسل آيدي (ID) المستخدم:")
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
        await query.message.edit_text("➕ **اختر القسم المخصص لإضافة الخدمة:**", reply_markup=markup)
        return ADMIN_SRV_CATEGORY
    elif data == "adm_new_card":
        await query.message.edit_text("🎟 أرسل كود البطاقة الجديد:")
        return ADMIN_CARD_CODE
    elif data.startswith("editprc_"):
        context.user_data['edit_id'] = int(data.split("_")[1])
        await query.message.edit_text("💵 أرسل السعر الجديد بالأرقام:")
        return ADMIN_NEW_PRICE
    elif data.startswith("addstock_"):
        context.user_data['stock_id'] = int(data.split("_")[1])
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 إضافة أكواد حقيقية (مفصول بـ ===)", callback_data="stock_real")],
            [InlineKeyboardButton("🔢 إضافة أرقام وهمية للكمية فقط", callback_data="stock_fake")],
            [InlineKeyboardButton("🔴 إلغاء", callback_data="stock_cancel")]
        ])
        await query.message.edit_text("📦 **اختر طريقة إضافة المخزون والكمية:**", reply_markup=markup)
        return ADMIN_STOCK_CHOICE

async def adm_rx_stock_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "stock_cancel":
        await query.message.edit_text("🚫 تم الإلغاء.")
        return ConversationHandler.END

    if query.data == "stock_real":
        context.user_data['stock_type'] = "real"
        await query.message.edit_text("🔑 **أرسل الأكواد الآن:**\n\n*(يمكنك إرسال عدة أكواد حقيقية مفصول بينها بـ `===`)*", parse_mode='Markdown')
        return ADMIN_STOCK_KEY
    elif query.data == "stock_fake":
        context.user_data['stock_type'] = "fake"
        await query.message.edit_text("🔢 **أرسل الرقم المطلوب لزيادة الكمية الوهمية (مثلاً: 10 أو 50):**")
        return ADMIN_STOCK_KEY

async def adm_rx_stock_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    srv_id = context.user_data.get('stock_id')
    stype = context.user_data.get('stock_type')

    if stype == "fake":
        try:
            qty_add = int(text)
            for i in range(qty_add):
                db_execute("INSERT INTO product_keys (service_id, key_text) VALUES (?, ?)", (srv_id, f"رقم وهمي كمية رقم {i+1}"))
            total_stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
            db_execute("UPDATE services SET quantity = ? WHERE id = ?", (total_stock, srv_id))
            await update.message.reply_text(f"✅ تم زيادة الكمية بمقدار `{qty_add}` وحدة وهمية.\n📦 الإجمالي المتوفر: `{total_stock}`", parse_mode='Markdown')
        except:
            await update.message.reply_text("❌ يرجى إرسال أرقام صحيحة فقط.")
    else:
        keys = [k.strip() for k in text.split('===') if k.strip()] if "===" in text else [text]
        for k in keys:
            db_execute("INSERT INTO product_keys (service_id, key_text) VALUES (?, ?)", (srv_id, k))
        total_stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
        db_execute("UPDATE services SET quantity = ? WHERE id = ?", (total_stock, srv_id))
        await update.message.reply_text(f"✅ تم إضافة `{len(keys)}` أكواد بنجاح.\n📦 إجمالي الكمية بالمخزون: `{total_stock}`", parse_mode='Markdown')

    context.user_data.clear()
    return ConversationHandler.END

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
    user = db_fetch_one("SELECT name, username, balance, join_date FROM users WHERE user_id = ?", (int(update.message.text),))
    if user: await update.message.reply_text(f"👤 الاسم: {user[0]}\n🔗 يوزر: {user[1]}\n💰 الرصيد: {user[2]}$\n📅 الانضمام: {user[3]}")
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
        await update.message.reply_text("🎁 **قسم العروض المجانية:**\n\nأرسل الآن **الملف أو الصورة أو الفيديو أو رابط/نص العرض المجاني**:")
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
        await update.message.reply_text("✅ تمت إضافة العرض المجاني بنجاح!")
        return ConversationHandler.END
    else:
        duration = update.message.text
        db_execute("INSERT INTO services (name, description, price, duration, category, quantity) VALUES (?, ?, ?, ?, ?, 0)", 
                   (context.user_data['s_name'], context.user_data['s_desc'], context.user_data['s_price'], duration, cat))
        await update.message.reply_text("✅ تمت إضافة الخدمة بنجاح!")
        return ConversationHandler.END

async def adm_rx_editprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_price = float(update.message.text)
        db_execute("UPDATE services SET price = ? WHERE id = ?", (new_price, context.user_data['edit_id']))
        await update.message.reply_text("✅ تم تعديل سعر الخدمة بنجاح.")
    except:
        await update.message.reply_text("❌ يرجى إرسال رقم صحيح للسعر.")
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
        entry_points=[CallbackQueryHandler(admin_conv_start, pattern="^(adm_add_bal|adm_sub_bal|adm_search|adm_broadcast|adm_add_srv|adm_new_card|editprc_.*|addstock_.*)$")],
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
            ADMIN_NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_editprice)],
            ADMIN_STOCK_CHOICE: [CallbackQueryHandler(adm_rx_stock_choice, pattern="^stock_")],
            ADMIN_STOCK_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_stock_key)],
            ADMIN_CARD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("💵 أرسل القيمة:"))],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True
    ))
    
    app.add_handler(CallbackQueryHandler(admin_menus_handler, pattern="^(adm_|delsrv_|delch_|editbtn_)"))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    
    async def global_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id == ADMIN_ID and context.user_data.get('waiting_ch_name'):
            context.user_data['ch_name_val'] = update.message.text.strip()
            context.user_data['waiting_ch_name'] = False
            context.user_data['waiting_ch_link'] = True
            await update.message.reply_text("🔗 أرسل الآن **رابط الدعوة الخاص** بالقناة:")
            return
            
        if user.id == ADMIN_ID and context.user_data.get('waiting_ch_link'):
            ch_link = update.message.text.strip()
            ch_name = context.user_data.get('ch_name_val')
            context.user_data.clear()
            db_execute("INSERT INTO forced_channels (name, link) VALUES (?, ?)", (ch_name, ch_link))
            await update.message.reply_text("✅ تمت إضافة القناة بنجاح لقائمة الاشتراك الإجباري!")
            return

        if user.id == ADMIN_ID and context.user_data.get('edit_btn_key'):
            bkey = context.user_data.get('edit_btn_key')
            new_text = update.message.text.strip()
            context.user_data.clear()
            db_execute("UPDATE custom_buttons SET btn_text = ? WHERE btn_key = ?", (new_text, bkey))
            await update.message.reply_text("✅ تم تعديل نص الزر بنجاح وتحديثه في واجهة العملاء!")
            return

        await handle_text_messages(update, context)

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_text_router))

    print("\n✅ البوت يعمل بكامل الخصائص ومتصل بقاعدة بيانات Neon السحابية بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main()
    except KeyboardInterrupt:
        print("\nتم إيقاف البوت.")

