"""Shapes the skills graph for the browser-side flowchart.

Layout itself happens in the browser (Cytoscape + dagre), so this module only
cleans the data: it normalises confidence labels and orients edges so that
"down the chart" always means "later in your progression".
"""

from __future__ import annotations
from typing import Any, Dict, Iterable, List


CONFIDENCE_ALIASES = {
    "learning": "learning",
    "still learning": "learning",
    "beginner": "learning",
    "new": "learning",
    "introduced": "learning",
    "developing": "comfortable",
    "comfortable": "comfortable",
    "intermediate": "comfortable",
    "competent": "comfortable",
    "reliable": "reliable",
    "proficient": "reliable",
    "advanced": "reliable",
    "strong": "reliable",
}


def normalise_confidence(value: Any) -> str:
    """
    Normalize a given confidence value to a standardized string representation.
    This function takes an input value, converts it to a lowercase string after
    stripping any leading or trailing whitespace, and maps it to a predefined
    alias using the CONFIDENCE_ALIASES dictionary. If the value does not match
    any alias, it defaults to returning "unrated".
    Args:
        value (Any): The input value to normalize. It can be of any type.
    Returns:
        str: The normalized confidence value as a string. Defaults to "unrated"
             if the input value does not match any predefined alias.
    """
    return CONFIDENCE_ALIASES.get(str(value or "").strip().lower(), "unrated")


def flowchart_payload(
    graph: Dict[str, List[Dict[str, Any]]], recommendations: Iterable[Any]
) -> Dict[str, Any]:
    """
    Generate a payload for a flowchart visualization based on a graph structure 
    and a list of recommendations.
    Args:
        graph (Dict[str, List[Dict[str, Any]]]): A dictionary representing the graph 
            structure with "nodes" and "edges". Each node is a dictionary containing 
            attributes like "name", "category", and "confidence". Each edge is a 
            dictionary with "from_name", "to_name", and optionally "relation".
        recommendations (Iterable[Any]): A collection of recommendation objects, 
            where each recommendation is expected to have "skill_name" and "text" fields.
    Returns:
        Dict[str, Any]: A dictionary containing two keys:
            - "nodes": A list of dictionaries representing the nodes in the flowchart. 
              Each node includes its ID, label, category, confidence, and associated 
              recommendations.
            - "edges": A list of dictionaries representing the edges in the flowchart. 
              Each edge includes its ID, source, target, and relation.
    Notes:
        - Nodes without a valid "name" or edges with invalid or cyclic connections 
          are ignored.
        - The "relation" field in edges defaults to "leads_to" if not provided.
        - If the relation is "depends_on", the direction of the edge is reversed.
    """
    recs_by_skill: Dict[str, List[str]] = {}
    for rec in recommendations:
        if rec["skill_name"]:
            recs_by_skill.setdefault(rec["skill_name"], []).append(rec["text"])

    names = {n["name"] for n in graph["nodes"]}
    nodes = [
        {
            "data": {
                "id": n["name"],
                "label": n["name"],
                "category": n.get("category") or "",
                "confidence": normalise_confidence(n.get("confidence")),
                "recommendations": recs_by_skill.get(n["name"], []),
            }
        }
        for n in graph["nodes"]
    ]

    edges = []
    seen = set()
    for e in graph["edges"]:
        src, dst = e["from_name"], e["to_name"]
        relation = (e.get("relation") or "leads_to").strip().lower()
        if src not in names or dst not in names or src == dst:
            continue
        if relation == "depends_on":
            src, dst = dst, src
        key = (src, dst, relation)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "data": {
                    "id": f"{src}->{dst}:{relation}",
                    "source": src,
                    "target": dst,
                    "relation": relation,
                }
            }
        )

    return {"nodes": nodes, "edges": edges}
