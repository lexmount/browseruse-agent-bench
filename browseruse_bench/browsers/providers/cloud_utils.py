from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

CLEANUP_EXCEPTIONS = (
    HTTPError,
    URLError,
    OSError,
    RuntimeError,
    TimeoutError,
    socket.timeout,
)
_URL_SECRET_QUERY_KEYS = {"token", "apikey", "apiKey", "key"}
_DEFAULT_USER_AGENT = "browseruse-bench/1.0"


def read_config(agent_config: dict[str, Any], key: str, env_key: str | None = None) -> Any:
    value = agent_config.get(key)
    if value not in (None, ""):
        return value
    if env_key:
        return os.getenv(env_key)
    return None


def read_bool(value: Any, default: bool, config_key: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    logger.warning("Invalid boolean value for %s: %r; using default=%s", config_key, value, default)
    return default


def read_int(value: Any, config_key: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer value for %s: %r; ignoring", config_key, value)
        return None


def redact_url_query(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    redacted_query = urlencode(
        (key, "<redacted>" if key in _URL_SECRET_QUERY_KEYS else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return urlunparse(parsed._replace(query=redacted_query))


def post_json(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    encoded_body = None
    request_headers = dict(headers)
    request_headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)
    if body is not None:
        encoded_body = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url=url, data=encoded_body, headers=request_headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {redact_url_query(url)} failed with HTTP {exc.code}: {error_payload}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise RuntimeError(f"POST {redact_url_query(url)} failed: {exc}") from exc

    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"POST {redact_url_query(url)} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"POST {redact_url_query(url)} returned non-object JSON")
    return decoded


def get_json(*, url: str, timeout_seconds: int) -> dict[str, Any]:
    request = Request(url=url, headers={"User-Agent": _DEFAULT_USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {redact_url_query(url)} failed with HTTP {exc.code}: {error_payload}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise RuntimeError(f"GET {redact_url_query(url)} failed: {exc}") from exc

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GET {redact_url_query(url)} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"GET {redact_url_query(url)} returned non-object JSON")
    return decoded


def append_query(url: str, params: dict[str, Any]) -> str:
    parsed = urlparse(url)
    existing_query = parsed.query
    extra_query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    query = "&".join(part for part in (existing_query, extra_query) if part)
    return urlunparse(parsed._replace(query=query))
