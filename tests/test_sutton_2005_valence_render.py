"""Render-checked row count and valence anchors for Sutton et al. (2005)."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sutton-2005-vanadium-xanes.yaml"


def test_sutton_table_valences_match_render_and_printed_row_counts():
    extract = yaml.safe_load(EXTRACT.read_text())
    observations = extract["species"]["V"]["observations"]
    schreiber = observations[0]["values"]["points"]
    synthetic = observations[1]["values"]["points"]
    natural = observations[2]["values"]["points"]

    assert len(schreiber) + len(synthetic) == 49  # Table 2, page 2339, PDF page 7
    assert len(natural) == 10  # Table 3 individual samples, page 2344, PDF page 12
    assert (schreiber[0]["sample"], schreiber[0]["value"], schreiber[0]["sigma"]) == (
        "X116",
        4.87,
        0.15,
    )
    assert (synthetic[4]["sample"], synthetic[4]["value"], synthetic[4]["sigma"]) == (
        "93-14-15 (1596B)",
        4.13,
        0.07,
    )
    assert (natural[0]["sample"], natural[0]["value"], natural[0]["sigma"]) == (
        "Green (15318, 15425, 15426)",
        2.87,
        0.07,
    )
    assert (natural[-1]["sample"], natural[-1]["value"], natural[-1]["sigma"]) == (
        "Cerro Negro inclusions",
        4.06,
        0.07,
    )


def test_table3_inferred_conditions_are_refused_and_table1_composition_is_fixed():
    extract = yaml.safe_load(EXTRACT.read_text())
    observations = extract["species"]["V"]["observations"]
    natural_points = observations[2]["values"]["points"]
    assert all(
        point["log_fO2"]["inferred"] is True
        and "T_C" not in point
        and "delta_log_fO2_IW" not in point
        for point in natural_points
    )

    yellow = extract["extraction"]["composition_template_lunar_yellow"]
    yellow_components = yellow["point_conditions"]["composition"]["state"][
        "value"
    ]["components"]
    assert [component for component in yellow_components if component[0] == "SO3"] == [
        ["SO3", "0.05"]
    ]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 59
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 0

    table2 = [
        observation
        for observation in result.observations.values()
        if "sutton_2005_table2_" in observation.observation_id
    ]
    table3 = [
        observation
        for observation in result.observations.values()
        if "sutton_2005_table3_natural" in observation.observation_id
    ]
    assert len(table2) == 49
    assert all(observation.identity.temperature_K.is_value for observation in table2)
    assert all(
        "composition" in observation.point_conditions
        and observation.point_conditions["composition"].locator is not None
        for observation in (*table2, *table3)
    )

    green = next(observation for observation in table3 if "row=green-" in observation.observation_id)
    assert not green.identity.temperature_K.is_value
    assert not green.identity.fO2_Pa.is_value
    assert str(green.value.point) == "2.87"

    x116 = next(
        observation
        for observation in result.observations.values()
        if "table2_schreiber" in observation.observation_id
        and "row=x116" in observation.observation_id
    )
    assert x116.uncertainty.verbatim["sigma"] == 0.15

    yellow_observation = next(
        observation for observation in table3 if "row=yellow-" in observation.observation_id
    )
    assert yellow_observation.identity.composition.is_value
    assert yellow_observation.identity.composition.value.as_map()["SO3"] > 0
