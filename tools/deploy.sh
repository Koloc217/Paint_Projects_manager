#!/usr/bin/env bash
# Выкладка ветки из GitHub на машину, где работает Project Hub (Linux + systemd).
#
#   tools/deploy.sh                      # main
#   tools/deploy.sh feature/postpone-snark
#   tools/deploy.sh --rollback           # вернуть коммит, который стоял до последней выкладки
#   tools/deploy.sh --force main         # выложить, даже если нет новых коммитов или есть локальные правки
#
# Что делает: копия базы в backups/ → git fetch → переключение на свежую версию ветки →
# pip install, если поменялся requirements.txt → перезапуск сервиса → проверка, что сервер отвечает.
# Данные (tracker.db, photos/, config.json, .env, auth.json) в git не входят и не трогаются.
#
# Настройки через переменные окружения:
#   VENV=/home/ops13/venv      путь к виртуальному окружению (по умолчанию .venv в папке проекта или ../venv)
#   SERVICE=tracker            имя systemd-сервиса
#   PORT=8000                  порт для проверки после перезапуска
#   KEEP_BACKUPS=10            сколько копий базы хранить
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

SERVICE="${SERVICE:-tracker}"
PORT="${PORT:-8000}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
if [[ -z "${VENV:-}" ]]; then
    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then VENV="$APP_DIR/.venv"
    elif [[ -x "$APP_DIR/../venv/bin/python" ]]; then VENV="$(cd "$APP_DIR/../venv" && pwd)"
    else echo "❌ Не найдено виртуальное окружение. Укажите VENV=/путь/к/venv"; exit 1
    fi
fi
PY="$VENV/bin/python"
PREV_FILE="$APP_DIR/.deploy_prev"

FORCE=0
ROLLBACK=0
BRANCH=""
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --rollback) ROLLBACK=1 ;;
        -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "Неизвестный флаг: $arg"; exit 1 ;;
        *) BRANCH="$arg" ;;
    esac
done
BRANCH="${BRANCH:-main}"

if [[ $FORCE -eq 0 ]] && ! git diff --quiet HEAD --; then
    echo "❌ В папке есть локальные изменения в файлах из git:"
    git status --short --untracked-files=no
    echo "Сохраните их (git stash) или запустите с --force, тогда они будут потеряны."
    exit 1
fi

backup_db() {
    [[ -f tracker.db ]] || return 0
    mkdir -p backups
    local dst="backups/tracker-$(date +%Y%m%d-%H%M%S).db"
    # sqlite3 backup API: корректная копия даже в режиме WAL и при работающем приложении
    "$PY" - "$dst" <<'EOF'
import sqlite3, sys
src = sqlite3.connect("tracker.db")
dst = sqlite3.connect(sys.argv[1])
with dst:
    src.backup(dst)
dst.close(); src.close()
EOF
    echo "💾 Копия базы: $dst"
    ls -1t backups/tracker-*.db 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f
}

OLD_COMMIT="$(git rev-parse HEAD)"
OLD_REQ="$(git rev-parse HEAD:requirements.txt 2>/dev/null || echo none)"

if [[ $ROLLBACK -eq 1 ]]; then
    [[ -f "$PREV_FILE" ]] || { echo "❌ Нет информации о прошлой выкладке"; exit 1; }
    TARGET="$(cat "$PREV_FILE")"
    git cat-file -e "$TARGET^{commit}" 2>/dev/null || { echo "❌ Коммит $TARGET не найден локально"; exit 1; }
    echo "↩️  Откат на $(git log -1 --format='%h %s' "$TARGET")"
    backup_db
    git checkout -q --detach "$TARGET"
else
    echo "⬇️  Загрузка ветки $BRANCH..."
    git fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
    TARGET="$(git rev-parse "origin/$BRANCH")"
    if [[ "$TARGET" == "$OLD_COMMIT" && $FORCE -eq 0 ]]; then
        echo "✅ Уже стоит последняя версия $BRANCH ($(git log -1 --format='%h %s'))"
        exit 0
    fi
    backup_db
    git checkout -q -f -B "$BRANCH" "origin/$BRANCH"
fi
echo "$OLD_COMMIT" > "$PREV_FILE"

NEW_REQ="$(git rev-parse HEAD:requirements.txt 2>/dev/null || echo none)"
if [[ "$NEW_REQ" != "$OLD_REQ" || $FORCE -eq 1 ]]; then
    echo "📦 Обновление зависимостей..."
    "$PY" -m pip install -q -r requirements.txt
fi

echo "🔄 Перезапуск $SERVICE..."
sudo systemctl restart "$SERVICE"

# Ждём, пока сервер ответит (HTTPS, если включён, иначе HTTP)
ok=0
for _ in $(seq 1 20); do
    sleep 1
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://127.0.0.1:$PORT/" 2>/dev/null || true)"
    [[ "$code" == "000" || -z "$code" ]] && code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)"
    if [[ "$code" =~ ^[23] ]]; then ok=1; break; fi
done

echo
git log -1 --format='Сейчас: %h %s (%cr)'
git log --oneline "$OLD_COMMIT..HEAD" 2>/dev/null | head -15 | sed 's/^/  + /'
if [[ $ok -eq 1 ]]; then
    echo "✅ Сервер отвечает на порту $PORT"
else
    echo "⚠️  Сервер не ответил за 20 секунд. Последние строки журнала:"
    sudo journalctl -u "$SERVICE" -n 30 --no-pager || true
    echo "Откатиться: tools/deploy.sh --rollback"
    exit 1
fi
