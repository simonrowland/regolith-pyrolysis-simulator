"""VapoRock kernel-registered VAPOR_PRESSURE diagnostic provider.

The provider wraps the today-hook :class:`VapoRockBackend` adapter as a
shadow diagnostic beside builtin Antoine/Ellingham authority. The provider:

- declares ``VAPOR_PRESSURE`` as its sole intent, diagnostic-only,
- declares ``process.cleaned_melt`` as its sole accessible account,
- delegates to :class:`simulator.melt_backend.vaporock.VapoRockBackend`
  for the chemistry (the library import + species-name normalization
  remain owned by the adapter),
- raises :class:`ProviderUnavailableError` when the upstream VapoRock
  library is missing; shadow dispatch records that without blocking
  builtin authoritative pressures.

Public exports:

- :class:`VapoRockProvider` -- the kernel diagnostic provider.
- :class:`VapoRockDiagnostics` -- frozen diagnostic payload.
"""

from engines.vaporock.provider import VapoRockProvider
from engines.vaporock.result import VapoRockDiagnostics

__all__ = [
    'VapoRockDiagnostics',
    'VapoRockProvider',
]
