"""Printed thermochemistry and solid-phase compositions for Itoh & Azakami (1989)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.records import ValueKind


EXTRACT = REPO_ROOT / "data/literature/extracts/itoh-1989-zno-solid-zinc-silicate.yaml"
ACTIVITIES = {
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r03_c02": ("ZnO", "0.995", "973"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r03_c03": ("SiO2", "0.024", "973"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r03_c04": ("ZnO", "0.154", "973"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r03_c05": ("SiO2", "1.0", "973"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r04_c02": ("ZnO", "0.996", "1073"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r04_c03": ("SiO2", "0.041", "1073"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r04_c04": ("ZnO", "0.202", "1073"),
    "itoh_1989_zno_solid_zinc_silicate_p006_t02_r04_c05": ("SiO2", "1.0", "1073"),
}
TABLE1_ADDITIONS = {
    "itoh_1989_table1_cu2o_present_work_2nd_law": "-172.1",
    "itoh_1989_table1_cu2o_present_work_3rd_law": "-169.0",
    "itoh_1989_table1_cu2o_incra": "-170.7",
    "itoh_1989_table1_cu2o_elliott": "-174.9",
}


def _migrate(path: Path):
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _assert_printed_values(document: dict, migrated) -> None:
    rows = {
        row.observation_id.split("::", 1)[1]: row
        for row in migrated.observations.values()
        if row.source_id == document["source_id"]
    }
    assert len(rows) == 67
    assert not migrated.validation.hard_issues
    assert not migrated.registry_issues
    for observation_id, (formula, printed, temperature) in ACTIVITIES.items():
        row = rows[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(printed)
        assert row.identity.species.formula == formula
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.point_conditions["phase_assemblage"].state.tag.value == "value"
    for observation_id, printed in TABLE1_ADDITIONS.items():
        row = rows[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(printed)
        assert row.identity.species.formula == "Cu2O"
        assert row.identity.temperature_K.value == Decimal("298.15")
    for observation_id, row in rows.items():
        assert row.value.kind is ValueKind.POINT
        assert row.identity.species.formula in document["species"]
        if observation_id in ACTIVITIES:
            assert row.identity.temperature_K.value in {Decimal("973"), Decimal("1073")}
        elif observation_id.startswith(("itoh_1989_table_3_", "itoh_1989_table_4_", "itoh_1989_table_5_")):
            assert row.identity.temperature_K.value == Decimal("1073")
        else:
            assert row.identity.temperature_K.value == Decimal("298.15")
    # Independent render pins for Tables 3–5 and Table 2.
    assert rows["itoh_1989_table_3_M1_zno_composition"].value.point == Decimal("99.75")
    assert rows["itoh_1989_table_4_1_zno_composition"].value.point == Decimal("99.78")
    assert rows["itoh_1989_table_5_1a_zno_composition"].value.point == Decimal("99.07")
    assert rows["itoh_1989_table_5_5b_mgo_composition"].value.point == Decimal("57.16")
    assert rows["itoh_1989_table2_zno_2ndlaw_air"].value.point == Decimal("-346.8")


def test_activities_and_table_compositions_migrate_as_printed_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_printed_values(document, _migrate(EXTRACT))


def test_activity_cell_mutation_is_detected(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["species"]["ZnO"]["observations"]
        if row["observation_id"] == "itoh_1989_zno_solid_zinc_silicate_p006_t02_r03_c02"
    )
    target["values"]["activity"] = "0.994"
    mutated = tmp_path / EXTRACT.name
    mutated.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_printed_values(document, _migrate(mutated))

TABLE5_RENDER_GRID = {
    "1a": {"ZnO": "99.07", "SiO2": "0.93", "MgO": None},
    "1b": {"ZnO": "64.49", "SiO2": "34.49", "MgO": "1.02"},
    "2a": {"ZnO": "100", "SiO2": None, "MgO": None},
    "2b": {"ZnO": "61.02", "SiO2": "32.93", "MgO": "6.05"},
    "3a": {"ZnO": "97.40", "SiO2": "0.58", "MgO": "2.02"},
    "3b": {"ZnO": "49.33", "SiO2": "34.29", "MgO": "16.38"},
    "4a": {"ZnO": "96.41", "SiO2": "0.98", "MgO": "2.61"},
    "4b": {"ZnO": "42.83", "SiO2": "33.26", "MgO": "23.91"},
    "5a": {"ZnO": "91.75", "SiO2": "0.95", "MgO": "7.30"},
    "5b": {"ZnO": "13.11", "SiO2": "29.73", "MgO": "57.16"},
    "6a": {"ZnO": "91.66", "SiO2": None, "MgO": "8.34"},
    "6b": {"ZnO": "34.90", "SiO2": None, "MgO": "65.10"},
}


def _assert_table5_grid(document: dict, expected_grid: dict) -> None:
    actual = {}
    for formula, body in document["species"].items():
        for row in body.get("observations", []):
            if row.get("locator", {}).get("table") != "5":
                continue
            sample = row["values"]["sample_id_as_published"]
            actual.setdefault(sample, {})[row["values"]["component_as_published"]] = row["values"]["value"]
    assert actual == {
        sample: {formula: value for formula, value in components.items() if value is not None}
        for sample, components in expected_grid.items()
    }


def test_table5_grid_matches_independent_page_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table5_grid(document, TABLE5_RENDER_GRID)
    migrated = _migrate(EXTRACT)
    expected_species = {
        row["observation_id"]: formula
        for formula, body in document["species"].items()
        for row in body.get("observations", [])
    }
    migrated_species = {
        row.observation_id.split("::", 1)[1]: row.identity.species.formula
        for row in migrated.observations.values()
        if row.source_id == document["source_id"]
    }
    assert migrated_species == expected_species


def test_table5_grid_mutations_fail_for_grid_and_extract_cells(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated_grid = {sample: dict(values) for sample, values in TABLE5_RENDER_GRID.items()}
    mutated_grid["1a"]["ZnO"] = "99.08"
    with pytest.raises(AssertionError):
        _assert_table5_grid(document, mutated_grid)

    target = next(
        row
        for row in document["species"]["ZnO"]["observations"]
        if row.get("locator", {}).get("table") == "5"
        and row["values"]["sample_id_as_published"] == "1a"
    )
    target["values"]["value"] = "99.08"
    with pytest.raises(AssertionError):
        _assert_table5_grid(document, TABLE5_RENDER_GRID)
    mutated = tmp_path / EXTRACT.name
    mutated.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert not _migrate(mutated).validation.hard_issues


def test_table6_silica_reference_is_quartz_and_solid_solutions_stay_crystal() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for row in document["species"]["SiO2"]["observations"]:
        if row.get("locator", {}).get("table") == "6":
            assert row["standard_state"]["state"]["value"]["endmember"]["polymorph"]["value"] == "quartz"
            assert "JANAF O-037" in row["standard_state"]["note"]
    migrated = _migrate(EXTRACT)
    zno_activity = next(row for row in document["species"]["ZnO"]["observations"] if row.get("locator", {}).get("table") == "6")
    assert zno_activity["standard_state"]["state"]["value"]["endmember"]["polymorph"]["tag"] == "unknown"
    assert zno_activity["standard_state"]["state"]["value"]["endmember"]["polymorph"]["reason"] == "polymorph not named in Table 6"
    for row in migrated.observations.values():
        if row.source_id == yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))["source_id"] and "table_5_" in row.observation_id:
            assert row.identity.species.phase.value.value == "cr"


def test_formation_free_energy_fits_remain_context_with_printed_equations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    cu2o_context = document["species"]["Cu2O"]["context"]
    zno_context = document["species"]["ZnO"]["context"]
    eq8 = next(row for row in cu2o_context if row["observation_id"] == "itoh_1989_eq8_cu2o_standard_formation_free_energy_fit")
    eq10 = next(row for row in zno_context if row["observation_id"] == "itoh_1989_eq10_zno_standard_formation_free_energy_fit")
    eq12 = next(row for row in zno_context if row["observation_id"] == "itoh_1989_eq12_zno_standard_formation_free_energy_fit")
    assert eq8["values"]["equation_as_printed"] == "ΔG°Cu2O/J mol-1 = -166690 + 71.03 T/K ± 100"
    assert eq10["locator"]["equation"] == "10"
    assert eq10["locator"]["published_page"] == 687
    assert eq10["values"]["equation_as_printed"] == "ΔG°ZnO/J mol-1 = -351540 + 105.00 T/K ± 120"
    assert eq10["values"]["temperature_range_as_printed"] == "840–1017 K"
    assert eq12["locator"]["equation"] == "12"
    assert eq12["locator"]["published_page"] == 688
    assert eq12["values"]["equation_as_printed"] == "ΔG°ZnO/J mol-1 = -355940 + 108.53 T/K ± 190"
    assert eq12["values"]["temperature_range_as_printed"] == "823–1073 K"
