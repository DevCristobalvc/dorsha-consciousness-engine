"""Curated memory (CE-014): what the agent explicitly chooses to keep.

The RAG is selective by design:

    - user prompts (user/conciencia)   → indexed from the session history
    - agent's own replies              → indexed from the session history
    - saved memory (`ce save`)         → the agent decides what matters

Tool outputs are NOT indexed by default (``recall.index_tools=False``).
The agent (or the consciousness) calls ``ce save`` to persist decisions,
lessons and facts it wants to remember forever — stored in the same vector
store, weighted higher at retrieval (``recall.memory_boost``).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from engine.config import Settings


def _connect(settings: Settings) -> sqlite3.Connection:
    vec_dir = Path(settings.vector_store)
    vec_dir.mkdir(parents=True, exist_ok=True)
    db = vec_dir / "recall.vec.db"
    con = sqlite3.connect(db)
    con.enable_load_extension(True)
    try:
        import sqlite_vec

        sqlite_vec.load(con)
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS mem USING vec0(embedding float[384])")
    except Exception:  # noqa: BLE001 — memory degrades to metadata-only if vectors unavailable
        pass
    con.execute(
        """CREATE TABLE IF NOT EXISTS saved_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            tags TEXT DEFAULT '',
            source TEXT DEFAULT 'agent',
            created_at REAL NOT NULL,
            message_id INTEGER
        )"""
    )
    return con


def save(settings: Settings, text: str, tags: str = "", source: str = "agent",
         message_id: int | None = None, embedding: list[float] | None = None,
         model=None) -> int:
    """Persist a memory entry; returns its id. Embedding optional (computed if omitted)."""
    con = _connect(settings)
    try:
        cur = con.execute(
            "INSERT INTO saved_memory (text, tags, source, created_at, message_id) VALUES (?, ?, ?, ?, ?)",
            (text, tags, source, time.time(), message_id),
        )
        mem_id = cur.lastrowid
        con.commit()
        if embedding is None:
            embedding = _embed(settings, text, model)
        if embedding is not None and len(embedding) == 384:
            con.execute("INSERT INTO mem (rowid, embedding) VALUES (?, ?)", (mem_id, json.dumps(embedding)))
            con.commit()
        return int(mem_id)
    finally:
        con.close()


def list_memory(settings: Settings, limit: int = 50) -> list[dict]:
    con = _connect(settings)
    try:
        rows = con.execute(
            "SELECT id, text, tags, source, created_at FROM saved_memory ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "text": r[1], "tags": r[2] or "", "source": r[3], "created_at": r[4]}
            for r in rows
        ]
    finally:
        con.close()


def remove(settings: Settings, mem_id: int) -> bool:
    con = _connect(settings)
    try:
        cur = con.execute("DELETE FROM saved_memory WHERE id = ?", (mem_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def _embed(settings: Settings, text: str, model=None) -> list[float] | None:
    try:
        if model is None:
            from engine.recall.indexer import _load_model

            model = _load_model(settings)
        vec = model.encode([text], normalize_embeddings=True)[0]
        return [float(x) for x in vec]  # numpy → plain floats (JSON-safe)
    except Exception:  # noqa: BLE001
        return None
