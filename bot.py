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
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==================== [ تنظیمات اصلی از فایل .env ] ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
CARD_NUMBER = os.getenv("CARD_NUMBER")
PANEL_URL = os.getenv("PANEL_URL")
SECRET_PATH = os.getenv("SECRET_PATH")
API_TOKEN = os.getenv("API_TOKEN")
INBOUND_ID = int(os.getenv("INBOUND_ID", 2))

# دریافت لیست IPها از .env و تبدیل به لیست
CLEAN_IP_STR = os.getenv("CLEAN_IP", "188.114.97.2")
CLEAN_IPS = [ip.strip() for ip in CLEAN_IP_STR.split(",") if ip.strip()]

# آدرس API برای دریافت IP تمیز (به عنوان نمونه از ircf.space استفاده شده)
CLEAN_IP_API = os.getenv("CLEAN_IP_API", "https://api.ircf.space/v1/host")

# آدرس سرور اصلی پنل شما برای جایگزینی در سابسکریپشن
PANEL_SERVER_IP = os.getenv("PANEL_SERVER_IP", "185.215.244.29")

WS_DOMAIN = os.getenv("WS_DOMAIN", "v2.sanatify.ir")
WS_PATH = os.getenv("WS_PATH", "/sanatify-safe/")
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

DISCOUNT_CODES = {
    "MOHARAM20": 20,
}

DB_FILE = "/root/bot_users.db"

# ==================== [ توابع دیتابیس ] ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id TEXT PRIMARY KEY,
            referred_by TEXT,
            invite_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "first_name" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    if "username" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "has_received_test" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN has_received_test INTEGER DEFAULT 0")
    if "has_received_referral_reward" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN has_received_referral_reward INTEGER DEFAULT 0")
    conn.commit()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_discounts (
            chat_id TEXT,
            discount_code TEXT,
            used_at TEXT,
            PRIMARY KEY (chat_id, discount_code)
        )
    ''')
    conn.commit()
    conn.close()

def fa_to_en_num(num_str):
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    en_digits = "0123456789"
    translation_table = str.maketrans(fa_digits, en_digits)
    return num_str.translate(translation_table)

def en_to_fa_num(num_str):
    en_digits = "0123456789"
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    translation_table = str.maketrans(en_digits, fa_digits)
    return num_str.translate(translation_table)

def parse_price(price_str):
    cleaned = price_str.replace(",", "").replace("،", "")
    en_str = fa_to_en_num(cleaned)
    return int(en_str)

def format_price(price_int):
    formatted_en = f"{price_int:,}"
    return en_to_fa_num(formatted_en)

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
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR IGNORE INTO user_discounts (chat_id, discount_code, used_at) VALUES (?, ?, ?)", (str(chat_id), str(code), now_str))
        conn.commit()
    except Exception as e:
        logging.error(f"Error marking discount as used: {e}")
    conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT referred_by, invite_count, has_received_test, has_received_referral_reward FROM users WHERE chat_id = ?", (str(chat_id),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "referred_by": row[0],
            "invite_count": row[1],
            "has_received_test": row[2],
            "has_received_referral_reward": row[3]
        }
    return None

def add_or_update_user(chat_id, first_name, username, referred_by=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT chat_id FROM users WHERE chat_id = ?", (str(chat_id),))
        exists = cursor.fetchone()
        if exists:
            cursor.execute("UPDATE users SET first_name = ?, username = ? WHERE chat_id = ?", (first_name, username, str(chat_id)))
        else:
            cursor.execute("""
                INSERT INTO users (chat_id, referred_by, invite_count, first_name, username, has_received_test, has_received_referral_reward)
                VALUES (?, ?, 0, ?, ?, 0, 0)
            """, (str(chat_id), referred_by, first_name, username))
        conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت/بروزرسانی کاربر در SQLite: {e}")
    conn.close()

def set_received_referral_reward(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET has_received_referral_reward = 1 WHERE chat_id = ?", (str(chat_id),))
        conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت دریافت پاداش دعوت در SQLite: {e}")
        raise e
    finally:
        conn.close()

def set_received_test(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET has_received_test = 1 WHERE chat_id = ?", (str(chat_id),))
        conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت وضعیت دریافت اکانت تست در SQLite: {e}")
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
    logging.info("=== [شروع پردازه پس‌زمینه بروزرسانی مشخصات کاربران...] ===")
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, referred_by FROM users WHERE first_name IS NULL")
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            logging.info("=== [تمام مشخصات کاربران در دیتابیس بروز هستند. پردازه متوقف شد] ===")
            return
        logging.info(f"=== [یافتن {len(rows)} کاربر بدون مشخصات. شروع بروزرسانی تدریجی...] ===")
        for chat_id, referred_by in rows:
            try:
                chat_obj = bot.get_chat(int(chat_id))
                first_name = chat_obj.first_name or "کاربر قدیمی"
                username = chat_obj.username
                add_or_update_user(chat_id, first_name, username, referred_by)
                logging.info(f"بروزرسانی موفق کاربر {chat_id} -> {first_name}")
            except Exception:
                add_or_update_user(chat_id, "کاربر قدیمی (آفلاین)", None, referred_by)
                logging.info(f"کاربر {chat_id} در دسترس نبود یا ربات را بلاک کرده است.")
            time.sleep(1.5)
        logging.info("=== [پردازه پس‌زمینه بروزرسانی مشخصات با موفقیت به پایان رسید] ===")
    except Exception as e:
        logging.error(f"خطای کلی در پردازه پس‌زمینه بروزرسانی: {e}")

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
        return "♾️ نامحدود (بدون انقضا)"
    current_time_ms = int(time.time() * 1000)
    if expiry_time_ms < 0:
        days = abs(expiry_time_ms) // (24 * 60 * 60 * 1000)
        return f"⏳ {days} روز (پس از اولین اتصال فعال می‌شود)"
    remaining_ms = expiry_time_ms - current_time_ms
    expiry_datetime = jdatetime.datetime.fromtimestamp(expiry_time_ms / 1000)
    shamsi_date_str = expiry_datetime.strftime('%Y/%m/%d ساعت %H:%M:%S')
    if remaining_ms <= 0:
        return f"❌ منقضی شده در تاریخ {shamsi_date_str}"
    remaining_days = remaining_ms / (24 * 60 * 60 * 1000)
    if remaining_days >= 1:
        days_int = int(remaining_days)
        return f"{shamsi_date_str} ({days_int} روز باقی‌مانده)"
    else:
        remaining_hours = int(remaining_ms / (60 * 60 * 1000))
        return f"{shamsi_date_str} ({remaining_hours} ساعت باقی‌مانده)"

def test_api():
    url = f"{PANEL_URL}/{SECRET_PATH}/panel/api/inbounds/list"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {API_TOKEN}"}, verify=False, timeout=20)
        logging.info("=== [ تست اتصال وب‌سرویس در استارت‌آپ ] ===")
        logging.info(f"STATUS: {r.status_code}")
        if r.status_code == 200:
            logging.info("توکن معتبر است و ارتباط با پنل برقرار شد. ✅")
        else:
            logging.error(f"BODY: {r.text}")
    except Exception as e:
        logging.error(f"خطا در تست API: {e}")

# ==================== [ تابع ساخت سابسکریپشن ] ====================
def create_vless_link(email, limit_gb, expiry_days=30):
    try:
        add_url = f"{PANEL_URL}/{SECRET_PATH}/panel/api/clients/add"
        client_uuid = str(uuid.uuid4())
        sub_id = generate_sub_id()
        traffic_bytes = int(limit_gb * 1024 * 1024 * 1024)
        expiry_time_ms = -int(expiry_days * 24 * 60 * 60 * 1000)
        payload = {
            "client": {
                "id": client_uuid,
                "email": email,
                "flow": "",
                "limitIp": 2,
                "totalGB": traffic_bytes,
                "expiryTime": expiry_time_ms,
                "enable": True,
                "tgId": 0,
                "subId": sub_id
            },
            "inboundIds": [INBOUND_ID]
        }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {API_TOKEN}"
        }
        response = requests.post(add_url, json=payload, headers=headers, timeout=10, verify=False)
        logging.info(f"وضعیت پاسخ پنل: {response.status_code}")
        
        if response.status_code == 200:
            res_data = response.json()
            if res_data.get('success') == True:
                sub_link = f"http://{PANEL_SERVER_IP}:8080/sub/{sub_id}"
                logging.info(f"سابسکریپشن ساخته شد: {sub_link}")
                return sub_link
        return None
    except Exception as e:
        logging.error(f"خطای سرور: {str(e)}")
        return None

# ==================== [ Flask Proxy برای سابسکریپشن با IPهای رندوم ] ====================
app = Flask(__name__)

def get_clean_ip():
    """تابع دریافت IP تمیز از API و در صورت شکست، استفاده از لیست فایل env"""
    try:
        response = requests.get(CLEAN_IP_API, timeout=5, verify=False)
        if response.status_code == 200:
            ip = response.text.strip()
            if ip:
                logging.info(f"🔹 [SUB] IP تمیز از API دریافت شد: {ip}")
                return ip
    except Exception as e:
        logging.warning(f"⚠️ [SUB] خطا در دریافت IP از API، استفاده از لیست ثابت: {e}")
    
    if CLEAN_IPS:
        selected = random.choice(CLEAN_IPS)
        logging.info(f"🔹 [SUB] IP از لیست ثابت انتخاب شد: {selected}")
        return selected
    
    return "188.114.97.2" # آی‌پی پیش‌فرض اضطراری

@app.route('/sub/<sub_id>', methods=['GET'])
def proxy_subscription(sub_id):
    """Endpoint برای دریافت سابسکریپشن با IPهای Cloudflare"""
    logging.info(f"🔸 [ROUTE] دریافت درخواست برای /sub/{sub_id}")
    
    try:
        panel_url = f"http://{PANEL_SERVER_IP}:2096/sub/{sub_id}"
        response = requests.get(panel_url, timeout=10, verify=False)
        
        if response.status_code == 200:
            content = response.text
            selected_ip = get_clean_ip()
            
            new_content = content.replace(PANEL_SERVER_IP, selected_ip)
            logging.info(f"✅ [SUB] IP جایگزین شد: {PANEL_SERVER_IP} -> {selected_ip}")
            
            return Response(new_content, mimetype='text/plain', headers={
                'Content-Disposition': f'attachment; filename=sub_{sub_id}.txt',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Expires': '0'
            })
        else:
            logging.error(f"❌ [SUB] خطا در دریافت سابسکریپشن: {response.status_code}")
            return Response("Error: Unable to fetch subscription", status=500)
            
    except Exception as e:
        logging.error(f"❌ [SUB] خطا در proxy subscription: {str(e)}")
        return Response("Error: Internal Server Error", status=500)

@app.route('/health', methods=['GET'])
def health_check():
    return Response("Flask is running - OK", status=200, mimetype='text/plain')

@app.errorhandler(404)
def not_found(e):
    return Response("404 Not Found", status=404)

def run_flask():
    try:
        logging.info("🚀 [FLASK] شروع اجرای Flask روی پورت 80...")
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        app.run(host='0.0.0.0', port=80, debug=False, threaded=True, use_reloader=False)
    except Exception as e:
        logging.error(f"❌ [FLASK] خطا در اجرای Flask (احتمالاً پورت 80 اشغال است): {e}")

# اجرای Flask در ترد پس‌زمینه
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
logging.info("✅ [MAIN] Proxy Subscription روی پورت 80 فعال شد")

# ================================================================================

def get_user_stats(chat_id):
    url = f"{PANEL_URL}/{SECRET_PATH}/panel/api/inbounds/list"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_TOKEN}"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        if response.status_code == 200:
            data = response.json()
            if data.get("success") == True:
                prefix_paid = f"user_{chat_id}_"
                prefix_gift = f"free_{chat_id}_"
                prefix_test = f"free_test_{chat_id}_"
                found_clients = []
                for inbound in data.get("obj", []):
                    if inbound.get("id") == INBOUND_ID:
                        for client in inbound.get("clientStats", []):
                            email = client.get("email", "")
                            if (email.startswith(prefix_paid) or
                                email.startswith(prefix_gift) or
                                email.startswith(prefix_test)):
                                found_clients.append(client)
                if not found_clients:
                    return None
                stats_text = (
                    f"📊 <b>گزارش وضعیت اشتراک‌های شما</b>\n\n"
                    f"👤 <b>شناسه کاربری:</b> <code>user_{chat_id}</code>\n"
                    f"📦 <b>تعداد اکانت‌های فعال شما:</b> {len(found_clients)} عدد\n\n"
                )
                for index, client in enumerate(found_clients, 1):
                    up = client.get("up", 0)
                    down = client.get("down", 0)
                    total = client.get("total", 0)
                    expiry_time = client.get("expiryTime", 0)
                    enable = client.get("enable", True)
                    email = client.get("email", "")
                    usage_gb = bytes_to_gb(up + down)
                    total_gb = bytes_to_gb(total) if total > 0 else "نامحدود"
                    status = "فعال 🟢" if enable else "قطع شده 🔴"
                    if email.startswith(prefix_paid):
                        acc_type = "💎 اکانت خریداری شده"
                    else:
                        acc_type = "🎁 اکانت هدیه / تست"
                    stats_text += (
                        f"🔹 <b>اشتراک شماره {index} ({acc_type}):</b>\n"
                        f"  ⚡ <b>وضعیت:</b> {status}\n"
                        f"  📥 <b>دانلود:</b> {bytes_to_gb(down)} GB\n"
                        f"  📤 <b>آپلود:</b> {bytes_to_gb(up)} GB\n"
                        f"  🔄 <b>مصرف کل:</b> {usage_gb} GB از {total_gb} GB\n"
                        f"  📅 <b>تاریخ انقضا:</b> {format_date_shamsi_with_countdown(expiry_time)}\n\n"
                    )
                stats_text += f"📌 برای به‌روزرسانی اطلاعات، مجدداً روی دکمه کلیک کنید."
                return stats_text
        return None
    except Exception as e:
        logging.error(f"خطا در استعلام حجم کاربر: {str(e)}")
        return None

# ==================== [ هندلرهای ربات ] ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_spammer(message.from_user.id):
        return
    chat_id = str(message.chat.id)
    text_parts = message.text.split()
    referrer_id = None
    if len(text_parts) > 1:
        referrer_id = text_parts[1]
    first_name = message.from_user.first_name or "کاربر ناشناس"
    username = message.from_user.username
    user_data = get_user(chat_id)
    is_new = False
    if not user_data:
        is_new = True
        if referrer_id == chat_id:
            referrer_id = None
        add_or_update_user(chat_id, first_name, username, referrer_id if referrer_id else None)
    else:
        add_or_update_user(chat_id, first_name, username, user_data.get("referred_by"))
    if is_new and referrer_id:
        update_invite_count(referrer_id, 1)
        inviter_data = get_user(referrer_id)
        if inviter_data:
            current_invites = inviter_data.get("invite_count", 0)
            has_received_reward = inviter_data.get("has_received_referral_reward", 0)
            try:
                if has_received_reward == 1:
                    bot.send_message(int(referrer_id), f"👤 <b>یک کاربر جدید با لینک شما عضو ربات شد!</b>\n\n🎯 تعداد کل دعوت‌های شما: <code>{current_invites}</code> نفر", parse_mode="HTML")
                else:
                    bot.send_message(int(referrer_id), f"👤 <b>یک کاربر جدید با لینک شما عضو ربات شد!</b>\n\n🎯 تعداد دعوت‌های شما: <code>{current_invites}/3</code>", parse_mode="HTML")
            except Exception as msg_error:
                logging.error(f"خطا در ارسال پیام اطلاع‌رسانی دعوت به میزبان: {msg_error}")
            if current_invites >= 3 and has_received_reward == 0:
                logging.info(f"=== [تایید شرط] دعوت‌کننده {referrer_id} واجد شرایط دریافت پاداش ۲ گیگی است. ===")
                try:
                    set_received_referral_reward(referrer_id)
                    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                    free_username = f"free_{referrer_id}_{suffix}"
                    bot.send_message(ADMIN_CHAT_ID, f"🎁 <b>یک لایسنس هدیه ۲ گیگی ۱ روزه</b> به طور خودکار برای کاربر `{referrer_id}` به دلیل دعوت ۳ نفر صادر شد.")
                    vless_link = create_vless_link(free_username, limit_gb=2, expiry_days=1)
                    if vless_link:
                        gift_text = (
                            "🎉 <b>تبریک فراوان! شما با موفقیت ۳ کاربر را به ربات دعوت کردید.</b>\n\n"
                            "🎁 <b>هدیه شما آماده است!</b> یک اکانت پرسرعت ۲ گیگابایتی با اعتبار ۱ روزه برای شما صادر شد:\n\n"
                            f"<code>{vless_link}</code>\n\n"
                            "📱 <b>راهنمای استفاده:</b>\n"
                            "در منوی اصلی ربات روی دکمه <b>📱 راهنمای سابسکریپشن</b> بزنید."
                        )
                        try:
                            bot.send_message(int(referrer_id), gift_text, parse_mode="HTML")
                        except Exception as send_error:
                            logging.error(f"خطا در ارسال پیام پاداش به کاربر {referrer_id}: {send_error}")
                    else:
                        logging.error(f"خطا در صدور لینک کلاینت از پنل برای کاربر {referrer_id}")
                except Exception as db_error:
                    logging.error(f"خطای جدی دیتابیس. عملیات صدور لایسنس لغو شد: {db_error}")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🛒 خرید اشتراک پرسرعت"),
        types.KeyboardButton("📊 وضعیت اشتراک من"),
        types.KeyboardButton("🎁 دعوت از دوستان (حجم رایگان)"),
        types.KeyboardButton("📱 راهنمای سابسکریپشن"),
        types.KeyboardButton("📚 راهنمای اتصال"),
        types.KeyboardButton("📞 پشتیبانی")
    )
    welcome_text = (
        f"سلام جناب {message.from_user.first_name} گرامی، به ربات هوشمند ما خوش آمدید. 🌹\n\n"
        "ما برای شما امن‌ترین و پرسرعت‌ترین پروتکل‌های لایت‌اسپید را تدارک دیده‌ایم.\n"
        "جهت تهیه اشتراک یا مانیتورینگ مصرف خود، از دکمه‌های زیر استفاده فرمایید."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "📱 راهنمای سابسکریپشن")
def subscription_guide(message):
    if is_spammer(message.from_user.id):
        return
    guide_text = (
        "📱 راهنمای کامل استفاده از سابسکریپشن\n\n"
        "✅ مزایای سابسکریپشن:\n"
        "• با هر بار آپدیت، جدیدترین آی‌پی‌های تمیز را دریافت می‌کنید\n"
        "• نیازی به دریافت کانفیگ جدید نیست\n"
        "• حجم و تاریخ انقضا به صورت خودکار نمایش داده می‌شود\n\n"
        "🔧 نحوه اضافه کردن در v2rayNG:\n"
        "۱. برنامه v2rayNG را باز کنید\n"
        "۲. روی منوی سه خط (☰) بالا سمت راست بزنید\n"
        "۳. گزینه Subscription group setting را انتخاب کنید\n"
        "۴. روی آیکون + (بالا سمت راست) بزنید\n"
        "۵. در صفحه باز شده:\n"
        "   • در کادر remarks یک نام دلخواه بنویسید\n"
        "   • در کادر Optional URL لینک سابسکریپشن را پیست کنید\n"
        "   • گزینه Enable update را روشن کنید\n"
        "   • ⚠️ مهم: گزینه Allow insecure HTTP address را حتماً روشن کنید\n"
        "۶. روی تیک (✓) بالا سمت راست بزنید تا ذخیره شود\n"
        "۷. با دکمه بازگشت به صفحه اصلی برگردید\n"
        "۸. روی آیکون فلش چرخان (🔄) بزنید تا کانفیگ‌ها دانلود شوند\n"
        "۹. سرور را انتخاب کرده و دکمه V را بزنید\n\n"
        "📌 نکته مهم:\n"
        "هر زمان که آی‌پی‌های سرور تغییر کند یا کانفیگ جدیدی اضافه شود،\n"
        "با زدن دکمه آپدیت، همه چیز به‌طور خودکار دریافت می‌شود."
    )
    bot.send_message(message.chat.id, guide_text)

@bot.message_handler(func=lambda message: message.text == "📚 راهنمای اتصال")
def connection_guide(message):
    if is_spammer(message.from_user.id):
        return
    guide_text = (
        "📚 راهنمای جامع اتصال به شبکه پرسرعت ما\n\n"
        "لطفاً بر اساس سیستم‌عامل دستگاه خود، نرم‌افزار مربوطه را نصب کنید:\n\n"
        "🤖 سیستم‌عامل اندروید:\n"
        "۱. ابتدا نرم‌افزار v2rayNG را از گوگل‌پلی دانلود کنید.\n"
        "۲. لینکی که ربات برای شما فرستاده را کپی کنید.\n"
        "۳. وارد برنامه شوید، علامت مثبت + بالا را بزنید و گزینه Import config from clipboard را انتخاب کنید.\n"
        "۴. روی کانکشن اضافه شده کلیک کرده و دکمه اتصال در پایین را بزنید.\n\n"
        "🍏 سیستم‌عامل آیفون (iOS):\n"
        "۱. نرم‌افزار FoXray یا v2raybox را از اپ‌استور دانلود کنید.\n"
        "۲. لینک کپی‌شده را از طریق علامت + در برنامه پیست (Import) کنید.\n\n"
        "💻 سیستم‌عامل ویندوز (کامپیوتر):\n"
        "۱. برنامه v2rayN را دانلود و اجرا کرده و لینک را پیست کنید."
    )
    bot.send_message(message.chat.id, guide_text)

@bot.message_handler(func=lambda message: message.text == "🎁 دعوت از دوستان (حجم رایگان)")
def invite_friends_menu(message):
    if is_spammer(message.from_user.id, cooldown=2.0):
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎁 دریافت اکانت تست رایگان (۱ گیگ)", callback_data="get_test_config"),
        types.InlineKeyboardButton("🤝 دعوت از دوستان (۲ گیگ هدیه)", callback_data="get_referral_link")
    )
    bot.send_message(message.chat.id, "کاربر عزیز، شما می‌توانید یک بار اکانت تست دریافت کنید یا با دعوت دوستان حجم رایگان بیشتری بگیرید.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["get_test_config", "get_referral_link"])
def handle_free_features(call):
    chat_id = str(call.message.chat.id)
    if is_spammer(call.from_user.id, cooldown=2.0):
        try: bot.answer_callback_query(call.id, "⚠️ لطفاً اسپم نکنید!", show_alert=True)
        except: pass
        return
    if call.data == "get_test_config":
        user_data = get_user(chat_id)
        if not user_data:
            add_or_update_user(chat_id, call.from_user.first_name or "کاربر ناشناس", call.from_user.username)
            user_data = {"has_received_test": 0, "invite_count": 0, "has_received_referral_reward": 0, "referred_by": None}
        if user_data.get("has_received_test", 0) == 1:
            bot.answer_callback_query(call.id, "❌ شما قبلاً اکانت تست خود را دریافت کرده‌اید.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "⏳ در حال ساخت اکانت تست...")
        bot.edit_message_text("⏳ در حال صدور اکانت تست رایگان (۱ گیگابایت)... لطفاً صبر کنید.", call.message.chat.id, call.message.message_id)
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        test_username = f"free_test_{chat_id}_{suffix}"
        vless_link = create_vless_link(test_username, limit_gb=1, expiry_days=1)
        if vless_link:
            set_received_test(chat_id)
            success_text = (
                "🎉 <b>اکانت تست ۱ روزه شما با موفقیت صادر شد!</b>\n\n"
                "🎁 <b>لینک سابسکریپشن:</b>\n\n"
                f"<code>{vless_link}</code>\n\n"
                "📱 در منوی اصلی روی دکمه <b>📱 راهنمای سابسکریپشن</b> بزنید."
            )
            bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        else:
            bot.edit_message_text("❌ متأسفانه در حال حاضر امکان ساخت اکانت تست وجود ندارد. لطفاً بعداً تلاش کنید.", call.message.chat.id, call.message.message_id)
    elif call.data == "get_referral_link":
        user_data = get_user(chat_id) or {"invite_count": 0, "has_received_referral_reward": 0, "referred_by": None}
        invite_count = user_data.get("invite_count", 0)
        has_received_reward = user_data.get("has_received_referral_reward", 0)
        bot_info = bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={chat_id}"
        progress_text = f"👤 تعداد دعوت‌های فعلی شما: <code>{invite_count}/3</code>" if has_received_reward == 0 else f"👤 تعداد کل دعوت‌ها: <code>{invite_count}</code>"
        share_text = (
            "🎁 <b>طرح ویژه دعوت از دوستان (حجم رایگان)</b>\n\n"
            "با دعوت هر <b>۳ نفر</b> اول، یک اکانت پرسرعت <b>۲ گیگابایتی ۱ روزه</b> آنی دریافت کنید!\n\n"
            f"{progress_text}\n\n"
            "🔗 <b>لینک اختصاصی شما:</b>\n"
            f"<code>{ref_link}</code>"
        )
        bot.edit_message_text(share_text, call.message.chat.id, call.message.message_id, parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text == "📞 پشتیبانی")
def support_info(message):
    if is_spammer(message.from_user.id):
        return
    bot.send_message(message.chat.id, "✍️ در صورت اختلال یا نیاز به راهنمایی، با پشتیبانی در ارتباط باشید:\n\n🆔 @SpeedNet_VpnBot")

@bot.message_handler(func=lambda message: message.text == "📊 وضعیت اشتراک من")
def show_user_stats(message):
    if is_spammer(message.from_user.id, cooldown=5.0):
        try: bot.send_message(message.chat.id, "⚠️ لطفاً چند ثانیه صبور باشید.")
        except: pass
        return
    bot.send_message(message.chat.id, "⏳ در حال استعلام وضعیت اشتراک شما از سرور...")
    stats_text = get_user_stats(message.chat.id)
    if stats_text:
        bot.send_message(message.chat.id, stats_text, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "❌ شما در حال حاضر اشتراک فعالی ندارید.\nجهت خرید روی دکمه خرید کلیک کنید. 🛒")

@bot.message_handler(func=lambda message: message.text == "🛒 خرید اشتراک پرسرعت")
def select_plan(message):
    if is_spammer(message.from_user.id):
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, plan in PLANS.items():
        markup.add(types.InlineKeyboardButton(f"📦 {plan['name']} 👈 {plan['price']} تومان 💸", callback_data=f"buy_{key}"))
    bot.send_message(message.chat.id, "لطفاً پلن مورد نظر خود را انتخاب فرمایید:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def ask_discount_prompt(call):
    if is_spammer(call.from_user.id):
        return
    plan_key = call.data.split("_")[1]
    USER_SELECTED_PLAN[call.message.chat.id] = plan_key
    USER_APPLIED_DISCOUNT.pop(call.message.chat.id, None)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✍️ وارد کردن کد تخفیف", callback_data=f"apply_discount_{plan_key}"),
        types.InlineKeyboardButton("💳 ادامه بدون کد تخفیف", callback_data=f"skip_discount_{plan_key}")
    )
    bot.edit_message_text(
        f"🎯 پلن <b>{PLANS[plan_key]['name']}</b> انتخاب شد.\nآیا کد تخفیف دارید؟",
        call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("skip_discount_"))
def show_payment_card_no_discount(call):
    plan_key = call.data.split("_")[2]
    USER_SELECTED_PLAN[call.message.chat.id] = plan_key
    USER_APPLIED_DISCOUNT.pop(call.message.chat.id, None)
    render_payment_card(call.message, call.message.chat.id, plan_key)

@bot.callback_query_handler(func=lambda call: call.data.startswith("apply_discount_"))
def ask_for_discount_code(call):
    plan_key = call.data.split("_")[2]
    bot.edit_message_text("✍️ لطفاً کد تخفیف خود را ارسال کنید:", call.message.chat.id, call.message.message_id)
    dummy_message = call.message
    dummy_message.from_user = call.from_user
    bot.register_next_step_handler(dummy_message, process_discount_code, plan_key)

def process_discount_code(message, plan_key):
    chat_id = message.chat.id
    raw_text = message.text.strip() if message.text else ""
    code = fa_to_en_num(raw_text).upper()
    if code not in DISCOUNT_CODES:
        markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("✍️ تلاش مجدد", callback_data=f"apply_discount_{plan_key}"),
            types.InlineKeyboardButton("💳 ادامه بدون کد", callback_data=f"skip_discount_{plan_key}")
        )
        bot.send_message(chat_id, "❌ کد تخفیف معتبر نیست.", reply_markup=markup)
        return
    if has_user_used_discount_code(chat_id, code):
        bot.send_message(chat_id, "❌ شما قبلاً از این کد استفاده کرده‌اید.")
        return
    USER_APPLIED_DISCOUNT[chat_id] = code
    bot.send_message(chat_id, "✅ کد تخفیف اعمال شد!")
    render_payment_card(message, chat_id, plan_key)

def render_payment_card(message, chat_id, plan_key):
    plan = PLANS[plan_key]
    price_str = plan['price']
    applied_code = USER_APPLIED_DISCOUNT.get(chat_id)
    discount_text = ""
    if applied_code and applied_code in DISCOUNT_CODES:
        discount_percent = DISCOUNT_CODES[applied_code]
        original_price = parse_price(price_str)
        final_price = original_price - int(original_price * (discount_percent / 100))
        price_str = format_price(final_price)
        discount_text = f"🎁 <b>تخفیف:</b> <code>{applied_code}</code> (%{discount_percent})\n\n"
    payment_text = (
        f"💳 جهت فعال‌سازی <b>{plan['name']}</b>،\n"
        f"{discount_text}"
        f"مبلغ <b>{price_str} تومان</b> را واریز کنید:\n\n"
        f"💳 <code>{CARD_NUMBER}</code>\n"
        f"👤 به نام فرید صابونچیان\n\n"
        f"⚠️ تصویر فیش را ارسال کنید."
    )
    if isinstance(message, types.Message):
        bot.send_message(chat_id, payment_text, parse_mode="HTML")
    else:
        bot.edit_message_text(payment_text, chat_id, message.message_id, parse_mode="HTML")

@bot.message_handler(content_types=['photo'])
def handle_receipt(message):
    if is_spammer(message.from_user.id, cooldown=3.0):
        return
    file_id = message.photo[-1].file_id
    plan_key = USER_SELECTED_PLAN.get(message.chat.id, "20gb")
    full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
    username = f"(@{message.from_user.username})" if message.from_user.username else ""
    plan = PLANS[plan_key]
    price_str = plan['price']
    applied_code = USER_APPLIED_DISCOUNT.get(message.chat.id, "none")
    discount_text = ""
    if applied_code != "none" and applied_code in DISCOUNT_CODES:
        discount_percent = DISCOUNT_CODES[applied_code]
        original_price = parse_price(price_str)
        final_price = original_price - int(original_price * (discount_percent / 100))
        price_str = format_price(final_price)
        discount_text = f"\n🎁 تخفیف: {applied_code}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ تایید و صدور", callback_data=f"adm_approve_{message.chat.id}_{plan_key}_{applied_code}"),
        types.InlineKeyboardButton("❌ رد تراکنش", callback_data=f"adm_reject_{message.chat.id}")
    )
    caption_text = (
        f"📥 فیش از: <b>{full_name}</b> {username}\n"
        f"🆔 شناسه: <code>{message.chat.id}</code>\n"
        f"📦 پلن: {plan['name']}{discount_text}\n"
        f"💰 مبلغ: {price_str} تومان"
    )
    bot.send_photo(ADMIN_CHAT_ID, file_id, caption=caption_text, reply_markup=markup, parse_mode="HTML")
    bot.send_message(message.chat.id, "فیش دریافت شد. در صف تایید است... ⏳")

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def handle_admin_action(call):
    if str(call.from_user.id) != str(ADMIN_CHAT_ID):
        return
    data_parts = call.data.split("_")
    action = data_parts[1]
    target_user_id = data_parts[2]
    if action == "approve":
        plan_key = data_parts[3]
        discount_code = data_parts[4] if len(data_parts) > 4 else "none"
        bot.answer_callback_query(call.id, "در حال ساخت اکانت...")
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        unique_username = f"user_{target_user_id}_{suffix}"
        sub_link = create_vless_link(unique_username, limit_gb=PLANS[plan_key]['gb'], expiry_days=PLANS[plan_key]['days'])
        if sub_link:
            if discount_code != "none" and discount_code in DISCOUNT_CODES:
                mark_discount_used(target_user_id, discount_code)
            success_text = (
                f"پرداخت تایید شد! 🎉\n\n"
                f"🚀 <b>لینک سابسکریپشن:</b>\n\n"
                f"<code>{sub_link}</code>\n\n"
                f"📱 راهنما در منوی ربات <b>📱 راهنمای سابسکریپشن</b>"
            )
            bot.send_message(target_user_id, success_text, parse_mode="HTML")
            bot.send_message(ADMIN_CHAT_ID, "سابسکریپشن صادر شد. ✅")
        else:
            bot.send_message(ADMIN_CHAT_ID, "خطا در ساخت سابسکریپشن! ❌")
    elif action == "reject":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("❌ نامعتبر / تکراری", callback_data=f"rj_fake_{target_user_id}"),
            types.InlineKeyboardButton("❌ عدم واریز", callback_data=f"rj_nowork_{target_user_id}"),
            types.InlineKeyboardButton("❌ مغایرت مبلغ", callback_data=f"rj_mismatch_{target_user_id}")
        )
        bot.edit_message_caption("علت رد تراکنش را انتخاب کنید:", chat_id=ADMIN_CHAT_ID, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("rj_"))
def handle_rejection_reason(call):
    if str(call.from_user.id) != str(ADMIN_CHAT_ID):
        return
    data_parts = call.data.split("_")
    reason_type = data_parts[1]
    target_user_id = data_parts[2]
    reasons = {
        "fake": "تصویر فیش نامعتبر یا تکراری است.",
        "nowork": "مبلغی به حساب بانکی واریز نشده است.",
        "mismatch": "مبلغ واریزی با قیمت پلن مغایرت دارد."
    }
    selected_reason = reasons.get(reason_type, "عدم تطابق اطلاعات.")
    bot.send_message(target_user_id, f"❌ <b>پرداخت شما تایید نگردید.</b>\n\nعلت: {selected_reason}\n\nلطفاً فیش صحیح را ارسال کنید. 🙏", parse_mode="HTML")
    bot.edit_message_caption(f"تراکنش کاربر `{target_user_id}` رد شد.", chat_id=ADMIN_CHAT_ID, message_id=call.message.message_id, reply_markup=None)

@bot.message_handler(commands=['stats'])
def show_admin_stats(message):
    if str(message.chat.id) != str(ADMIN_CHAT_ID):
        return
    total_members = get_total_users()
    stats_msg = f"📊 <b>آمار اعضای ربات</b>\n\n👥 <b>کل اعضا:</b> <code>{total_members}</code> نفر\n\n"
    items = get_recent_users(20)
    for chat_id, data in items:
        first_name = (data.get("first_name") or "ناشناس").replace('<', '&lt;').replace('>', '&gt;')
        username = f" | @{data.get('username')}" if data.get("username") else ""
        ref_by = data.get("referred_by")
        ref_text = f"دعوت شده توسط: <code>{ref_by}</code>" if ref_by else "عضویت مستقیم"
        stats_msg += f"• <b>{first_name}</b>{username} | شناسه: <code>{chat_id}</code> ({ref_text})\n"
    bot.send_message(ADMIN_CHAT_ID, stats_msg, parse_mode="HTML")

@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    if str(message.chat.id) != str(ADMIN_CHAT_ID):
        return
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) < 2:
        bot.send_message(ADMIN_CHAT_ID, "⚠️ لطفا متن پیام را وارد کنید.\nمثال:\n`/broadcast سلام`", parse_mode="Markdown")
        return
    broadcast_text = text_parts[1]
    user_ids = get_all_user_ids()
    bot.send_message(ADMIN_CHAT_ID, f"⏳ در حال ارسال پیام به {len(user_ids)} کاربر...")
    success_count = 0
    fail_count = 0
    for user_id in user_ids:
        try:
            bot.send_message(int(user_id), broadcast_text)
            success_count += 1
        except Exception:
            fail_count += 1
    bot.send_message(ADMIN_CHAT_ID, f"📢 **ارسال همگانی پایان یافت!**\n🟢 موفق: `{success_count}`\n🔴 ناموفق: `{fail_count}`")

# ==================== [ اجرای نهایی ] ====================
init_db()
threading.Thread(target=sync_profiles_background, daemon=True).start()
test_api()
bot.infinity_polling(skip_pending=True)
