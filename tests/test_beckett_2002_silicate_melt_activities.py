from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from tools.validate_literature_extracts import check_fidelity_sample_matches


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/beckett-2002-silicate-melt-activities.yaml"
)


def test_every_beckett_fidelity_pin_fails_on_body_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    samples = document["fidelity_samples"]
    assert len(samples) == 23

    for sample in samples:
        assert check_fidelity_sample_matches(document, sample) == []

        mutated = deepcopy(document)
        block = mutated["species"][sample["species"]]
        rows = block.get("observations", []) + block.get("context", [])
        row = next(
            row
            for row in rows
            if row["observation_id"] == sample["observation_id"]
        )
        field = sample["field"]
        original = row["values"][field]
        assert isinstance(original, str)
        row["values"][field] = original + " [mutation]"

        assert check_fidelity_sample_matches(mutated, sample)
