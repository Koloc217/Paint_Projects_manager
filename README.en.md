# RPi5 Project Hub

[Русский](README.md) · **English**

A local tracker for hobby projects (3D printing and miniature painting) built for a Raspberry Pi 5.
Web UI, Telegram bot, scheduled notifications and a "token" system: starting a new project costs tokens,
finishing one earns a reward. The idea is to finish what you start instead of piling up unpainted models.

> Status: a personal project. Single user, data stays local.
> Detailed description of the logic and the API: [PROJECT_OVERVIEW.en.md](PROJECT_OVERVIEW.en.md)
> ([Русский](PROJECT_OVERVIEW.md)).

## Features

**Projects and tasks**
- Project cards: status, stage (printing, sanding, assembly, priming, painting, finishing), priority, deadline, photo.
- Search in the active list and in the Hall of Fame: by name, id, status, stage, notes and task names.
- Tasks with deadlines, statuses, postponing and editing.
- References: images attached to a project, with a full-screen viewer.
- Progress photos: shots taken along the way, with date and stage; a timeline in the project card and a shared "Feed" tab.
- Activity journal on the Feed tab: by day, what happened to projects and tasks (statuses, stages, new and closed
  tasks, deadline moves), from the web and from the bot.
- Trash: a deleted project stays in the trash for 30 days and can be restored.
- Deadline calendar: a month grid with task and project deadlines.
- Deadlines to a phone calendar (an option, off by default: Settings → General → Deadlines to phone calendar): as an
  `.ics` file (the "To phone" button on the Calendar tab or `/calendar` in the bot) or as a subscription that updates by itself.
- Project notes right in the project card: paint recipes, ratios, ideas, anything.
- Pipeline templates: "3D print" (7 steps) and "kit model" (5 steps) by default; they can be edited in the settings
  and you can add your own. Before the tasks are created, a deadline window opens: every step gets its own date,
  a step can be skipped, and dates can be spread evenly up to the project deadline. Steps the project already has
  are not created again.
- Vacation: up to 30 days without notifications; deadlines of unfinished projects and tasks move by the same amount.

**Tokens and motivation**
- Token balance: starting a project costs tokens, finishing it pays a reward.
- Hall of Fame: finished projects, statistics and charts (stage funnel, tasks and projects closed over 6 months).
- DEV MODE for testing and balance corrections.

**Telegram bot**
- Linked to one chat with a pairing code; the bot answers only that chat.
- Commands and buttons: project list, the current task, closing and postponing tasks, changing status and stage.
- `/help` lists all commands and features. `/task` (or the "Task" button) creates a task step by step: project, name, deadline.
- `/calendar` sends the deadlines as an `.ics` file for a phone calendar (when calendar export is on in the settings).
- Photos: send the bot a picture (the caption becomes the note) and it lands in the project timeline. `/photo` shows
  the latest photos and lets you flip through them. `/myphotos`: pick a project, then its progress photos or paint
  references. If a photo could not be saved, the bot tells you.
- Evening digest, Friday plan, monthly report, stagnation warning.
- The chat stays tidy: old digests remove themselves (the latest stays; mode in Settings → Telegram), and `/clear` wipes
  the whole chat for the last 48 hours (Telegram does not let a bot delete anything older).
- The schedule is set per weekday in the UI.

**Look and language**
- A standard theme plus eight more: Cyberpunk, Warhammer 40k, SCP Dossier, Workbench, Synthwave, JRPG (FFVII),
  Model Manual, Hardware PCB. Charts follow the theme colors. How to add your own: [THEMES_GUIDE.en.md](THEMES_GUIDE.en.md).
- Language of the UI, the bot and notifications: Russian or English. All texts live in one file, `locales.json`.
- Date format with the year: `21.03.2027` or `03/21/2027`.
- Phone layout (tabs at the bottom of the screen) and installation on the home screen as an app (PWA), see "Phone".

**Security**
- Optional password login, off by default. Turn it on in Settings → Access.
  The first login is `admin` / `123456`, then the app requires a password change.
- The password never travels in plain text (the client sends a hash, the server stores scrypt).
- Basic HTTPS: the line `HTTPS=1` in `.env` turns on encryption and the certificate is created automatically
  (see "HTTPS" below). Without HTTPS an intercepted hash can be replayed, so never expose the app to the internet
  without it.

**Backups and reliability**
- Automatic backup of projects and tasks to Google Sheets through Apps Script. Photos are not included.
- If the app was off when a push or a backup was due, the missed run is done after start-up.
- SQLite in WAL mode: the web app, the bot and the scripts write to it concurrently without lock errors.

## Stack

Python 3.10+, FastAPI, SQLAlchemy + SQLite, Jinja2, httpx, Tailwind, Chart.js, Telegram Bot API.

The interface does not need internet access: Tailwind CSS, Chart.js and the theme fonts live in `static/vendor/` and
`static/fonts/`. After adding new Tailwind classes to templates or JS: `python tools/build_assets.py css` (needs Node.js,
only on the machine where you build).

## Getting started

```bash
git clone https://github.com/Koloc217/Paint_Projects_manager.git
cd Paint_Projects_manager
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # set TELEGRAM_BOT_TOKEN; APPS_SCRIPT_URL if needed
python app.py                 # web app, bot and scheduler in one process
```

The UI opens at `http://<device address>:8000`. The files `tracker.db`, `config.json`, `auth.json` and the `photos/`
folder are created on the first start and are not committed to git.

Linking the bot: Settings → Telegram, get a code and send it to the bot from the chat you want to use.

A proxy for Telegram is optional. If Telegram is not reachable directly, turn it on in the same place (Settings →
Telegram → "Proxy for Telegram"): SOCKS5 or HTTP, a "Check connection" button, applied without a restart.
The old way still works: `PROXY_URL` in `.env` is used until a proxy is configured in the app.

## Updating on the device

If the app is installed as a git clone and runs as a systemd service, one command deploys the latest version of any branch:

```bash
tools/deploy.sh                          # main
tools/deploy.sh feature/branch-name      # try a branch before merging
tools/deploy.sh --rollback               # go back to what was deployed before the last deploy
```

The script copies the database into `backups/` (keeps the last 10), fetches the branch from GitHub, installs
dependencies if `requirements.txt` changed, restarts the service and checks that the server answers. Data
(`tracker.db`, `photos/`, `config.json`, `.env`) is not touched. If project files have local edits, the script stops.
Settings through variables: `VENV` (venv path), `SERVICE` (default `tracker`), `PORT` (8000).
Leftovers of older versions (files neither in git nor in `.gitignore`) are cleaned by `tools/clean.sh`: it shows
the list and, after confirmation, moves them into the sibling folder `<project>-leftovers-<date>`.
The scripts are for Linux with systemd only; on Windows update by hand: `git pull` and a restart.

## HTTPS

By default the app serves plain HTTP. To turn encryption on, add to `.env`:

```
HTTPS=1
```

and restart the app. On the first start a self-signed certificate is created automatically
(`certs/server.crt` and `certs/server.key`, valid for 825 days). It covers `localhost`, the device name and its network
address, and everything listed in `TLS_NAMES` (for example `TLS_NAMES=hub.local,192.168.1.50`). The port stays the same
(`PORT` in `.env`, 8000 by default); only the address now starts with `https://`. The `openssl` tool is required
(Raspberry Pi OS has it).

- **Browser warning.** The certificate is self-signed, so every device shows "connection is not private" once.
  Click "Advanced → Proceed". To get rid of the warning, add `certs/server.crt` to the device's trusted certificates.
- **If the device address changed**, re-create the certificate: `python tls.py cert --force`
  (or add the new address to `TLS_NAMES`).
- **Your own certificate** (for example from mkcert or Let's Encrypt): set both paths, `SSL_CERTFILE` and `SSL_KEYFILE`.
- **Check:** `python tls.py status` shows the mode, the port and how many days the certificate has left.
  The "Access" tab in the settings shows which connection the page was opened with.
- Never share `certs/server.key`; the `certs/` folder is not committed to git.

## Phone

On screens narrower than 640 px the page rearranges itself: the tabs move to a bottom bar, the header takes two rows
(logo, theme, settings and tokens on top, "+ Project" and "+ Task" below), task rows stack vertically, and modals take
almost the whole screen. The "edit" and "delete" buttons that appear on mouse hover on a computer are always visible on
touch screens. The styles live in `static/mobile.css` and change nothing on desktop.

**Installing on the home screen (PWA).** A hint for the current browser: Settings → General → "App on your phone".

- **iPhone and iPad (Safari):** "Share" → "Add to Home Screen". The page opens without the address bar.
- **Android (Chrome):** if the page is opened over trusted HTTPS, an "Install" button appears in the settings
  (or Chrome offers the installation itself). Otherwise: menu ⋮ → "Add to Home screen".

**What depends on HTTPS.** The browser enables the service worker (the "No connection" offline page, caching of the
interface files, full installation in Chrome) only on `https` with a certificate the device trusts, or on `localhost`.
Over plain `http` in a home network an icon can still be added, but on Android it is a shortcut that opens in a browser
tab. To make the phone trust the certificate:

- the most reliable way: a certificate from [mkcert](https://github.com/FiloSottile/mkcert) (`SSL_CERTFILE`,
  `SSL_KEYFILE` in `.env`), with its root certificate `rootCA.pem` installed on the phone
  (Android: Settings → Security → Encryption & credentials → Install a certificate → CA certificate;
  iPhone: open the file, install the profile, then General → About → Certificate Trust Settings);
- iPhone accepts the self-signed `certs/server.crt` the same way (profile + trust); Android may refuse to install it
  as a CA certificate.

Project data is not cached: everything comes from the server, and without a connection an offline page with a "Retry"
button is shown. App files are requested from the server first, so new versions arrive right after an update.
Password login does not break installation: `/sw.js` and `/manifest.webmanifest` are available without logging in.

## Configuration

| What | Where |
|---|---|
| Secrets (bot token, proxy, backup URL), HTTPS and port | `.env` (template: `.env.example`) |
| Language, date format, project cost, deadlines to phone calendar, schedule, notifications | Settings in the UI, stored in `config.json` |
| Password | Settings → Access; reset from the console: `python auth.py reset` |
| Theme background images | `static/backgrounds/`: `workbench_bg.jpg`, `synthwave_bg.jpg`, `pcb_bg.jpg` ship with the repository, `wh40k_bg.jpg` you add yourself (without an image the theme draws its own background), see [static/backgrounds/README.en.md](static/backgrounds/README.en.md) |

## Layout

```
app.py, bot.py, notify.py, scheduler.py, backup.py   server, bot, notifications, schedule, backup
auth.py, tls.py, common.py, database.py, i18n.py     login, HTTPS, shared helpers and config, DB models, translations
pipeline.py, vacation.py, tgproxy.py, storage.py     pipeline templates, vacation, Telegram proxy, used space
progress.py, logs.py                                 progress photos (web and bot), rotating logs
locales.json                                         all texts of the UI, the bot and notifications
templates/                                           page, login, components (nav, tabs, modals, toasts, pwa_head)
static/app.js, i18n.js, auth.js, dateinput.js        UI logic, translations, password hashing, date fields
static/calendar.js, progress.js, templates.js, trash.js   calendar, progress photos, templates and vacation, trash
static/dropzone.js, dropzone.css                        drag and drop of images (cover, references, progress)
static/themes.css, mobile.css, card-tab.css          themes, phone layout, card in a separate tab
static/sw.js, manifest.webmanifest, pwa.js, icons/, offline.html   PWA: service worker, manifest, install, icons, offline page
static/backgrounds/                                  theme background images
static/vendor/, static/fonts/                        Tailwind CSS, Chart.js and fonts (built by tools/build_assets.py)
tools/                                               check_locales.py, check_theme.py, build_assets.py, deploy.sh, clean.sh
logs/                                                app.log, notify.log, backup.log (created automatically)
PLANS.md                                             postponed ideas and decisions (in Russian)
```

## Checks

```bash
python tools/check_locales.py      # translation keys: what is used, what is missing
python tools/check_theme.py        # every theme in the registry (or one: theme-name)
python tools/build_assets.py css   # rebuild Tailwind CSS after new classes (without css: also fonts and Chart.js)
```

## Known limitations

- One user, no roles or permissions.
- HTTPS is off by default and the certificate is self-signed: traffic is encrypted, but every device shows a warning
  once. There is no automatic certificate renewal.
- No redirect from HTTP to HTTPS: there is one port, serving either HTTP or HTTPS.
- The backup does not include photos, references or progress photos.
- Without trusted HTTPS the PWA is limited (a shortcut instead of an app, no offline page).
- Catching up missed runs is limited: pushes within 3 hours on the same day, the backup within 24 hours, only the latest run.
- The database runs in WAL mode: do not copy `tracker.db` while the app is running (stop the app or use
  `sqlite3 tracker.db ".backup copy.db"`).
- Task names created from a template are stored in the language that was active at creation time.
