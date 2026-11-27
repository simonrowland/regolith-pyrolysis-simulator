from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_utils import isolated_root
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "gran-2011-thesis-high-basicity-cmas-liquidus"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits" / f"{SOURCE_ID}.cells.csv"
PDF_SHA256 = "17d30fc1ec3175eba336bab4c8f4e6ee730be20fdef82f35dfa7a227d30c081a"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _isolated_migration(tmp_path: Path, document: dict):
    root = isolated_root(tmp_path, document)
    path = root / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
    migrator = Migrator(root=root, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _source_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for formula_block in document["species"].values()
        for row in formula_block["context"]
    }


def test_table12_estimates_preserve_rows_model_method_reference_states_and_coverage() -> None:
    document = _document()
    rows = _source_rows(document)
    by_formula = {
        formula: block["context"]
        for formula, block in document["species"].items()
    }

    assert document["source"]["sha256"] == PDF_SHA256
    assert document["source"]["corpus_sha256"] == PDF_SHA256
    assert len(rows) == 92
    assert document.get("fidelity_samples", []) == []
    assert document["extraction"]["audit_file"] == (
        f"data/literature/extracts/audits/{SOURCE_ID}.cells.csv"
    )
    with CELL_AUDIT.open(newline="", encoding="utf-8") as stream:
        cell_audit = list(csv.DictReader(stream))
    assert len(cell_audit) == 209
    activity_cells = {
        (cell["printed_row"], cell["printed_column"]): cell["printed_value"]
        for cell in cell_audit
        if cell["printed_column"].startswith("a(")
    }
    assert len(activity_cells) == 92
    composition_cells = {
        (cell["printed_row"], cell["printed_column"]): cell["printed_value"]
        for cell in cell_audit
        if cell["printed_column"].startswith("liquid composition ")
    }
    assert len(composition_cells) == 116
    assert Counter({formula: len(values) for formula, values in by_formula.items()}) == {
        "CaO": 29,
        "Al2O3": 34,
        "MgO": 29,
    }
    assert all(row["type"] == "activity_estimate" for row in rows.values())
    for row in rows.values():
        locator = row["locator"]
        assert activity_cells[(locator["row"], locator["column"])] == row["values"][
            "activity_as_printed"
        ]
        composition_locator = row["point_conditions"]["composition"]["locator"]
        components = row["point_conditions"]["composition"]["state"]["value"][
            "components"
        ]
        for oxide, value in components:
            key = (
                composition_locator["row"],
                f"liquid composition {oxide} (mass%)",
            )
            assert composition_cells[key] == value
    assert len({row["values"]["sample_id"] for row in rows.values()}) == 29
    assert all(
        row["values"]["method_class"]
        == row["values"]["evidence_class"]
        == "model_derived"
        and row["values"]["method_as_printed"].startswith(
            "Estimated by thermodynamic calculation"
        )
        for row in rows.values()
    )
    assert "MgO.Al2O3 formation Gibbs-energy data" in document["extraction"]["method"]
    assert "[32]" in document["extraction"]["method"]
    assert "[33]" in document["extraction"]["method"]
    assert all(
        row["standard_state"]["state"]["value"]["endmember"]["phase"]
        == {"tag": "value", "value": "cr"}
        and row["standard_state"]["state"]["value"]["endmember"]["polymorph"]
        == {
            "tag": "unknown",
            "reason": "The table states pure solid oxide but does not name its polymorph.",
        }
        for row in rows.values()
    )
    alumina = by_formula["Al2O3"]
    assert Counter(row["values"]["thermodynamic_data_reference_as_printed"] for row in alumina) == {
        "[32]": 17,
        "[33]": 17,
    }
    assert any(row["values"]["activity_as_printed"] == "0.00031(2)" for row in alumina)
    assert any(row["values"]["activity_as_printed"] == "0.00057(3)" for row in alumina)
    assert {
        row["point_conditions"]["saturation_phase"]["state"]["value"]
        for row in rows.values()
    } == {
        "MgO",
        "CaO",
        "MgO + spinel",
        "MgO + CaO",
    }
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    fidelity_warnings = []
    errors = validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SOURCE_ID,
        check_fidelity_match=True,
        fidelity_warnings=fidelity_warnings,
        pre_policy_ids=set(),
        artifact_root=ROOT,
    )
    assert errors == []
    assert not [warning for warning in fidelity_warnings if warning.kind == "uncovered_observation"]


def test_isolated_migration_does_not_admit_contextual_estimates_as_observations(
    tmp_path: Path,
) -> None:
    result = _isolated_migration(tmp_path, _document())

    assert not [row for row in result.observations.values() if row.source_id == SOURCE_ID]
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
