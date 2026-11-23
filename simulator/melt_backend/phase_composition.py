"""Battery role classification of an engine phase label.

Native labels are not rewritten. A label is a liquid name the scorer
already matches, an unmapped crystal, or an ambiguous token. This is
not the liquid-mass rule (``phase_name_is_liquid_mass``). Changing
which names count as a battery role must not change liquid fraction.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum


class PhaseLabelClass(str, Enum):
    LIQUID = "liquid"
    UNMAPPED = "unmapped"
    AMBIGUOUS = "ambiguous"


# Casefolded. These are the names the phase-row selector already treats
# as melt, slag, or glass. A second stored alias for one phase would
# make that selector report ambiguous_phase.
LIQUID_ROLE_LABELS = frozenset({"liq", "liquid", "melt", "slag", "glass"})

# Printed crystals. None of these is a PhaseRole. The table is the
# closed set the mapping test pins; any other unknown label is also
# unmapped rather than guessed onto a role.
UNMAPPED_CRYSTAL_LABELS = frozenset({
    "ol",
    "cpx",
    "opx",
    "spl",
    "pl",
    "fsp",
    "olivine",
    "clinopyroxene",
    "orthopyroxene",
    "spinel",
    "plagioclase",
    "feldspar",
})

# MAGEMin ig ``g`` is garnet. The same letter is gas in other engines.
# Never map it to glass.
AMBIGUOUS_LABEL_REASONS = {"g": "garnet_or_gas"}


def classify_phase_label(label: str) -> tuple[PhaseLabelClass, str | None]:
    """Return the class and, for an ambiguous label, the typed reason."""

    folded = str(label).casefold()
    reason = AMBIGUOUS_LABEL_REASONS.get(folded)
    if reason is not None:
        return PhaseLabelClass.AMBIGUOUS, reason
    if folded in LIQUID_ROLE_LABELS:
        return PhaseLabelClass.LIQUID, None
    return PhaseLabelClass.UNMAPPED, "no_battery_role"


def ambiguous_role_reason(
    role_value: str,
    phase_names: Iterable[str],
) -> str | None:
    """Typed reason when a present label must not be guessed onto this role.

    Only glass consults ``g``. Melt does not treat garnet-or-gas as a liquid.
    """

    if str(role_value).casefold() != "glass":
        return None
    for name in phase_names:
        klass, reason = classify_phase_label(str(name))
        if klass is PhaseLabelClass.AMBIGUOUS:
            return f"ambiguous_phase_label:{reason}"
    return None
