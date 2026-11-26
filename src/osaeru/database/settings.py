"""Funcs to get, set and delete settings in the db"""

from typing import Optional
from sqlalchemy.engine import Connection
from sqlalchemy import select

from osaeru.database.helper import _insert, _now
from osaeru.database.config import app_settings


def get_setting(conn: Connection, key: str) -> Optional[str]:
    """Returns the settings value from the db.

    Args:
        conn (Connection): The database connection.
        key (str): The name of the key to retrieve
        from the db.

    Returns:
        Optional[str]: The key's value.
    """
    return conn.execute(
        select(app_settings.c.value).where(app_settings.c.key == key)
    ).scalar_one_or_none()


def set_setting(conn: Connection, key: str, value: str) -> None:
    """Stores a setting, replacing any existing value.

    Args:
        conn (Connection): The database connection.
        key (str): The key name.
        value (str): The value to store.
    """
    values = dict(value=value, updated_at=_now())
    stmt = _insert(conn, app_settings).values(key=key, **values)
    conn.execute(stmt.on_conflict_do_update(index_elements=["key"], set_=values))
    conn.commit()


def delete_setting(conn: Connection, key: str) -> None:
    """Deletes a setting.

    Args:
        conn (Connection): The database connection.
        key (str): The name of the key to delete.
    """
    conn.execute(app_settings.delete().where(app_settings.c.key == key))
    conn.commit()