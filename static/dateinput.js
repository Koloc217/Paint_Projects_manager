/* Date fields in the format from the settings (Settings → General → date format).
 *
 * The browser shows a native <input type="date"> in the format of its own language (10/31/2026 in English
 * Chrome), and the app setting cannot change that. So next to every such field a text field in the app format
 * is placed (31.10.2026 for dmy, 10/31/2026 for mdy) with a calendar button, and the native field becomes an
 * invisible store of the value:
 * - the rest of the code reads and writes `input.value` in ISO (2026-10-31) as before, nothing to change;
 * - writing `input.value = '...'` from code updates the visible field right away;
 * - the calendar button opens the app's own month grid (the system picker takes the week start from the
 *   browser language, so English Chrome always starts on Sunday): Monday first for dmy, Sunday for mdy,
 *   the same rule as the Calendar tab;
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

        /* !important: themes give .bg-slate-800 (cards) position: relative, which would drop the popup below the page */
        .dp-pop {
            position: fixed !important; z-index: 10000; width: 16.5rem; padding: 0.6rem;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55); user-select: none;
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);   /* some themes make cards translucent */
        }
        .dp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.35rem; }
        .dp-title { font-weight: 600; font-size: 0.875rem; text-transform: capitalize; }
        .dp-nav { background: none; border: 0; color: inherit; opacity: 0.7; cursor: pointer;
                  width: 1.9rem; height: 1.9rem; border-radius: 0.4rem; display: flex; align-items: center; justify-content: center; }
        .dp-nav:hover { opacity: 1; background: rgba(148, 163, 184, 0.15); }
        .dp-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
        .dp-wd { font-size: 0.68rem; text-transform: uppercase; opacity: 0.55; text-align: center; padding: 0.2rem 0; }
        .dp-day { background: none; border: 1px solid transparent; color: inherit; cursor: pointer;
                  height: 2rem; border-radius: 0.4rem; font-size: 0.8rem; font-variant-numeric: tabular-nums; }
        .dp-day:hover { background: rgba(148, 163, 184, 0.18); }
        .dp-day.dp-out { opacity: 0.38; }
        .dp-day.dp-weekend:not(.dp-sel) { color: #fda4af; }
        .dp-day.dp-today { border-color: rgba(52, 211, 153, 0.75); }
        .dp-day.dp-sel { background: #6366f1; color: #fff; font-weight: 600; }
        .dp-foot { display: flex; justify-content: space-between; margin-top: 0.4rem; }
        .dp-link { background: none; border: 0; color: #818cf8; cursor: pointer; font-size: 0.8rem; padding: 0.3rem 0.4rem; border-radius: 0.4rem; }
        .dp-link:hover { background: rgba(148, 163, 184, 0.15); }
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

    // ----- the month grid -----
    let pop = null;          // the open popup: { el, native, month }

    const pad = (n) => String(n).padStart(2, '0');
    const isoOf = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const lang = () => window.APP_LANG || 'ru-RU';
    const weekStart = () => (isMdy() ? 0 : 1);          // US: Sunday, otherwise Monday (as in calendar.js)

    function setValue(native, iso) {
        proto.set.call(native, iso);
        native.dispatchEvent(new Event('input', { bubbles: true }));
        native.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function closePicker() {
        if (!pop) return;
        pop.el.remove();
        pop = null;
        document.removeEventListener('mousedown', onOutside, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('resize', closePicker);
        window.removeEventListener('scroll', onScroll, true);
    }

    function onOutside(e) {
        if (pop && !pop.el.contains(e.target) && !pop.anchor.contains(e.target)) closePicker();
    }
    function onKey(e) {
        if (e.key === 'Escape' && pop) { e.stopPropagation(); e.preventDefault(); closePicker(); }
    }
    function onScroll(e) {
        if (pop && !pop.el.contains(e.target)) closePicker();
    }

    function renderPicker() {
        const { el, native, month } = pop;
        const sel = proto.get.call(native);
        const today = isoOf(new Date());
        const ws = weekStart();
        const title = new Intl.DateTimeFormat(lang(), { month: 'long', year: 'numeric' }).format(month);

        let html = `<div class="dp-head">
                <button type="button" class="dp-nav" data-nav="-1" title="${t('date.prev_month', 'Previous month')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg></button>
                <span class="dp-title">${title}</span>
                <button type="button" class="dp-nav" data-nav="1" title="${t('date.next_month', 'Next month')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></button>
            </div><div class="dp-grid">`;
        for (let i = 0; i < 7; i++) {
            const d = new Date(2023, 0, 1 + ((ws + i) % 7));              // January 1, 2023 was a Sunday
            html += `<div class="dp-wd">${new Intl.DateTimeFormat(lang(), { weekday: 'short' }).format(d)}</div>`;
        }
        const offset = (month.getDay() - ws + 7) % 7;
        for (let i = 0; i < 42; i++) {
            const d = new Date(month.getFullYear(), month.getMonth(), 1 - offset + i);
            const iso = isoOf(d);
            const cls = ['dp-day'];
            if (d.getMonth() !== month.getMonth()) cls.push('dp-out');
            if (d.getDay() === 0 || d.getDay() === 6) cls.push('dp-weekend');
            if (iso === today) cls.push('dp-today');
            if (iso === sel) cls.push('dp-sel');
            html += `<button type="button" class="${cls.join(' ')}" data-day="${iso}">${d.getDate()}</button>`;
        }
        html += `</div><div class="dp-foot">
                <button type="button" class="dp-link" data-act="clear">${t('date.clear', 'Clear')}</button>
                <button type="button" class="dp-link" data-act="today">${t('date.today', 'Today')}</button>
            </div>`;
        el.innerHTML = html;
    }

    function placePicker() {
        const r = pop.anchor.getBoundingClientRect();
        const w = pop.el.offsetWidth, h = pop.el.offsetHeight, m = 6;
        // right edge under the calendar button (the button sits at the right end of the field)
        let left = Math.min(r.right - w, window.innerWidth - w - m);
        let top = r.bottom + 4;
        if (top + h > window.innerHeight - m && r.top - h - 4 >= m) top = r.top - h - 4;   // no room below: open above
        pop.el.style.left = Math.max(m, left) + 'px';
        pop.el.style.top = Math.max(m, top) + 'px';
    }

    function openPicker(native, anchor) {
        if (pop && pop.native === native) { closePicker(); return; }
        closePicker();
        const cur = proto.get.call(native);
        const base = /^\d{4}-\d{2}-\d{2}$/.test(cur) ? new Date(cur + 'T00:00:00') : new Date();
        const el = document.createElement('div');
        // theme classes: every theme already restyles cards with them
        el.className = 'dp-pop bg-slate-800 border border-slate-700 rounded-xl text-slate-200';
        pop = { el, native, anchor, month: new Date(base.getFullYear(), base.getMonth(), 1) };
        renderPicker();
        document.body.appendChild(el);
        placePicker();

        // mousedown keeps the focus in the text field and stops the modal underneath from closing
        el.addEventListener('mousedown', (e) => e.preventDefault());
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            const b = e.target.closest('button');
            if (!b) return;
            if (b.dataset.nav) {
                pop.month = new Date(pop.month.getFullYear(), pop.month.getMonth() + Number(b.dataset.nav), 1);
                renderPicker();
                placePicker();
            } else if (b.dataset.day) {
                setValue(native, b.dataset.day); closePicker();
            } else if (b.dataset.act === 'today') {
                setValue(native, isoOf(new Date())); closePicker();
            } else if (b.dataset.act === 'clear') {
                setValue(native, ''); closePicker();
            }
        });
        el.addEventListener('wheel', (e) => {             // the wheel flips months
            e.preventDefault();
            pop.month = new Date(pop.month.getFullYear(), pop.month.getMonth() + (e.deltaY > 0 ? 1 : -1), 1);
            renderPicker();
        }, { passive: false });

        document.addEventListener('mousedown', onOutside, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', closePicker);
        window.addEventListener('scroll', onScroll, true);
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

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openPicker(native, wrap);
        });
        text.addEventListener('keydown', (e) => {          // Alt+Down opens the grid, as in a native field
            if (e.altKey && e.key === 'ArrowDown') { e.preventDefault(); openPicker(native, wrap); }
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
