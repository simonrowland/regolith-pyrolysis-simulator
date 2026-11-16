"""Prove the locator rekey leaves migrated observation values unchanged."""

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
_BASELINE_VALUE_SIGNATURE_SHA256_BY_SHARD = (
    "e9993cacc045ba5b8657c47ba5e9eeaf8b4a33d952adad974f619b6f3fa375af",
    "72e9e7c7f2e63de13c2c6aa6ae08f261ed581ec69983af3289a77e9a1d2c8cf0",
    "d59254bdb812e063d46656b689b0dbb96744372afb6b3f18f20bd2935ce859f0",
    "5e49f28dc1d6bd8080b7188b222df87cd82172c47bec6476ce2fbf4c977a6bdb",
)
_VALUE_SIGNATURE_SHARD_COUNT = len(_BASELINE_VALUE_SIGNATURE_SHA256_BY_SHARD)


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
def test_locator_rekey_preserves_extract_values(shard: int) -> None:
    paths = discover_extracts(REPO_ROOT / "data" / "literature" / "extracts")
    shard_paths = [
        path
        for index, path in enumerate(paths)
        if index % _VALUE_SIGNATURE_SHARD_COUNT == shard
    ]
    migrator = Migrator(REPO_ROOT)
    with patch(
        "simulator.battery.migrate.discover_extracts",
        return_value=shard_paths,
    ):
        migrator.migrate_extracts()
    migrator.finalize()

    migrated_values, _migrated_payloads = _normalized_value_records(
        migrator.result.observations.values()
    )
    digest = _value_signature_digest(migrated_values)
    assert digest == _BASELINE_VALUE_SIGNATURE_SHA256_BY_SHARD[shard], (
        "values-only extract signature differs from the pre-change migration: "
        f"shard={shard}; "
        f"expected={_BASELINE_VALUE_SIGNATURE_SHA256_BY_SHARD[shard]}; "
        f"actual={digest}; observations={sum(migrated_values.values())}"
    )
