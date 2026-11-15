"""Shared test setup.

Every test runs fully isolated:
- HOME points at a temp dir, so nothing touches ~/.osaeru.
- The OS keychain is replaced by an in-memory one.
- claude_sync's fallback key file lives in the temp dir.
- The Anthropic client is replaced by a fake, so no test can reach the
  real API or spend credit. Tests script the fake's replies.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import keyring
import keyring.backend
import pytest

# Lets `pytest` import the package from src/ without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from osaeru import claude_sync, database as db  # noqa: E402


# ---- keychain ----------------------------------------------------------------
class MemoryKeyring(keyring.backend.KeyringBackend):
    """A real keyring backend that keeps passwords in a dict."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


class BrokenKeyring(MemoryKeyring):
    """A keyring that exists but errors on every call."""

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("locked")


# ---- fake Anthropic client -----------------------------------------------------
def text_message(text, stop_reason="end_turn", extra_blocks=()):
    """Builds an object shaped like an anthropic Message."""
    blocks = [*extra_blocks, SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic.

    Set `FakeAnthropic.replies` to a list of Message-like objects (or
    exceptions to raise); each messages.create() call pops the next one.
    Every call is recorded in `FakeAnthropic.calls`.
    """

    replies = []
    calls = []
    inits = []
    models_error = None

    def __init__(self, **kwargs):
        FakeAnthropic.inits.append(kwargs)
        self.messages = SimpleNamespace(create=self._create)
        self.models = SimpleNamespace(list=self._list_models)

    def _create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        if not FakeAnthropic.replies:
            raise AssertionError("FakeAnthropic: test made an unexpected Claude call")
        reply = FakeAnthropic.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _list_models(self, **kwargs):
        if FakeAnthropic.models_error:
            raise FakeAnthropic.models_error
        return SimpleNamespace(data=[])


def api_error(cls, status):
    """Builds a real anthropic APIStatusError subclass (401, 403, 429...)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx.Response(status, request=request), body=None)


def connection_error():
    import anthropic

    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


# ---- fixtures ------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Applied to every test automatically."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OSAERU_DB_PATH", str(tmp_path / "osaeru.sqlite3"))

    original = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    monkeypatch.setattr(claude_sync, "_key_file", home / ".osaeru" / "api_key")

    FakeAnthropic.replies = []
    FakeAnthropic.calls = []
    FakeAnthropic.inits = []
    FakeAnthropic.models_error = None
    monkeypatch.setattr(claude_sync, "Anthropic", FakeAnthropic)

    yield SimpleNamespace(home=home, keyring=memory, tmp=tmp_path)

    keyring.set_keyring(original)


@pytest.fixture
def conn(tmp_path):
    """A connection to a fresh database file."""
    c = db.connect(tmp_path / "osaeru.sqlite3")
    yield c
    c.close()


@pytest.fixture
def fake_claude():
    return FakeAnthropic


@pytest.fixture
def with_key():
    """Stores a (fake) API key in the in-memory keychain."""
    claude_sync.set_api_key("sk-ant-test-1234")
    return "sk-ant-test-1234"
