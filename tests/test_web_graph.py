"""Unit tests for osaeru.web.graph (the flowchart data shaping)."""

import pytest

from osaeru.web.graph import flowchart_payload, normalise_confidence


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("learning", "learning"),
        ("Still Learning", "learning"),
        ("  beginner ", "learning"),
        ("intermediate", "comfortable"),
        ("Developing", "comfortable"),
        ("advanced", "reliable"),
        ("PROFICIENT", "reliable"),
        ("expert", "unrated"),
        ("", "unrated"),
        (None, "unrated"),
    ],
)
def test_normalise_confidence(raw, expected):
    assert normalise_confidence(raw) == expected


def graph(nodes, edges=()):
    return {
        "nodes": [{"name": n, "category": None, "confidence": None} for n in nodes],
        "edges": [{"from_name": a, "to_name": b, "relation": r} for a, b, r in edges],
    }


def edge_pairs(payload):
    return [(e["data"]["source"], e["data"]["target"], e["data"]["relation"]) for e in payload["edges"]]


def test_empty():
    assert flowchart_payload({"nodes": [], "edges": []}, []) == {"nodes": [], "edges": []}


def test_node_shape():
    payload = flowchart_payload(
        {"nodes": [{"name": "Armbar", "category": "submission", "confidence": "Beginner"}], "edges": []},
        [],
    )
    assert payload["nodes"] == [
        {
            "data": {
                "id": "Armbar",
                "label": "Armbar",
                "category": "submission",
                "confidence": "learning",
                "recommendations": [],
            }
        }
    ]


def test_missing_category_becomes_empty_string():
    payload = flowchart_payload(graph(["A"]), [])
    assert payload["nodes"][0]["data"]["category"] == ""


def test_leads_to_kept_as_is():
    assert edge_pairs(flowchart_payload(graph(["A", "B"], [("A", "B", "leads_to")]), [])) == [("A", "B", "leads_to")]


def test_depends_on_is_reversed():
    payload = flowchart_payload(graph(["Armbar", "Mount"], [("Armbar", "Mount", "depends_on")]), [])
    assert edge_pairs(payload) == [("Mount", "Armbar", "depends_on")]


def test_counters_kept_and_labelled():
    payload = flowchart_payload(graph(["A", "B"], [("A", "B", "counters")]), [])
    assert edge_pairs(payload) == [("A", "B", "counters")]


def test_relation_normalised_and_defaulted():
    g = graph(["A", "B", "C"], [("A", "B", "  Leads_To "), ("B", "C", None)])
    assert edge_pairs(flowchart_payload(g, [])) == [("A", "B", "leads_to"), ("B", "C", "leads_to")]


def test_self_loops_dropped():
    assert flowchart_payload(graph(["A"], [("A", "A", "leads_to")]), [])["edges"] == []


def test_edges_to_unknown_nodes_dropped():
    assert flowchart_payload(graph(["A"], [("A", "Ghost", "leads_to")]), [])["edges"] == []


def test_duplicates_after_reversal_dropped():
    # "B depends_on A" reverses to A -> B, the same pair as a second listing.
    g = graph(["A", "B"], [("B", "A", "depends_on"), ("B", "A", "depends_on")])
    assert len(flowchart_payload(g, [])["edges"]) == 1


def test_edge_ids_are_unique():
    g = graph(["A", "B"], [("A", "B", "leads_to"), ("A", "B", "counters")])
    ids = [e["data"]["id"] for e in flowchart_payload(g, [])["edges"]]
    assert len(ids) == len(set(ids)) == 2


def test_recommendations_attached_to_their_skill():
    recs = [
        {"skill_name": "A", "text": "first"},
        {"skill_name": "A", "text": "second"},
        {"skill_name": None, "text": "unlinked"},
        {"skill_name": "Ghost", "text": "no such node"},
    ]
    nodes = {n["data"]["id"]: n["data"] for n in flowchart_payload(graph(["A", "B"]), recs)["nodes"]}
    assert nodes["A"]["recommendations"] == ["first", "second"]
    assert nodes["B"]["recommendations"] == []
