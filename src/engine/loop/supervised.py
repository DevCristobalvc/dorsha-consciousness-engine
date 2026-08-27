"""Supervised loop (CE-012): the consciousness continues the worker's turn.

Flow the user asked for:

    user prompt → worker answers → consciousness reads EVERYTHING, judges,
    and injects the next instruction AS IF THE USER WROTE IT (gateway
    webhook — same path as a real user message) → worker answers again…

Repeats up to ``max_iterations`` (default 3). Then it stops and returns
control to the user — no infinite self-prompting.

On a problem the consciousness escalates internally:

    recall (session history) → advisor (DeepSeek, fresh session with the
    FULL problem context — never GPT) → inject the recommendation.

If nothing works (advisor fails / still blocked): the task is marked
``blocked`` with notes in the TODO contract, and the loop moves on.

Token budget: ``max_tokens_per_task`` (0 = unlimited) — measured from the
worker's real session (``token_count`` column).

State is persistent (``.ce/supervise_state.json``) so the cron tick can
pick up exactly where the last injection left off.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from engine.advisor.advisor import AdvisorBrief
from engine.config import Settings
from engine.core import Engine
from engine.judge.detector import VerdictType
from engine.loop.worker import Notifier

log = logging.getLogger(__name__)

STATE_PATH = Path(".ce/supervise_state.json")


@dataclass
class SupervisedTick:
    action: str  # idle_wait | injected | iterations_done | tokens_done | task_blocked | no_session
    message: str = ""
    iteration: int = 0
    max_iterations: int = 0
    meta: dict = field(default_factory=dict)


class SupervisedLoop:
    def __init__(self, settings: Settings, engine: Engine, todo_path: str | Path | None = None,
                 notifier: Notifier | None = None, state_path: str | Path | None = None):
        self.settings = settings
        self.engine = engine
        self.todo_path = Path(todo_path or Path.cwd() / "TODO.md")
        self.notifier = notifier if notifier is not None else Notifier(
            settings.loop.wake_webhook, settings.loop.wake_webhook_secret
        )
        self.state_path = Path(state_path or STATE_PATH)

    # ---- state ----
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    @property
    def active(self) -> bool:
        return bool(self._load_state().get("active"))

    def start(self, task_id: str, max_iterations: int | None = None, max_tokens: int | None = None) -> dict:
        turn = self.engine.loop.watcher.tasks()
        session = self._latest_session_id()
        state = {
            "active": True,
            "session_id": session,
            "task_id": task_id,
            "iterations_used": 0,
            "max_iterations": max_iterations or self.settings.loop.max_iterations,
            "max_tokens": max_tokens if max_tokens is not None else self.settings.loop.max_tokens_per_task,
            "tokens_used": 0,
            "last_processed_msg_id": 0,
            "last_action": "started",
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        self._save_state(state)
        log.info("supervised loop started: task=%s max_iterations=%s max_tokens=%s", task_id, state["max_iterations"], state["max_tokens"])
        return state

    def stop(self, reason: str = "manual") -> None:
        state = self._load_state()
        if state:
            state["active"] = False
            state["last_action"] = f"stopped: {reason}"
            state["updated_at"] = time.time()
            self._save_state(state)
        log.info("supervised loop stopped: %s", reason)

    def status(self) -> dict:
        state = self._load_state()
        return {
            "active": state.get("active", False),
            "task": state.get("task_id"),
            "session": state.get("session_id"),
            "iterations": f"{state.get('iterations_used', 0)}/{state.get('max_iterations', 0)}",
            "tokens_used": state.get("tokens_used", 0),
            "max_tokens": state.get("max_tokens", 0),
            "last_action": state.get("last_action"),
            "last_processed_msg_id": state.get("last_processed_msg_id"),
        }

    # ---- session helpers ----
    def _latest_session_id(self) -> str | None:
        con = sqlite3.connect(f"file:{self.settings.session_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            try:
                row = con.execute(
                    "SELECT m.session_id FROM messages m JOIN sessions s ON s.id = m.session_id "
                    "WHERE m.active = 1 AND s.archived = 0 ORDER BY m.id DESC LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        finally:
            con.close()
        return row["session_id"] if row else None

    def _latest_assistant_turn(self, session_id: str) -> tuple[int, str, float] | None:
        """(message_id, content, timestamp) of the newest assistant reply in the session."""
        con = sqlite3.connect(f"file:{self.settings.session_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT id, content, timestamp FROM messages "
                "WHERE session_id = ? AND role = 'assistant' AND active = 1 "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return row["id"], row["content"] or "", float(row["timestamp"])

    def _session_tokens(self, session_id: str) -> int:
        con = sqlite3.connect(f"file:{self.settings.session_db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(token_count), 0) FROM messages WHERE session_id = ? AND active = 1",
                (session_id,),
            ).fetchone()
        finally:
            con.close()
        return int(row[0]) if row else 0

    # ---- the tick ----
    def tick(self) -> SupervisedTick:
        state = self._load_state()
        if not state.get("active"):
            return SupervisedTick("idle_wait", "supervised loop not active")

        session = state.get("session_id") or self._latest_session_id()
        if not session:
            return SupervisedTick("no_session", "no active session")

        # token budget
        max_tokens = int(state.get("max_tokens") or 0)
        if max_tokens > 0:
            used = self._session_tokens(session)
            state["tokens_used"] = used
            if used > max_tokens:
                self.stop(f"token budget exceeded ({used} > {max_tokens})")
                return SupervisedTick(
                    "tokens_done", f"token budget exceeded: {used}/{max_tokens} — task left pending, control back to user",
                    iteration=state.get("iterations_used", 0), max_iterations=state.get("max_iterations", 0),
                    meta={"tokens_used": used, "max_tokens": max_tokens},
                )

        # new worker turn?
        turn = self._latest_assistant_turn(session)
        if turn is None or turn[0] <= int(state.get("last_processed_msg_id", 0)):
            return SupervisedTick("idle_wait", "waiting for the worker's next turn", meta={"session": session})

        msg_id, content, ts = turn
        state["last_processed_msg_id"] = msg_id

        # iterations budget
        iterations_used = int(state.get("iterations_used", 0))
        max_iterations = int(state.get("max_iterations", 0))
        if iterations_used >= max_iterations:
            self.stop("iterations exhausted")
            return SupervisedTick(
                "iterations_done",
                f"iterations exhausted ({iterations_used}/{max_iterations}) — returning control to the user",
                iteration=iterations_used, max_iterations=max_iterations,
                meta={"session": session, "task": state.get("task_id")},
            )

        # judge the worker's turn
        verdict = self.engine.detector.classify(content, task_id=state.get("task_id", "default"))
        iterations_used += 1
        state["iterations_used"] = iterations_used

        if verdict.type is VerdictType.OK:
            msg = self._continue_message(state, content)
        elif verdict.type is VerdictType.OBVIOUS_ASK:
            msg = (f"[conciencia] Respuesta obvia (auto): sí, continúa y ejecuta. "
                   f"Tarea: {state.get('task_id')}. {verdict.suggestion}")
        else:
            # failed / uncertain → recall + advisor (DeepSeek, fresh session, full context)
            recall = self.engine.recall(content, k=self.settings.recall.top_k)
            advice = self.engine.advise(AdvisorBrief(
                problem=content[:2500],
                attempts=[f"worker turn judged {verdict.type.value} (attempts={verdict.attempts})"],
                hypothesis=verdict.suggestion or "worker blocked on this task",
                evidence=recall[:2000],
            ))
            if advice is None:
                self._mark_task_blocked(state, verdict, content)
                self.stop("advisor failed / task blocked")
                return SupervisedTick(
                    "task_blocked",
                    f"advisor unavailable — task {state.get('task_id')} marked blocked with notes; moving on",
                    iteration=iterations_used, max_iterations=max_iterations,
                )
            msg = (f"[conciencia] Detecté {verdict.type.value}: {verdict.evidence}. "
                   f"Recomendación del asesor (DeepSeek): {advice.recommendation}. "
                   f"Alternativas: {' | '.join(advice.alternatives[:3])}. Reintenta con este enfoque.")

        # inject as if the user wrote it → the worker keeps going
        ok = self.notifier.send({
            "message": msg,
            "source": "consciousness",
            "chat_id": "1680839317",
        })
        state["last_action"] = f"injected #{iterations_used}" if ok else "inject_failed"
        state["updated_at"] = time.time()
        self._save_state(state)
        return SupervisedTick(
            "injected" if ok else "inject_failed",
            msg[:200],
            iteration=iterations_used, max_iterations=max_iterations,
            meta={"session": session, "msg_id": msg_id},
        )

    # ---- helpers ----
    def _continue_message(self, state: dict, last_reply: str) -> str:
        task = state.get("task_id", "")
        summary = last_reply[:400].replace("\n", " ")
        return (
            f"[conciencia] El worker terminó su turno. Sigue trabajando en {task} "
            f"(iteración {state.get('iterations_used')}/{state.get('max_iterations')}). "
            f"Último estado: \"{summary}\". Continúa per los criterios de aceptación del TODO; "
            f"cuando termines marca la tarea completed y reporta el resultado."
        )

    def _mark_task_blocked(self, state: dict, verdict, content: str) -> None:
        """Write an honest blocked + notes entry for the current task in the TODO contract."""
        try:
            text = self.todo_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.warning("TODO not found at %s", self.todo_path)
            return
        task_id = state.get("task_id", "")
        note = (
            f"blocked — [conciencia] iteraciones agotadas tras {verdict.type.value}: "
            f"{verdict.evidence}. Último turno: {content[:200]}. Advisor no disponible."
        )
        if task_id and task_id in text:
            import re

            text = re.sub(
                rf"(## TASK: {re.escape(task_id)}.*?)(### Status[^\n]*\n)(- [^\n]*)",
                lambda m: m.group(1) + m.group(2) + "- " + note,
                text, flags=re.DOTALL,
            )
        else:
            text += f"\n## TASK: {task_id or 'BLOCKED'} — bloqueada por la conciencia\n\n### Description\n{note}\n\n### Status\n- blocked\n"
        self.todo_path.write_text(text, encoding="utf-8")
        log.info("task %s marked blocked in %s", task_id, self.todo_path)
