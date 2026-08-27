"""Injector: formats retrieved chunks into a context block with citations.

Output shape (CE-003):

    [RECALL — retrieved from session history]
    (session:<id> msg:<id> @ <iso-timestamp>) <role>: "<text>"
    ...

Bounded by ``max_chars`` (default from settings) so the injected block never
blows the worker's context.
"""

from __future__ import annotations

from datetime import datetime, timezone

from engine.config import Settings
from engine.recall.retriever import RetrievedChunk


class Injector:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _fmt_ts(ts: float) -> str:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            return "unknown"

    def format(
        self,
        chunks: list[RetrievedChunk],
        query: str | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Build the injectable recall block, truncated to ``max_chars``."""
        max_chars = max_chars or self.settings.recall.max_chars
        lines = ["[RECALL — retrieved from session history]"]
        if query:
            lines.append(f"# query: {query}")
        lines.append("")
        for c in chunks:
            lines.append(f'({c.session_id} msg:{c.message_id} @ {self._fmt_ts(c.timestamp)}) {c.role}: "{c.text}"')

        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[: max_chars - 80] + "\n… [recall truncated to fit context]"
        return block
