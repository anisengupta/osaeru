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
    return CONFIDENCE_ALIASES.get(str(value or "").strip().lower(), "unrated")


def flowchart_payload(
    graph: Dict[str, List[Dict[str, Any]]], recommendations: Iterable[Any]
) -> Dict[str, Any]:
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
