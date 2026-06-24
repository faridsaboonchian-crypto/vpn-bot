import telebot
from telebot import types
import requests
import json
import uuid
import logging
import urllib3
import random
import string
import time
from datetime import datetime
import jdatetime
import os
from dotenv import load_dotenv
import sqlite3
import threading
from flask import Flask, Response, request

# بارگذاری متغیرهای محیطی
load_dotenv(dotenv_path="/root/.env")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('/root/bot.log'),
        logging.StreamHandler()
    ]
)

# ==================== [ تنظیمات از .env ] ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
CARD_NUMBER = os.getenv("CARD_NUMBER")
PANEL_URL = os.getenv("PANEL_URL")
SECRET_PATH = os.getenv("SECRET_PATH")
API_TOKEN = os.getenv("API_TOKEN")
INBOUND_ID = int(os.getenv("INBOUND_ID", 2))

# دریافت لیست IPها
CLEAN_IP_STR = os.getenv("CLEAN_IP", "188.114.97.2")
CLEAN_IPS = [ip.strip() for ip in CLEAN_IP_STR.split(",") if ip.strip()]

WS_DOMAIN = os.getenv("WS_DOMAIN", "v2.sanatify.ir")
WS_PATH = os.getenv("WS_PATH", "/sanatify-safe/")

logging.info(f"✅ IPهای Cloudflare: {CLEAN_IPS}")
# ====================================================================

bot = telebot.TeleBot(BOT_TOKEN)

PLANS = {
    "1gb14d": {"name": "دو هفته‌ای: ۱ گیگ", "price": "۲۰,۰۰۰", "gb": 1, "days": 14},
    "3gb14d": {"name": "دو هفته‌ای: ۳ گیگ", "price": "۶۰,۰۰۰", "gb": 3, "days": 14},
    "5gb": {"name": "یک ماهه: ۵ گیگ", "price": "۱۰۰,۰۰۰", "gb": 5, "days": 30},
    "10gb": {"name": "یک ماهه: ۱۰ گیگ", "price": "۲۰۰,۰۰۰", "gb": 10, "days": 30},
    "15gb": {"name": "یک ماهه: ۱۵ گیگ", "price": "۳۰۰,۰۰۰", "gb": 15, "days": 30},
    "20gb": {"name": "یک ماهه: ۲۰ گیگ", "price": "۴۰۰,۰۰۰", "gb": 20, "days": 30},
    "30gb": {"name": "یک ماهه: ۳۰ گیگ", "price": "۶۰۰,۰۰۰", "gb": 30, "days": 30}
}

USER_SELECTED_PLAN = {}
USER_LAST_INTERACTION = {}
USER_APPLIED_DISCOUNT = {}
DISCOUNT_CODES = {"MOHARAM20": 20}
DB_FILE = "/root/bot_users.db"

# ==================== [ توابع دیتابیس ] ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (chat_id TEXT PRIMARY KEY, referred_by TEXT, invite_count INTEGER DEFAULT 0)''')
    conn.commit()
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    for col_name in ["first_name", "username", "has_received_test", "has_received_referral_reward"]:
        if col_name not in columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} TEXT" if col_name in ["first_name", "username"] else f"ALTER TABLE users ADD COLUMN {col_name} INTEGER DEFAULT 0")
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_discounts (chat_id TEXT, discount_code TEXT, used_at TEXT, PRIMARY KEY (chat_id, discount_code))''')
    conn.commit()
    conn.close()

def fa_to_en_num(num_str):
    return num_str.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def en_to_fa_num(num_str):
    return num_str.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

def parse_price(price_str):
    return int(fa_to_en_num(price_str.replace(",", "").replace("،", "")))

def format_price(price_int):
    return en_to_fa_num(f"{price_int:,}")

def has_user_used_discount_code(chat_id, code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM user_discounts WHERE chat_id = ? AND discount_code = ?", (str(chat_id), str(code)))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def mark_discount_used(chat_id, code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO user_discounts (chat_id, discount_code, used_at) VALUES (?, ?, ?)", 
                   (str(chat_id), str(code), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT referred_by, invite_count, has_received_test, has_received_referral_reward FROM users WHERE chat_id = ?", (str(chat_id),))
    row = cursor.fetchone()
    conn.close()
    return {"referred_by": row[0], "invite_count": row[1], "has_received_test": row[2], "has_received_referral_reward": row[3]} if row else None

def add_or_update_user(chat_id, first_name, username, referred_by=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users WHERE chat_id = ?", (str(chat_id),))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET first_name = ?, username = ? WHERE chat_id = ?", (first_name, username, str(chat_id)))
    else:
        cursor.execute("INSERT INTO users (chat_id, referred_by, invite_count, first_name, username, has_received_test, has_received_referral_reward) VALUES (?, ?, 0, ?, ?, 0, 0)", 
                      (str(chat_id), referred_by, first_name, username))
    conn.commit()
    conn.close()

def set_received_referral_reward(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET has_received_referral_reward = 1 WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()

def set_received_test(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET has_received_test = 1 WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()

def update_invite_count(chat_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET invite_count = invite_count + ? WHERE chat_id = ?", (amount, str(chat_id)))
    conn.commit()
    conn.close()

def get_total_users():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_recent_users(limit=20):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, referred_by, invite_count, first_name, username FROM users ORDER BY rowid DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [(row[0], {"referred_by": row[1], "invite_count": row[2], "first_name": row[3], "username": row[4]}) for row in rows]

def get_all_user_ids():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def sync_profiles_background():
    logging.info("=== [شروع پردازه پس‌زمینه] ===")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, referred_by FROM users WHERE first_name IS NULL")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        logging.info("=== [همه کاربران بروز هستند] ===")
        return
    for chat_id, referred_by in rows:
        try:
            chat_obj = bot.get_chat(int(chat_id))
            add_or_update_user(chat_id, chat_obj.first_name or "کاربر", chat_obj.username, referred_by)
        except:
            add_or_update_user(chat_id, "آفلاین", None, referred_by)
        time.sleep(1.5)

def is_spammer(user_id, cooldown=1.5):
    if str(user_id) == str(ADMIN_CHAT_ID):
        return False
    current_time = time.time()
    last_time = USER_LAST_INTERACTION.get(user_id, 0)
    if current_time - last_time < cooldown:
        return True
    USER_LAST_INTERACTION[user_id] = current_time
    return False

def generate_sub_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

def bytes_to_gb(b):
    return round(b / (1024 * 1024 * 1024), 2)

def format_date_shamsi_with_countdown(expiry_time_ms):
    if expiry_time_ms == 0:
        return "♾️ نامحدود"
    current_time_ms = int(time.time() * 1000)
    if expiry_time_ms < 0:
        return f"⏳ {abs(expiry_time_ms) // (24*60*60*1000)} روز (پس از اولین اتصال)"
    remaining_ms = expiry_time_ms - current_time_ms
    expiry_datetime = jdatetime.datetime.fromtimestamp(expiry_time_ms / 1000)
    shamsi_date_str = expiry_datetime.strftime('%Y/%m/%d ساعت %H:%M')
    if remaining_ms <= 0:
        return f"❌ منقضی شده در {shamsi_date_str}"
    remaining_days = remaining_ms / (24 * 60 * 60 * 1000)
    return f"{shamsi_date_str} ({int(remaining_days)} روز باقی‌مانده)" if remaining_days >= 1 else f"{shamsi_date_str} ({int(remaining_ms/(60*60*1000))} ساعت باقی‌مانده)"

def test_api():
    try:
        r = requests.get(f"{PANEL_URL}/{SECRET_PATH}/panel/api/inbounds/list", 
                        headers={"Authorization": f"Bearer {API_TOKEN}"}, verify=False, timeout=20)
        logging.info(f"=== [تست API] STATUS: {r.status_code} {'✅' if r.status_code == 200 else '❌'} ===")
    except Exception as e:
        logging.error(f"خطا در تست API: {e}")

# ==================== [ Flask Proxy - پورت 80 ] ====================
app = Flask(__name__)

@app.route('/health')
def health():
    """تست سلامت Flask"""
    logging.info("🔵 [FLASK] Health check received")
    return Response("Flask OK - Port 80", status=200, mimetype='text/plain')

@app.route('/sub/<sub_id>')
def proxy_sub(sub_id):
    """دریافت سابسکریپشن و جایگزینی IP"""
    try:
        logging.info(f"🔵 [SUB] درخواست سابسکریپشن: {sub_id}")
        logging.info(f"🔵 [SUB] Remote IP: {request.remote_addr}")
        logging.info(f"🔵 [SUB] IPهای موجود: {CLEAN_IPS}")
        
        # دریافت از پنل Sanaei (پورت 2096)
        panel_url = f"http://185.215.244.29:2096/sub/{sub_id}"
        logging.info(f"🔵 [SUB] درخواست به پنل: {panel_url}")
        
        response = requests.get(panel_url, timeout=10, verify=False)
        logging.info(f"🔵 [SUB] پاسخ پنل: {response.status_code}")
        
        if response.status_code == 200:
            content = response.text
            logging.info(f"🔵 [SUB] طول محتوا: {len(content)} بایت")
            
            # انتخاب IP رندوم
            selected_ip = random.choice(CLEAN_IPS) if CLEAN_IPS else "188.114.97.2"
            logging.info(f"🔵 [SUB] IP انتخابی: {selected_ip}")
            
            # جایگزینی IP
            new_content = content.replace("185.215.244.29", selected_ip)
            logging.info(f"✅ [SUB] سابسکریپن با IP {selected_ip} ساخته شد")
            
            return Response(new_content, mimetype='text/plain', headers={
                'Content-Disposition': f'attachment; filename=sub.txt',
                'Cache-Control': 'no-cache'
            })
        else:
            logging.error(f"❌ [SUB] خطا از پنل: {response.status_code} - {response.text[:200]}")
            return Response(f"Error from panel: {response.status_code}", status=500, mimetype='text/plain')
            
    except Exception as e:
        logging.error(f"❌ [SUB] خطا: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return Response(f"Error: {str(e)}", status=500, mimetype='text/plain')

def run_flask():
    """اجرای Flask"""
    try:
        logging.info("🚀 [FLASK] شروع اجرا روی پورت 80...")
        logging.info(f"🚀 [FLASK] IPهای Cloudflare: {CLEAN_IPS}")
        
        # غیرفعال کردن لاگ‌های اضافی
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.WARNING)
        
        app.run(host='0.0.0.0', port=80, debug=False, threaded=True, use_reloader=False)
    except Exception as e:
        logging.error(f"❌ [FLASK] خطا در اجرا: {e}")
        logging.error(f"❌ [FLASK] احتمالاً پورت 80 اشغال است!")
        import traceback
        logging.error(traceback.format_exc())

# اجرای Flask در ترد جدا
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
logging.info("✅ [MAIN] Flask روی پورت 80 فعال شد")
# ====================================================================

def create_vless_link(email, limit_gb, expiry_days=30):
    try:
        add_url = f"{PANEL_URL}/{SECRET_PATH}/panel/api/clients/add"
        client_uuid = str(uuid.uuid4())
        sub_id = generate_sub_id()
        payload = {
            "client": {
                "id": client_uuid, "email": email, "flow": "",
                "limitIp": 2, "totalGB": int(limit_gb * 1024**3),
                "expiryTime": -int(expiry_days * 24 * 60 * 60 * 1000),
                "enable": True, "tgId": 0, "subId": sub_id
            },
            "inboundIds": [INBOUND_ID]
        }
        headers = {"Accept": "application/json", "Authorization": f"Bearer {API_TOKEN}"}
        
        response = requests.post(add_url, json=payload, headers=headers, timeout=10, verify=False)
        logging.info(f"پاسخ پنل: {response.status_code} - {response.text[:100]}")
        
        if response.status_code == 200 and response.json().get('success'):
            # ساخت لینک با پورت 80 (Flask)
            sub_link = f"http://185.215.244.29:80/sub/{sub_id}"
            logging.info(f"✅ سابسکریپشن ساخته شد: {sub_link}")
            return sub_link
        return None
    except Exception as e:
        logging.error(f"خطا در ساخت لینک: {e}")
        return None

def get_user_stats(chat_id):
    try:
        response = requests.get(f"{PANEL_URL}/{SECRET_PATH}/panel/api/inbounds/list",
                               headers={"Authorization": f"Bearer {API_TOKEN}"}, timeout=10, verify=False)
        if response.status_code == 200 and response.json().get("success"):
            data = response.json()
            prefixes = [f"user_{chat_id}_", f"free_{chat_id}_", f"free_test_{chat_id}_"]
            found_clients = []
            
            for inbound in data.get("obj", []):
                if inbound.get("id") == INBOUND_ID:
                    for client in inbound.get("clientStats", []):
                        if any(client.get("email", "").startswith(p) for p in prefixes):
                            found_clients.append(client)
            
            if not found_clients:
                return None
                
            stats_text = f"📊 <b>گزارش وضعیت</b>\n\n👤 <b>شناسه:</b> <code>user_{chat_id}</code>\n📦 <b>تعداد اکانت‌ها:</b> {len(found_clients)}\n\n"
            
            for i, client in enumerate(found_clients, 1):
                acc_type = "💎 خریداری شده" if client.get("email", "").startswith(f"user_{chat_id}_") else "🎁 هدیه/تست"
                stats_text += (
                    f"🔹 <b>اکانت {i} ({acc_type}):</b>\n"
                    f"  ⚡ وضعیت: {'فعال 🟢' if client.get('enable') else 'قطع 🔴'}\n"
                    f"  📥 دانلود: {bytes_to_gb(client.get('down', 0))} GB\n"
                    f"  📤 آپلود: {bytes_to_gb(client.get('up', 0))} GB\n"
                    f"  🔄 مصرف: {bytes_to_gb(client.get('up', 0) + client.get('down', 0))} GB\n"
                    f"  📅 انقضا: {format_date_shamsi_with_countdown(client.get('expiryTime', 0))}\n\n"
                )
            return stats_text
    except Exception as e:
        logging.error(f"خطا در استعلام: {e}")
    return None

def render_payment_card(message, chat_id, plan_key):
    plan = PLANS[plan_key]
    price_str = plan['price']
    applied_code = USER_APPLIED_DISCOUNT.get(chat_id)
    discount_text = ""
    
    if applied_code and applied_code in DISCOUNT_CODES:
        discount_percent = DISCOUNT_CODES[applied_code]
        final_price = parse_price(price_str) - int(parse_price(price_str) * discount_percent / 100)
        price_str = format_price(final_price)
        discount_text = f"🎁 <b>تخفیف:</b> <code>{applied_code}</code> (%{discount_percent})\n\n"
    
    payment_text = (
        f"💳 <b>اشتراک {plan['name']}</b>\n{discount_text}"
        f"مبلغ: <b>{price_str} تومان</b>\n\n"
        f"💳 <code>{CARD_NUMBER}</code>\n"
        f"👤 <b>فرید صابونچیان</b>\n\n"
        f"⚠️ <b>فیش واریزی</b> را ارسال کنید."
    )
    
    if isinstance(message, types.Message):
        bot.send_message(chat_id, payment_text, parse_mode="HTML")
    else:
        bot.edit_message_text(payment_text, chat_id, message.message_id, parse_mode="HTML")

# ==================== [ هندلرهای ربات ] ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_spammer(message.from_user.id):
        return
    
    chat_id = str(message.chat.id)
    parts = message.text.split()
    referrer_id = parts[1] if len(parts) > 1 else None
    
    first_name = message.from_user.first_name or "کاربر"
    username = message.from_user.username
    
    user_data = get_user(chat_id)
    is_new = user_data is None
    
    if is_new:
        if referrer_id == chat_id:
            referrer_id = None
        add_or_update_user(chat_id, first_name, username, referrer_id)
    else:
        add_or_update_user(chat_id, first_name, username, user_data.get("referred_by"))
    
    # منطق دعوت
    if is_new and referrer_id:
        update_invite_count(referrer_id, 1)
        inviter = get_user(referrer_id)
        if inviter:
            invites = inviter.get("invite_count", 0)
            has_reward = inviter.get("has_received_referral_reward", 0)
            
            if has_reward == 0:
                bot.send_message(int(referrer_id), f"👤 کاربر جدید با لینک شما!\n🎯 دعوت‌ها: <code>{invites}/3</code>", parse_mode="HTML")
                
                if invites >= 3:
                    set_received_referral_reward(referrer_id)
                    bot.send_message(ADMIN_CHAT_ID, f"🎁 هدیه خودکار برای کاربر {referrer_id}")
                    
                    sub_link = create_vless_link(f"free_{referrer_id}_{random.randint(1000,9999)}", 2, 1)
                    if sub_link:
                        bot.send_message(int(referrer_id), f"🎉 <b>۳ دعوت کامل شد!</b>\n\n🎁 <b>هدیه ۲ گیگی:</b>\n<code>{sub_link}</code>", parse_mode="HTML")
            else:
                bot.send_message(int(referrer_id), f"👤 کاربر جدید!\n🎯 کل دعوت‌ها: <code>{invites}</code>", parse_mode="HTML")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🛒 خرید اشتراک"),
        types.KeyboardButton("📊 وضعیت من"),
        types.KeyboardButton("🎁 دعوت (رایگان)"),
        types.KeyboardButton("📱 راهنمای سابسکریپشن"),
        types.KeyboardButton("📚 راهنمای اتصال"),
        types.KeyboardButton("📞 پشتیبانی")
    )
    
    bot.send_message(chat_id, f"سلام {first_name}! به ربات VPN خوش آمدید. 🌹\n\nاز منوی زیر استفاده کنید:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📱 راهنمای سابسکریپشن")
def sub_guide(message):
    guide = (
        "📱 <b>راهنمای سابسکریپشن</b>\n\n"
        "✅ <b>مزایا:</b>\n"
        "• آپدیت خودکار IPها\n"
        "• بدون نیاز به کانفیگ جدید\n\n"
        "🔧 <b>نحوه استفاده در v2rayNG:</b>\n"
        "۱. منوی سه خط ☰ → Subscription group setting\n"
        "۲. دکمه + را بزنید\n"
        "۳. در Optional URL، لینک سابسکریپشن را پیست کنید\n"
        "۴. Enable update را روشن کنید\n"
        "۵. ⚠️ <b>Allow insecure HTTP</b> را روشن کنید\n"
        "۶. تیک ✓ را بزنید\n"
        "۷. به صفحه اصلی برگردید\n"
        "۸. دکمه 🔄 را بزنید تا دانلود شود\n\n"
        "🔄 <b>برای آپدیت:</b>\n"
        "هر زمان خواستید، روی 🔄 بزنید!"
    )
    bot.send_message(message.chat.id, guide, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📚 راهنمای اتصال")
def conn_guide(message):
    guide = (
        "📚 <b>راهنمای اتصال</b>\n\n"
        "🤖 <b>اندروید:</b>\n"
        "۱. v2rayNG از گوگل‌پلی\n"
        "۲. لینک را کپی کنید\n"
        "۳. + → Import from clipboard\n"
        "۴. اتصال را بزنید\n\n"
        "🍏 <b>iOS:</b>\n"
        "۱. FoXray یا v2raybox\n"
        "۲. + → Import\n\n"
        "💻 <b>ویندوز:</b>\n"
        "۱. v2rayN\n"
        "۲. لینک را پیست کنید"
    )
    bot.send_message(message.chat.id, guide, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎁 دعوت (رایگان)")
def invite_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎁 اکانت تست (۱ گیگ)", callback_data="get_test"),
        types.InlineKeyboardButton("🤝 دعوت (۲ گیگ هدیه)", callback_data="get_ref")
    )
    bot.send_message(message.chat.id, "یکی را انتخاب کنید:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data in ["get_test", "get_ref"])
def free_features(call):
    chat_id = str(call.message.chat.id)
    if is_spammer(call.from_user.id, 2.0):
        bot.answer_callback_query(call.id, "⚠️ اسپم نکنید!", show_alert=True)
        return
    
    if call.data == "get_test":
        user = get_user(chat_id)
        if not user:
            add_or_update_user(chat_id, call.from_user.first_name or "کاربر", call.from_user.username)
            user = {"has_received_test": 0}
        
        if user.get("has_received_test", 0) == 1:
            bot.answer_callback_query(call.id, "❌ قبلاً تست گرفتید!", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "⏳ در حال ساخت...")
        bot.edit_message_text("⏳ در حال صدور اکانت تست...", call.message.chat.id, call.message.message_id)
        
        sub_link = create_vless_link(f"test_{chat_id}_{random.randint(1000,9999)}", 1, 1)
        if sub_link:
            set_received_test(chat_id)
            bot.edit_message_text(f"🎉 <b>تست ۱ گیگی:</b>\n\n<code>{sub_link}</code>\n\n📱 در منوی اصلی، راهنمای سابسکریپشن را ببینید!", 
                                 call.message.chat.id, call.message.message_id, parse_mode="HTML")
    else:
        user = get_user(chat_id)
        if not user:
            add_or_update_user(chat_id, call.from_user.first_name or "کاربر", call.from_user.username)
            user = {"invite_count": 0, "has_received_referral_reward": 0}
        
        invites = user.get("invite_count", 0)
        has_reward = user.get("has_received_referral_reward", 0)
        bot_info = bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={chat_id}"
        
        progress = f"👤 دعوت‌ها: <code>{invites}/3</code>" if has_reward == 0 else f"👤 کل: <code>{invites}</code> (✅ هدیه گرفتید)"
        
        text = (
            f"🎁 <b>دعوت از دوستان</b>\n\n"
            f"با دعوت ۳ نفر، <b>۲ گیگ ۱ روزه</b> هدیه بگیرید!\n\n"
            f"{progress}\n\n"
            f"🔗 <b>لینک شما:</b>\n<code>{ref_link}</code>"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        bot.send_message(call.message.chat.id, f"🚀 VPN پرسرعت!\n\n{ref_link}")

@bot.message_handler(func=lambda m: m.text == "📞 پشتیبانی")
def support(message):
    bot.send_message(message.chat.id, "✍️ @SpeedNet_VpnBot")

@bot.message_handler(func=lambda m: m.text == "📊 وضعیت من")
def show_stats(message):
    if is_spammer(message.from_user.id, 5.0):
        return
    bot.send_message(message.chat.id, "⏳ در حال استعلام...")
    stats = get_user_stats(message.chat.id)
    bot.send_message(message.chat.id, stats if stats else "❌ اشتراکی ندارید. 🛒", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🛒 خرید اشتراک")
def select_plan(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, plan in PLANS.items():
        markup.add(types.InlineKeyboardButton(f"📦 {plan['name']} ← {plan['price']} تومان", callback_data=f"buy_{key}"))
    bot.send_message(message.chat.id, "پلن را انتخاب کنید:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def ask_discount(call):
    plan_key = call.data.split("_")[1]
    USER_SELECTED_PLAN[call.message.chat.id] = plan_key
    USER_APPLIED_DISCOUNT.pop(call.message.chat.id, None)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✍️ کد تخفیف", callback_data=f"disc_{plan_key}"),
        types.InlineKeyboardButton("💳 بدون تخفیف", callback_data=f"pay_{plan_key}")
    )
    bot.edit_message_text(f"🎯 {PLANS[plan_key]['name']}\n\nکد تخفیف دارید؟", 
                         call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("pay_"))
def show_card(call):
    plan_key = call.data.split("_")[1]
    USER_SELECTED_PLAN[call.message.chat.id] = plan_key
    render_payment_card(call.message, call.message.chat.id, plan_key)

@bot.callback_query_handler(func=lambda c: c.data.startswith("disc_"))
def ask_code(call):
    plan_key = call.data.split("_")[1]
    bot.edit_message_text("✍️ کد تخفیف را تایپ کنید:", call.message.chat.id, call.message.message_id)
    msg = call.message
    msg.from_user = call.from_user
    bot.register_next_step_handler(msg, process_code, plan_key)

def process_code(message, plan_key):
    chat_id = message.chat.id
    code = message.text.strip().upper().replace("/", "").replace("\\", "")
    
    if code not in DISCOUNT_CODES:
        bot.send_message(chat_id, "❌ کد نامعتبر است.")
        return
    
    if has_user_used_discount_code(chat_id, code):
        bot.send_message(chat_id, "❌ قبلاً استفاده کردید.")
        return
    
    USER_APPLIED_DISCOUNT[chat_id] = code
    bot.send_message(chat_id, "✅ تخفیف اعمال شد!")
    render_payment_card(message, chat_id, plan_key)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if is_spammer(message.from_user.id, 3.0):
        return
    
    plan_key = USER_SELECTED_PLAN.get(message.chat.id, "20gb")
    plan = PLANS[plan_key]
    price = plan['price']
    applied = USER_APPLIED_DISCOUNT.get(message.chat.id, "none")
    
    if applied != "none" and applied in DISCOUNT_CODES:
        disc = DISCOUNT_CODES[applied]
        price = format_price(parse_price(price) - int(parse_price(price) * disc / 100))
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ تایید و ارسال لینک", callback_data=f"ok_{message.chat.id}_{plan_key}_{applied}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"no_{message.chat.id}")
    )
    
    caption = f"📥 فیش از: {message.from_user.first_name}\n🆔 {message.chat.id}\n📦 {plan['name']}\n💰 {price} تومان"
    
    bot.send_photo(ADMIN_CHAT_ID, message.photo[-1].file_id, caption=caption, reply_markup=markup)
    bot.send_message(message.chat.id, "⏳ در انتظار تایید ادمین...")

@bot.callback_query_handler(func=lambda c: c.data.startswith("ok_") or c.data.startswith("no_"))
def admin_action(call):
    if str(call.from_user.id) != str(ADMIN_CHAT_ID):
        return
    
    parts = call.data.split("_")
    action = parts[0]
    user_id = parts[1]
    
    if action == "ok":
        plan_key = parts[2]
        disc = parts[3] if len(parts) > 3 else "none"
        
        bot.answer_callback_query(call.id, "در حال ساخت...")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
        sub_link = create_vless_link(f"user_{user_id}_{random.randint(1000,9999)}", 
                                     PLANS[plan_key]['gb'], PLANS[plan_key]['days'])
        
        if sub_link:
            if disc != "none":
                mark_discount_used(user_id, disc)
            
            text = (
                f"✅ <b>پرداخت تایید شد!</b>\n\n"
                f"🚀 <b>سابسکریپشن شما:</b>\n\n"
                f"<code>{sub_link}</code>\n\n"
                f"📱 <b>راهنما:</b>\n"
                f"در منوی اصلی، <b>📱 راهنمای سابسکریپشن</b> را ببینید.\n\n"
                f"✨ هر بار آپدیت کنید، IP جدید می‌گیرید!"
            )
            bot.send_message(user_id, text, parse_mode="HTML")
            bot.send_message(ADMIN_CHAT_ID, "✅ ارسال شد.")
        else:
            bot.send_message(ADMIN_CHAT_ID, "❌ خطا! لاگ را چک کنید.")
    else:
        bot.edit_message_caption("❌ رد شد.", call.message.chat.id, call.message.message_id)

@bot.message_handler(commands=['stats'])
def admin_stats(message):
    if str(message.chat.id) != str(ADMIN_CHAT_ID):
        return
    
    total = get_total_users()
    recent = get_recent_users(20)
    
    text = f"📊 <b>آمار ربات</b>\n\n👥 کل: <code>{total}</code>\n\n📌 آخرین کاربران:\n"
    for chat_id, data in recent:
        name = data.get('first_name', 'Unknown')
        ref = f"دعوت: {data.get('referred_by', 'مستقیم')}"
        text += f"• {name} | {ref}\n"
    
    bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if str(message.chat.id) != str(ADMIN_CHAT_ID):
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(ADMIN_CHAT_ID, "❌ متن را وارد کنید: /broadcast متن")
        return
    
    text = parts[1]
    users = get_all_user_ids()
    success = fail = 0
    
    for uid in users:
        try:
            bot.send_message(int(uid), text)
            success += 1
        except:
            fail += 1
    
    bot.send_message(ADMIN_CHAT_ID, f"📢 پایان\n🟢 {success}\n🔴 {fail}")

# ==================== [ اجرای ربات ] ====================
if __name__ == "__main__":
    init_db()
    threading.Thread(target=sync_profiles_background, daemon=True).start()
    test_api()
    logging.info("🚀 ربات شروع به کار کرد!")
    bot.infinity_polling(skip_pending=True)
