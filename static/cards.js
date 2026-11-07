/* Dashboard and project cards: search, lists, the project card (details), time spent, pipeline templates,
 * creating, editing and deleting projects and tasks, the card in a separate tab. */

// ---------- Project card in a separate tab ----------
function openCardTab(id) {
    if (!id) return;
    const url = `/?card=${encodeURIComponent(id)}`;
    // without window features the browser opens a normal tab; with the same name, another click on the
    // same project switches to the already open tab instead of opening new ones
    const tab = window.open(url, `card_${id}`);
    if (!tab) showToast(tr('toast.popup_blocked'));
}

function closeStandaloneWindow() {
    window.close();
    // if the browser refused to close the tab (it was not opened by the app), go back to the dashboard
    setTimeout(() => { location.href = '/'; }, 150);
}

async function initStandaloneCard(id) {
    document.body.classList.add('card-standalone');      // wide layout, see static/card-tab.css
    document.querySelector('nav').classList.add('hidden');
    ['view-dashboard', 'view-allTasks', 'view-analytics'].forEach(v => document.getElementById(v).classList.add('hidden'));
    document.getElementById('pdOpenWindow').classList.add('hidden');
    await loadData();
    const project = currentProjects.find(p => p.ID === id);
    if (!project) {
        showToast(tr('err.project_not_found_deleted'));
        setTimeout(() => { location.href = '/'; }, 1800);
        return;
    }
    document.title = project.Name;
    openProjectDetails(id);
}

// ---------- Project search ----------
// The query is several space-separated words; all of them must match (case-insensitive, ё equals е).
// Searches the name, id, priority, status, stage (both the stored Russian value and its UI translation),
// the paint recipe and the project's task names.
const searchQuery = { active: '', hall: '' };
const STATUS_TR = { 'planned': 'status.planned', 'in_work': 'status.in_work', 'on_hold': 'status.on_hold', 'finished': 'status.finished' };
const STAGE_TR = { 'printing': 'stage.printing', 'sanding': 'stage.sanding', 'assembly': 'stage.assembly', 'priming': 'stage.priming', 'painting': 'stage.painting', 'finishing': 'stage.finishing' };

function normSearch(s) { return String(s == null ? '' : s).toLowerCase().replace(/ё/g, 'е'); }

function projectMatches(p, query) {
    const words = normSearch(query).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const parts = [p.Name, p.ID, p.Priority, p.Status, p.Stage, p.PaintNotes];
    if (STATUS_TR[p.Status]) parts.push(tr(STATUS_TR[p.Status]));
    if (STAGE_TR[p.Stage]) parts.push(tr(STAGE_TR[p.Stage]));
    projectTasks(p.ID).forEach(t => parts.push(t.Task));
    const hay = normSearch(parts.filter(x => x != null && x !== '').join(' \n '));
    return words.every(w => hay.includes(w));
}

function onProjectSearch(which, value) {
    searchQuery[which] = value || '';
    if (which === 'hall') renderHall(); else renderProjects();
}

function renderProjects() {
    const container = document.getElementById('projectsContainer');
    const allActive = currentProjects.filter(p => !isDoneStatus(p.Status));
    const activeProjects = allActive.filter(p => projectMatches(p, searchQuery.active));
    const searching = searchQuery.active.trim() !== '';
    document.getElementById('projectCount').innerText = searching ? `${activeProjects.length} / ${allActive.length}` : allActive.length;
    if (searching && !activeProjects.length) {
        container.innerHTML = `<div class="col-span-full text-center py-8 text-slate-500 bg-slate-800/40 rounded-2xl border border-slate-800">${tr('search.nothing')}</div>`;
        return;
    }
    if (!allActive.length) {
        const hasDone = currentProjects.length > 0;
        container.innerHTML = `<div class="col-span-full text-center py-10 px-4 bg-slate-800/40 rounded-2xl border border-dashed border-slate-700">
            <div class="text-3xl mb-2">🎨</div>
            <div class="text-slate-300 font-semibold mb-1">${tr(hasDone ? 'empty.projects_all_done' : 'empty.projects_title')}</div>
            <div class="text-xs text-slate-500 mb-4">${tr('empty.projects_text')}</div>
            <button onclick="openModal('projectModal')" class="text-sm px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg">+ ${tr('common.project')}</button>
        </div>`;
        return;
    }

    patchList(container, activeProjects, p => p.ID, p => {
        const pTasks = projectTasks(p.ID);
        const doneCount = pTasks.filter(t => isDoneStatus(t.Status)).length;
        const activeCount = pTasks.length - doneCount;
        const hasPaintNotes = p.PaintNotes && p.PaintNotes.trim().length > 0;

        return `
        <div class="bg-slate-800 rounded-2xl p-4 border border-slate-700/50 hover:border-slate-600 transition-colors shadow flex flex-col gap-3 h-full group relative">
            <button onclick="event.stopPropagation(); openCardTab('${p.ID}')" class="absolute top-3 right-10 text-slate-500 hover:text-sky-400 transition-colors z-10" title="${tr('project.open_card_tab')}">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
            </button>
            <button onclick="event.stopPropagation(); deleteProject('${p.ID}')" class="absolute top-3 right-3 text-slate-500 hover:text-red-400 transition-colors z-10" title="${tr('project.delete')}">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
            </button>

            <div class="flex gap-3 sm:gap-4 flex-grow">
                <div onclick="event.stopPropagation(); onProjectPhotoClick('${p.ID}')" data-drop="photo" data-pid="${esc(p.ID)}" title="${p.Photo ? tr('photo.open') : tr('photo.add')}${'\n' + tr('drop.photo_hint')}" class="w-20 h-20 sm:w-32 sm:h-32 shrink-0 rounded-xl border border-slate-600/60 bg-slate-900/60 overflow-hidden cursor-pointer hover:border-indigo-400 transition-colors">
                    ${projectPhotoInner(p)}
                </div>

                <div onclick="openProjectDetails('${p.ID}')" class="cursor-pointer flex-grow min-w-0">
                    <h3 class="text-base font-semibold text-white mb-2 pr-14 sm:pr-16 line-clamp-2 sm:truncate break-words" title="${esc(p.Name)}">${esc(p.Name)}</h3>

                    <div class="flex items-center gap-2 mb-3 flex-wrap">
                        ${p['Token Reward'] > 0 ? `<span class="text-[10px] text-amber-400 font-bold bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">+${p['Token Reward']}</span>` : ''}
                        <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${getPriorityColor(p.Priority)}">${esc(p.Priority)}</span>
                        ${hasPaintNotes ? `<span class="text-[10px] bg-pink-500/10 border border-pink-500/30 text-pink-300 px-1.5 py-0.5 rounded" title="${tr('recipe.exists')}">📝</span>` : ''}
                        ${(p.Refs && p.Refs.length) ? `<span class="text-[10px] bg-sky-500/10 border border-sky-500/30 text-sky-300 px-1.5 py-0.5 rounded" title="${tr('ref.title')}">🖼 ${p.Refs.length}</span>` : ''}
                        <div class="flex items-center gap-2 text-[11px] bg-slate-900/50 px-2 py-0.5 rounded-md border border-slate-700/50 ml-auto">
                            <span class="flex items-center gap-1 text-emerald-400" title="${tr('common.completed')}">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>
                                ${doneCount}
                            </span>
                            <span class="text-slate-600">|</span>
                            <span class="flex items-center gap-1 text-indigo-400" title="${tr('task.pending_hint')}">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path></svg>
                                ${activeCount}
                            </span>
                        </div>
                    </div>
                    <div class="text-xs">${getDeadlineHTML(p.Estimate)}</div>
                </div>
            </div>

            <div class="pt-3 border-t border-slate-700/50 flex items-center justify-between gap-2">
                <select onclick="event.stopPropagation()" onchange="updateProjectStatus('${p.ID}', this.value)" class="w-1/2 text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer">
                    <option value="planned" ${p.Status === 'planned' ? 'selected' : ''}>${tr('status.planned')}</option>
                    <option value="in_work" ${p.Status === 'in_work' ? 'selected' : ''}>${tr('status.in_work')}</option>
                    <option value="on_hold" ${p.Status === 'on_hold' ? 'selected' : ''}>${tr('status.on_hold')}</option>
                    <option value="finished">${tr('project.complete')} ✔️</option>
                </select>
                <select onclick="event.stopPropagation()" onchange="updateProjectStage('${p.ID}', this.value)" class="w-1/2 text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer">
                    <option value="none" ${p.Stage === 'none' ? 'selected' : ''}>${tr('project.stage')}: —</option>
                    <option value="printing" ${p.Stage === 'printing' ? 'selected' : ''}>${tr('stage.printing')}</option>
                    <option value="sanding" ${p.Stage === 'sanding' ? 'selected' : ''}>${tr('stage.sanding')}</option>
                    <option value="assembly" ${p.Stage === 'assembly' ? 'selected' : ''}>${tr('stage.assembly')}</option>
                    <option value="priming" ${p.Stage === 'priming' ? 'selected' : ''}>${tr('stage.priming')}</option>
                    <option value="painting" ${p.Stage === 'painting' ? 'selected' : ''}>${tr('stage.painting')}</option>
                    <option value="finishing" ${p.Stage === 'finishing' ? 'selected' : ''}>${tr('stage.finishing')}</option>
                </select>
            </div>
        </div>`;
    });
}

function renderTasks() {
    const container = document.getElementById('tasksContainer');
    let activeTasks = currentTasks.filter(t => !isDoneStatus(t.Status));
    activeTasks.sort((a, b) => {
        if (!a.Deadline && !b.Deadline) return 0;
        if (!a.Deadline) return 1;
        if (!b.Deadline) return -1;
        return new Date(a.Deadline) - new Date(b.Deadline);
    });
    
    if (!activeTasks.length) {
        const noProjects = !currentProjects.some(p => !isDoneStatus(p.Status));
        // a general task (no project) can be added even without projects
        container.innerHTML = `<div class="p-6 text-center">
            <div class="text-sm text-slate-400 mb-3">${tr(noProjects ? 'empty.tasks_no_projects' : 'empty.tasks')}</div>
            <button onclick="openTaskModal(${noProjects ? "''" : 'undefined'})" class="text-xs px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg">+ ${tr('common.task')}</button>
        </div>`;
        return;
    }
    patchList(container, activeTasks.slice(0, 5), t => t.ID, t => {
        const proj = currentProjects.find(p => p.ID === t['Project ID']);
        const label = taskProjectLabel(t, proj);
        const isTaskDone = isDoneStatus(t.Status);
        return `
        <div class="p-3 hover:bg-slate-750 transition-colors relative group">
            <div class="absolute top-3 right-3 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 p-1" title="${tr('common.edit')}">✏️</button>
                <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 p-1" title="${tr('common.delete')}">✕</button>
            </div>
            <div class="flex flex-wrap justify-between items-start gap-x-2 mb-1 pr-14">
                <span class="text-[10px] uppercase tracking-wider font-semibold ${t['Project ID'] ? 'text-indigo-400' : 'text-teal-300'} truncate max-w-[10rem]" title="${esc(label)}">${esc(label)}</span>
                <div class="text-xs">${getDeadlineHTML(t.Deadline, true, isTaskDone)}</div>
            </div>
            <p class="text-sm text-slate-200 font-medium mb-2 leading-snug">${esc(t.Task)}</p>
            <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-[11px] bg-slate-900 border border-slate-700 text-slate-300 rounded px-1.5 py-1 outline-none cursor-pointer w-32">
                <option value="not_started" ${t.Status === 'not_started' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                <option value="in_progress" ${t.Status === 'in_progress' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                <option value="finished">${tr('status.done')} ✔️</option>
            </select>
        </div>`;
    });
}

// Group label of a task: its project, or "General" for a task without a project
function taskProjectLabel(t, proj) {
    if (!t['Project ID']) return tr('general.label');
    return proj ? proj.Name : t['Project ID'];
}

function renderAllTasks() {
    const container = document.getElementById('allTasksContainer');
    const activeProjects = currentProjects.filter(p => !isDoneStatus(p.Status));
    // General tasks (no project) come first; closed ones older than the open ones stay visible like in projects
    const generalTasks = projectTasks(null);
    if (activeProjects.length === 0 && !generalTasks.length) {
        container.innerHTML = `<p class="text-slate-500 text-center py-4">${tr('project.no_active')}</p>`;
        return;
    }

    const groups = [];
    const sections = [];
    if (generalTasks.length) sections.push({ key: '_general', tasks: generalTasks, head: `
                <div class="min-w-0">
                    <h3 class="text-base font-bold text-teal-300">🧰 ${esc(tr('general.title'))}</h3>
                    <div class="text-[11px] text-slate-500">${esc(tr('general.hint'))}</div>
                </div>
                <button onclick="openTaskModal('')" class="shrink-0 text-xs px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-lg">+ ${tr('common.task')}</button>` });
    activeProjects.forEach(proj => sections.push({ key: proj.ID, tasks: projectTasks(proj.ID), head: `
                <h3 class="text-base font-bold text-white">${esc(proj.Name)}</h3>
                <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${getPriorityColor(proj.Priority)}">${esc(proj.Priority)}</span>` }));

    sections.forEach(sec => {
        if (sec.tasks.length === 0) return;
        const pTasks = sortTasks(sec.tasks.slice());

        let html = `
        <div class="bg-slate-800 rounded-2xl border border-slate-700/50 shadow-xl overflow-hidden mb-6">
            <div class="bg-slate-900/80 px-4 py-3 border-b border-slate-700/50 flex justify-between items-center gap-3">${sec.head}
            </div>
            <div class="p-3 space-y-2">`;

        html += pTasks.map(t => {
            const isDone = isDoneStatus(t.Status);
            const op = isDone ? 'opacity-40 line-through' : '';
            return `
            <div class="p-3 bg-slate-900 rounded-xl border border-slate-700 flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 group">
                <div class="${op} flex-grow min-w-0 break-words">
                    <div class="text-xs font-mono text-slate-500 mb-1">${getDeadlineHTML(t.Deadline, true, isDone)}</div>
                    <div class="text-sm text-slate-200 font-medium">${esc(t.Task)}${spentBadge(t)}</div>
                </div>
                <div class="flex items-center gap-3 sm:ml-4">
                    <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-xs bg-slate-800 border border-slate-600 text-slate-300 rounded-lg px-2 py-1 outline-none cursor-pointer">
                        <option value="not_started" ${t.Status === 'not_started' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                        <option value="in_progress" ${t.Status === 'in_progress' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                        <option value="finished" ${isDone ? 'selected' : ''}>${tr('status.done')} ✔️</option>
                    </select>
                    <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 opacity-0 group-hover:opacity-100 p-1">✏️</button>
                    <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 p-1">🗑️</button>
                </div>
            </div>`;
        }).join('');
        html += `</div></div>`;
        groups.push({ key: sec.key, html });
    });
    if (!groups.length) {
        container.innerHTML = `<p class="text-slate-500 text-center py-4">${tr('task.no_active')}</p>`;
        return;
    }
    patchList(container, groups, g => g.key, g => g.html);
}

let cardTab = 'main';
let cardTabProjectId = null;

function switchCardTab(tab) {
    cardTab = tab === 'media' ? 'media' : 'main';
    document.getElementById('pdPanel').classList.toggle('pd-tab-media', cardTab === 'media');
    ['main', 'media'].forEach(k => {
        const btn = document.getElementById(`pdSubBtn-${k}`);
        const on = k === cardTab;
        btn.classList.toggle('bg-slate-700', on);
        btn.classList.toggle('text-white', on);
        btn.classList.toggle('shadow-sm', on);
        btn.classList.toggle('text-slate-400', !on);
        btn.classList.toggle('hover:text-white', !on);
        btn.classList.toggle('hover:bg-slate-800', !on);
    });
}

// ---------- Time spent (optional; never affects tokens) ----------
// Exact for a task ("1 h 30 min"), rounded for a project total: not every task has a time, so a total is an estimate.
function formatMinutes(min) {
    const h = Math.floor(min / 60), m = min % 60;
    if (h && m) return tr('time.h_m', { h, m });
    return h ? tr('time.h', { h }) : tr('time.m', { m });
}

function formatApproxMinutes(min) {
    if (min >= 600) return tr('time.h', { h: Math.round(min / 60) });
    return formatMinutes(min >= 10 ? Math.max(10, Math.round(min / 10) * 10) : min);
}

// The value put back into the edit field: what the server reads ("90", "1:30")
function minutesToInput(min) {
    const h = Math.floor(min / 60), m = min % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}` : String(m);
}

function projectSpent(project) {
    const tasks = projectTasks(project.ID);
    const withTime = tasks.filter(t => t.Spent);
    const extra = project.ExtraSpent || 0;
    const byStage = {};
    withTime.forEach(t => { const k = t.SpentStage || 'none'; byStage[k] = (byStage[k] || 0) + t.Spent; });
    if (extra) byStage._extra = extra;
    return { total: withTime.reduce((a, t) => a + t.Spent, 0) + extra, withTime: withTime.length, tasks: tasks.length, extra, byStage };
}

const STAGE_ORDER = ['printing', 'sanding', 'assembly', 'priming', 'painting', 'finishing', 'none', '_extra'];

function spentBadge(t) {
    return t.Spent ? ` <span class="ml-1 text-[10px] font-normal text-slate-400 whitespace-nowrap no-underline" title="${esc(tr('time.spent'))}">⏱ ${formatMinutes(t.Spent)}</span>` : '';
}

function spentSummaryHTML(project) {
    const s = projectSpent(project);
    const editBtn = `<button onclick="editProjectExtraSpent('${esc(project.ID)}')" class="text-[11px] text-slate-400 hover:text-indigo-300 underline decoration-dotted underline-offset-2">${tr('time.edit_extra')}</button>`;
    if (!s.total) {
        return `<div class="mb-3 px-3 py-2 bg-slate-900/60 border border-slate-700/60 rounded-xl text-xs text-slate-500 flex flex-wrap items-center justify-between gap-2">
            <span>⏱ ${tr('time.none')}</span>${editBtn}</div>`;
    }
    const parts = STAGE_ORDER.filter(k => s.byStage[k]).map(k => {
        const label = k === '_extra' ? tr('time.extra') : (k === 'none' || !PROGRESS_STAGE_KEYS[k] ? tr('time.no_stage') : tr(PROGRESS_STAGE_KEYS[k]));
        return `<span class="whitespace-nowrap">${esc(label)} ${formatApproxMinutes(s.byStage[k])}</span>`;
    });
    return `<div class="mb-3 px-3 py-2 bg-slate-900/60 border border-slate-700/60 rounded-xl text-xs">
        <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span class="text-slate-200">⏱ ${tr('time.spent')}: <b>${tr('time.approx', { time: formatApproxMinutes(s.total) })}</b></span>
            ${editBtn}
        </div>
        <div class="text-[11px] text-slate-500 mt-1">${tr('time.coverage', { n: s.withTime, total: s.tasks })}${parts.length > 1 ? ' · ' + parts.join(' · ') : ''}</div>
    </div>`;
}

async function editProjectExtraSpent(projectId) {
    const p = currentProjects.find(x => x.ID === projectId);
    if (!p) return;
    const answer = await askDialog({
        title: tr('time.extra_title'), message: tr('time.extra_text'),
        input: { value: p.ExtraSpent ? minutesToInput(p.ExtraSpent) : '', placeholder: tr('time.extra_ph'), maxLength: 20 },
        buttons: [{ value: 'save', label: tr('common.save'), primary: true }],
    });
    if (!answer) return;
    const r = await postJson(`/api/projects/${encodeURIComponent(projectId)}/spent`, { extra: answer.text.trim() });
    if (r.status === 'error') return showToast(r.message || tr('err.save_failed'), 'error');
    showToast(tr('time.saved'), 'success');
    loadData();
}

function openProjectDetails(projectId) {
    if (cardTabProjectId !== projectId) cardTab = 'main';
    cardTabProjectId = projectId;
    currentOpenProjectId = projectId;
    const project = currentProjects.find(p => p.ID === projectId);
    if (!project) return;
    const titleEl = document.getElementById('pdTitle');
    document.getElementById('pdTitleText').innerText = project.Name;
    titleEl.title = project.Name;          // the full name on hover when it is longer than two lines
    // A background refresh (window focus, another action) must not wipe notes the user is typing right now
    const notesEl = document.getElementById('pdPaintNotes');
    const sameProject = notesEl.dataset.projectId === projectId;
    const editing = sameProject && (document.activeElement === notesEl || notesEl.value !== (notesEl.dataset.saved ?? ''));
    if (!editing) {
        notesEl.value = project.PaintNotes || '';
        notesEl.dataset.saved = notesEl.value;
        notesEl.dataset.projectId = projectId;
    }
    document.getElementById('pdPhoto').innerHTML = projectPhotoInner(project, false);
    document.getElementById('pdPhotoActions').classList.toggle('invisible', !project.Photo);   // the row keeps its place: same header height with or without a cover
    renderProjectRefs(project);
    renderProjectProgress(project);
    const mediaCount = (project.Refs || []).length + (project.Progress || []).length;
    document.getElementById('pdMediaCount').innerText = mediaCount ? String(mediaCount) : '';
    switchCardTab(cardTab);

    let pTasks = projectTasks(projectId).slice();
    const container = document.getElementById('pdTasks');
    
    if (pTasks.length === 0) {
        container.innerHTML = spentSummaryHTML(project) + `<div class="text-center py-4">
            <p class="text-sm text-slate-500 mb-3">${tr('project.no_tasks_hint')}</p>
            <button onclick="openTaskModalForProject()" class="text-xs px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg">+ ${tr('common.task')}</button>
        </div>`;
    } else {
        pTasks = sortTasks(pTasks);
        const rows = [{ key: '_spent', html: spentSummaryHTML(project) }, ...pTasks.map(t => ({ key: t.ID, html: (() => {
            const isDone = isDoneStatus(t.Status);
            const op = isDone ? 'opacity-40 line-through' : '';
            return `
            <div class="p-3 bg-slate-900 rounded-xl border border-slate-700 flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 group mb-2">
                <div class="${op} flex-grow min-w-0 break-words">
                    <div class="text-xs font-mono text-slate-500 mb-1">${getDeadlineHTML(t.Deadline, true, isDone)}</div>
                    <div class="text-sm text-slate-200 font-medium">${esc(t.Task)}${spentBadge(t)}</div>
                </div>
                <div class="flex gap-2 sm:ml-3 items-center">
                    <select onchange="updateTaskStatus('${t.ID}', this.value)" class="text-xs bg-slate-800 border border-slate-600 text-slate-300 rounded-lg px-2 py-1 outline-none cursor-pointer">
                        <option value="not_started" ${t.Status === 'not_started' ? 'selected' : ''}>${tr('status.not_started')} ⏸️</option>
                        <option value="in_progress" ${t.Status === 'in_progress' ? 'selected' : ''}>${tr('status.in_progress')} 🔄</option>
                        <option value="finished" ${isDone ? 'selected' : ''}>${tr('status.done')} ✔️</option>
                    </select>
                    <button onclick="openEditTaskModal('${t.ID}')" class="text-slate-500 hover:text-blue-400 opacity-0 group-hover:opacity-100 p-1">✏️</button>
                    <button onclick="deleteTask('${t.ID}')" class="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 p-1">🗑️</button>
                </div>
            </div>`;
        })() }))];
        patchList(container, rows, r => r.key, r => r.html);
    }
    openModal('projectDetailsModal');
}

async function savePaintNotes(silent = false) {
    if (!currentOpenProjectId) return;
    const notesEl = document.getElementById('pdPaintNotes');
    const notes = notesEl.value;
    const projectId = currentOpenProjectId;
    const proj = currentProjects.find(p => p.ID === projectId);
    if (proj && proj.PaintNotes === notes && silent) { notesEl.dataset.saved = notes; return; }

    const result = await postJson(`/api/projects/${projectId}/notes`, { paint_notes: notes });
    if (result.status === 'error') return showToast(result.message || tr('err.save_failed'), 'error');
    if (proj) proj.PaintNotes = notes;
    if (notesEl.dataset.projectId === projectId) notesEl.dataset.saved = notes;
    renderProjects();
    if (!silent) showToast(tr('toast.recipe_saved'), "success");
}

// Pipeline template: step 1 picks the model type, step 2 sets step deadlines (steps can be skipped or spread evenly).
let templateKind = null;

function showTemplateStep(step) {
    document.getElementById('templateStepKind').classList.toggle('hidden', step !== 'kind');
    document.getElementById('templateStepDates').classList.toggle('hidden', step !== 'dates');
}

// openTemplateModal lives in static/templates.js (the template list comes from the settings)

function backToTemplateKind() { showTemplateStep('kind'); }

function dateToIso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function chooseTemplate(kind) {
    const project = currentProjects.find(p => p.ID === currentOpenProjectId);
    if (!project) return;
    // templateInfo was loaded when the window opened (openTemplateModal): templates and the steps the project already has
    const info = templateInfo || {};
    const tpl = (info.templates || []).find(t => t.id === kind);
    if (!tpl) return showToast(tr('err.unknown_template'));
    const names = tpl.steps;
    const existing = new Set((info.existing && info.existing[kind]) || []);
    templateKind = kind;
    const est = project.Estimate || '';
    document.getElementById('templateProjectDeadline').textContent =
        `${tr('template.project_deadline')}: ${est ? formatIsoDate(est) : tr('deadline.none')}`;
    document.getElementById('templateStepsList').innerHTML = names.map((name, i) => existing.has(i) ? `
        <div class="flex items-center gap-2 p-2 bg-slate-900 rounded-lg border border-slate-700 opacity-50" data-step="${i}" data-exists="1">
            <input type="checkbox" disabled class="tpl-on w-4 h-4 accent-indigo-500 shrink-0">
            <span class="flex-1 min-w-0 text-sm text-slate-400 truncate" title="${esc(name)}">${i + 1}. ${esc(name)}</span>
            <span class="shrink-0 text-xs text-emerald-400">✓ ${tr('template.step_exists')}</span>
        </div>` : `
        <div class="flex items-center gap-2 p-2 bg-slate-900 rounded-lg border border-slate-700" data-step="${i}">
            <input type="checkbox" checked class="tpl-on w-4 h-4 accent-indigo-500 cursor-pointer shrink-0">
            <span class="flex-1 min-w-0 text-sm text-slate-200 truncate" title="${esc(name)}">${i + 1}. ${esc(name)}</span>
            <input type="date" value="${esc(est)}" class="tpl-date bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-white outline-none [color-scheme:dark]">
        </div>`).join('');
    const allExist = existing.size >= names.length;
    document.getElementById('templateAllExist').classList.toggle('hidden', !allExist);
    document.getElementById('templateCreateBtn').disabled = allExist;
    document.getElementById('templateCreateBtn').classList.toggle('opacity-50', allExist);
    document.getElementById('templateSpreadBtn').classList.toggle('hidden', !est || allExist);
    showTemplateStep('dates');
}

// Evenly from today to the project deadline over the enabled steps; a deadline in the past goes to every step
function spreadTemplateDates() {
    const project = currentProjects.find(p => p.ID === currentOpenProjectId);
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec((project && project.Estimate) || '');
    if (!m) return;
    const end = new Date(+m[1], +m[2] - 1, +m[3]);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const rows = [...document.querySelectorAll('#templateStepsList [data-step]')]
        .filter(r => !r.dataset.exists && r.querySelector('.tpl-on').checked);
    const span = Math.max(0, Math.round((end - today) / 86400000));
    rows.forEach((row, i) => {
        const d = new Date(today);
        d.setDate(d.getDate() + (span ? Math.round(span * (i + 1) / rows.length) : 0));
        row.querySelector('.tpl-date').value = dateToIso(span ? d : end);
    });
}

async function applyTemplateToProject() {
    if (!currentOpenProjectId || !templateKind) return;
    const steps = [...document.querySelectorAll('#templateStepsList [data-step]')]
        .filter(r => !r.dataset.exists && r.querySelector('.tpl-on').checked)
        .map(r => ({ index: +r.dataset.step, deadline: r.querySelector('.tpl-date').value || null }));
    if (!steps.length) return showToast(tr('template.none_selected'));
    const res = await fetch(`/api/projects/${currentOpenProjectId}/template`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ kind: templateKind, steps })
    });
    const data = await res.json().catch(() => ({ status: 'error' }));
    if (data.status !== 'ok') return showToast(data.message || tr('err.save_failed'));
    closeModal('templateModal');
    showToast(data.skipped ? tr('toast.pipeline_added_skipped', { n: data.added, m: data.skipped }) : tr('toast.pipeline_added'), "success");
    loadData();
}

async function updateProjectStatus(id, status) {
    const p = currentProjects.find(x => x.ID === id);
    if (isDoneStatus(status) && !(p && isDoneStatus(p.Status))) {
        // Finishing closes every open task: one slip in the status list on a phone must not do that silently
        const open = currentTasks.filter(t => t['Project ID'] === id && !isDoneStatus(t.Status)).length;
        if (open > 0) {
            const ok = await confirmDialog({
                title: tr('project.finish_title'),
                message: tr('confirm.finish_open_tasks', { name: p ? p.Name : '', n: open }),
                okText: tr('project.finish_ok'), danger: false, icon: '🏆',
            });
            if (!ok) { renderProjects(); return; }     // puts the old value back into the status list
        }
    }
    if (p) p.Status = status;
    if (isDoneStatus(status)) {
        currentTasks.forEach(t => { if (t['Project ID'] === id && !isDoneStatus(t.Status)) t.Status = 'finished'; });
        showToast(tr('toast.project_to_hall'), "success");
    }
    renderProjects(); renderTasks(); renderAllTasks();
    sendAction({ action: 'update_project_status', id: id, status: status });   // reopening: the server brings the tasks back
}

function updateProjectStage(id, stage) {
    const p = currentProjects.find(x => x.ID === id);
    if(p) p.Stage = stage;
    renderProjects();
    sendAction({ action: 'update_project_stage', id: id, stage: stage });
}

function updateTaskStatus(id, status) {
    const t = currentTasks.find(x => x.ID === id);
    if(t) t.Status = status;
    renderProjects(); renderTasks(); renderAllTasks();
    if (currentOpenProjectId) openProjectDetails(currentOpenProjectId);
    sendAction({ action: 'update_task_status', id: id, status: status });
}

function updateProjectSelect() {
    const select = document.getElementById('tProjectId');
    // Rebuilding the options resets the selection to the first project; keep the chosen one
    // (loadData runs on window focus, e.g. after a native date picker, while the modal is open)
    const prev = select.value;
    const hadOptions = select.options.length > 0;
    const activeProjects = currentProjects.filter(p => !isDoneStatus(p.Status));
    // the empty value is a general task (no project); the first project stays the default choice
    select.innerHTML = activeProjects.map(p => `<option value="${p.ID}">${esc(p.Name)}</option>`).join('')
        + `<option value="">${esc(tr('general.option'))}</option>`;
    if (hadOptions && (prev === '' || activeProjects.some(p => p.ID === prev))) select.value = prev;
}

// The "new task" dialog; projectId: a project to preselect, '' for a general task, undefined to keep the choice
function openTaskModal(projectId) {
    if (projectId !== undefined) document.getElementById('tProjectId').value = projectId;
    openModal('taskModal');
}

async function createProject() {
    const body = { name: document.getElementById('pName').value.trim(), priority: document.getElementById('pPriority').value, estimate: document.getElementById('pEstimate').value || null, token_reward: Number(document.getElementById('pToken').value) || 0 };
    if(!body.name) return showToast(tr('project.err_name_empty'));
    if (!Number.isInteger(body.token_reward) || body.token_reward < 0) return showToast(tr('project.err_reward_whole'));

    const result = await postJson('/api/projects', body);
    
    if(result.status === "error") {
        showToast(result.message || tr('err.save_failed'), "error");
        return;
    }
    closeModal('projectModal');
    showToast(tr('toast.project_created'), "success");
    loadData();
}

function openEditModal(id) {
    const p = currentProjects.find(x => x.ID === id);
    if(!p) return;
    document.getElementById('epId').value = p.ID; document.getElementById('epName').value = p.Name;
    document.getElementById('epPriority').value = p.Priority; document.getElementById('epEstimate').value = p.Estimate || '';
    // Name and priority are always editable; the deadline in DEV MODE only.
    document.getElementById('epDevFields').classList.toggle('hidden', !isDevMode);
    openModal('editProjectModal');
}

async function saveProjectEdit() {
    const id = document.getElementById('epId').value;
    const name = document.getElementById('epName').value.trim();
    if (!name) return showToast(tr('project.err_name_empty'));

    let url, body;
    if (isDevMode) {
        url = `/api/projects/${id}/edit`;
        body = { name: name, priority: document.getElementById('epPriority').value, estimate: document.getElementById('epEstimate').value || null };
    } else {
        url = `/api/projects/${id}/rename`;
        body = { name: name, priority: document.getElementById('epPriority').value };
    }
    const result = await postJson(url, body);
    if(result.status === "error") return showToast(result.message || tr('err.save_failed'), "error");
    closeModal('editProjectModal');
    showToast(tr('toast.project_updated'), "success");
    loadData();
}

function openEditTaskModal(id) {
    const t = currentTasks.find(x => x.ID === id);
    if(!t) return;
    document.getElementById('etId').value = t.ID; document.getElementById('etName').value = t.Task;
    document.getElementById('etDeadline').value = t.Deadline || '';
    document.getElementById('etSpent').value = t.Spent ? minutesToInput(t.Spent) : '';
    openModal('editTaskModal');
}

async function saveTaskEdit() {
    const id = document.getElementById('etId').value;
    const body = {
        task_name: document.getElementById('etName').value.trim(),
        deadline: document.getElementById('etDeadline').value || null,
        spent: document.getElementById('etSpent').value.trim(),      // "" clears the time
    };
    if(!body.task_name) return showToast(tr('task.err_empty'));
    const result = await postJson(`/api/tasks/${id}/edit`, body);
    if (result.status === 'error') return showToast(result.message || tr('err.save_failed'), 'error');
    closeModal('editTaskModal');
    showToast(tr('toast.task_updated'), "success");
    loadData();
}

async function createTask() {
    const projectId = document.getElementById('tProjectId').value;
    const taskName = document.getElementById('tName').value.trim();
    let deadline = document.getElementById('tDeadline').value;
    if(!taskName) return showToast(tr('task.err_empty'));
    // projectId '' = a general task: no project, the deadline is optional
    const proj = projectId ? currentProjects.find(p => p.ID === projectId) : null;
    if (projectId && !proj) return showToast(tr('err.project_not_found'));
    if (!deadline) {
        if (proj && proj.Status !== 'planned') return showToast(tr('project.err_deadline_required'));
        if (proj && proj.Estimate) deadline = proj.Estimate;
    }
    const body = { project_id: projectId || null, task_name: taskName, deadline: deadline || null };
    const result = await postJson('/api/tasks', body);
    if (result.status === 'error') return showToast(result.message || tr('err.save_failed'), 'error');
    closeModal('taskModal');
    showToast(tr('toast.task_added'), "success");
    loadData();
}

function openTaskModalForProject() {
    if (!currentOpenProjectId) return;
    document.getElementById('tProjectId').value = currentOpenProjectId;
    openModal('taskModal');
}

async function deleteProject(id) {
    const p = currentProjects.find(x => x.ID === id);
    const hint = deleteKeepsBalance
        ? tr('confirm.balance_unchanged')
        : tr('confirm.cost_refunded');
    const ok = await confirmDialog({
        title: tr('project.delete_title'),
        message: `${p ? tr('confirm.delete_project_named', { name: p.Name }) : tr('confirm.delete_project_text')}\n${hint}`
    });
    if (!ok) return;
    await postOk(`/api/projects/${id}`, undefined, 'DELETE');
    loadData();
}
async function deleteTask(id) {
    if (!(await confirmDialog({ title: tr('task.delete_title'), message: tr('confirm.delete_task_text') }))) return;
    await postOk(`/api/tasks/${id}`, undefined, 'DELETE');
    loadData();
}
