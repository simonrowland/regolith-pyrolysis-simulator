"""Builtin chemistry provider plane (kernel-registered).

Source tree for the simulator's builtin chemistry providers. See
``engines/builtin/README.md`` for the migration plan, and
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §3 (authority
matrix) for which intents the builtin owns.

Public exports:

- :class:`BuiltinVaporPressureProvider` — authoritative provider for
  ``ChemistryIntent.VAPOR_PRESSURE`` (Ellingham/Antoine, diagnostic
  result — no ledger mutation).
"""

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

__all__ = [
    "BuiltinVaporPressureProvider",
]
