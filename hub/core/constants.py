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

def is_done(status) -> bool:
    return str(status).strip() == STATUS_FINISHED


DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # index = datetime.weekday(); labels: locales "day.<key>"
DATE_FORMATS = {"dmy": "%d.%m.%Y", "mdy": "%m/%d/%Y"}
TG_CLEANUP_MODES = ("keep_46h", "latest_only", "off")

MAX_TOKENS = 1_000_000          # upper bound for a reward or a start cost

# Name limits shared by the web app, the bot and the templates. A task name has to fit, with a short phrase around it,
# into a pop-up answer to a button (200 characters), see TG_ALERT_LIMIT.
MAX_PROJECT_NAME_LEN = 120
MAX_TASK_NAME_LEN = 120

TG_ALERT_LIMIT = 200        # text of a pop-up answer to a button (answerCallbackQuery)
# Telegram rejects a message longer than 4096 characters entirely (counted after the HTML markup is parsed).
TG_SAFE_LIMIT = 3900        # what we aim for: that limit minus room for the "…and N more" line and counting slack
