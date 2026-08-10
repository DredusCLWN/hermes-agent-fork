<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent — Fork ☤

<p align="center">
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Upstream-NousResearch/hermes--agent-blueviolet?style=for-the-badge" alt="Upstream: NousResearch/hermes-agent"></a>
  <a href="https://github.com/DredusCLWN/hermes-agent-fork/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/DredusCLWN/hermes-agent-fork/releases"><img src="https://img.shields.io/badge/Releases-latest-FFD700?style=for-the-badge" alt="Releases"></a>
</p>

> **Upstream:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the self-improving AI agent by [Nous Research](https://nousresearch.com). Full documentation: [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/).

---

## Что это

Форк [Hermes Agent](https://github.com/NousResearch/hermes-agent) — self-improving AI агента от Nous Research. **Не официальный релиз, не поддерживается Nous Research.** Ставьте на свой риск. Форк поддерживает upstream-синхронизацию через `scripts/merge-upstream.sh` и вырезает функции, не нужные автору (cron, voice/wake, ACP, i18n, pet) — остальное работает как в upstream.

### Что изменено

- **Удалены мёртвые модули** (~29K строк): `cron`, `voice/wake`, `ACP adapter`, `i18n/locales`, `pet`. Все продакшн-импорты guarded (`try/except ImportError`).
- **Fork installer** (`fork-install.ps1`) — wrapper поверх канонического `scripts/install.ps1`: клонирует форк по тегу, передаёт управление оригинальному установщику, добавляет `.env` template и desktop shortcut.
- **`release-node-dists.yml`** — CI workflow: при push тега `v*` собирает JS-dist (web/, ui-tui/) и крепит к GitHub Release. Ассеты готовы для будущего no-Node fast-path (пока установщик сам бутстрапит Node и собирает из исходников).
- **Durability-тесты** — 20 тестов покрытия crash-safety: SessionDB, holographic store, memory_tool, active_sessions.
- **Версионирование holographic store** — `PRAGMA user_version`, транзакционные миграции v1/v2, `hrr_vector` backfill.
- **Улучшения memory system** — CacheAligner, effort routing, failure mining, provenance tracking через `threading.local`, Jaccard dedup, secret scrubbing.
- **Графовая память (graphify)** — `os.getcwd()` вместо phantom `_launch_cwd`, mtime fallback для shallow clones, dynamic scope в tool description.
- **Skill Gate** — 4 слоя защиты: protected skills, deletion/rename detection, held-out validation (документировано в `AGENTS.md`).
- **Очистка** — `tenacity` удалён из deps, `packages.json` → `.gitignore` + локальная генерация, `graphify-out/` → `.gitignore`, `log.txt` удалён, `atomic_write` консолидирован.
- **`merge-upstream.sh`** — `REMOVE_LIST` предотвращает возврат удалённых модулей при upstream-merge.

### Чего нет (удалено)

| Модуль | Причина |
|---|---|
| `cron` | Не используется в данной конфигурации |
| `voice/wake` | Не используется (wake word detection) |
| `ACP adapter` | Не используется (agent communication protocol) |
| `i18n/locales` | Не используется (локализация) |
| `pet` | Не используется (orchestra/pet система) |

---

## Установка

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File fork-install.ps1
```

Опции:

```powershell
# Пин к конкретному тегу
powershell -ExecutionPolicy Bypass -File fork-install.ps1 -Tag v2026.8.10

# Пропустить setup wizard
powershell -ExecutionPolicy Bypass -File fork-install.ps1 -SkipSetup

# Собрать desktop app
powershell -ExecutionPolicy Bypass -File fork-install.ps1 -IncludeDesktop

# Без ярлыка на рабочем столе
powershell -ExecutionPolicy Bypass -File fork-install.ps1 -NoShortcut
```

Установщик:
1. Клонирует форк по тегу (`git clone --depth 1 --branch <tag>`)
2. Передаёт управление `scripts/install.ps1` (uv, Python 3.11, Node.js, ripgrep, ffmpeg, MinGit)
3. Создаёт `.env` template для API-ключей
4. Создаёт ярлык desktop (если собран)

**Требования:** [Git for Windows](https://git-scm.com).

### Linux, macOS, WSL2

```bash
# Клонировать форк по тегу
git clone --depth 1 --branch v2026.8.10 https://github.com/DredusCLWN/hermes-agent-fork ~/hermes-agent
cd ~/hermes-agent

# Venv вне исходников
uv venv ~/.hermes/venvs/hermes-dev --python 3.11
source ~/.hermes/venvs/hermes-dev/bin/activate

uv pip install -e ".[all,dev]"
```

> ⚠️ Не используйте `curl install.sh | bash` + `git remote set-url` — это ломает `hermes update` (detached HEAD на теге, origin переписан). Клонируйте форк напрямую.

### После установки

```bash
hermes              # CLI — начать разговор
hermes setup        # Setup wizard (провайдер, модель, ключи)
hermes model        # Выбрать LLM
hermes --tui        # TUI режим
```

---

## Начало работы

```bash
hermes              # Interactive CLI
hermes model        # Выбрать LLM провайдера и модель
hermes tools        # Настроить инструменты
hermes config set   # Установить значение конфигурации
hermes config get   # Прочитать значение конфигурации
hermes gateway      # Запустить messaging gateway (Telegram, Discord, и т.д.)
hermes setup        # Полный setup wizard
hermes update       # Обновить до последней версии
hermes doctor       # Диагностика проблем
```

📖 **[Документация upstream →](https://hermes-agent.nousresearch.com/docs/)**

---

## CLI vs Messaging

| Действие                       | CLI                                           | Messaging                                                                       |
| ------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------- |
| Начать чат                     | `hermes`                                      | `hermes gateway setup` + `hermes gateway start`, затем сообщение боту           |
| Новая сессия                   | `/new` или `/reset`                           | `/new` или `/reset`                                                             |
| Сменить модель                 | `/model [provider:model]`                     | `/model [provider:model]`                                                       |
| Личность                       | `/personality [name]`                         | `/personality [name]`                                                           |
| Retry / undo                   | `/retry`, `/undo`                             | `/retry`, `/undo`                                                               |
| Compress / usage               | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                       |
| Skills                         | `/skills` или `/<skill-name>`                 | `/<skill-name>`                                                                 |
| Прервать                       | `Ctrl+C` или новое сообщение                  | `/stop` или новое сообщение                                                     |

Полный список команд: [CLI guide](https://hermes-agent.nousresearch.com/docs/user-guide/cli) и [Messaging Gateway guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging).

---

## Провайдеры

Hermes работает с любым провайдером — OpenRouter, OpenAI, Anthropic, DeepSeek, своим endpoint, или [Nous Portal](https://portal.nousresearch.com). Переключение: `hermes model` — без смены кода.

```bash
hermes model              # Интерактивный выбор провайдера и модели
# Примеры:
#   DeepSeek (free/дешёвые модели)
#   OpenRouter (агрегатор, 100+ провайдеров)
#   Свой endpoint (OpenAI-совместимый API)

hermes setup --portal    # Nous Portal: 300+ моделей + Tool Gateway (опционально)
```

---

## Разработка

```bash
# Клонировать форк
git clone https://github.com/DredusCLWN/hermes-agent-fork
cd hermes-agent

# Venv вне исходников (важно — venv внутри может быть уничтожен агентом)
uv venv ~/.hermes/venvs/hermes-dev --python 3.11
source ~/.hermes/venvs/hermes-dev/bin/activate    # Linux/macOS
# или: ~/.hermes/venvs/hermes-dev/Scripts/activate   # Windows

uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

### Upstream sync

```bash
scripts/merge-upstream.sh
```

Скрипт подтягивает изменения из `NousResearch/hermes-agent`, применяет `REMOVE_LIST` (предотвращает возврат удалённых модулей), и оставляет fork-specific файлы нетронутыми.

---

## Архитектура

Полная документация архитектуры: [upstream docs](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture).

Ключевые fork-specific файлы:

| Файл | Назначение |
|---|---|
| `fork-install.ps1` | Wrapper установщика форка |
| `scripts/merge-upstream.sh` | Upstream sync + REMOVE_LIST |
| `.github/workflows/release-node-dists.yml` | CI: сборка JS-dist на тег |
| `plugins/memory/holographic/store.py` | Версионирование schema + миграции |
| `agent/live_session_registry.py` | Live session tracking |
| `agent/refine_snapshot.py` | Refine snapshot logic |
| `tools/agent_message_tool.py` | Agent messaging tool |

---

## Community

- 🐛 [Issues](https://github.com/DredusCLWN/hermes-agent-fork/issues)
- 📚 [Skills Hub](https://agentskills.io)
- 💬 [Upstream Discord](https://discord.gg/NousResearch)

---

## License

MIT — see [LICENSE](LICENSE).

Upstream: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com).
