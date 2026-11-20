"""Tests for every page of the web front end (osaeru.web.main).

The database, keychain and Claude are all real-ish but isolated (see conftest):
pages read and write a temp SQLite file, keys go to an in-memory keychain,
and Claude replies are scripted on the fake client.
"""

import datetime as dt
import json
import os
from pathlib import Path

import anthropic
import pytest
from fastapi.testclient import TestClient

from osaeru import claude_sync, database as db
from osaeru.web import main
from conftest import api_error, connection_error, sqlite_only, text_message

PAGES = ["/notes", "/flowchart", "/chat", "/recommendations", "/profile", "/settings"]


@pytest.fixture
def client():
    return TestClient(main.app)


def open_conn():
    """The same database the app is using (SQLite file, or Postgres in Postgres mode)."""
    return db.connect()


def seed(fn):
    c = open_conn()
    try:
        return fn(c)
    finally:
        c.close()


def location(response):
    assert response.status_code == 303, response.text
    return response.headers["location"]


def analysis(**kw):
    return text_message(
        json.dumps(
            {
                "new_skills": kw.get("skills", []),
                "new_edges": kw.get("edges", []),
                "recommendations": kw.get("recs", []),
            }
        )
    )


# ---- shared layout ------------------------------------------------------------------
class TestLayout:
    @pytest.mark.parametrize("path", PAGES)
    def test_page_renders(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "Osaeru" in r.text

    @pytest.mark.parametrize("path", PAGES)
    def test_active_tab_is_marked(self, client, path):
        r = client.get(path)
        assert f'href="{path}" class="tab is-active" aria-current="page"' in r.text

    def test_root_redirects_to_notes(self, client):
        r = client.get("/", follow_redirects=False)
        assert location(r) == "/notes"

    @pytest.mark.parametrize("path", [p for p in PAGES if p != "/settings"])
    def test_no_key_notice(self, client, path):
        assert "Claude isn’t connected yet" in client.get(path).text

    def test_no_key_notice_hidden_on_settings(self, client):
        assert "Claude isn’t connected yet" not in client.get("/settings").text

    def test_no_key_notice_hidden_with_key(self, client, with_key):
        assert "Claude isn’t connected yet" not in client.get("/notes").text

    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"ok": True}

    @pytest.mark.parametrize(
        "path",
        [
            "/static/css/app.css",
            "/static/js/app.js",
            "/static/manifest.webmanifest",
            "/static/icons/icon-180.png",
            "/static/vendor/htmx-2.0.4.min.js",
            "/static/vendor/cytoscape-3.30.4.min.js",
            "/static/vendor/dagre-0.8.5.min.js",
            "/static/vendor/cytoscape-dagre-2.5.0.js",
        ],
    )
    def test_static_assets_served(self, client, path):
        assert client.get(path).status_code == 200

    def test_unknown_page_is_404(self, client):
        assert client.get("/nope").status_code == 404


# ---- notes ----------------------------------------------------------------------------
class TestNotesPage:
    def test_empty_state(self, client):
        r = client.get("/notes")
        assert "How was training?" in r.text
        assert "Your sessions will appear here" in r.text
        assert "not analysed yet" not in r.text

    def test_lists_sessions_newest_first(self, client):
        seed(lambda c: (db.add_session_note(c, "Older note", "tired"), db.add_session_note(c, "Newer note", "great")))
        text = client.get("/notes").text
        assert text.index("Newer note") < text.index("Older note")
        assert "feeling-tired" in text and "feeling-great" in text

    def test_note_text_is_escaped(self, client):
        seed(lambda c: db.add_session_note(c, "<script>alert(1)</script>"))
        text = client.get("/notes").text
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text

    def test_shows_pending_count_with_button(self, client):
        seed(lambda c: (db.add_session_note(c, "a"), db.add_session_note(c, "b")))
        text = client.get("/notes").text
        assert "2 notes not analysed yet." in text
        assert "Analyse now" in text

    def test_saved_with_key_starts_analysis_automatically(self, client, with_key):
        seed(lambda c: db.add_session_note(c, "a"))
        text = client.get("/notes?saved=1").text
        assert 'hx-trigger="load"' in text
        assert "Claude is reading your note" in text

    def test_saved_without_key_does_not_auto_analyse(self, client):
        seed(lambda c: db.add_session_note(c, "a"))
        text = client.get("/notes?saved=1").text
        assert 'hx-trigger="load"' not in text
        assert "Analyse now" in text

    def test_shows_error(self, client):
        assert "Something broke" in client.get("/notes?error=Something+broke").text

    def test_post_saves_and_redirects(self, client):
        r = client.post("/notes", data={"notes": "  Drilled knee cut  ", "feeling": "good"}, follow_redirects=False)
        assert location(r) == "/notes?saved=1"
        (s,) = seed(db.list_sessions)
        assert (s["notes"], s["feeling"]) == ("Drilled knee cut", "good")

    def test_unknown_feeling_saved_as_none(self, client):
        client.post("/notes", data={"notes": "x", "feeling": "ecstatic"})
        assert seed(db.list_sessions)[0]["feeling"] is None

    @pytest.mark.parametrize("notes", ["", "   ", "\n\t"])
    def test_empty_note_rejected(self, client, notes):
        r = client.post("/notes", data={"notes": notes}, follow_redirects=False)
        assert "error=" in location(r)
        assert seed(db.list_sessions) == []

    def test_missing_field_rejected(self, client):
        r = client.post("/notes", data={}, follow_redirects=False)
        assert "error=" in location(r)


# ---- sync (Analyse now) ------------------------------------------------------------------
class TestSync:
    HX = {"HX-Request": "true"}

    def test_success_fragment(self, client, with_key, fake_claude):
        seed(lambda c: db.add_session_note(c, "x"))
        fake_claude.replies = [analysis(skills=[{"name": "Armbar"}, {"name": "Mount"}])]
        r = client.post("/sync", headers=self.HX)
        assert "Analysed 1 note. 2 new skills on your flowchart." in r.text
        assert "is-ok" in r.text and 'href="/flowchart"' in r.text
        assert seed(main.pending_note_count) == 0

    def test_pluralisation(self, client, with_key, fake_claude):
        seed(lambda c: (db.add_session_note(c, "a"), db.add_session_note(c, "b")))
        fake_claude.replies = [analysis(skills=[{"name": "Armbar"}]), analysis()]
        r = client.post("/sync", headers=self.HX)
        assert "Analysed 2 notes. 1 new skill on your flowchart." in r.text

    def test_nothing_to_do(self, client, with_key, fake_claude):
        r = client.post("/sync", headers=self.HX)
        assert "Everything&#39;s already analysed." in r.text
        assert fake_claude.calls == []

    def test_no_key(self, client):
        seed(lambda c: db.add_session_note(c, "x"))
        r = client.post("/sync", headers=self.HX)
        assert "Add your Claude API key in Settings first." in r.text
        assert "is-error" in r.text
        assert seed(main.pending_note_count) == 1

    def test_claude_error_shown_and_note_kept(self, client, with_key, fake_claude):
        seed(lambda c: db.add_session_note(c, "x"))
        fake_claude.replies = [api_error(anthropic.AuthenticationError, 401)]
        r = client.post("/sync", headers=self.HX)
        assert "Claude rejected the API key" in r.text
        assert seed(main.pending_note_count) == 1

    def test_plain_post_redirects_back(self, client, with_key, fake_claude):
        r = client.post("/sync", headers={"referer": "http://testserver/flowchart?x=1"}, follow_redirects=False)
        assert location(r) == "http://testserver/flowchart"

    def test_plain_post_error_redirects_with_message(self, client):
        seed(lambda c: db.add_session_note(c, "x"))
        r = client.post("/sync", follow_redirects=False)
        assert location(r).startswith("/notes?error=")


# ---- flowchart ------------------------------------------------------------------------------
class TestFlowchartPage:
    def test_empty_state(self, client):
        text = client.get("/flowchart").text
        assert "No skills yet" in text
        assert 'id="chart"' not in text

    def test_counts_and_chart_container(self, client):
        seed(lambda c: (db.add_skill_edge(c, "A", "B", "leads_to"), db.add_skill_edge(c, "B", "C", "leads_to")))
        text = client.get("/flowchart").text
        assert "3 skills, 2 connections" in text
        assert 'data-src="/api/flowchart"' in text

    def test_singular_counts(self, client):
        seed(lambda c: db.upsert_skill(c, "A"))
        assert "1 skill, 0 connections" in client.get("/flowchart").text

    def test_shows_pending_button_but_never_auto_runs(self, client, with_key):
        seed(lambda c: db.add_session_note(c, "x"))
        text = client.get("/flowchart").text
        assert "1 note not analysed yet." in text
        assert 'hx-trigger="load"' not in text


class TestFlowchartApi:
    def test_empty(self, client):
        assert client.get("/api/flowchart").json() == {"nodes": [], "edges": []}

    def test_nodes_edges_and_recommendations(self, client):
        def setup(c):
            db.upsert_skill(c, "Knee Cut", "passing", "Beginner")
            db.add_skill_edge(c, "Knee Cut", "Side Control", "leads_to")
            rec = db.add_recommendation(c, "Win the underhook", db.upsert_skill(c, "Knee Cut"))
            done = db.add_recommendation(c, "Old tip", db.upsert_skill(c, "Knee Cut"))
            db.set_recommendation_status(c, done, "tried")

        seed(setup)
        data = client.get("/api/flowchart").json()
        knee = next(n["data"] for n in data["nodes"] if n["data"]["id"] == "Knee Cut")
        assert knee == {
            "id": "Knee Cut",
            "label": "Knee Cut",
            "category": "passing",
            "confidence": "learning",
            "recommendations": ["Win the underhook"],  # tried ones are left out
        }
        (edge,) = data["edges"]
        assert edge["data"]["source"] == "Knee Cut" and edge["data"]["target"] == "Side Control"


# ---- chat ------------------------------------------------------------------------------------
class TestChatPage:
    def test_empty_state(self, client):
        assert "Ask about anything from your training" in client.get("/chat").text

    def test_shows_conversation(self, client):
        seed(lambda c: (db.add_chat_message(c, "user", "My question"), db.add_chat_message(c, "assistant", "My answer")))
        text = client.get("/chat").text
        assert text.index("My question") < text.index("My answer")
        assert 'class="msg msg-user"' in text and 'class="msg msg-assistant"' in text

    def test_message_text_escaped(self, client):
        seed(lambda c: db.add_chat_message(c, "assistant", "<b>bold</b>"))
        assert "&lt;b&gt;bold&lt;/b&gt;" in client.get("/chat").text

    def test_send_stores_both_sides(self, client, with_key, fake_claude):
        seed(lambda c: db.add_skill_edge(c, "Mount", "Armbar", "leads_to"))
        fake_claude.replies = [text_message("Keep your elbows tight.")]
        r = client.post("/chat", data={"message": "  How do I keep mount?  "}, follow_redirects=False)
        assert location(r) == "/chat"
        msgs = [(m["role"], m["content"]) for m in seed(db.list_chat_messages)]
        assert msgs == [("user", "How do I keep mount?"), ("assistant", "Keep your elbows tight.")]
        (call,) = fake_claude.calls
        assert call["messages"] == [{"role": "user", "content": "How do I keep mount?"}]
        assert "Armbar" in call["system"]

    def test_send_includes_history(self, client, with_key, fake_claude):
        seed(lambda c: (db.add_chat_message(c, "user", "q1"), db.add_chat_message(c, "assistant", "a1")))
        fake_claude.replies = [text_message("a2")]
        client.post("/chat", data={"message": "q2"})
        assert [m["content"] for m in fake_claude.calls[0]["messages"]] == ["q1", "a1", "q2"]

    def test_blank_message_does_nothing(self, client, fake_claude):
        r = client.post("/chat", data={"message": "   "}, follow_redirects=False)
        assert location(r) == "/chat"
        assert seed(db.list_chat_messages) == []

    @pytest.mark.parametrize(
        "error, expected",
        [
            (api_error(anthropic.AuthenticationError, 401), "Claude rejected the API key"),
            (connection_error(), "Couldn"),
            (api_error(anthropic.RateLimitError, 429), "rate-limiting"),
        ],
    )
    def test_error_keeps_message(self, client, with_key, fake_claude, error, expected):
        fake_claude.replies = [error]
        r = client.post("/chat", data={"message": "hello"}, follow_redirects=False)
        follow = client.get(location(r)).text
        assert expected in follow and "Your message is saved." in follow
        assert [m["content"] for m in seed(db.list_chat_messages)] == ["hello"]

    def test_no_key(self, client):
        r = client.post("/chat", data={"message": "hello"}, follow_redirects=False)
        assert "Add+your+Claude+API+key" in location(r)


# ---- recommendations ---------------------------------------------------------------------
class TestRecommendationsPage:
    def test_empty_state(self, client):
        text = client.get("/recommendations").text
        assert "Nothing to try yet" in text
        assert "So far" not in text

    def test_lists_new_with_skill_name(self, client):
        def setup(c):
            db.add_recommendation(c, "Drill hip escapes", db.upsert_skill(c, "Hip Escape"))
            db.add_recommendation(c, "General conditioning")

        seed(setup)
        text = client.get("/recommendations").text
        assert "Drill hip escapes" in text and "Hip Escape" in text
        assert "General conditioning" in text
        assert text.count("Tried it") == 2

    def test_hides_tried_and_skipped_and_shows_tally(self, client):
        def setup(c):
            a = db.add_recommendation(c, "Tried one")
            b = db.add_recommendation(c, "Skipped one")
            db.add_recommendation(c, "Still new")
            db.set_recommendation_status(c, a, "tried")
            db.set_recommendation_status(c, b, "skipped")

        seed(setup)
        text = client.get("/recommendations").text
        assert "Still new" in text
        assert "Tried one" not in text and "Skipped one" not in text
        assert "tried 1 and skipped 1" in text

    @pytest.mark.parametrize("status", ["tried", "skipped", "new"])
    def test_update_status(self, client, status):
        rid = seed(lambda c: db.add_recommendation(c, "x"))
        r = client.post(f"/recommendations/{rid}", data={"status": status}, follow_redirects=False)
        assert location(r) == "/recommendations"
        assert seed(db.list_recommendations)[0]["status"] == status

    def test_invalid_status_ignored(self, client):
        rid = seed(lambda c: db.add_recommendation(c, "x"))
        client.post(f"/recommendations/{rid}", data={"status": "deleted"})
        assert seed(db.list_recommendations)[0]["status"] == "new"

    def test_missing_status_is_422(self, client):
        rid = seed(lambda c: db.add_recommendation(c, "x"))
        assert client.post(f"/recommendations/{rid}", data={}).status_code == 422

    def test_non_numeric_id_is_422(self, client):
        assert client.post("/recommendations/abc", data={"status": "tried"}).status_code == 422


# ---- profile --------------------------------------------------------------------------------
class TestProfilePage:
    VALID = {"age": "38", "sex": "male", "weight_kg": "78.5", "sessions_per_week": "3"}

    def test_empty_form(self, client):
        text = client.get("/profile").text
        assert 'name="age" inputmode="numeric" min="5" max="100" value=""' in text

    def test_prefilled(self, client):
        seed(lambda c: db.save_profile(c, age=38, sex="female", weight_kg=61.5, sessions_per_week=4))
        text = client.get("/profile").text
        assert 'value="38"' in text and 'value="61.5"' in text
        assert '<option value="female" selected>' in text

    def test_save(self, client):
        r = client.post("/profile", data=self.VALID, follow_redirects=False)
        assert location(r) == "/profile?saved=1"
        p = seed(db.get_profile)
        assert (p["age"], p["sex"], p["weight_kg"], p["sessions_per_week"]) == (38, "male", 78.5, 3.0)
        assert "Profile saved." in client.get("/profile?saved=1").text

    def test_unknown_sex_saved_as_other(self, client):
        client.post("/profile", data={**self.VALID, "sex": "x"})
        assert seed(db.get_profile)["sex"] == "other"

    @pytest.mark.parametrize("field", ["age", "weight_kg", "sessions_per_week"])
    @pytest.mark.parametrize("value", ["", "abc"])
    def test_non_numbers_rejected(self, client, field, value):
        r = client.post("/profile", data={**self.VALID, field: value}, follow_redirects=False)
        assert "error=" in location(r)
        assert seed(db.get_profile) is None

    def test_decimal_age_rejected(self, client):
        r = client.post("/profile", data={**self.VALID, "age": "38.5"}, follow_redirects=False)
        assert "error=" in location(r)

    def test_error_shown(self, client):
        assert "need to be numbers" in client.get("/profile?error=Age%2C+weight+and+sessions+per+week+need+to+be+numbers.").text


# ---- settings ----------------------------------------------------------------------------------
class TestSettingsPage:
    def test_no_key(self, client):
        text = client.get("/settings").text
        assert "No key yet." in text
        assert "Paste your key" in text
        assert "Remove key" not in text

    def test_shows_only_last_four_characters(self, client, with_key):
        text = client.get("/settings").text
        assert "…1234" in text
        assert with_key not in text
        assert "Replace key" in text and "Remove key" in text

    def test_save_verified_key(self, client, fake_claude):
        r = client.post("/settings/key", data={"api_key": "  sk-ant-good-9999 "}, follow_redirects=False)
        assert "Key+checked+and+saved+to+keychain" in location(r)
        assert claude_sync.get_api_key() == "sk-ant-good-9999"

    def test_save_when_check_unavailable(self, client, fake_claude):
        fake_claude.models_error = connection_error()
        r = client.post("/settings/key", data={"api_key": "sk-ant-offline"}, follow_redirects=False)
        assert "couldn%27t+be+checked" in location(r)
        assert claude_sync.get_api_key() == "sk-ant-offline"

    def test_rejected_key_not_saved(self, client, fake_claude):
        fake_claude.models_error = api_error(anthropic.AuthenticationError, 401)
        r = client.post("/settings/key", data={"api_key": "sk-ant-bad"}, follow_redirects=False)
        assert "rejected+that+key" in location(r)
        assert claude_sync.get_api_key() is None

    @pytest.mark.parametrize("value", ["", "hello", "sk-proj-123"])
    def test_wrong_format_not_checked_or_saved(self, client, fake_claude, value):
        r = client.post("/settings/key", data={"api_key": value}, follow_redirects=False)
        assert "error=" in location(r)
        assert fake_claude.inits == []
        assert claude_sync.get_api_key() is None

    def test_remove(self, client, with_key):
        r = client.post("/settings/key/remove", follow_redirects=False)
        assert "Key+removed" in location(r)
        assert claude_sync.get_api_key() is None

    def test_messages_shown(self, client):
        text = client.get("/settings?msg=All+good&error=Not+good").text
        assert "All good" in text and "Not good" in text


# ---- helpers in main.py ----------------------------------------------------------------------------
class TestHelpers:
    def test_day_this_year(self):
        this_year = dt.date.today().year
        assert main._day(f"{this_year}-11-06T18:30:00+00:00").endswith("6 Nov")

    def test_day_other_year_includes_year(self):
        assert main._day("2020-03-01T10:00:00+00:00") == "Sun 1 Mar 2020"

    @pytest.mark.parametrize("value, expected", [("garbage", "garbage"), (None, ""), ("", "")])
    def test_day_bad_input(self, value, expected):
        assert main._day(value) == expected

    def test_redirect_drops_none_params(self):
        assert main.redirect("/x", a=1, b=None).headers["location"] == "/x?a=1"
        assert main.redirect("/x").headers["location"] == "/x"

    def test_pending_note_count_ignores_chat(self):
        def setup(c):
            db.add_session_note(c, "x")
            db.add_chat_message(c, "user", "y")

        seed(setup)
        assert seed(main.pending_note_count) == 1

    def test_recommendation_counts(self):
        def setup(c):
            a = db.add_recommendation(c, "a")
            db.add_recommendation(c, "b")
            db.set_recommendation_status(c, a, "tried")

        seed(setup)
        assert seed(main.recommendation_counts) == {"tried": 1, "new": 1}

    @sqlite_only
    def test_open_db_uses_env_path(self, tmp_path, monkeypatch):
        target = tmp_path / "elsewhere.sqlite3"
        monkeypatch.setenv("OSAERU_DB_PATH", str(target))
        with main.open_db() as c:
            db.add_session_note(c, "x")
        assert target.exists()

    @sqlite_only
    def test_open_db_defaults_to_home(self, isolated, monkeypatch):
        monkeypatch.delenv("OSAERU_DB_PATH")
        with main.open_db():
            pass
        assert (isolated.home / ".osaeru" / "osaeru.sqlite3").exists()

    def test_open_db_closes_connection(self):
        with main.open_db() as c:
            pass
        assert c.closed


class TestFriendlyError:
    def test_no_key(self):
        assert main.friendly_error(Exception()) == "Add your Claude API key in Settings first."

    @pytest.mark.parametrize(
        "error, expected",
        [
            (api_error(anthropic.AuthenticationError, 401), "Claude rejected the API key. Check it in Settings."),
            (connection_error(), "Couldn't connect to Claude. Check your connection and try again."),
            (api_error(anthropic.RateLimitError, 429), "Claude is rate-limiting requests right now. Try again in a minute."),
            (RuntimeError("hit max_tokens"), "Claude's reply was cut off before it finished. Try a shorter note."),
        ],
    )
    def test_known_errors(self, with_key, error, expected):
        assert main.friendly_error(error) == expected

    def test_other_errors_truncated(self, with_key):
        msg = main.friendly_error(ValueError("x" * 500))
        assert msg.startswith("Couldn't reach Claude: ")
        assert len(msg) == len("Couldn't reach Claude: ") + 200


# ---- entry point (python -m osaeru.web) ------------------------------------------------------
class TestEntryPoint:
    @pytest.fixture
    def run(self, monkeypatch):
        import uvicorn
        from osaeru import logging_config
        from osaeru.web import __main__ as entry

        calls = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
        monkeypatch.setattr(logging_config, "configure", lambda: calls.setdefault("logged", True))
        return entry.main, calls

    def test_defaults_to_localhost_8000(self, run, monkeypatch):
        main_fn, calls = run
        for var in ("OSAERU_HOST", "PORT", "OSAERU_RELOAD"):
            monkeypatch.delenv(var, raising=False)
        main_fn()
        assert calls == {"app": "osaeru.web.main:app", "host": "127.0.0.1", "port": 8000, "reload": False, "logged": True}

    def test_env_overrides(self, run, monkeypatch):
        main_fn, calls = run
        monkeypatch.setenv("OSAERU_HOST", "0.0.0.0")
        monkeypatch.setenv("PORT", "9090")
        monkeypatch.setenv("OSAERU_RELOAD", "1")
        main_fn()
        assert (calls["host"], calls["port"], calls["reload"]) == ("0.0.0.0", 9090, True)