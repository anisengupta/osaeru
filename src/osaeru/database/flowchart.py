"""Funcs to retrieve flowchart information from the db"""

from typing import Dict, Any
from sqlalchemy.engine import Connection


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