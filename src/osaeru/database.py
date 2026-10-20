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
    return Path.home() / ".osaeru" / "osaeru.sqlite3"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    
    return conn


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---- sync queue --------------------------------------------------------
def queue_for_sync(
    conn: sqlite3.Connection,
    kind: str, 
    payload: Dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO sync_queue (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), _now())
    )


def pending_sync_items(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sync_queue WHERE sent_at IS NULL ORDER BY created_at ASC"
    ).fetchall()


def mark_synced(conn: sqlite3.Connection, item_id: int) -> None:
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
    return conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()


# ---- sessions / notes ---------------------------------------------------
def add_session_note(
    conn: sqlite3.Connection,
    notes: str,
    feeling: Optional[str] = None
) -> int:
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
    return conn.execute(
        "SELECT * FROM chat_messages ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()


# ---- recommendations --------------------------------------------------
def add_recommendation(
    conn: sqlite3.Connection,
    text: str,
    related_skill_id: Optional[int] = None
) -> int:
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
    conn.execute("UPDATE recommendations SET status = ? WHERE id = ?", (status, rec_id))
    conn.commit()
