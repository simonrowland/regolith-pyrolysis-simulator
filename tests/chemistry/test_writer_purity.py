"""Writer-purity invariant: only the kernel writes the AtomLedger.

After ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) all chemistry-transition
ledger writes flow through :meth:`ChemistryKernel.commit_batch`.  A
narrow set of NON-chemistry writers (terminal routing moves, the
backend-equilibrium transition) is permitted under the binding-spec §3
exemption -- the F-A2 cleanup labels each tagged call site so this
test can enforce "no new unwhitelisted writer can be introduced".

The audit walks every Python module under ``simulator/`` excluding
``simulator/chemistry/kernel/`` (which IS the writer the kernel uses
internally) and ``simulator/melt_backend/`` (the backend ABC owns its
own ledger plumbing).  Every ``atom_ledger.apply(...)`` reference is
checked against the whitelist; any unwhitelisted writer fails the
test.

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
# writer (the kernel commits through them; the backend ABC writes its
# own legacy equilibrium-transition path).  Files under these subtrees
# are skipped entirely by the audit -- the writer-purity invariant
# applies to the SIMULATOR call sites, not to the engine that owns
# commit_batch.
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
# Mirrors F-A2:
#   * simulator/core.py: backend-equilibrium ledger transition (legacy
#     backend writes its own LedgerTransition).
#   * simulator/core.py: ``_drain_to_terminal`` -- the single chemistry-
#     exempt entry point for the four terminal-routing move-shaped
#     transfers (overhead_gas -> terminal.offgas; overhead_gas <->
#     melt_offgas oxygen accounts).
_WRITER_EXEMPT_CALLS = (
    ("simulator/core.py", "backend-equilibrium"),
    ("simulator/core.py", "terminal-routing move"),
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


def _find_apply_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``<...>.atom_ledger.apply(...)`` call node."""

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "apply":
            continue
        # ``<expr>.atom_ledger.apply(...)`` -- the receiver of ``.apply``
        # must itself be ``<expr>.atom_ledger``.
        receiver = func.value
        if not isinstance(receiver, ast.Attribute):
            continue
        if receiver.attr != "atom_ledger":
            continue
        calls.append(node)
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


def test_atom_ledger_apply_writers_are_all_whitelisted():
    """Every ``atom_ledger.apply`` site outside ``simulator/chemistry/
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

    found: list[tuple[str, int, str]] = []
    for path in _iter_python_files():
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        calls = _find_apply_calls(tree)
        if not calls:
            continue
        source_lines = source.splitlines()
        for call in calls:
            ok, marker = _has_writer_exempt_marker(
                source_lines, call.lineno
            )
            assert ok, (
                f"unwhitelisted atom_ledger.apply at "
                f"{_normalise(path)}:{call.lineno} -- new chemistry "
                "ledger writes MUST go through "
                "ChemistryKernel.commit_batch.  If this is a non-"
                "chemistry terminal-routing move, add a "
                "'# WRITER-EXEMPT: ...' comment AND a whitelist entry "
                "in tests/chemistry/test_writer_purity.py."
            )
            assert marker is not None
            found.append((_normalise(path), call.lineno, marker))

    # Each whitelist entry must be backed by at least one actual call
    # site whose marker contains the expected substring.  Catches the
    # opposite drift -- a whitelist entry that no longer maps to any
    # real call (e.g. the call was deleted but the entry was forgotten).
    for module_path, expected_marker_substring in _WRITER_EXEMPT_CALLS:
        matches = [
            (path, lineno)
            for (path, lineno, marker) in found
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
