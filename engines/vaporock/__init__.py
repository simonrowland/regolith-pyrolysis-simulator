"""VapoRock kernel-registered VAPOR_PRESSURE provider.

Promoted from the today-hook :class:`VapoRockBackend` adapter to a
kernel-registered authoritative provider under
``\\goal VAPOROCK-AUTHORITY-PROMOTION`` (#10). The provider:

- declares ``VAPOR_PRESSURE`` as its sole intent, with itself
  authoritative for it,
- declares ``process.cleaned_melt`` as its sole accessible account,
- delegates to :class:`simulator.melt_backend.vaporock.VapoRockBackend`
  for the chemistry (the library import + species-name normalization
  remain owned by the adapter),
- raises :class:`ProviderUnavailableError` when the upstream VapoRock
  library is missing -- silent fallback to the builtin Antoine path is
  forbidden by the goal spec.  The kernel's
  ``allow_fallback_<intent>`` config flag is the explicit opt-in path
  for sites that need the builtin to take over.

Public exports:

- :class:`VapoRockProvider` -- the kernel-authoritative provider.
- :class:`VapoRockDiagnostics` -- frozen diagnostic payload.
"""

from engines.vaporock.provider import VapoRockProvider
from engines.vaporock.result import VapoRockDiagnostics

__all__ = [
    'VapoRockDiagnostics',
    'VapoRockProvider',
]
