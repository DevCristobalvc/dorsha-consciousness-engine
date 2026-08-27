"""Tests for CE-009: watchdog — continuous supervision of a live session."""

import hashlib
import re
import sqlite3

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.loop.worker import Notifier
from engine.recall.indexer import Indexer
from engine.watchdog import Watchdog, WatchdogReport

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
def live_env(tmp_path):
    """Session DB with a live session + indexed vectors."""
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
        INSERT INTO sessions (id, title) VALUES ('live1', 'Active worker session');
        """
    )
    con.commit()
    con.close()

    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    Indexer(settings, model=FakeModel()).index()
    engine = Engine(settings, todo_path=str(tmp_path / "TODO.md"), advisor_client=object())
    wd = Watchdog(settings, engine, notifier=Notifier(""))
    return settings, db, wd


def _add_message(db, role, content, ts, session="live1"):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session, role, content, ts),
    )
    con.commit()
    con.close()


def test_watchdog_no_session(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    settings = Settings(session_db=str(db), vector_store=str(tmp_path / "vectors"))
    wd = Watchdog(settings, Engine(settings, todo_path=str(tmp_path / "TODO.md")), notifier=Notifier(""))
    report = wd.scan()
    assert report.action == "no_session"


def test_watchdog_continue_on_ok(live_env):
    _, db, wd = live_env
    _add_message(db, "user", "haz la tarea", 1000.0)
    _add_message(db, "assistant", "tarea completada, tests pasan", 1001.0)
    report = wd.scan(now=1002.0)
    assert report.action == "continue"
    assert report.verdict.type.value == "ok"


def test_watchdog_wake_when_idle(live_env):
    _, db, wd = live_env
    _add_message(db, "assistant", "última respuesta", 1000.0)
    report = wd.scan(now=1000.0 + 60 * 10)  # 10 min idle > 3 min timeout
    assert report.action == "wake"
    assert report.idle_minutes == 10.0


def test_watchdog_failure_triggers_recall(live_env):
    _, db, wd = live_env
    _add_message(db, "assistant", "el deploy falló con error 500", 2000.0)
    report = wd.scan(now=2001.0)
    assert report.action == "retry"
    assert report.verdict.type.value == "failed"
    assert report.recall_block is not None
    assert report.recall_block.startswith("[RECALL")


def test_watchdog_uncertain_triggers_advisor(live_env):
    _, db, wd = live_env
    _add_message(db, "assistant", "No sé cómo seguir con esto", 3000.0)
    report = wd.scan(now=3001.0)
    assert report.action in ("advisor", "escalate")
    assert report.recall_block is not None


def test_watchdog_obvious_ask(live_env):
    _, db, wd = live_env
    _add_message(db, "assistant", "¿Te lo hago?", 4000.0)
    report = wd.scan(now=4001.0)
    assert report.action == "auto_answer"


def test_watchdog_sends_webhook_on_failure(live_env):
    _, db, wd = live_env
    sent = {}

    class FakeNotifier(Notifier):
        def send(self, payload):
            sent.update(payload)
            return True

    wd.notifier = FakeNotifier("")
    _add_message(db, "assistant", "error crítico en producción", 5000.0)
    wd.scan(now=5001.0)
    assert sent.get("type") == "watchdog"
    assert sent.get("action") == "retry"


def test_watchdog_consecutive_failures_escalate(live_env):
    _, db, wd = live_env
    for i, text in enumerate(["error uno", "error dos", "error tres", "error cuatro"]):
        _add_message(db, "assistant", text, 6000.0 + i)
        report = wd.scan(now=6001.0 + i)
    assert report.action == "escalate"
