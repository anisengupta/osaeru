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
    """
    Convert an ISO 8601 formatted date string into a human-readable date string.
    Args:
        iso (str): An ISO 8601 formatted date string.
    Returns:
        str: A formatted date string. If the input is invalid, returns the input string or an empty string.
             The format is "%a %-d %b" for dates in the current year, and "%a %-d %b %Y" for other years.
    """
    try:
        when = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    fmt = "%a %-d %b" if when.year == dt.date.today().year else "%a %-d %b %Y"
    return when.strftime(fmt)


templates.env.filters["day"] = _day


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    """
    Context manager for opening a SQLite database connection.
    This function retrieves the database path from the environment variable
    'OSAERU_DB_PATH'. If the variable is not set, it defaults to using an
    in-memory database. The function yields a SQLite connection object
    and ensures that the connection is properly closed after use.
    Yields:
        sqlite3.Connection: A SQLite database connection object.
    Raises:
        sqlite3.Error: If there is an issue connecting to the database.
    """
    path = os.environ.get("OSAERU_DB_PATH")
    conn = db.connect(Path(path).expanduser() if path else None)
    try:
        yield conn
    finally:
        conn.close()


def pending_note_count(conn: sqlite3.Connection) -> int:
    """
    Calculate the number of pending session notes in the database.
    This function iterates through the pending synchronization items retrieved
    from the database and counts the items where the "kind" is "session_note".
    Args:
        conn (sqlite3.Connection): A connection object to the SQLite database.
    Returns:
        int: The count of pending session notes.
    """
    return sum(1 for item in db.pending_sync_items(conn) if item["kind"] == "session_note")


def new_recommendations(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Retrieve a list of new recommendations from the database, including the skill names
    associated with each recommendation.
    Args:
        conn (sqlite3.Connection): A connection object to the SQLite database.
    Returns:
        List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents
        a recommendation. Each dictionary includes all fields from the recommendation
        record and an additional "skill_name" field that maps the "related_skill_id"
        to the corresponding skill name.
    """
    names = {n["id"]: n["name"] for n in db.get_flowchart(conn)["nodes"]}
    return [
        {**dict(r), "skill_name": names.get(r["related_skill_id"])}
        for r in db.list_recommendations(conn, status="new")
    ]


def recommendation_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Retrieves the count of recommendations grouped by their status.
    Args:
        conn (sqlite3.Connection): A connection object to the SQLite database.
    Returns:
        Dict[str, int]: A dictionary where the keys are recommendation statuses 
        and the values are the counts of recommendations for each status.
    """
    return dict(Counter(r["status"] for r in db.list_recommendations(conn)))


def render(request: Request, template: str, active: str, **context) -> HTMLResponse:
    """
    Renders an HTML response using the specified template and context.
    Args:
        request (Request): The HTTP request object.
        template (str): The name of the template to render.
        active (str): The name of the active page or section.
        **context: Additional context variables to pass to the template.
    Returns:
        HTMLResponse: The rendered HTML response.
    """
    return templates.TemplateResponse(
        request, template, {"active": active, "has_key": bool(claude_sync.get_api_key()), **context}
    )


def redirect(path: str, **params) -> RedirectResponse:
    """
    Generate a redirect response with optional query parameters.
    Args:
        path (str): The target URL or path to redirect to.
        **params: Arbitrary keyword arguments representing query parameters. 
                  Only parameters with non-None values will be included in the query string.
    Returns:
        RedirectResponse: A response object that redirects the client to the specified path 
                          with a 303 status code (See Other).
    """
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def friendly_error(exc: Exception) -> str:
    """
    Generate a user-friendly error message based on the provided exception.
    This function interprets specific exceptions related to the Claude API 
    and returns a corresponding error message that can be displayed to the user.
    Args:
        exc (Exception): The exception to interpret.
    Returns:
        str: A user-friendly error message describing the issue.
    Specific Errors Handled:
        - If the Claude API key is missing, prompts the user to add it in Settings.
        - If the API key is invalid, notifies the user to check it in Settings.
        - If there is a connection issue with Claude, advises checking the network.
        - If Claude is rate-limiting requests, suggests trying again later.
        - If the response from Claude is cut off due to token limits, recommends a shorter note.
        - For other exceptions, returns a generic error message with the exception details.
    """
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
    """
    Redirects the user to the notes page.
    Returns:
        RedirectResponse: A response object that redirects the user to the "/notes" URL.
    """
    return redirect("/notes")


@app.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, saved: Optional[int] = None, error: Optional[str] = None):
    """
    Renders the notes page with the provided request and optional parameters.
    Args:
        request (Request): The HTTP request object.
        saved (Optional[int], optional): An optional parameter indicating the ID of a saved note. Defaults to None.
        error (Optional[str], optional): An optional error message to display on the page. Defaults to None.
    Returns:
        HttpResponse: The rendered notes page with the provided context, including sessions, pending notes count, 
                      feelings, and any saved or error information.
    """
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
    """
    Handles saving a note for a session.
    This function processes the input notes and feeling, validates the notes, 
    and saves the session note to the database. If the notes are empty, it redirects 
    the user back to the notes page with an error message. The feeling is only saved 
    if it matches one of the predefined feelings.
    Args:
        notes (str): The content of the note provided by the user. Defaults to an empty string.
        feeling (str): The feeling associated with the note. Defaults to an empty string.
    Returns:
        A redirect response to the notes page, either with an error message if the notes 
        are empty or with a success indicator if the note is saved successfully.
    """
    notes = notes.strip()
    if not notes:
        return redirect("/notes", error="Write something about the session first.")
    with open_db() as conn:
        db.add_session_note(conn, notes, feeling if feeling in FEELINGS else None)
    return redirect("/notes", saved=1)


@app.post("/sync", response_class=HTMLResponse)
def sync(request: Request):
    """
    Handles the synchronization process for analyzing notes and updating the flowchart.
    This function processes notes, updates the flowchart with new skills, and returns
    a response indicating the result of the synchronization. It handles both standard
    and HTMX requests.
    Args:
        request (Request): The HTTP request object containing headers and other request data.
    Returns:
        HttpResponse: A rendered HTML response or a redirect response depending on the request type.
    Raises:
        Exception: Logs and handles any exceptions that occur during the synchronization process.
    Notes:
        - If the synchronization is successful, a message is generated indicating the number
          of notes analyzed and new skills added to the flowchart.
        - If no new notes are processed, a message indicates that everything is already analyzed.
        - In case of an error, a user-friendly error message is returned.
    """
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
    """
    Handles the rendering of the flowchart page.
    This function retrieves the flowchart data from the database, calculates
    the number of nodes and edges, and renders the "flowchart.html" template
    with the relevant context.
    Args:
        request (Request): The HTTP request object.
    Returns:
        HttpResponse: The rendered HTML response for the flowchart page.
    """
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
    """
    Generate and return flowchart data as a JSON response.
    This function retrieves flowchart data and new recommendations from the database
    and constructs a JSON response containing the payload.
    Returns:
        JSONResponse: A JSON response containing the flowchart data and new recommendations.
    """
    with open_db() as conn:
        return JSONResponse(flowchart_payload(db.get_flowchart(conn), new_recommendations(conn)))


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, error: Optional[str] = None):
    """
    Renders the chat page with a list of chat messages and an optional error message.
    Args:
        request (Request): The HTTP request object.
        error (Optional[str], optional): An optional error message to display on the page. Defaults to None.
    Returns:
        HttpResponse: The rendered chat page with the provided messages and error (if any).
    """
    with open_db() as conn:
        return render(request, "chat.html", "chat", messages=db.list_chat_messages(conn), error=error)


@app.post("/chat")
def send_chat(message: str = Form("")):
    """
    Handles the chat message sent by the user, processes it, and generates a reply.
    This function takes a user-provided chat message, validates it, and stores it in the database.
    It then retrieves the chat history and generates a reply using the `claude_sync.chat_reply` method.
    The reply is also stored in the database. If an error occurs during the reply generation,
    the error is logged, and the user is redirected with an error message.
    Args:
        message (str): The chat message sent by the user. Defaults to an empty string.
    Returns:
        Response: A redirect response to the chat page, optionally with an error message.
    """
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
    """
    Handles the recommendations page request and renders the corresponding HTML template.
    Args:
        request (Request): The HTTP request object.
    Returns:
        HttpResponse: The rendered HTML response for the recommendations page.
    This function establishes a database connection, retrieves new recommendations
    and their counts, and renders the "recommendations.html" template with the
    retrieved data.
    """
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
    """
    Update the status of a recommendation.
    Args:
        rec_id (int): The unique identifier of the recommendation to update.
        status (str): The new status for the recommendation. Must be one of 
                      "tried", "skipped", or "new". Defaults to a required 
                      form input.
    Returns:
        Response: A redirect response to the "/recommendations" page.
    Raises:
        ValueError: If the provided status is not one of the allowed values.
    Notes:
        This function interacts with the database to update the status of a 
        recommendation and ensures the status is valid before proceeding.
    """
    if status in ("tried", "skipped", "new"):
        with open_db() as conn:
            db.set_recommendation_status(conn, rec_id, status)
    return redirect("/recommendations")


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: Optional[int] = None, error: Optional[str] = None):
    """
    Render the profile page with user profile data and optional status messages.
    Args:
        request (Request): The HTTP request object.
        saved (Optional[int], optional): An optional flag indicating if the profile was successfully saved. Defaults to None.
        error (Optional[str], optional): An optional error message to display on the page. Defaults to None.
    Returns:
        HttpResponse: The rendered profile page with user data and additional context.
    """
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
    """
    Save user profile information to the database.
    This function processes user input from a form, validates the data, and saves
    the profile information to the database. If the input is invalid, it redirects
    the user back to the profile page with an error message.
    Args:
        age (str): The age of the user as a string. Defaults to an empty string.
        sex (str): The sex of the user as a string. Defaults to an empty string.
                   Expected values are defined in the `SEXES` constant.
        weight_kg (str): The weight of the user in kilograms as a string. Defaults to an empty string.
        sessions_per_week (str): The number of workout sessions per week as a string. Defaults to an empty string.
    Returns:
        Response: A redirect response to the profile page. If the input is invalid,
                  an error message is included. If the profile is successfully saved,
                  a success flag is included.
    """
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
    """
    Render the settings page with optional message and error details.
    Args:
        request (Request): The HTTP request object.
        msg (Optional[str], optional): An optional message to display on the settings page. Defaults to None.
        error (Optional[str], optional): An optional error message to display on the settings page. Defaults to None.
    Returns:
        HttpResponse: The rendered settings page with the provided context.
    """
    key = claude_sync.get_api_key()
    return render(
        request, "settings.html", "settings",
        key_tail=key[-4:] if key else None, msg=msg, error=error,
    )


@app.post("/settings/key")
def save_key(api_key: str = Form("")):
    """
    Handles the saving of an Anthropic API key.
    This function validates the provided API key, checks its format, and verifies it with Anthropic's service. 
    If the key is valid, it is saved and an appropriate success message is returned. If the key is invalid 
    or verification fails, an error message is returned.
    Args:
        api_key (str): The API key provided by the user. Defaults to an empty string.
    Returns:
        Response: A redirect response to the settings page with either a success or error message.
    """
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
    """
    Removes the stored API key and redirects the user to the settings page.
    This function clears the API key using the `claude_sync.clear_api_key()` method 
    and then redirects the user to the "/settings" page with a message indicating 
    that the key has been removed.
    Returns:
        flask.Response: A redirect response to the "/settings" page with a message.
    """
    claude_sync.clear_api_key()
    return redirect("/settings", msg="Key removed.")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """
    Health check endpoint.
    This function returns a dictionary indicating the health status of the application.
    It is typically used to verify that the application is running and responsive.
    Returns:
        dict: A dictionary containing the health status, with a key "ok" set to True.
    """
    return {"ok": True}
