"""Funcs to deal with skills in the db"""

from typing import Optional
from sqlalchemy.engine import Connection
from sqlalchemy import func, select

from osaeru.database.config import skills, skill_edges
from osaeru.database.helper import _insert, _now


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
