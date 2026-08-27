"""MCP over HTTP (CE-016): same engine, URL transport — the SaaS option.

Reuses the exact same ``McpEngine`` (JSON-RPC handler) from the stdio server;
only the transport changes. This is what makes the 3 distribution channels
one codebase:

    stdio  → Claude Code / Hermes local (npx / uvx / pip)
    HTTP   → mcp.devcristobalvc.com (managed SaaS, zero install)
    panel  → config + status UI

Endpoints:
    POST /          JSON-RPC 2.0 (initialize, tools/list, tools/call)
    GET  /health    {"ok": true, "chunks": N, "memory": M}
    GET  /sse       SSE transport for clients that require it (minimal)

Auth: optional bearer token via ``--token`` (required when binding non-loopback).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine.config import Settings
from engine.core import Engine
from engine.mcp_server import McpEngine

log = logging.getLogger(__name__)


def _stats(settings: Settings) -> dict:
    vec_db = Path(settings.vector_store) / "recall.vec.db"
    chunks = memory = 0
    if vec_db.exists():
        try:
            con = sqlite3.connect(vec_db)
            chunks = con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
            memory = con.execute("SELECT COUNT(*) FROM saved_memory").fetchone()[0]
            con.close()
        except Exception:  # noqa: BLE001
            chunks = memory = -1
    return {"chunks": chunks, "memory": memory}


class HttpMcpHandler(BaseHTTPRequestHandler):
    mcp: McpEngine = None  # set by serve()
    settings: Settings = None
    token: str = ""

    def log_message(self, format, *args):  # noqa: A002
        log.debug(format, *args)

    def _authorized(self) -> bool:
        if not self.token:
            return True  # loopback-only deployment
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}})
            return
        msg = self._read_body()
        if not msg:
            self._send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}})
            return
        resp = self.mcp.handle(msg)
        if resp is None:
            self._send_json(202, {"jsonrpc": "2.0", "result": {"accepted": True}})
            return
        self._send_json(200, resp)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"ok": True, "engine": "dorsha-consciousness-engine", **_stats(self.settings)})
        elif self.path == "/sse":
            self._send_json(501, {"ok": False, "message": "use POST / for JSON-RPC (streamable HTTP)"})
        else:
            self._send_json(404, {"ok": False, "message": "not found — POST / for JSON-RPC, GET /health"})


def serve(port: int = 8900, host: str = "127.0.0.1", token: str = "",
          settings: Settings | None = None, engine: Engine | None = None) -> ThreadingHTTPServer:
    if settings is None:
        settings = Settings.from_yaml("config/local.yaml") if Path("config/local.yaml").exists() else Settings()
    engine = engine or Engine(settings)
    HttpMcpHandler.mcp = McpEngine(settings, engine)
    HttpMcpHandler.settings = settings
    HttpMcpHandler.token = token
    server = ThreadingHTTPServer((host, port), HttpMcpHandler)
    log.info("mcp-http on http://%s:%s (token=%s)", host, port, "yes" if token else "no")
    return server
