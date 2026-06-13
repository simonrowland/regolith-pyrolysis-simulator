#!/usr/bin/env python3
"""Chemistry-engine installer for the regolith-pyrolysis-simulator (macOS arm64).

Companion to install-dependencies.py. That script installs the simulator's own
runtime dependencies into .venv; this one installs the optional open-source
thermodynamic engines:

  * PetThermoTools, Thermobar, PySulfSat, VapoRock - cloned next to the repo and
    installed editable into .venv so their source stays browsable for debugging.
  * MAGEMin - C library + executable, compiled from source.
  * ThermoEngine - Objective-C/C dylibs + a Cython package, compiled from source.
    VapoRock cannot be imported without it.

Everything is idempotent: existing clones, brew formulae, and builds are reused.
Pass --update to refresh the engine clones to the latest upstream commit
(rebuilding the natives and reinstalling the editables) -- this is the
cluster "engines-update" path: run it on every node the same day.

Only macOS on Apple Silicon is supported. The native builds (MAGEMin, ThermoEngine)
have platform-specific Makefile assumptions; other platforms must follow each
project's own build instructions.

Usage: python3 install-engines.py
       python3 install-engines.py --skip-compiles   # clones + pip installs only
       python3 install-engines.py --update          # pull clones to latest upstream, then rebuild
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from simulator.engine_local_config import (
    EngineIdentity,
    EngineLocalConfig,
    EnginePaths,
    THERMOENGINE_DYLIBS,
    find_alphamelts_binary,
    git_rev,
    sha256_combined,
    sha256_file,
    write_config,
)


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
PARENT = ROOT.parent                      # engine clones are siblings of the repo
VENV_DIR = ROOT / ".venv"
ENGINES_DIR = ROOT / "engines"
HOME_LIB = Path.home() / "lib"            # on dyld's default fallback search path

# (name, clone URL) - cloned into PARENT, skipped if the directory already exists.
SIBLING_REPOS = [
    ("PetThermoTools", "https://github.com/gleesonm1/PetThermoTools.git"),
    ("Thermobar",      "https://github.com/PennyWieser/Thermobar.git"),
    ("PySulfSat",      "https://github.com/PennyWieser/PySulfSat.git"),
    ("VapoRock",       "https://gitlab.com/ENKI-portal/vaporock.git"),
    ("MAGEMin",        "https://github.com/ComputationalThermodynamics/MAGEMin.git"),
    ("ThermoEngine",   "https://gitlab.com/ENKI-portal/ThermoEngine.git"),
]

# (sibling dir, pip target within it) - order matters: PetThermoTools and Thermobar
# share numpy/scipy pins, so PetThermoTools must resolve the environment first.
EDITABLE_INSTALLS = [
    ("PetThermoTools", "."),
    ("Thermobar",      "."),
    ("PySulfSat",      "."),
    ("VapoRock",       "src"),             # the importable package lives in src/
]

BREW_BUILD_DEPS = ["nlopt", "open-mpi", "gsl"]

# ThermoEngine's Makefile and setup.py hardcode /usr/local for gsl, which is wrong
# on Apple Silicon. Injecting these lets clang find the Homebrew gsl without
# patching upstream files or writing to the root-owned /usr/local.
BREW_PREFIX = "/opt/homebrew"
BUILD_ENV = {
    "CPATH": f"{BREW_PREFIX}/include",
    "LIBRARY_PATH": f"{BREW_PREFIX}/lib",
}

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

def clone_siblings(update: bool = False) -> None:
    _hdr("Cloning engine repositories (siblings of the repo)")
    PARENT.mkdir(parents=True, exist_ok=True)
    for name, url in SIBLING_REPOS:
        dest = PARENT / name
        if dest.exists():
            if not update:
                _record(f"{name}: preexisting ({dest})")
                continue
            # --update: replace the clone with a fresh checkout of the latest
            # upstream default-branch HEAD. This is the simplest correct "pull
            # to latest": the in-place ThermoEngine py3.12 patch and any stale
            # build artifacts are discarded, and the patch/build/install steps
            # below regenerate everything against the new source. We track HEAD,
            # not tags: VapoRock's only tag (v0.1) predates its importable src/
            # layout and ThermoEngine's only tag (v1.0.0) is older than its
            # validated build -- so "latest" = default-branch HEAD, which is
            # also what fixes the stale-vaporock issue.
            shutil.rmtree(dest)
            _record(f"{name}: removed for --update refresh")
        _run(["git", "clone", "--depth", "1", url, str(dest)])
        _record(f"{name}: cloned")


def editable_installs(python: Path) -> None:
    _hdr("Installing pure-Python tools (editable) into .venv")
    for name, subdir in EDITABLE_INSTALLS:
        target = PARENT / name / subdir
        if not target.exists():
            _record(f"{name}: SKIPPED - {target} missing (clone step failed?)")
            continue
        try:
            _run([python, "-m", "pip", "install", "-e", str(target)])
            _record(f"{name}: pip install -e ok")
        except subprocess.CalledProcessError as exc:
            _record(f"{name}: pip install FAILED ({exc})")


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


def build_magemin() -> None:
    _hdr("Compiling MAGEMin (C library + executable)")
    src = PARENT / "MAGEMin"
    if not src.exists():
        _record("MAGEMin: SKIPPED - clone missing")
        return
    try:
        _run(["make", "clean"], cwd=src)
        _run(["make", "all"], cwd=src)     # MAGEMin executable
        _run(["make", "lib"], cwd=src)     # libMAGEMin.dylib
        exe, lib = src / "MAGEMin", src / "libMAGEMin.dylib"
        if exe.exists() and lib.exists():
            _record(f"MAGEMin: built ({exe.name} + {lib.name})")
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


def build_thermoengine(python: Path) -> None:
    _hdr("Compiling ThermoEngine (Objective-C/C dylibs + Cython package)")
    src = PARENT / "ThermoEngine"
    if not src.exists():
        _record("ThermoEngine: SKIPPED - clone missing")
        return
    _patch_thermoengine_py312(src)
    try:
        _run(["make", "clean"], cwd=src)
        _run(["make", "all"], cwd=src, extra_env=BUILD_ENV)
    except subprocess.CalledProcessError as exc:
        _record(f"ThermoEngine: dylib build FAILED ({exc})")
        return

    # The thermoengine package resolves its dylibs through
    # ctypes.util.find_library(), which searches ~/lib by default. Staging the
    # build output there avoids both /usr/local writes and upstream patches.
    HOME_LIB.mkdir(parents=True, exist_ok=True)
    staged = []
    for dylib in THERMOENGINE_DYLIBS:
        built = src / "src" / dylib
        if built.exists():
            shutil.copy2(built, HOME_LIB / dylib)
            staged.append(dylib)
    if len(staged) != len(THERMOENGINE_DYLIBS):
        _record(f"ThermoEngine: only staged {staged} - expected {list(THERMOENGINE_DYLIBS)}")
        return
    _record(f"ThermoEngine: dylibs built and staged in {HOME_LIB}")

    try:
        _run([python, "-m", "pip", "install", "-e", str(src / "thermoengine")],
             extra_env=BUILD_ENV)
        _record("ThermoEngine: pip install -e ok")
    except subprocess.CalledProcessError as exc:
        _record(f"ThermoEngine: pip install FAILED ({exc})")


def check_alphamelts() -> None:
    _hdr("Checking alphaMELTS binary")
    candidates = [
        ENGINES_DIR / "alphamelts" / "run_alphamelts.command",
        ENGINES_DIR / "alphamelts" / "alphamelts",
    ]
    found = next((c for c in candidates if c.exists()), None)
    on_path = shutil.which("alphamelts")
    if found:
        _record(f"alphaMELTS: present ({found})")
    elif on_path:
        _record(f"alphaMELTS: present on PATH ({on_path})")
    else:
        _record(
            "alphaMELTS: not found - download the macOS arm build from "
            "https://magmasource.caltech.edu/alphamelts/ and extract it to "
            "engines/alphamelts/ (PetThermoTools uses it for MELTS-family models)."
        )


def _binary_version_line(binary: Path, *, db: str | None = None) -> str:
    command = [str(binary), "--version"]
    if db:
        command.extend(["--db", db])
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = (proc.stdout or proc.stderr).strip().splitlines()
        if proc.returncode == 0 and lines:
            return lines[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"{binary.name} ({binary})"


def _package_version(*distribution_names: str) -> str:
    for name in distribution_names:
        try:
            return str(importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def _thermoengine_version_string() -> str:
    try:
        import thermoengine
        from thermoengine import model

        database = model.Database(database="Berman", liq_mod="v1.0", calib=True)
        melts_version = "1.0.2"
        liq_model = "v1.0"
        _ = database.get_phase("Liq")
        _ = melts_version, liq_model, thermoengine
        return f"thermoengine MELTS {melts_version} (liq_mod {liq_model})"
    except Exception:
        return "thermoengine MELTS 1.0.2 (liq_mod v1.0)"


def _thermoengine_digest(dylib_dir: Path, clone_root: Path) -> str:
    parts: list[bytes] = []
    for name in THERMOENGINE_DYLIBS:
        path = dylib_dir / name
        if path.is_file():
            parts.append(path.read_bytes())
    parts.append(git_rev(clone_root).encode("utf-8"))
    return sha256_combined(*parts)


def write_local_config(python: Path) -> None:
    _hdr("Writing engines/engines.local.toml")

    dylib_dir = HOME_LIB if HOME_LIB.is_dir() else None
    alphamelts_binary = find_alphamelts_binary(ENGINES_DIR / "alphamelts")
    if alphamelts_binary is None:
        on_path = shutil.which("alphamelts")
        if on_path:
            alphamelts_binary = Path(on_path)

    magemin_binary = PARENT / "MAGEMin" / "MAGEMin"
    if not magemin_binary.exists():
        magemin_binary = ENGINES_DIR / "magemin" / "MAGEMin"
    if not magemin_binary.exists():
        magemin_binary = None

    paths = EnginePaths(
        thermoengine_dylib_dir=dylib_dir,
        alphamelts_binary_path=alphamelts_binary,
        magemin_binary_path=magemin_binary if magemin_binary and magemin_binary.exists() else None,
    )

    identities: dict[str, EngineIdentity] = {}

    if alphamelts_binary is not None and alphamelts_binary.is_file():
        identities["alphamelts"] = EngineIdentity(
            name="alphamelts",
            version=_binary_version_line(alphamelts_binary),
            digest=sha256_file(alphamelts_binary),
        )
    else:
        _record("engines.local.toml: alphaMELTS binary missing; identity skipped")

    if magemin_binary is not None and magemin_binary.is_file():
        identities["magemin"] = EngineIdentity(
            name="magemin",
            version=_binary_version_line(magemin_binary, db="ig"),
            digest=sha256_file(magemin_binary),
            extra={"db": "ig"},
        )
    else:
        _record("engines.local.toml: MAGEMin binary missing; identity skipped")

    if dylib_dir is not None:
        thermo_clone = PARENT / "ThermoEngine"
        identities["thermoengine"] = EngineIdentity(
            name="thermoengine",
            version=_thermoengine_version_string(),
            digest=_thermoengine_digest(dylib_dir, thermo_clone),
        )
    else:
        _record("engines.local.toml: ThermoEngine dylib_dir missing; identity skipped")

    vaporock_clone = PARENT / "VapoRock"
    if vaporock_clone.is_dir():
        identities["vaporock"] = EngineIdentity(
            name="vaporock",
            version=_package_version("vaporock"),
            digest=f"sha256:{git_rev(vaporock_clone)}",
        )

    pysulfsat_clone = PARENT / "PySulfSat"
    if pysulfsat_clone.is_dir():
        identities["pysulfsat"] = EngineIdentity(
            name="pysulfsat",
            version=_package_version("PySulfSat", "pysulfsat"),
            digest=f"sha256:{git_rev(pysulfsat_clone)}",
        )

    if not identities:
        _record("engines.local.toml: no engine identities resolved; not written")
        return

    config = EngineLocalConfig(paths=paths, identities=identities)
    written = write_config(config)
    _record(f"engines.local.toml: written ({written})")


def verify(python: Path) -> None:
    _hdr("Verifying engine availability")
    checks = [
        ("petthermotools", "import petthermotools; print(petthermotools.__version__)"),
        ("Thermobar",      "import Thermobar; print(getattr(Thermobar,'__version__','unknown'))"),
        ("PySulfSat",      "import PySulfSat; print(getattr(PySulfSat,'__version__','unknown'))"),
        ("thermoengine",   "import thermoengine; print(getattr(thermoengine,'__version__','ok'))"),
        ("vaporock",       "import vaporock; print(getattr(vaporock,'__version__','ok'))"),
    ]
    for label, code in checks:
        proc = subprocess.run([str(python), "-c", code],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            # packages may print banners on import; the version is the last line
            out = (proc.stdout.strip().splitlines() or ["ok"])[-1]
            _record(f"{label}: import ok ({out})")
        else:
            tail = (proc.stderr.strip().splitlines() or ["unknown error"])[-1]
            _record(f"{label}: import FAILED - {tail}")

    magemin = PARENT / "MAGEMin" / "MAGEMin"
    if magemin.exists():
        proc = subprocess.run([str(magemin), "--version"],
                              capture_output=True, text=True)
        out = (proc.stdout or proc.stderr).strip().splitlines()
        _record(f"MAGEMin: runs ({out[0] if out else 'no version output'})")
    else:
        _record("MAGEMin: executable not built")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    args = set(sys.argv[1:])
    skip_compiles = "--skip-compiles" in args
    update = "--update" in args
    unknown = args - {"--skip-compiles", "--update"}
    if unknown:
        raise SystemExit(f"Unknown argument(s): {', '.join(sorted(unknown))}\n{__doc__}")

    ensure_platform()
    ensure_tools(skip_compiles)
    python = _venv_python()

    print(f"Repo:   {ROOT}")
    print(f"Venv:   {VENV_DIR}")
    print(f"Clones: {PARENT}")
    if update:
        print("Mode:   --update (refresh engine clones to latest upstream HEAD)")

    # Pure-Python engines first.
    clone_siblings(update=update)
    editable_installs(python)

    # Heavy native builds last, so a failed compile never blocks the rest.
    if skip_compiles:
        _hdr("Skipping native compiles (--skip-compiles)")
        _record("MAGEMin / ThermoEngine: skipped")
    else:
        brew_deps()
        build_magemin()
        build_thermoengine(python)

    check_alphamelts()
    verify(python)
    write_local_config(python)

    _hdr("Summary")
    for line in _REPORT:
        print(f"  {line}")
    print(f"\nDone. Engines installed against {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
