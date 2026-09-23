"""R17 P1: tip derived store must not stamp JANAF glass-region series as liquid.

``review/s9-glass`` (``b5b9dacc8``) taught ``generate_table`` to leave mixed
glass+liquid liquid-table series as phase unknown with the printed marker
quoted, but did not regenerate ``observations-v2``. Store consumers
(``load_migrated_store``) still saw ``phase.value=l`` on all 128
``transition_temperature:glass-liquid`` tables (R17 P1).

These tests pin the committed store: those series are unknown (never ``l``),
Mg-013 / W-003 quote their printed markers, and no migrate-input commit is
newer than the last store-output touch (STALE half of
``check_store_freshness``). Generator-only coverage stays in
``test_janaf_generator.py``.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
JANAF_STORE = (
    REPO_ROOT / "data" / "literature" / "observations-v2" / "compilations-janaf"
)

# Tip that shipped the glass generator without a store regen (R17 evidence).
STALE_TIP = "b5b9dacc8"

_MG_CP_ID = "nist-janaf-4th:Mg-013:cp:phase-window:whole"
_W_CP_ID = "nist-janaf-4th:W-003:cp:phase-window:whole"
# F4 retargeted JANAF segment ids to phase-window T-bounds and keyed
# transitions on temperature (not printed glass-liquid subtype). Detect
# glass-region cp series by a printed GLASS marker in the phase reason.
_CP_GLASS_RE = re.compile(
    r"observation_id:\s+nist-janaf-4th:([^:]+):cp:phase-window:[^\n]+\n"
    r"(?:.*\n){0,80}?"
    r"      phase:\n"
    r"        tag:\s+(\w+)(?:\n        value:\s+(\w+))?",
    re.M,
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _phase_token(tag: str, value: str | None) -> str:
    return value if tag == "value" else tag


def _glass_region_cp_tables_and_phases(text: str) -> tuple[set[str], dict[str, str]]:
    tables: set[str] = set()
    phases: dict[str, str] = {}
    for match in _CP_GLASS_RE.finditer(text):
        table, tag, value = match.group(1), match.group(2), match.group(3)
        chunk = text[match.start() : match.start() + 2500]
        if "GLASS" not in chunk.upper():
            continue
        tables.add(table)
        phases[table] = _phase_token(tag, value)
    return tables, phases


def _observation_phase(path: Path, observation_id: str) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for row in doc.get("observations") or []:
        if row.get("observation_id") == observation_id:
            return row["identity"]["species"]["phase"]
    raise AssertionError(f"{observation_id} missing from {path}")


def test_committed_store_glass_region_cp_series_are_unknown_not_liquid() -> None:
    """All 128 glass-region tables must have cp phase-window phase unknown."""
    glass_tables: set[str] = set()
    cp_phases: dict[str, str] = {}
    for path in sorted(JANAF_STORE.glob("janaf-*.yaml")):
        tables, phases = _glass_region_cp_tables_and_phases(
            path.read_text(encoding="utf-8")
        )
        glass_tables |= tables
        for table in tables:
            assert table in phases, f"{table}: missing glass-region cp phase-window in {path.name}"
            cp_phases[table] = phases[table]
    assert len(glass_tables) == 128, (
        f"expected 128 glass-liquid tables, found {len(glass_tables)}"
    )
    stamped_liquid = sorted(
        table for table, phase in cp_phases.items() if phase == "l"
    )
    assert not stamped_liquid, (
        "glass-region series still stamped liquid in committed store: "
        + ", ".join(stamped_liquid[:8])
    )
    assert set(cp_phases.values()) == {"unknown"}


def test_committed_store_mg013_and_w003_quote_printed_glass_markers() -> None:
    """Named attack tables keep ids and quote the printed glass marker."""
    mg = _observation_phase(JANAF_STORE / "janaf-Mg.yaml", _MG_CP_ID)
    assert mg.get("tag") == "unknown"
    assert mg.get("value") is None
    reason = mg.get("reason") or ""
    assert 'printed "GLASS <--> LIQUID" at 900.000 K' in reason
    assert "not stamped liquid" in reason
    assert "supercooled liquid / glass-transition region" in reason

    w = _observation_phase(JANAF_STORE / "janaf-W.yaml", _W_CP_ID)
    assert w.get("tag") == "unknown"
    w_reason = w.get("reason") or ""
    assert 'printed "GLASS <--> LIQ" at ' in w_reason
    assert "not stamped liquid" in w_reason


def test_committed_store_has_no_stale_migrate_input_commits() -> None:
    """No migrate-input commit may land after the last store-output touch."""
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


def test_mutation_pre_regen_tip_stamps_mg013_liquid() -> None:
    """Mutation proof: the pre-regen tip still stamps Mg-013 cp as liquid.

    Re-reading ``b5b9dacc8`` siblings must show ``phase.value=l`` — the
    defect R17 P1 named. If this goes green against that tip, the census
    no longer guards the hole.
    """
    blob = _git(
        "show",
        f"{STALE_TIP}:data/literature/observations-v2/compilations-janaf/janaf-Mg.yaml",
    )
    match = re.search(
        r"observation_id:\s+nist-janaf-4th:Mg-013:cp:segment-0\n"
        r"(?:  .*\n)*?"
        r"      phase:\n"
        r"        tag:\s+(\w+)(?:\n        value:\s+(\w+))?",
        blob,
    )
    assert match is not None, "Mg-013 cp:segment-0 missing from stale tip store"
    tag, value = match.group(1), match.group(2)
    assert _phase_token(tag, value) == "l"
    assert 'printed "GLASS <--> LIQUID"' not in blob[
        match.start() : match.start() + 400
    ]


def test_mutation_restamping_mg013_liquid_fails_unknown_pin(tmp_path: Path) -> None:
    """Mutation proof: rewrite Mg-013 cp phase to ``l`` → unknown pin fails."""
    import shutil

    src = JANAF_STORE / "janaf-Mg.yaml"
    dest = tmp_path / "janaf-Mg.yaml"
    shutil.copy2(src, dest)
    before = _observation_phase(dest, _MG_CP_ID)
    assert before.get("tag") == "unknown"

    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    found = False
    for row in doc.get("observations") or []:
        if row.get("observation_id") == _MG_CP_ID:
            row["identity"]["species"]["phase"] = {"tag": "value", "value": "l"}
            found = True
            break
    assert found
    dest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    after = _observation_phase(dest, _MG_CP_ID)
    assert after.get("tag") == "value" and after.get("value") == "l"
    with pytest.raises(AssertionError):
        assert after.get("tag") == "unknown"


def test_mutation_stale_tip_is_flagged_by_freshness_stale_half() -> None:
    """Mutation proof: on ``b5b9dacc8`` the STALE half still fires."""
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
    assert any("b5b9dacc8" in k for k in stale), stale
