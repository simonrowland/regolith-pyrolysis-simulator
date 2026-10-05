"""Liquid oxide parents for trace-element vapour channels.

One assignment of element to parent-oxide ledger key. The channel generator
and the feedstock bridge both read this table. ``ACTIVITY_BASIS`` is the
gamma component (GaO1.5, CuO0.5), not the inventory key. The ledger key is
the conventional oxide formula stored here (Ga2O3, Cu2O).
"""

from __future__ import annotations

from types import MappingProxyType


LIQUID_PARENT_OXIDE = MappingProxyType(
    {
        "Ga": "Ga2O3",
        "In": "In2O3",
        "Pb": "PbO",
        "Ge": "GeO2",
        "Sn": "SnO",
        "Rb": "Rb2O",
        "Cs": "Cs2O",
        "B": "B2O3",
        "Cu": "Cu2O",
        "V": "V2O3",
        "Li": "Li2O",
    }
)

ACTIVITY_BASIS = MappingProxyType(
    {
        "Ga": "GaO1.5",
        "In": "InO1.5",
        "Cu": "CuO0.5",
    }
)

# Not a parent map. These elements sit in Fe metal in metal-bearing
# feedstocks; the bridge keeps the element key and flags the scope gap
# instead of writing an oxide that the metal does not contain.
SIDEROPHILE_IN_METAL_SCOPE_GAP = frozenset(
    {"Ni", "Co", "Ir", "Os", "Pt", "Au"}
)


def ledger_component_key(formula: str) -> str:
    """Inventory key for a component formula. Phase tags are not ledger keys."""

    text = str(formula).strip()
    if text.endswith(")") and "(" in text:
        return text[: text.rfind("(")]
    return text
