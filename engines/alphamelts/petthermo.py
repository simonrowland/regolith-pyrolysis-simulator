"""PetThermoTools (Python API) path for the AlphaMELTS provider.

Thin glue layer between :class:`AlphaMELTSProvider` and the today-hook
adapter in :mod:`simulator.melt_backend.alphamelts`. The math lives in
the adapter (the PetThermoTools / MELTSdynamic / equilibrate_MELTS
call) -- this module owns ONLY the mode-selection contract: it tells
the provider whether the Python API path is reachable and runs an
equilibration through it.

Goal #8 checklist item 3 calls for a separate ``petthermo.py`` for the
Python API path and ``subprocess_runner.py`` + ``parser.py`` for the
binary path, "both behind a single provider entry". The provider entry
is :class:`AlphaMELTSProvider.dispatch`. The runtime split is owned by
the adapter, so these glue modules stay thin -- the alternative
(re-implementing PetThermoTools / subprocess logic at this layer)
would duplicate hardened code that goal #1 already locked down.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def equilibrate_via_python_api(
    backend: Any,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    species_formula_registry: Mapping[str, Any],
) -> Any:
    """Run AlphaMELTS via the PetThermoTools Python API.

    The provider passes the live :class:`AlphaMELTSBackend` instance
    (already initialised) so this function delegates to the adapter's
    ``equilibrate`` method, which itself selects the python_api branch
    when ``backend._mode == 'python_api'``. Returns the adapter's
    :class:`EquilibriumResult` unchanged; the provider then projects it
    into a :class:`LiquidusDiagnostics`.

    Raises ``RuntimeError`` if the backend is not in python_api mode --
    the caller (the provider) is responsible for choosing the right
    path; calling this function on a subprocess-only backend is a bug.
    """
    mode = getattr(backend, '_mode', None)
    if mode != 'python_api':
        raise RuntimeError(
            'equilibrate_via_python_api requires backend._mode == '
            f'"python_api"; got {mode!r}. Provider must dispatch the '
            'subprocess path instead.'
        )
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=float(fO2_log),
        composition_mol_by_account=composition_mol_by_account,
        species_formula_registry=species_formula_registry,
    )


def python_api_available(backend: Any) -> bool:
    """True when the backend has initialised the PetThermoTools path."""
    return getattr(backend, '_mode', None) == 'python_api'


__all__ = ('equilibrate_via_python_api', 'python_api_available')
