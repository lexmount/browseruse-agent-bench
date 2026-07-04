## Summary

Describe what changed and why.

## Contribution type

- [ ] Agent adapter
- [ ] Browser backend
- [ ] Benchmark task or data
- [ ] Leaderboard/result submission
- [ ] Evaluation or judge strategy
- [ ] Documentation/example
- [ ] Bug fix

## Reproduction or validation

List the commands you ran. For agent-touching changes, include a real smoke run:

```bash
bubench run --agent <agent> --data LexBench-Browser --mode single
```

## Result artifacts

For result submissions, link or attach the run directory, evaluation output, and redacted config.

## Self-review (mandatory)

See [Mandatory Self-Review](https://github.com/lexmount/browseruse-agent-bench/blob/main/CONTRIBUTING.md#mandatory-self-review-before-every-pr).
PRs with unticked boxes are returned without review. / 以下勾选项未完成的 PR 会被直接打回。

- [ ] I read every line of my diff and every change is intentional
      / 我逐行读完了自己的 diff,每处改动都是有意为之
- [ ] The diff passes the hard rules in CONTRIBUTING.md (logger not print, specific exceptions,
      no hardcoded config, imports at top) / diff 符合 CONTRIBUTING.md 的硬性红线
- [ ] I ran a local `/code-review` and fixed or justified every finding
      / 本地跑过 `/code-review`,findings 已全部修复或说明理由
- [ ] `uv run pytest tests/` passes and behavior changes have targeted tests
      / 测试通过,行为变更配了针对性测试
- [ ] Agent-touching change: real smoke run evidence is included above, or this PR does not
      touch any agent runtime path / 涉及 agent 运行路径的改动已附真实 smoke run 证据,或本
      PR 不涉及 agent 运行路径
- [ ] No secrets, cookies, or unredacted logs in the diff / diff 中无密钥、cookie、未脱敏日志

## Notes for reviewers

Call out compatibility risks, optional dependency changes, or follow-up work.
