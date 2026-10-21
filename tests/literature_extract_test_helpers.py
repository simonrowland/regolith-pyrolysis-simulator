import os
from collections.abc import Iterable
from pathlib import Path

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
