"""d-443: literature method bands are additive, never headline evidence."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery import method_class_bands as owner
from simulator.battery import score
from simulator.battery.enums import EvidenceClass, MethodToken, Quantity, UncertaintyKind
from simulator.battery.migrate import to_plain
from simulator.battery.records import State, Uncertainty
from simulator.battery.score_metrics import metric_operation_for_identity
from tests.battery import factories as F
from tests.battery.test_score import _compile, _context, _predict


def _fixture(class_id="kems_absolute_pressure_weight_loss_calibrated"):
    entry = owner.method_class_bands()[class_id]
    identity = F.psat_identity("Na")
    quantity = Quantity(entry["quantities"][0])
    if quantity is Quantity.P_PARTIAL:
        quantity = Quantity.P_SAT
    identity = replace(identity, quantity=State.of(quantity))
    if quantity is Quantity.ACTIVITY:
        identity = F.activity_identity()
    elif quantity is Quantity.H_MINUS_H298:
        identity = replace(
            F.oxide_identity("Na2O", score.Phase.L),
            quantity=State.of(quantity), temperature_K=State.of(Decimal("800")),
        )
    elif quantity is Quantity.FE3_FE2_RATIO:
        from tests.battery.test_score import _fe_ratio_identity

        identity = _fe_ratio_identity()
    experiment = replace(
        F.kems_experiment(), method=State.of(MethodToken(entry["method_token"]))
    )
    conditions = {"technique": F.located(entry["technique"])}
    if quantity is Quantity.FE3_FE2_RATIO:
        conditions[next(iter(score.FERRIC_FRACTION_FIELDS))] = F.located(Decimal("0.1"))
    for key, value in entry["applicability"].items():
        if key == "dominant_vapour_mole_fraction_min":
            key, value = "dominant_vapour_mole_fraction", Decimal(value)
        elif key.startswith("temperature_K_"):
            continue
        conditions[key] = F.located(value)
    observation = replace(
        F.observation(
            "assumed", experiment.experiment_id, identity, Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT, source_id="work-1",
        ),
        point_conditions=conditions,
    )
    return observation, experiment


@pytest.mark.parametrize("class_id,reference,width", [
    ("kems_absolute_pressure_weight_loss_calibrated", "1", "0.096910013"),
    ("kems_direct_comparison_activity", "1", "0.045757491"),
    ("knudsen_weight_loss_collection_torsion_pressure", "1", "0.096910013"),
    ("transpiration_mass_spectrometry_pressure", "1", "0.096910013"),
    ("mossbauer_fe_glass_ferric_fraction", "0.1111111111111111111111111111", "0.048254942"),
    ("xanes_fe_glass_ferric_fraction", "0.1111111111111111111111111111", "0.048254942"),
    ("drop_calorimetry_heat_content_copper_block", "100", "0.300000000"),
])
def test_each_cited_class_uses_existing_metric_owner(class_id, reference, width):
    observation, experiment = _fixture(class_id)
    assert "operation" not in owner.method_class_bands()[class_id]
    assert metric_operation_for_identity(observation.identity) is not None
    resolved = score._assumed_method_class_band(observation, experiment, Decimal(reference))
    assert resolved["class_id"] == class_id
    assert resolved["band"].value.quantize(Decimal("1e-9")) == Decimal(width)
    assert resolved["band"].band_basis == "method_class_assumed"
    assert resolved["precision"]["confidence"] == "unstated"
    assert resolved["citation"]["printed_page"]


def test_weight_loss_band_requires_controlled_parasitic_phenomena():
    observation, experiment = _fixture("knudsen_weight_loss_collection_torsion_pressure")
    conditions = dict(observation.point_conditions)
    conditions.pop("parasitic_phenomena")
    resolved = score._assumed_method_class_band(
        replace(observation, point_conditions=conditions), experiment, Decimal("1")
    )
    assert "band" not in resolved
    assert resolved["refusal"]["reason"] == "method_class_applicability_unknown"


def test_multiple_matching_classes_have_typed_refusal_and_census(monkeypatch):
    observation, experiment = _fixture()
    table = dict(owner.method_class_bands())
    table["duplicate"] = dict(table["kems_absolute_pressure_weight_loss_calibrated"])
    monkeypatch.setattr(owner, "method_class_bands", lambda: table)
    resolved = score._assumed_method_class_band(observation, experiment, Decimal("1"))
    assert "band" not in resolved
    assert resolved["refusal"]["reason"] == "method_class_ambiguous"
    row = {
        "assumed_band_details": resolved,
        "reference": observation.observation_id,
    }
    summary = score._assumed_band_summary([row])
    assert summary["refusal_counts"] == {"method_class_ambiguous": 1}


@pytest.mark.parametrize("change", ["unknown_method", "missing", "failed", "unlocated", "ambiguous", "quantity"])
def test_unproven_class_never_supplies_a_band(change, monkeypatch):
    observation, experiment = _fixture()
    conditions = dict(observation.point_conditions)
    if change == "unknown_method":
        experiment = replace(experiment, method=State.unknown("not printed"))
    elif change == "missing":
        conditions.pop("cell")
    elif change == "failed":
        conditions["dominant_vapour_mole_fraction"] = F.located(Decimal("0.94"))
    elif change == "unlocated":
        conditions["cell"] = replace(conditions["cell"], locator=None)
    elif change == "ambiguous":
        table = dict(owner.method_class_bands())
        table["duplicate"] = dict(next(iter(table.values())))
        monkeypatch.setattr(owner, "method_class_bands", lambda: table)
    else:
        observation = replace(observation, identity=replace(observation.identity, quantity=State.of(Quantity.CP)))
    observation = replace(observation, point_conditions=conditions)
    resolved = score._assumed_method_class_band(observation, experiment, Decimal("1"))
    assert "band" not in resolved
    assert resolved["refusal"]["reason"].startswith("method_class_")


@pytest.mark.parametrize("method", [
    MethodToken.LANGMUIR_FREE_EVAPORATION, MethodToken.EMF_CELL,
    MethodToken.WET_CHEMICAL_ANALYSIS, MethodToken.SOLUTION_CALORIMETRY,
    MethodToken.STATIC_EQUILIBRIUM,
])
def test_unsupported_methods_have_no_default(method):
    observation, experiment = _fixture()
    resolved = score._assumed_method_class_band(
        observation, replace(experiment, method=State.of(method)), Decimal("1")
    )
    assert "band" not in resolved


@pytest.mark.parametrize("width", ["0.01 Pa", "0.5 Pa", "2 furlong"])
def test_printed_width_or_printed_refusal_always_wins(width):
    observation, experiment = _fixture()
    printed = replace(observation, uncertainty=Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=width))
    residual, _ = _compile(printed, experiment, _predict(Decimal("2"), printed.identity))
    assert residual.assumed_band is None
    assert residual.assumed_band_details is None


def _headline_bytes(rows):
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("include_printed", [False, True])
def test_assumed_stratum_never_changes_headlines_or_numeric(monkeypatch, tmp_path, copies, include_printed):
    observation, experiment = _fixture()
    prediction = _predict(Decimal("2"), observation.identity)
    enabled, candidate = _compile(observation, experiment, prediction)
    assert enabled.assumed_band is True
    assert enabled.numeric.decision_band is None
    assert enabled.numeric.metric_uncertainty is None
    assert enabled.assumed_band_details["eligible"] == enabled.score_eligible
    monkeypatch.setattr(owner, "method_class_bands", lambda: {})
    disabled, _ = _compile(observation, experiment, prediction)
    assert enabled.numeric == disabled.numeric
    assert enabled.status == disabled.status
    assert enabled.score_eligible == disabled.score_eligible
    printed = replace(observation, observation_id="printed", uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED, verbatim="0.1 Pa",
    ))
    printed_row, _ = _compile(printed, experiment, _predict(Decimal("1"), printed.identity))
    context = _context(F.work(), experiment, observation, printed, candidate)
    engines = (score.Engine.INTERNAL_ANALYTICAL,)
    prefix = [printed_row] if include_printed else []
    on, off = prefix + [enabled] * copies, prefix + [disabled] * copies
    assert _headline_bytes(score.headline_records(on, context=context, engines=engines)) == _headline_bytes(score.headline_records(off, context=context, engines=engines))
    payload_on, payload_off = to_plain(on), to_plain(off)
    assert _headline_bytes(score.headline_payload_records(payload_on, engines=engines, observations=context.observations)) == _headline_bytes(score.headline_payload_records(payload_off, engines=engines, observations=context.observations))
    accumulators = [score._ScorePayloadAccumulator.from_rows(rows, context=context, engines=engines) for rows in (payload_on, payload_off)]
    assert _headline_bytes(accumulators[0].headline_records()) == _headline_bytes(accumulators[1].headline_records())
    summary = accumulators[0].summary_payload(None)
    assert _headline_bytes(summary["records"]) == _headline_bytes(accumulators[1].summary_payload(None)["records"])
    assumed = summary["assumed_band"]["records"]
    assert len(assumed) == 1
    assert (assumed[0]["n"], assumed[0]["n_inside_band"], assumed[0]["n_eligible"]) == (copies, 0, copies * int(enabled.score_eligible))
    assert assumed[0]["n_observations"] == 1
    for suffix, rows in (("typed", on), ("payload", payload_on)):
        path = tmp_path / f"{suffix}.json"
        if suffix == "typed":
            score.write_headline_summary_json(rows, path, context=context, engines=engines)
        else:
            score.write_headline_summary_from_payloads_json(rows, path, engines=engines, observations=context.observations)
        assert json.loads(path.read_text())["assumed_band"] == summary["assumed_band"]
        off_path = tmp_path / f"{suffix}-off.json"
        if suffix == "typed":
            score.write_headline_summary_json(off, off_path, context=context, engines=engines)
        else:
            score.write_headline_summary_from_payloads_json(payload_off, off_path, engines=engines, observations=context.observations)
        assert _headline_bytes(json.loads(path.read_text())["records"]) == _headline_bytes(json.loads(off_path.read_text())["records"])
    report = score.render_score_report_from_payloads(payload_on, engines=engines, hostname="test", observations=context.observations)
    assert "assumed band — literature method precision; confidence not stated" in report
    assert "kems_absolute_pressure_weight_loss_calibrated" in report
    off_report = score.render_score_report_from_payloads(payload_off, engines=engines, hostname="test", observations=context.observations)
    assert report.split("## Assumed band")[0] == off_report.split("## Assumed band")[0]
    typed_reports = [score.render_score_report(rows, context=context, engines=engines) for rows in (on, off)]
    assert typed_reports[0].split("## Assumed band")[0] == typed_reports[1].split("## Assumed band")[0]


def test_assumed_width_does_not_depend_on_prediction():
    observation, experiment = _fixture()
    near, _ = _compile(observation, experiment, _predict(Decimal("1"), observation.identity))
    far, _ = _compile(observation, experiment, _predict(Decimal("100"), observation.identity))
    assert near.assumed_band_details["band"] == far.assumed_band_details["band"]
    assert near.assumed_band_details["inside_band"] is True
    assert far.assumed_band_details["inside_band"] is False


@pytest.mark.parametrize("basis", ["relative_percent", "unmapped"])
@pytest.mark.parametrize("denial", [False, True])
def test_source_method_width_or_refusal_always_prevents_assumption(basis, denial):
    from simulator.battery.records import SourceMethodUncertainty

    observation, experiment = _fixture()
    if denial:
        observation = replace(observation, uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED, verbatim="Row-specific uncertainties are not printed.",
        ))
    work = replace(F.work(), source_method_uncertainties={"work-1": SourceMethodUncertainty(
        value=Decimal("5"), meaning="range_half_width", quantity=Quantity.P_SAT,
        basis=basis, quantity_basis="vapour pressure", locator=F.loc(page=2),
    )})
    context = _context(work, experiment, observation)
    residual, _ = score.compile_residual(
        observation, score.Engine.INTERNAL_ANALYTICAL, context=context,
        prediction=_predict(Decimal("1"), observation.identity),
    )
    assert residual.assumed_band is None
    assert residual.assumed_band_details is None
    if not denial and basis == "relative_percent":
        assert residual.numeric.decision_band.band_basis == "source_method"


@pytest.mark.parametrize("fraction", ["0", "0.01", "0.1", "0.99", "1"])
def test_fe_fraction_endpoint_refuses(fraction):
    from simulator.battery.identity import ValenceBasis
    from simulator.battery.enums import ValenceDenominatorKind, ValenceValueForm
    from simulator.battery.records import Species

    observation, experiment = _fixture("xanes_fe_glass_ferric_fraction")
    identity = replace(
        observation.identity, quantity=State.of(Quantity.VALENCE_RATIO),
        species=Species("Fe", score.Phase.L), valence_basis=State.of(ValenceBasis(
            numerator_oxidation_states=(3,), denominator_oxidation_state=None,
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            value_form=ValenceValueForm.FRACTION,
        )),
    )
    resolved = score._assumed_method_class_band(replace(observation, identity=identity), experiment, Decimal(fraction))
    if fraction == "0.1":
        assert resolved["band"].value.quantize(Decimal("1e-9")) == Decimal("0.045757491")
        cr = replace(identity, species=Species("Cr", score.Phase.L))
        assert "band" not in score._assumed_method_class_band(replace(observation, identity=cr), experiment, Decimal(fraction))
    else:
        assert "band" not in resolved
        assert "refusal" in resolved


def test_failed_applicability_is_counted_and_does_not_repair_other_gates():
    observation, experiment = _fixture()
    missing = replace(observation, point_conditions={})
    residual, _ = _compile(missing, experiment, _predict(Decimal("1"), missing.identity))
    assert residual.assumed_band is None
    summary = score._assumed_band_summary([to_plain(residual)])
    assert summary["refusal_counts"] == {"method_class_applicability_unknown": 1}
    assert summary["records"] == []
    flag = score.Notice(kind=score.NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
                        affected_quantities=(Quantity.P_SAT,), reason="unverified input", origin="test")
    flagged, _ = _compile(observation, experiment, _predict(Decimal("1"), observation.identity, notices=(flag,)))
    assert flagged.assumed_band is True
    assert flagged.assumed_band_details["eligible"] is False
    assert flagged.numeric.decision_band is None
    assert flagged.score_eligible is False


def test_belton_fruehan_activity_and_out_of_range_drop_have_no_default():
    observation, experiment = _fixture("kems_direct_comparison_activity")
    conditions = {**observation.point_conditions, "activity_method": F.located("belton_fruehan_gibbs_duhem")}
    assert "band" not in score._assumed_method_class_band(replace(observation, point_conditions=conditions), experiment, Decimal("1"))
    observation, experiment = _fixture("drop_calorimetry_heat_content_copper_block")
    for temperature in ("597", "1011"):
        outside = replace(observation, identity=replace(observation.identity, temperature_K=State.of(Decimal(temperature))))
        assert "band" not in score._assumed_method_class_band(outside, experiment, Decimal("100"))
