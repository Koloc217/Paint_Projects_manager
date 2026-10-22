/* PWA: service worker registration and installing the page on a phone home screen.
 *
 * A service worker only works in a "secure context": https with a trusted certificate, or localhost.
 * Over plain http in a home network it is not registered, but a home screen icon can still be added
 * (see the hint in Settings → General → "App on the phone").
 */
(function () {
    let deferredPrompt = null;

    const isStandalone = () =>
        window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    const isIos = () =>
        /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);   // iPadOS presents itself as a Mac

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
        try { await prompt.userChoice; } catch (e) { /* the dialog was closed */ }
        renderPwaState();
    };

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();          // no prompt right away: there is a button in the settings
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
                .catch((err) => console.warn('Service worker not registered:', err));
        });
    }

    document.addEventListener('DOMContentLoaded', renderPwaState);
    if (document.readyState !== 'loading') renderPwaState();
})();
