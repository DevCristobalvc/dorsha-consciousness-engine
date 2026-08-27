"""Tests for CE-007: engine orchestration + CLI."""

import hashlib
import re
import sqlite3

import pytest

from engine.advisor.advisor import AdvisorBrief
from engine.cli import main
from engine.config import Settings
from engine.core import Engine
from engine.recall.indexer import Indexer

EMBED_DIM = 384


class FakeModel:
    """Deterministic bag-of-words embeddings (mirror of recall tests)."""

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
    """Full fake environment: session DB + TODO + indexed vectors."""
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
        INSERT INTO sessions (id, title) VALUES ('s1', 'RAG build');
        INSERT INTO messages (session_id, role, content, timestamp) VALUES
            ('s1', 'user', 'We decided to use LangGraph for agents', 1000.0),
            ('s1', 'assistant', 'Agreed — LangGraph with FastAPI', 1001.0);
        """
    )
    con.commit()
    con.close()

    todo = tmp_path / "TODO.md"
    todo.write_text(
        "## TASK: T1 — Build engine\n\n### Description\nx\n\n### Status\n- in_progress\n",
        encoding="utf-8",
    )

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    return settings, todo, FakeModel()


def test_engine_recall_block(env):
    settings, todo, model = env
    e = Engine(settings, todo_path=todo)
    block = e.recall("LangGraph agents", k=2)
    assert block.startswith("[RECALL — retrieved from session history]")
    assert "s1 msg:" in block


def test_engine_judge_decision(env):
    settings, todo, model = env
    e = Engine(settings, todo_path=todo)
    d = e.judge("the build failed with an error", task_id="t1")
    assert d.action == "retry"
    d2 = e.judge("No sé cómo seguir", task_id="t2")
    assert d2.action == "advisor"


def test_engine_advise_failure_returns_none(env):
    settings, todo, model = env
    e = Engine(settings, todo_path=todo, advisor_client=object())  # broken client -> AdvisorError -> None
    assert e.advise(AdvisorBrief(problem="x")) is None


def test_engine_tick(env):
    settings, todo, model = env
    e = Engine(settings, todo_path=todo)
    action = e.tick(last_activity_ts=0.0, now=10000.0, last_exchange="user: sigue")
    assert action.action == "wake"
    assert action.task.id == "T1"


def test_engine_status(env):
    settings, todo, model = env
    e = Engine(settings, todo_path=todo)
    st = e.status()
    assert st["chunks_indexed"] == 2
    assert st["todo_next"] == "T1"
    assert st["todo_blocked"] == []
    assert st["thresholds"]["max_attempts"] == 3


def test_cli_judge(capsys, env):
    settings, todo, model = env
    assert main(["--config", "", "--todo", str(todo), "judge", "error during deploy"]) == 0
    out = capsys.readouterr().out
    assert "action: retry" in out


def test_cli_recall(capsys, env):
    settings, todo, model = env
    assert main(["--config", "", "--todo", str(todo), "recall", "LangGraph", "--k", "1"]) == 0
    out = capsys.readouterr().out
    assert "[RECALL" in out


def test_cli_loop_status(capsys, env):
    settings, todo, model = env
    assert main(["--config", "", "--todo", str(todo), "loop", "status"]) == 0
    out = capsys.readouterr().out
    assert "next: T1" in out
