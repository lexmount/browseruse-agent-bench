# browser-harness agent integration — design

Date: 2026-07-07
Status: approved for implementation (autonomous session; assumptions listed at the end)

## Goal

Benchmark [browser-harness](https://github.com/browser-use/browser-harness) — the thin CDP
harness plus its agent skill — as a first-class agent in browseruse-agent-bench, so its
LexBench-Browser results flow through the standard `bubench run` / `bubench eval` pipeline
and are directly comparable with browser-use, openclaw, claude-code, etc.

A prior ad-hoc A/B study (browser-harness repo, `docs/lexbench-benchmark/`) showed large
skill gains but used custom scripts outside the bench. This integration replaces that with
the standard pipeline. Domain skills (`BH_DOMAIN_SKILLS`) stay **off**: the arm under test
is "harness + its core skill", the same thing `browser-harness skill` installs for users.

## Approach

New agent `browser-harness` (`browseruse_bench/agents/browser_harness.py`), a `CLIAgent`
subclass. The executor is the Claude Code CLI (`claude -p`, stream-json), reusing the
stream-parsing / usage / api_logs helpers already in `agents/claude_code.py`. The agent's
only browser tool is Bash running `browser-harness <<'PY' ... PY` heredocs, exactly as the
shipped SKILL.md teaches.

Alternatives considered:

- **bh-lex isolated mode** (harness manages its own Lexmount sessions): rejected — bypasses
  the bench browsers layer (CDP readiness probe from #83, session accounting, backend
  selection via config).
- **Skill via `.claude/skills` + Skill tool**: rejected — the bench invokes `claude --bare`,
  which skips project skill discovery; embedding the skill text in the system prompt is
  deterministic and version-pinned per run.
- **Codex CLI executor**: deferred — the claude CLI path is already proven against the
  bench gateway (strip proxy), and one executor keeps the first experiment interpretable.

## Wiring

- **Browser**: `open_browser_session(...)` as in claude_code.py; require a CDP transport
  (lexmount/cdp), fail fast otherwise. The session's ws URL is exported to the claude
  subprocess as `BU_CDP_WS`, which browser-harness documents as an explicit-endpoint
  override (blocks local-Chrome discovery and cloud autospawn).
- **Isolation**: `BU_NAME=bench-<task_id>` — browser-harness daemons are keyed by
  `BU_NAME`, so concurrent tasks each get their own daemon attached to their own browser.
  The daemon inherits env from the first `browser-harness` call inside the claude Bash
  tool, which inherits it from the agent subprocess.
- **Cleanup**: after the claude subprocess exits, run `browser-harness --reload` with the
  task's `BU_NAME` to stop the per-task daemon (log-once on failure, per cleanup rules).
- **Skill delivery**: at agent start, run `browser-harness skill` and embed its output in
  the system prompt, followed by a bench-overrides section that supersedes the parts that
  do not apply headless: no `bh-lex`, no cloud daemons/auth, no asking the user at login
  walls (answer from collected data instead), the browser is pre-connected via `BU_CDP_WS`,
  screenshots go to `trajectory/screenshot-<n>.png` in the task workspace, answer in final
  message. If the `browser-harness` executable is missing, return an error AgentResult
  (same pattern as the missing-`claude` case).
- **Tools**: `--allowedTools "Bash"`; cwd = task workspace; `--bare`,
  `--dangerously-skip-permissions`, `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` from agent
  config (same as claude-code).
- **Artifacts**: screenshots collected by globbing `trajectory/*.png` after the run;
  action_history from Bash tool_use commands (first line, truncated); api_logs and token
  usage via the shared claude_code helpers.

## Config

`config.example.yaml` gains an `agents.browser-harness` section (active_model,
active_browser, max_turns, timeout). The live `config.yaml` entry points at
`dmx-claude-opus-4-8` (LiteLLM gateway behind the local strip proxy, the proven
claude-CLI path) and `active_browser: lexmount`.

## Testing

- Targeted pytest: system-prompt assembly (skill text + overrides, executable-missing
  error path), env construction (`BU_CDP_WS`, `BU_NAME`), action extraction from Bash
  tool_use blocks, screenshot collection from trajectory dir. Subprocess boundaries mocked.
- Real smoke test per AGENTS.md: `bubench run --agent browser-harness --data
  LexBench-Browser --mode single`, verified by subprocess log evidence.
- Experiment: LexBench-Browser batch through `bubench run` + `bubench eval`.

## Assumptions (made autonomously)

1. "harness skill" = browser-harness's shipped skill (`browser-harness skill`), executor =
   Claude Code CLI. Domain skills off for this phase.
2. Agent name `browser-harness`; model `dmx-claude-opus-4-8` via strip proxy (the only
   claude-CLI-compatible gateway path currently green).
3. Experiment scope: LexBench-Browser on lexmount, moderate first_n batch first; full
   split only after the batch looks healthy.
