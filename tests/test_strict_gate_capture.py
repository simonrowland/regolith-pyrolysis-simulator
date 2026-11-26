from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.reduced_real_determinism as rrd
from scripts.seed_reduced_real_cache import merge_cache_source
from simulator.chemistry.kernel import ChemistryIntent
from simulator.reduced_real_determinism import (
    PT0DeterminismStore,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentEquilibriumStore,
    PT1PersistentStoreCorrupt,
    canonical_json_bytes,
)
from simulator.state import CampaignPhase
from tests.binding_admission_fixtures import install_synthetic_binding_receipt
from simulator import engine_binding_admission as admission
from tests.chemistry.test_evaporation_freeze_gate import (
    _FakeMAGEMinGateFallbackProvider,
)
from tests.test_reduced_real_pt0_determinism import (
    _CountingSilicateEquilibriumProvider,
    _build_pt0_sim_with_strict_vapor_default,
    _configure_authoritative_alphamelts_equilibrium,
    _capture_pt0_gate_curve,
    _persistent_artifact_count,
    _select_synthetic_gate_curve_binding,
)


@pytest.fixture(autouse=True)
def _synthetic_binding_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_binding_receipt(
        tmp_path,
        monkeypatch,
        bind_direct_backend=False,
    )


def test_stub_engine_hour_captures_strict_gate_row_and_merges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shard_db = tmp_path / "populate-shard.sqlite"
    merged_db = tmp_path / "merged.sqlite"
    capture = PT0DeterminismStore(
        "capture",
        db_path=shard_db,
        strict_vapor_gate=True,
    )
    from simulator.chemistry.kernel import config as chemistry_config

    provider_order = dict(chemistry_config.LIQUID_FRACTION_PROVIDER_ORDER)
    provider_order["magemin"] = ("magemin",)
    monkeypatch.setattr(
        chemistry_config,
        "LIQUID_FRACTION_PROVIDER_ORDER",
        provider_order,
    )
    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    sim = _build_pt0_sim_with_strict_vapor_default(capture)
    assert sim._pt0_store() is capture
    assert capture.capture_enabled
    assert len(sim._chem_registry.gate_provider_order) == 1
    gate_provider = _FakeMAGEMinGateFallbackProvider()
    sim._chem_registry.register(
        gate_provider,
        [ChemistryIntent.GATE_LIQUID_FRACTION],
    )
    gate_provider._backend = SimpleNamespace(is_available=lambda: True)
    admission.publish_live_backend_admission(gate_provider._backend)
    equilibrium_provider = _CountingSilicateEquilibriumProvider()
    _configure_authoritative_alphamelts_equilibrium(
        sim,
        equilibrium_provider,
        allow_internal_analytical_fallback=False,
    )
    # The live hour's equilibrium record carries out-of-range vapor source
    # diagnostics. This test covers PT-1 gate-curve capture, so omit only the
    # unrelated equilibrium artifact and leave the strict corpus policy intact.
    monkeypatch.setattr(
        capture,
        "capture_equilibrium",
        lambda *_args, **_kwargs: None,
    )

    sim.start_campaign(CampaignPhase.C2A_STAGED)
    target_C, _ramp_rate = sim.campaign_mgr.get_temp_target(
        sim.melt.campaign,
        sim.melt.campaign_hour,
        sim.melt,
    )
    assert target_C is not None
    sim.melt.temperature_C = float(target_C)

    sim.step()

    assert capture.summary()["capture_calls_by_artifact"].get(
        "freeze_gate_curve",
        0,
    ) >= 1
    assert gate_provider.requests
    with sqlite3.connect(shard_db) as conn:
        rows = conn.execute(
            f"SELECT key_bytes, payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE artifact = 'freeze_gate_curve' ORDER BY key_hash"
        ).fetchall()
    assert rows
    key = json.loads(bytes(rows[0][0]).decode("utf-8"))
    payload = json.loads(bytes(rows[0][1]).decode("utf-8"))
    resolved_vapor_authority = rrd._provider_identity(
        sim,
        ChemistryIntent.VAPOR_PRESSURE,
    )
    assert resolved_vapor_authority["fallback_allowed"] is False
    assert payload["authority"]["vapor_pressure"] == resolved_vapor_authority
    assert key["vapor_pressure_provider_selection"] == "builtin-vapor-pressure"
    assert key["vapor_pressure_provider_selection"] == (
        resolved_vapor_authority["resolved_provider_id"]
    )

    merge_result = merge_cache_source(shard_db, merged_db)
    assert merge_result["inserted_rows"] == merge_result["rows"]
    assert _persistent_artifact_count(merged_db, "freeze_gate_curve") == len(rows)

    # A prior codec-v3 target row has the same key but lacks authority. The
    # current-version source supersedes that stale row during shard merge.
    stale_target_db = tmp_path / "merged-v3-target.sqlite"
    with sqlite3.connect(shard_db) as source_conn, sqlite3.connect(
        stale_target_db
    ) as target_conn:
        source_conn.backup(target_conn)
        for key_hash, raw_payload in target_conn.execute(
            f"SELECT key_hash, payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE artifact = 'freeze_gate_curve'"
        ).fetchall():
            legacy_payload = json.loads(bytes(raw_payload))
            legacy_payload.pop("authority")
            legacy_bytes = canonical_json_bytes(legacy_payload)
            target_conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                "SET payload_bytes = ?, payload_sha256 = ?, "
                "payload_codec_version = ? WHERE key_hash = ?",
                (
                    sqlite3.Binary(legacy_bytes),
                    hashlib.sha256(legacy_bytes).hexdigest(),
                    rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,
                    key_hash,
                ),
            )
    stale_merge_result = merge_cache_source(shard_db, stale_target_db)
    assert stale_merge_result["inserted_rows"] == 0
    with sqlite3.connect(stale_target_db) as conn:
        merged_rows = conn.execute(
            f"SELECT key_bytes, payload_bytes, payload_codec_version "
            f"FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE artifact = 'freeze_gate_curve'"
        ).fetchall()
    assert {
        (bytes(key_bytes), bytes(payload_bytes))
        for key_bytes, payload_bytes in rows
    } == {
        (bytes(key_bytes), bytes(payload_bytes))
        for key_bytes, payload_bytes, _version in merged_rows
    }
    assert all(
        version == rrd.PT0_GATE_CURVE_CODEC_VERSION
        for _key_bytes, _payload_bytes, version in merged_rows
    )


def _corrupt_current_gate_curve_row(db_path: Path, key_hash: str) -> bytes:
    with sqlite3.connect(db_path) as conn:
        payload_bytes = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()[0]
        malformed_payload = json.loads(bytes(payload_bytes))
        malformed_payload["curve"] = {}
        malformed_bytes = canonical_json_bytes(malformed_payload)
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ?, payload_codec_version = ? "
            "WHERE key_hash = ?",
            (
                sqlite3.Binary(malformed_bytes),
                hashlib.sha256(malformed_bytes).hexdigest(),
                rrd.PT0_GATE_CURVE_CODEC_VERSION,
                key_hash,
            ),
        )
    return malformed_bytes


def test_pt1_put_rejects_current_codec_gate_curve_that_fails_decode(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "current-malformed-put.sqlite"
    capture, _sim, _fO2_log, _curve = _capture_pt0_gate_curve(
        db_path,
        solidus_T_C=1200.0,
    )
    key_hash = str(capture.capture_sequence[-1]["hash"])
    entry = capture._entries[key_hash]
    malformed_bytes = _corrupt_current_gate_curve_row(db_path, key_hash)
    payload = entry["payload"]
    payload_bytes = canonical_json_bytes(payload)

    with pytest.raises(
        PT1PersistentStoreCorrupt,
        match="current payload fails codec admission",
    ):
        PT1PersistentEquilibriumStore(db_path).put(
            artifact="freeze_gate_curve",
            key=entry["key"],
            key_bytes=entry["key_bytes"].encode("utf-8"),
            key_hash=key_hash,
            payload=payload,
            payload_bytes=payload_bytes,
            payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
            repair_notices_json=entry["repair_notices_json"],
            law_disclosures=entry["law_disclosures"],
        )

    with sqlite3.connect(db_path) as conn:
        persisted_bytes = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()[0]
    assert bytes(persisted_bytes) == malformed_bytes


def test_pt1_merge_rejects_current_codec_gate_curve_that_fails_decode(
    tmp_path: Path,
) -> None:
    source_db = tmp_path / "current-malformed-source.sqlite"
    target_db = tmp_path / "current-malformed-target.sqlite"
    capture, _sim, _fO2_log, _curve = _capture_pt0_gate_curve(
        source_db,
        solidus_T_C=1200.0,
    )
    key_hash = str(capture.capture_sequence[-1]["hash"])
    with sqlite3.connect(source_db) as source_conn, sqlite3.connect(
        target_db
    ) as target_conn:
        source_conn.backup(target_conn)
    malformed_bytes = _corrupt_current_gate_curve_row(target_db, key_hash)

    with pytest.raises(
        PT1PersistentStoreCorrupt,
        match="current payload fails codec admission",
    ):
        merge_cache_source(source_db, target_db)

    with sqlite3.connect(target_db) as conn:
        persisted_bytes = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()[0]
    assert bytes(persisted_bytes) == malformed_bytes
