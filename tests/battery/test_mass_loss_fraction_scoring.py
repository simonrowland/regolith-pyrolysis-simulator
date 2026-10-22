from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

import simulator.battery.migrate as migrate_module
import simulator.battery.score as score_module
from simulator.battery.enums import (
    Engine,
    MethodToken,
    MetricOperation,
    Quantity,
    RefusalReason,
    ResidualStatus,
    ValueKind,
)
from simulator.battery.migrate import (
    LITERATURE,
    Migrator,
    load_migrated_store,
    load_yaml,
    sample_from_equipment,
    select_declared_source,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    Apparatus,
    ApparatusGeometry,
    Located,
    Locator,
    State,
    Value,
)
from simulator.battery.residue import _initial_oxide_wt_percent
from simulator.battery.score import (
    QUANTITY_METRIC,
    compile_residual,
    load_score_context,
    predict_with_engine,
    quantity_token,
)

pytestmark = pytest.mark.usefixtures("_close_openimcc_worker_between_tests")


@pytest.fixture
def without_sourced_alpha_context(monkeypatch):
    """Exercise d-204 even when the extraction offer is overlaid by CI."""
    original = score_module.load_yaml

    def load_without_fits(path):
        document = original(path)
        if path.name == "kems-010-richter-2007.yaml":
            document = deepcopy(document)
            for block in document.get("species", {}).values():
                block["context"] = [
                    row for row in block.get("context", ())
                    if not isinstance(row.get("values"), dict)
                    or "alpha_form" not in row["values"]
                ]
        return document

    monkeypatch.setattr(score_module, "load_yaml", load_without_fits)


@pytest.fixture(scope="module")
def fresh_mass_loss_extracts():
    richter = Migrator()
    richter._migrate_extract(LITERATURE / "extracts" / "kems-010-richter-2007.yaml")
    markova = Migrator()
    markova._migrate_extract(LITERATURE / "extracts" / "kems-026-markova-1984.yaml")
    return richter.result, markova.result


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


_EVAPORATION_COEFFICIENTS_NOT_SOURCED = (
    "per-species evaporation coefficients are not sourced for this work"
)


def _assert_evaporation_coefficients_not_sourced(prediction) -> None:
    assert prediction.value is None, prediction.value
    assert prediction.refusal_reason is RefusalReason.UNDERDETERMINED_APPARATUS, (
        prediction.refusal_reason
    )
    detail = prediction.refusal_detail
    assert detail["reason"] == "evaporation_coefficients_not_sourced"
    assert detail["detail"] == _EVAPORATION_COEFFICIENTS_NOT_SOURCED
    assert detail["missing_input"] is Quantity.EVAPORATION_COEFFICIENT_ALPHA


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


def test_pyrolysis_yield_mass_loss_uses_bulk_dispatch(
    fresh_mass_loss_extracts
) -> None:
    _richter_result, markova_result = fresh_mass_loss_extracts
    markova = next(
        row
        for row in markova_result.observations.values()
        if row.experiment_id.endswith("::VI")
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.value.point == 0
    )
    markova_experiment = markova_result.experiments[markova.experiment_id]

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        markova,
        experiment=markova_experiment,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "duration_not_stated"
    assert prediction.refusal_detail["missing_input"] == "thermal_schedule.total_duration_s"
    assert _initial_oxide_wt_percent(markova_experiment.sample) == pytest.approx(
        {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95},
        abs=0.01,
    )


@pytest.mark.parametrize(
    ("sample_id", "expected"),
    (
        (
            "VI",
            {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95},
        ),
        (
            "V",
            {"SiO2": 52.32, "Al2O3": 19.13, "FeO": 9.37, "MgO": 6.25, "CaO": 12.93},
        ),
    ),
)
def test_markova_printed_t0_charge_is_accepted_as_initial(
    fresh_mass_loss_extracts, sample_id, expected
) -> None:
    _richter_result, markova_result = fresh_mass_loss_extracts
    row = next(
        row
        for row in markova_result.observations.values()
        if row.experiment_id.endswith(f"::{sample_id}")
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.value.point == 0
    )
    experiment = markova_result.experiments[row.experiment_id]

    assert _initial_oxide_wt_percent(experiment.sample) == pytest.approx(
        expected, abs=0.01
    )
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.refusal_detail["reason"] == "duration_not_stated"


def test_untyped_richter_final_map_never_becomes_initial(mass_loss_rows) -> None:
    _richter, _experiment, _markova, _markova_experiment, r14, r14_experiment = (
        mass_loss_rows
    )
    printed = r14_experiment.sample.printed_composition
    assert printed is not None and printed.state.is_value
    final_map = printed.state.value
    untyped_sample = sample_from_equipment(
        {},
        values={"composition_wt_pct": final_map},
        locator=Locator(
            table="1",
            paragraph="R-14 final-state composition",
            note="post-run after evaporation",
        ),
    )

    assert untyped_sample.initial_composition is None
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        r14,
        experiment=replace(r14_experiment, sample=untyped_sample),
    )
    assert prediction.refusal_detail["reason"] == "initial_composition_not_stated"


@pytest.mark.parametrize(
    ("label", "locator", "extract_comment"),
    (
        (
            "table_header",
            {"table": "1: composition after evaporation"},
            None,
        ),
        (
            "caption",
            {"paragraph": "Caption: final residual melt composition"},
            None,
        ),
        (
            "extract_comment",
            {"table": "1"},
            "The composition is the final residue after evaporation.",
        ),
        ("no_role", {"table": "1"}, None),
    ),
)
def test_finalize_never_promotes_untyped_observation_map_to_initial(
    label, locator, extract_comment, monkeypatch
) -> None:
    source = LITERATURE / "extracts" / "kems-010-richter-2007.yaml"
    original = load_yaml(source)
    enriched = migrate_module.Migrator._richter_table1_sample_inputs(original)
    experiment = next(
        deepcopy(item)
        for item in enriched["experiments"]
        if item.get("experiment_id") == "cai-r-14"
    )
    final_map = experiment["sample"].pop("printed_composition")["state"]["value"]
    experiment["sample"].pop("initial_composition")
    observation = next(
        deepcopy(item)
        for item in original["species"]["Mg"]["observations"]
        if item.get("observation_id")
        == "richter_2007_mg_table1_mass_loss_fraction_R-14"
    )
    observation.pop("supersedes", None)
    observation["values"]["composition_wt_pct"] = deepcopy(final_map)
    observation["values"]["locator"] = locator
    document = {
        **deepcopy(enriched),
        "experiments": [experiment],
        "species": {"Mg": {"observations": [observation]}},
    }
    if extract_comment is not None:
        document["extraction"]["comment"] = extract_comment

    migrator = Migrator()
    original_load_yaml = load_yaml
    monkeypatch.setattr(
        migrate_module,
        "load_yaml",
        lambda path: document if path == source else original_load_yaml(path),
    )
    migrator._migrate_extract(source)
    row = next(
        row
        for row in migrator.result.observations.values()
        if quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
    )
    before = migrator.result.experiments[row.experiment_id]
    assert before.sample.initial_composition is None, label

    migrator.finalize()
    after = migrator.result.experiments[row.experiment_id]
    assert after.sample.initial_composition is None, label
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=after)
    assert prediction.refusal_detail["reason"] == "initial_composition_not_stated"


@pytest.mark.parametrize("sample_id", ("R-11", "R-14"))
@pytest.mark.usefixtures("without_sourced_alpha_context")
def test_richter_rows_refuse_unsourced_evaporation_coefficients(
    fresh_mass_loss_extracts, sample_id
) -> None:
    richter_result, _markova_result = fresh_mass_loss_extracts
    row = next(
        row
        for row in richter_result.observations.values()
        if quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and f"row={sample_id.lower()}" in row.observation_id
    )
    experiment = richter_result.experiments[row.experiment_id]

    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)

    _assert_evaporation_coefficients_not_sourced(prediction)


@pytest.mark.parametrize(
    "source_id",
    ("kems-015-hashimoto-1983", "kems-026-markova-1984"),
)
def test_any_source_refuses_unsourced_evaporation_coefficients(
    fresh_mass_loss_extracts, source_id
) -> None:
    """The predictor does not exempt a source, including Hashimoto 1983."""
    richter_result, _markova_result = fresh_mass_loss_extracts
    row = next(
        row
        for row in richter_result.observations.values()
        if quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and "row=r-11" in row.observation_id
    )
    experiment = richter_result.experiments[row.experiment_id]

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        replace(row, source_id=source_id),
        experiment=experiment,
    )

    _assert_evaporation_coefficients_not_sourced(prediction)


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


@pytest.mark.usefixtures("without_sourced_alpha_context")
def test_richter_catalogue_sibling_keeps_initial_through_finalization(
    monkeypatch,
) -> None:
    source = LITERATURE / "extracts" / "kems-010-richter-2007.yaml"
    document = deepcopy(load_yaml(source))
    experiment = next(
        item for item in document["experiments"] if item["experiment_id"] == "cai-r-14"
    )
    final_map = experiment["sample"]["printed_composition"]["state"]["value"]
    experiment["sample"].pop("initial_composition")

    catalogue_map = {
        "MgO": 11.48,
        "SiO2": 46.00,
        "Al2O3": 19.39,
        "CaO": 23.12,
    }
    source_note = "Richter 2007 Table 1, R-14 starting glass"
    selection_rule = "the Table 1 starting-material composition for R-14"
    document["species"].setdefault("Mg", {}).setdefault("observations", []).append(
        {
            "observation_id": "richter_2007_r14_catalogue_residue_composition",
            "type": "composition_series",
            "experiment": "cai-r-14",
            "locator": {"table": "1", "row": "R-14"},
            "phase": "l",
            "regime": "langmuir_free_evaporation",
            "units": "wt.%",
            "values": {
                "quantity": "residue_composition_vs_time",
                "method_class": "measured_direct",
                "composition_from_sample_catalog": True,
                "composition_source": source_note,
                "analysis_selection_rule": selection_rule,
                "sample_oxide_composition_wt_pct": catalogue_map,
                "series": [
                    {
                        "sample": "R-14",
                        "T_C": 1800,
                        "t_min": 30,
                        **{f"{name}_wt_pct": amount for name, amount in final_map.items()},
                    }
                ],
            },
        }
    )

    original_load_yaml = load_yaml
    monkeypatch.setattr(
        migrate_module,
        "load_yaml",
        lambda path: document if path == source else original_load_yaml(path),
    )
    migrator = Migrator()
    migrator._migrate_extract(source)
    migrator.finalize()

    row = next(
        row
        for row in migrator.result.observations.values()
        if row.source_id == "kems-010-richter-2007"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and "R-14::T=2073.15" in row.observation_id
    )
    migrated = migrator.result.experiments[row.experiment_id]
    initial = migrated.sample.initial_composition
    assert initial is not None and initial.state.is_value
    composition = initial.state.value
    assert composition.proxy_flag == "composition_from_sample_catalog"
    assert composition.proxy_source == source_note
    assert composition.analysis_selection_rule == selection_rule
    assert _initial_oxide_wt_percent(migrated.sample) == pytest.approx(
        catalogue_map, abs=0.02
    )

    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=migrated)
    _assert_evaporation_coefficients_not_sourced(prediction)


def test_mendybaev_func5_keeps_serialization_equivalent_table1_start() -> None:
    source = LITERATURE / "extracts" / (
        "mendybaev-2017-fun-cai-lab-evaporation.yaml"
    )
    migrator = Migrator()
    migrator._migrate_extract(source)
    migrator.finalize()

    row = next(
        row
        for row in migrator.result.observations.values()
        if row.source_id == "mendybaev-2017-fun-cai-lab-evaporation"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.experiment_id.endswith("::func-5")
    )
    experiment = migrator.result.experiments[row.experiment_id]
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert _initial_oxide_wt_percent(experiment.sample) == pytest.approx(
        {"MgO": 37.9, "Al2O3": 11.6, "SiO2": 42.9, "CaO": 7.6},
        abs=0.02,
    )

    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.value is not None or (
        prediction.refusal_reason is not None
        and prediction.refusal_detail is not None
        and prediction.refusal_detail.get("reason")
        != "initial_composition_not_stated"
    )


@pytest.mark.usefixtures("without_sourced_alpha_context")
def test_richter_rep5_row_refuses_unsourced_evaporation_coefficients(
    mass_loss_rows,
) -> None:
    richter, experiment, _markova, _markova_experiment, _r14, _r14_experiment = (
        mass_loss_rows
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        richter,
        experiment=_richter_rep5_run(experiment),
    )

    _assert_evaporation_coefficients_not_sourced(prediction)
    assert prediction.notices == ()
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
    assert scored.status is ResidualStatus.REFUSED
    assert scored.numeric is None
    assert scored.refusal is not None
    assert scored.refusal.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert scored.refusal.detail["reason"] == "evaporation_coefficients_not_sourced"


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


def test_hashimoto_table5_starting_composition_is_typed_on_each_point() -> None:
    source_id = "kems-015-hashimoto-1983"
    source_path = LITERATURE / "extracts" / f"{source_id}.yaml"
    document = load_yaml(source_path)
    source_row = next(
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row["observation_id"]
        == "hashimoto_1983_table5_initial_vaporization_rate_F"
    )
    expected = migrate_module.wt_pct_to_mole_fraction(
        source_row["values"]["composition_wt_pct"]
    )

    migrator = Migrator()
    migrator._migrate_extract(source_path)
    point_prefix = (
        f"{source_id}::hashimoto_1983_table5_initial_vaporization_rate_F::T="
    )
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.startswith(point_prefix)
    ]

    assert len(observations) == 4
    assert {
        observation.observation_id.split("::T=", 1)[1].split(":h=", 1)[0]
        for observation in observations
    } == {"1973.15", "2073.15", "2173.15", "2273.15"}
    for observation in observations:
        point_composition = (observation.point_conditions or {}).get("composition")
        assert point_composition is not None and point_composition.state.is_value
        point = point_composition.state.value
        assert dict(point.components) == dict(expected.components)
        assert point.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG

        initial = migrator.result.experiments[
            observation.experiment_id
        ].sample.initial_composition
        assert initial is not None and initial.state.is_value
        assert initial.state.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
        assert not migrate_module._initial_compositions_conflict(
            initial, point_composition
        )


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
