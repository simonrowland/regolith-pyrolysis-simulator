from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    Authority,
    Engine,
    NoticeKind,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.migrate import Migrator, load_yaml
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    predict_with_engine,
    score_store,
    SCORE_ENGINE_SET,
)
from simulator.battery.records import Value


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
    assert len(residuals) == 435
    assert all(residual.numeric is not None for residual in residuals)
    assert all(residual.status is not ResidualStatus.REFUSED for residual in residuals)
    assert all(residual.numeric.operation.value == "dex" for residual in residuals)

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


def test_existing_sixteen_redox_rows_are_refused_for_unprinted_composition():
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
