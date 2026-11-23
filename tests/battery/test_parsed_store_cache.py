"""Contract tests for the parsed-store snapshot and the pure-function reuse.

The snapshot key is the migrated-store content digest plus the builder
token. A warm load must not call validate_corpus again. Compilation rows
and the source-band catalog are reused only for that same snapshot.
"""

from __future__ import annotations

import ast
import socket
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Engine, EvidenceClass
from simulator.battery.migrate import REPO_ROOT
from simulator.battery.parsed_store_cache import (
    HARMLESS_LOADER_SETTINGS,
    PARSED_STORE_ENTRY_FILES,
    ParsedScoreInputs,
    _remember,
    load_unfiltered_score_inputs,
    reachable_source_files,
)
from simulator.battery.records import DecisionBand
from simulator.battery.score import ScoreContext
from simulator.battery.store_validation import (
    _migrated_store_content_sha256,
    hash_relative_files,
)
from tests.battery import factories as F

_LOADER_MODULES = (
    "simulator/battery/migrate.py",
    "simulator/battery/records.py",
    "simulator/battery/identity.py",
    "simulator/battery/validate.py",
    "simulator/battery/validity.py",
    "simulator/battery/polymorph_dictionary.py",
    "simulator/accounting/formulas.py",
    "simulator/accounting/oxide_mass.py",
    "simulator/yaml_cache.py",
    "simulator/state.py",
)


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


def _store_paths(root: Path) -> list[Path]:
    from simulator.battery.migrate import (
        iter_observation_store_paths,
        iter_work_store_paths,
    )

    literature = root / "data" / "literature"
    paths = iter_work_store_paths(literature / "works")
    for directory_name in ("extracts-v2", "observations-v2"):
        paths.extend(iter_observation_store_paths(literature / directory_name))
    return paths


def _issue_rows(context: ScoreContext) -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
    return _issue_rows_from_map(context.hard_issues_by_observation)


def _issue_rows_from_map(issues_by_observation):
    return tuple(
        (
            observation_id,
            tuple((issue.path, issue.reason.value, issue.detail) for issue in issues),
        )
        for observation_id, issues in sorted(issues_by_observation.items())
    )


def _plain_records(context: ScoreContext) -> tuple[object, object, object, dict[str, str]]:
    from simulator.battery.migrate import to_plain

    return (
        to_plain(context.works),
        to_plain(context.experiments),
        to_plain(context.observations),
        dict(context.origins),
    )


def _use_cache(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE", "on")
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE_DIR", str(directory))


def _counting_validate(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import simulator.battery.validate as validate_mod

    calls = {"n": 0}
    real = validate_mod.validate_corpus

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(validate_mod, "validate_corpus", wrapped)
    return calls


class _Row:
    def __init__(self, observation_id: str) -> None:
        self.observation_id = observation_id
        self.experiment_id = "experiment-1"


def _registered_context(
    root: Path,
    *,
    observations: dict | None = None,
) -> ScoreContext:
    works: dict = {}
    experiments: dict = {}
    rows = {} if observations is None else observations
    benches: dict = {}
    origins: dict = {}
    parsed = ParsedScoreInputs(
        store_content_sha256="ab" * 32,
        builder_token="cd" * 32,
        works=works,
        experiments=experiments,
        observations=rows,
        benches=benches,
        origins=origins,
        hard_issues_by_observation={},
    )
    _remember(parsed)
    return ScoreContext(
        works=works,
        experiments=experiments,
        observations=rows,
        origins=origins,
        benches=benches,
        root=root,
    )


def test_snapshot_digest_is_the_store_content_hash(tmp_path: Path) -> None:
    root = _tiny_store(tmp_path / "store")
    digest = _migrated_store_content_sha256(root)
    assert hash_relative_files(root, _store_paths(root)) == digest


def test_loader_closure_includes_modules_that_build_records() -> None:
    files = set(reachable_source_files(REPO_ROOT, PARSED_STORE_ENTRY_FILES))
    missing = [relative for relative in _LOADER_MODULES if relative not in files]
    assert missing == []


def test_warm_snapshot_skips_validation_and_matches_a_fresh_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery.score import load_score_context

    root = _tiny_store(tmp_path / "store")
    _use_cache(monkeypatch, tmp_path / "cache")
    calls = _counting_validate(monkeypatch)

    cold = load_score_context(root)
    assert calls["n"] == 1
    assert cold.hostname == socket.gethostname()
    warm = load_score_context(root)
    assert calls["n"] == 1
    assert warm.observations is not cold.observations
    assert _plain_records(warm) == _plain_records(cold)
    assert _issue_rows(warm) == _issue_rows(cold)
    assert any((tmp_path / "cache").rglob("*.pickle"))

    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir()
    (extracts / "child-source.yaml").write_text(
        "source_id: child-source\nreview_status: reviewed\n",
        encoding="utf-8",
    )
    reviewed = load_score_context(root)
    assert calls["n"] == 1
    assert reviewed.extract_review["child-source"] == "reviewed"

    work_path = root / "data" / "literature" / "works" / "work.yaml"
    text = work_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    work_path.write_text(text + "# cache-bust\n", encoding="utf-8")
    rebuilt = load_score_context(root)
    assert calls["n"] == 2
    assert _plain_records(rebuilt) == _plain_records(cold)
    assert _issue_rows(rebuilt) == _issue_rows(cold)

    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE", "off")
    fresh = load_score_context(root)
    assert calls["n"] == 3
    assert _plain_records(fresh) == _plain_records(cold)
    assert _issue_rows(fresh) == _issue_rows(cold)


def test_comment_changes_the_digest_without_changing_parsed_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery.migrate import to_plain

    root = _tiny_store(tmp_path / "store")
    _use_cache(monkeypatch, tmp_path / "cache")
    before_digest = _migrated_store_content_sha256(root)
    before = load_unfiltered_score_inputs(root)
    assert before.store_content_sha256 == before_digest
    work_path = root / "data" / "literature" / "works" / "work.yaml"
    text = work_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    work_path.write_text(text + "# cache-bust\n", encoding="utf-8")
    after_digest = _migrated_store_content_sha256(root)
    assert before_digest != after_digest
    after = load_unfiltered_score_inputs(root)
    assert after.store_content_sha256 == after_digest
    assert to_plain(before.works) == to_plain(after.works)
    assert to_plain(before.experiments) == to_plain(after.experiments)
    assert to_plain(before.observations) == to_plain(after.observations)
    assert before.origins == after.origins
    assert _issue_rows_from_map(before.hard_issues_by_observation) == _issue_rows_from_map(
        after.hard_issues_by_observation
    )


def test_fixture_root_does_not_write_the_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery.score import load_score_context

    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    monkeypatch.delenv("REGOLITH_PARSED_STORE_CACHE_DIR", raising=False)
    monkeypatch.setenv("REGOLITH_PARSED_STORE_CACHE", "on")
    calls = _counting_validate(monkeypatch)
    root = _tiny_store(tmp_path / "store")
    load_score_context(root)
    load_score_context(root)
    assert calls["n"] == 2
    assert not (xdg / "regolith-pyrolysis-simulator" / "parsed-store").exists()


def test_patched_loader_is_not_served_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod
    from simulator.battery.score import load_score_context

    root = _tiny_store(tmp_path / "store")
    _use_cache(monkeypatch, tmp_path / "cache")
    load_score_context(root)

    def boom(*_args, **_kwargs):
        raise RuntimeError("patched loader")

    monkeypatch.setattr(score_mod, "load_migrated_store", boom)
    with pytest.raises(RuntimeError, match="patched loader"):
        load_score_context(root)


def test_compilation_reuse_follows_the_snapshot_and_its_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier

    _use_cache(monkeypatch, tmp_path / "cache")
    rows = {"a": _Row("a"), "b": _Row("b")}
    context = _registered_context(tmp_path, observations=rows)
    calls = {"n": 0}

    def body(_context, path: Path, **_kwargs) -> int:
        calls["n"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"row":1}\n', encoding="utf-8")
        return 1

    monkeypatch.setattr(compilation_tier, "_write_compilation_comparisons_body", body)
    path = tmp_path / "comparisons.jsonl"
    engines = (Engine.VAPOROCK,)
    assert (
        compilation_tier._write_compilation_comparisons_jsonl(
            context, path, engines=engines, invoke_pure_phase=False
        )
        == 1
    )
    assert calls["n"] == 1
    path.unlink()
    assert (
        compilation_tier._write_compilation_comparisons_jsonl(
            context, path, engines=engines, invoke_pure_phase=False
        )
        == 1
    )
    assert calls["n"] == 1
    assert path.read_text(encoding="utf-8") == '{"row":1}\n'

    other = ScoreContext(works={}, experiments={}, observations={}, root=tmp_path)
    compilation_tier._write_compilation_comparisons_jsonl(
        other, path, engines=engines, invoke_pure_phase=False
    )
    assert calls["n"] == 2

    compilation_tier._write_compilation_comparisons_jsonl(
        context,
        path,
        engines=(Engine.OPENIMCC,),
        invoke_pure_phase=False,
    )
    assert calls["n"] == 3

    def keep_a(observation: _Row) -> bool:
        return observation.observation_id == "a"

    def keep_a_again(observation: _Row) -> bool:
        return observation.observation_id == "a"

    def keep_b(observation: _Row) -> bool:
        return observation.observation_id == "b"

    compilation_tier._write_compilation_comparisons_jsonl(
        context,
        path,
        engines=engines,
        invoke_pure_phase=False,
        observation_filter=keep_a,
    )
    compilation_tier._write_compilation_comparisons_jsonl(
        context,
        path,
        engines=engines,
        invoke_pure_phase=False,
        observation_filter=keep_a_again,
    )
    assert calls["n"] == 4
    compilation_tier._write_compilation_comparisons_jsonl(
        context,
        path,
        engines=engines,
        invoke_pure_phase=False,
        observation_filter=keep_b,
    )
    assert calls["n"] == 5

    monkeypatch.setenv("REGOLITH_MAGEMIN_BINARY", str(tmp_path / "missing-a"))
    compilation_tier._write_compilation_comparisons_jsonl(
        context, path, engines=engines, invoke_pure_phase=True
    )
    monkeypatch.setenv("REGOLITH_MAGEMIN_BINARY", str(tmp_path / "missing-b"))
    compilation_tier._write_compilation_comparisons_jsonl(
        context, path, engines=engines, invoke_pure_phase=True
    )
    assert calls["n"] == 7
    compilation_tier._write_compilation_comparisons_jsonl(
        context, path, engines=engines, invoke_pure_phase=False
    )
    assert calls["n"] == 7


def test_source_band_reuse_follows_the_snapshot_and_the_ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod

    _use_cache(monkeypatch, tmp_path / "cache")
    context = _registered_context(tmp_path)
    calls = {"n": 0}
    band = DecisionBand(Decimal("1.5"), "K", "derived_scatter")

    def compute(_context, _entries):
        calls["n"] += 1
        return ({"group": band}, [{"group_key": {"formula": "MgO"}}], {"other": "zero_scatter_aligned_group"})

    monkeypatch.setattr(score_mod, "_source_band_catalog_compute", compute)
    first_bands, first_records, first_reasons = score_mod._source_band_catalog(context, {})
    second_bands, second_records, second_reasons = score_mod._source_band_catalog(context, {})
    assert calls["n"] == 1
    assert second_bands["group"].value == first_bands["group"].value
    assert second_bands["group"] is not first_bands["group"]
    assert second_records == first_records
    assert second_reasons == first_reasons

    score_mod._source_band_catalog(context, {"group": {"ticket_line": "1"}})
    assert calls["n"] == 2
    plain = ScoreContext(works={}, experiments={}, observations={}, root=tmp_path)
    score_mod._source_band_catalog(plain, {})
    assert calls["n"] == 3
    score_mod._source_band_catalog(context, {"bad": {"values": {1}}})
    score_mod._source_band_catalog(context, {"bad": {"values": {1}}})
    assert calls["n"] == 5


def _environ_string_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and _is_os_environ(node.func.value):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                found.add(slice_node.value)
    return found


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def test_loader_settings_are_harmless_or_absent_from_the_records() -> None:
    import simulator.yaml_cache as yaml_cache
    from simulator.battery import parsed_store_cache as cache_mod

    names = {
        yaml_cache._CACHE_ENV,
        yaml_cache._CACHE_ENABLED_ENV,
        yaml_cache._CACHE_MAX_BYTES_ENV,
        cache_mod._CACHE_SWITCH,
        cache_mod._CACHE_DIR_ENV,
    }
    for relative in (
        "simulator/yaml_cache.py",
        "simulator/battery/migrate.py",
        "simulator/battery/validate.py",
        "simulator/battery/parsed_store_cache.py",
    ):
        names.update(_environ_string_names(REPO_ROOT / relative))
    missing = sorted(names.difference(HARMLESS_LOADER_SETTINGS))
    assert missing == []
    assert "REGOLITH_PARSED_STORE_CACHE" in HARMLESS_LOADER_SETTINGS


def test_compilation_entry_files_are_in_the_reuse_closure() -> None:
    from simulator.battery.compilation_tier import _COMPILATION_REUSE_ENTRY_FILES

    files = set(reachable_source_files(REPO_ROOT, _COMPILATION_REUSE_ENTRY_FILES))
    missing = [relative for relative in _COMPILATION_REUSE_ENTRY_FILES if relative not in files]
    assert missing == []
    assert "simulator/chemistry/ellingham_thermo.py" in files


def test_source_band_entry_files_are_in_the_reuse_closure() -> None:
    from simulator.battery.score import _SOURCE_BAND_CODE_FILES

    files = set(reachable_source_files(REPO_ROOT, _SOURCE_BAND_CODE_FILES))
    missing = [relative for relative in _SOURCE_BAND_CODE_FILES if relative not in files]
    assert missing == []
