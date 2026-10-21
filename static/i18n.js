/* Локализация интерфейса (клиентская часть).
 *
 * Тексты лежат в одном файле locales.json (блоки по языкам: "ru-RU", "en-US"...). Сервер выбирает
 * блок текущего языка, закрывает пропуски языком по умолчанию и кладёт готовый словарь в страницу
 * как window.APP_I18N. Статическая разметка переводится на сервере ({{ tr(KEY) }} в шаблонах),
 * здесь — только тексты, которые рисует JavaScript.
 *
 *   tr('common.save')                        -> 'Сохранить' / 'Save'
 *   tr('ref.limit', {n: 10})                 -> 'Лимит: максимум 10 референсов на проект'
 *
 * Ключ технический (area.name); нет ключа в словаре — виден сам ключ.
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
