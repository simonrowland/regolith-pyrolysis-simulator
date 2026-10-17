from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    Engine,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.migrate import (
    LITERATURE,
    Migrator,
    load_migrated_store,
    load_yaml,
    select_declared_source,
)
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Located,
    State,
    Value,
)
from simulator.battery.residue import _initial_oxide_wt_percent
from simulator.battery.score import (
    QUANTITY_METRIC,
    compile_residual,
    compute_metric,
    load_score_context,
    predict_with_engine,
    quantity_token,
)


@pytest.fixture(scope="module")
def mass_loss_rows():
    _works, experiments, observations = load_migrated_store(
        sources=("kems-010-richter-2007", "kems-026-markova-1984")
    )
    richter = next(
        row
        for row in observations.values()
        if row.source_id == "kems-010-richter-2007"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.observation_id.endswith("R-11::T=2073.15:row=r-11:h=0d649fba8fb2")
    )
    richter_r14 = next(
        row
        for row in observations.values()
        if row.source_id == "kems-010-richter-2007"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and "R-14::T=2073.15:row=r-14:" in row.observation_id
    )
    markova = next(
        row
        for row in observations.values()
        if row.source_id == "kems-026-markova-1984"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
    )
    return (
        richter,
        experiments[richter.experiment_id],
        markova,
        experiments[markova.experiment_id],
        richter_r14,
        experiments[richter_r14.experiment_id],
    )


def _richter_rep5_run(experiment):
    """Add exact R-11 mass and starting area printed in rep5's Table 1 row."""
    locator = experiment.locator
    area = Located(
        State.of(Value(ValueKind.POINT, point=Decimal("0.0000913"))), locator
    )
    sample = replace(
        experiment.sample,
        mass_kg=Located(
            State.of(Value(ValueKind.POINT, point=Decimal("0.0001904"))), locator
        ),
    )
    apparatus = experiment.apparatus or Apparatus()
    geometry = apparatus.geometry or ApparatusGeometry()
    apparatus = replace(
        apparatus,
        geometry=replace(geometry, exposed_area_m2=area),
    )
    return replace(
        experiment,
        method=State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
        sample=sample,
        apparatus=apparatus,
    )


def _extract_mass_loss_selection(source_id: str, observation_id: str):
    document = load_yaml(LITERATURE / "extracts" / f"{source_id}.yaml")
    assert isinstance(document, dict)
    for block in (document.get("species") or {}).values():
        for observation in block.get("observations") or ():
            if observation.get("observation_id") == observation_id:
                return select_declared_source(
                    Quantity.MASS_LOSS_FRACTION,
                    observation.get("units"),
                    observation.get("values"),
                )
    raise AssertionError(f"source observation not found: {source_id}/{observation_id}")


def test_pyrolysis_yield_mass_loss_uses_bulk_dispatch(mass_loss_rows) -> None:
    (
        _richter,
        _richter_experiment,
        markova,
        markova_experiment,
        _r14,
        _r14_experiment,
    ) = mass_loss_rows

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        markova,
        experiment=markova_experiment,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert "residual melts" in (
        markova_experiment.sample.initial_composition.locator.note.lower()
    )
    assert prediction.refusal_detail["reason"] == "initial_composition_not_stated"
    assert prediction.refusal_detail["missing_input"] == "sample.initial_composition"


def test_richter_r14_mass_loss_uses_source_declared_initial_glass_not_final_residue(
    mass_loss_rows,
) -> None:
    *_, _row, experiment = mass_loss_rows

    starting_wt_percent = _initial_oxide_wt_percent(experiment.sample)

    # R-14's starting glass is the shared Table 1 source composition. Its
    # printed row composition (7.03/29.63/29.03/34.31 wt%) is the final residue.
    assert starting_wt_percent == pytest.approx(
        {"MgO": 11.48, "SiO2": 46.00, "Al2O3": 19.39, "CaO": 23.12},
        abs=0.02,
    )


def test_richter_rep5_row_has_numeric_residual_and_domain_notice(mass_loss_rows) -> None:
    richter, experiment, _markova, _markova_experiment, _r14, _r14_experiment = (
        mass_loss_rows
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        richter,
        experiment=_richter_rep5_run(experiment),
    )

    assert prediction.refusal_reason is None, prediction.refusal_detail
    assert prediction.value is not None
    assert prediction.unit == "dimensionless"
    assert richter.value.point is not None
    residual = compute_metric(
        MetricOperation.ABSOLUTE, prediction.value, richter.value.point
    )
    assert residual is not None and residual.is_finite()
    context = load_score_context(sources=("kems-010-richter-2007",))
    context = replace(
        context,
        experiments={
            **context.experiments,
            experiment.experiment_id: _richter_rep5_run(experiment),
        },
    )
    scored, _candidate = compile_residual(
        richter,
        Engine.OPENIMCC,
        context=context,
        prediction=prediction,
        comparison_ids={richter.observation_id},
    )
    assert scored.numeric is not None
    assert scored.numeric.operation is MetricOperation.ABSOLUTE
    assert scored.numeric.value == residual
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "paper-demonstrated-window" in notice.reason
        for notice in prediction.notices
    )


def test_richter_rep5_table1_inputs_migrate_to_sample_mass_and_area() -> None:
    # These R-11 values are copied from rep5 commit f6960521a, Table 1's
    # sample_surface_area rows; they are the same source inputs as the score fixture.
    document = {
        "source_id": "kems-010-richter-2007",
        "species": {
            "MgO": {
                "observations": [
                    {
                        "equipment": {
                            "sample_surface_area": {
                                "value": [
                                    {
                                        "sample": "R-11",
                                        "initial_area_mm2": 91.3,
                                        "initial_weight_mg": 190.4,
                                        "locator": {"table": "1", "row": "R-11"},
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        },
        "experiments": [
            {
                "experiment_id": "cai-r-11",
                "locator": {"row": "R-11"},
                "sample": {},
            }
        ],
    }

    migrated = Migrator._richter_table1_sample_inputs(document)
    experiment = migrated["experiments"][0]
    sample_mass = experiment["sample"]["mass_kg"]["state"]["value"]["point"]
    exposed_area = experiment["apparatus"]["geometry"]["exposed_area_m2"]["state"]["value"]["point"]

    assert Decimal(sample_mass) == Decimal("0.0001904")
    assert Decimal(exposed_area) == Decimal("0.0000913")


def test_richter_row_without_exposed_area_refuses(mass_loss_rows) -> None:
    richter, experiment, _markova, _markova_experiment, _r14, _r14_experiment = (
        mass_loss_rows
    )
    enriched = _richter_rep5_run(experiment)
    apparatus = replace(
        enriched.apparatus,
        geometry=replace(enriched.apparatus.geometry, exposed_area_m2=None),
    )
    missing_geometry = replace(enriched, apparatus=apparatus)

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        richter,
        experiment=missing_geometry,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "sample_geometry_not_stated"
    assert prediction.value is None


def test_engine_without_residue_capability_refuses_with_engine_name(mass_loss_rows) -> None:
    (
        _richter,
        _richter_experiment,
        markova,
        markova_experiment,
        _r14,
        _r14_experiment,
    ) = mass_loss_rows
    engine = Engine.VAPOROCK

    prediction = predict_with_engine(
        engine,
        markova,
        experiment=markova_experiment,
    )

    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "engine_capability_unsupported"
    assert prediction.refusal_detail["engine"] == engine.value
    assert "free_evaporation_residue_integrator" in prediction.refusal_detail["capability"]


def test_mass_loss_uses_absolute_fraction_residual() -> None:
    assert QUANTITY_METRIC[Quantity.MASS_LOSS_FRACTION] is MetricOperation.ABSOLUTE


def test_mass_loss_source_values_select_points_and_preserve_printed_range() -> None:
    britt = _extract_mass_loss_selection(
        "britt-2019-asteroid-simulants", "britt_2019_ci_tg_integrated_mass_loss"
    )
    assert britt.field_name == "total_integrated_mass_loss_wt_pct"
    assert britt.amount == Decimal("0.143")

    wilkerson = _extract_mass_loss_selection(
        "wilkerson-2021-jsc1a-tga-ms-poster",
        "wilkerson_2021_h2o_total_loss_wt_pct",
    )
    assert wilkerson.field_name == "total_loss_wt_pct"
    assert wilkerson.amount == Decimal("0.0016")

    engelschion = _extract_mass_loss_selection(
        "engelschion-2020-eac1a-simulant",
        "engelschion_2020_eac1a_water_release",
    )
    assert engelschion.field_name == "water_released_wt_pct_as_printed"
    assert engelschion.amount == Decimal("0.008")
    assert engelschion.value.approximate

    street = _extract_mass_loss_selection(
        "street-2010-thermal-properties-simulants",
        "street_2010_anorthosite_water_mass_loss_0p1_pct",
    )
    assert street.field_name == "mass_loss_percent"
    assert street.amount == Decimal("0.001")
    assert street.value.approximate

    wilkerson_range = _extract_mass_loss_selection(
        "wilkerson-2021-jsc1a-tga-ms-poster",
        "wilkerson_2021_jsc1a_arh2_750C_2hr_hold_mass_loss",
    )
    assert wilkerson_range.value.kind is ValueKind.INTERVAL
    assert wilkerson_range.value.interval_low == Decimal("0.0045")
    assert wilkerson_range.value.interval_high == Decimal("0.005")


def test_printed_mass_loss_range_refuses_point_residual() -> None:
    source_id = "wilkerson-2021-jsc1a-tga-ms-poster"
    observation_id = "wilkerson_2021_jsc1a_arh2_750C_2hr_hold_mass_loss"
    _works, _experiments, observations = load_migrated_store(sources=(source_id,))
    stored_row = next(
        row
        for row in observations.values()
        if row.source_id == source_id and observation_id in row.observation_id
    )
    selected = _extract_mass_loss_selection(source_id, observation_id)
    assert selected.value.kind is ValueKind.INTERVAL
    reference = replace(stored_row, value=selected.value)
    context = load_score_context(sources=(source_id,))

    scored, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        comparison_ids={reference.observation_id},
    )

    assert scored.refusal is not None
    assert scored.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert scored.refusal.detail["reason"] == "observed_mass_loss_interval_not_point"
