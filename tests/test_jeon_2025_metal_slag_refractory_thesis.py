from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import simulator.battery.migrate as migration_module
from simulator.battery.enums import Quantity, UncertaintyKind
from simulator.battery.score import _printed_uncertainty_band
from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jeon-2025-metal-slag-refractory-thesis.yaml"
SOURCE_ID = "jeon-2025-metal-slag-refractory-thesis"
RENDER_PINS = (
    ("jeonthesis_t8_s1_olivine_eds_mgo", "MgO", 61.1, 57),
    ("jeonthesis_t8_s19_zincite_epma_zno", "ZnO", 96.7, 57),
    ("jeonthesis_t8_s19_slag_eds_mgo", "MgO", 9.3, 58),
    ("jeonthesis_t8_s23_slag_epma_sio2", "SiO2", 54.2, 58),
)


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, block in document["species"].items()
        for row in block.get("observations", [])
    }


def _assert_render_pins(document: dict, pins=RENDER_PINS) -> None:
    rows = _rows(document)
    for observation_id, species, expected, page in pins:
        actual_species, row = rows[observation_id]
        assert actual_species == species
        assert row["values"]["value"] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["locator"]["published_page"] == page - 12


def _assert_olivine_eds_uncertainty(observation) -> None:
    assert observation.uncertainty.kind is UncertaintyKind.PRINTED
    assert observation.uncertainty.verbatim["value"] == "0.9"
    assert observation.uncertainty.verbatim["units"] == "mol%"
    assert observation.uncertainty.verbatim["confidence_level"] == "not_stated"


def test_table8_render_pins_reject_mutated_grid_and_extract_cells() -> None:
    document = _document()
    _assert_render_pins(document)
    changed_grid = list(RENDER_PINS)
    changed_grid[0] = (changed_grid[0][0], "MgO", 61.0, 57)
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_grid)
    changed_extract = deepcopy(document)
    _rows(changed_extract)[RENDER_PINS[0][0]][1]["values"]["value"] = 61.0
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_extract)


def test_thesis_only_table8_migrates_all_components_and_duplicate_tables_stay_context(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path, "jeon-2025-metal-slag-refractory-thesis.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    source_rows = _rows(document)
    assert len(source_rows) == 330
    _assert_render_pins(document)
    assert all(
        "value" in source["values"]
        and source["values"]["quantity"] == "equilibrium_phase_composition"
        for _species, source in source_rows.values()
    )
    # d-233 leaves crystal phase roles outside the closed schema. Preserve every
    # Table 8 value and pin the migrator's typed refusal for each row.
    refusals = [
        item
        for item in result.queue
        if item.source == "data/literature/extracts/jeon-2025-metal-slag-refractory-thesis.yaml"
    ]
    assert len(refusals) == len(source_rows)
    assert {
        item.observation_id.rsplit("::", 1)[-1] for item in refusals
    } == set(source_rows)
    assert all(
        item.axes == ["phase_composition"]
        and item.why == "phase_label is not a closed phase role (melt, slag, glass, metal, matte)"
        for item in refusals
    )
    contexts = [row for block in document["species"].values() for row in block.get("context", [])]
    linked = [row for row in contexts if row.get("observation_id", "").startswith(("jeonthesis_t5_", "jeonthesis_t9_", "jeonthesis_t10_")) and row.get("values", {}).get("linked_observation_id")]
    assert len(linked) == 305
    assert {row["values"]["published_source_id"] for row in linked} == {
        "jeon-lindberg-2023-mg-ca-zn-oxide-equilibria",
        "jeon-lindberg-2025-mgo-refractory-slag-interface",
    }


def test_table8_uncertainties_migrate_with_width_confidence_and_stable_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    source_rows = _rows(document)
    uncertain_rows = {
        observation_id: row
        for observation_id, (_species, row) in source_rows.items()
        if "uncertainty" in row
    }
    assert len(uncertain_rows) == 282
    for row in uncertain_rows.values():
        assert "points" not in row["values"]
        assert "±" in row["values"]["value_as_published"]
        assert row["uncertainty"]["units"] == "mol%"
        assert row["uncertainty"]["confidence_level"] == "not_stated"
        Decimal(row["uncertainty"]["value"])

    olivine_id = "jeonthesis_t8_s1_olivine_eds_mgo"
    olivine = uncertain_rows[olivine_id]
    assert olivine["values"]["value"] == 61.1
    assert olivine["values"]["value_as_published"] == "61.1 ± 0.9"
    assert olivine["uncertainty"] == {
        "value": "0.9",
        "units": "mol%",
        "confidence_level": "not_stated",
        "note": "Printed ± 0.9.",
    }

    # The independent phase-role gate currently queues these rows before they
    # reach the uncertainty converter. Bypass only that unrelated gate here so
    # this focused migration pins the extract-to-record uncertainty mapping.
    monkeypatch.setattr(
        migration_module,
        "_phase_row_rejection",
        lambda _values, _obs: None,
    )
    result = _migrate_real_extract(
        tmp_path / "before-mutation", EXTRACT.name, extract_doc=document
    )
    migrated = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(f"{SOURCE_ID}::")
    }
    assert set(migrated) == set(source_rows)
    counts = Counter(
        (
            observation.uncertainty.kind.value,
            (observation.uncertainty.verbatim or {}).get("confidence_level"),
            (observation.uncertainty.verbatim or {}).get("value") is not None,
        )
        for observation in migrated.values()
    )
    assert counts == Counter(
        {("printed", "not_stated", True): 282, ("none", None, False): 48}
    )
    _assert_olivine_eds_uncertainty(migrated[olivine_id])

    resolution = _printed_uncertainty_band(
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        migrated[olivine_id].uncertainty,
        Decimal("61.1"),
        source_observation=migrated[olivine_id],
    )
    assert resolution.band is None
    assert resolution.refusal is not None
    assert resolution.refusal["reason"] == "printed_uncertainty_unit_unmapped"

    mutated_document = deepcopy(document)
    mutated = _rows(mutated_document)[olivine_id][1]
    mutated["values"]["value_as_published"] = "61.1 ± 0.8"
    mutated["uncertainty"]["value"] = "0.8"
    mutated["uncertainty"]["note"] = "Printed ± 0.8."
    mutated_result = _migrate_real_extract(
        tmp_path / "mutated-width", EXTRACT.name, extract_doc=mutated_document
    )
    assert set(mutated_result.observations) == set(result.observations)
    mutated_observation = mutated_result.observations[
        f"{SOURCE_ID}::{olivine_id}"
    ]
    assert mutated_observation.uncertainty.verbatim["value"] == "0.8"
    with pytest.raises(AssertionError):
        _assert_olivine_eds_uncertainty(mutated_observation)


def test_all_thesis_figures_are_deferred_records_without_numeric_points() -> None:
    document = _document()
    figures = [
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row.get("observation_id", "").startswith("jeonthesis_figure_")
    ]
    assert len(figures) == 32
    assert {row["locator"]["figure"] for row in figures} == {
        str(number) for number in range(1, 33)
    }
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("value" not in row["values"] for row in figures)
    assert next(row for row in figures if row["locator"]["figure"] == "20")["locator"]["page"] == 56
    assert next(row for row in figures if row["locator"]["figure"] == "32")["locator"]["page"] == 70
