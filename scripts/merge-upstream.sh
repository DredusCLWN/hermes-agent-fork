#!/usr/bin/env bash
# ============================================================================
# merge-upstream.sh — безопасный merge обновлений upstream в локальный форк.
#
# Проблема: форк сознательно УДАЛИЛ фичи (pets, cron-engine, voice/wake-word,
# acp_adapter) — а upstream их продолжает менять. Прямой git merge origin/main:
#   - молча вернёт удалённые файлы (или наделает modify/delete конфликтов)
#   - либо сломает сборку новым кодом, который ссылается на удалённые модули
#
# Политика:
#   dry-run (по умолчанию): ничего не меняет. Только отчёт — какие файлы
#     сольются чисто, какие останутся удалёнными (modify/delete -> git rm),
#     какие останутся человеку (содержательные конфликты).
#   --auto: реальный merge (--no-commit, НЕ коммитит сам), авто-разрешает
#     ТОЛЬКО modify/delete политикой "сохранить наше удаление", НЕ трогает
#     содержательные конфликты. Перед слиянием проверяет, что "чистые"
#     upstream-файлы не импортируют удалённые нами модули (иначе abort).
#     После merge — tsc + python import sanity; при поломке: git merge --abort.
#
# Безопасность:
#   - По умолчанию только чтение. Никаких изменений без явного --auto.
#   - Ref'ы не двигаются: fetch (только в --auto), слияние без коммита.
#   - Отчёт пишется в .merge-tmp/merge_report.txt (не засоряет git status).
#   - Перед abort всегда git merge --abort — рабочая копия возвращается как была.
#
# Использование:
#   ./scripts/merge-upstream.sh                  # dry-run: план + риски
#   ./scripts/merge-upstream.sh --auto           # реальный merge (без коммита)
#   ./scripts/merge-upstream.sh --auto --allow-dirty
#
# Зависимости: git, bash, python3 (для проверки импортов), node/tsc (для сборки).
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; CYA=$'\033[0;36m'; NC=$'\033[0m'
log()  { printf '%s%s%s\n' "$CYA" "$*" "$NC"; }
warn() { printf '%s%s%s\n' "$YELLOW" "$*" "$NC"; }
err()  { printf '%s%s%s\n' "$RED" "$*" "$NC" >&2; }
ok()   { printf '%s%s%s\n' "$GREEN" "$*" "$NC"; }

AUTO=0
ALLOW_DIRTY=0
for arg in "$@"; do
  case "$arg" in
    --auto) AUTO=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    *) err "Неизвестный аргумент: $arg"; exit 2 ;;
  esac
done

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
mkdir -p "$REPO/.merge-tmp"
REPORT="$REPO/.merge-tmp/merge_report.txt"
rm -f "$REPORT"
touch "$REPORT"
# trap: чистим только ВРЕМЕННЫЕ листы, отчёт merge_report.txt оставляем
# (иначе пользователь теряет план конфликтов после exit 0/3).
trap 'rm -f "$REPO/.merge-tmp"/mu_*.txt' EXIT

echo "==============================================================" | tee -a "$REPORT"
echo "Merge-guard отчёт  (fork: $REPO)" | tee -a "$REPORT"
date | tee -a "$REPORT"
echo "==============================================================" | tee -a "$REPORT"

# --- 0. Рабочий каталог чистый? --------------------------------------------
DIRTY=0
if [ -n "$(git status --porcelain)" ]; then
  DIRTY=1
fi
# Грязные файлы ДО merge — нужны в 7b (guard от перезатирания незакоммиченного)
git status --porcelain | awk '{print $2}' | sort -u > "$REPO/.merge-tmp/mu_dirty_pre.txt"
if [ "$DIRTY" -eq 1 ]; then
  if [ "$ALLOW_DIRTY" -eq 0 ]; then
    err "Рабочий каталог не чистый. Либо закоммить незакоммиченное, либо"
    err "  используй --allow-dirty, чтобы продолжить."
    exit 1
  else
    warn "Рабочий каталог грязный — продолжаю (--allow-dirty)."
    git status --porcelain | head -10 | sed 's/^/    /' | tee -a "$REPORT"
  fi
fi

# --- 1. fetch (только --auto) ------------------------------------------------
UPSTREAM="origin/main"
if [ "$AUTO" -eq 1 ]; then
  if ! git fetch origin --prune 2>&1 | tee -a "$REPORT"; then
    err "fetch не удался. Отказываюсь merge по стейлому origin/main."
    exit 7
  fi
else
  warn "dry-run: fetch не выполняю. Используй --auto для актуального origin/main."
fi

MERGE_BASE=$(git merge-base HEAD "$UPSTREAM")
HEAD_SHORT=$(git rev-parse --short HEAD)
UP_SHORT=$(git rev-parse --short "$UPSTREAM")
BASE_SHORT=$(git rev-parse --short "$MERGE_BASE")

log "HEAD=$HEAD_SHORT | upstream=$UP_SHORT | merge-base=$BASE_SHORT" | tee -a "$REPORT"

if [ "$(git rev-parse HEAD)" = "$(git rev-parse "$UPSTREAM")" ]; then
  ok "Ветка up-to-date с $UPSTREAM." | tee -a "$REPORT"
  exit 0
fi
if [ "$MERGE_BASE" = "$(git rev-parse "$UPSTREAM")" ]; then
  log "Ветка — прямой наследник; upstream не имеет новых коммитов относительно нас." | tee -a "$REPORT"
  log "Ничего сливать не надо (только наши изменения)." | tee -a "$REPORT"
  exit 0
fi

# --- 2. Классификация файлов по сторонам -------------------------------------
mapfile -t UP_FILES < <(git diff --name-only "$MERGE_BASE".."$UPSTREAM" | sort -u)
mapfile -t OUR_ALL  < <(git diff --name-only "$MERGE_BASE"..HEAD | sort -u)
mapfile -t OUR_DEL  < <(git diff --diff-filter=D --name-only "$MERGE_BASE"..HEAD | sort -u)

: > "$REPO/.merge-tmp/mu_clean.txt"
: > "$REPO/.merge-tmp/mu_moddel.txt"
: > "$REPO/.merge-tmp/mu_overlap.txt"
declare -A our_all_set our_del_set
for f in "${OUR_ALL[@]}"; do our_all_set["$f"]=1; done
for f in "${OUR_DEL[@]}"; do our_del_set["$f"]=1; done
for f in "${UP_FILES[@]}"; do
  if [[ -n "${our_all_set[$f]:-}" ]]; then
    if [[ -n "${our_del_set[$f]:-}" ]]; then
      printf '%s\n' "$f" >> "$REPO/.merge-tmp/mu_moddel.txt"
    else
      printf '%s\n' "$f" >> "$REPO/.merge-tmp/mu_overlap.txt"
    fi
  else
    printf '%s\n' "$f" >> "$REPO/.merge-tmp/mu_clean.txt"
  fi
done

: > "$REPO/.merge-tmp/mu_updel.txt"
mapfile -t UP_DEL < <(git diff --diff-filter=D --name-only "$MERGE_BASE".."$UPSTREAM" | sort -u)
for f in "${UP_DEL[@]}"; do
  if [[ -z "${our_del_set[$f]:-}" ]]; then
    printf '%s\n' "$f" >> "$REPO/.merge-tmp/mu_updel.txt"
  fi
done

N_CLEAN=$(wc -l < "$REPO/.merge-tmp/mu_clean.txt")
N_MODDEL=$(wc -l < "$REPO/.merge-tmp/mu_moddel.txt")
N_OVER=$(wc -l < "$REPO/.merge-tmp/mu_overlap.txt")
N_UPDEL=$(wc -l < "$REPO/.merge-tmp/mu_updel.txt")

log "" | tee -a "$REPORT"
log "План merge:" | tee -a "$REPORT"
printf '  %s  чисто (только upstream менял)       -> применится без вопросов\n' "$N_CLEAN"   | tee -a "$REPORT"
printf '  %s  удалено нами + изменено upstream    -> git rm (сохраняем удаление)\n' "$N_MODDEL" | tee -a "$REPORT"
printf '  %s  изменено с обеих сторон             -> РУЧНЫЕ конфликты (не трогаю)\n' "$N_OVER"   | tee -a "$REPORT"
printf '  %s  удалено upstream (мы не трогали)    -> удалится из форка\n' "$N_UPDEL"   | tee -a "$REPORT"

if [ "$N_OVER" -gt 0 ]; then
  warn "Содержательные конфликты (нужна рука):" | tee -a "$REPORT"
  head -30 "$REPO/.merge-tmp/mu_overlap.txt" | sed 's/^/    /' | tee -a "$REPORT"
  if [ "$N_OVER" -gt 30 ]; then
    warn "    ...и ещё $((N_OVER-30)) (см. merge_report.txt)" | tee -a "$REPORT"
  fi
fi
if [ "$N_MODDEL" -gt 0 ]; then
  log "modify/delete — сохраняю наше удаление (git rm):" | tee -a "$REPORT"
  cat "$REPO/.merge-tmp/mu_moddel.txt" | sed 's/^/    /' | tee -a "$REPORT"
fi

# --- 3. Проверка: чистые файлы импортируют удалённые модули? -----------------
# (самое опасное: файл из upstream применится, а его импорт ссылается на удалённый модуль)
PY3=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY3="$c"; break; fi
done
if [ -n "$PY3" ]; then
  PYOUT=$("$PY3" - "$REPO" "$REPO/.merge-tmp" <<'PYEOF'
import sys, re, os
repo = sys.argv[1]
tmp = sys.argv[2]
up_files = [l for l in open(os.path.join(tmp, "mu_clean.txt")).read().splitlines() if l.strip()]
del_files = [l for l in open(os.path.join(tmp, "mu_moddel.txt")).read().splitlines() if l.strip()]
del_mods = set()
for f in del_files:
    if f.endswith(".py"):
        del_mods.add(f[:-3].replace("/", "."))
danger = []
for f in up_files:
    if not f.endswith((".py", ".pyi")): continue
    if f.startswith(("tests/", "website/", "docs/", "scripts/")): continue
    p = os.path.join(repo, f)
    if not os.path.exists(p): continue
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    for m in re.finditer(r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))', text, re.M):
        mod = m.group(1) or m.group(2)
        segs = mod.split(".")
        for i in range(1, len(segs) + 1):
            pref = ".".join(segs[:i])
            if pref in del_mods:
                danger.append((f, pref))
                break
if danger:
    print("DANGER")
    for f, m in sorted(set(danger)):
        print(f"  {f} -> {m}")
else:
    print("SAFE")
PYEOF
  )
  if echo "$PYOUT" | grep -q "^DANGER"; then
    NOT_SAFE=1
    echo "$PYOUT" | grep '^  ' | sed 's/^  //' > "$REPO/.merge-tmp/mu_danger.txt"
    warn "ОПАСНО: чистые upstream-файлы импортируют удалённые модули." | tee -a "$REPORT"
    warn "Их НЕ возьму из upstream (останется наша версия / файл не создастся):" | tee -a "$REPORT"
    cat "$REPO/.merge-tmp/mu_danger.txt" | cut -d' ' -f1 | sed 's/^/    /' | tee -a "$REPORT"
  else
    ok "Чистые файлы не импортируют удалённые модули." | tee -a "$REPORT"
  fi
else
  warn "python не найден — проверку импортов пропускаю."
fi

# --- 4. Upstream удалил файлы, которые мы держим ---------------------------------
if [ "$N_UPDEL" -gt 0 ]; then
  warn "Файлы, которые upstream удаляет (удалятся из форка):" | tee -a "$REPORT"
  cat "$REPO/.merge-tmp/mu_updel.txt" | sed 's/^/    /' | tee -a "$REPORT"
fi

log "" | tee -a "$REPORT"
warn "Классификация завершена. Отчёт: $REPORT"

if [ "$AUTO" -eq 0 ]; then
  warn "dry-run (ничего не изменилось). Для реального merge: ./scripts/merge-upstream.sh --auto"
  warn "Отчёт: $REPORT"
  exit 0
fi

# ============================================================================
# 5. РЕАЛЬНЫЙ MERGE (только с --auto)
# ============================================================================
if [ "${NOT_SAFE:-0}" -eq 1 ] && [ ! -s "$REPO/.merge-tmp/mu_danger.txt" ]; then
  err "Отказ: найдены импорты удалённых модулей в чистых файлах."
  err "merge НЕ выполняется. Разберись вручную: отчёт $REPORT."
  exit 2
fi

log "=== Merge (--no-commit, политика: удаления сохраняем) ==="
if git merge --no-commit --no-ff "$UPSTREAM" > /tmp/mu_merge_out.txt 2>&1; then
  ok "Merge без автоматических конфликтов."
else
  warn "Merge: есть unmerged paths, обрабатываю..."
fi
grep -vE "^Updating|^Fast-forward|^$" /tmp/mu_merge_out.txt | head -15 | tee -a "$REPORT" || true

# 5b. Критично: merge мог сорваться на pre-merge проверке (грязный файл в overlap)
#     — тогда MERGE_HEAD отсутствует, и любые git rm НИЖЕ испортят рабочую копию.
if ! git rev-parse --verify MERGE_HEAD >/dev/null 2>&1; then
  err "merge НЕ начался (MERGE_HEAD отсутствует). Возможные причины:"
  err "  - незакоммиченные правки в файле из overlap (с обеих сторон)"
  err "  - conflicts на стадии pre-merge."
  err "Рабочая копия НЕ тронута. Разберись с грязными файлами, затем повтори."
  err "Список грязных файлов:"
  git status --porcelain | head -20 | sed 's/^/    /'
  exit 6
fi

# 6. modify/delete -> сохраняем наше удаление
if [ -s "$REPO/.merge-tmp/mu_moddel.txt" ]; then
  log "modify/delete: git rm (сохраняем удаление)..."
  while IFS= read -r f; do
    git rm -f "$f" >/dev/null 2>&1 || true
  done < "$REPO/.merge-tmp/mu_moddel.txt"
fi
# 7. upstream удалил, мы не удаляли -> применяем их удаление
if [ -s "$REPO/.merge-tmp/mu_updel.txt" ]; then
  while IFS= read -r f; do
    git rm -f "$f" >/dev/null 2>&1 || true
  done < "$REPO/.merge-tmp/mu_updel.txt"
fi

# 7b. REMOVE_LIST — paths we deliberately removed and want to STAY removed.
#     Upstream may re-add them; this enforces our deletion policy.
REMOVE_LIST=(
  "cron/"
  "acp_adapter/"
  "locales/"
  "agent/i18n.py"
  "agent/copilot_acp_client.py"
  "gateway/wake.py"
  "tools/wakewords/"
  "plugins/cron_providers/"
  "tests/cron/"
  "tests/acp/"
)
log "REMOVE_LIST: enforcing deletion of removed modules..."
for pattern in "${REMOVE_LIST[@]}"; do
  while IFS= read -r f; do
    if [ -n "$f" ]; then
      git rm -f -- "$f" >/dev/null 2>&1 || true
      log "  removed: $f"
    fi
  done < <(git ls-files -- "$pattern")
done

# 7c. DANGER-файлы: upstream-версия ссылается на удалённые нами модули.
#     Оставляем НАШУ версию (или не создаём новый файл) — иначе поломка.
#     НО: если файл имеет незакоммиченные правки — его не трогаем (guard).
if [ -s "$REPO/.merge-tmp/mu_danger.txt" ]; then
  log "Исключаю DANGER-файлы (наша версия / без создания):"
  while IFS= read -r line; do
    f="${line%% *}"
    # Guard: были ли у файла незакоммиченные правки ДО merge?
    # После merge git status не пуст для ЛЮБОГО слитого чисто файла,
    # поэтому проверяем по снимку грязи, снятому до merge (секция 0).
    if grep -qxF "$f" "$REPO/.merge-tmp/mu_dirty_pre.txt"; then
      warn "  ! $f был грязным до merge — НЕ трогаю, реши вручную"
      continue
    fi
    if git cat-file -e "HEAD:$f" 2>/dev/null; then
      git restore --source=HEAD --staged --worktree -- "$f" >/dev/null 2>&1 || true
      warn "  restore(наша версия): $f"
    else
      git rm -f -- "$f" >/dev/null 2>&1 || true
      warn "  не создаю: $f"
    fi
  done < "$REPO/.merge-tmp/mu_danger.txt"
fi

# 8. Содержательные конфликты — НЕ решаем автоматически
left=$(git ls-files -u | wc -l)
if [ "$left" -gt 0 ]; then
  err "Осталось $left нерешённых содержательных конфликтов. merge НЕ закоммичен."
  err "Разрешай вручную, затем: git add <файлы> && git commit"
  exit 3
fi

# 9. Верификация: tsc (desktop) + базовые импорты. Поломка -> abort
log "Проверка сборки (tsc)..."
if command -v node >/dev/null 2>&1 && [ -d apps/desktop ]; then
  if (cd apps/desktop && ../../node_modules/.bin/tsc --noEmit -p tsconfig.json > /tmp/mu_tsc.txt 2>&1); then
    ok "tsc OK."
  else
    err "tsc СЛОМАЛСЯ после merge:"; head -30 /tmp/mu_tsc.txt >&2
    err "Откатываю merge."; git merge --abort; exit 4
  fi
else
  warn "node/tsc нет — пропускаю tsc."
fi

log "python import sanity..."
if command -v python >/dev/null 2>&1; then
  for m in hermes_cli.main agent.agent_init tools.registry; do
    if python -c "import $m" >/tmp/mu_py.txt 2>&1; then
      ok "  import $m OK"
    else
      err "import $m FAILED:"; tail -5 /tmp/mu_py.txt >&2
      err "Откатываю merge."; git merge --abort; exit 5
    fi
  done
fi

log ""
ok "Merge готов к ревью, НЕ закоммичен. Смотри:"
warn "  git status --short | head -50"
ok "Если всё ок:  git commit"
warn "Если передумал: git merge --abort"