"""Funcs to set and retrieve recommendations from the db"""

from typing import Optional, List
from sqlalchemy.engine import Connection
from sqlalchemy import select, update

from osaeru.database.config import Row, recommendations


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