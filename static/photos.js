/* Project cover, full-screen viewer and paint references. */

// ---------- Project photos ----------
const PHOTO_ICON = `<svg class="__SZ__" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"/></svg>`;

// Phone layout (same breakpoint as mobile.css): covers come as small previews to save traffic and memory.
// On a computer the original cover is shown as before.
const PHONE_MQ = window.matchMedia('(max-width: 639.98px)');
function isPhoneLayout() { return PHONE_MQ.matches; }

function coverSrc(p) {
    return (isPhoneLayout() && p.PhotoThumb) ? p.PhotoThumb : p.Photo;
}

function projectPhotoInner(p, big = true) {
    if (p.Photo) return `<img src="${esc(coverSrc(p))}" alt="" class="w-full h-full object-cover" loading="lazy" decoding="async">`;
    const icon = PHOTO_ICON.replace('__SZ__', big ? 'w-10 h-10' : 'w-7 h-7');
    const plus = big ? 'bottom-3 right-4 text-xl' : 'bottom-1 right-2 text-sm';
    return `<div class="relative w-full h-full flex items-center justify-center text-slate-500">${icon}<span class="absolute ${plus} font-bold leading-none">+</span></div>`;
}

// Project cover photo: resized in the browser to 1400px on the long side, JPEG
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
    input.accept = 'image/*,.avif';   // .avif: Windows without the AV1 extension hides it under image/*
    input.onchange = () => { if (input.files && input.files[0]) uploadProjectPhoto(id, input.files[0]); };
    input.click();
}

async function uploadProjectPhoto(id, file) {
    try {
        // the cover plus a small preview for the phone layout (cards are 80px there)
        const [dataUrl, thumb] = await Promise.all([fileToJpegDataUrl(file), fileToJpegDataUrl(file, 320, 0.8)]);
        const result = await postJson(`/api/projects/${id}/photo`, { image: dataUrl, thumb });
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
    if (await postOk(`/api/projects/${id}/photo`, undefined, 'DELETE')) showToast(tr('toast.photo_removed'), 'success');
    loadData();
}

// ---------- Photo click: no photo -> file picker, a photo -> full screen ----------
function onProjectPhotoClick(id) {
    const p = currentProjects.find(x => x.ID === id);
    if (!p) return;
    if (p.Photo) openLightbox([{ url: p.Photo }], 0);
    else pickProjectPhoto(id);
}

// ---------- Full-screen viewer ----------
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

// ---------- Paint references: originals without recompression, up to 20 ----------
const MAX_REFS = 20;
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
    bindRefsReorder(box);
    box.dataset.pid = project.ID;
    patchList(box, refs.map((r, i) => ({ r, i })), x => x.r.ID, ({ r, i }) => `
        <div draggable="true" data-ref-id="${r.ID}" class="relative group aspect-square rounded-lg overflow-hidden border border-slate-700 bg-slate-950 cursor-pointer hover:border-sky-400 transition-colors" onclick="openProjectRefs('${project.ID}', ${i})" title="${tr('ref.drag_to_sort')}">
            <img src="${esc(r.Thumb)}" alt="" loading="lazy" decoding="async" draggable="false" class="w-full h-full object-cover">
            <button onclick="event.stopPropagation(); removeProjectRef('${project.ID}', ${r.ID})" class="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/70 text-white/80 hover:bg-red-600 hover:text-white text-xs leading-none opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity" title="${tr('ref.delete')}">&times;</button>
        </div>`);
}

// Reordering references by dragging the tiles. The tiles move live; the new order is saved when the drag ends.
// The drag carries no files, so the file drop zones (static/dropzone.js) ignore it.
let refDrag = null;      // {tile, before: [ids at the start]}

function refTileIds(box) {
    return Array.from(box.querySelectorAll('[data-ref-id]')).map(t => Number(t.dataset.refId));
}

function bindRefsReorder(box) {
    if (box.dataset.reorderBound) return;
    box.dataset.reorderBound = '1';
    box.addEventListener('dragstart', (e) => {
        const tile = e.target.closest && e.target.closest('[data-ref-id]');
        if (!tile) return;
        refDrag = { tile, before: refTileIds(box) };
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/x-ref', tile.dataset.refId);       // Firefox starts a drag only with data
        setTimeout(() => tile.classList.add('opacity-40'), 0);
    });
    box.addEventListener('dragover', (e) => {
        if (!refDrag) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const over = e.target.closest && e.target.closest('[data-ref-id]');
        if (!over || over === refDrag.tile) return;
        const r = over.getBoundingClientRect();
        const after = e.clientX > r.left + r.width / 2;
        box.insertBefore(refDrag.tile, after ? over.nextSibling : over);
    });
    box.addEventListener('drop', (e) => { if (refDrag) e.preventDefault(); });
    box.addEventListener('dragend', () => {
        if (!refDrag) return;
        const { tile, before } = refDrag;
        refDrag = null;
        tile.classList.remove('opacity-40');
        const ids = refTileIds(box);
        if (ids.join(',') !== before.join(',')) saveRefsOrder(box.dataset.pid, ids);
    });
}

async function saveRefsOrder(projectId, ids) {
    const result = await postJson(`/api/projects/${encodeURIComponent(projectId)}/refs/order`, { ids });
    if (result.status === 'error') showToast(result.message || tr('ref.err_order'), 'error');
    loadData();      // the lightbox and the indexes in onclick follow the new order
}

function openProjectRefs(projectId, index) {
    const p = currentProjects.find(x => x.ID === projectId);
    if (!p || !p.Refs) return;
    openLightbox(p.Refs.map(r => ({ url: r.Url })), index);
}

// How many more references the project can take (also used by drag and drop, static/dropzone.js)
function refsFree(id) {
    const p = currentProjects.find(x => x.ID === id);
    return MAX_REFS - ((p && p.Refs) ? p.Refs.length : 0);
}

function pickProjectRefs(id) {
    if (!id) return;
    const free = refsFree(id);
    if (free <= 0) return showToast(tr('ref.limit', { n: MAX_REFS }));
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp,image/gif,image/avif,.avif';
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
    await postOk(`/api/projects/${projectId}/refs/${refId}`, undefined, 'DELETE');
    loadData();
}
