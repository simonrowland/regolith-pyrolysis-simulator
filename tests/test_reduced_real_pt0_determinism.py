from __future__ import annotations

import copy
import hashlib
import math
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.reduced_real_determinism as rrd
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.capabilities import CapabilityProfile
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.corpus_version import current_corpus_version
from simulator.grind_preflight import GrindSourceGateError
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.optimize.determinism import deterministic_result_view
from simulator.reduced_real_determinism import (
    ControlQuantization,
    PT0CacheMiss,
    PT0DeterminismStore,
    PT0InvalidControls,
    PT0NonFinitePayload,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentStoreCorrupt,
    canonical_physics_bucket_key_from_replay_key,
    canonical_json_bytes,
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


def _build_pt0_sim(store: PT0DeterminismStore | None):
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _pt0_setpoints(),
        additives_kg={"K": 26.0, "Na": 12.0},
    )
    sim.configure_pt0_determinism_store(store)
    return sim


def _persistent_artifact_count(db_path: Path, artifact: str) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (PT1_EQUILIBRIUM_TABLE,),
        ).fetchone()
        if table_exists is None:
            return 0
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE artifact = ?",
            (artifact,),
        ).fetchone()[0])


class _CaptureDispatchKernel:
    def __init__(self) -> None:
        self.kwargs = None

    def dispatch(self, intent, **kwargs):
        self.kwargs = kwargs
        return IntentResult(intent=intent, status="ok", transition=None)


def test_json_ready_nonfinite_error_names_payload_path() -> None:
    with pytest.raises(PT0NonFinitePayload, match=r"\$\.outer\.inner\[0\]"):
        canonical_json_bytes({"outer": {"inner": [float("inf")]}})


def test_dispatch_only_quantizes_mre_melt_fo2_control_with_request() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    raw_fO2_log = -7.123456789
    expected = store.quantized_controls(sim, fO2_log=raw_fO2_log)["fO2_log"]
    assert expected != raw_fO2_log
    kernel = _CaptureDispatchKernel()
    sim._chem_kernel = kernel

    sim._dispatch_only(
        ChemistryIntent.ELECTROLYSIS_STEP,
        control_inputs={
            "voltage_V": 1.0,
            "current_A": 10.0,
            "dt_hr": 1.0,
            "melt_fO2_log": raw_fO2_log,
        },
        fO2_log=raw_fO2_log,
        fe_redox_policy="kress91_live",
    )

    assert kernel.kwargs is not None
    assert kernel.kwargs["fO2_log"] == pytest.approx(expected)
    assert kernel.kwargs["control_inputs"]["melt_fO2_log"] == pytest.approx(
        expected
    )
    assert kernel.kwargs["fe_redox_policy"] == "kress91_live"


def test_pt0_supplied_finite_fo2_quantization_is_golden_neutral() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)

    controls = store.quantized_controls(sim, fO2_log=-7.123456789)
    key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=-7.123456789,
        fe_redox_policy="intrinsic",
    )

    assert controls["fO2_log"] == -7.123
    assert key["controls"]["log_fO2"] == -7.123


def test_control_quantization_default_production_key_is_byte_identical() -> None:
    key = _freeze_gate_key()
    key_hash = _key_hash(key)
    fine_key = _freeze_gate_key(control_quantization=ControlQuantization.PRODUCTION)

    # 2026-07-04: rebaselined for #89 — the reduced-real production key now hashes
    # the FUNCTIONAL parsed content of setpoints/vapor_pressures (canonical JSON),
    # not raw file bytes, so doc-annotation/formatting edits no longer move it (the
    # over-broad digest flagged on 2026-07-03 is now fixed). Deterministic (byte-
    # identical across runs) and PRODUCTION==default still hold; golden-neutral
    # (runner outputs unchanged) — only this cache-identity hash moved.
    # 2026-07-06: rebaselined for the bug-hunt fix wave — the Na/K pure-Antoine
    # `source:` provenance strings in data/vapor_pressures.yaml were corrected from
    # the REF-002 compilation id to their primary ids (REF-041 Rodebush & Walters,
    # REF-042 Fiock & Rodebush). Parsed-content digest moves on those metadata
    # strings; verified the embedded formulas and every coefficient are unchanged.
    # Provenance-only cache-identity move, not a physics move.
    assert key_hash == "6417caf9722eb3b2e1039d2f02263374db20a6250bd23df78300bd0a6affaba8"
    assert canonical_json_bytes(fine_key) == canonical_json_bytes(key)
    assert _key_hash(fine_key) == key_hash


def test_control_quantization_tiers_produce_distinct_key_hashes() -> None:
    hashes = {
        name: _key_hash(
            _freeze_gate_key(control_quantization=ControlQuantization.from_name(name))
        )
        for name in ("xx_coarse", "coarse", "fine")
    }

    assert len(set(hashes.values())) == 3
    assert ControlQuantization.from_name("XX-COARSE") == (
        ControlQuantization.from_name("xx_coarse")
    )


def test_control_quantization_is_session_scoped_not_module_global() -> None:
    coarse = PT0DeterminismStore(
        "capture",
        control_quantization=ControlQuantization.from_name("coarse"),
    )
    fine = PT0DeterminismStore(
        "capture",
        control_quantization=ControlQuantization.from_name("fine"),
    )
    coarse_sim = _build_pt0_sim(coarse)
    fine_sim = _build_pt0_sim(fine)

    assert coarse.quantized_controls(coarse_sim, fO2_log=-7.123456789) == {
        "temperature_C": pytest.approx(24.85),
        "pressure_bar": 0.0,
        "fO2_log": -7.12,
    }
    assert fine.quantized_controls(fine_sim, fO2_log=-7.123456789) == {
        "temperature_C": pytest.approx(25.0),
        "pressure_bar": 0.0,
        "fO2_log": -7.123,
    }


class _CountingSilicateEquilibriumProvider(ChemistryProvider):
    name = "alphamelts-write-through-test"

    def __init__(
        self,
        *,
        fail_live: bool = False,
        provider_id: str | None = None,
        engine_version: str = "alphamelts-authentic-test",
    ) -> None:
        self.calls = 0
        self.fail_live = fail_live
        self.provider_id = provider_id or self.name
        self.engine_version = engine_version

    def capability_profile(self) -> CapabilityProfile:
        intents = frozenset({ChemistryIntent.SILICATE_EQUILIBRIUM})
        return CapabilityProfile(
            provider_id=self.provider_id,
            intents=intents,
            is_authoritative_for=intents,
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.calls += 1
        if self.fail_live:
            raise AssertionError(
                "PT-2 cached AlphaMELTS run attempted live dispatch"
            )
        return IntentResult(
            intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
            status="ok",
            transition=None,
            diagnostic={
                "phases_present": ("liquid",),
                "phase_masses_kg": {"liquid": 1.2345},
                "phase_modes_wt_pct": {"liquid": 100.0},
                "liquid_fraction": 1.0,
                "liquid_composition_wt_pct": {
                    "SiO2": 45.0,
                    "Al2O3": 15.0,
                    "FeO": 10.0,
                    "MgO": 10.0,
                    "CaO": 10.0,
                    "Na2O": 5.0,
                    "K2O": 5.0,
                },
                "fO2_log": request.fO2_log,
                "fe_redox_policy": request.fe_redox_policy,
                "mode": "equilibrate",
                "engine_version": self.engine_version,
                "backend_status": "ok",
            },
        )

    def _engine_version(self) -> str:
        return self.engine_version


def _run_authoritative_alphamelts_equilibrium(
    store: PT0DeterminismStore | None,
    provider: _CountingSilicateEquilibriumProvider,
    *,
    allow_stub_fallback: bool = True,
):
    sim = _build_pt0_sim(store)
    class AlphaMELTSBackend:
        def is_available(self) -> bool:
            return True

        def get_engine_version(self) -> str:
            return provider.engine_version

    sim.backend = AlphaMELTSBackend()
    if not allow_stub_fallback:
        sim._backend_allows_stub_fallback = lambda: False

    def fail_backend_equilibrate(*_args, **_kwargs):
        raise AssertionError(
            "authoritative SILICATE_EQUILIBRIUM used backend.equilibrate"
        )

    sim.backend.equilibrate = fail_backend_equilibrate
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    return sim._get_equilibrium()


def test_pt2_alphamelts_diagnostic_no_store_bypasses_ledger_guard() -> None:
    provider = _CountingSilicateEquilibriumProvider()

    result = _run_authoritative_alphamelts_equilibrium(
        None,
        provider,
        allow_stub_fallback=False,
    )

    assert provider.calls == 1
    assert result.status == "ok"
    assert result.ledger_transition is None
    assert result.phase_masses_kg == {"liquid": 1.2345}
    assert (
        result.alphamelts_diagnostics["engine_version"]
        == "alphamelts-authentic-test"
    )


def test_pt2_db_path_none_keeps_write_through_inert() -> None:
    store = PT0DeterminismStore("capture", db_path=None)
    sim = _build_pt0_sim(store)

    assert store.persistent_path is None
    assert store.persistent_store is None
    assert store.write_through_enabled is False
    assert store.cached_equilibrium(sim) is None
    assert store.summary()["persistent_store"] is None


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
    return _capped_trace(sim, store, snapshots)


def _run_capped_c2a_with_equilibrium_counter(
    store: PT0DeterminismStore,
    *,
    max_hours: int = 1,
    fail_live: bool = False,
) -> tuple[dict, int]:
    sim = _build_pt0_sim(store)
    sim.backend.is_available = lambda: True
    freeze_curve = {
        "source": "unit-test",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    sim._freeze_gate_curve_from_gate_dispatch = lambda reasons, *, fO2_log: dict(freeze_curve)
    original_equilibrate = sim.backend.equilibrate
    calls = 0

    def counted_equilibrate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if fail_live:
            raise AssertionError(
                "PT-2 cached live run attempted backend.equilibrate"
            )
        return original_equilibrate(*args, **kwargs)

    sim.backend.equilibrate = counted_equilibrate
    sim.backend.find_liquidus_solidus = lambda **_kwargs: SimpleNamespace(
        status="ok",
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
    )
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    snapshots = []
    for _ in range(max_hours):
        snapshots.append(sim.step())
    return _capped_trace(sim, store, snapshots), calls


def _capped_trace(sim, store: PT0DeterminismStore, snapshots: list) -> dict:
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


def _freeze_gate_key(
    *,
    control_quantization: ControlQuantization | None = None,
) -> dict:
    store = PT0DeterminismStore(
        "capture",
        control_quantization=control_quantization,
    )
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    return canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        fe_redox_policy="intrinsic",
        control_quantization=control_quantization,
    )


def _silicate_equilibrium_key(
    provider: _CountingSilicateEquilibriumProvider,
) -> dict:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim.backend.is_available = lambda: True
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    return store._equilibrium_key(sim)


def _key_hash(key: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(key)).hexdigest()


def _physics_bucket_hash(key: dict) -> str:
    return _key_hash(canonical_physics_bucket_key_from_replay_key(key))


def _physics_ladder_hash(key: dict, rung_tag: str) -> str:
    return _key_hash(rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, rung_tag))


def test_interpolation_diagnostics_do_not_enter_replay_key() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    before = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=fO2_log,
        fe_redox_policy="intrinsic",
    )

    sim.reduced_real_cache = {
        "interpolation_uncertainty_ranked_table_drain": {
            "schema_version": "interpolation_uncertainty_ranked_tables.v1",
            "selected": [{"point_id": "a", "uncertainty": {"large": "blob"}}],
        }
    }
    sim._last_backend_diagnostics = {
        "interpolation_feasibility_verdict": {
            "schema_version": "interpolation_feasibility_verdict.v1",
            "verdict": "indeterminate",
        }
    }
    after = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=fO2_log,
        fe_redox_policy="intrinsic",
    )

    assert after == before
    assert b"interpolation_uncertainty" not in canonical_json_bytes(after)
    assert b"interpolation_feasibility" not in canonical_json_bytes(after)


def _c3a_ladder_key(
    label: str,
    *,
    feo_fraction: float,
    temperature_K: float,
) -> dict:
    key = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-cache-c3a",
            engine_version="alpha-v1",
        )
    )
    key["backend"] = {
        "backend_name": "AlphaMELTSBackend",
        "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
        "backend_version": "alpha-v1",
    }
    key["composition_mol_fraction"] = [
        ["FeO", feo_fraction],
        ["SiO2", 1.0 - feo_fraction],
    ]
    key["controls"]["T_K"] = temperature_K
    return key


def _put_c3a_payload(db_path: Path, key: dict, label: str) -> None:
    payload = {"equilibrium_result": {"status": "ok"}, "label": label}
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact=str(key["artifact"]),
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _strict_vapor_key() -> dict:
    return {
        "schema_version": rrd.SCHEMA_VERSION,
        "artifact": "equilibrium_post_record",
        "intent": ChemistryIntent.SILICATE_EQUILIBRIUM.value,
        "composition_mol_fraction": [["Na2O", 0.1], ["SiO2", 0.9]],
        "controls": {"T_K": 1473.15, "log_fO2": -8.0, "pressure_bar": 0.001},
        "redox": {"fe_redox_policy": "intrinsic", "fe_split": {}},
        "backend": {
            "backend_name": "AlphaMELTSBackend",
            "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
            "backend_version": "alpha-v1",
        },
        "provider": {
            "resolved_provider_id": "alphamelts-diagnostic",
            "resolved_role": "authoritative",
        },
        "vapor_pressure_provider": {
            "resolved_provider_id": "builtin-vapor-pressure",
            "resolved_role": "authoritative",
            "authoritative_provider_id": "builtin-vapor-pressure",
            "fallback_provider_id": None,
            "fallback_allowed": False,
        },
        "sulfur_side": {"S_input_ppm": 0.0, "stage0_inventory_digest": "test"},
        "model": {"model": "alphamelts-diagnostic", "mode": "AlphaMELTSProvider"},
        "data_digests": {"vapor_pressures": "test"},
        "corpus_version": current_corpus_version(),
    }


def test_equilibrium_payload_hash_ignores_melt_regime_divergence_diagnostics():
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        liquid_fraction=0.5,
    )
    base_diagnostic = {
        "status": "ok",
        "vapor_pressures_Pa": {"Na": 1.0},
    }
    base_sim = SimpleNamespace(
        _last_vapor_pressures_source={"Na": "builtin_authoritative"},
        _last_vapor_pressure_diagnostic=dict(base_diagnostic),
    )
    live_diagnostic = {
        **base_diagnostic,
        "melt_regime_predicate_divergences": [
            {
                "site": "core.vapor_pressure.no_liquid_phase",
                "effective_regime": "partial",
                "canonical_error": "liquid_fraction must be finite",
                "liquid_fraction_repr": "nan",
            }
        ],
        "future_melt_regime_divergences": [
            {"liquid_fraction": float("nan")},
        ],
    }
    diverged_sim = SimpleNamespace(
        _last_vapor_pressures_source={"Na": "builtin_authoritative"},
        _last_vapor_pressure_diagnostic=live_diagnostic,
    )

    base_payload = rrd.equilibrium_payload(base_sim, result)
    diverged_payload = rrd.equilibrium_payload(diverged_sim, result)

    assert canonical_json_bytes(diverged_payload) == canonical_json_bytes(
        base_payload
    )
    assert (
        "melt_regime_predicate_divergences"
        not in diverged_payload["last_vapor_pressure_diagnostic"]
    )
    assert (
        "future_melt_regime_divergences"
        not in diverged_payload["last_vapor_pressure_diagnostic"]
    )
    assert "melt_regime_predicate_divergences" in live_diagnostic
    assert "future_melt_regime_divergences" in live_diagnostic


def test_strict_pt1_put_rejects_builtin_fallback_vapor_source(
    tmp_path: Path,
) -> None:
    key = _strict_vapor_key()
    payload = {
        "equilibrium_result": {"status": "ok"},
        "last_vapor_pressures_source": {"Na": "builtin_fallback"},
    }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    db_path = tmp_path / "strict-pt1.sqlite"
    store = rrd.PT1PersistentEquilibriumStore(db_path, strict_vapor_gate=True)

    with pytest.raises(GrindSourceGateError, match="builtin_fallback"):
        store.put(
            artifact="equilibrium_post_record",
            key=key,
            key_bytes=key_bytes,
            key_hash=hashlib.sha256(key_bytes).hexdigest(),
            payload=payload,
            payload_bytes=payload_bytes,
            payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
        )

    assert _persistent_artifact_count(db_path, "equilibrium_post_record") == 0


def _lookup_c3a_payload(db_path: Path, key: dict) -> tuple[dict, PT0DeterminismStore]:
    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        str(key["artifact"]),
        key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(key),
    )
    assert payload is not None
    return payload, store


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
    assert "code_version" not in key
    assert key["corpus_version"] == current_corpus_version()
    assert "engine_version" not in key
    assert "source_module_digest" not in key
    assert set(key["data_digests"]) == {
        "setpoints",
        "feedstocks",
        "vapor_pressures",
        "species_formula_registry",
    }


def test_pt0_gate_curve_key_is_tstd_aligned_across_isochemical_ramp(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-gate-tstd.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._freeze_gate_liquid_fraction_cache = {
        "key": ("test",),
        "curve": {"source": "test", "solidus_T_C": 1000.0, "liquidus_T_C": 1300.0},
    }
    sim.melt.temperature_C = 1450.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = 1450.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    curve = {
        "source": "unit-test",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }

    capture.capture_gate_curve(
        sim,
        fO2_log=sim._current_melt_redox_fO2_log(),
        curve=curve,
    )
    capture_key = capture.capture_sequence[-1]["key"]
    assert capture_key["controls"]["T_K"] == pytest.approx(298.15)

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replay_sim._freeze_gate_liquid_fraction_cache = sim._freeze_gate_liquid_fraction_cache
    replay_sim.melt.temperature_C = 1450.0
    replay_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    replay_sim.melt.oxygen_reservoir.reference_T_K = 1450.0 + 273.15
    replay_sim._sync_oxygen_reservoir_mirror()
    replay_sim.melt.temperature_C = 1600.0
    replay_sim._re_reference_melt_fO2_to_temperature(1600.0 + 273.15)

    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._current_melt_redox_fO2_log(),
    ) == curve
    replay_key = replay.replay_sequence[-1]["key"]
    assert replay_key == capture_key

    capacity = replay_sim._melt_redox_capacity_mol_per_ln_fO2(
        fO2_log=replay_sim._current_melt_redox_fO2_log(),
        T_K=1600.0 + 273.15,
    )
    replay_sim._apply_oxygen_reservoir_redox_source_terms(
        {"pt0_real_redox_step": capacity * math.log(10.0) * 0.25},
        temperature_K=1600.0 + 273.15,
    )
    redox_key = canonical_replay_key(
        replay_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=replay_sim._current_melt_redox_fO2_log(),
        fe_redox_policy="intrinsic",
    )

    assert redox_key != capture_key
    assert redox_key["controls"]["log_fO2"] != capture_key["controls"]["log_fO2"]


def test_pt2_silicate_provider_identity_changes_equilibrium_key() -> None:
    first = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-a",
            engine_version="alpha-v1",
        )
    )
    different_provider = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-b",
            engine_version="alpha-v1",
        )
    )
    different_version = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-a",
            engine_version="alpha-v2",
        )
    )

    assert first["intent"] == ChemistryIntent.SILICATE_EQUILIBRIUM.value
    assert first["provider"]["resolved_provider_id"] == "alphamelts-diagnostic-a"
    assert _key_hash(different_provider) != _key_hash(first)
    assert _key_hash(different_version) == _key_hash(first)


def test_pt2_physics_bucket_partitions_setpoints_and_feedstock_digests() -> None:
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic-cache-c1",
        engine_version="alpha-v1",
    )
    store = PT0DeterminismStore("capture")

    def build_sim():
        sim = _build_pt0_sim(store)
        sim.backend.is_available = lambda: True
        sim._chem_registry.register(
            provider,
            [ChemistryIntent.SILICATE_EQUILIBRIUM],
        )
        return sim

    baseline_key = store._equilibrium_key(build_sim())
    unchanged_key = store._equilibrium_key(build_sim())

    setpoints_changed = build_sim()
    setpoints_changed.setpoints["optimizer_candidate_patch"] = {
        "mre_target_species": ["FeO"],
        "temperature_C": 1275.0,
    }
    setpoints_key = store._equilibrium_key(setpoints_changed)

    feedstocks_changed = build_sim()
    feedstocks_changed.feedstocks = copy.deepcopy(feedstocks_changed.feedstocks)
    feedstocks_changed.feedstocks["lunar_mare_high_ti"]["sc37_digest_probe"] = (
        "feedstock-drift"
    )
    feedstocks_key = store._equilibrium_key(feedstocks_changed)

    baseline_hash = _physics_bucket_hash(baseline_key)
    assert _physics_bucket_hash(unchanged_key) == baseline_hash
    assert _key_hash(setpoints_key) != _key_hash(baseline_key)
    assert (
        setpoints_key["data_digests"]["setpoints"]
        != baseline_key["data_digests"]["setpoints"]
    )
    assert _physics_bucket_hash(setpoints_key) != baseline_hash
    assert (
        feedstocks_key["data_digests"]["feedstocks"]
        != baseline_key["data_digests"]["feedstocks"]
    )
    assert _physics_bucket_hash(feedstocks_key) != baseline_hash


def test_pt2_physics_bucket_partitions_real_determinants() -> None:
    key = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-cache-c1",
            engine_version="alpha-v1",
        )
    )
    baseline = _physics_bucket_hash(key)

    composition_changed = copy.deepcopy(key)
    composition_changed["composition_mol_fraction"] = list(
        composition_changed["composition_mol_fraction"]
    )
    composition_changed["composition_mol_fraction"].append(("Fe2O3", 1.0e-5))

    temperature_changed = copy.deepcopy(key)
    temperature_changed["controls"]["T_K"] += 0.01

    pressure_changed = copy.deepcopy(key)
    pressure_changed["controls"]["pressure_bar"] += 0.00001

    redox_changed = copy.deepcopy(key)
    redox_changed["controls"]["log_fO2"] -= 0.001

    po2_changed = copy.deepcopy(key)
    po2_changed["controls"]["pO2_bar"] *= 10.0

    solver_version_changed = copy.deepcopy(key)
    solver_version_changed["provider"]["engine_version"] = "alpha-v2"
    solver_version_changed["engine_version"] = "alpha-v2"

    assert _physics_bucket_hash(composition_changed) != baseline
    assert _physics_bucket_hash(temperature_changed) != baseline
    assert _physics_bucket_hash(pressure_changed) != baseline
    assert _physics_bucket_hash(redox_changed) != baseline
    assert _physics_bucket_hash(po2_changed) != baseline
    assert _physics_bucket_hash(solver_version_changed) != baseline


def test_pt2_physics_bucket_partitions_stage0_inventory_digest() -> None:
    key = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-cache-c1",
            engine_version="alpha-v1",
        )
    )
    sulfur_key = copy.deepcopy(key)
    sulfur_key["sulfur_side"]["S_input_ppm"] = 1000.0
    sulfur_key["sulfur_side"]["stage0_inventory_digest"] = "history-a"
    bucket = canonical_physics_bucket_key_from_replay_key(sulfur_key)

    assert bucket["physics_bucket"]["sulfur"]["S_input_ppm"] == 1000.0
    assert (
        bucket["physics_bucket"]["sulfur"]["stage0_inventory_digest"] == "history-a"
    )

    history_changed = copy.deepcopy(sulfur_key)
    history_changed["sulfur_side"]["stage0_inventory_digest"] = "history-b"
    assert _physics_bucket_hash(history_changed) != _physics_bucket_hash(sulfur_key)


def test_pt2_physics_ladder_snaps_composition_and_temperature_only() -> None:
    key = _c3a_ladder_key(
        "snap",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    c1 = canonical_physics_bucket_key_from_replay_key(key)
    h40 = rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, "h40")
    h30 = rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, "h30")

    assert h40["physics_bucket"]["composition_mol_fraction"] == [
        ["FeO", 0.1235],
        ["SiO2", 0.8765],
    ]
    assert h40["physics_bucket"]["controls"]["T_K"] == 1235.0
    assert h30["physics_bucket"]["composition_mol_fraction"] == [
        ["FeO", 0.123],
        ["SiO2", 0.877],
    ]
    assert h30["physics_bucket"]["controls"]["T_K"] == 1230.0
    for rung in (h40, h30):
        assert rung["replay_scope"] == c1["replay_scope"]
        assert rung["physics_bucket"]["controls"]["pressure_bar"] == c1[
            "physics_bucket"
        ]["controls"]["pressure_bar"]
        assert rung["physics_bucket"]["controls"]["log_fO2"] == c1[
            "physics_bucket"
        ]["controls"]["log_fO2"]
        assert rung["physics_bucket"]["controls"]["pO2_bar"] == c1[
            "physics_bucket"
        ]["controls"]["pO2_bar"]
        assert rung["physics_bucket"]["precision_rung"]["tag"] in {"h40", "h30"}


def test_pt2_control_ladder_snaps_pressure_and_pO2_additively() -> None:
    key = _c3a_ladder_key(
        "control-snap",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    key["controls"]["pressure_bar"] = 0.00123456
    key["controls"]["pO2_bar"] = 1.23456e-6
    c1 = canonical_physics_bucket_key_from_replay_key(key)
    h40 = rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, "h40")
    h40c = rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, "h40c")
    h30c = rrd.canonical_physics_ladder_bucket_key_from_replay_key(key, "h30c")

    assert h40["physics_bucket"]["controls"]["pressure_bar"] == c1[
        "physics_bucket"
    ]["controls"]["pressure_bar"]
    assert h40["physics_bucket"]["controls"]["pO2_bar"] == c1["physics_bucket"][
        "controls"
    ]["pO2_bar"]
    assert h40c["physics_bucket"]["controls"]["pressure_bar"] == 0.001235
    assert h40c["physics_bucket"]["controls"]["pO2_bar"] == 1.235e-6
    assert h30c["physics_bucket"]["controls"]["pressure_bar"] == 0.00123
    assert h30c["physics_bucket"]["controls"]["pO2_bar"] == 1.23e-6
    assert h40c["physics_bucket"]["controls"]["log_fO2"] == c1["physics_bucket"][
        "controls"
    ]["log_fO2"]
    assert h40c["physics_bucket"]["precision_rung"]["controls"] == (
        "pressure-pO2-sigfig-log_fO2-exact"
    )


def test_pt2_control_ladder_error_budget_computes_named_sio_term() -> None:
    source_key = _c3a_ladder_key(
        "source",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    query_key = copy.deepcopy(source_key)
    source_key["controls"]["pO2_bar"] = 1.23454e-6
    query_key["controls"]["pO2_bar"] = 1.23456e-6

    budget = rrd.physics_control_rung_error_budget(
        query_key,
        source_key,
        "h40c",
        source_payload={
            "equilibrium_result": {
                "vapor_pressures_Pa": {"SiO": 12.0},
            },
        },
    )

    assert budget["term"] == rrd.CONTROL_RUNG_SIO_ERROR_BUDGET_TERM
    assert budget["accepted"] is True
    assert budget["relative_error"] <= rrd.CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET
    assert budget["absolute_error_Pa"] > 0.0


def test_pt2_ladder_walk_forward_uses_finest_matching_rung(
    tmp_path: Path,
) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123446,
        temperature_K=1234.46,
    )
    h40_key = _c3a_ladder_key(
        "h40-row",
        feo_fraction=0.123444,
        temperature_K=1234.44,
    )
    h30_key = _c3a_ladder_key(
        "h30-row",
        feo_fraction=0.1231,
        temperature_K=1231.0,
    )

    assert _physics_bucket_hash(query_key) != _physics_bucket_hash(h40_key)
    assert _physics_ladder_hash(query_key, "h40") == _physics_ladder_hash(
        h40_key,
        "h40",
    )
    assert _physics_ladder_hash(query_key, "h40") != _physics_ladder_hash(
        h30_key,
        "h40",
    )
    assert _physics_ladder_hash(query_key, "h30") == _physics_ladder_hash(
        h30_key,
        "h30",
    )

    db_path = tmp_path / "ladder-finest.sqlite"
    _put_c3a_payload(db_path, h30_key, "h30")
    _put_c3a_payload(db_path, h40_key, "h40")

    payload, store = _lookup_c3a_payload(db_path, query_key)

    assert payload["label"] == "h40"
    assert store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"
    assert store.replay_sequence[-1]["physics_bucket_rung"] == "h40"

    db_path = tmp_path / "ladder-coarse.sqlite"
    _put_c3a_payload(db_path, h30_key, "h30")

    payload, store = _lookup_c3a_payload(db_path, query_key)

    assert payload["label"] == "h30"
    assert store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"
    assert store.replay_sequence[-1]["physics_bucket_rung"] == "h30"


def test_pt2_control_ladder_hit_records_error_budget(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    source_key = copy.deepcopy(query_key)
    query_key["controls"]["pressure_bar"] = 0.00123456
    source_key["controls"]["pressure_bar"] = 0.00123454
    query_key["controls"]["pO2_bar"] = 1.23456e-6
    source_key["controls"]["pO2_bar"] = 1.23454e-6

    assert _physics_bucket_hash(query_key) != _physics_bucket_hash(source_key)
    assert _physics_ladder_hash(query_key, "h40") != _physics_ladder_hash(
        source_key,
        "h40",
    )
    assert _physics_ladder_hash(query_key, "h40c") == _physics_ladder_hash(
        source_key,
        "h40c",
    )

    db_path = tmp_path / "control-rung.sqlite"
    _put_c3a_payload(db_path, source_key, "control-source")
    payload, store = _lookup_c3a_payload(db_path, query_key)

    assert payload["label"] == "control-source"
    event = store.replay_sequence[-1]
    assert event["cache_state"] == "cached_physics_bucket"
    assert event["physics_bucket_rung"] == "h40c"
    assert event["physics_bucket_error_budget"]["accepted"] is True
    assert event["physics_bucket_error_budget"]["term"] == (
        rrd.CONTROL_RUNG_SIO_ERROR_BUDGET_TERM
    )


def test_pt2_control_ladder_refuses_po2_knee_crossing(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query-knee",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    source_key = copy.deepcopy(query_key)
    query_key["controls"]["pO2_bar"] = 1.0001e-9
    source_key["controls"]["pO2_bar"] = 9.999e-10

    budget = rrd.physics_control_rung_error_budget(query_key, source_key, "h30c")
    assert budget["accepted"] is False
    assert budget["refusal_reason"] == "pO2_knee_crossing"
    assert _physics_ladder_hash(query_key, "h30c") == _physics_ladder_hash(
        source_key,
        "h30c",
    )

    db_path = tmp_path / "control-rung-knee.sqlite"
    _put_c3a_payload(db_path, source_key, "knee-source")
    store = PT0DeterminismStore("capture", db_path=db_path)

    payload = store._lookup_optional(
        str(query_key["artifact"]),
        query_key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query_key),
    )

    assert payload is None
    assert store.replay_sequence == []


def test_pt2_ladder_tiebreak_is_insertion_independent(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.12342,
        temperature_K=1234.2,
    )
    near_center = _c3a_ladder_key(
        "near-center",
        feo_fraction=0.123401,
        temperature_K=1234.01,
    )
    far_center = _c3a_ladder_key(
        "far-center",
        feo_fraction=0.123449,
        temperature_K=1234.49,
    )
    labels = []
    for index, rows in enumerate(
        (
            ((near_center, "test-near-center"), (far_center, "test-far-center")),
            ((far_center, "test-far-center"), (near_center, "test-near-center")),
        )
    ):
        db_path = tmp_path / f"ladder-tiebreak-{index}.sqlite"
        for row, label in rows:
            _put_c3a_payload(db_path, row, label)
        payload, store = _lookup_c3a_payload(db_path, query_key)
        labels.append(payload["label"])
        assert store.replay_sequence[-1]["physics_bucket_rung"] == "h40"

    assert labels == ["test-near-center", "test-near-center"]


def test_pt2_ladder_columns_are_nullable_additive(tmp_path: Path) -> None:
    db_path = tmp_path / "pt1-schema.sqlite"
    rrd.PT1PersistentEquilibriumStore(db_path)
    with sqlite3.connect(db_path) as conn:
        column_flags = {
            row[1]: row[3]
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }

    for column in (
        "physics_bucket_h40_sha256",
        "physics_bucket_h40_distance",
        "physics_bucket_h30_sha256",
        "physics_bucket_h30_distance",
        "physics_bucket_h40c_sha256",
        "physics_bucket_h40c_distance",
        "physics_bucket_h30c_sha256",
        "physics_bucket_h30c_distance",
    ):
        assert column in column_flags
        assert column_flags[column] == 0


def test_pt2_persistent_physics_bucket_hit_is_not_cached_exact(tmp_path: Path) -> None:
    class NonStubBackend:
        def get_engine_version(self) -> str:
            return "non-stub-test"

    class ReplayOnlySulfsatGate:
        def is_available(self) -> bool:
            return False

        def package_version(self) -> str:
            return "exact-only-replay"

        def calibration_version(self) -> str:
            return "exact-only-replay"

    db_path = tmp_path / "pt1.sqlite"
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic-cache-c1",
        engine_version="alpha-v1",
    )
    capture_store = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture_store)
    capture_sim.backend = NonStubBackend()
    capture_sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    capture_store.capture_equilibrium(
        capture_sim,
        EquilibriumResult(
            temperature_C=float(capture_sim.melt.temperature_C),
            pressure_bar=float(capture_sim.melt.p_total_mbar) / 1000.0,
            phases_present=["liquid"],
            phase_masses_kg={"liquid": 1.0},
            liquid_fraction=1.0,
            liquid_composition_wt_pct={"SiO2": 45.0, "FeO": 10.0},
            vapor_pressures_Pa={"SiO": 1.0},
            vapor_pressures_source={"SiO": "test"},
            fO2_log=capture_sim._compute_intrinsic_melt_fO2(),
        ),
    )

    replay_store = PT0DeterminismStore("capture", db_path=db_path)
    replay_sim = _build_pt0_sim(replay_store)
    replay_sim.backend = NonStubBackend()
    replay_sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    replay_sim._sulfsat_gate = ReplayOnlySulfsatGate()

    capture_key = capture_store.capture_sequence[-1]["key"]
    replay_key = replay_store._equilibrium_key(replay_sim)
    assert _key_hash(replay_key) != _key_hash(capture_key)
    assert _physics_bucket_hash(replay_key) == _physics_bucket_hash(capture_key)

    payload = replay_store.cached_equilibrium(replay_sim)

    assert payload is not None
    assert replay_sim._last_reduced_real_cache_state == "cached_physics_bucket"
    assert replay_sim._backend_authoritative is False
    assert payload.diagnostics["reduced_real_cache_authoritative"] is False
    assert payload.diagnostics["reduced_real_cache_state"] == "cached_physics_bucket"
    counts = replay_store.summary()["cache_state_counts_by_artifact"][
        "equilibrium_post_record"
    ]
    assert counts["cached_physics_bucket"] == 1
    assert counts["cached_exact"] == 0


def test_control_quantization_coarse_store_fine_lookup_uses_ladder(
    tmp_path: Path,
) -> None:
    class NonStubBackend:
        def get_engine_version(self) -> str:
            return "non-stub-test"

    db_path = tmp_path / "control-quantization.sqlite"
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic-cache-c1",
        engine_version="alpha-v1",
    )
    capture_store = PT0DeterminismStore(
        "capture",
        db_path=db_path,
        control_quantization=ControlQuantization.from_name("coarse"),
    )
    capture_sim = _build_pt0_sim(capture_store)
    capture_sim.backend = NonStubBackend()
    capture_sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    capture_store.capture_equilibrium(
        capture_sim,
        EquilibriumResult(
            temperature_C=float(capture_sim.melt.temperature_C),
            pressure_bar=float(capture_sim.melt.p_total_mbar) / 1000.0,
            phases_present=["liquid"],
            phase_masses_kg={"liquid": 1.0},
            liquid_fraction=1.0,
            liquid_composition_wt_pct={"SiO2": 45.0, "FeO": 10.0},
            vapor_pressures_Pa={"SiO": 1.0},
            vapor_pressures_source={"SiO": "test"},
            fO2_log=capture_sim._compute_intrinsic_melt_fO2(),
        ),
    )

    replay_store = PT0DeterminismStore(
        "capture",
        db_path=db_path,
        control_quantization=ControlQuantization.from_name("fine"),
    )
    replay_sim = _build_pt0_sim(replay_store)
    replay_sim.backend = NonStubBackend()
    replay_sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )

    assert replay_store.cached_equilibrium(replay_sim) is not None
    assert replay_sim._last_reduced_real_cache_state == "cached_physics_bucket"
    assert replay_store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "temperature_C",
            float("nan"),
            "non-finite melt temperature passed to PT-0 quantization",
        ),
        (
            "p_total_mbar",
            float("inf"),
            "non-finite melt pressure passed to PT-0 quantization",
        ),
    ),
)
def test_pt0_quantized_controls_fail_loudly_on_non_finite_melt_controls(
    field: str,
    value: float,
    message: str,
) -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    setattr(sim.melt, field, value)

    with pytest.raises(PT0InvalidControls, match=message):
        store.quantized_controls(sim, fO2_log=0.0)


@pytest.mark.parametrize("fO2_log", [float("nan"), float("inf"), float("-inf")])
def test_pt0_quantized_controls_fail_loudly_on_non_finite_fo2(
    fO2_log: float,
) -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)

    with pytest.raises(PT0InvalidControls, match="non-finite fO2_log"):
        store.quantized_controls(sim, fO2_log=fO2_log)


@pytest.mark.parametrize("fO2_log", [float("nan"), float("inf"), float("-inf")])
def test_pt0_cache_key_fails_loudly_on_non_finite_fo2(
    fO2_log: float,
) -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)

    with pytest.raises(PT0InvalidControls, match="cache key"):
        canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("temperature_C", float("nan"), "non-finite melt temperature"),
        ("p_total_mbar", float("inf"), "non-finite melt pressure"),
    ),
)
def test_pt0_cache_key_fails_loudly_on_non_finite_melt_controls(
    field: str,
    value: float,
    message: str,
) -> None:
    # SC-49 class-completeness: canonical_replay_key() quantizes T_K and
    # pressure with the same _quantize() that returns None on non-finite input,
    # so it must refuse them like the fO2_log sibling above (and like
    # quantized_controls). A None T_K otherwise also flows into the intrinsic
    # fO2 fallback.
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    setattr(sim.melt, field, value)

    with pytest.raises(PT0InvalidControls, match=message):
        canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=None,
            fe_redox_policy="intrinsic",
        )


@pytest.mark.parametrize("bad_pO2", [float("nan"), float("inf"), float("-inf")])
def test_pt0_cache_key_fails_loudly_on_non_finite_pO2(bad_pO2: float) -> None:
    # SC-49 class-completeness: the commanded pO2 control is _sigfig-quantized in
    # the same cache key, and _sigfig returns None on non-finite input — so a
    # non-finite commanded pO2 must be refused like T_K / pressure / fO2 rather
    # than encoded as None. (Commanded 0.0 stays valid: _sigfig(0.0)==0.0.)
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim._commanded_pO2_bar = lambda: bad_pO2

    with pytest.raises(PT0InvalidControls, match="commanded pO2"):
        canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=-7.5,
            fe_redox_policy="intrinsic",
        )


@pytest.mark.parametrize("bad_pO2", [float("nan"), float("inf"), float("-inf")])
def test_pt0_quantized_pO2_bar_fails_loudly_on_non_finite(bad_pO2: float) -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim._commanded_pO2_bar = lambda: bad_pO2

    with pytest.raises(PT0InvalidControls, match="commanded pO2"):
        store.quantized_pO2_bar(sim)


FOULANT_DISPOSITION_MODULE = "engines/builtin/foulant_disposition.py"
RECIPE_SCHEMA_MODULE = "simulator/optimize/recipe.py"


def test_foulant_disposition_helper_source_module_digest_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.reduced_real_determinism import _SOURCE_MODULE_PATTERNS

    assert FOULANT_DISPOSITION_MODULE in _SOURCE_MODULE_PATTERNS

    rrd._source_module_digest.cache_clear()
    before_digest = rrd._source_module_digest()
    before_key_hash = _key_hash(_freeze_gate_key())
    assert FOULANT_DISPOSITION_MODULE in before_digest["paths"]

    target = rrd._repo_root() / FOULANT_DISPOSITION_MODULE
    original_read_bytes = Path.read_bytes

    def changed_read_bytes(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path.resolve() == target.resolve():
            return data + b"\n# foulant-disposition-digest-test\n"
        return data

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)
    rrd._source_module_digest.cache_clear()
    try:
        after_digest = rrd._source_module_digest()
        after_key_hash = _key_hash(_freeze_gate_key())
    finally:
        rrd._source_module_digest.cache_clear()

    assert before_digest["sha256"] != after_digest["sha256"]
    assert after_key_hash == before_key_hash


def test_recipe_schema_source_module_digest_coverage() -> None:
    from simulator.reduced_real_determinism import _SOURCE_MODULE_PATTERNS

    rrd._source_module_digest.cache_clear()
    try:
        digest = rrd._source_module_digest()
    finally:
        rrd._source_module_digest.cache_clear()

    assert RECIPE_SCHEMA_MODULE in _SOURCE_MODULE_PATTERNS
    assert RECIPE_SCHEMA_MODULE in digest["paths"]
    assert digest["module_set"] == "equilibrium-vapor-melt-backend-v3"


@pytest.mark.parametrize(
    "module_path",
    [
        "simulator/evaporation.py",
        "engines/builtin/evaporation_flux.py",
        "engines/builtin/vapor_pressure.py",
        "engines/builtin/stage0_pretreatment.py",
        "engines/builtin/foulant_disposition.py",
        "simulator/optimize/recipe.py",
    ],
)
def test_pt2_source_module_digest_changes_with_payload_source(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
) -> None:
    rrd._source_module_digest.cache_clear()
    before_digest = rrd._source_module_digest()
    before_key_hash = _key_hash(_freeze_gate_key())
    target = rrd._repo_root() / module_path
    original_read_bytes = Path.read_bytes

    def changed_read_bytes(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path.resolve() == target.resolve():
            return data + b"\n# source-digest-test\n"
        return data

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)
    rrd._source_module_digest.cache_clear()
    try:
        after_digest = rrd._source_module_digest()
        after_key_hash = _key_hash(_freeze_gate_key())
    finally:
        rrd._source_module_digest.cache_clear()

    assert before_digest["sha256"] != after_digest["sha256"]
    assert after_key_hash == before_key_hash


def test_pt2_source_module_digest_cross_process_key_determinism() -> None:
    rrd._source_module_digest.cache_clear()
    local_hash = _key_hash(_freeze_gate_key())
    repo_root = Path(__file__).resolve().parent.parent
    script = """
import hashlib
from simulator.reduced_real_determinism import canonical_json_bytes
from tests.test_reduced_real_pt0_determinism import _freeze_gate_key
print(hashlib.sha256(canonical_json_bytes(_freeze_gate_key())).hexdigest())
"""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    remote_hash = subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
    ).strip()

    assert remote_hash == local_hash


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
    assert not any("interpolation" in column for column in columns)
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


def test_pt1_capture_equilibrium_rejects_stub_provider(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-equilibrium-status.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    failed_result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        liquid_fraction=None,
        phase_assemblage_available=False,
        status="unavailable",
    )
    ok_result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        liquid_fraction=None,
        phase_assemblage_available=False,
        status="ok",
    )

    capture.capture_equilibrium(sim, failed_result)

    assert capture.summary()["entries"] == 0
    assert capture.summary()["capture_calls"] == 0
    assert capture.last_cache_state is None
    assert sim._last_reduced_real_cache_state is None
    assert _persistent_artifact_count(db_path, "equilibrium_post_record") == 0

    with pytest.raises(RuntimeError, match="builtin-backend-equilibrium"):
        capture.capture_equilibrium(sim, ok_result)

    assert capture.summary()["entries"] == 0
    assert capture.summary()["capture_calls"] == 0
    assert capture.last_cache_state is None
    assert sim._last_reduced_real_cache_state is None
    assert _persistent_artifact_count(db_path, "equilibrium_post_record") == 0


def test_pt1_capture_gate_curve_skips_non_cacheable_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-gate-status.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    failed_curve = {
        "source": "unit-test",
        "status": "unavailable",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    ok_curve = {
        "source": "unit-test",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }

    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=failed_curve)

    assert capture.summary()["entries"] == 0
    assert capture.summary()["capture_calls"] == 0
    assert capture.last_cache_state is None
    assert sim._last_reduced_real_cache_state is None
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 0

    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=ok_curve)

    assert capture.summary()["entries"] == 1
    assert capture.summary()["capture_calls_by_artifact"] == {
        "freeze_gate_curve": 1,
    }
    assert capture.last_cache_state == "live_fill"
    assert sim._last_reduced_real_cache_state == "live_fill"
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 1


def test_pt2_live_write_through_skips_unavailable_equilibrium_cells(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt2-live-write-through.db"
    live = PT0DeterminismStore("capture", db_path=db_path)

    _, live_calls = _run_capped_c2a_with_equilibrium_counter(live)

    assert live_calls > 0
    live_summary = live.summary()
    assert "equilibrium_post_record" not in live_summary[
        "cache_state_counts_by_artifact"
    ]
    assert live_summary["cache_state_counts_by_artifact"][
        "freeze_gate_curve"
    ]["live_fill"] >= 1
    assert _persistent_artifact_count(db_path, "equilibrium_post_record") == 0

    cached = PT0DeterminismStore("capture", db_path=db_path)
    with pytest.raises(
        AssertionError,
        match="PT-2 cached live run attempted backend.equilibrate",
    ):
        _run_capped_c2a_with_equilibrium_counter(cached, fail_live=True)


def test_pt2_alphamelts_write_through_populates_then_exact_hits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt2-alphamelts-write-through.db"
    live = PT0DeterminismStore("capture", db_path=db_path)
    live_provider = _CountingSilicateEquilibriumProvider()

    live_result = _run_authoritative_alphamelts_equilibrium(
        live,
        live_provider,
    )

    assert live_result.status == "ok"
    assert live_provider.calls == 1
    assert live_result.phase_masses_kg == {"liquid": 1.2345}
    assert (
        live_result.alphamelts_diagnostics["engine_version"]
        == "alphamelts-authentic-test"
    )
    live_counts = live.summary()["cache_state_counts_by_artifact"][
        "equilibrium_post_record"
    ]
    assert live_counts["live_fill"] == 1
    with sqlite3.connect(db_path) as conn:
        populated_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {PT1_EQUILIBRIUM_TABLE}
            WHERE artifact = 'equilibrium_post_record'
            """
        ).fetchone()[0]
        payload_bytes = conn.execute(
            f"""
            SELECT payload_bytes
            FROM {PT1_EQUILIBRIUM_TABLE}
            WHERE artifact = 'equilibrium_post_record'
            """
        ).fetchone()[0]
    assert populated_rows == 1
    assert b"alphamelts-authentic-test" in payload_bytes

    cached = PT0DeterminismStore("capture", db_path=db_path)
    cached_provider = _CountingSilicateEquilibriumProvider(fail_live=True)
    cached_result = _run_authoritative_alphamelts_equilibrium(
        cached,
        cached_provider,
    )

    assert cached_provider.calls == 0
    assert cached_result.status == live_result.status
    assert cached_result.phase_masses_kg == live_result.phase_masses_kg
    assert (
        cached_result.alphamelts_diagnostics["engine_version"]
        == "alphamelts-authentic-test"
    )
    cached_counts = cached.summary()["cache_state_counts_by_artifact"][
        "equilibrium_post_record"
    ]
    assert cached_counts["cached_exact"] == 1
    assert cached_counts["live_fill"] == 0
    cached_summary = cached.summary()
    assert cached_summary["key_drift_histogram"] == {}
    assert (
        cached_summary["key_drift_histogram_scope"]
        == "replay_mode_1_to_1_capture_replay_only"
    )
    assert cached.replay_sequence
    assert cached.capture_sequence == []
    with sqlite3.connect(db_path) as conn:
        rows_after_hit = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {PT1_EQUILIBRIUM_TABLE}
            WHERE artifact = 'equilibrium_post_record'
            """
        ).fetchone()[0]
    assert rows_after_hit == populated_rows


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


def test_tier_ceiling_cached_exact_refuses_physics_bucket_hit(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123446,
        temperature_K=1234.46,
    )
    h40_key = _c3a_ladder_key(
        "h40-row",
        feo_fraction=0.123444,
        temperature_K=1234.44,
    )
    db_path = tmp_path / "tier-exact.sqlite"
    _put_c3a_payload(db_path, h40_key, "h40")

    store = PT0DeterminismStore("capture", db_path=db_path)
    store.cache_tier_ceiling = "cached_exact"
    payload = store._lookup_optional(
        str(query_key["artifact"]),
        query_key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query_key),
    )

    assert payload is None
    assert store.replay_sequence == []


def test_tier_ceiling_cached_physics_bucket_refuses_interpolation(
    tmp_path: Path,
) -> None:
    from tests.test_reduced_real_cache_interpolation import (
        _interpolation_key,
        _put_interpolation_row,
    )

    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "tier-bucket.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=0.71, sio_pa=7.05)

    store = PT0DeterminismStore("capture", db_path=db_path)
    store.cache_tier_ceiling = "cached_physics_bucket"
    payload = store._lookup_optional(
        str(query["artifact"]),
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is None
    assert store.replay_sequence == []


def test_tier_ceiling_default_preserves_pre_c5_lookup_behavior(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123446,
        temperature_K=1234.46,
    )
    h40_key = _c3a_ladder_key(
        "h40-row",
        feo_fraction=0.123444,
        temperature_K=1234.44,
    )
    db_path = tmp_path / "tier-default.sqlite"
    _put_c3a_payload(db_path, h40_key, "h40")

    payload, store = _lookup_c3a_payload(db_path, query_key)

    assert payload["label"] == "h40"
    assert store.cache_tier_ceiling == "cached_interpolated"
    assert store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"
