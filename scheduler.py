"""Встроенный планировщик (замена cron) и запуск скриптов через subprocess.

Расписание берётся из config.json (секция SCHEDULE) и настраивается в UI.
Скрипты запускаются тем же интерпретатором, что и приложение (sys.executable),
поэтому путь к venv нигде не прописан.
"""
import os
import sys
import json
import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime

import logs
from common import BASE_DIR, DAY_KEYS, load_config, normalize_schedule

JOB_TIMEOUT_SEC = 180
TICK_SEC = 20
STATE_FILE = os.path.join(BASE_DIR, "scheduler_state.json")
HEARTBEAT_SEC = 300          # как часто отмечаем «приложение работает» (для поиска пропущенных запусков)
CATCHUP_DELAY_SEC = 15       # после старта подождать, пока поднимутся веб и бот
STAMP_FMT = "%Y-%m-%d %H:%M"
ALIVE_FMT = "%Y-%m-%d %H:%M:%S"

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
    # Догон после простоя: сколько часов после времени запуска он ещё имеет смысл (0 — не догонять)
    # и только ли в тот же календарный день (для вечерней сводки: ночью она уже не нужна).
    catchup_hours: float = 0
    catchup_same_day: bool = False


def build_jobs(cfg: dict) -> list:
    s = normalize_schedule(cfg.get("SCHEDULE"))
    jobs = []
    if s["notify_enabled"]:
        # по одному запуску на каждый включённый день недели, со своим временем
        for weekday, key in enumerate(DAY_KEYS):
            day = s["notify_days"][key]
            if day["enabled"]:
                jobs.append(Job(f"notify_{key}", "notify.py", frozenset({weekday}), str(day["time"]), ("--source=scheduler",),
                                catchup_hours=3, catchup_same_day=True))
    if s.get("backup_enabled", False):
        jobs.append(Job("backup", "backup.py", EVERY_DAY, str(s.get("backup_time", "03:00")), catchup_hours=24))
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


def _parse_time(value):
    try:
        hh, mm = (int(x) for x in str(value).split(":"))
        return dtime(hh, mm)
    except (ValueError, TypeError):
        return None


def missed_jobs(cfg: dict, now: datetime, last_alive, last_fired: dict) -> list:
    """Запуски, которые пришлись на время, когда приложение не работало.

    last_alive — когда приложение в последний раз отмечалось живым (None — первый запуск: догонять нечего).
    Берётся последний пропущенный запуск каждой задачи (не все подряд), если он ещё не устарел
    (catchup_hours, catchup_same_day) и не был выполнен. Возвращает список (задача, плановое время).
    """
    if last_alive is None:
        return []
    result = []
    for job in build_jobs(cfg):
        at = _parse_time(job.time)
        if not job.catchup_hours or at is None:
            continue
        best = None
        day = last_alive.date()
        while day <= now.date():
            run_at = datetime.combine(day, at)
            if day.weekday() in job.days and last_alive < run_at <= now:
                best = run_at
            day += timedelta(days=1)
        if best is None:
            continue
        if now - best > timedelta(hours=job.catchup_hours):
            continue
        if job.catchup_same_day and best.date() != now.date():
            continue
        if last_fired.get(job.id, "") >= best.strftime(STAMP_FMT):
            continue
        result.append((job, best))
    return result


def load_state() -> dict:
    """{'alive': datetime | None, 'fired': {id задачи: 'ГГГГ-ММ-ДД ЧЧ:ММ'}}; битый или пустой файл — как первый запуск."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        alive = datetime.strptime(raw["alive"], ALIVE_FMT)
        fired = {str(k): str(v) for k, v in dict(raw.get("fired", {})).items()}
        return {"alive": alive, "fired": fired}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"alive": None, "fired": {}}


def save_state(alive: datetime, fired: dict) -> None:
    """Атомарная запись, как у config.json."""
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".sched.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"alive": alive.strftime(ALIVE_FMT), "fired": fired}, f, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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


async def run_script(script: str, args=(), timeout: float = JOB_TIMEOUT_SEC):
    """Запускает скрипт из папки проекта отдельным процессом, вывод — в logs/<имя>.log (с ротацией)."""
    script_path = os.path.join(BASE_DIR, script)
    log_path = logs.log_path(os.path.splitext(script)[0] + ".log")
    logs.rotate_file(log_path)
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


def _start_job(job: Job) -> None:
    task = asyncio.create_task(_run_job(job))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _save_state_safe(alive: datetime, fired: dict) -> None:
    try:
        save_state(alive, fired)
    except OSError as e:
        print(f"[SCHEDULER] не удалось сохранить состояние: {e!r}")


async def scheduler_loop():
    print("⏰ Планировщик запущен (расписание — в настройках приложения)")
    state = load_state()
    last_fired = state["fired"]
    last_alive = state["alive"]
    last_saved = datetime.now()

    # Догон: запуски, которые пришлись на время, когда приложение было выключено
    await asyncio.sleep(CATCHUP_DELAY_SEC)
    try:
        now = datetime.now()
        for job, run_at in missed_jobs(load_config(), now, last_alive, last_fired):
            print(f"[SCHEDULER] догон {job.id} ({job.script}): плановое время {run_at:%Y-%m-%d %H:%M}, приложение не работало")
            last_fired[job.id] = run_at.strftime(STAMP_FMT)
            _start_job(job)
        _save_state_safe(now, last_fired)
        last_saved = now
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[SCHEDULER ERROR] догон: {e!r}")

    while True:
        try:
            now = datetime.now()
            fired_now = False
            for job in due_jobs(load_config(), now, last_fired):
                print(f"[SCHEDULER] запуск {job.id} ({job.script}) в {job.time}")
                _start_job(job)
                fired_now = True
            if fired_now or (now - last_saved).total_seconds() >= HEARTBEAT_SEC:
                _save_state_safe(now, last_fired)
                last_saved = now
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e!r}")
        await asyncio.sleep(TICK_SEC)
