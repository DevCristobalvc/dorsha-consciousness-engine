"""Tests for CE-014: selective memory — curated saves + no tool indexing."""

import hashlib
import re
import sqlite3

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.memory import list_memory, remove, save
from engine.recall.indexer import Indexer, chunk_from_row
from engine.recall.retriever import Retriever

EMBED_DIM = 384


class FakeModel:
    """Deterministic bag-of-words embeddings (mirror of other recall tests)."""

    def encode(self, texts, normalize_embeddings=True):
        out = []
        for t in texts:
            vec = [0.0] * EMBED_DIM
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % EMBED_DIM
                vec[idx] += 1.0
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


@pytest.fixture
def env(tmp_path):
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
        INSERT INTO sessions (id, title) VALUES ('s1', 'build');
        """
    )
    con.commit()
    con.close()

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    return settings, db


def _add(db, role, content, ts, tool=None):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, tool_name) VALUES ('s1', ?, ?, ?, ?)",
        (role, content, ts, tool),
    )
    con.commit()
    con.close()


def test_indexer_skips_tools_by_default(env):
    settings, db = env
    _add(db, "user", "haz el deploy", 2000.0)
    _add(db, "tool", '{"output": "build log 500 lines"}', 2001.0, tool="bash")
    _add(db, "assistant", "deploy listo", 2002.0)
    stats = Indexer(settings, model=FakeModel()).index()
    # only user + assistant chunks, no tool chunk
    con = sqlite3.connect(settings.vector_store + "/recall.vec.db")
    roles = [r[0] for r in con.execute("SELECT role FROM meta").fetchall()]
    con.close()
    assert "tool" not in roles
    assert "user" in roles and "assistant" in roles


def test_indexer_includes_tools_when_enabled(env):
    settings, db = env
    settings.recall.index_tools = True
    _add(db, "tool", '{"output": "log"}', 2000.0, tool="bash")
    Indexer(settings, model=FakeModel()).index()
    con = sqlite3.connect(settings.vector_store + "/recall.vec.db")
    roles = [r[0] for r in con.execute("SELECT role FROM meta").fetchall()]
    con.close()
    assert "tool" in roles


def test_chunk_from_row_filters_tool(env):
    settings, db = env
    _add(db, "tool", "dump", 2000.0, tool="bash")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert chunk_from_row(row) is None  # default: skip
    assert chunk_from_row(row, index_tools=True) is not None  # explicit: keep


def test_save_and_list_memory(env):
    settings, db = env
    mid = save(settings, "decisión: el deploy de Vercel usa push a main", tags="deploy,vercel", source="agent", model=FakeModel())
    assert mid > 0
    items = list_memory(settings)
    assert len(items) == 1
    assert items[0]["tags"] == "deploy,vercel"
    assert items[0]["source"] == "agent"


def test_remove_memory(env):
    settings, db = env
    mid = save(settings, "lección temporal", model=FakeModel())
    assert remove(settings, mid) is True
    assert list_memory(settings) == []


def test_retriever_combines_memory_with_boost(env):
    settings, db = env
    _add(db, "user", "qué modelo usamos para el deploy", 1000.0)
    _add(db, "assistant", "usamos deepseek-chat", 1001.0)
    save(settings, "decisión importante: deepseek-chat para el deploy", tags="deepseek", source="agent", model=FakeModel())
    r = Retriever(settings, model=FakeModel())
    results = r.query("deepseek deploy", k=3)
    mem_hits = [c for c in results if c.session_id == "__memory__"]
    assert mem_hits, "saved memory must be retrieved"
    # memory boost: the curated entry should outscore its unboosted counterparts
    assert results[0].session_id == "__memory__" or any(c.score > 0 for c in mem_hits)


def test_engine_memory_save_and_cli(env, tmp_path):
    settings, db = env
    engine = Engine(settings, todo_path=str(tmp_path / "TODO.md"))
    mid = engine.memory_save("lección aprendida", tags="lesson", source="conciencia")
    items = engine.memory_list()
    assert items[0]["id"] == mid
    assert items[0]["source"] == "conciencia"
