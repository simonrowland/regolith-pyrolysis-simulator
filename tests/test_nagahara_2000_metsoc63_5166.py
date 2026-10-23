from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/nagahara-2000-metsoc63-5166.yaml"
)


def _contexts(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
    }


def _assert_results(contexts: dict[str, dict]) -> None:
    assert len(contexts) == 7
    assert contexts["nagahara_2000_degree_of_melting"]["values"]["at_1270_C"] == {
        "value": "15–20%",
        "approximate": True,
    }
    assert contexts["nagahara_2000_degree_of_melting"]["values"]["at_1300_C"] == {
        "value": "25–30%",
        "approximate": True,
    }
    assert contexts["nagahara_2000_platinum_cell_dimensions"]["values"]["length"] == {
        "value": "about 1",
        "units": "cm",
        "approximate": True,
    }
    assert contexts["nagahara_2000_melt_separation_at_1400_c"]["values"]["limitation_as_printed"] == "results are not discussed in the present study"
    assert contexts["nagahara_2000_sodium_fractionation_factor"]["values"]["value_range_as_printed"] == "10–100"
    assert contexts["nagahara_2000_potassium_fractionation_factor"]["values"]["value_range_as_printed"] == "2–20"
    assert contexts["nagahara_2000_modeled_alkali_loss"]["values"]["method_class"] == "model_derived"


def test_abstract_results_are_preserved_as_unscored_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = _contexts(document)
    _assert_results(contexts)
    assert all(not species["observations"] for species in document["species"].values())

    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated["species"]["Na"]["context"].clear()
    with pytest.raises(AssertionError):
        _assert_results(_contexts(mutated))

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 7
