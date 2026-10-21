// ---------- Вход: если сервер просит авторизацию, уходим на /login ----------
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

// ---------- 24-часовой выбор времени (два select вместо input[type=time]) ----------
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

// ---------- Фото проектов ----------
const PHOTO_ICON = `<svg class="__SZ__" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"/></svg>`;

function projectPhotoInner(p, big = true) {
    if (p.Photo) return `<img src="${esc(p.Photo)}" alt="" class="w-full h-full object-cover" loading="lazy">`;
    const icon = PHOTO_ICON.replace('__SZ__', big ? 'w-10 h-10' : 'w-7 h-7');
    const plus = big ? 'bottom-3 right-4 text-xl' : 'bottom-1 right-2 text-sm';
    return `<div class="relative w-full h-full flex items-center justify-center text-slate-500">${icon}<span class="absolute ${plus} font-bold leading-none">+</span></div>`;
}

// Фото проекта (обложка): сжимаем в браузере до 1400px по длинной стороне, JPEG
function fileToJpegDataUrl(file, maxSide = 1400, quality = 0.88) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
            const w = Math.max(1, Math.round(img.width * scale));
            const h = Math.max(1, Math.round(img.height * scale));
            const canvas = document.createElement('canvas');
            canvas.width = w; canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#0f172a';
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(img, 0, 0, w, h);
            URL.revokeObjectURL(url);
            resolve(canvas.toDataURL('image/jpeg', quality));
        };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(tr('err.image_unreadable'))); };
        img.src = url;
    });
}

function pickProjectPhoto(id) {
    if (!id) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = () => { if (input.files && input.files[0]) uploadProjectPhoto(id, input.files[0]); };
    input.click();
}

async function uploadProjectPhoto(id, file) {
    try {
        const dataUrl = await fileToJpegDataUrl(file);
        const res = await fetch(`/api/projects/${id}/photo`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ image: dataUrl }) });
        const result = await res.json();
        if (result.status === 'error') return showToast(result.message || tr('photo.err_save'), 'error');
        showToast(tr('toast.photo_updated'), 'success');
        loadData();
    } catch (err) {
        showToast(err.message || tr('photo.err_upload'), 'error');
    }
}

async function removeProjectPhoto(id) {
    if (!id) return;
    if (!(await confirmDialog({ title: tr('photo.remove_title'), message: tr('photo.remove_text'), okText: tr('common.remove') }))) return;
    await fetch(`/api/projects/${id}/photo`, { method: 'DELETE' });
    showToast(tr('toast.photo_removed'), 'success');
    loadData();
}

// ---------- Подтверждение в стиле интерфейса (вместо системного confirm) ----------
let confirmResolver = null;

function isConfirmOpen() {
    const el = document.getElementById('confirmModal');
    return !!el && !el.classList.contains('hidden');
}

function confirmDialog({ title = tr('confirm.title'), message = '', okText = tr('common.delete'), danger = true } = {}) {
    if (confirmResolver) { confirmResolver(false); confirmResolver = null; }
    return new Promise(resolve => {
        confirmResolver = resolve;
        document.getElementById('confirmTitle').textContent = title;
        document.getElementById('confirmMessage').textContent = message;
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

// ---------- Карточка проекта в отдельном окне ----------
function openCardTab(id) {
    if (!id) return;
    const url = `/?card=${encodeURIComponent(id)}`;
    // без параметров окна браузер открывает обычную вкладку; одно и то же имя — повторный клик
    // по тому же проекту переключается на уже открытую вкладку, а не плодит новые
    const tab = window.open(url, `card_${id}`);
    if (!tab) showToast(tr('toast.popup_blocked'));
}

function closeStandaloneWindow() {
    window.close();
    // если браузер не дал закрыть окно (оно открыто не из приложения) — вернёмся на дашборд
    setTimeout(() => { location.href = '/'; }, 150);
}

async function initStandaloneCard(id) {
    document.body.classList.add('card-standalone');      // широкая раскладка, см. static/card-tab.css
    document.querySelector('nav').classList.add('hidden');
    ['view-dashboard', 'view-allTasks', 'view-analytics'].forEach(v => document.getElementById(v).classList.add('hidden'));
    document.getElementById('pdOpenWindow').classList.add('hidden');
    await loadData();
    const project = currentProjects.find(p => p.ID === id);
    if (!project) {
        showToast(tr('err.project_not_found_deleted'));
        setTimeout(() => { location.href = '/'; }, 1800);
        return;
    }
    document.title = project.Name;
    openProjectDetails(id);
}

// ---------- Клик по фото: нет фото -> выбор файла, есть -> раскрытие ----------
function onProjectPhotoClick(id) {
    const p = currentProjects.find(x => x.ID === id);
    if (!p) return;
    if (p.Photo) openLightbox([{ url: p.Photo }], 0);
    else pickProjectPhoto(id);
}

// ---------- Просмотр на весь экран ----------
let lbItems = [];
let lbIndex = 0;

function isLightboxOpen() {
    const el = document.getElementById('lightbox');
    return !!el && !el.classList.contains('hidden');
}

function openLightbox(items, index = 0) {
    lbItems = items;
    lbIndex = index;
    document.getElementById('lightbox').classList.remove('hidden');
    renderLightbox();
}

function renderLightbox() {
    const item = lbItems[lbIndex];
    if (!item) return closeLightbox();
    document.getElementById('lbImg').src = item.url;
    document.getElementById('lbOpen').href = item.url;
    const multi = lbItems.length > 1;
    document.getElementById('lbPrev').classList.toggle('hidden', !multi);
    document.getElementById('lbNext').classList.toggle('hidden', !multi);
    document.getElementById('lbCaption').innerText = [multi ? `${lbIndex + 1} / ${lbItems.length}` : '', item.caption || ''].filter(Boolean).join('  ·  ');
}

function lightboxStep(delta) {
    if (lbItems.length < 2) return;
    lbIndex = (lbIndex + delta + lbItems.length) % lbItems.length;
    renderLightbox();
}

function closeLightbox() {
    document.getElementById('lightbox').classList.add('hidden');
    document.getElementById('lbImg').removeAttribute('src');
    lbItems = [];
}

// ---------- Референсы для покраса: оригиналы без сжатия, до 10 шт. ----------
const MAX_REFS = 10;
const MAX_REF_MB = 25;

function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error(tr('err.file_unreadable')));
        reader.readAsDataURL(file);
    });
}

function renderProjectRefs(project) {
    const refs = project.Refs || [];
    document.getElementById('pdRefsCount').innerText = `${refs.length}/${MAX_REFS}`;
    document.getElementById('pdRefsAdd').classList.toggle('hidden', refs.length >= MAX_REFS);
    const box = document.getElementById('pdRefs');
    if (refs.length === 0) {
        box.innerHTML = `<div class="col-span-full text-xs text-slate-600 py-2">${tr('ref.empty')}</div>`;
        return;
    }
    box.innerHTML = refs.map((r, i) => `
        <div class="relative group aspect-square rounded-lg overflow-hidden border border-slate-700 bg-slate-950 cursor-pointer hover:border-sky-400 transition-colors" onclick="openProjectRefs('${project.ID}', ${i})">
            <img src="${esc(r.Thumb)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover">
            <button onclick="event.stopPropagation(); removeProjectRef('${project.ID}', ${r.ID})" class="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/70 text-white/80 hover:bg-red-600 hover:text-white text-xs leading-none opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity" title="${tr('ref.delete')}">&times;</button>
        </div>`).join('');
}

function openProjectRefs(projectId, index) {
    const p = currentProjects.find(x => x.ID === projectId);
    if (!p || !p.Refs) return;
    openLightbox(p.Refs.map(r => ({ url: r.Url })), index);
}

function pickProjectRefs(id) {
    if (!id) return;
    const p = currentProjects.find(x => x.ID === id);
    const free = MAX_REFS - ((p && p.Refs) ? p.Refs.length : 0);
    if (free <= 0) return showToast(tr('ref.limit', { n: MAX_REFS }));
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp,image/gif';
    input.multiple = true;
    input.onchange = () => { if (input.files && input.files.length) uploadProjectRefs(id, Array.from(input.files), free); };
    input.click();
}

async function uploadProjectRefs(id, files, free) {
    if (files.length > free) {
        showToast(tr('ref.can_add_more', { n: free }));
        files = files.slice(0, free);
    }
    const btn = document.getElementById('pdRefsAdd');
    btn.disabled = true;
    btn.innerText = tr('common.uploading');
    let ok = 0;
    for (const file of files) {
        try {
            if (file.size > MAX_REF_MB * 1024 * 1024) throw new Error(tr('toast.file_too_big_named', { name: file.name, n: MAX_REF_MB }));
            const [image, thumb] = await Promise.all([fileToDataUrl(file), fileToJpegDataUrl(file, 400, 0.8)]);
            const res = await fetch(`/api/projects/${id}/refs`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ image, thumb }) });
            const result = await res.json();
            if (result.status === 'error') throw new Error(result.message || tr('ref.err_save'));
            ok++;
        } catch (err) {
            showToast(err.message || tr('ref.err_upload'), 'error');
        }
    }
    btn.disabled = false;
    btn.innerText = tr('ref.add_btn');
    if (ok) showToast(ok === 1 ? tr('toast.ref_added') : tr('toast.refs_added', { n: ok }), 'success');
    loadData();
}

async function removeProjectRef(projectId, refId) {
    if (!(await confirmDialog({ title: tr('ref.delete_title'), message: tr('confirm.delete_ref_text') }))) return;
    await fetch(`/api/projects/${projectId}/refs/${refId}`, { method: 'DELETE' });
    loadData();
}

// Экранирование пользовательского текста перед вставкой в innerHTML
function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

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

document.addEventListener('keydown', function(e) {
    if (isConfirmOpen()) {
        if (e.key === 'Escape') resolveConfirm(false);
        return;   // Enter срабатывает на кнопке в фокусе (по умолчанию — «Отмена»)
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
    const modals = ['trashModal', 'templateModal', 'settingsModal', 'editProjectModal', 'editTaskModal', 'taskModal', 'projectDetailsModal', 'projectModal'];
    const active = modals.filter(id => !document.getElementById(id).classList.contains('hidden'));
    if (active.length === 0) return;
    const topModal = active[0];

    if (e.key === 'Escape') closeModal(topModal);
    else if (e.key === 'Enter') {
        e.preventDefault();
        if (topModal === 'projectModal') createProject();
        else if (topModal === 'editProjectModal') saveProjectEdit();
        else if (topModal === 'taskModal') createTask();
        else if (topModal === 'editTaskModal') saveTaskEdit();
        else if (topModal === 'settingsModal') saveSettings();
    }
});

// Дата для интерфейса всегда с годом: задачи на следующие годы не должны выглядеть прошедшими.
// Формат из настроек: dmy = 21.03.2027 (по умолчанию), mdy = 03/21/2027.
function formatDate(date) {
    const dd = String(date.getDate()).padStart(2, '0');
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const yyyy = date.getFullYear();
    return window.APP_DATE_FORMAT === 'mdy' ? `${mm}/${dd}/${yyyy}` : `${dd}.${mm}.${yyyy}`;
}

// 'YYYY-MM-DD' (так даты приходят с сервера) -> дата по локали; непонятную строку возвращает как есть
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
        const aDone = ['завершен', 'completed', 'закончен'].includes(String(a.Status).toLowerCase());
        const bDone = ['завершен', 'completed', 'закончен'].includes(String(b.Status).toLowerCase());
        if (aDone && !bDone) return 1;
        if (!aDone && bDone) return -1;
        if (!a.Deadline && !b.Deadline) return 0;
        if (!a.Deadline) return 1; 
        if (!b.Deadline) return -1;
        return new Date(a.Deadline) - new Date(b.Deadline); 
    });
    return tasksList;
}

async function loadData() {
    try {
        const res = await fetch('/api/data');
        const data = await res.json();
        currentProjects = data.projects;
        currentTasks = data.tasks;
        isDevMode = data.dev_mode;
        projectCost = data.project_cost ?? 10;
        earnedTokensTotal = data.earned_tokens ?? 0;
        notifySettings = data.notify_settings || {};
        scheduleSettings = data.schedule_settings || {};
        deleteKeepsBalance = data.delete_keeps_balance ?? true;
        languagesList = data.languages || [];
        trashItems = data.trash || [];
        trashDays = data.trash_days || 30;
        
        document.getElementById('tokenCounter').innerText = data.total_tokens + (isDevMode ? ' (DEV / MUTE)' : '');
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
    } catch (err) { console.error("Ошибка загрузки:", err); }
}

function switchSettingsTab(name) {
    ['general', 'notifications', 'schedule', 'backup', 'telegram', 'security'].forEach(k => {
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
    if (name === 'telegram') startTelegramPolling(); else stopTelegramPolling();
    if (name === 'security') refreshAuthStatus();
}

// ---------- Доступ: вход по паролю (по умолчанию выключен) ----------
let authState = null;

function renderAuthState(st) {
    authState = st;
    const badge = document.getElementById('authBadge');
    badge.textContent = st.enabled ? tr('auth.state_on') : tr('auth.state_off');
    badge.className = 'ml-3 shrink-0 text-xs px-2.5 py-1 rounded-full border ' + (st.enabled
        ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30'
        : 'text-slate-300 bg-slate-500/10 border-slate-500/30');
    document.getElementById('authUserName').textContent = st.username;
    document.getElementById('authToggleBtn').textContent = st.enabled ? tr('auth.disable') : tr('auth.enable');
    document.getElementById('authLogoutBtn').classList.toggle('hidden', !st.enabled);
    const hint = document.getElementById('authDefaultHint');
    hint.textContent = st.must_change ? tr('auth.default_creds_hint', { user: st.username }) : '';
    hint.classList.toggle('hidden', !st.must_change);
    // тип соединения: по http перехваченный digest можно переслать повторно (см. README, HTTPS)
    const secure = location.protocol === 'https:';
    const conn = document.getElementById('authConnHint');
    conn.textContent = secure ? tr('auth.conn_https') : tr('auth.conn_http');
    conn.className = 'text-xs ' + (secure ? 'text-emerald-300' : 'text-slate-400');
}

async function refreshAuthStatus() {
    try {
        const res = await fetch('/api/auth/status');
        renderAuthState(await res.json());
    } catch (e) { /* сеть недоступна — оставляем как есть */ }
}

async function authPost(url, body) {
    const res = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {}) });
    return res.json().catch(() => ({ status: 'error', message: tr('auth.err_bad_request') }));
}

async function authToggle() {
    if (!authState) return;
    if (!authState.enabled) {
        const r = await authPost('/api/auth/enable', { enabled: true });
        if (r.status !== 'ok') return showToast(r.message || tr('auth.err_bad_request'));
        if (r.must_change) {                      // пароль по умолчанию: сначала смена на странице входа
            showToast(tr('auth.toast_enabled_change'), 'success');
            setTimeout(() => { location.href = '/login'; }, 900);
            return;
        }
        showToast(tr('auth.toast_enabled'), 'success');
        refreshAuthStatus();
        return;
    }
    const pwd = document.getElementById('authPwdOld').value;
    if (!pwd) return showToast(tr('auth.disable_need_password'));
    const r = await authPost('/api/auth/enable', { enabled: false, digest: phDigest(authState.username, pwd) });
    if (r.status !== 'ok') return showToast(r.message || tr('auth.err_bad_request'));
    document.getElementById('authPwdOld').value = '';
    showToast(tr('auth.toast_disabled'), 'success');
    refreshAuthStatus();
}

async function authChangePassword() {
    if (!authState) return;
    const oldPwd = document.getElementById('authPwdOld').value;
    const n1 = document.getElementById('authPwdNew').value;
    const n2 = document.getElementById('authPwdNew2').value;
    if (!oldPwd) return showToast(tr('auth.err_required'));
    if (n1.length < PH_MIN_PASSWORD) return showToast(tr('auth.err_short', { n: PH_MIN_PASSWORD }));
    if (n1 !== n2) return showToast(tr('auth.err_mismatch'));
    const r = await authPost('/api/auth/change', {
        old_digest: phDigest(authState.username, oldPwd),
        new_digest: phDigest(authState.username, n1),
    });
    if (r.status !== 'ok') return showToast(r.message || tr('auth.err_bad_request'));
    ['authPwdOld', 'authPwdNew', 'authPwdNew2'].forEach(id => { document.getElementById(id).value = ''; });
    showToast(tr('auth.toast_changed'), 'success');
    refreshAuthStatus();
}

async function authLogout() {
    await authPost('/api/auth/logout');
    location.href = '/login';
}

// ---------- Telegram: привязка бота к чату ----------
let tgState = null;
let tgPollTimer = null;

function renderTelegramState(st) {
    tgState = st;
    const badge = document.getElementById('tgStatusBadge');
    document.getElementById('tgBound').classList.toggle('hidden', !st.bound);
    document.getElementById('tgUnbound').classList.toggle('hidden', st.bound);
    if (st.bound) {
        badge.textContent = tr('tg.state_linked');
        badge.className = 'ml-3 shrink-0 text-xs px-2.5 py-1 rounded-full border text-emerald-300 bg-emerald-500/10 border-emerald-500/30';
        document.getElementById('tgChatId').textContent = st.chat_id;
    } else {
        badge.textContent = tr('tg.state_unlinked');
        badge.className = 'ml-3 shrink-0 text-xs px-2.5 py-1 rounded-full border text-amber-300 bg-amber-500/10 border-amber-500/30';
        document.getElementById('tgCode').textContent = st.pairing_code || '';
    }
}

async function refreshTelegramState() {
    try {
        const res = await fetch('/api/telegram');
        const st = await res.json();
        const wasUnbound = tgState && !tgState.bound;
        renderTelegramState(st);
        if (wasUnbound && st.bound) showToast(tr('toast.chat_linked'), 'success');
    } catch (e) { /* сеть недоступна — повторим при следующем опросе */ }
}

function startTelegramPolling() {
    stopTelegramPolling();
    refreshTelegramState();
    tgPollTimer = setInterval(() => {
        const modalHidden = document.getElementById('settingsModal').classList.contains('hidden');
        const tabHidden = document.getElementById('cfgTab-telegram').classList.contains('hidden');
        if (modalHidden || tabHidden) { stopTelegramPolling(); return; }
        refreshTelegramState();   // заметит привязку, сделанную из Telegram
    }, 3000);
}

function stopTelegramPolling() {
    if (tgPollTimer) clearInterval(tgPollTimer);
    tgPollTimer = null;
}

async function unlinkTelegram() {
    const ok = await confirmDialog({
        title: tr('tg.unlink_title'),
        message: tr('tg.unlink_text'),
        okText: tr('tg.unlink_btn')
    });
    if (!ok) return;
    const res = await fetch('/api/telegram/unlink', { method: 'POST' });
    renderTelegramState(await res.json());
    showToast(tr('toast.chat_unlinked'), 'success');
}

async function newTelegramCode() {
    const res = await fetch('/api/telegram/code', { method: 'POST' });
    const r = await res.json();
    if (r.status === 'error') return showToast(r.message, 'error');
    renderTelegramState(r);
    showToast(tr('tg.new_code_created'), 'success');
}

function openSettingsModal() {
    const langs = languagesList.length ? languagesList : [{ code: window.APP_LANG || 'ru-RU', name: tr('meta.name') }];
    document.getElementById('cfgLanguage').innerHTML = langs
        .map(l => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join('');
    document.getElementById('cfgLanguage').value = window.APP_LANG || 'ru-RU';
    document.getElementById('cfgDateFormat').value = window.APP_DATE_FORMAT === 'mdy' ? 'mdy' : 'dmy';
    switchSettingsTab('general');
    document.getElementById('cfgDevMode').checked = isDevMode;
    document.getElementById('cfgProjectCost').value = projectCost;
    document.getElementById('cfgDeleteKeepsBalance').checked = deleteKeepsBalance;
    document.getElementById('cfgAllowPostpone').checked = notifySettings.allow_postpone ?? true;
    document.getElementById('cfgStagnationAlert').checked = notifySettings.stagnation_alert ?? true;
    document.getElementById('cfgStagnationDays').value = notifySettings.stagnation_days ?? 10;
    document.getElementById('cfgFridayDigest').checked = notifySettings.friday_digest ?? true;
    document.getElementById('cfgMonthlyReport').checked = notifySettings.monthly_report ?? true;
    document.getElementById('cfgNotifyEnabled').checked = scheduleSettings.notify_enabled ?? true;
    DAY_KEYS.forEach(k => {
        const d = (scheduleSettings.notify_days || {})[k] || {};
        document.getElementById(`cfgDayEn_${k}`).checked = d.enabled ?? true;
        setTimeSelect(`cfgDay_${k}`, d.time, (k === 'sat' || k === 'sun') ? '12:05' : '18:05');
        syncDayRow(k);
    });
    document.getElementById('cfgBackupEnabled').checked = scheduleSettings.backup_enabled ?? false;
    setTimeSelect('cfgBackupTime', scheduleSettings.backup_time, '03:00');
    openModal('settingsModal');
}

async function saveSettings() {
    const costVal = parseFloat(document.getElementById('cfgProjectCost').value);
    const body = {
        dev_mode: document.getElementById('cfgDevMode').checked,
        project_cost: isNaN(costVal) ? 10 : costVal,
        delete_keeps_balance: document.getElementById('cfgDeleteKeepsBalance').checked,
        allow_postpone: document.getElementById('cfgAllowPostpone').checked,
        stagnation_alert: document.getElementById('cfgStagnationAlert').checked,
        stagnation_days: parseInt(document.getElementById('cfgStagnationDays').value) || 10,
        friday_digest: document.getElementById('cfgFridayDigest').checked,
        monthly_report: document.getElementById('cfgMonthlyReport').checked,
        notify_enabled: document.getElementById('cfgNotifyEnabled').checked,
        notify_days: Object.fromEntries(DAY_KEYS.map(k => [k, {
            enabled: document.getElementById(`cfgDayEn_${k}`).checked,
            time: getTimeSelect(`cfgDay_${k}`)
        }])),
        backup_enabled: document.getElementById('cfgBackupEnabled').checked,
        backup_time: getTimeSelect('cfgBackupTime'),
        language: document.getElementById('cfgLanguage').value,
        date_format: document.getElementById('cfgDateFormat').value
    };
    const res = await fetch('/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const result = await res.json();
    if (result.status === 'error') return showToast(result.message || tr('settings.err_save'), 'error');
    closeModal('settingsModal');
    if (body.language !== (window.APP_LANG || 'ru-RU') || body.date_format !== (window.APP_DATE_FORMAT || 'dmy')) { location.reload(); return; }   // новый язык — перерисовать всю страницу
    showToast(tr('toast.settings_saved'), "success");
    loadData();
}

async function triggerTestNotify() {
    await fetch('/api/notify/test', { method: 'POST' });
    showToast(tr('toast.test_push_sent'), "success");
}

function getPriorityColor(priority) {
    const p = String(priority).toLowerCase();
    if (p.includes('critical')) return 'text-red-400 bg-red-400/10 border-red-400/20';
    if (p.includes('high')) return 'text-orange-400 bg-orange-400/10 border-orange-400/20';
    if (p.includes('low')) return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
    return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
}

// ---------- Поиск по проектам ----------
// Строка запроса — несколько слов через пробел, все должны встретиться (без учёта регистра и ё/е).
// Ищет по названию, номеру, приоритету, статусу, стадии (и по русскому значению, и по переводу на язык интерфейса),
// рецепту краски и названиям задач проекта.
const searchQuery = { active: '', hall: '' };
const STATUS_TR = { 'В планах': 'status.planned', 'В работе': 'status.in_work', 'На паузе': 'status.on_hold', 'Закончен': 'status.finished' };
const STAGE_TR = { 'Печать': 'stage.printing', 'Шлифовка': 'stage.sanding', 'Сборка': 'stage.assembly', 'Грунт': 'stage.priming', 'Покрас': 'stage.painting' };

function normSearch(s) { return String(s == null ? '' : s).toLowerCase().replace(/ё/g, 'е'); }

function projectMatches(p, query) {
    const words = normSearch(query).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const parts = [p.Name, p.ID, p.Priority, p.Status, p.Stage, p.PaintNotes];
    if (STATUS_TR[p.Status]) parts.push(tr(STATUS_TR[p.Status]));
    if (STAGE_TR[p.Stage]) parts.push(tr(STAGE_TR[p.Stage]));
    currentTasks.forEach(t => { if (t['Project ID'] === p.ID) parts.push(t.Task); });
    const hay = normSearch(parts.filter(x => x != null && x !== '').join(' \n '));
    return words.every(w => hay.includes(w));
}

function onProjectSearch(which, value) {
    searchQuery[which] = value || '';
    if (which === 'hall') renderHall(); else renderProjects();
}

function renderProjects() {
    const container = document.getElementById('projectsContainer');
    const allActive = currentProjects.filter(p => !['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    const activeProjects = allActive.filter(p => projectMatches(p, searchQuery.active));
    const searching = searchQuery.active.trim() !== '';
    document.getElementById('projectCount').innerText = searching ? `${activeProjects.length} / ${allActive.length}` : allActive.length;
    if (searching && !activeProjects.length) {
        container.innerHTML = `<div class="col-span-full text-center py-8 text-slate-500 bg-slate-800/40 rounded-2xl border border-slate-800">${tr('search.nothing')}</div>`;
        return;
    }

    container.innerHTML = activeProjects.map(p => {
        const pTasks = currentTasks.filter(t => t['Project ID'] === p.ID);
        const doneCount = pTasks.filter(t => ['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase())).length;
        const activeCount = pTasks.length - doneCount;
        const hasPaintNotes = p.PaintNotes && p.PaintNotes.trim().length > 0;

        return `
        <div class="bg-slate-800 rounded-2xl p-4 border border-slate-700/50 hover:border-slate-600 transition-colors shadow flex flex-col gap-3 h-full group relative">
            <button onclick="event.stopPropagation(); openCardTab('${p.ID}')" class="absolute top-3 right-10 text-slate-500 hover:text-sky-400 transition-colors z-10" title="${tr('project.open_card_tab')}">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
            </button>
            <button onclick="event.stopPropagation(); deleteProject('${p.ID}')" class="absolute top-3 right-3 text-slate-500 hover:text-red-400 transition-colors z-10" title="${tr('project.delete')}">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
            </button>

            <div class="flex gap-3 sm:gap-4 flex-grow">
                <div onclick="event.stopPropagation(); onProjectPhotoClick('${p.ID}')" title="${p.Photo ? tr('photo.open') : tr('photo.add')}" class="w-20 h-20 sm:w-32 sm:h-32 shrink-0 rounded-xl border border-slate-600/60 bg-slate-900/60 overflow-hidden cursor-pointer hover:border-indigo-400 transition-colors">
                    ${projectPhotoInner(p)}
                </div>

                <div onclick="openProjectDetails('${p.ID}')" class="cursor-pointer flex-grow min-w-0">
                    <h3 class="text-base font-semibold text-white mb-2 pr-14 sm:pr-16 line-clamp-2 sm:truncate break-words" title="${esc(p.Name)}">${esc(p.Name)}</h3>

                    <div class="flex items-center gap-2 mb-3 flex-wrap">
                        ${p['Token Reward'] > 0 ? `<span class="text-[10px] text-amber-400 font-bold bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">+${p['Token Reward']}</span>` : ''}
                        <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${getPriorityColor(p.Priority)}">${esc(p.Priority)}</span>
                        ${hasPaintNotes ? `<span class="text-[10px] bg-pink-500/10 border border-pink-500/30 text-pink-300 px-1.5 py-0.5 rounded" title="${tr('recipe.exists')}">🎨</span>` : ''}
                        ${(p.Refs && p.Refs.length) ? `<span class="text-[10px] bg-sky-500/10 border border-sky-500/30 text-sky-300 px-1.5 py-0.5 rounded" title="${tr('ref.title')}">🖼 ${p.Refs.length}</span>` : ''}
                        <div class="flex items-center gap-2 text-[11px] bg-slate-900/50 px-2 py-0.5 rounded-md border border-slate-700/50 ml-auto">
                            <span class="flex items-center gap-1 text-emerald-400" title="${tr('common.completed')}">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>
                                ${doneCount}
                            </span>
                            <span class="text-slate-600">|</span>
                            <span class="flex items-center gap-1 text-indigo-400" title="${tr('task.pending_hint')}">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path></svg>
                                ${activeCount}
                            </span>
                        </div>
                    </div>
                    <div class="text-xs">${getDeadlineHTML(p.Estimate)}</div>
                </div>
            </div>

            <div class="pt-3 border-t border-slate-700/50 flex items-center justify-between gap-2">
                <select onclick="event.stopPropagation()" onchange="updateProjectStatus('${p.ID}', this.value)" class="w-1/2 text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer">
                    <option value="В планах" ${p.Status === 'В планах' ? 'selected' : ''}>${tr('status.planned')}</option>
                    <option value="В работе" ${p.Status === 'В работе' ? 'selected' : ''}>${tr('status.in_work')}</option>
                    <option value="На паузе" ${p.Status === 'На паузе' ? 'selected' : ''}>${tr('status.on_hold')}</option>
                    <option value="Закончен">${tr('project.complete')} ✔️</option>
                </select>
                <select onclick="event.stopPropagation()" onchange="updateProjectStage('${p.ID}', this.value)" class="w-1/2 text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer">
                    <option value="—" ${p.Stage === '—' ? 'selected' : ''}>${tr('project.stage')}: —</option>
                    <option value="Печать" ${p.Stage === 'Печать' ? 'selected' : ''}>${tr('stage.printing')}</option>
                    <option value="Шлифовка" ${p.Stage === 'Шлифовка' ? 'selected' : ''}>${tr('stage.sanding')}</option>
                    <option value="Сборка" ${p.Stage === 'Сборка' ? 'selected' : ''}>${tr('stage.assembly')}</option>
                    <option value="Грунт" ${p.Stage === 'Грунт' ? 'selected' : ''}>${tr('stage.priming')}</option>
                    <option value="Покрас" ${p.Stage === 'Покрас' ? 'selected' : ''}>${tr('stage.painting')}</option>
                </select>
            </div>
        </div>`;
    }).join('');
}

function renderTasks() {
    const container = document.getElementById('tasksContainer');
    let activeTasks = currentTasks.filter(t => !['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase()));
    activeTasks.sort((a, b) => {
        if (!a.Deadline && !b.Deadline) return 0;
        if (!a.Deadline) return 1;
        if (!b.Deadline) return -1;
        return new Date(a.Deadline) - new Date(b.Deadline);
    });
    
    container.innerHTML = activeTasks.slice(0, 5).map(t => {
        const proj = currentProjects.find(p => p.ID === t['Project ID']);
        const isTaskDone = ['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase());
        return `
        <div class="p-3 hover:bg-slate-750 transition-colors relative group">
            <div class="absolute top-3 right-3 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 p-1" title="${tr('common.edit')}">✏️</button>
                <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 p-1" title="${tr('common.delete')}">✕</button>
            </div>
            <div class="flex flex-wrap justify-between items-start gap-x-2 mb-1 pr-14">
                <span class="text-[10px] uppercase tracking-wider font-semibold text-indigo-400 truncate max-w-[10rem]" title="${esc(proj ? proj.Name : '')}">${esc(proj ? proj.Name : t['Project ID'])}</span>
                <div class="text-xs">${getDeadlineHTML(t.Deadline, true, isTaskDone)}</div>
            </div>
            <p class="text-sm text-slate-200 font-medium mb-2 leading-snug">${esc(t.Task)}</p>
            <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer w-32">
                <option value="Не начато" ${t.Status === 'Не начато' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                <option value="В процессе" ${t.Status === 'В процессе' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                <option value="Закончен">${tr('status.done')} ✔️</option>
            </select>
        </div>`;
    }).join('');
}

function renderAllTasks() {
    const container = document.getElementById('allTasksContainer');
    const activeProjects = currentProjects.filter(p => !['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    if (activeProjects.length === 0) {
        container.innerHTML = `<p class="text-slate-500 text-center py-4">${tr('project.no_active')}</p>`;
        return;
    }

    let html = '';
    let hasTasks = false;
    activeProjects.forEach(proj => {
        let pTasks = currentTasks.filter(t => t['Project ID'] === proj.ID);
        if (pTasks.length === 0) return;
        hasTasks = true;
        pTasks = sortTasks(pTasks);

        html += `
        <div class="bg-slate-800 rounded-2xl border border-slate-700/50 shadow-xl overflow-hidden mb-6">
            <div class="bg-slate-900/80 px-4 py-3 border-b border-slate-700/50 flex justify-between items-center">
                <h3 class="text-base font-bold text-white">${esc(proj.Name)}</h3>
                <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${getPriorityColor(proj.Priority)}">${esc(proj.Priority)}</span>
            </div>
            <div class="p-3 space-y-2">`;

        html += pTasks.map(t => {
            const isDone = ['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase());
            const op = isDone ? 'opacity-40 line-through' : '';
            return `
            <div class="p-3 bg-slate-900 rounded-xl border border-slate-700 flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 group">
                <div class="${op} flex-grow min-w-0 break-words">
                    <div class="text-xs font-mono text-slate-500 mb-1">${getDeadlineHTML(t.Deadline, true, isDone)}</div>
                    <div class="text-sm text-slate-200 font-medium">${esc(t.Task)}</div>
                </div>
                <div class="flex items-center gap-3 sm:ml-4">
                    <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-xs bg-slate-800 border border-slate-600 text-slate-300 rounded-lg px-2 py-1 outline-none cursor-pointer">
                        <option value="Не начато" ${t.Status === 'Не начато' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                        <option value="В процессе" ${t.Status === 'В процессе' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                        <option value="Закончен" ${isDone ? 'selected' : ''}>${tr('status.done')} ✔️</option>
                    </select>
                    <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 opacity-0 group-hover:opacity-100 p-1">✏️</button>
                    <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 p-1">🗑️</button>
                </div>
            </div>`;
        }).join('');
        html += `</div></div>`;
    });
    container.innerHTML = hasTasks ? html : `<p class="text-slate-500 text-center py-4">${tr('task.no_active')}</p>`;
}

// Палитра графиков: читается из CSS-токенов активной темы (--chart-* в themes.css), поэтому новая тема
// настраивает графики только своим CSS-блоком, без правок JS.
function chartTheme() {
    const css = getComputedStyle(document.body);
    const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
    const stageDefaults = ['#475569', '#6366f1', '#f59e0b', '#06b6d4', '#a855f7', '#10b981'];
    return {
        font: v('--chart-font', 'sans-serif'),
        text: v('--chart-text', '#cbd5e1'),
        muted: v('--chart-muted', '#94a3b8'),
        grid: v('--chart-grid', '#334155'),
        stages: stageDefaults.map((d, i) => v(`--chart-stage-${i + 1}`, d)),
        series: [v('--chart-series-1', '#6366f1'), v('--chart-series-2', '#10b981')],
        border: v('--chart-border', 'transparent'),
        borderWidth: parseFloat(v('--chart-border-width', '0')) || 0,
        radius: parseFloat(v('--chart-radius', '6')) || 0,
    };
}

function renderAnalytics() {
    const doneProjs = currentProjects.filter(p => ['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    const activeProjs = currentProjects.filter(p => !['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    const doneTasks = currentTasks.filter(t => ['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase()));
    const rate = currentTasks.length > 0 ? Math.round((doneTasks.length / currentTasks.length) * 100) : 0;

    document.getElementById('statDoneProjects').innerText = doneProjs.length;
    document.getElementById('statDoneTasks').innerText = doneTasks.length;
    document.getElementById('statEarnedTokens').innerText = `+${earnedTokensTotal}`;
    document.getElementById('statTaskRate').innerText = `${rate}%`;

    const stages = ['—', 'Печать', 'Шлифовка', 'Сборка', 'Грунт', 'Покрас'];
    const stageCounts = stages.map(st => activeProjs.filter(p => (p.Stage || '—') === st).length);

    const ct = chartTheme();
    Chart.defaults.font.family = ct.font;
    Chart.defaults.color = ct.text;

    const ctxStage = document.getElementById('stageChart').getContext('2d');
    if (stageChartInstance) stageChartInstance.destroy();
    stageChartInstance = new Chart(ctxStage, {
        type: 'bar',
        data: {
            labels: ['stage.none', 'stage.printing', 'stage.sanding', 'stage.assembly', 'stage.priming', 'stage.painting'].map(s => tr(s)),
            datasets: [{
                label: tr('analytics.active_projects'),
                data: stageCounts,
                backgroundColor: ct.stages,
                borderColor: ct.border,
                borderWidth: ct.borderWidth,
                borderRadius: ct.radius
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { stepSize: 1, color: ct.muted }, grid: { color: ct.grid } },
                x: { ticks: { color: ct.text }, grid: { display: false } }
            }
        }
    });

    const months = [];
    const now = new Date();
    for (let i = 5; i >= 0; i--) {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
        const label = d.toLocaleDateString(window.APP_LANG || 'ru-RU', { month: 'short', year: '2-digit' });
        months.push({ ym, label, tasks: 0, projs: 0 });
    }

    doneTasks.forEach(t => {
        if (t.CompletedAt) {
            const ym = t.CompletedAt.slice(0, 7);
            const m = months.find(x => x.ym === ym);
            if (m) m.tasks++;
        }
    });
    doneProjs.forEach(p => {
        if (p.CompletedAt) {
            const ym = p.CompletedAt.slice(0, 7);
            const m = months.find(x => x.ym === ym);
            if (m) m.projs++;
        }
    });

    const ctxVel = document.getElementById('velocityChart').getContext('2d');
    if (velocityChartInstance) velocityChartInstance.destroy();
    velocityChartInstance = new Chart(ctxVel, {
        type: 'bar',
        data: {
            labels: months.map(m => m.label),
            datasets: [
                {
                    label: tr('stats.tasks_closed'),
                    data: months.map(m => m.tasks),
                    backgroundColor: ct.series[0],
                    borderColor: ct.border,
                    borderWidth: ct.borderWidth,
                    borderRadius: ct.radius
                },
                {
                    label: tr('stats.projects_completed'),
                    data: months.map(m => m.projs),
                    backgroundColor: ct.series[1],
                    borderColor: ct.border,
                    borderWidth: ct.borderWidth,
                    borderRadius: ct.radius
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: ct.text } } },
            scales: {
                y: { beginAtZero: true, ticks: { stepSize: 1, color: ct.muted }, grid: { color: ct.grid } },
                x: { ticks: { color: ct.text }, grid: { display: false } }
            }
        }
    });

    renderHall();
}

// Зал славы: список завершённых проектов с поиском (отдельно от графиков, чтобы набор текста их не перерисовывал)
function renderHall() {
    const doneProjs = currentProjects.filter(p => ['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    const hallSearching = searchQuery.hall.trim() !== '';
    const hallProjs = doneProjs.filter(p => projectMatches(p, searchQuery.hall));
    document.getElementById('archiveCount').innerText = hallSearching ? `${hallProjs.length} / ${doneProjs.length}` : doneProjs.length;
    const hallContainer = document.getElementById('hallOfFameContainer');
    if (doneProjs.length === 0 || (hallSearching && hallProjs.length === 0)) {
        hallContainer.innerHTML = `<div class="col-span-full text-center py-8 text-slate-500 bg-slate-800/40 rounded-2xl border border-slate-800">${tr(doneProjs.length === 0 ? 'hall.empty' : 'search.nothing')}</div>`;
        return;
    }

    hallContainer.innerHTML = hallProjs.map(p => {
        const pTasks = currentTasks.filter(t => t['Project ID'] === p.ID);
        const notesHtml = p.PaintNotes && p.PaintNotes.trim()
            ? `<div class="mt-3 p-2.5 bg-slate-900/90 border border-amber-500/20 rounded-lg text-xs text-slate-300 font-mono whitespace-pre-wrap"><span class="text-amber-400 font-bold block mb-1">🎨 ${tr('recipe.title')}:</span>${esc(p.PaintNotes)}</div>`
            : `<div class="mt-3 text-xs text-slate-500 italic">${tr('recipe.empty')}</div>`;

        return `
        <div class="bg-slate-800/90 rounded-2xl p-5 border border-emerald-500/30 shadow-lg flex flex-col justify-between">
            <div>
                <div class="flex justify-between items-start gap-2 mb-2">
                    <h3 class="text-base font-bold text-white">${esc(p.Name)}</h3>
                    <span class="text-xs font-bold text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2.5 py-0.5 rounded-full whitespace-nowrap">🏆 +${p['Token Reward'] || 0} 💰</span>
                </div>
                <div class="text-xs text-slate-400 flex items-center gap-3 mb-2">
                    <span>✅ ${tr('stats.tasks')}: ${pTasks.length}</span>
                    ${p.CompletedAt ? `<span>📅 ${tr('project.finish')}: ${formatIsoDate(p.CompletedAt)}</span>` : ''}
                </div>
                ${notesHtml}
            </div>
            <div class="mt-4 pt-3 border-t border-slate-700/50 flex justify-between items-center">
                <button onclick="openProjectDetails('${p.ID}')" class="text-xs text-indigo-400 hover:text-indigo-300">${tr('project.open_card')}</button>
                <button onclick="updateProjectStatus('${p.ID}', 'В работе')" class="text-xs px-2.5 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors">↩️ ${tr('project.reopen')}</button>
            </div>
        </div>`;
    }).join('');
}

function openProjectDetails(projectId) {
    currentOpenProjectId = projectId;
    const project = currentProjects.find(p => p.ID === projectId);
    if (!project) return;
    document.getElementById('pdTitle').innerText = project.Name;
    document.getElementById('pdPaintNotes').value = project.PaintNotes || '';
    document.getElementById('pdPhoto').innerHTML = projectPhotoInner(project, false);
    document.getElementById('pdPhotoActions').classList.toggle('hidden', !project.Photo);
    renderProjectRefs(project);
    renderProjectProgress(project);

    let pTasks = currentTasks.filter(t => t['Project ID'] === projectId);
    const container = document.getElementById('pdTasks');
    
    if (pTasks.length === 0) {
        container.innerHTML = `<p class="text-sm text-slate-500 text-center py-4">${tr('project.no_tasks_hint')}</p>`;
    } else {
        pTasks = sortTasks(pTasks);
        container.innerHTML = pTasks.map(t => {
            const isDone = ['завершен', 'completed', 'закончен'].includes(String(t.Status).toLowerCase());
            const op = isDone ? 'opacity-40 line-through' : '';
            return `
            <div class="p-3 bg-slate-900 rounded-xl border border-slate-700 flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 group mb-2">
                <div class="${op} flex-grow min-w-0 break-words">
                    <div class="text-xs font-mono text-slate-500 mb-1">${getDeadlineHTML(t.Deadline, true, isDone)}</div>
                    <div class="text-sm text-slate-200 font-medium">${esc(t.Task)}</div>
                </div>
                <div class="flex gap-2 sm:ml-3 items-center">
                    <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-xs bg-slate-800 border border-slate-600 text-slate-300 rounded-lg px-2 py-1 outline-none cursor-pointer">
                        <option value="Не начато" ${t.Status === 'Не начато' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                        <option value="В процессе" ${t.Status === 'В процессе' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                        <option value="Закончен" ${isDone ? 'selected' : ''}>${tr('status.done')} ✔️</option>
                    </select>
                    <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 opacity-0 group-hover:opacity-100 p-1">✏️</button>
                    <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 p-1">🗑️</button>
                </div>
            </div>`;
        }).join('');
    }
    openModal('projectDetailsModal');
}

async function savePaintNotes(silent = false) {
    if (!currentOpenProjectId) return;
    const notes = document.getElementById('pdPaintNotes').value;
    const proj = currentProjects.find(p => p.ID === currentOpenProjectId);
    if (proj && proj.PaintNotes === notes && silent) return;
    if (proj) proj.PaintNotes = notes;

    await fetch(`/api/projects/${currentOpenProjectId}/notes`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ paint_notes: notes })
    });
    renderProjects();
    if (!silent) showToast(tr('toast.recipe_saved'), "success");
}

// Шаблон пайплайна: шаг 1 — выбор модели, шаг 2 — сроки этапов (можно пропустить этап или распределить сроки равномерно).
let templateKind = null;
let templateNames = null;        // {kind: [названия этапов]} с сервера, кэш на время страницы

function showTemplateStep(step) {
    document.getElementById('templateStepKind').classList.toggle('hidden', step !== 'kind');
    document.getElementById('templateStepDates').classList.toggle('hidden', step !== 'dates');
}

function openTemplateModal() {
    if (!currentOpenProjectId) return;
    showTemplateStep('kind');
    openModal('templateModal');
}

function backToTemplateKind() { showTemplateStep('kind'); }

function dateToIso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function chooseTemplate(kind) {
    const project = currentProjects.find(p => p.ID === currentOpenProjectId);
    if (!project) return;
    if (!templateNames) {
        try { templateNames = await (await fetch('/api/pipeline-templates')).json(); }
        catch (e) { return showToast(tr('err.save_failed')); }
    }
    const names = templateNames[kind];
    if (!names) return showToast(tr('err.unknown_template'));
    templateKind = kind;
    const est = project.Estimate || '';
    document.getElementById('templateProjectDeadline').textContent =
        `${tr('template.project_deadline')}: ${est ? formatIsoDate(est) : tr('deadline.none')}`;
    document.getElementById('templateSpreadBtn').classList.toggle('hidden', !est);
    document.getElementById('templateStepsList').innerHTML = names.map((name, i) => `
        <div class="flex items-center gap-2 p-2 bg-slate-900 rounded-lg border border-slate-700" data-step="${i}">
            <input type="checkbox" checked class="tpl-on w-4 h-4 accent-indigo-500 cursor-pointer shrink-0">
            <span class="flex-1 min-w-0 text-sm text-slate-200 truncate" title="${esc(name)}">${esc(name)}</span>
            <input type="date" value="${esc(est)}" class="tpl-date bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white outline-none [color-scheme:dark]">
        </div>`).join('');
    showTemplateStep('dates');
}

// Равномерно от сегодня до срока проекта по включённым этапам; срок в прошлом — всем срок проекта
function spreadTemplateDates() {
    const project = currentProjects.find(p => p.ID === currentOpenProjectId);
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec((project && project.Estimate) || '');
    if (!m) return;
    const end = new Date(+m[1], +m[2] - 1, +m[3]);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const rows = [...document.querySelectorAll('#templateStepsList [data-step]')]
        .filter(r => r.querySelector('.tpl-on').checked);
    const span = Math.max(0, Math.round((end - today) / 86400000));
    rows.forEach((row, i) => {
        const d = new Date(today);
        d.setDate(d.getDate() + (span ? Math.round(span * (i + 1) / rows.length) : 0));
        row.querySelector('.tpl-date').value = dateToIso(span ? d : end);
    });
}

async function applyTemplateToProject() {
    if (!currentOpenProjectId || !templateKind) return;
    const steps = [...document.querySelectorAll('#templateStepsList [data-step]')]
        .filter(r => r.querySelector('.tpl-on').checked)
        .map(r => ({ index: +r.dataset.step, deadline: r.querySelector('.tpl-date').value || null }));
    if (!steps.length) return showToast(tr('template.none_selected'));
    const res = await fetch(`/api/projects/${currentOpenProjectId}/template`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ kind: templateKind, steps })
    });
    const data = await res.json().catch(() => ({ status: 'error' }));
    if (data.status !== 'ok') return showToast(data.message || tr('err.save_failed'));
    closeModal('templateModal');
    showToast(tr('toast.pipeline_added'), "success");
    loadData();
}

function updateProjectStatus(id, status) {
    const p = currentProjects.find(x => x.ID === id);
    if (p) p.Status = status;
    if (['закончен', 'завершено', 'completed'].includes(String(status).toLowerCase())) {
        currentTasks.forEach(t => { if(t['Project ID'] === id) t.Status = 'Закончен'; });
        showToast(tr('toast.project_to_hall'), "success");
    }
    renderProjects(); renderTasks(); renderAllTasks();
    fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'update_project_status', id: id, status: status }) }).then(() => loadData());
}

function updateProjectStage(id, stage) {
    const p = currentProjects.find(x => x.ID === id);
    if(p) p.Stage = stage;
    renderProjects();
    fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'update_project_stage', id: id, stage: stage }) });
}

function updateTaskStatus(id, status) {
    const t = currentTasks.find(x => x.ID === id);
    if(t) t.Status = status;
    renderProjects(); renderTasks(); renderAllTasks();
    if (currentOpenProjectId) openProjectDetails(currentOpenProjectId);
    fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'update_task_status', id: id, status: status }) }).then(() => loadData());
}

function updateProjectSelect() {
    const select = document.getElementById('tProjectId');
    const activeProjects = currentProjects.filter(p => !['завершен', 'completed', 'закончен'].includes(String(p.Status).toLowerCase()));
    select.innerHTML = activeProjects.map(p => `<option value="${p.ID}">${esc(p.Name)}</option>`).join('');
}

async function createProject() {
    const body = { name: document.getElementById('pName').value.trim(), priority: document.getElementById('pPriority').value, estimate: document.getElementById('pEstimate').value || null, token_reward: parseFloat(document.getElementById('pToken').value) || 0 };
    if(!body.name) return showToast(tr('project.err_name_empty'));
    
    const res = await fetch('/api/projects', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const result = await res.json();
    
    if(result.status === "error") {
        showToast(result.message, "error");
        return;
    }
    closeModal('projectModal');
    showToast(tr('toast.project_created'), "success");
    loadData();
}

function openEditModal(id) {
    const p = currentProjects.find(x => x.ID === id);
    if(!p) return;
    document.getElementById('epId').value = p.ID; document.getElementById('epName').value = p.Name;
    document.getElementById('epPriority').value = p.Priority; document.getElementById('epEstimate').value = p.Estimate || '';
    // Приоритет и дедлайн правятся только в DEV MODE; название — всегда.
    document.getElementById('epDevFields').classList.toggle('hidden', !isDevMode);
    document.getElementById('epTitle').innerText = isDevMode ? tr('project.edit') : tr('project.rename');
    openModal('editProjectModal');
}

async function saveProjectEdit() {
    const id = document.getElementById('epId').value;
    const name = document.getElementById('epName').value.trim();
    if (!name) return showToast(tr('project.err_name_empty'));

    let url, body;
    if (isDevMode) {
        url = `/api/projects/${id}/edit`;
        body = { name: name, priority: document.getElementById('epPriority').value, estimate: document.getElementById('epEstimate').value || null };
    } else {
        url = `/api/projects/${id}/rename`;
        body = { name: name };
    }
    const res = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const result = await res.json();
    if(result.status === "error") return showToast(result.message || tr('err.save_failed'), "error");
    closeModal('editProjectModal');
    showToast(isDevMode ? tr('toast.project_updated') : tr('toast.project_renamed'), "success");
    loadData();
}

function openEditTaskModal(id) {
    const t = currentTasks.find(x => x.ID === id);
    if(!t) return;
    document.getElementById('etId').value = t.ID; document.getElementById('etName').value = t.Task;
    document.getElementById('etDeadline').value = t.Deadline || '';
    openModal('editTaskModal');
}

async function saveTaskEdit() {
    const id = document.getElementById('etId').value;
    const body = { task_name: document.getElementById('etName').value, deadline: document.getElementById('etDeadline').value || null };
    if(!body.task_name) return showToast(tr('task.err_empty'));
    await fetch(`/api/tasks/${id}/edit`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    closeModal('editTaskModal');
    showToast(tr('toast.task_updated'), "success");
    loadData();
}

async function createTask() {
    const projectId = document.getElementById('tProjectId').value;
    const taskName = document.getElementById('tName').value;
    let deadline = document.getElementById('tDeadline').value;
    if(!taskName) return showToast(tr('task.err_empty'));
    const proj = currentProjects.find(p => p.ID === projectId);
    if (!deadline) {
        if (proj && proj.Status !== 'В планах') return showToast(tr('project.err_deadline_required'));
        if (proj && proj.Estimate) deadline = proj.Estimate;
    }
    const body = { project_id: projectId, task_name: taskName, deadline: deadline || null };
    await fetch('/api/tasks', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    closeModal('taskModal');
    showToast(tr('toast.task_added'), "success");
    loadData();
}

function openTaskModalForProject() {
    if (!currentOpenProjectId) return;
    document.getElementById('tProjectId').value = currentOpenProjectId;
    openModal('taskModal');
}

async function deleteProject(id) {
    const p = currentProjects.find(x => x.ID === id);
    const hint = deleteKeepsBalance
        ? tr('confirm.balance_unchanged')
        : tr('confirm.cost_refunded');
    const ok = await confirmDialog({
        title: tr('project.delete_title'),
        message: `${p ? tr('confirm.delete_project_named', { name: p.Name }) : tr('confirm.delete_project_text')}\n${hint}`
    });
    if (!ok) return;
    await fetch(`/api/projects/${id}`, { method: 'DELETE' });
    loadData();
}
async function deleteTask(id) {
    if (!(await confirmDialog({ title: tr('task.delete_title'), message: tr('confirm.delete_task_text') }))) return;
    await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
    loadData();
}

function openModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { 
    if (id === 'projectDetailsModal' && typeof standaloneCardId !== 'undefined' && standaloneCardId) { closeStandaloneWindow(); return; }
    document.getElementById(id).classList.add('hidden'); 
    if (id === 'projectDetailsModal') currentOpenProjectId = null; 
    if (id === 'taskModal') { document.getElementById('tName').value = ''; document.getElementById('tDeadline').value = ''; }
    if (id === 'editTaskModal') { document.getElementById('etName').value = ''; document.getElementById('etDeadline').value = ''; }
    if (id === 'projectModal') { document.getElementById('pName').value = ''; document.getElementById('pEstimate').value = ''; document.getElementById('pToken').value = ''; }
    if (id === 'editProjectModal') { document.getElementById('epName').value = ''; document.getElementById('epEstimate').value = ''; }
}

fillTimeSelects();

// Окно-карточка: /?card=<id> показывает только карточку проекта
const standaloneCardId = new URLSearchParams(location.search).get('card');
if (standaloneCardId) initStandaloneCard(standaloneCardId);
else loadData();

// Несколько окон: при возврате в окно подтягиваем свежие данные
let lastFocusRefresh = 0;
window.addEventListener('focus', () => {
    const now = Date.now();
    if (now - lastFocusRefresh > 3000) { lastFocusRefresh = now; loadData(); }
});

// ---------- Переключатель скинов интерфейса ----------
// Реестр тем. Новая тема = строка здесь + CSS-блок body.<id> в themes.css (см. THEMES_GUIDE.md).
// id — имя класса на <body>; 'default' — стандартный вид без класса.
const THEMES = [
    { id: 'default', label: () => `💿 ${tr('theme.default')}` },
    { id: 'theme-cyberpunk', label: () => '⚡ Cyberpunk' },
    { id: 'theme-warhammer', label: () => '💀 Warhammer 40k' },
    { id: 'theme-scp', label: () => '☢️ SCP Dossier' },
    { id: 'theme-workbench', label: () => '🛠️ Workbench' },
    { id: 'theme-synthwave', label: () => '🌆 Synthwave' },
    { id: 'theme-ff7', label: () => '💠 JRPG (FFVII)' },
    { id: 'theme-tamiya', label: () => '📐 Model Manual' },
    { id: 'theme-pcb', label: () => '📟 Hardware PCB' },
];

function initThemeSwitcher() {
    let savedTheme = 'default';
    try { savedTheme = localStorage.getItem('ph_skin') || 'default'; } catch (e) { /* ignore */ }
    applyThemeSkin(savedTheme);

    const nav = document.querySelector('nav');
    if (!nav) return;

    const select = document.createElement('select');
    select.id = 'skinSelector';
    select.title = tr('theme.choose');
    select.className = 'text-xs bg-slate-800 border border-slate-600 text-slate-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer ml-2';
    select.innerHTML = THEMES.map(t => `<option value="${t.id}">${t.label()}</option>`).join('');
    select.value = THEMES.some(t => t.id === savedTheme) ? savedTheme : 'default';
    select.onchange = (e) => applyThemeSkin(e.target.value);

    // Вставляем переключатель рядом с правым блоком кнопок в шапке
    const rightGroup = nav.lastElementChild || nav;
    rightGroup.prepend(select);
}

function applyThemeSkin(skin) {
    if (!THEMES.some(t => t.id === skin)) skin = 'default';      // удалённая или неизвестная тема -> стандартная
    document.body.classList.remove(...THEMES.map(t => t.id).filter(id => id !== 'default'));
    if (skin !== 'default') document.body.classList.add(skin);
    try { localStorage.setItem('ph_skin', skin); } catch (e) { /* без localStorage тема просто не запомнится */ }
    // графики уже нарисованы старыми цветами — перерисовываем под новую тему
    if (stageChartInstance || velocityChartInstance) renderAnalytics();
}

initThemeSwitcher();