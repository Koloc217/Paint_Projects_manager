#!/usr/bin/env bash
# Убирает из папки проекта файлы, которых нет в git и которые не указаны в .gitignore
# (остатки старых версий: удалённые модули, старые папки и т.п.).
#
#   tools/clean.sh            показать список и спросить подтверждение
#   tools/clean.sh --yes      без вопроса
#   tools/clean.sh --dry-run  только показать
#
# Файлы не удаляются насовсем, а переносятся в ../<папка>-leftovers-<дата>/ с сохранением путей.
# Данные приложения (tracker.db, photos/, config.json, .env, журналы и т.д.) указаны в .gitignore
# и сюда не попадают.
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
# Убираем папки, которые опустели после переноса (только их, другие пустые папки не трогаем)
for f in "${FILES[@]}"; do
    d="$(dirname "$f")"
    while [[ "$d" != "." ]] && rmdir -- "$d" 2>/dev/null; do d="$(dirname "$d")"; done
done

echo "✅ Перенесено в $DEST"
echo "Если всё работает, папку можно удалить: rm -rf \"$DEST\""
