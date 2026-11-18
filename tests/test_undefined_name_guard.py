from __future__ import annotations

from pathlib import Path

from tests.undefined_name_check import undefined_names


def test_simulator_and_web_have_no_undefined_names() -> None:
    repo = Path(__file__).resolve().parents[1]
    violations = []
    for root_name in ("simulator", "web"):
        for path in sorted((repo / root_name).rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            violations.extend(
                f"{relative}:{line}: undefined name {name}"
                for name, line in undefined_names(path)
            )

    assert not violations, "\n".join(violations)
