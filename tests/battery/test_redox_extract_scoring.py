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
from simulator.battery.engine_capability import engine_covers_quantity
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
)
from simulator.battery.migrate import (
    FERRIC_FRACTION_RELATION,
    Migrator,
    _BARE_LOG_BAR_NOTICE,
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


def _subset_artifact(root: Path, name: str) -> Path:
    paths = tuple((root / "data/battery/subsets").glob(f"*/{name}"))
    assert len(paths) == 1
    return paths[0]


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


def _printed_fraction_width(observation):
    uncertainty = observation.uncertainty
    if uncertainty is None:
        return None
    source_value, _shape, mapped_unit = score_module._printed_uncertainty_source(
        uncertainty,
        exact_fields=("Fe3_over_FeT", "Fe3_over_sum_Fe"),
    )
    if source_value is None:
        return None
    width = (
        (source_value[1] - source_value[0]) / Decimal(2)
        if isinstance(source_value, tuple)
        else source_value
    )
    raw_text = score_module._uncertainty_original(uncertainty)
    source_unit = score_module._uncertainty_unit(
        Quantity.FE3_FE2_RATIO,
        uncertainty,
        observation,
        mapped_unit,
        source_value,
        raw_text,
    )
    converted, converted_unit = score_module._convert_uncertainty_width(
        Quantity.FE3_FE2_RATIO,
        width,
        source_unit,
        source_observation=observation,
    )
    if converted is None or converted_unit not in {
        "fraction",
        score_module.QUANTITY_UNITS[Quantity.FE3_FE2_RATIO],
    }:
        return None
    return converted


def _printed_fraction_sigma_r(observation):
    width = _printed_fraction_width(observation)
    if width is None:
        return None
    confidence_level = (
        score_module._stated_printed_confidence_level(observation.uncertainty)
        or "not_stated"
    )
    confidence_scale = (
        Decimal(2)
        if confidence_level == "two_sigma"
        else score_module.NORMAL_95_TICKET_MULTIPLIER
        if confidence_level == "ci95"
        else Decimal(1)
    )
    return width / confidence_scale


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
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
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=context.experiments[observation.experiment_id],
    )

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.refusal_reason is None
    quench = [
        notice.reason
        for notice in prediction.notices
        if notice.reason.startswith("single_melt_evidence_quench_glass")
    ]
    assert quench == [
        "single_melt_evidence_quench_glass: "
        "observation.phase: quenched peridotite glass; "
        "experiment.thermal_schedule.cooling_or_quench: "
        "Laser switched off; reported cooling rate approximately 800 °C/s"
    ]
    assert prediction.execution.call_evidence == "openimcc.evaluate_redox"
    assert prediction.authority is Authority.BRIDGE
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0]["Fe"] > 0.0
    assert args[0]["Mg"] > 0.0
    assert args[2] == "imposed"
    assert args[1] == float(observation.identity.temperature_K.value)
    assert float(observation.point_conditions["fO2_log"].state.value) == 0.0
    assert observation.identity.fO2_Pa.value == Decimal("100000")
    assert kwargs["lambda_imposed"] == pytest.approx(0.0, abs=1e-12)
    assert prediction.identity.fO2_Pa.value == Decimal("100000")
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in prediction.notices
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
    assert residuals[0].status is ResidualStatus.MISMATCH
    assert residuals[0].refusal is None
    assert not any(
        notice.reason == "uncertainty_confidence_level_assumed_1sigma"
        for notice in residuals[0].notices
    )


def test_internal_analytical_sossi_pure_oxygen_commands_one_bar(monkeypatch):
    """The Kress route commands the same 1 bar pure-O2 end member."""

    context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    experiment = context.experiments[observation.experiment_id]
    assert observation.identity.fO2_Pa.value == Decimal("100000")
    captured: list[float] = []
    import simulator.fe_redox as fe_redox

    original = fe_redox.kress91_split

    def capture_split(**kwargs):
        captured.append(kwargs["fO2_log"])
        return original(**kwargs)

    monkeypatch.setattr(fe_redox, "kress91_split", capture_split)
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
    )

    assert prediction.value is not None, prediction.refusal_detail
    assert captured
    assert captured == [0.0] * len(captured)
    assert prediction.identity is not None
    assert prediction.identity.fO2_Pa is not None
    assert prediction.identity.fO2_Pa.value == Decimal("100000")
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in prediction.notices
    )


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
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
            and notice.origin == "openimcc.evaluate_redox"
        )
        quench_notices = tuple(
            notice
            for notice in prediction.notices
            if notice.reason.startswith("single_melt_evidence_quench_glass")
        )
        assert flags
        assert tuple(notice.reason for notice in flag_notices) == flags
        if observation_id.startswith("sossi-2020-"):
            assert len(quench_notices) == 1
        else:
            assert quench_notices == ()
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
    assert total_flags == 166


def test_openimcc_sossi_aithala_redox_residuals_remain_bit_identical():
    """Golden pin for extracting the shared inventory and evidence helpers."""
    pytest.importorskip("openimcc")
    context, _observations = _sossi_aithala_redox_context()
    residuals, _ = score_store(
        context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    values = [
        residual
        for residual in residuals
        if residual.numeric is not None
    ]
    assert len(values) == 18
    # The pure-O2 end member is a printed 1 bar frame. No row in this set
    # is scale-unknown, so none carries DP-010. Eligibility stays false.
    unknown_rows = []
    grounded_rows = []
    for residual in values:
        observation = context.observations[residual.reference]
        if _scale_unknown_oxygen(observation):
            unknown_rows.append(residual)
            assert any(
                notice.policy is not None
                and notice.policy.id == "DP-010"
                and _BARE_LOG_SCALE_UNKNOWN in notice.reason
                and "printed_fO2_log=" in notice.reason
                for notice in residual.notices
            )
            assert not residual.score_eligible
            assert "not_flagged_stratum" in residual.exclusions
        else:
            grounded_rows.append(residual)
            assert not any(
                notice.policy is not None and notice.policy.id == "DP-010"
                for notice in residual.notices
            )
    assert unknown_rows == []
    assert len(grounded_rows) == 18
    # d-460 flags the unknown-frame row, and the other rows stay ineligible
    # on the same conjuncts as the internal-analytical table. An empty
    # eligible list is the pin. Requiring a score_eligible row was the
    # contradiction with that table.
    eligible = [residual for residual in values if residual.score_eligible]
    assert eligible == []
    backlog = score_module.notice_backlog(values)
    assert backlog.get(NoticeKind.ASSUMED_FO2.value, 0) == sum(
        1
        for residual in unknown_rows
        for notice in residual.notices
        if notice.kind is NoticeKind.ASSUMED_FO2
        and notice.policy is not None
        and notice.policy.id == "DP-010"
    )
    encoded = json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode()
    # Provisional pin; regenerate at the final integration pin.
    assert hashlib.sha256(encoded).hexdigest() == (
        "cdbef7d7303e5c0dea58ff312dbdfa59dcaff988cb5417b1a11db2d825cea011"
    )
    for backlog in (
        score_module.notice_backlog(residuals),
        score_module.notice_backlog_payloads(
            [residual_to_plain(residual) for residual in residuals]
        ),
    ):
        assert backlog.get(NoticeKind.SOURCE_DISAGREEMENT.value, 0) == 0
        assert backlog["engine_model_caveat"] == 178
        assert backlog["admission_defaulted"] == 18
        assert backlog["total_iron_reported_as_feo"] == 18
    assert all(
        not score_module.flagged_strata(residual.notices) for residual in residuals
    )
    assert not any(residual.score_eligible for residual in residuals)


def test_openimcc_redox_without_printed_fo2_uses_the_registered_default():
    """Absent printed oxygen with composition and temperature is DP-010.

    d-460 refuses only when the engine cannot be called. This row still has
    iron, composition, and temperature. The notice names the absence, not
    the unknown-scale sentence, so a removed log stays distinct from a
    printed zero whose frame was not stated.
    """

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

    assert prediction.refusal_reason is None
    assert prediction.value is not None
    assert any(
        notice.policy is not None
        and notice.policy.id == "DP-010"
        and "no printed fO2, applicable buffer, or air-composition evidence"
        in notice.reason
        and "reference scale not printed" not in notice.reason
        for notice in prediction.notices
    )


def test_unknown_scale_without_frame_or_atmosphere_keeps_dp010():
    """DP-010 stays when the log has no frame and no governing atmosphere.

    The pure-O2 end member is printed evidence. This control removes that
    frame and the experiment atmosphere, and keeps the printed zero so the
    notice stays the unknown-scale sentence rather than the absence sentence.
    """

    pytest.importorskip("openimcc")
    from simulator.battery.migrate import (
        _BARE_LOG_BAR_NOTICE,
        _BARE_LOG_SCALE_UNKNOWN,
        _bare_log_frame_decision,
        located_unknown,
    )

    context, observations = _sossi_aithala_redox_context()
    observation = observations[
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    refused = _bare_log_frame_decision(
        Decimal("0.00"),
        Decimal("2173.15"),
        printed_atmospheres=(),
        total_pressure_pa=Decimal("100000"),
    )
    assert refused.state.is_unknown
    assert refused.state.reason == _BARE_LOG_SCALE_UNKNOWN
    experiment = context.experiments[observation.experiment_id]
    conditions = {
        key: value
        for key, value in experiment.conditions.items()
        if "gas" not in key.casefold() and "atmosphere" not in key.casefold()
    }
    assert conditions
    stripped = replace(
        experiment,
        conditions=conditions,
        fO2_control=None,
        ambient_pressure_evidence=None,
        pressure_environment=replace(
            experiment.pressure_environment,
            sweep_gas=located_unknown("control has no governing atmosphere"),
        ),
    )
    point_conditions = {
        key: value
        for key, value in (observation.point_conditions or {}).items()
        if "gas" not in key.casefold() and "atmosphere" not in key.casefold()
    }
    assert "fO2_log" in point_conditions
    control = replace(
        observation,
        point_conditions=point_conditions,
        notices=tuple(
            notice
            for notice in observation.notices
            if notice.kind is not NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
            and notice.reason != _BARE_LOG_BAR_NOTICE
        ),
        identity=replace(observation.identity, fO2_Pa=refused.state),
    )

    for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
        prediction = predict_with_engine(engine, control, experiment=stripped)
        assert prediction.value is not None, (engine, prediction.refusal_detail)
        assert any(
            notice.policy is not None
            and notice.policy.id == "DP-010"
            and _BARE_LOG_SCALE_UNKNOWN in notice.reason
            and "printed_fO2_log=0" in notice.reason
            for notice in prediction.notices
        ), engine


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


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
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
    assert float(row.point_conditions["fO2_log"].state.value) == 0.0
    assert kwargs["lambda_imposed"] == pytest.approx(0.0, abs=1e-12)
    assert prediction.identity.fO2_Pa.value == Decimal("100000")
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in prediction.notices
    )


def test_openimcc_missing_fo2_without_kress_iron_refuses_printed_oxygen():
    """Silica with no iron cannot call OpenIMCC. Absent fO2 is not that refusal.

    d-460 supplies DP-010 when composition and temperature exist. The engine
    still cannot be called with no iron, so the refusal is total_iron.
    """

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
        "input": "total_iron",
    }
    assert prediction.coefficient_sources == ()


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_openimcc_borisov_row_follows_the_train_redox_path(monkeypatch):
    """Borisov row 4 on OpenIMCC is the train parent's produced ratio.

    ``8e1d1ef23d`` carries this receipt onto L after the OpenIMCC numeric
    repin ``5c670398e0`` (``9c327c2``) and DG46 redox pack ``938c3e3717``;
    that moved the Decimal from ``1.6414352204105178`` to
    ``2.3552879363945403``. It comes from ``openimcc.evaluate_redox`` on
    the printed mole-fraction composition. The row is not a mass-percent
    stand-in. OpenIMCC must not open or equilibrate a cell. The pack digest
    is the digest on the result the scorer just stamped, not a host-specific
    literal.
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
    assert prediction.value == Decimal("2.3552879363945403")
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


def test_internal_analytical_sossi_aithala_redox_payload_pin():
    context, observations = _sossi_aithala_redox_context()
    residuals, _ = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )
    sossi_sd_ids = {
        observation_id
        for observation_id, observation in observations.items()
        if observation.source_id == "sossi-2020-magma-ocean-redox"
        and isinstance(observation.uncertainty.verbatim, dict)
        and "confidence_level" not in observation.uncertainty.verbatim
    }
    assert len(sossi_sd_ids) == 11
    sossi_sd_residuals = [
        residual for residual in residuals if residual.reference in sossi_sd_ids
    ]
    assert len(sossi_sd_residuals) == 11
    expected_statuses = {
        "aithala_2026_1800_30_xanes": ResidualStatus.MATCH,
        "aithala_2026_1900_30_xanes": ResidualStatus.MISMATCH,
        "aithala_2026_2000_10_xanes": ResidualStatus.MISMATCH,
        "aithala_2026_2000_120_xanes": ResidualStatus.MISMATCH,
        "aithala_2026_2000_30_xanes": ResidualStatus.MISMATCH,
        "aithala_2026_2100_30_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_1_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_2_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_3_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_6_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_7_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_8_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_9_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_f_o2_10_xanes": ResidualStatus.MATCH,
        "sossi_2020_per_f_o2_11_xanes": ResidualStatus.MATCH,
        "sossi_2020_per_f_o2_12_xanes": ResidualStatus.MATCH,
        "sossi_2020_per_ts1_xanes": ResidualStatus.MISMATCH,
        "sossi_2020_per_ts2_xanes": ResidualStatus.MISMATCH,
    }
    assert {
        residual.reference.rsplit("::", 1)[-1]: residual.status
        for residual in residuals
    } == expected_statuses
    boundary_refusals = []
    for residual in sossi_sd_residuals:
        assert residual.numeric is not None
        observation = observations[residual.reference]
        fraction = dict(observation.derivation.parameters)[
            "original_Fe3_over_FeT"
        ].state.value
        printed_width = _printed_fraction_width(observation)
        assert printed_width is not None
        if printed_width >= fraction or printed_width >= (
            Decimal(1) - fraction
        ):
            assert residual.numeric.decision_band is None
            assert residual.status is ResidualStatus.NO_BAND
            assert residual.refusal is not None
            assert residual.refusal.detail["reason"] == "printed_uncertainty_outside_metric_domain"
            boundary_refusals.append(residual.reference)
        else:
            assert residual.refusal is None
            assert residual.numeric.decision_band is not None
            assert residual.numeric.decision_band.rule.startswith("printed_1sigma:")
        assert not any(
            notice.reason == "uncertainty_confidence_level_assumed_1sigma"
            or "two_sigma_reading_verdict=" in (notice.band or "")
            for notice in residual.notices
        )
    assert boundary_refusals == []
    unknown = []
    grounded = []
    for residual in residuals:
        observation = observations[residual.reference]
        has_default = any(
            notice.policy is not None and notice.policy.id == "DP-010"
            for notice in residual.notices
        )
        if _scale_unknown_oxygen(observation):
            unknown.append(residual)
            assert residual.numeric is not None
            assert residual.numeric.value.is_finite()
            assert has_default
            assert not residual.score_eligible
            assert "not_flagged_stratum" in residual.exclusions
        else:
            grounded.append(residual)
            assert residual.numeric is not None
            assert residual.numeric.value.is_finite()
            assert not has_default
            assert "not_flagged_stratum" in residual.exclusions
    assert unknown == []
    assert len(grounded) == 18
    backlog = score_module.notice_backlog(residuals)
    assert backlog.get(NoticeKind.ASSUMED_FO2.value, 0) == 0
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
    # walks are complete.
    # d-342 retarget. At b1cb2dec4 the payload hashed to
    # 1ab48f4d30da838c5ae5a6445b3a773bee1c04738637e2693f7b2e17263d46b2
    # and the twelve Sossi rows were refused input_phase_assemblage_unstated.
    # Those twelve now score. The six Aithala rows are byte-identical.
    # t-1792 validates the ten Sossi gas-ratio rows against their printed
    # atmosphere. Nine fO2 identities match green again; fO2-10 keeps its
    # pressure. Their residual notices now carry the certified atmosphere
    # frame instead of the prior 1-bar-convention notice. The two Sossi rows
    # without gas ratios and all six Aithala rows are unchanged by t-1792.
    # d-341 (t-1646) assumes 1 sigma only where neither metadata nor printed
    # text states the level; the Sossi rows' printed SD caption keeps their
    # primary 1-sigma verdicts without the assumption/alternative notices.
    # t-1646 round 5 refused Sossi (11)/(12) because the normalized standard
    # width crossed r=0. Their original one-sigma intervals are in-domain, so
    # each now has a printed band and no refusal. Relative to round 10's
    # 0140d9de... payload, serialization changes each row's status from NO_BAND
    # to MATCH, numeric.decision_band from None to its printed-width-derived
    # ticket, and refusal from the typed ferric linearization refusal to None.
    # Those fields follow directly from the original printed interval passing
    # the domain gate; the remaining sixteen rows' statuses remain unchanged.
    # fam13 binds the six Aithala Table S-1 Fe rows to the physical SI PDF
    # (page 13, SI-7; SHA-256 ab859d250ab0d4f0b1def8c7b894f22604bb132d644d112769c8e1c27ef750fa).
    # Their printed redox values and decisions are unchanged; the serialized
    # locator changes from source_path=None to that PDF path. The combined
    # payload hash therefore moves from 6a3fb7b24e11ad8c838ada5e83177719d6463ebc6683b3faccfec9257b865862
    # Round-10 pin 0140d9de1cf1d76f49fcf1a1c1889efb3153a95adb25768a39d901cc175ddb50
    # is refreshed to 3fc087feb0df00013c1632d3a5bf05aea7448739fc17f7e82d18f7bc95dac925.
    # DP-112's registry flag now admits its canonical-buffer notices, adding
    # them to the serialized rows and producing the merged payload pin below.
    assert hashlib.sha256(encoded).hexdigest() == (
        "9d1888c0ad721e9eb64c4af53fd256af673e1229fb4e261da300f96b168d0e05"
    )


def test_real_sossi_aithala_bands_propagate_fraction_uncertainty_to_dex():
    context, observations = _sossi_aithala_redox_context()
    residuals, _ = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )
    residual_by_reference = {row.reference: row for row in residuals}
    assert set(residual_by_reference) == set(observations)
    assert len(residual_by_reference) == 18

    boundary_refusals = []
    for reference, observation in observations.items():
        derivation_parameters = dict(observation.derivation.parameters)
        fraction = derivation_parameters["original_Fe3_over_FeT"].state.value
        printed_sigma = _printed_fraction_sigma_r(observation)
        assert isinstance(fraction, Decimal)
        assert isinstance(printed_sigma, Decimal)
        residual = residual_by_reference[reference]
        assert residual.numeric is not None
        assert residual.numeric.value.is_finite()

        # Independent first-order propagation into the scorer's log10 ratio
        # metric, using only each row's recorded printed r and sigma_r.
        printed_width = _printed_fraction_width(observation)
        assert printed_width is not None
        crosses_log_domain = (
            printed_width >= fraction or printed_width >= Decimal(1) - fraction
        )
        if crosses_log_domain:
            assert residual.numeric.decision_band is None
            assert residual.status is ResidualStatus.NO_BAND
            assert residual.refusal is not None
            assert residual.refusal.detail["reason"] == "printed_uncertainty_outside_metric_domain"
            boundary_refusals.append(reference)
        else:
            assert residual.refusal is None
            assert residual.numeric.decision_band is not None
            expected_band = (
                printed_sigma
                / (Decimal(10).ln() * fraction * (Decimal(1) - fraction))
                * score_module.NORMAL_95_TICKET_MULTIPLIER
            )
            assert abs(residual.numeric.decision_band.value - expected_band) < Decimal("1e-20")

    assert boundary_refusals == []
    assert sum(row.status is ResidualStatus.NO_BAND for row in residuals) == 0
    # The owner hold defers the exact MATCH/MISMATCH split for the two rows
    # whose bands were restored; Studio must refresh the serialized payload pin.
    assert sum(
        row.status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}
        for row in residuals
    ) == 18
    aithala_1800_30 = next(
        observation_id
        for observation_id, observation in observations.items()
        if observation.source_id == "aithala-2026-magmatic-iron-redox-to-2100c"
        and observation.experiment_id.rsplit("::", 1)[-1] == "aithala-2026-1800-30"
    )
    assert residual_by_reference[aithala_1800_30].status is ResidualStatus.MATCH


def test_fraction_uncertainty_boundaries_cover_all_migrated_source_rows():
    root = Path(__file__).resolve().parents[2]
    extracts = root / "data/literature/extracts"
    source_paths = [
        path
        for path in sorted(extracts.glob("*.yaml"))
        if "Fe3_over_FeT" in path.read_text(encoding="utf-8")
        or "Fe3_over_sum_Fe" in path.read_text(encoding="utf-8")
    ]
    assert len(source_paths) == 11

    migrator = Migrator(root)
    for path in source_paths:
        migrator._migrate_extract(path)
    migrator.finalize()
    fraction_rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.FE3_FE2_RATIO
        and observation.derivation is not None
        and FERRIC_FRACTION_RELATION in (observation.derivation.relation or "")
        and observation.value.kind.value == "point"
    ]
    assert len(fraction_rows) == 168

    invalid_rows = []
    band_counts = {}
    for observation in fraction_rows:
        fraction_parameter = next(
            parameter
            for name, parameter in observation.derivation.parameters
            if name in {"original_Fe3_over_FeT", "original_Fe3_over_sum_Fe"}
        )
        fraction = fraction_parameter.state.value
        resolution = score_module._printed_uncertainty_band(
            Quantity.FE3_FE2_RATIO,
            observation.uncertainty,
            observation.value.point,
            source_observation=observation,
        )
        printed_width = _printed_fraction_width(observation)
        if resolution.band is not None:
            assert printed_width is not None
            assert printed_width < fraction
            assert printed_width < Decimal(1) - fraction
            band_counts[observation.source_id] = (
                band_counts.get(observation.source_id, 0) + 1
            )
        elif (
            resolution.refusal is not None
            and resolution.refusal.get("reason")
            == "printed_uncertainty_outside_metric_domain"
        ):
            assert printed_width is not None
            assert printed_width >= fraction or printed_width >= (
                Decimal(1) - fraction
            )
            invalid_rows.append(observation.observation_id)

    assert band_counts == {
        "aithala-2026-magmatic-iron-redox-to-2100c": 16,
        "cottrell-2009-iron-xanes": 18,
        "sossi-2020-magma-ocean-redox": 12,
    }
    assert invalid_rows == []


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
    # t-1534 stored this conversion on derivation.raw_inputs for every
    # measured_reduced row. The car types the 404 supplementary rows
    # quoted_attributed, so the same conversion stays on values.inference.
    # raw_inputs is not valid on quoted evidence.
    conversion = "x = log10(Fe3+/Fe2+) -> ratio = 10^x, dimensionless; x = 0 -> 1."
    for row in table1_rows:
        assert row["values"]["evidence_class"] == "measured_direct"
        assert row["values"]["method_class"] == "measured_direct"
        assert "inference" not in row["values"]
        assert "derivation" not in row
    assert next(
        row["values"]["fe3_fe2_ratio"]
        for row in table1_rows
        if row["observation_id"] == "borisov_2018_table1_row_1"
    ) == "2.83"
    for row in inferred_rows:
        values = row["values"]
        inference = values["inference"]
        assert values["evidence_class"] == "quoted_attributed"
        assert values["method_class"] == "quoted_attributed"
        assert "derivation" not in row
        assert values["ratio_derivation"] == conversion
        assert inference["relation"] == conversion
        assert inference["inputs"] == ["log10_Fe3_over_Fe2_as_published"]
        original = inference["parameters"][0][1]
        assert original["state"]["value"] == values["log10_Fe3_over_Fe2_as_published"]
        assert original["locator"] == row["locator"]
        observation = result.observations[f"{extract.stem}::{row['observation_id']}"]
        assert observation.derivation is not None
        migrated_conversion = observation.derivation.parameters[0][1].inference
        assert migrated_conversion is not None
        assert migrated_conversion.relation == conversion

    hard_issue_details = [issue.detail for issue in result.validation.hard_issues]
    assert hard_issue_details.count("derived observation requires derived_from") == 0
    assert hard_issue_details == []
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
    # t-1534 scored borisov_2018_table2_row_155 near 3.277. Spreadsheet rows
    # 155-185 are article Table 1 (31 wet-chemical ratios, printed Fe3+/Fe2+
    # for row 1 is 2.83). That id is not a second scored observation.
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
    # d-460: composition and temperature are enough to call the engine.
    # The command is the registered default, not the removed printed log.
    printed_log = row4.point_conditions["fO2_log"].state.value
    assert missing_prediction.refusal_reason is None
    assert missing_prediction.value is not None
    assert missing_prediction.value.is_finite()
    default_notices = [
        notice
        for notice in missing_prediction.notices
        if notice.policy is not None and notice.policy.id == "DP-010"
    ]
    assert len(default_notices) == 1
    assert "commanded_default_log10_bar=" in default_notices[0].reason
    assert "printed_fO2_log=" not in default_notices[0].reason
    assert default_notices[0].policy.value not in {
        format(printed_log, "f"),
        str(printed_log),
    }

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
    # METHOD_UNSTATED/PHASE_INFERRED (ea358c0945) flag all rows; 10 also carry
    # out_of_certified_band.
    assert (
        measured_unknown["certified_numeric"],
        measured_unknown["flagged_numeric"],
    ) == (0, 404)
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
    # The oxygen owner commands a positive identity fO2. The disagreement
    # has to be on the prediction identity; planting it on the row is the command.
    # Later cases reuse this observation id, so only the planted 1000 Pa is rewritten.
    original_predict = score_module.predict_with_engine

    def prediction_with_other_oxygen(engine, observation, **kwargs):
        prediction = original_predict(engine, observation, **kwargs)
        planted = (
            observation.identity.fO2_Pa
            if observation.identity is not None
            else None
        )
        if (
            planted is None
            or not planted.is_value
            or planted.value != Decimal("1000")
            or prediction.identity is None
        ):
            return prediction
        return replace(
            prediction,
            identity=replace(
                prediction.identity, fO2_Pa=State.of(Decimal("1"))
            ),
            notices=tuple(
                notice
                for notice in prediction.notices
                if notice.origin != "fo2_ladder"
            ),
        )

    monkeypatch.setattr(
        score_module, "predict_with_engine", prediction_with_other_oxygen
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
        _subset_artifact(tmp_path, "score-summary.json").read_text(encoding="utf-8")
    )
    _assert_unknown_redox_headline(summary["records"])
    report = _subset_artifact(tmp_path, "score-report.md").read_text(encoding="utf-8")
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
        _subset_artifact(tmp_path, "score-summary.json").read_text(encoding="utf-8")
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
    report = _subset_artifact(tmp_path, "score-report.md").read_text(encoding="utf-8")
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
        if identity is None:
            return prediction
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
            # A positive identity fO2 is the command, so the row's planted
            # 1000 Pa is not a conflict. The prediction has to disagree.
            conflict = replace(identity, fO2_Pa=State.of(Decimal("1")))
        updated = replace(prediction, identity=conflict)
        if axis == "fO2_Pa":
            updated = replace(
                updated,
                notices=tuple(
                    notice
                    for notice in updated.notices
                    if notice.origin != "fo2_ladder"
                ),
            )
        return updated

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


def test_aithala_redox_rows_score_when_printed_composition_is_joined():
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
        if row.identity.composition is not None and row.identity.composition.is_value
    }
    assert len(projected_ids) == 16
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: extract.name for key in result.observations},
        benches=result.benches,
    )
    residuals, _ = score_store(context, engines=SCORE_ENGINE_SET, rail=Rail.REDOX)
    covered = tuple(
        engine
        for engine in SCORE_ENGINE_SET
        if engine_covers_quantity(engine, Quantity.FE3_FE2_RATIO)
    )
    assert Engine.CATALOG_EVALUATOR not in covered
    assert Engine.NASA_CEA_9 in covered
    assert len(residuals) == len(redox_rows) * len(covered)
    assert not any(
        residual.key.rsplit("::", 1)[-1] == Engine.CATALOG_EVALUATOR.value
        for residual in residuals
    )
    for residual in residuals:
        observation_id = residual.key.split(":T=", 1)[0]
        engine = residual.key.rsplit("::", 1)[-1]
        if observation_id in projected_ids and engine in {
            "internal-analytical",
            "openimcc",
        }:
            assert residual.numeric is not None
            assert residual.refusal is None
            assert residual.numeric.decision_band is not None
            assert residual.numeric.decision_band.rule.startswith("printed_1sigma:")
            assert any(
                notice.reason == "uncertainty_confidence_level_assumed_1sigma"
                for notice in residual.notices
            )
        elif engine in {"alphamelts", "thermoengine"}:
            # Composition is printed, so these engines attempt a ferric split.
            # The unsupported-engine refusal is the one from before that path.
            if (
                residual.refusal is not None
                and residual.refusal.reason
                is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
            ):
                assert residual.refusal.detail.get("detail") != (
                    "no fixed-T/fO2/composition ferric output without "
                    "phase-equilibrium execution"
                )
        elif engine == "catalog_evaluator":
            # Gate 9's catalog evaluator answers vapour pressures only.
            # Fe3+/Fe2+ stays a quantity refusal, not a ferric-split refusal.
            assert residual.numeric is None
            assert residual.refusal is not None
            assert residual.refusal.reason is RefusalReason.UNSUPPORTED
        else:
            assert residual.numeric is None
            assert residual.refusal is not None
            assert (
                residual.refusal.reason
                is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
            )


def test_kress_table1_redox_rows_score_at_ambient_furnace_pressure():
    # SCHEMA.md: an ambient_pressure_evidence quote supplies 101325 Pa when
    # total pressure is otherwise missing. Kress Table 1's gas_mixing quote is
    # that evidence. The row text says the uncertainty was not printed, so the
    # score has no band and is not an unpublished-pressure refusal.
    root = Path(__file__).resolve().parents[2]
    extract = (
        root
        / "data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml"
    )
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    hard_issue_details = [issue.detail for issue in result.validation.hard_issues]
    assert hard_issue_details.count("derived observation requires derived_from") == 0
    assert result.validation.hard_issues == ()
    redox_rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.FE3_FE2_RATIO
    ]
    assert len(redox_rows) == 63
    for row in redox_rows:
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        ambient = [
            notice
            for notice in row.notices
            if notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
        ]
        assert ambient
        assert all(
            notice.authority is Authority.DERIVED_FROM_APPARATUS
            and notice.band is None
            for notice in ambient
        )
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: extract.name for key in result.observations},
        benches=result.benches,
    )
    residuals, _ = score_store(
        context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    residuals = [
        residual
        for residual in residuals
        if "kress-carmichael-1988-iron-oxidation-stoichiometry" in residual.key
    ]
    assert len(residuals) == 63
    assert all(
        residual.refusal is None
        and residual.numeric is not None
        and residual.numeric.value.is_finite()
        and residual.numeric.decision_band is None
        and residual.status is ResidualStatus.NO_BAND
        for residual in residuals
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


def _scale_unknown_oxygen(observation) -> bool:
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    state = observation.identity.fO2_Pa
    return (
        state is not None
        and state.is_unknown
        and state.reason == _BARE_LOG_SCALE_UNKNOWN
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
    _resolved_identity, printed, oxygen_notices = score_module._resolve_row_fo2(
        row4.identity,
        row4,
        None,
        {},
        Decimal(0),
        Quantity.FE3_FE2_RATIO,
    )
    assert printed is not None
    assert not any(
        item.policy is not None and item.policy.id == "DP-010" for item in oxygen_notices
    )
    assert all(
        item.reason == _BARE_LOG_BAR_NOTICE
        or item.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        for item in oxygen_notices
    )
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


def test_standalone_melts_fe_redox_prediction_closes_its_engine_handle(monkeypatch):
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle

    row4 = _borisov_row4()
    opened = []

    class FakeBackend:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    def open_fake_engine(name):
        handle = EngineHandle(
            name=name,
            backend=FakeBackend(),
            available=True,
            unavailable_reason=None,
            takes_fo2=True,
            supports_intrinsic_fo2=True,
        )
        opened.append(handle)
        return handle

    monkeypatch.setattr(binary_pot_battery, "open_battery_engine", open_fake_engine)
    monkeypatch.setattr(
        binary_pot_battery,
        "equilibrate_cell",
        lambda *_args, **_kwargs: _ok_cell(
            fe_redox_split={"FeO_wt_pct": 9.0, "Fe2O3_wt_pct": 1.0}
        ),
    )

    prediction = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)

    assert prediction.value == fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal("1.0"), Decimal("9.0")
    )
    assert len(opened) == 1
    assert opened[0].backend.close_calls == 1


def test_thermoengine_fe_redox_reaches_equilibrate_cell_with_cache_enabled(
    tmp_path, monkeypatch
):
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        normalize_engine_settings,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    row4 = _borisov_row4()

    class FakeBackend:
        calls = 0

        def equilibrate(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                liquid_fraction=0.5,
                phase_assemblage_available=True,
                activity_coefficients={},
                vapor_pressures_Pa={},
                vapor_pressures_source={},
                liquid_composition_wt_pct={"FeO": 9.0, "Fe2O3": 1.0},
                fe_redox_split={"FeO_wt_pct": 9.0, "Fe2O3_wt_pct": 1.0},
            )

    backend = FakeBackend()
    handle = EngineHandle(
        name=Engine.THERMOENGINE.value,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
        engine_settings=normalize_engine_settings(
            Engine.THERMOENGINE.value, None
        ),
    )
    handles = {Engine.THERMOENGINE.value: handle}

    first = predict_with_engine(
        Engine.THERMOENGINE, row4, handles=handles, isolated=False
    )
    second = predict_with_engine(
        Engine.THERMOENGINE, row4, handles=handles, isolated=False
    )

    assert first.value == second.value == fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal("1.0"), Decimal("9.0")
    )
    assert backend.calls == 1
    assert engine_cache.engine_cache_counters(reset=True)["thermoengine"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }


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
    """Absent printed oxygen is the registered default on both MELTS engines.

    The cell is commanded with the admitted pascal pressure over 1 bar.
    The log is the production resolver's command, not a pasted intrinsic.
    """

    from simulator.physical_constants import BAR_PA_DECIMAL

    row4 = _without_printed_fo2(_borisov_row4())
    calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            liquid_composition_wt_pct={"FeO": 9.0, "Fe2O3": 1.0},
        ),
    )
    expected = fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1.0"), Decimal("9.0"))
    admitted_pa = None
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE):
        prediction = predict_with_engine(engine, row4, isolated=False)
        assert prediction.refusal_reason is None
        assert prediction.value == expected
        assert prediction.identity is not None
        assert prediction.identity.fO2_Pa is not None
        assert prediction.identity.fO2_Pa.is_value
        pressure = prediction.identity.fO2_Pa.value
        assert isinstance(pressure, Decimal) and pressure > 0
        if admitted_pa is None:
            admitted_pa = pressure
        else:
            assert pressure == admitted_pa
        assert any(
            notice.policy is not None and notice.policy.id == "DP-010"
            for notice in prediction.notices
        )
    assert admitted_pa is not None
    assert len(calls) == 2
    for call in calls:
        assert call["po2"].mode == "commanded"
        assert call["po2"].po2_bar == pytest.approx(
            float(admitted_pa / BAR_PA_DECIMAL)
        )
        assert call["physical_pressure_bar"] == pytest.approx(1.01325)


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
    for engine in (Engine.MAGEMIN, Engine.VAPOROCK, Engine.NASA_CEA_9):
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
