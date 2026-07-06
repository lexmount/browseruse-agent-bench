# Contributing

Thanks for helping make browseruse-agent-bench a reproducible evaluation framework for browser
agents. LexBench-Browser is the built-in public dataset. The most useful contributions are the
ones other teams can run, inspect, and compare.

## Good First Contributions

- Add or improve docs, examples, and troubleshooting notes.
- Propose a dataset task with clear success criteria. See
  `community/tasks/multilingual-task-proposal-examples.md` for examples.
- Submit a reproducible result with redacted config and task-level outputs.
- Add a small agent adapter or example config for an existing integration.

## High-Impact Contributions

- **Agent adapters**: implement a `BaseAgent` adapter, register it, add an optional dependency
  group if needed, and include a smoke command.
- **Dataset tasks**: include the target site, language/region, login requirements, expected
  final state, and evaluation criteria.
- **Browser backends**: implement the backend contract, keep provider dependencies lazy, and
  document agent compatibility. See `docs/en/browser/custom-backend.mdx`.
- **Leaderboard results**: include benchmark, split, agent, model, browser backend, judge model,
  success rate, average steps, average latency, and artifacts.
- **Evaluation improvements**: explain how the judge strategy changes reproducibility or failure
  attribution.

## Result Submissions

Closed-source agents can be submitted. Official leaderboard entries require maintainer review
and maintainer rerun. Use `community/results/example/submission.json` as the metadata template,
and see `EVALUATION_PROTOCOL.md` for the full policy.

## Code Quality Handbook

These rules are binding for every contributor, human or coding agent. The full conventions live
in [docs_4_codeagent/](docs_4_codeagent/) and are the single source of truth; this section is
the short version that reviewers actually enforce. When in doubt, the detailed docs win.

这些规则对所有贡献者(人和 coding agent)一律生效。完整规范在 `docs_4_codeagent/`,以那里为准;
本节是 reviewer 实际执行的速查版,有疑问时以详细文档为准。

Full rules: [coding style](docs_4_codeagent/coding-style.md) ·
[architecture and boundaries](docs_4_codeagent/architecture-boundaries.md) ·
[imports, runtime, config](docs_4_codeagent/imports-runtime-config.md) ·
[error handling and testing](docs_4_codeagent/error-handling-testing.md)

### Hard rules

Runtime and configuration / 运行时与配置:

- Use `uv` with Python >= 3.11; never system Python. / 一律用 `uv`,禁止系统 Python。
- Do not hardcode timeouts, URLs, API keys, or model names; read them from `config.yaml`,
  environment variables, or config objects. / 禁止硬编码超时、URL、密钥、模型名。
- Use `from browseruse_bench.utils import REPO_ROOT`; never `Path(__file__).parents[N]` or
  `sys.path.insert()`. / 路径统一用 `REPO_ROOT`,禁止手工路径拼接和 `sys.path` 注入。

Logging and errors / 日志与异常:

- Use `logger`, never `print()`. / 日志用 `logger`,禁止 `print()`。
- Catch specific exceptions only; `except:` and `except Exception:` are rejected in review, and
  `pass`-swallowed errors are rejected too. / 只捕获具体异常,禁止裸 `except` 和静默吞错。
- Fail fast: do not wrap the normal path in broad defensive `try` blocks; catch only where
  there is a real recovery action. / 快速失败,只在有明确恢复动作的地方捕获。

Control flow and style / 控制流与风格:

- Imports at the top of the file, PEP 8 grouped. Function-local imports are allowed only in
  registry/router lazy-load factories. / import 一律放文件顶部,仅 registry 工厂例外。
- Guard clauses over nesting: max nesting depth 2, max 3 branches per `if/elif/else`, functions
  <= 40 lines, no `else` after `return`/`raise`/`continue`. / 用卫语句而不是深嵌套。
- Type hints plus `from __future__ import annotations`; no emojis in code or comments.
  / 函数要有类型标注;代码和注释里不要用 emoji。

Testing / 测试:

- Every behavior change ships with targeted tests (`uv run pytest tests/`).
  / 行为变更必须带针对性测试。
- Agent-touching changes additionally require a real smoke run — pytest and `--dry-run` do not
  count. See [smoke testing](docs_4_codeagent/error-handling-testing.md#smoke-testing-before-commit).
  / 涉及 agent 运行路径的改动必须真实跑通一次,pytest 和 dry-run 不算。

## Mandatory Self-Review Before Every PR

The automated review workflow cannot run on this public repository, so self-review is a hard
requirement, not a suggestion. A PR whose self-review checklist is not ticked, or that visibly
skipped these steps, will be returned without review.

自动 code review workflow 在 public 仓库上无法运行,因此自查是硬性要求而不是建议。PR 模板中的
自查项未勾选、或明显没做自查的 PR,reviewer 会不经 review 直接打回。

Before opening a PR, and again before pushing substantial updates to one:

1. **Read your own diff, every line.** Run `git diff main...HEAD` (or use the GitHub "Files
   changed" tab) and read it end to end. You should be able to explain why every changed line
   exists. Remove debug leftovers, commented-out code, and changes unrelated to this PR.
   / 逐行读完自己的 diff:每一行都要能解释为什么存在;删掉调试残留、注释掉的代码和无关改动。
2. **Check the diff against the hard rules above**, one section at a time.
   / 对照上面的硬性红线逐条检查。
3. **Run an automated local review.** Start `claude` in the repo and run `/code-review`, then
   fix every finding or justify it explicitly in the PR description. This step replaces the old
   PR-triggered review workflow. / 本地跑 `/code-review`,findings 逐条修复,不修的要在 PR
   描述里写明理由。这一步用于替代原来的自动 review workflow。
4. **Run the tests**: `uv run pytest tests/`, and add or update targeted tests for the changed
   behavior. / 跑测试并为行为变更补针对性测试。
5. **Smoke test agent-touching changes** with a real run, and put the command plus one log line
   proving real subprocess work in the PR description:
   / 涉及 agent 的改动要真实跑通,并把命令和证明日志贴进 PR:

   ```bash
   bubench run --agent <agent> --data LexBench-Browser --mode single
   ```

6. **Scan for secrets**: no API keys, cookies, tokens, or unredacted provider logs in the diff.
   / 检查 diff 中没有密钥、cookie、token 或未脱敏日志。

Then tick every applicable box in the PR template's self-review section.

**For reviewers and approvers**: do not approve a PR whose self-review checklist is unticked or
clearly not done — return it and ask the author to complete the steps first. Approving is
vouching for the code. / 给 approver:自查清单未勾选或明显没做的 PR 不要 approve,打回补做。
approve 意味着为这段代码背书。

## More Detail

- [English contribution guide](docs/en/development/contributing.mdx)
- [中文贡献指南](docs/zh/development/contributing.mdx)
- [Governance](GOVERNANCE.md)
- [Evaluation Protocol](EVALUATION_PROTOCOL.md)
