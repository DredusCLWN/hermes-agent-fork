# Hermes Agent — Project State

## Current Version
- **Hermes Agent** — autonomous AI agent with multi-provider LLM support, tool calling, delegation, context compression.

## Key Files
- `cli.py` — TUI, commands (/status, /refine, /trace), status bar, turn summary
- `agent/conversation_loop.py` — main turn loop (run_conversation)
- `agent/context_compressor.py` — context window compression with anti-thrash
- `agent/chat_completion_helpers.py` — LLM API calls (direct + interruptible)
- `agent/background_review.py` — memory/skill review after turns
- `tools/delegate_tool.py` — subagent delegation
- `tools/code_execution_tool.py` — PTC sandbox
- `tools/file_tools.py` — read_file (with sketch mode, auto-sketch >500 lines)
- `hermes_state.py` — SQLite state.db (sessions, messages, model_usage, delegations)

## Recent Changes
- **Token economy improvements**: sketch mode nudge, persist nudge, cumulative savings counter, auto-sketch for large files
- **Opik integration**: attempted, then fully removed (user decided to build own observability)
- **Extended /status**: full agent observability — 5 sections (Session, Token Economy, Agent Activity, Performance, Health)

## Architecture Decisions
- No external observability deps — use existing state.db + agent attributes
- /status as primary observability surface (no separate /trace command yet)
- All DB queries in try/except — graceful degradation
