/**
 * Browser Agent Analyzer - Main Application
 *
 * Visualises browser-agent experiment runs with:
 *   - Step-first view   (sequential steps with memory / actions / screenshots)
 *   - Screenshot-first   (carousel with step info)
 *   - Compare mode        (side-by-side runs for same task)
 *   - Statistics           (Chart.js dashboards)
 *   - GIF playback         (agent_history.gif)
 *   - API-log modal        (replaces old conversation modal)
 */

class BrowserAgentAnalyzer {
    constructor() {
        this.currentMode = 'run'; // 'run' | 'experiment-set'
        this.currentRun = null;
        this.currentTask = null;
        this.currentTaskData = null;
        this.currentView = 'step-first';
        this.judgeMode = 'llm_judge';
        this.screenshotIndex = 0;
        this.screenshotSteps = [];
        this.charts = {};
        // Experiment set state
        this.currentExperimentSet = null;
        this.currentExpSetTask = null;
        // Tree collapse state: agents expanded by default, models collapsed
        this.collapsedAgents = new Set();
        this.expandedModels = new Set();
    }

    // ==================================================================
    // Initialisation
    // ==================================================================

    async init() {
        this.initTheme();
        this.initSidebarResize();
        try {
            await dataLoader.loadIndex();
            this.judgeMode = dataLoader.getDefaultJudgeMode();
            this.populateJudgeModeSelect();
            this.populateRunsList();
            this.setupEventListeners();
            this.showWelcomeScreen();

            // Start polling for hot-reload (works when using serve.py --watch)
            dataLoader.startWatching(() => this.onIndexReloaded());

            console.log('Browser Agent Analyzer initialized');
        } catch (error) {
            console.error('Failed to initialize:', error);
            this.showError('Failed to load experiment data. Make sure experiments.json exists.\n' + error.message);
        }
    }

    // ==================================================================
    // Event listeners
    // ==================================================================

    setupEventListeners() {
        document.getElementById('judge-mode-select').addEventListener('change', (e) => {
            this.setJudgeMode(e.target.value);
        });

        // Home
        document.getElementById('btn-home').addEventListener('click', () => this.goHome());

        // View mode
        document.getElementById('btn-step-first').addEventListener('click', () => this.switchView('step-first'));
        document.getElementById('btn-screenshot-first').addEventListener('click', () => this.switchView('screenshot-first'));

        // Stats / Refresh
        document.getElementById('btn-stats').addEventListener('click', () => this.toggleStatsView());
        document.getElementById('btn-refresh').addEventListener('click', () => this.refreshIndex());
        document.getElementById('btn-theme').addEventListener('click', () => this.toggleTheme());

        // Filters
        document.getElementById('task-search').addEventListener('input', (e) => this.filterTasks(e.target.value));


        // Screenshot nav
        document.getElementById('prev-screenshot').addEventListener('click', () => this.navigateScreenshot(-1));
        document.getElementById('next-screenshot').addEventListener('click', () => this.navigateScreenshot(1));

        // Lightbox
        document.getElementById('lightbox').addEventListener('click', () => this.closeLightbox());
        document.getElementById('lightbox-img').addEventListener('click', (e) => e.stopPropagation());

        // GIF lightbox
        document.getElementById('gif-lightbox').addEventListener('click', () => this.closeGifLightbox());
        document.getElementById('gif-lightbox-img').addEventListener('click', (e) => e.stopPropagation());
        document.getElementById('btn-play-gif')?.addEventListener('click', (e) => { e.stopPropagation(); this.openGifLightbox(); });
        document.getElementById('gif-preview')?.addEventListener('click', () => this.openGifLightbox());

        // Modal
        document.getElementById('close-modal').addEventListener('click', () => this.closeModal());
        document.getElementById('api-log-modal').addEventListener('click', (e) => {
            if (e.target === document.getElementById('api-log-modal')) this.closeModal();
        });

        // Keyboard
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const modal = document.getElementById('api-log-modal');
                const gifLb = document.getElementById('gif-lightbox');
                const lb = document.getElementById('lightbox');
                const statsView = document.getElementById('stats-view');
                if (!modal.classList.contains('hidden')) this.closeModal();
                else if (!gifLb.classList.contains('hidden')) this.closeGifLightbox();
                else if (!lb.classList.contains('hidden')) this.closeLightbox();
                else if (!statsView.classList.contains('hidden')) this.closeStatsView();
            }
            if (this.currentView === 'screenshot-first'
                && document.getElementById('stats-view').classList.contains('hidden')) {
                if (e.key === 'ArrowLeft') this.navigateScreenshot(-1);
                if (e.key === 'ArrowRight') this.navigateScreenshot(1);
            }
        });
    }

    // ==================================================================
    // Judge mode
    // ==================================================================

    populateJudgeModeSelect() {
        const select = document.getElementById('judge-mode-select');
        const modes = dataLoader.getJudgeModes();
        select.innerHTML = modes.map(mode => `
            <option value="${mode.value}">${this.escapeHtml(mode.label)}</option>
        `).join('');
        select.value = this.judgeMode;
    }

    setJudgeMode(mode) {
        this.judgeMode = mode;
        this.populateRunsList();

        if (this.currentRun) {
            document.querySelector(`.tree-run[data-uuid="${CSS.escape(this.currentRun)}"]`)?.classList.add('active');
            this.populateTasksList(document.getElementById('task-search').value);
        }
        if (this.currentTask) {
            document.querySelector(`.task-item[data-task-id="${this.currentTask}"]`)?.classList.add('active');
        }
        if (!document.getElementById('welcome-screen').classList.contains('hidden')) {
            this.showWelcomeScreen();
        }
        if (!document.getElementById('stats-view').classList.contains('hidden')) {
            this.renderCharts();
        }
        if (this.currentTaskData) {
            this.updateTaskHeader();
            this.showEvalPanel();
        }
    }

    // ==================================================================
    // Sidebar – Runs
    // ==================================================================

    populateRunsList() {
        const container = document.getElementById('runs-list');
        const grouped = dataLoader.getRunsGrouped(this.judgeMode);

        const html = grouped.map(bench => {
            const bKey = `${bench.benchmark}|||${bench.split}`;
            const agentsHtml = bench.agents.map(agent => {
                const aKey = `${bKey}|||${agent.name}`;
                const isAgentCollapsed = this.collapsedAgents.has(aKey);
                const modelsHtml = agent.models.map(model => {
                    const mKey = `${aKey}|||${model.name}`;
                    const isModelExpanded = this.expandedModels.has(mKey);
                    const runsHtml = model.runs.map(run => `
                        <div class="tree-run" data-uuid="${this.escapeHtml(run.uuid)}">
                            <span class="tree-run-ts">${this.escapeHtml(run.uuid.replace(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/, '$1-$2-$3 $4:$5'))}</span>
                            <span class="tree-run-stats">${run.stats.successRate.toFixed(1)}% (${run.stats.successCount}/${run.stats.evaluatedTasks})</span>
                        </div>
                    `).join('');
                    return `
                        <div class="tree-model ${isModelExpanded ? 'expanded' : 'collapsed'}" data-model-key="${this.escapeHtml(mKey)}">
                            <div class="tree-model-header">
                                <span class="tree-toggle">${isModelExpanded ? '▾' : '▸'}</span>
                                <span class="tree-model-name">${this.escapeHtml(model.name)}</span>
                                <span class="tree-node-stats">${model.stats.avgSuccessRate.toFixed(1)}% avg · ${model.stats.totalRuns} run${model.stats.totalRuns !== 1 ? 's' : ''}</span>
                            </div>
                            <div class="tree-model-children" style="display:${isModelExpanded ? 'block' : 'none'}">
                                ${runsHtml}
                            </div>
                        </div>
                    `;
                }).join('');
                return `
                    <div class="tree-agent ${isAgentCollapsed ? 'collapsed' : 'expanded'}" data-agent-key="${this.escapeHtml(aKey)}">
                        <div class="tree-agent-header">
                            <span class="tree-toggle">${isAgentCollapsed ? '▸' : '▾'}</span>
                            <span class="tree-agent-name">${this.escapeHtml(agent.name)}</span>
                            <span class="tree-node-stats">${agent.stats.avgSuccessRate.toFixed(1)}% avg · ${agent.stats.totalRuns} run${agent.stats.totalRuns !== 1 ? 's' : ''}</span>
                        </div>
                        <div class="tree-agent-children" style="display:${isAgentCollapsed ? 'none' : 'block'}">
                            ${modelsHtml}
                        </div>
                    </div>
                `;
            }).join('');
            return `
                <div class="tree-benchmark">
                    <div class="tree-benchmark-header">${this.escapeHtml(bench.benchmark)} / ${this.escapeHtml(bench.split)}<span class="tree-node-stats">${bench.stats.avgSuccessRate.toFixed(1)}% avg · ${bench.stats.totalRuns} run${bench.stats.totalRuns !== 1 ? 's' : ''}</span></div>
                    <div class="tree-benchmark-children">${agentsHtml}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;

        // Agent toggle
        container.querySelectorAll('.tree-agent-header').forEach(header => {
            header.addEventListener('click', () => {
                const agentEl = header.closest('.tree-agent');
                const aKey = agentEl.dataset.agentKey;
                const children = agentEl.querySelector('.tree-agent-children');
                const toggle = header.querySelector('.tree-toggle');
                if (this.collapsedAgents.has(aKey)) {
                    this.collapsedAgents.delete(aKey);
                    children.style.display = 'block';
                    agentEl.classList.replace('collapsed', 'expanded');
                    toggle.textContent = '▾';
                } else {
                    this.collapsedAgents.add(aKey);
                    children.style.display = 'none';
                    agentEl.classList.replace('expanded', 'collapsed');
                    toggle.textContent = '▸';
                }
            });
        });

        // Model toggle
        container.querySelectorAll('.tree-model-header').forEach(header => {
            header.addEventListener('click', () => {
                const modelEl = header.closest('.tree-model');
                const mKey = modelEl.dataset.modelKey;
                const children = modelEl.querySelector('.tree-model-children');
                const toggle = header.querySelector('.tree-toggle');
                if (this.expandedModels.has(mKey)) {
                    this.expandedModels.delete(mKey);
                    children.style.display = 'none';
                    modelEl.classList.replace('expanded', 'collapsed');
                    toggle.textContent = '▸';
                } else {
                    this.expandedModels.add(mKey);
                    children.style.display = 'block';
                    modelEl.classList.replace('collapsed', 'expanded');
                    toggle.textContent = '▾';
                }
            });
        });

        // Run click
        container.querySelectorAll('.tree-run').forEach(item => {
            item.addEventListener('click', () => {
                container.querySelectorAll('.tree-run').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                this.selectRun(item.dataset.uuid);
            });
        });

        this.populateExperimentSetsList();
    }

    populateExperimentSetsList() {
        const section = document.getElementById('exp-sets-section');
        const container = document.getElementById('exp-sets-list');
        const sets = dataLoader.getExperimentSets();

        if (sets.length === 0) {
            section.style.display = 'none';
            return;
        }

        section.style.display = '';
        container.innerHTML = sets.map(s => {
            const methodCount = (s.methods || []).length;
            return `
                <div class="filter-item exp-set-item" data-set-id="${this.escapeHtml(s.id)}">
                    <span class="exp-set-name">${this.escapeHtml(s.display_name)}</span>
                    <span class="exp-set-meta">${this.escapeHtml(s.benchmark)} · ${s.task_count} tasks · ${methodCount} method${methodCount !== 1 ? 's' : ''}</span>
                </div>
            `;
        }).join('');

        container.querySelectorAll('.exp-set-item').forEach(item => {
            item.addEventListener('click', () => {
                // Deselect all run items
                document.querySelectorAll('.tree-run').forEach(i => i.classList.remove('active'));
                container.querySelectorAll('.exp-set-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                this.selectExperimentSet(item.dataset.setId);
            });
        });
    }

    // ==================================================================
    // Sidebar – Tasks
    // ==================================================================

    populateTasksList(filter = '') {
        const container = document.getElementById('tasks-list');

        let taskIds;
        if (this.currentMode === 'experiment-set' && this.currentExperimentSet) {
            const set = dataLoader.getExperimentSetById(this.currentExperimentSet);
            taskIds = set ? [...(set.task_ids || [])] : [];
        } else if (this.currentRun) {
            taskIds = dataLoader.getTaskIdsForRun(this.currentRun);
        } else {
            taskIds = dataLoader.getAllTaskIds();
        }

        if (filter) taskIds = taskIds.filter(id => id.includes(filter));
        taskIds = taskIds.sort((a, b) => parseInt(a) - parseInt(b));

        if (this.currentMode === 'experiment-set') {
            const set = dataLoader.getExperimentSetById(this.currentExperimentSet);
            container.innerHTML = taskIds.map(taskId => {
                const perTask = set?.aggregates?.per_task?.[taskId];
                const hasFlip = perTask?.has_verdict_flip;
                const scoreText = perTask?.score_mean != null
                    ? `μ=${perTask.score_mean.toFixed(2)}`
                    : perTask?.verdict_sample_count > 0
                        ? `n=${perTask.verdict_sample_count}`
                        : '';
                return `
                    <div class="filter-item task-item" data-task-id="${taskId}">
                        <span class="task-id">Task ${taskId}</span>
                        ${scoreText ? `<span class="task-score">${scoreText}${hasFlip ? ' ⚡' : ''}</span>` : ''}
                    </div>
                `;
            }).join('');
        } else {
            container.innerHTML = taskIds.map(taskId => {
                const scoreInfo = this.getTaskScoreInfo(taskId);
                return `
                    <div class="filter-item task-item" data-task-id="${taskId}">
                        <span class="task-id">Task ${taskId}</span>
                        ${scoreInfo ? `<span class="task-score ${scoreInfo.cls}">${scoreInfo.text}</span>` : ''}
                    </div>
                `;
            }).join('');
        }

        container.querySelectorAll('.task-item').forEach(item => {
            item.addEventListener('click', () => {
                container.querySelectorAll('.task-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                this.selectTask(item.dataset.taskId);
            });
        });
    }

    // ==================================================================
    // Experiment Set mode
    // ==================================================================

    selectExperimentSet(setId) {
        this.currentMode = 'experiment-set';
        this.currentExperimentSet = setId;
        this.currentExpSetTask = null;

        // Hide run-mode panels
        document.getElementById('welcome-screen').classList.add('hidden');
        document.getElementById('task-header').classList.add('hidden');
        document.getElementById('replay-section').classList.add('hidden');
        document.getElementById('eval-panel').classList.add('hidden');
        document.getElementById('run-output-logs').classList.add('hidden');

        // Show tasks sidebar for experiment set tasks
        document.getElementById('sidebar-right').classList.remove('hidden');
        this.populateTasksList(document.getElementById('task-search').value);

        // Show experiment set view
        const view = document.getElementById('exp-set-view');
        view.classList.remove('hidden');
        document.getElementById('exp-set-task-comparison').classList.add('hidden');

        this.renderExperimentSetOverview();
    }

    renderExperimentSetOverview() {
        const set = dataLoader.getExperimentSetById(this.currentExperimentSet);
        if (!set) return;

        const methodCount = (set.methods || []).length;
        const totalRuns = (set.methods || []).reduce((s, m) => s + m.run_count, 0);
        const highVar = set.aggregates?.high_variance_tasks || [];
        const flipTasks = set.aggregates?.verdict_flip_tasks || [];
        const flipSet = new Set(flipTasks);
        const perTask = set.aggregates?.per_task || {};
        const consistentTasks = Object.keys(perTask)
            .filter(tid => (perTask[tid].verdict_sample_count || 0) >= 2 && !flipSet.has(tid))
            .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

        // Methods table rows
        const methodRows = (set.methods || []).map(m => {
            const stats = set.aggregates?.per_method?.[m.id] || {};
            const runBadges = [
                m.completed_run_count > 0 ? `<span class="run-status-badge completed">${m.completed_run_count} done</span>` : '',
                m.partial_run_count > 0   ? `<span class="run-status-badge partial">${m.partial_run_count} partial</span>` : '',
                m.failed_run_count > 0    ? `<span class="run-status-badge failed">${m.failed_run_count} failed</span>` : '',
            ].filter(Boolean).join('');

            const fmtNum = (v) => v != null ? v.toFixed(4) : '—';
            const fmtPct = (v) => v != null ? (v * 100).toFixed(1) + '%' : '—';

            return `
                <tr>
                    <td style="font-weight:600">${this.escapeHtml(m.display_name)}</td>
                    <td>${m.run_count} ${runBadges}</td>
                    <td>${stats.score_sample_count ?? '—'} / ${stats.verdict_sample_count ?? '—'}</td>
                    <td>${fmtNum(stats.score_mean)}</td>
                    <td>${fmtNum(stats.score_std)}</td>
                    <td>${fmtNum(stats.score_min)} – ${fmtNum(stats.score_max)}</td>
                    <td>${fmtPct(stats.pass_rate)}</td>
                </tr>
            `;
        }).join('');

        const highVarChips = highVar.length > 0
            ? highVar.map(tid => `<span class="exp-set-task-chip" data-task-id="${tid}">Task ${tid}</span>`).join('')
            : '<span style="color:var(--text-muted);font-size:13px">None (need ≥2 comparable samples)</span>';

        const flipChips = flipTasks.length > 0
            ? flipTasks.map(tid => `<span class="exp-set-task-chip flip" data-task-id="${tid}">Task ${tid}</span>`).join('')
            : '<span style="color:var(--text-muted);font-size:13px">None detected</span>';

        const consistentChips = consistentTasks.length > 0
            ? consistentTasks.map(tid => `<span class="exp-set-task-chip consistent" data-task-id="${tid}">Task ${tid}</span>`).join('')
            : '<span style="color:var(--text-muted);font-size:13px">None (need ≥2 verdict samples)</span>';

        document.getElementById('exp-set-overview').innerHTML = `
            <div class="exp-set-header">
                <div class="exp-set-title">${this.escapeHtml(set.display_name)}</div>
                <div class="exp-set-subtitle">
                    <span>Benchmark: ${this.escapeHtml(set.benchmark)}</span>
                    <span>ID: ${this.escapeHtml(set.id)}</span>
                </div>
            </div>

            <div class="exp-set-summary-cards">
                <div class="exp-set-summary-card">
                    <span class="card-value">${set.task_count}</span>
                    <span class="card-label">Tasks</span>
                </div>
                <div class="exp-set-summary-card">
                    <span class="card-value">${methodCount}</span>
                    <span class="card-label">Methods</span>
                </div>
                <div class="exp-set-summary-card">
                    <span class="card-value">${totalRuns}</span>
                    <span class="card-label">Runs</span>
                </div>
                <div class="exp-set-summary-card">
                    <span class="card-value">${flipTasks.length}</span>
                    <span class="card-label">Flip Tasks</span>
                </div>
                <div class="exp-set-summary-card">
                    <span class="card-value">${consistentTasks.length}</span>
                    <span class="card-label">Consistent Tasks</span>
                </div>
            </div>

            <div class="exp-set-section">
                <div class="exp-set-section-title">Methods Overview</div>
                ${methodCount === 0
                    ? '<p class="exp-set-empty">No eval data found yet.</p>'
                    : `<div class="task-comparison-scroll">
                        <table class="exp-set-methods-table">
                            <thead>
                                <tr>
                                    <th>Method</th>
                                    <th>Runs</th>
                                    <th>Samples</th>
                                    <th>Mean Score</th>
                                    <th>Std Dev</th>
                                    <th>Range</th>
                                    <th>Pass Rate</th>
                                </tr>
                            </thead>
                            <tbody>${methodRows}</tbody>
                        </table>
                    </div>`
                }
            </div>

            <div class="exp-set-section">
                <div class="exp-set-section-title">High Variance Tasks (top 10)</div>
                <div class="exp-set-task-chips" id="high-var-chips">${highVarChips}</div>
            </div>

            <div class="exp-set-section">
                <div class="exp-set-section-title">Verdict Flip Tasks</div>
                <div class="exp-set-task-chips" id="flip-chips">${flipChips}</div>
            </div>

            <div class="exp-set-section">
                <div class="exp-set-section-title">Verdict Consistent Tasks</div>
                <div class="exp-set-task-chips" id="consistent-chips">${consistentChips}</div>
            </div>
        `;

        // Bind task chip clicks
        document.getElementById('exp-set-overview').querySelectorAll('.exp-set-task-chip[data-task-id]').forEach(chip => {
            chip.addEventListener('click', () => {
                const tid = chip.dataset.taskId;
                // Highlight in sidebar
                document.querySelectorAll('.task-item').forEach(i => i.classList.remove('active'));
                document.querySelector(`.task-item[data-task-id="${tid}"]`)?.classList.add('active');
                this.selectTask(tid);
            });
        });
    }

    async renderTaskComparison(taskId) {
        const set = dataLoader.getExperimentSetById(this.currentExperimentSet);
        if (!set) return;

        const taskMeta = set.task_meta?.[taskId] || {};
        const perTask = set.aggregates?.per_task?.[taskId] || {};

        // ------------------------------------------------------------------
        // Load task data (result.json) for GIF + action history
        // ------------------------------------------------------------------
        const taskData = taskMeta.path ? await dataLoader.loadExpSetTaskData(taskMeta) : null;

        // ------------------------------------------------------------------
        // GIF banner (reuses same classes as run mode)
        // ------------------------------------------------------------------
        let gifBannerHtml = '';
        if (taskData?.has_gif) {
            const steps = taskData.metrics?.steps || taskData.action_history?.length || '?';
            const e2e = taskData.metrics?.end_to_end_ms ? (taskData.metrics.end_to_end_ms / 1000).toFixed(1) + 's' : '?';
            gifBannerHtml = `
                <div class="gif-banner" id="exp-gif-banner">
                    <div class="gif-preview" id="exp-gif-preview" title="Click to view full GIF">
                        <img src="${taskData.gif_path}" alt="Agent History GIF" class="exp-gif-thumb-img">
                    </div>
                    <div class="gif-info">
                        <span class="gif-label">Agent History</span>
                        <span class="gif-meta">${steps} steps | ${e2e} total</span>
                        <button class="gif-play-btn" id="exp-btn-play-gif">View Full GIF</button>
                    </div>
                </div>
            `;
        }

        // ------------------------------------------------------------------
        // Action history (reuses same classes as run mode step-first view)
        // ------------------------------------------------------------------
        let actionHistoryHtml = '';
        if (taskData?.action_history?.length > 0) {
            const screenshots = taskData.screenshots || {};
            const totalSteps = taskData.action_history.length;
            const screenshotCount = Object.keys(screenshots).length;
            const e2e = taskData.metrics?.end_to_end_ms ? (taskData.metrics.end_to_end_ms / 1000).toFixed(1) + 's' : null;
            const cost = taskData.metrics?.usage?.total_cost;

            const chips = [`${totalSteps} steps`];
            if (screenshotCount) chips.push(`${screenshotCount} screenshots`);
            if (e2e) chips.push(e2e);
            if (cost != null) chips.push(`$${cost.toFixed(4)}`);

            let stepsHtml = '';
            taskData.action_history.forEach((action, index) => {
                const stepNum = index + 1;
                const screenshot = screenshots[stepNum] || screenshots[String(stepNum)];
                const screenshotUrl = screenshot ? `${taskMeta.path}/trajectory/${screenshot}` : null;

                stepsHtml += `
                    <div class="step-card" data-step="${stepNum}">
                        <div class="step-header">
                            <span class="step-number">Step ${stepNum}</span>
                            <span class="step-action-summary">${this.escapeHtml(this.truncate(action, 120))}</span>
                        </div>
                        <div class="step-content">
                            <div class="step-main">
                                <div class="action-display">
                                    <pre>${this.escapeHtml(action)}</pre>
                                </div>
                            </div>
                            ${screenshotUrl ? `
                                <div class="step-screenshot">
                                    <img src="${screenshotUrl}" alt="Step ${stepNum}" class="screenshot-thumb" loading="lazy">
                                </div>
                            ` : ''}
                        </div>
                    </div>
                `;
            });

            actionHistoryHtml = `
                <div class="exp-set-section">
                    <div class="steps-card">
                        <div class="steps-card-header" id="exp-steps-card-toggle">
                            <div class="steps-card-title">
                                <span class="steps-card-label">Action History</span>
                                <span class="steps-card-chevron collapsed">&#9662;</span>
                            </div>
                            <div class="steps-card-meta">
                                ${chips.map(c => `<span class="steps-meta-chip">${this.escapeHtml(c)}</span>`).join('')}
                            </div>
                        </div>
                        <div class="steps-card-body collapsed" id="exp-steps-card-body">
                            ${stepsHtml}
                        </div>
                    </div>
                </div>
            `;
        }

        // ------------------------------------------------------------------
        // Judge results comparison table
        // ------------------------------------------------------------------
        let tableRows = '';
        for (const method of (set.methods || [])) {
            for (const run of (method.runs || [])) {
                const result = run.task_results?.[taskId];
                const statusBadge = run.status !== 'completed'
                    ? `<span class="run-status-${run.status}">(${run.status})</span>`
                    : '';

                let scoreCell = '—';
                let verdictCell = `<span class="verdict-badge none">—</span>`;
                let reasoningCell = '—';

                if (result) {
                    const score = result.score;
                    const verdict = result.verdict;
                    scoreCell = score != null
                        ? `<span class="comparison-score">${typeof score === 'number' ? score.toFixed(4) : score}</span>`
                        : '—';
                    if (verdict === 'pass' || verdict === 'fail') {
                        verdictCell = `<span class="verdict-badge ${verdict}">${verdict}</span>`;
                    } else if (verdict) {
                        verdictCell = `<span class="verdict-badge error">${this.escapeHtml(verdict)}</span>`;
                    }
                    const reasoning = result.reasoning || result.summary_text || '';
                    reasoningCell = reasoning
                        ? `<div class="comparison-reasoning">${this.escapeHtml(reasoning)}</div>`
                        : '—';
                } else if (run.status === 'partial') {
                    scoreCell = '<span class="sample-note">partial run</span>';
                } else if (run.status === 'failed') {
                    scoreCell = '<span class="sample-note">failed run</span>';
                }

                tableRows += `
                    <tr>
                        <td style="font-weight:600">${this.escapeHtml(method.display_name)}</td>
                        <td>${this.escapeHtml(run.id)} ${statusBadge}</td>
                        <td>${scoreCell}</td>
                        <td>${verdictCell}</td>
                        <td>${reasoningCell}</td>
                    </tr>
                `;
            }
        }

        if (!tableRows) {
            tableRows = `<tr><td colspan="5" class="exp-set-empty">No eval results found for task ${taskId}</td></tr>`;
        }

        // Aggregate summary — score and verdict sample counts are independent
        const scoreSamples = perTask.score_sample_count ?? 0;
        const verdictSamples = perTask.verdict_sample_count ?? 0;
        const aggParts = [];
        if (scoreSamples > 0) {
            aggParts.push(`Score samples: <b>${scoreSamples}</b>`);
            aggParts.push(`Mean: <b>${perTask.score_mean?.toFixed(4) ?? '—'}</b>`);
            aggParts.push(`Std: <b>${perTask.score_std?.toFixed(4) ?? '—'}</b>`);
            aggParts.push(`Range: <b>${perTask.score_min?.toFixed(2) ?? '—'} – ${perTask.score_max?.toFixed(2) ?? '—'}</b>`);
        } else {
            aggParts.push('<span class="sample-note">No numeric scores</span>');
        }
        if (verdictSamples > 0) {
            aggParts.push(`Verdict samples: <b>${verdictSamples}</b>`);
        }
        if (perTask.has_verdict_flip) {
            aggParts.push('<span style="color:var(--accent-yellow)">⚡ Verdict flip</span>');
        }
        const aggSummary = aggParts.map(p => `<span>${p}</span>`).join('');

        // ------------------------------------------------------------------
        // Task info cards (agent result, metrics, config)
        // ------------------------------------------------------------------
        let taskInfoHtml = '';
        {
            const m = taskMeta;
            const metrics = m.metrics || {};
            const statusClass = m.agent_success ? 'success' : 'fail';
            const statusLabel = m.agent_success ? 'Success' : (m.agent_success === false ? 'Fail' : '—');
            const steps = metrics.steps ?? '—';
            const e2e = metrics.end_to_end_ms ? (metrics.end_to_end_ms / 1000).toFixed(1) + 's' : '—';
            const cost = metrics.total_cost != null ? `$${metrics.total_cost.toFixed(4)}` : '—';
            const threshold = m.rubric?.score_threshold;

            taskInfoHtml = `
                <div class="exp-set-section">
                    <div class="exp-set-summary-cards" style="grid-template-columns:repeat(auto-fit,minmax(100px,1fr))">
                        <div class="exp-set-summary-card">
                            <span class="card-value ${statusClass}">${statusLabel}</span>
                            <span class="card-label">Agent Self-Report</span>
                        </div>
                        <div class="exp-set-summary-card">
                            <span class="card-value">${steps}</span>
                            <span class="card-label">Steps</span>
                        </div>
                        <div class="exp-set-summary-card">
                            <span class="card-value">${e2e}</span>
                            <span class="card-label">Duration</span>
                        </div>
                        <div class="exp-set-summary-card">
                            <span class="card-value">${cost}</span>
                            <span class="card-label">Cost</span>
                        </div>
                        ${threshold != null ? `
                        <div class="exp-set-summary-card">
                            <span class="card-value">${threshold}</span>
                            <span class="card-label">Pass Threshold</span>
                        </div>` : ''}
                        ${m.model_id ? `
                        <div class="exp-set-summary-card">
                            <span class="card-value" style="font-size:13px">${this.escapeHtml(m.model_id)}</span>
                            <span class="card-label">Model</span>
                        </div>` : ''}
                    </div>
                </div>
            `;
        }

        // ------------------------------------------------------------------
        // Agent answer
        // ------------------------------------------------------------------
        let answerHtml = '';
        if (taskMeta.answer) {
            answerHtml = `
                <div class="exp-set-section">
                    <div class="steps-card">
                        <div class="steps-card-header" id="exp-answer-toggle">
                            <div class="steps-card-title">
                                <span class="steps-card-label">Agent Answer</span>
                                <span class="steps-card-chevron collapsed">&#9662;</span>
                            </div>
                        </div>
                        <div class="steps-card-body collapsed" id="exp-answer-body">
                            <div style="padding:12px 16px;white-space:pre-wrap;font-size:13px;line-height:1.6">${this.escapeHtml(taskMeta.answer)}</div>
                        </div>
                    </div>
                </div>
            `;
        }

        // ------------------------------------------------------------------
        // Scoring rubric (scoring_items, key_points, reference_steps)
        // ------------------------------------------------------------------
        let rubricHtml = '';
        const rubric = taskMeta.rubric;
        if (rubric) {
            const sections = [];
            if (rubric.scoring_items) {
                sections.push(`
                    <div class="rubric-subsection">
                        <div class="rubric-subtitle">Scoring Items</div>
                        <pre class="rubric-text">${this.escapeHtml(rubric.scoring_items)}</pre>
                    </div>
                `);
            }
            if (rubric.key_points) {
                sections.push(`
                    <div class="rubric-subsection">
                        <div class="rubric-subtitle">Key Points</div>
                        <pre class="rubric-text">${this.escapeHtml(rubric.key_points)}</pre>
                    </div>
                `);
            }
            if (rubric.reference_steps) {
                sections.push(`
                    <div class="rubric-subsection">
                        <div class="rubric-subtitle">Reference Steps</div>
                        <pre class="rubric-text">${this.escapeHtml(rubric.reference_steps)}</pre>
                    </div>
                `);
            }
            if (rubric.common_mistakes) {
                sections.push(`
                    <div class="rubric-subsection">
                        <div class="rubric-subtitle">Common Mistakes</div>
                        <pre class="rubric-text">${this.escapeHtml(rubric.common_mistakes)}</pre>
                    </div>
                `);
            }
            if (sections.length > 0) {
                rubricHtml = `
                    <div class="exp-set-section">
                        <div class="steps-card">
                            <div class="steps-card-header" id="exp-rubric-toggle">
                                <div class="steps-card-title">
                                    <span class="steps-card-label">Scoring Rubric</span>
                                    <span class="steps-card-chevron collapsed">&#9662;</span>
                                </div>
                            </div>
                            <div class="steps-card-body collapsed" id="exp-rubric-body">
                                ${sections.join('')}
                            </div>
                        </div>
                    </div>
                `;
            }
        }

        const compEl = document.getElementById('exp-set-task-comparison');
        compEl.innerHTML = `
            <button class="exp-set-back-btn" id="comp-back-btn">← Back to Overview</button>
            <div class="task-comparison-header">
                <span class="task-comparison-title">Task ${taskId}</span>
                <span class="task-comparison-desc">${this.escapeHtml(taskMeta.task || '')}</span>
            </div>
            <div class="exp-set-subtitle" style="margin-bottom:16px;gap:12px;flex-wrap:wrap">${aggSummary}</div>

            ${taskInfoHtml}

            ${rubricHtml}

            ${answerHtml}

            ${gifBannerHtml}

            ${actionHistoryHtml}

            <div class="exp-set-section">
                <div class="exp-set-section-title">Judge Results by Method / Run</div>
                <div class="task-comparison-scroll">
                    <table class="task-comparison-table">
                        <thead>
                            <tr>
                                <th>Method</th>
                                <th>Run</th>
                                <th>Score</th>
                                <th>Verdict</th>
                                <th>Reasoning</th>
                            </tr>
                        </thead>
                        <tbody>${tableRows}</tbody>
                    </table>
                </div>
            </div>
        `;

        compEl.classList.remove('hidden');
        document.getElementById('exp-set-overview').classList.add('hidden');

        // GIF banner: click to open lightbox
        const gifPlayBtn = document.getElementById('exp-btn-play-gif');
        const gifPreview = document.getElementById('exp-gif-preview');
        if (gifPlayBtn && taskData?.gif_path) {
            const openGif = () => {
                document.getElementById('gif-lightbox-img').src = taskData.gif_path;
                document.getElementById('gif-lightbox').classList.remove('hidden');
            };
            gifPlayBtn.addEventListener('click', (e) => { e.stopPropagation(); openGif(); });
            gifPreview.addEventListener('click', openGif);
        }

        // Collapsible sections: toggle expand/collapse
        for (const [toggleId, bodyId] of [
            ['exp-steps-card-toggle', 'exp-steps-card-body'],
            ['exp-answer-toggle', 'exp-answer-body'],
            ['exp-rubric-toggle', 'exp-rubric-body'],
        ]) {
            const toggle = document.getElementById(toggleId);
            if (toggle) {
                toggle.addEventListener('click', () => {
                    const body = document.getElementById(bodyId);
                    const chevron = toggle.querySelector('.steps-card-chevron');
                    body.classList.toggle('collapsed');
                    chevron.classList.toggle('collapsed');
                });
            }
        }

        // Screenshot click → lightbox (both step screenshots and standalone thumbs)
        compEl.querySelectorAll('.screenshot-thumb').forEach(img => {
            img.addEventListener('click', () => this.openLightbox(img.src));
        });

        document.getElementById('comp-back-btn').addEventListener('click', () => {
            compEl.classList.add('hidden');
            document.getElementById('exp-set-overview').classList.remove('hidden');
            document.querySelectorAll('.task-item').forEach(i => i.classList.remove('active'));
            this.currentExpSetTask = null;
        });
    }

    getTaskScoreInfo(taskId) {
        if (!this.currentRun) return null;
        const decision = dataLoader.getJudgeDecision(this.currentRun, taskId, this.judgeMode);
        if (!decision.available) return null;

        if (this.judgeMode === 'agent_success' || this.judgeMode === 'llm_judge') {
            return {
                text: decision.label,
                cls: decision.success ? 'score-success' : 'score-fail'
            };
        }

        if (typeof decision.score === 'number') {
            return {
                text: typeof decision.threshold === 'number'
                    ? `${decision.score}/${decision.threshold}`
                    : `${decision.score}`,
                cls: decision.success ? 'score-success' : 'score-fail'
            };
        }

        return {
            text: decision.success ? 'OK' : 'FAIL',
            cls: decision.success ? 'score-success' : 'score-fail'
        };
    }

    filterTasks(query) {
        this.populateTasksList(query);
    }

    // ==================================================================
    // Selection
    // ==================================================================

    selectRun(uuid) {
        this.currentMode = 'run';
        this.currentExperimentSet = null;
        this.currentExpSetTask = null;
        const runChanged = this.currentRun !== uuid;
        this.currentRun = uuid;
        document.getElementById('welcome-screen').classList.add('hidden');
        document.getElementById('exp-set-view').classList.add('hidden');
        document.getElementById('sidebar-right').classList.remove('hidden');
        this.populateTasksList(document.getElementById('task-search').value);
        const run = dataLoader.getRuns().find(r => r.uuid === uuid);
        if (run) document.getElementById('current-run-badge').textContent = run.displayName;
        this.renderRunOutputLogs(run);

        if (this.currentTask) {
            // Re-highlight the task in the refreshed list
            document.querySelector(`.task-item[data-task-id="${this.currentTask}"]`)?.classList.add('active');
            this.loadAndDisplayTask();
        } else if (runChanged) {
            // No task selected yet — clear any stale content
            document.getElementById('task-header').classList.add('hidden');
            document.getElementById('replay-section').classList.add('hidden');
            document.getElementById('eval-panel').classList.add('hidden');
            this.currentTaskData = null;
        }
    }

    async selectTask(taskId) {
        if (this.currentMode === 'experiment-set') {
            this.currentExpSetTask = taskId;
            await this.renderTaskComparison(taskId);
            return;
        }

        this.currentTask = taskId;
        if (!this.currentRun) {
            const runs = dataLoader.getRuns();
            if (runs.length > 0) {
                this.currentRun = runs[0].uuid;
                document.querySelector('.run-item')?.classList.add('active');
                document.getElementById('current-run-badge').textContent = runs[0].displayName;
                this.renderRunOutputLogs(runs[0]);
                document.getElementById('sidebar-right').classList.remove('hidden');
                this.populateTasksList(document.getElementById('task-search').value);
            }
        }
        await this.loadAndDisplayTask();
    }

    // ==================================================================
    // Task loading & display
    // ==================================================================

    async loadAndDisplayTask() {
        if (!this.currentRun || !this.currentTask) return;
        document.getElementById('welcome-screen').classList.add('hidden');
        document.getElementById('replay-section').classList.remove('hidden');

        try {
            this.currentTaskData = await dataLoader.loadTaskData(this.currentRun, this.currentTask);
            this.updateTaskHeader();
            this.showEvalPanel();
            this.showGifBanner();

            if (this.currentView === 'step-first') this.renderStepFirstView();
            else this.renderScreenshotFirstView();
            this.showCurrentView();
        } catch (error) {
            console.error('Failed to load task:', error);
            this.showError(`Failed to load task ${this.currentTask}: ${error.message}`);
        }
    }

    updateTaskHeader() {
        document.getElementById('task-header').classList.remove('hidden');
        const d = this.currentTaskData;
        document.getElementById('task-title').textContent = `Task #${d.task_id}`;
        document.getElementById('task-description').textContent = d.task || '';
        this.renderTaskStatusBadge(String(d.task_id));
        this.renderTaskJudgeSummary(String(d.task_id));
    }

    renderRunOutputLogs(run) {
        const section = document.getElementById('run-output-logs');
        const container = document.getElementById('run-output-logs-list');
        const logs = run?.outputLogs || [];

        if (logs.length === 0) {
            section.classList.add('hidden');
            container.innerHTML = '';
            return;
        }

        section.classList.remove('hidden');
        container.innerHTML = logs.map((log, index) => `
            <button class="output-log-item" data-log-index="${index}">
                <span class="output-log-name">${this.escapeHtml(log.name || 'log')}</span>
                <span class="output-log-source">${this.escapeHtml(log.source || '')}</span>
            </button>
        `).join('');

        container.querySelectorAll('.output-log-item').forEach(btn => {
            btn.addEventListener('click', () => {
                const log = logs[parseInt(btn.dataset.logIndex, 10)];
                this.showOutputLogModal(log);
            });
        });
    }

    renderTaskStatusBadge(taskId) {
        const badge = document.getElementById('task-status-badge');
        const decision = dataLoader.getJudgeDecision(this.currentRun, taskId, this.judgeMode);
        if (!decision.available) {
            badge.className = 'status-badge pending';
            badge.textContent = 'Pending';
            badge.classList.remove('hidden');
            return;
        }
        const ok = decision.success;
        badge.className = `status-badge ${ok ? 'success' : 'fail'}`;
        badge.textContent = ok ? 'Success' : 'Failed';
        badge.classList.remove('hidden');
    }

    renderTaskJudgeSummary(taskId) {
        const container = document.getElementById('task-judge-summary');
        const cards = [
            { mode: 'agent_success', title: 'Agent' },
            { mode: 'llm_judge', title: 'Verdict' },
            { mode: 'lexjudge_per_task', title: 'Per-task Threshold' },
            { mode: 'lexjudge_fixed_60', title: 'Fixed 60' }
        ];

        container.innerHTML = cards.map(({ mode, title }) => {
            const decision = dataLoader.getJudgeDecision(this.currentRun, taskId, mode);
            const isActive = mode === this.judgeMode;
            const stateClass = !decision.available ? 'pending' : (decision.success ? 'success' : 'fail');
            const value = this.getJudgeSummaryValue(decision, mode);
            return `
                <span class="judge-pill ${stateClass} ${isActive ? 'active' : ''}" title="${this.escapeHtml(this.getJudgeSummaryDetail(decision, mode))}">
                    <span class="judge-pill-label">${this.escapeHtml(title)}</span>
                    <span class="judge-pill-value">${this.escapeHtml(value)}</span>
                </span>
            `;
        }).join('');
    }

    // ------------------------------------------------------------------
    // GIF banner
    // ------------------------------------------------------------------

    showGifBanner() {
        const banner = document.getElementById('gif-banner');
        const d = this.currentTaskData;
        if (!d.has_gif) { banner.classList.add('hidden'); return; }

        banner.classList.remove('hidden');
        document.getElementById('gif-thumb').src = d.gif_path;

        const meta = document.getElementById('gif-meta');
        const steps = d.metrics?.steps || d.action_history?.length || '?';
        const e2e = d.metrics?.end_to_end_ms ? (d.metrics.end_to_end_ms / 1000).toFixed(1) + 's' : '?';
        meta.textContent = `${steps} steps | ${e2e} total`;
    }

    openGifLightbox() {
        if (!this.currentTaskData?.gif_path) return;
        document.getElementById('gif-lightbox-img').src = this.currentTaskData.gif_path;
        document.getElementById('gif-lightbox').classList.remove('hidden');
    }

    closeGifLightbox() {
        document.getElementById('gif-lightbox').classList.add('hidden');
    }

    // ------------------------------------------------------------------
    // Eval panel
    // ------------------------------------------------------------------

    showEvalPanel() {
        const panel = document.getElementById('eval-panel');
        const evalData = this.currentTaskData.evaluation;
        const rubric = this.currentTaskData.rubric;
        const decision = dataLoader.getJudgeDecision(this.currentRun, String(this.currentTaskData.task_id), this.judgeMode);
        if (!evalData && !decision.available && !rubric) { panel.classList.add('hidden'); return; }
        panel.classList.remove('hidden');

        // Score number with color
        const scoreEl = document.getElementById('eval-score-value');
        if (typeof decision.score === 'number') {
            scoreEl.textContent = decision.score;
            scoreEl.className = `eval-score-number ${decision.success ? 'success' : 'fail'}`;
        } else if (decision.available) {
            scoreEl.textContent = decision.label;
            scoreEl.className = `eval-score-number ${decision.success ? 'success' : 'fail'}`;
        } else {
            scoreEl.textContent = '--';
            scoreEl.className = 'eval-score-number';
        }

        // Threshold block (shown only when a numeric threshold is available)
        const thresholdBlock = document.getElementById('eval-threshold-block');
        const thresholdEl = document.getElementById('eval-threshold-value');
        if (typeof decision.threshold === 'number') {
            thresholdEl.textContent = decision.threshold;
            thresholdEl.className = 'eval-score-number eval-threshold-number';
            thresholdBlock.classList.remove('hidden');
        } else {
            thresholdBlock.classList.add('hidden');
        }

        // Verdict text (judge mode label only — score/threshold now shown above)
        const verdictEl = document.getElementById('eval-verdict');
        verdictEl.textContent = `Judge: ${this.getJudgeModeLabel(this.judgeMode)}`;

        // Setup tabs
        this._evalTabData = { evalData };
        const tabs = panel.querySelectorAll('.eval-tab');
        tabs.forEach(tab => {
            const clone = tab.cloneNode(true);
            tab.parentNode.replaceChild(clone, tab);
            clone.addEventListener('click', () => {
                panel.querySelectorAll('.eval-tab').forEach(t => t.classList.remove('active'));
                clone.classList.add('active');
                this.renderEvalTab(clone.dataset.evalTab);
            });
        });
        // Show default tab
        this.renderEvalTab('response');
    }

    renderEvalTab(tab) {
        const container = document.getElementById('eval-tab-content');
        const { evalData } = this._evalTabData || {};

        if (tab === 'response') {
            const response = evalData?.response || '';
            const reasoning = evalData?.reasoning || '';
            if (!response && !reasoning) {
                container.innerHTML = '<p class="no-data">No judge response data available</p>';
                return;
            }
            let html = '';
            if (response) {
                html += `
                    <div class="eval-section">
                        <div class="eval-section-label response">Raw Response</div>
                        <div class="eval-section-body"><pre>${this.escapeHtml(response)}</pre></div>
                    </div>`;
            }
            if (reasoning) {
                html += `
                    <div class="eval-section">
                        <div class="eval-section-label reasoning">Reasoning</div>
                        <div class="eval-section-body"><pre>${this.escapeHtml(reasoning)}</pre></div>
                    </div>`;
            }
            container.innerHTML = html;
        } else if (tab === 'rubric') {
            this.renderRubricTab(container);
        }
    }

    renderRubricTab(container) {
        const rubric = this.currentTaskData?.rubric;
        if (!rubric) {
            container.innerHTML = '<p class="no-data">No rubric data available for this task</p>';
            return;
        }

        let html = '';

        // Reference Steps
        if (rubric.steps?.length) {
            html += `
                <div class="eval-section">
                    <div class="eval-section-label system">Reference Steps</div>
                    <div class="eval-section-body">
                        <ol class="rubric-list">${rubric.steps.map(s =>
                            `<li>${this.escapeHtml(s)}</li>`
                        ).join('')}</ol>
                    </div>
                </div>`;
        }

        // Key Points
        if (rubric.key_points?.length) {
            html += `
                <div class="eval-section">
                    <div class="eval-section-label response">Key Points</div>
                    <div class="eval-section-body">
                        <ul class="rubric-list">${rubric.key_points.map(p =>
                            `<li>${this.escapeHtml(p)}</li>`
                        ).join('')}</ul>
                    </div>
                </div>`;
        }

        // Common Mistakes
        if (rubric.common_mistakes?.length) {
            html += `
                <div class="eval-section">
                    <div class="eval-section-label reasoning">Common Mistakes</div>
                    <div class="eval-section-body">
                        <ul class="rubric-list">${rubric.common_mistakes.map(m =>
                            `<li>${this.escapeHtml(m)}</li>`
                        ).join('')}</ul>
                    </div>
                </div>`;
        }

        // Scoring
        if (rubric.scoring) {
            const scoring = rubric.scoring;
            html += `
                <div class="eval-section">
                    <div class="eval-section-label user">Scoring (Total: ${scoring.total})</div>
                    <div class="eval-section-body">
                        <table class="rubric-scoring-table">
                            <thead>
                                <tr><th>Item</th><th>Score</th><th>Description</th></tr>
                            </thead>
                            <tbody>${(scoring.items || []).map(item => `
                                <tr>
                                    <td>${this.escapeHtml(item.name)}</td>
                                    <td class="rubric-score-cell">${item.score}</td>
                                    <td>${this.escapeHtml(item.description)}</td>
                                </tr>`
                            ).join('')}</tbody>
                        </table>
                    </div>
                </div>`;
        }

        container.innerHTML = html;
    }


    // ==================================================================
    // View switching
    // ==================================================================

    switchView(view) {
        this.currentView = view;
        document.getElementById('btn-step-first').classList.toggle('active', view === 'step-first');
        document.getElementById('btn-screenshot-first').classList.toggle('active', view === 'screenshot-first');
        if (this.currentTaskData) {
            if (view === 'step-first') this.renderStepFirstView();
            else this.renderScreenshotFirstView();
            this.showCurrentView();
        }
    }

    showCurrentView() {
        document.getElementById('step-first-view').classList.toggle('hidden', this.currentView !== 'step-first');
        document.getElementById('screenshot-first-view').classList.toggle('hidden', this.currentView !== 'screenshot-first');
        document.getElementById('compare-view').classList.add('hidden');
        // Ensure stats overlay isn't covering the replay (no restore logic — task UI is already wired up).
        document.getElementById('stats-view').classList.add('hidden');
        document.getElementById('btn-stats').classList.remove('active');
    }

    // ==================================================================
    // Step-first view
    // ==================================================================

    renderStepFirstView() {
        const container = document.getElementById('steps-container');
        const data = this.currentTaskData;
        const run = dataLoader.getRuns().find(r => r.uuid === this.currentRun);

        if (!data || !data.action_history || data.action_history.length === 0) {
            container.innerHTML = '<p class="no-data">No step data available</p>';
            return;
        }

        const screenshots = data.screenshots || {};
        const apiLogs = data.api_logs || {};
        const totalSteps = data.action_history.length;
        const screenshotCount = Object.keys(screenshots).length;
        const e2e = data.metrics?.end_to_end_ms ? (data.metrics.end_to_end_ms / 1000).toFixed(1) + 's' : null;
        const cost = data.metrics?.usage?.total_cost;

        // Build meta chips
        const chips = [`${totalSteps} steps`];
        if (screenshotCount) chips.push(`${screenshotCount} screenshots`);
        if (e2e) chips.push(e2e);
        if (cost != null) chips.push(`$${cost.toFixed(4)}`);

        // Build step list HTML
        let stepsHtml = '';
        data.action_history.forEach((action, index) => {
            const stepNum = index + 1;
            const screenshot = screenshots[stepNum] || screenshots[String(stepNum)];
            const screenshotUrl = screenshot ? dataLoader.getScreenshotUrl(run.path, data.task_id, screenshot) : null;
            const hasApiLog = apiLogs[stepNum] || apiLogs[String(stepNum)];

            stepsHtml += `
                <div class="step-card" data-step="${stepNum}">
                    <div class="step-header">
                        <span class="step-number">Step ${stepNum}</span>
                        <span class="step-action-summary">${this.escapeHtml(this.truncate(action, 120))}</span>
                        ${hasApiLog ? `<button class="btn-expand" data-step="${stepNum}">View Details</button>` : ''}
                    </div>
                    <div class="step-content">
                        <div class="step-main">
                            <div class="action-display">
                                <pre>${this.escapeHtml(action)}</pre>
                            </div>
                        </div>
                        ${screenshotUrl ? `
                            <div class="step-screenshot">
                                <img src="${screenshotUrl}" alt="Step ${stepNum}" class="screenshot-thumb" loading="lazy">
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        });

        container.innerHTML = `
            <div class="steps-card">
                <div class="steps-card-header" id="steps-card-toggle">
                    <div class="steps-card-title">
                        <span class="steps-card-label">Action History</span>
                        <span class="steps-card-chevron collapsed">&#9662;</span>
                    </div>
                    <div class="steps-card-meta">
                        ${chips.map(c => `<span class="steps-meta-chip">${this.escapeHtml(c)}</span>`).join('')}
                    </div>
                </div>
                <div class="steps-card-body collapsed" id="steps-card-body">
                    ${stepsHtml}
                </div>
            </div>
        `;

        // Toggle expand/collapse
        document.getElementById('steps-card-toggle').addEventListener('click', (e) => {
            if (e.target.closest('.btn-expand')) return; // don't toggle when clicking View Details
            const body = document.getElementById('steps-card-body');
            const chevron = container.querySelector('.steps-card-chevron');
            body.classList.toggle('collapsed');
            chevron.classList.toggle('collapsed');
        });

        // Screenshot click → lightbox
        container.querySelectorAll('.screenshot-thumb').forEach(img => {
            img.addEventListener('click', () => this.openLightbox(img.src));
        });

        // View Details → API log modal
        container.querySelectorAll('.btn-expand').forEach(btn => {
            btn.addEventListener('click', () => {
                this.showApiLogModal(parseInt(btn.dataset.step, 10));
            });
        });
    }

    // ==================================================================
    // Screenshot-first view
    // ==================================================================

    renderScreenshotFirstView() {
        const data = this.currentTaskData;
        const screenshots = data.screenshots || {};
        this.screenshotSteps = Object.keys(screenshots).map(Number).sort((a, b) => a - b);

        if (this.screenshotSteps.length === 0) {
            document.getElementById('main-screenshot').src = '';
            document.getElementById('screenshot-counter').textContent = '0 / 0';
            document.getElementById('screenshot-step-info').innerHTML = '<p class="no-data">No screenshots available</p>';
            return;
        }
        this.screenshotIndex = 0;
        this.updateScreenshotView();
    }

    updateScreenshotView() {
        const data = this.currentTaskData;
        const run = dataLoader.getRuns().find(r => r.uuid === this.currentRun);
        const screenshots = data.screenshots || {};
        const apiLogs = data.api_logs || {};

        if (!this.screenshotSteps || this.screenshotSteps.length === 0) return;

        const stepNum = this.screenshotSteps[this.screenshotIndex];
        const screenshot = screenshots[stepNum] || screenshots[String(stepNum)];
        const screenshotUrl = dataLoader.getScreenshotUrl(run.path, data.task_id, screenshot);

        document.getElementById('main-screenshot').src = screenshotUrl;
        document.getElementById('screenshot-counter').textContent =
            `${this.screenshotIndex + 1} / ${this.screenshotSteps.length}`;

        const action = data.action_history?.[stepNum - 1] || '';
        const hasApiLog = apiLogs[stepNum] || apiLogs[String(stepNum)];

        const infoPanel = document.getElementById('screenshot-step-info');
        infoPanel.innerHTML = `
            <div class="screenshot-step-detail">
                <h4>Step ${stepNum}</h4>
                <pre class="action-text">${this.escapeHtml(action)}</pre>
                ${hasApiLog
                    ? `<button class="btn-view-conv" data-step="${stepNum}">View API Log</button>`
                    : ''}
            </div>
        `;

        const convBtn = infoPanel.querySelector('.btn-view-conv');
        if (convBtn) {
            convBtn.addEventListener('click', () => {
                this.showApiLogModal(parseInt(convBtn.dataset.step, 10));
            });
        }

        document.getElementById('main-screenshot').onclick = () => this.openLightbox(screenshotUrl);
    }

    navigateScreenshot(direction) {
        if (!this.screenshotSteps || this.screenshotSteps.length === 0) return;
        const newIdx = this.screenshotIndex + direction;
        if (newIdx >= 0 && newIdx < this.screenshotSteps.length) {
            this.screenshotIndex = newIdx;
            this.updateScreenshotView();
        }
    }

    // ==================================================================
    // API Log Modal (replaces conversation modal)
    // ==================================================================

    async showApiLogModal(stepNumber) {
        const data = this.currentTaskData;
        const taskDir = data._taskDir;

        // Load in parallel
        const [apiLog, systemPrompt] = await Promise.all([
            dataLoader.loadApiLog(taskDir, stepNumber),
            dataLoader.loadSystemPrompt(taskDir)
        ]);

        if (!apiLog) {
            this.showError('Failed to load API log for step ' + stepNumber);
            return;
        }

        const modal = document.getElementById('api-log-modal');
        modal.classList.remove('hidden');
        modal.querySelector('.conversation-tabs').classList.remove('hidden');
        document.getElementById('modal-title').textContent = `API Log - Step ${stepNumber}`;

        // Prepare parsed data bundle
        const bundle = { apiLog, systemPrompt };

        // Setup tabs
        const tabBtns = modal.querySelectorAll('.tab-btn');
        tabBtns.forEach(btn => {
            // Clone to remove old listeners
            const clone = btn.cloneNode(true);
            btn.parentNode.replaceChild(clone, btn);
            clone.addEventListener('click', () => {
                modal.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                clone.classList.add('active');
                this.renderApiLogTab(bundle, clone.dataset.tab);
            });
        });

        // Reset all tab states then activate first
        modal.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        modal.querySelector('.tab-btn').classList.add('active');
        this.renderApiLogTab(bundle, 'memory');
    }

    async showOutputLogModal(log) {
        if (!log?.path) return;
        const text = await dataLoader.loadTextFile(log.path);
        if (text === null) {
            this.showError(`Failed to load output log: ${log.name || log.path}`);
            return;
        }

        const modal = document.getElementById('api-log-modal');
        modal.classList.remove('hidden');
        modal.querySelector('.conversation-tabs').classList.add('hidden');
        document.getElementById('modal-title').textContent = `Output Log - ${log.name || 'log'}`;
        document.getElementById('conversation-content').innerHTML = `
            <div class="conv-section">
                <h4>${this.escapeHtml(log.source || '')}</h4>
                <pre>${this.escapeHtml(text)}</pre>
            </div>
        `;
    }

    renderApiLogTab(bundle, tab) {
        const container = document.getElementById('conversation-content');
        const log = bundle.apiLog;

        switch (tab) {
            case 'memory': {
                const memory = log.output?.memory || '';
                const thinking = log.output?.thinking || '';
                const nextGoal = log.output?.next_goal || '';
                const actions = log.output?.actions || [];
                const results = log.action_results || [];

                container.innerHTML = `
                    ${thinking ? `<div class="conv-section"><h4>Thinking</h4><pre>${this.escapeHtml(thinking)}</pre></div>` : ''}
                    <div class="conv-section">
                        <h4>Memory (Agent Reasoning)</h4>
                        <pre>${this.escapeHtml(memory) || 'No memory'}</pre>
                    </div>
                    ${nextGoal ? `<div class="conv-section"><h4>Next Goal</h4><pre>${this.escapeHtml(nextGoal)}</pre></div>` : ''}
                    <div class="conv-section">
                        <h4>Actions</h4>
                        <pre>${this.escapeHtml(JSON.stringify(actions, null, 2))}</pre>
                    </div>
                    <div class="conv-section">
                        <h4>Action Results</h4>
                        <pre>${this.escapeHtml(JSON.stringify(results, null, 2))}</pre>
                    </div>
                `;
                break;
            }
            case 'full': {
                container.innerHTML = `
                    <div class="conv-section">
                        <h4>Full API Log (raw JSON)</h4>
                        <pre>${this.escapeHtml(JSON.stringify(log, null, 2))}</pre>
                    </div>
                `;
                break;
            }
            case 'system': {
                const prompt = bundle.systemPrompt || 'System prompt not available';
                container.innerHTML = `
                    <div class="conv-section">
                        <h4>System Prompt</h4>
                        <pre>${this.escapeHtml(prompt)}</pre>
                    </div>
                `;
                break;
            }
            case 'browser': {
                const stateMsg = log.input?.state_message || '';
                const browserState = dataLoader.extractBrowserState(stateMsg);
                const url = log.input?.url || '';
                container.innerHTML = `
                    <div class="conv-section">
                        <h4>Current URL</h4>
                        <pre>${this.escapeHtml(url)}</pre>
                    </div>
                    <div class="conv-section">
                        <h4>Browser State (DOM Tree)</h4>
                        <pre>${this.escapeHtml(browserState) || 'No browser state extracted'}</pre>
                    </div>
                `;
                break;
            }
        }
    }

    closeModal() {
        document.getElementById('api-log-modal').classList.add('hidden');
    }

    // ==================================================================
    // Lightbox
    // ==================================================================

    openLightbox(src) {
        document.getElementById('lightbox-img').src = src;
        document.getElementById('lightbox').classList.remove('hidden');
    }

    closeLightbox() {
        document.getElementById('lightbox').classList.add('hidden');
    }

    // ==================================================================
    // Compare mode
    // ==================================================================

    toggleCompareMode() {
        const view = document.getElementById('compare-view');
        if (!view.classList.contains('hidden')) {
            view.classList.add('hidden');
            this.showCurrentView();
            document.getElementById('btn-compare').classList.remove('active');
        } else {
            this.showCompareView();
            document.getElementById('btn-compare').classList.add('active');
            document.getElementById('btn-stats').classList.remove('active');
        }
    }

    showCompareView() {
        document.getElementById('welcome-screen').classList.add('hidden');
        document.getElementById('step-first-view').classList.add('hidden');
        document.getElementById('screenshot-first-view').classList.add('hidden');
        document.getElementById('stats-view').classList.add('hidden');
        document.getElementById('compare-view').classList.remove('hidden');

        const runs = dataLoader.getRuns(this.judgeMode);
        const selectors = document.getElementById('compare-run-selectors');
        selectors.innerHTML = runs.map((run, i) => `
            <label class="checkbox-label">
                <input type="checkbox" class="compare-run-checkbox" data-uuid="${run.uuid}" ${i < 3 ? 'checked' : ''}>
                ${this.escapeHtml(run.displayName)}
            </label>
        `).join('');

        selectors.querySelectorAll('.compare-run-checkbox').forEach(cb => {
            cb.addEventListener('change', () => this.updateCompareContent());
        });
        this.updateCompareContent();
    }

    async updateCompareContent() {
        const container = document.getElementById('compare-content');
        const selectedUuids = Array.from(document.querySelectorAll('.compare-run-checkbox:checked'))
            .map(cb => cb.dataset.uuid);

        if (!this.currentTask) {
            container.innerHTML = '<p class="no-data">Select a task from the sidebar to compare</p>';
            return;
        }
        if (selectedUuids.length === 0) {
            container.innerHTML = '<p class="no-data">Select at least one run to compare</p>';
            return;
        }

        container.innerHTML = '<p class="loading">Loading comparison data...</p>';

        try {
            const tasks = await Promise.all(
                selectedUuids.map(uuid => dataLoader.loadTaskData(uuid, this.currentTask).catch(() => null))
            );
            const valid = tasks.filter(Boolean);

            if (valid.length === 0) {
                container.innerHTML = '<p class="no-data">No data available for selected runs</p>';
                return;
            }

            container.innerHTML = `
                <div class="compare-grid" style="grid-template-columns: repeat(${valid.length}, 1fr)">
                    ${valid.map(d => `
                        <div class="compare-column">
                            <div class="compare-column-header">
                                <span class="model-name">${this.escapeHtml(d.runInfo.displayName)}</span>
                                <span class="score-badge ${this.getTaskJudgeClass(d.runInfo.uuid, d.task_id)}">
                                    ${this.escapeHtml(this.getTaskJudgeLabel(d.runInfo.uuid, d.task_id))}
                                </span>
                            </div>
                            <div class="compare-stats">
                                <span>Steps: ${d.action_history?.length || 0}</span>
                                <span>E2E: ${d.metrics?.end_to_end_ms ? (d.metrics.end_to_end_ms / 1000).toFixed(1) + 's' : '?'}</span>
                                <span>Cost: $${d.metrics?.usage?.total_cost?.toFixed(4) || '?'}</span>
                            </div>
                            <div class="compare-steps">
                                ${(d.action_history || []).slice(0, 8).map((a, i) => `
                                    <div class="compare-step">
                                        <span class="step-num">${i + 1}</span>
                                        <span class="step-action">${this.escapeHtml(this.truncate(a, 80))}</span>
                                    </div>
                                `).join('')}
                                ${d.action_history?.length > 8
                                    ? `<p class="more-steps">... ${d.action_history.length - 8} more steps</p>`
                                    : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        } catch (err) {
            console.error('Compare error:', err);
            container.innerHTML = '<p class="error">Failed to load comparison data</p>';
        }
    }

    // ==================================================================
    // Statistics view
    // ==================================================================

    toggleStatsView() {
        const view = document.getElementById('stats-view');
        if (!view.classList.contains('hidden')) {
            this.closeStatsView();
        } else {
            this.showStatsView();
        }
    }

    showStatsView() {
        // Hide other top-level sections so Stats owns the content area.
        document.getElementById('welcome-screen').classList.add('hidden');
        document.getElementById('task-header').classList.add('hidden');
        document.getElementById('replay-section').classList.add('hidden');
        document.getElementById('eval-panel').classList.add('hidden');
        document.getElementById('exp-set-view').classList.add('hidden');
        document.getElementById('stats-view').classList.remove('hidden');
        document.getElementById('btn-stats').classList.add('active');
        this.renderCharts();
    }

    closeStatsView() {
        document.getElementById('stats-view').classList.add('hidden');
        document.getElementById('btn-stats').classList.remove('active');
        // Restore the prior view: task content if loaded, otherwise welcome.
        if (this.currentTaskData) {
            document.getElementById('task-header').classList.remove('hidden');
            document.getElementById('replay-section').classList.remove('hidden');
            this.showEvalPanel();
        } else if (this.currentMode === 'experiment-set' && this.currentExperimentSet) {
            document.getElementById('exp-set-view').classList.remove('hidden');
        } else {
            document.getElementById('welcome-screen').classList.remove('hidden');
        }
    }

    renderCharts() {
        const stats = dataLoader.getSummaryStats(this.judgeMode);
        const labels = stats.map(s => s.displayName.split(' [')[0]);
        const fullLabels = stats.map(s => s.displayName);

        this._renderChart('chart-success-rate', 'successRate', {
            labels,
            fullLabels,
            datasets: [{
                label: 'Success Rate (%)',
                data: stats.map(s => s.successRate),
                backgroundColor: 'rgba(74,222,128,0.7)',
                borderColor: '#4ade80',
                borderWidth: 1
            }]
        }, { y: { max: 100 } });

        this._renderChart('chart-score-dist', 'scoreDist', {
            labels,
            fullLabels,
            datasets: [
                { label: 'Success', data: stats.map(s => s.successCount), backgroundColor: 'rgba(96,165,250,0.7)', borderColor: '#60a5fa', borderWidth: 1 },
                { label: 'Failed', data: stats.map(s => s.evaluatedTasks - s.successCount), backgroundColor: 'rgba(248,113,113,0.7)', borderColor: '#f87171', borderWidth: 1 }
            ]
        }, { y: { stacked: true }, x: { stacked: true } });

        this._renderChart('chart-steps', 'steps', {
            labels,
            fullLabels,
            datasets: [{
                label: 'Avg Steps',
                data: stats.map(s => s.avgSteps),
                backgroundColor: 'rgba(167,139,250,0.7)',
                borderColor: '#a78bfa',
                borderWidth: 1
            }]
        });

        this._renderChart('chart-cost', 'cost', {
            labels,
            fullLabels,
            datasets: [{
                label: 'Avg Cost ($)',
                data: stats.map(s => s.avgCost),
                backgroundColor: 'rgba(251,191,36,0.7)',
                borderColor: '#fbbf24',
                borderWidth: 1
            }]
        });
    }

    _renderChart(canvasId, key, data, scaleOverrides = {}) {
        const ctx = document.getElementById(canvasId)?.getContext('2d');
        if (!ctx) return;
        if (this.charts[key]) this.charts[key].destroy();

        const baseScale = {
            y: { beginAtZero: true, ticks: { color: '#9ca3af' }, grid: { color: 'rgba(75,85,99,0.3)' } },
            x: { ticks: { color: '#9ca3af', maxRotation: 45 }, grid: { display: false } }
        };

        // Merge overrides
        for (const axis of Object.keys(scaleOverrides)) {
            Object.assign(baseScale[axis], scaleOverrides[axis]);
        }

        this.charts[key] = new Chart(ctx, {
            type: 'bar',
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#9ca3af' } },
                    tooltip: {
                        callbacks: {
                            title: (items) => {
                                const item = items[0];
                                if (!item) return '';
                                return data.fullLabels?.[item.dataIndex] ?? item.label;
                            }
                        }
                    }
                },
                scales: baseScale
            }
        });
    }

    // ==================================================================
    // Home / Welcome screen
    // ==================================================================

    goHome() {
        // Reset selection state
        this.currentMode = 'run';
        this.currentRun = null;
        this.currentTask = null;
        this.currentTaskData = null;
        this.currentExperimentSet = null;
        this.currentExpSetTask = null;

        // Deselect sidebar items
        document.querySelectorAll('.tree-run').forEach(i => i.classList.remove('active'));
        document.querySelectorAll('.exp-set-item').forEach(i => i.classList.remove('active'));
        document.querySelectorAll('.task-item').forEach(i => i.classList.remove('active'));

        // Hide right sidebar (tasks are meaningless without a run)
        document.getElementById('sidebar-right').classList.add('hidden');

        // Hide all content views
        document.getElementById('task-header').classList.add('hidden');
        document.getElementById('replay-section').classList.add('hidden');
        document.getElementById('eval-panel').classList.add('hidden');
        document.getElementById('step-first-view').classList.add('hidden');
        document.getElementById('screenshot-first-view').classList.add('hidden');
        document.getElementById('stats-view').classList.add('hidden');
        document.getElementById('btn-stats').classList.remove('active');
        document.getElementById('exp-set-view').classList.add('hidden');
        document.getElementById('run-output-logs').classList.add('hidden');

        // Show welcome
        document.getElementById('welcome-screen').classList.remove('hidden');
        this.showWelcomeScreen();
    }

    showWelcomeScreen() {
        const stats = dataLoader.getSummaryStats(this.judgeMode);
        const runs = dataLoader.getRuns(this.judgeMode);
        const expSets = dataLoader.getExperimentSets();
        const container = document.getElementById('quick-stats');

        const expSetsSection = expSets.length > 0 ? `
            <div class="welcome-runs" style="margin-top:16px">
                <h4>Judge Experiment Sets</h4>
                ${expSets.map(s => `
                    <div class="welcome-run-item" data-set-id="${s.id}" style="cursor:pointer">
                        <span class="run-name">${this.escapeHtml(s.display_name)}</span>
                        <span class="run-rate">${s.task_count}t · ${(s.methods||[]).length}m</span>
                    </div>
                `).join('')}
            </div>
        ` : '';

        container.innerHTML = `
            <div class="welcome-stats-grid">
                <div class="welcome-stat">
                    <span class="stat-value">${stats.length}</span>
                    <span class="stat-label">Runs</span>
                </div>
                <div class="welcome-stat">
                    <span class="stat-value">${expSets.length}</span>
                    <span class="stat-label">Judge Sets</span>
                </div>
                <div class="welcome-stat">
                    <span class="stat-value">${dataLoader.getAllTaskIds().length}</span>
                    <span class="stat-label">Tasks</span>
                </div>
                <div class="welcome-stat">
                    <span class="stat-value">${dataLoader.getCommonTaskIds().length}</span>
                    <span class="stat-label">Common Tasks</span>
                </div>
            </div>
            ${expSetsSection}
        `;

        container.querySelectorAll('.welcome-run-item[data-set-id]').forEach(item => {
            item.addEventListener('click', () => {
                const setId = item.dataset.setId;
                if (!setId) return;
                document.querySelectorAll('.tree-run').forEach(i => i.classList.remove('active'));
                document.querySelectorAll('.exp-set-item').forEach(i => i.classList.remove('active'));
                document.querySelector(`.exp-set-item[data-set-id="${setId}"]`)?.classList.add('active');
                this.selectExperimentSet(setId);
            });
        });
    }

    // ==================================================================
    // Hot-reload
    // ==================================================================

    async refreshIndex() {
        const btn = document.getElementById('btn-refresh');
        const original = btn.innerHTML;
        btn.classList.add('refreshing');
        btn.disabled = true;
        try {
            await dataLoader.regenerate();
            this.onIndexReloaded();
        } catch (e) {
            console.error('Refresh failed:', e);
        } finally {
            btn.classList.remove('refreshing');
            btn.innerHTML = original;
            btn.disabled = false;
        }
    }

    onIndexReloaded() {
        const currentJudgeMode = this.judgeMode;
        this.populateRunsList(); // also calls populateExperimentSetsList
        this.populateJudgeModeSelect();
        document.getElementById('judge-mode-select').value = currentJudgeMode;

        if (this.currentMode === 'experiment-set' && this.currentExperimentSet) {
            const setEl = document.querySelector(`.exp-set-item[data-set-id="${this.currentExperimentSet}"]`);
            if (setEl) setEl.classList.add('active');
            this.populateTasksList(document.getElementById('task-search').value);
            if (this.currentExpSetTask) {
                const taskEl = document.querySelector(`.task-item[data-task-id="${this.currentExpSetTask}"]`);
                if (taskEl) taskEl.classList.add('active');
                this.renderTaskComparison(this.currentExpSetTask);
            } else {
                this.renderExperimentSetOverview();
            }
            return;
        }

        // Re-select current run/task if they still exist
        if (this.currentRun) {
            const runEl = document.querySelector(`.tree-run[data-uuid="${CSS.escape(this.currentRun)}"]`);
            if (runEl) runEl.classList.add('active');
            this.populateTasksList(document.getElementById('task-search').value);
        }
        if (this.currentTask) {
            const taskEl = document.querySelector(`.task-item[data-task-id="${this.currentTask}"]`);
            if (taskEl) taskEl.classList.add('active');
        }
        // Update welcome stats if visible
        if (!document.getElementById('welcome-screen').classList.contains('hidden')) {
            this.showWelcomeScreen();
        }
        // Re-render current task if one was active
        if (this.currentRun && this.currentTask) {
            this.loadAndDisplayTask();
        }
    }

    // ==================================================================
    // Theme
    // ==================================================================

    initSidebarResize() {
        const handle = document.getElementById('sidebar-left-resize');
        const sidebar = document.getElementById('sidebar-left');
        if (!handle || !sidebar) return;

        const MIN_WIDTH = 180;
        const MAX_WIDTH_VW = 0.5;
        let startX, startWidth, rafId;

        const onMouseMove = (e) => {
            if (rafId) return;
            rafId = requestAnimationFrame(() => {
                rafId = 0;
                const maxWidth = window.innerWidth * MAX_WIDTH_VW;
                const newWidth = Math.max(MIN_WIDTH, Math.min(maxWidth, startWidth + (e.clientX - startX)));
                sidebar.style.width = `${newWidth}px`;
            });
        };

        const onMouseUp = () => {
            if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
            handle.classList.remove('active');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = sidebar.getBoundingClientRect().width;
            handle.classList.add('active');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        });
    }

    initTheme() {
        const saved = localStorage.getItem('theme');
        if (saved === 'light') {
            document.documentElement.classList.add('light');
        }
        this.updateThemeButton();
    }

    toggleTheme() {
        document.documentElement.classList.toggle('light');
        const isLight = document.documentElement.classList.contains('light');
        localStorage.setItem('theme', isLight ? 'light' : 'dark');
        this.updateThemeButton();
    }

    updateThemeButton() {
        const btn = document.getElementById('btn-theme');
        if (!btn) return;
        const isLight = document.documentElement.classList.contains('light');
        btn.innerHTML = isLight ? '&#9790;' : '&#9788;';
        btn.title = isLight ? 'Switch to dark mode' : 'Switch to light mode';
    }

    // ==================================================================
    // Utilities
    // ==================================================================

    truncate(str, maxLen) {
        if (!str) return '';
        return str.length > maxLen ? str.substring(0, maxLen) + '...' : str;
    }

    getJudgeModeLabel(mode) {
        const match = dataLoader.getJudgeModes().find(item => item.value === mode);
        return match ? match.label : mode;
    }

    getJudgeSummaryValue(decision, mode) {
        if (!decision.available) return 'N/A';
        if (mode === 'agent_success' || mode === 'llm_judge') return decision.label;
        if (mode === 'lexjudge_per_task') {
            return typeof decision.score === 'number' && typeof decision.threshold === 'number'
                ? `${decision.score} / ${decision.threshold}`
                : decision.label;
        }
        if (mode === 'lexjudge_fixed_60') {
            return typeof decision.score === 'number'
                ? `${decision.score} / 60`
                : decision.label;
        }
        if (typeof decision.score === 'number' && typeof decision.threshold === 'number') {
            return `${decision.score} / ${decision.threshold}`;
        }
        if (typeof decision.score === 'number') return `${decision.score}`;
        return decision.label;
    }

    getJudgeSummaryDetail(decision, mode) {
        if (!decision.available) {
            return mode === 'agent_success' ? 'No self-report' : 'No result';
        }
        if (mode === 'agent_success') return 'Agent completion claim';
        if (mode === 'llm_judge') {
            return `Predictive Label: ${decision.label}`;
        }
        if (mode === 'lexjudge_per_task') {
            return typeof decision.threshold === 'number'
                ? `Task threshold ${decision.threshold}`
                : 'Recorded verdict';
        }
        if (mode === 'lexjudge_fixed_60') return 'Threshold 60';
        return decision.detail || '';
    }

    getTaskJudgeDecision(uuid, taskId) {
        return dataLoader.getJudgeDecision(uuid, String(taskId), this.judgeMode);
    }

    getTaskJudgeLabel(uuid, taskId) {
        const decision = this.getTaskJudgeDecision(uuid, taskId);
        if (!decision.available) return 'N/A';
        if (this.judgeMode === 'agent_success') return `Agent: ${decision.label}`;
        if (this.judgeMode === 'llm_judge') return decision.label;
        if (typeof decision.score === 'number' && typeof decision.threshold === 'number') {
            return `${decision.score}/${decision.threshold}`;
        }
        if (typeof decision.score === 'number') return `${decision.score}`;
        return decision.label;
    }

    getTaskJudgeClass(uuid, taskId) {
        const decision = this.getTaskJudgeDecision(uuid, taskId);
        if (!decision.available) return '';
        return decision.success ? 'success' : 'fail';
    }

    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    showError(message) {
        console.error(message);
        const container = document.getElementById('toast-container');
        if (!container) { alert(message); return; }
        const toast = document.createElement('div');
        toast.className = 'toast error';
        toast.textContent = message;
        container.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new BrowserAgentAnalyzer();
    window.app.init();
});
