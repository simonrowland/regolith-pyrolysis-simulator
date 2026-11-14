"""Keep the three designated simulator monoliths from growing unchecked."""

from pathlib import Path
import warnings


REPO_ROOT = Path(__file__).resolve().parents[1]

# Measured on green sha be76d28ad8b73d0563ee34fddf452ec77b3d6de5.
LINE_CEILINGS = {
    Path("simulator/battery/score.py"): 25_855,
    Path("simulator/battery/migrate.py"): 34_113,
    Path("simulator/core.py"): 17_282,
}


def test_monolith_line_counts_stay_within_ceilings() -> None:
    for path, ceiling in LINE_CEILINGS.items():
        line_count = len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines())
        assert line_count <= ceiling, (
            f"{path} has {line_count} lines, exceeding its {ceiling}-line ceiling. "
            "Move code into an owner module, or raise the ceiling in the same "
            "commit with a cited owner ruling id."
        )
        if line_count < ceiling - 200:
            warnings.warn(
                f"{path} is {ceiling - line_count} lines below its ceiling; "
                "lower the ceiling.",
                stacklevel=1,
            )
