# ТЗ: Модернизация Hermes Agent — Out-of-the-Box

> **Принцип:** пользователь устанавливает Hermes, вводит один API-ключ, выбирает модель и тип агента — всё остальное работает автоматически. Никаких ручных настроек `config.yaml`, никаких env-переменных (кроме ключа), никаких активаций плагинов для базовой работы.

---

## Оглавление

1. [Принципы](#принципы)
2. [Анализ конфликтов с существующей архитектурой](#анализ-конфликтов)
3. [Smart Setup Wizard](#этап-0--smart-setup-wizard)
4. [DEFAULT_CONFIG — оптимальные значения](#этап-1--default_config--оптимальные-значения-из-коробки)
5. [Agent Presets — выбор агентов](#этап-2--agent-presets--выбор-агентов)
6. [Новые фичи — zero-config](#этап-3--новые-фичи--zero-config-defaults)
7. [Opt-in плагины](#этап-4--opt-in-плагины-не-требуются-для-базовой-работы)
8. [Что НЕ требует настройки](#этап-5--что-не-требует-настройки-explicit-list)
9. [Изменения в коде](#этап-6--изменения-в-коде)
10. [Тесты](#этап-7--тесты)
11. [Пользовательский flow](#этап-8--что-пользователь-делает-итог)
12. [Оценка влияния на KPI](#оценка-влияния-на-kpi)

---

## Принципы

1. **Единственный required input** — API-ключ провайдера. Всё остальное — defaults.
2. **Smart auto-detection** — Hermes сам определяет: ОС, shell, наличие Docker/SSH, кодовую директорию, размер context window модели.
3. **Sensible defaults** — каждый параметр в `DEFAULT_CONFIG` имеет значение, работающее для 95% пользователей.
4. **Opt-in, not opt-out** — продвинутые фичи (Langfuse, Vector RAG, Graphify, кастомный Caveman) доступны через `hermes tools`, но не требуют ручной активации.
5. **Cache-safe** — все defaults не нарушают prompt caching, role alternation, byte-stable system prompt.
6. **Profile-aware** — всё через `get_hermes_home()`, работает с multi-instance.
7. **Narrow waist** — новые фичи добавляются через plugins/ABC, не раздувая core toolset.
8. **Dependency pinning** — новые heavy deps — lazy, не в `pyproject.toml` core.

---

## Анализ конфликтов

### Критические конфликты (избегаем)

| Компонент исходного ТЗ | Конфликт | Решение |
| --- | --- | --- |
| **LiteLLM как единственный gateway** | Заменяет `ProviderProfile` + 67 плагинов + 500K+ строк transport-кода | LiteLLM как opt-in provider plugin `plugins/model-providers/litellm/` |
| **LangGraph для оркестрации** | Заменяет `conversation_loop.py` (7200 строк) + `run_agent.py` | BudgetManager в `agent/`, TTL в session store. Core loop не трогаем |
| **Прямой доступ к API запрещён** | `openai==2.24.0` — единственная core-dep. Весь transport на прямом SDK | Прямой доступ остаётся. LiteLLM — опция |
| **Multi-agent запрещён** | Убивает `delegate_task` (175K) + kanban (92K) | Delegation уже opt-in. Добавить config gate |
| **MemPalace как in-tree provider** | Нарушает policy «no new in-tree memory providers» | Standalone plugin repo, реализует `MemoryProvider` ABC |
| **Instructor/Outlines** | Меняет transport-слой, cache break | Post-hoc Pydantic-валидация. Tool calling уже structured |
| **Env vars как primary config** | Нарушает «.env only for secrets» policy | `config.yaml` canonical, `.agent/config.yaml` project override |

### Существующее перекрытие (не дублируем)

| Фича ТЗ | Уже есть в Hermes |
| --- | --- |
| Langfuse telemetry | `plugins/observability/langfuse/` (1138 строк), auto-enable если keys найдены |
| Context compression | `agent/context_compressor.py` (343K), `agent/conversation_compression.py` (189K) |
| SQLite session store | `hermes_state.py` (426K), WAL, FTS5, parent_session_id chains |
| Tool security | `tools/approval.py` (198K), `tools/threat_patterns.py` (14K), `tools/path_security.py` |
| Secret masking | `agent/redact.py` (47K), `agent/secret_scope.py` (13K) |
| Prompt caching | `agent/prompt_caching.py` (394 строки), 4-breakpoint Anthropic strategy |
| Tool output truncation | `tools/tool_output_limits.py` (111 строк), configurable через `config.yaml` |
| Smart model routing | `smart_model_routing` config key, пишется wizard'ом |
| Auxiliary models | Все auxiliary tasks — `provider: "auto"`, fallback на OpenRouter |
| Coding context | `agent/coding_context.py`, git/workspace snapshot в system prompt |
| Tool loop guardrails | `tool_loop_guardrails.warnings_enabled: True` |

---

## Этап 0 — Smart Setup Wizard

### 0.1. Минимальный first-run

**Сейчас:** wizard имеет 5 секций (Model, Terminal, Agent Settings, Messaging, Tools).

**Требуется:** First-run = **3 шага**:

1. **Выбор провайдера** — список из `list_providers()`, auto-detect уже установленных ключей в `.env`. Если ключ найден — провайдер выбран автоматически, шаг пропущен.
2. **Выбор модели** — `fallback_models` из `ProviderProfile` + live `/models` запрос.
3. **Выбор типа агента** — preset (default/coder/researcher/minimal).

Остальные секции (Terminal, Messaging, Tools) — **не показываются при first-run**. Доступны через `hermes setup --advanced`.

### 0.2. Auto-detection при first-run

Добавить в `setup.py` функцию `_auto_detect_environment()`:

```python
def _auto_detect_environment() -> dict:
    """Detect OS, shell, Docker, SSH, code workspace, GPU."""
    return {
        "os": platform.system(),
        "shell": os.environ.get("SHELL", "bash"),
        "docker": shutil.which("docker") is not None,
        "ssh": shutil.which("ssh") is not None,
        "cwd_is_workspace": Path.cwd().is_dir() and any(
            (Path.cwd() / marker).exists()
            for marker in [".git", "pyproject.toml", "package.json", "Cargo.toml"]
        ),
    }
```

- Docker найден — **не активировать** автоматически (безопасность). Отметить `terminal.docker_available: true` (информационно).
- `cwd_is_workspace` — `agent.coding_context: "auto"` (уже default).

### 0.3. Smart model routing — auto-enable

**Сейчас:** `smart_model_routing.enabled: False` (явно ставится в `setup.py:2911`).

**Требуется:** Для first-run — `smart_model_routing.enabled: True`. Hermes автоматически выбирает дешёвую модель для auxiliary tasks и основную — для диалога. Если у пользователя только один провайдер — routing использует его для всего.

### 0.4. Langfuse — auto-enable если ключи найдены

Если `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY` найдены в `.env` (или env) — **автоматически** добавить `observability/langfuse` в `plugins.enabled`. Не нашёл — пропустить, не спрашивать.

### 0.5. Setup Wizard — финальный flow

```
$ hermes

╭─ Hermes Setup ───────────────────────────────────────╮
│                                                       │
│  Step 1/3: Provider                                   │
│  Detected: OPENAI_API_KEY in ~/.hermes/.env           │
│  → Using: OpenAI                                      │
│                                                       │
│  Step 2/3: Model                                      │
│  Available models:                                    │
│  1. gpt-5.4                                           │
│  2. gpt-5.4-mini                                      │
│  3. gpt-5-mini                                        │
│  → Select [1-3]: 1                                    │
│                                                       │
│  Step 3/3: Agent Type                                 │
│  1. Default (balanced)                                │
│  2. Coder (focused coding)                            │
│  3. Researcher (web + browser)                        │
│  4. Minimal (max token savings)                       │
│  → Select [1-4] (Enter=1):                            │
│                                                       │
│  ✓ Setup complete!                                    │
│  Run `hermes` to start chatting.                      │
│  Run `hermes setup --advanced` for more options.      │
╰───────────────────────────────────────────────────────╯
```

- Если ключ не найден — Step 1 спрашивает провайдера, потом ключ.
- Если ключей несколько — показываем найденные провайдеры, пользователь выбирает.
- Langfuse keys найдены → silently enabled.
- Preset `coder` + `cwd_is_workspace` → одноразовая подсказка: `ℹ️  For large codebases, enable Code Graph: hermes plugins enable graphify`

---

## Этап 1 — DEFAULT_CONFIG — оптимальные значения из коробки

### 1.1. Compression

**Сейчас:** `compression.enabled: True`, `threshold: 0.50`, `target_ratio: 0.20`, `protect_last_n: 20`, `proactive_prune_tokens: 0`.

**Требуется:**

```python
"compression": {
    ...existing...,
    "proactive_prune_tokens": 48000,  # ИЗМЕНИТЬ с 0 — включить proactive prune
    # min_reclaim gate (4096) уже защищает от частых cache breaks
    # micro_compact: False — оставить (cache break каждый ход недопустимо)
},
```

### 1.2. Tool output — расширить

**Сейчас:** `tool_output.max_bytes: 50000`, `max_lines: 2000`, `max_line_length: 2000`.

**Требуется:** Добавить в `DEFAULT_CONFIG`:

```python
"tool_output": {
    "max_bytes": 50_000,
    "max_lines": 2000,
    "max_line_length": 2000,
    # NEW: head/tail split for terminal output
    "keep_first_lines": 50,
    "keep_last_lines": 80,
    # NEW: artifact store for full logs
    "artifact_store_enabled": True,
    "artifact_ttl_days": 7,
    "artifact_max_gb": 1,
},
```

- `artifact_store_enabled: True` — полные логи сохраняются в `get_hermes_home() / "artifacts" /`. В LLM context — только head+tail.
- TTL 7 дней, max 1GB — lazy cleanup при старте (аналог `checkpoints.auto_prune`).
- Формат tool result: `{exit_code, stdout_head, stdout_tail, stderr_tail, artifact_path, truncated}`.

### 1.3. Memory — без изменений

`memory.memory_enabled: True`, `user_profile_enabled: True`, `provider: ""` (built-in). External providers — opt-in через `hermes tools`.

### 1.4. Display — Caveman по умолчанию

```python
"display": {
    ...existing...,
    "response_style": "caveman",  # NEW: terse, structured, no filler
    "caveman_mode": "auto",       # NEW: auto|brief|action|detailed
},
```

- `caveman` — добавляется в system prompt через `agent/system_prompt.py`. **Статично в рамках сессии** — cache-safe.
- `auto` — режим выбирается моделью: brief для простых ответов, action для tool-heavy, detailed для объяснений.

Caveman block в system prompt:

```
## Response Style
Be terse. No filler, no preamble, no "Great question!".
Lead with the answer. Use bullet points for lists.
Action mode: report what you did, not what you're about to do.
```

### 1.5. Security — без изменений

Approval system, threat patterns, path security, secret masking — всё работает из коробки. `privacy.redact_pii: False` — opt-in.

### 1.6. Prompt caching — без изменений

Sacred. `prompt_caching.cache_ttl: "5m"`, 4-breakpoint Anthropic strategy. Не трогаем.

### 1.7. Session store — добавить TTL

```python
"session": {
    "ttl_hours": 0,       # 0 = no auto-cleanup (backward compat)
    "max_sessions": 0,    # 0 = unlimited
},
```

### 1.8. Auxiliary models — без изменений

Все auxiliary tasks — `provider: "auto"`. Auto-detect + fallback на OpenRouter.

### 1.9. MCP discovery — без изменений

`mcp_discovery_timeout: 1.5`, background thread, between-turns refresh.

---

## Этап 2 — Agent Presets — «выбор агентов»

### 2.1. Концепция

Preset настраивает модель, toolset, display style, auxiliary routing одной командой. Presets — **не profiles** (profiles = изоляция данных), это behavioral templates.

### 2.2. Реализация

Добавить в `DEFAULT_CONFIG`:

```python
"agent_presets": {
    "default": {
        "description": "Balanced agent for general tasks",
        "model": "",
        "toolsets": ["hermes-cli"],
        "display": {"response_style": "caveman", "caveman_mode": "auto"},
        "compression": {"threshold": 0.50},
    },
    "coder": {
        "description": "Focused coding agent — lean toolset, coding context on. "
                       "Run `hermes plugins enable graphify` for large repos (500K+ lines).",
        "model": "",
        "toolsets": ["hermes-cli"],
        "agent": {"coding_context": "focus"},
        "display": {"response_style": "caveman", "caveman_mode": "action"},
    },
    "researcher": {
        "description": "Web research agent — web + browser tools, verbose output",
        "model": "",
        "toolsets": ["web", "browser"],
        "display": {"response_style": "caveman", "caveman_mode": "detailed"},
    },
    "minimal": {
        "description": "Minimal agent — terminal + file only, maximum token savings",
        "model": "",
        "toolsets": ["minimal"],
        "display": {"response_style": "caveman", "caveman_mode": "brief"},
        "compression": {"threshold": 0.35},
    },
},
```

### 2.3. CLI

```bash
hermes agent list          # показать пресеты
hermes agent select coder  # выбрать пресет
hermes agent select default
```

- `hermes agent select <name>` — merge preset values в `config.yaml` (deep-merge, user values win).
- Preset хранится в `config.yaml` как `active_preset: "coder"`.
- При смене preset — новая сессия (cache-safe).

### 2.4. Setup wizard integration

Step 3 first-run: «Выберите тип агента» с 4 опциями. Default = `default`. Можно пропустить (Enter).

---

## Этап 3 — Новые фичи — zero-config defaults

### 3.1. Token Budgeting

Добавить в `DEFAULT_CONFIG`:

```python
"agent": {
    ...existing...,
    "token_budget": {
        "warning_threshold": 0.80,  # warn user at 80% context
        "hard_limit": 0.95,         # force compression at 95%
        "per_turn_output_cap": 0,   # 0 = no per-turn output token cap
    },
},
```

- `warning_threshold: 0.80` — показать «⚠️ Context 80% full».
- `hard_limit: 0.95` — trigger emergency compression.
- Всё автоматическое, без user config.
- **Не мутирует system prompt, не мутирует messages** — cache-safe.

### 3.2. Tool Output Policy

Head/tail truncation + artifact store — в `DEFAULT_CONFIG.tool_output` (см. 1.2). Работает из коробки.

### 3.3. Caveman Output

В `DEFAULT_CONFIG.display.response_style` (см. 1.4). Работает из коробки.

### 3.4. Structured Output

**Не требуется new config.** Hermes уже использует function calling для tool results. Для финального ответа — post-hoc Pydantic-валидация только если `agent.response_schema` задан (пусто по умолчанию = нет валидации = работает как сейчас).

### 3.5. Session Continuity

SQLite session store, `/resume`, `hermes -c`. Без изменений.

---

## Этап 4 — Opt-in плагины (не требуются для базовой работы)

### 4.1. Graphify (Code Graph)

**Установка:**

```bash
hermes plugins enable graphify
# или через hermes tools → Code Graph
```

**Что даёт:**
- AST + call graph — «функция A вызывает B в модуле C»
- Semantic code search — «найди всех callers функции X»
- Module dependency map — «что зависит от этого файла?»
- Structured repo overview при входе в проект

**Реализация:**
- Plugin в `~/.hermes/plugins/graphify/` (standalone repo)
- Lazy install `tree-sitter` + `ast-grep` через `tools/lazy_deps.py`
- Индексация в background при первом обращении к кодовой директории
- Service-gated tool `graph_query` — `check_fn` на наличие индекса
- Инвалидация через `git diff --name-only` (lightweight)
- Индекс хранится в `get_hermes_home() / "graphify_cache/"`

**Влияние на KPI:**
- Token savings: +5-10% на coding tasks в крупных репозиториях (500K+ строк)
- На малых проектах и non-coding задачах: 0% влияния
- Без Graphify: `search_files` (ripgrep) + `terminal` + `coding_context` покрывают 90% coding tasks

### 4.2. MemPalace (Spatial Memory)

**Установка:**

```bash
hermes plugins enable mempalace
# или через memory.provider: mempalace в config.yaml
```

**Реализация:**
- Standalone plugin repo, реализует `MemoryProvider` ABC
- `~/.hermes/plugins/memory/mempalace/`
- Активация через `memory.provider: mempalace` в `config.yaml`
- Дедупликация, TTL, conflict resolution — внутри плагина через hooks ABC

### 4.3. Vector RAG

**Установка:**

```bash
hermes plugins enable vector_rag
```

**Реализация:**
- Plugin через `ContextEngine` ABC (`agent/context_engine.py`)
- Qdrant/pgvector — lazy deps
- BGE embeddings — через auxiliary model

### 4.4. LiteLLM Gateway

**Установка:**

```bash
hermes plugins enable litellm
# или выбрать провайдера "litellm" в setup wizard
```

**Реализация:**
- `plugins/model-providers/litellm/` — регистрирует `ProviderProfile(name="litellm")`
- Пользователи выбирают `litellm` как провайдер для unified routing
- Существующие провайдеры остаются

### 4.5. Redis Cache / LLMLingua

**Установка:**

```bash
hermes plugins enable redis_cache
```

- Semantic cache — opt-in, не трогает prompt caching
- LLMLingua — только для неструктурированных текстов, не для кода
- Redis — optional, для multi-instance setups

### 4.6. Langfuse

**Auto-enable** если `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` найдены (см. 0.4). Ручная активация:

```bash
hermes plugins enable observability/langfuse
```

---

## Этап 5 — Что НЕ требует настройки (explicit list)

| Фича | Как работает из коробки |
| --- | --- |
| Context compression | `compression.enabled: True`, auto-trigger at 50% context |
| Proactive tool output prune | `proactive_prune_tokens: 48000`, min reclaim 4096 |
| Prompt caching | `cache_ttl: "5m"`, 4-breakpoint Anthropic strategy |
| Memory (built-in) | `memory_enabled: True`, no plugin needed |
| Tool output truncation | `max_bytes: 50000`, head/tail split, artifact store |
| Artifact store | `artifact_store_enabled: True`, TTL 7 дней, max 1GB |
| Security/approval | `tools/approval.py`, threat patterns, path security |
| Secret masking | `agent/redact.py`, automatic |
| Session persistence | SQLite WAL, FTS5, `/resume` |
| Smart model routing | `smart_model_routing.enabled: True` (после 0.3) |
| Auxiliary models | `provider: "auto"` для всех auxiliary tasks |
| MCP discovery | Background thread, 1.5s timeout, between-turns refresh |
| Coding context | `agent.coding_context: "auto"`, auto-detect workspace |
| Caveman output | `display.response_style: "caveman"` |
| Token budgeting | `agent.token_budget` auto-thresholds (80% warn, 95% compress) |
| Langfuse | Auto-enable если keys найдены |
| Parallel tool calls | `agent.parallel_tool_call_guidance: True` |
| Tool loop guardrails | `tool_loop_guardrails.warnings_enabled: True` |
| Environment probe | `agent.environment_probe: True` |
| Task completion | `agent.task_completion_guidance: True` |
| Verify on stop | `agent.verify_on_stop: "auto"` |
| Intent ack continuation | `agent.intent_ack_continuation: "auto"` |
| Checkpoints | `checkpoints.enabled: False` (opt-in через `--checkpoints`) |

---

## Этап 6 — Изменения в коде

### 6.1. `hermes_cli/config_defaults.py`

- Добавить `tool_output.keep_first_lines: 50`, `keep_last_lines: 80`, `artifact_store_enabled: True`, `artifact_ttl_days: 7`, `artifact_max_gb: 1`
- Добавить `display.response_style: "caveman"`, `display.caveman_mode: "auto"`
- Добавить `agent.token_budget` блок (`warning_threshold: 0.80`, `hard_limit: 0.95`, `per_turn_output_cap: 0`)
- Добавить `agent_presets` блок (4 пресета)
- Добавить `session.ttl_hours: 0`, `session.max_sessions: 0`
- Изменить `compression.proactive_prune_tokens: 48000` (с 0)

### 6.2. `hermes_cli/setup.py`

- Реструктурировать wizard: 3 шага (Provider → Model → Agent Type) для first-run
- Добавить `_auto_detect_environment()`
- Добавить auto-enable Langfuse если keys найдены
- Добавить `smart_model_routing.enabled: True` для first-run
- `--advanced` флаг для старых 5 секций
- Подсказка про Graphify если preset=coder + cwd_is_workspace

### 6.3. `agent/system_prompt.py`

- Если `display.response_style == "caveman"` — добавить Caveman block в system prompt
- **Статично в рамках сессии** — cache-safe

### 6.4. `tools/tool_output_limits.py`

- Добавить `get_keep_first_lines()`, `get_keep_last_lines()`
- Добавить `is_artifact_store_enabled()`, `get_artifact_ttl_days()`, `get_artifact_max_gb()`

### 6.5. `tools/artifact_store.py` (новый, ~100 строк)

- `save_artifact(tool_name, output) -> Path` — сохраняет полный output в `get_hermes_home() / "artifacts" / <date> / <uuid>.txt`
- `cleanup_artifacts()` — lazy cleanup при старте, удаляет старше TTL, соблюдает max_gb
- Вызывается из `tools/terminal_tool.py` и `tools/file_operations.py` если `artifact_store_enabled`

### 6.6. `agent/budget_manager.py` (новый, ~80 строк)

- `check_budget(context_tokens, model_context_length) -> BudgetStatus` — возвращает `ok | warning | critical`
- Вызывается из `agent/turn_context.py` перед каждым API call
- `warning` → print notice, `critical` → trigger emergency compression
- **Не мутирует system prompt, не мутирует messages** — cache-safe

### 6.7. `hermes_cli/subcommands/agent.py` (новый, ~120 строк)

- `hermes agent list` — показать пресеты
- `hermes agent select <name>` — применить пресет (deep-merge в config.yaml)
- `hermes agent show [name]` — показать детали пресета

### 6.8. `tools/terminal_tool.py`

- Использовать `get_keep_first_lines()`, `get_keep_last_lines()` для head/tail split
- Сохранять полный output через `artifact_store.save_artifact()` если enabled
- Возвращать `{exit_code, stdout_head, stdout_tail, stderr_tail, artifact_path, truncated}`

### 6.9. `tools/file_operations.py`

- Аналогично — head/tail для больших файлов + artifact store

---

## Этап 7 — Тесты

Все через `scripts/run_tests.sh`, E2E с temp `HERMES_HOME` (`_isolate_hermes_home` fixture).

| Тест | Что проверяет |
| --- | --- |
| `tests/test_setup_zero_config.py` | First-run с одним ключом → 3 шага → работает |
| `tests/test_agent_presets.py` | Preset select → config merge → values applied |
| `tests/test_artifact_store.py` | Save → cleanup → TTL → max_gb enforcement |
| `tests/test_budget_manager.py` | Thresholds → warning → critical → compression trigger |
| `tests/test_caveman_prompt.py` | System prompt содержит Caveman block, cache-stable |
| `tests/test_tool_output_head_tail.py` | Terminal output truncation с head/tail split |
| `tests/test_auto_detect_env.py` | OS/shell/docker/workspace detection |
| `tests/test_langfuse_auto_enable.py` | Keys found → plugin enabled; keys missing → not enabled |

**Правила тестов (из AGENTS.md):**
- Behavior contracts, not snapshots
- No change-detector tests
- No source-regex tests
- E2E for resolution chains, config propagation, file I/O
- `_isolate_hermes_home` fixture for all tests touching `~/.hermes/`

---

## Этап 8 — Что пользователь делает (итог)

```bash
pip install hermes-agent    # или git clone + uv pip install -e .
hermes                      # first-run wizard
  → Step 1: Provider (auto-detected from .env)
  → Step 2: Model (pick from list)
  → Step 3: Agent type (default/coder/researcher/minimal)
  → Done. Start chatting.

hermes agent select coder   # сменить пресет (опционально)
hermes setup --advanced     # настроить messaging/tools (опционально)
hermes tools                # включить плагины (опционально)
hermes plugins enable graphify  # code graph для крупных репо (опционально)
```

**Больше ничего настраивать не нужно.** Compression, caching, memory, security, tool truncation, artifact store, Caveman, token budgeting, auxiliary models, session persistence — всё работает из коробки.

---

## Оценка влияния на KPI

| KPI из исходного ТЗ | Без opt-in плагинов | С opt-in плагинами | Комментарий |
| --- | --- | --- | --- |
| Token reduction 40-70% | 35-55% (coding) / 50-70% (non-coding) | 45-65% (coding, +Graphify) / 50-70% (non-coding) | Graphify даёт +5-10% на coding в крупных репо |
| Context Window Exceeded — 100% elimination | 100% | 100% | Compression + BudgetManager + proactive prune |
| Memory retrieval >=90% | 90%+ (built-in) | 92%+ (+MemPalace) | Built-in memory достаточно для большинства |
| Session continuity | 100% | 100% | SQLite WAL, /resume, hermes -c |
| Hallucination <=5% | ~5% | ~3-4% (+Graphify подтверждает связи) | 1-2% improvement на coding tasks |
| Zero config | ✅ | ✅ | 3 шага wizard, всё остальное — defaults |

---

## Архитектурные принципы (памятка)

- **Prompt caching is sacred** — никакие изменения system prompt mid-conversation
- **Narrow waist** — новые фичи через plugins/ABC, не через core tools
- **Footprint Ladder** — extend existing → CLI+skill → service-gated tool → plugin → MCP → core tool (last resort)
- **Extend, don't duplicate** — проверять существующую инфраструктуру перед добавлением новой
- **Behavior contracts over snapshots** — тесты проверяют инварианты, не значения
- **E2E validation** — реальные импорты, temp HERMES_HOME, не моки
- **Cache-, alternation-, invariant-safe** — role alternation, byte-stable system prompt
- **Dependency pinning** — новые heavy deps — lazy, не в core
- **`.env` — только секреты** — behavioral settings в `config.yaml`
- **`get_hermes_home()`** — для всех путей, profile-aware
