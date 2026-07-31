"""Vapour-rail unification package.

U0 freezes the canonical manifest union. VR-4 adds typed NASA CEA polynomial
evaluators (``nasa_cea``). VR-7 adds charge-alias canonicalization for
ledger/cache-safe oxyanion names. Later VR chunks add the request builder and
runtime cutovers under this package.
"""

from __future__ import annotations

from simulator.vapour_rail.catalog import (
    CHARGE_ALIAS_CANONICAL,
    canonicalize_charge_alias,
)
from simulator.vapour_rail.u0_manifest import (
    ASSOCIATION_POLYMER_IDS,
    CARRIER_ONLY_IDS,
    COLLISION_GAS_IDS,
    FEEDSTOCK_DELTA_IDS,
    GROUP_A_GAS_IDS,
    GROUP_B_ELEMENT_IDS,
    GROUP_B_GAS_IDS,
    REFRACTORY_GAS_IDS_RAW,
    VAPOROCK_42_IDS,
    build_u0_manifest,
    canonicalize_gas_id,
    load_u0_manifest,
)

__all__ = [
    "ASSOCIATION_POLYMER_IDS",
    "CARRIER_ONLY_IDS",
    "CHARGE_ALIAS_CANONICAL",
    "COLLISION_GAS_IDS",
    "FEEDSTOCK_DELTA_IDS",
    "GROUP_A_GAS_IDS",
    "GROUP_B_ELEMENT_IDS",
    "GROUP_B_GAS_IDS",
    "REFRACTORY_GAS_IDS_RAW",
    "VAPOROCK_42_IDS",
    "build_u0_manifest",
    "canonicalize_charge_alias",
    "canonicalize_gas_id",
    "load_u0_manifest",
]
