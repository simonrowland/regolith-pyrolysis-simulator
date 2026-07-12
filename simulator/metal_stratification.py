"""Pure metal-phase stratification math shared by provider and diagnostics."""

from __future__ import annotations

import math
from typing import Mapping

BOTTOM_POOL_SPECIES = frozenset({"Fe", "Cr", "Mn", "Ni", "Ti"})
FLOAT_LAYER_SPECIES = frozenset({"Al", "Si"})


def first_order_transfer_fraction(k_mix_per_hr: float, dt_hr: float) -> float:
    """Exact constant-k solution for one pool-to-pool contact interval.

    Premise: first-order contact gives dn_source/dt=-k*n_source. Integration
    gives n(t+dt)=n(t)*exp(-k*dt), so transferred fraction=1-exp(-k*dt).
    Unit check: (1/hr)*hr is dimensionless. Sanity: k=0 transfers nothing;
    k*dt=ln(100) transfers 99% without ever overshooting the source inventory.
    """

    k = float(k_mix_per_hr)
    dt = float(dt_hr)
    if not math.isfinite(k) or not math.isfinite(dt) or k < 0.0 or dt < 0.0:
        raise ValueError("k_mix_per_hr and dt_hr must be finite and non-negative")
    return -math.expm1(-k * dt)


def k_mix_from_axial_stirring(stir_factor: float) -> float:
    """Map the existing physical stir command to a transparent contact rate."""

    from simulator.material_densities import material_density_data

    assumptions = material_density_data()["diagnostic_assumptions"]
    floor = float(assumptions["settling_floor_k_mix_per_hr"])
    default_stir = float(assumptions["default_axial_stir_factor"])
    mixed_fraction = float(
        assumptions["well_mixed_fraction_per_hour_at_default_stirring"]
    )
    stir = float(stir_factor)
    # Typed boundary: NaN would silently satisfy max(0.0, nan)==0.0 and land
    # on the settling floor as if stirring were deliberately OFF; infinity
    # already fails downstream. Both are invalid commands, not settings.
    if not math.isfinite(stir):
        raise ValueError(
            "stir_factor must be finite (got a non-finite stir command)"
        )
    stir = max(0.0, stir)
    # Premise: default induction stirring is the well-mixed operating
    # assumption; OFF retains only the labeled settling/diffusion floor.
    # Algebra: k_default=-ln(1-f_default), then
    # k=floor+(k_default-floor)*stir/default_stir.
    # Unit check: -ln(1-f) is per one-hour tick -> 1/hr. Sanity: default 6x
    # transfers 99%/hr; zero command transfers about 0.01%/hr.
    k_default = -math.log1p(-mixed_fraction)
    return floor + (k_default - floor) * stir / default_stir


def target_pool(species: str, *, si_destination_verdict: str) -> str | None:
    if species in BOTTOM_POOL_SPECIES:
        return "bottom_pool"
    if species == "Al":
        return "float_layer"
    if species == "Si":
        # Si joins the lower alloy only when that destination is itself denser
        # than the melt. This makes routing composition-based and invariant to
        # a uniform rescaling of every mol inventory.
        return "bottom_pool" if si_destination_verdict == "sink" else "float_layer"
    return None


def pool_weight_percent(species_mol: Mapping[str, float]) -> dict[str, float]:
    from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL

    # Premise: ledger inventory is mol-native while alloy composition is
    # conventionally reported by mass. Algebra: m_i=n_i*M_i and
    # wt%_i=100*m_i/sum(m_j). Unit check: mol*(g/mol)=g and g/g is
    # dimensionless. Sanity: equimolar Fe-Si reports about 66.5 wt% Fe.
    masses = {
        species: max(0.0, float(amount)) * float(ATOMIC_WEIGHTS_G_PER_MOL[species])
        for species, amount in dict(species_mol or {}).items()
        if float(amount) > 0.0 and species in ATOMIC_WEIGHTS_G_PER_MOL
    }
    total = sum(masses.values())
    if total <= 0.0:
        return {}
    return {species: 100.0 * mass / total for species, mass in masses.items()}
