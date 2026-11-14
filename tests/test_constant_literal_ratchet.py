"""Shrink-only ratchet for physical-constant literals in simulator/.

Counts NUMBER tokens, and STRING tokens whose whole value is a numeric
literal, outside simulator/physical_constants.py. Comments and prose
that merely mention the digits are not counted. A named exclusion is a
source-era gas constant or a 100_000 that is not a pressure conversion;
its token must still be present. The counted total must equal the
ceiling. Removing a literal lowers the ceiling in the same commit, so
a later edit cannot climb back up to the old number.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import io
from pathlib import Path
import re
import subprocess
import tokenize


ROOT = Path(__file__).resolve().parents[1]
OWNER = "simulator/physical_constants.py"
GAS_TEXT = re.compile(r"^8\.314(?:\d+)?(?:[eE][+-]?\d+)?$")
CELSIUS_TEXT = re.compile(r"^273\.150*$")
NUMERIC_TEXT = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")

# Non-excluded literals that remain after the ownership moves.
# gas, bar, and atm are zero: every executable copy calls the owner.
# celsius is the simulator remainder outside the modules this dispatch moved.
CEILINGS = {
    "gas": 0,
    "bar": 0,
    "atm": 0,
    "celsius": 82,
}

# path, bare numeric text, family, allowed count, reason
EXCLUSIONS = (
    (
        "simulator/battery/generators/janaf.py",
        "8.31441",
        "gas",
        1,
        "JANAF printed R; CODATA does not reproduce the printed log K",
    ),
    (
        "simulator/reference_data/janaf.py",
        "8.31441",
        "gas",
        1,
        "JANAF printed R; CODATA does not reproduce the printed log K",
    ),
    (
        "simulator/chemistry/fo2_buffers.py",
        "8.31441",
        "gas",
        1,
        "O'Neill 1987 printed R; the CODATA path is a different name",
    ),
    (
        "simulator/battery/generators/usgs_b1452.py",
        "8.3143",
        "gas",
        1,
        "B1452 Table 1 printed gas constant",
    ),
    (
        "simulator/battery/generators/usgs_b1544.py",
        "8.3143",
        "gas",
        1,
        "B1544 inherits B1452's printed gas constant",
    ),
    (
        "simulator/reference_data/hemingway_haas_robinson_1982_usgs_b1544_loader.py",
        "8.3143",
        "gas",
        1,
        "Robie et al. 1979 R printed for the B1544 bulletin",
    ),
    (
        "simulator/battery/generators/usgs_b1259.py",
        "8.31469",
        "gas",
        1,
        "B1259 printed joule gas constant, not CODATA",
    ),
    (
        "simulator/melt_backend/sulfliq_matte.py",
        "8.31468",
        "gas",
        1,
        "SulfLiq Phase::R transcription, not CODATA",
    ),
    (
        "simulator/melt_backend/liquidus.py",
        "100000",
        "bar",
        1,
        "liquidus scan-point budget, not a bar-to-pascal factor",
    ),
    (
        "simulator/optimize/strategy/staged.py",
        "100000",
        "bar",
        2,
        "optimizer index band and index scale, not pressures",
    ),
)


@dataclass(frozen=True)
class LiteralHit:
    path: str
    line: int
    family: str
    bare: str
    token: str


def _bare_numeric(token_text: str, kind: str) -> str | None:
    if kind == "NUMBER":
        return token_text.replace("_", "")
    if kind != "STRING":
        return None
    try:
        value = ast.literal_eval(token_text)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    return value.replace("_", "")


def _families(bare: str) -> set[str]:
    found: set[str] = set()
    if GAS_TEXT.fullmatch(bare):
        found.add("gas")
    if CELSIUS_TEXT.fullmatch(bare):
        found.add("celsius")
    if NUMERIC_TEXT.fullmatch(bare):
        value = float(bare)
        if value == 1e5:
            found.add("bar")
        if value == 101325.0:
            found.add("atm")
    return found


def _tracked_simulator_python() -> list[str]:
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "simulator"],
        cwd=ROOT,
    )
    return [
        path
        for path in listed.decode().split("\0")
        if path.endswith(".py") and path != OWNER
    ]


def scan_literals(root: Path | None = None) -> dict[str, list[LiteralHit]]:
    """Return literal hits grouped by family."""

    root = ROOT if root is None else root
    hits: dict[str, list[LiteralHit]] = {family: [] for family in CEILINGS}
    for rel in _tracked_simulator_python():
        text = (root / rel).read_text(encoding="utf-8")
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        except tokenize.TokenError as exc:
            raise AssertionError(f"cannot tokenize {rel}: {exc}") from exc
        for tok in tokens:
            kind = tokenize.tok_name[tok.type]
            bare = _bare_numeric(tok.string, kind)
            if bare is None:
                continue
            for family in _families(bare):
                hits[family].append(
                    LiteralHit(rel, tok.start[0], family, bare, tok.string)
                )
    return hits


def evaluate(
    hits: dict[str, list[LiteralHit]],
    exclusions: tuple[tuple[str, str, str, int, str], ...] = EXCLUSIONS,
    ceilings: dict[str, int] | None = None,
) -> list[str]:
    """Return ratchet failures. An empty list means the ceilings hold."""

    ceilings = CEILINGS if ceilings is None else ceilings
    errors: list[str] = []
    consumed: Counter[int] = Counter()
    for path, bare, family, allowed, reason in exclusions:
        if family not in _families(bare):
            errors.append(
                f"exclusion {path} {bare!r} is not a {family} literal: {reason}"
            )
            continue
        if not reason.strip():
            errors.append(f"exclusion {path} {bare!r} has no reason")
        matched = [
            hit
            for hit in hits.get(family, [])
            if hit.path == path and hit.bare == bare
        ]
        if len(matched) < allowed:
            errors.append(
                f"stale exclusion {path} {bare!r} {family}: "
                f"found {len(matched)}, allowed {allowed}: {reason}"
            )
        for hit in matched[:allowed]:
            consumed[id(hit)] += 1
    for family, ceiling in sorted(ceilings.items()):
        leftover = [
            hit for hit in hits.get(family, []) if consumed[id(hit)] == 0
        ]
        if len(leftover) != ceiling:
            rendered = ", ".join(
                f"{hit.path}:{hit.line}:{hit.token}" for hit in leftover[:12]
            )
            errors.append(
                f"{family} count {len(leftover)} != ceiling {ceiling}"
                + (f" [{rendered}]" if rendered else "")
            )
    return errors


def test_constant_literals_match_ceiling() -> None:
    """Executable copies of R, bar, atm, and 273.15 stay at the ceiling."""

    errors = evaluate(scan_literals())
    assert not errors, "\n".join(errors)


def test_constant_literal_ratchet_rejects_a_rise() -> None:
    """One new gas literal above a zero ceiling is a failure."""

    hits = {family: [] for family in CEILINGS}
    hits["gas"].append(
        LiteralHit("simulator/example.py", 1, "gas", "8.31446261815324", "8.31446261815324")
    )
    errors = evaluate(hits, (), {family: 0 for family in CEILINGS})
    assert len(errors) == 1
    assert "gas count 1 != ceiling 0" in errors[0]
    assert "simulator/example.py:1" in errors[0]
