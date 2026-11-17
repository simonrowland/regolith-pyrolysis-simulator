"""Engine cache identities cover each pinned package and runtime data input."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from simulator import engine_cache


_KEY_REQUEST = {"temperature_K": 1700.0}


def _input_key(input_ref) -> tuple[str, str, str | None]:
    return input_ref.path, input_ref.kind, input_ref.selector


def _keys() -> dict[str, bytes]:
    return {
        engine: engine_cache.engine_cache_key(
            engine, version, _KEY_REQUEST
        )
        for engine, version in engine_cache.ENGINE_CHEMISTRY_VERSION.items()
    }


def _changed_source_bytes(input_ref, original: bytes) -> bytes:
    if input_ref.kind == "data":
        assert original
        return bytes((original[0] ^ 1,)) + original[1:]

    if input_ref.kind == "pyproject-dependency":
        match = re.search(
            rb'(?m)^\s*"openimcc @ [^"]+@([0-9a-f]{40})"', original
        )
    elif input_ref.kind == "engine-lock":
        engine_name = input_ref.selector.split(".", 1)[1]
        escaped_engine = re.escape(engine_name.encode())
        section = re.search(
            rb"(?ms)^\[engines\." + escaped_engine + rb"\]\s*$"
            rb"(.*?)(?=^\[engines\.(?!" + escaped_engine + rb"(?:\.|\]))|\Z)",
            original,
        )
        assert section is not None, input_ref
        match = re.search(
            rb'(?m)^\s*(?:commit|reference_sha256|sha256)\s*=\s*"([0-9a-f]{40,64})"',
            section.group(1),
        )
        assert match is not None, input_ref
        start, stop = section.start(1) + match.start(1), section.start(1) + match.end(1)
        token = original[start:stop]
        altered = (b"0" if token[0:1] != b"0" else b"1") + token[1:]
        return original[:start] + altered + original[stop:]
    else:
        raise AssertionError(f"unknown cache input kind: {input_ref.kind}")

    assert match is not None, input_ref
    start, stop = match.span(1)
    token = original[start:stop]
    altered = (b"0" if token[0:1] != b"0" else b"1") + token[1:]
    return original[:start] + altered + original[stop:]


def _assert_input_byte_changes_only_consumers(
    input_ref,
    expected_consumers: set[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = engine_cache._ENGINE_CACHE_ROOT
    paths = {
        ref.path
        for refs in engine_cache.ENGINE_CACHE_INPUTS.values()
        for ref in refs
    } | {input_ref.path}
    copied_root = tmp_path / "identity-inputs"
    for relative_path in paths:
        source = source_root / relative_path
        target = copied_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    source = source_root / input_ref.path
    target = copied_root / input_ref.path
    original = source.read_bytes()
    target.write_bytes(_changed_source_bytes(input_ref, original))
    original_root = engine_cache._ENGINE_CACHE_ROOT
    identity_cache = engine_cache._engine_cache_input_identity_digest
    try:
        identity_cache.cache_clear()
        before = _keys()
        monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", copied_root)
        identity_cache.cache_clear()
        after = _keys()
        changed = {engine for engine in before if before[engine] != after[engine]}
        assert changed == expected_consumers, (
            f"changing one byte of {input_ref.path} ({input_ref.selector}) "
            f"changed keys for {sorted(changed)}, expected {sorted(expected_consumers)}"
        )
    finally:
        monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", original_root)
        identity_cache.cache_clear()


def test_each_engine_identity_input_is_present_and_byte_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = engine_cache.ENGINE_CACHE_INPUTS
    assert set(inputs) == set(engine_cache.ENGINE_CHEMISTRY_VERSION)

    consumers_by_input = {}
    for engine, refs in inputs.items():
        assert refs, engine
        for input_ref in refs:
            assert (engine_cache._ENGINE_CACHE_ROOT / input_ref.path).is_file(), (
                engine,
                input_ref.path,
            )
            consumers_by_input.setdefault(
                _input_key(input_ref), (input_ref, set())
            )[1].add(engine)

    for index, (input_ref, consumers) in enumerate(consumers_by_input.values()):
        _assert_input_byte_changes_only_consumers(
            input_ref,
            consumers,
            tmp_path / str(index),
            monkeypatch,
        )


def test_removing_an_input_from_one_engine_is_caught_by_byte_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = "internal-analytical"
    input_ref = next(
        ref
        for ref in engine_cache.ENGINE_CACHE_INPUTS[engine]
        if ref.path == "data/vapor_pressures.yaml"
    )
    expected_consumers = {
        name
        for name, refs in engine_cache.ENGINE_CACHE_INPUTS.items()
        if any(_input_key(ref) == _input_key(input_ref) for ref in refs)
    }
    monkeypatch.setitem(
        engine_cache.ENGINE_CACHE_INPUTS,
        engine,
        tuple(
            ref
            for ref in engine_cache.ENGINE_CACHE_INPUTS[engine]
            if _input_key(ref) != _input_key(input_ref)
        ),
    )

    with pytest.raises(AssertionError, match="changed keys for"):
        _assert_input_byte_changes_only_consumers(
            input_ref,
            expected_consumers,
            tmp_path,
            monkeypatch,
        )


def test_invalid_pin_metadata_fails_with_typed_identity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text('project = "not a table"\n')
    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", tmp_path)
    engine_cache._engine_cache_input_identity_digest.cache_clear()

    with pytest.raises(
        engine_cache.EngineCacheInputIdentityError,
        match="project is not a TOML table",
    ):
        engine_cache.engine_cache_key(
            "openimcc", engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"], {}
        )
