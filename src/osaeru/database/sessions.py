"""Funcs to add and get session notes"""

from typing import Optional, List
from sqlalchemy.engine import Connection
from sqlalchemy import select

from osaeru.database.config import Row, sessions
from osaeru.database.helper import _now
from osaeru.database.sync import queue_for_sync


def add_session_note(
    conn: Connection, notes: str, feeling: Optional[str] = None
) -> int:
    """Adds a session note into the database.

    Args:
        conn (Connection): The database connection.
        notes (str): The notes inputed from the user.
        feeling (Optional[str], optional): The feeling
        of the user related to the session. Defaults to None.

    Returns:
        int: The session id of the note just created by the
        user.
    """
    now = _now()
    result = conn.execute(
        sessions.insert().values(
            occurred_at=now, notes=notes, feeling=feeling, created_at=now
        )
    )
    session_id = result.inserted_primary_key[0]
    queue_for_sync(conn, "session_note", {"session_id": session_id, "notes": notes})
    conn.commit()

    return session_id


def list_sessions(conn: Connection, limit: int = 50) -> List[Row]:
    """List the previous sessions of the user.

    Args:
        conn (Connection): The database connection.
        limit (int, optional): The number of sessions
        to retrieve. Defaults to 50.

    Returns:
        List[Row]: A list of previous sessions.
    """
    return (
        conn.execute(
            select(sessions)
            .order_by(sessions.c.occurred_at.desc(), sessions.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )