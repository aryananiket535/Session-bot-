# ==============================================================================
# AccStore Session Manager — Account & Pack Operations
# ==============================================================================

import config
import session_core
from config import *
from config import _RAW_APP_NAME
from session_core import *
for _m in (config, session_core):
    for _k, _v in vars(_m).items():
        if not _k.startswith("__"):
            globals()[_k] = _v

def creation_dir(user_id):
    return os.path.join(ROOT_DIR, "generated", str(user_id))


def next_creation_prompt(state):
    count = len(state.get("sessions", [])) + 1
    created_count = len(state.get("sessions", []))
    info = f" (Saved so far: `{created_count}`)" if created_count > 0 else ""
    return f"📱 Send phone number for account {count} or send /done to finish.{info}"



# ─────────────────────────────────────────────────────────────────────────────
# Account Scanners
# Automated spam status and
# are clean, in spam jail, or permanently frozen.
# It also checks how old accounts are using @tgdnabot.
# ─────────────────────────────────────────────────────────────────────────────


# Read SpamBot's reply and figure out the spam status:
# "spamfree" = account is clean, "tempspam" = temporarily limited,
# "spam" = permanent spam restriction, "freeze" = account blocked/frozen
def normalize_spam_status(reply_text):
    reply = (reply_text or "").strip().lower()
    # Check for frozen/blocked account explicitly declared by Telegram
    if "your account was blocked" in reply or "account is blocked" in reply:
        return "freeze"
    if "good news, no limits are currently applied to your account" in reply:
        return "spamfree"
    if "unfortunately, some phone numbers may trigger a harsh response from our anti-spam systems" in reply:
        return "tempspam"
    if "i'm very sorry" in reply or "submit a complaint" in reply or "actions can trigger" in reply:
        return "spam"
    # Never declare an account frozen just because SpamBot was slow or didn't reply
    if not reply:
        return "timeout"
    return "spam"


async def collect_spambot_status_pyrogram(client):
    if SKIP_SPAMBOT:
        return "clean", "Skipped by configuration"
    
    try:
        sent = await client.send_message("SpamBot", "/start")
    except FloodWait:
        # Rule: Proxy only used when FloodWait hit
        fw_proxy = get_floodwait_pyro_proxy()
        if fw_proxy and hasattr(client, "export_session_string"):
            try:
                s_str = await client.export_session_string()
                async with make_pyro_client(client.name, session_string=s_str, proxy=fw_proxy) as fw_client:
                    return await collect_spambot_status_pyrogram(fw_client)
            except Exception:
                pass
        return "rate_limited", ""
    except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan):
        return "inactive", ""
    except Exception as e:
        err_str = str(e).lower()
        if "flood" in err_str or "wait" in err_str:
            return "rate_limited", ""
        if "auth_key" in err_str or "deactivated" in err_str:
            return "inactive", ""
        return "timeout", ""
    
    reply_text = ""
    start_time = time.time()
    while time.time() - start_time < 5.0:
        async for msg in client.get_chat_history("SpamBot", limit=3):
            if msg.id > sent.id and msg.text:
                reply_text = msg.text
                break
        if reply_text:
            break
        await asyncio.sleep(0.25)

    try:
        await client.delete_messages("SpamBot", sent.id)
    except Exception:
        pass
    if DEAD_MESSAGE.lower() in reply_text.lower():
        return "freeze", reply_text
    return normalize_spam_status(reply_text), reply_text


async def collect_spambot_status_telethon(client):
    if SKIP_SPAMBOT:
        return "clean", "Skipped by configuration"
        
    try:
        sent = await client.send_message("SpamBot", "/start")
    except FloodWaitError:
        # Rule: Proxy only used when FloodWait hit
        fw_proxy = get_floodwait_tele_proxy()
        if fw_proxy and hasattr(client, "session"):
            fw_cl = None
            try:
                fw_cl = make_tele_client(client.session, proxy=fw_proxy)
                await asyncio.wait_for(fw_cl.connect(), timeout=CONNECT_TIMEOUT)
                sent = await fw_cl.send_message("SpamBot", "/start")
                client = fw_cl
            except Exception:
                if fw_cl:
                    await safe_telethon_disconnect(fw_cl)
                return "rate_limited", ""
        else:
            return "rate_limited", ""
    except (TeleAuthError, TeleUserDeactivatedError):
        return "inactive", ""
    except Exception as e:
        err_str = str(e).lower()
        if "flood" in err_str or "wait" in err_str:
            return "rate_limited", ""
        if "auth_key" in err_str or "deactivated" in err_str:
            return "inactive", ""
        return "timeout", ""
    
    reply_text = ""
    start_time = time.time()
    while time.time() - start_time < 5.0:
        try:
            async for msg in client.iter_messages("SpamBot", limit=3):
                if msg.id > sent.id and msg.text:
                    reply_text = msg.text
                    break
        except Exception as e:
            err_str = str(e)
            if "Constructor ID" in err_str or "TypeNotFound" in type(e).__name__:
                for fragment in ["anti-spam", "harsh response", "submit a complaint", "limits on your account"]:
                    if fragment in err_str.lower():
                        reply_text = "Your account is limited. Unfortunately, some phone numbers may trigger a harsh response from our anti-spam systems."
                        break
                if not reply_text:
                    reply_text = "Your account is limited."
                break
            break
        if reply_text:
            break
        await asyncio.sleep(0.25)

    try:
        await client.delete_messages("SpamBot", [sent.id])
    except Exception:
        pass
    if DEAD_MESSAGE.lower() in reply_text.lower():
        return "freeze", reply_text
    return normalize_spam_status(reply_text), reply_text


async def check_session_status(session_path, return_phone=False, check_spambot=False):
    st, ph = await _check_session_status_inner(session_path, check_spambot=check_spambot)
    if return_phone:
        return st, ph
    return st

async def _check_session_status_inner(session_path, check_spambot=False):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    verified_phone = None

    # Offline extraction as first candidate
    try:
        cand_p, _, _, _ = extract_session_phone_and_id(session_path)
        if cand_p and cand_p not in ("42777", "777000", "178220800"):
            verified_phone = cand_p
    except Exception:
        pass

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if me and getattr(me, "phone_number", None):
                    p = str(me.phone_number).strip().lstrip("+")
                    if p.isdigit() and p not in ("42777", "777000", "178220800"):
                        verified_phone = p
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session", verified_phone
                if check_spambot:
                    spam_status, _ = await collect_spambot_status_pyrogram(client)
                    return ("freeze" if spam_status == "freeze" else "active"), verified_phone
                return "active", verified_phone
        except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan):
            return "inactive", verified_phone
        except SessionPasswordNeeded:
            return "inactive", verified_phone
        except FloodWait as e:
            # Rule: Proxy only used when FloodWait hit
            fw_proxy = get_floodwait_pyro_proxy()
            if fw_proxy:
                try:
                    async with make_pyro_client(session_name, workdir=workdir, proxy=fw_proxy) as client:
                        me = await client.get_me()
                        if me and getattr(me, "phone_number", None):
                            p = str(me.phone_number).strip().lstrip("+")
                            if p.isdigit() and p not in ("42777", "777000", "178220800"):
                                verified_phone = p
                        if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                            return "bot_session", verified_phone
                        if check_spambot:
                            spam_status, _ = await collect_spambot_status_pyrogram(client)
                            return ("freeze" if spam_status == "freeze" else "active"), verified_phone
                        return "active", verified_phone
                except Exception:
                    pass
            return "rate_limited", verified_phone
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return "timeout", verified_phone
        except Exception as e:
            err_str = str(e).lower()
            if "auth_key" in err_str or "deactivated" in err_str or "unregistered" in err_str:
                return "inactive", verified_phone
            if "flood" in err_str or "wait" in err_str:
                return "rate_limited", verified_phone
            if "database is locked" in err_str or "already exists" in err_str:
                logging.warning(f"[VERIFY] SQLite transient on {session_name}: {e}")
                return "timeout", verified_phone
            if "constructor id" in err_str or "typenotfounderror" in type(e).__name__.lower():
                logging.warning(f"[VERIFY] TL schema mismatch on {session_name}, treating as active")
                return "active", verified_phone
            if "no workers running" in err_str or "servererror" in type(e).__name__.lower():
                logging.warning(f"[VERIFY] Telegram server error on {session_name}: {e}")
                return "timeout", verified_phone
            logging.error(f"[VERIFY] Timeout/Error on {session_name}: {type(e).__name__} - {e}")
            return "timeout", verified_phone

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive", verified_phone
            me = await client.get_me()
            if me and getattr(me, "phone", None):
                p = str(me.phone).strip().lstrip("+")
                if p.isdigit() and p not in ("42777", "777000", "178220800"):
                    verified_phone = p
            if getattr(me, "bot", False):
                return "bot_session", verified_phone
            if check_spambot:
                spam_status, _ = await collect_spambot_status_telethon(client)
                return ("freeze" if spam_status == "freeze" else "active"), verified_phone
            return "active", verified_phone
        except (TeleAuthError, TeleUserDeactivatedError):
            return "inactive", verified_phone
        except TeleSessionPasswordNeededError:
            return "inactive", verified_phone
        except FloodWaitError:
            # Rule: Proxy only used when FloodWait hit
            await safe_telethon_disconnect(client)
            client = None
            fw_proxy = get_floodwait_tele_proxy()
            if fw_proxy:
                fw_cl = None
                try:
                    session_obj = safe_sqlite_session(session_path)
                    fw_cl = make_tele_client(session_obj, proxy=fw_proxy)
                    await asyncio.wait_for(fw_cl.connect(), timeout=CONNECT_TIMEOUT)
                    if not await fw_cl.is_user_authorized():
                        return "inactive", verified_phone
                    me = await fw_cl.get_me()
                    if me and getattr(me, "phone", None):
                        p = str(me.phone).strip().lstrip("+")
                        if p.isdigit() and p not in ("42777", "777000", "178220800"):
                            verified_phone = p
                    if getattr(me, "bot", False):
                        return "bot_session", verified_phone
                    if check_spambot:
                        spam_status, _ = await collect_spambot_status_telethon(fw_cl)
                        return ("freeze" if spam_status == "freeze" else "active"), verified_phone
                    return "active", verified_phone
                except Exception:
                    pass
                finally:
                    if fw_cl:
                        await safe_telethon_disconnect(fw_cl)
            return "rate_limited", verified_phone
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return "timeout", verified_phone
        except Exception as e:
            err_str = str(e).lower()
            if "auth_key" in err_str or "deactivated" in err_str or "unregistered" in err_str:
                return "inactive", verified_phone
            if "flood" in err_str or "wait" in err_str:
                return "rate_limited", verified_phone
            if "database is locked" in err_str or "already exists" in err_str:
                logging.warning(f"[VERIFY] SQLite transient on {session_name}: {e}")
                return "timeout", verified_phone
            if "constructor id" in err_str or "typenotfounderror" in type(e).__name__.lower():
                logging.warning(f"[VERIFY] TL schema mismatch on {session_name}, treating as active")
                return "active", verified_phone
            if "no workers running" in err_str or "servererror" in type(e).__name__.lower():
                logging.warning(f"[VERIFY] Telegram server error on {session_name}: {e}")
                return "timeout", verified_phone
            logging.error(f"[VERIFY] Timeout/Error on {session_name}: {type(e).__name__} - {e}")
            return "timeout", verified_phone
        finally:
            await safe_telethon_disconnect(client)

    return "inactive", verified_phone


# Fast alive check — only verifies the session connects, skips SpamBot.
# Much faster than check_session_status, used where spam status isn't needed.
async def check_session_alive(session_path):
    return await _check_session_alive_inner(session_path)

async def _check_session_alive_inner(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    # Stagger connections to protect accounts from flood/freezing

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                await client.get_me()
                return True
        except Exception:
            return False

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return False
            await client.get_me()
            return True
        except Exception:
            return False
        finally:
            await safe_telethon_disconnect(client)

    return False



async def classify_age_once(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    # Stagger connections to protect accounts from flood/freezing

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                await client.get_me()
                spam_status, _ = await collect_spambot_status_pyrogram(client)
                if spam_status == "freeze":
                    return "freeze"
                sent = await client.send_message(AGE_TARGET_BOT, "/start")
                found_year = None
                start_time = time.time()
                while time.time() - start_time < 10:
                    async for msg in client.get_chat_history(AGE_TARGET_BOT, limit=5):
                        if msg.id > sent.id and msg.text:
                            match = AGE_REGEX.search(msg.text)
                            if match:
                                found_year = match.group(1)
                                break
                    if found_year:
                        break
                    await asyncio.sleep(1)
                return found_year or "timeout"
        except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan):
            return "inactive"
        except SessionPasswordNeeded:
            return "inactive"
        except FloodWait as e:
            return "rate_limited"
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return "timeout"
        except Exception as e:
            err_str = str(e).lower()
            if "auth_key" in err_str or "deactivated" in err_str or "unregistered" in err_str:
                return "inactive"
            if "flood" in err_str or "wait" in err_str:
                return "rate_limited"
            return "timeout"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            await client.get_me()
            spam_status, _ = await collect_spambot_status_telethon(client)
            if spam_status == "freeze":
                return "freeze"
            sent = await client.send_message(AGE_TARGET_BOT, "/start")
            found_year = None
            start_time = time.time()
            while time.time() - start_time < 15:
                async for msg in client.iter_messages(AGE_TARGET_BOT, limit=5):
                    text = msg.text or ""
                    if not text:
                        continue
                    if msg.id > sent.id:
                        match = AGE_REGEX.search(text)
                        if match:
                            found_year = match.group(1)
                            break
                if found_year:
                    break
                await asyncio.sleep(1)
            return found_year or "timeout"
        except (TeleAuthError, TeleUserDeactivatedError):
            return "inactive"
        except TeleSessionPasswordNeededError:
            return "inactive"
        except FloodWaitError:
            return "rate_limited"
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return "timeout"
        except Exception as e:
            err_str = str(e).lower()
            if "auth_key" in err_str or "deactivated" in err_str or "unregistered" in err_str:
                return "inactive"
            if "flood" in err_str or "wait" in err_str:
                return "rate_limited"
            return "timeout"
        finally:
            await safe_telethon_disconnect(client)

    return "inactive"


def is_recent_otp(msg_date, max_age_seconds=259200):
    if not msg_date:
        return True
    try:
        from datetime import timezone
        import time
        if msg_date.tzinfo is None:
            msg_timestamp = msg_date.replace(tzinfo=timezone.utc).timestamp()
        else:
            msg_timestamp = msg_date.timestamp()
        # Use abs() to handle slight clock skew where msg_timestamp might be slightly in the future
        return abs(time.time() - msg_timestamp) <= max_age_seconds
    except Exception:
        return True


async def get_account_identity(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        async with make_pyro_client(session_name, workdir=workdir) as client:
            me = await client.get_me()
            return {"name": f"{me.first_name} {me.last_name or ''}".strip(), "phone": me.phone_number}

    if session_type == "telethon":
        session_obj = safe_sqlite_session(session_path)
        client = make_tele_client(session_obj)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise AuthKeyUnregistered("Unauthorized")
            me = await client.get_me()
            return {"name": f"{me.first_name} {me.last_name or ''}".strip(), "phone": me.phone}
        finally:
            await safe_telethon_disconnect(client)

    raise AuthKeyUnregistered("Unknown session format")


async def find_latest_otp(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        async with make_pyro_client(session_name, workdir=workdir) as client:
            try:
                async for msg in client.get_chat_history(777000, limit=5):
                    if msg.text:
                        match = OTP_REGEX.search(msg.text)
                        if match and is_recent_otp(msg.date):
                            return match.group(1), msg.date
            except Exception:
                pass
            return None, None

    if session_type == "telethon":
        session_obj = safe_sqlite_session(session_path)
        client = make_tele_client(session_obj)
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            if not await client.is_user_authorized():
                return None, None
            entity = 777000
            try:
                entity = await client.get_input_entity(777000)
            except Exception:
                try:
                    entity = await client.get_input_entity('Telegram')
                except Exception:
                    pass
            try:
                await client.get_dialogs(limit=15)
            except Exception:
                pass
            try:
                async for msg in client.iter_messages(entity, limit=5):
                    if msg.text:
                        match = OTP_REGEX.search(msg.text)
                        if match and is_recent_otp(msg.date):
                            return match.group(1), msg.date
            except Exception:
                pass
            return None, None
        except asyncio.TimeoutError:
            return None, None
        finally:
            await safe_telethon_disconnect(client)

    return None


async def get_latest_777000_info(client, session_type):
    """Returns (latest_msg_id, latest_otp_code, latest_msg_timestamp) for service notifications."""
    if session_type == "pyrogram":
        try:
            async for msg in client.get_chat_history(777000, limit=3):
                code = None
                if msg.text:
                    m = OTP_REGEX.search(msg.text)
                    if m:
                        code = m.group(1)
                ts = None
                if msg.date:
                    ts = msg.date.replace(tzinfo=timezone.utc).timestamp() if msg.date.tzinfo is None else msg.date.timestamp()
                return getattr(msg, "id", None), code, ts
        except Exception:
            return None, None, None
    elif session_type == "telethon":
        try:
            async for msg in client.iter_messages(777000, limit=3):
                code = None
                if msg.text:
                    m = OTP_REGEX.search(msg.text)
                    if m:
                        code = m.group(1)
                ts = None
                if msg.date:
                    ts = msg.date.replace(tzinfo=timezone.utc).timestamp() if msg.date.tzinfo is None else msg.date.timestamp()
                return getattr(msg, "id", None), code, ts
        except Exception:
            try:
                entity = await client.get_input_entity(777000)
                async for msg in client.iter_messages(entity, limit=3):
                    code = None
                    if msg.text:
                        m = OTP_REGEX.search(msg.text)
                        if m:
                            code = m.group(1)
                    ts = None
                    if msg.date:
                        ts = msg.date.replace(tzinfo=timezone.utc).timestamp() if msg.date.tzinfo is None else msg.date.timestamp()
                    return getattr(msg, "id", None), code, ts
            except Exception:
                return None, None, None
    return None, None, None


async def fetch_latest_otp_from_client(client, session_type):
    _, code, _ = await get_latest_777000_info(client, session_type)
    return code


async def wait_for_new_otp(client, session_type, initial_msg_id=None, req_time=None, timeout=30):
    start_time = time.time()
    req_timestamp = req_time or start_time
    while time.time() - start_time < timeout:
        msg_id, code, msg_ts = await get_latest_777000_info(client, session_type)
        if code:
            is_new = False
            if initial_msg_id and msg_id and msg_id > initial_msg_id:
                is_new = True
            elif msg_ts and msg_ts >= (req_timestamp - 3):
                is_new = True
            elif not initial_msg_id and (msg_ts is None or msg_ts >= (req_timestamp - 3)):
                is_new = True

            if is_new:
                return code
        await asyncio.sleep(1.5)
    return None


async def create_new_session_from_existing(session_path, target_format, password_text, output_dir):
    session_type = detect_session_type(session_path)
    source_client = None
    new_client = None
    new_pyro_path = None
    result_path = None
    source_needs_disconnect = (session_type == "telethon")

    try:
        if session_type == "pyrogram":
            session_name = os.path.basename(session_path).replace(".session", "")
            workdir = os.path.dirname(session_path)
            source_client = make_pyro_client(session_name, workdir=workdir)
            await asyncio.wait_for(source_client.connect(), timeout=20)
            me = await asyncio.wait_for(source_client.get_me(), timeout=10)
            if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                return "bot_session", None
            phone = getattr(me, "phone_number", None) or getattr(me, "phone", None)
        elif session_type == "telethon":
            source_client = make_tele_client(safe_sqlite_session(session_path))
            await asyncio.wait_for(source_client.connect(), timeout=20)
            if not await asyncio.wait_for(source_client.is_user_authorized(), timeout=10):
                return "inactive", None
            me = await asyncio.wait_for(source_client.get_me(), timeout=10)
            if getattr(me, "bot", False):
                return "bot_session", None
            phone = getattr(me, "phone", None) or getattr(me, "phone_number", None)
        else:
            return "failed", None

        if not phone:
            return "failed", None

        phone_clean = str(phone).strip()
        if not phone_clean.startswith("+"):
            phone_clean = "+" + phone_clean

        initial_msg_id, _, _ = await get_latest_777000_info(source_client, session_type)
        target_name = sanitize_filename(f"fresh_{phone_clean.lstrip('+')}_{int(time.time() * 1000)}")
        new_client = make_pyro_client(target_name, workdir=output_dir)
        await asyncio.wait_for(new_client.connect(), timeout=20)

        req_time = time.time()
        try:
            sent = await asyncio.wait_for(new_client.send_code(phone_clean), timeout=25)
        except FloodWait as fw:
            logging.warning(f"[FreshSession] FloodWait on send_code for {phone_clean}: {fw.value}s")
            return "failed", None
        except INVALID_EXCEPTIONS:
            return "inactive", None
        except Exception as e:
            logging.warning(f"[FreshSession] send_code failed for {phone_clean}: {e}")
            return "failed", None

        otp_code = await wait_for_new_otp(source_client, session_type, initial_msg_id=initial_msg_id, req_time=req_time, timeout=30)
        if not otp_code:
            return "otp_timeout", None

        try:
            await asyncio.wait_for(new_client.sign_in(phone_clean, sent.phone_code_hash, otp_code), timeout=15)
        except SessionPasswordNeeded:
            if password_text == "/no2fa":
                return "password_required", None
            try:
                await asyncio.wait_for(new_client.check_password(password_text), timeout=15)
            except PasswordHashInvalid:
                return "wrong_password", None
        except (PhoneCodeInvalid, PhoneCodeExpired):
            return "otp_timeout", None

        new_pyro_path = os.path.join(output_dir, f"{target_name}.session")
        if target_format == "telethon":
            converted_path = convert_pyro_to_telethon_file(new_pyro_path, output_dir)
            if not converted_path:
                return "failed", None
            try:
                os.remove(new_pyro_path)
            except Exception:
                pass
            result_path = converted_path
        else:
            result_path = new_pyro_path

        return "created", result_path
    except INVALID_EXCEPTIONS:
        return "inactive", None
    except Exception as e:
        logging.warning(f"[FreshSession] Error processing session {session_path}: {e}")
        return "failed", None
    finally:
        if new_client:
            await safe_pyrogram_disconnect(new_client)
        if source_client and source_needs_disconnect:
            await safe_telethon_disconnect(source_client)
        elif source_client:
            await safe_pyrogram_disconnect(source_client)
        if not result_path and new_pyro_path:
            for suffix in ("", "-journal", "-wal", "-shm"):
                p = f"{new_pyro_path}{suffix}"
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass


META_LOCK = asyncio.Lock()


async def run_new_session_shift(source_message, zip_id, target_format, password_text):
    async with lock_pack(zip_id):
        files = get_session_files(zip_id)
        if not files:
            return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

        await safe_edit(
            source_message,
            f"🆕 🆕 Creating — Pack #{zip_id}\n\n🎯 Target Format: {target_format.title()}\n🔐 2FA Status: {'OFF' if password_text == '/no2fa' else 'ON'}",
        )
        temp_dir = tempfile.mkdtemp(prefix="acs_fresh_")
        groups = {"created": [], "inactive": [], "password_required": [], "wrong_password": [], "otp_timeout": [], "bot_session": [], "failed": []}
        total = len(files)

        progress = ThrottledProgress(
            source_message,
            f"🆕 🆕 Creating — Pack #{zip_id}\n\n🎯 Target Format: {target_format.title()}\n{{progress}}",
            total
        )
        await progress.update(0)

        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def worker(file_path):
            phone = os.path.basename(file_path).replace('.session', '')
            if phone.startswith('+'): phone = phone[1:]
            try:
                async with sem:
                    await progress.update(0, f'Processing +{phone}...')
                    try:
                        status, result_path = await asyncio.wait_for(
                            create_new_session_from_existing(file_path, target_format, password_text, temp_dir),
                            timeout=50
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        logging.warning(f"[FreshSession] Account +{phone} timed out after 50s")
                        status, result_path = "otp_timeout", None
                    except Exception as e:
                        logging.warning(f"[FreshSession] Account +{phone} failed: {e}")
                        status, result_path = "failed", None

                    if result_path:
                        groups["created"].append(result_path)
                    else:
                        groups.setdefault(status, []).append(file_path)
            finally:
                await progress.update(1, f"Processed +{phone}")

        try:
            await gather_chunked((worker(f) for f in files))

            labels = {
                "created": {"label": f"New {target_format.title()} Sessions", "icon": "✅"},
                "inactive": {"label": "Dead", "icon": "❌"},
                "password_required": {"label": "Need Password", "icon": "🔒"},
                "wrong_password": {"label": "Wrong Password", "icon": "❌"},
                "otp_timeout": {"label": "Timeout / SMS OTP", "icon": "⏳"},
                "bot_session": {"label": "Bot Session", "icon": "🤖"},
                "failed": {"label": "Failed", "icon": "❌"},
            }
            await send_classified_zips(source_message.chat.id, groups, labels)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))




async def register_zip(user_id, source_zip_path, original_name):
    zip_hash = file_sha256(source_zip_path)

    async with META_LOCK:
        meta = load_meta()
        for existing_id, record in meta["zips"].items():
            if record.get("file_hash") == zip_hash and record.get("owner_id") == user_id:
                existing_root = os.path.join(PACKS_DIR, f"zip_{existing_id}")
                sessions_root = os.path.join(existing_root, "sessions")
                if not get_session_files(int(existing_id)):
                    await extract_sessions_from_zip(source_zip_path, sessions_root)
                return int(existing_id), len(get_session_files(int(existing_id))), False

        zip_id = meta["next_zip_id"]
        meta["next_zip_id"] += 1
        
        # Pre-allocate to prevent race conditions on the ID
        meta["zips"][str(zip_id)] = {
            "owner_id": user_id,
            "original_name": original_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_hash": zip_hash,
        }
        save_meta(meta)

    zip_root = os.path.join(PACKS_DIR, f"zip_{zip_id}")
    sessions_root = os.path.join(zip_root, "sessions")
    os.makedirs(sessions_root, exist_ok=True)
    session_count = await extract_sessions_from_zip(source_zip_path, sessions_root)

    if session_count == 0:
        shutil.rmtree(zip_root, ignore_errors=True)
        async with META_LOCK:
            meta = load_meta()
            if str(zip_id) in meta["zips"]:
                del meta["zips"][str(zip_id)]
                save_meta(meta)
        return None, 0, False

    return zip_id, session_count, True


async def register_session_files(user_id, session_paths, original_name):
    """Register one or more raw .session files as a new pack (like register_zip but for loose files)."""
    async with META_LOCK:
        meta = load_meta()
        zip_id = meta["next_zip_id"]
        meta["next_zip_id"] += 1
        
        meta["zips"][str(zip_id)] = {
            "owner_id": user_id,
            "original_name": original_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_hash": None,
        }
        save_meta(meta)

    zip_root = os.path.join(PACKS_DIR, f"zip_{zip_id}")
    sessions_root = os.path.join(zip_root, "sessions")
    os.makedirs(sessions_root, exist_ok=True)

    count = 0
    for src in session_paths:
        if not os.path.isfile(src):
            continue
        base = os.path.basename(src)
        if original_name and len(session_paths) == 1 and original_name.endswith(".session"):
            base = original_name
        else:
            base = re.sub(r'^\d+_', '', base)
        dest = os.path.join(sessions_root, base)
        name_only, ext = os.path.splitext(base)
        suffix = 1
        while os.path.exists(dest):
            dest = os.path.join(sessions_root, f"{name_only}_{suffix}{ext}")
            suffix += 1
        shutil.copy2(src, dest)
        count += 1

    if count == 0:
        shutil.rmtree(zip_root, ignore_errors=True)
        return None, 0, False

    meta["zips"][str(zip_id)] = {
        "owner_id": user_id,
        "original_name": original_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_hash": "",
    }
    save_meta(meta)
    return zip_id, count, True


async def prompt_for_input(source_message, user_id, zip_id, action):
    PROMPT_STATE[user_id] = {
        "zip_id": zip_id,
        "action": action,
        "chat_id": source_message.chat.id,
        "menu_message_id": source_message.id,
        "source_message": source_message,
    }
    prompts = {
        "rename": "✏️ Enter the new label for this pack.",
        "split": "✂️ Enter split sizes as comma-separated numbers..\nExample: `3,5`",
        "protect": "🛡 Send device names to protect (comma-separated).\nSend same name again to unprotect.\nSend /clear to remove all.",
        "merge": "📎 Send zip files to merge.\nSend /done when finished.",
        "tfa_set_new": "🔐 Enter the new two-step password.",
        "tfa_edit_current": "🔐 Enter the current two-step password.",
        "tfa_edit_new": "🔐 Enter the new two-step password.",
        "tfa_remove_current": "🔐 Enter the current two-step password.",
        "change_bio": "📝 Enter the new bio for all accounts.\nMax 70 characters. Send /clear to remove bio.",
        "change_name": "🏷 Enter the new name.\nFormat: `FirstName` or `FirstName|LastName`\nExample: `John` or `John|Doe`",
        "change_username": "👤 Enter the new username prefix.\nThe bot will append 5 random letters/numbers to it to prevent clashes.\nExample: `CryptoKing`",
        "join_chat": "📣 Enter channel/group username(s) or invite link(s) to join (comma, space, or newline separated for multiple).\nExample: `@channel1, @channel2` or private link `https://t.me/+invite_link` (automatically submits join request if approval is required).",
        "leave_chat": "📣 Enter channel/group username(s), link(s), or chat ID(s) to leave (comma, space, or newline separated for multiple).\nExample: `@channel1, -100123456789`",
        "send_msg": "💬 Enter target and message text separated by `|`.\nFormat: `Target|Message`\nExample: `@username|Hello from all accounts!`",
        "report_target": "🛡 Enter target and reason separated by `|`.\nReasons: `spam`, `violence`, `pornography`, `child_abuse`, `copyright`, `fake`, `drugs`, `personal_details`, `other`\nExample: `@scammer|spam`\nExample: `@scammer|drugs`\nExample: `@scammer|personal_details`",
        "change_avatar": "🖼 Send or forward the image/photo to set as the profile picture for all accounts.",
        "bc_groups": "👥 Enter the message you want to broadcast to all groups.",
        "bc_dms": "👤 Enter the message you want to broadcast to all DMs.",
        "bc_all": "💬 Enter the message you want to broadcast to all DMs and groups.",
        "increase_views": "👁 Enter the post URL or target and message ID separated by `|` to increase post views.\nFormat: `Target|MessageID` or link `https://t.me/channel/123`\nExample: `@mychannel|45` or `https://t.me/mychannel/45`",
        "react_post": "🎭 Enter the target, message ID, and emoji reaction separated by `|`.\nFormat: `Target|MessageID|Emoji` or link `https://t.me/channel/123|Emoji`\nExample: `@mychannel|45|👍` or `https://t.me/mychannel/45|🔥`\n\n🎲 **Random Mode**: Use `random` as the emoji to randomly pick from the channel's available reactions (including custom emojis).\nExample: `@mychannel|45|random`\n\n🖤 **Custom Emoji**: Use a custom emoji document ID (number) for custom emoji packs.\nExample: `@mychannel|45|5368324170671202286`",
        "vote_poll": "📊 Enter the target, message ID, and poll option index (0 for first option) separated by `|`.\nFormat: `Target|MessageID|OptionIndex`\nExample: `@channel|123|0`",
        "scrape_members": "👥 Enter the target group username or invite link to scrape members from.\nExample: `@public_group`",
    }
    state = PROMPT_STATE[user_id]
    if action == "merge":
        merge_dir = tempfile.mkdtemp(prefix="acs_merge_")
        state["merge_dir"] = merge_dir
        state["merge_files"] = []
    await safe_edit(source_message, prompts[action], reply_markup=cancel_keyboard("prompt"))


async def start_creation_flow(chat_id, user_id, session_format):
    folder = creation_dir(user_id)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    CREATE_STATE[user_id] = {
        "format": session_format,
        "step": "phone",
        "dir": folder,
        "sessions": [],
        "current": {},
    }
    await app.send_message(
        chat_id,
        f"⚙️ {session_format.title()} session creation started.\n\n{next_creation_prompt(CREATE_STATE[user_id])}",
        reply_markup=cancel_keyboard("create"),
    )


async def send_created_zip(chat_id, user_id):
    state = CREATE_STATE.get(user_id)
    if not state:
        return await app.send_message(chat_id, "ℹ️ No session creation in progress.")
    
    # Clean up any lingering active client connection
    client = state.get("client")
    if client:
        if state.get("format") == "pyrogram":
            try:
                await client.disconnect()
            except Exception:
                pass
        else:
            await safe_telethon_disconnect(client)
        state["client"] = None

    folder = state.get("dir")
    session_paths = list(state.get("sessions", []))
    if folder and os.path.exists(folder):
        disk_files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.endswith(".session") and os.path.isfile(os.path.join(folder, f))
        ]
        for df in disk_files:
            if df not in session_paths:
                session_paths.append(df)

    if not session_paths:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        CREATE_STATE.pop(user_id, None)
        return await app.send_message(chat_id, "📭 ❌ No accounts were generated.")

    status_msg = await app.send_message(chat_id, f"📦 **Packaging `{len(session_paths)}` created session(s)...**")
    fmt_name = state.get("format", "telethon")
    file_name = f"{len(session_paths)}_{fmt_name}.zip"
    try:
        zip_path, temp_dir = await build_zip_from_sessions(session_paths, file_name)
        try:
            await app.send_document(
                chat_id,
                zip_path,
                caption=f"✅ **Session Pack Created Successfully**\n • Accounts: `{len(session_paths)}`\n • Format: `{fmt_name.title()}`"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        await app.send_message(chat_id, f"❌ Failed to package sessions: {e}")
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        CREATE_STATE.pop(user_id, None)


async def request_login_code(user_id, phone):
    state = CREATE_STATE[user_id]
    session_name = sanitize_filename(phone)
    session_path = os.path.join(state["dir"], f"{session_name}.session")

    # Clean up any old active client connection for this creation flow
    old_client = state.get("client")
    if old_client:
        if state["format"] == "pyrogram":
            try:
                await old_client.disconnect()
            except Exception:
                pass
        else:
            await safe_telethon_disconnect(old_client)
        state["client"] = None

    if state["format"] == "pyrogram":
        client = make_pyro_client(session_name, workdir=state["dir"])
        await client.connect()
        try:
            sent = await client.send_code(phone)
        except FloodWait as fw:
            # Rule: Proxy only used when FloodWait hit
            fw_proxy = get_floodwait_pyro_proxy(session_id=phone)
            if fw_proxy:
                logging.info(f"[FLOODWAIT] Hit {fw.value}s on VPS IP for +{phone}. Reconnecting via failover proxy...")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = make_pyro_client(session_name, workdir=state["dir"], proxy=fw_proxy)
                await client.connect()
                sent = await client.send_code(phone)
            else:
                raise
        phone_code_hash = sent.phone_code_hash
    else:
        client = make_tele_client(safe_sqlite_session(session_path))
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except FloodWaitError as fw:
            # Rule: Proxy only used when FloodWait hit
            fw_proxy = get_floodwait_tele_proxy(session_id=phone)
            if fw_proxy:
                logging.info(f"[FLOODWAIT] Hit {fw.seconds}s on VPS IP for +{phone}. Reconnecting via failover proxy...")
                await safe_telethon_disconnect(client)
                client = make_tele_client(safe_sqlite_session(session_path), proxy=fw_proxy)
                await client.connect()
                sent = await client.send_code_request(phone)
            else:
                raise
        phone_code_hash = sent.phone_code_hash

    state["step"] = "otp"
    state["client"] = client  # KEEP client connected and reuse
    state["current"] = {
        "phone": phone,
        "session_name": session_name,
        "session_path": session_path,
        "phone_code_hash": phone_code_hash,
    }


async def complete_login_with_otp(user_id, otp_text):
    state = CREATE_STATE[user_id]
    current = state["current"]
    client = state.get("client")
    otp_code = re.sub(r"\D", "", otp_text)

    if not client:
        raise ValueError("No active session connection. Please request code again.")

    if state["format"] == "pyrogram":
        try:
            await client.sign_in(current["phone"], current["phone_code_hash"], otp_code)
        except SessionPasswordNeeded:
            state["step"] = "password"
            # Keep client connected for the password step!
            return "password"
        except FloodWait as fw:
            # Rule: Proxy only used when FloodWait hit
            fw_proxy = get_floodwait_pyro_proxy(session_id=current["phone"])
            if fw_proxy:
                logging.info(f"[FLOODWAIT] OTP sign-in hit {fw.value}s on VPS IP. Retrying via proxy...")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = make_pyro_client(current["session_name"], workdir=state["dir"], proxy=fw_proxy)
                await client.connect()
                state["client"] = client
                await client.sign_in(current["phone"], current["phone_code_hash"], otp_code)
            else:
                raise
        except PhoneCodeInvalid:
            raise ValueError("The verification code is incorrect. Please check the code and try again.")
        except PhoneCodeExpired:
            try:
                await client.disconnect()
            except Exception:
                pass
            state["client"] = None
            state["step"] = "phone"
            raise ValueError("The verification code has expired. Please enter your phone number again to get a new code.")
        except Exception as e:
            # Allow retry on other general exceptions, keeping step = "otp" and client active
            raise ValueError(f"Could not complete login: {e}. Please try again.")
        
        # Disconnect only when successfully completed login
        try:
            await client.disconnect()
        except Exception:
            pass
        state["client"] = None
    else:
        try:
            await client.sign_in(current["phone"], otp_code, phone_code_hash=current["phone_code_hash"])
        except TeleSessionPasswordNeededError:
            state["step"] = "password"
            # Keep client connected for the password step!
            return "password"
        except FloodWaitError as fw:
            # Rule: Proxy only used when FloodWait hit
            fw_proxy = get_floodwait_tele_proxy(session_id=current["phone"])
            if fw_proxy:
                logging.info(f"[FLOODWAIT] OTP sign-in hit {fw.seconds}s on VPS IP. Retrying via proxy...")
                await safe_telethon_disconnect(client)
                client = make_tele_client(safe_sqlite_session(current["session_path"]), proxy=fw_proxy)
                await client.connect()
                state["client"] = client
                await client.sign_in(current["phone"], otp_code, phone_code_hash=current["phone_code_hash"])
            else:
                raise
        except PhoneCodeInvalidError:
            raise ValueError("The verification code is incorrect. Please check the code and try again.")
        except PhoneCodeExpiredError:
            await safe_telethon_disconnect(client)
            state["client"] = None
            state["step"] = "phone"
            raise ValueError("The verification code has expired. Please enter your phone number again to get a new code.")
        except Exception as e:
            error_msg = str(e)
            if "already used" in error_msg.lower():
                await safe_telethon_disconnect(client)
                state["client"] = None
                state["step"] = "phone"
                error_msg = "You have requested too many codes for this number recently. Please wait before trying again."
                raise ValueError(error_msg)
                
            # Allow retry on general errors
            raise ValueError(f"Could not complete login: {error_msg}. Please try again.")
        
        # Disconnect only when successfully completed login
        await safe_telethon_disconnect(client)
        state["client"] = None

    state["sessions"].append(current["session_path"])
    state["step"] = "phone"
    state["current"] = {}
    return "done"


async def complete_login_with_password(user_id, password):
    state = CREATE_STATE[user_id]
    current = state["current"]
    client = state.get("client")

    if not client:
        raise ValueError("No active session connection. Please restart the process.")

    if state["format"] == "pyrogram":
        try:
            await client.check_password(password)
        except PasswordHashInvalid:
            raise ValueError("The 2FA password entered is incorrect. Please try again.")
        except Exception as e:
            raise ValueError(f"Password check failed: {e}. Please try again.")

        # ONLY disconnect and clear client connection if SUCCESSFUL!
        try:
            await client.disconnect()
        except Exception:
            pass
        state["client"] = None
    else:
        try:
            await client.sign_in(password=password)
        except TelePasswordHashInvalidError:
            raise ValueError("The 2FA password entered is incorrect. Please try again.")
        except Exception as e:
            raise ValueError(f"Password check failed: {e}. Please try again.")

        # ONLY disconnect and clear client connection if SUCCESSFUL!
        await safe_telethon_disconnect(client)
        state["client"] = None

    state["sessions"].append(current["session_path"])
    state["step"] = "phone"
    state["current"] = {}


async def run_check_sessions(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🔍 Verifying Pack #{zip_id} Accounts\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    results = []
    sem = asyncio.Semaphore(SAFE_CONCURRENCY)

    async def worker(file_path):
        ext_phone, _, _, _ = extract_session_phone_and_id(file_path)
        base_stem = os.path.splitext(os.path.basename(file_path))[0]
        phone = ext_phone
        if not phone:
            cand = base_stem.lstrip("+")
            if cand.isdigit() and cand not in ("42777", "777000", "178220800") and len(cand) >= 7:
                phone = cand

        disp_phone = f"+{phone}" if phone else base_stem
        await progress.update(0, f"Processing {disp_phone}...")
        async with sem:
            # Anti-freeze jitter sleep: prevents Telegram from detecting automated bursts from same IP
            await asyncio.sleep(random.uniform(1.2, 2.5))
            await progress.update(0, f"Verifying {disp_phone}...")
            status, online_phone = await check_session_status(file_path, return_phone=True, check_spambot=False)
            if online_phone:
                phone = online_phone

            # Rename session file on disk if it currently has a bad/service name
            if phone and phone not in ("42777", "777000", "178220800"):
                dir_name = os.path.dirname(file_path)
                curr_stem = os.path.splitext(os.path.basename(file_path))[0]
                if curr_stem in ("42777", "777000") or curr_stem.startswith("42777_") or not curr_stem.isdigit():
                    new_file_path = os.path.join(dir_name, f"{phone}.session")
                    if new_file_path != file_path and not os.path.exists(new_file_path):
                        try:
                            os.rename(file_path, new_file_path)
                            old_json = os.path.join(dir_name, f"{curr_stem}.json")
                            new_json = os.path.join(dir_name, f"{phone}.json")
                            if os.path.exists(old_json) and not os.path.exists(new_json):
                                os.rename(old_json, new_json)
                            file_path = new_file_path
                        except Exception as e:
                            logging.warning(f"Error renaming session file {file_path} -> {new_file_path}: {e}")

            groups.setdefault(status, []).append(file_path)
            
            results.append({
                "phone": phone or base_stem,
                "status": status
            })
            final_disp = f"+{phone}" if phone else base_stem
            await progress.update(1, f"Verified {final_disp} ({status})")

    await gather_chunked((worker(f) for f in files))

    await safe_edit(call.message, f"📦 Packaging results for Pack #{zip_id}...")

    labels = {
        "active": {"label": "Alive", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "freeze": {"label": "Frozen/Blocked", "icon": "🧊"},
        "timeout": {"label": "Timeout/Error", "icon": "⏳"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
    }
    await send_classified_zips(call.message.chat.id, groups, labels)

    # Compile verified summary
    report_lines = [
        f"✅ **Account Verification Report** — Pack #{zip_id}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]
    
    status_order = ["active", "freeze", "rate_limited", "timeout", "bot_session", "inactive"]
    for st in status_order:
        items = [r for r in results if r["status"] == st]
        if not items:
            continue
        meta = labels.get(st, {"label": st, "icon": "❓"})
        report_lines.append(f"**{meta['icon']} {meta['label']}** (`{len(items)}`):")
        for item in items:
            p_val = str(item['phone']).strip()
            p_disp = f"+{p_val}" if not p_val.startswith("+") else p_val
            report_lines.append(f" • `{p_disp}`")
        report_lines.append("")
        
    report_text = "\n".join(report_lines).strip()
    
    if len(report_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_verify_")
        txt_path = os.path.join(temp_dir, f"verification_report_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"✅ **Verification Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        await app.send_message(call.message.chat.id, report_text)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_check_spam(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🔍 Auditing Pack #{zip_id} Spam Status\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    results = []
    sem = asyncio.Semaphore(SAFE_CONCURRENCY)

    def extract_spam_lift_date(reply_text):
        if not reply_text:
            return None
        import re
        # Match e.g., "lifted automatically on October 24, 2026 at 14:00 UTC"
        match = re.search(r"lifted automatically on ([A-Za-z]+ \d+, \d+ at \d+:\d+ [A-Z]+)", reply_text)
        if match:
            return match.group(1)
        match2 = re.search(r"lifted automatically on ([A-Za-z]+ \d+, \d+)", reply_text)
        if match2:
            return match2.group(1)
        return None

    async def worker(file_path):
        ext_phone, _, _, _ = extract_session_phone_and_id(file_path)
        base_stem = os.path.splitext(os.path.basename(file_path))[0]
        phone = ext_phone
        if not phone:
            cand = base_stem.lstrip("+")
            if cand.isdigit() and cand not in ("42777", "777000", "178220800") and len(cand) >= 7:
                phone = cand

        disp_phone = f"+{phone}" if phone else base_stem
        await progress.update(0, f"Processing {disp_phone}...")
        async with sem:
            # Gentle anti-freeze stagger between SpamBot queries
            await asyncio.sleep(random.uniform(2.0, 3.5))
            session_type = detect_session_type(file_path)
            session_name = os.path.basename(file_path).replace(".session", "")
            workdir = os.path.dirname(file_path)
            
            status = "timeout"
            reply_text = ""
            
            await progress.update(0, f"Spam checking {disp_phone}...")
            try:
                if session_type == "pyrogram":
                    async with make_pyro_client(session_name, workdir=workdir) as client:
                        me = await client.get_me()
                        if me and getattr(me, "phone_number", None):
                            p = str(me.phone_number).strip().lstrip("+")
                            if p.isdigit() and p not in ("42777", "777000", "178220800"):
                                phone = p
                        status, reply_text = await collect_spambot_status_pyrogram(client)
                elif session_type == "telethon":
                    session_obj = safe_sqlite_session(file_path)
                    client = make_tele_client(session_obj)
                    try:
                        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                        if await client.is_user_authorized():
                            me = await client.get_me()
                            if me and getattr(me, "phone", None):
                                p = str(me.phone).strip().lstrip("+")
                                if p.isdigit() and p not in ("42777", "777000", "178220800"):
                                    phone = p
                            status, reply_text = await collect_spambot_status_telethon(client)
                        else:
                            status = "inactive"
                    finally:
                        await safe_telethon_disconnect(client)
            except INVALID_EXCEPTIONS:
                status = "inactive"
            except (FloodWait, FloodWaitError):
                status = "rate_limited"
            except Exception as e:
                status = "timeout"
                err_str = str(e).lower()
                err_name = type(e).__name__.lower()
                if isinstance(e, (asyncio.TimeoutError, ConnectionError, OSError)):
                    logging.warning(f"[SPAM_CHECK] Timeout on {disp_phone}: {type(e).__name__}")
                elif "database is locked" in err_str or "already exists" in err_str:
                    logging.warning(f"[SPAM_CHECK] SQLite transient on {disp_phone}: {e}")
                elif "constructor id" in err_str or "typenotfounderror" in err_name:
                    status = "spam"
                    logging.warning(f"[SPAM_CHECK] TL schema mismatch on {disp_phone}, treating as spam")
                elif "no workers running" in err_str or "servererror" in err_name:
                    logging.warning(f"[SPAM_CHECK] Telegram server error on {disp_phone}: {e}")
                elif "sessionrevoked" in err_name or "session_revoked" in err_str or "unauthorized" in err_name or "authkey" in err_name or "deactivated" in err_name:
                    status = "inactive"
                    logging.warning(f"[SPAM_CHECK] Session Revoked/Unauthorized on {disp_phone}: {e}")
                else:
                    logging.warning(f"[SPAM_CHECK] Unhandled issue on {disp_phone}: {type(e).__name__} - {e}")

            # Rename session file on disk if it currently has a bad/service name
            if phone and phone not in ("42777", "777000", "178220800"):
                dir_name = os.path.dirname(file_path)
                curr_stem = os.path.splitext(os.path.basename(file_path))[0]
                if curr_stem in ("42777", "777000") or curr_stem.startswith("42777_") or not curr_stem.isdigit():
                    new_file_path = os.path.join(dir_name, f"{phone}.session")
                    if new_file_path != file_path and not os.path.exists(new_file_path):
                        try:
                            os.rename(file_path, new_file_path)
                            old_json = os.path.join(dir_name, f"{curr_stem}.json")
                            new_json = os.path.join(dir_name, f"{phone}.json")
                            if os.path.exists(old_json) and not os.path.exists(new_json):
                                os.rename(old_json, new_json)
                            file_path = new_file_path
                        except Exception as e:
                            logging.warning(f"Error renaming session file {file_path} -> {new_file_path}: {e}")

            groups.setdefault(status, []).append(file_path)
            
            lift_date = extract_spam_lift_date(reply_text)
            results.append({
                "phone": phone or base_stem,
                "status": status,
                "lift_date": lift_date,
            })
            final_disp = f"+{phone}" if phone else base_stem
            await progress.update(1, f"Spam checked {final_disp} ({status})")

    await gather_chunked((worker(f) for f in files))

    await safe_edit(call.message, f"📦 Packaging results for Pack #{zip_id}...")

    # Send partitioned zips as before
    labels = {
        "spamfree": {"label": "Clean", "icon": "✅"},
        "tempspam": {"label": "Flagged", "icon": "⚠️"},
        "spam": {"label": "Restricted", "icon": "🚫"},
        "freeze": {"label": "Frozen", "icon": "🧊"},
        "inactive": {"label": "Dead", "icon": "❌"},
        "timeout": {"label": "Timeout", "icon": "⏳"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
    }
    await send_classified_zips(call.message.chat.id, groups, labels)

    # Compile a beautiful on-screen report
    report_lines = [
        f"🛡 **Spam Status Audit Report** — Pack #{zip_id}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]
    
    status_order = ["spamfree", "tempspam", "spam", "freeze", "rate_limited", "timeout", "inactive"]
    for st in status_order:
        items = [r for r in results if r["status"] == st]
        if not items:
            continue
        meta = labels.get(st, {"label": st, "icon": "❓"})
        report_lines.append(f"**{meta['icon']} {meta['label']}** (`{len(items)}`):")
        for item in items:
            p_val = str(item['phone']).strip()
            p_disp = f"+{p_val}" if not p_val.startswith("+") else p_val
            line = f" • `{p_disp}`"
            if item["lift_date"]:
                line += f" — Lift: `{item['lift_date']}`"
            report_lines.append(line)
        report_lines.append("")
        
    report_text = "\n".join(report_lines).strip()
    
    if len(report_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_spam_")
        txt_path = os.path.join(temp_dir, f"spam_report_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"🛡 **Spam Audit Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        await app.send_message(call.message.chat.id, report_text)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_check_mail(call, zip_id):
    # Step 1: Retrieve all session file paths inside the specified pack/zip ID
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    total = len(files)
    # Step 2: Initialize a throttled progress tracker to keep the user informed in Telegram
    progress = ThrottledProgress(
        call.message,
        f"📧 Auditing Pack #{zip_id} Mail Status\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}  # Categorized sessions by their status (e.g. mail_added, no_mail)
    results = []  # Detailed results containing phone numbers and their statuses
    sem = asyncio.Semaphore(MAX_CONCURRENCY)  # Control maximum concurrent connections to prevent FloodWaits

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_type = detect_session_type(file_path)
            session_name = os.path.basename(file_path).replace(".session", "")
            workdir = os.path.dirname(file_path)
            
            status = "timeout"
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
                
            await progress.update(0, f"Checking mail for +{phone}...")
            try:
                # Step 3: Handle Pyrogram-type sessions
                if session_type == "pyrogram":
                    async with make_pyro_client(session_name, workdir=workdir) as client:
                        me = await client.get_me()
                        if me:
                            phone = me.phone_number or phone
                        # Check if session belongs to a bot; if so, skip security checks
                        if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                            status = "bot_session"
                        else:
                            # Invoke account.GetPassword to inspect recovery email settings
                            pwd = await client.invoke(functions.account.GetPassword())
                            status = "mail_added" if getattr(pwd, "login_email_pattern", None) else "no_mail"
                # Step 4: Handle Telethon-type sessions
                elif session_type == "telethon":
                    session_obj = safe_sqlite_session(file_path)
                    client = make_tele_client(session_obj)
                    try:
                        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                        if await client.is_user_authorized():
                            me = await client.get_me()
                            if me:
                                phone = getattr(me, "phone", phone)
                            if getattr(me, "bot", False):
                                status = "bot_session"
                            else:
                                # Fetch account credentials information to detect active recovery email
                                pwd = await client(GetPasswordRequest())
                                status = "mail_added" if getattr(pwd, "login_email_pattern", None) else "no_mail"
                        else:
                            status = "inactive"
                    finally:
                        await safe_telethon_disconnect(client)
            # Catch known session errors, rate limits, or timeout exceptions
            except INVALID_EXCEPTIONS:
                status = "inactive"
            except (FloodWait, FloodWaitError):
                status = "rate_limited"
            except Exception as e:
                status = "timeout"
                logging.error(f"[MAIL_CHECK] Timeout/Error on +{phone}: {type(e).__name__} - {e}")
                
            groups.setdefault(status, []).append(file_path)
            results.append({
                "phone": phone,
                "status": status,
            })
            await progress.update(1, f"Mail checked +{phone} ({status})")

    # Step 5: Execute all worker tasks in parallel with semaphore throttling
    await gather_chunked((worker(f) for f in files))

    await safe_edit(call.message, f"📦 Packaging results for Pack #{zip_id}...")

    # Step 6: Generate separated zip packages by status and upload them to the chat
    labels = {
        "mail_added": {"label": "Mail Added", "icon": "✅"},
        "no_mail": {"label": "No Mail", "icon": "⚠️"},
        "inactive": {"label": "Dead", "icon": "❌"},
        "timeout": {"label": "Timeout", "icon": "⏳"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
    }
    await send_classified_zips(call.message.chat.id, groups, labels)

    # Step 7: Format and send a descriptive text report of the audit results
    report_lines = [
        f"📧 **Mail Status Audit Report** — Pack #{zip_id}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]
    
    status_order = ["mail_added", "no_mail", "rate_limited", "timeout", "inactive"]
    for st in status_order:
        items = [r for r in results if r["status"] == st]
        if not items:
            continue
        meta = labels.get(st, {"label": st, "icon": "❓"})
        report_lines.append(f"**{meta['icon']} {meta['label']}** (`{len(items)}`):")
        for item in items:
            report_lines.append(f" • `+{item['phone']}`")
        report_lines.append("")
        
    report_text = "\n".join(report_lines).strip()
    
    # If the text report exceeds Telegram's 4096-character limit, send it as a file attachment
    if len(report_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_mail_")
        txt_path = os.path.join(temp_dir, f"mail_report_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"📧 **Mail Audit Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        await app.send_message(call.message.chat.id, report_text)

    # Return the user to the interactive operations menu
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_bind_email(call, zip_id, mode="self"):
    """
    MTProto Email Binding Orchestrator.
    
    CRITICAL SAFETY RULES:
    1. STRICTLY 1-BY-1 SEQUENTIAL: Never concurrent! This guarantees
       that incoming OTPs belong uniquely to the account currently being bound.
    2. ANTI-FREEZE DELAYS: 3.5 to 6.0 second jitter delay between accounts to
       prevent Telegram account and IP freezing.
    3. REAL-TIME AUDIT NOTIFICATIONS: Emits live per-account confirmation messages
       specifying exactly which phone got bound to which email permutation/temp address.
    """
    import mail_engine
    from telethon.tl import functions as tele_functions, types as tele_types
    from pyrogram.raw import functions as pyro_functions, types as pyro_types
    
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    user_id = call.from_user.id
    meta = load_meta()
    total = len(files)
    
    # ── Self-Mail Setup Validation ──
    perm_gen = None
    imap_client = None
    used_perms = []
    
    if mode == "self":
        mail_cfg = mail_engine.get_user_mail_config(meta, user_id)
        base_email = mail_cfg.get("email")
        app_password = mail_cfg.get("app_password")
        
        if not base_email or not app_password:
            try:
                await call.answer("⚠️ No Self-Mail configured! Please setup your email first.", show_alert=True)
            except Exception:
                pass
            return await safe_edit(
                call.message,
                "⚠️ **No Self-Mail Configured**\n\nPlease set up your email first before binding.",
                reply_markup=mail_binding_keyboard(zip_id, has_self_mail=False)
            )
        
        imap_host = mail_cfg.get("imap_host")
        imap_port = mail_cfg.get("imap_port")
        
        try:
            perm_gen = mail_engine.PermutationGenerator(base_email)
        except Exception as e:
            return await call.answer(f"❌ Email error: {e}", show_alert=True)
        
        used_perms = list(mail_engine.get_used_permutations(meta, user_id))
        batch_assigned_perms = set(used_perms)
        remaining = perm_gen.remaining_count(used_perms)
        
        if remaining < 1:
            try:
                await call.answer(
                    f"⚠️ All {perm_gen.total_permutations} permutations have already been used for {base_email}!",
                    show_alert=True
                )
            except Exception:
                pass
            return await safe_edit(
                call.message,
                f"⚠️ **Permutations Exhausted**\n\nAll {perm_gen.total_permutations} permutations for `{base_email}` have been used.\nPlease configure a new email or use Free Temp-Mail.",
                reply_markup=mail_binding_keyboard(zip_id, has_self_mail=True)
            )
        
        imap_client = mail_engine.IMAPMailClient(base_email, app_password, imap_host, imap_port)
        await safe_edit(call.message, f"🧪 Testing IMAP connection to `{imap_host}:{imap_port}`...")
        try:
            await imap_client.test_connection()
        except Exception as conn_err:
            return await safe_edit(
                call.message,
                f"❌ **IMAP Login Failed**\n\n{conn_err}\n\nPlease check your email & App Password in Mail Settings.",
                reply_markup=mail_binding_keyboard(zip_id, has_self_mail=True)
            )

    # ── Progress Tracker ──
    mode_name = "Self-Mail (IMAP)" if mode == "self" else "Free Temp-Mail (Auto)"
    progress = ThrottledProgress(
        call.message,
        f"✉️ **Binding Mail to Pack #{zip_id}**\nMode: `{mode_name}` (1-by-1 Sequential)\n\n{{progress}}",
        total
    )
    await progress.update(0)
    
    results = []
    groups = {}
    
    # ── Sequential 1-by-1 Execution Loop ──
    for idx, file_path in enumerate(files, 1):
        session_type = detect_session_type(file_path)
        session_name = os.path.basename(file_path).replace(".session", "")
        workdir = os.path.dirname(file_path)
        phone = session_name[1:] if session_name.startswith("+") else session_name
        
        target_email = None
        temp_client = None
        
        # 1. Obtain target email for this account
        if mode == "self":
            try:
                target_email = perm_gen.get_next_unused(batch_assigned_perms)
                batch_assigned_perms.add(target_email)
            except ValueError:
                results.append({
                    "phone": phone,
                    "email": "N/A",
                    "status": "exhausted",
                    "detail": "Permutations exhausted"
                })
                groups.setdefault("failed", []).append(file_path)
                break
        else:
            temp_client = mail_engine.TempMailClient()
            try:
                await progress.update(0, f"Account {idx}/{total} (+{phone}): Creating temp mailbox...")
                target_email = await temp_client.create_temp_email()
            except Exception as temp_err:
                results.append({
                    "phone": phone,
                    "email": "N/A",
                    "status": "temp_mail_error",
                    "detail": f"Temp mail creation failed: {temp_err}"
                })
                groups.setdefault("failed", []).append(file_path)
                await progress.update(1, f"❌ +{phone}: Temp mail error")
                continue
        
        await progress.update(0, f"Account {idx}/{total} (+{phone}): Connecting session...")
        
        status = "error"
        status_detail = ""
        bound_email = None
        prev_email = None
        
        async def _fetch_otp(t_email, min_ts, timeout=80, expected_length=None, min_uid=None):
            if mode == "self":
                return await imap_client.fetch_verification_code(
                    target_email_permutation=t_email,
                    timeout=timeout,
                    poll_interval=2.5,
                    min_timestamp=min_ts,
                    expected_length=expected_length,
                    min_uid=min_uid
                )
            else:
                return await temp_client.fetch_verification_code(
                    target_email=t_email,
                    timeout=timeout,
                    poll_interval=3.0,
                    expected_length=expected_length
                )
        
        try:
            if session_type == "pyrogram":
                async with make_pyro_client(session_name, workdir=workdir) as client:
                    me = await client.get_me()
                    if me and me.phone_number:
                        phone = me.phone_number
                    if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                        status = "bot_session"
                        status_detail = "Bot account (cannot bind mail)"
                    else:
                        # Check existing email pattern (for overwrite audit)
                        pwd = await client.invoke(pyro_functions.account.GetPassword())
                        prev_email = getattr(pwd, "login_email_pattern", None)
                        
                        max_email_attempts = 5 if mode == "temp" else 1
                        rejected_domains = set()

                        for email_attempt in range(1, max_email_attempts + 1):
                            if mode == "temp" and email_attempt > 1:
                                await progress.update(0, f"+{phone}: Trying alternative temp mail (attempt {email_attempt}/{max_email_attempts})...")
                                try:
                                    target_email = await temp_client.create_temp_email(exclude_domains=list(rejected_domains))
                                except Exception as temp_err:
                                    status = "temp_mail_error"
                                    status_detail = f"Failed to get alternative temp email: {temp_err}"
                                    break

                            current_domain = target_email.split("@")[-1].lower() if "@" in target_email else ""
                            action_msg = f"+{phone}: Overwriting existing mail `{prev_email}` with `{target_email}`..." if prev_email else f"+{phone}: Sending code to `{target_email}`..."
                            await progress.update(0, action_msg)
                            
                            min_uid = None
                            if mode == "self":
                                try:
                                    min_uid = await imap_client.get_max_uid()
                                except Exception as uid_err:
                                    logging.warning(f"[IMAP] Could not snapshot baseline UID: {uid_err}")

                            req_ts = time.time()
                            send_ok = False
                            retry_domain = False

                            try:
                                sent_code = await client.invoke(pyro_functions.account.SendVerifyEmailCode(
                                    purpose=pyro_types.EmailVerifyPurposeLoginChange(),
                                    email=target_email
                                ))
                                send_ok = True
                            except FloodWait as fw:
                                # Dedicated FloodWait proxy failover (proxy ONLY used when FloodWait hit!)
                                fw_proxy = get_floodwait_pyro_proxy(session_id=phone)
                                if fw_proxy:
                                    await progress.update(0, f"+{phone}: FloodWait ({fw.value}s) on VPS IP! Switching to failover proxy...")
                                    try:
                                        async with make_pyro_client(session_name, workdir=workdir, proxy=fw_proxy) as fw_client:
                                            sent_code = await fw_client.invoke(pyro_functions.account.SendVerifyEmailCode(
                                                purpose=pyro_types.EmailVerifyPurposeLoginChange(),
                                                email=target_email
                                            ))
                                            send_ok = True
                                            client = fw_client
                                            status_detail = "Bypassed FloodWait via failover proxy"
                                    except FloodWait as fw2:
                                        status = "rate_limited"
                                        status_detail = f"FloodWait: {fw2.value}s"
                                        break
                                    except Exception as fw_err:
                                        status = "rate_limited"
                                        status_detail = f"FloodWait: {fw.value}s ({fw_err})"
                                        break
                                else:
                                    status = "rate_limited"
                                    status_detail = f"FloodWait: {fw.value}s"
                                    break
                            except Exception as send_err:
                                err_str = str(send_err)
                                if "EMAIL_NOT_ALLOWED" in err_str:
                                    rejected_domains.add(current_domain)
                                    if mode == "temp" and email_attempt < max_email_attempts:
                                        await progress.update(0, f"+{phone}: Telegram rejected @{current_domain}! Trying another temp mail domain...")
                                        retry_domain = True
                                    else:
                                        status = "email_not_allowed"
                                        status_detail = f"Telegram blocked temp email domain (@{current_domain})"
                                elif "EMAIL_INVALID" in err_str:
                                    rejected_domains.add(current_domain)
                                    if mode == "temp" and email_attempt < max_email_attempts:
                                        await progress.update(0, f"+{phone}: Telegram marked @{current_domain} invalid! Trying another temp mail domain...")
                                        retry_domain = True
                                    else:
                                        status = "email_invalid"
                                        status_detail = "Telegram reports invalid email format"
                                else:
                                    if mode == "temp" and email_attempt < max_email_attempts and any(x in err_str for x in ["RPCError", "BAD_REQUEST", "400", "EMAIL"]):
                                        rejected_domains.add(current_domain)
                                        await progress.update(0, f"+{phone}: Telegram rejected @{current_domain} ({err_str[:30]})! Trying another domain...")
                                        retry_domain = True
                                    else:
                                        status = "email_rejected"
                                        status_detail = f"{type(send_err).__name__}: {send_err}"
                            
                            if retry_domain:
                                await asyncio.sleep(1.5)
                                continue

                            if not send_ok:
                                break
                            
                            # Code sent successfully, fetch OTP and verify
                            await progress.update(0, f"+{phone}: Waiting for OTP in inbox `{target_email}`...")
                            try:
                                otp_timeout = 45 if mode == "temp" else 80
                                exp_len = getattr(sent_code, "length", None)
                                code = await _fetch_otp(target_email, req_ts, timeout=otp_timeout, expected_length=exp_len, min_uid=min_uid)
                                await progress.update(0, f"+{phone}: OTP received! Verifying...")
                                verify_res = await client.invoke(pyro_functions.account.VerifyEmail(
                                    purpose=pyro_types.EmailVerifyPurposeLoginChange(),
                                    verification=pyro_types.EmailVerificationCode(code=code)
                                ))
                                status = "mail_bound"
                                status_detail = f"Bound to {target_email} (Overwrote: {prev_email})" if prev_email else f"Bound to {target_email}"
                                bound_email = target_email
                                break # SUCCESS!
                            except TimeoutError:
                                if mode == "temp" and email_attempt < max_email_attempts:
                                    rejected_domains.add(current_domain)
                                    await progress.update(0, f"+{phone}: No OTP received on @{current_domain} (timeout). Trying another domain...")
                                    await asyncio.sleep(1.5)
                                    continue
                                else:
                                    status = "timeout_otp"
                                    status_detail = f"No OTP received in inbox within {otp_timeout}s"
                                    break
                            except Exception as verify_err:
                                v_err_str = str(verify_err)
                                v_upper = v_err_str.upper()
                                v_type = type(verify_err).__name__
                                if "EMAIL_NOT_ALLOWED" in v_upper:
                                    rejected_domains.add(current_domain)
                                    if mode == "temp" and email_attempt < max_email_attempts:
                                        await progress.update(0, f"+{phone}: Telegram rejected @{current_domain} during verification! Trying another domain...")
                                        await asyncio.sleep(1.5)
                                        continue
                                    else:
                                        status = "email_not_allowed"
                                        status_detail = f"Telegram rejected temp email (@{current_domain}) during verification"
                                        break
                                elif any(x in v_upper for x in ["CODE_INVALID", "CODE INVALID", "CODEINVALID"]) or "CodeInvalid" in v_type:
                                    status = "code_invalid"
                                    status_detail = "Verification code was invalid or incorrect"
                                    break
                                elif any(x in v_upper for x in ["CODE_EXPIRED", "CODE EXPIRED", "CODEEXPIRED"]) or "CodeExpired" in v_type:
                                    status = "code_expired"
                                    status_detail = "Verification code expired"
                                    break
                                else:
                                    status = "verify_failed"
                                    status_detail = f"{type(verify_err).__name__}: {verify_err}"
                                    break

            elif session_type == "telethon":
                client = None
                for tele_attempt in range(3):
                    try:
                        session_obj = safe_sqlite_session(file_path)
                        client = make_tele_client(session_obj)
                        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                        break
                    except (sqlite3.OperationalError, Exception) as te:
                        if ("database is locked" in str(te).lower() or "busy" in str(te).lower()) and tele_attempt < 2:
                            if client:
                                await safe_telethon_disconnect(client)
                                client = None
                            await asyncio.sleep(1.0 * (tele_attempt + 1))
                            continue
                        raise
                try:
                    if not await client.is_user_authorized():
                        status = "inactive"
                        status_detail = "Unauthorized / session revoked"
                    else:
                        me = await client.get_me()
                        if me and getattr(me, "phone", None):
                            phone = me.phone
                        if getattr(me, "bot", False):
                            status = "bot_session"
                            status_detail = "Bot account (cannot bind mail)"
                        else:
                            pwd = await client(GetPasswordRequest())
                            prev_email = getattr(pwd, "login_email_pattern", None)
                            
                            max_email_attempts = 5 if mode == "temp" else 1
                            rejected_domains = set()

                            for email_attempt in range(1, max_email_attempts + 1):
                                if mode == "temp" and email_attempt > 1:
                                    await progress.update(0, f"+{phone}: Trying alternative temp mail (attempt {email_attempt}/{max_email_attempts})...")
                                    try:
                                        target_email = await temp_client.create_temp_email(exclude_domains=list(rejected_domains))
                                    except Exception as temp_err:
                                        status = "temp_mail_error"
                                        status_detail = f"Failed to get alternative temp email: {temp_err}"
                                        break

                                current_domain = target_email.split("@")[-1].lower() if "@" in target_email else ""
                                action_msg = f"+{phone}: Overwriting existing mail `{prev_email}` with `{target_email}`..." if prev_email else f"+{phone}: Sending code to `{target_email}`..."
                                await progress.update(0, action_msg)
                                
                                min_uid = None
                                if mode == "self":
                                    try:
                                        min_uid = await imap_client.get_max_uid()
                                    except Exception as uid_err:
                                        logging.warning(f"[IMAP] Could not snapshot baseline UID: {uid_err}")

                                req_ts = time.time()
                                send_ok = False
                                retry_domain = False

                                try:
                                    sent_code = await client(tele_functions.account.SendVerifyEmailCodeRequest(
                                        purpose=tele_types.EmailVerifyPurposeLoginChange(),
                                        email=target_email
                                    ))
                                    send_ok = True
                                except FloodWaitError as fw:
                                    # Dedicated FloodWait proxy failover (proxy ONLY used when FloodWait hit!)
                                    fw_proxy = get_floodwait_tele_proxy(session_id=phone)
                                    if fw_proxy:
                                        await progress.update(0, f"+{phone}: FloodWait ({fw.seconds}s) on VPS IP! Switching to failover proxy...")
                                        try:
                                            await safe_telethon_disconnect(client)
                                            client = None
                                            fw_client = make_tele_client(session_obj, proxy=fw_proxy)
                                            await asyncio.wait_for(fw_client.connect(), timeout=CONNECT_TIMEOUT)
                                            sent_code = await fw_client(tele_functions.account.SendVerifyEmailCodeRequest(
                                                purpose=tele_types.EmailVerifyPurposeLoginChange(),
                                                email=target_email
                                            ))
                                            send_ok = True
                                            client = fw_client
                                            status_detail = "Bypassed FloodWait via failover proxy"
                                        except FloodWaitError as fw2:
                                            status = "rate_limited"
                                            status_detail = f"FloodWait: {fw2.seconds}s"
                                            break
                                        except Exception as fw_err:
                                            status = "rate_limited"
                                            status_detail = f"FloodWait: {fw.seconds}s ({fw_err})"
                                            break
                                    else:
                                        status = "rate_limited"
                                        status_detail = f"FloodWait: {fw.seconds}s"
                                        break
                                except Exception as send_err:
                                    err_str = str(send_err)
                                    if "EMAIL_NOT_ALLOWED" in err_str:
                                        rejected_domains.add(current_domain)
                                        if mode == "temp" and email_attempt < max_email_attempts:
                                            await progress.update(0, f"+{phone}: Telegram rejected @{current_domain}! Trying another temp mail domain...")
                                            retry_domain = True
                                        else:
                                            status = "email_not_allowed"
                                            status_detail = f"Telegram blocked temp email domain (@{current_domain})"
                                    elif "EMAIL_INVALID" in err_str:
                                        rejected_domains.add(current_domain)
                                        if mode == "temp" and email_attempt < max_email_attempts:
                                            await progress.update(0, f"+{phone}: Telegram marked @{current_domain} invalid! Trying another temp mail domain...")
                                            retry_domain = True
                                        else:
                                            status = "email_invalid"
                                            status_detail = "Telegram reports invalid email format"
                                    else:
                                        if mode == "temp" and email_attempt < max_email_attempts and any(x in err_str for x in ["RPCError", "BAD_REQUEST", "400", "EMAIL"]):
                                            rejected_domains.add(current_domain)
                                            await progress.update(0, f"+{phone}: Telegram rejected @{current_domain} ({err_str[:30]})! Trying another domain...")
                                            retry_domain = True
                                        else:
                                            status = "email_rejected"
                                            status_detail = f"{type(send_err).__name__}: {send_err}"
                                
                                if retry_domain:
                                    await asyncio.sleep(1.5)
                                    continue

                                if not send_ok:
                                    break
                                
                                # Code sent successfully, fetch OTP and verify
                                await progress.update(0, f"+{phone}: Waiting for OTP in inbox `{target_email}`...")
                                try:
                                    otp_timeout = 45 if mode == "temp" else 80
                                    exp_len = getattr(sent_code, "length", None)
                                    code = await _fetch_otp(target_email, req_ts, timeout=otp_timeout, expected_length=exp_len, min_uid=min_uid)
                                    await progress.update(0, f"+{phone}: OTP received! Verifying...")
                                    verify_res = await client(tele_functions.account.VerifyEmailRequest(
                                        purpose=tele_types.EmailVerifyPurposeLoginChange(),
                                        verification=tele_types.EmailVerificationCode(code=code)
                                    ))
                                    status = "mail_bound"
                                    status_detail = f"Bound to {target_email} (Overwrote: {prev_email})" if prev_email else f"Bound to {target_email}"
                                    bound_email = target_email
                                    break # SUCCESS!
                                except TimeoutError:
                                    if mode == "temp" and email_attempt < max_email_attempts:
                                        rejected_domains.add(current_domain)
                                        await progress.update(0, f"+{phone}: No OTP received on @{current_domain} (timeout). Trying another domain...")
                                        await asyncio.sleep(1.5)
                                        continue
                                    else:
                                        status = "timeout_otp"
                                        status_detail = f"No OTP received in inbox within {otp_timeout}s"
                                        break
                                except Exception as verify_err:
                                    v_err_str = str(verify_err)
                                    v_upper = v_err_str.upper()
                                    v_type = type(verify_err).__name__
                                    if "EMAIL_NOT_ALLOWED" in v_upper:
                                        rejected_domains.add(current_domain)
                                        if mode == "temp" and email_attempt < max_email_attempts:
                                            await progress.update(0, f"+{phone}: Telegram rejected @{current_domain} during verification! Trying another domain...")
                                            await asyncio.sleep(1.5)
                                            continue
                                        else:
                                            status = "email_not_allowed"
                                            status_detail = f"Telegram rejected temp email (@{current_domain}) during verification"
                                            break
                                    elif any(x in v_upper for x in ["CODE_INVALID", "CODE INVALID", "CODEINVALID"]) or "CodeInvalid" in v_type:
                                        status = "code_invalid"
                                        status_detail = "Verification code was invalid or incorrect"
                                        break
                                    elif any(x in v_upper for x in ["CODE_EXPIRED", "CODE EXPIRED", "CODEEXPIRED"]) or "CodeExpired" in v_type:
                                        status = "code_expired"
                                        status_detail = "Verification code expired"
                                        break
                                    else:
                                        status = "verify_failed"
                                        status_detail = f"{type(verify_err).__name__}: {verify_err}"
                                        break
                finally:
                    await safe_telethon_disconnect(client)

        except INVALID_EXCEPTIONS:
            status = "inactive"
            status_detail = "Dead / revoked session"
        except (FloodWait, FloodWaitError) as fw:
            status = "rate_limited"
            secs = getattr(fw, "value", getattr(fw, "seconds", 0))
            status_detail = f"FloodWait: {secs}s"
        except Exception as e:
            status = "error"
            status_detail = f"{type(e).__name__}: {e}"
        
        # Temp mail cleanup if failed
        if temp_client and status != "mail_bound":
            try:
                await temp_client.cleanup()
            except Exception:
                pass
        
        # Record used permutation on success
        if status == "mail_bound" and mode == "self":
            used_perms.append(target_email)
            mail_engine.add_used_permutation(meta, user_id, target_email)
            save_meta(meta)
        
        groups.setdefault(status, []).append(file_path)
        results.append({
            "phone": phone,
            "email": bound_email or (target_email if status == "mail_bound" else "N/A"),
            "status": status,
            "detail": status_detail,
        })
        
        status_icon = "✅" if status == "mail_bound" else ("⏩" if status == "already_bound" else "❌")
        await progress.update(1, f"{status_icon} +{phone}: {status_detail}")
        
        # Real-time per-account audit message to chat
        if status == "mail_bound":
            try:
                ov_text = f"\n • Previous Mail: `{prev_email}` (Overwritten)" if prev_email else ""
                await app.send_message(
                    call.message.chat.id,
                    f"✅ **Account Bound Successfully**\n"
                    f" • Phone: `+{phone}`\n"
                    f" • Bound Email: `{target_email}`"
                    f"{ov_text}\n"
                    f" • Pack: `#{zip_id}` ({idx}/{total})"
                )
            except Exception:
                pass
        
        # Anti-freeze delay: 3.5 to 6.0 seconds jitter between accounts
        if idx < total:
            await asyncio.sleep(random.uniform(3.5, 6.0))
    
    await safe_edit(call.message, f"📦 Packaging results for Pack #{zip_id}...")
    
    # ── Generate Classified Zip Packages ──
    labels = {
        "mail_bound": {"label": "Mail Bound", "icon": "✅"},
        "already_bound": {"label": "Already Bound", "icon": "⏩"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "timeout_otp": {"label": "OTP Timeout", "icon": "⌛"},
        "email_rejected": {"label": "Mail Rejected", "icon": "🚫"},
        "verify_failed": {"label": "Verify Failed", "icon": "❌"},
        "inactive": {"label": "Dead Sessions", "icon": "💀"},
        "bot_session": {"label": "Bot Sessions", "icon": "🤖"},
        "error": {"label": "Errors", "icon": "⚠️"},
    }
    await send_classified_zips(call.message.chat.id, groups, labels)
    
    # ── Final Audit Report Text ──
    bound_list = [r for r in results if r["status"] == "mail_bound"]
    already_list = [r for r in results if r["status"] == "already_bound"]
    failed_list = [r for r in results if r["status"] not in ("mail_bound", "already_bound")]
    
    report_lines = [
        f"📧 **Mail Binding Audit Report** — Pack #{zip_id}",
        f"Mode: `{mode_name}` | Total Accounts: `{total}`",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"**Summary:**",
        f" • ✅ Successfully Bound: `{len(bound_list)}`",
        f" • ⏩ Already Had Mail: `{len(already_list)}`",
        f" • ❌ Failed / Skipped: `{len(failed_list)}`",
        "\n**📋 Account-To-Mail Mapping:**",
    ]
    
    for r in results:
        ph = f"+{r['phone']}"
        st = r["status"]
        if st == "mail_bound":
            report_lines.append(f" • `{ph}` ➔ `{r['email']}` ✅")
        elif st == "already_bound":
            report_lines.append(f" • `{ph}` ➔ `{r['detail']}` ⏩")
        else:
            report_lines.append(f" • `{ph}` ➔ Failed: `{r['detail']}` ❌")
    
    report_text = "\n".join(report_lines).strip()
    
    if len(report_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_mailbind_")
        txt_path = os.path.join(temp_dir, f"mail_bind_audit_pack_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", "").replace("`", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"📧 **Mail Binding Audit Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        await app.send_message(call.message.chat.id, report_text)
    
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_check_age(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    status_by_file = {path: "timeout" for path in files}
    pending = list(files)

    for attempt in range(1, 4):
        if not pending:
            break
        attempt_total = len(pending)
        progress = ThrottledProgress(
            call.message,
            f"🔍 Checking Pack #{zip_id} Age (Pass {attempt}/3)\n\n{{progress}}",
            attempt_total
        )
        await progress.update(0)

        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def worker(file_path):
            phone = os.path.basename(file_path).replace('.session', '')
            if phone.startswith('+'): phone = phone[1:]
            await progress.update(0, f'Processing +{phone}...')
            async with sem:
                session_name = os.path.basename(file_path).replace(".session", "")
                phone = session_name
                if phone.startswith("+"):
                    phone = phone[1:]
                await progress.update(0, f"Checking age on +{phone}...")
                status = await classify_age_once(file_path)
                status_by_file[file_path] = status
                await progress.update(1, f"Age checked +{phone} ({status})")

        await gather_chunked((worker(f) for f in pending))

        if attempt < 3:
            pending = [path for path in pending if status_by_file.get(path) in ("timeout", "rate_limited")]

    groups = {}
    for file_path in files:
        final_status = status_by_file.get(file_path, "timeout")
        groups.setdefault(final_status, []).append(file_path)

    labels = {
        "inactive": {"label": "Dead", "icon": "❌"},
        "freeze": {"label": "Frozen", "icon": "🧊"},
        "timeout": {"label": "Timeout", "icon": "⏳"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
    }
    for key in list(groups.keys()):
        if key not in labels:
            labels[key] = {"label": key, "icon": "📅"}
    await send_classified_zips(call.message.chat.id, groups, labels)
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


def _format_device_line_clean(item):
    device_name = getattr(item, "device_model", "") or "Unknown Device"
    app_name = getattr(item, "app_name", "") or ""
    app_version = getattr(item, "app_version", "") or ""
    platform = getattr(item, "platform", "") or getattr(item, "system_version", "") or ""
    ip_addr = getattr(item, "ip", "") or getattr(item, "ip_address", "") or ""
    country = getattr(item, "country", "") or getattr(item, "region", "") or ""
    
    app_display = f"{app_name} {app_version}".strip() or device_name
    platform_str = f" ({platform})" if platform else ""
    loc_str = f" [{country or ip_addr}]" if (country or ip_addr) else ""
    
    status_icon = "🟢" if getattr(item, "current", False) else "⚫"
    return f"{status_icon} {app_display}{platform_str}{loc_str}"


async def check_all_status_once(session_path):
    """
    Performs a comprehensive, atomic health check on a single session.
    Retrieves Age, Spam status, 2FA status, Recovery email, and Active device list.
    """
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    phone = session_name
    if phone.startswith("+"):
        phone = phone[1:]

    # Step 1: Define default response variables in case checks fail or time out
    age_year = "Unknown"
    spam_status = "Unknown"
    spambot_reply = "No reply"
    twofa_status = "OFF"
    mail_status = "Unknown"
    devices_info = "No devices loaded"
    
    # Step 2: Handle Pyrogram-type session check
    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                phone = getattr(me, "phone_number", phone) or phone
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return {
                        "phone": phone, "age": "Bot", "spam": "Bot", 
                        "spambot_reply": "Bot session", "twofa": "N/A", 
                        "mail": "N/A",
                        "devices": "Bot session", "status": "bot"
                    }
                
                # Check 2FA Security and Recovery Email settings
                try:
                    pwd_info = await client.invoke(functions.account.GetPassword())
                    twofa_status = "ON" if pwd_info.has_password else "OFF"
                    mail_status = "Added" if getattr(pwd_info, "login_email_pattern", None) else "Not Added"
                except Exception:
                    twofa_status = "Unknown"
                    mail_status = "Unknown"
                
                # Query Spambot for account limitations or restrictions
                try:
                    spam_status, spambot_reply = await collect_spambot_status_pyrogram(client)
                except Exception as e:
                    spam_status = f"Error ({type(e).__name__})"
                    spambot_reply = str(e)
                
                # Retrieve Account Creation Age via target bot query if account isn't frozen
                if spam_status != "freeze":
                    try:
                        sent = await client.send_message(AGE_TARGET_BOT, "/start")
                        found_year = None
                        start_time = time.time()
                        while time.time() - start_time < 10:
                            async for msg in client.get_chat_history(AGE_TARGET_BOT, limit=5):
                                if msg.id > sent.id and msg.text:
                                    match = AGE_REGEX.search(msg.text)
                                    if match:
                                        found_year = match.group(1)
                                        break
                            if found_year:
                                break
                            await asyncio.sleep(1)
                        age_year = found_year or "timeout"
                    except Exception:
                        age_year = "Error"
                else:
                    age_year = "Frozen"

                # Pull list of currently authorized active devices/sessions
                try:
                    auths = await client.invoke(functions.account.GetAuthorizations())
                    dev_lines = []
                    for item in auths.authorizations:
                        dev_lines.append(_format_device_line_clean(item))
                    devices_info = "\n".join(dev_lines) if dev_lines else "No devices"
                except Exception as e:
                    devices_info = f"Error: {e}"
                    
                return {
                    "phone": phone, "age": age_year, "spam": spam_status,
                    "spambot_reply": spambot_reply, "twofa": twofa_status,
                    "mail": mail_status,
                    "devices": devices_info, "status": "success"
                }
        except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan):
            return {"phone": phone, "status": "dead"}
        except SessionPasswordNeeded:
            return {"phone": phone, "status": "dead"}
        except FloodWait as e:
            return {"phone": phone, "status": "rate_limited", "wait_time": e.value}
        except Exception as e:
            return {"phone": phone, "status": "error", "error": str(e)}

    elif session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return {"phone": phone, "status": "dead"}
            me = await client.get_me()
            phone = getattr(me, "phone", phone) or phone
            if getattr(me, "bot", False):
                return {
                    "phone": phone, "age": "Bot", "spam": "Bot", 
                    "spambot_reply": "Bot session", "twofa": "N/A", 
                    "mail": "N/A",
                    "devices": "Bot session", "status": "bot"
                }

            # 1. 2FA Status & Mail Status
            try:
                pwd_info = await client(GetPasswordRequest())
                twofa_status = "ON" if pwd_info.has_password else "OFF"
                mail_status = "Added" if getattr(pwd_info, "login_email_pattern", None) else "Not Added"
            except Exception:
                twofa_status = "Unknown"
                mail_status = "Unknown"

            # 2. Spam Status & Reply
            try:
                spam_status, spambot_reply = await collect_spambot_status_telethon(client)
            except Exception as e:
                spam_status = f"Error ({type(e).__name__})"
                spambot_reply = str(e)

            # 3. Age
            if spam_status != "freeze":
                try:
                    sent = await client.send_message(AGE_TARGET_BOT, "/start")
                    found_year = None
                    start_time = time.time()
                    while time.time() - start_time < 15:
                        async for msg in client.iter_messages(AGE_TARGET_BOT, limit=5):
                            text = msg.text or ""
                            if not text:
                                continue
                            if msg.id > sent.id:
                                match = AGE_REGEX.search(text)
                                if match:
                                    found_year = match.group(1)
                                    break
                        if found_year:
                            break
                        await asyncio.sleep(1)
                    age_year = found_year or "timeout"
                except Exception:
                    age_year = "Error"
            else:
                age_year = "Frozen"

            # 4. Devices
            try:
                auths = await client(GetAuthorizationsRequest())
                dev_lines = []
                for item in auths.authorizations:
                    dev_lines.append(_format_device_line_clean(item))
                devices_info = "\n".join(dev_lines) if dev_lines else "No devices"
            except Exception as e:
                devices_info = f"Error: {e}"

            return {
                "phone": phone, "age": age_year, "spam": spam_status,
                "spambot_reply": spambot_reply, "twofa": twofa_status,
                "mail": mail_status,
                "devices": devices_info, "status": "success"
            }
        except (TeleAuthError, TeleUserDeactivatedError):
            return {"phone": phone, "status": "dead"}
        except TeleSessionPasswordNeededError:
            return {"phone": phone, "status": "dead"}
        except FloodWaitError as e:
            return {"phone": phone, "status": "rate_limited", "wait_time": e.seconds}
        except Exception as e:
            return {"phone": phone, "status": "error", "error": str(e)}
        finally:
            await safe_telethon_disconnect(client)

    return {"phone": phone, "status": "unsupported"}


async def run_check_all(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🔍 Auditing Pack #{zip_id} — Full Report\n\n{{progress}}",
        total
    )
    await progress.update(0)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_name = os.path.basename(file_path).replace(".session", "")
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
                
            await progress.update(0, f"Auditing +{phone}...")
            res = await check_all_status_once(file_path)
            phone = res.get('phone', phone)
            
            if res["status"] in ("success", "bot"):
                spam_lbl = "Good ✅"
                if res["spam"] == "tempspam":
                    spam_lbl = "Flagged ⚠️"
                elif res["spam"] == "spamfree":
                    spam_lbl = "Good ✅"
                elif res["spam"] == "spam":
                    spam_lbl = "Spam 🚫"
                elif res["spam"] == "freeze":
                    spam_lbl = "Frozen 🧊"
                elif res["spam"] == "Bot":
                    spam_lbl = "Bot 🤖"
                else:
                    spam_lbl = f"Unknown"
                
                dev_count = 0
                if res['devices'] not in ("No devices", "Error", "Bot session") and not res['devices'].startswith("Error:"):
                    dev_count = len([x for x in res['devices'].splitlines() if x.strip()])
                    
                report = (
                    f"🌟 **Audit Report** | `+{phone}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f" ├ 📅 **Age:** `{res['age']}`\n"
                    f" ├ 🚦 **Spam:** `{spam_lbl}`\n"
                    f" ├ 🔐 **2FA:** `{res['twofa']}`\n"
                    f" ├ 📧 **Mail:** `{res.get('mail', 'Unknown')}`\n"
                    f" └ 📱 **Devices:** `{dev_count} active`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 **SpamBot Reply:**\n_{res['spambot_reply']}_\n\n"
                    f"💻 **Device List:**\n{res['devices']}"
                )
            elif res["status"] == "dead":
                report = (
                    f"❌ **Dead / Revoked Session**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📞 **Phone:** `+{phone}`"
                )
            elif res["status"] == "rate_limited":
                report = (
                    f"⏳ **Rate Limited**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📞 **Phone:** `+{phone}`\n"
                    f"⏱ **Wait Time:** `{res.get('wait_time', 0)}s`"
                )
            else:
                report = (
                    f"⚠️ **Error Occurred**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📞 **Phone:** `+{phone}`\n"
                    f"ℹ️ **Details:** `{res.get('error', 'Unknown')}`"
                )
                
            await app.send_message(call.message.chat.id, report)
            await progress.update(1, f"Audited +{phone}")

    await gather_chunked((worker(f) for f in files))
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def clear_single_dialog(client, dialog, me_id):
    chat = dialog.chat
    chat_id = chat.id
    if is_protected_chat(chat, me_id):
        return

    try:
        peer = await client.resolve_peer(chat_id)
    except Exception:
        return

    if chat.type in [ChatType.CHANNEL, ChatType.SUPERGROUP]:
        # Channels / Supergroups: clear history then leave
        try:
            await client.invoke(
                DeleteHistory(peer=peer, max_id=0, just_clear=False, revoke=True)
            )
        except Exception:
            pass
        try:
            await client.leave_chat(chat_id)
        except Exception:
            try:
                await client.invoke(LeaveChannel(channel=peer))
            except Exception:
                pass
    elif chat.type == ChatType.GROUP:
        # Basic groups: clear history then leave via DeleteChatUser
        try:
            await client.invoke(
                DeleteHistory(peer=peer, max_id=0, just_clear=False, revoke=True)
            )
        except Exception:
            pass
        try:
            await client.leave_chat(chat_id)
        except Exception:
            try:
                # For basic groups, we need to remove ourselves
                await client.invoke(
                    functions.messages.DeleteChatUser(
                        chat_id=abs(chat_id),
                        user_id=await client.resolve_peer(me_id)
                    )
                )
            except Exception:
                pass
    else:
        # DMs / Private chats / Bots: delete history
        try:
            await client.invoke(
                DeleteHistory(
                    peer=peer,
                    max_id=0,
                    just_clear=False,
                    revoke=True,
                )
            )
        except Exception:
            pass


async def clean_account_for_session(session_path, mode="chats"):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"

                if mode in ("chats", "all"):
                    dialogs = []
                    try:
                        async for dialog in client.get_dialogs():
                            dialogs.append(dialog)
                    except Exception as e:
                        logging.warning(f"Failed to fetch dialogs for pyrogram {session_name}: {e}")

                    dialog_sem = asyncio.Semaphore(3)
                    async def _clear_one(d):
                        async with dialog_sem:
                            try:
                                await clear_single_dialog(client, d, me.id)
                            except FloodWait as fw:
                                await asyncio.sleep(min(fw.value, 30))
                                try:
                                    await clear_single_dialog(client, d, me.id)
                                except Exception:
                                    pass
                            except (PeerIdInvalid, ChannelPrivate, BadRequest):
                                pass
                            except Exception:
                                pass
                            await asyncio.sleep(random.uniform(0.3, 0.8))
                    if dialogs:
                        await asyncio.gather(*[_clear_one(d) for d in dialogs], return_exceptions=True)

                if mode in ("contacts", "all"):
                    try:
                        contacts = await client.get_contacts()
                        if contacts:
                            await client.delete_contacts([c.id for c in contacts])
                    except FloodWait as fw:
                        await asyncio.sleep(min(fw.value, 30))
                        try:
                            contacts = await client.get_contacts()
                            if contacts:
                                await client.delete_contacts([c.id for c in contacts])
                        except INVALID_EXCEPTIONS:
                            raise
                        except Exception:
                            pass
                    except INVALID_EXCEPTIONS:
                        raise
                    except Exception as e:
                        logging.error(f"Failed to clear contacts for pyrogram {session_name}: {e}")

                # Verify the account is still alive before declaring success
                await client.get_me()
                return "cleaned"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception as e:
            err_lower = str(e).lower()
            if "database is locked" in err_lower or "already exists" in err_lower:
                logging.warning(f"Clean transient for pyrogram {session_name}: {e}")
            else:
                logging.error(f"Clean failed for pyrogram {session_name}: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"

            reconnect_lock = asyncio.Lock()

            async def ensure_connected():
                async with reconnect_lock:
                    if not client.is_connected():
                        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                    if not await client.is_user_authorized():
                        raise TeleAuthError("Session lost authorization")

            if mode in ("chats", "all"):
                dialogs = []
                try:
                    async for dialog in client.iter_dialogs():
                        dialogs.append(dialog)
                except Exception as e:
                    logging.warning(f"Failed to fetch dialogs for telethon {session_name}: {e}")

                # Sequential clearing per session avoids SQLite write collisions and FloodWait
                dialog_sem = asyncio.Semaphore(1)
                async def _clear_tele(d):
                    async with dialog_sem:
                        if is_protected_chat_telethon(d, me.id):
                            return
                        entity = d.entity
                        try:
                            await ensure_connected()
                            if d.is_channel:  # channels & supergroups
                                try:
                                    await client(LeaveChannelRequest(entity))
                                except Exception:
                                    try:
                                        await client.delete_dialog(entity, revoke=True)
                                    except Exception:
                                        pass
                            elif d.is_group:  # basic groups
                                try:
                                    await client(DeleteChatUserRequest(
                                        chat_id=entity.id,
                                        user_id=me.id
                                    ))
                                except Exception:
                                    try:
                                        await client.delete_dialog(entity, revoke=True)
                                    except Exception:
                                        pass
                            else:  # DMs / private chats
                                try:
                                    await client.delete_dialog(entity, revoke=True)
                                except Exception:
                                    pass
                        except FloodWaitError as fw:
                            await asyncio.sleep(min(fw.seconds, 30))
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(0.3, 0.8))
                if dialogs:
                    await asyncio.gather(*[_clear_tele(d) for d in dialogs], return_exceptions=True)

            if mode in ("contacts", "all"):
                try:
                    await ensure_connected()
                    from telethon.tl.functions.contacts import GetContactsRequest, DeleteContactsRequest
                    contacts = await client(GetContactsRequest(hash=0))
                    if contacts and hasattr(contacts, "users") and contacts.users:
                        from telethon.tl.types import InputUser
                        to_delete = [InputUser(user_id=u.id, access_hash=u.access_hash) for u in contacts.users if getattr(u, 'access_hash', None) is not None]
                        if to_delete:
                            await ensure_connected()
                            await client(DeleteContactsRequest(id=to_delete))
                except INVALID_EXCEPTIONS:
                    raise
                except Exception as e:
                    logging.error(f"Failed to clear contacts for telethon {session_name}: {e}")

            await ensure_connected()
            return "cleaned"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception as e:
            err_lower = str(e).lower()
            if "database is locked" in err_lower or "already exists" in err_lower:
                logging.warning(f"Clean transient for telethon {session_name}: {e}")
            else:
                logging.error(f"Clean failed for telethon {session_name}: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_clean_accounts(call, zip_id, mode):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    
    mode_text = {
        "chats": "Chats",
        "contacts": "Contacts",
        "all": "Everything (Chats + Contacts)"
    }[mode]
    
    progress = ThrottledProgress(
        call.message,
        f"🧹 Cleaning {mode_text} — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {"cleaned": [], "inactive": [], "unsupported": [], "bot_session": [], "failed": []}
    groups_lock = asyncio.Lock()
    sem = asyncio.Semaphore(5)  # Low concurrency to protect accounts from bans

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_name = os.path.basename(file_path).replace(".session", "")
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
            await progress.update(0, f"Cleaning on +{phone}...")
            await asyncio.sleep(random.uniform(1.0, 2.0))  # Stagger connections
            try:
                status = await clean_account_for_session(file_path, mode)
            except Exception:
                status = "failed"
            async with groups_lock:
                groups.setdefault(status, []).append(file_path)
            await progress.update(1, f"Cleaned +{phone} ({status})")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "cleaned": {"label": "Cleaned Successfully", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "failed": {"label": "Failed", "icon": "❌"},
        "unsupported": {"label": "Unsupported", "icon": "❓"},
    }
    result_lines = []
    for status, items in groups.items():
        if items:
            meta = labels.get(status, {"label": status, "icon": "❓"})
            result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    
    await app.send_message(call.message.chat.id, f"🧹 **Mass Clean Results ({mode_text})**\n\n" + "\n".join(result_lines))
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_convert_format(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    current_format, _, counts = summarize_zip_formats(files)
    if current_format == "telethon" and counts.get("pyrogram", 0) == 0:
        return await call.answer("Pack is already 100% Telethon format.", show_alert=True)

    converted_format = "telethon"
    temp_dir = tempfile.mkdtemp(prefix="acs_conv_")
    groups = {"converted": [], "failed": []}
    total = len(files)

    progress = ThrottledProgress(
        call.message,
        f"📦 Pack #{zip_id} [{format_name(current_format).upper()}]\n\n🔄 Convert to {format_name(converted_format).upper()}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_name = os.path.basename(file_path).replace(".session", "")
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
            await progress.update(0, f"Converting +{phone}...")
            try:
                stype = detect_session_type(file_path)
                if stype == "pyrogram":
                    converted = convert_pyro_to_telethon_file(file_path, temp_dir)
                elif stype == "telethon":
                    target = os.path.join(temp_dir, os.path.basename(file_path))
                    shutil.copy2(file_path, target)
                    converted = target
                else:
                    converted = convert_pyro_to_telethon_file(file_path, temp_dir)

                if converted:
                    groups["converted"].append(converted)
                else:
                    groups["failed"].append(file_path)
            except Exception:
                groups["failed"].append(file_path)
            await progress.update(1, f"Converted +{phone}")

    try:
        await gather_chunked((worker(f) for f in files))

        detail_lines = [
            f"📦 Pack #{zip_id} 🔄 Format Details",
            "",
            f"🔹 From: `{format_name(current_format).upper()}`",
            f"🔹 To: `{format_name(converted_format).upper()}`",
            "",
            f"🔹 Pyrogram: `{counts['pyrogram']}`",
            f"🔹 Telethon: `{counts['telethon']}`",
            f"🔹 Unknown: `{counts['unknown']}`",
            "",
            f"✅ Converted: `{len(groups['converted'])}`",
            f"❌ Failed: `{len(groups['failed'])}`",
        ]
        await app.send_message(call.message.chat.id, "\n".join(detail_lines))

        labels = {
            "converted": {"label": "Converted", "icon": "🔄"},
            "failed": {"label": "Failed", "icon": "❌"},
        }
        await send_classified_zips(call.message.chat.id, groups, labels)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def send_otp_card(chat_id, zip_id, index):
    state = OTP_STATE.get((chat_id, zip_id))
    if not state:
        files = get_session_files(zip_id)
        if not files:
            return await app.send_message(chat_id, "❌ No active OTP session and the pack no longer exists.")
        OTP_STATE[(chat_id, zip_id)] = {"sessions": files}
        state = OTP_STATE[(chat_id, zip_id)]
    sessions = state["sessions"]
    if index >= len(sessions):
        OTP_STATE.pop((chat_id, zip_id), None)
        return await app.send_message(chat_id, "✅ All done!")

    session_path = sessions[index]
    try:
        identity = await get_account_identity(session_path)
    except Exception:
        return await send_otp_card(chat_id, zip_id, index + 1)

    text = (
        f"👤 **Account {index + 1} / {len(sessions)}**\n"
        f"Name: {identity['name']}\n"
        f"Phone: `{identity['phone']}`"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔑 Fetch OTP", callback_data=f"otp|{zip_id}|{index}")]]
    )
    await app.send_message(chat_id, text, reply_markup=keyboard)


async def start_otp_flow(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"✅ Verifying Pack #{zip_id} Sessions\n\n{{progress}}",
        total
    )
    await progress.update(0)

    valid_sessions = []
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                alive = await check_session_alive(file_path)
                if alive:
                    valid_sessions.append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    if not valid_sessions:
        await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        return await app.send_message(call.message.chat.id, "❌ No active accounts found.")
    OTP_STATE[(call.message.chat.id, zip_id)] = {"sessions": valid_sessions}
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
    await send_otp_card(call.message.chat.id, zip_id, 0)


async def show_destroy_confirm(call, zip_id):
    files = get_session_files(zip_id)
    count = len(files)
    text = (
        f"🚨 **CRITICAL WARNING: PERMANENT LOGOUT** 🚨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"You are about to **TERMINATE / LOG OUT** all `{count}` session(s) in **Pack #{zip_id}**.\n\n"
        f"⚠️ **THIS ACTION IS PERMANENT & CANNOT BE UNDONE:**\n"
        f" • Calls `log_out()` directly on Telegram servers for all sessions.\n"
        f" • All `{count}` account(s) will be **PERMANENTLY DE-AUTHORIZED**.\n"
        f" • All session files will be deleted from this server.\n"
        f" • You will NOT be able to log back into these accounts without fresh SMS/OTP codes!\n\n"
        f"❓ **Are you 100% sure you want to proceed?**"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❌ CANCEL (Keep Accounts Safe)", callback_data=f"zip|cancel_destroy|{zip_id}"),
            ],
            [
                InlineKeyboardButton("💀 Yes, Permanently Log Out Accounts", callback_data=f"zip|destroy|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


async def show_clean_menu(call, zip_id):
    """Show the clean accounts menu."""
    text = (
        f"🧹 **Pack #{zip_id} Clean Accounts**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select what you want to wipe from all sessions in this pack:\n"
        " • **Chats**: Clear all DMs, Groups, and Channels.\n"
        " • **Contacts**: Clear all saved contacts.\n"
        " • **Everything**: Clear all chats AND contacts."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💬 Clear Chats Only", callback_data=f"zip|clean_confirm|{zip_id}|chats"),
            ],
            [
                InlineKeyboardButton("👤 Clear Contacts Only", callback_data=f"zip|clean_confirm|{zip_id}|contacts"),
            ],
            [
                InlineKeyboardButton("🗑 Clear Everything (Chats + Contacts)", callback_data=f"zip|clean_confirm|{zip_id}|all"),
            ],
            [
                InlineKeyboardButton("🔙 Return", callback_data=f"zip|cancel_clean|{zip_id}"),
            ],
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


async def show_clean_confirm(call, zip_id, mode):
    files = get_session_files(zip_id)
    count = len(files)
    
    mode_text = {
        "chats": "all chats (DMs, Groups, Channels)",
        "contacts": "all contacts",
        "all": "all chats AND all contacts"
    }[mode]
    
    text = (
        f"⚠️ **Confirm Mass Cleaning**\n\n"
        f"Are you sure you want to completely clear **{mode_text}** from all `{count}` account(s) in **Pack #{zip_id}**?\n\n"
        f"This action cannot be undone."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧹 Yes, Clean", callback_data=f"zip|do_clean|{zip_id}|{mode}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"zip|clean_menu|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


async def destroy_zip_sessions(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        async with META_LOCK:
            meta = load_meta()
            if str(zip_id) in meta["zips"]:
                meta["zips"].pop(str(zip_id))
                save_meta(meta)
        shutil.rmtree(os.path.join(PACKS_DIR, f"zip_{zip_id}"), ignore_errors=True)
        return await call.answer("❌ No sessions found. Registry cleared.", show_alert=True)
    
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"💀 Killing Pack #{zip_id} Sessions\n\n{{progress}}",
        total
    )
    await progress.update(0)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                session_type = detect_session_type(file_path)
                try:
                    if session_type == "pyrogram":
                        session_name = os.path.basename(file_path).replace(".session", "")
                        async with make_pyro_client(session_name, workdir=os.path.dirname(file_path)) as client:
                            await client.log_out()
                    elif session_type == "telethon":
                        session_obj = safe_sqlite_session(file_path)
                        client = make_tele_client(session_obj)
                        try:
                            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                            await client.log_out()
                        finally:
                            await safe_telethon_disconnect(client)
                except Exception:
                    pass
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))
    
    shutil.rmtree(os.path.join(PACKS_DIR, f"zip_{zip_id}"), ignore_errors=True)
    async with META_LOCK:
        meta = load_meta()
        if str(zip_id) in meta["zips"]:
            meta["zips"].pop(str(zip_id))
            save_meta(meta)

    await safe_edit(call.message, f"✅ Pack #{zip_id} sessions destroyed and registry cleaned!")


async def show_devices_menu(call, zip_id):
    protected = user_protected_devices(call.from_user.id)
    suffix = f"\n\nProtected: {', '.join(f'`{item}`' for item in protected)}" if protected else ""
    await safe_edit(call.message, f"📱 Pack #{zip_id} Device Operations{suffix}", reply_markup=devices_keyboard(zip_id))


def _format_device_line(item, is_current, action, protected_upper):
    """Build a rich device info line from an authorization object."""
    device_name = item.device_model or "Unknown Device"
    app_name = getattr(item, "app_name", "") or ""
    app_version = getattr(item, "app_version", "") or ""
    platform = getattr(item, "platform", "") or getattr(item, "system_version", "") or ""
    ip_addr = getattr(item, "ip", "") or ""
    country = getattr(item, "country", "") or getattr(item, "region", "") or ""
    api_id_used = getattr(item, "api_id", "") or ""
    is_official = getattr(item, "official_app", None)
    # Get login time — Pyrogram raw returns date_created, Telethon returns date_created
    login_ts = getattr(item, "date_created", None) or getattr(item, "date_active", None) or 0
    active_ts = getattr(item, "date_active", None) or 0

    login_full = format_timestamp(login_ts) if login_ts else "Unknown"
    active_full = format_timestamp(active_ts) if active_ts else "Unknown"
    
    # Clean up app display: if we injected our custom app name into app_version,
    # hide the default API ID app_name (like "Dndjddufu") so it doesn't look messy.
    if _RAW_APP_NAME and _RAW_APP_NAME in app_version:
        app_name = ""
    app_display = f"{app_name} {app_version}".strip() if (app_name or app_version) else ""
    
    official_tag = ""
    if is_official is True:
        official_tag = " ✅ Official"
    elif is_official is False:
        official_tag = " ⚠️ Unofficial"

    if is_current:
        lines = [f"📱 **Current Device**{official_tag}"]
        lines.append(f"  ├ Device: `{device_name}`")
        if platform:
            lines.append(f"  ├ OS: `{platform}`")
        if app_display:
            lines.append(f"  ├ App: `{app_display}`")
        if api_id_used:
            lines.append(f"  ├ API ID: `{api_id_used}`")
        if ip_addr:
            lines.append(f"  ├ IP: `{ip_addr}`")
        if country:
            lines.append(f"  ├ Location: `{country}`")
        lines.append(f"  ├ Created: `{login_full}`")
        lines.append(f"  └ Last Active: `{active_full}`")
        return "\n".join(lines)

    is_protected = any(d in device_name.upper() for d in protected_upper)
    if is_protected:
        status_icon = "🛡 PROTECTED"
    elif action == "remove_success":
        status_icon = "❌ REMOVED"
    elif action == "remove_failed_24h":
        status_icon = "⚠️ BLOCKED (24h)"
    elif action == "remove_failed":
        status_icon = "⚠️ FAILED"
    elif action == "remove":
        status_icon = "❌ REMOVED"
    else:
        status_icon = "👁 FOUND"

    lines = [f"• `{device_name}` → {status_icon}{official_tag}"]
    if platform:
        lines.append(f"  ├ OS: `{platform}`")
    if app_display:
        lines.append(f"  ├ App: `{app_display}`")
    if api_id_used:
        lines.append(f"  ├ API ID: `{api_id_used}`")
    if ip_addr:
        lines.append(f"  ├ IP: `{ip_addr}`")
    if country:
        lines.append(f"  ├ Location: `{country}`")
    lines.append(f"  ├ Created: `{login_full}`")
    lines.append(f"  └ Last Active: `{active_full}`")
    return "\n".join(lines), is_protected


async def list_or_remove_devices(session_path, action, protected_devices):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    protected_upper = [item.upper() for item in protected_devices]

    clean_phone = session_name
    clean_phone = re.sub(r'^\d+_', '', clean_phone)
    clean_phone = re.sub(r'_(telethon|pyrogram).*$', '', clean_phone)
    if clean_phone.startswith('+'):
        clean_phone = clean_phone[1:]

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    raise ValueError("Bot sessions don't have devices — this is a bot account, not a user account.")
                auths = await client.invoke(functions.account.GetAuthorizations())
                total_devices = len(auths.authorizations) - 1  # exclude current
                report = [f"👤 **{me.first_name} (+{me.phone_number or clean_phone})** • `{total_devices}` other device(s)"]
                removed_count = 0
                failed_24h = False
                # Current device first
                for item in auths.authorizations:
                    if item.current:
                        report.append(_format_device_line(item, True, action, protected_upper))
                        break
                report.append("────────────────")
                for item in auths.authorizations:
                    if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                        continue
                    device_name = getattr(item, "device_model", "") or "Unknown Device"
                    is_protected = any(d in device_name.upper() for d in protected_upper)
                    
                    status_action = action
                    if action == "remove" and not is_protected:
                        try:
                            await client.invoke(functions.account.ResetAuthorization(hash=item.hash))
                            removed_count += 1
                            status_action = "remove_success"
                        except Exception as e:
                            if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in str(e).upper() or "FreshResetAuthorisationForbidden" in type(e).__name__:
                                status_action = "remove_failed_24h"
                                failed_24h = True
                            else:
                                status_action = "remove_failed"
                                
                    result = _format_device_line(item, False, status_action, protected_upper)
                    if isinstance(result, tuple):
                        line_text, _ = result
                    else:
                        line_text, _ = result, False
                    report.append(line_text)
                if action == "remove":
                    report.append(f"\n❌ Removed: {removed_count} / {total_devices} other devices.")
                    if failed_24h:
                        report.append("💡 **Note:** Telegram prevents terminating other sessions if the current session was created less than 24 hours ago. Please try again tomorrow.")
                return "\n".join(report)
        except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan):
            return f"👤 **+{clean_phone}**\n🔴 **Inactive / Revoked Session** (Session terminated or logged out on Telegram)"
        except SessionPasswordNeeded:
            return f"👤 **+{clean_phone}**\n🔐 **2FA Password Needed**"

    if session_type == "telethon":
        session_obj = safe_sqlite_session(session_path)
        client = make_tele_client(session_obj)
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return f"👤 **+{clean_phone}**\n🔴 **Inactive / Revoked Session** (Session terminated or logged out on Telegram)"
            me = await client.get_me()
            if getattr(me, "bot", False):
                raise ValueError("Bot sessions don't have devices — this is a bot account, not a user account.")
            auths = await client(GetAuthorizationsRequest())
            total_devices = len(auths.authorizations) - 1
            report = [f"👤 **{me.first_name} (+{me.phone or clean_phone})** • `{total_devices}` other device(s)"]
            removed_count = 0
            failed_24h = False
            for item in auths.authorizations:
                if item.current:
                    report.append(_format_device_line(item, True, action, protected_upper))
                    break
            report.append("────────────────")
            for item in auths.authorizations:
                if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                    continue
                device_name = getattr(item, "device_model", "") or "Unknown Device"
                is_protected = any(d in device_name.upper() for d in protected_upper)
                
                status_action = action
                if action == "remove" and not is_protected:
                    try:
                        await client(ResetAuthorizationRequest(hash=item.hash))
                        removed_count += 1
                        status_action = "remove_success"
                    except Exception as e:
                        if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in str(e).upper() or "FreshResetAuthorisationForbidden" in type(e).__name__:
                            status_action = "remove_failed_24h"
                            failed_24h = True
                        else:
                            status_action = "remove_failed"

                result = _format_device_line(item, False, status_action, protected_upper)
                if isinstance(result, tuple):
                    line_text, _ = result
                else:
                    line_text, _ = result, False
                report.append(line_text)
            if action == "remove":
                report.append(f"\n❌ Removed: {removed_count} / {total_devices} other devices.")
                if failed_24h:
                    report.append("💡 **Note:** Telegram prevents terminating other sessions if the current session was created less than 24 hours ago. Please try again tomorrow.")
            return "\n".join(report)
        except (TeleAuthError, TeleUserDeactivatedError):
            return f"👤 **+{clean_phone}**\n🔴 **Inactive / Revoked Session** (Session terminated or logged out on Telegram)"
        except TeleSessionPasswordNeededError:
            return f"👤 **+{clean_phone}**\n🔐 **2FA Password Needed**"
        finally:
            await safe_telethon_disconnect(client)

    return f"👤 **+{clean_phone}**\n⚠️ **Unsupported Session Format**"


async def run_sort_devices(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
        
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"📱 Sorting Pack #{zip_id} by Devices\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def check_devices_only(session_path):
        session_type = detect_session_type(session_path)
        session_name = os.path.basename(session_path).replace(".session", "")
        workdir = os.path.dirname(session_path)
        phone = session_name
        if phone.startswith("+"):
            phone = phone[1:]

        if session_type == "pyrogram":
            try:
                async with make_pyro_client(session_name, workdir=workdir) as client:
                    me = await client.get_me()
                    phone = getattr(me, "phone_number", phone) or phone
                    if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                        return {"phone": phone, "devices": "Bot session", "status": "bot"}

                    auths = await client.invoke(functions.account.GetAuthorizations())
                    dev_lines = [_format_device_line_clean(item) for item in auths.authorizations]
                    devices_info = "\n".join(dev_lines) if dev_lines else "No devices"
                    return {"phone": phone, "devices": devices_info, "status": "success"}
            except (AuthKeyUnregistered, AuthKeyDuplicated, UserDeactivated, UserDeactivatedBan, SessionPasswordNeeded):
                return {"phone": phone, "status": "dead"}
            except FloodWait as e:
                return {"phone": phone, "status": "rate_limited", "wait_time": e.value}
            except Exception as e:
                return {"phone": phone, "status": "error", "error": str(e)}

        elif session_type == "telethon":
            client = None
            try:
                session_obj = safe_sqlite_session(session_path)
                client = make_tele_client(session_obj)
                await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                if not await client.is_user_authorized():
                    return {"phone": phone, "status": "dead"}
                me = await client.get_me()
                phone = getattr(me, "phone", phone) or phone
                if getattr(me, "bot", False):
                    return {"phone": phone, "devices": "Bot session", "status": "bot"}

                auths = await client(GetAuthorizationsRequest())
                dev_lines = [_format_device_line_clean(item) for item in auths.authorizations]
                devices_info = "\n".join(dev_lines) if dev_lines else "No devices"
                return {"phone": phone, "devices": devices_info, "status": "success"}
            except (TeleAuthError, TeleUserDeactivatedError, TeleSessionPasswordNeededError):
                return {"phone": phone, "status": "dead"}
            except FloodWaitError as e:
                return {"phone": phone, "status": "rate_limited", "wait_time": e.seconds}
            except Exception as e:
                return {"phone": phone, "status": "error", "error": str(e)}
            finally:
                await safe_telethon_disconnect(client)

        return {"phone": phone, "status": "unsupported"}

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_name = os.path.basename(file_path).replace(".session", "")
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
                
            await progress.update(0, f"Checking +{phone}...")
            res = await check_devices_only(file_path)
            phone = res.get('phone', phone)
            
            dev_count = 0
            if res.get("status") in ("success", "bot"):
                if res['devices'] not in ("No devices", "Error", "Bot session") and not res['devices'].startswith("Error:"):
                    dev_count = len([x for x in res['devices'].splitlines() if x.strip()])
                else:
                    dev_count = 1
            else:
                dev_count = -1

            if dev_count not in groups:
                groups[dev_count] = []
            groups[dev_count].append(file_path)

            await progress.update(1, f"Checked +{phone}")

    await gather_chunked((worker(f) for f in files))

    await safe_edit(call.message, f"📦 Packaging device split results for Pack #{zip_id}...")

    report_lines = [
        f"📱 **Devices Split Report** — Pack #{zip_id}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]
    
    for dev_count, gfiles in sorted(groups.items()):
        if not gfiles: continue
        
        if dev_count == -1:
            label = "Dead / Error"
            icon = "❌"
        elif dev_count == 1:
            label = "1 Device"
            icon = "📱"
        else:
            label = f"{dev_count} Devices"
            icon = "📱"
            
        report_lines.append(f"**{icon} {label}** (`{len(gfiles)}`):")
        for f in gfiles:
            phone = os.path.basename(f).replace(".session", "")
            if phone.startswith("+"):
                phone = phone[1:]
            report_lines.append(f" • `+{phone}`")
        report_lines.append("")
        
    report_text = "\n".join(report_lines).strip()
    if len(report_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_dev_split_")
        txt_path = os.path.join(temp_dir, f"device_split_report_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"📱 **Devices Split Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        await app.send_message(call.message.chat.id, report_text)
    
    try:
        meta = load_meta()
        pack_name = meta["zips"].get(str(zip_id), {}).get("name", f"Pack {zip_id}")
        
        created_zips = []
        for dev_count, gfiles in sorted(groups.items()):
            if gfiles:
                if dev_count == -1:
                    gname = "dead_or_error"
                elif dev_count == 1:
                    gname = "1_device"
                else:
                    gname = f"{dev_count}_devices"
                    
                new_id = meta["next_zip_id"]
                meta["next_zip_id"] += 1
                new_zip_name = f"{pack_name}_{gname}"
                new_sessions_dir = os.path.join(PACKS_DIR, f"zip_{new_id}", "sessions")
                os.makedirs(new_sessions_dir, exist_ok=True)
                for src in gfiles:
                    shutil.copy2(src, os.path.join(new_sessions_dir, os.path.basename(src)))
                meta["zips"][str(new_id)] = {
                    "name": new_zip_name,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "owner_id": call.from_user.id,
                }
                created_zips.append((new_id, new_zip_name, len(gfiles), dev_count))
                
        save_meta(meta)
        
        if created_zips:
            lines = []
            for nid, name, n, count in created_zips:
                label = "Dead/Error" if count == -1 else f"{count} Device(s)"
                lines.append(f"✅ `{n}` sessions ({label}): **{name}** (Pack #{nid})")
            report = "📱 **Device Split Complete!**\n\n" + "\n".join(lines)
            if len(report) > 4000:
                report = report[:4000] + "\n... (truncated)"
            await app.send_message(call.message.chat.id, report)
            
            for dev_count, gfiles in sorted(groups.items()):
                if gfiles:
                    if dev_count == -1:
                        gname = "dead_or_error"
                    elif dev_count == 1:
                        gname = "1_device"
                    else:
                        gname = f"{dev_count}_devices"
                    zip_path, temp_dir = await build_zip_from_sessions(gfiles, f"{pack_name}_{gname}.zip")
                    try:
                        await app.send_document(call.message.chat.id, zip_path)
                    except Exception:
                        pass
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await app.send_message(call.message.chat.id, "❌ No valid sessions to split.")
    except Exception as e:
        await app.send_message(call.message.chat.id, f"❌ Device split failed: {e}")
        
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def process_devices(call, zip_id, action):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    protected = user_protected_devices(call.from_user.id)
    
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"📱 Processing Pack #{zip_id} Devices\n\n{{progress}}",
        total
    )
    await progress.update(0)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    results = []

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        clean_disp = phone
        clean_disp = re.sub(r'^\d+_', '', clean_disp)
        clean_disp = re.sub(r'_(telethon|pyrogram).*$', '', clean_disp)
        if clean_disp.startswith('+'):
            clean_disp = clean_disp[1:]

        await progress.update(0, f'Processing +{clean_disp}...')
        try:
            async with sem:
                res = await list_or_remove_devices(file_path, action, protected)
                results.append(res)
        except Exception as e:
            err_msg = str(e)
            if "401" in err_msg or "AUTH_KEY_UNREGISTERED" in err_msg or "AuthKeyUnregistered" in type(e).__name__ or "Unauthorized" in err_msg:
                results.append(f"👤 **+{clean_disp}**\n🔴 **Inactive / Revoked Session** (Session terminated or logged out on Telegram)")
            elif "UserDeactivated" in type(e).__name__ or "deactivated" in err_msg.lower():
                results.append(f"👤 **+{clean_disp}**\n🔴 **Account Banned / Deactivated** by Telegram")
            elif "FloodWait" in type(e).__name__ or "flood" in err_msg.lower():
                results.append(f"👤 **+{clean_disp}**\n⏳ **Rate Limited (FloodWait)**: Please try again later.")
            else:
                results.append(f"👤 **+{clean_disp}**\n❌ Error: {err_msg}")
        finally:
            await progress.update(1, f"Processed +{clean_disp}")

    await gather_chunked((worker(f) for f in files))
    
    report_text = "\n\n".join(results)
    
    if len(report_text) > 4000:
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix="acs_devices_")
        txt_path = os.path.join(temp_dir, f"devices_report_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text.replace("**", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"📱 **Devices Report** — Pack #{zip_id}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        try:
            await app.send_message(call.message.chat.id, report_text)
        except Exception:
            pass
            
    await show_devices_menu(call, zip_id)


async def show_comm_ops_menu(call, zip_id):
    """Show the community operations menu."""
    text = (
        f"📢 **Pack #{zip_id} Community Hub**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Execute batch operations on all active accounts:\n"
        " • **➕ Join**: Join a public group/channel or private link.\n"
        " • **➖ Leave**: Exit any group, channel, or supergroup.\n"
        " • **💬 Send**: Broadcast a message to a username/ID.\n"
        " • **🛡 Report**: Mass-report a target for custom reasons.\n"
        " • **📢 Broadcast**: Broadcast a message to all groups, DMs, or both.\n"
        " • **👁 Views**: Mass-increase views of a channel post.\n"
        " • **🎭 React**: Mass-react to a channel post with emojis (supports premium & random)."
    )
    await safe_edit(call.message, text, reply_markup=comm_ops_keyboard(zip_id))


async def show_broadcast_menu(call, zip_id):
    """Show the broadcast options menu."""
    text = (
        f"📢 **Pack #{zip_id} Broadcast Menu**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose where you want to broadcast your message:\n"
        " • **Groups**: Send message to all joined groups and supergroups.\n"
        " • **DMs**: Send message to all direct messages / private chats.\n"
        " • **DMs & Groups**: Send message to all DMs, groups, and supergroups."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👥 Send in All Groups", callback_data=f"comm|bc_groups|{zip_id}"),
            ],
            [
                InlineKeyboardButton("👤 Send in All DMs", callback_data=f"comm|bc_dms|{zip_id}"),
            ],
            [
                InlineKeyboardButton("💬 Send in All DMs and Groups", callback_data=f"comm|bc_all|{zip_id}"),
            ],
            [
                InlineKeyboardButton("🔙 Return", callback_data=f"zip|comm_menu|{zip_id}"),
            ],
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


def simplify_error(e: Exception) -> str:
    err_str = str(e).strip()
    import re
    match = re.search(r'\[\d+\s+([A-Z_]+)\](?:\s*-\s*(.*))?', err_str)
    if match:
        desc = match.group(2)
        if desc:
            return desc.split('(caused by')[0].strip()
        return match.group(1).replace('_', ' ').capitalize()
    if "(caused by" in err_str:
        err_str = err_str.split("(caused by")[0].strip()
    if not err_str or len(err_str) > 60:
        return type(e).__name__
    return err_str

async def join_chat_for_session(session_path, target):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                await client.join_chat(target)
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except PyroPeerFlood:
            return "peer_flood"
        except InviteRequestSent:
            return "request_sent"
        except Exception as e:
            error_str = str(e).upper()
            if "USER_ALREADY_PARTICIPANT" in error_str or "ALREADY A PARTICIPANT" in error_str or "UserAlreadyParticipant" in type(e).__name__:
                return "success"
            if "USERNAME_NOT_OCCUPIED" in error_str or "USERNAME_INVALID" in error_str:
                return "failed: Invalid username"
            logging.error(f"Join chat failed for pyrogram: {e}")
            return f"failed: {simplify_error(e)}"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            target_str = target.strip()
            invite_match = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w\-]+)', target_str)
            if invite_match:
                invite_hash = invite_match.group(1)
                await client(ImportChatInviteRequest(hash=invite_hash))
            else:
                target_str = re.sub(r'^https?://(?:w{3}\.)?(?:t\.me|telegram\.me)/', '', target_str).strip('@')
                entity = await client.get_entity(target_str)
                if not isinstance(entity, Channel):
                    return "failed"
                await client(JoinChannelRequest(channel=entity))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except TelePeerFloodError:
            return "peer_flood"
        except InviteRequestSentError:
            return "request_sent"
        except ValueError as e:
            if "No user has" in str(e) or "Cannot find" in str(e):
                return "failed: Chat not found"
            if "expired" in str(e).lower() or "not valid anymore" in str(e).lower():
                return "invalid_target"
            err_lower = str(e).lower()
            if "database is locked" in err_lower or "already exists" in err_lower or "no workers running" in err_lower:
                logging.warning(f"Join chat transient for telethon: {e}")
            else:
                logging.error(f"Join chat failed for telethon: {e}")
            return f"failed: {simplify_error(e)}"
        except Exception as e:
            error_str = str(e)
            if "already a participant" in error_str.lower() or "USER_ALREADY_PARTICIPANT" in error_str.upper() or "UserAlreadyParticipant" in type(e).__name__:
                return "success"
            if "Cannot cast InputPeerUser" in error_str:
                return "failed: Cannot join a user (bot/person)"
            if "expired" in error_str.lower() or "invitehashexpired" in error_str.lower() or "not valid anymore" in error_str.lower():
                return "invalid_target"
            err_lower = str(e).lower()
            if "database is locked" in err_lower or "already exists" in err_lower or "no workers running" in err_lower:
                logging.warning(f"Join chat transient for telethon: {e}")
            else:
                logging.error(f"Join chat failed for telethon: {e}")
            return f"failed: {simplify_error(e)}"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_join_chat(source_message, zip_id, target):
    targets = [t.strip() for t in re.split(r'[\n,\s]+', target) if t.strip()]
    if not targets:
        return await app.send_message(source_message.chat.id, "❌ No valid group/channel username or invite links found.")

    total = len(files) if (files := get_session_files(zip_id)) else 0
    if not total:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    progress = ThrottledProgress(
        source_message,
        f"📣 Joining Chat — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                session_statuses = []
                for t in targets:
                    status = await join_chat_for_session(file_path, t)
                    session_statuses.append(status)
                    if len(targets) > 1:
                        await asyncio.sleep(1)

                if "inactive" in session_statuses:
                    final_status = "inactive"
                elif "rate_limited" in session_statuses:
                    final_status = "rate_limited"
                elif "peer_flood" in session_statuses:
                    final_status = "peer_flood"
                elif "request_sent" in session_statuses:
                    final_status = "request_sent"
                elif "bot_session" in session_statuses:
                    final_status = "bot_session"
                elif "success" in session_statuses:
                    final_status = "success"
                else:
                    final_status = "failed"

                groups.setdefault(final_status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Successfully Joined", "icon": "✅"},
        "request_sent": {"label": "Request Sent (Approval Pending)", "icon": "📩"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "peer_flood": {"label": "Peer Flood / Restricted", "icon": "⚠️"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        if status.startswith("failed:"):
            err_msg = status[7:].strip()
            if len(err_msg) > 60: err_msg = err_msg[:57] + "..."
            result_lines.append(f"❌ Failed ({err_msg}): `{len(items)}`")
        else:
            meta = labels.get(status, {"label": status, "icon": "❓"})
            result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    
    targets_str = ", ".join(targets)
    if len(targets_str) > 100:
        targets_str = targets_str[:97] + "..."
    await app.send_message(source_message.chat.id, f"📣 **Mass Join Chat Results**\nTargets: `{targets_str}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def leave_chat_for_session(session_path, target):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                target_peer = target
                if isinstance(target, str) and ("+" in target or "joinchat" in target):
                    invite_match = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w\-]+)', target)
                    hash_val = invite_match.group(1) if invite_match else target.replace("https://t.me/", "").replace("+", "").split("/")[-1]
                    try:
                        from pyrogram.raw.functions.messages import CheckChatInvite
                        from pyrogram.raw.types import ChatInviteAlready, ChatInvite
                        res = await client.invoke(CheckChatInvite(hash=hash_val))
                        if isinstance(res, ChatInviteAlready):
                            c = res.chat
                            if getattr(c, "megagroup", False) or getattr(c, "broadcast", False):
                                target_peer = int(f"-100{c.id}")
                            else:
                                target_peer = int(f"-{c.id}")
                        elif isinstance(res, ChatInvite):
                            return "success"
                        else:
                            return "invalid_target"
                    except Exception:
                        return "invalid_target"
                
                await client.leave_chat(target_peer)
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception as e:
            if "USER_NOT_PARTICIPANT" in str(e) or "not a member" in str(e).lower() or "UserNotParticipant" in type(e).__name__ or "CHANNEL_PRIVATE" in str(e) or "ChannelPrivateError" in type(e).__name__ or "private and you lack permission" in str(e):
                return "success"
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Leave chat failed for pyrogram: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            if isinstance(target, str) and ("+" in target or "joinchat" in target):
                invite_match = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w\-]+)', target)
                hash_val = invite_match.group(1) if invite_match else target.replace("https://t.me/", "").replace("+", "").split("/")[-1]
                try:
                    from telethon.tl.functions.messages import CheckChatInviteRequest
                    from telethon.tl.types import ChatInviteAlready, ChatInvite
                    res = await client(CheckChatInviteRequest(hash=hash_val))
                    if isinstance(res, ChatInviteAlready):
                        entity = res.chat
                    elif isinstance(res, ChatInvite):
                        return "success"
                    else:
                        return "invalid_target"
                except Exception:
                    return "invalid_target"
            else:
                entity = await client.get_entity(target)
            if isinstance(entity, Channel):
                await client(LeaveChannelRequest(channel=entity))
            elif isinstance(entity, Chat):
                await client(DeleteChatUserRequest(chat_id=entity.id, user_id='me'))
            else:
                return "failed"
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception as e:
            if "USER_NOT_PARTICIPANT" in str(e) or "not a member" in str(e).lower() or "UserNotParticipant" in type(e).__name__ or "CHANNEL_PRIVATE" in str(e) or "ChannelPrivateError" in type(e).__name__ or "private and you lack permission" in str(e):
                return "success"
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "Could not find the input entity" in str(e) or "invalid peer" in str(e).lower() or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Leave chat failed for telethon: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_leave_chat(source_message, zip_id, target):
    raw_targets = [t.strip() for t in re.split(r'[\n,\s]+', target) if t.strip()]
    if not raw_targets:
        return await app.send_message(source_message.chat.id, "❌ No valid group/channel username or invite links found.")
        
    targets = []
    for t in raw_targets:
        parsed_peer, _ = parse_post_link(t)
        if parsed_peer:
            targets.append(parsed_peer)
        else:
            tp = t.strip()
            if "t.me/+" in tp or "t.me/joinchat/" in tp or "telegram.me/+" in tp or "telegram.me/joinchat/" in tp or tp.startswith("+"):
                targets.append(tp)
            else:
                if "t.me/" in tp or "telegram.me/" in tp:
                    tp = tp.rstrip("/").split("/")[-1]
                if not str(tp).startswith("@") and not str(tp).replace("-", "").isdigit():
                    tp = "@" + str(tp)
                if str(tp).replace("-", "").isdigit():
                    tp = int(tp)
                targets.append(tp)

    total = len(files) if (files := get_session_files(zip_id)) else 0
    if not total:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    progress = ThrottledProgress(
        source_message,
        f"📣 Leaving Chat — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                session_statuses = []
                for t in targets:
                    status = await leave_chat_for_session(file_path, t)
                    session_statuses.append(status)
                    if len(targets) > 1:
                        await asyncio.sleep(1)

                if "inactive" in session_statuses:
                    final_status = "inactive"
                elif "rate_limited" in session_statuses:
                    final_status = "rate_limited"
                elif "bot_session" in session_statuses:
                    final_status = "bot_session"
                elif "success" in session_statuses:
                    final_status = "success"
                else:
                    final_status = "failed"

                groups.setdefault(final_status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Successfully Left", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "invalid_target": {"label": "Invalid Target", "icon": "🎯"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    
    targets_str = ", ".join(targets)
    if len(targets_str) > 100:
        targets_str = targets_str[:97] + "..."
    await app.send_message(source_message.chat.id, f"📣 **Mass Leave Chat Results**\nTargets: `{targets_str}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def send_msg_for_session(session_path, target, text):
    original_target = target
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    if isinstance(target, str):
        target = target.strip()
        if "?start=" in target:
            target, start_param = target.split("?start=", 1)
            if text.strip() == "/start":
                text = f"/start {start_param}"
        
        if "t.me/" in target or "telegram.me/" in target:
            target = target.rstrip("/").split("/")[-1]
            
        if not target.startswith("@") and not target.lstrip("-").isdigit() and not target.startswith("+"):
            target = "@" + target
            
        if target.lstrip("-").isdigit() and not target.startswith("+"):
            target = int(target)
    
    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                try:
                    await client.send_message(target, text)
                except Exception as e:
                    err_str = str(e).lower()
                    err_type = type(e).__name__.lower()
                    if ("write_forbidden" in err_str or "private" in err_str or 
                        "lack permission" in err_str or "not a participant" in err_str or
                        "not a member" in err_str or "chatwriteforbidden" in err_type or
                        "join the discussion group" in err_str or "peeridinvalid" in err_type or 
                        "username" in err_type or "badrequest" in err_type):
                        try:
                            from pyrogram.errors import UserAlreadyParticipant
                            chat_id_to_send = target
                            try:
                                chat = await client.join_chat(original_target)
                                chat_id_to_send = chat.id
                            except UserAlreadyParticipant:
                                pass # We are already in it, but we need the chat ID
                            
                            # If we still have the invite link string, we must resolve it
                            if isinstance(chat_id_to_send, str) and (chat_id_to_send.startswith('+') or "joinchat" in chat_id_to_send):
                                # Let's get the chat from the invite link
                                chat = await client.get_chat(original_target)
                                chat_id_to_send = chat.id

                            await client.send_message(chat_id_to_send, text)
                        except Exception:
                            raise e
                    else:
                        raise e
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except (PyroPeerFlood, PyroUserBannedInChannel, PyroChatWriteForbidden):
            return "peer_flood"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "PeerIdInvalid" in type(e).__name__ or "ChannelInvalid" in type(e).__name__ or "ChatIdInvalid" in type(e).__name__ or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__ or "CHANNEL_PRIVATE" in str(e) or "ChannelPrivate" in type(e).__name__ or "private and you lack permission" in str(e).lower() or "no user has" in str(e).lower() or "cannot find any entity corresponding to" in str(e).lower():
                return "invalid_target"
            logging.error(f"Send message failed for pyrogram: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            try:
                await client.send_message(target, text)
            except Exception as e:
                err_str = str(e).lower()
                err_type = type(e).__name__.lower()
                if ("write_forbidden" in err_str or "private" in err_str or 
                    "lack permission" in err_str or "not a participant" in err_str or
                    "not a member" in err_str or "chatwriteforbidden" in err_type or
                    "join the discussion group" in err_str or "cannot find any entity" in err_str or 
                    "could not find the input entity" in err_str):
                    try:
                        from telethon.tl.functions.messages import CheckChatInviteRequest
                        from telethon.tl.types import ChatInviteAlready
                        target_str = str(original_target).strip()
                        entity_to_send = target
                        invite_hash = None
                        
                        invite_match = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w\-]+)', target_str)
                        if invite_match:
                            invite_hash = invite_match.group(1)
                        elif target_str.startswith('+'):
                            invite_hash = target_str[1:]
                        elif "joinchat/" in target_str:
                            invite_hash = target_str.split("joinchat/")[-1]
                            
                        if invite_hash:
                            invite_info = await client(CheckChatInviteRequest(hash=invite_hash))
                            if isinstance(invite_info, ChatInviteAlready):
                                entity_to_send = invite_info.chat.id
                            else:
                                res = await client(ImportChatInviteRequest(hash=invite_hash))
                                if hasattr(res, 'chats') and res.chats: 
                                    entity_to_send = res.chats[0].id
                        else:
                            target_str = re.sub(r'^https?://(?:w{3}\.)?(?:t\.me|telegram\.me)/', '', target_str).strip('@')
                            entity = await client.get_entity(target_str)
                            if isinstance(entity, Channel):
                                await client(JoinChannelRequest(channel=entity))
                            entity_to_send = entity
                        await client.send_message(entity_to_send, text)
                    except Exception:
                        raise e
                else:
                    raise e
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except (TelePeerFloodError, TeleUserBannedInChannelError, TeleChatWriteForbiddenError):
            return "peer_flood"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "PeerIdInvalid" in type(e).__name__ or "ChannelInvalid" in type(e).__name__ or "ChatIdInvalid" in type(e).__name__ or "Could not find the input entity" in str(e) or "invalid peer" in str(e).lower() or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__ or "CHANNEL_PRIVATE" in str(e) or "ChannelPrivate" in type(e).__name__ or "private and you lack permission" in str(e).lower() or "no user has" in str(e).lower() or "cannot find any entity corresponding to" in str(e).lower():
                return "invalid_target"
            logging.error(f"Send message failed for telethon: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_send_msg(source_message, zip_id, target, text):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"💬 Sending Message — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await send_msg_for_session(file_path, target, text)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Sent Successfully", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "peer_flood": {"label": "Peer Flood / Restricted", "icon": "⚠️"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "invalid_target": {"label": "Invalid Target", "icon": "🎯"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"💬 **Mass Message Results**\nTarget: `{target}`\nMessage: `{text}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def broadcast_for_session(session_path, target_type, text):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    # Stagger connections to protect accounts from flood/freezing

    sent_count = 0
    failed_count = 0

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session", 0, 0
                
                dialogs = []
                async for dialog in client.get_dialogs():
                    dialogs.append(dialog)
                
                for dialog in dialogs:
                    chat = dialog.chat
                    if is_protected_chat(chat, me.id):
                        continue
                    
                    is_match = False
                    if target_type == "groups":
                        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                            is_match = True
                    elif target_type == "dms":
                        if chat.type in (ChatType.PRIVATE, ChatType.BOT):
                            is_match = True
                    elif target_type == "all":
                        if chat.type in (ChatType.PRIVATE, ChatType.BOT, ChatType.GROUP, ChatType.SUPERGROUP):
                            is_match = True
                    
                    if is_match:
                        try:
                            await client.send_message(chat.id, text)
                            sent_count += 1
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                            try:
                                await client.send_message(chat.id, text)
                                sent_count += 1
                            except Exception:
                                failed_count += 1
                        except Exception:
                            failed_count += 1
                return "success", sent_count, failed_count
        except INVALID_EXCEPTIONS:
            return "inactive", 0, 0
        except Exception as e:
            logging.error(f"Broadcast failed for pyrogram: {e}")
            return "failed", 0, 0

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive", 0, 0
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session", 0, 0

            dialogs = []
            async for dialog in client.iter_dialogs():
                dialogs.append(dialog)

            for dialog in dialogs:
                if is_protected_chat_telethon(dialog, me.id):
                    continue
                
                is_match = False
                if target_type == "groups":
                    if dialog.is_group:
                        is_match = True
                elif target_type == "dms":
                    if dialog.is_user:
                        is_match = True
                elif target_type == "all":
                    if dialog.is_group or dialog.is_user:
                        is_match = True
                
                if is_match:
                    try:
                        await client.send_message(dialog.entity, text)
                        sent_count += 1
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                        try:
                            await client.send_message(dialog.entity, text)
                            sent_count += 1
                        except Exception:
                            failed_count += 1
                    except Exception:
                        failed_count += 1
            return "success", sent_count, failed_count
        except INVALID_EXCEPTIONS:
            return "inactive", 0, 0
        except Exception as e:
            logging.error(f"Broadcast failed for telethon: {e}")
            return "failed", 0, 0
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported", 0, 0


async def run_broadcast(source_message, zip_id, target_type, text):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"📢 Broadcasting Message — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    total_sent = 0
    total_failed = 0
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            nonlocal total_sent, total_failed
            async with sem:
                status, sent, failed = await broadcast_for_session(file_path, target_type, text)
                groups.setdefault(status, []).append(file_path)
                total_sent += sent
                total_failed += failed
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Broadcast Completed", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "unsupported": {"label": "Unsupported", "icon": "❓"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}` account(s)")
    
    await app.send_message(
        source_message.chat.id,
        f"📢 **Mass Broadcast Results ({target_type.upper()})**\n"
        f"💬 Message: `{text}`\n\n"
        f"✨ **Stats:**\n"
        f" ├ ✉️ Total Sent: `{total_sent}`\n"
        f" └ ⚠️ Total Failed: `{total_failed}`\n\n"
        f"👥 **Accounts Status:**\n" + "\n".join(result_lines)
    )
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def report_target_for_session(session_path, target, reason):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    import random
    await asyncio.sleep(random.uniform(0.5, 2.0))
    
    if session_type == "pyrogram":
        try:
            from pyrogram.raw.functions.account import ReportPeer
            import pyrogram.raw.types as pyro_types
            pyro_raw_reasons = {
                "spam": pyro_types.InputReportReasonSpam(),
                "violence": pyro_types.InputReportReasonViolence(),
                "pornography": pyro_types.InputReportReasonPornography(),
                "child_abuse": pyro_types.InputReportReasonChildAbuse(),
                "copyright": pyro_types.InputReportReasonCopyright(),
                "other": pyro_types.InputReportReasonOther(),
                "fake": pyro_types.InputReportReasonFake(),
                "drugs": pyro_types.InputReportReasonIllegalDrugs(),
                "personal_details": pyro_types.InputReportReasonPersonalDetails(),
            }
            raw_reason = pyro_raw_reasons.get(reason, pyro_types.InputReportReasonOther())

            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                
                peer = await client.resolve_peer(target)
                await client.invoke(
                    ReportPeer(
                        peer=peer,
                        reason=raw_reason,
                        message="reported by user"
                    )
                )
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Report target failed for pyrogram: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            from telethon.tl.functions.account import ReportPeerRequest
            tele_reason = tele_reasons.get(reason, InputReportReasonOther())
            peer = await client.get_input_entity(target)
            await client(ReportPeerRequest(peer=peer, reason=tele_reason, message="reported by user"))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "Could not find the input entity" in str(e) or "invalid peer" in str(e).lower() or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Report target failed for telethon: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_report_target(source_message, zip_id, target, reason):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    parsed_peer, _ = parse_post_link(target)
    if parsed_peer:
        target_peer = parsed_peer
    else:
        target_peer = target.strip()
        if "t.me/" in target_peer or "telegram.me/" in target_peer:
            target_peer = target_peer.rstrip("/").split("/")[-1]
        if not str(target_peer).startswith("@") and not str(target_peer).replace("-", "").isdigit():
            target_peer = "@" + str(target_peer)
        if str(target_peer).replace("-", "").isdigit():
            target_peer = int(target_peer)

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"🛡 Reporting Target — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await report_target_for_session(file_path, target_peer, reason)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Reported Successfully", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "invalid_target": {"label": "Invalid Target", "icon": "🎯"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"🛡 **Mass Report Results**\nTarget: `{target}`\nReason: `{reason}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


def parse_post_link(text):
    import re
    text = text.strip()
    
    # Check for private channel post format: t.me/c/123456789/123
    private_match = re.search(r'(?:t\.me/|telegram\.me/)(?:c)/(\d+)/(\d+)', text)
    if private_match:
        target = int(f"-100{private_match.group(1)}")
        msg_id = int(private_match.group(2))
        return target, msg_id

    # Check for public channel post format: t.me/username/123
    public_match = re.search(r'(?:t\.me/|telegram\.me/)([^/]+)/(\d+)', text)
    if public_match:
        target = public_match.group(1)
        msg_id = int(public_match.group(2))
        if not target.startswith("-") and not target.isdigit():
            target = f"@{target}"
        return target, msg_id

    return None, None


async def increase_views_for_session(session_path, target, msg_id):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                
                try:
                    peer = await client.resolve_peer(target)
                    from pyrogram.raw.functions.messages import GetMessagesViews
                    await client.invoke(GetMessagesViews(peer=peer, id=[msg_id], increment=True))
                except Exception as e:
                    err_str = str(e).lower()
                    if ("private" in err_str or "lack permission" in err_str or "not a participant" in err_str or "not a member" in err_str or "join the discussion group" in err_str):
                        try:
                            await client.join_chat(target)
                            peer = await client.resolve_peer(target)
                            from pyrogram.raw.functions.messages import GetMessagesViews
                            await client.invoke(GetMessagesViews(peer=peer, id=[msg_id], increment=True))
                        except Exception:
                            try:
                                await client.get_messages(target, msg_id)
                            except Exception:
                                raise e
                    else:
                        try:
                            await client.get_messages(target, msg_id)
                        except Exception:
                            pass
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Increase views failed for pyrogram: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"

            from telethon.tl.functions.messages import GetMessagesViewsRequest
            try:
                peer = await client.get_input_entity(target)
                await client(GetMessagesViewsRequest(peer=peer, id=[msg_id], increment=True))
            except Exception as e:
                err_str = str(e).lower()
                if ("private" in err_str or "lack permission" in err_str or "not a participant" in err_str or "not a member" in err_str or "join the discussion group" in err_str):
                    try:
                        from telethon.tl.functions.channels import JoinChannelRequest
                        if hasattr(peer, 'channel_id'):
                            await client(JoinChannelRequest(channel=peer))
                        await client(GetMessagesViewsRequest(peer=peer, id=[msg_id], increment=True))
                    except Exception:
                        try:
                            await client.get_messages(target, ids=[msg_id])
                        except Exception:
                            raise e
                else:
                    try:
                        await client.get_messages(target, ids=[msg_id])
                    except Exception:
                        pass
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception as e:
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "Could not find the input entity" in str(e) or "invalid peer" in str(e).lower() or "UsernameInvalid" in type(e).__name__ or "UsernameNotOccupied" in type(e).__name__:
                return "invalid_target"
            logging.error(f"Increase views failed for telethon: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_increase_views(source_message, zip_id, target, msg_id):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"👁 Increasing Views — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await increase_views_for_session(file_path, target, msg_id)
            groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Views Increased", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "invalid_target": {"label": "Invalid Target", "icon": "🎯"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"👁 **Mass Views Results**\nTarget: `{target}`\nMessage ID: `{msg_id}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


# ─────────────────────────────────────────────────────────────────────────────
# Reaction Emoji Lists (for random reaction mode)
# ─────────────────────────────────────────────────────────────────────────────
REACTION_EMOJIS_FREE = [
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
    "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨",
    "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿",
    "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂",
    "🤷", "🤷‍♀", "😡",
]

REACTION_EMOJIS_PREMIUM = [
    "🤑", "😋", "🫠", "😲", "🤐", "😑", "🫥", "🫤", "🤫", "🫣",
    "🥲", "😓", "😖", "😣", "😞", "😤", "😠", "🤯", "🥶", "🥵",
    "😶‍🌫️", "🤥", "😬", "🙄", "😮‍💨", "😪", "🤤", "😵", "😵‍💫", "🥳",
    "🥸", "🧐", "🤠", "😻", "🙀", "😿", "💀", "☠", "💟", "🩷",
    "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🩵", "💝", "💓",
    "💗", "💖", "❣", "💞", "🫶", "👃", "🦷", "🦴", "👄", "🫦",
    "🌹", "🌺", "🪻", "🌸", "🌼", "🌻", "🍀", "🎋", "🎍", "🐶",
    "🐱", "🐹", "🐰", "🦊", "🐻", "🐼", "🐸", "🦁", "🐮", "🐷",
    "🐔", "🐧", "🐦", "🐤", "🦋", "🐛", "🐝", "🦀", "🐙", "🐠",
    "🐡", "🐬", "🦈", "🐳", "🐊", "🦩", "🦜", "🕊", "🐕", "🐈",
    "🦙", "🐏", "🐐", "🐓", "🦃", "🕷", "🦂", "🐢", "🐍", "🦎",
    "🐙", "🦑", "🐌", "🐞", "🍎", "🍐", "🍊", "🍋", "🍑", "🍒",
    "🍈", "🍇", "🍉", "🍏", "🍓", "🍌", "🥝", "🥭", "🥥", "🍍",
    "🍅", "🍆", "🥑", "🥦", "🌽", "🌶", "🍄", "🥜", "🧄", "🧅",
    "🌰", "🍞", "🥐", "🥖", "🧀", "🥚", "🍳", "🥞", "🧇", "🍕",
    "🌮", "🌯", "🍔", "🍟", "🧆", "🥙", "🍗", "🍖", "🥩", "🍝",
    "🍜", "🍣", "🍱", "🥟", "🍤", "🍙", "🍚", "🍘", "🍥", "🍡",
    "🍧", "🍨", "🍦", "🎂", "🍰", "🧁", "🍫", "🍬", "🍭", "🍮",
    "☕", "🍵", "🧃", "🥤", "🍶", "🍺", "🍻", "🥂", "🍷", "🥃",
    "🍸", "🍹", "🧊", "⚽", "🏀", "🏈", "⚾", "🎾", "🏐", "🏉",
    "🎱", "🏓", "🏸", "🥊", "🎯", "⛳", "🥇", "🥈", "🥉", "🏅",
    "🎖", "🎗", "🎪", "🎨", "🎭", "🎤", "🎧", "🎼", "🎹", "🎷",
    "🎺", "🎸", "🎻", "🎬", "🎮", "🕹", "🎰", "🎲", "🧩", "♟",
    "🌍", "🌎", "🌏", "🌐", "🗺", "🧭", "🏔", "⛰", "🌋", "🗻",
    "🏕", "🏖", "🏜", "🏝", "🏞", "🌅", "🌄", "🌠", "🎇", "🎆",
    "🌇", "🌆", "🏙", "🌃", "🌉", "🌌", "🌑", "🌒", "🌓", "🌔",
    "🌕", "🌖", "🌗", "🌘", "🌙", "🌛", "🌜", "☀", "🌝", "🌞",
    "⭐", "🌟", "🌈", "☁", "⛅", "⛈", "🌤", "🌥", "🌦", "🌧",
    "🌨", "🌩", "🌪", "🌫", "☔", "❄", "💧", "🌊", "🎑", "🎃",
    "💐", "🪷", "🌷",
]

# Combined list for "random" mode
REACTION_EMOJIS_ALL = REACTION_EMOJIS_FREE + REACTION_EMOJIS_PREMIUM


def _build_reaction(emoji_str):
    """Determine if emoji_str is a custom emoji ID (numeric) or a regular emoticon.
    Returns a tuple: (is_custom: bool, value: int|str)
    """
    if emoji_str.isdigit() and len(emoji_str) > 6:
        return (True, int(emoji_str))
    return (False, emoji_str)


async def fetch_channel_reactions(session_path, target):
    """Fetch the list of available reactions for a channel/group using one session.
    Returns a list of emoji strings and/or custom emoji document IDs (as strings), or None.
    """
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return None
                from pyrogram.raw.functions.channels import GetFullChannel
                from pyrogram.raw.types import InputChannel
                peer = await client.resolve_peer(target)
                if not hasattr(peer, 'channel_id'):
                    return None
                full = await client.invoke(GetFullChannel(
                    channel=InputChannel(
                        channel_id=peer.channel_id,
                        access_hash=peer.access_hash
                    )
                ))
                reactions_setting = getattr(full.full_chat, 'available_reactions', None)
                if not reactions_setting:
                    return None
                available = []
                if hasattr(reactions_setting, 'reactions'):
                    for r in reactions_setting.reactions:
                        if hasattr(r, 'emoticon'):
                            available.append(r.emoticon)
                        elif hasattr(r, 'document_id'):
                            available.append(str(r.document_id))
                return available if available else None
        except Exception as e:
            logging.warning(f"Failed to fetch channel reactions (pyrogram): {e}")
            return None

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return None
            from telethon.tl.functions.channels import GetFullChannelRequest
            peer = await client.get_input_entity(target)
            full = await client(GetFullChannelRequest(channel=peer))
            reactions_setting = getattr(full.full_chat, 'available_reactions', None)
            if not reactions_setting:
                return None
            available = []
            if hasattr(reactions_setting, 'reactions'):
                for r in reactions_setting.reactions:
                    if hasattr(r, 'emoticon'):
                        available.append(r.emoticon)
                    elif hasattr(r, 'document_id'):
                        available.append(str(r.document_id))
            return available if available else None
        except Exception as e:
            logging.warning(f"Failed to fetch channel reactions (telethon): {e}")
            return None
        finally:
            await safe_telethon_disconnect(client)

    return None


async def react_post_for_session(session_path, target, msg_id, emoji):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    is_custom, emoji_val = _build_reaction(emoji)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                
                try:
                    await client.get_messages(target, msg_id)
                except Exception:
                    pass

                # Build the reaction object — custom emoji ID or regular emoticon
                from pyrogram.raw.functions.messages import SendReaction as PyroSendReaction
                if is_custom:
                    from pyrogram.raw.types import ReactionCustomEmoji as PyroReactionCustom
                    reaction_obj = [PyroReactionCustom(document_id=emoji_val)]
                else:
                    from pyrogram.raw.types import ReactionEmoji as PyroReactionEmoji
                    reaction_obj = [PyroReactionEmoji(emoticon=emoji_val)]

                peer = await client.resolve_peer(target)

                try:
                    await client.invoke(PyroSendReaction(
                        peer=peer,
                        msg_id=msg_id,
                        reaction=reaction_obj
                    ))
                except Exception as e:
                    err_str = str(e).lower()
                    if ("write_forbidden" in err_str or "private" in err_str or 
                        "lack permission" in err_str or "not a participant" in err_str or
                        "not a member" in err_str or "chatwriteforbidden" in type(e).__name__.lower() or
                        "join the discussion group" in err_str):
                        try:
                            await client.join_chat(target)
                            await client.invoke(PyroSendReaction(
                                peer=peer,
                                msg_id=msg_id,
                                reaction=reaction_obj
                            ))
                        except Exception:
                            raise e
                    else:
                        raise e
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception as e:
            err_str = str(e).lower()
            if "banned" in err_str or "userbannedinchannel" in type(e).__name__.lower() or "invokewithoutupdatesrequest" in err_str:
                return "banned_in_chat"
            if "MESSAGE_ID_INVALID" in str(e) or "MessageIdInvalid" in type(e).__name__ or "you can't write in this chat" in err_str or "chatwriteforbidden" in type(e).__name__.lower():
                return "invalid_msg"
            if "custom_reactions_too_many" in err_str:
                return "custom_limit"
            if "REACTION_INVALID" in str(e) or "ReactionInvalid" in type(e).__name__:
                return "invalid_reaction"
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "PeerIdInvalid" in type(e).__name__ or "ChannelInvalid" in type(e).__name__ or "ChatIdInvalid" in type(e).__name__ or "USERNAME_INVALID" in str(e) or "UsernameInvalid" in type(e).__name__ or "USERNAME_NOT_OCCUPIED" in str(e):
                return "invalid_target"
            logging.error(f"React post failed for pyrogram: {e}")
            return f"failed: {simplify_error(e)}"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"

            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            from telethon.tl.types import ReactionCustomEmoji as TeleReactionCustom
            
            peer = await client.get_input_entity(target)
            
            # Build the reaction object — custom emoji ID or regular emoticon
            if is_custom:
                reaction_obj = [TeleReactionCustom(document_id=emoji_val)]
            else:
                reaction_obj = [ReactionEmoji(emoticon=emoji_val)]

            try:
                await client.get_messages(peer, ids=[msg_id])
            except Exception:
                pass

            try:
                await client(SendReactionRequest(
                    peer=peer,
                    msg_id=msg_id,
                    reaction=reaction_obj
                ))
            except Exception as e:
                err_str = str(e).lower()
                if ("write_forbidden" in err_str or "private" in err_str or 
                    "lack permission" in err_str or "not a participant" in err_str or
                    "not a member" in err_str or "chatwriteforbidden" in type(e).__name__.lower() or
                    "join the discussion group" in err_str):
                    try:
                        from telethon.tl.functions.channels import JoinChannelRequest
                        if hasattr(peer, 'channel_id'):
                            await client(JoinChannelRequest(channel=peer))
                        await client(SendReactionRequest(
                            peer=peer,
                            msg_id=msg_id,
                            reaction=reaction_obj
                        ))
                    except Exception:
                        raise e
                else:
                    raise e
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception as e:
            err_str = str(e).lower()
            if "banned" in err_str or "userbannedinchannel" in type(e).__name__.lower() or "invokewithoutupdatesrequest" in err_str:
                return "banned_in_chat"
            if "MESSAGE_ID_INVALID" in str(e) or "MessageIdInvalid" in type(e).__name__ or "you can't do that operation on such message" in str(e) or "you can't write in this chat" in err_str or "chatwriteforbidden" in type(e).__name__.lower():
                return "invalid_msg"
            if "custom_reactions_too_many" in err_str:
                return "custom_limit"
            if "REACTION_INVALID" in str(e) or "ReactionInvalid" in type(e).__name__:
                return "invalid_reaction"
            if "PEER_ID_INVALID" in str(e) or "CHANNEL_INVALID" in str(e) or "PeerIdInvalid" in type(e).__name__ or "ChannelInvalid" in type(e).__name__ or "ChatIdInvalid" in type(e).__name__ or "Could not find the input entity" in str(e) or "invalid peer" in err_str or "Cannot find any entity" in str(e):
                return "invalid_target"
            logging.error(f"React post failed for telethon: {e}")
            return f"failed: {simplify_error(e)}"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_react_post(source_message, zip_id, target, msg_id, emoji):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    is_random = emoji.lower() == "random"
    total = len(files)

    # For random mode, fetch the channel's available reactions (including custom emojis)
    reaction_pool = None
    if is_random:
        status_msg = await safe_edit(source_message, "🎲 Fetching available reactions from channel...")
        for f in files[:5]:  # Try up to 5 sessions to fetch reactions
            try:
                reaction_pool = await fetch_channel_reactions(f, target)
                if reaction_pool:
                    logging.info(f"Fetched {len(reaction_pool)} available reactions from channel")
                    break
            except Exception:
                continue
        if not reaction_pool:
            reaction_pool = REACTION_EMOJIS_FREE  # Fallback to default free emojis
            logging.info("Could not fetch channel reactions, using default emoji list")

    mode_label = f"🎲 Random ({len(reaction_pool)} available)" if is_random else emoji
    progress = ThrottledProgress(
        source_message,
        f"🎭 Reacting to Post — Pack #{zip_id}\nMode: {mode_label}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            # Pick a random emoji/custom_id per session if random mode
            session_emoji = random.choice(reaction_pool) if is_random else emoji
            async with sem:
                status = await react_post_for_session(file_path, target, msg_id, session_emoji)
            # If reaction was invalid in random mode, retry with another from the pool
            if status == "invalid_reaction" and is_random:
                session_emoji = random.choice(reaction_pool)
                async with sem:
                    status = await react_post_for_session(file_path, target, msg_id, session_emoji)
            groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Reaction Sent", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "invalid_target": {"label": "Invalid Target/Peer", "icon": "🎯"},
        "invalid_msg": {"label": "Invalid Msg/Reactions Disabled", "icon": "🚫"},
        "invalid_reaction": {"label": "Invalid Emoji Used", "icon": "⚠️"},
        "custom_limit": {"label": "Custom Reaction Limit Reached", "icon": "🛑"},
        "banned_in_chat": {"label": "Banned in Chat", "icon": "🔇"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        if status.startswith("failed:"):
            err_msg = status[7:].strip()
            if len(err_msg) > 60: err_msg = err_msg[:57] + "..."
            result_lines.append(f"❌ Failed ({err_msg}): `{len(items)}`")
        else:
            meta = labels.get(status, {"label": status, "icon": "❓"})
            result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    
    if is_random:
        # Show which reactions were available
        custom_count = sum(1 for r in reaction_pool if r.isdigit() and len(r) > 6)
        regular_count = len(reaction_pool) - custom_count
        emoji_display = f"🎲 Random ({regular_count} regular + {custom_count} custom emojis)"
    else:
        is_custom_emoji = emoji.isdigit() and len(emoji) > 6
        emoji_display = f"🖤 Custom Emoji `{emoji}`" if is_custom_emoji else emoji
    await app.send_message(source_message.chat.id, f"🎭 **Mass Reaction Results**\nTarget: `{target}`\nMessage ID: `{msg_id}`\nEmoji: {emoji_display}\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def change_avatar_for_session(session_path, photo_path):
    """Change profile avatar safely with anti-freeze protection."""
    if not photo_path or not os.path.isfile(photo_path) or os.path.getsize(photo_path) == 0:
        return "failed"

    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                await client.set_profile_photo(photo=photo_path)
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            if getattr(e, "value", 0) <= 15:
                await asyncio.sleep(e.value)
                try:
                    await client.set_profile_photo(photo=photo_path)
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change avatar failed for pyrogram {session_name}: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj, workdir=workdir, session_name=session_name)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            uploaded = await client.upload_file(photo_path)
            await client(UploadProfilePhotoRequest(file=uploaded))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            if getattr(e, "seconds", 0) <= 15:
                await asyncio.sleep(e.seconds)
                try:
                    uploaded = await client.upload_file(photo_path)
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change avatar failed for telethon {session_name}: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_change_avatar(source_message, zip_id, photo_path):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"🖼 Changing Profile Picture — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    # Gentle concurrency with anti-freeze randomized sleep
    sem = asyncio.Semaphore(min(2, MAX_CONCURRENCY))

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                status = await change_avatar_for_session(file_path, photo_path)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except:
            pass

    labels = {
        "success": {"label": "Avatar Changed Successfully", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"🖼 **Mass Profile Picture Results**\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def delete_avatar_for_session(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                
                photos = []
                async for photo in client.get_chat_photos("me"):
                    photos.append(photo.file_id)
                
                if photos:
                    await client.delete_profile_photos(photos)
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            return "rate_limited"
        except Exception as e:
            logging.error(f"Delete avatar failed for pyrogram session {session_name}: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj, workdir=workdir, session_name=session_name)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            from telethon.tl.functions.photos import GetUserPhotosRequest, DeletePhotosRequest
            from telethon.tl.types import InputPhoto
            photos = await client(GetUserPhotosRequest(user_id="me", offset=0, max_id=0, limit=100))
            if photos and photos.photos:
                input_photos = [
                    InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)
                    for p in photos.photos
                ]
                await client(DeletePhotosRequest(id=input_photos))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            return "rate_limited"
        except Exception as e:
            logging.error(f"Delete avatar failed for telethon session {session_name}: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_delete_avatars(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🗑 Deleting Profile Pictures — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await delete_avatar_for_session(file_path)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Profile Pictures Deleted Successfully", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
        
    await app.send_message(call.message.chat.id, f"🗑 **Mass Profile Picture Deletion Results**\n\n" + "\n".join(result_lines))
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def show_delete_avatar_confirm(call, zip_id):
    files = get_session_files(zip_id)
    count = len(files)
    text = (
        f"🗑 **Delete Profile Pictures**\n\n"
        f"Are you sure you want to delete **ALL** profile pictures for all `{count}` session(s) in **Pack #{zip_id}**?\n\n"
        f"⚠️ **This will:**\n"
        f"- Delete all existing profile pictures from these accounts permanently."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Confirm Delete", callback_data=f"zip|delete_avatar|{zip_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"zip|cancel_delete_avatar|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


async def show_twofa_menu(call, zip_id):
    text = (
        f"🔐 **Pack #{zip_id} Two-Step Verification (2FA)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Manage cloud password security for all active accounts:\n"
        " • **➕ Enable**: Set a new 2FA password on accounts without one.\n"
        " • **✏️ Change**: Update/rotate the active 2FA password.\n"
        " • **➖ Disable**: Remove the active 2FA password entirely."
    )
    await safe_edit(call.message, text, reply_markup=twofa_keyboard(zip_id))


async def process_twofa(session_path, mode, current_password=None, new_password=None):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"

                if mode == "check":
                    try:
                        pwd_info = await client.invoke(functions.account.GetPassword())
                        return "2fa_on" if pwd_info.has_password else "2fa_off"
                    except Exception:
                        return "failed"

                if mode == "set":
                    try:
                        await client.enable_cloud_password(password=new_password)
                        return "success"
                    except BadRequest as exc:
                        if "PASSWORD" in str(exc).upper():
                            return "already_has_2fa"
                        return "failed"

                if mode == "edit":
                    try:
                        await client.change_cloud_password(current_password=current_password, new_password=new_password)
                        return "success"
                    except PasswordHashInvalid:
                        return "wrong_password"
                    except BadRequest:
                        return "failed"

                if mode == "remove":
                    try:
                        await client.remove_cloud_password(password=current_password)
                        return "success"
                    except PasswordHashInvalid:
                        return "wrong_password"
                    except BadRequest as exc:
                        text = str(exc).upper()
                        if "PASSWORD_MISSING" in text or "PASSWORD IS NOT SET" in text:
                            return "no_2fa"
                        return "failed"

        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception as exc:
            text = str(exc).lower()
            if "violations" in text or "spambot" in text:
                return "freeze"
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"

            if mode == "check":
                try:
                    pwd_info = await client(GetPasswordRequest())
                    return "2fa_on" if pwd_info.has_password else "2fa_off"
                except Exception:
                    return "failed"

            if mode == "set":
                try:
                    await client.edit_2fa(new_password=new_password)
                    return "success"
                except Exception as exc:
                    text = str(exc).upper()
                    if "PASSWORD" in text or "ALREADY" in text:
                        return "already_has_2fa"
                    return "failed"

            if mode == "edit":
                try:
                    await client.edit_2fa(current_password=current_password, new_password=new_password)
                    return "success"
                except Exception as exc:
                    text = str(exc).upper()
                    if "INVALID" in text or "HASH" in text or "PASSWORD" in text:
                        return "wrong_password"
                    return "failed"

            if mode == "remove":
                try:
                    await client.edit_2fa(current_password=current_password, new_password=None)
                    return "success"
                except Exception as exc:
                    text = str(exc).upper()
                    if "INVALID" in text or "HASH" in text or "PASSWORD" in text:
                        return "wrong_password"
                    return "failed"

        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception as exc:
            text = str(exc).lower()
            if "violations" in text or "spambot" in text:
                return "freeze"
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_twofa_operation(source_message, zip_id, mode, current_password=None, new_password=None):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    title_map = {
        "set": "🔐 Setting 2FA",
        "edit": "🔐 Changing 2FA",
        "remove": "🔐 Removing 2FA",
        "check": "🔍 Checking 2FA",
    }
    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"{title_map[mode]} For Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(SAFE_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                await asyncio.sleep(random.uniform(2.0, 3.5))
                status = await process_twofa(file_path, mode, current_password=current_password, new_password=new_password)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "2fa_on": {"label": "2FA is ON", "icon": "🔐"},
        "2fa_off": {"label": "2FA is OFF", "icon": "🔓"},
        "success": {"label": "2FA Set", "icon": "✅"},
        "inactive": {"label": "Dead", "icon": "❌"},
        "freeze": {"label": "Frozen", "icon": "🧊"},
        "wrong_password": {"label": "Wrong Password", "icon": "🚫"},
        "already_has_2fa": {"label": "Already Has 2FA", "icon": "⚠️"},
        "no_2fa": {"label": "No 2FA", "icon": "ℹ️"},
        "unsupported": {"label": "Not Supported", "icon": "⚠️"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "failed": {"label": "Failed", "icon": "❌"},
    }
    await send_classified_zips(source_message.chat.id, groups, labels)
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def send_renamed_zip(chat_id, zip_id, new_name):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(chat_id, "📭 ❌ No sessions found.")
    zip_path, temp_dir = await build_zip_from_sessions(files, f"{sanitize_filename(new_name)}.zip")
    try:
        await app.send_document(chat_id, zip_path, caption=format_result_caption("✏️ Relabeled Pack", len(files), "✏️"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


try:
    import phonenumbers
    from phonenumbers import geocoder
except ImportError:
    phonenumbers = None
    geocoder = None

GLOBAL_FALLBACK_COUNTRY_CODES = {
    "1": "USA_Canada", "7": "Russia_Kazakhstan", "20": "Egypt", "27": "South_Africa",
    "30": "Greece", "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain",
    "36": "Hungary", "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria",
    "44": "United_Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway", "48": "Poland",
    "49": "Germany", "51": "Peru", "52": "Mexico", "54": "Argentina", "55": "Brazil",
    "56": "Chile", "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia",
    "62": "Indonesia", "63": "Philippines", "64": "New_Zealand", "65": "Singapore", "66": "Thailand",
    "81": "Japan", "82": "South_Korea", "84": "Vietnam", "86": "China", "90": "Turkey",
    "91": "India", "92": "Pakistan", "93": "Afghanistan", "94": "Sri_Lanka", "95": "Myanmar",
    "98": "Iran", "212": "Morocco", "213": "Algeria", "216": "Tunisia", "218": "Libya",
    "220": "Gambia", "221": "Senegal", "225": "Ivory_Coast", "233": "Ghana", "234": "Nigeria",
    "237": "Cameroon", "251": "Ethiopia", "254": "Kenya", "255": "Tanzania", "256": "Uganda",
    "351": "Portugal", "353": "Ireland", "358": "Finland", "370": "Lithuania", "371": "Latvia",
    "372": "Estonia", "373": "Moldova", "375": "Belarus", "380": "Ukraine", "381": "Serbia",
    "385": "Croatia", "420": "Czech_Republic", "421": "Slovakia", "852": "Hong_Kong",
    "855": "Cambodia", "856": "Laos", "880": "Bangladesh", "961": "Lebanon", "962": "Jordan",
    "963": "Syria", "964": "Iraq", "965": "Kuwait", "966": "Saudi_Arabia", "967": "Yemen",
    "968": "Oman", "971": "United_Arab_Emirates", "972": "Israel", "992": "Tajikistan",
    "993": "Turkmenistan", "994": "Azerbaijan", "995": "Georgia", "996": "Kyrgyzstan", "998": "Uzbekistan"
}

def get_country_from_phone(phone_str):
    """
    Accurately resolves the country name for ANY phone number across all countries on Earth.
    Supports all 215+ country calling codes via standard libphonenumber with full English names.
    """
    if not phone_str:
        return "Unknown"
    
    clean = re.sub(r'[^0-9+]', '', str(phone_str))
    digits_only = re.sub(r'[^0-9]', '', clean)
    if not digits_only or len(digits_only) < 5:
        return "Unknown"
    
    with_plus = "+" + digits_only
    try:
        pn = phonenumbers.parse(with_plus, None)
        region = phonenumbers.region_code_for_number(pn)
        if region:
            if region in ('GB', 'GG', 'JE', 'IM'):
                return "United_Kingdom"
            # Get full clean country name
            name = geocoder._region_display_name(region, "en")
            if not name:
                name = geocoder.country_name_for_number(pn, "en")
            if name:
                cleaned_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.strip().replace(' ', '_'))
                cleaned_name = re.sub(r'_+', '_', cleaned_name).strip('_')
                if cleaned_name:
                    return cleaned_name
            return region
    except Exception:
        pass
    
    # Prefix matching fallback
    for l in (4, 3, 2, 1):
        c = digits_only[:l]
        if c in GLOBAL_FALLBACK_COUNTRY_CODES:
            return GLOBAL_FALLBACK_COUNTRY_CODES[c]
            
    return f"Country_{digits_only[:2]}"

async def check_spam_for_session(file_path):
    session_type = detect_session_type(file_path)
    session_name = os.path.basename(file_path).replace(".session", "")
    workdir = os.path.dirname(file_path)
    
    status = "timeout"
    try:
        if session_type == "pyrogram":
            async with make_pyro_client(session_name, workdir=workdir) as client:
                status, _ = await collect_spambot_status_pyrogram(client)
        elif session_type == "telethon":
            session_obj = safe_sqlite_session(file_path)
            client = make_tele_client(session_obj)
            try:
                await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                if await client.is_user_authorized():
                    status, _ = await collect_spambot_status_telethon(client)
                else:
                    status = "inactive"
            finally:
                await safe_telethon_disconnect(client)
    except INVALID_EXCEPTIONS:
        status = "inactive"
    except (FloodWait, FloodWaitError):
        status = "rate_limited"
    except Exception:
        status = "timeout"
        
    if status == "spamfree":
        return "clean"
    elif status in ["tempspam", "spam", "freeze"]:
        return "spam_blocked"
    else:
        return "dead_or_other"

async def run_split_country(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    
    status_msg = await call.message.reply_text("🌍 Splitting pack by country... Analyzing session phone numbers.")
    
    try:
        groups = {}
        for f in files:
            fname = os.path.basename(f)
            stem = fname.replace(".session", "")
            
            # Step 1: Resolve authentic phone number
            resolved_phone = None
            
            # Check paired JSON first
            workdir = os.path.dirname(f)
            json_candidate = os.path.join(workdir, f"{stem}.json")
            if os.path.isfile(json_candidate):
                try:
                    with open(json_candidate, "r", encoding="utf-8") as jf:
                        jdata = json.load(jf)
                        cand = jdata.get("phone") or jdata.get("phone_number")
                        if cand and any(c.isdigit() for c in str(cand)):
                            resolved_phone = str(cand)
                except Exception:
                    pass
            
            # Check filename digits
            if not resolved_phone:
                m = re.search(r'(?:^|[^0-9])(\d{7,15})(?:[^0-9]|$)', stem)
                if m and m.group(1) not in ('42777', '777000', '178220800'):
                    resolved_phone = m.group(1)
            
            # Check SQLite database entities/peers
            if not resolved_phone:
                try:
                    db_phone, _, _, _ = extract_session_phone_and_id(f)
                    if db_phone:
                        resolved_phone = db_phone
                except Exception:
                    pass
            
            # Step 2: Determine country from resolved phone
            if resolved_phone:
                country = get_country_from_phone(resolved_phone)
            else:
                country = "Unknown"
                
            groups.setdefault(country, []).append(f)
            
        meta = load_meta()
        pack_name = meta["zips"].get(str(zip_id), {}).get("name", f"Pack {zip_id}")
        
        created_zips = []
        for cname, gfiles in sorted(groups.items()):
            if gfiles:
                new_id = meta["next_zip_id"]
                meta["next_zip_id"] += 1
                new_zip_name = f"{pack_name}_{cname}"
                new_sessions_dir = os.path.join(PACKS_DIR, f"zip_{new_id}", "sessions")
                os.makedirs(new_sessions_dir, exist_ok=True)
                for src in gfiles:
                    src_stem = os.path.splitext(os.path.basename(src))[0]
                    shutil.copy2(src, os.path.join(new_sessions_dir, os.path.basename(src)))
                    # Also copy paired JSON if exists to preserve authentic device parameters
                    src_dir = os.path.dirname(src)
                    paired_json = os.path.join(src_dir, f"{src_stem}.json")
                    if os.path.isfile(paired_json):
                        shutil.copy2(paired_json, os.path.join(new_sessions_dir, f"{src_stem}.json"))
                meta["zips"][str(new_id)] = {
                    "name": new_zip_name,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "owner_id": call.from_user.id,
                }
                created_zips.append((new_id, new_zip_name, len(gfiles)))
                
        save_meta(meta)
        
        if created_zips:
            lines = [f"✅ `{n}` session(s): **{name}** (Pack #{nid})" for nid, name, n in created_zips]
            report = f"🌍 **Country Split Complete!** (Pack #{zip_id})\n\n" + "\n".join(lines)
            if len(report) > 4000:
                report = report[:4000] + "\n... (truncated)"
            await safe_edit(status_msg, report)
            
            for cname, gfiles in sorted(groups.items()):
                if gfiles:
                    zip_path, temp_dir = await build_zip_from_sessions(gfiles, f"{pack_name}_{cname}.zip")
                    try:
                        await app.send_document(call.message.chat.id, zip_path)
                    except Exception:
                        pass
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await safe_edit(status_msg, "❌ No valid sessions to split.")
    except Exception as e:
        await safe_edit(status_msg, f"❌ Country split failed: {e}")
    
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

async def run_split_spam(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
        
    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🚫 Splitting Pack #{zip_id} by Spam Status\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {"clean": [], "spam_blocked": [], "dead_or_other": []}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                try:
                    status = await check_spam_for_session(file_path)
                except Exception:
                    status = "dead_or_other"
                if status == "clean":
                    groups["clean"].append(file_path)
                elif status == "spam_blocked":
                    groups["spam_blocked"].append(file_path)
                else:
                    groups["dead_or_other"].append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))
    
    try:
        meta = load_meta()
        pack_name = meta["zips"].get(str(zip_id), {}).get("name", f"Pack {zip_id}")
        
        created_zips = []
        for gname, gfiles in groups.items():
            if gfiles:
                new_id = meta["next_zip_id"]
                meta["next_zip_id"] += 1
                new_zip_name = f"{pack_name}_{gname}"
                new_sessions_dir = os.path.join(PACKS_DIR, f"zip_{new_id}", "sessions")
                os.makedirs(new_sessions_dir, exist_ok=True)
                for src in gfiles:
                    shutil.copy2(src, os.path.join(new_sessions_dir, os.path.basename(src)))
                meta["zips"][str(new_id)] = {
                    "name": new_zip_name,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "owner_id": call.from_user.id,
                }
                created_zips.append((new_id, new_zip_name, len(gfiles)))
                
        save_meta(meta)
        
        if created_zips:
            lines = [f"✅ `{n}` sessions: **{name}** (Pack #{nid})" for nid, name, n in created_zips]
            report = "🚫 **Spam Split Complete!**\n\n" + "\n".join(lines)
            if len(report) > 4000:
                report = report[:4000] + "\n... (truncated)"
            await app.send_message(call.message.chat.id, report)
            
            for gname, gfiles in groups.items():
                if gfiles:
                    zip_path, temp_dir = await build_zip_from_sessions(gfiles, f"{pack_name}_{gname}.zip")
                    try:
                        await app.send_document(call.message.chat.id, zip_path)
                    except Exception:
                        pass
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await app.send_message(call.message.chat.id, "❌ No valid sessions to split.")
    except Exception as e:
        await app.send_message(call.message.chat.id, f"❌ Spam split failed: {e}")
    
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

async def send_split_zips(chat_id, zip_id, counts):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(chat_id, "📭 ❌ No sessions found.")
    cursor = 0
    for count in counts:
        if cursor >= len(files):
            break
        chunk = files[cursor: cursor + count]
        cursor += count
        zip_path, temp_dir = await build_zip_from_sessions(chunk, f"{len(chunk)}.zip")
        try:
            await app.send_document(chat_id, zip_path, caption=format_result_caption("✂️ Divide Pack", len(chunk), "🧩"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    if cursor < len(files):
        remaining = files[cursor:]
        zip_path, temp_dir = await build_zip_from_sessions(remaining, f"{len(remaining)}.zip")
        try:
            await app.send_document(chat_id, zip_path, caption=format_result_caption("✂️ Divide Pack", len(remaining), "🧩"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def send_merged_zip(chat_id, zip_id, extra_session_files):
    base_files = get_session_files(zip_id)
    merged_files = list(base_files) + list(extra_session_files)
    if not merged_files:
        return await app.send_message(chat_id, "📭 ❌ No sessions found.")
    zip_path, temp_dir = await build_zip_from_sessions(merged_files, f"merged_{len(merged_files)}.zip")
    try:
        await app.send_document(chat_id, zip_path, caption=format_result_caption("📎 Combined Packs", len(merged_files), "🧷"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def show_pack_info(call, zip_id):
    """Show detailed statistics about a pack."""
    files = get_session_files(zip_id)
    record = zip_record(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    _, _, counts = summarize_zip_formats(files)
    total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    created = record.get("created_at", "Unknown") if record else "Unknown"
    original_name = record.get("original_name", "Unknown") if record else "Unknown"

    lines = [
        f"📊 **Pack #{zip_id} — Detailed Info**",
        "",
        f"📁 Original Name: `{original_name}`",
        f"📅 Uploaded: `{created}`",
        f"💾 Total Size: `{human_size(total_size)}`",
        f"📦 Total Sessions: `{len(files)}`",
        "",
        "**Format Breakdown:**",
        f"  🔹 Pyrogram: `{counts['pyrogram']}`",
        f"  🔹 Telethon: `{counts['telethon']}`",
        f"  🔹 Unknown: `{counts['unknown']}`",
    ]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Return", callback_data=f"zip|open|{zip_id}")]]
    )
    await safe_edit(call.message, "\n".join(lines), reply_markup=keyboard)


async def show_download_menu(call, zip_id):
    """Show options to download pack formatted specifically for Telethon, Pyrogram, or Universal bundle."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    current_format, _, counts = summarize_zip_formats(files)
    count = len(files)

    text = (
        f"📥 **Download & Export — Pack #{zip_id}**\n\n"
        f"📊 Current Internal Format: `{format_name(current_format).upper()}`\n"
        f"📱 Total Accounts: `{count}` session(s)\n\n"
        f"Choose an export format optimized for external bots:\n\n"
        f"• 🌐 **Universal Pack (ZIP)**: Complete bundle containing **BOTH** Telethon & Pyrogram `.session` files, `.json` configs & strings. *Guaranteed to work in any bot!*\n"
        f"• 📲 **Telethon (.session + .json ZIP)**: Pristine SQLite schema for Telethon bots (AccStore, Prime, etc.)\n"
        f"• 📲 **Pyrogram (.session + .json ZIP)**: Pristine SQLite schema for Pyrogram, Kurigram & Hydrogram bots\n"
        f"• 📄 **Raw .session File**: Sends uncompressed `.session` directly as a Telegram document so you can forward it to any bot without extracting!\n\n"
        f"✨ *All exports include pristine spec-compliant SQLite databases + full device configs.*"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Universal Pack (Telethon + Pyrogram ZIP)", callback_data=f"zip|dl_format|{zip_id}|universal"),
        ],
        [
            InlineKeyboardButton("📲 Telethon (.session + .json)", callback_data=f"zip|dl_format|{zip_id}|telethon"),
            InlineKeyboardButton("📲 Pyrogram (.session + .json)", callback_data=f"zip|dl_format|{zip_id}|pyrogram"),
        ],
        [
            InlineKeyboardButton("📄 Send Raw Telethon .session", callback_data=f"zip|dl_raw|{zip_id}|telethon"),
            InlineKeyboardButton("📄 Send Raw Pyrogram .session", callback_data=f"zip|dl_raw|{zip_id}|pyrogram"),
        ],
        [
            InlineKeyboardButton(f"⚡ Direct Download ({format_name(current_format).upper()})", callback_data=f"zip|dl_format|{zip_id}|auto"),
        ],
        [
            InlineKeyboardButton("🔙 Return to Pack Menu", callback_data=f"zip|open|{zip_id}")
        ]
    ])
    await safe_edit(call.message, text, reply_markup=keyboard)


async def download_raw_sessions(call, zip_id, target_format="telethon"):
    """Export and send raw uncompressed .session files directly to chat so they can be forwarded to other bots."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    fmt_label = target_format.capitalize()
    await call.answer(f"Preparing {fmt_label} .session file(s)...")
    await safe_edit(call.message, f"📤 Exporting pristine {fmt_label} `.session` file(s)...\nPlease wait.")

    temp_dir = tempfile.mkdtemp(prefix="acs_raw_")
    try:
        max_send = min(len(files), 10)
        used_names = set()
        for idx in range(max_send):
            session_path = files[idx]
            arcname, phone, user_id = await session_export_name(session_path, used_names)
            clean_path = os.path.join(temp_dir, arcname)
            _, dc_id, auth_key, out_phone, out_uid = export_clean_session_db(session_path, clean_path, target_format=target_format, phone=phone, user_id=user_id)
            phone = out_phone or phone
            user_id = out_uid or user_id
            
            stem = os.path.splitext(arcname)[0]
            clean_phone = f"+{phone}" if phone and not str(phone).startswith("+") else (phone or stem)
            phone_digits = re.sub(r"\D", "", clean_phone)
            uid_val = user_id or (int(phone_digits) if phone_digits else 0)

            tele_str = make_offline_telethon_string_session(dc_id, auth_key) if auth_key and len(auth_key) == 256 else None
            pyro_str = make_offline_pyrogram_string_session(dc_id, auth_key, user_id=uid_val) if auth_key and len(auth_key) == 256 else None
            hex_str = f"{dc_id}:{auth_key.hex()}" if auth_key and len(auth_key) == 256 else None

            caption = (
                f"📄 **{fmt_label} .session File** (`{idx+1}/{max_send}`)\n"
                f"📞 Phone: `{clean_phone}`\n"
                f"✨ Spec-compliant SQLite format ready for external {fmt_label} bots"
            )
            await app.send_document(
                call.message.chat.id,
                clean_path,
                caption=caption
            )

            # If sending 1 or 2 files, also send the accompanying .json for full metadata
            if max_send <= 2:
                meta_content = {
                    "session_file": stem,
                    "phone": clean_phone,
                    "phone_number": clean_phone,
                    "phone_digits": phone_digits,
                    "register_time": int(time.time()),
                    "app_id": API_ID,
                    "api_id": API_ID,
                    "app_hash": API_HASH,
                    "api_hash": API_HASH,
                    "device": DEVICE_MODEL or SAFE_DEVICE_MODEL,
                    "device_model": DEVICE_MODEL or SAFE_DEVICE_MODEL,
                    "sdk": SYSTEM_VERSION or SAFE_SYSTEM_VERSION,
                    "system_version": SYSTEM_VERSION or SAFE_SYSTEM_VERSION,
                    "app_version": APP_VERSION or SAFE_APP_VERSION,
                    "lang_code": SAFE_LANG_CODE,
                    "system_lang_code": SAFE_SYSTEM_LANG_CODE,
                    "user_id": uid_val,
                    "id": uid_val,
                    "dc_id": dc_id,
                    "format": target_format.upper(),
                    "telethon_string": tele_str or "",
                    "pyrogram_string": pyro_str or "",
                    "hex_auth_key": hex_str or ""
                }
                json_path = os.path.join(temp_dir, f"{stem}.json")
                try:
                    with open(json_path, "w", encoding="utf-8") as jf:
                        json.dump(meta_content, jf, indent=2)
                    await app.send_document(
                        call.message.chat.id,
                        json_path,
                        caption=f"⚙️ **{fmt_label} Config Metadata** (`{stem}.json`)"
                    )
                except Exception:
                    pass

        if len(files) > 10:
            await app.send_message(
                call.message.chat.id,
                f"ℹ️ Sent first 10 session files directly to avoid Telegram flood limits. For all `{len(files)}` accounts, please download the Universal or {fmt_label} ZIP package."
            )
    except Exception as exc:
        logging.exception(f"[download_raw_sessions] Failed: {exc}")
        await app.send_message(call.message.chat.id, f"❌ Failed to export raw session file: `{exc}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def download_pack(call, zip_id, target_format="universal"):
    """Re-package and send the current pack as a universal, 100% compatible zip."""
    if not target_format or target_format == "auto":
        target_format = "universal"
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)
    record = zip_record(zip_id)
    name = sanitize_filename(record.get("original_name", f"pack_{zip_id}")) if record else f"pack_{zip_id}"
    
    if target_format == "universal":
        fmt_suffix = "_Universal"
        fmt_title = "UNIVERSAL (Telethon + Pyrogram)"
    elif target_format in ("telethon", "pyrogram"):
        fmt_suffix = f"_{target_format.title()}"
        fmt_title = target_format.upper()
    else:
        fmt_suffix = "_Universal"
        fmt_title = "UNIVERSAL"
        target_format = "universal"

    file_name = f"{name}{fmt_suffix}_{len(files)}.zip"
    await safe_edit(call.message, f"📥 Packaging Pack #{zip_id} [{fmt_title}]...\nPlease wait.")
    
    zip_path, temp_dir = await build_zip_from_sessions(files, file_name, target_format=target_format)
    try:
        if target_format == "universal":
            caption = (
                f"📥 **Pack #{zip_id}** [UNIVERSAL BUNDLE]\n"
                f"✅ Accounts: `{len(files)}`\n"
                f"🌐 Includes BOTH Telethon & Pyrogram formats + .json configs + session strings\n"
                f"✨ 100% compatible with ANY external Telegram bot or script"
            )
        elif target_format == "telethon":
            caption = (
                f"📥 **Pack #{zip_id}** [TELETHON]\n"
                f"✅ Accounts: `{len(files)}`\n"
                f"📦 Clean SQLite `.session` + matching `.json` configs\n"
                f"✨ 100% compatible with external Telethon bots (AccStore, Prime, etc.)"
            )
        elif target_format == "pyrogram":
            caption = (
                f"📥 **Pack #{zip_id}** [PYROGRAM]\n"
                f"✅ Accounts: `{len(files)}`\n"
                f"📦 Clean SQLite `.session` + matching `.json` configs\n"
                f"✨ 100% compatible with external Pyrogram / Kurigram / Hydrogram bots"
            )
        else:
            caption = (
                f"📥 **Pack #{zip_id}** [{fmt_title}]\n"
                f"✅ Accounts: `{len(files)}`\n"
                f"📦 Clean SQLite `.session` + matching `.json` configs\n"
                f"✨ 100% compatible with external session manager bots"
            )
        await app.send_document(
            call.message.chat.id,
            zip_path,
            caption=caption,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_convert_to_pyro(call, zip_id):
    """Convert Telethon sessions to Pyrogram format."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    current_format, _, counts = summarize_zip_formats(files)
    if current_format == "pyrogram" and counts.get("telethon", 0) == 0:
        return await call.answer("Pack is already 100% Pyrogram format.", show_alert=True)

    converted_format = "pyrogram"
    temp_dir = tempfile.mkdtemp(prefix="acs_conv_pyro_")
    groups = {"converted": [], "failed": []}
    total = len(files)

    progress = ThrottledProgress(
        call.message,
        f"📦 Pack #{zip_id} [{format_name(current_format).upper()}]\n\n🔄 Convert to {format_name(converted_format).upper()}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                try:
                    stype = detect_session_type(file_path)
                    if stype == "telethon":
                        converted, err_msg = await convert_telethon_to_pyro_file(file_path, temp_dir)
                        if err_msg:
                            with open("conversion_debug.log", "a") as f:
                                f.write(f"[{os.path.basename(file_path)}] {err_msg}\n")
                    elif stype == "pyrogram":
                        target = os.path.join(temp_dir, os.path.basename(file_path))
                        shutil.copy2(file_path, target)
                        converted = target
                    else:
                        converted, err_msg = await convert_telethon_to_pyro_file(file_path, temp_dir)
                    if converted:
                        groups["converted"].append(converted)
                    else:
                        groups["failed"].append(file_path)
                except Exception as exc:
                    logging.warning(f"[CONV] Worker exception for {os.path.basename(file_path)}: {exc}")
                    with open("conversion_debug.log", "a") as f:
                        import traceback
                        f.write(f"[{os.path.basename(file_path)}] Exception: {exc}\n{traceback.format_exc()}\n")
                    groups["failed"].append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    try:
        await gather_chunked((worker(f) for f in files))

        detail_lines = [
            f"📦 Pack #{zip_id} 🔄 Format Details",
            "",
            f"🔹 From: `{format_name(current_format).upper()}`",
            f"🔹 To: `{format_name(converted_format).upper()}`",
            "",
            f"✅ Converted: `{len(groups['converted'])}`",
            f"❌ Failed: `{len(groups['failed'])}`",
        ]
        await app.send_message(call.message.chat.id, "\n".join(detail_lines))

        labels = {
            "converted": {"label": "Converted", "icon": "🔄"},
            "failed": {"label": "Failed", "icon": "❌"},
        }
        await send_classified_zips(call.message.chat.id, groups, labels)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


# ─────────────────────────────────────────────────────────────────────────────
# New Feature Functions
# Account profiles, DC info, bio/name changing, string session export.
# ─────────────────────────────────────────────────────────────────────────────


async def get_account_profile(session_path):
    """Get detailed profile info for a session."""
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return {
                        "name": f"🤖 {me.first_name or 'Bot'}",
                        "username": me.username or "None",
                        "phone": "Bot",
                        "user_id": me.id,
                        "dc_id": "—",
                        "premium": False,
                        "bio": "Bot account",
                        "photos": 0,
                        "restricted": False,
                        "verified": getattr(me, "is_verified", False) or False,
                        "status": "active",
                    }
                full = await client.invoke(functions.users.GetFullUser(id=await client.resolve_peer(me.id)))
                about = getattr(full, "full_user", None)
                bio = getattr(about, "about", "") or "" if about else ""
                photos = await client.get_chat_photos_count(me.id)
                dc_id = getattr(me, "dc_id", "Unknown") or "Unknown"
                return {
                    "name": f"{me.first_name} {me.last_name or ''}".strip(),
                    "username": me.username or "None",
                    "phone": me.phone_number or "Unknown",
                    "user_id": me.id,
                    "dc_id": dc_id,
                    "premium": getattr(me, "is_premium", False) or False,
                    "bio": bio,
                    "photos": photos,
                    "restricted": getattr(me, "is_restricted", False) or False,
                    "verified": getattr(me, "is_verified", False) or False,
                    "status": "active",
                }
        except INVALID_EXCEPTIONS:
            return {"status": "inactive"}
        except Exception:
            return {"status": "error"}

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return {"status": "inactive"}
            me = await client.get_me()
            if getattr(me, "bot", False):
                return {
                    "name": f"🤖 {me.first_name or 'Bot'}",
                    "username": me.username or "None",
                    "phone": "Bot",
                    "user_id": me.id,
                    "dc_id": "—",
                    "premium": False,
                    "bio": "Bot account",
                    "photos": 0,
                    "restricted": False,
                    "verified": getattr(me, "verified", False) or False,
                    "status": "active",
                }
            bio = ""
            try:
                full = await client(TeleGetFullUserRequest(id=await client.get_input_entity(me)))
                about_obj = getattr(full, "full_user", None)
                bio = getattr(about_obj, "about", "") or "" if about_obj else ""
            except Exception:
                pass
            dc_id = getattr(me, "photo", None)
            dc = getattr(dc_id, "dc_id", "Unknown") if dc_id else "Unknown"
            return {
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username or "None",
                "phone": me.phone or "Unknown",
                "user_id": me.id,
                "dc_id": dc,
                "premium": getattr(me, "premium", False) or False,
                "bio": bio,
                "photos": 0,
                "restricted": getattr(me, "restricted", False) or False,
                "verified": getattr(me, "verified", False) or False,
                "status": "active",
            }
        except INVALID_EXCEPTIONS:
            return {"status": "inactive"}
        except Exception:
            return {"status": "error"}
        finally:
            await safe_telethon_disconnect(client)

    return {"status": "error"}


async def run_profiles(call, zip_id):
    """Show account profiles for all sessions in a pack."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"👤 Loading Pack #{zip_id} Profiles\n\n{{progress}}",
        total
    )
    await progress.update(0)

    results = []
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                profile = await get_account_profile(file_path)
                results.append(profile)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    active = [p for p in results if p.get("status") == "active"]
    inactive = [p for p in results if p.get("status") != "active"]

    if active:
        lines = [f"👤 **Pack #{zip_id} — Account Profiles**\n"]
        for i, p in enumerate(active, 1):
            premium_badge = " ⭐" if p.get("premium") else ""
            verified_badge = " ☑️" if p.get("verified") else ""
            lines.append(
                f"**{i}.** {p['name']}{premium_badge}{verified_badge}\n"
                f"  ├ Phone: `+{p['phone']}`\n"
                f"  ├ Username: @{p['username']}\n"
                f"  ├ User ID: `{p['user_id']}`\n"
                f"  ├ DC: `{p['dc_id']}`\n"
                f"  ├ Bio: {p['bio'][:50] + '...' if len(p.get('bio', '')) > 50 else (p.get('bio') or 'None')}\n"
                f"  ├ Photos: `{p.get('photos', 0)}`\n"
                f"  └ Restricted: {'🚫 Yes' if p.get('restricted') else '✅ No'}"
            )
        # Telegram has 4096 char limit, split if needed
        full_text = "\n".join(lines)
        if len(full_text) > 4000:
            await app.send_message(call.message.chat.id, full_text[:4000] + "\n...")
        else:
            await app.send_message(call.message.chat.id, full_text)

    summary = f"👤 Pack #{zip_id} • `{len(active)}` active, `{len(inactive)}` dead"
    await safe_edit(call.message, summary + "\n\n" + zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def run_dc_info(call, zip_id):
    """Show Data Center info for all sessions."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"🌐 Checking Pack #{zip_id} DCs\n\n{{progress}}",
        total
    )
    await progress.update(0)

    dc_counts = {}
    results = []
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                session_type = detect_session_type(file_path)
                session_name = os.path.basename(file_path).replace(".session", "")
                workdir = os.path.dirname(file_path)
                dc_id = "Unknown"
                phone = session_name
                conn = None
                try:
                    if session_type == "pyrogram" or session_type == "telethon":
                        conn = sqlite3.connect(file_path)
                        cur = conn.cursor()
                        cur.execute("SELECT dc_id FROM sessions")
                        row = cur.fetchone()
                        if row:
                            dc_id = str(row[0])
                except Exception:
                    pass
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
                dc_counts[dc_id] = dc_counts.get(dc_id, 0) + 1
                results.append({"phone": phone, "dc": dc_id})
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    dc_names = {
        "1": "DC1 — Miami, US",
        "2": "DC2 — Amsterdam, NL",
        "3": "DC3 — Miami, US",
        "4": "DC4 — Amsterdam, NL",
        "5": "DC5 — Singapore, SG",
    }

    lines = [f"🌐 **Pack #{zip_id} — Data Center Report**\n"]
    lines.append("**Distribution:**")
    for dc, count in sorted(dc_counts.items()):
        dc_label = dc_names.get(dc, f"DC{dc}")
        lines.append(f"  🔹 {dc_label}: `{count}` account(s)")

    lines.append(f"\n**Details:**")
    for r in results[:30]:
        dc_label = dc_names.get(r["dc"], f"DC{r['dc']}")
        lines.append(f"  • `+{r['phone']}` → {dc_label}")
    if len(results) > 30:
        lines.append(f"  ... and `{len(results) - 30}` more")

    await app.send_message(call.message.chat.id, "\n".join(lines))
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def change_bio_for_session(session_path, new_bio):
    """Change bio safely without redundant double invocations or profile polling."""
    new_bio = (new_bio or "").strip()[:70]
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                await client.invoke(functions.account.UpdateProfile(about=new_bio))
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            if getattr(e, "value", 0) <= 15:
                await asyncio.sleep(e.value)
                try:
                    await client.invoke(functions.account.UpdateProfile(about=new_bio))
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change bio failed for pyrogram {session_name}: {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj, workdir=workdir, session_name=session_name)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            await client(TeleUpdateProfileRequest(about=new_bio))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            if getattr(e, "seconds", 0) <= 15:
                await asyncio.sleep(e.seconds)
                try:
                    await client(TeleUpdateProfileRequest(about=new_bio))
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change bio failed for telethon {session_name}: {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_change_bio(source_message, zip_id, new_bio):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"📝 Changing Bio — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(min(2, MAX_CONCURRENCY))

    async def worker(file_path, index):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                status = await change_bio_for_session(file_path, new_bio)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f, i) for i, f in enumerate(files)))

    labels = {
        "success": {"label": "Bio Changed", "icon": "✅"},
        "inactive": {"label": "Dead", "icon": "❌"},
        "unsupported": {"label": "Not Supported", "icon": "⚠️"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "failed": {"label": "Failed", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"📝 **Bio Change Results**\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def change_name_for_session(session_path, first_name, last_name):
    """Change name safely with sanitized inputs and anti-freeze safeguards."""
    first_name = (first_name or "").strip()[:64]
    if not first_name:
        first_name = "User"
    last_name = (last_name or "").strip()[:64] if last_name else ""

    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                await client.invoke(functions.account.UpdateProfile(
                    first_name=first_name,
                    last_name=last_name or "",
                ))
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            if getattr(e, "value", 0) <= 15:
                await asyncio.sleep(e.value)
                try:
                    await client.invoke(functions.account.UpdateProfile(
                        first_name=first_name,
                        last_name=last_name or "",
                    ))
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change name failed for pyrogram session {session_name}: {type(e).__name__} {e}")
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj, workdir=workdir, session_name=session_name)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            await client(TeleUpdateProfileRequest(
                first_name=first_name,
                last_name=last_name or "",
            ))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            if getattr(e, "seconds", 0) <= 15:
                await asyncio.sleep(e.seconds)
                try:
                    await client(TeleUpdateProfileRequest(
                        first_name=first_name,
                        last_name=last_name or "",
                    ))
                    return "success"
                except Exception:
                    pass
            return "rate_limited"
        except Exception as e:
            logging.error(f"Change name failed for telethon session {session_name}: {type(e).__name__} {e}")
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"


async def run_change_name(source_message, zip_id, first_name, last_name):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"🏷 Changing Name — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(min(2, MAX_CONCURRENCY))

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                status = await change_name_for_session(file_path, first_name, last_name)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Name Changed", "icon": "✅"},
        "inactive": {"label": "Dead", "icon": "❌"},
        "unsupported": {"label": "Not Supported", "icon": "⚠️"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session", "icon": "🤖"},
        "failed": {"label": "Failed", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    display_name = f"{first_name} {last_name}".strip()
    await app.send_message(source_message.chat.id, f"🏷 **Name Change Results** → `{display_name}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


async def show_export_menu(call, zip_id):
    meta = load_meta()
    zip_meta = meta.get("zips", {}).get(str(zip_id), {})
    is_direct = zip_meta.get("is_direct", False)
    
    label = "Session" if is_direct else "Pack"
    text = f"📋 **Export Options** — {label} #{zip_id}\nChoose the format for exporting your active session(s):"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Export as String", callback_data=f"zip|export_strings|{zip_id}")],
        [InlineKeyboardButton("📋 Export as Hex", callback_data=f"zip|export_hex|{zip_id}")],
        [InlineKeyboardButton("📋 Export Both (String & Hex)", callback_data=f"zip|export_both|{zip_id}")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data=f"zip|open|{zip_id}")]
    ])
    await safe_edit(call.message, text, reply_markup=keyboard)


async def export_sessions_format(call, zip_id, export_type):
    """Export sessions as string, hex, or both."""
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress_label = "String & Hex" if export_type == "both" else export_type.capitalize()
    progress = ThrottledProgress(
        call.message,
        f"📋 Exporting Pack #{zip_id} ({progress_label} Format)\n\n{{progress}}",
        total
    )
    await progress.update(0)

    results = []
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        async with sem:
            session_type = detect_session_type(file_path)
            session_name = os.path.basename(file_path).replace(".session", "")
            workdir = os.path.dirname(file_path)
            
            phone = session_name
            if phone.startswith("+"):
                phone = phone[1:]
                
            await progress.update(0, f"Exporting +{phone}...")
            
            # Read hex format (extract dc_id and auth_key from SQLite database)
            conn = None
            try:
                conn = sqlite3.connect(file_path)
                cur = conn.cursor()
                cur.execute("SELECT dc_id, auth_key FROM sessions")
                row = cur.fetchone()
                if row:
                    dc_id, auth_key = row
                    if auth_key and len(auth_key) == 256:
                        hex_str = f"{dc_id}:{auth_key.hex()}"
            except Exception:
                pass
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            string_str = None
            authorized = False

            # Connect to client if string or both is needed, or just to get the authorized phone number
            try:
                if session_type == "pyrogram":
                    async with make_pyro_client(session_name, workdir=workdir) as client:
                        me = await client.get_me()
                        if me:
                            phone = me.phone_number or phone
                            string_str = await client.export_session_string()
                            authorized = True
                elif session_type == "telethon":
                    session_obj = safe_sqlite_session(file_path)
                    client = make_tele_client(session_obj)
                    try:
                        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                        if await client.is_user_authorized():
                            me = await client.get_me()
                            if me:
                                phone = me.phone or phone
                                string_str = StringSession.save(client.session)
                                authorized = True
                    finally:
                        await safe_telethon_disconnect(client)
            except Exception:
                pass

            results.append({
                "phone": phone,
                "type": session_type.upper(),
                "string": string_str,
                "hex": hex_str,
                "authorized": authorized
            })
            await progress.update(1, f"Exported +{phone}")

    await gather_chunked((worker(f) for f in files))

    if not results:
        await app.send_message(call.message.chat.id, "❌ No sessions could be processed.")
    else:
        temp_dir = tempfile.mkdtemp(prefix="acs_export_")
        txt_path = os.path.join(temp_dir, f"export_sessions_{zip_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"# {PLATFORM_NAME} — Session Export ({export_type.upper()})\n")
                f.write(f"# Pack #{zip_id} — {len(results)} session(s)\n")
                f.write(f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
                for r in results:
                    f.write(f"# Phone: +{r['phone']} | Format: {r['type']}\n")
                    if export_type == "string" or export_type == "both":
                        if r["string"]:
                            if export_type == "both":
                                f.write(f"# String Session:\n{r['string']}\n")
                            else:
                                f.write(f"{r['string']}\n")
                        else:
                            f.write("# [String Session Export Failed: Session Dead/Unauthorized]\n")
                    if export_type == "hex" or export_type == "both":
                        if r["hex"]:
                            if export_type == "both":
                                f.write(f"# Hex Session:\n{r['hex']}\n")
                            else:
                                f.write(f"{r['hex']}\n")
                        else:
                            f.write("# [Hex Session Export Failed]\n")
                    f.write("\n")

            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption=f"📋 **Session Export** — Pack #{zip_id}\nFormat: `{export_type.upper()}`\n`{len(results)}` session(s) processed",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))



# ─────────────────────────────────────────────────────────────────────────────
# Session Logout by String / Hex Key
# Allows users to log out a session or revoke other devices by providing
# a Telethon string session, Pyrogram string session, or raw hex auth key.
# ─────────────────────────────────────────────────────────────────────────────

LOGOUT_STATE = {}  # Tracks pending logout sessions: user_id → session info


async def _connect_from_auth_key(auth_key_bytes, dc_id):
    """Create and connect a TelethonClient from raw auth key bytes."""
    async def try_dc(d_id):
        session = StringSession()
        ip, port = DC_TABLE.get(d_id, DC_TABLE[2])
        session.set_dc(d_id, ip, port)
        session.auth_key = AuthKey(data=auth_key_bytes)
        client = make_tele_client(
            session,
            connection_retries=2,
            auto_reconnect=False,
            flood_sleep_threshold=0,
            timeout=10
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            if not await client.is_user_authorized():
                raise ValueError(f"Not authorized on DC {d_id}")
            return client
        except asyncio.CancelledError:
            await safe_telethon_disconnect(client)
            raise
        except Exception as e:
            await safe_telethon_disconnect(client)
            raise e

    # Try the suggested DC first
    try:
        return await try_dc(dc_id)
    except Exception as first_exc:
        last_exc = first_exc
        # If it's a known account-level error, don't bother with other DCs
        if "Deactivated" in type(first_exc).__name__ or "Ban" in type(first_exc).__name__:
            raise first_exc
            
        # Otherwise, try the rest concurrently
        dcs_to_try = [d for d in DC_TABLE.keys() if d != dc_id]
        pending = [asyncio.create_task(try_dc(d_id)) for d_id in dcs_to_try]
        
        priority_exc = None
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    client = task.result()
                    for p in pending:
                        p.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    return client
                except Exception as e:
                    last_exc = e
                    # Prioritize definitive authentication errors over generic timeouts/connection drops
                    if "Not authorized" in str(e) or "AuthKeyUnregistered" in type(e).__name__ or "Deactivated" in type(e).__name__:
                        priority_exc = e

        raise priority_exc or last_exc or asyncio.TimeoutError("Failed to connect to any DC")


async def _run_logout_self(auth_key, dc_id, status_msg):
    """Log out the session itself (terminate this auth key)."""
    client = None
    try:
        client = await _connect_from_auth_key(auth_key, dc_id)
        dc_id = client.session.dc_id
        if not await client.is_user_authorized():
            return "❌ Session is not authorized (already dead or invalid)."
        me = await client.get_me()
        phone = getattr(me, "phone", "Unknown")
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
        await client.log_out()
        return (
            f"✅ **Session Logged Out**\n\n"
            f"👤 `{name}`\n"
            f"📞 `+{phone}`\n"
            f"🏢 DC `{dc_id}`\n\n"
            f"This session has been permanently terminated."
        )
    except INVALID_EXCEPTIONS as exc:
        return f"⚠️ Session already dead: `{type(exc).__name__}`"
    except asyncio.TimeoutError:
        return "❌ Connection timed out. Telegram servers might be slow."
    except (ConnectionError, OSError):
        return "❌ Server closed the connection — session is likely dead."
    except Exception as exc:
        return f"❌ Logout failed: `{exc}`"
    finally:
        await safe_telethon_disconnect(client)


async def _run_revoke_others(auth_key, dc_id, status_msg):
    """Revoke all OTHER devices/sessions on the account (keeps current one alive)."""
    client = None
    try:
        client = await _connect_from_auth_key(auth_key, dc_id)
        dc_id = client.session.dc_id
        if not await client.is_user_authorized():
            return "❌ Session is not authorized (already dead or invalid)."
        me = await client.get_me()
        if getattr(me, "bot", False):
            return "🤖 This is a bot session — bots don't have devices to revoke."
        phone = getattr(me, "phone", "Unknown")
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"

        # Primary: Use Telegram MTProto atomic ResetAuthorizationsRequest
        from telethon.tl.functions.auth import ResetAuthorizationsRequest as TeleAuthReset
        try:
            await client(TeleAuthReset())
            return (
                f"✅ **Other Devices Revoked**\n\n"
                f"👤 `{name}` (`+{phone}`)\n"
                f"🏢 DC `{dc_id}`\n\n"
                f"All other active sessions have been terminated successfully."
            )
        except Exception as auth_err:
            if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in str(auth_err).upper():
                return (
                    f"⚠️ **Telegram 24-Hour Security Rule**\n\n"
                    f"👤 `{name}` (`+{phone}`)\n\n"
                    f"Telegram does not allow terminating other sessions because this session was logged in less than 24 hours ago. Please try again after 24 hours."
                )

        # Fallback to individual reset with strict hash != 0 check
        auths = await client(GetAuthorizationsRequest())
        other_sessions = [a for a in auths.authorizations if not (getattr(a, "current", False) or getattr(a, "hash", 0) == 0)]

        if not other_sessions:
            return (
                f"ℹ️ **No other devices found**\n\n"
                f"👤 `{name}` (`+{phone}`)\n"
                f"This is the only active session."
            )

        revoked = 0
        failed = 0
        for auth in other_sessions:
            if getattr(auth, "hash", 0) == 0:
                continue
            try:
                await client(ResetAuthorizationRequest(hash=auth.hash))
                revoked += 1
            except Exception:
                failed += 1

        result = (
            f"✅ **Other Devices Revoked**\n\n"
            f"👤 `{name}` (`+{phone}`)\n"
            f"🏢 DC `{dc_id}`\n\n"
            f"🗑 Revoked: `{revoked}`\n"
        )
        if failed:
            result += f"⚠️ Failed: `{failed}`\n"
        result += f"📱 Total found: `{len(other_sessions)}`"
        return result
    except INVALID_EXCEPTIONS as exc:
        return f"⚠️ Session is dead: `{type(exc).__name__}`"
    except asyncio.TimeoutError:
        return "❌ Connection timed out. Telegram servers might be slow."
    except (ConnectionError, OSError):
        return "❌ Server closed the connection — session is likely dead."
    except Exception as exc:
        return f"❌ Revoke failed: `{exc}`"
    finally:
        await safe_telethon_disconnect(client)


def _time_ago(dt):
    if not dt:
        return "Unknown"
    diff = datetime.now(timezone.utc) - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        return f"{int(seconds // 60)} mins ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    else:
        return f"{int(seconds // 86400)} days ago"

async def _render_devices_menu(auth_key, dc_id, input_type, status_msg, otp_text=None):
    """Connect and list all devices for this session, returning a rich UI."""
    client = None
    try:
        client = await _connect_from_auth_key(auth_key, dc_id)
        dc_id = client.session.dc_id
        if not await client.is_user_authorized():
            return await status_msg.edit_text("❌ Session is not authorized (already dead or invalid).")
        
        me = await client.get_me()
        if getattr(me, "bot", False):
            return await status_msg.edit_text("🤖 This is a bot session — bots don't have devices to list.")
        phone = getattr(me, "phone", "Unknown")
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"

        auths = await client(GetAuthorizationsRequest())
        
        keyboard_buttons = []
        device_details = []
        # Add Read OTP and Refresh buttons at the top
        keyboard_buttons.append([
            InlineKeyboardButton("📩 Read OTP", callback_data="slo|otp"),
            InlineKeyboardButton("🔄 Refresh", callback_data="slo|refresh")
        ])
        
        for i, a in enumerate(auths.authorizations, 1):
            device_name = a.device_model or "Unknown Device"
            app_name = getattr(a, "app_name", "") or ""
            app_version = getattr(a, "app_version", "") or ""
            country = getattr(a, "country", "") or getattr(a, "region", "") or "Unknown"
            ip_addr = getattr(a, "ip", "") or "Unknown"
            
            created_str = _time_ago(getattr(a, "date_created", None))
            
            is_curr = getattr(a, "current", False) or getattr(a, "hash", 0) == 0
            status_icon = "🟢 [CURRENT]" if is_curr else "📱"
            title = f"{status_icon} {device_name}"
            
            detail_line = (
                f"**{i}. {title}**\n"
                f"├ App: `{app_name} {app_version}`\n"
                f"├ Location: `{ip_addr}` ({country})\n"
                f"└ Logged In: `{created_str}`"
            )
            device_details.append(detail_line)
            
            # Action: either logout self (if current) or revoke specific hash
            action_data = f"slo|self" if is_curr else f"slo|rev|{a.hash}"
            btn_label = f"Logout: {device_name} (Self)" if is_curr else f"Logout: {device_name}"
            keyboard_buttons.append([InlineKeyboardButton(btn_label, callback_data=action_data)])
            
        other_devices = [a for a in auths.authorizations if not (getattr(a, "current", False) or getattr(a, "hash", 0) == 0)]
        if other_devices:
            keyboard_buttons.append([InlineKeyboardButton("💥 Revoke ALL Other Devices", callback_data="slo|others")])
            
        keyboard_buttons.append([InlineKeyboardButton("❌ Close", callback_data="slo|cancel")])
        
        type_labels = {
            "telethon_string": "Telethon String",
            "pyrogram_string": "Pyrogram String",
            "hex_key": "Hex Auth Key",
        }
        
        text = (
            f"✅ **Session Connected**\n\n"
            f"👤 `{name}` (`+{phone}`)\n"
            f"📝 Type: `{type_labels.get(input_type, input_type)}`\n"
            f"🏢 DC: `{dc_id}`\n\n"
        )
        
        if otp_text:
            text += f"📩 **Latest OTP:**\n`{otp_text}`\n\n"
            
        text += "**Active Devices:**\n" + "\n\n".join(device_details) + "\n\n👇 **Tap a button below to log out a device:**"
        
        return await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard_buttons))
    except INVALID_EXCEPTIONS as exc:
        return await status_msg.edit_text(f"⚠️ Session already dead: `{type(exc).__name__}`")
    except asyncio.TimeoutError:
        return await status_msg.edit_text("❌ Connection timed out. Telegram servers might be slow.")
    except (ConnectionError, OSError):
        return await status_msg.edit_text("❌ Server closed the connection — session is likely dead.")
    except Exception as exc:
        return await status_msg.edit_text(f"❌ Connection failed: `{exc}`")
    finally:
        if client:
            await safe_telethon_disconnect(client)



# ─────────────────────────────────────────────────────────────────────────────

async def render_direct_devices(zip_id):
    """Retrieve authorizations and format the devices menu for direct sessions."""
    session_files = get_session_files(zip_id)
    if not session_files:
        return "❌ Session file not found.", None
    
    session_path = session_files[0]
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    client = None
    try:
        if session_type == "pyrogram":
            client = make_pyro_client(session_name, workdir=workdir)
            await asyncio.wait_for(client.start(), timeout=15)
            me = await asyncio.wait_for(client.get_me(), timeout=15)
            phone = getattr(me, "phone_number", "Unknown")
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
            auths = await asyncio.wait_for(client.invoke(functions.account.GetAuthorizations()), timeout=15)
            authorizations = auths.authorizations
        else:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(
                session_obj,
                connection_retries=2,
                auto_reconnect=False,
                flood_sleep_threshold=0,
                timeout=10
            )
            await asyncio.wait_for(client.connect(), timeout=15)
            if not await client.is_user_authorized():
                return "❌ Session is invalid or revoked.", None
            me = await asyncio.wait_for(client.get_me(), timeout=15)
            phone = getattr(me, "phone", "Unknown")
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
            auths = await asyncio.wait_for(client(GetAuthorizationsRequest()), timeout=15)
            authorizations = auths.authorizations

        # Parse authorizations
        current_device = None
        other_devices = []
        for item in authorizations:
            if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                current_device = item
            else:
                other_devices.append(item)
                
        # Build text message with all logged in information
        text_lines = [
            f"👤 **Account:** `{name}` (`+{phone}`)",
            f"⚡ **Direct Active Devices Status**",
            "────────────────"
        ]
        
        if current_device:
            dev_model = current_device.device_model or "Unknown Device"
            app_name = getattr(current_device, "app_name", "") or ""
            app_version = getattr(current_device, "app_version", "") or ""
            platform = getattr(current_device, "platform", "") or getattr(current_device, "system_version", "") or ""
            ip = getattr(current_device, "ip", "") or ""
            country = getattr(current_device, "country", "") or getattr(current_device, "region", "") or ""
            created = getattr(current_device, "date_created", None) or getattr(current_device, "date_active", None) or 0
            created_str = format_timestamp(created) if created else "Unknown"
            
            text_lines.extend([
                "📱 **Current Device (This Connection)**",
                f"  ├ Device: `{dev_model}`",
                f"  ├ OS: `{platform}`",
                f"  ├ App: `{app_name} {app_version}`",
                f"  ├ IP: `{ip}`",
                f"  ├ Location: `{country}`",
                f"  └ Login Time: `{created_str}`",
                "────────────────"
            ])
            
        if other_devices:
            text_lines.append(f"🔌 **Other Active Session(s) (`{len(other_devices)}`):**")
            for idx, item in enumerate(other_devices, 1):
                dev_model = item.device_model or "Unknown Device"
                platform = getattr(item, "platform", "") or getattr(item, "system_version", "") or ""
                ip = getattr(item, "ip", "") or ""
                country = getattr(item, "country", "") or getattr(item, "region", "") or ""
                login_ts = getattr(item, "date_created", None) or getattr(item, "date_active", None) or 0
                login_str = format_timestamp(login_ts) if login_ts else "Unknown"
                active = getattr(item, "date_active", None) or 0
                active_str = format_timestamp(active) if active else "Unknown"
                
                text_lines.extend([
                    f"**{idx}. {dev_model}**",
                    f"  ├ OS: `{platform}`",
                    f"  ├ IP: `{ip}`",
                    f"  ├ Location: `{country}`",
                    f"  ├ Login Time: `{login_str}`",
                    f"  └ Last Active: `{active_str}`"
                ])
        else:
            text_lines.append("🟢 No other active devices.")
            
        # Build Keyboard
        keyboard_rows = []
        
        # Add a button to terminate each other device
        for item in other_devices:
            if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                continue
            dev_model = item.device_model or "Unknown"
            ip = getattr(item, "ip", "") or "Unknown IP"
            keyboard_rows.append([
                InlineKeyboardButton(
                    f"🗑 Terminate: {dev_model} ({ip})",
                    callback_data=f"term_dev|{zip_id}|{item.hash}"
                )
            ])
            
        # Refresh and Read OTP buttons
        keyboard_rows.append([
            InlineKeyboardButton("🔄 Refresh Devices", callback_data=f"refresh_dev|{zip_id}"),
            InlineKeyboardButton("🔑 Read OTP", callback_data=f"direct_otp|{zip_id}")
        ])
        
        # Terminate Bot Connection (Self Logout) button
        keyboard_rows.append([
            InlineKeyboardButton("💀 Terminate Bot Connection", callback_data=f"term_self|{zip_id}")
        ])
        
        # Revoke ALL Other Devices button
        if other_devices:
            keyboard_rows.append([
                InlineKeyboardButton("💥 Revoke ALL Other Devices", callback_data=f"term_all_dev|{zip_id}")
            ])
        
        # Add Link to the Full Operations Menu
        keyboard_rows.append([
            InlineKeyboardButton("🛠 Full Operations Menu", callback_data=f"zip|open|{zip_id}")
        ])
        
        markup = InlineKeyboardMarkup(keyboard_rows)
        return "\n".join(text_lines), markup

    except INVALID_EXCEPTIONS as exc:
        return f"⚠️ Session is dead or revoked: `{type(exc).__name__}`", None
    except asyncio.TimeoutError:
        return "❌ Connection timed out. Telegram servers might be slow or time out of sync.", None
    except (ConnectionError, OSError) as exc:
        return f"❌ Network error: `{type(exc).__name__}`", None
    except Exception as exc:
        return f"❌ Failed to load active devices: {exc}", None
    finally:
        if client:
            if session_type == "pyrogram":
                await client.stop()
            else:
                await safe_telethon_disconnect(client)



async def _run_bulk_session_import(message, sessions_data):
    total = len(sessions_data)
    status_msg = await message.reply_text(f"⏳ Processing and importing `{total}` sessions...")
    temp_dir = tempfile.mkdtemp(prefix="acs_bulk_")
    session_files = []
    
    try:
        for idx, (auth_key, dc_id, input_type) in enumerate(sessions_data, 1):
            ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
            session_name = f"session_{idx}_{int(time.time())}"
            session_path = os.path.join(temp_dir, session_name)
            session_obj = SQLiteSession(session_path)
            session_obj.set_dc(dc_id, ip, port)
            session_obj.auth_key = AuthKey(data=auth_key)
            session_obj.save()
            session_obj.close()
            session_files.append(session_path + ".session")

        zip_id, count, _ = await register_session_files(
            message.from_user.id, session_files, f"Bulk_Import_{total}_sessions"
        )
        if zip_id:
            await status_msg.edit_text(
                f"✅ **Import Successful!**\n\nRegistered `{count}` sessions into Pack `#{zip_id}`.",
                reply_markup=operations_keyboard(zip_id)
            )
        else:
            await status_msg.edit_text("❌ Failed to register bulk sessions.")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Failed to import sessions: {exc}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

async def _run_session_import(message, auth_key, dc_id, input_type):
    type_labels = {
        "telethon_string": "Telethon String",
        "pyrogram_string": "Pyrogram String",
        "hex_key": "Hex Auth Key",
    }

    status_msg = await message.reply_text(f"⏳ Connecting to `{type_labels.get(input_type, 'Session')}` to verify...")
    
    async def _safe_update(text, reply_markup=None):
        if status_msg:
            try:
                return await status_msg.edit_text(text, reply_markup=reply_markup)
            except Exception:
                pass
        try:
            return await message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            pass

    client = None
    try:
        async with register_user_task(message.from_user.id, status_msg):
            try:
                client = await _connect_from_auth_key(auth_key, dc_id)
                dc_id = client.session.dc_id
            except asyncio.TimeoutError:
                await _safe_update("❌ **Connection timed out (Telegram rejected the key).**\n\nTelegram did not respond. This means the session key is **completely dead, revoked, or unrecognized** by Telegram.")
                return
            except INVALID_EXCEPTIONS as exc:
                await _safe_update(f"❌ Session is dead or revoked: `{type(exc).__name__}`")
                return
            except (ConnectionError, OSError) as exc:
                await _safe_update(f"❌ Network error: `{type(exc).__name__}: {exc}`")
                return
            except Exception as conn_exc:
                err_str = str(conn_exc)
                if "Not authorized" in err_str:
                    await _safe_update(f"❌ **Session Key Rejected:** `{err_str}`\n\nThis session auth key is invalid, revoked, or already logged out from Telegram.")
                else:
                    await _safe_update(f"❌ Connection failed: `{type(conn_exc).__name__}: {conn_exc}`")
                return

            try:
                if not await client.is_user_authorized():
                    await _safe_update("❌ Session is not authorized (already dead or invalid).")
                    return
                
                me = await client.get_me()
            except INVALID_EXCEPTIONS as exc:
                await _safe_update(f"❌ Session is dead or revoked: `{type(exc).__name__}`")
                return
            except asyncio.TimeoutError:
                await _safe_update("❌ Connection timed out. Telegram servers might be slow.")
                return
            except (ConnectionError, OSError):
                await _safe_update("❌ Server closed the connection. The session key is likely dead.")
                return

            if getattr(me, "bot", False):
                await _safe_update("🤖 Bot sessions are not supported. Please use a user session.")
                return
                
            phone = getattr(me, "phone", "Unknown")
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
            
            # Disconnect early before registering — we don't need the connection anymore
            await safe_telethon_disconnect(client)
            client = None
            
            await _safe_update(f"✅ Connection successful! Registering pack...")
            
            temp_dir = tempfile.mkdtemp(prefix="acs_str_")
            ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
            session_name = f"session_{int(time.time())}"
            session_path = os.path.join(temp_dir, session_name)
            session_obj = SQLiteSession(session_path)
            session_obj.set_dc(dc_id, ip, port)
            session_obj.auth_key = AuthKey(data=auth_key)
            session_obj.save()
            session_obj.close()

            session_file = session_path + ".session"
            zip_id, count, _ = await register_session_files(
                message.from_user.id, [session_file], f"{type_labels.get(input_type, 'session')}.session"
            )
            shutil.rmtree(temp_dir, ignore_errors=True)

            if zip_id:
                meta = load_meta()
                if str(zip_id) in meta["zips"]:
                    meta["zips"][str(zip_id)]["is_direct"] = True
                    save_meta(meta)
                
                try:
                    text, markup = await asyncio.wait_for(render_direct_devices(zip_id), timeout=25)
                    await _safe_update(text, reply_markup=markup if markup else None)
                except asyncio.TimeoutError:
                    await _safe_update(
                        "✅ Session registered, but device info timed out.\nUse the menu to view active sessions.",
                        reply_markup=operations_keyboard(zip_id)
                    )
            else:
                await _safe_update("❌ Failed to register session.")
            
    except INVALID_EXCEPTIONS as exc:
        await _safe_update(f"❌ Session is dead: `{type(exc).__name__}`")
    except asyncio.TimeoutError:
        await _safe_update("❌ Connection timed out. Telegram servers might be slow or out of sync.")
    except (ConnectionError, OSError):
        await _safe_update("❌ Server closed the connection. The session key is likely dead.")
    except Exception as exc:
        await _safe_update(f"❌ Error during registration: `{type(exc).__name__}: {exc}`")
    finally:
        if client:
            await safe_telethon_disconnect(client)

async def change_username_for_session(session_path, base_username):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    # Sanitize base username for Telegram (a-z, 0-9, _)
    clean_base = re.sub(r'[^a-zA-Z0-9_]', '', base_username or '').lower()
    if len(clean_base) < 3:
        clean_base = "user"
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    new_username = f"{clean_base}_{suffix}"[:32]

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                await client.set_username(new_username)
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception:
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            from telethon.tl.functions.account import UpdateUsernameRequest
            await client(UpdateUsernameRequest(new_username))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception:
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"

async def vote_for_session(session_path, target, msg_id, option_idx):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "bot", False):
                    return "bot_session"
                
                try:
                    await client.get_messages(target, msg_id)
                except Exception:
                    pass
                await client.vote_poll(target, msg_id, [option_idx])
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return "rate_limited"
        except Exception:
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
                
            from telethon.tl.functions.messages import SendVoteRequest
            peer = await client.get_input_entity(target)
            
            try:
                await client.get_messages(peer, ids=msg_id)
            except Exception:
                pass
                
            await client(SendVoteRequest(peer=peer, msg_id=msg_id, options=[str(option_idx).encode()]))
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return "rate_limited"
        except Exception:
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"

async def run_vote_poll(source_message, zip_id, target, msg_id, option_idx):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"📊 Voting on Poll — Pack #{zip_id}\nTarget: `{target}` | Msg: `{msg_id}`\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await vote_for_session(file_path, target, msg_id, option_idx)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Votes Cast", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"📊 **Mass Poll Vote Results**\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

async def run_scrape_members(source_message, zip_id, target):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "❌ No sessions found.")
        
    status = await app.send_message(source_message.chat.id, f"👥 Scraping members from {target}... Using a random active session.")
    
    members = []
    random.shuffle(files)
    for f in files:
        session_type = detect_session_type(f)
        if session_type == "pyrogram":
            session_name = os.path.basename(f).replace(".session", "")
            try:
                async with make_pyro_client(session_name, workdir=os.path.dirname(f)) as client:
                    async for member in client.get_chat_members(target):
                        if member.user:
                            members.append(f"{member.user.id},{member.user.username or ''},{member.user.first_name or ''}")
                    if members:
                        break
            except Exception:
                continue
        elif session_type == "telethon":
            client = None
            try:
                session_obj = safe_sqlite_session(f)
                client = make_tele_client(session_obj)
                await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                if await client.is_user_authorized():
                    async for user in client.iter_participants(target):
                        members.append(f"{user.id},{user.username or ''},{user.first_name or ''}")
                    if members:
                        break
            except Exception:
                pass
            finally:
                await safe_telethon_disconnect(client)
                
        if members:
            break
            
    if not members:
        return await status.edit_text("❌ Failed to scrape. Make sure the group is public or at least one session is inside it.")
        
    out_path = os.path.join(tempfile.gettempdir(), f"scraped_{target.replace('@', '').replace('https://t.me/', '')}.csv")
    with open(out_path, "w", encoding="utf-8") as file_out:
        file_out.write("ID,Username,FirstName\n")
        file_out.write("\n".join(members))
        
    await status.delete()
    await app.send_document(
        source_message.chat.id, 
        out_path,
        caption=f"✅ Successfully scraped {len(members)} members from {target}!",
        reply_markup=operations_keyboard(zip_id)
    )
    try:
        os.remove(out_path)
    except:
        pass



async def run_change_username(source_message, zip_id, base_username):
    files = get_session_files(zip_id)
    if not files:
        return await app.send_message(source_message.chat.id, "📭 ❌ No sessions found.")

    total = len(files)
    progress = ThrottledProgress(
        source_message,
        f"👤 Changing Usernames — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(SAFE_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                status = await change_username_for_session(file_path, base_username)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "Username Updated", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "rate_limited": {"label": "Rate Limited", "icon": "⏳"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(source_message.chat.id, f"👤 **Mass Username Results**\nPrefix: `{base_username}`\n\n" + "\n".join(result_lines))
    await safe_edit(source_message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

async def mark_read_for_session(session_path):
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)

    if session_type == "pyrogram":
        try:
            async with make_pyro_client(session_name, workdir=workdir) as client:
                me = await client.get_me()
                if getattr(me, "is_bot", False) or getattr(me, "bot", False):
                    return "bot_session"
                
                async for dialog in client.get_dialogs():
                    if dialog.unread_messages_count > 0:
                        try:
                            await client.read_chat_history(dialog.chat.id)
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                        except Exception:
                            pass
                return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception:
            return "failed"

    if session_type == "telethon":
        client = None
        try:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(session_obj)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                return "inactive"
            me = await client.get_me()
            if getattr(me, "bot", False):
                return "bot_session"
            
            async for dialog in client.iter_dialogs():
                if dialog.unread_count > 0:
                    try:
                        await client.send_read_acknowledge(dialog.entity)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        pass
            return "success"
        except INVALID_EXCEPTIONS:
            return "inactive"
        except Exception:
            return "failed"
        finally:
            await safe_telethon_disconnect(client)

    return "unsupported"

async def run_mark_read(call, zip_id):
    files = get_session_files(zip_id)
    if not files:
        return await call.answer("❌ No sessions found.", show_alert=True)

    total = len(files)
    progress = ThrottledProgress(
        call.message,
        f"👁️ Marking All As Read — Pack #{zip_id}\n\n{{progress}}",
        total
    )
    await progress.update(0)

    groups = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(file_path):
        phone = os.path.basename(file_path).replace('.session', '')
        if phone.startswith('+'): phone = phone[1:]
        await progress.update(0, f'Processing +{phone}...')
        try:
            async with sem:
                status = await mark_read_for_session(file_path)
                groups.setdefault(status, []).append(file_path)
        finally:
            await progress.update(1, f"Processed +{phone}")

    await gather_chunked((worker(f) for f in files))

    labels = {
        "success": {"label": "All Chats Read", "icon": "✅"},
        "inactive": {"label": "Dead/Revoked", "icon": "❌"},
        "bot_session": {"label": "Bot Session (Skipped)", "icon": "🤖"},
        "failed": {"label": "Failed/Error", "icon": "❌"},
    }
    result_lines = []
    for status, items in groups.items():
        meta = labels.get(status, {"label": status, "icon": "❓"})
        result_lines.append(f"{meta['icon']} {meta['label']}: `{len(items)}`")
    await app.send_message(call.message.chat.id, f"👁️ **Mass Mark Read Results**\n\n" + "\n".join(result_lines))
    await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))


