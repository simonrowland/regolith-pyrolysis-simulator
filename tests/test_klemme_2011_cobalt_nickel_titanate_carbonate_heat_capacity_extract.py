"""Source-pinned checks for Klemme et al. (2011) calorimetry."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/klemme-2011-cobalt-nickel-titanate-carbonate-heat-capacity.yaml"
SOURCE_ID = "klemme-2011-cobalt-nickel-titanate-carbonate-heat-capacity"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations_by_id(document: dict) -> dict:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _context_by_id(document: dict) -> dict:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
    }


def _assert_source_pins(document: dict) -> None:
    assert document["source"]["corpus_sha256"] == (
        "de63741dd94f4f557cfb66a5b3f8a7f323ce93dc25b0caec4f67063c3d8959de"
    )
    observations = _observations_by_id(document)
    assert len(observations) == 14

    expected_cp = {
        "klemme_2011_nitio3_table1_series1_cp": (180, "2.70", "0.02", "303.1"),
        "klemme_2011_nitio3_table1_series2_cp": (75, "2.17", "0.010", "40.8"),
        "klemme_2011_cotio3_table2_series1_cp": (210, "2.17", "0.002", "304.3"),
        "klemme_2011_cotio3_table2_series2_cp": (90, "2.24", "0.0018", "61.2"),
        "klemme_2011_coco3_table3_series1_cp": (170, "2.21", "0.02", "304.0"),
    }
    for observation_id, (count, first_t, first_cp, last_t) in expected_cp.items():
        observation = observations[observation_id]
        assert observation["phase"] == "cr"
        assert observation["units"] == "J/mol K"
        assert observation["published_unit_and_molar_basis"] == (
            "J mol-1 K-1, per mole of formula unit"
        )
        points = observation["values"]["series"]
        assert len(points) == count
        assert (points[0]["T_as_printed"], points[0]["cp_as_printed"]) == (
            first_t,
            first_cp,
        )
        assert points[-1]["T_as_printed"] == last_t
    assert observations["klemme_2011_nitio3_table1_series1_cp"]["values"]["series"][20][
        "cp_as_printed"
    ] == "9.76"

    expected_entropy = {
        "klemme_2011_nitio3_standard_entropy_298_15k": ("90.9", "0.7"),
        "klemme_2011_cotio3_standard_entropy_298_15k": ("94.4", "0.8"),
        "klemme_2011_coco3_standard_entropy_298_15k": ("88.9", "0.7"),
    }
    for observation_id, (value, uncertainty) in expected_entropy.items():
        observation = observations[observation_id]
        assert observation["values"]["entropy_as_printed"] == value
        assert observation["uncertainty"]["value_J_per_mol_K"] == float(uncertainty)
        assert observation["uncertainty"]["uncertainty_as_printed"].startswith("± ")

    expected_quotes = {
        "klemme_2011_nitio3_s298_quote_38": ("80.1", "3.7", "[38]"),
        "klemme_2011_cotio3_s298_quote_38": ("96.9", None, "[38]"),
        "klemme_2011_coco3_s298_quote_37": ("88.7", "1.7", "[37]"),
    }
    for observation_id, (value, uncertainty, attribution) in expected_quotes.items():
        observation = observations[observation_id]
        quoted_point = observation["values"]["series"][0]
        assert observation["units"] == "J/mol K"
        assert observation["published_unit_and_molar_basis"] == (
            "J mol-1 K-1, per mole of formula unit"
        )
        assert observation["values"]["molar_basis_as_published"] == (
            "per mole of formula unit"
        )
        assert quoted_point["entropy_as_printed"] == value
        assert observation["values"]["attribution"] == attribution
        assert observation["values"]["method_class"] == "quoted_attributed"
        if uncertainty is None:
            assert observation["uncertainty"]["reported_status"] == "not_reported"
            assert "value_J_per_mol_K" not in observation["uncertainty"]
        else:
            assert observation["uncertainty"]["uncertainty_as_printed"] == uncertainty

    contexts = _context_by_id(document)
    figures = [record for record in contexts.values() if record["type"] == "figure_record"]
    assert len(figures) == 4
    assert all(
        figure["values"]["digitisation"] == "deferred to a figure pass"
        and figure["values"]["numeric_points"] == "none"
        for figure in figures
    )
    assert contexts["klemme_2011_coco3_heat_capacity_anomaly_peak_text"]["values"][
        "approximate"
    ] is True
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_extract_preserves_printed_values_and_validates() -> None:
    _assert_source_pins(_document())


def test_mutating_a_printed_heat_capacity_cell_breaks_the_source_pins() -> None:
    mutated = deepcopy(_document())
    row = _observations_by_id(mutated)["klemme_2011_cotio3_table2_series2_cp"]
    row["values"]["series"][0]["cp_as_printed"] = "0.0019"
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_isolated_migration_keeps_species_values_and_temperatures() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = migrator.result.observations.values()
    assert len(migrator.result.observations) == 827
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    assert all(
        observation.identity.species.formula
        in {"NiTiO3", "CoTiO3", "CoCO3"}
        for observation in observations
    )
    assert all(
        observation.identity.temperature_K.tag.value == "value"
        for observation in observations
    )
    assert {
        formula: sum(
            observation.identity.species.formula == formula
            for observation in observations
        )
        for formula in ("NiTiO3", "CoTiO3", "CoCO3")
    } == {"NiTiO3": 289, "CoTiO3": 334, "CoCO3": 204}


def test_all_locators_match_physical_pdf_pages() -> None:
    if not PDF.is_file():
        pytest.skip(f"corpus source PDF is absent: {PDF}")

    document = _document()
    locators = list(iter_locators(document))
    assert locators
    for locator in locators:
        page = locator["page"]
        assert locator["pdf_page_index"] == page - 1
        citation = locator.get("table") or locator.get("figure")
        if citation is None:
            continue
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        label = "Table" if locator.get("table") else r"Fig(?:ure)?[.]?"
        assert re.search(rf"{label}\s*{re.escape(str(citation))}", result.stdout, re.I), (
            page,
            citation,
        )
