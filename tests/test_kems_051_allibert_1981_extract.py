"""Render pins and isolated migration checks for Allibert et al. (1981) Table II."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-051-allibert-1981.yaml"
RENDERED_TABLE_II = {
    "64.5": ("0.42", "0.02"),
    "57.8": ("0.20", ".05"),
    "54.8": ("0.14", ".06"),
    "49.5": ("0.082", ".20"),
    "43.8": ("0.048", ".41"),
    "41.4": ("0.040", ".25"),
    "35.2": ("0.025", ".50"),
    "80": ("1", ".005"),
}
ALUMINA_CAVEAT = (
    "A critical evaluation of these mass-spectrometric data in the light of emf "
    "measurements reported in the next section and the other data reported in the "
    "literature (Section IV) also indicate that the activities of CaO obtained in "
    "this study are more reliable than the values for Al2O3."
)


def _table_observations(document: dict) -> dict[str, dict]:
    rows = {}
    for formula in ("CaO", "Al2O3"):
        for observation in document["species"][formula]["observations"]:
            if observation["observation_id"].startswith("allibert_1981_table2_"):
                rows[formula] = observation
    return rows


def _printed_grid(document: dict) -> dict[str, tuple[str, str]]:
    rows = _table_observations(document)
    cao_points = rows["CaO"]["values"]["points"]
    alumina_points = rows["Al2O3"]["values"]["points"]
    assert len(cao_points) == len(alumina_points) == 8
    return {
        str(cao["composition_mol_pct_CaO"]): (
            str(cao["activity_as_published"]),
            str(alumina["activity_as_published"]),
        )
        for cao, alumina in zip(cao_points, alumina_points, strict=True)
    }


def test_table_ii_numeric_cells_match_render_and_mutation_is_detected() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _printed_grid(document) == RENDERED_TABLE_II
    rows = _table_observations(document)
    assert rows["CaO"]["observation_id"] == "allibert_1981_table2_cao_activity_kems"
    assert rows["Al2O3"]["observation_id"] == "allibert_1981_table2_alumina_activity_kems"

    mutated = deepcopy(document)
    mutated_rows = _table_observations(mutated)
    mutated_rows["CaO"]["values"]["points"][0]["activity_as_published"] = "0.43"
    assert _printed_grid(mutated) != RENDERED_TABLE_II
    with pytest.raises(AssertionError):
        assert _printed_grid(mutated) == RENDERED_TABLE_II

    # Independent page 309 render checks across the first, middle, and last rows.
    assert RENDERED_TABLE_II["64.5"] == ("0.42", "0.02")
    assert RENDERED_TABLE_II["49.5"] == ("0.082", ".20")
    assert RENDERED_TABLE_II["80"] == ("1", ".005")


def test_table_ii_migrates_all_activity_rows_with_printed_identity_axes() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = _table_observations(document)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 99
    assert not result.validation.hard_issues
    assert len(result.queue) == 52
    table_rows = [
        row
        for row in result.observations.values()
        if row.source_id == "kems-051-allibert-1981"
        and str(row.locator.table) == "II"
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "activity"
    ]
    assert len(table_rows) == 16

    by_species_and_composition = {}
    for row in table_rows:
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("2060")
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "l"
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value.value == "quoted_attributed"
        assert row.locator.published_page == 309
        assert row.locator.pdf_page_index == 2
        composition = row.identity.composition
        assert composition is not None and composition.is_value
        components = dict(composition.value.components)
        assert set(components) == {"CaO", "Al2O3"}
        assert components["CaO"] + components["Al2O3"] == Decimal("1")
        point_composition = row.point_conditions["composition"]
        assert point_composition.state.is_value
        assert point_composition.locator.published_page == 309
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown
        key = (row.identity.species.formula, components["CaO"])
        by_species_and_composition[key] = row

    assert len(by_species_and_composition) == 16
    for composition_pct, (cao_printed, alumina_printed) in RENDERED_TABLE_II.items():
        cao_fraction = Decimal(composition_pct) / Decimal("100")
        assert by_species_and_composition[("CaO", cao_fraction)].value.point == Decimal(cao_printed)
        assert by_species_and_composition[("Al2O3", cao_fraction)].value.point == Decimal(alumina_printed)

    for formula, observation in source_rows.items():
        assert observation["standard_state"]
        for point in observation["values"]["points"]:
            assert point["temperature_K"] == 2060
    assert source_rows["CaO"]["values"]["standard_state_as_printed"] == "solid CaO"
    assert source_rows["Al2O3"]["standard_state_as_printed"] == "solid Al2O3; pure α-Al2O3"
    alumina_points = source_rows["Al2O3"]["values"]["points"]
    assert all(
        point["caveat_as_published"] == ALUMINA_CAVEAT
        and point["caveat_locator"]["published_page"] == 309
        and point["caveat_locator"]["pdf_page_index"] == 2
        for point in alumina_points
    )
    assert all(row.identity.quantity.is_value for row in table_rows)
    al_reference = by_species_and_composition[("Al2O3", Decimal("0.645"))].identity.reference_state
    assert al_reference.is_value
    assert al_reference.value.endmember.polymorph.is_value
    assert al_reference.value.endmember.polymorph.value.value == "alpha"

