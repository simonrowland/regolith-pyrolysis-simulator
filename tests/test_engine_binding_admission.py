from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator import engine_binding_admission as admission
from simulator.backends import CachedRealBackend
from simulator.engine_binding_admission import (
    BindingAssessmentCandidate,
    BindingIdentity,
    EngineBindingAdmissionError,
    assess_bindings,
    assessment_candidates,
    authorize_binding,
    binding_admission_run_notice,
    install_provenance,
    live_cache_eligibility,
    version_getter_provenance,
)
from simulator.reduced_real_determinism import PT0DeterminismStore


FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_PINS = FIXTURES / "binding_admission_synthetic"
SYNTHETIC_IDENTITY = BindingIdentity(
    engine_id="synthetic-fake",
    model_id="fixture-model-v1",
    binding_revision="synthetic-r1",
    transport="subprocess",
)
SYNTHETIC_CURVE = {
    "source": "gate_liquid_fraction:authoritative:synthetic-fake",
    "solidus_T_C": 1000.0,
    "liquidus_T_C": 1300.0,
    "path": ((1000.0, 0.0), (1300.0, 1.0)),
}


def _candidate(
    identity: BindingIdentity = SYNTHETIC_IDENTITY,
    *,
    curve: dict | None = None,
    provenance: dict | None = None,
) -> BindingAssessmentCandidate:
    return BindingAssessmentCandidate(
        identity=identity,
        artifact="freeze_gate_curve",
        simulator=None,
        result=curve or SYNTHETIC_CURVE,
        provenance=provenance
        or {
            "binding_provenance_verifiable": True,
            "runtime_artifact": "test-only",
        },
    )


def _assess(tmp_path: Path, candidate: BindingAssessmentCandidate):
    receipt_path = tmp_path / "engines.local.binding-admission.json"
    assess_bindings(
        [candidate],
        pin_directory=SYNTHETIC_PINS,
        receipt_path=receipt_path,
    )
    return receipt_path


def test_synthetic_reviewed_pin_admits_matching_projection(tmp_path: Path) -> None:
    candidate = _candidate()
    receipt_path = _assess(tmp_path, candidate)
    results = assess_bindings(
        [candidate], pin_directory=SYNTHETIC_PINS, receipt_path=receipt_path
    )

    assert results[0].status == "admitted"
    admitted = authorize_binding(
        SYNTHETIC_IDENTITY,
        candidate.provenance,
        receipt_path=receipt_path,
    )
    assert BindingIdentity.from_mapping(admitted["identity"]) == SYNTHETIC_IDENTITY
    receipt = json.loads(receipt_path.read_text())
    assert receipt["entries"][0]["comparison"]["status"] == "matched"


def test_projection_mismatch_is_recorded_but_not_admitted(tmp_path: Path) -> None:
    candidate = _candidate(curve={**SYNTHETIC_CURVE, "liquidus_T_C": 1301.0})
    receipt_path = _assess(tmp_path, candidate)
    results = assess_bindings(
        [candidate], pin_directory=SYNTHETIC_PINS, receipt_path=receipt_path
    )

    assert results[0].status == "failed"
    with pytest.raises(EngineBindingAdmissionError, match="differs"):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_matching_projection_with_unverifiable_install_is_not_admitted(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        provenance={
            "binding_provenance_verifiable": False,
            "runtime_artifact": "test-only",
        }
    )
    receipt_path = _assess(tmp_path, candidate)

    results = assess_bindings(
        [candidate], pin_directory=SYNTHETIC_PINS, receipt_path=receipt_path
    )

    assert results[0].status == "failed"
    assert results[0].reason == "install provenance unverifiable"
    with pytest.raises(EngineBindingAdmissionError, match="unverifiable"):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_missing_receipt_is_a_typed_refusal(tmp_path: Path) -> None:
    with pytest.raises(EngineBindingAdmissionError, match="receipt missing"):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            {"runtime_artifact": "test-only"},
            receipt_path=tmp_path / "missing.json",
        )


@pytest.mark.parametrize("failure", ["failed", "stale", "transport-mismatched"])
def test_failed_stale_and_transport_mismatched_receipts_refuse(
    tmp_path: Path,
    failure: str,
) -> None:
    candidate = _candidate()
    receipt_path = _assess(tmp_path, candidate)
    results = assess_bindings(
        [candidate], pin_directory=SYNTHETIC_PINS, receipt_path=receipt_path
    )
    receipt = json.loads(receipt_path.read_text())
    entry = receipt["entries"][0]
    if failure == "failed":
        entry["status"] = "failed"
        entry["reason"] = "reviewed projection did not match"
    elif failure == "stale":
        entry["provenance"]["runtime_artifact"] = "different-install"
    else:
        entry["identity"]["transport"] = "python_api"
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(EngineBindingAdmissionError):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_alpha_subprocess_receipt_does_not_admit_python_api(tmp_path: Path) -> None:
    candidate = _candidate()
    receipt_path = _assess(tmp_path, candidate)
    results = assess_bindings(
        [candidate], pin_directory=SYNTHETIC_PINS, receipt_path=receipt_path
    )

    assert results[0].status == "admitted"
    with pytest.raises(EngineBindingAdmissionError, match="transport mismatch"):
        authorize_binding(
            BindingIdentity(
                engine_id="synthetic-fake",
                model_id="fixture-model-v1",
                binding_revision="synthetic-r1",
                transport="python_api",
            ),
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_missing_real_pin_reports_no_reviewed_pins_and_admits_nothing(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        BindingIdentity(
            engine_id="thermoengine",
            model_id="MELTSv1.0.2",
            binding_revision="thermoengine-r1",
            transport="native",
        )
    )
    receipt_path = tmp_path / "receipt.json"
    results = assess_bindings(
        [candidate],
        pin_directory=tmp_path / "no-pins",
        receipt_path=receipt_path,
    )

    assert results[0].status == "failed"
    assert results[0].reason == "no reviewed pins for thermoengine/native"
    with pytest.raises(EngineBindingAdmissionError, match="no reviewed pins"):
        authorize_binding(
            candidate.identity,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_failed_version_getter_records_typed_unknown_not_unavailable() -> None:
    state = version_getter_provenance(
        lambda: (_ for _ in ()).throw(RuntimeError("getter failed"))
    )

    assert state == {
        "tag": "unknown",
        "value": None,
        "reason": "version getter failed: RuntimeError",
    }
    assert state["value"] != "unavailable"


def test_install_provenance_binds_alpha_subprocess_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_local_config

    monkeypatch.setattr(
        engine_local_config,
        "config_path",
        lambda: tmp_path / "engines.local.toml",
    )
    monkeypatch.delenv("ALPHAMELTS_CALC_MODE", raising=False)
    identity = BindingIdentity(
        "alphamelts",
        "MELTSv1.0.2",
        "alphamelts-r1",
        "subprocess",
    )
    initial = install_provenance(identity)["runtime_environment"]
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "conflicting-mode")
    changed = install_provenance(identity)["runtime_environment"]

    assert initial["sha256"] != changed["sha256"]


def test_cached_real_admission_lifecycle_clears_and_publishes_after_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = {"binding_provenance_verifiable": True, "runtime": "test"}
    authorized = True

    class LiveBackend:
        initialized = True
        available = True

        def initialize(self, _config=None):
            return self.initialized

        def is_available(self):
            return self.available

        def get_engine_version(self):
            return "synthetic-fake test"

    def authorize(identity, _provenance):
        if not authorized:
            raise EngineBindingAdmissionError(identity, "receipt stale")
        return {"status": "admitted"}

    monkeypatch.setattr(
        admission,
        "binding_identity_for_cached_real",
        lambda _config: SYNTHETIC_IDENTITY,
    )
    monkeypatch.setattr(
        admission,
        "cached_real_provenance",
        lambda _config, _backend: provenance,
    )
    monkeypatch.setattr(admission, "authorize_binding", authorize)
    live = LiveBackend()
    facade = CachedRealBackend(
        config=SimpleNamespace(
            miss_policy="live-fill",
            authorized_backend_family=SimpleNamespace(name="ALPHAMELTS"),
        ),
        live_backend=live,
    )

    assert facade._admitted_bindings == {}
    live.initialized = False
    assert not facade.initialize()
    assert facade._admitted_bindings == {}

    live.initialized = True
    assert facade.initialize()
    assert facade._admitted_bindings == {
        SYNTHETIC_IDENTITY.producer_transport: admission.admission_fingerprint(
            SYNTHETIC_IDENTITY,
            provenance,
        )
    }

    live.initialized = False
    assert not facade.initialize()
    assert facade._admitted_bindings == {}

    live.initialized = True
    authorized = False
    assert facade.initialize()
    assert facade._admitted_bindings == {}
    assert facade._binding_admission_errors[
        SYNTHETIC_IDENTITY.producer_transport
    ].reason == "receipt stale"


def test_direct_replay_only_constructor_uses_receipt_authorizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def authorize(identity, provenance):
        calls.append((identity, provenance))
        return {"status": "admitted"}

    monkeypatch.setattr(
        admission,
        "binding_identity_for_cached_real",
        lambda _config: SYNTHETIC_IDENTITY,
    )
    monkeypatch.setattr(
        admission,
        "cached_real_provenance",
        lambda _config, _backend: {"binding_provenance_verifiable": True},
    )
    monkeypatch.setattr(admission, "authorize_binding", authorize)

    facade = CachedRealBackend(
        config=SimpleNamespace(
            miss_policy="fail-loud",
            authorized_backend_family=SimpleNamespace(name="ALPHAMELTS"),
        ),
        live_backend=None,
    )

    assert calls == [(SYNTHETIC_IDENTITY, {"binding_provenance_verifiable": True})]
    assert SYNTHETIC_IDENTITY.producer_transport in facade._admitted_bindings


def test_gate_replay_skips_unadmitted_optional_fallback_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.reduced_real_determinism as replay

    monkeypatch.setattr(
        replay,
        "_gate_provider_roles_for_replay",
        lambda _sim: ("authoritative", "fallback"),
    )
    monkeypatch.setattr(
        replay,
        "canonical_replay_key",
        lambda _sim, **kwargs: {"provider_role": kwargs["provider_role"]},
    )
    store = PT0DeterminismStore("replay", db_path=tmp_path / "unused.db")
    authorized_roles = []
    authorized_artifacts = []
    lookup_keys = []

    def authorize(_sim, *, provider_role=None, artifact=None):
        authorized_roles.append(provider_role)
        authorized_artifacts.append(artifact)
        if provider_role == "fallback":
            raise EngineBindingAdmissionError(
                SYNTHETIC_IDENTITY,
                "receipt missing",
            )

    def lookup(_artifact, keys, *, sim):
        lookup_keys.extend(keys)
        return {
            "curve": {
                "source": "gate_liquid_fraction",
                "solidus_T_C": 1000.0,
                "liquidus_T_C": 1300.0,
                "path": [],
            }
        }

    monkeypatch.setattr(store, "_authorize_cached_real_replay", authorize)
    monkeypatch.setattr(store, "_lookup_first_available", lookup)
    sim = SimpleNamespace()

    result = store.replay_gate_curve(sim, fO2_log=-10.0)

    assert result["liquidus_T_C"] == 1300.0
    assert authorized_roles == ["authoritative", "fallback"]
    assert authorized_artifacts == ["freeze_gate_curve", "freeze_gate_curve"]
    assert lookup_keys == [{"provider_role": "authoritative"}]


def test_live_cache_gate_fails_open_and_deduplicates_typed_notice(
    tmp_path: Path,
) -> None:
    sim = SimpleNamespace()
    missing_receipt = tmp_path / "missing.json"

    assert not live_cache_eligibility(
        sim,
        SYNTHETIC_IDENTITY,
        {"runtime_artifact": "test-only"},
        receipt_path=missing_receipt,
    )
    assert not live_cache_eligibility(
        sim,
        SYNTHETIC_IDENTITY,
        {"runtime_artifact": "test-only"},
        receipt_path=missing_receipt,
    )

    notice = binding_admission_run_notice(sim)
    assert notice is not None
    assert len(notice["notices"]) == 1
    assert notice["notices"][0]["type"] == "typed_notice"
    assert notice["notices"][0]["message"] == (
        "binding not admitted on this host (receipt missing); replay and "
        "capture disabled; run scripts/assess_engine_bindings.py"
    )

    other_transport = BindingIdentity(
        engine_id=SYNTHETIC_IDENTITY.engine_id,
        model_id=SYNTHETIC_IDENTITY.model_id,
        binding_revision=SYNTHETIC_IDENTITY.binding_revision,
        transport="python_api",
    )
    assert not live_cache_eligibility(
        sim,
        other_transport,
        {"runtime_artifact": "test-only"},
        receipt_path=missing_receipt,
    )
    notice = binding_admission_run_notice(sim)
    assert notice is not None
    assert len(notice["notices"]) == 2


@pytest.mark.parametrize("failure", ["failed", "stale", "transport-mismatched"])
def test_live_cache_gate_refuses_failed_stale_and_wrong_transport_receipts(
    tmp_path: Path,
    failure: str,
) -> None:
    candidate = _candidate()
    receipt_path = _assess(tmp_path, candidate)
    receipt = json.loads(receipt_path.read_text())
    entry = receipt["entries"][0]
    if failure == "failed":
        entry["status"] = "failed"
        entry["reason"] = "reviewed projection did not match"
    elif failure == "stale":
        entry["provenance"]["runtime_artifact"] = "different-install"
    else:
        entry["identity"]["transport"] = "python_api"
    receipt_path.write_text(json.dumps(receipt))

    sim = SimpleNamespace()
    assert not live_cache_eligibility(
        sim,
        SYNTHETIC_IDENTITY,
        candidate.provenance,
        receipt_path=receipt_path,
    )
    notice = binding_admission_run_notice(sim)
    assert notice is not None
    assert len(notice["notices"]) == 1
    assert notice["notices"][0]["kind"] == "engine_binding_not_admitted"
    expected_reason = {
        "failed": "reviewed projection did not match",
        "stale": "receipt stale",
        "transport-mismatched": "transport mismatch",
    }[failure]
    assert notice["notices"][0]["reason"] == expected_reason
    assert expected_reason in notice["notices"][0]["message"]


def test_real_assessment_targets_fail_closed_without_reviewed_pins(
    tmp_path: Path,
) -> None:
    candidates = assessment_candidates(tmp_path / "empty-real-pins")
    receipt_path = tmp_path / "receipt.json"

    results = assess_bindings(
        candidates,
        pin_directory=tmp_path / "empty-real-pins",
        receipt_path=receipt_path,
    )
    receipt = json.loads(receipt_path.read_text())

    assert len(results) == 9
    assert all(result.status == "failed" for result in results)
    assert all(
        result.reason.startswith("no reviewed pins for ")
        for result in results
    )
    assert len(receipt["entries"]) == 7
    assert all(entry["status"] == "failed" for entry in receipt["entries"])


def test_equilibrium_binding_producers_follow_input_selected_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.reduced_real_determinism as replay

    monkeypatch.setattr(
        replay,
        "record_dependency_vector",
        lambda *_args, **_kwargs: {
            "vapor_pressure_provider_selection": "builtin-vapor-pressure",
            "sulfur_side": {"S_input_ppm": 12.0},
        },
    )
    sim = SimpleNamespace(
        setpoints={"high_t_melt_activity": " OpenIMCC "},
        melt=SimpleNamespace(temperature_C=2000.0),
        _sulfsat_gate=object(),
    )

    from simulator.reduced_real_determinism import record_binding_producer_ids

    assert record_binding_producer_ids(
        sim,
        artifact="equilibrium_post_record",
    ) == ("builtin-vapor-pressure", "openimcc", "sulfsat")
    assert record_binding_producer_ids(
        SimpleNamespace(
            setpoints={"high_t_melt_activity": "openimcc"},
            melt=SimpleNamespace(temperature_C=1000.0),
            _sulfsat_gate=object(),
        ),
        artifact="equilibrium_post_record",
    ) == ("builtin-vapor-pressure", "sulfsat")
    assert record_binding_producer_ids(
        sim,
        artifact="freeze_gate_curve",
    ) == ()


def test_missing_selected_contributor_disables_live_cache_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.reduced_real_determinism as replay
    from tests.binding_admission_fixtures import install_synthetic_binding_receipt

    install_synthetic_binding_receipt(
        tmp_path,
        monkeypatch,
        bind_direct_backend=True,
    )
    monkeypatch.setattr(
        replay,
        "record_binding_producer_ids",
        lambda _sim, *, artifact, **_kwargs: (
            ("builtin-vapor-pressure", "openimcc", "sulfsat")
            if artifact == "equilibrium_post_record"
            else ()
        ),
    )
    backend = SimpleNamespace(
        name="synthetic-fake",
        get_engine_version=lambda: "fixture-runtime",
    )
    sim = SimpleNamespace(backend=backend)
    admission.publish_live_backend_admission(backend)

    assert not admission.live_binding_cache_eligibility(
        sim,
        artifact="equilibrium_post_record",
    )
    notice = binding_admission_run_notice(sim)
    assert notice is not None
    assert [
        item["identity"]["engine_id"] for item in notice["notices"]
    ] == ["builtin-vapor-pressure", "openimcc", "sulfsat"]
    assert not admission.live_binding_cache_eligibility(
        sim,
        artifact="equilibrium_post_record",
    )
    assert len(binding_admission_run_notice(sim)["notices"]) == 3

    with pytest.raises(
        EngineBindingAdmissionError,
        match="builtin-vapor-pressure/native",
    ):
        admission.authorize_sim_binding_replay(
            sim,
            artifact="equilibrium_post_record",
        )


def test_openimcc_selected_fallback_is_not_captured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.reduced_real_determinism as replay
    from simulator.chemistry.kernel import ChemistryIntent

    monkeypatch.setattr(
        replay,
        "record_binding_producer_ids",
        lambda *_args, **_kwargs: ("openimcc",),
    )
    monkeypatch.setattr(
        replay,
        "_is_cacheable_equilibrium_result",
        lambda _result: True,
    )
    monkeypatch.setattr(
        replay,
        "_equilibrium_payload_intent",
        lambda _sim: ChemistryIntent.BACKEND_EQUILIBRIUM,
    )
    monkeypatch.setattr(
        replay,
        "canonical_replay_output_projection",
        lambda *_args: {"synthetic": "payload"},
    )
    monkeypatch.setattr(
        replay,
        "_engine_version_provenance",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        replay,
        "_repair_notices_for_capture",
        lambda *_args, **_kwargs: None,
    )
    store = PT0DeterminismStore("capture", db_path=tmp_path / "unused.db")
    monkeypatch.setattr(
        store,
        "_cached_real_binding_eligible",
        lambda _sim: True,
    )
    monkeypatch.setattr(store, "_equilibrium_key", lambda _sim: {})
    captures = []
    monkeypatch.setattr(
        store,
        "_store",
        lambda *args, **kwargs: captures.append((args, kwargs)),
    )
    sim = SimpleNamespace(
        _last_vapor_pressure_diagnostic={
            "high_t_melt_activity": {
                "provider": "constant_gamma",
                "fallback": True,
            }
        }
    )

    store.capture_equilibrium(sim, SimpleNamespace())

    assert captures == []
    assert sim._last_reduced_real_cache_state is None
