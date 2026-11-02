/* Core of the page: shared state, requests and errors, toasts, dialogs, tabs, dates, loading /api/data.
 * The other scripts (photos.js, settings.js, cards.js, analytics.js, then main.js which starts the page)
 * are plain scripts that share these globals; load order is set in templates/index.html. */

// ---------- Login: if the server asks for authorization, go to /login ----------
(function () {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async function (...args) {
        const res = await nativeFetch(...args);
        if (res.status === 401) {
            location.href = '/login';
        } else if (res.status === 403) {
            res.clone().json().then(j => { if (j && j.code === 'must_change') location.href = '/login'; }).catch(() => {});
        }
        return res;
    };
})();

let currentProjects = [];
let currentTasks = [];
let currentOpenProjectId = null;
let isDevMode = false;
let projectCost = 10;
let earnedTokensTotal = 0;
let notifySettings = {};
let scheduleSettings = {};
let languagesList = [];
let deleteKeepsBalance = true;
let stageChartInstance = null;
let velocityChartInstance = null;
let timeChartInstance = null;

// ---------- 24-hour time picker (two selects instead of input[type=time]) ----------
const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

function syncDayRow(key) {
    const on = document.getElementById(`cfgDayEn_${key}`).checked;
    document.getElementById(`cfgDayRow_${key}`).classList.toggle('opacity-40', !on);
    document.getElementById(`cfgDay_${key}H`).disabled = !on;
    document.getElementById(`cfgDay_${key}M`).disabled = !on;
}

function copyMondayTimeToAll() {
    const h = document.getElementById('cfgDay_monH').value;
    const m = document.getElementById('cfgDay_monM').value;
    DAY_KEYS.forEach(k => {
        document.getElementById(`cfgDay_${k}H`).value = h;
        document.getElementById(`cfgDay_${k}M`).value = m;
    });
}

function fillTimeSelects() {
    [...DAY_KEYS.map(k => `cfgDay_${k}`), 'cfgBackupTime'].forEach(prefix => {
        const h = document.getElementById(prefix + 'H');
        const m = document.getElementById(prefix + 'M');
        if (!h || !m) return;
        h.innerHTML = Array.from({length: 24}, (_, i) => `<option value="${String(i).padStart(2, '0')}">${String(i).padStart(2, '0')}</option>`).join('');
        m.innerHTML = Array.from({length: 60}, (_, i) => `<option value="${String(i).padStart(2, '0')}">${String(i).padStart(2, '0')}</option>`).join('');
    });
}
function setTimeSelect(prefix, value, fallback) {
    const [hh, mm] = (/^\d{2}:\d{2}$/.test(value || '') ? value : fallback).split(':');
    document.getElementById(prefix + 'H').value = hh;
    document.getElementById(prefix + 'M').value = mm;
}
function getTimeSelect(prefix) {
    return `${document.getElementById(prefix + 'H').value}:${document.getElementById(prefix + 'M').value}`;
}

// ---------- Confirmation dialog in the app style (instead of the system confirm) ----------
let confirmResolver = null;

function isConfirmOpen() {
    const el = document.getElementById('confirmModal');
    return !!el && !el.classList.contains('hidden');
}

function confirmDialog({ title = tr('confirm.title'), message = '', okText = tr('common.delete'), danger = true, icon = null } = {}) {
    if (confirmResolver) { confirmResolver(false); confirmResolver = null; }
    return new Promise(resolve => {
        confirmResolver = resolve;
        document.getElementById('confirmTitle').textContent = title;
        document.getElementById('confirmMessage').textContent = message;
        const iconEl = document.getElementById('confirmIcon');
        iconEl.textContent = icon || (danger ? '🗑' : '❔');
        iconEl.className = 'w-10 h-10 shrink-0 rounded-full flex items-center justify-center text-lg border ' +
            (danger ? 'bg-red-500/15 border-red-500/30' : 'bg-indigo-500/15 border-indigo-500/30');
        const ok = document.getElementById('confirmOk');
        ok.textContent = okText;
        ok.className = 'px-4 py-2 rounded-xl text-sm font-medium text-white outline-none focus:ring-2 ' +
            (danger ? 'bg-red-600 hover:bg-red-500 focus:ring-red-400' : 'bg-indigo-600 hover:bg-indigo-500 focus:ring-indigo-400');
        document.getElementById('confirmModal').classList.remove('hidden');
        setTimeout(() => document.getElementById('confirmCancel').focus(), 0);
    });
}

function resolveConfirm(value) {
    document.getElementById('confirmModal').classList.add('hidden');
    const resolve = confirmResolver;
    confirmResolver = null;
    if (resolve) resolve(value);
}

// ---------- A question with buttons and an optional text field (instead of the system prompt) ----------
// askDialog({ title, message, preview, input: {value, placeholder, maxLength}, buttons: [{value, label, primary}] })
// resolves to the chosen button value (with input: {value, text}) or null when cancelled.
let askResolver = null;
let askHasInput = false;

function isAskOpen() {
    const el = document.getElementById('askModal');
    return !!el && !el.classList.contains('hidden');
}

function askDialog({ title = '', message = '', preview = '', input = null, buttons = [] } = {}) {
    if (askResolver) { askResolver(null); askResolver = null; }
    return new Promise(resolve => {
        askResolver = resolve;
        askHasInput = !!input;
        document.getElementById('askTitle').textContent = title;
        document.getElementById('askMessage').textContent = message;
        const img = document.getElementById('askPreview');
        img.classList.toggle('hidden', !preview);
        if (preview) img.src = preview; else img.removeAttribute('src');
        const field = document.getElementById('askInput');
        field.classList.toggle('hidden', !input);
        field.value = input ? (input.value || '') : '';
        field.placeholder = input ? (input.placeholder || '') : '';
        field.maxLength = input && input.maxLength ? input.maxLength : 524288;
        const box = document.getElementById('askButtons');
        box.innerHTML = '';
        const all = [...buttons, { value: null, label: tr('common.cancel') }];
        all.forEach(b => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = b.label;
            btn.className = 'px-4 py-2 rounded-xl text-sm font-medium text-white outline-none focus:ring-2 ' +
                (b.primary ? 'bg-blue-600 hover:bg-blue-500 focus:ring-blue-400' : 'bg-slate-700 hover:bg-slate-600 focus:ring-slate-500');
            btn.dataset.primary = b.primary ? '1' : '';
            btn.onclick = () => resolveAsk(b.value);
            box.appendChild(btn);
        });
        document.getElementById('askModal').classList.remove('hidden');
        setTimeout(() => {
            if (input) { field.focus(); field.select(); }
            else (box.querySelector('button') || field).focus();
        }, 0);
    });
}

function resolveAsk(value) {
    document.getElementById('askModal').classList.add('hidden');
    document.getElementById('askPreview').removeAttribute('src');
    const resolve = askResolver;
    askResolver = null;
    if (!resolve) return;
    if (value === null) return resolve(null);
    resolve(askHasInput ? { value, text: document.getElementById('askInput').value } : value);
}

// Enter in the text field presses the main button
function askEnter() {
    const primary = document.querySelector('#askButtons button[data-primary="1"]');
    if (primary) primary.click();
}

// ---------- Keyed redraw: only the items whose markup changed are replaced ----------
// Lists (project cards, tasks) used to be rebuilt with innerHTML after every action: the scroll jumped, an open
// dropdown closed and every picture was created again. patchList keeps the DOM node of an item while its markup
// is the same, replaces only changed items and moves nodes into the new order.
// A kept item may still show a value the user picked and the app then put back (a cancelled dialog, a failed
// request): its selects are reset to the value written in the markup.
function htmlToNode(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    return tpl.content.firstElementChild;
}

function resetSelects(node) {
    node.querySelectorAll('select').forEach(sel => {
        const opt = [...sel.options].find(o => o.defaultSelected) || sel.options[0];
        if (opt && sel.value !== opt.value) sel.value = opt.value;
    });
}

function patchList(container, items, keyFn, htmlFn) {
    const old = new Map();
    [...container.children].forEach(el => { if (el.dataset.key) old.set(el.dataset.key, el); else el.remove(); });
    let prev = null;
    items.forEach(item => {
        const key = String(keyFn(item));
        const html = htmlFn(item);
        let node = old.get(key);
        if (node && node.__html === html) {
            resetSelects(node);
            old.delete(key);
        } else {
            if (node) old.delete(key);
            const fresh = htmlToNode(html);
            fresh.dataset.key = key;
            fresh.__html = html;
            if (node) node.replaceWith(fresh);
            node = fresh;
        }
        const want = prev ? prev.nextElementSibling : container.firstElementChild;
        if (want !== node) container.insertBefore(node, want);
        prev = node;
    });
    old.forEach(el => el.remove());
}

// Every spelling of "finished" the server may store (see DONE_STATUSES in common.py)
const DONE_STATUSES = ['finished', 'закончен', 'завершено', 'завершен', 'выполнено', 'completed'];
function isDoneStatus(status) {
    return DONE_STATUSES.includes(String(status ?? '').toLowerCase().trim());
}

// Tasks grouped by project id, rebuilt once per data load (renders used to filter the whole task list per project)
let tasksByProject = new Map();
function rebuildTaskIndex() {
    tasksByProject = new Map();
    currentTasks.forEach(t => {
        const list = tasksByProject.get(t['Project ID']);
        if (list) list.push(t); else tasksByProject.set(t['Project ID'], [t]);
    });
}
function projectTasks(projectId) {
    return tasksByProject.get(projectId) || [];
}

// Escapes user text before it goes into innerHTML
function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// POST/DELETE with a JSON body; always resolves to the parsed answer or {status: 'error'}
async function postJson(url, body, method = 'POST') {
    try {
        const res = await fetch(url, {
            method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
        });
        let data = null;
        try { data = await res.json(); } catch (e) { /* not JSON: a proxy page, a crash */ }
        if (!data || typeof data !== 'object') data = {};
        // a 4xx/5xx without our {status} (FastAPI 422, a 500) must not pass for success
        if (!res.ok && !data.status) data = { status: 'error', message: tr('err.save_failed') };
        if (data.status === 'error') invalidateData();
        return data;
    } catch (e) {
        invalidateData();
        return { status: 'error', message: tr('err.network') };     // no answer at all: the server is unreachable
    }
}

// postJson for actions whose only answer is ok/error: shows the error, true on success
async function postOk(url, body, method = 'POST') {
    const r = await postJson(url, body, method);
    if (r.status === 'error') { showToast(r.message || tr('err.save_failed'), 'error'); return false; }
    return true;
}

function sendAction(body) {
    return postJson('/api/action', body).then(r => {
        if (r.status === 'error') showToast(r.message || tr('err.save_failed'), 'error');
        loadData();
    });
}

// "No connection" while the server is unreachable: one toast per burst, not one per failed request
let lastNetToast = 0;
function showNetworkError(key = 'err.network') {
    const now = Date.now();
    if (now - lastNetToast < 8000) return;
    lastNetToast = now;
    showToast(tr(key), 'error');
}

// A request that failed and that no code caught (fetch rejects with TypeError on a network error):
// the user sees that the action did not go through instead of nothing happening.
window.addEventListener('unhandledrejection', (ev) => {
    const r = ev.reason;
    if (r instanceof TypeError && /fetch|network|load failed/i.test(String(r.message))) showNetworkError();
});

function showToast(message, type = 'error') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const colors = type === 'error'
        ? 'bg-red-950/95 border-red-500/60 text-red-200 shadow-red-950/50'
        : 'bg-emerald-950/95 border-emerald-500/60 text-emerald-200 shadow-emerald-950/50';
    const icon = type === 'error' ? '🚫' : '✅';

    toast.className = `pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl border shadow-2xl backdrop-blur-md transition-all duration-300 transform translate-y-2 opacity-0 ${colors}`;
    toast.innerHTML = `
        <span class="text-lg leading-none mt-0.5">${icon}</span>
        <div class="text-xs font-medium leading-relaxed flex-grow">${esc(message)}</div>
        <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-white leading-none text-base">&times;</button>
    `;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.remove('translate-y-2', 'opacity-0'));
    setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-2');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function switchTab(tab) {
    ['dashboard', 'allTasks', 'calendar', 'feed', 'analytics'].forEach(t => {
        document.getElementById(`view-${t}`).classList.toggle('hidden', t !== tab);
        document.getElementById(`tab-btn-${t}`).className = t === tab
            ? 'px-3.5 py-1.5 text-sm font-medium rounded-md bg-slate-700 text-white transition-colors shadow-sm'
            : 'px-3.5 py-1.5 text-sm font-medium rounded-md text-slate-400 hover:text-white hover:bg-slate-800 transition-colors';
    });
    if (tab === 'analytics') renderAnalytics();
    if (tab === 'feed') renderFeed();
    if (tab === 'calendar') renderCalendar();
}

// ---------- Windows and the keyboard ----------
// Every window is tracked in the order it was opened (a MutationObserver watches its "hidden" class, so windows
// opened by any code are covered). For the window on top:
// - Esc closes it (the handler below; the question, confirmation and viewer windows have their own Esc);
// - Tab and Shift+Tab move only between its own buttons and fields, never to the page behind it;
// - on opening, focus moves into it: the first field of a small form (on a computer only, so a phone does not pop
//   up the keyboard), otherwise the window itself; on closing, focus goes back to the button that opened it.
const DIALOG_IDS = ['askModal', 'confirmModal', 'lightbox', 'settingsModal', 'editProjectModal', 'editTaskModal',
    'taskModal', 'trashModal', 'calExportModal', 'templateModal', 'projectModal', 'projectDetailsModal'];
const FORM_DIALOGS = { projectModal: 'pName', taskModal: 'tName', editProjectModal: 'epName', editTaskModal: 'etName' };
const dialogStack = [];          // [{ id, back: the element that had focus before the window opened }]
const HAS_MOUSE = window.matchMedia('(hover: hover) and (pointer: fine)');

function isDialogShown(el) { return !!el && !el.classList.contains('hidden'); }

function topDialogId() {
    for (let i = dialogStack.length - 1; i >= 0; i--) {
        if (isDialogShown(document.getElementById(dialogStack[i].id))) return dialogStack[i].id;
    }
    return null;
}

function focusableIn(root) {
    return [...root.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
        .filter(el => !el.disabled && el.type !== 'hidden' && el.tabIndex >= 0 && el.getClientRects().length > 0);
}

function onDialogShown(el) {
    const i = dialogStack.findIndex(d => d.id === el.id);
    if (i >= 0) dialogStack.splice(i, 1);
    dialogStack.push({ id: el.id, back: document.activeElement });
    // after the code that opened the window had its chance to focus something (askDialog, confirmDialog do)
    setTimeout(() => {
        if (!isDialogShown(el) || el.contains(document.activeElement)) return;
        const field = FORM_DIALOGS[el.id] && HAS_MOUSE.matches ? document.getElementById(FORM_DIALOGS[el.id]) : null;
        if (field) { field.focus(); return; }
        if (!el.hasAttribute('tabindex')) { el.tabIndex = -1; el.style.outline = 'none'; }
        el.focus({ preventScroll: true });
    }, 0);
}

function onDialogHidden(el) {
    const i = dialogStack.findIndex(d => d.id === el.id);
    if (i < 0) return;
    const [entry] = dialogStack.splice(i, 1);
    const active = document.activeElement;
    const lost = !active || active === document.body || el.contains(active);
    const back = entry.back;
    // the button may have been redrawn meanwhile (lists keep unchanged items, but not changed ones)
    if (lost && back && back.isConnected && back.getClientRects().length > 0 && back !== document.body) {
        back.focus({ preventScroll: true });
    }
}

DIALOG_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    let shown = isDialogShown(el);
    if (shown) onDialogShown(el);
    new MutationObserver(() => {
        const now = isDialogShown(el);
        if (now === shown) return;
        shown = now;
        if (now) onDialogShown(el); else onDialogHidden(el);
    }).observe(el, { attributes: true, attributeFilter: ['class'] });
});

// Tab stays inside the top window (the date picker pop-up is its own small window: Tab works inside it as usual)
document.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    const id = topDialogId();
    if (!id) return;
    const active = document.activeElement;
    if (active && active.closest && active.closest('.dp-pop')) return;
    const root = document.getElementById(id);
    const items = focusableIn(root);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (!root.contains(active)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
    else if (e.shiftKey && (active === first || active === root)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
});

document.addEventListener('keydown', function(e) {
    if (isAskOpen()) {
        if (e.key === 'Escape') resolveAsk(null);
        else if (e.key === 'Enter' && askHasInput && document.activeElement === document.getElementById('askInput')) {
            e.preventDefault();
            askEnter();
        }
        return;   // otherwise Enter triggers the focused button
    }
    if (isConfirmOpen()) {
        if (e.key === 'Escape') resolveConfirm(false);
        return;   // Enter triggers the focused button ("Cancel" by default)
    }
    if (isLightboxOpen()) {
        if (e.key === 'Escape') closeLightbox();
        else if (e.key === 'ArrowLeft') lightboxStep(-1);
        else if (e.key === 'ArrowRight') lightboxStep(1);
        return;
    }
    if (document.activeElement && document.activeElement.tagName === 'TEXTAREA' && e.key === 'Enter') {
        return;
    }
    const topModal = topDialogId();
    if (!topModal) return;

    if (e.key === 'Escape') closeModal(topModal);
    else if (e.key === 'Enter') {
        // Enter on a button, a link or a list does what that element does (Tab can now reach "Cancel")
        const el = document.activeElement;
        if (el && ['BUTTON', 'A', 'SELECT'].includes(el.tagName)) return;
        e.preventDefault();
        if (topModal === 'projectModal') createProject();
        else if (topModal === 'editProjectModal') saveProjectEdit();
        else if (topModal === 'taskModal') createTask();
        else if (topModal === 'editTaskModal') saveTaskEdit();
        else if (topModal === 'settingsModal') saveSettings();
    }
});

// UI dates always include the year: tasks planned for next years must not look like past ones.
// Format from the settings: dmy = 21.03.2027 (default), mdy = 03/21/2027.
function formatDate(date) {
    const dd = String(date.getDate()).padStart(2, '0');
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const yyyy = date.getFullYear();
    return window.APP_DATE_FORMAT === 'mdy' ? `${mm}/${dd}/${yyyy}` : `${dd}.${mm}.${yyyy}`;
}

// 'YYYY-MM-DD' (how the server sends dates) -> a date in the app format; anything else is returned as is
function formatIsoDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? formatDate(new Date(+m[1], +m[2] - 1, +m[3])) : (iso || '');
}

function getDeadlineHTML(dateStr, isSmall = false, isDone = false) {
    if (!dateStr) return `<span class="text-slate-500 text-xs">${tr('deadline.none')}</span>`;
    const [y, m, d] = dateStr.split('-');
    const deadlineDate = new Date(y, m - 1, d);
    const today = new Date();
    today.setHours(0,0,0,0);
    const txt = formatDate(deadlineDate);

    if (isDone) {
        return `<span class="text-slate-500">${tr('stats.closed')} (${txt})</span>`;
    }

    const diffDays = Math.ceil((deadlineDate - today) / (1000 * 60 * 60 * 24));

    if (diffDays < 0) return `<span class="text-red-500 font-bold animate-pulse">⚠️ ${tr('deadline.overdue')} (${txt})</span>`;
    else if (diffDays <= 3) return `<span class="text-red-400 font-bold">🔥 ${tr('deadline.burning')} (${txt})</span>`;
    else return `<span class="text-slate-400"><span class="dl-icon">📅</span> ${txt}</span>`;
}

function sortTasks(tasksList) {
    tasksList.sort((a, b) => {
        const aDone = isDoneStatus(a.Status);
        const bDone = isDoneStatus(b.Status);
        if (aDone && !bDone) return 1;
        if (!aDone && bDone) return -1;
        if (aDone && bDone) {
            // Closed tasks: most recently completed first (ISO strings compare correctly)
            const aTs = a.CompletedTs || a.CompletedAt || '';
            const bTs = b.CompletedTs || b.CompletedAt || '';
            if (aTs !== bTs) return aTs < bTs ? 1 : -1;
            return (b.Deadline || '').localeCompare(a.Deadline || '');
        }
        if (!a.Deadline && !b.Deadline) return 0;
        if (!a.Deadline) return 1; 
        if (!b.Deadline) return -1;
        return new Date(a.Deadline) - new Date(b.Deadline); 
    });
    return tasksList;
}

// Several loads can overlap (window focus + an action); only the newest response is applied,
// otherwise an older answer arriving late would overwrite fresher data.
let tgCleanupMode = 'keep_46h';    // digest cleanup in the bot chat (Settings → Telegram)
let askSpentTime = true;           // the bot asks for the time spent when a task is closed (Settings → Telegram)

let loadSeq = 0;
// ETag of the last /api/data answer that was drawn. The server answers 304 (no body) while nothing changed,
// and then nothing is downloaded or redrawn. Cleared after a failed request (see postJson), so the next load
// draws again and puts back what the user changed on the page but the server did not accept.
let dataEtag = null;
function invalidateData() { dataEtag = null; }

async function loadData(force = false) {
    const seq = ++loadSeq;
    try {
        const headers = (!force && dataEtag) ? { 'If-None-Match': dataEtag } : {};
        const res = await fetch('/api/data', { cache: 'no-store', headers });
        if (res.status === 304) return;
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (seq !== loadSeq) return;
        dataEtag = res.headers.get('ETag');
        currentProjects = data.projects || [];
        currentTasks = data.tasks || [];
        rebuildTaskIndex();
        isDevMode = data.dev_mode;
        projectCost = data.project_cost ?? 10;
        earnedTokensTotal = data.earned_tokens ?? 0;
        notifySettings = data.notify_settings || {};
        scheduleSettings = data.schedule_settings || {};
        deleteKeepsBalance = data.delete_keeps_balance ?? true;
        applyCalendarExport(!!data.calendar_export);
        tgCleanupMode = data.tg_cleanup || 'keep_46h';
        askSpentTime = data.ask_spent_time ?? true;
        languagesList = data.languages || [];
        trashItems = data.trash || [];
        trashDays = data.trash_days || 30;
        renderVacation(data.vacation);
        
        document.getElementById('tokenCounter').innerText = (data.total_tokens ?? 0) + (isDevMode ? ' (DEV / MUTE)' : '');
        document.getElementById('projCostHint').innerText = isDevMode ? tr('project.free_dev') : `${tr('project.cost', { n: projectCost })} 💰`;
        renderProjects();
        renderTasks();
        renderAllTasks();
        renderTrashButton();
        if (!document.getElementById('view-calendar').classList.contains('hidden')) renderCalendar();
        if (!document.getElementById('view-feed').classList.contains('hidden')) renderFeed();
        if (!document.getElementById('view-analytics').classList.contains('hidden')) {
            renderAnalytics();
        }
        updateProjectSelect();
        if (currentOpenProjectId) openProjectDetails(currentOpenProjectId);
    } catch (err) {
        console.error('Data load failed:', err);
        if (seq === loadSeq) showNetworkError('err.load_failed');
    }
}

function switchSettingsTab(name) {
    const scroller = document.getElementById('cfgScroll');
    if (scroller) scroller.scrollTop = 0;            // a new tab starts from its top
    ['general', 'notifications', 'schedule', 'templates', 'backup', 'telegram', 'security'].forEach(k => {
        document.getElementById(`cfgTab-${k}`).classList.toggle('hidden', k !== name);
        const btn = document.getElementById(`cfgTabBtn-${k}`);
        const on = k === name;
        btn.classList.toggle('bg-slate-700', on);
        btn.classList.toggle('text-white', on);
        btn.classList.toggle('shadow-sm', on);
        btn.classList.toggle('text-slate-400', !on);
        btn.classList.toggle('hover:text-white', !on);
        btn.classList.toggle('hover:bg-slate-800', !on);
    });
    if (name === 'telegram') { startTelegramPolling(); loadTelegramProxy(); } else stopTelegramPolling();
    if (name === 'security') refreshAuthStatus();
    if (name === 'templates' && !tplLoaded) tplLoad();
    if (name === 'backup') loadStorage();
}

function getPriorityColor(priority) {
    const p = String(priority).toLowerCase();
    if (p.includes('critical')) return 'text-red-400 bg-red-400/10 border-red-400/20';
    if (p.includes('high')) return 'text-orange-400 bg-orange-400/10 border-orange-400/20';
    if (p.includes('low')) return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
    return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
}

function openModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { 
    if (id === 'projectDetailsModal' && typeof standaloneCardId !== 'undefined' && standaloneCardId) { closeStandaloneWindow(); return; }
    document.getElementById(id).classList.add('hidden'); 
    if (id === 'projectDetailsModal') {
        currentOpenProjectId = null; cardTabProjectId = null;
        delete document.getElementById('pdPaintNotes').dataset.projectId;     // reopening shows the stored notes
    }
    if (id === 'taskModal') { document.getElementById('tName').value = ''; document.getElementById('tDeadline').value = ''; }
    if (id === 'editTaskModal') { document.getElementById('etName').value = ''; document.getElementById('etDeadline').value = ''; }
    if (id === 'projectModal') { document.getElementById('pName').value = ''; document.getElementById('pEstimate').value = ''; document.getElementById('pToken').value = ''; }
    if (id === 'editProjectModal') { document.getElementById('epName').value = ''; document.getElementById('epEstimate').value = ''; }
}
