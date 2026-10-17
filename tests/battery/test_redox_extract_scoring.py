import hashlib
import json
import math
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
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    QuenchState,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.migrate import Migrator, load_yaml, write_outputs
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    predict_with_engine,
    score_store,
    SCORE_ENGINE_SET,
)
from simulator.battery.records import Located, Locator, State, Value
from simulator.battery.score import headline_payload_records, residual_to_plain


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
    assert hashlib.sha256(encoded).hexdigest() == (
        "12100581851275f022f1e7ed747ee7aeb578a99537e11ca0aaf3eabe7a8bbc82"
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
    assert hashlib.sha256(encoded).hexdigest() == (
        "690007627c3e104224bad710c8c406dc6c7c12548f044f6bb0f309952e5f42de"
    )


def test_borisov_2018_rows_reach_redox_scoring_population():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    source = load_yaml(extract)
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
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
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
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
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
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
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
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


def test_aithala_redox_rows_score_only_when_composition_is_printed():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
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
        elif engine in {"internal-analytical", "openimcc"}:
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
