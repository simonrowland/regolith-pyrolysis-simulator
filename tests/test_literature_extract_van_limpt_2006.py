"""Rendered-cell and isolated-migration pins for van Limpt et al. (2006)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from simulator.battery.score import rail_for_quantity
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-limpt-2006-sodium-evaporation.yaml"


def test_van_limpt_2006_rendered_rows_and_isolated_migration():
    """The six Table II rows and prose rate summary stay tied to rendered pages."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sodium_rows = document["species"]["Na"]["observations"]
    activity = document["species"]["Na2O"]["observations"][0]
    balance = document["species"]["Na2O"]["observations"][1]
    points = balance["values"]["points"]
    assert all(
        row["regime"] == "transpiration"
        for row in (*sodium_rows, activity, balance)
    )
    assert "3446–3455" in document["source"]["citation"]

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
    sodium_1400, sodium_range = sodium_rows
    assert sodium_1400["values"]["rate_range_mol_m2_s_as_printed"] == [
        "6.7 × 10^-5",
        "7.4 × 10^-5",
    ]
    assert sodium_range["values"]["value_as_printed"] == "~1.2 × 10^-4"
    assert sodium_range["values"]["T_C_range"] == ["1450", "1460"]
    assert all(row["locator"]["page"] == 6 for row in sodium_rows)
    assert sodium_1400["values"]["no_total_experiment_pressure"] is True
    assert sodium_1400["point_conditions"]["oxygen_partial_pressure_bar_by_gas"]["state"]["value"] == {
        "N2_H2O": "<0.01",
        "air_H2O": "~0.16",
    }
    assert activity["locator"]["page"] == 7
    assert activity["values"]["value_as_printed"] == "about 10–15% lower"
    assert activity["values"]["reference"] == "original glass sample Na2O surface activity"
    assert "ICP-ES" in activity["derivation"]["relation"]
    assert "transpiration" in activity["derivation"]["relation"]
    for rate in sodium_rows:
        assert "ICP-ES" in rate["derivation"]["relation"]
        assert "transpiration" in rate["derivation"]["relation"]
    assert "ICP-ES" in balance["derivation"]["relation"]
    assert "transpiration" in balance["derivation"]["relation"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 9
    assert len(result.validation.hard_issues) == 0
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 9
    quantity_unknown = {
        observation_id.split("::", 1)[-1]
        for observation_id, row in result.observations.items()
        if not row.identity.quantity.is_value
    }
    assert quantity_unknown == {
        "van_limpt_2006_measured_total_na_evaporation_rate_at_1400C",
        "van_limpt_2006_measured_total_na_evaporation_rate_at_1450_1460C",
        "van_limpt_2006_p007_na2o_activity_relative_to_original_glass",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1689.15:h=a089432cc539",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1710.15:h=fbeae097ed7c",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1712.15:h=74031c625f45",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1627.15:h=fa5212c33379",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1727.15:h=545c14795633",
        "van_limpt_2006_p007_t02_sodium_disilicate_mass_balance::T=1629.15:h=f81e52ec9b4a",
    }
    unknown_rows = [
        row for row in result.observations.values()
        if not row.identity.quantity.is_value
    ]
    assert all(
        rail_for_quantity(None, species_formula=row.identity.species.formula) is None
        for row in unknown_rows
    )
    assert all(
        row.evidence.class_.value.value == "measured_reduced"
        for row in unknown_rows
    )
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
    rate_rows = [
        row for key, row in result.observations.items()
        if "van_limpt_2006_measured_total_na_evaporation_rate_at_" in key
    ]
    assert len(rate_rows) == 2
    assert all(row.locator.page == 6 for row in rate_rows)
    assert all(row.identity.composition.is_value for row in rate_rows)
    assert all(
        row.identity.total_pressure_Pa is None
        or not row.identity.total_pressure_Pa.is_value
        for row in rate_rows
    )
    assert all(
        row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        for row in rate_rows
    )
    rate_1400 = next(row for row in rate_rows if "1400C" in row.observation_id)
    assert rate_1400.identity.temperature_K.is_value
    assert rate_1400.identity.temperature_K.value == Decimal("1673.15")
    assert rate_1400.point_conditions["gas_mix"].state.value == "N2/H2O or air/H2O"
    assert rate_1400.point_conditions["gas_mix"].locator.page == 6
    rate_range = next(row for row in rate_rows if "1450_1460C" in row.observation_id)
    assert not rate_range.identity.temperature_K.is_value
    assert "[1723.15, 1733.15]" in rate_range.identity.temperature_K.reason
    assert all(row.value.kind.value == "unavailable" for row in rate_rows)
    assert all(row.evidence.class_.value.value == "measured_reduced" for row in rate_rows)
    assert all(
        rail_for_quantity(None, species_formula=row.identity.species.formula) is None
        for row in rate_rows
    )
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
        assert not row.identity.quantity.is_value
        assert rail_for_quantity(None, species_formula=row.identity.species.formula) is None
        assert row.evidence.class_.value.value == "measured_reduced"
    assert not activity["values"].get("T_C_range_as_printed")
    assert not activity["values"].get("duration_as_printed")


def test_van_limpt_2006_fidelity_pin_rejects_mutated_table_cell():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Na2O"]["observations"][1]["values"]["points"][0]["value"] = "113"

    errors = check_all_fidelity_samples_match(mutated, label="van-limpt-2006-sodium-evaporation")
    assert errors
