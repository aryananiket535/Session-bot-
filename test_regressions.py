import os
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("BOT_TOKEN", "12345:abcdefghijklmnopqrstuvwxyz0123456789abcdef")

import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import handlers
import session_core


class TestAuditRegressions(unittest.TestCase):
    def test_legacy_pack_without_owner_is_not_public(self):
        with patch.object(config, "zip_record", return_value={"original_name": "legacy.zip"}), \
             patch.object(config, "is_admin", return_value=False):
            self.assertFalse(config.zip_belongs_to_user(41, 99))

    def test_pack_owner_and_admin_access(self):
        with patch.object(config, "zip_record", return_value={"owner_id": 99}), \
             patch.object(config, "is_admin", return_value=False):
            self.assertTrue(config.zip_belongs_to_user(41, 99))
            self.assertFalse(config.zip_belongs_to_user(41, 100))
        with patch.object(config, "zip_record", return_value={"owner_id": 99}), \
             patch.object(config, "is_admin", return_value=True):
            self.assertTrue(config.zip_belongs_to_user(41, 100))

    def test_button_semantic_styles(self):
        self.assertEqual(config.styled_button("Open Pack", callback_data="open").style, config._PRIMARY)
        for label in ("Logout", "Clear Cache", "Delete Pack", "Cancel", "Revoke Device"):
            self.assertEqual(config.styled_button(label, callback_data="x").style, config._DANGER)


    def test_sudo_duration_and_expiry(self):
        self.assertEqual(handlers.parse_sudo_duration("2h"), 7200)
        self.assertEqual(handlers.parse_sudo_duration("3mo"), 3 * 30 * 86400)
        self.assertEqual(handlers.parse_sudo_duration("30 days"), 30 * 86400)
        self.assertEqual(handlers.parse_sudo_duration("1 month"), 30 * 86400)
        self.assertIsNone(handlers.parse_sudo_duration("0d"))
        self.assertIsNone(handlers.parse_sudo_duration("tomorrow"))
        old_mode, old_sudo = config.ACCESS_MODE, dict(config.SUDO_USERS)
        try:
            config.ACCESS_MODE = "paid"
            config.SUDO_USERS.clear()
            config.SUDO_USERS["55"] = 2000
            self.assertTrue(config.has_access(55, now=1000))
            self.assertFalse(config.has_access(55, now=3000))
            self.assertFalse(config.has_access(56, now=1000))
        finally:
            config.ACCESS_MODE = old_mode
            config.SUDO_USERS.clear()
            config.SUDO_USERS.update(old_sudo)

    def test_admin_keyboard_has_new_controls(self):
        data = [
            button.callback_data
            for row in session_core.admin_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn("admin_force_join", data)
        self.assertIn("admin_whitelist_manage", data)
        self.assertIn("admin_accessibility", data)

    def test_archive_size_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "oversized.zip")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("large.txt", "x" * 10001)
            with patch.object(session_core, "MAX_ARCHIVE_EXPANDED_BYTES", 10000), \
                 patch.object(session_core, "MAX_ARCHIVE_MEMBER_BYTES", 20000):
                self.assertFalse(session_core.safe_extract_archive(archive, os.path.join(tmp, "out")))


class TestPaidAccessMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_paid_mode_blocks_ungranted_user(self):
        old_mode, old_whitelist = config.ACCESS_MODE, config.WHITELIST_MODE
        config.ACCESS_MODE = "paid"
        config.WHITELIST_MODE = False
        msg = SimpleNamespace(from_user=SimpleNamespace(id=777), reply_text=AsyncMock())
        try:
            with patch.object(handlers, "is_admin", return_value=False), patch.object(config, "has_access", return_value=False):
                with self.assertRaises(config.StopPropagation):
                    await handlers.whitelist_middleware(None, msg)
            msg.reply_text.assert_awaited_once()
        finally:
            config.ACCESS_MODE, config.WHITELIST_MODE = old_mode, old_whitelist


class TestForceJoinStatus(unittest.IsolatedAsyncioTestCase):
    async def test_membership_check_and_cache(self):
        before = dict(config.FORCE_JOIN_CHATS)
        config.FORCE_JOIN_CHATS.clear()
        config.FORCE_JOIN_CHATS["-100123"] = {"title": "Test Group", "invite_link": "https://t.me/example"}
        try:
            with patch.object(handlers.app, "get_chat_member", new=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="member")))) as get_member:
                allowed, missing, errors = await handlers.force_join_status(54321, refresh=True)
                self.assertTrue(allowed)
                self.assertEqual(missing, [])
                self.assertEqual(errors, [])
                get_member.assert_awaited_once()
            with patch.object(handlers.app, "get_chat_member", new=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="left")))):
                allowed, missing, errors = await handlers.force_join_status(54322, refresh=True)
                self.assertFalse(allowed)
                self.assertEqual(len(missing), 1)
                self.assertEqual(errors, [])
        finally:
            config.FORCE_JOIN_CHATS.clear()
            config.FORCE_JOIN_CHATS.update(before)
            handlers._FORCE_JOIN_CACHE.clear()
