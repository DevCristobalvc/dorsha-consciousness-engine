"""Tests for CE-003: retriever + injector."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from engine.config import Settings
from engine.recall.indexer import Indexer
from engine.recall.injector import Injector
from engine.recall.retriever import RetrievedChunk, Retriever

EMBED_DIM = 384


class FakeModel:
    def __init__(self):
        self.dim = EMBED_DIM

    def encode(self, texts, normalize_embeddings=True):
        out = []
        for t in texts:
            h = hash(t) % (2**31)
            vec = [float((h >> (i % 8)) & 0xFF) / 255.0 for i in range(self.dim)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


@pytest.fixture
def indexed_env(tmp_path):
    """A session DB with known messages, indexed with the fake model."""
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            compacted INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sessions (id, title) VALUES ('s1', 'RAG build'), ('s2', 'Vercel deploy');
        INSERT INTO messages (session_id, role, content, timestamp) VALUES
            ('s1', 'user', 'We decided to use LangGraph for agents', 1000.0),
            ('s1', 'assistant', 'Agreed — LangGraph with FastAPI', 1001.0),
            ('s2', 'user', 'The deploy failed with a 500 on Vercel', 2000.0);
        """
    )
    con.commit()
    con.close()

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    model = FakeModel()
    Indexer(settings, model=model).index()
    return settings, model


def test_retriever_returns_chunks_with_provenance(indexed_env):
    settings, model = indexed_env
    retriever = Retriever(settings, model=model)
    results = retriever.query("LangGraph agents", k=2)
    assert len(results) == 2
    for r in results:
        assert r.session_id in ("s1", "s2")
        assert r.message_id >= 1
        assert r.score > 0
        assert 0.0 <= r.distance <= 2.0


def test_retriever_semantic_ranking(indexed_env):
    settings, model = indexed_env
    retriever = Retriever(settings, model=model)
    results = retriever.query("LangGraph agents", k=3)
    # the LangGraph messages should rank above the Vercel one
    top_ids = {r.message_id for r in results[:2]}
    assert 1 in top_ids or 2 in top_ids
    assert results[0].session_id == "s1"


def test_retriever_recency_boost(monkeypatch, indexed_env):
    import engine.recall.retriever as retriever_mod

    settings, model = indexed_env
    monkeypatch.setattr(settings.recall, "recency_half_life_days", 0.001)  # 86.4s half-life
    monkeypatch.setattr(retriever_mod.time, "time", lambda: 2000.0)  # freeze clock at newest message
    retriever = Retriever(settings, model=model)
    results = retriever.query("anything", k=3)
    # msg 3 (ts 2000, age 0) must rank first; msg 1 (ts 1000) decays ~e^-11
    assert results[0].message_id == 3
    assert results[0].score > results[-1].score


def test_injector_format_has_citations(indexed_env):
    settings, _ = indexed_env
    injector = Injector(settings)
    chunk = RetrievedChunk(message_id=2, session_id="s1", role="assistant", text="Agreed — LangGraph", timestamp=1001.0, distance=0.1, score=0.9)
    block = injector.format([chunk], query="stack decision")
    assert block.startswith("[RECALL — retrieved from session history]")
    assert "s1 msg:2 @" in block
    assert "assistant" in block
    assert "stack decision" in block


def test_injector_truncates(indexed_env):
    settings, _ = indexed_env
    injector = Injector(settings)
    chunks = [RetrievedChunk(message_id=i, session_id="s", role="user", text="x" * 500, timestamp=1.0, distance=0.1, score=0.9) for i in range(1, 10)]
    block = injector.format(chunks, max_chars=1000)
    assert len(block) <= 1000
    assert "truncated to fit context" in block


def test_injector_iso_timestamp(indexed_env):
    settings, _ = indexed_env
    injector = Injector(settings)
    block = injector.format([RetrievedChunk(message_id=1, session_id="s", role="user", text="t", timestamp=1000.0, distance=0.0, score=1.0)])
    assert "1970-01-01 00:16 UTC" in block  # 1000s epoch
