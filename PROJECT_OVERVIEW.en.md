# RPi5 Project Hub — logic and features

[Русский](PROJECT_OVERVIEW.md) · **English**

A local tracker for hobby projects (3D printing and miniature painting) built for a Raspberry Pi 5.
It is a web app with a Telegram bot, notifications and a "token" system: finished projects earn a reward,
and starting a new project costs tokens. The goal is to finish what you start instead of piling up unpainted models.

> Status values, stages and some UI labels below are given as they appear in the English interface. In the database
> statuses and stages are stored as canonical Russian strings (see section 2).

---

## 1. Components and architecture

```
                   ┌──────────────────────────── one process: python app.py (uvicorn, port 8000) ──┐
 Browser ───────►  │  FastAPI (app.py)  ── REST API + the page                                     │
                   │  Bot (bot.py)      ── Telegram long polling (direct or through a proxy)       │
                   │  Scheduler (scheduler.py) ── checks the schedule every 20 s                   │
                   └───────────┬───────────────────────────────┬───────────────────────────────────┘
                               │                               │ on schedule, as separate processes
                  SQLite tracker.db + photos/          notify.py (pushes)   backup.py (backup)
                  config.json (settings, chat link)            │                    │
                                                         Telegram Bot API    Google Apps Script
```

- **One service.** The web app, the bot and the scheduler start together with the app (`lifespan` in `app.py`).
- **The `notify.py` and `backup.py` scripts** are started by the scheduler as separate processes with the same
  interpreter (`sys.executable`), so the venv path is not hard-coded anywhere. Their output goes to `logs/notify.log`
  and `logs/backup.log` (rotated at 1 MB, three copies, job timeout 180 s). Everything the app prints to the console is
  copied to `logs/app.log` with the same rotation (`logs.py`, pure Python, so it works the same on Windows and Linux).
- **Data.** `tracker.db` (SQLite), the `photos/` folder (photos and references), `config.json` (settings and the chat
  link), `.env` (secrets).

### Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI: the page, REST API, photo and reference uploads, starting the bot and the scheduler |
| `bot.py` | Telegram bot: commands, buttons, chat linking, long polling, receiving and viewing progress photos |
| `logs.py` | Size-rotated logs: `logs/app.log` (console copy), script logs |
| `progress.py` | Progress photos: saving, listing, deleting (shared by the web app and the bot) |
| `notify.py` | Building and sending notifications (evening digest, monthly report) |
| `scheduler.py` | Built-in schedule (replaces cron), runs scripts as subprocesses |
| `backup.py` | Sends a copy of projects and tasks to Google Sheets through Apps Script |
| `database.py` | SQLAlchemy models and automatic schema migration |
| `common.py` | Config (cached, atomic writes), canonical values, token balance, escaping, chat linking |
| `storage.py` | Used space: app, database, photos, logs (folder walk, Windows and Linux) |
| `pipeline.py` | Pipeline templates: built-in defaults, storage and validation in `config.json` |
| `vacation.py` | Vacation: moving deadlines, state, ending early |
| `tgproxy.py` | Optional proxy for Telegram: where the address comes from, validation, password masking, connection check |
| `i18n.py`, `static/i18n.js` | Localization of the server/bot and of the browser |
| `auth.py`, `static/auth.js`, `templates/login.html` | Optional password login (off by default) |
| `tls.py` | Basic HTTPS: a self-signed certificate via `openssl`, uvicorn options, `python tls.py status \| cert [--force]` |
| `locales.json` | All texts in one file, blocks per language (`ru-RU`, `en-US`) |
| `tools/check_locales.py` | Checks `locales.json` |
| `tools/deploy.sh`, `tools/clean.sh` | Deploying a branch to the device (database copy, update, restart, rollback); cleaning up files of old versions |
| `templates/index.html`, `templates/components/{nav,tabs,modals,toasts}.html` | Page markup |
| `static/app.js` | UI logic |
| `static/trash.js`, `static/calendar.js` | Trash window; deadline calendar |
| `static/templates.js` | Template editor in the settings, template choice in the card, vacation |
| `static/progress.js` | Progress photos: the block in the project card and the "Feed" tab |
| `static/dropzone.js`, `static/dropzone.css` | Drag and drop of images onto the cover, references and progress |
| `static/themes.css` | UI themes (eight themes, listed in the `THEMES` registry in `static/app.js`) and chart tokens |
| `static/mobile.css` | Phone layout (narrower than 640 px): bottom tab bar, two-row header, full-screen modals |
| `static/card-tab.css` | Project card in a separate tab (`/?card=`): full-width page, photo column |
| `static/dateinput.js` | Date inputs in the format from the settings (a wrapper over `<input type="date">`) |
| `static/sw.js`, `static/manifest.webmanifest`, `static/pwa.js`, `static/icons/`, `static/offline.html`, `templates/components/pwa_head.html` | PWA: service worker (served as `/sw.js`), manifest, registration and install button, icons, offline page, `<head>` tags |
| `THEMES_GUIDE.md` (`THEMES_GUIDE.en.md`), `tools/check_theme.py` | Guide for creating themes (also for AI models) and the theme check |
| `.env` | `TELEGRAM_BOT_TOKEN`, `PROXY_URL`, `APPS_SCRIPT_URL`, `HTTPS`, `PORT`, `TLS_NAMES`, `SSL_CERTFILE`, `SSL_KEYFILE` |
| `auth.json` | Login state: password hash, session secret (created automatically, mode 600, never commit it) |

---

## 2. Data model

SQLite; tables are created automatically, missing columns are added by `ensure_schema()` without data loss.
There are no foreign keys; links are string IDs; when a project is erased from the trash its tasks and files are
removed in code. `projects` has `deleted_at` (set: the project is in the trash). `ensure_schema()` also creates the
indexes on `tasks.project_id` and `projects.deleted_at` in older databases (`CREATE INDEX IF NOT EXISTS`).

**`projects`**

| Field | Meaning |
|---|---|
| `id` | `PRJ-XXXXXX` |
| `name` | name (up to 120 characters, not empty) |
| `status` | `В планах` (Planned), `В работе` (In progress), `На паузе` (On hold), `Закончен` (Finished) |
| `stage` | `—`, `Печать` (Printing), `Шлифовка` (Sanding), `Сборка` (Assembly), `Грунт` (Priming), `Покрас` (Painting) |
| `priority` | `Low`, `Medium`, `High`, `Critical` |
| `estimate` | project deadline |
| `token` | tokens spent to start it (0 in DEV MODE) |
| `token_reward` | reward for finishing |
| `paint_notes` | paint notebook with recipes |
| `photo` | file name of the photo in `photos/` |
| `updated_at` | last "progress" on the project (for the stagnation detector) |
| `completed_at` | when the project was finished |

**`tasks`**: `id` (`TSK-XXXXXX`), `project_id`, `task_name`, `deadline`, `status` (`Не начато` (Not started),
`В процессе` (In progress), `Закончен` (Done)), `postponed` (how many times the deadline was moved from the bot),
`completed_at`.

**`project_refs`** — paint references: `project_id`, `filename` (original), `thumb` (preview), `created_at`.

**`progress_photos`** — progress (WIP) photos: `project_id`, `filename`, `thumb`, `stage` (the project stage at upload
time), `note` (caption), `source` (`web` or `telegram`), `created_at`. Files live in `photos/progress/`.

**`adjustments`** — balance adjustments (deleted projects): `amount`, `reason`, `created_at`.

Statuses and stages are stored in Russian and serve as values in the logic; for display they are translated into the
interface language. "Finished" is any of the statuses `закончен`, `завершено`, `завершен` or `completed`
(case-insensitive).

---

## 3. Tokens and the project life cycle

**Balance** = `INITIAL_TOKENS` + rewards of finished projects − start cost of all projects + adjustments.

- **Creating a project.** If DEV MODE is off and the balance is below `PROJECT_COST`, the project is not created (an
  error with the current balance). Otherwise the project gets `token = PROJECT_COST` (0 in DEV MODE).
- **The reward** counts once the project gets the "Finished" status.
- **Finishing a project.** `completed_at` is set, and all its open tasks become "Done" with the current date.
  Tasks closed earlier keep their own completion date, so the analytics charts do not shift. Setting the same status
  again does not change the completion date. Returning a project from the Hall of Fame ("Return to work") clears
  `completed_at`; the tasks stay closed.
- **Deleting a project = the trash.** The project gets `deleted_at` and disappears from lists, the bot, notifications,
  the backup and the photo feed; its tasks, photos and references stay where they are. From the trash the project can
  be restored, erased right away, or left alone: after `TRASH_DAYS` days (30 by default) a background check erases it
  (at start-up and every 6 hours). Tasks cannot be added to a project in the trash.
- **Balance and the trash.** With `DELETE_KEEPS_BALANCE = true` (the default) the balance does not change, neither in
  the trash nor after erasing: on erasing, the project's contribution (the reward if it was finished, minus the start
  cost) is recorded in `adjustments`. With the option off, the start cost is refunded as soon as the project goes to the
  trash and charged again on restore.
- **Editing.** The name can always be changed. Priority and deadline of a project only in DEV MODE.
- **What counts as "progress"** (`updated_at`): changing status and stage, creating a task, changing a task status,
  changing the paint notebook text (saving it unchanged does not count), applying a pipeline template, closing or
  postponing a task from the bot, an edit in DEV MODE. Renaming, photos and references do not count.

### DEV MODE
One switch does three things: turns off automatic pushes ("silent mode"), allows editing project priority and
deadline, and allows creating projects without tokens.

---

## 4. Web interface

### Tabs
- **Dashboard.** Active projects as cards on the left, "Upcoming tasks" on the right (the 5 open tasks with the earliest
  deadlines, tasks without a date at the end).
  - **Project search:** a field above the list. Several space-separated words, all of them must match. Case and ё/е do
    not matter. Searches the name, id, priority, status, stage (both the stored Russian value and its translation),
    the paint recipe and the project's task names. The counter shows "found / total", Esc clears the field, the filter
    survives data refreshes. Runs in the browser; nothing is sent to the server.
- **All tasks.** Tasks grouped by active project.
- **Calendar.** A month grid (with the `mdy` date format the week starts on Sunday, otherwise on Monday): deadlines of
  open tasks and 📌 project deadlines. Overdue items are red, closed ones struck through; a cell shows three entries and
  "N more". Clicking a day opens its list, clicking an entry opens the project. Finished projects are not shown.
- **Feed.** All progress photos by day, newest first, with a project filter. A click opens the photo full screen with a
  caption (project, date, stage, note) and arrow navigation.
- **Hall of Fame & Analytics.** Four figures (projects finished, tasks closed, tokens earned, overall task progress), a
  stage funnel chart for active projects, a chart of tasks and projects closed over 6 months, and an archive of finished
  projects (reward, number of tasks, finish date, paint recipe, a "Return to work" button). The archive has its own
  search with the same rules; typing does not redraw the charts.

### Project card in the list
Photo on the left; on the right the name, badges (reward, priority, 🎨 if there is a recipe, 🖼 N for the number of
references, counters of closed and open tasks) and the deadline: "Due soon!" when 3 days or less are left, "Overdue" after
the date. Status and stage dropdowns at the bottom. Buttons: open in a new tab, and delete (with a confirmation dialog).

- **Clicking the photo:** with no photo, a file picker opens; with a photo, it opens full screen.
- **Drag and drop:** an image can be dragged onto the project photo (replaces the cover), onto the references block or the progress block (several files at once). While a file is dragged over the window, all zones are highlighted.
- **Clicking the name or the badges** opens the project card.

### Open project card
- Header: photo (change and remove), the name with a pencil (rename; in DEV MODE also priority and deadline),
  an "open in a new tab" button, "Pipeline template", "+ Task".
- **Sub-tabs** (only in the compact card over the dashboard): "Tasks" — the paint notebook and tasks,
  "Photos" — references and progress (the number of images next to the name). The open sub-tab is kept while the card
  is open; opening another project switches back to "Tasks". The separate tab (`/?card=`) has no sub-tabs.
- **Paint notebook:** saved automatically when the field loses focus, and with the button.
- **Paint references:** up to 20 images (JPEG, PNG, WebP, GIF up to 25 MB). Originals are stored unchanged; the browser
  makes small previews for the grid. Full-screen viewer with arrows.
- **Progress:** a timeline of the project's photos (oldest on the left, newest on the right). Every photo keeps the date
  and the stage the project had at upload time, plus an optional caption. Up to 10 files at once; the browser resizes
  photos to 1600 px (JPEG); the cross on a photo deletes it. A TG mark means the photo came from Telegram.
- **Tasks:** status (dropdown), edit, delete.
- **Pipeline template:** the button opens a two-step window. Step 1 picks a template (the list comes from the settings,
  each with its number of steps and their names), step 2 sets step deadlines. Templates are edited in Settings →
  Templates (see below). There are two by default:
  - **3D print**, 7 steps: supports and printing, washing and curing, sanding and filling, drilling for pins,
    priming and pre-shading, airbrushed base coat, details and varnish.
  - **Kit model**, 5 steps: removing from the sprue and cleanup, gluing and filling seams, priming and pre-shading,
    base coat, details and varnish.
  - **Deadline window (step 2):** every step has its own date (the project deadline by default) and a "create" checkbox.
    An unchecked step is not created; an empty date means a task without a deadline. "Spread evenly up to the project
    deadline" lays out the dates of the checked steps from today to the project deadline (if the deadline is already in
    the past, every step gets the project deadline).
  - **Applying again** creates no duplicates. Steps the project already has are marked "already added" in the window
    and are not created; if all of them exist, the "Create steps" button is disabled. Names are compared without a
    leading number and case-insensitively, so tasks from older numbered templates ("3. Gluing…") are recognized too.
    Untouched steps of the built-in templates are recognized in any interface language; custom and edited steps are
    matched by their text as is. Steps shared by different templates (priming, painting, details) are not repeated
    either.
  - Tasks are created **without numbers** in their names (numbers are shown only in the template window), so a custom
    task or a deleted step does not break the numbering.
  - API: `GET /api/pipeline-templates?project_id=<id>` returns `{"templates": [{"id", "name", "steps": [names in the
    interface language], "raw": {stored values}}], "defaults": [built-in], "existing": {id: [numbers of the steps the
    project already has]}, "custom": templates_changed}`. `POST /api/projects/{id}/template` accepts
    `{"kind": "<template id>", "steps": [{"index": 0, "deadline": "2027-03-21"}, ...]}`. Without `steps` all steps are
    created with the project deadline; without a body it works as `print`. A wrong step number, a repeated step, a date
    not in `YYYY-MM-DD` format or an empty list give an error and nothing is written. The answer contains `added` and
    `skipped` (how many steps were created and how many were skipped as existing); if all selected steps already exist,
    an error is returned.
- **Dates** in the UI, the bot and notifications always include the year. The format is chosen in Settings → General:
  `dmy` — `21.03.2027` (the default, for any language), `mdy` — `03/21/2027`. Date inputs in modals follow the setting
  too (`static/dateinput.js`): the browser shows a native `<input type="date">` in the format of its own language, so a
  text field in the app format with a calendar button is placed next to it. A date can be typed as digits (separators
  are inserted automatically) or picked in the system calendar; an invalid date reverts to the previous value when the
  field loses focus. The value is still stored in the native field in ISO (`2026-10-31`), so the rest of the code reads
  and writes `.value` as before.

### Task rules
- A task cannot be created without a description; outer spaces are trimmed, the length is up to 300 characters.
- A task can only be created in an existing project outside the trash.
- For projects not in the "Planned" status a task deadline is required; if none is given and the project has a date,
  that date is used.
- Dates are accepted only as `YYYY-MM-DD`; an invalid date gives a clear error instead of a server failure.
- Server errors (empty name, project not found, invalid value) are shown as a toast.

### Data refresh and input
- The page reloads the data after every action and when the window gets focus again. If requests overlap, only the
  newest answer is applied.
- Text being typed in the paint notebook is not overwritten by such a refresh: the field is updated only when it has no
  unsaved edits.

### Photos
The browser resizes the project photo (cover) to 1400 px and saves it as JPEG (6 MB at most). The format is checked on
the server by the file content; the file name is generated by the server.

### Other
- **Card in a separate tab:** the `/?card=<id>` address shows only the project card, full page width: on a wide screen
  the recipe and tasks are on the left, references and progress photos with large previews on the right (styles in
  `static/card-tab.css`).
- **Data refresh** when the window gets focus again (at most once every 3 seconds), so several tabs stay in sync.
- **Keys:** Esc closes the top window, Enter confirms a form, arrows in the image viewer.
- **Trash.** The "🗑 Trash (N)" button next to the search (visible when the trash is not empty): a window with the list,
  days left, "Restore", "Erase" and "Empty trash" buttons.
- **Delete confirmations** are shown as a dialog in the app style.
- **Phone.** On screens narrower than 640 px (`static/mobile.css`) the tabs become a bottom bar, the header takes two
  rows (and does not stick to the top), task rows stack vertically, modals take almost the whole screen. Buttons shown
  on mouse hover are always visible on touch screens. On iPhone inputs use 16 px text so Safari does not zoom the page.
- **PWA.** The manifest and icons allow adding the page to the home screen; the "App on your phone" block in
  Settings → General shows an "Install" button (Chrome, the `beforeinstallprompt` event) or a hint for the browser.
  Service worker (only on trusted HTTPS or `localhost`): pages and `/static/*` go network first, without a network a page
  is replaced by `static/offline.html`; CDN libraries come from the cache and are refreshed in the background;
  `/api/*` and `/photos/*` are never cached. After editing `sw.js`, increase `VERSION`.
- **Themes:** Standard, Cyberpunk, Warhammer 40k, SCP Dossier, Workbench, Synthwave, JRPG (FFVII), Model Manual,
  Hardware PCB. The choice is stored in the browser (`localStorage`, key `ph_skin`); buttons and modals are styled for
  every theme. Themes are listed in the `THEMES` registry (`static/app.js`).
  **Charts** follow the theme: bar, grid and label colors, the font and the corner radius come from the CSS variables
  `--chart-*` of the active theme and are redrawn right away when it changes. How to add your own theme is described in
  `THEMES_GUIDE.md` / `THEMES_GUIDE.en.md`; check: `python tools/check_theme.py [name]`.

### Settings window
Seven sections: **General** (DEV MODE, project cost, delete behavior, language), **Notifications** (vacation, postponing,
stagnation detector, Friday plan, monthly report, connection test), **Schedule** (per weekday), **Templates** (pipeline
templates), **Backup** (automatic backup and used space), **Telegram** (chat link, optional proxy, a "What the bot can do" guide with the same texts as `/help`), **Access** (password
login, password change, logout).

**Pipeline templates** (`pipeline.py`, `static/templates.js`). Stored in `config.json` (`PIPELINE_TEMPLATES`); while the
key is absent, the two built-in ones apply. The editor can rename a template, add, delete, reorder and rename steps,
create a template or delete any of them, including built-in ones (at least one must remain). "Reset to defaults" brings
back the two built-in ones. Changes are saved with the common "Save" button. Limits: up to 20 templates, up to 30 steps,
a template name up to 60 characters, a step up to 120; empty steps are dropped. The name and steps of built-in templates
are stored as translation keys (`template.print`, `pipeline.print.step1`...) and follow a language change; an edited row
is saved as plain text. If the saved list equals the built-in one, the key is removed from the config. Tasks already
created from a template are not changed by editing it. `POST /api/pipeline-templates` accepts
`{"templates": [{"id", "name", "steps": [...]}]}` or `{"reset": true}`.

**Vacation** (`vacation.py`, Settings → Notifications). A "Days" field (1–30) and a "Start vacation" button with a
confirmation. Takes effect right away, without the "Save" button:
- deadlines of all unfinished projects outside the trash (including those on hold) and of their open tasks move by N days;
  tasks without a deadline, finished projects and finished tasks do not change;
- the "last progress" time of these projects moves as well, so the stagnation detector does not count the vacation as
  idle time;
- automatic pushes (evening digest, Friday plan, monthly report) are not sent until the vacation ends;
  the test push and the bot work as usual;
- the header shows a "Vacation until …" pill (a click opens the notification settings).
"End vacation" brings notifications back immediately; deadlines stay moved. A new vacation cannot start while one is
active. In `config.json`: `VACATION` = `{"start", "until", "days"}`; `until` is the first day after the vacation.
API: `POST /api/vacation` (`{"days": N}`), `POST /api/vacation/cancel`; the state comes with `GET /api/data`
(`vacation`: `active`, `start`, `until`, `days`).

---

## 5. Telegram bot

Uses long polling. Requests run in a worker thread with a hard timeout, directly or through a proxy (see "Proxy"
below). One HTTP connection is reused between requests (no new TCP/TLS/SOCKS handshake per call); it is rebuilt when the
proxy changes and at the planned reconnect: every 3 hours and about 10 minutes before the nearest scheduled push.
- The `getUpdates` offset is kept across sessions, so the latest button presses are not delivered again after a reconnect.
- After a network error (no connection, the proxy refused) the bot waits 3 seconds instead of retrying in a loop.
- An error in one update does not stop the others; the bot token is removed from error texts in the log.
- Cards and lists are built from the database in a worker thread and do not hold up the web interface.

### Proxy (optional)
Only needed if Telegram is not reachable directly (`tgproxy.py`, Settings → Telegram → "Proxy for Telegram").
- **Where the address comes from.** Until the block has been saved in the UI, `PROXY_URL` from `.env` applies, as before
  (empty = direct). After "Apply" only the UI setting counts (`config.json`, `TG_PROXY` = `{"enabled", "url"}`):
  off means direct, even if `PROXY_URL` in `.env` is set.
- **No restart needed.** The address is looked up for every bot request and every notification run; a switch takes
  effect from the next request. At the start of a session the log shows how the bot connects: "direct" or
  "via proxy …".
- **Formats:** `socks5://`, `socks5h://` (DNS through the proxy), `http://`, `https://`; a login and password in the
  address are allowed. MTProto proxies (`tg://proxy?…`, `dd…` secrets) do not work with the Bot API and are rejected.
- **The password never reaches the browser:** the UI only gets a mask (`socks5://***@host:1080`). An empty field on
  "Apply" keeps the saved address (or the one from `.env`, which is then copied into `config.json`).
- **"Check connection"** sends a `getMe` request with what is in the form right now (before saving) and shows the bot
  name or the error.
- API: `GET /api/telegram/proxy` (state: `source` = `env` or `settings`, `enabled`, `has_url`, `display`),
  `POST /api/telegram/proxy` (`{"enabled", "url"}`), `POST /api/telegram/proxy/check` (the same fields).
All user text is escaped before sending (HTML mode).

### Chat linking
- The bot answers **only the linked chat** (`TG_CHAT_ID` in `config.json`). Messages and button presses from other chats
  are ignored, logged at most once an hour.
- While no chat is linked, the bot accepts only `/start CODE` from a **private** chat. The code is single-use
  (8 characters like `K7M2-X9QP`) and is shown in Settings → Telegram and in the log at start-up.
- After five wrong codes within 10 minutes, linking attempts are blocked.
- The "Unlink" button removes the link and creates a new code. "New code" changes the code while no chat is linked.

### Commands

| Command | What it does |
|---|---|
| `/start` | greeting and the list of commands |
| `/current_task` | the project in focus: status, stage, priority, reward, balance, tasks with buttons |
| `/projects` | all active projects with navigation buttons |
| `/help` | commands and features of the bot; the list comes from the `COMMANDS` and `FEATURES` registry in `bot.py`, which also builds the "/" menu in Telegram |
| `/task [name]` | task builder: project, name, deadline (buttons "Today", "Tomorrow", "In 3 days", "In a week", "Project deadline", "Pick a date", "No deadline"); a date can be typed in the format from the settings |
| `/photo` | the latest progress photo across all projects; ◀ ▶ buttons flip through, "Delete" asks for confirmation |
| `/stats` | statistics of the current month: tasks closed, projects finished, tokens earned, balance |
| `/test_notify` | starts a test notification |

**Project in focus:** among active projects, "In progress" first, then by priority (Critical → Low), then by the nearest
deadline. Below the card there is a block "Urgent tasks in other projects" (3 days or less, or overdue; projects on hold
are not included).

### Task builder in the bot
`/task` or the "➕ Task" button (in a project card and in the project list). If there is only one active project or the
button was pressed in a card, the project step is skipped; the name can be given right away: `/task Prime the cloak`.
A deadline is required for projects that are not "planned" (as in the web UI); "No deadline" is offered only for
"planned" ones. An own date: `21.03.2027` or `03/21/2027` depending on the setting; the year may be omitted (the nearest
future date is taken). Any command cancels a task in progress; the draft lives in memory for one hour. New bot features
must be added to the `COMMANDS` or `FEATURES` registry, then `/help` updates itself.

### Photos in the bot
- **Send the bot a photo** (as a photo or as an image file, up to 20 MB). The caption becomes the note. With one active
  project the photo is saved right away; with several, the bot asks with buttons which project it belongs to (there is
  "Cancel"). An album of several photos gets one question. After saving there are "Project photos" and "Undo" buttons
  (the latter deletes the photos just saved).
- **Viewing.** `/photo` or the "Photos (N)" button in a project card. Flipping happens in the same message
  (`editMessageMedia`), newest first.
- Photos waiting for an answer are kept in memory for one hour; after a bot restart the "which project" question
  expires and the photo has to be sent again.

### Buttons
"Start" and "Done" on a task, "Change stage" (stage menu), "All active projects", navigation between projects.
From a push: close the task and "+2d" — **postponing the deadline**: the date moves by 2 days from the later of two dates
(the current deadline or today). If postponing is turned off in the settings (`allow_postpone`), a button from an old
push replies that it is turned off; a closed task cannot be postponed, and pressing "close" again does not change the
completion date. Postponing can be repeated, but from the second time on the bot answers with a snarky comment that gets
nastier with every postponement (2nd, 3rd, 4th, 5th and later). It is text only: no tokens are taken. In the bot card a
task shows ⏳ (or ⏳×N); in the push the ⏳×N counter appears from the second postponement on.

---

## 6. Notifications

`notify.py` sends one message per run to the linked chat:

1. **Overdue** and **Urgent (3 days or less)**: open tasks with a deadline in projects that are neither finished nor on
   hold, from the most overdue to the nearest. Tasks without a deadline are not included. Below the message there are up
   to 4 "close" and "+2d" buttons for the most urgent tasks.
2. **Stagnation detector:** projects "In progress" without progress for `stagnation_days` days (10 by default).
3. **Friday plan** (on Fridays): up to three open tasks of every project "In progress", nearest deadline first.
4. **Report for the previous month** is sent on the 1st (if enabled) before the evening digest.
5. If there is nothing to say, an "All on track!" message with the current focus is sent.

In DEV MODE and during a vacation (see "Settings window") automatic pushes are skipped.

**Duplicate protection.** The automatic digest runs at most once every 10 minutes: a run takes a "slot" in
`.notify_state.json` under a file lock, so simultaneous runs (an old cron entry plus the scheduler, two copies of the
app) produce one message. The lock works on Linux (`fcntl`) and on Windows (`msvcrt`). A skipped run writes to
`logs/notify.log` who took the slot. Flags: `--test` (test, skips the protection), `--force` (skips the protection),
`--monthly` (report only), `--source=name`. Exit code 0 means the message was sent (or there was nothing to send), 1
means Telegram did not accept it or there is no token; the code is visible in `logs/notify.log`.

---

## 7. Scheduler

- Checks the schedule every 20 seconds; each job starts at most once a minute.
- **Pushes are configured separately for every weekday:** a checkbox and a time (24-hour). A master switch turns all
  pushes off at once. The "Copy Mon time → all days" button copies the time.
- **Backup:** daily at the given time, if enabled.
- The schedule is stored in `config.json` (`SCHEDULE`); the old "weekdays and weekends" format migrates automatically.
- **Catch-up after downtime.** The scheduler records "the app is running" in `scheduler_state.json` (every 5 minutes and
  on every job start). At start-up it looks for runs that fell on the downtime and performs the latest missed run of each
  job (15 seconds after start, when the web app and the bot are up):
  - pushes: only if no more than 3 hours passed since the planned time and it is the same calendar day (an evening
    digest is useless at night);
  - backup: if no more than 24 hours passed.
  A job that already ran is not repeated. The first start (no state file) catches up nothing.
  Changing the schedule in a running app does not trigger a catch-up: the time "has passed", but there was no downtime.
- A job that is still running is not started again.

---

## 8. Backup

`backup.py` sends JSON (`projects`, `tasks`, `timestamp`) to the Google Apps Script at `APPS_SCRIPT_URL` (redirects are
followed). Exit code 1 if the URL is not set or Google answered with an error (visible in `logs/backup.log`).
The copy contains projects and tasks. **Photos, references and balance adjustments are not included**: copy the
`photos/` folder and the `tracker.db` file separately. The database runs in WAL mode, so `tracker.db-wal` and
`tracker.db-shm` appear next to `tracker.db`. Do not copy the file while the app is running (fresh writes are in `-wal`):
stop the app or make a copy with `sqlite3 tracker.db ".backup copy.db"`.

**Used space** (Settings → Backup, `storage.py`, `GET /api/storage`). Computed when the section opens and with the
"Refresh" button, by walking folders, without external commands. Rows: app files (everything in the app folder except the
data below), the database (`tracker.db` with `-wal`/`-shm`), photos (covers, references, progress photos), logs (`logs/`
and old `*.log` files in the root). If present, database copies from `tools/deploy.sh` (`backups/`), `.venv` and `.git`
are shown separately. The size is the file length, not the space allocated on disk; free disk space is not shown yet.

---

## 9. Localization

- Languages: Russian (`ru-RU`) and English (`en-US`). The language is chosen in the settings and applies everywhere: the
  interface, the bot, notifications and server messages. After a language change the page reloads.
- **One file, `locales.json`**, with blocks per language code:

  ```json
  {
    "ru-RU": { "meta.name": "Русский", "common.save": "Сохранить", "ref.limit": "Лимит: максимум {n} референсов на проект" },
    "en-US": { "meta.name": "English", "common.save": "Save",      "ref.limit": "Limit: at most {n} references per project" }
  }
  ```

- **Keys are technical**, shaped `area.name` (`common.save`, `settings.tab.backup`, `err.project_not_found`,
  `bot.task_closed`). To change wording, edit the value in `locales.json`; code and templates are not touched. Areas:
  `common`, `nav`, `analytics`, `stats`, `project`, `task`, `deadline`, `confirm`, `photo`, `ref`, `recipe`, `toast`,
  `err`, `settings`, `tg`, `bot`, `notify`, `status`, `stage`, `day`, `pipeline`, `theme`, `hall`, `meta` and others.
- Where it is used:
  - Python (server, bot, notifications): `tr("common.save")`, `tr("ref.limit", n=10)`.
  - Templates: `{{ tr('common.save') }}`. The text is filled in on the server, so there is no flash of untranslated text.
  - JavaScript: `tr('common.save')`, `tr('ref.limit', {n: 10})`. The server puts the dictionary of the chosen language
    into the page (`window.APP_I18N`).
- **Fallbacks:** a key missing in the chosen language is taken from `ru-RU`, and if it is missing there too, the key
  itself is shown. A placeholder error (`{n}` not passed) does not crash the app.
- Project and task names are never translated. Statuses, stages and weekdays stay canonical Russian values in the
  database (`В работе`, `Покрас`, `Пн`...) so data and the bot's `callback_data` do not break; for display they are
  translated by `tv(value)` (the `VALUE_KEYS` table in `i18n.py`). Pipeline step names are inserted in the language
  active at the moment of clicking and are then stored as ordinary task text.
- **A new language:** add a block with its code and a `meta.name` key to `locales.json`; it appears in the list right
  away. A stub with all keys: `python tools/check_locales.py --create de-DE --name Deutsch`.
- **A new text:** invent a key, add it to `ru-RU` and `en-US`, use it in the code.
- **Check:** `python tools/check_locales.py`. Finds keys missing in one of the languages, mismatched `{placeholders}`,
  "text instead of a key" in `tr()` calls, and unused keys; `--fix` adds missing keys and sorts the file.

---

## 10. Configuration

**`config.json`**

| Key | Meaning |
|---|---|
| `DEV_MODE` | silent mode, editing, projects without tokens |
| `INITIAL_TOKENS`, `PROJECT_COST` | starting balance and project cost |
| `DELETE_KEEPS_BALANCE` | deleting a project does not change the balance |
| `TRASH_DAYS` | how many days a project stays in the trash before it is erased (1–3650, 30 by default) |
| `LANGUAGE` | language code from `locales.json`: `ru-RU`, `en-US` (old `ru`/`en` are migrated automatically) |
| `DATE_FORMAT` | date format: `dmy` (default) or `mdy`; an unknown value falls back to `dmy` |
| `NOTIFY` | `allow_postpone`, `stagnation_alert`, `stagnation_days`, `friday_digest`, `monthly_report` |
| `SCHEDULE` | `notify_enabled`, `notify_days` (per day: `enabled`, `time`), `backup_enabled`, `backup_time` |
| `PIPELINE_TEMPLATES` | pipeline templates (no key = the two built-in ones), edited in Settings → Templates |
| `VACATION` | vacation: `start`, `until` (first day after the vacation), `days` |
| `TG_PROXY` | proxy for Telegram set in the UI: `enabled`, `url` (no key = `PROXY_URL` from `.env`) |
| `TG_CHAT_ID`, `TG_PAIRING_CODE` | bot link (internal) |

The file is written atomically (a temporary file and `os.replace`); missing keys are filled with defaults.
The parsed config is cached and re-read only when the file on disk changes (time, size, inode), so changes from another
process (`notify.py`) and manual edits are picked up without a restart. If `config.json` is damaged (not valid JSON), it
is renamed to `config.json.broken-<time>`, the app starts with default settings and says so in the log; the bot link and
the settings can be restored from the saved copy.

**`.env`:** `TELEGRAM_BOT_TOKEN`, `PROXY_URL` (optional; used until a proxy is configured in the UI), `APPS_SCRIPT_URL`,
and for HTTPS `HTTPS`, `PORT`, `TLS_NAMES`, `SSL_CERTFILE`, `SSL_KEYFILE`.

---

## 11. HTTP API

Responses look like `{"status": "ok"}` or `{"status": "error", "message": "..."}`; messages come in the interface language.

| Method and path | Purpose |
|---|---|
| `GET /` | the page (language and dictionary are embedded) |
| `GET /login` | login page (when login is on) |
| `GET /sw.js`, `/manifest.webmanifest` | PWA service worker and manifest (no login needed) |
| `POST /api/auth/login`, `/logout` | log in (login + password digest) and log out |
| `GET /api/auth/status` | whether login is on, the user name, whether a password change is required |
| `POST /api/auth/change`, `/enable` | change the password; turn login on or off (turning it off requires the password) |
| `GET /api/data` | projects, tasks, balance, settings, schedule, languages |
| `POST /api/config` | save settings (time and language format are checked) |
| `POST /api/notify/test` | start a test notification |
| `GET /api/storage` | used space by category (bytes and number of files) |
| `GET /api/telegram` | link state: whether a chat is linked, the chat ID or the pairing code |
| `POST /api/telegram/unlink`, `/api/telegram/code` | unlink the chat; create a new code |
| `GET`, `POST /api/telegram/proxy`, `POST /api/telegram/proxy/check` | Telegram proxy: state (without the password), save, check the connection |
| `POST /api/action` | change project status and stage, task status |
| `POST /api/projects` | create a project |
| `POST /api/projects/{id}/edit`, `/rename` | edit (DEV MODE); rename (always) |
| `POST`, `DELETE /api/projects/{id}/photo` | upload and remove the photo |
| `POST /api/projects/{id}/refs`, `DELETE …/refs/{ref_id}` | add and remove a reference |
| `DELETE /api/projects/{id}` | move a project to the trash |
| `POST /api/projects/{id}/restore`, `DELETE /api/trash/{id}`, `DELETE /api/trash` | restore from the trash, erase for good, empty the trash |
| `POST /api/projects/{id}/progress`, `PATCH`, `DELETE …/progress/{photo_id}` | add a progress photo (`image`, `thumb`, `note`), edit the caption, delete |
| `POST /api/projects/{id}/notes`, `/template` | paint notebook; pipeline template (`kind`: template id, optional `steps` with deadlines) |
| `GET`, `POST /api/pipeline-templates` | templates in the interface language (with `project_id`: which steps the project already has); save or reset templates |
| `POST /api/vacation`, `/api/vacation/cancel` | start a vacation of N days (1–30); end it |
| `POST /api/tasks`, `/api/tasks/{id}/edit`, `DELETE /api/tasks/{id}` | tasks |

`POST /api/action` accepts only known values: project statuses `В планах`, `В работе`, `На паузе`, `Закончен`; the stages
listed above; task statuses `Не начато`, `В процессе`, `Закончен`. Anything else gives an error. Project priority is
`Low`, `Medium`, `High` or `Critical`; the reward and the project cost are not negative.

Handlers that work with the database and files run in the FastAPI thread pool, so a slow SD card or a long query does
not stall the bot and the scheduler running in the same process.

Uploads are sent as JSON with base64, without extra libraries for multipart. Images are checked by their content (JPEG,
PNG, GIF, WebP); SVG and a faked extension are rejected.

---

## 12. Security

- **Password login is optional and off by default** (Settings → Access). While it is off, the web interface is open to
  anyone who can reach port 8000. It is meant for a home network or a VPN.
- **How login works** (personal variant, one user):
  - The default login is `admin`, password `123456`. Until the password is changed, everything except changing the
    password and logging out is closed. A new password must be at least 8 characters (checked in the browser; the server
    only receives the digest).
  - The password never travels in plain text: the browser sends `SHA-256("project-hub:<login>:<password>")`, the server
    stores `scrypt(digest, salt)` in `auth.json`. This protects against casual sniffing and password reuse on other
    sites. **Without HTTPS an intercepted digest can be replayed**, so turn on HTTPS for network use (see "HTTPS" below).
  - Session: a signed cookie (`HttpOnly`, `SameSite=Lax`, `Secure` over https), valid for 14 days. The signature is tied
    to the password: changing it drops all sessions.
  - Brute force: 5 failures from one address within 5 minutes lock login for the rest of the window; every failure adds
    a pause.
  - Everything is closed, including `/photos`; only `/login`, `/api/auth/login`, `/static/`, `/sw.js` and
    `/manifest.webmanifest` are open (PWA installation needs the last two).
  - The password check (scrypt) runs in a worker thread and does not hold up other requests.
  - The Telegram bot, notifications and the scheduler do not go through the web app; login does not affect them.
  - **Forgot the password:** `python auth.py reset` (resets to `admin`/`123456` and turns login off);
    `python auth.py status` shows the state.
- **HTTPS** (`tls.py`, off by default):
  - `HTTPS=1` in `.env` turns on TLS on the same port (`PORT`, 8000 by default). `app.py` takes the options from
    `tls.uvicorn_options()` at start-up; a configuration error stops the start with a clear message.
  - Certificate: if `SSL_CERTFILE` and `SSL_KEYFILE` are not set, `certs/server.crt` and `certs/server.key` are created on
    the first start (RSA 2048, SHA-256, 825 days, key with mode 600) with `openssl`. `subjectAltName` includes
    `localhost`, `127.0.0.1`, the host name and `name.local`, the host's network addresses and everything in
    `TLS_NAMES`. If the address changed: `python tls.py cert --force`. Less than 30 days left: a warning at start-up;
    expired: the app refuses to start.
  - Your own certificate (mkcert, Let's Encrypt): set both paths in `.env`; nothing is created then.
  - The session cookie gets the `Secure` flag automatically (`auth.py` looks at the request scheme and at
    `X-Forwarded-Proto`). If a reverse proxy (Caddy, nginx) sits in front of the app, TLS can be left to it and `HTTPS`
    kept off.
  - The "Access" tab shows which connection the page was opened with (from `location.protocol`).
- **The bot** is linked to one chat, linking only with a single-use code from a private chat, with brute-force protection.
- Secrets live in `.env`; `config.json` only holds the chat ID and the pairing code (and the proxy address if it was set
  in the UI). The bot token is removed from error texts before they are logged.
- User text is escaped on the web and in Telegram messages; the translation dictionary is embedded in the page in a way
  that cannot break out of `<script>`.
- Uploads: size limits, format check by content, file names generated by the server.

---

## 13. Operation

- **Start:** `python app.py` (uvicorn, `0.0.0.0:8000`); in the current installation, the systemd service `tracker.service`.
- **Logs:** `logs/app.log` (bot, scheduler, errors), `logs/notify.log`, `logs/backup.log`; each has three copies `.1`–`.3`
  of 1 MB.
- **What to copy when moving or updating:** `tracker.db`, `photos/`, `config.json`, `auth.json`, `.env`.
- **Updating:** `tools/deploy.sh [branch]` (database copy, `git fetch`, dependencies, restart, check; `--rollback`
  returns to the previous version) or by hand: replace the app files and restart the service. The database schema
  migrates by itself.
- **If cron is still there:** remove the `notify.py` and `backup.py` lines from crontab; the schedule now lives inside the app.

---

## 14. Known limitations

- Password login: one user, no roles or permissions. Over HTTP a digest can be intercepted (see "Security").
- HTTPS is off by default, the certificate is self-signed (a browser warning on every device), no renewal, no redirect
  from HTTP.
- The Google Sheets backup does not include photos, references or progress photos.
- PWA without trusted HTTPS: only a shortcut on the home screen, no service worker. Libraries load from a CDN.
- Receiving photos in Telegram was tested only against a stubbed Telegram API; it should be checked once on a live bot.
- Step names are edited in Settings → Templates; the deadline window only has dates and checkboxes. Task names are
  stored in the language active at creation time.
- Vacation keeps only the last period. An ended vacation does not move deadlines back (by design).
- Catching up missed runs is limited by a window (3 hours for pushes, 24 hours for the backup) and takes only the latest run.
- DEV MODE combines three functions in one switch.
- Statuses and stages are stored as Russian strings; the list of stages is defined in code (`common.py`).
- SQLite without foreign keys. WAL mode is on, but on a file system without WAL support (network drives) the database
  runs in the default mode, and the app says so in the log.
