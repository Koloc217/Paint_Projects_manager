/* Hall of fame and analytics charts. */

// Chart palette: read from the CSS tokens of the active theme (--chart-* in themes.css), so a new theme
// styles the charts with its own CSS block only, without JS changes.
function chartTheme() {
    const css = getComputedStyle(document.body);
    const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
    const stageDefaults = ['#475569', '#6366f1', '#f59e0b', '#06b6d4', '#a855f7', '#10b981', '#f472b6'];
    return {
        font: v('--chart-font', 'sans-serif'),
        text: v('--chart-text', '#cbd5e1'),
        muted: v('--chart-muted', '#94a3b8'),
        grid: v('--chart-grid', '#334155'),
        stages: stageDefaults.map((d, i) => v(`--chart-stage-${i + 1}`, d)),
        series: [v('--chart-series-1', '#6366f1'), v('--chart-series-2', '#10b981')],
        border: v('--chart-border', 'transparent'),
        borderWidth: parseFloat(v('--chart-border-width', '0')) || 0,
        radius: parseFloat(v('--chart-radius', '6')) || 0,
    };
}

function renderAnalytics() {
    const doneProjs = currentProjects.filter(p => isDoneStatus(p.Status));
    const activeProjs = currentProjects.filter(p => !isDoneStatus(p.Status));
    const doneTasks = currentTasks.filter(t => isDoneStatus(t.Status));
    const rate = currentTasks.length > 0 ? Math.round((doneTasks.length / currentTasks.length) * 100) : 0;

    document.getElementById('statDoneProjects').innerText = doneProjs.length;
    document.getElementById('statDoneTasks').innerText = doneTasks.length;
    document.getElementById('statEarnedTokens').innerText = `+${earnedTokensTotal}`;
    document.getElementById('statTaskRate').innerText = `${rate}%`;

    const stages = ['none', 'printing', 'sanding', 'assembly', 'priming', 'painting', 'finishing'];
    const stageCounts = stages.map(st => activeProjs.filter(p => (p.Stage || 'none') === st).length);

    const ct = chartTheme();
    Chart.defaults.font.family = ct.font;
    Chart.defaults.color = ct.text;

    const ctxStage = document.getElementById('stageChart').getContext('2d');
    if (stageChartInstance) stageChartInstance.destroy();
    stageChartInstance = new Chart(ctxStage, {
        type: 'bar',
        data: {
            labels: ['stage.none', 'stage.printing', 'stage.sanding', 'stage.assembly', 'stage.priming', 'stage.painting', 'stage.finishing'].map(s => tr(s)),
            datasets: [{
                label: tr('analytics.active_projects'),
                data: stageCounts,
                backgroundColor: ct.stages,
                borderColor: ct.border,
                borderWidth: ct.borderWidth,
                borderRadius: ct.radius
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { stepSize: 1, color: ct.muted }, grid: { color: ct.grid } },
                x: { ticks: { color: ct.text }, grid: { display: false } }
            }
        }
    });

    const months = [];
    const now = new Date();
    for (let i = 5; i >= 0; i--) {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
        // "Oct 26" reads like a day of the month, so the year goes full and on its own line,
        // only on the first bar and where a new year starts
        const lang = window.APP_LANG || 'ru-RU';
        const month = d.toLocaleDateString(lang, { month: 'short' });
        const label = (i === 5 || d.getMonth() === 0) ? [month, String(d.getFullYear())] : month;
        months.push({ ym, label, tasks: 0, projs: 0 });
    }

    doneTasks.forEach(t => {
        if (t.CompletedAt) {
            const ym = t.CompletedAt.slice(0, 7);
            const m = months.find(x => x.ym === ym);
            if (m) m.tasks++;
        }
    });
    doneProjs.forEach(p => {
        if (p.CompletedAt) {
            const ym = p.CompletedAt.slice(0, 7);
            const m = months.find(x => x.ym === ym);
            if (m) m.projs++;
        }
    });

    const ctxVel = document.getElementById('velocityChart').getContext('2d');
    if (velocityChartInstance) velocityChartInstance.destroy();
    velocityChartInstance = new Chart(ctxVel, {
        type: 'bar',
        data: {
            labels: months.map(m => m.label),
            datasets: [
                {
                    label: tr('stats.tasks_closed'),
                    data: months.map(m => m.tasks),
                    backgroundColor: ct.series[0],
                    borderColor: ct.border,
                    borderWidth: ct.borderWidth,
                    borderRadius: ct.radius
                },
                {
                    label: tr('stats.projects_completed'),
                    data: months.map(m => m.projs),
                    backgroundColor: ct.series[1],
                    borderColor: ct.border,
                    borderWidth: ct.borderWidth,
                    borderRadius: ct.radius
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: ct.text } } },
            scales: {
                y: { beginAtZero: true, ticks: { stepSize: 1, color: ct.muted }, grid: { color: ct.grid } },
                x: { ticks: { color: ct.text }, grid: { display: false } }
            }
        }
    });

    renderTimeChart(ct);
    renderHall();
}

// Time spent by stage over all projects (in the trash: not counted). Hours with one decimal; the stage of a task's
// time is the project stage when the time was given (see spent.py), time outside tasks is its own bar.
function renderTimeChart(ct) {
    const keys = ['printing', 'sanding', 'assembly', 'priming', 'painting', 'finishing', 'none', '_extra', '_general'];
    const totals = Object.fromEntries(keys.map(k => [k, 0]));
    let withTime = 0;
    currentTasks.forEach(t => {
        if (!t.Spent) return;
        withTime++;
        // general tasks (no project) have no stage: their own bar
        const k = !t['Project ID'] ? '_general'
            : (PROGRESS_STAGE_KEYS[t.SpentStage] && t.SpentStage !== 'none' ? t.SpentStage : 'none');
        totals[k] += t.Spent;
    });
    currentProjects.forEach(p => { totals._extra += p.ExtraSpent || 0; });
    const total = keys.reduce((a, k) => a + totals[k], 0);

    if (timeChartInstance) { timeChartInstance.destroy(); timeChartInstance = null; }
    document.getElementById('timeChartBox').classList.toggle('hidden', !total);
    document.getElementById('timeChartEmpty').classList.toggle('hidden', !!total);
    document.getElementById('timeChartNote').innerText = total
        ? `${tr('time.spent')}: ${tr('time.approx', { time: formatApproxMinutes(total) })} · ${tr('time.coverage', { n: withTime, total: currentTasks.length })}`
        : '';
    if (!total) return;

    const shown = keys.filter(k => totals[k] > 0);
    // colors follow the stage chart: ct.stages[0] is "no stage", then the stages in order
    const stageIndex = { none: 0, printing: 1, sanding: 2, assembly: 3, priming: 4, painting: 5, finishing: 6 };
    timeChartInstance = new Chart(document.getElementById('timeChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: shown.map(k => k === '_extra' ? tr('time.extra') : k === '_general' ? tr('general.time')
                : (k === 'none' ? tr('time.no_stage') : tr(PROGRESS_STAGE_KEYS[k]))),
            datasets: [{
                label: tr('time.spent'),
                data: shown.map(k => Math.round(totals[k] / 6) / 10),          // hours, one decimal
                backgroundColor: shown.map(k => k === '_extra' ? ct.series[0]
                    : k === '_general' ? (ct.series[1] || ct.series[0]) : ct.stages[stageIndex[k]]),
                borderColor: ct.border,
                borderWidth: ct.borderWidth,
                borderRadius: ct.radius,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (c) => formatMinutes(totals[shown[c.dataIndex]]) } },
            },
            scales: {
                x: { beginAtZero: true, ticks: { color: ct.muted, callback: (v) => tr('time.h', { h: v }) }, grid: { color: ct.grid } },
                y: { ticks: { color: ct.text }, grid: { display: false } }
            }
        }
    });
}

// Hall of Fame: finished projects with search (separate from the charts, so typing does not redraw them)
function hallSpent(p) {
    const s = projectSpent(p);
    return s.total ? `<span title="${esc(tr('time.coverage', { n: s.withTime, total: s.tasks }))}">⏱ ${tr('time.approx', { time: formatApproxMinutes(s.total) })}</span>` : '';
}

function renderHall() {
    const doneProjs = currentProjects.filter(p => isDoneStatus(p.Status));
    const hallSearching = searchQuery.hall.trim() !== '';
    const hallProjs = doneProjs.filter(p => projectMatches(p, searchQuery.hall));
    document.getElementById('archiveCount').innerText = hallSearching ? `${hallProjs.length} / ${doneProjs.length}` : doneProjs.length;
    const hallContainer = document.getElementById('hallOfFameContainer');
    if (doneProjs.length === 0 || (hallSearching && hallProjs.length === 0)) {
        hallContainer.innerHTML = `<div class="col-span-full text-center py-8 text-slate-500 bg-slate-800/40 rounded-2xl border border-slate-800">${tr(doneProjs.length === 0 ? 'hall.empty' : 'search.nothing')}</div>`;
        return;
    }

    patchList(hallContainer, hallProjs, p => p.ID, p => {
        const pTasks = projectTasks(p.ID);
        const notesHtml = p.PaintNotes && p.PaintNotes.trim()
            ? `<div class="mt-3 p-2.5 bg-slate-900/90 border border-amber-500/20 rounded-lg text-xs text-slate-300 font-mono whitespace-pre-wrap"><span class="text-amber-400 font-bold block mb-1">📝 ${tr('recipe.title')}:</span>${esc(p.PaintNotes)}</div>`
            : `<div class="mt-3 text-xs text-slate-500 italic">${tr('recipe.empty')}</div>`;

        return `
        <div class="bg-slate-800/90 rounded-2xl p-5 border border-emerald-500/30 shadow-lg flex flex-col justify-between">
            <div>
                <div class="flex justify-between items-start gap-2 mb-2">
                    <h3 class="text-base font-bold text-white">${esc(p.Name)}</h3>
                </div>
                <div class="text-xs text-slate-400 flex items-center gap-3 mb-2">
                    <span>✅ ${tr('stats.tasks')}: ${pTasks.length}</span>
                    ${p.CompletedAt ? `<span>📅 ${tr('project.finish')}: ${formatIsoDate(p.CompletedAt)}</span>` : ''}
                    ${hallSpent(p)}
                </div>
                ${notesHtml}
            </div>
            <div class="mt-4 pt-3 border-t border-slate-700/50 flex justify-between items-center">
                <button onclick="openProjectDetails('${p.ID}')" class="text-xs text-indigo-400 hover:text-indigo-300">${tr('project.open_card')}</button>
                <button onclick="updateProjectStatus('${p.ID}', 'in_work')" class="text-xs px-2.5 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors">↩️ ${tr('project.reopen')}</button>
            </div>
        </div>`;
    });
}

// Sub-tabs of the compact card. Opening another project switches back to "Tasks";
// redrawing the same project (after a save) keeps the current sub-tab.
