import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from simulator.battery.enums import Phase, ReferenceStateConvention, ValueKind
from simulator.battery.records import Observation
from tests.helpers.corpus_paths import regolith_corpus_root


def corpus_root() -> Path:
    return regolith_corpus_root()


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


def assert_wustite_reference_state(reference_state) -> None:
    assert reference_state is not None and reference_state.is_value
    standard_state = reference_state.value
    assert standard_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert standard_state.endmember.formula == "Fe(1-x)O"
    assert standard_state.endmember.phase.is_value
    assert standard_state.endmember.phase.value is Phase.L
    assert standard_state.component_basis == "Fe(1-x)O"
    assert standard_state.reference_pressure_bar is None


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
    pdf = corpus_root() / "raw" / source_id / f"{source_id}.pdf"
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
