from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import yaml

import simulator.battery.migrate as migrate
import simulator.battery.score as score
from simulator.battery.score import load_score_context
from simulator.yaml_cache import YAML12SafeLoader, load_cached_safe_yaml


class _PythonYAML12SafeLoader(yaml.SafeLoader):
    """Pure-Python parser with the production YAML 1.2 resolver table."""

    yaml_implicit_resolvers = YAML12SafeLoader.yaml_implicit_resolvers


def _assert_type_exact_equal(left: object, right: object, seen=None) -> None:
    assert type(left) is type(right)
    visited = seen if seen is not None else set()
    pair = (id(left), id(right))
    if pair in visited:
        return
    visited.add(pair)

    if isinstance(left, Mapping):
        assert isinstance(right, Mapping)
        assert len(left) == len(right)
        unmatched = list(right.items())
        for left_key, left_value in left.items():
            match = next(
                (
                    index
                    for index, (right_key, _right_value) in enumerate(unmatched)
                    if type(left_key) is type(right_key) and left_key == right_key
                ),
                None,
            )
            assert match is not None, f"mapping key differs: {left_key!r}"
            right_key, right_value = unmatched.pop(match)
            _assert_type_exact_equal(left_key, right_key, visited)
            _assert_type_exact_equal(left_value, right_value, visited)
        return

    if isinstance(left, (list, tuple)):
        assert isinstance(right, type(left))
        assert len(left) == len(right)
        for left_item, right_item in zip(left, right, strict=True):
            _assert_type_exact_equal(left_item, right_item, visited)
        return

    if isinstance(left, (set, frozenset)):
        assert isinstance(right, type(left))
        assert len(left) == len(right)
        unmatched = list(right)
        for left_item in left:
            match = next(
                (
                    index
                    for index, right_item in enumerate(unmatched)
                    if type(left_item) is type(right_item) and left_item == right_item
                ),
                None,
            )
            assert match is not None, f"set value differs: {left_item!r}"
            _assert_type_exact_equal(left_item, unmatched.pop(match), visited)
        return

    if isinstance(left, float) and math.isnan(left):
        assert math.isnan(right)
        return
    assert left == right


def test_score_store_yaml_cache_matches_both_loaders_for_every_loaded_file(
    monkeypatch, tmp_path: Path
) -> None:
    """Cold/warm cache values equal fresh C and pure-Python parses, type exactly."""

    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    loaded_paths: set[Path] = set()
    migrate_load_yaml = migrate.load_yaml
    score_load_yaml = score.load_yaml

    def track_migrate_load(path: Path):
        loaded_paths.add(path.resolve())
        return migrate_load_yaml(path)

    def track_score_load(path: Path):
        loaded_paths.add(path.resolve())
        return score_load_yaml(path)

    monkeypatch.setattr(migrate, "load_yaml", track_migrate_load)
    monkeypatch.setattr(score, "load_yaml", track_score_load)
    context = load_score_context()
    assert context.observations
    assert loaded_paths

    for path in sorted(loaded_paths):
        raw = path.read_bytes()
        cold_or_warm = load_cached_safe_yaml(raw)
        cached = load_cached_safe_yaml(raw)
        c_value = yaml.load(raw, Loader=YAML12SafeLoader)
        python_value = yaml.load(raw, Loader=_PythonYAML12SafeLoader)
        _assert_type_exact_equal(cold_or_warm, cached)
        _assert_type_exact_equal(cached, c_value)
        _assert_type_exact_equal(c_value, python_value)

    # These types have surprising equality or are often lost by JSON caches.
    probe = load_cached_safe_yaml(
        b"integer: 1\nfloat: 1.0\ndate: 2026-11-11\n"
        b"none: null\nboolean: false\npairs: !!pairs [alpha: 1]\n"
        b"duplicate: old\nduplicate: new\n"
    )
    assert type(probe["integer"]) is int
    assert type(probe["float"]) is float
    assert type(probe["date"]) is date
    assert type(probe["none"]) is type(None)
    assert type(probe["boolean"]) is bool
    assert type(probe["pairs"][0]) is tuple
    assert probe["duplicate"] == "new"
