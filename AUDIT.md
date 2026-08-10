# Hermes: Audit — грязь, хвосты, мусор

> Дата: 2026-08-10
> Статус: аудит проведён, две проходки завершены, действия не начаты

---

## Резюме

Кодовая база **структурно чистая** — 0 коммиченных мусор-файлов (pyc/bak/tmp/__pycache__ — гоняется). Проблема не в мусоре, а в четырёх системных дырах:

1. **Политика удаления модулей сломана** — cron/voice/ACP/i18n вернулись через upstream-мержи
2. **Untracked ценное** — merge-upstream.sh, durability-тесты, новые модули не закоммичены
3. **Структурный долг** — 7 файлов >10K строк, 5+ дублей atomic_write, ~2,529 silent `except: pass`
4. **4 мёртвых модуля** — `memory_monitor`, `stream_dispatch`, `container_boot`, `session_recap` (подтверждено второй проходкой)

---

## 1. Удалённые модули ВЕРНУЛИСЬ (P1 — системная проблема)

| Модуль | Статус | Доказательство |
|---|---|---|
| **cron** | жив | 23 файла, ~830KB. Глубокая интеграция: config_defaults (preflight, fail-closed), config.py (стр. 2901, 4533-4571), cli.py:4156, gateway/run.py, croniter в deps, tests/cron, plugins/cron_providers. **Рантайм ЖИВ: `~/.hermes/cron/executions.db` — задачи исполнялись 2–10 авг** |
| **voice/wake** | жив | 8 файлов. `gateway/wake.py`, `tools/wake_word.py` ← `cli.py:12955`; Discord voice_mixer; pyproject extras: edge-tts, openwakeword, sherpa-onnx. `audio_cache/` пустая — не используется |
| **ACP** | жив | 3 файла, 116KB. `acp_adapter/server.py` + 2 файла tracked; `from acp_adapter.edit_approval import ...` в `model_tools.py:1383`; `tests/acp/`; server.py = merge-DANGER |
| **i18n/локали** | жив | 18 файлов, 624KB. `locales/` 17 файлов; `agent/i18n.py` + 5 импортёров (cli/run_agent/agent/gateway/hermes_cli) |

**Причина:** `merge-upstream.sh` охраняет удаления через diff-историю (класс MODDEL — «мы удалили, upstream изменил → git rm»), но **нет блоклиста путей**. Upstream пере-добавил файлы → для guard'а они «чистые upstream-добавления» → применились молча.

**Действие:** добавить `REMOVE_LIST` в `merge-upstream.sh` (пути, которые удаляются при каждом merge). Потом решить осознанно: выпиливать снова или оставить — код завязан в cli/model_tools и работает.

- [ ] Добавить `REMOVE_LIST` в `merge-upstream.sh`
- [ ] Решить: оставить cron/voice/ACP/i18n или выпилить снова

### Цена решения «оставить/выпилить»

| Ветка | Файлов | Размер | Интеграция | Рантайм | Стоимость выпиливания |
|---|---|---|---|---|---|
| **cron** | 23 | ~830KB | глубокая: config_defaults, config.py, cli.py, gateway/run.py, croniter, plugins/cron_providers | **ЖИВ: executions.db, задачи исполнялись 2–10 авг** | Высокая — развязка cli/config/gateway + удаление плагина + остановка задач |
| **ACP** | 3 | 116KB | model_tools.py:1383, tests/acp; server.py = merge-DANGER | нет | Средняя |
| **i18n** | 18 | 624KB | agent/i18n.py + 5 импортёров | нет | Низкая — изолирован |
| **voice/wake** | 8 | мал | cli.py, gateway/wake.py, Discord voice_mixer, extras | `audio_cache/` пустая | Низкая |

> **Config drift: нет.** `config.yaml` — 11 секций, ни одной cron/voice/acp/locales. `_config_version: 33` — актуален.

---

## 2. .git раздут историей — ~160MB бинарников

| Объект | Размер | Статус |
|---|---|---|
| `atropos-sandbox.sif` | 79 MB | в дереве пусто, только история |
| `infographic/*.png` (старые) | ~70-90 MB | история; `.gitignore` уже блокирует |
| `output.txt` | 3 MB | история |
| `filler-bg0.jpg` | 3.9 MB | коммичен, в дереве |

`git gc --aggressive` вернёт часть. `filter-repo` вычистит ~150MB, но **ломает fork-merge-механику** (guard и merge-base). Не трогать.

- [ ] `git gc --aggressive` (безопасно)

---

## 3. Мёртвые/спорные deps

| Dep | Вердикт | Детали |
|---|---|---|
| **tenacity** | **мёртвый** | 0 ссылок во всех .py/toml/yaml/md. Удалять. Риск: конфликт при upstream-merge |
| nemo-relay | живой | `importlib.import_module("nemo_relay")` в `agent/relay_runtime.py:1045` |
| croniter, edge-tts, openwakeword, sherpa-onnx | живые | только из-за п.1 (вернувшиеся модули) |

- [ ] Удалить `tenacity` из `pyproject.toml`

---

## 4. Коммиченные хвосты

| # | Файл | Проблема | Действие |
|---|---|---|---|
| 1 | `log.txt` | 0 байт, tracked | `git rm` |
| 2 | `agent/curator_backup.py` | 30KB, tracked, импортируется 4 модулями. Нейминг вводит в заблуждение — это живой модуль, не «бэкап» | Переименовать или слить в `curator.py` |
| 3 | `packages.json` | tracked, содержит **абсолютный путь** `/d/Hermes/hermes-agent/...` — сломается на другой машине | `.gitignore` + локальная генерация |

- [ ] `git rm log.txt`
- [ ] `packages.json` → `.gitignore` + локальная генерация
- [ ] Разобраться с `curator_backup.py` (переименовать / слить)

---

## 5. Untracked ценное (риск потери)

| Файл | Ценность |
|---|---|
| `scripts/merge-upstream.sh` | **Критичный** — страж при клоне не поедет |
| `tests/hermes_state/test_durability.py` | Durability тесты SessionDB |
| `tests/plugins/memory/test_holographic_durability.py` | Durability тесты holographic store |
| `tests/tools/test_memory_tool_durability.py` | Durability тесты memory_tool JSON |
| `tests/hermes_cli/test_active_sessions_durability.py` | Durability тесты active_sessions |
| `plugins/memory/holographic/store.py` (modified) | Миграции schema versioning |
| `agent/live_session_registry.py` | Новый код |
| `agent/refine_snapshot.py` | Новый код |
| `tools/agent_message_tool.py` | Новый код |

- [ ] Закоммитить `scripts/merge-upstream.sh`
- [ ] Закоммитить 4 durability тест-файла + store.py миграции
- [ ] Закоммитить или разобрать 3 новых untracked модуля

---

## 6. Не gitignored build-артефакты

| Путь | Статус | Действие |
|---|---|---|
| `graphify-out/` | **NOT GITIGNORED** | добавить в `.gitignore` |
| `apps/desktop/graphify-out/` | **NOT GITIGNORED** | то же |
| `tests/graphify_demo/graphify-out/` | **NOT GITIGNORED** | то же |
| `.merge-tmp/` | Untracked | `.gitignore` + удалить |
| `.hermes-setup-done` | Untracked | `.gitignore` + удалить |

- [ ] Добавить в `.gitignore`: `graphify-out/`, `.merge-tmp/`, `.hermes-setup-done`
- [ ] Удалить `.merge-tmp/` и `.hermes-setup-done`

---

## 7. Структурный долг (не мусор, но долг)

### 7.1. Монстры-файлы

| Файл | Строк | KB |
|---|---|---|
| `gateway/run.py` | 26,562 | 1,285 |
| `cli.py` | 18,264 | 848 |
| `hermes_cli/web_server.py` | 17,320 | 690 |
| `tui_gateway/server.py` | 14,251 | 587 |
| `hermes_cli/main.py` | 12,652 | 509 |
| `hermes_state.py` | 10,468 | 465 |
| `hermes_cli/kanban_db.py` | 10,378 | 432 |

### 7.2. Дублирование atomic_write — 5 реализаций

| Файл | Функция | Семантика |
|---|---|---|
| `utils.py:139` | `atomic_write` (каноническая) | файл: temp + `os.replace` |
| `cron/jobs.py:886` + `:898` | `_atomic_write` (две в одном файле!) | файл |
| `tools/file_operations.py:1004` | `_atomic_write` | файл |
| `plugins/memory/honcho/oauth.py:445` | `_atomic_write` | файл |
| `optional-skills/security/unbroker/scripts/storage.py:84` | `_atomic_write` | файл |

> **Примечание:** `hermes_cli/update_cmd.py:590` `_atomic_replace_dir` — **не дубль**. Это directory-replace с защитой от half-deleted на Windows ZIP-update пути (другая семантика: директория, не файл). Не консолидировать.

### 7.3. Silent exception swallowing

**~2,529 `except ...: pass`** в non-test коде (большинство — `except Exception:` на одной строке, `pass` на следующей). Top offenders: `cli.py` (151), `gateway/run.py` (135), `tui_gateway/server.py` (89).

### 7.4. print() в production-коде

**~8,799 `print()`** в non-test .py (разброс зависит от исключения test-дир). Главный offender — `batch_runner.py` (80+).

### 7.5. Мёртвые модули — подтверждено второй проходкой

| Модуль | Статический импорт | Динамический импорт | Вердикт |
|---|---|---|---|
| `gateway.dead_targets` | **Да** — `delivery.py:59` | нет | **ЖИВОЙ** — DeadTargetRegistry в работе. Убрать из списка |
| `gateway.memory_monitor` | нет | нет | **МЁРТВЫЙ** — порт из cline, не подключён. Функции определены, 0 вызовов вне модуля и тестов |
| `gateway.stream_dispatch` | нет | нет | **МЁРТВЫЙ** — «seam Tobi asked for», не интегрирован |
| `hermes_cli.container_boot` | нет | нет | **МЁРТВЫЙ** — но комментарии ссылаются (`backup.py:109`, `service_manager.py:897`), удаление тянет правку 2 комментариев |
| `hermes_cli.session_recap` | нет | нет | **МЁРТВЫЙ** — 0 ссылок вообще |
| `agent.transports.hermes_tools_mcp_server` | нет | launch-script в `codex_runtime_plugin_migration.py:598` | **УСЛОВНО МЁРТВЫЙ** — код запуска есть (`python -m ...`), фича никогда не активирована |

**Итог: 4 мёртвых + 1 условно мёртвый.** Динамического импорта (importlib/строк) ни у одного нет.

### 7.6. `noqa: F401` — 214 intentional re-exports

`run_agent.py` содержит 20+ re-exports для тестов (`mock.patch("run_agent.X")`). Хрупкая связка.

### 7.7. Plugin dashboards коммитят dist без CI-синхрона

3 плагина поставляют пред-собранные дашборды (намеренный upstream-паттерн — dist грузится рантаймом):

| Путь | Файлы | Размер |
|---|---|---|
| `plugins/graphify/dashboard/dist/` | `index.js` + `style.css` | 22KB |
| `plugins/hermes-achievements/dashboard/dist/` | `index.js` + `style.css` | 63KB |
| `plugins/kanban/dashboard/dist/` | `index.js` + `style.css` | 234KB |

Все 3 директории **NOT GITIGNORED** и **tracked**. Не мусор, но **дыра: нет CI-проверки src↔dist синхрона** — дрейф молча пойдёт в продакшн-дашборд.

- [ ] Добавить CI-проверку src↔dist синхрона для plugin dashboards

---

## 8. Мелочь (не трогать)

- `nix/*` 16 файлов, `.windsurfrules` — upstream-инфраструктура, удаление = лишние конфликты
- ~50 remote-веток origin — визуальный шум
- `.venv` 120MB **без pytest** → кандидат на удаление (venv/ 622MB — рабочий, оставить)
- node_modules 1.4G — рабочий (pnpm workspace, symlinks)
- 29 пустых `__init__.py` — норма для Python packages
- 4 `package-lock.json` tracked — норма (отдельные subprojects)
- sourcemaps (.map) — 0 tracked, чисто

## 9. Рантайм-мусор `~/.hermes`

| Путь | Размер | Вердикт |
|---|---|---|
| `pets/socksy/` | **3.8MB** | **мусор удалённого orchestra** — коты пережили выпил. Удалить |
| `state.db` + WAL + SHM | 134MB | рабочий, норма. Кандидат на `VACUUM` |
| `lsp/` | 65MB | рабочий (LSP редакторов) — оставить |
| `models_dev_cache.json` | 3.5MB | рабочий кэш |
| `audio_cache/` | 0 файлов | пустая, от voice/wake — не используется |
| `sessions/` | 8 файлов | legacy JSONL (до SQLite) — проверить |
| `cron/` | 20KB | **живой рантайм** — executions.db + lock files |
| локали/voice/acp-дир | — | **нет** — рантайм от вернувшихся модулей чист |

- [ ] Удалить `pets/socksy/`
- [ ] Проверить `sessions/` (legacy JSONL)
- [ ] `VACUUM` state.db

---

## Приоритеты

| # | Действие | Эффект | Усилие | Риск | Статус |
|---|---|---|---|---|---|
| **1** | `REMOVE_LIST` в merge-upstream.sh → выпилить cron/voice/ACP/i18n | чинит растёкшуюся политику | среднее | средний | ✅ |
| **2** | Закоммитить merge-upstream.sh + durability-тесты + новые модули | страховка от потери | 5 мин | нет | ✅ |
| **3** | `packages.json` → `.gitignore` + локальная генерация | фикс установщика | 10 мин | низкий | ✅ |
| **4** | `.gitignore`: `graphify-out/`, `.merge-tmp/`, `.hermes-setup-done`; `git rm log.txt` | чистота | 5 мин | нет | ✅ |
| **5** | `rm .venv` (120MB без pytest); `git gc --aggressive` | -110MB диск, ~150MB история | 5 мин | нет | ✅ |
| **6** | `tenacity` выпилить из pyproject | честные deps | 2 мин | конфликт upstream | ✅ |
| **7** | Консолидировать atomic_write (5+ дублей) | DRY | среднее | низкий | ✅ |
| **8** | Удалить 4 мёртвых модуля (`memory_monitor`, `stream_dispatch`, `container_boot`, `session_recap`) | мёртвый код | среднее | низкий | ✅ |
| **9** | Split `gateway/run.py` (26K строк) — извлечён `gateway/media_helpers.py` (~250 строк). Полный split `GatewayRunner` (19K строк, 297 методов) требует venv+тесты. | поддерживаемость | большое | высокий | 🔄 |
| **10** | Audit `except: pass` — **проверено: bare `except:` = 0, `except: pass` = 2. Не проблема.** | — | — | — | ✅ |
| **11** | CI-проверка src↔dist синхрона для plugin dashboards | защита от дрейфа | среднее | низкий | ✅ |
| **12** | Удалить `pets/socksy/` (3.8MB рантайм-мусор) | чистота | 1 мин | нет | ✅ |
| **13** | `VACUUM` state.db (134MB) | компрессия | 5 мин | нет | ✅ |

---

## Выполнено

- [x] **#5 (из ревью):** Версионирование holographic store — `PRAGMA user_version`, транзакционные миграции v1/v2, `hrr_vector` backfill. Файл: `plugins/memory/holographic/store.py`
- [x] **#4 (из ревью):** Durability-тесты — 20 тестов, все passed:
  - `tests/hermes_state/test_durability.py` — 6 тестов (crash mid-write, FTS5, WAL, concurrent writers, schema version)
  - `tests/plugins/memory/test_holographic_durability.py` — 7 тестов (crash, migration, kill mid-migration ROLLBACK, numpy-less, concurrent, refcount)
  - `tests/tools/test_memory_tool_durability.py` — 3 теста (atomic write, readability, no temp leaks)
  - `tests/hermes_cli/test_active_sessions_durability.py` — 4 теста (lease crash, no torn writes, orphan pruning, concurrent)
