from __future__ import annotations

import hashlib

from browseruse_bench.utils import run_identity


def test_collect_machine_identity_records_hashed_mac_by_default(monkeypatch) -> None:
    monkeypatch.setattr(run_identity.uuid, "getnode", lambda: 0x001122334455)
    monkeypatch.delenv(run_identity.MACHINE_ID_ENV_KEY, raising=False)
    monkeypatch.delenv(run_identity.LEGACY_MACHINE_ID_ENV_KEY, raising=False)

    identity = run_identity.collect_machine_identity(machine_id="worker-a")

    expected_mac = "00:11:22:33:44:55"
    expected_hash = hashlib.sha256(expected_mac.encode("utf-8")).hexdigest()
    assert identity["machine_id"] == "worker-a"
    assert identity["mac_address_sha256"] == expected_hash
    assert identity["hardware_fingerprint"] == expected_hash[:16]
    assert identity["mac_address_source"] == "uuid.getnode"
    assert "mac_address" not in identity


def test_collect_machine_identity_can_include_raw_mac(monkeypatch) -> None:
    monkeypatch.setattr(run_identity.uuid, "getnode", lambda: 0x001122334455)

    identity = run_identity.collect_machine_identity(
        machine_id="worker-a",
        include_raw_identifiers=True,
    )

    assert identity["mac_address"] == "00:11:22:33:44:55"
