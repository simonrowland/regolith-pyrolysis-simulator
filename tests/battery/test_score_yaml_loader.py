from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)

from simulator.battery.migrate import _iter_score_loaded_yaml_paths
from simulator.yaml_cache import (
    YAML12SafeLoader,
    _assert_type_exact_equal,
    load_cached_safe_yaml,
)


class _PythonYAML12SafeLoader(yaml.SafeLoader):
    """Pure-Python parser with the production YAML 1.2 resolver table."""

    yaml_implicit_resolvers = YAML12SafeLoader.yaml_implicit_resolvers


def test_score_yaml_cache_preserves_every_store_schema(
    monkeypatch, tmp_path: Path
) -> None:
    """Compare cached, C, and Python parses for every scorer-loaded schema.

    The smallest file for each schema version in the scorer's unfiltered YAML
    path set is checked against cold and warm cache reads. The synthetic
    document below pins bool/int/float/date/null, ``!!pairs``, and duplicate-key
    behavior.

    Run ``python scripts/check_yaml_cache_parity.py`` on a studio and after
    store regeneration for the equivalent type-exact C-loader check over every
    store file.
    """

    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    assert yaml.CSafeLoader in YAML12SafeLoader.__mro__

    root = Path(__file__).resolve().parents[2]
    def schema_version(path: Path) -> str | None:
        depth = 0
        root_key_expected = False
        schema_value_expected = False
        with path.open("rb") as stream:
            events = iter(yaml.parse(stream, Loader=YAML12SafeLoader))
            for event in events:
                if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                    depth += 1
                    if depth == 1 and isinstance(event, MappingStartEvent):
                        root_key_expected = True
                    continue
                if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                    depth -= 1
                    if depth == 1:
                        root_key_expected = True
                    continue
                if depth != 1:
                    continue
                if schema_value_expected:
                    return event.value if isinstance(event, ScalarEvent) else None
                if root_key_expected:
                    if not isinstance(event, ScalarEvent):
                        root_key_expected = False
                        continue
                    root_key_expected = False
                    schema_value_expected = event.value == "schema_version"
                elif isinstance(event, (ScalarEvent, AliasEvent)):
                    root_key_expected = True
        return None

    smallest_path_by_schema: dict[str, Path] = {}
    loaded_schema_versions: set[str] = set()
    for path in _iter_score_loaded_yaml_paths(root):
        version = schema_version(path)
        if version is None:
            continue
        loaded_schema_versions.add(version)
        previous = smallest_path_by_schema.get(version)
        if previous is None or (path.stat().st_size, path.as_posix()) < (
            previous.stat().st_size,
            previous.as_posix(),
        ):
            smallest_path_by_schema[version] = path

    assert set(smallest_path_by_schema) == loaded_schema_versions
    assert smallest_path_by_schema, "scorer loaded no schema-versioned YAML documents"
    for expected_version, path in sorted(smallest_path_by_schema.items()):
        raw = path.read_bytes()
        cold_cache = load_cached_safe_yaml(raw)
        warm_cache = load_cached_safe_yaml(raw)
        c_value = yaml.load(raw, Loader=YAML12SafeLoader)
        python_value = yaml.load(raw, Loader=_PythonYAML12SafeLoader)
        assert isinstance(c_value, dict)
        assert c_value["schema_version"] == expected_version
        _assert_type_exact_equal(cold_cache, c_value)
        _assert_type_exact_equal(warm_cache, c_value)
        _assert_type_exact_equal(c_value, python_value)

    # These types have surprising equality or are often lost by JSON caches.
    special_types = (
        b"integer: 1\nfloat: 1.0\ndate: 2026-11-11\n"
        b"none: null\nboolean: false\npairs: !!pairs [alpha: 1]\n"
        b"duplicate: old\nduplicate: new\n"
    )
    cold_probe = load_cached_safe_yaml(special_types)
    warm_probe = load_cached_safe_yaml(special_types)
    c_probe = yaml.load(special_types, Loader=YAML12SafeLoader)
    python_probe = yaml.load(special_types, Loader=_PythonYAML12SafeLoader)
    _assert_type_exact_equal(cold_probe, c_probe)
    _assert_type_exact_equal(warm_probe, c_probe)
    _assert_type_exact_equal(c_probe, python_probe)
    assert type(c_probe["integer"]) is int
    assert type(c_probe["float"]) is float
    assert type(c_probe["date"]) is date
    assert type(c_probe["none"]) is type(None)
    assert type(c_probe["boolean"]) is bool
    assert type(c_probe["pairs"][0]) is tuple
    assert c_probe["duplicate"] == "new"
