#!/usr/bin/env python3
"""Motzfeldt / Whitman inversion consumer for literature-extract equipment metadata.

Closes owner-audit Q3 / t-511: equipment fields (``orifice_area``,
``clausing_factor``, ``sample_surface_area``, ``cell_material``,
``multi_orifice_series``) were captured but never read (SC-50 in waiting).
This tool is the consumer.

**(a) Alpha inversion.** Given a KEMS measured pressure plus cell geometry —
and optionally a Langmuir free-evaporation rate on the same substance — solve
for the vaporization coefficient ``alpha`` with first-order uncertainty
propagation. A multi-orifice series yields ``P_eq`` by zero-orifice
extrapolation and ``alpha`` from the Motzfeldt slope.

**(b) Cell-material → effective-pO₂ boundary.** Mo/W cells are reducing
(oxygen sinks via oxide formation); Ir / Pt / alumina / YSZ are redox-neutral
for oxide samples. Annotations are attached at merge time
(:func:`tools.extract_merge.build_by_species`) via
:func:`effective_po2_boundary_from_cell_material`.

**(c) Outputs.** DRAFT ``type: alpha`` observations written **into** the
literature extract store, marked ``inferred: true`` with derivation text and
parent ``(source_id, observation_id)`` pointers. Runtime never reads this
tree; every write is re-validated by
:mod:`tools.validate_literature_extracts` before the file is left dirty.

Derivation
----------
Premise (Whitman 1952; Motzfeldt 1955; standard modern statement in
Sossi & Fegley 2018 *Reviews in Mineralogy & Geochemistry* 84, eq. 18)::

    The sample free surface (area A_s) evaporates into a Knudsen cell that
    approaches closed-system equilibrium at pressure P_eq. Gas leaves only
    through a pinhole orifice of area a with Clausing (transmission) factor
    f ≡ W_o. When the evaporation coefficient α < 1, the steady-state cell
    pressure P_meas inferred from the effusion rate (HKL mass-loss reduction
    with α_orifice = 1) is lower than P_eq because surface kinetics cannot
    fully replenish the vapor against orifice loss.

Full Sossi–Fegley form (their W_c is the cell-body Clausing factor)::

    P_eq / P_meas = 1 + (f * a / A_s) * (1/α + 1/W_c − 2)     (1)

Open, short cell body (W_c → 1) and α ≪ 1 so that 1/α − 1 ≈ 1/α reduces to
the owner-ratified working form (t-508 equipment-metadata note,
VALUE-PRECEDENCE companion)::

    P_eq / P_meas = 1 + f * a / (α * A_s)                     (2)

Algebra — invert (2) for α (require P_eq > P_meas > 0)::

    α = (f * a) / (A_s * (P_eq / P_meas − 1))                 (3)

Units::

    a, A_s : m²  (any consistent length²; ratio a/A_s is dimensionless)
    f      : dimensionless Clausing factor in (0, 1]
    P_eq, P_meas : Pa (any consistent pressure unit; only the ratio enters)
    α      : dimensionless

Sanity (published Motzfeldt analysis). Costa & Jacobson 2015 (NASA NTRS
20150002321) report Fe vaporization coefficients α ≈ 0.011–0.020 on Fo93Fa7
olivine via multi-cell Whitman–Motzfeldt orifice series in Mo Knudsen cells
at 1700–1800 K. With the synthetic geometry used in the unit tests
(a = 1e-6 m², f = 1, A_s = 1e-4 m², P_eq/P_meas = 1.5) equation (3) returns
α = 0.02 — the high side of Costa's measured Fe band — confirming that a
plausible orifice/sample ratio (a/A_s = 0.01) at α ~ 0.02 produces an O(1)
Motzfeldt correction, the regime those multi-orifice studies target.

Multi-orifice rearrangement of (2)::

    1/P_meas = 1/P_eq + (f a / A_s) / (α P_eq)                (4)

Plot 1/P_meas against x ≡ f a / A_s. Linear fit gives intercept b = 1/P_eq
and slope m = 1/(α P_eq), so::

    P_eq = 1/b ,   α = b / m                                  (5)

Zero-orifice extrapolation (x → 0) recovers P_eq = P_meas(x=0).

Optional Langmuir free-surface rate on the same substance. Ideal HKL flux
J (mol m⁻² s⁻¹) at free surface with bulk pressure ≈ 0::

    J = α * P_eq / sqrt(2 π M R T)                            (6)

so α_L = J * sqrt(2 π M R T) / P_eq. When geometry-only Motzfeldt α and
Langmuir α_L both exist they are retained as competing inferred rows
(never averaged; VALUE-PRECEDENCE).

Uncertainty (first-order relative product/quotient form on (3))::

    (σ_α/α)² = (σ_f/f)² + (σ_a/a)² + (σ_As/A_s)²
               + (σ_R / (R − 1))²                             (7)

where R = P_eq/P_meas and σ_R/R combines relative errors on the two
pressures in quadrature. Absolute σ_α = |α| * (σ_α/α). Inputs lacking an
uncertainty contribution are treated as exact (zero variance).

Usage::

  python tools/motzfeldt.py scan
  python tools/motzfeldt.py invert --P-eq 1.5 --P-meas 1.0 \\
      --orifice-area 1e-6 --clausing 1.0 --sample-area 1e-4
  python tools/motzfeldt.py write-drafts --extracts-dir DIR [--dry-run]
  python tools/motzfeldt.py classify-cell Mo
"""

from __future__ import annotations

import argparse
import copy
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
EXTRACTS_DIR = ROOT / "data" / "literature" / "extracts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from validate_literature_extracts import (  # noqa: E402
    EXTRACTS_DIR as _VLE_EXTRACTS,
    discover_extracts,
    validate_extract_document,
)

# Gas constant J/(mol·K) — matches simulator.condensation.GAS_CONSTANT_J_MOL_K.
R_GAS = 8.314462618


# ---------------------------------------------------------------------------
# Typed refusals + SI unit tables (areas → m², pressures → Pa)
# ---------------------------------------------------------------------------


class MotzfeldtUnitError(ValueError):
    """Typed refusal: missing or unrecognized unit on a dimensional field.

    Never silently assume SI (or any other) unit when the extract omits one —
    that path is exactly how cm²/m² mixing produced schema-valid α = 200.
    """


class MotzfeldtDomainError(ValueError):
    """Typed refusal: derived quantity outside its physical domain.

    Evaporation coefficient α is defined on (0, 1]; α ≤ 0 or α > 1 is
    non-physical regardless of whether unit conversion was applied.
    """


# Explicit conversion factors to SI. Keys are normalized tokens (see
# ``_normalize_unit_token``). Unlisted tokens → MotzfeldtUnitError.
AREA_TO_M2: dict[str, float] = {
    "m2": 1.0,
    "m^2": 1.0,
    "squaremeter": 1.0,
    "squaremeters": 1.0,
    "cm2": 1.0e-4,
    "cm^2": 1.0e-4,
    "squarecentimeter": 1.0e-4,
    "squarecentimeters": 1.0e-4,
    "mm2": 1.0e-6,
    "mm^2": 1.0e-6,
    "squaremillimeter": 1.0e-6,
    "squaremillimeters": 1.0e-6,
    "um2": 1.0e-12,
    "um^2": 1.0e-12,
    "µm2": 1.0e-12,
    "µm^2": 1.0e-12,
    "micrometer2": 1.0e-12,
    "micrometer^2": 1.0e-12,
}

PRESSURE_TO_PA: dict[str, float] = {
    "pa": 1.0,
    "pascal": 1.0,
    "pascals": 1.0,
    "kpa": 1.0e3,
    "mpa": 1.0e6,
    "bar": 1.0e5,
    "mbar": 1.0e2,
    "atm": 101325.0,
    "torr": 133.322368421,
    "mmhg": 133.322368421,
    "psi": 6894.757293168,
}


def _normalize_unit_token(units: str) -> str:
    """Collapse unicode superscripts / whitespace to a lookup token."""
    s = str(units).strip().lower()
    s = s.replace("²", "2").replace("³", "3")
    s = s.replace("μ", "u").replace("µ", "u")
    s = s.replace(" ", "").replace("_", "")
    return s


def normalize_area_m2(value: float, units: str | None, *, field: str = "area") -> float:
    """Convert an area to m². Missing/unknown units → typed refusal."""
    if units is None or str(units).strip() == "":
        raise MotzfeldtUnitError(
            f"{field}: units missing for value={value!r}; "
            f"refusing assumed unit (require explicit area unit, e.g. m2/cm2)"
        )
    token = _normalize_unit_token(str(units))
    factor = AREA_TO_M2.get(token)
    if factor is None:
        raise MotzfeldtUnitError(
            f"{field}: unrecognized area units {units!r} for value={value!r}; "
            f"known tokens={sorted(set(AREA_TO_M2))}"
        )
    v = float(value)
    if not math.isfinite(v):
        raise MotzfeldtUnitError(f"{field}: non-finite area value={value!r}")
    return v * factor


def normalize_pressure_pa(
    value: float, units: str | None, *, field: str = "pressure"
) -> float:
    """Convert a pressure to Pa. Missing/unknown units → typed refusal."""
    if units is None or str(units).strip() == "":
        raise MotzfeldtUnitError(
            f"{field}: units missing for value={value!r}; "
            f"refusing assumed unit (require explicit pressure unit, e.g. Pa)"
        )
    token = _normalize_unit_token(str(units))
    factor = PRESSURE_TO_PA.get(token)
    if factor is None:
        raise MotzfeldtUnitError(
            f"{field}: unrecognized pressure units {units!r} for value={value!r}; "
            f"known tokens={sorted(set(PRESSURE_TO_PA))}"
        )
    v = float(value)
    if not math.isfinite(v):
        raise MotzfeldtUnitError(f"{field}: non-finite pressure value={value!r}")
    return v * factor


def require_alpha_physical(alpha: float, *, inputs_diagnostic: str) -> None:
    """Refuse α outside (0, 1] — definition of the evaporation coefficient.

    Defense in depth: unit conversion alone would not catch an arithmetic slip
    that still lands outside the physical interval.
    """
    if not math.isfinite(alpha):
        raise MotzfeldtDomainError(
            f"evaporation coefficient alpha={alpha!r} is non-finite; "
            f"inputs: {inputs_diagnostic}"
        )
    if not (0.0 < alpha <= 1.0):
        raise MotzfeldtDomainError(
            f"evaporation coefficient alpha={alpha:.6g} outside physical "
            f"domain (0, 1]; inputs: {inputs_diagnostic}"
        )


def _require_finite(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite (got {value!r})")
    return v


# ---------------------------------------------------------------------------
# Cell-material → effective pO₂ boundary (merge-time annotation)
# ---------------------------------------------------------------------------

# Citations for the redox-boundary classification. Mo/W getters form stable
# oxides and pull cell pO₂ down (raising metal-vapor partial pressures for
# MO ⇌ M + ½ O₂); Ir / Pt / alumina / YSZ are effectively inert oxygen
# buffers under KEMS vacuum (Costa & Jacobson 2015 crucible selection;
# Sossi & Fegley 2018 KEMS primer; Shornikov-class Mo-cell caveat in
# docs-private/research/2026-07-30-kems-liner-ranking).
CELL_MATERIAL_CLASS = {
    # reducing getters
    "mo": "reducing",
    "molybdenum": "reducing",
    "w": "reducing",
    "tungsten": "reducing",
    "ta": "reducing",
    "tantalum": "reducing",
    "nb": "reducing",
    "niobium": "reducing",
    "re": "reducing",  # Re can getter; treat as reducing until proven neutral
    "rhenium": "reducing",
    # neutral / inert liners
    "ir": "neutral",
    "iridium": "neutral",
    "pt": "neutral",
    "platinum": "neutral",
    "pt-rh": "neutral",
    "pt_rh": "neutral",
    "alumina": "neutral",
    "al2o3": "neutral",
    "α-al2o3": "neutral",
    "alpha-al2o3": "neutral",
    "ysz": "neutral",
    "zirconia": "neutral",
    "zro2": "neutral",
    "thoria": "neutral",
    "tho2": "neutral",
    "graphite": "reducing",  # C + ½ O₂ → CO sink under vacuum
    "c": "reducing",
    "bn": "neutral",
    "pbn": "neutral",
}

CELL_MATERIAL_CITATIONS = {
    "reducing": (
        "Mo/W (and C) Knudsen cells act as oxygen getters under vacuum: cell "
        "wall oxidation sinks pO₂ and elevates metal-vapor partial pressures "
        "for MO ⇌ M(g) + ½ O₂ relative to the free-surface congruent buffer. "
        "Costa & Jacobson 2015 (NASA NTRS 20150002321) report Mo cells react "
        "with olivine above the melting point and prefer Ir liners; "
        "Shornikov-class Mo-cell KEMS on CaO is ~10× high in pCa vs neutral "
        "(docs-private/research/2026-07-30-kems-liner-ranking)."
    ),
    "neutral": (
        "Ir / Pt / alumina / YSZ liners are redox-inert under KEMS vacuum for "
        "silicate/oxide samples: they do not impose an external oxygen sink, "
        "so the cell pO₂ boundary is set by the sample's own congruent "
        "vaporization (or an externally fixed buffer). Costa & Jacobson 2015 "
        "select Ir liner in graphite as the preferred unreactive cell; "
        "Sossi & Fegley 2018 Rev. Mineral. Geochem. 84 describe the ideal "
        "inert-cell KEMS limit."
    ),
    "unknown": (
        "Cell material not in the Mo/W-reducing vs Ir/alumina-neutral table; "
        "effective pO₂ boundary left unclassified (fail-closed annotation)."
    ),
}


def normalize_cell_material(raw: Any) -> str:
    """Normalize a free-text cell_material value to a lookup token."""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    # Common composite phrases: "Ir liner in graphite cell", "Mo Knudsen cell"
    # Prefer the liner / primary metal token when present.
    for token in (
        "ir liner",
        "iridium",
        "ir ",
        "pt-rh",
        "pt_rh",
        "platinum",
        "alumina",
        "al2o3",
        "ysz",
        "zirconia",
        "molybdenum",
        "tungsten",
        "graphite",
        " mo",
        "mo ",
        "mo,",
        " w ",
        "tungsten",
    ):
        if token in f" {s} " or s.startswith(token.strip()) or s.endswith(token.strip()):
            # Map phrase → key
            if "ir" in token:
                return "ir"
            if "pt" in token:
                return "pt"
            if "alumina" in token or "al2o3" in token:
                return "alumina"
            if "ysz" in token or "zirconia" in token:
                return "ysz"
            if "molybdenum" in token or token.strip() in {"mo", "mo,", "mo"}:
                return "mo"
            if "tungsten" in token or token.strip() == "w":
                return "w"
            if "graphite" in token:
                return "graphite"
    # Bare short tokens
    compact = s.replace(" ", "").replace("_", "-")
    if compact in CELL_MATERIAL_CLASS:
        return compact
    # First word
    first = s.split()[0] if s.split() else s
    first = first.strip(",.;:()")
    if first in CELL_MATERIAL_CLASS:
        return first
    return s


def classify_cell_material(raw: Any) -> dict[str, Any]:
    """Return effective-pO₂ boundary classification for a cell material string.

    Returns a dict with keys:
      material_raw, material_normalized, boundary (reducing|neutral|unknown),
      citation, inferred (always True — this is a tool annotation, not a
      published label).
    """
    norm = normalize_cell_material(raw)
    boundary = CELL_MATERIAL_CLASS.get(norm, "unknown")
    # Composite "Ir liner in graphite": liner wins (Ir is the sample-facing wall).
    raw_s = str(raw or "").lower()
    if "ir" in raw_s and ("liner" in raw_s or "lining" in raw_s):
        boundary = "neutral"
        norm = "ir"
    elif norm not in CELL_MATERIAL_CLASS:
        # Try token search for Mo/W vs Ir/alumina keywords.
        if any(k in raw_s for k in ("molybdenum", " mo", "mo cell", "mo knudsen", "tungsten")):
            if "ir" not in raw_s:
                boundary = "reducing"
                norm = "mo" if "tungsten" not in raw_s and " w" not in raw_s else "w"
        elif any(k in raw_s for k in ("iridium", "ir cell", "ir knudsen", "alumina", "al2o3", "ysz")):
            boundary = "neutral"
    return {
        "material_raw": None if raw is None else str(raw),
        "material_normalized": norm or None,
        "boundary": boundary,
        "citation": CELL_MATERIAL_CITATIONS[boundary],
        "inferred": True,
        "inference": (
            f"cell_material {raw!r} classified as effective-pO2 boundary "
            f"{boundary!r} (Mo/W/C reducing vs Ir/Pt/alumina/YSZ neutral)"
        ),
    }


def effective_po2_boundary_from_cell_material(raw: Any) -> dict[str, Any] | None:
    """Merge-time helper: None when no cell_material is stated."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        # equipment.cell_material is {value, locator, ...}
        val = raw.get("value")
        if val is None:
            return None
        ann = classify_cell_material(val)
        ann["locator"] = raw.get("locator")
        return ann
    if isinstance(raw, str) and not raw.strip():
        return None
    return classify_cell_material(raw)


def effective_po2_boundary_for_observation(obs: Mapping[str, Any]) -> dict[str, Any] | None:
    """Pull equipment.cell_material (if any) and return the pO₂ annotation."""
    equipment = obs.get("equipment")
    if not isinstance(equipment, Mapping):
        return None
    return effective_po2_boundary_from_cell_material(equipment.get("cell_material"))


# ---------------------------------------------------------------------------
# Motzfeldt inversion core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotzfeldtInputs:
    """Inputs for a single-orifice Motzfeldt inversion (equation 3)."""

    P_eq: float
    P_meas: float
    orifice_area: float  # a  (m²)
    clausing_factor: float  # f  (dimensionless)
    sample_surface_area: float  # A_s (m²)
    # Optional absolute 1σ uncertainties (same units as the value).
    sigma_P_eq: float | None = None
    sigma_P_meas: float | None = None
    sigma_orifice_area: float | None = None
    sigma_clausing_factor: float | None = None
    sigma_sample_surface_area: float | None = None


@dataclass(frozen=True)
class MotzfeldtResult:
    """Inverted α with propagated uncertainty and diagnostic ratio."""

    alpha: float
    sigma_alpha: float | None
    P_eq_over_P_meas: float
    orifice_to_sample_ratio: float  # f * a / A_s
    formula: str = "P_eq/P_meas = 1 + f*a/(alpha*A_s)"
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["notes"] = list(self.notes)
        return d


def motzfeldt_ratio(
    *,
    orifice_area: float,
    clausing_factor: float,
    sample_surface_area: float,
    alpha: float,
) -> float:
    """Forward Motzfeldt ratio P_eq/P_meas from equation (2).

    Areas are SI m² at the public API (unit conversion is an extract-IO concern).
    """
    a = _require_finite("orifice_area", orifice_area)
    f = _require_finite("clausing_factor", clausing_factor)
    A_s = _require_finite("sample_surface_area", sample_surface_area)
    al = _require_finite("alpha", alpha)
    if a <= 0 or A_s <= 0 or al <= 0 or f <= 0:
        raise ValueError("orifice_area, clausing_factor, sample_surface_area, alpha must be > 0")
    if f > 1.0:
        raise ValueError("clausing_factor must be <= 1")
    require_alpha_physical(
        al,
        inputs_diagnostic=(
            f"forward alpha input a={a}, f={f}, A_s={A_s}"
        ),
    )
    return 1.0 + (f * a) / (al * A_s)


def invert_alpha(inputs: MotzfeldtInputs) -> MotzfeldtResult:
    """Solve equation (3) for α with first-order uncertainty propagation.

    Raises ValueError when the Motzfeldt premise is violated (P_eq ≤ P_meas,
    non-positive geometry, Clausing factor outside (0, 1]).
    Raises MotzfeldtDomainError when inverted α is outside (0, 1].
    Areas/pressures are SI (m² / Pa) at this API; extract-store callers must
    normalize via :func:`normalize_area_m2` / :func:`normalize_pressure_pa`
    before constructing :class:`MotzfeldtInputs`.
    """
    P_eq = _require_finite("P_eq", inputs.P_eq)
    P_meas = _require_finite("P_meas", inputs.P_meas)
    a = _require_finite("orifice_area", inputs.orifice_area)
    f = _require_finite("clausing_factor", inputs.clausing_factor)
    A_s = _require_finite("sample_surface_area", inputs.sample_surface_area)

    if P_eq <= 0 or P_meas <= 0:
        raise ValueError("P_eq and P_meas must be > 0")
    if P_eq <= P_meas:
        raise ValueError(
            f"Motzfeldt premise requires P_eq > P_meas (got P_eq={P_eq}, P_meas={P_meas}); "
            f"zero orifice correction would imply α → ∞ or non-physical"
        )
    if a <= 0 or A_s <= 0 or f <= 0:
        raise ValueError("orifice_area, clausing_factor, sample_surface_area must be > 0")
    if f > 1.0:
        raise ValueError("clausing_factor must be <= 1")

    R = P_eq / P_meas
    x = (f * a) / A_s  # dimensionless orifice load
    alpha = x / (R - 1.0)
    require_alpha_physical(
        alpha,
        inputs_diagnostic=(
            f"P_eq={P_eq}, P_meas={P_meas}, R={R}, a={a}, f={f}, A_s={A_s}, "
            f"x=f*a/A_s={x}"
        ),
    )
    notes: list[str] = []

    # Uncertainty via equation (7).
    sigma_alpha: float | None = None
    rel_var = 0.0
    contrib = 0

    def _rel(val: float, sig: float | None) -> float:
        if sig is None:
            return 0.0
        sig_f = _require_finite("sigma", float(sig))
        if val == 0:
            return 0.0
        return (sig_f / abs(val)) ** 2

    rel_var += _rel(f, inputs.sigma_clausing_factor)
    if inputs.sigma_clausing_factor is not None:
        contrib += 1
    rel_var += _rel(a, inputs.sigma_orifice_area)
    if inputs.sigma_orifice_area is not None:
        contrib += 1
    rel_var += _rel(A_s, inputs.sigma_sample_surface_area)
    if inputs.sigma_sample_surface_area is not None:
        contrib += 1

    # σ_R from pressure pair: R = P_eq/P_meas → (σ_R/R)² = (σ_eq/P_eq)² + (σ_m/P_meas)²
    rel_R_sq = _rel(P_eq, inputs.sigma_P_eq) + _rel(P_meas, inputs.sigma_P_meas)
    if inputs.sigma_P_eq is not None or inputs.sigma_P_meas is not None:
        contrib += 1
        sigma_R = R * math.sqrt(rel_R_sq)
        # dα/α contribution from (R − 1): α ∝ 1/(R−1) → |dα/α| = |dR|/(R−1)
        rel_var += (sigma_R / (R - 1.0)) ** 2

    if contrib > 0 and rel_var > 0:
        sigma_alpha = abs(alpha) * math.sqrt(rel_var)

    return MotzfeldtResult(
        alpha=alpha,
        sigma_alpha=sigma_alpha,
        P_eq_over_P_meas=R,
        orifice_to_sample_ratio=x,
        notes=tuple(notes),
    )


def invert_alpha_full(
    *,
    P_eq: float,
    P_meas: float,
    orifice_area: float,
    orifice_clausing: float,
    sample_surface_area: float,
    cell_clausing: float = 1.0,
    sigma_P_eq: float | None = None,
    sigma_P_meas: float | None = None,
    sigma_orifice_area: float | None = None,
    sigma_orifice_clausing: float | None = None,
    sigma_sample_surface_area: float | None = None,
) -> MotzfeldtResult:
    """Invert the full Sossi–Fegley form (equation 1) for α.

    P_eq/P_meas = 1 + (f a / A_s) * (1/α + 1/W_c − 2)

    Let K = (P_eq/P_meas − 1) * (A_s / (f a)). Then
    K = 1/α + 1/W_c − 2  ⇒  1/α = K − 1/W_c + 2  ⇒  α = 1/(K − 1/W_c + 2).
    When W_c = 1 this reduces to α = 1/(K + 1); the simplified form (2) is the
    further α ≪ 1 limit of that (dropping the −1 vs 1/α).
    """
    P_eq = _require_finite("P_eq", P_eq)
    P_meas = _require_finite("P_meas", P_meas)
    a = _require_finite("orifice_area", orifice_area)
    f = _require_finite("orifice_clausing", orifice_clausing)
    A_s = _require_finite("sample_surface_area", sample_surface_area)
    W_c = _require_finite("cell_clausing", cell_clausing)
    if P_eq <= P_meas or P_eq <= 0 or P_meas <= 0:
        raise ValueError("require P_eq > P_meas > 0")
    if a <= 0 or A_s <= 0 or f <= 0 or W_c <= 0 or f > 1.0 or W_c > 1.0:
        raise ValueError("geometry/Clausing factors out of range")
    R = P_eq / P_meas
    x = (f * a) / A_s
    K = (R - 1.0) / x
    inv_alpha = K - (1.0 / W_c) + 2.0
    if inv_alpha <= 0:
        raise ValueError(
            f"full Motzfeldt inversion non-physical (1/α={inv_alpha}); "
            f"check W_c and P_eq/P_meas"
        )
    alpha = 1.0 / inv_alpha
    require_alpha_physical(
        alpha,
        inputs_diagnostic=(
            f"full-form P_eq={P_eq}, P_meas={P_meas}, a={a}, f={f}, "
            f"A_s={A_s}, W_c={W_c}, K={K}"
        ),
    )
    # Uncertainty disposition (review P2): simplified-form relative propagation
    # is reused as a conservative *order-of-magnitude* draft estimate, not the
    # full Jacobian of α=1/(K−1/W_c+2). When a full-form σ is required for a
    # reviewed row, recompute with an analytic Jacobian or drop sigma_alpha.
    proxy = invert_alpha(
        MotzfeldtInputs(
            P_eq=P_eq,
            P_meas=P_meas,
            orifice_area=a,
            clausing_factor=f,
            sample_surface_area=A_s,
            sigma_P_eq=sigma_P_eq,
            sigma_P_meas=sigma_P_meas,
            sigma_orifice_area=sigma_orifice_area,
            sigma_clausing_factor=sigma_orifice_clausing,
            sigma_sample_surface_area=sigma_sample_surface_area,
        )
    )
    return MotzfeldtResult(
        alpha=alpha,
        sigma_alpha=proxy.sigma_alpha,
        P_eq_over_P_meas=R,
        orifice_to_sample_ratio=x,
        formula="P_eq/P_meas = 1 + (f*a/A_s)*(1/alpha + 1/W_c - 2)",
        notes=proxy.notes
        + (
            f"W_c={W_c}",
            "sigma_alpha uses simplified-form proxy (not full-form Jacobian)",
        ),
    )


# ---------------------------------------------------------------------------
# Multi-orifice extrapolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrificePoint:
    """One orifice in a multi-orifice Motzfeldt series."""

    P_meas: float
    orifice_area: float
    clausing_factor: float = 1.0
    sample_surface_area: float | None = None  # defaults to series A_s


@dataclass(frozen=True)
class MultiOrificeResult:
    P_eq: float
    alpha: float
    intercept: float  # 1/P_eq
    slope: float  # 1/(α P_eq)
    n_points: int
    r_squared: float | None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["notes"] = list(self.notes)
        return d


def _linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float | None]:
    """Ordinary least-squares y = m x + b. Returns (slope, intercept, R²)."""
    n = len(xs)
    if n < 2:
        raise ValueError("linear fit needs ≥2 points")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_yy = sum((y - mean_y) ** 2 for y in ys)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if ss_xx == 0:
        raise ValueError("all x identical; cannot fit slope")
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    r_sq: float | None
    if ss_yy == 0:
        r_sq = 1.0 if all(abs(y - mean_y) < 1e-30 for y in ys) else None
    else:
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        r_sq = 1.0 - ss_res / ss_yy
    return slope, intercept, r_sq


def multi_orifice_alpha(
    points: Sequence[OrificePoint],
    *,
    sample_surface_area: float,
) -> MultiOrificeResult:
    """Extrapolate multi-orifice series via equation (4)/(5).

    Requires ≥2 distinct orifice loads. ``sample_surface_area`` is the series
    default A_s (m²); a per-point override is allowed when the free surface
    changes between cells.

    SI contract: every ``OrificePoint.P_meas`` must already be in **Pa** and
    every ``orifice_area`` / ``sample_surface_area`` in **m²** (extract-store
    callers normalize via :func:`normalize_pressure_pa` /
    :func:`normalize_area_m2`). Then intercept b = 1/P_eq has units Pa⁻¹, so
    P_eq = 1/b is in Pa — the ``P_eq_Pa`` label on the write path is that
    identification, not a unit assumption at emit time.
    """
    if len(points) < 2:
        raise ValueError("multi-orifice inversion needs ≥2 orifice points")
    A_s_default = _require_finite("sample_surface_area", sample_surface_area)
    if A_s_default <= 0:
        raise ValueError("sample_surface_area must be > 0")

    xs: list[float] = []
    ys: list[float] = []
    for i, pt in enumerate(points):
        P_m = _require_finite(f"point[{i}].P_meas", pt.P_meas)
        a = _require_finite(f"point[{i}].orifice_area", pt.orifice_area)
        f = _require_finite(f"point[{i}].clausing_factor", pt.clausing_factor)
        A_s = (
            _require_finite(f"point[{i}].sample_surface_area", pt.sample_surface_area)
            if pt.sample_surface_area is not None
            else A_s_default
        )
        if P_m <= 0 or a <= 0 or f <= 0 or A_s <= 0 or f > 1.0:
            raise ValueError(f"point[{i}] has non-physical geometry or P_meas")
        xs.append((f * a) / A_s)
        ys.append(1.0 / P_m)

    slope, intercept, r_sq = _linear_fit(xs, ys)
    if intercept <= 0:
        raise ValueError(
            f"extrapolated intercept 1/P_eq={intercept} ≤ 0; series is non-physical"
        )
    if slope <= 0:
        raise ValueError(
            f"Motzfeldt slope={slope} ≤ 0; expected positive d(1/P)/d(f a/A_s)"
        )
    P_eq = 1.0 / intercept
    alpha = intercept / slope  # = (1/P_eq) / (1/(α P_eq)) = α
    require_alpha_physical(
        alpha,
        inputs_diagnostic=(
            f"multi-orifice n={len(points)}, intercept={intercept}, "
            f"slope={slope}, P_eq={P_eq}, A_s_default={A_s_default}, "
            f"xs={xs}"
        ),
    )
    notes: list[str] = []
    if r_sq is not None and r_sq < 0.9:
        notes.append(f"low linear-fit R²={r_sq:.4f}; orifice series may be non-Motzfeldt")
    return MultiOrificeResult(
        P_eq=P_eq,
        alpha=alpha,
        intercept=intercept,
        slope=slope,
        n_points=len(points),
        r_squared=r_sq,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Optional Langmuir free-surface rate
# ---------------------------------------------------------------------------


def alpha_from_langmuir_rate(
    *,
    flux_mol_m2_s: float,
    P_eq: float,
    T_K: float,
    molar_mass_kg_mol: float,
    sigma_flux: float | None = None,
    sigma_P_eq: float | None = None,
    sigma_T: float | None = None,
) -> tuple[float, float | None]:
    """α from free-surface HKL flux (equation 6).

    Returns (alpha, sigma_alpha_or_None).
    """
    J = _require_finite("flux_mol_m2_s", flux_mol_m2_s)
    P = _require_finite("P_eq", P_eq)
    T = _require_finite("T_K", T_K)
    M = _require_finite("molar_mass_kg_mol", molar_mass_kg_mol)
    if J < 0 or P <= 0 or T <= 0 or M <= 0:
        raise ValueError("Langmuir inputs must be positive (flux may be 0)")
    # J = α P / sqrt(2 π M R T)  ⇒  α = J * sqrt(2 π M R T) / P
    denom_sqrt = math.sqrt(2.0 * math.pi * M * R_GAS * T)
    alpha = J * denom_sqrt / P
    require_alpha_physical(
        alpha,
        inputs_diagnostic=(
            f"Langmuir J={J}, P_eq={P}, T_K={T}, M={M}"
        ),
    )
    sigma_alpha: float | None = None
    rel_var = 0.0
    n = 0
    if sigma_flux is not None:
        if J > 0:
            rel_var += (_require_finite("sigma_flux", sigma_flux) / J) ** 2
        n += 1
    if sigma_P_eq is not None:
        rel_var += (_require_finite("sigma_P_eq", sigma_P_eq) / P) ** 2
        n += 1
    if sigma_T is not None:
        # α ∝ sqrt(T) → (σ_α/α) contribution ½ (σ_T/T)
        rel_var += 0.25 * (_require_finite("sigma_T", sigma_T) / T) ** 2
        n += 1
    if n > 0 and rel_var > 0:
        sigma_alpha = abs(alpha) * math.sqrt(rel_var)
    return alpha, sigma_alpha


def alpha_combined_langmuir_kems(
    *,
    P_meas: float,
    orifice_area: float,
    clausing_factor: float,
    sample_surface_area: float,
    flux_mol_m2_s: float,
    T_K: float,
    molar_mass_kg_mol: float,
    # If P_eq is known independently (e.g. multi-orifice intercept), pass it;
    # otherwise the Langmuir branch alone cannot close Motzfeldt without P_eq.
    P_eq: float | None = None,
    sigma_P_meas: float | None = None,
    sigma_P_eq: float | None = None,
    sigma_orifice_area: float | None = None,
    sigma_clausing_factor: float | None = None,
    sigma_sample_surface_area: float | None = None,
    sigma_flux: float | None = None,
) -> dict[str, Any]:
    """Combine KEMS Motzfeldt geometry with a Langmuir rate when P_eq is known.

    Returns a dict with ``alpha_motzfeldt``, optional ``alpha_langmuir``, and
    notes. Does **not** average the two (VALUE-PRECEDENCE: competing rows).
    """
    out: dict[str, Any] = {"notes": []}
    if P_eq is None:
        out["notes"].append(
            "P_eq not provided; Motzfeldt inversion requires an independent "
            "equilibrium pressure (multi-orifice intercept or thermodynamic P_sat)"
        )
        out["alpha_motzfeldt"] = None
    else:
        res = invert_alpha(
            MotzfeldtInputs(
                P_eq=P_eq,
                P_meas=P_meas,
                orifice_area=orifice_area,
                clausing_factor=clausing_factor,
                sample_surface_area=sample_surface_area,
                sigma_P_eq=sigma_P_eq,
                sigma_P_meas=sigma_P_meas,
                sigma_orifice_area=sigma_orifice_area,
                sigma_clausing_factor=sigma_clausing_factor,
                sigma_sample_surface_area=sigma_sample_surface_area,
            )
        )
        out["alpha_motzfeldt"] = res.as_dict()
        a_L, s_L = alpha_from_langmuir_rate(
            flux_mol_m2_s=flux_mol_m2_s,
            P_eq=P_eq,
            T_K=T_K,
            molar_mass_kg_mol=molar_mass_kg_mol,
            sigma_flux=sigma_flux,
            sigma_P_eq=sigma_P_eq,
        )
        out["alpha_langmuir"] = {"alpha": a_L, "sigma_alpha": s_L}
        out["notes"].append(
            "Motzfeldt and Langmuir alphas retained as competing observations; "
            "never averaged (VALUE-PRECEDENCE)"
        )
    return out


# ---------------------------------------------------------------------------
# Extract-store IO: scan, build DRAFT alpha rows, write (validated)
# ---------------------------------------------------------------------------


def _equip_value(equipment: Mapping[str, Any], field: str) -> Any:
    payload = equipment.get(field)
    if isinstance(payload, Mapping):
        return payload.get("value")
    return None


def _equip_units(equipment: Mapping[str, Any], field: str) -> str | None:
    """Return the units string for an equipment field, or None if absent.

    Callers that consume a dimensional field MUST pass this through
    :func:`normalize_area_m2` / :func:`normalize_pressure_pa` rather than
    treating the bare numeric value as SI.
    """
    payload = equipment.get(field)
    if isinstance(payload, Mapping):
        u = payload.get("units")
        return str(u) if u is not None else None
    return None


def _equip_area_m2(
    equipment: Mapping[str, Any], field: str
) -> tuple[float | None, str | None]:
    """Read equipment area, normalize to m².

    Returns (area_m2, refusal_message). On success refusal is None; when the
    field is simply absent both are None; when value is present but units are
    missing/unknown, area is None and refusal names the field.
    """
    raw = _equip_value(equipment, field)
    if raw is None:
        return None, None
    v = _as_float(raw)
    if v is None:
        return None, f"{field}: non-numeric value={raw!r}"
    try:
        return normalize_area_m2(v, _equip_units(equipment, field), field=field), None
    except MotzfeldtUnitError as exc:
        return None, str(exc)


def _as_float(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        return v if math.isfinite(v) else None
    if isinstance(val, str):
        try:
            v = float(val.strip())
            return v if math.isfinite(v) else None
        except ValueError:
            return None
    return None


def _first_present(raw: Mapping[str, Any], *keys: str) -> Any:
    """Return the first key that is present and not None (0.0 is valid)."""
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


def _mapping_value_units(
    raw: Any, *, value_keys: Sequence[str], unit_keys: Sequence[str]
) -> tuple[float | None, str | None]:
    """Pull (numeric value, units) from a bare number, nested mapping, or flat keys."""
    if isinstance(raw, Mapping):
        v = None
        for k in value_keys:
            if k in raw and raw[k] is not None:
                v = _as_float(raw[k])
                if v is not None:
                    break
        if v is None and "value" in raw:
            v = _as_float(raw.get("value"))
        u = None
        for k in unit_keys:
            if k in raw and raw[k] is not None:
                u = str(raw[k])
                break
        if u is None and raw.get("units") is not None:
            u = str(raw["units"])
        return v, u
    return _as_float(raw), None


def _pressure_from_values(
    values: Any, *, default_units: str | None = None
) -> tuple[float | None, str | None]:
    """Extract P_meas in Pa from a psat_series / rate observation.

    Keys ending in ``_Pa`` imply Pascal (explicit unit identification).
    Bare keys (``value``, etc.) require ``default_units`` or a units field.
    Returns (P_Pa, refusal_or_None).
    """
    if not isinstance(values, Mapping):
        return None, None
    # Key suffix `_Pa` is an explicit unit identification (not an assumed unit).
    for key in ("P_Pa", "p_Pa", "pressure_Pa", "P_meas_Pa", "p_meas_Pa"):
        v = _as_float(values.get(key))
        if v is not None and v > 0:
            return v, None
    for key in ("P_meas", "p_meas", "pressure", "value"):
        if key not in values or values[key] is None:
            continue
        v = _as_float(values[key])
        if v is None or v <= 0:
            continue
        units = values.get("units") or values.get(f"{key}_units") or default_units
        try:
            return normalize_pressure_pa(v, units if units is None else str(units), field=key), None
        except MotzfeldtUnitError as exc:
            return None, str(exc)
    series = values.get("points") or values.get("series")
    if isinstance(series, list) and series:
        first = series[0]
        if isinstance(first, Mapping):
            return _pressure_from_values(first, default_units=default_units)
    return None, None


def _P_eq_from_values(
    values: Any, *, default_units: str | None = None
) -> tuple[float | None, str | None]:
    """Extract P_eq in Pa. Keys ending in ``_Pa`` imply Pascal."""
    if not isinstance(values, Mapping):
        return None, None
    for key in ("P_eq_Pa", "p_eq_Pa", "equilibrium_pressure_Pa"):
        v = _as_float(values.get(key))
        if v is not None and v > 0:
            return v, None
    for key in ("P_eq", "p_eq", "equilibrium_pressure"):
        if key not in values or values[key] is None:
            continue
        v = _as_float(values[key])
        if v is None or v <= 0:
            continue
        units = values.get("units") or values.get(f"{key}_units") or default_units
        try:
            return normalize_pressure_pa(v, units if units is None else str(units), field=key), None
        except MotzfeldtUnitError as exc:
            return None, str(exc)
    return None, None


@dataclass
class GeometryCandidate:
    """An extract observation that carries enough geometry for Motzfeldt work.

    Dimensional fields (``orifice_area``, ``sample_surface_area``, ``P_meas``,
    ``P_eq``) are stored in SI (m² / Pa) after unit normalization. When a
    dimensional field was present but units were missing/unknown, the SI
    field is None and ``unit_refusals`` records the typed refusal.
    """

    source_id: str
    species_id: str
    observation_id: str
    extract_path: Path
    observation: dict[str, Any]
    orifice_area: float | None  # m²
    clausing_factor: float | None
    sample_surface_area: float | None  # m²
    cell_material: Any
    multi_orifice_series: Any
    P_meas: float | None  # Pa
    P_eq: float | None  # Pa
    complete_for_single_orifice: bool
    complete_for_multi_orifice: bool
    unit_refusals: tuple[str, ...] = ()


def scan_geometry_candidates(
    extracts_dir: Path | None = None,
) -> list[GeometryCandidate]:
    """Scan extract store for observations with Motzfeldt-relevant equipment.

    Every dimensional equipment field is normalized through
    :func:`_equip_area_m2` / pressure helpers; missing or unrecognized units
    produce a typed refusal (candidate stays incomplete) rather than an
    assumed SI value.
    """
    d = extracts_dir or EXTRACTS_DIR
    out: list[GeometryCandidate] = []
    for path in discover_extracts(d):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(doc, Mapping):
            continue
        source_id = str(doc.get("source_id") or path.stem)
        species_map = doc.get("species") or {}
        if not isinstance(species_map, Mapping):
            continue
        for sid, block in species_map.items():
            if not isinstance(block, Mapping):
                continue
            for obs in block.get("observations") or []:
                if not isinstance(obs, Mapping):
                    continue
                equipment = obs.get("equipment")
                if not isinstance(equipment, Mapping) or not equipment:
                    continue
                refusals: list[str] = []
                a, a_ref = _equip_area_m2(equipment, "orifice_area")
                if a_ref:
                    refusals.append(a_ref)
                A_s, A_ref = _equip_area_m2(equipment, "sample_surface_area")
                if A_ref:
                    refusals.append(A_ref)
                f = _as_float(_equip_value(equipment, "clausing_factor"))
                multi = _equip_value(equipment, "multi_orifice_series")
                cell = _equip_value(equipment, "cell_material")
                values = obs.get("values")
                obs_units = obs.get("units")
                default_P_units = str(obs_units) if obs_units is not None else None
                P_meas, P_m_ref = _pressure_from_values(
                    values, default_units=default_P_units
                )
                if P_m_ref:
                    refusals.append(P_m_ref)
                P_eq, P_eq_ref = _P_eq_from_values(
                    values, default_units=default_P_units
                )
                if P_eq_ref:
                    refusals.append(P_eq_ref)
                # Multi-orifice series may live under equipment.multi_orifice_series
                multi_ok = False
                if isinstance(multi, list) and len(multi) >= 2:
                    multi_ok = True
                elif isinstance(multi, Mapping) and isinstance(multi.get("points"), list):
                    multi_ok = len(multi["points"]) >= 2
                complete_single = (
                    a is not None
                    and f is not None
                    and A_s is not None
                    and P_meas is not None
                    and P_eq is not None
                    and P_eq > P_meas
                    and not refusals
                )
                cand = GeometryCandidate(
                    source_id=source_id,
                    species_id=str(sid),
                    observation_id=str(obs.get("observation_id") or ""),
                    extract_path=path,
                    observation=dict(obs),
                    orifice_area=a,
                    clausing_factor=f,
                    sample_surface_area=A_s,
                    cell_material=cell,
                    multi_orifice_series=multi,
                    P_meas=P_meas,
                    P_eq=P_eq,
                    complete_for_single_orifice=bool(complete_single),
                    complete_for_multi_orifice=bool(
                        multi_ok and A_s is not None and not any(
                            "sample_surface_area" in r for r in refusals
                        )
                    ),
                    unit_refusals=tuple(refusals),
                )
                out.append(cand)
    return out


def build_inferred_alpha_observation(
    *,
    observation_id: str,
    alpha: float,
    sigma_alpha: float | None,
    parent_source_id: str,
    parent_observation_id: str,
    derivation: str,
    T_range_K: Sequence[float] | None = None,
    phase: str | None = None,
    regime: str = "kems_effusion_motzfeldt_inferred",
    extra_parents: Sequence[Mapping[str, str]] | None = None,
    extra_values: Mapping[str, Any] | None = None,
    locator_note: str | None = None,
) -> dict[str, Any]:
    """Build a schema-valid DRAFT alpha observation marked inferred."""
    parents: list[dict[str, str]] = [
        {"source_id": parent_source_id, "observation_id": parent_observation_id}
    ]
    if extra_parents:
        for p in extra_parents:
            parents.append(
                {
                    "source_id": str(p["source_id"]),
                    "observation_id": str(p["observation_id"]),
                }
            )
    values: dict[str, Any] = {
        "alpha": float(alpha),
        "semantics": "tool_derived_motzfeldt_inferred",
        "derivation": derivation,
        "parents": parents,
    }
    if sigma_alpha is not None and math.isfinite(float(sigma_alpha)):
        values["alpha_sigma"] = float(sigma_alpha)
        values["alpha_range"] = [
            max(0.0, float(alpha) - float(sigma_alpha)),
            float(alpha) + float(sigma_alpha),
        ]
    if extra_values:
        for k, v in extra_values.items():
            if k not in values:
                values[k] = v

    unc: dict[str, Any] = {
        "note": "propagated first-order Motzfeldt inversion uncertainty (tools/motzfeldt.py)",
    }
    if sigma_alpha is not None and math.isfinite(float(sigma_alpha)):
        unc["sigma_absolute"] = float(sigma_alpha)
        if alpha != 0:
            unc["sigma_relative"] = abs(float(sigma_alpha) / float(alpha))

    obs: dict[str, Any] = {
        "observation_id": observation_id,
        "type": "alpha",
        "locator": {
            "note": locator_note
            or (
                f"tool-derived Motzfeldt inversion from parent "
                f"({parent_source_id}, {parent_observation_id}); "
                f"not a published table row"
            ),
            "record": f"motzfeldt_inferred:{parent_source_id}:{parent_observation_id}",
        },
        "regime": regime,
        "units": "dimensionless",
        "inferred": True,
        "inference": derivation,
        "values": values,
        "uncertainty": unc,
        "review_status": "draft",
    }
    if T_range_K is not None and len(T_range_K) == 2:
        obs["T_range_K"] = [float(T_range_K[0]), float(T_range_K[1])]
    if phase is not None:
        obs["phase"] = phase
    return obs


def _multi_points_from_equipment(
    multi: Any,
    *,
    default_clausing: float | None,
    default_A_s_m2: float | None,
    default_P_units: str | None,
    default_area_units: str | None,
) -> tuple[list[OrificePoint] | None, str | None]:
    """Parse multi-orifice points with mandatory unit normalization to Pa / m².

    Returns (points, refusal). Bare numeric fields require an explicit unit
    (per-point, nested mapping, series default, or parent observation units);
    absent/unknown units are a typed refusal, never an SI assumption.
    ``default_A_s_m2`` is already SI (from equipment normalization).
    """
    raw_points: list[Any]
    series_P_units = default_P_units
    series_area_units = default_area_units
    if isinstance(multi, list):
        raw_points = multi
    elif isinstance(multi, Mapping):
        raw_points = list(multi.get("points") or [])
        if multi.get("P_meas_units") is not None:
            series_P_units = str(multi["P_meas_units"])
        elif multi.get("pressure_units") is not None:
            series_P_units = str(multi["pressure_units"])
        if multi.get("orifice_area_units") is not None:
            series_area_units = str(multi["orifice_area_units"])
        elif multi.get("area_units") is not None:
            series_area_units = str(multi["area_units"])
    else:
        return None, "multi_orifice_series: not a list or mapping"
    if len(raw_points) < 2:
        return None, "multi_orifice_series: need ≥2 points"
    points: list[OrificePoint] = []
    for i, raw in enumerate(raw_points):
        if not isinstance(raw, Mapping):
            return None, f"multi_orifice_series.points[{i}]: not a mapping"
        # --- pressure (Pa) ---
        P_m: float | None = None
        # Explicit Pa keys are unit-identified.
        for pk in ("P_meas_Pa", "P_Pa", "p_Pa"):
            if pk in raw and raw[pk] is not None:
                P_m = _as_float(raw[pk])
                break
        if P_m is None:
            p_raw = _first_present(raw, "P_meas", "p_meas", "P")
            if isinstance(p_raw, Mapping):
                pv = _as_float(p_raw.get("value"))
                pu = p_raw.get("units")
                if pv is not None:
                    try:
                        P_m = normalize_pressure_pa(
                            pv,
                            str(pu) if pu is not None else series_P_units,
                            field=f"points[{i}].P_meas",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
            elif p_raw is not None:
                pv = _as_float(p_raw)
                pu = (
                    raw.get("P_meas_units")
                    or raw.get("pressure_units")
                    or raw.get("units")
                    or series_P_units
                )
                if pv is not None:
                    try:
                        P_m = normalize_pressure_pa(
                            pv,
                            str(pu) if pu is not None else None,
                            field=f"points[{i}].P_meas",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
        # --- orifice area (m²) ---
        a: float | None = None
        # orifice_area_m2 is an explicit SI identification (like _Pa).
        if "orifice_area_m2" in raw and raw["orifice_area_m2"] is not None:
            a = _as_float(raw["orifice_area_m2"])
        if a is None:
            a_raw = _first_present(raw, "orifice_area", "a")
            if isinstance(a_raw, Mapping):
                av = _as_float(a_raw.get("value"))
                au = a_raw.get("units")
                if av is not None:
                    try:
                        a = normalize_area_m2(
                            av,
                            str(au) if au is not None else series_area_units,
                            field=f"points[{i}].orifice_area",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
            elif a_raw is not None:
                av = _as_float(a_raw)
                au = (
                    raw.get("orifice_area_units")
                    or raw.get("area_units")
                    or series_area_units
                )
                if av is not None:
                    try:
                        a = normalize_area_m2(
                            av,
                            str(au) if au is not None else None,
                            field=f"points[{i}].orifice_area",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
        # --- Clausing (dimensionless; 0.0 is valid and must not fail open) ---
        f_raw = _first_present(raw, "clausing_factor", "f", "clausing")
        f = _as_float(f_raw) if f_raw is not None else default_clausing
        # --- per-point A_s (optional; default already m²) ---
        A_s = default_A_s_m2
        A_raw = _first_present(raw, "sample_surface_area", "A_s")
        if A_raw is not None:
            if isinstance(A_raw, Mapping):
                Av = _as_float(A_raw.get("value"))
                Au = A_raw.get("units")
                if Av is not None:
                    try:
                        A_s = normalize_area_m2(
                            Av,
                            str(Au) if Au is not None else series_area_units,
                            field=f"points[{i}].sample_surface_area",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
            else:
                Av = _as_float(A_raw)
                Au = (
                    raw.get("sample_surface_area_units")
                    or raw.get("area_units")
                    or series_area_units
                )
                if Av is not None:
                    try:
                        A_s = normalize_area_m2(
                            Av,
                            str(Au) if Au is not None else None,
                            field=f"points[{i}].sample_surface_area",
                        )
                    except MotzfeldtUnitError as exc:
                        return None, str(exc)
        if P_m is None or a is None or f is None:
            return None, (
                f"multi_orifice_series.points[{i}]: incomplete after unit "
                f"normalization (P_meas={P_m}, a={a}, f={f})"
            )
        points.append(
            OrificePoint(
                P_meas=P_m,
                orifice_area=a,
                clausing_factor=f,
                sample_surface_area=A_s,
            )
        )
    return points, None


def derive_alpha_for_candidate(cand: GeometryCandidate) -> dict[str, Any] | None:
    """Run Motzfeldt (single or multi) for one geometry candidate.

    Returns a result dict with ``mode``, ``result``, and ``observation`` ready
    to append, or an ``error`` dict on typed unit/domain refusal, or None if
    the candidate is incomplete.
    """
    parent_obs = cand.observation
    T_range = parent_obs.get("T_range_K")
    phase = parent_obs.get("phase")
    obs_units = parent_obs.get("units")
    default_P_units = str(obs_units) if obs_units is not None else None

    if cand.unit_refusals and not cand.complete_for_multi_orifice:
        # Single-orifice path blocked by unit refusal — surface it.
        if not cand.complete_for_single_orifice:
            return {
                "error": "; ".join(cand.unit_refusals),
                "mode": "unit_refusal",
                "refusal": "MotzfeldtUnitError",
            }

    if cand.complete_for_multi_orifice and cand.sample_surface_area is not None:
        # Series-level area units default: only when equipment declared them
        # (already consumed into sample_surface_area m²). Per-point orifice
        # areas still need their own units unless series carries orifice_area_units.
        points, multi_ref = _multi_points_from_equipment(
            cand.multi_orifice_series,
            default_clausing=cand.clausing_factor,
            default_A_s_m2=cand.sample_surface_area,
            default_P_units=default_P_units,
            default_area_units=None,
        )
        if multi_ref is not None:
            return {
                "error": multi_ref,
                "mode": "multi_orifice",
                "refusal": "MotzfeldtUnitError",
            }
        if points is not None:
            try:
                multi = multi_orifice_alpha(
                    points, sample_surface_area=cand.sample_surface_area
                )
            except (ValueError, MotzfeldtDomainError, MotzfeldtUnitError) as exc:
                return {
                    "error": str(exc),
                    "mode": "multi_orifice",
                    "refusal": type(exc).__name__,
                }
            # P_eq_Pa identification: every point P_meas was normalized to Pa
            # above, so the linear fit intercept b = 1/P_eq is in Pa⁻¹ and
            # P_eq = 1/b is in Pa. The key name is that identification.
            derivation = (
                f"Multi-orifice Motzfeldt/Whitman extrapolation "
                f"(tools/motzfeldt.py::multi_orifice_alpha): "
                f"linear fit of 1/P_meas vs f*a/A_s over {multi.n_points} orifices "
                f"→ P_eq={multi.P_eq:.6g} Pa, alpha={multi.alpha:.6g} "
                f"(intercept={multi.intercept:.6g} Pa^-1, slope={multi.slope:.6g}"
                f"{f', R²={multi.r_squared:.4f}' if multi.r_squared is not None else ''}). "
                f"All P_meas normalized to Pa and areas to m² before the fit, so "
                f"P_eq=1/intercept is in Pa (P_eq_Pa key is that identification). "
                f"Parents: ({cand.source_id}, {cand.observation_id}). "
                f"Formula: P_eq/P_meas = 1 + f*a/(alpha*A_s); "
                f"cite Sossi & Fegley 2018 Rev. Mineral. Geochem. 84 eq. 18; "
                f"Costa & Jacobson 2015 NASA NTRS 20150002321 multi-cell method."
            )
            oid = f"{cand.observation_id}_motzfeldt_multi_alpha"
            obs = build_inferred_alpha_observation(
                observation_id=oid,
                alpha=multi.alpha,
                sigma_alpha=None,
                parent_source_id=cand.source_id,
                parent_observation_id=cand.observation_id,
                derivation=derivation,
                T_range_K=T_range if isinstance(T_range, (list, tuple)) else None,
                phase=str(phase) if phase is not None else None,
                extra_values={
                    # Justified: multi_orifice_alpha SI contract + unit
                    # normalization of every P_meas to Pa before the fit.
                    "P_eq_Pa": multi.P_eq,
                    "method": "multi_orifice_extrapolation",
                    "n_orifices": multi.n_points,
                    "fit_r_squared": multi.r_squared,
                },
            )
            return {
                "mode": "multi_orifice",
                "result": multi.as_dict(),
                "observation": obs,
            }

    if cand.complete_for_single_orifice:
        assert cand.P_eq is not None and cand.P_meas is not None
        assert cand.orifice_area is not None and cand.clausing_factor is not None
        assert cand.sample_surface_area is not None
        try:
            res = invert_alpha(
                MotzfeldtInputs(
                    P_eq=cand.P_eq,
                    P_meas=cand.P_meas,
                    orifice_area=cand.orifice_area,
                    clausing_factor=cand.clausing_factor,
                    sample_surface_area=cand.sample_surface_area,
                )
            )
        except (ValueError, MotzfeldtDomainError) as exc:
            return {
                "error": str(exc),
                "mode": "single_orifice",
                "refusal": type(exc).__name__,
            }
        derivation = (
            f"Single-orifice Motzfeldt/Whitman inversion "
            f"(tools/motzfeldt.py::invert_alpha): "
            f"alpha = (f*a)/(A_s*(P_eq/P_meas - 1)) with "
            f"P_eq={cand.P_eq:.6g} Pa, P_meas={cand.P_meas:.6g} Pa, "
            f"a={cand.orifice_area:.6g} m2, f={cand.clausing_factor:.6g}, "
            f"A_s={cand.sample_surface_area:.6g} m2 → alpha={res.alpha:.6g}"
            f"{f' ± {res.sigma_alpha:.6g}' if res.sigma_alpha is not None else ''}. "
            f"Parents: ({cand.source_id}, {cand.observation_id}). "
            f"Formula: P_eq/P_meas = 1 + f*a/(alpha*A_s); "
            f"cite Sossi & Fegley 2018 Rev. Mineral. Geochem. 84 eq. 18; "
            f"sanity: Costa & Jacobson 2015 Fe α band 0.011–0.020 on olivine."
        )
        oid = f"{cand.observation_id}_motzfeldt_alpha"
        obs = build_inferred_alpha_observation(
            observation_id=oid,
            alpha=res.alpha,
            sigma_alpha=res.sigma_alpha,
            parent_source_id=cand.source_id,
            parent_observation_id=cand.observation_id,
            derivation=derivation,
            T_range_K=T_range if isinstance(T_range, (list, tuple)) else None,
            phase=str(phase) if phase is not None else None,
            extra_values={
                "P_eq_Pa": cand.P_eq,
                "P_meas_Pa": cand.P_meas,
                "method": "single_orifice_inversion",
                "orifice_to_sample_ratio": res.orifice_to_sample_ratio,
                "P_eq_over_P_meas": res.P_eq_over_P_meas,
            },
        )
        return {
            "mode": "single_orifice",
            "result": res.as_dict(),
            "observation": obs,
        }

    if cand.unit_refusals:
        return {
            "error": "; ".join(cand.unit_refusals),
            "mode": "unit_refusal",
            "refusal": "MotzfeldtUnitError",
        }
    return None


def _append_fidelity_sample(doc: dict[str, Any], obs: Mapping[str, Any], species_id: str) -> None:
    """Append or replace the fidelity sample for this derived observation path."""
    samples = doc.setdefault("fidelity_samples", [])
    if not isinstance(samples, list):
        doc["fidelity_samples"] = []
        samples = doc["fidelity_samples"]
    oid = obs["observation_id"]
    alpha = obs["values"]["alpha"]
    path = f"species.{species_id}.observations[{oid}].values.alpha"
    # Idempotent: replace existing sample for the same path (P3).
    samples[:] = [
        s
        for s in samples
        if not (isinstance(s, Mapping) and s.get("path") == path)
    ]
    samples.append(
        {
            "path": path,
            "value": alpha,
            "note": (
                "tool-derived Motzfeldt DRAFT alpha; arithmetic pin from "
                "tools/motzfeldt.py (not a published table cell)"
            ),
            "locator": obs.get("locator"),
        }
    )


def write_draft_alpha_observations(
    *,
    extracts_dir: Path | None = None,
    dry_run: bool = True,
    only_source_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Derive Motzfeldt alphas and (unless dry_run) append to extract files.

    Every write is validated with :func:`validate_extract_document` before the
    file is replaced. Invalid results are refused (never bypass the validator).
    """
    d = extracts_dir or EXTRACTS_DIR
    only = set(only_source_ids) if only_source_ids else None
    candidates = scan_geometry_candidates(d)
    reports: list[dict[str, Any]] = []

    # Group by extract path so we can batch appends + one validate per file.
    by_path: dict[Path, list[tuple[GeometryCandidate, dict[str, Any]]]] = {}
    for cand in candidates:
        if only is not None and cand.source_id not in only:
            continue
        derived = derive_alpha_for_candidate(cand)
        if derived is None or "observation" not in derived:
            reports.append(
                {
                    "source_id": cand.source_id,
                    "observation_id": cand.observation_id,
                    "status": "skipped_incomplete_or_error",
                    "detail": derived,
                }
            )
            continue
        by_path.setdefault(cand.extract_path, []).append((cand, derived))

    for path, items in by_path.items():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, Mapping):
            reports.append({"path": str(path), "status": "error", "detail": "not a mapping"})
            continue
        doc = copy.deepcopy(dict(doc))
        species_map = doc.setdefault("species", {})
        appended: list[str] = []
        for cand, derived in items:
            obs = derived["observation"]
            oid = obs["observation_id"]
            block = species_map.setdefault(cand.species_id, {"observations": []})
            if not isinstance(block, dict):
                continue
            obs_list = block.setdefault("observations", [])
            if not isinstance(obs_list, list):
                continue
            # Idempotent: replace existing tool-derived row with same id.
            obs_list[:] = [
                o
                for o in obs_list
                if not (isinstance(o, Mapping) and o.get("observation_id") == oid)
            ]
            obs_list.append(obs)
            _append_fidelity_sample(doc, obs, cand.species_id)
            appended.append(oid)
            reports.append(
                {
                    "source_id": cand.source_id,
                    "species_id": cand.species_id,
                    "parent_observation_id": cand.observation_id,
                    "derived_observation_id": oid,
                    "mode": derived["mode"],
                    "alpha": obs["values"]["alpha"],
                    "status": "dry_run" if dry_run else "written",
                    "result": derived.get("result"),
                }
            )

        if not appended:
            continue

        # Ensure extraction.method notes the tool pass when we write.
        extraction = doc.setdefault("extraction", {})
        if isinstance(extraction, dict):
            method = str(extraction.get("method") or "")
            tag = "motzfeldt_inferred_alpha"
            if tag not in method:
                extraction["method"] = (method + f"; {tag}").strip("; ")
            extraction.setdefault("date", date.today().isoformat())
            extraction.setdefault("worker", "t511-motzfeldt")

        errs = validate_extract_document(
            doc, path=path, expected_source_id=path.stem
        )
        if errs:
            for item in reports:
                if item.get("source_id") == doc.get("source_id") and item.get("status") in {
                    "dry_run",
                    "written",
                }:
                    item["status"] = "refused_by_validator"
                    item["validation_errors"] = errs
            continue

        if not dry_run:
            # Atomic replace: serialize + validate a temp sibling, then
            # os.replace. A crash or post-serialize validation failure must
            # leave the original extract untouched (review P2).
            # Temp filename stem ≠ source_id, so re-validate via
            # validate_extract_document with expected_source_id=path.stem.
            import os
            import tempfile

            text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
            tmp_path: Path | None = None
            try:
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    dir=str(path.parent),
                )
                tmp_path = Path(tmp_name)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                reloaded = yaml.safe_load(tmp_path.read_text(encoding="utf-8"))
                disk_errs = validate_extract_document(
                    reloaded if isinstance(reloaded, Mapping) else {},
                    path=tmp_path,
                    expected_source_id=path.stem,
                )
                if disk_errs:
                    for item in reports:
                        if item.get("source_id") == doc.get("source_id"):
                            item["status"] = "disk_validation_failed"
                            item["validation_errors"] = disk_errs
                    tmp_path.unlink(missing_ok=True)
                    continue
                os.replace(str(tmp_path), str(path))
                tmp_path = None
            except Exception as exc:  # noqa: BLE001
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                for item in reports:
                    if item.get("source_id") == doc.get("source_id") and item.get(
                        "status"
                    ) in {"dry_run", "written"}:
                        item["status"] = "write_failed"
                        item["detail"] = str(exc)

    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Motzfeldt/Whitman equipment-metadata consumer (t-511)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inv = sub.add_parser("invert", help="Single-orifice Motzfeldt α inversion")
    p_inv.add_argument("--P-eq", dest="P_eq", type=float, required=True)
    p_inv.add_argument("--P-meas", dest="P_meas", type=float, required=True)
    p_inv.add_argument("--orifice-area", type=float, required=True)
    p_inv.add_argument("--clausing", type=float, required=True)
    p_inv.add_argument("--sample-area", type=float, required=True)
    p_inv.add_argument("--sigma-P-eq", type=float, default=None)
    p_inv.add_argument("--sigma-P-meas", type=float, default=None)
    p_inv.add_argument("--sigma-orifice-area", type=float, default=None)
    p_inv.add_argument("--sigma-clausing", type=float, default=None)
    p_inv.add_argument("--sigma-sample-area", type=float, default=None)

    p_multi = sub.add_parser("multi-orifice", help="Multi-orifice extrapolation")
    p_multi.add_argument(
        "--point",
        action="append",
        required=True,
        help="P_meas,orifice_area[,clausing] (repeatable, ≥2)",
    )
    p_multi.add_argument("--sample-area", type=float, required=True)

    p_scan = sub.add_parser("scan", help="Scan extracts for Motzfeldt geometry")
    p_scan.add_argument("--extracts-dir", type=Path, default=EXTRACTS_DIR)

    p_write = sub.add_parser(
        "write-drafts",
        help="Write DRAFT inferred alpha rows into the extract store",
    )
    p_write.add_argument("--extracts-dir", type=Path, default=EXTRACTS_DIR)
    p_write.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Validate and report without writing (default)",
    )
    p_write.add_argument(
        "--commit-writes",
        action="store_true",
        help="Actually write validated DRAFT rows into extract YAML files",
    )
    p_write.add_argument(
        "--only-source",
        action="append",
        default=None,
        help="Restrict to these source_id values (repeatable)",
    )

    p_cell = sub.add_parser("classify-cell", help="Classify cell_material pO2 boundary")
    p_cell.add_argument("material", type=str)

    p_fwd = sub.add_parser("forward", help="Forward Motzfeldt ratio from known alpha")
    p_fwd.add_argument("--alpha", type=float, required=True)
    p_fwd.add_argument("--orifice-area", type=float, required=True)
    p_fwd.add_argument("--clausing", type=float, required=True)
    p_fwd.add_argument("--sample-area", type=float, required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "invert":
        res = invert_alpha(
            MotzfeldtInputs(
                P_eq=args.P_eq,
                P_meas=args.P_meas,
                orifice_area=args.orifice_area,
                clausing_factor=args.clausing,
                sample_surface_area=args.sample_area,
                sigma_P_eq=args.sigma_P_eq,
                sigma_P_meas=args.sigma_P_meas,
                sigma_orifice_area=args.sigma_orifice_area,
                sigma_clausing_factor=args.sigma_clausing,
                sigma_sample_surface_area=args.sigma_sample_area,
            )
        )
        print(yaml.safe_dump(res.as_dict(), sort_keys=False))
        return 0

    if args.cmd == "multi-orifice":
        points: list[OrificePoint] = []
        for raw in args.point:
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 2:
                print(f"bad --point {raw!r}; expected P_meas,orifice_area[,clausing]", file=sys.stderr)
                return 2
            P_m = float(parts[0])
            a = float(parts[1])
            f = float(parts[2]) if len(parts) > 2 else 1.0
            points.append(OrificePoint(P_meas=P_m, orifice_area=a, clausing_factor=f))
        res = multi_orifice_alpha(points, sample_surface_area=args.sample_area)
        print(yaml.safe_dump(res.as_dict(), sort_keys=False))
        return 0

    if args.cmd == "scan":
        cands = scan_geometry_candidates(args.extracts_dir)
        rows = []
        for c in cands:
            rows.append(
                {
                    "source_id": c.source_id,
                    "species_id": c.species_id,
                    "observation_id": c.observation_id,
                    "complete_single": c.complete_for_single_orifice,
                    "complete_multi": c.complete_for_multi_orifice,
                    "orifice_area": c.orifice_area,
                    "clausing_factor": c.clausing_factor,
                    "sample_surface_area": c.sample_surface_area,
                    "cell_material": c.cell_material,
                    "P_meas": c.P_meas,
                    "P_eq": c.P_eq,
                }
            )
        print(
            yaml.safe_dump(
                {
                    "n_candidates": len(rows),
                    "n_complete_single": sum(1 for r in rows if r["complete_single"]),
                    "n_complete_multi": sum(1 for r in rows if r["complete_multi"]),
                    "note": (
                        "No landed extract currently carries a complete Motzfeldt "
                        "geometry set; use tests/fixtures/literature/motzfeldt-synthetic.yaml"
                        if not any(r["complete_single"] or r["complete_multi"] for r in rows)
                        else "geometry present"
                    ),
                    "candidates": rows,
                },
                sort_keys=False,
            )
        )
        return 0

    if args.cmd == "write-drafts":
        dry = not args.commit_writes
        reports = write_draft_alpha_observations(
            extracts_dir=args.extracts_dir,
            dry_run=dry,
            only_source_ids=args.only_source,
        )
        print(yaml.safe_dump({"dry_run": dry, "reports": reports}, sort_keys=False))
        return 0

    if args.cmd == "classify-cell":
        print(yaml.safe_dump(classify_cell_material(args.material), sort_keys=False))
        return 0

    if args.cmd == "forward":
        R = motzfeldt_ratio(
            orifice_area=args.orifice_area,
            clausing_factor=args.clausing,
            sample_surface_area=args.sample_area,
            alpha=args.alpha,
        )
        print(yaml.safe_dump({"P_eq_over_P_meas": R}, sort_keys=False))
        return 0

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
