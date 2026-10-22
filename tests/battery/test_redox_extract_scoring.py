import json
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import simulator.battery.score as score_module
from simulator.battery.enums import (
    Authority,
    Engine,
    NoticeKind,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.migrate import load_yaml, write_outputs
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    predict_with_engine,
    score_store,
    SCORE_ENGINE_SET,
)
from simulator.battery.records import State, Value
from simulator.battery.score import headline_payload_records, residual_to_plain
from tests.battery.factories import migrate_one_extract


def _assert_kress91_citation_is_unknown(residuals, observations) -> None:
    """Every Borisov redox residual is UNKNOWN, and the dex pins stay put.

    Kress & Carmichael 1991 is not a registered work. regolith-main is
    registering it under kc91-1022, and these source-relation pins are
    re-pinned once that work lands. The numeric predictions are the
    previous attribution split: calibration overlap versus the other
    printed sources. That split is no longer the source relation.
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
    for residual in residuals:
        observation = observations[residual.reference]
        overlap = score_module._kc91_calibration_source_overlap(
            observation.evidence.attribution
        )
        assert residual.numeric is not None
        value = residual.numeric.value
        (overlap_all if overlap else held_all).append(value)
        if score_module._headline_payload_admits(
            residual_to_plain(residual), tier="measured"
        ):
            (overlap_admitted if overlap else held_admitted).append(value)
    assert (len(overlap_all), len(held_all)) == (223, 212)
    assert (len(overlap_admitted), len(held_admitted)) == (213, 212)
    assert Decimal(str(score_module._rms(overlap_all))).quantize(
        Decimal("0.00001")
    ) == Decimal("0.11250")
    assert abs(
        score_module._rms(overlap_admitted)
        - Decimal("0.1126289501574099044023575236")
    ) < Decimal("0.00001")
    assert abs(
        score_module._median(overlap_admitted) - Decimal("-0.04086499066062312")
    ) < Decimal("0.00001")
    assert abs(
        score_module._rms(held_admitted) - Decimal("0.10468")
    ) < Decimal("0.00001")
    assert abs(
        score_module._median(held_admitted) - Decimal("-0.01574")
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
    assert (row["n_candidates"], row["n"]) == (425, 425)
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
    for row in source_rows:
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

    hard_issue_details = [issue.detail for issue in result.validation.hard_issues]
    assert hard_issue_details.count("derived observation requires derived_from") == 435
    assert "derived observation requires derivation" not in hard_issue_details
    assert set(hard_issue_details) == {"derived observation requires derived_from"}

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
    row155 = rows_by_id[f"{extract.stem}::borisov_2018_table2_row_155"]

    # Hand evaluation of Kress & Carmichael 1991 at each printed T and log fO2,
    # with 1 atm from the cited compilation: row 4 gives Fe2O3/FeO=0.9191803065,
    # hence Fe3+/Fe2+=2*0.9191803065=1.838361; row 155 gives 1.6385001231,
    # hence 3.277000. The factor two converts oxide formula units to Fe cations.
    prediction4 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row4)
    prediction155 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, row155)
    assert prediction4.value is not None
    assert abs(prediction4.value - Decimal("1.838360613")) < Decimal("1e-9")
    assert prediction155.value is not None
    assert abs(prediction155.value - Decimal("3.277000246")) < Decimal("1e-9")
    assert prediction4.refusal_reason is None
    assert prediction155.refusal_reason is None
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

    unsupported = predict_with_engine(Engine.ALPHAMELTS, row4)
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
    assert (measured_unknown["candidates"], measured_unknown["numeric"]) == (435, 435)
    assert (measured_unknown["certified_numeric"], measured_unknown["flagged_numeric"]) == (425, 10)
    assert len(residuals) == 435
    assert all(residual.numeric is not None for residual in residuals)
    assert all(residual.status is not ResidualStatus.REFUSED for residual in residuals)
    assert all(residual.numeric.operation.value == "dex" for residual in residuals)

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
    assert fit_row[3] == fit_row[5] == "425"
    assert fit_row[6] == "0"

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
    assert (measured_unknown["candidates"], measured_unknown["numeric"]) == (435, 435)
    assert (measured_unknown["certified_numeric"], measured_unknown["flagged_numeric"]) == (425, 10)
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
        if line.startswith("| redox | dex / dex | internal-analytical | unknown |")
    )
    assert measured_fit[3] == "unknown"
    assert measured_fit[4] == measured_fit[5] == "435"
    assert measured_fit[6] == "425"
    assert measured_fit[7] == "10"


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


def test_existing_sixteen_redox_rows_are_refused_for_unprinted_composition():
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
        assert residual.numeric is None
        assert residual.refusal is not None
        if residual.key.endswith("internal-analytical"):
            assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
            assert residual.refusal.detail == {
                "reason": "missing_input",
                "input": "composition",
            }
        else:
            assert (
                residual.refusal.reason
                is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
            )
