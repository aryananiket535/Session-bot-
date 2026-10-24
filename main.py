#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         AccStore — Session Management Platform         ║
╚═══════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import sqlite3

# Optimize file descriptor limit for 500+ concurrent user sessions
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(65536, _hard), _hard))
except Exception:
    pass

import config
import session_core
import operations
import handlers

# Re-export everything from major modules for 100% backward compatibility
from config import *
from session_core import *
from operations import *
from handlers import *

for _m in (config, session_core, operations, handlers):
    for _k, _v in vars(_m).items():
        if not _k.startswith('__'):
            globals()[_k] = _v

async def handle_health_check(_reader, writer):
    try:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def start_web_server():
    """Starts a dummy web server to satisfy KataBump's / Wispbyte's port-binding requirement."""
    port = int(os.environ.get("PORT", os.environ.get("SERVER_PORT", 8080)))
    try:
        server = await asyncio.start_server(
            handle_health_check, '0.0.0.0', port,
            reuse_address=True, reuse_port=True
        )
    except OSError:
        # Kill whatever is holding the port and retry
        os.system(f"fuser -k {port}/tcp 2>/dev/null")
        await asyncio.sleep(1)
        server = await asyncio.start_server(
            handle_health_check, '0.0.0.0', port,
            reuse_address=True, reuse_port=True
        )
    print(f"🌐 Web health server started on 0.0.0.0:{port}")
    return server

if __name__ == "__main__":
    ensure_storage()
    try:
        _meta_startup = load_meta()
        GLOBAL_ANNOUNCEMENT = _meta_startup.get("announcement", "")
        MAINTENANCE_MODE = _meta_startup.get("maintenance", False)
        WHITELIST_MODE = _meta_startup.get("whitelist", False)
        for _adm in _meta_startup.get("admins", []):
            if _adm not in ADMIN_IDS:
                ADMIN_IDS.append(_adm)
    except Exception:
        pass
    print(f"💎 {PLATFORM_NAME} is online.")  # Tell the terminal it's alive
    try:
        loop = asyncio.get_event_loop()
        import concurrent.futures
        loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix="AsyncWorker"))
        loop.run_until_complete(start_web_server())
        
        async def main_runner():
            app.loop = asyncio.get_running_loop()
            for _attempt in range(5):
                try:
                    print("🚀 Calling app.start()...")
                    await app.start()
                    print("✅ app.start() successful!")
                    break
                except (TimeoutError, ConnectionError, OSError) as e:
                    print(f"⏳ Telegram connection failed (attempt {_attempt + 1}/5): {type(e).__name__} — retrying in 5s...")
                    await asyncio.sleep(5)
            else:
                print("❌ Failed to connect to Telegram after 5 attempts.")
                return
            if ADMIN_IDS:
                for admin_id in ADMIN_IDS:
                    try:
                        print(f"✉️ Sending startup message to {admin_id}...")
                        await app.send_message(admin_id, f"🟢 **Bot Online**\n\n{PLATFORM_NAME} has been successfully started/restarted and is now active.")
                    except Exception as e:
                        print(f"⚠️ Failed to send startup message: {e}")
            
            async def connection_watchdog():
                """Heartbeat monitor: ensures bot stays responsive and auto-recovers if disconnected."""
                heartbeat_count = 0
                while True:
                    await asyncio.sleep(20)
                    heartbeat_count += 1
                    try:
                        if not getattr(app, "is_connected", False):
                            print("⚠️ [Watchdog] app.is_connected is False! Reconnecting...")
                            try:
                                await asyncio.wait_for(app.connect(), timeout=15)
                                print("✅ [Watchdog] Reconnected successfully!")
                            except Exception as e:
                                print(f"❌ [Watchdog] Reconnect attempt failed: {e}")
                        elif heartbeat_count % 3 == 0:  # Active MTProto health check every 60s
                            try:
                                await asyncio.wait_for(app.get_me(), timeout=10)
                            except Exception as ping_err:
                                print(f"⚠️ [Watchdog] Active ping warning: {ping_err}")
                    except Exception:
                        pass

            watchdog_task = asyncio.create_task(connection_watchdog())
            import pyrogram
            print("💤 Entering pyrogram.idle()...")
            try:
                await pyrogram.idle()
            finally:
                watchdog_task.cancel()
            print("🛑 Exiting pyrogram.idle()...")
            await app.stop()
            
        loop.run_until_complete(main_runner())
    except KeyboardInterrupt:
        pass
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print(f"\n❌ Error: The session database '{ROOT_DIR}/accstore_engine.session' is locked.")
            print("Another instance of the bot is already running in the background and using this file.")
            print("To fix this, please close or kill the other process. You can find it using:")
            print(f"  lsof {ROOT_DIR}/accstore_engine.session")
            print("And kill it using: kill -9 <PID>")
            import sys
            sys.exit(1)
        else:
            raise
    finally:
        # Clean up any pending asyncio tasks (like hanging Telethon client loops)
        try:
            loop = asyncio.get_event_loop()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass

