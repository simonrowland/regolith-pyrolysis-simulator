"""Migration pins for Niggli (1913) equilibrium data."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery import factories as battery_factories


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/niggli-1913-silica-alkali-carbonate-equilibria.yaml"
)


def _migrate():
    migrator = battery_factories.migrate_one_extract(
        ROOT, EXTRACT, index={}, aliases={}
    )
    migrator.finalize()
    return migrator


def _context(document, species, observation_id):
    return next(
        item
        for item in document["species"][species]["context"]
        if item["observation_id"] == observation_id
    )


def test_render_pins_context_data_and_migrated_transition_points():
    """Pin printed context cells and all migrated melting-point observations."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = _migrate().result

    # PDF page 8 render: Table II column (a) is mol per cent of initial CO2
    # content lost, retained as context on that basis rather than sample mass.
    table_ii = _context(
        document,
        "K2CO3",
        "niggli_1913_table_ii_co2_displaced_schema_gap",
    )
    table_ii_points = table_ii["values"]["points"]
    assert len(table_ii_points) == 30  # 18 initial charges; 30 numeric cells in each measured column.
    assert all(
        "co2_loss_mol_percent_initial_as_printed" in point
        and "co2_displaced_mol_per_100_mol_sio2_as_printed" in point
        for point in table_ii_points
    )
    table_ii_by_key = {
        (point["row"], point["T_C"]): point
        for point in table_ii_points
    }
    assert table_ii_by_key[("I", "898")]["co2_loss_mol_percent_initial_as_printed"] == "6.6"
    assert table_ii_by_key[("VII", "998")]["co2_loss_mol_percent_initial_as_printed"] == "28.6"
    assert table_ii_by_key[("XIII", "956")]["co2_loss_mol_percent_initial_as_printed"] == "79.3"
    assert table_ii_by_key[("unnumbered-3", "998")]["co2_loss_mol_percent_initial_as_printed"] == "100.0"
    assert all(point["locator"]["page"] == 8 for point in table_ii_points)
    assert all(point["locator"]["table"] == "II" for point in table_ii_points)
    assert table_ii_by_key[("I", "898")]["SiO2_mol_percent_as_printed"] == "6.9"

    # PDF page 23 render: Table VI's displaced-mole values and the separate
    # 956 °C mol-percentage CO2-loss column remain context on their printed bases.
    table_vi = _context(
        document,
        "Na2CO3",
        "niggli_1913_table_vi_co2_displaced_schema_gap",
    )["values"]["points"]
    assert len(table_vi) == 15  # 14 printed mixture rows; 15 displaced-mole cells.
    table_vi_by_key = {(point["row"], point["T_C"]): point for point in table_vi}
    assert table_vi_by_key[("I", "898")]["co2_displaced_mol_per_100_mol_sio2_as_printed"] == "174"
    assert table_vi_by_key[("II", "956")]["co2_displaced_mol_per_100_mol_sio2_as_printed"] == "182"
    assert table_vi_by_key[("XII", "956")]["co2_displaced_mol_per_100_mol_sio2_as_printed"] == "134"
    expected_loss_percent = {
        ("II", "956"): "15.8",
        ("III", "956"): "19.1",
        ("V", "956"): "22.8",
        ("VII", "956"): "41.7",
        ("VIII", "956"): "42.6",
        ("IX", "956"): "48.6",
        ("X", "956"): "50.0",
        ("XII", "956"): "66.4",
    }
    assert {
        key: point["co2_loss_mol_percent_initial_as_printed"]
        for key, point in table_vi_by_key.items()
        if "co2_loss_mol_percent_initial_as_printed" in point
    } == expected_loss_percent
    assert "co2_loss_mol_percent_initial_as_printed" not in table_vi_by_key[("XI", "956")]
    assert all(point["locator"]["page"] == 23 for point in table_vi)
    assert all(point["locator"]["table"] == "VI" for point in table_vi)
    assert table_vi_by_key[("I", "898")]["SiO2_mol_percent_as_printed"] == "7.1"

    # PDF page 33 render: historical comparisons and Mallard retain their rows.
    table_viii_k = _context(
        document,
        "K2CO3",
        "niggli_1913_table_viii_k_comparison",
    )
    table_viii_na = _context(
        document,
        "Na2CO3",
        "niggli_1913_table_viii_na_comparison",
    )
    mallard = _context(
        document,
        "Na2CO3",
        "niggli_1913_mallard_soda_displacement",
    )
    assert len(table_viii_k["values"]["rows"]) == 6
    assert table_viii_k["values"]["rows"][0] == ["3.0", "114", "96", "129"]
    assert table_viii_k["values"]["rows"][-1] == ["60.0", "61", "61.5", None]
    assert len(table_viii_na["values"]["rows"]) == 7
    assert table_viii_na["values"]["rows"][0] == ["3.0", None, "193", "197"]
    assert table_viii_na["values"]["rows"][-1] == [
        "60",
        "All displaced",
        "All displaced",
        "All displaced",
    ]
    assert len(mallard["values"]["rows"]) == 2
    assert mallard["values"]["rows"][0] == ["20.0", "107", "129", "148"]

    k2si2o5_estimate = document["species"]["K2Si2O5"]["observations"][0]
    assert k2si2o5_estimate["values"]["method_class"] == "author_estimate"
    assert "1005 °C" in k2si2o5_estimate["values"]["model"]
    assert "1025 °C" in k2si2o5_estimate["values"]["model"]
    assert [sample["value"] for sample in document["fidelity_samples"]] == [
        "6.6",
        "28.6",
        "79.3",
    ]
    assert all(
        sample["path"].startswith(
            "species.K2CO3.context[niggli_1913_table_ii_co2_displaced_schema_gap]"
        )
        for sample in document["fidelity_samples"]
    )

    unavailable = sum(
        row.value.kind.value == "unavailable"
        for row in result.observations.values()
    )
    assert len(result.observations) == 10  # The 10 melting-point observations; CO2 cells remain context.
    assert unavailable == 0
    assert not result.validation.hard_issues
    assert not any(row.identity.quantity.is_unknown for row in result.observations.values())
    assert not any(row.identity.species.phase.is_unknown for row in result.observations.values())
    assert all(
        row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator.page == row.locator.page
        for row in result.observations.values()
    )

    # PDF pages 18 and 25: estimates and quoted melting points retain their locators.
    k2si2o5_melt = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith("::niggli_1913_k2si2o5_melting_point")
    )
    assert k2si2o5_melt.value.point == Decimal("1288.15")
    assert k2si2o5_melt.identity.quantity.value.value == "transition_temperature"
    assert k2si2o5_melt.identity.species.phase.value.value == "cr"
    assert k2si2o5_melt.locator.page == 18
    assert k2si2o5_melt.point_conditions["temperature_K"].locator.page == 18
    assert k2si2o5_melt.identity.total_pressure_Pa.is_unknown
    assert "total_pressure_Pa" not in k2si2o5_melt.point_conditions
    assert k2si2o5_melt.point_conditions["fO2_Pa"].state.is_unknown

    na2co3_melt = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith("::niggli_1913_huttner_tammann_na2co3_melting_point")
    )
    na2sio3_melt = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith("::niggli_1913_wallace_na2sio3_melting_point")
    )
    assert na2co3_melt.value.point == Decimal("1126.15")
    assert na2sio3_melt.value.point == Decimal("1291.15")
    assert na2co3_melt.locator.page == na2sio3_melt.locator.page == 25
    assert na2co3_melt.point_conditions["temperature_K"].locator.page == 25
