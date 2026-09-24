"""R16 P1: tip derived store must carry post-regen printed log fO2 landings.

The r34-hardening series regenerated the store at ``2e9e17c3d``, then landed
printed per-run log fO2 (``d4f91337f``) and migrate hardenings (``22cf80906``,
``07ad01dae``) with no follow-up regen. Store consumers
(``load_migrated_store``, readiness, score) read the committed extracts-v2,
so the tip advertised landings that readiness never saw.

These tests pin the committed store: the four fO2 source stems carry
``fO2_log`` in extracts-v2, and no migrate-input commit is newer than the
last store-output touch (the STALE half of ``check_store_freshness``).
UNTRACED extracts (no v2 sibling and no queue entry) are out of scope —
they predate this series' fo2 hole.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Stems from d4f91337f — printed per-run log fO2 landings that were absent
# from extracts-v2 on tip 07ad01dae (R16 P1 census: 0 fO2_log keys each).
PRINTED_FO2_STEMS = (
    "holzheid-1997-feo-nio-coo-activity-metal-saturated",
    "sossi-2020-cu-zn-isotope-evap-formalism",
    "kems-140-heck-2025",
    "thomas-2022-chlorine-bonding-silicate-melts",
)

# Lower bounds from the tip regen census (author extract keys that landed as
# point_conditions.fO2_log). A tip that merely retouches the sibling without
# landing the logs fails these. Pre-regen tip had 0 for every stem.
MIN_FO2_LOG_HITS = {
    "holzheid-1997-feo-nio-coo-activity-metal-saturated": 30,
    "sossi-2020-cu-zn-isotope-evap-formalism": 60,
    "kems-140-heck-2025": 70,
    "thomas-2022-chlorine-bonding-silicate-melts": 40,
}

# Parent tip that shipped the stale store (R16 evidence SHA).
STALE_TIP = "07ad01dae"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _require_git_history(*revisions: str) -> None:
    """Skip history-only mutation proofs when CI copied a shallow worktree."""
    for revision in revisions:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(
                "git_history_unavailable: required mutation revision "
                f"{revision!r} is absent"
            )


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_fo2_log_keys(text: str) -> int:
    """Count ``fO2_log:`` mapping keys under point_conditions."""
    return len(re.findall(r"(?m)^\s+fO2_log:\s*$", text))


def test_committed_printed_fo2_stems_land_fO2_log_in_extracts_v2() -> None:
    """The four d4f stems must carry printed fO2_log in the committed store."""
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    for stem in PRINTED_FO2_STEMS:
        path = extracts_v2 / f"{stem}.yaml"
        assert path.is_file(), f"missing extracts-v2 sibling for {stem}"
        hits = _count_fo2_log_keys(path.read_text(encoding="utf-8"))
        minimum = MIN_FO2_LOG_HITS[stem]
        assert hits >= minimum, (
            f"{stem}: extracts-v2 has {hits} fO2_log keys, need >= {minimum} "
            "(store looks pre-d4f / unregenerated)"
        )


def test_committed_store_has_no_stale_migrate_input_commits() -> None:
    """No migrate-input commit may land after the last store-output touch.

    Mirrors the STALE half of ``scripts/check_store_freshness.py`` so a
    mid-series regen followed by extract/migrate edits without a tip regen
    fails in CI. Does not assert UNTRACED (pre-existing, out of R16 P1).
    """
    freshness = _load_freshness()
    head = _git("rev-parse", "HEAD").strip()
    last_store = _git(
        "log", "-1", "--format=%H", head, "--", *freshness.STORE_OUTPUTS
    ).strip()
    assert last_store, "no store-output commit found"
    log = _git(
        "log",
        "--format=%H%x00%s",
        "--name-only",
        f"{last_store}..{head}",
        "--",
        "data/literature",
        "simulator/battery",
        *freshness.CODE_INPUTS,
    )
    stale: dict[str, list[str]] = {}
    commit: str | None = None
    subject = ""
    for line in log.splitlines():
        if "\x00" in line:
            commit, subject = line.split("\x00", 1)
        elif line.strip() and commit is not None and freshness._is_input(line.strip()):
            stale.setdefault(f"{commit[:9]} {subject}", []).append(line.strip())
    assert not stale, (
        "store is STALE w.r.t. migrate inputs; regenerate with "
        "scripts/battery_migrate.py + build_index --write-store-summary:\n"
        + "\n".join(f"  {k}: {v[:3]}" for k, v in stale.items())
    )


def test_mutation_pre_regen_tip_extracts_v2_have_zero_fo2_log() -> None:
    """Mutation proof: the stale tip's extracts-v2 fail the census bounds.

    Re-reading ``07ad01dae`` siblings (the series tip before this regen)
    must show zero ``fO2_log`` keys — the defect R16 P1 named. If this
    ever goes green against that tip, the census no longer guards the hole.
    """
    _require_git_history(STALE_TIP)
    for stem in PRINTED_FO2_STEMS:
        blob = _git(
            "show",
            f"{STALE_TIP}:data/literature/extracts-v2/{stem}.yaml",
        )
        hits = _count_fo2_log_keys(blob)
        assert hits == 0, (
            f"{stem} at {STALE_TIP} unexpectedly has {hits} fO2_log keys"
        )
        assert hits < MIN_FO2_LOG_HITS[stem]


def test_mutation_stripping_fo2_log_fails_census_bound(tmp_path: Path) -> None:
    """Mutation proof: delete landed fO2_log keys → census bound fails."""
    import shutil

    stem = "holzheid-1997-feo-nio-coo-activity-metal-saturated"
    src = REPO_ROOT / "data" / "literature" / "extracts-v2" / f"{stem}.yaml"
    dest = tmp_path / f"{stem}.yaml"
    shutil.copy2(src, dest)
    before = _count_fo2_log_keys(dest.read_text(encoding="utf-8"))
    assert before >= MIN_FO2_LOG_HITS[stem]

    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    stripped = 0
    for obs in doc.get("observations") or []:
        pc = obs.get("point_conditions")
        if isinstance(pc, dict) and "fO2_log" in pc:
            del pc["fO2_log"]
            stripped += 1
    assert stripped > 0
    dest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    after = _count_fo2_log_keys(dest.read_text(encoding="utf-8"))
    assert after == 0
    with pytest.raises(AssertionError):
        assert after >= MIN_FO2_LOG_HITS[stem]


def test_mutation_stale_tip_is_flagged_by_freshness_stale_half() -> None:
    """Mutation proof: on ``07ad01dae`` the STALE half still fires.

    Revert-shaped check — if HEAD were still the pre-regen tip, the
    migrate-input commits after ``2e9e17c3d`` would be reported. Guarding
    this keeps the freshness assertion from going vacuous.
    """
    _require_git_history(STALE_TIP)
    freshness = _load_freshness()
    head = _git("rev-parse", STALE_TIP).strip()
    last_store = _git(
        "log", "-1", "--format=%H", head, "--", *freshness.STORE_OUTPUTS
    ).strip()
    assert last_store
    log = _git(
        "log",
        "--format=%H%x00%s",
        "--name-only",
        f"{last_store}..{head}",
        "--",
        "data/literature",
        "simulator/battery",
        *freshness.CODE_INPUTS,
    )
    stale: dict[str, list[str]] = {}
    commit: str | None = None
    subject = ""
    for line in log.splitlines():
        if "\x00" in line:
            commit, subject = line.split("\x00", 1)
        elif line.strip() and commit is not None and freshness._is_input(line.strip()):
            stale.setdefault(f"{commit[:9]} {subject}", []).append(line.strip())
    assert stale, "expected STALE_TIP to have post-regen migrate-input commits"
    assert any("d4f91337f" in k for k in stale), stale
