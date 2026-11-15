"""Unit tests for osaeru.database."""

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from osaeru import database as db


# ---- connection and setup -------------------------------------------------------
class TestConnect:
    def test_default_path_is_under_home(self, isolated):
        assert db.default_db_path() == isolated.home / ".osaeru" / "osaeru.sqlite3"

    def test_connect_with_no_path_uses_default(self, isolated):
        c = db.connect()
        c.close()
        assert (isolated.home / ".osaeru" / "osaeru.sqlite3").exists()

    def test_creates_missing_parent_directories(self, tmp_path):
        path = tmp_path / "a" / "b" / "c.sqlite3"
        db.connect(path).close()
        assert path.exists()

    def test_creates_all_tables(self, conn):
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "profile",
            "sessions",
            "skills",
            "skill_edges",
            "chat_messages",
            "recommendations",
            "sync_queue",
        } <= tables

    def test_rows_are_accessible_by_column_name(self, conn):
        assert conn.row_factory is sqlite3.Row

    def test_foreign_keys_enforced(self, conn):
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.add_recommendation(conn, "orphan", related_skill_id=999)

    def test_reconnecting_keeps_data(self, tmp_path):
        path = tmp_path / "x.sqlite3"
        c = db.connect(path)
        db.add_session_note(c, "persisted")
        c.close()
        c = db.connect(path)
        assert [s["notes"] for s in db.list_sessions(c)] == ["persisted"]
        c.close()


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
        sent = conn.execute("SELECT sent_at FROM sync_queue WHERE id=?", (first["id"],)).fetchone()
        assert sent["sent_at"] is not None

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
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 1
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
