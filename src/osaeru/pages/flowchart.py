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
NODE_GAP_X = 20


CONFIDENCE_COLORS = {
    "learning": "#C1440E",
    "developing": "#C1440E",
    "low": "#C1440E",
    "comfortable": "#6C5CE7",
    "solid": "#6C5CE7",
    "reliable": "#1E8E6E",
}
DEFAULT_COLOR = "#3B8BD4"


def _assign_levels(nodes: List[dict], edges: List[dict]) -> Dict[str, int]:
    name_to_node = {n["name"]: n for n in nodes}
    incoming = {n["name"]: [] for n in nodes}
    
    for e in edges:
        if e["to_name"] in incoming and e["from_name"] in name_to_node:
            incoming[e["to_name"]].append(e["from_name"])
    
    levels = {}
    
    def level_of(name: str, seen: Optional[set] = None) -> int:
        if name in levels:
            return levels[name]
        seen = seen or set()
        if name in seen:
            return 0
        seen = seen | {name}
        parents = incoming.get(name, [])
        levels[name] = 0 if not parents else 1 + max(level_of(p, seen) for p in parents)
        return levels[name]
    
    for n in nodes:
        level_of(n["name"])
    
    return levels


def _layout(nodes: List[dict], edges: List[dict], width: int) -> Dict[str, Tuple[float, float]]:
    levels = _assign_levels(nodes, edges)
    rows = {}
    
    for n in nodes:
        rows.setdefault(levels[n["name"]], []).append(n)
    
    positions = {}
    for level, row_nodes in sorted(rows.items()):
        count = len(row_nodes)
        row_width = count * NODE_WIDTH + (count - 1) * NODE_GAP_X
        start_x = max((width - row_width) / 2, NODE_GAP_X)
        y = 40 + level + LEVEL_GAP_Y
        
        for i, node in enumerate(row_nodes):
            x = start_x + i * (NODE_WIDTH + NODE_GAP_X)
            positions[node["name"]] = (x, y)
    
    return positions


def _wrap_text(canvas: toga.Canvas, text: str, max_width: float) -> List[str]:
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
            status_label.text = "No skills recorded yet - add session notes to build this out"
            state["positions"] = {}
            return
        status_label.text = f"{len(nodes)} skills, {len(edges)} connections"

        width = canvas.layout.content_width or 900
        positions = _layout(nodes, edges, width)
        state["positions"] = positions

        for edge in edges:
            if edge["from_name"] in positions and edge["to_name"] in positions:
                x1, y1 = positions[edge["from_name"]]
                x2, y2 = positions[edge["to_name"]]
                with canvas.context.Stroke(color="#888888") as stroke:
                    stroke.move_to(x1 + NODE_WIDTH / 2, y1 + NODE_HEIGHT)
                    stroke.line_to(x2 + NODE_WIDTH / 2, y2)

        for node in nodes:
            x, y = positions[node["name"]]
            color = CONFIDENCE_COLORS.get((node.get("confidence") or "").lower(), DEFAULT_COLOR)

            with canvas.context.Fill(color=color) as fill:
                fill.rect(x, y, NODE_WIDTH, NODE_HEIGHT)

            with canvas.context.Fill(color="white") as text_fill:
                lines = _wrap_text(canvas, node["name"], NODE_WIDTH - 12)
                line_height = 16
                start_y = y + NODE_HEIGHT / 2 - (len(lines) * line_height) / 2
                for i, line in enumerate(lines):
                    text_fill.write_text(line, x + 6, start_y + i * line_height, font=None)

        levels = _assign_levels(nodes, edges)
        max_level = max(levels.values(), default=0)

        row_counts: Dict[int, int] = {}
        for n in nodes:
            row_counts[levels[n["name"]]] = row_counts.get(levels[n["name"]], 0) + 1
        widest_row_count = max(row_counts.values(), default=1)
        required_width = widest_row_count * (NODE_WIDTH + NODE_GAP_X)

        canvas.style.update(
            width=max(900, required_width),
            height=max(600, 80 + (max_level + 1) * LEVEL_GAP_Y),
        )
    
    def on_tap(widget, x, y):
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
    
    legend = toga.Label("🟧 still learning    🟪 comfortable    🟩 reliable", style=Pack(padding_top=8))
    
    scroll = toga.ScrollContainer(content=canvas, horizontal=True, vertical=True, style=Pack(flex=1))
    
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
