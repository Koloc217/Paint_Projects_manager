// ---------- Drag and drop of image files onto the project photo, paint references and progress ----------
// A drop zone is any element with data-drop="photo|refs|progress". data-pid sets the project;
// without it the zone belongs to the project open in the card (currentOpenProjectId).
// One set of listeners on the document, so zones in re-rendered markup work without re-binding.
// While files are dragged over the window, <body> gets .drop-active (all zones are outlined)
// and the zone under the pointer gets .drop-over.
(function () {
    const REF_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
    let overZone = null;

    const hasFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
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
        const images = files.filter(f => (f.type || '').startsWith('image/'));
        if (!images.length) return showToast(tr('drop.not_image'), 'error');
        if (images.length < files.length) showToast(tr('drop.skipped', { n: files.length - images.length }));

        if (kind === 'photo') return uploadProjectPhoto(projectId, images[0]);

        if (kind === 'refs') {
            const ok = images.filter(f => REF_TYPES.includes(f.type));
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

    function isUploading(btnId) {
        const btn = document.getElementById(btnId);
        return !!btn && btn.disabled;
    }
})();
