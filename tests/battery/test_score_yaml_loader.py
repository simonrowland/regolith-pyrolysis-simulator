from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from simulator.yaml_cache import (
    YAML12SafeLoader,
    _assert_type_exact_equal,
    load_cached_safe_yaml,
)


class _PythonYAML12SafeLoader(yaml.SafeLoader):
    """Pure-Python parser with the production YAML 1.2 resolver table."""

    yaml_implicit_resolvers = YAML12SafeLoader.yaml_implicit_resolvers


def test_score_yaml_cache_preserves_representative_store_schemas(
    monkeypatch, tmp_path: Path
) -> None:
    """Compare cached, C, and Python parses for each store document kind.

    The sample covers a work bundle with experiment, bench, and context records;
    an observations-v2 shard; an extracts-v2 observation bundle; and a legacy
    literature extract review document. Those exercise every document and
    record kind the scorer's YAML loaders consume. The synthetic document below
    pins bool/int/float/date/null, ``!!pairs``, and duplicate-key behavior.

    Run ``python scripts/check_yaml_cache_parity.py`` on a studio and after
    store regeneration for the equivalent type-exact C-loader check over every
    store file.
    """

    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE_DIR", str(tmp_path / "yaml-cache"))
    monkeypatch.delenv("REGOLITH_PARSED_YAML_CACHE", raising=False)
    assert yaml.CSafeLoader in YAML12SafeLoader.__mro__

    root = Path(__file__).resolve().parents[2]
    sample_documents = {
        "battery_work.v2.1 work/experiment/bench/context bundle": (
            root / "data/literature/works/10.2205_2011nz000234.yaml",
            "battery_work.v2.1",
            {"work", "experiments", "benches", "context"},
        ),
        "battery_observations.v2.1 observation shard": (
            root
            / "data/literature/observations-v2"
            / "compilations-hemingway-haas-robinson-1982-usgs-b1544"
            / "usgs-b1544-al2sio5-reference.yaml",
            "battery_observations.v2.1",
            {"schema_version", "observations"},
        ),
        "battery_observations.v2.1 extracts-v2 bundle": (
            root / "data/literature/extracts-v2/1997jonesthermo.yaml",
            "battery_observations.v2.1",
            {"schema_version", "observations"},
        ),
        "literature_extract.v1 legacy review document": (
            root / "data/literature/extracts/1997jonesthermo.yaml",
            "literature_extract.v1",
            {"schema_version", "source_id", "review_status", "species"},
        ),
    }
    for kind, (path, expected_version, expected_fields) in sample_documents.items():
        assert path.is_file(), f"missing {kind} sample: {path}"
        raw = path.read_bytes()
        cold_cache = load_cached_safe_yaml(raw)
        warm_cache = load_cached_safe_yaml(raw)
        c_value = yaml.load(raw, Loader=YAML12SafeLoader)
        python_value = yaml.load(raw, Loader=_PythonYAML12SafeLoader)
        assert isinstance(c_value, dict)
        assert c_value["schema_version"] == expected_version
        assert expected_fields <= c_value.keys()
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
