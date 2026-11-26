from __future__ import annotations

from pathlib import Path

import simulator.battery.migrate as migrate
from tests.battery.store_cache import load_cached_test_store


def test_parsed_store_cache_invalidates_on_document_and_loader_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    document = root / "data/literature/observations-v2/sample.yaml"
    document.parent.mkdir(parents=True)
    document.write_text("observations:\n  - value: before\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    loader_source = tmp_path / "migrate.py"
    loader_source.write_text("loader version one\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "__file__", str(loader_source))

    parse_calls = 0
    original_parse = migrate._load_yaml_text

    def counted_parse(payload: str | bytes) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(payload)

    monkeypatch.setattr(migrate, "_load_yaml_text", counted_parse)

    first = load_cached_test_store(root, cache_root=cache_root)
    assert first["data/literature/observations-v2/sample.yaml"]["observations"][0][
        "value"
    ] == "before"
    assert parse_calls == 1
    assert load_cached_test_store(root, cache_root=cache_root) == first
    assert parse_calls == 1

    document.write_text("observations:\n  - value: after\n", encoding="utf-8")
    changed_document = load_cached_test_store(root, cache_root=cache_root)
    assert changed_document[
        "data/literature/observations-v2/sample.yaml"
    ]["observations"][0]["value"] == "after"
    assert parse_calls == 2

    loader_source.write_text("loader version two\n", encoding="utf-8")
    changed_loader = load_cached_test_store(root, cache_root=cache_root)
    assert changed_loader == changed_document
    assert parse_calls == 3
