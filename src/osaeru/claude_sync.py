"""
Module containing funcs to sync data with Claude API.

Only Module that requires an internet connetion to perform the following:

1. Store/retrieve the Anthropic API key from OS keychain
2. Drai the sync_queue: for each queued session note or chat message,
ask Claude to
    (a) Extract skills/edges to merge into the flowchart,
    (b) Suggest recommendations
3. Handle ordinary chat replies.
"""

from __future__ import annotations
import json
from typing import Any, Optional, Dict, List
import keyring
from anthropic import Anthropic

from osaeru.config import KEYRING_KEY, KEYRING_SERVICE, MODEL, FLOWCHART_SYSTEM_PROMPT
from osaeru import database as db


def get_api_key() -> Optional[str]:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)


def set_api_key(api_key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, api_key)


def _client() -> Anthropic:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key stored. Prompt the user to enter one in "
            "Settings before calling anything in this module."
        )
    
    return Anthropic(api_key=api_key)


def analyse_session_note(notes: str, existing_flowchart: Dict[str, Any]) -> Dict[str, Any]:
    client = _client()
    message = client.messages.create(
        model=MODEL,
        max_tokens=1000,
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
    text = "".join(block.text for block in message.content if block.type == "text")
    
    return json.loads(text)


def chat_reply(
    history: List[Dict[str, str]],
    flowchart: Dict[str, Any]
) -> str:
    client = _client()
    system = (
        "You are a supportive, knowledgeable jiu jitsu coach chatting with "
        "your student about their training. Be specific and practical. "
        f"Here is what you currently know about their skills: {json.dumps(flowchart)}"
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=system,
        messages=history,
    )
    
    return "".join(block.text for block in message.content if block.type == "text")


def run_sync(conn) -> int:
    processed = 0
    flowchart = db.get_flowchart(conn)
    
    for item in db.pending_sync_items(conn):
        payload = json.loads(item["payload"])
        if item["kind"] == "session_note":
            result = analyse_session_note(payload["notes"], flowchart)
            
            for skill in result.get("new_skills", []):
                db.upsert_skill(
                    conn,
                    skill["name"],
                    skill.get("category"),
                    skill.get("confidence"),
                    session_id=payload.get("session_id"),
                )
            
            for edge in result.get("new_edges", []):
                db.add_skill_edge(
                    conn,
                    edge["from"],
                    edge["to"],
                    edge["relation"]
                )
            
            for rec in result.get("recommendations", []):
                related = db.upsert_skill(
                    conn, 
                    rec["related_skill"]
                ) if rec.get("related_skill") else None
                db.add_recommendation(conn, rec["text"], related)
            
            flowchart = db.get_flowchart(conn)
        
        elif item["kind"] == "chat_message":
            pass
        
        db.mark_synced(conn, item["id"])
        processed += 1
    
    return processed
