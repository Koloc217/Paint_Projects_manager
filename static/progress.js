// ---------- Progress (WIP) photos: the block in the project card and the shared "Feed" ----------
const PROGRESS_STAGE_KEYS = {
    '—': 'stage.none', 'Печать': 'stage.printing', 'Шлифовка': 'stage.sanding',
    'Сборка': 'stage.assembly', 'Грунт': 'stage.priming', 'Покрас': 'stage.painting',
};
const PROGRESS_MAX_SIDE = 1600;
const PROGRESS_MAX_FILES = 10;      // per upload

function progressStageLabel(stage) {
    const key = PROGRESS_STAGE_KEYS[stage || '—'];
    return key ? tr(key) : (stage || '');
}

function progressCaption(ph, projectName) {
    const parts = [];
    if (projectName) parts.push(projectName);
    parts.push(formatIsoDate((ph.At || '').slice(0, 10)) + ((ph.At || '').slice(11) ? ' ' + ph.At.slice(11) : ''));
    if (ph.Stage && ph.Stage !== '—') parts.push(progressStageLabel(ph.Stage));
    if (ph.Note) parts.push(ph.Note);
    return parts.join(' · ');
}

// ----- project card -----
function renderProjectProgress(project) {
    const list = project.Progress || [];
    document.getElementById('pdProgressCount').innerText = list.length ? String(list.length) : '';
    const box = document.getElementById('pdProgress');
    if (!list.length) {
        box.innerHTML = `<div class="text-xs text-slate-600 py-2">${tr('progress.empty')}</div>`;
        return;
    }
    // Timeline: oldest on the left, newest on the right; scrolls horizontally and starts at the newest end.
    box.innerHTML = list.map((ph, i) => `
        <div class="shrink-0 w-24">
            <div class="relative group aspect-square rounded-lg overflow-hidden border border-slate-700 bg-slate-950 cursor-pointer hover:border-fuchsia-400 transition-colors" onclick="openProjectProgress('${esc(project.ID)}', ${i})">
                <img src="${esc(ph.Thumb)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover">
                ${ph.Source === 'telegram' ? '<span class="absolute bottom-1 left-1 text-[10px] bg-black/70 text-sky-300 rounded px-1">TG</span>' : ''}
                <button onclick="event.stopPropagation(); editProgressNote('${esc(project.ID)}', ${ph.ID})" class="absolute top-1 left-1 w-5 h-5 rounded-full bg-black/70 text-white/80 hover:bg-blue-600 hover:text-white text-[10px] leading-none opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity" title="${tr('progress.edit_note')}">✎</button>
                <button onclick="event.stopPropagation(); removeProgressPhoto('${esc(project.ID)}', ${ph.ID})" class="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/70 text-white/80 hover:bg-red-600 hover:text-white text-xs leading-none opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity" title="${tr('progress.delete')}">&times;</button>
            </div>
            <div class="text-[10px] text-slate-400 mt-1 leading-tight">${esc(formatIsoDate((ph.At || '').slice(0, 10)))}</div>
            <div class="text-[10px] text-slate-500 leading-tight truncate">${esc(ph.Stage && ph.Stage !== '—' ? progressStageLabel(ph.Stage) : '')}</div>
            ${ph.Note ? `<div onclick="editProgressNote('${esc(project.ID)}', ${ph.ID})" class="text-[10px] text-slate-300 leading-tight line-clamp-2 break-words cursor-text hover:text-white" title="${esc(ph.Note)}">${esc(ph.Note)}</div>` : ''}
        </div>`).join('');
    box.scrollLeft = box.scrollWidth;
}

function openProjectProgress(projectId, index) {
    const p = currentProjects.find(x => x.ID === projectId);
    if (!p || !p.Progress) return;
    openLightbox(p.Progress.map(ph => ({ url: ph.Url, caption: progressCaption(ph, '') })), index);
}

function pickProgressPhotos(projectId) {
    if (!projectId) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.multiple = true;
    input.onchange = () => { if (input.files && input.files.length) uploadProgressPhotos(projectId, Array.from(input.files)); };
    input.click();
}

async function uploadProgressPhotos(projectId, files) {
    if (files.length > PROGRESS_MAX_FILES) {
        showToast(tr('progress.too_many', { n: PROGRESS_MAX_FILES }));
        files = files.slice(0, PROGRESS_MAX_FILES);
    }
    const btn = document.getElementById('pdProgressAdd');
    const noteEl = document.getElementById('pdProgressNote');
    const note = noteEl ? noteEl.value.trim() : '';
    btn.disabled = true;
    btn.innerText = tr('common.uploading');
    let ok = 0;
    for (const file of files) {
        try {
            const [image, thumb] = await Promise.all([
                fileToJpegDataUrl(file, PROGRESS_MAX_SIDE, 0.88), fileToJpegDataUrl(file, 400, 0.8)]);
            const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/progress`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image, thumb, note }),
            });
            const result = await res.json();
            if (result.status === 'error') throw new Error(result.message || tr('progress.err_save'));
            ok++;
        } catch (err) {
            showToast(err.message || tr('progress.err_save'), 'error');
        }
    }
    btn.disabled = false;
    btn.innerText = '+ ' + tr('progress.add_btn');
    if (ok) {
        if (noteEl) noteEl.value = '';
        showToast(ok === 1 ? tr('progress.added') : tr('progress.added_many', { n: ok }), 'success');
    }
    loadData();
}

const PROGRESS_NOTE_MAX = 300;     // MAX_NOTE_LEN in progress.py

async function editProgressNote(projectId, photoId) {
    const p = currentProjects.find(x => x.ID === projectId);
    const ph = p && (p.Progress || []).find(x => x.ID === photoId);
    if (!ph) return;
    const answer = await askDialog({
        title: tr('progress.edit_note'),
        message: progressCaption({ ...ph, Note: '' }, ''),
        preview: ph.Thumb,
        input: { value: ph.Note || '', placeholder: tr('progress.note_ph'), maxLength: PROGRESS_NOTE_MAX },
        buttons: [{ value: 'save', label: tr('common.save'), primary: true }],
    });
    if (!answer) return;
    const note = answer.text.trim();
    if (note === (ph.Note || '')) return;
    try {
        const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/progress/${photoId}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }),
        });
        const result = await res.json();
        if (result.status === 'error') throw new Error(result.message);
        showToast(tr('progress.note_saved'), 'success');
    } catch (err) {
        showToast(err.message || tr('progress.err_save'), 'error');
    }
    loadData();
}

async function removeProgressPhoto(projectId, photoId) {
    if (!(await confirmDialog({ title: tr('progress.delete_title'), message: tr('progress.delete_text') }))) return;
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/progress/${photoId}`, { method: 'DELETE' });
    loadData();
}

// ----- shared feed -----
let feedFilter = '';
let feedItems = [];

function feedAll() {
    const items = [];
    currentProjects.forEach(p => (p.Progress || []).forEach(ph => items.push({ ph, name: p.Name, pid: p.ID })));
    items.sort((a, b) => (b.ph.At || '').localeCompare(a.ph.At || '') || b.ph.ID - a.ph.ID);
    return items;
}

function onFeedFilter(value) {
    feedFilter = value;
    renderFeed();
}

function renderFeed() {
    const view = document.getElementById('view-feed');
    if (!view) return;
    const all = feedAll();
    const sel = document.getElementById('feedProject');
    const withPhotos = currentProjects.filter(p => (p.Progress || []).length);
    if (feedFilter && !withPhotos.some(p => p.ID === feedFilter)) feedFilter = '';
    sel.innerHTML = `<option value="">${tr('feed.all_projects')}</option>` +
        withPhotos.map(p => `<option value="${esc(p.ID)}" ${p.ID === feedFilter ? 'selected' : ''}>${esc(p.Name)} (${p.Progress.length})</option>`).join('');
    feedItems = feedFilter ? all.filter(x => x.pid === feedFilter) : all;
    const box = document.getElementById('feedList');
    if (!feedItems.length) {
        box.innerHTML = `<div class="text-sm text-slate-500 py-8 text-center">${tr('feed.empty')}</div>`;
        return;
    }
    let html = '';
    let day = null;
    feedItems.forEach((x, i) => {
        const d = (x.ph.At || '').slice(0, 10);
        if (d !== day) {
            if (day !== null) html += '</div>';
            day = d;
            html += `<h3 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mt-5 mb-2">${esc(formatIsoDate(d))}</h3><div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2">`;
        }
        html += `
            <div class="relative aspect-square rounded-lg overflow-hidden border border-slate-700 bg-slate-950 cursor-pointer hover:border-fuchsia-400 transition-colors" onclick="openFeedPhoto(${i})">
                <img src="${esc(x.ph.Thumb)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover">
                <div class="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 pt-4 pb-1 text-[10px] leading-tight text-white/90">
                    <div class="truncate">${esc(x.name)}</div>
                    ${x.ph.Stage && x.ph.Stage !== '—' ? `<div class="truncate text-white/60">${esc(progressStageLabel(x.ph.Stage))}</div>` : ''}
                </div>
            </div>`;
    });
    box.innerHTML = html + '</div>';
}

function openFeedPhoto(index) {
    openLightbox(feedItems.map(x => ({ url: x.ph.Url, caption: progressCaption(x.ph, x.name) })), index);
}
