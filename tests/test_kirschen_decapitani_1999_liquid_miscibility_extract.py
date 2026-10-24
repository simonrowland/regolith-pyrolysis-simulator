"""Migration pins for Kirschen and DeCapitani (1999), Tables 1 and 2."""

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kirschen-decapitani-1999-liquid-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _migrated_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    raw_rows = {
        f"{SOURCE_ID}::{row['observation_id']}": (formula, row)
        for formula, species in document["species"].items()
        for row in species.get("observations", [])
    }
    assert len(raw_rows) == len(result.observations) == 576
    refused_ids = {
        observation_id
        for observation_id, (_formula, row) in raw_rows.items()
        if row["values"].get("status") == "typed_refusal"
    }
    queued_ids = {entry.observation_id for entry in result.queue}
    assert queued_ids == refused_ids
    assert all(entry.axes == ["value"] for entry in result.queue)
    assert all("no mapped equilibrium_phase_composition field selected" in entry.why for entry in result.queue)
    point_count = unavailable_count = 0
    for observation_id, observation in result.observations.items():
        formula, raw = raw_rows[observation_id]
        values = raw["values"]
        assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
        assert observation.identity.species.formula == values["formula"]
        assert observation.identity.species.phase.value.value == "glass"
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value is not None
        assert observation.point_conditions["temperature_K"].state.value == observation.identity.temperature_K.value
        assert observation.point_conditions["temperature_K"].locator.page == raw["locator"]["page"]
        assert observation.point_conditions["temperature_K"].locator.pdf_page_index == raw["locator"]["page"] - 1
        assert observation.point_conditions["gas_mix"].state.value == "air"
        assert observation.point_conditions["gas_mix"].locator.page == 1
        assert values["pressure_as_printed"] == "ambient pressure"
        assert values["pressure_locator"]["page"] == 1
        assert "no numeric total pressure" in values["condition_note"]
        assert values["sample_id_as_printed"]
        assert values["system_as_printed"]
        assert values["assemblage_id"]
        assert values["phase_assemblage_as_printed"]
        assert values["basis"] == "wt_percent"

        if values.get("status") == "typed_refusal":
            unavailable_count += 1
            assert values["admission_status"] == "typed_refusal"
            assert "ellipsis" in values["reason"]
            assert values["printed_cell_as_published"] == "..."
            assert observation.value.kind.value == "unavailable"
        else:
            point_count += 1
            amount = values["component_amount"]
            assert observation.value.kind.value == "point"
            assert Decimal(str(observation.value.point)) == Decimal(str(amount["value"]))
            assert amount["sigma"] not in (None, "", "0", "0.0")
            assert Decimal(str(raw["uncertainty"]["sigma"])) == Decimal(str(amount["sigma"]))
            assert not observation.notices
    assert point_count == 466
    assert unavailable_count == 110
    assert len(result.experiments) == len(document["experiments"]) == 74
    return document, result


def test_all_table_cells_migrate_with_species_temperature_and_atmosphere() -> None:
    _migrated_rows()


def test_render_transcribed_stmc_grids_and_printed_precision() -> None:
    document, result = _migrated_rows()
    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    # Independently transcribed from the 600 dpi article renders: Table 1, PDF p. 14;
    # Table 2, PDF p. 15. Values include printed one-sigma uncertainties.
    grid = {
        (1, "STMC 4-1", 1, 1610): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("10.38", "0.86"),
            "TiO2": ("88.50", "1.33"),
        },
        (1, "STMC 4-1", 2, 1610): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("55.57", "1.13"),
            "TiO2": ("30.74", "0.53"),
        },
        (1, "STMC 4-2", 1, 1551): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("88.50", "1.33"),
            "TiO2": ("10.38", "0.86"),
        },
        (1, "STMC 4-2", 2, 1551): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("30.74", "0.53"),
            "TiO2": ("55.57", "1.13"),
        },
        (2, "STMC 4-1H", 1, 1890): {
            "CaO": ("5.91", "0.12"),
            "MgO": ("3.86", "0.09"),
            "SiO2": ("45.30", "0.40"),
            "TiO2": ("45.56", "0.52"),
        },
    }
    for (table, sample, phase, temperature), components in grid.items():
        page = 14 if table == 1 else 15
        for formula, (amount, sigma) in components.items():
            row_id = f"kirschen_decapitani_1999_t{table}_{sample.lower().replace(' ', '_').replace('-', '_')}_phase{phase}_{formula.lower()}"
            row = source_rows[row_id]
            observation = result.observations[f"{SOURCE_ID}::{row_id}"]
            assert row["values"]["component_amount"] == {"value": amount, "sigma": sigma}
            assert observation.value.point == Decimal(amount)
            assert Decimal(str(observation.identity.temperature_K.value)) == Decimal(temperature) + Decimal("273.15")
            assert observation.locator.page == page
            assert observation.locator.pdf_page_index == page - 1
            assert observation.identity.species.formula == formula
            assert observation.point_conditions["gas_mix"].state.value == "air"
    # Preserve the scan's two-decimal precision even though the value equals 0.4.
    laser_sigma = source_rows[
        "kirschen_decapitani_1999_t2_stmc_4_1h_phase1_sio2"
    ]["values"]["component_amount"]["sigma"]
    assert laser_sigma == "0.40"


def test_unavailable_cells_are_only_the_printed_ellipses() -> None:
    document, result = _migrated_rows()
    unavailable = [
        row
        for row in document["species"]["SiO2"]["observations"]
        if row["values"].get("status") == "typed_refusal"
    ]
    assert len(unavailable) == 110
    assert all(row["values"]["printed_cell_as_published"] == "..." for row in unavailable)
    assert all(
        result.observations[f"{SOURCE_ID}::{row['observation_id']}"].value.kind.value == "unavailable"
        for row in unavailable
    )
