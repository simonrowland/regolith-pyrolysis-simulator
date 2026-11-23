"""Contract tests for the one prologue pickle.

The key is the migrated-store content digest, every declared data input,
the simulator and engines Python trees, the VapoRock package, and, when
pure phase is on, the ThermoEngine tree and the MAGEMin binary. A warm
prologue must not call validate_corpus again. A torn pickle rebuilds.
"""

from __future__ import annotations

import os
import pickle
import socket
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Engine, EvidenceClass
from simulator.battery.parsed_store_cache import (
    PROLOGUE_DATA_INPUTS,
    _directory_token,
    canonical_material,
    close_prologue,
    open_prologue,
    prologue_key,
    runtime_token,
)
from simulator.battery.records import DecisionBand
from simulator.battery.score import ScoreContext
from tests.battery import factories as F


def _tiny_store(root: Path) -> Path:
    from simulator.battery.migrate import dump_yaml, to_plain

    source_id = "child-source"
    work = replace(F.work(), source_ids=(source_id,))
    experiment = F.tabulation_experiment()
    observation = F.observation(
        f"{source_id}::row",
        experiment.experiment_id,
        F.pref_identity(),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=source_id,
    )
    literature = root / "data" / "literature"
    (literature / "works").mkdir(parents=True)
    (literature / "extracts-v2").mkdir()
    dump_yaml(
        {"work": to_plain(work), "experiments": [to_plain(experiment)]},
        literature / "works" / "work.yaml",
    )
    dump_yaml(
        {"observations": [to_plain(observation)]},
        literature / "extracts-v2" / f"{source_id}.yaml",
    )
    return root


def _use_cache(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE", "on")
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE_DIR", str(directory))


def _stub_installed_trees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep session tests off the installed VapoRock and checkout trees.

    The generative test hashes a tmp tree for real. These stubs only stop
    the session tests from rehashing that installed tree on every load.
    """

    import simulator.battery.parsed_store_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_vaporock_token", lambda: "vaporock-stub")
    monkeypatch.setattr(cache_mod, "_python_tree_token", lambda _root: "code-stub")
    monkeypatch.setattr(cache_mod, "_thermoengine_token", lambda: "thermo-stub")
    monkeypatch.setattr(cache_mod, "_magemin_binary_token", lambda: "magemin-stub")


def _counting_validate(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import simulator.battery.validate as validate_mod

    calls = {"n": 0}
    real = validate_mod.validate_corpus

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(validate_mod, "validate_corpus", wrapped)
    return calls


def _plain_records(context: ScoreContext):
    from simulator.battery.migrate import to_plain

    return (
        to_plain(context.works),
        to_plain(context.experiments),
        to_plain(context.observations),
        dict(context.origins),
    )


def _issue_rows(context: ScoreContext):
    return tuple(
        (
            observation_id,
            tuple((issue.path, issue.reason.value, issue.detail) for issue in issues),
        )
        for observation_id, issues in sorted(context.hard_issues_by_observation.items())
    )


def _layout(root: Path) -> Path:
    """One file for every declared input, plus one simulator and engines module."""

    for relative in PROLOGUE_DATA_INPUTS:
        path = root / relative
        if Path(relative).suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("a\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "marker.txt").write_text("a\n", encoding="utf-8")
    (root / "simulator").mkdir()
    (root / "simulator" / "marker.py").write_text("a = 1\n", encoding="utf-8")
    (root / "engines").mkdir()
    (root / "engines" / "marker.py").write_text("a = 1\n", encoding="utf-8")
    return root


def _key(root: Path, **overrides) -> str:
    args = {
        "engines": ("vaporock",),
        "invoke_pure_phase": False,
        "work_id": None,
        "rail": None,
        "species_arg": None,
        "entries_material": "{}",
        "code_root": root,
    }
    args.update(overrides)
    return prologue_key(root, **args)


def _mutate(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "b\n", encoding="utf-8")


def test_prologue_key_changes_for_every_declared_input_and_a_python_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.parsed_store_cache as cache_mod

    # The installed package is not the tmp copy. Pin it so this test's
    # mutations are the declared inputs and the Python files.
    monkeypatch.setattr(cache_mod, "_vaporock_token", lambda: "vaporock-stub")
    root = _layout(tmp_path / "copy")
    base = _key(root)
    targets = []
    for relative in PROLOGUE_DATA_INPUTS:
        path = root / relative
        targets.append(path if path.is_file() else path / "marker.txt")
    targets.append(root / "simulator" / "marker.py")
    targets.append(root / "engines" / "marker.py")
    assert len(targets) == len(PROLOGUE_DATA_INPUTS) + 2
    for path in targets:
        original = path.read_text(encoding="utf-8")
        _mutate(path)
        assert _key(root) != base
        path.write_text(original, encoding="utf-8")
        assert _key(root) == base


def test_directory_token_and_installed_packages_change_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.parsed_store_cache as cache_mod

    package = tmp_path / "package"
    package.mkdir()
    (package / "JANAF-vapor-data-full.csv").write_text("a\n", encoding="utf-8")
    before = _directory_token(package)
    (package / "JANAF-vapor-data-full.csv").write_text("b\n", encoding="utf-8")
    assert _directory_token(package) != before

    monkeypatch.setattr(cache_mod, "_python_tree_token", lambda _root: "code-stub")
    monkeypatch.setattr(cache_mod, "_thermoengine_token", lambda: "thermo-stub")
    monkeypatch.setattr(cache_mod, "_magemin_binary_token", lambda: "magemin-stub")
    root = _layout(tmp_path / "copy")
    monkeypatch.setattr(cache_mod, "_vaporock_token", lambda: "one")
    first = _key(root)
    monkeypatch.setattr(cache_mod, "_vaporock_token", lambda: "two")
    assert _key(root) != first
    calls = {"n": 0}

    def counted() -> str:
        calls["n"] += 1
        return "backend"

    monkeypatch.setattr(cache_mod, "_thermoengine_token", counted)
    monkeypatch.setattr(cache_mod, "_magemin_binary_token", counted)
    assert _key(root, invoke_pure_phase=False) == _key(root, invoke_pure_phase=False)
    assert calls["n"] == 0
    pure = _key(root, invoke_pure_phase=True)
    assert calls["n"] == 2
    monkeypatch.setattr(cache_mod, "_magemin_binary_token", lambda: "other-binary")
    assert _key(root, invoke_pure_phase=True) != pure


def test_runtime_token_names_the_interpreter_and_pyyaml() -> None:
    import yaml

    token = runtime_token()
    assert sys_version_in(token)
    assert yaml.__version__ in token


def sys_version_in(token: str) -> bool:
    import sys

    return sys.version in token


def test_species_arg_and_engines_change_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_installed_trees(monkeypatch)
    root = _layout(tmp_path / "copy")
    base = _key(root)
    assert _key(root, species_arg="SiO2") != base
    assert _key(root, engines=("openimcc",)) != base
    assert canonical_material({"values": {1}}) is None


def _run_prologue(root: Path, *, entries: object, species_arg: str | None = None) -> ScoreContext:
    from simulator.battery.compilation_tier import _write_compilation_comparisons_jsonl
    from simulator.battery.score import _source_band_catalog, load_score_context

    open_prologue(
        root,
        engines=("vaporock",),
        invoke_pure_phase=False,
        work_id=None,
        rail=None,
        species_arg=species_arg,
        entries=entries,
    )
    try:
        context = load_score_context(root)
        _source_band_catalog(context, entries if isinstance(entries, dict) else {})
        _write_compilation_comparisons_jsonl(
            context,
            root / "comparisons.jsonl",
            engines=(Engine.VAPOROCK,),
            invoke_pure_phase=False,
            observation_filter=None if species_arg is None else (lambda _observation: True),
        )
        return context
    finally:
        close_prologue()


def test_warm_prologue_skips_validation_and_restores_both_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.score as score_mod
    from simulator.battery.score import load_score_context

    _stub_installed_trees(monkeypatch)
    _use_cache(monkeypatch, tmp_path / "cache")
    root = _tiny_store(tmp_path / "store")
    calls = _counting_validate(monkeypatch)
    entries: dict = {}
    band_calls = {"n": 0}
    body_calls = {"n": 0}
    band = DecisionBand(Decimal("1.5"), "K", "derived_scatter")

    def compute(_context, _entries):
        band_calls["n"] += 1
        return ({"group": band}, [{"group_key": {"formula": "MgO"}}], {"other": "held"})

    def body(_context, path: Path, **_kwargs) -> int:
        body_calls["n"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"row":1}\n', encoding="utf-8")
        return 1

    monkeypatch.setattr(score_mod, "_source_band_catalog_compute", compute)
    monkeypatch.setattr(compilation_tier, "_write_compilation_comparisons_body", body)

    cold = _run_prologue(root, entries=entries)
    assert calls["n"] == 1
    assert band_calls["n"] == 1
    assert body_calls["n"] == 1
    assert cold.hostname == socket.gethostname()
    (root / "comparisons.jsonl").unlink()

    warm = _run_prologue(root, entries=entries)
    assert calls["n"] == 1
    assert band_calls["n"] == 1
    assert body_calls["n"] == 1
    assert warm.observations is not cold.observations
    assert _plain_records(warm) == _plain_records(cold)
    assert _issue_rows(warm) == _issue_rows(cold)
    assert (root / "comparisons.jsonl").read_text(encoding="utf-8") == '{"row":1}\n'
    bands, _records, _reasons = score_mod._source_band_catalog(warm, entries)
    # The catalog above ran outside a session, so it computed. Re-open for a hit copy.
    assert band_calls["n"] == 2
    open_prologue(
        root,
        engines=("vaporock",),
        invoke_pure_phase=False,
        work_id=None,
        rail=None,
        species_arg=None,
        entries=entries,
    )
    try:
        held = load_score_context(root)
        first = score_mod._source_band_catalog(held, entries)
        second = score_mod._source_band_catalog(held, entries)
        assert band_calls["n"] == 2
        assert first[0]["group"] is not second[0]["group"]
        assert second[0]["group"].value == Decimal("1.5")
        compilation_tier._write_compilation_comparisons_jsonl(
            held,
            root / "comparisons.jsonl",
            engines=(Engine.OPENIMCC,),
            invoke_pure_phase=False,
        )
        assert body_calls["n"] == 2
        plain = ScoreContext(works={}, experiments={}, observations={}, root=root)
        compilation_tier._write_compilation_comparisons_jsonl(
            plain,
            root / "comparisons.jsonl",
            engines=(Engine.VAPOROCK,),
            invoke_pure_phase=False,
        )
        assert body_calls["n"] == 3
    finally:
        close_prologue()
    assert calls["n"] == 1
    assert bands["group"].value == Decimal("1.5")

    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir()
    (extracts / "child-source.yaml").write_text(
        "source_id: child-source\nreview_status: reviewed\n",
        encoding="utf-8",
    )
    reviewed = _run_prologue(root, entries=entries)
    assert calls["n"] == 1
    assert reviewed.extract_review["child-source"] == "reviewed"

    work_path = root / "data" / "literature" / "works" / "work.yaml"
    text = work_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    work_path.write_text(text + "# cache-bust\n", encoding="utf-8")
    rebuilt = _run_prologue(root, entries=entries)
    assert calls["n"] == 2
    assert _plain_records(rebuilt) == _plain_records(cold)


def test_torn_and_random_pickles_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.score as score_mod

    _stub_installed_trees(monkeypatch)
    cache = tmp_path / "cache"
    _use_cache(monkeypatch, cache)
    root = _tiny_store(tmp_path / "store")
    calls = _counting_validate(monkeypatch)

    def compute(_context, _entries):
        return ({}, [], {})

    def body(_context, path: Path, **_kwargs) -> int:
        path.write_text("{}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(score_mod, "_source_band_catalog_compute", compute)
    monkeypatch.setattr(compilation_tier, "_write_compilation_comparisons_body", body)
    _run_prologue(root, entries={})
    assert calls["n"] == 1
    pickles = list(cache.rglob("*.pickle"))
    assert len(pickles) == 1
    original = pickles[0].read_bytes()

    pickles[0].write_bytes(original[:10])
    _run_prologue(root, entries={})
    assert calls["n"] == 2

    pickles = list(cache.rglob("*.pickle"))
    assert len(pickles) == 1
    pickles[0].write_bytes(os.urandom(64))
    _run_prologue(root, entries={})
    assert calls["n"] == 3

    pickles = list(cache.rglob("*.pickle"))
    assert len(pickles) == 1
    with pickles[0].open("wb") as handle:
        pickle.dump({"schema": 1, "key": pickles[0].stem}, handle)
    _run_prologue(root, entries={})
    assert calls["n"] == 4


def test_fixture_root_does_not_write_the_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.score as score_mod

    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    monkeypatch.delenv("REGOLITH_PARSED_STORE_CACHE_DIR", raising=False)
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE", "on")
    _stub_installed_trees(monkeypatch)
    monkeypatch.setattr(
        score_mod, "_source_band_catalog_compute", lambda _context, _entries: ({}, [], {})
    )
    monkeypatch.setattr(
        compilation_tier,
        "_write_compilation_comparisons_body",
        lambda _context, path, **_kwargs: path.write_text("{}\n", encoding="utf-8") or 0,
    )
    calls = _counting_validate(monkeypatch)
    root = _tiny_store(tmp_path / "store")
    _run_prologue(root, entries={})
    _run_prologue(root, entries={})
    assert calls["n"] == 2
    assert not (xdg / "regolith-pyrolysis-simulator" / "parsed-store").exists()


def test_patched_loader_is_not_served_the_prologue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.score as score_mod
    from simulator.battery.score import load_score_context

    _stub_installed_trees(monkeypatch)
    _use_cache(monkeypatch, tmp_path / "cache")
    root = _tiny_store(tmp_path / "store")

    def compute(_context, _entries):
        return ({}, [], {})

    def body(_context, path: Path, **_kwargs) -> int:
        path.write_text("{}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(score_mod, "_source_band_catalog_compute", compute)
    monkeypatch.setattr(compilation_tier, "_write_compilation_comparisons_body", body)
    _run_prologue(root, entries={})

    def boom(*_args, **_kwargs):
        raise RuntimeError("patched loader")

    monkeypatch.setattr(score_mod, "load_migrated_store", boom)
    open_prologue(
        root,
        engines=("vaporock",),
        invoke_pure_phase=False,
        work_id=None,
        rail=None,
        species_arg=None,
        entries={},
    )
    try:
        with pytest.raises(RuntimeError, match="patched loader"):
            load_score_context(root)
    finally:
        close_prologue()


def test_noncanonical_entries_are_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.score as score_mod

    _stub_installed_trees(monkeypatch)
    cache = tmp_path / "cache"
    _use_cache(monkeypatch, cache)
    root = _tiny_store(tmp_path / "store")
    calls = _counting_validate(monkeypatch)

    def compute(_context, _entries):
        return ({}, [], {})

    def body(_context, path: Path, **_kwargs) -> int:
        path.write_text("{}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(score_mod, "_source_band_catalog_compute", compute)
    monkeypatch.setattr(compilation_tier, "_write_compilation_comparisons_body", body)
    entries = {"bad": {"values": {1}}}
    _run_prologue(root, entries=entries)
    _run_prologue(root, entries=entries)
    assert calls["n"] == 2
    assert list(cache.rglob("*.pickle")) == []
