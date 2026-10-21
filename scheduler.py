"""Встроенный планировщик (замена cron) и запуск скриптов через subprocess.

Расписание берётся из config.json (секция SCHEDULE) и настраивается в UI.
Скрипты запускаются тем же интерпретатором, что и приложение (sys.executable),
поэтому путь к venv нигде не прописан.
"""
import os
import sys
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime

from common import BASE_DIR, DAY_KEYS, load_config, normalize_schedule

LOG_MAX_BYTES = 1_000_000
JOB_TIMEOUT_SEC = 180
TICK_SEC = 20

EVERY_DAY = frozenset(range(7))

_background_tasks = set()
_running_jobs = set()


@dataclass(frozen=True)
class Job:
    id: str
    script: str
    days: frozenset
    time: str  # "HH:MM"
    args: tuple = ()


def build_jobs(cfg: dict) -> list:
    s = normalize_schedule(cfg.get("SCHEDULE"))
    jobs = []
    if s["notify_enabled"]:
        # по одному запуску на каждый включённый день недели, со своим временем
        for weekday, key in enumerate(DAY_KEYS):
            day = s["notify_days"][key]
            if day["enabled"]:
                jobs.append(Job(f"notify_{key}", "notify.py", frozenset({weekday}), str(day["time"]), ("--source=scheduler",)))
    if s.get("backup_enabled", False):
        jobs.append(Job("backup", "backup.py", EVERY_DAY, str(s.get("backup_time", "03:00"))))
    return jobs


def due_jobs(cfg: dict, now: datetime, last_fired: dict) -> list:
    """Какие задачи пора запустить в эту минуту (каждая — не чаще раза в минуту)."""
    stamp = now.strftime("%Y-%m-%d %H:%M")
    hhmm = now.strftime("%H:%M")
    due = []
    for job in build_jobs(cfg):
        if now.weekday() in job.days and job.time == hhmm and last_fired.get(job.id) != stamp:
            last_fired[job.id] = stamp
            due.append(job)
    return due


def seconds_to_next_run(cfg=None, now=None):
    """Секунд до ближайшего запуска по расписанию (None, если расписание пустое)."""
    cfg = cfg or load_config()
    now = now or datetime.now()
    best = None
    for job in build_jobs(cfg):
        try:
            hh, mm = (int(x) for x in job.time.split(":"))
            at = dtime(hh, mm)
        except (ValueError, TypeError):
            continue
        for offset in range(0, 8):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in job.days:
                continue
            run_at = datetime.combine(day, at)
            if run_at < now:
                continue
            delta = (run_at - now).total_seconds()
            if best is None or delta < best:
                best = delta
            break
    return best


def _rotate_log(path: str) -> None:
    try:
        if os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


async def run_script(script: str, args=(), timeout: float = JOB_TIMEOUT_SEC):
    """Запускает скрипт из папки проекта отдельным процессом, вывод — в <имя>.log."""
    script_path = os.path.join(BASE_DIR, script)
    log_path = os.path.join(BASE_DIR, os.path.splitext(script)[0] + ".log")
    _rotate_log(log_path)
    args = [str(a) for a in args]

    with open(log_path, "ab") as log:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.write(f"\n[{stamp}] ▶ {script} {' '.join(args)}\n".encode("utf-8"))
        log.flush()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path, *args,
            cwd=BASE_DIR, stdout=log, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ⏱ {script}: превышен таймаут {int(timeout)} c, процесс убит\n".encode("utf-8"))
        else:
            log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ■ {script}: код выхода {proc.returncode}\n".encode("utf-8"))
    return proc.returncode


def spawn_script(script: str, args=()):
    """Запустить скрипт в фоне (не блокируя event loop); вызывать из async-кода."""
    task = asyncio.create_task(_safe_run(script, args))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _safe_run(script: str, args=()):
    try:
        await run_script(script, args)
    except Exception as e:
        print(f"[SCHEDULER ERROR] {script}: {e!r}")


async def _run_job(job: Job):
    if job.id in _running_jobs:
        print(f"[SCHEDULER] {job.id} ещё выполняется — пропуск")
        return
    _running_jobs.add(job.id)
    try:
        await _safe_run(job.script, job.args)
    finally:
        _running_jobs.discard(job.id)


async def scheduler_loop():
    print("⏰ Планировщик запущен (расписание — в настройках приложения)")
    last_fired = {}
    while True:
        try:
            for job in due_jobs(load_config(), datetime.now(), last_fired):
                print(f"[SCHEDULER] запуск {job.id} ({job.script}) в {job.time}")
                task = asyncio.create_task(_run_job(job))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e!r}")
        await asyncio.sleep(TICK_SEC)
