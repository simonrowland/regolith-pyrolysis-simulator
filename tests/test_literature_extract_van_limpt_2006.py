"""Rendered-cell and isolated-migration pins for van Limpt et al. (2006)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-limpt-2006-sodium-evaporation.yaml"


def test_van_limpt_2006_rendered_rows_and_isolated_migration():
    """The six Table II rows and prose rate summary stay tied to rendered pages."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sodium = document["species"]["Na"]["observations"][0]
    activity = document["species"]["Na2O"]["observations"][0]
    balance = document["species"]["Na2O"]["observations"][1]
    points = balance["values"]["points"]

    # Independent pins transcribed from PDF page 3452 (PDF page 7), Table II.
    # The page render was checked for all six row/column assignments.
    assert len(points) == 6
    assert [
        (
            point["T_C"],
            point["extractive_weight_loss_mg"],
            point["gravimetric_weight_loss_mg"],
        )
        for point in points
    ] == [
        ("1416", "112", "125"),
        ("1437", "146", "153"),
        ("1439", "166", "170"),
        ("1354", "69", "79"),
        ("1454", "188", "188"),
        ("1356", "85", "71"),
    ]
    assert balance["locator"] == {"page": 7, "table": "II"}
    ratios = document["species"]["Na2O"]["context"][0]["values"]["points"]
    assert [point["value"] for point in ratios] == [
        "0.90", "0.96", "0.98", "0.87", "1.00", "1.21"
    ]

    # PDF page 3451 (PDF page 6), Results V(1), prose values also checked in
    # the page render. Endpoint-to-atmosphere assignment is not stated.
    assert sodium["values"]["points"][0]["rate_range_mol_m2_s_as_printed"] == [
        "6.7 × 10^-5",
        "7.4 × 10^-5",
    ]
    assert sodium["values"]["points"][1]["value_as_printed"] == "~1.2 × 10^-4"
    assert sodium["locator"]["page"] == 6
    assert sodium["values"]["no_total_experiment_pressure"] is True
    assert sodium["values"]["points"][0]["point_conditions"]["oxygen_partial_pressure_bar_by_gas"]["state"]["value"] == {
        "N2_H2O": "<0.01",
        "air_H2O": "~0.16",
    }
    assert activity["locator"]["page"] == 7
    assert activity["values"]["value_as_printed"] == "about 10–15% lower"
    assert activity["values"]["reference"] == "original glass sample Na2O surface activity"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 8
    assert len(result.validation.hard_issues) == 0
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 8
    assert all(row.identity.quantity.is_value for row in result.observations.values())
    assert all(
        row.identity.species is not None and row.identity.species.phase.is_value
        for row in result.observations.values()
    )

    # Every migrated Table II identity binds its printed T and composition;
    # pressure and fO2 stay unknown because Table II does not print them.
    table_rows = [
        row for key, row in result.observations.items()
        if "van_limpt_2006_p007_t02" in key
    ]
    assert len(table_rows) == 6
    rate_row = next(
        row for key, row in result.observations.items()
        if "van_limpt_2006_measured_total_na_evaporation_rates" in key
    )
    assert rate_row.locator.page == 6
    assert rate_row.identity.composition.is_value
    assert rate_row.identity.total_pressure_Pa is None or not rate_row.identity.total_pressure_Pa.is_value
    assert rate_row.identity.fO2_Pa is None or not rate_row.identity.fO2_Pa.is_value
    assert table_rows[0].locator.page == 7
    assert table_rows[0].locator.table == "II"
    for row, temperature_c in zip(
        table_rows, ("1416", "1437", "1439", "1354", "1454", "1356")
    ):
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(temperature_c) + Decimal("273.15")
        assert row.identity.composition.is_value
        composition = row.identity.composition.value.as_map()
        assert composition["Na2O"] > 0
        assert composition["SiO2"] > composition["Na2O"]
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        assert row.value.kind.value == "unavailable"


def test_van_limpt_2006_fidelity_pin_rejects_mutated_table_cell():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Na2O"]["observations"][1]["values"]["points"][0]["value"] = "113"

    errors = check_all_fidelity_samples_match(mutated, label="van-limpt-2006-sodium-evaporation")
    assert errors
