"""Database config"""

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
)


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
    Column("feeling", Text),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

skills = Table(
    "skills",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("category", Text),
    Column("confidence", Text),
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
    Column("relation", Text, nullable=False),
    UniqueConstraint("from_skill_id", "to_skill_id", "relation"),
    sqlite_autoincrement=True,
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("role", Text, nullable=False),
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
    Column("status", String, nullable=False, server_default="new"),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

app_settings = Table(
    "app_settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

sync_queue = Table(
    "sync_queue",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("sent_at", Text),
    sqlite_autoincrement=True,
)