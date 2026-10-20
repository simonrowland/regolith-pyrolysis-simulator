from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/itoh-yokokawa-1984-sodium-activity-nas.yaml"

# Independent transcription from the 600 dpi render of physical page 3.
TABLE2_RENDER = (
    ("55/45/0", "38.4±1.4"), ("50/50/0", "24.9±4.0"), ("45/55/0", "13.6±0.6"),
    ("40/60/0", "0"), ("35/65/0", "-13.1±4.8"), ("30/70/0", "-17.9±3.7"),
    ("27.5/72.5/0", "-30.0±1.6"), ("25/75/0", "-28.9±3.0"), ("22.5/77.5/0", "-20.7±4.2"),
    ("unknown", "-31.7±2.5"), ("20/80/0", "-18.8±1.8"), ("unknown", "-24.4±5.6"),
    ("50/45/5", "38.3±0.7"), ("45/50/5", "16.1±1.9"), ("40/55/5", "9.2±0.7"),
    ("35/60/5", "-7.4±0.3"), ("30/65/5", "-13.7±2.8"), ("25/70/5", "-22.5±2.7"),
    ("20/75/5", "-23.6±2.4"), ("45/45/10", "24.8±4.9"), ("40/50/10", "12.1±2.6"),
    ("35/55/10", "3.1±1.3"), ("30/60/10", "-9.0±0.7"), ("25/65/10", "-13.9±5.6"),
    ("20/70/10", "-15.8±1.6"), ("40/45/15", "28.7±1.7"), ("35/50/15", "15.7±1.0"),
    ("30/55/15", "2.6±1.6"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _grid(document: dict) -> tuple[tuple[str, str], ...]:
    context = next(
        item for item in document["species"]["Na2O"]["context"]
        if item["observation_id"] == "itoh_yokokawa_1984_table2_relative_gibbs_entropy"
    )
    result = []
    for row in context["values"]["rows_as_printed"]:
        composition = row["composition_as_printed"]
        if composition == "not printed in this row":
            key = "unknown"
        else:
            key = "/".join(value.split()[0] for value in composition.split(", "))
        result.append((key, row["delta_H_relative_kJ_per_mol"]))
    return tuple(result)


def _assert_render_grid(document: dict, expected=TABLE2_RENDER) -> None:
    assert _grid(document) == tuple(expected)


def test_table2_render_transcription_and_mutation_checks() -> None:
    document = _document()
    _assert_render_grid(document)

    changed_grid = list(TABLE2_RENDER)
    changed_grid[4] = (changed_grid[4][0], "-13.2±4.8")
    with pytest.raises(AssertionError):
        _assert_render_grid(document, changed_grid)

    changed_extract = deepcopy(document)
    table2 = next(
        item for item in changed_extract["species"]["Na2O"]["context"]
        if item["observation_id"] == "itoh_yokokawa_1984_table2_relative_gibbs_entropy"
    )
    table2["values"]["rows_as_printed"][4]["delta_H_relative_kJ_per_mol"] = "-13.2±4.8"
    with pytest.raises(AssertionError):
        _assert_render_grid(changed_extract)


def test_context_only_migration_has_no_unavailable_or_hard_rows() -> None:
    document = _document()
    assert document["species"]["Na2O"]["observations"] == []
    assert len(_grid(document)) == 28
    raw_emf = next(
        item for item in document["species"]["Na2O"]["context"]
        if item["observation_id"] == "itoh_yokokawa_1984_table1_emf_candidate"
    )
    assert len(raw_emf["values"]["tables_as_printed"]) == 2
    assert all(len(row["cells"]) == 6 for table in raw_emf["values"]["tables_as_printed"] for row in table["rows_as_printed"])
    equations = {item["observation_id"]: item for item in document["species"]["Na2O"]["context"]}
    assert equations["itoh_yokokawa_1984_equation_2_formation_from_oxides"]["values"]["reaction_delta_G_kJ_per_mol"] == -244.5
    assert equations["itoh_yokokawa_1984_equation_3_partial_molar_gibbs_relation"]["values"]["right_hand_side_kJ_per_mol"] == -81.5
    assert equations["itoh_yokokawa_1984_equation_4_log_activity_silica"]["values"]["reference"] == "pure solid SiO2, tridymite"
    assert equations["itoh_yokokawa_1984_equation_5_log_activity_sodium_oxide"]["values"]["reference"] == "pure solid Na2O"

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not tuple(item for item in migrator.result.observations.values() if item.source_id == EXTRACT.stem)
    assert migrator.result.registry_issues == []
    assert not migrator.result.validation.hard_issues
