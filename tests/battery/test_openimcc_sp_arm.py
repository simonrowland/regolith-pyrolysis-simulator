"""Contract tests for the OpenIMCC SP-extension battery arm."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from collections.abc import Mapping

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
)
from simulator.battery.oxygen_balance import (
    IMCC_ENGINES,
    OXYGEN_BALANCE_EFFUSION_ENGINES,
)
from simulator.battery.records import Composition
from simulator.battery.score import (
    ENGINE_CHANNELS,
    SCORE_ENGINE_SET,
    candidate_observation,
    compile_residual,
    load_score_context,
    predict_with_engine,
    residual_key,
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
_SP_PACK_SOURCE = "openimcc-pack-id:d066-ext"


def _pin_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _pin_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_pin_value(item) for item in value]
    return value


def _openimcc_prediction_pin(prediction) -> dict[str, object]:
    return {
        "value": _pin_value(prediction.value),
        "authority": _pin_value(prediction.authority),
        "notices": [
            {
                "kind": notice.kind.value,
                "affected_quantities": _pin_value(notice.affected_quantities),
                "reason": notice.reason,
                "origin": notice.origin,
                "original": _pin_value(notice.original),
                "source": notice.source,
                "destination": notice.destination,
                "band": notice.band,
                "dropped": _pin_value(notice.dropped),
                "dropped_mass_fraction": _pin_value(notice.dropped_mass_fraction),
                "authority": _pin_value(notice.authority),
                "certification": notice.certification,
                "fo2_mode": _pin_value(notice.fo2_mode),
            }
            for notice in prediction.notices
        ],
        "refusal": (
            None
            if prediction.refusal_reason is None
            else {
                "reason": prediction.refusal_reason.value,
                "detail": _pin_value(prediction.refusal_detail),
            }
        ),
    }


def _sp_engine() -> Engine:
    return next(engine for engine in SCORE_ENGINE_SET if engine.value == "openimcc-sp")


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


def test_openimcc_value_authority_notices_and_refusal_match_green_baseline() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.battery.score import (
        _hashimoto_residue_prediction,
        quantity_token,
    )

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

    residue_context = load_score_context(sources=("kems-015-hashimoto-1983",))
    residue_reference = next(
        row
        for row in residue_context.observations.values()
        if row.source_id == "kems-015-hashimoto-1983"
        and row.identity.species.formula == "FeO"
        and quantity_token(row.identity)
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    residue_prediction = _hashimoto_residue_prediction(
        residue_context, residue_reference, Engine.OPENIMCC, {}
    )

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

    actual = {
        "activity": _openimcc_prediction_pin(activity_prediction),
        "residue": _openimcc_prediction_pin(residue_prediction),
        "mass_loss": _openimcc_prediction_pin(mass_loss_prediction),
    }
    expected = {'activity': {'value': '0.000053875504071992326',
              'authority': 'extrapolated',
              'notices': [{'kind': 'source_disagreement',
                           'affected_quantities': ['activity'],
                           'reason': 'fo2_tier=1; route=observation_fO2_Pa_to_log_fO2',
                           'origin': 'fo2_ladder',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': None,
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': 'shared_ladder'},
                          {'kind': 'engine_model_caveat',
                           'affected_quantities': ['activity'],
                           'reason': 'species-coverage-edge: free x*(SiO2) is below '
                                     '0.00191005 of nominal x(SiO2); the K silicate '
                                     'ladder has exhausted its acidic sink',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': None,
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'imcc_complex_saturation',
                           'affected_quantities': ['activity'],
                           'reason': 'species-coverage-edge: free x*(SiO2) is below '
                                     '0.00191005 of nominal x(SiO2); the K silicate '
                                     'ladder has exhausted its acidic sink',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': None,
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'engine_model_caveat',
                           'affected_quantities': ['activity'],
                           'reason': 'K predictions from IMCC-SF04-D066 remain low '
                                     'against Hastie 1981 KEMS pressures (case 4: '
                                     '−1.17 to −1.22 dex); see '
                                     'https://github.com/simonrowland/openimcc',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': None,
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'out_of_certified_band',
                           'affected_quantities': ['activity'],
                           'reason': 'K2O(l) uses a tier-4 liquid_from_solid '
                                     'construction from K-012',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': '',
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'out_of_certified_band',
                           'affected_quantities': ['activity'],
                           'reason': 'MgO(l) uses a labelled constant-Cp '
                                     'supercooled-liquid continuation from JANAF '
                                     'Mg-009',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': '',
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'out_of_certified_band',
                           'affected_quantities': ['activity'],
                           'reason': 'Al2O3(l) uses a labelled constant-Cp '
                                     'supercooled-liquid continuation from JANAF '
                                     'Al-100',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': '',
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'out_of_certified_band',
                           'affected_quantities': ['activity'],
                           'reason': 'CaO(l) uses a labelled constant-Cp '
                                     'supercooled-liquid continuation from JANAF '
                                     'Ca-028',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': '',
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None},
                          {'kind': 'out_of_certified_band',
                           'affected_quantities': ['activity'],
                           'reason': 'TiO2(l) uses a source-tabulated supercooled '
                                     'liquid branch below the 2130 K fusion marker '
                                     '(O-044)',
                           'origin': 'engine:openimcc',
                           'original': None,
                           'source': None,
                           'destination': None,
                           'band': '',
                           'dropped': None,
                           'dropped_mass_fraction': None,
                           'authority': None,
                           'certification': None,
                           'fo2_mode': None}],
              'refusal': None},
 'residue': {'value': '17.05767377471813',
             'authority': None,
             'notices': [{'kind': 'out_of_certified_band',
                          'affected_quantities': ['residue_component_composition'],
                          'reason': 'feo_melt_redox_stack2_pending; regimes=M5,M2,M4',
                          'origin': 'hashimoto-residue-r4',
                          'original': None,
                          'source': None,
                          'destination': None,
                          'band': '1973-2273 K; engine tip 191960ce8',
                          'dropped': None,
                          'dropped_mass_fraction': None,
                          'authority': None,
                          'certification': None,
                          'fo2_mode': None}],
             'refusal': None},
 'mass_loss': {'value': None,
               'authority': None,
               'notices': [],
               'refusal': {'reason': 'identity_incomplete',
                           'detail': {'detail': 'thermal_schedule.total_duration_s',
                                      'missing_input': 'thermal_schedule.total_duration_s',
                                      'reason': 'duration_not_stated'}}}}
    assert actual == expected


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
) -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.melt_backend import openimcc_bridge

    context = load_score_context(sources=("li-2005-na-al-phosphate-fe-activities",))
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
    assert candidate.authority is not None
    # Pin the pack's actual result below once the controller's green probe
    # confirms whether this release's d066-ext datapack includes P2O5.
    if prediction.value is None:
        assert isinstance(prediction.refusal_reason, RefusalReason)
        assert candidate.authority is Authority.REFUSED
    else:
        assert candidate.authority in {Authority.BRIDGE, Authority.EXTRAPOLATED}
    assert any(
        _SP_PACK_SOURCE in notice.reason
        and f"authority={candidate.authority.value}" in notice.reason
        for notice in prediction.notices
    )
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
    assert any(
        _SP_PACK_SOURCE in notice.reason and "authority=refused" in notice.reason
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
        and "outside_validated" in notice.reason
        for notice in prediction.notices
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
