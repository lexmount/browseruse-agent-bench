"""Run identity helpers for experiment traceability."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import socket
import uuid
from typing import Any

MACHINE_ID_ENV_KEY = "BUBENCH_MACHINE_ID"
LEGACY_MACHINE_ID_ENV_KEY = "BROWSERUSE_BENCH_MACHINE_ID"
MACHINE_IDENTITY_ENV_KEY = "BUBENCH_MACHINE_IDENTITY"
INCLUDE_RAW_MACHINE_IDENTIFIERS_ENV_KEY = "BUBENCH_INCLUDE_RAW_MACHINE_IDENTIFIERS"


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return ""


def _clean_identity_value(value: Any) -> str:
    return str(value or "").strip()


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _format_mac_address(node: int) -> str:
    return ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -1, -8))


def _machine_hardware_identity(include_raw_identifiers: bool) -> dict[str, Any]:
    node = uuid.getnode()
    mac_address = _format_mac_address(node)
    # uuid.getnode() may synthesize a random multicast address when it cannot
    # find a hardware address. The multicast bit in the first octet flags that.
    is_random = bool((node >> 40) & 0x01)
    source = "uuid.getnode_random" if is_random else "uuid.getnode"
    mac_hash = hashlib.sha256(mac_address.encode("utf-8")).hexdigest()
    hardware: dict[str, Any] = {
        "mac_address_sha256": mac_hash,
        "mac_address_source": source,
        "hardware_fingerprint": mac_hash[:16],
    }
    if include_raw_identifiers:
        hardware["mac_address"] = mac_address
    return hardware


def collect_machine_identity(
    machine_id: str | None = None,
    *,
    include_raw_identifiers: bool = False,
) -> dict[str, Any]:
    """Return non-secret machine metadata for run/result attribution.

    ``machine_id`` is intentionally overrideable so distributed workers can use
    stable labels such as ``gpu-a100-01`` instead of OS hostnames.
    """
    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    resolved_id = _clean_identity_value(machine_id)
    source = "cli"
    if not resolved_id:
        for env_key in (MACHINE_ID_ENV_KEY, LEGACY_MACHINE_ID_ENV_KEY):
            resolved_id = _clean_identity_value(os.getenv(env_key))
            if resolved_id:
                source = f"env:{env_key}"
                break
    if not resolved_id:
        resolved_id = hostname or "unknown"
        source = "hostname"

    return {
        "machine_id": resolved_id,
        "machine_id_source": source,
        "hostname": hostname,
        "fqdn": fqdn,
        "user": _safe_user(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        **_machine_hardware_identity(include_raw_identifiers),
    }


def load_machine_identity_from_env() -> dict[str, Any]:
    """Load parent-provided machine identity, falling back to local detection."""
    raw = os.getenv(MACHINE_IDENTITY_ENV_KEY)
    if raw:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict) and _clean_identity_value(decoded.get("machine_id")):
            return {
                str(key): value
                for key, value in decoded.items()
                if isinstance(key, str)
            }
    return collect_machine_identity(
        include_raw_identifiers=_truthy_env(os.getenv(INCLUDE_RAW_MACHINE_IDENTIFIERS_ENV_KEY)),
    )
