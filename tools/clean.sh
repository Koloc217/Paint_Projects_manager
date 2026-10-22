#!/usr/bin/env bash
# Moves out of the project folder the files that are neither in git nor listed in .gitignore
# (leftovers of older versions: removed modules, old folders and so on).
#
#   tools/clean.sh            show the list and ask for confirmation
#   tools/clean.sh --yes      without asking
#   tools/clean.sh --dry-run  only show
#
# Files are not deleted for good: they are moved to ../<folder>-leftovers-<date>/ with their paths kept.
# App data (tracker.db, photos/, config.json, .env, logs, etc.) is listed in .gitignore
# and is never touched.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

YES=0
DRY=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) YES=1 ;;
        --dry-run|-n) DRY=1 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Неизвестный аргумент: $arg"; exit 1 ;;
    esac
done

mapfile -t FILES < <(git ls-files --others --exclude-standard)
if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "✅ Лишних файлов нет"
    exit 0
fi

echo "Файлы вне git (будут перенесены):"
printf '  %s\n' "${FILES[@]}"
[[ $DRY -eq 1 ]] && exit 0

if [[ $YES -eq 0 ]]; then
    read -r -p "Перенести ${#FILES[@]} файл(ов)? [y/N] " answer
    [[ "$answer" =~ ^[YyДд]$ ]] || { echo "Отменено"; exit 0; }
fi

DEST="$(dirname "$APP_DIR")/$(basename "$APP_DIR")-leftovers-$(date +%Y%m%d-%H%M%S)"
for f in "${FILES[@]}"; do
    mkdir -p "$DEST/$(dirname "$f")"
    mv -- "$f" "$DEST/$f"
done
# Remove folders that became empty after the move (only those; other empty folders are left alone)
for f in "${FILES[@]}"; do
    d="$(dirname "$f")"
    while [[ "$d" != "." ]] && rmdir -- "$d" 2>/dev/null; do d="$(dirname "$d")"; done
done

echo "✅ Перенесено в $DEST"
echo "Если всё работает, папку можно удалить: rm -rf \"$DEST\""
