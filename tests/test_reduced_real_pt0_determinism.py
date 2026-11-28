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
from typing import Any, Mapping, Sequence

import pytest
import yaml

from engines.alphamelts import AlphaMELTSProvider
from scripts.seed_reduced_real_cache import seed_cache
import simulator.reduced_real_determinism as rrd
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.capabilities import CapabilityProfile
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    ProviderAccountView,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.corpus_version import current_corpus_version
from simulator.fe_redox import (
    KRESS91_FO2_KEY_REFERENCE_T_K,
    FE_FEO_BUFFER_STANDARD,
    REDOX_STATE_REFERENCE_T_K,
    RedoxState,
    kress91_referenced_log_fO2,
)
from simulator.evaporation import EvaporationFluxRefusal
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
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    MBAR_PER_BAR,
)
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
    _lunar_composition_projected_diagnostic,
)
from tests.binding_admission_fixtures import (
    register_synthetic_gate_curve_producer,
    seed_gate_curve_memo_record,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _install_interpolation_test_binding_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.engine_binding_admission import BindingIdentity
    from tests.binding_admission_fixtures import install_synthetic_binding_receipt

    install_synthetic_binding_receipt(
        tmp_path,
        monkeypatch,
        bind_direct_backend=True,
        identity_override=BindingIdentity(
            "magemin",
            "ig",
            "magemin-ig-r1",
            "subprocess",
        ),
        provenance_override={
            "binding_provenance_verifiable": True,
            "test_fixture": (
                "binding_admission_synthetic/synthetic-fake.json"
            ),
            "runtime_version": {
                "tag": "value",
                "value": "magemin-deterministic-test-stub",
            },
        },
    )


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


def _capture_pt0_gate_curve(
    db_path: Path,
    *,
    solidus_T_C: float,
    law_disclosures: Any = (),
) -> tuple[PT0DeterminismStore, Any, float, dict[str, Any]]:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
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
    payload["authority"] = {
        "vapor_pressure": _alphamelts_record_authority()["vapor_pressure"]
    }
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


def _run_authoritative_alphamelts_equilibrium(
    store: PT0DeterminismStore | None,
    provider: _CountingSilicateEquilibriumProvider,
    *,
    allow_internal_analytical_fallback: bool = True,
):
    sim = _build_pt0_sim(store)
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
) -> tuple[Any, EquilibriumResult]:
    class AlphaMELTSBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.ALPHAMELTS

        def equilibrate(self, *, composition_mol_by_account=None):
            raise AssertionError(
                "the registered equilibrium fixture provider must handle the solve"
            )

        def get_engine_version(self) -> str:
            return "non-stub-test"

    provider = _CountingSilicateEquilibriumProvider(
        provider_id="alphamelts-diagnostic",
        engine_version="alpha-v1",
    )
    sim = _build_pt0_sim(store)
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
    result = EquilibriumResult(
        status="ok",
        temperature_C=1200.0,
        pressure_bar=0.001,
        fO2_log=-9.0,
        liquid_fraction=0.5,
        phase_assemblage_available=True,
        liquidus_T_C=1300.0,
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        warnings=[],
    )
    result_payload = {
        field.name: rrd._json_ready(getattr(result, field.name))
        for field in rrd._equilibrium_result_payload_fields()
        if not (
            field.name in rrd._EQUILIBRIUM_RESULT_FIELDS_OMITTED_WHEN_NONE
            and getattr(result, field.name) is None
        )
    }
    payload = {
        "authority": _alphamelts_record_authority(),
        "equilibrium_result": result_payload,
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
        "provider": rrd._provider_authority_payload(
            resolved_provider_id="alphamelts-diagnostic",
            resolved_role="authoritative",
            authoritative_provider_id="alphamelts-diagnostic",
            fallback_provider_id=None,
            fallback_allowed=False,
            model="MELTSv1.0.2",
            mode="subprocess",
        ),
        "vapor_pressure": rrd._provider_authority_payload(
            resolved_provider_id="builtin-vapor-pressure",
            resolved_role="authoritative",
            authoritative_provider_id="builtin-vapor-pressure",
            fallback_provider_id=None,
            fallback_allowed=False,
            model="BuiltinVaporPressureProvider",
            mode="BuiltinVaporPressureProvider",
        ),
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

    runtime_record = seed_gate_curve_memo_record(sim, curve)
    assert set(runtime_record) == {
        "curve",
        "origin",
        "repair_notices",
        "disposition",
        "provider_outcome",
        # Measured empty. The fixture records that no flagged law ran.
        # The payload bytes below stay the curve alone.
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
    assert row[2]
    expected_payload = {
        "curve": {
            "source": "gate_liquid_fraction:fallback:magemin-shadow",
            "solidus_T_C": 1210.0,
            "liquidus_T_C": 1320.0,
            "is_liquidus_floor": False,
            "path": [
                {"temperature_C": 1210.0, "liquid_fraction": 0.0},
                {"temperature_C": 1320.0, "liquid_fraction": 1.0},
            ],
        },
    }
    expected_bytes = canonical_json_bytes(expected_payload)
    assert row[3] == expected_bytes
    assert len(row[3]) == 239
    assert hashlib.sha256(row[3]).hexdigest() == (
        "0f8c8858ed0f6ccdd103f7c0141a9666f2adff2b47f69e7797d08e8dcf2fe538"
    )
    payload = json.loads(row[3])
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

    assert summary["seed_rows"] == 1
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
        (shard_codec, shard_disclosures) = conn.execute(
            f"SELECT payload_codec_version, law_disclosures_json "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert hashlib.sha256(base_db.read_bytes()).hexdigest() == base_sha256
    assert (shard_codec, shard_disclosures) == (None, None)

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
) -> None:
    key_bytes = rrd.canonical_json_bytes(key)
    payload_bytes = rrd._payload_integrity_bytes(payload)
    store.persistent_store.put(
        artifact=artifact,
        key=key,
        key_bytes=key_bytes,
        key_hash=rrd._sha256(key_bytes),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=rrd._sha256(payload_bytes),
        law_disclosures=law_disclosures,
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
        try:
            store._store(
                artifact,
                key,
                payload,
                law_disclosures=law_disclosures,
            )
        except rrd.PT0NonFinitePayload:
            finite_payload = _replace_nonfinite_test_values(payload)
            store._store(
                artifact,
                key,
                finite_payload,
                law_disclosures=law_disclosures,
            )
            store._entries[_key_hash(key)]["payload"] = copy.deepcopy(payload)
            store._entries[_key_hash(key)]["payload_hash"] = rrd._sha256(
                rrd._payload_integrity_bytes(payload)
            )
    else:
        _store_raw_replay_payload(
            store,
            artifact,
            key,
            payload,
            law_disclosures=law_disclosures,
        )


def _replace_nonfinite_test_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _replace_nonfinite_test_values(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_replace_nonfinite_test_values(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return 0.0
    return value


def _assert_corrupt_cache_row_misses_and_replaces(
    capture: PT0DeterminismStore,
    *,
    artifact: str,
    key: dict,
    corrupt_payload: dict,
    current_payload: dict,
    persistent: bool,
    db_path: Path | None,
    monkeypatch: pytest.MonkeyPatch,
    sim: Any = None,
) -> None:
    from simulator.vapour_rail import run_disclosure

    disclosure_calls: list[None] = []
    original_accept = run_disclosure.accept_stored_memo

    def record_disclosure(*args: Any, **kwargs: Any) -> Any:
        disclosure_calls.append(None)
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(run_disclosure, "accept_stored_memo", record_disclosure)
    _store_unadmitted_test_payload(
        capture,
        artifact,
        key,
        corrupt_payload,
        law_disclosures=[],
    )
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    with pytest.raises(PT0CacheMiss):
        replay._lookup(artifact, key, sim=sim)
    assert replay.hits == 0
    assert disclosure_calls == []

    capture._store(artifact, key, current_payload, law_disclosures=[])
    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    assert replay._lookup(artifact, key, sim=sim) == current_payload
    assert replay.hits == 1


def _valid_test_gate_payload() -> dict[str, Any]:
    return {
        "curve": rrd._curve_payload(
            {
                "source": "gate_liquid_fraction",
                "solidus_T_C": 1200.0,
                "liquidus_T_C": 1300.0,
                "path": (
                    (1200.0, 0.0),
                    (1300.0, 1.0),
                ),
                "is_liquidus_floor": False,
            }
        ),
    }


def test_pt0_gate_curve_codec_misses_floor_rows_without_magemin_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / 'markerless-gate-curve.db'
    store = PT0DeterminismStore('capture', db_path=db_path)
    key = _freeze_gate_key()
    assert 'gate_curve_codec_version' not in key
    assert rrd.PT0_GATE_CURVE_CODEC_VERSION == 3
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
    )
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


def test_extrapolated_gate_admission_uses_query_identity_at_each_reader(
    tmp_path: Path,
) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.ledger import AtomLedger

    sim = _build_pt0_sim(None)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._liquid_fraction_provider = "magemin"
    register_synthetic_gate_curve_producer(sim)
    composition_wt_pct = {"SiO2": 80.001, "MgO": 10.0, "FeO": 9.999}
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
        source="d447 PT-1 row-admission request key",
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

    def payload_for(silica_bounds: list[float]) -> dict[str, Any]:
        return {
            "curve": {
                "source": "gate_liquid_fraction:fallback:magemin-shadow",
                "solidus_T_C": 1200.0,
                "liquidus_T_C": 1300.0,
                "path": [
                    {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                    {"temperature_C": 1300.0, "liquid_fraction": 1.0},
                ],
                "is_liquidus_floor": False,
                "authority": "extrapolated",
                "certified_band": {"sio2_wt_pct": silica_bounds},
            }
        }

    def row_for(
        payload: Mapping[str, Any],
        *,
        replay_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        key_bytes = canonical_json_bytes(
            key if replay_key is None else replay_key
        )
        payload_bytes = canonical_json_bytes(payload)
        key_hash = rrd._sha256(key_bytes)
        return {
            "artifact": "freeze_gate_curve",
            "key_hash": key_hash,
            "key_sha256": key_hash,
            "key_bytes": key_bytes,
            "payload_bytes": payload_bytes,
            "payload_sha256": rrd._sha256(payload_bytes),
            "payload_codec_version": rrd.PT0_GATE_CURVE_CODEC_VERSION,
        }

    stale_payload = payload_for([0.0, 100.0])
    valid_payload = payload_for([30.0, 80.0])
    assert not rrd._pt1_row_admission(row_for(stale_payload))
    assert rrd._pt1_row_admission(row_for(valid_payload))
    reversed_band_key = copy.deepcopy(rrd._json_ready(key))
    reversed_band_key["freeze_gate_cache_key"][7]["certified_band"] = [
        80.0,
        30.0,
    ]
    reversed_identity = rrd._magemin_identity_from_replay_key(reversed_band_key)
    assert reversed_identity is not None
    assert reversed_identity.silica_verdict == "extrapolated"
    assert reversed_identity.certified_band == (80.0, 30.0)
    assert not rrd._pt1_row_admission(
        row_for(
            payload_for([80.0, 30.0]),
            replay_key=reversed_band_key,
        )
    )

    store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "d447-query-identity-readers.sqlite",
    )
    store._store("freeze_gate_curve", key, valid_payload, law_disclosures=[])
    wrong_query_key = copy.deepcopy(rrd._json_ready(key))
    identity_payload = wrong_query_key["freeze_gate_cache_key"][7]
    identity_payload["certified_band"] = [31.0, 80.0]
    wrong_identity = rrd._magemin_identity_from_replay_key(wrong_query_key)
    assert wrong_identity is not None
    assert wrong_identity.silica_verdict == "extrapolated"
    assert wrong_identity.certified_band == (31.0, 80.0)
    bucket_key = canonical_physics_bucket_key_from_replay_key(key)
    assert canonical_physics_bucket_key_from_replay_key(
        wrong_query_key
    ) == bucket_key
    bucket_bytes = canonical_json_bytes(bucket_key)
    bucket_hash = rrd._sha256(bucket_bytes)

    assert store._entry_for_physics_bucket(
        "freeze_gate_curve",
        bucket_key,
        query_key=wrong_query_key,
    ) is None
    persistent = store.persistent_store
    assert persistent is not None
    assert persistent._get_by_physics_bucket(
        artifact="freeze_gate_curve",
        physics_bucket_key=bucket_key,
        physics_bucket_bytes=bucket_bytes,
        physics_bucket_hash=bucket_hash,
        query_key=wrong_query_key,
    ) is None
    rung_tag = rrd.PHYSICS_BUCKET_LADDER_RUNGS[0][0]
    rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        key,
        rung_tag,
    )
    assert rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        wrong_query_key,
        rung_tag,
    ) == rung_key
    rung_hash = rrd._sha256(canonical_json_bytes(rung_key))
    assert persistent._get_by_physics_ladder_bucket(
        artifact="freeze_gate_curve",
        rung_tag=rung_tag,
        rung_key=rung_key,
        rung_hash=rung_hash,
        query_key=wrong_query_key,
    ) is None
    assert persistent._list_interpolation_candidates(
        artifact="freeze_gate_curve",
        replay_scope_sha256=rrd._replay_scope_hash(bucket_key),
        query_key=wrong_query_key,
    ) == []

    # The inverse mismatch also matters: an extrapolated row can be valid
    # for its stored identity and still be incompatible with an in-band query.
    stored_wrong_band_key = copy.deepcopy(rrd._json_ready(key))
    stored_identity_payload = stored_wrong_band_key[
        "freeze_gate_cache_key"
    ][7]
    stored_identity_payload["certified_band"] = [0.0, 100.0]
    in_band_query_key = copy.deepcopy(rrd._json_ready(key))
    in_band_identity_payload = in_band_query_key[
        "freeze_gate_cache_key"
    ][7]
    in_band_identity_payload["silica_verdict"] = "in_band"
    in_band_identity_payload["certified_band"] = None
    stored_identity = rrd._magemin_identity_from_replay_key(
        stored_wrong_band_key
    )
    in_band_identity = rrd._magemin_identity_from_replay_key(
        in_band_query_key
    )
    assert stored_identity is not None
    assert stored_identity.silica_verdict == "extrapolated"
    assert stored_identity.certified_band == (0.0, 100.0)
    assert in_band_identity is not None
    assert in_band_identity.silica_verdict == "in_band"
    assert in_band_identity.certified_band is None

    wrong_band_store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "d447-stored-identity-readers.sqlite",
    )
    wrong_band_store._store(
        "freeze_gate_curve",
        stored_wrong_band_key,
        payload_for([0.0, 100.0]),
        law_disclosures=[],
    )
    stored_bucket_key = canonical_physics_bucket_key_from_replay_key(
        stored_wrong_band_key
    )
    in_band_bucket_key = canonical_physics_bucket_key_from_replay_key(
        in_band_query_key
    )
    assert stored_bucket_key == in_band_bucket_key
    stored_bucket_bytes = canonical_json_bytes(stored_bucket_key)
    stored_bucket_hash = rrd._sha256(stored_bucket_bytes)
    assert wrong_band_store._entry_for_physics_bucket(
        "freeze_gate_curve",
        stored_bucket_key,
        query_key=in_band_query_key,
    ) is None
    wrong_band_persistent = wrong_band_store.persistent_store
    assert wrong_band_persistent is not None
    assert wrong_band_persistent._get_by_physics_bucket(
        artifact="freeze_gate_curve",
        physics_bucket_key=in_band_bucket_key,
        physics_bucket_bytes=stored_bucket_bytes,
        physics_bucket_hash=stored_bucket_hash,
        query_key=in_band_query_key,
    ) is None
    stored_rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        stored_wrong_band_key,
        rung_tag,
    )
    in_band_rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        in_band_query_key,
        rung_tag,
    )
    assert stored_rung_key == in_band_rung_key
    stored_rung_hash = rrd._sha256(canonical_json_bytes(stored_rung_key))
    assert wrong_band_store._entry_for_physics_ladder_bucket(
        "freeze_gate_curve",
        rung_tag,
        in_band_rung_key,
        query_key=in_band_query_key,
    ) is None
    assert wrong_band_persistent._get_by_physics_ladder_bucket(
        artifact="freeze_gate_curve",
        rung_tag=rung_tag,
        rung_key=in_band_rung_key,
        rung_hash=stored_rung_hash,
        query_key=in_band_query_key,
    ) is None
    assert wrong_band_persistent._list_interpolation_candidates(
        artifact="freeze_gate_curve",
        replay_scope_sha256=rrd._replay_scope_hash(in_band_bucket_key),
        query_key=in_band_query_key,
    ) == []


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
    payload = {"curve": rrd._curve_payload(live_curve)}
    key_hash = rrd._sha256(rrd.canonical_json_bytes(key))
    captured_entry = live_store._entries[key_hash]
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "unreadable-gate-row.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = _valid_test_gate_payload()
    unreadable_payload = copy.deepcopy(current_payload)
    unreadable_payload["curve"].pop("liquidus_T_C")
    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="freeze_gate_curve",
        key=key,
        corrupt_payload=unreadable_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_empty_gate_curve_path_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "empty-gate-path.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = _valid_test_gate_payload()
    empty_path_payload = copy.deepcopy(current_payload)
    empty_path_payload["curve"]["path"] = []
    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="freeze_gate_curve",
        key=key,
        corrupt_payload=empty_path_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_omitted_gate_curve_path_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "omitted-gate-path.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = _valid_test_gate_payload()
    omitted_path_payload = copy.deepcopy(current_payload)
    omitted_path_payload["curve"].pop("path")

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="freeze_gate_curve",
        key=key,
        corrupt_payload=omitted_path_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_fixture_style_gate_row_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "fixture-style-gate-row.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    current_payload = _valid_test_gate_payload()
    fixture_payload = {
        "curve": {
            "status": "ok",
            "tag": "fixture-only",
            "is_liquidus_floor": False,
        },
    }

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="freeze_gate_curve",
        key=key,
        corrupt_payload=fixture_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
    )


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
def test_current_equilibrium_capture_hits_after_admission(
    tmp_path: Path,
    persistent: bool,
) -> None:
    db_path = tmp_path / "current-equilibrium-hit.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    capture.capture_equilibrium(sim, result)
    key = capture._equilibrium_key(sim)
    current_payload = capture._entries[_key_hash(key)]["payload"]

    replay = (
        PT0DeterminismStore("replay", db_path=db_path)
        if persistent
        else capture
    )
    assert replay._lookup("equilibrium_post_record", key, sim=sim) == current_payload
    assert replay.hits == 1


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_unreadable_equilibrium_replay_side_row_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
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
    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=unreadable_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "bad_value",
    (7, None, {"SiO": 7}),
    ids=("scalar", "null", "non-mapping-species-value"),
)
def test_nested_equilibrium_vapor_provenance_must_be_a_mapping(
    tmp_path: Path,
    persistent: bool,
    bad_value: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "nested-equilibrium-provenance.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    corrupt_payload = copy.deepcopy(current_payload)
    corrupt_payload["last_vapor_pressure_diagnostic"][
        "vapor_pressure_numerator_provenance"
    ] = bad_value

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=corrupt_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("vapor_pressures_Pa", 7),
        ("vapor_pressures_Pa", {"SiO": float("nan")}),
        ("vapor_pressures_Pa", {"SiO": float("inf")}),
        ("warnings", 7),
        ("warnings", ["valid warning", 7]),
    ),
)
def test_unreadable_equilibrium_result_field_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    field: str,
    bad_value: Any,
    monkeypatch: pytest.MonkeyPatch,
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
    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=unreadable_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "field",
    ("vapor_pressures_Pa", "vapor_pressures_source", "warnings"),
)
def test_missing_equilibrium_replay_field_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "missing-equilibrium-field.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    corrupt_payload = copy.deepcopy(current_payload)
    corrupt_payload["equilibrium_result"].pop(field)

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=corrupt_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "field_path",
    (
        ("equilibrium_result", "temperature_C"),
        ("equilibrium_result", "pressure_bar"),
        ("equilibrium_result", "fO2_log"),
        ("equilibrium_result", "status"),
        ("last_vapor_pressures_source",),
        ("last_vapor_pressure_diagnostic",),
    ),
    ids=(
        "temperature_C",
        "pressure_bar",
        "fO2_log",
        "status",
        "last_vapor_pressures_source",
        "last_vapor_pressure_diagnostic",
    ),
)
def test_missing_encoder_equilibrium_field_misses_and_is_replaceable(
    tmp_path: Path,
    persistent: bool,
    field_path: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "missing-encoder-equilibrium-field.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    corrupt_payload = copy.deepcopy(current_payload)
    parent = corrupt_payload
    for path_part in field_path[:-1]:
        parent = parent[path_part]
    parent.pop(field_path[-1])

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=corrupt_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_equilibrium_vapor_provenance_rejects_wrong_consumer_field_types(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "bad-vapor-provenance-type.sqlite" if persistent else None
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    corrupt_payload = copy.deepcopy(current_payload)
    corrupt_payload["last_vapor_pressure_diagnostic"][
        "vapor_pressure_numerator_provenance"
    ] = {"Fe": {"pressure_rail": ["condensed_raoult_psat"]}}

    _assert_corrupt_cache_row_misses_and_replaces(
        capture,
        artifact="equilibrium_post_record",
        key=key,
        corrupt_payload=corrupt_payload,
        current_payload=current_payload,
        persistent=persistent,
        db_path=db_path,
        monkeypatch=monkeypatch,
        sim=sim,
    )


def test_pt1_non_mapping_payload_misses_and_is_replaceable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.vapour_rail import run_disclosure

    db_path = tmp_path / "pt1-non-mapping-payload.sqlite"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    sim, result = _pt0_equilibrium_capture_context(capture)
    key = capture._equilibrium_key(sim)
    current_payload = rrd.canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )
    capture._store("equilibrium_post_record", key, current_payload, law_disclosures=[])
    key_hash = _key_hash(key)
    corrupt_bytes = b"[7]"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ? WHERE key_hash = ?",
            (corrupt_bytes, rrd._sha256(corrupt_bytes), key_hash),
        )

    disclosure_calls: list[None] = []
    original_accept = run_disclosure.accept_stored_memo

    def record_disclosure(*args: Any, **kwargs: Any) -> Any:
        disclosure_calls.append(None)
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(run_disclosure, "accept_stored_memo", record_disclosure)
    replay = PT0DeterminismStore("replay", db_path=db_path)
    with pytest.raises(PT0CacheMiss):
        replay._lookup("equilibrium_post_record", key, sim=sim)
    assert replay.hits == 0
    assert disclosure_calls == []

    capture._store("equilibrium_post_record", key, current_payload, law_disclosures=[])
    replay = PT0DeterminismStore("replay", db_path=db_path)
    assert replay._lookup("equilibrium_post_record", key, sim=sim) == current_payload
    assert replay.hits == 1


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


@pytest.mark.parametrize(
    ("index_kind", "distance_corruption"),
    (
        ("bucket", None),
        ("ladder", "missing"),
        ("ladder", "incorrect"),
    ),
    ids=("bucket-key-bytes", "ladder-distance-missing", "ladder-distance-wrong"),
)
def test_pt1_bucket_and_ladder_integrity_precedes_stale_codec_admission(
    tmp_path: Path,
    index_kind: str,
    distance_corruption: str | None,
) -> None:
    db_path = tmp_path / f"pt1-{index_kind}-{distance_corruption}.sqlite"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    key = _freeze_gate_key()
    payload = _valid_test_gate_payload()
    capture._store("freeze_gate_curve", key, payload, law_disclosures=[])

    bucket_key = canonical_physics_bucket_key_from_replay_key(key)
    bucket_bytes = canonical_json_bytes(bucket_key)
    bucket_hash = rrd._sha256(bucket_bytes)
    rung_tag = rrd.PHYSICS_BUCKET_LADDER_RUNGS[0][0]
    rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        key,
        rung_tag,
    )
    rung_hash = rrd._sha256(canonical_json_bytes(rung_key))
    key_hash = _key_hash(key)

    with sqlite3.connect(db_path) as conn:
        if index_kind == "bucket":
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                "SET payload_codec_version = ?, physics_key_bytes = ? "
                "WHERE key_hash = ?",
                (0, b"{}", key_hash),
            )
        else:
            distance_column = rrd._physics_ladder_distance_column(rung_tag)
            if distance_corruption == "missing":
                corrupted_distance = None
            else:
                corrupted_distance = (
                    rrd.physics_ladder_bucket_distance_from_replay_key(
                        key,
                        rung_tag,
                    )
                    + 0.01
                )
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                f"SET payload_codec_version = ?, {distance_column} = ? "
                "WHERE key_hash = ?",
                (0, corrupted_distance, key_hash),
            )

    persistent = PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(PT1PersistentStoreCorrupt):
        if index_kind == "bucket":
            persistent._get_by_physics_bucket(
                artifact="freeze_gate_curve",
                physics_bucket_key=bucket_key,
                physics_bucket_bytes=bucket_bytes,
                physics_bucket_hash=bucket_hash,
                query_key=key,
            )
        else:
            persistent._get_by_physics_ladder_bucket(
                artifact="freeze_gate_curve",
                rung_tag=rung_tag,
                rung_key=rung_key,
                rung_hash=rung_hash,
                query_key=key,
            )


@pytest.mark.parametrize(
    ("reader", "corruption", "error_type"),
    (
        ("pt0_exact", "artifact", rrd.PT0CacheCollision),
        ("pt0_exact", "key_hash", rrd.PT0CacheCollision),
        ("pt0_exact", "key_bytes", rrd.PT0CacheCollision),
        ("pt0_bucket", "key_bytes", rrd.PT0CacheCollision),
        ("pt0_verify", "payload_checksum", rrd.PT0CacheCollision),
        ("pt0_replace", "artifact", rrd.PT0CacheCollision),
        ("pt1_exact", "artifact", PT1PersistentStoreCorrupt),
        ("pt1_exact", "key_hash", PT1PersistentStoreCorrupt),
        ("pt1_exact", "key_bytes", PT1PersistentStoreCorrupt),
        ("pt1_exact", "payload_checksum", PT1PersistentStoreCorrupt),
        ("pt1_bucket", "physics_key_bytes", PT1PersistentStoreCorrupt),
        ("pt0_ladder", "ladder_distance", PT1PersistentStoreCorrupt),
        ("pt1_ladder", "ladder_distance", PT1PersistentStoreCorrupt),
        ("pt0_interpolation", "replay_scope", PT1PersistentStoreCorrupt),
        (
            "pt1_interpolation_candidates",
            "replay_scope",
            PT1PersistentStoreCorrupt,
        ),
        ("pt1_row_decode", "payload_checksum", PT1PersistentStoreCorrupt),
        ("pt1_row_decode", "malformed_key_json", PT1PersistentStoreCorrupt),
        ("pt1_exact", "malformed_payload_json", PT1PersistentStoreCorrupt),
        ("pt1_exact", "noncanonical_payload_bytes", PT1PersistentStoreCorrupt),
        ("pt1_replace", "malformed_payload_json", PT1PersistentStoreCorrupt),
        ("pt1_replace", "key_hash", PT1PersistentStoreCorrupt),
        (
            "pt1_bucket",
            "compound_key_json_physics_key_bytes",
            PT1PersistentStoreCorrupt,
        ),
        (
            "pt1_ladder",
            "compound_key_json_ladder_distance",
            PT1PersistentStoreCorrupt,
        ),
        (
            "pt1_interpolation_candidates",
            "compound_replay_scope_malformed_payload_json",
            PT1PersistentStoreCorrupt,
        ),
    ),
    ids=(
        "pt0-exact-artifact",
        "pt0-exact-key-hash",
        "pt0-exact-key-bytes",
        "pt0-bucket-key-bytes",
        "pt0-verify-payload-checksum",
        "pt0-replace-artifact",
        "pt1-exact-artifact",
        "pt1-exact-key-hash",
        "pt1-exact-key-bytes",
        "pt1-exact-payload-checksum",
        "pt1-bucket-physics-key-bytes",
        "pt0-ladder-distance",
        "pt1-ladder-distance",
        "pt0-interpolation-replay-scope",
        "pt1-interpolation-replay-scope",
        "pt1-row-decode-payload-checksum",
        "pt1-row-decode-malformed-key-json",
        "pt1-exact-malformed-payload-json",
        "pt1-exact-noncanonical-payload-bytes",
        "pt1-replace-malformed-payload-json",
        "pt1-replace-key-hash",
        "pt1-bucket-key-json-before-physics-index",
        "pt1-ladder-key-json-before-distance",
        "pt1-interpolation-scope-before-payload-json",
    ),
)
def test_stale_codec_integrity_checks_precede_admission_for_every_reader(
    tmp_path: Path,
    reader: str,
    corruption: str,
    error_type: type[Exception],
) -> None:
    interpolation_reader = "interpolation" in reader
    persistent_reader = reader.startswith("pt1_") or reader in {
        "pt0_ladder",
        "pt0_interpolation",
    }
    db_path = tmp_path / f"{reader}-{corruption}.sqlite" if persistent_reader else None
    if interpolation_reader:
        assert db_path is not None
        artifact = "equilibrium_post_record"
        key = _c3a_ladder_key(
            "stored",
            feo_fraction=0.20,
            temperature_K=1490.0,
        )
        key["model"]["model"] = "other-binding"
        query_key = _c3a_ladder_key(
            "query",
            feo_fraction=0.20,
            temperature_K=1500.0,
        )
        _put_c3a_payload(db_path, key, "stored")
        capture = PT0DeterminismStore("replay", db_path=db_path)
        payload = None
    else:
        artifact = "freeze_gate_curve"
        key = _freeze_gate_key()
        query_key = key
        payload = _valid_test_gate_payload()
        capture = PT0DeterminismStore("capture", db_path=db_path)
        capture._store(artifact, key, payload, law_disclosures=[])

    key_hash = _key_hash(key)
    entry = capture._entries.get(key_hash)
    if entry is not None:
        entry["payload_codec_version"] = 0
    if persistent_reader:
        assert db_path is not None
        _set_pt1_payload_codec_version(db_path, key_hash, 0)

    rung_tag = rrd.PHYSICS_BUCKET_LADDER_RUNGS[0][0]
    if persistent_reader:
        assert db_path is not None
        if corruption == "malformed_key_json":
            malformed_key = sqlite3.Binary(b"{")
            corrupted_key_hash = rrd._sha256(bytes(malformed_key))
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    "SET key_bytes = ?, key_hash = ?, key_sha256 = ? "
                    "WHERE key_hash = ?",
                    (
                        malformed_key,
                        corrupted_key_hash,
                        corrupted_key_hash,
                        key_hash,
                    ),
                )
            key_hash = corrupted_key_hash
        elif corruption == "malformed_payload_json":
            malformed_payload = sqlite3.Binary(b"{")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    "SET payload_bytes = ?, payload_sha256 = ? WHERE key_hash = ?",
                    (
                        malformed_payload,
                        rrd._sha256(bytes(malformed_payload)),
                        key_hash,
                    ),
                )
        elif corruption == "noncanonical_payload_bytes":
            with sqlite3.connect(db_path) as conn:
                payload_bytes = bytes(
                    conn.execute(
                        f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE} "
                        "WHERE key_hash = ?",
                        (key_hash,),
                    ).fetchone()[0]
                )
                noncanonical_payload = sqlite3.Binary(b" " + payload_bytes)
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    "SET payload_bytes = ?, payload_sha256 = ? WHERE key_hash = ?",
                    (
                        noncanonical_payload,
                        rrd._sha256(bytes(noncanonical_payload)),
                        key_hash,
                    ),
                )
        elif corruption == "compound_key_json_physics_key_bytes":
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    "SET key_bytes = ?, physics_key_bytes = ? WHERE key_hash = ?",
                    (sqlite3.Binary(b"{"), sqlite3.Binary(b"{}"), key_hash),
                )
        elif corruption == "compound_key_json_ladder_distance":
            distance_column = rrd._physics_ladder_distance_column(rung_tag)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    f"SET key_bytes = ?, {distance_column} = NULL "
                    "WHERE key_hash = ?",
                    (sqlite3.Binary(b"{"), key_hash),
                )
        elif corruption == "compound_replay_scope_malformed_payload_json":
            malformed_payload = sqlite3.Binary(b"{")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                    "SET replay_scope_sha256 = ?, payload_bytes = ?, "
                    "payload_sha256 = ? WHERE key_hash = ?",
                    (
                        rrd._replay_scope_hash(
                            canonical_physics_bucket_key_from_replay_key(query_key)
                        ),
                        malformed_payload,
                        rrd._sha256(bytes(malformed_payload)),
                        key_hash,
                    ),
                )
        elif corruption in {"replay_scope", "ladder_distance"}:
            bad_value = (
                rrd._replay_scope_hash(
                    canonical_physics_bucket_key_from_replay_key(query_key)
                )
                if corruption == "replay_scope"
                else None
            )
            column = (
                "replay_scope_sha256"
                if corruption == "replay_scope"
                else rrd._physics_ladder_distance_column(rung_tag)
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET {column} = ? "
                    "WHERE key_hash = ?",
                    (bad_value, key_hash),
                )
        else:
            bad_value = {
                "artifact": "corrupt-artifact",
                "key_hash": "0" * 64,
                "key_bytes": sqlite3.Binary(b"{}"),
                "payload_checksum": "0" * 64,
                "physics_key_bytes": sqlite3.Binary(b"{}"),
            }[corruption]
            column = {
                "artifact": "artifact",
                "key_hash": "key_sha256",
                "key_bytes": "key_bytes",
                "payload_checksum": "payload_sha256",
                "physics_key_bytes": "physics_key_bytes",
                "ladder_distance": rrd._physics_ladder_distance_column(rung_tag),
                "replay_scope": "replay_scope_sha256",
            }[corruption]
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET {column} = ? "
                    "WHERE key_hash = ?",
                    (bad_value, key_hash),
                )
    else:
        assert entry is not None
        entry_field, bad_value = {
            "artifact": ("artifact", "corrupt-artifact"),
            "key_hash": ("key_hash", "0" * 64),
            "key_bytes": ("key_bytes", "{}"),
            "payload_checksum": ("payload_hash", "0" * 64),
        }[corruption]
        entry[entry_field] = bad_value

    with pytest.raises(error_type) as raised:
        if reader == "pt0_exact":
            capture._lookup(artifact, key)
        elif reader == "pt0_bucket":
            capture._entry_for_physics_bucket(
                artifact,
                canonical_physics_bucket_key_from_replay_key(key),
            )
        elif reader == "pt0_verify":
            assert entry is not None
            capture._verify_entry(
                artifact,
                key,
                canonical_json_bytes(key),
                key_hash,
                entry,
            )
        elif reader == "pt0_replace":
            assert payload is not None
            capture._store(artifact, key, payload, law_disclosures=[])
        elif reader == "pt0_ladder":
            rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
                key,
                rung_tag,
            )
            capture._entry_for_physics_ladder_bucket(
                artifact,
                rung_tag,
                rung_key,
                query_key=query_key,
            )
        elif reader == "pt0_interpolation":
            capture._lookup_interpolated(artifact, query_key)
        else:
            assert capture.persistent_store is not None
            persistent = capture.persistent_store
            if reader == "pt1_exact":
                persistent._get_with_decoded(
                    artifact=artifact,
                    key=key,
                    key_bytes=canonical_json_bytes(key),
                    key_hash=key_hash,
                )
            elif reader == "pt1_bucket":
                bucket_key = canonical_physics_bucket_key_from_replay_key(key)
                bucket_bytes = canonical_json_bytes(bucket_key)
                persistent._get_by_physics_bucket(
                    artifact=artifact,
                    physics_bucket_key=bucket_key,
                    physics_bucket_bytes=bucket_bytes,
                    physics_bucket_hash=rrd._sha256(bucket_bytes),
                    query_key=query_key,
                )
            elif reader == "pt1_ladder":
                rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
                    key,
                    rung_tag,
                )
                persistent._get_by_physics_ladder_bucket(
                    artifact=artifact,
                    rung_tag=rung_tag,
                    rung_key=rung_key,
                    rung_hash=rrd._sha256(canonical_json_bytes(rung_key)),
                    query_key=query_key,
                )
            elif reader == "pt1_interpolation_candidates":
                persistent._list_interpolation_candidates(
                    artifact=artifact,
                    replay_scope_sha256=rrd._replay_scope_hash(
                        canonical_physics_bucket_key_from_replay_key(query_key)
                    ),
                    query_key=query_key,
                )
            elif reader == "pt1_row_decode":
                with persistent._connect() as conn:
                    row = persistent._fetch(conn, key_hash)
                    assert row is not None
                    persistent._entry_from_row(
                        row,
                        artifact=artifact,
                        key=key,
                        key_bytes=(
                            b"{"
                            if corruption == "malformed_key_json"
                            else canonical_json_bytes(key)
                        ),
                        key_hash=key_hash,
                        return_decoded=True,
                    )
            elif reader == "pt1_replace":
                assert payload is not None
                payload_bytes = canonical_json_bytes(payload)
                persistent.put(
                    artifact=artifact,
                    key=key,
                    key_bytes=canonical_json_bytes(key),
                    key_hash=key_hash,
                    payload=payload,
                    payload_bytes=payload_bytes,
                    payload_hash=rrd._sha256(payload_bytes),
                    law_disclosures=[],
                )
    assert type(raised.value) is error_type
    if corruption == "compound_key_json_physics_key_bytes":
        assert "physics bucket bytes mismatch" in str(raised.value)
    elif corruption == "compound_key_json_ladder_distance":
        assert "distance is missing" in str(raised.value)
    elif corruption == "compound_replay_scope_malformed_payload_json":
        assert "exact key replay scope mismatch" in str(raised.value)


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


def _run_interpolation_class_guard_hour(
    store: PT0DeterminismStore,
    *,
    interpolation_source: Mapping[str, Any] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    replay_interpolation_into_flux: bool = False,
) -> tuple[Any, Any, int]:
    sim = _build_pt0_sim(store)
    class CappedDeterministicBackend(RealBackendAuthority):
        real_backend_family = RealBackendFamily.MAGEMIN
        name = "magemin"
        calls = 0

        def is_available(self):
            return True

        def get_engine_version(self) -> str:
            return "magemin-deterministic-test-stub"

        def equilibrate(
            self,
            *,
            temperature_C: float,
            pressure_bar: float,
            fO2_log: float,
            **_kwargs: Any,
        ) -> EquilibriumResult:
            self.calls += 1
            return EquilibriumResult(
                temperature_C=float(temperature_C),
                pressure_bar=float(pressure_bar),
                phases_present=["liquid"],
                liquid_fraction=1.0,
                liquid_composition_wt_pct={
                    "SiO2": 45.0,
                    "Al2O3": 15.0,
                    "FeO": 10.0,
                    "MgO": 10.0,
                    "CaO": 10.0,
                    "Na2O": 5.0,
                    "K2O": 5.0,
                },
                fO2_log=float(fO2_log),
                diagnostics={"deterministic_engine_stub": "magemin"},
            )

    backend = CappedDeterministicBackend()
    sim.backend = backend
    from simulator.chemistry.kernel import ChemistryIntent

    provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    provider._backend = backend
    provider._backend_initialised = True
    from simulator.engine_binding_admission import publish_live_backend_admission

    publish_live_backend_admission(backend)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim.melt.temperature_C = 1500.0
    seed_gate_curve_memo_record(
        sim,
        {
            "source": "unit-test:interpolation-class-guard",
            "solidus_T_C": 1000.0,
            "liquidus_T_C": 1600.0,
            "path": ((1000.0, 0.0), (1600.0, 1.0)),
        },
    )
    gate_curve = {
        "source": "unit-test:interpolation-class-guard",
        "status": "ok",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1600.0,
        "path": ((1000.0, 0.0), (1600.0, 1.0)),
    }
    sim._freeze_gate_curve_from_gate_dispatch = lambda *_args, **_kwargs: (
        _classified_gate_curve_for_test(sim, gate_curve)
    )
    assert store._cached_real_binding_eligible(sim)
    if interpolation_source is not None:
        def candidates(**kwargs: Any) -> list[dict[str, Any]]:
            query_key = kwargs["query_key"]
            result = []
            for offset in (-1.0, 1.0):
                key = copy.deepcopy(query_key)
                key["controls"]["T_K"] = (
                    float(query_key["controls"]["T_K"]) + offset
                )
                payload = copy.deepcopy(interpolation_source["payload"])
                payload["equilibrium_result"]["temperature_C"] = (
                    float(key["controls"]["T_K"])
                    - CELSIUS_TO_KELVIN_OFFSET
                )
                key_hash = rrd._sha256(canonical_json_bytes(key))
                result.append({
                    **copy.deepcopy(dict(interpolation_source)),
                    "key": key,
                    "key_hash": key_hash,
                    "payload": payload,
                })
            return result

        assert store.persistent_store is not None
        store.persistent_store._list_interpolation_candidates = candidates
    if replay_interpolation_into_flux:
        assert monkeypatch is not None
        original_lookup = store._lookup_interpolated

        def unguarded_flux_lookup(
            artifact: str,
            key: Mapping[str, Any],
            *,
            sim: Any | None = None,
            return_decoded: bool = False,
            flux_consumer: bool = False,
        ) -> Any | None:
            return original_lookup(
                artifact,
                key,
                sim=sim,
                return_decoded=return_decoded,
                flux_consumer=False,
            )

        monkeypatch.setattr(store, "_lookup_interpolated", unguarded_flux_lookup)
    snapshot = sim.step()
    return sim, snapshot, backend.calls


def test_interpolated_equilibrium_cache_miss_keeps_campaign_flux_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production-eligible interpolation candidate still runs the live engine."""
    import simulator.reduced_real_cache_interpolation as interpolation

    _install_interpolation_test_binding_receipt(tmp_path, monkeypatch)
    cold_store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "interpolation-class-cold.sqlite",
    )
    cold_sim, cold_snapshot, cold_calls = _run_interpolation_class_guard_hour(
        cold_store
    )
    assert cold_calls > 0
    cold_rows = [
        entry
        for entry in cold_store._entries.values()
        if entry.get("artifact") == "equilibrium_post_record"
    ]
    assert cold_rows

    attempted_interpolations: list[Mapping[str, Any]] = []
    production_attempt = interpolation.attempt_cached_interpolation

    def record_interpolation_attempt(
        query_key: Mapping[str, Any],
        neighbors: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        attempt = production_attempt(query_key, neighbors)
        if attempt is not None:
            attempted_interpolations.append(attempt)
        return attempt

    admitted_interpolations: list[rrd._DecodedEquilibriumReplayPayload] = []
    production_admission = rrd._decode_payload_for_admission

    def record_interpolation_admission(
        artifact: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        decoded = production_admission(artifact, payload, **kwargs)
        if (
            kwargs.get("interpolation_synthesized")
            and isinstance(decoded, rrd._DecodedEquilibriumReplayPayload)
        ):
            admitted_interpolations.append(decoded)
        return decoded

    monkeypatch.setattr(
        interpolation,
        "attempt_cached_interpolation",
        record_interpolation_attempt,
    )
    monkeypatch.setattr(
        rrd,
        "_decode_payload_for_admission",
        record_interpolation_admission,
    )
    warm_store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "interpolation-class-warm.sqlite",
    )
    warm_sim, warm_snapshot, warm_calls = _run_interpolation_class_guard_hour(
        warm_store,
        interpolation_source=cold_rows[0],
    )

    assert attempted_interpolations
    assert admitted_interpolations
    assert all(
        isinstance(item.result, EquilibriumResult)
        for item in admitted_interpolations
    )
    assert warm_calls > 0
    assert not any(
        event.get("artifact") == "equilibrium_post_record"
        and event.get("cache_state") == "cached_interpolated"
        for event in warm_store.replay_sequence
    )
    assert cold_snapshot.evap_flux.species_kg_hr
    assert warm_snapshot.evap_flux.species_kg_hr == pytest.approx(
        cold_snapshot.evap_flux.species_kg_hr,
        rel=1.0e-12,
        abs=1.0e-15,
    )
    cold_ledger = cold_sim.atom_ledger.kg_by_account()
    warm_ledger = warm_sim.atom_ledger.kg_by_account()
    assert set(warm_ledger) == set(cold_ledger)
    for account, cold_species in cold_ledger.items():
        warm_species = warm_ledger[account]
        assert set(warm_species) == set(cold_species)
        assert warm_species == pytest.approx(
            cold_species,
            rel=1.0e-12,
            abs=1.0e-12,
        )


def test_interpolated_flux_replay_mutation_cannot_finish_campaign_hour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_interpolation_test_binding_receipt(tmp_path, monkeypatch)
    cold_store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "interpolation-mutation-cold.sqlite",
    )
    _run_interpolation_class_guard_hour(cold_store)
    cold_row = next(
        entry
        for entry in cold_store._entries.values()
        if entry.get("artifact") == "equilibrium_post_record"
    )
    warm_store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "interpolation-mutation-warm.sqlite",
    )
    from simulator.core import PyrolysisSimulator
    from simulator.reduced_real_cache_interpolation import (
        is_interpolated_equilibrium,
    )

    original_builder = PyrolysisSimulator.build_vapour_batch
    interpolated_builder_inputs = []

    def observe_interpolated_batch_build(self, *args, **kwargs):
        diagnostic = getattr(self, "_last_vapor_pressure_diagnostic", {}) or {}
        if is_interpolated_equilibrium(
            self,
            vapor_pressure_diagnostic=diagnostic,
        ):
            interpolated_builder_inputs.append((args, kwargs))
        return original_builder(self, *args, **kwargs)

    monkeypatch.setattr(
        PyrolysisSimulator,
        "build_vapour_batch",
        observe_interpolated_batch_build,
    )

    with pytest.raises(EvaporationFluxRefusal) as refusal:
        _run_interpolation_class_guard_hour(
            warm_store,
            interpolation_source=cold_row,
            monkeypatch=monkeypatch,
            replay_interpolation_into_flux=True,
        )

    assert refusal.value.reason == (
        "interpolated_equilibrium_not_flux_eligible"
    )
    notices = refusal.value.diagnostic["vapour_batch_flux_overlay"]["notices"]
    assert notices
    assert all(
        notice["kind"] == "vapour_batch_unresolved"
        and notice["reason"] == "interpolated_equilibrium_not_flux_eligible"
        for notice in notices
    )
    assert interpolated_builder_inputs == []


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


def _capture_recursive_equilibrium_row(
    store: PT0DeterminismStore,
    *,
    temperature_K: float | None = None,
    commanded_pO2_bar: float | None = None,
) -> tuple[Any, EquilibriumResult, dict[str, Any], dict[str, Any]]:
    sim, _fixture_result = _pt0_equilibrium_capture_context(store)
    capture_temperature_K = (
        1599.0 if temperature_K is None else float(temperature_K)
    )
    sim.melt.temperature_C = (
        capture_temperature_K - CELSIUS_TO_KELVIN_OFFSET
    )
    if commanded_pO2_bar is not None:
        sim._commanded_pO2_bar = lambda: float(commanded_pO2_bar)
        sim._vapor_pressure_dispatch_pO2_bar = lambda: float(
            commanded_pO2_bar
        )
    sim.melt.fO2_log = -9.0
    if hasattr(sim.melt, "melt_fO2_log"):
        sim.melt.melt_fO2_log = -9.0
    _fixture_result.temperature_C = float(sim.melt.temperature_C)
    _fixture_result.pressure_bar = float(sim.melt.p_total_mbar) / MBAR_PER_BAR
    _fixture_result.fO2_log = sim._current_melt_redox_fO2_log()
    with pytest.MonkeyPatch.context() as monkeypatch:
        _configure_recursive_gate_sim(sim, monkeypatch)
        result = sim._record_equilibrium_status(
            _fixture_result,
            run_post_equilibrium_sulfsat=False,
        )
        assert (
            store.capture_sequence[-1]["artifact"]
            == "equilibrium_post_record"
        )
        key = store._equilibrium_key(sim)
        payload = copy.deepcopy(store._entries[_key_hash(key)]["payload"])
    return sim, result, key, payload


def _capture_recursive_gate_curve_in_context(
    store: PT0DeterminismStore,
    sim: Any,
) -> dict[str, Any]:
    projected_diagnostic, notice = _lunar_composition_projected_diagnostic(
        sim
    )
    assert projected_diagnostic["backend_status"] == "out_of_domain"
    curve = sim._freeze_gate_curve_from_bounds(
        solidus_T_C=1200.0,
        liquidus_T_C=1500.0,
        source="unit-test:recursive-admission",
        reasons=[],
    )
    assert isinstance(curve, Mapping)
    curve = copy.deepcopy(dict(curve))
    curve["composition_projected_notice"] = notice
    fO2_log = float(sim._current_melt_redox_fO2_log())
    seed_gate_curve_memo_record(
        sim,
        curve,
        repair_notices=[],
    )
    store.capture_gate_curve(
        sim,
        fO2_log=fO2_log,
        curve=curve,
        repair_notices=[],
        law_disclosures=[],
    )
    return curve


def _capture_recursive_gate_row(
    db_path: Path,
    *,
    persistent: bool,
) -> tuple[
    PT0DeterminismStore,
    Any,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    capture = PT0DeterminismStore(
        "capture",
        db_path=db_path if persistent else None,
    )
    sim = _build_pt0_sim(capture)
    with pytest.MonkeyPatch.context() as monkeypatch:
        _configure_recursive_gate_sim(sim, monkeypatch)
        curve = _capture_recursive_gate_curve_in_context(capture, sim)
    assert isinstance(curve.get("composition_projected_notice"), Mapping)
    assert capture.capture_sequence[-1]["artifact"] == "freeze_gate_curve"
    key = copy.deepcopy(capture.capture_sequence[-1]["key"])
    baseline = copy.deepcopy(capture._entries[_key_hash(key)]["payload"])
    assert any(
        path and path[-1] == "mass_fraction"
        for path, _value in _recursive_payload_paths(baseline)
    )
    return capture, sim, curve, key, baseline


class _RecursiveMappingKey:
    def __init__(self, key: Any) -> None:
        self.key = key


def _recursive_payload_paths(payload: Any):
    def walk(value: Any, path: tuple[Any, ...]):
        yield path, value
        if isinstance(value, Mapping):
            for key, child in value.items():
                yield (*path, _RecursiveMappingKey(key)), key
                yield from walk(child, (*path, str(key)))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                yield from walk(child, (*path, index))

    yield from walk(payload, ())


def _curated_recursive_mutations(
    payload: Mapping[str, Any],
    artifact: str,
    source_name: str,
) -> list[tuple[tuple[Any, ...], str, Any]]:
    """Return representative admission mutations for a single captured row."""
    if artifact == "equilibrium_post_record":
        diagnostic = payload["last_vapor_pressure_diagnostic"]
        provenance = diagnostic.get("vapor_pressure_numerator_provenance")
        assert isinstance(provenance, Mapping) and provenance
        species = sorted(provenance)[0]
        source_names = payload["last_vapor_pressures_source"]
        assert isinstance(source_names, Mapping) and source_names
        source_species = sorted(source_names)[0]
        mutations = [
            (("equilibrium_result", "temperature_C"), "delete", None),
            (
                ("equilibrium_result", "status"),
                "wrong-scalar",
                0,
            ),
            (("equilibrium_result", "pressure_bar"), "none", None),
            (
                ("last_vapor_pressure_diagnostic", "source_reaction_fO2_bar"),
                "bool",
                True,
            ),
            (
                ("last_vapor_pressure_diagnostic", "source_reaction_fO2_bar"),
                "non-finite",
                math.inf,
            ),
            (
                ("last_vapor_pressure_diagnostic", "high_t_melt_activity"),
                "empty-map",
                {},
            ),
            (
                (
                    "last_vapor_pressure_diagnostic",
                    "high_t_melt_activity",
                    "vaporock_cap_K",
                ),
                "wrong-scalar",
                "wrong-type",
            ),
            (
                (
                    "last_vapor_pressure_diagnostic",
                    "vapor_pressure_numerator_provenance",
                    species,
                ),
                "malformed-provenance",
                [],
            ),
            (("last_vapor_pressures_source",), "wrong-scalar", []),
            # Keep a real source-entry deletion: it remains admitted and the
            # full replay consumer completes for both stores.
            (
                (
                    "last_vapor_pressures_source",
                    _RecursiveMappingKey(source_species),
                ),
                "delete",
                None,
            ),
            (("last_vapor_pressure_diagnostic",), "none", None),
        ]
        equilibrium_result = payload["equilibrium_result"]
        result_diagnostics = equilibrium_result.get("diagnostics")
        diagnostic_sources = [
            (("last_vapor_pressure_diagnostic",), diagnostic)
        ]
        if isinstance(result_diagnostics, Mapping):
            diagnostic_sources.append(
                (("equilibrium_result", "diagnostics"), result_diagnostics)
            )
        for field_name in ("vaporock_full_speciation_Pa", "activities"):
            for prefix, diagnostic_source in diagnostic_sources:
                values = diagnostic_source.get(field_name)
                if isinstance(values, Mapping) and values:
                    species = sorted(values)[0]
                    mutations.append(
                        (
                            (*prefix, field_name, species),
                            "wrong-scalar",
                            "wrong-type",
                        )
                    )
                    break
        return [
            (path, mutation, replacement)
            for path, mutation, replacement in mutations
            if _payload_path_exists(payload, path)
        ]

    if artifact != "freeze_gate_curve":
        raise AssertionError(f"unexpected recursive payload artifact: {artifact}")
    if source_name == "real-gate-step-down-output":
        return [
            # This optional producer detail can be removed while the stored
            # curve still replays through the real consumer in both stores.
            (
                (
                    "curve",
                    "gate_provider_step_down",
                    "failed_providers",
                    0,
                    "status",
                ),
                "delete",
                None,
            ),
            (
                ("curve", "gate_provider_step_down", "realized_provider"),
                "delete",
                None,
            )
        ]
    curve = payload["curve"]
    mutations = [
        (("curve", "solidus_T_C"), "delete", None),
        (("curve", "source"), "wrong-scalar", 0),
        (("curve", "liquidus_T_C"), "none", None),
        (("curve", "solidus_T_C"), "bool", True),
        (("curve", "liquidus_T_C"), "non-finite", math.inf),
        (("curve",), "empty-map", {}),
    ]
    projected_notice = curve.get("composition_projected_notice")
    if isinstance(projected_notice, Mapping):
        for path, _value in _recursive_payload_paths(projected_notice):
            if path and path[-1] == "mass_fraction":
                mutations.append(
                    (
                        ("curve", "composition_projected_notice", *path),
                        "wrong-scalar",
                        "wrong-type",
                    )
                )
                break
    return [
        (path, mutation, replacement)
        for path, mutation, replacement in mutations
        if _payload_path_exists(payload, path)
    ]


def _payload_path_exists(payload: Any, path: tuple[Any, ...]) -> bool:
    value = payload
    for part in path:
        if isinstance(part, _RecursiveMappingKey):
            if not isinstance(value, Mapping) or part.key not in value:
                return False
            value = value[part.key]
            continue
        if isinstance(value, Mapping):
            if part not in value:
                return False
            value = value[part]
        elif isinstance(value, (list, tuple)) and isinstance(part, int):
            if part < 0 or part >= len(value):
                return False
            value = value[part]
        else:
            return False
    return True


def _recursive_mutation_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return []
    if isinstance(value, (list, tuple)):
        return 0
    if isinstance(value, bool) or value is None:
        return "wrong-type"
    if isinstance(value, str):
        return 0
    if isinstance(value, (int, float)):
        return "wrong-type"
    return []


def _mutate_recursive_payload(
    payload: Any,
    path: tuple[Any, ...],
    mutation: str,
    replacement: Any = None,
) -> Any:
    def mutate(value: Any, depth: int) -> Any:
        if depth == len(path):
            return copy.deepcopy(replacement)
        part = path[depth]
        is_last = depth == len(path) - 1
        if isinstance(part, _RecursiveMappingKey):
            if not is_last or not isinstance(value, Mapping):
                raise TypeError("mapping-key mutation path is malformed")
            updated = copy.deepcopy(dict(value))
            original_key = part.key
            if mutation == "delete":
                del updated[original_key]
            else:
                child = updated.pop(original_key)
                updated[copy.deepcopy(replacement)] = child
            return updated
        if isinstance(value, Mapping):
            updated = copy.deepcopy(dict(value))
            if is_last and mutation == "delete":
                del updated[part]
            else:
                updated[part] = mutate(updated[part], depth + 1)
            return updated
        if isinstance(value, list):
            updated = copy.deepcopy(value)
            if is_last and mutation == "delete":
                updated.pop(part)
            else:
                updated[part] = mutate(updated[part], depth + 1)
            return updated
        if isinstance(value, tuple):
            updated = list(copy.deepcopy(value))
            if is_last and mutation == "delete":
                updated.pop(part)
            else:
                updated[part] = mutate(updated[part], depth + 1)
            return tuple(updated)
        raise TypeError(f"cannot mutate path through {type(value).__name__}")

    return mutate(payload, 0)


def _recursive_path_label(path: tuple[Any, ...]) -> str:
    label = "root"
    for part in path:
        if isinstance(part, _RecursiveMappingKey):
            label += f"[key={part.key!r}]"
        elif isinstance(part, int):
            label += f"[{part}]"
        else:
            label += f".{part}"
    return label


def _write_pt1_payload_for_invariant(
    db_path: Path,
    key_hash: str,
    payload: Any,
) -> None:
    payload_bytes = rrd._payload_integrity_bytes(payload)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ? WHERE key_hash = ?",
            (payload_bytes, rrd._sha256(payload_bytes), key_hash),
        )


def _recursive_cache_reader(
    capture: PT0DeterminismStore,
    *,
    persistent: bool,
    db_path: Path,
) -> PT0DeterminismStore:
    if persistent:
        return PT0DeterminismStore("replay", db_path=db_path)
    reader = PT0DeterminismStore("replay")
    reader._entries = copy.deepcopy(capture._entries)
    reader.physics_bucket_entries = copy.deepcopy(
        capture.physics_bucket_entries
    )
    return reader


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
@pytest.mark.parametrize(
    "missing_field",
    (
        "empty",
        "min_T_C",
        "max_T_C",
        "source",
        "empty-source",
        "null-projection-band",
    ),
    ids=(
        "empty-band",
        "missing-min",
        "missing-max",
        "missing-source",
        "empty-source",
        "null-projection-band",
    ),
)
def test_projected_floor_cache_notice_requires_full_producer_certified_band(
    tmp_path: Path,
    persistent: bool,
    missing_field: str,
) -> None:
    db_path = tmp_path / f"projected-floor-band-{persistent}.sqlite"
    capture, sim, _curve, key, baseline = _capture_recursive_gate_row(
        db_path,
        persistent=persistent,
    )
    _, projection_notice = _lunar_composition_projected_diagnostic(sim)
    ordinary_band = projection_notice["certified_band"]
    assert {
        "engine",
        "database",
        "bulk_components",
    }.issubset(ordinary_band)
    assert isinstance(ordinary_band["engine"], str)
    assert isinstance(ordinary_band["database"], str)
    assert isinstance(ordinary_band["bulk_components"], (list, tuple))
    assert all(
        isinstance(item, str)
        for item in ordinary_band["bulk_components"]
    )
    assert "projection_certified_band" not in projection_notice
    floor_curve = sim._freeze_gate_kress_floor_curve(projection_notice, [])
    assert floor_curve is not None
    floor_notice = floor_curve["composition_projected_notice"]
    assert floor_notice["projection_certified_band"]
    assert {
        "min_T_C",
        "max_T_C",
        "source",
    }.issubset(floor_notice["certified_band"])

    valid_notice = (
        projection_notice
        if missing_field == "null-projection-band"
        else floor_notice
    )
    valid_payload = copy.deepcopy(baseline)
    valid_payload["curve"]["composition_projected_notice"] = copy.deepcopy(
        valid_notice
    )
    key_hash = _key_hash(key)
    capture_entry = capture._entries[key_hash]
    assert rrd._decode_payload_for_admission(
        "freeze_gate_curve",
        valid_payload,
        payload_codec_version=capture_entry.get("payload_codec_version"),
        keys=(key,),
    ) is not None
    malformed_payload = copy.deepcopy(valid_payload)
    certified_band = malformed_payload["curve"][
        "composition_projected_notice"
    ]["certified_band"]
    if missing_field == "empty":
        malformed_payload["curve"]["composition_projected_notice"][
            "certified_band"
        ] = {}
    elif missing_field == "empty-source":
        certified_band["source"] = ""
    elif missing_field == "null-projection-band":
        malformed_payload["curve"]["composition_projected_notice"][
            "projection_certified_band"
        ] = None
    else:
        del certified_band[missing_field]

    capture._entries[key_hash]["payload"] = copy.deepcopy(malformed_payload)
    capture._entries[key_hash]["payload_hash"] = rrd._sha256(
        canonical_json_bytes(malformed_payload)
    )
    if persistent:
        _write_pt1_payload_for_invariant(db_path, key_hash, malformed_payload)
    reader = _recursive_cache_reader(
        capture,
        persistent=persistent,
        db_path=db_path,
    )
    with pytest.raises(PT0CacheMiss):
        reader._lookup("freeze_gate_curve", key)
    assert reader.hits == 0

    original_entry = capture._entries[key_hash]
    capture._store(
        "freeze_gate_curve",
        key,
        valid_payload,
        law_disclosures=original_entry.get("law_disclosures", ()),
    )
    if not persistent:
        reader._entries[key_hash] = copy.deepcopy(capture._entries[key_hash])
    assert reader._lookup("freeze_gate_curve", key) == valid_payload
    assert reader.hits == 1


def _assert_authorityless_pt1_reads_miss_and_replace(
    capture: PT0DeterminismStore,
    *,
    key: Mapping[str, Any],
    baseline: Mapping[str, Any],
    db_path: Path,
) -> None:
    key_hash = _key_hash(dict(key))
    key_bytes = canonical_json_bytes(key)
    authorityless = copy.deepcopy(dict(baseline))
    authorityless.pop("authority")
    entry = capture._entries[key_hash]
    entry["payload"] = copy.deepcopy(authorityless)
    entry["payload_hash"] = rrd._sha256(canonical_json_bytes(authorityless))
    _write_pt1_payload_for_invariant(db_path, key_hash, authorityless)

    assert capture._verify_entry(
        "equilibrium_post_record",
        key,
        key_bytes,
        key_hash,
        entry,
    ) is None
    assert rrd._decode_payload_for_admission(
        "equilibrium_post_record",
        authorityless,
        payload_codec_version=entry["payload_codec_version"],
    ) is None

    persistent = capture.persistent_store
    assert persistent is not None
    exact = persistent._get_with_decoded(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
    )
    assert exact is None

    bucket_key = canonical_physics_bucket_key_from_replay_key(key)
    bucket_bytes = canonical_json_bytes(bucket_key)
    bucket_hash = rrd._sha256(bucket_bytes)
    assert persistent._get_by_physics_bucket(
        artifact="equilibrium_post_record",
        physics_bucket_key=bucket_key,
        physics_bucket_bytes=bucket_bytes,
        physics_bucket_hash=bucket_hash,
        query_key=key,
    ) is None

    rung_tag = rrd.PHYSICS_BUCKET_LADDER_RUNGS[0][0]
    rung_key = rrd.canonical_physics_ladder_bucket_key_from_replay_key(
        key,
        rung_tag,
    )
    rung_hash = rrd._sha256(canonical_json_bytes(rung_key))
    assert persistent._get_by_physics_ladder_bucket(
        artifact="equilibrium_post_record",
        rung_tag=rung_tag,
        rung_key=rung_key,
        rung_hash=rung_hash,
        query_key=key,
    ) is None

    replay_scope_sha256 = rrd._replay_scope_hash(bucket_key)
    assert persistent._list_interpolation_candidates(
        artifact="equilibrium_post_record",
        replay_scope_sha256=replay_scope_sha256,
        query_key=key,
    ) == []

    capture._store("equilibrium_post_record", key, baseline)
    repaired = PT1PersistentEquilibriumStore(db_path)._get_with_decoded(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
    )
    assert repaired is not None


def _exercise_equilibrium_replay_consumers(
    store: PT0DeterminismStore,
    sim: Any | None = None,
    *,
    allow_typed_batch_failure: bool = False,
) -> Any:
    if sim is None:
        sim, _ = _pt0_equilibrium_capture_context(store)
    result = sim._get_equilibrium()
    assert store.hits == 1
    assert store.last_cache_state in {
        "cached_exact",
        "cached_physics_bucket",
        "cached_interpolated",
    }
    _exercise_equilibrium_consumers_from_result(
        sim,
        result,
        allow_typed_batch_failure=allow_typed_batch_failure,
    )
    return sim


def _exercise_equilibrium_consumers_from_result(
    sim: Any,
    result: EquilibriumResult,
    *,
    allow_typed_batch_failure: bool = False,
) -> None:
    resolved = sim._resolve_evaporation_batch_flux_state(
        result,
        temperature_K=(
            float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        ),
    )
    assert isinstance(resolved, tuple) and len(resolved) == 7
    if not allow_typed_batch_failure:
        assert resolved[3].get("status") != "unavailable"
    assert isinstance(
        getattr(sim, "_langmuir_flux_closure_context", None), Mapping
    )
    assert {
        "publication_Pa",
        "temperature_K",
        "film_partials_Pa",
        "film_pressure_Pa",
        "root_closed",
    }.issubset(sim._langmuir_flux_closure_context)


def _exercise_gate_replay_consumer(
    sim: Any,
    curve: Mapping[str, Any],
) -> None:
    midpoint_C = (float(curve["solidus_T_C"]) + float(curve["liquidus_T_C"])) / 2.0
    liquid_fraction = sim._melt_redox_liquid_fraction_at_temperature(
        curve,
        midpoint_C,
    )
    assert isinstance(liquid_fraction, float)
    if "composition_projected_notice" in curve:
        assert isinstance(
            sim.composition_projected_liquidus_run_notice(), Mapping
        )


def _accepted_hit_disclosures_for_test(store: PT0DeterminismStore) -> Any:
    return copy.deepcopy(getattr(store, "_accepted_hit_disclosures", None))


def _configure_recursive_gate_sim(
    sim: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim._liquid_fraction_provider = "magemin"
    register_synthetic_gate_curve_producer(sim)
    trace = _install_magemin_subprocess_gate(sim, monkeypatch)
    alpha_provider = sim._chem_registry.gate_provider_order[1]
    monkeypatch.setattr(alpha_provider, "_ensure_backend", lambda: None)
    if not callable(getattr(sim.backend, "equilibrate", None)):
        def unexpected_equilibrium(
            *,
            composition_mol_by_account: Any = None,
        ) -> None:
            raise AssertionError(
                "gate cache fixture unexpectedly used the equilibrium backend"
            )

        sim.backend.equilibrate = unexpected_equilibrium
    return trace


def _exercise_cache_payload_replay(
    store: PT0DeterminismStore,
    artifact: str,
    *,
    sim: Any | None = None,
    allow_typed_batch_failure: bool = False,
) -> Any:
    if artifact == "equilibrium_post_record":
        if sim is not None:
            sim.configure_pt0_determinism_store(store)
            sim._freeze_gate_liquid_fraction_cache = None
        return _exercise_equilibrium_replay_consumers(
            store,
            sim,
            allow_typed_batch_failure=allow_typed_batch_failure,
        )
    if sim is None:
        sim = _build_pt0_sim(store)
        with pytest.MonkeyPatch.context() as monkeypatch:
            _configure_recursive_gate_sim(sim, monkeypatch)
    sim.configure_pt0_determinism_store(store)
    sim._freeze_gate_liquid_fraction_cache = None
    curve = sim._freeze_gate_curve()
    assert store.hits == 1
    assert store.last_cache_state == "cached_exact"
    _exercise_gate_replay_consumer(sim, curve)
    return sim


_SIO_REPORT_DIAGNOSTIC_FIELDS = (
    "high_t_melt_activity",
    "vaporock_full_speciation_Pa",
)


def _exercise_sio_yield_report_diagnostic(sim: Any) -> None:
    import simulator.runner as runner_module

    session = SimpleNamespace(simulator=sim)
    completed_result = {
        "status": "complete",
        "final_state": {},
        "per_hour_summary": [{"mass_balance_pct": 0.0}],
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            runner_module.PyrolysisRun,
            "_start_session",
            lambda _run: session,
        )
        monkeypatch.setattr(
            runner_module.PyrolysisRun,
            "_run_session",
            lambda _run, _session: completed_result,
        )
        monkeypatch.setattr(
            runner_module,
            "_prepare_sio_campaign_start",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "_apply_sio_wall_sweep_controls",
            lambda *_args, **_kwargs: None,
        )
        report, diagnostics = runner_module.build_sio_yield_report(
            feedstock_id="lunar_mare_low_ti",
            hours=0,
            include_diagnostics=True,
            allow_unmeasured_alpha_fallback=True,
        )

    assert isinstance(report, Mapping)
    assert isinstance(diagnostics, Mapping)
    for field_name in _SIO_REPORT_DIAGNOSTIC_FIELDS:
        if field_name in diagnostics:
            assert isinstance(diagnostics[field_name], Mapping)
    source_diagnostic = getattr(
        sim,
        "_last_vapor_pressure_diagnostic",
        {},
    ) or {}
    source_speciation = source_diagnostic.get("vaporock_full_speciation_Pa")
    vaporock_speciation = diagnostics.get("vaporock_full_speciation_Pa")
    if isinstance(vaporock_speciation, Mapping):
        assert isinstance(source_speciation, Mapping)
        assert vaporock_speciation == dict(source_speciation)
        assert all(
            isinstance(pressure, (int, float))
            and not isinstance(pressure, bool)
            and math.isfinite(float(pressure))
            for pressure in vaporock_speciation.values()
        )
        if "SiO" in vaporock_speciation:
            assert vaporock_speciation["SiO"] == source_speciation["SiO"]
            assert math.isfinite(float(vaporock_speciation["SiO"]))


def _is_sio_report_diagnostic_path(path: tuple[Any, ...]) -> bool:
    return (
        len(path) >= 2
        and path[0] == "last_vapor_pressure_diagnostic"
        and path[1] in _SIO_REPORT_DIAGNOSTIC_FIELDS
    )


def _real_gate_step_down_mutation_source(
    payload: dict[str, Any],
    key: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    store = PT0DeterminismStore("capture")
    sim = _build_pt0_sim(store)
    with pytest.MonkeyPatch.context() as monkeypatch:
        trace = _configure_recursive_gate_sim(sim, monkeypatch)
        successful_curve = sim._establish_melt_redox_gate_authority_for_current_hour()
        assert isinstance(successful_curve, Mapping)
        completed_hour = int(sim.melt.hour)
        sim._clear_melt_redox_gate_authority_for_completed_hour(completed_hour)
        sim.melt.hour += 1
        sim._freeze_gate_liquid_fraction_cache = None
        sim._freeze_gate_liquid_fraction_curve_memo.clear()
        sim._freeze_gate_shared_curve_cache = {}
        trace["fail"] = True
        held_curve = sim._establish_melt_redox_gate_authority_for_current_hour()

    step_down = held_curve.get("gate_provider_step_down")
    assert isinstance(step_down, Mapping)
    assert "realized_provider" in step_down
    assert type(step_down.get("magemin_sourced")) is bool
    mutation_payload = copy.deepcopy(payload)
    mutation_payload["curve"]["gate_provider_step_down"] = copy.deepcopy(
        step_down
    )
    assert rrd._decode_payload_for_admission(
        "freeze_gate_curve",
        mutation_payload,
        payload_codec_version=rrd._payload_codec_version(
            "freeze_gate_curve"
        ),
        keys=(key,),
    ) is not None
    return "real-gate-step-down-output", mutation_payload


def _recursive_capture_for_kind(
    payload_kind: str,
    *,
    tmp_path: Path,
    persistent: bool,
) -> tuple[
    PT0DeterminismStore,
    str,
    dict[str, Any],
    dict[str, Any],
    Path,
    Any,
]:
    db_path = tmp_path / f"recursive-{payload_kind}.sqlite"
    capture: PT0DeterminismStore
    key: dict[str, Any]
    baseline: dict[str, Any]
    sim: Any
    if payload_kind == "gate":
        capture, sim, _curve, key, baseline = _capture_recursive_gate_row(
            db_path,
            persistent=persistent,
        )
        artifact = "freeze_gate_curve"
    else:
        capture = PT0DeterminismStore(
            "capture",
            db_path=db_path if persistent else None,
        )
        artifact = "equilibrium_post_record"
        sim, _result, key, baseline = _capture_recursive_equilibrium_row(
            capture,
            temperature_K=2073.15,
        )
        replay_diagnostic = baseline["last_vapor_pressure_diagnostic"]
        assert isinstance(replay_diagnostic, Mapping)
        assert isinstance(
            replay_diagnostic.get("high_t_melt_activity"), Mapping
        )
        assert any(
            path
            and path[-1] == "activities"
            and isinstance(value, Mapping)
            for path, value in _recursive_payload_paths(baseline)
        )
    return capture, artifact, key, baseline, db_path, sim


@pytest.mark.parametrize(
    "payload_kind",
    ("equilibrium", "gate"),
    ids=("production-equilibrium-capture", "production-gate-capture"),
)
@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_real_cache_payload_recursive_mutations_miss_or_replay_identically(
    tmp_path: Path,
    payload_kind: str,
    persistent: bool,
) -> None:
    """Replay a compact representative admission matrix through both stores.

    The equilibrium cases delete a required temperature, use an invalid
    status scalar, set pressure to None, use bool and infinity for a numeric
    diagnostic, empty the required high-temperature map, corrupt its numeric
    cap, replace a nested provenance species mapping, sample one VapoRock and
    activity species value, and corrupt replay-side fields. Gate cases delete
    a required bound, use an invalid source scalar, set a bound to None, bool,
    or infinity, empty the curve, corrupt one projected mass fraction, and
    remove step-down provenance. Each keeps real recapture or replay while
    leaving repetitive leaves to nightly.
    """
    _run_real_cache_payload_recursive_mutations(
        tmp_path,
        payload_kind,
        persistent,
        exhaustive_sweep=False,
    )


@pytest.mark.nightly
@pytest.mark.timeout(21600)
@pytest.mark.parametrize(
    "payload_kind",
    ("equilibrium", "gate"),
    ids=("production-equilibrium-capture", "production-gate-capture"),
)
@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_real_cache_payload_recursive_mutations_exhaustive_nightly(
    tmp_path: Path,
    payload_kind: str,
    persistent: bool,
) -> None:
    _run_real_cache_payload_recursive_mutations(
        tmp_path,
        payload_kind,
        persistent,
        exhaustive_sweep=True,
    )


def _run_real_cache_payload_recursive_mutations(
    tmp_path: Path,
    payload_kind: str,
    persistent: bool,
    *,
    exhaustive_sweep: bool,
) -> None:
    from simulator.evaporation import EvaporationFluxRefusal

    (
        capture,
        artifact,
        key,
        baseline,
        db_path,
        replay_sim,
    ) = _recursive_capture_for_kind(
        payload_kind,
        tmp_path=tmp_path,
        persistent=persistent,
    )
    if artifact == "equilibrium_post_record":
        assert rrd._admission_rejection(
            artifact,
            baseline,
            payload_codec_version=rrd._payload_codec_version(artifact),
            keys=(key,),
        ) is None
        assert rrd._decode_payload_for_admission(
            artifact,
            baseline,
            payload_codec_version=rrd._payload_codec_version(artifact),
            keys=(key,),
        ) is not None
    key_hash = _key_hash(key)
    reader = _recursive_cache_reader(
        capture,
        persistent=persistent,
        db_path=db_path,
    )
    if artifact == "equilibrium_post_record":
        # Keep the nested gate identity separate from the mutated equilibrium
        # row. The curve itself comes from the real gate capture path; gate
        # payload admission is exercised by its own memory/PT1 sweep below.
        gate_curve = _capture_recursive_gate_row(
            tmp_path / "recursive-consumer-gate.sqlite",
            persistent=False,
        )[2]
        reader.replay_gate_curve = lambda *_args, **_kwargs: copy.deepcopy(
            gate_curve
        )
    replay_sim.configure_pt0_determinism_store(reader)
    _exercise_cache_payload_replay(reader, artifact, sim=replay_sim)
    if artifact == "equilibrium_post_record":
        _exercise_sio_yield_report_diagnostic(replay_sim)
    if payload_kind == "equilibrium" and persistent:
        _assert_authorityless_pt1_reads_miss_and_replace(
            capture,
            key=key,
            baseline=baseline,
            db_path=db_path,
        )
    failures: list[str] = []
    paths_seen = 0
    cases_expected = 0
    outcomes: dict[str, str] = {}
    executed_sources: set[str] = set()
    mutation_sources = [("encoded-row", copy.deepcopy(baseline))]
    if artifact == "freeze_gate_curve":
        mutation_sources.append(
            _real_gate_step_down_mutation_source(baseline, key)
        )
    if exhaustive_sweep:
        mutation_paths = [
            (source_name, mutation_baseline, path, value, None, None)
            for source_name, mutation_baseline in mutation_sources
            for path, value in _recursive_payload_paths(mutation_baseline)
        ]
    else:
        mutation_paths = [
            (
                source_name,
                mutation_baseline,
                path,
                None,
                mutation,
                replacement,
            )
            for source_name, mutation_baseline in mutation_sources
            for path, mutation, replacement in _curated_recursive_mutations(
                mutation_baseline,
                artifact,
                source_name,
            )
        ]
        assert len(mutation_paths) <= 30

    def reset_reader_for_next_row() -> None:
        reader.hits = 0
        reader.misses.clear()
        reader.replay_sequence.clear()
        reader.capture_sequence.clear()
        reader.cache_events.clear()
        reader.last_cache_state = None
        reader._accepted_hit_disclosures = None

    for (
        source_name,
        mutation_baseline,
        path,
        value,
        selected_mutation,
        selected_replacement,
    ) in mutation_paths:
        executed_sources.add(source_name)
        paths_seen += 1
        mutations: list[tuple[str, Any]] = []
        if selected_mutation is not None:
            mutations.append((selected_mutation, selected_replacement))
        else:
            if path:
                mutations.append(("delete", None))
            mutations.extend(
                (
                    ("wrong-scalar", _recursive_mutation_value(value)),
                    ("none", None),
                )
            )
            if path[-1:] == ("source_reaction_fO2_bar",):
                mutations.append(("bool", True))
            if len(path) == 2 and _is_sio_report_diagnostic_path(path):
                mutations.append(("scalar-map", 7))
        for mutation, replacement in mutations:
            cases_expected += 1
            path_name = f"{source_name}: {_recursive_path_label(path)}"
            case_name = f"{path_name} {mutation}"
            outcomes[case_name] = "error"
            mutated = _mutate_recursive_payload(
                mutation_baseline,
                path,
                mutation,
                replacement,
            )
            capture._entries[key_hash]["payload"] = copy.deepcopy(mutated)
            capture._entries[key_hash]["payload_hash"] = rrd._sha256(
                rrd._payload_integrity_bytes(mutated)
            )
            if persistent:
                _write_pt1_payload_for_invariant(db_path, key_hash, mutated)
                reader._entries.pop(key_hash, None)
            else:
                reader._entries[key_hash] = copy.deepcopy(
                    capture._entries[key_hash]
                )
            reset_reader_for_next_row()
            before_disclosures = _accepted_hit_disclosures_for_test(reader)
            nonfinite_mutation = mutation == "non-finite"
            typed_rejection = False
            try:
                admitted = reader._lookup(
                    artifact,
                    key,
                    return_decoded=(artifact == "equilibrium_post_record"),
                )
            except PT0CacheMiss:
                admitted = None
            except PT0NonFinitePayload as exc:
                if persistent or not nonfinite_mutation:
                    failures.append(
                        f"{path_name} {mutation}: admission lookup raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                admitted = None
                typed_rejection = True
            except PT1PersistentStoreCorrupt as exc:
                if not persistent or not nonfinite_mutation:
                    failures.append(
                        f"{path_name} {mutation}: admission lookup raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                admitted = None
                typed_rejection = True
            except Exception as exc:
                failures.append(
                    f"{path_name} {mutation}: admission lookup raised "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if nonfinite_mutation and admitted is not None:
                failures.append(
                    f"{path_name} {mutation}: non-finite payload was admitted"
                )
                continue
            if admitted is None:
                outcomes[case_name] = (
                    "typed-rejection" if typed_rejection else "miss"
                )
                if (
                    reader.hits != 0
                    or _accepted_hit_disclosures_for_test(reader)
                    != before_disclosures
                ):
                    failures.append(
                        f"{path_name} {mutation}: miss changed hit/disclosure state"
                    )
                original = capture._entries[key_hash]
                try:
                    capture._store(
                        artifact,
                        key,
                        baseline,
                        law_disclosures=original.get("law_disclosures", ()),
                    )
                except Exception as exc:
                    failures.append(
                        f"{path_name} {mutation}: recapture raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                if persistent:
                    reader._entries.pop(key_hash, None)
                else:
                    reader._entries[key_hash] = copy.deepcopy(
                        capture._entries[key_hash]
                    )
                reset_reader_for_next_row()
                try:
                    recaptured = reader._lookup(
                        artifact,
                        key,
                        return_decoded=(
                            artifact == "equilibrium_post_record"
                        ),
                    )
                    if recaptured is None or reader.hits != 1:
                        failures.append(
                            f"{path_name} {mutation}: recaptured original row "
                            f"did not hit exactly once (hits={reader.hits})"
                        )
                except Exception as exc:
                    failures.append(
                        f"{path_name} {mutation}: recaptured original row "
                        f"lookup raised "
                        f"{type(exc).__name__}: {exc}"
                    )
            else:
                outcomes[case_name] = "admitted"
                reset_reader_for_next_row()
                report_diagnostic_mutation = (
                    artifact == "equilibrium_post_record"
                    and _is_sio_report_diagnostic_path(path)
                )
                try:
                    replay_sim = _exercise_cache_payload_replay(
                        reader,
                        artifact,
                        sim=replay_sim,
                        allow_typed_batch_failure=True,
                    )
                except EvaporationFluxRefusal:
                    outcomes[case_name] = "typed-outcome"
                    target_hits = sum(
                        replay.get("artifact") == artifact
                        for replay in reader.replay_sequence
                    )
                    if target_hits != 1:
                        failures.append(
                            f"{path_name} {mutation}: typed replay outcome recorded "
                            f"{target_hits} {artifact} hits across "
                            f"{reader.hits} total cache hits"
                        )
                    if report_diagnostic_mutation:
                        try:
                            _exercise_sio_yield_report_diagnostic(replay_sim)
                        except Exception as exc:
                            failures.append(
                                f"{path_name} {mutation}: SiO report consumer "
                                f"raised {type(exc).__name__}: {exc}"
                            )
                    continue
                except Exception as exc:
                    failures.append(
                        f"{path_name} {mutation}: admitted replay raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                target_hits = sum(
                    replay.get("artifact") == artifact
                    for replay in reader.replay_sequence
                )
                if target_hits != 1:
                    failures.append(
                        f"{path_name} {mutation}: admitted replay recorded "
                        f"{target_hits} {artifact} hits across "
                        f"{reader.hits} total cache hits"
                    )
                if report_diagnostic_mutation:
                    try:
                        _exercise_sio_yield_report_diagnostic(replay_sim)
                    except Exception as exc:
                        failures.append(
                            f"{path_name} {mutation}: SiO report consumer "
                            f"raised {type(exc).__name__}: {exc}"
                        )
            if reader.hits == 0 and (
                _accepted_hit_disclosures_for_test(reader) != before_disclosures
            ):
                failures.append(
                    f"{path_name} {mutation}: miss changed disclosure state"
                )

    assert paths_seen > 0
    assert executed_sources == {name for name, _source in mutation_sources}
    assert not failures, "\n".join(failures)
    assert len(outcomes) == cases_expected
    assert all(
        outcome in {"miss", "admitted", "typed-outcome", "typed-rejection"}
        for outcome in outcomes.values()
    ), (
        "recursive mutation cases did not each take exactly one admission branch: "
        + "; ".join(
            f"{case}={outcome}" for case, outcome in outcomes.items()
        )
    )
    if not exhaustive_sweep:
        nonfinite_outcomes = [
            outcome
            for case, outcome in outcomes.items()
            if case.endswith(" non-finite")
        ]
        assert len(nonfinite_outcomes) == 1
        assert nonfinite_outcomes[0] in {"miss", "typed-rejection"}
    assert "miss" in outcomes.values()
    assert any(
        outcome in {"admitted", "typed-outcome"}
        for outcome in outcomes.values()
    )
    if artifact == "equilibrium_post_record":
        source_species = sorted(baseline["last_vapor_pressures_source"])[0]
        admitted_source_path = (
            "encoded-row: root.last_vapor_pressures_source"
            f"[key={source_species!r}] delete"
        )
        assert outcomes[admitted_source_path] == "admitted"
        source_reaction_path = (
            "encoded-row: "
            "root.last_vapor_pressure_diagnostic.source_reaction_fO2_bar"
        )
        assert outcomes[f"{source_reaction_path} bool"] == "miss"
        high_t_cap_path = (
            "encoded-row: "
            "root.last_vapor_pressure_diagnostic.high_t_melt_activity."
            "vaporock_cap_K wrong-scalar"
        )
        assert outcomes[high_t_cap_path] == "miss"
    else:
        admitted_step_down_path = (
            "real-gate-step-down-output: "
            "root.curve.gate_provider_step_down.failed_providers[0].status delete"
        )
        assert outcomes[admitted_step_down_path] == "admitted"
        realized_provider_path = (
            "real-gate-step-down-output: "
            "root.curve.gate_provider_step_down.realized_provider delete"
        )
        assert outcomes[realized_provider_path] == "miss"


class _MemoryInterpolationCandidates:
    def __init__(self, capture: PT0DeterminismStore) -> None:
        self.capture = capture

    def _get_with_decoded(self, **_kwargs: Any) -> None:
        return None

    def _get_by_physics_bucket(self, **_kwargs: Any) -> None:
        return None

    def _get_by_physics_ladder_bucket(self, **_kwargs: Any) -> None:
        return None

    def _list_interpolation_candidates(
        self,
        *,
        artifact: str,
        replay_scope_sha256: str,
        exclude_key_hash: str | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        rows = []
        for entry in self.capture._entries.values():
            if (
                entry.get("artifact") != artifact
                or entry.get("key_hash") == exclude_key_hash
            ):
                continue
            bucket = canonical_physics_bucket_key_from_replay_key(entry["key"])
            if rrd._replay_scope_hash(bucket) == replay_scope_sha256:
                rows.append(copy.deepcopy(entry))
        return rows


@pytest.mark.parametrize("persistent", (False, True), ids=("memory", "pt1"))
def test_real_interpolated_payload_recursive_mutations_use_nonflux_admission(
    tmp_path: Path,
    persistent: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-flux consumers retain recursive admission checks for interpolation."""
    import simulator.reduced_real_cache_interpolation as interpolation

    db_path = tmp_path / "recursive-interpolation.sqlite"
    capture = PT0DeterminismStore(
        "capture",
        db_path=db_path if persistent else None,
    )
    interpolation_pO2_bar = 1.0e-8
    _capture_recursive_equilibrium_row(
        capture,
        temperature_K=1599.0,
        commanded_pO2_bar=interpolation_pO2_bar,
    )
    _capture_recursive_equilibrium_row(
        capture,
        temperature_K=1601.0,
        commanded_pO2_bar=interpolation_pO2_bar,
    )
    production_attempt = interpolation.attempt_cached_interpolation
    query_sim, _ = _pt0_equilibrium_capture_context(capture)
    query_sim.melt.temperature_C = 1600.0 - CELSIUS_TO_KELVIN_OFFSET
    query_sim._commanded_pO2_bar = lambda: interpolation_pO2_bar
    query_sim._vapor_pressure_dispatch_pO2_bar = lambda: interpolation_pO2_bar
    query_sim.melt.fO2_log = -9.0
    if hasattr(query_sim.melt, "melt_fO2_log"):
        query_sim.melt.melt_fO2_log = -9.0
    _configure_recursive_gate_sim(query_sim, monkeypatch)
    query_key = capture._equilibrium_key(query_sim)
    assert capture._equilibrium_key(query_sim) == query_key
    if persistent:
        assert capture.persistent_store is not None
        candidates = capture.persistent_store._list_interpolation_candidates(
            artifact="equilibrium_post_record",
            replay_scope_sha256=interpolation.replay_scope_for_interpolation(
                query_key
            ),
            exclude_key_hash=_key_hash(query_key),
            query_key=query_key,
        )
    else:
        candidates = _MemoryInterpolationCandidates(capture)._list_interpolation_candidates(
            artifact="equilibrium_post_record",
            replay_scope_sha256=interpolation.replay_scope_for_interpolation(
                query_key
            ),
            exclude_key_hash=_key_hash(query_key),
        )
    attempt = production_attempt(query_key, candidates)
    if attempt is None:
        neighbors = interpolation.greedy_nearest_neighbors(query_key, candidates)
        gate = interpolation.interpolation_validity_gate(query_key, neighbors)
        weight_info = interpolation.barycentric_interpolation_weights(
            query_key,
            neighbors,
        )
        dependency_matches = [
            rrd.replay_dependency_vectors_match(query_key, candidate["key"])
            for candidate in candidates
        ]
        pytest.fail(
            "production captures did not synthesize interpolation: "
            f"candidates={len(candidates)}, neighbors={len(neighbors)}, "
            f"dependency_matches={dependency_matches}, gate={gate}, "
            f"weight_info={weight_info}"
        )
    baseline = copy.deepcopy(attempt["payload"])
    synthesized_result = baseline["equilibrium_result"]
    assert not {"phases_present", "phase_thermo"} & set(synthesized_result)
    assert rrd._INTERPOLATED_EQUILIBRIUM_REQUIRED_FIELDS <= set(
        synthesized_result
    )
    assert rrd._admission_rejection(
        "equilibrium_post_record",
        baseline,
        payload_codec_version=rrd._payload_codec_version(
            "equilibrium_post_record"
        ),
        keys=(query_key,),
        interpolation_synthesized=True,
    ) is None

    if not persistent:
        capture.persistent_store = _MemoryInterpolationCandidates(capture)
    capture.cache_tier_ceiling = "cached_interpolated"
    monkeypatch.setattr(capture, "_entry_for_physics_bucket", lambda *_a, **_k: None)
    monkeypatch.setattr(
        capture,
        "_entry_for_physics_ladder_bucket",
        lambda *_a, **_k: None,
    )
    production_admission = rrd._decode_payload_for_admission
    interpolation_admission_keys: list[tuple[Mapping[str, Any], ...]] = []

    def observe_interpolation_admission_keys(
        artifact: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        if kwargs.get("interpolation_synthesized"):
            interpolation_admission_keys.append(tuple(kwargs.get("keys", ())))
        return production_admission(artifact, payload, **kwargs)

    monkeypatch.setattr(
        rrd,
        "_decode_payload_for_admission",
        observe_interpolation_admission_keys,
    )

    def lookup_nonflux() -> EquilibriumResult | None:
        return capture._lookup_optional(
            "equilibrium_post_record",
            query_key,
            physics_bucket_key=canonical_physics_bucket_key_from_replay_key(
                query_key
            ),
            sim=query_sim,
            return_decoded=True,
            flux_consumer=False,
        )

    baseline_result = lookup_nonflux()
    assert isinstance(
        baseline_result,
        rrd._DecodedEquilibriumReplayPayload,
    )
    assert isinstance(baseline_result.result, EquilibriumResult)
    assert capture.hits == 1
    assert capture.last_cache_state == "cached_interpolated"
    capture._equilibrium_from_payload(query_sim, baseline_result)
    _exercise_sio_yield_report_diagnostic(query_sim)
    mutation_sources = [("synthesized-row", copy.deepcopy(baseline))]
    mutation_paths = [
        (source_name, mutation_baseline, path, value)
        for source_name, mutation_baseline in mutation_sources
        for path, value in _recursive_payload_paths(mutation_baseline)
    ]
    outcomes: dict[str, str] = {}
    failures: list[str] = []
    cases_expected = 0
    executed_sources: set[str] = set()
    for source_name, mutation_baseline, path, value in mutation_paths:
        executed_sources.add(source_name)
        mutations: list[tuple[str, Any]] = []
        if path:
            mutations.append(("delete", None))
        mutations.extend(
            (
                ("wrong-scalar", _recursive_mutation_value(value)),
                ("none", None),
            )
        )
        if path[-1:] == ("source_reaction_fO2_bar",):
            mutations.append(("bool", True))
        if len(path) == 2 and _is_sio_report_diagnostic_path(path):
            mutations.append(("scalar-map", 7))
        for mutation, replacement in mutations:
            cases_expected += 1
            case_name = (
                f"{source_name}: {_recursive_path_label(path)} {mutation}"
            )
            mutated_attempt = copy.deepcopy(attempt)
            mutated_attempt["payload"] = _mutate_recursive_payload(
                mutation_baseline,
                path,
                mutation,
                replacement,
            )
            monkeypatch.setattr(
                interpolation,
                "attempt_cached_interpolation",
                lambda *_a, _attempt=mutated_attempt, **_k: copy.deepcopy(
                    _attempt
                ),
            )
            capture.hits = 0
            capture.misses.clear()
            capture.replay_sequence.clear()
            capture.capture_sequence.clear()
            capture.cache_events.clear()
            capture.last_cache_state = None
            capture._accepted_hit_disclosures = None
            before_disclosures = _accepted_hit_disclosures_for_test(capture)
            result = lookup_nonflux()
            if result is None:
                outcomes[case_name] = "miss"
                if capture.hits != 0:
                    failures.append(
                        f"{case_name}: rejected interpolation changed hits"
                    )
                if capture.last_cache_state is not None:
                    failures.append(
                        f"{case_name}: rejected interpolation changed cache state"
                    )
                if capture.replay_sequence or capture.cache_events:
                    failures.append(
                        f"{case_name}: rejected interpolation emitted replay events"
                    )
                if _accepted_hit_disclosures_for_test(capture) != before_disclosures:
                    failures.append(f"{case_name}: rejected interpolation changed disclosures")
            else:
                outcomes[case_name] = "admitted"
                if not isinstance(
                    result,
                    rrd._DecodedEquilibriumReplayPayload,
                ):
                    failures.append(f"{case_name}: admitted result has wrong type")
                if capture.hits != 1:
                    failures.append(
                        f"{case_name}: admitted interpolation recorded "
                        f"{capture.hits} hits"
                    )
                if capture.last_cache_state != "cached_interpolated":
                    failures.append(
                        f"{case_name}: admitted interpolation did not record its tier"
                    )
                if len(capture.replay_sequence) != 1 or len(capture.cache_events) != 1:
                    failures.append(
                        f"{case_name}: admitted interpolation omitted replay events"
                    )
                try:
                    capture._equilibrium_from_payload(query_sim, result)
                    _exercise_sio_yield_report_diagnostic(query_sim)
                except Exception as exc:
                    failures.append(
                        f"{case_name}: SiO report consumer raised "
                        f"{type(exc).__name__}: {exc}"
                    )
            monkeypatch.setattr(
                interpolation,
                "attempt_cached_interpolation",
                production_attempt,
            )
            capture.hits = 0
            capture.misses.clear()
            capture.replay_sequence.clear()
            capture.capture_sequence.clear()
            capture.cache_events.clear()
            capture.last_cache_state = None
            capture._accepted_hit_disclosures = None
            restored_result = lookup_nonflux()
            if not isinstance(
                restored_result,
                rrd._DecodedEquilibriumReplayPayload,
            ):
                failures.append(f"{case_name}: restored interpolation missed")
            if capture.hits != 1:
                failures.append(f"{case_name}: restored interpolation hit {capture.hits} times")
            if capture.last_cache_state != "cached_interpolated":
                failures.append(f"{case_name}: restored interpolation used the wrong tier")
            if (
                len(path) == 2
                and _is_sio_report_diagnostic_path(path)
            ):
                try:
                    capture._equilibrium_from_payload(query_sim, restored_result)
                    _exercise_sio_yield_report_diagnostic(query_sim)
                except Exception as exc:
                    failures.append(
                        f"{case_name}: SiO report consumer raised "
                        f"{type(exc).__name__}: {exc}"
                    )

    assert len(outcomes) == cases_expected
    assert executed_sources == {name for name, _source in mutation_sources}
    assert outcomes
    assert set(outcomes.values()).issubset({"miss", "admitted"})
    assert "miss" in outcomes.values()
    assert "admitted" in outcomes.values()
    assert not failures, "\n".join(failures)
    assert interpolation_admission_keys
    assert all(
        len(keys) == 1
        and canonical_json_bytes(keys[0]) == canonical_json_bytes(query_key)
        for keys in interpolation_admission_keys
    )
