"""Tests for CE-016: MCP over HTTP — same engine, URL transport."""

import hashlib
import json
import re
import sqlite3
import threading
import urllib.request

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.mcp_http import serve
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
def base(tmp_path):
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
            ('s1', 'user', 'usamos deepseek-chat para el deploy', 1000.0);
        """
    )
    con.commit()
    con.close()
    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    srv = serve(port=0, settings=settings, engine=Engine(settings, todo_path=str(tmp_path / "TODO.md")))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _post(url, msg, token="", timeout=30):
    req = urllib.request.Request(
        url + "/", data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def test_health(base):
    with urllib.request.urlopen(base + "/health", timeout=5) as r:
        body = json.loads(r.read())
    assert body["ok"] is True
    assert "chunks" in body


def test_http_initialize(base):
    status, body = _post(base, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "dorsha-consciousness-engine"


def test_http_tools_list(base):
    _, body = _post(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in body["result"]["tools"]]
    assert "ce_memory_search" in names


def test_http_memory_search(base):
    _, body = _post(base, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "ce_memory_search", "arguments": {"query": "deepseek", "k": 2}}})
    results = json.loads(body["result"]["content"][0]["text"])
    assert results and results[0]["text"].startswith("usamos deepseek")


def test_http_auth_required(base):
    srv = None  # base fixture is unauth; test auth via a token server
    settings = Settings(session_db="", vector_store="/nonexistent-vec")
    srv2 = serve(port=0, token="sekret", settings=settings, engine=Engine(settings, todo_path="/tmp/x"))
    port = srv2.server_address[1]
    t = threading.Thread(target=srv2.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert exc.value.code == 401
        status, body = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token="sekret")
        assert status == 200
    finally:
        srv2.shutdown()
