# LexBench-Browser Data Version History

## Version Overview

| Version | Date | Total Tasks | Description |
|---------|------|-------------|-------------|
| 1.0 | 2026-04-30 | 210 | Initial public release |

## v1.0 (2026-04-30)

First public release of the LexBench-Browser dataset.

- **210 tasks** across **107 distinct websites**, covering both Chinese and English mainstream sites (e-commerce, social, video, finance/gaming, tools/education, general).
- **Two task types**: `T1` single-site information retrieval and `T2` multi-site operations.
- **Robustness label system**: 6 categories × 16 tags spanning popup interference, sequence complexity, content dynamics, anti-crawl behavior, localization, and complex interaction. See `data_info.json` for the full taxonomy.
- **Per-task scoring rubric**: every record carries `reference_answer.steps`, `key_points`, `common_mistakes`, and a 100-point `scoring.items` breakdown. The pass threshold is declared per task via `score_threshold`; there is no global default.
- **Slicing**: use the `login_required`, `domain`, `risk_control`, or `robustness_tags` fields directly. The dataset does not ship pre-baked tier splits.
- **Format**: single `task.jsonl` file alongside `data_info.json`.

## v1.0 RPA (2026-05-20)

RPA extension: 10 real-world enterprise automation tasks.

- **10 tasks** targeting 6 real web systems.
- **6 Odoo demo tasks** covering Accounting, Inventory, CRM, and Documents modules.
- **4 external-site tasks**: Amazon (e-commerce), Trip.com (travel), GitHub (tech), Yahoo Finance + npm (finance/tech).
- **Scoring**: 100-point rubrics with itemized breakdowns, thresholds 60-70.
- **Format**: `task_rpa.jsonl`, registered as `"rpa"` split in `data_info.json`.
