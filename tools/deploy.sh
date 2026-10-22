#!/usr/bin/env bash
# Deploys a branch from GitHub to the machine that runs Project Hub (Linux + systemd).
#
#   tools/deploy.sh                      # main
#   tools/deploy.sh feature/postpone-snark
#   tools/deploy.sh --rollback           # go back to the commit that was deployed before the last deploy
#   tools/deploy.sh --force main         # deploy even if there are no new commits or there are local edits
#
# What it does: database copy into backups/ → git fetch → switch to the latest version of the branch →
# pip install if requirements.txt changed → restart the service → check that the server answers.
# Data (tracker.db, photos/, config.json, .env, auth.json) is not in git and is not touched.
#
# Settings through environment variables:
#   VENV=/home/ops13/venv      path to the virtual environment (default: .venv in the project folder or ../venv)
#   SERVICE=tracker            systemd service name
#   PORT=8000                  port to check after the restart
#   KEEP_BACKUPS=10            how many database copies to keep
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
    # sqlite3 backup API: a consistent copy even in WAL mode and while the app is running
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

# Wait until the server answers (HTTPS if enabled, otherwise HTTP)
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
