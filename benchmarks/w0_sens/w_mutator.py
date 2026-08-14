"""W0-W-MUTATOR-v1 — custodian-only source-patched-build W mutation adapter.

Frozen by PREREGISTRATION-wave0.md step 2 (line 37). The frozen mutation
mechanism is a SOURCE-PATCHED BUILD of the exact ThermoEngine
``MELTSv1.0.2`` ``LiquidMelts`` / ``liq_mod=v1.0`` implementation: for each
``(row, perturbation)`` a FRESH build prefix is made by replacing only the
named constant initializer in ``src/LiquidMelts.m``
(``referenceValuesOfModelParameters[]``, each entry tagged with a
``// <index> W(<comp>,<comp>)`` comment) with ``0``, ``+10,000``, or
``-10,000`` J, rebuilding ``libphaseobjc.dylib`` in that prefix, and
evaluating against that build in a fresh worker process. Runtime setters
are forbidden by the prereg and are never called anywhere in this package
(``tests/test_w0_sens_mutator.py`` asserts this mechanically).

Sensitivity is evaluated around a target-free origin (step 3, line 38),
never around the held row. The pristine engine tree and pristine dylib
directory are opened read-only and are never modified; "restoration" under
this mechanism is therefore structural (nothing is ever written into the
original build), and ``verify_restoration`` proves by exact readback in a
fresh worker that the original custodian-held build still reads back its
original parameter vector.

ROW IDENTITY (re-review finding 1): prereg step 2 requires a FRESH build
prefix per ``(row, perturbation)``. ``make_build`` therefore requires the
row identity, binds it into the returned record, refuses to issue a second
build for the same ``(row, perturbation)`` pair in one session, records the
binding in a per-build quarantine lock file, and ships a
:meth:`MutationBuildRecord.require_row` guard for the evaluation seam.
Build provenance is auditable: one lock JSON per ``(row, perturbation)``
under ``<quarantine>/builds/``.

CUSTODIAN BOUNDARY — HONEST CLAIM (re-review finding 2, resolved by
downgrading the claim, not by another hardening round):

What this boundary IS: procedural custody, tamper-evident by audit. It is
NOT technical isolation from a same-user process, and it cannot be: Python
offers no boundary against code running as the same OS user. Private
naming, file modes (0600/0700), and frozen dataclasses are conventions,
not enforcement — ``_custodian_worker._capture_vector`` is importable,
``PristineCapture.envelope_path``, ``W0WMutator.quarantine_dir``, and
``MutationBuild.prefix`` are readable, and mode 0600 does not isolate a
same-user process. That is accepted; no further in-process hardening round
will change it.

What the mechanism DOES prevent:

- Accidental use from the normal evaluation path: nothing in
  ``simulator``/``web``/``engines`` or the normal benchmark harness
  imports this package (``tests/test_w0_sens_mutator.py`` guards that
  mechanically), and no released-result pipeline consumes these records.
- Unlogged invocation THROUGH this instrument: every engine touch runs in
  an ephemeral worker subprocess and leaves an auditable quarantine record
  (per-build lock JSON, source diff, envelope hash), so use of this
  instrument is tamper-evident after the fact.
- Build-identity confusion — a correctness property, not a security one.
  Every engine touch (pristine capture, patched-build readback,
  restoration verify) happens inside an ephemeral worker subprocess
  (``benchmarks.w0_sens._custodian_worker``) spawned with
  ``DYLD_FALLBACK_LIBRARY_PATH`` scoped to exactly one build prefix's
  ``lib/``. The Objective-C class namespace is process-global, so one
  process can only ever register one build; a fresh process per build is
  the build-separation mechanism.

What it does NOT prevent: a determined or careless caller running as the
same user importing the worker helpers, reading the envelope or build
prefixes, or driving the mutator directly. A deliberate operator with
shell access can also hand-copy the engine tree and patch it themselves —
the source is on disk and no in-repo code can change that. REAL isolation
requires a separate security context — a distinct OS user or a
container/VM boundary with the custodian holding the only credential, the
same resolution this project already applies to executor network-egress
custody (whose manifest records the isolation mechanism and an isolated
user/container/VM identity). Whether to run W0-SENS under such a context
is an OWNER DECISION, deliberately not implemented here.

Cheap deterrents kept (custody aids, NOT isolation): quarantined values
(the original parameter vector, the held W) cross the worker boundary only
into the quarantine envelope file (mode 0600) under ``quarantine_dir``;
worker stdout carries verdicts, slot indices, hashes, and the readback of
the *substituted* (frozen, public) value only; ``MutationBuild`` has no
``phase`` handle and no ``pristine_J`` field; and ``quarantine_dir`` must
resolve OUTSIDE the repository tree so build artifacts, diffs (which quote
the pristine initializer line), and the envelope can never land in
executor staging by accident.
"""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from benchmarks.w0_sens import W0SensAbort


W0_W_MUTATOR_ID = "W0-W-MUTATOR-v1"

# Frozen substitution values (J/mol), prereg step 2. The control build is
# exactly 0 J; the two signed perturbations are exactly +/-10,000 J.
CONTROL_J = 0.0
PLUS_J = 10_000.0
MINUS_J = -10_000.0
FROZEN_PERTURBATIONS_J = (CONTROL_J, PLUS_J, MINUS_J)

# Exact engine parameter name form, e.g. ``W(Na2SiO3   ,SiO2)``: matched
# byte-for-byte against the live ``param_names`` vector, then parsed for the
# component pair. In the ``LiquidMelts.m`` initializer comments BOTH
# components are padded to ``_COMMENT_PAD_WIDTH`` characters.
PARAM_NAME_RE = re.compile(r"^W\(\s*(.+?)\s*,\s*(.+?)\s*\)$")
_COMMENT_PAD_WIDTH = 10

MELTS_MODEL = "MELTSv1.0.2"
SOURCE_FILE_REL = Path("src") / "LiquidMelts.m"
WORKER_MODULE = "_custodian_worker.py"
WORKER_SENTINEL = "W0SENS_WORKER_JSON:"

# Reserved row identity for the step-2 synthetic pre-screen gate. The gate
# is target-free (it is not a benchmark row), but its builds are bound to
# this identity like any other so the per-(row, perturbation) freshness
# rule applies uniformly.
SYNTHETIC_GATE_ROW_ID = "__w0_sens_synthetic_gate__"

# Homebrew gsl lives outside the Makefile's hardcoded /usr/local paths;
# install-engines.py establishes this same build environment.
_BUILD_ENV_EXTRA = {
    "CPATH": "/opt/homebrew/include",
    "LIBRARY_PATH": "/opt/homebrew/lib",
}

_DYLIB_NAMES = ("libphaseobjc.dylib", "libswimdew.dylib", "libspeciation.dylib")

# One initializer line: leading ws, a numeric literal, comma, then a
# ``// <index> W(<comp>,<comp>)`` comment. Both components are padded; a
# component may itself contain parentheses (e.g. Ca2(PO4)2), so the name
# group runs greedily to the LAST closing paren before end-of-line.
_INITIALIZER_RE = re.compile(
    r"^(?P<lead>\s*)"
    r"(?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"(?P<trail>\s*,\s*//\s*(?P<idx>\d+)\s+(?P<name>W\(.*\))\s*)$"
)


class CustodianBoundaryError(RuntimeError):
    """A custodian-custody rule was violated.

    Custody here is procedural (tamper-evident by audit), not a security
    boundary against a same-user process — see the module docstring.
    """


class AbortWMutator(W0SensAbort):
    """Frozen typed abort for step 2: the mutation mechanism failed."""

    abort_type = "ABORT-W-MUTATOR"


@dataclass(frozen=True)
class PatchResult:
    """Value-free record of one source patch.

    The replaced (pristine) literal is deliberately NOT recorded anywhere in
    this process: it is a quarantined W-side value. The full unified diff
    (which quotes it) is written to the quarantine; only its hash, the
    changed line number, and the parameter identity are releasable.
    """

    line_number: int  # 1-based line in src/LiquidMelts.m
    slot_index: int  # parameter index from the initializer comment
    n_initializers: int  # total initializer entries in the array
    patched_source_sha256: str
    pristine_source_sha256: str
    diff_sha256: str


@dataclass(frozen=True)
class BuildIdentity:
    """Version identity the step-2 lock requires for every build."""

    melts_model: str
    engine_root: str
    engine_git_rev: str
    makefile_sha256: str
    module_sha256: str | None  # thermoengine package __init__.py, if resolvable
    pristine_dylib_sha256: Mapping[str, str]


@dataclass(frozen=True)
class PristineCapture:
    """Value-free record of the original custodian-held build."""

    param_name: str
    slot_index: int
    n_slots: int
    vector_sha256: str
    envelope_path: str
    identity: BuildIdentity


@dataclass(frozen=True)
class MutationBuildRecord:
    """Lock-record fields for one ``(row, perturbation)`` source-patched build.

    ``row_id`` binds the build to the single benchmark row it was made for
    (prereg step 2: a fresh build prefix per ``(row, perturbation)``);
    ``require_row`` is the evaluation-side guard enforcing that binding.
    ``readback_J`` is the readback of the SUBSTITUTED value (a frozen,
    public constant), asserted exact by both the worker and this parent.
    ``changed_slots`` is the runtime full-vector structural diff proving no
    other model datum changed. ``lock_path`` points at the per-build
    quarantine lock JSON carrying the same provenance for audit. No
    pristine value is present.
    """

    mutator_id: str
    row_id: str
    param_name: str
    component_i: str
    component_j: str
    slot_index: int
    perturbation_J: float
    readback_J: float
    changed_slots: tuple[int, ...]
    n_slots: int
    prefix: str
    lock_path: str
    patch: PatchResult
    binary_sha256: str
    identity: BuildIdentity

    def require_row(self, row_id: str) -> None:
        """Refuse to let this build serve any row but the one it was made for."""
        if str(row_id) != self.row_id:
            raise AbortWMutator(
                f"build for row {self.row_id!r} was used for row "
                f"{row_id!r}; a fresh build per (row, perturbation) is "
                "required"
            )


@dataclass(frozen=True)
class MutationBuild(MutationBuildRecord):
    """PRODUCTION lock record — minted only by an all-default-seams mutator.

    Any injected ``builder``/``worker_runner``/engine-path override flips
    the session to :class:`NonProductionMutationBuild`, so an injected fake
    cannot mint a record that passes as a production live-verified build.
    """


@dataclass(frozen=True)
class NonProductionMutationBuild(MutationBuildRecord):
    """NON-PRODUCTION build record — never a production W0-SENS lock record.

    Returned by :meth:`W0WMutator.make_build` whenever any dependency-
    injection seam (``builder``, ``worker_runner``, ``engine_root``,
    ``pristine_dylib_dir``) is in use — i.e. every synthetic test harness.
    It is deliberately NOT a subclass of :class:`MutationBuild`, so a
    pipeline consuming the production record cannot accept it (the same
    resolution as ``NonProductionBootstrapInterval`` in the driver).
    """


@dataclass(frozen=True)
class RestorationRecord:
    """Exact restoration/readback verdict for the original build."""

    param_name: str
    vector_matches_pristine: bool
    vector_sha256: str


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def comment_form(param_name: str) -> str:
    """Map the runtime parameter name to its initializer-comment form."""
    match = PARAM_NAME_RE.fullmatch(str(param_name))
    if match is None:
        raise AbortWMutator(
            f"unrecognized MELTS W parameter name: {param_name!r}"
        )
    comp_i, comp_j = match.group(1), match.group(2)
    return f"W({comp_i:<{_COMMENT_PAD_WIDTH}},{comp_j:<{_COMMENT_PAD_WIDTH}})"


def _format_frozen_literal(value: float) -> str:
    """Exact C double literal for a frozen value (0.0 / 10000.0 / -10000.0)."""
    return f"{float(value):.1f}"


def patch_initializer(
    source_text: str, param_name: str, value_J: float
) -> tuple[str, PatchResult]:
    """Replace only the named constant initializer with a frozen value.

    The target line is identified by its ``// <index> W(...)`` comment,
    matched byte-for-byte against the padded comment form. Exactly one line
    may match; zero or several matches are version-identity failures
    (``ABORT-W-MUTATOR``). The returned :class:`PatchResult` is value-free.
    """
    wanted = comment_form(param_name)
    lines = source_text.splitlines(keepends=True)
    matches: list[tuple[int, re.Match[str]]] = []
    n_initializers = 0
    for lineno, line in enumerate(lines, start=1):
        match = _INITIALIZER_RE.match(line.rstrip("\n"))
        if match is None:
            continue
        n_initializers += 1
        if match.group("name") == wanted:
            matches.append((lineno, match))
    if n_initializers == 0:
        raise AbortWMutator(
            f"{SOURCE_FILE_REL} contains no W initializer lines; the engine "
            "source identity cannot be confirmed"
        )
    if len(matches) != 1:
        raise AbortWMutator(
            f"initializer comment {wanted!r} matches {len(matches)} lines in "
            f"{SOURCE_FILE_REL}; the exact engine parameter identity cannot "
            "be confirmed"
        )
    lineno, match = matches[0]
    literal = _format_frozen_literal(value_J)
    original_line = lines[lineno - 1]
    ending = "\n" if original_line.endswith("\n") else ""
    patched_line = f"{match.group('lead')}{literal}{match.group('trail')}{ending}"
    lines[lineno - 1] = patched_line
    patched_text = "".join(lines)
    diff_text = "".join(
        difflib.unified_diff(
            [original_line],
            [patched_line],
            fromfile=str(SOURCE_FILE_REL),
            tofile=str(SOURCE_FILE_REL),
            lineterm="",
        )
    )
    result = PatchResult(
        line_number=lineno,
        slot_index=int(match.group("idx")),
        n_initializers=n_initializers,
        patched_source_sha256=_sha256_text(patched_text),
        pristine_source_sha256=_sha256_text(source_text),
        diff_sha256=_sha256_text(diff_text),
    )
    return patched_text, result


def _diff_lines(before: str, after: str) -> tuple[int, ...]:
    """1-based line numbers that differ between two texts (value-free)."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if len(before_lines) != len(after_lines):
        raise AbortWMutator(
            "patched source changed the line count; the patch must replace "
            "exactly one initializer literal in place"
        )
    return tuple(
        index
        for index, (old, new) in enumerate(zip(before_lines, after_lines), start=1)
        if old != new
    )


def _git_rev(engine_root: Path) -> str:
    try:
        rev = subprocess.check_output(
            ["git", "-C", str(engine_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return rev or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _default_engine_root() -> Path:
    """Locate the ThermoEngine source checkout WITHOUT importing it.

    ``importlib.util.find_spec`` does not execute the module, so the parent
    process never loads the engine or its dylib.
    """
    spec = importlib.util.find_spec("thermoengine")
    if spec is None or spec.origin is None:
        raise AbortWMutator(
            "the thermoengine package is not resolvable; version identity "
            "cannot be established"
        )
    engine_root = Path(spec.origin).resolve().parents[2]
    if not (engine_root / "Makefile").is_file() or not (
        engine_root / SOURCE_FILE_REL
    ).is_file():
        raise AbortWMutator(
            f"{engine_root} is not a ThermoEngine source checkout with "
            f"Makefile and {SOURCE_FILE_REL}; version identity cannot be "
            "established"
        )
    return engine_root


def _default_pristine_dylib_dir() -> Path:
    """Pristine staged dylib dir from engines.local.toml (no env mutation)."""
    from simulator.engine_local_config import load_config

    config = load_config()
    candidates: list[Path] = []
    if config is not None and config.paths.thermoengine_dylib_dir is not None:
        candidates.append(Path(config.paths.thermoengine_dylib_dir).expanduser())
    candidates.append(Path.home() / "lib")
    for candidate in candidates:
        if all((candidate / name).is_file() for name in _DYLIB_NAMES):
            return candidate
    raise AbortWMutator(
        f"no pristine dylib directory holding {list(_DYLIB_NAMES)}; version "
        "identity cannot be established"
    )


def _worker_env(prefix_lib: Path) -> dict[str, str]:
    """Worker process environment: exactly one build prefix is loadable.

    ``DYLD_FALLBACK_LIBRARY_PATH`` is REPLACED (never extended) with the
    build prefix's ``lib/`` so ``ctypes.util.find_library('phaseobjc')`` can
    only resolve to this prefix's ``libphaseobjc.dylib``. The Objective-C
    runtime registers classes process-globally at load, so this env scoping
    plus a fresh process is the whole build-separation mechanism — a
    correctness property (no two builds' classes can mix), not a security
    boundary against a same-user process.
    """
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["DYLD_FALLBACK_LIBRARY_PATH"] = str(prefix_lib)
    return env


def _spawn_worker(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Production worker runner: one fresh subprocess per engine touch."""
    worker_path = Path(__file__).resolve().parent / WORKER_MODULE
    prefix_lib = Path(str(spec["prefix_lib"])).resolve()
    try:
        completed = subprocess.run(
            [sys.executable, str(worker_path)],
            input=json.dumps(dict(spec)),
            capture_output=True,
            text=True,
            env=_worker_env(prefix_lib),
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AbortWMutator(f"custodian worker failed to run: {exc!r}") from exc
    payload: dict[str, Any] | None = None
    for line in completed.stdout.splitlines():
        if line.startswith(WORKER_SENTINEL):
            try:
                payload = json.loads(line[len(WORKER_SENTINEL):])
            except json.JSONDecodeError:
                payload = None
    if payload is None:
        raise AbortWMutator(
            "custodian worker emitted no result envelope; exit="
            f"{completed.returncode} stderr_tail={completed.stderr[-500:]!r}"
        )
    return payload


def _make_libphaseobjc(build_root: Path) -> None:
    """Rebuild ``libphaseobjc.dylib`` inside a copied (patched) tree."""
    env = dict(os.environ)
    env.update(_BUILD_ENV_EXTRA)
    try:
        completed = subprocess.run(
            ["make", "libphaseobjc.dylib"],
            cwd=build_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AbortWMutator(f"source-patched build failed to run: {exc!r}") from exc
    if completed.returncode != 0:
        raise AbortWMutator(
            "source-patched build failed: make libphaseobjc.dylib exit="
            f"{completed.returncode} stderr_tail={completed.stderr[-500:]!r}"
        )
    built = build_root / "src" / "libphaseobjc.dylib"
    if not built.is_file():
        raise AbortWMutator(
            "source-patched build produced no src/libphaseobjc.dylib"
        )


class W0WMutator:
    """W0-W-MUTATOR-v1 orchestrator over source-patched build prefixes.

    This parent process never loads the engine. ``builder`` and
    ``worker_runner`` are the dependency-injection seams that keep the test
    suite synthetic; production defaults rebuild with ``make`` and spawn
    the custodian worker subprocess. ANY injected seam (``builder``,
    ``worker_runner``, ``engine_root``, ``pristine_dylib_dir``) makes the
    whole session NON-PRODUCTION: :meth:`make_build` then returns
    :class:`NonProductionMutationBuild`, a distinct type that no
    released-result pipeline (which consumes only the production
    :class:`MutationBuild`) can accept — an injected fake cannot mint a
    record that passes as a production live-verified build.
    """

    mutator_id = W0_W_MUTATOR_ID

    def __init__(
        self,
        *,
        param_name: str,
        quarantine_dir: Path | str,
        engine_root: Path | str | None = None,
        pristine_dylib_dir: Path | str | None = None,
        builder: Callable[[Path], None] | None = None,
        worker_runner: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        match = PARAM_NAME_RE.fullmatch(str(param_name))
        if match is None:
            raise AbortWMutator(
                f"unrecognized MELTS W parameter name: {param_name!r}"
            )
        self._component_i, self._component_j = match.group(1), match.group(2)
        self._param_name = str(param_name)
        quarantine = Path(quarantine_dir).resolve()
        root = repo_root()
        if quarantine == root or root in quarantine.parents:
            raise CustodianBoundaryError(
                "quarantine_dir must resolve OUTSIDE the repository tree: "
                "build artifacts and quarantined values may never land in "
                "executor staging"
            )
        quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(quarantine, 0o700)
        self._quarantine = quarantine
        self._engine_root = (
            Path(engine_root).resolve()
            if engine_root is not None
            else _default_engine_root()
        )
        self._pristine_dylib_dir = (
            Path(pristine_dylib_dir).resolve()
            if pristine_dylib_dir is not None
            else _default_pristine_dylib_dir()
        )
        self._builder = builder if builder is not None else _make_libphaseobjc
        self._worker_runner = (
            worker_runner if worker_runner is not None else _spawn_worker
        )
        # Production typing: only the all-default-seams configuration (the
        # pinned engine paths, the real ``make`` builder, the real worker
        # subprocess) mints production :class:`MutationBuild` records.
        self._production = (
            builder is None
            and worker_runner is None
            and engine_root is None
            and pristine_dylib_dir is None
        )
        # (row_id, perturbation_J) pairs already built this session; the
        # frozen fresh-build-per-(row, perturbation) rule is enforced by
        # refusing re-issue of a successfully built pair.
        self._built_pairs: set[tuple[str, float]] = set()
        self._capture: PristineCapture | None = None
        self._identity = self._build_identity()

    def _build_identity(self) -> BuildIdentity:
        spec = importlib.util.find_spec("thermoengine")
        module_sha256 = (
            _sha256_file(Path(spec.origin).resolve())
            if spec is not None and spec.origin is not None
            else None
        )
        pristine_hashes = {
            name: _sha256_file(self._pristine_dylib_dir / name)
            for name in _DYLIB_NAMES
            if (self._pristine_dylib_dir / name).is_file()
        }
        if sorted(pristine_hashes) != sorted(_DYLIB_NAMES):
            raise AbortWMutator(
                f"pristine dylib dir {self._pristine_dylib_dir} is missing "
                "staged dylibs; version identity cannot be established"
            )
        return BuildIdentity(
            melts_model=MELTS_MODEL,
            engine_root=str(self._engine_root),
            engine_git_rev=_git_rev(self._engine_root),
            makefile_sha256=_sha256_file(self._engine_root / "Makefile"),
            module_sha256=module_sha256,
            pristine_dylib_sha256=pristine_hashes,
        )

    @property
    def param_name(self) -> str:
        return self._param_name

    @property
    def quarantine_dir(self) -> Path:
        return self._quarantine

    @property
    def identity(self) -> BuildIdentity:
        return self._identity

    @property
    def _envelope_path(self) -> Path:
        return self._quarantine / "pristine-envelope.json"

    def _run_worker(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._worker_runner(spec)
        if not isinstance(payload, dict) or "ok" not in payload:
            raise AbortWMutator(
                f"custodian worker returned a malformed envelope: {payload!r}"
            )
        if not payload["ok"]:
            raise AbortWMutator(
                "custodian worker aborted: "
                f"{payload.get('abort', 'ABORT-W-MUTATOR')}: "
                f"{payload.get('detail', '<no detail>')}"
            )
        return payload

    def capture_pristine(self) -> PristineCapture:
        """Capture the original build's vector into the quarantine envelope.

        The full pristine parameter vector (including the held W) is written
        by the worker to ``pristine-envelope.json`` (mode 0600) inside the
        quarantine and never enters this process; only slot indices and a
        joint vector hash cross the boundary.
        """
        if self._capture is not None:
            return self._capture
        payload = self._run_worker(
            {
                "command": "capture-pristine",
                "prefix_lib": str(self._pristine_dylib_dir),
                "param_name": self._param_name,
                "envelope_path": str(self._envelope_path),
            }
        )
        capture = PristineCapture(
            param_name=self._param_name,
            slot_index=int(payload["slot_index"]),
            n_slots=int(payload["n_slots"]),
            vector_sha256=str(payload["vector_sha256"]),
            envelope_path=str(self._envelope_path),
            identity=self._identity,
        )
        self._capture = capture
        return capture

    def make_build(
        self, perturbation_J: float, *, row_id: str
    ) -> MutationBuildRecord:
        """One fresh source-patched build prefix for one ``(row, perturbation)``.

        Copies the engine source tree into a fresh prefix, patches exactly
        the named constant initializer, rebuilds ``libphaseobjc.dylib``
        there, stages the prefix ``lib/`` (patched ``libphaseobjc`` plus
        byte-identical pristine ``libswimdew``/``libspeciation``), and reads
        the build back in a fresh worker process.

        ``row_id`` is REQUIRED and is bound into the returned record and its
        quarantine lock file: the frozen mechanism is a fresh build per
        ``(row, perturbation)``, so a second successful build for the same
        pair in one session is refused, and the record's
        :meth:`MutationBuildRecord.require_row` refuses evaluation against
        any other row. The record type is :class:`MutationBuild` only in an
        all-default-seams (production) session; any injected seam yields
        :class:`NonProductionMutationBuild`.
        """
        value = float(perturbation_J)
        if value not in FROZEN_PERTURBATIONS_J:
            raise AbortWMutator(
                f"perturbation {value!r} J is outside the frozen "
                "{0, +10000, -10000} J set; the screen may only substitute "
                "the preregistered values"
            )
        row = str(row_id).strip()
        if not row:
            raise AbortWMutator(
                "make_build requires a non-empty row_id: a fresh build per "
                "(row, perturbation) must be bound to its row"
            )
        pair = (row, value)
        if pair in self._built_pairs:
            raise AbortWMutator(
                f"a build for (row {row!r}, perturbation {value:+.0f} J) "
                "was already issued this session; fresh-per-(row, "
                "perturbation) means exactly one build per pair — start a "
                "new mutator session to rebuild"
            )
        capture = self.capture_pristine()
        build_id = f"slot{capture.slot_index:03d}_{value:+08.0f}J_{uuid.uuid4().hex[:8]}"
        prefix = self._quarantine / "builds" / build_id
        build_root = prefix / "build"
        shutil.copytree(
            self._engine_root / "src", build_root / "src", symlinks=True
        )
        shutil.copy2(self._engine_root / "Makefile", build_root / "Makefile")

        source_path = build_root / SOURCE_FILE_REL
        pristine_text = source_path.read_text()
        patched_text, patch = patch_initializer(pristine_text, self._param_name, value)
        changed_lines = _diff_lines(pristine_text, patched_text)
        if changed_lines != (patch.line_number,):
            raise AbortWMutator(
                "structural source diff shows lines other than the named "
                f"initializer changed: {changed_lines!r}"
            )
        if patch.slot_index != capture.slot_index:
            raise AbortWMutator(
                f"source initializer index {patch.slot_index} != runtime "
                f"parameter slot {capture.slot_index}; source/runtime "
                "version identity mismatch"
            )
        source_path.write_text(patched_text)
        # The full unified diff quotes the pristine initializer line; it is
        # quarantine-only. The lock carries its hash plus the line number.
        diff_dir = self._quarantine / "diffs"
        diff_dir.mkdir(exist_ok=True)
        diff_path = diff_dir / f"{build_id}.diff"
        diff_path.write_text(
            f"--- pristine {SOURCE_FILE_REL}\n+++ patched {SOURCE_FILE_REL}\n"
            f"@@ line {patch.line_number} @@\n"
            f"-{pristine_text.splitlines()[patch.line_number - 1]}\n"
            f"+{patched_text.splitlines()[patch.line_number - 1]}\n"
        )
        os.chmod(diff_path, 0o600)

        self._builder(build_root)
        lib_dir = prefix / "lib"
        lib_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build_root / "src" / "libphaseobjc.dylib", lib_dir)
        for name in ("libswimdew.dylib", "libspeciation.dylib"):
            shutil.copy2(self._pristine_dylib_dir / name, lib_dir)
        binary_sha256 = _sha256_file(lib_dir / "libphaseobjc.dylib")

        payload = self._run_worker(
            {
                "command": "readback-build",
                "prefix_lib": str(lib_dir),
                "param_name": self._param_name,
                "expected_value_J": value,
                "envelope_path": str(self._envelope_path),
            }
        )
        readback = float(payload["readback_J"])
        if readback != value:
            raise AbortWMutator(
                f"readback of the substituted value {readback!r} J does not "
                f"equal the frozen value {value!r} J"
            )
        changed_slots = tuple(int(slot) for slot in payload["changed_slots"])
        if changed_slots != (capture.slot_index,):
            raise AbortWMutator(
                "runtime structural diff shows a model datum other than "
                f"{self._param_name!r} changed at slots {changed_slots!r}"
            )
        record_type = MutationBuild if self._production else NonProductionMutationBuild
        lock_path = self._quarantine / "builds" / f"{build_id}.lock.json"
        lock = {
            "mutator_id": self.mutator_id,
            "record_type": (
                "MutationBuild" if self._production else "NonProductionMutationBuild"
            ),
            "row_id": row,
            "param_name": self._param_name,
            "component_i": self._component_i,
            "component_j": self._component_j,
            "slot_index": capture.slot_index,
            "perturbation_J": value,
            "readback_J": readback,
            "changed_slots": list(changed_slots),
            "n_slots": int(payload["n_slots"]),
            "prefix": str(prefix),
            "patch": asdict(patch),
            "binary_sha256": binary_sha256,
            "identity": asdict(self._identity),
        }
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        os.chmod(lock_path, 0o600)
        self._built_pairs.add(pair)
        return record_type(
            mutator_id=self.mutator_id,
            row_id=row,
            param_name=self._param_name,
            component_i=self._component_i,
            component_j=self._component_j,
            slot_index=capture.slot_index,
            perturbation_J=value,
            readback_J=readback,
            changed_slots=changed_slots,
            n_slots=int(payload["n_slots"]),
            prefix=str(prefix),
            lock_path=str(lock_path),
            patch=patch,
            binary_sha256=binary_sha256,
            identity=self._identity,
        )

    def verify_restoration(self) -> RestorationRecord:
        """Prove the original custodian-held build still reads back exactly.

        Under fresh-per-build prefixes the pristine tree and dylibs are never
        written, so restoration is structural; this fresh-worker readback is
        the step-2 'exact restoration/readback of the original
        custodian-held build': the worker reloads the pristine build and
        compares its full parameter vector against the quarantine envelope.
        """
        capture = self.capture_pristine()
        payload = self._run_worker(
            {
                "command": "verify-restoration",
                "prefix_lib": str(self._pristine_dylib_dir),
                "envelope_path": str(self._envelope_path),
            }
        )
        record = RestorationRecord(
            param_name=self._param_name,
            vector_matches_pristine=bool(payload["vector_matches_pristine"]),
            vector_sha256=str(payload["vector_sha256"]),
        )
        if not record.vector_matches_pristine:
            raise AbortWMutator(
                "post-build readback of the original custodian-held build "
                "differs from the captured pristine vector; restoration "
                "cannot be proven"
            )
        if record.vector_sha256 != capture.vector_sha256:
            raise AbortWMutator(
                "post-build pristine vector hash differs from the capture "
                "hash; the original build changed under the screen"
            )
        return record


def synthetic_response_gate(
    mutator: W0WMutator,
    evaluate: Callable[[MutationBuildRecord], float],
    analytic: Callable[[float], float],
    *,
    rel_tol: float = 1.0e-8,
    abs_tol: float = 1.0e-12,
) -> tuple[tuple[float, float, float], ...]:
    """Step-2 pre-screen gate: a synthetic row with an analytic response.

    Each sign is evaluated against its OWN fresh source-patched build
    prefix (``mutator.make_build`` is called once per sign, bound to the
    reserved :data:`SYNTHETIC_GATE_ROW_ID` identity). The measured
    response must reproduce the analytic response for each sign to ``1e-8``
    relative or ``1e-12`` absolute; any failure is ``ABORT-W-MUTATOR`` and
    no chemical compute starts.
    """
    outcomes: list[tuple[float, float, float]] = []
    for value in (PLUS_J, MINUS_J):
        build = mutator.make_build(value, row_id=SYNTHETIC_GATE_ROW_ID)
        build.require_row(SYNTHETIC_GATE_ROW_ID)
        measured = float(evaluate(build))
        expected = float(analytic(value))
        tolerance = max(rel_tol * abs(expected), abs_tol)
        if not (measured == measured) or abs(measured - expected) > tolerance:
            raise AbortWMutator(
                f"synthetic analytic-response check failed at "
                f"{value:+.0f} J: measured={measured!r} "
                f"expected={expected!r} tol={tolerance!r}"
            )
        outcomes.append((value, measured, expected))
    return tuple(outcomes)
