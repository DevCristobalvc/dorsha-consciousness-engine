"""Loop worker: the pulse that keeps the agent moving.

On each tick:
1. Read the TODO contract and find the active task (in_progress > pending)
2. If the worker is idle → produce a wake message (last exchange + TODO state)
3. If the current task is blocked → skip it, surface it for the human
4. Otherwise → produce the next-action prompt for the worker

The notifier (webhook) is optional and injectable; without one the worker
just returns the action (CLI/stdin mode).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from engine.config import Settings
from engine.loop.idle import IdleDetector
from engine.loop.todowatcher import Task, TodoWatcher

log = logging.getLogger(__name__)


@dataclass
class LoopAction:
    action: str  # wake | next_task | blocked_skip | all_done | idle_ok
    message: str = ""
    task: Task | None = None
    meta: dict = field(default_factory=dict)


class Notifier:
    """Optional webhook delivery for wake prompts.

    Signs the body with HMAC-SHA256 (``X-Webhook-Signature`` header) when a
    secret is configured — the Hermes gateway webhook expects this.
    """

    def __init__(self, webhook_url: str = "", webhook_secret: str = ""):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret

    def send(self, payload: dict) -> bool:
        if not self.webhook_url:
            return False
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.webhook_secret:
            import hashlib
            import hmac

            sig = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = sig
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook delivery failed: %s", exc)
            return False


class LoopWorker:
    def __init__(
        self,
        settings: Settings,
        todo_path: str | Path,
        idle_detector: IdleDetector | None = None,
        notifier: Notifier | None = None,
    ):
        self.settings = settings
        self.watcher = TodoWatcher(todo_path)
        self.idle = idle_detector or IdleDetector(settings)
        self.notifier = notifier if notifier is not None else Notifier(
            settings.loop.wake_webhook, settings.loop.wake_webhook_secret
        )

    def _build_prompt(self, task: Task, last_exchange: str | None) -> str:
        parts = [f"Engine tick — active task: {task.id} ({task.title})"]
        if last_exchange:
            parts.append(f"Last exchange:\n{last_exchange[:2000]}")
        parts.append(f"Next action: execute {task.id} per its acceptance criteria. Status contract: mark "
                     f"completed only when tests pass; on a real limitation mark blocked with a comment and move on.")
        return "\n\n".join(parts)

    def tick(
        self,
        last_activity_ts: float | None = None,
        last_exchange: str | None = None,
        now: float | None = None,
    ) -> LoopAction:
        task = self.watcher.next_task()
        if task is None:
            blocked = self.watcher.blocked_tasks()
            if blocked:
                ids = ", ".join(t.id for t in blocked)
                return LoopAction("blocked_skip", f"All tasks done except blocked: {ids}. Awaiting human resolution.", meta={"blocked": [t.id for t in blocked]})
            return LoopAction("all_done", "No pending tasks — cycle complete. Human review only.", meta={"done": True})

        idle = self.idle.is_idle(last_activity_ts, now)
        if idle:
            msg = self._build_prompt(task, last_exchange)
            if self.notifier.send({"type": "wake", "task": task.id, "message": msg}):
                log.info("wake sent for %s", task.id)
            return LoopAction("wake", msg, task=task, meta={"idle_min": self.idle.idle_for_minutes(last_activity_ts, now)})

        return LoopAction("next_task", self._build_prompt(task, last_exchange), task=task)
