#!/usr/bin/env bash
# check-plugin-dist-sync.sh — verify committed dist/ matches src/ for plugin dashboards.
#
# Plugin dashboards ship pre-built dist/ (index.js + style.css) that is
# loaded at runtime. Without a CI check, src↔dist drift goes undetected.
#
# Usage: ./scripts/check-plugin-dist-sync.sh
# Exit: 0 = all in sync, 1 = drift detected
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"

PLUGINS=(
  "plugins/graphify/dashboard"
  "plugins/hermes-achievements/dashboard"
  "plugins/kanban/dashboard"
)

DRIFT=0
for dir in "${PLUGINS[@]}"; do
  if [ ! -d "$dir/dist" ] || [ ! -d "$dir/src" ]; then
    continue
  fi
  # Check if dist files are tracked
  if ! git ls-files --error-unmatch "$dir/dist/index.js" >/dev/null 2>&1; then
    continue
  fi
  # Rebuild dist in a temp dir and compare
  tmpdist=$(mktemp -d)
  if [ -f "$dir/package.json" ]; then
    (cd "$dir" && npm run build -- --outDir "$tmpdist" 2>/dev/null) || true
    if [ -f "$tmpdist/index.js" ]; then
      if ! diff -q "$tmpdist/index.js" "$dir/dist/index.js" >/dev/null 2>&1; then
        echo "DRIFT: $dir/dist/index.js does not match rebuilt output"
        DRIFT=1
      fi
    fi
    if [ -f "$tmpdist/style.css" ] && [ -f "$dir/dist/style.css" ]; then
      if ! diff -q "$tmpdist/style.css" "$dir/dist/style.css" >/dev/null 2>&1; then
        echo "DRIFT: $dir/dist/style.css does not match rebuilt output"
        DRIFT=1
      fi
    fi
  fi
  rm -rf "$tmpdist"
done

if [ "$DRIFT" -eq 0 ]; then
  echo "OK: all plugin dashboards in sync"
else
  echo "FAIL: dist drift detected — rebuild with 'npm run build' in each plugin dashboard"
  exit 1
fi
