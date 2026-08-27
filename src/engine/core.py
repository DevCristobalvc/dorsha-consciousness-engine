"""Engine orchestration: wires recall → judge → advisor → loop (CE-007).

Events:
    context_exhausted → ``recall()``  (RAG over session history, injected with citations)
    attempt_failed    → ``judge()``   (retry → advisor → escalate)
    uncertain_choice  → ``judge()``   (advisor → escalate)
    idle_timeout      → ``tick()``    (wake prompt / next-task prompt)

The engine is transport-agnostic: the CLI, a webhook or any agent glue can
call these methods.
"""

from __future__ import annotations

from pathlib import Path

from engine.advisor.advisor import Advisor, AdvisorAdvice, AdvisorBrief, AdvisorError
from engine.config import Settings
from engine.judge.detector import JudgeDetector, Verdict, VerdictType
from engine.judge.router import Decision, JudgeRouter
from engine.loop.worker import LoopAction, LoopWorker
from engine.recall.injector import Injector
from engine.recall.retriever import Retriever


class Engine:
    def __init__(self, settings: Settings, todo_path: str | Path | None = None, advisor_client=None):
        self.settings = settings
        self.retriever = Retriever(settings)
        self.injector = Injector(settings)
        self.detector = JudgeDetector(settings)
        self.router = JudgeRouter(settings)
        self.advisor = Advisor(settings, client=advisor_client)
        self.loop = LoopWorker(settings, todo_path or Path.cwd() / "TODO.md")

    # ---- Recall ----
    def recall(self, query: str, k: int | None = None) -> str:
        """Retrieve relevant history and build the injectable [RECALL] block."""
        chunks = self.retriever.query(query, k=k)
        return self.injector.format(chunks, query=query)

    # ---- Judge ----
    def judge(self, text: str, task_id: str = "default", tool_exit_codes: list[int] | None = None) -> Decision:
        verdict = self.detector.classify(text, task_id=task_id, tool_exit_codes=tool_exit_codes)
        return self.router.decide(verdict, task_id=task_id)

    def advise(self, brief: AdvisorBrief) -> AdvisorAdvice | None:
        """Consult the advisor; None when it fails (caller escalates)."""
        try:
            return self.advisor.consult(brief)
        except AdvisorError:
            return None

    # ---- Loop ----
    def tick(
        self,
        last_activity_ts: float | None = None,
        last_exchange: str | None = None,
        now: float | None = None,
    ) -> LoopAction:
        return self.loop.tick(last_activity_ts=last_activity_ts, last_exchange=last_exchange, now=now)

    # ---- Status ----
    def status(self) -> dict:
        vec_db = Path(self.settings.vector_store) / "recall.vec.db"
        chunks = 0
        if vec_db.exists():
            try:
                import sqlite3

                con = sqlite3.connect(vec_db)
                chunks = con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
                con.close()
            except Exception:  # noqa: BLE001
                chunks = -1
        tasks = self.loop.watcher.tasks()
        nxt = self.loop.watcher.next_task()
        return {
            "session_db": self.settings.session_db,
            "vector_store": str(vec_db),
            "chunks_indexed": chunks,
            "worker_model": self.settings.worker_model,
            "advisor_model": self.settings.advisor_model,
            "todo_tasks": len(tasks),
            "todo_next": nxt.id if nxt else None,
            "todo_blocked": [t.id for t in self.loop.watcher.blocked_tasks()],
            "thresholds": {
                "max_attempts": self.settings.judge.max_attempts,
                "idle_timeout_min": self.settings.loop.idle_timeout_min,
                "recall_top_k": self.settings.recall.top_k,
            },
        }
