#!/usr/bin/env python3
"""One-command dependency installer for the simulator."""

from __future__ import annotations

import os
import hashlib
import platform
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 10)
ALPHAMELTS_VERSION = "2.3.1"
ALPHAMELTS_RELEASE = (
    "https://github.com/magmasource/alphaMELTS/releases/download/v2.3.1"
)
ALPHAMELTS_ASSETS = {
    ("Darwin", "arm64"): (
        "alphamelts-app-2.3.1-macos-arm64.zip",
        "f233ff5180df4e8318af922183beb85c20c33c28d3cb1be1254916f856327136",
        "alphamelts_macos",
    ),
    ("Darwin", "x86_64"): (
        "alphamelts-app-2.3.1-macos-x86_64.zip",
        "68017f2bb67547524bb6bbe940304c5ddf72a1e98cc0092b072b7e267b4a0f0c",
        "alphamelts_macos",
    ),
    ("Linux", "x86_64"): (
        "alphamelts-app-2.3.1-linux.zip",
        "d8c26879a6c84807d976181d5132ab0107d90598f20ad9798eac4822403effbb",
        "alphamelts_linux",
    ),
    ("Windows", "AMD64"): (
        "alphamelts-app-2.3.1-win64.zip",
        "a7df461a261ded5347abbd95dd55cbbc489ae9fa7697903a31db8c13e5a5d6c6",
        "alphamelts_win64.exe",
    ),
}


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


def _alpha_asset() -> tuple[str, str, str]:
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key not in ALPHAMELTS_ASSETS:
        raise SystemExit(
            "No bundled alphaMELTS app asset is configured for "
            f"{system}/{machine}"
        )
    return ALPHAMELTS_ASSETS[key]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    print(f"Downloading {url}", flush=True)
    with urllib.request.urlopen(url) as response:
        target.write_bytes(response.read())


def _write_runner(engine_dir: Path, package_dir: Path, binary_name: str) -> None:
    if os.name == "nt":
        runner = engine_dir / "run_alphamelts.command"
        runner.write_text(
            "@echo off\r\n"
            f"\"%~dp0{package_dir.name}\\run-alphamelts.command\" %*\r\n",
            encoding="utf-8",
        )
        return

    runner = engine_dir / "run_alphamelts.command"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'exec "$SCRIPT_DIR/{package_dir.name}/run-alphamelts.command" "$@"\n',
        encoding="utf-8",
    )
    runner.chmod(0o755)

    link = package_dir / "alphamelts2"
    binary = package_dir / binary_name
    if link.exists() or link.is_symlink():
        link.unlink()
    try:
        link.symlink_to(binary.name)
    except OSError:
        shutil.copy2(binary, link)
    binary.chmod(0o755)
    link.chmod(0o755)
    for script in package_dir.glob("*.command"):
        script.chmod(0o755)


def _install_alphamelts() -> None:
    filename, expected_hash, binary_name = _alpha_asset()
    engine_dir = ROOT / "engines" / "alphamelts"
    engine_dir.mkdir(parents=True, exist_ok=True)
    zip_path = engine_dir / filename
    package_dir = engine_dir / filename.removesuffix(".zip")
    binary = package_dir / binary_name

    if not zip_path.exists() or _sha256(zip_path) != expected_hash:
        _download(f"{ALPHAMELTS_RELEASE}/{filename}", zip_path)
    actual_hash = _sha256(zip_path)
    if actual_hash != expected_hash:
        raise SystemExit(
            f"alphaMELTS archive hash mismatch for {filename}: {actual_hash}"
        )

    if not binary.exists():
        if package_dir.exists():
            shutil.rmtree(package_dir)
        print(f"Extracting alphaMELTS {ALPHAMELTS_VERSION}", flush=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(engine_dir)

    _write_runner(engine_dir, package_dir, binary_name)
    print(f"alphaMELTS installed: {engine_dir / 'run_alphamelts.command'}")


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python3 install-dependencies.py")

    _ensure_python_version()
    _ensure_requirements_file()
    python = _ensure_target_python()

    if not _install_with_uv(python):
        _install_with_pip(python)
    _install_alphamelts()

    print("\nInstall complete.")
    print(f"Run: {python} regolith-pyrolysis-run.py")
    print("Open: http://localhost:3000/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
