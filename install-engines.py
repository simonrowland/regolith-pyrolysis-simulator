#!/usr/bin/env python3
"""Chemistry-engine installer for the regolith-pyrolysis-simulator (macOS arm64).

Companion to install-dependencies.py. That script installs the simulator's own
runtime dependencies into .venv; this one installs the optional open-source
thermodynamic engines:

  * PetThermoTools, Thermobar, PySulfSat, VapoRock - pinned clones installed
    editable into .venv so their source stays browsable for debugging.
  * MAGEMin - C library + executable, compiled from source.
  * ThermoEngine - Objective-C/C dylibs + a Cython package, compiled from source.
    VapoRock cannot be imported without it.
  * alphaMELTS - the pinned app artifact installed by install-dependencies.py.

Engine source pins and build commands live in engines/engines.lock.toml.
Versioned installs live under ~/repos/engine-bin/<engine>/<pin>/ and are shared
by every checkout on the host. Existing clones at a different pin are preserved.
The installer is idempotent; change the lock to update an engine.

Only macOS on Apple Silicon is supported. The native builds (MAGEMin, ThermoEngine)
have platform-specific Makefile assumptions; other platforms must follow each
project's own build instructions.

Usage: python3 install-engines.py
       python3 install-engines.py --skip-compiles   # clones + pip installs only
       python3 install-engines.py --check           # read-only lock drift report
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os
import platform
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_local_config_loader import load_engine_local_config

_engine_config = load_engine_local_config(Path(__file__).resolve().parent)
EngineCheckReport = _engine_config.EngineCheckReport
EngineLock = _engine_config.EngineLock
artifact_for_platform = _engine_config.artifact_for_platform
checkout_matches_pin = _engine_config.checkout_matches_pin
check_engine_install = _engine_config.check_engine_install
config_path = _engine_config.config_path
engine_install_path = _engine_config.engine_install_path
engine_install_root = _engine_config.engine_install_root
load_engine_lock = _engine_config.load_engine_lock
thermoengine_artifact_names = _engine_config.thermoengine_artifact_names
write_locked_config = _engine_config.write_locked_config


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def _repo_root() -> Path:
    """Main repo checkout, even when this script runs from a linked worktree."""
    here = Path(__file__).resolve().parent
    try:
        common = subprocess.check_output(
            ["git", "-C", str(here), "rev-parse",
             "--path-format=absolute", "--git-common-dir"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        main_root = Path(common).parent
        if (main_root / "pyproject.toml").exists():
            return main_root
    except Exception:
        pass
    return here


ROOT = _repo_root()
VENV_DIR = ROOT / ".venv"
ENGINE_ROOT = engine_install_root()

BREW_BUILD_DEPS = ["nlopt", "open-mpi", "gsl"]

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

_REPORT: list[str] = []


def _hdr(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def _say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _record(line: str) -> None:
    _REPORT.append(line)
    _say(line)


def _run(command: list[str], cwd: Path | None = None,
         extra_env: dict[str, str] | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"  + {printable}", flush=True)
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    subprocess.check_call(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def _venv_python() -> Path:
    python = VENV_DIR / "bin" / "python"
    if not python.exists():
        raise SystemExit(
            f"Virtual environment not found at {VENV_DIR}\n"
            f"Run install-dependencies.py first to create it."
        )
    return python


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------

def ensure_platform() -> None:
    system, machine = platform.system(), platform.machine()
    if system != "Darwin" or machine != "arm64":
        raise SystemExit(
            f"install-engines.py supports macOS arm64 only "
            f"(detected {system} {machine}).\n"
            f"On other platforms, build MAGEMin and ThermoEngine using each "
            f"project's own instructions, then pip install the editable clones."
        )


def ensure_tools(skip_compiles: bool) -> None:
    needed = ["git"]
    if not skip_compiles:
        needed += ["brew", "make", "clang"]
    missing = [t for t in needed if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"Missing required tools: {', '.join(missing)}\n"
            f"Install the Xcode command line tools (xcode-select --install) "
            f"and Homebrew (https://brew.sh)."
        )


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def clone_locked_sources(lock: EngineLock, root: Path) -> None:
    _hdr("Installing pinned engine source checkouts")
    root.mkdir(parents=True, exist_ok=True)
    for key, pin in sorted(lock.engines.items()):
        if pin.source_kind != "git":
            continue
        dest = engine_install_path(lock, root, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if not checkout_matches_pin(pin, dest):
                _record(
                    f"{pin.name}: REFUSED - {dest} does not match locked "
                    f"commit {pin.pin}; existing data was preserved"
                )
                continue
            _record(f"{pin.name}: pinned clone preexisting ({dest})")
            continue
        try:
            _run(["git", "clone", "--no-checkout", pin.url, str(dest)])
            _run(["git", "-C", str(dest), "checkout", "--detach", pin.pin])
            _record(f"{pin.name}: cloned at {pin.pin}")
        except subprocess.CalledProcessError as exc:
            _record(f"{pin.name}: clone FAILED ({exc})")


def editable_installs(python: Path, lock: EngineLock, root: Path) -> None:
    _hdr("Installing pure-Python tools (editable) into .venv")
    for key, pin in sorted(lock.engines.items()):
        if pin.source_kind != "git" or pin.editable_subdir is None:
            continue
        source = engine_install_path(lock, root, key)
        target = source / pin.editable_subdir
        if not checkout_matches_pin(pin, source):
            _record(f"{pin.name}: REFUSED - editable install source is not at {pin.pin}")
            continue
        if not target.exists():
            _record(f"{pin.name}: SKIPPED - {target} missing (clone step failed?)")
            continue
        try:
            _run([python, "-m", "pip", "install", "-e", str(target)])
            _record(f"{pin.name}: pip install -e ok")
        except subprocess.CalledProcessError as exc:
            _record(f"{pin.name}: pip install FAILED ({exc})")


def brew_deps() -> None:
    _hdr("Installing native build dependencies via Homebrew")
    for formula in BREW_BUILD_DEPS:
        already = subprocess.run(
            ["brew", "list", "--versions", formula],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        if already:
            _record(f"{formula}: preexisting")
            continue
        _run(["brew", "install", formula])
        _record(f"{formula}: installed")


def build_magemin(lock: EngineLock, root: Path) -> None:
    _hdr("Compiling MAGEMin (C library + executable)")
    pin = lock.engines["magemin"]
    src = engine_install_path(lock, root, "magemin")
    if not checkout_matches_pin(pin, src):
        _record(f"MAGEMin: SKIPPED - source is not at locked commit {pin.pin}")
        return
    try:
        for command in pin.build_commands:
            _run(list(command), cwd=src, extra_env=dict(pin.build_env))
        if all((src / artifact).is_file() for artifact in pin.artifacts):
            _record(f"MAGEMin: built from {pin.pin}")
        else:
            _record("MAGEMin: build ran but artifacts missing")
    except subprocess.CalledProcessError as exc:
        _record(f"MAGEMin: build FAILED ({exc})")


def _patch_thermoengine_py312(src: Path) -> None:
    """Make ThermoEngine importable on Python 3.11+.

    Several thermoengine dataclasses define __eq__ without __hash__, so Python
    sets __hash__ = None and the interpreter then rejects those classes when
    they are used as dataclass field defaults. Both edits are idempotent.
    """
    pkg = src / "thermoengine" / "thermoengine"

    # Atom.__eq__ compares by symbol only -> give it a matching __hash__.
    chem_library = pkg / "chem_library.py"
    if chem_library.exists():
        text = chem_library.read_text()
        eq_block = (
            "    def __eq__(self, other):\n"
            "        if type(other) is Atom:\n"
            "            return self.symbol == other.symbol\n"
            "        else:\n"
            "            return self.symbol == other\n"
        )
        if "def __hash__" not in text and eq_block in text:
            chem_library.write_text(text.replace(
                eq_block,
                eq_block + "\n    def __hash__(self):\n        return hash(self.symbol)\n",
                1,
            ))
            _record("ThermoEngine: patched chem_library.py (Atom.__hash__)")

    # The Comp composition dataclasses use approximate __eq__, so let dataclass
    # synthesise a field-based __hash__ via unsafe_hash rather than inventing one.
    chemistry = pkg / "chemistry.py"
    if chemistry.exists():
        text = chemistry.read_text()
        if "@dataclass(order=True)" in text:
            chemistry.write_text(
                text.replace("@dataclass(order=True)",
                             "@dataclass(order=True, unsafe_hash=True)")
            )
            _record("ThermoEngine: patched chemistry.py (Comp unsafe_hash)")


def build_thermoengine(python: Path, lock: EngineLock, root: Path) -> None:
    _hdr("Compiling ThermoEngine (Objective-C/C dylibs + Cython package)")
    pin = lock.engines["thermoengine"]
    src = engine_install_path(lock, root, "thermoengine")
    if not checkout_matches_pin(pin, src):
        _record(f"ThermoEngine: SKIPPED - source is not at locked commit {pin.pin}")
        return
    if "python312-hashability" in pin.patches:
        _patch_thermoengine_py312(src)
    try:
        for command in pin.build_commands:
            _run(list(command), cwd=src, extra_env=dict(pin.build_env))
    except subprocess.CalledProcessError as exc:
        _record(f"ThermoEngine: dylib build FAILED ({exc})")
        return

    dylib_dir = src / (pin.runtime_directory or "lib")
    dylib_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    dylib_names = thermoengine_artifact_names(lock)
    for artifact, dylib in zip(pin.artifacts, dylib_names, strict=True):
        built = src / "src" / dylib
        if built.exists():
            target = src / artifact
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, target)
            staged.append(dylib)
    expected_dylibs = list(dylib_names)
    if len(staged) != len(expected_dylibs):
        _record(f"ThermoEngine: only staged {staged} - expected {expected_dylibs}")
        return
    _record(f"ThermoEngine: dylibs built and staged in {dylib_dir}")

    home_lib = Path.home() / "lib"
    home_lib.mkdir(parents=True, exist_ok=True)
    for artifact, dylib in zip(pin.artifacts, dylib_names, strict=True):
        target = (src / artifact).resolve()
        link = home_lib / dylib
        if link.is_symlink() and link.resolve() == target:
            continue
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)

    try:
        _run([python, "-m", "pip", "install", "-e", str(src / "thermoengine")],
             extra_env=dict(pin.build_env))
        _record("ThermoEngine: pip install -e ok")
    except subprocess.CalledProcessError as exc:
        _record(f"ThermoEngine: pip install FAILED ({exc})")


def alpha_artifact_notice(lock: EngineLock, root: Path) -> None:
    pin = lock.engines["alphamelts"]
    asset = artifact_for_platform(pin)
    artifact = engine_install_path(lock, root, "alphamelts") / asset.runtime_artifact
    if artifact.is_file():
        _record(f"alphaMELTS: staged at {artifact}")
    else:
        _record(
            f"alphaMELTS: missing {artifact}; run install-dependencies.py to "
            "download the locked archive and verify its checksum"
        )


def print_check_report(report: EngineCheckReport) -> int:
    _hdr(f"Read-only engine check on {report.host}")
    for key, relative, digest in report.artifact_shas:
        _record(f"artifact sha256 {key}/{relative}: {digest}")
    for drift in report.drifts:
        _record(
            f"{drift.severity.upper()} {drift.code} {drift.engine} "
            f"{drift.path}: {drift.message}"
        )
    if not report.drifts:
        _record("engine root and local config match the lock")
    _record(
        "Compare artifact sha256 lines across hosts; native build differences are "
        "informational because locked source commits are authoritative."
    )
    return 1 if report.has_errors else 0


def verify(python: Path, lock: EngineLock, root: Path) -> None:
    _hdr("Verifying engine availability")
    checks = [
        ("petthermotools", "import petthermotools; print(petthermotools.__version__)"),
        ("Thermobar",      "import Thermobar; print(getattr(Thermobar,'__version__','unknown'))"),
        ("PySulfSat",      "import PySulfSat; print(getattr(PySulfSat,'__version__','unknown'))"),
        ("thermoengine",   "import thermoengine; print(getattr(thermoengine,'__version__','ok'))"),
        ("vaporock",       "import vaporock; print(getattr(vaporock,'__version__','ok'))"),
    ]
    env = os.environ.copy()
    home_lib = Path.home() / "lib"
    existing_library_path = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
        part for part in (str(home_lib), existing_library_path) if part
    )
    failed = False
    for label, code in checks:
        proc = subprocess.run([str(python), "-c", code],
                              capture_output=True, text=True, env=env)
        if proc.returncode == 0:
            # packages may print banners on import; the version is the last line
            out = (proc.stdout.strip().splitlines() or ["ok"])[-1]
            _record(f"{label}: import ok ({out})")
        else:
            failed = True
            tail = (proc.stderr.strip().splitlines() or ["unknown error"])[-1]
            _record(f"{label}: import FAILED - {tail}")

    magemin_pin = lock.engines["magemin"]
    magemin = engine_install_path(lock, root, "magemin") / (
        magemin_pin.runtime_artifact or "MAGEMin"
    )
    if checkout_matches_pin(
        magemin_pin, engine_install_path(lock, root, "magemin")
    ) and magemin.exists():
        proc = subprocess.run(
            [str(magemin), "--version"], capture_output=True, text=True
        )
        out = (proc.stdout or proc.stderr).strip().splitlines()
        _record(f"MAGEMin: runs ({out[0] if out else 'no version output'})")
    else:
        _record("MAGEMin: executable not built")

    if failed:
        raise SystemExit("Engine verification failed; see failed imports above")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    args = set(sys.argv[1:])
    check_only = "--check" in args
    skip_compiles = "--skip-compiles" in args
    unknown = args - {"--skip-compiles", "--check"}
    if unknown:
        raise SystemExit(f"Unknown argument(s): {', '.join(sorted(unknown))}\n{__doc__}")

    if check_only:
        if args != {"--check"}:
            raise SystemExit("--check cannot be combined with installer options")
        lock = load_engine_lock()
        report = check_engine_install(lock, ENGINE_ROOT, local_config=config_path())
        return print_check_report(report)

    ensure_platform()
    ensure_tools(skip_compiles)
    python = _venv_python()
    lock = load_engine_lock()

    print(f"Repo:   {ROOT}")
    print(f"Venv:   {VENV_DIR}")
    print(f"Engines: {ENGINE_ROOT}")

    clone_locked_sources(lock, ENGINE_ROOT)
    editable_installs(python, lock, ENGINE_ROOT)
    alpha_artifact_notice(lock, ENGINE_ROOT)

    # Heavy native builds last, so a failed compile never blocks the rest.
    if skip_compiles:
        _hdr("Skipping native compiles (--skip-compiles)")
        _record("MAGEMin / ThermoEngine: skipped")
    else:
        brew_deps()
        build_magemin(lock, ENGINE_ROOT)
        build_thermoengine(python, lock, ENGINE_ROOT)

    verify(python, lock, ENGINE_ROOT)
    written = write_locked_config(lock, ENGINE_ROOT)
    _record(f"engines.local.toml: written from lock ({written})")
    check_status = print_check_report(
        check_engine_install(lock, ENGINE_ROOT)
    )

    _hdr("Summary")
    for line in _REPORT:
        print(f"  {line}")
    print(f"\nDone. Engines provisioned against {python}")
    return check_status


if __name__ == "__main__":
    raise SystemExit(main())
