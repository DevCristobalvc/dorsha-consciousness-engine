"""Tests for CE-006: TODO watcher, idle detector and loop worker."""

from pathlib import Path

import pytest

from engine.config import Settings
from engine.loop.idle import IdleDetector
from engine.loop.todowatcher import TodoWatcher
from engine.loop.worker import LoopWorker, Notifier


@pytest.fixture
def todo_file(tmp_path):
    p = tmp_path / "TODO.md"
    p.write_text(
        """# TODO

## TASK: T1 — First task

### Description
Do the thing.

### Status
- in_progress

---

## TASK: T2 — Second task

### Description
Do the other thing.

### Status
- pending

---

## TASK: T3 — Stuck task

### Description
Needs external dependency.

### Status
- blocked — waiting for API key

---

## TASK: T4 — Finished task

### Description
Already done.

### Status
- completed
""",
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def settings():
    return Settings()


def test_watcher_parses_tasks(todo_file):
    tasks = TodoWatcher(todo_file).tasks()
    assert [t.id for t in tasks] == ["T1", "T2", "T3", "T4"]
    assert tasks[0].status == "in_progress"
    assert tasks[1].status == "pending"
    assert tasks[2].status == "blocked"
    assert tasks[3].status == "completed"


def test_watcher_next_task_prefers_in_progress(todo_file):
    w = TodoWatcher(todo_file)
    assert w.next_task().id == "T1"


def test_watcher_progress_table_fallback(tmp_path):
    p = tmp_path / "TODO.md"
    p.write_text(
        """## TASK: CE-001 — Scaffold

### Description
x

## Progress

| Task | Status |
|---|---|
| CE-001 Scaffold | **DONE** — verified |
""",
        encoding="utf-8",
    )
    t = TodoWatcher(p).tasks()[0]
    assert t.status == "completed"


def test_idle_detector(settings):
    d = IdleDetector(settings)
    assert d.is_idle(None) is True
    assert d.is_idle(1000.0, now=1000.0) is False
    assert d.is_idle(1000.0, now=1000.0 + 60 * 3 + 1) is True  # over 3 min
    assert d.idle_for_minutes(1000.0, now=1000.0 + 60 * 5) == 5.0


def test_worker_wake_when_idle(settings, todo_file):
    w = LoopWorker(settings, todo_file, notifier=Notifier(""))
    action = w.tick(last_activity_ts=0.0, now=10000.0, last_exchange="user: sigue")
    assert action.action == "wake"
    assert action.task.id == "T1"
    assert "T1" in action.message
    assert "Last exchange" in action.message


def test_worker_next_task_when_active(settings, todo_file):
    w = LoopWorker(settings, todo_file, notifier=Notifier(""))
    action = w.tick(last_activity_ts=10000.0, now=10000.0)
    assert action.action == "next_task"
    assert action.task.id == "T1"


def test_worker_notifies_via_webhook(settings, todo_file, monkeypatch):
    sent = {}

    class FakeNotifier(Notifier):
        def send(self, payload):
            sent.update(payload)
            return True

    w = LoopWorker(settings, todo_file, notifier=FakeNotifier(""))
    action = w.tick(last_activity_ts=0.0, now=10000.0)
    assert action.action == "wake"
    assert sent.get("type") == "wake"
    assert sent.get("task") == "T1"


def test_notifier_signs_body_with_hmac(monkeypatch):
    import hashlib
    import hmac

    captured = {}

    class FakeResp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout):
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    n = Notifier("http://x/webhook", webhook_secret="sekret")
    assert n.send({"message": "wake up"}) is True
    expected = hmac.new(b"sekret", captured["body"], hashlib.sha256).hexdigest()
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers_lower.get("x-webhook-signature") == expected


def test_notifier_silent_without_url():
    assert Notifier("").send({"message": "x"}) is False


def test_worker_all_done(settings, tmp_path):
    p = tmp_path / "TODO.md"
    p.write_text("## TASK: T1 — done\n\n### Status\n- completed\n", encoding="utf-8")
    w = LoopWorker(settings, p, notifier=Notifier(""))
    action = w.tick(last_activity_ts=0.0, now=10000.0)
    assert action.action == "all_done"


def test_worker_blocked_skip(settings, tmp_path):
    p = tmp_path / "TODO.md"
    p.write_text("## TASK: T1 — stuck\n\n### Status\n- blocked — needs human\n", encoding="utf-8")
    w = LoopWorker(settings, p, notifier=Notifier(""))
    action = w.tick(last_activity_ts=0.0, now=10000.0)
    assert action.action == "blocked_skip"
    assert "T1" in action.meta["blocked"]
