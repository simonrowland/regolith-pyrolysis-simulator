"""FactSAGE/ChemApp backend diagnostics."""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Optional, TextIO

from simulator.melt_backend.factsage import (
    DEFAULT_COMPONENT_MAP,
    DEFAULT_SPECIES_MAP,
    FactSAGEBackend,
)
from simulator.melt_backend.factsage_config import (
    FactSAGEConfigError,
    load_factsage_config,
)
from simulator.state import MOLAR_MASS


DEFAULT_SMOKE_COMPOSITION_KG = {
    'SiO2': 45.0,
    'TiO2': 2.0,
    'Al2O3': 14.0,
    'FeO': 12.0,
    'Fe2O3': 0.2,
    'MgO': 10.0,
    'CaO': 10.0,
    'Na2O': 3.0,
    'K2O': 1.0,
    'Cr2O3': 0.5,
    'MnO': 0.5,
    'P2O5': 0.2,
    'NiO': 0.1,
    'CoO': 0.1,
}


def run_doctor(config_path: Optional[str] = None,
               stream: Optional[TextIO] = None) -> int:
    """Run FactSAGE diagnostics and return a process exit code."""
    out = stream or sys.stdout
    _write(out, 'FactSAGE doctor')

    try:
        config = load_factsage_config(config_path)
    except FactSAGEConfigError as exc:
        _write(out, f'[fail] config: {exc}')
        return 1

    configured_path = config_path or 'FACTSAGE_CONFIG'
    _write(out, f'[info] config source: {configured_path}')

    requested_module = config.get('chemapp_module')
    module_names = (
        (str(requested_module),)
        if requested_module
        else ('chemapp.friendly', 'ChemApp')
    )
    for module_name in module_names:
        try:
            importlib.import_module(module_name)
            _write(out, f'[ ok ] ChemApp import: {module_name}')
            break
        except ImportError as exc:
            last_import_error = exc
    else:
        _write(out, f'[fail] ChemApp import: {module_names[0]} '
                    f'({last_import_error})')
        return 1

    datafile = (
        config.get('datafile_path')
        or config.get('database_path')
        or config.get('factsage_datafile')
        or config.get('data_file')
    )
    if datafile:
        _write(out, f'[info] data file: {datafile}')
    else:
        _write(out, '[fail] data file: not configured')
        return 1

    component_map = dict(DEFAULT_COMPONENT_MAP)
    component_map.update(config.get('component_map') or {})
    species_map = dict(DEFAULT_SPECIES_MAP)
    species_map.update(config.get('species_map') or {})

    disabled = sorted(k for k, v in component_map.items() if v is None)
    mapped = sorted(k for k, v in component_map.items() if v is not None)
    _write(out, '[info] mapped melt components: ' + ' '.join(mapped))
    if disabled:
        _write(out, '[warn] disabled melt components: ' + ' '.join(disabled))
    _write(out, '[info] configured vapor species: ' + ' '.join(sorted(species_map)))

    backend = FactSAGEBackend()
    if not backend.initialize(config):
        message = backend.last_error or '; '.join(backend.warnings)
        _write(out, f'[fail] backend initialize: {message or "unavailable"}')
        return 1
    _write(out, '[ ok ] backend initialize')
    _write(out, f'[info] capabilities: {backend.capability_summary()}')

    composition_mol = dict(
        config.get('smoke_composition_mol')
        or _smoke_composition_mol_for(component_map)
    )
    _write(out, '[info] smoke composition mol: ' + ' '.join(sorted(composition_mol)))

    try:
        result = backend.equilibrate(
            temperature_C=float(config.get('smoke_temperature_C', 1600.0)),
            composition_mol=composition_mol,
            fO2_log=float(config.get('smoke_fO2_log', -9.0)),
            pressure_bar=float(config.get('smoke_pressure_bar', 1e-6)),
        )
    except Exception as exc:
        _write(out, f'[fail] smoke equilibrium: {exc}')
        for warning in backend.warnings:
            _write(out, f'[warn] {warning}')
        return 1

    _write(out, '[ ok ] smoke equilibrium')
    _write(out, '[info] phases: ' + (' '.join(result.phases_present) or 'none'))
    vapor_species = sorted(result.vapor_pressures_Pa)
    _write(out, '[info] vapor pressures: ' + (' '.join(vapor_species) or 'none'))
    for warning in backend.warnings:
        _write(out, f'[warn] {warning}')
    return 0


def _smoke_composition_mol_for(component_map: dict) -> dict:
    return {
        oxide: amount / (MOLAR_MASS[oxide] / 1000.0)
        for oxide, amount in DEFAULT_SMOKE_COMPOSITION_KG.items()
        if component_map.get(oxide) is not None
    }


def _write(stream: TextIO, message: str) -> None:
    stream.write(message + '\n')


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Diagnose FactSAGE/ChemApp backend configuration.')
    parser.add_argument(
        '--config',
        help='Path to factsage.local.json. Defaults to FACTSAGE_CONFIG.',
    )
    args = parser.parse_args(argv)
    return run_doctor(args.config)


if __name__ == '__main__':
    raise SystemExit(main())
