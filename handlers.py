# ==============================================================================
# AccStore Session Manager — Event Handlers, Callbacks & Commands
# ==============================================================================

import config
import session_core
import operations
import mail_engine
from config import *
from session_core import *
from operations import *
from session_core import (
    _parse_session_input,
    _parse_all_session_inputs,
)
from operations import (
    _connect_from_auth_key,
    _run_session_import,
    _run_bulk_session_import,
    _run_logout_self,
    _run_revoke_others,
    _render_devices_menu,
)
for _m in (config, session_core, operations):
    for _k, _v in vars(_m).items():
        if not _k.startswith('__'):
            globals()[_k] = _v

@app.on_message(filters.command("logout"))
async def logout_handler(_, message):
    LOGOUT_STATE.pop(message.from_user.id, None)
    LOGOUT_STATE[message.from_user.id] = {"step": "awaiting_input"}
    await message.reply_text(
        "🔑 **Session Manager**\n\n"
        "Send one of the following to manage an account:\n\n"
        "• A **Telethon** string session\n"
        "• A **Pyrogram** string session\n"
        "• A **hex auth key** (512 hex characters)\n"
        "• `dc_id:hex_key` (e.g. `2:aabbcc…`)\n\n"
        "You will get the **full operations menu** —\n"
        "spam check, OTP, profiles, 2FA, and more.\n\n"
        "Send /cancel to abort.",
        reply_markup=cancel_keyboard("logout"),
    )


# When the user sends /start, show a welcome message with buttons.
@app.on_message(filters.command("start"))
async def start_handler(_, message):
    LOGOUT_STATE.pop(message.from_user.id, None)
    
    # Track users with full details
    user = message.from_user
    meta = load_meta()
    if "users" not in meta or isinstance(meta["users"], list):
        meta["users"] = {}
    uid_str = str(user.id)
    if uid_str not in meta["users"]:
        meta["users"][uid_str] = {
            "id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
            "joined_at": int(time.time()),
            "last_seen": int(time.time()),
        }
    else:
        meta["users"][uid_str]["last_seen"] = int(time.time())
        meta["users"][uid_str]["first_name"] = user.first_name or ""
        meta["users"][uid_str]["last_name"] = user.last_name or ""
        meta["users"][uid_str]["username"] = user.username or ""
    save_meta(meta)
        
    param = message.command[1].lower().strip() if len(message.command) > 1 else ""

    if param in ("mail", "tempmail", "temp_mail"):
        text = (
            "📧 **Telegram Email Binding Engine** 💎\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose your preferred mail method for automatic account binding:\n\n"
            "1️⃣ **Free Temp-Mail (Zero Cost)**\n"
            "• Automatically binds accounts using disposable inboxes\n"
            "• Features 5 backup domains with instant auto-retry if Telegram rejects a domain\n\n"
            "2️⃣ **Self-Mail Vault (Personal Gmail / IMAP)**\n"
            "• Bind **2,000+ accounts** to a single Gmail mailbox using dot/case permutations\n"
            "• Auto-fetches login OTP codes in ~1 second directly into your personal vault\n\n"
            "💡 *Select an option below to proceed:*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Open Self-Mail Vault", callback_data="mail_settings_open")],
            [InlineKeyboardButton("➕ Create Session (Pyrogram)", callback_data="create|pyrogram"),
             InlineKeyboardButton("➕ Create Session (Telethon)", callback_data="create|telethon")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="mail_settings_back")]
        ])
        return await message.reply_text(text, reply_markup=keyboard)

    if param in ("vault", "gmail", "mail_settings"):
        meta = load_meta()
        mail_cfg = mail_engine.get_user_mail_config(meta, user.id)
        has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
        if has_self:
            email_str = mail_cfg.get("email")
            provider = mail_cfg.get("provider", "Custom IMAP")
            host = mail_cfg.get("imap_host", "Unknown")
            port = mail_cfg.get("imap_port", 993)
            total_p = mail_cfg.get("total_permutations", 0)
            used_p = len(mail_cfg.get("used_permutations", []))
            bound_count = mail_cfg.get("accounts_bound", used_p)
            rem_p = max(0, total_p - used_p)
            text = (
                "📧 **Mail Settings & Multiplexer Vault**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 **Your Configured Self-Mail:**\n"
                f" • Email: `{email_str}`\n"
                f" • Provider: **{provider}** (`{host}:{port}`)\n"
                f" • App Password: `{'•' * 8}` (Isolated Store)\n\n"
                f"⚡ **Permutation Stats:**\n"
                f" • Total Permutations: `{total_p:,}` unique variants\n"
                f" • Accounts Bound: `{bound_count:,}`\n"
                f" • Remaining Available: `{rem_p:,}`\n\n"
                "🔒 *Your credentials are kept isolated to your user account.*"
            )
        else:
            text = (
                "📧 **Mail Settings & Multiplexer Vault**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ **No Self-Mail Configured Yet**\n\n"
                "Add your personal email (Gmail, Outlook, Yahoo, etc.) to enable automatic multi-account binding via case & dot permutations.\n\n"
                "• Single inbox receives all OTPs\n"
                "• Each Telegram account gets a unique permutation\n"
                "• Zero code confusion with strict 1-by-1 binding"
            )
        return await message.reply_text(text, reply_markup=mail_settings_keyboard(has_self_mail=has_self))

    if param in ("shield", "floodwait", "help", "proxy"):
        text = (
            "🛡️ **Smart Anti-Flood & Proxy Shield** ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "• **Default Speed:** All Telegram account sessions & IMAP queries connect **directly** via high-speed VPS for 0ms lag.\n"
            "• **Smart Trigger:** When Telegram returns a `FloodWait` (IP rate limit), the shield instantly and automatically switches to dedicated residential proxies.\n"
            "• **Auto-Bypass:** Bypasses rate limits seamlessly during session creation, login, spam checking, and email binding!\n\n"
            "💎 *100% automated protection with zero manual setup required.*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Create Session (Pyrogram)", callback_data="create|pyrogram"),
             InlineKeyboardButton("➕ Create Session (Telethon)", callback_data="create|telethon")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="mail_settings_back")]
        ])
        return await message.reply_text(text, reply_markup=keyboard)

    if param in ("create", "session"):
        text = (
            "➕ **Create New Session**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose the session framework for your new accounts:\n\n"
            "• **Telethon**: Native `.session` SQLite format\n"
            "• **Pyrogram**: Modern async Pyrogram format"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Telethon Session", callback_data="create|telethon"),
             InlineKeyboardButton("⚡ Pyrogram Session", callback_data="create|pyrogram")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="mail_settings_back")]
        ])
        return await message.reply_text(text, reply_markup=keyboard)

    await message.reply_text(start_menu_text(), reply_markup=start_keyboard(message.from_user.id if message.from_user else None))


OVERVIEW_ANNOUNCEMENT_TEXT = (
    "🚀 **AccStore Session Manager — Mega Update** 💎\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Hey Community! Here is what is new in today's update:\n\n"
    "📧 **Multi-Domain Temp-Mail**\n"
    "• Free 1-click email binding (zero cost!)\n"
    "• Auto-switches across 5 backup domains if Telegram blocks one\n\n"
    "📬 **Self-Mail Vault (Gmail / IMAP)**\n"
    "• Connect 1 Gmail to bind **2,000+ accounts** with auto dot/case tricks\n"
    "• Telegram OTP codes read automatically in ~1 second\n\n"
    "🛡️ **Smart Anti-Flood Protection**\n"
    "• Zero lag: accounts connect direct on high-speed VPS\n"
    "• Proxy turns ON **only** when FloodWait hits to bypass rate limits!\n\n"
    "📱 **Smooth Session Creation**\n"
    "• /done button now instantly wraps & downloads your clean ZIP\n"
    "• Super-fast logins with disk recovery\n\n"
    "⚡ *Tap any colored button below to explore:*"
)

def overview_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Free Temp-Mail", callback_data="cb_overview_mail"),
            InlineKeyboardButton("💎 Gmail Mailbox Vault", callback_data="cb_overview_vault"),
        ],
        [
            InlineKeyboardButton("🛡️ FloodWait Shield", callback_data="cb_overview_shield"),
            InlineKeyboardButton("➕ Create Session", callback_data="cb_overview_create"),
        ],
        [
            InlineKeyboardButton("⚡ Main Dashboard", callback_data="cb_overview_main"),
        ]
    ])


@app.on_callback_query(filters.regex(r"^cb_overview_(mail|vault|shield|create|main|restore)$"))
async def cb_overview_handler(_, call: CallbackQuery):
    action = call.data.split("_")[-1]
    await call.answer()
    user_id = call.from_user.id

    if action == "restore":
        return await safe_edit(call.message, OVERVIEW_ANNOUNCEMENT_TEXT, reply_markup=overview_keyboard())

    if action == "mail":
        text = (
            "📧 **Telegram Email Binding Engine** 💎\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose your preferred mail method for automatic account binding:\n\n"
            "1️⃣ **Free Temp-Mail (Zero Cost)**\n"
            "• Automatically binds accounts using disposable inboxes\n"
            "• Features 5 backup domains with instant auto-retry if Telegram rejects a domain\n\n"
            "2️⃣ **Self-Mail Vault (Personal Gmail / IMAP)**\n"
            "• Bind **2,000+ accounts** to a single Gmail mailbox using dot/case permutations\n"
            "• Auto-fetches login OTP codes in ~1 second directly into your personal vault\n\n"
            "💡 *Select an option below to proceed:*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Open Self-Mail Vault", callback_data="mail_settings_open")],
            [InlineKeyboardButton("➕ Create Session (Pyrogram)", callback_data="create|pyrogram"),
             InlineKeyboardButton("➕ Create Session (Telethon)", callback_data="create|telethon")],
            [InlineKeyboardButton("🔙 Back to Announcement", callback_data="cb_overview_restore")]
        ])
        return await call.message.reply_text(text, reply_markup=keyboard)

    if action == "vault":
        meta = load_meta()
        mail_cfg = mail_engine.get_user_mail_config(meta, user_id)
        has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
        if has_self:
            email_str = mail_cfg.get("email")
            provider = mail_cfg.get("provider", "Custom IMAP")
            host = mail_cfg.get("imap_host", "Unknown")
            port = mail_cfg.get("imap_port", 993)
            total_p = mail_cfg.get("total_permutations", 0)
            used_p = len(mail_cfg.get("used_permutations", []))
            bound_count = mail_cfg.get("accounts_bound", used_p)
            rem_p = max(0, total_p - used_p)
            text = (
                "📧 **Mail Settings & Multiplexer Vault**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 **Your Configured Self-Mail:**\n"
                f" • Email: `{email_str}`\n"
                f" • Provider: **{provider}** (`{host}:{port}`)\n"
                f" • App Password: `{'•' * 8}` (Isolated Store)\n\n"
                f"⚡ **Permutation Stats:**\n"
                f" • Total Permutations: `{total_p:,}` unique variants\n"
                f" • Accounts Bound: `{bound_count:,}`\n"
                f" • Remaining Available: `{rem_p:,}`\n\n"
                "🔒 *Your credentials are kept isolated to your user account.*"
            )
        else:
            text = (
                "📧 **Mail Settings & Multiplexer Vault**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ **No Self-Mail Configured Yet**\n\n"
                "Add your personal email (Gmail, Outlook, Yahoo, etc.) to enable automatic multi-account binding via case & dot permutations.\n\n"
                "• Single inbox receives all OTPs\n"
                "• Each Telegram account gets a unique permutation\n"
                "• Zero code confusion with strict 1-by-1 binding"
            )
        return await call.message.reply_text(text, reply_markup=mail_settings_keyboard(has_self_mail=has_self))

    if action == "shield":
        text = (
            "🛡️ **Smart Anti-Flood & Proxy Shield** ⚡\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "• **Default Speed:** All Telegram account sessions & IMAP queries connect **directly** via high-speed VPS for 0ms lag.\n"
            "• **Smart Trigger:** When Telegram returns a `FloodWait` (IP rate limit), the shield instantly and automatically switches to dedicated residential proxies.\n"
            "• **Auto-Bypass:** Bypasses rate limits seamlessly during session creation, login, spam checking, and email binding!\n\n"
            "💎 *100% automated protection with zero manual setup required.*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Create Session (Pyrogram)", callback_data="create|pyrogram"),
             InlineKeyboardButton("➕ Create Session (Telethon)", callback_data="create|telethon")],
            [InlineKeyboardButton("🔙 Back to Announcement", callback_data="cb_overview_restore")]
        ])
        return await call.message.reply_text(text, reply_markup=keyboard)

    if action == "create":
        text = (
            "➕ **Create New Session**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose the session framework for your new accounts:\n\n"
            "• **Telethon**: Native `.session` SQLite format\n"
            "• **Pyrogram**: Modern async Pyrogram format"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Telethon Session", callback_data="create|telethon"),
             InlineKeyboardButton("⚡ Pyrogram Session", callback_data="create|pyrogram")],
            [InlineKeyboardButton("🔙 Back to Announcement", callback_data="cb_overview_restore")]
        ])
        return await call.message.reply_text(text, reply_markup=keyboard)

    if action == "main":
        return await call.message.reply_text(start_menu_text(), reply_markup=start_keyboard(user_id))


# ─────────────────────────────────────────────────────────────────────────────
# Admin Panel (/admin)
# Only accessible by users listed in ADMIN_IDS in .env
# ─────────────────────────────────────────────────────────────────────────────

BROADCAST_STATE = {}

BAN_STATE = {}
DM_STATE = {}

MAINTENANCE_MODE = False

@app.on_message(filters.command("admin"))
async def admin_handler(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ **Access Denied**\nYou are not authorized to access the admin panel.")
    
    meta = load_meta()
    total_users = len(meta.get("users", {}))
    total_packs = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    
    text = (
        "👑 **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: `{total_users}` · 📦 Packs: `{total_packs}` · 🚫 Banned: `{banned_count}`\n"
        f"⏱ Uptime: `{format_uptime()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply_text(text, reply_markup=admin_keyboard())

@app.on_callback_query(filters.regex(r"^admin_broadcast$"))
async def admin_broadcast_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    BROADCAST_STATE[call.from_user.id] = True
    await call.message.edit_text(
        "📢 **Broadcast Mode**\n\n"
        "Send the message you want to broadcast to all users.\n"
        "You can send **text, photo, video, document** — anything.\n\n"
        "Send /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

@app.on_callback_query(filters.regex(r"^admin_users:(\d+)$"))
async def admin_users_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    page = int(call.matches[0].group(1))
    meta = load_meta()
    users_raw = meta.get("users", {})
    user_list = []
    if isinstance(users_raw, dict):
        for uid_k, u in users_raw.items():
            if isinstance(u, dict):
                user_list.append(u)
            elif isinstance(u, (int, str)):
                user_list.append({"id": u, "first_name": f"User {u}", "joined_at": 0, "last_seen": 0})
    elif isinstance(users_raw, list):
        for u in users_raw:
            if isinstance(u, dict):
                user_list.append(u)
            elif isinstance(u, (int, str)):
                user_list.append({"id": u, "first_name": f"User {u}", "joined_at": 0, "last_seen": 0})
    
    # Sort by join date (newest first)
    user_list.sort(key=lambda u: u.get("joined_at", 0) if isinstance(u, dict) else 0, reverse=True)
    
    per_page = 10
    total = len(user_list)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages - 1)
    start = page * per_page
    chunk = user_list[start:start + per_page]
    
    text = f"👥 **All Users** — Page {page + 1}/{total_pages} (`{total}` total)\n\n"
    
    for i, u in enumerate(chunk, start=start + 1):
        name = u.get("first_name", "")
        if u.get("last_name"):
            name += f" {u['last_name']}"
        uname = f"@{u['username']}" if u.get("username") else "—"
        joined = human_time_ago(u.get("joined_at", 0))
        last_seen = human_time_ago(u.get("last_seen", 0))
        text += (
            f"**{i}.** `{u.get('id', '?')}`\n"
            f"    ├ Name: {name}\n"
            f"    ├ Username: {uname}\n"
            f"    ├ Joined: {joined}\n"
            f"    └ Last seen: {last_seen}\n\n"
        )
    
    if not chunk:
        text += "_No users yet._\n"
    
    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_users:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_users:{page + 1}"))
    
    keyboard_rows = []
    if nav_buttons:
        keyboard_rows.append(nav_buttons)
    keyboard_rows.append([InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")])
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))

@app.on_callback_query(filters.regex(r"^admin_stats$"))
async def admin_stats_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    meta = load_meta()
    users_dict = meta.get("users", {})
    total_users = len(users_dict)
    total_zips = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    
    # Count total sessions across all packs
    total_sessions = 0
    for zid in meta.get("zips", {}):
        total_sessions += len(get_session_files(zid))
    
    # Growth metrics
    now = int(time.time())
    today_start = now - (now % 86400)
    week_start = now - 604800
    users_val = list(users_dict.values()) if isinstance(users_dict, dict) else []
    users_today = sum(1 for u in users_val if isinstance(u, dict) and u.get("joined_at", 0) >= today_start)
    users_week = sum(1 for u in users_val if isinstance(u, dict) and u.get("joined_at", 0) >= week_start)
    active_today = sum(1 for u in users_val if isinstance(u, dict) and u.get("last_seen", 0) >= today_start)
    
    # Active tasks
    active_task_count = sum(len(tasks) for tasks in USER_TASKS.values() if tasks)
    active_task_users = sum(1 for tasks in USER_TASKS.values() if tasks)
    
    uptime = format_uptime()
    
    text = (
        "📊 **Bot Statistics**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👥 **Users**\n"
        f" ├ Total: `{total_users}`\n"
        f" ├ New Today: `{users_today}`\n"
        f" ├ New This Week: `{users_week}`\n"
        f" ├ Active Today: `{active_today}`\n"
        f" └ Banned: `{banned_count}`\n\n"
        "📦 **Content**\n"
        f" ├ Packs: `{total_zips}`\n"
        f" └ Sessions: `{total_sessions}`\n\n"
        "⚙️ **System**\n"
        f" ├ Uptime: `{uptime}`\n"
        f" ├ Concurrency: `{MAX_CONCURRENCY}`\n"
        f" ├ Active Tasks: `{active_task_count}` across `{active_task_users}` user(s)\n"
        f" ├ Asyncio Tasks: `{len(asyncio.all_tasks())}`\n"
        f" └ Maintenance: `{'ON' if MAINTENANCE_MODE else 'OFF'}` • Whitelist: `{'ON' if WHITELIST_MODE else 'OFF'}`\n"
    )
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]])
    await call.message.edit_text(text, reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^admin_export$"))
async def admin_export_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    if os.path.exists(META_FILE):
        await call.message.reply_document(
            document=META_FILE,
            caption="📄 **Database Export**\nThis contains all users, pack metadata, and settings."
        )
        await call.answer("Database exported successfully!")
    else:
        await call.answer("Database file not found.", show_alert=True)

@app.on_callback_query(filters.regex(r"^admin_clear_cache$"))
async def admin_clear_cache_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    deleted_count = 0
    # Clear temporary download files
    import glob
    for p in glob.glob(os.path.join(ROOT_DIR, "*.zip")):
        try:
            os.remove(p)
            deleted_count += 1
        except Exception:
            pass
            
    await call.answer(f"✅ Cache cleared! Removed {deleted_count} temporary files.", show_alert=True)

# ── Ban User ──
@app.on_callback_query(filters.regex(r"^admin_ban$"))
async def admin_ban_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    BAN_STATE[call.from_user.id] = "ban"
    await call.message.edit_text(
        "🚫 **Ban User**\n\nSend the **user ID** to ban.\nBanned users won't be able to use the bot.\n\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

@app.on_callback_query(filters.regex(r"^admin_unban$"))
async def admin_unban_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    meta = load_meta()
    banned = meta.get("banned_users", [])
    if not banned:
        return await call.answer("No banned users.", show_alert=True)
    BAN_STATE[call.from_user.id] = "unban"
    ban_list = "\n".join([f"• `{uid}`" for uid in banned])
    await call.message.edit_text(
        f"✅ **Unban User**\n\nCurrently banned:\n{ban_list}\n\nSend the **user ID** to unban.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── DM User ──
@app.on_callback_query(filters.regex(r"^admin_dm$"))
async def admin_dm_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "awaiting_id"}
    await call.message.edit_text(
        "💬 **DM User**\n\nSend the **user ID** you want to message.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── Lookup User ──
@app.on_callback_query(filters.regex(r"^admin_lookup$"))
async def admin_lookup_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "lookup"}
    await call.message.edit_text(
        "🔍 **Lookup User**\n\nSend the **user ID** to look up their details.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── Server Logs ──
@app.on_callback_query(filters.regex(r"^admin_logs$"))
async def admin_logs_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    import subprocess
    try:
        result = subprocess.run(
            ["tail", "-n", "50", "/proc/self/fd/1"],
            capture_output=True, text=True, timeout=3
        )
        logs = result.stdout.strip() or "No stdout logs available."
    except Exception:
        logs = "Could not read logs."
    # Fallback: show process info
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info().rss / 1024 / 1024
        cpu = proc.cpu_percent(interval=0.5)
        logs = (
            f"🖥 **Server Info**\n\n"
            f"CPU: `{cpu}%`\n"
            f"RAM: `{mem:.1f} MB`\n"
            f"PID: `{os.getpid()}`\n"
            f"Uptime: `{format_uptime()}`\n"
            f"Python: `{os.popen('python3 --version').read().strip()}`\n"
            f"Tasks: `{len(asyncio.all_tasks())}` active asyncio tasks"
        )
    except ImportError:
        mem_info = "N/A"
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        mem_info = line.split(":")[1].strip()
                        break
        except Exception:
            pass
        logs = (
            f"🖥 **Server Info**\n\n"
            f"PID: `{os.getpid()}`\n"
            f"RAM: `{mem_info}`\n"
            f"Uptime: `{format_uptime()}`\n"
            f"Tasks: `{len(asyncio.all_tasks())}` active asyncio tasks"
        )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]])
    await call.message.edit_text(logs, reply_markup=keyboard)


def get_vps_specs_text():
    """Generate detailed VPS specs text using psutil."""
    import psutil
    import platform

    def create_progress_bar(percent, length=10):
        filled = int(round(length * percent / 100))
        empty = length - filled
        return "█" * filled + "░" * empty

    lines = ["🖥 **VPS Performance & Specifications**"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    # ── CPU ──
    cpu_count_physical = psutil.cpu_count(logical=False) or "?"
    cpu_count_logical = psutil.cpu_count(logical=True) or "?"
    try:
        cpu_freq = psutil.cpu_freq()
        freq_str = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "N/A"
    except Exception:
        freq_str = "N/A"
    cpu_percent = psutil.cpu_percent(interval=0.5)
    load1, load5, load15 = os.getloadavg()

    cpu_model = "Unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    lines.append("🔲 **CPU Information**")
    lines.append(f"└ **Model:** `{cpu_model}`")
    lines.append(f"└ **Cores:** `{cpu_count_physical}` Physical · `{cpu_count_logical}` Logical")
    lines.append(f"└ **Freq:** `{freq_str}`")
    lines.append(f"└ **Load Avg:** `{load1:.2f}` | `{load5:.2f}` | `{load15:.2f}`")
    lines.append(f"📊 **CPU Usage:** `{cpu_percent}%`")
    lines.append(f"`[{create_progress_bar(cpu_percent)}]`\n")

    # ── Memory ──
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    lines.append("💾 **Memory Information**")
    lines.append(f"📊 **RAM Usage:** `{mem.used / 1073741824:.1f} GB` / `{mem.total / 1073741824:.1f} GB` (`{mem.percent}%`)")
    lines.append(f"`[{create_progress_bar(mem.percent)}]`")
    lines.append(f"📊 **Swap Usage:** `{swap.used / 1073741824:.1f} GB` / `{swap.total / 1073741824:.1f} GB` (`{swap.percent}%`)")
    lines.append(f"`[{create_progress_bar(swap.percent)}]`\n")

    # ── Disk ──
    disk = psutil.disk_usage("/")
    lines.append("📀 **Storage Information**")
    lines.append(f"📊 **Disk Usage:** `{disk.used / 1073741824:.1f} GB` / `{disk.total / 1073741824:.1f} GB` (`{disk.percent}%`)")
    lines.append(f"`[{create_progress_bar(disk.percent)}]`\n")

    # ── System ──
    boot_time = psutil.boot_time()
    sys_uptime = int(time.time() - boot_time)
    d, r = divmod(sys_uptime, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    uptime_parts = []
    if d: uptime_parts.append(f"{d}d")
    if h: uptime_parts.append(f"{h}h")
    if m: uptime_parts.append(f"{m}m")
    uptime_parts.append(f"{s}s")

    lines.append("🌐 **System Information**")
    lines.append(f"└ **OS:** `{platform.system()} {platform.release()}`")
    lines.append(f"└ **Arch:** `{platform.machine()}`")
    lines.append(f"└ **Python:** `{platform.python_version()}`")
    lines.append(f"└ **Uptime:** `{' '.join(uptime_parts)}`\n")

    # ── Network ──
    try:
        net = psutil.net_io_counters()
        lines.append("📡 **Network Traffic**")
        lines.append(f"└ **Sent:** `{net.bytes_sent / 1073741824:.2f} GB`  |  **Recv:** `{net.bytes_recv / 1073741824:.2f} GB`\n")
    except Exception:
        pass

    # ── Bot Process ──
    try:
        proc = psutil.Process()
        proc_mem = proc.memory_info().rss / 1048576
        proc_cpu = proc.cpu_percent(interval=0.3)
        proc_threads = proc.num_threads()
        lines.append("🤖 **Bot Process Info**")
        lines.append(f"└ **PID:** `{os.getpid()}`")
        lines.append(f"└ **RAM Used:** `{proc_mem:.1f} MB`")
        lines.append(f"└ **CPU Used:** `{proc_cpu}%`")
        lines.append(f"└ **Threads:** `{proc_threads}`")
        lines.append(f"└ **Bot Uptime:** `{format_uptime()}`")
    except Exception:
        pass

    return "\n".join(lines)


@app.on_message(filters.command("device"))
async def device_command(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ **Access Denied**\nOnly admins can view VPS specs.")
    try:
        text = get_vps_specs_text()
    except Exception as e:
        text = f"❌ Failed to fetch VPS specs: `{e}`"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_device"),
         InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_back")]
    ])
    await message.reply_text(text, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^admin_device$"))
async def admin_device_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    try:
        text = get_vps_specs_text()
    except Exception as e:
        text = f"❌ Failed to fetch VPS specs: `{e}`"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_device"),
         InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)

# ── Maintenance Mode ──
@app.on_callback_query(filters.regex(r"^admin_maint$"))
async def admin_maint_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    meta = load_meta()
    meta["maintenance"] = MAINTENANCE_MODE
    save_meta(meta)
    status = "🔴 ON — Only admins can use the bot" if MAINTENANCE_MODE else "🟢 OFF — Everyone can use the bot"
    log_admin_action(call.from_user.id, f"Maintenance mode {'enabled' if MAINTENANCE_MODE else 'disabled'}")
    await call.answer(f"Maintenance Mode: {'ON' if MAINTENANCE_MODE else 'OFF'}", show_alert=True)
    total_users = len(meta.get("users", {}))
    total_packs = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    text = (
        "👑 **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: `{total_users}` · 📦 Packs: `{total_packs}` · 🚫 Banned: `{banned_count}`\n"
        f"⏱ Uptime: `{format_uptime()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await call.message.edit_text(text, reply_markup=admin_keyboard())

# ── All Packs View ──
@app.on_callback_query(filters.regex(r"^admin_packs:(\d+)$"))
async def admin_packs_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    page = int(call.matches[0].group(1))
    meta = load_meta()
    zips = meta.get("zips", {})
    pack_list = [(zid, z) for zid, z in zips.items()]
    pack_list.sort(key=lambda x: int(x[0]), reverse=True)

    per_page = 8
    total = len(pack_list)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages - 1)
    chunk = pack_list[page * per_page:(page + 1) * per_page]

    text = f"📦 **All Packs** — Page {page + 1}/{total_pages} (`{total}` total)\n\n"
    for zid, z in chunk:
        name = z.get("name") or f"Pack {zid}"
        owner_id = z.get("owner_id", z.get("owner"))
        owner = "System/Legacy"
        if owner_id:
            uinfo = meta.get("users", {}).get(str(owner_id), {})
            if uinfo.get("username"):
                owner = f"@{uinfo['username']} (`{owner_id}`)"
            elif uinfo.get("first_name"):
                owner = f"{uinfo['first_name']} (`{owner_id}`)"
            else:
                owner = f"`{owner_id}`"
        sessions = len(get_session_files(zid))
        uploaded_ts = z.get("created_at", z.get("uploaded_at", 0))
        uploaded = human_time_ago(uploaded_ts) if uploaded_ts else "Legacy / Auto"
        text += f"**#{zid}** `{name}`\n  ├ Owner: {owner} · Sessions: `{sessions}`\n  └ Uploaded: {uploaded}\n\n"
    if not chunk:
        text += "_No packs yet._\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_packs:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_packs:{page+1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))

# ── Disk Usage ──
@app.on_callback_query(filters.regex(r"^admin_disk$"))
async def admin_disk_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    total_size = 0
    file_count = 0
    for dirpath, _, filenames in os.walk(ROOT_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    
    session_size = 0
    session_count = 0
    for dirpath, _, filenames in os.walk(PACKS_DIR):
        for f in filenames:
            if f.endswith(".session"):
                fp = os.path.join(dirpath, f)
                try:
                    session_size += os.path.getsize(fp)
                    session_count += 1
                except OSError:
                    pass

    text = (
        "💾 **Disk Usage**\n\n"
        f"📂 Data Directory: `{ROOT_DIR}/`\n"
        f"📊 Total Size: `{human_size(total_size)}`\n"
        f"📄 Total Files: `{file_count}`\n\n"
        f"🔑 Session Files: `{session_count}`\n"
        f"💿 Sessions Size: `{human_size(session_size)}`\n"
        f"📋 Registry: `{human_size(os.path.getsize(META_FILE) if os.path.exists(META_FILE) else 0)}`\n"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]])
    await call.message.edit_text(text, reply_markup=keyboard)

# ── Add Admin (runtime only) ──
@app.on_callback_query(filters.regex(r"^admin_addadmin$"))
async def admin_addadmin_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "addadmin"}
    current = ", ".join([f"`{a}`" for a in ADMIN_IDS]) or "_None_"
    await call.message.edit_text(
        f"👑 **Manage Admins**\n\nCurrent admins: {current}\n\n"
        "Send a **user ID** to add as admin (runtime only).\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Remove Admin ──
@app.on_callback_query(filters.regex(r"^admin_deladmin$"))
async def admin_deladmin_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "deladmin"}
    current = ", ".join([f"`{a}`" for a in ADMIN_IDS]) or "_None_"
    await call.message.edit_text(
        f"🗑️ **Remove Admin**\n\nCurrent admins: {current}\n\n"
        "Send the **user ID** to remove from admins.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Top Users ──
@app.on_callback_query(filters.regex(r"^admin_top_users$"))
async def admin_top_users_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    meta = load_meta()
    zips = meta.get("zips", {})
    user_packs = {}
    for zid, z in zips.items():
        owner = str(z.get("owner_id", z.get("owner", "unknown")))
        user_packs[owner] = user_packs.get(owner, 0) + 1
    
    sorted_users = sorted(user_packs.items(), key=lambda x: x[1], reverse=True)[:10]
    text = "🏆 **Top 10 Users by Packs**\n\n"
    for i, (uid, count) in enumerate(sorted_users, 1):
        if uid in ("unknown", "None", "?", ""):
            text += f"**{i}.** `System / Unclaimed` — 📦 `{count}` packs\n"
        else:
            user_data = meta.get("users", {}).get(uid, {}) if isinstance(meta.get("users"), dict) else {}
            name = user_data.get("first_name", f"User {uid}")
            text += f"**{i}.** `{uid}` ({name}) — 📦 `{count}` packs\n"
    if not sorted_users:
        text += "_No packs uploaded yet._"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

# ── NEW: Prune Packs ──
@app.on_callback_query(filters.regex(r"^admin_prune$"))
async def admin_prune_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    meta = load_meta()
    zips = meta.get("zips", {})
    empty_zips = []
    for zid in list(zips.keys()):
        if len(get_session_files(zid)) == 0:
            empty_zips.append(zid)
            del zips[zid]
            folder_path = os.path.join(PACKS_DIR, str(zid))
            if os.path.exists(folder_path):
                import shutil
                shutil.rmtree(folder_path, ignore_errors=True)
    
    if empty_zips:
        save_meta(meta)
        await call.answer(f"✅ Pruned {len(empty_zips)} empty packs!", show_alert=True)
    else:
        await call.answer("No empty packs found.", show_alert=True)

# ── NEW: Export Logs ──
@app.on_callback_query(filters.regex(r"^admin_export_logs$"))
async def admin_export_logs_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    import glob
    logs = glob.glob(os.path.join(ROOT_DIR, "*.log"))
    sent = False
    for log in logs:
        try:
            if os.path.exists(log) and os.path.getsize(log) > 0:
                await call.message.reply_document(log, caption=f"📄 Log file: `{os.path.basename(log)}`")
                sent = True
        except Exception:
            pass
    if sent:
        await call.answer("Logs sent successfully!")
    else:
        await call.answer("No log files with data found.", show_alert=True)

# ── NEW: Full Backup ──
@app.on_callback_query(filters.regex(r"^admin_full_backup$"))
async def admin_full_backup_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    await call.answer("⏳ Generating full backup...", show_alert=False)
    backup_path = os.path.join(ROOT_DIR, "full_backup.zip")
    try:
        import zipfile
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(ROOT_DIR):
                for file in files:
                    if file == "full_backup.zip": continue
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, ROOT_DIR)
                    zf.write(abs_path, arcname=rel_path)
        await call.message.reply_document(backup_path, caption="📦 **Full System Backup**")
        os.remove(backup_path)
    except Exception as e:
        await call.message.reply_text(f"❌ Backup failed: `{e}`")


# ── NEW: Admin Mail Vault ──
@app.on_callback_query(filters.regex(r"^admin_mail_vault$"))
async def admin_mail_vault_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    await call.answer()
    
    meta = load_meta()
    vault = meta.get("user_mail_vault", {})
    
    if not vault:
        return await safe_edit(
            call.message,
            "🔐 **Admin Mail Vault**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nNo users have configured self-mail yet.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Return", callback_data="admin_panel_open")]])
        )
    
    lines = [
        "🔐 **Admin Mail Vault**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Configured Users: `{len(vault)}`\n"
    ]
    
    for uid_str, cfg in vault.items():
        email = cfg.get("email", "Unknown")
        pwd = cfg.get("app_password", "••••••••")
        prov = cfg.get("provider", "IMAP")
        host = cfg.get("imap_host", "")
        port = cfg.get("imap_port", 993)
        total_p = cfg.get("total_permutations", 0)
        used_p = len(cfg.get("used_permutations", []))
        bound = cfg.get("accounts_bound", 0)
        
        lines.append(
            f"👤 **User ID:** `{uid_str}`\n"
            f" • Email: `{email}`\n"
            f" • Password: `{pwd}`\n"
            f" • Server: `{host}:{port}` ({prov})\n"
            f" • Permutations: `{used_p}` used / `{total_p:,}` total\n"
            f" • Accounts Bound: `{bound}`\n"
        )
    
    vault_text = "\n".join(lines).strip()
    
    if len(vault_text) > 4000:
        temp_dir = tempfile.mkdtemp(prefix="acs_vault_")
        txt_path = os.path.join(temp_dir, "admin_mail_vault.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(vault_text.replace("**", "").replace("`", ""))
            await app.send_document(
                call.message.chat.id,
                txt_path,
                caption="🔐 **Admin Mail Vault Report**",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        await safe_edit(
            call.message,
            f"🔐 **Admin Mail Vault**\n\nVault data exceeded message length and was sent as a document (`{len(vault)}` users).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Return", callback_data="admin_panel_open")]])
        )
    else:
        await safe_edit(
            call.message,
            vault_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Return", callback_data="admin_panel_open")]])
        )


# ── NEW: Search Pack ──
@app.on_callback_query(filters.regex(r"^admin_search_pack$"))
async def admin_search_pack_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "search_pack"}
    await call.message.edit_text(
        "🔎 **Search Pack**\n\nSend the **Pack ID** you want to find.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Delete Pack ──
@app.on_callback_query(filters.regex(r"^admin_del_pack$"))
async def admin_del_pack_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "del_pack"}
    await call.message.edit_text(
        "💣 **Delete Pack**\n\nSend the **Pack ID** you want to forcefully delete.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Set Announce ──
@app.on_callback_query(filters.regex(r"^admin_set_announce$"))
async def admin_set_announce_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "set_announce"}
    await call.message.edit_text(
        "📝 **Set Announcement**\n\nSend the text you want all users to see when they start the bot.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Clear Announce ──
@app.on_callback_query(filters.regex(r"^admin_clear_announce$"))
async def admin_clear_announce_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    global GLOBAL_ANNOUNCEMENT
    GLOBAL_ANNOUNCEMENT = ""
    meta = load_meta()
    meta["announcement"] = ""
    save_meta(meta)
    await call.answer("✅ Announcement cleared!", show_alert=True)

# ── NEW: Kill Tasks ──
@app.on_callback_query(filters.regex(r"^admin_kill_tasks$"))
async def admin_kill_tasks_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    DM_STATE[call.from_user.id] = {"step": "kill_tasks"}
    await call.message.edit_text(
        "🛑 **Kill User Tasks**\n\nSend the **user ID** whose background operations you want to terminate.\nSend /cancel to abort.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]])
    )

# ── NEW: Restart Bot ──
@app.on_callback_query(filters.regex(r"^admin_restart$"))
async def admin_restart_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    log_admin_action(call.from_user.id, "Restarted the bot")
    try:
        await call.message.edit_text("🔄 **Restarting bot...**\nThe bot will be back online in a few seconds.")
    except Exception:
        pass
    
    # Save any states if needed, then execute process restart
    import sys
    import os
    try:
        await app.stop()
    except Exception:
        pass
    # We use execl to completely replace the current python process
    os.execl(sys.executable, sys.executable, *sys.argv)

# ── NEW: Activity Log ──
@app.on_callback_query(filters.regex(r"^admin_activity$"))
async def admin_activity_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    if not ADMIN_ACTIVITY_LOG:
        text = "📝 **Admin Activity Log**\n\n_No actions recorded yet._"
    else:
        entries = list(ADMIN_ACTIVITY_LOG)[:20]  # Show latest 20
        log_text = "\n".join(entries)
        text = f"📝 **Admin Activity Log** (Latest {len(entries)})\n━━━━━━━━━━━━━━━━━━━━━━\n\n{log_text}"
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]])
    await call.message.edit_text(text, reply_markup=keyboard)

# ── NEW: Active Tasks Monitor ──
@app.on_callback_query(filters.regex(r"^admin_active_tasks$"))
async def admin_active_tasks_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    
    meta = load_meta()
    lines = []
    total_tasks = 0
    for uid, tasks in USER_TASKS.items():
        if not tasks:
            continue
        task_count = len(tasks)
        total_tasks += task_count
        user_data = meta.get("users", {}).get(str(uid), {})
        name = user_data.get("first_name", f"User {uid}")
        uname = f"@{user_data['username']}" if user_data.get("username") else ""
        lines.append(f" ├ 👤 `{uid}` ({name} {uname}) — `{task_count}` task(s)")
    
    if not lines:
        text = "⚡ **Active Tasks Monitor**\n\n_No active tasks running._"
    else:
        text = (
            f"⚡ **Active Tasks Monitor**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Total: `{total_tasks}` task(s) across `{len(lines)}` user(s)\n\n"
            + "\n".join(lines)
        )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_active_tasks")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)

# ── NEW: Whitelist Mode Toggle ──
@app.on_callback_query(filters.regex(r"^admin_whitelist$"))
async def admin_whitelist_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    global WHITELIST_MODE
    WHITELIST_MODE = not WHITELIST_MODE
    meta = load_meta()
    meta["whitelist"] = WHITELIST_MODE
    save_meta(meta)
    status = "ON — Only whitelisted users + admins can use the bot" if WHITELIST_MODE else "OFF — All users can use the bot"
    log_admin_action(call.from_user.id, f"Whitelist mode {'enabled' if WHITELIST_MODE else 'disabled'}")
    await call.answer(f"Whitelist Mode: {'ON' if WHITELIST_MODE else 'OFF'}", show_alert=True)
    total_users = len(meta.get("users", {}))
    total_packs = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    text = (
        "👑 **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: `{total_users}` · 📦 Packs: `{total_packs}` · 🚫 Banned: `{banned_count}`\n"
        f"⏱ Uptime: `{format_uptime()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await call.message.edit_text(text, reply_markup=admin_keyboard())

@app.on_callback_query(filters.regex(r"^admin_back$"))
async def admin_back_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    BROADCAST_STATE.pop(call.from_user.id, None)
    BAN_STATE.pop(call.from_user.id, None)
    DM_STATE.pop(call.from_user.id, None)
    meta = load_meta()
    total_users = len(meta.get("users", {}))
    total_packs = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    text = (
        "👑 **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: `{total_users}` · 📦 Packs: `{total_packs}` · 🚫 Banned: `{banned_count}`\n"
        f"⏱ Uptime: `{format_uptime()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await call.message.edit_text(text, reply_markup=admin_keyboard())

@app.on_message(filters.group | filters.private, group=1)
async def broadcast_message_handler(_, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    
    if BROADCAST_STATE.get(user_id):
        if message.text and message.text.startswith("/"):
            if message.text.startswith("/cancel"):
                BROADCAST_STATE.pop(user_id, None)
                await message.reply_text("📢 Broadcast cancelled.")
            return
            
        BROADCAST_STATE.pop(user_id, None)
        meta = load_meta()
        users_dict = meta.get("users", {})
        user_ids = [u.get("id") for u in users_dict.values() if u.get("id")]
        
        status_msg = await message.reply_text(f"🚀 Broadcasting to `{len(user_ids)}` users...")
        success = 0
        failed = 0
        
        for uid in user_ids:
            try:
                await message.copy(uid)
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
                
        await status_msg.edit_text(
            f"✅ **Broadcast Completed**\n\n"
            f"📨 Sent: `{success}`\n"
            f"❌ Failed: `{failed}`\n"
            f"👥 Total: `{len(user_ids)}`"
        )


# Ban enforcement & DM-only middleware — runs before all handlers
@app.on_message(group=-9)
async def ban_check_middleware(_, message):
    if not message.from_user or getattr(message.from_user, "is_bot", False) or getattr(message.from_user, "is_self", False):
        raise StopPropagation
    if message.chat and "private" not in str(message.chat.type).lower():
        raise StopPropagation
    meta = load_meta()
    banned = meta.get("banned_users", [])
    if message.from_user.id in banned:
        await message.reply_text("🚫 **You are banned from using this bot.**\nContact the admin if you think this is a mistake.")
        raise StopPropagation

@app.on_callback_query(group=-9)
async def ban_check_callback_middleware(_, call):
    meta = load_meta()
    banned = meta.get("banned_users", [])
    if call.from_user.id in banned:
        await call.answer("🚫 You are banned from this bot.", show_alert=True)
        raise StopPropagation


@app.on_message(filters.private, group=-6)
async def admin_state_handler(_, message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    uid = message.from_user.id
    if uid not in BAN_STATE and uid not in DM_STATE:
        return
    text = (message.text or "").strip()

    try:
        # ── Ban/Unban flow ──
        if uid in BAN_STATE:
            action = BAN_STATE.pop(uid)
            if text.startswith("/"):
                return
            meta = load_meta()
            target_id = resolve_user_id(text, meta)
            if not target_id:
                return await message.reply_text("❌ Please send a valid numeric user ID or @username.")
            meta.setdefault("banned_users", [])

            if action == "ban":
                if target_id in meta["banned_users"]:
                    return await message.reply_text(f"ℹ️ User `{target_id}` is already banned.")
                if target_id in ADMIN_IDS:
                    return await message.reply_text("❌ You cannot ban an admin.")
                meta["banned_users"].append(target_id)
                save_meta(meta)
                log_admin_action(uid, f"Banned user {target_id}")
                await message.reply_text(f"🚫 User `{target_id}` has been **banned**.")
            elif action == "unban":
                if target_id not in meta["banned_users"]:
                    return await message.reply_text(f"ℹ️ User `{target_id}` is not banned.")
                meta["banned_users"].remove(target_id)
                save_meta(meta)
                log_admin_action(uid, f"Unbanned user {target_id}")
                await message.reply_text(f"✅ User `{target_id}` has been **unbanned**.")
            return

        # ── DM User flow ──
        if uid in DM_STATE:
            state = DM_STATE[uid]
            if text.startswith("/"):
                DM_STATE.pop(uid, None)
                return

            if state.get("step") == "awaiting_id":
                meta = load_meta()
                target_id = resolve_user_id(text, meta)
                if not target_id:
                    return await message.reply_text("❌ Please send a valid numeric user ID or @username.")
                DM_STATE[uid] = {"step": "awaiting_msg", "target": target_id}
                return await message.reply_text(f"💬 Now send the **message** you want to deliver to `{target_id}`.\nSend /cancel to abort.")

            if state.get("step") == "awaiting_msg":
                target = state["target"]
                DM_STATE.pop(uid, None)
                try:
                    await message.copy(target)
                    await message.reply_text(f"✅ Message sent to `{target}` successfully.")
                except Exception as e:
                    await message.reply_text(f"❌ Failed to send message to `{target}`:\n`{e}`")
                return

            # ── Lookup User flow ──
            if state.get("step") == "lookup":
                DM_STATE.pop(uid, None)
                meta = load_meta()
                target_id = resolve_user_id(text, meta)
                if not target_id:
                    return await message.reply_text(f"❌ User `{text}` not found in the database.")
                target_id_str = str(target_id)
                user_data = meta.get("users", {}).get(target_id_str)
                banned = meta.get("banned_users", [])

                if not user_data:
                    user_data = {"id": target_id, "first_name": "Unknown", "last_name": "", "username": "", "joined_at": 0, "last_seen": 0}

                name = user_data.get("first_name", "")
                if user_data.get("last_name"):
                    name += f" {user_data['last_name']}"
                uname = f"@{user_data['username']}" if user_data.get("username") else "—"
                joined = format_timestamp(user_data.get("joined_at", 0)) if user_data.get("joined_at") else "Unknown"
                last_seen = format_timestamp(user_data.get("last_seen", 0)) if user_data.get("last_seen") else "Unknown"
                is_banned = "🚫 Yes" if target_id in banned else "✅ No"
                is_adm = "👑 Yes" if target_id in ADMIN_IDS else "No"
                packs_owned = sum(1 for z in meta.get("zips", {}).values() if str(z.get("owner_id", z.get("owner"))) == target_id_str)

                info = (
                    f"🔍 **User Lookup**\n\n"
                    f"🆔 ID: `{target_id}`\n"
                    f"👤 Name: {name}\n"
                    f"📛 Username: {uname}\n"
                    f"📅 Joined: {joined}\n"
                    f"🕐 Last Seen: {last_seen}\n"
                    f"📦 Packs Owned: `{packs_owned}`\n"
                    f"🚫 Banned: {is_banned}\n"
                    f"👑 Admin: {is_adm}\n"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 DM User", callback_data="admin_dm"),
                     InlineKeyboardButton("🚫 Ban User" if target_id not in banned else "✅ Unban User", callback_data="admin_ban" if target_id not in banned else "admin_unban")],
                    [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back")]
                ])
                return await message.reply_text(info, reply_markup=keyboard)

            # ── Add Admin flow ──
            if state.get("step") == "addadmin":
                DM_STATE.pop(uid, None)
                if not text.isdigit():
                    return await message.reply_text("❌ Please send a valid numeric user ID.")
                new_admin = int(text)
                if new_admin in ADMIN_IDS:
                    return await message.reply_text(f"ℹ️ User `{new_admin}` is already an admin.")
                ADMIN_IDS.append(new_admin)
                meta = load_meta()
                admins_list = meta.setdefault("admins", [])
                if new_admin not in admins_list:
                    admins_list.append(new_admin)
                save_meta(meta)
                await message.reply_text(f"👑 User `{new_admin}` added as **admin** and saved to database.")
                return

            # ── Remove Admin flow ──
            if state.get("step") == "deladmin":
                DM_STATE.pop(uid, None)
                if not text.isdigit():
                    return await message.reply_text("❌ Please send a valid numeric user ID.")
                target = int(text)
                if target not in ADMIN_IDS:
                    return await message.reply_text(f"ℹ️ User `{target}` is not an admin.")
                if target == uid:
                    return await message.reply_text("❌ You cannot remove yourself.")
                ADMIN_IDS.remove(target)
                meta = load_meta()
                admins_list = meta.setdefault("admins", [])
                if target in admins_list:
                    admins_list.remove(target)
                save_meta(meta)
                await message.reply_text(f"🗑️ User `{target}` removed from admins.")
                return

            # ── Search Pack flow ──
            if state.get("step") == "search_pack":
                DM_STATE.pop(uid, None)
                search_query = text.strip().lstrip("#")
                meta = load_meta()
                zips = meta.get("zips", {})
                
                matching = []
                if search_query in zips:
                    matching.append((search_query, zips[search_query]))
                for zid, z in zips.items():
                    if zid != search_query:
                        name = str(z.get("name", "")).lower()
                        owner = str(z.get("owner_id", z.get("owner", "")))
                        if search_query.lower() in name or search_query == owner:
                            matching.append((zid, z))
                            
                if not matching:
                    return await message.reply_text(f"❌ No packs found matching `{text}`.")
                    
                for zid, z in matching[:5]:
                    sessions = len(get_session_files(zid))
                    owner_id = z.get('owner_id', z.get('owner'))
                    name = z.get('name') or f"Pack {zid}"
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📂 Open Pack", callback_data=f"zip|open|{zid}")],
                        [InlineKeyboardButton("💣 Force Delete", callback_data=f"zip|confirm_destroy|{zid}")]
                    ])
                    await message.reply_text(
                        f"📦 **Pack #{zid}** — `{name}`\n"
                        f" ├ Owner: `{owner_id}`\n"
                        f" ├ Sessions: `{sessions}`\n"
                        f" └ Uploaded: `{human_time_ago(z.get('created_at', z.get('uploaded_at', 0)))}`",
                        reply_markup=keyboard
                    )
                return

            # ── Delete Pack flow ──
            if state.get("step") == "del_pack":
                DM_STATE.pop(uid, None)
                pack_id = text
                meta = load_meta()
                if pack_id not in meta.get("zips", {}):
                    return await message.reply_text(f"❌ Pack `#{pack_id}` not found.")
                del meta["zips"][pack_id]
                save_meta(meta)
                import shutil
                shutil.rmtree(os.path.join(PACKS_DIR, pack_id), ignore_errors=True)
                log_admin_action(uid, f"Force-deleted pack #{pack_id}")
                return await message.reply_text(f"💣 Pack `#{pack_id}` forcefully deleted.")

            # ── Set Announce flow ──
            if state.get("step") == "set_announce":
                DM_STATE.pop(uid, None)
                global GLOBAL_ANNOUNCEMENT
                GLOBAL_ANNOUNCEMENT = text
                meta = load_meta()
                meta["announcement"] = text
                save_meta(meta)
                return await message.reply_text("✅ Announcement set and saved successfully!")

            # ── Kill Tasks flow ──
            if state.get("step") == "kill_tasks":
                DM_STATE.pop(uid, None)
                if text.lower() == "all":
                    count = 0
                    for u_id, t_set in list(USER_TASKS.items()):
                        for t in list(t_set):
                            t.cancel()
                            count += 1
                    log_admin_action(uid, f"Killed {count} active tasks across all users")
                    return await message.reply_text(f"🛑 Terminated `{count}` background tasks across all users.")
                if not text.isdigit():
                    return await message.reply_text("❌ Send a valid numeric user ID or `all` to cancel everything.")
                target_id = int(text)
                tasks = USER_TASKS.get(target_id)
                if not tasks:
                    return await message.reply_text(f"ℹ️ User `{target_id}` has no active tasks.")
                count = len(tasks)
                for t in list(tasks):
                    t.cancel()
                log_admin_action(uid, f"Killed {count} tasks for user {target_id}")
                return await message.reply_text(f"🛑 Terminated `{count}` tasks for user `{target_id}`.")
    finally:
        raise StopPropagation


# Maintenance mode middleware — blocks non-admins when maintenance is on
@app.on_message(group=-8)
async def maintenance_middleware(_, message):
    if not MAINTENANCE_MODE:
        return
    if not message.from_user:
        return
    if is_admin(message.from_user.id):
        return
    await message.reply_text("🔧 **Bot is under maintenance.**\nPlease try again later.")
    raise StopPropagation

@app.on_callback_query(group=-8)
async def maintenance_callback_middleware(_, call):
    if not MAINTENANCE_MODE:
        return
    if is_admin(call.from_user.id):
        return
    await call.answer("🔧 Bot is under maintenance. Try later.", show_alert=True)
    raise StopPropagation

# Whitelist mode middleware — blocks non-whitelisted users when whitelist is on
@app.on_message(group=-7)
async def whitelist_middleware(_, message):
    if not WHITELIST_MODE:
        return
    if not message.from_user:
        return
    if is_admin(message.from_user.id):
        return
    if message.from_user.id in WHITELISTED_USERS:
        return
    await message.reply_text("🔐 **Access Restricted**\nThis bot is currently in whitelist-only mode. Contact an admin for access.")
    raise StopPropagation

@app.on_callback_query(group=-7)
async def whitelist_callback_middleware(_, call):
    if not WHITELIST_MODE:
        return
    if is_admin(call.from_user.id):
        return
    if call.from_user.id in WHITELISTED_USERS:
        return
    await call.answer("🔐 Bot is in whitelist mode. Contact admin.", show_alert=True)
    raise StopPropagation


@app.on_message(filters.command("ping"))
async def ping_handler(_, message):
    import time
    start = time.time()
    msg = await message.reply_text("🏓 Pong!")
    diff = int((time.time() - start) * 1000)
    await msg.edit_text(f"🏓 Pong! `{diff}ms`")

@app.on_message(filters.command(["cancel", "cancil"]))
async def cancel_handler(_, message):
    LOGOUT_STATE.pop(message.from_user.id, None)
    PROMPT_STATE.pop(message.from_user.id, None)
    CREATE_STATE.pop(message.from_user.id, None)
    BROADCAST_STATE.pop(message.from_user.id, None)
    BAN_STATE.pop(message.from_user.id, None)
    DM_STATE.pop(message.from_user.id, None)
    
    tasks = USER_TASKS.get(message.from_user.id)
    if not tasks:
        return await message.reply_text("ℹ️ No active operations found to cancel.")
    
    count = len(tasks)
    for task in list(tasks):
        task.cancel()
        
    await message.reply_text(f"🛑 Cancelled {count} active operation(s).")



@app.on_callback_query(filters.regex(r"^start_menu$"))
async def start_menu_callback(_, call: CallbackQuery):
    LOGOUT_STATE.pop(call.from_user.id, None)
    await safe_edit(call.message, start_menu_text(), reply_markup=start_keyboard(call.from_user.id))

@app.on_callback_query(filters.regex(r"^admin_panel_open$"))
async def admin_panel_open_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Access Denied", show_alert=True)
    meta = load_meta()
    total_users = len(meta.get("users", {}))
    total_packs = len(meta.get("zips", {}))
    banned_count = len(meta.get("banned_users", []))
    text = (
        "👑 **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: `{total_users}` · 📦 Packs: `{total_packs}` · 🚫 Banned: `{banned_count}`\n"
        f"⏱ Uptime: `{format_uptime()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(call.message, text, reply_markup=admin_keyboard())

@app.on_callback_query(filters.regex(r"^user_packs:(\d+)$"))
async def user_packs_callback(_, call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    page = int(call.matches[0].group(1))
    meta = load_meta()
    zips = meta.get("zips", {})
    uid = call.from_user.id
    user_zips = [(zid, z) for zid, z in zips.items() if zip_belongs_to_user(int(zid), uid)]
    user_zips.sort(key=lambda x: int(x[0]), reverse=True)
    
    if not user_zips:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Create Telethon Pack", callback_data="create|telethon"),
             InlineKeyboardButton("➕ Create Pyrogram Pack", callback_data="create|pyrogram")],
            [InlineKeyboardButton("🔙 Back to Start", callback_data="start_menu")]
        ])
        return await safe_edit(
            call.message,
            "📦 **My Session Packs**\n\nYou don't have any saved packs yet.\nSend an archive (`.zip`, `.rar`, `.7z`) or `.session` file, paste session strings, or create one below.",
            reply_markup=keyboard
        )
    
    per_page = 6
    total = len(user_zips)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = user_zips[page * per_page:(page + 1) * per_page]
    
    keyboard_rows = []
    for zid, z in chunk:
        name = z.get("name") or f"Pack {zid}"
        cnt = len(get_session_files(zid))
        keyboard_rows.append([InlineKeyboardButton(f"📂 #{zid} {name} ({cnt} sessions)", callback_data=f"zip|open|{zid}")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"user_packs:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"user_packs:{page+1}"))
    if nav:
        keyboard_rows.append(nav)
    keyboard_rows.append([InlineKeyboardButton("🔙 Back to Start", callback_data="start_menu")])
    
    await safe_edit(call.message, f"📦 **My Session Packs** (Page {page + 1}/{total_pages})\nSelect a pack to manage:", reply_markup=InlineKeyboardMarkup(keyboard_rows))

@app.on_callback_query(filters.regex(r"^start_logout$"))
async def start_logout_callback(_, call: CallbackQuery):
    """Triggered when user taps the 'Logout by String/Key' button on the /start menu."""
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    await call.answer()
    LOGOUT_STATE.pop(call.from_user.id, None)
    LOGOUT_STATE[call.from_user.id] = {"step": "awaiting_input"}
    await call.message.reply_text(
        "🔑 **Session Manager**\n\n"
        "Send one of the following to manage an account:\n\n"
        "• A **Telethon** string session\n"
        "• A **Pyrogram** string session\n"
        "• A **hex auth key** (512 hex characters)\n"
        "• `dc_id:hex_key` (e.g. `2:aabbcc…`)\n\n"
        "You will get the **full operations menu** —\n"
        "spam check, OTP, profiles, 2FA, and more.\n\n"
        "Send /cancel to abort.",
        reply_markup=cancel_keyboard("logout"),
    )


USER_FILE_LOCKS = {}

@app.on_message(filters.document & ~filters.me)
async def file_handler(_, message):
    if getattr(message, "outgoing", False) or (message.from_user and getattr(message.from_user, "is_self", False)):
        return
        
    user_id = message.from_user.id
    if user_id not in USER_FILE_LOCKS:
        import asyncio
        USER_FILE_LOCKS[user_id] = asyncio.Lock()
        
    async with USER_FILE_LOCKS[user_id]:
        await _file_handler_locked(_, message)

async def _file_handler_locked(_, message):
    prompt_state = PROMPT_STATE.get(message.from_user.id)
    if prompt_state and prompt_state.get("action") == "change_avatar":
        PROMPT_STATE.pop(message.from_user.id, None)
        status = await message.reply_text("📥 Downloading photo from file...")
        downloaded = None
        try:
            downloaded = await message.download(file_name=f"{message.id}_avatar")
            async with register_user_task(message.from_user.id, message):
                await run_change_avatar(prompt_state["source_message"], prompt_state["zip_id"], downloaded)
        except Exception as e:
            await status.edit_text(f"❌ Failed to process photo file: {e}")
        finally:
            if downloaded and os.path.exists(downloaded):
                try:
                    os.remove(downloaded)
                except:
                    pass
        return



    document = message.document
    file_name = (document.file_name or "").strip()
    lower_name = file_name.lower()

    # Strip Telegram's duplicate-file suffixes like " (1)", " (2)" etc.
    import re
    clean_name = re.sub(r'\s*\(\d+\)\s*$', '', lower_name)

    # Determine the file type
    ARCHIVE_EXTS = (
        ".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz",
        ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz", ".bz2", ".xz"
    )

    supported_ext = None
    if any(clean_name.endswith(ext) for ext in ARCHIVE_EXTS):
        supported_ext = "archive"
    elif clean_name.endswith(".session"):
        supported_ext = ".session"
    elif clean_name.endswith(".json"):
        supported_ext = ".json"
    elif clean_name.endswith(".txt"):
        supported_ext = ".txt"

    # Also inspect MIME type for archives with missing or non-standard extension
    if not supported_ext and document.mime_type:
        mime = document.mime_type.lower()
        if any(m in mime for m in ("zip", "rar", "7z", "tar", "compressed", "archive", "gzip", "bzip2")):
            supported_ext = "archive"
        elif "json" in mime:
            supported_ext = ".json"
        elif "text" in mime:
            supported_ext = ".txt"

    if not supported_ext:
        return await message.reply_text(
            "❌ **Unsupported file format.**\n\n"
            "Accepted formats:\n"
            "• **Archives**: `.zip`, `.rar`, `.7z`, `.tar`, `.tar.gz` (with session files or `tdata`)\n"
            "• **Sessions**: `.session` (Telethon or Pyrogram)\n"
            "• **JSON Data**: `.json` (session exports)\n"
            "• **Text Strings**: `.txt` (string sessions or hex keys)"
        )

    # --- Merge flow (accepts archives and .session) ---
    prompt_state = PROMPT_STATE.get(message.from_user.id)
    if prompt_state and prompt_state.get("action") == "merge":
        if supported_ext == "archive":
            merge_status = await message.reply_text("📥 Downloading merge pack...")
            downloaded = await message.download(
                file_name=f"{message.id}_{file_name or 'merge.zip'}",
                progress=download_progress,
                progress_args=(merge_status, "📥 Downloading merge pack..."),
            )
            try:
                await safe_edit(merge_status, "📦 Extracting sessions...")
                added = await extract_sessions_from_zip(downloaded, prompt_state["merge_dir"], clean=False)
                prompt_state["merge_files"] = sorted(
                    os.path.join(prompt_state["merge_dir"], name)
                    for name in os.listdir(prompt_state["merge_dir"])
                    if name.endswith(".session")
                )
            finally:
                PROGRESS_STATE.pop((merge_status.chat.id, merge_status.id), None)
                if downloaded and os.path.exists(downloaded):
                    os.remove(downloaded)
            return await message.reply_text(f"✅ Added `{added}` session(s) for merge (Total: `{len(prompt_state['merge_files'])}`). Send more packs or /done.")
        elif supported_ext == ".session":
            merge_status = await message.reply_text("📥 Downloading session for merge...")
            downloaded = await message.download(file_name=f"{message.id}_{file_name or 'merge.session'}")
            try:
                dest = os.path.join(prompt_state["merge_dir"], os.path.basename(downloaded))
                shutil.copy2(downloaded, dest)
                prompt_state["merge_files"] = sorted(
                    os.path.join(prompt_state["merge_dir"], name)
                    for name in os.listdir(prompt_state["merge_dir"])
                    if name.endswith(".session")
                )
            finally:
                if downloaded and os.path.exists(downloaded):
                    os.remove(downloaded)
            return await message.reply_text(f"✅ Added `1` session for merge (Total: `{len(prompt_state['merge_files'])}`). Send more files or /done.")
        else:
            return await message.reply_text("❌ Only archive files (`.zip`, `.rar`, `.7z`) and `.session` files are supported for merge.")

    # --- Normal upload ---
    status = await message.reply_text("📥 Downloading...")
    downloaded = await message.download(
        file_name=f"{message.id}_{file_name or 'upload'}",
        progress=download_progress,
        progress_args=(status, "📥 Downloading..."),
    )

    try:
        if supported_ext == "archive":
            await safe_edit(status, "🔍 Analyzing archive & extracting...")
            zip_id, session_count, _ = await register_zip(message.from_user.id, downloaded, file_name)
            if not zip_id:
                return await status.edit_text(
                    "❌ **No valid Telegram sessions found in this archive.**\n\n"
                    "Supported contents inside archive:\n"
                    "• `.session` files (Telethon or Pyrogram)\n"
                    "• `tdata/` Telegram Desktop folder\n"
                    "• `.json` account files (with auth_key, session_string, or hex)\n"
                    "• `.txt` files containing string sessions or auth keys"
                )
            await status.edit_text(zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

        elif supported_ext == ".session":
            await safe_edit(status, "🔍 Analyzing session file...")
            zip_id, session_count, _ = await register_session_files(
                message.from_user.id, [downloaded], file_name
            )
            if not zip_id:
                return await status.edit_text("❌ Invalid or empty `.session` file.")
            await status.edit_text(zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

        elif supported_ext == ".json":
            await safe_edit(status, "🔍 Parsing JSON session data...")
            temp_dir = tempfile.mkdtemp(prefix="acs_json_")
            try:
                session_paths = parse_json_session_file(downloaded, temp_dir)
                if not session_paths:
                    return await status.edit_text(
                        "❌ Could not parse session from JSON.\n\n"
                        "Supported JSON formats:\n"
                        "• `{\"dc_id\": N, \"auth_key\": \"hex_or_base64\"}`\n"
                        "• `{\"session_string\": \"...\"}` (Telethon/Pyrogram)\n"
                        "• A list of the above structures"
                    )
                zip_id, session_count, _ = await register_session_files(
                    message.from_user.id, session_paths, file_name
                )
                if not zip_id:
                    return await status.edit_text("❌ Failed to register session from JSON.")
                await status.edit_text(zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        elif supported_ext == ".txt":
            await safe_edit(status, "🔍 Reading text file...")
            try:
                with open(downloaded, "r", encoding="utf-8", errors="ignore") as fh:
                    file_content = fh.read().strip()
                auth_key, dc_id, input_type = _parse_session_input(file_content)
            except Exception as parse_exc:
                return await status.edit_text(f"❌ Could not find a valid session or hex key in the text file: {parse_exc}")
            
            await status.edit_text(f"⏳ Connecting to `{input_type}` from TXT file...")
            type_labels = {
                "telethon_string": "Telethon String",
                "pyrogram_string": "Pyrogram String",
                "hex_key": "Hex Auth Key",
            }
            client = None
            try:
                try:
                    client = await _connect_from_auth_key(auth_key, dc_id)
                    dc_id = client.session.dc_id
                except asyncio.TimeoutError:
                    await status.edit_text("❌ **Connection timed out (Telegram rejected the key).**\n\nTelegram did not respond. This means the session key is **completely dead, revoked, or unrecognized** by Telegram.")
                    return
                except Exception as conn_exc:
                    await status.edit_text(f"❌ Connection failed: `{type(conn_exc).__name__}: {conn_exc}`")
                    return

                if not await client.is_user_authorized():
                    await status.edit_text("❌ Session is not authorized (already dead or invalid).")
                    return
                
                me = await client.get_me()
                if getattr(me, "bot", False):
                    await status.edit_text("🤖 Bot sessions are not supported. Please use a user session.")
                    return
                    
                await status.edit_text(f"✅ Connection successful! Registering pack...")
                
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
                        text_out, markup = await asyncio.wait_for(render_direct_devices(zip_id), timeout=25)
                        if markup:
                            await status.edit_text(text_out, reply_markup=markup)
                        else:
                            await status.edit_text(text_out)
                    except asyncio.TimeoutError:
                        await status.edit_text(
                            "✅ Session registered, but device info timed out.\nUse the menu to view active sessions.",
                            reply_markup=operations_keyboard(zip_id)
                        )
                else:
                    await status.edit_text("❌ Failed to register session.")
            finally:
                if client:
                    await safe_telethon_disconnect(client)

    except Exception as exc:
        return await status.edit_text(f"❌ Error: {exc}")
    finally:
        PROGRESS_STATE.pop((status.chat.id, status.id), None)
        if downloaded and os.path.exists(downloaded):
            os.remove(downloaded)



@app.on_message(filters.text & ~filters.command(["start", "logout", "cancel"]), group=-2)
async def handle_logout_input(_, message):
    """Catch session string / hex key input."""
    # Do not intercept if user is currently engaged in another prompt flow
    if PROMPT_STATE.get(message.from_user.id) or CREATE_STATE.get(message.from_user.id):
        return

    state = LOGOUT_STATE.get(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        return

    # Check if we are explicitly awaiting or if the user sent a valid session string/hex key
    is_awaiting = state and state.get("step") == "awaiting_input"
    
    try:
        auth_key, dc_id, input_type = _parse_session_input(text)
        parsed_ok = True
    except ValueError as exc:
        parsed_ok = False
        last_err = exc

    # If we're not awaiting and it doesn't parse as a session, let other text handlers process it
    if not (is_awaiting or parsed_ok):
        return

    if text.lower() == "/cancel":
        LOGOUT_STATE.pop(message.from_user.id, None)
        await message.reply_text("❌ Logout aborted.")
        raise StopPropagation

    if not parsed_ok:
        # We were awaiting input, but what was sent is invalid
        await message.reply_text(f"❌ {last_err}")
        raise StopPropagation

    LOGOUT_STATE.pop(message.from_user.id, None)
    try:
        await _run_session_import(message, auth_key, dc_id, input_type)
    except Exception as exc:
        logging.error(f"[IMPORT_ERROR] {exc}", exc_info=True)
        try:
            await message.reply_text(f"❌ Session import failed: `{type(exc).__name__}: {exc}`")
        except Exception:
            pass

    # Stop propagation so other text handlers don't fire
    raise StopPropagation


@app.on_message(filters.text & ~filters.command(["start"]), group=-1)
async def handle_new_session_prompt(_, message):
    state = PROMPT_STATE.get(message.from_user.id)
    if not state or state.get("action") != "new_sessions_password":
        return

    text = (message.text or "").strip()
    if not text:
        return

    PROMPT_STATE.pop(message.from_user.id, None)
    password_text = "/no2fa" if text.lower() == "/no2fa" else text
    async with register_user_task(message.from_user.id, message):
        await run_new_session_shift(state["source_message"], state["zip_id"], state["target_format"], password_text)
    raise StopPropagation


@app.on_message(filters.command(["done", "finish"]))
async def command_done_handler(_, message):
    user_id = message.from_user.id
    if user_id in CREATE_STATE:
        return await send_created_zip(message.chat.id, user_id)
    
    state = PROMPT_STATE.get(user_id)
    if state and state.get("action") == "merge":
        zip_id = state.get("zip_id")
        merge_files = list(state.get("merge_files", []))
        merge_dir = state.get("merge_dir")
        PROMPT_STATE.pop(user_id, None)
        await send_merged_zip(message.chat.id, zip_id, merge_files)
        if merge_dir:
            shutil.rmtree(merge_dir, ignore_errors=True)
        return await app.edit_message_text(
            state["chat_id"],
            state["menu_message_id"],
            zip_menu_text(zip_id),
            reply_markup=operations_keyboard(zip_id),
        )
    
    return await message.reply_text("ℹ️ No active session creation or merge operation in progress.")


@app.on_message(filters.text & ~filters.command(["start", "done", "finish"]))
async def prompt_handler(_, message):
    async with register_user_task(message.from_user.id, message):
        create_state = CREATE_STATE.get(message.from_user.id)
        if create_state:
            text = (message.text or "").strip()
            clean_text = text.lower().strip()
            if clean_text in ("/done", "done", "finish", "/finish") or clean_text.startswith(("/done@", "/done ")):
                return await send_created_zip(message.chat.id, message.from_user.id)

            step = create_state["step"]

            if step == "phone":
                if clean_text in ("/done", "done", "finish", "/finish") or clean_text.startswith(("/done@", "/done ")):
                    return await send_created_zip(message.chat.id, message.from_user.id)
                # Check if it looks like a hex key or string session instead of a phone number (> 20 characters)
                if len(text.replace("+", "").replace(" ", "").strip()) > 20:
                    # Clean up the session creation flow state
                    create_state = CREATE_STATE.pop(message.from_user.id, None)
                    if create_state:
                        client = create_state.get("client")
                        if client:
                            if create_state.get("format") == "pyrogram":
                                try:
                                    await client.disconnect()
                                except Exception:
                                    pass
                            else:
                                await safe_telethon_disconnect(client)
                        shutil.rmtree(create_state.get("dir"), ignore_errors=True)
                    
                    try:
                        auth_key, dc_id, input_type = _parse_session_input(text)
                        await _run_session_import(message, auth_key, dc_id, input_type)
                    except ValueError as exc:
                        await message.reply_text(f"❌ The text was too long for a phone number and failed to parse as a session/hex key:\n{exc}")
                    return
                
                status_msg = await message.reply_text("⏳ Processing phone number and requesting code...")
                try:
                    await request_login_code(message.from_user.id, text)
                    await status_msg.delete()
                    return await message.reply_text("📬 Verification code sent. Enter it here.", reply_markup=cancel_keyboard("create"))
                except Exception as exc:
                    await status_msg.delete()
                    err_msg = str(exc)
                    if "FloodWait" in type(exc).__name__:
                        secs = getattr(exc, "value", getattr(exc, "seconds", None))
                        if secs:
                            err_msg = f"A wait of {secs} seconds is required (Telegram Rate Limit)."
                    return await message.reply_text(f"❌ Could not send code: {err_msg}", reply_markup=cancel_keyboard("create"))

            if step == "otp":
                status_msg = await message.reply_text("⏳ Submitting verification code...")
                try:
                    result = await complete_login_with_otp(message.from_user.id, text)
                    await status_msg.delete()
                except Exception as exc:
                    await status_msg.delete()
                    err_msg = str(exc)
                    if "FloodWait" in type(exc).__name__:
                        secs = getattr(exc, "value", getattr(exc, "seconds", None))
                        if secs:
                            err_msg = f"A wait of {secs} seconds is required (Telegram Rate Limit)."
                    return await message.reply_text(f"❌ Login failed: {err_msg}", reply_markup=cancel_keyboard("create"))
                
                if result == "password":
                    return await message.reply_text("🔐 Two-step auth detected. Enter the password.", reply_markup=cancel_keyboard("create"))
                return await message.reply_text(
                    f"✅ Account authenticated.\n\n{next_creation_prompt(CREATE_STATE[message.from_user.id])}",
                    reply_markup=cancel_keyboard("create"),
                )

            if step == "password":
                status_msg = await message.reply_text("⏳ Submitting password...")
                try:
                    await complete_login_with_password(message.from_user.id, text)
                    await status_msg.delete()
                except Exception as exc:
                    await status_msg.delete()
                    return await message.reply_text(f"❌ Wrong password: {exc}", reply_markup=cancel_keyboard("create"))
                return await message.reply_text(
                    f"✅ Account authenticated.\n\n{next_creation_prompt(CREATE_STATE[message.from_user.id])}",
                    reply_markup=cancel_keyboard("create"),
                )

        state = PROMPT_STATE.get(message.from_user.id)
        if not state:
            # Universal message processor
            # User can do /start and send anything and bot will process it
            text_input = (message.text or "").strip()
            if not text_input:
                return

            # Check if user sent session string(s) or hex key(s)
            sessions_found = _parse_all_session_inputs(text_input)
            if sessions_found:
                if len(sessions_found) == 1:
                    auth_key, dc_id, input_type = sessions_found[0]
                    return await _run_session_import(message, auth_key, dc_id, input_type)
                else:
                    return await _run_bulk_session_import(message, sessions_found)

            # Check if user sent a phone number
            clean_digits = re.sub(r'[\s\-+()]+', '', text_input)
            if clean_digits.isdigit() and 7 <= len(clean_digits) <= 16:
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📲 Create Telethon Pack", callback_data="create|telethon"),
                        InlineKeyboardButton("📲 Create Pyrogram Pack", callback_data="create|pyrogram")
                    ],
                    [InlineKeyboardButton("🔙 Back to Start", callback_data="start_menu")]
                ])
                return await message.reply_text(
                    f"📱 **Phone Number Detected:** `+{clean_digits}`\n\n"
                    "To generate a new session file or ZIP pack using this number, "
                    "click one of the buttons below to begin verification login:",
                    reply_markup=keyboard
                )

            # Silently ignore non-credential text without prompt state to prevent DM spam loops
            return
        zip_id = state.get("zip_id")
        action = state["action"]
        text = (message.text or "").strip()

        if action == "mail_setup_email":
            raw_email = text.strip()
            if "@" not in raw_email or len(raw_email.split("@")) != 2 or "." not in raw_email.split("@")[1]:
                return await message.reply_text("❌ Invalid email format! Please send a valid email address (e.g. `user@gmail.com`).")
            
            domain = raw_email.split("@")[1].lower()
            if domain in ("outlook.com", "hotmail.com", "live.com", "msn.com"):
                return await message.reply_text(
                    "⚠️ **Outlook / Hotmail Not Supported for IMAP**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Microsoft officially disabled **Basic Authentication** & App Passwords for all personal `@outlook.com`, `@hotmail.com`, and `@live.com` accounts on **September 16, 2024** (returning error: `Basic authentication is disabled`).\n\n"
                    "👉 **What you can use instead:**\n"
                    "1. **Gmail** (Recommended ⭐): Fully supported! Generates 2,000+ permutations delivering to one inbox.\n"
                    "2. **Yahoo / Mail.ru / Yandex / Zoho / AOL**: Fully supported with App Passwords.\n"
                    "3. **Free Temp-Mail**: 100% automated instant binding with zero setup!\n\n"
                    "Please send a **Gmail** address or another supported email, or send /cancel to use Free Temp-Mail.",
                    reply_markup=cancel_keyboard("prompt")
                )

            pinfo = mail_engine.detect_imap_provider(raw_email)
            state["email"] = raw_email
            state["pinfo"] = pinfo
            state["action"] = "mail_setup_password"
            
            hint = pinfo.get("setup_hint", "")
            example = pinfo.get("example", "`your_16_char_password`")
            return await message.reply_text(
                f"📧 **Email:** `{raw_email}`\n"
                f"🌐 **Provider Detected:** **{pinfo['provider']}** (`{pinfo['host']}:{pinfo['port']}`)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💡 **Setup Instructions:**\n{hint}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 **Enter your App Password below:**\n\n"
                f"📝 **Format Example:** {example}\n"
                f"*(Spaces & hyphens are automatically removed)*\n\n"
                f"⚠️ **Note:** Do NOT send your main email login password! Use a generated App Password.",
                reply_markup=cancel_keyboard("prompt")
            )

        if action == "mail_setup_password":
            app_pwd = text.replace(" ", "").replace("-", "").strip()
            if not app_pwd or len(app_pwd) < 4:
                return await message.reply_text("❌ Password too short. Please send your valid App Password.")
            
            email_addr = state["email"]
            pinfo = state["pinfo"]
            
            test_msg = await message.reply_text("⏳ Testing IMAP connection to your mailbox...")
            imap_client = mail_engine.IMAPMailClient(
                email_addr, app_pwd, pinfo["host"], pinfo["port"]
            )
            try:
                await imap_client.test_connection()
            except Exception as conn_err:
                await test_msg.delete()
                err_str = str(conn_err)
                err_lower = err_str.lower()
                is_gmail = any(g in email_addr.lower() for g in ["gmail", "googlemail"])
                is_ms = any(m in email_addr.lower() for m in ["outlook", "hotmail", "live", "msn"]) or "basic authentication is disabled" in err_lower
                
                extra_hint = ""
                example = pinfo.get("example", "`your_app_password`")
                if is_ms:
                    extra_hint = (
                        "\n\n⚠️ **Microsoft Discontinued Basic Auth:**\n"
                        "Microsoft permanently shut down App Password & IMAP login for Outlook and Hotmail accounts on Sept 16, 2024 (returning error: `Basic authentication is disabled`).\n\n"
                        "👉 **Solution:** Please switch to **Gmail** (which fully supports Google App Passwords) or use **Free Temp-Mail**!"
                    )
                elif is_gmail:
                    extra_hint = (
                        "\n\n📌 **Gmail Fix Checklist:**\n"
                        "1. **Enable IMAP**: Open Gmail in a browser → click ⚙️ Settings → 'See all settings' → 'Forwarding and POP/IMAP' tab → select **'Enable IMAP'** → click **'Save Changes'**.\n"
                        "2. **App Password**: Go to `myaccount.google.com/apppasswords` with 2-Step Verification turned ON, create a new App Password, and paste it here.\n"
                        "3. **Do not use regular password**: Your normal Google account password will fail."
                    )
                return await message.reply_text(
                    f"❌ **IMAP Connection Failed**\n\n`{conn_err}`\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💡 **Please verify:**\n"
                    f"1. IMAP is enabled in your email provider settings\n"
                    f"2. You are using an **App Password** (not your regular account password)\n"
                    f"3. Format Example: {example}"
                    f"{extra_hint}\n\n"
                    f"Send your App Password again or send /cancel.",
                    reply_markup=cancel_keyboard("prompt")
                )
            
            await test_msg.delete()
            
            # Connection succeeded! Generate permutations
            try:
                gen = mail_engine.PermutationGenerator(email_addr)
                total_perms = gen.total_permutations
            except Exception:
                total_perms = 1
            
            meta = load_meta()
            mail_engine.set_user_mail_config(meta, message.from_user.id, {
                "email": email_addr,
                "app_password": app_pwd,
                "imap_host": pinfo["host"],
                "imap_port": pinfo["port"],
                "provider": pinfo["provider"],
                "total_permutations": total_perms,
                "used_permutations": [],
                "accounts_bound": 0,
                "updated_at": int(time.time()),
            })
            save_meta(meta)
            
            zip_id = state.get("zip_id")
            source_msg = state.get("source_message")
            PROMPT_STATE.pop(message.from_user.id, None)
            
            success_text = (
                "✅ **Mail Configuration Verified & Saved!**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 Email: `{email_addr}`\n"
                f"🌐 Provider: **{pinfo['provider']}**\n"
                f"⚡ Available Permutations: `{total_perms:,}` unique variants\n\n"
                "🔒 Your credentials are saved securely in your isolated vault."
            )
            await message.reply_text(success_text)
            
            if zip_id and source_msg:
                try:
                    await safe_edit(
                        source_msg,
                        f"✉️ **Mail Binding Options — Pack #{zip_id}**\n\nSelf-mail `{email_addr}` is now configured and ready!",
                        reply_markup=mail_binding_keyboard(zip_id, has_self_mail=True)
                    )
                except Exception:
                    pass
            return

        if action == "merge":
            clean_m = text.lower().strip()
            if clean_m in ("/done", "done", "finish", "/finish") or clean_m.startswith(("/done@", "/done ")):
                merge_files = list(state.get("merge_files", []))
                merge_dir = state.get("merge_dir")
                PROMPT_STATE.pop(message.from_user.id, None)
                await send_merged_zip(message.chat.id, zip_id, merge_files)
                if merge_dir:
                    shutil.rmtree(merge_dir, ignore_errors=True)
                return await app.edit_message_text(
                    state["chat_id"],
                    state["menu_message_id"],
                    zip_menu_text(zip_id),
                    reply_markup=operations_keyboard(zip_id),
                )
            
            try:
                auth_key, dc_id, input_type = _parse_session_input(text)
                status_msg = await message.reply_text(f"⏳ Connecting to `{input_type}` to verify...")
                client = None
                try:
                    client = await _connect_from_auth_key(auth_key, dc_id)
                    dc_id = client.session.dc_id
                    if not await client.is_user_authorized():
                        return await status_msg.edit_text("❌ Session is not authorized (already dead or invalid).")
                    
                    me = await client.get_me()
                    if getattr(me, "bot", False):
                        return await status_msg.edit_text("🤖 Bot sessions are not supported for merge.")
                        
                    merge_dir = state.get("merge_dir")
                    ip, port = DC_TABLE.get(dc_id, DC_TABLE[2])
                    session_name = f"merge_str_{int(time.time())}_{random.randint(1000, 9999)}"
                    session_path = os.path.join(merge_dir, session_name)
                    session_obj = SQLiteSession(session_path)
                    session_obj.set_dc(dc_id, ip, port)
                    session_obj.auth_key = AuthKey(data=auth_key)
                    session_obj.save()
                    session_obj.close()
                    
                    state["merge_files"] = sorted(
                        os.path.join(merge_dir, name)
                        for name in os.listdir(merge_dir)
                        if name.endswith(".session")
                    )
                    
                    return await status_msg.edit_text(f"✅ Added `1` session for merge (Total: `{len(state['merge_files'])}`). Send more files/strings or /done.")
                except asyncio.TimeoutError:
                    return await status_msg.edit_text("❌ **Connection timed out (Telegram rejected the key).**\n\nTelegram did not respond. This means the session key is **completely dead, revoked, or unrecognized** by Telegram.")
                except Exception as exc:
                    return await status_msg.edit_text(f"❌ Failed to verify session: `{type(exc).__name__}: {exc}`")
                finally:
                    if client:
                        await safe_telethon_disconnect(client)
            except ValueError:
                pass

            return await message.reply_text("🧷 Send zip files, .session files, text sessions, or send /done when finished.")

        if action == "rename":
            PROMPT_STATE.pop(message.from_user.id, None)
            await send_renamed_zip(message.chat.id, zip_id, text)
            return await app.edit_message_text(
                state["chat_id"],
                state["menu_message_id"],
                zip_menu_text(zip_id),
                reply_markup=operations_keyboard(zip_id),
            )

        if action == "split":
            try:
                counts = [int(part.strip()) for part in text.split(",") if part.strip()]
                if not counts or any(num <= 0 for num in counts):
                    raise ValueError
            except ValueError:
                return await message.reply_text("❌ Enter numbers like: 3,5,2")
            PROMPT_STATE.pop(message.from_user.id, None)
            await send_split_zips(message.chat.id, zip_id, counts)
            return await app.edit_message_text(
                state["chat_id"],
                state["menu_message_id"],
                zip_menu_text(zip_id),
                reply_markup=operations_keyboard(zip_id),
            )

        if action == "protect":
            clear_all = text.lower() == "/clear"
            devices = [part.strip() for part in text.split(",") if part.strip()]
            updated = toggle_user_protected_devices(message.from_user.id, devices, clear_all=clear_all)
            PROMPT_STATE.pop(message.from_user.id, None)
            if updated:
                await message.reply_text("✅ Whitelist updated:\n" + ", ".join(f"`{item}`" for item in updated))
            else:
                await message.reply_text("✅ Whitelist has been reset.")
            return await app.edit_message_text(
                state["chat_id"],
                state["menu_message_id"],
                zip_menu_text(zip_id),
                reply_markup=operations_keyboard(zip_id),
            )

        if action == "tfa_set_new":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_twofa_operation(state["source_message"], zip_id, "set", new_password=text)
            return

        if action == "tfa_edit_current":
            state["action"] = "tfa_edit_new"
            state["current_password"] = text
            return await app.edit_message_text(
                state["chat_id"],
                state["menu_message_id"],
                "🔐 Enter the new two-step password.",
                reply_markup=None,
            )

        if action == "tfa_edit_new":
            current_password = state.get("current_password")
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_twofa_operation(state["source_message"], zip_id, "edit", current_password=current_password, new_password=text)
            return

        if action == "tfa_remove_current":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_twofa_operation(state["source_message"], zip_id, "remove", current_password=text)
            return

        if action == "change_bio":
            PROMPT_STATE.pop(message.from_user.id, None)
            new_bio = "" if text.lower() == "/clear" else text
            await run_change_bio(state["source_message"], zip_id, new_bio)
            return

        if action == "change_name":
            PROMPT_STATE.pop(message.from_user.id, None)
            parts = text.split("|", 1)
            first_name = parts[0].strip()
            last_name = parts[1].strip() if len(parts) > 1 else ""
            if not first_name:
                return await message.reply_text("❌ First name cannot be empty.")
            await run_change_name(state["source_message"], zip_id, first_name, last_name)
            return

        if action == "change_username":
            PROMPT_STATE.pop(message.from_user.id, None)
            if not text:
                return await message.reply_text("❌ Username prefix cannot be empty.")
            await run_change_username(state["source_message"], zip_id, text)
            return

        if action == "join_chat":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_join_chat(state["source_message"], zip_id, text)
            return

        if action == "vote_poll":
            PROMPT_STATE.pop(message.from_user.id, None)
            parts = text.split("|")
            if len(parts) < 3:
                return await message.reply_text("❌ Invalid format. Use Target|MessageID|OptionIndex")
            target = parts[0].strip()
            msg_id = int(parts[1].strip())
            opt_idx = int(parts[2].strip())
            await run_vote_poll(state["source_message"], zip_id, target, msg_id, opt_idx)
            return

        if action == "scrape_members":
            PROMPT_STATE.pop(message.from_user.id, None)
            if not text:
                return await message.reply_text("❌ Target cannot be empty.")
            await run_scrape_members(state["source_message"], zip_id, text.strip())
            return

        if action == "leave_chat":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_leave_chat(state["source_message"], zip_id, text)
            return

        if action == "send_msg":
            parts = text.split("|", 1)
            if len(parts) < 2:
                return await message.reply_text("❌ Format must be: `Target|Message`")
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_send_msg(state["source_message"], zip_id, parts[0].strip(), parts[1].strip())
            return

        if action == "report_target":
            parts = text.split("|", 1)
            if len(parts) < 2:
                return await message.reply_text("❌ Format must be: `Target|Reason` (e.g. `@target|spam`)")
            target = parts[0].strip()
            reason = parts[1].strip().lower()
            allowed = ["spam", "violence", "pornography", "child_abuse", "copyright", "fake", "drugs", "personal_details", "other"]
            if reason not in allowed:
                return await message.reply_text(f"❌ Reason must be one of: {', '.join([f'`{r}`' for r in allowed])}")
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_report_target(state["source_message"], zip_id, target, reason)
            return

        if action == "bc_groups":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_broadcast(state["source_message"], zip_id, "groups", text)
            return

        if action == "bc_dms":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_broadcast(state["source_message"], zip_id, "dms", text)
            return

        if action == "bc_all":
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_broadcast(state["source_message"], zip_id, "all", text)
            return

        if action == "increase_views":
            parts = text.split("|")
            target, msg_id = None, None
            if len(parts) >= 1:
                target, msg_id = parse_post_link(parts[0])
            if not target or not msg_id:
                if len(parts) >= 2:
                    target = parts[0].strip()
                    try:
                        msg_id = int(parts[1].strip())
                    except ValueError:
                        pass
            if not target or not msg_id:
                return await message.reply_text("❌ Format must be: `Target|MessageID` (e.g. `@channel|123` or link `https://t.me/channel/123`)")
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_increase_views(state["source_message"], zip_id, target, msg_id)
            return

        if action == "react_post":
            parts = text.split("|")
            target, msg_id, emoji = None, None, None
            if len(parts) >= 1:
                target, msg_id = parse_post_link(parts[0])
                if target and msg_id:
                    if len(parts) >= 2:
                        emoji = parts[1].strip()
            
            if not target or not msg_id or not emoji:
                if len(parts) >= 3:
                    target = parts[0].strip()
                    try:
                        msg_id = int(parts[1].strip())
                    except ValueError:
                        pass
                    emoji = parts[2].strip()
            
            if not target or not msg_id or not emoji:
                return await message.reply_text("❌ Format must be: `Target|MessageID|Emoji` (e.g. `@channel|123|👍` or link `https://t.me/channel/123|🔥`)\n\n💡 Use `random` as the emoji for random reactions per session.")
            PROMPT_STATE.pop(message.from_user.id, None)
            await run_react_post(state["source_message"], zip_id, target, msg_id, emoji)
            return
@app.on_message(filters.photo & ~filters.me & ~filters.command(["start"]))
async def photo_prompt_handler(_, message):
    state = PROMPT_STATE.get(message.from_user.id)
    if not state or state.get("action") != "change_avatar":
        _photo_rows = [[InlineKeyboardButton("🔙 Back to Start", callback_data="start_menu")]]
        if is_admin(message.from_user.id):
            _photo_rows.insert(0, [InlineKeyboardButton("📦 My Packs", callback_data="user_packs:0")])
        keyboard = InlineKeyboardMarkup(_photo_rows)
        return await message.reply_text(
            "🖼 **Photo Received**\n\n"
            "To set this photo as profile picture for a session pack:\n"
            "1. Open your pack via **My Packs** below\n"
            "2. Tap **🖼 Change Avatar**\n"
            "3. Send the photo when prompted.",
            reply_markup=keyboard
        )
    
    PROMPT_STATE.pop(message.from_user.id, None)
    status = await message.reply_text("📥 Downloading photo...")
    downloaded = None
    try:
        downloaded = await message.download()
        async with register_user_task(message.from_user.id, message):
            await run_change_avatar(state["source_message"], state["zip_id"], downloaded)
    except Exception as e:
        await status.edit_text(f"❌ Failed to process photo: {e}")
    finally:
        if downloaded and os.path.exists(downloaded):
            try:
                os.remove(downloaded)
            except:
                pass


@app.on_callback_query(filters.regex(r"^ignore$"))
async def ignore_callback(_, call: CallbackQuery):
    await call.answer("This is a category header, not a button! 👇 Please use the options below it.", show_alert=True)


@app.on_callback_query(filters.regex(r"^zip\|"))
async def zip_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    parts = call.data.split("|")
    if len(parts) < 3:
        return await call.answer("❌ Invalid action.", show_alert=True)
    action = parts[1]
    try:
        zip_id = int(parts[2])
    except (ValueError, IndexError):
        return await call.answer("❌ Invalid pack.", show_alert=True)
    await call.answer()

    if not zip_record(zip_id):
        return await call.message.reply_text("❌ Pack not found.")
    if not zip_belongs_to_user(zip_id, call.from_user.id):
        return await call.message.reply_text("🚫 You don't have access to this pack.")

    # Guard against concurrent operations on the same pack to prevent account revocation & SQLite locks
    is_busy = (str(zip_id) in RUNNING_PACKS or int(zip_id) in RUNNING_PACKS)
    if is_busy and action not in ("cancel_destroy", "cancel_clean", "clean_menu", "confirm_destroy", "open", "info", "export_menu", "comm_menu", "twofa", "devices"):
        return await call.answer(f"⚠️ Pack #{zip_id} is currently running an operation. Please wait for it to finish!", show_alert=True)

    try:
      async with register_user_task(call.from_user.id, call):
        if action == "check_sessions":
            async with lock_pack(zip_id):
                return await run_check_sessions(call, zip_id)
        if action == "check_spam":
            return await run_check_spam(call, zip_id)
        if action == "check_mail":
            return await run_check_mail(call, zip_id)
        if action == "check_age":
            return await run_check_age(call, zip_id)
        if action == "check_all":
            return await run_check_all(call, zip_id)
        if action == "read_otp":
            return await start_otp_flow(call, zip_id)
        if action == "confirm_destroy":
            await call.answer("⚠️ WARNING: This will permanently log out and terminate all accounts in this pack!", show_alert=True)
            return await show_destroy_confirm(call, zip_id)
        if action == "cancel_destroy":
            return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        if action == "destroy":
            return await destroy_zip_sessions(call, zip_id)
        if action == "clean_menu":
            return await show_clean_menu(call, zip_id)
        if action == "clean_confirm":
            mode = parts[3] if len(parts) > 3 else "chats"
            return await show_clean_confirm(call, zip_id, mode)
        if action == "do_clean":
            mode = parts[3] if len(parts) > 3 else "chats"
            return await run_clean_accounts(call, zip_id, mode)
        if action == "cancel_clean":
            return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        
        if action == "change_username":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "change_username")
        if action == "mark_read":
            return await run_mark_read(call, zip_id)
        if action == "new_sessions":
            return await safe_edit(
                call.message,
                f"🆕 Pack #{zip_id} Session Generator\n\nGenerates fresh sessions from existing ones. Current sessions are logged out on success.\n\nSelect the output format.",
                reply_markup=new_session_format_keyboard(zip_id),
            )
        if action == "twofa":
            return await show_twofa_menu(call, zip_id)
        if action == "devices":
            return await show_devices_menu(call, zip_id)
        if action == "rename":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "rename")
        if action == "split":
            return await safe_edit(call.message, f"✂️ **Split Pack #{zip_id}**\n\nChoose how you want to split this pack:", reply_markup=split_options_keyboard(zip_id))
        if action == "split_quantity":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "split")
        if action == "split_country":
            return await run_split_country(call, zip_id)
        if action == "split_spam":
            return await run_split_spam(call, zip_id)
        if action == "convert_format":
            return await run_convert_format(call, zip_id)
        if action == "convert_to_pyro":
            return await run_convert_to_pyro(call, zip_id)
        if action == "merge":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "merge")
        if action == "open":
            return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        if action == "info":
            return await show_pack_info(call, zip_id)
        if action in ("download", "download_menu"):
            return await show_download_menu(call, zip_id)
        if action == "dl_format":
            target_fmt = parts[3] if len(parts) > 3 else "auto"
            return await download_pack(call, zip_id, target_format=target_fmt)
        if action == "dl_raw":
            target_fmt = parts[3] if len(parts) > 3 else "telethon"
            return await download_raw_sessions(call, zip_id, target_format=target_fmt)
        if action == "profiles":
            return await run_profiles(call, zip_id)
        if action == "dc_info":
            return await run_dc_info(call, zip_id)
        if action == "change_bio":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "change_bio")
        if action == "change_name":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "change_name")
        if action == "export_menu":
            return await show_export_menu(call, zip_id)
        if action == "export_strings":
            return await export_sessions_format(call, zip_id, "string")
        if action == "export_hex":
            return await export_sessions_format(call, zip_id, "hex")
        if action == "export_both":
            return await export_sessions_format(call, zip_id, "both")
        if action == "comm_menu":
            return await show_comm_ops_menu(call, zip_id)
        if action == "change_avatar":
            return await prompt_for_input(call.message, call.from_user.id, zip_id, "change_avatar")
        if action == "confirm_delete_avatar":
            return await show_delete_avatar_confirm(call, zip_id)
        if action == "cancel_delete_avatar":
            return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        if action == "delete_avatar":
            return await run_delete_avatars(call, zip_id)
        if action == "direct_devices_dash":
            text, markup = await render_direct_devices(zip_id)
            if markup:
                await safe_edit(call.message, text, reply_markup=markup)
            else:
                await safe_edit(call.message, text, reply_markup=None)
            return
        if action == "bind_mail":
            return await show_mail_binding_menu(call, zip_id)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.exception(f"[zip_actions] Unhandled error in action={action} zip_id={zip_id}")
        try:
            await safe_edit(call.message, f"❌ Operation failed: `{type(e).__name__}`\n\nPlease try again.", reply_markup=operations_keyboard(zip_id))
        except Exception:
            pass


async def show_mail_binding_menu(call: CallbackQuery, zip_id: int):
    user_id = call.from_user.id
    meta = load_meta()
    mail_cfg = mail_engine.get_user_mail_config(meta, user_id)
    has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
    
    email_display = mail_cfg.get("email", "Not configured")
    if has_self:
        total_p = mail_cfg.get("total_permutations", 0)
        used_p = len(mail_cfg.get("used_permutations", []))
        rem_p = max(0, total_p - used_p)
        self_status = f"✅ `{email_display}` ({rem_p:,} permutations remaining)"
    else:
        self_status = "⚠️ No email configured yet"
    
    text = (
        f"✉️ **Mail Binding Options — Pack #{zip_id}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "**Available Binding Modes:**\n\n"
        "🌐 **Free Temp-Mail (Auto)**\n"
        " • Uses zero-cost disposable email API\n"
        " • Automatically creates email and fetches Telegram OTP\n"
        " • Zero setup required\n\n"
        "📧 **Self-Mail (IMAP Multi-Permutations)**\n"
        " • Uses your own email (Gmail, Outlook, Yahoo, Mail.ru, etc.)\n"
        " • Generates thousands of case + dot permutations\n"
        " • All OTPs deliver to your single inbox\n"
        f" • Current Self-Mail: {self_status}\n\n"
        "🛡️ **Safety Enforcement:**\n"
        " • Accounts are bound **strictly 1-by-1 sequentially**\n"
        " • Anti-freeze delays between accounts\n"
        " • Live audit updates sent as each account is bound"
    )
    await safe_edit(call.message, text, reply_markup=mail_binding_keyboard(zip_id, has_self_mail=has_self))


@app.on_callback_query(filters.regex(r"^mailbind\|"))
async def mailbind_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    
    parts = call.data.split("|")
    action = parts[1]
    zip_id = int(parts[2])
    
    if action == "back":
        await call.answer()
        return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
    
    if action == "setup":
        await call.answer()
        PROMPT_STATE[call.from_user.id] = {
            "action": "mail_setup_email",
            "zip_id": zip_id,
            "chat_id": call.message.chat.id,
            "menu_message_id": call.message.id,
            "source_message": call.message,
        }
        return await safe_edit(
            call.message,
            "📧 **Self-Mail Setup: Step 1 of 2**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Send your email address (e.g. `yourname@gmail.com` or `user@outlook.com`):\n\n"
            "• **Gmail & Providers**: Case and dot permutations will be generated automatically\n"
            "• **Single Inbox**: All Telegram OTP verification codes will arrive in this one inbox\n"
            "• **Step 2**: You will be prompted for your provider's **App Password** (with format example and setup guide)",
            reply_markup=cancel_keyboard("prompt")
        )
    
    if action == "self":
        meta = load_meta()
        mail_cfg = mail_engine.get_user_mail_config(meta, call.from_user.id)
        has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
        if not has_self:
            await call.answer()
            PROMPT_STATE[call.from_user.id] = {
                "action": "mail_setup_email",
                "zip_id": zip_id,
                "chat_id": call.message.chat.id,
                "menu_message_id": call.message.id,
                "source_message": call.message,
            }
            return await safe_edit(
                call.message,
                "📧 **Self-Mail Setup: Step 1 of 2**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ **Self-Mail is not configured yet.** Please set it up below:\n\n"
                "Send your email address (e.g. `yourname@gmail.com` or `user@outlook.com`):\n\n"
                "• **Gmail & Providers**: Case and dot permutations will be generated automatically\n"
                "• **Single Inbox**: All Telegram OTP verification codes will arrive in this one inbox\n"
                "• **Step 2**: You will be prompted for your provider's **App Password**",
                reply_markup=cancel_keyboard("prompt")
            )

    if action in ("temp", "self"):
        is_busy = (str(zip_id) in RUNNING_PACKS or int(zip_id) in RUNNING_PACKS)
        if is_busy:
            return await call.answer(f"⚠️ Pack #{zip_id} is currently running an operation. Please wait!", show_alert=True)
        await call.answer()
        async with register_user_task(call.from_user.id, call.message), lock_pack(zip_id):
            return await run_bind_email(call, zip_id, mode=action)


@app.on_callback_query(filters.regex(r"^mail_"))
async def mail_settings_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    
    data = call.data
    user_id = call.from_user.id
    meta = load_meta()
    mail_cfg = mail_engine.get_user_mail_config(meta, user_id)
    has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
    
    if data == "mail_settings_open":
        await call.answer()
        return await render_mail_settings_menu(call.message, user_id)
    
    if data == "mail_settings_back":
        await call.answer()
        return await safe_edit(call.message, start_menu_text(), reply_markup=start_keyboard(user_id))
    
    if data == "mail_setup_start":
        await call.answer()
        PROMPT_STATE[user_id] = {
            "action": "mail_setup_email",
            "chat_id": call.message.chat.id,
            "menu_message_id": call.message.id,
            "source_message": call.message,
        }
        return await safe_edit(
            call.message,
            "📧 **Self-Mail Setup: Step 1 of 2**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Send your email address (e.g. `yourname@gmail.com` or `user@outlook.com`):\n\n"
            "• **Gmail & Providers**: Case and dot permutations will be generated automatically\n"
            "• **Single Inbox**: All Telegram OTP verification codes will arrive in this one inbox\n"
            "• **Step 2**: You will be prompted for your provider's **App Password** (with format example and setup guide)",
            reply_markup=cancel_keyboard("prompt")
        )
    
    if data == "mail_setup_test":
        if not has_self:
            return await call.answer("❌ No email configured yet.", show_alert=True)
        await call.answer("Testing IMAP connection...")
        client = mail_engine.IMAPMailClient(
            mail_cfg["email"],
            mail_cfg["app_password"],
            mail_cfg.get("imap_host"),
            mail_cfg.get("imap_port")
        )
        try:
            await client.test_connection()
            return await call.answer("✅ IMAP Connection Test PASSED!", show_alert=True)
        except Exception as e:
            return await call.answer(f"❌ Connection Failed:\n{e}", show_alert=True)
    
    if data == "mail_setup_delete":
        mail_engine.delete_user_mail_config(meta, user_id)
        save_meta(meta)
        await call.answer("🗑️ Your mail configuration has been removed.", show_alert=True)
        return await render_mail_settings_menu(call.message, user_id)


async def render_mail_settings_menu(message, user_id: int):
    meta = load_meta()
    mail_cfg = mail_engine.get_user_mail_config(meta, user_id)
    has_self = bool(mail_cfg.get("email") and mail_cfg.get("app_password"))
    
    if has_self:
        email_str = mail_cfg.get("email")
        provider = mail_cfg.get("provider", "Custom IMAP")
        host = mail_cfg.get("imap_host", "Unknown")
        port = mail_cfg.get("imap_port", 993)
        total_p = mail_cfg.get("total_permutations", 0)
        used_p = len(mail_cfg.get("used_permutations", []))
        bound_count = mail_cfg.get("accounts_bound", used_p)
        rem_p = max(0, total_p - used_p)
        
        text = (
            "📧 **Mail Settings & Multiplexer Vault**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **Your Configured Self-Mail:**\n"
            f" • Email: `{email_str}`\n"
            f" • Provider: **{provider}** (`{host}:{port}`)\n"
            f" • App Password: `{'•' * 8}` (Isolated Store)\n\n"
            f"⚡ **Permutation Stats:**\n"
            f" • Total Permutations: `{total_p:,}` unique variants\n"
            f" • Accounts Bound: `{bound_count:,}`\n"
            f" • Remaining Available: `{rem_p:,}`\n\n"
            "🔒 *Your credentials are kept isolated to your user account and cannot be seen by other users.*"
        )
    else:
        text = (
            "📧 **Mail Settings & Multiplexer Vault**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ **No Self-Mail Configured Yet**\n\n"
            "Add your personal email (Gmail, Outlook, Yahoo, etc.) to enable automatic multi-account binding via case & dot permutations.\n\n"
            "• Single inbox receives all OTPs\n"
            "• Each Telegram account gets a unique permutation\n"
            "• Zero code confusion with strict 1-by-1 binding"
        )
    await safe_edit(message, text, reply_markup=mail_settings_keyboard(has_self_mail=has_self))


@app.on_message(filters.command(["mail"]))
async def mail_command(_, message):
    if is_rate_limited(message.from_user.id):
        return await message.reply_text("Please wait...", quote=True)
    msg = await message.reply_text("⏳ Loading Mail Settings...")
    await render_mail_settings_menu(msg, message.from_user.id)


@app.on_callback_query(filters.regex(r"^create\|"))
async def create_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, session_format = call.data.split("|")
    await call.answer()
    await start_creation_flow(call.message.chat.id, call.from_user.id, session_format)


@app.on_callback_query(filters.regex(r"^create_done$"))
async def create_done_action(_, call: CallbackQuery):
    await call.answer()
    await send_created_zip(call.message.chat.id, call.from_user.id)


@app.on_callback_query(filters.regex(r"^shift\|"))
async def new_session_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, action, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    await call.answer()

    if action == "back":
        return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))

    PROMPT_STATE[call.from_user.id] = {
        "zip_id": zip_id,
        "action": "new_sessions_password",
        "target_format": action,
        "chat_id": call.message.chat.id,
        "menu_message_id": call.message.id,
        "source_message": call.message,
    }
    await safe_edit(
        call.message,
        f"🆕 Pack #{zip_id} Session Generator\n\nTarget: {action.title()}\n\nGenerates fresh sessions from existing ones. Current sessions are logged out on success.\n\nEnter the current 2FA password, or send /no2fa if none is set.\n⚠️ Incorrect passwords will cause failures.",
        reply_markup=cancel_keyboard("prompt"),
    )


@app.on_callback_query(filters.regex(r"^tfa\|"))
async def twofa_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, action, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    await call.answer()

    if action == "back":
        return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
    if action == "set":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "tfa_set_new")
    if action == "edit":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "tfa_edit_current")
    if action == "remove":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "tfa_remove_current")
    if action == "check":
        return await run_twofa_operation(call.message, zip_id, "check")

@app.on_callback_query(filters.regex(r"^comm\|"))
async def comm_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, action, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    await call.answer()

    if action == "back":
        return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
        
    if action == "vote":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "vote_poll")
    if action == "scrape":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "scrape_members")

    if action == "join":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "join_chat")
    if action == "leave":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "leave_chat")
    if action == "send":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "send_msg")
    if action == "report":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "report_target")
    if action == "broadcast_menu":
        return await show_broadcast_menu(call, zip_id)
    if action == "bc_groups":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "bc_groups")
    if action == "bc_dms":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "bc_dms")
    if action == "bc_all":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "bc_all")
    if action == "views":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "increase_views")
    if action == "react":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "react_post")



@app.on_callback_query(filters.regex(r"^cancel\|"))
async def cancel_actions(_, call: CallbackQuery):
    await call.answer()
    _, kind = call.data.split("|")

    if kind == "prompt":
        state = PROMPT_STATE.pop(call.from_user.id, None)
        if state:
            merge_dir = state.get("merge_dir")
            if merge_dir:
                shutil.rmtree(merge_dir, ignore_errors=True)
            zip_id = state.get("zip_id")
            if zip_id:
                return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
            return await safe_edit(call.message, "❌ Aborted.", reply_markup=None)
        return await safe_edit(call.message, "❌ Aborted.", reply_markup=None)

    if kind == "create":
        state = CREATE_STATE.pop(call.from_user.id, None)
        if state:
            client = state.get("client")
            if client:
                if state["format"] == "pyrogram":
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                else:
                    await safe_telethon_disconnect(client)
            shutil.rmtree(state["dir"], ignore_errors=True)
        return await safe_edit(call.message, "❌ Aborted.", reply_markup=None)

    if kind == "logout":
        LOGOUT_STATE.pop(call.from_user.id, None)
        return await safe_edit(call.message, "❌ Logout aborted.", reply_markup=None)


@app.on_callback_query(filters.regex(r"^slo\|"))
async def session_logout_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    
    parts = call.data.split("|")
    action = parts[1]
    
    await call.answer()

    if action == "cancel":
        LOGOUT_STATE.pop(call.from_user.id, None)
        return await safe_edit(call.message, "❌ Logout closed.", reply_markup=None)

    state = LOGOUT_STATE.get(call.from_user.id)
    if not state or state.get("step") != "awaiting_action":
        return await safe_edit(call.message, "❌ Session expired. Use /logout again.", reply_markup=None)

    auth_key = state["auth_key"]
    dc_id = state["dc_id"]
    input_type = state["input_type"]

    if action == "self":
        LOGOUT_STATE.pop(call.from_user.id, None)
        await safe_edit(call.message, "⏳ Logging out self...", reply_markup=None)
        result = await _run_logout_self(auth_key, dc_id, call.message)
        return await safe_edit(call.message, result, reply_markup=None)
        
    elif action == "others":
        await safe_edit(call.message, "⏳ Revoking other devices...")
        await _run_revoke_others(auth_key, dc_id, call.message)
        await _render_devices_menu(auth_key, dc_id, input_type, call.message)
        
    elif action == "rev":
        auth_hash = int(parts[2])
        if auth_hash == 0:
            await call.answer("⚠️ Cannot revoke current device!", show_alert=True)
            return await _render_devices_menu(auth_key, dc_id, input_type, call.message)
        await safe_edit(call.message, "⏳ Revoking device...")
        client = None
        try:
            client = await _connect_from_auth_key(auth_key, dc_id)
            dc_id = client.session.dc_id
            await client(ResetAuthorizationRequest(hash=auth_hash))
        except Exception:
            pass
        finally:
            if client:
                await safe_telethon_disconnect(client)
        await _render_devices_menu(auth_key, dc_id, input_type, call.message)
        
    elif action == "otp":
        await safe_edit(call.message, "⏳ Fetching OTP...")
        client = None
        otp_text = "❌ No recent OTP found."
        try:
            client = await _connect_from_auth_key(auth_key, dc_id)
            dc_id = client.session.dc_id
            entity = 777000
            try:
                entity = await client.get_input_entity(777000)
            except Exception:
                try:
                    entity = await client.get_input_entity('Telegram')
                except Exception:
                    pass
            async for msg in client.iter_messages(entity, limit=3):
                if msg.text:
                    otp_text = msg.text
                    break
        except Exception:
            pass
        finally:
            if client:
                await safe_telethon_disconnect(client)
        await _render_devices_menu(auth_key, dc_id, input_type, call.message, otp_text=otp_text)
        
    elif action == "refresh":
        await safe_edit(call.message, "⏳ Refreshing devices...")
        await _render_devices_menu(auth_key, dc_id, input_type, call.message)
        
    else:
        return await safe_edit(call.message, "❌ Unknown action.", reply_markup=None)


@app.on_callback_query(filters.regex(r"^dev\|"))
async def device_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, action, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    await call.answer()

    if not zip_belongs_to_user(zip_id, call.from_user.id):
        return await call.message.reply_text("🚫 You don't have access to this pack.")

    if action == "back":
        return await safe_edit(call.message, zip_menu_text(zip_id), reply_markup=operations_keyboard(zip_id))
    if action == "protect":
        return await prompt_for_input(call.message, call.from_user.id, zip_id, "protect")
    if action == "list":
        return await process_devices(call, zip_id, "list")
    if action == "confirm_remove":
        files = get_session_files(zip_id)
        count = len(files)
        text = (
            f"⚠️ **Revoke All Other Devices**\n\n"
            f"Are you sure you want to terminate all other active sessions for the `{count}` account(s) in **Pack #{zip_id}**?\n\n"
            f"This will log out all other devices/clients except the whitelisted ones."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Confirm Revoke", callback_data=f"dev|remove|{zip_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"dev|back_devices|{zip_id}"),
                ]
            ]
        )
        return await safe_edit(call.message, text, reply_markup=keyboard)
    if action == "back_devices":
        return await show_devices_menu(call, zip_id)
    if action == "remove":
        return await process_devices(call, zip_id, "remove")
    if action == "sort":
        return await run_sort_devices(call, zip_id)


@app.on_callback_query(filters.regex(r"^otp\|"))
async def otp_actions(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
    _, zip_id_str, index_str = call.data.split("|")
    zip_id = int(zip_id_str)
    index = int(index_str)
    await call.answer()

    state = OTP_STATE.get((call.message.chat.id, zip_id))
    if not state:
        # Auto-Recovery Protocol: Rebuild state from disk if memory wiped due to server restart
        files = get_session_files(zip_id)
        if not files:
            return await call.message.reply_text("❌ This OTP session has expired and the pack no longer exists.")
        OTP_STATE[(call.message.chat.id, zip_id)] = {"sessions": files}
        state = OTP_STATE[(call.message.chat.id, zip_id)]

    sessions = state["sessions"]
    if index >= len(sessions):
        return await call.answer("All accounts processed.", show_alert=True)

    session_path = sessions[index]
    try:
        otp_res = await find_latest_otp(session_path)
        otp_code, msg_date = otp_res if isinstance(otp_res, tuple) else (otp_res, None)
    except INVALID_EXCEPTIONS as exc:
        await call.answer(f"⚠️ Account error: {type(exc).__name__}", show_alert=True)
        return await send_otp_card(call.message.chat.id, zip_id, index + 1)
    except Exception as exc:
        return await call.answer(f"Connection Error: {exc}", show_alert=True)

    if otp_code:
        from datetime import timezone, timedelta
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        msg_time_str = "Unknown"
        if msg_date:
            msg_time_ist = msg_date.replace(tzinfo=timezone.utc).astimezone(ist_tz)
            msg_time_str = msg_time_ist.strftime("%I:%M:%S %p")
        await app.send_message(
            call.message.chat.id, 
            f"🌟 **Telegram Verification** 🌟\n──────────────────\n🔐 **Code:** `{otp_code}`\n🕒 **Time:** `{msg_time_str} (IST)`\n──────────────────"
        )
        await call.answer("OTP sent!")
        await send_otp_card(call.message.chat.id, zip_id, index + 1)
    else:
        await call.answer("❌ No OTP yet. Tap again to refresh.", show_alert=True)



@app.on_callback_query(filters.regex(r"^term_dev\|"))
async def handle_terminate_device(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str, device_hash_str = call.data.split("|")
    zip_id = int(zip_id_str)
    device_hash = int(device_hash_str)
    
    text = (
        f"⚠️ **Confirm Device Termination**\n\n"
        f"Are you sure you want to disconnect this device?\n"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Confirm", callback_data=f"do_term_dev|{zip_id}|{device_hash}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"refresh_dev|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^do_term_dev\|"))
async def handle_do_terminate_device(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str, device_hash_str = call.data.split("|")
    zip_id = int(zip_id_str)
    device_hash = int(device_hash_str)
    
    if device_hash == 0:
        return await call.answer("❌ Cannot terminate the current active device!", show_alert=True)
        
    await call.answer("⏳ Terminating device...", show_alert=False)
    
    session_files = get_session_files(zip_id)
    if not session_files:
        return await call.answer("❌ Session file not found.", show_alert=True)
        
    session_path = session_files[0]
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    client = None
    success = False
    error_msg = ""
    try:
        if session_type == "pyrogram":
            client = make_pyro_client(session_name, workdir=workdir)
            await client.start()
            await client.invoke(functions.account.ResetAuthorization(hash=device_hash))
            success = True
        else:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(
                session_obj,
                connection_retries=1,
                auto_reconnect=False,
                flood_sleep_threshold=0,
                timeout=3
            )
            await asyncio.wait_for(client.connect(), timeout=5)
            await client(ResetAuthorizationRequest(hash=device_hash))
            success = True
    except Exception as exc:
        error_msg = str(exc)
    finally:
        if client:
            if session_type == "pyrogram":
                await client.stop()
            else:
                await safe_telethon_disconnect(client)
                
    if success:
        await call.answer("✅ Device terminated successfully!", show_alert=True)
        # Re-render the active devices menu
        text, markup = await render_direct_devices(zip_id)
        if markup:
            await safe_edit(call.message, text, reply_markup=markup)
        else:
            await safe_edit(call.message, text)
    else:
        await call.answer(f"❌ Termination failed: {error_msg}", show_alert=True)


@app.on_callback_query(filters.regex(r"^term_all_dev\|"))
async def handle_terminate_all_devices(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    text = (
        f"⚠️ **Confirm Revoke All**\n\n"
        f"Are you sure you want to disconnect ALL other active devices?\n"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Confirm Revoke All", callback_data=f"do_term_all_dev|{zip_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"refresh_dev|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^do_term_all_dev\|"))
async def handle_do_terminate_all_devices(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    await call.answer("⏳ Terminating all other devices...", show_alert=False)
    
    session_files = get_session_files(zip_id)
    if not session_files:
        return await call.answer("❌ Session file not found.", show_alert=True)
        
    session_path = session_files[0]
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    client = None
    success_count = 0
    error_msg = ""
    try:
        if session_type == "pyrogram":
            from pyrogram.raw.functions.auth import ResetAuthorizations as PyroResetAuthorizations
            client = make_pyro_client(session_name, workdir=workdir)
            await client.start()
            
            # Primary: Atomic reset all other authorizations (leaves current session alive)
            reset_done = False
            try:
                await client.invoke(PyroResetAuthorizations())
                reset_done = True
                success_count = 1
            except Exception as atomic_err:
                a_str = str(atomic_err).upper()
                if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in a_str:
                    error_msg = "Telegram 24h Security Rule: Session was logged in less than 24h ago and cannot terminate other sessions yet."
                else:
                    logging.warning(f"[term_all_dev] Pyrogram Atomic ResetAuthorizations failed ({atomic_err}), trying individual fallback...")
            
            if not reset_done and not error_msg:
                # Fallback: Individual resets with strict hash != 0 and not current checks
                auths = await client.invoke(functions.account.GetAuthorizations())
                for item in auths.authorizations:
                    if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                        continue
                    try:
                        await client.invoke(functions.account.ResetAuthorization(hash=item.hash))
                        success_count += 1
                    except Exception as ind_err:
                        if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in str(ind_err).upper():
                            error_msg = "Telegram 24h Security Rule: Session was logged in less than 24h ago and cannot terminate other sessions yet."
                            break
        else:
            from telethon.tl.functions.auth import ResetAuthorizationsRequest as TeleResetAuthorizationsRequest
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(
                session_obj,
                connection_retries=2,
                auto_reconnect=False,
                flood_sleep_threshold=0,
                timeout=10
            )
            await asyncio.wait_for(client.connect(), timeout=10)
            
            # Primary: Atomic reset all other authorizations (leaves current session alive)
            reset_done = False
            try:
                await client(TeleResetAuthorizationsRequest())
                reset_done = True
                success_count = 1
            except Exception as atomic_err:
                a_str = str(atomic_err).upper()
                if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in a_str:
                    error_msg = "Telegram 24h Security Rule: Session was logged in less than 24h ago and cannot terminate other sessions yet."
                else:
                    logging.warning(f"[term_all_dev] Telethon Atomic ResetAuthorizations failed ({atomic_err}), trying individual fallback...")
            
            if not reset_done and not error_msg:
                # Fallback: Individual resets with strict hash != 0 and not current checks
                auths = await client(GetAuthorizationsRequest())
                for item in auths.authorizations:
                    if getattr(item, "current", False) or getattr(item, "hash", 0) == 0:
                        continue
                    try:
                        await client(ResetAuthorizationRequest(hash=item.hash))
                        success_count += 1
                    except Exception as ind_err:
                        if "FRESH_RESET_AUTHORISATION_FORBIDDEN" in str(ind_err).upper():
                            error_msg = "Telegram 24h Security Rule: Session was logged in less than 24h ago and cannot terminate other sessions yet."
                            break
    except Exception as exc:
        if not error_msg:
            error_msg = str(exc)
    finally:
        if client:
            if session_type == "pyrogram":
                await client.stop()
            else:
                await safe_telethon_disconnect(client)
                
    if not error_msg:
        await call.answer("✅ Other device(s) terminated successfully!", show_alert=True)
        await asyncio.sleep(0.5)
        text, markup = await render_direct_devices(zip_id)
        if markup:
            await safe_edit(call.message, text, reply_markup=markup)
        else:
            await safe_edit(call.message, text)
    else:
        await call.answer(f"❌ Revoke failed: {error_msg}", show_alert=True)

@app.on_callback_query(filters.regex(r"^refresh_dev\|"))
async def handle_refresh_devices(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    await call.answer("🔄 Refreshing active devices...", show_alert=False)
    
    text, markup = await render_direct_devices(zip_id)
    if markup:
        await safe_edit(call.message, text, reply_markup=markup)
    else:
        await safe_edit(call.message, text)


@app.on_callback_query(filters.regex(r"^term_self\|"))
async def handle_terminate_self(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    await call.answer("⚠️ WARNING: This will permanently log out this account from Telegram servers!", show_alert=True)
    
    text = (
        f"🚨 **Confirm Bot Termination (Permanent Logout)** 🚨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ **CRITICAL WARNING:**\n"
        f"• This action calls `log_out()` on Telegram servers and deletes the session.\n"
        f"• The account will be permanently logged out and cannot be recovered without a fresh login code.\n\n"
        f"Are you sure you want to permanently log out this session?"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❌ Cancel (Keep Account Safe)", callback_data=f"refresh_dev|{zip_id}"),
            ],
            [
                InlineKeyboardButton("💀 Yes, Permanently Terminate & Log Out", callback_data=f"do_term_self|{zip_id}"),
            ]
        ]
    )
    await safe_edit(call.message, text, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^do_term_self\|"))
async def handle_do_terminate_self(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    await call.answer("⏳ Terminating bot connection...", show_alert=False)
    return await destroy_zip_sessions(call, zip_id)


@app.on_callback_query(filters.regex(r"^direct_otp\|"))
async def handle_direct_otp(_, call: CallbackQuery):
    if is_rate_limited(call.from_user.id):
        return await call.answer("Please wait...", show_alert=False)
        
    _, zip_id_str = call.data.split("|")
    zip_id = int(zip_id_str)
    
    await call.answer("⏳ Reading OTP code...", show_alert=False)
    
    session_files = get_session_files(zip_id)
    if not session_files:
        return await call.answer("❌ Session file not found.", show_alert=True)
        
    session_path = session_files[0]
    session_type = detect_session_type(session_path)
    session_name = os.path.basename(session_path).replace(".session", "")
    workdir = os.path.dirname(session_path)
    
    client = None
    otp_code = None
    msg_time_str = "Unknown"
    
    try:
        if session_type == "pyrogram":
            client = make_pyro_client(session_name, workdir=workdir)
            await client.start()
            async for msg in client.get_chat_history(777000, limit=5):
                if msg.text:
                    match = OTP_REGEX.search(msg.text)
                    if match and is_recent_otp(msg.date):
                        otp_code = match.group(1)
                        from datetime import timezone, timedelta
                        ist_tz = timezone(timedelta(hours=5, minutes=30))
                        msg_time_ist = msg.date.replace(tzinfo=timezone.utc).astimezone(ist_tz)
                        msg_time_str = msg_time_ist.strftime("%I:%M:%S %p")
                        break
        else:
            session_obj = safe_sqlite_session(session_path)
            client = make_tele_client(
                session_obj,
                connection_retries=1,
                auto_reconnect=False,
                flood_sleep_threshold=0,
                timeout=3
            )
            await asyncio.wait_for(client.connect(), timeout=5)
            if await client.is_user_authorized():
                entity = 777000
                try:
                    entity = await client.get_input_entity(777000)
                except Exception:
                    try:
                        entity = await client.get_input_entity('Telegram')
                    except Exception:
                        pass
                async for msg in client.iter_messages(entity, limit=5):
                    if msg.text:
                        match = OTP_REGEX.search(msg.text)
                        if match and is_recent_otp(msg.date):
                            otp_code = match.group(1)
                            from datetime import timezone, timedelta
                            ist_tz = timezone(timedelta(hours=5, minutes=30))
                            msg_time_ist = msg.date.replace(tzinfo=timezone.utc).astimezone(ist_tz)
                            msg_time_str = msg_time_ist.strftime("%I:%M:%S %p")
                            break
    except Exception as exc:
        return await call.answer(f"❌ Error: {exc}", show_alert=True)
    finally:
        if client:
            if session_type == "pyrogram":
                await client.stop()
            else:
                await safe_telethon_disconnect(client)
                
    if otp_code:
        await app.send_message(
            call.message.chat.id,
            f"🌟 **Telegram Verification** 🌟\n"
            f"──────────────────\n"
            f"🔐 **Code:** `{otp_code}`\n"
            f"🕒 **Time:** `{msg_time_str} (IST)`\n"
            f"──────────────────"
        )
        await call.answer("OTP Code retrieved successfully!")
    else:
        await call.answer("❌ No OTP code found in recent messages.", show_alert=True)



# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# This is the very last part. It sets up the storage folders
# Initialize and launch.
# ─────────────────────────────────────────────────────────────────────────────

