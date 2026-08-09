"""Species-class evaporation-α model (ADR-001) — DATA + diagnostics only.

This module is the EC1 / class-α *scaffold*: it holds the class table with
per-class provenance, maps runtime species onto classes, and reports class α,
residual band, and interface-resistance share at given conditions.

**Golden-neutral / instrument-before-gate.** Nothing here is read by the
production flux path (``_load_evaporation_alpha_by_species``). Behavior-
changing adoption is a separate gated landing.

Design authority:
``docs-private/research/2026-08-09-evap-class-model/design.md``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from engines.builtin.evaporation_flux import (
    SeriesEvaporationFlux,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.evaporation import _load_evaporation_alpha_by_species

# ---------------------------------------------------------------------------
# Paths / store
# ---------------------------------------------------------------------------

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_EXTRACTS_DIR: Final[Path] = _REPO_ROOT / "data" / "literature" / "extracts"
_VAPOR_PRESSURES_PATH: Final[Path] = _REPO_ROOT / "data" / "vapor_pressures.yaml"

# ---------------------------------------------------------------------------
# Comparability gate (mirror of rps-evid si-comparability / rail_system_class)
# ---------------------------------------------------------------------------

RAIL_COMPARABLE_SYSTEM_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "silicate_melt",
        "solid_solution_silicate",
        "pure_oxide_condensed",
    }
)

RAIL_INCOMPARABLE_SYSTEM_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "pure_element_condensed",
        "molten_metal",
        "solid_film_growth",
    }
)

AlphaForm = Literal["central", "upper_bound"]

# Programme pin (EC2 + ADR-001): half-dex residual on every production class.
DEFAULT_RESIDUAL_DEX: Final[float] = 0.5

# ---------------------------------------------------------------------------
# Evidence row identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRow:
    """One extract-store observation that grounds (or fences) a class.

    ``comparable`` is the Si-comparability gate outcome. Non-comparable rows
    are retained as *negative evidence* / fences and never enter residual
    scoring for the production melt carrier.

    ``alpha`` is the numeric extract pin when the store carries a scalar (or a
    well-defined range mid used only for band-coverage checks). Fence rows may
    omit it. Band coverage for comparable non-fence rows with a numeric alpha
    is enforced by :func:`assert_evidence_rows_present`.
    """

    source_id: str
    observation_id: str
    species: str
    system_class: str
    transformation_class: str | None = None
    alpha_note: str = ""
    comparable: bool = True
    role: Literal["grounding", "fence", "regime_pole"] = "grounding"
    alpha: float | None = None


@dataclass(frozen=True)
class EvaporationClass:
    """One production (or survey) α class."""

    class_id: str
    label: str
    alpha_form: AlphaForm
    alpha_value: float
    residual_dex: float
    system_classes: tuple[str, ...]
    transformation_class: str | None
    species: tuple[str, ...]
    evidence: tuple[EvidenceRow, ...]
    notes: str = ""

    def band_low(self) -> float | None:
        """Lower edge of the residual band on a central α (None for upper_bound)."""

        if self.alpha_form != "central":
            return None
        if self.alpha_value <= 0.0:
            return None
        return self.alpha_value * (10.0 ** (-self.residual_dex))

    def band_high(self) -> float | None:
        """Upper edge of the residual band (capped at 1.0 for a physical α)."""

        if self.alpha_form != "central":
            return min(1.0, self.alpha_value)
        return min(1.0, self.alpha_value * (10.0 ** self.residual_dex))


# ---------------------------------------------------------------------------
# Class table — DATA (provenance in EvidenceRow, not prose-only)
# ---------------------------------------------------------------------------

_SIO_EVIDENCE: tuple[EvidenceRow, ...] = (
    EvidenceRow(
        source_id="kems-007-costa-2015",
        observation_id="costa_2015_sio_olivine_kems_alpha_multicell",
        species="SiO",
        system_class="solid_solution_silicate",
        transformation_class="network_former_dissociation",
        alpha_note="KEMS band ~0.003-0.036; Motzfeldt geometry ABSENT",
        comparable=True,
        role="grounding",
        # Range-only in extract; log-mid for band coverage (√(0.003·0.036)).
        alpha=math.sqrt(0.003 * 0.036),
    ),
    EvidenceRow(
        source_id="costa-jacobson-2015",
        observation_id="costa_jacobson_2015_sio_olivine_kems",
        species="SiO",
        system_class="solid_solution_silicate",
        transformation_class="network_former_dissociation",
        alpha_note="duplicate/pilot of Costa multicell SiO band",
        comparable=True,
        role="grounding",
        alpha=math.sqrt(0.003 * 0.036),
    ),
    EvidenceRow(
        source_id="kems-005-fedkin-2006",
        observation_id="fedkin_2006_sio_hashimoto_langmuir_table3",
        species="SiO",
        system_class="silicate_melt",
        transformation_class="network_former_dissociation",
        alpha_note="Hashimoto Langmuir series 0.12-0.21",
        comparable=True,
        role="grounding",
        alpha=0.17,  # series mid (matches table3 pin)
    ),
    EvidenceRow(
        source_id="fedkin-grossman-ghiorso-2006",
        observation_id="fedkin_2006_table3_sio_hashimoto_langmuir",
        species="SiO",
        system_class="silicate_melt",
        transformation_class="network_former_dissociation",
        alpha_note="mid ~0.17",
        comparable=True,
        role="grounding",
        alpha=0.17,
    ),
    EvidenceRow(
        source_id="kems-037-richter-2002",
        observation_id="richter_2002_sio_cai_langmuir_gamma",
        species="SiO",
        system_class="silicate_melt",
        transformation_class="network_former_dissociation",
        alpha_note="Type-B CAI-like melt; gamma matrix",
        comparable=True,
        role="grounding",
        alpha=None,  # matrix, no single scalar pin in extract
    ),
    EvidenceRow(
        source_id="kems-011-wetzel-gail-2013",
        observation_id="wetzel_gail_2013_sio_growth_alpha_arrhenius",
        species="SiO",
        system_class="solid_film_growth",
        transformation_class="congruent_no_transformation",
        alpha_note="growth/condensation alpha — fence, not melt alpha_e",
        comparable=False,
        role="fence",
        alpha=None,
    ),
)

_REDOX_EVIDENCE: tuple[EvidenceRow, ...] = (
    EvidenceRow(
        source_id="kems-007-costa-2015",
        observation_id="costa_2015_fe_olivine_kems_alpha_multicell",
        species="Fe",
        system_class="solid_solution_silicate",
        transformation_class="redox_reduction_required",
        alpha_note="0.02 KEMS; geometry ABSENT (b-116 open)",
        comparable=True,
        role="grounding",
        alpha=0.02,
    ),
    EvidenceRow(
        source_id="costa-jacobson-2015",
        observation_id="costa_jacobson_2015_fe_olivine_kems",
        species="Fe",
        system_class="solid_solution_silicate",
        transformation_class="redox_reduction_required",
        alpha_note="0.02",
        comparable=True,
        role="grounding",
        alpha=0.02,
    ),
    EvidenceRow(
        source_id="kems-005-fedkin-2006",
        observation_id="fedkin_2006_fe_hashimoto_langmuir_table3",
        species="Fe",
        system_class="silicate_melt",
        transformation_class="redox_reduction_required",
        alpha_note="0.24 Langmuir free-evap",
        comparable=True,
        role="grounding",
        alpha=0.24,
    ),
    EvidenceRow(
        source_id="fedkin-grossman-ghiorso-2006",
        observation_id="fedkin_2006_table3_fe_hashimoto_langmuir",
        species="Fe",
        system_class="silicate_melt",
        transformation_class="redox_reduction_required",
        alpha_note="0.24",
        comparable=True,
        role="grounding",
        alpha=0.24,
    ),
    EvidenceRow(
        source_id="kems-005-fedkin-2006",
        observation_id="fedkin_2006_mg_hashimoto_langmuir_table3",
        species="Mg",
        system_class="silicate_melt",
        transformation_class="redox_reduction_required",
        alpha_note="0.24",
        comparable=True,
        role="grounding",
        alpha=0.24,
    ),
    EvidenceRow(
        source_id="fedkin-grossman-ghiorso-2006",
        observation_id="fedkin_2006_table3_mg_hashimoto_langmuir",
        species="Mg",
        system_class="silicate_melt",
        transformation_class="redox_reduction_required",
        alpha_note="0.27",
        comparable=True,
        role="grounding",
        alpha=0.27,
    ),
    EvidenceRow(
        source_id="kems-037-richter-2002",
        observation_id="richter_2002_mg_cai_langmuir_gamma",
        species="Mg",
        system_class="silicate_melt",
        transformation_class="redox_reduction_required",
        alpha_note="0.04 gamma-derived",
        comparable=True,
        role="grounding",
        alpha=0.04,
    ),
    EvidenceRow(
        source_id="kems-003-pound-1972",
        observation_id="pound_1972_fe_solid_alpha_mccabe",
        species="Fe",
        system_class="pure_element_condensed",
        transformation_class="congruent_no_transformation",
        alpha_note="0.9 solid metal — fence",
        comparable=False,
        role="fence",
        alpha=0.9,
    ),
)

_MODIFIER_EVIDENCE: tuple[EvidenceRow, ...] = (
    EvidenceRow(
        source_id="kems-005-fedkin-2006",
        observation_id="fedkin_2006_na_yu_langmuir",
        species="Na",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="0.26 vacuum Langmuir (Yu)",
        comparable=True,
        role="regime_pole",
        alpha=0.26,
    ),
    EvidenceRow(
        source_id="fedkin-grossman-ghiorso-2006",
        observation_id="fedkin_2006_yu_na_vacuum_langmuir",
        species="Na",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="0.26",
        comparable=True,
        role="regime_pole",
        alpha=0.26,
    ),
    EvidenceRow(
        source_id="kems-005-fedkin-2006",
        observation_id="fedkin_2006_k_yu_langmuir",
        species="K",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="0.13 vacuum",
        comparable=True,
        role="regime_pole",
        alpha=0.13,
    ),
    EvidenceRow(
        source_id="fedkin-grossman-ghiorso-2006",
        observation_id="fedkin_2006_yu_k_vacuum_langmuir",
        species="K",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="0.13",
        comparable=True,
        role="regime_pole",
        alpha=0.13,
    ),
    EvidenceRow(
        source_id="kems-012-sossi-2019",
        observation_id="sossi_2019_na_alpha_e_authors_adopted_unity",
        species="Na",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="1.0 open-furnace apparent",
        comparable=True,
        role="regime_pole",
        alpha=1.0,
    ),
    EvidenceRow(
        source_id="kems-012-sossi-2019",
        observation_id="sossi_2019_k_open_furnace_alpha_e_context",
        species="K",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="1.0 open-furnace apparent",
        comparable=True,
        role="regime_pole",
        alpha=1.0,
    ),
    EvidenceRow(
        source_id="sossi-et-al-2019",
        observation_id="sossi_2019_na_open_furnace_apparent",
        species="Na",
        system_class="silicate_melt",
        transformation_class="network_modifier_desorption",
        alpha_note="1.0 open-furnace apparent",
        comparable=True,
        role="regime_pole",
        alpha=1.0,
    ),
)

_OXIDE_SURVEY_EVIDENCE: tuple[EvidenceRow, ...] = (
    EvidenceRow(
        source_id="kems-008-schaefer-fegley-2004",
        observation_id="schaefer_fegley_2004_sio_alpha_s_survey",
        species="SiO",
        system_class="pure_oxide_condensed",
        transformation_class="network_former_dissociation",
        alpha_note="survey 0.2",
        comparable=True,
        role="grounding",
        alpha=0.2,
    ),
    EvidenceRow(
        source_id="kems-008-schaefer-fegley-2004",
        observation_id="schaefer_fegley_2004_mg_forsterite_alpha_s_survey",
        species="Mg",
        system_class="pure_oxide_condensed",
        transformation_class="redox_reduction_required",
        alpha_note="survey 0.2",
        comparable=True,
        role="grounding",
        alpha=0.2,
    ),
)

_MARKED_IDEAL_FENCES: tuple[EvidenceRow, ...] = (
    EvidenceRow(
        source_id="safarian-engh-2013-si-pure-langmuir",
        observation_id="safarian_engh_2013_si_pure_langmuir",
        species="Si",
        system_class="pure_element_condensed",
        transformation_class="congruent_no_transformation",
        alpha_note="pure-Si 1.0 — category error for melt/SiO carrier",
        comparable=False,
        role="fence",
        alpha=1.0,
    ),
    EvidenceRow(
        source_id="kems-003-pound-1972",
        observation_id="pound_1972_cr_solid_alpha_mccabe",
        species="Cr",
        system_class="pure_element_condensed",
        transformation_class="congruent_no_transformation",
        alpha_note="solid Cr 0.9 — not silicate-melt redox",
        comparable=False,
        role="fence",
        alpha=0.9,
    ),
    EvidenceRow(
        source_id="kems-001-homma-1966",
        observation_id="homma_1966_mn_olette_alpha_exp_table1",
        species="Mn",
        system_class="molten_metal",
        transformation_class="congruent_no_transformation",
        alpha_note="olette alpha_B >> 1 — not HKL",
        comparable=False,
        role="fence",
        alpha=None,
    ),
)


# ---------------------------------------------------------------------------
# Class centrals derived from comparable rows (P0 b-153)
# ---------------------------------------------------------------------------
#
# silicate_melt_cation_redox — recompute from comparable grounding alphas.
# Pilot/kems mirrors of the same measurement are collapsed to one pole:
#
#   Costa Fe olivine KEMS (costa_2015_fe… / costa_jacobson…):     0.02
#   Fedkin Fe Hashimoto Langmuir (fedkin_2006_fe… / table3_fe…):  0.24
#   Fedkin Mg Hashimoto (kems 0.24 + table3 0.27) → √(0.24·0.27): 0.254558
#   Richter Mg CAI γ (richter_2002_mg…):                          0.04
#
# gmean = exp(mean(ln α_i))
#       = exp( (ln 0.02 + ln 0.24 + ln √(0.24·0.27) + ln 0.04) / 4 )
#       = 0.083612…  → class central 0.084 (3 s.f.)
#
# Cross-check: Fe-pole-only gmean √(0.02·0.24) = 0.0693, which alone already
# refutes the prior 0.10 central. Full-class gmean is used (not Fe-only).
#
# Band: every grounded α ∈ {0.02, 0.24, 0.27, 0.04} must lie in
#   [α_c · 10^{-σ}, α_c · 10^{+σ}].
#   max_i |log10(α_i / 0.084)| = |log10(0.02/0.084)| = 0.623 → residual 0.63 dex.
#   (Programme 0.5-dex pin is a floor, not a ceiling: Fe pole-to-pole ~1.08 dex
#   is irreducible without Motzfeldt; residual widens to cover, not split, until
#   a comparable Motzfeldt-corrected Fe row lands.)
#
# Unit check: α dimensionless; gmean and 10^{±σ} dimensionless. ✔
# Limiting cases: σ→0 collapses to a point estimate (false precision here);
# σ ≥ half-span of log poles is the minimum honest band.

_REDOX_CENTRAL_ALPHA: Final[float] = 0.084
_REDOX_RESIDUAL_DEX: Final[float] = 0.63

# SiO-class: keep rail prior_scalar 0.04 as central; residual widens so every
# stored comparable numeric pin (Costa log-mid ~0.0104, Fedkin 0.17) is covered.
# max |log10(α/0.04)| over {0.010392, 0.17} ≈ 0.628 → 0.63 dex.
_SIO_RESIDUAL_DEX: Final[float] = 0.63

# Na/K: regime poles 0.13 / 0.26 / 1.0 around central 0.30.
# max |log10(α/0.30)| = log10(1.0/0.30) ≈ 0.523 → 0.53 dex (band_high caps at 1).
_MODIFIER_RESIDUAL_DEX: Final[float] = 0.53


EVAPORATION_CLASSES: Final[dict[str, EvaporationClass]] = {
    "silicate_melt_network_former": EvaporationClass(
        class_id="silicate_melt_network_former",
        label="silicate-melt incongruent network former (SiO-class)",
        alpha_form="central",
        alpha_value=0.04,
        residual_dex=_SIO_RESIDUAL_DEX,
        system_classes=("silicate_melt", "solid_solution_silicate"),
        transformation_class="network_former_dissociation",
        species=("SiO", "Si", "Si2", "Si3", "SiO2_gas"),
        evidence=_SIO_EVIDENCE,
        notes=(
            "Central matches rail prior_scalar on SiO. Residual 0.63 dex covers "
            "Costa log-mid (~0.010) through Fedkin Langmuir mid (0.17). Wetzel "
            "growth form is a fence (solid_film_growth)."
        ),
    ),
    "silicate_melt_cation_redox": EvaporationClass(
        class_id="silicate_melt_cation_redox",
        label="silicate-melt cation redox reduction (Fe/Mg-class)",
        alpha_form="central",
        alpha_value=_REDOX_CENTRAL_ALPHA,
        residual_dex=_REDOX_RESIDUAL_DEX,
        system_classes=("silicate_melt", "solid_solution_silicate"),
        transformation_class="redox_reduction_required",
        species=("Fe", "Mg", "Mg2", "MgO_gas"),
        evidence=_REDOX_EVIDENCE,
        notes=(
            "Central 0.084 = gmean of comparable grounding poles (Costa Fe 0.02, "
            "Fedkin Fe 0.24, Fedkin Mg √(0.24·0.27), Richter Mg 0.04); residual "
            "0.63 dex covers every grounded α including rail Fe 0.02 (b-153). "
            "Not a Motzfeldt resolution (b-116 still open)."
        ),
    ),
    "silicate_melt_network_modifier": EvaporationClass(
        class_id="silicate_melt_network_modifier",
        label="silicate-melt network-modifier desorption (Na/K-class)",
        alpha_form="central",
        alpha_value=0.30,
        residual_dex=_MODIFIER_RESIDUAL_DEX,
        system_classes=("silicate_melt",),
        transformation_class="network_modifier_desorption",
        species=("Na", "Na2", "Na2O_gas", "K", "K2", "K2O_gas"),
        evidence=_MODIFIER_EVIDENCE,
        notes=(
            "Log-middle of vacuum (0.13-0.26) and open-furnace (~1.0) poles. "
            "Residual 0.53 dex covers the open-furnace 1.0 pole. Promotion "
            "trigger: mbar-inert measurement that splits regime."
        ),
    ),
    "oxide_condensed_congruent": EvaporationClass(
        class_id="oxide_condensed_congruent",
        label="pure-oxide condensed survey (Schaefer-class)",
        alpha_form="central",
        alpha_value=0.20,
        residual_dex=DEFAULT_RESIDUAL_DEX,
        system_classes=("pure_oxide_condensed",),
        transformation_class=None,
        species=(),  # diagnostic survey class — not a production species map
        evidence=_OXIDE_SURVEY_EVIDENCE,
        notes=(
            "Survey-only. Does not assign production melt-rail species. Thin "
            "store coverage in this worktree (Mg + SiO Schaefer rows only)."
        ),
    ),
    "marked_ideal_upper_bound": EvaporationClass(
        class_id="marked_ideal_upper_bound",
        label="marked-ideal upper bound (b-136 / no-data posture)",
        alpha_form="upper_bound",
        alpha_value=1.0,
        residual_dex=DEFAULT_RESIDUAL_DEX,
        system_classes=(),
        transformation_class=None,
        species=(
            "Ca",
            "CaO_gas",
            "Ca2",
            "Al",
            "AlO",
            "Al2O",
            "Al2",
            "Al2O2",
            "Al2O3_gas",
            "AlO2",
            "Ti",
            "TiO",
            "TiO2_gas",
            "Cr",
            "CrO",
            "CrO2",
            "CrO3",
            "Mn",
        ),
        evidence=_MARKED_IDEAL_FENCES,
        notes=(
            "alpha=1.0 is an explicit upper-bound marker (HKL ideal), not a "
            "calibrated central. Ca/Ti rest on b-136 mis-tag; Cr/Mn fences are "
            "pure-element / molten-metal. Split requires comparable melt alpha."
        ),
    ),
}


# Species → class_id (production assignment). Explicit map wins over scan.
_SPECIES_CLASS: dict[str, str] = {}
for _cid, _cls in EVAPORATION_CLASSES.items():
    if _cid == "oxide_condensed_congruent":
        continue  # survey only
    for _sp in _cls.species:
        if _sp in _SPECIES_CLASS:
            raise RuntimeError(
                f"species {_sp!r} assigned to both {_SPECIES_CLASS[_sp]!r} "
                f"and {_cid!r}"
            )
        _SPECIES_CLASS[_sp] = _cid

DEFAULT_CLASS_ID: Final[str] = "marked_ideal_upper_bound"

# Structurally-zero P carriers (EC2): no VP channel — reported separately.
STRUCTURALLY_ZERO_P_CARRIERS: Final[frozenset[str]] = frozenset(
    {"PO", "PO2", "P4O6", "P4O10", "P2", "P4"}
)


# ---------------------------------------------------------------------------
# Classification + reporting surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassMembership:
    species: str
    class_id: str
    label: str
    alpha_form: AlphaForm
    alpha_class: float
    residual_dex: float
    band_low: float | None
    band_high: float | None
    transformation_class: str | None
    notes: str
    structurally_zero: bool = False


@dataclass(frozen=True)
class SpeciesClassDiagnostic:
    """Per-species diagnostic: class + runtime α + interface share.

    ``alpha_runtime`` is what the flux path uses today (from vapor_pressures).
    ``alpha_class`` is the class-model value (not applied to flux in this chunk).
    """

    membership: ClassMembership
    alpha_runtime: float | None
    alpha_runtime_note: str
    T_K: float | None
    overhead_pressure_pa: float | None
    interface_share_s: float | None
    r_interface: float | None
    r_gas: float | None
    r_melt: float | None
    limiting_resistance_label: str | None
    flux_band_factor_high: float | None
    flux_band_factor_low: float | None
    series: SeriesEvaporationFlux | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        m = self.membership
        return {
            "species": m.species,
            "class_id": m.class_id,
            "class_label": m.label,
            "alpha_form": m.alpha_form,
            "alpha_class": m.alpha_class,
            "residual_dex": m.residual_dex,
            "band_low": m.band_low,
            "band_high": m.band_high,
            "transformation_class": m.transformation_class,
            "structurally_zero": m.structurally_zero,
            "notes": m.notes,
            "alpha_runtime": self.alpha_runtime,
            "alpha_runtime_note": self.alpha_runtime_note,
            "T_K": self.T_K,
            "overhead_pressure_pa": self.overhead_pressure_pa,
            "interface_share_s": self.interface_share_s,
            "r_interface": self.r_interface,
            "r_gas": self.r_gas,
            "r_melt": self.r_melt,
            "limiting_resistance_label": self.limiting_resistance_label,
            "flux_band_factor_high": self.flux_band_factor_high,
            "flux_band_factor_low": self.flux_band_factor_low,
        }


def classify_species(species: str) -> ClassMembership:
    """Map a runtime species id onto its production evaporation class."""

    sp = str(species).strip()
    if sp in STRUCTURALLY_ZERO_P_CARRIERS:
        cls = EVAPORATION_CLASSES[DEFAULT_CLASS_ID]
        return ClassMembership(
            species=sp,
            class_id="structurally_zero_no_vp_channel",
            label="structurally zero (no vapor-pressure channel)",
            alpha_form="upper_bound",
            alpha_class=1.0,
            residual_dex=DEFAULT_RESIDUAL_DEX,
            band_low=None,
            band_high=1.0,
            transformation_class=None,
            notes="EC2 structural zero — kinetic alpha present, no VP channel.",
            structurally_zero=True,
        )

    class_id = _SPECIES_CLASS.get(sp, DEFAULT_CLASS_ID)
    cls = EVAPORATION_CLASSES[class_id]
    return ClassMembership(
        species=sp,
        class_id=cls.class_id,
        label=cls.label,
        alpha_form=cls.alpha_form,
        alpha_class=cls.alpha_value,
        residual_dex=cls.residual_dex,
        band_low=cls.band_low(),
        band_high=cls.band_high(),
        transformation_class=cls.transformation_class,
        notes=cls.notes,
        structurally_zero=False,
    )


def list_production_classes() -> tuple[EvaporationClass, ...]:
    return tuple(
        EVAPORATION_CLASSES[cid]
        for cid in (
            "silicate_melt_network_former",
            "silicate_melt_cation_redox",
            "silicate_melt_network_modifier",
            "marked_ideal_upper_bound",
        )
    )


def grounding_evidence(class_id: str) -> tuple[EvidenceRow, ...]:
    cls = EVAPORATION_CLASSES[class_id]
    return tuple(e for e in cls.evidence if e.comparable and e.role != "fence")


# ---------------------------------------------------------------------------
# Production-α source gate (instrument-only; NOT wired into flux this chunk)
# ---------------------------------------------------------------------------
#
# Given (species, current production alpha source metadata) return whether that
# source is class-comparable for the production silicate-melt carrier. The flux
# path does not call this yet (instrument-before-gate / golden-neutral).

ProductionAlphaGate = Literal[
    "comparable",
    "no_data",
    # non_comparable_proxy:<system_class> — built dynamically below
]

# Tags that mark the production source as a *proxy* (not intrinsic melt α),
# even when the underlying measurement system is pure-oxide / similar.
_PROXY_TAGS: Final[frozenset[str]] = frozenset(
    {
        "pure_elemental_only",
        "proxy",
        "broad_proxy",
        "proxy_not_intrinsic",
        "broad_proxy_not_intrinsic",
    }
)

# Default system_class for each proxy tag when source text does not refine it.
_PROXY_TAG_DEFAULT_SYSTEM_CLASS: Final[dict[str, str]] = {
    "pure_elemental_only": "pure_element_condensed",
    "proxy": "pure_oxide_condensed",
    "broad_proxy": "pure_oxide_condensed",
    "proxy_not_intrinsic": "pure_oxide_condensed",
    "broad_proxy_not_intrinsic": "pure_oxide_condensed",
}

# Explicit system_class tokens accepted from structured metadata.
_KNOWN_SYSTEM_CLASS_TOKENS: Final[frozenset[str]] = (
    RAIL_COMPARABLE_SYSTEM_CLASSES | RAIL_INCOMPARABLE_SYSTEM_CLASSES
)


def _metadata_blob(source_metadata: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """Normalise production alpha source metadata into a dict."""

    if source_metadata is None:
        return {}
    if isinstance(source_metadata, str):
        text = source_metadata.strip()
        return {"source": text} if text else {}
    if not isinstance(source_metadata, Mapping):
        return {}
    return dict(source_metadata)


def _joined_source_text(blob: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("source", "source_note", "cite", "provenance", "tag"):
        val = blob.get(key)
        if isinstance(val, str):
            parts.append(val)
    nested = blob.get("value")
    if isinstance(nested, Mapping):
        for key in ("cite", "source", "status"):
            val = nested.get(key)
            if isinstance(val, str):
                parts.append(val)
    return " ".join(parts)


def _first_system_class_token(text: str) -> str | None:
    """Pull an explicit system_class=… token or a known class name from text."""

    lower = text.lower()
    for key in ("system_class=", "source_class="):
        idx = lower.find(key)
        if idx >= 0:
            raw = lower[idx + len(key) :].split()[0].strip(",;:)")
            # open_furnace_apparent_not_intrinsic is a regime flag, not system_class
            if raw in _KNOWN_SYSTEM_CLASS_TOKENS:
                return raw
    for token in sorted(_KNOWN_SYSTEM_CLASS_TOKENS, key=len, reverse=True):
        if token in lower:
            return token
    return None


def _refine_system_class_from_text(text: str, default: str | None = None) -> str | None:
    """Map free-text provenance onto a closed system_class token."""

    lower = text.lower()
    token = _first_system_class_token(lower)
    if token:
        return token
    if "pure elemental" in lower or "pure-si" in lower or "pure si " in lower:
        return "pure_element_condensed"
    if "polycrystalline solid" in lower or "solid cr" in lower:
        return "pure_element_condensed"
    if "molten metal" in lower or "olette" in lower:
        return "molten_metal"
    if (
        "solid_film" in lower
        or ("wetzel" in lower and ("alpha_s" in lower or "growth" in lower or "arrhenius" in lower))
    ):
        return "solid_film_growth"
    if "activity proxy" in lower or "catio3" in lower or "proxy_not_intrinsic" in lower:
        return "pure_oxide_condensed"
    if "olivine" in lower or "ferrobasalt" in lower:
        # KEMS olivine → solid_solution; open-furnace ferrobasalt melt → silicate_melt
        if "kems" in lower or "fo" in lower or "fa" in lower:
            return "solid_solution_silicate"
        return "silicate_melt"
    if "silicate melt" in lower or "silicate-melt" in lower:
        return "silicate_melt"
    if "hashimoto" in lower or "langmuir free" in lower:
        return "silicate_melt"
    if "fedkin" in lower and ("kems" in lower or "sealed-chamber" in lower or "sealed chamber" in lower):
        return "silicate_melt"
    if "sf2004" in lower or "schaefer" in lower or "forsterite" in lower or "mgsio4" in lower:
        return "pure_oxide_condensed"
    if "richter" in lower and ("cai" in lower or "mg/sio" in lower or "vacuum" in lower):
        return "silicate_melt"
    if "sossi" in lower and ("open-furnace" in lower or "open furnace" in lower or "ferrobasalt" in lower):
        return "silicate_melt"
    if "monoatomic-class proxy" in lower or "monoatomic-class" in lower:
        return "pure_element_condensed"
    return default


def _infer_source_system_class(
    blob: Mapping[str, Any],
) -> tuple[str | None, bool]:
    """Return (system_class, is_explicit_proxy_tag).

    ``is_explicit_proxy_tag`` is True when the production row is tagged as a
    non-intrinsic proxy — those always gate as ``non_comparable_proxy:…``
    even if the host system is pure-oxide (Si-comparable as a *measurement
    class*, but not as an intrinsic melt-α source for production).
    """

    # Structured field wins.
    for key in ("system_class", "source_system_class", "rail_system_class"):
        val = blob.get(key)
        if isinstance(val, str) and val in _KNOWN_SYSTEM_CLASS_TOKENS:
            tag = str(blob.get("tag") or "").strip().lower()
            return val, tag in _PROXY_TAGS

    tag = str(blob.get("tag") or "").strip().lower()
    text = _joined_source_text(blob)

    if tag in _PROXY_TAGS:
        refined = _refine_system_class_from_text(
            text, default=_PROXY_TAG_DEFAULT_SYSTEM_CLASS[tag]
        )
        return refined, True

    # Arrhenius growth form (SiO Wetzel) → solid_film_growth fence.
    nested = blob.get("value")
    if isinstance(nested, Mapping) and str(nested.get("form") or "") == "arrhenius":
        cite = text.lower()
        if "wetzel" in cite or "growth" in cite or "alpha_s" in cite:
            return "solid_film_growth", True

    if not text:
        return None, False

    refined = _refine_system_class_from_text(text, default=None)
    # Free-text proxy markers without a structured tag.
    lower = text.lower()
    free_proxy = (
        "proxy_not_intrinsic" in lower
        or "activity proxy" in lower
        or "class proxy" in lower
        or "status-bearing proxy" in lower
        or "status-bearing" in lower and "proxy" in lower
    )
    return refined, free_proxy


def gate_production_alpha_source(
    species: str,
    source_metadata: Mapping[str, Any] | str | None = None,
) -> str:
    """Gate whether a production α source is class-comparable.

    Parameters
    ----------
    species:
        Runtime species id (used for structurally-zero / membership context).
    source_metadata:
        Current production α source metadata — typically the
        ``evaporation_alpha`` mapping from ``vapor_pressures.yaml`` (with
        ``source``, ``tag``, optional ``system_class``), or a free-form source
        string. ``None`` / empty → ``no_data``.

    Returns
    -------
    str
        One of:

        * ``"comparable"`` — source is a Si-comparable system class for the
          production melt carrier (intrinsic-style grounding).
        * ``"non_comparable_proxy:<system_class>"`` — source is an incomparable
          fence class or an explicit non-intrinsic proxy
          (e.g. ``pure_element_condensed``, ``solid_film_growth``,
          ``pure_oxide_condensed`` activity proxy).
        * ``"no_data"`` — no usable source metadata.

    Notes
    -----
    **Not wired into the flux path.** Callers that later adopt class α should
    consult this gate before accepting a production source as class evidence.
    """

    _ = classify_species(species)  # membership available for future refinements
    blob = _metadata_blob(source_metadata)
    if not blob:
        return "no_data"

    # Empty of any source-bearing keys → no_data.
    source_bearing = any(
        blob.get(k)
        for k in (
            "source",
            "source_note",
            "cite",
            "provenance",
            "tag",
            "system_class",
            "source_system_class",
            "value",
        )
    )
    if not source_bearing:
        return "no_data"

    system_class, is_proxy = _infer_source_system_class(blob)
    if system_class is None:
        # Has metadata but no class signal — treat as no_data for the gate
        # (caller cannot claim comparable without evidence).
        return "no_data"
    if is_proxy or system_class in RAIL_INCOMPARABLE_SYSTEM_CLASSES:
        return f"non_comparable_proxy:{system_class}"
    if system_class in RAIL_COMPARABLE_SYSTEM_CLASSES:
        return "comparable"
    return "no_data"


def gate_label(status: str) -> str:
    """Human-readable label for a :func:`gate_production_alpha_source` result."""

    if status == "comparable":
        return "comparable (production melt carrier)"
    if status == "no_data":
        return "no source metadata"
    if status.startswith("non_comparable_proxy:"):
        sc = status.split(":", 1)[1]
        return f"non-comparable proxy ({sc})"
    return status


# ---------------------------------------------------------------------------
# Interface-resistance share (series form; no flux mutation)
# ---------------------------------------------------------------------------


def interface_resistance_share(
    *,
    species: str,
    alpha: float,
    T_K: float,
    molar_mass_kg_mol: float,
    overhead_pressure_pa: float,
    p_eq_pa: float = 1.0,
    p_bulk_pa: float = 0.0,
    pipe_diameter_m: float = 0.12,
    carrier_gas: str = "N2",
    radial_stir_factor: float = 1.0,
) -> SeriesEvaporationFlux:
    """Compute series-resistance diagnostics including interface share ``s``.

    Premise / algebra / units: see design.md §2. Uses the authoritative
    ``_series_resistance_evaporation_flux_kg_m2_s`` helper so diagnostics
    cannot drift from production physics.
    """

    if not math.isfinite(alpha) or alpha <= 0.0:
        raise ValueError(f"alpha must be finite and > 0, got {alpha!r}")
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError(f"T_K must be finite and > 0, got {T_K!r}")
    if not math.isfinite(molar_mass_kg_mol) or molar_mass_kg_mol <= 0.0:
        raise ValueError(
            f"molar_mass_kg_mol must be finite and > 0, got {molar_mass_kg_mol!r}"
        )

    # Ensure a positive driving force so resistances are well-defined even
    # when the caller only wants s (share is independent of delta_p once
    # both resistances are finite).
    p_eq = max(float(p_eq_pa), 1.0)
    p_bulk = max(0.0, min(float(p_bulk_pa), p_eq * 0.5))

    return _series_resistance_evaporation_flux_kg_m2_s(
        species=str(species),
        P_eq_pa=p_eq,
        P_bulk_pa=p_bulk,
        T_surface_K=float(T_K),
        molar_mass_kg_mol=float(molar_mass_kg_mol),
        alpha_i=float(alpha),
        pipe_diameter_m=float(pipe_diameter_m),
        overhead_pressure_pa=float(overhead_pressure_pa),
        radial_stir_factor=float(radial_stir_factor),
        carrier_gas=str(carrier_gas),
        melt_resistance_enabled=False,
        gas_resistance_enabled=True,
    )


def interface_share_s(series: SeriesEvaporationFlux) -> float:
    """s = r_interface / (r_interface + r_gas + r_melt)."""

    denom = series.r_interface + series.r_gas + series.r_melt
    if not math.isfinite(denom) or denom <= 0.0:
        if math.isinf(series.r_interface) and series.r_interface > 0.0:
            return 1.0
        return 0.0
    return float(series.r_interface / denom)


def flux_band_factors(s: float, residual_dex: float) -> tuple[float, float]:
    """Approximate flux multiplier band from class residual compressed by ``s``.

    Premise: local elasticity E = s, so a ±σ dex move in α scales ln J by
    ±s·σ·ln(10). Factors are 10^{±s·σ}.
    Unit check: s and σ dimensionless → factor dimensionless. ✔
    Limiting cases: s→0 → (1,1); s→1 → (10^{-σ}, 10^{+σ}).
    """

    s = min(max(float(s), 0.0), 1.0)
    sigma = abs(float(residual_dex))
    expo = s * sigma
    return (10.0 ** (-expo), 10.0 ** expo)


# ---------------------------------------------------------------------------
# Runtime α loader (read-only diagnostic; does not mutate)
# ---------------------------------------------------------------------------


def _runtime_alpha_map(
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if vapor_pressure_data is None:
        with _VAPOR_PRESSURES_PATH.open(encoding="utf-8") as fh:
            vapor_pressure_data = yaml.safe_load(fh) or {}
    return _load_evaporation_alpha_by_species(dict(vapor_pressure_data))


def _scalar_runtime_alpha(raw: Any) -> tuple[float | None, str]:
    if raw is None:
        return None, "missing"
    if isinstance(raw, (int, float)) and math.isfinite(float(raw)):
        return float(raw), "scalar"
    if isinstance(raw, Mapping):
        # SiO Arrhenius contract — evaluate note only; caller may pass T.
        form = str(raw.get("form") or "")
        if form == "arrhenius" or "A" in raw:
            return None, "arrhenius_contract"
        if "value" in raw and isinstance(raw["value"], (int, float)):
            return float(raw["value"]), "mapping_value"
        return None, f"unparsed_mapping:{form or 'unknown'}"
    return None, f"unparsed_type:{type(raw).__name__}"


def _default_molar_mass_kg_mol(species: str) -> float:
    """Best-effort molar mass for diagnostic share; never used in flux path."""

    # Local proxies for common rail species (kg/mol). Matches LJ proxy masses
    # in engines/builtin/evaporation_flux.py where available.
    table = {
        "Fe": 0.055845,
        "Mg": 0.024305,
        "MgO_gas": 0.040304,
        "Mg2": 0.048610,
        "Na": 0.022989769,
        "Na2": 0.045979538,
        "Na2O_gas": 0.0619785,
        "K": 0.0390983,
        "K2": 0.0781966,
        "K2O_gas": 0.0941956,
        "SiO": 0.044085,
        "Si": 0.028085,
        "Si2": 0.056170,
        "Si3": 0.084255,
        "SiO2_gas": 0.060083,
        "Ca": 0.040078,
        "CaO_gas": 0.056077,
        "Al": 0.0269815,
        "Ti": 0.047867,
        "Cr": 0.051996,
        "Mn": 0.054938,
    }
    if species in table:
        return table[species]
    # Fallback: 50 g/mol — diagnostic only; share is weakly mass-dependent
    # through k_HKL vs k_g (both ∝ √M or M), so relative s is still informative.
    return 0.050


def report_species_class_diagnostics(
    species: str,
    *,
    T_K: float | None = None,
    overhead_pressure_pa: float | None = None,
    alpha_override: float | None = None,
    molar_mass_kg_mol: float | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    pipe_diameter_m: float = 0.12,
    carrier_gas: str = "N2",
) -> SpeciesClassDiagnostic:
    """Classify ``species`` and optionally compute interface share at conditions.

    When ``T_K`` and ``overhead_pressure_pa`` are both provided, the series-
    resistance diagnostic is evaluated with the **runtime** α (or
    ``alpha_override``) so ``interface_share_s`` reflects current conditions.
    Class α is reported alongside but is **not** applied to the series call
    unless the caller passes it as ``alpha_override``.
    """

    membership = classify_species(species)
    runtime_map = _runtime_alpha_map(vapor_pressure_data)
    raw = runtime_map.get(species)
    alpha_runtime, runtime_note = _scalar_runtime_alpha(raw)

    # Arrhenius SiO: evaluate at T when available for a diagnostic scalar.
    if (
        alpha_runtime is None
        and runtime_note == "arrhenius_contract"
        and isinstance(raw, Mapping)
        and T_K is not None
        and math.isfinite(float(T_K))
        and T_K > 0.0
    ):
        try:
            A = float(raw["A"])
            B = float(raw["B"])
            alpha_runtime = A * math.exp(-B / float(T_K))
            runtime_note = f"arrhenius_eval@T={T_K:g}"
        except (KeyError, TypeError, ValueError):
            alpha_runtime = None
            runtime_note = "arrhenius_eval_failed"

    series: SeriesEvaporationFlux | None = None
    s: float | None = None
    band_lo: float | None = None
    band_hi: float | None = None
    limiting: str | None = None
    r_i = r_g = r_m = None

    alpha_for_s = alpha_override
    if alpha_for_s is None:
        alpha_for_s = alpha_runtime
    if alpha_for_s is None and membership.alpha_form == "central":
        alpha_for_s = membership.alpha_class
        runtime_note = runtime_note + "+class_alpha_fallback_for_s"

    if (
        T_K is not None
        and overhead_pressure_pa is not None
        and alpha_for_s is not None
        and not membership.structurally_zero
    ):
        M = (
            float(molar_mass_kg_mol)
            if molar_mass_kg_mol is not None
            else _default_molar_mass_kg_mol(species)
        )
        series = interface_resistance_share(
            species=species,
            alpha=float(alpha_for_s),
            T_K=float(T_K),
            molar_mass_kg_mol=M,
            overhead_pressure_pa=float(overhead_pressure_pa),
            pipe_diameter_m=pipe_diameter_m,
            carrier_gas=carrier_gas,
        )
        s = interface_share_s(series)
        r_i, r_g, r_m = series.r_interface, series.r_gas, series.r_melt
        diag = series.as_diagnostic()
        limiting = str(diag.get("limiting_resistance_label"))
        band_lo, band_hi = flux_band_factors(s, membership.residual_dex)

    return SpeciesClassDiagnostic(
        membership=membership,
        alpha_runtime=alpha_runtime,
        alpha_runtime_note=runtime_note,
        T_K=T_K,
        overhead_pressure_pa=overhead_pressure_pa,
        interface_share_s=s,
        r_interface=r_i,
        r_gas=r_g,
        r_melt=r_m,
        limiting_resistance_label=limiting,
        flux_band_factor_high=band_hi,
        flux_band_factor_low=band_lo,
        series=series,
    )


def report_all_species_diagnostics(
    species_list: Sequence[str] | None = None,
    **kwargs: Any,
) -> list[SpeciesClassDiagnostic]:
    """Report diagnostics for a species list (default: all mapped production)."""

    if species_list is None:
        species_list = sorted(_SPECIES_CLASS.keys())
    return [report_species_class_diagnostics(sp, **kwargs) for sp in species_list]


# ---------------------------------------------------------------------------
# Extract-store probe (evidence rows must exist)
# ---------------------------------------------------------------------------


def _index_extract_observation_ids(
    extracts_dir: Path | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    root = extracts_dir or _EXTRACTS_DIR
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(root.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        source_id = str(data.get("source_id") or path.stem)
        for sp, block in (data.get("species") or {}).items():
            if not isinstance(block, Mapping):
                continue
            for obs in block.get("observations") or []:
                if not isinstance(obs, Mapping):
                    continue
                oid = obs.get("observation_id")
                if not oid:
                    continue
                index[(source_id, str(oid))] = {
                    "species": sp,
                    "type": obs.get("type"),
                    "phase": obs.get("phase"),
                    "regime": obs.get("regime"),
                    "values": dict(obs.get("values") or {}),
                }
    return index


def assert_evidence_rows_present(
    *,
    extracts_dir: Path | None = None,
    classes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Probe the extract store: every class evidence row must resolve.

    Also asserts **band coverage** for every central class: each comparable
    non-fence evidence row that carries a numeric ``alpha`` must satisfy
    ``band_low <= alpha <= band_high`` (P2 / b-153).

    Returns a structured report. Raises ``FileNotFoundError`` if any
    observation_id is missing from the store, or ``AssertionError`` if a
    numeric alpha falls outside its class residual band.
    """

    index = _index_extract_observation_ids(extracts_dir)
    class_ids = list(classes) if classes is not None else list(EVAPORATION_CLASSES)
    missing: list[dict[str, str]] = []
    found: list[dict[str, Any]] = []
    band_violations: list[dict[str, Any]] = []
    band_checked: list[dict[str, Any]] = []

    for cid in class_ids:
        cls = EVAPORATION_CLASSES[cid]
        band_lo = cls.band_low()
        band_hi = cls.band_high()
        for row in cls.evidence:
            key = (row.source_id, row.observation_id)
            hit = index.get(key)
            if hit is None:
                # Accept same observation_id under a different source stem
                # (pilot vs kems- mirror) if unique.
                alts = [k for k in index if k[1] == row.observation_id]
                if len(alts) == 1:
                    hit = index[alts[0]]
                    found.append(
                        {
                            "class_id": cid,
                            "source_id": alts[0][0],
                            "observation_id": row.observation_id,
                            "requested_source_id": row.source_id,
                            "species": hit["species"],
                            "comparable": row.comparable,
                            "role": row.role,
                            "alpha": row.alpha,
                            "resolved_via": "observation_id_unique",
                        }
                    )
                else:
                    missing.append(
                        {
                            "class_id": cid,
                            "source_id": row.source_id,
                            "observation_id": row.observation_id,
                        }
                    )
                    continue
            else:
                found.append(
                    {
                        "class_id": cid,
                        "source_id": row.source_id,
                        "observation_id": row.observation_id,
                        "species": hit["species"],
                        "comparable": row.comparable,
                        "role": row.role,
                        "alpha": row.alpha,
                        "resolved_via": "source_id+observation_id",
                    }
                )

            # Band coverage: comparable non-fence numeric pins on central classes.
            if (
                cls.alpha_form == "central"
                and row.comparable
                and row.role != "fence"
                and row.alpha is not None
                and band_lo is not None
                and band_hi is not None
            ):
                a = float(row.alpha)
                ok = band_lo <= a <= band_hi
                entry = {
                    "class_id": cid,
                    "observation_id": row.observation_id,
                    "alpha": a,
                    "band_low": band_lo,
                    "band_high": band_hi,
                    "alpha_class": cls.alpha_value,
                    "residual_dex": cls.residual_dex,
                    "ok": ok,
                }
                band_checked.append(entry)
                if not ok:
                    band_violations.append(entry)

    report = {
        "n_expected": sum(len(EVAPORATION_CLASSES[c].evidence) for c in class_ids),
        "n_found": len(found),
        "n_missing": len(missing),
        "missing": missing,
        "found": found,
        "n_band_checked": len(band_checked),
        "n_band_violations": len(band_violations),
        "band_checked": band_checked,
        "band_violations": band_violations,
    }
    if missing:
        detail = ", ".join(
            f"{m['source_id']}/{m['observation_id']}" for m in missing
        )
        raise FileNotFoundError(
            f"class evidence rows missing from extract store: {detail}"
        )
    if band_violations:
        detail = "; ".join(
            (
                f"{v['class_id']}/{v['observation_id']}: alpha={v['alpha']} "
                f"outside [{v['band_low']:.6g}, {v['band_high']:.6g}] "
                f"(central={v['alpha_class']}, residual_dex={v['residual_dex']})"
            )
            for v in band_violations
        )
        raise AssertionError(
            f"class residual band does not cover grounded alpha(s): {detail}"
        )
    return report


def e_down_from_s(s: float, delta_dex: float = 0.5) -> float:
    """Closed-form chord elasticity from interface share (EC2 / design §2.3).

    Premise: E_down(δ) = log10(1 + s(10^δ − 1)) / δ
    Unit check: dimensionless. ✔
    Limiting cases: s→0 ⇒ E→0; s→1 ⇒ E→1.
    """

    d = abs(float(delta_dex))
    if d == 0.0:
        raise ValueError("delta_dex must be non-zero")
    return math.log10(1.0 + float(s) * (10.0**d - 1.0)) / d


__all__ = [
    "AlphaForm",
    "ClassMembership",
    "DEFAULT_CLASS_ID",
    "DEFAULT_RESIDUAL_DEX",
    "EVAPORATION_CLASSES",
    "EvidenceRow",
    "EvaporationClass",
    "RAIL_COMPARABLE_SYSTEM_CLASSES",
    "RAIL_INCOMPARABLE_SYSTEM_CLASSES",
    "STRUCTURALLY_ZERO_P_CARRIERS",
    "SpeciesClassDiagnostic",
    "assert_evidence_rows_present",
    "classify_species",
    "e_down_from_s",
    "flux_band_factors",
    "gate_label",
    "gate_production_alpha_source",
    "grounding_evidence",
    "interface_resistance_share",
    "interface_share_s",
    "list_production_classes",
    "report_all_species_diagnostics",
    "report_species_class_diagnostics",
]
