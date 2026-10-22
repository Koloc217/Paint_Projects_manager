// ---------- Шаблоны пайплайна в настройках и отпуск ----------
// Шаблоны хранятся в config.json. У встроенных названия и этапы — ключи перевода: если строку не трогали,
// обратно уходит ключ (и при смене языка текст переведётся), если поправили — уходит новый текст.

const TPL_MAX_TEMPLATES = 20;
const TPL_MAX_STEPS = 30;

let tplState = [];          // [{id, name: {text, raw, orig}, steps: [{text, raw, orig}]}]
let tplDefaults = [];
let tplLoaded = false;
let tplDirty = false;

function tplFromServer(list) {
    return (list || []).map(t => ({
        id: t.id,
        name: { text: t.name, raw: t.raw ? t.raw.name : t.name, orig: t.name },
        steps: t.steps.map((text, i) => ({ text, raw: t.raw ? t.raw.steps[i] : text, orig: text })),
    }));
}

// Что отправить на сервер: нетронутая строка — исходное значение (ключ), изменённая — текст
function tplValue(item) {
    const text = item.text.trim();
    return text === item.orig ? item.raw : text;
}

function tplPayload() {
    return tplState.map(t => ({
        id: t.id,
        name: tplValue(t.name),
        steps: t.steps.filter(s => s.text.trim()).map(tplValue),
    }));
}

async function tplLoad() {
    try {
        const info = await (await fetch('/api/pipeline-templates')).json();
        tplState = tplFromServer(info.templates);
        tplDefaults = info.defaults || [];
        tplLoaded = true;
        tplDirty = false;
        tplRender();
    } catch (e) {
        showToast(tr('err.save_failed'));
    }
}

function tplTouch() { tplDirty = true; }

function tplRender() {
    const box = document.getElementById('tplEditor');
    if (!box) return;
    box.innerHTML = tplState.map((t, ti) => `
        <div class="p-3 bg-slate-900 rounded-xl border border-slate-700 space-y-2">
            <div class="flex items-center gap-2">
                <input value="${esc(t.name.text)}" maxlength="60" placeholder="${esc(tr('settings.templates.name_ph'))}"
                    oninput="tplState[${ti}].name.text = this.value; tplTouch()"
                    class="flex-1 min-w-0 bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-white outline-none focus:border-indigo-500">
                <button type="button" onclick="tplRemoveTemplate(${ti})" class="shrink-0 text-xs px-2 py-1 text-slate-400 hover:text-red-400" title="${esc(tr('settings.templates.delete'))}">${tr('common.delete')}</button>
            </div>
            <div class="space-y-1.5">
                ${t.steps.map((st, si) => `
                <div class="flex items-center gap-1.5">
                    <span class="shrink-0 w-6 text-right text-xs text-slate-500">${si + 1}.</span>
                    <input value="${esc(st.text)}" maxlength="120" placeholder="${esc(tr('settings.templates.step_ph'))}"
                        oninput="tplState[${ti}].steps[${si}].text = this.value; tplTouch()"
                        class="flex-1 min-w-0 bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500">
                    <button type="button" onclick="tplMoveStep(${ti}, ${si}, -1)" ${si === 0 ? 'disabled' : ''} class="shrink-0 w-6 h-6 rounded text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:hover:bg-transparent" title="${esc(tr('settings.templates.up'))}">&#8593;</button>
                    <button type="button" onclick="tplMoveStep(${ti}, ${si}, 1)" ${si === t.steps.length - 1 ? 'disabled' : ''} class="shrink-0 w-6 h-6 rounded text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:hover:bg-transparent" title="${esc(tr('settings.templates.down'))}">&#8595;</button>
                    <button type="button" onclick="tplRemoveStep(${ti}, ${si})" class="shrink-0 w-6 h-6 rounded text-slate-400 hover:text-red-400 hover:bg-slate-700" title="${esc(tr('settings.templates.remove_step'))}">&times;</button>
                </div>`).join('')}
            </div>
            <button type="button" onclick="tplAddStep(${ti})" class="text-xs text-indigo-300 hover:text-indigo-200">+ ${tr('settings.templates.add_step')}</button>
        </div>`).join('');
}

function tplAddStep(ti) {
    const t = tplState[ti];
    if (t.steps.length >= TPL_MAX_STEPS) return showToast(tr('err.template_too_many_steps', { name: t.name.text, n: TPL_MAX_STEPS }));
    t.steps.push({ text: '', raw: '', orig: '' });
    tplTouch();
    tplRender();
    const inputs = document.querySelectorAll('#tplEditor > div')[ti].querySelectorAll('.space-y-1\\.5 input');
    if (inputs.length) inputs[inputs.length - 1].focus();
}

function tplRemoveStep(ti, si) {
    tplState[ti].steps.splice(si, 1);
    tplTouch();
    tplRender();
}

function tplMoveStep(ti, si, delta) {
    const steps = tplState[ti].steps;
    const j = si + delta;
    if (j < 0 || j >= steps.length) return;
    [steps[si], steps[j]] = [steps[j], steps[si]];
    tplTouch();
    tplRender();
}

function tplAddTemplate() {
    if (tplState.length >= TPL_MAX_TEMPLATES) return showToast(tr('err.templates_too_many', { n: TPL_MAX_TEMPLATES }));
    tplState.push({ id: '', name: { text: '', raw: '', orig: '' }, steps: [{ text: '', raw: '', orig: '' }] });
    tplTouch();
    tplRender();
    const blocks = document.querySelectorAll('#tplEditor > div');
    blocks[blocks.length - 1].querySelector('input').focus();
}

async function tplRemoveTemplate(ti) {
    if (tplState.length <= 1) return showToast(tr('err.templates_empty'));
    const name = tplState[ti].name.text || tr('settings.templates.untitled');
    if (!(await confirmDialog({ title: tr('settings.templates.delete'), message: tr('settings.templates.delete_text', { name }) }))) return;
    tplState.splice(ti, 1);
    tplTouch();
    tplRender();
}

async function tplResetDefaults() {
    if (!(await confirmDialog({ title: tr('settings.templates.reset'), message: tr('settings.templates.reset_text'), okText: tr('settings.templates.reset_ok') }))) return;
    tplState = tplFromServer(tplDefaults);
    tplTouch();
    tplRender();
}

// Вызывается из saveSettings: true — можно закрывать окно, false — ошибка (показана)
async function tplSaveIfDirty() {
    if (!tplLoaded || !tplDirty) return true;
    const res = await fetch('/api/pipeline-templates', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ templates: tplPayload() }),
    });
    const data = await res.json().catch(() => ({ status: 'error' }));
    if (data.status !== 'ok') {
        switchSettingsTab('templates');
        showToast(data.message || tr('settings.err_save'), 'error');
        return false;
    }
    tplDirty = false;
    return true;
}

// ----- выбор шаблона в карточке проекта -----
let templateInfo = null;

async function openTemplateModal() {
    if (!currentOpenProjectId) return;
    try {
        templateInfo = await (await fetch(`/api/pipeline-templates?project_id=${encodeURIComponent(currentOpenProjectId)}`)).json();
    } catch (e) { return showToast(tr('err.save_failed')); }
    document.getElementById('templateKindList').innerHTML = (templateInfo.templates || []).map(t => `
        <button type="button" onclick="chooseTemplate('${esc(t.id)}')" class="text-left p-4 bg-slate-900 hover:bg-slate-700 border border-slate-700 hover:border-amber-500/50 rounded-xl transition-colors">
            <div class="font-semibold text-white">${esc(t.name)}</div>
            <div class="text-xs text-slate-400 mt-1 line-clamp-2">${esc(tr('template.steps_count', { n: t.steps.length }))}: ${esc(t.steps.join(', '))}</div>
        </button>`).join('');
    showTemplateStep('kind');
    openModal('templateModal');
}

// ----- отпуск -----
let vacationState = { active: false };

function renderVacation(st) {
    vacationState = st || { active: false };
    const on = !!vacationState.active;
    const until = on ? formatIsoDate(vacationState.until) : '';
    const pill = document.getElementById('navVacation');
    if (pill) {
        pill.classList.toggle('hidden', !on);
        pill.innerText = on ? tr('vacation.until', { date: until }) : '';
    }
    const off = document.getElementById('vacOff');
    if (!off) return;
    off.classList.toggle('hidden', on);
    document.getElementById('vacOn').classList.toggle('hidden', !on);
    document.getElementById('vacOnText').innerText = on ? tr('vacation.active_until', { date: until }) : '';
}

async function startVacation() {
    const days = parseInt(document.getElementById('cfgVacationDays').value, 10);
    if (!(days >= 1 && days <= 30)) return showToast(tr('err.vacation_days', { n: 30 }));
    if (!(await confirmDialog({
        title: tr('vacation.title'), message: tr('vacation.confirm', { n: days }), okText: tr('vacation.start'), danger: false,
    }))) return;
    const res = await fetch('/api/vacation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days }) });
    const data = await res.json().catch(() => ({ status: 'error' }));
    if (data.status !== 'ok') return showToast(data.message || tr('settings.err_save'), 'error');
    renderVacation(data.vacation);
    showToast(tr('vacation.started', { date: formatIsoDate(data.vacation.until), p: data.projects, t: data.tasks }), 'success');
    loadData();
}

async function cancelVacation() {
    if (!(await confirmDialog({
        title: tr('vacation.cancel'), message: tr('vacation.cancel_confirm'), okText: tr('vacation.cancel'), danger: false,
    }))) return;
    const res = await fetch('/api/vacation/cancel', { method: 'POST' });
    const data = await res.json().catch(() => ({ status: 'error' }));
    if (data.status !== 'ok') return showToast(tr('settings.err_save'), 'error');
    renderVacation(data.vacation);
    showToast(tr('vacation.cancelled'), 'success');
}
