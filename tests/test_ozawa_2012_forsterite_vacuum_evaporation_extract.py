"""Independent pins for Ozawa et al. (2012) printed tables and migration."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ozawa-2012-forsterite-vacuum-evaporation.yaml"

# Table 3a transcribed independently from the 600-dpi render of physical page 13.
# Each cell is (rate, printed uncertainty, marker); the three cells are (100),
# (010), and (001), in that order.
TABLE3_RENDER = [
    ("Fo-11", "1504", 7, [("2.9e-11", "1.4e-11", "*"), ("3.1e-11", "0.9e-11", ""), ("7.9e-11", "2.1e-11", "*")]),
    ("Fo-11", "1598", 7, [("4.0e-10", "0.4e-10", "*"), ("1.8e-10", "0.3e-10", "*"), ("6.3e-10", "0.6e-10", "*")]),
    ("Fo-11", "1692", 16, [("2.7e-9", "0.2e-9", "*"), ("1.3e-9", "0.1e-9", "*"), ("4.8e-9", "0.1e-9", "")]),
    ("Fo-11", "1786", 5, [("2.6e-8", "0.08e-8", "†"), ("2.1e-8", "0.13e-8", "†"), ("2.9e-8", "0.2e-8", "†")]),
    ("Fo-16", "1504", 4, [("4.7e-11", "2.4e-11", "*"), ("3.0e-11", "0.5e-11", ""), ("7.5e-11", "0.8e-11", "*")]),
    ("Fo-16", "1598", 5, [("4.0e-10", "0.4e-10", "*"), ("2.6e-10", "0.2e-10", "*"), ("6.8e-10", "0.1e-10", "")]),
    ("Fo-16", "1645", 3, [("1.6e-9", "0.2e-9", ""), ("7.7e-10", "0.2e-10", ""), ("1.8e-9", "0.6e-9", "")]),
    ("Fo-16", "1692", 5, [("4.3e-9", "0.4e-9", ""), ("1.7e-9", "0.2e-9", ""), ("4.9e-9", "0.2e-9", "*")]),
    ("Fo-16", "1739", 3, [("1.1e-8", "0.13e-8", ""), ("4.0e-9", "0.4e-9", ""), ("1.3e-8", "0.2e-8", "")]),
    ("Fo-16", "1786", 5, [("2.9e-8", "0.07e-8", "†"), ("2.4e-8", "0.08e-8", "†"), ("2.7e-8", "0.1e-8", "†")]),
    ("Fo-17", "1735", 3, [("1.3e-8", "0.07e-8", ""), ("4.1e-9", "0.5e-9", ""), ("1.6e-8", "0.01e-8", "")]),
    ("Fo-17", "1750", 3, [("2.1e-8", "0.09e-8", ""), ("9.1e-9", "0.6e-9", ""), ("2.1e-8", "0.02e-8", "")]),
    ("Fo-17", "1770", 9, [("2.8e-8", "0.2e-8", ""), ("1.6e-8", "0.3e-8", ""), ("2.9e-8", "0.2e-8", "")]),
    ("Fo-17", "1810", 3, [("4.9e-8", "0.1e-8", ""), ("3.3e-8", "0.06e-8", ""), ("4.2e-8", "0.1e-8", "")]),
]
SURFACES = ("(100)", "(010)", "(001)")


def assert_table3_matches_render(extract: dict, rendered: list | None = None) -> None:
    rendered = TABLE3_RENDER if rendered is None else rendered
    observations = extract["species"]["forsterite"]["observations"]
    rate_points = {}
    for row in observations:
        if row.get("observation_id") not in {
            "ozawa_2012_table3a_nominal_rates_authors_measurements",
            "ozawa_2012_table3a_nominal_rates_quoted_yamada_2006",
        }:
            continue
        for point in row["values"]["series"]:
            rate_points[(point["sample_as_printed"], point["T_C"], point["surface"])] = (
                point,
                row["values"]["method_class"],
            )

    assert len(rate_points) == 42
    assert len(rendered) == 14
    for sample, temperature, experiments_n, cells in rendered:
        assert len(cells) == len(SURFACES)
        for surface, (rate, uncertainty, marker) in zip(SURFACES, cells, strict=True):
            point, method_class = rate_points[(sample, temperature, surface)]
            assert point["evaporation_rate"] == rate
            assert point["uncertainty"] == {"evaporation_rate": uncertainty}
            assert point["experiments_n"] == str(experiments_n)
            assert point["rate_as_printed"].endswith(marker)
            expected_class = "quoted_attributed" if marker == "*" else "measured_reduced"
            assert method_class == expected_class
            assert point["locator"]["page"] == 13
            assert point["locator"]["pdf_page_index"] == 12
            assert point["locator"]["row"] == f"{sample} {temperature}"
            assert point["locator"]["column"] == surface


def test_table3a_rendered_grid_and_cell_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert_table3_matches_render(extract)

    changed_render = deepcopy(TABLE3_RENDER)
    changed_render[0][3][0] = ("9.9e-11", "1.4e-11", "*")
    with pytest.raises(AssertionError):
        assert_table3_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    own = next(
        row for row in changed_extract["species"]["forsterite"]["observations"]
        if row.get("observation_id") == "ozawa_2012_table3a_nominal_rates_authors_measurements"
    )
    first_cell = next(
        point for point in own["values"]["series"]
        if (point["sample_as_printed"], point["T_C"], point["surface"]) == ("Fo-11", "1504", "(010)")
    )
    first_cell["evaporation_rate"] = "9.9e-11"
    with pytest.raises(AssertionError):
        assert_table3_matches_render(changed_extract)


def test_isolated_migration_preserves_species_temperature_and_conditions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = next(
        row for row in extract["species"]["forsterite"]["observations"]
        if row.get("observation_id") == "ozawa_2012_table1_mass_loss_fraction"
    )["values"]["series"]
    assert len(table1) == 86
    assert {point["sample_as_printed"] for point in table1} == {"Fo-11", "Fo-16", "Fo-17"}
    assert all(point["surface"] and point["T_C"] and point["duration_h"] for point in table1)
    assert all(point["point_conditions"]["composition"]["state"]["tag"] == "unknown" for point in table1)
    assert all(point["point_conditions"]["composition"]["locator"] for point in table1)
    comment_rows = {point["run_id_as_printed"].rstrip("†"): point.get("comment_as_printed") for point in table1}
    assert comment_rows["911"] == "melt present, optical observation"
    assert comment_rows["937"] == "melt present (trace)"
    assert comment_rows["829A"] == "“"

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 134
    counts = Counter(row.identity.quantity.value for row in observations)
    assert counts == Counter({
        Quantity.MASS_LOSS_FRACTION: 86,
        Quantity.EVAPORATION_RATE: 42,
        Quantity.ISOTOPE_DELTA: 6,
    })
    expected_species = {
        Quantity.MASS_LOSS_FRACTION: "Mg2SiO4",
        Quantity.EVAPORATION_RATE: "Mg2SiO4",
        Quantity.ISOTOPE_DELTA: "Mg",
    }
    for row in observations:
        quantity = row.identity.quantity.value
        assert row.identity.species.formula == expected_species[quantity]
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.value > Decimal("273.15")
        assert row.locator.page == row.locator.pdf_page_index + 1
        conditions = row.point_conditions or {}
        assert "temperature_K" in conditions
        assert "total_pressure_Pa" in conditions
        assert "fO2_Pa" in conditions
        assert all(condition.locator is not None for condition in conditions.values())
        assert conditions["temperature_K"].state.is_value

    mass_loss_observations = [row for row in observations if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION]
    assert all(
        row.locator.table == "1" and "row=" in (row.locator.note or "")
        for row in mass_loss_observations
    )
    assert all(row.point_conditions["total_pressure_Pa"].locator.table == "1" for row in mass_loss_observations)

