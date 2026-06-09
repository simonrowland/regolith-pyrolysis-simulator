from __future__ import annotations

from pathlib import Path

import pytest
import yaml


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUMP_PATH = DATA_DIR / "rump_compositions.yaml"
FEEDSTOCKS_PATH = DATA_DIR / "feedstocks.yaml"

REPRESENTATIVE_FEEDSTOCKS = {
    "lunar_mare_low_ti",
    "lunar_highland",
    "lunar_pkt_kreep_average",
    "ci_carbonaceous_chondrite",
    "mars_basalt",
}

VOLATILE_OR_NON_OXIDE_SOURCE_KEYS = {
    "H2O",
    "S",
    "carbonaceous_organic",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _source_oxide_basis(feedstock: dict) -> dict[str, float]:
    composition = feedstock.get("composition_wt_pct") or {}
    oxides = {
        str(species): float(value)
        for species, value in composition.items()
        if species not in VOLATILE_OR_NON_OXIDE_SOURCE_KEYS
        and "O" in str(species)
    }
    total = sum(oxides.values())
    assert total > 0.0
    return {
        species: value / total * 100.0
        for species, value in oxides.items()
    }


def _total_fe_oxide(composition: dict[str, float]) -> float:
    return float(composition.get("FeO", 0.0)) + float(
        composition.get("Fe2O3", 0.0)
    )


def test_rump_composition_schema_loads_and_vectors_are_normalized():
    data = _load_yaml(RUMP_PATH)

    assert data["source"] == "sim-derived"
    assert data["basis"] == "oxide_wt_pct_normalized_volatiles_free"
    assert data["c5_enabled"] is False
    assert data["normalization_exclusions"]
    assert data["target_species_ladder"]["stage_1"] == ["Fe"]

    entries = data["entries"]
    assert {entry["feedstock_id"] for entry in entries} == REPRESENTATIVE_FEEDSTOCKS

    for entry in entries:
        assert entry["source"] == "sim-derived"
        assert entry["basis"] == data["basis"]
        assert entry["recipe_id"] == data["recipe_id"]
        assert entry["extraction_sequence_version"] == data[
            "extraction_sequence_version"
        ]
        assert entry["c5_enabled"] is False
        assert entry["source_run_id"].startswith("sim-rump-")

        vector = entry["oxide_wt_pct"]
        assert vector
        assert sum(vector.values()) == pytest.approx(100.0, abs=0.01)

        classification = entry["classification"]
        assert classification["status"] in {"match", "no-match", "ambiguous"}
        assert "matched_ceramic" in classification
        assert "service_temp" in classification
        assert "kind" in classification["service_temp"]


def test_representative_rumps_are_enriched_and_depleted_on_matching_basis():
    feedstocks = _load_yaml(FEEDSTOCKS_PATH)
    data = _load_yaml(RUMP_PATH)

    for entry in data["entries"]:
        feedstock = feedstocks[entry["feedstock_id"]]
        raw_source = feedstock.get("composition_wt_pct") or {}
        oxide_source = _source_oxide_basis(feedstock)
        rump = entry["oxide_wt_pct"]

        assert rump["CaO"] > float(raw_source.get("CaO", 0.0))
        assert rump["Al2O3"] > float(raw_source.get("Al2O3", 0.0))
        for alkali in ("Na2O", "K2O"):
            if oxide_source.get(alkali, 0.0) > 0.0:
                assert rump.get(alkali, 0.0) < oxide_source[alkali]
            else:
                assert rump.get(alkali, 0.0) == pytest.approx(0.0)
        assert _total_fe_oxide(rump) < _total_fe_oxide(oxide_source)


def test_remaining_feedstocks_are_todo_without_fabricated_vectors():
    feedstocks = _load_yaml(FEEDSTOCKS_PATH)
    data = _load_yaml(RUMP_PATH)

    represented = {entry["feedstock_id"] for entry in data["entries"]}
    todos = data["todo_feedstocks"]
    todo_ids = {item["feedstock_id"] for item in todos}

    assert represented == REPRESENTATIVE_FEEDSTOCKS
    assert todo_ids == set(feedstocks) - REPRESENTATIVE_FEEDSTOCKS

    for item in todos:
        assert item["status"] == "TODO"
        assert "oxide_wt_pct" not in item
        assert "source_run_id" not in item
