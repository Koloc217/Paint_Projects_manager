"""Canonical values and limits shared by the web app, the bot and the jobs. No logic, no imports from the app."""

# Canonical values: technical codes stored in the database and used in the logic and the bot's callback_data.
# Labels come from locales.json ("status.<code>", "stage.<code>"), see i18n.tv().
STATUS_PLANNED = "planned"
STATUS_IN_WORK = "in_work"
STATUS_ON_HOLD = "on_hold"
STATUS_FINISHED = "finished"
PROJECT_STATUSES = (STATUS_PLANNED, STATUS_IN_WORK, STATUS_ON_HOLD, STATUS_FINISHED)

TASK_NOT_STARTED = "not_started"
TASK_IN_PROGRESS = "in_progress"
TASK_STATUSES = (TASK_NOT_STARTED, TASK_IN_PROGRESS, STATUS_FINISHED)

NO_STAGE = "none"
STAGES = (NO_STAGE, "printing", "sanding", "assembly", "priming", "painting", "finishing")   # finishing: touches before completion
PRIORITIES = ("Low", "Medium", "High", "Critical")

# Values written by older versions (Russian strings) -> current codes.
# Used by the one-time database migration and to accept old input (bot buttons still in the chat, old clients).
LEGACY_VALUES = {
    "В планах": STATUS_PLANNED, "В работе": STATUS_IN_WORK, "На паузе": STATUS_ON_HOLD,
    "Закончен": STATUS_FINISHED, "Завершено": STATUS_FINISHED, "Завершен": STATUS_FINISHED,
    "Выполнено": STATUS_FINISHED, "Completed": STATUS_FINISHED, "completed": STATUS_FINISHED,
    "Не начато": TASK_NOT_STARTED, "В процессе": TASK_IN_PROGRESS,
    "—": NO_STAGE, "Без стадии": NO_STAGE,
    "Печать": "printing", "Шлифовка": "sanding", "Сборка": "assembly",
    "Грунт": "priming", "Покрас": "painting", "Доводка": "finishing",
}

# Every spelling of "finished": the current code plus what older versions could have written
DONE_STATUSES = (STATUS_FINISHED, "закончен", "завершено", "завершен", "выполнено", "completed")


def normalize_value(value):
    """A status or stage as a current code: old Russian values are converted, anything else is returned as is."""
    if value is None:
        return None
    s = str(value).strip()
    return LEGACY_VALUES.get(s, s)


def is_done(status) -> bool:
    return str(status).lower().strip() in DONE_STATUSES


DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # index = datetime.weekday(); labels: locales "day.<key>"
DATE_FORMATS = {"dmy": "%d.%m.%Y", "mdy": "%m/%d/%Y"}
TG_CLEANUP_MODES = ("keep_46h", "latest_only", "off")

MAX_TOKENS = 1_000_000          # upper bound for a reward or a start cost

# Name limits shared by the web app, the bot and the templates. A task name has to fit, with a short phrase around it,
# into a pop-up answer to a button (200 characters), see TG_ALERT_LIMIT.
MAX_PROJECT_NAME_LEN = 120
MAX_TASK_NAME_LEN = 120

TG_TEXT_LIMIT = 4096        # Telegram rejects longer messages entirely (characters after the HTML markup is parsed)
TG_ALERT_LIMIT = 200        # text of a pop-up answer to a button (answerCallbackQuery)
TG_SAFE_LIMIT = 3900        # what we aim for: the limit minus room for the "…and N more" line and counting slack
