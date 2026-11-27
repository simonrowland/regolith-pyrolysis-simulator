import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from _ia_rhs_profile_support import (
    clear_ia_profile_caches,
    profile_ia_residue_source,
)


_BASE_SHA = "0207386f7c1a6a98e807bf9e2bdcfac8ab14c8ea"
_STUDIES = (
    ("Mendybaev 2013", "mendybaev-2013-fun-cai-evaporation", 96),
    ("Markova 1984", "kems-026-markova-1984", 110),
)
_GATE_ROWS = (
    (
        "Markova partial residue",
        "kems-026-markova-1984::"
        "markova_1984_table2_residual_melt_VI_lherzolite::"
        "T=2156.15:h=c98ab06cc333",
    ),
    (
        "Markova analytical VI 2207",
        "kems-026-markova-1984::"
        "markova_1984_table2_residual_melt_VI_lherzolite::"
        "T=2480.15:h=9e6722a89608",
    ),
)

_BASE_PROBE = '''
import json
import os

import pytest

from _ia_rhs_profile_support import (
    clear_ia_profile_caches,
    profile_ia_residue_source,
)


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(129600)
@pytest.mark.engine_cache_off
def test_r6_base_probe(monkeypatch, capsys):
    clear_ia_profile_caches(monkeypatch)
    mode = os.environ["IA_R6_MODE"]
    kwargs = {}
    if mode == "gate_node":
        kwargs["observation_ids"] = (os.environ["IA_R6_OBSERVATION_ID"],)
        kwargs["direct_compile_residual"] = True
    report = profile_ia_residue_source(
        os.environ["IA_R6_LABEL"],
        os.environ["IA_R6_SOURCE_ID"],
        monkeypatch,
        memoize=True,
        **kwargs,
    )
    payload = {
        key: report[key]
        for key in (
            "study", "row_count", "numeric_rows", "typed_refusal_rows",
            "residual_digest", "budget_refusal_rows",
            "scoring_process_cpu_seconds", "scoring_wall_seconds",
            "scoring_wall_to_process_cpu_ratio",
            "scoring_loadavg_1_5_15_start", "scoring_loadavg_1_5_15_end",
            "integration_totals", "selected_row_output",
            "selected_row_output_digest",
        )
    }
    payload["cache_warmth"] = "cold: engine cache off; parsed YAML, catalog, CEA, and process caches cleared"
    with capsys.disabled():
        print("IA_R6_BASE_PROBE " + json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str,
        ), flush=True)
    assert report["budget_refusal_rows"] == 0
'''


@pytest.fixture(scope="module")
def base_probe_checkout():
    repo_root = Path(__file__).resolve().parents[2]
    scratch_root = Path(
        tempfile.mkdtemp(
            prefix="ia-r6-base-probe-",
            dir=os.environ.get("TMPDIR") or tempfile.gettempdir(),
        )
    )
    clone = scratch_root / "repo"
    try:
        subprocess.run(
            ["git", "clone", "-q", str(repo_root), str(clone)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "checkout", "-q", "--detach", _BASE_SHA],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        profile_dir = clone / "scripts" / "ia_hang_profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            repo_root / "scripts" / "ia_hang_profiles" / "_ia_rhs_profile_support.py",
            profile_dir / "_ia_rhs_profile_support.py",
        )
        (profile_dir / "test_ia_r6_base_probe.py").write_text(
            _BASE_PROBE,
            encoding="utf-8",
        )
        yield clone
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def _summary(report):
    return {
        key: report[key]
        for key in (
            "study", "row_count", "numeric_rows", "typed_refusal_rows",
            "residual_digest", "budget_refusal_rows",
            "scoring_process_cpu_seconds", "scoring_wall_seconds",
            "scoring_wall_to_process_cpu_ratio",
            "scoring_loadavg_1_5_15_start", "scoring_loadavg_1_5_15_end",
            "integration_totals", "selected_row_output",
            "selected_row_output_digest",
        )
    } | {
        "cache_warmth": (
            "cold: engine cache off; parsed YAML, catalog, CEA, "
            "and process caches cleared"
        ),
    }


def _run_base_probe(
    base_probe_checkout, *, mode, label, source_id, observation_id=None,
):
    environment = {
        **os.environ,
        "IA_R6_MODE": mode,
        "IA_R6_LABEL": label,
        "IA_R6_SOURCE_ID": source_id,
        "REGOLITH_PARSED_YAML_CACHE": "off",
        "REGOLITH_ENGINE_CACHE": "",
        "REGOLITH_ENGINE_CACHE_MODE": "",
    }
    if observation_id is not None:
        environment["IA_R6_OBSERVATION_ID"] = observation_id
    baseline = subprocess.run(
        [
            "python3", "-m", "pytest", "-q", "-n", "0",
            "scripts/ia_hang_profiles/test_ia_r6_base_probe.py",
        ],
        cwd=base_probe_checkout,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=129600,
    )
    baseline_line = next((
        line[len("IA_R6_BASE_PROBE "):]
        for line in baseline.stdout.splitlines()
        if line.startswith("IA_R6_BASE_PROBE ")
    ), None)
    assert baseline_line is not None, (
        f"base probe emitted no report (pytest {baseline.returncode}):\n"
        f"{baseline.stdout[-6000:]}\n{baseline.stderr[-6000:]}"
    )
    report = json.loads(baseline_line)
    report["pytest_returncode"] = baseline.returncode
    return report


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(129600)
@pytest.mark.engine_cache_off
@pytest.mark.parametrize("label,source_id,expected_count", _STUDIES)
def test_r6_study_digest_matches_pure_b274_fold(
    label, source_id, expected_count, base_probe_checkout, monkeypatch, capsys,
):
    clear_ia_profile_caches(monkeypatch)
    with capsys.disabled():
        candidate = profile_ia_residue_source(
            label, source_id, monkeypatch, memoize=True,
        )
    base = _run_base_probe(
        base_probe_checkout,
        mode="study_digest",
        label=label,
        source_id=source_id,
    )
    candidate_summary = _summary(candidate)
    print(
        "IA_R6_DIGEST_PAIR "
        + json.dumps(
            {
                "base_sha": _BASE_SHA,
                "base": base,
                "candidate": candidate_summary,
                "same_digest": base["residual_digest"]
                == candidate["residual_digest"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    assert candidate["row_count"] == expected_count
    assert candidate["numeric_rows"] + candidate["typed_refusal_rows"] == expected_count
    assert candidate["budget_refusal_rows"] == 0
    assert base["row_count"] == expected_count
    assert base["numeric_rows"] + base["typed_refusal_rows"] == expected_count
    assert base["budget_refusal_rows"] == 0
    assert candidate["residual_digest"] == base["residual_digest"]


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(129600)
@pytest.mark.engine_cache_off
@pytest.mark.parametrize("label,observation_id", _GATE_ROWS)
def test_r6_markova_gate_nodes_match_cold_base(
    label, observation_id, base_probe_checkout, monkeypatch, capsys,
):
    clear_ia_profile_caches(monkeypatch)
    with capsys.disabled():
        candidate = profile_ia_residue_source(
            "Markova 1984",
            "kems-026-markova-1984",
            monkeypatch,
            observation_ids=(observation_id,),
            direct_compile_residual=True,
        )
    base = _run_base_probe(
        base_probe_checkout,
        mode="gate_node",
        label="Markova 1984",
        source_id="kems-026-markova-1984",
        observation_id=observation_id,
    )
    print(
        "IA_R6_GATE_NODE_PAIR "
        + json.dumps(
            {
                "node": label,
                "row_id": observation_id,
                "base_sha": _BASE_SHA,
                "base": base,
                "candidate": _summary(candidate),
                "same_residual_digest": base["residual_digest"]
                == candidate["residual_digest"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    assert base["row_count"] == candidate["row_count"] == 1
    assert base["budget_refusal_rows"] == candidate["budget_refusal_rows"] == 0
    assert candidate["selected_row_output"] is not None
    assert base["residual_digest"] == candidate["residual_digest"]
