"""Watchdog: continuous supervision of a live worker session (CE-009).

This is what turns the engine from a library into a living consciousness:
it polls the worker's session DB, classifies recent turns with the Judge and
acts on its own — no human prompt required.

Signals → actions:

    repeated failures  → recall problem context + advisor + alert
    uncertain turn     → recall + advisor
    idle worker        → wake prompt (+ webhook)
    obvious_ask        → auto_answer note (no human)

The watchdog is framework-agnostic: it only needs read access to the worker's
session database (schema adapter: ``latest_turn``) and the engine's own pieces.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from engine.advisor.advisor import AdvisorAdvice, AdvisorBrief
from engine.config import Settings
from engine.core import Engine
from engine.judge.detector import Verdict, VerdictType
from engine.judge.router import Decision
from engine.loop.worker import Notifier

log = logging.getLogger(__name__)


@dataclass
class WatchdogReport:
    session_id: str | None
    last_message_id: int | None
    idle_minutes: float
    action: str  # wake | retry | advisor | escalate | auto_answer | continue | no_session | recall_advise
    verdict: Verdict | None = None
    decision: Decision | None = None
    recall_block: str | None = None
    advice: AdvisorAdvice | None = None
    message: str = ""
    meta: dict = field(default_factory=dict)


class Watchdog:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        session_db: str | None = None,
        notifier: Notifier | None = None,
    ):
        self.settings = settings
        self.engine = engine
        self.session_db = session_db or settings.session_db
        self.notifier = notifier if notifier is not None else Notifier(settings.loop.wake_webhook)

    # ---- schema adapter ----
    def latest_turn(self) -> tuple[int, str, str, str, float] | None:
        """Return (message_id, session_id, role, content, timestamp) of the
        newest non-tool message in the worker's session DB."""
        con = sqlite3.connect(f"file:{self.session_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            try:
                row = con.execute(
                    """
                    SELECT m.id, m.session_id, m.role, m.content, m.timestamp
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.active = 1 AND s.archived = 0 AND m.role IN ('user','assistant')
                    ORDER BY m.id DESC LIMIT 1
                    """
                ).fetchone()
            except sqlite3.OperationalError:
                row = None  # schema not present yet — no session
        finally:
            con.close()
        if row is None:
            return None
        return row["id"], row["session_id"], row["role"], row["content"] or "", float(row["timestamp"])

    def _latest_activity(self) -> float | None:
        con = sqlite3.connect(f"file:{self.session_db}?mode=ro", uri=True)
        try:
            try:
                row = con.execute(
                    "SELECT MAX(timestamp) FROM messages WHERE active = 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        finally:
            con.close()
        return row[0] if row and row[0] is not None else None

    # ---- scan ----
    def scan(self, now: float | None = None) -> WatchdogReport:
        now = now if now is not None else time.time()
        turn = self.latest_turn()
        last_activity = self._latest_activity()

        if turn is None:
            return WatchdogReport(None, None, 0.0, "no_session", message="no active session found")

        msg_id, session_id, role, content, ts = turn
        idle_min = max((now - last_activity) / 60.0, 0.0) if last_activity else 0.0

        # 1. idle check
        if idle_min > float(self.settings.loop.idle_timeout_min):
            msg = f"worker idle for {idle_min:.0f} min — wake"
            report = WatchdogReport(
                session_id, msg_id, idle_min, "wake", message=msg,
                meta={"idle_minutes": round(idle_min, 1)},
            )
            self.notifier.send({"type": "wake", "session": session_id, "message": msg})
            return report

        # 2. classify the latest turn
        verdict = self.engine.detector.classify(content, task_id=session_id)
        decision = self.engine.router.decide(verdict, task_id=session_id)

        if verdict.type is VerdictType.OK:
            return WatchdogReport(session_id, msg_id, idle_min, "continue", verdict=verdict, decision=decision)

        if verdict.type is VerdictType.OBVIOUS_ASK:
            return WatchdogReport(
                session_id, msg_id, idle_min, "auto_answer", verdict=verdict, decision=decision,
                message=f"auto_answer: {content[:120]}",
            )

        # failed or uncertain → consciousness kicks in: recall + advisor
        recall_block = self.engine.recall(content, k=self.settings.recall.top_k)

        advice = None
        if decision.action in ("advisor", "escalate"):
            brief = AdvisorBrief(
                problem=content[:2000],
                attempts=[f"consecutive failures: {verdict.attempts}"],
                hypothesis="worker blocked on this task",
                evidence=recall_block[:1500],
            )
            advice = self.engine.advise(brief)

        action = decision.action if decision.action != "escalate" else "escalate"
        message = (
            f"[{session_id} msg:{msg_id}] {action} — verdict={verdict.type.value} "
            f"attempts={verdict.attempts}"
        )
        self.notifier.send({"type": "watchdog", "session": session_id, "action": action, "message": message})
        return WatchdogReport(
            session_id, msg_id, idle_min, action,
            verdict=verdict, decision=decision,
            recall_block=recall_block, advice=advice, message=message,
        )

    # ---- loop ----
    def watch(self, interval_sec: float = 60.0, once: bool = False):
        if once:
            return self.scan()
        log.info("watchdog on — tick every %ss. Ctrl+C to stop.", interval_sec)
        try:
            while True:
                report = self.scan()
                if report.action not in ("continue", "no_session"):
                    log.info("action=%s session=%s msg=%s", report.action, report.session_id, report.message[:120])
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            log.info("watchdog stopped")
