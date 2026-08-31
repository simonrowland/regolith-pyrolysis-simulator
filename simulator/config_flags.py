"""Boolean feature-flag lookup that treats a present YAML/JSON null as absent.

Admission is an allow-list, not a type. See ``CONSERVATIVE_BOOL_FEATURE_FLAGS``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Present-null-as-absent is a relaxation of fail-loud doctrine. It is safe
# only because each of these four defaults is the conservative setting — an
# audit of these instances, not a property of boolean flags in general. A
# call for an unlisted key is a loud failure, not a quiet default.
CONSERVATIVE_BOOL_FEATURE_FLAGS: frozenset[str] = frozenset(
    {
        # default True -> gas-film resistance ON
        "gas_resistance_enabled",
        # default True -> Arrhenius dependence ON
        "temperature_dependence_enabled",
        # default True -> flag stays raised, not cleared
        "needs_experiment",
        # computed -> import penalty applied in bootstrap narrative
        "import_flag_enabled",
    }
)

# Flags whose conservative setting is the constant True. ``import_flag_enabled``
# is computed (True only in bootstrap narrative) and is admitted above but not
# here, so a mature-mode default of False still goes through the helper.
_CONSTANT_CONSERVATIVE_TRUE: frozenset[str] = frozenset(
    {
        "gas_resistance_enabled",
        "temperature_dependence_enabled",
        "needs_experiment",
    }
)

_CONSERVATIVE_DEFAULT_NOTES: dict[str, str] = {
    "gas_resistance_enabled": "default True -> gas-film resistance ON",
    "temperature_dependence_enabled": "default True -> Arrhenius dependence ON",
    "needs_experiment": "default True -> flag stays raised, not cleared",
    "import_flag_enabled": (
        "computed -> import penalty applied in bootstrap narrative"
    ),
}


def bool_feature_flag(
    mapping: Mapping[str, Any] | None,
    key: str,
    default: bool,
) -> bool:
    """Return a boolean feature flag, treating present ``null`` as absent.

    ``dict.get(key, default)`` and ``getattr(obj, name, default)`` supply
    ``default`` only when the key is ABSENT. An explicit YAML/JSON ``null`` is
    *presence*, so the lookup hits, the default never runs, and ``None`` flows
    onward. ``bool(None)`` is ``False`` with no exception, so a default-True
    flag silently flips OFF.

    A human writing ``gas_resistance_enabled: null`` in a config file means
    "leave this at the default". Treating null as absent is the
    least-surprising reading of that intent.

    This is the safe direction for the flags that call this helper: the
    default is the conservative setting (resistance ON, temperature
    dependence ON, needs_experiment ON, import penalty ON in bootstrap
    narrative). Present-null used to flip each to the permissive setting.
    Refusing the null would make a currently-loadable config fail — a larger
    behavioural change for no safety gain. The danger is the silent flip to
    permissive; treating null as absent eliminates that without breaking
    anything that loads today.

    This helper is for BOOLEAN FEATURE FLAGS whose default is the
    conservative setting, and only for keys admitted in
    ``CONSERVATIVE_BOOL_FEATURE_FLAGS``. It is not a licence to swallow
    nulls generally. A null numeric setpoint, a null identifier, or a null
    measurement must still refuse. ``furnace_max_T_C: null`` keeps its own
    distinct documented meaning ("inherit from the material"), which is NOT
    the same thing as "use the default".

    The ``default`` argument is evaluated at the call site, so a computed
    default such as ``mode == "bootstrap_narrative"`` still runs when the
    stored value is null.
    """
    if key not in CONSERVATIVE_BOOL_FEATURE_FLAGS:
        admitted = ", ".join(sorted(CONSERVATIVE_BOOL_FEATURE_FLAGS))
        raise ValueError(
            f"{key!r} is not an admitted conservative boolean feature flag; "
            f"present-null-as-absent is only for {{{admitted}}}"
        )
    if key in _CONSTANT_CONSERVATIVE_TRUE and bool(default) is not True:
        raise ValueError(
            f"{key} default must be the audited conservative setting True "
            f"({_CONSERVATIVE_DEFAULT_NOTES[key]}); got {default!r}"
        )
    if mapping is None or key not in mapping:
        return bool(default)
    value = mapping[key]
    if value is None:
        return bool(default)
    return bool(value)


__all__ = (
    "CONSERVATIVE_BOOL_FEATURE_FLAGS",
    "bool_feature_flag",
)
