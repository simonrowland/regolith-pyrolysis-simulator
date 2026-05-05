#!/usr/bin/env python3
"""One-command dependency installer for the simulator."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 10)


def _is_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(command: list[str | os.PathLike[str]]) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}", flush=True)
    subprocess.check_call([str(part) for part in command], cwd=ROOT)


def _ensure_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = ".".join(str(part) for part in sys.version_info[:3])
        raise SystemExit(
            f"Python {required}+ is required; current interpreter is {current}"
        )


def _ensure_requirements_file() -> None:
    if not REQUIREMENTS.exists():
        raise SystemExit(f"Missing dependency file: {REQUIREMENTS}")


def _ensure_target_python() -> Path:
    if _is_venv():
        return Path(sys.executable)

    python = _venv_python()
    if not python.exists():
        print(f"Creating virtual environment: {VENV_DIR}", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    return python


def _ensure_pip(python: Path) -> None:
    try:
        _run([python, "-m", "pip", "--version"])
    except subprocess.CalledProcessError:
        _run([python, "-m", "ensurepip", "--upgrade"])


def _install_with_uv(python: Path) -> bool:
    uv = shutil.which("uv")
    if uv is None:
        return False
    try:
        _run([uv, "pip", "install", "--python", python, "-r", REQUIREMENTS])
    except subprocess.CalledProcessError:
        print("uv install failed; falling back to pip.", flush=True)
        return False
    return True


def _install_with_pip(python: Path) -> None:
    _ensure_pip(python)
    _run([python, "-m", "pip", "install", "--upgrade", "pip"])
    _run([python, "-m", "pip", "install", "-r", REQUIREMENTS])


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python3 install-dependencies.py")

    _ensure_python_version()
    _ensure_requirements_file()
    python = _ensure_target_python()

    if not _install_with_uv(python):
        _install_with_pip(python)

    print("\nInstall complete.")
    print(f"Run: {python} regolith-pyrolysis-run.py")
    print("Open: http://localhost:3000/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
