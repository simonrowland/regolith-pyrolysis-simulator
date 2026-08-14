"""W0-SENS driver — frozen sensitivity computation over mutation-build responses.

Frozen by PREREGISTRATION-wave0.md steps 4-7 (lines 39-42). This module is
the computation half of the instrument: it consumes per-channel build
responses (control ``0 J`` and the two signed ``+/-10,000 J`` perturbations,
each either a positive finite activity or a typed-missing cell) and computes

- ``delta_c = log10(y_perturbed) - log10(y_control)`` per channel and sign
  (step 4; the endmember-to-oxide reference term cancels exactly, so the
  response is target-free),
- the affected-channel test: a channel is affected when its larger
  signed-magnitude shift across the two perturbations is ``>= 0.05 dex``,
- ``C`` (affected count), ``S`` (median affected shift in units of
  ``0.1 dex``), ``I_measured = C*S``, ``R_measured = E*I_measured``, with
  the frozen ``C=0`` convention ``S = I_measured = R_measured = 0`` rather
  than the median of an empty set (step 5),
- the rank-1 Na-anchor gate: at least two eligible Na-bearing source
  clusters — every row PROVEN by sealed-manifest membership
  (:class:`NaBearingProof` against a :class:`SealedNaManifest`, whose
  SHA-256 is recomputed from the exact sealed bytes and whose row
  membership is checked per row, so no unproved row can contribute to
  ``C``); a row without proof refuses (missing input refuses; repo
  fail-closed category 1) — at least one nonmissing channel per cluster,
  and ``C > 0``, else the typed ``ABORT-RANKING-INSTRUMENT-NULL``
  (step 5),
- the fixed 10,000-replicate cluster bootstrap (``numpy.random.PCG64``,
  seed ``649013``) resampling whole ``(source, composition)`` series and
  returning the percentile 2.5-97.5% interval of ``R_measured`` with
  ``h = (U - L) / 2`` (step 7). The frozen seed and replicate count are NOT
  parameters of the production entry point; only the explicitly labelled
  non-production entry point accepts overrides, and its result type cannot
  be mistaken for a released interval.

Missing/refused model CELLS remain typed missing and are never imputed
zero (step 4): every absent cell (control or either sign) must carry a
typed reason at construction, and released missing counts are cell
counts, so a missing sign always counts (step 6 "typed missing counts").
A channel with a typed-missing sign is MISSING, not affected (step 5
measures the shift "across the two perturbations"; AMBIGUITY A-16 in
``benchmarks/w0_sens/screen.py``). This module never runs the screen,
never reads a quarantined W value, and
never produces a ranking: the step-7 reserve-versus-fourth
``ABORT-RANKING-INVALIDATED`` comparison is a ranking decision and is
deliberately not implemented here.

Reading note: step 5 states ``I_measured = C*S`` and ``R_measured =
E*I_measured`` explicitly for "all other joins"; the same arithmetic is
applied to the rank-1 join once its additional gate passes, since the gate
text defines only extra preconditions, not a different formula.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from benchmarks.w0_sens import W0SensAbort


AFFECTED_THRESHOLD_DEX = 0.05
# Inclusive-boundary noise floor, in dex. The log10 evaluation of a
# nominal 0.05 dex shift lands a few ulps BELOW the float 0.05
# (``math.log10(10.0**0.05) == 0.04999999999999996``), so a strict ``>=``
# against the frozen constant would EXCLUDE a shift sitting exactly on the
# frozen bar, which step 5 ("at least 0.05 dex") counts as affected. The
# comparison is therefore made in dex space against the exact frozen
# constant plus this ulp-scale floor — never by round-tripping through a
# ``10**0.05`` ratio, whose own float error is what broke the boundary.
#
# The tolerance is ULP-BOUNDED, not an absolute epsilon. An earlier fix used an
# absolute 1e-12 dex floor; a closing pass showed that is ~144,000 ulps of 0.05,
# so it admitted genuinely sub-threshold shifts (0.05 - 5e-13 and 0.05 - 9e-13
# both classified as affected). That is the opposite error from the original
# defect: the original excluded the exact boundary, the over-wide floor included
# values below it.
#
# The slack is sized from a measurement, not chosen by feel. Sweeping the
# 10**x -> log10 round trip over 20k points either side of the bar gives a
# worst-case error of 6.0 ulps of 0.05 (the exact-0.05 case is itself 6.0 ulps
# low, landing on 0.04999999999999996). The nearest margin that must still be
# REJECTED is 5e-13 dex, which is 7.2e4 ulps below the bar. Any slack between
# ~8 and ~1e4 ulps therefore separates them; 16 gives 2.7x headroom over the
# measured error while staying ~4500x below the smallest real margin, so it
# cannot reclassify a physically distinguishable shift.
_DEX_BOUNDARY_ULP_SLACK = 16
_DEX_BOUNDARY_NOISE_FLOOR_DEX = _DEX_BOUNDARY_ULP_SLACK * math.ulp(AFFECTED_THRESHOLD_DEX)
S_SHIFT_UNIT_DEX = 0.1
BOOTSTRAP_SEED = 649013
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_PERCENTILES = (2.5, 97.5)

_NA_OXIDE = "Na2O"
_SI_OXIDE = "SiO2"

_CELL_NAMES = ("y_control", "y_plus", "y_minus")


class AbortRankingInstrumentNull(W0SensAbort):
    """Frozen typed abort for step 5: the rank-1 Na instrument is null."""

    abort_type = "ABORT-RANKING-INSTRUMENT-NULL"


@dataclass(frozen=True)
class NaManifestRow:
    """One eligible row of the sealed Na-bearing manifest.

    ``na2o_wt_pct`` / ``sio2_wt_pct`` come from the sealed manifest bytes
    themselves — never from the caller — so a forged composition cannot be
    spliced onto a real row identity.
    """

    source: str
    composition_id: str
    channel: str
    na2o_wt_pct: float
    sio2_wt_pct: float


@dataclass(frozen=True)
class SealedNaManifest:
    """A validated ``W0-SENS-ELIGIBLE-MANIFEST`` (prereg step 4).

    Constructed ONLY via :meth:`from_bytes`, which recomputes the SHA-256
    over the exact sealed bytes and validates the row schema and integrity
    (no duplicate row identities; one consistent Na2O/SiO2 composition per
    ``(source, composition_id)``). Row membership is then checked against
    the sealed rows — a proof cannot cite a row the sealed manifest does
    not contain.
    """

    sha256: str
    rows: tuple[NaManifestRow, ...]

    @classmethod
    def from_bytes(cls, raw: bytes) -> SealedNaManifest:
        try:
            text = raw.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"sealed Na manifest is not valid JSON: {exc!r}") from exc
        if not isinstance(document, dict) or not isinstance(
            document.get("rows"), list
        ):
            raise ValueError("sealed Na manifest must be an object with a rows list")
        rows: list[NaManifestRow] = []
        seen: set[tuple[str, str, str]] = set()
        compositions: dict[tuple[str, str], tuple[float, float]] = {}
        for index, entry in enumerate(document["rows"]):
            if not isinstance(entry, dict):
                raise ValueError(f"sealed Na manifest row {index} is not an object")
            try:
                source = str(entry["source"])
                composition_id = str(entry["composition_id"])
                channel = str(entry["channel"])
                na2o = float(entry["na2o_wt_pct"])
                sio2 = float(entry["sio2_wt_pct"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"sealed Na manifest row {index} lacks source, "
                    f"composition_id, channel, na2o_wt_pct, sio2_wt_pct: {exc!r}"
                ) from exc
            if not (source.strip() and composition_id.strip() and channel.strip()):
                raise ValueError(
                    f"sealed Na manifest row {index} carries an empty identity"
                )
            for label, value in ((_NA_OXIDE, na2o), (_SI_OXIDE, sio2)):
                if not (math.isfinite(value) and value >= 0.0):
                    raise ValueError(
                        f"sealed Na manifest row {index} {label} wt% must be "
                        f"finite >= 0: {value!r}"
                    )
            key = (source, composition_id, channel)
            if key in seen:
                raise ValueError(
                    f"sealed Na manifest carries a duplicate row identity: {key!r}"
                )
            seen.add(key)
            composition_key = (source, composition_id)
            composition = (na2o, sio2)
            prior = compositions.setdefault(composition_key, composition)
            if prior != composition:
                raise ValueError(
                    f"sealed Na manifest carries contradictory Na2O/SiO2 for "
                    f"{composition_key!r}: {prior!r} vs {composition!r}"
                )
            rows.append(
                NaManifestRow(
                    source=source,
                    composition_id=composition_id,
                    channel=channel,
                    na2o_wt_pct=na2o,
                    sio2_wt_pct=sio2,
                )
            )
        if not rows:
            raise ValueError("sealed Na manifest carries no rows")
        return cls(
            sha256=hashlib.sha256(raw).hexdigest(),
            rows=tuple(rows),
        )

    def row(
        self, source: str, composition_id: str, channel: str
    ) -> NaManifestRow | None:
        """The sealed row with this exact identity, or ``None``."""
        for entry in self.rows:
            if (
                entry.source == source
                and entry.composition_id == composition_id
                and entry.channel == channel
            ):
                return entry
        return None


@dataclass(frozen=True)
class NaBearingProof:
    """Sealed-manifest membership proof for one Na-bearing corpus row.

    Prereg step 4: the custodian writes the exact eligible measured
    Na-bearing ``(source, composition, T, species, type)`` rows and source
    hashes to ``W0-SENS-ELIGIBLE-MANIFEST.json`` and seals that manifest.
    A row counts as Na-bearing ONLY when it carries this proof — exact
    ``(source, composition_id, channel)`` membership in a
    :class:`SealedNaManifest` whose hash was recomputed from the sealed
    bytes, with nonzero Na and SiO2 read FROM the sealed row. Absent or
    unverifiable proof refuses at construction (missing input refuses;
    repo fail-closed category 1); a bare manifest string is not a proof.
    """

    manifest: SealedNaManifest
    source: str
    composition_id: str
    channel: str

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SealedNaManifest):
            raise TypeError(
                "NaBearingProof requires a SealedNaManifest, not "
                f"{type(self.manifest).__name__}: a caller-supplied string "
                "or hash is not a sealed manifest"
            )
        if self.manifest.row(self.source, self.composition_id, self.channel) is None:
            raise ValueError(
                "NaBearingProof cites a row outside the sealed manifest: "
                f"{self.source!r}::{self.composition_id!r} {self.channel!r}"
            )

    @property
    def manifest_sha256(self) -> str:
        return self.manifest.sha256

    @property
    def is_na_bearing(self) -> bool:
        row = self.manifest.row(self.source, self.composition_id, self.channel)
        return row is not None and row.na2o_wt_pct > 0.0 and row.sio2_wt_pct > 0.0


@dataclass(frozen=True)
class ChannelResponse:
    """One eligible channel's responses under the three mutation builds.

    ``y_control`` / ``y_plus`` / ``y_minus`` are the evaluated channel
    values under the ``0 J`` control and the two signed perturbations. A
    ``None`` cell is a typed missing cell and MUST carry a typed reason:
    either the whole-channel ``missing`` reason (all three cells absent) or
    a per-cell entry in ``missing_cells`` mapping the cell name
    (``y_control`` / ``y_plus`` / ``y_minus``) to its reason. A non-None
    cell must be positive and finite (the ``log10`` domain). A bare ``None``
    cell with no typed reason is a construction error, not a silent skip.
    ``missing_cells`` is DEEP-FROZEN at construction (copied into an
    immutable mapping): the caller's mapping is never retained, so mutating
    it after construction cannot change typed missing cells or released
    missing counts. ``na_proof`` must cite THIS row: its
    ``(source, composition_id, channel)`` identity must equal the
    response's own, so a proof minted for one row cannot be spliced onto
    another.
    """

    source: str
    composition_id: str
    temperature_K: float
    channel: str
    y_control: float | None
    y_plus: float | None
    y_minus: float | None
    missing: str | None = None
    missing_cells: Mapping[str, str] | None = None
    na_proof: NaBearingProof | None = None

    def __post_init__(self) -> None:
        if self.missing_cells is not None:
            object.__setattr__(
                self, "missing_cells", MappingProxyType(dict(self.missing_cells))
            )
        if self.na_proof is not None and (
            self.na_proof.source != self.source
            or self.na_proof.composition_id != self.composition_id
            or self.na_proof.channel != self.channel
        ):
            raise ValueError(
                "na_proof identity does not match the response row: "
                f"{self.cluster_id} {self.channel} vs proof "
                f"{self.na_proof.source!r}::{self.na_proof.composition_id!r} "
                f"{self.na_proof.channel!r}"
            )
        cells = {
            "y_control": self.y_control,
            "y_plus": self.y_plus,
            "y_minus": self.y_minus,
        }
        reasons = dict(self.missing_cells or {})
        unknown = set(reasons) - set(_CELL_NAMES)
        if unknown:
            raise ValueError(
                f"missing_cells names unknown cells {sorted(unknown)}: "
                f"{self.cluster_id} {self.channel}"
            )
        if any(not str(reason).strip() for reason in reasons.values()):
            raise ValueError(
                f"missing_cells carries an empty typed reason: "
                f"{self.cluster_id} {self.channel}"
            )
        if self.missing is not None:
            if any(cell is not None for cell in cells.values()):
                raise ValueError(
                    "typed-missing channel carries a response cell: "
                    f"{self.cluster_id} {self.channel}"
                )
            if reasons:
                raise ValueError(
                    "channel-level typed missing must not be mixed with "
                    f"per-cell reasons: {self.cluster_id} {self.channel}"
                )
        else:
            absent = {name for name, cell in cells.items() if cell is None}
            if absent != set(reasons):
                raise ValueError(
                    "every absent cell must carry a typed missing reason "
                    "and every reason an absent cell: "
                    f"{self.cluster_id} {self.channel} absent={sorted(absent)} "
                    f"reasons={sorted(reasons)}"
                )
        for name, cell in cells.items():
            if cell is not None and not (math.isfinite(cell) and cell > 0.0):
                raise ValueError(
                    "channel response outside the log10 domain (must be "
                    f"positive finite): {self.cluster_id} {self.channel} "
                    f"cell={name} value={cell!r}"
                )

    @property
    def cluster_id(self) -> str:
        return f"{self.source}::{self.composition_id}"

    def typed_missing_cells(self) -> tuple[tuple[str, str], ...]:
        """``(cell, typed reason)`` for every missing cell (cell-level)."""
        if self.missing is not None:
            return tuple((name, self.missing) for name in _CELL_NAMES)
        reasons = dict(self.missing_cells or {})
        return tuple((name, reasons[name]) for name in _CELL_NAMES if name in reasons)


@dataclass(frozen=True)
class ChannelShift:
    """Per-channel signed shifts, typed missing signs, and the verdict."""

    cluster_id: str
    channel: str
    delta_plus: float | None
    delta_minus: float | None
    shift_dex: float
    affected: bool
    missing_signs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class JoinMetrics:
    """Step-5 join aggregates (custodian-side record).

    ``n_missing`` counts typed missing CELLS (control/plus/minus), per the
    frozen cell-level wording: a wholly refused channel contributes three,
    a single missing sign contributes one.
    """

    evidence_grade: int
    C: int
    S: float
    I_measured: float
    R_measured: float
    n_channels: int
    n_missing: int
    n_clusters: int
    affected_channels: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapInterval:
    """Step-7 percentile interval of ``R_measured`` (released form)."""

    seed: int
    replicates: int
    lower: float
    upper: float
    half_width: float


@dataclass(frozen=True)
class NonProductionBootstrapInterval:
    """NON-PRODUCTION calibration interval — never a released W0-SENS result.

    Returned only by :func:`bootstrap_R_interval_NONPRODUCTION` for tests
    and calibration harnesses that need a fast override of the frozen
    step-7 settings. It is a distinct type from :class:`BootstrapInterval`
    so no released-result pipeline (which consumes only the frozen
    production record) can accept it.
    """

    seed: int
    replicates: int
    lower: float
    upper: float
    half_width: float


def delta_c(y_perturbed: float, y_control: float) -> float:
    """``log10(y_perturbed) - log10(y_control)`` (step 4)."""
    return math.log10(y_perturbed) - math.log10(y_control)


def channel_shift(response: ChannelResponse) -> ChannelShift | None:
    """Shift record for one channel, or ``None`` when uncomputable.

    A channel is computable only when its control cell AND BOTH signed
    perturbation cells are present: step 5 measures the larger
    signed-magnitude shift "across the two perturbations", so a channel
    with one typed-missing sign is MISSING, not affected (AMBIGUITY A-16 —
    one signed response is never silently sufficient). Its typed missing
    cells still count toward released typed missing counts and are never
    imputed zero.
    """
    if response.missing is not None or response.y_control is None:
        return None
    if response.y_plus is None or response.y_minus is None:
        return None
    delta_plus = delta_c(response.y_plus, response.y_control)
    delta_minus = delta_c(response.y_minus, response.y_control)
    shift = max(abs(delta_plus), abs(delta_minus))
    return ChannelShift(
        cluster_id=response.cluster_id,
        channel=response.channel,
        delta_plus=delta_plus,
        delta_minus=delta_minus,
        shift_dex=shift,
        affected=shift >= AFFECTED_THRESHOLD_DEX
        or math.isclose(
            shift,
            AFFECTED_THRESHOLD_DEX,
            rel_tol=0.0,
            abs_tol=_DEX_BOUNDARY_NOISE_FLOOR_DEX,
        ),
    )


def _metrics_from_shifts(
    shifts: Sequence[ChannelShift],
    *,
    evidence_grade: int,
    n_channels: int,
    n_missing: int,
    n_clusters: int,
) -> JoinMetrics:
    affected = tuple(shift for shift in shifts if shift.affected)
    C = len(affected)
    if C == 0:
        # Frozen C=0 convention: zeros, never the median of an empty set.
        S = I_measured = R_measured = 0.0
    else:
        S = (
            statistics.median(shift.shift_dex for shift in affected)
            / S_SHIFT_UNIT_DEX
        )
        I_measured = C * S
        R_measured = evidence_grade * I_measured
    return JoinMetrics(
        evidence_grade=evidence_grade,
        C=C,
        S=S,
        I_measured=I_measured,
        R_measured=R_measured,
        n_channels=n_channels,
        n_missing=n_missing,
        n_clusters=n_clusters,
        affected_channels=tuple(
            f"{shift.cluster_id}|{shift.channel}" for shift in affected
        ),
    )


def compute_join_metrics(
    responses: Sequence[ChannelResponse],
    *,
    evidence_grade: int,
    na_anchor: bool = False,
) -> JoinMetrics:
    """Step-5 ``C`` / ``S`` / ``I_measured`` / ``R_measured`` for one join.

    With ``na_anchor=True`` the rank-1 Na gate is enforced first: EVERY row
    must carry a sealed-manifest membership proof (exact
    ``(source, composition_id, channel)`` membership in one consistent
    :class:`SealedNaManifest`, so every row contributing to ``C`` is
    proved, not just one member per cluster), at least two Na-bearing
    ``(source, composition)`` clusters, at least one nonmissing channel
    per cluster, and ``C > 0``; any failure raises the typed
    ``ABORT-RANKING-INSTRUMENT-NULL`` instead of returning metrics.
    """
    E = int(evidence_grade)
    if E < 1:
        raise ValueError(f"evidence grade {evidence_grade!r} is not eligible")
    rows = list(responses)
    clusters: dict[str, list[ChannelResponse]] = {}
    for row in rows:
        clusters.setdefault(row.cluster_id, []).append(row)
    shifts = [channel_shift(row) for row in rows]
    computable = [shift for shift in shifts if shift is not None]
    n_missing = sum(len(row.typed_missing_cells()) for row in rows)

    if na_anchor:
        # Fail-closed: EVERY row must carry sealed-manifest proof. A row
        # without proof refuses rather than defaulting to either bearing
        # or non-bearing, and no unproved row can contribute to C.
        unproved = sorted(
            {row.cluster_id for row in rows if row.na_proof is None}
        )
        if unproved:
            raise AbortRankingInstrumentNull(
                "rank-1 Na join rows without sealed-manifest proof: "
                f"{unproved}"
            )
        manifest_ids = {
            str(row.na_proof.manifest_sha256)
            for row in rows
            if row.na_proof is not None
        }
        if len(manifest_ids) != 1:
            raise AbortRankingInstrumentNull(
                "rank-1 Na join rows cite more than one sealed manifest: "
                f"{sorted(manifest_ids)}"
            )
        bearing = sorted(
            cluster_id
            for cluster_id, members in clusters.items()
            if all(
                member.na_proof is not None and member.na_proof.is_na_bearing
                for member in members
            )
        )
        if len(bearing) < 2:
            raise AbortRankingInstrumentNull(
                "rank-1 Na join has fewer than two eligible Na-bearing "
                f"source clusters with sealed-manifest proof ({len(bearing)})"
            )
        # AMBIGUITY A-9, RESOLVED: "at least one nonmissing channel per
        # cluster" ranges over the two-or-more QUALIFYING Na-bearing anchor
        # clusters, NOT over every cluster in the sealed corpus. See
        # ADJUDICATION-A9.md (blind-adjudicated and recorded BEFORE the first
        # re-run, precisely so this could not be decided after seeing an
        # abort). This runner previously shipped the strict corpus-wide
        # reading as a fail-closed default while the question was open; that
        # default is now superseded by the ruling, not relaxed by a result.
        #
        # Consequence, stated in the ruling: a high-Na cluster that returns no
        # channel does NOT abort. Its absent cells stay typed and are counted
        # in the released missing counts (``n_missing`` below), so a dark
        # cluster remains VISIBLE to an auditor rather than vanishing.
        nonmissing_by_cluster = {
            cluster_id: sum(
                1 for member in members if channel_shift(member) is not None
            )
            for cluster_id, members in clusters.items()
        }
        anchors = [
            cluster_id
            for cluster_id in bearing
            if nonmissing_by_cluster.get(cluster_id, 0) > 0
        ]
        # Item 4 requires the anchors be "independent" clusters, which
        # ADJUDICATION-A9 reads as independent BY SOURCE. Two compositions
        # from one study are one measurement tradition, not two, so they
        # cannot both count toward the pair. This is STRICTER than counting
        # clusters and can only make the refusal more likely -- it is
        # implemented here because it is the faithful reading, not because of
        # any outcome.
        anchor_sources = {
            cluster_id.split("::", 1)[0] for cluster_id in anchors
        }
        if len(anchor_sources) < 2:
            raise AbortRankingInstrumentNull(
                "rank-1 Na join has fewer than two independent Na-bearing "
                "SOURCES carrying a nonmissing channel (A-9 reading A): "
                f"anchors={sorted(anchors)} sources={sorted(anchor_sources)}"
            )
    metrics = _metrics_from_shifts(
        computable,
        evidence_grade=E,
        n_channels=len(rows),
        n_missing=n_missing,
        n_clusters=len(clusters),
    )
    if na_anchor and metrics.C == 0:
        raise AbortRankingInstrumentNull(
            "rank-1 Na join measured C=0: no eligible channel shifted by "
            f">= {AFFECTED_THRESHOLD_DEX} dex"
        )
    return metrics


def _bootstrap_interval_core(
    responses: Sequence[ChannelResponse],
    *,
    evidence_grade: int,
    seed: int,
    replicates: int,
) -> tuple[float, float, float]:
    E = int(evidence_grade)
    if E < 1:
        raise ValueError(f"evidence grade {evidence_grade!r} is not eligible")
    clusters: dict[str, list[ChannelResponse]] = {}
    for row in responses:
        clusters.setdefault(row.cluster_id, []).append(row)
    keys = list(clusters)
    if not keys:
        raise ValueError("bootstrap requires at least one cluster")
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    r_values = np.empty(int(replicates), dtype=float)
    for replicate in range(int(replicates)):
        draw = rng.integers(0, len(keys), size=len(keys))
        resampled_rows = [
            row for index in draw for row in clusters[keys[index]]
        ]
        resampled_shifts = [channel_shift(row) for row in resampled_rows]
        computable = [shift for shift in resampled_shifts if shift is not None]
        r_values[replicate] = _metrics_from_shifts(
            computable,
            evidence_grade=E,
            n_channels=len(resampled_rows),
            n_missing=sum(
                len(row.typed_missing_cells()) for row in resampled_rows
            ),
            n_clusters=len(draw),
        ).R_measured
    lower, upper = np.percentile(r_values, BOOTSTRAP_PERCENTILES)
    return float(lower), float(upper), float(upper - lower) / 2.0


def bootstrap_R_interval(
    responses: Sequence[ChannelResponse],
    *,
    evidence_grade: int,
) -> BootstrapInterval:
    """Step-7 cluster bootstrap interval of ``R_measured`` — FROZEN settings.

    Resamples whole ``(source, composition)`` series with replacement,
    retaining all their temperatures/species together (typed-missing cells
    ride along and stay missing inside each replicate), recomputes
    ``R_measured`` per replicate, and returns the percentile 2.5-97.5%
    interval with ``h = (U - L) / 2``. The seed (``649013``), replicate
    count (``10,000``), generator (``numpy.random.PCG64``), and percentiles
    are frozen by the preregistration and are deliberately NOT parameters
    of this production entry point. A resampled ``C=0`` replicate is a
    legitimate ``R=0`` under the frozen convention, so the Na-anchor gate
    is a measured-corpus precondition and is not re-applied per replicate.
    """
    lower, upper, half_width = _bootstrap_interval_core(
        responses,
        evidence_grade=evidence_grade,
        seed=BOOTSTRAP_SEED,
        replicates=BOOTSTRAP_REPLICATES,
    )
    return BootstrapInterval(
        seed=BOOTSTRAP_SEED,
        replicates=BOOTSTRAP_REPLICATES,
        lower=lower,
        upper=upper,
        half_width=half_width,
    )


def bootstrap_R_interval_NONPRODUCTION(
    responses: Sequence[ChannelResponse],
    *,
    evidence_grade: int,
    seed: int,
    replicates: int,
) -> NonProductionBootstrapInterval:
    """NON-PRODUCTION bootstrap with caller-set seed/replicate count.

    Exists so tests and calibration harnesses can run a fast bootstrap.
    Its result is a :class:`NonProductionBootstrapInterval` — a different
    type from the released :class:`BootstrapInterval` — and values from it
    may never be released as a W0-SENS result: the prereg freezes the
    production interval at PCG64 seed ``649013`` with ``10,000``
    replicates, which only :func:`bootstrap_R_interval` applies.
    """
    lower, upper, half_width = _bootstrap_interval_core(
        responses,
        evidence_grade=evidence_grade,
        seed=seed,
        replicates=replicates,
    )
    return NonProductionBootstrapInterval(
        seed=int(seed),
        replicates=int(replicates),
        lower=lower,
        upper=upper,
        half_width=half_width,
    )
