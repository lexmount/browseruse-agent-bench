/**
 * Data Loader Module for Browser Agent Analyzer
 * Handles loading experiment data from JSON index and fetching task details.
 *
 * Directory layout:
 *   experiments/{benchmark}/{split}/{agent}/{timestamp}/tasks/{task_id}/
 *     ├── result.json
 *     ├── agent_history.gif          (optional)
 *     ├── api_logs/
 *     │   ├── step_001.json … step_NNN.json
 *     │   ├── system_prompt.txt
 *     │   └── summary.md
 *     └── trajectory/
 *         └── screenshot-1.png … screenshot-N.png
 */

class DataLoader {
    constructor() {
        this.index = null;
        this.cache = new Map();
        this.apiLogCache = new Map();
        this.systemPromptCache = new Map();
        this.textFileCache = new Map();
        this.judgeModes = [
            { value: 'lexjudge_per_task', label: 'LexJudge / Per-task threshold' },
            { value: 'lexjudge_fixed_60', label: 'LexJudge / Fixed 60' },
            { value: 'llm_judge', label: 'LexJudge Verdict / Predictive Label' },
            { value: 'agent_success', label: 'Agent self-report' }
        ];
    }

    // ------------------------------------------------------------------
    // Index loading
    // ------------------------------------------------------------------

    async loadIndex() {
        const response = await fetch('data/experiments.json');
        if (!response.ok) {
            throw new Error(`Failed to load index: HTTP ${response.status}`);
        }
        this.index = await response.json();
        console.log('Loaded index:', this.index.runs.length, 'runs,',
            (this.index.experiment_sets || []).length, 'experiment sets');
        return this.index;
    }

    // ------------------------------------------------------------------
    // Experiment Set helpers
    // ------------------------------------------------------------------

    getExperimentSets() {
        if (!this.index) return [];
        return this.index.experiment_sets || [];
    }

    getExperimentSetById(id) {
        if (!this.index) return null;
        return (this.index.experiment_sets || []).find(s => s.id === id) || null;
    }

    // ------------------------------------------------------------------
    // Run helpers
    // ------------------------------------------------------------------

    getRuns(judgeMode = this.getDefaultJudgeMode()) {
        if (!this.index) return [];
        return this.index.runs.map(run => ({
            uuid: run.uuid,
            benchmark: run.benchmark,
            agent: run.agent,
            modelId: run.model_id,
            split: run.split,
            config: run.config,
            stats: this.getRunStats(run, judgeMode),
            path: run.path,
            outputLogs: run.output_logs || [],
            evalMode: run.eval_data?.summary?.evaluation_config?.mode || null,
            displayName: this.getRunDisplayName(run)
        }));
    }

    getRunDisplayName(run) {
        const agent = run.agent || 'agent';
        const model = run.model_id || 'unknown';
        const ts = run.uuid;
        return `${agent} / ${model} [${ts}]`;
    }

    getRunsGrouped(judgeMode = this.getDefaultJudgeMode()) {
        if (!this.index) return [];

        // Group: benchmark+split → agent → model → [runs]
        const benchMap = new Map();

        for (const run of this.index.runs) {
            const bKey = `${run.benchmark}|||${run.split}`;
            if (!benchMap.has(bKey)) {
                benchMap.set(bKey, {
                    benchmark: run.benchmark,
                    split: run.split,
                    agentMap: new Map()
                });
            }
            const bench = benchMap.get(bKey);

            const agentName = run.agent || 'unknown';
            if (!bench.agentMap.has(agentName)) {
                bench.agentMap.set(agentName, { name: agentName, modelMap: new Map() });
            }
            const agent = bench.agentMap.get(agentName);

            const modelName = run.model || run.model_id || 'unknown';
            if (!agent.modelMap.has(modelName)) {
                agent.modelMap.set(modelName, { name: modelName, runs: [] });
            }
            const model = agent.modelMap.get(modelName);

            const stats = this.getRunStats(run, judgeMode);
            model.runs.push({ uuid: run.uuid, stats });
        }

        // Compute aggregated stats and flatten Maps to arrays
        const result = [];
        for (const bench of benchMap.values()) {
            const agents = [];
            for (const agent of bench.agentMap.values()) {
                const models = [];
                for (const model of agent.modelMap.values()) {
                    const rates = model.runs.map(r => r.stats.successRate);
                    const avgRate = rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : 0;
                    models.push({
                        name: model.name,
                        runs: model.runs,
                        stats: { avgSuccessRate: avgRate, totalRuns: model.runs.length }
                    });
                }
                const allRates = models.flatMap(m => m.runs.map(r => r.stats.successRate));
                const agentAvg = allRates.length ? allRates.reduce((a, b) => a + b, 0) / allRates.length : 0;
                agents.push({
                    name: agent.name,
                    models,
                    stats: { avgSuccessRate: agentAvg, totalRuns: allRates.length }
                });
            }
            const allBenchRates = agents.flatMap(a => a.models.flatMap(m => m.runs.map(r => r.stats.successRate)));
            const benchAvg = allBenchRates.length ? allBenchRates.reduce((a, b) => a + b, 0) / allBenchRates.length : 0;
            result.push({
                benchmark: bench.benchmark,
                split: bench.split,
                agents,
                stats: { avgSuccessRate: benchAvg, totalRuns: allBenchRates.length }
            });
        }
        return result;
    }

    // ------------------------------------------------------------------
    // Task ID helpers
    // ------------------------------------------------------------------

    getAllTaskIds() {
        if (!this.index) return [];
        return this.index.all_tasks || [];
    }

    getCommonTaskIds() {
        if (!this.index) return [];
        // Compute at runtime: tasks appearing in 2+ runs (not strict
        // intersection which is empty when _en and _zh runs are mixed).
        const counts = new Map();
        for (const run of this.index.runs) {
            for (const id of run.task_ids) {
                counts.set(id, (counts.get(id) || 0) + 1);
            }
        }
        return [...counts.entries()]
            .filter(([, n]) => n >= 2)
            .map(([id]) => id);
    }

    getTaskIdsForRun(uuid) {
        if (!this.index) return [];
        const run = this.index.runs.find(r => r.uuid === uuid);
        return run ? run.task_ids : [];
    }

    // ------------------------------------------------------------------
    // Evaluation helpers
    // ------------------------------------------------------------------

    getJudgeModes() {
        return this.judgeModes.slice();
    }

    getDefaultJudgeMode() {
        if (!this.index) return 'llm_judge';
        const hasLexBenchRun = this.index.runs.some(run => this.isLexJudgeRun(run));
        if (hasLexBenchRun) return 'lexjudge_per_task';
        const hasEvalRun = this.index.runs.some(run => Object.keys(run.eval_data?.task_results || {}).length > 0);
        return hasEvalRun ? 'llm_judge' : 'agent_success';
    }

    getRunByUuid(uuid) {
        if (!this.index) return null;
        return this.index.runs.find(run => run.uuid === uuid) || null;
    }

    getEvalData(uuid, taskId) {
        const run = this.getRunByUuid(uuid);
        if (!run || !run.eval_data) return null;
        return run.eval_data.task_results[taskId] || null;
    }

    getTaskMeta(uuid, taskId) {
        const run = this.getRunByUuid(uuid);
        if (!run) return null;
        return run.task_meta?.[taskId] || null;
    }

    getTaskRubric(taskId) {
        if (!this.index?.task_rubrics) return null;
        return this.index.task_rubrics[taskId] || null;
    }

    isLexJudgeRun(run) {
        const mode = run?.eval_data?.summary?.evaluation_config?.mode || '';
        return mode === 'LexBench-Browser_eval' || run?.benchmark === 'LexBench-Browser';
    }

    getJudgeDecision(uuid, taskId, judgeMode = this.getDefaultJudgeMode()) {
        const run = this.getRunByUuid(uuid);
        if (!run) return { available: false, success: null, label: 'Unavailable' };

        const evalData = this.getEvalData(uuid, taskId);
        const taskMeta = this.getTaskMeta(uuid, taskId) || {};
        const score = evalData?.score;
        const predictedLabel = evalData?.predicted_label;
        const agentSuccess = taskMeta.agent_success;
        const rubric = this.getTaskRubric(taskId);
        const taskThreshold = typeof taskMeta.score_threshold === 'number'
            ? taskMeta.score_threshold
            : (typeof rubric?.score_threshold === 'number' ? rubric.score_threshold : undefined);

        if (judgeMode === 'agent_success') {
            if (typeof agentSuccess !== 'boolean') {
                return { available: false, success: null, label: 'No self-report' };
            }
            return {
                available: true,
                success: agentSuccess,
                label: agentSuccess ? 'Success' : 'Failure',
                detail: agentSuccess ? 'Agent self-report: success' : 'Agent self-report: failure'
            };
        }

        if (!evalData) {
            return { available: false, success: null, label: 'Not evaluated' };
        }

        if (judgeMode === 'llm_judge') {
            const success = predictedLabel === 1;
            return {
                available: predictedLabel === 0 || predictedLabel === 1,
                success,
                label: success ? 'Success' : 'Failure',
                detail: `Predictive label${score == null ? '' : `, score ${score}`}`,
                score,
                predictedLabel
            };
        }

        if (judgeMode === 'lexjudge_fixed_60') {
            if (typeof score !== 'number') {
                return { available: false, success: null, label: 'No score' };
            }
            const success = score >= 60;
            return {
                available: true,
                success,
                label: success ? 'Success' : 'Failure',
                detail: `Score ${score} / threshold 60`,
                score,
                threshold: 60
            };
        }

        if (judgeMode === 'lexjudge_per_task') {
            if (!this.isLexJudgeRun(run)) {
                return { available: false, success: null, label: 'Non-LexJudge run' };
            }
            if (typeof score !== 'number') {
                return { available: false, success: null, label: 'No score' };
            }
            if (typeof taskThreshold === 'number') {
                const success = score >= taskThreshold;
                return {
                    available: true,
                    success,
                    label: success ? 'Success' : 'Failure',
                    detail: `Score ${score} / threshold ${taskThreshold}`,
                    score,
                    threshold: taskThreshold
                };
            }
            if (predictedLabel === 0 || predictedLabel === 1) {
                const success = predictedLabel === 1;
                return {
                    available: true,
                    success,
                    label: success ? 'Success' : 'Failure',
                    detail: `Recorded per-task label${score == null ? '' : `, score ${score}`}`,
                    score,
                    predictedLabel
                };
            }
            return { available: false, success: null, label: 'No threshold' };
        }

        return { available: false, success: null, label: 'Unavailable' };
    }

    getRunStats(runOrUuid, judgeMode = this.getDefaultJudgeMode()) {
        const run = typeof runOrUuid === 'string' ? this.getRunByUuid(runOrUuid) : runOrUuid;
        if (!run) {
            return {
                totalTasks: 0,
                evaluatedTasks: 0,
                successCount: 0,
                successRate: 0
            };
        }

        let evaluatedTasks = 0;
        let successCount = 0;
        for (const taskId of run.task_ids || []) {
            const decision = this.getJudgeDecision(run.uuid, taskId, judgeMode);
            if (!decision.available) continue;
            evaluatedTasks += 1;
            if (decision.success) successCount += 1;
        }

        return {
            totalTasks: run.stats.total_tasks,
            evaluatedTasks,
            successCount,
            successRate: evaluatedTasks ? (successCount / evaluatedTasks) * 100 : 0
        };
    }

    // ------------------------------------------------------------------
    // Task data loading
    // ------------------------------------------------------------------

    async loadTaskData(uuid, taskId) {
        const cacheKey = `${uuid}:${taskId}`;
        if (this.cache.has(cacheKey)) {
            return this.cache.get(cacheKey);
        }

        const run = this.getRunByUuid(uuid);
        if (!run) throw new Error(`Run not found: ${uuid}`);

        const taskDir = `${run.path}/tasks/${taskId}`;

        // Load result.json
        const response = await fetch(`${taskDir}/result.json`);
        if (!response.ok) {
            throw new Error(`Failed to load result: ${response.status}`);
        }
        const data = await response.json();

        // Attach evaluation data
        data.evaluation = this.getEvalData(uuid, taskId);
        data.evalPrompts = run.eval_data?.eval_prompts || {};
        data.rubric = this.getTaskRubric(taskId);
        data.task_meta = this.getTaskMeta(uuid, taskId) || {};
        data.runInfo = {
            uuid: run.uuid,
            modelId: run.model_id,
            agent: run.agent,
            benchmark: run.benchmark,
            displayName: this.getRunDisplayName(run)
        };

        // Attach file listings from index
        const taskFiles = run.task_files?.[taskId] || {};
        data.screenshots = taskFiles.screenshots || {};
        data.api_logs = taskFiles.api_logs || {};
        data.has_gif = taskFiles.has_gif || false;
        data.gif_path = data.has_gif ? `${taskDir}/agent_history.gif` : null;
        data._taskDir = taskDir;

        this.cache.set(cacheKey, data);
        return data;
    }

    // ------------------------------------------------------------------
    // Experiment-set task data loading
    // ------------------------------------------------------------------

    async loadExpSetTaskData(taskMeta) {
        const taskDir = taskMeta.path;
        const cacheKey = `expset:${taskDir}`;
        if (this.cache.has(cacheKey)) return this.cache.get(cacheKey);

        try {
            const response = await fetch(`${taskDir}/result.json`);
            if (!response.ok) return null;
            const data = await response.json();

            // Attach file info from indexed task_meta
            data.screenshots = taskMeta.screenshots || {};
            data.has_gif = taskMeta.has_gif || false;
            data.gif_path = data.has_gif ? `${taskDir}/agent_history.gif` : null;
            data._taskDir = taskDir;

            // Build api_logs availability map by scanning step files
            data.api_logs = {};

            this.cache.set(cacheKey, data);
            return data;
        } catch (e) {
            console.warn(`Failed to load exp-set task data from ${taskDir}:`, e);
            return null;
        }
    }

    // ------------------------------------------------------------------
    // API Log loading (replaces conversation loading)
    // ------------------------------------------------------------------

    async loadApiLog(taskDir, stepNumber) {
        const stepStr = String(stepNumber).padStart(3, '0');
        const url = `${taskDir}/api_logs/step_${stepStr}.json`;
        const cacheKey = url;

        if (this.apiLogCache.has(cacheKey)) {
            return this.apiLogCache.get(cacheKey);
        }

        try {
            const response = await fetch(url);
            if (!response.ok) return null;
            const data = await response.json();
            this.apiLogCache.set(cacheKey, data);
            return data;
        } catch (e) {
            console.warn(`Failed to load API log step ${stepNumber}:`, e);
            return null;
        }
    }

    async loadSystemPrompt(taskDir) {
        const url = `${taskDir}/api_logs/system_prompt.txt`;
        if (this.systemPromptCache.has(url)) {
            return this.systemPromptCache.get(url);
        }
        try {
            const response = await fetch(url);
            if (!response.ok) return null;
            const text = await response.text();
            this.systemPromptCache.set(url, text);
            return text;
        } catch (e) {
            return null;
        }
    }

    async loadTextFile(path) {
        if (this.textFileCache.has(path)) {
            return this.textFileCache.get(path);
        }
        try {
            const response = await fetch(path);
            if (!response.ok) return null;
            const text = await response.text();
            this.textFileCache.set(path, text);
            return text;
        } catch (e) {
            return null;
        }
    }

    // ------------------------------------------------------------------
    // URL helpers
    // ------------------------------------------------------------------

    getScreenshotUrl(runPath, taskId, screenshotName) {
        return `${runPath}/tasks/${taskId}/trajectory/${screenshotName}`;
    }

    getGifUrl(runPath, taskId) {
        return `${runPath}/tasks/${taskId}/agent_history.gif`;
    }

    // ------------------------------------------------------------------
    // Parsing helpers for API log
    // ------------------------------------------------------------------

    /**
     * Extract browser state (DOM tree) from the state_message field.
     */
    extractBrowserState(stateMessage) {
        if (!stateMessage) return '';
        const match = stateMessage.match(/<browser_state>([\s\S]*?)<\/browser_state>/);
        return match ? match[1].trim() : '';
    }

    /**
     * Extract agent history from the state_message field.
     */
    extractAgentHistory(stateMessage) {
        if (!stateMessage) return '';
        const match = stateMessage.match(/<agent_history>([\s\S]*?)<\/agent_history>/);
        return match ? match[1].trim() : '';
    }

    /**
     * Extract agent state from the state_message field.
     */
    extractAgentState(stateMessage) {
        if (!stateMessage) return '';
        const match = stateMessage.match(/<agent_state>([\s\S]*?)<\/agent_state>/);
        return match ? match[1].trim() : '';
    }

    // ------------------------------------------------------------------
    // Summary statistics
    // ------------------------------------------------------------------

    getSummaryStats(judgeMode = this.getDefaultJudgeMode()) {
        if (!this.index) return [];
        return this.index.runs.map(run => ({
            uuid: run.uuid,
            modelId: run.model_id,
            agent: run.agent,
            benchmark: run.benchmark,
            displayName: this.getRunDisplayName(run),
            split: run.split,
            ...this.getRunStats(run, judgeMode),
            avgSteps: run.stats.avg_steps || 0,
            avgCost: run.stats.avg_cost || 0
        }));
    }

    // ------------------------------------------------------------------
    // Hot-reload support
    // ------------------------------------------------------------------

    /**
     * Trigger server-side index regeneration via POST /api/regenerate.
     * Falls back to re-fetching the static file if the API is unavailable.
     */
    async regenerate() {
        try {
            const res = await fetch('/api/regenerate', { method: 'POST' });
            if (res.ok) {
                this.index = await res.json();
            } else {
                // Fallback: just re-fetch static file
                await this.loadIndex();
            }
        } catch {
            await this.loadIndex();
        }
        this.clearCache();
        return this.index;
    }

    /**
     * Start polling for index changes.  When the server-side mtime of
     * experiments.json changes, reload the index and invoke `onChange`.
     */
    startWatching(onChange, intervalMs = 3000) {
        if (this._watchTimer) return;
        this._lastMtime = 0;

        this._watchTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/index-mtime');
                if (!res.ok) return;
                const { mtime } = await res.json();
                if (this._lastMtime && mtime !== this._lastMtime) {
                    console.log('[watch] Index changed, reloading…');
                    await this.loadIndex();
                    this.clearCache();
                    if (onChange) onChange();
                }
                this._lastMtime = mtime;
            } catch {
                // API unavailable (static server) – ignore
            }
        }, intervalMs);
    }

    stopWatching() {
        if (this._watchTimer) { clearInterval(this._watchTimer); this._watchTimer = null; }
    }

    clearCache() {
        this.cache.clear();
        this.apiLogCache.clear();
        this.systemPromptCache.clear();
    }
}

// Create global instance
const dataLoader = new DataLoader();
