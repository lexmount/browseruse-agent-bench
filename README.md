<p align="center">
  <img src="docs/logo/blue.svg" alt="Browseruse-Bench" width="600">
</p>

<p align="center">
  <a href="#">Paper</a> •
  <a href="#">Blog</a> •
  <a href="#leaderboard">Leaderboard</a> •
  <a href="https://docs.bubench.lexmount.io/">Documentation</a> •
  <a href="./benchmarks">Data</a>
</p>

<p align="center">
  English | <a href="./README_ZH.md">简体中文</a>
</p>

## Description


**browseruse-bench** is an all-in-one evaluation framework for AI browser agents, designed to benchmark *multiple agents across multiple benchmarks, browsers, and models* under controlled and reproducible settings. It supports both local and cloud browsers, integrates LLM-as-Judge for automated evaluation, and provides a built-in local leaderboard along with efficiency and cost metrics such as agent steps, end-to-end latency, and token usage.

**Supported Benchmarks**

- [x] **LexBench-Browser** — E-commerce, social, academic, financial, 50+ mainstream Chinese/English websites (v1.0, 2026-04-30)
  - `All` (210, no login required)
  - `lexmount` (118, mainland-accessible websites) / `global` (92, international websites)
- [x] **Online-Mind2Web** — Real website interaction tasks
  - `All` (300) / `Hard` (hard subset)
- [x] **BrowseComp** — Browser operation competition tasks, no login required
  - `All` (1266)
- [ ] More benchmarks

> Details: [Benchmarks overview](https://docs.bubench.lexmount.io/en/benchmarks/overview).

**Supported Agents & Browsers**

| Agent | Supported Browsers |
|-------|-------------------|
| [browser-use](https://github.com/browser-use/browser-use) | `Chrome-Local`, `lexmount`, `browser-use-cloud`, `agentbay` |
| [skyvern](https://github.com/Skyvern-AI/Skyvern/) | `local`, `lexmount`, `skyvern-cloud` |
| [Agent-TARS](https://github.com/bytedance/UI-TARS-desktop) | Built-in browser |
| More agents | — |

> Details: [Agents overview](https://docs.bubench.lexmount.io/en/agents/overview).

## News

- **[2026.04.30]** 🎉 **browseruse-bench v1.0** — initial open-source release. LexBench-Browser v1.0 ships 210 public tasks across 107 distinct websites with a 6-category × 16-tag robustness label system; reference integrations cover browser-use, skyvern, Agent-TARS and deepbrowse.

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
| `HF_ENDPOINT=https://hf-mirror.com` | HuggingFace mirror (China) | — | Optional |

**3.2 Runtime config (`config.yaml`)**

```bash
cp config.example.yaml config.yaml
vim config.yaml
```

All agents are configured in one file. Key fields under `agents.<agent>`:

| Field | Description |
|-------|-------------|
| `active_model` | Which model entry to use (must match a key under `models`) |
| `models.<name>.model_type` | Provider: `BROWSER_USE`, `OPENAI`, `AZURE`, `GEMINI`, `ANTHROPIC` |
| `models.<name>.model_id` | Model ID (e.g. `gpt-4.1`, `qwen3.5-plus`, `kimi-k2.5`) |
| `models.<name>.api_key` | API key for this model (supports `$ENV_VAR` expansion) |
| `models.<name>.base_url` | API base URL (optional, supports `$ENV_VAR` expansion) |
| `browser.browser_id` | Browser backend: `Chrome-Local`, `lexmount`, `browser-use-cloud`, `agentbay`, `cdp` |
| `defaults.*` | Shared agent params: `max_steps`, `timeout`, `use_vision`, etc. |
| `eval.model` + `eval.api_key` + `eval.base_url` | Evaluation model settings |

To switch models, change `active_model` and ensure the matching entry exists under `models`.

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
@misc{2026browseruse-bench,
    title={LexBench-Browser: A Comprehensive Benchmark for Evaluating Browser Agents in Real-World Scenarios},
    author={},
    year={2026},
    eprint={},
    archivePrefix={arXiv},
    primaryClass={cs.SE},
    url={https://arxiv.org/abs/},
}
```

## Contact


💬 Questions or thoughts? Hit me up via GitHub Issue / Discussion!

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
