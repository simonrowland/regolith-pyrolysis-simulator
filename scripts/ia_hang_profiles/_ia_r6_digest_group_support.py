from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


BASE_SHA = "8d953a6cd26d42310b9fba5851c4f87dc0991d13"
CANDIDATE_PRODUCT_SHA = "d6acf7b21d9e4d719b91eb891bb1ccfcc136f1a9"
GROUP_SIZE = 16
STUDIES = {
    "mendybaev": (
        "Mendybaev 2013",
        "mendybaev-2013-fun-cai-evaporation",
        96,
    ),
    "markova": (
        "Markova 1984",
        "kems-026-markova-1984",
        110,
    ),
}

_DRIVER = '''
import json
import os
from pathlib import Path
import sys

root, label, source_id, mode, selector, group_size = sys.argv[1:]
root = Path(root)
sys.path[:0] = [str(root), str(root / "scripts" / "ia_hang_profiles")]
os.environ["REGOLITH_PARSED_YAML_CACHE"] = "off"
os.environ["REGOLITH_ENGINE_CACHE"] = ""
os.environ["REGOLITH_ENGINE_CACHE_MODE"] = ""

from _ia_rhs_profile_support import (
    clear_ia_profile_caches,
    profile_ia_residue_source,
)


class Patch:
    @staticmethod
    def setattr(target, name, value):
        setattr(target, name, value)

    @staticmethod
    def setenv(name, value):
        os.environ[name] = value


patch = Patch()
clear_ia_profile_caches(patch)
kwargs = {"memoize": True}
if mode == "digest":
    kwargs["observation_group_index"] = int(selector)
    kwargs["observation_group_size"] = int(group_size)
else:
    kwargs["observation_ids"] = (selector,)
    kwargs["direct_compile_residual"] = True
report = profile_ia_residue_source(label, source_id, patch, **kwargs)
print("IA_R6_PROFILE " + json.dumps({
    "study": report["study"],
    "row_count": report["row_count"],
    "numeric_rows": report["numeric_rows"],
    "typed_refusal_rows": report["typed_refusal_rows"],
    "budget_refusal_rows": report["budget_refusal_rows"],
    "row_residuals": report["row_residuals"],
    "row_residual_digests": report["row_residual_digests"],
    "scoring_process_cpu_seconds": report["scoring_process_cpu_seconds"],
    "scoring_wall_seconds": report["scoring_wall_seconds"],
    "scoring_wall_to_process_cpu_ratio": report["scoring_wall_to_process_cpu_ratio"],
    "scoring_loadavg_1_5_15_start": report["scoring_loadavg_1_5_15_start"],
    "scoring_loadavg_1_5_15_end": report["scoring_loadavg_1_5_15_end"],
    "integration_totals": report["integration_totals"],
    "selected_row_output": report["selected_row_output"],
    "selected_row_output_digest": report["selected_row_output_digest"],
}, sort_keys=True, separators=(",", ":"), default=str), flush=True)
'''


def _run_profile(
    root: Path,
    driver: Path,
    *,
    study_key: str,
    mode: str,
    selector: str | int,
    group_size: int,
    timeout_seconds: float,
):
    label, source_id, expected_rows = STUDIES[study_key]
    environment = {
        **os.environ,
        "REGOLITH_PARSED_YAML_CACHE": "off",
        "REGOLITH_ENGINE_CACHE": "",
        "REGOLITH_ENGINE_CACHE_MODE": "",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(driver),
            str(root),
            label,
            source_id,
            mode,
            str(selector),
            str(group_size),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    report_line = next((
        line[len("IA_R6_PROFILE "):]
        for line in completed.stdout.splitlines()
        if line.startswith("IA_R6_PROFILE ")
    ), None)
    assert report_line is not None, (
        f"{study_key} {mode} {selector} profile failed "
        f"({completed.returncode}):\n{completed.stdout[-5000:]}\n"
        f"{completed.stderr[-5000:]}"
    )
    report = json.loads(report_line)
    if mode == "digest":
        expected_count = min(
            group_size,
            expected_rows - int(selector) * group_size,
        )
        assert report["row_count"] == expected_count
    else:
        assert report["row_count"] == 1
    return report


def run_digest_group(study_key: str, group_index: int) -> None:
    if study_key not in STUDIES:
        raise ValueError(f"unknown residue study {study_key!r}")
    label, source_id, row_count = STUDIES[study_key]
    group_count = (row_count + GROUP_SIZE - 1) // GROUP_SIZE
    if group_index < 0 or group_index >= group_count:
        raise ValueError(f"group {group_index} is outside {study_key}'s {group_count} groups")

    repo_root = Path(__file__).resolve().parents[2]
    scratch_root = Path(tempfile.mkdtemp(
        prefix=f"ia-r6-{study_key}-{group_index:02d}-",
        dir=os.environ.get("TMPDIR") or tempfile.gettempdir(),
    ))
    clone = scratch_root / "base"
    driver = scratch_root / "profile_group.py"
    try:
        driver.write_text(_DRIVER, encoding="utf-8")
        subprocess.run(
            ["git", "clone", "-q", str(repo_root), str(clone)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "checkout", "-q", "--detach", BASE_SHA],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        base_profile_dir = clone / "scripts" / "ia_hang_profiles"
        base_profile_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            repo_root / "scripts" / "ia_hang_profiles" / "_ia_rhs_profile_support.py",
            base_profile_dir / "_ia_rhs_profile_support.py",
        )

        job_deadline = time.monotonic() + 6600.0
        candidate = _run_profile(
            repo_root,
            driver,
            study_key=study_key,
            mode="digest",
            selector=group_index,
            group_size=GROUP_SIZE,
            timeout_seconds=max(1.0, job_deadline - time.monotonic()),
        )
        base = _run_profile(
            clone,
            driver,
            study_key=study_key,
            mode="digest",
            selector=group_index,
            group_size=GROUP_SIZE,
            timeout_seconds=max(1.0, job_deadline - time.monotonic()),
        )
        candidate_rows = candidate["row_residuals"]
        base_rows = base["row_residuals"]
        same_rows = (
            candidate_rows == base_rows
            and candidate["row_residual_digests"]
            == base["row_residual_digests"]
        )
        print("IA_R6_DIGEST_GROUP_PAIR " + json.dumps({
            "study": label,
            "source_id": source_id,
            "group_index": group_index,
            "group_size": GROUP_SIZE,
            "row_digest_algorithm": "sha256(canonical sorted-key residual row); study digest recomputed from ordered canonical rows",
            "base_sha": BASE_SHA,
            "candidate_product_sha": CANDIDATE_PRODUCT_SHA,
            "candidate_harness_head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "cache_warmth": "cold per process: engine cache disabled and parsed/catalog/CEA/process caches cleared",
            "same_rows": same_rows,
            "base": base,
            "candidate": candidate,
        }, sort_keys=True, separators=(",", ":")), flush=True)
        assert candidate["budget_refusal_rows"] == base["budget_refusal_rows"] == 0
        assert same_rows, f"{study_key} row digest mismatch in group {group_index}"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def run_gate_node(observation_id: str) -> None:
    label, source_id, _row_count = STUDIES["markova"]
    repo_root = Path(__file__).resolve().parents[2]
    scratch_root = Path(tempfile.mkdtemp(
        prefix="ia-r6-gate-node-",
        dir=os.environ.get("TMPDIR") or tempfile.gettempdir(),
    ))
    clone = scratch_root / "base"
    driver = scratch_root / "profile_gate_node.py"
    try:
        driver.write_text(_DRIVER, encoding="utf-8")
        subprocess.run(
            ["git", "clone", "-q", str(repo_root), str(clone)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "checkout", "-q", "--detach", BASE_SHA],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        base_profile_dir = clone / "scripts" / "ia_hang_profiles"
        base_profile_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            repo_root / "scripts" / "ia_hang_profiles" / "_ia_rhs_profile_support.py",
            base_profile_dir / "_ia_rhs_profile_support.py",
        )
        job_deadline = time.monotonic() + 6600.0
        candidate = _run_profile(
            repo_root,
            driver,
            study_key="markova",
            mode="gate_node",
            selector=observation_id,
            group_size=1,
            timeout_seconds=max(1.0, job_deadline - time.monotonic()),
        )
        base = _run_profile(
            clone,
            driver,
            study_key="markova",
            mode="gate_node",
            selector=observation_id,
            group_size=1,
            timeout_seconds=max(1.0, job_deadline - time.monotonic()),
        )
        same_row = (
            candidate["row_residuals"] == base["row_residuals"]
            and candidate["row_residual_digests"]
            == base["row_residual_digests"]
            and candidate["selected_row_output_digest"]
            == base["selected_row_output_digest"]
        )
        print("IA_R6_GATE_NODE_PAIR " + json.dumps({
            "observation_id": observation_id,
            "base_sha": BASE_SHA,
            "candidate_product_sha": CANDIDATE_PRODUCT_SHA,
            "candidate_harness_head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "cache_warmth": "cold per process: engine cache disabled and parsed/catalog/CEA/process caches cleared",
            "same_row_output": same_row,
            "base": base,
            "candidate": candidate,
        }, sort_keys=True, separators=(",", ":"), default=str), flush=True)
        assert base["budget_refusal_rows"] == candidate["budget_refusal_rows"] == 0
        assert same_row, f"gate node row output differs for {observation_id}"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
