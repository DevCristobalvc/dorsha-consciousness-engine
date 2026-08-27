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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ce", description="Dorsha Consciousness Engine")
    p.add_argument("--config", default="config/local.yaml", help="YAML config path (empty string = defaults)")
    p.add_argument("--todo", default="TODO.md")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    r = sub.add_parser("recall")
    r.add_argument("query")
    r.add_argument("--k", type=int, default=None)

    j = sub.add_parser("judge")
    j.add_argument("turn")
    j.add_argument("--task", default="default")
    j.add_argument("--exit-codes", default=None)

    i = sub.add_parser("index")
    i.add_argument("--limit", type=int, default=0)

    l = sub.add_parser("loop")
    l.add_argument("loop_cmd", choices=["on", "off", "status"])
    l.add_argument("--interval", type=float, default=None)

    args = p.parse_args(argv)

    handlers = {
        "status": cmd_status,
        "recall": cmd_recall,
        "judge": cmd_judge,
        "index": cmd_index,
        "loop": cmd_loop,
    }
    handlers[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
