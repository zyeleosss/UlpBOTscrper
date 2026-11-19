import os
import re
import io
import zipfile
import sqlite3
import asyncio
import rarfile
from pathlib import Path
from datetime import datetime
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerEmpty
from telebot.async_telebot import AsyncTeleBot
from telebot import types as bot_types

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7922887920:AAENu-AGdVG4l8SQEdXCUzF9Y0TmYALnqa8")
API_ID = int(os.environ.get("API_ID", 37723049))
API_HASH = os.environ.get("API_HASH", "96cc79adf94fa7d7091223ac37521dc3")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BVtsOLMBu5BkwxfEmjGhgQgYSB82UZXelNrbYQoVL9SpeSgTBA4k58WmOcPpZ4RW9XJ5DfqA-Utg6l3IO7DVBEtHFn0pR_shrSDcc1Wj1U9nEpY5Uz0n5CdaVUDfBjKnJ3KNb0d7T1FeBlS_OY4s1af2AU_y00YqTAMi1AMGxj9wQFwRDZK2W086R7fjA11G6tvsa0prFT5krlTAeiwMvkczLzgAdbH8-jLiock8128g3l9K9j2rpSZcwT3UnEIsC2h9wd0c_aEyK4hnOQP6I_PntK-HAJVg8V_MyKrlpLqZPwlSpOTT9NA5E64SR4bLs283pf-hfSpqCDsixZ003iw5oGGtJaw=")

CREDIT = "@ZYELEO"
ADMIN_ID = 7240767926
DB_FILE = "combo_storage.db"
TEMP_DIR = Path("temp_downloads")
TEMP_DIR.mkdir(exist_ok=True)

bot = AsyncTeleBot(BOT_TOKEN)
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
user_search_cache = {}

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")          
    conn.execute("PRAGMA cache_size=-8000000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=30000000000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS combos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line TEXT UNIQUE,
            category TEXT,
            source TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_line ON combos(line)")
    conn.commit()
    conn.close()

init_db()

def save_to_db(lines, source_name):
    if not lines: return 0
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    added = 0
    batch = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 5 or not any(c in line for c in ":|,;"): continue
        line = re.sub(r"\s*[|;,]\s*", ":", line)
        batch.append((line, "user", source_name))
        if len(batch) >= 10000:
            cursor.executemany("INSERT OR IGNORE INTO combos (line, category, source) VALUES (?, ?, ?)", batch)
            added += cursor.rowcount
            batch = []
    if batch:
        cursor.executemany("INSERT OR IGNORE INTO combos (line, category, source) VALUES (?, ?, ?)", batch)
        added += cursor.rowcount
    conn.commit()
    conn.close()
    return added

def extract_lines(filepath):
    ext = filepath.suffix.lower()
    if ext == '.zip':
        try:
            with zipfile.ZipFile(filepath, 'r') as z:
                for info in z.infolist():
                    if info.is_dir() or info.file_size == 0: continue
                    with z.open(info) as member:
                        for line in io.TextIOWrapper(member, encoding='utf-8', errors='ignore'):
                            yield line
            return
        except Exception: pass
            
    elif ext in ['.rar', '.001']:
        try:
            with rarfile.RarFile(filepath, 'r') as r:
                for info in r.infolist():
                    if info.is_dir() or info.file_size == 0: continue
                    with r.open(info) as member:
                        for line in io.TextIOWrapper(member, encoding='utf-8', errors='ignore'):
                            yield line
            return
        except Exception: pass

    try:
        with filepath.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                yield line
    except Exception: pass

# ==================== TELEBOT FRONTEND ====================
@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    markup = bot_types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        bot_types.InlineKeyboardButton("📊 Stats", callback_data="stats"),
        bot_types.InlineKeyboardButton("📦 Export DB", callback_data="export_db")
    )
    text = (
        "🧠 **Smart AI ULP Bot**\n\n"
        "**Commands:**\n"
        "• Send any file → Reply `/store`\n"
        "• `/search domain.com` → Extract lines\n"
        "• `/autofind keyword` → Auto-find & send files\n"
        "• `/fulldb` → Backup database\n\n"
        f"💠 {CREDIT}"
    )
    await bot.reply_to(message, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(content_types=['document'])
async def handle_document(message):
    markup = bot_types.InlineKeyboardMarkup()
    markup.add(bot_types.InlineKeyboardButton("💾 Store to Database", callback_data=f"store_{message.message_id}"))
    await bot.reply_to(message, "📁 File received. Click below or reply `/store`.", reply_markup=markup)

@bot.message_handler(commands=['store'])
async def handle_store_command(message):
    if not message.reply_to_message or not message.reply_to_message.document:
        await bot.reply_to(message, "❌ Please reply to a file with `/store`.")
        return
    await process_file_to_db(message.reply_to_message, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('store_'))
async def handle_store_callback(call):
    await bot.answer_callback_query(call.id, "Processing file...")
    msg_id = int(call.data.split("_")[1])
    try:
        replied = await bot.get_message(call.message.chat.id, msg_id)
        await process_file_to_db(replied, call.message)
    except Exception as e:
        await bot.send_message(call.message.chat.id, f"❌ Error: {e}")

async def process_file_to_db(doc_message, reply_to_msg):
    doc = doc_message.document
    file_name = doc.file_name or "unknown.txt"
    
    status_msg = await bot.reply_to(reply_to_msg, f"⏳ Downloading `{file_name}`...", parse_mode='Markdown')
    file_info = await bot.get_file(doc.file_id)
    local_path = TEMP_DIR / file_name
    
    try:
        downloaded = await bot.download_file(file_info.file_path)
        with open(local_path, 'wb') as new_file:
            new_file.write(downloaded)
            
        await bot.edit_message_text(f"⚙️ Processing `{file_name}`...", chat_id=status_msg.chat.id, message_id=status_msg.message_id, parse_mode='Markdown')
        added = await asyncio.to_thread(save_to_db, list(extract_lines(local_path)), file_name)
        
        await bot.edit_message_text(f"✅ **Done!**\n📁 File: `{file_name}`\n➕ Added: `{added:,}`\n\n💠 {CREDIT}", chat_id=status_msg.chat.id, message_id=status_msg.message_id, parse_mode='Markdown')
    except Exception as e:
        await bot.edit_message_text(f"❌ Error: {e}", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
    finally:
        if local_path.exists(): local_path.unlink()

# ==================== SMART SEARCH ====================
@bot.message_handler(commands=['search'])
async def handle_search_command(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.reply_to(message, "❌ **Usage:** `/search domain.com`", parse_mode='Markdown')
        return
    
    query = parts[1].strip()
    status_msg = await bot.reply_to(message, f"🔍 Searching database for `{query}`...", parse_mode='Markdown')
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT line FROM combos WHERE line LIKE ?", (f"%{query}%",))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await bot.edit_message_text(f"❌ No results for `{query}`.", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
        return

    results = [row[0] for row in rows]
    user_search_cache[f"{message.chat.id}_{query}"] = results
    
    markup = bot_types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        bot_types.InlineKeyboardButton("📄 Log", callback_data=f"dl_log|{query}"),
        bot_types.InlineKeyboardButton("👤 User:Pass", callback_data=f"dl_userpass|{query}"),
        bot_types.InlineKeyboardButton("🌐 Domain:User:Pass", callback_data=f"dl_domain|{query}")
    )
    await bot.edit_message_text(f"✅ Found **{len(results):,}** lines.\nChoose format:", chat_id=status_msg.chat.id, message_id=status_msg.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("dl_"))
async def handle_format_download(call):
    await bot.answer_callback_query(call.id, "Generating file...")
    data_parts = call.data.split("|", 1)
    fmt = data_parts[0].replace("dl_", "")
    query = data_parts[1]
    
    cache_key = f"{call.message.chat.id}_{query}"
    if cache_key not in user_search_cache:
        await bot.send_message(call.message.chat.id, "❌ Session expired. Search again.")
        return

    results = user_search_cache[cache_key]
    filename = f"search_{re.sub(r'[^a-zA-Z0-9]', '_', query[:10])}_{fmt}.txt"
    out_path = TEMP_DIR / filename
    
    count = 0
    with open(out_path, 'w', encoding='utf-8') as f:
        for line in results:
            clean_line = re.sub(r'^https?://', '', line)
            parts = clean_line.split(':')
            if fmt == "log":
                f.write(line + "\n")
            elif fmt == "userpass" and len(parts) >= 3:
                f.write(f"{parts[-2]}:{parts[-1]}\n")
            elif fmt == "domain":
                f.write(clean_line + "\n")
            count += 1

    await client.send_file(
        call.message.chat.id, str(out_path), 
        caption=f"✅ {fmt.upper()} | `{query}` | {count:,} lines\n💠 {CREDIT}", 
        parse_mode='md'
    )
    if out_path.exists(): out_path.unlink()

# ==================== AUTO-FIND & SEND ====================
@bot.message_handler(commands=['autofind'])
async def handle_autofind(message):
    parts = message.text.split(maxsplit=1)
    keyword = parts[1].strip() if len(parts) > 1 else "ulp"
    
    status_msg = await bot.reply_to(message, f"🌍 AI Auto-Searching Telegram for `{keyword}`...\nThis might take a minute.", parse_mode='Markdown')
    
    try:
        result = await client(functions.messages.SearchGlobalRequest(
            q=keyword, filter=types.InputMessagesFilterDocument(), limit=10,
            min_date=None, max_date=None, offset_rate=0, offset_peer=InputPeerEmpty(), offset_id=0
        ))
        
        found_lines = set()
        
        for msg in result.messages:
            if msg.document and msg.file.name and msg.file.name.endswith(('.txt', '.log', '.zip', '.rar')):
                path = await msg.download_media()
                if path:
                    for line in extract_lines(Path(path)):
                        found_lines.add(line.strip())
                    os.remove(path)

        if not found_lines:
            await bot.edit_message_text(f"❌ No combos found in global search for `{keyword}`.", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
            return

        # Create final file and send directly to him
        out_path = TEMP_DIR / f"autofind_{keyword}.txt"
        with open(out_path, 'w', encoding='utf-8') as f:
            for line in found_lines:
                if line and any(c in line for c in ":|,;"):
                    f.write(line + "\n")
                    
        await bot.edit_message_text(f"✅ Found **{len(found_lines):,}** lines. Sending file...", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
        
        # Send directly to Admin ID using Telethon (bypasses Bot API limits)
        await client.send_file(
            ADMIN_ID, str(out_path),
            caption=f"🧠 AI Auto-Find: `{keyword}`\n📄 Lines: {len(found_lines):,}\n\n💠 {CREDIT}",
            parse_mode='md'
        )
        if out_path.exists(): out_path.unlink()
        
    except Exception as e:
        await bot.edit_message_text(f"❌ Search Error: {e}", chat_id=status_msg.chat.id, message_id=status_msg.message_id)

# ==================== EXPORT DB ====================
@bot.message_handler(commands=['fulldb'])
async def handle_fulldb(message):
    if not os.path.exists(DB_FILE):
        await bot.reply_to(message, "❌ DB not found.")
        return

    status_msg = await bot.reply_to(message, "⏳ Zipping DB...")
    zip_filename = f"Combo_DB_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = TEMP_DIR / zip_filename

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(DB_FILE, arcname=DB_FILE)
            if os.path.exists(f"{DB_FILE}-wal"):
                zipf.write(f"{DB_FILE}-wal", arcname=f"{DB_FILE}-wal")
        
        await client.send_file(
            ADMIN_ID, str(zip_path),
            caption=f"✅ Backup Complete\n📦 Size: {os.path.getsize(zip_path)/1024/1024:.1f} MB\n💠 {CREDIT}",
            parse_mode='md'
        )
        await bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e:
        await bot.edit_message_text(f"❌ Error: {e}", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
    finally:
        if zip_path.exists(): zip_path.unlink()

@bot.callback_query_handler(func=lambda call: call.data == "export_db")
async def handle_export_callback(call):
    await bot.answer_callback_query(call.id)
    await handle_fulldb(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "stats")
async def handle_stats(call):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM combos")
    total = cursor.fetchone()[0]
    conn.close()
    await bot.answer_callback_query(call.id)
    await bot.send_message(call.message.chat.id, f"📊 **Total Lines:** `{total:,}`", parse_mode='Markdown')

# ==================== SAVED MESSAGES AUTO-SCRAPER ====================
@client.on(events.NewMessage(chats='me'))
async def handle_saved_messages(event):
    if event.text:
        added = await asyncio.to_thread(save_to_db, event.text.split('\n'), "Saved Messages")
        if added: print(f"[+] Saved Msg: Added {added} lines.")
    elif event.document:
        filename = event.file.name or ""
        if filename.endswith(('.txt', '.log', '.zip', '.rar', '.001')):
            print(f"[+] Spawning background thread for: {filename}")
            asyncio.create_task(process_bg_file(event, filename))

async def process_bg_file(event, filename):
    path = await event.download_media(file=str(TEMP_DIR / filename))
    if path:
        print(f"[+] Extracting {filename}...")
        added = await asyncio.to_thread(save_to_db, list(extract_lines(Path(path))), f"TG: {filename}")
        os.remove(path)
        print(f"[+] ✅ {filename} finished! Added {added:,} lines.")

# ==================== MAIN ====================
async def main():
    print("Starting Telethon...")
    await client.start()
    print("Starting Telebot...")
    asyncio.create_task(bot.polling())
    print("[+] All systems online.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())