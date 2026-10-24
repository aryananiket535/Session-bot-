# ==============================================================================
# AccStore Session Manager — Session Engine, Storage, Clients & Formats
# ==============================================================================

import config
from config import *
import subprocess
import tarfile
from config import (
    _get_device_params_for_session,
    _PROXY_TYPE,
    _PROXY_HOST,
    _PROXY_PORT,
    _PROXY_USER,
    _PROXY_PASS,
)
for _k, _v in vars(config).items():
    if not _k.startswith('__'):
        globals()[_k] = _v

def _patch_session_for_telethon(session_path):
    """Ensure a .session file has the tables Telethon expects before opening it."""
    conn = None
    try:
        conn = sqlite3.connect(session_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        if not tables:
            return

        # Fix version table: Pyrogram uses column 'number', Telethon uses 'version'
        if "version" in tables:
            cur.execute("PRAGMA table_info(version)")
            v_cols = [col[1] for col in cur.fetchall()]
            if "version" not in v_cols and "number" in v_cols:
                cur.execute("ALTER TABLE version RENAME TO _old_version")
                cur.execute("CREATE TABLE version (version INTEGER PRIMARY KEY)")
                cur.execute("INSERT INTO version SELECT number FROM _old_version")
                cur.execute("DROP TABLE _old_version")
                conn.commit()
            try:
                row_v = cur.execute("SELECT version FROM version LIMIT 1").fetchone()
                if not row_v or row_v[0] < 8:
                    cur.execute("UPDATE version SET version = 8")
                    conn.commit()
            except Exception:
                pass
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS version (version INTEGER PRIMARY KEY)")
            cur.execute("INSERT OR IGNORE INTO version VALUES (8)")
            conn.commit()

        # Add missing Telethon tables
        if "entities" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY,
                hash INTEGER NOT NULL,
                username TEXT,
                phone INTEGER,
                name TEXT,
                date INTEGER
            )""")
            conn.commit()

        if "sent_files" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS sent_files (
                md5_digest BLOB,
                file_size INTEGER,
                type INTEGER,
                id INTEGER,
                hash INTEGER,
                PRIMARY KEY(md5_digest, file_size, type)
            )""")
            conn.commit()

        if "update_state" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS update_state (
                id INTEGER PRIMARY KEY,
                pts INTEGER,
                qts INTEGER,
                date INTEGER,
                seq INTEGER
            )""")
            conn.commit()

        # Fix sessions table to guarantee EXACT 6 Telethon columns: (dc_id, server_address, port, auth_key, takeout_id, tmp_auth_key)
        if "sessions" in tables:
            cur.execute("PRAGMA table_info(sessions)")
            s_cols = [col[1] for col in cur.fetchall()]
            tele_cols = ["dc_id", "server_address", "port", "auth_key", "takeout_id", "tmp_auth_key"]
            if s_cols != tele_cols:
                row = cur.execute("SELECT * FROM sessions LIMIT 1").fetchone()
                col_map = {col: val for col, val in zip(s_cols, row)} if row else {}
                dc_id = col_map.get("dc_id") or 2
                auth_key = col_map.get("auth_key")
                server_address = col_map.get("server_address")
                port = col_map.get("port") or 443
                takeout_id = col_map.get("takeout_id")
                tmp_auth_key = col_map.get("tmp_auth_key")

                DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
                if not server_address or ":" in str(server_address):
                    server_address = DC_MAP_IPV4.get(dc_id, "149.154.167.51")

                cur.execute("DROP TABLE sessions")
                cur.execute("""CREATE TABLE sessions (
                    dc_id INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port INTEGER,
                    auth_key BLOB,
                    takeout_id INTEGER,
                    tmp_auth_key BLOB
                )""")
                cur.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (dc_id, server_address, port or 443, auth_key, takeout_id, tmp_auth_key or b"")
                )
                conn.commit()
            else:
                try:
                    row = cur.execute("SELECT dc_id, server_address FROM sessions LIMIT 1").fetchone()
                    if row:
                        dc_id, current_server = row[0], row[1]
                        DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
                        target_server = DC_MAP_IPV4.get(dc_id, "149.154.167.51")
                        if current_server != target_server or ":" in str(current_server):
                            cur.execute("UPDATE sessions SET server_address = ?, port = 443 WHERE dc_id = ?", (target_server, dc_id))
                            conn.commit()
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _patch_session_for_pyrogram(session_path):
    """Ensure a .session file has the tables/columns Pyrogram expects before opening it."""
    conn = None
    try:
        conn = sqlite3.connect(session_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        if not tables:
            return

        # Fix version table: Telethon uses column 'version', Pyrogram uses 'number'
        if "version" in tables:
            cur.execute("PRAGMA table_info(version)")
            v_cols = [col[1] for col in cur.fetchall()]
            if "number" not in v_cols and "version" in v_cols:
                cur.execute("ALTER TABLE version RENAME TO _old_version")
                cur.execute("CREATE TABLE version (number INTEGER PRIMARY KEY)")
                cur.execute("INSERT INTO version SELECT version FROM _old_version")
                cur.execute("DROP TABLE _old_version")
                conn.commit()
            try:
                row_v = cur.execute("SELECT number FROM version LIMIT 1").fetchone()
                if not row_v:
                    cur.execute("INSERT INTO version (number) VALUES (7)")
                    conn.commit()
                elif row_v[0] < 7:
                    cur.execute("UPDATE version SET number = 7")
                    conn.commit()
            except Exception:
                pass
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS version (number INTEGER PRIMARY KEY)")
            cur.execute("INSERT OR IGNORE INTO version (number) VALUES (7)")
            conn.commit()

        # Fix sessions table for Pyrogram (ensure server_address, port, api_id, test_mode, auth_key, date, user_id, is_bot, tmp_auth_key)
        if "sessions" in tables:
            cur.execute("PRAGMA table_info(sessions)")
            s_cols = [col[1] for col in cur.fetchall()]
            pyro_cols_def = [
                ("server_address", "TEXT"),
                ("port", "INTEGER DEFAULT 443"),
                ("api_id", "INTEGER"),
                ("test_mode", "INTEGER DEFAULT 0"),
                ("auth_key", "BLOB"),
                ("date", "INTEGER NOT NULL DEFAULT 0"),
                ("user_id", "INTEGER DEFAULT 0"),
                ("is_bot", "INTEGER DEFAULT 0"),
                ("tmp_auth_key", "BLOB")
            ]
            for col_name, col_type in pyro_cols_def:
                if col_name not in s_cols:
                    try:
                        cur.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass
            conn.commit()

            try:
                row = cur.execute("SELECT dc_id, server_address FROM sessions LIMIT 1").fetchone()
                if row:
                    dc_id, s_addr = row[0] or 2, row[1]
                    DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
                    if not s_addr or ":" in str(s_addr):
                        cur.execute("UPDATE sessions SET server_address = ?, port = 443 WHERE dc_id = ?", (DC_MAP_IPV4.get(dc_id, "149.154.167.51"), dc_id))
                        conn.commit()
            except Exception:
                pass

        # Add peers table if missing
        if "peers" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS peers (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER,
                type TEXT,
                username TEXT,
                phone_number TEXT,
                last_update_on INTEGER DEFAULT 0
            )""")
            conn.commit()

        # Add usernames table if missing
        if "usernames" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS usernames (
                id INTEGER,
                username TEXT
            )""")
            conn.commit()

        # Add update_state table if missing
        if "update_state" not in tables:
            cur.execute("""CREATE TABLE IF NOT EXISTS update_state (
                id INTEGER PRIMARY KEY,
                pts INTEGER,
                qts INTEGER,
                date INTEGER,
                seq INTEGER
            )""")
            conn.commit()
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


class NonInteractivePyrogramClient(Client):
    """Pyrogram client that suppresses interactive phone prompt, gates connections via GLOBAL_CONNECTION_SEM, and paces handshakes."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sem_acquired = False

    async def authorize(self):
        raise AuthKeyUnregistered("Session revoked or unauthorized, interactive login disabled")

    async def connect(self):
        acquired_here = False
        if not self._sem_acquired:
            await asyncio.wait_for(GLOBAL_CONNECTION_SEM.acquire(), timeout=20)
            self._sem_acquired = True
            acquired_here = True
        try:
            res = await super().connect()
            # Anti-freeze pacing delay: prevents sudden concurrent handshake bursts to Telegram MTProto
            await asyncio.sleep(random.uniform(0.3, 0.7))
            return res
        except BaseException:
            if acquired_here and self._sem_acquired:
                try:
                    GLOBAL_CONNECTION_SEM.release()
                except ValueError:
                    pass
                self._sem_acquired = False
            raise

    async def disconnect(self):
        try:
            if getattr(self, "is_initialized", False):
                try:
                    await asyncio.wait_for(self.terminate(), timeout=3)
                except Exception:
                    pass
            if getattr(self, "is_connected", False):
                try:
                    await asyncio.wait_for(super().disconnect(), timeout=5)
                except Exception:
                    pass
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
            logging.debug(f"[Pyro] Client disconnect warning: {e}")
        finally:
            # Shut down crypto_executor to prevent thread leaks
            try:
                session = getattr(self, "session", None)
                if session:
                    conn = getattr(session, "connection", None)
                    proto = getattr(conn, "protocol", None) if conn else None
                    executor = getattr(proto, "crypto_executor", None) if proto else None
                    if executor:
                        executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            # Close media session crypto executors
            try:
                media_sessions = getattr(self, "media_sessions", {}) or {}
                for ms in list(media_sessions.values()):
                    m_conn = getattr(ms, "connection", None)
                    m_proto = getattr(m_conn, "protocol", None) if m_conn else None
                    m_exec = getattr(m_proto, "crypto_executor", None) if m_proto else None
                    if m_exec:
                        m_exec.shutdown(wait=False, cancel_futures=True)
                    if hasattr(ms, "stop"):
                        await ms.stop()
                if hasattr(self, "media_sessions") and isinstance(self.media_sessions, dict):
                    self.media_sessions.clear()
            except Exception:
                pass
            # Close storage
            try:
                storage = getattr(self, "storage", None)
                if storage:
                    await storage.close()
            except Exception:
                pass
            self.is_connected = False
            if self._sem_acquired:
                try:
                    GLOBAL_CONNECTION_SEM.release()
                except ValueError:
                    pass
                self._sem_acquired = False

    async def start(self):
        # Client.start() calls self.connect() which handles semaphore gating and anti-freeze pacing
        return await super().start()

    async def stop(self, *args, **kwargs):
        try:
            if getattr(self, "is_initialized", False):
                try:
                    await asyncio.wait_for(super().stop(*args, **kwargs), timeout=5)
                except Exception:
                    pass
            elif getattr(self, "is_connected", False):
                try:
                    await asyncio.wait_for(super().disconnect(), timeout=5)
                except Exception:
                    pass
            return await self.disconnect()
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
            logging.debug(f"[Pyro] Client stop warning: {e}")
        finally:
            if self._sem_acquired:
                try:
                    GLOBAL_CONNECTION_SEM.release()
                except ValueError:
                    pass
                self._sem_acquired = False

    async def __aenter__(self):
        import asyncio
        self.loop = asyncio.get_running_loop()
        return await asyncio.wait_for(self.start(), timeout=CONNECT_TIMEOUT)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()


def make_pyro_client(session_name, workdir=None, session_string=None, **kwargs):
    """Create a Pyrogram Client with authentic device parameters to prevent account freezing."""
    if workdir and not session_string:
        session_file = os.path.join(workdir, session_name + ".session")
        if os.path.isfile(session_file):
            _patch_session_for_pyrogram(session_file)

    dev_params = _get_device_params_for_session(workdir, session_name)
    kw = {
        "api_id": dev_params.get("api_id", API_ID),
        "api_hash": dev_params.get("api_hash", API_HASH),
        "no_updates": True,
        "ipv6": USE_IPV6,
        "device_model": dev_params.get("device_model", SAFE_DEVICE_MODEL),
        "system_version": dev_params.get("system_version", SAFE_SYSTEM_VERSION),
        "app_version": dev_params.get("app_version", SAFE_APP_VERSION),
        "lang_code": dev_params.get("lang_code", SAFE_LANG_CODE),
    }
    if workdir:
        kw["workdir"] = workdir
    if session_string:
        kw["session_string"] = session_string
    if PYRO_PROXY:
        kw["proxy"] = PYRO_PROXY
        
    kw.update(kwargs)
    return NonInteractivePyrogramClient(session_name, **kw)


class PatchedSQLiteSession(SQLiteSession):
    def __init__(self, session_id=None):
        if session_id:
            target = session_id if str(session_id).endswith(".session") else f"{session_id}.session"
            if os.path.isfile(target):
                _patch_session_for_telethon(target)
        try:
            super().__init__(session_id=session_id)
        except ValueError as e:
            if "unpack" in str(e).lower():
                target = session_id if str(session_id).endswith(".session") else f"{session_id}.session"
                if os.path.isfile(target):
                    _patch_session_for_telethon(target)
                    super().__init__(session_id=session_id)
                else:
                    raise
            else:
                raise

        self.save_entities = False

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                check_same_thread=False,
                timeout=10
            )
            # WAL mode allows concurrent reads and reduces "database is locked" errors
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=10000")
            except Exception:
                pass
        return self._conn.cursor()

    def save(self):
        """Override to handle SQLite locks gracefully during commit."""
        if self._conn is not None:
            for attempt in range(5):
                try:
                    self._conn.commit()
                    return
                except sqlite3.OperationalError as e:
                    if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < 4:
                        import time as _t
                        _t.sleep(0.05 * (attempt + 1))
                        continue
                    return

    def process_entities(self, tlo):
        """Override to safely ignore entity persistence and lock conflicts."""
        if not getattr(self, "save_entities", False):
            return
        try:
            super().process_entities(tlo)
        except sqlite3.OperationalError:
            pass

    def _upgrade_database(self, old):
        """Override to suppress duplicate column or table already exists errors during Telethon upgrade."""
        try:
            super()._upgrade_database(old=old)
        except sqlite3.OperationalError as e:
            err_lower = str(e).lower()
            if "duplicate column" in err_lower or "already exists" in err_lower:
                pass
            else:
                raise

    def _update_session_table(self):
        """Override to suppress 'table already exists' errors from Telethon's init."""
        try:
            super()._update_session_table()
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise

    def _execute(self, stmt, *values):
        """Override to handle SQLite locks gracefully with retries instead of crashing."""
        for attempt in range(5):
            try:
                c = self._cursor()
                try:
                    return c.execute(stmt, values).fetchone()
                finally:
                    c.close()
            except sqlite3.OperationalError as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < 4:
                    import time as _t
                    _t.sleep(0.1 * (attempt + 1))
                    continue
                if "update_state" in stmt.lower() or "locked" in str(e).lower():
                    logging.debug(f"[PatchedSQLiteSession] Suppressed locked error on {stmt}: {e}")
                    return None
                raise

    def close(self):
        """Safely close connection and suppress lock errors on commit."""
        if self.filename != ':memory:':
            if self._conn is not None:
                try:
                    self._conn.commit()
                except Exception:
                    pass
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

def safe_sqlite_session(session_path):
    """Create a Telethon SQLiteSession after patching the file for compatibility."""
    _patch_session_for_telethon(session_path)
    for attempt in range(5):
        try:
            return PatchedSQLiteSession(session_path)
        except sqlite3.OperationalError as e:
            if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < 4:
                import time as _time
                _time.sleep(0.05 * (attempt + 1))
                continue
            raise


class SafeTelethonClient(TelethonClient):
    """Telethon client that gates connections via GLOBAL_CONNECTION_SEM and paces handshakes to prevent account freezing."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sem_acquired = False

    async def connect(self):
        acquired_here = False
        if not self._sem_acquired:
            await asyncio.wait_for(GLOBAL_CONNECTION_SEM.acquire(), timeout=20)
            self._sem_acquired = True
            acquired_here = True
        try:
            res = await super().connect()
            # Anti-freeze pacing delay: prevents sudden concurrent handshake bursts to Telegram MTProto
            await asyncio.sleep(random.uniform(0.3, 0.7))
            return res
        except BaseException:
            if acquired_here and self._sem_acquired:
                try:
                    GLOBAL_CONNECTION_SEM.release()
                except ValueError:
                    pass
                self._sem_acquired = False
            raise

    async def disconnect(self):
        try:
            return await asyncio.wait_for(super().disconnect(), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
            logging.debug(f"[Tele] Client disconnect warning: {e}")
        finally:
            try:
                if hasattr(self, "session") and self.session:
                    self.session.close()
            except Exception:
                pass
            if self._sem_acquired:
                try:
                    GLOBAL_CONNECTION_SEM.release()
                except ValueError:
                    pass
                self._sem_acquired = False


def make_tele_client(session_obj, workdir=None, session_name=None, **kwargs):
    """Create a Telethon Client with authentic device parameters to prevent account freezing."""
    if not workdir and hasattr(session_obj, "filename") and session_obj.filename:
        try:
            workdir = os.path.dirname(session_obj.filename)
            session_name = os.path.splitext(os.path.basename(session_obj.filename))[0]
        except Exception:
            pass

    dev_params = _get_device_params_for_session(workdir, session_name)
    has_proxy = bool(kwargs.get("proxy") or TELE_PROXY)
    kw = {
        "api_id": dev_params.get("api_id", API_ID),
        "api_hash": dev_params.get("api_hash", API_HASH),
        "receive_updates": False,
        "auto_reconnect": False,
        "connection_retries": 3,
        "retry_delay": 1,
        "timeout": 25 if has_proxy else 15,
        "use_ipv6": USE_IPV6,
        "device_model": dev_params.get("device_model", SAFE_DEVICE_MODEL),
        "system_version": dev_params.get("system_version", SAFE_SYSTEM_VERSION),
        "app_version": dev_params.get("app_version", SAFE_APP_VERSION),
        "lang_code": dev_params.get("lang_code", SAFE_LANG_CODE),
        "system_lang_code": dev_params.get("system_lang_code", SAFE_SYSTEM_LANG_CODE),
    }
    if TELE_PROXY:
        kw["proxy"] = TELE_PROXY
        
    kw.update(kwargs)
    return SafeTelethonClient(session_obj, **kw)


# ==============================================================================

# --- Session Filesystem Paths ---
def session_dir(zip_id):
    return os.path.join(PACKS_DIR, f"zip_{zip_id}", "sessions")


# Get a sorted list of all .session files inside a zip's folder.
def get_session_files(zip_id):
    folder = session_dir(zip_id)
    if not os.path.exists(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.endswith(".session")
    )



# ─────────────────────────────────────────────────────────────────────────────
# Detection Utilities
# These functions figure out what type of session file you have
# (Pyrogram or Telethon) and help display progress and sizes.
# ─────────────────────────────────────────────────────────────────────────────


# Open a session file and look at its database columns to figure out
# if it's a Pyrogram session or a Telethon session.

# --- Session Type Detection ---
def detect_session_type(session_path):
    conn = None
    try:
        base_lower = os.path.basename(session_path).lower()
        if "telethon" in base_lower and "pyrogram" not in base_lower:
            return "telethon"
        if "pyrogram" in base_lower or "pyro" in base_lower:
            return "pyrogram"

        conn = sqlite3.connect(session_path, timeout=20)
        cur = conn.cursor()
        
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        
        if "sessions" in tables:
            cur.execute("PRAGMA table_info(sessions)")
            s_cols = [col[1] for col in cur.fetchall()]
            # Specific to Pyrogram: api_id, test_mode, user_id, is_bot, date
            if any(c in s_cols for c in ("api_id", "test_mode", "user_id", "is_bot", "date")):
                return "pyrogram"
            # Specific to Telethon: takeout_id
            if "takeout_id" in s_cols:
                return "telethon"
            if len(s_cols) == 6 and "server_address" in s_cols and "auth_key" in s_cols:
                return "telethon"

        if "peers" in tables and "entities" not in tables:
            return "pyrogram"
        if "entities" in tables and "peers" not in tables:
            return "telethon"

        if "version" in tables:
            cur.execute("PRAGMA table_info(version)")
            v_cols = [col[1] for col in cur.fetchall()]
            if "number" in v_cols and "version" not in v_cols:
                return "pyrogram"
            if "version" in v_cols and "number" not in v_cols:
                return "telethon"

        if "peers" in tables:
            return "pyrogram"
        if "entities" in tables:
            return "telethon"
            
        return "telethon"
    except Exception:
        return "telethon"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# Draw a cool loading bar like: ██████░░░░ 6/10

# --- Session Format Summaries ---
def format_name(session_type):
    names = {
        "pyrogram": "Pyrogram",
        "telethon": "Telethon",
        "unknown": "Unknown",
        "mixed": "Mixed",
    }
    return names.get(session_type, session_type.title())


# Count how many sessions of each type are in a zip.
# Returns: ("pyrogram", "telethon", {pyrogram: 5, telethon: 0, unknown: 0})
def summarize_zip_formats(session_files):
    counts = {"pyrogram": 0, "telethon": 0, "unknown": 0}
    for session_path in session_files:
        session_type = detect_session_type(session_path)
        counts[session_type if session_type in counts else "unknown"] += 1

    active_types = [key for key in ("pyrogram", "telethon", "unknown") if counts[key] > 0]
    current_format = active_types[0] if len(active_types) == 1 else "mixed"

    if current_format == "pyrogram":
        converted_format = "telethon"
    elif current_format == "telethon":
        converted_format = "pyrogram"
    else:
        converted_format = "mixed"

    return current_format, converted_format, counts


# Get the display format of a zip (e.g., "PYROGRAM" or "TELETHON")
def zip_display_format(zip_id):
    files = get_session_files(zip_id)
    if not files:
        return "UNKNOWN"
    current_format, _, _ = summarize_zip_formats(files)
    return format_name(current_format).upper()


# Safely try to edit a Telegram message. If it fails (already deleted, etc.), ignore.

# --- Session String & Auth Key Parsing ---
def _parse_session_input(text):
    """
    Detect and parse user input into an auth key + dc_id.
    Supports scanning arbitrary text for:
      - Telethon string session (starts with '1' and is ~353 chars)
      - Pyrogram string session (base64url, decodes to a known struct)
      - Raw hex auth key (256 hex chars = 128 bytes), optionally with dc_id prefix
    Returns: (auth_key_bytes, dc_id, input_type) or raises ValueError.
    """
    import re
    import unicodedata
    import json
    import base64
    import struct

    # ── Phase 0.5: Check if text is a JSON string ──
    try:
        json_text = text.strip()
        if json_text.startswith("```"):
            lines = json_text.splitlines()
            if len(lines) > 2:
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                json_text = "\n".join(lines).strip()
        data = json.loads(json_text)
        if isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            if isinstance(first_item, dict):
                return _parse_session_input(json.dumps(first_item))
            elif isinstance(first_item, str):
                return _parse_session_input(first_item)
        elif isinstance(data, dict):
            s_str = (
                data.get("session_string")
                or data.get("string")
                or data.get("session")
                or data.get("session_str")
                or ""
            )
            if s_str:
                return _parse_session_input(s_str)
            
            auth_key_raw = (
                data.get("auth_key")
                or data.get("authKey")
                or data.get("key")
                or data.get("hex")
                or ""
            )
            dc_id = data.get("dc_id") or data.get("dc") or 2
            
            if auth_key_raw:
                cleaned = re.sub(r'[\s"\'\[\],;:\n\r\t]+', '', str(auth_key_raw))
                auth_key_bytes = None
                if len(cleaned) == 513 and cleaned[0] in "12345":
                    dc_id = int(cleaned[0])
                    cleaned = cleaned[1:]
                if len(cleaned) == 512 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
                    auth_key_bytes = bytes.fromhex(cleaned)
                else:
                    try:
                        padded = cleaned + "=" * (-len(cleaned) % 4)
                        decoded = base64.b64decode(padded)
                        if len(decoded) == 256:
                            auth_key_bytes = decoded
                    except Exception:
                        pass
                if auth_key_bytes and len(auth_key_bytes) == 256:
                    dc_id = int(dc_id)
                    if dc_id < 1 or dc_id > 5:
                        dc_id = 2
                    return auth_key_bytes, dc_id, "hex_key"
    except Exception:
        pass

    # ── Phase 0: Aggressively strip ALL invisible / zero-width Unicode characters ──
    # Telegram, browsers, and clipboard operations inject these silently.
    # This is the most common reason hex keys "don't work".
    def _strip_invisible(s):
        """Remove every character that is not visible (control chars, zero-width, BOM, etc.)."""
        cleaned = []
        for ch in s:
            # Keep only printable, non-control characters
            cat = unicodedata.category(ch)
            # Cc = control, Cf = format (zero-width, BOM, etc.), Zs = space separator (includes NBSP),
            # Zl = line sep, Zp = paragraph sep, Co = private use, Cs = surrogate, Cn = unassigned
            if cat in ('Cc', 'Cf', 'Co', 'Cs', 'Cn'):
                continue  # drop invisible
            cleaned.append(ch)
        return ''.join(cleaned)

    text_clean = _strip_invisible(text)

    candidates = []

    # Token-based: split by whitespace and punctuation (keep colons)
    tokens = re.split(r'[\s"\'\[\],;]+', text_clean)
    for t in tokens:
        t = t.strip()
        if t and t not in candidates:
            candidates.append(t)
            
    # Collapsed: strip ALL formatting junk (whitespace, quotes, brackets, colons, etc.)
    collapsed = re.sub(r'[\s"\'\[\],;:\n\r\t]+', '', text_clean)
    if collapsed and collapsed not in candidates:
        candidates.append(collapsed)

    # ── Phase 1b: Extract base64-like and hex substrings directly using regex ──
    b64_pattern = r'[A-Za-z0-9+/=_-]{300,950}'
    for match in re.finditer(b64_pattern, text_clean):
        val = match.group()
        if val not in candidates:
            candidates.append(val)

    hex_pattern = r'[0-9a-fA-F]{512,514}'
    for match in re.finditer(hex_pattern, text_clean):
        val = match.group()
        if val not in candidates:
            candidates.append(val)

    # ── Phase 2: Also extract hex-only substrings directly from the cleaned text ──
    # This catches hex keys even if embedded in surrounding non-hex text.
    hex_only = re.sub(r'[^0-9a-fA-F]', '', text_clean)
    if hex_only and hex_only not in candidates:
        candidates.append(hex_only)

    # ── Phase 3: Try to match each candidate ──
    for token in candidates:

        # 1. Check for dc_id:hex_key (e.g. 2:aabbcc...) or hex_key:dc_id
        if ":" in token:
            parts = token.split(":")
            if len(parts) == 2:
                p0 = parts[0].strip()
                p1 = parts[1].strip()
                if p0.isdigit() and len(p1) == 512 and all(c in "0123456789abcdefABCDEF" for c in p1):
                    auth_key = bytes.fromhex(p1.lower())
                    dc_id = int(p0)
                    if 1 <= dc_id <= 5:
                        return auth_key, dc_id, "hex_key"
                elif p1.isdigit() and len(p0) == 512 and all(c in "0123456789abcdefABCDEF" for c in p0):
                    auth_key = bytes.fromhex(p0.lower())
                    dc_id = int(p1)
                    if 1 <= dc_id <= 5:
                        return auth_key, dc_id, "hex_key"

        # 2. Check for 513-char hex (DC ID prefix + hex key)
        if len(token) == 513 and token[0] in "12345" and all(c in "0123456789abcdefABCDEF" for c in token):
            dc_id = int(token[0])
            hex_part = token[1:]
            auth_key = bytes.fromhex(hex_part.lower())
            return auth_key, dc_id, "hex_key"

        # 2b. Check for 513-char hex (hex key + DC ID suffix)
        if len(token) == 513 and token[-1] in "12345" and all(c in "0123456789abcdefABCDEF" for c in token):
            dc_id = int(token[-1])
            hex_part = token[:-1]
            auth_key = bytes.fromhex(hex_part.lower())
            return auth_key, dc_id, "hex_key"

        # 3. Check for 512-char hex (raw key, default to DC 2)
        if len(token) == 512 and all(c in "0123456789abcdefABCDEF" for c in token):
            auth_key = bytes.fromhex(token.lower())
            return auth_key, 2, "hex_key"

        # 4. Check for Telethon string session (starts with '1' and is ~353 chars)
        if token.startswith('1') and 300 <= len(token) <= 450:
            try:
                session = StringSession(token)
                key = session.auth_key.key if session.auth_key else None
                if key and len(key) == 256:
                    return key, session.dc_id, "telethon_string"
            except Exception:
                pass

        # 5. Check for Pyrogram string session (base64url, decodes to known struct)
        if 300 <= len(token) <= 900:
            try:
                cleaned_b64 = re.sub(r'[^A-Za-z0-9+/=_-]', '', token)
                padded = cleaned_b64 + "=" * (-len(cleaned_b64) % 4)
                data = base64.urlsafe_b64decode(padded)
                for fmt in (Storage.OLD_SESSION_STRING_FORMAT, Storage.OLD_SESSION_STRING_FORMAT_64, Storage.SESSION_STRING_FORMAT):
                    try:
                        unpacked = struct.unpack(fmt, data)
                        if len(unpacked) == 5:
                            dc_id = unpacked[0]
                            auth_key = unpacked[2]
                        elif len(unpacked) == 6:
                            dc_id = unpacked[0]
                            auth_key = unpacked[3]
                        else:
                            continue
                        if 1 <= dc_id <= 5 and len(auth_key) == 256:
                            return auth_key, dc_id, "pyrogram_string"
                    except struct.error:
                        continue
            except Exception:
                pass

    # ── Phase 4: Last resort — scan for any 512-char hex substring within the raw hex ──
    # If the entire input contains exactly 512+ hex chars (with junk around it), extract them.
    if len(hex_only) >= 512:
        # Try 513 (DC suffix) first since appending DC is more common
        if len(hex_only) == 513 and hex_only[-1] in "12345":
            try:
                dc_id = int(hex_only[-1])
                auth_key = bytes.fromhex(hex_only[:-1].lower())
                if len(auth_key) == 256:
                    return auth_key, dc_id, "hex_key"
            except Exception:
                pass
        # Try 513 (DC prefix)
        if len(hex_only) == 513 and hex_only[0] in "12345":
            try:
                dc_id = int(hex_only[0])
                auth_key = bytes.fromhex(hex_only[1:].lower())
                if len(auth_key) == 256:
                    return auth_key, dc_id, "hex_key"
            except Exception:
                pass
        # Try exact 512
        if len(hex_only) == 512:
            try:
                auth_key = bytes.fromhex(hex_only.lower())
                if len(auth_key) == 256:
                    return auth_key, 2, "hex_key"
            except Exception:
                pass
        # Try extracting 512 hex chars from a longer string (e.g. hex key with extra digits around it)
        if len(hex_only) > 513:
            # Use regex to find 512 contiguous hex in original cleaned text
            match = re.search(r'[0-9a-fA-F]{512}', text_clean)
            if match:
                try:
                    auth_key = bytes.fromhex(match.group().lower())
                    if len(auth_key) == 256:
                        return auth_key, 2, "hex_key"
                except Exception:
                    pass

    raise ValueError(
        "Could not detect any valid session string or hex key in your message.\n\n"
        "Supported formats:\n"
        "• A **Telethon** string session (starts with `1`)\n"
        "• A **Pyrogram** string session\n"
        "• A **hex auth key** (512 hex characters)\n"
        "• `dc_id:hex_key` (e.g. `2:aabbcc…`)\n"
        "• `dc_id` prefix (e.g. `4fc20c3…`)"
    )


def _parse_all_session_inputs(text):
    """
    Extract all valid session credentials (strings or hex keys) from text.
    Supports single inputs, multi-line pastes, and JSON arrays.
    Returns list of (auth_key_bytes, dc_id, input_type).
    """
    if not text or not isinstance(text, str):
        return []
    
    results = []
    seen_keys = set()

    # 1. First check if text is a JSON array
    try:
        data = json.loads(text.strip())
        if isinstance(data, list):
            for item in data:
                try:
                    p = _parse_session_input(json.dumps(item) if isinstance(item, dict) else str(item))
                    if p and p[0] not in seen_keys:
                        results.append(p)
                        seen_keys.add(p[0])
                except Exception:
                    pass
            if results:
                return results
    except Exception:
        pass
        
    # 2. Split text into lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        try:
            parsed = _parse_session_input(line)
            if parsed and parsed[0] not in seen_keys:
                results.append(parsed)
                seen_keys.add(parsed[0])
        except Exception:
            # Try tokens inside line
            parts = re.split(r'[,;\s]+', line)
            for part in parts:
                part = part.strip()
                if len(part) >= 40:
                    try:
                        p = _parse_session_input(part)
                        if p and p[0] not in seen_keys:
                            results.append(p)
                            seen_keys.add(p[0])
                    except Exception:
                        pass

    # 3. If line-by-line found nothing, try the entire text as a single blob
    if not results:
        try:
            single = _parse_session_input(text)
            if single and single[0] not in seen_keys:
                results.append(single)
                seen_keys.add(single[0])
        except Exception:
            pass
            
    return results

def parse_json_session_file(json_path, output_dir):
    """
    Parse a JSON file containing session data (single dict or a list of dicts)
    and produce .session SQLite files.
    Returns: a list of paths to the created .session files.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if not isinstance(data, (dict, list)):
        return []

    items = data if isinstance(data, list) else [data]
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(json_path))[0]
    
    created_paths = []
    
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
            
        auth_key_bytes = None
        dc_id = 2
        
        # Check session_string first
        session_string = (
            item.get("session_string")
            or item.get("string")
            or item.get("session")
            or item.get("session_str")
            or item.get("pyrogram_string")
            or item.get("telethon_string")
            or ""
        )
        if session_string:
            try:
                auth_key_bytes, dc_id, _ = _parse_session_input(session_string)
            except Exception:
                pass
                
        # If no session_string, check auth_key + dc_id
        if not auth_key_bytes:
            auth_key_raw = (
                item.get("auth_key")
                or item.get("authKey")
                or item.get("key")
                or item.get("hex")
                or item.get("hex_auth_key")
                or ""
            )
            dc_id = item.get("dc_id") or item.get("dc") or 2

            if isinstance(auth_key_raw, (list, tuple)):
                try:
                    if len(auth_key_raw) == 256 and all(isinstance(x, int) for x in auth_key_raw):
                        auth_key_bytes = bytes(auth_key_raw)
                except Exception:
                    pass
            elif isinstance(auth_key_raw, (bytes, bytearray)):
                if len(auth_key_raw) == 256:
                    auth_key_bytes = bytes(auth_key_raw)
            elif isinstance(auth_key_raw, str) and auth_key_raw:
                cleaned = auth_key_raw.replace(" ", "").replace("\n", "").strip()
                if ":" in cleaned:
                    parts = cleaned.split(":", 1)
                    if parts[0].isdigit():
                        dc_id = int(parts[0])
                        cleaned = parts[1]
                if len(cleaned) == 513 and cleaned[0] in "12345":
                    dc_id = int(cleaned[0])
                    cleaned = cleaned[1:]
                if len(cleaned) == 512 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
                    auth_key_bytes = bytes.fromhex(cleaned)
                else:
                    # Try base64
                    try:
                        padded = cleaned + "=" * (-len(cleaned) % 4)
                        decoded = base64.b64decode(padded)
                        if len(decoded) == 256:
                            auth_key_bytes = decoded
                    except Exception:
                        pass
                        
        if auth_key_bytes and len(auth_key_bytes) == 256:
            try:
                dc_id = int(dc_id)
                if dc_id < 1 or dc_id > 5:
                    dc_id = 2
                    
                ident = (
                    item.get("phone")
                    or item.get("phone_number")
                    or item.get("user_id")
                    or item.get("id")
                    or item.get("username")
                    or f"{idx + 1}"
                )
                clean_ident = sanitize_filename(str(ident))
                session_filename = f"{sanitize_filename(base_name)}_{clean_ident}.session"
                session_path = os.path.join(output_dir, session_filename)
                
                # If file already exists, add a unique suffix
                if os.path.exists(session_path):
                    name_part, ext_part = os.path.splitext(session_filename)
                    suffix = 1
                    while os.path.exists(os.path.join(output_dir, f"{name_part}_{suffix}{ext_part}")):
                        suffix += 1
                    session_path = os.path.join(output_dir, f"{name_part}_{suffix}{ext_part}")
                    
                ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
                session_obj = SQLiteSession(session_path.replace(".session", ""))
                session_obj.set_dc(dc_id, ip, port)
                session_obj.auth_key = AuthKey(data=auth_key_bytes)
                session_obj.save()
                session_obj.close()
                created_paths.append(session_path)

                # Accompanying metadata JSON
                stem = os.path.splitext(os.path.basename(session_path))[0]
                comp_json = os.path.join(output_dir, f"{stem}.json")
                try:
                    with open(comp_json, "w", encoding="utf-8") as jf:
                        json.dump(item, jf, indent=2)
                except Exception:
                    pass
            except Exception:
                pass
                
    return created_paths






# --- Session DB Sanitization & ZIP Export ---
def extract_session_phone_and_id(session_path):
    """
    Extract the authentic phone number and user ID of the account owner from
    a Telethon or Pyrogram SQLite session file without connecting.
    Guarantees that Telegram service numbers (42777, 777000, 178220800) are never
    mistaken for the user's phone number.
    """
    owner_phone = None
    owner_id = None
    owner_name = None
    owner_username = None

    stem = os.path.splitext(os.path.basename(session_path))[0]
    m_stem = re.search(r'(?:^|[^0-9])(\d{7,15})(?:[^0-9]|$)', stem)
    if m_stem and m_stem.group(1) not in ('42777', '777000', '178220800'):
        owner_phone = m_stem.group(1)

    conn = None
    try:
        conn = sqlite3.connect(session_path, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]

        if 'entities' in tables:
            try:
                row_0 = cur.execute('SELECT hash FROM entities WHERE id = 0 LIMIT 1').fetchone()
                if row_0 and row_0[0] and row_0[0] > 0:
                    cand_id = row_0[0]
                    cand_row = cur.execute('SELECT phone, username, name FROM entities WHERE id = ? LIMIT 1', (cand_id,)).fetchone()
                    if cand_row:
                        owner_id = cand_id
                        p_cand = cand_row[0]
                        if p_cand and str(p_cand).isdigit() and str(p_cand) not in ('42777', '777000'):
                            owner_phone = str(p_cand)
                        if cand_row[1]:
                            owner_username = str(cand_row[1])
                        if cand_row[2]:
                            owner_name = str(cand_row[2])
            except Exception:
                pass

            if not owner_phone:
                try:
                    q = 'SELECT id, phone, username, name FROM entities WHERE phone IS NOT NULL AND phone NOT IN (42777, 777000, 178220800) AND length(CAST(phone AS TEXT)) >= 7 ORDER BY id ASC LIMIT 1'
                    r = cur.execute(q).fetchone()
                    if r and r[1]:
                        if not owner_id and r[0] > 0:
                            owner_id = r[0]
                        owner_phone = str(r[1])
                        if not owner_username and r[2]:
                            owner_username = str(r[2])
                        if not owner_name and r[3]:
                            owner_name = str(r[3])
                except Exception:
                    pass

        if 'sessions' in tables and not owner_id:
            try:
                s_cols = [c[1] for c in cur.execute('PRAGMA table_info(sessions)').fetchall()]
                if 'user_id' in s_cols:
                    r = cur.execute('SELECT user_id FROM sessions WHERE user_id IS NOT NULL AND user_id > 0 LIMIT 1').fetchone()
                    if r and r[0] and r[0] not in (777000, 42777, 178220800):
                        owner_id = int(r[0])
            except Exception:
                pass

        if 'peers' in tables and not owner_phone:
            try:
                p_cols = [c[1] for c in cur.execute('PRAGMA table_info(peers)').fetchall()]
                if 'phone_number' in p_cols:
                    if owner_id:
                        r = cur.execute('SELECT phone_number, username FROM peers WHERE id = ? LIMIT 1', (owner_id,)).fetchone()
                        if r and r[0]:
                            digits = re.sub(r'\D', '', str(r[0]))
                            if digits and digits not in ('42777', '777000') and len(digits) >= 7:
                                owner_phone = digits
                            if not owner_username and r[1]:
                                owner_username = str(r[1])
                    if not owner_phone:
                        r = cur.execute("SELECT phone_number, id, username FROM peers WHERE phone_number IS NOT NULL AND phone_number != '' AND id NOT IN (777000, 42777, 178220800) LIMIT 1").fetchone()
                        if r and r[0]:
                            digits = re.sub(r'\D', '', str(r[0]))
                            if digits and digits not in ('42777', '777000') and len(digits) >= 7:
                                owner_phone = digits
                                if not owner_id and r[1] > 0:
                                    owner_id = r[1]
                                if not owner_username and r[2]:
                                    owner_username = str(r[2])
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return owner_phone, owner_id, owner_name, owner_username


async def session_export_name(session_path, used_names=None):
    used_names = used_names if used_names is not None else set()
    base_name = os.path.basename(session_path)
    stem = os.path.splitext(base_name)[0]

    phone, user_id, _, _ = extract_session_phone_and_id(session_path)
    if not phone:
        # Check if stem contains phone digits (7 to 15 digits)
        m = re.search(r'(?:^|[^0-9])(\d{7,15})(?:[^0-9]|$)', stem)
        if m and m.group(1) not in ('42777', '777000', '178220800'):
            phone = m.group(1)

    if phone and phone not in ('42777', '777000', '178220800'):
        final_stem = str(phone)
    elif stem not in ('42777', '777000') and not stem.startswith('42777_'):
        final_stem = sanitize_filename(stem)
    elif user_id and user_id not in (777000, 42777, 178220800):
        final_stem = f"account_{user_id}"
    else:
        final_stem = f"session_{int(time.time())}"

    candidate = f"{final_stem}.session"
    suffix = 1
    while candidate in used_names:
        candidate = f"{final_stem}_{suffix}.session"
        suffix += 1
    used_names.add(candidate)
    return candidate, phone, user_id


def make_offline_telethon_string_session(dc_id, auth_key_bytes, server_ip=None, port=443):
    """Generate a 100% spec-compliant Telethon StringSession (1ApWap...) directly from DC & AuthKey without connecting."""
    try:
        if not auth_key_bytes or len(auth_key_bytes) != 256:
            return None
        import ipaddress
        if not server_ip:
            DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
            server_ip = DC_MAP_IPV4.get(dc_id, "149.154.167.51")
        ip = ipaddress.ip_address(server_ip).packed
        raw = struct.pack('>B{}sH256s'.format(len(ip)), int(dc_id or 2), ip, int(port or 443), auth_key_bytes)
        return '1' + base64.urlsafe_b64encode(raw).decode()
    except Exception:
        return None


def make_offline_pyrogram_string_session(dc_id, auth_key_bytes, user_id=0, api_id=None, test_mode=False, is_bot=False):
    """Generate a 100% spec-compliant Pyrogram StringSession directly from DC & AuthKey without connecting."""
    try:
        if not auth_key_bytes or len(auth_key_bytes) != 256:
            return None
        effective_api = int(api_id or API_ID or 2040)
        raw = struct.pack('>BI?256sQ?', int(dc_id or 2), effective_api, bool(test_mode), auth_key_bytes, int(user_id or 0), bool(is_bot))
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')
    except Exception:
        return None


def export_clean_session_db(source_session_path, target_path, target_format="telethon", phone=None, user_id=None):
    """
    Export a 100% spec-compliant, pristine SQLite .session file that works in
    ANY external session manager bot without schema or unpack errors.
    """
    dc_id = 2
    auth_key = None
    username = None
    name = None

    conn = None
    try:
        conn = sqlite3.connect(source_session_path, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]

        if "sessions" in tables:
            try:
                row = cur.execute(
                    "SELECT dc_id, auth_key FROM sessions WHERE auth_key IS NOT NULL AND length(auth_key) = 256 LIMIT 1"
                ).fetchone()
                if not row:
                    row = cur.execute("SELECT dc_id, auth_key FROM sessions LIMIT 1").fetchone()
                if row:
                    dc_id = row[0] or 2
                    auth_key = row[1]
            except Exception:
                pass

            if not user_id:
                try:
                    s_cols = [c[1] for c in cur.execute("PRAGMA table_info(sessions)").fetchall()]
                    if "user_id" in s_cols:
                        row = cur.execute("SELECT user_id FROM sessions WHERE user_id IS NOT NULL AND user_id != 0 LIMIT 1").fetchone()
                        if row and row[0]:
                            user_id = int(row[0])
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    db_phone, db_uid, db_name, db_user = extract_session_phone_and_id(source_session_path)
    if not phone and db_phone:
        phone = db_phone
    if not user_id and db_uid:
        user_id = db_uid
    if not username and db_user:
        username = db_user
    if not name and db_name:
        name = db_name

    if not phone:
        stem = os.path.splitext(os.path.basename(source_session_path))[0]
        m = re.search(r'(?:^|[^0-9])(\d{7,15})(?:[^0-9]|$)', stem)
        if m and m.group(1) not in ('42777', '777000', '178220800'):
            phone = m.group(1)

    if not auth_key or len(auth_key) != 256:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_session_path, target_path)
        return False, dc_id, auth_key, phone, user_id

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path):
        try:
            os.remove(target_path)
        except Exception:
            pass

    out_conn = sqlite3.connect(target_path)
    try:
        out_cur = out_conn.cursor()
        if target_format == "telethon":
            DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
            server = DC_MAP_IPV4.get(dc_id, "149.154.167.51")

            out_cur.executescript("""
                CREATE TABLE version (version integer primary key);
                CREATE TABLE sessions (
                    dc_id integer primary key,
                    server_address text,
                    port integer,
                    auth_key blob,
                    takeout_id integer,
                    tmp_auth_key blob
                );
                CREATE TABLE entities (
                    id integer primary key,
                    hash integer not null,
                    username text,
                    phone integer,
                    name text,
                    date integer
                );
                CREATE TABLE sent_files (
                    md5_digest blob,
                    file_size integer,
                    type integer,
                    id integer,
                    hash integer,
                    primary key(md5_digest, file_size, type)
                );
                CREATE TABLE update_state (
                    id integer primary key,
                    pts integer,
                    qts integer,
                    date integer,
                    seq integer
                );
            """)
            out_cur.execute("INSERT INTO version VALUES (8)")
            out_cur.execute("INSERT INTO sessions VALUES (?, ?, 443, ?, NULL, NULL)", (dc_id, server, auth_key))
            int_phone = int(phone) if phone and str(phone).isdigit() else 0
            uid = user_id or (int_phone if int_phone else 0)
            if uid:
                out_cur.execute(
                    "INSERT OR REPLACE INTO entities VALUES (0, ?, NULL, NULL, NULL, ?)",
                    (uid, int(time.time()))
                )
            if uid or int_phone or username or name:
                out_cur.execute(
                    "INSERT OR REPLACE INTO entities VALUES (?, 0, ?, ?, ?, ?)",
                    (uid, username, int_phone if int_phone else None, name or "", int(time.time()))
                )
        else:
            DC_MAP_IPV4 = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}
            server = DC_MAP_IPV4.get(dc_id, "149.154.167.51")
            out_cur.executescript("""
                CREATE TABLE version (number integer primary key);
                CREATE TABLE sessions (
                    dc_id     INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port      INTEGER,
                    api_id    INTEGER,
                    test_mode INTEGER,
                    auth_key  BLOB,
                    date      INTEGER NOT NULL DEFAULT 0,
                    user_id   INTEGER,
                    is_bot    INTEGER,
                    tmp_auth_key BLOB
                );
                CREATE TABLE peers (
                    id             INTEGER PRIMARY KEY,
                    access_hash    INTEGER,
                    type           TEXT NOT NULL,
                    username       TEXT,
                    phone_number   TEXT,
                    last_update_on INTEGER NOT NULL DEFAULT 0
                );
            """)
            out_cur.execute("INSERT INTO version VALUES (3)")
            int_phone = int(phone) if phone and str(phone).isdigit() else 0
            uid = user_id or (int_phone if int_phone else 0)
            out_cur.execute(
                "INSERT INTO sessions VALUES (?, ?, 443, ?, 0, ?, 0, ?, 0, NULL)",
                (dc_id, server, API_ID, auth_key, uid)
            )
            phone_str = f"+{phone}" if phone and not str(phone).startswith("+") else str(phone or "")
            if uid or phone_str or username:
                out_cur.execute(
                    "INSERT OR REPLACE INTO peers VALUES (?, 0, 'user', ?, ?, ?)",
                    (uid, username, phone_str, int(time.time()))
                )

        out_conn.commit()
        return True, dc_id, auth_key, phone, user_id
    finally:
        out_conn.close()


# Take a list of session files and pack them into a universal ZIP file.
async def build_zip_from_sessions(session_paths, file_name, target_format="universal"):
    """
    Take a list of session files and pack them into a universal ZIP file
    containing pristine, spec-compliant SQLite sessions + accompanying JSON configs.
    """
    temp_dir = tempfile.mkdtemp(prefix="acs_pkg_")
    zip_path = os.path.join(temp_dir, file_name)
    used_names = set()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if target_format in ("universal", "auto") or not target_format:
            # UNIVERSAL DUAL-FORMAT BUNDLE:
            # Package both Telethon and Pyrogram formatted sessions into the archive!
            tele_stage_dir = os.path.join(temp_dir, "telethon_sessions")
            pyro_stage_dir = os.path.join(temp_dir, "pyro_sessions")
            os.makedirs(tele_stage_dir, exist_ok=True)
            os.makedirs(pyro_stage_dir, exist_ok=True)

            strings_lines = [
                "================================================================================",
                "                       UNIVERSAL TELEGRAM SESSION PACK                         ",
                "================================================================================",
                f"Total Accounts : {len(session_paths)}",
                f"Generated At   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                "",
                "This archive provides 100% universal compatibility for ANY Telegram bot or tool:",
                " • 'telethon/' -> Pristine Telethon SQLite .session + .json device metadata",
                " • 'pyrogram/' -> Pristine Pyrogram/Kurigram SQLite .session + .json device metadata",
                " • Root files  -> Spec-compliant session copies ready for root-only importers",
                "================================================================================\n",
            ]

            for session_path in session_paths:
                arcname, phone, user_id = await session_export_name(session_path, used_names)

                # 1. Telethon clean export
                tele_session_path = os.path.join(tele_stage_dir, arcname)
                _, dc_id, auth_key, out_phone, out_uid = export_clean_session_db(session_path, tele_session_path, target_format="telethon", phone=phone, user_id=user_id)
                phone = out_phone or phone
                user_id = out_uid or user_id

                stem = os.path.splitext(arcname)[0]
                clean_phone_val = f"+{phone}" if phone and not str(phone).startswith("+") else (phone or stem)
                phone_digits = re.sub(r"\D", "", clean_phone_val)
                uid_val = user_id or (int(phone_digits) if phone_digits else 0)

                # Save pure Telethon session into telethon/ folder
                zf.write(tele_session_path, arcname=f"telethon/{arcname}")

                # 2. Pyrogram clean export into pyrogram/ folder
                pyro_session_path = os.path.join(pyro_stage_dir, arcname)
                export_clean_session_db(session_path, pyro_session_path, target_format="pyrogram", phone=phone, user_id=user_id)
                zf.write(pyro_session_path, arcname=f"pyrogram/{arcname}")

                # Place canonical Telethon session in root for external checkers and bots
                zf.write(tele_session_path, arcname=arcname)

                # Generate offline string sessions and hex keys
                tele_str = make_offline_telethon_string_session(dc_id, auth_key) if auth_key and len(auth_key) == 256 else None
                pyro_str = make_offline_pyrogram_string_session(dc_id, auth_key, user_id=uid_val) if auth_key and len(auth_key) == 256 else None
                hex_str = f"{dc_id}:{auth_key.hex()}" if auth_key and len(auth_key) == 256 else None

                # 3. Accompanying JSON metadata with authentic mobile parameters (prevents freezing/fraud flags)
                dev_p = _get_device_params_for_session(os.path.dirname(session_path), stem)
                meta_content = {
                    "session_file": stem,
                    "phone": clean_phone_val,
                    "phone_number": clean_phone_val,
                    "phone_digits": phone_digits,
                    "register_time": int(time.time()),
                    "app_id": dev_p.get("api_id", API_ID),
                    "api_id": dev_p.get("api_id", API_ID),
                    "app_hash": dev_p.get("api_hash", API_HASH),
                    "api_hash": dev_p.get("api_hash", API_HASH),
                    "device": dev_p.get("device_model", SAFE_DEVICE_MODEL),
                    "device_model": dev_p.get("device_model", SAFE_DEVICE_MODEL),
                    "sdk": dev_p.get("system_version", SAFE_SYSTEM_VERSION),
                    "system_version": dev_p.get("system_version", SAFE_SYSTEM_VERSION),
                    "app_version": dev_p.get("app_version", SAFE_APP_VERSION),
                    "lang_code": dev_p.get("lang_code", SAFE_LANG_CODE),
                    "system_lang_code": dev_p.get("system_lang_code", SAFE_SYSTEM_LANG_CODE),
                    "user_id": uid_val,
                    "id": uid_val,
                    "dc_id": dc_id,
                    "format": "UNIVERSAL",
                    "telethon_string": tele_str or "",
                    "pyrogram_string": pyro_str or "",
                    "hex_auth_key": hex_str or ""
                }
                json_name = f"{stem}.json"
                json_path = os.path.join(temp_dir, json_name)
                try:
                    with open(json_path, "w", encoding="utf-8") as jf:
                        json.dump(meta_content, jf, indent=2)
                    zf.write(json_path, arcname=f"telethon/{json_name}")
                    zf.write(json_path, arcname=f"pyrogram/{json_name}")
                    zf.write(json_path, arcname=json_name)
                except Exception:
                    pass

                strings_lines.append(f"📱 Account: {clean_phone_val} (ID: {uid_val} | DC: {dc_id})")
                if hex_str:
                    strings_lines.append(f" ├ Auth Key (Hex)  : {hex_str}")
                if tele_str:
                    strings_lines.append(f" ├ Telethon String : {tele_str}")
                if pyro_str:
                    strings_lines.append(f" └ Pyrogram String : {pyro_str}")
                strings_lines.append("────────────────────────────────────────────────────────────────────────────────")

            strings_path = os.path.join(temp_dir, "sessions_strings.txt")
            try:
                with open(strings_path, "w", encoding="utf-8") as sf:
                    sf.write("\n".join(strings_lines))
                zf.write(strings_path, arcname="sessions_strings.txt")
            except Exception:
                pass

            readme_path = os.path.join(temp_dir, "README.txt")
            try:
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(
                        "================================================================================\n"
                        "                   UNIVERSAL TELEGRAM SESSION PACK INSTRUCTIONS                  \n"
                        "================================================================================\n\n"
                        "This pack contains pristine sessions compatible with ALL external Telegram tools:\n\n"
                        "1. FOR TELETHON BOTS (e.g. AccStore, Telegram Prime, Telegram Expert, custom scripts):\n"
                        "   - Use the .session and .json files inside the 'telethon/' folder.\n"
                        "   - Or use the '*_telethon.session' files in the root folder.\n\n"
                        "2. FOR PYROGRAM BOTS (e.g. Kurigram, Hydrogram, Pyrogram v1/v2 bots & userbots):\n"
                        "   - Use the .session and .json files inside the 'pyrogram/' folder.\n"
                        "   - Or use the '*_pyrogram.session' files in the root folder.\n\n"
                        "3. FOR STRING SESSION BOTS:\n"
                        "   - Check 'sessions_strings.txt' for instant Telethon & Pyrogram string sessions.\n\n"
                        "================================================================================\n"
                    )
                zf.write(readme_path, arcname="README.txt")
            except Exception:
                pass

        else:
            # Single format export (telethon, pyrogram, or auto)
            clean_stage_dir = os.path.join(temp_dir, "clean_sessions")
            os.makedirs(clean_stage_dir, exist_ok=True)
            for session_path in session_paths:
                arcname, phone, user_id = await session_export_name(session_path, used_names)

                fmt = target_format
                if fmt == "auto" or not fmt:
                    fmt = detect_session_type(session_path)
                    if fmt not in ("telethon", "pyrogram"):
                        fmt = "telethon"

                clean_session_path = os.path.join(clean_stage_dir, arcname)
                _, dc_id, auth_key, out_phone, out_uid = export_clean_session_db(session_path, clean_session_path, target_format=fmt, phone=phone, user_id=user_id)
                phone = out_phone or phone
                user_id = out_uid or user_id
                zf.write(clean_session_path, arcname=arcname)

                stem = os.path.splitext(arcname)[0]
                clean_phone_val = f"+{phone}" if phone and not str(phone).startswith("+") else (phone or stem)
                phone_digits = re.sub(r"\D", "", clean_phone_val)
                uid_val = user_id or (int(phone_digits) if phone_digits else 0)

                tele_str = make_offline_telethon_string_session(dc_id, auth_key) if auth_key and len(auth_key) == 256 else None
                pyro_str = make_offline_pyrogram_string_session(dc_id, auth_key, user_id=uid_val) if auth_key and len(auth_key) == 256 else None
                hex_str = f"{dc_id}:{auth_key.hex()}" if auth_key and len(auth_key) == 256 else None

                json_name = f"{stem}.json"
                dev_p = _get_device_params_for_session(os.path.dirname(session_path), stem)
                meta_content = {
                    "session_file": stem,
                    "phone": clean_phone_val,
                    "phone_number": clean_phone_val,
                    "phone_digits": phone_digits,
                    "register_time": int(time.time()),
                    "app_id": dev_p.get("api_id", API_ID),
                    "api_id": dev_p.get("api_id", API_ID),
                    "app_hash": dev_p.get("api_hash", API_HASH),
                    "api_hash": dev_p.get("api_hash", API_HASH),
                    "device": dev_p.get("device_model", SAFE_DEVICE_MODEL),
                    "device_model": dev_p.get("device_model", SAFE_DEVICE_MODEL),
                    "sdk": dev_p.get("system_version", SAFE_SYSTEM_VERSION),
                    "system_version": dev_p.get("system_version", SAFE_SYSTEM_VERSION),
                    "app_version": dev_p.get("app_version", SAFE_APP_VERSION),
                    "lang_code": dev_p.get("lang_code", SAFE_LANG_CODE),
                    "system_lang_code": dev_p.get("system_lang_code", SAFE_SYSTEM_LANG_CODE),
                    "user_id": uid_val,
                    "id": uid_val,
                    "dc_id": dc_id,
                    "format": fmt.upper(),
                    "telethon_string": tele_str or "",
                    "pyrogram_string": pyro_str or "",
                    "hex_auth_key": hex_str or ""
                }
                json_file_path = os.path.join(clean_stage_dir, json_name)
                try:
                    with open(json_file_path, "w", encoding="utf-8") as jf:
                        json.dump(meta_content, jf, indent=2)
                    zf.write(json_file_path, arcname=json_name)
                except Exception:
                    pass

    return zip_path, temp_dir


# Send organized ZIP files back to the user, sorted by category.
# Example: one zip for "Alive" sessions, another for "Dead".
async def send_classified_zips(chat_id, groups, labels=None):
    for base_name, items in groups.items():
        if not items:
            continue
        zip_path, temp_dir = await build_zip_from_sessions(items, f"{base_name}_{len(items)}.zip")
        try:
            caption = None
            if labels and base_name in labels:
                meta = labels[base_name]
                caption = format_result_caption(meta["label"], len(items), meta.get("icon", "✅"))
            await app.send_document(chat_id, zip_path, caption=caption)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Format Converters
# Pyrogram and Telethon use different key formats.
# These functions melt down one key and reshape it into the other brand!
# ─────────────────────────────────────────────────────────────────────────────


def convert_pyro_to_telethon_file(pyro_path, output_folder):
    filename = os.path.basename(pyro_path)
    new_name = filename.replace("pyro_", "telethon_") if "pyro_" in filename else f"telethon_{filename}"
    target = os.path.join(output_folder, new_name if new_name.endswith(".session") else f"{new_name}.session")
    success, _, _, _, _ = export_clean_session_db(pyro_path, target, target_format="telethon")
    return target if success else None


async def convert_telethon_to_pyro_file(tele_path, output_folder):
    """Convert a Telethon .session file to Pyrogram format using pure SQLite."""
    filename = os.path.basename(tele_path)
    new_name = filename.replace("telethon_", "pyro_") if "telethon_" in filename else f"pyro_{filename}"
    target = os.path.join(output_folder, new_name if new_name.endswith(".session") else f"{new_name}.session")
    success, _, _, _, _ = export_clean_session_db(tele_path, target, target_format="pyrogram")
    if success:
        return target, None
    return None, "Failed to export Pyrogram session"




def safe_extract_archive(archive_path, dest_dir):
    """
    Resilient multi-tier archive extractor (ZIP, RAR, 7Z, TAR, GZ, BZ2, XZ, etc.)
    Handles UTF-8/CP437, Deflate64, LZMA, RAR4/RAR5, nested folders, damaged headers.
    Secures against Zip Slip directory traversal attacks.
    """
    os.makedirs(dest_dir, exist_ok=True)
    extracted = False
    lower_path = archive_path.lower()

    # Strategy 1: Python zipfile (fastest for valid standard ZIP archives)
    if lower_path.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.infolist():
                    filename = member.filename
                    if not (member.flag_bits & 0x800):
                        try:
                            filename = member.filename.encode("cp437").decode("utf-8")
                        except Exception:
                            filename = member.filename
                    target_path = os.path.abspath(os.path.join(dest_dir, filename))
                    if os.path.commonpath([dest_dir, target_path]) == dest_dir:
                        if member.is_dir():
                            os.makedirs(target_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            with zf.open(member) as src, open(target_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            extracted = True
        except Exception as e:
            logging.info(f"[safe_extract_archive] zipfile failed on {archive_path} ({e}), falling back to 7z/unzip")

    # Strategy 2: Python tarfile for tarballs
    if not extracted and (
        tarfile.is_tarfile(archive_path)
        or any(lower_path.endswith(ext) for ext in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"))
    ):
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                for member in tf.getmembers():
                    target_path = os.path.abspath(os.path.join(dest_dir, member.name))
                    if os.path.commonpath([dest_dir, target_path]) == dest_dir:
                        if member.isdir():
                            os.makedirs(target_path, exist_ok=True)
                        elif member.isfile():
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            with tf.extractfile(member) as src, open(target_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            extracted = True
        except Exception as e:
            logging.info(f"[safe_extract_archive] tarfile failed on {archive_path}: {e}")

    # Strategy 3: 7z command-line (Handles RAR, 7z, Deflate64, LZMA, header issues)
    if not extracted and shutil.which("7z"):
        try:
            proc = subprocess.run(
                ["7z", "x", "-y", "-p", f"-o{dest_dir}", archive_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90
            )
            if proc.returncode in (0, 1):
                extracted = True
        except Exception as e:
            logging.info(f"[safe_extract_archive] 7z error on {archive_path}: {e}")

    # Strategy 4: unrar command-line (for RAR archives)
    if not extracted and shutil.which("unrar"):
        try:
            proc = subprocess.run(
                ["unrar", "x", "-o+", "-p-", archive_path, dest_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90
            )
            if proc.returncode == 0:
                extracted = True
        except Exception as e:
            logging.info(f"[safe_extract_archive] unrar error on {archive_path}: {e}")

    # Strategy 5: unzip command-line fallback
    if not extracted and shutil.which("unzip"):
        try:
            proc = subprocess.run(
                ["unzip", "-o", "-q", "-P", "", archive_path, "-d", dest_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90
            )
            if proc.returncode in (0, 1):
                extracted = True
        except Exception as e:
            logging.info(f"[safe_extract_archive] unzip error on {archive_path}: {e}")

    return extracted


def _convert_tdata_to_sessions(tdata_path, target_dir):
    """Convert a Telegram Desktop tdata folder to Telethon SQLite .session files."""
    converted = []
    try:
        from opentele.td import TDesktop
        tdesk = TDesktop(basePath=tdata_path)
        if not tdesk.isLoaded():
            tdesk.LoadTData()

        if not getattr(tdesk, "accountsCount", 0):
            return converted

        for idx, acc in enumerate(tdesk.accounts):
            auth_key = getattr(getattr(acc, "authKey", None), "key", None)
            if not auth_key or len(auth_key) != 256:
                continue
            dc_id = getattr(acc, "MainDcId", 2) or 2
            user_id = getattr(acc, "UserId", 0) or 0
            phone = getattr(acc, "phone", None)

            clean_stem = str(phone) if phone else (str(user_id) if user_id else f"tdata_{int(time.time())}_{idx}")
            clean_stem = sanitize_filename(clean_stem)
            target_session = os.path.join(target_dir, f"{clean_stem}.session")
            suffix = 1
            while os.path.exists(target_session):
                target_session = os.path.join(target_dir, f"{clean_stem}_{suffix}.session")
                suffix += 1

            ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
            session_obj = SQLiteSession(target_session.replace(".session", ""))
            session_obj.set_dc(dc_id, ip, port)
            session_obj.auth_key = AuthKey(data=auth_key)
            session_obj.save()
            session_obj.close()
            converted.append(target_session)

            clean_stem_final = os.path.splitext(os.path.basename(target_session))[0]
            meta_content = {
                "session_file": clean_stem_final,
                "phone": str(phone) if phone else "",
                "user_id": user_id,
                "dc_id": dc_id,
                "app_id": API_ID,
                "app_hash": API_HASH,
                "device": SAFE_DEVICE_MODEL,
                "sdk": SAFE_SYSTEM_VERSION,
                "app_version": SAFE_APP_VERSION,
                "format": "TELETHON",
            }
            try:
                with open(os.path.join(target_dir, f"{clean_stem_final}.json"), "w", encoding="utf-8") as jf:
                    json.dump(meta_content, jf, indent=2)
            except Exception:
                pass
    except Exception as e:
        logging.warning(f"[extract_sessions] Error converting tdata from {tdata_path}: {e}")
    return converted


async def extract_sessions_from_zip(source_zip_path, target_dir, clean=True):
    os.makedirs(target_dir, exist_ok=True)
    if clean:
        for name in os.listdir(target_dir):
            if name.endswith(".session") or name.endswith(".json"):
                try:
                    os.remove(os.path.join(target_dir, name))
                except Exception:
                    pass

    session_count = 0
    with tempfile.TemporaryDirectory(prefix="acs_ext_") as temp_extract_dir:
        success = safe_extract_archive(source_zip_path, temp_extract_dir)
        if not success:
            logging.warning(f"[extract_sessions_from_zip] Failed to extract archive: {source_zip_path}")

        # Recursively unpack any nested archives within temp_extract_dir (up to 3 levels)
        nested_exts = (".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz", ".bz2", ".xz")
        for _ in range(3):
            found_nested = False
            for root, dirs, files in os.walk(temp_extract_dir):
                if "__MACOSX" in root:
                    continue
                for file in files:
                    low = file.lower()
                    if any(low.endswith(ext) for ext in nested_exts):
                        sub_archive = os.path.join(root, file)
                        sub_dest = os.path.join(root, f"_nested_{file}")
                        if not os.path.exists(sub_dest):
                            os.makedirs(sub_dest, exist_ok=True)
                            if safe_extract_archive(sub_archive, sub_dest):
                                found_nested = True
                                try:
                                    os.remove(sub_archive)
                                except Exception:
                                    pass
            if not found_nested:
                break

        # Check for tdata folders anywhere in the extracted directory
        for root, dirs, files in os.walk(temp_extract_dir):
            if "__MACOSX" in root:
                continue
            for d in list(dirs):
                low_d = d.lower()
                full_d = os.path.join(root, d)
                is_tdata = (low_d == "tdata")
                if not is_tdata:
                    try:
                        sub_files = [f.lower() for f in os.listdir(full_d)]
                        if "key_datas" in sub_files or any(f.startswith("d87fb344234c170e") for f in sub_files):
                            is_tdata = True
                    except Exception:
                        pass

                if is_tdata:
                    tdata_sessions = _convert_tdata_to_sessions(full_d, target_dir)
                    session_count += len(tdata_sessions)
                    try:
                        dirs.remove(d)
                    except ValueError:
                        pass

        # 1. Process standard .session files and SQLite databases with smart deduplication
        processed_session_basenames = set()
        seen_keys = set()
        session_candidates = []

        skip_exts = (
            ".json", ".txt", ".log", ".py", ".sh", ".md", ".png", ".jpg", ".jpeg",
            ".gif", ".mp3", ".mp4", ".pdf", ".exe", ".dll", ".so", ".zip", ".rar",
            ".7z", ".tar", ".gz", ".bz2", ".xz"
        )

        for root, dirs, files in os.walk(temp_extract_dir):
            if "__MACOSX" in root:
                continue
            is_root = (root == temp_extract_dir)
            for file in files:
                low = file.lower()
                full_p = os.path.join(root, file)

                if low.endswith(".session"):
                    priority = 0 if is_root else 2
                    if "_telethon" in low or "_pyrogram" in low:
                        priority += 1
                    session_candidates.append((priority, full_p, file))
                elif not any(low.endswith(x) for x in skip_exts):
                    # Check if it's an SQLite session without the .session extension
                    try:
                        if os.path.getsize(full_p) >= 1024:
                            with open(full_p, "rb") as fh:
                                header = fh.read(16)
                            if header == b"SQLite format 3\x00":
                                _c = sqlite3.connect(full_p, timeout=2)
                                tables = [r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                                _c.close()
                                if "sessions" in tables or "peers" in tables:
                                    priority = 1 if is_root else 3
                                    session_candidates.append((priority, full_p, f"{file}.session"))
                    except Exception:
                        pass

        session_candidates.sort(key=lambda x: x[0])

        for _, src_path, file in session_candidates:
            name_only, ext = os.path.splitext(file)
            clean_canonical = re.sub(r'_(telethon|pyrogram).*$', '', name_only)

            key_sig = None
            try:
                _c = sqlite3.connect(src_path, timeout=5)
                _r = _c.execute("SELECT auth_key FROM sessions WHERE auth_key IS NOT NULL LIMIT 1").fetchone()
                if _r and _r[0]:
                    key_sig = bytes(_r[0])[:16]
                _c.close()
            except Exception:
                pass

            if key_sig and key_sig in seen_keys:
                continue
            if not key_sig and clean_canonical in processed_session_basenames:
                continue

            if key_sig:
                seen_keys.add(key_sig)
            processed_session_basenames.add(clean_canonical)
            processed_session_basenames.add(name_only)

            # Determine clean target filename using real account phone if possible
            real_phone, _, _, _ = extract_session_phone_and_id(src_path)
            if real_phone and real_phone not in ('42777', '777000', '178220800'):
                clean_name = f"{real_phone}.session"
            else:
                m_dig = re.search(r'(\d{7,15})', clean_canonical)
                if m_dig and m_dig.group(1) not in ('42777', '777000', '178220800'):
                    clean_name = f"{m_dig.group(1)}.session"
                elif clean_canonical not in ('42777', '777000') and not clean_canonical.startswith('42777_'):
                    clean_name = f"{sanitize_filename(clean_canonical)}.session"
                else:
                    clean_name = f"account_{int(time.time())}_{session_count + 1}.session"

            target = os.path.join(target_dir, clean_name)
            suffix = 1
            while os.path.exists(target):
                base_part = os.path.splitext(clean_name)[0]
                target = os.path.join(target_dir, f"{base_part}_{suffix}.session")
                suffix += 1
            shutil.copy2(src_path, target)
            session_count += 1

            # If an accompanying .json existed for this session anywhere in the extracted tree, copy it
            dir_src = os.path.dirname(src_path)
            json_copied = False
            for jcand in (os.path.join(dir_src, f"{name_only}.json"), os.path.join(dir_src, f"{clean_canonical}.json")):
                if os.path.isfile(jcand):
                    clean_json_name = os.path.splitext(os.path.basename(target))[0] + ".json"
                    target_json = os.path.join(target_dir, clean_json_name)
                    try:
                        shutil.copy2(jcand, target_json)
                        json_copied = True
                    except Exception:
                        pass
                    break
            if not json_copied:
                for root, dirs, files in os.walk(temp_extract_dir):
                    if f"{name_only}.json" in files or f"{clean_canonical}.json" in files:
                        matching_f = f"{name_only}.json" if f"{name_only}.json" in files else f"{clean_canonical}.json"
                        clean_json_name = os.path.splitext(os.path.basename(target))[0] + ".json"
                        try:
                            shutil.copy2(os.path.join(root, matching_f), os.path.join(target_dir, clean_json_name))
                        except Exception:
                            pass
                        break

        # 1.5 Process .json files if no corresponding .session was found
        for root, dirs, files in os.walk(temp_extract_dir):
            if "__MACOSX" in root:
                continue
            for file in files:
                if file.lower().endswith(".json"):
                    name_only = os.path.splitext(file)[0]
                    if name_only not in processed_session_basenames:
                        src_path = os.path.join(root, file)
                        new_session_paths = parse_json_session_file(src_path, target_dir)
                        session_count += len(new_session_paths)

        # 1.6 Process .txt files if no sessions were extracted
        if session_count == 0:
            for root, dirs, files in os.walk(temp_extract_dir):
                if "__MACOSX" in root:
                    continue
                for file in files:
                    if file.lower().endswith(".txt"):
                        txt_path = os.path.join(root, file)
                        try:
                            with open(txt_path, "r", encoding="utf-8", errors="ignore") as fh:
                                lines = fh.readlines()
                            for line in lines:
                                line_str = line.strip()
                                if not line_str:
                                    continue
                                try:
                                    auth_key, dc_id, _ = _parse_session_input(line_str)
                                    if auth_key and len(auth_key) == 256:
                                        session_count += 1
                                        sess_name = f"txt_session_{session_count}.session"
                                        sess_path = os.path.join(target_dir, sess_name)
                                        ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
                                        s_obj = SQLiteSession(sess_path.replace(".session", ""))
                                        s_obj.set_dc(dc_id, ip, port)
                                        s_obj.auth_key = AuthKey(data=auth_key)
                                        s_obj.save()
                                        s_obj.close()
                                except Exception:
                                    pass
                        except Exception:
                            pass

    return session_count

extract_sessions_from_archive = extract_sessions_from_zip



# --- Interface Keyboards & Menus ---
def start_keyboard(user_id=None):
    rows = [
        [
            InlineKeyboardButton("➕ Create Session (Telethon)", callback_data="create|telethon"),
            InlineKeyboardButton("➕ Create Session (Pyrogram)", callback_data="create|pyrogram"),
        ],
        [
            InlineKeyboardButton("🔑 Input String / Hex Key", callback_data="start_logout"),
            InlineKeyboardButton("📧 Mail Settings", callback_data="mail_settings_open"),
        ],
    ]
    if user_id and is_admin(user_id):
        rows.append([InlineKeyboardButton("📦 My Packs", callback_data="user_packs:0")])
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel_open")])
    extra_row = []
    if CHANNEL_LINK:
        extra_row.append(InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK))
    if SUPPORT_LINK:
        extra_row.append(InlineKeyboardButton("💬 Support", url=SUPPORT_LINK))
    if extra_row:
        rows.append(extra_row)
    return InlineKeyboardMarkup(rows)


GLOBAL_ANNOUNCEMENT = ""

def start_menu_text():
    """Build the /start welcome message."""
    try:
        from proxy_manager import proxy_pool
        if proxy_pool and proxy_pool.total_count > 0:
            alive_cnt = len(proxy_pool.get_alive_nodes())
            proxy_line = f" ├ 📧 IMAP Proxies: `{alive_cnt}/{proxy_pool.total_count} Active` (Accounts: Direct)\n"
        else:
            proxy_line = " ├ 🌐 Network: `Direct VPS Connection`\n"
    except Exception:
        proxy_line = ""
    announcement_block = f"📢 **Announcement:**\n{GLOBAL_ANNOUNCEMENT}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" if GLOBAL_ANNOUNCEMENT else ""
    return (
        f"⚡ **{PLATFORM_NAME}** — Premium Session Control\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{announcement_block}"
        "📥 **Quick Start / Upload Session**\n"
        "Send or forward any of the following to begin:\n"
        " • 📦 `.zip` archive containing session files\n"
        " • 📱 Raw `.session` file (Telethon/Pyrogram)\n"
        " • 📄 `.json` descriptor file with session key data\n\n"
        "🔑 **Direct Authentication**\n"
        " • Paste a **Session String** or **Hex Auth Key** directly into the chat.\n"
        " • Or click **Manage by String/Key** below to input credentials.\n\n"
        "⚙️ **System Performance**\n"
        f" ├ ⏱ Uptime: `{format_uptime()}`\n"
        f"{proxy_line}"
        f" └ 💎 Engine: `v4.2.0-Stable`"
    )


# Buttons for managing devices (list, remove, protect)
def devices_keyboard(zip_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 Show Devices", callback_data=f"dev|list|{zip_id}"),
                InlineKeyboardButton("🗑 Revoke Devices", callback_data=f"dev|confirm_remove|{zip_id}"),
            ],
            [
                InlineKeyboardButton("🛡 Whitelist Device", callback_data=f"dev|protect|{zip_id}"),
                InlineKeyboardButton("📊 Sort by Devices", callback_data=f"dev|sort|{zip_id}"),
            ],
            [InlineKeyboardButton("🔙 Return", callback_data=f"dev|back|{zip_id}")],
        ]
    )


# The MAIN operations menu — all the things you can do with a zip.
# This is the big menu with all the powerful buttons!
def operations_keyboard(zip_id):
    current_format = zip_display_format(zip_id)
    session_count = len(get_session_files(zip_id))
    
    meta = load_meta()
    zip_meta = meta.get("zips", {}).get(str(zip_id), {})
    is_direct = zip_meta.get("is_direct", False)
    
    rows = []
    
    if is_direct:
        rows.append([InlineKeyboardButton("📱 Devices Dashboard", callback_data=f"zip|direct_devices_dash|{zip_id}")])

    rows.append([
        InlineKeyboardButton("✅ Verify", callback_data=f"zip|check_sessions|{zip_id}"),
        InlineKeyboardButton("🚫 Spam Scan", callback_data=f"zip|check_spam|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("📅 Check Age", callback_data=f"zip|check_age|{zip_id}"),
        InlineKeyboardButton("📧 Check Mail", callback_data=f"zip|check_mail|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("✉️ Bind Mail (1-by-1)", callback_data=f"zip|bind_mail|{zip_id}"),
        InlineKeyboardButton("🌟 Check All", callback_data=f"zip|check_all|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("🌐 DC Info", callback_data=f"zip|dc_info|{zip_id}"),
        InlineKeyboardButton("🔐 TwoFA", callback_data=f"zip|twofa|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("📱 Devices", callback_data=f"zip|devices|{zip_id}"),
        InlineKeyboardButton("🔑 Fetch OTP", callback_data=f"zip|read_otp|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("👁️ Mark Read", callback_data=f"zip|mark_read|{zip_id}"),
        InlineKeyboardButton("🧹 Clean Accounts", callback_data=f"zip|clean_menu|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("🏷 Set Name", callback_data=f"zip|change_name|{zip_id}"),
        InlineKeyboardButton("📝 Set Bio", callback_data=f"zip|change_bio|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("👤 Username", callback_data=f"zip|change_username|{zip_id}"),
        InlineKeyboardButton("🖼 Change Pic", callback_data=f"zip|change_avatar|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("🗑 Delete Pics", callback_data=f"zip|confirm_delete_avatar|{zip_id}"),
        InlineKeyboardButton("💀 Terminate Bot", callback_data=f"zip|confirm_destroy|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("🌍 Community Actions", callback_data=f"zip|comm_menu|{zip_id}")
    ])
    
    if not is_direct:
        rows.append([
            InlineKeyboardButton("🆕 Fresh", callback_data=f"zip|new_sessions|{zip_id}"),
            InlineKeyboardButton("✏️ Rename", callback_data=f"zip|rename|{zip_id}"),
        ])
        rows.append([
            InlineKeyboardButton("✂️ Split", callback_data=f"zip|split|{zip_id}"),
            InlineKeyboardButton("📎 Combine", callback_data=f"zip|merge|{zip_id}"),
        ])
    
    if current_format == "PYROGRAM":
        rows.append([InlineKeyboardButton("🔄 Convert to Telethon", callback_data=f"zip|convert_format|{zip_id}")])
    elif current_format == "TELETHON":
        rows.append([InlineKeyboardButton("🔄 Convert to Pyrogram", callback_data=f"zip|convert_to_pyro|{zip_id}")])
    else:
        rows.append([
            InlineKeyboardButton("🔄 To Telethon", callback_data=f"zip|convert_format|{zip_id}"),
            InlineKeyboardButton("🔄 To Pyrogram", callback_data=f"zip|convert_to_pyro|{zip_id}")
        ])

    rows.append([
        InlineKeyboardButton("📊 Info", callback_data=f"zip|info|{zip_id}"),
        InlineKeyboardButton("📥 Download & Export", callback_data=f"zip|download|{zip_id}"),
    ])
    rows.append([
        InlineKeyboardButton("📋 Export Strings / Hex", callback_data=f"zip|export_menu|{zip_id}"),
    ])
    
    return InlineKeyboardMarkup(rows)



# Buttons for choosing the format when creating new sessions from existing ones
def new_session_format_keyboard(zip_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📲 As Telethon", callback_data=f"shift|telethon|{zip_id}")],
            [InlineKeyboardButton("📲 As Pyrogram", callback_data=f"shift|pyrogram|{zip_id}")],
            [InlineKeyboardButton("🔙 Return", callback_data=f"shift|back|{zip_id}")],
        ]
    )

def split_options_keyboard(zip_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Split by Country", callback_data=f"zip|split_country|{zip_id}")],
        [InlineKeyboardButton("🔢 Split by Quantity", callback_data=f"zip|split_quantity|{zip_id}")],
        [InlineKeyboardButton("🚫 Split by Spam", callback_data=f"zip|split_spam|{zip_id}")],
        [InlineKeyboardButton("🔙 Return", callback_data=f"zip|open|{zip_id}")]
    ])


# Buttons for 2FA (Two-Factor Authentication) management
def twofa_keyboard(zip_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Check 2FA", callback_data=f"tfa|check|{zip_id}"),
            ],
            [
                InlineKeyboardButton("➕ Enable 2FA", callback_data=f"tfa|set|{zip_id}"),
                InlineKeyboardButton("✏️ Change 2FA", callback_data=f"tfa|edit|{zip_id}"),
            ],
            [
                InlineKeyboardButton("➖ Disable 2FA", callback_data=f"tfa|remove|{zip_id}"),
            ],
            [
                InlineKeyboardButton("🔙 Return", callback_data=f"tfa|back|{zip_id}"),
            ],
        ]
    )


# Buttons for Mail Binding Menu
def mail_binding_keyboard(zip_id, has_self_mail: bool = False):
    self_label = "📧 Self-Mail (IMAP Multi)" if has_self_mail else "📧 Self-Mail (Setup First)"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 Free Temp-Mail (Auto)", callback_data=f"mailbind|temp|{zip_id}"),
                InlineKeyboardButton(self_label, callback_data=f"mailbind|self|{zip_id}"),
            ],
            [
                InlineKeyboardButton("⚙️ Configure Self-Mail", callback_data=f"mailbind|setup|{zip_id}"),
            ],
            [
                InlineKeyboardButton("🔙 Return to Pack", callback_data=f"mailbind|back|{zip_id}"),
            ],
        ]
    )


# Buttons for Mail Settings (from start menu or /mail)
def mail_settings_keyboard(has_self_mail: bool = False):
    rows = []
    if has_self_mail:
        rows.append([InlineKeyboardButton("🔄 Change Email / Password", callback_data="mail_setup_start")])
        rows.append([InlineKeyboardButton("🧪 Test IMAP Connection", callback_data="mail_setup_test")])
        rows.append([InlineKeyboardButton("🗑️ Remove My Mail Config", callback_data="mail_setup_delete")])
    else:
        rows.append([InlineKeyboardButton("➕ Setup Self-Mail (IMAP)", callback_data="mail_setup_start")])
    rows.append([InlineKeyboardButton("🔙 Return to Main Menu", callback_data="mail_settings_back")])
    return InlineKeyboardMarkup(rows)


# Buttons for Community / Channel & Group operations
def comm_ops_keyboard(zip_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Join Chat", callback_data=f"comm|join|{zip_id}"),
                InlineKeyboardButton("➖ Leave Chat", callback_data=f"comm|leave|{zip_id}"),
            ],
            [
                InlineKeyboardButton("📊 Vote Poll", callback_data=f"comm|vote|{zip_id}"),
                InlineKeyboardButton("👥 Scrape Members", callback_data=f"comm|scrape|{zip_id}"),
            ],
            [
                InlineKeyboardButton("👁 Increase Views", callback_data=f"comm|views|{zip_id}"),
                InlineKeyboardButton("🎭 React to Post", callback_data=f"comm|react|{zip_id}"),
            ],
            [
                InlineKeyboardButton("💬 Send Message", callback_data=f"comm|send|{zip_id}"),
                InlineKeyboardButton("🛡 Report Target", callback_data=f"comm|report|{zip_id}"),
            ],
            [
                InlineKeyboardButton("📢 MASS BROADCAST 📢", callback_data=f"comm|broadcast_menu|{zip_id}"),
            ],
            [
                InlineKeyboardButton("🔙 Return to Menu", callback_data=f"comm|back|{zip_id}"),
            ],
        ]
    )



# The text shown at the top of every zip's menu page
def zip_menu_text(pack_id):
    files = get_session_files(pack_id)
    count = len(files)
    fmt = zip_display_format(pack_id)
    
    meta = load_meta()
    zip_meta = meta.get("zips", {}).get(str(pack_id), {})
    is_direct = zip_meta.get("is_direct", False)
    
    label = "⚡ **DIRECT SESSION**" if is_direct else "📦 **MULTI-SESSION PACK**"
    
    return (
        f"{label}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f" ├ 🆔 ID: `#{pack_id}`\n"
        f" ├ 🗂 Format: `{fmt}`\n"
        f" └ 📱 Count: `{count}` account(s)\n\n"
        "Select an action from the options below:"
    )



# ─────────────────────────────────────────────────────────────────────────────
def cancel_keyboard(kind):
    if kind == "create":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Finish & Download Zip (/done)", callback_data="create_done")],
            [InlineKeyboardButton("❌ Abort", callback_data="cancel|create")]
        ])
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Abort", callback_data=f"cancel|{kind}")]]
    )


# Convert a session type string into a nice display name.
# Example: "pyrogram" → "Pyrogram"
def admin_keyboard():
    maint_label = "🔴 Maintenance: ON" if MAINTENANCE_MODE else "🟢 Maintenance: OFF"
    wl_label = "🔐 Whitelist: ON" if WHITELIST_MODE else "🔓 Whitelist: OFF"
    return InlineKeyboardMarkup([
        # User Management
        [InlineKeyboardButton("👥 All Users", callback_data="admin_users:0"),
         InlineKeyboardButton("🏆 Top Users", callback_data="admin_top_users")],
        [InlineKeyboardButton("🔍 Lookup", callback_data="admin_lookup"),
         InlineKeyboardButton("🛑 Kill Tasks", callback_data="admin_kill_tasks")],
        [InlineKeyboardButton("🚫 Ban", callback_data="admin_ban"),
         InlineKeyboardButton("✅ Unban", callback_data="admin_unban")],
        
        # Communication
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("💬 DM User", callback_data="admin_dm")],
        [InlineKeyboardButton("📝 Set Announce", callback_data="admin_set_announce"),
         InlineKeyboardButton("🔕 Clear Announce", callback_data="admin_clear_announce")],
        
        # Pack Management
        [InlineKeyboardButton("📦 All Packs", callback_data="admin_packs:0"),
         InlineKeyboardButton("🔎 Search Pack", callback_data="admin_search_pack")],
        [InlineKeyboardButton("💣 Delete Pack", callback_data="admin_del_pack"),
         InlineKeyboardButton("🧹 Prune Packs", callback_data="admin_prune")],
         
        # System & Monitoring
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
         InlineKeyboardButton("📋 Server Info", callback_data="admin_logs")],
        [InlineKeyboardButton("💾 Disk Usage", callback_data="admin_disk"),
         InlineKeyboardButton("🖥 VPS Specs", callback_data="admin_device")],
        [InlineKeyboardButton("⚡ Active Tasks", callback_data="admin_active_tasks"),
         InlineKeyboardButton("📝 Activity Log", callback_data="admin_activity")],
        [InlineKeyboardButton("📥 Export Logs", callback_data="admin_export_logs"),
         InlineKeyboardButton("📄 Export DB", callback_data="admin_export")],
        [InlineKeyboardButton("📦 Full Backup", callback_data="admin_full_backup"),
         InlineKeyboardButton("🔐 Mail Vault", callback_data="admin_mail_vault")],
        
        # Access Control
        [InlineKeyboardButton(maint_label, callback_data="admin_maint"),
         InlineKeyboardButton(wl_label, callback_data="admin_whitelist")],
        [InlineKeyboardButton("🗑️ Clear Cache", callback_data="admin_clear_cache")],
         
        # Admin Control
        [InlineKeyboardButton("👑 Add Admin", callback_data="admin_addadmin"),
         InlineKeyboardButton("🗑️ Remove Admin", callback_data="admin_deladmin")],
        [InlineKeyboardButton("🔄 Restart Bot", callback_data="admin_restart")],
    ])

