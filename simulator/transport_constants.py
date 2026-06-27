"""Single-source transport constants shared by the live condensation/evaporation
path (:mod:`simulator.condensation`) and the pinned VPR-P0a reproduction formulas
(:mod:`simulator.transport_regime`).

This module holds ONLY plain constants and imports nothing from the package, so
either consumer can import it without an import cycle. It exists so the two
modules can stop carrying their own copies of the same grounded values (the
previous duplication is what let the N2 collision diameter drift to an
ungrounded ``3.7e-10`` in one path while the other used the grounded
``3.798e-10`` -- BUG-013). Covers BUG-023 (Knudsen flow-regime thresholds) and
BUG-013/BUG-027 (carrier-gas collision diameters).

Note: values are RELOCATED verbatim (same float literals) -- no arithmetic is
introduced here, so importing these is byte-identical to the previous in-module
literals (golden-neutral by construction).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

# Knudsen-number flow-regime thresholds (dimensionless). Below VISCOUS_KNUDSEN_MAX
# the flow is continuum/viscous; above FREE_MOLECULAR_KNUDSEN_MIN it is free
# molecular; between, transitional. [BUG-023]
VISCOUS_KNUDSEN_MAX = 0.01
FREE_MOLECULAR_KNUDSEN_MIN = 10.0

# Carrier-gas hard-sphere / Lennard-Jones collision diameters [m].
# Bird/Stewart/Lightfoot "Transport Phenomena" 2nd ed., Table E.1 (equivalently
# Poling/Prausnitz/O'Connell). Single source for the transport_regime MFP/Knudsen
# path and the condensation N2 kinetic diameter. [BUG-013 / BUG-027]
COLLISION_DIAMETER_SOURCE = "Poling et al., Lennard-Jones sigma"
COLLISION_DIAMETERS_M: Mapping[str, float] = MappingProxyType(
    {
        "N2": 3.798e-10,
        "Ar": 3.542e-10,
        "O2": 3.467e-10,
        "CO": 3.690e-10,
        "CO2": 3.941e-10,
        "H2": 2.827e-10,
        "H2O": 2.641e-10,
    }
)

# N2 kinetic collision diameter [m] -- the grounded value the condensation
# MFP/Knudsen path and the binary-diffusion (Lennard-Jones) path both key off, so
# they can never diverge. Prior value was an ungrounded rounded 3.7e-10 carryover
# with no cited source. [BUG-013]
N2_COLLISION_DIAMETER_M = COLLISION_DIAMETERS_M["N2"]
