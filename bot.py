# ==============================================================================
# |   B-Fix Smart Bot - النسخة السحابية المحسنة (Neon + Fast Updates + Custom Features) |
# |   متجر B-Fix Software | AI Store                                            |
# ==============================================================================

import os
import logging
import urllib.request
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)
from telegram.warnings import PTBUserWarning
import warnings

load_dotenv()
warnings.filterwarnings("ignore", category=PTBUserWarning)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= (0) خادم فحص الحيوية للاستضافات (Health Check) =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"B-Fix Bot AI Store is ALIVE and RUNNING 24/7!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ================= (1) الإعدادات والثوابت =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
CHANNEL_LINK = "https://t.me/+0QKwgEMQwHg2Y2U0"

# ================= (2) نظام قاعدة البيانات السحابية (Neon / PostgreSQL) =================
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
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

def db_fetch_all(query, params=()):
    pg_query = query.replace("?", "%s")
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute(pg_query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

def init_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    try:
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, name TEXT, username TEXT, balance REAL DEFAULT 0.0, join_date TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS services (id SERIAL PRIMARY KEY, name TEXT, description TEXT, price REAL, duration TEXT, delivery_time TEXT, category TEXT DEFAULT 'digital', quantity INTEGER DEFAULT 0, file_id TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, user_id BIGINT, service_id INTEGER, status TEXT, order_date TEXT, custom_data TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cards (code TEXT PRIMARY KEY, amount REAL, is_used INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_keys (id SERIAL PRIMARY KEY, service_id INTEGER, key_text TEXT, is_sold INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_mode (id SERIAL PRIMARY KEY, is_active INTEGER DEFAULT 0, custom_message TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS forced_channels (id SERIAL PRIMARY KEY, name TEXT, link TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS custom_buttons (btn_key TEXT PRIMARY KEY, btn_text TEXT, btn_action TEXT)''')
        conn.commit()
        
        cursor.execute("SELECT id FROM maintenance_mode WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO maintenance_mode (id, is_active, custom_message) VALUES (1, 0, '⚠️ البوت قيد الصيانة حالياً. نعود لكم قريباً!')")
            conn.commit()
            
        default_btns = [
            ("cat_digital", "⚡ شحن الأدوات والبوكسات 🛠️", "show_cat_digital"),
            ("cat_subscriptions", "🔵 الاشتراكات الرقمية 🚀", "show_cat_subscriptions"),
            ("cat_rentals", "🔧 خدمة إيجار الأدوات 🛠️", "show_cat_rentals"),
            ("cat_vip", "💎 عروض VIP والخدمات الخاصة ⭐", "show_cat_vip"),
            ("cat_free", "🎁 عروض مجانية حصرية 🆓", "show_cat_free"),
            ("my_orders", "ℹ️ سجل طلباتي 🔄", "my_orders"),
            ("my_profile", "⚡ حسابي ⚡", "my_profile"),
            ("charge_acc", "🔵 شحن بكود بطاقة", "charge_account"),
            ("fund_acc", "🔵 تغذية حسابك", "fund_account")
        ]
        for key, text, action in default_btns:
            cursor.execute("INSERT INTO custom_buttons (btn_key, btn_text, btn_action) VALUES (%s, %s, %s) ON CONFLICT (btn_key) DO NOTHING", (key, text, action))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    print("\n✅ تم الاتصال بقاعدة بيانات Neon السحابية بنجاح لمتجر B-Fix Software | AI Store!")

def add_user_if_not_exists(user_id, name, username):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, username, balance, join_date) VALUES (?, ?, ?, ?, ?)", 
                   (user_id, name, username, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def get_maintenance_status():
    status = db_fetch_one("SELECT is_active, custom_message FROM maintenance_mode WHERE id = 1")
    return (status[0] == 1, status[1]) if status else (False, "")

async def check_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin=False):
    is_active, custom_msg = get_maintenance_status()
    if is_active and not is_admin:
        msg = custom_msg or "⚠️ البوت قيد الصيانة حالياً."
        if update.message: await update.message.reply_text(msg, parse_mode='Markdown')
        elif update.callback_query: await update.callback_query.answer(msg, show_alert=True)
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

# ================= (3) الواجهة الرئيسية وأقسام المتجر =================
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
        "✨ ━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 | 𝐀𝐈 𝐒𝐭𝐨𝐫𝐞 ❳ ━━━━━ ✨\n\n"
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
        [InlineKeyboardButton("⚙️ لوحة التحكم للإدارة", callback_data="admin_panel")] if user.id == ADMIN_ID else InlineKeyboardButton("ℹ️ معلومات البوت", callback_data="bot_info")
    ]
    if user.id == ADMIN_ID:
        keyboard[-1].append(InlineKeyboardButton("ℹ️ معلومات", callback_data="bot_info"))

    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='Markdown')

# ================= (4) معالج الأزرار والتفاعلات والطلبات =================
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
        text = f"👤 **ملفك الشخصي:**\n\n▪️ **الاسم:** {user_info[0]}\n▪️ **الآيدي:** `{user_id}`\n▪️ **الرصيد:** `{user_info[1]}` $\n\n✨ **متجر B-Fix Software | AI Store**"
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
            "✨ *شكراً لثقتك في **متجر B-Fix Software | AI Store**.*"
        )
        context.user_data['waiting_receipt'] = True
        context.user_data['payment_method_name'] = data
        await query.message.edit_text(pay_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 الدعم", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="fund_account")]]), parse_mode='Markdown')

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        titles = {
            "digital": "⚡ خدمات شحن الأدوات والبوكسات 🛠️", 
            "subscriptions": "🔵 الاشتراكات الرقمية 🚀", 
            "rentals": "🔧 خدمة إيجار الأدوات 🛠️", 
            "vip": "💎 عروض VIP والخدمات الخاصة ⭐", 
            "free": "🎁 العروض المجانية الحصرية 🆓"
        }
        title = titles.get(category, "🛒 قائمة الخدمات")
        
        services = db_fetch_all("SELECT id, name, price FROM services WHERE category = ?", (category,))
        if not services:
            await query.message.edit_text(f"🚧 لا توجد خدمات في قسم {title} حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))
            return
            
        keyboard = []
        for srv in services:
            stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv[0],))[0]
            status = "🟢 تتوفر" if (stock > 0 or category in ["free", "rentals", "vip"]) else "🔴 نفدت"
            keyboard.append([InlineKeyboardButton(f"▪️ {srv[1]} - {srv[2]}$ ({status})", callback_data=f"srv_{srv[0]}")])
        keyboard.append([InlineKeyboardButton("🔴 رجوع للقائمة الرئيسية", callback_data="main_menu")])
        await query.message.edit_text(f"📑 **{title}:**\n\n👇 اختر الخدمة المطلوبة:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("srv_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT id, name, description, price, duration, category FROM services WHERE id = ?", (srv_id,))
        stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
        text = f"📌 **الخدمة/الأداة:** {srv[1]}\n📝 **الوصف:** {srv[2]}\n⏳ **المدة:** {srv[4]}\n💵 **السعر:** `{srv[3]}` $\n📦 **الكمية المتوفرة:** {stock}\n\n✨ **متجر B-Fix Software | AI Store**"
        
        keyboard = [[InlineKeyboardButton("🟢 شراء / طلب الآن ⚡", callback_data=f"buy_{srv[0]}")], [InlineKeyboardButton("🔴 رجوع للقسم", callback_data=f"show_cat_{srv[5]}")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("buy_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT price, name, category, file_id, description FROM services WHERE id = ?", (srv_id,))
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user_id,))
        
        cat = srv[2]
        price = srv[0]
        srv_name = srv[1]
        
        # (1) فحص الرصيد غير الكافي برساالة فخمة
        if user_info[0] < price and cat != "free":
            deficit = price - user_info[0]
            await query.message.edit_text(
                "🚫 **عـذراً عميلنـا العـزيـز!**\n"
                "رصيدك الحالي لا يغطي قيمة هذا الاشتراك.\n\n"
                f"💳 **سعر الاشتراك:** `{price}` $\n"
                f"💰 **رصيدك الحالي:** `{user_info[0]}` $\n"
                f"⚠️ **المبلغ المتبقي المطلوب:** `{deficit}` $\n\n"
                "🌟 *يرجى شحن حسابك للمضي قدماً والاستمتاع بخدمات متجرنا.*",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🟢 تغذية رصيد الحساب", callback_data="fund_account")],
                    [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]
                ]),
                parse_mode='Markdown'
            )
            return

        # (3) قسم العروض المجانية
        if cat == "free" or price == 0:
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅ مجاني', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            free_content = srv[3] or srv[4] or "لا توجد تفاصيل إضافية"
            free_msg = (
                "🎁 **هـديـتـك المجـانـيـة مـن المتجر!**\n\n"
                "تم تسليم العرض المجاني بنجاح تام ✅\n"
                f"📥 **التفاصيل / الملف:**\n{free_content}\n\n"
                "✨ *نتمنى أن تنال إعجابك، ونتشرف دائماً بخدمتك في **متجر B-Fix Software | AI Store**.*"
            )
            await query.message.edit_text(free_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')
            if srv[3] and srv[3].startswith("AgAC"): # فحص لو كان ملف تليجرام
                try: await context.bot.send_document(chat_id=user_id, document=srv[3], caption="🎁 هدية مجانية من متجر B-Fix Software | AI Store")
                except: pass
            return

        # (5) عروض VIP والخدمات الخاصة
        if cat == "vip":
            if "رقم المتصل" in srv_name or "معرفة" in srv_name:
                context.user_data['waiting_caller_id'] = True
                context.user_data['vip_srv_id'] = srv_id
                context.user_data['vip_price'] = price
                context.user_data['vip_name'] = srv_name
                await query.message.edit_text("📞 **خدمة معرفة هوية/رقم المتصل**\n\nيرجى إرسال **رقم الهاتف** المراد البحث عنه الآن:", parse_mode='Markdown')
                return
            elif "بوت" in srv_name or "تطوير" in srv_name:
                bot_vip_text = (
                    f"💎 **{srv_name}**\n\n"
                    "لطلب وتنسيق تطوير بوت باسمك وحقوقك الخاصة، يرجى التواصل مباشرة عبر زر الواتساب أدناه للتنسيق الشامل:\n\n"
                    "✨ **متجر B-Fix Software | AI Store**"
                )
                await query.message.edit_text(bot_vip_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 تواصل معي عبر الواتساب 💬", url=WHATSAPP_LINK)], [InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]), parse_mode='Markdown')
                return

        # (2) قسم إيجار الأدوات
        if cat == "rentals":
            if user_info[0] < price:
                return # تم التعامل معها بالأعلى
            db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'قيد التجهيز ⏳ إيجار', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            await query.message.edit_text(
                "⏳ **تـم تـسجيـل طـلب الإيـجـار بـنـجـاح!**\n\n"
                "🕒 جاري تجهيز بيانات الدخول (الإيميل وكلمة المرور) الخاصة بأداة الإيجار من سيرفر الأداة.\n"
                "⏱️ يرجى الانتظار من **5 إلى 10 دقائق** كحد أقصى، وسنرسل لك بيانات الحساب فوراً هنا في البوت!\n\n"
                "✨ *شكراً لثقتك في **متجر B-Fix Software | AI Store**.*",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]),
                parse_mode='Markdown'
            )
            # إشعار المدير
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔔 **طلب إيجار جديد قيد الانتظار!**\n▪️ العميل: {user_info[1]} (`{user_id}`)\n▪️ الأداة: {srv_name}\n\nيرجى سحب البيانات وتفعيلها للعميل.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 إرسال بيانات الإيجار للعميل", callback_data=f"send_rental_{user_id}_{srv_id}")]])
            )
            return

        # (1) شحن الأدوات والبوكسات والاشتراكات
        stock_key = db_fetch_one("SELECT id, key_text FROM product_keys WHERE service_id = ? AND is_sold = 0 LIMIT 1", (srv_id,))
        if cat in ["digital", "subscriptions"] and stock_key:
            new_balance = user_info[0] - price
            db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            db_execute("UPDATE product_keys SET is_sold = 1 WHERE id = ?", (stock_key[0],))
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            await query.message.edit_text("✅ **تم الشراء بنجاح!**\n\n🎁 تفاصيل اشتراكك في الرسالة التالية 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]))
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **تفاصيل اشتراكك:**\n\n{stock_key[1]}\n\n✨ **متجر B-Fix Software | AI Store**")
            return

        # إذا لم تتوفر مفاتيح مخزنة، يطلب الإيميل من العميل للتفعيل اليدوي (5 إلى 10 دقائق)
        context.user_data['pending_srv_id'] = srv_id
        context.user_data['pending_price'] = price
        context.user_data['pending_name'] = srv_name
        await query.message.edit_text(
            "📧 **تـفـعـيـل الاشـتـراك**\n\n"
            f"أهلاً بك! لإتمام تفعيل أداة `{srv_name}`، يرجى إرسال **الإيميل (البريد الإلكتروني)** الذي قمت بالتسجيل به في الموقع الرسمي للأداة أسفل هذه الرسالة 👇\n\n"
            "✨ **متجر B-Fix Software | AI Store**",
            parse_mode='Markdown'
        )
        context.user_data['waiting_email_input'] = True

    elif data.startswith("send_rental_"):
        if user_id != ADMIN_ID: return
        parts = data.split("_")
        target_user = parts[2]
        srv_id = parts[3]
        context.user_data['admin_target_rental_user'] = target_user
        context.user_data['admin_target_rental_srv'] = srv_id
        await query.message.reply_text("✍️ أرسل الآن بيانات الإيجار (الإيميل وكلمة المرور) في رسالة واحدة ليتم إرسالها للعميل فوراً:")

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        text = "📦 لا توجد طلبات سابقة." if not orders else "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        info_msg = "🌟 **━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 | 𝐀𝐈 𝐒𝐭𝐨𝐫𝐞 ❳ ━━━━━** 🌟\n\n🤖 نظام إدارة متجرك الآلي المتطور للخدمات الرقمية وشحن الأدوات."
        await query.message.edit_text(info_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "admin_panel":
        if user_id != ADMIN_ID: return
        admin_keyboard = [
            [InlineKeyboardButton("➕ إضافة خدمات متعددة (دفعة واحدة)", callback_data="admin_add_batch")],
            [InlineKeyboardButton("⚙️ تعيين رسالة صيانة البوت", callback_data="admin_set_maintenance")],
            [InlineKeyboardButton("🔴 العودة للقائمة الرئيسية", callback_data="main_menu")]
        ]
        await query.message.edit_text("👑 **لوحة تحكم المدير - متجر B-Fix Software | AI Store**\n\nاختر الإجراء المطلوب:", reply_markup=InlineKeyboardMarkup(admin_keyboard), parse_mode='Markdown')

    elif data == "admin_set_maintenance":
        if user_id != ADMIN_ID: return
        context.user_data['waiting_maintenance_msg'] = True
        await query.message.edit_text("✍️ أرسل الآن **نص رسالة الصيانة المخصصة** التي تريد ظهورها للعملاء:\n(أو أرسل /cancel للإلغاء)")

    elif data == "admin_add_batch":
        if user_id != ADMIN_ID: return
        categories = [
            [InlineKeyboardButton("⚡ شحن الأدوات والبوكسات", callback_data="batch_cat_digital")],
            [InlineKeyboardButton("🔵 الاشتراكات", callback_data="batch_cat_subscriptions")],
            [InlineKeyboardButton("🔧 إيجار الأدوات", callback_data="batch_cat_rentals")],
            [InlineKeyboardButton("💎 عروض VIP", callback_data="batch_cat_vip")],
            [InlineKeyboardButton("🎁 عروض مجانية", callback_data="batch_cat_free")],
            [InlineKeyboardButton("🔴 رجوع", callback_data="admin_panel")]
        ]
        await query.message.edit_text("📂 اختر **القسم** الذي تريد إضافة الخدمات إليه دفعة واحدة:", reply_markup=InlineKeyboardMarkup(categories))

    elif data.startswith("batch_cat_"):
        if user_id != ADMIN_ID: return
        cat_chosen = data.replace("batch_cat_", "")
        context.user_data['batch_category'] = cat_chosen
        context.user_data['waiting_batch_services'] = True
        await query.message.edit_text(
            "📝 **إضافة خدمات متعددة دفعة واحدة:**\n\n"
            "أرسل الخدمات الآن بالصيغة التالية (كل خدمة في سطر أو مفصولة بـ | أو بالشكل المناسب):\n"
            "مثال:\n"
            "اسم الخدمة | الوصف | مدة التسليم | مدة الاشتراك | السعر\n\n"
            "أو اكتبها بوضوح وسيقوم البوت بحفظها فوراً في القسم المحدد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 إلغاء", callback_data="admin_panel")]])
        )

    elif data.startswith("approve_fund_"):
        if user_id != ADMIN_ID: return
        parts = data.split("_")
        target_user = int(parts[2])
        context.user_data['admin_approving_user'] = target_user
        await query.message.reply_text("✍️ أرسل الآن **المبلغ المراد شحنه** لهذا العميل (رقم فقط):")

    elif data == "main_menu":
        await start_command(update, context)

# ================= (5) معالج الرسائل النصية والإشعارات الإدارية =================
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""

    if user.id != ADMIN_ID:
        if not await check_maintenance(update, context): return
        if not await enforce_subscription(update, context): return

    # (7) إعداد رسالة الصيانة المخصصة من المدير
    if user.id == ADMIN_ID and context.user_data.get('waiting_maintenance_msg'):
        db_execute("UPDATE maintenance_mode SET is_active = 1, custom_message = ? WHERE id = 1", (text,))
        context.user_data.clear()
        await update.message.reply_text(f"✅ **تم تفعيل وضع الصيانة بنجاح!**\nالرسالة المخصصة المعروضة للعملاء:\n\n{text}")
        return

    # (6) لوحة التحكم: إضافة خدمات متعددة دفعة واحدة
    if user.id == ADMIN_ID and context.user_data.get('waiting_batch_services'):
        cat = context.user_data.get('batch_category', 'digital')
        lines = text.split("\n")
        added_count = 0
        for line in lines:
            if not line.strip(): continue
            parts = [p.strip() for p in line.split("|")]
            s_name = parts[0] if len(parts) > 0 else "خدمة جديدة"
            s_desc = parts[1] if len(parts) > 1 else "بدون وصف"
            s_delivery = parts[2] if len(parts) > 2 else "5 إلى 10 دقائق"
            s_duration = parts[3] if len(parts) > 3 else "دائم"
            try:
                s_price = float(parts[4]) if len(parts) > 4 else 0.0
            except:
                s_price = 0.0
            
            db_execute("INSERT INTO services (name, description, price, duration, delivery_time, category) VALUES (?, ?, ?, ?, ?, ?)",
                       (s_name, s_desc, s_price, s_duration, s_delivery, cat))
            added_count += 1
            
        context.user_data.clear()
        await update.message.reply_text(f"✅ **تمت إضافة عدد ({added_count}) خدمة بنجاح في القسم المطلوب!**\n\n✨ **متجر B-Fix Software | AI Store**")
        return

    # إدخال المبلغ لشحن رصيد العميل بعد إرسال الإيصال
    if user.id == ADMIN_ID and context.user_data.get('admin_approving_user'):
        try:
            amount = float(text)
            target_user = context.user_data.get('admin_approving_user')
            db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_user))
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم إضافة مبلغ `{amount}` $ للعميل بنجاح!")
            
            # رسالة نجاح فخمة للعميل
            success_msg = (
                "🎉 **تـم التأكـد مـن بـيـانـات الدفـع بـنـجـاح!**\n\n"
                "🟢 **الحالة:** مقبول ✅\n"
                f"💰 تم شحن حسابك وإضافة مبلغ `{amount}` $ بنجاح إلى رصيدك.\n\n"
                "✨ *يمكنك الآن الاستمتاع بجميع خدمات المتجر بكل سهولة. شكراً لتعاملكم معنا في **متجر B-Fix Software | AI Store**.*"
            )
            await context.bot.send_message(chat_id=target_user, text=success_msg, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في المبلغ المدخل: {e}")
        return

    # إرسال بيانات الإيجار من المدير للعميل
    if user.id == ADMIN_ID and context.user_data.get('admin_target_rental_user'):
        target_user = context.user_data.get('admin_target_rental_user')
        context.user_data.clear()
        
        rental_delivery_msg = (
            "🔑 **بيـانـات حـسـاب الإيـجـار الجـاهـز:**\n\n"
            f"📥 **بيانات الدخول:**\n{text}\n\n"
            "⚠️ *يرجى عدم تغيير بيانات الحساب لكي لا يلغى الإيجار. نتمنى لك عملاً موفقاً!*\n\n"
            "✨ — **متجر B-Fix Software | AI Store**"
        )
        await context.bot.send_message(chat_id=target_user, text=rental_delivery_msg, parse_mode='Markdown')
        await update.message.reply_text("✅ تم إرسال بيانات الإيجار للعميل بنجاح!")
        return

    # (1) استقبال الإيميل للشحن اليدوي (5 إلى 10 دقائق)
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
        await update.message.reply_text(
            "✅ **تـم اسـتـلام طـلـبك بـنـجـاح!**\n\n"
            "⏳ يرجى الانتظار من **5 إلى 10 دقائق** كحد أقصى، ريثما يقوم فريق العمل بتجهيز وتفعيل اشتراكك على الإيميل الذي أرسلته.\n"
            "سنرسل لك إشعاراً فور الانتهاء! 🌹\n\n"
            "✨ **متجر B-Fix Software | AI Store**",
            parse_mode='Markdown'
        )
        
        # إشعار المدير
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **طلب تفعيل أداة جديد!**\n▪️ العميل: {user_info[1]} (`{user.id}`)\n▪️ الأداة: {srv_name}\n▪️ الإيميل المرسل: `{email}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 إرسال إشعار التفعيل للعميل", callback_data=f"send_activation_{user.id}_{srv_id}")]])
        )
        return

    # (5) استقبال رقم الهاتف لخدمة معرفة هوية المتصل (VIP)
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
            "جاري فحص الرقم والبحث عنه، وسيتم إرسال النتيجة فور الحصول على الاسم ومعلومات المتصل من النظام أو شركة الاتصالات.\n\n"
            "✨ *شكراً لثقتك في **متجر B-Fix Software | AI Store**.*",
            parse_mode='Markdown'
        )
        
        # إشعار المدير بالطلب والرقم
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💎 **طلب VIP (معرفة هوية متصل):**\n▪️ العميل: {user_info[1]} (`{user.id}`)\n▪️ الرقم المطلوب بحثه: `{phone_number}`"
        )
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
        await update.message.reply_text(f"✅ **تم الشحن بنجاح!**\n💰 القيمة: `{card[0]}` $\n💵 رصيدك الجديد: `{new_balance}` $\n\n✨ **متجر B-Fix Software | AI Store**", parse_mode='Markdown')
        return

    # استقبال صورة سند التحويل من العميل لتغذية الرصيد
    if update.message.photo and context.user_data.get('waiting_receipt'):
        photo_file = update.message.photo[-1].file_id
        method = context.user_data.get('payment_method_name', 'غير محدد')
        context.user_data.clear()
        
        await update.message.reply_text(
            "⏳ **تـم اسـتـلام إيـصـال الدفـع بـنـجـاح!**\n\n"
            "جاري مراجعة السند من قبل الإدارة وسيتم شحن حسابك فوراً.\n\n"
            "✨ *شكراً لثقتك في **متجر B-Fix Software | AI Store**.*",
            parse_mode='Markdown'
        )
        
        # إرسال الصورة وإشعار المدير مع زر الموافقة
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file,
            caption=f"📥 **إيصال تحويل جديد للشحن!**\n▪️ العميل: {user.first_name} (`{user.id}`)\n▪️ الوسيلة: `{method}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 قبول وشحن الرصيد", callback_data=f"approve_fund_{user.id}")]])
        )
        return

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message: await update.message.reply_text("🚫 تم الإلغاء بنجاح.")
    return ConversationHandler.END

# ================= (6) التشغيل الرئيسي =================
def main():
    init_db()
    try: urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook").read()
    except: pass
        
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))

    print("\n🚀 متجر B-Fix Software | AI Store يعمل الآن بسرعة فائقة ومتصل بقاعدة بيانات Neon السحابية!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
