"""C6 per-studio grind manifest acceptance tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from simulator.config import load_config_bundle
from simulator.grind_preflight import grind_feedstock_stage0_route_coverage_violations

REPO_ROOT = Path(__file__).resolve().parent.parent
GRIND_DIR = REPO_ROOT / "docs-private" / "grind"
MOON_MANIFEST = GRIND_DIR / "manifest-c6-moon-studio1.json"
MARS_MANIFEST = GRIND_DIR / "manifest-c6-mars-stype-studio2.json"
STYPE_MANIFEST = GRIND_DIR / "manifest-c6-stype-studio2.json"
LAUNCH_SCRIPT = GRIND_DIR / "launch-c6-studio.sh"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_c6_manifests.py"
EPOCH_GRIND = REPO_ROOT / "scripts" / "epoch_grind.py"
PROFILE_DIR = GRIND_DIR / "profiles"

MOON_EXTRA = {"targeted_super_kreep_ore"}
REQUIRED_JOB_FIELDS = {
    "id",
    "feedstock",
    "profile",
    "budget",
    "strategy",
    "seed",
    "out",
    "fidelity",
    "parallel",
}
MIN_SEEDS_PER_CELL = 3


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_feedstocks(manifest: dict) -> set[str]:
    return {str(job["feedstock"]) for job in manifest.get("jobs", [])}


def _optimize_profile_feedstocks() -> set[str]:
    opt_dir = REPO_ROOT / "data" / "optimize_profiles"
    return {path.stem for path in opt_dir.glob("*.yaml")}


def _feedstock_sets() -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    data = yaml.safe_load((REPO_ROOT / "data" / "feedstocks.yaml").read_text(encoding="utf-8"))
    all_keys = set(data.keys())
    moon = {k for k in all_keys if k.startswith("lunar_")} | MOON_EXTRA
    mars_stype = all_keys - moon
    optimizable = _optimize_profile_feedstocks()
    return moon, mars_stype, all_keys, moon & mars_stype, optimizable


@pytest.fixture(scope="module")
def moon_manifest() -> dict:
    if not MOON_MANIFEST.is_file():
        pytest.skip(f"requires private C6 grind manifest: {MOON_MANIFEST}")
    return _load_manifest(MOON_MANIFEST)


@pytest.fixture(scope="module")
def mars_manifest() -> dict:
    if not MARS_MANIFEST.is_file():
        pytest.skip(f"requires private C6 grind manifest: {MARS_MANIFEST}")
    return _load_manifest(MARS_MANIFEST)


@pytest.fixture(scope="module")
def stype_manifest() -> dict:
    if not STYPE_MANIFEST.is_file():
        pytest.skip(f"requires private C6 grind manifest: {STYPE_MANIFEST}")
    return _load_manifest(STYPE_MANIFEST)


def test_manifest_schema_and_profiles(moon_manifest: dict, mars_manifest: dict) -> None:
    for label, manifest in (("moon", moon_manifest), ("mars", mars_manifest)):
        jobs = manifest["jobs"]
        assert jobs, f"{label} manifest has no jobs"
        for job in jobs:
            missing = REQUIRED_JOB_FIELDS - set(job.keys())
            assert not missing, f"{label} job {job.get('id')} missing fields: {sorted(missing)}"
            profile_path = GRIND_DIR / job["profile"]
            assert profile_path.is_file(), f"missing profile for {job['id']}: {profile_path}"


def test_feedstock_partition_disjoint_and_complete(
    moon_manifest: dict,
    mars_manifest: dict,
) -> None:
    expected_moon, expected_mars, all_keys, overlap, optimizable = _feedstock_sets()
    assert not overlap

    moon_jobs = {job["feedstock"] for job in moon_manifest["jobs"]}
    mars_jobs = {job["feedstock"] for job in mars_manifest["jobs"]}
    expected_moon_ready = expected_moon & optimizable
    expected_mars_ready = expected_mars & optimizable
    assert moon_jobs <= expected_moon
    assert mars_jobs <= expected_mars
    assert moon_jobs & mars_jobs == set()
    assert moon_jobs == expected_moon_ready, f"moon missing: {sorted(expected_moon_ready - moon_jobs)}"
    assert mars_jobs == expected_mars_ready, f"mars+s-type missing: {sorted(expected_mars_ready - mars_jobs)}"
    assert moon_jobs | mars_jobs == optimizable & all_keys


def test_multiple_seeds_per_cell(moon_manifest: dict, mars_manifest: dict) -> None:
    for label, manifest in (("moon", moon_manifest), ("mars", mars_manifest)):
        by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for job in manifest["jobs"]:
            feedstock = job["feedstock"]
            target = job["profile"].split("__", 1)[-1].replace(".real.yaml", "")
            by_cell[(feedstock, target)].append(job)

        assert by_cell, f"{label} manifest has no cells"
        sample_cell, sample_jobs = next(iter(by_cell.items()))
        seeds = {job["seed"] for job in sample_jobs}
        outs = {job["out"] for job in sample_jobs}
        ids = {job["id"] for job in sample_jobs}
        assert len(sample_jobs) >= MIN_SEEDS_PER_CELL, (
            f"{label} cell {sample_cell} has {len(sample_jobs)} seeds, expected >={MIN_SEEDS_PER_CELL}"
        )
        assert len(seeds) == len(sample_jobs)
        assert len(outs) == len(sample_jobs)
        assert len(ids) == len(sample_jobs)


def test_stype_manifest_s_type_only_filter(
    mars_manifest: dict,
    stype_manifest: dict,
) -> None:
    source_stype_jobs = [
        job for job in mars_manifest["jobs"] if job["feedstock"] == "s_type_asteroid_silicate"
    ]
    assert source_stype_jobs, "superset manifest has no s_type_asteroid_silicate jobs"

    stype_jobs = stype_manifest["jobs"]
    assert stype_jobs, "stype manifest has no jobs"
    assert len(stype_jobs) == len(source_stype_jobs)

    feedstocks = {job["feedstock"] for job in stype_jobs}
    assert feedstocks == {"s_type_asteroid_silicate"}

    source_ids = {job["id"] for job in source_stype_jobs}
    assert {job["id"] for job in stype_jobs} == source_ids

    for key in ("description", "base_cache", "work_dir", "fidelity", "parallel"):
        assert key in stype_manifest
        assert stype_manifest[key] == mars_manifest[key]


def test_stype_manifest_schema_and_profiles(stype_manifest: dict) -> None:
    jobs = stype_manifest["jobs"]
    assert jobs
    for job in jobs:
        missing = REQUIRED_JOB_FIELDS - set(job.keys())
        assert not missing, f"stype job {job.get('id')} missing fields: {sorted(missing)}"
        profile_path = GRIND_DIR / job["profile"]
        assert profile_path.is_file(), f"missing profile for {job['id']}: {profile_path}"


@pytest.mark.skipif(
    not LAUNCH_SCRIPT.is_file(),
    reason="requires private C6 grind launcher",
)
def test_launch_studio2_defaults_to_stype_manifest() -> None:
    text = LAUNCH_SCRIPT.read_text(encoding="utf-8")
    assert "manifest-c6-stype-studio2.json" in text
    assert 'MANIFEST_DEFAULT="$REPO/docs-private/grind/manifest-c6-stype-studio2.json"' in text


def test_pc_glass_retain_excluded(moon_manifest: dict, mars_manifest: dict) -> None:
    for manifest in (moon_manifest, mars_manifest):
        for job in manifest["jobs"]:
            assert "pc-glass-retain-na-k-c3" not in job["profile"]
            assert "pc-glass-retain-na-k-c3" not in job["id"]


def test_c6_manifest_feedstocks_have_stage0_route_coverage() -> None:
    feedstocks = load_config_bundle().feedstocks
    manifest_paths = sorted(GRIND_DIR.glob("manifest-c6-*.json"))
    required_manifest_names = {
        "manifest-c6-kreep-studio3.json",
        "manifest-c6-mare-studio1.json",
        "manifest-c6-mars-stype-studio2.json",
        "manifest-c6-moon-studio1.json",
        "manifest-c6-stype-studio2.json",
    }
    if not required_manifest_names <= {path.name for path in manifest_paths}:
        pytest.skip("requires complete private C6 grind manifest set")

    violations = {
        path.name: grind_feedstock_stage0_route_coverage_violations(
            sorted(_manifest_feedstocks(_load_manifest(path))),
            feedstocks,
            backend_name="alphamelts",
        )
        for path in manifest_paths
    }
    assert violations == {path.name: [] for path in manifest_paths}


@pytest.mark.parametrize(
    ("manifest_path", "feedstock"),
    [
        (MOON_MANIFEST, "lunar_highland"),
        (MARS_MANIFEST, "mars_global_mgs1"),
    ],
    ids=["moon", "mars-stype"],
)
def test_epoch_grind_dry_run_materializes_safe_manifest_feedstocks(
    manifest_path: Path,
    feedstock: str,
) -> None:
    if not manifest_path.is_file():
        pytest.skip(f"requires private C6 grind manifest: {manifest_path}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base_cache = tmp_path / "base.sqlite"
        work_dir = tmp_path / "work"
        journal = tmp_path / "journal.json"
        work_dir.mkdir()
        proc = subprocess.run(
            [
                sys.executable,
                str(EPOCH_GRIND),
                "--manifest",
                str(manifest_path),
                "--base-cache",
                str(base_cache),
                "--work-dir",
                str(work_dir),
                "--journal",
                str(journal),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        payload = json.loads(proc.stdout)
        jobs = payload.get("jobs", [])
        assert jobs, "dry-run produced no jobs"
        assert any(
            str(job.get("id", "")).startswith(f"{feedstock}__")
            or f"--feedstock {feedstock}" in " ".join(job.get("command", []))
            for job in jobs
        )


@pytest.mark.skipif(
    not STYPE_MANIFEST.is_file(),
    reason="requires private C6 stype manifest",
)
def test_epoch_grind_dry_run_materializes_commands_for_stype_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base_cache = tmp_path / "base.sqlite"
        work_dir = tmp_path / "work"
        journal = tmp_path / "journal.json"
        work_dir.mkdir()
        proc = subprocess.run(
            [
                sys.executable,
                str(EPOCH_GRIND),
                "--manifest",
                str(STYPE_MANIFEST),
                "--base-cache",
                str(base_cache),
                "--work-dir",
                str(work_dir),
                "--journal",
                str(journal),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        payload = json.loads(proc.stdout)
        jobs = payload.get("jobs", [])
        assert jobs, "dry-run produced no jobs"
        for job in jobs:
            raw_command = job.get("command", "")
            command = (
                " ".join(raw_command) if isinstance(raw_command, list) else str(raw_command)
            )
            assert "--seed" in command
            assert "simulator.optimize" in command


def test_build_c6_manifests_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out_a = Path(tmp) / "a"
        out_b = Path(tmp) / "b"
        out_a.mkdir()
        out_b.mkdir()
        cmd = [sys.executable, str(BUILD_SCRIPT), "--output-dir"]
        for out_dir in (out_a, out_b):
            proc = subprocess.run(
                [*cmd, str(out_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, proc.stderr or proc.stdout
        for name in ("manifest-c6-moon-studio1.json", "manifest-c6-mars-stype-studio2.json"):
            left = (out_a / name).read_bytes()
            right = (out_b / name).read_bytes()
            assert left == right, f"{name} not byte-identical across generator runs"
