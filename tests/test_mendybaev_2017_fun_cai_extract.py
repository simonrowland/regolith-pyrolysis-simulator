"""Table 1 residue-composition pins for Mendybaev et al. (2017)."""

import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2017-fun-cai-lab-evaporation.yaml"
OXIDES = {"MgO", "Al2O3", "SiO2", "CaO"}
FUNC_STARTING_COMPOSITION = {
    "MgO": Decimal("37.9"),
    "Al2O3": Decimal("11.6"),
    "SiO2": Decimal("42.9"),
    "CaO": Decimal("7.6"),
}
SOURCE_PAGE_PINS = {
    8: "mixture loaded onto 2.5 mm diameter iridium wire loops",
    9: "15 keV accelerating voltage and a 0.4 nA beam current",
    11: "relatively slow quenching (~1000˚C/min) of the molten Mg2SiO4-rich droplets",
    13: "CaO and Al2O3 do not evaporate from the melt",
    16: "α25,24= 0.98383±0.00033, which within error",
    17: "silicon isotopic compositions plot along a line with slope",
    20: "γMg = 2.1 which was the value used in Fig. 3",
    38: "Table 1. Measured chemical bulk composition and magnesium, silicon",
}
SOURCE_LOCATOR_INDEX_PINS = {
    "sample_load_locator": 8,
    "quench_rate_locator": 11,
    "alpha_25_24_locator": 16,
    "alpha_29_28_locator": 17,
    "alpha_18_16_forsterite_field_locator": 17,
    "alpha_18_16_melilite_field_locator": 17,
}


def _migrate(path: Path = EXTRACT) -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator


def _residues(migrator: Migrator):
    return [
        item
        for item in migrator.result.observations.values()
        if item.identity.quantity.is_value
        and item.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]


def _named_values(value: object, name: str) -> list[object]:
    if isinstance(value, dict):
        matches = [value[name]] if name in value else []
        return matches + [
            match
            for child in value.values()
            for match in _named_values(child, name)
        ]
    if isinstance(value, list):
        return [match for child in value for match in _named_values(child, name)]
    return []


def _assert_source_page_index_pins(document: dict) -> None:
    indexes: set[int] = set()

    def collect_indexes(value: object) -> None:
        if isinstance(value, dict):
            if "pdf_page_index" in value:
                indexes.add(value["pdf_page_index"])
            for child in value.values():
                collect_indexes(child)
        elif isinstance(value, list):
            for child in value:
                collect_indexes(child)

    collect_indexes(document)
    assert indexes == set(SOURCE_PAGE_PINS)
    for locator_name, expected_index in SOURCE_LOCATOR_INDEX_PINS.items():
        locators = _named_values(document, locator_name)
        assert locators
        assert all(locator["pdf_page_index"] == expected_index for locator in locators)
    gamma = _named_values(document, "gamma_SiO_over_gamma_Mg")
    assert gamma and all(item["locator"]["pdf_page_index"] == 20 for item in gamma)
    detector = _named_values(document["benches"], "detector")
    assert detector and all(item["locator"]["pdf_page_index"] == 9 for item in detector)


def test_source_page_index_pins_and_plus_one_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_page_index_pins(document)

    mutated = deepcopy(document)
    sample_load = _named_values(mutated, "sample_load_locator")[0]
    sample_load["pdf_page_index"] += 1
    try:
        _assert_source_page_index_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("a +1 source-page mutation must fail the independent pin guard")


def test_table_1_first_last_and_largest_two_sigma_cells_match_page() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        point
        for observation in document["species"]["FUNC"]["observations"]
        if observation["observation_id"].endswith("table1_residue_composition")
        for point in observation["values"]["series"]
    ]
    cells = {
        (point["sample"], column): (point[f"{column}_wt_pct"], point["sigma"])
        for point in rows
        for column in OXIDES
        if f"{column}_wt_pct" in point
    }

    assert len(cells) == 36
    assert cells[("FUNC-5", "MgO")] == ("34.09", "0.10")
    assert cells[("FUNC-6", "CaO")] == ("26.75", "0.08")
    assert cells[("FUNC-4", "Al2O3")] == ("32.15", "0.54")
    assert all(
        point["locator"]["table"] == "1"
        and point["locator"]["pdf_page_index"] == 38
        and point["locator"]["row"] == point["sample"]
        and point["locator"]["column"] in OXIDES
        for point in rows
    )


def test_table_1_residues_migrate_with_run_charge_clock_and_conditions() -> None:
    migrator = _migrate()
    rows = _residues(migrator)

    assert len(rows) == 36
    assert len(migrator.result.experiments) == 10, sorted(migrator.result.experiments)
    assert not migrator.result.validation.hard_issues
    assert {row.identity.species.formula for row in rows} == OXIDES
    assert {row.identity.temperature_K.value for row in rows} == {Decimal("2173.15")}
    for row in rows:
        run = row.locator.note.partition(";")[0].removeprefix("row=")
        assert row.experiment_id.endswith(f"::experiment::{run.casefold()}")
        assert row.identity.sample_mass_kg.is_value
        starting = row.point_conditions["starting_composition"]
        assert starting.state.is_value
        assert {
            oxide: Decimal(str(value))
            for oxide, value in starting.state.value.items()
        } == FUNC_STARTING_COMPOSITION
        elapsed = row.point_conditions["time_min"]
        assert elapsed.state.is_value
        assert elapsed.locator is not None
        assert elapsed.locator.pdf_page_index == 38
        assert elapsed.locator.table == "1"
        assert row.locator.pdf_page_index == 38
        assert row.locator.table == "1"

    scheduled_experiments = [
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.thermal_schedule is not None
    ]
    assert len(scheduled_experiments) == 9
    for experiment in scheduled_experiments:
        schedule = experiment.thermal_schedule
        assert schedule.method is not None
        method_locator = schedule.method.locator
        assert method_locator is not None
        assert method_locator.section == "3.1"
        assert method_locator.pdf_page_index == 8
        assert method_locator.line_range == "166-173"
        assert schedule.total_duration_s is not None
        assert schedule.total_duration_s.state.is_unknown
        assert schedule.total_duration_s.state.reason == "not_published"
        duration_locator = schedule.total_duration_s.locator
        assert duration_locator is not None
        assert duration_locator.section == "3.1"
        assert duration_locator.pdf_page_index == 8


def test_table_locators_resolve_to_the_physical_page_named_by_extract() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    page_note = re.search(
        r"PDF page (\d+) used to map Table 1 and Table 2",
        document["extraction"]["method"],
    )
    assert page_note is not None
    expected_index = int(page_note.group(1)) - 1
    table_indices: dict[str, set[int]] = {}

    def collect_table_locators(value: object) -> None:
        if isinstance(value, dict):
            locator = value.get("locator")
            if isinstance(locator, dict) and "table" in locator:
                table_indices.setdefault(str(locator["table"]), set()).add(
                    locator["pdf_page_index"]
                )
            for child in value.values():
                collect_table_locators(child)
        elif isinstance(value, list):
            for child in value:
                collect_table_locators(child)

    collect_table_locators(document)

    assert table_indices == {"1": {expected_index}, "2": {expected_index}}


def test_table_1_cell_value_and_sigma_mutations_reach_migrated_rows(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        point
        for observation in document["species"]["FUNC"]["observations"]
        if observation["observation_id"] == "mendybaev_2017_func5_table1_residue_composition"
        for point in observation["values"]["series"]
        if point["locator"]["column"] == "MgO"
    )
    target["MgO_wt_pct"] = "34.10"
    target["sigma"] = "0.11"
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    row = next(
        item
        for item in _residues(_migrate(mutated_extract))
        if item.experiment_id.endswith("::experiment::func-5")
        and item.identity.species.formula == "MgO"
    )
    assert row.value.point == Decimal("34.10")
    assert row.uncertainty.kind.value == "printed"
    assert row.uncertainty.verbatim["sigma"] == "0.11"
