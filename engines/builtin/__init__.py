"""Builtin chemistry provider plane (kernel-registered).

Source tree for the simulator's builtin chemistry providers. See
``engines/builtin/README.md`` for the migration plan, and
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §3 (authority
matrix) for which intents the builtin owns.

Public exports:

- :class:`BuiltinVaporPressureProvider` -- authoritative provider for
  ``ChemistryIntent.VAPOR_PRESSURE`` (Ellingham/Antoine, diagnostic
  result -- no ledger mutation).
- :class:`BuiltinEvaporationFluxProvider` -- authoritative provider for
  ``ChemistryIntent.EVAPORATION_FLUX`` (Hertz-Knudsen-Langmuir,
  diagnostic kinetic flux).
- :class:`BuiltinEvaporationTransitionProvider` -- authoritative provider
  for ``ChemistryIntent.EVAPORATION_TRANSITION`` (mol-native
  debit/credit pair; the first ledger-mutating intent in the migration).
- :class:`BuiltinCondensationRouteProvider` -- authoritative provider
  for ``ChemistryIntent.CONDENSATION_ROUTE`` (mol-native
  debit overhead_gas / credit condensation_train; the second
  ledger-mutating intent in the migration, owns SiO disproportionation
  on deposition).
- :class:`BuiltinNativeFeSaturationProvider` -- authoritative provider
  for ``ChemistryIntent.NATIVE_FE_SATURATION`` (mol-native
  FeO -> Fe + 0.5 O2 split).
- :class:`BuiltinNativeFeMetallicTapProvider` -- authoritative provider
  for ``ChemistryIntent.NATIVE_FE_METALLIC_TAP`` (mol-native existing-Fe
  partition between drain tap and vapor).

Package-init cycle convention (binding for every provider in this
package): the providers must NOT top-level-import
``simulator.accounting.formulas``, ``simulator.state``, or any other
sub-module that re-enters ``simulator/__init__.py``. ``simulator.core``
imports the providers (via the kernel registry); ``simulator.core``
itself is imported by ``simulator/__init__.py``; a top-level import of
the simulator sub-modules from a provider therefore creates a cycle:
``simulator/__init__.py`` -> ``simulator.core`` -> provider module ->
``simulator.accounting.formulas`` (or ``simulator.state``) -> back into
``simulator/__init__.py``. The convention is to import lazily inside
method bodies (``dispatch`` and helpers in ``_common.py``) and let
``simulator.chemistry.kernel.*`` stay at module top -- those modules do
NOT loop back through ``simulator/__init__.py``.
"""

from engines.builtin.condensation_route import (
    BuiltinCondensationRouteProvider,
)
from engines.builtin.ca_aluminothermic_step import (
    BuiltinCaAluminothermicStepProvider,
)
from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
)
from engines.builtin.native_fe_saturation import (
    BuiltinNativeFeSaturationProvider,
)
from engines.builtin.native_fe_metallic_tap import (
    BuiltinNativeFeMetallicTapProvider,
)
from engines.builtin.overhead_bleed import BuiltinOverheadBleedProvider
from engines.builtin.overhead_gas_equilibrium import (
    BuiltinOverheadGasEquilibriumProvider,
)
from engines.builtin.stage0_pretreatment import (
    BuiltinStage0PretreatmentProvider,
)
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

__all__ = [
    "BuiltinCondensationRouteProvider",
    "BuiltinCaAluminothermicStepProvider",
    "BuiltinElectrolysisStepProvider",
    "BuiltinEvaporationFluxProvider",
    "BuiltinEvaporationTransitionProvider",
    "BuiltinMetallothermicStepProvider",
    "BuiltinNativeFeMetallicTapProvider",
    "BuiltinNativeFeSaturationProvider",
    "BuiltinOverheadBleedProvider",
    "BuiltinOverheadGasEquilibriumProvider",
    "BuiltinStage0PretreatmentProvider",
    "BuiltinVaporPressureProvider",
]
