from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "sugawara-2024-na-silicate-molybdate-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"

# Each pair is the rendered PDF's SL/ML mol% for SiO2, Na2O, and MoO3.
TABLE2 = {
    ("S50-15", "1200"): (("51.01", "11.53"), ("40.37", "48.50"), ("8.62", "39.97")),
    ("S55-15", "1200"): (("62.01", "2.33"), ("33.86", "47.84"), ("4.12", "49.84")),
    ("S62-15", "1200"): (("71.12", "1.43"), ("26.53", "48.96"), ("2.35", "49.61")),
    ("S70-15", "1200"): (("78.49", "3.76"), ("19.45", "46.95"), ("2.06", "49.29")),
    ("S50-15", "1000"): (("58.61", "3.01"), ("37.99", "49.46"), ("3.40", "47.53")),
    ("S55-15", "1000"): (("66.18", "0.62"), ("32.09", "48.67"), ("1.74", "50.71")),
    ("S62-15", "1000"): (("73.64", "2.28"), ("25.21", "49.69"), ("1.15", "48.03")),
    ("S70-15", "1000"): (("76.66", "1.39"), ("22.23", "50.67"), ("1.11", "47.94")),
}
OXIDES = ("SiO2", "Na2O", "MoO3")
PHASES = ("glass", "melt")


def test_render_verified_tables_emit_all_table2_tie_line_cells() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    context = species["SiO2"]["context"]
    rows = {row["observation_id"]: row for row in context}
    table1 = rows["sugawara_2024_table1_experimental_compositions_and_run_products"]
    table2 = rows["sugawara_2024_table2_phase_proportions_and_quenched_compositions"]
    table4 = rows["sugawara_2024_table4_calphad_interaction_parameters"]

    assert len(table1["values"]["rows_as_printed"]) == table1["values"]["printed_row_count"] == 11
    mass_rows = table2["values"]["rows_as_printed"]
    assert len(mass_rows) == table2["values"]["printed_row_count"] == 8
    assert all(len(row) == 6 for row in mass_rows)
    assert [row[4] for row in mass_rows] == ["", "", "", "6.6", "", "", "", "19.7"]
    assert len(table4["values"]["rows_as_printed"]) == table4["values"]["printed_row_count"] == 6

    # Independent render pins include Table 1 and Table 4 in addition to all 48 Table 2 cells.
    assert table1["values"]["rows_as_printed"][0][1:4] == ["43.50", "43.50", "13.00"]
    assert table1["locator"]["page"] == 2 and table1["locator"]["table"] == "1"
    assert table4["values"]["rows_as_printed"][0] == ["Mo2O6-Na2MoO4", "-161,000+72 T", "10,000", "0"]
    assert table4["values"]["rows_as_printed"][4] == ["Na4Si2O6-Na2MoO4", "12,000", "0", "10,000"]
    assert table4["locator"]["page"] == 6 and table4["locator"]["table"] == "4"

    actual = {}
    for oxide in OXIDES:
        for observation in species[oxide]["observations"]:
            values = observation["values"]
            locator = observation["locator"]
            key = (locator["sample"], locator["temperature_C"], oxide, values["phase_label"])
            actual[key] = (
                values["component_amount"],
                values["component_amount_as_published"].removesuffix(" mol%"),
                locator["page"],
                locator["table"],
            )

    expected = {}
    for (sample, temperature), oxide_pairs in TABLE2.items():
        for oxide, pair in zip(OXIDES, oxide_pairs, strict=True):
            for phase, printed in zip(PHASES, pair, strict=True):
                expected[(sample, temperature, oxide, phase)] = (
                    str(Decimal(printed) / Decimal(100)), printed, 4, "2"
                )
    assert actual == expected
    assert len(actual) == 48
    assert not table2["values"].get("schema_gap")

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.queue
    assert not result.validation.hard_issues
    assert len(result.observations) == 48
    assert sum(len(work_context) for work_context in result.context_by_work.values()) == 4

    for (sample, temperature, oxide, phase), (amount, _, _, _) in expected.items():
        phase_short = "sl" if phase == "glass" else "ml"
        observation_id = (
            f"{SOURCE_ID}::sugawara_2024_table2_{sample.lower().replace('-', '_')}_"
            f"{temperature}c_{phase_short}_{oxide.lower()}_mole_fraction"
        )
        observation = result.observations[observation_id]
        assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
        assert observation.identity.species.formula == oxide
        assert observation.identity.phase_role.value.value == phase
        assert observation.identity.subtype.value == "mole_fraction"
        assert observation.value.point == Decimal(amount)

    first_id = f"{SOURCE_ID}::sugawara_2024_table2_s50_15_1200c_sl_sio2_mole_fraction"
    first = result.observations[first_id]
    assert first.identity.quantity.value.value == "equilibrium_phase_composition"
    assert first.identity.species.formula == "SiO2"
    assert first.identity.species.phase.value.value == "glass"
    assert first.identity.phase_role.value.value == "glass"
    assert first.identity.subtype.value == "mole_fraction"
    assert first.value.point == Decimal("0.5101")
    assert first.identity.temperature_K.value == Decimal("1473.15")
    assert first.identity.total_pressure_Pa.is_unknown
    assert first.identity.fO2_Pa.is_unknown
