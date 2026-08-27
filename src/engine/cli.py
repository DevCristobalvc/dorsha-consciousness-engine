"""ce — Dorsha Consciousness Engine CLI (CE-007).

Commands:
    ce status               engine state (chunks, models, TODO)
    ce recall <query>       RAG recall with citations (--k N)
    ce judge <turn>         classify a turn + decision (--task, --exit-codes)
    ce index [--limit N]    incremental embedding index
    ce loop on|off|status   loop driver control
"""

from __future__ import annotations

import argparse
import sys
import time

from engine.config import Settings
from engine.core import Engine


def _engine(args) -> Engine:
    settings = Settings.from_yaml(args.config) if args.config else Settings()
    return Engine(settings, todo_path=args.todo)


def cmd_status(args) -> None:
    st = _engine(args).status()
    for k, v in st.items():
        print(f"{k}: {v}")


def cmd_recall(args) -> None:
    print(_engine(args).recall(args.query, k=args.k))


def cmd_save(args) -> None:
    mem_id = _engine(args).memory_save(args.text, tags=args.tags, source=args.source)
    print(f"guardado: memoria #{mem_id} — el RAG ahora la recuerda con prioridad")


def cmd_memory(args) -> None:
    items = _engine(args).memory_list(limit=args.limit)
    if not items:
        print("memoria vacía — usa: ce save 'texto' --tags x")
        return
    for m in items:
        import datetime

        when = datetime.datetime.fromtimestamp(m["created_at"]).strftime("%Y-%m-%d %H:%M")
        print(f"[{m['id']}] {when} ({m['source']}{' | ' + m['tags'] if m['tags'] else ''})")
        print(f"    {m['text'][:160]}")
    print(f"\n{len(items)} entradas de memoria curada")


def cmd_judge(args) -> None:
    codes = [int(x) for x in args.exit_codes.split(",")] if args.exit_codes else None
    d = _engine(args).judge(args.turn, task_id=args.task, tool_exit_codes=codes)
    print(f"action: {d.action} | reason: {d.reason} | attempts: {d.attempts}")


def cmd_index(args) -> None:
    from engine.recall.indexer import Indexer

    print(Indexer(_engine(args).settings).index(limit=args.limit))


def cmd_loop(args) -> None:
    e = _engine(args)
    if args.loop_cmd == "status":
        st = e.status()
        print(f"next: {st['todo_next']} | blocked: {st['todo_blocked']} | chunks: {st['chunks_indexed']}")
        return
    if args.loop_cmd == "off":
        print("loop off (no persistent daemon — schedule with cron/systemd)")
        return
    interval = args.interval or float(e.settings.loop.tick_interval_min)
    print(f"loop on — tick every {interval} min. Ctrl+C to stop.")
    try:
        while True:
            action = e.tick()
            print(f"[{time.strftime('%H:%M:%S')}] {action.action} | {action.message[:90]}")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print("\nloop stopped")


def cmd_watch(args) -> None:
    from engine.watchdog import Watchdog

    wd = Watchdog(_engine(args).settings, _engine(args))
    report = wd.watch(interval_sec=args.interval, once=args.once)
    if args.once and report is not None:
        print(f"session: {report.session_id} | action: {report.action} | idle_min: {report.idle_minutes:.0f}")
        if report.verdict is not None:
            print(f"verdict: {report.verdict.type.value} | attempts: {report.verdict.attempts} | evidence: {report.verdict.evidence}")
        if report.recall_block:
            print(f"\n--- recall inyectado ---\n{report.recall_block[:600]}")
        if report.advice is not None:
            print(f"\n--- advisor ---\nrecommendation: {report.advice.recommendation} | confidence: {report.advice.confidence}")
            for a in report.advice.alternatives:
                print(f"  - {a}")


def cmd_supervise(args) -> None:
    from engine.loop.supervised import SupervisedLoop

    loop = SupervisedLoop(_engine(args).settings, _engine(args), todo_path=args.todo)
    if args.super_cmd == "on":
        state = loop.start(args.task or "default", max_iterations=args.max_iterations, max_tokens=args.max_tokens)
        print(f"supervised loop ON — task={state['task_id']} max_iterations={state['max_iterations']} max_tokens={state['max_tokens']} session={state['session_id']}")
    elif args.super_cmd == "off":
        loop.stop(args.reason or "manual")
        print("supervised loop OFF")
    elif args.super_cmd == "status":
        st = loop.status()
        for k, v in st.items():
            print(f"{k}: {v}")
    elif args.super_cmd == "tick":
        t = loop.tick()
        print(f"action: {t.action} | iteration: {t.iteration}/{t.max_iterations}")
        if t.message:
            print(t.message[:400])


def cmd_panel(args) -> None:
    from engine.panel import serve

    server = serve(port=args.port, todo_path=args.todo)
    print(f"panel: http://127.0.0.1:{args.port} (loopback) — Ctrl+C para detener")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\npanel detenido")


def cmd_mcp(args) -> None:
    from engine.mcp_server import serve_stdio

    sys.exit(serve_stdio())


def cmd_mcp_http(args) -> None:
    from engine.mcp_http import serve

    server = serve(port=args.port, host=args.host, token=args.token or "")
    print(f"mcp-http: http://{args.host}:{args.port} (JSON-RPC POST /) — Ctrl+C para detener")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nmcp-http detenido")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ce", description="Dorsha Consciousness Engine")
    p.add_argument("--config", default="config/local.yaml", help="YAML config path (empty string = defaults)")
    p.add_argument("--todo", default="TODO.md")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    r = sub.add_parser("recall")
    r.add_argument("query")
    r.add_argument("--k", type=int, default=None)

    sv = sub.add_parser("save")
    sv.add_argument("text")
    sv.add_argument("--tags", default="")
    sv.add_argument("--source", default="agent")

    mem = sub.add_parser("memory")
    mem.add_argument("--limit", type=int, default=50)

    j = sub.add_parser("judge")
    j.add_argument("turn")
    j.add_argument("--task", default="default")
    j.add_argument("--exit-codes", default=None)

    i = sub.add_parser("index")
    i.add_argument("--limit", type=int, default=0)

    l = sub.add_parser("loop")
    l.add_argument("loop_cmd", choices=["on", "off", "status"])
    l.add_argument("--interval", type=float, default=None)

    w = sub.add_parser("watch")
    w.add_argument("--once", action="store_true", help="single scan, then exit")
    w.add_argument("--interval", type=float, default=60.0, help="tick seconds (default 60)")

    s = sub.add_parser("supervise")
    s.add_argument("super_cmd", choices=["on", "off", "status", "tick"])
    s.add_argument("--task", default="default")
    s.add_argument("--max-iterations", type=int, default=None)
    s.add_argument("--max-tokens", type=int, default=None)
    s.add_argument("--reason", default=None)

    pnl = sub.add_parser("panel")
    pnl.add_argument("--port", type=int, default=8899, help="loopback port (default 8899)")

    mcp = sub.add_parser("mcp", help="MCP stdio server — expose RAG/judge as agent tools")

    mh = sub.add_parser("mcp-http", help="MCP over HTTP — the SaaS option (JSON-RPC POST /)")
    mh.add_argument("--port", type=int, default=8900)
    mh.add_argument("--host", default="127.0.0.1", help="0.0.0.0 solo con --token")
    mh.add_argument("--token", default="", help="Bearer token (obligatorio si host no es loopback)")

    args = p.parse_args(argv)

    handlers = {
        "status": cmd_status,
        "recall": cmd_recall,
        "save": cmd_save,
        "memory": cmd_memory,
        "judge": cmd_judge,
        "index": cmd_index,
        "loop": cmd_loop,
        "watch": cmd_watch,
        "supervise": cmd_supervise,
        "panel": cmd_panel,
        "mcp": cmd_mcp,
        "mcp-http": cmd_mcp_http,
    }
    handlers[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
