from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml"
)
JAYASURIYA_EXTRACT = ROOT / "data/literature/extracts/jayasuriya-2004-iron-mossbauer.yaml"
OBSERVATION_ID = "kress_1988_experimental_apparatus_and_protocol"
EXPECTED_EQUIPMENT_CONTEXTS = (
    (
        "kress-carmichael-1988-iron-oxidation-stoichiometry",
        OBSERVATION_ID,
        "kress_1988_table1_equilibration_series",
    ),
    (
        "jayasuriya-2004-iron-mossbauer",
        "jayasuriya_2004_low_fe_apparatus_and_protocol",
        "jayasuriya_2004_adeu_1pct",
    ),
    (
        "jayasuriya-2004-iron-mossbauer",
        "jayasuriya_2004_high_fe_apparatus_and_protocol",
        "jayasuriya_2004_adeu_high_fe",
    ),
)


def _assert_protocol_pin(row: dict) -> None:
    assert row["locator"]["pdf_page_index"] == "1-2"
    assert row["locator"]["published_page"] == "1268-1269"
    assert row["locator"]["section"] == "Experimental Technique"
    notes = row["values"]["notes"]
    assert "physical PDF page 3 (published p. 1269, Experimental Technique)" in notes
    assert "duplicate runs of between 2 and 12 h duration" in notes
    assert "durations between 5 and 8 h were “considered an optimal compromise.”" in notes
    assert "no effort was made to presaturate the loops in iron" in notes
    assert "often found to be quite significant (20–30% of the amount present)." in notes
    assert "previously saturated in an iron-rich melt and cleaned in 50% HF solution" in notes
    assert "no per-run presaturation or iron-loss assignment" in notes
    assert "temperature and loop Fe presaturation: not stated" not in notes


def test_kress_protocol_quotes_and_scope_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        row
        for rows in document["species"].values()
        for row in rows.get("context", [])
        if row["observation_id"] == OBSERVATION_ID
    )
    _assert_protocol_pin(row)

    mutated = deepcopy(row)
    mutated["values"]["notes"] = mutated["values"]["notes"].replace(
        "durations between 5 and 8 h were “considered an optimal compromise.”",
        "preferred duration omitted",
    )
    with pytest.raises(AssertionError):
        _assert_protocol_pin(mutated)


def _migrate_equipment_contexts(jayasuriya_path: Path = JAYASURIYA_EXTRACT):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator._migrate_extract(jayasuriya_path)
    migrator.finalize()
    return migrator.result


def _assert_equipment_context_links(result) -> None:
    for source_id, observation_id, experiment_name in EXPECTED_EQUIPMENT_CONTEXTS:
        context_id = f"{source_id}::context::{observation_id}"
        experiments = [
            experiment
            for experiment in result.experiments.values()
            if experiment.experiment_id.endswith(f"::experiment::{experiment_name}")
        ]
        assert len(experiments) == 1
        assert experiments[0].equipment_context_id == context_id
        assert any(
            context["context_id"] == context_id
            and context["values"].get("apparatus")
            for rows in result.context_by_work.values()
            for context in rows
        )


def test_three_named_apparatus_contexts_link_to_their_experiments() -> None:
    _assert_equipment_context_links(_migrate_equipment_contexts())


def test_equipment_link_pin_detects_missing_apparatus(tmp_path: Path) -> None:
    document = yaml.safe_load(JAYASURIYA_EXTRACT.read_text(encoding="utf-8"))
    low_fe = next(
        row
        for rows in document["species"].values()
        for row in rows.get("context", [])
        if row["observation_id"] == "jayasuriya_2004_low_fe_apparatus_and_protocol"
    )
    del low_fe["values"]["apparatus"]
    mutated_extract = tmp_path / "jayasuriya-2004-missing-apparatus.yaml"
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_equipment_context_links(_migrate_equipment_contexts(mutated_extract))
