"""W0-SENS production screen runner — the frozen pre-step, end to end.

Frozen by PREREGISTRATION-wave0.md section 1.2 (lines 34-42) with the
immutable-record and typed-abort rules of section 9 (lines 301-323). This
module is the runner the aborted 2026-08-14 attempt found missing
(``sensitivity-screen/SCREEN-RESULTS.md:39-56``). It sequences, per
candidate join:

  step 1  pin the repository SHA, the benchmark harness, the bench set, the
          engine identity, the mutation adapter, and the output schema;
  step 2  build the ``0 J`` synthetic control, run the MANDATORY signed
          synthetic gate (both signs, ``1e-8`` relative / ``1e-12``
          absolute) for EVERY candidate before ANY candidate is screened;
  step 3  build ``0 J`` / ``+10,000 J`` / ``-10,000 J`` prefixes;
  step 4  evaluate the complete sealed eligible corpus against each build,
          typing every refused cell rather than imputing zero;
  step 5  ``C`` / ``S`` / ``I_measured`` / ``R_measured`` with the frozen
          ``C=0`` zeros, plus the rank-1 Na-anchor gate;
  step 6  release only the restricted public aggregate;
  step 7  the fixed PCG64/649013/10,000-replicate cluster bootstrap and the
          reserve-versus-fourth ``ABORT-RANKING-INVALIDATED`` decision.

It also emits, MECHANICALLY, the three material-evidence disclosures the
campaign requires: they are computed from the corpus rows actually consumed
(``benchmarks/w0_sens/corpus.py``), never carried as prose.

BUILDING THIS MODULE RAN NO SCREEN. No build was made, no held
interaction-parameter value was read, and no ranking was produced; the
focused tests drive synthetic fixtures only.

==========================================================================
AMBIGUITY LEDGER — READINGS TAKEN, FOR CONTROLLER ADJUDICATION
==========================================================================
The preregistration is frozen and this runner IMPLEMENTS it; where it is
genuinely ambiguous the reading taken is recorded here rather than chosen
silently. Every one of these should be adjudicated BEFORE the re-run:
adjudicating after seeing a result is the selective-abort failure mode the
preregistration forbids (line 297).

A-1  "A fresh build prefix is made for each ``(row, perturbation)``" (:37)
     — WHICH row? READING: the W SNAPSHOT row, i.e. the join's named
     constant (section 8.1 lines 249-256 is the only table that defines a
     "row" with a named constant; the same sentence says "replacing only
     THAT named constant initializer", and a benchmark corpus row names no
     constant). Consequence: 3 corpus builds per join, not 3 per bench row,
     and the corpus is evaluated against each build. The alternative
     reading costs 120 real rebuilds per join for bit-identical results.

A-2  "Run ITS complete measured corpus" (:39) — per-join subsets, or one
     shared eligible corpus? READING: ONE sealed eligible corpus (step 4
     seals a single ``W0-SENS-ELIGIBLE-MANIFEST.json``), and every
     candidate is screened over all of it. Cross-join comparability is
     required by step 7, which compares ``R`` between joins. Eligibility is
     fail-closed on the metadata step 4 demands (scored + experimental
     evidence class + ``provenance.source_sha256``); on the tracked bench
     set that admits the 40 scored ``a(SiO2)`` rows over 19 clusters and
     EXCLUDES the Hastie-1981 and Richter Type-B rows, which carry no
     scoring status, evidence class, or source hash. CONSEQUENCE, stated
     plainly: with that corpus the CaSiO3/Mg2SiO4/Fe2SiO4 and reserve joins
     have no cation in any eligible composition, so each measures ``C=0``
     and, by the frozen ``C=0`` convention, ``R_measured=0`` with ``h=0``.
     Step 7 then evaluates ``0 - 0 > 0`` = false: no abort, and no measured
     basis for reordering those four. Adding evidence-class and source-hash
     metadata to the Hastie/Richter rows is the concrete way to give this
     screen leverage on joins 2-5.

A-3  "For every eligible channel" (:39-40) — a channel per measured ROW, or
     per observable species? READING: per measured row, which is what the
     committed driver implements (one ``ChannelResponse`` per row) and what
     step 4's per-``(source, composition, T, species, type)`` manifest
     enumerates. The channel identity string therefore encodes T and the
     observed label (``a(SiO2)@1473K``), because the driver keys sealed
     rows on ``(source, composition_id, channel)`` with no temperature
     field and REFUSES duplicates — the four-temperature Yamaguchi
     compositions would otherwise collide. NOTE the consequence the
     controller should weigh: under this reading ``C`` counts rows, so
     ``R_measured`` scales with how many rows a literature source happened
     to publish.

A-4  "if ``R_reserve - R_fourth > max(h_reserve, h_fourth)``" (:42) — which
     join is "the fourth"? READING: the join at FROZEN expected rank 4
     (Fe2SiO4-SiO2). "The reserve" and "the fourth" are the
     preregistration's own labels from its section 1.1 table, and the very
     next sentence treats measured reordering as a separate, later thing.

A-5  "A synthetic row with an analytic response" (:37) — the analytic form
     is not specified anywhere. READING: the exact partial derivative of an
     endmember chemical potential with respect to the named constant in the
     MELTS symmetric regular-solution liquid, measured as the difference
     against a dedicated ``0 J`` control build at the same fabricated state
     point. Full derivation, structural check (15 endmembers, 105 = 15*14/2
     parameters), unit check, and sanity checks are in the
     ``benchmarks/w0_sens/evaluator.py`` module docstring. A gate failure
     means either a numerically silent substitution or that the
     regular-solution premise is wrong for this build; both are
     ``ABORT-W-MUTATOR`` and the record reports measured vs expected so an
     auditor can tell which.

A-6  "before any candidate is screened" (:37) — one gate, or one per
     parameter? READING: BOTH constraints are honoured. The gate is
     per-parameter (a ``W0WMutator`` session binds one ``param_name``), and
     the runner completes every candidate's gate in a first phase before
     the first corpus evaluation of any candidate.

A-7  Bootstrap seed stream (:42) — one shared stream, or restart per join?
     READING: the frozen production entry point constructs a fresh
     ``PCG64(649013)`` per call, so every join draws the identical cluster
     index sequence (paired resampling). That is the committed behaviour;
     it is recorded here because the preregistration does not say it.

A-8  The reserve join's engine parameter name is given nowhere (:28 names
     the join; section 8.1 lists only the four selected rows). READING:
     resolved from the live parameter vector by exact name; the worker
     refuses unless exactly one live slot matches. ``W(CaSiO3
     ,Mg2SiO4)`` is that unique match on the pinned build.

A-9  "at least two eligible Na-bearing source clusters, at least one
     nonmissing channel per cluster" (:40) — per WHICH clusters? The
     committed, reviewed driver applies the nonmissing requirement to EVERY
     cluster in the response set (``driver.py:544-559``); the permissive
     reading binds only the Na-bearing anchor clusters. THIS RUNNER DOES
     NOT PRE-FILTER: it passes the sealed corpus as sealed, because
     dropping dark clusters would reinterpret the procedure through data
     selection, visible to an auditor only as a smaller corpus. HIGHEST-
     CONSEQUENCE OPEN ITEM: the committed pre-seal feasibility evidence
     (``sensitivity-screen/FEASIBILITY-PRESEAL-2.md``) predicts several
     high-Na clusters return no channel at all, in which case the strict
     reading fires ``ABORT-RANKING-INSTRUMENT-NULL`` before ``C`` is ever
     computed. That is a designed refusal, not a defect — but it must be
     adjudicated before the re-run, not after.

A-10 Released fields (:41, :278) — both enumerations omit the bootstrap
     bounds that step 7 needs for its own decision. READING: ``L``, ``U``,
     and ``h`` are CUSTODIAN-RECORD ONLY; the staged public aggregate
     carries exactly row id, ``C``, ``S``, ``I_measured``, ``R_measured``,
     typed missing counts, and signed artifact hashes.

A-11 Does section 9's immutable ``runs/<run-id>/`` record bind a pre-step?
     READING: yes. "Every attempt" is unqualified and the discipline that a
     failed or partial record is never replaced by a successful-looking
     summary is exactly what a typed abort needs. The runner creates the
     run directory FIRST and records every abort into it — including
     precondition refusals, evidence-grade aborts, and (at the CLI) aborts
     fired while parsing the corpus or the lock before a runner exists.

A-12 fO2 protocol (:38 "every other input stays fixed") — unstated.
     READING: identical to the pinned harness — intrinsic/unpinned for
     ``activity`` and ``activity_coefficient`` observables, the row's own
     pin for gas observables — held fixed across control and both signs and
     recorded in the lock.

A-13 Evidence grade (:20) — ``E`` must come from a sealed
     ``EVIDENCE-GRADE-LOCK.json`` naming all three classes per join; a
     missing class or non-independent citation is ``ABORT-EVIDENCE-GRADE``
     and the grade is NOT lowered in place. READING: implemented literally;
     the expected ``E=3`` of the section 1.1 table is a prior, never a
     substitute for the lock, so the screen refuses without it.

A-14 "both signed responses" in the step-2 lock (:37). READING: the
     synthetic gate's two ``(value_J, measured, expected)`` triples. The
     corpus reading would place patched-engine outputs in the lock, which
     is permissible custodian-side but is a different artifact.

A-15 Section 5.2's ``+10 J``/``-10 J`` finite-difference basis check (:152)
     names the same mechanism but is a later staging-time step; the frozen
     perturbation set here is ``{0, +10000, -10000} J`` and the mutator
     refuses anything else. Out of scope for W0-SENS; flagged because the
     two sections will collide when 5.2 is implemented.

A-16 "the larger signed-magnitude shift across the two perturbations"
     (:40) — is a channel with ONE typed-missing sign "affected" when its
     present sign clears the 0.05 dex bar? READING (review 2026-08-14,
     MEDIUM): NO. "Across the two perturbations" requires BOTH signed
     responses; a channel with a typed-missing sign is MISSING, not
     affected, and its typed missing cells still count toward the released
     missing counts. The earlier max-over-available-signs reading counted
     a one-signed channel silently; A-1..A-15 never disclosed that
     reading, so it is recorded here now.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _datetime
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from benchmarks.w0_sens import W0SensAbort
from benchmarks.w0_sens.corpus import (
    EligibleCorpus,
    assemble_responses,
    read_eligible_corpus,
)
from benchmarks.w0_sens.driver import (
    BootstrapInterval,
    ChannelResponse,
    JoinMetrics,
    bootstrap_R_interval,
    compute_join_metrics,
)
from benchmarks.w0_sens.evaluator import (
    PatchedBuildEvaluator,
    SyntheticGateCallbacks,
    canonical_json,
)
from benchmarks.w0_sens.w_mutator import (
    CONTROL_J,
    MINUS_J,
    PLUS_J,
    MutationBuild,
    MutationBuildRecord,
    W0WMutator,
    repo_root,
    synthetic_response_gate,
)


SCREEN_ID = "W0-SENS-SCREEN-v1"

PREREGISTRATION_REL = Path(
    "docs-private/research/2026-08-09-upstream-mission/melts-backtest/"
    "PREREGISTRATION-wave0.md"
)
BENCH_HARNESS_REL = Path("benchmarks/melt_activity_benchmark.py")
DEFAULT_BENCH_SET_REL = Path("data/melt_activity/basalt-bench-set-v1.yaml")
INSTRUMENT_PACKAGE_REL = Path("benchmarks/w0_sens")

CSV_FIELDS = (
    "expected_rank",
    "candidate_join",
    "evidence_grade",
    "I_expected",
    "R_expected",
    "status",
    "abort_type",
    "C",
    "S",
    "I_measured",
    "R_measured",
    "bootstrap_lower",
    "bootstrap_upper",
    "bootstrap_half_width",
    "typed_missing_cells",
    "control_artifact_sha256",
    "plus_artifact_sha256",
    "minus_artifact_sha256",
)

# Fields the custodian may release (step 6, line 41; staging table line 278).
# Bootstrap bounds are deliberately absent — see AMBIGUITY A-10.
PUBLIC_AGGREGATE_FIELDS = (
    "row_id",
    "C",
    "S",
    "I_measured",
    "R_measured",
    "typed_missing_cells",
    "artifact_sha256",
)

_BUILD_KEYS = ("control", "plus", "minus")
_BUILD_VALUES = {"control": CONTROL_J, "plus": PLUS_J, "minus": MINUS_J}

# Step-2 gate tolerances (:37), passed explicitly so the emitted lock cannot
# disagree with the tolerance actually applied.
GATE_REL_TOL = 1.0e-8
GATE_ABS_TOL = 1.0e-12


class ScreenPreconditionRefusal(W0SensAbort):
    """An instrument precondition failed; the frozen attempt never starts.

    The refusal is still written into the immutable run directory like any
    other abort (A-11), but it is not one of the frozen section-9 outcome
    types and can never masquerade as one: its record carries this
    distinct type, no metrics, and no ranking. Fixing a precondition
    happens outside a frozen run.
    """

    abort_type = "ABORT-W0-SENS-PRECONDITION"


class AbortEvidenceGrade(W0SensAbort):
    """Frozen typed abort (line 20): an evidence-grade class is missing."""

    abort_type = "ABORT-EVIDENCE-GRADE"


class AbortRankingInvalidated(W0SensAbort):
    """Frozen typed abort (line 42): the reserve out-scores the fourth."""

    abort_type = "ABORT-RANKING-INVALIDATED"


@dataclass(frozen=True)
class CandidateJoin:
    """One frozen candidate from the section 1.1 table (lines 22-30)."""

    expected_rank: str
    join_id: str
    param_name: str
    snapshot_row: int | None
    I_expected: int
    R_expected: int
    disposition: str
    na_anchor: bool = False
    is_reserve: bool = False
    is_fourth: bool = False

    @property
    def row_id(self) -> str:
        """Build/row identity: the W snapshot row this join screens (A-1)."""
        if self.snapshot_row is None:
            return f"reserve::{self.join_id}"
        return f"snapshot-row-{self.snapshot_row}::{self.join_id}"


FROZEN_CANDIDATES: tuple[CandidateJoin, ...] = (
    CandidateJoin(
        expected_rank="1",
        join_id="Na2SiO3-SiO2",
        param_name="W(Na2SiO3   ,SiO2)",
        snapshot_row=10,
        I_expected=5,
        R_expected=15,
        disposition="selected",
        na_anchor=True,
    ),
    CandidateJoin(
        expected_rank="2=",
        join_id="CaSiO3-SiO2",
        param_name="W(CaSiO3    ,SiO2)",
        snapshot_row=9,
        I_expected=4,
        R_expected=12,
        disposition="selected",
    ),
    CandidateJoin(
        expected_rank="2=",
        join_id="Mg2SiO4-SiO2",
        param_name="W(Mg2SiO4   ,SiO2)",
        snapshot_row=6,
        I_expected=4,
        R_expected=12,
        disposition="selected",
    ),
    CandidateJoin(
        expected_rank="4",
        join_id="Fe2SiO4-SiO2",
        param_name="W(Fe2SiO4   ,SiO2)",
        snapshot_row=4,
        I_expected=3,
        R_expected=9,
        disposition="selected",
        is_fourth=True,
    ),
    CandidateJoin(
        expected_rank="5-reserve",
        join_id="CaSiO3-Mg2SiO4",
        # A-8: not given in the preregistration; the unique live match.
        param_name="W(CaSiO3    ,Mg2SiO4)",
        snapshot_row=None,
        I_expected=2,
        R_expected=6,
        disposition="reserve",
        is_reserve=True,
    ),
)


@dataclass(frozen=True)
class EvidenceGradeLock:
    """A sealed ``EVIDENCE-GRADE-LOCK.json`` (line 20).

    Every join must name all three earning classes with distinct,
    non-blank citations; a citation may earn exactly one role. A missing
    class, a blank citation, or a non-independent citation (one citation
    serving as source, bracket, and/or derivation) is
    ``ABORT-EVIDENCE-GRADE`` and the grade is never lowered in place.
    """

    sha256: str
    grades: Mapping[str, int]
    citations: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_bytes(cls, raw: bytes, *, required_joins: Sequence[str]) -> EvidenceGradeLock:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AbortEvidenceGrade(
                f"evidence-grade lock is not valid JSON: {exc!r}"
            ) from exc
        entries = document.get("joins") if isinstance(document, dict) else None
        if not isinstance(entries, dict):
            raise AbortEvidenceGrade(
                "evidence-grade lock must be an object with a joins mapping"
            )
        grades: dict[str, int] = {}
        citations: dict[str, Mapping[str, Any]] = {}
        for join_id in required_joins:
            entry = entries.get(join_id)
            if not isinstance(entry, dict):
                raise AbortEvidenceGrade(
                    f"evidence-grade lock names no entry for join {join_id!r}"
                )
            sources = entry.get("independent_sources")
            calorimetry = entry.get("direct_calorimetry_or_phase_equilibrium_brackets")
            derivation = entry.get("independent_derivation")
            if not isinstance(sources, list) or len(sources) < 2:
                raise AbortEvidenceGrade(
                    f"join {join_id!r} names fewer than two independent sources"
                )
            cleaned: dict[str, list[str]] = {}
            for label, value in (
                ("independent source", sources),
                ("direct calorimetry / phase-equilibrium brackets", calorimetry),
                ("independent derivation", derivation),
            ):
                if not isinstance(value, list) or not value:
                    raise AbortEvidenceGrade(
                        f"join {join_id!r} names no {label}"
                    )
                texts = [str(item).strip() for item in value]
                if any(not text for text in texts):
                    raise AbortEvidenceGrade(
                        f"join {join_id!r} carries a blank {label} citation; "
                        "a blank citation earns nothing"
                    )
                if len(set(texts)) != len(texts):
                    raise AbortEvidenceGrade(
                        f"join {join_id!r} repeats one citation as several "
                        f"{label} entries"
                    )
                cleaned[label] = texts
            if len(set(cleaned["independent source"])) < 2:
                raise AbortEvidenceGrade(
                    f"join {join_id!r} repeats one citation as two independent "
                    "sources"
                )
            # A-13: a citation earns exactly ONE role. One citation serving
            # as source, bracket, and/or independent derivation is a
            # non-independent citation — ABORT-EVIDENCE-GRADE, and the
            # grade is never lowered in place.
            owner: dict[str, str] = {}
            for label, texts in cleaned.items():
                for text in texts:
                    prior = owner.get(text)
                    if prior is not None:
                        raise AbortEvidenceGrade(
                            f"join {join_id!r} cites {text!r} as both "
                            f"{prior} and {label}; a non-independent "
                            "citation is ABORT-EVIDENCE-GRADE"
                        )
                    owner[text] = label
            grades[join_id] = 3
            citations[join_id] = {
                "independent_sources": cleaned["independent source"],
                "direct_calorimetry_or_phase_equilibrium_brackets": cleaned[
                    "direct calorimetry / phase-equilibrium brackets"
                ],
                "independent_derivation": cleaned["independent derivation"],
            }
        return cls(
            sha256=hashlib.sha256(raw).hexdigest(),
            grades=grades,
            citations=citations,
        )


@dataclass(frozen=True)
class RankingDecision:
    """Step-7 reserve-versus-fourth decision (line 42)."""

    reserve_join: str
    fourth_join: str
    reserve_R: float
    fourth_R: float
    reserve_half_width: float
    fourth_half_width: float
    margin: float
    threshold: float
    invalidated: bool
    execution_order: tuple[str, ...]
    membership: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reserve_join": self.reserve_join,
            "fourth_join": self.fourth_join,
            "R_reserve": self.reserve_R,
            "R_fourth": self.fourth_R,
            "h_reserve": self.reserve_half_width,
            "h_fourth": self.fourth_half_width,
            "margin_R_reserve_minus_R_fourth": self.margin,
            "threshold_max_h": self.threshold,
            "invalidated": self.invalidated,
            "execution_order": list(self.execution_order),
            "membership": list(self.membership),
        }


def decide_ranking(
    candidates: Sequence[CandidateJoin],
    metrics: Mapping[str, JoinMetrics],
    intervals: Mapping[str, BootstrapInterval],
) -> RankingDecision:
    """Step 7: ``R_reserve - R_fourth > max(h_reserve, h_fourth)`` (line 42).

    Raises :class:`AbortRankingInvalidated` when the reserve out-scores the
    frozen fourth by more than the larger half-width. Otherwise returns the
    permitted execution reordering over UNCHANGED membership.
    """
    reserve = [c for c in candidates if c.is_reserve]
    fourth = [c for c in candidates if c.is_fourth]
    if len(reserve) != 1 or len(fourth) != 1:
        raise ScreenPreconditionRefusal(
            "the frozen candidate set must name exactly one reserve and one "
            f"rank-4 join (got {len(reserve)} and {len(fourth)})"
        )
    reserve_join, fourth_join = reserve[0].join_id, fourth[0].join_id
    missing = sorted(
        candidate.join_id
        for candidate in candidates
        if candidate.join_id not in metrics or candidate.join_id not in intervals
    )
    if missing:
        # Missing input refuses: a ranking decision over a partial screen is
        # not a decision.
        raise ScreenPreconditionRefusal(
            f"the ranking decision needs every candidate screened; missing {missing}"
        )
    reserve_R = float(metrics[reserve_join].R_measured)
    fourth_R = float(metrics[fourth_join].R_measured)
    h_reserve = float(intervals[reserve_join].half_width)
    h_fourth = float(intervals[fourth_join].half_width)
    margin = reserve_R - fourth_R
    threshold = max(h_reserve, h_fourth)
    membership = tuple(
        candidate.join_id for candidate in candidates if not candidate.is_reserve
    )
    if margin > threshold:
        raise AbortRankingInvalidated(
            f"reserve {reserve_join!r} R={reserve_R!r} exceeds fourth "
            f"{fourth_join!r} R={fourth_R!r} by {margin!r} > "
            f"max(h)={threshold!r}; WAVE-0 stops before CP2K and the join set "
            "may be changed only in a new preregistration"
        )
    order = sorted(
        (candidate for candidate in candidates if not candidate.is_reserve),
        key=lambda candidate: (
            -float(metrics[candidate.join_id].R_measured),
            candidate.expected_rank,
            candidate.join_id,
        ),
    )
    return RankingDecision(
        reserve_join=reserve_join,
        fourth_join=fourth_join,
        reserve_R=reserve_R,
        fourth_R=fourth_R,
        reserve_half_width=h_reserve,
        fourth_half_width=h_fourth,
        margin=margin,
        threshold=threshold,
        invalidated=False,
        execution_order=tuple(candidate.join_id for candidate in order),
        membership=membership,
    )


@dataclass
class JoinResult:
    """Per-join outcome; ``NOT_RUN`` until its screen phase completes."""

    candidate: CandidateJoin
    evidence_grade: int
    status: str = "NOT_RUN"
    metrics: JoinMetrics | None = None
    interval: BootstrapInterval | None = None
    gate: Mapping[str, Any] | None = None
    builds: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    restoration: Mapping[str, Any] | None = None
    responses: tuple[ChannelResponse, ...] = ()

    def artifact_sha256(self) -> dict[str, str | None]:
        return {
            key: (None if key not in self.builds else str(self.builds[key]["binary_sha256"]))
            for key in _BUILD_KEYS
        }


@dataclass
class ScreenRecord:
    """The complete immutable record of one W0-SENS attempt (section 9)."""

    run_id: str
    run_dir: str
    started_utc: str
    finished_utc: str
    production: bool
    corpus: EligibleCorpus
    joins: tuple[JoinResult, ...]
    pins: Mapping[str, Any]
    environment: Mapping[str, Any]
    ranking: RankingDecision | None = None
    abort_type: str | None = None
    abort_detail: str | None = None
    wall_seconds: float = 0.0

    @property
    def aborted(self) -> bool:
        return self.abort_type is not None

    @property
    def outcome(self) -> str:
        if self.aborted:
            return f"INCONCLUSIVE/REFUSED — {self.abort_type}"
        return "SCREEN COMPLETE — ranking decision recorded"

    def join(self, join_id: str) -> JoinResult:
        for result in self.joins:
            if result.candidate.join_id == join_id:
                return result
        raise KeyError(join_id)

    def public_aggregate(self) -> dict[str, Any]:
        """Step-6 restricted release. Refuses on a non-production screen."""
        if not self.production:
            raise ScreenPreconditionRefusal(
                "a non-production screen (injected seams or non-production "
                "build records) may never be released as a W0-SENS aggregate"
            )
        rows = []
        for result in self.joins:
            metrics = result.metrics
            rows.append(
                {
                    "row_id": result.candidate.row_id,
                    "C": None if metrics is None else metrics.C,
                    "S": None if metrics is None else metrics.S,
                    "I_measured": None if metrics is None else metrics.I_measured,
                    "R_measured": None if metrics is None else metrics.R_measured,
                    "typed_missing_cells": (
                        None if metrics is None else metrics.n_missing
                    ),
                    "artifact_sha256": result.artifact_sha256(),
                }
            )
        for row in rows:
            extra = sorted(set(row) - set(PUBLIC_AGGREGATE_FIELDS))
            if extra:
                raise ScreenPreconditionRefusal(
                    f"public aggregate carries non-releasable fields: {extra}"
                )
        return {
            "aggregate": "W0-SENS-PUBLIC-AGGREGATE",
            "screen_id": SCREEN_ID,
            "run_id": self.run_id,
            "abort_type": self.abort_type,
            "eligible_manifest_sha256": self.corpus.manifest_sha256,
            "rows": rows,
        }

    def custodian_lock(self) -> dict[str, Any]:
        """The step-2/step-6 signed custodian lock (quarantine-side)."""
        return {
            "lock": "W0-SENS-LOCK",
            "screen_id": SCREEN_ID,
            "run_id": self.run_id,
            "production": self.production,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "abort_type": self.abort_type,
            "abort_detail": self.abort_detail,
            "pins": dict(self.pins),
            "environment": dict(self.environment),
            "eligible_manifest_sha256": self.corpus.manifest_sha256,
            "eligible_row_count": len(self.corpus.points),
            "eligible_cluster_count": len(self.corpus.cluster_ids),
            "disclosures": [
                {
                    "id": disclosure.id,
                    "title": disclosure.title,
                    "text": disclosure.text,
                    "facts": dict(disclosure.facts),
                }
                for disclosure in self.corpus.disclosures
            ],
            "joins": [
                {
                    "row_id": result.candidate.row_id,
                    "join_id": result.candidate.join_id,
                    "param_name": result.candidate.param_name,
                    "evidence_grade": result.evidence_grade,
                    "status": result.status,
                    "synthetic_gate": (
                        None if result.gate is None else dict(result.gate)
                    ),
                    "builds": {
                        key: dict(value) for key, value in result.builds.items()
                    },
                    "restoration": (
                        None
                        if result.restoration is None
                        else dict(result.restoration)
                    ),
                    "metrics": (
                        None
                        if result.metrics is None
                        else {
                            "C": result.metrics.C,
                            "S": result.metrics.S,
                            "I_measured": result.metrics.I_measured,
                            "R_measured": result.metrics.R_measured,
                            "n_channels": result.metrics.n_channels,
                            "n_missing": result.metrics.n_missing,
                            "n_clusters": result.metrics.n_clusters,
                            "affected_channels": list(
                                result.metrics.affected_channels
                            ),
                        }
                    ),
                    # A-10: bounds are custodian-record only.
                    "bootstrap": (
                        None
                        if result.interval is None
                        else {
                            "seed": result.interval.seed,
                            "replicates": result.interval.replicates,
                            "lower": result.interval.lower,
                            "upper": result.interval.upper,
                            "half_width": result.interval.half_width,
                        }
                    ),
                }
                for result in self.joins
            ],
            "ranking": None if self.ranking is None else self.ranking.to_dict(),
        }

    def run_record(self) -> dict[str, Any]:
        """Section-9 immutable run record (A-11)."""
        return {
            "record": "W0-SENS-RUN-RECORD",
            "screen_id": SCREEN_ID,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "wall_seconds": self.wall_seconds,
            "production": self.production,
            "typed_failure_status": self.abort_type,
            "typed_failure_detail": self.abort_detail,
            "environment": dict(self.environment),
            "pins": dict(self.pins),
            "eligible_corpus": {
                "bench_set_path": self.corpus.bench_set_path,
                "bench_set_sha256": self.corpus.bench_set_sha256,
                "manifest_sha256": self.corpus.manifest_sha256,
                "eligible_rows": len(self.corpus.points),
                "eligible_clusters": len(self.corpus.cluster_ids),
                "na_bearing_clusters": len(self.corpus.na_bearing_cluster_ids()),
                "exclusions": [
                    {
                        "point_id": row.point_id,
                        "source": row.source,
                        "reason": row.reason,
                    }
                    for row in self.corpus.exclusions
                ],
            },
            "joins": [
                {
                    "row_id": result.candidate.row_id,
                    "status": result.status,
                    "builds": sorted(result.builds),
                }
                for result in self.joins
            ],
            "ranking": None if self.ranking is None else self.ranking.to_dict(),
        }


def _utc_now() -> str:
    return (
        _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(repo_root()), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            or "unknown"
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def pin_inputs(bench_set_path: Path) -> dict[str, Any]:
    """Step-1 SHA-256 pins, regenerated for THIS attempt (never carried).

    The instrument inventory is enumerated from disk, so a runner or worker
    module added after a previous attempt cannot be silently omitted.
    """
    root = repo_root()
    files: dict[str, str] = {}
    for relative in (PREREGISTRATION_REL, BENCH_HARNESS_REL):
        path = root / relative
        if not path.is_file():
            raise ScreenPreconditionRefusal(f"pinned input is missing: {path}")
        files[relative.as_posix()] = _sha256_file(path)
    files[str(bench_set_path)] = _sha256_file(bench_set_path)
    for path in sorted((root / INSTRUMENT_PACKAGE_REL).glob("*.py")):
        files[path.relative_to(root).as_posix()] = _sha256_file(path)
    return {
        "repository_sha": _repo_head(),
        "output_schema": {
            "csv_fields": list(CSV_FIELDS),
            "public_aggregate_fields": list(PUBLIC_AGGREGATE_FIELDS),
        },
        "files": files,
    }


def _environment() -> dict[str, Any]:
    return {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "pid": os.getpid(),
    }


def _default_mutator_factory(param_name: str, quarantine_dir: Path) -> W0WMutator:
    return W0WMutator(param_name=param_name, quarantine_dir=quarantine_dir)


@dataclass
class _Session:
    candidate: CandidateJoin
    mutator: W0WMutator
    callbacks: SyntheticGateCallbacks


class ScreenRunner:
    """Executes the frozen W0-SENS procedure end to end."""

    def __init__(
        self,
        *,
        corpus: EligibleCorpus,
        evidence_lock: EvidenceGradeLock,
        quarantine_root: Path | str,
        run_root: Path | str,
        evaluator: PatchedBuildEvaluator | None = None,
        mutator_factory: Callable[[str, Path], W0WMutator] | None = None,
        candidates: Sequence[CandidateJoin] = FROZEN_CANDIDATES,
        bench_set_path: Path | str | None = None,
    ) -> None:
        self.corpus = corpus
        self.evidence_lock = evidence_lock
        self.quarantine_root = Path(quarantine_root).resolve()
        self.run_root = Path(run_root)
        self.evaluator = evaluator if evaluator is not None else PatchedBuildEvaluator()
        self._mutator_factory = (
            mutator_factory if mutator_factory is not None else _default_mutator_factory
        )
        self.candidates = tuple(candidates)
        self.bench_set_path = (
            Path(bench_set_path)
            if bench_set_path is not None
            else Path(corpus.bench_set_path)
        )

    # -- preconditions ---------------------------------------------------

    def _validate_preconditions(self) -> None:
        if not self.corpus.points:
            raise ScreenPreconditionRefusal("the sealed eligible corpus is empty")
        if not self.candidates:
            raise ScreenPreconditionRefusal("no candidate join to screen")
        root = repo_root()
        if self.quarantine_root == root or root in self.quarantine_root.parents:
            raise ScreenPreconditionRefusal(
                "quarantine_root must resolve OUTSIDE the repository tree"
            )
        missing_grades = sorted(
            candidate.join_id
            for candidate in self.candidates
            if candidate.join_id not in self.evidence_lock.grades
        )
        if missing_grades:
            raise AbortEvidenceGrade(
                f"evidence-grade lock names no entry for {missing_grades}"
            )

    def _create_run_dir(self, run_id: str) -> Path:
        run_dir = self.run_root / run_id
        if run_dir.exists():
            raise ScreenPreconditionRefusal(
                f"run directory already exists and is immutable: {run_dir}"
            )
        run_dir.mkdir(parents=True)
        return run_dir

    # -- phases ----------------------------------------------------------

    def _session(self, candidate: CandidateJoin) -> _Session:
        # One quarantine directory per join: the pristine envelope path is a
        # fixed name inside it (w_mutator.py:631-632), so two parameters
        # sharing a directory would clobber each other's capture.
        quarantine = self.quarantine_root / candidate.join_id
        mutator = self._mutator_factory(candidate.param_name, quarantine)
        callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=self.evaluator)
        return _Session(candidate=candidate, mutator=mutator, callbacks=callbacks)

    @staticmethod
    def _build_record(build: MutationBuildRecord) -> dict[str, Any]:
        return {
            "record_type": type(build).__name__,
            "row_id": build.row_id,
            "param_name": build.param_name,
            "slot_index": build.slot_index,
            "perturbation_J": build.perturbation_J,
            "readback_J": build.readback_J,
            "changed_slots": list(build.changed_slots),
            "n_slots": build.n_slots,
            "prefix": build.prefix,
            "lock_path": build.lock_path,
            "binary_sha256": build.binary_sha256,
            "patched_source_sha256": build.patch.patched_source_sha256,
            "pristine_source_sha256": build.patch.pristine_source_sha256,
            "diff_sha256": build.patch.diff_sha256,
            "initializer_line": build.patch.line_number,
            "n_initializers": build.patch.n_initializers,
            "identity": {
                "melts_model": build.identity.melts_model,
                "engine_root": build.identity.engine_root,
                "engine_git_rev": build.identity.engine_git_rev,
                "makefile_sha256": build.identity.makefile_sha256,
                "module_sha256": build.identity.module_sha256,
                "pristine_dylib_sha256": dict(build.identity.pristine_dylib_sha256),
            },
        }

    def _run_gate(self, session: _Session) -> dict[str, Any]:
        """Step-2 mandatory signed synthetic gate for one parameter (A-6)."""
        session.callbacks.build_control()
        outcomes = synthetic_response_gate(
            session.mutator,
            session.callbacks.evaluate,
            session.callbacks.analytic,
            rel_tol=GATE_REL_TOL,
            abs_tol=GATE_ABS_TOL,
        )
        return {
            **session.callbacks.as_record(),
            "signed_responses": [
                {
                    "perturbation_J": value,
                    "measured_J_per_mol": measured,
                    "expected_J_per_mol": expected,
                }
                for value, measured, expected in outcomes
            ],
            "rel_tol": GATE_REL_TOL,
            "abs_tol": GATE_ABS_TOL,
        }

    def _screen_join(self, session: _Session, result: JoinResult) -> None:
        """Steps 3-5 and 7 for one candidate join."""
        candidate = session.candidate
        cells: dict[str, Any] = {}
        for key in _BUILD_KEYS:
            build = session.mutator.make_build(
                _BUILD_VALUES[key], row_id=candidate.row_id
            )
            result.builds[key] = self._build_record(build)
            cells[key] = self.evaluator.evaluate_corpus(
                build, row_id=candidate.row_id, points=self.corpus.points
            )
        responses = assemble_responses(
            self.corpus,
            control=cells["control"],
            plus=cells["plus"],
            minus=cells["minus"],
        )
        result.responses = responses
        result.metrics = compute_join_metrics(
            responses,
            evidence_grade=result.evidence_grade,
            na_anchor=candidate.na_anchor,
        )
        result.interval = bootstrap_R_interval(
            responses, evidence_grade=result.evidence_grade
        )
        result.status = "SCREENED"

    # -- driver ----------------------------------------------------------

    def run(self) -> ScreenRecord:
        """Run the frozen screen; ALWAYS returns a record, aborted or not.

        A typed abort is an OUTCOME of this procedure, not an exception the
        caller has to catch to discover: section 9 requires the immutable
        record of a failed or partial attempt to be written and never
        replaced by a successful-looking summary. The immutable run
        directory is therefore created FIRST — before any precondition is
        evaluated — so every abort path, precondition and evidence-grade
        refusals included, leaves evidence (A-11).
        """
        started = _utc_now()
        started_monotonic = time.monotonic()
        run_id = (
            "w0sens-"
            + _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        run_dir = self._create_run_dir(run_id)
        ranking: RankingDecision | None = None
        abort_type: str | None = None
        abort_detail: str | None = None
        # Every build minted this run, by record type. A production screen
        # mints ONLY MutationBuild: any injected mutator seam demotes the
        # whole session to NonProductionMutationBuild (w_mutator.py:579-584),
        # and an injected evaluator seam is demoting on its own.
        minted_types: set[str] = set()
        sessions: dict[str, _Session] = {}
        captured: list[str] = []
        restored: set[str] = set()
        results: list[JoinResult] = []
        by_id: dict[str, JoinResult] = {}
        pins: dict[str, Any] = {
            "repository_sha": _repo_head(),
            "output_schema": {
                "csv_fields": list(CSV_FIELDS),
                "public_aggregate_fields": list(PUBLIC_AGGREGATE_FIELDS),
            },
            "files": {},
        }
        try:
            pins = pin_inputs(self.bench_set_path)
            self._validate_preconditions()
            results = [
                JoinResult(
                    candidate=candidate,
                    evidence_grade=int(self.evidence_lock.grades[candidate.join_id]),
                )
                for candidate in self.candidates
            ]
            by_id = {result.candidate.join_id: result for result in results}
            sessions = {
                candidate.join_id: self._session(candidate)
                for candidate in self.candidates
            }
            for candidate in self.candidates:
                sessions[candidate.join_id].mutator.capture_pristine()
                captured.append(candidate.join_id)
            # Phase 1 — every candidate's signed synthetic gate, before any
            # candidate is screened (:37).
            for candidate in self.candidates:
                session = sessions[candidate.join_id]
                by_id[candidate.join_id].gate = self._run_gate(session)
                minted_types.add(type(session.callbacks.control_build).__name__)
            # Phase 2 — the screen itself.
            for candidate in self.candidates:
                self._screen_join(sessions[candidate.join_id], by_id[candidate.join_id])
        except W0SensAbort as exc:
            abort_type = getattr(exc, "abort_type", "ABORT-W0-SENS")
            abort_detail = str(exc)
        finally:
            # Phase 3 — exact restoration readback of the original build,
            # on EVERY exit path: a screen that aborts during the gates or
            # the screen phase has still touched the engine and must prove
            # the original custodian-held build reads back pristine before
            # the record is written.
            for candidate in self.candidates:
                join_id = candidate.join_id
                if join_id in restored or join_id not in captured:
                    continue
                try:
                    restoration = sessions[join_id].mutator.verify_restoration()
                except W0SensAbort as exc:
                    if abort_type is None:
                        abort_type = getattr(exc, "abort_type", "ABORT-W0-SENS")
                        abort_detail = str(exc)
                    else:
                        abort_detail = (
                            f"{abort_detail}; restoration verification for "
                            f"{join_id!r} also failed: {exc}"
                        )
                else:
                    restored.add(join_id)
                    by_id[join_id].restoration = {
                        "param_name": restoration.param_name,
                        "vector_matches_pristine": (
                            restoration.vector_matches_pristine
                        ),
                        "vector_sha256": restoration.vector_sha256,
                    }
        if abort_type is None:
            # Phase 4 — the ranking decision, after restoration.
            try:
                ranking = decide_ranking(
                    self.candidates,
                    {
                        join_id: result.metrics
                        for join_id, result in by_id.items()
                        if result.metrics is not None
                    },
                    {
                        join_id: result.interval
                        for join_id, result in by_id.items()
                        if result.interval is not None
                    },
                )
            except W0SensAbort as exc:
                abort_type = getattr(exc, "abort_type", "ABORT-W0-SENS")
                abort_detail = str(exc)
        for result in results:
            if result.status != "SCREENED":
                result.status = "NOT_RUN"
            minted_types.update(
                str(build_record["record_type"])
                for build_record in result.builds.values()
            )
        # Fail closed: an attempt that minted nothing is not a production
        # screen either — there is no live-verified build behind it.
        production = (
            self.evaluator.production
            and bool(minted_types)
            and minted_types == {MutationBuild.__name__}
        )
        record = ScreenRecord(
            run_id=run_id,
            run_dir=str(run_dir),
            started_utc=started,
            finished_utc=_utc_now(),
            production=production,
            corpus=self.corpus,
            joins=tuple(results),
            pins=pins,
            environment=_environment(),
            ranking=ranking,
            abort_type=abort_type,
            abort_detail=abort_detail,
            wall_seconds=time.monotonic() - started_monotonic,
        )
        write_artifacts(record, run_dir)
        return record


# -- emitters ------------------------------------------------------------


def render_csv(record: ScreenRecord) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(CSV_FIELDS), extrasaction="raise", lineterminator="\n"
    )
    writer.writeheader()
    for result in record.joins:
        metrics = result.metrics
        interval = result.interval
        artifacts = result.artifact_sha256()
        writer.writerow(
            {
                "expected_rank": result.candidate.expected_rank,
                "candidate_join": result.candidate.join_id,
                "evidence_grade": result.evidence_grade,
                "I_expected": result.candidate.I_expected,
                "R_expected": result.candidate.R_expected,
                "status": result.status,
                "abort_type": record.abort_type or "",
                "C": "" if metrics is None else metrics.C,
                "S": "" if metrics is None else f"{metrics.S:.10g}",
                "I_measured": "" if metrics is None else f"{metrics.I_measured:.10g}",
                "R_measured": "" if metrics is None else f"{metrics.R_measured:.10g}",
                "bootstrap_lower": "" if interval is None else f"{interval.lower:.10g}",
                "bootstrap_upper": "" if interval is None else f"{interval.upper:.10g}",
                "bootstrap_half_width": (
                    "" if interval is None else f"{interval.half_width:.10g}"
                ),
                "typed_missing_cells": "" if metrics is None else metrics.n_missing,
                "control_artifact_sha256": artifacts["control"] or "",
                "plus_artifact_sha256": artifacts["plus"] or "",
                "minus_artifact_sha256": artifacts["minus"] or "",
            }
        )
    return buffer.getvalue()


def render_markdown(record: ScreenRecord) -> str:
    lines: list[str] = [
        "# WAVE-0 frozen sensitivity screen",
        "",
        f"**Outcome:** `{record.outcome}`  ",
        f"**Run id:** `{record.run_id}`  ",
        f"**Run date (UTC):** {record.started_utc}  ",
        f"**Repository SHA:** `{record.pins['repository_sha']}`  ",
        f"**Record type:** {'PRODUCTION' if record.production else 'NON-PRODUCTION (not releasable)'}",
        "",
    ]
    if record.aborted:
        lines += [
            "## Typed abort",
            "",
            f"`{record.abort_type}`",
            "",
            f"{record.abort_detail}",
            "",
            "An abort is a refusal under this method version. It is not a "
            "failed value that may be dropped, replaced, or tuned in place.",
            "",
        ]
    lines += ["## Material evidence properties", ""]
    for index, disclosure in enumerate(record.corpus.disclosures, start=1):
        lines += [f"{index}. **{disclosure.title}.** {disclosure.text}", ""]

    lines += [
        "## Eligible-corpus audit",
        "",
        f"Bench set `{record.corpus.bench_set_path}`  ",
        f"SHA-256 `{record.corpus.bench_set_sha256}`  ",
        f"Sealed eligible manifest SHA-256 `{record.corpus.manifest_sha256}`",
        "",
        "| Source population | Eligible rows | `(source, composition)` series |",
        "|---|---:|---:|",
    ]
    sources: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for point in record.corpus.points:
        counts[point.source] = counts.get(point.source, 0) + 1
        sources.setdefault(point.source, set()).add(point.cluster_id)
    for source in sorted(counts):
        lines.append(f"| `{source}` | {counts[source]} | {len(sources[source])} |")
    lines += [
        f"| **Total** | **{len(record.corpus.points)}** | "
        f"**{len(record.corpus.cluster_ids)}** |",
        "",
        f"Na-bearing clusters: {len(record.corpus.na_bearing_cluster_ids())}. "
        f"Excluded bench-set rows: {len(record.corpus.exclusions)}.",
        "",
    ]
    if record.corpus.exclusions:
        reasons: dict[str, int] = {}
        for row in record.corpus.exclusions:
            reasons[row.reason] = reasons.get(row.reason, 0) + 1
        lines += ["| Excluded rows | Typed reason |", "|---:|---|"]
        for reason in sorted(reasons):
            lines.append(f"| {reasons[reason]} | {reason} |")
        lines.append("")

    lines += [
        "## Per-join result",
        "",
        "| Rank | Candidate join | E | `I_expected` | `R_expected` | Status | "
        "`C` | `S` | `I_measured` | `R_measured` | typed missing cells |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for result in record.joins:
        metrics = result.metrics
        lines.append(
            "| {rank} | `{join}` | {E} | {ie} | {re} | {status} | {C} | {S} | "
            "{I} | {R} | {missing} |".format(
                rank=result.candidate.expected_rank,
                join=result.candidate.join_id,
                E=result.evidence_grade,
                ie=result.candidate.I_expected,
                re=result.candidate.R_expected,
                status=result.status,
                C="—" if metrics is None else metrics.C,
                S="—" if metrics is None else f"{metrics.S:.4g}",
                I="—" if metrics is None else f"{metrics.I_measured:.4g}",
                R="—" if metrics is None else f"{metrics.R_measured:.4g}",
                missing="—" if metrics is None else metrics.n_missing,
            )
        )
    lines.append("")
    if record.ranking is not None:
        ranking = record.ranking
        lines += [
            "## Step-7 ranking decision",
            "",
            f"`R_reserve - R_fourth = {ranking.margin:.6g}`; "
            f"`max(h_reserve, h_fourth) = {ranking.threshold:.6g}`; "
            f"`ABORT-RANKING-INVALIDATED` "
            f"{'FIRED' if ranking.invalidated else 'not triggered'}.",
            "",
            "Membership is unchanged: "
            + ", ".join(f"`{join}`" for join in ranking.membership)
            + ".",
            "",
            "Permitted execution order (reordering only): "
            + " → ".join(f"`{join}`" for join in ranking.execution_order)
            + ".",
            "",
        ]
    lines += [
        "## Pinned inputs (step 1)",
        "",
        "| Input | SHA-256 |",
        "|---|---|",
    ]
    for name in sorted(record.pins["files"]):
        lines.append(f"| `{name}` | `{record.pins['files'][name]}` |")
    lines += [
        "",
        f"Wall time {record.wall_seconds:.1f} s on {record.environment['host']} "
        f"(python {record.environment['python']}).",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(record: ScreenRecord, run_dir: Path) -> dict[str, Path]:
    """Write the immutable run record (section 9) and the screen results."""
    run_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    payloads: list[tuple[str, str]] = [
        ("SCREEN-RESULTS.md", render_markdown(record)),
        ("SCREEN-RESULTS.csv", render_csv(record)),
        ("RUN-RECORD.json", canonical_json(record.run_record())),
        ("W0-SENS-LOCK.json", canonical_json(record.custodian_lock())),
    ]
    for name, text in payloads:
        path = run_dir / name
        path.write_text(text, encoding="utf-8")
        written[name] = path
    manifest_path = run_dir / "W0-SENS-ELIGIBLE-MANIFEST.json"
    manifest_path.write_bytes(record.corpus.manifest_bytes)
    written["W0-SENS-ELIGIBLE-MANIFEST.json"] = manifest_path
    if record.production:
        aggregate = run_dir / "W0-SENS-PUBLIC-AGGREGATE.json"
        aggregate.write_text(canonical_json(record.public_aggregate()), encoding="utf-8")
        written["W0-SENS-PUBLIC-AGGREGATE.json"] = aggregate
    return written


def write_pre_runner_abort_record(
    run_root: Path | str, exc: W0SensAbort, *, bench_set_path: Path | str
) -> Path:
    """Section-9 immutable record for an abort fired before a runner exists.

    CLI corpus reading and evidence-lock parsing run before
    :class:`ScreenRunner` construction; without this record an
    ``ABORT-EVIDENCE-GRADE`` from lock parsing would escape with no
    immutable abort record at all. The record is deliberately minimal —
    no corpus, no joins, no ranking — and can never masquerade as a
    frozen outcome.
    """
    run_id = (
        "w0sens-"
        + _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    run_dir = Path(run_root) / run_id
    if run_dir.exists():
        raise ScreenPreconditionRefusal(
            f"run directory already exists and is immutable: {run_dir}"
        )
    run_dir.mkdir(parents=True)
    abort_type = getattr(exc, "abort_type", "ABORT-W0-SENS")
    started = _utc_now()
    try:
        pins = pin_inputs(Path(bench_set_path))
    except W0SensAbort as pin_exc:
        pins = {
            "repository_sha": _repo_head(),
            "files": {},
            "pin_error": str(pin_exc),
        }
    record = {
        "record": "W0-SENS-RUN-RECORD",
        "screen_id": SCREEN_ID,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "started_utc": started,
        "finished_utc": _utc_now(),
        "production": False,
        "abort_stage": "pre-runner input parsing",
        "typed_failure_status": abort_type,
        "typed_failure_detail": str(exc),
        "environment": _environment(),
        "pins": pins,
    }
    (run_dir / "RUN-RECORD.json").write_text(
        canonical_json(record), encoding="utf-8"
    )
    (run_dir / "SCREEN-RESULTS.md").write_text(
        "\n".join(
            [
                "# WAVE-0 frozen sensitivity screen",
                "",
                f"**Outcome:** `INCONCLUSIVE/REFUSED — {abort_type}`  ",
                f"**Run id:** `{run_id}`  ",
                f"**Run date (UTC):** {started}  ",
                f"**Repository SHA:** `{pins.get('repository_sha', 'unknown')}`  ",
                "**Record type:** NON-PRODUCTION (not releasable)",
                "",
                "## Typed abort",
                "",
                f"`{abort_type}`",
                "",
                f"{exc}",
                "",
                "The abort fired while parsing the screen inputs, before a "
                "runner could be constructed. An abort is a refusal under "
                "this method version. It is not a failed value that may be "
                "dropped, replaced, or tuned in place.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return run_dir


# -- CLI -----------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.w0_sens.screen",
        description="Run the frozen WAVE-0 W0-SENS sensitivity screen.",
    )
    parser.add_argument(
        "--bench-set",
        default=str(repo_root() / DEFAULT_BENCH_SET_REL),
        help="tracked melt-activity bench set (step-1 pinned input)",
    )
    parser.add_argument(
        "--evidence-grade-lock",
        required=True,
        help="sealed EVIDENCE-GRADE-LOCK.json (preregistration line 20)",
    )
    parser.add_argument(
        "--quarantine-root",
        required=True,
        help="WQ-1 quarantine root; must resolve OUTSIDE the repository tree",
    )
    parser.add_argument(
        "--run-root",
        default=str(
            repo_root()
            / "docs-private/research/2026-08-09-upstream-mission/melts-backtest/runs"
        ),
        help="parent of the immutable runs/<run-id>/ directory",
    )
    args = parser.parse_args(argv)

    try:
        corpus = read_eligible_corpus(Path(args.bench_set))
        lock_path = Path(args.evidence_grade_lock)
        if not lock_path.is_file():
            raise ScreenPreconditionRefusal(
                f"evidence-grade lock is not readable: {lock_path}"
            )
        evidence_lock = EvidenceGradeLock.from_bytes(
            lock_path.read_bytes(),
            required_joins=[candidate.join_id for candidate in FROZEN_CANDIDATES],
        )
    except W0SensAbort as exc:
        # A-11: even an abort fired before runner construction leaves the
        # section-9 immutable record.
        run_dir = write_pre_runner_abort_record(
            args.run_root, exc, bench_set_path=args.bench_set
        )
        print(f"run_dir: {run_dir}")
        print(
            "outcome: INCONCLUSIVE/REFUSED — "
            f"{getattr(exc, 'abort_type', 'ABORT-W0-SENS')}"
        )
        return 2
    runner = ScreenRunner(
        corpus=corpus,
        evidence_lock=evidence_lock,
        quarantine_root=args.quarantine_root,
        run_root=args.run_root,
    )
    record = runner.run()
    print(f"run_dir: {record.run_dir}")
    print(f"outcome: {record.outcome}")
    return 2 if record.aborted else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
