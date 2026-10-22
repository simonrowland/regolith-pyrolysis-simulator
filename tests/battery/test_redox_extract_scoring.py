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


def test_kress91_calibration_scope_migrates_as_model_work():
    root = Path(__file__).resolve().parents[2]
    extract = (
        root
        / "data/literature/extracts/kress-carmichael-1991-iron-redox-calibration.yaml"
    )
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()

    result = migrator.result
    work_id = "10.1007/bf00307328"
    assert not result.validation.hard_issues
    assert result.observations == {}
    assert result.works[work_id].doi == work_id
    assert (
        "kress-carmichael-1991-iron-redox-calibration"
        in result.works[work_id].source_ids
    )

    context = result.context_by_work[work_id][0]
    fit_scope = context["values"]["fit_data_scope"]
    assert fit_scope["data_count"] == 228
    assert len(fit_scope["sources"]) == 7
    assert fit_scope["sources"][-1]["source_id"] == (
        "kress-carmichael-1991-iron-redox-calibration"
    )


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
    relation_counts = {
        relation.value: sum(residual.source_relation is relation for residual in residuals)
        for relation in (score_module.SourceRelation.TRAINING, score_module.SourceRelation.INDEPENDENT)
    }
    assert relation_counts == {"training": 223, "independent": 212}
    measured_rows = score_module.measured_engine_rail_records(
        [residual_to_plain(residual) for residual in residuals],
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    measured_training = next(
        row for row in measured_rows
        if row.get("rail") == Rail.REDOX.value
        and row.get("source_relation") == "training"
        and row.get("candidates")
    )
    assert measured_training["population"] == "calibration_source_overlap_fit_fidelity"
    assert (measured_training["candidates"], measured_training["numeric"]) == (223, 223)
    assert (measured_training["certified_numeric"], measured_training["flagged_numeric"]) == (213, 10)
    assert Decimal(measured_training["metrics"][0]["all_numeric"]["rms"]).quantize(
        Decimal("0.00001")
    ) == Decimal("0.11250")
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


def test_borisov_redox_headline_splits_calibration_overlap_from_accuracy(tmp_path):
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
    split = {
        record["population"]: record
        for record in records
        if record.get("rail") == Rail.REDOX.value
        and record.get("engine") == Engine.INTERNAL_ANALYTICAL.value
        and record.get("population")
    }
    assert set(split) == {
        "calibration_source_overlap_fit_fidelity",
        "independent_source_accuracy",
    }
    assert split["calibration_source_overlap_fit_fidelity"]["n"] == 213
    assert split["independent_source_accuracy"]["n"] == 212
    assert abs(
        Decimal(split["calibration_source_overlap_fit_fidelity"]["rms_dex"])
        - Decimal("0.1126289501574099044023575236")
    ) < Decimal("0.00001")
    assert abs(
        Decimal(split["calibration_source_overlap_fit_fidelity"]["median_dex"])
        - Decimal("-0.04086499066062312")
    ) < Decimal("0.00001")
    assert abs(
        Decimal(split["independent_source_accuracy"]["rms_dex"])
        - Decimal("0.10468")
    ) < Decimal("0.00001")
    assert abs(
        Decimal(split["independent_source_accuracy"]["median_dex"])
        - Decimal("-0.01574")
    ) < Decimal("0.00001")
    assert not any(
        record.get("tier") == "measured"
        and record.get("rail") == Rail.REDOX.value
        and record.get("engine") == Engine.INTERNAL_ANALYTICAL.value
        and not record.get("population")
        for record in records
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
    for headline in (object_records, accumulator_records):
        split = {
            record["population"]: record
            for record in headline
            if record.get("tier") == "measured"
            and record.get("rail") == Rail.REDOX.value
            and record.get("engine") == Engine.INTERNAL_ANALYTICAL.value
            and record.get("population")
        }
        assert split["calibration_source_overlap_fit_fidelity"]["n"] == 213
        assert split["independent_source_accuracy"]["n"] == 212
        assert split["calibration_source_overlap_fit_fidelity"]["source_relation"] == "training"
        assert split["independent_source_accuracy"]["source_relation"] == "independent"
        assert not any(
            record.get("rail") == Rail.REDOX.value
            and record.get("engine") == Engine.INTERNAL_ANALYTICAL.value
            and record.get("tier") == "measured"
            and not record.get("population")
            for record in headline
        )

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
    production_rows = {
        row["population"]: row
        for row in summary["records"]
        if row.get("tier") == "measured"
        and row.get("rail") == Rail.REDOX.value
        and row.get("engine") == Engine.INTERNAL_ANALYTICAL.value
        and row.get("population")
    }
    assert production_rows["calibration_source_overlap_fit_fidelity"]["n"] == 213
    assert production_rows["independent_source_accuracy"]["n"] == 212
    assert production_rows["calibration_source_overlap_fit_fidelity"]["source_relation"] == "training"
    assert production_rows["independent_source_accuracy"]["source_relation"] == "independent"
    assert abs(
        Decimal(production_rows["calibration_source_overlap_fit_fidelity"]["rms_dex"])
        - Decimal("0.1126289501574099044023575236")
    ) < Decimal("0.00001")
    assert abs(
        Decimal(production_rows["independent_source_accuracy"]["rms_dex"])
        - Decimal("0.10468")
    ) < Decimal("0.00001")
    report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    report_rows = {
        parts[2]: parts
        for line in report.splitlines()
        if line.startswith("| redox | internal-analytical | ")
        if (parts := [part.strip() for part in line.strip("|").split("|")])
        and len(parts) == 15
    }
    fit_row = report_rows["calibration_source_overlap_fit_fidelity"]
    independent_row = report_rows["independent_source_accuracy"]
    assert fit_row[3] == fit_row[5] == "213"
    assert independent_row[3] == independent_row[5] == "212"
    assert abs(
        Decimal(fit_row[8]) - Decimal("0.1126289501574099044023575236")
    ) < Decimal("0.00001")
    assert abs(Decimal(independent_row[8]) - Decimal("0.10468")) < Decimal("0.00001")

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
    measured_training = next(
        row for row in measured_summary["records"]
        if row.get("rail") == Rail.REDOX.value
        and row.get("engine") == Engine.INTERNAL_ANALYTICAL.value
        and row.get("source_relation") == "training"
        and row.get("candidates")
    )
    assert measured_training["population"] == "calibration_source_overlap_fit_fidelity"
    assert (measured_training["candidates"], measured_training["certified_numeric"]) == (223, 213)
    measured_report = (tmp_path / "data/battery/score-report.md").read_text(encoding="utf-8")
    measured_fit = next(
        [part.strip() for part in line.strip("|").split("|")]
        for line in measured_report.splitlines()
        if line.startswith("| redox | dex / dex | internal-analytical | calibration_source_overlap_fit_fidelity |")
    )
    assert measured_fit[3] == "calibration_source_overlap_fit_fidelity"


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
