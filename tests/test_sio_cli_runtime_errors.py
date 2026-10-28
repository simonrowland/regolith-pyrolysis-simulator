import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("main_name", "builder_name", "argv"),
    (
        (
            "main_sio_yield",
            "build_sio_yield_report",
            ("--feedstock", "lunar_mare_low_ti", "--output", "{output}"),
        ),
        (
            "main_sio_tsweep",
            "run_sio_tsweep",
            ("--feedstock", "lunar_mare_low_ti", "--output-dir", "{output}"),
        ),
        (
            "main_sio_wall_sweep",
            "run_sio_wall_sweep",
            ("--output-dir", "{output}"),
        ),
    ),
)
def test_sio_cli_runner_errors_are_runtime_failures(
    tmp_path: Path, main_name: str, builder_name: str, argv: tuple[str, ...]
) -> None:
    args = tuple(part.format(output=tmp_path / "out") for part in argv)
    script = (
        "from unittest.mock import patch\n"
        "from simulator import runner\n"
        "def refuse(*args, **kwargs):\n"
        "    raise runner.RunnerError('typed_runtime_refusal')\n"
        f"with patch.object(runner, {builder_name!r}, refuse):\n"
        f"    raise SystemExit(runner.{main_name}({args!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "RunnerError: typed_runtime_refusal" in completed.stderr
    assert "usage:" not in completed.stderr
