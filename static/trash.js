// ---------- Project trash ----------
let trashItems = [];
let trashDays = 30;

function renderTrashButton() {
    const btn = document.getElementById('trashBtn');
    if (!btn) return;
    btn.classList.toggle('hidden', trashItems.length === 0);
    document.getElementById('trashCount').innerText = String(trashItems.length);
    if (!document.getElementById('trashModal').classList.contains('hidden')) renderTrash();
}

function openTrash() {
    renderTrash();
    openModal('trashModal');
}

function renderTrash() {
    const box = document.getElementById('trashList');
    document.getElementById('trashEmptyAll').classList.toggle('hidden', trashItems.length === 0);
    document.getElementById('trashHint').innerText = tr('trash.hint', { n: trashDays });
    if (!trashItems.length) {
        box.innerHTML = `<div class="text-sm text-slate-500 py-6 text-center">${tr('trash.empty')}</div>`;
        return;
    }
    box.innerHTML = trashItems.map(t => {
        const status = STATUS_TR[t.Status] ? tr(STATUS_TR[t.Status]) : t.Status;
        return `
        <div class="flex items-center justify-between gap-3 bg-slate-900/70 border border-slate-700 rounded-xl p-3">
            <div class="min-w-0">
                <div class="text-sm font-medium text-slate-100 truncate">${esc(t.Name)}</div>
                <div class="text-[11px] text-slate-400">${esc(status)} · ${tr('trash.tasks_n', { n: t.Tasks })} · ${tr('trash.deleted_on', { date: formatIsoDate(t.DeletedAt) })} · <span class="${t.DaysLeft <= 3 ? 'text-amber-400' : ''}">${tr('trash.days_left', { n: t.DaysLeft })}</span></div>
            </div>
            <div class="flex gap-2 shrink-0">
                <button onclick="restoreProject('${esc(t.ID)}')" class="text-xs px-2.5 py-1 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-md">${tr('trash.restore')}</button>
                <button onclick="purgeProject('${esc(t.ID)}')" class="text-xs px-2.5 py-1 bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-500/30 rounded-md">${tr('trash.purge')}</button>
            </div>
        </div>`;
    }).join('');
}

async function restoreProject(id) {
    const res = await (await fetch(`/api/projects/${encodeURIComponent(id)}/restore`, { method: 'POST' })).json();
    if (res.status === 'error') return showToast(res.message, 'error');
    showToast(tr('trash.restored'), 'success');
    loadData();
}

async function purgeProject(id) {
    const t = trashItems.find(x => x.ID === id);
    if (!(await confirmDialog({ title: tr('trash.purge_title'), message: tr('trash.purge_text', { name: t ? t.Name : '' }) }))) return;
    await fetch(`/api/trash/${encodeURIComponent(id)}`, { method: 'DELETE' });
    loadData();
}

async function emptyTrash() {
    if (!(await confirmDialog({ title: tr('trash.empty_title'), message: tr('trash.empty_text', { n: trashItems.length }) }))) return;
    await fetch('/api/trash', { method: 'DELETE' });
    loadData();
}
