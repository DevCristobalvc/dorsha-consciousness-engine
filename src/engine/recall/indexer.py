"""Indexer: reads the agent's session DB and embeds messages into a vector store.

Design (CE-002):
- Reads ``settings.session_db`` (Hermes ``state.db`` schema: ``sessions`` + ``messages``)
- Incremental: tracks the last indexed ``message_id`` — idempotent, no duplicates
- Chunks: one chunk per active message; large tool dumps trimmed with provenance
- Embeddings: local sentence-transformers model (configurable), injectable for tests
- Vector store: sqlite-vec (``vec0`` virtual table) + ``meta`` table with provenance

The schema adapter is a single function (``iter_new_messages``) so other agent
databases can be plugged in without touching the embedding/vector logic.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from engine.config import Settings

MAX_TOOL_CHARS = 2000  # tool outputs trimmed to this length
SUMMARY_CHARS = 500
EMBED_DIM = 384  # all-MiniLM-L6-v2


@dataclass
class Chunk:
    """One retrievable unit with full provenance."""

    message_id: int
    session_id: str
    role: str
    text: str
    timestamp: float


def iter_new_messages(db_path: str, last_id: int = 0, limit: int = 1000) -> list[sqlite3.Row]:
    """Yield active messages (id > last_id) with their session title, oldest first.

    Single point of schema coupling: adapt here for non-Hermes session DBs.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            """
            SELECT m.id, m.session_id, m.role, m.content, m.tool_name, m.timestamp, s.title
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.id > ? AND m.active = 1 AND s.archived = 0 AND m.compacted = 0
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (last_id, limit),
        ).fetchall()
    finally:
        con.close()


def chunk_from_row(row: sqlite3.Row) -> Chunk | None:
    """Convert a DB row into a chunk, trimming tool output to a bounded summary."""
    content = row["content"] or ""
    role = row["role"]

    if role == "tool":
        try:
            data = json.loads(content)
            text = data.get("output") or data.get("result") or json.dumps(data)[:SUMMARY_CHARS]
        except (json.JSONDecodeError, TypeError):
            text = content[:SUMMARY_CHARS]
        if len(text) > MAX_TOOL_CHARS:
            text = text[:MAX_TOOL_CHARS] + f" … [truncated {len(text)} chars]"
        text = f"[tool:{row['tool_name'] or 'output'}] {text}"
    elif role in ("user", "assistant"):
        text = content
    else:
        return None

    if not text.strip():
        return None
    return Chunk(
        message_id=row["id"],
        session_id=row["session_id"],
        role=role,
        text=text.strip(),
        timestamp=float(row["timestamp"]),
    )


class Indexer:
    """Incremental embedding indexer backed by sqlite-vec."""

    def __init__(self, settings: Settings, model=None, batch_size: int = 64):
        self.settings = settings
        self.model = model  # resolved lazily in index() to avoid import cost
        self.batch_size = batch_size
        self.vec_path = Path(settings.vector_store) / "recall.vec.db"

    def _connect(self) -> sqlite3.Connection:
        import sqlite_vec  # local import: optional dependency

        self.vec_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.vec_path)
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING vec0(embedding float[{EMBED_DIM}])")
        con.execute(
            """CREATE TABLE IF NOT EXISTS meta (
                message_id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp REAL NOT NULL
            )"""
        )
        return con

    def _last_indexed(self, con: sqlite3.Connection) -> int:
        row = con.execute("SELECT COALESCE(MAX(message_id), 0) FROM meta").fetchone()
        return int(row[0])

    def index(self, limit: int = 0) -> dict:
        """Index all new messages (or up to ``limit`` rows) and return stats."""
        if not self.settings.session_db:
            return {"rows_read": 0, "chunks_created": 0, "new_indexed": 0, "last_message_id": 0, "error": "no session_db configured"}
        if self.model is None:
            from sentence_transformers import SentenceTransformer  # local import

            self.model = SentenceTransformer(self.settings.embedding_model)

        rows = iter_new_messages(self.settings.session_db, last_id=0, limit=limit or 10**9)
        chunks = [c for c in (chunk_from_row(r) for r in rows) if c is not None]

        con = self._connect()
        try:
            last_id = self._last_indexed(con)
            new = [c for c in chunks if c.message_id > last_id]
            for i in range(0, len(new), self.batch_size):
                batch = new[i : i + self.batch_size]
                vectors = self.model.encode([c.text for c in batch], normalize_embeddings=True)
                for chunk, vec in zip(batch, vectors):
                    vec_json = json.dumps([float(x) for x in vec])
                    con.execute(
                        "INSERT INTO chunks (rowid, embedding) VALUES (?, ?)",
                        (chunk.message_id, vec_json),
                    )
                    con.execute(
                        "INSERT OR REPLACE INTO meta (message_id, session_id, role, text, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (chunk.message_id, chunk.session_id, chunk.role, chunk.text, chunk.timestamp),
                    )
            con.commit()
        finally:
            con.close()

        return {
            "rows_read": len(rows),
            "chunks_created": len(chunks),
            "new_indexed": sum(1 for c in chunks if c.message_id > last_id) if "last_id" in locals() else len(chunks),
            "last_message_id": max((c.message_id for c in chunks), default=0),
        }
