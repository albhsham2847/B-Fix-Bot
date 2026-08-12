# ==============================================================================
# |   B-Fix Smart Bot - النسخة الاحترافية الكاملة والمحدثة                    |
# |   (العروض المجانية، عروض VIP، نظام صيانة متكامل، إدارة كميات دقيقة،        |
# |    لوحة تحكم شاملة، حذف الإشعارات، وقاعدة بيانات آمنة 100%)              |
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

# إخفاء التحذيرات
warnings.filterwarnings("ignore", category=PTBUserWarning)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- سيرفر ويب مصغر لإبقاء الاستضافة تعمل 24/7 ---
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
BOT_TOKEN = "ضع_التوكن_الخاص_بك_هنا"  # ⬅️ ضع التوكن هنا
ADMIN_ID = 00000000  # ⬅️ ضع الآيدي الخاص بك كرقْم (Integer)

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
DB_NAME = "bfix_store.db"

# --- ألوان الأزرار (رموز تعبيرية) ---
COLOR_PRIMARY = "🔵"
COLOR_SUCCESS = "🟢"
COLOR_DANGER = "🔴"
COLOR_WARNING = "⚠️"
COLOR_INFO = "ℹ️"
COLOR_ACTION = "✨"

# --- حالات المحادثة الإدارية ---
(ADMIN_USER_ID, ADMIN_AMOUNT, ADMIN_SEARCH, ADMIN_BROADCAST, ADMIN_SRV_CATEGORY,
 ADMIN_SRV_NAME, ADMIN_SRV_DESC, ADMIN_SRV_PRICE, ADMIN_SRV_DURATION, 
 ADMIN_NEW_PRICE, ADMIN_CARD_CODE, ADMIN_CARD_AMOUNT, ADMIN_STOCK_KEY,
 ADMIN_MAINTENANCE_TEXT) = range(14)

USER_CARD_CODE = 20

# ================= (2) نظام قاعدة البيانات الآمن والمحدث =================
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
        
        # 1. جدول المستخدمين
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            name TEXT, 
            balance REAL DEFAULT 0.0, 
            join_date TEXT
        )''')
        
        # 2. جدول الخدمات
        conn.execute('''CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            name TEXT, 
            description TEXT, 
            price REAL, 
            duration TEXT, 
            category TEXT DEFAULT 'digital',
            quantity INTEGER DEFAULT 0 
        )''')
        
        try: conn.execute("ALTER TABLE services ADD COLUMN category TEXT DEFAULT 'digital'")
        except: pass
        try: conn.execute("ALTER TABLE services ADD COLUMN quantity INTEGER DEFAULT 0")
        except: pass
        
        # 3. جدول الطلبات
        conn.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            service_id INTEGER, 
            status TEXT, 
            order_date TEXT
        )''')
        
        # 4. جدول بطاقات الشحن
        conn.execute('''CREATE TABLE IF NOT EXISTS cards (
            code TEXT PRIMARY KEY, 
            amount REAL, 
            is_used INTEGER DEFAULT 0
        )''')
        
        # 5. جدول مفاتيح/أكواد المنتجات
        conn.execute('''CREATE TABLE IF NOT EXISTS product_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            service_id INTEGER, 
            key_text TEXT, 
            is_sold INTEGER DEFAULT 0
        )''')
        
        # 6. جدول وضع الصيانة
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_mode (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_active INTEGER DEFAULT 0,
            custom_message TEXT
        )''')
        
        # 7. جدول آخر إشعار جماعي
        conn.execute('''CREATE TABLE IF NOT EXISTS last_broadcast (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT
        )''')
        
        chk_main = conn.execute("SELECT id FROM maintenance_mode WHERE id = 1").fetchone()
        if not chk_main:
            conn.execute("INSERT INTO maintenance_mode (id, is_active) VALUES (1, 0)")
            
        conn.commit()
    print("\n✅ تم تهيئة قاعدة البيانات بنجاح!")

def add_user_if_not_exists(user_id, name):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, balance, join_date) VALUES (?, ?, ?, ?)", 
                   (user_id, name, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

# ================= (2.5) فحص وضع الصيانة =================
def is_bot_under_maintenance():
    status = db_fetch_one("SELECT is_active FROM maintenance_mode WHERE id = 1")
    return status[0] == 1 if status else False

def get_maintenance_message():
    msg = db_fetch_one("SELECT custom_message FROM maintenance_mode WHERE id = 1")
    return msg[0] if msg and msg[0] else None

async def check_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin=False):
    if is_bot_under_maintenance() and not is_admin:
        custom_msg = get_maintenance_message()
        if custom_msg:
            final_text = f"⚙️ **{custom_msg}** 🛠️"
        else:
            final_text = (
                "⚠️ **نعتذر منكم، البوت قيد الصيانة حالياً.** 🛠️\n\n"
                "يرجى المحاولة مرة أخرى لاحقاً. شكراً لتفهمكم! 🙏"
            )
        if update.message:
            await update.message.reply_text(final_text, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.answer("⚙️ البوت تحت الصيانة", show_alert=True)
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
        "في متجرك الآلي المطور للخدمات الرقمية والاشتراكات 🚀\n\n"
        "🛒 ❲ يرجى اختيار القسم المطلوب من القائمة ❳ 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = [
        [InlineKeyboardButton(f"{COLOR_SUCCESS} الخدمات الرقمية {COLOR_ACTION}", callback_data="show_cat_digital"),
         InlineKeyboardButton(f"{COLOR_SUCCESS} الاشتراكات {COLOR_ACTION}", callback_data="show_cat_subscriptions")],
        [InlineKeyboardButton(f"{COLOR_SUCCESS} خدمة إيجار الأدوات 🛠️", callback_data="show_cat_rentals")],
        [InlineKeyboardButton(f"{COLOR_SUCCESS} عروض VIP الماسي ⭐", callback_data="show_cat_vip"),
         InlineKeyboardButton(f"{COLOR_SUCCESS} عروض مجانية حصرية 🆓", callback_data="show_cat_free")],
        [InlineKeyboardButton(f"{COLOR_INFO} سجل طلباتي 🔄", callback_data="my_orders"),
         InlineKeyboardButton(f"{COLOR_INFO} حسابي ⚡", callback_data="my_profile")],
        [InlineKeyboardButton(f"{COLOR_PRIMARY} شحن الرصيد بكود", callback_data="charge_account"),
         InlineKeyboardButton(f"{COLOR_PRIMARY} تغذية حسابك", callback_data="fund_account")],
        [InlineKeyboardButton(f"{COLOR_INFO} واتساب 🌐", url=WHATSAPP_LINK),
         InlineKeyboardButton(f"{COLOR_INFO} الدعم 🛠️", url=SUPPORT_LINK)],
        [InlineKeyboardButton(f"{COLOR_WARNING} معلومات البوت ℹ️", callback_data="bot_info")]
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
        text = f"👤 **ملفك الشخصي:**\n\n▪️ **الاسم:** {user_info[0]}\n▪️ **الآيدي:** `{user_id}`\n▪️ **الرصيد:** `{user_info[1]}` $\n\nلزيادة رصيدك اضغط تغذية حسابك."
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_SUCCESS} تغذية حسابك", callback_data="fund_account")], [InlineKeyboardButton(f"{COLOR_DANGER} رجوع للقائمة الرئيسية 🔄", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "charge_account":
        await query.message.edit_text("💳 أرسل **كود البطاقة** الآن (أو أرسل /cancel للإلغاء):", parse_mode='Markdown')
        context.user_data['waiting_card'] = True

    elif data == "fund_account":
        payment_text = (
            "💳 **اختر وسيلة الدفع المناسبة لك لتغذية حسابك:**\n\n"
            "يرجى النقر على إحدى الطرق أدناه لعرض تفاصيل وحسابات التحويل المعتمدة 👇"
        )
        payment_keyboard = [
            [InlineKeyboardButton("🔹 محفظة جيب", callback_data="pay_jeep"),
             InlineKeyboardButton("🔹 جوالي", callback_data="pay_jawali")],
            [InlineKeyboardButton("🔹 وان كاش", callback_data="pay_onecash"),
             InlineKeyboardButton("🏦 بنك الكريمي", callback_data="pay_kuraimi")],
            [InlineKeyboardButton("🟡 Binance", callback_data="pay_binance"),
             InlineKeyboardButton("💳 VISA", callback_data="pay_visa")],
            [InlineKeyboardButton("🟢 شحن بكود", callback_data="charge_account")],
            [InlineKeyboardButton(f"{COLOR_DANGER} رجوع للقائمة الرئيسية 🔄", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_text, reply_markup=InlineKeyboardMarkup(payment_keyboard), parse_mode='Markdown')

    elif data.startswith("pay_"):
        payment_details = (
            "💳 **طرق الدفع المتاحة لدينا**\n\n"
            "📱 **المحافظ الإلكترونية**\n"
            "🔹 محفظة جيب: `580300`\n"
            "🔹 وان كاش: `178109713`\n"
            "🔹 جوالي: `777728478`\n\n"
            "🏦 **التحويل البنكي – بنك الكريمي**\n\n"
            "🇾🇪 الحساب بالريال اليمني: `3204168937`\n"
            "🇸🇦 الحساب بالريال السعودي: `3204433991`\n"
            "💵 الحساب بالدولار الأمريكي: `3191718649`\n\n"
            "🌍 **طرق الدفع العالمية**\n\n"
            "🟡 **Binance ID:**\n`1063050653`\n"
            "💳 **VISA:**\n`4909800019663092`\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "✅ **بعد إتمام عملية الدفع**\n\n"
            "يرجى إرسال صورة أو إشعار التحويل عبر واتساب مع توضيح:\n"
            "• **الاسم**\n• **الخدمة المطلوبة**\n• **المبلغ المحول**\n• **وسيلة الدفع المستخدمة**\n\n"
            "سيتم مراجعة عملية الدفع وتفعيل طلبك في أسرع وقت ممكن.\n\n"
            "🔒 دفع آمن • تفعيل سريع • خدمة موثوقة"
        )
        back_kb = [
            [InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)],
            [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")],
            [InlineKeyboardButton(f"{COLOR_DANGER} القائمة الرئيسية", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_details, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode='Markdown')

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        if category == "digital": title = "💠 الخدمات الرقمية (Software & Tools) ✨"
        elif category == "subscriptions": title = "🔵 الاشتراكات (AI & Premium) 🚀"
        elif category == "rentals": title = "🔧 خدمة إيجار الأدوات 🛠️"
        elif category == "vip": title = "💎 عروض VIP الماسي ⭐"
        elif category == "free": title = "🎁 العروض المجانية الحصرية 🆓"
        else: title = "🛒 قائمة الخدمات"
        
        services = db_fetch_all("SELECT id, name, price FROM services WHERE category = ?", (category,))
        if not services:
            await query.message.edit_text(f"🚧 لا توجد خدمات في قسم {title} حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} العودة للقائمة 🔄", callback_data="main_menu")]]))
            return
            
        keyboard = []
        for srv in services:
            stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv[0],))[0]
            status = "🟢 تتوفر" if stock > 0 else "🔴 نفدت"
            keyboard.append([InlineKeyboardButton(f"▪️ {srv[1]} - {srv[2]}$ ({status})", callback_data=f"srv_{srv[0]}")])
        keyboard.append([InlineKeyboardButton(f"{COLOR_DANGER} العودة للقائمة 🔄", callback_data="main_menu")])
        await query.message.edit_text(f"📑 **{title}:**\n\n👇 اختر الخدمة لعرض التفاصيل:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("srv_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT id, name, description, price, duration, category FROM services WHERE id = ?", (srv_id,))
        stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
        text = f"📌 **الخدمة:** {srv[1]}\n📝 **الوصف:** {srv[2]}\n⏳ **المدة:** {srv[4]}\n💵 **السعر:** `{srv[3]}` $\n📦 **الكمية المتوفرة:** {stock}"
        
        keyboard = [[InlineKeyboardButton(f"{COLOR_SUCCESS} شراء الآن ⚡", callback_data=f"buy_{srv[0]}")], [InlineKeyboardButton(f"{COLOR_DANGER} رجوع للقسم 🔄", callback_data=f"show_cat_{srv[5]}")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("buy_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT price, name, category FROM services WHERE id = ?", (srv_id,))
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user_id,))
        stock_key = db_fetch_one("SELECT id, key_text FROM product_keys WHERE service_id = ? AND is_sold = 0 LIMIT 1", (srv_id,))
        
        if not stock_key and srv[2] != "free":
            out_of_stock_msg = "❌ **عذراً، لقد نفدت الكمية (الأكواد) لهذه الخدمة للتو!**\n\nيرجى المحاولة لاحقاً أو مراسلة الدعم الفني."
            await query.message.edit_text(out_of_stock_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} العودة للخدمات", callback_data="main_menu")]]), parse_mode='Markdown')
            return
            
        # التحقق من الرصيد أو السعر المجاني
        is_free = (srv[2] == "free" or srv[0] == 0)
        if is_free or user_info[0] >= srv[0]:
            if not is_free:
                new_balance = user_info[0] - srv[0]
                db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            
            if stock_key:
                db_execute("UPDATE product_keys SET is_sold = 1 WHERE id = ?", (stock_key[0],))
                key_content = stock_key[1]
            else:
                key_content = "🎁 تم تفعيل عرضك المجاني بنجاح!"

            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            success_msg = "✅ **تم الشراء بنجاح!**\n\n🎁 تم إرسال تفاصيل طلبك في الرسالة التالية لتتمكن من نسخها بسهولة 👇"
            await query.message.edit_text(success_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')
            
            await context.bot.send_message(chat_id=user_id, text=key_content)
            await context.bot.send_message(chat_id=user_id, text="🌟 **شكراً لاستخدامك بوت Bfixsoftware** 🌟", parse_mode='Markdown')
            
            admin_msg = f"🔔 **طلب/شراء جديد!**\n👤 العميل: {user_info[1]}\n🆔 `{user_id}`\n🛒 {srv[1]}\n💵 {srv[0]}$\n\n👇 المحتوى المباع/المسلم:"
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode='Markdown')
            await context.bot.send_message(chat_id=ADMIN_ID, text=key_content)
        else:
            await query.answer("❌ رصيدك غير كافٍ لإتمام العملية!", show_alert=True)

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        if not orders: text = "📦 لا توجد طلبات."
        else: text = "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        await query.message.edit_text("🤖 متجر B-Fix الذكي للخدمات والاشتراكات الرقمية.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} رجوع", callback_data="main_menu")]]))

    elif data == "main_menu":
        await start_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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

# ================= (4) لوحة تحكم المشرف المتقدمة =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return 
    
    m_status = "🟢 (مفعل)" if is_bot_under_maintenance() else "🔴 (معطل)"
    text = "👑 **لوحة تحكم المطور والمدير**"
    
    keyboard = [
        [InlineKeyboardButton(f"{COLOR_SUCCESS} إضافة رصيد 🔵", callback_data="adm_add_bal"), InlineKeyboardButton(f"{COLOR_DANGER} خصم رصيد 🔴", callback_data="adm_sub_bal")],
        [InlineKeyboardButton(f"{COLOR_SUCCESS} إنشاء كود شحن 🎟", callback_data="adm_new_card"), InlineKeyboardButton(f"{COLOR_PRIMARY} بحث عن مستخدم 🔎", callback_data="adm_search")],
        [InlineKeyboardButton(f"{COLOR_SUCCESS} إدارة الخدمات والأقسام والأكواد 🛠️", callback_data="adm_srv_menu")],
        [InlineKeyboardButton(f"{COLOR_PRIMARY} إشعار جماعي 📢", callback_data="adm_broadcast"), InlineKeyboardButton(f"{COLOR_DANGER} حذف آخر إشعار 🗑️", callback_data="adm_del_broadcast")],
        [InlineKeyboardButton(f"⚙️ وضع الصيانة: {m_status}", callback_data="adm_toggle_main")],
        [InlineKeyboardButton(f"{COLOR_INFO} إحصائيات المتجر 📊", callback_data="adm_stats")]
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
            await query.answer("✅ تم إيقاف وضع الصيانة بنجاح.", show_alert=True)
            await admin_panel(update, context)
        else:
            # طلب إدخال رسالة صيانة مخصصة أو تفعيل مباشر
            keyboard = [
                [InlineKeyboardButton("🟢 تفعيل مباشر بالرسالة الافتراضية", callback_data="main_on_def")],
                [InlineKeyboardButton("✍️ تفعيل مع رسالة صيانة مخصصة فخمة", callback_data="main_on_custom")],
                [InlineKeyboardButton("🔴 رجوع", callback_data="adm_main")]
            ]
            await query.message.edit_text("⚙️ **إعدادات وضع الصيانة:**\n\nكيف ترغب في تفعيل الصيانة للعملاء؟", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "main_on_def":
        db_execute("UPDATE maintenance_mode SET is_active = 1, custom_message = NULL WHERE id = 1", ())
        await query.message.edit_text("✅ تم تفعيل وضع الصيانة بنجاح بالرسالة الافتراضية.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للوحة التحكم", callback_data="adm_main")]]))

    elif data == "main_on_custom":
        await query.message.edit_text("✍️ أرسل الآن **نص رسالة الصيانة** الفخم الذي سيظهر للعملاء عند محاولة المراسلة:\n(أرسل /cancel للإلغاء)", parse_mode='Markdown')
        context.user_data['waiting_main_text'] = True
        return

    elif data == "adm_del_broadcast":
        last_b = db_fetch_one("SELECT content FROM last_broadcast ORDER BY id DESC LIMIT 1")
        if not last_b:
            await query.answer("❌ لا يوجد إشعار سابق لحذفه!", show_alert=True)
            return
        # ملاحظة: التيليجرام لا يسمح بحذف رسالة تم إرسالها لكل مستخدم على حدة بعد إرسالها بالكامل، ولكن يمكننا حذف السجل وتنبيه المشرف
        db_execute("DELETE FROM last_broadcast", ())
        await query.answer("✅ تم مسح سجل آخر إشعار جماعي من قاعدة البيانات بنجاح.", show_alert=True)
        await admin_panel(update, context)

    elif data == "adm_stats":
        u_count = db_fetch_one("SELECT COUNT(*) FROM users")[0]
        o_count = db_fetch_one("SELECT COUNT(*) FROM orders")[0]
        k_count = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE is_sold = 0")[0]
        text = f"📊 **إحصائيات المتجر:**\n\n👥 إجمالي المستخدمين: `{u_count}`\n📦 إجمالي الطلبات: `{o_count}`\n🔑 الأكواد المتبقية بالمخزون: `{k_count}`"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{COLOR_DANGER} رجوع لوحة التحكم", callback_data="adm_main")]]), parse_mode='Markdown')

    elif data == "adm_srv_menu":
        keyboard = [
            [InlineKeyboardButton("🟢 إضافة خدمة جديدة (في أي قسم)", callback_data="adm_add_srv")],
            [InlineKeyboardButton("🔵 إضافة/شحن كمية أكواد لخدمة", callback_data="adm_stock_list")],
            [InlineKeyboardButton("🔵 تعديل سعر خدمة", callback_data="adm_edit_prc_list")],
            [InlineKeyboardButton("🔴 حذف خدمة", callback_data="adm_del_srv_list")],
            [InlineKeyboardButton(f"{COLOR_DANGER} رجوع لوحة التحكم", callback_data="adm_main")]
        ]
        await query.message.edit_text("📦 **إدارة الخدمات والأقسام:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data in ["adm_del_srv_list", "adm_edit_prc_list", "adm_stock_list"]:
        services = db_fetch_all("SELECT id, name, category FROM services")
        if not services:
            await query.message.edit_text("لا توجد خدمات مضافة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))
            return
        if data == "adm_del_srv_list": pref, action = "delsrv_", "حذف"
        elif data == "adm_edit_prc_list": pref, action = "editprc_", "تعديل سعر"
        else: pref, action = "addstock_", "إضافة كمية أكواد لـ"
        
        keyboard = []
        for s in services:
            keyboard.append([InlineKeyboardButton(f"▪️ {s[1]} ({s[2]})", callback_data=f"{pref}{s[0]}")])
        
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")])
        await query.message.edit_text(f"اختر الخدمة لـ {action}:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("delsrv_"):
        srv_id = int(data.split("_")[1])
        db_execute("DELETE FROM services WHERE id = ?", (srv_id,))
        db_execute("DELETE FROM product_keys WHERE service_id = ?", (srv_id,))
        await query.message.edit_text("✅ تم حذف الخدمة وجميع أكوادها بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 إدارة الخدمات", callback_data="adm_srv_menu")]]))

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
        await query.message.edit_text("🔍 أرسل آيدي (ID) المستخدم للبحث عنه:\n(أرسل /cancel للإلغاء)")
        return ADMIN_SEARCH
    elif data == "adm_broadcast":
        await query.message.edit_text("📢 أرسل نص الإشعار الجماعي لجميع العملاء:\n(أرسل /cancel للإلغاء)")
        return ADMIN_BROADCAST
    elif data == "adm_add_srv":
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💠 الخدمات الرقمية (Software)", callback_data="cat_digital")],
            [InlineKeyboardButton("🔵 الاشتراكات (AI & Premium)", callback_data="cat_subscriptions")],
            [InlineKeyboardButton("🔧 خدمة إيجار الأدوات", callback_data="cat_rentals")],
            [InlineKeyboardButton("💎 عروض VIP الماسي ⭐", callback_data="cat_vip")],
            [InlineKeyboardButton("🎁 العروض المجانية الحصرية 🆓", callback_data="cat_free")],
            [InlineKeyboardButton("🔴 إلغاء", callback_data="cat_cancel")]
        ])
        await query.message.edit_text("➕ **إضافة خدمة جديدة:**\n\nأين تريد وضع هذه الخدمة؟ اختر القسم المناسب:", reply_markup=markup, parse_mode='Markdown')
        return ADMIN_SRV_CATEGORY
    elif data.startswith("editprc_"):
        context.user_data['edit_id'] = int(data.split("_")[1])
        await query.message.edit_text("💵 أرسل السعر الجديد بالأرقام:\n(أرسل /cancel للإلغاء)")
        return ADMIN_NEW_PRICE
    elif data == "adm_new_card":
        await query.message.edit_text("🎟 أرسل كود البطاقة (مثال: B-10):\n(أرسل /cancel للإلغاء)")
        return ADMIN_CARD_CODE
    elif data.startswith("addstock_"):
        context.user_data['stock_id'] = int(data.split("_")[1])
        msg = (
            "🔑 **إضافة كمية أكواد / حسابات للخدمة:**\n\n"
            "أرسل الأكواد الآن.\n"
            "*(ملاحظة: يمكنك إرسال كود واحد، أو عدة أكواد مفصول بينها بـ `===` لتقسيم الكمية تلقائياً)*\n\n"
            "(أرسل /cancel للإلغاء)"
        )
        await query.message.edit_text(msg, parse_mode='Markdown')
        return ADMIN_STOCK_KEY

async def adm_rx_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cat_cancel":
        await query.message.edit_text("🚫 تم الإلغاء بنجاح.")
        return ConversationHandler.END
        
    context.user_data['s_cat'] = query.data.replace("cat_", "")
    await query.message.edit_text("📝 ممتاز. أرسل الآن **اسم** الخدمة أو العرض:\n(أرسل /cancel للإلغاء)")
    return ADMIN_SRV_NAME

async def adm_rx_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ أرقام فقط. أعد إرسال الآيدي أو /cancel")
        return ADMIN_USER_ID
    user = db_fetch_one("SELECT name, balance FROM users WHERE user_id = ?", (int(update.message.text),))
    if not user:
        await update.message.reply_text("❌ مستخدم غير مسجل في البوت. أعد الإرسال أو /cancel")
        return ADMIN_USER_ID
    context.user_data['target'] = int(update.message.text)
    await update.message.reply_text(f"✅ العميل: {user[0]} | رصيده الحالي: {user[1]}$\n✍️ أرسل المبلغ المراد إضافته أو خصمه:")
    return ADMIN_AMOUNT

async def adm_rx_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amt = float(update.message.text)
    except:
        await update.message.reply_text("❌ يرجى إرسال أرقام صحيحة أو عشرية فقط. أعد الإرسال:")
        return ADMIN_AMOUNT
    if context.user_data['action'] == "adm_sub_bal": amt = -amt
    db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, context.user_data['target']))
    await update.message.reply_text("✅ تم تحديث رصيد العميل بنجاح.")
    return ConversationHandler.END

async def adm_rx_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db_fetch_one("SELECT name, balance, join_date FROM users WHERE user_id = ?", (int(update.message.text),))
    if user: await update.message.reply_text(f"👤 الاسم: {user[0]}\n💰 الرصيد: {user[1]}$\n📅 تاريخ الانضمام: {user[2]}")
    else: await update.message.reply_text("❌ المستخدم غير موجود.")
    return ConversationHandler.END

async def adm_rx_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    db_execute("INSERT INTO last_broadcast (content) VALUES (?)", (content,))
    users = db_fetch_all("SELECT user_id FROM users")
    await update.message.reply_text("⏳ جاري إرسال الإشعار الجماعي للعملاء...")
    for u in users:
        try: await context.bot.send_message(chat_id=u[0], text=f"📢 {content}")
        except: pass
    await update.message.reply_text("✅ تم إرسال الإشعار الجماعي بنجاح.")
    return ConversationHandler.END

async def adm_rx_cardcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    if db_fetch_one("SELECT code FROM cards WHERE code = ?", (code,)):
        await update.message.reply_text("❌ الكود موجود مسبقاً. أرسل كوداً آخر أو /cancel")
        return ADMIN_CARD_CODE
    context.user_data['c_code'] = code
    await update.message.reply_text("💵 أرسل القيمة المالية للبطاقة:")
    return ADMIN_CARD_AMOUNT

async def adm_rx_cardamt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amt = float(update.message.text)
    except:
        await update.message.reply_text("❌ أرقام فقط. أرسل القيمة بالأرقام أو /cancel")
        return ADMIN_CARD_AMOUNT
    db_execute("INSERT INTO cards (code, amount) VALUES (?, ?)", (context.user_data['c_code'], amt))
    await update.message.reply_text(f"✅ تم إنشاء بطاقة الشحن بنجاح:\nالكود: `{context.user_data['c_code']}`\nالقيمة: `{amt}`$", parse_mode='Markdown')
    return ConversationHandler.END

async def adm_rx_srvname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_name'] = update.message.text
    await update.message.reply_text("📝 أرسل وصفاً مختصراً للخدمة أو العرض:")
    return ADMIN_SRV_DESC

async def adm_rx_srvdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_desc'] = update.message.text
    await update.message.reply_text("💵 أرسل السعر بالأرقام (اكتب 0 إذا كانت الخدمة مجانية في قسم العروض المجانية):")
    return ADMIN_SRV_PRICE

async def adm_rx_srvprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['s_price'] = float(update.message.text)
    except:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً. أعد الإرسال:")
        return ADMIN_SRV_PRICE
    await update.message.reply_text("⏳ أرسل المدة (مثال: تسليم فوري، شهر، دائم):")
    return ADMIN_SRV_DURATION

async def adm_rx_srvdur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_execute("INSERT INTO services (name, description, price, duration, category, quantity) VALUES (?, ?, ?, ?, ?, 0)", 
               (context.user_data['s_name'], context.user_data['s_desc'], context.user_data['s_price'], update.message.text, context.user_data['s_cat']))
    await update.message.reply_text("✅ تمت إضافة الخدمة أو العرض بنجاح في القسم المحدد!")
    return ConversationHandler.END

async def adm_rx_editprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_execute("UPDATE services SET price = ? WHERE id = ?", (float(update.message.text), context.user_data['edit_id']))
    await update.message.reply_text("✅ تم تعديل سعر الخدمة بنجاح.")
    return ConversationHandler.END

async def adm_rx_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "===" in text:
        keys = [k.strip() for k in text.split('===') if k.strip()]
    else:
        keys = [text]
        
    srv_id = context.user_data['stock_id']
    for k in keys:
        db_execute("INSERT INTO product_keys (service_id, key_text) VALUES (?, ?)", (srv_id, k))
        
    # تحديث إجمالي الكمية في جدول الخدمات بدقة
    total_stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
    db_execute("UPDATE services SET quantity = ? WHERE id = ?", (total_stock, srv_id))
        
    await update.message.reply_text(f"✅ تم إضافة `{len(keys)}` أكواد/حسابات جديدة بنجاح.\n📦 إجمالي الكمية المتوفرة الآن: `{total_stock}`", parse_mode='Markdown')
    return ConversationHandler.END

async def global_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # معالجة رسالة صيانة مخصصة إذا كان المشرف يكتبها
    if update.effective_user.id == ADMIN_ID and context.user_data.get('waiting_main_text'):
        custom_text = update.message.text.strip()
        db_execute("UPDATE maintenance_mode SET is_active = 1, custom_message = ? WHERE id = 1", (custom_text,))
        context.user_data['waiting_main_text'] = False
        await update.message.reply_text("✅ **تم تفعيل وضع الصيانة بنجاح مع رسالتك الفخمة المخصصة!**", parse_mode='Markdown')
        return
        
    await handle_text_messages(update, context)

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message: await update.message.reply_text("🚫 تم الإلغاء بنجاح.")
    elif update.callback_query: await update.callback_query.answer("🚫 تم الإلغاء")
    return ConversationHandler.END

# ================= (6) التشغيل الرئيسي =================
def main():
    init_db()
    
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook").read()
    except Exception: pass
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_conv_start, pattern="^(adm_add_bal|adm_sub_bal|adm_search|adm_broadcast|adm_add_srv|editprc_.*|adm_new_card|addstock_.*)$")],
        states={
            ADMIN_SRV_CATEGORY: [CallbackQueryHandler(adm_rx_category, pattern="^cat_")],
            ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_userid)],
            ADMIN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_amount)],
            ADMIN_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_search)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_broadcast)],
            ADMIN_CARD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_cardcode)],
            ADMIN_CARD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_cardamt)],
            ADMIN_SRV_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvname)],
            ADMIN_SRV_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvdesc)],
            ADMIN_SRV_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvprice)],
            ADMIN_SRV_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_srvdur)],
            ADMIN_NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_editprice)],
            ADMIN_STOCK_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rx_stock)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True
    ))
    
    app.add_handler(CallbackQueryHandler(admin_menus_handler, pattern="^(adm_|delsrv_|main_on_)"))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_handler))

    print("\n✅ البوت جاهز ويعمل الآن بكامل الميزات وبنظام الحفظ الآمن للبيانات!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main()
    except KeyboardInterrupt:
        print("\nتم إيقاف البوت.")
