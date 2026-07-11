"""Subprocess (alphaMELTS binary) path for the AlphaMELTS provider.

Thin glue layer between :class:`AlphaMELTSProvider` and the binary
subprocess path in :mod:`simulator.melt_backend.alphamelts`. The actual
subprocess invocation and stdout assembly lives in the adapter's
:meth:`AlphaMELTSBackend._equilibrate_subprocess`; this module exposes
the path-selection contract (``subprocess_available``) and an
equilibrate entry point.

See :mod:`engines.alphamelts.petthermo` for the parallel Python API
glue and :mod:`engines.alphamelts.parser` for the result projection.
Together they satisfy goal #8 checklist item 3 (subprocess +
PetThermoTools both reachable through a single provider entry).
"""

from __future__ import annotations

from typing import Any, Mapping

from simulator.melt_backend.alphamelts_contract import (
    AlphaMELTSSubprocessRunMode,
)


def equilibrate_via_subprocess(
    backend: Any,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    species_formula_registry: Mapping[str, Any],
    run_mode: AlphaMELTSSubprocessRunMode | str,
) -> Any:
    """Run AlphaMELTS via the binary subprocess path.

    The provider passes the live :class:`AlphaMELTSBackend` instance
    (already initialised; ``backend._mode == 'subprocess'``). The
    adapter's :meth:`equilibrate` method selects the subprocess branch
    internally; this function is a thin pass-through so the provider's
    dispatch table stays uniform with the python_api path.

    Raises ``RuntimeError`` if the backend is not in subprocess mode --
    the caller (the provider) is responsible for routing.
    """
    mode = getattr(backend, '_mode', None)
    if mode != 'subprocess':
        raise RuntimeError(
            'equilibrate_via_subprocess requires backend._mode == '
            f'"subprocess"; got {mode!r}. Provider must dispatch the '
            'python_api path instead.'
        )
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=float(fO2_log),
        composition_mol_by_account=composition_mol_by_account,
        species_formula_registry=species_formula_registry,
        subprocess_run_mode=run_mode,
    )


def subprocess_available(backend: Any) -> bool:
    """True when the backend has initialised the subprocess path."""
    return getattr(backend, '_mode', None) == 'subprocess'


__all__ = ('equilibrate_via_subprocess', 'subprocess_available')
