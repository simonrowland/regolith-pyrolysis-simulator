from pathlib import Path
from typing import Any

from simulator import reduced_real_determinism as rrd


def put_rekey_gate_row(
    db_path: Path,
    key: dict[str, Any],
    *,
    engine_version: str,
) -> tuple[str, bytes]:
    curve = rrd._curve_payload(
        {
            "source": "unit-test:rekey",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": ((1200.0, 0.0), (1300.0, 1.0)),
            "is_liquidus_floor": False,
        }
    )
    payload = {"curve": curve}
    key_bytes = rrd.canonical_json_bytes(key)
    payload_bytes = rrd.canonical_json_bytes(payload)
    key_hash = rrd._sha256(key_bytes)
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact="freeze_gate_curve",
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=rrd._sha256(payload_bytes),
        engine_version_provenance=engine_version,
        law_disclosures=[],
    )
    return key_hash, payload_bytes
