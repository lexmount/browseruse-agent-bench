from __future__ import annotations

from collections.abc import Callable

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.utils.config_loader import resolve_key_case_insensitive

_BACKEND_FACTORIES: dict[str, Callable[[], BrowserBackend]] = {}
_DEFAULTS_REGISTERED = False


def _create_local_backend(browser_id: str) -> BrowserBackend:
    from browseruse_bench.browsers.providers.local import LocalBackend

    return LocalBackend(browser_id)


def _create_cloud_native_backend(browser_id: str) -> BrowserBackend:
    from browseruse_bench.browsers.providers.cloudnative import CloudNativeBackend

    return CloudNativeBackend(browser_id)


def _create_cdp_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.cloudnative import CDPBackend

    return CDPBackend("cdp")


def _create_lexmount_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.lexmount import LexmountBackend

    return LexmountBackend("lexmount")


def _create_agentbay_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.agentbay import AgentBayBackend

    return AgentBayBackend("agentbay")


def _create_browserbase_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.browserbase import BrowserbaseBackend

    return BrowserbaseBackend("browserbase")


def _create_browserless_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.browserless import BrowserlessBackend

    return BrowserlessBackend("browserless")


def _create_steel_backend() -> BrowserBackend:
    from browseruse_bench.browsers.providers.steel import SteelBackend

    return SteelBackend("steel")


def register_backend(browser_id: str, factory: Callable[[], BrowserBackend]) -> None:
    if browser_id in _BACKEND_FACTORIES:
        raise ValueError(f"Browser backend already registered: {browser_id}")
    _BACKEND_FACTORIES[browser_id] = factory


def _register_default_backends() -> None:
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return

    register_backend("Chrome-Local", lambda: _create_local_backend("Chrome-Local"))
    register_backend("local", lambda: _create_local_backend("local"))
    register_backend("browser-use-cloud", lambda: _create_cloud_native_backend("browser-use-cloud"))
    register_backend("skyvern-cloud", lambda: _create_cloud_native_backend("skyvern-cloud"))
    register_backend("cdp", _create_cdp_backend)
    register_backend("lexmount", _create_lexmount_backend)
    register_backend("agentbay", _create_agentbay_backend)
    register_backend("browserbase", _create_browserbase_backend)
    register_backend("browserless", _create_browserless_backend)
    register_backend("steel", _create_steel_backend)
    _DEFAULTS_REGISTERED = True


def canonical_browser_id(browser_id: str) -> str:
    """Return the registered backend id matching *browser_id* case-insensitively.

    Falls back to the original *browser_id* when no backend matches.
    """
    _register_default_backends()
    return resolve_key_case_insensitive(browser_id, _BACKEND_FACTORIES)


def get_backend(browser_id: str) -> BrowserBackend:
    _register_default_backends()
    factory = _BACKEND_FACTORIES.get(canonical_browser_id(browser_id))
    if factory is None:
        available = ", ".join(sorted(_BACKEND_FACTORIES.keys()))
        raise ValueError(f"Unknown browser backend: '{browser_id}'. Available: {available}")
    return factory()
