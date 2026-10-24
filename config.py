# ==============================================================================
# AccStore Session Manager — Configuration, Constants, State & Foundation
# ==============================================================================

"""
╔═══════════════════════════════════════════════════════════════╗
║         AccStore — Session Management Platform         ║
╚═══════════════════════════════════════════════════════════════╝

  Upload .session packs and run powerful operations on them.

  Setup:
    1. Visit https://my.telegram.org → Get APP_ID + APP_HASH
    2. Open @BotFather → /newbot → Get BOT_TOKEN
    3. Fill in the .env file with your credentials
    4. pip install -r requirements.txt
    5. python main.py
"""

# ─────────────────────────────────────────────────────────────────────────────
# Module Imports
# External libraries used by the platform.
# 
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ==============================================================================
# SECTION 1: CONCURRENT BATCH RUNNER
# ==============================================================================
# This utility lets the bot process thousands of active session tasks concurrently
# without blocking the event loop or causing the system to freeze.
async def gather_chunked(coro_gen, limit=30):
    pending = set()
    for coro in coro_gen:
        if len(pending) >= limit:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            # Surface exceptions from completed tasks so errors aren't silently lost
            for task in done:
                if task.exception() is not None:
                    logging.warning(f"[gather_chunked] Worker task failed: {task.exception()}")
        pending.add(asyncio.create_task(coro))
    if pending:
        done, _ = await asyncio.wait(pending)
        for task in done:
            if task.exception() is not None:
                logging.warning(f"[gather_chunked] Worker task failed: {task.exception()}")
import base64           # Encodes data into text (like a secret code writer)
import hashlib          # Creates unique fingerprints for files (like a file ID card)
import json             # Reads and writes data in a format computers understand
import os               # Talks to your computer's file system (folders, files)
import random
import string
import re               # Searches for patterns in text (like finding a phone number)
import shutil           # Copies, moves, and deletes folders
import sqlite3          # Reads small database files (where sessions are stored)
import struct           # Packs data into binary format (computer-friendly)
import tempfile         # Creates temporary folders that get cleaned up later
import time             # Keeps track of time (for timeouts and delays)
import zipfile          # Opens and creates ZIP files (compressed folders)
from datetime import datetime, timezone  # Gets the current date and time
from dotenv import load_dotenv  # Loads configuration from .env file
import contextlib
import logging
import asyncio

# Configure stdout logging for server consoles (like KataBump)
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("pyrogram.connection.transport.tcp.tcp").setLevel(logging.CRITICAL)

ROOT_DIR = "accstore_data"
BOT_LOG_PATH = os.path.join(ROOT_DIR, "bot.log")
try:
    os.makedirs(ROOT_DIR, exist_ok=True)
    import sys
    # Avoid duplicate lines when running inside screen / daemon piped to tee
    if sys.stdout and getattr(sys.stdout, "isatty", lambda: False)():
        _bot_file_handler = logging.FileHandler(BOT_LOG_PATH, encoding="utf-8")
        _bot_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
        logging.getLogger().addHandler(_bot_file_handler)
except Exception:
    pass


class AdminNotifierHandler(logging.Handler):
    """Intercepts unhandled bot errors and forwards them to the admin."""
    _last_sent = 0  # Rate-limit: prevent flooding Telegram with error reports
    _cooldown = 10  # Minimum seconds between notifications

    def emit(self, record):
        if record.levelno >= logging.ERROR and not any(record.name.startswith(x) for x in ["asyncio", "pyrogram", "telethon"]):
            import time as _time
            now = _time.monotonic()
            if now - AdminNotifierHandler._last_sent < AdminNotifierHandler._cooldown:
                return  # Rate-limited, skip
            try:
                err_msg = self.format(record)
                # Suppress noisy SQLite transient errors — not real crashes
                err_lower = err_msg.lower()
                if "database is locked" in err_lower or "already exists" in err_lower:
                    return
                AdminNotifierHandler._last_sent = now
                if ADMIN_IDS:
                    try:
                        loop = asyncio.get_running_loop()
                        notify_msg = "⚠️ **Bot Crash / Unhandled Error**\n\n`" + err_msg[:3900] + "`"
                        for admin_id in ADMIN_IDS:
                            async def _safe_send(_uid=admin_id, _msg=notify_msg):
                                try:
                                    await app.send_message(_uid, _msg)
                                except Exception:
                                    pass
                            loop.create_task(_safe_send())
                    except RuntimeError:
                        pass
            except Exception:
                pass

# logging.getLogger().addHandler(AdminNotifierHandler())

META_LOCK = asyncio.Lock()
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("telethon").setLevel(logging.ERROR)

# Load .env file (looks in current dir and parent dirs)
load_dotenv()

# --- Python 3.14 / Pyrogram Fix ---
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# --- Pyrogram: One of the two "remote controls" for talking to Telegram ---
from pyrogram import Client, filters, StopPropagation
from pyrogram.enums import ChatType
from pyrogram.errors import (
    AuthKeyDuplicated,       # The login key was used somewhere else
    AuthKeyUnregistered,     # The login key is no longer valid
    BadRequest,              # Telegram didn't like what we sent
    ChannelPrivate,          # The group/channel is private, can't access
    FloodWait,               # Telegram says "slow down, you're going too fast!"
    PasswordHashInvalid,     # The 2FA password was wrong
    PeerIdInvalid,           # The chat/user ID doesn't exist
    SessionPasswordNeeded,   # This account has 2FA enabled, need password
    UserDeactivated,         # The account has been deleted
    UserDeactivatedBan,      # The account was banned by Telegram
    UserRestricted,          # The account is restricted
    PeerFlood as PyroPeerFlood,
    UserBannedInChannel as PyroUserBannedInChannel,
    ChatWriteForbidden as PyroChatWriteForbidden,
    InviteRequestSent,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionRevoked,
)
from pyrogram.raw.functions.channels import LeaveChannel    # Leave a group/channel
from pyrogram.raw.functions.messages import DeleteHistory   # Delete chat history
from pyrogram.raw import functions                          # Low-level Telegram commands
from pyrogram.storage import Storage                         # Where Pyrogram stores session data
try:
    from pyrogram.storage import FileStorage as _SessionStorage   # Original Pyrogram
except ImportError:
    from pyrogram.storage import SQLiteStorage as _SessionStorage  # Kurigram fork
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup  # Button menus

# --- Telethon: The second "remote control" for talking to Telegram ---
from telethon.errors import (
    AuthKeyError as TeleAuthError,                              # Login key error (Telethon version)
    AuthKeyNotFound as TeleAuthKeyNotFound,                      # Server forgot the auth key
    SecurityError as TeleSecurityError,                          # Crypto/security failures
    UserDeactivatedError as TeleUserDeactivatedError,            # Account deleted (Telethon version)
    SessionPasswordNeededError as TeleSessionPasswordNeededError,  # 2FA needed (Telethon version)
    FloodWaitError,
    AuthKeyUnregisteredError as TeleAuthKeyUnregisteredError,
    UserDeactivatedBanError as TeleUserDeactivatedBanError,
    UserRestrictedError as TeleUserRestrictedError,
    FrozenMethodInvalidError as TeleFrozenMethodInvalidError,
    PeerFloodError as TelePeerFloodError,
    UserBannedInChannelError as TeleUserBannedInChannelError,
    ChatWriteForbiddenError as TeleChatWriteForbiddenError,
    InviteRequestSentError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError as TelePasswordHashInvalidError,
    SessionRevokedError as TeleSessionRevokedError,
)
from telethon.crypto import AuthKey              # The secret key that proves you're logged in
from telethon.sessions import SQLiteSession, StringSession      # Where Telethon stores session data
from telethon.sync import TelegramClient as TelethonClient  # Telethon's main connection tool
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest, UpdateProfileRequest as TeleUpdateProfileRequest, GetPasswordRequest  # Device management + profile
from telethon.tl.functions.users import GetFullUserRequest as TeleGetFullUserRequest  # Full user info
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, DeleteChatUserRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import Chat, Channel
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonChildAbuse,
    InputReportReasonCopyright,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails,
)

# Monkey-patch CallbackQuery.answer to safely catch and ignore QueryIdInvalid/RPCError
from pyrogram.types import CallbackQuery
from pyrogram.errors import RPCError, QueryIdInvalid

_original_callback_answer = CallbackQuery.answer
async def _safe_callback_answer(self, *args, **kwargs):
    try:
        return await _original_callback_answer(self, *args, **kwargs)
    except (QueryIdInvalid, RPCError, TimeoutError, asyncio.TimeoutError, OSError) as e:
        logging.debug(f"Ignored error in CallbackQuery.answer: {e}")
CallbackQuery.answer = _safe_callback_answer

# Monkey-patch SQLiteStorage.update_peers to safely ignore 'closed database' errors during teardown
import pyrogram.storage.sqlite_storage as _pyro_sqlite_mod
SQLiteStorage = _pyro_sqlite_mod.SQLiteStorage
USERNAMES_SCHEMA = getattr(_pyro_sqlite_mod, "USERNAMES_SCHEMA", "")
UPDATE_STATE_SCHEMA = getattr(_pyro_sqlite_mod, "UPDATE_STATE_SCHEMA", "")
TEST = getattr(_pyro_sqlite_mod, "TEST", {2: "149.154.175.40"})
PROD = getattr(_pyro_sqlite_mod, "PROD", {2: "149.154.167.51"})

def _get_pyro_conn(storage):
    try:
        return storage.conn
    except Exception:
        return getattr(storage, "_conn", None)

_original_update_peers = SQLiteStorage.update_peers
async def _safe_update_peers(self, peers):
    for _retry in range(3):
        try:
            conn = _get_pyro_conn(self)
            if conn:
                await _original_update_peers(self, peers)
            return
        except sqlite3.OperationalError as e:
            if ("locked" in str(e).lower() or "busy" in str(e).lower()) and _retry < 2:
                await asyncio.sleep(0.05 * (_retry + 1))
                continue
            return
        except sqlite3.ProgrammingError:
            return
        except Exception:
            return
SQLiteStorage.update_peers = _safe_update_peers

# Monkey-patch SQLiteStorage.update to safely handle schema migrations without crashing on pre-existing columns/tables
if hasattr(SQLiteStorage, "update"):
    _orig_pyro_storage_update = SQLiteStorage.update

    async def _safe_pyro_storage_update(self):
        try:
            version = await self.version()
        except Exception:
            version = getattr(self, "VERSION", 7)

        if version is None:
            version = getattr(self, "VERSION", 7)

        if version == 1:
            with self.conn:
                try:
                    self.conn.execute("DELETE FROM peers;")
                except Exception:
                    pass
            version += 1

        if version == 2:
            with self.conn:
                try:
                    self.conn.execute("ALTER TABLE sessions ADD api_id INTEGER;")
                except Exception:
                    pass
            version += 1

        if version == 3:
            with self.conn:
                try:
                    if USERNAMES_SCHEMA:
                        self.conn.executescript(USERNAMES_SCHEMA)
                except Exception:
                    pass
            version += 1

        if version == 4:
            with self.conn:
                try:
                    if UPDATE_STATE_SCHEMA:
                        self.conn.executescript(UPDATE_STATE_SCHEMA)
                except Exception:
                    pass
            version += 1

        if version == 5:
            with self.conn:
                try:
                    self.conn.execute("CREATE INDEX idx_usernames_id ON usernames (id);")
                except Exception:
                    pass
            version += 1

        if version == 6:
            try:
                is_test = await self.test_mode()
                curr_dc = await self.dc_id()
            except Exception:
                is_test = False
                curr_dc = 2

            if is_test:
                address = TEST.get(curr_dc, "149.154.175.40") if isinstance(TEST, dict) else "149.154.175.40"
                port = 80
            else:
                address = PROD.get(curr_dc, "149.154.167.51") if isinstance(PROD, dict) else "149.154.167.51"
                port = 443

            with self.conn:
                try:
                    self.conn.execute("ALTER TABLE sessions ADD server_address TEXT;")
                except Exception:
                    pass
                try:
                    self.conn.execute("ALTER TABLE sessions ADD port INTEGER;")
                except Exception:
                    pass
                try:
                    self.conn.execute("UPDATE sessions SET server_address = ?;", (address,))
                    self.conn.execute("UPDATE sessions SET port = ?;", (port,))
                except Exception:
                    pass

            version += 1

        try:
            target_version = max(version, getattr(self, "VERSION", 7))
            cur = self.conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS version (number INTEGER PRIMARY KEY)")
            cur.execute("SELECT number FROM version LIMIT 1")
            if cur.fetchone() is None:
                cur.execute("INSERT INTO version (number) VALUES (?)", (target_version,))
            else:
                cur.execute("UPDATE version SET number = ?", (target_version,))
            self.conn.commit()
        except Exception:
            pass

    SQLiteStorage.update = _safe_pyro_storage_update

# Monkey-patch FileStorage & SQLiteStorage open and update to use safe timeouts,
# handle schema migrations safely (no duplicate column errors), and enforce WAL mode without blocking VACUUM
_orig_pyro_storage_open = SQLiteStorage.open

async def _safe_pyro_storage_open(self):
    if getattr(self, 'session_string', None) or getattr(self, 'in_memory', False):
        await _orig_pyro_storage_open(self)
        return

    from pathlib import Path
    path = self.database
    file_exists = isinstance(path, Path) and path.is_file()

    old_conn = _get_pyro_conn(self)
    if old_conn:
        try:
            old_conn.close()
        except Exception:
            pass
        if hasattr(self, "_conn"):
            self._conn = None
    self.conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    try:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass

    if file_exists:
        res = self.update()
        if asyncio.iscoroutine(res):
            await res
    else:
        res = self.create()
        if asyncio.iscoroutine(res):
            await res

    try:
        self.conn.commit()
    except Exception:
        pass

SQLiteStorage.open = _safe_pyro_storage_open

try:
    import pyrogram.storage.file_storage as _pyro_file_mod
    _FileStorage = getattr(_pyro_file_mod, "FileStorage", None)
    if _FileStorage:
        def _safe_file_storage_update(self):
            try:
                version = self.version()
            except Exception:
                version = 1

            if version == 1:
                with self.conn:
                    try:
                        self.conn.execute("DELETE FROM peers")
                    except Exception:
                        pass
                version += 1

            if version == 2:
                with self.conn:
                    try:
                        self.conn.execute("ALTER TABLE sessions ADD api_id INTEGER")
                    except Exception:
                        pass
                version += 1

            try:
                target_version = max(version, 7)
                cur = self.conn.cursor()
                cur.execute("CREATE TABLE IF NOT EXISTS version (number INTEGER PRIMARY KEY)")
                cur.execute("SELECT number FROM version LIMIT 1")
                if cur.fetchone() is None:
                    cur.execute("INSERT INTO version (number) VALUES (?)", (target_version,))
                else:
                    cur.execute("UPDATE version SET number = ?", (target_version,))
                self.conn.commit()
            except Exception:
                pass

        _FileStorage.update = _safe_file_storage_update
        _FileStorage.open = _safe_pyro_storage_open
except Exception:
    pass



tele_reasons = {
    "spam": InputReportReasonSpam(),
    "violence": InputReportReasonViolence(),
    "pornography": InputReportReasonPornography(),
    "child_abuse": InputReportReasonChildAbuse(),
    "copyright": InputReportReasonCopyright(),
    "other": InputReportReasonOther(),
    "fake": InputReportReasonFake(),
    "drugs": InputReportReasonIllegalDrugs(),
    "personal_details": InputReportReasonPersonalDetails(),
}

# ==============================================================================
# SECTION 2: SYSTEM CREDENTIALS AND FALLBACK CONFIGURATION
# ==============================================================================
# Loaded from the local .env configuration file. Keeps credentials secure.
# Falls back to default values if the .env file is missing.
API_ID_RAW = os.getenv("API_ID")
if not API_ID_RAW:
    raise ValueError("API_ID is missing from .env file")
API_ID = int(API_ID_RAW)

API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise ValueError("API_HASH is missing from .env file")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing from .env file")

# ─────────────────────────────────────────────────────────────────────────────
# Platform Customization
# ─────────────────────────────────────────────────────────────────────────────
PLATFORM_NAME = os.getenv("PLATFORM_NAME", "AccStore Session Manager")
# Ultra-Safe Anti-Freeze / Anti-Revoke concurrency policy:
# Never hammer Telegram with high concurrent connections from a single IP.
# Strictly satisfies: "if process slow then no worry but accounts must not freeze or revoke".
_raw_concurrency = int(os.getenv("MAX_CONCURRENCY", "2"))
MAX_CONCURRENCY = max(1, min(_raw_concurrency, 2))
SAFE_CONCURRENCY = max(1, min(MAX_CONCURRENCY, 2))
CONNECT_TIMEOUT = int(os.getenv("CONNECT_TIMEOUT", "20"))
SKIP_SPAMBOT = os.getenv("SKIP_SPAMBOT", "False").lower() == "true"

# Global connection limiter to prevent IP flagging across all 500+ users
GLOBAL_MAX_CONNECTIONS = max(25, int(os.getenv("GLOBAL_MAX_CONNECTIONS", "25")))
GLOBAL_CONNECTION_SEM = asyncio.Semaphore(GLOBAL_MAX_CONNECTIONS)

# External links (displayed on the main /start menu if set)
def _clean_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("@"):
        return f"https://t.me/{url[1:]}"
    if not (url.startswith("http://") or url.startswith("https://")):
        if "/" not in url and "." not in url:
            return f"https://t.me/{url}"
        return f"https://{url}"
    return url

CHANNEL_LINK = _clean_url(os.getenv("CHANNEL_LINK"))
SUPPORT_LINK = _clean_url(os.getenv("SUPPORT_LINK"))

# Access restriction: comma-separated user IDs. If empty, anyone can use the bot.
_raw_admins = (os.getenv("ADMIN_IDS") or "").strip()
ADMIN_IDS = [int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit()]

# ==============================================================================
# SECTION 3: SYSTEM-WIDE PROXY TRANSLATOR
# ==============================================================================
# Supports Socks5, Socks4, and HTTP protocols. Used to make session operations
# bypass local firewall limits and run under localized network regions.
_PROXY_TYPE = (os.getenv("PROXY_TYPE") or "").strip().lower()
_PROXY_HOST = (os.getenv("PROXY_HOST") or "").strip()
_PROXY_PORT = (os.getenv("PROXY_PORT") or "").strip()
_PROXY_USER = (os.getenv("PROXY_USER") or "").strip() or None
_PROXY_PASS = (os.getenv("PROXY_PASS") or "").strip() or None

# ─────────────────────────────────────────────────────────────────────────────
# Device Identity Configuration
# ─────────────────────────────────────────────────────────────────────────────
DEVICE_MODEL = (os.getenv("DEVICE_MODEL") or "").strip()
SYSTEM_VERSION = (os.getenv("SYSTEM_VERSION") or "").strip()
_RAW_APP_NAME = (os.getenv("APP_NAME") or "").strip()
_RAW_APP_VERSION = (os.getenv("APP_VERSION") or "").strip()
APP_VERSION = f"{_RAW_APP_NAME} {_RAW_APP_VERSION}".strip()



SAFE_DEVICE_MODEL = "Samsung Galaxy S23 Ultra"
SAFE_SYSTEM_VERSION = "Android 13 (API 33)"
SAFE_APP_VERSION = "10.14.5 (4589)"
SAFE_LANG_CODE = "en"
SAFE_SYSTEM_LANG_CODE = "en"

def _get_device_params_for_session(workdir, session_name):
    """Load authentic device params from .json if available, or realistic mobile defaults."""
    if workdir and session_name:
        candidates = [
            os.path.join(workdir, session_name + ".json"),
            os.path.join(workdir, session_name.replace("+", "") + ".json"),
            os.path.join(workdir, "+" + session_name + ".json"),
        ]
        m = re.search(r'(\d{7,15})', session_name)
        if m:
            candidates.append(os.path.join(workdir, m.group(1) + ".json"))
            candidates.append(os.path.join(workdir, "+" + m.group(1) + ".json"))

        for candidate in candidates:
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    dev = data.get("device") or data.get("device_model")
                    sys_v = data.get("sdk") or data.get("system_version")
                    app_v = data.get("app_version")
                    app_id = data.get("app_id")
                    app_hash = data.get("app_hash")
                    lang = data.get("lang_code") or "en"
                    sys_lang = data.get("system_lang_code") or "en"
                    
                    params = {}
                    if dev: params["device_model"] = str(dev)
                    if sys_v: params["system_version"] = str(sys_v)
                    if app_v: params["app_version"] = str(app_v)
                    if app_id: params["api_id"] = int(app_id)
                    if app_hash: params["api_hash"] = str(app_hash)
                    params["lang_code"] = str(lang)
                    params["system_lang_code"] = str(sys_lang)
                    if params:
                        return params
                except Exception:
                    pass
    
    # Safe mobile defaults that will NEVER trigger Telegram anti-fraud freezing
    return {
        "device_model": DEVICE_MODEL if (DEVICE_MODEL and DEVICE_MODEL.lower() not in ("accstore", "linuxkali096")) else SAFE_DEVICE_MODEL,
        "system_version": SYSTEM_VERSION if (SYSTEM_VERSION and "arch" not in SYSTEM_VERSION.lower()) else SAFE_SYSTEM_VERSION,
        "app_version": APP_VERSION if (APP_VERSION and "anonymous" not in APP_VERSION.lower()) else SAFE_APP_VERSION,
        "lang_code": SAFE_LANG_CODE,
        "system_lang_code": SAFE_SYSTEM_LANG_CODE
    }

def _build_pyro_proxy():
    """Build proxy dict for Pyrogram Client, or None if no proxy configured."""
    if not _PROXY_TYPE or _PROXY_TYPE == "none" or not _PROXY_HOST or not _PROXY_PORT:
        return None
    scheme_map = {"socks5": "socks5", "socks4": "socks4", "http": "http"}
    scheme = scheme_map.get(_PROXY_TYPE)
    if not scheme:
        return None
    proxy = {"scheme": scheme, "hostname": _PROXY_HOST, "port": int(_PROXY_PORT)}
    if _PROXY_USER:
        proxy["username"] = _PROXY_USER
    if _PROXY_PASS:
        proxy["password"] = _PROXY_PASS
    return proxy


def _build_tele_proxy():
    """Build proxy tuple for Telethon (python-socks format), or None."""
    if not _PROXY_TYPE or _PROXY_TYPE == "none" or not _PROXY_HOST or not _PROXY_PORT:
        return None
    import socks
    type_map = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    proxy_type = type_map.get(_PROXY_TYPE)
    if not proxy_type:
        return None
    return (proxy_type, _PROXY_HOST, int(_PROXY_PORT), True, _PROXY_USER, _PROXY_PASS)


# Telegram accounts connect DIRECTLY via VPS (no proxy for accounts)
PYRO_PROXY = None
TELE_PROXY = None

# ─────────────────────────────────────────────────────────────────
# DEDICATED IMAP PROXY POOL (EMAIL VERIFICATION / OTP ENGINE)
# ─────────────────────────────────────────────────────────────────
# Proxies are configured exclusively for IMAP mail retrieval (Gmail, Outlook, etc.)
# All Telegram account operations run directly on VPS without proxy.
try:
    from proxy_manager import proxy_pool, ProxyNode
except Exception:
    proxy_pool = None
    ProxyNode = None

# ─────────────────────────────────────────────────────────────────
# FLOODWAIT DEDICATED FAILOVER PROXY
# ─────────────────────────────────────────────────────────────────
# Dedicated residential proxy used exclusively when FloodWait / IP rate-limit occurs.
FLOODWAIT_PROXY_HOST = (os.getenv("FLOODWAIT_PROXY_HOST") or "").strip()
FLOODWAIT_PROXY_PORT = int((os.getenv("FLOODWAIT_PROXY_PORT") or "443").strip())
FLOODWAIT_PROXY_USER = (os.getenv("FLOODWAIT_PROXY_USER") or "").strip() or None
FLOODWAIT_PROXY_PASS = (os.getenv("FLOODWAIT_PROXY_PASS") or "").strip() or None

def get_floodwait_pyro_proxy(session_id=None, force_pool=False):
    """
    Get proxy configuration for Pyrogram to bypass FloodWait rate limit.
    Rule: Proxies are ONLY used when FloodWait is hit. Direct connection by default.
    Primary: Dedicated residential proxy from FLOODWAIT_PROXY_HOST.
    Fallback: Alive rotating proxy from proxy_pool if primary fails or force_pool is True.
    """
    if FLOODWAIT_PROXY_HOST and not force_pool:
        return {
            "scheme": "http",
            "hostname": FLOODWAIT_PROXY_HOST,
            "port": FLOODWAIT_PROXY_PORT,
            "username": FLOODWAIT_PROXY_USER,
            "password": FLOODWAIT_PROXY_PASS,
        }
    try:
        from proxy_manager import proxy_pool
        if proxy_pool and proxy_pool.total_count > 0:
            node = proxy_pool.get_node_for_session(session_id=session_id)
            if node:
                return node.to_pyro_dict()
    except Exception:
        pass
    return None


def get_floodwait_tele_proxy(session_id=None, force_pool=False):
    """
    Get proxy tuple for Telethon to bypass FloodWait rate limit.
    Rule: Proxies are ONLY used when FloodWait is hit. Direct connection by default.
    Primary: Dedicated residential proxy from FLOODWAIT_PROXY_HOST (HTTP CONNECT format).
    Fallback: Alive rotating proxy from proxy_pool if primary fails or force_pool is True.
    """
    try:
        import python_socks
        ptype = python_socks.ProxyType.HTTP
    except ImportError:
        try:
            import socks
            ptype = socks.HTTP
        except ImportError:
            ptype = 2

    if FLOODWAIT_PROXY_HOST and not force_pool:
        return (
            ptype,
            FLOODWAIT_PROXY_HOST,
            FLOODWAIT_PROXY_PORT,
            True,
            FLOODWAIT_PROXY_USER,
            FLOODWAIT_PROXY_PASS,
        )
    try:
        from proxy_manager import proxy_pool
        if proxy_pool and proxy_pool.total_count > 0:
            node = proxy_pool.get_node_for_session(session_id=session_id)
            if node:
                return (
                    ptype,
                    node.host,
                    node.port,
                    True,
                    node.user,
                    node.password,
                )
    except Exception:
        pass
    return None


FLOODWAIT_PYRO_PROXY = get_floodwait_pyro_proxy()
FLOODWAIT_TELE_PROXY = get_floodwait_tele_proxy()

# ─────────────────────────────────────────────────────────────────────────────
# Runtime Configuration
# 
# 
# ─────────────────────────────────────────────────────────────────────────────


ROOT_DIR = "accstore_data"
PACKS_DIR = os.path.join(ROOT_DIR, "packs")       # Folder where uploaded zips live
META_FILE = os.path.join(ROOT_DIR, "registry.json")  

# This message appears when an account has been permanently banned by Telegram
DEAD_MESSAGE = (
    "Your account was blocked for violations of the Telegram Terms of Service "
    "based on user reports confirmed by our moderators."
)

# Bot used to check how old an account is
AGE_TARGET_BOT = "tgdnabot"

# Pattern to find the year an account was created (e.g., "Created: 2019" or "**- Created:** `2019`")
AGE_REGEX = re.compile(r"Created.*?(\d{4})", re.IGNORECASE)

# Pattern to find a 5-digit OTP code in messages (e.g., "12345")
OTP_REGEX = re.compile(r"\b(\d{5})\b")

# Chats that should NEVER be deleted when clearing chats
# (important Telegram system accounts)
KEEP_USERNAMES = {"spambot", "tgdnabot", "telegram"}
KEEP_IDS = {777000, 42777, 34599577}  # 777000 = Telegram's own account

# IPv6 toggle — set to True only if VPS has a public IPv6 address
USE_IPV6 = os.environ.get("USE_IPV6", "False").lower() in ("true", "1", "t")

# Telegram's data center addresses around the world
# (like different offices of Telegram in different countries)
if USE_IPV6:
    DC_TABLE = {
        1: ("2001:b28:f23d:f001::a", 443),    # DC1 (IPv6)
        2: ("2001:67c:4e8:f002::a", 443),     # DC2 (IPv6)
        3: ("2001:b28:f23d:f003::a", 443),    # DC3 (IPv6)
        4: ("2001:67c:4e8:f004::a", 443),     # DC4 (IPv6)
        5: ("2001:b28:f23f:f005::a", 443),    # DC5 (IPv6)
    }
else:
    DC_TABLE = {
        1: ("149.154.175.53", 443),    # DC1
        2: ("149.154.167.50", 443),    # DC2 (most common)
        3: ("149.154.175.100", 443),   # DC3
        4: ("149.154.167.91", 443),    # DC4
        5: ("91.108.56.130", 443),     # DC5
    }

# List of errors that mean "this account is dead/invalid, skip it"
INVALID_EXCEPTIONS = (
    AuthKeyDuplicated,
    AuthKeyUnregistered,
    UserDeactivated,
    UserDeactivatedBan,
    SessionPasswordNeeded,
    UserRestricted,
    TeleAuthError,
    TeleAuthKeyNotFound,
    TeleSecurityError,
    TeleUserDeactivatedError,
    TeleUserDeactivatedBanError,
    TeleSessionPasswordNeededError,
    TeleAuthKeyUnregisteredError,
    TeleUserRestrictedError,
    TeleFrozenMethodInvalidError,
    SessionRevoked,
    TeleSessionRevokedError,
)

# ==============================================================================
# SECTION 4: GLOBAL SESSION STATE TRACKERS
# ==============================================================================
# Dict objects used to store interactive session states (such as active OTP flows,
# current rename operations, progress percentages, and user rate limiting).
PROMPT_STATE = {}       # Remembers if a user is typing a rename, split, etc.
OTP_STATE = {}          # Remembers which account OTP flow is active
CREATE_STATE = {}       # Remembers session creation progress (phone → OTP → password)
BUTTON_RATE_LIMIT = {}  # Prevents users from clicking buttons too fast
PROGRESS_STATE = {}     # Tracks download/upload progress percentages
BOT_START_TIME = time.time()  # Tracks when the bot was started

from collections import deque
ADMIN_ACTIVITY_LOG = deque(maxlen=50)  # Rolling log of admin actions
WHITELIST_MODE = False  # If True, only whitelisted users can use the bot
WHITELISTED_USERS = set()  # Set of user IDs allowed when whitelist mode is active
MAINTENANCE_MODE = False
GLOBAL_ANNOUNCEMENT = ''
BROADCAST_STATE = {}
BAN_STATE = {}
DM_STATE = {}

def log_admin_action(admin_id, action_text):
    """Record an admin action with timestamp."""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ADMIN_ACTIVITY_LOG.appendleft(f"[{ts}] Admin `{admin_id}`: {action_text}")

USER_TASKS = {}  # user_id -> set of asyncio.Task

@contextlib.asynccontextmanager
async def register_user_task(user_id, status_message_or_call=None):
    task = asyncio.current_task()
    if task:
        USER_TASKS.setdefault(user_id, set()).add(task)
    try:
        yield
    except asyncio.CancelledError:
        if status_message_or_call:
            try:
                # If it has edit_text, edit it; otherwise send a new message
                if hasattr(status_message_or_call, "edit_text"):
                    await status_message_or_call.edit_text("❌ Operation cancelled by user.")
                elif hasattr(status_message_or_call, "message") and hasattr(status_message_or_call.message, "edit_text"):
                    await status_message_or_call.message.edit_text("❌ Operation cancelled by user.")
                elif hasattr(status_message_or_call, "reply_text"):
                    await status_message_or_call.reply_text("❌ Operation cancelled by user.")
            except Exception:
                pass
        raise
    finally:
        if task:
            tasks = USER_TASKS.get(user_id)
            if tasks:
                tasks.discard(task)
                if not tasks:
                    USER_TASKS.pop(user_id, None)

RUNNING_PACKS = set()  # set of active zip_ids (both str and int) to prevent concurrent operations on the same pack

@contextlib.asynccontextmanager
async def lock_pack(zip_id):
    """Guarantees only 1 task can operate on a pack at a time, preventing account revocation & SQLite locks."""
    zip_str = str(zip_id)
    zip_int = int(zip_id) if str(zip_id).isdigit() else None
    if zip_str in RUNNING_PACKS or (zip_int is not None and zip_int in RUNNING_PACKS):
        raise RuntimeError(f"Pack #{zip_id} is currently busy with another operation.")
    RUNNING_PACKS.add(zip_str)
    if zip_int is not None:
        RUNNING_PACKS.add(zip_int)
    try:
        yield
    finally:
        RUNNING_PACKS.discard(zip_str)
        if zip_int is not None:
            RUNNING_PACKS.discard(zip_int)


# ─────────────────────────────────────────────────────────────────────────────
# Bot Instance
# 
# ─────────────────────────────────────────────────────────────────────────────

app = Client(
    "accstore_engine",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    proxy=PYRO_PROXY,
    workdir=ROOT_DIR,
    sleep_threshold=15,
    ipv6=USE_IPV6,
    max_concurrent_transmissions=4,
    workers=64
)


# Global Access Control Middleware
def is_authorized(user_id):
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS
def is_admin(user_id):
    """Strict admin check — only users in ADMIN_IDS. Returns False if ADMIN_IDS is empty."""
    if not ADMIN_IDS:
        return False
    return user_id in ADMIN_IDS



# --- Storage & Registry Persistence ---
_STORAGE_ENSURED = False

def ensure_storage():
    global _STORAGE_ENSURED
    if _STORAGE_ENSURED:
        return
    os.makedirs(PACKS_DIR, exist_ok=True)
    if not os.path.exists(META_FILE):
        save_meta({"next_zip_id": 1, "zips": {}, "user_settings": {}, "users": {}})
    
    # Migrate bot session from root to ROOT_DIR to avoid clutter and LSP file-watcher issues
    for ext in ["", "-journal", "-wal", "-shm"]:
        old_path = f"accstore_engine.session{ext}"
        new_path = os.path.join(ROOT_DIR, f"accstore_engine.session{ext}")
        if os.path.exists(old_path):
            try:
                shutil.move(old_path, new_path)
            except Exception as e:
                print(f"Warning: could not migrate {old_path} to {new_path}: {e}")

    # Enforce SQLite WAL mode on the bot's session database ONCE at startup
    bot_session_file = os.path.join(ROOT_DIR, "accstore_engine.session")
    if os.path.exists(bot_session_file):
        conn = None
        try:
            conn = sqlite3.connect(bot_session_file, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    _STORAGE_ENSURED = True


# Open the notebook and read all the data inside it.
import threading
_META_FILE_LOCK = threading.RLock()

def load_meta():
    ensure_storage()
    with _META_FILE_LOCK:
        try:
            with open(META_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, FileNotFoundError):
            logging.error(f"[REGISTRY] Corrupted or missing {META_FILE}, rebuilding...")
            fallback = {"next_zip_id": 1, "zips": {}, "user_settings": {}, "users": {}}
            save_meta(fallback)
            return fallback



def save_meta(meta):
    os.makedirs(ROOT_DIR, exist_ok=True)
    # Atomic write: write to temp file first, then replace.
    # This prevents corruption if the process is killed mid-write.
    tmp_path = META_FILE + ".tmp"
    with _META_FILE_LOCK:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        os.replace(tmp_path, META_FILE)


# Clean up a file name by removing weird characters.
# Example: "my file!!!.zip" becomes "my_file"
def sanitize_filename(name):
    cleaned = re.sub(r"[^\w\- ]+", "", (name or "").strip())
    cleaned = cleaned.replace(" ", "_").strip("._")
    return cleaned or f"zip_{int(time.time())}"


# Look up a specific zip's info from the notebook.
def zip_record(zip_id):
    meta = load_meta()
    return meta["zips"].get(str(zip_id))


# Check if a zip pack belongs to a specific user.
def zip_belongs_to_user(zip_id, user_id):
    record = zip_record(zip_id)
    if not record:
        return False
    if is_admin(user_id):
        return True
    owner = record.get("owner_id")
    if owner is None:
        return True
    return str(owner) == str(user_id)


# Get the list of devices a user has marked as "protected" (won't be deleted).
def user_protected_devices(user_id):
    meta = load_meta()
    return meta.get("user_settings", {}).get(str(user_id), {}).get("protected_devices", [])


# Save the list of protected devices for a user.
def set_user_protected_devices(user_id, devices):
    meta = load_meta()
    meta.setdefault("user_settings", {}).setdefault(str(user_id), {})["protected_devices"] = devices
    save_meta(meta)


# Toggle devices on/off the protected list.
# If a device is already protected, it gets UN-protected. And vice versa.
def toggle_user_protected_devices(user_id, devices, clear_all=False):
    if clear_all:
        set_user_protected_devices(user_id, [])
        return []

    current = user_protected_devices(user_id)
    current_map = {item.lower(): item for item in current}

    for device in devices:
        key = device.lower()
        if key in current_map:
            del current_map[key]
        else:
            current_map[key] = device

    updated = list(current_map.values())
    set_user_protected_devices(user_id, updated)
    return updated


# Get the folder path where a zip's session files are stored.

# --- User ID Resolution ---
def resolve_user_id(query, meta):
    """Resolve a numeric string or @username to an integer user ID."""
    query = str(query).strip()
    if query.isdigit():
        return int(query)
    target_uname = query.lstrip("@").lower()
    users_dict = meta.get("users", {})
    if isinstance(users_dict, dict):
        for uid_str, udata in users_dict.items():
            if isinstance(udata, dict) and udata.get("username", "").lower() == target_uname:
                return int(uid_str)
    return None

# Admin state message handler (ban/unban/dm/lookup)

# --- Formatting Utilities ---
def progress_bar(current, total, width=10):
    total = max(total, 1)
    filled = int((current / total) * width)
    pct = int((current / total) * 100)
    return f"{'█' * filled}{'░' * (width - filled)} {current}/{total} ({pct}%)"


# Convert big byte numbers into human-friendly sizes.
# Example: 1048576 bytes → "1.0MB"
def human_size(num_bytes):
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


# Convert a datetime or Unix timestamp into a human-friendly "time ago" string.
# Example: 3600 seconds ago → "1h ago", 86400 → "1d ago"
def human_time_ago(dt_or_ts):
    try:
        if isinstance(dt_or_ts, str):
            try:
                dt_or_ts = datetime.fromisoformat(dt_or_ts.replace('Z', '+00:00'))
            except ValueError:
                try:
                    dt_or_ts = datetime.strptime(dt_or_ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return "Unknown"
                    
        if isinstance(dt_or_ts, (int, float)):
            if dt_or_ts <= 0:
                return "Unknown"
            delta = time.time() - dt_or_ts
        elif isinstance(dt_or_ts, datetime):
            now = datetime.now(timezone.utc)
            if dt_or_ts.tzinfo is None:
                dt_or_ts = dt_or_ts.replace(tzinfo=timezone.utc)
            delta = (now - dt_or_ts).total_seconds()
        else:
            return "Unknown"
        if delta < 0:
            return "Just now"
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta // 60)}m ago"
        if delta < 86400:
            return f"{int(delta // 3600)}h ago"
        if delta < 2592000:
            return f"{int(delta // 86400)}d ago"
        if delta < 31536000:
            return f"{int(delta // 2592000)}mo ago"
        return f"{int(delta // 31536000)}y ago"
    except Exception:
        return "Unknown"


# Format a Unix timestamp or datetime into a full readable string + time-ago.
# Example: "May 15, 2025 at 14:30 (5d ago)"
def format_timestamp(dt_or_ts):
    try:
        from datetime import timezone, timedelta
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        if isinstance(dt_or_ts, (int, float)):
            if dt_or_ts <= 0:
                return "Unknown"
            dt = datetime.fromtimestamp(dt_or_ts, tz=timezone.utc)
        elif isinstance(dt_or_ts, datetime):
            dt = dt_or_ts if dt_or_ts.tzinfo else dt_or_ts.replace(tzinfo=timezone.utc)
        else:
            return "Unknown"
        
        dt_ist = dt.astimezone(ist_tz)
        formatted = dt_ist.strftime("%b %d, %Y at %I:%M %p IST")
        ago = human_time_ago(dt_or_ts)
        return f"{formatted} ({ago})"
    except Exception:
        return "Unknown"


# Format bot uptime into a readable string.
def format_uptime():
    delta = int(time.time() - BOT_START_TIME)
    days, remainder = divmod(delta, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)



# ─────────────────────────────────────────────────────────────────────────────
# Interface Components
# These functions create the clickable buttons you see in Telegram.
# Each button triggers a different action when you tap it.
# ─────────────────────────────────────────────────────────────────────────────


# The welcome screen buttons (shown when user sends /start)

# --- Helper Functions & Progress Trackers ---
def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Prevent users from spam-clicking buttons.
# If they click within 1 second of the last click, ignore it.
def is_rate_limited(user_id, cooldown=1.0):
    now = time.time()
    last_click = BUTTON_RATE_LIMIT.get(user_id, 0)
    if now - last_click < cooldown:
        return True
    BUTTON_RATE_LIMIT[user_id] = now
    return False


# Format a nice caption for result zips.
# Example: "✅ Active : 5 Session(s)."
def format_result_caption(label, count, icon="✅"):
    return f"{icon} {label} — `{count}` account(s)"


# A small "Cancel" button that the user can press to abort any action.
async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass


# Safely disconnect a Telethon client. Prevents errors if it's already disconnected.
# Also cancels any lingering internal tasks (_send_loop, _recv_loop) that cause
# "Task was destroyed but it is pending!" and "Future exception was never retrieved" noise.
async def safe_telethon_disconnect(client):
    if not client:
        return
    try:
        # Step 1: Proactively cancel pending state futures to prevent "Future exception was never retrieved" 
        try:
            sender = getattr(client, '_sender', None)
            if sender:
                pending_state = getattr(sender, '_pending_state', {})
                for state in pending_state.values():
                    future = getattr(state, 'future', None)
                    if future and not future.done():
                        future.cancel()
        except (asyncio.CancelledError, Exception):
            pass

        # Step 2: Disconnect the client (stops sender loops)
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

        # Helper to clean up any task safely
        async def _cleanup_task(task):
            if not task:
                return
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                try:
                    task.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        # Step 3: Cancel/retrieve lingering tasks inside client and MTProtoSender
        try:
            for attr in ('_updates_handle',):
                task = getattr(client, attr, None)
                await _cleanup_task(task)
        except (asyncio.CancelledError, Exception):
            pass

        try:
            sender = getattr(client, '_sender', None)
            if sender:
                for attr in ('_send_loop_handle', '_recv_loop_handle'):
                    task = getattr(sender, attr, None)
                    await _cleanup_task(task)
                
                # Also cancel the underlying Connection tasks
                connection = getattr(sender, 'connection', getattr(sender, '_connection', None))
                if connection:
                    for attr in ('_send_loop_handle', '_recv_loop_handle'):
                        task = getattr(connection, attr, None)
                        await _cleanup_task(task)
        except (asyncio.CancelledError, Exception):
            pass

        # Step 4: Close the session file handle
        try:
            if hasattr(client, "session") and client.session:
                client.session.close()
        except (asyncio.CancelledError, Exception):
            pass
    finally:
        # Step 5: Guaranteed release of global connection semaphore
        if getattr(client, "_sem_acquired", False):
            try:
                GLOBAL_CONNECTION_SEM.release()
            except ValueError:
                pass
            client._sem_acquired = False

    # Step 6: Brief yield to let the event loop clean up cancelled tasks
    await asyncio.sleep(0)


async def safe_pyrogram_disconnect(client):
    """Safely disconnect or stop a Pyrogram client with complete resource cleanup, shutting down crypto threads and closing storage."""
    if not client:
        return
    try:
        # Step 1: Disconnect or stop depending on whether client was initialized
        if getattr(client, "is_initialized", False):
            try:
                await asyncio.wait_for(client.stop(), timeout=4)
            except Exception:
                pass
        elif getattr(client, "is_connected", False):
            try:
                await asyncio.wait_for(client.disconnect(), timeout=4)
            except Exception:
                pass

        # Step 2: Explicitly terminate TCP protocol crypto worker thread pools
        session = getattr(client, "session", None)
        if session:
            try:
                conn = getattr(session, "connection", None)
                if conn:
                    proto = getattr(conn, "protocol", None)
                    if proto:
                        executor = getattr(proto, "crypto_executor", None)
                        if executor:
                            executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            try:
                await asyncio.wait_for(session.stop(), timeout=2)
            except Exception:
                pass
            client.session = None

        # Step 3: Explicitly shut down any media sessions crypto executors
        media_sessions = getattr(client, "media_sessions", {}) or {}
        for ms in list(media_sessions.values()):
            try:
                m_conn = getattr(ms, "connection", None)
                if m_conn:
                    m_proto = getattr(m_conn, "protocol", None)
                    if m_proto:
                        m_exec = getattr(m_proto, "crypto_executor", None)
                        if m_exec:
                            m_exec.shutdown(wait=False, cancel_futures=True)
                await asyncio.wait_for(ms.stop(), timeout=2)
            except Exception:
                pass
        if hasattr(client, "media_sessions") and isinstance(client.media_sessions, dict):
            client.media_sessions.clear()

        # Step 4: Ensure SQLite storage connection is closed
        storage = getattr(client, "storage", None)
        if storage:
            try:
                await asyncio.wait_for(storage.close(), timeout=2)
            except Exception:
                pass
            try:
                raw_conn = _get_pyro_conn(storage)
                if raw_conn and hasattr(raw_conn, "close"):
                    raw_conn.close()
                if hasattr(storage, "_conn"):
                    storage._conn = None
            except Exception:
                pass

        client.is_connected = False
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    finally:
        if getattr(client, "_sem_acquired", False):
            try:
                GLOBAL_CONNECTION_SEM.release()
            except ValueError:
                pass
            client._sem_acquired = False

    await asyncio.sleep(0)


# Throttles editing of messages to avoid Telegram rate limits
class ThrottledProgress:
    def __init__(self, message, template_text, total, interval=2.5):
        self.message = message
        self.template_text = template_text
        self.total = total
        self.interval = interval
        self.completed = 0
        self.last_update = 0.0
        self.lock = asyncio.Lock()
        self._initial = True  # First render always fires
        self.current_detail = ""

    async def update(self, delta=1, detail=""):
        should_render = False
        text = ""
        is_final = False
        
        async with self.lock:
            self.completed += delta
            if detail:
                self.current_detail = detail
            now = time.time()
            is_final = (self.completed >= self.total)
            should_render = (
                self._initial
                or is_final
                or (now - self.last_update >= self.interval)
            )
            if should_render:
                self._initial = False
                self.last_update = now
                bar = progress_bar(self.completed, self.total)
                text = self.template_text
                if "{progress}" in text:
                    text = text.replace("{progress}", bar)
                else:
                    try:
                        text = text.format(progress=bar)
                    except Exception:
                        pass
                
                if self.current_detail and not is_final:
                    text += f"\n\n⚡ **Active**: `{self.current_detail}`"
                
        if should_render:
            try:
                await self.message.edit_text(text)
            except FloodWait as fw:
                # If Telegram says wait, push back next update window
                self.last_update = time.time() + fw.value
                if is_final and fw.value <= 10:
                    await asyncio.sleep(fw.value + 0.5)
                    try:
                        await self.message.edit_text(text)
                    except Exception:
                        pass
            except Exception:
                pass


# Show download progress as a nice progress bar while files are being downloaded.
async def download_progress(current, total, status_message, label):
    if total <= 0:
        return
    progress_key = (status_message.chat.id, status_message.id)
    percent = int((current / total) * 100)
    
    last_state = PROGRESS_STATE.get(progress_key, (-1, 0))
    if isinstance(last_state, int):
        last_state = (last_state, 0)
    last_percent, last_time = last_state
    
    if percent == last_percent:
        return
        
    import time
    now = time.time()
    if percent < 100 and (now - last_time) < 2.0:
        return
        
    PROGRESS_STATE[progress_key] = (percent, now)
    await safe_edit(
        status_message,
        f"{label}\n\n{'█' * int(percent / 10)}{'░' * (10 - int(percent / 10))} {percent}%\n{human_size(current)} / {human_size(total)}",
    )


# Check if a chat should be protected from deletion.
# System chats (like Telegram itself, SpamBot) should never be cleared.
def is_protected_chat(chat, me_id):
    chat_id = chat.id
    username = (chat.username or "").lower()
    if chat_id == me_id:
        return True
    if chat_id in KEEP_IDS:
        return True
    if username in KEEP_USERNAMES:
        return True
    return False


def is_protected_chat_telethon(dialog, me_id):
    chat_id = dialog.id
    username = (getattr(dialog.entity, "username", "") or "").lower() if dialog.entity else ""
    if chat_id == me_id:
        return True
    if chat_id in KEEP_IDS:
        return True
    if username in KEEP_USERNAMES:
        return True
    return False



# ─────────────────────────────────────────────────────────────────────────────
# Pack Builder
# These functions package session files into ZIP archives
# and send them back to the user as organized downloads.
# ─────────────────────────────────────────────────────────────────────────────


