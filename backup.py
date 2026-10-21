import os
import httpx
import json
from datetime import datetime
from dotenv import load_dotenv
from database import SessionLocal, Project, Task, ensure_schema
from common import live_projects, live_tasks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Загружаем переменные из .env
load_dotenv(os.path.join(BASE_DIR, ".env"))
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL")

def main():
    ensure_schema()
    print(f"[{datetime.now()}] Начинаю резервное копирование...")

    if not APPS_SCRIPT_URL:
        print("❌ Ошибка: APPS_SCRIPT_URL не найден в файле .env!")
        return

    db = SessionLocal()
    try:
        projects = live_projects(db).all()
        tasks = live_tasks(db).all()

        proj_data = [{
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "stage": getattr(p, 'stage', '—') or '—',
            "priority": p.priority,
            "estimate": p.estimate.strftime("%Y-%m-%d") if p.estimate else "",
            "token": getattr(p, 'token', 0) or 0,
            "token_reward": getattr(p, 'token_reward', 0) or 0,
            "paint_notes": getattr(p, 'paint_notes', '') or '',
            "completed_at": p.completed_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(p, 'completed_at', None) else ""
        } for p in projects]

        task_data = [{
            "id": t.id,
            "project_id": t.project_id,
            "task_name": t.task_name,
            "deadline": t.deadline.strftime("%Y-%m-%d") if t.deadline else "",
            "status": t.status,
            "postponed": getattr(t, 'postponed', 0) or 0,
            "completed_at": t.completed_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(t, 'completed_at', None) else ""
        } for t in tasks]

        payload = {
            "projects": proj_data,
            "tasks": task_data,
            "timestamp": datetime.now().isoformat()
        }

        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            resp = client.post(APPS_SCRIPT_URL, json=payload)
            if resp.status_code in [200, 201]:
                print(f"[{datetime.now()}] ✅ Бэкап успешно отправлен в Google Sheets!")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка сервера Google (Код {resp.status_code}): {resp.text[:300]}")

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Критическая ошибка бэкапа: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
