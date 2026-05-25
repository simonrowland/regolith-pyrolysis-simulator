"""AlphaMELTS kernel-registered diagnostic provider.

Goal #8 ``ALPHAMELTS-DIAGNOSTIC-GATE`` promotes AlphaMELTS from the
today-hook adapter scaffolding (``simulator.melt_backend.alphamelts``)
into kernel-registered provider posture. AlphaMELTS is **diagnostic
only** for the SILICATE_LIQUIDUS, SILICATE_EQUILIBRIUM, and
EQUILIBRIUM_CRYSTALLIZATION intents -- the provider never emits a
:class:`LedgerTransitionProposal`. See the binding-spec §3 authority
matrix.

Public exports:

- :class:`AlphaMELTSProvider` -- kernel-registered provider.
- :class:`AlphaMELTSDomainGate` -- MELTS oxide-basis gate.
- :class:`LiquidusDiagnostics` -- frozen result payload returned via
  :attr:`IntentResult.diagnostic`.

Package-init cycle convention: the provider sub-modules must NOT
top-level-import ``simulator.accounting.formulas`` or
``simulator.state``; the helpers in :mod:`engines.alphamelts.provider`
import lazily inside method bodies for the same reason described in
``engines/builtin/__init__.py``. Lower-level kernel modules
(``simulator.chemistry.kernel.*``) stay at module top because they do
not loop back through ``simulator/__init__.py``.
"""

from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.alphamelts.provider import AlphaMELTSProvider
from engines.alphamelts.result import LiquidusDiagnostics

__all__ = (
    'AlphaMELTSDomainGate',
    'AlphaMELTSProvider',
    'LiquidusDiagnostics',
)
