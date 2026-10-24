import asyncio
import os
import sys
import json
import sqlite3
import tempfile
import shutil

import os
os.environ["BOT_TOKEN"] = "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ_test_token"
import main

def test_meta_and_lock():
    print("[1] Testing Meta and RLock...")
    meta = main.load_meta()
    assert isinstance(meta, dict)
    assert "zips" in meta
    # Test re-entrancy of _META_FILE_LOCK
    with main._META_FILE_LOCK:
        with main._META_FILE_LOCK:
            pass
    print(" -> Meta and RLock OK")

def test_device_params():
    print("[2] Testing Device Parameters (Anti-Fraud / Anti-Freeze)...")
    # Case A: fallback with no json
    params = main._get_device_params_for_session(None, None)
    assert "device_model" in params
    assert params["device_model"] not in ("AccStore", "linuxkali096", "")
    assert "arch" not in params["system_version"].lower()
    assert "anonymous" not in params["app_version"].lower()
    print(f" -> Safe mobile default: {params['device_model']} | {params['system_version']}")

    # Case B: session with json descriptor file
    td = tempfile.mkdtemp()
    try:
        json_path = os.path.join(td, "test_session.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "device": "Google Pixel 8",
                "sdk": "Android 14",
                "app_version": "11.0.0",
                "app_id": 12345,
                "app_hash": "abcdef"
            }, f)
        params_from_json = main._get_device_params_for_session(td, "test_session")
        assert params_from_json["device_model"] == "Google Pixel 8"
        assert params_from_json["system_version"] == "Android 14"
        assert params_from_json["app_version"] == "11.0.0"
        print(" -> Authentic JSON device parameters loaded successfully")
    finally:
        shutil.rmtree(td, ignore_errors=True)

def test_session_parsing():
    print("[3] Testing Session String & Key Parsing (Single & Bulk)...")
    # Telethon valid dummy auth key (256 bytes)
    dummy_key_hex = "a1b2c3d4" * 64
    
    # 1. Single hex key
    auth_key, dc, itype = main._parse_session_input(dummy_key_hex)
    assert len(auth_key) == 256
    assert itype == "hex_key"
    assert dc == 2

    # 2. Hex key with dc prefix
    auth_key2, dc2, itype2 = main._parse_session_input(f"4:{dummy_key_hex}")
    assert dc2 == 4

    # 3. Bulk multi-line parsing
    multi_input = f"""
    Here are the sessions:
    2:{dummy_key_hex}
    and another one:
    1:{"b" * 512}
    random notes here
    """
    results = main._parse_all_session_inputs(multi_input)
    assert len(results) == 2
    print(f" -> Bulk parsing parsed {len(results)} valid credentials from text")

def test_admin_user_resolution():
    print("[4] Testing Admin User ID Resolution...")
    meta = {
        "users": {
            "111222": {"id": 111222, "username": "alice", "first_name": "Alice"},
            "333444": {"id": 333444, "username": "bob_trader", "first_name": "Bob"}
        }
    }
    assert main.resolve_user_id("111222", meta) == 111222
    assert main.resolve_user_id("@alice", meta) == 111222
    assert main.resolve_user_id("bob_trader", meta) == 333444
    assert main.resolve_user_id("@non_existent", meta) is None
    print(" -> User ID resolution OK")

def test_keyboards_and_routes():
    print("[5] Testing Keyboards & Admin Buttons Routing...")
    test_uid = main.ADMIN_IDS[0] if main.ADMIN_IDS else 12345
    kb_user = main.start_keyboard(user_id=test_uid)
    user_btn_data = [btn.callback_data for row in kb_user.inline_keyboard for btn in row if btn.callback_data]
    assert "create|telethon" in user_btn_data
    assert "create|pyrogram" in user_btn_data
    assert "start_logout" in user_btn_data
    assert "user_packs:0" in user_btn_data

    # Admin keyboard
    kb_admin = main.admin_keyboard()
    admin_btn_data = [btn.callback_data for row in kb_admin.inline_keyboard for btn in row if btn.callback_data]
    
    # Check key admin buttons exist
    expected_buttons = [
        "admin_users:0", "admin_top_users", "admin_lookup", "admin_kill_tasks",
        "admin_ban", "admin_unban", "admin_broadcast", "admin_dm",
        "admin_set_announce", "admin_clear_announce", "admin_packs:0",
        "admin_search_pack", "admin_del_pack", "admin_prune", "admin_stats",
        "admin_logs", "admin_disk", "admin_device", "admin_active_tasks",
        "admin_activity", "admin_export_logs", "admin_export", "admin_full_backup",
        "admin_maint", "admin_whitelist", "admin_clear_cache", "admin_addadmin",
        "admin_deladmin", "admin_restart"
    ]
    for b in expected_buttons:
        assert b in admin_btn_data, f"Missing admin button: {b}"
    print(f" -> All {len(expected_buttons)} admin buttons present in admin_keyboard")

def test_clean_session_db_export():
    print("[6] Testing Universal Telethon and Pyrogram Session DB Sanitization...")
    td = tempfile.mkdtemp()
    try:
        src = os.path.join(td, "sample.session")
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, auth_key BLOB, takeout_id INTEGER)")
        conn.execute("INSERT INTO sessions VALUES (2, '149.154.167.50', 443, ?, NULL)", (b"x" * 256,))
        conn.commit()
        conn.close()

        # Telethon export
        tele_out = os.path.join(td, "sample_tele.session")
        main.export_clean_session_db(src, tele_out, "telethon")
        assert os.path.exists(tele_out)
        conn_t = sqlite3.connect(tele_out)
        cur_t = conn_t.cursor()
        cur_t.execute("SELECT version FROM version")
        assert cur_t.fetchone()[0] == 8
        conn_t.close()

        # Pyrogram export
        pyro_out = os.path.join(td, "sample_pyro.session")
        main.export_clean_session_db(src, pyro_out, "pyrogram")
        assert os.path.exists(pyro_out)
        conn_p = sqlite3.connect(pyro_out)
        cur_p = conn_p.cursor()
        cur_p.execute("SELECT number FROM version")
        assert cur_p.fetchone()[0] == 3
        conn_p.close()
        print(" -> Clean Telethon and Pyrogram SQLite schemas verified")
    finally:
        shutil.rmtree(td, ignore_errors=True)

def test_authentic_phone_extraction():
    print("[7] Testing Authentic Phone Extraction & 42777 Rejection...")
    td = tempfile.mkdtemp()
    try:
        # Create a session simulating the Telegram system account (42777)
        # along with the real account owner (9647501873037)
        db_path = os.path.join(td, "42777_1.session")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, hash INTEGER NOT NULL, username TEXT, phone INTEGER, name TEXT, date INTEGER)")
        conn.execute("CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, auth_key BLOB, takeout_id INTEGER)")
        
        # 1. Telegram service bot: id 777000, phone 42777
        conn.execute("INSERT INTO entities VALUES (777000, 111, 'telegram', 42777, 'Telegram', 1600000000)")
        # 2. Pointer to owner: id 0, hash 8452428607
        conn.execute("INSERT INTO entities VALUES (0, 8452428607, NULL, NULL, NULL, 1600000000)")
        # 3. Real user: id 8452428607, phone 9647501873037
        conn.execute("INSERT INTO entities VALUES (8452428607, 222, 'realuser', 9647501873037, 'Owner', 1600000000)")
        conn.execute("INSERT INTO sessions VALUES (2, '149.154.167.51', 443, ?, NULL)", (b"k" * 256,))
        conn.commit()
        conn.close()

        phone, uid, name, uname = main.extract_session_phone_and_id(db_path)
        assert phone == "9647501873037", f"Expected 9647501873037, got {phone}"
        assert uid == 8452428607, f"Expected 8452428607, got {uid}"
        assert uname == "realuser"
        print(f" -> Successfully extracted real owner phone: +{phone} (ID: {uid})")

        # Test session_export_name
        arcname, exp_phone, exp_uid = asyncio.run(main.session_export_name(db_path))
        assert arcname == "9647501873037.session", f"Expected 9647501873037.session, got {arcname}"
        assert exp_phone == "9647501873037"
        assert exp_uid == 8452428607
        print(f" -> Export name candidate correctly resolved to: {arcname}")
    finally:
        shutil.rmtree(td, ignore_errors=True)

def test_unpack_10_columns_and_universal_format():
    print("[8] Testing 10-Column Session Unpack & Universal Bundle Generation...")
    td = tempfile.mkdtemp()
    try:
        # Create a 10-column session exactly replicating Pack 58 / Kurigram style
        db_path = os.path.join(td, "21621715839.session")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE version (number INTEGER PRIMARY KEY);
            CREATE TABLE sessions (
                dc_id INTEGER PRIMARY KEY,
                server_address TEXT,
                port INTEGER,
                api_id INTEGER,
                test_mode INTEGER,
                auth_key BLOB,
                date INTEGER,
                user_id INTEGER,
                is_bot INTEGER,
                tmp_auth_key BLOB
            );
            CREATE TABLE peers (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER,
                type TEXT,
                username TEXT,
                phone_number TEXT,
                last_update_on INTEGER
            );
            CREATE TABLE usernames (id INTEGER, username TEXT);
        """)
        conn.execute("INSERT INTO version VALUES (8)")
        conn.execute(
            "INSERT INTO sessions VALUES (4, '149.154.167.91', 443, 35512496, 0, ?, 0, 6624681018, 0, NULL)",
            (b"k" * 256,)
        )
        conn.execute("INSERT INTO peers VALUES (6624681018, 12345, 'user', 'testuser', '+21621715839', 1600000000)")
        conn.commit()
        conn.close()

        # 1. Test detect_session_type
        detected = main.detect_session_type(db_path)
        assert detected == "pyrogram", f"Expected pyrogram, got {detected}"
        print(" -> detect_session_type correctly identified 10-column session as pyrogram")

        # 2. Test opening with Telethon PatchedSQLiteSession (previously raised ValueError: too many values to unpack (expected 6, got 10))
        t_sess = main.safe_sqlite_session(db_path)
        assert t_sess.dc_id == 4
        assert len(t_sess.auth_key.key) == 256
        print(" -> safe_sqlite_session opened 10-column session with zero unpack errors")

        # 3. Test building universal ZIP
        zip_path, pkg_dir = asyncio.run(main.build_zip_from_sessions([db_path], "test_universal.zip", target_format="universal"))
        assert os.path.isfile(zip_path)
        with main.zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            assert any("telethon/" in name for name in namelist)
            assert any("pyrogram/" in name for name in namelist)
            assert "sessions_strings.txt" in namelist
            assert "README.txt" in namelist
        print(f" -> Universal ZIP verified with dual formats and strings: {len(namelist)} items in archive")
        shutil.rmtree(pkg_dir, ignore_errors=True)
    finally:
        shutil.rmtree(td, ignore_errors=True)

def test_terminate_bot_confirmation():
    print("[9] Testing Terminate Bot Safety Confirmation Modal & Flow...")
    
    # 1. Test operations keyboard has confirm_destroy button
    kb = main.operations_keyboard(999)
    btn_destroy = [btn for row in kb.inline_keyboard for btn in row if "confirm_destroy" in (btn.callback_data or "")]
    assert len(btn_destroy) == 1, "confirm_destroy button missing from operations_keyboard"
    assert "Terminate" in btn_destroy[0].text or "Log Out" in btn_destroy[0].text
    
    # 2. Test show_destroy_confirm renders explicit warnings & confirmation buttons
    class DummyMessage:
        def __init__(self):
            self.text = ""
            self.reply_markup = None
        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

    class DummyCall:
        def __init__(self):
            self.message = DummyMessage()
            self.answered_text = None
            self.show_alert = False
        async def answer(self, text="", show_alert=False):
            self.answered_text = text
            self.show_alert = show_alert

    dummy_call = DummyCall()
    asyncio.run(main.show_destroy_confirm(dummy_call, 999))
    
    assert "CRITICAL WARNING" in dummy_call.message.text
    assert "CANNOT BE UNDONE" in dummy_call.message.text
    assert "log_out()" in dummy_call.message.text
    
    confirm_buttons = [btn for row in dummy_call.message.reply_markup.inline_keyboard for btn in row]
    btn_callbacks = [b.callback_data for b in confirm_buttons]
    assert "zip|cancel_destroy|999" in btn_callbacks, "Cancel button missing from destroy confirmation"
    assert "zip|destroy|999" in btn_callbacks, "Confirm destroy button missing from destroy confirmation"
    
    # Verify cancel is the first button (safety UX)
    assert confirm_buttons[0].callback_data == "zip|cancel_destroy|999"
    print(" -> Terminate Bot safety confirmation dialog, modal alert and buttons verified!")

def test_pyrogram_duplicate_column_immunity():
    print("[10] Testing Pyrogram Schema Migration & Duplicate Column Immunity...")
    from pathlib import Path
    from pyrogram.storage.sqlite_storage import SQLiteStorage
    td = tempfile.mkdtemp()
    try:
        # Recreate exact Pack 97 format: version=2, sessions lacking api_id originally
        db_path = os.path.join(td, "test_v2.session")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE version (number INTEGER PRIMARY KEY);
            INSERT INTO version VALUES (2);
            CREATE TABLE sessions (
                dc_id INTEGER PRIMARY KEY,
                test_mode INTEGER,
                auth_key BLOB,
                date INTEGER,
                user_id INTEGER,
                is_bot INTEGER
            );
            INSERT INTO sessions VALUES (2, 0, x'01020304', 1600000000, 1234567, 0);
            CREATE TABLE peers (
                id INTEGER PRIMARY KEY,
                access_hash INTEGER,
                type TEXT,
                phone_number TEXT,
                last_update_on INTEGER DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

        # 1. Run patcher: adds api_id, server_address, port etc. and ensures version.number >= 7
        main._patch_session_for_pyrogram(db_path)

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        v_num = cur.execute("SELECT number FROM version").fetchone()[0]
        assert v_num >= 7, f"Expected version >= 7, got {v_num}"
        cur.execute("PRAGMA table_info(sessions)")
        s_cols = [c[1] for c in cur.fetchall()]
        assert "api_id" in s_cols
        assert "server_address" in s_cols
        assert "port" in s_cols
        conn.close()

        # 2. Directly test Pyrogram SQLiteStorage opening a session where api_id exists and version is 2
        # (This previously crashed with: sqlite3.OperationalError: duplicate column name: api_id)
        db_path2 = os.path.join(td, "test_dup_col.session")
        conn2 = sqlite3.connect(db_path2)
        conn2.executescript("""
            CREATE TABLE version (number INTEGER PRIMARY KEY);
            INSERT INTO version VALUES (2);
            CREATE TABLE sessions (
                dc_id INTEGER PRIMARY KEY,
                api_id INTEGER,
                server_address TEXT,
                port INTEGER,
                auth_key BLOB,
                date INTEGER,
                user_id INTEGER,
                is_bot INTEGER,
                test_mode INTEGER
            );
            INSERT INTO sessions VALUES (2, 2040, '149.154.167.51', 443, x'01020304', 1600000000, 1234567, 0, 0);
            CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, type INTEGER, phone_number TEXT, last_update_on INTEGER);
        """)
        conn2.commit()
        conn2.close()

        try:
            from pyrogram.storage.file_storage import FileStorage as _StorageClass
        except ImportError:
            from pyrogram.storage.sqlite_storage import SQLiteStorage as _StorageClass
        storage = _StorageClass("test_dup_col", Path(td))
        storage.database = Path(db_path2)
        # Should not raise sqlite3.OperationalError: duplicate column name: api_id
        asyncio.run(storage.open())
        asyncio.run(storage.close())

        conn2 = sqlite3.connect(db_path2)
        v_final = conn2.execute("SELECT number FROM version").fetchone()[0]
        assert v_final >= 7, f"Expected version >= 7 after safe migration, got {v_final}"
        conn2.close()
        print(" -> SQLiteStorage opened and migrated successfully with zero duplicate column errors!")
    finally:
        shutil.rmtree(td, ignore_errors=True)

def test_country_detection_and_split():
    print("[11] Testing Global Country Detection & Universal Split...")
    import operations
    
    test_cases = {
        "+12025550123": "United_States",
        "+14165550123": "Canada",
        "+77011234567": "Kazakhstan",
        "+79031234567": "Russia",
        "+9647501873037": "Iraq",
        "+212612345678": "Morocco",
        "+573001234567": "Colombia",
        "+2348012345678": "Nigeria",
        "+447911123456": "United_Kingdom",
        "+919876543210": "India",
        "+33612345678": "France",
        "+4915123456789": "Germany",
        "+971501234567": "United_Arab_Emirates",
        "+966501234567": "Saudi_Arabia",
        "+8801712345678": "Bangladesh",
        "+628123456789": "Indonesia",
        "+5511987654321": "Brazil",
        "+84912345678": "Vietnam",
        "+923001234567": "Pakistan",
        "+201012345678": "Egypt",
        "+905321234567": "Turkey",
        "+27821234567": "South_Africa",
        "+819012345678": "Japan",
        "+821012345678": "South_Korea",
        "+48123456789": "Poland",
        "+639171234567": "Philippines",
        "+998901234567": "Uzbekistan",
        "+380501234567": "Ukraine",
        "+61412345678": "Australia",
    }
    
    for phone, expected_country in test_cases.items():
        detected = operations.get_country_from_phone(phone)
        assert detected == expected_country, f"Failed for {phone}: expected {expected_country}, got {detected}"
    
    print(f" -> Verified global country detection across {len(test_cases)} countries and continents: 100% accurate!")

if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING COMPREHENSIVE BOT TESTS")
    print("=" * 60)
    test_meta_and_lock()
    test_device_params()
    test_session_parsing()
    test_admin_user_resolution()
    test_keyboards_and_routes()
    test_clean_session_db_export()
    test_authentic_phone_extraction()
    test_unpack_10_columns_and_universal_format()
    test_terminate_bot_confirmation()
    test_pyrogram_duplicate_column_immunity()
    test_country_detection_and_split()
    print("=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
