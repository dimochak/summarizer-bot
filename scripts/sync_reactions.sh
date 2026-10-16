#!/usr/bin/env bash
# Заливає картинки реакцій з assets/reactions/ на том Fly (/app/data/reactions).
#
# Лише додає: файли, яких на сервері ще немає. Те, що є на сервері, але
# зникло локально, скрипт не видаляє, а тільки показує. Перезапускати бота
# не треба: теку перечитують при кожній реакції.
#
#   scripts/sync_reactions.sh            # усі теки
#   scripts/sync_reactions.sh trump      # лише одна
set -euo pipefail

APP="${FLY_APP:-xxl-bot-summarizer}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)/assets/reactions"
REMOTE_ROOT="/app/data/reactions"
MEDIA_RE='\.(jpe?g|png|webp|gif|mp4)$'

if [[ $# -gt 0 ]]; then
  rules=("$@")
else
  rules=()
  for dir in "$LOCAL_ROOT"/*/; do rules+=("$(basename "$dir")"); done
fi

for rule in "${rules[@]}"; do
  local_dir="$LOCAL_ROOT/$rule"
  remote_dir="$REMOTE_ROOT/$rule"
  [[ -d "$local_dir" ]] || { echo "Немає теки $local_dir" >&2; exit 1; }

  echo "== $rule"
  remote_files="$(fly ssh console -a "$APP" -q -C "sh -c 'mkdir -p $remote_dir && ls -1 $remote_dir'" | tr -d '\r')"

  uploaded=0
  for path in "$local_dir"/*; do
    name="$(basename "$path")"
    [[ -f "$path" ]] || continue
    echo "$name" | grep -qiE "$MEDIA_RE" || continue
    if grep -qxF "$name" <<<"$remote_files"; then
      continue
    fi
    echo "  + $name"
    fly ssh sftp put -a "$APP" -q "$path" "$remote_dir/$name"
    uploaded=$((uploaded + 1))
  done
  echo "  залито: $uploaded"

  while IFS= read -r name; do
    if [[ -n "$name" && ! -e "$local_dir/$name" ]]; then
      echo "  ! на сервері, але не локально: $name"
    fi
  done <<<"$remote_files"
done
