# LexBench-Browser Data Version History

## Version Overview

| Version | Date | Total Tasks | Description |
|---------|------|-------------|-------------|
| 1.0 | 2026-04-30 | 208 | Public release |

## v1.0 (2026-04-30)

First public release of the LexBench-Browser dataset.

- **208 tasks** across **107 distinct websites**, covering both Chinese and English mainstream sites (commerce, social/community, media/entertainment, finance/gaming, productivity/tools, education/research, local/lifestyle, and safety).
- **Two task types**: `T1` single-site information retrieval and `T2` multi-site operations.
- **Robustness label system**: 6 categories × 16 tags spanning popup interference, sequence complexity, content dynamics, anti-crawl behavior, localization, and complex interaction. See `data_info.json` for the full taxonomy.
- **Per-task scoring rubric**: every record carries `reference_answer.steps`, `key_points`, `common_mistakes`, and a 100-point `scoring.items` breakdown. The pass threshold is declared per task via `score_threshold`; there is no global default.
- **Slicing**: use the `login_required`, `domain`, `risk_control`, or `robustness_tags` fields directly. The dataset does not ship pre-baked tier splits.
- **Format**: single `task.jsonl` file alongside `data_info.json`.
