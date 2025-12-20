import os
import pytest

@pytest.fixture(autouse=True, scope="session")
def _env():
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
    os.environ.setdefault("TOKEN3", "TEST_TOKEN")
    os.environ.setdefault("API_ID", "123456")
    os.environ.setdefault("API_HASH", "hash")
    os.environ.setdefault("PHONE_NUMBER", "+70000000000")
    os.environ.setdefault("TELEGRAM_PASSWORD", "pass")
    os.environ.setdefault("TELEGRAM_CHANNEL_ID", "123456789")
    os.environ.setdefault("METRO_CLOSE_MAX_METERS", "800")
    yield
