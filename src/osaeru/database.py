"Local storage for app, when working in Cloud Run, the storage is persistant, when working locally, SQLite is used."

from __future__ import annotations
import atexit
import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.pool import NullPool

Row = RowMapping

metadata = MetaData()

profile = Table(
    "profile",
    metadata,
    Column(
        "id", Integer, primary_key=True, autoincrement=False
    ),  # singleton row, id = 1
    Column("age", Integer),
    Column("sex", Text),
    Column("weight_kg", Float),
    Column("sessions_per_week", Float),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("id = 1", name="profile_singleton"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("occurred_at", Text, nullable=False),
    Column("notes", Text, nullable=False),
    Column("feeling", Text),  # freeform: "good", "tired", "frustrated" etc
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

skills = Table(
    "skills",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("category", Text),  # e.g. "guard", "submission", "escape"
    Column("confidence", Text),  # e.g. "learning", "comfortable", "reliable"
    Column("first_seen_session_id", Integer, ForeignKey("sessions.id")),
    Column("updated_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

skill_edges = Table(
    "skill_edges",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("from_skill_id", Integer, ForeignKey("skills.id"), nullable=False),
    Column("to_skill_id", Integer, ForeignKey("skills.id"), nullable=False),
    Column(
        "relation", Text, nullable=False
    ),  # e.g. "leads_to", "counters", "depends_on"
    UniqueConstraint("from_skill_id", "to_skill_id", "relation"),
    sqlite_autoincrement=True,
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("role", Text, nullable=False),  # "user" or "assistant"
    Column("content", Text, nullable=False),
    Column("session_id", Integer, ForeignKey("sessions.id")),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

recommendations = Table(
    "recommendations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("text", Text, nullable=False),
    Column("related_skill_id", Integer, ForeignKey("skills.id")),
    Column(
        "status", String, nullable=False, server_default="new"
    ),  # new | tried | skipped
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

sync_queue = Table(
    "sync_queue",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Text, nullable=False),  # "session_note" | "chat_message"
    Column("payload", Text, nullable=False),  # JSON blob
    Column("created_at", Text, nullable=False),
    Column("sent_at", Text),
    sqlite_autoincrement=True,
)


def default_db_path() -> Path:
    """The default path for the SQLite database file, located
    in the user's home directory under `.osaeru/osaeru.sqlite3`.

    Returns:
        Path: The default path for the SQLite database file.
    """
    env_path = os.environ.get("OSAERU_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".osaeru" / "osaeru.sqlite3"


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
    """Disposes of a SQL engine.
    """
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


def upsert_skill(
    conn: Connection,
    name: str,
    category: Optional[str] = None,
    confidence: Optional[str] = None,
    session_id: Optional[int] = None,
) -> int:
    """Upserts a skill based on the user notes.

    Args:
        conn (Connection): The database connection.
        name (str): The name of the skill to upsert.
        category (Optional[str], optional): The category 
        of the skill to upsert. Defaults to None.
        confidence (Optional[str], optional): The confidence 
        value of the skill to upsert. Defaults to None.
        session_id (Optional[int], optional): The session 
        id related to the skill. Defaults to None.

    Returns:
        int: _description_
    """
    stmt = _insert(conn, skills).values(
        name=name,
        category=category,
        confidence=confidence,
        first_seen_session_id=session_id,
        updated_at=_now(),
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "category": func.coalesce(stmt.excluded.category, skills.c.category),
                "confidence": func.coalesce(
                    stmt.excluded.confidence, skills.c.confidence
                ),
                "updated_at": stmt.excluded.updated_at,
            },
        )
    )
    conn.commit()

    return conn.execute(select(skills.c.id).where(skills.c.name == name)).scalar_one()


def add_skill_edge(
    conn: Connection, from_name: str, to_name: str, relation: str
) -> None:
    """Adds a skill edge by retrieving the ids of the from and to
    skill names.

    Args:
        conn (Connection): The database connection.
        from_name (str): The from name skill
        to_name (str): The to name skill.
        relation (str): The skill relation.
    """
    from_id = upsert_skill(conn, from_name)
    to_id = upsert_skill(conn, to_name)
    stmt = _insert(conn, skill_edges).values(
        from_skill_id=from_id,
        to_skill_id=to_id,
        relation=relation,
    )
    conn.execute(
        stmt.on_conflict_do_nothing(
            index_elements=["from_skill_id", "to_skill_id", "relation"]
        )
    )
    conn.commit()


def get_flowchart(conn: Connection) -> Dict[str, Any]:
    """Retrieves the flowchart from the db to show in the UI.

    Args:
        conn (Connection): The database connection.

    Returns:
        Dict[str, Any]: A dict of nodes and edges which can be
        rendered in the UI.
    """
    nodes = conn.execute(select(skills).order_by(skills.c.id)).mappings().all()
    s1, s2 = skills.alias("s1"), skills.alias("s2")
    edges = (
        conn.execute(
            select(
                s1.c.name.label("from_name"),
                s2.c.name.label("to_name"),
                skill_edges.c.relation,
            )
            .select_from(skill_edges)
            .join(s1, s1.c.id == skill_edges.c.from_skill_id)
            .join(s2, s2.c.id == skill_edges.c.to_skill_id)
            .order_by(skill_edges.c.id)
        )
        .mappings()
        .all()
    )

    return {
        "nodes": [dict(node) for node in nodes],
        "edges": [dict(edge) for edge in edges],
    }


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


def add_recommendation(
    conn: Connection,
    text: str,
    related_skill_id: Optional[int] = None,
) -> int:
    """Adds a recommendation from Claude to try next session.

    Args:
        conn (Connection): The database connection.
        text (str): The text of the recommendation.
        related_skill_id (Optional[int], optional): The skill 
        id related to the recommendation. Defaults to None.

    Returns:
        int: The id .mmendation inserted into the db
    """
    result = conn.execute(
        recommendations.insert().values(
            text=text, related_skill_id=related_skill_id, created_at=_now()
        )
    )
    conn.commit()

    return result.inserted_primary_key[0]


def list_recommendations(conn: Connection, status: Optional[str] = None) -> List[Row]:
    """Lists all the recommendation from Claude.

    Args:
        conn (Connection): The database connection.
        status (Optional[str], optional): The status of those recommendations. 
        Defaults to None.

    Returns:
        List[Row]: A list of recommendations to try.
    """
    query = select(recommendations).order_by(
        recommendations.c.created_at.desc(), recommendations.c.id.desc()
    )
    if status:
        query = query.where(recommendations.c.status == status)

    return conn.execute(query).mappings().all()


def set_recommendation_status(conn: Connection, rec_id: int, status: str) -> None:
    """Sets the status of the recommendations suggested.

    Args:
        conn (Connection): The database connection.
        rec_id (int): The recommendation id.
        status (str): The status of the recommendation.
    """
    conn.execute(
        update(recommendations)
        .where(recommendations.c.id == rec_id)
        .values(status=status)
    )
    conn.commit()
