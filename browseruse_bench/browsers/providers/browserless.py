from __future__ import annotations

import logging
from typing import Any

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.browsers.providers import cloud_utils
from browseruse_bench.browsers.types import BrowserSessionContext

logger = logging.getLogger(__name__)


class BrowserlessBackend(BrowserBackend):
    """Browserless BaaS backend that exposes a connection URL directly."""

    def open(self, agent_name: str, agent_config: dict[str, Any]) -> BrowserSessionContext:
        token = (
            cloud_utils.read_config(agent_config, "browserless_token", "BROWSERLESS_TOKEN")
            or cloud_utils.read_config(agent_config, "browserless_api_key", "BROWSERLESS_API_KEY")
        )
        if not token:
            raise ValueError(
                "Browserless requires an API token: set `browserless_token` in config.yaml "
                "(e.g. `browserless_token: $BROWSERLESS_TOKEN`) or BROWSERLESS_TOKEN in the environment"
            )

        endpoint = str(
            cloud_utils.read_config(agent_config, "browserless_ws_url", "BROWSERLESS_WS_URL")
            or cloud_utils.read_config(agent_config, "browserless_endpoint", "BROWSERLESS_ENDPOINT")
            or "wss://production-sfo.browserless.io"
        ).rstrip("/")
        if "://" not in endpoint:
            endpoint = "wss://" + endpoint
        browser_path = str(agent_config.get("browserless_browser_path") or "")
        if browser_path and not browser_path.startswith("/"):
            browser_path = "/" + browser_path
        base_url = endpoint + browser_path

        query: dict[str, Any] = {"token": token}
        for key in (
            "timeout",
            "proxy",
            "proxyCountry",
            "proxyCity",
            "proxyState",
            "blockAds",
            "headless",
            "solveCaptchas",
            "integrations",
        ):
            cfg_key = f"browserless_{key}"
            value = cloud_utils.read_config(agent_config, cfg_key)
            if value not in (None, ""):
                query[key] = value

        cdp_url = cloud_utils.append_query(base_url, query)
        resolve_debugger_url = cloud_utils.read_bool(
            cloud_utils.read_config(agent_config, "browserless_resolve_debugger_url"),
            default=False,
            config_key="browserless_resolve_debugger_url",
        )
        if resolve_debugger_url:
            http_endpoint = endpoint.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
            version_url = cloud_utils.append_query(http_endpoint + "/json/version", query)
            version_payload = cloud_utils.get_json(url=version_url, timeout_seconds=30)
            debugger_url = version_payload.get("webSocketDebuggerUrl")
            if not isinstance(debugger_url, str) or not debugger_url:
                raise RuntimeError("Browserless /json/version response did not include webSocketDebuggerUrl")
            cdp_url = debugger_url
        logger.info("[SUCCESS] Browserless connection URL prepared for %s", endpoint)
        return BrowserSessionContext(
            backend_id=self.backend_id,
            transport="cdp",
            cdp_url=cdp_url,
            metadata={"endpoint": endpoint, "browser_path": browser_path},
        )

    def close(self, session_context: BrowserSessionContext) -> None:
        return None
