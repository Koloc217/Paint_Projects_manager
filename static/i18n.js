/* Interface localization (client side).
 *
 * Texts live in one file, locales.json (blocks per language: "ru-RU", "en-US"...). The server picks the block
 * of the current language, fills gaps from the default language and puts the ready dictionary into the page
 * as window.APP_I18N. Static markup is translated on the server ({{ tr(KEY) }} in templates);
 * here only the texts drawn by JavaScript.
 *
 *   tr('common.save')                        -> 'Сохранить' / 'Save'
 *   tr('ref.limit', {n: 10})                 -> 'Limit: at most 10 references per project'
 *
 * Keys are technical (area.name); a key missing from the dictionary is shown as is.
 */
(function () {
    const dict = window.APP_I18N || {};

    function fill(text, params) {
        if (!params) return text;
        return text.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined ? params[k] : m));
    }

    window.tr = function (key, params) {
        key = String(key);
        const text = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
        return fill(text, params);
    };
})();
