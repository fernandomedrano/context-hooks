
## Context Hooks — Agent Intelligence

This project uses [context-hooks](https://github.com/fernandomedrano/context-hooks) for proactive context intelligence. Hooks fire automatically — no manual invocation needed.

### What fires automatically
- **PreToolUse** (Read/Edit/Write): parity companions, bug history, knowledge refs surfaced before you touch a file
- **PreToolUse** (Bash tests): failure-class knowledge surfaced before test runs
- **PostToolUse**: commit indexing, parity alerts, large output indexing
- **SessionStart**: health check, project briefing, unread memos

### Commands you can run
- `context-hooks query parity` — find files edited without their usual companion
- `context-hooks query file <path>` — get intelligence on a specific file
- `context-hooks knowledge search <query>` — search the knowledge store
- `context-hooks search-output <query>` — search indexed tool outputs (large outputs are auto-indexed)
- `context-hooks memo list` — check inter-agent memos
- `context-hooks memo send --from <you> --to <agent> --subject "..." --content "..."` — send a memo
- `context-hooks status` — overview of what's tracked
