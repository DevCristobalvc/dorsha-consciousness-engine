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
        """Return top-k chunks for ``text``, ranked by semantic + recency score."""
        k = k or self.settings.recall.top_k
        vector = self._embed(text)
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT rowid, distance FROM chunks WHERE embedding MATCH ? AND k = ?",
                (json.dumps(vector), k),
            ).fetchall()
            if not rows:
                return []
            meta = {
                r[0]: r
                for r in con.execute(
                    f"SELECT message_id, session_id, role, text, timestamp FROM meta WHERE message_id IN ({','.join(str(r[0]) for r in rows)})"
                ).fetchall()
            }
        finally:
            con.close()

        now = time.time()
        half_life = max(float(self.settings.recall.recency_half_life_days) * 86400.0, 1e-9)
        results = []
        for message_id, distance in rows:
            m = meta.get(message_id)
            if m is None:
                continue
            age = max(now - m[4], 0.0)
            decay = math.exp(-age / half_life)
            score = (1.0 - float(distance)) * decay
            results.append(
                RetrievedChunk(
                    message_id=m[0],
                    session_id=m[1],
                    role=m[2],
                    text=m[3],
                    timestamp=m[4],
                    distance=float(distance),
                    score=score,
                )
            )
        results.sort(key=lambda c: c.score, reverse=True)
        return results[:k]
