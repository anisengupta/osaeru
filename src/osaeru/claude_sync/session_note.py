"""Funcs to analyse session notes."""

from typing import Dict, Any
import logging

from osaeru.config import MODEL, FLOWCHART_SYSTEM_PROMPT
from osaeru.database.helper import _client, _extract_json

logger = logging.getLogger(__name__)


def analyse_session_note(
    notes: str, existing_flowchart: Dict[str, Any]
) -> Dict[str, Any]:
    """Analyses a session note and existing flowchart to extract
    new skills, edges, and recommendations.

    Args:
        notes (str): The session note text to be analysed.
        existing_flowchart (Dict[str, Any]): The current flowchart
        represented as a dictionary.

    Returns:
        Dict[str, Any]: A dictionary containing new skills, edges, and recommendations.
    """
    logger.debug(
        f"analyse_session_note called. Note length={len(notes)}, flowchart_nodes={len(existing_flowchart.get("nodes", []))}"
    )
    client = _client()
    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=FLOWCHART_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Existing flowchart:\n{json.dumps(existing_flowchart)}\n\n"
                    f"New session note:\n{notes}"
                ),
            }
        ],
    )
    logger.debug(f"Raw API response object: {message}")

    if message.stop_reason == "max_tokens":
        logger.error(
            "Response was truncated at max_tokens - note may be too long/detailed "
            "for the current limit, or flowchart has grown large enough to eat into budget."
        )
        raise RuntimeError(
            "Claude's response was cut off (hit max_tokens) before finishing the JSON. "
            "Try a shorter note, or raise max_tokens further in claude_sync.py."
        )

    text = "".join(block.text for block in message.content if block.type == "text")
    logger.info(f"Raw text from Claude (first 500 chars): {text[:500]}")

    parsed = _extract_json(text)

    logger.info(
        f"""Parsed result: new_skills={len(parsed.get("new_skills", []))}, 
        new_edges={len(parsed.get("new_edges", []))}, 
        recommendations={len(parsed.get("recommendations", []))}
        """
    )

    return parsed