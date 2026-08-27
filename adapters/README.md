# Adapters — connect any agent to the engine

Same protocol, different glue. The engine never depends on a specific agent;
these adapters show the integration points:

1. **Work contract** — `AGENTS.md` (copy into the agent's project root; Claude
   Code/Codex/Gemini CLI read it automatically as `CLAUDE.md` / `AGENTS.md`)
2. **MCP tools** (recommended) — `ce mcp` exposes the RAG as callable agent
   tools (`ce_memory_search`, `ce_memory_save`, `ce_judge`, `ce_status`)
3. **Observation** — watch the worker's session DB (`ce watch`) or hook tool
   calls (Claude Code `PreToolUse` hook)
4. **Actions** — the `ce` CLI: recall, judge, advisor, loop

## MCP (Model Context Protocol) — the RAG as a search tool

Any MCP-capable agent (Claude Code, Hermes, Codex with MCP) can call the
memory directly, like a search tool:

```bash
pip install -e .          # 'ce' on PATH
ce mcp                    # stdio MCP server: expose the tools
```

Claude Code (`.mcp.json` in the project root — already shipped in this repo):

```json
{
  "mcpServers": {
    "dorsha-ce": { "command": "ce", "args": ["mcp"] }
  }
}
```

Hermes (`config.yaml`):

```yaml
mcp_servers:
  dorsha-ce:
    command: /path/to/venv/bin/ce
    args: [mcp]
```

Tools the agent gets:

| Tool | What it does |
|---|---|
| `ce_memory_search` | query the selective RAG (prompts + replies + saved memory), returns chunks with citations |
| `ce_memory_save` | persist a memory entry (decision, lesson) |
| `ce_judge` | classify a turn + get the decision path (retry/advisor/escalate) |
| `ce_status` | engine state |

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
