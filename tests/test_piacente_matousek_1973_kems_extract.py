from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/piacente-matousek-1973-sodium-disilicate-kems.yaml"
SOURCE_ID = "piacente-matousek-1973-sodium-disilicate-kems"


def _migrated_at(migrated: dict, source_observation_id: str, temperature: str):
    prefix = f"{SOURCE_ID}::{source_observation_id}::T={temperature}:"
    matches = [row for key, row in migrated.items() if key.startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


def test_piacente_matousek_1973_table_rows_and_render_pins() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = [
        (observation, point)
        for observation in extract["species"]["Na"]["observations"]
        if observation["type"] == "partial_pressure"
        for point in observation["values"]["points"]
    ]
    assert len(source_rows) == 80  # Tables I–IV print 41 + 24 + 7 + 8 rows.
    expected_cell_materials = {
        "piacente_1973_run_1_0_partial_pressure": "Mo",
        "piacente_1973_run_2_1_partial_pressure": "Mo",
        "piacente_1973_run_2_2_partial_pressure": "Mo",
        "piacente_1973_run_2_3_partial_pressure": "Mo",
        "piacente_1973_run_3_0_partial_pressure": "MgO",
        "piacente_1973_run_3_1_partial_pressure": "MgO",
        "piacente_1973_run_4_0_partial_pressure": "Re",
        "piacente_1973_run_5_0_partial_pressure": "Ta",
    }
    pressure_series = {
        row["observation_id"]: row
        for row in extract["species"]["Na"]["observations"]
        if row["type"] == "partial_pressure"
    }
    source_benches = {
        bench["id"]: bench["cell_materials"][0]["state"]["value"]
        for bench in extract["benches"]
    }
    assert source_benches == {
        "piacente-1973-mo-cell": "Mo",
        "piacente-1973-mgo-cell": "MgO",
        "piacente-1973-re-cell": "Re",
        "piacente-1973-ta-cell": "Ta",
    }

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 86
    assert not result.validation.hard_issues

    from simulator.battery.score import _cell_material_class, _uniform_modelled_reactive_cell

    expected_benches = {
        "Mo": ("piacente-1973-mo-cell", "reactive", "Mo"),
        "MgO": ("piacente-1973-mgo-cell", "not_inert", None),
        "Re": ("piacente-1973-re-cell", "reactive", None),
        "Ta": ("piacente-1973-ta-cell", "reactive", None),
    }
    bench_ids = {}
    for material, (bench_suffix, expected_class, expected_modelled) in expected_benches.items():
        bench = next(item for item in result.benches.values() if item.id.endswith(f"::bench::{bench_suffix}"))
        assert bench.cell_materials is not None and len(bench.cell_materials) == 1
        cell = bench.cell_materials[0]
        assert cell.state.is_value and cell.state.value.value == material
        assert cell.locator is not None
        assert _cell_material_class(bench.cell_materials) == expected_class
        assert _uniform_modelled_reactive_cell(bench.cell_materials) == expected_modelled
        bench_ids[material] = bench.id
    for observation_id, material in expected_cell_materials.items():
        experiment_name = pressure_series[observation_id]["experiment"]
        experiment = next(item for item in result.experiments.values() if item.experiment_id.endswith(f"::{experiment_name}"))
        assert experiment.bench_id == bench_ids[material]
    means = [item for item in result.experiments.values() if item.experiment_id.endswith("::four-cell-mean")]
    assert len(means) == 1 and means[0].bench_id is None

    pressure_rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert len(pressure_rows) == 80
    assert all(row.value.kind.value == "point" for row in pressure_rows)
    assert all(row.identity.species.formula == "Na" for row in pressure_rows)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in pressure_rows)
    assert all(row.point_conditions["composition"].state.is_value for row in pressure_rows)
    assert all(row.identity.temperature_K.is_value for row in pressure_rows)
    assert all(row.identity.composition.is_value for row in pressure_rows)

    # The page-4 and page-7 render pins are the printed coefficients in Tables I, II, IV.
    pins = (
        ("piacente_1973_run_2_1_partial_pressure", "1490", Decimal("0.91901775")),
        ("piacente_1973_run_3_1_partial_pressure", "1430", Decimal("0.332042025")),
        ("piacente_1973_run_5_0_partial_pressure", "1523", Decimal("0.123413850")),
        ("piacente_1973_run_5_0_partial_pressure", "1546", Decimal("0.229703775")),
    )
    for observation_id, temperature, expected_pa in pins:
        row = _migrated_at(result.observations, observation_id, temperature)
        assert row.value.point == expected_pa

    table_iv = pressure_series["piacente_1973_run_5_0_partial_pressure"]["values"]["points"]
    for index, temperature, printed in ((5, "1523", "12.18"), (6, "1531", "17.35"), (7, "1546", "22.67")):
        point = table_iv[index]
        assert point["T_K"] == temperature
        assert point["P_Na_times_10^7_atm_as_printed"] == printed
        assert "factor of ten" in point["locator"]["note"]
    sample_paths = {sample["path"]: sample["value"] for sample in extract["fidelity_samples"]}
    for index, printed in ((5, "12.18"), (6, "17.35"), (7, "22.67")):
        path = f"species.Na.observations[piacente_1973_run_5_0_partial_pressure].values.points[{index}].P_Na_times_10^7_atm_as_printed"
        assert sample_paths[path] == printed

    for observation_id, temperature in (
        ("piacente_1973_run_2_1_partial_pressure", "1490"),
        ("piacente_1973_run_3_1_partial_pressure", "1430"),
        ("piacente_1973_run_5_0_partial_pressure", "1523"),
    ):
        identity = _migrated_at(result.observations, observation_id, temperature).identity
        assert identity.temperature_K.value == Decimal(temperature)
        assert identity.composition.is_value
        assert identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa.is_unknown

    # The fitted values are retained in the source records; the current v2 migrator
    # recognizes their quantity but has no selected source field for its value.
    fits = [row for row in result.observations.values() if row.identity.quantity.value is Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW]
    assert len(fits) == 6
    assert all(row.value.kind.value == "unavailable" for row in fits)
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 6

    source_fits = {
        row["observation_id"]: row["values"]
        for row in extract["species"]["Na"]["observations"]
        if row["values"].get("quantity") == "second_law_enthalpy_of_vaporization"
    }
    assert source_fits["piacente_1973_mo_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_1_0_partial_pressure",
        "piacente_1973_run_2_1_partial_pressure",
        "piacente_1973_run_2_2_partial_pressure",
        "piacente_1973_run_2_3_partial_pressure",
    ]
    assert source_fits["piacente_1973_mgo_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_3_0_partial_pressure",
        "piacente_1973_run_3_1_partial_pressure",
    ]
    assert source_fits["piacente_1973_re_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_4_0_partial_pressure"
    ]
    assert source_fits["piacente_1973_ta_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_5_0_partial_pressure"
    ]
    expected_mean_parents = {
        "piacente_1973_mo_enthalpy_fit",
        "piacente_1973_mgo_enthalpy_fit",
        "piacente_1973_re_enthalpy_fit",
        "piacente_1973_ta_enthalpy_fit",
    }
    assert all(
        set(fit["derived_from"]) == expected_mean_parents
        and len(fit["derived_from"]) == len(expected_mean_parents)
        for fit_id, fit in source_fits.items()
        if fit_id.startswith("piacente_1973_four_cell_mean_")
    )
