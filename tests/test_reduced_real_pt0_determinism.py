from __future__ import annotations

import copy
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import simulator.reduced_real_determinism as rrd
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.capabilities import CapabilityProfile
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.optimize.determinism import deterministic_result_view
from simulator.reduced_real_determinism import (
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


def test_json_ready_nonfinite_error_names_payload_path() -> None:
    with pytest.raises(PT0NonFinitePayload, match=r"\$\.outer\.inner\[0\]"):
        canonical_json_bytes({"outer": {"inner": [float("inf")]}})


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


def _freeze_gate_key() -> dict:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    return canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        fe_redox_policy="intrinsic",
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
    assert key["source_module_digest"]["module_set"] == (
        "equilibrium-vapor-melt-backend-v2"
    )
    assert key["source_module_digest"]["sha256"]
    assert "simulator/melt_backend/base.py" in key["source_module_digest"]["paths"]
    assert "simulator/evaporation.py" in key["source_module_digest"]["paths"]
    assert (
        "engines/builtin/evaporation_flux.py"
        in key["source_module_digest"]["paths"]
    )
    assert "engines/builtin/vapor_pressure.py" in key["source_module_digest"]["paths"]
    assert set(key["data_digests"]) == {
        "setpoints",
        "feedstocks",
        "vapor_pressures",
        "species_formula_registry",
    }


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
    assert first["engine_version"] == "alpha-v1"
    assert _key_hash(different_provider) != _key_hash(first)
    assert _key_hash(different_version) != _key_hash(first)


def test_pt2_physics_bucket_ignores_recipe_setpoints_islands() -> None:
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic-cache-c1",
        engine_version="alpha-v1",
    )
    store = PT0DeterminismStore("capture")
    first = _build_pt0_sim(store)
    first.backend.is_available = lambda: True
    first._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    second = _build_pt0_sim(store)
    second.backend.is_available = lambda: True
    second._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    second.setpoints["optimizer_candidate_patch"] = {
        "mre_target_species": ["FeO"],
        "temperature_C": 1275.0,
    }

    first_key = store._equilibrium_key(first)
    second_key = store._equilibrium_key(second)

    assert _key_hash(first_key) != _key_hash(second_key)
    assert first_key["data_digests"]["setpoints"] != second_key["data_digests"]["setpoints"]
    assert _physics_bucket_hash(first_key) == _physics_bucket_hash(second_key)


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


def test_pt2_physics_bucket_keeps_sulfur_input_without_stage0_digest() -> None:
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
    assert "stage0_inventory_digest" not in canonical_json_bytes(bucket).decode("utf-8")

    history_changed = copy.deepcopy(sulfur_key)
    history_changed["sulfur_side"]["stage0_inventory_digest"] = "history-b"
    assert _physics_bucket_hash(history_changed) == _physics_bucket_hash(sulfur_key)


def test_pt2_persistent_physics_bucket_hit_is_not_cached_exact(tmp_path: Path) -> None:
    class NonStubBackend:
        def get_engine_version(self) -> str:
            return "non-stub-test"

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
    replay_sim.setpoints["optimizer_candidate_patch"] = {"temperature_C": 1275.0}

    payload = replay_store.cached_equilibrium(replay_sim)

    assert payload is not None
    assert replay_sim._last_reduced_real_cache_state == "cached_physics_bucket"
    counts = replay_store.summary()["cache_state_counts_by_artifact"][
        "equilibrium_post_record"
    ]
    assert counts["cached_physics_bucket"] == 1
    assert counts["cached_exact"] == 0


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


@pytest.mark.parametrize(
    "module_path",
    [
        "simulator/evaporation.py",
        "engines/builtin/evaporation_flux.py",
        "engines/builtin/vapor_pressure.py",
    ],
)
def test_pt2_source_module_digest_changes_with_payload_source(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
) -> None:
    rrd._source_module_digest.cache_clear()
    before = _freeze_gate_key()
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
        after = _freeze_gate_key()
    finally:
        rrd._source_module_digest.cache_clear()

    assert before["source_module_digest"]["sha256"] != after[
        "source_module_digest"
    ]["sha256"]
    assert _key_hash(before) != _key_hash(after)


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
