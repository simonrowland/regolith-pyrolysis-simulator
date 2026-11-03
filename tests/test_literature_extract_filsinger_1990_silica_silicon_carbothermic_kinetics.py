from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
SID = "filsinger-1990-silica-silicon-carbothermic-kinetics"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"

TABLE_II = (
    ("SiO2/C", "1/1", "1300", "11% β-SiC; 5% SiO2", "1720", "<11", "None"),
    ("SiO2/C", "1/2", "1300", "27% β-SiC; 5% SiO2; 3% C", "1730", "30", "97% β-SiC; 2% Si"),
    ("SiO2/C", "1/3", "1400", "", "", "42", "73% β-SiC; 12% C"),
    ("SiO2/SiC", "1/1", "1500", "", "", "29", "42% Si; 36% β-SiC"),
    ("SiO2/SiC", "1/2", "1550", "", "", "50", "71% β-SiC; 29% Si"),
    ("SiO2/SiC", "2/1", "1520", "", "", "8", ""),
    ("SiO/SiC (lid)", "1/1", "1500", "", "", "72", "92% β-SiC; 4% Si"),
    ("SiO/SiC (lid)", "2.8/1", "1500", "20% Si; 8% TaSi2; 4% β-SiC", "1800", "22", ">90% Si"),
    ("SiO/C", "1/1", "1470", "22% β-SiC; 3% SiC", "1720", "42", "70% β-SiC; 19% Si"),
    ("SiO/C", "1/2", "1450", "", "", "40", "75% SiC; β:α/3:1; 2% C"),
)

TABLE_III = (
    ("SiO2 + 3C → SiC + 2CO", "Open", "1733–1933", "-4.68", "18.82", "390", "108"),
    ("2SiO2 + SiC → 3SiO + CO", "Open", "1833–2033", "-4.18", "17.55", "348", "106"),
    ("SiO + 2C → SiC + CO", "0.4 mm", "1733–1933", "-5.69", "23.76", "472", "1010"),
    ("(2.8)SiO + SiC → 2Si + CO", "0.4 mm", "1963–2073", "-6.10", "24.42", "505", "1010"),
    ("(2.8)SiO + SiC → 2Si + CO", "Loose lid", "1998–2073", "-6.99", "25.10", "505", "1011"),
    ("SiO(s) → SiO(g)", "0.4 mm", "1833–2033", "-4.06", "13.79", "340", "106"),
    ("SiO(s) → SiO(g)", "Open", "1683–1833", "-3.68", "14.34", "305", "106"),
    ("SiO2 + Si → 2SiO", "Open", "1683–1883", "-4.18", "16.95", "348", "107"),
)

FIGURE_6_LEGEND = (
    "A: SiO(s) → SiO(g)",
    "B: SiO2 + Si → 2SiO",
    "C: SiO2 + 3C → SiC + 2CO",
    "D: SiO + 2C → SiC + CO (lid)",
    "E: SiO + SiC → 2Si + CO (loose lid)",
    "F: SiO + SiC → 2Si + CO (tight lid)",
    "G: 2SiO2 + SiC → 3SiO + CO",
    "H: SiO(s) → SiO(g) (lid)",
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _all_observations(document: dict) -> list[dict]:
    return [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]


def _table_rows(document: dict, table: str) -> list[dict]:
    return [
        observation
        for observation in _all_observations(document)
        if observation["locator"].get("table") == table
    ]


def test_tables_ii_and_iii_have_every_render_checked_row_and_cell() -> None:
    document = _document()
    assert vle.validate_extract_document(document, expected_source_id=SID) == []
    table_i = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "filsinger90_table_i_reagents"
    )
    assert table_i["values"]["printed_row_count"] == 6 == len(table_i["values"]["rows"])
    table_ii = sorted(_table_rows(document, "II"), key=lambda row: row["source_row_number"])
    table_iii = sorted(_table_rows(document, "III"), key=lambda row: row["source_row_number"])

    assert len(table_ii) == 10 == len(TABLE_II)
    assert len(table_iii) == 8 == len(TABLE_III)
    for row, expected in zip(table_ii, TABLE_II):
        values = row["values"]
        assert (
            values["reactants_as_printed"],
            values["mole_ratio_as_printed"],
            values["initial_weight_loss_temperature_C"],
            values["intermediate_material_as_printed"],
            values["intermediate_temperature_C"],
            values["value"],
            values["final_residue_xrd_as_printed"],
        ) == expected
        assert row["locator"]["page"] == 4 and row["locator"]["table"] == "II"
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["point_conditions"]["temperature_C"]["state"]["value"] == "2000"
        assert row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert row["point_conditions"]["reactant_ratio_as_printed"]["state"]["value"] == (
            f"{expected[0]}: {expected[1]}"
        )

    for row, expected in zip(table_iii, TABLE_III):
        values = row["values"]
        assert (
            values["reaction_as_printed"],
            values["crucible_vent_as_printed"],
            values["temperature_range_as_printed"],
            values["A"],
            values["B"],
            values["activation_energy_kJ_per_mol"],
            values["preexponential_factor_as_printed"],
        ) == expected
        assert row["locator"]["page"] == 7 and row["locator"]["table"] == "III"
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["values"]["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        raw_locator = row["values"]["derivation"]["raw_inputs"]["locator"]
        assert (raw_locator["page"], raw_locator["figure"]) == (6, "3, 4, 6")
        printed_z = expected[6]
        likely_power = f"10^{printed_z[2:]}"
        assert values["preexponential_factor_as_printed"] == printed_z
        assert f"likely {likely_power} with the exponent set on the baseline; not certain" in row["note"]
        assert row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert row["T_range_K"] == [int(expected[2].split("–")[0]), int(expected[2].split("–")[1])]

    assert all(row["point_conditions"]["gas_mix"]["state"]["value"] == "helium with <0.5 ppm oxygen" for row in table_ii + table_iii)
    assert all("CO/Ar" not in row["values"].get("gas_mix_as_printed", "") for row in table_ii + table_iii)


def test_figure_six_legend_is_bound_to_printed_series_and_mutation_fails() -> None:
    document = _document()
    legend_row = next(
        row
        for row in document["species"]["SiO"]["context"]
        if row["observation_id"] == "filsinger90_figure6_legend"
    )
    assert tuple(series["label_as_printed"] for series in legend_row["values"]["series"]) == FIGURE_6_LEGEND
    figure3 = next(row for row in document["species"]["CO"]["context"] if row["observation_id"] == "filsinger90_figure3_weight_loss")
    figure4 = next(row for row in document["species"]["CO"]["context"] if row["observation_id"] == "filsinger90_figure4_log_weight")
    expected_temperatures = ["1460°C", "1560°C", "1610°C", "1660°C"]
    assert figure3["values"]["series"][0]["temperature_labels_as_printed"] == expected_temperatures
    assert figure4["values"]["series"][0]["temperature_labels_as_printed"] == expected_temperatures
    assert vle.check_all_fidelity_samples_match(document, label="filsinger90") == []

    mutated = deepcopy(document)
    mutated["species"]["SiO"]["context"][-1]["values"]["series"][0]["label_as_printed"] = "A: altered legend"
    errors = vle.check_all_fidelity_samples_match(mutated, label="filsinger90-mutated")
    assert any("mismatch" in error for error in errors)


def test_prose_rate_is_pinned_to_the_page_three_render() -> None:
    document = _document()
    rate = next(
        row
        for row in document["species"]["CO"]["observations"]
        if row["observation_id"] == "filsinger90_p3_bn_crucible_rate_1725c"
    )
    assert rate["values"]["value"] == "49.2 × 10⁻⁴"
    assert rate["values"]["T_C"] == "1725"
    assert rate["point_conditions"]["crucible_material_as_printed"]["state"]["value"] == (
        "boron nitride; rate compared with tantalum crucibles"
    )
    assert rate["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert rate["values"]["derivation"]["raw_inputs"]["locator"]["page"] == 3
    assert vle.check_all_fidelity_samples_match(document, label="filsinger90-prose-rate") == []


def test_isolated_migration_keeps_conditions_and_only_declared_quantity_gaps() -> None:
    document = _document()
    source_rows = _all_observations(document)
    assert len(source_rows) == 19

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
        if row.observation_id.rsplit("::", 1)[-1]
        in {source["observation_id"] for source in source_rows}
    }
    assert set(migrated) == {source["observation_id"] for source in source_rows}
    assert len(migrated) == 19

    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == {source["observation_id"] for source in source_rows}
    phase_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species is None or not row.identity.species.phase.is_value
    }
    assert phase_unknown == set()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 19

    for source in source_rows:
        row = migrated[source["observation_id"]]
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["gas_mix"].state.value == "helium with <0.5 ppm oxygen"
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown
        assert row.identity.composition is not None and row.identity.composition.is_value
        if source["locator"].get("table") == "II":
            assert row.point_conditions["temperature_C"].state.value == Decimal("2000")
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == Decimal("2273.15")
        elif source["locator"].get("table") == "III":
            assert row.point_conditions["temperature_range_K"].state.is_value
            assert row.point_conditions["temperature_K"].state.is_unknown
        else:
            assert row.point_conditions["temperature_C"].state.value == Decimal("1725")
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == Decimal("1998.15")
