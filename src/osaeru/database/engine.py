"""Funcs to set and create a database engine."""

from typing import Optional
import os
from sqlalchemy.engine import Engine
from sqlalchemy import create_engine

from osaeru.database.helper import backend, _sqlite_engine, _cloudsql_engine, _create_tables


def get_engine(db_path: Optional[Path] = None) -> Engine:
    """Retrieves the engine to be used, either SQLite or
    CloudSQL.

    Args:
        db_path (Optional[Path], optional): The database path.
        Defaults to None.

    Raises:
        RuntimeError: A RuntimeError if something goes wrong
        to retrieve the engine.

    Returns:
        Engine: An engine ready to be used for the app.
    """
    if db_path is not None:
        key = ("sqlite", str(Path(db_path).resolve()))
    else:
        kind = backend()
        if kind == "url":
            key = ("url", os.environ["OSAERU_DATABASE_URL"])
        elif kind == "cloudsql":
            key = (
                "cloudsql",
                os.environ["CLOUD_SQL_INSTANCE"],
                os.environ.get("DB_NAME", "osaeru"),
                os.environ.get("DB_USER"),
            )
        else:
            if os.environ.get("K_SERVICE"):
                raise RuntimeError(
                    "Running on Cloud Run without a database configured. Set "
                    "CLOUD_SQL_INSTANCE (or OSAERU_DATABASE_URL); a SQLite file "
                    "here would be wiped whenever the instance stops."
                )
            key = ("sqlite", str(default_db_path().resolve()))

    with _engines_lock:
        engine = _engines.get(key)
        if engine is None:
            if key[0] == "sqlite":
                engine = _sqlite_engine(Path(key[1]))
            elif key[0] == "url":
                engine = create_engine(key[1], pool_pre_ping=True)
            else:
                engine = _cloudsql_engine()
            _create_tables(engine)
            _engines[key] = engine
    return engine


def dispose_engines() -> None:
    """Disposes of a SQL engine."""
    with _engines_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def connect(db_path: Optional[Path] = None) -> Connection:
    """Connects a SQL engine.

    Args:
        db_path (Optional[Path], optional): The database path.
        Defaults to None.

    Returns:
        Connection: The database connection ready to be used.
    """
    return get_engine(db_path).connect()