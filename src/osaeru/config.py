"""Config file containing vars to be used in the app."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    age INTEGER,
    sex TEXT,
    weight_kg REAL,
    sessions_per_week REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    notes TEXT NOT NULL,
    feeling TEXT,               -- freeform: "good", "tired", "frustrated" etc
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT,               -- e.g. "guard", "submission", "escape"
    confidence TEXT,              -- e.g. "learning", "comfortable", "reliable"
    first_seen_session_id INTEGER REFERENCES sessions(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_skill_id INTEGER NOT NULL REFERENCES skills(id),
    to_skill_id INTEGER NOT NULL REFERENCES skills(id),
    relation TEXT NOT NULL,       -- e.g. "leads_to", "counters", "depends_on"
    UNIQUE(from_skill_id, to_skill_id, relation)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,           -- "user" or "assistant"
    content TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    related_skill_id INTEGER REFERENCES skills(id),
    status TEXT NOT NULL DEFAULT 'new',  -- new | tried | skipped
    created_at TEXT NOT NULL
);

-- Anything written while offline lands here until claude_sync can send it.
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,           -- "session_note" | "chat_message"
    payload TEXT NOT NULL,        -- JSON blob
    created_at TEXT NOT NULL,
    sent_at TEXT
);
"""

KEYRING_SERVICE = "bjj_coach"

KEYRING_KEY = "anthropic_api_key"

MODEL = "claude-sonnet-4-6"  # keep in sync with whatever tier/model you're on

FLOWCHART_SYSTEM_PROMPT = """\
You are a jiu jitsu coaching assistant. You receive a training session note \
and the practitioner's current skill graph (nodes = techniques/positions, \
edges = relationships like leads_to / counters / depends_on). Your job:

1. Extract any techniques, positions, or concepts mentioned or implied.
2. Propose new nodes and edges to merge into the graph (don't repeat ones \
   that already exist unless their confidence changed).
3. Suggest 1-3 concrete things to try next session, tied to a specific skill.

Respond ONLY with JSON, no prose, no markdown fences, matching this shape:
{
  "new_skills": [{"name": str, "category": str, "confidence": str}],
  "new_edges": [{"from": str, "to": str, "relation": str}],
  "recommendations": [{"text": str, "related_skill": str}]
}
"""

NODE_RADIUS = 45
