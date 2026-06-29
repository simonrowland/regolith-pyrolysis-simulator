"""Writer-purity invariant: only the kernel writes the AtomLedger.

After ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) all chemistry-transition
ledger writes flow through the ChemistryKernel.  The only remaining
simulator-side exemptions are seed-time ``load_external`` calls and the
atom-balanced reagent shuttle.

The audit walks every Python module under ``simulator/`` excluding
``simulator/chemistry/kernel/`` (which IS the writer the kernel uses
internally) and ``simulator/melt_backend/`` (the backend ABC owns its
own ledger plumbing).  Every direct ``atom_ledger`` writer reference is
checked against the seed-time rule or the whitelist; any unwhitelisted
writer fails the test.

The whitelist below mirrors the F-A2 ``# WRITER-EXEMPT:`` comments in
``simulator/core.py``.  Each entry is ``(module path, AST node line)``
-- new exemptions must add a tagged comment AND a whitelist entry, so
the audit stays in lock-step with the codebase.
"""

from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SIMULATOR_ROOT = _REPO_ROOT / "simulator"

# Directories under simulator/ where atom_ledger.apply IS the legitimate
# writer (the kernel commits through them; the backend ABC builds
# backend-local transitions).  Files under these subtrees are skipped
# entirely by the audit -- the writer-purity invariant applies to the
# SIMULATOR call sites, not to the kernel writer internals.
_EXEMPT_SUBDIRS = (
    _SIMULATOR_ROOT / "chemistry" / "kernel",
    _SIMULATOR_ROOT / "melt_backend",
    _SIMULATOR_ROOT / "accounting",
)

# Whitelist of tagged exemption sites.  Each entry is
# ``(simulator-relative module path, expected nearby # WRITER-EXEMPT
# marker)``.  The marker is grepped on (or above) the call line so a
# reviewer cannot silently strip the label without the test catching
# it.
#
_WRITER_EXEMPT_CALLS = (
    # _move_ledger_species: atom-balanced reagent shuttle between accounts.
    ("simulator/extraction.py", "shuttle-reagent-move"),
    ("simulator/extraction.py", "c7-al-credit-funding"),
)

_ATOM_LEDGER_WRITER_METHODS = (
    "apply",
    "load_external",
    "move",
    "transfer",
)

# ``load_external`` is legitimate only while constructing the per-batch
# ledger from already-computed inventory projections.  These helpers are
# invoked from ``_seed_atom_ledger``; post-init direct loads must fail loud.
_SEED_LOAD_EXTERNAL_CONTEXTS = (
    ("simulator/core.py", "_load_ledger_account"),
    ("simulator/core.py", "_record_stage0_carbon_cleanup_transitions"),
    ("simulator/core.py", "_record_stage0_carbonate_decomposition_transitions"),
    ("simulator/core.py", "_record_stage0_oxidation_transitions"),
    ("simulator/core.py", "_record_stage0_perchlorate_cleanup_transitions"),
    ("simulator/core.py", "_seed_atom_ledger"),
)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for path in _SIMULATOR_ROOT.rglob("*.py"):
        if any(_is_under(path, exempt) for exempt in _EXEMPT_SUBDIRS):
            continue
        files.append(path)
    return sorted(files)


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function_name(
    parents: dict[ast.AST, ast.AST], node: ast.AST
) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _is_seed_load_external_call(
    path: Path, parents: dict[ast.AST, ast.AST], node: ast.AST
) -> bool:
    context = _enclosing_function_name(parents, node)
    return (_normalise(path), context) in _SEED_LOAD_EXTERNAL_CONTEXTS


def _find_atom_ledger_writer_calls(
    path: Path, tree: ast.AST
) -> list[tuple[ast.Call, str]]:
    """Return every non-seed ``<...>.atom_ledger.<writer>(...)`` call."""

    parents = _parent_map(tree)
    calls: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in _ATOM_LEDGER_WRITER_METHODS:
            continue
        # ``<expr>.atom_ledger.apply(...)`` -- the receiver of the writer
        # method must itself be ``<expr>.atom_ledger``.
        receiver = func.value
        if not isinstance(receiver, ast.Attribute):
            continue
        if receiver.attr != "atom_ledger":
            continue
        if func.attr == "load_external" and _is_seed_load_external_call(
            path, parents, node
        ):
            continue
        calls.append((node, func.attr))
    return calls


def _has_writer_exempt_marker(
    source_lines: list[str], call_line: int
) -> tuple[bool, str | None]:
    """Search up to ~10 lines above ``call_line`` for a ``# WRITER-EXEMPT:``
    marker.  Returns ``(found, marker_text)``.

    The 10-line window covers the realistic comment placement (block
    comment + a few blank lines + the call) without sweeping in
    unrelated comments from a neighbouring function.
    """
    start = max(0, call_line - 11)
    window = source_lines[start:call_line]
    for line in window:
        if "WRITER-EXEMPT" in line:
            return True, line.strip().lstrip("#").strip()
    return False, None


def _normalise(module_path: Path) -> str:
    return str(module_path.relative_to(_REPO_ROOT))


def test_atom_ledger_direct_writers_are_all_whitelisted():
    """Every direct ``atom_ledger`` writer outside ``simulator/chemistry/
    kernel/`` and ``simulator/melt_backend/`` must be:

      1. listed in :data:`_WRITER_EXEMPT_CALLS` (with a description that
         appears nearby in the source as a ``# WRITER-EXEMPT: ...``
         comment), AND
      2. tagged in the source with a ``# WRITER-EXEMPT:`` marker on or
         within ~10 lines above the call.

    If you add a NEW direct ledger writer outside the kernel, this
    test will fail -- routing through ``ChemistryKernel.commit_batch``
    is the correct fix.  Add a whitelist entry only when the writer
    is genuinely outside the chemistry-transition vocabulary (e.g. a
    new terminal-routing move).
    """

    found: list[tuple[str, int, str, str]] = []
    for path in _iter_python_files():
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        calls = _find_atom_ledger_writer_calls(path, tree)
        if not calls:
            continue
        source_lines = source.splitlines()
        for call, method in calls:
            ok, marker = _has_writer_exempt_marker(
                source_lines, call.lineno
            )
            assert ok, (
                f"unwhitelisted atom_ledger.{method} at "
                f"{_normalise(path)}:{call.lineno} -- new chemistry "
                "ledger writes MUST go through "
                "ChemistryKernel.commit_batch.  load_external is only "
                "allowed for seed-time bulk loads.  If this is a non-"
                "chemistry atom-balanced account move, add a "
                "'# WRITER-EXEMPT: ...' comment AND a whitelist entry "
                "in tests/chemistry/test_writer_purity.py."
            )
            assert marker is not None
            found.append((_normalise(path), call.lineno, method, marker))

    # Each whitelist entry must be backed by at least one actual call
    # site whose marker contains the expected substring.  Catches the
    # opposite drift -- a whitelist entry that no longer maps to any
    # real call (e.g. the call was deleted but the entry was forgotten).
    for module_path, expected_marker_substring in _WRITER_EXEMPT_CALLS:
        matches = [
            (path, lineno)
            for (path, lineno, _method, marker) in found
            if path == module_path
            and expected_marker_substring in marker
        ]
        assert matches, (
            f"whitelist entry ({module_path!r}, "
            f"{expected_marker_substring!r}) has no matching tagged "
            "call site -- update _WRITER_EXEMPT_CALLS if the writer "
            "was intentionally removed."
        )


def test_no_atom_ledger_record_writers_outside_kernel():
    """``atom_ledger.record`` was removed at the
    ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) flips; assert no caller
    ever re-introduces it under ``simulator/`` outside the kernel /
    accounting / melt_backend subtrees.

    This is the second leg of the writer-purity invariant -- the
    legacy ``record`` API was the pre-kernel direct write that the
    flips replaced.  Catching its return alongside any unwhitelisted
    ``.apply`` keeps the kernel-as-sole-writer contract enforced.
    """
    record_sites: list[tuple[str, int]] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "record":
                continue
            receiver = func.value
            if (
                isinstance(receiver, ast.Attribute)
                and receiver.attr == "atom_ledger"
            ):
                record_sites.append((_normalise(path), node.lineno))
    assert not record_sites, (
        "atom_ledger.record callers must route through "
        "ChemistryKernel.commit_batch; found: "
        f"{record_sites}"
    )
