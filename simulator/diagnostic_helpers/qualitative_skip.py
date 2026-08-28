"""Classify qualitative extract rows that are not parseable ordering claims.

``semantics: bound_not_point_ordering`` is a catch-all tag. Coverage used to
report every such row as ``ordering_claim_unparsed``, which made figure-only
blocks, model tables, and methodology notes look like evolution-order parser
gaps. This module names the actual payload class. It does not admit rows.
Figure-derived labeling is d-005 (open); do not change admissibility.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_FIGURE_ONLY_QUANTITIES = frozenset(
    {
        "partial_pressure_figure_only",
        "partial_pressure_figure_only_not_tabulated",
        "partial_pressure_figure_only_comparable_to_TiO",
        "partial_pressure_figure_only_comparable_to_TiO2",
        "I_T_vs_1_over_T",
        "mare_regolith_P_vs_T",
        "a_FeO_vs_x_FeO",
        "qms_partial_pressure_time_series",
        "Bi2O3_isoactivity_and_excess_gibbs",
        "factsage_yield_grid_figure_only",
    }
)
_DETECTED_NOT_TABULATED_QUANTITIES = frozenset(
    {
        "species_detected_absolute_P_not_tabulated",
        "associated_vapor_ions_observed_Mo_cell",
    }
)
_MODEL_OUTPUT_QUANTITIES = frozenset(
    {
        "SOLGASMIX_alkali_speciation",
        "equilibrium_molecular_oxygen_yield",
    }
)


def qualitative_payload_skip_reason(obs: Any) -> str:
    """Typed refusal for a qualitative row with no parseable ordering relation."""

    values = obs.values
    quantity = str(values.get("quantity") or "")
    admission = str(values.get("admission_status") or "")
    method_class = str(values.get("method_class") or "")
    locator = obs.locator if isinstance(obs.locator, Mapping) else {}

    if (
        quantity == "iupac_htms_reporting_requirements"
        or obs.phase == "methodology_standard"
    ):
        return "unsupported_observable:methodology_guidance_not_observable"

    first_observed = values.get("first_observed_T_K")
    try:
        first_observed_T = float(first_observed) if first_observed is not None else None
    except (TypeError, ValueError):
        first_observed_T = None
    if (
        first_observed_T is not None
        and math.isfinite(first_observed_T)
        and first_observed_T > 0.0
        and quantity == "partial_pressure_figure_only_rapid_depletion"
    ):
        # Appearance-order claim (Na+/K+ first ion species) with no pairwise
        # counterpart on the row. Keep as an unparsed ordering, not figure-only.
        return "unsupported_observable:ordering_claim_unparsed"

    if (
        admission == "rejected_no_figure_reading"
        or "figure_only" in quantity
        or quantity in _FIGURE_ONLY_QUANTITIES
        or locator.get("figure") is not None
    ):
        return "unsupported_observable:figure_only_not_digitized"

    if quantity == "vapour_species_map_no_numeric_pressures":
        return "unsupported_observable:vapour_species_map_no_numeric_pressures"

    if quantity == "pure_oxide_dominant_gas_species_index":
        return "unsupported_observable:pure_oxide_speciation_index"

    if quantity == "not_reported_among_detected_species":
        return "unsupported_observable:species_not_reported_among_detected"

    if quantity in _DETECTED_NOT_TABULATED_QUANTITIES:
        return "unsupported_observable:species_detected_absolute_P_not_tabulated"

    if quantity.startswith("deposit_") or "deposit_edx" in quantity:
        return "unsupported_observable:deposit_composition_not_species_rate"

    if method_class == "model_derived" or quantity in _MODEL_OUTPUT_QUANTITIES:
        return "model_output_not_measurement"

    return "unsupported_observable:ordering_claim_unparsed"
