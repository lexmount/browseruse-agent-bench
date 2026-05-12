---
language:
  - zh
  - en
license: cc-by-4.0
task_categories:
  - question-answering
  - text-generation
  - reinforcement-learning
pretty_name: LexBench-Browser
size_categories:
  - n<1K
tags:
  - browser-agent
  - web-agent
  - benchmark
  - evaluation
  - automation
---

# LexBench-Browser

LexBench-Browser is a public browser-agent dataset for evaluating agents on real-web workflows.
The v1.0 snapshot contains 210 no-login tasks across 107 distinct websites, with Chinese and
English instructions, task-level reference steps, key points, common mistakes, scoring rubrics,
and robustness tags.

Repository: https://github.com/lexmount/browseruse-agent-bench

Dataset page: https://huggingface.co/datasets/Lexmount/LexBench-Browser

Docs: https://docs.bubench.lexmount.io/

Contact: lexbench@lexmount.com

## Dataset Summary

LexBench-Browser is designed to support browser-agent engineering in browseruse-agent-bench:

- run an agent against real websites
- compare local and cloud browser backends
- evaluate task success with a declared judge strategy
- inspect trajectories and failure modes
- submit reproducible leaderboard results

The dataset does not require login for the v1.0 public snapshot.

## Files

```text
LexBench-Browser/
|-- data_info.json
|-- task.jsonl
|-- task_global.jsonl
|-- task_lexmount.jsonl
`-- VERSION_HISTORY.md
```

Splits:

| Split | File | Tasks | Notes |
| --- | --- | ---: | --- |
| `All` | `task.jsonl` | 210 | Default public v1.0 split |
| `global` | `task_global.jsonl` | 92 | Global-region task subset |
| `lexmount` | `task_lexmount.jsonl` | 118 | Lexmount-region task subset |

## Fields

Each JSONL row includes:

- `id`: stable task id
- `query`: user-facing browser task
- `task_type`: task type label
- `domain`: domain category
- `difficulty`: `easy`, `medium`, or `hard`
- `login_required`: whether login is required
- `risk_control`: whether the task has risk-control constraints
- `target_website`: intended website or website family
- `reasoning_type`: reasoning complexity label
- `language`: `zh` or `en`
- `website_region`: expected region/language context
- `reference_answer`: reference steps, key points, common mistakes, and scoring rubric
- `score_threshold`: pass threshold
- `robustness_tags`: practical browser-agent stressors

## Label Distribution

Language:

| Language | Tasks |
| --- | ---: |
| `zh` | 137 |
| `en` | 73 |

Reasoning type:

| Reasoning type | Tasks |
| --- | ---: |
| `single_step` | 117 |
| `multi_step` | 70 |
| `deep_analysis` | 23 |

Domain:

| Domain | Tasks |
| --- | ---: |
| `finance_gaming` | 44 |
| `video_platform` | 42 |
| `tools_education` | 40 |
| `general` | 34 |
| `social_lifestyle` | 26 |
| `ecommerce` | 23 |
| `gaming` | 1 |

## Robustness Tags

Tags cover:

- popup interference: `login_popup`, `cookie_consent`, `ad_overlay`
- sequence complexity: `long_sequence`, `deep_navigation`, `multi_site`
- content dynamics: `realtime_data`, `lazy_load_scroll`, `iframe_embed`
- anti-crawl behavior: `captcha_verification`, `anti_bot`, `rate_limiting`
- localization: `chinese_rendering`, `cross_language`
- complex interaction: `filter_sort`, `data_extraction`

## Usage

```bash
git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
uv sync --extra browser-use
uv run bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
```

For official leaderboard submissions, follow the repository evaluation protocol and result
submission docs.

## Licensing

The LexBench-Browser dataset metadata and task definitions are released under CC-BY 4.0.

This license covers the curated task records, labels, reference steps, scoring rubrics, and
metadata authored for the dataset. It does not relicense third-party website content,
screenshots, traces, marks, page text, or other artifacts collected while running agents.

Repository code is licensed separately under Apache-2.0.

## Limitations

- Real websites change over time, so tasks may become easier, harder, or temporarily unavailable.
- Some websites may show region-specific content, anti-bot interstitials, cookie banners, or
  localized layouts.
- The public v1.0 snapshot avoids login-required tasks.
- Automated judge results should be interpreted with the declared judge model, prompt strategy,
  benchmark version, and browser backend.

## Citation

Use the repository `CITATION.cff` for citation metadata.
