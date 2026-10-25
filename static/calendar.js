// ---------- Deadline calendar: a month grid with task and project deadlines ----------
let calMonth = null;          // first day of the month on screen
let calSelected = null;       // selected day, 'YYYY-MM-DD'

function calIso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const CAL_DONE = ['завершен', 'завершено', 'закончен', 'completed'];
function calIsDone(status) {
    return CAL_DONE.includes(String(status || '').toLowerCase().trim());
}

function calLang() {
    return window.APP_LANG || undefined;
}

// Deadlines by day: { 'YYYY-MM-DD': [{kind, id, projectId, name, projectName, done, overdue}] }
function calItemsByDay() {
    const today = calIso(new Date());
    const byId = {};
    currentProjects.forEach(p => { byId[p.ID] = p; });
    const map = {};
    const add = (day, item) => { (map[day] = map[day] || []).push(item); };
    currentProjects.forEach(p => {
        if (calIsDone(p.Status) || !p.Estimate) return;
        add(p.Estimate, { kind: 'project', id: p.ID, projectId: p.ID, name: p.Name, projectName: p.Name, done: false, overdue: p.Estimate < today });
    });
    currentTasks.forEach(t => {
        const p = byId[t['Project ID']];
        if (!p || calIsDone(p.Status) || !t.Deadline) return;
        const done = calIsDone(t.Status);
        add(t.Deadline, { kind: 'task', id: t.ID, projectId: p.ID, name: t.Task, projectName: p.Name, done, overdue: !done && t.Deadline < today });
    });
    return map;
}

function calChipClass(item) {
    if (item.done) return 'bg-slate-700/40 text-slate-500 line-through';
    if (item.overdue) return 'bg-red-500/20 text-red-300';
    return item.kind === 'project' ? 'bg-amber-500/20 text-amber-300' : 'bg-indigo-500/20 text-indigo-200';
}

// Dot color in the day list: the same colors as the chips in the grid
function calDotClass(item) {
    if (item.done) return 'bg-slate-500';
    if (item.overdue) return 'bg-red-400';
    return item.kind === 'project' ? 'bg-amber-400' : 'bg-indigo-400';
}

function calShift(delta) {
    if (!calMonth) calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + delta, 1);
    calSelected = null;
    renderCalendar();
}

function calToday() {
    const now = new Date();
    calMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    calSelected = calIso(now);
    renderCalendar();
}

function calSelect(day) {
    calSelected = calSelected === day ? null : day;
    renderCalendar();
}

function renderCalendar() {
    const view = document.getElementById('view-calendar');
    if (!view) return;
    if (!calMonth) calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    const lang = calLang();
    const items = calItemsByDay();
    const todayIso = calIso(new Date());
    const weekStart = window.APP_DATE_FORMAT === 'mdy' ? 0 : 1;      // US: the week starts on Sunday

    document.getElementById('calTitle').innerText = new Intl.DateTimeFormat(lang, { month: 'long', year: 'numeric' }).format(calMonth);
    const dayNames = [];
    for (let i = 0; i < 7; i++) {
        const d = new Date(2023, 0, 1 + ((weekStart + i) % 7));      // January 1, 2023 was a Sunday
        dayNames.push(new Intl.DateTimeFormat(lang, { weekday: 'short' }).format(d));
    }
    document.getElementById('calWeekdays').innerHTML = dayNames.map(n => `<div class="text-[11px] uppercase tracking-wider text-slate-500 text-center py-1">${esc(n)}</div>`).join('');

    const first = new Date(calMonth.getFullYear(), calMonth.getMonth(), 1);
    const offset = (first.getDay() - weekStart + 7) % 7;
    const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
    let cells = '';
    for (let i = 0; i < 42; i++) {
        const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
        const iso = calIso(d);
        const list = items[iso] || [];
        const inMonth = d.getMonth() === calMonth.getMonth();
        const shown = list.slice(0, 3);
        const chips = shown.map(it => `<div class="truncate text-[10px] leading-tight rounded px-1 py-0.5 ${calChipClass(it)}" title="${esc(it.projectName)}: ${esc(it.name)}">${esc(it.name)}</div>`).join('');
        const more = list.length > shown.length ? `<div class="text-[10px] text-slate-400">${tr('calendar.more', { n: list.length - shown.length })}</div>` : '';
        const ring = iso === calSelected ? 'ring-2 ring-indigo-400' : (iso === todayIso ? 'ring-1 ring-emerald-400/70' : '');
        cells += `
            <button type="button" onclick="calSelect('${iso}')" class="min-h-[4.5rem] sm:min-h-[6.5rem] text-left p-1 rounded-lg border border-slate-700/60 ${inMonth ? 'bg-slate-800' : 'bg-slate-900/50 opacity-60'} hover:border-indigo-400/60 transition-colors flex flex-col gap-0.5 ${ring}">
                <span class="text-xs ${iso === todayIso ? 'text-emerald-300 font-bold' : 'text-slate-300'}">${d.getDate()}</span>
                ${chips}${more}
            </button>`;
    }
    document.getElementById('calGrid').innerHTML = cells;

    const panel = document.getElementById('calDay');
    if (!calSelected) {
        panel.innerHTML = `<div class="text-sm text-slate-500">${tr('calendar.pick_day')}</div>`;
        return;
    }
    const list = items[calSelected] || [];
    const head = `<h3 class="text-sm font-semibold text-slate-200 mb-2">${esc(formatIsoDate(calSelected))}</h3>`;
    if (!list.length) {
        panel.innerHTML = head + `<div class="text-sm text-slate-500">${tr('calendar.day_empty')}</div>`;
        return;
    }
    panel.innerHTML = head + '<div class="space-y-1.5">' + list.map(it => `
        <div onclick="openProjectDetails('${esc(it.projectId)}')" class="flex items-center gap-2 bg-slate-900/70 border border-slate-700 rounded-lg px-3 py-2 cursor-pointer hover:border-indigo-400/60">
            <span class="shrink-0 w-2 h-2 rounded-full ${calDotClass(it)}"></span>
            <div class="min-w-0">
                <div class="text-sm ${it.done ? 'line-through text-slate-500' : 'text-slate-100'} truncate">${it.kind === 'project' ? tr('calendar.project_deadline') + ': ' : ''}${esc(it.name)}</div>
                ${it.kind === 'task' ? `<div class="text-[11px] text-slate-400 truncate">${esc(it.projectName)}</div>` : ''}
            </div>
        </div>`).join('') + '</div>';
}

// ---------- Calendar for a phone: download a .ics file or turn on a subscription address ----------
let calFeed = { enabled: false, path: null };

function calFeedUrl(path, scheme) {
    const url = new URL(path, window.location.origin);
    return scheme === 'webcal' ? url.href.replace(/^https?:/, 'webcal:') : url.href;
}

function renderCalendarFeed() {
    document.getElementById('calFeedEnabled').checked = calFeed.enabled;
    document.getElementById('calFeedBox').classList.toggle('hidden', !calFeed.enabled);
    if (!calFeed.enabled) return;
    document.getElementById('calFeedUrl').value = calFeedUrl(calFeed.path);
    document.getElementById('calFeedWebcal').href = calFeedUrl(calFeed.path, 'webcal');
}

async function openCalendarExport() {
    openModal('calExportModal');
    try {
        calFeed = await (await fetch('/api/calendar/feed')).json();
    } catch (e) { /* no network: keep what was shown */ }
    renderCalendarFeed();
}

async function postCalendarFeed(body) {
    try {
        const res = await fetch('/api/calendar/feed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await res.json();
        if (data.status !== 'ok') throw new Error(data.message || '');
        calFeed = data;
        return true;
    } catch (e) {
        showToast(e.message || tr('calexp.err_save'));
        return false;
    } finally {
        renderCalendarFeed();
    }
}

async function setCalendarFeed(enabled) {
    if (await postCalendarFeed({ enabled }) && enabled) showToast(tr('calexp.feed_on'), 'success');
}

async function newCalendarFeedAddress() {
    const ok = await confirmDialog({ title: tr('calexp.new_address'), message: tr('calexp.new_address_confirm'), okText: tr('calexp.new_address'), icon: '🔑' });
    if (!ok) return;
    if (await postCalendarFeed({ enabled: true, new_token: true })) showToast(tr('calexp.new_address_done'), 'success');
}

async function copyCalendarFeed() {
    const input = document.getElementById('calFeedUrl');
    try {
        await navigator.clipboard.writeText(input.value);
    } catch (e) {
        input.select();                           // http without a secure context: no clipboard API
        document.execCommand('copy');
    }
    showToast(tr('calexp.copied'), 'success');
}
