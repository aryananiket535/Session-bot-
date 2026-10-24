import asyncio
import threading
import tempfile
import os
import unittest
from session_core import make_pyro_client
from config import safe_pyrogram_disconnect

class TestPyrogramResourceLeak(unittest.IsolatedAsyncioTestCase):
    async def test_safe_pyrogram_disconnect_after_connect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            initial_threads = threading.active_count()
            client = make_pyro_client("test_leak_session", workdir=tmpdir)
            
            # Simulated state: client was connected (not initialized)
            client.is_connected = True
            client.is_initialized = False
            
            # Now call safe_pyrogram_disconnect
            await safe_pyrogram_disconnect(client)
            
            self.assertFalse(client.is_connected)
            self.assertFalse(getattr(client, "_sem_acquired", False))
            
    async def test_non_interactive_client_stop_when_uninitialized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = make_pyro_client("test_leak_session_2", workdir=tmpdir)
            
            # Connect only, without start()
            client.is_connected = True
            client.is_initialized = False
            
            # Calling client.stop() directly should safely call disconnect() without crashing or leaking
            await client.stop()
            self.assertFalse(client.is_connected)
            self.assertFalse(getattr(client, "_sem_acquired", False))

if __name__ == "__main__":
    unittest.main()
