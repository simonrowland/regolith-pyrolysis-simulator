"""C0 subsolidus bakeout: exact feed-declared hosts only.

Element totals are not mineral phases. ``FeS`` and elemental ``S`` do not
dispatch a troilite reaction, and ``FeSO4`` stays on the carbon-cleanup path.
"""

from __future__ import annotations

SALT_FEED_ACCOUNT = "process.stage0_salt_feed"
FOULANT_ACCOUNT = "process.stage0_foulant"
METAL_ACCOUNT = "process.metal_phase"
OVERHEAD_GAS_ACCOUNT = "process.overhead_gas"
CLEANED_MELT_ACCOUNT = "process.cleaned_melt"

# Catalog ids. Formula and phase match openimcc subsolidus.json at 680574d.
BAKEOUT_HOST_IDENTITY = {
    "FeS_troilite": {"formula": "FeS", "phase": "troilite"},
    "CaSO4": {"formula": "CaSO4", "phase": "anhydrite"},
    "MgSO4": {
        "formula": "MgSO4",
        "phase": "crystalline anhydrous magnesium sulfate",
    },
}

BAKEOUT_SULFIDE_HOSTS = frozenset({"FeS_troilite"})
BAKEOUT_SULFATE_HOSTS = frozenset({"CaSO4", "MgSO4"})
