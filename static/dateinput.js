/* Date fields in the format from the settings (Settings → General → date format).
 *
 * The browser shows a native <input type="date"> in the format of its own language (10/31/2026 in English
 * Chrome), and the app setting cannot change that. So next to every such field a text field in the app format
 * is placed (31.10.2026 for dmy, 10/31/2026 for mdy) with a calendar button, and the native field becomes an
 * invisible store of the value:
 * - the rest of the code reads and writes `input.value` in ISO (2026-10-31) as before, nothing to change;
 * - writing `input.value = '...'` from code updates the visible field right away;
 * - the calendar button opens the system date picker of the same native field (showPicker);
 * - fields added later (rows of the pipeline template window) are picked up automatically.
 */
(function () {
    const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    const isMdy = () => window.APP_DATE_FORMAT === 'mdy';
    const t = (key, fallback) => (typeof tr === 'function' ? tr(key) : fallback);

    const style = document.createElement('style');
    style.textContent = `
        .date-field { position: relative; }
        .date-field > .date-native {
            position: absolute; left: 0; bottom: 0; width: 100%; height: 100%;
            opacity: 0; pointer-events: none; border: 0; padding: 0; margin: 0;
        }
        body .date-field > input.date-text.date-text { padding-right: 2rem !important; }
        /* themes draw rivets and patterns as backgrounds on .bg-slate-800 (meant for cards); in an input they cover the digits */
        body .date-field > input.date-text.date-text { background-image: none !important; }
        .date-field > .date-btn {
            position: absolute; top: 0; bottom: 0; right: 0; padding: 0 0.55rem;
            display: flex; align-items: center; background: none; border: 0;
            color: currentColor; opacity: 0.6; cursor: pointer;
        }
        .date-field > .date-btn:hover { opacity: 1; }
    `;
    document.head.appendChild(style);

    function isoToText(iso) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
        if (!m) return '';
        return isMdy() ? `${m[2]}/${m[3]}/${m[1]}` : `${m[3]}.${m[2]}.${m[1]}`;
    }

    // Exactly 8 digits (dd mm yyyy or mm dd yyyy) forming a real date -> ISO, otherwise null
    function textToIso(text) {
        const digits = String(text || '').replace(/\D/g, '');
        if (digits.length !== 8) return null;
        const a = +digits.slice(0, 2), b = +digits.slice(2, 4), y = +digits.slice(4);
        const [d, m] = isMdy() ? [b, a] : [a, b];
        const dt = new Date(y, m - 1, d);
        if (y < 1900 || dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
        return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    }

    // Separators are inserted automatically: 3110 -> 31.10, 31102026 -> 31.10.2026
    function mask(digits) {
        const sep = isMdy() ? '/' : '.';
        let out = digits.slice(0, 2);
        if (digits.length > 2) out += sep + digits.slice(2, 4);
        if (digits.length > 4) out += sep + digits.slice(4, 8);
        return out;
    }

    function enhance(native) {
        if (native.dataset.dateEnhanced) return;
        native.dataset.dateEnhanced = '1';

        const wrap = document.createElement('span');
        wrap.className = 'date-field ' + (native.classList.contains('w-full') ? 'block w-full' : 'inline-block shrink-0');

        const text = document.createElement('input');
        text.type = 'text';
        text.inputMode = 'numeric';
        text.autocomplete = 'off';
        text.maxLength = 10;
        // Styling classes are copied; marker classes (tpl-date etc.) stay on the native field only
        text.className = native.className.split(/\s+/).filter(c => c && c !== '[color-scheme:dark]' && !c.startsWith('tpl-')).join(' ') + ' date-text';
        text.placeholder = isMdy() ? t('date.placeholder_mdy', 'mm/dd/yyyy') : t('date.placeholder_dmy', 'dd.mm.yyyy');
        if (!native.classList.contains('w-full')) text.style.width = '8.5rem';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.tabIndex = -1;
        btn.className = 'date-btn';
        btn.title = t('date.pick', 'Pick a date');
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>';

        native.parentNode.insertBefore(wrap, native);
        // The native field goes first: querySelector('.class') in the rest of the code finds it, not the visible copy
        wrap.append(native, text, btn);
        native.classList.add('date-native');
        native.tabIndex = -1;
        native.setAttribute('aria-hidden', 'true');

        const sync = () => { text.value = isoToText(proto.get.call(native)); };
        Object.defineProperty(native, 'value', {
            configurable: true,
            get() { return proto.get.call(this); },
            set(v) { proto.set.call(this, v); sync(); },
        });
        native.addEventListener('change', sync);
        native.addEventListener('input', sync);
        sync();

        text.addEventListener('input', (e) => {
            const digits = text.value.replace(/\D/g, '').slice(0, 8);
            // The mask is applied only while typing: otherwise Backspace over a separator would delete nothing
            if (!e.inputType || e.inputType.startsWith('insert')) {
                const masked = mask(digits);
                if (text.value !== masked) text.value = masked;
            }
            if (!digits) { proto.set.call(native, ''); return; }
            const iso = textToIso(digits);
            if (iso) proto.set.call(native, iso);
        });

        // Leaving the field with an incomplete or impossible date: restore the last valid value
        text.addEventListener('blur', () => {
            const digits = text.value.replace(/\D/g, '');
            if (!digits) { proto.set.call(native, ''); text.value = ''; return; }
            const iso = textToIso(digits);
            if (iso) { proto.set.call(native, iso); sync(); return; }
            if (typeof showToast === 'function') showToast(t('date.invalid', 'Invalid date'));
            sync();
        });

        btn.addEventListener('click', () => {
            try {
                if (typeof native.showPicker === 'function') { native.showPicker(); return; }
            } catch (e) { /* old browser: fallback below */ }
            native.style.pointerEvents = 'auto';
            native.focus();
            native.click();
            native.style.pointerEvents = '';
        });
    }

    function enhanceAll(root) {
        if (root.matches && root.matches('input[type="date"]')) enhance(root);
        if (root.querySelectorAll) root.querySelectorAll('input[type="date"]').forEach(enhance);
    }

    enhanceAll(document);
    new MutationObserver(records => {
        for (const r of records) r.addedNodes.forEach(n => { if (n.nodeType === 1) enhanceAll(n); });
    }).observe(document.body, { childList: true, subtree: true });
})();
