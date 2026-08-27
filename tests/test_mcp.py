"""Tests for CE-015: MCP server — the RAG as a callable agent tool."""

import hashlib
import json
import re
import sqlite3

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.mcp_server import TOOLS, McpEngine
from engine.recall.indexer import Indexer


class FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        out = []
        for t in texts:
            vec = [0.0] * 384
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % 384
                vec[idx] += 1.0
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


@pytest.fixture
def mcp(tmp_path):
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
        INSERT INTO messages (session_id, role, content, timestamp) VALUES
            ('s1', 'user', 'usamos deepseek-chat para el deploy', 1000.0),
            ('s1', 'assistant', 'el deploy se hace con push a main', 1001.0);
        """
    )
    con.commit()
    con.close()

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    from engine import memory

    memory.save(settings, "decision clave: deepseek-chat para el deploy", tags="deepseek", model=FakeModel())
    return McpEngine(settings, Engine(settings, todo_path=str(tmp_path / "TODO.md")))


def _msg(mcp, method, params=None, msg_id=1):
    return mcp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def test_initialize(mcp):
    r = _msg(mcp, "initialize", {"protocolVersion": "2024-11-05"})
    assert r["result"]["serverInfo"]["name"] == "dorsha-consciousness-engine"
    assert "tools" in r["result"]["capabilities"]


def test_tools_list_exposes_search_tool(mcp):
    r = _msg(mcp, "tools/list")
    names = [t["name"] for t in r["result"]["tools"]]
    assert "ce_memory_search" in names
    assert "ce_memory_save" in names
    assert "ce_judge" in names
    assert "ce_status" in names


def test_memory_search_returns_citations(mcp):
    r = _msg(mcp, "tools/call", {"name": "ce_memory_search", "arguments": {"query": "deepseek deploy", "k": 3}})
    assert "isError" not in r["result"]
    results = json.loads(r["result"]["content"][0]["text"])
    assert results, "must return at least one result"
    assert any(c["session_id"] == "__memory__" for c in results)  # curated memory first


def test_memory_save_tool(mcp):
    r = _msg(mcp, "tools/call", {"name": "ce_memory_save", "arguments": {"text": "lección nueva", "tags": "x"}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert body["saved_id"] > 0


def test_judge_tool(mcp):
    r = _msg(mcp, "tools/call", {"name": "ce_judge", "arguments": {"text": "el deploy falló con error 500"}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert body["action"] in ("retry", "advisor", "escalate")


def test_status_tool(mcp):
    r = _msg(mcp, "tools/call", {"name": "ce_status", "arguments": {}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert "chunks_indexed" in body


def test_unknown_tool_returns_error(mcp):
    r = _msg(mcp, "tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602


def test_unknown_method(mcp):
    r = _msg(mcp, "bogus")
    assert r["error"]["code"] == -32601


def test_notification_returns_none(mcp):
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
