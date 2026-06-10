from __future__ import annotations

import logging
from typing import Any

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.browsers.providers import cloud_utils
from browseruse_bench.browsers.session_state import clear_session_state, write_session_state
from browseruse_bench.browsers.types import BrowserSessionContext

logger = logging.getLogger(__name__)


class BrowserbaseBackend(BrowserBackend):
    """Browserbase cloud browser backend using the public Sessions REST API."""

    def open(self, agent_name: str, agent_config: dict[str, Any]) -> BrowserSessionContext:
        api_key = cloud_utils.read_config(agent_config, "browserbase_api_key", "BROWSERBASE_API_KEY")
        if not api_key:
            raise ValueError(
                "Browserbase requires an API key: set `browserbase_api_key` in config.yaml "
                "or BROWSERBASE_API_KEY in the environment"
            )

        base_url = str(
            cloud_utils.read_config(agent_config, "browserbase_base_url", "BROWSERBASE_BASE_URL")
            or "https://api.browserbase.com"
        ).rstrip("/")
        timeout_seconds = cloud_utils.read_int(
            cloud_utils.read_config(agent_config, "browserbase_request_timeout"),
            "browserbase_request_timeout",
        ) or 30

        body: dict[str, Any] = {}
        project_id = cloud_utils.read_config(agent_config, "browserbase_project_id", "BROWSERBASE_PROJECT_ID")
        if project_id:
            body["projectId"] = project_id
        context_id = cloud_utils.read_config(agent_config, "browserbase_context_id")
        if context_id:
            body["contextId"] = context_id
        extension_id = cloud_utils.read_config(agent_config, "browserbase_extension_id")
        if extension_id:
            body["extensionId"] = extension_id

        browser_settings: dict[str, Any] = {}
        session_timeout = cloud_utils.read_int(
            cloud_utils.read_config(agent_config, "browserbase_timeout"),
            "browserbase_timeout",
        )
        if session_timeout is not None:
            browser_settings["timeout"] = session_timeout
        if "browserbase_keep_alive" in agent_config:
            browser_settings["keepAlive"] = cloud_utils.read_bool(
                agent_config.get("browserbase_keep_alive"),
                default=False,
                config_key="browserbase_keep_alive",
            )
        region = cloud_utils.read_config(agent_config, "browserbase_region")
        if region:
            browser_settings["region"] = region
        if browser_settings:
            body["browserSettings"] = browser_settings

        logger.info("[INFO] Creating Browserbase browser session...")
        session = cloud_utils.post_json(
            url=f"{base_url}/v1/sessions",
            headers={"X-BB-API-Key": str(api_key)},
            body=body,
            timeout_seconds=timeout_seconds,
        )
        cdp_url = str(session.get("connectUrl") or "")
        session_id = str(session.get("id") or "")
        if not session_id:
            raise RuntimeError("Browserbase session creation failed: id is empty")
        if not cdp_url:
            self._release_session(
                base_url=base_url,
                api_key=str(api_key),
                project_id=str(project_id or ""),
                session_id=session_id,
                timeout_seconds=timeout_seconds,
            )
            raise RuntimeError("Browserbase session creation failed: connectUrl is empty")

        write_session_state(
            backend_id=self.backend_id,
            session_id=session_id,
            cleanup_metadata={
                "api_key": str(api_key),
                "base_url": base_url,
                "project_id": str(project_id or ""),
                "request_timeout": str(timeout_seconds),
            },
        )
        logger.info("[SUCCESS] Browserbase session created: %s", session_id)
        return BrowserSessionContext(
            backend_id=self.backend_id,
            transport="cdp",
            cdp_url=cdp_url,
            metadata={
                "base_url": base_url,
                "api_key": str(api_key),
                "project_id": str(project_id or ""),
                "session_id": session_id,
                "request_timeout": timeout_seconds,
                "session": session,
            },
        )

    def close(self, session_context: BrowserSessionContext) -> None:
        session_id = str(session_context.metadata.get("session_id") or "")
        released = False
        if session_id:
            try:
                self._release_session(
                    base_url=str(session_context.metadata.get("base_url") or "https://api.browserbase.com"),
                    api_key=str(session_context.metadata.get("api_key") or ""),
                    project_id=str(session_context.metadata.get("project_id") or ""),
                    session_id=session_id,
                    timeout_seconds=int(session_context.metadata.get("request_timeout") or 30),
                )
                released = True
            except cloud_utils.CLEANUP_EXCEPTIONS as exc:
                logger.error("Browserbase session release failed (session_id=%s): %s", session_id, exc)
        else:
            released = True
        if not released:
            return
        try:
            clear_session_state()
        except (OSError, RuntimeError) as exc:
            logger.error("Failed to clear browser session state: %s", exc)

    def _release_session(
        self,
        *,
        base_url: str,
        api_key: str,
        project_id: str,
        session_id: str,
        timeout_seconds: int,
    ) -> None:
        if not api_key:
            raise RuntimeError("Browserbase session release failed: api key is empty")
        body: dict[str, Any] = {"status": "REQUEST_RELEASE"}
        if project_id:
            body["projectId"] = project_id
        cloud_utils.post_json(
            url=f"{base_url.rstrip('/')}/v1/sessions/{session_id}",
            headers={"X-BB-API-Key": api_key},
            body=body,
            timeout_seconds=timeout_seconds,
        )
