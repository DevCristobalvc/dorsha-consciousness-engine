# Adapters — connect any agent to the engine

Same protocol, different glue. The engine never depends on a specific agent;
these adapters show the three integration points:

1. **Work contract** — `AGENTS.md` (copy into the agent's project root; Claude
   Code/Codex/Gemini CLI read it automatically as `CLAUDE.md` / `AGENTS.md`)
2. **Observation** — watch the worker's session DB (`ce watch`) or hook tool
   calls (Claude Code `PreToolUse` hook)
3. **Actions** — the `ce` CLI: recall, judge, advisor, loop

## Claude Code

```bash
# 1. contract (auto-loaded by Claude Code as CLAUDE.md)
cp adapters/AGENTS.md .claude/AGENTS.md

# 2. hook: consciousness observes every tool call
mkdir -p .claude
cat > .claude/settings.json <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|Write|Edit",
        "hooks": [{ "type": "command", "command": "python3 adapters/claude-hook.py" }] }
    ]
  }
}
JSON

# audit trail: .ce/audit.jsonl grows with judge verdicts per tool call
# enforce mode: python3 adapters/claude-hook.py --enforce
```

## Codex CLI

```bash
# 1. contract
cp adapters/AGENTS.md AGENTS.md

# 2. supervisor sidecar: watchdog in the background
ce watch --interval 60 &          # alerts on failures/idle via webhook

# 3. when the worker stalls, you (or the sidecar) run:
ce recall "where we left off"
ce judge "last turn text"
```

## Gemini CLI

```bash
# 1. contract
cp adapters/AGENTS.md AGENTS.md

# 2. same sidecar pattern as Codex:
ce watch --once                    # quick check before a manual nudge
ce recall "decision on X"          # inject memory into the prompt
```

## Any custom agent

- Point `settings.session_db` at the agent's session store (schema adapter:
  `Watchdog.latest_turn` is the single place to adapt)
- Emit events (`context_exhausted`, `attempt_failed`, `uncertain_choice`,
  `idle_timeout`) and call the matching `Engine` methods
- Schedule `ce watch` via cron/systemd for always-on supervision
