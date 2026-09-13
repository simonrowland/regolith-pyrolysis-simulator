"""Property tests: no assertion pins a corpus count (v2.1 M15 / growth invariants)."""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import EvidenceClass
from simulator.battery.validate import validate_corpus
from tests.battery import factories as F

_BATTERY_TESTS = Path(__file__).resolve().parent

# Census numbers from SCHEMA-PROPOSAL-v2.1 (audit facts, never test pins).
_FORBIDDEN_COUNT_LITERALS = frozenset({147, 3885, 1711, 2174, 3511, 1617, 1023, 594, 1625, 670, 374, 422, 60})


def test_m15_property_added_valid_row_does_not_break_validation() -> None:
    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    rows = []
    for n in (1, 2, 5, 11):
        rows.append(F.observation(f"grow-{n}", exp.experiment_id, ident, Decimal("0")))
        report = validate_corpus([w], [exp], rows)
        assert report.ok


def test_m15_property_no_test_asserts_a_corpus_census() -> None:
    """Walk tests/battery AST: no Compare against the proposal's census integers."""

    offenders: list[str] = []
    for path in sorted(_BATTERY_TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            literals = [
                n.value
                for n in list(node.comparators) + [node.left]
                if isinstance(n, ast.Constant) and isinstance(n.value, int)
            ]
            for value in literals:
                if value in _FORBIDDEN_COUNT_LITERALS:
                    offenders.append(f"{path.name}:{node.lineno}: pinned {value}")
    assert offenders == []


def test_m15_property_source_does_not_contain_certified_zero_invariant() -> None:
    pattern = re.compile(r"certified['\"]?\s*[:=]\s*0\b")
    hits: list[str] = []
    for path in sorted(_BATTERY_TESTS.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(path.name)
    assert hits == []


def test_referential_integrity_grows_without_a_size_gate() -> None:
    works = [F.work(f"w-{i}") for i in range(4)]
    experiments = [
        F.tabulation_experiment(experiment_id=f"e-{i}", work_id=f"w-{i}") for i in range(4)
    ]
    ident = F.o2_identity()
    observations = [
        F.observation(
            f"o-{i}",
            f"e-{i}",
            ident,
            Decimal("0"),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
        )
        for i in range(4)
    ]
    assert validate_corpus(works, experiments, observations).ok
