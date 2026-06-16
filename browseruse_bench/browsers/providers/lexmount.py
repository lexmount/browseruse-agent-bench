from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote, urlparse

from lexmount import Lexmount
from lexmount._sessions import SessionProxyConfig
from lexmount.exceptions import (
    ContextLockedError,
    ContextNotFoundError,
    SessionNotFoundError,
)

from browseruse_bench.browsers.base import BrowserBackend
from browseruse_bench.browsers.session_state import clear_session_state, write_session_state
from browseruse_bench.browsers.types import BrowserSessionContext

logger = logging.getLogger(__name__)

# Runtime-only env var: the parent bubench CLI injects a login context id here
# per task. Kept off agent_config on purpose — agent_config is user-authored
# (agent/model/browser choices) while the login context is tool-managed state.
LOGIN_CONTEXT_ID_ENV_KEY = "BUBENCH_LOGIN_CONTEXT_ID"

# Runtime-only env var carrying the profile key (matches the dataset's
# `website_region`) selected by the parent CLI. Same rationale as the login
# context env var: per-task injected state stays off agent_config.
LEXMOUNT_PROFILE_ENV_KEY = "BUBENCH_LEXMOUNT_PROFILE"

_PROFILE_FIELDS = (
    "api_key",
    "project_id",
    "base_url",
    "verify_ssl",
    "browser_mode",
    "official_proxy",
    "proxy_server",
    "proxy_type",
    "proxy_username",
    "proxy_password",
)


def normalize_profile_keys(profiles: Any) -> dict[str, dict[str, Any]]:
    """Lowercase + strip the keys of a ``lexmount_profiles`` mapping.

    Operators write keys in config.yaml (``zh``, ``en``, etc) while consumers
    look them up against task data values (``website_region``) that the dataset
    keeps lowercase. Normalizing on the consumer side avoids silent miss when
    a user types ``ZH`` or accidentally adds whitespace.
    """
    if not isinstance(profiles, dict):
        return {}
    return {
        str(k).strip().lower(): v
        for k, v in profiles.items()
        if isinstance(v, dict) and isinstance(k, str)
    }


def _resolve_lexmount_creds(agent_config: dict[str, Any]) -> dict[str, Any]:
    """Merge profile-keyed credentials over top-level fallbacks.

    Profile values win when present; missing fields fall back to the
    ``lexmount_<field>`` keys at the top of agent_config. Empty profile env
    var or no ``lexmount_profiles`` reproduces legacy behavior.
    """
    profile_key = (os.getenv(LEXMOUNT_PROFILE_ENV_KEY) or "").strip().lower()
    profiles = normalize_profile_keys(agent_config.get("lexmount_profiles"))
    profile_cfg = profiles.get(profile_key, {}) if profile_key else {}
    resolved: dict[str, Any] = {}
    for field in _PROFILE_FIELDS:
        value = profile_cfg.get(field)
        if value not in (None, ""):
            resolved[field] = value
            continue
        resolved[field] = agent_config.get(f"lexmount_{field}")
    return resolved


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


_ANSI_GREEN = "\033[32m"
_ANSI_RESET = "\033[0m"


def _build_debug_url(base_url: str, session_id: str) -> str:
    if not base_url or not session_id:
        return ""

    parsed = urlparse(base_url)
    api_host = parsed.hostname or ""
    if not api_host:
        return ""

    viewer_host = api_host
    if api_host.startswith("api."):
        viewer_host = "browser." + api_host[4:]
    elif api_host.startswith("api") and len(api_host) > 3:
        viewer_host = api_host[3:]

    port = f":{parsed.port}" if parsed.port is not None else ""
    api_host_port = f"{api_host}:{parsed.port}" if parsed.port is not None else api_host
    scheme = parsed.scheme or "https"
    quoted_session_id = quote(session_id, safe="")
    quoted_api_host = quote(api_host_port, safe="")
    return (
        f"{scheme}://{viewer_host}{port}/browser_dev/index.html"
        f"?session_id={quoted_session_id}#api_host={quoted_api_host}"
    )

class LexmountBackend(BrowserBackend):
    def __init__(self, backend_id: str) -> None:
        super().__init__(backend_id)

    def open(self, agent_name: str, agent_config: dict[str, Any]) -> BrowserSessionContext:
        creds = _resolve_lexmount_creds(agent_config)
        mode = str(creds.get("browser_mode") or "normal")
        api_key = creds.get("api_key") or None
        project_id = creds.get("project_id") or None
        base_url = creds.get("base_url") or None
        if base_url and "://" not in base_url:
            base_url = "https://" + base_url
        verify_ssl = _coerce_bool(creds.get("verify_ssl"), default=True)
        official_proxy = _coerce_bool(creds.get("official_proxy"), default=False)
        lexmount_client = Lexmount(
            **({} if not api_key else {"api_key": api_key}),
            **({} if not project_id else {"project_id": project_id}),
            **({} if not base_url else {"base_url": base_url}),
        )
        if not verify_ssl:
            # TODO: upstream a `verify=` parameter to the Lexmount SDK constructor
            # so we don't have to swap the private `_http_client`. Current SDK
            # (0.4.9) exposes no way to disable SSL verification at init time.
            import httpx
            lexmount_client._http_client = httpx.Client(
                timeout=lexmount_client._http_client.timeout,
                verify=False,
            )

        proxy: SessionProxyConfig | None = None
        proxy_server = str(creds.get("proxy_server") or "")
        if proxy_server:
            proxy = SessionProxyConfig(server=proxy_server)
            proxy_type = str(creds.get("proxy_type") or "")
            proxy_username = str(creds.get("proxy_username") or "")
            proxy_password = str(creds.get("proxy_password") or "")
            if proxy_type:
                proxy["type"] = proxy_type
            if proxy_username:
                proxy["username"] = proxy_username
            if proxy_password:
                proxy["password"] = proxy_password

        # Login context id is a runtime-only value; never written to agent_config.
        base_ctx_id = str(os.getenv(LOGIN_CONTEXT_ID_ENV_KEY) or "").strip() or None
        forked_ctx_id: str | None = None
        session_context_arg: dict[str, str] | None = None
        if base_ctx_id:
            try:
                forked = lexmount_client.contexts.fork(base_ctx_id)
            except ContextLockedError as exc:
                raise RuntimeError(
                    f"Login context {base_ctx_id} is locked by session "
                    f"{exc.active_session_id}; retry later or re-run `bubench login add <site>`"
                ) from exc
            except ContextNotFoundError as exc:
                raise RuntimeError(
                    f"Login context {base_ctx_id} not found; re-run `bubench login add <site>`"
                ) from exc
            forked_ctx_id = str(getattr(forked, "id", "") or "").strip() or None
            if not forked_ctx_id:
                raise RuntimeError(
                    f"Lexmount contexts.fork({base_ctx_id}) returned no id"
                )
            session_context_arg = {"id": forked_ctx_id, "mode": "read_write"}
            logger.info(
                "[SUCCESS] Lexmount forked login context: base=%s forked=%s",
                base_ctx_id, forked_ctx_id,
            )

        try:
            session = lexmount_client.sessions.create(
                browser_mode=mode,
                official_proxy=official_proxy,
                proxy=proxy,
                **({"context": session_context_arg} if session_context_arg else {}),
            )
        except Exception:
            # Clean up the orphan forked context if session creation itself fails.
            if forked_ctx_id is not None:
                try:
                    lexmount_client.contexts.delete(forked_ctx_id)
                except (ContextLockedError, ContextNotFoundError, OSError, RuntimeError, TimeoutError) as del_exc:
                    logger.warning(
                        "Failed to delete orphan forked context %s after session create error: %s",
                        forked_ctx_id, del_exc,
                    )
            raise
        cdp_url = getattr(session, "ws", None) or getattr(session, "connect_url", None)
        metadata = {
            "lexmount_client": lexmount_client,
            "session": session,
            "session_id": str(getattr(session, "session_id", None) or getattr(session, "id", None) or ""),
            "mode": mode,
        }
        if forked_ctx_id is not None:
            metadata["forked_context_id"] = forked_ctx_id
            metadata["base_login_context_id"] = base_ctx_id
        session_id = str(metadata.get("session_id") or "")
        if session_id:
            write_session_state(
                backend_id=self.backend_id,
                session_id=session_id,
                forked_context_id=forked_ctx_id,
            )
            inspect_url = str(getattr(session, "inspect_url", "") or "")
            if not inspect_url:
                base_url = str(getattr(lexmount_client, "base_url", "") or "")
                inspect_url = _build_debug_url(base_url=base_url, session_id=session_id)
            if inspect_url:
                metadata["inspect_url"] = inspect_url

        if not cdp_url:
            self.close(
                BrowserSessionContext(
                    backend_id=self.backend_id,
                    transport="cdp",
                    cdp_url=None,
                    metadata=metadata,
                )
            )
            raise RuntimeError("Lexmount session creation failed: connect_url is empty")

        logger.info("[SUCCESS] Lexmount session created: %s", str(cdp_url))
        logger.info("[SUCCESS] Lexmount session_id: %s", session_id)
        inspect_url = str(metadata.get("inspect_url", "") or "")
        if inspect_url:
            logger.info("[INFO] Lexmount inspect URL: %s%s%s", _ANSI_GREEN, inspect_url, _ANSI_RESET)
        return BrowserSessionContext(
            backend_id=self.backend_id,
            transport="cdp",
            cdp_url=str(cdp_url),
            metadata=metadata,
        )

    def close(self, session_context: BrowserSessionContext) -> None:
        lexmount_client = session_context.metadata.get("lexmount_client")
        session = session_context.metadata.get("session")
        session_id = str(session_context.metadata.get("session_id") or "")
        if session is not None:
            try:
                session.close()
            except (OSError, RuntimeError, TimeoutError) as exc:
                logger.warning("Lexmount session.close() failed: %s", exc)

        if lexmount_client is not None and session_id:
            try:
                lexmount_client.sessions.delete(session_id=session_id)
            except SessionNotFoundError:
                logger.info("Lexmount session already deleted: %s", session_id)
            except (OSError, RuntimeError, TimeoutError) as exc:
                logger.warning("Lexmount explicit session delete failed (session_id=%s): %s", session_id, exc)

        forked_ctx_id = str(session_context.metadata.get("forked_context_id") or "")
        if lexmount_client is not None and forked_ctx_id:
            try:
                lexmount_client.contexts.delete(forked_ctx_id)
                logger.info("Lexmount forked context deleted: %s", forked_ctx_id)
            except ContextNotFoundError:
                logger.info("Lexmount forked context already deleted: %s", forked_ctx_id)
            except (ContextLockedError, OSError, RuntimeError, TimeoutError) as exc:
                logger.warning(
                    "Lexmount forked context delete failed (id=%s): %s", forked_ctx_id, exc
                )

        try:
            clear_session_state()
        except (OSError, RuntimeError) as exc:
            logger.error("Failed to clear browser session state: %s", exc)
