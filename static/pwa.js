/* PWA: регистрация service worker и установка страницы на экран телефона.
 *
 * Service worker работает только в «безопасном контексте»: https с доверенным сертификатом или localhost.
 * По обычному http в домашней сети он не регистрируется, но значок на главный экран добавить всё равно можно
 * (подсказка в Настройки → Основные → «Приложение на телефоне»).
 */
(function () {
    let deferredPrompt = null;

    const isStandalone = () =>
        window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    const isIos = () =>
        /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);   // iPadOS представляется как Mac

    function renderPwaState() {
        const btn = document.getElementById('pwaInstallBtn');
        const status = document.getElementById('pwaStatus');
        if (!btn || !status) return;
        btn.classList.toggle('hidden', !deferredPrompt);
        let lines;
        if (isStandalone()) lines = ['✅ ' + tr('pwa.installed')];
        else if (deferredPrompt) lines = [];
        else if (isIos()) lines = [tr('pwa.ios')];
        else if (!window.isSecureContext) lines = [tr('pwa.menu'), '⚠️ ' + tr('pwa.insecure')];
        else lines = [tr('pwa.menu')];
        status.textContent = lines.join('\n');
        status.style.whiteSpace = 'pre-line';
        status.classList.toggle('hidden', lines.length === 0);
    }

    window.installPwa = async function () {
        if (!deferredPrompt) return;
        const prompt = deferredPrompt;
        deferredPrompt = null;
        prompt.prompt();
        try { await prompt.userChoice; } catch (e) { /* окно закрыли */ }
        renderPwaState();
    };

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();          // своё окно не показываем сразу: кнопка в настройках
        deferredPrompt = e;
        renderPwaState();
    });

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        renderPwaState();
        if (typeof showToast === 'function') showToast(tr('pwa.done'), 'success');
    });

    if ('serviceWorker' in navigator && window.isSecureContext) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .catch((err) => console.warn('Service worker не зарегистрирован:', err));
        });
    }

    document.addEventListener('DOMContentLoaded', renderPwaState);
    if (document.readyState !== 'loading') renderPwaState();
})();
