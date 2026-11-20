"""Check that locator validation does not alter migrated observation values."""

from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Iterable
from unittest.mock import patch

from simulator.battery.migrate import (
    REPO_ROOT,
    Migrator,
    discover_extracts,
    to_plain,
)
from simulator.battery.records import Observation
import pytest


_IDENTITY_KEYS = frozenset(
    {
        "id",
        "origin",
        "derived_from",
        "supersedes",
        "superseded_by",
    }
)
_VALUE_SIGNATURE_SHARD_COUNT = 4


def _without_ids_and_locators(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _without_ids_and_locators(item)
            for key, item in value.items()
            if str(key) not in _IDENTITY_KEYS
            and not str(key).endswith(("_id", "_ids", "_locator", "_locators"))
            and str(key) != "locator"
        }
    if isinstance(value, (list, tuple)):
        return [_without_ids_and_locators(item) for item in value]
    return value


def _normalized_value_records(
    observations: Iterable[Observation],
) -> tuple[Counter[tuple[str, str]], dict[tuple[str, str], str]]:
    signatures: Counter[tuple[str, str]] = Counter()
    payloads: dict[tuple[str, str], str] = {}
    for observation in observations:
        plain = to_plain(observation)
        value_record = {
            name: plain[name]
            for name in ("identity", "value", "uncertainty", "point_conditions")
            if name in plain
        }
        normalized = _without_ids_and_locators(value_record)
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
        key = (observation.source_id or "", sha256(payload.encode()).hexdigest())
        signatures[key] += 1
        payloads[key] = payload
    return signatures, payloads


def _value_signature_digest(
    signatures: Counter[tuple[str, str]],
) -> str:
    rows = sorted(
        (source_id, signature, count)
        for (source_id, signature), count in signatures.items()
    )
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


@pytest.mark.parametrize("shard", range(_VALUE_SIGNATURE_SHARD_COUNT))
def test_locator_validation_purity_preserves_extract_values(shard: int) -> None:
    paths = discover_extracts(REPO_ROOT / "data" / "literature" / "extracts")
    shard_paths = [
        path
        for index, path in enumerate(paths)
        if index % _VALUE_SIGNATURE_SHARD_COUNT == shard
    ]

    def migrate_shard(*, validate_locators: bool) -> Counter[tuple[str, str]]:
        migrator = Migrator(REPO_ROOT)
        with patch(
            "simulator.battery.migrate.discover_extracts",
            return_value=shard_paths,
        ):
            if validate_locators:
                migrator.migrate_extracts()
            else:
                with patch.object(
                    Migrator,
                    "_validate_extract_locators",
                    lambda _migrator, _document, *, source_key: None,
                    create=True,
                ):
                    migrator.migrate_extracts()
        migrator.finalize()
        values, _payloads = _normalized_value_records(
            migrator.result.observations.values()
        )
        return values

    baseline = migrate_shard(validate_locators=False)
    canonicalized = migrate_shard(validate_locators=True)
    assert canonicalized == baseline, (
        "locator validation changed values-only extract signatures: "
        f"shard={shard}; "
        f"baseline={_value_signature_digest(baseline)}; "
        f"canonicalized={_value_signature_digest(canonicalized)}; "
        f"baseline_observations={sum(baseline.values())}; "
        f"canonicalized_observations={sum(canonicalized.values())}"
    )
