# Protocol

The engine is framework-agnostic. This document defines the events, payloads
and the minimal glue any agent needs to participate. No Hermes-specific
dependencies — the same protocol applies to Claude Code, Codex, Gemini CLI
or a custom agent.

## Events

| Event | Trigger | Engine action |
|---|---|---|
| `context_exhausted` | window/memory full | `recall(query)` → inject `[RECALL]` block |
| `attempt_failed` | worker fails (error markers / exit codes) | `judge()` → retry → advisor → escalate |
| `uncertain_choice` | hedging / no clear direction | `judge()` → advisor → escalate |
| `obvious_ask` | permission question with obvious answer | auto_answer (no human) |
| `idle_timeout` | no activity > T min | `tick()` → wake prompt + webhook |
| `task_done` | acceptance criteria verified | mark completed → next task |

## Recall block (injected into worker context)

```
[RECALL — retrieved from session history]
# query: <original question>

(<session_id> msg:<message_id> @ <iso-utc>) <role>: "<content>"
```

Every injected fragment carries provenance: session id, message id, timestamp.
The block is truncated to `recall.max_chars` (default 4096) so it never
overflows the worker's context.

## Advisor brief / response

Request (worker → advisor):

```json
{
  "problem": "vector store slow on 34k messages",
  "attempts": ["tried chroma", "tried faiss"],
  "hypothesis": "vec0 kNN is the bottleneck",
  "evidence": "profile shows 80% time in kNN"
}
```

Response (advisor, JSON only, no tools):

```json
{
  "alternatives": ["pre-filter by session", "shard by month", "use HNSW"],
  "recommendation": "pre-filter by session before kNN",
  "confidence": 0.8
}
```

## Loop wake payload (webhook)

```json
{
  "type": "wake",
  "task": "CE-007",
  "message": "Engine tick — active task: CE-007 (...)\nLast exchange: ...\nNext action: execute CE-007 per its acceptance criteria..."
}
```

## Status values (TODO contract)

| Value | Meaning |
|---|---|
| `in_progress` | currently active |
| `completed` / `done` | acceptance criteria verified |
| `blocked` | real limitation, comment required |
| `pending` | not started |

The agent never stays stuck: a `blocked` task is skipped and surfaced to the
human at the end of the cycle. `completed` requires verified tests.

## Adapters (thin glue, same protocol)

| Agent | Glue |
|---|---|
| Hermes | `ce recall <q>` / `ce judge <turn>` via terminal; webhook for wake |
| Claude Code | read `TODO.md` from working tree; `ce` CLI for recall/judge |
| Codex | same as Claude Code; `ce loop` scheduled via cron |
| Gemini CLI | same; advisor JSON via stdin |

The engine never hardcodes a model or framework: worker model, advisor model,
embedding model and all thresholds come from `config/` (see `config.example.yaml`).
