"""Contract tests for the OpenIMCC SP-extension battery arm."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum
import hashlib
import json
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    ExecutionState,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
)
from simulator.battery.oxygen_balance import (
    IMCC_ENGINES,
    OXYGEN_BALANCE_EFFUSION_ENGINES,
)
from simulator.battery.records import Composition, Execution
from simulator.battery.score import (
    ENGINE_COEFFICIENT_SOURCES,
    ENGINE_CHANNELS,
    EnginePrediction,
    SCORE_ENGINE_SET,
    candidate_observation,
    cell_notices,
    compile_residual,
    load_score_context,
    predict_with_engine,
    residual_key,
    _input_refusal,
)
from simulator.battery.source_lineage import coefficient_lineage_sources
from simulator.battery.waypoints import ENGINE_POINT_CONSUMERS, MELT_ACTIVITY_ENGINES
from simulator.diagnostic_helpers.binary_pot_battery import (
    _ENGINE_OUTER_TIMEOUT_S,
    BATTERY_ENGINE_NAMES,
    OPENIMCC_ENGINE_NAMES,
)
from simulator.battery.pins import PIN_CHANNEL_ENGINES, PinChannel
from tests.battery import factories as F


_LI_2005_FEO_ROW = (
    "li-2005-na-al-phosphate-fe-activities::"
    "li_2005_table_1_feo_activity_coefficient::T=1823:row=1-1:h=946adbc5ed6a"
)
_HASHIMOTO_GREEN_RESIDUE_OBSERVATION_ID = (
    "kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series::"
    "T=1973.15:row=c3-(2):h=26a09cbd7ac5"
)
_SP_PACK_SOURCE = "openimcc-pack-id:d066-ext"
_OPENIMCC_GREEN_PIN_EXCLUDED_VOLATILE_PATHS = (
    "version",
    "refusal_detail.code_revision",
    "refusal_detail.integration.process_cpu_seconds",
    "refusal_detail.integration.process_cpu_seconds_by_geometry",
    "refusal_detail.production_alpha_arm.provenance.code_revision",
    (
        "refusal_detail.production_alpha_arm.provenance.integration."
        "process_cpu_seconds"
    ),
    (
        "refusal_detail.production_alpha_arm.provenance.integration."
        "process_cpu_seconds_by_geometry"
    ),
)
_OPENIMCC_GREEN_PIN_NORMALIZED_VOLATILE_PATHS = (
    "execution.call_evidence.host",
)
_OPENIMCC_GREEN_PREDICTION_SHA256 = {
    "activity": "b282c105c8a766c2a4f2493a8bc63db4d9b7931643619e7d2c1b5c6c78e9ec60",
    "residue": "794d18de88174b14f28d12617d163c9e863e6261abd3fe44e6a13e8a17994925",
    "mass_loss": "c3acd11cfc46b5067fba2de6c401c7cf7a661ccc36f77f923d1dca190358676a",
}


def _pin_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return {
            item.name: _pin_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _pin_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = value if not isinstance(value, (set, frozenset)) else sorted(value)
        return [_pin_value(item) for item in items]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _drop_pin_path(value: dict[str, object], dotted_path: str) -> None:
    parts = dotted_path.split(".")
    current: object = value
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _normalize_pin_host(pin: dict[str, object]) -> None:
    execution = pin.get("execution")
    if not isinstance(execution, dict):
        return
    call_evidence = execution.get("call_evidence")
    if not isinstance(call_evidence, str):
        return
    before_host, marker, after_host = call_evidence.partition("host=")
    if not marker:
        return
    _host, separator, after_separator = after_host.partition(":")
    execution["call_evidence"] = (
        before_host + "host=<host>" + separator + after_separator
    )


def _openimcc_prediction_pin(prediction) -> dict[str, object]:
    pin = _pin_value(prediction)
    assert isinstance(pin, dict)
    for volatile_path in _OPENIMCC_GREEN_PIN_EXCLUDED_VOLATILE_PATHS:
        _drop_pin_path(pin, volatile_path)
    for volatile_path in _OPENIMCC_GREEN_PIN_NORMALIZED_VOLATILE_PATHS:
        if volatile_path == "execution.call_evidence.host":
            _normalize_pin_host(pin)
    return pin


def _sp_engine() -> Engine:
    return next(engine for engine in SCORE_ENGINE_SET if engine.value == "openimcc-sp")


@pytest.fixture(scope="module")
def _li_2005_score_context():
    return load_score_context(sources=("li-2005-na-al-phosphate-fe-activities",))


def _assert_openimcc_prediction_matches_green_baseline(name: str, prediction) -> None:
    pin = _openimcc_prediction_pin(prediction)
    actual_sha256 = hashlib.sha256(
        json.dumps(pin, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert actual_sha256 == _OPENIMCC_GREEN_PREDICTION_SHA256[name]


def test_openimcc_sp_is_registered_as_a_distinct_battery_engine() -> None:
    engine = _sp_engine()

    assert engine is not Engine.OPENIMCC
    assert ENGINE_CHANNELS[engine] == "openimcc-sp"
    assert engine in IMCC_ENGINES
    assert engine in OXYGEN_BALANCE_EFFUSION_ENGINES
    assert "openimcc-sp" in BATTERY_ENGINE_NAMES
    assert "openimcc-sp" in OPENIMCC_ENGINE_NAMES
    assert _ENGINE_OUTER_TIMEOUT_S["openimcc-sp"] == _ENGINE_OUTER_TIMEOUT_S[
        "openimcc"
    ]
    assert "openimcc-sp" in ENGINE_POINT_CONSUMERS
    assert "openimcc-sp" in MELT_ACTIVITY_ENGINES
    assert PIN_CHANNEL_ENGINES[PinChannel.OPENIMCC_SP] is engine
    assert residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=engine,
        rail=Rail.MELT_ACTIVITY,
    ) == "obs-1::activity::melt_activity::openimcc-sp"


def test_openimcc_activity_prediction_matches_green_baseline() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")

    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("K2O", Decimal("0.5")), ("SiO2", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    activity_experiment = F.tabulation_experiment(
        "t1577-openimcc-activity", T_K=Decimal("1800"), total_P=Decimal("100000")
    )
    activity_reference = F.observation(
        "t1577-openimcc-activity",
        activity_experiment.experiment_id,
        F.activity_identity(
            formula="SiO2",
            component_basis="SiO2",
            T_K=Decimal("1800"),
            composition=composition,
        ),
        Decimal("0.5"),
        source_id="t1577-probe",
    )
    activity_prediction = predict_with_engine(
        Engine.OPENIMCC,
        activity_reference,
        experiment=activity_experiment,
        isolated=False,
    )
    _assert_openimcc_prediction_matches_green_baseline(
        "activity", activity_prediction
    )


def test_openimcc_residue_prediction_matches_green_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    import simulator.battery.residue as residue
    from simulator.battery.score import _hashimoto_residue_prediction

    residue_context = load_score_context(sources=("kems-015-hashimoto-1983",))
    residue_reference = residue_context.observations[
        _HASHIMOTO_GREEN_RESIDUE_OBSERVATION_ID
    ]
    original_predict_cohort = residue._predict_hashimoto_residue_cohort

    def predict_reference_run_only(experiments, runtime_catalog, **kwargs):
        reference_experiment = next(
            experiment
            for experiment in experiments
            if experiment["experiment_id"] == residue_reference.experiment_id
        )
        first_sample = experiments[0]["sample"]
        reference_sample = reference_experiment["sample"]
        assert first_sample["mass_kg"] == reference_sample["mass_kg"]
        assert (
            first_sample["printed_composition"]["state"]["value"]
            == reference_sample["printed_composition"]["state"]["value"]
        )
        return original_predict_cohort(
            [reference_experiment],
            runtime_catalog,
            _allow_partial_cohort_for_test=True,
            **kwargs,
        )

    monkeypatch.setattr(
        residue, "_predict_hashimoto_residue_cohort", predict_reference_run_only
    )
    residue_prediction = _hashimoto_residue_prediction(
        residue_context, residue_reference, Engine.OPENIMCC, {}
    )
    _assert_openimcc_prediction_matches_green_baseline(
        "residue", residue_prediction
    )


def test_openimcc_mass_loss_prediction_matches_green_baseline() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    mass_loss_context = load_score_context(
        sources=("mendybaev-2017-fun-cai-lab-evaporation",)
    )
    mass_loss_reference = next(
        row
        for row in mass_loss_context.observations.values()
        if row.source_id == "mendybaev-2017-fun-cai-lab-evaporation"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.experiment_id.endswith("::func-5")
    )
    mass_loss_prediction = predict_with_engine(
        Engine.OPENIMCC,
        mass_loss_reference,
        experiment=mass_loss_context.experiments[mass_loss_reference.experiment_id],
        isolated=False,
    )
    _assert_openimcc_prediction_matches_green_baseline(
        "mass_loss", mass_loss_prediction
    )


def test_sp_pack_id_is_provenance_not_a_coefficient_ancestor() -> None:
    assert coefficient_lineage_sources(
        ("openimcc-pack-id:d066-ext", "openimcc-v1.0.3-d066-v1")
    ) == ("openimcc-v1.0.3-d066-v1",)


def test_openimcc_sp_selects_the_extension_paired_with_default_d066() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )
    from simulator.melt_backend import openimcc_bridge

    default = _OpenImccBatteryBackend(Engine.OPENIMCC.value)
    sp_extension = _OpenImccBatteryBackend(Engine.OPENIMCC_SP.value)

    assert default._pack_name == openimcc_bridge.DEFAULT_BACKEND_PACK_NAME
    assert sp_extension._pack_name == "d066-ext"
    assert (
        sp_extension._pack_name
        == openimcc_bridge.OPENIMCC_SP_EXTENSION_PACK_NAME
    )


def test_li_2005_p2o5_feo_row_uses_the_extension_pack(
    monkeypatch: pytest.MonkeyPatch,
    _li_2005_score_context,
) -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.melt_backend import openimcc_bridge

    context = _li_2005_score_context
    reference = context.observations[_LI_2005_FEO_ROW]
    composition = reference.identity.composition.value
    assert any(name == "P2O5" and amount > 0 for name, amount in composition.components)

    bridge_calls: list[dict[str, object]] = []
    package_calls: list[dict[str, object]] = []
    evaluate_bridge = openimcc_bridge.evaluate
    package = openimcc_bridge._require_openimcc()
    evaluate_package = package.evaluate

    def capture_bridge(**kwargs):
        bridge_calls.append(dict(kwargs))
        return evaluate_bridge(**kwargs)

    def capture_package(*args, **kwargs):
        package_calls.append({"args": args, "kwargs": dict(kwargs)})
        return evaluate_package(*args, **kwargs)

    monkeypatch.setattr(openimcc_bridge, "evaluate", capture_bridge)
    monkeypatch.setattr(package, "evaluate", capture_package)

    default = predict_with_engine(Engine.OPENIMCC, reference, isolated=False)
    assert default.value is None
    assert default.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert default.refusal_detail["engine_reason"] == "imcc_sp_extension_required"

    prediction = predict_with_engine(_sp_engine(), reference, isolated=False)
    assert bridge_calls[-1]["pack"] == "d066-ext"
    assert package_calls[-1]["kwargs"]["enable_sp_extension"] is True
    candidate = candidate_observation(reference, prediction)
    assert candidate.engine is not None
    assert _SP_PACK_SOURCE in candidate.engine.coefficient_sources
    assert prediction.value is not None
    assert str(prediction.value) == "1.1729198290793348"
    assert prediction.refusal_reason is None
    assert prediction.authority is Authority.EXTRAPOLATED
    assert candidate.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and notice.affected_quantities == (Quantity.ACTIVITY_COEFFICIENT,)
        and notice.reason == "openimcc-pack-id:d066-ext; authority=extrapolated"
        and notice.origin == "engine:openimcc-sp"
        and notice.authority is Authority.EXTRAPOLATED
        for notice in prediction.notices
    )
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.affected_quantities == (Quantity.ACTIVITY_COEFFICIENT,)
        and notice.authority is Authority.EXTRAPOLATED
        and notice.reason
        == (
            "P2O5 parent reference state: crystal-reference screening proxy; "
            "no P2O5(l) claim"
        )
        and notice.origin == "engine:openimcc-sp"
        for notice in prediction.notices
    )
    assert {
        NoticeKind.OUT_OF_CERTIFIED_BAND,
        NoticeKind.IMCC_COMPLEX_SATURATION,
    } <= {notice.kind for notice in prediction.notices}
    residual, _ = compile_residual(
        reference,
        _sp_engine(),
        context=context,
        prediction=prediction,
    )
    assert residual.key.endswith(
        f"::melt_activity::{_sp_engine().value}::fo2_mode=shared_ladder"
    )
    assert any(
        _SP_PACK_SOURCE in notice.reason
        and f"authority={candidate.authority.value}" in notice.reason
        for notice in residual.notices
    )
    gated_residual, gated_candidate = compile_residual(
        reference,
        _sp_engine(),
        context=replace(context, experiments={}),
    )
    assert gated_candidate is None
    assert not any(
        notice.reason.startswith(_SP_PACK_SOURCE)
        for notice in gated_residual.notices
    )


def test_openimcc_sp_outside_validated_envelope_is_flagged() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("K2O", Decimal("0.50001")),
            ("SiO2", Decimal("0.49999")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula="SiO2",
        component_basis="SiO2",
        composition=composition,
        T_K=Decimal("1800"),
    )
    reference = F.observation(
        "openimcc-sp-outside-validated-envelope",
        F.tabulation_experiment().experiment_id,
        identity,
        Decimal("1"),
    )

    prediction = predict_with_engine(_sp_engine(), reference, isolated=False)
    candidate = candidate_observation(reference, prediction)
    assert candidate.engine is not None
    assert _SP_PACK_SOURCE in candidate.engine.coefficient_sources
    assert prediction.value is not None
    assert candidate.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.authority is Authority.EXTRAPOLATED
        and notice.reason
        == (
            "X_Me2O outside openimcc validated envelope; "
            "evaluate(allow_out_of_envelope=True)"
        )
        for notice in prediction.notices
    )


def test_openimcc_composition_domain_authority_is_sp_only() -> None:
    raw_notice = {
        "kind": "openimcc_composition_outside_validated_envelope",
        "authority": "extrapolated",
        "reason": (
            "X_Me2O outside openimcc validated envelope; "
            "evaluate(allow_out_of_envelope=True)"
        ),
    }
    cell = SimpleNamespace(notices=(raw_notice,))

    sp_notice = next(
        notice
        for notice in cell_notices(
            Quantity.ACTIVITY_COEFFICIENT, Engine.OPENIMCC_SP, cell
        )
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    openimcc_notice = next(
        notice
        for notice in cell_notices(
            Quantity.ACTIVITY_COEFFICIENT, Engine.OPENIMCC, cell
        )
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )

    assert sp_notice.reason == raw_notice["reason"]
    assert sp_notice.authority is Authority.EXTRAPOLATED
    assert openimcc_notice.reason == raw_notice["reason"]
    assert openimcc_notice.authority is None


def test_openimcc_sp_pack_notice_tracks_evaluation_state(
    monkeypatch: pytest.MonkeyPatch,
    _li_2005_score_context,
) -> None:
    context = _li_2005_score_context
    reference = context.observations[_LI_2005_FEO_ROW]
    identity = reference.identity
    assert identity.composition is not None
    engine = _sp_engine()

    not_probed = _input_refusal(
        engine=engine,
        channel=ENGINE_CHANNELS[engine],
        sources=ENGINE_COEFFICIENT_SOURCES[engine],
        identity=identity,
        requested=identity.composition,
        reason=RefusalReason.IDENTITY_UNKNOWN,
        detail={"reason": "temperature_unknown"},
    )
    assert not_probed.execution.state is ExecutionState.NOT_PROBED
    assert not any(
        notice.reason.startswith(
            (_SP_PACK_SOURCE, "P2O5 parent reference state:")
        )
        for notice in not_probed.notices
    )

    unsupported = EnginePrediction(
        engine=engine,
        channel=ENGINE_CHANNELS[engine],
        execution=Execution(state=ExecutionState.UNSUPPORTED),
        refusal_reason=RefusalReason.UNSUPPORTED,
        identity=identity,
    )
    assert not any(
        notice.reason.startswith(_SP_PACK_SOURCE)
        for notice in unsupported.notices
    )

    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.melt_backend import openimcc_bridge

    handle = binary_pot_battery.open_battery_engine(engine.value)
    assert handle.available
    assert handle.backend._pack_name == "d066-ext"
    bridge_calls: list[dict[str, object]] = []
    original_bridge_evaluate = openimcc_bridge.evaluate

    def record_bridge_evaluate(**kwargs):
        bridge_calls.append(dict(kwargs))
        return original_bridge_evaluate(**kwargs)

    monkeypatch.setattr(openimcc_bridge, "evaluate", record_bridge_evaluate)
    original_equilibrate_cell = binary_pot_battery.equilibrate_cell

    def fail_after_evaluation(*args, **kwargs):
        cell = original_equilibrate_cell(*args, **kwargs)
        assert cell.status == "ok"
        raise RuntimeError("forced failure after successful SP evaluation")

    monkeypatch.setattr(
        binary_pot_battery, "equilibrate_cell", fail_after_evaluation
    )
    attempted_unavailable = predict_with_engine(
        engine,
        reference,
        handles={engine.value: handle},
        isolated=False,
    )
    assert (
        attempted_unavailable.execution.state
        is ExecutionState.ATTEMPTED_UNAVAILABLE
    )
    assert (
        attempted_unavailable.execution.call_evidence
        == "equilibrate_cell:openimcc-sp:exception"
    )
    assert bridge_calls
    assert bridge_calls[-1]["pack"] == "d066-ext"

    pack_notices = tuple(
        notice
        for notice in attempted_unavailable.notices
        if notice.reason == f"{_SP_PACK_SOURCE}; authority=refused"
    )
    assert len(pack_notices) == 1
    assert pack_notices[0].authority is Authority.REFUSED
    assert not any(
        notice.reason.startswith("P2O5 parent reference state:")
        for notice in attempted_unavailable.notices
    )
    residual, candidate = compile_residual(
        reference,
        engine,
        context=context,
        prediction=attempted_unavailable,
    )
    assert candidate is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert any(
        notice.reason == f"{_SP_PACK_SOURCE}; authority=refused"
        for notice in residual.notices
    )


def test_openimcc_sp_residue_domain_status_becomes_a_scored_notice() -> None:
    from simulator.battery.score import _residue_domain_notices

    notices = _residue_domain_notices(
        Engine.OPENIMCC_SP,
        {
            "engine_flags": (
                "openimcc_extrapolated",
                "composition_outside_validated_envelope",
            ),
            "engine_metadata": {
                "envelope_status": "outside_validated",
                "extrapolated": "true",
            },
        },
    )

    assert notices
    assert all(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.authority is Authority.EXTRAPOLATED
        for notice in notices
    )
    assert any("outside_validated" in notice.reason for notice in notices)
    assert any("openimcc_extrapolated" in notice.reason for notice in notices)
    assert _residue_domain_notices(
        Engine.OPENIMCC,
        {
            "engine_flags": ("openimcc_extrapolated",),
            "engine_metadata": {"envelope_status": "outside_validated"},
        },
    ) == ()


def test_openimcc_sp_residue_provenance_keeps_the_bridge_domain_result() -> None:
    from simulator.battery.residue import (
        _openimcc_residue_state_provenance,
        _record_openimcc_residue_state,
    )

    state = SimpleNamespace(
        flags=("composition_outside_validated_envelope",),
        extrapolated=True,
        envelope_status="outside_validated",
        pack_model_id="openimcc-test-model",
        pack_version="test-version",
        pack_digest="test-digest",
        openimcc_version="test-openimcc-version",
    )
    flags: set[str] = set()
    metadata: dict[str, str] = {}

    _record_openimcc_residue_state(
        Engine.OPENIMCC_SP.value,
        state,
        pack_name="d066-ext",
        engine_metadata=metadata,
        engine_flags=flags,
    )
    provenance = _openimcc_residue_state_provenance(
        Engine.OPENIMCC_SP.value, flags, metadata
    )

    assert metadata["pack"] == "d066-ext"
    assert metadata["envelope_status"] == "outside_validated"
    assert metadata["extrapolated"] == "true"
    assert "composition_outside_validated_envelope" in flags
    assert "openimcc_extrapolated" in flags
    assert "openimcc_envelope_status:outside_validated" in flags
    assert provenance["engine_flags"] == tuple(sorted(flags))
    assert provenance["engine_metadata"] == metadata


def test_openimcc_sp_residue_worker_refusal_names_the_sp_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery import residue

    class Worker:
        def is_alive(self) -> bool:
            return True

    class Connection:
        def send(self, _payload) -> None:
            pass

        def poll(self, _timeout) -> bool:
            return True

        def recv(self):
            return False, "ImccNonconvergenceError", "forced SP nonconvergence"

    def evaluate(**_kwargs):
        raise AssertionError("the cached worker response should be used")

    monkeypatch.setattr(
        residue,
        "_OPENIMCC_EVALUATION_WORKER",
        (Worker(), Connection(), evaluate),
    )
    with pytest.raises(residue.ResidueEngineNonconvergence) as raised:
        residue._evaluate_openimcc_isolated(
            evaluate,
            timeout_s=1.0,
            engine_name=Engine.OPENIMCC_SP.value,
            temperature_K=1800.0,
        )

    assert raised.value.engine == Engine.OPENIMCC_SP.value
    assert raised.value.reason == "engine_nonconvergence"
