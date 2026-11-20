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

from osaeru import database as db
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
}


class TestConnect:
    def test_creates_all_tables(self, conn):
        assert TABLES <= set(inspect(conn).get_table_names())

    def test_rows_are_mappings(self, conn):
        db.add_session_note(conn, "x")
        row = db.list_sessions(conn)[0]
        assert row["notes"] == "x"
        assert dict(row)["notes"] == "x"

    def test_foreign_keys_enforced(self, conn):
        with pytest.raises(CONSTRAINT_ERRORS):
            db.add_recommendation(conn, "orphan", related_skill_id=999)

    def test_profile_is_a_single_row(self, conn):
        with pytest.raises(CONSTRAINT_ERRORS):
            conn.execute(db.profile.insert().values(id=2, updated_at="x"))
            conn.commit()

    def test_reconnecting_keeps_data(self):
        c = db.connect()
        db.add_session_note(c, "persisted")
        c.close()
        db.dispose_engines()  # like a restart
        c = db.connect()
        assert [s["notes"] for s in db.list_sessions(c)] == ["persisted"]
        c.close()

    def test_engine_is_reused(self):
        assert db.get_engine() is db.get_engine()

    def test_dispose_engines_forgets_cached_engines(self):
        first = db.get_engine()
        db.dispose_engines()
        assert db.get_engine() is not first

    def test_creating_tables_twice_is_harmless(self):
        db.connect().close()
        db._create_tables(db.get_engine())  # e.g. a second instance starting up
        c = db.connect()
        assert TABLES <= set(inspect(c).get_table_names())
        c.close()

    def test_connections_can_be_closed(self):
        c = db.connect()
        c.close()
        assert c.closed


@postgres_only
def test_parallel_startups_do_not_race():
    """Several Cloud Run instances starting at once all create tables; the
    advisory lock makes them take turns instead of failing."""
    import threading
    from sqlalchemy import create_engine, text

    with db.get_engine().begin() as c:  # start from no tables at all
        c.execute(text("DROP TABLE IF EXISTS " + ", ".join(t.name for t in db.metadata.sorted_tables) + " CASCADE"))
    db.dispose_engines()

    errors = []

    def start_instance():
        engine = create_engine(TEST_PG_URL)
        try:
            db._create_tables(engine)
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
    c = db.connect()
    assert TABLES <= set(inspect(c).get_table_names())
    c.close()


@sqlite_only
class TestSqliteFile:
    def test_default_path_is_under_home(self, isolated, monkeypatch):
        monkeypatch.delenv("OSAERU_DB_PATH")
        assert db.default_db_path() == isolated.home / ".osaeru" / "osaeru.sqlite3"

    def test_env_overrides_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OSAERU_DB_PATH", "~/custom.sqlite3")
        assert db.default_db_path() == Path.home() / "custom.sqlite3"

    def test_connect_with_no_path_uses_default(self, isolated, monkeypatch):
        monkeypatch.delenv("OSAERU_DB_PATH")
        db.connect().close()
        assert (isolated.home / ".osaeru" / "osaeru.sqlite3").exists()

    def test_explicit_path_wins(self, tmp_path):
        path = tmp_path / "a" / "b" / "c.sqlite3"
        db.connect(path).close()
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
        c = db.connect(path)
        assert [s["notes"] for s in db.list_sessions(c)] == ["old note"]
        db.add_session_note(c, "new note")
        assert len(db.list_sessions(c)) == 2
        c.close()


class TestChoosingBackend:
    def test_sqlite_by_default(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        assert db.backend() == "sqlite"

    def test_cloud_sql_when_instance_set(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "p:r:i")
        assert db.backend() == "cloudsql"

    def test_url_wins_over_cloud_sql(self, monkeypatch):
        monkeypatch.setenv("OSAERU_DATABASE_URL", "sqlite://")
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "p:r:i")
        assert db.backend() == "url"

    def test_database_url_is_used(self, tmp_path, monkeypatch):
        target = tmp_path / "via-url.sqlite3"
        monkeypatch.setenv("OSAERU_DATABASE_URL", f"sqlite:///{target}")
        db.connect().close()
        assert target.exists()

    def test_refuses_sqlite_on_cloud_run(self, monkeypatch):
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("K_SERVICE", "osaeru")
        with pytest.raises(RuntimeError, match="CLOUD_SQL_INSTANCE"):
            db.connect()

    def test_explicit_path_allowed_on_cloud_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("K_SERVICE", "osaeru")
        db.connect(tmp_path / "explicit.sqlite3").close()


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
                user=u.username, password=u.password, host=u.host, port=u.port, database=u.database
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
        if hasattr(db, "Connector"):  # imported at the top of database.py
            monkeypatch.setattr(db, "Connector", FakeConnector)
        monkeypatch.setattr(db, "_connector", None)
        monkeypatch.setattr(db.atexit, "register", lambda fn: None)
        monkeypatch.delenv("OSAERU_DATABASE_URL", raising=False)
        monkeypatch.setenv("CLOUD_SQL_INSTANCE", "my-proj:europe-west2:osaeru-db")
        monkeypatch.setenv("DB_NAME", "osaeru")
        monkeypatch.setenv("DB_USER", "osaeru-app")
        monkeypatch.setenv("K_SERVICE", "osaeru")  # pretend we're on Cloud Run

    def _first_connect(self):
        try:
            db.connect().close()
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
        assert kwargs == {"user": "osaeru-app", "db": "osaeru", "ip_type": IPTypes.PUBLIC, "password": "secret"}

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
            db.connect()

    @postgres_only
    def test_end_to_end_through_connector(self):
        c = db.connect()
        db.add_session_note(c, "via Cloud SQL connector")
        assert db.list_sessions(c)[0]["notes"] == "via Cloud SQL connector"
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
    parsed = dt.datetime.fromisoformat(db._now())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == dt.timedelta(0)


# ---- sync queue ------------------------------------------------------------------
class TestSyncQueue:
    def test_queue_item_is_pending_with_json_payload(self, conn):
        db.queue_for_sync(conn, "session_note", {"notes": "hi", "n": 1})
        conn.commit()
        (item,) = db.pending_sync_items(conn)
        assert item["kind"] == "session_note"
        assert json.loads(item["payload"]) == {"notes": "hi", "n": 1}
        assert item["sent_at"] is None

    def test_pending_items_oldest_first(self, conn):
        for i in range(3):
            db.queue_for_sync(conn, "session_note", {"i": i})
        conn.commit()
        assert [json.loads(r["payload"])["i"] for r in db.pending_sync_items(conn)] == [0, 1, 2]

    def test_mark_synced_removes_from_pending(self, conn):
        db.queue_for_sync(conn, "session_note", {})
        db.queue_for_sync(conn, "session_note", {})
        conn.commit()
        first, second = db.pending_sync_items(conn)
        db.mark_synced(conn, first["id"])
        assert [r["id"] for r in db.pending_sync_items(conn)] == [second["id"]]
        sent = conn.execute(
            select(db.sync_queue.c.sent_at).where(db.sync_queue.c.id == first["id"])
        ).scalar_one()
        assert sent is not None

    def test_empty_queue(self, conn):
        assert db.pending_sync_items(conn) == []


# ---- profile ----------------------------------------------------------------------
class TestProfile:
    def test_no_profile_returns_none(self, conn):
        assert db.get_profile(conn) is None

    def test_save_and_get(self, conn):
        db.save_profile(conn, age=38, sex="male", weight_kg=78.5, sessions_per_week=3)
        p = db.get_profile(conn)
        assert (p["age"], p["sex"], p["weight_kg"], p["sessions_per_week"]) == (38, "male", 78.5, 3)
        assert p["updated_at"]

    def test_saving_again_updates_the_single_row(self, conn):
        db.save_profile(conn, age=38, sex="male", weight_kg=78, sessions_per_week=3)
        db.save_profile(conn, age=39, sex="male", weight_kg=80, sessions_per_week=4.5)
        assert conn.execute(select(func.count()).select_from(db.profile)).scalar_one() == 1
        p = db.get_profile(conn)
        assert (p["age"], p["weight_kg"], p["sessions_per_week"]) == (39, 80, 4.5)

    def test_arguments_are_keyword_only(self, conn):
        with pytest.raises(TypeError):
            db.save_profile(conn, 38, "male", 78, 3)


# ---- sessions ----------------------------------------------------------------------
class TestSessions:
    def test_add_returns_id_and_stores_note(self, conn):
        sid = db.add_session_note(conn, "Drilled armbars", "good")
        (s,) = db.list_sessions(conn)
        assert s["id"] == sid
        assert (s["notes"], s["feeling"]) == ("Drilled armbars", "good")
        assert s["occurred_at"] and s["created_at"]

    def test_feeling_is_optional(self, conn):
        db.add_session_note(conn, "x")
        assert db.list_sessions(conn)[0]["feeling"] is None

    def test_adding_a_note_queues_it_for_sync(self, conn):
        sid = db.add_session_note(conn, "Rolled 5 rounds")
        (item,) = db.pending_sync_items(conn)
        assert item["kind"] == "session_note"
        assert json.loads(item["payload"]) == {"session_id": sid, "notes": "Rolled 5 rounds"}

    def test_list_is_newest_first(self, conn):
        for n in ("first", "second", "third"):
            db.add_session_note(conn, n)
        assert [s["notes"] for s in db.list_sessions(conn)] == ["third", "second", "first"]

    def test_list_respects_limit(self, conn):
        for i in range(5):
            db.add_session_note(conn, f"n{i}")
        assert len(db.list_sessions(conn, limit=2)) == 2
        assert len(db.list_sessions(conn)) == 5

    def test_default_limit_is_50(self, conn):
        for i in range(55):
            db.add_session_note(conn, f"n{i}")
        assert len(db.list_sessions(conn)) == 50


# ---- skills and flowchart ----------------------------------------------------------
class TestSkills:
    def test_upsert_creates_skill(self, conn):
        sid = db.upsert_skill(conn, "Armbar", "submission", "learning")
        (node,) = db.get_flowchart(conn)["nodes"]
        assert node["id"] == sid
        assert (node["name"], node["category"], node["confidence"]) == ("Armbar", "submission", "learning")

    def test_upsert_existing_returns_same_id(self, conn):
        assert db.upsert_skill(conn, "Armbar") == db.upsert_skill(conn, "Armbar")
        assert len(db.get_flowchart(conn)["nodes"]) == 1

    def test_upsert_updates_category_and_confidence(self, conn):
        db.upsert_skill(conn, "Armbar", "submission", "learning")
        db.upsert_skill(conn, "Armbar", "finish", "reliable")
        node = db.get_flowchart(conn)["nodes"][0]
        assert (node["category"], node["confidence"]) == ("finish", "reliable")

    def test_upsert_with_none_keeps_existing_values(self, conn):
        db.upsert_skill(conn, "Armbar", "submission", "learning")
        db.upsert_skill(conn, "Armbar")
        node = db.get_flowchart(conn)["nodes"][0]
        assert (node["category"], node["confidence"]) == ("submission", "learning")

    def test_first_seen_session_is_not_overwritten(self, conn):
        s1 = db.add_session_note(conn, "one")
        s2 = db.add_session_note(conn, "two")
        db.upsert_skill(conn, "Armbar", session_id=s1)
        db.upsert_skill(conn, "Armbar", session_id=s2)
        assert db.get_flowchart(conn)["nodes"][0]["first_seen_session_id"] == s1

    def test_skill_names_are_case_sensitive(self, conn):
        db.upsert_skill(conn, "Armbar")
        db.upsert_skill(conn, "armbar")
        assert len(db.get_flowchart(conn)["nodes"]) == 2


class TestEdges:
    def test_edge_creates_missing_skills(self, conn):
        db.add_skill_edge(conn, "Knee Cut", "Side Control", "leads_to")
        graph = db.get_flowchart(conn)
        assert {n["name"] for n in graph["nodes"]} == {"Knee Cut", "Side Control"}
        assert graph["edges"] == [
            {"from_name": "Knee Cut", "to_name": "Side Control", "relation": "leads_to"}
        ]

    def test_edge_keeps_existing_skill_details(self, conn):
        db.upsert_skill(conn, "Knee Cut", "passing", "comfortable")
        db.add_skill_edge(conn, "Knee Cut", "Side Control", "leads_to")
        knee = next(n for n in db.get_flowchart(conn)["nodes"] if n["name"] == "Knee Cut")
        assert knee["confidence"] == "comfortable"

    def test_duplicate_edge_is_ignored(self, conn):
        db.add_skill_edge(conn, "A", "B", "leads_to")
        db.add_skill_edge(conn, "A", "B", "leads_to")
        assert len(db.get_flowchart(conn)["edges"]) == 1

    def test_same_pair_different_relation_is_kept(self, conn):
        db.add_skill_edge(conn, "A", "B", "leads_to")
        db.add_skill_edge(conn, "A", "B", "counters")
        assert {e["relation"] for e in db.get_flowchart(conn)["edges"]} == {"leads_to", "counters"}

    def test_edges_are_directed(self, conn):
        db.add_skill_edge(conn, "A", "B", "leads_to")
        db.add_skill_edge(conn, "B", "A", "leads_to")
        assert len(db.get_flowchart(conn)["edges"]) == 2

    def test_empty_flowchart(self, conn):
        assert db.get_flowchart(conn) == {"nodes": [], "edges": []}

    def test_flowchart_is_json_serialisable(self, conn):
        db.add_skill_edge(conn, "A", "B", "leads_to")
        json.dumps(db.get_flowchart(conn))  # claude_sync sends this to Claude


# ---- chat --------------------------------------------------------------------------
class TestChat:
    def test_add_and_list(self, conn):
        mid = db.add_chat_message(conn, "user", "How do I escape mount?")
        (m,) = db.list_chat_messages(conn)
        assert (m["id"], m["role"], m["content"]) == (mid, "user", "How do I escape mount?")

    def test_list_is_oldest_first(self, conn):
        db.add_chat_message(conn, "user", "q")
        db.add_chat_message(conn, "assistant", "a")
        assert [m["role"] for m in db.list_chat_messages(conn)] == ["user", "assistant"]

    def test_user_messages_are_queued(self, conn):
        db.add_chat_message(conn, "user", "hello")
        (item,) = db.pending_sync_items(conn)
        assert item["kind"] == "chat_message"
        assert json.loads(item["payload"]) == {"content": "hello", "session_id": None}

    def test_assistant_messages_are_not_queued(self, conn):
        db.add_chat_message(conn, "assistant", "hi")
        assert db.pending_sync_items(conn) == []

    def test_message_can_link_to_session(self, conn):
        sid = db.add_session_note(conn, "x")
        db.add_chat_message(conn, "user", "about that session", session_id=sid)
        assert db.list_chat_messages(conn)[0]["session_id"] == sid

    def test_list_respects_limit(self, conn):
        for i in range(4):
            db.add_chat_message(conn, "assistant", str(i))
        assert [m["content"] for m in db.list_chat_messages(conn, limit=2)] == ["0", "1"]


# ---- recommendations ---------------------------------------------------------------
class TestRecommendations:
    def test_new_recommendation_defaults(self, conn):
        rid = db.add_recommendation(conn, "Drill hip escapes")
        (r,) = db.list_recommendations(conn)
        assert (r["id"], r["text"], r["status"], r["related_skill_id"]) == (rid, "Drill hip escapes", "new", None)

    def test_linked_to_skill(self, conn):
        skill = db.upsert_skill(conn, "Hip Escape")
        db.add_recommendation(conn, "Drill it", skill)
        assert db.list_recommendations(conn)[0]["related_skill_id"] == skill

    def test_list_newest_first(self, conn):
        db.add_recommendation(conn, "old")
        db.add_recommendation(conn, "new")
        assert [r["text"] for r in db.list_recommendations(conn)] == ["new", "old"]

    def test_filter_by_status(self, conn):
        a = db.add_recommendation(conn, "a")
        db.add_recommendation(conn, "b")
        db.set_recommendation_status(conn, a, "tried")
        assert [r["text"] for r in db.list_recommendations(conn, status="tried")] == ["a"]
        assert [r["text"] for r in db.list_recommendations(conn, status="new")] == ["b"]
        assert len(db.list_recommendations(conn)) == 2

    def test_set_status_on_missing_id_does_nothing(self, conn):
        db.set_recommendation_status(conn, 999, "tried")
        assert db.list_recommendations(conn) == []