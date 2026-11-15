from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path


def test_element_oxygen_mutation_markers_and_checks_are_live(capsys) -> None:
    script_path = Path(__file__).with_name("mutate_element_oxygen_excess.py")
    script = runpy.run_path(str(script_path))
    markers = {
        name: value
        for name, value in script.items()
        if name.endswith("_MARKER") and isinstance(value, str)
    }
    source = script["SOURCE"].read_text(encoding="utf-8")

    assert markers, "mutation script defines no production markers"
    for name, marker in markers.items():
        assert source.count(marker) == 1, (
            f"{name} must occur exactly once in {script['SOURCE']}"
        )

    with capsys.disabled():
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=script["SEAT"],
            capture_output=True,
            text=True,
            check=False,
        )
        print(completed.stdout, end="")
        print(completed.stderr, file=sys.stderr, end="")

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    for label in ("z_i/2", "refusal override"):
        assert f"{label}: mutation failed the tests (pytest exit " in output
        assert "and the source was restored" in output
    assert script["SOURCE"].read_text(encoding="utf-8") == source
