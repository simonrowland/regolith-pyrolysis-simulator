from __future__ import annotations

from hashlib import sha256
from datetime import date
from pathlib import Path
import pickle
import shutil
import subprocess
import sys

import pytest
import yaml

import simulator.config as config_module
import simulator.yaml_cache as yaml_cache_module
from simulator.config import (
    DEFAULT_DATA_DIR,
    functional_data_yaml_digest,
    load_config_bundle,
)
from simulator.yaml_cache import load_cached_safe_yaml


REQUIRED_CONFIGS = {
    "setpoints": "setpoints.yaml",
    "feedstocks": "feedstocks.yaml",
    "foulant_thermo": "foulant_thermo.yaml",
    "vapor_pressures": "vapor_pressures.yaml",
    "materials": "materials.yaml",
    "species_catalog": "species_catalog.yaml",
}
FUNCTIONAL_DATA_CONFIGS = {"setpoints", "vapor_pressures"}


def _unique_cache_payload(index: int) -> bytes:
    return f"value: item-{index:064d}\n".encode("ascii")


def _write_minimal_config_bundle(root: Path) -> None:
    functional_content = (
        "b: 2\n"
        "a: 1\n"
        "nested:\n"
        "  z: 3\n"
        "  y:\n"
        "    - 1\n"
        "    - 2\n"
    )
    for name, filename in REQUIRED_CONFIGS.items():
        content = functional_content if name in FUNCTIONAL_DATA_CONFIGS else "{}\n"
        if name == 'setpoints':
            content += yaml.safe_dump({
                'condensation_train': load_cached_safe_yaml(
                    (DEFAULT_DATA_DIR / 'setpoints.yaml').read_text()
                )['condensation_train'],
            })
        (root / filename).write_text(content)


def test_load_config_bundle_returns_all_required_data_and_digests() -> None:
    bundle = load_config_bundle()

    assert bundle.setpoints
    assert bundle.feedstocks
    assert bundle.foulant_thermo
    assert bundle.vapor_pressures
    assert bundle.materials
    assert bundle.species_catalog
    assert set(bundle.source_paths) == set(REQUIRED_CONFIGS)
    assert set(bundle.digests) == set(REQUIRED_CONFIGS)


def test_load_config_bundle_digests_are_stable_and_scoped() -> None:
    first = load_config_bundle()
    second = load_config_bundle()

    assert first.digests == second.digests
    for name, path in first.source_paths.items():
        if name in FUNCTIONAL_DATA_CONFIGS:
            assert first.digests[name] == functional_data_yaml_digest(
                load_cached_safe_yaml(path.read_text())
            )
        else:
            assert first.digests[name] == sha256(path.read_bytes()).hexdigest()


def test_load_config_bundle_reuses_exact_yaml_parse_without_sharing_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    config_module._parse_required_yaml.cache_clear()
    original_yaml_load = yaml.load
    parse_calls = 0

    def counted_yaml_load(value: str, *args, **kwargs) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_yaml_load(value, *args, **kwargs)

    monkeypatch.setattr(yaml_cache_module.yaml, "load", counted_yaml_load)
    first = load_config_bundle()
    first.setpoints["perf_cache_mutation_probe"] = True
    second = load_config_bundle()

    # Catalog validation may parse its three referenced thermo extracts in the
    # same call; the required config files still parse only once each.
    assert len(REQUIRED_CONFIGS) <= parse_calls < 2 * len(REQUIRED_CONFIGS)
    assert "perf_cache_mutation_probe" not in second.setpoints


def test_load_config_bundle_parse_cache_tracks_exact_file_bytes(tmp_path: Path) -> None:
    _write_minimal_config_bundle(tmp_path)
    first = load_config_bundle(tmp_path)
    setpoints_path = tmp_path / REQUIRED_CONFIGS["setpoints"]
    setpoints_path.write_text(setpoints_path.read_text() + "changed: true\n")

    second = load_config_bundle(tmp_path)

    assert "changed" not in first.setpoints
    assert second.setpoints["changed"] is True
    assert first.digests["setpoints"] != second.digests["setpoints"]


def test_parsed_yaml_process_cache_is_keyed_by_exact_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    original = (
        b"date: 2026-11-11\n"
        b"integer: 1\n"
        b"float: 1.0\n"
        b"none: null\n"
        b"boolean: false\n"
        b"pairs: !!pairs [alpha: 1, beta: 2]\n"
        b"duplicate: first\n"
        b"duplicate: last\n"
    )
    changed = original + b"new: true\n"
    original_yaml_load = yaml.load
    parse_calls = 0

    def counted_yaml_load(value: str | bytes, *args, **kwargs) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_yaml_load(value, *args, **kwargs)

    monkeypatch.setattr(yaml_cache_module.yaml, "load", counted_yaml_load)
    first = load_cached_safe_yaml(original)
    second = load_cached_safe_yaml(original)
    third = load_cached_safe_yaml(changed)

    assert first == second
    assert type(first["date"]) is date
    assert type(first["integer"]) is int
    assert type(first["float"]) is float
    assert first["integer"] == first["float"]
    assert first["none"] is None
    assert first["boolean"] is False
    assert type(first["pairs"][0]) is tuple
    assert first["duplicate"] == "last"
    assert third["new"] is True
    assert parse_calls == 2


def test_parsed_yaml_process_cache_preserves_shared_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    original_yaml_load = yaml.load
    parse_calls = 0

    def counted_yaml_load(value: str | bytes, *args, **kwargs) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_yaml_load(value, *args, **kwargs)

    monkeypatch.setattr(yaml_cache_module.yaml, "load", counted_yaml_load)
    payload = "base: &shared\n  value: 1\ncopy: *shared\n"
    first = load_cached_safe_yaml(payload)
    second = load_cached_safe_yaml(payload)

    assert first["base"] is first["copy"]
    assert second["base"] is second["copy"]
    assert parse_calls == 1


def test_parsed_yaml_cache_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
    original_yaml_load = yaml.load
    parse_calls = 0

    def counted_yaml_load(value: str | bytes, *args, **kwargs) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_yaml_load(value, *args, **kwargs)

    monkeypatch.setattr(yaml_cache_module.yaml, "load", counted_yaml_load)
    payload = b"root:\n  value: 1\n"

    assert load_cached_safe_yaml(payload) == {"root": {"value": 1}}
    assert load_cached_safe_yaml(payload) == {"root": {"value": 1}}
    assert parse_calls == 2
    assert not list((tmp_path / "yaml-cache").rglob("*.pickle"))


def test_corrupt_parsed_yaml_cache_entry_is_a_miss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    payload = b"value: cached\n"
    first = load_cached_safe_yaml(payload)
    cache_path = yaml_cache_module._cache_path(sha256(payload).hexdigest())
    cache_path.write_bytes(b"truncated pickle")

    second = load_cached_safe_yaml(payload)

    assert first["value"] == "cached"
    assert second == first


def test_parsed_yaml_cache_obeys_byte_cap_after_many_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "2048")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )

    for index in range(2 * yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL + 1):
        load_cached_safe_yaml(_unique_cache_payload(index))

    cache_entries = list(cache_root.rglob("*.pickle"))
    assert cache_entries
    total_bytes = sum(path.stat().st_size for path in cache_entries)
    target_bytes = yaml_cache_module._cache_eviction_target_bytes(2048)
    assert target_bytes == 1843
    assert total_bytes <= target_bytes


def test_first_write_in_fresh_process_evicts_over_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "off")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(yaml_cache_module, "_CACHE_WRITES_SINCE_EVICTION", 0)

    for index in range(12):
        load_cached_safe_yaml(_unique_cache_payload(index))
    prior_entries = sorted(cache_root.rglob("*.pickle"))
    cap_bytes = prior_entries[0].stat().st_size * 4
    assert sum(path.stat().st_size for path in prior_entries) > cap_bytes
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", str(cap_bytes))

    child = """
import os
from hashlib import sha256
from pathlib import Path

root = Path(os.environ["REGOLITH_PARSED_YAML_CACHE_DIR"])
os.environ["REGOLITH_PARSED_YAML_CACHE"] = "off"
# Package import loads optimize_costs.yaml through cost_energy's cache call.
import simulator.yaml_cache as cache
os.environ.pop("REGOLITH_PARSED_YAML_CACHE", None)
writes_before = cache._CACHE_WRITES_SINCE_EVICTION
assert writes_before == cache._CACHE_EVICTION_WRITE_INTERVAL, writes_before
payload = f"value: item-{1000:064d}\\n".encode("ascii")
cache.load_cached_safe_yaml(payload)
entries = sorted(
    (str(path), path.stat().st_size)
    for path in root.rglob("*.pickle")
)
writes_after = cache._CACHE_WRITES_SINCE_EVICTION
max_bytes = int(os.environ["REGOLITH_PARSED_YAML_CACHE_MAX_BYTES"])
diagnostics = {
    "entries": entries,
    "writes_before": writes_before,
    "writes_after": writes_after,
    "cap_bytes": max_bytes,
    "target_bytes": cache._cache_eviction_target_bytes(max_bytes),
}
total_bytes = sum(size for _path, size in entries)
assert writes_after == 0, diagnostics
assert total_bytes <= cache._cache_eviction_target_bytes(max_bytes), diagnostics
assert cache._cache_path(sha256(payload).hexdigest()).exists()
"""
    subprocess.run(
        [sys.executable, "-c", child],
        check=True,
        cwd=DEFAULT_DATA_DIR.parent,
        capture_output=True,
        text=True,
    )


def test_parsed_yaml_cache_hit_refreshes_lru_mtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )

    payloads = [
        _unique_cache_payload(index)
        for index in range(yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL + 1)
    ]
    first_result = load_cached_safe_yaml(payloads[0])
    first_path = yaml_cache_module._cache_path(sha256(payloads[0]).hexdigest())
    old_mtime_ns = first_path.stat().st_mtime_ns - 10_000_000_000
    yaml_cache_module.os.utime(first_path, ns=(old_mtime_ns, old_mtime_ns))
    monkeypatch.setenv(
        "REGOLITH_PARSED_YAML_CACHE_MAX_BYTES",
        str(first_path.stat().st_size * 4),
    )
    for payload in payloads[1:-1]:
        load_cached_safe_yaml(payload)

    assert load_cached_safe_yaml(payloads[0]) == first_result
    assert first_path.stat().st_mtime_ns > old_mtime_ns
    load_cached_safe_yaml(payloads[-1])

    second_path = yaml_cache_module._cache_path(sha256(payloads[1]).hexdigest())
    assert first_path.exists()
    assert not second_path.exists()


def test_zero_byte_yaml_cache_budget_keeps_only_the_just_written_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "0")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )
    previous_entries = [
        cache_root / "pyyaml-old" / "yaml12-old" / "v1" / f"entry-{index}.pickle"
        for index in range(3)
    ]
    for path in previous_entries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"older cache entry")
        yaml_cache_module.os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    payload = _unique_cache_payload(0)

    load_cached_safe_yaml(payload)

    cache_entries = list(cache_root.rglob("*.pickle"))
    just_written = yaml_cache_module._cache_path(sha256(payload).hexdigest())
    assert cache_entries == [just_written]
    assert all(not path.exists() for path in previous_entries)


@pytest.mark.parametrize("value", ["a lot", "-1"])
def test_parsed_yaml_cache_rejects_invalid_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", value)
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)

    with pytest.raises(
        ValueError,
        match="REGOLITH_PARSED_YAML_CACHE_MAX_BYTES",
    ):
        load_cached_safe_yaml(b"value: valid\n")


def test_parsed_yaml_cache_eviction_ignores_racing_deleted_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "off")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )

    payloads = [
        _unique_cache_payload(index)
        for index in range(yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL + 1)
    ]
    for payload in payloads[:-1]:
        load_cached_safe_yaml(payload)
    cache_entries = list(cache_root.rglob("*.pickle"))
    cap_bytes = cache_entries[0].stat().st_size * 4
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", str(cap_bytes))
    raced_path = min(
        cache_entries,
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
    )
    original_unlink = Path.unlink
    raced = False

    def unlink_with_race(path: Path, *args, **kwargs) -> None:
        nonlocal raced
        if path == raced_path and not raced:
            raced = True
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError(path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_with_race)
    load_cached_safe_yaml(payloads[-1])

    assert raced
    assert not raced_path.exists()
    cache_entries = list(cache_root.rglob("*.pickle"))
    total_bytes = sum(path.stat().st_size for path in cache_entries)
    assert total_bytes <= yaml_cache_module._cache_eviction_target_bytes(cap_bytes)


def test_failed_yaml_cache_write_still_runs_eviction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "off")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(yaml_cache_module, "_CACHE_WRITES_SINCE_EVICTION", 0)

    for index in range(12):
        load_cached_safe_yaml(_unique_cache_payload(index))
    prior_entries = list(cache_root.rglob("*.pickle"))
    cap_bytes = prior_entries[0].stat().st_size * 4
    assert sum(path.stat().st_size for path in prior_entries) > cap_bytes
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", str(cap_bytes))
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )

    def fail_replace(*_args, **_kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(yaml_cache_module.os, "replace", fail_replace)
    payload = _unique_cache_payload(1000)

    parsed = load_cached_safe_yaml(payload)

    assert parsed == {"value": f"item-{1000:064d}"}
    cache_entries = list(cache_root.rglob("*.pickle"))
    total_bytes = sum(path.stat().st_size for path in cache_entries)
    assert total_bytes <= yaml_cache_module._cache_eviction_target_bytes(cap_bytes)


def test_yaml_cache_walk_oserror_does_not_interrupt_parse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "1")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )
    walk_attempted = False

    def fail_directory_walk(_root: Path, _pattern: str):
        nonlocal walk_attempted
        walk_attempted = True
        raise OSError("directory unavailable")

    monkeypatch.setattr(Path, "rglob", fail_directory_walk)

    assert load_cached_safe_yaml(b"value: parse survives\n") == {
        "value": "parse survives"
    }
    assert walk_attempted


def test_evicted_yaml_cache_entry_reparses_to_same_bytes_as_cache_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "yaml-cache"
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_MAX_BYTES", "1")
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    monkeypatch.setattr(
        yaml_cache_module,
        "_CACHE_WRITES_SINCE_EVICTION",
        yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL,
    )

    payloads = [
        _unique_cache_payload(index)
        for index in range(yaml_cache_module._CACHE_EVICTION_WRITE_INTERVAL + 1)
    ]
    for payload in payloads:
        load_cached_safe_yaml(payload)
    evicted_path = yaml_cache_module._cache_path(sha256(payloads[0]).hexdigest())
    assert not evicted_path.exists()

    original_yaml_load = yaml_cache_module.yaml.load
    parse_calls = 0

    def counted_yaml_load(value: str | bytes, *args, **kwargs) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_yaml_load(value, *args, **kwargs)

    monkeypatch.setattr(yaml_cache_module.yaml, "load", counted_yaml_load)
    after_eviction = load_cached_safe_yaml(payloads[0])
    assert parse_calls == 1

    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
    with_cache_off = load_cached_safe_yaml(payloads[0])

    assert parse_calls == 2
    assert pickle.dumps(after_eviction) == pickle.dumps(with_cache_off)


def test_parsed_yaml_cache_is_reused_by_fresh_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    payload = b"root:\n  value: 1\n"
    assert load_cached_safe_yaml(payload) == {"root": {"value": 1}}

    child = """
import simulator.yaml_cache as cache_module

def fail_if_parsed(*args, **kwargs):
    raise AssertionError(f"fresh process reparsed cached YAML: {args!r}")

cache_module.yaml.load = fail_if_parsed
assert cache_module.load_cached_safe_yaml(b"root:\\n  value: 1\\n") == {
    "root": {"value": 1}
}
"""
    subprocess.run(
        [sys.executable, "-c", child],
        check=True,
        cwd=DEFAULT_DATA_DIR.parent,
        capture_output=True,
        text=True,
    )


def test_parsed_yaml_cache_default_is_in_writable_temp_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(yaml_cache_module.tempfile, "gettempdir", lambda: str(tmp_path))

    path = yaml_cache_module._cache_path("a" * 64)

    assert tmp_path in path.parents
    assert path.name == f"{'a' * 64}.pickle"


def test_parsed_yaml_cache_rejects_untrusted_temp_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(yaml_cache_module.tempfile, "gettempdir", lambda: str(tmp_path))
    user_token = (
        str(yaml_cache_module.os.getuid())
        if hasattr(yaml_cache_module.os, "getuid")
        else "user"
    )
    hostile_parent = tmp_path / f"regolith-pyrolysis-simulator-{user_token}"
    hostile_parent.mkdir()
    hostile_parent.chmod(0o777)
    hostile = hostile_parent / "parsed-yaml"

    path = yaml_cache_module._cache_path("b" * 64)

    assert hostile not in path.parents
    assert yaml_cache_module._ensure_private_directory(path.parents[2])


def test_functional_data_digest_resolves_mre_canonical_voltage_tokens() -> None:
    numeric = {
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75},
                {"species": "SiO2", "decomposition_V": 1.45},
            ],
        },
        "mre_stepped_voltage_holds": {
            "sequence": [
                {"species": ["FeO"], "voltage": 0.75},
                {"species": ["SiO2"], "voltage": 1.45},
            ],
        },
    }
    canonical = {
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": "canonical"},
                {"species": "SiO2", "decomposition_V": "canonical"},
            ],
        },
        "mre_stepped_voltage_holds": {
            "sequence": [
                {"species": ["FeO"], "voltage": "canonical"},
                {"species": ["SiO2"], "voltage": "canonical"},
            ],
        },
    }

    assert functional_data_yaml_digest(canonical) == functional_data_yaml_digest(numeric)


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
def test_functional_data_yaml_digests_ignore_comments_and_mapping_order(
    tmp_path: Path,
    name: str,
) -> None:
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]
    baseline = load_config_bundle(tmp_path).digests[name]

    path.write_text("# documentation-only note\n" + path.read_text())
    assert load_config_bundle(tmp_path).digests[name] == baseline

    collector_config = (
        yaml.safe_dump({'condensation_train': load_cached_safe_yaml(path.read_text())['condensation_train']})
        if name == 'setpoints' else ''
    )
    path.write_text(
        "nested:\n"
        "  y:\n"
        "    - 1\n"
        "    - 2\n"
        "  z: 3\n"
        "a: 1\n"
        "b: 2\n"
        + collector_config
    )
    assert load_config_bundle(tmp_path).digests[name] == baseline


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
def test_functional_data_yaml_digests_track_values_and_added_keys(
    tmp_path: Path,
    name: str,
) -> None:
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]
    baseline = load_config_bundle(tmp_path).digests[name]
    data = load_cached_safe_yaml(path.read_text())

    value_changed = dict(data)
    value_changed["a"] = 9
    path.write_text(yaml.safe_dump(value_changed, sort_keys=False))
    assert load_config_bundle(tmp_path).digests[name] != baseline

    added_key = dict(data)
    added_key["added_functional_key"] = True
    path.write_text(yaml.safe_dump(added_key, sort_keys=False))
    assert load_config_bundle(tmp_path).digests[name] != baseline


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
@pytest.mark.parametrize("invalid_root", ["[]\n", "null\n", "false\n", "0\n"])
def test_functional_data_yaml_refuses_non_mapping_roots(
    tmp_path: Path,
    name: str,
    invalid_root: str,
) -> None:
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]
    path.write_text(invalid_root)

    with pytest.raises(TypeError, match="must have a mapping root"):
        load_config_bundle(tmp_path)


def test_load_config_bundle_missing_required_file_raises(tmp_path: Path) -> None:
    for filename in REQUIRED_CONFIGS.values():
        shutil.copy2(DEFAULT_DATA_DIR / filename, tmp_path / filename)
    (tmp_path / "materials.yaml").unlink()

    with pytest.raises(FileNotFoundError, match="required config file missing"):
        load_config_bundle(tmp_path)


@pytest.mark.parametrize('band', [
    None, [], [350], [350, 700, 800], '350,700',
    [True, 700], ['warm', 700], [float('nan'), 700], [350, float('inf')],
    [-273.15, 0], [700, 350],
])
def test_collector_band_resolver_refuses_malformed_supplied_band(band):
    setpoints = load_config_bundle().setpoints
    setpoints['condensation_train']['metals_train'][
        'stage_4_alkali_mg_cyclone']['temp_range_C'] = band
    with pytest.raises(ValueError, match='stage_4_alkali_mg_cyclone.temp_range_C'):
        config_module.resolve_collector_bands_C(setpoints)


def test_collector_band_resolver_checks_declared_service_ceiling(tmp_path):
    setpoints = load_config_bundle().setpoints
    stage = setpoints['condensation_train']['metals_train']['stage_3_sio_zone']
    stage['temp_range_C'] = [900, stage['max_service_T_C'] + 1]
    path = tmp_path / 'setpoints.yaml'
    path.write_text(yaml.safe_dump(setpoints))
    with pytest.raises(ValueError, match='stage_3_sio_zone.*max_service_T_C'):
        load_config_bundle(setpoints_path=path)


def test_collector_band_defaults_are_yaml_owned_and_detached():
    from simulator.condensation_routing import COLLECTOR_STAGE_KEY_BY_NUMBER
    from simulator.state import CondensationTrain

    setpoints = load_config_bundle().setpoints
    resolved = config_module.resolve_collector_bands_C()
    assert resolved == {
        number: tuple(setpoints['condensation_train']['metals_train'][key]['temp_range_C'])
        for number, key in COLLECTOR_STAGE_KEY_BY_NUMBER.items()
    }
    assert config_module.resolve_collector_bands_C({}) == resolved
    resolved[4] = (1.0, 2.0)
    assert CondensationTrain.create_default().stages[4].temp_range_C != resolved[4]


@pytest.mark.parametrize('missing_path', [
    ('condensation_train',),
    ('condensation_train', 'metals_train'),
    *(
        ('condensation_train', 'metals_train', key)
        for key in (
            *config_module.COLLECTOR_STAGE_KEY_BY_NUMBER.values(),
            *config_module.SUBSTAGE_COLLECTOR_KEYS,
        )
    ),
    ('condensation_train', 'metals_train', 'stage_4_alkali_mg_cyclone', 'temp_range_C'),
])
def test_setpoints_file_refuses_missing_collector_input(tmp_path, missing_path):
    setpoints = load_cached_safe_yaml((DEFAULT_DATA_DIR / 'setpoints.yaml').read_text())
    parent = setpoints
    for key in missing_path[:-1]:
        parent = parent[key]
    del parent[missing_path[-1]]
    # A custom filename must be checked by its declared config role, too.
    path = tmp_path / 'custom-setpoints.yaml'
    path.write_text(yaml.safe_dump(setpoints))
    with pytest.raises(ValueError, match=r'\.'.join(missing_path)):
        load_config_bundle(setpoints_path=path)


def test_setpoints_file_refuses_typo_in_collector_stage_key(tmp_path):
    setpoints = load_cached_safe_yaml((DEFAULT_DATA_DIR / 'setpoints.yaml').read_text())
    stages = setpoints['condensation_train']['metals_train']
    stages['stage_4_alkali_mg_cylcone'] = stages.pop('stage_4_alkali_mg_cyclone')
    path = tmp_path / 'setpoints.yaml'
    path.write_text(yaml.safe_dump(setpoints))
    with pytest.raises(ValueError, match='stage_4_alkali_mg_cyclone'):
        load_config_bundle(setpoints_path=path)


@pytest.mark.parametrize('reader', [
    'strict_file', 'memory_defaults', 'recipe_defaults', 'recipe_ceiling',
    'runner', 'three_product', 'web_routes', 'web_events', 'web_feedstocks',
    'profile_mre', 'profile_hold',
    'selectivity_script', 'profile_script', 'map_script', 'thermal_fixture_script',
])
def test_other_setpoints_file_readers_refuse_missing_stage(tmp_path, monkeypatch, reader):
    import importlib

    setpoints = load_cached_safe_yaml((DEFAULT_DATA_DIR / 'setpoints.yaml').read_text())
    del setpoints['condensation_train']['metals_train']['stage_4_alkali_mg_cyclone']
    path = tmp_path / 'setpoints.yaml'
    path.write_text(yaml.safe_dump(setpoints))

    if reader == 'strict_file':
        load = lambda: config_module._load_setpoints_file(path)
    elif reader == 'memory_defaults':
        monkeypatch.setattr(config_module, 'DEFAULT_DATA_DIR', tmp_path)
        load = lambda: config_module.resolve_collector_bands_C({})
    elif reader in ('recipe_defaults', 'recipe_ceiling'):
        from simulator import recipe
        monkeypatch.setattr(recipe, 'DEFAULT_DATA_DIR', tmp_path)
        # Bypass the value cache to exercise the file boundary without leaking
        # an invalid temporary source into subsequent default-schema tests.
        load = (
            recipe._default_setpoints.__wrapped__ if reader == 'recipe_defaults'
            else recipe._c5_allow_mre_voltage_cap_upper_bound
        )
    elif reader == 'runner':
        from simulator.runner import PyrolysisRun
        load = PyrolysisRun(
            feedstock_id='lunar_mare_low_ti', setpoints_path=path,
        )._load_setpoints
    elif reader == 'three_product':
        from simulator.three_product_runner import _load_yaml
        load = lambda: _load_yaml(tmp_path, 'setpoints.yaml')
    elif reader.startswith('web_'):
        name = {'web_routes': 'routes', 'web_events': 'events',
                'web_feedstocks': 'feedstock_data'}[reader]
        module = importlib.import_module(f'web.{name}')
        monkeypatch.setattr(module, 'DATA_DIR', tmp_path)
        loader = module.load_yaml if name == 'feedstock_data' else module._load_yaml
        load = lambda: loader('setpoints.yaml')
    elif reader in ('profile_mre', 'profile_hold'):
        from simulator.optimize import profiles
        monkeypatch.setattr(profiles, 'DEFAULT_DATA_DIR', tmp_path)
        load = (
            lambda: profiles._canonical_mre_ladder_for_profile('test.yaml')
        ) if reader == 'profile_mre' else (
            lambda: profiles._campaign_max_hold_hr_for_profile('test.yaml', 'C4')
        )
    else:
        from types import SimpleNamespace
        data = tmp_path / 'data'
        data.mkdir()
        (data / 'setpoints.yaml').write_text(path.read_text())
        name, root, entry = {
            'selectivity_script': ('evaporation_selectivity_map', 'REPO_ROOT', '_build_sim'),
            'profile_script': ('make_recipe_db_profile', 'REPO_ROOT', '_setpoint_campaign_config'),
            'map_script': ('sso_r_validation_map', 'REPO_ROOT', '_load_data'),
            'thermal_fixture_script': ('generate_thermal_train_default_fixture', 'DATA', 'main'),
        }[reader]
        module = importlib.import_module(f'scripts.{name}')
        monkeypatch.setattr(module, root, data if root == 'DATA' else tmp_path)
        args = (SimpleNamespace(),) if reader == 'selectivity_script' else (
            ('C4',) if reader == 'profile_script' else ()
        )
        load = lambda: getattr(module, entry)(*args)
    with pytest.raises(ValueError, match='stage_4_alkali_mg_cyclone'):
        load()
