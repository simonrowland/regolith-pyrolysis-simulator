from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "shornikov-2008-mgo-aluminosilicate-melts"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
FRAME_REASON = (
    "No quantity token represents formation from simple oxides; "
    "delta_fG denotes formation from the elements."
)
HIT_ROWS = {
    "shornikov_2008_fig1_d_marker_1": (
        "Gibbs energies of melt formation from oxides",
        "-23",
        2,
        148,
    ),
    "shornikov_2008_fig1_d_marker_2": (
        "Gibbs energies of melt formation from oxides",
        "-25",
        2,
        148,
    ),
    "shornikov_2008_fig1_d_marker_3": (
        "Gibbs energies of melt formation from oxides",
        "-27",
        2,
        148,
    ),
    "shornikov_2008_fig1_d_marker_4": (
        "Gibbs energies of melt formation from oxides",
        "-26",
        2,
        148,
    ),
    "shornikov_2008_fig1_d_marker_5": (
        "Gibbs energies of melt formation from oxides",
        "-23",
        2,
        148,
    ),
    "shornikov_2008_fig1_d_marker_6": (
        "Gibbs energies of melt formation from oxides",
        "-19",
        2,
        148,
    ),
    "shornikov_2008_prose_cordierite_minimum_delta_fg": (
        "Gibbs energy of melt formation from oxides",
        "-26.9",
        1,
        147,
    ),
    "shornikov_2011_fig1_d_allibert_marker_01": (
        "Gibbs energy of melt formation from oxides, as captioned.",
        "-23.0",
        2,
        None,
    ),
    "shornikov_2011_fig1_d_allibert_marker_02": (
        "Gibbs energy of melt formation from oxides, as captioned.",
        "-24.25",
        2,
        None,
    ),
    "shornikov_2011_fig1_d_allibert_marker_03": (
        "Gibbs energy of melt formation from oxides, as captioned.",
        "-27.0",
        2,
        None,
    ),
    "shornikov_2011_fig1_d_allibert_marker_04": (
        "Gibbs energy of melt formation from oxides, as captioned.",
        "-30.0",
        2,
        None,
    ),
}


def _records(document: dict) -> dict[str, dict]:
    result = {}

    def visit(node):
        if isinstance(node, dict):
            if "observation_id" in node:
                result[node["observation_id"]] = node
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return result


def _assert_oxide_frame_rows(document: dict, hit_rows: dict) -> None:
    rows = _records(document)
    gaps = {
        gap["observation_id"]: gap["reason"]
        for gap in document["schema_gaps"]
        if gap["observation_id"] in hit_rows
    }
    assert set(gaps) == set(hit_rows)
    assert set(hit_rows).issubset(rows)
    for observation_id, (quantity, printed_value, page, published_page) in hit_rows.items():
        row = rows[observation_id]
        values = row["values"]
        assert "quantity" not in values
        assert "value" not in values
        assert values["quantity_as_printed"] == quantity
        assert values["reference_state_as_printed"] == "formation from simple oxides"
        assert values["schema_gap"] == FRAME_REASON
        assert gaps[observation_id] == FRAME_REASON
        assert values["value_as_printed"] == printed_value
        assert row["locator"]["page"] == page
        assert row["locator"].get("published_page") == published_page


def test_oxide_frame_rows_are_unavailable_and_pinned() -> None:
    extracts = {
        SOURCE_ID: EXTRACT,
        "shornikov-2011-cao-mgo-alumina-melts": ROOT
        / "data/literature/extracts/shornikov-2011-cao-mgo-alumina-melts.yaml",
    }
    for source_id, extract in extracts.items():
        source_prefix = f"shornikov_{source_id.split('-')[1]}_"
        hit_rows = {
            observation_id: values
            for observation_id, values in HIT_ROWS.items()
            if observation_id.startswith(source_prefix)
        }
        assert hit_rows
        document = yaml.safe_load(extract.read_text(encoding="utf-8"))
        _assert_oxide_frame_rows(document, hit_rows)

        mutated = deepcopy(document)
        rows = _records(mutated)
        mutation_id = next(iter(hit_rows))
        rows[mutation_id]["values"]["quantity"] = "delta_fG"
        with pytest.raises(AssertionError):
            _assert_oxide_frame_rows(mutated, hit_rows)

        migrator = Migrator(root=ROOT, index={}, aliases={})
        migrator._migrate_extract(extract)
        migrator.finalize()
        for observation_id in hit_rows:
            migrated = [
                row
                for key, row in migrator.result.observations.items()
                if key == f"{source_id}::{observation_id}"
                or key.startswith(f"{source_id}::{observation_id}::")
            ]
            assert len(migrated) == 1
            assert not migrated[0].identity.quantity.is_value
            assert migrated[0].value.kind is ValueKind.UNAVAILABLE
            assert migrated[0].admission.status is AdmissionStatus.PENDING
