from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import sys

import pytest

from simulator.battery import migrate
from simulator.battery.enums import Quantity
from simulator.yaml_cache import load_cached_decoded_record


@dataclass(frozen=True)
class _TypedCacheProbe:
    quantity: Quantity
    amount: Decimal
    ratio: Fraction


def _sample_store_paths(root: Path) -> tuple[tuple[Path, bool, str], ...]:
    works = root / "data" / "literature" / "works"
    extracts = root / "data" / "literature" / "extracts-v2"
    observations = root / "data" / "literature" / "observations-v2"
    return (
        (
            next(
                path
                for path in migrate.iter_work_store_paths(works)
                if path.name != "ALIASES.yaml"
            ),
            True,
            "works",
        ),
        (migrate.iter_observation_store_paths(extracts)[0], False, "extracts-v2"),
        (
            migrate.iter_observation_store_paths(observations)[0],
            False,
            "observations-v2",
        ),
    )


def _assert_typed_equal(left: object, right: object) -> None:
    assert type(left) is type(right)
    if is_dataclass(left) and not isinstance(left, type):
        assert left == right
        assert type(left).__dataclass_params__.frozen is type(
            right
        ).__dataclass_params__.frozen
        for item in fields(left):
            _assert_typed_equal(getattr(left, item.name), getattr(right, item.name))
    elif isinstance(left, Mapping):
        assert list(left) == list(right)
        for key in left:
            _assert_typed_equal(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for left_item, right_item in zip(left, right, strict=True):
            _assert_typed_equal(left_item, right_item)
    elif isinstance(left, (set, frozenset)):
        assert len(left) == len(right)
        remaining = list(right)
        for left_item in left:
            match = next(
                (
                    index
                    for index, right_item in enumerate(remaining)
                    if type(left_item) is type(right_item) and left_item == right_item
                ),
                None,
            )
            assert match is not None
            _assert_typed_equal(left_item, remaining.pop(match))
    else:
        assert left == right


def _serialized_plain(value: object, path: Path) -> bytes:
    migrate.dump_yaml(migrate.to_plain(value), path)
    return path.read_bytes()


def test_store_record_decode_reads_only_store_file_and_python_sources(
    monkeypatch,
) -> None:
    """Pin file-read dependencies before decoded records are cached."""
    root = Path(__file__).resolve().parents[2]
    samples = _sample_store_paths(root)
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
    captured: list[Path] = []
    capture_reads = False

    def audit(event: str, arguments: tuple[object, ...]) -> None:
        if not capture_reads or event != "open" or not arguments:
            return
        opened = arguments[0]
        if isinstance(opened, bytes):
            opened = os.fsdecode(opened)
        if isinstance(opened, (str, os.PathLike)):
            captured.append(Path(opened).resolve())

    sys.addaudithook(audit)
    for store_path, work_file, _directory in samples:
        captured.clear()
        capture_reads = True
        try:
            decoder = (
                migrate._decode_work_store_file
                if work_file
                else migrate._decode_observation_store_file
            )
            migrate._load_decoded_store_file(store_path, decoder)
        finally:
            capture_reads = False

        expected_store_path = store_path.resolve()
        unexpected = [
            path
            for path in captured
            if path != expected_store_path and path.suffix != ".py"
        ]
        assert expected_store_path in captured
        assert not unexpected, (
            f"decoder for {store_path} read non-source data files: {unexpected}"
        )


def test_decoded_record_cache_preserves_sample_record_types_and_serialization(
    monkeypatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)

    for index, (store_path, work_file, _directory) in enumerate(
        _sample_store_paths(root)
    ):
        decoder = (
            migrate._decode_work_store_file
            if work_file
            else migrate._decode_observation_store_file
        )
        cold = migrate._load_decoded_store_file(store_path, decoder)
        warm = migrate._load_decoded_store_file(store_path, decoder)

        _assert_typed_equal(cold, warm)
        assert cold == warm
        assert _serialized_plain(cold, tmp_path / f"cold-{index}.yaml") == (
            _serialized_plain(warm, tmp_path / f"warm-{index}.yaml")
        )


def test_decoded_record_cache_preserves_enum_decimal_fraction_and_frozen_record(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    expected = _TypedCacheProbe(
        quantity=Quantity.ACTIVITY,
        amount=Decimal("1.250"),
        ratio=Fraction(2, 3),
    )

    cold = load_cached_decoded_record(
        b"typed probe",
        decoder_identity="typed-probe-v1",
        decode=lambda: expected,
    )
    warm = load_cached_decoded_record(
        b"typed probe",
        decoder_identity="typed-probe-v1",
        decode=lambda: pytest.fail("warm decode unexpectedly ran"),
    )

    _assert_typed_equal(expected, cold)
    _assert_typed_equal(expected, warm)


def test_decoded_record_cache_keys_content_and_decoder_identity(
    monkeypatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    calls: list[bytes] = []

    def decode() -> bytes:
        calls.append(b"decoded")
        return b"decoded"

    first = load_cached_decoded_record(
        b"one", decoder_identity="decoder-a", decode=decode
    )
    second = load_cached_decoded_record(
        b"one", decoder_identity="decoder-a", decode=decode
    )
    different_decoder = load_cached_decoded_record(
        b"one", decoder_identity="decoder-b", decode=decode
    )
    different_content = load_cached_decoded_record(
        b"two", decoder_identity="decoder-a", decode=decode
    )

    assert first == second == different_decoder == different_content == b"decoded"
    assert len(calls) == 3
    assert list((cache_dir / "decoded-records").rglob("*.pickle"))


def test_decoded_record_cache_corruption_is_a_miss(
    monkeypatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    calls = 0

    def decode() -> str:
        nonlocal calls
        calls += 1
        return "decoded"

    assert (
        load_cached_decoded_record(
            b"same", decoder_identity="decoder", decode=decode
        )
        == "decoded"
    )
    entry = next((cache_dir / "decoded-records").rglob("*.pickle"))
    entry.write_bytes(b"not a pickle")

    assert (
        load_cached_decoded_record(
            b"same", decoder_identity="decoder", decode=decode
        )
        == "decoded"
    )
    assert calls == 2


def test_decoded_record_cache_does_not_store_decoder_errors(
    monkeypatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    calls = 0

    def decode() -> object:
        nonlocal calls
        calls += 1
        raise ValueError("same decode error")

    errors: list[tuple[type[BaseException], str]] = []
    for _ in range(2):
        with pytest.raises(ValueError) as raised:
            load_cached_decoded_record(
                b"same", decoder_identity="decoder", decode=decode
            )
        errors.append((type(raised.value), str(raised.value)))

    assert errors == [(ValueError, "same decode error")] * 2
    assert calls == 2
    assert not list((cache_dir / "decoded-records").rglob("*.pickle"))


def test_decoded_record_cache_obeys_shared_disable_setting(
    monkeypatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
    calls = 0
    identity_calls = 0

    def decoder_identity() -> str:
        nonlocal identity_calls
        identity_calls += 1
        return "decoder"

    def decode() -> str:
        nonlocal calls
        calls += 1
        return "decoded"

    for _ in range(2):
        assert (
            load_cached_decoded_record(
                b"same", decoder_identity=decoder_identity, decode=decode
            )
            == "decoded"
        )

    assert calls == 2
    assert identity_calls == 0
    assert not (cache_dir / "decoded-records").exists()


def test_load_migrated_store_matches_cold_and_warm_order(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = Path(__file__).resolve().parents[2]
    store_root = tmp_path / "sample-store"
    samples = _sample_store_paths(source_root)
    for source_path, _work_file, directory in samples:
        destination = store_root / "data" / "literature" / directory
        destination.mkdir(parents=True, exist_ok=True)
        relative = (
            source_path.relative_to(source_root / "data" / "literature" / directory)
        )
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        (destination / relative).symlink_to(source_path)

    monkeypatch.setenv(
        "REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "store-cache")
    )
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    cold = migrate.load_migrated_store(store_root)
    warm = migrate.load_migrated_store(store_root)

    for cold_mapping, warm_mapping in zip(cold, warm, strict=True):
        _assert_typed_equal(cold_mapping, warm_mapping)
        assert list(cold_mapping.items()) == list(warm_mapping.items())
        assert cold_mapping == warm_mapping
    assert _serialized_plain(cold, tmp_path / "store-cold.yaml") == (
        _serialized_plain(warm, tmp_path / "store-warm.yaml")
    )

    extract_path = next(
        path for path, _work_file, directory in samples if directory == "extracts-v2"
    )
    cached_filtered = migrate.load_migrated_store(
        store_root, sources=[extract_path.stem]
    )
    with monkeypatch.context() as uncached_environment:
        uncached_environment.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
        uncached_filtered = migrate.load_migrated_store(
            store_root, sources=[extract_path.stem]
        )
    for cached_mapping, uncached_mapping in zip(
        cached_filtered, uncached_filtered, strict=True
    ):
        _assert_typed_equal(cached_mapping, uncached_mapping)
        assert list(cached_mapping.items()) == list(uncached_mapping.items())
