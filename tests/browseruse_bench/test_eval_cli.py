from __future__ import annotations

import pytest

from browseruse_bench.cli.eval import _parse_extra_args


def test_parse_eval_extra_args_coerces_private_options() -> None:
    assert _parse_extra_args([
        "--max-screenshots", "50",
        "--image-scale-factor=0.5",
        "--use-cache", "false",
        "--dry-private-flag",
    ]) == {
        "max_screenshots": 50,
        "image_scale_factor": 0.5,
        "use_cache": False,
        "dry_private_flag": True,
    }


def test_parse_eval_extra_args_rejects_positional() -> None:
    with pytest.raises(SystemExit):
        _parse_extra_args(["unexpected"])
