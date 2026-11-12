"""Source-grounded checks for the Shornikov et al. 2022 LPSC abstract extract."""

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2022-saratov-chondrule-evaporation.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context() -> list[dict]:
    return _document()["species"]["Saratov_chondrule_melt"]["context"]


def _row(observation_id: str) -> dict:
    for row in _context():
        if row["observation_id"] == observation_id:
            return row
    raise AssertionError(f"missing {observation_id}")


def test_context_only_extract_preserves_printed_composition_and_defers_figures() -> None:
    document = _document()
    assert document["species"]["Saratov_chondrule_melt"]["observations"] == []
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    composition = _row("shornikov_2022_starting_chondrule_composition")["values"]
    assert composition["SiO2"] == "54.44"
    assert composition["TiO2"] == "0.13"
    assert composition["Al2O3"] == "3.20"
    assert composition["FeO"] == "13.17"
    assert composition["MgO"] == "24.98"
    assert composition["CaO"] == "2.24"
    assert composition["Na2O"] == "1.44"
    assert composition["K2O"] == "0.40"
    assert composition["sample_label"].startswith("type IIB")

    experiment = _row("shornikov_2022_experiment_cited_from_yakovlev_1987")["values"]
    assert experiment["temperature_range_K_as_printed"] == "1300–2400"
    assert "tungsten cell" in experiment["apparatus_as_printed"]
    assert experiment["evidence_class"] == "quoted_attributed"

    stages = _row("shornikov_2022_two_stage_evaporation_text")["values"]
    assert stages["stage_1"]["T_range_K_as_printed"] == "1300–1600"
    assert stages["stage_2"]["T_range_K_as_printed"] == "1600–2400"
    assert "approximately 2 wt %" in stages["stage_1"]["mass_loss_as_printed"]

    gamma = _row("shornikov_2022_calculated_alkali_activity_coefficients")["values"]
    assert gamma["range_as_printed"] == "10−12–10−8"
    assert gamma["evidence_class"] == "model_derived"

    residuals = _row("shornikov_2022_discussion_residuals")["values"]
    assert "0.1 wt %" in residuals["possible_causes_as_printed"][0]
    assert "10 wt %" in residuals["possible_causes_as_printed"][1]

    figures = [row for row in _context() if row["type"] == "figure_record"]
    assert [row["locator"]["figure"] for row in figures] == ["1", "2", "3"]
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert figure["values"]["numeric_points"] == "none"
        assert "points" not in figure["values"]
    assert figures[0]["values"]["temperature_as_printed"].startswith(
        "residual melt composition during evaporation at 1406–1569 K"
    )
    assert figures[1]["values"]["temperature_as_printed"] == "1569–2147 K"
    assert figures[2]["values"]["temperature_as_printed"] == "2000 K"


def test_digitising_a_figure_breaks_the_context_only_pin() -> None:
    document = _document()
    figures = [
        row
        for row in document["species"]["Saratov_chondrule_melt"]["context"]
        if row["type"] == "figure_record"
    ]
    figures[0]["values"]["numeric_points"] = ["54.44"]
    with pytest.raises(AssertionError):
        assert all(row["values"]["numeric_points"] == "none" for row in figures)


def test_isolated_migration_has_no_scored_observations_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 9
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
