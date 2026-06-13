#!/usr/bin/env python3
from __future__ import annotations

import sys

from browseruse_bench.cli.run_eval import run_and_eval


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    return run_and_eval(args)


if __name__ == "__main__":
    sys.exit(main())
