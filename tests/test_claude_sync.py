"""Unit tests for osaeru.claude_sync. Claude itself is always faked (see conftest)."""

import json
import logging
import os
from types import SimpleNamespace

import anthropic
import keyring
import keyring.backends.fail
import pytest

from osaeru.claude_sync.api_key import (
    _client,
    clear_api_key,
    get_api_key,
    set_api_key,
    verify_api_key,
)
from osaeru.claude_sync.chat import chat_reply
from osaeru.claude_sync.helper import (
    _extract_json,
    _keyring_available,
    _use_keychain,
    API_KEY_SETTING,
)
from osaeru.claude_sync.session_note import analyse_session_note
from osaeru.claude_sync.sync import run_sync
from osaeru.database.chat_message import add_chat_message
from osaeru.database.engine import connect, dispose_engines
from osaeru.database.flowchart import get_flowchart
from osaeru.database.recommendations import list_recommendations
from osaeru.database.sessions import add_session_note
from osaeru.database.settings import get_setting, set_setting
from osaeru.database.sync import pending_sync_items
from osaeru.config import FLOWCHART_SYSTEM_PROMPT, KEYRING_KEY, KEYRING_SERVICE, MODEL
from conftest import (
    BrokenKeyring,
    sqlite_only,
    api_error,
    connection_error,
    text_message,
)


def analysis(skills=(), edges=(), recs=()):
    """A Claude reply in the shape FLOWCHART_SYSTEM_PROMPT asks for."""
    return text_message(
        json.dumps(
            {
                "new_skills": list(skills),
                "new_edges": list(edges),
                "recommendations": list(recs),
            }
        )
    )


@pytest.fixture
def no_keyring():
    keyring.set_keyring(keyring.backends.fail.Keyring())


@pytest.fixture
def broken_keyring():
    keyring.set_keyring(BrokenKeyring())


# ---- key storage --------------------------------------------------------------------
class TestKeyringAvailable:
    def test_true_with_a_real_backend(self):
        assert _keyring_available() is True

    def test_false_with_fail_backend(self, no_keyring):
        assert _keyring_available() is False

    def test_false_if_keyring_itself_errors(self, monkeypatch):
        def boom():
            raise RuntimeError("no backend")

        monkeypatch.setattr(keyring, "get_keyring", boom)
        assert _keyring_available() is False


def db_key():
    c = connect()
    try:
        return get_setting(c, API_KEY_SETTING)
    finally:
        c.close()


def put_db_key(value):
    c = connect()
    try:
        set_setting(c, API_KEY_SETTING, value)
    finally:
        c.close()


class TestWhereKeysLive:
    @sqlite_only
    def test_keychain_locally(self):
        assert _use_keychain() is True

    def test_database_on_cloud_run(self, monkeypatch):
        monkeypatch.setenv("K_SERVICE", "osaeru")
        assert _use_keychain() is False

    def test_database_with_a_server_database(self, monkeypatch):
        monkeypatch.setenv("OSAERU_DATABASE_URL", "sqlite://")
        assert _use_keychain() is False

    def test_database_without_a_keychain(self, no_keyring):
        assert _use_keychain() is False


@sqlite_only
class TestKeychainStorage:
    """Running locally: the key lives in the OS keychain, as before."""

    def test_none_when_nothing_stored(self):
        assert get_api_key() is None

    def test_reads_from_keychain(self, isolated):
        isolated.keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, "sk-ant-abc")
        assert get_api_key() == "sk-ant-abc"

    def test_stores_in_keychain_and_strips(self, isolated):
        assert set_api_key("  sk-ant-new \n") == "keychain"
        assert (
            isolated.keyring.get_password(KEYRING_SERVICE, KEYRING_KEY) == "sk-ant-new"
        )
        assert db_key() is None

    def test_keychain_wins_over_database(self, isolated):
        isolated.keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, "from-keychain")
        put_db_key("from-db")
        assert get_api_key() == "from-keychain"

    def test_falls_back_to_database_when_keychain_empty(self):
        put_db_key("from-db")
        assert get_api_key() == "from-db"

    def test_broken_keychain_reads_database_and_warns(self, broken_keyring, caplog):
        put_db_key("from-db")
        with caplog.at_level(logging.WARNING, logger="osaeru.claude_sync"):
            assert get_api_key() == "from-db"
        assert "Keyring read failed" in caplog.text

    def test_broken_keychain_writes_database(self, broken_keyring):
        assert set_api_key("sk-ant-new") == "the database"
        assert db_key() == "sk-ant-new"


class TestDatabaseStorage:
    """On Cloud Run: no keychain, so the key lives in the database."""

    @pytest.fixture(autouse=True)
    def on_cloud_run(self, monkeypatch):
        monkeypatch.setenv("K_SERVICE", "osaeru")
        if not os.environ.get("OSAERU_DATABASE_URL"):
            # Cloud Run refuses SQLite, so stand in a database URL for it.
            monkeypatch.setenv(
                "OSAERU_DATABASE_URL", f"sqlite:///{os.environ['OSAERU_DB_PATH']}"
            )

    def test_none_when_nothing_stored(self):
        assert get_api_key() is None

    def test_stores_in_database_and_strips(self, isolated):
        assert set_api_key("  sk-ant-cloud \n") == "the database"
        assert db_key() == "sk-ant-cloud"
        assert isolated.keyring.store == {}  # never touches a keychain

    def test_round_trip(self):
        set_api_key("sk-ant-rt")
        assert get_api_key() == "sk-ant-rt"

    def test_replacing_a_key(self):
        set_api_key("sk-ant-old")
        set_api_key("sk-ant-new")
        assert get_api_key() == "sk-ant-new"

    def test_survives_a_restart(self):
        set_api_key("sk-ant-persist")
        dispose_engines()  # like a new Cloud Run instance
        assert get_api_key() == "sk-ant-persist"

    def test_ignores_a_stray_keychain_entry(self, isolated):
        isolated.keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, "stale")
        assert get_api_key() is None

    def test_blank_value_means_no_key(self):
        put_db_key("   ")
        assert get_api_key() is None

    def test_clear(self):
        set_api_key("sk-ant-x")
        clear_api_key()
        assert get_api_key() is None and db_key() is None


class TestClearApiKey:
    def test_removes_keychain_and_database(self, isolated):
        isolated.keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, "k")
        put_db_key("k")
        clear_api_key()
        assert isolated.keyring.store == {}
        assert db_key() is None
        assert get_api_key() is None

    def test_safe_when_nothing_stored(self):
        clear_api_key()

    def test_safe_with_broken_keychain(self, broken_keyring):
        clear_api_key()

    def test_safe_without_keyring(self, no_keyring):
        set_api_key("k")
        clear_api_key()
        assert get_api_key() is None


def test_round_trip():
    set_api_key("sk-ant-rt")
    assert get_api_key() == "sk-ant-rt"


class TestVerifyApiKey:
    def test_valid_key(self, fake_claude):
        assert verify_api_key(" sk-ant-ok ") is True
        (init,) = fake_claude.inits
        assert init["api_key"] == "sk-ant-ok"
        assert init["max_retries"] == 0 and init["timeout"] == 10.0

    @pytest.mark.parametrize(
        "error",
        [
            api_error(anthropic.AuthenticationError, 401),
            api_error(anthropic.PermissionDeniedError, 403),
        ],
    )
    def test_rejected_key(self, fake_claude, error):
        fake_claude.models_error = error
        assert verify_api_key("sk-ant-bad") is False

    @pytest.mark.parametrize(
        "error",
        [
            connection_error(),
            api_error(anthropic.InternalServerError, 500),
            TimeoutError(),
        ],
    )
    def test_cannot_check(self, fake_claude, error):
        fake_claude.models_error = error
        assert verify_api_key("sk-ant-x") is None


class TestClient:
    def test_raises_without_key(self):
        with pytest.raises(RuntimeError, match="No Anthropic API key"):
            _client()

    def test_uses_stored_key(self, with_key, fake_claude):
        _client()
        assert fake_claude.inits[-1] == {"api_key": with_key}


# ---- JSON extraction ------------------------------------------------------------------
class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_surrounding_whitespace(self):
        assert _extract_json('\n  {"a": 1}  \n') == {"a": 1}

    def test_json_fence(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_bare_fence(self):
        assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json")

    @pytest.mark.xfail(
        strict=True, reason="Known gap: prose before the code fence isn't stripped"
    )
    def test_prose_before_fence(self):
        assert _extract_json('Here you go:\n```json\n{"a": 1}\n```') == {
            "a": 1
        }

    @pytest.mark.xfail(
        strict=True, reason="Known gap: only a lowercase 'json' fence tag is stripped"
    )
    def test_uppercase_fence_tag(self):
        assert _extract_json('```JSON\n{"a": 1}\n```') == {"a": 1}


# ---- Claude calls ---------------------------------------------------------------------
class TestAnalyseSessionNote:
    def test_request_shape(self, with_key, fake_claude):
        fake_claude.replies = [analysis()]
        graph = {"nodes": [{"name": "Mount"}], "edges": []}
        analyse_session_note("Drilled armbars", graph)
        (call,) = fake_claude.calls
        assert call["model"] == MODEL
        assert call["system"] == FLOWCHART_SYSTEM_PROMPT
        assert call["max_tokens"] == 4000
        (msg,) = call["messages"]
        assert msg["role"] == "user"
        assert json.dumps(graph) in msg["content"]
        assert "Drilled armbars" in msg["content"]

    def test_returns_parsed_json(self, with_key, fake_claude):
        fake_claude.replies = [analysis(skills=[{"name": "Armbar"}])]
        result = analyse_session_note("x", {"nodes": [], "edges": []})
        assert result["new_skills"] == [{"name": "Armbar"}]

    def test_ignores_non_text_blocks(self, with_key, fake_claude):
        thinking = SimpleNamespace(type="thinking", thinking="hmm")
        fake_claude.replies = [
            text_message('{"new_skills": []}', extra_blocks=[thinking])
        ]
        assert analyse_session_note("x", {}) == {"new_skills": []}

    def test_truncated_reply_raises(self, with_key, fake_claude):
        fake_claude.replies = [text_message('{"new_sk', stop_reason="max_tokens")]
        with pytest.raises(RuntimeError, match="max_tokens"):
            analyse_session_note("x", {})

    def test_no_key_raises_before_calling(self, fake_claude):
        with pytest.raises(RuntimeError):
            analyse_session_note("x", {})
        assert fake_claude.calls == []


class TestChatReply:
    def test_request_shape(self, with_key, fake_claude):
        fake_claude.replies = [text_message("Frame first.")]
        history = [{"role": "user", "content": "Help with side control"}]
        graph = {"nodes": [{"name": "Side Control"}], "edges": []}
        assert chat_reply(history, graph) == "Frame first."
        (call,) = fake_claude.calls
        assert call["model"] == MODEL
        assert call["max_tokens"] == 800
        assert call["messages"] == history
        assert json.dumps(graph) in call["system"]
        assert "jiu jitsu coach" in call["system"]

    def test_joins_multiple_text_blocks(self, with_key, fake_claude):
        first = SimpleNamespace(type="text", text="Part one. ")
        fake_claude.replies = [text_message("Part two.", extra_blocks=[first])]
        assert chat_reply([], {}) == "Part one. Part two."

    def test_api_errors_propagate(self, with_key, fake_claude):
        fake_claude.replies = [api_error(anthropic.RateLimitError, 429)]
        with pytest.raises(anthropic.RateLimitError):
            chat_reply([], {})


# ---- run_sync -------------------------------------------------------------------------
class TestRunSync:
    def test_nothing_pending(self, conn, with_key, fake_claude):
        assert run_sync(conn) == 0
        assert fake_claude.calls == []

    def test_applies_skills_edges_and_recommendations(
        self, conn, with_key, fake_claude
    ):
        sid = add_session_note(conn, "Knee cut to side control")
        fake_claude.replies = [
            analysis(
                skills=[
                    {
                        "name": "Knee Cut",
                        "category": "passing",
                        "confidence": "learning",
                    }
                ],
                edges=[
                    {"from": "Knee Cut", "to": "Side Control", "relation": "leads_to"}
                ],
                recs=[
                    {"text": "Win the underhook first", "related_skill": "Knee Cut"},
                    {"text": "General drilling"},
                ],
            )
        ]
        assert run_sync(conn) == 1

        graph = get_flowchart(conn)
        knee = next(n for n in graph["nodes"] if n["name"] == "Knee Cut")
        assert (knee["category"], knee["confidence"]) == ("passing", "learning")
        assert knee["first_seen_session_id"] == sid
        assert graph["edges"] == [
            {"from_name": "Knee Cut", "to_name": "Side Control", "relation": "leads_to"}
        ]
        recs = {r["text"]: r["related_skill_id"] for r in list_recommendations(conn)}
        assert recs == {"Win the underhook first": knee["id"], "General drilling": None}
        assert pending_sync_items(conn) == []

    def test_recommendation_for_unknown_skill_creates_it(
        self, conn, with_key, fake_claude
    ):
        add_session_note(conn, "x")
        fake_claude.replies = [
            analysis(recs=[{"text": "Try it", "related_skill": "Leg Drag"}])
        ]
        run_sync(conn)
        assert [n["name"] for n in get_flowchart(conn)["nodes"]] == ["Leg Drag"]

    def test_missing_sections_are_fine(self, conn, with_key, fake_claude):
        add_session_note(conn, "x")
        fake_claude.replies = [text_message("{}")]
        assert run_sync(conn) == 1

    def test_flowchart_refreshed_between_notes(self, conn, with_key, fake_claude):
        add_session_note(conn, "first")
        add_session_note(conn, "second")
        fake_claude.replies = [analysis(skills=[{"name": "Armbar"}]), analysis()]
        assert run_sync(conn) == 2
        assert "Armbar" not in fake_claude.calls[0]["messages"][0]["content"]
        assert "Armbar" in fake_claude.calls[1]["messages"][0]["content"]

    def test_chat_items_are_marked_without_calling_claude(
        self, conn, with_key, fake_claude
    ):
        add_chat_message(conn, "user", "hi")
        assert run_sync(conn) == 1
        assert fake_claude.calls == []
        assert pending_sync_items(conn) == []

    def test_failure_stops_and_keeps_item_pending(self, conn, with_key, fake_claude):
        add_session_note(conn, "ok")
        add_session_note(conn, "fails")
        add_session_note(conn, "never reached")
        fake_claude.replies = [analysis(), connection_error()]
        with pytest.raises(anthropic.APIConnectionError):
            run_sync(conn)
        pending = [
            json.loads(i["payload"])["notes"] for i in pending_sync_items(conn)
        ]
        assert pending == ["fails", "never reached"]
        assert len(fake_claude.calls) == 2

    def test_bad_json_keeps_item_pending(self, conn, with_key, fake_claude):
        add_session_note(conn, "x")
        fake_claude.replies = [text_message("Sorry, I can't do that.")]
        with pytest.raises(json.JSONDecodeError):
            run_sync(conn)
        assert len(pending_sync_items(conn)) == 1

    def test_failure_is_logged(self, conn, with_key, fake_claude, caplog):
        add_session_note(conn, "x")
        fake_claude.replies = [connection_error()]
        with caplog.at_level(logging.ERROR, logger="osaeru.claude_sync"):
            with pytest.raises(anthropic.APIConnectionError):
                run_sync(conn)
        assert "Failed processing item" in caplog.text

    def test_no_key_raises_and_keeps_item(self, conn, fake_claude):
        add_session_note(conn, "x")
        with pytest.raises(RuntimeError, match="No Anthropic API key"):
            run_sync(conn)
        assert len(pending_sync_items(conn)) == 1

    def test_retry_after_failure_succeeds(self, conn, with_key, fake_claude):
        add_session_note(conn, "x")
        fake_claude.replies = [connection_error()]
        with pytest.raises(anthropic.APIConnectionError):
            run_sync(conn)
        fake_claude.replies = [analysis(skills=[{"name": "Armbar"}])]
        assert run_sync(conn) == 1

    @pytest.mark.xfail(
        strict=True,
        reason="Known gap: each upsert commits, so a reply that fails half-way leaves "
        "partial changes behind and the retry applies them again",
    )
    def test_failed_item_leaves_no_partial_changes(self, conn, with_key, fake_claude):
        add_session_note(conn, "x")
        fake_claude.replies = [
            analysis(
                skills=[{"name": "Armbar"}], edges=[{"from": "Armbar"}]
            )  # edge missing keys
        ]
        with pytest.raises(KeyError):
            run_sync(conn)
        assert get_flowchart(conn)["nodes"] == []
