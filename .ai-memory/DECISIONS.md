# Decisions

## 2026-08-09: Opik removed, built-in observability instead
- **Context**: User wanted observability for Hermes internals ("black box" problem)
- **Tried**: Opik (comet-ml) — installed, configured, @track on 5 functions
- **Decision**: Removed Opik entirely. Extended `/status` command instead.
- **Rationale**: No external deps, no network, no Docker. All data already in state.db + agent attributes. `/status` is in TUI — no browser needed.
- **Files**: `cli.py` `_show_session_status()` expanded from ~65 to ~350 lines, 5 sections

## 2026-08-09: Token economy improvements
- **Sketch mode nudge**: READ_FILE_SCHEMA description guides model to use sketch first for large files
- **Persist nudge**: execute_code description explains persist=true for data-heavy workflows
- **Cumulative savings**: `_cumulative_tokens_saved` in context_compressor, displayed in /status and turn summary
- **Auto-sketch**: Files >500 lines auto-return sketch instead of full content (saves tokens)
