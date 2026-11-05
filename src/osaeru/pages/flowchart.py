"""Contains funcs to render the flowchart interface and handle user input."""

import logging
import math
from typing import List, Dict, Tuple, Optional
import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from osaeru import database as db
from osaeru import claude_sync

logger = logging.getLogger(__name__)


NODE_WIDTH = 170
NODE_HEIGHT = 60
LEVEL_GAP_Y = 110
NODE_GAP_X = 40
MARGIN = 40
MAX_PER_ROW = 6

HIERARCHY_RELATIONS = {"leads_to"}
REVERSED_RELATIONS = {"depends_on"}


CONFIDENCE_COLORS = {
    "learning": "#C1440E",
    "developing": "#C1440E",
    "low": "#C1440E",
    "comfortable": "#6C5CE7",
    "solid": "#6C5CE7",
    "reliable": "#1E8E6E",
}
DEFAULT_COLOR = "#3B8BD4"


def _hierarchy_edges(nodes: List[dict], edges: List[dict]) -> List[Tuple[str, str]]:
    """Gathers the edges that represent a hierarchy (parent-child) relationship between nodes.

    Args:
        nodes (List[dict]): A list of node dictionaries, each containing at least a "name" key.
        edges (List[dict]): A list of edge dictionaries, each containing "from_name", "to_name",
        and optionally "relation" keys.

    Returns:
        List[Tuple[str, str]]: A list of tuples representing the parent-child relationships
        between nodes.
    """
    names = {n["name"] for n in nodes}
    pairs = []
    for e in edges:
        a, b, rel = e["from_name"], e["to_name"], (e.get("relation") or "").lower()
        if a not in names or b not in names or a == b:
            continue
        if rel in REVERSED_RELATIONS:
            pairs.append((b, a))
        elif rel != "counters":
            pairs.append((a, b))
    return pairs


def _assign_levels(nodes: List[dict], edges: List[dict]) -> Dict[str, int]:
    """Assigns a level (depth) to each node based on the hierarchy defined by the edges.

    Args:
        nodes (List[dict]): A list of node dictionaries, each containing at least a "name" key.
        edges (List[dict]): A list of edge dictionaries, each containing "from_name", "to_name"
        and optionally "relation" keys.

    Returns:
        Dict[str, int]: A dictionary mapping each node's name to its assigned level (depth)
        in the hierarchy.
    """
    parents = {n["name"]: [] for n in nodes}
    for p, c in _hierarchy_edges(nodes, edges):
        parents[c].append(p)

    levels: Dict[str, int] = {}
    visiting: set = set()

    def level_of(name: str) -> int:
        """Gets the level of a node by recursively checking its parents.

        Args:
            name (str): The name of the node for which to determine the level.

        Returns:
            int: The level (depth) of the node in the hierarchy,
            where 0 is the top level.
        """
        if name in levels:
            return levels[name]
        visiting.add(name)
        best = 0
        for p in parents[name]:
            if p in visiting:
                continue
            best = max(best, level_of(p) + 1)
        visiting.discard(name)
        levels[name] = best
        return best

    for n in nodes:
        level_of(n["name"])
    return levels


def _layout(
    nodes: List[dict], edges: List[dict]
) -> Tuple[Dict[str, Tuple[float, float]], float, float]:
    """Creates a layout for the flowchart nodes and edges, determining their
    positions on a canvas.

    Args:
        nodes (List[dict]): A list of node dictionaries, each containing at least a "name" key.
        edges (List[dict]): A list of edge dictionaries, each containing "from_name", "to_name"
        and optionally "relation" keys.

    Returns:
        Tuple[Dict[str, Tuple[float, float]], float, float]: A tuple containing the layout.
    """
    levels = _assign_levels(nodes, edges)
    parents = {n["name"]: [] for n in nodes}
    for p, c in _hierarchy_edges(nodes, edges):
        parents[c].append(p)

    by_level = {}
    for n in nodes:
        by_level.setdefault(levels[n["name"]], []).append(n["name"])

    order_index = {}
    rows: List[List[str]] = []
    for level in sorted(by_level):
        names = by_level[level]

        def key(name: str) -> Tuple[float, str]:
            px = [order_index[p] for p in parents[name] if p in order_index]
            return (sum(px) / len(px) if px else float("inf"), name)

        names.sort(key=key)
        for i in range(0, len(names), MAX_PER_ROW):
            chunk = names[i : i + MAX_PER_ROW]
            rows.append(chunk)
            for j, name in enumerate(chunk):
                order_index[name] = (j + 0.5) / len(chunk)

    widest = max(len(r) for r in rows)
    content_width = widest * NODE_WIDTH + (widest - 1) * NODE_GAP_X
    canvas_width = content_width + 2 * MARGIN
    canvas_height = 2 * MARGIN + (len(rows) - 1) * LEVEL_GAP_Y + NODE_HEIGHT

    positions = {}
    for r, row in enumerate(rows):
        row_width = len(row) * NODE_WIDTH + (len(row) - 1) * NODE_GAP_X
        start_x = MARGIN + (content_width - row_width) / 2
        y = MARGIN + r * LEVEL_GAP_Y
        for i, name in enumerate(row):
            positions[name] = (start_x + i * (NODE_WIDTH + NODE_GAP_X), y)

    return positions, canvas_width, canvas_height


def _wrap_text(canvas: toga.Canvas, text: str, max_width: float) -> List[str]:
    """Wraps the given text into multiple lines so that each line fits within
    the specified maximum width.

    Args:
        canvas (toga.Canvas): The canvas used to measure text width.
        text (str): The text to be wrapped into multiple lines.
        max_width (float): The maximum width allowed for each line of text.

    Returns:
        List[str]: A list of strings, where each string is a line of wrapped text.
    """
    words = text.split()
    lines = []
    current = ""

    for word in words:
        trial = f"{current} {word}".strip()
        try:
            width, _ = canvas.measure_text(trial, None)
        except AttributeError:
            width = len(trial) * 7
        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines[:3]


def build(conn) -> toga.Box:
    """Builds the flowchart interface.

    Args:
        conn (_type_): Database connection.

    Returns:
        toga.Box: The flowchart interface box containing the canvas for rendering
        the flowchart and status labels.
    """
    canvas = toga.Canvas(style=Pack(flex=1, height=600))
    status_label = toga.Label("")
    sync_status_label = toga.Label("")
    detail_label = toga.Label("Tap a node to see details.", style=Pack(padding_top=8))

    state = {"positions": {}, "nodes_by_name": {}}

    def redraw(widget=None):
        canvas.context.clear()
        graph = db.get_flowchart(conn)
        nodes = graph["nodes"]
        edges = graph["edges"]
        state["nodes_by_name"] = {n["name"]: n for n in nodes}

        if not nodes:
            status_label.text = (
                "No skills recorded yet - add session notes to build this out"
            )
            state["positions"] = {}
            return
        status_label.text = f"{len(nodes)} skills, {len(edges)} connections"

        positions, req_w, req_h = _layout(nodes, edges)
        state["positions"] = positions

        canvas.style.update(width=max(900, req_w), height=max(600, req_h))

        for edge in edges:
            a, b = edge["from_name"], edge["to_name"]
            if a not in positions or b not in positions or a == b:
                continue
            x1, y1 = positions[a]
            x2, y2 = positions[b]
            is_counter = (edge.get("relation") or "").lower() == "counters"

            # Connect bottom-centre of the upper node to top-centre of the lower one.
            if y1 > y2:
                (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
            sx, sy = x1 + NODE_WIDTH / 2, y1 + NODE_HEIGHT
            ex, ey = x2 + NODE_WIDTH / 2, y2
            if sy > ey:  # same row: link side to side instead
                sy = ey = y1 + NODE_HEIGHT / 2
                sx, ex = (x1 + NODE_WIDTH, x2) if x1 < x2 else (x1, x2 + NODE_WIDTH)
            mid = (sy + ey) / 2

            with canvas.context.Stroke(
                color="#E07A5F" if is_counter else "#777777",
                line_width=1.5,
                line_dash=[6, 4] if is_counter else None,
            ) as stroke:
                stroke.move_to(sx, sy)
                stroke.bezier_curve_to(sx, mid, ex, mid, ex, ey)

        for node in nodes:
            x, y = positions[node["name"]]
            color = CONFIDENCE_COLORS.get(
                (node.get("confidence") or "").lower(), DEFAULT_COLOR
            )

            with canvas.context.Fill(color=color) as fill:
                fill.rect(x, y, NODE_WIDTH, NODE_HEIGHT)

            with canvas.context.Fill(color="white") as text_fill:
                lines = _wrap_text(canvas, node["name"], NODE_WIDTH - 12)
                line_height = 16
                start_y = y + NODE_HEIGHT / 2 - (len(lines) * line_height) / 2
                for i, line in enumerate(lines):
                    text_fill.write_text(
                        line, x + 6, start_y + i * line_height, font=None
                    )

    def on_tap(widget, x, y):
        """Func to handle tap events on the canvas. It checks if a node was tapped
        and updates the detail label accordingly.

        Args:
            widget (_type_): The widget that triggered the event (canvas).
            x (_type_): The x-coordinate of the tap event.
            y (_type_): The y-coordinate of the tap event.
        """
        for name, (nx, ny) in state["positions"].items():
            if nx <= x <= nx + NODE_WIDTH and ny <= y <= ny + NODE_HEIGHT:
                node = state["nodes_by_name"][name]
                detail_label.text = (
                    f"{node['name']}  —  category: {node.get('category') or '-'}, "
                    f"confidence: {node.get('confidence') or '-'}"
                )
                return
        detail_label.text = "Tap a node to see details."

    canvas.on_press = on_tap

    def sync_now(widget):
        """Syncs the pending items in the sync queue with Claude and updates
        the sync status label.

        Args:
            widget (_type_): The widget that triggered the event (sync button).
        """
        logger.info("Sync now button pressed")
        try:
            count = claude_sync.run_sync(conn)
            sync_status_label.text = f"Synced {count} item(s)."
        except Exception as e:
            logger.error("sync_now failed: %s", e)
            sync_status_label.text = f"Sync failed: {e}"
        redraw()

    refresh_button = toga.Button("Refresh", on_press=redraw)
    sync_button = toga.Button("Sync now", on_press=sync_now)

    legend = toga.Label(
        "🟧 still learning    🟪 comfortable    🟩 reliable    🟦 unrated    ┄ counters",
        style=Pack(padding_top=8),
    )

    scroll = toga.ScrollContainer(
        content=canvas, horizontal=True, vertical=True, style=Pack(flex=1)
    )

    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(refresh_button)
    box.add(sync_button)
    box.add(sync_status_label)
    box.add(status_label)
    box.add(legend)
    box.add(scroll)
    box.add(detail_label)

    redraw()

    return box
