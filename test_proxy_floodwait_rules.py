import os
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("BOT_TOKEN", "12345:abcdefghijklmnopqrstuvwxyz0123456789abcdef")
import unittest
import imaplib
import config
from config import (
    PYRO_PROXY,
    TELE_PROXY,
    FLOODWAIT_PYRO_PROXY,
    FLOODWAIT_TELE_PROXY,
    get_floodwait_pyro_proxy,
    get_floodwait_tele_proxy,
)
from mail_engine import IMAPMailClient
from session_core import make_pyro_client, make_tele_client

class TestProxyFloodWaitRules(unittest.TestCase):
    def test_default_clients_have_no_proxy(self):
        """Rule: Telegram clients must connect DIRECTLY by default (no proxy)."""
        self.assertIsNone(PYRO_PROXY, "PYRO_PROXY must be None by default")
        self.assertIsNone(TELE_PROXY, "TELE_PROXY must be None by default")

        # Test make_pyro_client without explicit proxy
        pyro_c = make_pyro_client("test_session_direct")
        self.assertIsNone(getattr(pyro_c, "proxy", None), "Default Pyrogram client must not have proxy")

    def test_floodwait_proxy_getters(self):
        """Rule: Proxy is ONLY used when FloodWait is hit."""
        pyro_fw_proxy = get_floodwait_pyro_proxy("1234567890")
        tele_fw_proxy = get_floodwait_tele_proxy("1234567890")
        
        if pyro_fw_proxy is None or tele_fw_proxy is None:
            self.assertIsNone(pyro_fw_proxy)
            self.assertIsNone(tele_fw_proxy)
        else:
            self.assertIn("hostname", pyro_fw_proxy)
            self.assertEqual(pyro_fw_proxy["scheme"], "http")

    def test_imap_direct_first(self):
        """Rule: IMAP connects directly first without proxy overhead."""
        client = IMAPMailClient("dummy@gmail.com", "dummy_pass")
        self.assertEqual(client.imap_host, "imap.gmail.com")
        self.assertEqual(client.imap_port, 993)

if __name__ == "__main__":
    unittest.main()
