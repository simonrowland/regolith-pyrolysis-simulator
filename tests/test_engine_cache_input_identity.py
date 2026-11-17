"""Engine cache identities cover each pinned package and runtime data input."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from simulator import engine_cache


_KEY_REQUEST = {"temperature_K": 1700.0}

# Independent contract reviewed against the per-engine live file-access trace.
# Keep it separate from ENGINE_CACHE_INPUTS so dropping a production edge fails.
_EXPECTED_ENGINE_INPUTS = {
    "openimcc": {
        ("pyproject.toml", "pyproject-dependency", "openimcc"),
    },
    "internal-analytical": {
        ("pyproject.toml", "pyproject-dependency", "openimcc"),
        ("data/vapor_pressures.yaml", "data", None),
        ("data/vapor_pressures.yaml", "catalog-thermo-dependencies", None),
        ("data/setpoints.yaml", "data", None),
        ("data/species_catalog.yaml", "data", None),
    },
    "vaporock": {
        ("engines/engines.lock.toml", "engine-lock", "engines.vaporock"),
        ("engines/engines.lock.toml", "engine-lock", "engines.thermoengine"),
    },
    "magemin": {
        ("engines/engines.lock.toml", "engine-lock", "engines.magemin"),
    },
    "alphamelts": {
        ("engines/engines.lock.toml", "engine-lock", "engines.alphamelts"),
        ("engines/engines.lock.toml", "engine-lock", "engines.petthermotools"),
        ("engines/engines.lock.toml", "engine-lock", "engines.vaporock"),
        ("engines/engines.lock.toml", "engine-lock", "engines.thermoengine"),
        ("pyproject.toml", "pyproject-dependency", "openimcc"),
        ("data/vapor_pressures.yaml", "data", None),
        (
            "data/literature/extracts/nasa-cea-thermo.yaml",
            "data",
            None,
        ),
        ("data/vapor_pressures.yaml", "catalog-thermo-dependencies", None),
    },
    "thermoengine": {
        ("engines/engines.lock.toml", "engine-lock", "engines.thermoengine"),
        ("engines/engines.lock.toml", "engine-lock", "engines.vaporock"),
        ("data/vapor_pressures.yaml", "data", None),
        (
            "data/literature/extracts/nasa-cea-thermo.yaml",
            "data",
            None,
        ),
        ("data/vapor_pressures.yaml", "catalog-thermo-dependencies", None),
    },
}

_EXPECTED_CATALOG_EXTRACT_PATHS = {
    "data/literature/extracts/nasa-cea-thermo.yaml",
    "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
}

_EXPECTED_NUMERIC_TRACE_PATHS = {
    "openimcc": set(),
    "internal-analytical": {
        "data/vapor_pressures.yaml",
        "data/setpoints.yaml",
        "data/species_catalog.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
    "vaporock": set(),
    "magemin": set(),
    "alphamelts": {
        "data/vapor_pressures.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
    "thermoengine": {
        "data/vapor_pressures.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
}


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
        if input_ref.path == "data/vapor_pressures.yaml":
            payload = yaml.safe_load(original)
            payload["cache_identity_probe"] = "changed"
            return yaml.safe_dump(payload, sort_keys=False).encode()
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


def _catalog_extract_paths(root: Path) -> tuple[Path, ...]:
    from simulator.vapour_rail import catalog
    from simulator.yaml_cache import load_cached_safe_yaml

    payload = load_cached_safe_yaml((root / "data/vapor_pressures.yaml").read_bytes())
    dependencies = catalog._thermo_extract_dependencies(payload)
    paths = tuple(
        root / "data/literature/extracts" / f"{source_id}.yaml"
        for source_id in dependencies
    )
    assert {
        path.relative_to(root).as_posix() for path in paths
    } == _EXPECTED_CATALOG_EXTRACT_PATHS
    return paths


def _changed_catalog_extract(raw: bytes) -> bytes:
    payload = yaml.safe_load(raw)
    payload["cache_identity_probe"] = "changed"
    return yaml.safe_dump(payload, sort_keys=False).encode()


def _assert_input_byte_changes_only_consumers(
    input_ref,
    expected_consumers: set[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    catalog_extract: Path | None = None,
) -> None:
    source_root = engine_cache._ENGINE_CACHE_ROOT
    catalog_paths = _catalog_extract_paths(source_root)
    paths = {
        ref.path
        for refs in engine_cache.ENGINE_CACHE_INPUTS.values()
        for ref in refs
    } | {input_ref.path} | {
        path.relative_to(source_root).as_posix() for path in catalog_paths
    }
    copied_root = tmp_path / "identity-inputs"
    for relative_path in paths:
        source = source_root / relative_path
        target = copied_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    source = source_root / input_ref.path
    target = copied_root / input_ref.path
    original = source.read_bytes()
    if input_ref.kind == "catalog-thermo-dependencies":
        source = catalog_extract or next(
            path for path in catalog_paths if path.name == "nasa-cea-thermo.yaml"
        )
        target = copied_root / source.relative_to(source_root)
        target.write_bytes(_changed_catalog_extract(source.read_bytes()))
    else:
        target.write_bytes(_changed_source_bytes(input_ref, original))
    original_root = engine_cache._ENGINE_CACHE_ROOT
    identity_cache = engine_cache._engine_cache_input_identity_digest
    from simulator.vapour_rail import catalog

    original_extract_dir = catalog._THERMO_EXTRACT_DIR
    try:
        identity_cache.cache_clear()
        before = _keys()
        monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", copied_root)
        monkeypatch.setattr(
            catalog,
            "_THERMO_EXTRACT_DIR",
            copied_root / "data/literature/extracts",
        )
        identity_cache.cache_clear()
        after = _keys()
        changed = {engine for engine in before if before[engine] != after[engine]}
        assert changed == expected_consumers, (
            f"changing one byte of {input_ref.path} ({input_ref.selector}) "
            f"changed keys for {sorted(changed)}, expected {sorted(expected_consumers)}"
        )
    finally:
        monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", original_root)
        monkeypatch.setattr(catalog, "_THERMO_EXTRACT_DIR", original_extract_dir)
        catalog._THERMO_EXTRACT_PARSE_CACHE.clear()
        catalog._THERMO_OBSERVATION_CACHE.clear()
        identity_cache.cache_clear()


def test_each_engine_identity_input_is_present_and_byte_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = engine_cache.ENGINE_CACHE_INPUTS
    assert set(inputs) == set(_EXPECTED_ENGINE_INPUTS)
    assert set(inputs) == set(engine_cache.ENGINE_CHEMISTRY_VERSION)

    consumers_by_input = {}
    for engine, refs in _EXPECTED_ENGINE_INPUTS.items():
        for expected_ref in refs:
            consumers_by_input.setdefault(expected_ref, set()).add(engine)
    catalog_extract_consumers = {
        engine
        for engine, refs in _EXPECTED_ENGINE_INPUTS.items()
        if ("data/vapor_pressures.yaml", "catalog-thermo-dependencies", None) in refs
    }
    extract_paths = {
        path.relative_to(engine_cache._ENGINE_CACHE_ROOT).as_posix()
        for path in _catalog_extract_paths(engine_cache._ENGINE_CACHE_ROOT)
    }
    for input_key in consumers_by_input:
        if input_key[1] == "data" and input_key[0] in extract_paths:
            consumers_by_input[input_key].update(catalog_extract_consumers)
    for engine, refs in inputs.items():
        assert refs, engine
        assert {_input_key(ref) for ref in refs} == _EXPECTED_ENGINE_INPUTS[engine], engine
        for input_ref in refs:
            assert (engine_cache._ENGINE_CACHE_ROOT / input_ref.path).is_file(), (
                engine,
                input_ref.path,
            )

    catalog_paths = _catalog_extract_paths(engine_cache._ENGINE_CACHE_ROOT)
    assert catalog_paths
    assert all(path.is_file() for path in catalog_paths)

    for index, (input_key, consumers) in enumerate(consumers_by_input.items()):
        input_ref = next(
            ref
            for refs in inputs.values()
            for ref in refs
            if _input_key(ref) == input_key
        )
        if input_ref.kind == "catalog-thermo-dependencies":
            for extract_index, extract_path in enumerate(
                _catalog_extract_paths(engine_cache._ENGINE_CACHE_ROOT)
            ):
                _assert_input_byte_changes_only_consumers(
                    input_ref,
                    consumers,
                    tmp_path / str(index) / str(extract_index),
                    monkeypatch,
                    catalog_extract=extract_path,
                )
        else:
            _assert_input_byte_changes_only_consumers(
                input_ref,
                consumers,
                tmp_path / str(index),
                monkeypatch,
            )


def test_every_input_association_is_required_by_independent_mutation_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = engine_cache.ENGINE_CACHE_INPUTS
    for engine, expected_refs in _EXPECTED_ENGINE_INPUTS.items():
        for expected_ref in expected_refs:
            input_ref = next(
                ref for ref in original[engine] if _input_key(ref) == expected_ref
            )
            catalog_ref = (
                "data/vapor_pressures.yaml",
                "catalog-thermo-dependencies",
                None,
            )
            remove_refs = {expected_ref}
            catalog_extract = None
            if expected_ref == catalog_ref:
                for extract_path in _catalog_extract_paths(
                    engine_cache._ENGINE_CACHE_ROOT
                ):
                    remove_refs = {catalog_ref}
                    extract_relative = extract_path.relative_to(
                        engine_cache._ENGINE_CACHE_ROOT
                    ).as_posix()
                    direct_extract_ref = (extract_relative, "data", None)
                    if direct_extract_ref in _EXPECTED_ENGINE_INPUTS[engine]:
                        remove_refs.add(direct_extract_ref)
                    _assert_removing_engine_input_fails(
                        engine,
                        remove_refs,
                        input_ref,
                        tmp_path / engine / input_ref.kind / extract_path.stem,
                        monkeypatch,
                        catalog_extract=extract_path,
                    )
                continue
            if (
                expected_ref[1] == "data"
                and expected_ref[0]
                in {
                    path.relative_to(engine_cache._ENGINE_CACHE_ROOT).as_posix()
                    for path in _catalog_extract_paths(engine_cache._ENGINE_CACHE_ROOT)
                }
                and catalog_ref in _EXPECTED_ENGINE_INPUTS[engine]
            ):
                # The catalog dependency mutation above covers this file with
                # both key paths removed for the target engine.
                continue
            _assert_removing_engine_input_fails(
                engine,
                remove_refs,
                input_ref,
                tmp_path / engine / input_ref.kind / str(len(expected_refs)),
                monkeypatch,
                catalog_extract=catalog_extract,
            )


def _assert_removing_engine_input_fails(
    engine: str,
    remove_refs: set[tuple[str, str, str | None]],
    input_ref,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    catalog_extract: Path | None = None,
) -> None:
    original = engine_cache.ENGINE_CACHE_INPUTS
    expected_consumers = {
        name
        for name, refs in _EXPECTED_ENGINE_INPUTS.items()
        if any(ref in refs for ref in remove_refs)
    }
    # A catalog-derived extract is also a consumer for explicit data refs to
    # the same file; preserve that independent physical-input expectation.
    if input_ref.kind == "data" and input_ref.path in {
        path.relative_to(engine_cache._ENGINE_CACHE_ROOT).as_posix()
        for path in _catalog_extract_paths(engine_cache._ENGINE_CACHE_ROOT)
    }:
        expected_consumers.update(
            name
            for name, refs in _EXPECTED_ENGINE_INPUTS.items()
            if ("data/vapor_pressures.yaml", "catalog-thermo-dependencies", None)
            in refs
        )
    engine_cache.ENGINE_CACHE_INPUTS = {
        **original,
        engine: tuple(
            ref for ref in original[engine] if _input_key(ref) not in remove_refs
        ),
    }
    try:
        with pytest.raises(AssertionError, match="changed keys for"):
            _assert_input_byte_changes_only_consumers(
                input_ref,
                expected_consumers,
                tmp_path,
                monkeypatch,
                catalog_extract=catalog_extract,
            )
    finally:
        engine_cache.ENGINE_CACHE_INPUTS = original
        engine_cache._engine_cache_input_identity_digest.cache_clear()


def test_removing_an_input_from_one_engine_is_caught_by_byte_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = "internal-analytical"
    expected_ref = ("data/vapor_pressures.yaml", "data", None)
    input_ref = next(
        ref
        for ref in engine_cache.ENGINE_CACHE_INPUTS[engine]
        if _input_key(ref) == expected_ref
    )
    expected_consumers = {
        name
        for name, refs in _EXPECTED_ENGINE_INPUTS.items()
        if expected_ref in refs
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


def test_internal_analytical_thermo_extract_changes_pressure_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.vapour_rail import catalog

    source = (
        engine_cache._ENGINE_CACHE_ROOT
        / "data/literature/extracts/nasa-cea-thermo.yaml"
    )
    original_read_bytes = Path.read_bytes
    parsed = yaml.safe_load(original_read_bytes(source))
    target = next(
        observation
        for row in parsed["species"].values()
        for observation in row["observations"]
        if observation.get("observation_id") == "cea_Si_gibbs"
    )
    for segment in target["values"]["segments"]:
        segment["b1"] += 1000.0
    altered = yaml.safe_dump(parsed, sort_keys=False).encode()
    reads = []

    def reset_catalog() -> None:
        catalog.clear_vapor_pressure_view_caches()
        catalog.clear_vapour_rail_compile_cache()
        catalog._THERMO_EXTRACT_PARSE_CACHE.clear()
        catalog._THERMO_OBSERVATION_CACHE.clear()
        engine_cache._engine_cache_input_identity_digest.cache_clear()

    def solve():
        core = battery._new_internal_analytical_core()
        with engine_cache._without_engine_cache():
            return battery._internal_analytical_vapor_pressure_adapter(
                core=core,
                temperature_C=1526.85,
                pressure_bar=1.0,
                composition_kg=None,
                composition_mol={"SiO2": 0.6, "MgO": 0.4},
                fO2_log=-8.0,
                po2_request=battery.Po2Request(
                    mode="commanded", po2_bar=1.0e-8
                ),
                include_diagnostic_shadows=False,
            )

    try:
        reset_catalog()
        before = solve()
        key_before = engine_cache.engine_cache_key(
            "internal-analytical", 2, {"temperature_K": 1800.0}
        )

        def changed_bytes(path, *args, **kwargs):
            if path == source:
                reads.append(str(path))
                return altered
            return original_read_bytes(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", changed_bytes)
        reset_catalog()
        after = solve()
        key_after = engine_cache.engine_cache_key(
            "internal-analytical", 2, {"temperature_K": 1800.0}
        )
        assert reads, "coefficient mutation did not reach the real catalog loader"
        changed = {
            name: (value, after.vapor_pressures_Pa.get(name))
            for name, value in before.vapor_pressures_Pa.items()
            if after.vapor_pressures_Pa.get(name) != value
        }
        assert changed, "coefficient mutation did not change a returned pressure"
        assert key_before != key_after, (
            "stale key despite changed live outputs: "
            + json.dumps(changed, sort_keys=True)
        )
    finally:
        reset_catalog()


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
