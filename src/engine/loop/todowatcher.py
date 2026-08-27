"""TODO watcher: parses the work-contract file and tracks task status.

Supports two status sources, in order of preference:
1. ``### Status`` field inside each ``## TASK:`` block (canonical contract)
2. The ``## Progress`` table (``| CE-XXX ... | <status> |``)

Status normalization: completed/DONE/✅ → completed; in_progress → in_progress;
blocked → blocked; anything else → pending.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TASK_RE = re.compile(r"^## TASK:\s*(?P<id>\S+)\s*(?:—|-)?\s*(?P<title>.*)$", re.MULTILINE)
STATUS_RE = re.compile(r"^### Status\s*$", re.MULTILINE)
STATUS_VAL_RE = re.compile(r"(?i)(completed|done|in_progress|in progress|blocked|pending)")
PROGRESS_TABLE_RE = re.compile(r"^\|\s*(CE-\d+|[A-Z0-9-]+)\s+[^|]*\|\s*([^|]+?)\s*\|", re.MULTILINE)


@dataclass
class Task:
    id: str
    title: str
    status: str  # completed | in_progress | blocked | pending
    raw: str


class TodoWatcher:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @staticmethod
    def _normalize(status: str) -> str:
        s = status.lower()
        if any(k in s for k in ("completed", "done")):
            return "completed"
        if "in_progress" in s or "in progress" in s:
            return "in_progress"
        if "blocked" in s:
            return "blocked"
        return "pending"

    def _status_from_block(self, block: str) -> str | None:
        m = STATUS_RE.search(block)
        if not m:
            return None
        tail = block[m.end() : block.find("\n##", m.end())]
        m2 = STATUS_VAL_RE.search(tail)
        return self._normalize(m2.group(1)) if m2 else "pending"

    def tasks(self) -> list[Task]:
        text = self.path.read_text(encoding="utf-8")
        matches = list(TASK_RE.finditer(text))
        # fallback table statuses (id → status)
        table: dict[str, str] = {}
        for m in PROGRESS_TABLE_RE.finditer(text):
            table[m.group(1).strip()] = m.group(2).strip()

        tasks: list[Task] = []
        for i, m in enumerate(matches):
            block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[m.start() : block_end]
            status = self._status_from_block(block) or self._normalize(table.get(m.group("id"), "pending"))
            tasks.append(Task(id=m.group("id"), title=m.group("title").strip(), status=status, raw=block))
        return tasks

    def next_task(self) -> Task | None:
        """First non-completed task (in_progress first, then pending)."""
        tasks = self.tasks()
        for t in tasks:
            if t.status == "in_progress":
                return t
        for t in tasks:
            if t.status == "pending":
                return t
        return None

    def blocked_tasks(self) -> list[Task]:
        return [t for t in self.tasks() if t.status == "blocked"]

    def all_done(self) -> bool:
        return self.next_task() is None
