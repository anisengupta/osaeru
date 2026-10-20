"""Contains funcs to render the flowchart interface and handle user input."""

import math
from typing import List, Dict, Tuple
import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from osaeru.config import NODE_RADIUS
from osaeru import database as db
from osaeru import claude_sync


def _layout(
    nodes: List[dict], width: int, height: int
) -> Dict[str, Tuple[float, float]]:
    """Creates a circular layout for the nodes in the flowchart.

    Args:
        nodes (List[dict]): List of nodes, each represented as a dictionary with at least a "name" key.
        width (int): The width of the canvas area where the flowchart will be drawn.
        height (int): The height of the canvas area where the flowchart will be drawn.

    Returns:
        Dict[str, Tuple[float, float]]: A dictionary mapping each node's name to its (x, y)
        position on the canvas.
    """
    positions = {}
    n = max(len(nodes), 1)
    cx, cy = width / 2, height / 2
    r = min(width, height) / 2 - NODE_RADIUS - 10
    for i, node in enumerate(nodes):
        angle = 2 * math.pi * i / n
        positions[node["name"]] = (cx + r * math.cos(angle), cy + r * math.sin(angle))
    return positions


def build(conn) -> toga.Box:
    """Builds the flowchart interface.

    Args:
        conn (_type_): Database connection.

    Returns:
        toga.Box: The flowchart interface box containing
        the canvas, refresh button, sync button, and status label.
    """
    canvas = toga.Canvas(style=Pack(flex=1))
    status_label = toga.Label("")

    def redraw(widget=None):
        canvas.context.clear()
        graph = db.get_flowchart(conn)
        nodes = graph["nodes"]
        edges = graph["edges"]

        if not nodes:
            status_label.text = (
                "No skills recorded yet - add session notes to build this out."
            )
            return
        status_label.text = f"{len(nodes)} skills, {len(edges)} connections"

        width = canvas.layout.content_width or 600
        height = canvas.layout.content_height or 600
        positions = _layout(nodes, width, height)

        for edge in edges:
            if edge["from_name"] in positions and edge["to_name"] in positions:
                x1, y1 = positions[edge["from_name"]]
                x2, y2 = positions[edge["to_name"]]
                with canvas.context.Stroke(color="gray") as stroke:
                    stroke.move_to(x1, y1)
                    stroke.line_to(x2, y2)

        for node in nodes:
            x, y = positions[node["name"]]
            with canvas.context.Fill(color="#3B8BD4") as fill:
                fill.arc(x, y, NODE_RADIUS, 0, 2 * math.pi)
            canvas.context.write_text(node["name"], x - NODE_RADIUS, y, font=None)

    def sync_now(widget):
        """Syncs the local data with the remote server using Claude.
        Updates the status label with the result.

        Args:
            widget (_type_): The widget that triggered the event (sync button).
        """
        try:
            count = claude_sync.run_sync(conn)
            status_label.text = f"Synced {count} item(s)."
        except Exception as e:
            status_label.text = f"Sync failed: {e}"
        redraw()

    refresh_button = toga.Button("Refresh", on_press=redraw)
    sync_button = toga.Button("Sync now", on_press=sync_now)

    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(refresh_button)
    box.add(sync_button)
    box.add(status_label)
    box.add(canvas)

    redraw()

    return box
