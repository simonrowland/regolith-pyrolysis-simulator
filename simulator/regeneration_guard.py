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

import contextlib
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


class PlannedArtifactNotWrittenError(RegenerationShrinkageError):
    """A run planned an artifact, deleted the old copy, and never wrote it."""


def verify_planned_artifacts_written(
    output_dir: Path, report: RegenerationGuardReport
) -> None:
    """Refuse a run that planned an artifact and then did not write it.

    The pre-write check compares what is PRESENT against what is PLANNED, so a
    name in ``planned`` passes it by construction. That leaves a gap the
    pre-write check cannot see: the caller unlinks every planned name, the run
    then writes only some of them -- a conditional write whose condition came
    out false, an early return, a swallowed error -- and the run reports
    success with the artifact gone. That is the b-200 shape reproduced inside
    the b-200 fix (kimi cross-cut M4).

    A plan is a promise, not evidence. This is the evidence.

    Empty files count as not written: a zero-byte CSV where rows were expected
    is the same silent loss wearing a filename.
    """
    missing = sorted(
        name for name in report.planned
        if not (Path(output_dir) / name).is_file()
        or (Path(output_dir) / name).stat().st_size == 0
    )
    if missing:
        raise PlannedArtifactNotWrittenError(
            f"regeneration of {output_dir} planned {len(report.planned)} "
            f"artifact(s) but did not write {len(missing)}: "
            f"{', '.join(missing)}. The previous copies were already removed, "
            "so the output set has shrunk while the run reported success. "
            "Write the artifact, or declare it retired through the explicit "
            "opt-out instead of planning it."
        )


@contextlib.contextmanager
def regeneration_guard(
    output_dir: Path,
    planned: Iterable[str],
    *,
    managed: Iterable[str],
    retired: Iterable[str] = (),
):
    """Pre-write shrink check on entry, written-what-you-planned check on exit.

    Preferred over calling the two checks by hand: the post-check is the one
    that is easy to forget, and forgetting it restores the exact hole this
    exists to close. The exit check runs only on the success path -- a run that
    raised has already failed loudly and does not need a second complaint.
    """
    report = assert_no_silent_artifact_loss(
        output_dir, planned, managed=managed, retired=retired
    )
    yield report
    verify_planned_artifacts_written(output_dir, report)
