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
    FO2Channel,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    QuenchState,
    Rail,
    RefusalReason,
    ResidualStatus,
    ValueKind,
)
from simulator.battery.migrate import (
    FERRIC_FRACTION_RELATION,
    Migrator,
    experiment_from_plain,
    fe3_fe2_ratio_from_ferric_fraction,
    fe3_fe2_ratio_from_oxide_wt_pct,
    load_yaml,
    observation_from_plain,
    to_plain,
    write_outputs,
)
from simulator.battery.oxygen_identity import log10_fo2_to_pa
from simulator.battery.redox_fields import _printed_cell, _target_cells
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    predict_with_engine,
    score_store,
    SCORE_ENGINE_SET,
)
from simulator.battery.records import ApproxPressure, Composition, Derivation, FO2Control, Located, Locator, State, Value
from simulator.battery.score import headline_payload_records, residual_to_plain
from simulator.physical_constants import BAR_PA_DECIMAL
from tests.battery import factories as F
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


def _printed_fraction_sigma_r(observation):
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
    confidence_level = (
        score_module._stated_printed_confidence_level(uncertainty) or "not_stated"
    )
    confidence_scale = (
        Decimal(2)
        if confidence_level == "two_sigma"
        else score_module.NORMAL_95_TICKET_MULTIPLIER
        if confidence_level == "ci95"
        else Decimal(1)
    )
    return converted / confidence_scale


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
    assert residuals[0].status is ResidualStatus.MISMATCH
    assert residuals[0].refusal is None
    assert not any(
        notice.reason == "uncertainty_confidence_level_assumed_1sigma"
        for notice in residuals[0].notices
    )


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
    assert total_flags == 130


def test_openimcc_sossi_aithala_redox_residuals_remain_bit_identical():
    """Golden pin for extracting the shared inventory and evidence helpers."""
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
    # Pinned with the declared OpenIMCC pin 66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84 (pyproject.toml); under e975edd the digest is 12100581….
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
        assert backlog["engine_model_caveat"] == 142
        assert backlog["admission_defaulted"] == 18
        assert backlog["total_iron_reported_as_feo"] == 18
    assert all(
        not score_module.flagged_strata(residual.notices) for residual in residuals
    )
    assert not any(residual.score_eligible for residual in residuals)


def test_openimcc_redox_without_ferric_ratio_or_control_uses_dp010_air():
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

    assert prediction.value is not None
    assert prediction.refusal_reason is None
    assert prediction.identity is not None
    assert prediction.identity.fO2_Pa is not None
    assert prediction.identity.fO2_Pa.is_value
    ambient_notice = next(
        notice
        for notice in prediction.notices
        if notice.kind
        is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    )
    assert prediction.identity.fO2_Pa.value == Decimal("21278.25")
    assert "log10(fO2/bar)=-0.672064092852" in ambient_notice.reason


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


def test_openimcc_missing_fo2_without_iron_reaches_iron_refusal_after_ladder():
    """The fO2 ladder resolves first; OpenIMCC then reports its missing iron."""

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


def test_openimcc_borisov_row_follows_the_train_redox_path(monkeypatch):
    """Borisov row 4 on OpenIMCC is the train parent's produced ratio.

    ``predict_with_engine(OPENIMCC, row4)`` returns this Decimal from
    ``openimcc.evaluate_redox`` on the printed mole-fraction
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
    assert all(n.kind is not NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL for n in prediction.notices)
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
    boundary_refusals = []
    for residual in sossi_sd_residuals:
        assert residual.numeric is not None
        observation = observations[residual.reference]
        fraction = dict(observation.derivation.parameters)[
            "original_Fe3_over_FeT"
        ].state.value
        sigma_r = _printed_fraction_sigma_r(observation)
        assert sigma_r is not None
        if Decimal(2) * sigma_r >= fraction or Decimal(2) * sigma_r >= (
            Decimal(1) - fraction
        ):
            assert residual.numeric.decision_band is None
            assert residual.status is ResidualStatus.NO_BAND
            assert residual.refusal is not None
            assert residual.refusal.detail["reason"] == (
                "ferric_fraction_uncertainty_linearization_invalid"
            )
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
    assert len(boundary_refusals) == 2
    assert any(reference.endswith("sossi_2020_per_f_o2_11_xanes") for reference in boundary_refusals)
    assert any(reference.endswith("sossi_2020_per_f_o2_12_xanes") for reference in boundary_refusals)
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
    # t-1646 round 5 refuses the Sossi (11)/(12) two-sigma spans that cross
    # r=0; the remaining source widths use the transformed log-ratio metric.
    # fam13 binds the six Aithala Table S-1 Fe rows to the physical SI PDF
    # (page 13, SI-7; SHA-256 ab859d250ab0d4f0b1def8c7b894f22604bb132d644d112769c8e1c27ef750fa).
    # Their printed redox values and decisions are unchanged; the serialized
    # locator changes from source_path=None to that PDF path. The combined
    # payload hash therefore moves from 6a3fb7b24e11ad8c838ada5e83177719d6463ebc6683b3faccfec9257b865862
    # to 0140d9de1cf1d76f49fcf1a1c1889efb3153a95adb25768a39d901cc175ddb50.
    assert hashlib.sha256(encoded).hexdigest() == (
        "0140d9de1cf1d76f49fcf1a1c1889efb3153a95adb25768a39d901cc175ddb50"
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
        crosses_log_domain = (
            Decimal(2) * printed_sigma >= fraction
            or Decimal(2) * printed_sigma >= Decimal(1) - fraction
        )
        if crosses_log_domain:
            assert residual.numeric.decision_band is None
            assert residual.status is ResidualStatus.NO_BAND
            assert residual.refusal is not None
            assert residual.refusal.detail["reason"] == (
                "ferric_fraction_uncertainty_linearization_invalid"
            )
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

    assert len(boundary_refusals) == 2
    assert {reference.rsplit("::", 1)[-1] for reference in boundary_refusals} == {
        "sossi_2020_per_f_o2_11_xanes",
        "sossi_2020_per_f_o2_12_xanes",
    }
    assert sum(row.status is ResidualStatus.MATCH for row in residuals) == 2
    assert sum(row.status is ResidualStatus.MISMATCH for row in residuals) == 14
    assert sum(row.status is ResidualStatus.NO_BAND for row in residuals) == 2
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
        sigma_r = _printed_fraction_sigma_r(observation)
        if resolution.band is not None:
            assert sigma_r is not None
            assert Decimal(2) * sigma_r < fraction
            assert Decimal(2) * sigma_r < Decimal(1) - fraction
            band_counts[observation.source_id] = (
                band_counts.get(observation.source_id, 0) + 1
            )
        elif (
            resolution.refusal is not None
            and resolution.refusal.get("reason")
            == "ferric_fraction_uncertainty_linearization_invalid"
        ):
            assert sigma_r is not None
            assert Decimal(2) * sigma_r >= fraction or Decimal(2) * sigma_r >= (
                Decimal(1) - fraction
            )
            invalid_rows.append(observation.observation_id)

    assert band_counts == {
        "aithala-2026-magmatic-iron-redox-to-2100c": 16,
        "cottrell-2009-iron-xanes": 18,
        "sossi-2020-magma-ocean-redox": 10,
    }
    assert len(invalid_rows) == 2
    assert {row.rsplit("::", 1)[-1] for row in invalid_rows} == {
        "sossi_2020_per_f_o2_11_xanes",
        "sossi_2020_per_f_o2_12_xanes",
    }


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


def test_borisov_2018_rows_reach_redox_scoring_population():
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
    # for row 1 is 2.83). That id is not a second scored observation. The 404
    # numeric supplementary predictions carry the missing-pressure flag.
    assert f"{extract.stem}::borisov_2018_table2_row_155" not in rows_by_id

    # The sheet gives distinct printed values for the run oxygen and ratio
    # even though both locators identify only the row.
    assert row4.locator.table == row4.point_conditions["fO2_log"].locator.table
    assert row4.locator.record == row4.point_conditions["fO2_log"].locator.record
    prediction4 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row4)
    assert abs(prediction4.value - Decimal("1.838360613")) < Decimal("1e-9")
    assert all(n.kind is not NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL for n in prediction4.notices)
    independent_oxygen = _with_borisov_column_locators(row4)
    explicit_prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, independent_oxygen)
    assert explicit_prediction.value == prediction4.value
    printed_log = row4.point_conditions["fO2_log"].state.value
    if isinstance(printed_log, Value):
        printed_log = printed_log.point
    assert isinstance(printed_log, Decimal)
    canonical_fo2 = log10_fo2_to_pa(printed_log)
    assert prediction4.identity is not None
    assert prediction4.identity.fO2_Pa is not None
    assert explicit_prediction.identity.fO2_Pa.value == canonical_fo2
    assert explicit_prediction.identity.fO2_Pa.value == row4.identity.fO2_Pa.value
    float_reconstruction = Decimal(str(100000.0 * 10.0 ** float(printed_log)))
    if canonical_fo2 != float_reconstruction:
        assert explicit_prediction.identity.fO2_Pa.value != float_reconstruction
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
    assert missing_prediction.value is not None
    assert missing_prediction.refusal_reason is None
    assert any(n.policy and n.policy.id == "DP-010" for n in missing_prediction.notices)

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
        independent_oxygen,
        identity=replace(independent_oxygen.identity, fO2_Pa=State.of(Decimal("1000"))),
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
            _with_borisov_column_locators(reference),
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
    assert len(residuals) == 16 * len(SCORE_ENGINE_SET)
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
    residuals, candidates = score_store(
        context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    residuals = [
        residual
        for residual in residuals
        if "kress-carmichael-1988-iron-oxidation-stoichiometry" in residual.key
    ]
    assert len(residuals) == 63
    for residual in residuals:
        reference = result.observations[residual.reference]
        printed_log = score_module._printed_fe_redox_fo2_log(reference, reference.identity)
        assert printed_log is not None
        assert candidates[residual.candidate].identity.fO2_Pa.value == log10_fo2_to_pa(printed_log)
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


def _with_borisov_column_locators(row):
    oxygen = row.point_conditions["fO2_log"]
    return replace(
        row,
        locator=replace(row.locator, note="column=Fe3+/Fe2+"),
        point_conditions={**row.point_conditions, "fO2_log": replace(
            oxygen, locator=replace(oxygen.locator, note="column=log(fO2)"),
        )},
    )


def test_migration_retains_borisov_raw_log_and_temperature_prints():
    migrated = _borisov_row4()
    for row in (migrated, observation_from_plain(to_plain(migrated))):
        target, *_ = _target_cells(row)
        assert (target.value, target.unit) == ("0.271144", "log10")
        assert row.value.point == Decimal("1.867")
        temperature = row.point_conditions["temperature_K"]
        printed = _printed_cell(temperature, address_only=True)
        assert (printed.value, printed.unit, printed.address[4]) == ("1502", "C", "spreadsheet row 4")
        assert temperature.state.value == Decimal("1775.15")


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


@pytest.mark.parametrize("control", (
    "ferric", "annotated-ferric", "pressure-operand", "projected-pressure", "pressure-input",
    "oxygen-log", "projected-buffer", "projected-offset", "same-row-assay",
))
def test_internal_analytical_redox_prediction_is_independent_of_measured_target(control):
    location = Locator(page=2, record="Fe3+/Fe2+ cell")
    row = _without_printed_fo2(_borisov_row4())
    row = replace(
        row, locator=location, derivation=None,
        identity=replace(row.identity, total_pressure_Pa=State.of(Decimal("100000"))),
    )

    def predict(ratio, pressure, assay):
        candidate = replace(
            row, value=Value.point_of(ratio), point_conditions={},
        )
        if control in {"pressure-operand", "projected-pressure", "pressure-input"}:
            projected = control == "projected-pressure"
            field = "P_total_bar" if projected else "total_pressure_Pa"
            operand = Located(
                State.of(pressure / BAR_PA_DECIMAL if projected else pressure),
                Locator(page=3, record="pressure operand"),
            )
            candidate = replace(
                candidate,
                identity=replace(candidate.identity, total_pressure_Pa=(
                    State.of(pressure) if projected else None
                )),
                point_conditions={field: operand},
                derivation=Derivation(
                    "redox conversion",
                    (f"point_conditions.{field}",) if control == "pressure-input" else (),
                    () if control == "pressure-input" else ((f"original_{field}", operand),),
                    "1",
                ),
            )
        elif control in {"projected-buffer", "projected-offset"}:
            field, oxygen = (
                ("fO2_control", f"IW+{ratio}") if control == "projected-buffer"
                else ("delta_IW", ratio)
            )
            candidate = replace(
                candidate,
                identity=replace(candidate.identity, fO2_Pa=State.of(ratio)),
                point_conditions={field: Located(
                    State.of(oxygen), location,
                    Derivation("copied from target", (), (
                        ("operand", Located(State.of(ratio), location)),
                    ), "1"),
                )},
            )
        else:
            field = "fO2_log" if control == "oxygen-log" else "Fe3_over_Fe2"
            if control == "same-row-assay":
                candidate = replace(candidate, locator=replace(location, note="column=target"))
                condition_location = replace(location, note="column=assay")
                ratio = assay
            elif control == "annotated-ferric":
                candidate = replace(candidate, locator=replace(location, note="column=target"))
                condition_location = replace(location, note="transcribed; column=target")
            else:
                condition_location = location
            candidate = replace(candidate, point_conditions={
                field: Located(State.of(ratio), condition_location),
            })
        return predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate, isolated=False)

    predictions = [
        predict(Decimal("0.25"), Decimal("25000000"), Decimal("1.0")),
        predict(Decimal("1.0"), Decimal("100000000"), Decimal("0.25")),
    ]
    if control == "same-row-assay":
        assert all(p.value is not None for p in predictions)
        assert [float(p.value) for p in predictions] == pytest.approx([1.0, 0.25])
        assert all(
            any(n.kind is NoticeKind.FO2_FROM_PRINTED_FERRIC_RATIO for n in p.notices)
            for p in predictions
        )
        return
    if control in {"pressure-operand", "projected-pressure", "pressure-input"}:
        assert [p.value for p in predictions] == [
            Decimal("1.8265268104016068"), Decimal("1.7724056001021946"),
        ]
        assert all([n.policy.id if n.policy else n.kind.value for n in p.notices] == ["DP-010"]
                   for p in predictions)
        return
    assert [p.value for p in predictions] == [Decimal("1.8449575445556925")] * 2
    for prediction in predictions:
        assert any(n.policy and n.policy.id == "DP-010" for n in prediction.notices)


def test_internal_analytical_redox_requires_target_independent_material():
    row = _borisov_row4()
    composition = Located(row.identity.composition, inference=Derivation(
        "target-derived composition", (), (
            ("operand", dict(row.derivation.parameters)["original"]),
        ), "1",
    ))
    row = replace(row, point_conditions={**row.point_conditions, "composition": composition})

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, isolated=False)

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {"reason": "missing_input", "input": "composition"}
    assert any(
        n.kind is NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL
        and n.reason == "composition: ambiguous"
        for n in prediction.notices
    )


@pytest.mark.parametrize(("target", "source", "kind"), (
    (Locator(page=2, table="2", record="4", note="column=R"),
     Locator(page=2, table="2", record="4"), NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL),
    (Locator(page=2, table="2", record="4", note="column=R"),
     Locator(page=2, record="4", note="column=R"), NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY),
    (Locator(page=2, table="2", record="4", note="column=R"),
     Locator(pdf_page_index=1, table="2", note="row=4; column=R; transcribed"),
     NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY),
    (Locator(table="Supplementary Table 2", record="spreadsheet row 1"),
     Locator(table="Supplementary Table 2", record="spreadsheet row 1", note="transcribed"),
     NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL),
))
@pytest.mark.parametrize("derived", (False, True), ids=("copied", "derived"))
def test_redox_excludes_copied_oxygen_across_locator_forms(target, source, kind, derived):
    row = _without_printed_fo2(_borisov_row4())
    row = replace(row, locator=target, derivation=None, identity=replace(
        row.identity, total_pressure_Pa=State.of(BAR_PA_DECIMAL),
    ))
    for value in (Decimal(".25"), Decimal("1")):
        inference = (
            Derivation("from scored value", (), (
                ("operand", Located(State.of(value), source)),
            ), "1")
            if derived else None
        )
        candidate = replace(row, value=Value.point_of(value), point_conditions={
            "fO2_log": Located(State.of(Decimal(".123") if derived else str(value)), source, inference),
        })
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate, isolated=False)
        assert prediction.value == Decimal("1.8449575445556925")
        notice = next(n for n in prediction.notices if n.kind is kind)
        assert notice.reason.startswith("fO2_log:")
        assert notice.source == repr(source) and notice.destination == repr(target)


@pytest.mark.parametrize("corrected", (False, True))
def test_redox_temperature_selection_keeps_actual_run_controls(corrected):
    row = _without_printed_fo2(_borisov_row4())
    for celsius in (Decimal("1200"), Decimal("1400")):
        operand = Located(State.of(celsius), Locator(page=3, record="temperature operand"))
        conditions = {"temperature_C": operand}
        if corrected:
            conditions["T_C"] = Located(State.of(Decimal("1300")), Locator(page=4, record="run temperature"))
            conditions["temperature_K"] = Located(
                State.of(score_module.condition_identity_value("temperature_C", celsius)),
                operand.locator,
            )
        candidate = replace(
            row, point_conditions=conditions,
            identity=replace(row.identity, temperature_K=None, total_pressure_Pa=State.of(BAR_PA_DECIMAL)),
            derivation=Derivation("temperature correction", (), (("original_temperature_C", operand),), "1"),
        )
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate, isolated=False)
        expected = {
            Decimal("1200"): ("1473.15", "7.11493348008402"),
            Decimal("1300"): ("1573.15", "4.239439070820317"),
            Decimal("1400"): ("1673.15", "2.7219025137820543"),
        }
        kelvin, ratio = expected[Decimal("1300") if corrected else celsius]
        assert prediction.identity.temperature_K == State.of(Decimal(kelvin))
        assert prediction.value == Decimal(ratio)


@pytest.mark.parametrize(("field", "temperature"), (
    ("temperature_K", "1573.15"), ("temperature_C", "1300"),
    ("T_K_as_published", "1573.15"), ("temp_C", "1300"),
), ids=("kelvin", "celsius", "printed-kelvin", "short-celsius"))
def test_redox_temperature_correction_keeps_the_experiment_run(field, temperature):
    row = _without_printed_fo2(_borisov_row4())
    original = Located(State.of(Decimal("1200")), Locator(page=3, record="original"))
    row = replace(
        row, point_conditions={"temperature_C": original},
        identity=replace(row.identity, temperature_K=State.of(Decimal("1473.15")),
                         total_pressure_Pa=State.of(BAR_PA_DECIMAL)),
        derivation=Derivation("temperature correction", (), (("original_temperature_C", original),), "1"),
    )
    experiment = replace(F.tabulation_experiment(), conditions={
        field: Located(State.of(Decimal(temperature)), Locator(page=4, record="run")),
    })

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, experiment=experiment, isolated=False)

    assert prediction.identity.temperature_K == State.of(Decimal("1573.15"))
    assert prediction.value == Decimal("4.239439070820317")
    assert any(n.kind is NoticeKind.CONDITION_EXCLUDED_ORIGINAL_RUN_MISMATCH
               for n in prediction.notices)


@pytest.mark.parametrize(("source", "suffix", "axis", "expected"), (
    ("bonnand-bruand-matzen-et-al-2020-redox-chromium-isotope-magnesiochromite",
     "bonnand_2020_A1_fe3_over_fe2::T=1573.15:row=a1:h=107fe9f30206", "composition", "0.02365511868327201"),
    ("mysen-virgo-1989-nas-redox", "mysen_virgo_1989_p003_t02_r003_c010",
     "temperature_K", "1.6341128601112949"),
), ids=("bonnand-composition", "mysen-virgo-temperature"))
def test_reading_legacy_redox_projection_preserves_independent_inputs(source, suffix, axis, expected):
    root = Path(__file__).resolve().parents[2]
    stored = load_yaml(root / f"data/literature/extracts-v2/{source}.yaml")
    raw = next(row for row in stored["observations"] if row["observation_id"] == f"{source}::{suffix}")
    row = observation_from_plain(raw)
    work_file = row.experiment_id.split("::", 1)[0].replace("/", "_")
    work = load_yaml(root / f"data/literature/works/{work_file}.yaml")
    run = experiment_from_plain(next(item for item in work["experiments"]
                                     if item["experiment_id"] == row.experiment_id))

    view, _, notices = score_module._redox_input_view(row, row.identity.quantity.value, run)

    assert getattr(view.identity, axis) == getattr(row.identity, axis)
    assert axis in view.point_conditions
    assert not any(axis in notice.dropped for notice in notices)
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, experiment=run, isolated=False)
    assert prediction.value == Decimal(expected)


@pytest.mark.parametrize("run_source", ("identity", "experiment", "experiment-alias"))
def test_redox_pressure_correction_keeps_the_run_identity(run_source):
    row = _without_printed_fo2(_borisov_row4())
    operand = Located(State.of(Decimal("25000000")), Locator(page=3, record="original pressure"))
    run_pressure = Decimal("100000000")
    experiment = None
    if run_source == "experiment":
        experiment = F.tabulation_experiment()
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=Located(State.of(Value.point_of(run_pressure)),
                                      Locator(page=4, record="run pressure")),
        ))
    elif run_source == "experiment-alias":
        experiment = F.tabulation_experiment()
        experiment = replace(
            experiment,
            conditions={"P_total_bar": Located(State.of(Decimal("1000")), Locator(page=4, record="run pressure"))},
            pressure_environment=replace(
                experiment.pressure_environment, total_pressure_Pa=Located(State.unknown("no separate pressure")),
            ),
        )
    row = replace(
        row,
        identity=replace(row.identity, total_pressure_Pa=(
            State.of(run_pressure) if experiment is None else operand.state
        )),
        point_conditions={"total_pressure_Pa": operand},
        derivation=Derivation("pressure correction", (), (("original_total_pressure_Pa", operand),), "1"),
    )

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, experiment=experiment, isolated=False)

    assert prediction.value == Decimal("1.7724056001021946")
    assert [n.policy.id for n in prediction.notices if n.policy] == ["DP-010"]
    assert any(n.kind is NoticeKind.CONDITION_EXCLUDED_ORIGINAL_RUN_MISMATCH
               for n in prediction.notices)


@pytest.mark.parametrize("field", ("P_total_bar", "total_pressure_Pa", "point-P_total_bar"))
def test_redox_pressure_correction_keeps_the_printed_run_band(field):
    row = _without_printed_fo2(_borisov_row4())
    operand = Located(State.of(Decimal("25000000")), Locator(page=3, record="original pressure"))
    row = replace(
        row, identity=replace(row.identity, total_pressure_Pa=operand.state),
        point_conditions={"total_pressure_Pa": operand},
        derivation=Derivation("pressure correction", (), (("original_total_pressure_Pa", operand),), "1"),
    )
    low, high = Decimal("40000000"), Decimal("90000000")
    scale = Decimal(1) if field == "total_pressure_Pa" else BAR_PA_DECIMAL
    band = Value(ValueKind.INTERVAL, interval_low=low / scale, interval_high=high / scale)
    recorded = Located(State.of(band), Locator(page=4, record="run pressure"))
    experiment = F.tabulation_experiment()
    experiment = replace(
        experiment, conditions={field: recorded} if field == "P_total_bar" else experiment.conditions,
        pressure_environment=replace(experiment.pressure_environment, total_pressure_Pa=(
            recorded if field == "total_pressure_Pa" else Located(State.unknown("no separate pressure"))
        )),
    )
    if field == "point-P_total_bar":
        row = replace(row, point_conditions={**row.point_conditions, "P_total_bar": recorded})

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, experiment=experiment, isolated=False)

    assert prediction.value is not None
    resolved = prediction.identity.total_pressure_Pa.value
    assert isinstance(resolved, ApproxPressure)
    assert (resolved.low, resolved.high) == (low, high)
    assert resolved.point == Decimal("60000000")
    assert [n.policy.id for n in prediction.notices if n.policy] == ["DP-114", "DP-010"]
    assert "total_pressure_interval_nominal" in score_module.flagged_strata(prediction.notices)
    assert any(n.kind is NoticeKind.CONDITION_EXCLUDED_ORIGINAL_RUN_MISMATCH for n in prediction.notices)


def test_redox_excludes_its_analytes_and_keeps_independent_emf():
    row = _without_printed_fo2(_borisov_row4())
    locator = Locator(page=3, table="2", record="run 1")
    row = replace(
        row, locator=replace(locator, note="column=Fe3+/Fe2+"),
        identity=replace(row.identity, total_pressure_Pa=State.of(BAR_PA_DECIMAL)),
    )
    for amount in (Decimal(".25"), Decimal("1")):
        conditions = {
            "FeO_wt_pct": Located(State.of(Decimal("10")), replace(locator, note="column=FeO")),
            "Fe2O3_wt_pct": Located(State.of(amount), replace(locator, note="column=Fe2O3")),
        }
        candidate = replace(
            row, point_conditions=conditions, value=Value.point_of(amount),
            derivation=Derivation("oxide ratio", tuple(f"point_conditions.{key}" for key in conditions), (), "1"),
        )
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate, isolated=False)
        assert prediction.value == Decimal("1.8449575445556925")
        assert {n.reason for n in prediction.notices if n.kind is NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY} == {
            "FeO_wt_pct: analyte_operand", "Fe2O3_wt_pct: analyte_operand",
        }
        candidate = replace(candidate, point_conditions={
            **conditions, "fO2_log": Located(State.of(Decimal(".25")), replace(locator, note="column=EMF fO2")),
        })
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate, isolated=False)
        assert prediction.value == Decimal("2.7971189723222234")
        assert all(not n.reason.startswith("fO2_log:") for n in prediction.notices)


@pytest.mark.parametrize("column", ("O2 setpoint", None), ids=("explicit", "omitted"))
def test_imposed_oxygen_target_predicts_but_is_outside_accuracy_headline(column):
    experiment = F.tabulation_experiment()
    location = Locator(page=3, table="2", record="run 1", note=f"column={column}" if column else None)
    control = Located(State.of(Value.point_of(Decimal("10"))), location)
    experiment = replace(experiment, fO2_control=FO2Control(
        State.of(FO2Channel.COMMANDED), oxygen_partial_pressure_Pa=control,
    ))
    identity = replace(
        F.psat_identity("O2"), quantity=State.of(Quantity.FUGACITY),
        reservoir=None, total_pressure_Pa=None,
    )
    row = replace(F.observation("oxygen-setpoint", experiment.experiment_id, identity, Decimal("10")),
                  locator=location)

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row, experiment=experiment, isolated=False)

    assert prediction.value == Decimal("10")
    assert score_module.flagged_strata(prediction.notices) == ("target_is_imposed_control",)
    context = ScoreContext({}, {experiment.experiment_id: experiment}, {row.observation_id: row})
    residual, candidate = score_module.compile_residual(row, Engine.INTERNAL_ANALYTICAL, context=context)
    assert candidate is not None
    assert residual.numeric is not None and residual.numeric.value == 0, residual.refusal
    assert not score_module._headline_payload_admits(residual_to_plain(residual), tier="all_numeric")


@pytest.fixture(scope="module")
def borisov_redox_row():
    return _borisov_row4()


@pytest.mark.parametrize(("route", "column", "equal", "reduced", "matches"), [
    pytest.param(
        route, column, equal, reduced, matches,
        id=f"{route}-{address}-{comparison}-{form}",
    )
    for route in ("condition-value", "derivation-operand", "experiment-control", "imposed-control")
    for address, column, expectations in (
        ("explicit", "result", (True, True)),
        ("omitted", None, (True, False)),
        ("different-column", "independent", (False, False)),
    )
    for (comparison, equal), matches in zip((("equal", True), ("different", False)), expectations)
    for form, reduced in (("raw", False), ("normalised", True))
] + [
    pytest.param("borisov-real-oxygen", None, False, True, False, id="borisov-real-row"),
    pytest.param("condition-value", None, True, True, True, id="review-borisov-raw-copy"),
    pytest.param("derivation-operand", None, True, False, True, id="review-located-operand"),
    pytest.param("experiment-correction", None, False, True, False, id="review-experiment-correction"),
    pytest.param("imposed-control", None, True, False, True, id="review-imposed-row-copy"),
    pytest.param("experiment-log-oxygen", "result", False, False, True, id="review-experiment-log-oxygen"),
    pytest.param("independent-log-oxygen", "result", False, False, False, id="review-independent-log-oxygen"),
    pytest.param("celsius-operand", "result", False, False, True, id="review-explicit-celsius-operand"),
    pytest.param("composition-operand", "result", False, False, True, id="review-explicit-composition-operand"),
    pytest.param("own-condition-address", "result", False, False, True, id="review-explicit-condition-address"),
    pytest.param("normalised-target-address", "result", False, True, True, id="review-explicit-normalised-target-address"),
])
def test_redox_routes_share_printed_cell_identity(borisov_redox_row, route, column, equal, reduced, matches):
    real = borisov_redox_row
    original = dict(real.derivation.parameters)["original"]
    raw = original.state.value
    address = replace(original.locator, note="column=result" if column else None)
    input_address = replace(address, note=f"column={column}" if column else None)
    operands = Located(State.of(raw if equal else Decimal("0.5")), input_address)
    target = replace(_without_printed_fo2(real), locator=address,
                     value=real.value if reduced else Value.point_of(raw),
                     derivation=replace(real.derivation, parameters=(("original", replace(
                         original, locator=address,
                     )),)) if reduced else None)
    experiment = F.tabulation_experiment()
    quantity = Quantity.FE3_FE2_RATIO
    if route == "borisov-real-oxygen":
        target = real
    elif route in {"condition-value", "normalised-target-address"}:
        target = replace(target, point_conditions={**target.point_conditions, "fO2_log": operands})
        if route == "normalised-target-address":
            target = replace(
                target, identity=replace(target.identity, fO2_Pa=State.of(log10_fo2_to_pa(operands.state.value))),
                derivation=replace(target.derivation, parameters=(
                    ("original", replace(original, locator=replace(address, note="column=raw"))),
                )),
            )
    elif route in {"experiment-log-oxygen", "independent-log-oxygen"}:
        independent_oxygen = route == "independent-log-oxygen"
        point_oxygen = {"fO2_log": real.point_conditions["fO2_log"]} if independent_oxygen else {}
        projected_oxygen = (
            real.identity.fO2_Pa if independent_oxygen
            else State.of(log10_fo2_to_pa(operands.state.value))
        )
        target = replace(
            target,
            point_conditions=point_oxygen,
            identity=replace(
                target.identity, total_pressure_Pa=State.of(BAR_PA_DECIMAL), fO2_Pa=projected_oxygen,
            ),
        )
        experiment = replace(experiment, conditions={"fO2_log": operands})
    elif route in {"celsius-operand", "composition-operand", "own-condition-address"}:
        if route == "celsius-operand":
            field, projected = "temperature_K", State.of(Decimal("1473.15"))
            relation, unit = "celsius_to_kelvin", "K"
            operands = replace(operands, state=State.of(Decimal("1200")))
        elif route == "composition-operand":
            field, projected = "composition", target.identity.composition
            relation, unit = "composition closure", "1"
        else:
            field, projected = "total_pressure_Pa", State.of(Decimal("25000000"))
            relation, unit = "other transform", "Pa"
            operands = replace(operands, locator=replace(input_address, record="another run"))
        projection_address = input_address if route == "own-condition-address" else replace(input_address, note=None)
        condition = Located(projected, projection_address, Derivation(relation, (), (("original", operands),), unit))
        target = replace(target, point_conditions={field: condition},
                         identity=replace(target.identity, **{field: projected}))
        experiment = None
    elif route == "derivation-operand":
        condition = Located(State.of(Decimal("25000000")), replace(address, record="another run"),
                            Derivation("operand transform", (), (("input", operands),), "Pa"))
        target = replace(target, point_conditions={"total_pressure_Pa": condition},
                         identity=replace(target.identity, total_pressure_Pa=condition.state))
    elif route in {"experiment-control", "experiment-correction"}:
        pressure = operands
        if route == "experiment-control":
            target = replace(target, identity=replace(target.identity, total_pressure_Pa=pressure.state))
        else:
            original_pressure = Located(State.of(Decimal("25000000")), Locator(page=3, record="old"))
            target = replace(
                target,
                point_conditions={"total_pressure_Pa": original_pressure},
                identity=replace(target.identity, total_pressure_Pa=original_pressure.state),
                derivation=Derivation("pressure correction", (), (
                    ("original_total_pressure_Pa", original_pressure),
                ), "1"),
            )
            pressure = Located(State.of(Decimal("100000000")), Locator(page=4, record="run"))
        pressure = replace(pressure, state=State.of(Value.point_of(pressure.state.value)))
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment, total_pressure_Pa=pressure,
        ))
    else:
        quantity = Quantity.FUGACITY
        target = replace(target, identity=replace(
            F.psat_identity("O2"), quantity=State.of(quantity),
            reservoir=None, total_pressure_Pa=None,
        ), point_conditions={})
        control = replace(operands, state=State.of(Value.point_of(operands.state.value)))
        experiment = replace(experiment, fO2_control=FO2Control(
            State.of(FO2Channel.COMMANDED), oxygen_partial_pressure_Pa=control,
        ))
    view, run, notices = score_module._redox_input_view(target, quantity, experiment)
    if route == "imposed-control":
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, target, experiment=experiment, isolated=False)
        assert ("target_is_imposed_control" in score_module.flagged_strata(prediction.notices)) is matches
        if matches:
            assert prediction.value == operands.state.value
            assert prediction.refusal_reason is None
    elif route == "borisov-real-oxygen":
        assert view.point_conditions["fO2_log"].state.value == Decimal("-0.68")
        assert not notices
    elif route == "experiment-correction":
        pressure_bar, _, _, invalid = score_module._resolve_score_total_pressure(
            view.identity, quantity, observation=view, experiment=run,
        )
        assert (pressure_bar, invalid) == (1000, None)
        assert [n.kind for n in notices] == [NoticeKind.CONDITION_EXCLUDED_ORIGINAL_RUN_MISMATCH]
    elif route == "independent-log-oxygen":
        assert not run.conditions["fO2_log"].state.is_value
        assert view.identity.fO2_Pa == real.identity.fO2_Pa
        assert view.point_conditions["fO2_log"] == real.point_conditions["fO2_log"]
        assert [n.kind for n in notices] == [NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY]
    else:
        if route == "experiment-control":
            retained = run.pressure_environment.total_pressure_Pa.state.is_value
            assert (view.identity.total_pressure_Pa is None) is matches
        elif route == "experiment-log-oxygen":
            retained = run.conditions["fO2_log"].state.is_value
            assert view.identity.fO2_Pa is None
            prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, target, experiment=experiment, isolated=False)
            assert prediction.value == Decimal("1.8449575445556925")
            assert any(n.policy and n.policy.id == "DP-010" for n in prediction.notices)
        elif route in {"celsius-operand", "composition-operand", "own-condition-address"}:
            retained = field in view.point_conditions
            assert getattr(view.identity, field) is None
        else:
            field = "fO2_log" if route in {"condition-value", "normalised-target-address"} else "total_pressure_Pa"
            retained = field in view.point_conditions
            if route == "normalised-target-address":
                assert view.identity.fO2_Pa is None
        assert retained is not matches
        expected = (NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL if column is None
                    else NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY)
        assert [n.kind for n in notices] == ([expected] if matches else [])


def test_melts_fe_redox_uses_dp010_without_independent_oxygen(monkeypatch):
    row4 = _without_printed_fo2(_borisov_row4())
    calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            liquid_composition_wt_pct={"FeO": 9.0, "Fe2O3": 1.0},
        ),
    )
    alphamelts = predict_with_engine(Engine.ALPHAMELTS, row4, isolated=False)
    assert alphamelts.value is not None
    assert alphamelts.refusal_reason is None
    assert alphamelts.identity is not None
    assert alphamelts.identity.fO2_Pa is not None
    assert alphamelts.identity.fO2_Pa.is_value
    assert any(notice.policy and notice.policy.id == "DP-010" for notice in alphamelts.notices)
    assert not any(
        notice.kind is NoticeKind.FO2_FROM_PRINTED_FERRIC_RATIO
        for notice in alphamelts.notices
    )
    assert len(calls) == 1

    thermoengine = predict_with_engine(Engine.THERMOENGINE, row4, isolated=False)
    assert thermoengine.value == fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal("1.0"), Decimal("9.0")
    )
    assert thermoengine.identity is not None
    assert thermoengine.identity.fO2_Pa is not None
    assert thermoengine.identity.fO2_Pa.is_value
    assert len(calls) == 2
    assert calls[1]["po2"].mode == "commanded"
    assert calls[1]["po2"].po2_bar > 0.0
    assert calls[1]["physical_pressure_bar"] == pytest.approx(1.01325)


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
    assert any(
        notice.kind is NoticeKind.ASSUMED_PRESSURE
        and notice.policy is not None
        and notice.policy.id == "DP-001"
        for notice in prediction.notices
    )


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
