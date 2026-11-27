from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
import yaml

from engines.alphamelts import AlphaMELTSProvider
from scripts.seed_reduced_real_cache import merge_cache_source, seed_cache
import simulator.reduced_real_determinism as rrd
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.capabilities import CapabilityProfile
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.corpus_version import current_corpus_version
from simulator.fe_redox import (
    KRESS91_FO2_KEY_REFERENCE_T_K,
    FE_FEO_BUFFER_STANDARD,
    REDOX_STATE_REFERENCE_T_K,
    RedoxState,
    kress91_referenced_log_fO2,
)
from simulator.config import (
    DEFAULT_ALPHAMELTS_MODEL,
    resolve_alphamelts_python_api_model,
)
from simulator.grind_preflight import GrindSourceGateError
from simulator.melt_backend.base import (
    EquilibriumResult,
    RealBackendAuthority,
    RealBackendFamily,
)
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.melt_backend.melt_envelope import (
    MeltEnvelopeValidationError,
    melt_extrapolation_diagnostic,
)
from simulator.optimize.determinism import deterministic_result_view
from simulator.reduced_real_determinism import (
    ControlQuantization,
    PT0CacheMiss,
    PT0DeterminismStore,
    PT0InvalidControls,
    PT0NonFinitePayload,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentEquilibriumStore,
    PT1PersistentStoreCorrupt,
    canonical_physics_bucket_key_from_replay_key,
    canonical_json_bytes,
    canonical_replay_key,
)
from simulator.runner import _attach_composition_projected_liquidus_notice
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim
from tests.chemistry.test_evaporation_freeze_gate import (
    _install_magemin_subprocess_gate,
    _freeze_gate_redox_state_pair,
)
from tests.binding_admission_fixtures import (
    register_synthetic_gate_curve_producer,
    seed_gate_curve_memo_record,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(autouse=True)
def _synthetic_binding_receipt_for_pt0_store_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Give replay mechanics a test-only receipt, never a real engine pin."""
    from tests.binding_admission_fixtures import install_synthetic_binding_receipt

    if request.node.name == 'test_pt0_real_magemin_capped_replay_contract':
        source = DATA_DIR.parent / 'engines' / 'engines.local.toml'
        (tmp_path / source.name).write_bytes(source.read_bytes())
    install_synthetic_binding_receipt(
        tmp_path,
        monkeypatch,
        bind_direct_backend=False,
    )


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _pt0_setpoints() -> dict:
    setpoints = _load_yaml("setpoints.yaml")
    gate = dict(setpoints.get("freeze_gate", {}) or {})
    gate["enabled"] = True
    setpoints["freeze_gate"] = gate
    return setpoints


def _build_pt0_sim(store: PT0DeterminismStore | None, *, real_provider=False):
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _pt0_setpoints(),
        additives_kg={"K": 26.0, "Na": 12.0},
    )
    sim.configure_pt0_determinism_store(store)
    if not real_provider:
        sim._register_freeze_gate_liquid_fraction_providers()
        provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
        # Startup availability is not backend identity. Synthetic curve
        # mechanics register their producer explicitly at the call site.
        provider._ensure_backend = lambda: SimpleNamespace(is_available=lambda: True)
    return sim


def _build_pt0_sim_with_strict_vapor_default(
    store: PT0DeterminismStore | None,
):
    setpoints = _pt0_setpoints()
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = False
    # Keep the unrelated test alpha policy explicit; the vapor fallback setting
    # still matches production's strict default.
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        setpoints,
        additives_kg={"K": 26.0, "Na": 12.0},
        builtin_vapor_pressure=False,
    )
    sim.configure_pt0_determinism_store(store)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    provider._ensure_backend = lambda: SimpleNamespace(is_available=lambda: True)
    return sim


def _capture_pt0_gate_curve(
    db_path: Path,
    *,
    solidus_T_C: float,
    law_disclosures: Any = (),
) -> tuple[PT0DeterminismStore, Any, float, dict[str, Any]]:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim_with_strict_vapor_default(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(sim)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    curve = {
        "source": "unit-test:codec-transfer",
        "solidus_T_C": solidus_T_C,
        "liquidus_T_C": 1600.0,
        "path": ((solidus_T_C, 0.0), (1600.0, 1.0)),
    }
    seed_gate_curve_memo_record(sim, curve)
    store.capture_gate_curve(
        sim,
        fO2_log=fO2_log,
        curve=curve,
        repair_notices=[],
        law_disclosures=law_disclosures,
    )
    key_hash = str(store.capture_sequence[-1]["hash"])
    entry = store._entries[key_hash]
    payload = copy.deepcopy(entry["payload"])
    assert entry["key"].get("vapor_pressure_provider_selection") == (
        "builtin-vapor-pressure"
    )
    assert payload["authority"]["vapor_pressure"] == rrd._provider_identity(
        sim,
        ChemistryIntent.VAPOR_PRESSURE,
    )
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="freeze_gate_curve",
        key=entry["key"],
        key_bytes=str(entry["key_bytes"]).encode("utf-8"),
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
        repair_notices_json=entry["repair_notices_json"],
        law_disclosures=entry["law_disclosures"],
    )
    return store, sim, fO2_log, curve


def _select_synthetic_gate_curve_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_binding_admission as admission
    from tests.binding_admission_fixtures import install_synthetic_binding_receipt

    identity = admission.BindingIdentity(
        engine_id="magemin",
        model_id="ig",
        binding_revision="magemin-ig-r1",
        transport="subprocess",
    )
    provenance = {
        "binding_provenance_verifiable": True,
        "runtime_version": {
            "tag": "value",
            "value": "MAGEMin fixture runtime",
            "reason": None,
        },
        "runtime_artifacts": {
            "magemin_selected_binary": {"sha256": "a" * 64},
        },
    }
    install_synthetic_binding_receipt(
        tmp_path,
        monkeypatch,
        bind_direct_backend=True,
        identity_override=identity,
        provenance_override=provenance,
    )
    monkeypatch.setattr(
        admission,
        "selected_binding_identities",
        lambda _sim, *, artifact, provider_role=None: (
            (identity,) if artifact == "freeze_gate_curve" else ()
        ),
    )
    monkeypatch.setattr(
        admission,
        "selected_binding_provenance",
        lambda _identity, _sim: provenance,
    )


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


def _repair_notice_test_record() -> tuple[dict[str, str], dict[str, object]]:
    return (
        {
            "artifact": "freeze_gate_curve",
            "schema_version": "test",
            "tag": "notice-recapture",
            "namespace_id": "test:notice-recapture",
            "vapor_pressure_provider_selection": "test-vapor-provider",
            "sulfur_side": {},
            "model": {},
        },
        {
            "curve": {
                "source": "gate_liquid_fraction",
                "solidus_T_C": 1000.0,
                "liquidus_T_C": 1200.0,
                "path": [
                    {"temperature_C": 1000.0, "liquid_fraction": 0.0},
                    {"temperature_C": 1200.0, "liquid_fraction": 1.0},
                ],
                "is_liquidus_floor": False,
            },
        },
    )


def _store_repair_notice_test_record(
    store: PT0DeterminismStore,
    *,
    notices: list[dict[str, str]],
) -> None:
    key, payload = _repair_notice_test_record()
    store._store("freeze_gate_curve", key, payload, repair_notices=notices)


def _replay_repair_notice_test_record(
    store: PT0DeterminismStore,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    key, _payload = _repair_notice_test_record()
    sim = SimpleNamespace()
    entry = store._lookup("freeze_gate_curve", key, sim=sim)
    return entry, sim._reduced_real_repair_notices


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


def test_dispatch_only_reanchors_redox_state_after_pt0_fO2_quantization() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.2)
    raw_reference = state.log_fO2_at(REDOX_STATE_REFERENCE_T_K)
    expected_reference = store.quantized_controls(
        sim,
        fO2_log=raw_reference,
    )["fO2_log"]
    kernel = _CaptureDispatchKernel()
    sim._chem_kernel = kernel

    sim._dispatch_only(
        ChemistryIntent.SILICATE_LIQUIDUS,
        control_inputs={},
        fO2_log=raw_reference,
        redox_state=state,
    )

    forwarded_state = kernel.kwargs["redox_state"]
    assert forwarded_state.log_fO2_at(REDOX_STATE_REFERENCE_T_K) == pytest.approx(
        expected_reference
    )
    assert forwarded_state.log_fO2_at(1900.0) == pytest.approx(
        state.log_fO2_at(1900.0) + expected_reference - raw_reference
    )


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
    assert 'gate_curve_codec_version' not in key
    request_key = key
    key_hash = _key_hash(request_key)
    fine_key = _freeze_gate_key(control_quantization=ControlQuantization.PRODUCTION)
    fine_request_key = fine_key

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
    # 2026-07-07 (t-141 epoch): reduced-real key moves with the K standard-term
    # data digest; value matches the worker enumeration in golden-deltas.json
    # (the interim 62a416... reading in the refutation pass was the stale one).
    # 2026-07-12 combined-main rebaseline: the C6 window fold moves both the
    # parsed setpoints digest and recipe.py source-module digest. These are
    # cache-identity version moves; the production key was rerun after all
    # wave-09 folds landed.
    # 2026-07-12 control-audit contract fix: fO2-independent intents no longer
    # receive fO2_log in the dispatched request (CapabilityProfile.consumes_fO2
    # filtering at request construction), so the canonical replay payload — and
    # this key — legitimately moved. Determinism verified: byte-identical
    # across repeated canonical-environment runs. Reviewer-sandbox keys differ
    # (the documented reduced-real sandbox-drift class); the canonical
    # interpreter on main is the authority for this pin.
    # 2026-07-12 v0.6.0 gate: the t-172 token flip moved the serialized backend
    # identity ('stub' -> 'internal-analytical'), which is a cache-key input —
    # this IS the corpus_version-gated key migration the flip was deferred
    # for. Determinism re-proven by repeated canonical runs.
    # 2026-07-13 campaign epoch: 3089546 added the canonical EvalSpec cost
    # block, then bbf0134 moved the functional setpoints/vapor-pressure digests
    # (Cr stage-2 geometry, strict alpha default, and pO2 reference). Final
    # move: the em-dash->ASCII '--' fix in data/feedstocks.yaml (latin1 guard,
    # push-regression cluster C) changed the feedstock data digest, an EvalSpec
    # identity input, so this digest-based production key advances once more.
    # Two in-repo runs produced this identical key; cross-process determinism holds.
    # 2026-07-14 t-194 Cr grounding: the functional vapor-pressure data digest
    # moves because Cr gains its cited tier-1 evaporation_alpha block. Key
    # schema and quantization are unchanged; value captured by full-suite run.
    # 2026-07-14 t-194 Mn grounding: the parsed Mn alpha block advances only the
    # functional data digest; replay schema and control quantization are unchanged.
    # 2026-07-18 outward mol projection completeness: PT0 now projects every
    # live ledger account before canonicalizing provider overrides, so the
    # source-module identity advances without changing quantization tolerances.
    # 2026-08-05 batch data fold (1a1ab47, 7e6bebc, d1b4f5d): the parsed
    # carrier/P catalogs and MC-1 feedstock payloads are deliberate key inputs.
    # Two in-process evaluations and the cross-process test pin determinism.
    # 2026-10-31 b-929: Studio-2 canonical bytes after gate replay identity
    # began embedding the owner freeze-gate key. The key now binds the submitted
    # post-inert composition and inert membership, plus water/formula identity.
    # The PT-0 gate-curve codec version is pinned separately so its authorized
    # payload migration does not re-pin this production request identity.
    assert key_hash == "4cee08747e68f2c46df68ef2fbbe4b5141cd1395c9345d8315daa83305d5a2df"
    assert canonical_json_bytes(fine_key) == canonical_json_bytes(key)
    assert _key_hash(fine_request_key) == key_hash


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
        self.provider_id = provider_id or "alphamelts-diagnostic"
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


def _configure_authoritative_alphamelts_equilibrium(
    sim,
    provider: _CountingSilicateEquilibriumProvider,
    *,
    allow_internal_analytical_fallback: bool = True,
):
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        def is_available(self) -> bool:
            return True

        def get_engine_version(self) -> str:
            return provider.engine_version

    sim.backend = AlphaMELTSBackend()
    if not allow_internal_analytical_fallback:
        sim._backend_allows_internal_analytical_fallback = lambda: False

    def fail_backend_equilibrate(*_args, **_kwargs):
        raise AssertionError(
            "authoritative SILICATE_EQUILIBRIUM used backend.equilibrate"
        )

    sim.backend.equilibrate = fail_backend_equilibrate
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    return sim


def _run_authoritative_alphamelts_equilibrium(
    store: PT0DeterminismStore | None,
    provider: _CountingSilicateEquilibriumProvider,
    *,
    allow_internal_analytical_fallback: bool = True,
):
    sim = _configure_authoritative_alphamelts_equilibrium(
        _build_pt0_sim(store),
        provider,
        allow_internal_analytical_fallback=allow_internal_analytical_fallback,
    )
    return sim._get_equilibrium()


def test_pt2_alphamelts_diagnostic_no_store_bypasses_ledger_guard() -> None:
    provider = _CountingSilicateEquilibriumProvider()

    result = _run_authoritative_alphamelts_equilibrium(
        None,
        provider,
        allow_internal_analytical_fallback=False,
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


def test_malformed_receipt_identity_keeps_pt0_live_fill_cache_ineligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_binding_admission as admission
    from simulator.engine_binding_admission import (
        BindingIdentity,
        binding_admission_run_notice,
    )

    identity = BindingIdentity(
        engine_id="synthetic-fake",
        model_id="fixture-model-v1",
        binding_revision="synthetic-r1",
        transport="subprocess",
    )
    receipt_path = admission.binding_receipt_path()
    receipt = json.loads(receipt_path.read_text())
    receipt["entries"][0]["identity"] = None
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(
        admission,
        "binding_identity_for_backend",
        lambda _backend: identity,
    )

    store = PT0DeterminismStore("capture", db_path=tmp_path / "malformed.db")
    sim = _build_pt0_sim(store)

    assert store.cached_equilibrium(sim) is None
    notice = binding_admission_run_notice(sim)
    assert notice is not None
    assert notice["notices"][0]["reason"] == "receipt invalid"


def _run_capped_c2a(
    store: PT0DeterminismStore,
    *,
    max_hours: int = 1,
    disable_live: bool = False,
) -> dict:
    sim = _build_pt0_sim(store, real_provider=True)
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
    sim._freeze_gate_curve_from_gate_dispatch = (
        lambda reasons, *, fO2_log, repair_notices=None, redox_state=None: (
            _classified_gate_curve_for_test(sim, freeze_curve)
        )
    )
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


def _classified_gate_curve_for_test(sim: Any, curve: dict[str, Any]) -> dict[str, Any]:
    outcome = rrd.gate_curve_provider_outcome_for_status("ok")
    rrd.record_gate_curve_provider_outcome(sim, outcome)
    return dict(curve)


def _capped_trace(sim, store: PT0DeterminismStore, snapshots: list) -> dict:
    snapshot = snapshots[-1]
    curves = [
        entry["payload"]["curve"]
        for entry in store._entries.values()
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
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        _model = getattr(provider, "_test_model", "MELTSv1.0.2")
        _mode = getattr(provider, "_test_mode", "subprocess")

        def is_available(self):
            return True

    typed_backend = AlphaMELTSBackend()
    sim.backend = typed_backend
    provider._backend = typed_backend
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    return store._equilibrium_key(sim)


def _pt0_equilibrium_capture_context(
    store: PT0DeterminismStore,
    *,
    strict_vapor_default: bool = False,
) -> tuple[Any, EquilibriumResult]:
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        def get_engine_version(self) -> str:
            return "non-stub-test"

    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic",
        engine_version="alpha-v1",
    )
    sim = (
        _build_pt0_sim_with_strict_vapor_default(store)
        if strict_vapor_default
        else _build_pt0_sim(store)
    )
    sim.backend = AlphaMELTSBackend()
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    result = EquilibriumResult(
        temperature_C=float(sim.melt.temperature_C),
        pressure_bar=float(sim.melt.p_total_mbar) / 1000.0,
        phases_present=["liquid"],
        phase_masses_kg={"liquid": 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={"SiO2": 45.0, "FeO": 10.0},
        vapor_pressures_Pa={"SiO": 1.0},
        vapor_pressures_source={"SiO": "test"},
        fO2_log=sim._compute_intrinsic_melt_fO2(),
    )
    return sim, result


def _alphamelts_silicate_equilibrium_key(
    *,
    model: str = "MELTSv1.0.2",
    mode: str = "subprocess",
    engine_version: str = "alphamelts-authentic-test",
) -> dict:
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic",
        engine_version=engine_version,
    )
    provider._test_model = model
    provider._test_mode = mode
    return _silicate_equilibrium_key(provider)


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


def test_alphamelts_provider_key_partitions_mode_not_engine_version() -> None:
    base = _alphamelts_silicate_equilibrium_key()
    thermoengine = _alphamelts_silicate_equilibrium_key(mode="thermoengine")
    next_engine = _alphamelts_silicate_equilibrium_key(
        engine_version="alphamelts-authentic-test-2"
    )

    with pytest.raises(ValueError, match="no verified ALPHAMELTS_CALC_MODE"):
        _alphamelts_silicate_equilibrium_key(model="pMELTS")

    assert base["model"] == {
        "model": "MELTSv1.0.2",
        "mode": "subprocess",
        "magemin_database": None,
    }
    # Studio-2 canonical bytes at 35c987e96 after the equilibrium key was
    # narrowed to the submitted inert-filtered composition and its formulas.
    assert _key_hash(base) == (
        "79d74e79f9f00d458f048acbcd1da25f19cad645d0d0efc8f060cba22dd9373d"
    )
    assert thermoengine["model"]["mode"] == "thermoengine"
    assert "engine_version" not in next_engine["model"]
    assert _key_hash(base) != _key_hash(thermoengine)
    assert _key_hash(base) == _key_hash(next_engine)


def test_equilibrium_key_tracks_inert_and_refuses_cross_replay() -> None:
    capture = PT0DeterminismStore("capture")
    sodium_sim, result = _pt0_equilibrium_capture_context(capture)
    sodium_sim.inventory.inert_melt_components_kg.clear()
    sodium_sim.inventory.inert_melt_components_kg["Na2O"] = 1.0
    sodium_key = capture._equilibrium_key(sodium_sim)
    capture._store(
        "equilibrium_post_record",
        sodium_key,
        rrd.equilibrium_payload(sodium_sim, result),
        repair_notices=(),
    )

    replay = capture._clone_for_context("replay", persistent_cache=False)
    silica_sim, _ = _pt0_equilibrium_capture_context(replay)
    silica_sim.inventory.inert_melt_components_kg.clear()
    silica_sim.inventory.inert_melt_components_kg["SiO2"] = 1.0
    silica_key = replay._equilibrium_key(silica_sim)

    assert sodium_key != silica_key
    replay._authorize_cached_real_replay = lambda *args, **kwargs: None
    with pytest.raises(PT0CacheMiss):
        replay.replay_equilibrium(silica_sim)


def test_equilibrium_key_ignores_absent_species_formula_changes() -> None:
    from simulator.accounting.formulas import SpeciesFormula

    store = PT0DeterminismStore("capture")
    sim, _result = _pt0_equilibrium_capture_context(store)
    cleaned_mol = sim.atom_ledger.project_account_mol("process.cleaned_melt")
    assert float(cleaned_mol.get("CoO", 0.0)) <= 0.0
    baseline_key = store._equilibrium_key(sim)
    formula_species = {
        species for species, _elements in baseline_key["submitted_formula_identity"]
    }
    assert "SiO2" in formula_species
    assert "CoO" not in formula_species

    sim.species_formula_registry = dict(sim.species_formula_registry)
    sim.species_formula_registry["CoO"] = SpeciesFormula.from_atoms(
        "CoO",
        {"Co": 1, "O": 2},
    )

    assert store._equilibrium_key(sim) == baseline_key

    sim.species_formula_registry["SiO2"] = SpeciesFormula.from_atoms(
        "SiO2",
        {"Si": 1, "O": 1},
    )
    assert store._equilibrium_key(sim) != baseline_key


def test_equilibrium_key_uses_submitted_amount_when_inert_amount_changes() -> None:
    from simulator.accounting.ledger import AtomLedger

    store = PT0DeterminismStore("capture")
    first, _first_result = _pt0_equilibrium_capture_context(store)
    second, _second_result = _pt0_equilibrium_capture_context(store)
    cleaned_account = "process.cleaned_melt"

    for sim, na2o_mol in ((first, 1.0), (second, 2.0)):
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            cleaned_account,
            {"SiO2": 1.0, "FeO": 2.0, "Na2O": na2o_mol},
            source="b-929 inert amount identity",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim.inventory.inert_melt_components_kg.clear()
        sim.inventory.inert_melt_components_kg["Na2O"] = (
            ledger.project_account_kg(cleaned_account)["Na2O"]
        )

    first_na2o_kg = first.atom_ledger.project_account_kg(
        cleaned_account
    )["Na2O"]
    second_na2o_kg = second.atom_ledger.project_account_kg(
        cleaned_account
    )["Na2O"]

    first_mol = first.atom_ledger.project_account_mol(cleaned_account)
    second_mol = second.atom_ledger.project_account_mol(cleaned_account)
    first_payload = first._silicate_provider_melt_inputs(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        first_mol,
    )
    second_payload = second._silicate_provider_melt_inputs(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        second_mol,
    )
    assert first_payload == second_payload
    assert first_na2o_kg != second_na2o_kg
    assert store._equilibrium_key(first) == store._equilibrium_key(second)


def test_gate_replay_key_uses_pt0_quantized_pressure_for_provider_identity() -> None:
    def key_at(pressure_bar: float) -> dict[str, Any]:
        store = PT0DeterminismStore("capture")
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim.melt.p_total_mbar = pressure_bar * 1000.0
        return canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=-9.0,
            fe_redox_policy="intrinsic",
            control_quantization=store._control_quantization,
        )

    lower = key_at(0.014999)
    upper = key_at(0.015001)

    assert (
        lower["controls"]["pressure_bar"]
        == upper["controls"]["pressure_bar"]
    )
    assert lower["freeze_gate_cache_key"] == upper["freeze_gate_cache_key"]
    assert lower == upper


def test_gate_replay_key_uses_pt0_quantized_fO2_for_provider_identity() -> None:
    def key_at(fO2_log: float) -> dict[str, Any]:
        store = PT0DeterminismStore("capture")
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        return canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
            control_quantization=store._control_quantization,
        )

    lower = key_at(-9.500001)
    upper = key_at(-9.499999)

    assert lower["controls"]["log_fO2"] == upper["controls"]["log_fO2"]
    assert lower["freeze_gate_cache_key"] == upper["freeze_gate_cache_key"]
    assert lower == upper


def _thermoengine_pt0_identity(model: str) -> tuple[dict, dict]:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)

    class ThermoEngineBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.THERMOENGINE
        _model = model
        _mode = None

        def is_available(self) -> bool:
            return True

    backend = ThermoEngineBackend()
    provider = AlphaMELTSProvider(backend=backend)
    sim.backend = backend
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )

    key = store._equilibrium_key(sim)
    authority = rrd._equilibrium_record_authority(
        sim,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
    )

    return key, authority


def _alphamelts_pt0_identity(
    model: str,
    *,
    mode: str = "subprocess",
) -> tuple[dict, dict]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    backend = AlphaMELTSBackend(model_name=model)
    backend._mode = mode
    if mode == "python_api":
        backend._python_api_model = resolve_alphamelts_python_api_model(model)
    provider = AlphaMELTSProvider(backend=backend)
    sim.backend = backend
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )

    key = store._equilibrium_key(sim)
    authority = rrd._equilibrium_record_authority(
        sim,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
    )
    return key, authority


def test_blank_alphamelts_subprocess_model_resolves_to_default():
    key, authority = _alphamelts_pt0_identity("")

    assert key["model"]["model"] == DEFAULT_ALPHAMELTS_MODEL
    assert authority["provider"]["model"] == DEFAULT_ALPHAMELTS_MODEL


def test_explicit_default_alphamelts_subprocess_model_is_in_pt0_identity():
    key, authority = _alphamelts_pt0_identity(DEFAULT_ALPHAMELTS_MODEL)

    assert key["model"]["model"] == DEFAULT_ALPHAMELTS_MODEL
    assert authority["provider"]["model"] == DEFAULT_ALPHAMELTS_MODEL


def test_blank_and_explicit_default_alphamelts_identity_match_field_by_field():
    blank_key, blank_authority = _alphamelts_pt0_identity("")
    explicit_key, explicit_authority = _alphamelts_pt0_identity(
        DEFAULT_ALPHAMELTS_MODEL
    )

    assert blank_key == explicit_key
    assert blank_authority == explicit_authority

    blank_non_model_key = copy.deepcopy(blank_key)
    explicit_non_model_key = copy.deepcopy(explicit_key)
    del blank_non_model_key["model"]["model"]
    del explicit_non_model_key["model"]["model"]
    assert blank_non_model_key == explicit_non_model_key

    blank_non_model_provider = dict(blank_authority["provider"])
    explicit_non_model_provider = dict(explicit_authority["provider"])
    del blank_non_model_provider["model"]
    del explicit_non_model_provider["model"]
    assert blank_non_model_provider == explicit_non_model_provider


@pytest.mark.parametrize(
    ("model", "expected_identity"),
    [
        ("", "alphamelts-diagnostic"),
        (DEFAULT_ALPHAMELTS_MODEL, DEFAULT_ALPHAMELTS_MODEL),
        ("pMELTS", "pMELTS"),
    ],
)
def test_python_api_identity_pins_current_model_and_provider_fields(
    model: str,
    expected_identity: str,
) -> None:
    key, authority = _alphamelts_pt0_identity(model, mode="python_api")

    assert key["model"] == {
        "model": expected_identity,
        "mode": "python_api",
        "magemin_database": None,
    }
    assert authority["provider"]["resolved_provider_id"] == (
        "alphamelts-diagnostic"
    )
    assert authority["provider"]["model"] == expected_identity


@pytest.mark.parametrize(
    ("model", "expected_hash"),
    [
        (None, "aba38d237aa4c4e57ce0b1d4331e646e5f224e7baa349354b085aacff441637e"),
        ("", "20a65e05d5acf3303c5ac794dc455d45359c34f61602ddd7e10ecb5b06b6ce76"),
        (DEFAULT_ALPHAMELTS_MODEL, "250646f69b23a91fe5d1f0904fdeab2f206432e6f28f9472bff84ee032b54cb3"),
        ("pMELTS", "7b50ed0b4168c94d12ca1ae38e7a64b2e049942f16757765ea359ad52106eef1"),
        ("MELTSv1.1.0", "238d7c7582835a6d4983861344fbc8fa8cc05dbdb96c45eec242f58f5b83614d"),
        ("MELTSv1.2.0", "70ed32461ba7e9755beb1e95a34b356837e7ae09f3090b1ca150a08f4e39b273"),
    ],
)
def test_python_api_accepted_replay_key_hashes_include_provider_input_identity(
    model: str | None,
    expected_hash: str,
) -> None:
    # Studio-2 canonical hashes at 35c987e96 after keying on submitted inputs.
    key, _authority = _alphamelts_pt0_identity(model, mode="python_api")

    actual_hash = hashlib.sha256(rrd.canonical_json_bytes(key)).hexdigest()
    assert actual_hash == expected_hash


@pytest.mark.parametrize(
    ("model", "expected_hash"),
    [
        (None, "70082de80468f7c4239446ac453e340ccc53c58e7f06d30ed4340a97abf9dae1"),
        ("", "f2804f1dfd4bcb2835d0ab1e33a8b3f2ba2fced471d81ce21cbafe03de3da88c"),
        (DEFAULT_ALPHAMELTS_MODEL, "f2804f1dfd4bcb2835d0ab1e33a8b3f2ba2fced471d81ce21cbafe03de3da88c"),
        ("pMELTS", "bba92d10ca74b54a58eb5457558079a5c5ce4e196bac55dbeab2937ecf2afd97"),
        ("MELTSv1.1.0", "4a6ccd886a196e09ad376cb783b9c689d5210e5d5e797409355dccd13e8cdfb5"),
        ("MELTSv1.2.0", "771a9c502a0e0b789ca0f2fc1ccb95015cfcf7015d972777a6469adf02a9611f"),
    ],
)
def test_cached_real_python_api_replay_key_hashes_include_provider_input_identity(
    tmp_path: Path,
    model: str | None,
    expected_hash: str,
) -> None:
    from simulator.backends import CachedRealBackend, normalize_cached_real_config

    config = normalize_cached_real_config(
        {
            "db_path": str(tmp_path / "identity.db"),
            "miss_policy": "fail-loud",
            "authorized_backend_name": "alphamelts",
            "authorized_mode": "python_api",
            "authorized_model": model,
        }
    )
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(None)
    sim.backend = CachedRealBackend(config=config)
    sim._chem_registry._authoritative.pop(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        None,
    )

    key = store._equilibrium_key(sim)
    actual_hash = hashlib.sha256(rrd.canonical_json_bytes(key)).hexdigest()

    # Studio-2 canonical hashes at 35c987e96 after identity was narrowed to
    # submitted provider inputs. The cached-real fixture has no PT-0 store,
    # while the live fixture uses fine PT-0 quantization; their submitted oxide
    # values differ by a few last-place digits, so those replay identities must
    # differ even though their backend model fields match.
    assert actual_hash == expected_hash
    if model is None:
        live_key, _authority = _alphamelts_pt0_identity(
            model,
            mode="python_api",
        )
        assert key["model"] == live_key["model"]
        assert key["composition_mol_fraction"] != (
            live_key["composition_mol_fraction"]
        )
        payload = {"authority": _alphamelts_record_authority()}
        store._store("equilibrium_post_record", live_key, payload)
        with pytest.raises(PT0CacheMiss):
            store._lookup("equilibrium_post_record", key)


def test_blank_thermoengine_model_resolves_to_default_in_pt0_identity() -> None:
    key, authority = _thermoengine_pt0_identity("")

    assert key["model"]["model"] == "MELTSv1.0.2"
    assert authority["provider"]["model"] == "MELTSv1.0.2"


@pytest.mark.parametrize("model", ["MELTSv1.0.2", "pMELTS"])
def test_nonblank_thermoengine_model_passes_through_pt0_identity(model: str) -> None:
    key, authority = _thermoengine_pt0_identity(model)

    assert key["model"]["model"] == model
    assert authority["provider"]["model"] == model


def test_blank_and_explicit_default_thermoengine_identity_are_equal() -> None:
    blank_key, blank_authority = _thermoengine_pt0_identity("")
    explicit_key, explicit_authority = _thermoengine_pt0_identity("MELTSv1.0.2")

    assert blank_key == explicit_key
    assert blank_authority == explicit_authority


def test_non_alphamelts_magemin_shadow_key_identity_stays_byte_identical() -> None:
    key = _freeze_gate_key()

    assert key["namespace_id"] == "magemin:freeze-gate-curve"
    assert "provider_selection" not in key
    assert key["model"] == {
        "model": "magemin-shadow",
        "mode": "subprocess",
        "magemin_database": None,
    }
    # No engine_version key is folded into the non-alphamelts model identity (the exact dicts
    # above assert that — the BH-055/056 folding is alphamelts-only). The FULL-key byte-identity
    # of this same _freeze_gate_key() is anchored by the sibling
    # test_control_quantization_default_production_key_is_byte_identical hash pin; we do NOT
    # duplicate that hardcoded hash here (it rebaselines on legitimate data/corpus moves, and a
    # stale second copy would spuriously re-break this test).
    assert "engine_version" not in key["model"]
    assert "engine_version" not in key
    assert "total_submitted_amount_kg" not in key


def test_magemin_freeze_gate_memo_key_preserves_extrapolated_boundary_identity() -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    sim = _build_pt0_sim(PT0DeterminismStore("capture"))
    sim._liquid_fraction_provider = "magemin"
    def key_for(sio2_wt_pct: float):
        composition_wt_pct = {
            "SiO2": sio2_wt_pct,
            "MgO": 100.0 - sio2_wt_pct,
        }
        mol_by_species = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            mol_by_species,
            source="b-1225 freeze-gate cache boundary pin",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        key, reason = sim._freeze_gate_cache_key_and_domain(
            pressure_bar=1.0,
            fO2_log=-9.0,
        )
        return (
            key,
            reason,
            composition_wt_pct,
            sim._freeze_gate_current_magemin_domain_identity,
        )

    inside, inside_reason, _inside_composition, inside_identity = key_for(30.001)
    # PT-0 capture uses six significant figures before the gate sees this
    # binary bulk. d-447 keeps positive 29.999 wt% silica on the flagged
    # extrapolation path even though both compositions share a quantized key.
    outside, outside_reason, outside_composition, outside_identity = key_for(
        29.999
    )

    from engines.magemin.domain import MAGEMinDomainGate

    valid, warnings, reason = MAGEMinDomainGate.validate_with_reason(
        outside_composition
    )

    assert inside.composition_mol_fractions == outside.composition_mol_fractions
    assert inside_reason is None
    assert outside_reason is None
    assert inside_identity.silica_verdict == 'in_band'
    assert outside_identity.silica_verdict == 'extrapolated'
    assert not inside_identity.matches(outside_identity)
    assert valid is True
    assert reason is None
    assert any(
        "authority=extrapolated" in warning
        and "certified_band=SiO2 [30, 80] wt%" in warning
        for warning in warnings
    )
    assert inside != outside


def test_magemin_freeze_gate_key_carries_projection_threshold_verdict(
    monkeypatch,
) -> None:
    from engines.magemin.domain import MAGEMinDomainGate
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    sim = _build_pt0_sim(PT0DeterminismStore("capture"))
    sim._liquid_fraction_provider = "magemin"
    gate_inputs = []
    original_validate = MAGEMinDomainGate.validate_with_reason

    def capture_exact_gate_input(composition_wt_pct):
        gate_inputs.append(dict(composition_wt_pct))
        return original_validate(composition_wt_pct)

    monkeypatch.setattr(
        MAGEMinDomainGate,
        "validate_with_reason",
        staticmethod(capture_exact_gate_input),
    )

    def key_for(p2o5_wt_pct: float):
        composition_wt_pct = {
            "SiO2": 50.0,
            "MgO": 50.0 - p2o5_wt_pct,
            "P2O5": p2o5_wt_pct,
        }
        mol_by_species = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            mol_by_species,
            source="b-1225 database projection threshold pin",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        key = sim._freeze_gate_cache_key_and_domain(
            pressure_bar=1.0,
            fO2_log=-9.0,
        )[0]
        return key, sim._freeze_gate_current_magemin_domain_identity

    admitted, admitted_identity = key_for(0.999)
    refused, refused_identity = key_for(1.001)

    assert admitted.composition_mol_fractions == (
        refused.composition_mol_fractions
    )
    assert admitted_identity.projection_threshold_verdict == 'clear'
    assert refused_identity.projection_threshold_verdict == 'refused'
    assert refused_identity.refusal_reason.startswith(
        "composition_projected_over_threshold:"
    )
    assert admitted != refused
    # The exact submitted P2O5 bulk reaches the live domain gate before the
    # subsequent ig-order projection drops it.
    assert len(gate_inputs) == 4
    assert "P2O5" in gate_inputs[0]
    assert "P2O5" not in gate_inputs[1]
    assert "P2O5" in gate_inputs[2]
    assert "P2O5" not in gate_inputs[3]
    submitted_p2o5 = [
        float(bulk["P2O5"])
        for bulk in gate_inputs
        if "P2O5" in bulk
    ]
    assert len(submitted_p2o5) == 2
    assert submitted_p2o5[0] < 1.0 < submitted_p2o5[1]
    assert any("P2O5" not in bulk for bulk in gate_inputs)


def test_magemin_freeze_gate_projection_threshold_uses_ferric_folded_bulk():
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    sim = _build_pt0_sim(PT0DeterminismStore("capture"))
    sim._liquid_fraction_provider = "magemin"

    def identity_for(p2o5_wt_pct: float):
        composition_wt_pct = {
            "SiO2": 50.0,
            "Fe2O3": 10.0,
            "MgO": 40.0 - p2o5_wt_pct,
            "P2O5": p2o5_wt_pct,
        }
        composition_mol = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            composition_mol,
            source="r23 ferric projection-threshold regression",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim._freeze_gate_cache_key_and_domain(
            pressure_bar=1.0,
            fO2_log=-9.0,
        )
        return sim._freeze_gate_current_magemin_domain_identity

    below_threshold = identity_for(0.985)
    above_threshold = identity_for(0.995)

    assert below_threshold.projection_threshold_verdict == "clear"
    assert above_threshold.projection_threshold_verdict == "refused"
    assert above_threshold.refusal_reason.startswith(
        "composition_projected_over_threshold:"
    )
    assert not below_threshold.matches(above_threshold)


@pytest.mark.parametrize(
    "sio2_wt_pct",
    [[], [30.0], [30.0, "80"], [10**400, 80.0], [0.0, 100.0]],
    ids=["empty", "wrong-arity", "non-numeric", "float-overflow", "wrong-band"],
)
def test_gate_replay_rejects_malformed_required_silica_diagnostic(
    sio2_wt_pct,
):
    store = PT0DeterminismStore("capture")
    from simulator.evaporation import _FlaggedFreezeGateCurveCacheKey
    from simulator.melt_backend.magemin import MAGEMinDomainIdentity

    def key_with_required_identity():
        key = _freeze_gate_key()
        original = key["freeze_gate_cache_key"]
        identity = MAGEMinDomainIdentity(
            "extrapolated", "clear", (30.0, 80.0), None,
        )
        return {
            **key,
            "freeze_gate_cache_key": _FlaggedFreezeGateCurveCacheKey(
                schema=original.schema,
                pressure_bar=original.pressure_bar,
                fO2_log=original.fO2_log,
                composition_mol_fractions=original.composition_mol_fractions,
                water_mol_fraction=original.water_mol_fraction,
                formula_registry_digest=original.formula_registry_digest,
                inert_species=original.inert_species,
                magemin_domain_identity=identity,
                redox_state_identity=original.redox_state_identity,
            ),
        }

    valid_key = key_with_required_identity()
    valid_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "authority": "extrapolated",
            "certified_band": {"sio2_wt_pct": [30.0, 80.0]},
            "is_liquidus_floor": False,
        },
    }
    store._store(
        "freeze_gate_curve",
        valid_key,
        valid_payload,
        law_disclosures=[],
    )
    assert store._lookup_first_available(
        "freeze_gate_curve",
        (valid_key,),
    ) == valid_payload

    key = {
        **valid_key,
        "replay_identity": f"malformed-silica-diagnostic-{sio2_wt_pct!r}",
    }
    store._store(
        "freeze_gate_curve",
        key,
        {
            "curve": {
                "source": "gate_liquid_fraction",
                "solidus_T_C": 1200.0,
                "liquidus_T_C": 1300.0,
                "path": [
                    {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                    {"temperature_C": 1300.0, "liquid_fraction": 1.0},
                ],
                "authority": "extrapolated",
                "certified_band": {"sio2_wt_pct": sio2_wt_pct},
                "is_liquidus_floor": False,
            },
        },
        law_disclosures=[],
    )

    with pytest.raises(PT0CacheMiss):
        store._lookup_first_available(
            "freeze_gate_curve",
            (key,),
        )


def test_amount_is_present_only_for_extensive_alphamelts_composite() -> None:
    extensive = _alphamelts_silicate_equilibrium_key()
    intensive_magemin = _freeze_gate_key()

    assert extensive["namespace_id"] == "alphamelts:equilibrium-composite"
    assert extensive["total_submitted_amount_kg"] > 0.0
    assert intensive_magemin["namespace_id"] == "magemin:freeze-gate-curve"
    assert "total_submitted_amount_kg" not in intensive_magemin


def test_magemin_database_identity_splits_intensive_gate_keys() -> None:
    def key_for(database: str) -> dict:
        store = PT0DeterminismStore("capture")
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim._register_freeze_gate_liquid_fraction_providers()
        provider = sim._chem_registry.fallback_for(
            ChemistryIntent.GATE_LIQUID_FRACTION
        )
        provider._backend = SimpleNamespace(_database=database)
        return canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
            fe_redox_policy="intrinsic",
        )

    ig = key_for("ig")
    igad = key_for("igad")

    assert ig["model"]["magemin_database"] == "ig"
    assert igad["model"]["magemin_database"] == "igad"
    assert _key_hash(ig) != _key_hash(igad)


def test_silicate_dispatch_excludes_explicit_inert_species_from_mol_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _build_pt0_sim(PT0DeterminismStore("capture"))
    from simulator.accounting.ledger import AtomLedger

    ledger = AtomLedger(registry=sim.atom_ledger.registry)
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"SiO2": 1.0, "FeO": 2.0, "Na2O": 4.0},
        source="b-929 dispatch identity pin",
        material_origin="feedstock",
    )
    sim.atom_ledger = ledger
    sim.inventory.inert_melt_components_kg.clear()
    sim.inventory.inert_melt_components_kg["Na2O"] = 1.0
    observed: dict[str, Any] = {}

    class CapturingKernel:
        def dispatch(self, _intent, **kwargs):
            observed.update(kwargs)
            return None

    monkeypatch.setattr(sim, "_require_chem_kernel", lambda: CapturingKernel())
    sim._dispatch_only(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        control_inputs={},
        fO2_log=-9.0,
    )

    submitted_mol = observed["account_mol_overrides"]["process.cleaned_melt"]
    # Literal values captured from the base 952e7730f dispatch path: PT-0
    # canonicalizes the whole ledger before excluding inert Na2O.
    assert submitted_mol == {
        "FeO": 1.9999700000000002,
        "SiO2": 1.00002,
    }
    assert "Na2O" not in submitted_mol


def test_current_magemin_and_internal_analytical_replay_key_bytes() -> None:
    # Studio-2 canonical bytes at 35c987e96 after gate keys began embedding the
    # owner freeze-gate provider-input identity.
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    provider._backend = SimpleNamespace(_database="ig")
    magemin_key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        fe_redox_policy="intrinsic",
    )
    assert 'gate_curve_codec_version' not in magemin_key
    magemin_bytes = canonical_json_bytes(magemin_key)
    assert len(magemin_bytes) == 1319
    assert hashlib.sha256(magemin_bytes).hexdigest() == (
        "00f40658d0a344757029e661d4574a75c72b13521066689f205e6194a5dcea0a"
    )

    generic_store = PT0DeterminismStore("capture")
    generic_sim = _build_pt0_sim(generic_store)
    generic_key = generic_store._equilibrium_key(generic_sim)
    generic_bytes = canonical_json_bytes(generic_key)
    assert generic_key["namespace_id"] == "internal-analytical:equilibrium-composite"
    assert len(generic_bytes) == 1687
    assert hashlib.sha256(generic_bytes).hexdigest() == (
        "f23032fcd2ddba188fccad867d238b352c63e4ca53b77c3dc643baae0af422f9"
    )


def test_gate_replay_key_uses_freeze_gate_provider_input_identity() -> None:
    from simulator.accounting.formulas import SpeciesFormula
    from simulator.evaporation import _FREEZE_GATE_COMPOSITION_SPECIES

    class AlphaMELTSKeyBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS
        _model = "MELTSv1.0.2"
        _mode = "subprocess"

        def is_available(self):
            return True

    def build_sim():
        sim = _build_pt0_sim(PT0DeterminismStore("capture"))
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        return sim

    def gate_key(sim):
        return canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=-9.0,
            fe_redox_policy="intrinsic",
        )

    def legacy_identity(key):
        return {
            name: value
            for name, value in key.items()
            if name != "freeze_gate_cache_key"
        }

    inert_sim = build_sim()
    cleaned_before = inert_sim.atom_ledger.project_account_mol(
        "process.cleaned_melt"
    )
    inert_sim.inventory.inert_melt_components_kg.clear()
    inert_sim.inventory.inert_melt_components_kg["Na2O"] = 1.0
    sodium_inert_key = gate_key(inert_sim)
    assert gate_key(inert_sim) == sodium_inert_key

    inert_sim.inventory.inert_melt_components_kg.clear()
    inert_sim.inventory.inert_melt_components_kg["SiO2"] = 1.0
    silica_inert_key = gate_key(inert_sim)
    assert inert_sim.atom_ledger.project_account_mol(
        "process.cleaned_melt"
    ) == cleaned_before
    assert legacy_identity(sodium_inert_key) != legacy_identity(
        silica_inert_key
    )
    assert sodium_inert_key != silica_inert_key
    assert (
        sodium_inert_key["freeze_gate_cache_key"]
        != silica_inert_key["freeze_gate_cache_key"]
    )
    legacy_sim = build_sim()
    legacy_sim.inventory.inert_melt_components_kg.clear()
    legacy_key = legacy_identity(gate_key(legacy_sim))
    legacy_store = PT0DeterminismStore("capture")
    legacy_store._store(
        "freeze_gate_curve",
        legacy_key,
        {
            "curve": {
                "source": "gate_liquid_fraction:legacy",
                "status": "in_range",
                "solidus_T_C": 1000.0,
                "liquidus_T_C": 1200.0,
                "is_liquidus_floor": False,
            }
        },
        repair_notices=(),
    )
    legacy_replay = legacy_store._clone_for_context(
        "replay",
        persistent_cache=False,
    )
    legacy_replay._authorize_cached_real_replay = lambda *args, **kwargs: None
    replay_sim = build_sim()
    replay_sim.inventory.inert_melt_components_kg.clear()
    replay_sim.inventory.inert_melt_components_kg["Na2O"] = 1.0
    assert legacy_identity(gate_key(replay_sim)) != legacy_key
    with pytest.raises(PT0CacheMiss):
        legacy_replay.replay_gate_curve(replay_sim, fO2_log=-9.0)

    current_store = PT0DeterminismStore("capture")
    sodium_curve = {
        "source": "gate_liquid_fraction:test-sodium-inert",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1200.0,
        "is_liquidus_floor": False,
    }
    current_store._store(
        "freeze_gate_curve",
        sodium_inert_key,
        {"curve": sodium_curve},
        repair_notices=(),
    )
    current_replay = current_store._clone_for_context(
        "replay",
        persistent_cache=False,
    )
    current_replay._authorize_cached_real_replay = lambda *args, **kwargs: None
    silica_replay_sim = build_sim()
    silica_replay_sim.inventory.inert_melt_components_kg.clear()
    silica_replay_sim.inventory.inert_melt_components_kg["SiO2"] = 1.0
    assert legacy_identity(gate_key(silica_replay_sim)) != legacy_identity(
        sodium_inert_key
    )
    with pytest.raises(PT0CacheMiss):
        current_replay.replay_gate_curve(
            silica_replay_sim,
            fO2_log=-9.0,
        )
    assert len(current_replay.misses) == 1

    water_sim = build_sim()
    water_sim._chem_registry = None
    water_sim._register_freeze_gate_liquid_fraction_providers = lambda: None
    water_sim.backend = AlphaMELTSKeyBackend()
    dry_key = gate_key(water_sim)
    cleaned_mol = water_sim.atom_ledger.project_account_mol(
        "process.cleaned_melt"
    )
    oxide_total_mol = sum(
        float(cleaned_mol.get(species, 0.0))
        for species in _FREEZE_GATE_COMPOSITION_SPECIES
    )
    water_sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"H2O": oxide_total_mol * 0.004 / 100.0},
        source="b-929 replay key water identity",
        material_origin="feedstock",
    )
    wet_key = gate_key(water_sim)
    assert wet_key != dry_key
    assert wet_key["freeze_gate_cache_key"] != dry_key["freeze_gate_cache_key"]

    formula_sim = build_sim()
    formula_sim._chem_registry = None
    formula_sim._register_freeze_gate_liquid_fraction_providers = lambda: None
    formula_sim.backend = None
    baseline_formula_key = gate_key(formula_sim)
    formula_sim.species_formula_registry = dict(
        formula_sim.species_formula_registry
    )
    formula_sim.species_formula_registry["SiO2"] = (
        SpeciesFormula.from_atoms("SiO2", {"Si": 1, "O": 1})
    )
    changed_formula_key = gate_key(formula_sim)
    assert changed_formula_key != baseline_formula_key
    assert (
        changed_formula_key["freeze_gate_cache_key"]
        != baseline_formula_key["freeze_gate_cache_key"]
    )


def test_magemin_shadow_fallback_under_alphamelts_config_excludes_engine_version(
    monkeypatch,
) -> None:
    # A magemin-shadow gate fallback can resolve UNDER an alphamelts-authorized cached-real
    # config (the real alphamelts backend is unavailable, so the gate falls back to the
    # magemin shadow). engine_version must NOT be folded into the shadow provider's cache key:
    # the shadow identity must not depend on the alphamelts binary version, else an alphamelts
    # version bump spuriously invalidates the magemin-shadow cache. Regression for the
    # BH-055/056 config-misclassification path — a RESOLVED non-alphamelts provider is not an
    # alphamelts key identity even when the authorized backend is alphamelts.
    sim = SimpleNamespace(backend=SimpleNamespace())
    monkeypatch.setattr(
        rrd,
        "_cached_real_config",
        lambda _sim: SimpleNamespace(authorized_backend_name="alphamelts"),
    )
    magemin_identity = {
        "resolved_provider_id": "magemin-shadow",
        "authoritative_provider_id": None,
    }
    # Even though the cached-real config authorizes alphamelts, the resolved shadow provider
    # is NOT an alphamelts key identity.
    assert rrd._is_alphamelts_key_identity(sim, magemin_identity) is False
    # ...so no engine_version is folded into its cache key.
    assert (
        rrd._cache_key_engine_version(
            sim,
            ChemistryIntent.GATE_LIQUID_FRACTION,
            provider_identity=magemin_identity,
            provider_role="fallback",
        )
        is None
    )


def test_failed_version_getter_is_typed_pt1_provenance() -> None:
    class BrokenVersionBackend:
        def get_engine_version(self) -> str:
            raise RuntimeError("version unavailable")

    provenance = json.loads(
        rrd._backend_version_for_key(BrokenVersionBackend())
    )

    assert provenance == {
        "tag": "unknown",
        "value": None,
        "reason": "version getter failed: RuntimeError",
    }


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
    payload = {
        "authority": _alphamelts_record_authority(),
        "equilibrium_result": {
            "status": "ok",
            "temperature_C": 1200.0,
            "pressure_bar": 0.001,
            "liquid_fraction": 0.5,
            "phase_assemblage_available": True,
            "liquidus_T_C": 1300.0,
        },
        "last_vapor_pressures_source": {},
        "last_vapor_pressure_diagnostic": {},
        "label": label,
    }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _strict_vapor_key() -> dict:
    return {
        "namespace_id": "alphamelts:equilibrium-composite",
        "composition_mol_fraction": [["Na2O", 0.1], ["SiO2", 0.9]],
        "controls": {"T_K": 1473.15, "log_fO2": -8.0, "pressure_bar": 0.001},
        "redox": {"fe_redox_policy": "intrinsic", "fe_split": {}},
        "vapor_pressure_provider_selection": "builtin-vapor-pressure",
        "sulfur_side": {"S_input_ppm": 0.0, "stage0_inventory_digest": "test"},
        "model": {"model": "alphamelts-diagnostic", "mode": "AlphaMELTSProvider"},
    }


def _alphamelts_record_authority() -> dict:
    return {
        "schema": "reduced-real-authority-v1",
        "evidence_class": "melts",
        "backend_family": "alphamelts",
        "backend": {
            "backend_name": "AlphaMELTSBackend",
            "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
        },
        "provider": {
            "resolved_provider_id": "alphamelts-diagnostic",
            "resolved_role": "authoritative",
            "authoritative_provider_id": "alphamelts-diagnostic",
            "fallback_provider_id": None,
        },
        "vapor_pressure": {
            "resolved_provider_id": "builtin-vapor-pressure",
            "resolved_role": "authoritative",
            "authoritative_provider_id": "builtin-vapor-pressure",
            "fallback_provider_id": None,
            "fallback_allowed": False,
        },
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


def test_equilibrium_replay_restores_requested_fo2_not_printed_echo(
    tmp_path: Path,
):
    requested_fO2_log = -4.05594164298
    printed_echo_fO2_log = -4.056
    payload = {
        "equilibrium_result": {
            "temperature_C": 1526.85,
            "pressure_bar": 1.0,
            "fO2_log": printed_echo_fO2_log,
            "requested_fO2_log": requested_fO2_log,
            "printed_fO2_echo_log": printed_echo_fO2_log,
            "liquid_fraction": 1.0,
            "status": "ok",
        },
        "last_vapor_pressures_source": {},
        "last_vapor_pressure_diagnostic": {},
    }
    store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "requested-fo2-replay.sqlite",
    )
    store.last_cache_state = "cached_exact"
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            fO2_log=-9.0,
            melt_fO2_log=-9.0,
        ),
    )

    replayed = store._equilibrium_from_payload(sim, payload)

    assert replayed.fO2_log == printed_echo_fO2_log
    assert replayed.requested_fO2_log == requested_fO2_log
    assert replayed.printed_fO2_echo_log == printed_echo_fO2_log
    assert sim.melt.fO2_log == requested_fO2_log
    assert sim.melt.melt_fO2_log == requested_fO2_log


def test_equilibrium_cache_hits_refresh_feo_liquidus_qualification(
    tmp_path: Path,
):
    from simulator.fe_redox import (
        LiquidFeODomain,
        calphad_ferrous_feo_activity_diagnostic,
    )
    from simulator.melt_backend.magemin import MAGEMinDomainIdentity
    from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

    db_path = tmp_path / 'equilibrium-feo-domain.sqlite'
    capture = PT0DeterminismStore('capture', db_path=db_path)
    capture_sim, result = _pt0_equilibrium_capture_context(capture)
    temperature_K = (
        float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    )
    identity = MAGEMinDomainIdentity(
        'in_band', 'not_applicable', None, None,
    )
    measured_domain = LiquidFeODomain(
        below_liquidus=True,
        temperature_K=temperature_K,
        liquidus_T_C=float(result.temperature_C) + 100.0,
        solidus_T_C=float(result.temperature_C) - 100.0,
        source='liquidus_solidus:alphamelts:test',
        domain_identity=identity,
    )
    domains = [measured_domain]
    capture_sim.liquid_feo_domain_at = lambda _temperature_K: domains[0]
    captured_activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=capture_sim.melt.composition_wt_pct(),
        fO2_log=float(capture_sim._compute_intrinsic_melt_fO2()),
        T_K=temperature_K,
        pressure_bar=float(result.pressure_bar),
        liquid_domain=measured_domain,
    )
    result.diagnostics = {'a_FeO_calphad': captured_activity}
    capture.capture_equilibrium(capture_sim, result)
    captured_key = capture._equilibrium_key(capture_sim)

    no_pin_domain = LiquidFeODomain(
        below_liquidus=None,
        temperature_K=temperature_K,
        liquidus_T_C=None,
        solidus_T_C=None,
        source='none:liquidus_gate_not_established',
        domain_identity=identity,
        liquidus_unknown=True,
    )
    domains[0] = no_pin_domain
    no_pin_hit = capture.cached_equilibrium(capture_sim)
    assert no_pin_hit is not None
    assert capture._equilibrium_key(capture_sim) == captured_key
    no_pin_activity = no_pin_hit.diagnostics['a_FeO_calphad']
    assert no_pin_activity['a_FeO_authoritative'] == (
        captured_activity['a_FeO_authoritative']
    )
    assert [notice['kind'] for notice in no_pin_activity['notices']] == [
        'liquidus_unknown',
    ]
    assert no_pin_activity['notices'][0]['source'] == (
        'none:liquidus_gate_not_established'
    )

    floor_domain = LiquidFeODomain(
        below_liquidus=None,
        temperature_K=temperature_K,
        liquidus_T_C=1200.0,
        solidus_T_C=1200.0,
        source='kress91_liquid_calibration_floor',
        domain_identity=identity,
        liquidus_unknown=True,
    )
    domains[0] = floor_domain
    floor_hit = capture.cached_equilibrium(capture_sim)
    assert floor_hit is not None
    assert capture._equilibrium_key(capture_sim) == captured_key
    floor_activity = floor_hit.diagnostics['a_FeO_calphad']
    assert floor_activity['a_FeO_authoritative'] == (
        captured_activity['a_FeO_authoritative']
    )
    assert [notice['kind'] for notice in floor_activity['notices']] == [
        'liquidus_unknown',
    ]
    assert floor_activity['notices'][0]['source'] == (
        'kress91_liquid_calibration_floor'
    )

    replay = PT0DeterminismStore('replay', db_path=db_path)
    replay_sim, _ = _pt0_equilibrium_capture_context(replay)
    replay_sim.liquid_feo_domain_at = lambda _temperature_K: floor_domain
    replayed = replay.replay_equilibrium(replay_sim)
    assert replay._equilibrium_key(replay_sim) == captured_key
    replayed_activity = replayed.diagnostics['a_FeO_calphad']
    assert replayed_activity['a_FeO_authoritative'] == (
        captured_activity['a_FeO_authoritative']
    )
    assert [notice['kind'] for notice in replayed_activity['notices']] == [
        'liquidus_unknown',
    ]
    assert replayed_activity['notices'][0]['source'] == (
        'kress91_liquid_calibration_floor'
    )


def test_equilibrium_capture_strips_read_time_feo_qualification(
    tmp_path: Path,
):
    from simulator.fe_redox import calphad_ferrous_feo_activity_diagnostic

    store = PT0DeterminismStore(
        'capture',
        db_path=tmp_path / 'equilibrium-feo-capture-domain.sqlite',
    )
    sim, result = _pt0_equilibrium_capture_context(store)
    sim._liquid_fraction_provider = 'alphamelts'
    temperature_K = float(result.temperature_C) + 273.15
    measured_curve = {
        'source': 'liquidus_solidus:alphamelts:test',
        'solidus_T_C': float(result.temperature_C) - 100.0,
        'liquidus_T_C': float(result.temperature_C) + 100.0,
        'path': (
            (float(result.temperature_C) - 100.0, 0.0),
            (float(result.temperature_C) + 100.0, 1.0),
        ),
        'is_liquidus_floor': False,
    }
    sim._melt_redox_gate_authority_this_tick = {
        'disposition': 'live',
        'curve': measured_curve,
    }
    sim._melt_redox_gate_authority_tick_hour = int(sim.melt.hour)
    sim._melt_redox_gate_authority_tick_verdict_identity = None

    def capture_with_current_domain():
        domain = sim.liquid_feo_domain_at(temperature_K)
        result.diagnostics = {
            'a_FeO_calphad': calphad_ferrous_feo_activity_diagnostic(
                comp_wt=sim.melt.composition_wt_pct(),
                fO2_log=float(sim._compute_intrinsic_melt_fO2()),
                T_K=temperature_K,
                pressure_bar=float(result.pressure_bar),
                liquid_domain=domain,
            ),
        }
        payload = rrd.equilibrium_payload(sim, result)
        store.capture_equilibrium(sim, result)
        return payload

    measured_payload = capture_with_current_domain()
    measured_notices = result.diagnostics['a_FeO_calphad']['notices']
    assert measured_notices
    for name in (
        '_liquid_feo_resolved_curve_hour',
        '_liquid_feo_resolved_curve',
        '_liquid_feo_resolved_curve_domain_identity',
    ):
        if hasattr(sim, name):
            delattr(sim, name)
    sim._melt_redox_gate_authority_tick_hour = None
    sim._melt_redox_gate_authority_this_tick = object()

    unknown_payload = capture_with_current_domain()
    unknown_notices = result.diagnostics['a_FeO_calphad']['notices']
    assert [notice['kind'] for notice in unknown_notices] == [
        'liquidus_unknown',
    ]
    assert rrd._sha256(rrd.canonical_json_bytes(measured_payload)) == (
        rrd._sha256(rrd.canonical_json_bytes(unknown_payload))
    )
    stored_diagnostic = unknown_payload['equilibrium_result']['diagnostics'][
        'a_FeO_calphad'
    ]
    assert 'notices' not in stored_diagnostic


def test_unset_alphamelts_fo2_fields_do_not_change_other_cache_payloads():
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        liquid_fraction=0.5,
    )
    sim = SimpleNamespace(
        _last_vapor_pressures_source={},
        _last_vapor_pressure_diagnostic={},
    )

    payload = rrd.equilibrium_payload(sim, result)

    assert "requested_fO2_log" not in payload["equilibrium_result"]
    assert "printed_fO2_echo_log" not in payload["equilibrium_result"]


def test_equilibrium_envelope_is_cache_inert_and_attached_on_legacy_replay(
    tmp_path: Path,
):
    legacy_result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        phase_assemblage_available=False,
        status="ok",
        diagnostics={"existing_diagnostic": "unchanged"},
    )
    instrumented_result = copy.deepcopy(legacy_result)
    expected_diagnostic = melt_extrapolation_diagnostic(
        1600.0 + 273.15,
        "MELTS-v1.0",
    )
    instrumented_result.diagnostics.update(expected_diagnostic)
    legacy_result.alphamelts_diagnostics = {"transport": "subprocess"}
    instrumented_result.alphamelts_diagnostics = {
        "transport": "subprocess",
        **expected_diagnostic,
    }
    legacy_sim = SimpleNamespace(
        _last_vapor_pressure_diagnostic={"backend_status": "ok"},
    )
    instrumented_sim = SimpleNamespace(
        _last_vapor_pressure_diagnostic={
            "backend_status": "ok",
            **expected_diagnostic,
        },
    )
    legacy_payload = rrd.equilibrium_payload(legacy_sim, legacy_result)
    instrumented_payload = rrd.equilibrium_payload(
        instrumented_sim,
        instrumented_result,
    )
    legacy_payload["authority"] = copy.deepcopy(_alphamelts_record_authority())
    instrumented_payload["authority"] = copy.deepcopy(
        _alphamelts_record_authority()
    )

    assert canonical_json_bytes(instrumented_payload) == canonical_json_bytes(
        legacy_payload
    )

    unchanged_key = {
        "namespace_id": "alphamelts:equilibrium-composite",
        "composition_mol_fraction": [["SiO2", 1.0]],
        "controls": {"T_K": 1873.15, "pressure_bar": 1.0e-6},
        "model": {},
        "vapor_pressure_provider_selection": {},
    }
    store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "h2-cache-inert.sqlite",
    )
    store._store("equilibrium_post_record", unchanged_key, legacy_payload)
    store._store("equilibrium_post_record", unchanged_key, instrumented_payload)

    replayed = rrd.equilibrium_from_payload(legacy_payload)
    assert {
        field: replayed.diagnostics[field]
        for field in expected_diagnostic
    } == expected_diagnostic
    assert {
        field: replayed.alphamelts_diagnostics[field]
        for field in expected_diagnostic
    } == expected_diagnostic

    partial_payload = copy.deepcopy(legacy_payload)
    partial_payload["alphamelts_diagnostics"] = {
        "melt_model_id": "MELTS-v1.0"
    }
    with pytest.raises(MeltEnvelopeValidationError, match="partial H2"):
        rrd.equilibrium_from_payload(partial_payload)


@pytest.mark.parametrize(
    "carrier",
    (
        "equilibrium_result.diagnostics",
        "last_vapor_pressure_diagnostic",
        "alphamelts_diagnostics",
    ),
)
def test_marker_only_melt_envelope_is_rejected_on_every_cache_carrier(
    carrier: str,
):
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        phase_assemblage_available=False,
        status="ok",
        diagnostics={"existing_diagnostic": "unchanged"},
    )
    result.alphamelts_diagnostics = {"transport": "subprocess"}
    sim = SimpleNamespace(
        _last_vapor_pressure_diagnostic={"backend_status": "ok"},
    )
    marker = {"instrument_status": "status_bearing_non_authoritative"}
    if carrier == "equilibrium_result.diagnostics":
        result.diagnostics.update(marker)
    elif carrier == "last_vapor_pressure_diagnostic":
        sim._last_vapor_pressure_diagnostic.update(marker)
    else:
        result.alphamelts_diagnostics.update(marker)

    with pytest.raises(MeltEnvelopeValidationError, match="partial H2"):
        rrd.equilibrium_payload(sim, result)


@pytest.mark.parametrize(
    "carrier",
    (
        "equilibrium_result.diagnostics",
        "last_vapor_pressure_diagnostic",
        "alphamelts_diagnostics",
    ),
)
def test_marker_only_melt_envelope_is_rejected_before_replay_reconstruction(
    carrier: str,
):
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        phase_assemblage_available=False,
        status="ok",
        diagnostics={"existing_diagnostic": "unchanged"},
    )
    sim = SimpleNamespace(
        _last_vapor_pressure_diagnostic={"backend_status": "ok"},
    )
    payload = rrd.equilibrium_payload(sim, result)
    payload["authority"] = copy.deepcopy(_alphamelts_record_authority())
    marker = {"instrument_status": "status_bearing_non_authoritative"}
    if carrier == "equilibrium_result.diagnostics":
        payload["equilibrium_result"]["diagnostics"] = marker
        replay = lambda: rrd.equilibrium_from_payload(payload)
    elif carrier == "last_vapor_pressure_diagnostic":
        payload["last_vapor_pressure_diagnostic"] = marker
        store = PT0DeterminismStore("capture")
        replay_sim = SimpleNamespace()
        replay = lambda: store._equilibrium_from_payload(replay_sim, payload)
    else:
        payload["alphamelts_diagnostics"] = marker
        replay = lambda: rrd.equilibrium_from_payload(payload)

    with pytest.raises(MeltEnvelopeValidationError, match="partial H2"):
        replay()


def test_strict_pt1_put_rejects_builtin_fallback_vapor_source(
    tmp_path: Path,
) -> None:
    key = _strict_vapor_key()
    payload = {
        "authority": _alphamelts_record_authority(),
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
        "equilibrium_post_record",
        key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(key),
    )
    assert payload is not None
    return payload, store


def test_pt1_physics_ladder_reemits_source_notices_and_approximation_notice(
    tmp_path: Path,
) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    source_key = _c3a_ladder_key(
        "source",
        feo_fraction=0.123444,
        temperature_K=1234.44,
    )
    db_path = tmp_path / "ladder-repair-notices.sqlite"
    _put_c3a_payload(db_path, source_key, "ladder-source")
    notice = {"kind": "ladder_repair", "field": "SiO2"}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET repair_notices_json = ?",
            (canonical_json_bytes([notice]).decode("utf-8"),),
        )

    replay_sim = SimpleNamespace()
    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        "equilibrium_post_record",
        query_key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query_key),
        sim=replay_sim,
    )

    assert payload is not None
    assert replay_sim._reduced_real_repair_notices == [
        notice,
        {
            "kind": "reduced_real_cache_approximation",
            "cache_state": "cached_physics_bucket",
        },
    ]


def _real_magemin_available() -> bool:
    backend = MAGEMinBackend()
    backend.initialize({"python_bridge": "subprocess", "warm_worker": False})
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

    assert key["composition_mol_fraction"]
    assert set(key["controls"]) == {
        "T_K", "log_fO2", "pressure_bar", "pO2_bar", "vapor_transport_pO2_bar"
    }
    assert "provider_selection" not in key
    assert "vapor_pressure_provider_selection" in key
    assert "inventory" in key["sulfur_side"]
    assert "sulfsat_package_version" not in key["sulfur_side"]
    assert "sulfsat_calibration_version" not in key["sulfur_side"]
    assert "code_version" not in key
    assert "corpus_version" not in key
    assert "engine_version" not in key
    assert "source_module_digest" not in key
    assert "data_digests" not in key


def test_pt0_gate_replay_separates_buffers_at_same_scalar_fO2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.engine_binding_admission import write_gate_curve_memo_record

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    scalar_fO2_log = -9.0
    state_a, state_b = _freeze_gate_redox_state_pair(
        distinct_buffers=True,
        monkeypatch=monkeypatch,
    )
    assert state_a.log_fO2_at(REDOX_STATE_REFERENCE_T_K) == pytest.approx(
        scalar_fO2_log
    )
    assert state_b.log_fO2_at(REDOX_STATE_REFERENCE_T_K) == pytest.approx(
        scalar_fO2_log
    )

    db_path = tmp_path / "pt1-redox-state.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture)
    capture_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(capture_sim)
    capture_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = scalar_fO2_log
    capture_sim.melt.oxygen_reservoir.reference_T_K = (
        REDOX_STATE_REFERENCE_T_K
    )
    capture_sim._sync_oxygen_reservoir_mirror()
    curve = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    record = write_gate_curve_memo_record(
        capture_sim,
        curve,
        redox_state=state_a,
        provider_outcome=rrd.gate_curve_provider_outcome_for_status("ok"),
    )
    local_key = capture_sim._freeze_gate_cache_key(
        pressure_bar=float(capture_sim.melt.p_total_mbar) / 1000.0,
        fO2_log=capture_sim._freeze_gate_redox_key_fO2_log(
            fO2_log=scalar_fO2_log,
        ),
        redox_state=state_a,
    )
    record["law_disclosures"] = []
    capture_sim._freeze_gate_liquid_fraction_cache = {
        **record,
        "key": local_key,
    }
    capture.capture_gate_curve(
        capture_sim,
        fO2_log=scalar_fO2_log,
        curve=curve,
        redox_state=state_a,
    )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)
    replay_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = scalar_fO2_log
    replay_sim.melt.oxygen_reservoir.reference_T_K = (
        REDOX_STATE_REFERENCE_T_K
    )
    replay_sim._sync_oxygen_reservoir_mirror()

    same_state_curve = replay.replay_gate_curve(
        replay_sim,
        fO2_log=scalar_fO2_log,
        redox_state=state_a,
    )
    assert same_state_curve["source"] == curve["source"]
    assert same_state_curve["solidus_T_C"] == curve["solidus_T_C"]
    assert same_state_curve["liquidus_T_C"] == curve["liquidus_T_C"]

    with pytest.raises(PT0CacheMiss):
        replay.replay_gate_curve(
            replay_sim,
            fO2_log=scalar_fO2_log,
            redox_state=state_b,
        )


def test_physics_bucket_replay_scope_keeps_nonabsolute_redox_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scalar_fO2_log = -9.0
    state_a, state_b = _freeze_gate_redox_state_pair(
        distinct_buffers=True,
        monkeypatch=monkeypatch,
    )
    sim = _build_pt0_sim(None)
    keys = [
        canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=scalar_fO2_log,
            fe_redox_policy="intrinsic",
            redox_state=state,
        )
        for state in (state_a, state_b)
    ]
    buckets = [
        canonical_physics_bucket_key_from_replay_key(key) for key in keys
    ]

    assert buckets[0]["physics_bucket"] == buckets[1]["physics_bucket"]
    assert buckets[0]["replay_scope"]["redox_state_identity"] == (
        state_a.cache_identity()
    )
    assert buckets[1]["replay_scope"]["redox_state_identity"] == (
        state_b.cache_identity()
    )
    assert buckets[0]["replay_scope"] != buckets[1]["replay_scope"]


def test_pt0_gate_curve_key_is_tstd_aligned_across_isochemical_ramp(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-gate-tstd.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    seed_gate_curve_memo_record(
        sim,
        {"source": "test", "solidus_T_C": 1000.0, "liquidus_T_C": 1300.0},
        key=("test",),
    )
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

    seed_gate_curve_memo_record(sim, curve)
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


def test_pt0_production_gate_curve_cold_replays_across_isochemical_ramp(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-production-gate-ramp.db"
    curve = {
        "source": "unit-test",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture)
    capture_sim.start_campaign(CampaignPhase.C2A_STAGED)
    capture_sim.melt.temperature_C = 1450.0
    capture_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    capture_sim.melt.oxygen_reservoir.reference_T_K = 1723.15
    capture_sim._sync_oxygen_reservoir_mirror()
    capture_redox_key_fO2_log = capture_sim._freeze_gate_redox_key_fO2_log()
    capture_local_key = capture_sim._freeze_gate_cache_key(
        pressure_bar=capture_sim.melt.p_total_mbar / 1000.0,
        fO2_log=capture_redox_key_fO2_log,
    )
    seed_gate_curve_memo_record(capture_sim, curve, key=capture_local_key)

    assert capture_sim._freeze_gate_curve() == curve
    capture_key = capture.capture_sequence[-1]["key"]
    capture_controls = capture_key["controls"]
    assert capture_controls["T_K"] == pytest.approx(298.15)
    assert capture_redox_key_fO2_log == pytest.approx(
        capture_controls["log_fO2"],
        abs=ControlQuantization.PRODUCTION.log_fo2_quantum / 2.0,
        rel=0.0,
    )
    assert capture_local_key[2] == round(capture_redox_key_fO2_log)

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replay_sim.melt.temperature_C = 1450.0
    replay_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    replay_sim.melt.oxygen_reservoir.reference_T_K = 1723.15
    replay_sim._sync_oxygen_reservoir_mirror()
    assert replay_sim._freeze_gate_curve() == curve
    replay_controls = replay.replay_sequence[-1]["key"]["controls"]
    assert replay_controls == capture_controls

    replay_sim.melt.temperature_C = 1600.0
    replay_sim._re_reference_melt_fO2_to_temperature(1873.15)
    replay_redox_key_fO2_log = replay_sim._freeze_gate_redox_key_fO2_log()
    replay_local_key = replay_sim._freeze_gate_cache_key(
        pressure_bar=replay_sim.melt.p_total_mbar / 1000.0,
        fO2_log=replay_redox_key_fO2_log,
    )
    replay_sim._freeze_gate_liquid_fraction_cache = None
    replay_sim._freeze_gate_liquid_fraction_curve_memo = {}

    assert replay_sim._melt_redox_liquidus_gate_curve() == curve
    replay_key = replay.replay_sequence[-1]["key"]
    replay_controls = replay_key["controls"]
    assert replay_controls == capture_controls
    assert replay_redox_key_fO2_log == pytest.approx(
        replay_controls["log_fO2"],
        abs=ControlQuantization.PRODUCTION.log_fo2_quantum / 2.0,
        rel=0.0,
    )
    assert replay_local_key == capture_local_key
    assert replay_local_key[2] == round(replay_redox_key_fO2_log)


def test_pt0_gate_curve_rekeys_from_live_fo2_after_first_liquid_seed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pt1-preseed-gate-rekey.db"
    curve = {
        "source": "unit-test",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture)
    capture_sim.start_campaign(CampaignPhase.C2A_STAGED)
    capture_sim.melt.temperature_C = 1450.0
    capture_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    capture_sim.melt.oxygen_reservoir.reference_T_K = None
    capture_sim._sync_oxygen_reservoir_mirror()
    preseed_redox_key_fO2_log = capture_sim._freeze_gate_redox_key_fO2_log()
    preseed_local_key = capture_sim._freeze_gate_cache_key(
        pressure_bar=capture_sim.melt.p_total_mbar / 1000.0,
        fO2_log=preseed_redox_key_fO2_log,
    )
    seed_gate_curve_memo_record(capture_sim, curve, key=preseed_local_key)

    assert capture_sim._freeze_gate_curve() == curve
    preseed_controls = capture.capture_sequence[-1]["key"]["controls"]
    assert preseed_controls["log_fO2"] == pytest.approx(-9.0)
    assert preseed_controls["T_K"] == pytest.approx(298.15)
    assert preseed_redox_key_fO2_log == pytest.approx(
        preseed_controls["log_fO2"],
        abs=ControlQuantization.PRODUCTION.log_fo2_quantum / 2.0,
        rel=0.0,
    )
    assert preseed_local_key[2] == round(preseed_redox_key_fO2_log)

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replay_sim.melt.temperature_C = 1450.0
    replay_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    replay_sim.melt.oxygen_reservoir.reference_T_K = None
    replay_sim._sync_oxygen_reservoir_mirror()
    assert replay_sim._freeze_gate_curve() == curve
    assert replay.replay_sequence[-1]["key"]["controls"] == preseed_controls

    replay_sim._re_reference_melt_fO2_to_temperature(1723.15)
    postseed_redox_key_fO2_log = replay_sim._freeze_gate_redox_key_fO2_log()
    postseed_local_key = replay_sim._freeze_gate_cache_key(
        pressure_bar=replay_sim.melt.p_total_mbar / 1000.0,
        fO2_log=postseed_redox_key_fO2_log,
    )
    replay_sim._freeze_gate_liquid_fraction_cache = None
    replay_sim._freeze_gate_liquid_fraction_curve_memo = {}

    with pytest.raises(PT0CacheMiss):
        replay_sim._melt_redox_liquidus_gate_curve()

    postseed_controls = replay.replay_sequence[-1]["key"]["controls"]
    expected_postseed_fO2 = kress91_referenced_log_fO2(
        -9.0,
        reference_T_K=1723.15,
        target_T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
    )
    assert postseed_controls["log_fO2"] == pytest.approx(
        expected_postseed_fO2,
        abs=ControlQuantization.PRODUCTION.log_fo2_quantum / 2.0,
        rel=0.0,
    )
    assert postseed_redox_key_fO2_log == pytest.approx(
        postseed_controls["log_fO2"],
        abs=ControlQuantization.PRODUCTION.log_fo2_quantum / 2.0,
        rel=0.0,
    )
    assert postseed_local_key[2] == round(postseed_redox_key_fO2_log)
    assert postseed_controls["log_fO2"] != preseed_controls["log_fO2"]


def test_pt2_equilibrium_provider_id_is_namespace_metadata_only() -> None:
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

    assert first["namespace_id"] == "alphamelts:equilibrium-composite"
    assert "provider_selection" not in first
    assert _key_hash(different_provider) == _key_hash(first)
    assert _key_hash(different_version) == _key_hash(first)


def test_pt2_physics_bucket_ignores_static_data_fingerprints() -> None:
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
    assert _key_hash(setpoints_key) == _key_hash(baseline_key)
    assert _physics_bucket_hash(setpoints_key) == baseline_hash
    assert _key_hash(feedstocks_key) == _key_hash(baseline_key)
    assert _physics_bucket_hash(feedstocks_key) == baseline_hash


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

    assert _physics_bucket_hash(composition_changed) != baseline
    assert _physics_bucket_hash(temperature_changed) != baseline
    assert _physics_bucket_hash(pressure_changed) != baseline
    assert _physics_bucket_hash(redox_changed) != baseline
    assert _physics_bucket_hash(po2_changed) != baseline
    assert _physics_bucket_hash(solver_version_changed) == baseline


def test_pt2_physics_bucket_key_ignores_schema_version(monkeypatch) -> None:
    key = _c3a_ladder_key(
        "schema-version",
        feo_fraction=0.123456,
        temperature_K=1234.5678,
    )
    monkeypatch.setattr(rrd, "PHYSICS_BUCKET_SCHEMA_VERSION", "test-bucket-v1")
    first = canonical_physics_bucket_key_from_replay_key(key)
    first_ladder = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        key, "h40"
    )

    monkeypatch.setattr(rrd, "PHYSICS_BUCKET_SCHEMA_VERSION", "test-bucket-v2")
    second = canonical_physics_bucket_key_from_replay_key(key)
    second_ladder = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        key, "h40"
    )

    assert first == second
    assert first_ladder == second_ladder


def test_pt2_physics_bucket_partitions_stage0_inventory_values() -> None:
    key = _silicate_equilibrium_key(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic-cache-c1",
            engine_version="alpha-v1",
        )
    )
    sulfur_key = copy.deepcopy(key)
    sulfur_key["sulfur_side"]["S_input_ppm"] = 1000.0
    sulfur_key["sulfur_side"]["inventory"] = {"salt_phase": {"CaSO4": 1.0}}
    bucket = canonical_physics_bucket_key_from_replay_key(sulfur_key)

    assert bucket["physics_bucket"]["sulfur"]["S_input_ppm"] == 1000.0
    assert (
        bucket["physics_bucket"]["sulfur"]["inventory"]
        == {"salt_phase": {"CaSO4": 1.0}}
    )

    history_changed = copy.deepcopy(sulfur_key)
    history_changed["sulfur_side"]["inventory"] = {"salt_phase": {"CaSO4": 2.0}}
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
        "equilibrium_post_record",
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
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        def get_engine_version(self) -> str:
            return "non-stub-test"

    db_path = tmp_path / "pt1.sqlite"
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic",
        engine_version="alpha-v1",
    )
    capture_store = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture_store)
    capture_sim.backend = AlphaMELTSBackend()
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
    replay_sim.backend = AlphaMELTSBackend()
    replay_sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    # 2026-08-05 MC-1 trace wiring d1b4f5d adds real lunar S inventory, making
    # SulfSat availability part of the physical bucket. Split only the exact
    # replay key with a vapor-transport control that equilibrium does not consume.
    replay_sim._vapor_pressure_dispatch_pO2_bar = lambda: 1.0e-8

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


def test_pt0_exact_route_refuses_dependency_vector_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    source_key = capture.capture_sequence[-1]["key"]
    source_entry = capture.entries[_key_hash(source_key)]
    query_key = copy.deepcopy(source_key)
    query_key["model"]["model"] = "different-admitted-model"
    monkeypatch.setattr(
        capture,
        "_entry_for_key",
        lambda *_args, **_kwargs: (source_entry, None),
    )

    with pytest.raises(PT0CacheMiss):
        capture._lookup("equilibrium_post_record", query_key)

    assert capture.hits == 0


def test_pt0_approximate_route_refuses_dependency_vector_mismatch() -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    source_key = capture.capture_sequence[-1]["key"]
    query_key = copy.deepcopy(source_key)
    query_key["model"]["model"] = "different-admitted-model"
    query_bucket = canonical_physics_bucket_key_from_replay_key(query_key)
    source_hash = _key_hash(source_key)
    query_bucket_hash = _key_hash(query_bucket)
    capture.physics_bucket_entries[query_bucket_hash] = source_hash
    capture.cache_tier_ceiling = "cached_physics_bucket"

    payload = capture._lookup_optional(
        "equilibrium_post_record",
        query_key,
        physics_bucket_key=query_bucket,
    )

    assert payload is None
    assert capture.hits == 0


@pytest.mark.parametrize(
    "rung_tag",
    ("h40", "h30", "h40c", "h30c"),
)
def test_pt0_physics_ladder_route_refuses_dependency_vector_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    rung_tag: str,
) -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    source_key = capture.capture_sequence[-1]["key"]
    source_hash = _key_hash(source_key)
    source_entry = capture._entries[source_hash]
    query_key = copy.deepcopy(source_key)
    query_key["model"]["model"] = "different-admitted-model"
    query_bucket = canonical_physics_bucket_key_from_replay_key(query_key)
    capture.cache_tier_ceiling = "cached_physics_bucket"
    monkeypatch.setattr(
        capture,
        "_entry_for_physics_bucket",
        lambda *_args, **_kwargs: None,
    )

    decoded_payload = rrd._decode_payload_for_admission(
        "equilibrium_post_record",
        source_entry["payload"],
        payload_codec_version=source_entry["payload_codec_version"],
        keys=(source_entry["key"], query_key),
    )

    def ladder_entry(_artifact, candidate_rung, *_args, **_kwargs):
        return (
            (source_entry, decoded_payload)
            if candidate_rung == rung_tag
            else None
        )

    monkeypatch.setattr(
        capture,
        "_entry_for_physics_ladder_bucket",
        ladder_entry,
    )

    payload = capture._lookup_optional(
        "equilibrium_post_record",
        query_key,
        physics_bucket_key=query_bucket,
    )

    assert payload is None
    assert capture.hits == 0


def test_pt0_capture_refuses_dependency_vector_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    original_key = capture._equilibrium_key

    def mismatched_key(current_sim):
        key = original_key(current_sim)
        key["model"]["model"] = "different-admitted-model"
        return key

    monkeypatch.setattr(capture, "_equilibrium_key", mismatched_key)
    capture.capture_equilibrium(sim, result)

    assert capture.capture_sequence == []
    assert capture._entries == {}


def test_control_quantization_coarse_store_fine_lookup_uses_ladder(
    tmp_path: Path,
) -> None:
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        def get_engine_version(self) -> str:
            return "non-stub-test"

    db_path = tmp_path / "control-quantization.sqlite"
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic",
        engine_version="alpha-v1",
    )
    capture_store = PT0DeterminismStore(
        "capture",
        db_path=db_path,
        control_quantization=ControlQuantization.from_name("coarse"),
    )
    capture_sim = _build_pt0_sim(capture_store)
    capture_sim.backend = AlphaMELTSBackend()
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
    replay_sim.backend = AlphaMELTSBackend()
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
RECIPE_SCHEMA_MODULE = "simulator/recipe.py"


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
        "simulator/recipe.py",
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


def test_gate_replay_uses_evaluated_silica_verdict_for_flag_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    db_path = tmp_path / "evaluated-silica-gate-replay.db"
    composition_wt_pct = {
        "SiO2": 29.999,
        "MgO": 60.001,
        "Fe2O3": 10.0,
    }
    curve_payload = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
        "status": "ok",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1300.0,
        "path": ((1000.0, 0.0), (1300.0, 1.0)),
    }

    def configure_sim(store):
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim._liquid_fraction_provider = "magemin"
        register_synthetic_gate_curve_producer(sim)
        composition_mol = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            composition_mol,
            source="d-447 evaluated-bulk replay regression",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim._chem_kernel = sim._build_chemistry_kernel()
        sim.inventory.inert_melt_components_kg.clear()
        sim._project_cleaned_melt_from_atom_ledger()
        return sim

    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = configure_sim(capture)
    capture_fO2_log = capture_sim._compute_intrinsic_melt_fO2()
    key, _reason = capture_sim._freeze_gate_cache_key_and_domain(
        pressure_bar=float(capture_sim.melt.p_total_mbar) / 1000.0,
        fO2_log=capture_sim._freeze_gate_redox_key_fO2_log(
            fO2_log=capture_fO2_log,
        ),
    )
    assert getattr(key, "magemin_domain_identity", None) is None
    seed_gate_curve_memo_record(capture_sim, curve_payload)
    capture.capture_gate_curve(
        capture_sim,
        fO2_log=capture_fO2_log,
        curve=curve_payload,
    )
    assert capture.last_cache_state == "live_fill"

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = configure_sim(replay)
    import simulator.melt_backend.magemin as magemin_module

    def unexpected_second_silica_projection(*_args, **_kwargs):
        pytest.fail("replay must consume the identity built for its cache key")

    monkeypatch.setattr(
        magemin_module,
        "evaluated_silica_verdict",
        unexpected_second_silica_projection,
    )
    original_domain_key_builder = replay_sim._freeze_gate_cache_key_and_domain
    domain_key_calls = []

    def count_domain_key_builds(**kwargs):
        domain_key_calls.append(kwargs)
        return original_domain_key_builder(**kwargs)

    monkeypatch.setattr(
        replay_sim,
        "_freeze_gate_cache_key_and_domain",
        count_domain_key_builds,
    )
    replayed = replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    )
    assert replayed["source"] == curve_payload["source"]
    assert replayed["path"] == ((1000.0, 0.0), (1300.0, 1.0))
    assert replay.last_cache_state == "cached_exact"
    assert len(domain_key_calls) == 1


def test_retained_replayed_gate_curve_redispatches_and_flags_changed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger
    from simulator.physical_constants import MBAR_PER_BAR

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    db_path = tmp_path / "retained-domain-identity.db"
    composition_wt_pct = {
        "SiO2": 79.999,
        "MgO": 10.001,
        "FeO": 10.0,
    }
    curve_payload = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
        "status": "ok",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1300.0,
        "path": ((1000.0, 0.0), (1300.0, 1.0)),
    }

    def configure_sim(store):
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim._liquid_fraction_provider = "magemin"
        register_synthetic_gate_curve_producer(sim)
        composition_mol = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            composition_mol,
            source="r23 retained replay curve identity regression",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim._chem_kernel = sim._build_chemistry_kernel()
        sim.inventory.inert_melt_components_kg.clear()
        sim._project_cleaned_melt_from_atom_ledger()
        return sim

    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = configure_sim(capture)
    fO2_log = capture_sim._compute_intrinsic_melt_fO2()
    capture_sim._freeze_gate_cache_key_and_domain(
        pressure_bar=float(capture_sim.melt.p_total_mbar) / MBAR_PER_BAR,
        fO2_log=capture_sim._freeze_gate_redox_key_fO2_log(
            fO2_log=fO2_log,
        ),
    )
    seed_gate_curve_memo_record(capture_sim, curve_payload)
    capture.capture_gate_curve(
        capture_sim,
        fO2_log=fO2_log,
        curve=curve_payload,
    )
    assert capture.last_cache_state == "live_fill"

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = configure_sim(replay)
    retained_curve = (
        replay_sim._establish_melt_redox_gate_authority_for_current_hour()
    )
    retained_identity = replay_sim._freeze_gate_current_magemin_domain_identity
    assert retained_identity.silica_verdict == "in_band"
    assert replay_sim._liquid_feo_resolved_curve == retained_curve
    assert replay_sim._liquid_feo_resolved_curve_domain_identity.matches(
        retained_identity
    )

    composition_wt_pct.update({"SiO2": 80.001, "MgO": 9.999})
    composition_mol = {
        species: amount
        / resolve_species_formula(
            species,
            replay_sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
        for species, amount in composition_wt_pct.items()
    }
    ledger = AtomLedger(registry=replay_sim.atom_ledger.registry)
    ledger.load_external_mol(
        "process.cleaned_melt",
        composition_mol,
        source="r23 post-char replay curve identity regression",
        material_origin="feedstock",
    )
    replay_sim.atom_ledger = ledger
    replay_sim._chem_kernel = replay_sim._build_chemistry_kernel()
    replay_sim._project_cleaned_melt_from_atom_ledger()
    _key, domain_reason = replay_sim._freeze_gate_cache_key_and_domain(
        pressure_bar=float(replay_sim.melt.p_total_mbar) / MBAR_PER_BAR,
        fO2_log=replay_sim._freeze_gate_redox_key_fO2_log(
            fO2_log=float(replay_sim._current_melt_redox_fO2_log()),
        ),
    )
    assert domain_reason is None
    assert (
        replay_sim._freeze_gate_current_magemin_domain_identity.silica_verdict
        == "extrapolated"
    )
    activity = replay_sim._native_fe_saturation_state(
        composition_wt_pct,
        fe3_over_sigma_fe=0.1,
        T_K=1800.0 + 273.15,
        pressure_bar=float(replay_sim.melt.p_total_mbar) / MBAR_PER_BAR,
        fO2_log=float(replay_sim._current_melt_redox_fO2_log()),
    )
    identity_notice = next(
        notice
        for notice in activity["notices"]
        if notice.get("kind") == "magemin_domain_identity"
    )
    assert identity_notice["silica_verdict"] == "extrapolated"
    assert identity_notice["certified_band"] == {
        "sio2_wt_pct": [30.0, 80.0],
    }


def test_pt1_persistent_store_round_trips_exact_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    db_path = tmp_path / "pt1-reduced-real.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(sim)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    curve = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
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
        provider_role="fallback",
    )

    expected_vapor_authority = {
        "resolved_provider_id": "builtin-vapor-pressure",
        "resolved_role": "authoritative",
        "authoritative_provider_id": "builtin-vapor-pressure",
        "fallback_provider_id": None,
        "fallback_allowed": True,
        "model": "builtin-vapor-pressure",
        "mode": "BuiltinVaporPressureProvider",
    }
    assert rrd._provider_identity(
        sim,
        ChemistryIntent.VAPOR_PRESSURE,
    ) == expected_vapor_authority
    # This independent payload pin uses the literal curve and resolver
    # identity before capture. Literal bytes and digest prevent serializer
    # changes from moving both sides together.
    expected_payload = {
        "curve": {
            "source": "gate_liquid_fraction:fallback:magemin-shadow",
            "solidus_T_C": 1210.0,
            "liquidus_T_C": 1320.0,
            "path": [
                {"temperature_C": 1210.0, "liquid_fraction": 0.0},
                {"temperature_C": 1320.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
        "authority": {
            "vapor_pressure": expected_vapor_authority,
        },
    }
    expected_bytes = (
        b'{"authority":{"vapor_pressure":{"authoritative_provider_id":"builtin-vapor-pressure",'
        b'"fallback_allowed":true,"fallback_provider_id":null,"mode":"BuiltinVaporPressureProvider",'
        b'"model":"builtin-vapor-pressure","resolved_provider_id":"builtin-vapor-pressure",'
        b'"resolved_role":"authoritative"}},"curve":{"is_liquidus_floor":false,'
        b'"liquidus_T_C":1320.0,"path":[{"liquid_fraction":0.0,"temperature_C":1210.0},'
        b'{"liquid_fraction":1.0,"temperature_C":1320.0}],"solidus_T_C":1210.0,'
        b'"source":"gate_liquid_fraction:fallback:magemin-shadow"}}'
    )
    expected_digest = "18caad6ce53865029b5ee185cc5550b163d8c423f0d5b01929f675f51e8edea7"
    assert json.loads(expected_bytes) == expected_payload

    runtime_record = seed_gate_curve_memo_record(sim, curve)
    assert set(runtime_record) == {
        "curve",
        "origin",
        "repair_notices",
        "disposition",
        "provider_outcome",
        # Measured empty. The fixture records that no flagged law ran.
        # The serialized PT1 payload separately carries vapor authority.
        "law_disclosures",
    }
    assert runtime_record["provider_outcome"] is rrd.GateCurveProviderOutcome.PRODUCED
    assert set(runtime_record["origin"]) == {
        "producer_pid",
        "producer_configuration",
        "producer_evidence",
    }
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
            SELECT artifact, key_hash, payload_sha256, payload_bytes
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
    # Gate payload bytes intentionally include authority.vapor_pressure.
    assert bytes(row[3]) == expected_bytes
    assert row[2] == expected_digest
    payload = json.loads(bytes(row[3]))
    assert payload == expected_payload
    assert b"producer_pid" not in row[3]
    assert b"producer_configuration" not in row[3]
    assert b"producer_evidence" not in row[3]
    assert set(payload["curve"]) == {
        "is_liquidus_floor",
        "liquidus_T_C",
        "path",
        "solidus_T_C",
        "source",
    }
    assert payload["curve"]["source"] == (
        "gate_liquid_fraction:fallback:magemin-shadow"
    )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)
    replay_fO2_log = replay_sim._compute_intrinsic_melt_fO2()
    replay_key = canonical_replay_key(
        replay_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=replay_fO2_log,
        fe_redox_policy="intrinsic",
        provider_role="fallback",
    )

    assert replay_key == key
    assert replay.replay_gate_curve(replay_sim, fO2_log=replay_fO2_log) == curve
    assert replay.summary()["hits"] == 1
    assert replay.summary()["misses"] == 0
    assert replay.replay_sequence[-1]["cache_state"] == "cached_exact"


def test_pt1_repair_notices_are_outside_payload_and_reemit_on_exact_replay(
    tmp_path: Path,
) -> None:
    curve = {
        "source": "unit-test:repair-notice",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1600.0,
        "path": ((1100.0, 0.0), (1600.0, 1.0)),
    }
    notices = [
        {
            "type": "typed_notice",
            "kind": "freeze_gate_curve_point_repaired",
            "site": "SR18",
            "temperature_C": 1200.0,
            "raw_fraction": -0.2,
            "action": "clamp_fraction",
        },
        {
            "type": "typed_notice",
            "kind": "freeze_gate_curve_source_replaced",
            "site": "SR19",
            "failed_source": "gate_liquid_fraction",
            "replacement_source": "liquidus_solidus:backend",
        },
    ]
    rows = []
    db_paths = (tmp_path / "without-notice.db", tmp_path / "with-notice.db")
    for db_path, captured_notices in zip(db_paths, ([], notices), strict=True):
        capture = PT0DeterminismStore("capture", db_path=db_path)
        sim = _build_pt0_sim(capture)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        seed_gate_curve_memo_record(
            sim,
            curve,
            repair_notices=captured_notices,
        )
        capture.capture_gate_curve(
            sim,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
            curve=curve,
            repair_notices=captured_notices,
        )
        with sqlite3.connect(db_path) as conn:
            rows.append(
                conn.execute(
                    f"""
                    SELECT payload_sha256, payload_bytes, repair_notices_json
                    FROM {PT1_EQUILIBRIUM_TABLE}
                    """
                ).fetchone()
            )

    assert rows[0][:2] == rows[1][:2]
    assert rows[0][2] == "[]"
    assert rows[1][2] == canonical_json_bytes(notices).decode("utf-8")

    replay = PT0DeterminismStore("replay", db_path=db_paths[1])
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == curve
    assert replay_sim._reduced_real_repair_notices == notices
    assert replay_sim._last_reduced_real_replay_notices == notices


def test_pt1_legacy_null_repair_notices_emit_typed_unavailable_notice(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-repair-notices.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = sim._compute_intrinsic_melt_fO2()
    curve = {
        "source": "unit-test:legacy-repair-notice",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1600.0,
        "path": ((1100.0, 0.0), (1600.0, 1.0)),
    }
    seed_gate_curve_memo_record(sim, curve)
    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=curve, repair_notices=[])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET repair_notices_json = NULL"
        )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    )

    assert replay_sim._reduced_real_repair_notices == [
        {"kind": "repair_notices_unavailable_for_legacy_row"}
    ]


def test_pt1_attached_legacy_table_without_notice_column_reads_as_null(
    tmp_path: Path,
) -> None:
    base_db = tmp_path / "attached-legacy.db"
    local_db = tmp_path / "local-replay.db"
    capture = PT0DeterminismStore("capture", db_path=base_db)
    capture_sim = _build_pt0_sim(capture)
    capture_sim.start_campaign(CampaignPhase.C2A_STAGED)
    fO2_log = capture_sim._compute_intrinsic_melt_fO2()
    curve = {
        "source": "unit-test:attached-legacy",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1600.0,
        "path": ((1100.0, 0.0), (1600.0, 1.0)),
    }
    seed_gate_curve_memo_record(capture_sim, curve)
    capture.capture_gate_curve(
        capture_sim,
        fO2_log=fO2_log,
        curve=curve,
        repair_notices=[],
    )
    with sqlite3.connect(base_db) as conn:
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
            "DROP COLUMN repair_notices_json"
        )
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET store_schema_version = 'pt1-reduced-real-equilibrium-store-v2'"
        )
        conn.execute(
            f"UPDATE {rrd.PT1_METADATA_TABLE} "
            "SET value = 'pt1-reduced-real-equilibrium-store-v2' "
            "WHERE key = 'store_schema_version'"
        )

    replay = PT0DeterminismStore(
        "replay",
        db_path=local_db,
        read_only_base_db_path=base_db,
    )
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == curve
    assert replay_sim._reduced_real_repair_notices == [
        {"kind": "repair_notices_unavailable_for_legacy_row"}
    ]


def test_pt1_exact_lookup_skips_stale_local_row_for_current_attached_base(
    tmp_path: Path,
) -> None:
    base_db = tmp_path / "current-base.db"
    local_db = tmp_path / "stale-local.db"
    _base_store, _base_sim, _base_fO2, base_curve = _capture_pt0_gate_curve(
        base_db,
        solidus_T_C=1100.0,
    )
    _local_store, _local_sim, _local_fO2, _local_curve = _capture_pt0_gate_curve(
        local_db,
        solidus_T_C=1200.0,
    )
    with sqlite3.connect(local_db) as conn:
        (key_hash,) = conn.execute(
            f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    _set_pt1_payload_codec_version(
        local_db,
        key_hash,
        rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,
    )

    replay = PT0DeterminismStore(
        "replay",
        db_path=local_db,
        read_only_base_db_path=base_db,
    )
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)

    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == base_curve
    assert replay.hits == 1


def test_gate_curve_codec_stamp_survives_seed_and_replays_from_target(
    tmp_path: Path,
) -> None:
    shard_db = tmp_path / "capture-shard.db"
    target_db = tmp_path / "seeded-base.db"
    _capture, _capture_sim, _capture_fO2, curve = _capture_pt0_gate_curve(
        shard_db,
        solidus_T_C=1100.0,
    )

    seed_cache(target_db, [shard_db])

    with sqlite3.connect(target_db) as conn:
        (stored_version,) = conn.execute(
            f"SELECT payload_codec_version FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored_version == rrd.PT0_GATE_CURVE_CODEC_VERSION

    replay = PT0DeterminismStore("replay", db_path=target_db)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)
    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == curve
    assert replay.hits == 1


@pytest.mark.parametrize("transport", ("seed", "populate", "epoch"))
@pytest.mark.parametrize(
    "disclosures",
    (
        (),
        (
            {
                "law_id": "FeO_association_gas:diagnostic",
                "provenance": "fixture",
                "authority": "diagnostic/extrapolated",
                "certified_band_K": None,
                "anchor_gaps_dex": {},
                "closer": "fixture",
                "certification_route": [],
                "evaluation_count": 1,
            },
        ),
    ),
    ids=("measured-empty", "measured-disclosure"),
)
def test_pt1_assembly_transports_measured_gate_disclosures_and_hits(
    tmp_path: Path,
    transport: str,
    disclosures: tuple[Mapping[str, Any], ...],
) -> None:
    shard_db = tmp_path / "capture-shard.sqlite"
    target_db = tmp_path / "assembled-cache.sqlite"
    _capture, _capture_sim, _capture_fO2, curve = _capture_pt0_gate_curve(
        shard_db,
        solidus_T_C=1100.0,
        law_disclosures=disclosures,
    )

    if transport == "seed":
        seed_cache(target_db, [shard_db])
    elif transport == "populate":
        from scripts import populate_reduced_real_cache

        populate_reduced_real_cache._merge_cache_shard(shard_db, target_db)
    else:
        from scripts import epoch_grind

        epoch_grind.merge_epoch_shards(target_db, [shard_db])

    with sqlite3.connect(target_db) as conn:
        (stored_disclosures,) = conn.execute(
            f"SELECT law_disclosures_json FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert json.loads(stored_disclosures) == list(disclosures)

    replay = PT0DeterminismStore("replay", db_path=target_db)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)
    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == curve
    assert replay.hits == 1
    assert replay._accepted_hit_disclosures == list(disclosures)


def test_populate_accepts_identical_pt1_payloads_with_different_capture_times(
    tmp_path: Path,
) -> None:
    from scripts import populate_reduced_real_cache

    shard_db = tmp_path / "capture-shard.sqlite"
    target_db = tmp_path / "target-cache.sqlite"
    _capture_pt0_gate_curve(
        shard_db,
        solidus_T_C=1100.0,
        law_disclosures=[],
    )
    with sqlite3.connect(shard_db) as source, sqlite3.connect(target_db) as target:
        source.backup(target)
    with sqlite3.connect(target_db) as connection:
        connection.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET created_at=?",
            ("2000-01-01T00:00:00Z",),
        )

    with sqlite3.connect(shard_db) as source, sqlite3.connect(target_db) as target:
        source_payload = source.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        target_payload = target.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        assert bytes(source_payload) == bytes(target_payload)
        source_created_at = source.execute(
            f"SELECT created_at FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        target_created_at = target.execute(
            f"SELECT created_at FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        assert source_created_at != target_created_at

    populate_reduced_real_cache._merge_cache_shard(shard_db, target_db)

    with sqlite3.connect(target_db) as connection:
        merged_payload = connection.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        assert bytes(merged_payload) == bytes(source_payload)


def test_pt1_exact_replay_reuses_persistent_admission_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "single-admission-decode.sqlite"
    _capture, _capture_sim, _capture_fO2, curve = _capture_pt0_gate_curve(
        db_path,
        solidus_T_C=1100.0,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)

    original_decode = rrd._decode_payload_for_admission
    calls = 0

    def count_admission_decode(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(rrd, "_decode_payload_for_admission", count_admission_decode)
    assert replay.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    ) == curve
    assert replay.hits == 1
    assert calls == 1


@pytest.mark.parametrize("transport", ("seed", "populate", "epoch"))
def test_pt1_assembly_replaces_stale_equilibrium_payload_bytes(
    tmp_path: Path,
    transport: str,
) -> None:
    from simulator.reduced_real_determinism import (
        PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION,
    )

    shard_db = tmp_path / "fresh-equilibrium-shard.sqlite"
    target_db = tmp_path / "stale-equilibrium-target.sqlite"
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    sim._last_vapor_pressures_source = {"SiO": "builtin_authoritative"}
    result.vapor_pressures_source = {"SiO": "builtin_authoritative"}
    key = capture._equilibrium_key(sim)
    payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    assert key["vapor_pressure_provider_selection"] == "builtin-vapor-pressure"
    payload["authority"]["vapor_pressure"] = _alphamelts_record_authority()[
        "vapor_pressure"
    ]
    capture._store("equilibrium_post_record", key, payload, law_disclosures=[])
    key_hash = hashlib.sha256(canonical_json_bytes(key)).hexdigest()
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(shard_db).put(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=canonical_json_bytes(key),
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
        law_disclosures=[],
    )

    with sqlite3.connect(shard_db) as conn:
        (fresh_payload_bytes,) = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        legacy_payload_bytes = canonical_json_bytes(
            {**payload, "legacy_equilibrium_marker": "old-row"}
        )
    # The target is a second real PT1 store with the same key and legacy bytes.
    target_store = PT1PersistentEquilibriumStore(target_db)
    with sqlite3.connect(shard_db) as source_conn, target_store._connect() as target_conn:
        source_conn.backup(target_conn)
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ?, payload_codec_version = ? "
            "WHERE key_hash = ?",
            (
                sqlite3.Binary(legacy_payload_bytes),
                hashlib.sha256(legacy_payload_bytes).hexdigest(),
                PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION - 1,
                key_hash,
            ),
        )

    if transport == "seed":
        seed_cache(target_db, [shard_db])
    elif transport == "populate":
        from scripts import populate_reduced_real_cache

        populate_reduced_real_cache._merge_cache_shard(shard_db, target_db)
    else:
        from scripts import epoch_grind

        epoch_grind.merge_epoch_shards(target_db, [shard_db])

    with sqlite3.connect(target_db) as conn:
        (stored_payload_bytes,) = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert bytes(stored_payload_bytes) == bytes(fresh_payload_bytes)


    replay = PT0DeterminismStore("replay", db_path=target_db)
    assert replay._lookup("equilibrium_post_record", key) is not None
    assert replay.hits == 1


def test_epoch_seed_migrates_b265_columns_and_legacy_row_still_misses(
    tmp_path: Path,
) -> None:
    from scripts import epoch_grind

    base_db = tmp_path / "unmigrated-b265-base.sqlite"
    shard_db = tmp_path / "seeded-epoch-shard.sqlite"
    _capture, _capture_sim, _capture_fO2, _curve = _capture_pt0_gate_curve(
        base_db,
        solidus_T_C=1100.0,
        law_disclosures=[],
    )
    with sqlite3.connect(base_db) as conn:
        (key_hash,) = conn.execute(
            f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} DROP COLUMN payload_codec_version"
        )
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} DROP COLUMN law_disclosures_json"
        )
    base_sha256 = hashlib.sha256(base_db.read_bytes()).hexdigest()

    summary = epoch_grind.seed_job_cache(
        shard_db,
        base_db,
        point_sources=[{"point_id": key_hash, "source": "ladder_polish"}],
        job_id="job-a",
    )

    # Removing the codec column makes this intentionally legacy; epoch seeding
    # must count it as superseded rather than copying a row that replay rejects.
    assert summary["seed_rows"] == 0
    assert summary["rejected_seed_rows"] == 1
    assert summary["superseded_seed_rows"] == 1
    with sqlite3.connect(base_db) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        assert not {"payload_codec_version", "law_disclosures_json"} & columns
    with sqlite3.connect(shard_db) as conn:
        shard_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        assert {"payload_codec_version", "law_disclosures_json"} <= shard_columns
        shard_row = conn.execute(
            f"SELECT payload_codec_version, law_disclosures_json "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert hashlib.sha256(base_db.read_bytes()).hexdigest() == base_sha256
    assert shard_row is None

    replay = PT0DeterminismStore(
        "replay",
        db_path=shard_db,
        read_only_base_db_path=base_db,
    )
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(replay_sim)
    with pytest.raises(PT0CacheMiss):
        replay.replay_gate_curve(
            replay_sim,
            fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
        )
    assert replay.hits == 0


@pytest.mark.parametrize(
    ("existing", "incoming", "expected"),
    [
        (None, "[]", "[]"),
        ('[{"kind":"a"}]', '[{"kind":"a"},{"kind":"b"}]', '[{"kind":"a"},{"kind":"b"}]'),
        ('[{"kind":"a"},{"kind":"b"}]', '[{"kind":"a"}]', '[{"kind":"a"},{"kind":"b"}]'),
        ('[{"kind":"a"}]', '[{"kind":"a"}]', '[{"kind":"a"}]'),
        ('[{"kind":"a"}]', '[{"kind":"b"}]', '[{"kind":"a"}]'),
        ('[{"kind":"a"}]', None, '[{"kind":"a"}]'),
    ],
)
def test_pt1_repair_notice_recapture_replaces_only_with_strict_superset(
    existing: str | None,
    incoming: str | None,
    expected: str | None,
) -> None:
    assert rrd.monotonic_repair_notices_json(existing, incoming) == expected


def test_pt1_recapture_hydrates_effective_notices_for_same_instance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recapture-known-notices.db"
    notice_a = {"kind": "repair_a"}
    notice_b = {"kind": "repair_b"}
    _store_repair_notice_test_record(
        PT0DeterminismStore("capture", db_path=db_path),
        notices=[notice_a, notice_b],
    )

    recapture = PT0DeterminismStore("capture", db_path=db_path)
    _store_repair_notice_test_record(recapture, notices=[notice_a])

    same_instance_entry, same_instance_notices = (
        _replay_repair_notice_test_record(recapture)
    )
    reopened_entry, reopened_notices = _replay_repair_notice_test_record(
        PT0DeterminismStore("replay", db_path=db_path)
    )

    _key, expected_payload = _repair_notice_test_record()
    assert same_instance_entry == reopened_entry == expected_payload
    assert same_instance_notices == reopened_notices == [notice_a, notice_b]


@pytest.mark.parametrize(
    ("base_notices", "incoming_notices", "stored_base_notices"),
    [
        (None, [{"kind": "repair_a"}], None),
        ([{"kind": "repair_a"}], [{"kind": "repair_a"}, {"kind": "repair_b"}],
         rrd.encode_repair_notices_json([{"kind": "repair_a"}])),
    ],
)
def test_pt1_recapture_against_attached_base_persists_and_reopens_notice_upgrade(
    tmp_path: Path,
    base_notices: list[dict[str, str]] | None,
    incoming_notices: list[dict[str, str]],
    stored_base_notices: str | None,
) -> None:
    base_db = tmp_path / "attached-base.db"
    local_db = tmp_path / "writable-shard.db"
    base_capture = PT0DeterminismStore("capture", db_path=base_db)
    _store_repair_notice_test_record(base_capture, notices=base_notices or [])
    key, _payload = _repair_notice_test_record()
    key_hash = hashlib.sha256(canonical_json_bytes(key)).hexdigest()
    if base_notices is None:
        with sqlite3.connect(base_db) as conn:
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                "SET repair_notices_json = NULL WHERE key_hash = ?",
                (key_hash,),
            )

    with sqlite3.connect(base_db) as conn:
        base_row = conn.execute(
            f"SELECT key_bytes, payload_bytes, repair_notices_json "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert base_row is not None
    assert base_row[2] == stored_base_notices
    base_sha256 = hashlib.sha256(base_db.read_bytes()).hexdigest()

    recapture = PT0DeterminismStore(
        "capture", db_path=local_db, read_only_base_db_path=base_db
    )
    with sqlite3.connect(local_db) as conn:
        assert conn.execute(
            f"SELECT 1 FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone() is None
    _store_repair_notice_test_record(recapture, notices=incoming_notices)

    with sqlite3.connect(local_db) as conn:
        local_row = conn.execute(
            f"SELECT key_bytes, payload_bytes, repair_notices_json "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert local_row is not None
    assert local_row[:2] == base_row[:2]
    assert local_row[2] == rrd.encode_repair_notices_json(incoming_notices)

    same_instance_entry, same_instance_notices = (
        _replay_repair_notice_test_record(recapture)
    )
    reopened_entry, reopened_notices = _replay_repair_notice_test_record(
        PT0DeterminismStore(
            "replay", db_path=local_db, read_only_base_db_path=base_db
        )
    )
    _key, expected_payload = _repair_notice_test_record()
    assert same_instance_entry == reopened_entry == expected_payload
    assert same_instance_notices == reopened_notices == incoming_notices
    with sqlite3.connect(base_db) as conn:
        unchanged_base_row = conn.execute(
            f"SELECT key_bytes, payload_bytes, repair_notices_json "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert unchanged_base_row == base_row
    assert hashlib.sha256(base_db.read_bytes()).hexdigest() == base_sha256


def test_pt1_concurrent_first_open_upgrades_legacy_schema_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "concurrent-legacy-open.db"
    _store_repair_notice_test_record(
        PT0DeterminismStore("capture", db_path=db_path), notices=[]
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
            "DROP COLUMN repair_notices_json"
        )
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET store_schema_version = ?",
            (rrd.PT1_LEGACY_STORE_SCHEMA_VERSION,),
        )
        conn.execute(
            f"UPDATE {rrd.PT1_METADATA_TABLE} SET value = ? "
            "WHERE key = 'store_schema_version'",
            (rrd.PT1_LEGACY_STORE_SCHEMA_VERSION,),
        )

    schema_read_barrier = threading.Barrier(2)
    pragma = f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"

    class BarrierConnection(sqlite3.Connection):
        synchronized = False

        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if not self.synchronized and sql == pragma:
                rows = cursor.fetchall()
                self.synchronized = True
                schema_read_barrier.wait(timeout=20)
                return rows
            return cursor

    class SynchronizedStore(rrd.PT1PersistentEquilibriumStore):
        def _connect(self):
            conn = sqlite3.connect(
                self.db_path,
                timeout=60.0,
                factory=BarrierConnection,
            )
            conn.row_factory = sqlite3.Row
            return conn

    def open_store(_index: int) -> str | None:
        try:
            SynchronizedStore(db_path)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        failures = list(executor.map(open_store, (1, 2)))

    assert failures == [None, None]
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
    assert "repair_notices_json" in columns


def test_pt1_concurrent_row_copy_checks_and_writes_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    entry = next(iter(capture._entries.values()))
    key = entry["key"]
    key_hash = entry["key_hash"]
    key_bytes = entry["key_bytes"].encode("utf-8")
    first_payload = copy.deepcopy(entry["payload"])
    second_payload = copy.deepcopy(first_payload)
    second_payload["equilibrium_result"]["vapor_pressures_Pa"]["SiO"] = 2.0
    assert canonical_json_bytes(first_payload) != canonical_json_bytes(
        second_payload
    )

    source_paths = (tmp_path / "first-source.sqlite", tmp_path / "second-source.sqlite")
    for source_path, payload in zip(
        source_paths,
        (first_payload, second_payload),
    ):
        payload_bytes = canonical_json_bytes(payload)
        PT1PersistentEquilibriumStore(source_path).put(
            artifact="equilibrium_post_record",
            key=key,
            key_bytes=key_bytes,
            key_hash=key_hash,
            payload=payload,
            payload_bytes=payload_bytes,
            payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
            repair_notices_json=entry["repair_notices_json"],
            law_disclosures=entry["law_disclosures"],
        )

    target_path = tmp_path / "atomic-row-copy-target.sqlite"
    PT1PersistentEquilibriumStore(target_path)
    admission_barrier = threading.Barrier(2)
    original_admission = rrd._pt1_row_admission

    def synchronized_admission(row):
        try:
            admission_barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return original_admission(row)

    monkeypatch.setattr(rrd, "_pt1_row_admission", synchronized_admission)

    def copy_source(source_path: Path) -> str:
        try:
            result = rrd._copy_pt1_equilibrium_rows(
                (source_path,),
                target_path,
                require_capture_epoch_match=False,
            )
        except rrd._PT1CacheMergeCollision:
            return "collision"
        return "copied" if result["inserted_rows"] == 1 else "unchanged"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(copy_source, source_paths))

    assert sorted(outcomes) == ["collision", "copied"]
    with sqlite3.connect(target_path) as conn:
        row = conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert row is not None
    assert bytes(row[0]) in {
        canonical_json_bytes(first_payload),
        canonical_json_bytes(second_payload),
    }


def test_pt1_capture_rejects_unproven_primary_with_admitted_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_binding_admission as admission
    from simulator.engine_binding_admission import GateCurveEvidence

    db_path = tmp_path / "unproven-gate-producer.sqlite"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._liquid_fraction_provider = "magemin"
    register_synthetic_gate_curve_producer(sim)
    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    curve = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
        "solidus_T_C": 1200.0,
        "liquidus_T_C": 1600.0,
        "path": ((1200.0, 0.0), (1600.0, 1.0)),
    }

    selected_binding_evidence = admission.gate_curve_memo_evidence(
        sim,
        curve,
        include_selected_bindings=True,
    )
    assert selected_binding_evidence.eligible
    unproven_record = admission.write_gate_curve_memo_record(
        sim,
        curve,
        evidence=GateCurveEvidence("unproven", True),
        provider_outcome=rrd.GateCurveProviderOutcome.PRODUCED,
    )
    assert unproven_record["origin"]["producer_evidence"] == "unproven"
    assert not admission.gate_curve_memo_origin_matches(
        sim,
        curve,
        unproven_record["origin"],
        fresh_evidence=False,
    )
    assert not admission.gate_curve_memo_reuse_eligible(
        sim,
        curve,
        unproven_record["origin"],
        fresh_evidence=False,
        evidence=selected_binding_evidence,
    )
    sim._freeze_gate_liquid_fraction_cache = {
        **unproven_record,
        "law_disclosures": [],
    }

    capture.capture_gate_curve(
        sim,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        curve=curve,
        law_disclosures=[],
    )

    assert capture.capture_sequence == []
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 0


def test_pt1_projected_gate_notice_replays_into_run_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "pt1-projected-notice.db"
    notice = {
        "kind": "composition_projected",
        "reason": "composition_projected",
        "authority": "extrapolated",
        "certified_band": {
            "engine": "magemin",
            "database": "ig",
            "bulk_components": ["SiO2", "MgO"],
        },
        "dropped_components": [
            {"component": "P2O5", "mass_fraction": 0.01},
        ],
    }
    curve = {
        "source": "unit-test:composition_projected",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1600.0,
        "path": ((1100.0, 0.0), (1600.0, 1.0)),
        "composition_projected_notice": notice,
    }

    capture = PT0DeterminismStore("capture", db_path=db_path)
    capture_sim = _build_pt0_sim(capture)
    capture_sim.start_campaign(CampaignPhase.C2A_STAGED)
    capture_fO2_log = capture_sim._compute_intrinsic_melt_fO2()
    seed_gate_curve_memo_record(capture_sim, curve)
    capture.capture_gate_curve(
        capture_sim,
        fO2_log=capture_fO2_log,
        curve=curve,
    )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = _build_pt0_sim(replay)
    replay_sim.start_campaign(CampaignPhase.C2A_STAGED)
    replayed_curve = replay_sim._freeze_gate_curve()
    metadata = {}
    _attach_composition_projected_liquidus_notice(metadata, replay_sim)

    assert replayed_curve == curve
    assert metadata["composition_projected_liquidus_notice"]["notices"] == [
        notice
    ]


def test_pt1_domain_gate_prediction_replays_with_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / 'domain-gate-notice.db'
    capture = PT0DeterminismStore('capture', db_path=db_path)
    sim = _build_pt0_sim(capture)
    monkeypatch.setattr(sim, '_dispatch_only', lambda intent, **kw: IntentResult(
        intent=intent, status='out_of_domain', transition=None,
        diagnostic={'backend_status_reason': 'silicate_window'},
    ))
    curve = sim._melt_redox_liquidus_gate_curve()
    assert curve['is_liquidus_floor'] is True
    notice = curve['gate_provider_refusal']
    assert notice['reason'] == 'silicate_window'
    assert notice['authority'] == 'extrapolated'
    assert notice['qualification'] == 'out_of_domain_kress91_floor_prediction'
    assert notice['certified_band']['sio2_wt_pct'] == [30.0, 80.0]
    assert notice['certified_band']['min_T_C'] == 1200.0
    assert notice['certified_band']['max_T_C'] == 1630.0
    seed_gate_curve_memo_record(sim, curve)
    capture.capture_gate_curve(
        sim, fO2_log=sim._current_melt_redox_fO2_log(), curve=curve,
    )

    replay_sim = _build_pt0_sim(PT0DeterminismStore('replay', db_path=db_path))
    replayed_curve = replay_sim._establish_melt_redox_gate_authority_for_current_hour()
    assert replayed_curve == curve
    assert replayed_curve['is_liquidus_floor'] is True
    assert replay_sim._make_snapshot().liquid_fraction_gate['redox'][
        'gate_provider_refusal'
    ] == notice
    activity = replay_sim._native_fe_saturation_state(
        replay_sim.melt.composition_wt_pct(),
        fe3_over_sigma_fe=0.1,
        T_K=1800.0 + 273.15,
        pressure_bar=float(replay_sim.melt.p_total_mbar) / 1000.0,
        fO2_log=float(replay_sim._current_melt_redox_fO2_log()),
    )
    assert any(
        item.get('kind') == 'liquidus_unknown'
        for item in activity.get('notices', ())
    )


def _store_raw_replay_payload(
    store: PT0DeterminismStore,
    artifact: str,
    key: dict,
    payload: dict,
    *,
    law_disclosures=(),
    stale_codec: bool = True,
) -> None:
    """Inject a replay payload, optionally marking its codec stamp stale."""

    key_bytes = rrd.canonical_json_bytes(key)
    payload_bytes = rrd.canonical_json_bytes(payload)
    key_hash = rrd._sha256(key_bytes)
    store.persistent_store.put(
        artifact=artifact,
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=rrd._sha256(payload_bytes),
        law_disclosures=law_disclosures,
    )
    codec_version = rrd._payload_codec_version(artifact)
    if stale_codec and codec_version is not None:
        with sqlite3.connect(store.persistent_path) as conn:
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                "SET payload_codec_version = ? WHERE key_hash = ?",
                (codec_version - 1, key_hash),
            )


def _store_unadmitted_test_payload(
    store: PT0DeterminismStore,
    artifact: str,
    key: dict,
    payload: dict,
    *,
    law_disclosures=(),
) -> None:
    if store.persistent_store is None:
        store._store(
            artifact,
            key,
            payload,
            law_disclosures=law_disclosures,
        )
    else:
        _store_raw_replay_payload(
            store,
            artifact,
            key,
            payload,
            law_disclosures=law_disclosures,
        )


def test_pt0_gate_curve_codec_misses_floor_rows_without_magemin_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / 'markerless-gate-curve.db'
    store = PT0DeterminismStore('capture', db_path=db_path)
    key = _freeze_gate_key()
    assert 'gate_curve_codec_version' not in key
    assert rrd.PT0_GATE_CURVE_CODEC_VERSION == 4
    assert rrd._curve_payload({
        'source': 'gate_liquid_fraction',
        'solidus_T_C': 950.0,
        'liquidus_T_C': 1630.0,
        'path': (),
    })['is_liquidus_floor'] is False
    markerless_payload = {
        'curve': {
            'source': 'gate_liquid_fraction:fallback:magemin-shadow',
            'solidus_T_C': 1200.0,
            'liquidus_T_C': 1200.000001,
            'path': [
                {'temperature_C': 1200.0, 'liquid_fraction': 0.0},
                {'temperature_C': 1200.000001, 'liquid_fraction': 1.0},
            ],
            'is_liquidus_floor': True,
            'gate_provider_step_down': {
                'failed_providers': [],
                'realized_provider': 'magemin-shadow',
            },
        },
    }
    markerless_disclosure = {
        'law_id': 'markerless-test',
        'provenance': 'test',
        'authority': 'extrapolated',
        'certified_band_K': [1200.0, 1600.0],
        'anchor_gaps_dex': {},
        'closer': 'test',
        'certification_route': [],
        'evaluation_count': 1,
    }
    _store_raw_replay_payload(
        store,
        'freeze_gate_curve',
        key,
        markerless_payload,
        law_disclosures=[markerless_disclosure],
        stale_codec=False,
    )
    with sqlite3.connect(db_path) as conn:
        (stored_codec_version,) = conn.execute(
            f"SELECT payload_codec_version FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored_codec_version == rrd.PT0_GATE_CURVE_CODEC_VERSION
    old_replay = PT0DeterminismStore('replay', db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        old_replay._lookup('freeze_gate_curve', key)
    assert old_replay.hits == 0
    alternate_key = dict(key)
    alternate_pressure = float(key['controls']['pressure_bar']) + 0.01
    alternate_controls = dict(key['controls'])
    alternate_controls['pressure_bar'] = alternate_pressure
    alternate_key['controls'] = alternate_controls
    cache_key = key['freeze_gate_cache_key']._replace(
        pressure_bar=alternate_pressure,
    )
    alternate_key['freeze_gate_cache_key'] = cache_key
    valid_payload = {
        'curve': {
            'source': 'gate_liquid_fraction:fallback:magemin-shadow',
            'solidus_T_C': 1200.0,
            'liquidus_T_C': 1200.000001,
            'path': [
                {'temperature_C': 1200.0, 'liquid_fraction': 0.0},
                {'temperature_C': 1200.000001, 'liquid_fraction': 1.0},
            ],
            'is_liquidus_floor': True,
            'gate_provider_step_down': {
                'failed_providers': [],
                'realized_provider': 'magemin-shadow',
                'magemin_sourced': True,
            },
        },
    }
    store._store(
        'freeze_gate_curve',
        alternate_key,
        valid_payload,
        law_disclosures=[],
    )

    replay = PT0DeterminismStore('replay', db_path=db_path)
    accepted = []
    original_accept = replay._accept_cached_hit

    def record_accepted(entry, *, owner=None, decoded_payload=None):
        accepted.append(entry)
        return original_accept(
            entry,
            owner=owner,
            decoded_payload=decoded_payload,
        )

    replay._accept_cached_hit = record_accepted
    payload = replay._lookup_first_available(
        'freeze_gate_curve', (key, alternate_key),
    )
    assert payload == valid_payload
    assert replay.hits == 1
    assert len(accepted) == 1
    assert rrd.canonical_json_bytes(accepted[0]['key']) == (
        rrd.canonical_json_bytes(alternate_key)
    )
    assert replay._accepted_hit_disclosures == []
    assert rrd.canonical_json_bytes(replay.replay_sequence[-1]['key']) == (
        rrd.canonical_json_bytes(alternate_key)
    )

    # The markerless floor row missed replay while current-stamped; only the
    # legacy stamp lets recapture replace that row after the replay assertions.
    _set_pt1_payload_codec_version(
        db_path,
        _key_hash(key),
        rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,
    )
    store._store('freeze_gate_curve', key, valid_payload)
    recapture = PT0DeterminismStore('replay', db_path=db_path)
    assert recapture._lookup('freeze_gate_curve', key) == valid_payload
    assert recapture.hits == 1


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_markerless_gate_curve_row_is_a_miss_and_can_be_replaced(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "markerless-gate-row-recapture.sqlite" if persistent else None
    store = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    legacy_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
        },
    }
    current_payload = {
        "curve": {
            **legacy_payload["curve"],
            "is_liquidus_floor": False,
        },
    }

    _store_unadmitted_test_payload(
        store,
        "freeze_gate_curve",
        key,
        legacy_payload,
        law_disclosures=[],
    )
    with pytest.raises(PT0CacheMiss):
        store._lookup("freeze_gate_curve", key)

    store._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else store
    )

    assert replay._lookup("freeze_gate_curve", key) == current_payload


@pytest.mark.parametrize(
    "old_repair_notices_json",
    ["{invalid notice JSON", '[{"kind":"old repair"}]'],
    ids=("malformed-stale-notices", "valid-stale-notices"),
)
def test_pt1_recapture_supersedes_stale_row_without_decoding_payload_or_notices(
    tmp_path: Path,
    old_repair_notices_json: str,
) -> None:
    db_path = tmp_path / "stale-invalid-gate-payload.sqlite"
    store = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    legacy_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    _store_raw_replay_payload(
        store,
        "freeze_gate_curve",
        key,
        legacy_payload,
        law_disclosures=[],
    )
    key_hash = _key_hash(key)
    with sqlite3.connect(db_path) as conn:
        codec_version, stored_key_bytes = conn.execute(
            f"SELECT payload_codec_version, key_bytes "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert codec_version == rrd.PT0_GATE_CURVE_CODEC_VERSION - 1 == 3
    assert bytes(stored_key_bytes) == rrd.canonical_json_bytes(key)
    invalid_old_payload = b"{invalid json"
    invalid_old_key = b"{invalid key"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET key_bytes = ?, payload_bytes = ?, payload_sha256 = ?, "
            "repair_notices_json = ? "
            "WHERE key_hash = ?",
            (
                sqlite3.Binary(invalid_old_key),
                sqlite3.Binary(invalid_old_payload),
                hashlib.sha256(invalid_old_payload).hexdigest(),
                old_repair_notices_json,
                key_hash,
            ),
        )

    current_payload = {
        "curve": {
            **legacy_payload["curve"],
            "is_liquidus_floor": False,
        },
    }
    new_notice = {"kind": "new repair"}
    recapture = PT0DeterminismStore("capture", db_path=db_path)
    recapture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        repair_notices=[new_notice],
        law_disclosures=[],
    )

    current_key_bytes = rrd.canonical_json_bytes(key)
    current_payload_bytes = rrd.canonical_json_bytes(current_payload)
    with sqlite3.connect(db_path) as conn:
        (
            stored_key_hash,
            stored_key_sha256,
            stored_key_bytes,
            stored_payload_sha256,
            stored_payload_bytes,
            stored_codec_version,
            stored_repair_notices_json,
        ) = conn.execute(
            f"SELECT key_hash, key_sha256, key_bytes, payload_sha256, "
            f"payload_bytes, payload_codec_version, repair_notices_json FROM "
            f"{PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert stored_key_hash == key_hash
    assert stored_key_sha256 == hashlib.sha256(current_key_bytes).hexdigest()
    assert bytes(stored_key_bytes) == current_key_bytes
    assert stored_payload_sha256 == hashlib.sha256(current_payload_bytes).hexdigest()
    assert bytes(stored_payload_bytes) == current_payload_bytes
    assert stored_codec_version == rrd.PT0_GATE_CURVE_CODEC_VERSION
    assert stored_repair_notices_json == rrd.encode_repair_notices_json([new_notice])

    replay = PT0DeterminismStore("replay", db_path=db_path)
    assert replay._lookup("freeze_gate_curve", key) == current_payload


def test_pt1_copy_skips_stale_invalid_source_payload_without_parsing(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "stale-invalid-source.sqlite"
    target_path = tmp_path / "stale-invalid-target.sqlite"
    source = PT0DeterminismStore("capture", db_path=source_path)
    key = _freeze_gate_key()
    payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    _store_raw_replay_payload(
        source,
        "freeze_gate_curve",
        key,
        payload,
        law_disclosures=[],
    )
    key_hash = _key_hash(key)
    invalid_payload_bytes = b"{invalid json"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ? WHERE key_hash = ?",
            (
                sqlite3.Binary(invalid_payload_bytes),
                hashlib.sha256(invalid_payload_bytes).hexdigest(),
                key_hash,
            ),
        )

    result = rrd._copy_pt1_equilibrium_rows(
        (source_path,),
        target_path,
        require_capture_epoch_match=False,
    )

    assert result["inserted_rows"] == 0
    assert result["sources"][0]["inserted_rows"] == 0
    with sqlite3.connect(target_path) as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "bad_band",
    (None, [0.0, 100.0]),
    ids=("missing-band", "wrong-band"),
)
def test_extrapolated_gate_rows_that_miss_d447_are_replaceable(
    tmp_path: Path,
    persistent: bool,
    bad_band: list[float] | None,
) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    db_path = (
        tmp_path / "extrapolated-gate-row-recapture.sqlite"
        if persistent
        else None
    )
    store = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(store)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._liquid_fraction_provider = "magemin"
    register_synthetic_gate_curve_producer(sim)
    composition_wt_pct = {
        "SiO2": 80.001,
        "MgO": 10.0,
        "FeO": 9.999,
    }
    composition_mol = {
        species: amount
        / resolve_species_formula(
            species,
            sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
        for species, amount in composition_wt_pct.items()
    }
    ledger = AtomLedger(registry=sim.atom_ledger.registry)
    ledger.load_external_mol(
        "process.cleaned_melt",
        composition_mol,
        source="r33 extrapolated cache admission regression",
        material_origin="feedstock",
    )
    sim.atom_ledger = ledger
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim.inventory.inert_melt_components_kg.clear()
    sim._project_cleaned_melt_from_atom_ledger()
    key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=float(sim._current_melt_redox_fO2_log()),
        fe_redox_policy="intrinsic",
        provider_role="fallback",
    )
    identity = key["freeze_gate_cache_key"].magemin_domain_identity
    assert identity.silica_verdict == "extrapolated"
    assert identity.certified_band == (30.0, 80.0)
    stored_identity = rrd._magemin_identity_from_replay_key(
        rrd._json_ready(key)
    )
    assert stored_identity.matches(identity)

    stale_curve = {
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
        "solidus_T_C": 1200.0,
        "liquidus_T_C": 1300.0,
        "path": [
            {"temperature_C": 1200.0, "liquid_fraction": 0.0},
            {"temperature_C": 1300.0, "liquid_fraction": 1.0},
        ],
        "is_liquidus_floor": False,
        "authority": "extrapolated",
        "gate_provider_step_down": {
            "failed_providers": [],
            "realized_provider": None,
            "magemin_sourced": True,
        },
    }
    if bad_band is not None:
        stale_curve["certified_band"] = {"sio2_wt_pct": bad_band}
    current_curve = {
        **stale_curve,
        "certified_band": {"sio2_wt_pct": [30.0, 80.0]},
    }
    _store_unadmitted_test_payload(
        store,
        "freeze_gate_curve",
        key,
        {"curve": stale_curve},
        law_disclosures=[],
    )
    miss = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else store
    )
    with pytest.raises(PT0CacheMiss):
        miss._lookup("freeze_gate_curve", key)

    store._store(
        "freeze_gate_curve",
        key,
        {"curve": current_curve},
        law_disclosures=[],
    )
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else store
    )
    assert replay._lookup("freeze_gate_curve", key) == {
        "curve": current_curve,
    }


def test_legacy_magemin_gate_curve_replays_with_d447_feo_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    composition_wt_pct = {
        "SiO2": 79.9,
        "MgO": 14.6,
        "MnO": 0.5,
        "FeO": 5.0,
    }
    db_path = tmp_path / "legacy-magemin-domain-curve.sqlite"

    def configure_sim(store):
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim._liquid_fraction_provider = "magemin"
        register_synthetic_gate_curve_producer(sim)
        composition_mol = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            composition_mol,
            source="r47 markerless MAGEMin step-down replay regression",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim._chem_kernel = sim._build_chemistry_kernel()
        sim.inventory.inert_melt_components_kg.clear()
        sim._project_cleaned_melt_from_atom_ledger()
        _install_magemin_subprocess_gate(sim, monkeypatch)
        alpha_provider = sim._chem_registry.gate_provider_order[1]
        monkeypatch.setattr(alpha_provider, "_ensure_backend", lambda: None)
        return sim

    live_sim = configure_sim(PT0DeterminismStore("capture"))
    live_curve = live_sim._freeze_gate_curve_record()["curve"]
    assert live_curve["authority"] == "extrapolated"
    assert live_curve["certified_band"] == {
        "sio2_wt_pct": [30.0, 80.0],
    }
    assert live_curve.get("is_liquidus_floor", False) is False
    assert live_curve["gate_provider_step_down"]["magemin_sourced"] is True

    fO2_log = float(live_sim._current_melt_redox_fO2_log())
    key = canonical_replay_key(
        live_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=fO2_log,
        fe_redox_policy="intrinsic",
        provider_role="fallback",
    )
    payload = {"curve": rrd._curve_payload(live_curve)}
    store = PT0DeterminismStore("capture", db_path=db_path)
    legacy_payload = copy.deepcopy(payload)
    legacy_curve = legacy_payload["curve"]
    legacy_curve["is_liquidus_floor"] = False
    legacy_curve["gate_provider_step_down"].pop("magemin_sourced")
    _store_unadmitted_test_payload(
        store,
        "freeze_gate_curve",
        key,
        legacy_payload,
        law_disclosures=[],
    )

    legacy_replay = PT0DeterminismStore("replay", db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        legacy_replay._lookup("freeze_gate_curve", key)

    store._store(
        "freeze_gate_curve",
        key,
        payload,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim = configure_sim(replay)
    curve = replay_sim._establish_melt_redox_gate_authority_for_current_hour()
    assert curve["authority"] == "extrapolated"
    assert replay.last_cache_state == "cached_exact"

    activity = replay_sim._native_fe_saturation_state(
        composition_wt_pct,
        fe3_over_sigma_fe=0.1,
        T_K=1800.0 + 273.15,
        pressure_bar=float(replay_sim.melt.p_total_mbar) / 1000.0,
        fO2_log=float(replay_sim._current_melt_redox_fO2_log()),
    )
    identity_notice = next(
        notice
        for notice in activity["notices"]
        if notice.get("kind") == "magemin_domain_identity"
    )
    assert identity_notice["authority"] == "extrapolated"
    assert identity_notice["certified_band"] == {
        "sio2_wt_pct": [30.0, 80.0],
    }


def test_projected_magemin_step_down_replay_keeps_d447_feo_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    composition_wt_pct = {
        "SiO2": 79.9,
        "MgO": 14.6,
        "MnO": 0.5,
        "FeO": 5.0,
    }
    db_path = tmp_path / "projected-magemin-step-down.sqlite"

    def configure_sim(store):
        sim = _build_pt0_sim(store)
        sim.start_campaign(CampaignPhase.C2A_STAGED)
        sim._liquid_fraction_provider = "magemin"
        register_synthetic_gate_curve_producer(sim)
        composition_mol = {
            species: amount
            / resolve_species_formula(
                species,
                sim.species_formula_registry,
            ).molar_mass_kg_per_mol()
            for species, amount in composition_wt_pct.items()
        }
        ledger = AtomLedger(registry=sim.atom_ledger.registry)
        ledger.load_external_mol(
            "process.cleaned_melt",
            composition_mol,
            source="r33 projected MAGEMin step-down replay regression",
            material_origin="feedstock",
        )
        sim.atom_ledger = ledger
        sim._chem_kernel = sim._build_chemistry_kernel()
        sim.inventory.inert_melt_components_kg.clear()
        sim._project_cleaned_melt_from_atom_ledger()
        trace = _install_magemin_subprocess_gate(sim, monkeypatch)
        alpha_provider = sim._chem_registry.gate_provider_order[1]
        monkeypatch.setattr(alpha_provider, "_ensure_backend", lambda: None)
        return sim, trace

    live_store = PT0DeterminismStore("capture")
    live_sim, trace = configure_sim(live_store)
    live_curve = live_sim._establish_melt_redox_gate_authority_for_current_hour()
    assert trace["provider_diagnostics"][0]["backend_status"] == "out_of_domain"
    assert (
        trace["provider_diagnostics"][0]["backend_diagnostics"][
            "projected_liquidus_status"
        ]
        == "ok"
    )
    assert live_curve["authority"] == "extrapolated"
    assert live_curve["certified_band"] == {
        "sio2_wt_pct": [30.0, 80.0],
    }
    step_down = live_curve["gate_provider_step_down"]
    assert len(step_down["failed_providers"]) == 2
    assert step_down["failed_providers"][0]["status"] == "out_of_domain"
    assert step_down["failed_providers"][1]["status"] != "ok"
    assert step_down["realized_provider"] is None
    assert step_down["magemin_sourced"] is True

    key = canonical_replay_key(
        live_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=float(live_sim._current_melt_redox_fO2_log()),
        fe_redox_policy="intrinsic",
        provider_role="fallback",
    )
    # This top-level authority is an intentional payload addition sourced
    # from the production resolver; the numeric curve remains byte-exact.
    payload = {
        "curve": rrd._curve_payload(live_curve),
        "authority": {
            "vapor_pressure": rrd._provider_identity(
                live_sim,
                ChemistryIntent.VAPOR_PRESSURE,
            ),
        },
    }
    key_hash = rrd._sha256(rrd.canonical_json_bytes(key))
    captured_entry = live_store._entries[key_hash]
    assert captured_entry["payload"]["curve"] == rrd._curve_payload(live_curve)
    assert captured_entry["payload"] == payload
    store = PT0DeterminismStore("capture", db_path=db_path)
    legacy_payload = copy.deepcopy(captured_entry["payload"])
    legacy_step_down = legacy_payload["curve"]["gate_provider_step_down"]
    assert legacy_step_down["realized_provider"] is None
    legacy_step_down.pop("magemin_sourced")
    _store_unadmitted_test_payload(
        store,
        "freeze_gate_curve",
        key,
        legacy_payload,
        law_disclosures=[],
    )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        replay._lookup("freeze_gate_curve", key)
    assert replay.hits == 0

    store._store(
        "freeze_gate_curve",
        key,
        payload,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim, _replay_trace = configure_sim(replay)
    curve = replay_sim._establish_melt_redox_gate_authority_for_current_hour()
    assert curve["authority"] == "extrapolated"
    assert replay.last_cache_state == "cached_exact"

    activity = replay_sim._native_fe_saturation_state(
        composition_wt_pct,
        fe3_over_sigma_fe=0.1,
        T_K=1800.0 + 273.15,
        pressure_bar=float(replay_sim.melt.p_total_mbar) / 1000.0,
        fO2_log=float(replay_sim._current_melt_redox_fO2_log()),
    )
    identity_notice = next(
        notice
        for notice in activity["notices"]
        if notice.get("kind") == "magemin_domain_identity"
    )
    assert identity_notice["authority"] == "extrapolated"
    assert identity_notice["certified_band"] == {
        "sio2_wt_pct": [30.0, 80.0],
    }


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_decoder_unreadable_gate_row_misses_before_hit_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "unreadable-gate-row.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    unreadable_payload = copy.deepcopy(current_payload)
    unreadable_payload["curve"].pop("liquidus_T_C")
    _store_unadmitted_test_payload(
        capture,
        "freeze_gate_curve",
        key,
        unreadable_payload,
        law_disclosures=[],
    )

    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    with pytest.raises(PT0CacheMiss):
        replay._lookup("freeze_gate_curve", key)
    assert replay.hits == 0

    capture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    assert replay._lookup("freeze_gate_curve", key) == current_payload


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_empty_gate_curve_path_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "empty-gate-path.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    empty_path_payload = copy.deepcopy(current_payload)
    empty_path_payload["curve"]["path"] = []
    _store_unadmitted_test_payload(
        capture,
        "freeze_gate_curve",
        key,
        empty_path_payload,
        law_disclosures=[],
    )

    with pytest.raises(PT0CacheMiss):
        capture._lookup("freeze_gate_curve", key)
    assert capture.hits == 0

    capture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else capture
    assert replay._lookup("freeze_gate_curve", key) == current_payload


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_gate_codec_admission_uses_the_row_version(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "gate-codec-current.sqlite" if persistent else None
    store = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    store._store("freeze_gate_curve", key, payload, law_disclosures=[])
    key_hash = _key_hash(key)
    stored_entry = store._entries[key_hash]
    assert "payload_codec_version" not in stored_entry["payload"]
    assert stored_entry["payload_codec_version"] == rrd.PT0_GATE_CURVE_CODEC_VERSION
    if persistent:
        assert store.persistent_store is not None
        persisted_entry = store.persistent_store.get(
            artifact="freeze_gate_curve",
            key=key,
            key_bytes=canonical_json_bytes(key),
            key_hash=key_hash,
        )
        assert persisted_entry is not None
        assert persisted_entry["payload_codec_version"] == (
            rrd.PT0_GATE_CURVE_CODEC_VERSION
        )

    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else store

    decoded = replay._lookup_first_available(
        "freeze_gate_curve",
        (key,),
        return_decoded=True,
    )

    assert decoded["path"] == ((1200.0, 0.0), (1300.0, 1.0))
    assert replay.hits == 1


def _set_pt1_payload_codec_version(
    db_path: Path,
    key_hash: str,
    version: int | None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_codec_version = ? WHERE key_hash = ?",
            (version, key_hash),
        )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "row_version",
    (None, rrd.PT0_GATE_CURVE_CODEC_VERSION - 1),
    ids=("missing", "old"),
)
def test_gate_codec_admission_rejects_and_replaces_a_stale_row_version(
    tmp_path: Path,
    persistent: bool,
    row_version: int | None,
) -> None:
    db_path = tmp_path / "gate-codec-stale.sqlite" if persistent else None
    store = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    store._store("freeze_gate_curve", key, current_payload, law_disclosures=[])
    key_hash = _key_hash(key)
    store._entries[key_hash]["payload_codec_version"] = row_version
    if persistent:
        assert db_path is not None
        _set_pt1_payload_codec_version(db_path, key_hash, row_version)

    miss_store = (
        PT0DeterminismStore("replay", db_path=db_path) if persistent else store
    )
    with pytest.raises(PT0CacheMiss):
        miss_store._lookup("freeze_gate_curve", key)
    assert miss_store.hits == 0

    store._store("freeze_gate_curve", key, current_payload, law_disclosures=[])
    entry = store._entries[key_hash]
    assert entry["payload_codec_version"] == rrd.PT0_GATE_CURVE_CODEC_VERSION
    assert entry["payload"] == current_payload
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else store
    assert replay._lookup("freeze_gate_curve", key) == current_payload
    assert replay.hits == 1


def test_malformed_stale_gate_row_misses_and_recapture_replaces_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "malformed-stale-gate.sqlite"
    key = _freeze_gate_key()
    current_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    initial_capture = PT0DeterminismStore("capture", db_path=db_path)
    initial_capture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    key_hash = _key_hash(key)
    malformed_payload_bytes = b'{"curve":'
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ?, payload_codec_version = ? "
            "WHERE key_hash = ?",
            (
                sqlite3.Binary(malformed_payload_bytes),
                hashlib.sha256(malformed_payload_bytes).hexdigest(),
                rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,
                key_hash,
            ),
        )

    replay = PT0DeterminismStore("replay", db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        replay._lookup("freeze_gate_curve", key)
    assert replay.hits == 0

    recapture = PT0DeterminismStore("capture", db_path=db_path)
    recapture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    assert recapture.persistent_store is not None
    persisted = recapture.persistent_store.get(
        artifact="freeze_gate_curve",
        key=key,
        key_bytes=canonical_json_bytes(key),
        key_hash=key_hash,
    )
    assert persisted is not None
    assert persisted["payload"] == current_payload
    assert persisted["payload_codec_version"] == rrd.PT0_GATE_CURVE_CODEC_VERSION
    replay_after_recapture = PT0DeterminismStore("replay", db_path=db_path)
    assert replay_after_recapture._lookup("freeze_gate_curve", key) == current_payload


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_gate_codec_stale_row_with_different_payload_bytes_is_replaceable(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "gate-codec-different-payload.sqlite" if persistent else None
    store = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    legacy_payload = {
        "curve": {
            "source": "gate_liquid_fraction",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
        },
    }
    current_payload = copy.deepcopy(legacy_payload)
    current_payload["curve"]["solidus_T_C"] = 1190.0
    current_payload["curve"]["path"][0]["temperature_C"] = 1190.0
    assert canonical_json_bytes(legacy_payload) != canonical_json_bytes(
        current_payload
    )
    store._store("freeze_gate_curve", key, legacy_payload, law_disclosures=[])
    key_hash = _key_hash(key)
    old_version = rrd.PT0_GATE_CURVE_CODEC_VERSION - 1
    store._entries[key_hash]["payload_codec_version"] = old_version
    if persistent:
        assert db_path is not None
        _set_pt1_payload_codec_version(db_path, key_hash, old_version)

    store._store("freeze_gate_curve", key, current_payload, law_disclosures=[])

    entry = store._entries[key_hash]
    assert entry["payload"] == current_payload
    assert entry["payload_codec_version"] == rrd.PT0_GATE_CURVE_CODEC_VERSION
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else store
    assert replay._lookup("freeze_gate_curve", key) == current_payload
    assert replay.hits == 1


def test_pt1_gate_capture_replaces_pre_authority_codec_v3_row(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gate-authority-codec-recapture.sqlite"
    original, sim, fO2_log, curve = _capture_pt0_gate_curve(
        db_path,
        solidus_T_C=1200.0,
    )
    key_hash = str(original.capture_sequence[-1]["hash"])
    current_entry = original._entries[key_hash]
    legacy_payload = {"curve": copy.deepcopy(current_entry["payload"]["curve"])}
    legacy_bytes = canonical_json_bytes(legacy_payload)
    legacy_version = rrd.PT0_GATE_CURVE_CODEC_VERSION - 1
    assert legacy_version == 3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ?, "
            "payload_codec_version = ? WHERE key_hash = ?",
            (
                sqlite3.Binary(legacy_bytes),
                hashlib.sha256(legacy_bytes).hexdigest(),
                legacy_version,
                key_hash,
            ),
        )

    recapture = PT0DeterminismStore("capture", db_path=db_path)
    recapture.capture_gate_curve(sim, fO2_log=fO2_log, curve=curve)

    assert recapture.capture_sequence[-1]["hash"] == key_hash
    expected_payload = recapture._entries[key_hash]["payload"]
    assert expected_payload["authority"]["vapor_pressure"] == (
        rrd._provider_identity(sim, ChemistryIntent.VAPOR_PRESSURE)
    )
    with sqlite3.connect(db_path) as conn:
        payload_bytes, codec_version = conn.execute(
            f"SELECT payload_bytes, payload_codec_version "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert bytes(payload_bytes) == canonical_json_bytes(expected_payload)
    assert codec_version == rrd.PT0_GATE_CURVE_CODEC_VERSION


def test_pt1_same_codec_gate_payload_collision_stays_corrupt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gate-authority-codec-collision.sqlite"
    capture, _sim, _fO2_log, _curve = _capture_pt0_gate_curve(
        db_path,
        solidus_T_C=1200.0,
    )
    key_hash = str(capture.capture_sequence[-1]["hash"])
    entry = capture._entries[key_hash]
    changed_payload = copy.deepcopy(entry["payload"])
    changed_payload["curve"]["solidus_T_C"] = 1190.0
    changed_payload["curve"]["path"][0]["temperature_C"] = 1190.0
    changed_bytes = canonical_json_bytes(changed_payload)

    with pytest.raises(PT1PersistentStoreCorrupt, match="payload collision"):
        PT1PersistentEquilibriumStore(db_path).put(
            artifact="freeze_gate_curve",
            key=entry["key"],
            key_bytes=entry["key_bytes"].encode("utf-8"),
            key_hash=key_hash,
            payload=changed_payload,
            payload_bytes=changed_bytes,
            payload_hash=hashlib.sha256(changed_bytes).hexdigest(),
            repair_notices_json=entry["repair_notices_json"],
            law_disclosures=entry["law_disclosures"],
        )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "row_version",
    (None, rrd.PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION - 1),
    ids=("missing", "old"),
)
def test_equilibrium_codec_row_version_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    row_version: int | None,
) -> None:
    db_path = tmp_path / "equilibrium-codec-stale.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    key = capture._equilibrium_key(sim)
    key_hash = _key_hash(key)
    entry = capture._entries[key_hash]
    current_payload = copy.deepcopy(entry["payload"])
    assert "payload_codec_version" not in current_payload
    assert entry["payload_codec_version"] == (
        rrd.PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION
    )
    entry["payload_codec_version"] = row_version
    if persistent:
        assert db_path is not None
        _set_pt1_payload_codec_version(db_path, key_hash, row_version)

    miss_store = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    with pytest.raises(PT0CacheMiss):
        miss_store._lookup("equilibrium_post_record", key, sim=sim)
    assert miss_store.hits == 0

    capture.capture_equilibrium(sim, result)
    entry = capture._entries[key_hash]
    assert entry["payload_codec_version"] == (
        rrd.PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION
    )
    assert entry["payload"] == current_payload
    if persistent:
        assert capture.persistent_store is not None
        persisted_entry = capture.persistent_store.get(
            artifact="equilibrium_post_record",
            key=key,
            key_bytes=canonical_json_bytes(key),
            key_hash=key_hash,
        )
        assert persisted_entry is not None
        assert persisted_entry["payload_codec_version"] == (
            rrd.PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION
        )
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else capture
    assert replay._lookup("equilibrium_post_record", key, sim=sim) == current_payload
    assert replay.hits == 1


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_unreadable_equilibrium_replay_side_row_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "unreadable-equilibrium.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    unreadable_payload = copy.deepcopy(current_payload)
    unreadable_payload["last_vapor_pressure_diagnostic"] = 7
    _store_unadmitted_test_payload(
        capture,
        "equilibrium_post_record",
        key,
        unreadable_payload,
        law_disclosures=[],
    )

    with pytest.raises(PT0CacheMiss):
        capture._lookup("equilibrium_post_record", key)
    assert capture.hits == 0

    capture._store(
        "equilibrium_post_record",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else capture
    assert replay._lookup("equilibrium_post_record", key) == current_payload


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    ("field", "bad_value"),
    (("vapor_pressures_Pa", 7), ("warnings", 7)),
)
def test_unreadable_equilibrium_result_field_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    field: str,
    bad_value: Any,
) -> None:
    db_path = tmp_path / "unreadable-equilibrium-result.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    unreadable_payload = copy.deepcopy(current_payload)
    unreadable_payload["equilibrium_result"][field] = bad_value
    _store_unadmitted_test_payload(
        capture,
        "equilibrium_post_record",
        key,
        unreadable_payload,
        law_disclosures=[],
    )

    with pytest.raises(PT0CacheMiss):
        capture._lookup("equilibrium_post_record", key)
    assert capture.hits == 0

    capture._store(
        "equilibrium_post_record",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = PT0DeterminismStore("replay", db_path=db_path) if persistent else capture
    assert replay._lookup("equilibrium_post_record", key) == current_payload


def test_equilibrium_replay_result_is_deep_copied_from_cached_payload() -> None:
    capture = PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    result.warnings = ["captured warning"]
    result.vapor_pressures_Pa = {"SiO": 2.5}
    key = capture._equilibrium_key(sim)
    payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    capture._store(
        "equilibrium_post_record",
        key,
        payload,
        law_disclosures=[],
    )
    entry = capture._entries[_key_hash(key)]
    stored_bytes = canonical_json_bytes(entry["payload"])

    first = capture._equilibrium_from_payload(
        sim,
        capture._lookup(
            "equilibrium_post_record",
            key,
            return_decoded=True,
        ),
    )
    first.warnings.append("caller mutation")
    first.vapor_pressures_Pa["SiO"] = 999.0

    assert canonical_json_bytes(entry["payload"]) == stored_bytes
    second = capture._equilibrium_from_payload(
        sim,
        capture._lookup(
            "equilibrium_post_record",
            key,
            return_decoded=True,
        ),
    )
    assert second.warnings == ["captured warning"]
    assert second.vapor_pressures_Pa == {"SiO": 2.5}
    assert canonical_json_bytes(entry["payload"]) == stored_bytes


def test_pt1_stale_replacement_publishes_fresh_measured_disclosure(
    tmp_path: Path,
) -> None:
    from simulator.chemistry.ellingham_graph import (
        effective_equilibrium_pressure_Pa,
    )
    from simulator.vapour_rail.run_disclosure import disclosure_scope

    db_path = tmp_path / "stale-disclosure-replacement.sqlite"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    stale_payload = copy.deepcopy(payload)
    stale_payload["last_vapor_pressure_diagnostic"] = 7
    _store_unadmitted_test_payload(
        capture,
        "equilibrium_post_record",
        key,
        stale_payload,
        law_disclosures=[],
    )

    with disclosure_scope(inherit=False) as measured:
        effective_equilibrium_pressure_Pa(
            "FeO_association_gas",
            1800.0,
            1.0e-9,
            vapor_pressure_data=_load_yaml("vapor_pressures.yaml"),
        )
        fresh_disclosures = measured.snapshot()
    assert fresh_disclosures

    capture._store(
        "equilibrium_post_record",
        key,
        payload,
        law_disclosures=fresh_disclosures,
    )
    replay = PT0DeterminismStore("replay", db_path=db_path)
    with disclosure_scope(inherit=False) as published:
        assert replay._lookup("equilibrium_post_record", key) == payload
        assert published.snapshot() == fresh_disclosures
    assert replay._accepted_hit_disclosures == list(fresh_disclosures)


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_in_band_legacy_step_down_without_source_marker_is_replaceable(
    tmp_path: Path,
    persistent: bool,
) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    db_path = tmp_path / "in-band-step-down-row.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._liquid_fraction_provider = "magemin"
    register_synthetic_gate_curve_producer(sim)
    composition_wt_pct = {
        "SiO2": 50.0,
        "MgO": 49.5,
        "MnO": 0.5,
    }
    composition_mol = {
        species: amount
        / resolve_species_formula(
            species,
            sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
        for species, amount in composition_wt_pct.items()
    }
    ledger = AtomLedger(registry=sim.atom_ledger.registry)
    ledger.load_external_mol(
        "process.cleaned_melt",
        composition_mol,
        source="r34 in-band legacy step-down regression",
        material_origin="feedstock",
    )
    sim.atom_ledger = ledger
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim.inventory.inert_melt_components_kg.clear()
    sim._project_cleaned_melt_from_atom_ledger()
    key = canonical_replay_key(
        sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=float(sim._current_melt_redox_fO2_log()),
        fe_redox_policy="intrinsic",
        provider_role="fallback",
    )
    legacy_payload = {
        "curve": {
            "source": "gate_liquid_fraction:fallback:magemin-shadow",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
            "gate_provider_step_down": {
                "failed_providers": [],
                "realized_provider": None,
            },
        },
    }
    current_payload = copy.deepcopy(legacy_payload)
    current_payload["curve"]["gate_provider_step_down"][
        "magemin_sourced"
    ] = True
    _store_unadmitted_test_payload(
        capture,
        "freeze_gate_curve",
        key,
        legacy_payload,
        law_disclosures=[],
    )

    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    with pytest.raises(PT0CacheMiss):
        replay._lookup("freeze_gate_curve", key)
    assert replay.hits == 0

    capture._store(
        "freeze_gate_curve",
        key,
        current_payload,
        law_disclosures=[],
    )
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    assert replay._lookup("freeze_gate_curve", key) == current_payload


def test_pt1_old_equilibrium_notice_row_misses_and_recapture_is_stable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / 'old-equilibrium-notice-row.db'
    capture = PT0DeterminismStore('capture', db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    notices = [{
        'kind': 'liquidus_unknown',
        'authority': 'unknown',
        'source': 'none:liquidus_gate_not_established',
    }]
    result.diagnostics = {
        'a_FeO_calphad': {
            'a_FeO_authoritative': 0.5,
            'notices': notices,
        },
    }
    new_key = capture._equilibrium_key(sim)
    assert 'equilibrium_payload_codec_version' not in new_key
    assert rrd.PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION == 2
    old_payload = rrd.canonical_replay_output_projection(
        'equilibrium_post_record', sim, result,
    )
    assert 'payload_codec_version' not in old_payload
    old_payload['equilibrium_result']['diagnostics'][
        'a_FeO_calphad'
    ]['notices'] = notices
    _store_raw_replay_payload(
        capture,
        'equilibrium_post_record',
        new_key,
        old_payload,
    )

    replay = PT0DeterminismStore('replay', db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        replay._lookup(
            'equilibrium_post_record',
            replay._equilibrium_key(sim),
            sim=sim,
        )
    assert replay.hits == 0

    capture.capture_equilibrium(sim, result)
    result.diagnostics['a_FeO_calphad']['notices'] = [{
        'kind': 'liquidus_unknown',
        'authority': 'unknown',
        'source': 'none:second-read-state',
    }]
    capture.capture_equilibrium(sim, result)
    assert len(capture._entries) == 1


def test_pt1_capture_equilibrium_rejects_internal_analytical_provider(
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

    with pytest.raises(RuntimeError, match="internal-analytical"):
        capture.capture_equilibrium(sim, ok_result)

    assert capture.summary()["entries"] == 0
    assert capture.summary()["capture_calls"] == 0
    assert capture.last_cache_state is None
    assert sim._last_reduced_real_cache_state is None
    assert _persistent_artifact_count(db_path, "equilibrium_post_record") == 0


@pytest.mark.parametrize(
    "provider_id",
    ["alphamelts-diagnostic", "unknown-real-engine-label"],
)
def test_internal_backend_cannot_be_relabelled_real_by_provider_text(
    provider_id: str,
) -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    provider = _CountingSilicateEquilibriumProvider(
        provider_id=provider_id
    )
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    key = store._equilibrium_key(sim)
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        phase_assemblage_available=False,
        status="ok",
    )
    payload = rrd.equilibrium_payload(sim, result)

    assert key["namespace_id"] == "internal-analytical:equilibrium-composite"
    assert payload["authority"]["evidence_class"] == "internal-analytical"
    relabelled = {**key, "namespace_id": "alphamelts:equilibrium-composite"}
    with pytest.raises(RuntimeError, match="row-validated real backend"):
        rrd.validate_reduced_real_equilibrium_record_key(
            "equilibrium_post_record", relabelled, payload
        )


@pytest.mark.parametrize(
    "stub_name",
    [
        "AlphaMELTSBackend",
        "ThermoEngineBackend",
        "MAGEMinBackend",
        "MAGEMinShadowProvider",
    ],
)
def test_same_name_stub_has_no_real_engine_authority(stub_name: str) -> None:
    stub_type = type(stub_name, (), {})

    assert rrd._typed_backend_family(stub_type()) == "internal-analytical"


def test_same_name_alphamelts_stub_cannot_pass_row_authority() -> None:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    stub_type = type("AlphaMELTSBackend", (), {})
    stub = stub_type()
    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic"
    )
    provider._backend = stub
    sim.backend = stub
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    key = store._equilibrium_key(sim)
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        phase_assemblage_available=False,
        status="ok",
    )
    payload = rrd.equilibrium_payload(sim, result)

    assert key["namespace_id"] == "internal-analytical:equilibrium-composite"
    assert payload["authority"]["evidence_class"] == "internal-analytical"
    relabelled = {**key, "namespace_id": "alphamelts:equilibrium-composite"}
    with pytest.raises(RuntimeError, match="row-validated real backend"):
        rrd.validate_reduced_real_equilibrium_record_key(
            "equilibrium_post_record", relabelled, payload
        )


def test_row_authority_strings_cannot_promote_stub_to_real_engine() -> None:
    key = {"namespace_id": "alphamelts:equilibrium-composite"}
    base = _alphamelts_record_authority()
    hostile_authorities = []

    fake_same_name = copy.deepcopy(base)
    fake_same_name["backend"] = {
        "backend_name": "AlphaMELTSBackend",
        "backend_class": "hostile.fake.AlphaMELTSBackend",
    }
    hostile_authorities.append(fake_same_name)

    arbitrary_real_labels = copy.deepcopy(base)
    arbitrary_real_labels["backend"] = {
        "backend_name": "definitely-real-engine",
        "backend_class": "hostile.fake.RealBackend",
    }
    hostile_authorities.append(arbitrary_real_labels)

    builtin_same_name = copy.deepcopy(fake_same_name)
    builtin_same_name["provider"]["resolved_provider_id"] = (
        "builtin-backend-equilibrium"
    )
    hostile_authorities.append(builtin_same_name)

    internal_spelled_real = copy.deepcopy(base)
    internal_spelled_real["backend"] = {
        "backend_name": "InternalAnalyticalBackend",
        "backend_class": (
            "simulator.melt_backend.internal_analytical."
            "InternalAnalyticalBackend"
        ),
    }
    hostile_authorities.append(internal_spelled_real)

    for authority in hostile_authorities:
        with pytest.raises(RuntimeError, match="row-validated real backend"):
            rrd.validate_reduced_real_equilibrium_record_key(
                "equilibrium_post_record", key, {"authority": authority}
            )

    for field in (
        "resolved_provider_id",
        "authoritative_provider_id",
        "fallback_provider_id",
    ):
        unknown_provider = copy.deepcopy(base)
        unknown_provider["provider"][field] = "unknown-real-engine-label"
        with pytest.raises(RuntimeError, match="validated alphamelts provider"):
            rrd.validate_reduced_real_equilibrium_record_key(
                "equilibrium_post_record",
                key,
                {"authority": unknown_provider},
            )

    wrong_family_provider = copy.deepcopy(base)
    wrong_family_provider["provider"]["authoritative_provider_id"] = (
        "magemin-shadow"
    )
    with pytest.raises(RuntimeError, match="validated alphamelts provider"):
        rrd.validate_reduced_real_equilibrium_record_key(
            "equilibrium_post_record",
            key,
            {"authority": wrong_family_provider},
        )

    wrong_role = copy.deepcopy(base)
    wrong_role["provider"]["resolved_role"] = "fallback"
    with pytest.raises(RuntimeError, match="validated alphamelts provider"):
        rrd.validate_reduced_real_equilibrium_record_key(
            "equilibrium_post_record", key, {"authority": wrong_role}
        )

    builtin_wrapped_real = copy.deepcopy(base)
    builtin_wrapped_real["provider"] = {
        "resolved_provider_id": "builtin-backend-equilibrium",
        "resolved_role": "authoritative",
        "authoritative_provider_id": "builtin-backend-equilibrium",
        "fallback_provider_id": None,
    }
    rrd.validate_reduced_real_equilibrium_record_key(
        "equilibrium_post_record",
        key,
        {"authority": builtin_wrapped_real},
    )


def test_typed_real_backend_produces_row_validated_authority() -> None:
    class TypedAlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

    sim = _build_pt0_sim(None)
    sim.backend = TypedAlphaMELTSBackend()
    sim._chem_registry.register(
        _CountingSilicateEquilibriumProvider(
            provider_id="alphamelts-diagnostic"
        ),
        [ChemistryIntent.SILICATE_EQUILIBRIUM],
    )
    key = PT0DeterminismStore("capture")._equilibrium_key(sim)
    payload = rrd.equilibrium_payload(
        sim,
        EquilibriumResult(
            temperature_C=sim.melt.temperature_C,
            pressure_bar=sim.melt.p_total_mbar / 1000.0,
            phase_assemblage_available=False,
            status="ok",
        ),
    )

    assert payload["authority"]["backend"] == {
        "backend_name": "AlphaMELTSBackend",
        "backend_class": (
            "simulator.melt_backend.alphamelts.AlphaMELTSBackend"
        ),
    }
    rrd.validate_reduced_real_equilibrium_record_key(
        "equilibrium_post_record", key, payload
    )


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

    seed_gate_curve_memo_record(sim, ok_curve)
    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=ok_curve)

    assert capture.summary()["entries"] == 1
    assert capture.summary()["capture_calls_by_artifact"] == {
        "freeze_gate_curve": 1,
    }
    assert capture.last_cache_state == "live_fill"
    assert sim._last_reduced_real_cache_state == "live_fill"
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 1


def test_gate_capture_rechecks_receipt_after_memo_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.engine_binding_admission import binding_receipt_path

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    receipt_path = binding_receipt_path()
    receipt_path.unlink()

    db_path = tmp_path / "pt0-gate-memo-no-receipt.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(sim)
    curve = {
        "source": "gate_liquid_fraction:fallback:synthetic-fake",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    provider_calls = []
    def successful_gate_curve_dispatch(
        reasons,
        *,
        fO2_log,
        repair_notices=None,
        redox_state=None,
    ):
        provider_calls.append(fO2_log)
        return _classified_gate_curve_for_test(sim, curve)

    sim._freeze_gate_curve_from_gate_dispatch = successful_gate_curve_dispatch
    store_calls = []
    persistent_put_calls = []
    original_store = capture._store
    original_put = capture.persistent_store.put

    def record_store(*args, **kwargs):
        store_calls.append((args, kwargs))
        return original_store(*args, **kwargs)

    def record_put(*args, **kwargs):
        persistent_put_calls.append((args, kwargs))
        return original_put(*args, **kwargs)

    monkeypatch.setattr(capture, "_store", record_store)
    monkeypatch.setattr(capture.persistent_store, "put", record_put)

    first = sim._freeze_gate_curve()
    second = sim._freeze_gate_curve()

    assert first == second
    assert len(provider_calls) == 1
    assert store_calls == []
    assert persistent_put_calls == []
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 0


def test_pt0_gate_capture_with_receipt_covers_fresh_curve_and_memo_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    db_path = tmp_path / "pt0-gate-memo-with-receipt.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    register_synthetic_gate_curve_producer(sim)
    curve = {
        "source": "gate_liquid_fraction:fallback:synthetic-fake",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    provider_calls = []
    def successful_gate_curve_dispatch(
        reasons,
        *,
        fO2_log,
        repair_notices=None,
        redox_state=None,
    ):
        provider_calls.append(fO2_log)
        return _classified_gate_curve_for_test(sim, curve)

    sim._freeze_gate_curve_from_gate_dispatch = successful_gate_curve_dispatch
    capture_calls = []
    original_capture = capture.capture_gate_curve

    def record_capture(*args, **kwargs):
        capture_calls.append((args, kwargs))
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(capture, "capture_gate_curve", record_capture)

    first = sim._freeze_gate_curve()
    second = sim._freeze_gate_curve()

    assert first == second
    assert len(provider_calls) == 1
    assert len(capture_calls) == 2
    assert _persistent_artifact_count(db_path, "freeze_gate_curve") == 1


def test_pt0_equilibrium_lookup_capture_and_replay_stay_receipt_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.engine_binding_admission import (
        EngineBindingAdmissionError,
        binding_receipt_path,
    )

    _select_synthetic_gate_curve_binding(tmp_path, monkeypatch)
    db_path = tmp_path / "pt0-other-artifacts-receipt-gated.db"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim = _build_pt0_sim(capture)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    curve = {
        "source": "gate_liquid_fraction:fallback:synthetic-fake",
        "status": "ok",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    seed_gate_curve_memo_record(sim, curve)
    capture.capture_gate_curve(
        sim,
        fO2_log=sim._compute_intrinsic_melt_fO2(),
        curve=curve,
    )
    replay = capture.clone_for_replay()
    assert replay.mode == "replay"
    assert replay.persistent_path == db_path
    assert replay.persistent_store is not capture.persistent_store
    assert replay._entries == capture._entries
    assert replay._entries is not capture._entries
    assert replay.capture_sequence == capture.capture_sequence
    assert replay.capture_sequence is not capture.capture_sequence

    receipt_path = binding_receipt_path()
    receipt_path.unlink()
    result = EquilibriumResult(
        liquid_fraction=0.5,
        vapor_pressures_Pa={"Fe": 1.0},
        status="ok",
    )
    equilibrium_lookups = []
    replay_lookups = []
    replay_gate_lookups = []
    monkeypatch.setattr(
        capture,
        "_lookup_optional",
        lambda *args, **kwargs: equilibrium_lookups.append((args, kwargs)),
    )
    original_lookup = replay._lookup
    original_gate_lookup = replay._lookup_first_available

    def record_replay_lookup(*args, **kwargs):
        replay_lookups.append((args, kwargs))
        return original_lookup(*args, **kwargs)

    def record_replay_gate_lookup(*args, **kwargs):
        replay_gate_lookups.append((args, kwargs))
        return original_gate_lookup(*args, **kwargs)

    monkeypatch.setattr(replay, "_lookup", record_replay_lookup)
    monkeypatch.setattr(
        replay,
        "_lookup_first_available",
        record_replay_gate_lookup,
    )
    store_calls = []
    original_store = capture._store

    def record_store(*args, **kwargs):
        store_calls.append((args, kwargs))
        return original_store(*args, **kwargs)

    monkeypatch.setattr(capture, "_store", record_store)

    assert capture.cached_equilibrium(sim) is None
    capture.capture_equilibrium(sim, result)
    assert store_calls == []
    assert equilibrium_lookups == []
    with pytest.raises(EngineBindingAdmissionError, match="receipt missing"):
        replay.replay_equilibrium(sim)
    with pytest.raises(EngineBindingAdmissionError, match="receipt missing"):
        replay.replay_gate_curve(
            sim,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
        )
    assert replay_lookups == []
    assert replay_gate_lookups == []


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
    curve = {
        "source": "unit-test",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }
    seed_gate_curve_memo_record(sim, curve)
    capture.capture_gate_curve(sim, fO2_log=fO2_log, curve=curve)
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
        "equilibrium_post_record",
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
        "equilibrium_post_record",
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
