"""MCP server (CE-015): expose the engine as search/save tools.

The RAG works as a *tool* the agent calls — like ``web_search``, not passive
injection. Any MCP-capable agent (Claude Code, Hermes, Codex with MCP, Gemini
CLI with MCP) connects and gets:

    ce_memory_search   query the selective RAG (history + curated memory)
    ce_memory_save     persist a memory entry
    ce_judge           classify a worker turn + get a decision
    ce_status          engine state

Transport: stdio with LSP-style framing (Content-Length header), JSON-RPC 2.0.
Zero dependencies — the protocol is implemented directly.
"""

from __future__ import annotations

import json
import sys

from engine.config import Settings
from engine.core import Engine

TOOLS = [
    {
        "name": "ce_memory_search",
        "description": (
            "Recall from the agent's selective memory: user prompts, agent replies "
            "and explicitly saved memory. Returns chunks with provenance citations "
            "(session/message id + timestamp). Use it when you need to remember a "
            "past decision, instruction or lesson that escapes your context window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "what to recall"},
                "k": {"type": "integer", "description": "number of results (default from config)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ce_memory_save",
        "description": (
            "Persist a memory entry the agent chose to keep (decision, lesson, fact). "
            "Saved memory is weighted higher at retrieval. Call this when a task is "
            "done or you learned something worth remembering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "the memory content"},
                "tags": {"type": "string", "description": "comma-separated tags, e.g. 'deploy,vercel'"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "ce_judge",
        "description": (
            "Classify a worker turn (ok | failed | uncertain | obvious_ask) and get "
            "the decision path (retry | advisor | escalate | auto_answer). Use it when "
            "you hit an error or don't know how to continue."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "the turn to classify"},
                "task_id": {"type": "string", "description": "task identifier for attempt counting"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "ce_status",
        "description": "Engine state: indexed chunks, current task, supervision status, models.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpEngine:
    """Handles one JSON-RPC message; returns the response (None for notifications)."""

    def __init__(self, settings: Settings | None = None, engine: Engine | None = None):
        self.settings = settings or Settings()
        self.engine = engine or Engine(self.settings)

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dorsha-consciousness-engine", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None  # notification
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            return self._call(msg_id, params)

        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    def _call(self, msg_id, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            if name == "ce_memory_search":
                chunks = self.engine.retriever.query(args.get("query", ""), k=args.get("k"))
                text = json.dumps(
                    [
                        {
                            "text": c.text,
                            "session_id": c.session_id,
                            "message_id": c.message_id,
                            "timestamp": c.timestamp,
                            "score": round(c.score, 4),
                        }
                        for c in chunks
                    ],
                    ensure_ascii=False, indent=2,
                )
            elif name == "ce_memory_save":
                mem_id = self.engine.memory_save(args.get("text", ""), tags=args.get("tags", ""), source="mcp")
                text = json.dumps({"saved_id": mem_id}, ensure_ascii=False)
            elif name == "ce_judge":
                d = self.engine.judge(args.get("text", ""), task_id=args.get("task_id", "mcp"))
                text = json.dumps({"action": d.action, "reason": d.reason, "attempts": d.attempts}, ensure_ascii=False)
            elif name == "ce_status":
                text = json.dumps(self.engine.status(), ensure_ascii=False, default=str)
            else:
                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"},
                }
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"isError": True, "content": [{"type": "text", "text": f"error: {exc}"}]}}


# ---- stdio transport (LSP-style framing) ----

def _read_message(stream) -> dict | None:
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get(b"content-length", 0))
    if length <= 0:
        return None
    body = stream.read(length)
    return json.loads(body)


def _write_message(stream, msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode())
    stream.write(body)
    stream.flush()


def serve_stdio(settings: Settings | None = None, engine: Engine | None = None) -> int:
    """Run the MCP server on stdin/stdout until EOF. Returns exit code."""
    mcp = McpEngine(settings, engine)
    while True:
        try:
            msg = _read_message(sys.stdin.buffer)
        except (json.JSONDecodeError, ValueError):
            continue
        if msg is None:
            return 0
        resp = mcp.handle(msg)
        if resp is not None:
            _write_message(sys.stdout.buffer, resp)
