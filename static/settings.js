/* Settings window: used space, password login, Telegram linking and proxy, saving settings. */

// ---------- Used space: app, database, photos, logs ----------
// keys are listed explicitly so that tools/check_locales.py can see them in the code
const STORAGE_LABELS = {
    app: 'storage.app', db: 'storage.db', photos: 'storage.photos', photos_covers: 'storage.photos_covers',
    photos_refs: 'storage.photos_refs', photos_progress: 'storage.photos_progress', logs: 'storage.logs',
    backups: 'storage.backups', orphans: 'storage.orphans', venv: 'storage.venv', git: 'storage.git',
};
function formatBytes(n) {
    const units = ['storage.unit_b', 'storage.unit_kb', 'storage.unit_mb', 'storage.unit_gb', 'storage.unit_tb'];
    let i = 0;
    n = Number(n) || 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    const num = i === 0 ? String(n) : n.toLocaleString(window.APP_LANG || 'ru-RU', { maximumFractionDigits: n < 10 ? 1 : 0 });
    return `${num} ${tr(units[i])}`;
}

function storageRow(item, total, child) {
    const pct = total > 0 ? Math.round(item.bytes * 100 / total) : 0;
    const files = tr('storage.files', { n: item.files });
    return `<div class="flex items-center justify-between gap-3 ${child ? 'pl-4 text-slate-500' : 'text-slate-300'}">
        <span class="min-w-0 truncate">${esc(tr(STORAGE_LABELS[item.key] || item.key))}</span>
        <span class="shrink-0 tabular-nums text-right">
            <span class="${child ? '' : 'text-white font-semibold'}">${formatBytes(item.bytes)}</span>
            <span class="text-slate-500"> · ${esc(files)}${child ? '' : ` · ${pct}%`}</span>
        </span>
    </div>`;
}

let storageLoading = false;
async function loadStorage() {
    if (storageLoading) return;
    storageLoading = true;
    const box = document.getElementById('storageList');
    const btn = document.getElementById('storageRefreshBtn');
    btn.disabled = true;
    box.innerHTML = `<div class="text-slate-500">${esc(tr('storage.loading'))}</div>`;
    try {
        const data = await (await fetch('/api/storage')).json();
        if (data.status !== 'ok') throw new Error(data.message || 'error');
        const rows = data.items.map(it =>
            storageRow(it, data.total, false) + (it.children || []).map(ch => storageRow(ch, data.total, true)).join('')
        ).join('');
        box.innerHTML = rows + `<div class="flex items-center justify-between gap-3 pt-1 mt-1 border-t border-slate-800 text-white font-semibold">
            <span>${esc(tr('storage.total'))}</span><span class="tabular-nums">${formatBytes(data.total)}</span></div>
            <div class="text-[11px] text-slate-500 break-all pt-1">${esc(tr('storage.folder', { path: data.path }))}</div>`;
    } catch (e) {
        box.innerHTML = `<div class="text-red-400">${esc(tr('storage.error'))}</div>`;
    } finally {
        storageLoading = false;
        btn.disabled = false;
    }
}

// ---------- Access: password login (off by default) ----------
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
    // connection type: over http an intercepted digest can be replayed (see README, HTTPS)
    const secure = location.protocol === 'https:';
    const conn = document.getElementById('authConnHint');
    conn.textContent = secure ? tr('auth.conn_https') : tr('auth.conn_http');
    conn.className = 'text-xs ' + (secure ? 'text-emerald-300' : 'text-slate-400');
}

async function refreshAuthStatus() {
    try {
        const res = await fetch('/api/auth/status');
        renderAuthState(await res.json());
    } catch (e) { /* no network: leave as is */ }
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
        if (r.must_change) {                      // default password: change it on the login page first
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

// ---------- Telegram: linking the bot to a chat ----------
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
    } catch (e) { /* no network: retried on the next poll */ }
}

function startTelegramPolling() {
    stopTelegramPolling();
    refreshTelegramState();
    tgPollTimer = setInterval(() => {
        const modalHidden = document.getElementById('settingsModal').classList.contains('hidden');
        const tabHidden = document.getElementById('cfgTab-telegram').classList.contains('hidden');
        if (modalHidden || tabHidden) { stopTelegramPolling(); return; }
        refreshTelegramState();   // notices a link made from Telegram
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
    const r = await postJson('/api/telegram/unlink');
    if (r.status === 'error') return showToast(r.message || tr('err.save_failed'), 'error');
    renderTelegramState(r);
    showToast(tr('toast.chat_unlinked'), 'success');
}

// ---------- Proxy for Telegram (optional) ----------
// The proxy password never reaches the browser: a masked address is shown; an empty field on save means "keep as is".
let tgProxyState = null;

function syncProxyForm() {
    const on = document.getElementById('tgProxyEnabled').checked;
    document.getElementById('tgProxyFields').classList.toggle('opacity-50', !on);
    document.getElementById('tgProxyUrl').disabled = !on;
}

function renderTelegramProxy(st) {
    tgProxyState = st;
    document.getElementById('tgProxyEnabled').checked = !!st.enabled;
    document.getElementById('tgProxyUrl').value = '';
    let current = st.has_url ? tr('tg.proxy_current', { url: st.display }) : tr('tg.proxy_none');
    if (st.source === 'env' && st.has_url) current += ' ' + tr('tg.proxy_from_env');
    document.getElementById('tgProxyCurrent').textContent = current;
    document.getElementById('tgProxyResult').classList.add('hidden');
    syncProxyForm();
}

async function loadTelegramProxy() {
    try { renderTelegramProxy(await (await fetch('/api/telegram/proxy')).json()); } catch (e) { /* no network: leave as is */ }
}

function proxyFormBody() {
    return { enabled: document.getElementById('tgProxyEnabled').checked, url: document.getElementById('tgProxyUrl').value.trim() };
}

function showProxyResult(ok, message) {
    const box = document.getElementById('tgProxyResult');
    box.textContent = message;
    box.className = 'text-xs rounded-lg px-2.5 py-2 break-words border ' +
        (ok ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30' : 'text-red-300 bg-red-500/10 border-red-500/30');
}

async function checkTelegramProxy() {
    const btn = document.getElementById('tgProxyCheckBtn');
    btn.disabled = true;
    btn.textContent = tr('tg.proxy_checking');
    try {
        const res = await fetch('/api/telegram/proxy/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(proxyFormBody()) });
        const data = await res.json();
        showProxyResult(!!data.ok, data.message || '');
    } catch (e) {
        showProxyResult(false, tr('settings.err_save'));
    } finally {
        btn.disabled = false;
        btn.textContent = tr('tg.proxy_check');
    }
}

async function saveTelegramProxy() {
    const data = await postJson('/api/telegram/proxy', proxyFormBody());
    if (data.status !== 'ok') return showToast(data.message || tr('settings.err_save'), 'error');
    renderTelegramProxy(data);
    showToast(data.enabled ? tr('tg.proxy_saved_on') : tr('tg.proxy_saved_off'), 'success');
}

async function newTelegramCode() {
    const r = await postJson('/api/telegram/code');
    if (r.status === 'error') return showToast(r.message || tr('err.save_failed'), 'error');
    renderTelegramState(r);
    showToast(tr('tg.new_code_created'), 'success');
}

function openSettingsModal(tab = 'general') {
    const langs = languagesList.length ? languagesList : [{ code: window.APP_LANG || 'ru-RU', name: tr('meta.name') }];
    document.getElementById('cfgLanguage').innerHTML = langs
        .map(l => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join('');
    document.getElementById('cfgLanguage').value = window.APP_LANG || 'ru-RU';
    document.getElementById('cfgDateFormat').value = window.APP_DATE_FORMAT === 'mdy' ? 'mdy' : 'dmy';
    tplLoaded = false;          // templates are reloaded every time the settings open
    tplDirty = false;
    renderVacation(vacationState);
    switchSettingsTab(tab);
    document.getElementById('cfgDevMode').checked = isDevMode;
    document.getElementById('cfgProjectCost').value = projectCost;
    document.getElementById('cfgDeleteKeepsBalance').checked = deleteKeepsBalance;
    document.getElementById('cfgCalendarExport').checked = calendarExport;
    document.getElementById('cfgTgCleanup').value = tgCleanupMode;
    document.getElementById('cfgAskSpentTime').checked = askSpentTime;
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
    if (!(await tplSaveIfDirty())) return;
    const costVal = Number(document.getElementById('cfgProjectCost').value);
    const body = {
        dev_mode: document.getElementById('cfgDevMode').checked,
        project_cost: isNaN(costVal) ? 10 : costVal,
        delete_keeps_balance: document.getElementById('cfgDeleteKeepsBalance').checked,
        calendar_export: document.getElementById('cfgCalendarExport').checked,
        tg_cleanup: document.getElementById('cfgTgCleanup').value,
        ask_spent_time: document.getElementById('cfgAskSpentTime').checked,
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
    const result = await postJson('/api/config', body);
    if (result.status === 'error') return showToast(result.message || tr('settings.err_save'), 'error');
    closeModal('settingsModal');
    if (body.language !== (window.APP_LANG || 'ru-RU') || body.date_format !== (window.APP_DATE_FORMAT || 'dmy')) { location.reload(); return; }   // new language: redraw the whole page
    showToast(tr('toast.settings_saved'), "success");
    loadData();
}

async function triggerTestNotify() {
    if (await postOk('/api/notify/test')) showToast(tr('toast.test_push_sent'), "success");
}

async function triggerNotifyNow() {
    if (await postOk('/api/notify/now')) showToast(tr('toast.notify_now_sent'), "success");
}
