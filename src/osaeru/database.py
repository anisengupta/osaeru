"""
Local, persistant storage for the app. Everything here works fully offline with SQLite.
"""

from __future__ import annotations
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional, Dict, List

from osaeru.config import SCHEMA


def default_db_path() -> Path:
    """The default path for the SQLite database file, located 
    in the user's home directory under `.osaeru/osaeru.sqlite3`.

    Returns:
        Path: The default path for the SQLite database file.
    """
    return Path.home() / ".osaeru" / "osaeru.sqlite3"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Connects to the SQLite database, creating it and the 
    necessary tables if they don't exist.

    Args:
        db_path (Optional[Path], optional): The database path. Defaults to None.

    Returns:
        sqlite3.Connection: A connection object to the SQLite database.
    """
    db_path = db_path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    
    return conn


def _now() -> str:
    """Creates a timestamp string in ISO 8601 format for the current UTC time.

    Returns:
        str: The current UTC time as an ISO 8601 formatted string.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---- sync queue --------------------------------------------------------
def queue_for_sync(
    conn: sqlite3.Connection,
    kind: str, 
    payload: Dict[str, Any]
) -> None:
    """Queues an item for syncing with the remote server by 
    inserting it into the `sync_queue` table.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        kind (str): The type of the item to be synced (e.g., "session_note", "chat_message").
        payload (Dict[str, Any]): The data associated with the item to be synced, 
        which will be serialized to JSON.
    """
    conn.execute(
        "INSERT INTO sync_queue (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), _now())
    )


def pending_sync_items(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Pends items in the sync queue that have not yet been sent, 
    ordered by their creation time.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.

    Returns:
        List[sqlite3.Row]: A list of rows from the `sync_queue` 
        table representing items that are pending sync.
    """
    return conn.execute(
        "SELECT * FROM sync_queue WHERE sent_at IS NULL ORDER BY created_at ASC"
    ).fetchall()


def mark_synced(conn: sqlite3.Connection, item_id: int) -> None:
    """Marks an item in the sync queue as sent by updating its `sent_at` timestamp.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        item_id (int): The ID of the item in the `sync_queue` table to mark as sent.
    """
    conn.execute("UPDATE sync_queue SET sent_at = ? WHERE id = ?", (_now(), item_id))
    conn.commit()


# ---- profile -----------------------------------------------------------
def save_profile(
    conn: sqlite3.Connection,
    *,
    age: int,
    sex: str,
    weight_kg: float,
    sessions_per_week: float
) -> None:
    """Saves the user's profile information to the database, either inserting 
    a new row or updating the existing one.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        age (int): The user's age.
        sex (str): The user's sex ("male", "female", or "other").
        weight_kg (float): The user's weight in kilograms.
        sessions_per_week (float): The number of training sessions per week.
    """    
    conn.execute(
        """
        INSERT INTO profile (id, age, sex, weight_kg, sessions_per_week, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            age=excluded.age, sex=excluded.sex, weight_kg=excluded.weight_kg,
            sessions_per_week=excluded.sessions_per_week, updated_at=excluded.updated_at
        """,
        (age, sex, weight_kg, sessions_per_week, _now()),
    )
    conn.commit()


def get_profile(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Gets a user's profile information from the database. Returns 
    None if no profile exists.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.

    Returns:
        Optional[sqlite3.Row]: A row from the `profile` table containing 
        the user's profile information, or None if no profile exists.
    """
    return conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()


# ---- sessions / notes ---------------------------------------------------
def add_session_note(
    conn: sqlite3.Connection,
    notes: str,
    feeling: Optional[str] = None
) -> int:
    """Adds a session note to the database and queues it for syncing with the 
    remote server.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        notes (str): The session notes text.
        feeling (Optional[str], optional): The user's feeling. Defaults to None.

    Returns:
        int: _description_
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO sessions (occurred_at, notes, feeling, created_at) VALUES (?, ?, ?, ?)",
        (now, notes, feeling, now),
    )
    
    session_id = cur.lastrowid
    queue_for_sync(
        conn,
        "session_note",
        {
            "session_id": session_id,
            "notes": notes,
        }
    )
    conn.commit()
    
    return session_id


def list_sessions(
    conn: sqlite3.Connection,
    limit: int = 50
) -> List[sqlite3.Row]:
    """Lists the most recent session notes from the database, ordered by their occurrence time.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        limit (int, optional): The number of sessions to retrieve. Defaults to 50.

    Returns:
        List[sqlite3.Row]: A list of rows from the `sessions` table 
        representing the most recent session notes.
    """
    return conn.execute(
        "SELECT * FROM sessions ORDER BY occurred_at DESC LIMIT ?", (limit,)
    ).fetchall()


# ---- skills / flowchart --------------------------------------------------
def upsert_skill(
    conn: sqlite3.Connection,
    name: str,
    category: Optional[str] = None,
    confidence: Optional[str] = None,
    session_id: Optional[int] = None,
) -> int:
    """Upserts a skill into the database. If a skill with the same name already exists, it 
    updates the existing record with the new category, confidence, and updated_at timestamp.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        name (str): The name of the skill to upsert.
        category (Optional[str], optional): The category to update. Defaults to None.
        confidence (Optional[str], optional): The confidence to update. Defaults to None.
        session_id (Optional[int], optional): The session id. Defaults to None.

    Returns:
        int: _description_
    """    
    now = _now()
    conn.execute(
        """
        INSERT INTO skills (name, category, confidence, first_seen_session_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            category=COALESCE(excluded.category, skills.category),
            confidence=COALESCE(excluded.confidence, skills.confidence),
            updated_at=excluded.updated_at
        """,
        (name, category, confidence, session_id, now),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()
    
    return row["id"]


def add_skill_edge(
    conn: sqlite3.Connection,
    from_name: str,
    to_name: str,
    relation: str,
) -> None:
    """Adds a directed edge between two skills in the flowchart, 
    representing a relationship between them.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        from_name (str): The name of the skill from which the edge originates.
        to_name (str): The name of the skill to which the edge points.
        relation (str): The type of relationship between the two skills 
        (e.g., "leads_to", "counters", "depends_on").
    """    
    from_id = upsert_skill(conn, from_name)
    to_id = upsert_skill(conn, to_name)
    conn.execute(
        """
        INSERT OR IGNORE INTO skill_edges (from_skill_id, to_skill_id, relation)
        VALUES (?, ?, ?)
        """,
        (from_id, to_id, relation),
    )
    conn.commit()


def get_flowchart(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Gets the flowchart data from the database, including all skills (nodes) 
    and their relationships (edges).

    Args:
        conn (sqlite3.Connection): The SQLite database connection.

    Returns:
        Dict[str, Any]: A dictionary containing two keys: "nodes" and "edges".
    """    
    nodes = conn.execute("SELECT * FROM skills").fetchall()
    edges = conn.execute(
        """
        SELECT s1.name AS from_name, s2.name AS to_name, e.relation
        FROM skill_edges e
        JOIN skills s1 ON s1.id = e.from_skill_id
        JOIN skills s2 ON s2.id = e.to_skill_id
        """
    ).fetchall()
    
    return {
        "nodes": [dict(node) for node in nodes],
        "edges": [dict(edge) for edge in edges],
    }


# ---- chat -----------------------------------------------------------------
def add_chat_message(
    conn: sqlite3.Connection,
    role: str,
    content: str,
    session_id: Optional[int] = None,
) -> int:
    """Adds a chat message to the database and queues it for syncing with 
    the remote server if it's a user message.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        role (str): The role of the message sender, either "user" or "assistant".
        content (str): The content of the chat message.
        session_id (Optional[int], optional): The session id. Defaults to None.

    Returns:
        int: The ID of the newly inserted chat message.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO chat_messages (role, content, session_id, created_at) VALUES (?, ?, ?, ?)",
        (role, content, session_id, now),
    )
    
    if role == "user":
        queue_for_sync(
            conn,
            "chat_message",
            {
                "content": content,
                "session_id": session_id,
            }
        )
    conn.commit()
    
    return cur.lastrowid


def list_chat_messages(conn: sqlite3.Connection, limit: int = 200) -> List[sqlite3.Row]:
    """Lists chat messages from the database, ordered by their creation time.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        limit (int, optional): The limit of chat messages to retrieve. Defaults to 200.

    Returns:
        List[sqlite3.Row]: A list of rows from the `chat_messages` table representing the chat messages.
    """
    return conn.execute(
        "SELECT * FROM chat_messages ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()


# ---- recommendations --------------------------------------------------
def add_recommendation(
    conn: sqlite3.Connection,
    text: str,
    related_skill_id: Optional[int] = None
) -> int:
    """Adds a recommendation to the database.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        text (str): The text of the recommendation.
        related_skill_id (Optional[int], optional): The skill of the 
        related id to update. Defaults to None.

    Returns:
        int: The ID of the newly inserted recommendation.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO recommendations (text, related_skill_id, created_at) VALUES (?, ?, ?)",
        (text, related_skill_id, now)
    )
    conn.commit()
    
    return cur.lastrowid


def list_recommendations(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Lists recommendations from the database, optionally filtered by their status.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        status (Optional[str], optional): The status to filer with. Defaults to None.

    Returns:
        List[sqlite3.Row]: _description_
    """
    if status:
        return conn.execute(
            "SELECT * FROM recommendations WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    
    return conn.execute("SELECT * FROM recommendations ORDER BY created_at DESC").fetchall()


def set_recommendation_status(
    conn: sqlite3.Connection,
    rec_id: int,
    status: str
) -> None:
    """Sets the status of a recommendation in the database.

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        rec_id (int): The ID of the recommendation to update.
        status (str): The new status to set for the recommendation (e.g., "tried", "skipped").
    """
    conn.execute("UPDATE recommendations SET status = ? WHERE id = ?", (status, rec_id))
    conn.commit()
