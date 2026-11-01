"""Printed-table fidelity and isolated migration for Kozhina and Shultz (2000)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
import pytest

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kozhina-2000-na-glassforming-thermodynamics.yaml"

EXPECTED_ROWS = [
    ("Na2O-SiO2", "0.1", "1300", "-26.8", "-18.4"),
    ("Na2O-SiO2", "0.2", "1300", "-53.6", "-43.1"),
    ("Na2O-SiO2", "0.3", "1300", "-78.7", "-73.3"),
    ("Na2O-SiO2", "0.4", "1300", "-98.6", "-99.2"),
    ("Na2O-SiO2", "0.5", "1300", "-113.7", "-120.6"),
    ("Na2O-B2O3", "0.1", "1300", "-36.4", "-38.9"),
    ("Na2O-B2O3", "0.2", "1300", "-67.4", "-76.2"),
    ("Na2O-B2O3", "0.3", "1300", "-94.0", "-98.8"),
    ("Na2O-B2O3", "0.4", "1300", "-116.0", "-110.1"),
    ("Na2O-B2O3", "0.5", "1300", "-134.0", "-113.9"),
    ("Na2O-GeO2", "0.1", "1300", "-33.5", "-27.6"),
    ("Na2O-GeO2", "0.2", "1300", "-61.8", "-55.7"),
    ("Na2O-GeO2", "0.3", "1300", "-86.0", "-81.6"),
    ("Na2O-GeO2", "0.4", "1300", "-105.9", "-107.2"),
    ("Na2O-GeO2", "0.5", "1300", "-120.7", "-129.8"),
    ("Na2O-GeO2", "0.6", "1300", "-127.8", "-137.3"),
    ("Na2O-P2O5", "0.4", "1073", "-224.0", "-186.7"),
    ("Na2O-P2O5", "0.5", "1073", "-258.5", "-224.4"),
    ("Na2O-P2O5", "0.6", "1073", "-278.8", "-254.1"),
    ("Na2O-P2O5", "0.7", "1073", "-285.2", "-272.3"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_rows(document: dict) -> list[tuple[str, str, str, str, str]]:
    context = document["species"]["Na2O"]["context"]
    table = next(row for row in context if row["observation_id"] == "kozhina_2000_table_1_integral_thermochemistry")
    return [
        (
            row["system"],
            row["XNa2O"],
            row["T_K"],
            row["delta_Gf_kJ_per_mol"],
            row["delta_Hf_kJ_per_mol"],
        )
        for row in table["values"]["rows_as_published"]
    ]


def test_table_1_preserves_all_rows_cells_and_render_anchors() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document) == []
    rows = _table_rows(document)
    assert len(rows) == 20
    assert rows == EXPECTED_ROWS
    assert sum(len(row[3:]) for row in rows) == 40

    # Render-checked cells on printed page 94, PDF page 4, Table 1.
    assert rows[0][3] == "-26.8"
    assert rows[9][4] == "-113.9"
    assert rows[-1][4] == "-272.3"
    table = document["species"]["Na2O"]["context"][0]
    assert table["locator"] == {
        "page": 4,
        "published_page": 94,
        "pdf_page_index": 3,
        "table": "1",
    }
    assert table["values"]["reference_state_as_printed"] == (
        "referred to the corresponding properties of the pure oxides"
    )
    assert all(set(row) == {"system", "XNa2O", "T_K", "delta_Gf_kJ_per_mol", "delta_Hf_kJ_per_mol"} for row in table["values"]["rows_as_published"])
    figure = next(
        row
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "kozhina_2000_figure_2_partial_properties_candidate"
    )
    assert figure["locator"] == {
        "page": 3,
        "published_page": 93,
        "pdf_page_index": 2,
        "figure": "2",
    }

    mutated = deepcopy(document)
    mutated_table = mutated["species"]["Na2O"]["context"][0]["values"]["rows_as_published"]
    mutated_table[9]["delta_Hf_kJ_per_mol"] = "-114.0"
    with pytest.raises(AssertionError):
        assert _table_rows(mutated) == EXPECTED_ROWS


def test_isolated_migration_carries_context_without_scored_rows() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 0
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in migrator.result.observations.values()
    ) == 0
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 3
    carried = next(iter(migrator.result.context_by_work.values()))
    table = next(row for row in carried if row["observation_id"] == "kozhina_2000_table_1_integral_thermochemistry")
    assert len(table["values"]["rows_as_published"]) == 20
    assert "total_pressure" not in str(table["values"]).lower()
    assert "fo2" not in str(table["values"]).lower()
