/* Поля даты в формате из настроек (Настройки → Общие → формат даты).
 *
 * Родное <input type="date"> браузер показывает в формате своего языка (в английском Chrome это 10/31/2026),
 * и на настройку приложения повлиять нельзя. Поэтому рядом с каждым таким полем ставится текстовое поле
 * в формате приложения (31.10.2026 для dmy, 10/31/2026 для mdy) с кнопкой календаря, а родное поле
 * становится невидимым хранилищем значения:
 * - остальной код как раньше читает и пишет `input.value` в ISO (2026-10-31), ничего менять не нужно;
 * - запись `input.value = '...'` из кода сразу обновляет видимое поле;
 * - кнопка календаря открывает системный выбор даты того же родного поля (showPicker);
 * - поля, добавленные позже (строки окна шаблона пайплайна), подхватываются автоматически.
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
        .date-field > .date-text { padding-right: 2rem !important; }
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

    // Ровно 8 цифр (дд мм гггг или мм дд гггг) и реально существующая дата -> ISO, иначе null
    function textToIso(text) {
        const digits = String(text || '').replace(/\D/g, '');
        if (digits.length !== 8) return null;
        const a = +digits.slice(0, 2), b = +digits.slice(2, 4), y = +digits.slice(4);
        const [d, m] = isMdy() ? [b, a] : [a, b];
        const dt = new Date(y, m - 1, d);
        if (y < 1900 || dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
        return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    }

    // Разделители подставляются сами: 3110 -> 31.10, 31102026 -> 31.10.2026
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
        // Классы оформления копируем, служебные (tpl-date и т. п.) остаются только у родного поля
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
        // Родное поле первым: querySelector('.класс') из остального кода находит его, а не видимую копию
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
            // Маску ставим только при вводе: иначе Backspace по разделителю ничего бы не стирал
            if (!e.inputType || e.inputType.startsWith('insert')) {
                const masked = mask(digits);
                if (text.value !== masked) text.value = masked;
            }
            if (!digits) { proto.set.call(native, ''); return; }
            const iso = textToIso(digits);
            if (iso) proto.set.call(native, iso);
        });

        // Ушли с поля с неполной или несуществующей датой: возвращаем последнее верное значение
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
            } catch (e) { /* старый браузер: ниже запасной вариант */ }
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
