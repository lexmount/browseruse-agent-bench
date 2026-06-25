<p align="center">
  <img src="docs/logo/blue.svg" alt="Browseruse-Bench" width="600">
</p>

<p align="center">
  <a href="https://lexmount.github.io/browseruse-agent-bench/">项目主页</a> •
  <a href="https://github.com/lexmount/browseruse-agent-bench/issues">Issues</a> •
  <a href="https://github.com/lexmount/browseruse-agent-bench/discussions">Discussions</a> •
  <a href="#排行榜">排行榜</a> •
  <a href="https://docs.bubench.lexmount.io/">文档</a> •
  <a href="https://huggingface.co/datasets/Lexmount/LexBench-Browser">数据集</a>
</p>

<p align="center">
  <a href="./README.md">English</a> | 简体中文
</p>

## 为什么值得关注 browseruse-agent-bench

**browseruse-agent-bench** 是面向浏览器 Agent 的可复现评测框架。
**LexBench-Browser** 是其中内置的公开数据集，也是默认 benchmark workflow 使用的数据。
两者配合，让外部团队可以运行、比较、引用，并通过 PR 提交自己的 Agent、任务和结果。

| 你可以做什么 | 为什么重要 |
| ------------ | ---------- |
| 运行 **LexBench-Browser：210 个公开任务，覆盖 107 个真实网站** | 在长尾、多语言真实网页工作流上测试浏览器 Agent |
| 比较 **Agent × Model × Browser × Eval** | 区分 Agent 能力、模型选择、浏览器后端和评测策略的影响 |
| 查看排行榜、成本、延迟、Token 使用量和轨迹 | 不只看最终分数，也能定位失败原因 |
| 提交 Agent、数据集任务和可复现实验结果 | 让 fork 和 PR 直接变成可见的 benchmark 贡献 |

## 简介

**browseruse-agent-bench** 是一个针对 AI 浏览器 Agent 的全能评估框架，旨在*受控且可复现的设置下，跨多个数据集、浏览器后端和模型*对*多个 Agent* 进行评测。Python package/CLI 名称为 **browseruse-bench** 和 `bubench`。它支持本地和云端浏览器，集成了 LLM-as-Judge 进行自动化评估，并提供内置的本地排行榜以及 Agent 步骤数、端到端延迟和 Token 使用量等效率与成本指标。

**支持的数据集**

- **LexBench-Browser** — 浏览器 Agent 数据集，覆盖电商、社交、学术、金融等主流中英文网站（v1.0，2026-04-30）
  - `All`（210，无需登录）
  - `lexmount`（118，国内可访问网站）/ `global`（92，国外网站）
  - Hugging Face：[Lexmount/LexBench-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Browser)
- **Online-Mind2Web** — 真实网站交互任务
  - `All`（300）/ `Hard`（困难子集）
- **BrowseComp** — 浏览器操作竞赛任务，无需登录
  - `All`（1266）
- 更多数据集

> 详情见[基准测试总览](https://docs.bubench.lexmount.io/zh/benchmarks/overview)。

**支持的 Agent 与浏览器**


| Agent                                                      | 支持的浏览器                                                   |
| ---------------------------------------------------------- | -------------------------------------------------------- |
| [browser-use](https://github.com/browser-use/browser-use)  | `Chrome-Local`、`lexmount`、`browser-use-cloud`、`agentbay` |
| [skyvern](https://github.com/Skyvern-AI/Skyvern/)          | `local`、`lexmount`、`skyvern-cloud`                       |
| [Agent-TARS](https://github.com/bytedance/UI-TARS-desktop) | 内置浏览器                                                    |
| 更多 Agent                                                   | —                                                        |

> 详情见 [Agents 总览](https://docs.bubench.lexmount.io/zh/agents/overview)。

## 新闻

- **[2026.04.30]** 🎉 **browseruse-agent-bench v1.0** —— 首个开源版本发布。LexBench-Browser 数据集 v1.0 包含 210 个公开任务，覆盖 107 个真实网站，搭配 6 大类 / 16 个标签的鲁棒性标签体系；参考集成覆盖 browser-use、skyvern、Agent-TARS、deepbrowse。

## 快速开始

**1. 克隆仓库**

```bash
git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
```

**2. 安装依赖 (Python>=3.11)**

需要 [uv](https://docs.astral.sh/uv/)（推荐），根据使用的 Agent 选择对应小节。

> **注意**: `browser-use` 与 `skyvern` 的依赖存在冲突，不可同时安装。若需并行运行多个 Agent，请参考文档中的[环境隔离](https://docs.bubench.lexmount.io/zh/quickstart#running-multiple-agents-in-parallel)方案。

**browser-use**

```bash
uv sync --extra browser-use
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell
```

**skyvern**

```bash
uv sync --extra skyvern
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell
```

**Agent-TARS**（需要 Node.js 18+）

```bash
uv sync
npm install -g @agent-tars/cli@0.3.0
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell
```

**CLI agents: claude-code / codex / cursor / openclaw**（需要 Node.js 18+；**openclaw 需要 Node.js 22.19+**）

这些 agent 共用主 venv（`uv sync`,无 extra），并依赖单独安装的外部 CLI：

```bash
uv sync
npm install -g @anthropic-ai/claude-code     # claude-code（认证：ANTHROPIC_API_KEY）
npm install -g @openai/codex                 # codex（认证：codex login 或 OPENAI_API_KEY）
curl https://cursor.com/install -fsS | bash  # cursor（认证：CURSOR_API_KEY）
export PATH="$HOME/.local/bin:$PATH"         # cursor-agent 安装在 ~/.local/bin
npm install -g openclaw                      # openclaw（无需登录；key 通过 config.yaml 注入）
```

认证细节、服务器/无头部署注意事项（含 Docker 镜像要求）及逐 agent 冒烟验证见
[docs/cli-agents-deployment.md](docs/cli-agents-deployment.md)。

> 激活 venv 后 `bubench` CLI 就进入了 PATH。若未激活，请在后续步骤的所有 `bubench …` 命令前加上 `uv run`（如 `uv run bubench run …`）。

**3. 配置**

> **原则**：`.env` 存放敏感凭证（API Key）；`config.example.yaml` → `config.yaml`（git 已忽略）集中管理所有 Agent 的模型、浏览器和评估配置。

**3.1 共享凭证（`.env`）**

```bash
cp .env.example .env
vim .env
```


| 变量                                         | 说明                             | 申请地址                                                        | 必填                  |
| ------------------------------------------ | ------------------------------ | ----------------------------------------------------------- | ------------------- |
| `OPENAI_API_KEY`                           | Agent 与评估用 API Key             | [platform.openai.com](https://platform.openai.com/api-keys) | ✅                   |
| `OPENAI_BASE_URL`                          | 自定义 API Base URL（如 LiteLLM 代理） | —                                                           | 可选                  |
| `LEXMOUNT_API_KEY` + `LEXMOUNT_PROJECT_ID` | lexmount 云端浏览器                 | [browser.lexmount.cn](https://browser.lexmount.cn/)         | 使用 lexmount 时       |
| `BROWSER_USE_API_KEY`                      | Browser Use 云端浏览器              | [browser-use.com](https://www.browser-use.com/)             | 使用 browser-use-cloud 时 |
| `AGENTBAY_API_KEY`                         | AgentBay 云端浏览器                 | [agentbay.ai](https://agentbay.ai/)                         | 使用 agentbay 时       |
| `HF_ENDPOINT=https://hf-mirror.com`        | HuggingFace 加速（国内）             | —                                                           | 可选                  |


**3.2 运行配置（`config.yaml`）**

```bash
cp config.example.yaml config.yaml
vim config.yaml
```

所有 Agent 统一在一个文件中配置。`agents.<agent>` 下的关键字段：


| 字段                                              | 说明                                                                   |
| ----------------------------------------------- | -------------------------------------------------------------------- |
| `active_model`                                  | 当前使用的模型名称（须与 `models` 下的 key 对应）                                     |
| `models.<name>.model_type`                      | 提供商：`BROWSER_USE`、`OPENAI`、`AZURE`、`GEMINI`、`ANTHROPIC`              |
| `models.<name>.model_id`                        | 模型 ID（如 `gpt-4.1`、`qwen3.5-plus`、`kimi-k2.5`）                        |
| `models.<name>.api_key`                         | 该模型的 API Key（支持 `$ENV_VAR` 展开）                                       |
| `models.<name>.base_url`                        | API Base URL（可选，支持 `$ENV_VAR` 展开）                                    |
| `browser.browser_id`                            | 浏览器后端：`Chrome-Local`、`lexmount`、`browser-use-cloud`、`agentbay`、`cdp` |
| `defaults.*`                                    | Agent 公共参数：`max_steps`、`timeout`、`use_vision` 等                      |
| `eval.model` + `eval.api_key` + `eval.base_url` | 评估模型配置                                                               |


切换模型只需修改 `active_model`，并确保 `models` 下有对应条目。

**4. 安装 Skills（可选）**

```bash
bubench skills
```

将预置的开发者友好 Skills 包（`browseruse_bench/skills/`）安装到 Agent 工具链中。

**5. 运行 & 评估**

**运行**

```bash
bubench run --agent {AGENT} --data {BENCHMARK} --mode first_n --count 3
# 结果输出至: experiments/{benchmark}/{split}/{agent}/{model_id}/{timestamp}/

# 示例：LexBench-Browser（无需登录）
bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
# 结果输出至: experiments/LexBench-Browser/All/browser-use/gpt-4.1/20260101_120000/
```

**评估**

```bash
bubench eval --agent {AGENT} --data {BENCHMARK} --model-id {MODEL_ID}

# 示例
bubench eval --agent browser-use --data LexBench-Browser --model-id gpt-4.1
```

> `--split` 为可选参数：默认使用 `data_info.json` 中的 `default_split`；只有当需要覆盖默认值时再传 `--split <name>`。

> 全量参数说明见[快速开始文档](https://docs.bubench.lexmount.io/zh/quickstart)。

**Post-attribution 重测流程**

LexBench-Browser 结果分析推荐使用这套自动化 post-run 流程：

```text
run benchmark -> hard artifact pre-check -> eval remaining results
-> failure attribution excluding hard-hit tasks -> post-attribution rerun check
-> rerun selected tasks -> re-eval -> final attribution / visualization
```

最终 rerun candidate 集合是：

```text
hard_artifact_rerun
∪ taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks
```

先收集确定性的 hard failures，这部分可以直接进入 rerun，并从 judge 调用中排除：

```bash
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL_DIR_NAME \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --out-dir experiments/LexBench-Browser/All/browser-use/MODEL_DIR_NAME/TIMESTAMP/rerun_candidates_hard
```

然后对 non-hard tasks 跑 eval / failure attribution，再生成最终 rerun task ids：

```bash
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL_DIR_NAME \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --include-taxonomy-web-constraints
```

详见 [LexBench 自动化评测体系](docs/lexbench-automated-evaluation-system.md)、
[rerun check rules](docs/result-rerun-check-rules.md) 和
[12-model rerun rule validation](docs/rerun-rule-validation-12-models.md)。

## 数据加载

通过 `--data-source` 控制数据来源：


| 模式                                 | 说明                                              | 命令示例                                         |
| ---------------------------------- | ----------------------------------------------- | -------------------------------------------- |
| `local`（默认）                        | 使用本地 `benchmarks/{benchmark}/data/` 下的文件，不存在则报错 | `--data-source local`                        |
| `huggingface`                      | 从 HuggingFace 下载到 `~/.cache/huggingface`，不写回仓库  | `--data-source huggingface`                  |
| `huggingface` + `--force-download` | 强制重新下载，刷新 HF 缓存                                 | `--data-source huggingface --force-download` |


> **国内用户提速**：在 `.env` 中设置 `HF_ENDPOINT=https://hf-mirror.com`。
> **私有数据集**：需在 `.env` 中设置 `HF_TOKEN=hf_your_token_here`。

详情见[数据加载](https://docs.bubench.lexmount.io/zh/benchmarks/data-loading)。

> 📖 查看完整指南、API 参考和更多示例，请访问[完整文档](https://docs.bubench.lexmount.io/)。

## 排行榜

我们提供交互式本地排行榜来比较不同 Agent 在各基准上的表现。

生成排行榜 HTML：

```bash
bubench leaderboard
```

部署排行榜服务（临时进程）：

```bash
bubench server --host 0.0.0.0 --port 8012 &
```

部署排行榜服务（systemd）：

```bash
sudo bubench service install
sudo bubench service start
```

查看[排行榜文档](https://docs.bubench.lexmount.io/zh/leaderboard/overview)了解更多详情。

**访问地址（默认端口 `8012`）：**
- 本地排行榜: [http://localhost:8012](http://localhost:8012)
- 本地 API 文档: [http://localhost:8012/docs](http://localhost:8012/docs)
- 远程排行榜: `http://<SERVER_IP>:8012/`
- 远程 API 文档: `http://<SERVER_IP>:8012/docs`

## 可视化工具

交互式实验浏览器，支持逐任务浏览 Agent 轨迹、评测详情和 API 日志——对静态排行榜的补充，提供任务级别深度分析。

```bash
# 启动服务器（文件变化时自动重新生成索引）
bubench viz --watch

# 访问 http://localhost:8080
```

**参数说明：**

```bash
bubench viz --port 8090              # 自定义端口（默认 8080）
bubench viz --generate-only          # 仅生成 experiments.json 后退出
bubench viz --watch-interval 5       # 轮询间隔秒数（默认 3）
```

远程服务器 tmux 部署及防火墙配置，参见[可视化工具文档](https://docs.bubench.lexmount.io/zh/leaderboard/visualization#内网共享)。

## 致谢

本项目引用并修改了来自 [Online-Mind2Web](https://github.com/OSU-NLP-Group/Online-Mind2Web) 和 [simple-evals](https://github.com/openai/simple-evals) 的部分代码。

## 引用

```bibtex
@misc{lexbench_browser_2026,
    title        = {LexBench-Browser: A Real-World Browser Agent Benchmark with Long-Tail and Multilingual Tasks},
    author       = {Lexmount Research and Collaborators},
    year         = {2026},
    howpublished = {\url{https://lexmount.github.io/browseruse-agent-bench/}},
    note         = {Open benchmark; v1.0 reference release},
}
```

## 联系我们

欢迎提交问题、Benchmark 提案、Agent 集成和可复现实验结果：

- Bug 和功能请求请发到 [GitHub Issues](https://github.com/lexmount/browseruse-agent-bench/issues)。
- 问题讨论和结果交流请发到 [GitHub Discussions](https://github.com/lexmount/browseruse-agent-bench/discussions)。
- official result、数据集和合作问题可发邮件至 [lexbench@lexmount.com](mailto:lexbench@lexmount.com)。
- 后续版本计划见 [Milestones](https://github.com/lexmount/browseruse-agent-bench/milestones)。
- 提交 PR 或新增 Agent/Benchmark 前，请参考 [Contributing](./CONTRIBUTING.md)。
- 结果审核规则见 [Governance](./GOVERNANCE.md) 和 [Evaluation Protocol](./EVALUATION_PROTOCOL.md)。

## 即将推出

- 🔐 **登录态保持** —— 原生支持跨评测复用浏览器登录态，需登录任务无需每次人工重登即可跑通。敬请期待。

## Roadmap/ Development Plan

有关后续版本计划和截止时间，请参考我们的 [Milestones](https://github.com/lexmount/browseruse-agent-bench/milestones)。

## Star 历史



<a href="https://star-history.com/#lexmount/browseruse-agent-bench&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date" />
 </picture>
</a>
