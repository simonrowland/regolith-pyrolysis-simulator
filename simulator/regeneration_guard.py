"""Guard against silent output-set shrinkage in regenerate-and-replace steps.

A regenerate-and-replace step rewrites a known set of artifacts in a
directory. When its scope narrows unnoticed — a defaulted-off flag, a
narrowed traversal, a filter covering only what the step itself produced —
the run can report success while leaving fewer artifacts than the previous
run, and every downstream consumer finds out late, if at all. This module
is the shared refusal point: call :func:`assert_no_silent_artifact_loss`
before replacing an output set. It refuses when the new set would drop a
previously-present artifact unless each dropped name is explicitly declared
retired. Deliberate removal stays possible; silent removal does not.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class RegenerationShrinkageError(RuntimeError):
    """A regeneration would drop previously-present artifacts without opt-out."""


class RetiredArtifactWarning(UserWarning):
    """An artifact was removed via the explicit retirement opt-out."""


@dataclass(frozen=True)
class RegenerationGuardReport:
    present: frozenset[str]
    planned: frozenset[str]
    retired_removed: frozenset[str]


def assert_no_silent_artifact_loss(
    output_dir: Path,
    planned: Iterable[str],
    *,
    managed: Iterable[str],
    retired: Iterable[str] = (),
) -> RegenerationGuardReport:
    """Refuse a regeneration that would silently drop present artifacts.

    ``managed`` is the full set of artifact names the step owns in
    ``output_dir``; ``planned`` is the subset this run will actually write.
    A managed name currently on disk but absent from ``planned`` is a
    dropped artifact: the call raises :class:`RegenerationShrinkageError`
    naming every one, unless the caller declared it in ``retired`` — the
    explicit, warned-on opt-out for deliberate retirement. Files outside
    the managed set are not the step's property and are ignored.
    """

    managed_set = frozenset(str(name) for name in managed)
    planned_set = frozenset(str(name) for name in planned)
    retired_set = frozenset(str(name) for name in retired)
    unknown = (planned_set | retired_set) - managed_set
    if unknown:
        raise ValueError(
            f"planned/retired names outside the managed artifact set: {sorted(unknown)}"
        )
    present = frozenset(
        name for name in managed_set if (Path(output_dir) / name).is_file()
    )
    dropped = present - planned_set - retired_set
    if dropped:
        raise RegenerationShrinkageError(
            f"refusing to regenerate {output_dir}: the new artifact set drops "
            f"{len(dropped)} previously-present artifact(s): "
            f"{', '.join(sorted(dropped))}. A regenerate-and-replace step "
            "must not silently shrink its output; regenerate the missing "
            "artifact(s) or declare them retired through the step's explicit "
            "opt-out."
        )
    retired_removed = present & retired_set
    for name in sorted(retired_removed):
        warnings.warn(
            f"artifact {name!r} removed from {output_dir} via explicit "
            "retirement opt-out",
            RetiredArtifactWarning,
            stacklevel=2,
        )
    return RegenerationGuardReport(
        present=present, planned=planned_set, retired_removed=retired_removed
    )
