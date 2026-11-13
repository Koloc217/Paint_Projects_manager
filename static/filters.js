/* Project list view: filters (status, stage, priority, overdue), sorting, highlight of search matches.
 *
 * The choice is remembered in the browser (localStorage), the search text is not. Filters work together
 * (AND between groups, OR inside a group) and with the search box. They only touch the list of active projects;
 * the Hall of Fame has its own search. Plain script: shares globals with cards.js (see templates/index.html). */

const VIEW_KEY = 'ph.projectView';
const PRIORITY_RANK = { Critical: 0, High: 1, Medium: 2, Low: 3 };
const SORT_LABELS = { default: 'sort.default', deadline: 'sort.deadline', priority: 'sort.priority', activity: 'sort.activity' };
const SORT_MODES = Object.keys(SORT_LABELS);

// Groups of the filter bar: [value, label key or ready text]. Priorities are shown as they are on the cards.
const FILTER_GROUPS = [
    { key: 'status', label: 'filter.status', hint: 'filter.status_hint', field: 'Status',
      options: [['planned', 'status.planned'], ['in_work', 'status.in_work'], ['on_hold', 'status.on_hold']] },
    { key: 'stage', label: 'filter.stage', hint: 'filter.stage_hint', field: 'Stage',
      options: [['none', 'stage.none'], ['printing', 'stage.printing'], ['sanding', 'stage.sanding'],
                ['assembly', 'stage.assembly'], ['priming', 'stage.priming'], ['painting', 'stage.painting'],
                ['finishing', 'stage.finishing']] },
    { key: 'priority', label: 'filter.priority', hint: 'filter.priority_hint', field: 'Priority',
      options: [['Critical', null], ['High', null], ['Medium', null], ['Low', null]] },
];

let projectView = loadProjectView();
let openFilterGroup = null;       // which pop-up list is open right now

function emptyProjectView() {
    return { status: [], stage: [], priority: [], overdue: false, sort: 'default' };
}

function loadProjectView() {
    const view = emptyProjectView();
    try {
        const saved = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null');
        if (saved && typeof saved === 'object') {
            FILTER_GROUPS.forEach(g => {
                const known = g.options.map(o => o[0]);
                if (Array.isArray(saved[g.key])) view[g.key] = saved[g.key].filter(v => known.includes(v));
            });
            view.overdue = saved.overdue === true;
            if (SORT_MODES.includes(saved.sort)) view.sort = saved.sort;
        }
    } catch (e) { /* no storage or broken value: start with defaults */ }
    return view;
}

function saveProjectView() {
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(projectView)); } catch (e) { /* storage is optional */ }
}

function hasActiveFilters() {
    return projectView.overdue || FILTER_GROUPS.some(g => projectView[g.key].length > 0);
}

function localIsoToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Overdue: the project deadline or the deadline of any of its open tasks is before today
function isProjectOverdue(p, today = localIsoToday()) {
    if (p.Estimate && p.Estimate < today) return true;
    return projectTasks(p.ID).some(t => t.Deadline && t.Deadline < today && !isDoneStatus(t.Status));
}

function projectPassesFilters(p, today = localIsoToday()) {
    for (const g of FILTER_GROUPS) {
        const chosen = projectView[g.key];
        if (chosen.length && !chosen.includes(p[g.field])) return false;
    }
    return !projectView.overdue || isProjectOverdue(p, today);
}

// Sorting never changes the data order itself: a new array. "Without a date" always goes last.
function sortProjects(list) {
    const mode = projectView.sort;
    if (mode === 'default') return list;
    const byDate = (a, b) => (a || '9999-99-99').localeCompare(b || '9999-99-99');
    const rank = p => (p.Priority in PRIORITY_RANK ? PRIORITY_RANK[p.Priority] : 9);
    return [...list].sort((a, b) => {
        if (mode === 'deadline') return byDate(a.Estimate, b.Estimate);
        if (mode === 'priority') return rank(a) - rank(b) || byDate(a.Estimate, b.Estimate);
        return String(b.UpdatedAt || '').localeCompare(String(a.UpdatedAt || ''));      // newest activity first
    });
}

// ---------- Search highlight ----------
function searchWords(query) {
    return normSearch(query).split(/\s+/).filter(Boolean);
}

// Text for innerHTML with the found words in <mark>. Lowercasing may change the length of exotic letters:
// then the offsets would not fit the original text, and the text is shown without a highlight.
function highlightText(text, query) {
    const src = String(text ?? '');
    const words = searchWords(query);
    const norm = normSearch(src);
    if (!words.length || norm.length !== src.length) return esc(src);
    const ranges = [];
    words.forEach(w => {
        let i = norm.indexOf(w);
        while (i !== -1) { ranges.push([i, i + w.length]); i = norm.indexOf(w, i + w.length); }
    });
    if (!ranges.length) return esc(src);
    ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [ranges[0].slice()];
    ranges.slice(1).forEach(r => {
        const last = merged[merged.length - 1];
        if (r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push(r.slice());
    });
    let out = '', pos = 0;
    merged.forEach(([from, to]) => {
        out += esc(src.slice(pos, from)) + '<mark class="hl">' + esc(src.slice(from, to)) + '</mark>';
        pos = to;
    });
    return out + esc(src.slice(pos));
}

// "Why is this project here": when the name itself does not contain the words, say where they were found
function searchMatchHint(p, query) {
    const words = searchWords(query);
    if (!words.length) return '';
    const name = normSearch(p.Name);
    const rest = words.filter(w => !name.includes(w));
    if (!rest.length) return '';
    const notes = normSearch(p.PaintNotes);
    if (rest.some(w => notes.includes(w))) return `🔎 ${esc(tr('search.in_notes'))}`;
    const task = projectTasks(p.ID).find(t => rest.some(w => normSearch(t.Task).includes(w)));
    if (task) return `🔎 ${esc(tr('search.in_task'))}: ${highlightText(task.Task, query)}`;
    return '';
}

// ---------- The filter bar ----------
function filterOptionLabel(group, option) {
    return option[1] ? tr(option[1]) : option[0];
}

function renderFilterBar() {
    const bar = document.getElementById('projectFilters');
    if (!bar) return;
    const groups = FILTER_GROUPS.map(g => {
        const n = projectView[g.key].length;
        const open = openFilterGroup === g.key;
        const items = g.options.map(o => {
            const on = projectView[g.key].includes(o[0]);
            return `<label class="pf-item"><input type="checkbox" ${on ? 'checked' : ''} onchange="toggleFilterValue('${g.key}', '${o[0]}')"> <span>${esc(filterOptionLabel(g, o))}</span></label>`;
        }).join('');
        return `<div class="pf-group">
            <button type="button" class="pf-btn${n ? ' pf-on' : ''}" aria-expanded="${open}" onclick="toggleFilterPopover('${g.key}')" title="${esc(tr(g.hint))}">${esc(tr(g.label))}${n ? ` <span class="pf-count">${n}</span>` : ''} <span class="pf-caret">▾</span></button>
            <div class="pf-pop${open ? '' : ' hidden'}">${items}</div>
        </div>`;
    }).join('');
    const sortOptions = SORT_MODES.map(m => `<option value="${m}" ${projectView.sort === m ? 'selected' : ''}>${esc(tr(SORT_LABELS[m]))}</option>`).join('');
    bar.innerHTML = `${groups}
        <button type="button" class="pf-btn${projectView.overdue ? ' pf-on' : ''}" aria-pressed="${projectView.overdue}" onclick="toggleOverdueFilter()" title="${esc(tr('filter.overdue_hint'))}">⚠️ ${esc(tr('filter.overdue'))}</button>
        <select class="pf-sort" onchange="setProjectSort(this.value)" title="${esc(tr('sort.hint'))}" aria-label="${esc(tr('sort.hint'))}">${sortOptions}</select>
        ${hasActiveFilters() || projectView.sort !== 'default' ? `<button type="button" class="pf-reset" onclick="resetProjectView()" title="${esc(tr('filter.reset_hint'))}">✕ ${esc(tr('filter.reset'))}</button>` : ''}`;
}

function refreshProjectView() {
    saveProjectView();
    renderFilterBar();
    renderProjects();
}

function toggleFilterValue(groupKey, value) {
    const list = projectView[groupKey];
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
    refreshProjectView();
}

function toggleOverdueFilter() {
    projectView.overdue = !projectView.overdue;
    refreshProjectView();
}

function setProjectSort(mode) {
    projectView.sort = SORT_MODES.includes(mode) ? mode : 'default';
    refreshProjectView();
}

function resetProjectView() {
    projectView = emptyProjectView();
    openFilterGroup = null;
    refreshProjectView();
}

function toggleFilterPopover(groupKey) {
    openFilterGroup = openFilterGroup === groupKey ? null : groupKey;
    renderFilterBar();
}

function closeFilterPopover() {
    if (openFilterGroup === null) return;
    openFilterGroup = null;
    renderFilterBar();
}

// A click outside the open list or Escape closes it
document.addEventListener('click', e => {
    if (openFilterGroup !== null && !(e.target.closest && e.target.closest('.pf-group'))) closeFilterPopover();
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && openFilterGroup !== null) closeFilterPopover();
});
