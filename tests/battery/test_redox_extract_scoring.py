import hashlib
import json
import math
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.battery.score as score_module
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    QuenchState,
    Rail,
    RefusalReason,
    ResidualStatus,
    ValenceDenominatorKind,
    ValenceValueForm,
)
from simulator.battery.identity import ValenceBasis
from simulator.battery.migrate import (
    Migrator,
    _identity_from_plain,
    fe3_fe2_ratio_from_ferric_fraction,
    fe3_fe2_ratio_from_oxide_wt_pct,
    load_yaml,
    write_outputs,
)
from simulator.battery.oxygen_identity import log10_fo2_to_pa
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    predict_with_engine,
    score_store,
    SCORE_ENGINE_SET,
)
from simulator.battery.records import Composition, Located, Locator, State, Value
from simulator.battery.score import headline_payload_records, residual_to_plain
from tests.battery.factories import migrate_one_extract


_AITHALA_REDOX_EXPERIMENTS = {
    "aithala-2026-1800-30",
    "aithala-2026-1900-30",
    "aithala-2026-2000-10",
    "aithala-2026-2000-30",
    "aithala-2026-2000-120",
    "aithala-2026-2100-30",
}


def _sossi_aithala_redox_context():
    root = Path(__file__).resolve().parents[2]
    source_paths = (
        root / "data/literature/extracts/sossi-2020-magma-ocean-redox.yaml",
        root / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml",
    )
    migrator = Migrator(root)
    for path in source_paths:
        migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result
    observations = {
        observation_id: observation
        for observation_id, observation in result.observations.items()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value.value == "fe3_fe2_ratio"
        and (
            observation.source_id == "sossi-2020-magma-ocean-redox"
            or (
                observation.source_id
                == "aithala-2026-magmatic-iron-redox-to-2100c"
                and observation.experiment_id.rsplit("::", 1)[-1]
                in _AITHALA_REDOX_EXPERIMENTS
            )
        )
    }
    assert len(observations) == 18
    origins = {
        observation_id: next(
            path.name
            for path in source_paths
            if observation_id.startswith(f"{path.stem}::")
        )
        for observation_id in observations
    }
    context = ScoreContext(
        result.works,
        result.experiments,
        observations,
        origins=origins,
        benches=result.benches,
    )
    return context, observations


def _stored_fe_valence_fraction_basis():
    root = Path(__file__).resolve().parents[2]
    store = load_yaml(
        root / "data/literature/extracts-v2/berry-2018-morb-iron-redox.yaml"
    )
    row = next(
        row
        for row in store["observations"]
        if row["identity"]["quantity"].get("value") == "valence_ratio"
        and row["identity"]["species"]["formula"] == "Fe"
    )
    basis = _identity_from_plain(row["identity"]).valence_basis.value
    assert basis == ValenceBasis(
        numerator_oxidation_states=(3,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    return basis


def _as_stored_fe_valence_fraction(observation):
    identity = replace(
        observation.identity,
        quantity=Quantity.VALENCE_RATIO,
        valence_basis=State.of(_stored_fe_valence_fraction_basis()),
    )
    return replace(observation, identity=identity)


@pytest.mark.parametrize(
    "engine", (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL)
)
def test_stored_fe3_total_iron_fraction_uses_fe_redox_prediction(engine):
    if engine is Engine.OPENIMCC:
        pytest.importorskip("openimcc")
    _context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]

    fe3_fe2 = predict_with_engine(engine, observation)
    fe3_total_iron = predict_with_engine(
        engine, _as_stored_fe_valence_fraction(observation)
    )

    assert fe3_fe2.value is not None, fe3_fe2.refusal_detail
    assert fe3_total_iron.value == fe3_fe2.value / (Decimal(1) + fe3_fe2.value)
    assert fe3_total_iron.refusal_reason is None


@pytest.mark.parametrize(
    "engine", (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL)
)
def test_stored_fe_valence_fraction_without_printed_fo2_refuses(engine):
    if engine is Engine.OPENIMCC:
        pytest.importorskip("openimcc")
    _context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    conditions = dict(observation.point_conditions or {})
    conditions.pop("fO2_log", None)
    conditions.pop("fO2_Pa", None)
    valence_observation = _as_stored_fe_valence_fraction(observation)
    missing_fo2 = replace(
        valence_observation,
        point_conditions=conditions,
        identity=replace(
            valence_observation.identity,
            fO2_Pa=None,
        ),
    )

    prediction = predict_with_engine(engine, missing_fo2)

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }


def test_kress91_valence_calibration_rows_are_fit_fidelity():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    row = next(
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value is Quantity.FE3_FE2_RATIO
        and observation.identity.species.formula == "Fe"
        and score_module._kc91_calibration_source_overlap(
            None
            if observation.evidence is None
            else observation.evidence.attribution
        )
    )
    valence_row = _as_stored_fe_valence_fraction(row)
    valence_row = replace(valence_row, value=Value.point_of(Decimal("0.5")))
    context = ScoreContext(
        result.works,
        result.experiments,
        {valence_row.observation_id: valence_row},
        origins={valence_row.observation_id: extract.name},
        benches=result.benches,
    )

    residuals, _ = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )

    assert len(residuals) == 1
    assert residuals[0].source_relation is score_module.SourceRelation.TRAINING
    records = score_module.measured_engine_rail_records(
        [residual_to_plain(residuals[0])],
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    populated = [record for record in records if record.get("candidates")]
    assert len(populated) == 1
    assert populated[0]["source_relation"] == "training"
    assert populated[0]["population"] == "calibration_source_overlap_fit_fidelity"


@pytest.mark.parametrize(
    ("engine", "quantity", "expected"),
    (
        (Engine.ALPHAMELTS, Quantity.FE3_FE2_RATIO, Decimal("2")),
        (Engine.THERMOENGINE, Quantity.FE3_FE2_RATIO, Decimal("2")),
        (Engine.ALPHAMELTS, Quantity.VALENCE_RATIO, Decimal(2) / 3),
        (Engine.THERMOENGINE, Quantity.VALENCE_RATIO, Decimal(2) / 3),
    ),
)
def test_melts_redox_outputs_use_printed_conditions_and_valence_identity(
    engine, quantity, expected, monkeypatch
):
    row4 = _borisov_row4()
    observation = (
        row4 if quantity is Quantity.FE3_FE2_RATIO
        else _as_stored_fe_valence_fraction(row4)
    )

    def cell_for(handle):
        if handle.name == "thermoengine":
            return _ok_cell(
                fe_redox_split={
                    "FeO_wt_pct": 7.1844,
                    "Fe2O3_wt_pct": 15.9687,
                }
            )
        return _ok_cell(
            liquid_composition_wt_pct={
                "FeO": 7.1844,
                "Fe2O3": 15.9687,
            }
        )

    calls = _patch_melts_cell(monkeypatch, cell_for)
    prediction = predict_with_engine(engine, observation, isolated=False)

    assert prediction.value == expected
    assert prediction.refusal_reason is None
    assert prediction.execution.state.value == "produced"
    assert prediction.identity is not None
    assert prediction.identity.quantity == observation.identity.quantity
    assert prediction.identity.valence_basis == observation.identity.valence_basis
    assert prediction.identity.fO2_Pa is not None
    assert len(calls) == 1
    call = calls[0]
    assert call["temperature_K"] == float(
        score_module.temperature_of(observation.identity)
    )
    assert call["physical_pressure_bar"] == pytest.approx(1.01325)
    assert call["po2"].mode == "commanded"
    printed = score_module._printed_fe_redox_fo2_log(
        observation, observation.identity
    )
    assert printed is not None
    assert call["po2"].po2_bar == pytest.approx(10 ** float(printed))
    expected_wt = score_module.composition_wt_pct(
        observation.identity.composition.value
    )
    assert call["pot"].composition_wt_pct == expected_wt


@pytest.mark.parametrize(
    ("quantity", "expected", "refusal"),
    (
        (Quantity.FE3_FE2_RATIO, None, RefusalReason.METRIC_DOMAIN),
        (Quantity.VALENCE_RATIO, Decimal(0), None),
    ),
)
def test_melts_zero_ferric_split_is_only_valid_for_total_iron_fraction(
    quantity, expected, refusal, monkeypatch
):
    observation = _borisov_row4()
    if quantity is Quantity.VALENCE_RATIO:
        observation = _as_stored_fe_valence_fraction(observation)
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={"FeO_wt_pct": 9.0, "Fe2O3_wt_pct": 0.0}
        ),
    )

    prediction = predict_with_engine(Engine.THERMOENGINE, observation, isolated=False)

    assert prediction.value == expected
    assert prediction.refusal_reason is refusal


def test_openimcc_sossi_redox_scores_the_public_redox_output(monkeypatch):
    pytest.importorskip("openimcc")
    import openimcc

    context, observations = _sossi_aithala_redox_context()
    observation_id = (
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    )
    observation = observations[observation_id]
    calls = []
    original_evaluate_redox = openimcc.evaluate_redox

    def capture_redox_call(*args, **kwargs):
        calls.append((args, kwargs))
        return original_evaluate_redox(*args, **kwargs)

    monkeypatch.setattr(openimcc, "evaluate_redox", capture_redox_call)
    prediction = predict_with_engine(Engine.OPENIMCC, observation)

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.refusal_reason is None
    assert prediction.execution.call_evidence == "openimcc.evaluate_redox"
    assert prediction.authority is Authority.BRIDGE
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0]["Fe"] > 0.0
    assert args[0]["Mg"] > 0.0
    assert args[2] == "imposed"
    assert args[1] == float(observation.identity.temperature_K.value)
    assert kwargs["lambda_imposed"] == pytest.approx(
        float(observation.point_conditions["fO2_log"].state.value) * math.log(10.0),
        rel=1e-15,
    )
    direct = original_evaluate_redox(*args, **kwargs)
    direct_ratio = direct.ferric_fe_mol / direct.ferrous_fe_mol
    assert abs(float(prediction.value) / direct_ratio - 1.0) <= 1e-9

    row_context = replace(
        context,
        observations={observation_id: observation},
        origins={observation_id: context.origins[observation_id]},
    )
    residuals, _ = score_store(
        row_context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    assert len(residuals) == 1
    assert residuals[0].numeric is not None
    assert residuals[0].numeric.operation is MetricOperation.DEX
    assert residuals[0].status is ResidualStatus.NO_BAND
    assert residuals[0].refusal is not None
    assert residuals[0].refusal.reason is RefusalReason.DECISION_RULE_MISSING


def test_openimcc_in_domain_redox_flags_become_prediction_notices(monkeypatch):
    pytest.importorskip("openimcc")
    import openimcc

    _context, observations = _sossi_aithala_redox_context()
    results = []
    original_evaluate_redox = openimcc.evaluate_redox

    def capture_redox_result(*args, **kwargs):
        result = original_evaluate_redox(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(openimcc, "evaluate_redox", capture_redox_result)
    total_flags = 0
    for observation_id, observation in sorted(observations.items()):
        calls_before = len(results)
        prediction = predict_with_engine(Engine.OPENIMCC, observation)

        assert prediction.value is not None, (observation_id, prediction.refusal_detail)
        assert prediction.authority is Authority.BRIDGE
        assert len(results) == calls_before + 1
        flags = tuple(results[-1].flags)
        flag_notices = tuple(
            notice
            for notice in prediction.notices
            if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        )
        assert flags
        assert tuple(notice.reason for notice in flag_notices) == flags
        assert all(
            notice.origin == "openimcc.evaluate_redox"
            and notice.affected_quantities == (Quantity.FE3_FE2_RATIO,)
            for notice in flag_notices
        )
        assert not any(
            notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            for notice in prediction.notices
        )
        total_flags += len(flags)

    assert len(results) == 18
    assert total_flags == 130


def test_openimcc_sossi_aithala_redox_residuals_remain_bit_identical():
    pytest.importorskip("openimcc")
    context, _observations = _sossi_aithala_redox_context()
    residuals, _ = score_store(
        context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    values = [
        float(residual.numeric.value).hex()
        for residual in residuals
        if residual.numeric is not None
    ]
    assert len(values) == 18
    encoded = json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode()
    # Pinned with the imported OpenIMCC source tree e975edd229b7653ceac5dbac9558d0156e51c2f6.
    assert hashlib.sha256(encoded).hexdigest() == (
        "c79971dce81ac423bf34a04525b3da956a437f83e6dbe1fa601d24c1627b8f44"
    )
    for backlog in (
        score_module.notice_backlog(residuals),
        score_module.notice_backlog_payloads(
            [residual_to_plain(residual) for residual in residuals]
        ),
    ):
        assert backlog.get(NoticeKind.SOURCE_DISAGREEMENT.value, 0) == 0
        assert backlog["engine_model_caveat"] == 130
        assert backlog["admission_defaulted"] == 18
        assert backlog["total_iron_reported_as_feo"] == 18
    assert all(
        not score_module.flagged_strata(residual.notices) for residual in residuals
    )
    assert not any(residual.score_eligible for residual in residuals)


def test_openimcc_redox_without_printed_fo2_still_refuses():
    pytest.importorskip("openimcc")
    _context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    conditions = dict(observation.point_conditions or {})
    conditions.pop("fO2_log", None)
    conditions.pop("fO2_Pa", None)
    missing_fo2 = replace(
        observation,
        point_conditions=conditions,
        identity=replace(observation.identity, fO2_Pa=None),
    )

    prediction = predict_with_engine(Engine.OPENIMCC, missing_fo2)

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }


def _openimcc_row_with_mass_composition(components):
    """Sossi redox row with a replacement mass-percent composition."""

    _context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    printed = observation.identity.composition
    assert printed is not None and printed.is_value
    composition = Composition(
        basis=printed.value.basis,
        components=components,
        amount_basis=AmountBasis.MASS_PERCENT,
    )
    return replace(
        observation,
        identity=replace(observation.identity, composition=State.of(composition)),
    )


def test_openimcc_redox_with_non_kress_iron_reaches_evaluate_redox(monkeypatch):
    """Fe3O4 is iron for OpenIMCC and not a Kress FeOt component.

    The merged helper refused ``total_iron`` from the Kress basis before
    printed fO2 and before ``evaluate_redox``. Offer 20 converts weights,
    reads printed fO2, then counts Fe atoms. OpenIMCC must not equilibrate.
    """

    row = _openimcc_row_with_mass_composition(
        (("SiO2", Decimal("90")), ("Fe3O4", Decimal("10")))
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("OpenIMCC redox must not open or equilibrate a cell")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _boom,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _boom,
    )
    calls = []
    try:
        import openimcc
    except ImportError:
        openimcc = None
    else:
        original_evaluate_redox = openimcc.evaluate_redox

        def capture_redox_call(*args, **kwargs):
            calls.append((args, kwargs))
            return original_evaluate_redox(*args, **kwargs)

        monkeypatch.setattr(openimcc, "evaluate_redox", capture_redox_call)

    prediction = predict_with_engine(Engine.OPENIMCC, row)

    assert prediction.refusal_detail != {
        "reason": "missing_input",
        "input": "total_iron",
    }
    if openimcc is None:
        assert prediction.refusal_reason is RefusalReason.OPENIMCC_NOT_IMPORTABLE
        return
    assert calls, prediction.refusal_detail
    args, kwargs = calls[0]
    assert args[0].get("Fe", 0.0) > 0.0
    assert args[2] == "imposed"
    assert kwargs["lambda_imposed"] == pytest.approx(
        float(row.point_conditions["fO2_log"].state.value) * math.log(10.0),
        rel=1e-15,
    )


def test_openimcc_missing_fo2_without_kress_iron_refuses_printed_oxygen():
    """Silica with no fO2 is a printed-oxygen refusal, not a Kress FeOt refusal."""

    row = _openimcc_row_with_mass_composition((("SiO2", Decimal("100")),))
    conditions = dict(row.point_conditions or {})
    conditions.pop("fO2_log", None)
    conditions.pop("fO2_Pa", None)
    missing_fo2 = replace(
        row,
        point_conditions=conditions,
        identity=replace(row.identity, fO2_Pa=None),
    )

    prediction = predict_with_engine(Engine.OPENIMCC, missing_fo2)

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }
    assert prediction.coefficient_sources == ()


def test_openimcc_borisov_row_follows_the_train_redox_path(monkeypatch):
    """Borisov row 4 on OpenIMCC is the train parent's produced ratio.

    ``b1a7c34db`` ``predict_with_engine(OPENIMCC, row4)`` returns this
    Decimal from ``openimcc.evaluate_redox`` on the printed mole-fraction
    composition. The row is not a mass-percent stand-in. OpenIMCC must
    not open or equilibrate a cell. The pack digest is the digest on the
    result the scorer just stamped, not a host-specific literal.
    """

    pytest.importorskip("openimcc")
    import openimcc

    row4 = _borisov_row4()

    def _boom(*_args, **_kwargs):
        raise AssertionError("OpenIMCC redox must not open or equilibrate a cell")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _boom,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _boom,
    )
    redox_results = []
    original_evaluate_redox = openimcc.evaluate_redox

    def capture_redox_result(*args, **kwargs):
        result = original_evaluate_redox(*args, **kwargs)
        redox_results.append(result)
        return result

    monkeypatch.setattr(openimcc, "evaluate_redox", capture_redox_result)
    prediction = predict_with_engine(Engine.OPENIMCC, row4)

    assert prediction.refusal_reason is None
    assert prediction.value == Decimal("1.6414352204105178")
    assert prediction.unit == "dimensionless"
    assert prediction.authority is Authority.BRIDGE
    assert prediction.execution.call_evidence == "openimcc.evaluate_redox"
    assert prediction.version == "openimcc-redox"
    assert len(redox_results) == 1
    digest = redox_results[0].redox_pack_digest
    assert len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
    assert prediction.coefficient_sources == (
        f"openimcc-redox-pack:sha256:{digest}",
    )


def test_openimcc_redox_predicts_and_flags_temperature_extrapolation():
    pytest.importorskip("openimcc")
    _context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    low_temperature = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(Decimal("1600")),
        ),
    )

    prediction = predict_with_engine(Engine.OPENIMCC, low_temperature)

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.authority is Authority.EXTRAPOLATED
    notice = next(
        item
        for item in prediction.notices
        if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    assert notice.authority is Authority.EXTRAPOLATED
    assert "FeO(l)" in notice.reason


def test_sossi_internal_analytical_numeric_count_matches_green():
    context, observations = _sossi_aithala_redox_context()
    sossi_ids = [
        observation_id
        for observation_id, observation in observations.items()
        if observation.source_id == "sossi-2020-magma-ocean-redox"
    ]
    assert len(sossi_ids) == 12
    sossi_context = replace(
        context,
        observations={key: observations[key] for key in sossi_ids},
        origins={key: context.origins[key] for key in sossi_ids},
    )
    residuals, _ = score_store(
        sossi_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    assert len(residuals) == 12
    assert all(residual.numeric is not None for residual in residuals)
    assert all(
        residual.status is not ResidualStatus.REFUSED for residual in residuals
    )


def test_internal_analytical_sossi_aithala_redox_outputs_remain_byte_identical():
    context, _observations = _sossi_aithala_redox_context()
    residuals, _ = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )
    encoded = json.dumps(
        [residual_to_plain(residual) for residual in residuals],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    # Combined-base pin. The payload still includes t-1279's six joined-Aithala
    # FeOT notices. Quench4 adds quench_state not_stated and the glass-to-melt
    # identity compare, so the identity_equal conjunct passes. Printed
    # uncertainty with an unmapped unit is printed_uncertainty_unit_unmapped
    # and carries the source unit text.
    # b-979 / d-053: source_relation on all 18 residuals moves from
    # independent to unknown. Kress attribution stays UNKNOWN unless both
    # walks are complete. No numeric field moved.
    assert hashlib.sha256(encoded).hexdigest() == (
        "11313cf08df6b900eb17e50da8ab69b4387ac75d91956105d34e50cfcdee33d5"
    )


def _assert_kress91_citation_is_unknown(residuals, observations) -> None:
    """Kress & Carmichael remains unknown; direct rows lack full compositions.

    Kress & Carmichael 1991 is not a registered work. regolith-main is
    registering it under kc91-1022, and these source-relation pins are
    re-pinned once that work lands. The numeric predictions are the
    previous attribution split: calibration overlap versus the other
    printed sources. That split is no longer the source relation. The 31 direct
    Table 1 ratios also cannot be scored because the table omits oxides required
    by the model; the 404 supplementary rows remain numeric.
    """

    assert len(residuals) == 435
    assert all(
        residual.source_relation is score_module.SourceRelation.UNKNOWN
        for residual in residuals
    )
    assert all(not residual.score_eligible for residual in residuals)
    overlap_all: list[Decimal] = []
    held_all: list[Decimal] = []
    overlap_admitted: list[Decimal] = []
    held_admitted: list[Decimal] = []
    refused = [residual for residual in residuals if residual.numeric is None]
    assert len(refused) == 31
    assert all(
        residual.reference.rsplit("::", 1)[-1].startswith("borisov_2018_table1_")
        and residual.refusal is not None
        and residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
        and residual.refusal.detail
        == {"reason": "missing_input", "input": "composition"}
        and "TiO2 and P2O5" in observations[residual.reference].identity.composition.reason
        for residual in refused
    )
    for residual in residuals:
        observation = observations[residual.reference]
        overlap = score_module._kc91_calibration_source_overlap(
            observation.evidence.attribution
        )
        if residual.numeric is None:
            continue
        value = residual.numeric.value
        (overlap_all if overlap else held_all).append(value)
        if score_module._headline_payload_admits(
            residual_to_plain(residual), tier="measured"
        ):
            (overlap_admitted if overlap else held_admitted).append(value)
    assert (len(overlap_all), len(held_all)) == (223, 181)
    assert (len(overlap_admitted), len(held_admitted)) == (0, 0)
    assert Decimal(str(score_module._rms(overlap_all))).quantize(
        Decimal("0.00001")
    ) == Decimal("0.11250")
    assert Decimal(str(score_module._rms(held_all))).quantize(
        Decimal("0.00001")
    ) == Decimal("0.10885")
    assert abs(
        score_module._median(overlap_all) - Decimal("-0.034867523522288196")
    ) < Decimal("0.00001")
    assert abs(
        score_module._median(held_all) - Decimal("-0.016561640746814852")
    ) < Decimal("0.00001")


def _assert_unknown_redox_headline(records) -> None:
    """The redox headline is one UNKNOWN row, not the retired split."""

    retired = {
        "calibration_source_overlap_fit_fidelity",
        "independent_source_accuracy",
    }
    redox = [
        record
        for record in records
        if record.get("rail") == Rail.REDOX.value
        and record.get("engine") == Engine.INTERNAL_ANALYTICAL.value
    ]
    assert not any(record.get("population") in retired for record in redox)
    measured = [
        record
        for record in redox
        if record.get("tier") == "measured"
        and record.get("source_relation") == "unknown"
    ]
    assert len(measured) == 1
    row = measured[0]
    assert "population" not in row
    assert (row["n_candidates"], row["n"]) == (31, 0)
    assert row["n_refused"] == 31
    assert row["n_score_eligible"] == 0


def test_borisov_2018_rows_reach_redox_scoring_population(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    source = load_yaml(extract)
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    source_rows = source["species"]["Fe"]["observations"]
    assert len(source_rows) == 435
    # Article Table 1 (1175b49fc) is the wet-chemical ratio itself: 31 rows,
    # no values.inference. The 404 quoted Supplementary Table 2 rows keep the
    # log10 conversion. Quoted evidence does not require derived_from.
    inferred_rows = [
        row for row in source_rows if "inference" in (row.get("values") or {})
    ]
    table1_rows = [
        row
        for row in source_rows
        if str(row.get("observation_id") or "").startswith("borisov_2018_table1_")
    ]
    assert len(table1_rows) == 31
    assert len(inferred_rows) == 404
    assert {row["observation_id"] for row in table1_rows}.isdisjoint(
        {row["observation_id"] for row in inferred_rows}
    )
    for row in inferred_rows:
        values = row["values"]
        inference = values["inference"]
        assert inference["relation"] == values["ratio_derivation"]
        assert inference["inputs"] == ["log10_Fe3_over_Fe2_as_published"]
        original = inference["parameters"][0][1]
        assert original["state"]["value"] == values["log10_Fe3_over_Fe2_as_published"]
        assert original["locator"] == row["locator"]
        observation = result.observations[f"{extract.stem}::{row['observation_id']}"]
        assert observation.derivation is not None
        conversion = observation.derivation.parameters[0][1].inference
        assert conversion is not None
        assert conversion.relation == values["ratio_derivation"]

    assert result.validation.hard_issues == ()

    origins = {
        observation_id: "extracts/borisov-2018-compilation.yaml"
        for observation_id in result.observations
    }
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins=origins,
        benches=result.benches,
    )

    assert len(comparison_candidates(context)) == 435
    rows_by_id = {row.observation_id: row for row in result.observations.values()}
    row4 = rows_by_id[f"{extract.stem}::borisov_2018_table2_row_4"]
    # Spreadsheet row 155 is article Table 1 (1175b49fc), not a quoted Table 2 id.
    assert f"{extract.stem}::borisov_2018_table2_row_155" not in rows_by_id

    # Hand evaluation of Kress & Carmichael 1991 at row 4's printed T and log fO2,
    # with 1 atm from the cited compilation: Fe2O3/FeO=0.9191803065, hence
    # Fe3+/Fe2+=2*0.9191803065=1.838361. The factor two converts oxide formula
    # units to Fe cations.
    prediction4 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row4)
    assert prediction4.value is not None
    assert abs(prediction4.value - Decimal("1.838360613")) < Decimal("1e-9")
    printed_log = row4.point_conditions["fO2_log"].state.value
    if isinstance(printed_log, Value):
        printed_log = printed_log.point
    assert isinstance(printed_log, Decimal)
    canonical_fo2 = log10_fo2_to_pa(printed_log)
    assert prediction4.identity is not None
    assert prediction4.identity.fO2_Pa is not None
    assert prediction4.identity.fO2_Pa.value == canonical_fo2
    assert prediction4.identity.fO2_Pa.value == row4.identity.fO2_Pa.value
    float_reconstruction = Decimal(str(100000.0 * 10.0 ** float(printed_log)))
    if canonical_fo2 != float_reconstruction:
        assert prediction4.identity.fO2_Pa.value != float_reconstruction
    table1_row1 = rows_by_id[f"{extract.stem}::borisov_2018_table1_row_1"]
    assert table1_row1.identity.composition.is_unknown
    assert "TiO2 and P2O5" in table1_row1.identity.composition.reason
    prediction_table1_row1 = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, table1_row1
    )
    assert prediction_table1_row1.value is None
    assert (
        prediction_table1_row1.refusal_reason
        is RefusalReason.IDENTITY_INCOMPLETE
    )
    assert prediction_table1_row1.refusal_detail == {
        "reason": "missing_input",
        "input": "composition",
    }
    assert prediction4.refusal_reason is None

    glass_row = replace(
        row4,
        observation_id="borisov_2018_glass_fe3_fe2_fixture",
        identity=replace(
            row4.identity,
            species=replace(row4.identity.species, phase=Phase.GLASS),
        ),
        point_conditions={
            **(row4.point_conditions or {}),
            "quench_state": Located(
                State.of(QuenchState.ASSERTED_PRESERVED),
                locator=row4.locator or Locator(page=2),
            ),
        },
    )
    glass_context = replace(
        context,
        observations={glass_row.observation_id: glass_row},
        origins={glass_row.observation_id: extract.name},
    )
    glass_residuals, glass_candidates = score_store(
        glass_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    assert len(glass_residuals) == 1
    glass_residual = glass_residuals[0]
    assert glass_residual.numeric is not None
    assert glass_residual.refusal is None
    assert glass_residual.quench_state is QuenchState.ASSERTED_PRESERVED
    assert glass_residual.candidate in glass_candidates
    assert (
        glass_candidates[glass_residual.candidate].identity.species.phase.value
        is Phase.L
    )

    out_of_band_source = next(
        row for row in source_rows if float(row["values"]["T_C"]) > 1630.0
    )
    out_of_band_row = rows_by_id[
        f"{extract.stem}::{out_of_band_source['observation_id']}"
    ]
    extrapolated = predict_with_engine(Engine.INTERNAL_ANALYTICAL, out_of_band_row)
    assert extrapolated.value is not None
    assert extrapolated.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "1200,1630" in notice.reason
        for notice in extrapolated.notices
    )

    conditions_without_fo2 = dict(row4.point_conditions or {})
    conditions_without_fo2.pop("fO2_log", None)
    conditions_without_fo2.pop("fO2_Pa", None)
    missing_fo2 = replace(
        row4,
        point_conditions=conditions_without_fo2,
        identity=replace(row4.identity, fO2_Pa=None),
    )
    missing_prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, missing_fo2)
    assert missing_prediction.value is None
    assert missing_prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert missing_prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }

    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            status="refusal",
            refusal_reason="engine_crash",
            engine_reason="fixture backend unavailable",
        ),
    )
    unavailable = predict_with_engine(Engine.ALPHAMELTS, row4, isolated=False)
    assert unavailable.value is None
    assert unavailable.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert unavailable.refusal_detail["refusal_reason"] == "engine_crash"

    unsupported = predict_with_engine(Engine.MAGEMIN, row4)
    assert unsupported.value is None
    assert unsupported.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX

    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    # Kress & Carmichael 1991 is not a registered work. regolith-main is
    # registering it under kc91-1022, and these pins are re-pinned once it lands.
    _assert_kress91_citation_is_unknown(residuals, result.observations)
    measured_rows = score_module.measured_engine_rail_records(
        [residual_to_plain(residual) for residual in residuals],
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    measured_unknown = next(
        row for row in measured_rows
        if row.get("rail") == Rail.REDOX.value
        and row.get("source_relation") == "unknown"
        and row.get("candidates")
    )
    assert "population" not in measured_unknown
    assert (measured_unknown["candidates"], measured_unknown["numeric"]) == (
        435,
        404,
    )
    assert (
        measured_unknown["certified_numeric"],
        measured_unknown["flagged_numeric"],
    ) == (0, 10)
    assert not any(
        row.get("source_relation") == "training" and row.get("candidates")
        for row in measured_rows
    )
    assert Decimal(
        measured_unknown["metrics"][0]["all_numeric"]["rms"]
    ).quantize(Decimal("0.00001")) == Decimal("0.11088")
    assert len(residuals) == 435

    conflicting_fo2 = replace(
        row4,
        identity=replace(row4.identity, fO2_Pa=State.of(Decimal("1000"))),
    )
    mismatch_context = replace(
        context,
        observations={conflicting_fo2.observation_id: conflicting_fo2},
    )
    mismatch_residuals, _ = score_store(
        mismatch_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    assert len(mismatch_residuals) == 1
    assert mismatch_residuals[0].numeric is None
    assert mismatch_residuals[0].refusal is not None
    assert mismatch_residuals[0].refusal.reason is RefusalReason.IDENTITY_MISMATCH
    assert mismatch_residuals[0].refusal.detail["fields"] == ["fO2_Pa"]

    zero_ratio = replace(row4, value=Value.point_of(Decimal(0)))
    zero_context = replace(
        context,
        observations={zero_ratio.observation_id: zero_ratio},
    )
    zero_residuals, _ = score_store(
        zero_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    assert len(zero_residuals) == 1
    assert zero_residuals[0].numeric is None
    assert zero_residuals[0].refusal is not None
    assert zero_residuals[0].refusal.reason is RefusalReason.METRIC_DOMAIN


def test_borisov_redox_headline_is_unknown_pending_kc91_registration(tmp_path):
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: extract.name for key in result.observations},
        benches=result.benches,
    )
    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    # Kress & Carmichael 1991 is not a registered work. regolith-main is
    # registering it under kc91-1022, and these pins are re-pinned once it lands.
    _assert_kress91_citation_is_unknown(residuals, result.observations)
    records = headline_payload_records(
        [residual_to_plain(residual) for residual in residuals],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        observations=result.observations,
        origins={key: extract.name for key in result.observations},
    )
    _assert_unknown_redox_headline(records)

    object_records = score_module.headline_records(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    accumulator = score_module._ScorePayloadAccumulator(
        context, (Engine.INTERNAL_ANALYTICAL,)
    )
    for residual in residuals:
        accumulator.add(score_module.residual_to_plain(residual))
    accumulator_records = accumulator.headline_records()
    for headline in (object_records, accumulator_records):
        _assert_unknown_redox_headline(headline)

    fixture_extract = tmp_path / "data/literature/extracts" / extract.name
    fixture_extract.parent.mkdir(parents=True)
    shutil.copy2(extract, fixture_extract)
    write_outputs(result, tmp_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "data"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Battery score test",
            "-c",
            "user.email=battery-score-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Borisov fixture store",
        ],
        check=True,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "battery_score.py"),
            "--root",
            str(tmp_path),
            "--engines",
            Engine.INTERNAL_ANALYTICAL.value,
            "--rail",
            Rail.REDOX.value,
            "--work",
            "borisov-2018-compilation",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(
        (tmp_path / "data/battery/score-summary.json").read_text(encoding="utf-8")
    )
    _assert_unknown_redox_headline(summary["records"])
    report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    retired = (
        "calibration_source_overlap_fit_fidelity",
        "independent_source_accuracy",
    )
    assert not any(
        population in line
        for line in report.splitlines()
        if line.startswith("| redox |")
        for population in retired
    )
    fit_row = next(
        [part.strip() for part in line.strip("|").split("|")]
        for line in report.splitlines()
        if line.startswith("| redox | internal-analytical | unknown |")
    )
    assert fit_row[2] == "unknown"
    assert fit_row[3] == "31"
    assert fit_row[5] == fit_row[6] == "0"

    measured_completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "battery_score.py"),
            "--root",
            str(tmp_path),
            "--engines",
            Engine.INTERNAL_ANALYTICAL.value,
            "--rail",
            Rail.REDOX.value,
            "--work",
            "borisov-2018-compilation",
            "--measured-headline",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert measured_completed.returncode == 0, measured_completed.stdout + measured_completed.stderr
    measured_summary = json.loads(
        (tmp_path / "data/battery/score-summary.json").read_text(encoding="utf-8")
    )
    measured_unknown = next(
        row for row in measured_summary["records"]
        if row.get("rail") == Rail.REDOX.value
        and row.get("engine") == Engine.INTERNAL_ANALYTICAL.value
        and row.get("source_relation") == "unknown"
        and row.get("candidates")
    )
    assert "population" not in measured_unknown
    assert (measured_unknown["candidates"], measured_unknown["numeric"]) == (31, 0)
    assert measured_unknown["refused"] == 31
    assert (
        measured_unknown["certified_numeric"],
        measured_unknown["flagged_numeric"],
    ) == (0, 0)
    measured_report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    assert not any(
        population in line
        for line in measured_report.splitlines()
        if line.startswith("| redox |")
        for population in (
            "calibration_source_overlap_fit_fidelity",
            "independent_source_accuracy",
        )
    )
    measured_fit = next(
        [part.strip() for part in line.strip("|").split("|")]
        for line in measured_report.splitlines()
        if line.startswith("| redox | — / — | internal-analytical | unknown |")
    )
    assert measured_fit[3] == "unknown"
    assert measured_fit[4] == "31"
    assert measured_fit[5] == measured_fit[6] == measured_fit[7] == "0"
    assert measured_fit[8] == "31"


def test_borisov_redox_headline_paths_keep_unknown_source_relation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: extract.name for key in result.observations},
        benches=result.benches,
    )
    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    records = headline_payload_records(
        [residual_to_plain(residual) for residual in residuals],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        observations=result.observations,
        origins={key: extract.name for key in result.observations},
    )
    object_records = score_module.headline_records(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    accumulator = score_module._ScorePayloadAccumulator(
        context, (Engine.INTERNAL_ANALYTICAL,)
    )
    for residual in residuals:
        accumulator.add(score_module.residual_to_plain(residual))
    accumulator_records = accumulator.headline_records()
    for headline in (records, object_records, accumulator_records):
        _assert_unknown_redox_headline(headline)

    fixture_extract = tmp_path / "data/literature/extracts" / extract.name
    fixture_extract.parent.mkdir(parents=True)
    shutil.copy2(extract, fixture_extract)
    write_outputs(result, tmp_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "data"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Battery score test",
            "-c",
            "user.email=battery-score-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Borisov fixture store",
        ],
        check=True,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "battery_score.py"),
            "--root",
            str(tmp_path),
            "--engines",
            Engine.INTERNAL_ANALYTICAL.value,
            "--rail",
            Rail.REDOX.value,
            "--work",
            "borisov-2018-compilation",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(
        (tmp_path / "data/battery/score-summary.json").read_text(encoding="utf-8")
    )
    _assert_unknown_redox_headline(summary["records"])
    assert not any(
        row.get("population") in {
            "calibration_source_overlap_fit_fidelity",
            "independent_source_accuracy",
        }
        or row.get("source_relation") == "training"
        for row in summary["records"]
    )
    report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    assert "calibration_source_overlap_fit_fidelity" not in report
    assert "independent_source_accuracy" not in report
    report_rows = {
        parts[2]: parts
        for line in report.splitlines()
        if line.startswith("| redox | internal-analytical | ")
        if (parts := [part.strip() for part in line.strip("|").split("|")])
        and len(parts) == 15
    }
    assert set(report_rows) == {"unknown"}
    unknown_row = report_rows["unknown"]
    assert unknown_row[3] == "31"
    assert unknown_row[5] == unknown_row[6] == "0"

    measured_completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "battery_score.py"),
            "--root",
            str(tmp_path),
            "--engines",
            Engine.INTERNAL_ANALYTICAL.value,
            "--rail",
            Rail.REDOX.value,
            "--work",
            "borisov-2018-compilation",
            "--measured-headline",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert measured_completed.returncode == 0, measured_completed.stdout + measured_completed.stderr
    measured_summary = json.loads(
        (tmp_path / "data/battery/score-summary.json").read_text(encoding="utf-8")
    )
    assert {
        row.get("population")
        for row in measured_summary["records"]
        if row.get("population")
    } == set()
    assert not any(
        row.get("population") == "calibration_source_overlap_fit_fidelity"
        or row.get("source_relation") == "training"
        for row in measured_summary["records"]
    )
    measured_redox = [
        row
        for row in measured_summary["records"]
        if row.get("rail") == Rail.REDOX.value
        and row.get("engine") == Engine.INTERNAL_ANALYTICAL.value
    ]
    assert len(measured_redox) == 1
    measured_unknown = measured_redox[0]
    assert "population" not in measured_unknown
    assert measured_unknown["source_relation"] == "unknown"
    assert measured_unknown["candidates"] == 31
    assert measured_unknown["numeric"] == 0
    assert measured_unknown["refused"] == 31
    measured_report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    assert "calibration_source_overlap_fit_fidelity" not in measured_report
    measured_rows = [
        parts
        for line in measured_report.splitlines()
        if line.startswith("| redox |")
        if (parts := [part.strip() for part in line.strip("|").split("|")])
        and len(parts) == 15
    ]
    assert len(measured_rows) == 1
    assert measured_rows[0][3] == "unknown"
    assert measured_rows[0][4] == "31"
    assert measured_rows[0][5] == "0"
    assert measured_rows[0][8] == "31"




def test_kress91_redox_flags_oxide_outside_its_converter_domain():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    row4 = result.observations[
        f"{extract.stem}::borisov_2018_table2_row_4"
    ]
    composition = row4.identity.composition.value
    changed_composition = replace(
        composition,
        components=tuple(
            (name, amount * Decimal("0.9"))
            for name, amount in composition.components
        ) + (("Cr2O3", Decimal("0.1")),),
    )
    projected_row = replace(
        row4,
        identity=replace(
            row4.identity,
            composition=State.of(changed_composition),
        ),
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, projected_row)
    notice = next(
        item
        for item in prediction.notices
        if item.kind is NoticeKind.COMPOSITION_PROJECTED
    )
    assert prediction.value is not None
    assert notice.dropped == ("Cr2O3",)
    assert notice.dropped_mass_fraction is not None
    assert notice.authority is prediction.authority is Authority.BRIDGE
    assert notice.band == "[1473.15, 1903.15] K"
    assert "Cr2O3" in notice.reason
    assert "authority=bridge" in notice.reason
    assert "certified_band=" in notice.reason


@pytest.mark.parametrize("axis", ("temperature_K", "composition", "total_pressure_Pa", "fO2_Pa"))
def test_unknown_phase_redox_still_refuses_other_identity_axis_mismatches(axis, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    reference = result.observations[
        f"{extract.stem}::borisov_2018_table2_row_4"
    ]
    if axis == "total_pressure_Pa":
        reference = replace(
            reference,
            identity=replace(
                reference.identity,
                total_pressure_Pa=State.of(Decimal("101325")),
            ),
        )
    elif axis == "fO2_Pa":
        reference = replace(
            reference,
            identity=replace(
                reference.identity,
                fO2_Pa=State.of(Decimal("1000")),
            ),
        )
    context = ScoreContext(
        result.works,
        result.experiments,
        {reference.observation_id: reference},
        origins={reference.observation_id: extract.name},
        benches=result.benches,
    )
    original_predict = score_module.predict_with_engine

    def prediction_with_conflicting_axis(engine, observation, **kwargs):
        prediction = original_predict(engine, observation, **kwargs)
        if observation.observation_id != reference.observation_id:
            return prediction
        identity = prediction.identity
        if axis == "temperature_K":
            conflict = replace(
                identity,
                temperature_K=State.of(Decimal("1700")),
            )
        elif axis == "composition":
            source = identity.composition.value
            first, amount = source.components[0]
            conflict = replace(
                identity,
                composition=State.of(
                    replace(
                        source,
                        components=((first, amount + Decimal("0.01")), *source.components[1:]),
                    )
                ),
            )
        elif axis == "total_pressure_Pa":
            conflict = replace(
                identity, total_pressure_Pa=State.of(Decimal("200000"))
            )
        else:
            conflict = identity
        return replace(prediction, identity=conflict)

    monkeypatch.setattr(score_module, "predict_with_engine", prediction_with_conflicting_axis)
    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
    )
    assert len(residuals) == 1
    assert residuals[0].numeric is None
    assert residuals[0].refusal is not None
    assert residuals[0].refusal.reason is RefusalReason.IDENTITY_MISMATCH


def test_aithala_redox_rows_score_only_when_composition_is_printed(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    redox_rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value.value == "fe3_fe2_ratio"
    ]
    assert len(redox_rows) == 16
    projected_ids = {
        row.observation_id
        for row in redox_rows
        if row.experiment_id.rsplit("::", 1)[-1]
        in _AITHALA_REDOX_EXPERIMENTS
    }
    assert len(projected_ids) == 6
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: extract.name for key in result.observations},
        benches=result.benches,
    )
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            status="refusal",
            refusal_reason="engine_crash",
            engine_reason="fixture backend unavailable",
        ),
    )
    residuals, _ = score_store(context, engines=SCORE_ENGINE_SET, rail=Rail.REDOX)
    assert len(residuals) == 16 * len(SCORE_ENGINE_SET)
    for residual in residuals:
        observation_id = residual.key.split(":T=", 1)[0]
        engine = residual.key.rsplit("::", 1)[-1]
        if observation_id in projected_ids and engine in {
            "internal-analytical",
            "openimcc",
        }:
            assert residual.numeric is not None
            assert residual.refusal is not None
            assert residual.refusal.reason is RefusalReason.DECISION_RULE_MISSING
        elif observation_id in projected_ids and engine in {
            "alphamelts",
            "thermoengine",
        }:
            assert residual.numeric is None
            assert residual.refusal is not None
            assert residual.refusal.reason is RefusalReason.ATTEMPTED_UNAVAILABLE
            assert residual.refusal.detail["refusal_reason"] == "engine_crash"
        elif engine in {
            "internal-analytical",
            "openimcc",
            "alphamelts",
            "thermoengine",
        }:
            assert residual.numeric is None
            assert residual.refusal is not None
            assert residual.refusal.detail == {
                "reason": "missing_input",
                "input": "composition",
            }
            assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
        else:
            assert residual.numeric is None
            assert residual.refusal is not None
            assert (
                residual.refusal.reason
                is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
            )


def _borisov_row4():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    return next(
        row
        for row in migrator.result.observations.values()
        if row.observation_id.endswith("borisov_2018_table2_row_4")
    )


def _without_printed_fo2(observation):
    conditions = dict(observation.point_conditions or {})
    conditions.pop("fO2_log", None)
    conditions.pop("fO2_Pa", None)
    return replace(
        observation,
        point_conditions=conditions,
        identity=replace(observation.identity, fO2_Pa=None),
    )


def _patch_melts_cell(monkeypatch, cell_for):
    calls = []

    def _open(name):
        return SimpleNamespace(
            name=name,
            available=True,
            unavailable_reason=None,
            backend=object(),
            supports_intrinsic_fo2=(name == "thermoengine"),
        )

    def _cell(handle, pot, **kwargs):
        calls.append({"handle": handle, "pot": pot, **kwargs})
        return cell_for(handle)

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    return calls


def _ok_cell(**fields):
    payload = {
        "status": "ok",
        "refusal_reason": None,
        "hostname": "test",
        "exit_code": 0,
        "exit_signal": None,
        "model_id": None,
        "notices": [],
        "authority": None,
        "certified_band": None,
        "liquid_composition_wt_pct": {},
        "fe_redox_split": {},
    }
    payload.update(fields)
    return SimpleNamespace(**payload)


def test_melts_fe_redox_uses_recorded_engine_oxide_weights(monkeypatch):
    """9 wt% FeO and 1 wt% Fe2O3 is the recorded ThermoEngine split shape.

    The battery ratio is the production oxide-weight converter. Split weights
    win over a different liquid pair and over Fe3Fet_Liq.
    """

    row4 = _borisov_row4()
    expected = fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1.0"), Decimal("9.0"))
    assert expected is not None
    other_liquid = fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1.0"), Decimal("100.0"))
    other_fraction = fe3_fe2_ratio_from_ferric_fraction(Decimal("0.9"))
    assert expected != other_liquid
    assert expected != other_fraction

    def cell_for(handle):
        if handle.name == "thermoengine":
            return _ok_cell(
                fe_redox_split={
                    "FeO_wt_pct": 9.0,
                    "Fe2O3_wt_pct": 1.0,
                    "Fe3Fet_Liq": 0.9,
                },
                liquid_composition_wt_pct={"FeO": 100.0, "Fe2O3": 1.0},
            )
        return _ok_cell(
            liquid_composition_wt_pct={"SiO2": 50.0, "FeO": 9.0, "Fe2O3": 1.0},
        )

    calls = _patch_melts_cell(monkeypatch, cell_for)
    printed = score_module._printed_fe_redox_fo2_log(row4, row4.identity)
    assert printed is not None
    for engine in (Engine.THERMOENGINE, Engine.ALPHAMELTS):
        prediction = predict_with_engine(engine, row4, isolated=False)
        assert prediction.value == expected
        assert prediction.refusal_reason is None
        assert prediction.unit == "dimensionless"
        assert prediction.coefficient_sources == (engine.value,)
    assert len(calls) == 2
    for call in calls:
        assert call["qualification"] is False
        assert call["physical_pressure_bar"] == pytest.approx(1.01325)
        assert call["po2"].mode == "commanded"
        assert call["po2"].po2_bar == pytest.approx(10 ** float(printed))


def test_melts_fe_redox_uses_ferric_fraction_only_without_weights(monkeypatch):
    row4 = _borisov_row4()
    calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(fe_redox_split={"Fe3Fet_Liq": 0.5}),
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)
    assert prediction.value == fe3_fe2_ratio_from_ferric_fraction(Decimal("0.5"))
    assert prediction.value == Decimal(1)
    assert len(calls) == 1


def test_melts_fe_redox_nonpositive_weights_do_not_fall_through(monkeypatch):
    row4 = _borisov_row4()
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={
                "FeO_wt_pct": 0.0,
                "Fe2O3_wt_pct": 1.0,
                "Fe3Fet_Liq": 0.5,
            }
        ),
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.METRIC_DOMAIN
    assert prediction.refusal_detail == {
        "reason": "predicted_fe3_fe2_ratio_nonpositive_or_nonfinite",
    }


def test_melts_fe_redox_without_a_split_stays_refused(monkeypatch):
    row4 = _borisov_row4()
    _patch_melts_cell(monkeypatch, lambda _handle: _ok_cell())
    prediction = predict_with_engine(Engine.ALPHAMELTS, row4, isolated=False)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX


def test_melts_fe_redox_honours_intrinsic_fo2_and_requires_printed_otherwise(monkeypatch):
    row4 = _without_printed_fo2(_borisov_row4())
    calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            liquid_composition_wt_pct={"FeO": 9.0, "Fe2O3": 1.0},
        ),
    )
    alphamelts = predict_with_engine(Engine.ALPHAMELTS, row4, isolated=False)
    assert alphamelts.value is None
    assert alphamelts.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert alphamelts.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }
    assert calls == []

    thermoengine = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)
    assert thermoengine.value == fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal("1.0"), Decimal("9.0")
    )
    assert thermoengine.identity is not None
    assert thermoengine.identity.fO2_Pa is None
    assert len(calls) == 1
    assert calls[0]["po2"].mode == "engine_default"
    assert calls[0]["po2"].po2_bar is None
    assert calls[0]["physical_pressure_bar"] == pytest.approx(1.01325)


def test_melts_fe_redox_commissioning_notice_is_the_only_extrapolated_flag(monkeypatch):
    row4 = _borisov_row4()
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={"FeO_wt_pct": 9.0, "Fe2O3_wt_pct": 1.0},
            authority="extrapolated",
            certified_band={"temperature_K": [1473.15, 1903.15]},
            notices=[
                {
                    "kind": "out_of_certified_band",
                    "authority": "extrapolated",
                    "reason": "melts above the certified band",
                    "certified_band": {"temperature_K": [1473.15, 1903.15]},
                }
            ],
        ),
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)
    assert prediction.value is not None
    assert prediction.authority is Authority.EXTRAPOLATED
    extrapolated = [
        notice
        for notice in prediction.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    ]
    assert len(extrapolated) == 1
    assert len(prediction.notices) == 1


def test_melts_fe_redox_timeout_is_attempted_unavailable(monkeypatch):
    row4 = _borisov_row4()
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            status="refusal",
            refusal_reason="engine_timeout",
            engine_reason="equilibrate exceeded hard timeout",
        ),
    )
    prediction = predict_with_engine(Engine.ALPHAMELTS, row4, isolated=False)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert prediction.refusal_detail["refusal_reason"] == "engine_timeout"


def test_melts_fe_redox_out_of_domain_is_predicted_and_flagged(monkeypatch):
    row4 = _borisov_row4()
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            status="refusal",
            engine_status="out_of_domain",
            engine_reason="fixture_domain_gate",
            fe_redox_split={"FeO_wt_pct": 7.1844, "Fe2O3_wt_pct": 15.9687},
        ),
    )

    prediction = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)

    assert prediction.value == Decimal("2")
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "fixture_domain_gate" in notice.reason
        for notice in prediction.notices
    )


def test_engines_without_a_ferric_split_do_not_equilibrate(monkeypatch):
    row4 = _borisov_row4()

    def _boom(*_args, **_kwargs):
        raise AssertionError("fe redox must not open or equilibrate this engine")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _boom,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _boom,
    )
    for engine in (Engine.MAGEMIN, Engine.VAPOROCK):
        prediction = predict_with_engine(engine, row4)
        assert prediction.value is None
        assert prediction.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
        assert prediction.coefficient_sources == (
            "Kress and Carmichael 1991, doi:10.1007/BF00307328",
        )
        assert prediction.refusal_detail == {
            "reason": "engine_does_not_report_fe_redox",
            "engine": engine.value,
            "detail": (
                "no fixed-T/fO2/composition ferric output without "
                "phase-equilibrium execution"
            ),
        }
    kress = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row4)
    assert kress.refusal_reason is None
    assert kress.value is not None
