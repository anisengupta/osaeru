"""Database helper funcs"""

import os
from pathlib import Path
from sqlalchemy.engine import Engine


def backend() -> str:
    """Sets up the backend SQL instance to use.

    Returns:
        str: The backend SQL instance.
    """
    if os.environ.get("OSAERU_DATABASE_URL"):
        return "url"
    if os.environ.get("CLOUD_SQL_INSTANCE"):
        return "cloudsql"
    return "sqlite"


_engines = {}
_engines_lock = threading.Lock()
_connector = None


def _sqlite_engine(path: Path) -> Engine:
    """Sets up the SLQLite engine to work locally.

    Args:
        path (Path): The SQLite database path.

    Returns:
        Engine: The local SQLite engine to be used.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    return engine


def _cloudsql_engine() -> Engine:
    """Sets up the CloudSQL engine to be used
    for the app in CloudRun.

    Raises:
        RuntimeError: A RunTimeError in the event
        that there is an error setting up the engine.

    Returns:
        Engine: The CloudSQL engine, ready to be used.
    """
    global _connector
    from google.cloud.sql.connector import Connector, IPTypes

    instance = os.environ["CLOUD_SQL_INSTANCE"]
    name = os.environ.get("DB_NAME", "osaeru")
    user = os.environ.get("DB_USER")

    if not user:
        raise RuntimeError("DB_USER must be set when CLOUD_SQL_INSTANCE is set.")
    password = os.environ.get("DB_PASS")
    ip_type = (
        IPTypes.PRIVATE
        if os.environ.get("CLOUD_SQL_IP_TYPE", "public").lower() == "private"
        else IPTypes.PUBLIC
    )

    if _connector is None:
        _connector = Connector(refresh_strategy="lazy")
        atexit.register(_connector.close)

    def getconn():
        kwargs = dict(user=user, db=name, ip_type=ip_type)
        if password:
            kwargs["password"] = password
        else:
            kwargs["enable_iam_auth"] = True
        return _connector.connect(instance, "pg8000", **kwargs)

    return create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def _create_tables(engine: Engine) -> None:
    """Create the relevant tables.

    Args:
        engine (Engine): The SQL engine.
    """
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(7302215)"))
        metadata.create_all(conn)


def _now() -> str:
    """Creates a timestamp string in ISO 8601 format for the current UTC time.

    Returns:
        str: The current UTC time as an ISO 8601 formatted string.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _insert(conn: Connection, table: Table):
    """Inserts a table into the SQL database.

    Args:
        conn (Connection): The database connection.
        table (Table): The table to be inserted.

    Returns:
        _type_: The insertion of a table in the relevant
        SQL database.
    """
    dialect = postgresql if conn.dialect.name == "postgresql" else sqlite
    return dialect.insert(table)