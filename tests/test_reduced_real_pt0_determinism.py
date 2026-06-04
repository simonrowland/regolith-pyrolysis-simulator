from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from simulator.chemistry.kernel import ChemistryIntent
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.optimize.determinism import deterministic_result_view
from simulator.reduced_real_determinism import (
    PT0CacheMiss,
    PT0DeterminismStore,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentStoreCorrupt,
    canonical_replay_key,
)
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _pt0_setpoints() -> dict:
    setpoints = _load_yaml("setpoints.yaml")
    gate = dict(setpoints.get("freeze_gate", {}) or {})
    gate["enabled"] = True
    setpoints["freeze_gate"] = gate
    return setpoints


def _build_pt0_sim(store: PT0DeterminismStore):
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _pt0_setpoints(),
        additives_kg={"K": 26.0, "Na": 12.0},
    )
    sim.configure_pt0_determinism_store(store)
    return sim


def _run_capped_c2a(
    store: PT0DeterminismStore,
    *,
    max_hours: int = 1,
    disable_live: bool = False,
) -> dict:
    sim = _build_pt0_sim(store)
    if disable_live:
        _disable_live_providers(sim)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    snapshots = []
    for _ in range(max_hours):
        snapshots.append(sim.step())
    snapshot = snapshots[-1]
    curves = [
        entry["payload"]["curve"]
        for entry in store.entries.values()
        if entry["artifact"] == "freeze_gate_curve"
    ]
    liquid_fraction_path = []
    if curves:
        liquid_fraction_path = list(curves[0]["path"])
    return {
        "campaign_hours": float(sim.melt.campaign_hour),
        "temperature_C": float(snapshot.temperature_C),
        "mass_balance_error_pct": float(snapshot.mass_balance_error_pct),
        "products": sim.product_ledger(),
        "liquid_fraction_path": liquid_fraction_path,
        "snapshot_count": len(snapshots),
    }


def _disable_live_providers(sim) -> None:
    def disabled(*_args, **_kwargs):
        raise AssertionError("PT-0 replay attempted a live provider call")

    sim.backend.equilibrate = disabled
    sim.backend.find_liquidus_solidus = disabled
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    if provider is not None:
        provider.dispatch = disabled


def _real_magemin_available() -> bool:
    backend = MAGEMinBackend()
    backend.initialize({"python_bridge": "subprocess"})
    return backend.is_available()


def test_pt0_canonical_key_contains_required_identity_fields() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)

    key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        fe_redox_policy="intrinsic",
    )

    assert key["schema_version"] == "pt0-reduced-real-determinism-v1"
    assert key["composition_mol_fraction"]
    assert set(key["controls"]) == {"T_K", "log_fO2", "pressure_bar", "pO2_bar"}
    assert key["provider"]["resolved_provider_id"] == "magemin-shadow"
    assert key["provider"]["resolved_role"] == "fallback"
    assert "vapor_pressure_provider" in key
    assert "stage0_inventory_digest" in key["sulfur_side"]
    assert "sulfsat_package_version" in key["sulfur_side"]
    assert "sulfsat_calibration_version" in key["sulfur_side"]
    assert key["code_version"]
    assert key["engine_version"] is not None
    assert set(key["data_digests"]) == {
        "setpoints",
        "feedstocks",
        "vapor_pressures",
        "species_formula_registry",
    }


def test_pt0_replay_miss_fails_loudly() -> None:
    store = PT0DeterminismStore("replay")
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)

    with pytest.raises(PT0CacheMiss):
        store.replay_gate_curve(
            sim,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
        )


def test_pt1_persistent_store_round_trips_exact_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "pt1-reduced-real.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    curve = {
        "source": "unit-test",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=fO2_log,
        fe_redox_policy="intrinsic",
    )

    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=curve)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"
            )
        }
        row = conn.execute(
            f"""
            SELECT artifact, key_hash, payload_sha256
            FROM {PT1_EQUILIBRIUM_TABLE}
            """
        ).fetchone()
    assert {
        "key_hash",
        "artifact",
        "store_schema_version",
        "request_schema_version",
        "key_sha256",
        "payload_sha256",
        "key_bytes",
        "payload_bytes",
        "code_version",
        "engine_version",
        "data_digests_json",
        "git_dirty",
    } <= columns
    assert row[0] == "freeze_gate_curve"
    assert row[1]
    assert row[2]

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replay_fO2_log = replay_sim._compute_intrinsic_melt_fO2()
    replay_key = canonical_replay_key(
        replay_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=replay_fO2_log,
        fe_redox_policy="intrinsic",
    )

    assert replay_key == key
    assert replay.replay_gate_curve(replay_sim, fO2_log=replay_fO2_log) == curve
    assert replay.summary()["hits"] == 1
    assert replay.summary()["misses"] == 0
    assert replay.replay_sequence[-1]["cache_state"] == "cached_exact"


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("payload_bytes", b'{"corrupt":true}'),
        ("store_schema_version", "stale-store-schema"),
    ),
)
def test_pt1_verify_on_hit_fails_loudly(
    tmp_path: Path,
    column: str,
    value: bytes | str,
) -> None:
    db_path = tmp_path / "pt1-reduced-real.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    capture.capture_gate_curve(
        sim,
        fO2_log=fO2_log,
        curve={
            "source": "unit-test",
            "solidus_T_C": 1210.0,
            "liquidus_T_C": 1320.0,
            "path": ((1210.0, 0.0), (1320.0, 1.0)),
        },
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET {column} = ?",
            (value,),
        )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    with pytest.raises(PT1PersistentStoreCorrupt):
        replay.replay_gate_curve(
            replay_sim,
            fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
        )


@pytest.mark.skipif(
    os.environ.get("REGOLITH_PT0_REAL_PROVIDER") != "1",
    reason="set REGOLITH_PT0_REAL_PROVIDER=1 to run the real MAGEMin PT-0 proof",
)
@pytest.mark.skipif(
    not _real_magemin_available(),
    reason="real MAGEMin subprocess backend unavailable",
)
def test_pt0_real_magemin_capped_replay_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "pt1-real-provider.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    live_trace = _run_capped_c2a(capture, max_hours=1)
    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_trace = _run_capped_c2a(replay, max_hours=1, disable_live=True)

    replay_summary = replay.summary()
    assert replay_summary["hits"] == 3
    assert replay_summary["misses"] == 0
    assert replay_summary["key_drift_histogram"] == {}
    assert {
        entry["cache_state"]
        for entry in replay.replay_sequence
    } == {"cached_exact"}
    assert deterministic_result_view(live_trace) == deterministic_result_view(
        replay_trace
    )
    assert live_trace["mass_balance_error_pct"] == pytest.approx(
        replay_trace["mass_balance_error_pct"],
        abs=5e-12,
    )
    assert capture.summary()["capture_calls_by_artifact"]["freeze_gate_curve"] >= 1
    assert capture.summary()["capture_calls_by_artifact"][
        "equilibrium_post_record"
    ] >= 1
