"""Source pins for the measured-reduced Guo et al. MgO activities."""

from __future__ import annotations

from copy import deepcopy
import csv
from decimal import Decimal
from pathlib import Path
import os

import yaml

from simulator.battery.migrate import Migrator, load_aliases, load_index
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "guo-2021-mgo-activity-cmas-slag"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
TABLE5_CSV = CORPUS_ROOT / "tables" / SOURCE_ID / "t5.csv"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate(path: Path):
    migrator = Migrator(root=ROOT, index=load_index(), aliases=load_aliases())
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator


def _table5_activities() -> tuple[Decimal, ...]:
    with TABLE5_CSV.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    return tuple(Decimal(row["a_MgO"]) for row in rows)


def test_extract_preserves_printed_reduction_and_model_context() -> None:
    document = _document()
    observation = document["species"]["MgO"]["observations"][0]
    values = observation["values"]
    assert values["method_class"] == "measured_reduced"
    assert observation["derived_from"] == [f"tables:{SOURCE_ID}"]
    assert observation["derivation"]["inputs"] == [f"tables:{SOURCE_ID}"]
    assert "Eq. 6" in observation["derivation"]["relation"]
    assert "t5.csv" in observation["derivation"]["relation"]
    assert "t4.csv" in observation["derivation"]["relation"]
    points = values["points"]
    activities = _table5_activities()
    assert tuple(Decimal(point["activity"]) for point in points) == activities
    assert [point["row_as_printed"] for point in points] == [str(i) for i in range(1, 9)]
    assert all(point["method_class"] == "measured_reduced" for point in points)
    assert len(document["species"]["Mg"]["context"]) == 1
    assert document["species"]["Mg"]["context"][0]["values"]["evidence_class"] == "model_derived"
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_isolated_migration_resolves_the_table_asset_and_pins_all_eight_values() -> None:
    migrator = _migrate(EXTRACT)
    records = [
        observation
        for observation in migrator.result.observations.values()
        if "guo_2021_table5_mgo_activities_1873k" in observation.observation_id
    ]
    assert len(records) == 8
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert tuple(record.value.point for record in records) == _table5_activities()
    assert all(record.evidence.class_.value.value == "measured_reduced" for record in records)
    assert all(record.admission.status.value == "admitted" for record in records)
    assert all(record.identity.temperature_K.value == 1873 for record in records)
    assert all(record.derived_from == (f"tables:{SOURCE_ID}",) for record in records)
    assert all(record.derivation.inputs == (f"tables:{SOURCE_ID}",) for record in records)
    assert len(migrator.result.context_by_work[next(iter(migrator.result.context_by_work))]) >= 1


def test_mutating_a_printed_activity_changes_its_migrated_value(tmp_path: Path) -> None:
    document = deepcopy(_document())
    point = document["species"]["MgO"]["observations"][0]["values"][
        "points"
    ][0]
    point["activity"] = "0.4488"
    point["activity_as_printed"] = "0.4488"
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    migrator = _migrate(mutated_extract)
    records = [
        observation
        for observation in migrator.result.observations.values()
        if "guo_2021_table5_mgo_activities_1873k" in observation.observation_id
    ]
    assert not migrator.result.validation.hard_issues
    assert records[0].value.point == Decimal("0.4488")
    assert records[0].value.point != _table5_activities()[0]

