import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from simulator.battery.enums import ValueKind
from simulator.battery.records import Observation


def corpus_root() -> Path:
    configured = os.environ.get("REGOLITH_CORPUS_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "Repos" / "regolith-corpus"


def assert_no_unavailable_observation_values(
    observations: Iterable[Observation],
) -> None:
    """Ensure F21 scored rows did not lose numeric values during migration.

    These sources keep explicit refusals and unknown values in context or
    conditions, so no scored observation is intentionally unavailable.
    """
    unavailable_ids = [
        observation.observation_id
        for observation in observations
        if observation.value.kind is ValueKind.UNAVAILABLE
    ]
    assert not unavailable_ids, (
        f"migrated observations are unavailable: {unavailable_ids}"
    )


def iter_locators(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "locator" and isinstance(child, dict):
                yield child
            yield from iter_locators(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_locators(child)


def source_page_text(source_id: str, page: int) -> str:
    corpus_root = Path(
        os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
    )
    pdf = corpus_root / "raw" / source_id / f"{source_id}.pdf"
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
