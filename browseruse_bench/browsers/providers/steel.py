from __future__ import annotations

import logging
from typing import Any

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.browsers.providers import cloud_utils
from browseruse_bench.browsers.session_state import clear_session_state, write_session_state
from browseruse_bench.browsers.types import BrowserSessionContext

logger = logging.getLogger(__name__)


class SteelBackend(BrowserBackend):
    """Steel.dev cloud browser backend using the public Sessions REST API."""

    def open(self, agent_name: str, agent_config: dict[str, Any]) -> BrowserSessionContext:
        api_key = cloud_utils.read_config(agent_config, "steel_api_key", "STEEL_API_KEY")
        if not api_key:
            raise ValueError(
                "Steel requires an API key: set `steel_api_key` in config.yaml "
                "or STEEL_API_KEY in the environment"
            )

        base_url = str(
            cloud_utils.read_config(agent_config, "steel_base_url", "STEEL_BASE_URL")
            or "https://api.steel.dev"
        ).rstrip("/")
        connect_url = str(
            cloud_utils.read_config(agent_config, "steel_connect_url", "STEEL_CONNECT_URL")
            or "wss://connect.steel.dev"
        ).rstrip("/")
        timeout_seconds = (
            cloud_utils.read_int(cloud_utils.read_config(agent_config, "steel_request_timeout"), "steel_request_timeout")
            or 30
        )

        body = self._build_create_body(agent_config)
        logger.info("[INFO] Creating Steel browser session...")
        session = cloud_utils.post_json(
            url=f"{base_url}/v1/sessions",
            headers={"steel-api-key": str(api_key)},
            body=body,
            timeout_seconds=timeout_seconds,
        )
        session_id = str(session.get("id") or "")
        if not session_id:
            raise RuntimeError("Steel session creation failed: id is empty")

        cdp_url = str(session.get("websocketUrl") or session.get("websocket_url") or "")
        if not cdp_url:
            cdp_url = cloud_utils.append_query(connect_url, {"apiKey": api_key, "sessionId": session_id})
        elif "apiKey=" not in cdp_url:
            cdp_url = cloud_utils.append_query(cdp_url, {"apiKey": api_key})

        write_session_state(backend_id=self.backend_id, session_id=session_id)
        logger.info("[SUCCESS] Steel session created: %s", session_id)
        return BrowserSessionContext(
            backend_id=self.backend_id,
            transport="cdp",
            cdp_url=cdp_url,
            metadata={
                "base_url": base_url,
                "api_key": str(api_key),
                "session_id": session_id,
                "request_timeout": timeout_seconds,
                "session": session,
            },
        )

    def close(self, session_context: BrowserSessionContext) -> None:
        session_id = str(session_context.metadata.get("session_id") or "")
        if session_id:
            try:
                base_url = str(session_context.metadata.get("base_url") or "https://api.steel.dev").rstrip("/")
                cloud_utils.post_json(
                    url=f"{base_url}/v1/sessions/{session_id}/release",
                    headers={"steel-api-key": str(session_context.metadata.get("api_key") or "")},
                    body=None,
                    timeout_seconds=int(session_context.metadata.get("request_timeout") or 30),
                )
            except cloud_utils.CLEANUP_EXCEPTIONS as exc:
                logger.error("Steel session release failed (session_id=%s): %s", session_id, exc)
        try:
            clear_session_state()
        except (OSError, RuntimeError) as exc:
            logger.error("Failed to clear browser session state: %s", exc)

    def _build_create_body(self, agent_config: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "steel_block_ads": "blockAds",
            "steel_headless": "headless",
            "steel_persist_profile": "persistProfile",
            "steel_profile_id": "profileId",
            "steel_proxy_url": "proxyUrl",
            "steel_region": "region",
            "steel_session_id": "sessionId",
            "steel_solve_captcha": "solveCaptcha",
            "steel_timeout": "timeout",
            "steel_use_proxy": "useProxy",
            "steel_user_agent": "userAgent",
        }
        bool_keys = {
            "steel_block_ads",
            "steel_headless",
            "steel_persist_profile",
            "steel_solve_captcha",
            "steel_use_proxy",
        }
        int_keys = {"steel_timeout"}
        body: dict[str, Any] = {}
        for config_key, api_key in field_map.items():
            if config_key not in agent_config:
                continue
            value = agent_config.get(config_key)
            if value in (None, ""):
                continue
            if config_key in bool_keys:
                body[api_key] = cloud_utils.read_bool(value, default=False, config_key=config_key)
            elif config_key in int_keys:
                int_value = cloud_utils.read_int(value, config_key)
                if int_value is not None:
                    body[api_key] = int_value
            else:
                body[api_key] = value
        return body
