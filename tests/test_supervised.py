"""Tests for CE-012: supervised loop (iterations, token budget, escalation)."""

import hashlib
import json
import re
import sqlite3

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.loop.worker import Notifier
from engine.loop.supervised import SupervisedLoop
from engine.recall.indexer import Indexer

EMBED_DIM = 384


class FakeModel:
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
            compacted INTEGER NOT NULL DEFAULT 0,
            token_count INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sessions (id, title) VALUES ('s1', 'worker session');
        """
    )
    con.commit()
    con.close()

    todo = tmp_path / "TODO.md"
    todo.write_text(
        "## TASK: T1 — Build\n\n### Description\nx\n\n### Status\n- in_progress\n",
        encoding="utf-8",
    )

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    engine = Engine(settings, todo_path=str(todo), advisor_client=object())
    return settings, db, todo, engine


def _add(db, role, content, ts, tokens=10, session="s1"):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, token_count) VALUES (?, ?, ?, ?, ?)",
        (session, role, content, ts, tokens),
    )
    con.commit()
    con.close()


def _loop(env, tmp_path, **kw):
    settings, db, todo, engine = env
    sent = []

    class FakeNotifier(Notifier):
        def send(self, payload):
            sent.append(payload)
            return True

    loop = SupervisedLoop(settings, engine, todo_path=todo, notifier=FakeNotifier(""),
                          state_path=tmp_path / "supervise_state.json")
    return loop, sent


def test_start_sets_state(env, tmp_path):
    loop, _ = _loop(env, tmp_path)
    st = loop.start("T1", max_iterations=2)
    assert st["active"] is True
    assert st["max_iterations"] == 2
    assert loop.active is True


def test_tick_injects_continuation(env, tmp_path):
    loop, sent = _loop(env, tmp_path)
    loop.start("T1", max_iterations=3)
    _add(env[1], "user", "haz la tarea", 1000.0)
    _add(env[1], "assistant", "tarea avanzando bien", 1001.0)
    t = loop.tick()
    assert t.action == "injected"
    assert t.iteration == 1
    assert sent[0]["source"] == "consciousness"
    assert "conciencia" in sent[0]["message"]


def test_tick_waits_for_new_turn(env, tmp_path):
    loop, _ = _loop(env, tmp_path)
    loop.start("T1", max_iterations=3)
    _add(env[1], "assistant", "primera respuesta", 1000.0)
    loop.tick()  # processes msg 1
    t = loop.tick()  # same msg → wait
    assert t.action == "idle_wait"


def test_iterations_exhausted_returns_control(env, tmp_path):
    loop, _ = _loop(env, tmp_path)
    loop.start("T1", max_iterations=1)
    _add(env[1], "assistant", "respuesta uno", 1000.0)
    loop.tick()  # iteration 1/1 → injected
    _add(env[1], "assistant", "respuesta dos", 1001.0)
    t = loop.tick()
    assert t.action == "iterations_done"
    assert loop.active is False


def test_token_budget_stops_loop(env, tmp_path):
    loop, _ = _loop(env, tmp_path)
    loop.start("T1", max_iterations=5, max_tokens=25)
    _add(env[1], "assistant", "respuesta con tokens", 1000.0, tokens=30)
    t = loop.tick()
    assert t.action == "tokens_done"
    assert loop.active is False


def test_failure_escalates_through_advisor(env, tmp_path):
    settings, db, todo, engine = env
    sent = []

    class FakeNotifier(Notifier):
        def send(self, payload):
            sent.append(payload)
            return True

    class FakeAdvisorClient:
        def __init__(self):
            self._chat = _FakeChat()

        @property
        def chat(self):
            return self._chat

    class _FakeChat:
        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            content = json.dumps({"alternatives": ["alt1"], "recommendation": "revisa los logs", "confidence": 0.8})
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

    engine2 = Engine(settings, todo_path=todo, advisor_client=FakeAdvisorClient())
    loop = SupervisedLoop(settings, engine2, todo_path=todo, notifier=FakeNotifier(""),
                          state_path=tmp_path / "supervise_state.json")
    loop.start("T1", max_iterations=3)
    _add(db, "assistant", "el deploy falló con error 500", 1000.0)
    t = loop.tick()
    assert t.action == "injected"
    assert "DeepSeek" in sent[0]["message"] or "asesor" in sent[0]["message"]


def test_advisor_failure_marks_task_blocked(env, tmp_path):
    loop, sent = _loop(env, tmp_path)
    loop.start("T1", max_iterations=3)
    _add(env[1], "assistant", "el deploy falló con error 500", 1000.0)  # advisor_client=object() → fails
    t = loop.tick()
    assert t.action == "task_blocked"
    assert loop.active is False
    todo_text = env[2].read_text(encoding="utf-8")
    assert "blocked" in todo_text.lower()
