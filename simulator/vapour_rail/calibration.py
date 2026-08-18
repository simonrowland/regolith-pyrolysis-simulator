"""VR-10 warm calibration runner library and progressive-validation reports.

DESIGN-REV5 §5.2–5.5 / DECOMPOSITION VR-10:

* Executes VapoRock **only** through the VR-5 warm pool (no result/calibration
  cache, no cold in-process path for the calibration runner).
* Corpus is a fixed simple-melt grid over 1350–1950 K with held-out
  formulation / T / fO2 cells.
* Sub-floor provider values are **censored interval** evidence
  (``0 < P <= P_floor``); never ``log10(0)``, never floor-as-point, never a
  fitted physical zero.
* Analytical families and parameter caps are frozen before fit; a changed
  family needs a new calibration ID and review.
* Raw cells / residuals live in a SQLite **research store**. Runtime data
  may only load the reviewed sidecar ``data/vapour_rail_calibration.yaml``.
  Runtime code paths never open the SQLite store.

Golden-neutral / offline: this module does not flip flux authority and does
not write production catalog rows. Validated promotions remain an R-epoch
concern (VR-13+).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

import yaml

from simulator.fe_redox import feo_iw_log10_fO2_bar
from simulator.melt_backend.melt_envelope import (
    MELT_EXTRAPOLATION_ENVELOPE_FIELDS,
    consume_melt_extrapolation_envelope,
    has_melt_extrapolation_envelope,
    melt_extrapolation_diagnostic,
)
from simulator.melt_backend.vaporock import (
    VAPOROCK_T_MAX_K,
    VAPOROCK_T_MIN_K,
    VapoRockBackend,
    vaporock_speciation_is_live,
)
from simulator.vapour_rail.trace_acquisition import list_pending_validation

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIDECAR_PATH: Final[Path] = (
    _REPO_ROOT / "data" / "vapour_rail_calibration.yaml"
)
DEFAULT_RESEARCH_STORE_DIR: Final[Path] = (
    _REPO_ROOT / "docs-private" / "research" / "vapour-rail-calibration"
)

# Design §5.2 initial grid.
DEFAULT_T_MIN_K: Final[float] = VAPOROCK_T_MIN_K  # 1350
DEFAULT_T_MAX_K: Final[float] = VAPOROCK_T_MAX_K  # 1950
DEFAULT_T_STEP_K: Final[float] = 50.0
DEFAULT_FO2_DELTAS_DEX: Final[tuple[float, ...]] = (-1.0, 0.0, 1.0)
# Explicit user-story slice (1300 °C) is already on the 50 K grid at 1573.15
# only if we include it; design names 1573.15 K so we force it into the grid.
USER_STORY_T_K: Final[float] = 1573.15

# Provider underflow floor (Pa). Values at or below this are censored intervals.
DEFAULT_P_FLOOR_PA: Final[float] = 1.0e-30

# Owner-approved relative flux error budget (HKL: |Δlog10 J| = |Δlog10 P|).
# Stored as metadata; not a magic runtime threshold.
DEFAULT_EPSILON_J: Final[float] = 0.30  # 30 % relative flux tolerance
_MELT_MODEL_ID: Final[str] = "MELTS-v1.0"

SIDECAR_KIND: Final[str] = "vapour_rail_calibration"
SIDECAR_SCHEMA_VERSION: Final[int] = 1

# ---------------------------------------------------------------------------
# Fixed analytical families + parameter caps (DESIGN-REV5 §5.2–5.3)
# ---------------------------------------------------------------------------


class AnalyticalFamilyId(str, Enum):
    """Reaction-physics family for the declared initial VapoRock-calibrated form."""

    OXIDE_REDUCTION_HALF = "oxide_reduction_half_o2"  # SiO, Fe, Mg: n_pO2 = -1/2
    ALKALI_QUARTER = "alkali_quarter_o2"  # Na, K: n_pO2 = -1/4
    MONATOMIC_OXYGEN = "monatomic_oxygen_half"  # O: n_pO2 = +1/2
    MOLECULAR_OXYGEN = "molecular_oxygen_unit"  # O2: n_pO2 = +1


@dataclass(frozen=True)
class AnalyticalFamilySpec:
    """Frozen family: basis functions + hard parameter cap.

    A fit may not add terms after failure. Changing the family requires a new
    calibration ID and written physical cause (DESIGN-REV5 §5.3).
    """

    family_id: AnalyticalFamilyId
    species: str
    pO2_exponent: float
    coefficient_names: tuple[str, ...]
    max_parameters: int
    parent_oxide: str | None
    activity_exponent: float
    notes: str

    def __post_init__(self) -> None:
        if self.max_parameters < 1:
            raise ValueError(f"{self.species}: max_parameters must be >= 1")
        if len(self.coefficient_names) != self.max_parameters:
            raise ValueError(
                f"{self.species}: coefficient_names length must equal "
                f"max_parameters={self.max_parameters}"
            )


# Declared initial analytical forms from the 2026-07-31 probe slopes
# (DESIGN-REV5 §5.2 table). Temperature term is log-linear in 1/T with optional
# Antoine C (three coefficients max); pO2 and activity exponents are fixed
# physics, not free fit parameters.
FROZEN_ANALYTICAL_FAMILIES: Final[dict[str, AnalyticalFamilySpec]] = {
    "SiO": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.OXIDE_REDUCTION_HALF,
        species="SiO",
        pO2_exponent=-0.5,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide="SiO2",
        activity_exponent=1.0,
        notes="SiO2(l) -> SiO(g) + 1/2 O2(g); d log10 P / d log10 fO2 = -1/2",
    ),
    "Fe": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.OXIDE_REDUCTION_HALF,
        species="Fe",
        pO2_exponent=-0.5,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide="FeO",
        activity_exponent=1.0,
        notes="FeO(l) -> Fe(g) + 1/2 O2(g); d log10 P / d log10 fO2 = -1/2",
    ),
    "Mg": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.OXIDE_REDUCTION_HALF,
        species="Mg",
        pO2_exponent=-0.5,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide="MgO",
        activity_exponent=1.0,
        notes="MgO(l) -> Mg(g) + 1/2 O2(g); d log10 P / d log10 fO2 = -1/2",
    ),
    "Na": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.ALKALI_QUARTER,
        species="Na",
        pO2_exponent=-0.25,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide="Na2O",
        activity_exponent=0.5,
        notes="1/2 Na2O -> Na(g) + 1/4 O2; d log10 P / d log10 fO2 = -1/4",
    ),
    "K": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.ALKALI_QUARTER,
        species="K",
        pO2_exponent=-0.25,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide="K2O",
        activity_exponent=0.5,
        notes="1/2 K2O -> K(g) + 1/4 O2; d log10 P / d log10 fO2 = -1/4",
    ),
    "O": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.MONATOMIC_OXYGEN,
        species="O",
        pO2_exponent=0.5,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide=None,
        activity_exponent=0.0,
        notes="1/2 O2(g) ⇌ O(g); d log10 P / d log10 fO2 = +1/2",
    ),
    "O2": AnalyticalFamilySpec(
        family_id=AnalyticalFamilyId.MOLECULAR_OXYGEN,
        species="O2",
        pO2_exponent=1.0,
        coefficient_names=("A", "B", "C"),
        max_parameters=3,
        parent_oxide=None,
        activity_exponent=0.0,
        notes="O2(g) partial tracks fO2; d log10 P / d log10 fO2 = +1",
    ),
}

DEFAULT_CALIBRATION_SPECIES: Final[tuple[str, ...]] = tuple(
    FROZEN_ANALYTICAL_FAMILIES.keys()
)


# ---------------------------------------------------------------------------
# Independent external anchors (not VapoRock model-model agreement)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndependentAnchor:
    """One external ground-truth anchor required before validated flip."""

    anchor_id: str
    species: str
    kind: str  # janaf_nist | kems | langmuir | paper_wolfe | pure_component
    citation: str
    temperature_K: float | None
    notes: str
    may_certify: bool = False

    def __post_init__(self) -> None:
        if self.may_certify:
            raise ValueError(
                f"anchor {self.anchor_id!r} must not certify "
                "(O1 ceiling / diagnostic-only until R epoch)"
            )


# Scaffold anchors: real literature pointers that already exist in-repo.
# A validated flip still requires the full progressive-validation ladder;
# these records only satisfy the "independent anchor identified" gate.
DEFAULT_INDEPENDENT_ANCHORS: Final[tuple[IndependentAnchor, ...]] = (
    IndependentAnchor(
        anchor_id="sio_soffiatti_wolfe_class",
        species="SiO",
        kind="paper_wolfe",
        citation=(
            "Sossi et al. / Wolfe-class vacuum evaporation envelopes; "
            "corpus §25 SiO residual tests"
        ),
        temperature_K=1873.0,
        notes="Independent of VapoRock; used as holdout residual envelope",
    ),
    IndependentAnchor(
        anchor_id="na_nist_webbook_pure",
        species="Na",
        kind="pure_component",
        citation="NIST Chemistry WebBook SRD 69 Rodebush & Walters 1930 sodium",
        temperature_K=None,
        notes="Pure-component Antoine; activity path is separate",
    ),
    IndependentAnchor(
        anchor_id="fe_kems_regime",
        species="Fe",
        kind="kems",
        citation="data/literature/vapour_rail_kems_anchors.yaml (Fe rows)",
        temperature_K=None,
        notes="KEMS regime only; not averaged with Langmuir",
    ),
    IndependentAnchor(
        anchor_id="mg_langmuir_regime",
        species="Mg",
        kind="langmuir",
        citation="data/literature/vapour_rail_langmuir_anchors.yaml (Mg rows)",
        temperature_K=None,
        notes="Langmuir free-evaporation regime; distinct from KEMS",
    ),
    IndependentAnchor(
        anchor_id="k_nist_webbook_pure",
        species="K",
        kind="pure_component",
        citation="NIST Chemistry WebBook potassium pure-component rail",
        temperature_K=None,
        notes="Independent pure-component anchor",
    ),
    IndependentAnchor(
        anchor_id="o_janaf_half_o2",
        species="O",
        kind="janaf_nist",
        citation="JANAF / NASA CEA 1/2 O2 ⇌ O equilibrium constant",
        temperature_K=None,
        notes="Thermodynamic K(T); not a VapoRock fit",
    ),
    IndependentAnchor(
        anchor_id="o2_buffer_identity",
        species="O2",
        kind="janaf_nist",
        citation="fO2 identity / buffer definition",
        temperature_K=None,
        notes="O2 partial tracks the imposed fO2 buffer by definition",
    ),
)


# ---------------------------------------------------------------------------
# Corpus: formulations + state grid + holdouts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Formulation:
    """One fixed simple-melt composition (oxide mol basis, unnormalized OK)."""

    formulation_id: str
    family: str  # leave-one-family-out key
    composition_mol: Mapping[str, float]
    role: str  # binary | ternary | integration
    loadings_note: str


def _binary(oxide: str, mol_frac_oxide: float) -> dict[str, float]:
    """SiO2–M_xO_y binary at the given oxide mole fraction (rest SiO2)."""

    if not 0.0 < mol_frac_oxide < 1.0:
        raise ValueError("binary oxide mole fraction must be in (0, 1)")
    return {"SiO2": 1.0 - mol_frac_oxide, oxide: mol_frac_oxide}


def default_simple_melt_corpus() -> tuple[Formulation, ...]:
    """DESIGN-REV5 §5.2 simple SiO2–M_xO_y binaries + FeO ternary + mare slice.

    Two or three parent-oxide loadings per binary family, one FeO ternary,
    and a small multicomponent lunar-mare integration set.
    """

    rows: list[Formulation] = []
    binary_specs: tuple[tuple[str, str, tuple[float, ...]], ...] = (
        ("SiO2-MgO", "MgO", (0.10, 0.25, 0.40)),
        ("SiO2-FeO", "FeO", (0.10, 0.25, 0.40)),
        ("SiO2-CaO", "CaO", (0.10, 0.25)),
        ("SiO2-Al2O3", "Al2O3", (0.10, 0.20)),
        ("SiO2-Na2O", "Na2O", (0.05, 0.15)),
        ("SiO2-K2O", "K2O", (0.05, 0.10)),
    )
    for family, oxide, loadings in binary_specs:
        for loading in loadings:
            pct = int(round(loading * 100))
            rows.append(
                Formulation(
                    formulation_id=f"{family}@{pct}mol%",
                    family=family,
                    composition_mol=_binary(oxide, loading),
                    role="binary",
                    loadings_note=f"{oxide} mole fraction {loading}",
                )
            )

    # One FeO ternary: SiO2–MgO–FeO.
    rows.append(
        Formulation(
            formulation_id="SiO2-MgO-FeO@60-25-15",
            family="SiO2-MgO-FeO",
            composition_mol={"SiO2": 0.60, "MgO": 0.25, "FeO": 0.15},
            role="ternary",
            loadings_note="mare-like major oxides, FeO-bearing",
        )
    )

    # Lunar mare low-Ti simplified integration (mol, major oxides only).
    rows.append(
        Formulation(
            formulation_id="lunar_mare_low_ti_simplified",
            family="lunar_mare_integration",
            composition_mol={
                "SiO2": 0.48,
                "Al2O3": 0.09,
                "FeO": 0.16,
                "MgO": 0.12,
                "CaO": 0.12,
                "TiO2": 0.02,
                "Na2O": 0.005,
                "K2O": 0.001,
            },
            role="integration",
            loadings_note="simplified mare low-Ti major-oxide slice",
        )
    )
    return tuple(rows)


def temperature_grid_K(
    t_min: float = DEFAULT_T_MIN_K,
    t_max: float = DEFAULT_T_MAX_K,
    t_step: float = DEFAULT_T_STEP_K,
    *,
    include_user_story_slice: bool = True,
) -> tuple[float, ...]:
    """Inclusive temperature grid in Kelvin with forced 1573.15 K slice."""

    if t_step <= 0.0:
        raise ValueError("t_step must be positive")
    if t_min > t_max:
        raise ValueError("t_min must be <= t_max")
    if t_min < VAPOROCK_T_MIN_K or t_max > VAPOROCK_T_MAX_K:
        raise ValueError(
            f"temperature grid must lie inside VapoRock domain "
            f"[{VAPOROCK_T_MIN_K:g}, {VAPOROCK_T_MAX_K:g}] K"
        )
    count = int(round((t_max - t_min) / t_step))
    values = [round(t_min + i * t_step, 10) for i in range(count + 1)]
    if include_user_story_slice and USER_STORY_T_K not in values:
        if VAPOROCK_T_MIN_K <= USER_STORY_T_K <= VAPOROCK_T_MAX_K:
            values.append(USER_STORY_T_K)
            values.sort()
    return tuple(values)


def iw_log_fO2(temperature_K: float) -> float:
    """Kress91/Holzheid pure-FeO IW log10(fO2/bar) at a_FeO = 1."""

    return float(feo_iw_log10_fO2_bar(float(temperature_K), a_feo=1.0))


def fo2_grid_for_temperature(
    temperature_K: float,
    deltas_dex: Sequence[float] = DEFAULT_FO2_DELTAS_DEX,
) -> tuple[tuple[str, float], ...]:
    """Return ``(label, log10_fO2)`` for IW and ±dex offsets."""

    base = iw_log_fO2(temperature_K)
    out: list[tuple[str, float]] = []
    for delta in deltas_dex:
        if abs(delta) < 1e-15:
            label = "IW"
        elif delta > 0:
            label = f"IW+{delta:g}"
        else:
            label = f"IW{delta:g}"
        out.append((label, base + float(delta)))
    return tuple(out)


class HoldoutSplit(str, Enum):
    TRAIN = "train"
    HOLDOUT_FORMULATION = "holdout_formulation"
    HOLDOUT_T = "holdout_T"
    HOLDOUT_FO2 = "holdout_fO2"


@dataclass(frozen=True)
class HoldoutPlan:
    """Leave-one-formulation-family-out plus held-out T and fO2 cells."""

    held_out_formulation_family: str
    held_out_temperatures_K: tuple[float, ...]
    held_out_fo2_labels: tuple[str, ...]

    def assign(
        self,
        *,
        formulation_family: str,
        temperature_K: float,
        fo2_label: str,
    ) -> HoldoutSplit:
        if formulation_family == self.held_out_formulation_family:
            return HoldoutSplit.HOLDOUT_FORMULATION
        if any(
            math.isclose(temperature_K, t, rel_tol=0.0, abs_tol=1e-9)
            for t in self.held_out_temperatures_K
        ):
            return HoldoutSplit.HOLDOUT_T
        if fo2_label in self.held_out_fo2_labels:
            return HoldoutSplit.HOLDOUT_FO2
        return HoldoutSplit.TRAIN


def default_holdout_plan(
    temperatures_K: Sequence[float] | None = None,
) -> HoldoutPlan:
    """Default progressive-validation splits.

    Holds out one complete formulation family (SiO2–Na2O binaries), the
    mid-domain temperature 1650 K, and the oxidizing IW+1 fO2 cell.
    """

    temps = list(temperatures_K) if temperatures_K is not None else list(
        temperature_grid_K()
    )
    # Prefer exact 1650 K; otherwise the median grid point.
    preferred = 1650.0
    held_t = preferred if any(
        math.isclose(t, preferred, rel_tol=0.0, abs_tol=1e-9) for t in temps
    ) else temps[len(temps) // 2]
    return HoldoutPlan(
        held_out_formulation_family="SiO2-Na2O",
        held_out_temperatures_K=(float(held_t),),
        held_out_fo2_labels=("IW+1",),
    )


@dataclass(frozen=True)
class CalibrationCell:
    """One (formulation, T, fO2) evaluation request."""

    cell_id: str
    formulation: Formulation
    temperature_K: float
    fo2_label: str
    fO2_log: float
    split: HoldoutSplit


def build_calibration_cells(
    formulations: Sequence[Formulation] | None = None,
    *,
    temperatures_K: Sequence[float] | None = None,
    fo2_deltas_dex: Sequence[float] = DEFAULT_FO2_DELTAS_DEX,
    holdout: HoldoutPlan | None = None,
) -> tuple[CalibrationCell, ...]:
    """Cartesian product of corpus × T × fO2 with holdout labels."""

    forms = tuple(formulations or default_simple_melt_corpus())
    temps = tuple(temperatures_K or temperature_grid_K())
    plan = holdout or default_holdout_plan(temps)
    cells: list[CalibrationCell] = []
    for form in forms:
        for t_k in temps:
            for fo2_label, fO2_log in fo2_grid_for_temperature(
                t_k, fo2_deltas_dex
            ):
                split = plan.assign(
                    formulation_family=form.family,
                    temperature_K=float(t_k),
                    fo2_label=fo2_label,
                )
                cell_id = (
                    f"{form.formulation_id}|T={t_k:g}|fO2={fo2_label}"
                )
                cells.append(
                    CalibrationCell(
                        cell_id=cell_id,
                        formulation=form,
                        temperature_K=float(t_k),
                        fo2_label=fo2_label,
                        fO2_log=float(fO2_log),
                        split=split,
                    )
                )
    return tuple(cells)


# ---------------------------------------------------------------------------
# Censored observations
# ---------------------------------------------------------------------------


class ObservationKind(str, Enum):
    POINT = "point"
    CENSORED_SUB_FLOOR = "censored_sub_floor"
    REFUSED = "refused"
    MISSING_SPECIES = "missing_species"


@dataclass(frozen=True)
class SpeciesObservation:
    """One species pressure observation at a calibration cell."""

    species: str
    kind: ObservationKind
    pressure_Pa: float | None
    p_floor_Pa: float
    log10_pressure_Pa: float | None  # None when censored or refused
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "species": self.species,
            "kind": self.kind.value,
            "pressure_Pa": self.pressure_Pa,
            "p_floor_Pa": self.p_floor_Pa,
            "log10_pressure_Pa": self.log10_pressure_Pa,
            "note": self.note,
        }


def censor_pressure(
    pressure_Pa: float | None,
    *,
    p_floor_Pa: float = DEFAULT_P_FLOOR_PA,
    species: str = "",
) -> SpeciesObservation:
    """Map a raw provider pressure onto point or censored interval evidence.

    Forbidden (DESIGN-REV5 §5.3):

    * ``log10(0)``
    * replacing a sub-floor value with the floor as a point observation
    * treating underflow as a fitted physical zero
    """

    floor = float(p_floor_Pa)
    if floor <= 0.0 or not math.isfinite(floor):
        raise ValueError("p_floor_Pa must be finite and positive")

    if pressure_Pa is None:
        return SpeciesObservation(
            species=species,
            kind=ObservationKind.MISSING_SPECIES,
            pressure_Pa=None,
            p_floor_Pa=floor,
            log10_pressure_Pa=None,
            note="species absent from provider result",
        )

    try:
        p = float(pressure_Pa)
    except (TypeError, ValueError):
        return SpeciesObservation(
            species=species,
            kind=ObservationKind.MISSING_SPECIES,
            pressure_Pa=None,
            p_floor_Pa=floor,
            log10_pressure_Pa=None,
            note=f"non-numeric pressure {pressure_Pa!r}",
        )

    if not math.isfinite(p) or p <= 0.0 or p <= floor:
        # Censored interval: 0 < P <= P_floor. Do not emit log10.
        return SpeciesObservation(
            species=species,
            kind=ObservationKind.CENSORED_SUB_FLOOR,
            pressure_Pa=None,
            p_floor_Pa=floor,
            log10_pressure_Pa=None,
            note="0 < P <= P_floor (censored interval; not a point)",
        )

    return SpeciesObservation(
        species=species,
        kind=ObservationKind.POINT,
        pressure_Pa=p,
        p_floor_Pa=floor,
        log10_pressure_Pa=math.log10(p),
        note="",
    )


# ---------------------------------------------------------------------------
# Error budget (HKL linearity)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownstreamErrorBudget:
    """Propagated pressure residual → flux/product error budget metadata.

    Premise (HKL, fixed alpha and T): ``J ∝ P`` so
    ``|Δlog10 J| = |Δlog10 P|``.

    For relative flux tolerance ``ε_J``, the pointwise pressure threshold is
    ``log10(1 + ε_J)``.

    Sanity: ε_J → 0 ⇒ threshold → 0; ε_J = 1 (100 %) ⇒ threshold = log10(2).
    Units: dimensionless relative flux; threshold in dex (log10 decades).
    """

    epsilon_J: float
    log10_pressure_threshold_dex: float
    algebra: str
    units: str
    limiting_check: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_error_budget(epsilon_J: float = DEFAULT_EPSILON_J) -> DownstreamErrorBudget:
    """Derive the pointwise pressure residual threshold from ε_J."""

    eps = float(epsilon_J)
    if not math.isfinite(eps) or eps < 0.0:
        raise ValueError("epsilon_J must be finite and non-negative")
    threshold = math.log10(1.0 + eps) if eps > 0.0 else 0.0
    return DownstreamErrorBudget(
        epsilon_J=eps,
        log10_pressure_threshold_dex=threshold,
        algebra=(
            "HKL at fixed alpha,T: J ∝ P ⇒ |Δlog10 J| = |Δlog10 P|; "
            "pointwise threshold = log10(1 + ε_J)"
        ),
        units="ε_J dimensionless relative flux; threshold in log10 dex",
        limiting_check=(
            "ε_J→0 ⇒ threshold→0; ε_J=1 ⇒ threshold=log10(2)≈0.3010 dex"
        ),
    )


# ---------------------------------------------------------------------------
# Boundary statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryStatistic:
    """Signed/absolute Δlog10(P) on a validated-domain boundary face."""

    species: str
    boundary: str  # e.g. T_min | T_max | fO2_min | fO2_max | formulation_edge
    channel: str
    delta_log10_P: float | None
    abs_delta_log10_P: float | None
    source_before: str
    source_after: str
    admissible: bool | None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def boundary_faces_for_domain(
    *,
    t_min: float = DEFAULT_T_MIN_K,
    t_max: float = DEFAULT_T_MAX_K,
    fo2_labels: Sequence[str] = ("IW-1", "IW", "IW+1"),
) -> tuple[dict[str, Any], ...]:
    """Enumerate domain boundary faces for boundary-continuity checks."""

    return (
        {"boundary": "T_min", "temperature_K": float(t_min), "fo2_label": "IW"},
        {"boundary": "T_max", "temperature_K": float(t_max), "fo2_label": "IW"},
        {
            "boundary": "fO2_min",
            "temperature_K": 1650.0,
            "fo2_label": fo2_labels[0],
        },
        {
            "boundary": "fO2_max",
            "temperature_K": 1650.0,
            "fo2_label": fo2_labels[-1],
        },
        {
            "boundary": "user_story_1300C",
            "temperature_K": USER_STORY_T_K,
            "fo2_label": "IW",
        },
    )


def evaluate_boundary_jumps(
    *,
    species: str,
    interior_log10_P: float | None,
    boundary_log10_P: float | None,
    boundary: str,
    error_budget: DownstreamErrorBudget,
    source_before: str = "vaporock_warm",
    source_after: str = "analytical_rail",
) -> BoundaryStatistic:
    """Signed/absolute Δlog10(P) and admissibility vs error budget."""

    if interior_log10_P is None or boundary_log10_P is None:
        return BoundaryStatistic(
            species=species,
            boundary=boundary,
            channel=species,
            delta_log10_P=None,
            abs_delta_log10_P=None,
            source_before=source_before,
            source_after=source_after,
            admissible=None,
            note="missing interior or boundary observation",
        )
    delta = float(boundary_log10_P) - float(interior_log10_P)
    abs_delta = abs(delta)
    admissible = abs_delta <= float(error_budget.log10_pressure_threshold_dex)
    return BoundaryStatistic(
        species=species,
        boundary=boundary,
        channel=species,
        delta_log10_P=delta,
        abs_delta_log10_P=abs_delta,
        source_before=source_before,
        source_after=source_after,
        admissible=admissible,
        note="" if admissible else "exceeds error-budget boundary step",
    )


# ---------------------------------------------------------------------------
# Progressive-validation report
# ---------------------------------------------------------------------------


class RowValidationState(str, Enum):
    PENDING = "pending_validation"
    VALIDATED = "validated"


@dataclass
class PerRowValidationState:
    species: str
    family_id: str
    validation_status: RowValidationState
    parameter_cap: int
    coefficient_names: tuple[str, ...]
    independent_anchor_ids: tuple[str, ...]
    may_flip_validated: bool
    flip_blockers: tuple[str, ...]
    surface: str = "calibration_candidate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "species": self.species,
            "family_id": self.family_id,
            "validation_status": self.validation_status.value,
            "parameter_cap": self.parameter_cap,
            "coefficient_names": list(self.coefficient_names),
            "independent_anchor_ids": list(self.independent_anchor_ids),
            "may_flip_validated": self.may_flip_validated,
            "flip_blockers": list(self.flip_blockers),
            "surface": self.surface,
        }


@dataclass
class SourceSelectionFractions:
    """Realized-state coverage: selectable vs refused vs source class."""

    n_cells: int
    n_ok: int
    n_refused: int
    fraction_selectable: float
    fraction_refused: float
    fraction_vaporock_covered: float
    fraction_literature_rail: float
    fraction_pending_validation: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProgressiveValidationReport:
    """Full progressive-validation report required by VR-10 acceptance."""

    calibration_id: str
    domain: dict[str, Any]
    frozen_families: dict[str, Any]
    holdout: dict[str, Any]
    per_row_state: list[dict[str, Any]]
    remaining_pending: list[dict[str, Any]]
    source_selection_fractions: dict[str, Any]
    error_budget: dict[str, Any]
    boundary_statistics: list[dict[str, Any]]
    cell_counts: dict[str, int]
    notes: list[str] = field(default_factory=list)
    authority: str = "diagnostic_only"
    certifies: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "calibration_id": self.calibration_id,
            "authority": self.authority,
            "certifies": self.certifies,
            "domain": self.domain,
            "frozen_families": self.frozen_families,
            "holdout": self.holdout,
            "per_row_state": self.per_row_state,
            "remaining_pending": self.remaining_pending,
            "source_selection_fractions": self.source_selection_fractions,
            "error_budget": self.error_budget,
            "boundary_statistics": self.boundary_statistics,
            "cell_counts": self.cell_counts,
            "notes": list(self.notes),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)


def _anchors_by_species(
    anchors: Sequence[IndependentAnchor] = DEFAULT_INDEPENDENT_ANCHORS,
) -> dict[str, list[IndependentAnchor]]:
    out: dict[str, list[IndependentAnchor]] = {}
    for anchor in anchors:
        out.setdefault(anchor.species, []).append(anchor)
    return out


def build_per_row_states(
    *,
    species: Sequence[str] = DEFAULT_CALIBRATION_SPECIES,
    anchors: Sequence[IndependentAnchor] = DEFAULT_INDEPENDENT_ANCHORS,
    holdout_accepted: Mapping[str, bool] | None = None,
    boundary_accepted: Mapping[str, bool] | None = None,
    error_budget_accepted: Mapping[str, bool] | None = None,
    promoted_validated: Mapping[str, bool] | None = None,
) -> list[PerRowValidationState]:
    """Per-species pending/validated state with explicit flip blockers.

    A row may flip to ``validated`` only when all ladder gates pass
    (DESIGN-REV5 §5.3). This builder never auto-promotes: absent an explicit
    ``promoted_validated`` flag the status stays pending.
    """

    by_species = _anchors_by_species(anchors)
    holdout_accepted = dict(holdout_accepted or {})
    boundary_accepted = dict(boundary_accepted or {})
    error_budget_accepted = dict(error_budget_accepted or {})
    promoted_validated = dict(promoted_validated or {})
    rows: list[PerRowValidationState] = []
    for sid in species:
        spec = FROZEN_ANALYTICAL_FAMILIES[sid]
        species_anchors = by_species.get(sid, [])
        blockers: list[str] = []
        if not species_anchors:
            blockers.append("missing_independent_anchor")
        if not holdout_accepted.get(sid, False):
            blockers.append("holdout_validation_incomplete")
        if not boundary_accepted.get(sid, False):
            blockers.append("boundary_statistics_incomplete")
        if not error_budget_accepted.get(sid, False):
            blockers.append("error_budget_incomplete")
        if any(a.may_certify for a in species_anchors):
            blockers.append("anchor_claims_certify")  # defensive

        may_flip = not blockers
        # Explicit promotion only — progressive ladder is reviewed, not auto.
        is_validated = bool(promoted_validated.get(sid, False)) and may_flip
        if is_validated:
            status = RowValidationState.VALIDATED
        else:
            status = RowValidationState.PENDING
            if promoted_validated.get(sid, False) and blockers:
                blockers.append("promotion_blocked_by_ladder")

        rows.append(
            PerRowValidationState(
                species=sid,
                family_id=spec.family_id.value,
                validation_status=status,
                parameter_cap=spec.max_parameters,
                coefficient_names=spec.coefficient_names,
                independent_anchor_ids=tuple(a.anchor_id for a in species_anchors),
                may_flip_validated=may_flip,
                flip_blockers=tuple(blockers),
            )
        )
    return rows


def compute_source_selection_fractions(
    *,
    n_cells: int,
    n_ok: int,
    n_refused: int,
    n_species_pending: int,
    n_species_total: int,
    n_vaporock_species_covered: int,
    n_literature_species: int,
) -> SourceSelectionFractions:
    """Aggregate selectable/refused fractions for the progressive report."""

    if n_cells < 0 or n_ok < 0 or n_refused < 0:
        raise ValueError("cell counts must be non-negative")
    if n_cells == 0:
        frac_sel = 0.0
        frac_ref = 0.0
    else:
        frac_sel = n_ok / n_cells
        frac_ref = n_refused / n_cells
    n_species_total = max(int(n_species_total), 1)
    return SourceSelectionFractions(
        n_cells=int(n_cells),
        n_ok=int(n_ok),
        n_refused=int(n_refused),
        fraction_selectable=frac_sel,
        fraction_refused=frac_ref,
        fraction_vaporock_covered=n_vaporock_species_covered / n_species_total,
        fraction_literature_rail=n_literature_species / n_species_total,
        fraction_pending_validation=n_species_pending / n_species_total,
    )


def build_progressive_validation_report(
    *,
    calibration_id: str,
    cells: Sequence[CalibrationCell],
    cell_results: Sequence[Mapping[str, Any]] | None = None,
    boundary_statistics: Sequence[BoundaryStatistic] | None = None,
    epsilon_J: float = DEFAULT_EPSILON_J,
    include_rail_pending: bool = True,
    holdout: HoldoutPlan | None = None,
    promoted_validated: Mapping[str, bool] | None = None,
) -> ProgressiveValidationReport:
    """Assemble the VR-10 progressive-validation report."""

    cell_results = list(cell_results or [])
    n_ok = sum(1 for r in cell_results if r.get("status") == "ok")
    n_refused = sum(
        1
        for r in cell_results
        if r.get("status") in {"out_of_domain", "refused", "unavailable", "not_converged"}
    )
    # When no live results yet, counts still describe the planned corpus.
    n_cells = len(cell_results) if cell_results else len(cells)

    per_row = build_per_row_states(promoted_validated=promoted_validated)
    remaining_from_calibration = [
        row.as_dict()
        for row in per_row
        if row.validation_status is RowValidationState.PENDING
    ]

    remaining_pending: list[dict[str, Any]] = list(remaining_from_calibration)
    if include_rail_pending:
        try:
            rail_pending = list_pending_validation()
        except Exception as exc:  # noqa: BLE001 - report must still build
            remaining_pending.append(
                {
                    "surface": "trace_acquisition",
                    "error": f"list_pending_validation failed: {exc}",
                }
            )
        else:
            # Cap noise in the calibration report: record count + sample.
            remaining_pending.append(
                {
                    "surface": "rail_pending_set_summary",
                    "n_pending": len(rail_pending),
                    "sample_ids": [r.get("id") for r in rail_pending[:25]],
                }
            )

    budget = derive_error_budget(epsilon_J)
    plan = holdout or default_holdout_plan(
        sorted({c.temperature_K for c in cells})
    )

    fractions = compute_source_selection_fractions(
        n_cells=n_cells,
        n_ok=n_ok,
        n_refused=n_refused if cell_results else 0,
        n_species_pending=sum(
            1
            for r in per_row
            if r.validation_status is RowValidationState.PENDING
        ),
        n_species_total=len(per_row),
        n_vaporock_species_covered=len(DEFAULT_CALIBRATION_SPECIES),
        n_literature_species=len(DEFAULT_CALIBRATION_SPECIES),
    )

    frozen = {
        sid: {
            "family_id": spec.family_id.value,
            "pO2_exponent": spec.pO2_exponent,
            "max_parameters": spec.max_parameters,
            "coefficient_names": list(spec.coefficient_names),
            "parent_oxide": spec.parent_oxide,
            "activity_exponent": spec.activity_exponent,
            "notes": spec.notes,
        }
        for sid, spec in FROZEN_ANALYTICAL_FAMILIES.items()
    }

    split_counts: dict[str, int] = {}
    for cell in cells:
        split_counts[cell.split.value] = split_counts.get(cell.split.value, 0) + 1

    notes = [
        "Raw VapoRock remains calibration/diagnostic-only; never drives flux.",
        "Validated flips require independent anchors + holdout + boundary + "
        "error budget; model-model agreement alone cannot certify.",
        "Runtime loads only data/vapour_rail_calibration.yaml; never the "
        "SQLite research store.",
        "Warm pool only — no VapoRock result/calibration cache.",
    ]

    return ProgressiveValidationReport(
        calibration_id=calibration_id,
        domain={
            "temperature_K": [DEFAULT_T_MIN_K, DEFAULT_T_MAX_K],
            "temperature_step_K": DEFAULT_T_STEP_K,
            "user_story_slice_K": USER_STORY_T_K,
            "fo2_deltas_dex": list(DEFAULT_FO2_DELTAS_DEX),
            "fo2_buffer": "Kress91/Holzheid pure-FeO IW",
            "p_floor_Pa": DEFAULT_P_FLOOR_PA,
        },
        frozen_families=frozen,
        holdout={
            "held_out_formulation_family": plan.held_out_formulation_family,
            "held_out_temperatures_K": list(plan.held_out_temperatures_K),
            "held_out_fo2_labels": list(plan.held_out_fo2_labels),
        },
        per_row_state=[r.as_dict() for r in per_row],
        remaining_pending=remaining_pending,
        source_selection_fractions=fractions.as_dict(),
        error_budget=budget.as_dict(),
        boundary_statistics=[
            b.as_dict() for b in (boundary_statistics or ())
        ],
        cell_counts={
            "planned_cells": len(cells),
            "evaluated_cells": len(cell_results),
            "ok": n_ok,
            "refused": n_refused if cell_results else 0,
            **{f"split_{k}": v for k, v in split_counts.items()},
        },
        notes=notes,
    )


# ---------------------------------------------------------------------------
# SQLite research store (offline only)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cells (
    cell_id TEXT PRIMARY KEY,
    formulation_id TEXT NOT NULL,
    formulation_family TEXT NOT NULL,
    temperature_K REAL NOT NULL,
    fo2_label TEXT NOT NULL,
    fO2_log REAL NOT NULL,
    split TEXT NOT NULL,
    status TEXT NOT NULL,
    composition_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id TEXT NOT NULL,
    species TEXT NOT NULL,
    kind TEXT NOT NULL,
    pressure_Pa REAL,
    p_floor_Pa REAL NOT NULL,
    log10_pressure_Pa REAL,
    note TEXT NOT NULL,
    FOREIGN KEY (cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE IF NOT EXISTS boundary_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species TEXT NOT NULL,
    boundary TEXT NOT NULL,
    channel TEXT NOT NULL,
    delta_log10_P REAL,
    abs_delta_log10_P REAL,
    source_before TEXT NOT NULL,
    source_after TEXT NOT NULL,
    admissible INTEGER,
    note TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_cell ON observations(cell_id);
CREATE INDEX IF NOT EXISTS idx_obs_species ON observations(species);
"""


class CalibrationResearchStore:
    """SQLite research store for raw calibration cells (offline only).

    Runtime code must never open this store. Only the reviewed YAML sidecar
    enters ``data/``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA_SQL)
        cell_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(cells)").fetchall()
        }
        if "diagnostics_json" not in cell_columns:
            self._conn.execute(
                "ALTER TABLE cells ADD COLUMN diagnostics_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CalibrationResearchStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])

    def insert_cell(
        self,
        cell: CalibrationCell,
        *,
        status: str,
        warnings: Sequence[str] = (),
        observations: Sequence[SpeciesObservation] = (),
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        diagnostic_payload = dict(diagnostics or {})
        if diagnostic_payload:
            consume_melt_extrapolation_envelope(
                diagnostic_payload,
                temperature_K=float(cell.temperature_K),
            )
        self._conn.execute(
            """
            INSERT OR REPLACE INTO cells(
                cell_id, formulation_id, formulation_family, temperature_K,
                fo2_label, fO2_log, split, status, composition_json,
                warnings_json, diagnostics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cell.cell_id,
                cell.formulation.formulation_id,
                cell.formulation.family,
                cell.temperature_K,
                cell.fo2_label,
                cell.fO2_log,
                cell.split.value,
                status,
                json.dumps(dict(cell.formulation.composition_mol), sort_keys=True),
                json.dumps(list(warnings)),
                json.dumps(diagnostic_payload, sort_keys=True),
            ),
        )
        self._conn.execute(
            "DELETE FROM observations WHERE cell_id = ?", (cell.cell_id,)
        )
        for obs in observations:
            self._conn.execute(
                """
                INSERT INTO observations(
                    cell_id, species, kind, pressure_Pa, p_floor_Pa,
                    log10_pressure_Pa, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cell.cell_id,
                    obs.species,
                    obs.kind.value,
                    obs.pressure_Pa,
                    obs.p_floor_Pa,
                    obs.log10_pressure_Pa,
                    obs.note,
                ),
            )
        self._conn.commit()

    def insert_boundary_stats(
        self, stats: Sequence[BoundaryStatistic]
    ) -> None:
        for stat in stats:
            self._conn.execute(
                """
                INSERT INTO boundary_stats(
                    species, boundary, channel, delta_log10_P,
                    abs_delta_log10_P, source_before, source_after,
                    admissible, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stat.species,
                    stat.boundary,
                    stat.channel,
                    stat.delta_log10_P,
                    stat.abs_delta_log10_P,
                    stat.source_before,
                    stat.source_after,
                    (
                        None
                        if stat.admissible is None
                        else (1 if stat.admissible else 0)
                    ),
                    stat.note,
                ),
            )
        self._conn.commit()

    def digest(self) -> str:
        """Digest promotion-relevant cell and observation inputs only."""

        h = hashlib.sha256()
        cur = self._conn.execute(
            "SELECT cell_id, status, temperature_K, fO2_log, split "
            "FROM cells ORDER BY cell_id"
        )
        for row in cur.fetchall():
            h.update(repr(row).encode("utf-8"))
        cur = self._conn.execute(
            "SELECT cell_id, species, kind, pressure_Pa, log10_pressure_Pa "
            "FROM observations ORDER BY cell_id, species"
        )
        for row in cur.fetchall():
            h.update(repr(row).encode("utf-8"))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Warm-pool runner
# ---------------------------------------------------------------------------


class CalibrationRunnerError(RuntimeError):
    """Raised when the calibration runner violates its hard contracts."""


def require_warm_pool_backend(backend: VapoRockBackend) -> None:
    """Hard-require the VR-5 warm pool (owner: warm pool only, no cache)."""

    if not backend.is_available():
        raise CalibrationRunnerError("VapoRock backend is not available")
    if not backend.uses_warm_pool:
        raise CalibrationRunnerError(
            "VR-10 calibration runner requires the VR-5 warm pool "
            "(initialize with warm_worker=True); cold in-process path "
            "and any result/calibration cache are forbidden"
        )


def open_warm_vaporock_backend(
    *,
    warm_pool_size: int = 1,
    reuse_system: bool = False,
    temperature_units: str = "C",
) -> VapoRockBackend:
    """Construct a VapoRock backend that uses the warm pool only."""

    backend = VapoRockBackend()
    ok = backend.initialize(
        {
            "warm_worker": True,
            "warm_pool_size": int(warm_pool_size),
            "reuse_system": bool(reuse_system),
            "temperature_units": temperature_units,
        }
    )
    if not ok:
        raise CalibrationRunnerError(
            f"warm pool failed to initialize: {backend._last_error}"
        )
    require_warm_pool_backend(backend)
    return backend


def evaluate_cell(
    backend: VapoRockBackend,
    cell: CalibrationCell,
    *,
    species: Sequence[str] = DEFAULT_CALIBRATION_SPECIES,
    p_floor_Pa: float = DEFAULT_P_FLOOR_PA,
) -> dict[str, Any]:
    """Evaluate one cell via the warm pool and censor species observations."""

    require_warm_pool_backend(backend)
    temperature_C = float(cell.temperature_K) - 273.15
    result = backend.equilibrate(
        temperature_C=temperature_C,
        composition_mol=dict(cell.formulation.composition_mol),
        fO2_log=float(cell.fO2_log),
        liquid_fraction=1.0,
    )
    status = str(getattr(result, "status", "unknown") or "unknown")
    warnings = list(getattr(result, "warnings", None) or [])
    pressures = dict(
        getattr(result, "vaporock_full_speciation_Pa", None)
        or getattr(result, "vapor_pressures_Pa", None)
        or {}
    )
    result_diagnostics = dict(getattr(result, "diagnostics", None) or {})
    computed_envelope = melt_extrapolation_diagnostic(
        float(cell.temperature_K),
        _MELT_MODEL_ID,
    )
    envelope_source = (
        result_diagnostics
        if has_melt_extrapolation_envelope(result_diagnostics)
        else computed_envelope
    )
    consume_melt_extrapolation_envelope(
        envelope_source,
        temperature_K=float(cell.temperature_K),
    )
    envelope = {
        field: envelope_source[field]
        for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
    }
    instrument_status = envelope_source["instrument_status"]

    # non_authoritative is pressure-authority, not completeness. Hollow
    # producer results carry empty_speciation_cause / not_converged so
    # this cell is not a live speciation.
    if not vaporock_speciation_is_live(status, result_diagnostics, pressures):
        if status in {"ok", "non_authoritative"}:
            status = "not_converged"

    observations: list[SpeciesObservation] = []
    if status not in {"ok", "non_authoritative"}:
        for sid in species:
            observations.append(
                SpeciesObservation(
                    species=sid,
                    kind=ObservationKind.REFUSED,
                    pressure_Pa=None,
                    p_floor_Pa=float(p_floor_Pa),
                    log10_pressure_Pa=None,
                    note=f"cell status={status}",
                )
            )
    else:
        for sid in species:
            raw = pressures.get(sid)
            observations.append(
                censor_pressure(raw, p_floor_Pa=p_floor_Pa, species=sid)
            )

    return {
        **envelope,
        "cell_id": cell.cell_id,
        "status": status,
        "instrument_status": instrument_status,
        "warnings": warnings,
        "observations": observations,
        "n_pressures": len(pressures),
    }


def run_calibration_campaign(
    *,
    store_path: Path,
    calibration_id: str,
    backend: VapoRockBackend | None = None,
    cells: Sequence[CalibrationCell] | None = None,
    species: Sequence[str] = DEFAULT_CALIBRATION_SPECIES,
    p_floor_Pa: float = DEFAULT_P_FLOOR_PA,
    epsilon_J: float = DEFAULT_EPSILON_J,
    close_backend: bool = True,
) -> ProgressiveValidationReport:
    """Run the warm-pool campaign, persist raw cells, return the report.

    The optional *backend* must already be a warm-pool instance when provided.
    When omitted, a fresh warm pool is opened and closed at the end.
    """

    cells = tuple(cells or build_calibration_cells())
    owns_backend = backend is None
    if backend is None:
        backend = open_warm_vaporock_backend()
    else:
        require_warm_pool_backend(backend)

    cell_results: list[dict[str, Any]] = []
    boundary_stats: list[BoundaryStatistic] = []
    budget = derive_error_budget(epsilon_J)

    try:
        with CalibrationResearchStore(store_path) as store:
            store.set_meta("calibration_id", calibration_id)
            store.set_meta("p_floor_Pa", str(p_floor_Pa))
            store.set_meta("epsilon_J", str(epsilon_J))
            store.set_meta(
                "frozen_families_json",
                json.dumps(
                    {
                        sid: spec.family_id.value
                        for sid, spec in FROZEN_ANALYTICAL_FAMILIES.items()
                    },
                    sort_keys=True,
                ),
            )
            store.set_meta("warm_pool_only", "true")
            store.set_meta("cache_layer", "none")

            for cell in cells:
                evaluated = evaluate_cell(
                    backend,
                    cell,
                    species=species,
                    p_floor_Pa=p_floor_Pa,
                )
                observations: list[SpeciesObservation] = list(
                    evaluated["observations"]
                )
                store.insert_cell(
                    cell,
                    status=str(evaluated["status"]),
                    warnings=list(evaluated["warnings"]),
                    observations=observations,
                    diagnostics={
                        field: evaluated[field]
                        for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
                    }
                    | {
                        "instrument_status": evaluated["instrument_status"],
                    },
                )
                cell_results.append(
                    {
                        "cell_id": evaluated["cell_id"],
                        "status": evaluated["status"],
                        "n_observations": len(observations),
                        "n_point": sum(
                            1
                            for o in observations
                            if o.kind is ObservationKind.POINT
                        ),
                        "n_censored": sum(
                            1
                            for o in observations
                            if o.kind is ObservationKind.CENSORED_SUB_FLOOR
                        ),
                    }
                )

            # Boundary faces: compare T_min vs adjacent interior when available.
            # Without dual-source analytical evaluation here, record the face
            # enumeration and mark statistics as pending measurement.
            for face in boundary_faces_for_domain():
                for sid in species:
                    boundary_stats.append(
                        BoundaryStatistic(
                            species=sid,
                            boundary=str(face["boundary"]),
                            channel=sid,
                            delta_log10_P=None,
                            abs_delta_log10_P=None,
                            source_before="vaporock_warm",
                            source_after="analytical_rail",
                            admissible=None,
                            note=(
                                "boundary face enumerated; dual-source "
                                "Δlog10(P) awaits analytical candidate fit "
                                f"(budget threshold "
                                f"{budget.log10_pressure_threshold_dex:.4f} dex)"
                            ),
                        )
                    )
            store.insert_boundary_stats(boundary_stats)
            store.set_meta("raw_store_digest", store.digest())

            report = build_progressive_validation_report(
                calibration_id=calibration_id,
                cells=cells,
                cell_results=cell_results,
                boundary_statistics=boundary_stats,
                epsilon_J=epsilon_J,
            )
            store.set_meta(
                "report_json",
                json.dumps(report.as_dict(), sort_keys=True),
            )
            return report
    finally:
        if owns_backend and backend is not None:
            backend.close()


# ---------------------------------------------------------------------------
# Reviewed sidecar (runtime-safe)
# ---------------------------------------------------------------------------


class CalibrationSidecarError(ValueError):
    """Invalid or unapproved calibration sidecar."""


def build_sidecar_document(
    *,
    calibration_id: str,
    raw_store_digest: str | None,
    raw_store_path: str | None,
    report: ProgressiveValidationReport | None = None,
    approval: str = "scaffold_unpromoted",
    notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build the reviewed promotion sidecar payload (no SQLite contents)."""

    report = report or build_progressive_validation_report(
        calibration_id=calibration_id,
        cells=build_calibration_cells(),
        include_rail_pending=False,
    )
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "kind": SIDECAR_KIND,
        "calibration_id": calibration_id,
        "authority": "diagnostic_only",
        "certifies": False,
        "approval": approval,
        "domain": report.domain,
        "frozen_families": report.frozen_families,
        "parameter_caps": {
            sid: {
                "max_parameters": spec.max_parameters,
                "coefficient_names": list(spec.coefficient_names),
            }
            for sid, spec in FROZEN_ANALYTICAL_FAMILIES.items()
        },
        "validation_splits": report.holdout,
        "independent_anchors": [
            {
                "anchor_id": a.anchor_id,
                "species": a.species,
                "kind": a.kind,
                "citation": a.citation,
                "temperature_K": a.temperature_K,
                "notes": a.notes,
                "may_certify": a.may_certify,
            }
            for a in DEFAULT_INDEPENDENT_ANCHORS
        ],
        "error_budget": report.error_budget,
        "boundary_statistics_summary": {
            "n_faces": len(report.boundary_statistics),
            "note": (
                "Full dual-source boundary Δlog10(P) lives in the research "
                "store; sidecar carries reviewed summary only."
            ),
        },
        "per_row_state": report.per_row_state,
        "remaining_pending_species": [
            r["species"]
            for r in report.per_row_state
            if r.get("validation_status") == RowValidationState.PENDING.value
        ],
        "source_selection_fractions": report.source_selection_fractions,
        "raw_store": {
            "digest": raw_store_digest,
            "path": raw_store_path,
            "runtime_readable": False,
            "note": (
                "Runtime must never open the SQLite research store; only this "
                "reviewed sidecar is admitted under data/."
            ),
        },
        "performance": {
            "execution": "vaporock_warm_pool_only",
            "cache_layer": None,
            "note": (
                "Owner ruling: warm pool only; no VapoRock result/calibration "
                "cache until measured need."
            ),
        },
        "notes": list(
            notes
            or (
                "VR-10 scaffold: progressive-validation infrastructure landed; "
                "no catalog row flipped to validated in this chunk.",
                "Golden-neutral / offline; R1+ epochs own any flux cutover.",
            )
        ),
    }


def load_vapour_rail_calibration_sidecar(
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the reviewed runtime sidecar (YAML only — never SQLite)."""

    sidecar_path = Path(path) if path is not None else DEFAULT_SIDECAR_PATH
    if not sidecar_path.is_file():
        raise CalibrationSidecarError(
            f"calibration sidecar missing: {sidecar_path}"
        )
    payload = yaml.safe_load(sidecar_path.read_text())
    if not isinstance(payload, Mapping):
        raise CalibrationSidecarError("sidecar root must be a mapping")
    if int(payload.get("schema_version", -1)) != SIDECAR_SCHEMA_VERSION:
        raise CalibrationSidecarError(
            f"sidecar schema_version must be {SIDECAR_SCHEMA_VERSION}"
        )
    if payload.get("kind") != SIDECAR_KIND:
        raise CalibrationSidecarError(f"sidecar kind must be {SIDECAR_KIND!r}")
    if payload.get("certifies") is True:
        raise CalibrationSidecarError(
            "sidecar must not certify (O1 status-bearing ceiling)"
        )
    if payload.get("authority") != "diagnostic_only":
        raise CalibrationSidecarError(
            "sidecar authority must be diagnostic_only until an R epoch"
        )
    raw_store = payload.get("raw_store") or {}
    if raw_store.get("runtime_readable") is True:
        raise CalibrationSidecarError(
            "sidecar must declare raw_store.runtime_readable: false"
        )
    perf = payload.get("performance") or {}
    if perf.get("cache_layer") not in (None, "none", False):
        raise CalibrationSidecarError(
            "sidecar must not introduce a VapoRock cache layer "
            f"(got performance.cache_layer={perf.get('cache_layer')!r})"
        )
    if not payload.get("calibration_id"):
        raise CalibrationSidecarError("sidecar requires calibration_id")
    if not isinstance(payload.get("frozen_families"), Mapping):
        raise CalibrationSidecarError("sidecar requires frozen_families")
    if not isinstance(payload.get("parameter_caps"), Mapping):
        raise CalibrationSidecarError("sidecar requires parameter_caps")
    return dict(payload)


def write_sidecar(
    path: Path,
    document: Mapping[str, Any],
) -> None:
    """Write a reviewed sidecar YAML document."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate by round-trip through the loader contract after write.
    text = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    path.write_text(text)
    load_vapour_rail_calibration_sidecar(path)


def runtime_modules_must_not_import_research_sqlite() -> tuple[str, ...]:
    """Static contract: listed runtime loaders never open the research store.

    Returns the public runtime entrypoints that are permitted to touch
    calibration data (YAML sidecar only).
    """

    return (
        "simulator.vapour_rail.calibration.load_vapour_rail_calibration_sidecar",
    )


def assert_no_runtime_sqlite_reader(
    *,
    source_text: str,
    allowed_symbols: Iterable[str] = (
        "CalibrationResearchStore",
        "sqlite3",
    ),
) -> None:
    """Guard helper for tests: runtime loader source must not open SQLite.

    *source_text* should be the source of a runtime-facing function. The
    research-store class may mention sqlite3; the sidecar loader must not.
    """

    lowered = source_text.lower()
    if "sqlite3.connect" in lowered or "calibrationresearchstore" in lowered:
        # Allow only if this is clearly the store implementation itself.
        if "class calibrationresearchstore" not in lowered:
            raise AssertionError(
                "runtime calibration loader must not open the SQLite "
                "research store"
            )


__all__ = [
    "AnalyticalFamilyId",
    "AnalyticalFamilySpec",
    "BoundaryStatistic",
    "CalibrationCell",
    "CalibrationResearchStore",
    "CalibrationRunnerError",
    "CalibrationSidecarError",
    "DEFAULT_CALIBRATION_SPECIES",
    "DEFAULT_EPSILON_J",
    "DEFAULT_INDEPENDENT_ANCHORS",
    "DEFAULT_P_FLOOR_PA",
    "DEFAULT_SIDECAR_PATH",
    "DEFAULT_T_MAX_K",
    "DEFAULT_T_MIN_K",
    "DEFAULT_T_STEP_K",
    "DownstreamErrorBudget",
    "FROZEN_ANALYTICAL_FAMILIES",
    "Formulation",
    "HoldoutPlan",
    "HoldoutSplit",
    "IndependentAnchor",
    "ObservationKind",
    "PerRowValidationState",
    "ProgressiveValidationReport",
    "RowValidationState",
    "SIDECAR_KIND",
    "SIDECAR_SCHEMA_VERSION",
    "SourceSelectionFractions",
    "SpeciesObservation",
    "USER_STORY_T_K",
    "assert_no_runtime_sqlite_reader",
    "boundary_faces_for_domain",
    "build_calibration_cells",
    "build_per_row_states",
    "build_progressive_validation_report",
    "build_sidecar_document",
    "censor_pressure",
    "compute_source_selection_fractions",
    "default_holdout_plan",
    "default_simple_melt_corpus",
    "derive_error_budget",
    "evaluate_boundary_jumps",
    "evaluate_cell",
    "fo2_grid_for_temperature",
    "iw_log_fO2",
    "load_vapour_rail_calibration_sidecar",
    "open_warm_vaporock_backend",
    "require_warm_pool_backend",
    "run_calibration_campaign",
    "runtime_modules_must_not_import_research_sqlite",
    "temperature_grid_K",
    "write_sidecar",
]
