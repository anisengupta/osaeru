"""FastAPI web front end for Osaeru.

Server-rendered Jinja templates, with htmx for the few places that need to
update without a full page load. Every form also works as a plain HTML form
(post, then redirect), so nothing breaks if JavaScript doesn't load.

Route handlers are plain `def` functions on purpose: FastAPI runs them in a
threadpool, so the blocking SQLite and Anthropic calls don't stall the server.

This module only uses the existing functions in `database` and `claude_sync`;
it doesn't change them.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlencode

import anthropic
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from osaeru import claude_sync, database as db
from osaeru.web.graph import flowchart_payload

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
FEELINGS = ["great", "good", "okay", "tired", "frustrated"]
SEXES = ["male", "female", "other"]

app = FastAPI(title="Osaeru", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _day(iso: str) -> str:
    try:
        when = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    fmt = "%a %-d %b" if when.year == dt.date.today().year else "%a %-d %b %Y"
    return when.strftime(fmt)


templates.env.filters["day"] = _day


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    path = os.environ.get("OSAERU_DB_PATH")
    conn = db.connect(Path(path).expanduser() if path else None)
    try:
        yield conn
    finally:
        conn.close()


def pending_note_count(conn: sqlite3.Connection) -> int:
    return sum(1 for item in db.pending_sync_items(conn) if item["kind"] == "session_note")


def new_recommendations(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    names = {n["id"]: n["name"] for n in db.get_flowchart(conn)["nodes"]}
    return [
        {**dict(r), "skill_name": names.get(r["related_skill_id"])}
        for r in db.list_recommendations(conn, status="new")
    ]


def recommendation_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    return dict(Counter(r["status"] for r in db.list_recommendations(conn)))


def render(request: Request, template: str, active: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template, {"active": active, "has_key": bool(claude_sync.get_api_key()), **context}
    )


def redirect(path: str, **params) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def friendly_error(exc: Exception) -> str:
    if not claude_sync.get_api_key():
        return "Add your Claude API key in Settings first."
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude rejected the API key. Check it in Settings."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't connect to Claude. Check your connection and try again."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is rate-limiting requests right now. Try again in a minute."
    text = str(exc)
    if "max_tokens" in text:
        return "Claude's reply was cut off before it finished. Try a shorter note."
    return f"Couldn't reach Claude: {text[:200]}"


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return redirect("/notes")


@app.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, saved: Optional[int] = None, error: Optional[str] = None):
    with open_db() as conn:
        return render(
            request,
            "notes.html",
            "notes",
            sessions=db.list_sessions(conn),
            pending=pending_note_count(conn),
            feelings=FEELINGS,
            saved=saved,
            error=error,
        )


@app.post("/notes")
def save_note(notes: str = Form(""), feeling: str = Form("")):
    notes = notes.strip()
    if not notes:
        return redirect("/notes", error="Write something about the session first.")
    with open_db() as conn:
        db.add_session_note(conn, notes, feeling if feeling in FEELINGS else None)
    return redirect("/notes", saved=1)


@app.post("/sync", response_class=HTMLResponse)
def sync(request: Request):
    message, ok = "", True
    try:
        with open_db() as conn:
            before = len(db.get_flowchart(conn)["nodes"])
            processed = claude_sync.run_sync(conn)
            new_skills = len(db.get_flowchart(conn)["nodes"]) - before
        if processed:
            message = (
                f"Analysed {processed} note{'s' if processed != 1 else ''}. "
                f"{new_skills} new skill{'s' if new_skills != 1 else ''} on your flowchart."
            )
        else:
            message = "Everything's already analysed."
    except Exception as exc:  # surfaced to the page, full trace in the log
        logger.exception("Sync failed")
        message, ok = friendly_error(exc), False

    if request.headers.get("HX-Request"):
        return render(request, "_sync_status.html", "", message=message, ok=ok)
    back = request.headers.get("referer", "/notes").split("?")[0]
    return redirect(back, **({"error": message} if not ok else {}))


@app.get("/flowchart", response_class=HTMLResponse)
def flowchart_page(request: Request):
    with open_db() as conn:
        graph = db.get_flowchart(conn)
        return render(
            request,
            "flowchart.html",
            "flowchart",
            node_count=len(graph["nodes"]),
            edge_count=len(graph["edges"]),
            pending=pending_note_count(conn),
        )


@app.get("/api/flowchart")
def flowchart_data() -> JSONResponse:
    with open_db() as conn:
        return JSONResponse(flowchart_payload(db.get_flowchart(conn), new_recommendations(conn)))


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, error: Optional[str] = None):
    with open_db() as conn:
        return render(request, "chat.html", "chat", messages=db.list_chat_messages(conn), error=error)


@app.post("/chat")
def send_chat(message: str = Form("")):
    text = message.strip()
    if not text:
        return redirect("/chat")
    with open_db() as conn:
        db.add_chat_message(conn, "user", text)
        try:
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in db.list_chat_messages(conn)
                if m["role"] in ("user", "assistant")
            ]
            reply = claude_sync.chat_reply(history, db.get_flowchart(conn))
            db.add_chat_message(conn, "assistant", reply)
        except Exception as exc:
            logger.exception("Chat reply failed")
            return redirect("/chat", error=f"{friendly_error(exc)} Your message is saved.")
    return redirect("/chat")


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations_page(request: Request):
    with open_db() as conn:
        return render(
            request,
            "recommendations.html",
            "recommendations",
            recs=new_recommendations(conn),
            counts=recommendation_counts(conn),
        )


@app.post("/recommendations/{rec_id}")
def update_recommendation(rec_id: int, status: str = Form(...)):
    if status in ("tried", "skipped", "new"):
        with open_db() as conn:
            db.set_recommendation_status(conn, rec_id, status)
    return redirect("/recommendations")


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: Optional[int] = None, error: Optional[str] = None):
    with open_db() as conn:
        return render(
            request, "profile.html", "profile",
            profile=db.get_profile(conn), sexes=SEXES, saved=saved, error=error,
        )


@app.post("/profile")
def save_profile(
    age: str = Form(""),
    sex: str = Form(""),
    weight_kg: str = Form(""),
    sessions_per_week: str = Form(""),
):
    try:
        values = dict(
            age=int(age),
            sex=sex if sex in SEXES else "other",
            weight_kg=float(weight_kg),
            sessions_per_week=float(sessions_per_week),
        )
    except ValueError:
        return redirect("/profile", error="Age, weight and sessions per week need to be numbers.")
    with open_db() as conn:
        db.save_profile(conn, **values)
    return redirect("/profile", saved=1)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: Optional[str] = None, error: Optional[str] = None):
    key = claude_sync.get_api_key()
    return render(
        request, "settings.html", "settings",
        key_tail=key[-4:] if key else None, msg=msg, error=error,
    )


@app.post("/settings/key")
def save_key(api_key: str = Form("")):
    key = api_key.strip()
    if not key.startswith("sk-ant-"):
        return redirect("/settings", error="That doesn't look like an Anthropic key. They start with sk-ant-.")
    ok = claude_sync.verify_api_key(key)
    if ok is False:
        return redirect("/settings", error="Anthropic rejected that key, so it wasn't saved.")
    where = claude_sync.set_api_key(key)
    if ok:
        return redirect("/settings", msg=f"Key checked and saved to {where}.")
    return redirect("/settings", msg=f"Key saved to {where}, but couldn't be checked right now.")


@app.post("/settings/key/remove")
def remove_key():
    claude_sync.clear_api_key()
    return redirect("/settings", msg="Key removed.")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"ok": True}
