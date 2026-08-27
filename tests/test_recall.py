"""Tests for CE-002: indexer (chunking, idempotency, incrementality).

Uses a fake embedding model and a throwaway session DB — no network, no real data.
"""

import json
import sqlite3

import pytest

from engine.config import Settings
from engine.recall.indexer import Indexer, chunk_from_row, iter_new_messages

EMBED_DIM = 384


class FakeModel:
    """Deterministic embeddings: hash of the text → unit vector."""

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim
        self.calls: list[str] = []

    def encode(self, texts, normalize_embeddings=True):
        self.calls.extend(texts)
        out = []
        for t in texts:
            h = hash(t) % (2**31)
            vec = [float((h >> (i % 8)) & 0xFF) / 255.0 for i in range(self.dim)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


@pytest.fixture
def fake_db(tmp_path):
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
        INSERT INTO sessions (id, title) VALUES ('s1', 'Session one'), ('s2', 'Session two');
        INSERT INTO messages (session_id, role, content, timestamp) VALUES
            ('s1', 'user', 'We decided to use LangGraph for agents', 1000.0),
            ('s1', 'assistant', 'Agreed — LangGraph with FastAPI', 1001.0),
            ('s2', 'user', 'The deploy failed with a 500 on Vercel', 2000.0);
        """
    )
    con.commit()
    con.close()
    return str(db)


@pytest.fixture
def settings(tmp_path, fake_db):
    return Settings(session_db=fake_db, vector_store=str(tmp_path / "vectors"))


def test_chunk_from_row_trims_tool_blobs():
    import sqlite3 as s

    con = s.connect(":memory:")
    con.row_factory = s.Row
    con.execute("CREATE TABLE t (id, session_id, role, content, tool_name, timestamp, title)")
    con.execute("INSERT INTO t VALUES (1, 's', 'tool', ?, 'bash', 1.0, 't')", (json.dumps({"output": "x" * 5000}),))
    row = con.execute("SELECT * FROM t").fetchone()
    # selective RAG: tools are skipped by default (CE-014)
    assert chunk_from_row(row) is None
    chunk = chunk_from_row(row, index_tools=True)
    assert chunk is not None
    assert chunk.text.startswith("[tool:bash]")
    assert "truncated" in chunk.text
    assert len(chunk.text) < 2500


def test_index_creates_chunks(settings):
    idx = Indexer(settings, model=FakeModel())
    stats = idx.index()
    assert stats["new_indexed"] == 3
    assert stats["last_message_id"] == 3

    con = sqlite3.connect(idx.vec_path)
    meta = con.execute("SELECT message_id, session_id, role, text FROM meta ORDER BY message_id").fetchall()
    con.close()
    assert len(meta) == 3
    assert meta[0][1] == "s1"
    assert meta[0][2] == "user"
    assert "LangGraph" in meta[0][3]


def test_index_is_idempotent(settings):
    idx = Indexer(settings, model=FakeModel())
    idx.index()
    stats2 = idx.index()
    assert stats2["new_indexed"] == 0

    con = sqlite3.connect(idx.vec_path)
    n = con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    con.close()
    assert n == 3


def test_index_is_incremental(settings, fake_db):
    idx = Indexer(settings, model=FakeModel())
    idx.index()

    con = sqlite3.connect(fake_db)
    con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'assistant', 'New insight', 3000.0)")
    con.commit()
    con.close()

    stats = idx.index()
    assert stats["new_indexed"] == 1

    con = sqlite3.connect(idx.vec_path)
    n = con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    con.close()
    assert n == 4


def test_iter_new_messages_respects_last_id(settings):
    rows = iter_new_messages(settings.session_db, last_id=1)
    assert [r["id"] for r in rows] == [2, 3]
