"""Unit tests for osaeru.database."""

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pg8000.dbapi
import pytest
from sqlalchemy import inspect, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, ProgrammingError

# pg8000 reports foreign-key and CHECK violations as ProgrammingError;
# sqlite3 reports them as IntegrityError. Either means the constraint held.
CONSTRAINT_ERRORS = (IntegrityError, ProgrammingError)

from osaeru.database.chat_message import add_chat_message, list_chat_messages
from osaeru.database.config import app_settings, metadata, profile, sync_queue
from osaeru.database.engine import connect, dispose_engines, get_engine
from osaeru.database.flowchart import get_flowchart
from osaeru.database.helper import _create_tables, _now, backend, default_db_path
from osaeru.database.profile import get_profile, save_profile
from osaeru.database.recommendations import (
    add_recommendation,
    list_recommendations,
    set_recommendation_status,
)
from osaeru.database.sessions import add_session_note, list_sessions
from osaeru.database.settings import delete_setting, get_setting, set_setting
from osaeru.database.skills import add_skill_edge, upsert_skill
from osaeru.database.sync import mark_synced, pending_sync_items, queue_for_sync
from osaeru.database import helper as db_helper
from conftest import TEST_PG_URL, postgres_only, sqlite_only


# ---- connection and setup -------------------------------------------------------
TABLES = {
    "profile",
    "sessions",
    "skills",
    "skill_edges",
    "chat_messages",
    "recommendations",
    "sync_queue",
    "app_settings",
}


class TestConnect:
    def test_creates_all_tables(self, conn):
        assert TABLES <= set(inspect(conn).get_table_names())

    def test_rows_are_mappings(self, conn):
        add_session_note(conn, "x")
        row = list_sessions(conn)[0]
        assert row["notes"] == "x"
        assert dict(row)["notes"] == "x"

    def test_foreign_keys_enforced(self, conn):
        with pytest.raises(CONSTRAINT_ERRORS):
            add_recommendation(conn, "orphan", related_skill_id=999)

    def test_profile_is_a_single_row(self, conn):
        with pytest.raises(CONSTRAINT_ERRORS):
            conn.execute(profile.insert().values(id=2, updated_at="x"))
            conn.commit()

    def test_reconnecting_keeps_data(self):
        c = connect()
        add_session_note(c, "persisted")
        c.close()
        dispose_engines()  # like a restart
        c = connect()
        assert [s["notes"] for s in list_sessions(c)] == ["persisted"]
        c.close()

    def test_engine_is_reused(self):
        assert get_engine() is get_engine()

    def test_dispose_engines_forgets_cached_engines(self):
        first = get_engine()
        dispose_engines()
        assert get_engine() is not first

    def test_creating_tables_twice_is_harmless(self):
        connect().close()
        _create_tables(get_engine())  # e.g. a second instance starting up
        c = connect()
        assert TABLES <= set(inspect(c).get_table_names())
        c.close()

    def test_connections_can_be_closed(self):
        c = connect()
        c.close()
        assert c.closed


@postgres_only
def test_parallel_startups_do_not_race():
    """Several Cloud Run instances starting at once all create tables; the
    advisory lock makes them take turns instead of failing."""
    import threading
    from sqlalchemy import create_engine, text

    with get_engine().begin() as c:  # start from no tables at all
        c.execute(
            text(
                "DROP TABLE IF EXISTS "
                + ", ".join(t.name for t in metadata.sorted_tables)
                + " CASCADE"
            )
        )
    dispose_engines()

    errors = []

    def start_instance():
        engine = create_engine(TEST_PG_URL)
        try:
            _create_tables(engine)
        except Exception as e:  # pragma: no cover - only on failure
            errors.append(e)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=start_instance) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    c = connect()
    assert TABLES <= set(inspect(c).get_table_names())
    c.close()


@sqlite_only
class TestSqliteFile:
    def test_default_path_is_under_home(self, isolated, monkeypatch):
        monkeypatch.delenv("OSAERU_DB_PATH")
        assert default_db_path() == isolated.home / ".osaeru" / "osaeru.sqlite3"

    def test_env_overrides_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OSAERU_DB_PATH", "~/custom.sqlite3")
        assert default_db_path() == Path.home() / "custom.sqlite3"

    def test_connect_with_no_path_uses_default(self, isolated, monkeypatch):
        monkeypatch.delenv("OSAERU_DB_PATH")
        connect().close()
        assert (isolated.home / ".osaeru" / "osaeru.sqlite3").exists()

    def test_explicit_path_wins(self, tmp_path):
        path = tmp_path / "a" / "b" / "c.sqlite3"
        connect(path).close()
        assert path.exists()

    def test_foreign_keys_pragma_on(self, conn):
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    def test_opens_file_made_by_old_version(self, tmp_path):
        """A database created by the pre-SQLAlchemy app (raw SQLite schema)
        is used as-is, with its data intact."""
        path = tmp_path / "old.sqlite3"
        raw = sqlite3.connect(path)
        raw.executescript(OLD_SCHEMA)
        raw.execute(
            "INSERT INTO sessions (occurred_at, notes, created_at) VALUES ('2026-01-01', 'old note', '2026-01-01')"
        )
        raw.commit()
        raw.close()
        c = connect(path)
        assert [s["notes"] for s in list_sessions(c)] == ["old note"]
        add_session_note(c, "new note")
        assert len(list_sessions(c)) == 2
        # Tables added since then are created alongside the old ones.
        assert "app_settings" in inspect(c).get_table_names()
        set_setting(c, "k", "v")
        assert get_setting(c, "k") == "v"
        c.close()


class TestChoosingBackend:
    def test_sqlite_by_default(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        assert backend() == "sqlite"

    def test_cloud_sql_when_instance_set(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "p:r:i")
        assert backend() == "cloudsql"

    def test_url_wins_over_cloud_sql(self, monkeypatch):
        monkeypatch.setenv("OSAERU_DATABASE_URL", "sqlite://")
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "p:r:i")
        assert backend() == "url"

    def test_database_url_is_used(self, tmp_path, monkeypatch):
        target = tmp_path / "via-url.sqlite3"
        monkeypatch.setenv("OSAERU_DATABASE_URL", f"sqlite:///{target}")
        connect().close()
        assert target.exists()

    def test_refuses_sqlite_on_cloud_run(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("K_SERVICE", "osaeru")
        with pytest.raises(RuntimeError, match="CLOUD_SQL_INSTANCE"):
            connect()

    def test_explicit_path_allowed_on_cloud_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("K_SERVICE", "osaeru")
        connect(tmp_path / "explicit.sqlite3").close()


class FakeConnector:
    """Stands in for google.cloud.sql.connector.Connector."""

    instances = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.connect_calls = []
        self.closed = False
        FakeConnector.instances.append(self)

    def connect(self, instance, driver, **kwargs):
        self.connect_calls.append((instance, driver, kwargs))
        if TEST_PG_URL:  # hand back a real connection to the test Postgres
            u = make_url(TEST_PG_URL)
            return pg8000.dbapi.connect(
                user=u.username,
                password=u.password,
                host=u.host,
                port=u.port,
                database=u.database,
            )
        raise ConnectionRefusedError("no Cloud SQL in tests")

    def close(self):
        self.closed = True


class TestCloudSql:
    @pytest.fixture(autouse=True)
    def fake_connector(self, monkeypatch):
        import google.cloud.sql.connector as gcsc

        FakeConnector.instances = []
        monkeypatch.setattr(gcsc, "Connector", FakeConnector)
        monkeypatch.setattr(db_helper, "_connector", None)
        monkeypatch.setattr(db_helper.atexit, "register", lambda fn: None)
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "my-proj:europe-west2:osaeru-db")
        monkeypatch.setenv("DB_NAME", "osaeru")
        monkeypatch.setenv("DB_USER", "osaeru-app")
        monkeypatch.setenv("K_SERVICE", "osaeru")  # pretend we're on Cloud Run

    def _first_connect(self):
        try:
            connect().close()
        except Exception:
            if TEST_PG_URL:
                raise
        (connector,) = FakeConnector.instances
        return connector, connector.connect_calls[0]

    def test_password_auth(self, monkeypatch):
        from google.cloud.sql.connector import IPTypes

        monkeypatch.setenv("DB_PASS", "secret")
        connector, (instance, driver, kwargs) = self._first_connect()
        assert connector.init_kwargs == {"refresh_strategy": "lazy"}
        assert (instance, driver) == ("my-proj:europe-west2:osaeru-db", "pg8000")
        assert kwargs == {
            "user": "osaeru-app",
            "db": "osaeru",
            "ip_type": IPTypes.PUBLIC,
            "password": "secret",
        }

    def test_iam_auth_when_no_password(self):
        _, (_, _, kwargs) = self._first_connect()
        assert kwargs["enable_iam_auth"] is True
        assert "password" not in kwargs

    def test_private_ip(self, monkeypatch):
        from google.cloud.sql.connector import IPTypes

        monkeypatch.setenv("CLOUD_SQL_IP_TYPE", "PRIVATE")
        _, (_, _, kwargs) = self._first_connect()
        assert kwargs["ip_type"] == IPTypes.PRIVATE

    def test_db_user_required(self, monkeypatch):
        monkeypatch.delenv("DB_USER")
        with pytest.raises(RuntimeError, match="DB_USER"):
            connect()

    @postgres_only
    def test_end_to_end_through_connector(self):
        c = connect()
        add_session_note(c, "via Cloud SQL connector")
        assert list_sessions(c)[0]["notes"] == "via Cloud SQL connector"
        c.close()
        assert len(FakeConnector.instances) == 1  # one connector per process


OLD_SCHEMA = """
CREATE TABLE profile (id INTEGER PRIMARY KEY CHECK (id = 1), age INTEGER, sex TEXT,
    weight_kg REAL, sessions_per_week REAL, updated_at TEXT NOT NULL);
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
    notes TEXT NOT NULL, feeling TEXT, created_at TEXT NOT NULL);
CREATE TABLE skills (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    category TEXT, confidence TEXT, first_seen_session_id INTEGER REFERENCES sessions(id),
    updated_at TEXT NOT NULL);
CREATE TABLE skill_edges (id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_skill_id INTEGER NOT NULL REFERENCES skills(id),
    to_skill_id INTEGER NOT NULL REFERENCES skills(id), relation TEXT NOT NULL,
    UNIQUE(from_skill_id, to_skill_id, relation));
CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
    content TEXT NOT NULL, session_id INTEGER REFERENCES sessions(id), created_at TEXT NOT NULL);
CREATE TABLE recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
    related_skill_id INTEGER REFERENCES skills(id), status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL);
CREATE TABLE sync_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT);
"""


def test_now_is_utc_iso8601():
    parsed = dt.datetime.fromisoformat(_now())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == dt.timedelta(0)


# ---- sync queue ------------------------------------------------------------------
class TestSyncQueue:
    def test_queue_item_is_pending_with_json_payload(self, conn):
        queue_for_sync(conn, "session_note", {"notes": "hi", "n": 1})
        conn.commit()
        (item,) = pending_sync_items(conn)
        assert item["kind"] == "session_note"
        assert json.loads(item["payload"]) == {"notes": "hi", "n": 1}
        assert item["sent_at"] is None

    def test_pending_items_oldest_first(self, conn):
        for i in range(3):
            queue_for_sync(conn, "session_note", {"i": i})
        conn.commit()
        assert [json.loads(r["payload"])["i"] for r in pending_sync_items(conn)] == [
            0,
            1,
            2,
        ]

    def test_mark_synced_removes_from_pending(self, conn):
        queue_for_sync(conn, "session_note", {})
        queue_for_sync(conn, "session_note", {})
        conn.commit()
        first, second = pending_sync_items(conn)
        mark_synced(conn, first["id"])
        assert [r["id"] for r in pending_sync_items(conn)] == [second["id"]]
        sent = conn.execute(
            select(sync_queue.c.sent_at).where(sync_queue.c.id == first["id"])
        ).scalar_one()
        assert sent is not None

    def test_empty_queue(self, conn):
        assert pending_sync_items(conn) == []


# ---- settings --------------------------------------------------------------------
class TestSettings:
    def test_missing_setting_is_none(self, conn):
        assert get_setting(conn, "nope") is None

    def test_set_and_get(self, conn):
        set_setting(conn, "anthropic_api_key", "sk-ant-123")
        assert get_setting(conn, "anthropic_api_key") == "sk-ant-123"

    def test_set_replaces_existing_value(self, conn):
        set_setting(conn, "k", "old")
        set_setting(conn, "k", "new")
        assert get_setting(conn, "k") == "new"
        assert (
            conn.execute(select(func.count()).select_from(app_settings)).scalar_one()
            == 1
        )

    def test_set_updates_timestamp(self, conn):
        set_setting(conn, "k", "v")
        first = conn.execute(select(app_settings.c.updated_at)).scalar_one()
        set_setting(conn, "k", "v2")
        second = conn.execute(select(app_settings.c.updated_at)).scalar_one()
        assert second >= first

    def test_keys_are_independent(self, conn):
        set_setting(conn, "a", "1")
        set_setting(conn, "b", "2")
        delete_setting(conn, "a")
        assert (get_setting(conn, "a"), get_setting(conn, "b")) == (None, "2")

    def test_delete_missing_setting_is_harmless(self, conn):
        delete_setting(conn, "never-set")

    def test_values_are_stored_exactly(self, conn):
        value = "  spaces, unicode ✓, 'quotes' and \"double\" \n newline "
        set_setting(conn, "k", value)
        assert get_setting(conn, "k") == value

    def test_persists_across_connections(self, conn):
        set_setting(conn, "k", "v")
        other = connect()
        assert get_setting(other, "k") == "v"
        other.close()

    def test_value_cannot_be_null(self, conn):
        with pytest.raises(CONSTRAINT_ERRORS):
            set_setting(conn, "k", None)


# ---- profile ----------------------------------------------------------------------
class TestProfile:
    def test_no_profile_returns_none(self, conn):
        assert get_profile(conn) is None

    def test_save_and_get(self, conn):
        save_profile(conn, age=38, sex="male", weight_kg=78.5, sessions_per_week=3)
        p = get_profile(conn)
        assert (p["age"], p["sex"], p["weight_kg"], p["sessions_per_week"]) == (
            38,
            "male",
            78.5,
            3,
        )
        assert p["updated_at"]

    def test_saving_again_updates_the_single_row(self, conn):
        save_profile(conn, age=38, sex="male", weight_kg=78, sessions_per_week=3)
        save_profile(conn, age=39, sex="male", weight_kg=80, sessions_per_week=4.5)
        assert (
            conn.execute(select(func.count()).select_from(profile)).scalar_one() == 1
        )
        p = get_profile(conn)
        assert (p["age"], p["weight_kg"], p["sessions_per_week"]) == (39, 80, 4.5)

    def test_arguments_are_keyword_only(self, conn):
        with pytest.raises(TypeError):
            save_profile(conn, 38, "male", 78, 3)


# ---- sessions ----------------------------------------------------------------------
class TestSessions:
    def test_add_returns_id_and_stores_note(self, conn):
        sid = add_session_note(conn, "Drilled armbars", "good")
        (s,) = list_sessions(conn)
        assert s["id"] == sid
        assert (s["notes"], s["feeling"]) == ("Drilled armbars", "good")
        assert s["occurred_at"] and s["created_at"]

    def test_feeling_is_optional(self, conn):
        add_session_note(conn, "x")
        assert list_sessions(conn)[0]["feeling"] is None

    def test_adding_a_note_queues_it_for_sync(self, conn):
        sid = add_session_note(conn, "Rolled 5 rounds")
        (item,) = pending_sync_items(conn)
        assert item["kind"] == "session_note"
        assert json.loads(item["payload"]) == {
            "session_id": sid,
            "notes": "Rolled 5 rounds",
        }

    def test_list_is_newest_first(self, conn):
        for n in ("first", "second", "third"):
            add_session_note(conn, n)
        assert [s["notes"] for s in list_sessions(conn)] == [
            "third",
            "second",
            "first",
        ]

    def test_list_respects_limit(self, conn):
        for i in range(5):
            add_session_note(conn, f"n{i}")
        assert len(list_sessions(conn, limit=2)) == 2
        assert len(list_sessions(conn)) == 5

    def test_default_limit_is_50(self, conn):
        for i in range(55):
            add_session_note(conn, f"n{i}")
        assert len(list_sessions(conn)) == 50


# ---- skills and flowchart ----------------------------------------------------------
class TestSkills:
    def test_upsert_creates_skill(self, conn):
        sid = upsert_skill(conn, "Armbar", "submission", "learning")
        (node,) = get_flowchart(conn)["nodes"]
        assert node["id"] == sid
        assert (node["name"], node["category"], node["confidence"]) == (
            "Armbar",
            "submission",
            "learning",
        )

    def test_upsert_existing_returns_same_id(self, conn):
        assert upsert_skill(conn, "Armbar") == upsert_skill(conn, "Armbar")
        assert len(get_flowchart(conn)["nodes"]) == 1

    def test_upsert_updates_category_and_confidence(self, conn):
        upsert_skill(conn, "Armbar", "submission", "learning")
        upsert_skill(conn, "Armbar", "finish", "reliable")
        node = get_flowchart(conn)["nodes"][0]
        assert (node["category"], node["confidence"]) == ("finish", "reliable")

    def test_upsert_with_none_keeps_existing_values(self, conn):
        upsert_skill(conn, "Armbar", "submission", "learning")
        upsert_skill(conn, "Armbar")
        node = get_flowchart(conn)["nodes"][0]
        assert (node["category"], node["confidence"]) == ("submission", "learning")

    def test_first_seen_session_is_not_overwritten(self, conn):
        s1 = add_session_note(conn, "one")
        s2 = add_session_note(conn, "two")
        upsert_skill(conn, "Armbar", session_id=s1)
        upsert_skill(conn, "Armbar", session_id=s2)
        assert get_flowchart(conn)["nodes"][0]["first_seen_session_id"] == s1

    def test_skill_names_are_case_sensitive(self, conn):
        upsert_skill(conn, "Armbar")
        upsert_skill(conn, "armbar")
        assert len(get_flowchart(conn)["nodes"]) == 2


class TestEdges:
    def test_edge_creates_missing_skills(self, conn):
        add_skill_edge(conn, "Knee Cut", "Side Control", "leads_to")
        graph = get_flowchart(conn)
        assert {n["name"] for n in graph["nodes"]} == {"Knee Cut", "Side Control"}
        assert graph["edges"] == [
            {"from_name": "Knee Cut", "to_name": "Side Control", "relation": "leads_to"}
        ]

    def test_edge_keeps_existing_skill_details(self, conn):
        upsert_skill(conn, "Knee Cut", "passing", "comfortable")
        add_skill_edge(conn, "Knee Cut", "Side Control", "leads_to")
        knee = next(
            n for n in get_flowchart(conn)["nodes"] if n["name"] == "Knee Cut"
        )
        assert knee["confidence"] == "comfortable"

    def test_duplicate_edge_is_ignored(self, conn):
        add_skill_edge(conn, "A", "B", "leads_to")
        add_skill_edge(conn, "A", "B", "leads_to")
        assert len(get_flowchart(conn)["edges"]) == 1

    def test_same_pair_different_relation_is_kept(self, conn):
        add_skill_edge(conn, "A", "B", "leads_to")
        add_skill_edge(conn, "A", "B", "counters")
        assert {e["relation"] for e in get_flowchart(conn)["edges"]} == {
            "leads_to",
            "counters",
        }

    def test_edges_are_directed(self, conn):
        add_skill_edge(conn, "A", "B", "leads_to")
        add_skill_edge(conn, "B", "A", "leads_to")
        assert len(get_flowchart(conn)["edges"]) == 2

    def test_empty_flowchart(self, conn):
        assert get_flowchart(conn) == {"nodes": [], "edges": []}

    def test_flowchart_is_json_serialisable(self, conn):
        add_skill_edge(conn, "A", "B", "leads_to")
        json.dumps(get_flowchart(conn))  # claude_sync sends this to Claude


# ---- chat --------------------------------------------------------------------------
class TestChat:
    def test_add_and_list(self, conn):
        mid = add_chat_message(conn, "user", "How do I escape mount?")
        (m,) = list_chat_messages(conn)
        assert (m["id"], m["role"], m["content"]) == (
            mid,
            "user",
            "How do I escape mount?",
        )

    def test_list_is_oldest_first(self, conn):
        add_chat_message(conn, "user", "q")
        add_chat_message(conn, "assistant", "a")
        assert [m["role"] for m in list_chat_messages(conn)] == ["user", "assistant"]

    def test_user_messages_are_queued(self, conn):
        add_chat_message(conn, "user", "hello")
        (item,) = pending_sync_items(conn)
        assert item["kind"] == "chat_message"
        assert json.loads(item["payload"]) == {"content": "hello", "session_id": None}

    def test_assistant_messages_are_not_queued(self, conn):
        add_chat_message(conn, "assistant", "hi")
        assert pending_sync_items(conn) == []

    def test_message_can_link_to_session(self, conn):
        sid = add_session_note(conn, "x")
        add_chat_message(conn, "user", "about that session", session_id=sid)
        assert list_chat_messages(conn)[0]["session_id"] == sid

    def test_list_respects_limit(self, conn):
        for i in range(4):
            add_chat_message(conn, "assistant", str(i))
        assert [m["content"] for m in list_chat_messages(conn, limit=2)] == [
            "0",
            "1",
        ]


# ---- recommendations ---------------------------------------------------------------
class TestRecommendations:
    def test_new_recommendation_defaults(self, conn):
        rid = add_recommendation(conn, "Drill hip escapes")
        (r,) = list_recommendations(conn)
        assert (r["id"], r["text"], r["status"], r["related_skill_id"]) == (
            rid,
            "Drill hip escapes",
            "new",
            None,
        )

    def test_linked_to_skill(self, conn):
        skill = upsert_skill(conn, "Hip Escape")
        add_recommendation(conn, "Drill it", skill)
        assert list_recommendations(conn)[0]["related_skill_id"] == skill

    def test_list_newest_first(self, conn):
        add_recommendation(conn, "old")
        add_recommendation(conn, "new")
        assert [r["text"] for r in list_recommendations(conn)] == ["new", "old"]

    def test_filter_by_status(self, conn):
        a = add_recommendation(conn, "a")
        add_recommendation(conn, "b")
        set_recommendation_status(conn, a, "tried")
        assert [r["text"] for r in list_recommendations(conn, status="tried")] == [
            "a"
        ]
        assert [r["text"] for r in list_recommendations(conn, status="new")] == ["b"]
        assert len(list_recommendations(conn)) == 2

    def test_set_status_on_missing_id_does_nothing(self, conn):
        set_recommendation_status(conn, 999, "tried")
        assert list_recommendations(conn) == []
