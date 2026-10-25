"""Storage-only runner migration: exact bytes, typed changes, capture continuity."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.golden_sections import (
    PRODUCER_CONTRACT, check_manifest, diff_nodes, flux_sections, json_bytes,
    load_json, nodes, partition, read_sections, reassemble, reassemble_flux, write_sections,
)
from tests.test_runner_smoke import SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
BASE = "b1708cd98ffa4edfd0fa61cae3aea6a3e8552e0f"
LAYOUT = ROOT / "tests/fixtures/golden_sections/runner-layout.json"


@pytest.fixture(scope="session")
def generated_runner_sections(tmp_path_factory):
    root = tmp_path_factory.mktemp("runner-sections")
    generated = {}
    for scenario in SCENARIOS:
        fixture = f"tests/fixtures/runner/{scenario['fixture']}"
        current = (ROOT / fixture).read_bytes()
        files = partition(load_json(current))
        destination = root / scenario["name"]
        write_sections(destination, files)
        generated[scenario["name"]] = (current, destination)
    return generated


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_runner_byte_roundtrip_on_current_fixture(scenario, generated_runner_sections):
    path = f"tests/fixtures/runner/{scenario['fixture']}"
    current = (ROOT / path).read_bytes()
    generated_current, directory = generated_runner_sections[scenario["name"]]
    assert generated_current == current
    files = read_sections(directory)
    assert partition(load_json(current)) == files
    assert reassemble(files) == current


def _runner_structure(files):
    section_names = sorted(name for name in files if name != "index.json")
    return {
        "section_count": len(section_names),
        "section_filenames_sha256": hashlib.sha256("\n".join(section_names).encode()).hexdigest(),
        "index_sha256": hashlib.sha256(files["index.json"]).hexdigest(),
    }


def _section_content_digest(files):
    content_pairs = sorted((name, hashlib.sha256(raw).hexdigest())
                           for name, raw in files.items())
    return hashlib.sha256(json.dumps(content_pairs, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_step2_migration_proof_on_base(scenario):
    path = f"tests/fixtures/runner/{scenario['fixture']}"
    pinned = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    files = partition(load_json(pinned))
    assert reassemble(files) == pinned
    expected = json.loads(LAYOUT.read_text(encoding="utf-8"))[scenario["name"]]
    assert _section_content_digest(files) == expected["content_sha256"]


def test_runner_section_layout_matches_pinned_expectations(generated_runner_sections):
    expected = json.loads(LAYOUT.read_text(encoding="utf-8"))
    assert set(expected) == set(generated_runner_sections)
    for scenario, (_, directory) in generated_runner_sections.items():
        files = read_sections(directory)
        # Content pins certify the historical migration, not future golden values.
        assert _runner_structure(files) == {
            key: value for key, value in expected[scenario].items() if key != "content_sha256"
        }


def test_partition_fresh_process_hash_seeds():
    code = """
import hashlib
from pathlib import Path
from tests.golden_sections import load_json, partition
h = hashlib.sha256()
for source in sorted(Path("tests/fixtures/runner").glob("*.json")):
    for name, raw in partition(load_json(source.read_bytes())).items():
        h.update(name.encode() + b"\\0" + raw + b"\\0")
print(h.hexdigest())
"""
    results = [subprocess.check_output([sys.executable, "-c", code], cwd=ROOT,
                                      env={**os.environ, "PYTHONHASHSEED": seed}, timeout=60)
               for seed in ("0", "370")]
    assert results[0] == results[1]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_raw_flux_capture_continuity(scenario, runner_golden_loader, generated_runner_sections):
    _, capture, expected_text = runner_golden_loader(scenario)
    current, directory = generated_runner_sections[scenario["name"]]
    assert current == expected_text.encode()
    _assert_raw_flux_capture_continuity(current, read_sections(directory), capture["flux_maps"])


def _assert_raw_flux_capture_continuity(current, files, flux_maps):
    assert reassemble(files) == current
    flux = flux_sections(flux_maps)
    restored = reassemble_flux(flux)
    # Keys normalize only the captured evidence, with dispatch ordinal intact.
    assert json.dumps(restored, sort_keys=True) == json.dumps(flux_maps, sort_keys=True)
    for raw in flux.values():
        value = load_json(raw)
        if isinstance(value, dict):
            assert list(value) == sorted(value)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_value_only_fixture_move_roundtrips_with_capture_continuity(
    scenario, tmp_path, runner_golden_loader, generated_runner_sections,
):
    current, directory = generated_runner_sections[scenario["name"]]
    before = read_sections(directory)
    payload = load_json(current)
    payload["per_hour_summary"][0]["T_C"] += 1.0
    moved_fixture = tmp_path / scenario["fixture"]
    moved_fixture.write_bytes(json_bytes(payload))
    moved = moved_fixture.read_bytes()
    assert moved != current
    destination = tmp_path / "sections"
    write_sections(destination, partition(load_json(moved)))
    after = read_sections(destination)
    assert _runner_structure(after) == _runner_structure(before)
    assert _section_content_digest(after) != _section_content_digest(before)
    _, capture, _ = runner_golden_loader(scenario)
    _assert_raw_flux_capture_continuity(moved, after, capture["flux_maps"])


def _declaration(old, new):
    rows = [dict(row, cause="measured") for row in diff_nodes("runner/example", old, new)]
    manifest = {"format": "golden-moves-v1", "offer": "test", "base_green": BASE,
                "producer_contract": PRODUCER_CONTRACT, "expected_diff": "expected.jsonl",
                "classes": {"measured": {"cause": "synthetic checker control",
                    "owner_decision": "test-only", "summary": "exact synthetic rows", "count": len(rows)}}}
    return manifest, rows


def _check(old, new, manifest, rows):
    check_manifest({"runner/example": old}, {"runner/example": new}, json_bytes(manifest),
                   b"".join(json.dumps(row, allow_nan=False).encode() + b"\n" for row in rows),
                   base_green=BASE)


@pytest.fixture
def declared_move():
    old = {"value": 1.0, "ordered": [1, 2], "remove": None}
    new = {**copy.deepcopy(old), "value": 2.0}
    manifest, rows = _declaration(old, new)
    return old, new, manifest, rows


def test_exact_declared_move_passes(declared_move):
    _check(*declared_move)


def test_single_offer_contract_pin_before_cli_extension():
    old, new = {"value": -0.0}, {"value": 0.0}
    manifest, rows = _declaration(old, new)
    assert rows == [{"artifact": "runner/example", "section": "envelope.json",
                     "path": "/value", "old": {"kind": "f64", "hex": "-0x0.0p+0"},
                     "new": {"kind": "f64", "hex": "0x0.0p+0"}, "cause": "measured"}]
    _check(old, new, manifest, rows)
    rows[0]["old"]["hex"] = "0x1.0000000000000p+0"
    with pytest.raises(ValueError, match="^wrong old value$"):
        _check(old, new, manifest, rows)


@pytest.mark.parametrize("mutation", [
    "undeclared-leaf", "missing-declared", "wrong-old", "reordered-list",
    "type-change", "added-key", "removed-key",
])
def test_manifest_rejects_seven_required_mutations(declared_move, mutation):
    old, new, manifest, rows = copy.deepcopy(declared_move)
    if mutation == "undeclared-leaf":
        new["ordered"][0] = 3
    elif mutation == "missing-declared":
        new["value"] = old["value"]
    elif mutation == "wrong-old":
        rows[0]["old"]["hex"] = (0.5).hex()
    elif mutation == "reordered-list":
        new["ordered"].reverse()
    elif mutation == "type-change":
        new["value"] = 2
    elif mutation == "added-key":
        new["extra"] = {}
    elif mutation == "removed-key":
        del new["remove"]
    with pytest.raises(ValueError):
        _check(old, new, manifest, rows)


@pytest.mark.parametrize("new", [
    {"a": True}, {"a": -0.0}, {"a": []}, {"b": None},
    {"a": {"x": [], "y": None}}, {},
])
def test_declared_structural_and_typed_moves_pass(new):
    old = {"a": 0.0}
    manifest, rows = _declaration(old, new)
    _check(old, new, manifest, rows)
    if len(rows) > 1:
        rows.pop()
        manifest["classes"]["measured"]["count"] -= 1
        with pytest.raises(ValueError):
            _check(old, new, manifest, rows)


@pytest.mark.parametrize("key,old_value", [
    ("per_hour_summary", [{"hour": 1}]), ("final_state", {"account": {}}),
    ("stage_purity_report", {}), ("run_metadata", {}),
])
def test_reserved_runner_block_type_changes_are_typed_rows(key, old_value):
    old, new = {key: old_value}, {key: None}
    manifest, rows = _declaration(old, new)
    _check(old, new, manifest, rows)
    assert reassemble(partition(new)) == json_bytes(new)


@pytest.mark.parametrize("mutation", ["duplicate-row", "unknown-class", "count", "noop",
                                     "malformed-type", "wrong-base", "wrong-section", "unsorted"])
def test_manifest_format_rejections(declared_move, mutation):
    old, new, manifest, rows = copy.deepcopy(declared_move)
    if mutation == "duplicate-row":
        rows *= 2
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
        manifest["base_green"] = "wrong"
    elif mutation == "wrong-section":
        rows[0]["section"] = "unowned.json"
    elif mutation == "unsorted":
        new["ordered"] = [2, 1]
        manifest, rows = _declaration(old, new)
        rows.reverse()
    with pytest.raises(ValueError):
        _check(old, new, manifest, rows)


def test_duplicate_json_keys_rejected(declared_move):
    old, new, manifest, rows = declared_move
    with pytest.raises(ValueError, match="duplicate JSON key"):
        check_manifest({"runner/example": old}, {"runner/example": new},
                       json_bytes(manifest).replace(b'"offer": "test"', b'"offer": "test", "offer": "other"'),
                       b"", base_green=BASE)


def test_typed_nodes_preserve_float_bits_and_presence():
    pinned = nodes({"null": None, "empty": {}, "list": [], "i": 0,
                    "f": 0.0, "minus": -0.0, "bool": False})
    assert len({json.dumps(pinned["/" + k]) for k in ("null", "empty", "list", "i", "f", "minus", "bool")}) == 7
    with pytest.raises(ValueError):
        nodes({"nan": float("nan")})


def test_flux_capture_keeps_null_empty_and_dispatch_order():
    capture = [None, {}, {"z": -0.0, "a": 1.0}, None]
    files = flux_sections(capture)
    restored = reassemble_flux(files)
    assert nodes(restored) == nodes([None, {}, {"a": 1.0, "z": -0.0}, None])
    assert capture[2] == {"z": -0.0, "a": 1.0}
    assert list(capture[2]) == ["z", "a"]


def test_pointer_and_filename_escaping_and_order():
    payload = {"~/../%": {"/": [-0.0, None, {}, []]}, "envelope": {},
               "per_hour_summary": [{"hour": 0, "state": {"/": True}}]}
    files = partition(payload)
    assert reassemble(files) == json_bytes(payload)
    assert "/~0~1..~1%" in nodes(payload)
    assert all(".." not in name.split("/") for name in files)


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate-owner", "overlap"])
def test_reassembly_rejects_broken_coverage(mutation):
    files = partition({"value": 1, "block": {"x": 2}})
    if mutation == "extra":
        files["extra.json"] = b"{}\n"
    elif mutation == "missing":
        del files["envelope.json"]
    elif mutation == "duplicate-owner":
        files["field-block.json"] = json_bytes({"/block": {"x": 2}, "/value": 1})
    else:
        files["field-block.json"] = json_bytes({"/block": {"x": 2}, "/block/x": 2})
    with pytest.raises(ValueError):
        reassemble(files)
