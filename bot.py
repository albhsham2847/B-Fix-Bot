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
BOT_TOKEN = "8299192931:AAHkXI_BLyoAp8TvrSCU9i_CnoDSyDFbTGA"
ADMIN_ID = 8218627841  # استبدله بـ ID الخاص بك 

WHATSAPP_LINK = "https://iwtsp.com/967777728478"
SUPPORT_LINK = "https://t.me/bfixSoftware"
DB_NAME = "bfix_store.db"

(ADMIN_USER_ID, ADMIN_AMOUNT, ADMIN_SEARCH, ADMIN_BROADCAST, ADMIN_SRV_CATEGORY,
 ADMIN_SRV_NAME, ADMIN_SRV_DESC, ADMIN_SRV_PRICE, ADMIN_SRV_DURATION, 
 ADMIN_NEW_PRICE, ADMIN_CARD_CODE, ADMIN_CARD_AMOUNT, ADMIN_STOCK_KEY) = range(13)

USER_CARD_CODE = 20

# ================= (2) نظام قاعدة البيانات الآمن 100% =================
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
        # جداول آمنة لا تحذف البيانات القديمة أبداً بوجود (IF NOT EXISTS)
        conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, balance REAL DEFAULT 0.0, join_date TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, price REAL, duration TEXT, category TEXT DEFAULT 'digital')''')
        try: conn.execute("ALTER TABLE services ADD COLUMN category TEXT DEFAULT 'digital'")
        except: pass
        conn.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, service_id INTEGER, status TEXT, order_date TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS cards (code TEXT PRIMARY KEY, amount REAL, is_used INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS product_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER, key_text TEXT, is_sold INTEGER DEFAULT 0)''')
        conn.commit()

def add_user_if_not_exists(user_id, name):
    user = db_fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        db_execute("INSERT INTO users (user_id, name, balance, join_date) VALUES (?, ?, ?, ?)", 
                   (user_id, name, 0.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

# ================= (3) واجهة العميل والأقسام =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_if_not_exists(user.id, user.first_name)
    
    text = (
        "✨ ━━━━━ ❲ 𝐁-𝐅𝐢𝐱 𝐒𝐨𝐟𝐭𝐰𝐚𝐫𝐞 ❳ ━━━━━ ✨\n\n"
        f"👋 أهلاً بك يا [{user.first_name}](tg://user?id={user.id})\n"
        "في متجرك الآلي للخدمات الرقمية والاشتراكات 🚀\n\n"
        "🛒 ❲ يرجى اختيار القسم المطلوب من القائمة ❳ 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = [
        [InlineKeyboardButton("🟢 الخدمات الرقمية ✨", callback_data="show_cat_digital"),
         InlineKeyboardButton("🟢 الاشتراكات 🚀", callback_data="show_cat_subscriptions")],
        [InlineKeyboardButton("🟢 خدمة إيجار الأدوات 🛠️", callback_data="show_cat_rentals")],
        [InlineKeyboardButton("🔵 سجل طلباتي 🔄", callback_data="my_orders"),
         InlineKeyboardButton("🔵 حسابي ⚡", callback_data="my_profile")],
        [InlineKeyboardButton("🟢 شحن الرصيد بكود", callback_data="charge_account"),
         InlineKeyboardButton("🟢 تغذية حسابك", callback_data="fund_account")],
        [InlineKeyboardButton("🔵 واتساب 🌐", url=WHATSAPP_LINK),
         InlineKeyboardButton("🔵 الدعم 🛠️", url=SUPPORT_LINK)],
        [InlineKeyboardButton("🔴 معلومات البوت ℹ️", callback_data="bot_info")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='Markdown')

async def main_buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "my_profile":
        user_info = db_fetch_one("SELECT name, balance FROM users WHERE user_id = ?", (user_id,))
        text = f"👤 **ملفك الشخصي:**\n\n▪️ **الاسم:** {user_info[0]}\n▪️ **الآيدي:** `{user_id}`\n▪️ **الرصيد:** `{user_info[1]}` $\n\nلزيادة رصيدك اضغط تغذية حسابك."
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 تغذية حسابك", callback_data="fund_account")], [InlineKeyboardButton("🔴 رجوع للقائمة الرئيسية 🔄", callback_data="main_menu")]]), parse_mode='Markdown')

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
            [InlineKeyboardButton("🔴 رجوع للقائمة الرئيسية 🔄", callback_data="main_menu")]
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
            "• **الاسم**\n"
            "• **الخدمة المطلوبة**\n"
            "• **المبلغ المحول**\n"
            "• **وسيلة الدفع المستخدمة**\n\n"
            "سيتم مراجعة عملية الدفع وتفعيل طلبك في أسرع وقت ممكن.\n\n"
            "🔒 دفع آمن • تفعيل سريع • خدمة موثوقة"
        )
        back_kb = [
            [InlineKeyboardButton("🟢 مراسلة الدعم عبر واتساب", url=WHATSAPP_LINK)],
            [InlineKeyboardButton("🔵 العودة لطرق الدفع", callback_data="fund_account")],
            [InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]
        ]
        await query.message.edit_text(payment_details, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode='Markdown')

    elif data.startswith("show_cat_"):
        category = data.replace("show_cat_", "")
        if category == "digital": title = "💠 الخدمات الرقمية (Software & Tools) ✨"
        elif category == "subscriptions": title = "🔵 الاشتراكات (AI & Premium) 🚀"
        elif category == "rentals": title = "🔧 خدمة إيجار الأدوات 🛠️"
        else: title = "🛒 قائمة الخدمات"
        
        services = db_fetch_all("SELECT id, name, price FROM services WHERE category = ?", (category,))
        if not services:
            await query.message.edit_text(f"🚧 لا توجد خدمات في قسم {title} حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للقائمة 🔄", callback_data="main_menu")]]))
            return
            
        keyboard = []
        for srv in services:
            stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv[0],))[0]
            status = "🟢 تتوفر" if stock > 0 else "🔴 نفدت"
            keyboard.append([InlineKeyboardButton(f"▪️ {srv[1]} - {srv[2]}$ ({status})", callback_data=f"srv_{srv[0]}")])
        keyboard.append([InlineKeyboardButton("🔴 العودة للقائمة 🔄", callback_data="main_menu")])
        await query.message.edit_text(f"📑 **{title}:**\n\n👇 اختر الخدمة لعرض التفاصيل:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("srv_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT id, name, description, price, duration, category FROM services WHERE id = ?", (srv_id,))
        stock = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE service_id = ? AND is_sold = 0", (srv_id,))[0]
        text = f"📌 **الخدمة:** {srv[1]}\n📝 **الوصف:** {srv[2]}\n⏳ **المدة:** {srv[4]}\n💵 **السعر:** `{srv[3]}` $\n📦 **الكمية المتوفرة:** {stock}"
        
        keyboard = [[InlineKeyboardButton("🟢 شراء الآن ⚡", callback_data=f"buy_{srv[0]}")], [InlineKeyboardButton("🔴 رجوع للقسم 🔄", callback_data=f"show_cat_{srv[5]}")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("buy_"):
        srv_id = int(data.split("_")[1])
        srv = db_fetch_one("SELECT price, name FROM services WHERE id = ?", (srv_id,))
        user_info = db_fetch_one("SELECT balance, name FROM users WHERE user_id = ?", (user_id,))
        stock_key = db_fetch_one("SELECT id, key_text FROM product_keys WHERE service_id = ? AND is_sold = 0 LIMIT 1", (srv_id,))
        
        if not stock_key:
            out_of_stock_msg = "❌ **عذراً، لقد نفدت الكمية (الأكواد) لهذه الخدمة للتو!**\n\nيرجى المحاولة لاحقاً أو مراسلة الدعم الفني."
            await query.message.edit_text(out_of_stock_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 العودة للخدمات", callback_data="main_menu")]]), parse_mode='Markdown')
            return
            
        if user_info[0] >= srv[0]:
            new_balance = user_info[0] - srv[0]
            db_execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            db_execute("UPDATE product_keys SET is_sold = 1 WHERE id = ?", (stock_key[0],))
            db_execute("INSERT INTO orders (user_id, service_id, status, order_date) VALUES (?, ?, 'مكتمل ✅', ?)", (user_id, srv_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            success_msg = "✅ **تم الشراء بنجاح!**\n\n🎁 تم إرسال معلومات اشتراكك في الرسالة التالية لتتمكن من نسخها بسهولة 👇"
            await query.message.edit_text(success_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 القائمة الرئيسية", callback_data="main_menu")]]), parse_mode='Markdown')
            
            await context.bot.send_message(chat_id=user_id, text=stock_key[1])
            await context.bot.send_message(chat_id=user_id, text="🌟 **شكراً لاستخدامك بوت Bfixsoftware** 🌟", parse_mode='Markdown')
            
            admin_msg = f"🔔 **بيع آلي جديد!**\n👤 العميل: {user_info[1]}\n🆔 `{user_id}`\n🛒 {srv[1]}\n💵 {srv[0]}$\n\n👇 الكود المباع:"
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode='Markdown')
            await context.bot.send_message(chat_id=ADMIN_ID, text=stock_key[1])
        else:
            await query.answer("❌ رصيدك غير كافٍ لإتمام العملية!", show_alert=True)

    elif data == "my_orders":
        orders = db_fetch_all("SELECT s.name, o.status, o.order_date FROM orders o JOIN services s ON o.service_id = s.id WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5", (user_id,))
        if not orders: text = "📦 لا توجد طلبات."
        else: text = "📦 **آخر 5 طلبات:**\n\n" + "\n".join([f"▪️ **{o[0]}**\nالحالة: {o[1]}\nالتاريخ: {o[2]}\n" for o in orders])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع للقائمة", callback_data="main_menu")]]), parse_mode='Markdown')

    elif data == "bot_info":
        await query.message.edit_text("🤖 متجر B-Fix الذكي للخدمات والاشتراكات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="main_menu")]]))

    elif data == "main_menu":
        await start_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_card'):
        code = update.message.text.strip()
        card = db_fetch_one("SELECT amount, is_used FROM cards WHERE code = ?", (code,))
        
        if not card or card[1] == 1:
            await update.message.reply_text("❌ الكود غير صحيح أو مستخدم. أرسل كوداً آخر أو /cancel للإلغاء.")
            return
            
        db_execute("UPDATE cards SET is_used = 1 WHERE code = ?", (code,))
        db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (card[0], update.effective_user.id))
        new_balance = db_fetch_one("SELECT balance FROM users WHERE user_id = ?", (update.effective_user.id,))[0]
        
        context.user_data['waiting_card'] = False
        await update.message.reply_text(f"✅ **تم الشحن بنجاح!**\n💰 القيمة: `{card[0]}` $\n💵 رصيدك الجديد: `{new_balance}` $", parse_mode='Markdown')
        return

# ================= (4) نظام المطور =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return 
    text = "👑 **لوحة تحكم المطور**"
    keyboard = [
        [InlineKeyboardButton("🟢 إضافة رصيد 🔵", callback_data="adm_add_bal"), InlineKeyboardButton("🔴 خصم رصيد 🔴", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🟢 إنشاء كود شحن 🎟", callback_data="adm_new_card"), InlineKeyboardButton("🔵 بحث 🔎", callback_data="adm_search")],
        [InlineKeyboardButton("🟢 إدارة الخدمات والأكواد 🛠️", callback_data="adm_srv_menu")],
        [InlineKeyboardButton("🔵 إشعار جماعي 📢", callback_data="adm_broadcast"), InlineKeyboardButton("🔴 إحصائيات 📊", callback_data="adm_stats")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup)
    elif update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup)

async def admin_menus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return
    await query.answer()
    data = query.data

    if data == "adm_main":
        await admin_panel(update, context)
    elif data == "adm_stats":
        u_count = db_fetch_one("SELECT COUNT(*) FROM users")[0]
        o_count = db_fetch_one("SELECT COUNT(*) FROM orders")[0]
        k_count = db_fetch_one("SELECT COUNT(*) FROM product_keys WHERE is_sold = 0")[0]
        text = f"📊 **إحصائيات:**\n👥 مستخدمين: `{u_count}`\n📦 طلبات: `{o_count}`\n🔑 أكواد متوفرة: `{k_count}`"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع لوحة التحكم", callback_data="adm_main")]]), parse_mode='Markdown')
    elif data == "adm_srv_menu":
        keyboard = [
            [InlineKeyboardButton("🟢 إضافة خدمة جديدة", callback_data="adm_add_srv")],
            [InlineKeyboardButton("🔵 شحن أكواد لخدمة", callback_data="adm_stock_list")],
            [InlineKeyboardButton("🔵 تعديل سعر خدمة", callback_data="adm_edit_prc_list")],
            [InlineKeyboardButton("🔴 حذف خدمة", callback_data="adm_del_srv_list")],
            [InlineKeyboardButton("🔴 رجوع لوحة التحكم", callback_data="adm_main")]
        ]
        await query.message.edit_text("📦 **إدارة الخدمات:**", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data in ["adm_del_srv_list", "adm_edit_prc_list", "adm_stock_list"]:
        services = db_fetch_all("SELECT id, name, category FROM services")
        if not services:
            await query.message.edit_text("لا توجد خدمات مضافة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")]]))
            return
        if data == "adm_del_srv_list": pref, action = "delsrv_", "حذف"
        elif data == "adm_edit_prc_list": pref, action = "editprc_", "تعديل سعر"
        else: pref, action = "addstock_", "إضافة أكواد لـ"
        
        keyboard = []
        for s in services:
            if s[2] == "digital": cat_icon = "💠"
            elif s[2] == "subscriptions": cat_icon = "🔵"
            else: cat_icon = "🔧"
            keyboard.append([InlineKeyboardButton(f"{cat_icon} {s[1]}", callback_data=f"{pref}{s[0]}")])
        
        keyboard.append([InlineKeyboardButton("🔴 رجوع", callback_data="adm_srv_menu")])
        await query.message.edit_text(f"اختر الخدمة لـ {action}:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("delsrv_"):
        srv_id = int(data.split("_")[1])
        db_execute("DELETE FROM services WHERE id = ?", (srv_id,))
        db_execute("DELETE FROM product_keys WHERE service_id = ?", (srv_id,))
        await query.message.edit_text("✅ تم الحذف بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 إدارة الخدمات", callback_data="adm_srv_menu")]]))

# ================= (5) معالجات المحادثة للإدارة =================
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
        await query.message.edit_text("🔍 أرسل آيدي (ID) للبحث:\n(أرسل /cancel للإلغاء)")
        return ADMIN_SEARCH
    elif data == "adm_broadcast":
        await query.message.edit_text("📢 أرسل نص الإشعار الجماعي:\n(أرسل /cancel للإلغاء)")
        return ADMIN_BROADCAST
    elif data == "adm_add_srv":
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💠 قسم الخدمات الرقمية (Software)", callback_data="cat_digital")],
            [InlineKeyboardButton("🔵 قسم الاشتراكات (AI)", callback_data="cat_subscriptions")],
            [InlineKeyboardButton("🔧 قسم إيجار الأدوات", callback_data="cat_rentals")],
            [InlineKeyboardButton("🔴 إلغاء", callback_data="cat_cancel")]
        ])
        await query.message.edit_text("➕ **إضافة خدمة جديدة:**\n\nأين تريد وضع هذه الخدمة؟ اختر القسم المناسب:", reply_markup=markup)
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
            "🔑 **إضافة أكواد للخدمة:**\n\n"
            "أرسل معلومات الحساب أو الكود الآن.\n"
            "*(ملاحظة: سيتم حفظ رسالتك كاملة ككود واحد حتى لو كان فيها عدة أسطر)*\n\n"
            "⚠️ إذا أردت إضافة **عدة حسابات منفصلة** دفعة واحدة، افصل بين كل حساب وآخر بوضع `===`\n\n"
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
    await query.message.edit_text("📝 ممتاز. أرسل الآن **اسم** الخدمة أو الاشتراك:\n(أرسل /cancel للإلغاء)")
    return ADMIN_SRV_NAME

async def adm_rx_userid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ أرقام فقط. أعد الإرسال أو /cancel")
        return ADMIN_USER_ID
    user = db_fetch_one("SELECT name, balance FROM users WHERE user_id = ?", (int(update.message.text),))
    if not user:
        await update.message.reply_text("❌ مستخدم غير موجود. أعد أو /cancel")
        return ADMIN_USER_ID
    context.user_data['target'] = int(update.message.text)
    await update.message.reply_text(f"✅ العميل: {user[0]} | رصيده: {user[1]}\n✍️ أرسل المبلغ:")
    return ADMIN_AMOUNT

async def adm_rx_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amt = float(update.message.text)
    except:
        await update.message.reply_text("❌ أرقام فقط. أعد أو /cancel")
        return ADMIN_AMOUNT
    if context.user_data['action'] == "adm_sub_bal": amt = -amt
    db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, context.user_data['target']))
    await update.message.reply_text("✅ تم تحديث الرصيد بنجاح.")
    return ConversationHandler.END

async def adm_rx_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db_fetch_one("SELECT name, balance, join_date FROM users WHERE user_id = ?", (int(update.message.text),))
    if user: await update.message.reply_text(f"👤 {user[0]}\n💰 {user[1]}$\n📅 {user[2]}")
    else: await update.message.reply_text("❌ غير موجود.")
    return ConversationHandler.END

async def adm_rx_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db_fetch_all("SELECT user_id FROM users")
    await update.message.reply_text("⏳ جاري الإرسال...")
    for u in users:
        try: await context.bot.send_message(chat_id=u[0], text=f"📢 {update.message.text}")
        except: pass
    await update.message.reply_text("✅ تم الإرسال.")
    return ConversationHandler.END

async def adm_rx_cardcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    if db_fetch_one("SELECT code FROM cards WHERE code = ?", (code,)):
        await update.message.reply_text("❌ الكود موجود مسبقاً. أرسل غيره أو /cancel")
        return ADMIN_CARD_CODE
    context.user_data['c_code'] = code
    await update.message.reply_text("💵 أرسل القيمة (السعر):")
    return ADMIN_CARD_AMOUNT

async def adm_rx_cardamt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amt = float(update.message.text)
    except:
        await update.message.reply_text("❌ أرقام فقط. أرسل السعر أو /cancel")
        return ADMIN_CARD_AMOUNT
    db_execute("INSERT INTO cards (code, amount) VALUES (?, ?)", (context.user_data['c_code'], amt))
    await update.message.reply_text(f"✅ تم إنشاء البطاقة: `{context.user_data['c_code']}` بقيمة `{amt}`$", parse_mode='Markdown')
    return ConversationHandler.END

async def adm_rx_srvname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_name'] = update.message.text
    await update.message.reply_text("📝 أرسل وصفاً مختصراً للخدمة:")
    return ADMIN_SRV_DESC
async def adm_rx_srvdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['s_desc'] = update.message.text
    await update.message.reply_text("💵 أرسل السعر بالأرقام:")
    return ADMIN_SRV_PRICE
async def adm_rx_srvprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['s_price'] = float(update.message.text)
    except:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً. أعد الإرسال:")
        return ADMIN_SRV_PRICE
    await update.message.reply_text("⏳ أرسل المدة (مثال: تسليم فوري، أو شهر):")
    return ADMIN_SRV_DURATION
async def adm_rx_srvdur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_execute("INSERT INTO services (name, description, price, duration, category) VALUES (?, ?, ?, ?, ?)", 
               (context.user_data['s_name'], context.user_data['s_desc'], context.user_data['s_price'], update.message.text, context.user_data['s_cat']))
    await update.message.reply_text("✅ تم إضافة الخدمة بالقسم المخصص بنجاح!")
    return ConversationHandler.END

async def adm_rx_editprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_execute("UPDATE services SET price = ? WHERE id = ?", (float(update.message.text), context.user_data['edit_id']))
    await update.message.reply_text("✅ تم تعديل السعر.")
    return ConversationHandler.END

async def adm_rx_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "===" in text:
        keys = [k.strip() for k in text.split('===') if k.strip()]
    else:
        keys = [text]
        
    for k in keys:
        db_execute("INSERT INTO product_keys (service_id, key_text) VALUES (?, ?)", (context.user_data['stock_id'], k))
        
    await update.message.reply_text(f"✅ تم إضافة `{len(keys)}` أكواد/حسابات للمخزون بنجاح.", parse_mode='Markdown')
    return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    app.add_handler(CallbackQueryHandler(admin_menus_handler, pattern="^(adm_|delsrv_)"))
    app.add_handler(CallbackQueryHandler(main_buttons_handler))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("\n✅ البوت جاهز ويعمل الآن بقوة وبنظام الحفظ الآمن للبيانات!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main()
    except KeyboardInterrupt:
        print("\nتم إيقاف البوت.")

