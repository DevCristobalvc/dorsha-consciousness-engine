"""Retriever: hybrid semantic + recency retrieval over the indexed session history.

Scoring (CE-003):
    score = (1 - cosine_distance) * recency_decay
    recency_decay = exp(-age_days / half_life_days)

Queries are embedded with the same model used by the Indexer; the ``vec0``
table stores normalized embeddings so distance == cosine distance.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from engine.config import Settings


@dataclass
class RetrievedChunk:
    message_id: int
    session_id: str
    role: str
    text: str
    timestamp: float
    distance: float
    score: float


class Retriever:
    def __init__(self, settings: Settings, model=None):
        self.settings = settings
        self.model = model
        self.vec_path = Path(settings.vector_store) / "recall.vec.db"

    def _connect(self) -> sqlite3.Connection:
        import sqlite_vec  # local import: optional dependency

        con = sqlite3.connect(self.vec_path)
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        return con

    def _embed(self, text: str) -> list[float]:
        if self.model is None:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(self.settings.embedding_model)
        return [float(x) for x in self.model.encode([text], normalize_embeddings=True)[0]]

    def query(self, text: str, k: int | None = None) -> list[RetrievedChunk]:
        """Return top-k chunks for ``text``, ranked by semantic + recency score.

        Combines two sources (CE-014 selective memory):
        1. session history (user prompts + agent replies)
        2. curated memory (``ce save``) — scored with ``memory_boost``
        """
        k = k or self.settings.recall.top_k
        vector = self._embed(text)
        con = self._connect()
        try:
            hist = con.execute(
                "SELECT rowid, distance FROM chunks WHERE embedding MATCH ? AND k = ?",
                (json.dumps(vector), k * 2),
            ).fetchall()
            hist_meta = {
                r[0]: r
                for r in con.execute(
                    f"SELECT message_id, session_id, role, text, timestamp FROM meta WHERE message_id IN ({','.join(str(r[0]) for r in hist) if hist else '0'})"
                ).fetchall()
            }
            try:
                mem = con.execute(
                    "SELECT rowid, distance FROM mem WHERE embedding MATCH ? AND k = ?",
                    (json.dumps(vector), k),
                ).fetchall()
                mem_meta = {
                    r[0]: r
                    for r in con.execute(
                        f"SELECT id, text, tags, created_at FROM saved_memory WHERE id IN ({','.join(str(r[0]) for r in mem) if mem else '0'})"
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                mem, mem_meta = [], {}
        finally:
            con.close()

        now = time.time()
        half_life = max(float(self.settings.recall.recency_half_life_days) * 86400.0, 1e-9)
        results = []

        def _cosine(distance: float) -> float:
            # sqlite-vec returns squared euclidean distance; for unit vectors cosine = 1 - d/2
            return max(1.0 - distance / 2.0, 0.0)

        for message_id, distance in hist:
            m = hist_meta.get(message_id)
            if m is None:
                continue
            cosine = _cosine(float(distance))
            age = max(now - m[4], 0.0)
            score = cosine * math.exp(-age / half_life)
            results.append(
                RetrievedChunk(
                    message_id=m[0], session_id=m[1], role=m[2], text=m[3],
                    timestamp=m[4], distance=float(distance), score=score,
                )
            )

        for mem_id, distance in mem:
            m = mem_meta.get(mem_id)
            if m is None:
                continue
            cosine = _cosine(float(distance))
            age = max(now - m[3], 0.0)
            # curated memory is weighted higher — the agent chose to keep it
            score = cosine * math.exp(-age / half_life) * self.settings.recall.memory_boost
            results.append(
                RetrievedChunk(
                    message_id=-mem_id,  # negative id → curated memory, not a session message
                    session_id="__memory__", role="memory", text=m[1],
                    timestamp=m[3], distance=float(distance), score=score,
                )
            )
        results.sort(key=lambda c: c.score, reverse=True)
        return results[:k]
