# /status Extended — Design Spec

## Goal
Expand `/status` command to show full agent observability: token economy, activity breakdown, performance, health.

## Data Sources (no new tables)
- `agent` object: session_*_tokens, session_api_calls
- `context_compressor`: compression_count, _last_compression_savings_pct, _cumulative_tokens_saved, last_prompt_tokens, context_length, compression_ineffective_count, compression_fallback_streak
- `state.db → sessions`: message_count, tool_call_count, api_call_count, estimated_cost_usd, rewind_count
- `state.db → session_model_usage`: per-model token/cost breakdown
- `state.db → messages`: tool_name GROUP BY for tool usage stats, timestamps for performance
- `state.db → async_delegations`: delegation history
- `async_delegation.active_count()`: live subagents
- `process_registry.count_running()`: live processes
- `_background_tasks`: live tasks count
- `GoalManager`: goal state
- `TurnTally`: per-turn tool usage

## Sections
1. **Session** — ID, model, provider, toolsets, focus, timestamps, CWD, git
2. **Token Economy** — spent (input/output/cache/reasoning), cost, compressions, context window
3. **Agent Activity** — turns, API calls, tool calls breakdown, delegation, background, memory
4. **Performance** — avg/slowest turn, avg API call, calls/turn, tools/turn
5. **Health** — running, compression health, anti-thrash, provider, rewinds

## Files to modify
- `cli.py` — `_show_session_status()` method (expand from ~65 lines to ~250)

## No new dependencies, no new tables, no new decorators.
