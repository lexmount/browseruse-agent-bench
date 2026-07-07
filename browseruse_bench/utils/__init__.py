"""Core utilities for browseruse_bench

Unified export entry point for common utility functions and classes.
"""
from __future__ import annotations

from browseruse_bench.utils.browsecomp_core import (
    QUERY_TEMPLATE,
    decrypt,
    derive_key,
)
from browseruse_bench.utils.cli import (
    add_common_task_args,
    add_eval_args,
    create_base_agent_parser,
    create_eval_parser,
    create_run_parser,
    handle_cli_errors,
)
from browseruse_bench.utils.config_loader import (
    apply_skyvern_env,
    canonicalize_skyvern_model_name,
    get_default_split,
    get_default_version,
    load_agent_config_from_path,
    load_agent_registry,
    load_config_file,
    load_data_info,
    load_eval_config,
    normalize_agent_name,
    normalize_benchmark_name,
    normalize_split_name,
    resolve_agent_entry,
    resolve_agent_inline_config,
    resolve_dir_name_case_insensitive,
    resolve_output_model_id,
    resolve_split,
)
from browseruse_bench.utils.constants import (
    BENCHMARKS_DIR,
    CONFIG_PATH_REL,
    CONFIG_YAML,
    DATA_DIR,
    DATA_INFO_JSON,
    DEFAULT_BENCHMARK_NAME,
    DEFAULT_TASKS_REL_PATH,
    EXPERIMENTS_DIR,
    IS_WINDOWS,
    RESULT_JSON,
    TASKS_DIR,
    TASKS_EVAL_RESULT_DIR,
    TRAJECTORY_DIR,
)
from browseruse_bench.utils.data_loader import DataSource, load_dataset_file
# Eval helpers re-exported directly from browseruse_bench.eval (avoids circular
# import via utils.eval → eval.summary → utils.stats → utils/__init__.py).
from browseruse_bench.eval.failure import classify_failure_case, classify_failures_batch
from browseruse_bench.eval.model import EvaluationModel, encode_image, load_evaluation_model
from browseruse_bench.eval.score import calculate_success, extract_score_from_response
from browseruse_bench.eval.summary import (
    aggregate_evaluation_costs,
    calculate_evaluation_cost,
    dedupe_records_keep_newest,
    normalized_results_file,
)
from browseruse_bench.utils.image_utils import (
    decode_base64_to_file,
    extract_base64_from_content_item,
    strip_base64_prefix,
)
from browseruse_bench.utils.json_io import load_task_file
from browseruse_bench.utils.logger import add_file_handler, setup_logger
from browseruse_bench.utils.parse_utils import find_key_recursive, load_json_records, safe_int
from browseruse_bench.utils.prompt_loader import (
    load_prompt,
    make_prompt_ref,
    make_template_prompt,
    make_text_prompt,
)

# Project Root Directory
from browseruse_bench.utils.repo_root import REPO_ROOT
from browseruse_bench.utils.stats import (
    calculate_all_metrics_stats,
    calculate_failure_category_stats,
    calculate_metric_stats,
    filter_tasks_by_label,
)
from browseruse_bench.eval.summary import generate_evaluation_summary  # re-export
from browseruse_bench.utils.task import (
    filter_completed_tasks,
    filter_tasks,
    filter_tasks_by_region,
    is_browsecomp_benchmark,
    is_task_completed_by_result_json,
    load_tasks,
    load_tasks_with_benchmark_support,
    print_task_summary,
    resolve_tasks_json_path,
)
from browseruse_bench.utils.utils import (
    check_uv_available,
    find_latest_tasks_dir,
    get_env_var,
    load_env_file,
    resolve_timeout_value,
)
from browseruse_bench.utils.venv import (
    ensure_venv,
    install_agent_dependencies,
    resolve_agent_venv_path,
)

__all__ = [
    # Project Root
    "REPO_ROOT",
    # CLI
    "handle_cli_errors",
    "add_common_task_args",
    "add_eval_args",
    "create_run_parser",
    "create_eval_parser",
    "create_base_agent_parser",
    # Tasks
    "load_tasks",
    "load_tasks_with_benchmark_support",
    "filter_tasks",
    "filter_tasks_by_region",
    "is_browsecomp_benchmark",
    "is_task_completed_by_result_json",
    "resolve_tasks_json_path",
    "filter_completed_tasks",
    "print_task_summary",
    # Data loader
    "DataSource",
    "load_dataset_file",
    "load_task_file",
    "decrypt",
    "QUERY_TEMPLATE",
    "derive_key",
    # Config (includes agent config functions)
    "load_config_file",
    "load_eval_config",
    "load_agent_registry",
    "resolve_agent_entry",
    "resolve_agent_inline_config",
    "resolve_output_model_id",
    "load_agent_config_from_path",
    "apply_skyvern_env",
    "canonicalize_skyvern_model_name",
    "resolve_agent_venv_path",
    # Stats
    "calculate_metric_stats",
    "calculate_all_metrics_stats",
    "filter_tasks_by_label",
    "generate_evaluation_summary",
    # Venv
    "ensure_venv",
    "install_agent_dependencies",
    # Utils
    "check_uv_available",
    "load_env_file",
    "get_env_var",
    "resolve_timeout_value",
    "load_data_info",
    "get_default_split",
    "get_default_version",
    # Logger
    "setup_logger",
    "add_file_handler",
    # Constants
    "IS_WINDOWS",
    "EXPERIMENTS_DIR",
    "TASKS_EVAL_RESULT_DIR",
    "BENCHMARKS_DIR",
    "DATA_DIR",
    "TASKS_DIR",
    "TRAJECTORY_DIR",
    "RESULT_JSON",
    "DATA_INFO_JSON",
    "CONFIG_YAML",
    "DEFAULT_BENCHMARK_NAME",
    "CONFIG_PATH_REL",
    "DEFAULT_TASKS_REL_PATH",
    # Parse/Image utils
    "safe_int",
    "load_json_records",
    "find_key_recursive",
    "strip_base64_prefix",
    "extract_base64_from_content_item",
    "decode_base64_to_file",
    # Eval
    "encode_image",
    "EvaluationModel",
    "load_evaluation_model",
    "extract_score_from_response",
    "calculate_evaluation_cost",
    "aggregate_evaluation_costs",
    "calculate_success",
    "find_latest_tasks_dir",
    "dedupe_records_keep_newest",
    "normalized_results_file",
    # Failure classification
    "classify_failure_case",
    "classify_failures_batch",
    "calculate_failure_category_stats",
    # Prompt loading
    "load_prompt",
    "make_prompt_ref",
    "make_template_prompt",
    "make_text_prompt",
]
