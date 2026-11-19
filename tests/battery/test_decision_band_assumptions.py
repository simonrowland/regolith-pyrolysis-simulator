"""d-443: literature method bands are additive, never headline evidence."""

import json
import warnings
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery import method_class_bands as owner
from simulator.battery import score
from simulator.battery.enums import (
    MEASURED_EVIDENCE,
    AmountBasis,
    EvidenceClass,
    MethodToken,
    Phase,
    Quantity,
    UncertaintyKind,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.migrate import Migrator, to_plain
from simulator.battery.records import Composition, State, Uncertainty
from simulator.battery.score import ScoreContext
from simulator.battery.score_metrics import metric_operation_for_identity
from tests.battery import factories as F
from tests.battery.test_score import (
    _compile,
    _context,
    _predict,
    _with_activity_reference_polymorph,
)


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


def _crystal_cao(uncertainty):
    """CaO(cr, lime) activity with the direct-comparison applicability facts."""

    observation, experiment = _fixture("kems_direct_comparison_activity")
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.8")), ("Al2O3", Decimal("0.2"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="CaO",
            T_K=Decimal("2000"),
            endmember_phase=Phase.CR,
            component_basis="CaO",
            composition=composition,
        ),
        "lime",
    )
    return replace(observation, identity=identity, uncertainty=uncertainty), experiment


def _compile_with_prediction(observation, experiment, prediction):
    return score.compile_residual(
        observation,
        score.Engine.INTERNAL_ANALYTICAL,
        context=_context(F.work(), experiment, observation),
        prediction=prediction,
    )


def _fused_prediction(observation):
    converted = score.fusion_comparison_reference(
        observation, engine=score.Engine.INTERNAL_ANALYTICAL
    )
    return converted, _predict(converted.value.point, converted.identity)


def test_crystal_activity_fusion_refusal_is_not_a_printed_uncertainty(monkeypatch):
    """Kind NONE has no measurement refusal. Fusion's JANAF-sigma refusal must not block d-443.

    Restoring the gate's ``printed.refusal is None`` conjunct makes this red:
    the headline refusal is ``conversion_uncertainty_unprinted`` and the
    assumed stratum would stay empty.
    """

    observation, experiment = _crystal_cao(Uncertainty(kind=UncertaintyKind.NONE))
    converted, prediction = _fused_prediction(observation)
    assert converted.value.point != observation.value.point
    enabled, candidate = _compile_with_prediction(observation, experiment, prediction)
    assert enabled.assumed_band is True
    assert enabled.assumed_band_details["class_id"] == "kems_direct_comparison_activity"
    assert enabled.assumed_band_details["band"].band_basis == "method_class_assumed"
    assert enabled.numeric is not None and enabled.numeric.decision_band is None
    assert enabled.status is score.ResidualStatus.NO_BAND
    assert enabled.refusal is not None
    assert enabled.refusal.detail["reason"] == "conversion_uncertainty_unprinted"
    monkeypatch.setattr(owner, "method_class_bands", lambda: {})
    disabled, _candidate = _compile_with_prediction(observation, experiment, prediction)
    assert disabled.assumed_band is None
    assert disabled.assumed_band_details is not None
    assert "band" not in disabled.assumed_band_details
    assert enabled.numeric == disabled.numeric
    assert enabled.status == disabled.status
    assert enabled.score_eligible == disabled.score_eligible
    assert enabled.refusal == disabled.refusal
    context = _context(F.work(), experiment, observation, candidate)
    engines = (score.Engine.INTERNAL_ANALYTICAL,)
    assert _headline_bytes(
        score.headline_records((enabled,), context=context, engines=engines)
    ) == _headline_bytes(
        score.headline_records((disabled,), context=context, engines=engines)
    )


def test_printed_activity_width_still_blocks_the_assumed_stratum():
    observation, experiment = _crystal_cao(
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim="±0.10 standard deviation of log10(a) as printed",
            locator=F.loc(page=4, table="2"),
        )
    )
    _converted, prediction = _fused_prediction(observation)
    residual, _candidate = _compile_with_prediction(observation, experiment, prediction)
    assert residual.assumed_band is None
    assert residual.assumed_band_details is None
    assert residual.numeric is not None and residual.numeric.decision_band is None
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "conversion_uncertainty_unprinted"


_SURVEY_SOURCES = (
    "kems-055-kambayashi-1983",
    "kems-012-sossi-2019",
    "kems-053-stolyarova-1991",
    "kambayashi-kato-1984-mgo-sio2-1973k",
    "kems-ms2000-044",
)
# AlphaMELTS is not a single-liquid engine, so a two-phase bulk activity
# reaches the band gate. Internal-analytical refuses those rows first.
_SURVEY_ENGINE = score.Engine.ALPHAMELTS


def _survey_context():
    root = Path(__file__).resolve().parents[2]
    migrator = Migrator(root=root)
    extracts = root / "data" / "literature" / "extracts"
    for source_id in _SURVEY_SOURCES:
        migrator._migrate_extract(extracts / f"{source_id}.yaml")
    migrator.finalize()
    return ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations=migrator.result.observations,
        benches=migrator.result.benches,
        root=root,
    )


def _source_method_uncertainty(observation, experiment, context):
    if experiment is None:
        return None
    work = context.works.get(experiment.work_id or "")
    table = None if work is None else work.source_method_uncertainties
    if table is None or observation.source_id is None:
        return None
    found = table.get(observation.source_id)
    quantity = quantity_token(observation.identity)
    if found is not None and (quantity is None or found.quantity is not quantity):
        return None
    return found


def _derived_band_for(observation, bands, empirical_ids):
    if observation.observation_id not in empirical_ids:
        return None
    key = score._source_alignment_group_key(observation, tier="empirical")
    if key is None:
        return None
    return bands.get(score._group_token(key))


def _legacy_blocker(observation, experiment, context, residual, derived_band):
    """First d-443 conjunct that failed before fusion refusals were set aside."""

    if score._implied_alpha_activity_observation(observation) is not None:
        return "implied_alpha"
    evidence = observation.evidence.class_
    if not evidence.is_value or evidence.value not in MEASURED_EVIDENCE:
        label = evidence.value.value if evidence.is_value else "unknown"
        return f"evidence_not_measured:{label}"
    if residual.numeric is None or residual.status is not score.ResidualStatus.NO_BAND:
        reason = None if residual.refusal is None else residual.refusal.reason.value
        detail = ""
        if residual.refusal is not None:
            detail = str(
                residual.refusal.detail.get("reason")
                or residual.refusal.detail.get("primary_check")
                or ""
            )
        status = None if residual.status is None else residual.status.value
        return f"not_numeric_no_band:{status}:{reason}:{detail}"
    if residual.numeric.decision_band is not None:
        return "numeric_decision_band"
    if derived_band is not None:
        return "derived_band"
    if residual.refusal is not None:
        return "printed_refusal:" + str(residual.refusal.detail.get("reason"))
    if _source_method_uncertainty(observation, experiment, context) is not None:
        return "source_method_uncertainty"
    if not (
        observation.uncertainty.kind is UncertaintyKind.NONE
        or score._verbatim_denies_printed_uncertainty(observation.uncertainty)
    ):
        return f"uncertainty_kind:{observation.uncertainty.kind.value}"
    if residual.assumed_band_details is None:
        return "printed_band_or_other"
    return "legacy_would_enter"


def _post_fix_blocker(observation, experiment, context, residual, derived_band):
    """Why the gate still does not run after a fusion refusal is set aside."""

    if residual.assumed_band_details is not None:
        return _after_fix(residual)
    if derived_band is not None:
        return "derived_band"
    if _source_method_uncertainty(observation, experiment, context) is not None:
        return "source_method_uncertainty"
    if not (
        observation.uncertainty.kind is UncertaintyKind.NONE
        or score._verbatim_denies_printed_uncertainty(observation.uncertainty)
    ):
        return f"uncertainty_kind:{observation.uncertainty.kind.value}"
    return "measurement_or_printed_band"


def _after_fix(residual):
    details = residual.assumed_band_details
    if details is None:
        return "still_none"
    if "band" in details:
        return "entered_band:" + str(details.get("class_id"))
    refusal = details.get("refusal") or {}
    return "entered_refusal:" + str(refusal.get("reason"))


def test_measured_no_band_rows_name_the_blocking_precondition():
    """Real KEMS rows. Kambayashi 1983 activities were blocked only by the fusion refusal.

    The stub prediction is not an engine solve. ``derived_band`` is this
    five-source catalog, not the full corpus. A cross-source scatter band
    would still suppress the stratum.
    """

    context = _survey_context()
    entries, _missing, _history = score._read_decision_band_ratchet(context.root)
    bands, _rows, _reasons = score._source_band_catalog(context, entries)
    empirical_ids = {
        row.observation_id for row in score.comparison_candidates(context)
    }
    records = []
    for observation in context.observations.values():
        if observation.source_id not in _SURVEY_SOURCES:
            continue
        experiment = context.experiments.get(observation.experiment_id)
        derived_band = _derived_band_for(observation, bands, empirical_ids)
        identity = observation.identity
        token = quantity_token(identity) if isinstance(identity, Identity) else None
        quantity = None if token is None else token.value
        evidence = observation.evidence.class_
        try:
            converted = score.fusion_comparison_reference(
                observation, engine=_SURVEY_ENGINE
            )
            magnitude = score.point_magnitude(converted.value)
            if magnitude is None or not isinstance(converted.identity, Identity):
                raise RuntimeError("no comparable point")
            residual, _candidate = score.compile_residual(
                observation,
                _SURVEY_ENGINE,
                context=context,
                prediction=replace(
                    _predict(magnitude, converted.identity),
                    engine=_SURVEY_ENGINE,
                ),
                derived_band=derived_band,
            )
        except Exception as exc:
            records.append({
                "source": observation.source_id,
                "id": observation.observation_id,
                "quantity": quantity,
                "evidence": evidence.value.value if evidence.is_value else "unknown",
                "legacy": f"compile_error:{type(exc).__name__}:{exc}",
                "after": "compile_error",
            })
            continue
        records.append({
            "source": observation.source_id,
            "id": observation.observation_id,
            "quantity": quantity,
            "evidence": evidence.value.value if evidence.is_value else "unknown",
            "uncertainty": observation.uncertainty.kind.value,
            "legacy": _legacy_blocker(
                observation, experiment, context, residual, derived_band
            ),
            "after": _after_fix(residual),
            "headline": (
                None
                if residual.refusal is None
                else residual.refusal.detail.get("reason")
            ),
            "status": None if residual.status is None else residual.status.value,
            "assumed_band": residual.assumed_band,
            "post_fix": _post_fix_blocker(
                observation, experiment, context, residual, derived_band
            ),
        })
    kambayashi = [
        row for row in records
        if row["source"] == "kems-055-kambayashi-1983"
        and row["quantity"] == "activity"
        and row["evidence"] == "measured_reduced"
    ]
    census = {}
    for source_id in _SURVEY_SOURCES:
        source_rows = [row for row in records if row["source"] == source_id]
        # Numeric NO_BAND, including rows whose evidence is not measured.
        numeric = [row for row in source_rows if row.get("status") == "no_band"]
        census[source_id] = {
            "rows": len(source_rows),
            "numeric_no_band": len(numeric),
            "legacy_numeric_no_band": dict(Counter(row["legacy"] for row in numeric)),
            "after_numeric_no_band": dict(Counter(row["after"] for row in numeric)),
            "post_fix_numeric_no_band": dict(
                Counter(row.get("post_fix", "compile_error") for row in numeric)
            ),
            "legacy_all": dict(Counter(row["legacy"] for row in source_rows)),
        }
    warnings.warn("D443-CENSUS " + json.dumps(census, sort_keys=True))
    assert len(kambayashi) == 20, json.dumps(kambayashi, default=str)
    failures = [
        row for row in kambayashi
        if row["legacy"] != "printed_refusal:conversion_uncertainty_unprinted"
        or row["after"] != "entered_refusal:method_class_applicability_unknown"
        or row["headline"] != "conversion_uncertainty_unprinted"
        or row["assumed_band"] is not None
    ]
    assert failures == [], json.dumps(failures, default=str)
