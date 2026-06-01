"""Registry mapping benchmark name -> evaluator class via lazy factories.

Modeled on browseruse_bench/browsers/registry.py: factory closures perform
function-local imports so that subpackage SDKs (OpenAI, PIL) load only when
the corresponding benchmark is used.
"""
from __future__ import annotations

from collections.abc import Callable

from browseruse_bench.eval.base import BaseEvaluator

_FACTORIES: dict[str, Callable[[], type[BaseEvaluator]]] = {}


def register_evaluator(
    name: str,
) -> Callable[[Callable[[], type[BaseEvaluator]]], Callable[[], type[BaseEvaluator]]]:
    """Decorator: bind a factory closure under the given benchmark name."""

    def decorator(
        factory: Callable[[], type[BaseEvaluator]],
    ) -> Callable[[], type[BaseEvaluator]]:
        _FACTORIES[name] = factory
        return factory

    return decorator


def get_evaluator_class(name: str) -> type[BaseEvaluator]:
    if name not in _FACTORIES:
        raise KeyError(
            f"Unknown evaluator: {name}. Registered: {sorted(_FACTORIES)}"
        )
    return _FACTORIES[name]()


def list_evaluators() -> list[str]:
    return sorted(_FACTORIES)


def _register_defaults() -> None:
    """Bind built-in benchmarks. Called at module import time.

    The factory closures defer the subpackage import until ``get_evaluator_class``
    is called, so missing optional SDKs in unused subpackages do not break import.
    """

    @register_evaluator("Online-Mind2Web")
    def _online_mind2web():
        from browseruse_bench.eval.online_mind2web.evaluator import OnlineMind2WebEvaluator
        return OnlineMind2WebEvaluator

    @register_evaluator("BrowseComp")
    def _browse_comp():
        from browseruse_bench.eval.browse_comp.evaluator import BrowseCompEvaluator
        return BrowseCompEvaluator

    @register_evaluator("LexBench-Browser")
    def _lexbench():
        from browseruse_bench.eval.lexbench_browser.evaluator import LexBenchBrowserEvaluator
        return LexBenchBrowserEvaluator

    @register_evaluator("WebVoyager")
    def _webvoyager():
        from browseruse_bench.eval.webvoyager.evaluator import WebVoyagerEvaluator
        return WebVoyagerEvaluator

    @register_evaluator("Odysseys")
    def _odysseys():
        from browseruse_bench.eval.odysseys.evaluator import OdysseysEvaluator
        return OdysseysEvaluator


_register_defaults()
