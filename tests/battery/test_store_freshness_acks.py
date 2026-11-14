"""File-backed acknowledgements for store-neutral input commits.

Invariant: no migrate-input change is silent. Every path is one input class
or not an input, and ``stale_input_commits`` refuses an unacknowledged
post-store commit of every class. One receipt does not cover another class.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests.git_history import require_store_neutral_ack_history

REPO_ROOT = Path(__file__).resolve().parents[2]
_STALE_INPUT = "data/literature/extracts/example.yaml"

# One specimen for every input_class branch, plus the suffixes that class accepts.
INPUT_CLASS_SPECIMENS = (
    ("exact", "data/literature/INDEX.yaml"),
    ("exact", "data/literature/lab_parameter_vocabulary.yaml"),
    ("code", "simulator/accounting/exceptions.py"),
    ("code", "simulator/accounting/formulas.py"),
    ("code", "simulator/physical_constants.py"),
    ("code", "simulator/reference_data/janaf.py"),
    ("code", "simulator/state_types.py"),
    ("battery_package", "simulator/battery/migrate.py"),
    ("battery_package", "simulator/battery/score.py"),
    ("extract", "data/literature/extracts/paper.yaml"),
    ("compilation", "data/literature/compilations/janaf/al.yaml"),
    ("compilation", "data/literature/compilations/family/table.yml"),
    ("compilation", "data/literature/compilations/family/table.json"),
    ("named_source", "data/literature/ledger.yaml"),
)
INPUT_CLASS_NAMES = tuple(sorted({name for name, _path in INPUT_CLASS_SPECIMENS}))

# Paths the byte-check commit also touches, and the near-misses of each class.
# None of these may become a silent input or a second refusal path.
INPUT_CLASS_NEAR_MISSES = (
    "data/literature/extracts/_draft.yaml",
    "data/literature/extracts/SCHEMA.yaml",
    "data/literature/extracts/Schema-notes.yaml",
    "data/literature/extracts/nested/paper.yaml",
    "data/literature/compilations/README.md",
    "data/literature/compilations/family/__pycache__/table.yaml",
    "data/literature/extracts-v2/paper.yaml",
    "data/literature/works/work.yaml",
    "data/literature/observations-v2/row.yaml",
    "data/literature/observations-v2/compilations-janaf/janaf-Al.yaml",
    "scripts/battery_migrate.py",
    "scripts/check_store_freshness.py",
    "scripts/store_neutral_acks/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt",
    "tests/battery/test_store_regeneration.py",
    "docs/validation-battery.md",
)


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_log(*commits: str) -> str:
    return "".join(
        f"{commit}\0input change {index}\n\n{_STALE_INPUT}\n"
        for index, commit in enumerate(commits)
    )


def _fake_git(log: str, resolutions: dict[str, str]):
    def run(*args: str) -> str:
        if args[0] == "rev-parse":
            stem = args[-1].removesuffix("^{commit}")
            return resolutions[stem] + "\n"
        return log

    return run


def test_store_neutral_ack_file_exempts_stale_input_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "abcdef1" + "0" * 33
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdef1.txt").write_text(
        "Fixed-point migration produced no store diff.\n", encoding="utf-8"
    )
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log(commit), {"abcdef1": commit})
    )

    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_full_commit_id_ack_file_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "a" * 40
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / f"{commit}.txt").write_text("Fixed-point regen.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log(commit), {commit: commit})
    )

    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_ambiguous_ack_prefix_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "eb850d2.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)

    # Simulate git's response to an ambiguous short oid. Whether a real prefix
    # is ambiguous depends on which objects the local clone holds, so the test
    # must not depend on the repository's contents.
    def ambiguous_rev_parse(*args: str) -> str:
        if "rev-parse" in args:
            raise subprocess.CalledProcessError(
                128,
                ["git", *args],
                output="",
                stderr="error: short object ID eb850d2 is ambiguous\n"
                "fatal: Needed a single revision\n",
            )
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(freshness, "_git", ambiguous_rev_parse)
    with pytest.raises(subprocess.CalledProcessError) as error:
        freshness._store_neutral_ack_commits()
    assert "ambiguous" in (error.value.stderr or "")


def test_malformed_store_neutral_ack_name_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "not-a-sha.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


@pytest.mark.parametrize("entry_name", ["README.md", "abcdefg.md", "abcdefg.TXT"])
def test_ack_directory_strays_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_name: str
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / entry_name).write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_directory_subdirectories_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    (ack_dir / "abcdefa.txt").mkdir(parents=True)
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


def test_empty_ack_body_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdefa.txt").write_text(" \n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="one reason line"):
        freshness.stale_input_commits("HEAD", "store")


def test_multiline_ack_body_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdefa.txt").write_text(
        "first reason line\n\nsecond reason line\n", encoding="utf-8"
    )
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="one reason line"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_cannot_duplicate_frozenset_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "037f97f.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    duplicate = "037f97ff6" + "a" * 31
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log("a" * 40), {"037f97f": duplicate})
    )

    with pytest.raises(ValueError, match="duplicate store-neutral acknowledgement"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_for_one_commit_does_not_exempt_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdef1.txt").write_text("Reason.\n", encoding="utf-8")
    acknowledged = "abcdef1" + "0" * 33
    stale_commit = "bbcdef2" + "0" * 33
    log = _fake_log(acknowledged, stale_commit)
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness,
        "_git",
        _fake_git(log, {"abcdef1": acknowledged}),
    )

    stale = freshness.stale_input_commits("HEAD", "store")

    assert set(stale) == {f"{stale_commit[:9]} input change 1"}


def test_existing_store_neutral_frozenset_still_exempts_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "037f97ff6" + "a" * 31
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log(commit))

    assert freshness.stale_input_commits("HEAD", "store") == {}


def _specimen_commit(index: int) -> str:
    return f"{index + 1:040x}"


def _specimen_log() -> tuple[str, dict[str, str], dict[str, str]]:
    """One commit per specimen. Maps class and path back to that commit."""
    resolutions: dict[str, str] = {}
    path_commits: dict[str, str] = {}
    chunks: list[str] = []
    for index, (class_name, path) in enumerate(INPUT_CLASS_SPECIMENS):
        commit = _specimen_commit(index)
        resolutions[commit] = commit
        path_commits[path] = commit
        chunks.append(f"{commit}\0{class_name} input\n\n{path}\n")
    return "".join(chunks), resolutions, path_commits


def _install_acks(
    root: Path, commits: dict[str, str], monkeypatch: pytest.MonkeyPatch, log: str,
    resolutions: dict[str, str],
) -> object:
    freshness = _load_freshness()
    ack_dir = root / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    for commit in commits.values():
        (ack_dir / f"{commit}.txt").write_text(
            "Fixed-point regeneration produced no store diff.\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(freshness, "ROOT", root)
    monkeypatch.setattr(freshness, "_git", _fake_git(log, resolutions))
    return freshness


@pytest.mark.parametrize(("class_name", "path"), INPUT_CLASS_SPECIMENS)
def test_each_input_class_is_refused_without_an_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, class_name: str, path: str
) -> None:
    """Every input class reaches the same refusal. No class is a silent pass."""
    freshness = _load_freshness()
    commit = "c" * 40
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness,
        "_git",
        _fake_git(_fake_log(commit).replace(_STALE_INPUT, path), {commit: commit}),
    )

    assert freshness.input_class(path) == class_name
    assert freshness.commit_walk_includes(path)
    assert freshness._is_input(path)
    stale = freshness.stale_input_commits("HEAD", "store")
    assert stale == {f"{commit[:9]} input change 0": [path]}


@pytest.mark.parametrize(("class_name", "path"), INPUT_CLASS_SPECIMENS)
def test_each_input_class_is_exempt_only_with_its_own_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, class_name: str, path: str
) -> None:
    freshness = _install_acks(
        tmp_path,
        {path: "d" * 40},
        monkeypatch,
        _fake_log("d" * 40).replace(_STALE_INPUT, path),
        {"d" * 40: "d" * 40},
    )

    assert freshness.input_class(path) == class_name
    assert freshness.stale_input_commits("HEAD", "store") == {}


@pytest.mark.parametrize("path", INPUT_CLASS_NEAR_MISSES)
def test_near_miss_paths_are_not_migrate_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    freshness = _load_freshness()
    commit = "e" * 40
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness,
        "_git",
        _fake_git(_fake_log(commit).replace(_STALE_INPUT, path), {commit: commit}),
    )

    assert freshness.input_class(path) is None
    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_one_acknowledgement_does_not_exempt_other_input_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt for one class leaves every other class refused."""
    log, resolutions, path_commits = _specimen_log()
    acknowledged_path = "simulator/battery/migrate.py"
    acknowledged = path_commits[acknowledged_path]
    freshness = _install_acks(
        tmp_path, {acknowledged_path: acknowledged}, monkeypatch, log, resolutions
    )

    stale = freshness.stale_input_commits("HEAD", "store")
    stale_paths = {path for paths in stale.values() for path in paths}

    assert acknowledged_path not in stale_paths
    assert stale_paths == {
        path for _class_name, path in INPUT_CLASS_SPECIMENS if path != acknowledged_path
    }
    refused_classes = {freshness.input_class(path) for path in stale_paths}
    assert refused_classes == set(INPUT_CLASS_NAMES)


def test_tracked_tree_uses_exactly_the_declared_input_classes() -> None:
    """A new tracked path class fails here instead of becoming a silent pass."""
    freshness = _load_freshness()
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    observed: set[str | None] = set()
    for path in listed:
        class_name = freshness.input_class(path)
        if class_name is None:
            continue
        assert freshness.commit_walk_includes(path), path
        observed.add(class_name)

    assert observed == set(INPUT_CLASS_NAMES)
    for class_name, path in INPUT_CLASS_SPECIMENS:
        assert freshness.input_class(path) == class_name
        assert freshness.commit_walk_includes(path)


def _committed_stale_by_class() -> dict[str, list[str]]:
    freshness = _load_freshness()
    require_store_neutral_ack_history(REPO_ROOT, freshness)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    last_store = subprocess.run(
        ["git", "log", "-1", "--format=%H", head, "--", *freshness.STORE_OUTPUTS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert last_store, "no store-output commit found"
    stale = freshness.stale_input_commits(head, last_store)
    by_class: dict[str, list[str]] = {name: [] for name in INPUT_CLASS_NAMES}
    for title, paths in stale.items():
        for path in paths:
            class_name = freshness.input_class(path)
            assert class_name is not None, path
            by_class[class_name].append(f"{title}: {path}")
    return by_class


@pytest.mark.parametrize("class_name", INPUT_CLASS_NAMES)
def test_committed_range_acknowledges_every_input_class(class_name: str) -> None:
    """Enumerate the committed range by class, not by one reviewer's commit."""
    offenders = _committed_stale_by_class()[class_name]
    assert not offenders, (
        f"input class {class_name} changed after the last store output "
        "without a store-neutral acknowledgement:\n" + "\n".join(offenders)
    )
