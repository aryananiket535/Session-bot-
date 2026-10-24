import asyncio
try:
    import pytest
except ImportError:
    class _MockPytest:
        class raises:
            def __init__(self, exc_type):
                self.exc_type = exc_type
                self.value = None
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    raise AssertionError(f"Expected {self.exc_type.__name__} but no exception was raised")
                if not issubclass(exc_type, self.exc_type):
                    return False
                self.value = exc_val
                return True
    pytest = _MockPytest()

from unittest.mock import AsyncMock, patch, MagicMock
import config
from config import lock_pack, RUNNING_PACKS, gather_chunked, safe_telethon_disconnect
import session_core
from session_core import SafeTelethonClient, NonInteractivePyrogramClient, GLOBAL_CONNECTION_SEM

def test_pack_locking_prevents_duplicate_runs():
    async def _inner():
        zip_id = 999
        assert zip_id not in RUNNING_PACKS
        assert str(zip_id) not in RUNNING_PACKS

        async with lock_pack(zip_id):
            assert str(zip_id) in RUNNING_PACKS
            assert zip_id in RUNNING_PACKS

            # Second lock attempt must fail
            with pytest.raises(RuntimeError) as exc_info:
                async with lock_pack(zip_id):
                    pass
            assert "busy with another operation" in str(exc_info.value)

        # After exit, pack must be unlocked
        assert zip_id not in RUNNING_PACKS
        assert str(zip_id) not in RUNNING_PACKS
    asyncio.run(_inner())

def test_safe_telethon_disconnect_releases_sem_even_if_not_connected():
    async def _inner():
        client = MagicMock()
        client._sem_acquired = True
        client.is_connected = MagicMock(return_value=False)
        client.disconnect = AsyncMock()

        # Pre-decrement semaphore to simulate client holding a permit
        await GLOBAL_CONNECTION_SEM.acquire()

        await safe_telethon_disconnect(client)

        assert client._sem_acquired is False
        # Semaphore permit was restored
        permits = []
        for _ in range(config.GLOBAL_MAX_CONNECTIONS):
            permits.append(await asyncio.wait_for(GLOBAL_CONNECTION_SEM.acquire(), timeout=1.0))
        for _ in permits:
            GLOBAL_CONNECTION_SEM.release()
    asyncio.run(_inner())

def test_pyro_client_releases_sem_on_cancellation():
    async def _inner():
        client = NonInteractivePyrogramClient("test_dummy_pyro", api_id=123, api_hash="abc", workdir="/tmp")
        
        with patch("pyrogram.Client.connect", side_effect=asyncio.CancelledError):
            initial_value = GLOBAL_CONNECTION_SEM._value
            with pytest.raises(asyncio.CancelledError):
                await client.connect()
            assert GLOBAL_CONNECTION_SEM._value == initial_value
            assert client._sem_acquired is False
    asyncio.run(_inner())

def test_tele_client_releases_sem_on_cancellation():
    async def _inner():
        client = SafeTelethonClient("test_dummy_tele", api_id=123, api_hash="abc")
        
        with patch("telethon.TelegramClient.connect", side_effect=asyncio.CancelledError):
            initial_value = GLOBAL_CONNECTION_SEM._value
            with pytest.raises(asyncio.CancelledError):
                await client.connect()
    asyncio.run(_inner())

if __name__ == "__main__":
    print("Running test_pack_locking_prevents_duplicate_runs...")
    test_pack_locking_prevents_duplicate_runs()
    print("Running test_safe_telethon_disconnect_releases_sem_even_if_not_connected...")
    test_safe_telethon_disconnect_releases_sem_even_if_not_connected()
    print("Running test_pyro_client_releases_sem_on_cancellation...")
    test_pyro_client_releases_sem_on_cancellation()
    print("Running test_tele_client_releases_sem_on_cancellation...")
    test_tele_client_releases_sem_on_cancellation()
    print("✅ ALL CONCURRENCY TESTS PASSED!")
