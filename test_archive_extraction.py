import asyncio
import os
import shutil
import tempfile
import sqlite3
import tarfile
import zipfile
import json

import session_core
from session_core import (
    extract_sessions_from_zip,
    safe_extract_archive,
    parse_json_session_file,
    _convert_tdata_to_sessions,
)


def _create_dummy_sqlite_session(path, dc_id=2, auth_key=None):
    """Helper to create a minimal spec-compliant Telethon SQLite session."""
    if auth_key is None:
        auth_key = b"\xaa" * 256
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE version (version INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO version VALUES (8)")
    conn.execute(
        "CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, auth_key BLOB, takeout_id INTEGER)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        (dc_id, "149.154.167.51", 443, auth_key, None),
    )
    conn.execute(
        "CREATE TABLE entities (id INTEGER PRIMARY KEY, hash INTEGER NOT NULL, username TEXT, phone INTEGER, name TEXT, date INTEGER)"
    )
    conn.commit()
    conn.close()


def test_standard_zip_extraction():
    """Test extracting a standard ZIP archive containing .session files."""
    td = tempfile.mkdtemp()
    try:
        sess_src = os.path.join(td, "+12345678901.session")
        _create_dummy_sqlite_session(sess_src)

        zip_p = os.path.join(td, "test.zip")
        with zipfile.ZipFile(zip_p, "w") as zf:
            zf.write(sess_src, arcname="+12345678901.session")

        target_dir = os.path.join(td, "extracted")
        count = asyncio.run(extract_sessions_from_zip(zip_p, target_dir))
        assert count == 1
        extracted_files = os.listdir(target_dir)
        assert any("12345678901" in f and f.endswith(".session") for f in extracted_files)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_nested_zip_extraction():
    """Test extracting a ZIP archive that contains another nested ZIP."""
    td = tempfile.mkdtemp()
    try:
        sess_src = os.path.join(td, "+998901234567.session")
        _create_dummy_sqlite_session(sess_src)

        inner_zip = os.path.join(td, "inner.zip")
        with zipfile.ZipFile(inner_zip, "w") as zf:
            zf.write(sess_src, arcname="+998901234567.session")

        outer_zip = os.path.join(td, "outer.zip")
        with zipfile.ZipFile(outer_zip, "w") as zf:
            zf.write(inner_zip, arcname="folder/inner.zip")

        target_dir = os.path.join(td, "extracted_nested")
        count = asyncio.run(extract_sessions_from_zip(outer_zip, target_dir))
        assert count == 1
        extracted_files = os.listdir(target_dir)
        assert any("998901234567" in f and f.endswith(".session") for f in extracted_files)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_tar_archive_extraction():
    """Test extracting a tar.gz archive with sessions."""
    td = tempfile.mkdtemp()
    try:
        sess_src = os.path.join(td, "+447123456789.session")
        _create_dummy_sqlite_session(sess_src)

        tar_p = os.path.join(td, "sessions.tar.gz")
        with tarfile.open(tar_p, "w:gz") as tf:
            tf.add(sess_src, arcname="+447123456789.session")

        target_dir = os.path.join(td, "extracted_tar")
        count = asyncio.run(extract_sessions_from_zip(tar_p, target_dir))
        assert count == 1
        extracted_files = os.listdir(target_dir)
        assert any("447123456789" in f and f.endswith(".session") for f in extracted_files)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_sqlite_without_session_extension():
    """Test discovering raw SQLite database files named without .session extension (e.g. phone number)."""
    td = tempfile.mkdtemp()
    try:
        # File has NO .session extension
        sess_src = os.path.join(td, "628123456789")
        _create_dummy_sqlite_session(sess_src)

        zip_p = os.path.join(td, "raw_db.zip")
        with zipfile.ZipFile(zip_p, "w") as zf:
            zf.write(sess_src, arcname="628123456789")

        target_dir = os.path.join(td, "extracted_raw")
        count = asyncio.run(extract_sessions_from_zip(zip_p, target_dir))
        assert count == 1
        # Extracted file gets .session extension
        files = [f for f in os.listdir(target_dir) if f.endswith(".session")]
        assert len(files) == 1
        assert "628123456789" in files[0]
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_json_int_list_auth_key_extraction():
    """Test extracting a JSON account with auth_key formatted as an integer array [12, 45, ...]."""
    td = tempfile.mkdtemp()
    try:
        raw_key = [i % 256 for i in range(256)]
        account_data = {
            "phone": "+15551234567",
            "dc_id": 2,
            "auth_key": raw_key,
        }
        json_p = os.path.join(td, "export.json")
        with open(json_p, "w") as f:
            json.dump(account_data, f)

        zip_p = os.path.join(td, "json_pack.zip")
        with zipfile.ZipFile(zip_p, "w") as zf:
            zf.write(json_p, arcname="export.json")

        target_dir = os.path.join(td, "extracted_json")
        count = asyncio.run(extract_sessions_from_zip(zip_p, target_dir))
        assert count == 1
        files = [f for f in os.listdir(target_dir) if f.endswith(".session")]
        assert len(files) == 1
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_txt_session_inside_archive():
    """Test extracting a TXT file containing a session string when no .session files exist in the archive."""
    td = tempfile.mkdtemp()
    try:
        # Valid Telethon string session structure
        from session_core import make_offline_telethon_string_session
        test_key = b"\x77" * 256
        s_str = make_offline_telethon_string_session(2, test_key)

        txt_p = os.path.join(td, "sessions.txt")
        with open(txt_p, "w") as f:
            f.write(f"{s_str}\n")

        zip_p = os.path.join(td, "txt_pack.zip")
        with zipfile.ZipFile(zip_p, "w") as zf:
            zf.write(txt_p, arcname="sessions.txt")

        target_dir = os.path.join(td, "extracted_txt")
        count = asyncio.run(extract_sessions_from_zip(zip_p, target_dir))
        assert count == 1
        files = [f for f in os.listdir(target_dir) if f.endswith(".session")]
        assert len(files) == 1
    finally:
        shutil.rmtree(td, ignore_errors=True)
