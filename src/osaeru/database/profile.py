"""Funcs to save and get a profile"""

from typing import Optional
from sqlalchemy.engine import Connection
from sqlalchemy import select

from osaeru.database.config import Row, profile
from osaeru.database.helper import _insert, _now


def save_profile(
    conn: Connection,
    *,
    age: int,
    sex: str,
    weight_kg: float,
    sessions_per_week: float,
) -> None:
    """Saves the profile information.

    Args:
        conn (Connection): The database connection.
        age (int): The age of the user.
        sex (str): The sex of the user.
        weight_kg (float): The weight of the user.
        sessions_per_week (float): The number of sessions of BJJ
        done by the user.
    """
    values = dict(
        age=age,
        sex=sex,
        weight_kg=weight_kg,
        sessions_per_week=sessions_per_week,
        updated_at=_now(),
    )
    stmt = _insert(conn, profile).values(id=1, **values)
    conn.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))
    conn.commit()


def get_profile(conn: Connection) -> Optional[Row]:
    """Retrieves the profile of the signed in user.

    Args:
        conn (Connection): The database connection.

    Returns:
        Optional[Row]: A row with the user's profile.
    """
    return conn.execute(select(profile).where(profile.c.id == 1)).mappings().first()