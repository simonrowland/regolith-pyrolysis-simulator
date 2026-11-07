from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml"
)
OBSERVATION_ID = "kress_1988_experimental_apparatus_and_protocol"


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
