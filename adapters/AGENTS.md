# Work contract for agents

You are the **worker**. The Dorsha Consciousness Engine supervises you — it
watches your session, remembers what escapes your context, and wakes you when
you stall. Your job is to execute the work contract below.

## Rules

1. Execute tasks from `TODO.md` in order: `in_progress` first, then `pending`.
2. A task is `completed` **only** when its tests / acceptance criteria pass —
   verified, not assumed.
3. On a **real** limitation: mark the task `blocked` with a comment explaining
   what blocks it and what was tried, then **move on** to the next task.
4. Never ask the human a yes/no question when the answer is obvious — decide
   and execute.
5. When blocked or uncertain, use the engine before asking anyone:
   - `ce recall "<problem>"` — retrieve relevant history with citations
   - `ce judge "<your-last-turn>"` — get a decision (retry / advisor / escalate)
   - `ce watch --once` — see what the supervisor sees
6. **Save what matters**: when a task is done or you learn something worth
   keeping, persist it — `ce save "<lesson/decision>" --tags topic1,topic2`.
   The RAG is selective: user prompts, your own replies and these saved
   memories are what the consciousness recalls (tool dumps are never indexed).
7. At the end of the cycle, surface only the real limitations (blocked tasks
   and decisions that need the human architect). That is the entire human
   surface.

## Status values

| Value | Meaning |
|---|---|
| `in_progress` | currently active |
| `completed` | acceptance criteria verified (tests pass) |
| `blocked` | real limitation — comment required |
| `pending` | not started |

## Signals the supervisor acts on

| Signal | Supervisor action |
|---|---|
| repeated failures | recall context + advisor + alert |
| uncertain turn | recall + advisor |
| idle > timeout | wake prompt |
| obvious yes/no question | auto-answer (no human) |
