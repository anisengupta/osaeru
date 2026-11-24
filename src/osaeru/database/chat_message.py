"""Funcs to deal with chat messages from the db"""

from typing import Optional, List
from sqlalchemy.engine import Connection
from sqlalchemy import select

from osaeru.database.config import Row, chat_messages


def add_chat_message(
    conn: Connection,
    role: str,
    content: str,
    session_id: Optional[int] = None,
) -> int:
    """Adds a chat message to the database.

    Args:
        conn (Connection): The database connection.
        role (str): The role of the user logged in.
        content (str): The content of the chat.
        session_id (Optional[int], optional): The session id.
        Defaults to None.

    Returns:
        int: _description_
    """
    result = conn.execute(
        chat_messages.insert().values(
            role=role,
            content=content,
            session_id=session_id,
            created_at=_now(),
        )
    )
    if role == "user":
        queue_for_sync(
            conn, "chat_message", {"content": content, "session_id": session_id}
        )
    conn.commit()

    return result.inserted_primary_key[0]


def list_chat_messages(conn: Connection, limit: int = 200) -> List[Row]:
    """Lists previous chat messages.

    Args:
        conn (Connection): The database connection.
        limit (int, optional): The limit of chats to retrieve.
        Defaults to 200.

    Returns:
        List[Row]: A list of messages.
    """
    return (
        conn.execute(
            select(chat_messages)
            .order_by(chat_messages.c.created_at, chat_messages.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )