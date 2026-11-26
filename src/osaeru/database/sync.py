"""Funcs for dealing with the sync with the db"""

from typing import Dict, Any, List
from sqlalchemy.engine import Connection
from sqlalchemy import update, select
import json

from osaeru.database.config import Row, sync_queue
from osaeru.database.helper import _now


def queue_for_sync(conn: Connection, kind: str, payload: Dict[str, Any]) -> None:
    """Creates a queue for a Claude sync.

    Args:
        conn (Connection): The database connection.
        kind (str): The kind to insert.
        payload (Dict[str, Any]): The payload to insert.
    """
    conn.execute(
        sync_queue.insert().values(
            kind=kind, payload=json.dumps(payload), created_at=_now()
        )
    )


def pending_sync_items(conn: Connection) -> List[Row]:
    """Retrieves all the items as pending.

    Args:
        conn (Connection): The database connection.

    Returns:
        List[Row]: A list of pending sync items.
    """
    return (
        conn.execute(
            select(sync_queue)
            .where(sync_queue.c.sent_at.is_(None))
            .order_by(sync_queue.c.created_at, sync_queue.c.id)
        )
        .mappings()
        .all()
    )


def mark_synced(conn: Connection, item_id: int) -> None:
    """Marks items as synced.

    Args:
        conn (Connection): The database connection.
        item_id (int): The id of the item to mark as synced.
    """
    conn.execute(
        update(sync_queue).where(sync_queue.c.id == item_id).values(sent_at=_now())
    )
    conn.commit()