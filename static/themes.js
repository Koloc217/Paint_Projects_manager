/* UI skins: the THEMES registry and the switcher in the header. A new theme = a row in THEMES here + a body.<id>
 * block in themes.css (see docs/THEMES_GUIDE.md). Loaded after main.js, as the switcher was set up after the first load. */

// ---------- UI skin switcher ----------
// Theme registry. A new theme = a row here + a body.<id> CSS block in themes.css (see docs/THEMES_GUIDE.md).
// id is the class name on <body>; 'default' is the standard look without a class.
const THEMES = [
    { id: 'default', label: () => `💿 ${tr('theme.default')}` },
    { id: 'theme-cyberpunk', label: () => '⚡ Cyberpunk' },
    { id: 'theme-warhammer', label: () => '💀 Hammer' },
    { id: 'theme-scp', label: () => '☢️ SCP Dossier' },
    { id: 'theme-workbench', label: () => '🛠️ Workbench' },
    { id: 'theme-synthwave', label: () => '🌆 Synthwave' },
    { id: 'theme-ff7', label: () => '💠 JRPG (FFVII)' },
    { id: 'theme-tamiya', label: () => '📐 Model Manual' },
    { id: 'theme-pcb', label: () => '📟 Hardware PCB' },
];

function initThemeSwitcher() {
    let savedTheme = 'default';
    try { savedTheme = localStorage.getItem('ph_skin') || 'default'; } catch (e) { /* ignore */ }
    applyThemeSkin(savedTheme);

    const nav = document.querySelector('nav');
    if (!nav) return;

    const select = document.createElement('select');
    select.id = 'skinSelector';
    select.title = tr('theme.choose');
    select.className = 'text-xs bg-slate-800 border border-slate-600 text-slate-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer ml-2';
    select.innerHTML = THEMES.map(t => `<option value="${t.id}">${t.label()}</option>`).join('');
    select.value = THEMES.some(t => t.id === savedTheme) ? savedTheme : 'default';
    select.onchange = (e) => applyThemeSkin(e.target.value);

    // Put the switcher next to the right-hand button group in the header
    const rightGroup = nav.lastElementChild || nav;
    rightGroup.prepend(select);
}

function applyThemeSkin(skin) {
    if (!THEMES.some(t => t.id === skin)) skin = 'default';      // a removed or unknown theme -> the standard one
    document.body.classList.remove(...THEMES.map(t => t.id).filter(id => id !== 'default'));
    if (skin !== 'default') document.body.classList.add(skin);
    try { localStorage.setItem('ph_skin', skin); } catch (e) { /* without localStorage the theme is just not remembered */ }
    // the charts were drawn with the old colors: redraw them for the new theme
    if (stageChartInstance || velocityChartInstance || timeChartInstance) renderAnalytics();
}

initThemeSwitcher();
