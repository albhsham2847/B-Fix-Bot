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

# ================= (3) واجهة العميل والأقسام (مع التعديلات الستة السابقة) =================
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
        pay_msg = (
            f"{details[data]}\n\n"
            "💎 **بـوابـة الدفـع الإلكتروني المعتمدة**\n\n"
            "يرجى إتمام عملية التحويل إلى الحساب الموضح أعلاه، ثم إرسال **صورة سند التحويل (الإيصال)** في هذه المحادثة مباشرة 👇\n\n"
            "✨ *شكراً لثقتك في **متجر B-Fix Software**.*"
        )
        context.user_data['waiting_receipt'] = True
        context.user_data['payment_method_name'] = data
        await query.message.edit_text(pay_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]), parse_mode='Markdown')

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
        
        # التعديل الأول: تنبيه الرصيد غير الكافي الفخم
        if user_info[0] < price and cat != "free":
            deficit = price - user_info[0]
            await query.message.edit_text(
                "🚫 **عـذراً عميلنـا العـزيـز!**\n"
                "رصيدك الحالي لا يغطي قيمة هذا الاشتراك.\n\n"
                f"💳 **سعر الاشتراك:** `{price}` $\n"
                f"💰 **رصيدك الحالي:** `{user_info[0]}` $\n"
                f"⚠️ **المبلغ المتبقي المطلوب:** `{deficit}` $\n\n"
                "🌟 *يرجى شحن حسابك للمضي قدماً والاستمتاع بخدمات B-Fix السريعة.*",
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

        # التعديل الخامس: عروض VIP ومعرفة المتصل
        if cat == "vip":
            if "رقم المتصل" in srv[1] or "معرفة" in srv[1]:
                context.user_data['waiting_caller_id'] = True
                context.user_data['vip_srv_id'] = srv_id
                context.user_data['vip_price'] = price
                context.user_data['vip_name'] = srv[1]
                await query.message.edit_text("📞 **خدمة معرفة هوية/رقم المتصل**\n\nيرجى إرسال **رقم الهاتف** المراد البحث عنه الآن:", parse_mode='Markdown')
                return
            else:
                bot_vip_text = (
                    f"💎 **{srv[1]}**\n\n"
                    "لطلب وتنسيق تطوير بوت باسمك وحقوقك الخاصة، يرجى التواصل مباشرة عبر زر الواتساب أدناه للتنسيق الشامل:\n\n"
                    "✨ **متجر B-Fix Software**"
                )
                await query.message.edit_text(bot_vip_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 تواصل معي عبر الواتساب 💬", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]), parse_mode='Markdown')
                return

        # التعديل الثاني: إيجار الأدوات
        if cat == "rentals":
            new_balance = user_info[0] - price
            db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'قيد التجهيز ⏳ إيجار', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            await query.message.edit_text(
                "⏳ **تـم تـسجيـل طـلب الإيـجـار بـنـجـاح!**\n\n"
                "🕒 جاري تجهيز بيانات الدخول (الإيميل وكلمة المرور) الخاصة بأداة الإيجار من سيرفر الأداة.\n"
                "⏱️ يرجى الانتظار من **5 إلى 10 دقائق** كحد أقصى، وسنرسل لك بيانات الحساب فوراً هنا في البوت!\n\n"
                "✨ *شكراً لثقتك في متجر B-Fix Software.*",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]),
                parse_mode='Markdown'
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔔 **طلب إيجار جديد قيد الانتظار!**\n▪️ العميل: {user_info[1]} (`{user_id}`)\n▪️ الأداة: {srv[1]}\n\nيرجى سحب البيانات وتفعيلها للعميل عبر لوحة الإدارة.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 إرسال بيانات الإيجار للعميل", callback_data=f"send_rental_{user_id}_{srv_id}")]])
            )
            return

        stock_key = db_fetch_one("SELECT id, key_text FROM product_keys WHERE service_id = ? AND is_sold = 0 LIMIT 1", (srv_id,))
        if cat in ["digital", "subscriptions"] and stock_key:
            new_balance = user_info[0] - price
            db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            db_execute("UPDATE product_keys SET is_sold = 1 WHERE id = ?", (stock_key[0],))
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            await query.message.edit_text("✅ **تم الشراء بنجاح!**\n\n🎁 تفاصيل اشتراكك في الرسالة التالية 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]))
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **تفاصيل اشتراكك:**\n\n{stock_key[1]}")
            return

        # التعديل الثالث: طلب الإيميل والتفعيل اليدوي المتقدم
        context.user_data['pending_srv_id'] = srv_id
        context.user_data['pending_price'] = price
        context.user_data['pending_name'] = srv[1]
        await query.message.edit_text(
            "📧 **تـفـعـيـل الاشـتـراك**\n\n"
            f"أهلاً بك! لإتمام تفعيل أداة `{srv[1]}`، يرجى إرسال **الإيميل (البريد الإلكتروني)** الذي قمت بالتسجيل به في الموقع الرسمي للأداة أسفل هذه الرسالة 👇\n\n"
            "✨ **متجر B-Fix Software**",
            parse_mode='Markdown'
        )
        context.user_data['waiting_email_input'] = True
        return

    elif data.startswith("send_rental_"):
        if user_id != ADMIN_ID: return
        parts = data.split("_")
        target_user = parts[2]
        srv_id = parts[3]
        context.user_data['admin_target_rental_user'] = target_user
        context.user_data['admin_target_rental_srv'] = srv_id
        await query.message.reply_text("✍️ أرسل الآن بيانات الإيجار (الإيميل وكلمة المرور) في رسالة واحدة ليتم إرسالها للعميل فوراً:")

    elif data.startswith("send_activation_"):
        if user_id != ADMIN_ID: return
        parts = data.split("_")
        target_user = parts[2]
        success_activation_msg = (
            "🎉 **تـم تـفـعـيـل حـسـابـك بـنـجـاح!**\n\n"
            "عميلنا العزيز، تم تفعيل اشتراكك في الأداة بنجاح ✅\n"
            "🔐 يمكنك الآن تسجيل الدخول في الأداة باستخدام الإيميل وكلمة المرور الخاصة بك.\n\n"
            "✨ *شكراً لثقتكم بخدمات مركز العدني (B-Fix Software) نتمنى لك عملاً موفقاً!*"
        )
        await context.bot.send_message(chat_id=target_user, text=success_activation_msg, parse_mode='Markdown')
        await query.message.reply_text("✅ تم إرسال إشعار التفعيل الناجح للعميل بنجاح!")

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        text = "📦 لا توجد طلبات سابقة." if not orders else "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        info_msg = "🌟 **━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━** 🌟\n\n🤖 نظام إدارة متجرك الآلي المتطور للخدمات الرقمية."
        await query.message.edit_text(info_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "main_menu":
        await start_command(update, context)

# ================= (4) لوحة تحكم المدير المرتبة (حسب صورتك تماماً وعبر /admin) =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ عذراً، هذا الأمر مخصص للمشرف فقط.")
        return

    status = is_bot_under_maintenance()
    maint_label = "🟢 (مفعل)" if status else "🔴 (معطل)"

    admin_keyboard = [
        [InlineKeyboardButton("🔴 خصم رصيد", callback_data="admin_sub_bal"), InlineKeyboardButton("🟢 إضافة رصيد", callback_data="admin_add_bal")],
        [InlineKeyboardButton("🔍 بحث عن مستخدم", callback_data="admin_search_user"), InlineKeyboardButton("🎫 كود شحن", callback_data="admin_gen_card")],
        [InlineKeyboardButton("✉️ مراسلة عميل عبر الآيدي", callback_data="admin_msg_user")],
        [InlineKeyboardButton("📢 إدارة قنوات الاشتراك الإجباري", callback_data="admin_channels")],
        [InlineKeyboardButton("🎛️ إدارة وتعديل أزرار القائمة", callback_data="admin_buttons_edit")],
        [InlineKeyboardButton("🛠️ إدارة الخدمات والأقسام والأكواد", callback_data="admin_services")],
        [InlineKeyboardButton("🗑️ حذف آخر إشعار", callback_data="admin_del_last"), InlineKeyboardButton("📢 إشعار جماعي", callback_data="admin_broadcast")],
        [InlineKeyboardButton(f"⚙️ صيانة البوت: {maint_label}", callback_data="admin_toggle_maint")],
        [InlineKeyboardButton("📊 إحصائيات المتجر", callback_data="admin_stats")],
        [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]
    ]
    await update.message.reply_text("👑 **لوحة تحكم المطور والمدير المتقدمة**\n\nاختر الإجراء المطلوب من الأزرار أدناه:", reply_markup=InlineKeyboardMarkup(admin_keyboard), parse_mode='Markdown')

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "admin_add_bal":
        context.user_data['admin_action'] = 'add_balance'
        await query.message.reply_text("✍️ أرسل **آيدي (ID) العميل** لإضافة الرصيد له:")
    elif data == "admin_sub_bal":
        context.user_data['admin_action'] = 'sub_balance'
        await query.message.reply_text("✍️ أرسل **آيدي (ID) العميل** لخصم الرصيد منه:")
    elif data == "admin_search_user":
        context.user_data['admin_action'] = 'search_user'
        await query.message.reply_text("🔍 أرسل **آيدي (ID) العميل** أو المعرف للبحث عنه:")
    elif data == "admin_gen_card":
        context.user_data['admin_action'] = 'gen_card'
        await query.message.reply_text("🎫 أرسل **قيمة كود الشحن** (رقم فقط، مثلاً: 10):")
    elif data == "admin_msg_user":
        context.user_data['admin_action'] = 'msg_user_id_step1'
        await query.message.reply_text("✉️ أرسل **آيدي (ID) العميل** المراد مراسلته:")
    elif data == "admin_channels":
        channels = db_fetch_all("SELECT id, name, link FROM forced_channels")
        text = "📢 **قنوات الاشتراك الإجباري الحالية:**\n\n"
        for ch in channels: text += f"▪️ [{ch[1]}]({ch[2]})\n" if channels else "لا توجد قنوات.\n"
        text += "\nلإضافة قناة، أرسل بالصيغة:\n`اسم القناة | الرابط`"
        context.user_data['admin_action'] = 'add_channel'
        await query.message.reply_text(text, parse_mode='Markdown')
    elif data == "admin_buttons_edit":
        await query.message.reply_text("🎛️ لتعديل أزرار القائمة، يمكنك تعديل جدول `custom_buttons` بقاعدة البيانات مباشرة.")
    elif data == "admin_services":
        srvs = db_fetch_all("SELECT id, name, price, category FROM services")
        text = "🛠️ **قائمة الخدمات المسجلة:**\n\n"
        for s in srvs: text += f"🆔 `{s[0]}` - **{s[1]}** ({s[3]}) | {s[2]}$\n" if srvs else "لا توجد خدمات.\n"
        await query.message.reply_text(text, parse_mode='Markdown')
    elif data == "admin_del_last":
        db_execute("DELETE FROM orders WHERE id = (SELECT MAX(id) FROM orders)")
        await query.answer("🗑️ تم حذف آخر طلب بنجاح!", show_alert=True)
    elif data == "admin_broadcast":
        context.user_data['admin_action'] = 'broadcast'
        await query.message.reply_text("📢 أرسل **نص الإشعار الجماعي** لإرساله لجميع العملاء:")
    elif data == "admin_toggle_maint":
        current = is_bot_under_maintenance()
        new_status = 0 if current else 1
        db_execute("UPDATE maintenance_mode SET is_active = ? WHERE id = 1", (new_status,))
        await query.answer("✅ تم تحديث وضع الصيانة بنجاح!", show_alert=True)
    elif data == "admin_stats":
        u_cnt = db_fetch_one("SELECT COUNT(*) FROM users")[0]
        o_cnt = db_fetch_one("SELECT COUNT(*) FROM orders")[0]
        s_cnt = db_fetch_one("SELECT COUNT(*) FROM services")[0]
        await query.message.edit_text(f"📊 **إحصائيات المتجر:**\n\n▪️ العملاء: `{u_cnt}`\n▪️ الطلبات: `{o_cnt}`\n▪️ الخدمات: `{s_cnt}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]), parse_mode='Markdown')

# ================= (5) معالجة الرسائل النصية والإدارية =================
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""

    if user.id != ADMIN_ID:
        if not await check_maintenance(update, context): return
        if not await enforce_subscription(update, context): return

    # تنفيذ أوامر الإدارة عبر المحادثة النصية
    if user.id == ADMIN_ID and context.user_data.get('admin_action'):
        action = context.user_data.get('admin_action')
        if action == 'add_balance':
            context.user_data['target_user_id'] = int(text)
            context.user_data['admin_action'] = 'add_balance_amount'
            await update.message.reply_text("✍️ أرسل الآن **المبلغ المراد إضافته**:")
            return
        elif action == 'add_balance_amount':
            target_id = context.user_data.get('target_user_id')
            amount = float(text)
            db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم إضافة `{amount}` $ للعميل بنجاح!")
            try: await context.bot.send_message(chat_id=target_id, text=f"🎉 تم إضافة مبلغ `{amount}` $ إلى رصيدك بواسطة الإدارة!")
            except: pass
            return
        elif action == 'sub_balance':
            context.user_data['target_user_id'] = int(text)
            context.user_data['admin_action'] = 'sub_balance_amount'
            await update.message.reply_text("✍️ أرسل الآن **المبلغ المراد خصمه**:")
            return
        elif action == 'sub_balance_amount':
            target_id = context.user_data.get('target_user_id')
            amount = float(text)
            db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, target_id))
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم خصم `{amount}` $ من العميل بنجاح!")
            return
        elif action == 'search_user':
            u_data = db_fetch_one("SELECT user_id, name, username, balance, join_date FROM users WHERE user_id::TEXT = ? OR username = ?", (text, text))
            context.user_data.clear()
            if u_data:
                await update.message.reply_text(f"👤 **المستخدم:** {u_data[1]}\n🆔 الآيدي: `{u_data[0]}`\n💰 الرصيد: `{u_data[3]}` $", parse_mode='Markdown')
            else: await update.message.reply_text("❌ لم يتم العثور على المستخدم.")
            return
        elif action == 'gen_card':
            try:
                amount = float(text)
                import random, string
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
                db_execute("INSERT INTO cards (code, amount, is_used) VALUES (?, ?, 0)", (code, amount))
                context.user_data.clear()
                await update.message.reply_text(f"🎫 **تم إنشاء الكود:**\n🔑 `{code}`\n💰 القيمة: `{amount}` $", parse_mode='Markdown')
            except Exception as e: await update.message.reply_text(f"❌ خطأ: {e}")
            return
        elif action == 'msg_user_id_step1':
            context.user_data['target_msg_user'] = int(text)
            context.user_data['admin_action'] = 'msg_user_id_step2'
            await update.message.reply_text("✍️ أرسل الآن **نص الرسالة** للعميل:")
            return
        elif action == 'msg_user_id_step2':
            target_id = context.user_data.get('target_msg_user')
            context.user_data.clear()
            try:
                await context.bot.send_message(chat_id=target_id, text=f"📬 **رسالة من الإدارة:**\n\n{text}")
                await update.message.reply_text("✅ تم إرسال الرسالة بنجاح!")
            except Exception as e: await update.message.reply_text(f"❌ فشل: {e}")
            return
        elif action == 'broadcast':
            context.user_data.clear()
            users = db_fetch_all("SELECT user_id FROM users")
            sent = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u[0], text=f"📢 **إشعار هام:**\n\n{text}")
                    sent += 1
                except: pass
            await update.message.reply_text(f"✅ تم الإرسال إلى `{sent}` مستخدم.")
            return

    # استقبال الإيميل لتفعيل الاشتراكات اليدوية (التعديل الثالث)
    if context.user_data.get('waiting_email_input'):
        email = text
        srv_id = context.user_data.get('pending_srv_id')
        price = context.user_data.get('pending_price')
        srv_name = context.user_data.get('pending_name')
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
            context.user_data.clear()
            return
            
        db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user.id))
        db_execute("INSERT INTO orders (user_id, service_id, status, order_date, custom_data) VALUES (?, ?, 'قيد التفعيل ⏳', ?, ?)", 
                   (user.id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Email: {email}"))
        context.user_data.clear()
        
        # رسالة الانتظار الفخمة للعميل
        await update.message.reply_text(
            "✅ **تـم اسـتـلام طـلـبك بـنـجـاح!**\n\n"
            "⏳ يرجى الانتظار من **5 إلى 10 دقائق** كحد أقصى، ريثما يقوم فريق العمل بتجهيز وتفعيل اشتراكك على الإيميل الذي أرسلته.\n"
            "سنرسل لك إشعاراً فور الانتهاء! 🌹\n\n"
            "✨ **متجر B-Fix Software**",
            parse_mode='Markdown'
        )
        
        # تنبيه المدير مع زر تفعيل الإشعار
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **طلب تفعيل أداة جديد!**\n▪️ العميل: {user_info[1]} (`{user.id}`)\n▪️ الأداة: {srv_name}\n▪️ الإيميل المرسل: `{email}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 إرسال إشعار التفعيل للعميل", callback_data=f"send_activation_{user.id}_{srv_id}")]])
        )
        return

    # استقبال طلبات رقم المتصل VIP
    if context.user_data.get('waiting_caller_id'):
        phone_number = text
        srv_name = context.user_data.get('vip_name')
        price = context.user_data.get('vip_price')
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user.id,))
        
        if user_info[0] < price:
            await update.message.reply_text("❌ رصيدك غير كافٍ.")
            context.user_data.clear()
            return
            
        db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user.id))
        context.user_data.clear()
        
        await update.message.reply_text(
            "⏳ **تـم اسـتـلام طـلـبك بـنـجـاح!**\n\n"
            "جاري فحص الرقم والبحث عنه، وسيتم إرسال النتيجة فور الحصول على الاسم ومعلومات المتصل.\n\n"
            "✨ *شكراً لثقتك في متجر B-Fix Software.*",
            parse_mode='Markdown'
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"💎 **طلب VIP (معرفة هوية متصل):**\n▪️ العميل: {user_info[1]} (`{user.id}`)\n▪️ الرقم المطلوب: `{phone_number}`")
        return

    # شحن الرصيد بكود البطاقة
    if context.user_data.get('waiting_card'):
        code = text
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

    # إيصالات الدفع (التعديل الرابع)
    if update.message.photo and context.user_data.get('waiting_receipt'):
        photo_file = update.message.photo[-1].file_id
        method = context.user_data.get('payment_method_name', 'غير محدد')
        context.user_data.clear()
        
        await update.message.reply_text(
            "⏳ **تـم اسـتـلام إيـصـال الدفـع بـنـجـاح!**\n\n"
            "جاري مراجعة السند من قبل الإدارة وسيتم شحن حسابك فوراً.\n\n"
            "✨ *شكراً لثقتك في متجر B-Fix Software.*",
            parse_mode='Markdown'
        )
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file,
            caption=f"📥 **إيصال تحويل جديد للشحن!**\n▪️ العميل: {user.first_name} (`{user.id}`)\n▪️ الوسيلة: `{method}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 قبول وشحن الرصيد", callback_data=f"approve_fund_{user.id}")]])
        )
        return

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message: await update.message.reply_text("🚫 تم الإلغاء.")
    return ConversationHandler.END

def main():
    init_db()
    try: urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook").read()
    except: pass
        
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))  # لوحة الإدارة تعمل حصرياً بأمر /admin للمشرف
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^(admin_|approve_fund_)"))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))

    print("\n🚀 البوت يعمل الآن بسرعة فائقة وبلوحة تحكم إدارية مرتبة!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()

