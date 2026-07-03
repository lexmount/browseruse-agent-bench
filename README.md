<p align="center">
  <img src="docs/logo/blue.svg" alt="Browseruse-Bench" width="600">
</p>

<p align="center">
  <a href="https://lexmount.github.io/browseruse-agent-bench/">Landing Page</a> •
  <a href="https://github.com/lexmount/browseruse-agent-bench/issues">Issues</a> •
  <a href="https://github.com/lexmount/browseruse-agent-bench/discussions">Discussions</a> •
  <a href="#leaderboard">Leaderboard</a> •
  <a href="https://docs.bubench.lexmount.io/">Documentation</a> •
  <a href="https://huggingface.co/datasets/Lexmount/LexBench-Browser">Dataset</a>
</p>

<p align="center">
  English | <a href="./README_ZH.md">简体中文</a>
</p>

## Why browseruse-agent-bench

**browseruse-agent-bench** is a reproducible evaluation framework for browser agents.
**LexBench-Browser** is the built-in public dataset used by the default benchmark workflow.
Together they make external results easy to run, compare, cite, and submit back.

| What you can do | Why it matters |
|-----------------|----------------|
| Run **LexBench-Browser: 210 public tasks across 107 real websites** | Test browser agents on long-tail multilingual workflows beyond toy pages |
| Compare **Agent × Model × Browser × Eval** | Separate agent quality from model choice, browser backend, and judge strategy |
| Inspect leaderboard, cost, latency, token usage, and trajectories | Debug failures instead of only reporting a final score |
| Submit agents, dataset tasks, and reproducible results | Turn forks and PRs into visible benchmark contributions |

## Description


**browseruse-agent-bench** is an all-in-one evaluation framework for AI browser agents, designed to benchmark *multiple agents across multiple datasets, browser backends, and models* under controlled and reproducible settings. The Python package/CLI is published as **browseruse-bench** and `bubench`. It supports both local and cloud browsers, integrates LLM-as-Judge for automated evaluation, and provides a built-in local leaderboard along with efficiency and cost metrics such as agent steps, end-to-end latency, and token usage.

**Supported Datasets**

- [x] **LexBench-Browser** — Browser-agent dataset covering e-commerce, social, academic, financial, and other mainstream Chinese/English websites (v1.0, 2026-04-30)
  - `All` (210, no login required)
  - `lexmount` (118, mainland-accessible websites) / `global` (92, international websites)
  - Hugging Face: [Lexmount/LexBench-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Browser)
- [x] **Online-Mind2Web** — Real website interaction tasks
  - `All` (300) / `Hard` (hard subset)
- [x] **BrowseComp** — Browser operation competition tasks, no login required
  - `All` (1266)
- [ ] More benchmarks

> Details: [Benchmarks overview](https://docs.bubench.lexmount.io/en/benchmarks/overview).

**Supported Agents & Browsers**

| Agent | Supported Browsers |
|-------|-------------------|
| [browser-use](https://github.com/browser-use/browser-use) | `Chrome-Local`, `lexmount`, `browser-use-cloud`, `agentbay`, `browserbase`, `browserless`, `steel` |
| [skyvern](https://github.com/Skyvern-AI/Skyvern/) | `local`, `lexmount`, `skyvern-cloud`, `agentbay`, `browserbase`, `browserless`, `steel` |
| [Agent-TARS](https://github.com/bytedance/UI-TARS-desktop) | Built-in browser |
| More agents | — |

> Details: [Agents overview](https://docs.bubench.lexmount.io/en/agents/overview).

## News

- **[2026.04.30]** 🎉 **browseruse-agent-bench v1.0** — initial open-source release. The LexBench-Browser dataset v1.0 ships 210 public tasks across 107 distinct websites with a 6-category × 16-tag robustness label system; reference integrations cover browser-use, skyvern, Agent-TARS and deepbrowse.

## Quickstart


**1. Clone the repository**

```bash
git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
```

**2. Install dependencies (Python>=3.11)**

Requires [uv](https://docs.astral.sh/uv/) (recommended). Select the section for your agent.

> **Note**: `browser-use` and `skyvern` have conflicting dependencies and cannot be installed together. If you plan to run multiple agents in parallel, refer to the [Environment Isolation](https://docs.bubench.lexmount.io/en/quickstart#running-multiple-agents-in-parallel) section in the documentation.

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

**Agent-TARS** (requires Node.js 18+)

```bash
uv sync
npm install -g @agent-tars/cli@0.3.0
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell
```

**CLI agents: claude-code / codex / cursor / openclaw** (requires Node.js 18+; **openclaw requires Node.js 22.19+**)

These agents share the main venv (`uv sync`, no extra) and drive an external
CLI installed separately:

```bash
uv sync
npm install -g @anthropic-ai/claude-code     # claude-code (auth: ANTHROPIC_API_KEY)
npm install -g @openai/codex                 # codex (auth: codex login or OPENAI_API_KEY)
curl https://cursor.com/install -fsS | bash  # cursor (auth: CURSOR_API_KEY)
export PATH="$HOME/.local/bin:$PATH"         # cursor-agent installs to ~/.local/bin
npm install -g openclaw                      # openclaw (no login; key via config.yaml)
```

See [docs/cli-agents-deployment.md](docs/cli-agents-deployment.md) for
authentication details, server/headless deployment notes (incl. Docker image
requirements), and per-agent smoke verification.

> After activation, the `bubench` CLI is available on your PATH. Without activation, prefix every `bubench …` command in the following steps with `uv run` (e.g. `uv run bubench run …`).

**3. Configure**

> **Principle**: `.env` holds sensitive credentials (API keys). `config.example.yaml` → `config.yaml` (git-ignored) holds all agent, model, browser, and eval settings in one place.

**3.1 Shared credentials (`.env`)**

```bash
cp .env.example .env
vim .env
```

| Variable | Description | Sign up | Required |
|----------|-------------|---------|----------|
| `OPENAI_API_KEY` | API key for agents and evaluation | [platform.openai.com](https://platform.openai.com/api-keys) | ✅ |
| `OPENAI_BASE_URL` | Custom API base URL (e.g. LiteLLM proxy) | — | Optional |
| `LEXMOUNT_API_KEY` + `LEXMOUNT_PROJECT_ID` | Lexmount cloud browser | [browser.lexmount.cn](https://browser.lexmount.cn/) | When using lexmount |
| `BROWSER_USE_API_KEY` | Browser Use cloud browser | [browser-use.com](https://www.browser-use.com/) | When using browser-use-cloud |
| `AGENTBAY_API_KEY` | AgentBay cloud browser | [agentbay.ai](https://agentbay.ai/) | When using agentbay |
| `BROWSERBASE_API_KEY` + optional `BROWSERBASE_PROJECT_ID` | Browserbase cloud browser | [browserbase.com](https://www.browserbase.com/) | When using browserbase |
| `BROWSERLESS_API_KEY` | Browserless BaaS cloud browser | [browserless.io](https://www.browserless.io/) | When using browserless |
| `STEEL_API_KEY` | Steel.dev cloud browser | [steel.dev](https://steel.dev/) | When using steel |
| `HF_ENDPOINT=https://hf-mirror.com` | HuggingFace mirror (China) | — | Optional |

**3.2 Runtime config (`config.yaml`)**

```bash
cp config.example.yaml config.yaml
vim config.yaml
```

All agents are configured in one file. Runtime config is resolved as:
`agents.<agent> + models.<model> + browsers.<browser>`.

| Field | Description |
|-------|-------------|
| `default.model` | Default model key (overridden by `--model`) |
| `default.browser` | Default browser key (overridden by `--browser-id`) |
| `agents.<agent>.*` | Agent params: `max_steps`, `timeout`, `use_vision`, etc. |
| `models.<name>.model_type` | Provider: `BROWSER_USE`, `OPENAI`, `AZURE`, `GEMINI`, `ANTHROPIC` |
| `models.<name>.model_id` | Model ID (e.g. `gpt-4.1`, `qwen3.5-plus`, `kimi-k2.5`) |
| `models.<name>.api_key` | API key for this model (supports `$ENV_VAR` expansion) |
| `models.<name>.base_url` | API base URL (optional, supports `$ENV_VAR` expansion) |
| `browsers.<name>.browser_id` | Browser backend: `Chrome-Local`, `lexmount`, `browser-use-cloud`, `agentbay`, `browserbase`, `browserless`, `steel`, `cdp` |
| `eval.model` + `eval.api_key` + `eval.base_url` | Evaluation model settings |

To switch per run, use `--model <name>` and `--browser-id <name>`.

**4. Install Skills (Optional)**

```bash
bubench skills
```

Installs the prebuilt developer-friendly skills pack (`browseruse_bench/skills/`) into your agent toolchain.

**5. Run & Evaluate**

**Run**
```bash
bubench run --agent {AGENT} --data {BENCHMARK} --mode first_n --count 3
# Output: experiments/{benchmark}/{split}/{agent}/{model_id}/{timestamp}/

# Example: LexBench-Browser (no login required)
bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
# Output: experiments/LexBench-Browser/All/browser-use/gpt-4.1/20260101_120000/
```

**Evaluate**
```bash
bubench eval --agent {AGENT} --data {BENCHMARK} --model-id {MODEL_ID}

# Example
bubench eval --agent browser-use --data LexBench-Browser --model-id gpt-4.1
```

> `--split` is optional — the benchmark's `default_split` (from `data_info.json`) is used automatically. Pass `--split <name>` only to override the default.
> For the full parameter reference, see the [Quickstart docs](https://docs.bubench.lexmount.io/en/quickstart).

## Data Loading

Use `--data-source` to control where benchmark data is loaded from:

| Mode | Description | Example |
|------|-------------|---------|
| `local` (default) | Uses local files under `benchmarks/{benchmark}/data/`, errors if missing | `--data-source local` |
| `huggingface` | Downloads to HF cache (`~/.cache/huggingface`), does not write back to repo | `--data-source huggingface` |
| `huggingface` + `--force-download` | Forces re-download, refreshes HF cache | `--data-source huggingface --force-download` |

> **Speed up in China**: Set `HF_ENDPOINT=https://hf-mirror.com` in `.env`.
> **Private datasets**: Set `HF_TOKEN=hf_your_token_here` in `.env`.

Details: [Data Loading](https://docs.bubench.lexmount.io/en/benchmarks/data-loading).

> 📖 For complete guides, API reference, and more examples, see the [full documentation](https://docs.bubench.lexmount.io/).

## Leaderboard

We provide an interactive local leaderboard to compare agent performance across benchmarks.

Generate leaderboard HTML:
```bash
bubench leaderboard
```

Deploy leaderboard service (temporary process):
```bash
bubench server --host 0.0.0.0 --port 8012 &
```

Deploy leaderboard service (systemd):
```bash
sudo bubench service install
sudo bubench service start
```

See [Leaderboard Documentation](https://docs.bubench.lexmount.io/en/leaderboard/overview) for more details.

**Access URLs (default port `8012`):**
- Local leaderboard: [http://localhost:8012](http://localhost:8012)
- Local API docs: [http://localhost:8012/docs](http://localhost:8012/docs)
- Remote leaderboard: `http://<SERVER_IP>:8012/`
- Remote API docs: `http://<SERVER_IP>:8012/docs`

## Visualization

An interactive experiment explorer for browsing agent trajectories, evaluation details, and per-task API logs — complements the static leaderboard with task-level drill-down.

```bash
# Start server (auto-regenerates index when experiment files change)
bubench viz --watch

# Access at http://localhost:8080
```

**Options:**

```bash
bubench viz --port 8090              # custom port (default: 8080)
bubench viz --generate-only          # regenerate experiments.json and exit
bubench viz --watch-interval 5       # poll interval in seconds (default: 3)
```

For remote sharing with tmux and firewall configuration, see [Visualization Documentation](https://docs.bubench.lexmount.io/en/leaderboard/visualization#remote--intranet-sharing).

## Acknowledgements

Some code in this project is cited and modified from [Online-Mind2Web](https://github.com/OSU-NLP-Group/Online-Mind2Web) and [simple-evals](https://github.com/openai/simple-evals).

## Citation


```bibtex
@misc{lexbench_browser_2026,
    title        = {LexBench-Browser: A Real-World Browser Agent Benchmark with Long-Tail and Multilingual Tasks},
    author       = {Lexmount Research and Collaborators},
    year         = {2026},
    howpublished = {\url{https://lexmount.github.io/browseruse-agent-bench/}},
    note         = {Open benchmark; v1.0 reference release},
}
```

## Contact


Questions, benchmark proposals, agent integrations, and result reproductions are welcome:

- Report bugs or request features in [GitHub Issues](https://github.com/lexmount/browseruse-agent-bench/issues).
- Ask questions and discuss results in [GitHub Discussions](https://github.com/lexmount/browseruse-agent-bench/discussions).
- Email official result, dataset, and collaboration questions to [lexbench@lexmount.com](mailto:lexbench@lexmount.com).
- Track upcoming releases in [Milestones](https://github.com/lexmount/browseruse-agent-bench/milestones).
- Use [Contributing](./CONTRIBUTING.md) when opening pull requests or adding a new agent/benchmark.
- See [Governance](./GOVERNANCE.md) and [Evaluation Protocol](./EVALUATION_PROTOCOL.md) for result review rules.

## Coming Soon

- 🔐 **Login-state preservation** — first-class support for reusing browser login across eval runs, so login-gated tasks can be benchmarked end-to-end without manual re-login. Stay tuned.

## Roadmap/ Development Plan

Refer to our [Milestones](https://github.com/lexmount/browseruse-agent-bench/milestones) for upcoming versions and deadlines.


## Star History



<a href="https://star-history.com/#lexmount/browseruse-agent-bench&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=lexmount/browseruse-agent-bench&type=Date" />
 </picture>
</a>
