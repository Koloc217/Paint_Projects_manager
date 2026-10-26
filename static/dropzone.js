// ---------- Drag and drop and paste (Ctrl+V) of images onto the project photo, paint references and progress ----------
// A drop zone is any element with data-drop="photo|refs|progress". data-pid sets the project;
// without it the zone belongs to the project open in the card (currentOpenProjectId).
// One set of listeners on the document, so zones in re-rendered markup work without re-binding.
// While files are dragged over the window, <body> gets .drop-active (all zones are outlined)
// and the zone under the pointer gets .drop-over.
(function () {
    const REF_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'];
    // Windows without the AV1 extension gives .avif files an empty type: fall back to the extension
    const typeOf = (f) => f.type || (/\.avif$/i.test(f.name || '') ? 'image/avif' : '');
    let overZone = null;

    // A drag that starts inside the page (an image from the card, a reference tile being reordered) is not an
    // upload: Chrome hands a dragged <img> over as a file, and dropping it on a zone used to create a copy.
    // Files dragged in from the computer never fire dragstart in the page, so only they count as uploads.
    let internalDrag = false;
    document.addEventListener('dragstart', () => { internalDrag = true; }, true);
    const endInternal = () => { internalDrag = false; };
    document.addEventListener('dragend', endInternal, true);
    document.addEventListener('drop', () => setTimeout(endInternal, 0), true);
    // safety net: if the dragged element was removed and dragend never reached the document,
    // the next mouse move (mouse events do not fire during a drag) clears the flag
    document.addEventListener('mousemove', () => { if (internalDrag) internalDrag = false; }, true);

    const hasFiles = (e) => !internalDrag && !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    const zoneOf = (e) => (e.target && e.target.closest) ? e.target.closest('[data-drop]') : null;

    function setOver(zone) {
        if (zone === overZone) return;
        if (overZone) overZone.classList.remove('drop-over');
        overZone = zone;
        if (zone) {
            zone.dataset.dropLabel = zone.dataset.drop === 'photo' ? '⬇' : tr('drop.release');
            zone.classList.add('drop-over');
        }
    }

    function clearAll() {
        setOver(null);
        document.body.classList.remove('drop-active');
    }

    document.addEventListener('dragenter', (e) => {
        if (hasFiles(e)) document.body.classList.add('drop-active');
    });

    document.addEventListener('dragover', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();                     // otherwise the browser opens the file instead of dropping it
        document.body.classList.add('drop-active');
        const zone = zoneOf(e);
        e.dataTransfer.dropEffect = zone ? 'copy' : 'none';
        setOver(zone);
    });

    document.addEventListener('dragleave', (e) => {
        // relatedTarget is null when the pointer leaves the window
        if (!e.relatedTarget) clearAll();
    });

    document.addEventListener('dragend', clearAll);

    document.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        const zone = zoneOf(e);
        clearAll();
        if (!zone) return;
        const projectId = zone.dataset.pid || currentOpenProjectId;
        if (!projectId) return;
        handleDrop(zone.dataset.drop, projectId, Array.from(e.dataTransfer.files || []));
    });

    function handleDrop(kind, projectId, files) {
        // an image dragged from another web page usually arrives as a link, not a file
        if (!files.length) return showToast(tr('drop.no_file'));
        const images = files.filter(f => typeOf(f).startsWith('image/'));
        if (!images.length) return showToast(tr('drop.not_image'), 'error');
        if (images.length < files.length) showToast(tr('drop.skipped', { n: files.length - images.length }));

        if (kind === 'photo') return uploadProjectPhoto(projectId, images[0]);

        if (kind === 'refs') {
            const ok = images.filter(f => REF_TYPES.includes(typeOf(f)));
            if (!ok.length) return showToast(tr('drop.ref_types'), 'error');
            const free = refsFree(projectId);
            if (free <= 0) return showToast(tr('ref.limit', { n: MAX_REFS }));
            if (isUploading('pdRefsAdd')) return showToast(tr('drop.busy'));
            return uploadProjectRefs(projectId, ok, free);
        }

        if (kind === 'progress') {
            if (isUploading('pdProgressAdd')) return showToast(tr('drop.busy'));
            return uploadProgressPhotos(projectId, images);
        }
    }

    // ----- paste from the clipboard (Ctrl+V) -----
    // The zone under the mouse pointer decides where the image goes; with no zone under the pointer
    // and a project card open, a small dialog asks. Pasting text works as usual.
    let hoverZone = null;
    document.addEventListener('mouseover', (e) => { hoverZone = zoneOf(e); });

    const visible = (el) => !!el && document.contains(el) && el.offsetParent !== null;
    const cardOpen = () => !!currentOpenProjectId && !document.getElementById('projectDetailsModal').classList.contains('hidden');

    document.addEventListener('paste', async (e) => {
        const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
        const files = items.filter(i => i.kind === 'file' && (i.type || '').startsWith('image/'))
            .map(i => i.getAsFile()).filter(Boolean);
        if (!files.length) return;
        if (isAskOpen() || isConfirmOpen() || isLightboxOpen()) return;

        const zone = visible(hoverZone) ? hoverZone : null;
        if (!zone && !cardOpen()) return showToast(tr('paste.open_card'));
        e.preventDefault();

        if (zone) {
            const projectId = zone.dataset.pid || currentOpenProjectId;
            if (projectId) handleDrop(zone.dataset.drop, projectId, files);
            return;
        }
        const projectId = currentOpenProjectId;
        const preview = URL.createObjectURL(files[0]);
        const kind = await askDialog({
            title: tr('paste.where_title'),
            message: tr('paste.where_text'),
            preview,
            buttons: [
                { value: 'progress', label: tr('paste.to_progress'), primary: true },
                { value: 'refs', label: tr('paste.to_refs') },
                { value: 'photo', label: tr('paste.to_cover') },
            ],
        });
        URL.revokeObjectURL(preview);
        if (kind) handleDrop(kind, projectId, files);
    });

    function isUploading(btnId) {
        const btn = document.getElementById(btnId);
        return !!btn && btn.disabled;
    }
})();
