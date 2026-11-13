from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import Quantity
from simulator.battery import residue
from simulator.battery.residue import (
    ResidueInventoryRefusal,
    _sossi_project_element_ppm,
)

ROOT = Path(__file__).resolve().parents[2]


def test_existing_sossi_inventory_projection_is_pinned_before_refactor() -> None:
    projected = _sossi_project_element_ppm(
        {"SiO2": 1.0e-3, "MnO": 1.0e-4, "TiO2": 2.0e-5},
        -3.0e-6,
        ("Mn", "Ti"),
    )

    assert projected["Mn"] == pytest.approx(79937.77912959337, rel=1e-13)
    assert projected["Ti"] == pytest.approx(13929.806724084485, rel=1e-13)


def test_element_retention_quantity_is_declared() -> None:
    assert hasattr(Quantity, "ELEMENT_RETENTION_RATIO")


def test_element_retention_uses_residue_rail_and_dex_metric() -> None:
    from simulator.battery.enums import MetricOperation, Rail
    from simulator.battery.score import metric_operation, rail_for_quantity

    quantity = Quantity.ELEMENT_RETENTION_RATIO
    assert rail_for_quantity(quantity) is Rail.RESIDUE_COMPOSITION
    assert metric_operation(quantity) is MetricOperation.DEX


def test_inventory_retention_uses_the_declared_printed_definition() -> None:
    retention = getattr(residue, "_element_retention_ratio_from_inventories", None)
    assert callable(retention)

    # Hand calculation, with CIAAW formula masses: initially 1 mol FeO + 1 mol
    # MgO weighs 71.844 + 40.304 = 112.148 g. After 80% of FeO and 40% of MgO
    # remain, the bulk weighs 0.8*71.844 + 0.4*40.304 = 73.5968 g, so
    # M_f/M_i = 73.5968/112.148 = 0.6562471. Fe mass retention is 0.8;
    # concentration retention is 0.8/0.6562471 = 1.219053. Majors lost from
    # the melt make the concentration FM exceed one while Fe mass still falls.
    initial = {"FeO": 1.0, "MgO": 1.0}
    final = {"FeO": 0.8, "MgO": 0.4}

    assert retention(initial, final, "Fe", "mass_retention") == pytest.approx(0.8)
    assert retention(
        initial, final, "Fe", "concentration_retention"
    ) == pytest.approx(1.2190530023044481)


def test_unprinted_fm_definition_is_a_typed_refusal() -> None:
    resolve = getattr(residue, "_printed_fm_definition", None)
    assert callable(resolve)
    with pytest.raises(ResidueInventoryRefusal) as exc:
        resolve(None)
    assert exc.value.reason == "fo_definition_unprinted"


@pytest.mark.parametrize(
    "missing_key,reason",
    (
        ("duration", "duration_missing"),
        ("temperature", "temperature_missing"),
        ("fO2", "fO2_missing"),
        ("starting_composition", "starting_composition_missing"),
        ("sample_area_or_geometry", "sample_area_or_geometry_missing"),
        ("gas_conditions", "gas_conditions_missing"),
    ),
)
def test_each_missing_transport_input_is_typed(missing_key, reason) -> None:
    require_inputs = getattr(
        residue, "_require_element_retention_transport_inputs", None
    )
    assert callable(require_inputs)
    inputs = {
        "duration": 900.0,
        "temperature": 1573.15,
        "fO2": -0.68,
        "starting_composition": {"FeO": 1.0},
        "sample_area_or_geometry": {"area_m2": 2.0e-5},
        "gas_conditions": {"total_pressure_Pa": 101325.0},
    }
    inputs[missing_key] = None
    with pytest.raises(ResidueInventoryRefusal) as exc:
        require_inputs(inputs)
    assert exc.value.reason == reason


def test_element_absent_from_integrated_inventory_is_unavailable() -> None:
    retention = getattr(residue, "_element_retention_ratio_from_inventories", None)
    assert callable(retention)
    with pytest.raises(ResidueInventoryRefusal) as exc:
        retention({"FeO": 1.0}, {"FeO": 0.5}, "Gd", "mass_retention")
    assert exc.value.reason == "element_unavailable"


def test_sossi_printed_mn_row_reaches_the_scored_retention_path() -> None:
    import math
    from dataclasses import replace
    from decimal import Decimal

    import yaml

    from simulator.battery.enums import (
        Engine,
        MetricOperation,
        Phase,
        Quantity,
        UncertaintyKind,
    )
    from simulator.battery.records import (
        Located,
        Locator,
        Species,
        State,
        Uncertainty,
        Value,
    )
    from simulator.battery.score import (
        _sossi_residue_prediction,
        compile_residual,
        load_score_context,
        load_yaml,
        refusal_census,
        render_score_report,
    )
    from simulator.battery import score as score_module

    source_path = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"
    source = yaml.safe_load(source_path.read_text())
    values = source["species"]["Mn"]["observations"][0]["values"]
    source_row = next(
        row for row in values["series"] if row["run_id"] == "2M-15-07-16c"
    )
    initial_ppm = Decimal(str(values["starting_measured_ppm"]))
    final_ppm = Decimal(str(source_row["residue_ppm"]))
    observed_fm = final_ppm / initial_ppm
    # Table 1 reports starting Mn as 690.2 ppm (3.8 ppm SD; p. 53); Table 2
    # reports 759.6 ppm by mass in the quenched glass (p. 54). Since both
    # columns are ppm by mass, FM_obs = X_f/X_i = 759.6/690.2 = 1.10055… .
    # The integration below is the production Sossi inventory integrator;
    # it uses this row's printed T, duration, fO2 and starting Mn composition.
    run_id = "sossi-2019-run-2m-15-07-16c"
    experiment = next(
        row for row in source["experiments"] if row["experiment_id"] == run_id
    )
    context = load_score_context(sources=("kems-012-sossi-2019",))
    reference = next(
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.identity.species.formula == "Mn"
        and row.experiment_id.endswith(run_id)
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    locator = Locator(page=54, table="2", section="Results")
    definition = Located(
        State.of(
            {
                "definition": "concentration_retention",
                "quote": "ppm by mass in quenched glass",
                "page": 54,
            }
        ),
        locator=locator,
    )
    geometry = Located(
        State.of("powder slurry loaded onto a metal wire loop"),
        locator=Locator(page=4, section="2.1 Experimental Methods"),
    )
    q_reference = replace(
        reference,
        observation_id=reference.observation_id + "::element_retention_ratio",
        identity=replace(
            reference.identity,
            quantity=Quantity.ELEMENT_RETENTION_RATIO,
            subtype=State.of("concentration_retention"),
        ),
        value=Value.point_of(observed_fm),
        uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "Table 1 starting Mn SD": "3.8 ppm",
                "Table 2 residue Mn SD": "not reported",
            },
        ),
        point_conditions={
            **(reference.point_conditions or {}),
            "fm_definition": definition,
            "sample_area_or_geometry": geometry,
        },
    )
    context = replace(
        context,
        observations={**context.observations, q_reference.observation_id: q_reference},
    )
    starting_ppm = float(values["starting_measured_ppm"])
    buffered_fO2 = float(source_row["log10_fO2"]["value"])
    starting_ti_ppm = float(
        source["species"]["Ti"]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
    )
    integrated = residue._predict_sossi_residue_cohort(
        [experiment],
        load_yaml(ROOT / "data/vapor_pressures.yaml"),
        starting_trace_ppm_by_experiment={
            run_id: {"Mn": starting_ppm, "Ti": starting_ti_ppm}
        },
        buffered_fO2_log_by_experiment={run_id: buffered_fO2},
        code_revision="test",
        engine="internal-analytical",
    )[0]
    cache = {}
    score_module._residue_prediction_cache_entry(
        cache,
        "kems-012-sossi-2019",
        Engine.INTERNAL_ANALYTICAL,
        value={run_id: integrated},
    )
    prediction = _sossi_residue_prediction(
        context, q_reference, Engine.INTERNAL_ANALYTICAL, cache
    )
    assert prediction.value is not None
    scored, _candidate = compile_residual(
        q_reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert scored.numeric is not None
    assert scored.numeric.operation is MetricOperation.DEX
    assert float(scored.numeric.value) == pytest.approx(
        math.log10(float(prediction.value) / float(observed_fm)), rel=1e-12
    )
    from simulator.battery.score_report import format_element_retention_diagnostics
    from simulator.battery.score_report_data import element_retention_report_data

    report = format_element_retention_diagnostics(
        "score report",
        element_retention_report_data((scored,), context.observations),
    )
    assert "observed loss" in report
    assert "3.8 ppm" in report
    assert "negative (FM > 1)" in report
    rendered_report = render_score_report(
        (scored,),
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        root=ROOT,
    )
    assert "Element retention diagnostics" in rendered_report
    assert "Observed loss bins" in rendered_report

    unavailable_reference = replace(
        q_reference,
        observation_id=q_reference.observation_id + "::Gd",
        identity=replace(q_reference.identity, species=Species("Gd", Phase.L)),
    )
    unavailable_prediction = _sossi_residue_prediction(
        context, unavailable_reference, Engine.INTERNAL_ANALYTICAL, {}
    )
    assert unavailable_prediction.refusal_detail["reason"] == "element_unavailable"
    context = replace(
        context,
        observations={
            **context.observations,
            unavailable_reference.observation_id: unavailable_reference,
        },
    )
    unavailable_score, _ = compile_residual(
        unavailable_reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=unavailable_prediction,
    )
    census = refusal_census((unavailable_score,))
    assert census.get("outside_supported_species:element_unavailable") == 1
