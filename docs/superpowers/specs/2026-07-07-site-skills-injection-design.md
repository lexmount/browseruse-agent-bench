# Site-skills prompt injection — design

Date: 2026-07-07
Status: approved (supersedes the earlier "browser-harness as a new agent" draft in this
file's first revision; the user's requirement is that ALL bench agents can use the
written skills, not that browser-harness becomes one more agent)

## Goal

Make the site skills written in the sibling checkout
`../browser-harness/agent-workspace/domain-skills/` (182 site dirs, 191 md files —
URL patterns, anti-bot workarounds, selectors, API fallbacks) usable by **every** bench
agent, and run skill-on vs skill-off experiments through the standard
`bubench run` / `bubench eval` pipeline.

## Approach: inject at the bench task-assembly layer

The only integration surface shared by all agents (browser-use, skyvern, openclaw,
claude-code, codex, cursor, openai-cua, deepbrowse) is the task prompt the bench
assembles. File-drop/pointer or per-agent skill mechanisms only cover CLI agents with
file tools. So: match the task's declared target site against the skill library at
prompt-assembly time in `cli/run.py`, and append the matched skill files' full content
to the task prompt. Agents need zero per-agent wiring.

Known, accepted deltas vs the harness's native mechanism:

- Harness matches at navigation time per visited URL; the bench matches statically
  against the task's declared `target_website` / `task_start_url` / `url` (`urls` union
  for multi-site tasks). LexBench tasks carry a single-site constraint, so the miss case
  is only mid-task hops to undeclared domains.
- Injection is unconditional (skill always in context); "pointer / read-on-demand" mode
  is deferred — it only works for file-capable CLI agents and confounds "skill available"
  with "skill used" for weak models.
- Token cost: median skill ~7k chars (~2.5k tokens), p90 ~20k chars; sits in the cached
  prompt prefix for step-loop agents. Prior harness A/B showed net total tokens DROP
  56-64% because skills cut trial-and-error rounds; the experiment re-measures this
  through AgentUsage.

## Components

1. `browseruse_bench/utils/site_skills.py`
   - `match_skill_files(url, skills_dir) -> list[Path]`: port of harness
     `helpers._domain_skills()` matching — hostname, dotted suffixes, single labels,
     compared with `.-_`-stripped normalization, plus per-dir `hosts` alias files.
     Multi-URL tasks take the union. Cap at 10 files (harness parity).
   - `build_skills_section(files, max_chars) -> str`: markdown section
     `## Site knowledge (pre-collected)` + file contents, truncated at `max_chars`,
     with a note that code snippets reference browser-harness helpers and should be
     adapted to the agent's own tooling.
2. Config (`config.example.yaml`, top level, default off):
   ```yaml
   site_skills:
     enabled: false
     dir: ../browser-harness/agent-workspace/domain-skills  # resolved against REPO_ROOT
     max_chars: 30000
   ```
3. CLI: `bubench run --site-skills on|off` overrides `site_skills.enabled`.
4. Injection: in `cli/run.py` where the task prompt is assembled (same layer that already
   resolves the site for login-context routing). Off arm produces byte-identical prompts
   to today.
5. Observability: per-task log line with matched dirs/files and injected char count;
   `run_manifest.json` records `site_skills: {enabled, dir, matched_files, injected_chars}`
   per run so eval/attribution can split arms and hit/miss strata.

## Testing

- Unit: matching (exact host, suffix, label, hosts alias, no-match), default-off
  behavior, max_chars truncation, section assembly, multi-site union.
- Smoke (per AGENTS.md, shared-prompt-path change ⇒ every experiment agent):
  `bubench run --agent {browser-use,openclaw,claude-code} --data LexBench-Browser
  --mode single --site-skills on`, verified by subprocess log evidence; one off-arm
  run to confirm no regression.

## Experiment

3 agents (browser-use, openclaw, claude-code) × 2 arms (`--site-skills off|on`) ×
LexBench-Browser `first_n 20`, lexmount backend, standard eval; compare judge score and
token totals per arm. Full-split runs only after this batch looks healthy.
