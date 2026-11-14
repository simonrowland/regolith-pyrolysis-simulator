"""Table 1 residue-composition pins for Mendybaev et al. (2017)."""

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
        and point["locator"]["pdf_page_index"] == 39
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
        assert elapsed.locator.pdf_page_index == 39
        assert elapsed.locator.table == "1"
        assert row.locator.pdf_page_index == 39
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
