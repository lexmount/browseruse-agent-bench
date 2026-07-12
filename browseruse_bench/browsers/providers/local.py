from __future__ import annotations

import logging
from typing import Any, Dict

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.browsers.types import BrowserSessionContext

_logger = logging.getLogger(__name__)
_warned_agents: set[str] = set()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def warn_if_local_proxy_unsupported(agent_config: Dict[str, Any], agent_name: str) -> None:
    """One-shot warning for agents whose local-Chrome launch path can't honour
    `local_proxy_*`.

    Agents like agent-tars, claude-code, openai-cua, and deepbrowse delegate
    Chrome startup to an external CLI / SDK / MCP server that this repo can't
    easily intercept. Without this warning a user who configures a proxy would
    see their requests silently bypass it (the exact failure mode that prompted
    this whole feature). Call once per agent; subsequent calls are no-ops.
    """
    server = str(agent_config.get("local_proxy_server") or "").strip()
    if not server:
        return
    browser_id = str(agent_config.get("browser_id") or "").lower()
    if browser_id not in ("local", "chrome-local", ""):
        return  # non-local backend (e.g. lexmount) — not relevant
    if agent_name in _warned_agents:
        return
    _warned_agents.add(agent_name)
    _logger.warning(
        "[local_proxy] Agent '%s' does not support local-browser proxy. "
        "Configured local_proxy_server=%s is being ignored. "
        "Use browser_id=lexmount (cloud, ex-CN egress) or run skyvern / "
        "browser-use which DO honour local_proxy_*.",
        agent_name,
        server,
    )


def _extract_local_proxy(agent_config: Dict[str, Any]) -> Dict[str, str] | None:
    """Read local_proxy_* fields from agent_config.

    Returns a dict {server, username?, password?, bypass?} when local_proxy_server
    is set and non-empty, else None. Empty server is treated as "no proxy" so
    users on default config don't break.
    """
    server = str(agent_config.get("local_proxy_server") or "").strip()
    if not server:
        return None
    proxy: Dict[str, str] = {"server": server}
    for key in ("local_proxy_username", "local_proxy_password", "local_proxy_bypass"):
        val = str(agent_config.get(key) or "").strip()
        if val:
            proxy[key.removeprefix("local_proxy_")] = val
    return proxy


class LocalBackend(BrowserBackend):
    """Local browser backend.

    No external session lifecycle, but plumbs `local_proxy_*` config from the
    user's `config.yaml` into the session metadata so each agent's local-Chrome
    launch path can apply it. The local browser does NOT inherit OS-level proxy
    settings, so this is the supported way to point it at a clash/v2ray/etc.
    """

    def __init__(self, backend_id: str) -> None:
        super().__init__(backend_id)

    def open(self, agent_name: str, agent_config: Dict[str, Any]) -> BrowserSessionContext:
        metadata: Dict[str, Any] = {}
        if agent_config.get("headless") not in (None, ""):
            metadata["headless"] = _coerce_bool(agent_config["headless"])
        executable_path = str(agent_config.get("local_executable_path") or "").strip()
        if executable_path:
            metadata["executable_path"] = executable_path
        proxy = _extract_local_proxy(agent_config)
        if proxy is not None:
            metadata["local_proxy"] = proxy
        return BrowserSessionContext(
            backend_id=self.backend_id,
            transport="local",
            metadata=metadata,
        )

    def close(self, session_context: BrowserSessionContext) -> None:
        pass
