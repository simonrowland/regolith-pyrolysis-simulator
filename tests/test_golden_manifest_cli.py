"""Exercise the worker/train entry points, including exact rejection controls."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.golden_sections import json_bytes, load_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests/fixtures/runner").glob("*.json"))
PRIMARY = FIXTURES[0].name


def _run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)


def _snapshot(directory, payload):
    directory.mkdir()
    for fixture in FIXTURES:
        (directory / fixture.name).write_bytes(json_bytes(
            payload if fixture.name == PRIMARY else {"status": "ok"}))
    return directory


def _offer(directory, base, tip, *, compressed=False, offer="test"):
    directory.mkdir()
    class_map = directory / "class-map.json"
    class_map.write_bytes(json_bytes({"offer": offer, "classes": {
        "measured": {"cause": "synthetic control", "owner_decision": "test-only",
                     "summary": "all exact measured rows"}}, "rules": [{"cause": "measured"}]}))
    output = directory / ("expected.jsonl.gz" if compressed else "expected.jsonl")
    result = _run("make_golden_manifest.py", "--base", base, "--tip", tip,
                  "--class-map", class_map, "--out", output)
    assert result.returncode == 0, result.stdout + result.stderr
    return directory / "expected.json"


def _check(base, tip, *manifests):
    return _run("check_golden_manifest.py", "--base", base, "--tip", tip,
                *(arg for path in manifests for arg in ("--manifest", path)))


@pytest.fixture
def move(tmp_path):
    old = {"status": "ok", "value": 1.0, "ordered": [1, 2], "remove": None}
    new = {**copy.deepcopy(old), "value": 2.0}
    base = _snapshot(tmp_path / "base", old)
    tip = _snapshot(tmp_path / "tip", new)
    manifest = _offer(tmp_path / "offer", base, tip)
    return old, new, base, tip, manifest


def _rewrite(manifest_path, change):
    manifest = load_json(manifest_path.read_bytes())
    rows_path = manifest_path.parent / manifest["expected_diff"]
    rows = [load_json(line) for line in rows_path.read_bytes().splitlines()]
    change(manifest, rows)
    manifest_path.write_bytes(json_bytes(manifest))
    rows_path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))


def test_measured_offer_and_exact_file_identities(move):
    _, _, base, tip, manifest = move
    result = _check(base, tip, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "D=1 E=1 exact=yes" in result.stdout
    assert "COVERAGE-EXCEPTION: sio_yield" in result.stdout
    for path in (manifest, manifest.parent / "expected.jsonl"):
        assert f"SHA256 {hashlib.sha256(path.read_bytes()).hexdigest()} {path}" in result.stdout
    assert 'D-CLASSES {"test:measured": 1}' in result.stdout


@pytest.mark.parametrize("mutation", [
    "undeclared-leaf", "missing-declared", "wrong-old", "reordered-list",
    "type-change", "added-key", "removed-key",
])
def test_cli_rejects_step2_seven_mutations(move, mutation):
    old, new, base, tip, manifest = move
    if mutation == "undeclared-leaf":
        new["ordered"][0] = 3
    elif mutation == "missing-declared":
        new["value"] = old["value"]
    elif mutation == "wrong-old":
        _rewrite(manifest, lambda m, r: r[0]["old"].update(hex=(0.5).hex()))
    elif mutation == "reordered-list":
        new["ordered"].reverse()
    elif mutation == "type-change":
        new["value"] = 2
    elif mutation == "added-key":
        new["extra"] = {}
    elif mutation == "removed-key":
        del new["remove"]
    (tip / PRIMARY).write_bytes(json_bytes(new))
    result = _check(base, tip, manifest)
    assert result.returncode != 0, result.stdout
    category = "WRONG-OLD" if mutation == "wrong-old" else "MISSING" if mutation == "missing-declared" else "UNDECLARED"
    assert category + " " in result.stdout
    assert '"section":' in result.stdout and '"path":' in result.stdout
    assert '"old":' in result.stdout and '"new":' in result.stdout


@pytest.mark.parametrize("mutation", ["duplicate-row", "unknown-class", "count", "noop",
                                     "malformed-type", "wrong-base", "wrong-section", "unsorted"])
def test_cli_rejects_step2_format_mutations(move, mutation):
    _, _, base, tip, path = move

    def change(manifest, rows):
        if mutation == "duplicate-row":
            rows.extend(copy.deepcopy(rows))
            manifest["classes"]["measured"]["count"] = 2
        elif mutation == "unknown-class":
            rows[0]["cause"] = "unknown"
        elif mutation == "count":
            manifest["classes"]["measured"]["count"] += 1
        elif mutation == "noop":
            rows[0]["new"] = rows[0]["old"]
        elif mutation == "malformed-type":
            rows[0]["new"] = {"kind": "integer", "value": True}
        elif mutation == "wrong-base":
            manifest["base_green"] = "not-a-git-revision"
        elif mutation == "wrong-section":
            rows[0]["section"] = "unowned.json"
        elif mutation == "unsorted":
            rows.insert(0, {**rows[0], "path": "/zzz"})
            manifest["classes"]["measured"]["count"] = 2
    _rewrite(path, change)
    result = _check(base, tip, path)
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("case", ["disjoint", "chain", "independent-overlap", "wrong-base-chain", "cancelled", "gzip"])
def test_required_composition_cases(tmp_path, case):
    old = {"status": "ok", "a": 0.0, "b": 0.0}
    first = {**old, "a": 1.0}
    final = {**first, "a": 2.0}
    if case == "disjoint":
        final = {**first, "b": 2.0}
    elif case == "cancelled":
        final = old
    base = _snapshot(tmp_path / "base", old)
    middle = _snapshot(tmp_path / "middle", first)
    tip = _snapshot(tmp_path / "tip", final)
    a = _offer(tmp_path / "A", base, middle, offer="A", compressed=case == "gzip")
    if case == "disjoint":
        independent = _snapshot(tmp_path / "independent", {**old, "b": 2.0})
        b = _offer(tmp_path / "B", base, independent, offer="B")
    else:
        b = _offer(tmp_path / "B", base if case == "independent-overlap" else middle,
                   tip, offer="B")
    if case == "wrong-base-chain":
        _rewrite(b, lambda m, r: m.update(base_green=load_json(a.read_bytes())["base_green"]))
    result = _check(base, tip, a.parent / "expected.jsonl.gz" if case == "gzip" else a, b)
    passing = case not in {"independent-overlap", "wrong-base-chain"}
    assert (result.returncode == 0) == passing, result.stdout + result.stderr
    if not passing:
        assert "OVERLAP " in result.stdout
    if case == "cancelled":
        assert "D=0 E=0 exact=yes" in result.stdout
        assert "CANCELLED: count=1 shown=1" in result.stdout
        assert '"offer": "A"' in result.stdout and '"offer": "B"' in result.stdout
    if case == "gzip":
        rows = a.parent / "expected.jsonl.gz"
        assert gzip.decompress(rows.read_bytes())
        assert hashlib.sha256(rows.read_bytes()).hexdigest() in result.stdout


@pytest.mark.parametrize("replacement", [True, -0.0, [], None, {"x": [], "y": None}, {}])
def test_atomic_structural_chain(tmp_path, replacement):
    old = {"status": "ok", "a": {"child": 0.0}}
    intermediate = {"status": "ok", "a": replacement}
    new = {"status": "ok", "a": 1}
    base = _snapshot(tmp_path / "base", old)
    middle = _snapshot(tmp_path / "middle", intermediate)
    tip = _snapshot(tmp_path / "tip", new)
    a = _offer(tmp_path / "A", base, middle, offer="A")
    b = _offer(tmp_path / "B", middle, tip, offer="B")
    result = _check(base, tip, a, b)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("order", [("A", "B", "C"), ("B", "A", "C")])
def test_continuation_can_omit_unrelated_disjoint_offer(tmp_path, order):
    old = {"status": "ok", "a": 0, "b": 0}
    base = _snapshot(tmp_path / "base", old)
    a_tip = _snapshot(tmp_path / "a-tip", {**old, "a": 1})
    b_tip = _snapshot(tmp_path / "b-tip", {**old, "b": 1})
    c_tip = _snapshot(tmp_path / "c-tip", {**old, "a": 2})
    tip = _snapshot(tmp_path / "tip", {**old, "a": 2, "b": 1})
    offers = {"A": _offer(tmp_path / "A", base, a_tip, offer="A"),
              "B": _offer(tmp_path / "B", base, b_tip, offer="B"),
              "C": _offer(tmp_path / "C", a_tip, c_tip, offer="C")}
    result = _check(base, tip, *(offers[k] for k in order))
    assert result.returncode == 0, result.stdout + result.stderr


def test_independent_overlap_after_cancellation_is_rejected(tmp_path):
    old = {"status": "ok", "a": 0}
    base = _snapshot(tmp_path / "base", old)
    middle = _snapshot(tmp_path / "middle", {**old, "a": 1})
    tip = _snapshot(tmp_path / "tip", {**old, "a": 2})
    a = _offer(tmp_path / "A", base, middle, offer="A")
    b = _offer(tmp_path / "B", middle, base, offer="B")
    c = _offer(tmp_path / "C", base, tip, offer="C-independent")
    result = _check(base, tip, a, b, c)
    assert result.returncode != 0
    assert 'OVERLAP {"artifact":' in result.stdout


def test_review_cancelled_chain_with_disjoint_measurement_base():
    repro = ROOT / "tests/fixtures/golden_sections/step3-ror-repro"
    # Every measured offer is valid alone; C still predates the x chain.
    for base, tip, offers in [("g", "u-tip", ("U",)), ("g", "a-tip", ("A",)),
                              ("a-tip", "g", ("cancel",)), ("u-tip", "final", ("C",))]:
        result = _check(repro / base, repro / tip,
                        *(repro / offer / "expected.json" for offer in offers))
        assert result.returncode == 0, result.stdout + result.stderr
    result = _check(repro / "g", repro / "final",
                    *(repro / offer / "expected.json" for offer in ("U", "A", "cancel", "C")))
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'OVERLAP {"artifact":' in result.stdout
    assert '"offer": "C-independent-of-A-and-cancel"' in result.stdout


def test_continuation_can_measure_descendant_of_previous_edge(tmp_path):
    old = {"status": "ok", "a": 0, "b": 0}
    base = _snapshot(tmp_path / "base", old)
    first = _snapshot(tmp_path / "first", {**old, "a": 1})
    descendant = _snapshot(tmp_path / "descendant", {**old, "a": 1, "b": 1})
    tip = _snapshot(tmp_path / "tip", {**old, "a": 2, "b": 1})
    a = _offer(tmp_path / "A", base, first, offer="A")
    b = _offer(tmp_path / "B", first, descendant, offer="B")
    c = _offer(tmp_path / "C", descendant, tip, offer="C")
    result = _check(base, tip, a, b, c)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "D=2 E=2 exact=yes" in result.stdout


@pytest.mark.parametrize("status", ["refused", "unavailable"])
def test_review_cancelled_non_ok_intermediate(status):
    repro = ROOT / "tests/fixtures/golden_sections/step3-ror-repro"
    result = _check(repro / "g", repro / "g",
                    *(repro / f"intermediate-{status}-{i}" / "expected.json" for i in (0, 1)))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "runner output refused/unavailable" in result.stderr
    assert f"status={status!r}" in result.stderr


def test_revision_extra_fixture_is_rejected(tmp_path):
    def git(*args, raw=None):
        return subprocess.check_output(["git", *args], input=raw, cwd=ROOT)

    # Create tree objects only: no branch/index changes or commits in this test.
    revision = "6bb8fc08c"
    blob = git("hash-object", "-w", "--stdin", raw=b'{"status":"ok"}').strip()
    child = None
    for parent, name in [("tests/fixtures/runner", "extra.json"),
                         ("tests/fixtures", "runner"), ("tests", "fixtures"), ("", "tests")]:
        tree = git("ls-tree", revision + (":" + parent if parent else ""))
        entries = [line for line in tree.splitlines() if line.split(b"\t")[1] != name.encode()]
        entries.append((b"100644 blob " + blob if child is None else b"040000 tree " + child)
                       + b"\t" + name.encode())
        child = git("mktree", raw=b"\n".join(entries) + b"\n").strip()
    rules = tmp_path / "rules.json"
    rules.write_bytes(json_bytes({"offer": "no-move", "classes": {}, "rules": []}))
    output = tmp_path / "manifest.json"
    generated = _run("make_golden_manifest.py", "--base", revision, "--tip", revision,
                     "--class-map", rules, "--out", output)
    assert generated.returncode == 0, generated.stderr
    result = _check(revision, child.decode(), output)
    assert result.returncode != 0
    assert "inventory mismatch" in result.stderr


def test_partial_parent_edit_rejected(move):
    _, new, base, tip, path = move
    new["ordered"] = None
    (tip / PRIMARY).write_bytes(json_bytes(new))
    path = _offer(path.parent / "structural", base, tip)

    def change(manifest, rows):
        rows[:] = [r for r in rows if not r["path"].startswith("/ordered/")]
        manifest["classes"]["measured"]["count"] = len(rows)
    _rewrite(path, change)
    result = _check(base, tip, path)
    assert result.returncode != 0
    assert "structure" in result.stderr


@pytest.mark.parametrize("status", ["refused", "unavailable", None])
def test_failed_output_is_not_admitted(move, status):
    _, new, base, tip, manifest = move
    new["status"] = status
    (tip / PRIMARY).write_bytes(json_bytes(new))
    assert _check(base, tip, manifest).returncode != 0
    failed = _run("make_golden_manifest.py", "--base", base, "--tip", tip,
                  "--class-map", manifest.parent / "class-map.json", "--out", manifest.parent / "bad.json")
    assert failed.returncode != 0
    assert not (manifest.parent / "bad.json").exists()


def test_revision_input_and_summary_out(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_bytes(json_bytes({"offer": "no-move", "classes": {}, "rules": []}))
    output = tmp_path / "manifest.json"
    result = _run("make_golden_manifest.py", "--base", "6bb8fc08c", "--tip", "6bb8fc08c",
                  "--class-map", rules, "--out", output)
    assert result.returncode == 0, result.stdout + result.stderr
    result = _check("6bb8fc08c", "6bb8fc08c", output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "D=0 E=0 exact=yes" in result.stdout


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "nonfinite", "duplicate-json-key"])
def test_producer_inventory_and_json_rejections(move, mutation):
    _, _, base, tip, manifest = move
    if mutation == "missing":
        (tip / PRIMARY).unlink()
    elif mutation == "extra":
        (tip / "unknown.json").write_text("{}")
    elif mutation == "duplicate":
        (tip / ("runner-" + PRIMARY + ".gz")).write_bytes(gzip.compress((tip / PRIMARY).read_bytes()))
    elif mutation == "nonfinite":
        (tip / PRIMARY).write_text('{"status":"ok","value":1e999}')
    else:
        (tip / PRIMARY).write_text('{"status":"ok","status":"ok"}')
    assert _check(base, tip, manifest).returncode != 0
