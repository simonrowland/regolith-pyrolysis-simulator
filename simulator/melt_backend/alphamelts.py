"""
AlphaMELTS Backend
==================

Wraps alphaMELTS for thermodynamic equilibrium calculations via:

1. Subprocess transport (default): write .melts files, run binary, parse stdout/tables
2. Python API fallback: petthermotools -> alphaMELTS for Python

PetThermoTools 0.4.5 schema verified from installed source:

* import package: ``petthermotools``; distribution: ``petthermotools``.
* compiled MELTS payload is the separate ``meltsdynamic.MELTSdynamic`` loader.
* single equilibrium entry point is ``equilibrate_MELTS(...)``; it returns
  ``(Results, Affinity)`` where ``Results`` contains ``Conditions``, phase
  composition tables, and ``<phase>_prop`` tables.
* ``fO2_offset`` is a delta from ``fO2_buffer``. The simulator's absolute
  ``fO2_log`` is not passed as an offset.
* MELTS/ThermoEngine chemical-potential output must be converted to
  thermodynamic activity as ``a_i = exp((mu_i - mu_i0) / RT)``. Activity is
  absent when the live path does not supply both ``mu`` and ``mu0``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import math
import os
import re
import signal
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from engines.alphamelts.domain import canonical_melt_oxide_activity_name
from engines.domain_reason import OutOfDomainReason, reason_value
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    resolve_species_formula,
)
from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltBackend,
    MeltBackendError,
    liquid_fraction_from_phase_masses,
)
from simulator.melt_backend.alphamelts_contract import (
    AlphaMELTSSubprocessRunMode,
)
from simulator.melt_backend.vaporock import VapoRockBackend
from simulator.melt_backend.liquidus import (
    LiquidusSampleError,
    LiquidusSolidusResult,
    find_liquidus_solidus_by_fraction,
)
from simulator.physical_constants import GAS_CONSTANT


ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C = 800.0
ALPHAMELTS_PYTHON_MIN_PRESSURE_BAR = 1.0e-6
ALPHAMELTS_SUBPROCESS_MIN_PRESSURE_BAR = 1.0
MELTS_OXIDE_BASIS = (
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
    'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
)
MELTS_MAJOR_OXIDES = set(MELTS_OXIDE_BASIS)
# MELTS binding spec: admit only compositions with major oxide sum >95.0 wt%.
# Exact 95.0 wt% is rejected so adapter and provider gates share one boundary.
MELTS_MAJOR_OXIDE_MIN_TOTAL_WT_PCT = 95.0
MELTS_OXIDE_ALIASES = {
    oxide.lower(): oxide
    for oxide in MELTS_OXIDE_BASIS
}
MELTS_OXIDE_ALIASES.update({
    'feo_total': 'FeO_total',
    'feot': 'FeO_total',
    'feototal': 'FeO_total',
    'feo_tot': 'FeO_total',
})
FE_REDOX_BUFFERS = {'QFM', 'NNO', 'IW', 'HM'}
FE_REDOX_BUFFER_ALIASES = {'FMQ': 'QFM'}
FE3_TO_FEOT_FACTOR = 0.8998

ALPHAMELTS_REASON_TIMEOUT = 'timeout'
ALPHAMELTS_REASON_SUBPROCESS_DIED = 'subprocess_died'
ALPHAMELTS_REASON_NONZERO_EXIT = 'nonzero_exit'
ALPHAMELTS_REASON_NO_CONVERGENCE = 'no_convergence'
ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT = 'parse_empty_output'
ALPHAMELTS_REASON_MISSING_BINARY = 'missing_binary'
ALPHAMELTS_REASON_RUN_MODE_REQUIRED = 'subprocess_run_mode_required'
ALPHAMELTS_REASON_RUN_MODE_INVALID = 'subprocess_run_mode_invalid'
ALPHAMELTS_REASON_EXECUTED_T_MISSING = 'executed_temperature_missing'
ALPHAMELTS_REASON_EXECUTED_T_MISMATCH = 'executed_temperature_mismatch'
ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED = 'subprocess_pressure_below_minimum'
ALPHAMELTS_REASON_FO2_CONSTRAINT_INVALID = 'fo2_constraint_invalid'
ALPHAMELTS_REASON_FO2_CONSTRAINT_UNAPPLIED = 'fo2_constraint_unapplied'
ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING = 'system_output_missing'
ALPHAMELTS_REASON_PHASE_MASS_INCOMPLETE = 'phase_mass_incomplete'
ALPHAMELTS_EXECUTED_T_TOLERANCE_C = 0.01
ALPHAMELTS_FO2_ECHO_TOLERANCE_LOG10 = 1.0e-6
# alphaMELTS input serialization emits an oxide only above this wt% value.
# Values at/below it are native zero-component cells, regardless of Python sign.
ALPHAMELTS_MIN_EMITTED_COMPONENT_WT_PCT = 0.001

ALPHAMELTS_BACKEND_FAILURE_REASON_CODE_KEY = 'backend_failure_reason_code'
ALPHAMELTS_BACKEND_FAILURE_CATEGORY_KEY = 'backend_failure_category'
ALPHAMELTS_BACKEND_FAILURE_CATEGORY_BY_REASON = {
    ALPHAMELTS_REASON_TIMEOUT: OutOfDomainReason.NOT_CONVERGED.value,
    ALPHAMELTS_REASON_SUBPROCESS_DIED: 'engine_crash',
    ALPHAMELTS_REASON_NONZERO_EXIT: OutOfDomainReason.NOT_CONVERGED.value,
    ALPHAMELTS_REASON_NO_CONVERGENCE: 'out_of_domain',
    ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT: OutOfDomainReason.NOT_CONVERGED.value,
    ALPHAMELTS_REASON_MISSING_BINARY: OutOfDomainReason.BACKEND_UNAVAILABLE.value,
    ALPHAMELTS_REASON_RUN_MODE_REQUIRED: 'contract_error',
    ALPHAMELTS_REASON_RUN_MODE_INVALID: 'contract_error',
    ALPHAMELTS_REASON_EXECUTED_T_MISSING: 'parse_error',
    ALPHAMELTS_REASON_EXECUTED_T_MISMATCH: 'contract_error',
    ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED: 'out_of_domain',
    ALPHAMELTS_REASON_FO2_CONSTRAINT_INVALID: 'contract_error',
    ALPHAMELTS_REASON_FO2_CONSTRAINT_UNAPPLIED: 'contract_error',
    ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING: 'parse_error',
    ALPHAMELTS_REASON_PHASE_MASS_INCOMPLETE: 'parse_error',
}

ALPHAMELTS_BACKEND_FAILURE_MESSAGES = {
    ALPHAMELTS_REASON_TIMEOUT: 'AlphaMELTS subprocess timed out',
    ALPHAMELTS_REASON_SUBPROCESS_DIED: (
        'AlphaMELTS subprocess exited before producing a result'
    ),
    ALPHAMELTS_REASON_NONZERO_EXIT: (
        'AlphaMELTS subprocess returned a nonzero exit code'
    ),
    ALPHAMELTS_REASON_NO_CONVERGENCE: (
        'AlphaMELTS reported no convergence before phase rows'
    ),
    ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT: (
        'AlphaMELTS stdout had no parseable phase assemblage'
    ),
    ALPHAMELTS_REASON_MISSING_BINARY: (
        'AlphaMELTS subprocess binary was not available'
    ),
    ALPHAMELTS_REASON_RUN_MODE_REQUIRED: (
        'AlphaMELTS subprocess run mode was not selected explicitly'
    ),
    ALPHAMELTS_REASON_RUN_MODE_INVALID: (
        'AlphaMELTS subprocess run mode was invalid'
    ),
    ALPHAMELTS_REASON_EXECUTED_T_MISSING: (
        'AlphaMELTS did not report its executed temperature'
    ),
    ALPHAMELTS_REASON_EXECUTED_T_MISMATCH: (
        'AlphaMELTS executed at a temperature other than the isothermal request'
    ),
    ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED: (
        'AlphaMELTS subprocess does not support the requested pressure'
    ),
    ALPHAMELTS_REASON_FO2_CONSTRAINT_INVALID: (
        'AlphaMELTS subprocess fO2 constraint was invalid'
    ),
    ALPHAMELTS_REASON_FO2_CONSTRAINT_UNAPPLIED: (
        'AlphaMELTS transport cannot apply the requested absolute fO2'
    ),
    ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING: (
        'AlphaMELTS did not emit required system properties'
    ),
    ALPHAMELTS_REASON_PHASE_MASS_INCOMPLETE: (
        'AlphaMELTS phase rows do not close to the engine system mass'
    ),
}


class AlphaMELTSSubprocessContractError(MeltBackendError):
    """Typed failure for subprocess request/output contract violations."""


def _alphamelts_backend_failure_category(reason_code: str,
                                         backend_status: str | None = None
                                         ) -> str | None:
    return (
        ALPHAMELTS_BACKEND_FAILURE_CATEGORY_BY_REASON.get(reason_code)
        or backend_status
    )


def _annotate_alphamelts_backend_failure(
    payload: dict[str, object],
    *,
    reason_code: str | None,
    backend_status: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    if reason_code is None:
        return payload
    payload[ALPHAMELTS_BACKEND_FAILURE_REASON_CODE_KEY] = str(reason_code)
    category = _alphamelts_backend_failure_category(
        str(reason_code),
        backend_status,
    )
    if category is not None:
        payload[ALPHAMELTS_BACKEND_FAILURE_CATEGORY_KEY] = category
    if message is not None:
        payload.setdefault('backend_status_reason_message', message)
    return payload


def _signal_name(returncode: int) -> str:
    try:
        return signal.Signals(-int(returncode)).name
    except ValueError:
        return f'signal {-int(returncode)}'


def _alphamelts_backend_failure_detail(reason_code: str,
                                       detail: str | None = None) -> str:
    message = ALPHAMELTS_BACKEND_FAILURE_MESSAGES[reason_code]
    if detail:
        return (
            f'{message} [backend_status_reason={reason_code}]: {detail}'
        )
    return f'{message} [backend_status_reason={reason_code}]'


def _alphamelts_backend_failure_error(reason_code: str,
                                      detail: str | None = None
                                      ) -> AlphaMELTSSubprocessContractError:
    error = AlphaMELTSSubprocessContractError(
        _alphamelts_backend_failure_detail(reason_code, detail)
    )
    error.backend_status_reason = reason_code  # type: ignore[attr-defined]
    error.backend_failure_reason_code = reason_code  # type: ignore[attr-defined]
    error.backend_failure_category = _alphamelts_backend_failure_category(
        reason_code
    )  # type: ignore[attr-defined]
    error.backend_status_reason_message = (
        ALPHAMELTS_BACKEND_FAILURE_MESSAGES[reason_code]
    )
    return error


def _normalize_subprocess_run_mode(
    value: AlphaMELTSSubprocessRunMode | str | None,
) -> AlphaMELTSSubprocessRunMode:
    if value is None:
        raise _alphamelts_backend_failure_error(
            ALPHAMELTS_REASON_RUN_MODE_REQUIRED
        )
    if isinstance(value, AlphaMELTSSubprocessRunMode):
        return value
    try:
        return AlphaMELTSSubprocessRunMode(str(value))
    except ValueError as exc:
        raise _alphamelts_backend_failure_error(
            ALPHAMELTS_REASON_RUN_MODE_INVALID,
            repr(value),
        ) from exc
PETTHERMOTOOLS_NON_PHASE_KEYS = {
    'All', 'Mass', 'Volume', 'rho', 'Conditions', 'Input', 'Affinity',
    'Activities', 'activities', 'activity_coefficients',
    'melt_oxide_activities',
    'chemical_potentials', 'chem_potentials', 'mu', 'mu_oxides',
    'oxide_mu', 'standard_chemical_potentials', 'pure_chemical_potentials',
    'reference_chemical_potentials', 'mu0', 'mu0_oxides', 'oxide_mu0',
}
# Single-sourced from the physical_constants leaf (SC-CONST pass-B); byte-identical
# to the prior local literal (8.31446261815324).
GAS_CONSTANT_J_PER_MOL_K = GAS_CONSTANT
ACTIVITY_KEYS_BY_VAPOR_SPECIES = {
    'Na': ('Na', 'Na2O', 'NaAlSi3O8'),
    'K': ('K', 'K2O', 'KAlSi3O8'),
    'Mg': ('Mg', 'MgO', 'Mg2SiO4', 'MgSiO3'),
    'Fe': ('Fe', 'FeO', 'Fe2SiO4', 'FeSiO3'),
    'Ca': ('Ca', 'CaO', 'CaSiO3', 'CaMgSi2O6', 'CaAl2Si2O8'),
    'Al': ('Al', 'Al2O3', 'CaAl2Si2O8', 'NaAlSi3O8', 'KAlSi3O8'),
    'Si': ('Si', 'SiO2', 'CaSiO3', 'Mg2SiO4', 'MgSiO3'),
    'SiO': ('SiO', 'SiO2', 'CaSiO3', 'Mg2SiO4', 'MgSiO3'),
    'Ti': ('Ti', 'TiO2'),
    'Cr': ('Cr', 'Cr2O3', 'CrO2'),
    'CrO2': ('CrO2', 'Cr2O3'),
    'Mn': ('Mn', 'MnO'),
}


def activity_from_chem_potential(mu: float, mu0: float, T_K: float) -> float:
    """Convert chemical potential to pure-endmember-referenced activity."""
    mu_val = float(mu)
    mu0_val = float(mu0)
    T_val = float(T_K)
    if not (
        math.isfinite(mu_val)
        and math.isfinite(mu0_val)
        and math.isfinite(T_val)
    ):
        raise ValueError('chemical potentials and temperature must be finite')
    if T_val <= 0.0:
        raise ValueError('temperature must be positive')
    return math.exp((mu_val - mu0_val) / (GAS_CONSTANT_J_PER_MOL_K * T_val))


class _MELTSBackendSupport(MeltBackend):
    """
    AlphaMELTS thermodynamic backend.

    Shared MELTS-family input, domain, result, and diagnostic plumbing.
    """

    backend_name = 'alphamelts'

    def __init__(self):
        self._mode: Optional[str] = None  # 'python_api' or 'subprocess'
        self._engine_path: Optional[Path] = None
        self._binary_path: Optional[Path] = None
        self._pet_available = False
        self._pet_module = None
        self._pet_melts = None
        self._pet_import_error: Optional[ImportError] = None
        self._pet_payload_preloaded = False
        self._engine_version: Optional[str] = None
        self._vaporock_available = False
        self._vaporock_helper: Optional[VapoRockBackend] = None
        self._vaporock_unavailable_logged = False
        self._redox_buffer: Optional[str] = None
        self._fo2_offset: Optional[float] = None
        self._fe3fet_ratio: Optional[float] = None
        self._model = 'MELTSv1.0.2'
        self._timeout_s = 20.0
        self._last_normalization_warnings: List[str] = []
        self._vapor_pressure_table: Optional[dict] = None
        self._subprocess_vapor_pressure_provider = None
        self._pseudo_vapor_pressure_warning_seen: set[str] = set()

    def initialize(self, config: dict) -> bool:
        """
        Detect available alphaMELTS interfaces.

        Checks in order unless ``mode`` pins a transport:
        1. alphaMELTS binary in engines/alphamelts/
        2. alphaMELTS on system PATH
        3. PetThermoTools Python package
        """
        config = self._alphamelts_config(config)
        self.close()
        requested_mode = self._normalize_mode(config.get('mode'))
        self._mode = None
        self._engine_path = None
        self._binary_path = None
        self._engine_version = None
        self._pet_available = False
        self._pet_module = None
        self._pet_melts = None
        self._pet_payload_preloaded = False
        self._redox_buffer = self._normalize_redox_buffer(
            config.get('fO2_buffer', config.get('redox_buffer')))
        self._fo2_offset = self._optional_float(config.get('fO2_offset'))
        self._fe3fet_ratio = self._normalize_fe3fet_ratio(
            config.get('Fe3Fet_Liq', config.get('fe3fet_ratio')))
        self._model = str(config.get('model', self._model))
        self._timeout_s = float(config.get('timeout_s', self._timeout_s))
        require_petthermotools = bool(
            config.get('require_petthermotools')
            or requested_mode == 'python_api'
        )
        require_subprocess = requested_mode == 'subprocess'

        if (
            self._mode is None
            and (requested_mode == 'python_api' or require_petthermotools)
        ):
            self._initialize_petthermotools(
                require_petthermotools=require_petthermotools
            )

        # Vapor-side delegate: lazy-init ONE real VapoRockBackend helper
        # (lowercase ``vaporock`` import lives inside ``VapoRockBackend``).
        # The helper is the tested adapter — it owns oxide projection, the
        # log10(bar)->Pa conversion, and the (g)-suffix normalisation — so
        # alphaMELTS does NOT re-implement any of that.  is_available() is
        # the real import gate (the old uppercase-module probe never
        # resolved against the lowercase ``vaporock`` upstream package).  The
        # INELIGIBLE_ACTIVE_BACKENDS guard only blocks selecting vaporock as
        # the *active standalone* backend; an internal helper is unaffected.
        if self._vaporock_helper is None:
            self._vaporock_helper = VapoRockBackend()
        with warnings.catch_warnings():
            # The helper warns once on a missing library; that becomes the
            # WARN below so we don't double-emit the upstream UserWarning.
            warnings.simplefilter('ignore', UserWarning)
            self._vaporock_helper.initialize({})
        self._vaporock_available = self._vaporock_helper.is_available()
        if not self._vaporock_available and not self._vaporock_unavailable_logged:
            warnings.warn(
                'VapoRock vapor-melt library unavailable; alphaMELTS vapor '
                'pressures fall back to activity x Antoine rows; '
                'vapor_pressures_source distinguishes pure-component '
                'first-principles rows from backsolved VapoRock curve-fit rows.',
                stacklevel=2,
            )
            self._vaporock_unavailable_logged = True

        # Try binary
        if self._mode is None and requested_mode in (None, 'subprocess'):
            from simulator.engine_local_config import find_alphamelts_binary

            project_root = Path(__file__).parent.parent.parent
            engine_root = project_root / 'engines' / 'alphamelts'
            engine_path = engine_root / 'run_alphamelts.command'
            binary_path = find_alphamelts_binary(engine_root)
            if binary_path is None:
                binary_path = self._find_project_binary(engine_root)
            if engine_path.exists() or binary_path is not None:
                self._engine_path = engine_path if engine_path.exists() else binary_path
                self._binary_path = binary_path
                self._mode = 'subprocess'
            else:
                # Check system PATH
                try:
                    result = subprocess.run(
                        ['alphamelts', '--version'],
                        capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self._engine_path = Path('alphamelts')
                        self._binary_path = Path('alphamelts')
                        self._mode = 'subprocess'
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
            if self._mode is None and require_subprocess:
                raise ImportError('AlphaMELTS subprocess transport unavailable')

        if self._mode is None and requested_mode in (None, 'python_api'):
            self._initialize_petthermotools(
                require_petthermotools=require_petthermotools
            )

        return self._mode is not None

    def close(self) -> None:
        """Idempotently release transport-owned native resources."""
        return None

    def _initialize_petthermotools(
        self,
        *,
        require_petthermotools: bool,
    ) -> None:
        try:
            self._pet_module = self._import_petthermotools()
            self._engine_version = None
            self._preload_petthermotools_payload(self._pet_module)
            self._pet_available = True
            self._mode = 'python_api'
        except ImportError:
            self._pet_available = False
            self._pet_module = None
            self._pet_melts = None
            self._pet_payload_preloaded = False
            self._pet_import_error = ImportError(
                'PetThermoTools Python path unavailable: '
                'petthermotools and meltsdynamic must both import'
            )
            if require_petthermotools:
                raise self._pet_import_error

    def _alphamelts_config(self, config: dict) -> dict:
        if not isinstance(config, Mapping):
            return {}
        top_mode = config.get('mode')
        top_bridge = config.get('python_bridge')
        nested = config.get('alphamelts')
        if isinstance(nested, Mapping):
            merged = dict(config)
            merged.update(nested)
            if self._normalize_mode(top_mode) == 'subprocess':
                merged['mode'] = 'subprocess'
            if str(top_bridge or '').strip().lower() == 'subprocess':
                merged['python_bridge'] = 'subprocess'
            return merged
        return dict(config)

    def _normalize_mode(self, value) -> Optional[str]:
        if value is None or value == '' or str(value).lower() == 'auto':
            return None
        mode = str(value).strip().lower()
        aliases = {
            'petthermotools': 'python_api',
            'ptt': 'python_api',
            'python': 'python_api',
            'binary': 'subprocess',
        }
        mode = aliases.get(mode, mode)
        if mode not in {'python_api', 'subprocess'}:
            raise ValueError(f'unsupported AlphaMELTS mode: {value}')
        return mode

    def _optional_float(self, value) -> Optional[float]:
        if value is None or value == '':
            return None
        return float(value)

    def _normalize_redox_buffer(self, value) -> Optional[str]:
        if value is None or value == '':
            return None
        buffer = str(value).strip().upper()
        buffer = FE_REDOX_BUFFER_ALIASES.get(buffer, buffer)
        if buffer not in FE_REDOX_BUFFERS:
            raise ValueError(f'unsupported AlphaMELTS redox buffer: {value}')
        return buffer

    def _normalize_fe3fet_ratio(self, value) -> Optional[float]:
        ratio = self._optional_float(value)
        if ratio is None:
            return None
        if not 0.0 <= ratio <= 1.0:
            raise ValueError('Fe3Fet ratio must be in [0, 1]')
        return ratio

    def _import_petthermotools(self):
        try:
            return importlib.import_module('petthermotools')
        except ImportError:
            return importlib.import_module('PetThermoTools')

    def _preload_petthermotools_payload(self, module) -> None:
        loader = getattr(module, 'MELTSdynamic', None)
        if loader is None:
            try:
                meltsdynamic = importlib.import_module('meltsdynamic')
                loader = getattr(meltsdynamic, 'MELTSdynamic', None)
            except ImportError as exc:
                raise ImportError(
                    'PetThermoTools compiled MELTS payload missing: '
                    'cannot import meltsdynamic.MELTSdynamic'
                ) from exc
        if loader is None:
            raise ImportError(
                'PetThermoTools compiled MELTS payload missing: '
                'MELTSdynamic loader not found'
            )
        self._pet_melts = loader(self._melts_model_code())
        self._pet_payload_preloaded = True

    def _melts_model_code(self) -> int:
        if self._model == 'pMELTS':
            return 2
        if self._model == 'MELTSv1.1.0':
            return 3
        if self._model == 'MELTSv1.2.0':
            return 4
        return 1

    def _find_project_binary(self, engine_root: Path) -> Optional[Path]:
        if not engine_root.exists():
            return None
        binary_names = (
            'alphamelts2',
            'alphamelts_macos',
            'alphamelts_linux',
            'alphamelts_win64.exe',
        )
        for name in binary_names:
            direct = engine_root / name
            if direct.exists():
                return direct
        for child in sorted(engine_root.iterdir()):
            if not child.is_dir():
                continue
            for name in binary_names:
                candidate = child / name
                if candidate.exists():
                    return candidate
        return None

    def is_available(self) -> bool:
        return self._mode is not None

    def get_engine_version(self) -> str:
        if self._engine_version:
            return self._engine_version
        if self._mode == 'subprocess':
            binary_version = self._subprocess_engine_version()
            if binary_version:
                self._engine_version = binary_version
                return self._engine_version
        if self._pet_module is not None:
            version = getattr(self._pet_module, '__version__', None)
            if version:
                self._engine_version = f'petthermotools {version}'
                return self._engine_version
        for package_name in ('petthermotools', 'PetThermoTools'):
            try:
                version = importlib.metadata.version(package_name)
                self._engine_version = f'{package_name} {version}'
                return self._engine_version
            except importlib.metadata.PackageNotFoundError:
                continue
        binary_version = self._subprocess_engine_version()
        if binary_version:
            self._engine_version = binary_version
            return self._engine_version
        return 'unavailable'

    def _subprocess_engine_version(self) -> Optional[str]:
        from simulator.engine_local_config import (
            cache_version_for,
            warn_legacy_once,
        )

        config_version = cache_version_for('alphamelts')
        if config_version is not None:
            return config_version

        if self._binary_path is None and self._engine_path is None:
            return None
        binary = self._binary_path or self._engine_path
        try:
            result = subprocess.run(
                [str(binary), '--version'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            text = (result.stdout or result.stderr).strip().splitlines()
            if result.returncode == 0 and text:
                legacy = text[0]
            else:
                legacy = f'alphaMELTS subprocess ({binary})'
        except (OSError, subprocess.TimeoutExpired):
            legacy = f'alphaMELTS subprocess ({binary})'
        warn_legacy_once(
            'alphamelts',
            'engines.local.toml absent; using legacy alphaMELTS '
            'path-based identity for cache comparison',
        )
        return legacy

    def capabilities(self) -> Dict[str, object]:
        caps = super().capabilities()
        caps['engine_version'] = self.get_engine_version()
        return caps

    def ledger_account_policies(self) -> tuple[Any, ...]:
        return ()

    def get_vapor_species(self) -> List[str]:
        if self._vaporock_available:
            # VapoRock provides 34 species
            return [
                'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
                'SiO', 'FeO', 'MgO', 'CaO', 'AlO', 'TiO', 'NaO', 'KO',
                'O2', 'O', 'SiO2', 'Fe2O3',
            ]
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO', 'Mn', 'Cr']

    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: Optional[float] = None,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None,
                    composition_mol_by_account: Optional[Mapping[str, Mapping[str, float]]] = None,
                    species_formula_registry: Optional[Mapping[str, object]] = None,
                    subprocess_run_mode: AlphaMELTSSubprocessRunMode | str | None = None,
                    ) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium.

        Routes to the configured alphaMELTS transport.
        """
        if fO2_log is None and not self.supports_intrinsic_fO2:
            raise ValueError(
                'intrinsic closed-system fO2 is supported only by the '
                'ThermoEngine transport; other transports require an '
                'explicit absolute fO2_log'
            )
        if composition_mol_by_account is not None:
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                return self._domain_gate_result(
                    temperature_C,
                    pressure_bar,
                    fO2_log,
                    [
                        'unsupported ledger accounts present: '
                        + ', '.join(
                            f'{account}={species}'
                            for account, species in sorted(unsupported.items())
                        )
                    ],
                    diagnostics=self._out_of_domain_diagnostics(
                        temperature_C=temperature_C,
                        pressure_bar=pressure_bar,
                        fO2_log=fO2_log,
                        composition_mol=composition_mol,
                        composition_mol_by_account=composition_mol_by_account,
                        reason=OutOfDomainReason.FORBIDDEN_SPECIES.value,
                    ),
                    reason=OutOfDomainReason.FORBIDDEN_SPECIES,
                )
            composition_mol = {}
            for species, mol in composition_mol_by_account.get(
                'process.cleaned_melt', {}
            ).items():
                composition_mol[species] = (
                    composition_mol.get(species, 0.0) + float(mol))
        if composition_mol is not None:
            composition_kg = {
                species: float(mol)
                * resolve_species_formula(
                    species,
                    species_formula_registry,
                ).molar_mass_kg_per_mol()
                for species, mol in composition_mol.items()
                if float(mol) > 0.0
            }
        else:
            composition_kg = dict(composition_kg or {})

        total_input_kg = sum(
            float(mass_kg)
            for mass_kg in composition_kg.values()
            if math.isfinite(float(mass_kg)) and float(mass_kg) > 0.0
        )

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        crash_diagnostics = self._out_of_domain_diagnostics(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            composition_wt_pct=raw_comp_wt,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
            reason=(
                OutOfDomainReason.MAJOR_SUM.value if not raw_comp_wt else None
            ),
        )
        if not raw_comp_wt:
            # No melt oxides supplied - the engine has nothing valid to act on.
            return self._emit_equilibrium_result(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                diagnostics=crash_diagnostics,
            )
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            diagnostics=crash_diagnostics,
        )
        if domain_rejection is not None:
            return domain_rejection
        comp_wt = self._normalize_composition_to_melts_basis(raw_comp_wt)
        crash_diagnostics = self._out_of_domain_diagnostics(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            composition_wt_pct=raw_comp_wt,
            composition_melts_wt_pct=comp_wt,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
            reason=OutOfDomainReason.NOT_CONVERGED.value,
        )
        warnings = list(self._last_normalization_warnings)

        return self._equilibrate_prepared(
            temperature_C=temperature_C,
            comp_wt=comp_wt,
            fO2_log=fO2_log,
            pressure_bar=pressure_bar,
            warnings=warnings,
            total_input_kg=total_input_kg,
            crash_diagnostics=crash_diagnostics,
            subprocess_run_mode=subprocess_run_mode,
        )

    def _equilibrate_prepared(
        self,
        *,
        temperature_C: float,
        comp_wt: Mapping[str, float],
        fO2_log: Optional[float],
        pressure_bar: float,
        warnings: List[str],
        total_input_kg: float,
        crash_diagnostics: Mapping[str, object],
        subprocess_run_mode: AlphaMELTSSubprocessRunMode | str | None,
    ) -> EquilibriumResult:
        if self._mode == 'python_api':
            return self._equilibrate_python(
                temperature_C,
                comp_wt,
                fO2_log,
                pressure_bar,
                warnings,
                total_input_kg=total_input_kg,
                require_solved_fo2=True,
            )
        if self._mode == 'subprocess':
            return self._equilibrate_subprocess(
                temperature_C, comp_wt, fO2_log, pressure_bar, warnings,
                total_input_kg=total_input_kg,
                diagnostics=crash_diagnostics,
                run_mode=_normalize_subprocess_run_mode(
                    subprocess_run_mode
                ),
            )
        # No PetThermoTools or binary -- the engine is not present.
        return self._emit_equilibrium_result(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=warnings,
            status='unavailable',
        )

    def find_liquidus_solidus(self,
                              composition_kg: Optional[Dict[str, float]] = None,
                              fO2_log: float = -9.0,
                              pressure_bar: float = 1e-6,
                              *,
                              composition_mol: Optional[Dict[str, float]] = None,
                              composition_mol_by_account: Optional[
                                  Mapping[str, Mapping[str, float]]
                              ] = None,
                              species_formula_registry: Optional[
                                  Mapping[str, object]
                              ] = None,
                              min_T_C: float = 400.0,
                              max_T_C: float = 2200.0,
                              scan_step_C: float = 50.0,
                              tolerance_C: float = 2.0,
                              ) -> LiquidusSolidusResult:
        """Find solidus/liquidus when a liquid-fraction transport is live."""
        if not self.is_available():
            return LiquidusSolidusResult(
                status='unavailable',
                warnings=(
                    f'{type(self).__name__} liquidus finder requires an '
                    'initialized transport',
                ),
            )

        comp_wt_result = self._composition_for_liquidus_finder(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
            species_formula_registry=species_formula_registry,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            min_T_C=min_T_C,
        )
        if isinstance(comp_wt_result, LiquidusSolidusResult):
            return comp_wt_result
        comp_wt = comp_wt_result

        if self._mode == 'subprocess':
            # The subprocess has a native liquidus-start mode.  Keep it
            # explicit and separate from isothermal fraction samples: using
            # liquidus mode inside a temperature scan would repeat the same
            # liquidus equilibrium at every requested sample temperature.
            result = self.equilibrate(
                max(min_T_C, ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C),
                composition_kg=composition_kg,
                fO2_log=fO2_log,
                pressure_bar=pressure_bar,
                composition_mol=composition_mol,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_formula_registry,
                subprocess_run_mode=(
                    AlphaMELTSSubprocessRunMode.LIQUIDUS_FINDER
                ),
            )
            liquidus_C = result.liquidus_T_C
            if liquidus_C is None and result.status == 'ok':
                liquidus_C = result.temperature_C
            return LiquidusSolidusResult(
                liquidus_T_C=liquidus_C,
                liquidus_T_K=(
                    liquidus_C + 273.15 if liquidus_C is not None else None
                ),
                liquid_fraction=result.liquid_fraction,
                status=result.status,
                warnings=tuple(result.warnings),
                diagnostics=dict(result.diagnostics or {}),
            )

        ptt_liquidus_C: Optional[float] = None
        ptt_warnings: tuple[str, ...] = ()
        if self._mode == 'python_api':
            ptt_liquidus_C, ptt_warnings = self._find_petthermotools_liquidus_C(
                comp_wt,
                pressure_bar=pressure_bar,
                seed_T_C=max(min_T_C, ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C),
            )
            if ptt_liquidus_C is None:
                return LiquidusSolidusResult(
                    status='unavailable',
                    warnings=(
                        *ptt_warnings,
                        'AlphaMELTS liquidus finder unavailable: '
                        'PetThermoTools findLiq did not return a temperature',
                    ),
                )

        sample_warnings: list[str] = []
        def sample_fraction(temperature_C: float) -> float:
            result = self.equilibrate(
                float(temperature_C),
                composition_kg=composition_kg,
                fO2_log=fO2_log,
                pressure_bar=pressure_bar,
                composition_mol=composition_mol,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_formula_registry,
            )
            if result.status != 'ok':
                raise LiquidusSampleError(
                    result.status,
                    tuple(result.warnings),
                    dict(result.diagnostics or {}),
                )
            for warning in result.warnings:
                if warning not in sample_warnings:
                    sample_warnings.append(warning)
            return float(result.liquid_fraction)

        result = find_liquidus_solidus_by_fraction(
            sample_fraction,
            min_T_C=min_T_C,
            max_T_C=max_T_C,
            scan_step_C=scan_step_C,
            tolerance_C=tolerance_C,
        )
        warnings_out = [*ptt_warnings, *result.warnings, *sample_warnings[:6]]
        if (
            ptt_liquidus_C is not None
            and
            result.status == 'ok'
            and result.liquidus_T_C is not None
            and abs(result.liquidus_T_C - ptt_liquidus_C)
            > max(5.0, tolerance_C * 2.0)
        ):
            warnings_out.append(
                'PetThermoTools findLiq differs from frac_M bisection: '
                f'findLiq={ptt_liquidus_C:.3f} C, '
                f'bisection={result.liquidus_T_C:.3f} C'
            )
        return LiquidusSolidusResult(
            liquidus_T_C=result.liquidus_T_C,
            liquidus_T_K=result.liquidus_T_K,
            solidus_T_C=result.solidus_T_C,
            liquid_fraction=result.liquid_fraction,
            status=result.status,
            warnings=tuple(warnings_out),
            samples=result.samples,
            iterations=result.iterations,
            diagnostics=dict(result.diagnostics or {}),
        )

    def _unapplied_absolute_fo2_result(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float],
        transport: str,
        warnings: Optional[List[str]] = None,
    ) -> EquilibriumResult:
        message = (
            f'AlphaMELTS {transport} refused absolute fO2_log={fO2_log:g}: '
            'the transport exposes only initialization-time redox controls'
        )
        diagnostics = self._diagnostics_with_backend_status_reason(
            {
                'requested_fO2_log': float(fO2_log),
                'fO2_transport': str(transport),
                'authoritative_for_requested_conditions': False,
            },
            backend_status='out_of_domain',
            reason=ALPHAMELTS_REASON_FO2_CONSTRAINT_UNAPPLIED,
            message=message,
        )
        return self._emit_equilibrium_result(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=[*(warnings or []), message],
            status='out_of_domain',
            diagnostics=diagnostics,
        )

    def _unsupported_accounts(
        self,
        composition_mol_by_account: Mapping[str, Mapping[str, float]],
    ) -> Dict[str, List[str]]:
        unsupported = {
            str(account): sorted(
                str(species)
                for species, mol in (species_mol or {}).items()
                if float(mol) > 0.0
            )
            for account, species_mol in composition_mol_by_account.items()
            if str(account) != 'process.cleaned_melt'
        }
        return {account: species for account, species in unsupported.items()
                if species}

    def _resolve_ok_liquid_fraction(
        self,
        *,
        liquid_fraction: Optional[float],
        phase_masses_kg: Mapping[str, float],
    ) -> float:
        computed = liquid_fraction_from_phase_masses(phase_masses_kg)
        if computed is None:
            raise LiquidFractionInvalidError('liquid_fraction_missing')
        if liquid_fraction is not None:
            try:
                supplied = float(liquid_fraction)
            except (TypeError, ValueError) as exc:
                raise LiquidFractionInvalidError(
                    f'liquid_fraction_invalid: {liquid_fraction!r}'
                ) from exc
            if not math.isfinite(supplied):
                raise LiquidFractionInvalidError(
                    f'liquid_fraction_invalid: {liquid_fraction!r}'
                )
            if not math.isclose(
                supplied, computed, rel_tol=1e-6, abs_tol=1e-6
            ):
                raise LiquidFractionInvalidError(
                    'liquid_fraction_mismatch: '
                    f'supplied={supplied!r} phase_masses={computed!r}'
                )
        return computed

    def _emit_equilibrium_result(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: float,
        requested_temperature_C: Optional[float] = None,
        phases_present: Optional[List[str]] = None,
        phase_masses_kg: Optional[Mapping[str, float]] = None,
        phase_species_mol: Optional[Mapping[str, Mapping[str, float]]] = None,
        phase_species_kg: Optional[Mapping[str, Mapping[str, float]]] = None,
        phase_instances: Optional[List[Mapping[str, object]]] = None,
        liquid_fraction: Optional[float] = None,
        liquid_composition_wt_pct: Optional[Mapping[str, float]] = None,
        liquid_viscosity_Pa_s: Optional[float] = None,
        liquid_density_kg_m3: Optional[float] = None,
        system_enthalpy: Optional[float] = None,
        system_entropy: Optional[float] = None,
        system_volume: Optional[float] = None,
        system_heat_capacity_Cp: Optional[float] = None,
        system_dVdP: Optional[float] = None,
        system_dVdT: Optional[float] = None,
        system_fO2_delta_QFM: Optional[float] = None,
        system_solid_density_rhos: Optional[float] = None,
        system_phi: Optional[float] = None,
        system_chisqr: Optional[float] = None,
        phase_thermo: Optional[Mapping[str, Mapping[str, object]]] = None,
        phase_compositions: Optional[Mapping[str, Mapping[str, float]]] = None,
        chem_potentials: Optional[Mapping[str, Mapping[str, object]]] = None,
        phase_affinities: Optional[Mapping[str, Mapping[str, object]]] = None,
        solid_composition_wt_pct: Optional[Mapping[str, float]] = None,
        bulk_composition_wt_pct: Optional[Mapping[str, float]] = None,
        activity_coefficients: Optional[Mapping[str, float]] = None,
        vapor_pressures_Pa: Optional[Mapping[str, float]] = None,
        vapor_pressures_source: Optional[Mapping[str, str]] = None,
        warnings: Optional[List[str]] = None,
        status: str = 'ok',
        diagnostics: Optional[Mapping[str, object]] = None,
    ) -> EquilibriumResult:
        phase_masses = dict(phase_masses_kg or {})
        result_status = str(status)
        result_diagnostics = dict(diagnostics or {})
        reported_activities = dict(activity_coefficients or {})
        result_diagnostics.update(
            self._activity_diagnostic_payload(reported_activities)
        )
        resolved_liquid_fraction = liquid_fraction
        if result_status == 'ok':
            resolved_liquid_fraction = self._resolve_ok_liquid_fraction(
                liquid_fraction=resolved_liquid_fraction,
                phase_masses_kg=phase_masses,
            )
        if self._requested_operating_point_non_authoritative(result_diagnostics):
            result_status = 'out_of_domain'
            result_diagnostics['backend_status'] = 'out_of_domain'
            result_diagnostics.setdefault(
                'backend_status_reason',
                'clamped_operating_point',
            )
        result_diagnostics.setdefault('backend_name', self.backend_name)
        result_diagnostics.setdefault('engine_version', self.get_engine_version())
        result = EquilibriumResult(
            temperature_C=float(temperature_C),
            pressure_bar=float(pressure_bar),
            fO2_log=(None if fO2_log is None else float(fO2_log)),
            phases_present=list(phases_present or []),
            phase_masses_kg=phase_masses,
            phase_species_mol={
                str(phase): dict(species)
                for phase, species in dict(phase_species_mol or {}).items()
            },
            phase_species_kg={
                str(phase): dict(species)
                for phase, species in dict(phase_species_kg or {}).items()
            },
            phase_compositions={
                str(phase): dict(composition)
                for phase, composition in dict(phase_compositions or {}).items()
            },
            liquid_fraction=resolved_liquid_fraction,
            liquid_composition_wt_pct=dict(liquid_composition_wt_pct or {}),
            liquid_viscosity_Pa_s=(
                None
                if liquid_viscosity_Pa_s is None
                else float(liquid_viscosity_Pa_s)
            ),
            activity_coefficients=reported_activities,
            vapor_pressures_Pa=dict(vapor_pressures_Pa or {}),
            vapor_pressures_source=dict(vapor_pressures_source or {}),
            warnings=list(warnings or []),
            status=result_status,
            diagnostics=result_diagnostics,
            requested_temperature_C=(
                None
                if requested_temperature_C is None
                else float(requested_temperature_C)
            ),
            liquid_density_kg_m3=(
                None
                if liquid_density_kg_m3 is None
                else float(liquid_density_kg_m3)
            ),
            system_enthalpy=system_enthalpy,
            system_entropy=system_entropy,
            system_volume=system_volume,
            system_heat_capacity_Cp=system_heat_capacity_Cp,
            system_dVdP=system_dVdP,
            system_dVdT=system_dVdT,
            system_fO2_delta_QFM=system_fO2_delta_QFM,
            system_solid_density_rhos=system_solid_density_rhos,
            system_phi=system_phi,
            system_chisqr=system_chisqr,
            phase_thermo={
                str(phase): dict(values)
                for phase, values in dict(phase_thermo or {}).items()
            },
            chem_potentials=(
                None
                if chem_potentials is None
                else {
                    str(phase): dict(values)
                    for phase, values in chem_potentials.items()
                }
            ),
            phase_affinities=(
                None
                if phase_affinities is None
                else {
                    str(phase): dict(values)
                    for phase, values in phase_affinities.items()
                }
            ),
            solid_composition_wt_pct=dict(solid_composition_wt_pct or {}),
            bulk_composition_wt_pct=dict(bulk_composition_wt_pct or {}),
            phase_instances=[dict(instance) for instance in phase_instances or []],
        )
        result.backend_name = self.backend_name
        result.engine_version = self.get_engine_version()
        return result

    @staticmethod
    def _merge_diagnostics(
        *diagnostics: Optional[Mapping[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for item in diagnostics:
            payload.update(dict(item or {}))
        return payload

    @staticmethod
    def _requested_operating_point_non_authoritative(
        diagnostics: Mapping[str, object],
    ) -> bool:
        return (
            bool(diagnostics.get('operating_point_clamped'))
            or diagnostics.get('authoritative_for_requested_conditions') is False
        )

    def _fail_closed_on_clamped_operating_point(
        self,
        result: EquilibriumResult,
    ) -> EquilibriumResult:
        diagnostics = dict(result.diagnostics or {})
        if (
            result.status == 'ok'
            and self._requested_operating_point_non_authoritative(diagnostics)
        ):
            result.status = 'out_of_domain'
            diagnostics['backend_status'] = 'out_of_domain'
            diagnostics.setdefault(
                'backend_status_reason',
                'clamped_operating_point',
            )
            result.diagnostics = diagnostics
        return result

    @staticmethod
    def _clamped_operating_point_context(
        *,
        requested_temperature_C: float,
        requested_pressure_bar: float,
        solved_temperature_C: float,
        solved_pressure_bar: float,
        transport: str,
        warnings: Optional[List[str]] = None,
    ) -> tuple[dict[str, object], list[str]]:
        result_warnings = list(warnings or [])
        requested_T = float(requested_temperature_C)
        requested_P = float(requested_pressure_bar)
        solved_T = float(solved_temperature_C)
        solved_P = float(solved_pressure_bar)
        temperature_clamped = not math.isclose(
            requested_T, solved_T, rel_tol=0.0, abs_tol=1.0e-12)
        pressure_clamped = not math.isclose(
            requested_P, solved_P, rel_tol=0.0, abs_tol=1.0e-12)
        if not (temperature_clamped or pressure_clamped):
            return {}, result_warnings

        diagnostics = {
            'operating_point_clamped': True,
            'operating_point_transport': str(transport),
            'temperature_clamped': temperature_clamped,
            'pressure_clamped': pressure_clamped,
            'requested_temperature_C': requested_T,
            'requested_pressure_bar': requested_P,
            'solved_temperature_C': solved_T,
            'solved_pressure_bar': solved_P,
            'authoritative_for_requested_conditions': False,
            'authoritative_for_solved_conditions': True,
        }
        pieces = []
        if temperature_clamped:
            pieces.append(
                f'T_C requested={requested_T:g} solved={solved_T:g}')
        if pressure_clamped:
            pieces.append(
                f'P_bar requested={requested_P:g} solved={solved_P:g}')
        result_warnings.append(
            'AlphaMELTS solved at clamped operating point via '
            f'{transport}: ' + ', '.join(pieces)
        )
        return diagnostics, result_warnings

    def _out_of_domain_diagnostics(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float],
        composition_wt_pct: Optional[Mapping[str, float]] = None,
        composition_melts_wt_pct: Optional[Mapping[str, float]] = None,
        composition_mol: Optional[Mapping[str, float]] = None,
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        crash_point: dict[str, object] = {
            'temperature_C': float(temperature_C),
            'pressure_bar': float(pressure_bar),
            'composition_wt_pct': self._finite_positive_mapping(
                composition_wt_pct or {}
            ),
            'composition_mol': self._finite_positive_mapping(
                composition_mol or {}
            ),
        }
        if fO2_log is not None:
            crash_point['fO2_log'] = float(fO2_log)
        melts_wt = self._finite_positive_mapping(composition_melts_wt_pct or {})
        if melts_wt:
            crash_point['composition_melts_wt_pct'] = melts_wt
        by_account = self._finite_positive_nested_mapping(
            composition_mol_by_account or {}
        )
        if by_account:
            crash_point['composition_mol_by_account'] = by_account
        payload: dict[str, object] = {
            'backend_status': 'out_of_domain',
            'out_of_domain_crash_point': crash_point,
        }
        if reason is not None:
            reason_code = str(reason)
            payload['backend_status_reason'] = reason_code
            _annotate_alphamelts_backend_failure(
                payload,
                reason_code=reason_code,
                backend_status='out_of_domain',
            )
        return payload

    @staticmethod
    def _diagnostics_with_backend_status_reason(
        diagnostics: Optional[Mapping[str, object]],
        *,
        backend_status: str,
        reason: OutOfDomainReason | str,
        message: str | None = None,
    ) -> dict[str, object]:
        payload = dict(diagnostics or {})
        payload.setdefault('backend_status', backend_status)
        structured_reason = reason_value(reason)
        if structured_reason is not None:
            payload['backend_status_reason'] = structured_reason
            reason_message = (
                message
                or ALPHAMELTS_BACKEND_FAILURE_MESSAGES.get(structured_reason)
            )
            _annotate_alphamelts_backend_failure(
                payload,
                reason_code=structured_reason,
                backend_status=backend_status,
                message=reason_message,
            )
        return payload

    @staticmethod
    def _finite_positive_mapping(values: Mapping[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for species, raw in values.items():
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0.0:
                result[str(species)] = value
        return result

    @classmethod
    def _finite_positive_nested_mapping(
        cls,
        values: Mapping[str, Mapping[str, float]],
    ) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for account, species_mol in values.items():
            cleaned = cls._finite_positive_mapping(species_mol or {})
            if cleaned:
                result[str(account)] = cleaned
        return result

    def _composition_kg_to_wt_pct(self, composition_kg: Mapping[str, float]) -> dict:
        total = sum(float(value) for value in composition_kg.values()
                    if float(value) > 0.0)
        if total <= 0.0:
            return {}
        return {
            species: float(value) / total * 100.0
            for species, value in composition_kg.items()
            if float(value) > 0.0
        }

    def _domain_gate(self, comp_wt: Mapping[str, float], *,
                     temperature_C: float,
                     pressure_bar: float,
                     fO2_log: Optional[float],
                     diagnostics: Optional[Mapping[str, object]] = None,
                     ) -> Optional[EquilibriumResult]:
        canonical_wt: Dict[str, float] = {}
        non_oxides: List[str] = []
        unrecognised: List[str] = []
        for raw_name, raw_wt in comp_wt.items():
            wt = float(raw_wt)
            if wt <= 0.0:
                continue
            oxide = self._canonical_oxide_name(raw_name)
            if oxide is None:
                if self._is_non_oxide_species_name(raw_name):
                    non_oxides.append(str(raw_name))
                else:
                    unrecognised.append(str(raw_name))
                continue
            canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt

        sio2_pct = canonical_wt.get('SiO2', 0.0)
        major_pct = sum(canonical_wt.values())
        reasons: List[str] = []
        reason: OutOfDomainReason | None = None
        if not 30.0 <= sio2_pct <= 80.0:
            reason = OutOfDomainReason.SILICATE_WINDOW
            reasons.append(f'SiO2 {sio2_pct:.3f} wt% outside [30, 80]')
        if major_pct <= MELTS_MAJOR_OXIDE_MIN_TOTAL_WT_PCT:
            reason = reason or OutOfDomainReason.MAJOR_SUM
            reasons.append(
                f'major oxide sum {major_pct:.3f} wt% <= '
                f'{MELTS_MAJOR_OXIDE_MIN_TOTAL_WT_PCT:g}'
            )
        if non_oxides:
            reason = OutOfDomainReason.FORBIDDEN_SPECIES
            reasons.append(
                'non-oxide species present: ' + ', '.join(sorted(non_oxides)))
        if unrecognised:
            reason = OutOfDomainReason.FORBIDDEN_SPECIES
            reasons.append(
                'unrecognised species outside MELTS basis: '
                + ', '.join(sorted(unrecognised))
            )
        if not reasons:
            return None
        return self._domain_gate_result(
            temperature_C,
            pressure_bar,
            fO2_log,
            reasons,
            diagnostics=diagnostics,
            reason=reason,
        )

    def _domain_gate_result(self, temperature_C: float, pressure_bar: float,
                            fO2_log: Optional[float],
                            reasons: List[str],
                            diagnostics: Optional[Mapping[str, object]] = None,
                            reason: OutOfDomainReason | str | None = None,
                            ) -> EquilibriumResult:
        diagnostics_out = dict(diagnostics or {})
        structured_reason = reason_value(reason)
        if structured_reason is not None:
            diagnostics_out['backend_status_reason'] = structured_reason
            _annotate_alphamelts_backend_failure(
                diagnostics_out,
                reason_code=structured_reason,
                backend_status='out_of_domain',
            )
        return self._emit_equilibrium_result(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=['DomainGate rejected: ' + '; '.join(reasons)],
            status='out_of_domain',
            diagnostics=diagnostics_out,
        )

    def _is_non_oxide_species_name(self, name: str) -> bool:
        text = str(name).strip()
        elements = re.findall(r'[A-Z][a-z]?', text)
        if not elements:
            return True
        if 'O' not in elements:
            return True
        return any(element in {'Cl', 'F', 'Br', 'I', 'S'} for element in elements)

    def _canonical_oxide_name(self, name: str) -> Optional[str]:
        key = str(name).strip()
        if key.endswith('_Liq'):
            key = key[:-4]
        return MELTS_OXIDE_ALIASES.get(key.lower())

    def _canonical_activity_mapping(self, values: Mapping[str, object]) -> dict:
        activities: Dict[str, float] = {}
        for raw_name, raw_value in dict(values or {}).items():
            oxide = canonical_melt_oxide_activity_name(raw_name)
            if oxide is None:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value > 0.0 and math.isfinite(value):
                activities.setdefault(oxide, value)
        return activities

    def _activity_diagnostic_payload(
        self,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        reported: Dict[str, float] = {}
        label_map: Dict[str, dict[str, object]] = {}
        for raw_name, raw_value in dict(values or {}).items():
            label = str(raw_name)
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if not (value > 0.0 and math.isfinite(value)):
                continue
            oxide = canonical_melt_oxide_activity_name(label)
            reported[label] = value
            label_map[label] = {
                'oxide_activity': oxide,
                'basis': (
                    'oxide_activity'
                    if oxide is not None
                    else 'reported_endmember_or_component_not_oxide_activity'
                ),
            }
        if not reported:
            return {}
        return {
            'diagnostic_activity_basis': (
                'reported_label; endmember/component labels are NOT '
                'oxide activities without an explicit basis conversion'
            ),
            'diagnostic_reported_activities': reported,
            'diagnostic_oxide_activities': self._canonical_activity_mapping(
                reported
            ),
            'diagnostic_activity_label_map': label_map,
        }

    def _normalize_composition_to_melts_basis(self, comp_wt: dict) -> dict:
        self._last_normalization_warnings = []
        normalized_basis = {oxide: 0.0 for oxide in MELTS_OXIDE_BASIS}
        feo_total = 0.0

        for raw_name, raw_wt in comp_wt.items():
            wt = float(raw_wt)
            if wt <= 0.0:
                continue
            oxide = self._canonical_oxide_name(raw_name)
            if oxide is None:
                self._last_normalization_warnings.append(
                    f'Dropped non-MELTS component {raw_name}')
                continue
            if oxide == 'FeO_total':
                feo_total += wt
            else:
                normalized_basis[oxide] += wt

        if feo_total > 0.0:
            if self._fe3fet_ratio is not None:
                normalized_basis['FeO'] += feo_total * (1.0 - self._fe3fet_ratio)
                normalized_basis['Fe2O3'] += (
                    feo_total * self._fe3fet_ratio / FE3_TO_FEOT_FACTOR
                )
            elif self._redox_buffer is not None:
                normalized_basis['FeO'] += feo_total
                self._last_normalization_warnings.append(
                    f'FeO_total treated as FeOt with {self._redox_buffer} '
                    'redox buffer; no FeO/Fe2O3 auto-split applied'
                )
            else:
                raise ValueError('FeO_total requires explicit redox policy')

        total = sum(normalized_basis.values())
        if total <= 0.0:
            raise ValueError('AlphaMELTS composition has no MELTS-basis oxides')
        return {
            oxide: normalized_basis[oxide] / total * 100.0
            for oxide in MELTS_OXIDE_BASIS
        }

    # ------------------------------------------------------------------
    # Python API mode (PetThermoTools)
    # ------------------------------------------------------------------

    def _equilibrate_python(
        self,
        temperature_C,
        comp_wt,
        fO2_log,
        pressure_bar,
        warnings=None,
        *,
        total_input_kg: float = 0.1,
        require_solved_fo2: bool = False,
    ):
        """
        Use PetThermoTools for equilibrium calculation.

        PetThermoTools wraps alphaMELTS for Python, providing
        phase assemblage, liquid composition, and activity data.
        """
        try:
            ptt = self._require_petthermotools_runtime()
            ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
            solved_pressure_bar = max(
                float(pressure_bar), ALPHAMELTS_PYTHON_MIN_PRESSURE_BAR)
            clamp_diagnostics, result_warnings = (
                self._clamped_operating_point_context(
                    requested_temperature_C=temperature_C,
                    requested_pressure_bar=pressure_bar,
                    solved_temperature_C=temperature_C,
                    solved_pressure_bar=solved_pressure_bar,
                    transport='python_api',
                    warnings=warnings,
                )
            )
            results = ptt.equilibrate_MELTS(
                Model=self._model,
                P_bar=solved_pressure_bar,
                T_C=temperature_C,
                comp=ptt_comp,
                fO2_buffer=self._redox_buffer,
                fO2_offset=self._fo2_offset,
                melts=self._pet_melts,
            )
            eq = self._parse_petthermotools_result(
                results,
                temperature_C=temperature_C,
                pressure_bar=solved_pressure_bar,
                fO2_log=fO2_log,
                comp_wt=comp_wt,
                total_input_kg=total_input_kg,
                require_solved_fo2=require_solved_fo2,
                warnings=result_warnings,
                diagnostics=clamp_diagnostics,
            )
            if eq.status != 'ok':
                return eq

            # Vapor pressures via the real VapoRock helper if available,
            # fed the SOLVED equilibrium liquid composition (not the
            # pre-equilibrium input); explicit Antoine fallback otherwise.
            if self._vaporock_available:
                eq.vapor_pressures_Pa, source = (
                    self._vapor_pressures_via_vaporock_or_antoine(
                        T_C=temperature_C,
                        solved_melt_wt_pct=eq.liquid_composition_wt_pct,
                        liquid_fraction=eq.liquid_fraction,
                        fO2_log=fO2_log,
                        pressure_bar=solved_pressure_bar,
                        activities=eq.activity_coefficients,
                    )
                )
            else:
                # Use activity x Antoine fallback rows only when the
                # chemical-potential convention supplied real activities.
                # Only pure_component_psat rows are pure-component /
                # first-principles; pseudo rows are backsolved VapoRock
                # curve-fit fallbacks.
                eq.vapor_pressures_Pa = self._activities_times_antoine_or_fail(
                    temperature_C,
                    eq.activity_coefficients,
                    comp_wt,
                    context='PetThermoTools VapoRock fallback unavailable',
                )
                source = (
                    self._antoine_vapor_pressure_source_by_species(
                        'alphamelts_python_api',
                        eq.vapor_pressures_Pa,
                    )
                    if eq.vapor_pressures_Pa
                    else 'no_volatile_species'
                )
            eq.vapor_pressures_source = self._vapor_pressure_source_map(
                eq.vapor_pressures_Pa,
                source,
            )
            eq.diagnostics = self._vapor_pressure_diagnostics(
                eq.diagnostics,
                eq.vapor_pressures_Pa,
                source,
            )

            return self._fail_closed_on_clamped_operating_point(eq)

        except ImportError:
            self._mode = None
            raise
        except Exception as e:
            self._mode = None
            raise RuntimeError(f'AlphaMELTS Python equilibrium failed: {e}') from e

    def _require_petthermotools_runtime(self):
        if not self._pet_payload_preloaded or self._pet_melts is None:
            raise ImportError(
                'PetThermoTools runtime not preloaded; call initialize() '
                'so meltsdynamic loads outside the request hot path'
            )
        if self._pet_module is None:
            raise ImportError('PetThermoTools module not initialized')
        return self._pet_module

    def _to_petthermotools_liq_comp(self, comp_wt: Mapping[str, float]) -> dict:
        feot = float(comp_wt.get('FeO', 0.0)) + (
            FE3_TO_FEOT_FACTOR * float(comp_wt.get('Fe2O3', 0.0))
        )
        if feot > 0.0:
            fe3fet = (
                FE3_TO_FEOT_FACTOR * float(comp_wt.get('Fe2O3', 0.0)) / feot
            )
        else:
            fe3fet = self._fe3fet_ratio or 0.0
        return {
            'SiO2_Liq': float(comp_wt.get('SiO2', 0.0)),
            'TiO2_Liq': float(comp_wt.get('TiO2', 0.0)),
            'Al2O3_Liq': float(comp_wt.get('Al2O3', 0.0)),
            'Cr2O3_Liq': float(comp_wt.get('Cr2O3', 0.0)),
            'FeOt_Liq': feot,
            'MnO_Liq': float(comp_wt.get('MnO', 0.0)),
            'MgO_Liq': float(comp_wt.get('MgO', 0.0)),
            'CaO_Liq': float(comp_wt.get('CaO', 0.0)),
            'Na2O_Liq': float(comp_wt.get('Na2O', 0.0)),
            'K2O_Liq': float(comp_wt.get('K2O', 0.0)),
            'P2O5_Liq': float(comp_wt.get('P2O5', 0.0)),
            'H2O_Liq': 0.0,
            'CO2_Liq': 0.0,
            'Fe3Fet_Liq': max(0.0, min(1.0, fe3fet)),
        }

    def _composition_for_liquidus_finder(
        self,
        *,
        composition_kg: Optional[Dict[str, float]],
        composition_mol: Optional[Dict[str, float]],
        composition_mol_by_account: Optional[Mapping[str, Mapping[str, float]]],
        species_formula_registry: Optional[Mapping[str, object]],
        pressure_bar: float,
        fO2_log: float,
        min_T_C: float,
    ) -> dict | LiquidusSolidusResult:
        if composition_mol_by_account is not None:
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                diagnostics = self._out_of_domain_diagnostics(
                    temperature_C=min_T_C,
                    pressure_bar=pressure_bar,
                    fO2_log=fO2_log,
                    composition_wt_pct=None,
                    composition_mol=composition_mol,
                    composition_mol_by_account=composition_mol_by_account,
                )
                return LiquidusSolidusResult(
                    status='out_of_domain',
                    warnings=(
                        'unsupported ledger accounts present: '
                        + ', '.join(
                            f'{account}={species}'
                            for account, species in sorted(unsupported.items())
                        ),
                    ),
                    diagnostics=diagnostics,
                )
            composition_mol = {}
            for species, mol in composition_mol_by_account.get(
                'process.cleaned_melt', {}
            ).items():
                composition_mol[species] = (
                    composition_mol.get(species, 0.0) + float(mol))
        if composition_mol is not None:
            composition_kg = {
                species: float(mol)
                * resolve_species_formula(
                    species,
                    species_formula_registry,
                ).molar_mass_kg_per_mol()
                for species, mol in composition_mol.items()
                if float(mol) > 0.0
            }
        else:
            composition_kg = dict(composition_kg or {})

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        diagnostics = self._out_of_domain_diagnostics(
            temperature_C=min_T_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            composition_wt_pct=raw_comp_wt,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
        )
        if not raw_comp_wt:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=('AlphaMELTS liquidus finder received no melt oxides',),
                diagnostics=diagnostics,
            )
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=min_T_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            diagnostics=diagnostics,
        )
        if domain_rejection is not None:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=tuple(domain_rejection.warnings),
                diagnostics=dict(domain_rejection.diagnostics),
            )
        try:
            return self._normalize_composition_to_melts_basis(raw_comp_wt)
        except ValueError as exc:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=(f'AlphaMELTS composition rejected: {exc}',),
                diagnostics=diagnostics,
            )

    def _find_petthermotools_liquidus_C(
        self,
        comp_wt: Mapping[str, float],
        *,
        pressure_bar: float,
        seed_T_C: float,
    ) -> tuple[Optional[float], tuple[str, ...]]:
        try:
            ptt = self._require_petthermotools_runtime()
        except ImportError as exc:
            return None, (f'PetThermoTools runtime unavailable: {exc}',)
        ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
        find_liq_melts = getattr(ptt, 'findLiq_MELTS', None)
        find_liq = getattr(ptt, 'findLiq', None)
        try:
            if callable(find_liq_melts):
                raw = find_liq_melts(
                    P_bar=max(pressure_bar, 1e-6),
                    Model=self._model,
                    T_C_init=float(seed_T_C),
                    comp=ptt_comp,
                    melts=self._pet_melts,
                    fO2_buffer=self._redox_buffer,
                    fO2_offset=self._fo2_offset,
                    Step=50.0,
                )
            elif callable(find_liq):
                raw = find_liq(
                    None,
                    0,
                    Model=self._model,
                    P_bar=max(pressure_bar, 1e-6),
                    T_initial_C=float(seed_T_C),
                    comp=ptt_comp,
                    fO2_buffer=self._redox_buffer,
                    fO2_offset=self._fo2_offset,
                )
            else:
                return None, ('PetThermoTools findLiq API not found',)
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            return None, (f'PetThermoTools findLiq failed: {exc}',)
        return self._extract_temperature_C(raw), ()

    def _extract_temperature_C(self, raw: Any) -> Optional[float]:
        if raw is None:
            return None
        if self._is_number(raw):
            return float(raw)
        if isinstance(raw, Mapping):
            for key in (
                'T_Liq',
                'T_liq',
                'T_liquidus_C',
                'liquidus_T_C',
                'Liquidus',
                'liquidus',
                'T_C',
                'T',
            ):
                if key in raw and self._is_number(raw[key]):
                    return float(raw[key])
            for value in raw.values():
                found = self._extract_temperature_C(value)
                if found is not None:
                    return found
        if isinstance(raw, (tuple, list)):
            for value in raw:
                found = self._extract_temperature_C(value)
                if found is not None:
                    return found
        for key in (
            'T_Liq',
            'T_liq',
            'T_liquidus_C',
            'liquidus_T_C',
            'Liquidus',
            'liquidus',
            'T_C',
            'T',
        ):
            value = getattr(raw, key, None)
            if value is not None and self._is_number(value):
                return float(value)
        return None

    # ------------------------------------------------------------------
    # Subprocess mode
    # ------------------------------------------------------------------

    def _equilibrate_subprocess(
        self,
        temperature_C,
        comp_wt,
        fO2_log,
        pressure_bar,
        warnings=None,
        *,
        total_input_kg: float,
        diagnostics: Optional[Mapping[str, object]] = None,
        run_mode: AlphaMELTSSubprocessRunMode,
    ):
        """
        Run alphaMELTS binary via subprocess.

        Writes a .melts input file, runs the binary, and parses
        the *_tbl.txt output files for phase data.

        Slower (~1-3s per call) but reliable.
        """
        requested_temperature_C = float(temperature_C)
        requested_pressure_bar = float(pressure_bar)
        if requested_pressure_bar < ALPHAMELTS_SUBPROCESS_MIN_PRESSURE_BAR:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED,
                f'requested={requested_pressure_bar:g} bar; '
                f'minimum={ALPHAMELTS_SUBPROCESS_MIN_PRESSURE_BAR:g} bar',
            )
        fO2_path, fO2_offset = self._subprocess_fo2_constraint(fO2_log)
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write .melts file
            melts_path = Path(tmpdir) / 'input.melts'
            calculation_temperature_C = requested_temperature_C
            if run_mode is AlphaMELTSSubprocessRunMode.LIQUIDUS_FINDER:
                calculation_temperature_C = max(
                    calculation_temperature_C,
                    ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C,
                )
            calculation_pressure_bar = requested_pressure_bar
            result_warnings = list(warnings or [])
            self._write_melts_file(
                melts_path,
                comp_wt,
                calculation_temperature_C,
                calculation_pressure_bar,
                fO2_path=fO2_path,
                fO2_offset=fO2_offset,
            )

            binary = self._binary_path or self._engine_path
            if binary is None:
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_MISSING_BINARY,
                    'subprocess binary is not configured',
                )
            starting_guess = (
                '1'
                if run_mode is AlphaMELTSSubprocessRunMode.ISOTHERMAL
                else '2'
            )
            # Menu option 4 with a one-step bound is required by alphaMELTS 2
            # to emit System_main_tbl.txt.  That file is the engine-owned
            # source for fO2, liquid density, and liquid viscosity.
            menu_input = f'1\ninput.melts\n4\n{starting_guess}\n1\nx\n'
            env = os.environ.copy()
            env.setdefault('ALPHAMELTS_CALC_MODE', 'MELTS')
            env['ALPHAMELTS_RUN_MODE'] = 'isobaric'
            env['ALPHAMELTS_DELTAT'] = '0'
            env['ALPHAMELTS_MINT'] = f'{calculation_temperature_C:.12g}'
            env['ALPHAMELTS_MAXT'] = f'{calculation_temperature_C:.12g}'
            env['ALPHAMELTS_CELSIUS_OUTPUT'] = 'true'

            # Run alphaMELTS directly. The alphaMELTS 2 app runner only
            # emits *_tbl.txt for path-style runs; single-point equilibria
            # report the stable phase assemblage on stdout.
            timeout_s = getattr(self, '_timeout_s', 20.0)
            if timeout_s is None:
                timeout_s = 20.0
            try:
                result = subprocess.run(
                    [str(binary), '1'],
                    cwd=tmpdir,
                    input=menu_input,
                    capture_output=True, text=True,
                    timeout=timeout_s,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_TIMEOUT,
                    str(exc),
                ) from exc
            except FileNotFoundError as exc:
                self._mode = None
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_MISSING_BINARY,
                    f'binary not found: {exc}',
                ) from exc

            if result.returncode < 0:
                signal_name = _signal_name(result.returncode)
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_SUBPROCESS_DIED,
                    f'{signal_name} (returncode {result.returncode})',
                )
            if result.returncode > 0:
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_NONZERO_EXIT,
                    f'returncode {result.returncode}: '
                    f'{result.stderr or result.stdout}',
                )

            table_outputs = {}
            for table_name in (
                'System_main_tbl.txt',
                'Phase_main_tbl.txt',
                'Solid_comp_tbl.txt',
                'Bulk_comp_tbl.txt',
                'Liquid_comp_tbl.txt',
            ):
                table_path = Path(tmpdir) / table_name
                table_outputs[table_name] = (
                    table_path.read_text(errors='replace')
                    if table_path.is_file()
                    else ''
                )
            eq = self._parse_single_point_stdout(
                f'{result.stdout}\n{result.stderr}',
                requested_temperature_C=requested_temperature_C,
                pressure_bar=calculation_pressure_bar,
                fO2_log=fO2_log,
                total_input_kg=total_input_kg,
                warnings=result_warnings,
                diagnostics=diagnostics,
                success_diagnostics=diagnostics,
                run_mode=run_mode,
                system_output=table_outputs['System_main_tbl.txt'],
                fO2_constraint={
                    'path': fO2_path,
                    'offset': fO2_offset,
                },
                table_outputs=table_outputs,
            )
            if eq.status != 'ok':
                return eq
            (
                eq.vapor_pressures_Pa,
                eq.vapor_pressures_source,
                vapor_diagnostics,
            ) = self._builtin_vapor_projection_for_subprocess(eq)
            eq.diagnostics = self._merge_diagnostics(
                eq.diagnostics,
                {'subprocess_vapor_projection': vapor_diagnostics},
            )
            return eq

    def _subprocess_fo2_constraint(self, fO2_log: float) -> tuple[str, float]:
        if self._redox_buffer is not None:
            path = 'FMQ' if self._redox_buffer == 'QFM' else self._redox_buffer
            return path, float(self._fo2_offset or 0.0)
        if self._fo2_offset is not None:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_FO2_CONSTRAINT_INVALID,
                'fO2_offset requires fO2_buffer',
            )
        value = float(fO2_log)
        if not math.isfinite(value):
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_FO2_CONSTRAINT_INVALID,
                f'absolute fO2_log={fO2_log!r}',
            )
        return 'Absolute', value

    def _write_melts_file(self, path: Path, comp_wt: dict,
                           T_C: float, P_bar: float, *,
                           fO2_path: str, fO2_offset: float):
        """Write a .melts input file for alphaMELTS."""
        lines = ['Title: regolith_pyrolysis_simulator']
        for oxide, wt in sorted(comp_wt.items()):
            if wt > ALPHAMELTS_MIN_EMITTED_COMPONENT_WT_PCT:
                # Map our oxide names to MELTS format
                melts_name = oxide.replace('2O3', '2O3').replace('2O', '2O')
                lines.append(f'Initial Composition: {melts_name} {wt:.4f}')
        lines.append(f'Initial Temperature: {T_C:.1f}')
        lines.append(f'Initial Pressure: {P_bar:.6g}')
        lines.append(f'Log fO2 Path: {fO2_path}')
        lines.append(f'Log fO2 Offset: {fO2_offset:.12g}')

        with open(path, 'w') as f:
            f.write('\n'.join(lines) + '\n')

    def _parse_liquidus_C(self, output: str) -> Optional[float]:
        match = re.search(
            r'Found the liquidus at T\s*=\s*([0-9.+\-Ee]+)\s*\(C\)',
            output,
        )
        if match is None:
            return None
        return float(match.group(1))

    def _parse_executed_temperatures_C(self, output: str) -> list[float]:
        matches = re.finditer(
            r'Initial alphaMELTS calculation at:.*?\bT\s+'
            r'(?P<started>[0-9.+\-Ee]+)\s*\(C\)'
            r'|Initial calculation failed\s*\(\s*'
            r'(?P<failed_pressure>[0-9.+\-Ee]+)\s+bars\s*,\s*'
            r'(?P<failed>[0-9.+\-Ee]+)\s+C\s*\)!',
            output,
        )
        temperatures = []
        for match in matches:
            started = match.group('started')
            if started is not None:
                temperatures.append(float(started))
                continue
            pressure_bar = float(match.group('failed_pressure'))
            failed_temperature_C = float(match.group('failed'))
            # alphaMELTS 2.3.1 emits its internal reset state as 0 bar, 0 K.
            # -273.15 C is therefore not an executed thermodynamic state.
            if pressure_bar == 0.0 and failed_temperature_C == -273.15:
                continue
            temperatures.append(failed_temperature_C)
        return temperatures

    def _parse_system_main_output(self, output: str) -> dict[str, object]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            headers = line.split()
            if (
                'Temperature' not in headers
                or not any(name.startswith('fO2(') for name in headers)
            ):
                continue
            if index + 1 >= len(lines):
                raise ValueError('System_main_tbl.txt lacks data row')
            values = lines[index + 1].split()
            if len(values) != len(headers):
                raise ValueError(
                    'System_main_tbl.txt row width '
                    f'{len(values)} != header width {len(headers)}'
                )

            def number(name: str) -> float:
                return float(values[headers.index(name)])

            def optional_number(name: str) -> Optional[float]:
                if name not in headers:
                    return None
                raw_value = values[headers.index(name)]
                if raw_value.lower() == 'n/a':
                    return None
                try:
                    value = float(raw_value)
                except ValueError as exc:
                    raise ValueError(
                        f'System_main_tbl.txt invalid {name}={raw_value!r}'
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError(
                        f'System_main_tbl.txt non-finite {name}={raw_value!r}'
                    )
                return value

            fO2_index = next(
                (
                    i for i, name in enumerate(headers)
                    if name.lower().startswith('fo2(absolute)')
                ),
                None,
            )
            payload: dict[str, object] = {}
            density_g_cm3 = optional_number('rhol')
            if density_g_cm3 is not None and density_g_cm3 > 0.0:
                payload['liquid_density_kg_m3'] = density_g_cm3 * 1000.0
            log10_viscosity_poise = optional_number('viscosity')
            if log10_viscosity_poise is not None:
                payload['liquid_viscosity_Pa_s'] = (
                    0.1 * (10.0 ** log10_viscosity_poise)
                )
            if fO2_index is not None:
                payload['fO2_header'] = headers[fO2_index]
                fO2_value = optional_number(headers[fO2_index])
                if fO2_value is not None:
                    payload['fO2_value'] = fO2_value
            if 'Temperature' in headers:
                payload['temperature_C'] = number('Temperature')
            system_mass_g = optional_number('mass')
            if system_mass_g is not None and system_mass_g > 0.0:
                payload['system_mass_g'] = system_mass_g
            for header, field in (
                ('H', 'system_enthalpy'),
                ('S', 'system_entropy'),
                ('V', 'system_volume'),
                ('Cp', 'system_heat_capacity_Cp'),
                ('dVdP*10^6', 'system_dVdP'),
                ('dVdT*10^6', 'system_dVdT'),
                ('phi', 'system_phi'),
                ('chisqr', 'system_chisqr'),
            ):
                if header in headers:
                    payload[field] = optional_number(header)
            delta_qfm_header = next(
                (
                    name for name in headers
                    if name.lower() in {'fo2-(qfm)', 'fo2-(fmq)'}
                ),
                None,
            )
            if delta_qfm_header is not None:
                payload['system_fO2_delta_QFM'] = optional_number(
                    delta_qfm_header
                )
            solid_density_g_cm3 = optional_number('rhos')
            payload['system_solid_density_rhos'] = (
                solid_density_g_cm3 * 1000.0
                if solid_density_g_cm3 is not None
                and solid_density_g_cm3 > 0.0
                else None
            )
            return payload
        return {}

    def _parse_phase_main_output(self, output: str) -> dict[str, object]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        header_index = next(
            (
                index for index, line in enumerate(lines)
                if line.startswith('index ')
                and ' Pressure ' in f' {line} '
                and ' Temperature ' in f' {line} '
            ),
            None,
        )
        if header_index is None:
            if lines:
                raise ValueError('Phase_main_tbl.txt lacks parseable header')
            return {}
        headers = lines[header_index].split()
        try:
            oxide_start = headers.index('Temperature') + 2
        except ValueError as exc:
            raise ValueError('Phase_main_tbl.txt malformed header') from exc
        oxides = headers[oxide_start:]
        if not oxides:
            raise ValueError('Phase_main_tbl.txt lacks oxide columns')

        accumulated: dict[str, dict[str, object]] = {}
        phase_instances: list[dict[str, object]] = []
        for line in lines[header_index + 1:]:
            tokens = line.split()
            if len(tokens) < 7 + len(oxides):
                raise ValueError(
                    f'Phase_main_tbl.txt malformed row: {line!r}'
                )
            instance_id = tokens[0]
            phase = re.sub(r'\d+$', '', instance_id)
            try:
                mass_g, enthalpy, entropy, volume, heat_capacity = (
                    float(value) for value in tokens[1:6]
                )
                composition_values = [
                    float(value) for value in tokens[-len(oxides):]
                ]
            except ValueError as exc:
                raise ValueError(
                    f'Phase_main_tbl.txt has non-numeric table value: {line!r}'
                ) from exc
            property_tokens = tokens[6:-len(oxides)]
            if not property_tokens:
                raise ValueError(
                    f'Phase_main_tbl.txt lacks density/formula field: {line!r}'
                )
            # AlphaMELTS prints liquid viscosity or a solid formula in this
            # slot, despite older table descriptions calling it density.
            # The table's mass (g) / volume (cm3) is the phase density.
            density_kg_m3 = (
                mass_g / volume * 1000.0
                if mass_g > 0.0 and volume > 0.0 else None
            )
            phase_instances.append({
                'instance_id': instance_id,
                'phase': phase,
                'solver_basis_mass_kg': mass_g / 1000.0,
                'formula_or_endmember_token': ' '.join(property_tokens),
                'enthalpy_J': enthalpy,
                'entropy_J_K': entropy,
                'volume_m3': volume * 1.0e-6,
                'heat_capacity_J_K': heat_capacity,
                'density_kg_m3': density_kg_m3,
                'reference_mass_kg': mass_g / 1000.0,
                'reference_basis': 'alphamelts_solver_phase_amount',
                'composition_wt_pct': dict(zip(oxides, composition_values)),
            })

            entry = accumulated.setdefault(
                phase,
                {
                    'mass_g': 0.0,
                    'enthalpy': 0.0,
                    'entropy': 0.0,
                    'volume': 0.0,
                    'heat_capacity_Cp': 0.0,
                    'density_mass_sum': 0.0,
                    'density_mass_g': 0.0,
                    'composition_mass_sums': {oxide: 0.0 for oxide in oxides},
                },
            )
            entry['mass_g'] = float(entry['mass_g']) + mass_g
            for key, value in (
                ('enthalpy', enthalpy),
                ('entropy', entropy),
                ('volume', volume),
                ('heat_capacity_Cp', heat_capacity),
            ):
                entry[key] = float(entry[key]) + value
            if density_kg_m3 is not None and mass_g > 0.0:
                entry['density_mass_sum'] = (
                    float(entry['density_mass_sum']) + density_kg_m3 * mass_g
                )
                entry['density_mass_g'] = float(entry['density_mass_g']) + mass_g
            composition_sums = entry['composition_mass_sums']
            assert isinstance(composition_sums, dict)
            for oxide, value in zip(oxides, composition_values):
                composition_sums[oxide] += value * mass_g

        phase_thermo = {}
        phase_compositions = {}
        for phase, entry in accumulated.items():
            mass_g = float(entry['mass_g'])
            if mass_g <= 0.0:
                continue
            density_mass_g = float(entry['density_mass_g'])
            phase_thermo[phase] = {
                'enthalpy': float(entry['enthalpy']),
                'entropy': float(entry['entropy']),
                'volume': float(entry['volume']),
                'heat_capacity_Cp': float(entry['heat_capacity_Cp']),
                'reference_mass_kg': mass_g / 1000.0,
                'density_kg_m3': (
                    float(entry['density_mass_sum']) / density_mass_g
                    if density_mass_g > 0.0 else None
                ),
            }
            composition_sums = entry['composition_mass_sums']
            assert isinstance(composition_sums, dict)
            phase_compositions[phase] = {
                oxide: float(value) / mass_g
                for oxide, value in composition_sums.items()
            }
        return {
            'phase_thermo': phase_thermo,
            'phase_compositions': phase_compositions,
            'phase_instances': phase_instances,
        }

    @staticmethod
    def _phase_species_from_instances(
        phase_instances: List[Mapping[str, object]],
    ) -> tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
        species_mol: Dict[str, Dict[str, float]] = {}
        species_kg: Dict[str, Dict[str, float]] = {}
        for instance in phase_instances:
            instance_id = str(instance['instance_id'])
            mass_kg = float(instance.get('physical_mass_kg') or 0.0)
            if not math.isfinite(mass_kg) or mass_kg <= 0.0:
                continue
            formula_token = str(
                instance.get('formula_or_endmember_token') or ''
            ).strip()
            if not str(instance.get('phase') or '').startswith('liquid'):
                try:
                    molar_mass = (
                        _MELTSBackendSupport._alphamelts_formula_molar_mass_kg_mol(
                            formula_token
                        )
                    )
                except ValueError:  # solver tokens are not always formulas
                    molar_mass = 0.0
                if molar_mass > 0.0 and math.isfinite(molar_mass):
                    species_kg[instance_id] = {formula_token: mass_kg}
                    species_mol[instance_id] = {
                        formula_token: mass_kg / molar_mass
                    }
                    continue

            composition = dict(instance.get('composition_wt_pct') or {})
            instance_kg: Dict[str, float] = {}
            instance_mol: Dict[str, float] = {}
            for species, raw_wt_pct in composition.items():
                wt_pct = float(raw_wt_pct)
                if not math.isfinite(wt_pct) or wt_pct <= 0.0:
                    continue
                component_kg = mass_kg * wt_pct / 100.0
                molar_mass = resolve_species_formula(
                    str(species)
                ).molar_mass_kg_per_mol()
                instance_kg[str(species)] = component_kg
                instance_mol[str(species)] = component_kg / molar_mass
            if instance_kg:
                species_kg[instance_id] = instance_kg
                species_mol[instance_id] = instance_mol
        return species_mol, species_kg

    @staticmethod
    def _alphamelts_formula_molar_mass_kg_mol(formula: str) -> float:
        # AlphaMELTS annotates oxidation state with prime marks (for example
        # Fe'' in olivine).  Charge does not alter elemental molar mass.
        formula = formula.replace("'", '').replace('"', '')
        tokens = re.findall(r'[A-Z][a-z]?|[()]|\d+(?:\.\d+)?', formula)
        if not tokens or ''.join(tokens) != formula:
            raise ValueError(f'invalid AlphaMELTS formula token {formula!r}')

        def parse_group(index: int, *, nested: bool) -> tuple[Dict[str, float], int]:
            atoms: Dict[str, float] = {}
            while index < len(tokens):
                token = tokens[index]
                if token == ')':
                    if not nested:
                        raise ValueError(f'unmatched formula close in {formula!r}')
                    return atoms, index + 1
                if token == '(':
                    group, index = parse_group(index + 1, nested=True)
                    multiplier = 1.0
                    if index < len(tokens) and re.fullmatch(
                        r'\d+(?:\.\d+)?', tokens[index]
                    ):
                        multiplier = float(tokens[index])
                        index += 1
                    for element, count in group.items():
                        atoms[element] = atoms.get(element, 0.0) + count * multiplier
                    continue
                if token not in ATOMIC_WEIGHTS_G_PER_MOL:
                    raise ValueError(f'invalid element in {formula!r}')
                index += 1
                count = 1.0
                if index < len(tokens) and re.fullmatch(
                    r'\d+(?:\.\d+)?', tokens[index]
                ):
                    count = float(tokens[index])
                    index += 1
                atoms[token] = atoms.get(token, 0.0) + count
            if nested:
                raise ValueError(f'unclosed formula group in {formula!r}')
            return atoms, index

        atoms, final_index = parse_group(0, nested=False)
        if final_index != len(tokens) or not atoms:
            raise ValueError(f'invalid AlphaMELTS formula token {formula!r}')
        return sum(
            ATOMIC_WEIGHTS_G_PER_MOL[element] * count
            for element, count in atoms.items()
        ) / 1000.0

    def _parse_composition_table(
        self,
        output: str,
        *,
        table_name: str,
    ) -> dict[str, float]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        header_index = next(
            (
                index for index, line in enumerate(lines)
                if line.startswith('index ')
                and all(name in line.split() for name in ('Pressure', 'Temperature', 'mass'))
            ),
            None,
        )
        if header_index is None:
            if lines:
                raise ValueError(f'{table_name} lacks parseable header')
            return {}
        headers = lines[header_index].split()
        oxides = headers[headers.index('mass') + 1:]
        if not oxides or header_index + 1 >= len(lines):
            raise ValueError(f'{table_name} lacks composition row')
        values = lines[header_index + 1].split()
        if values[-1:] == ['---']:
            try:
                mass_g = float(values[headers.index('mass')])
            except (IndexError, ValueError) as exc:
                raise ValueError(f'{table_name} malformed empty row') from exc
            if mass_g != 0.0:
                raise ValueError(f'{table_name} non-zero mass uses --- sentinel')
            return {}
        if len(values) != len(headers):
            raise ValueError(
                f'{table_name} row width {len(values)} != header width {len(headers)}'
            )
        try:
            return {
                oxide: float(values[headers.index(oxide)])
                for oxide in oxides
            }
        except ValueError as exc:
            raise ValueError(f'{table_name} has non-numeric composition') from exc

    def _extract_subprocess_activity_mapping(self, output: str) -> dict:
        lines = output.splitlines()
        activities: Dict[str, float] = {}
        for line in lines:
            activities.update(self._activity_assignments_from_line(line))
        for idx, line in enumerate(lines):
            if 'activit' not in line.lower():
                continue
            activities.update(self._activity_pairs_from_line(line))
            table = self._activity_table_after(lines, idx)
            if table:
                activities.update(table)
        return activities

    def _activity_assignments_from_line(self, line: str) -> dict:
        if 'activit' not in line.lower():
            return {}
        number = r'([0-9.+\-Ee]+)'
        name = r'([A-Za-z][A-Za-z0-9_]*(?:_Liq)?)'
        patterns = (
            rf'\bactivity(?:\s+of)?\s+{name}\s*[=:]\s*{number}',
            rf'\b{name}\s+activity\s*[=:]\s*{number}',
        )
        activities: Dict[str, float] = {}
        for pattern in patterns:
            for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                activities[match.group(1)] = float(match.group(2))
        return activities

    def _activity_pairs_from_line(self, line: str) -> dict:
        if ':' not in line:
            return {}
        tokens = line.split(':', 1)[1].split()
        return self._activity_pairs_from_tokens(tokens)

    def _activity_table_after(self, lines: list[str], idx: int) -> dict:
        for header_idx in range(idx + 1, min(idx + 6, len(lines))):
            header = lines[header_idx].strip()
            if not header:
                continue
            names = header.split()
            if not any(self._looks_like_activity_label(name) for name in names):
                continue
            for values_idx in range(header_idx + 1, min(header_idx + 5, len(lines))):
                value_tokens = lines[values_idx].strip().split()
                if not value_tokens:
                    continue
                values = []
                for token in value_tokens:
                    if self._is_number(token):
                        values.append(float(token))
                if len(values) >= len(names):
                    return {
                        name: value
                        for name, value in zip(names, values)
                    }
        return {}

    def _activity_pairs_from_tokens(self, tokens: list[str]) -> dict:
        activities: Dict[str, float] = {}
        idx = 0
        while idx + 1 < len(tokens):
            name = tokens[idx].strip(',')
            raw_value = tokens[idx + 1].strip(',')
            if (
                self._looks_like_activity_label(name)
                and self._is_number(raw_value)
            ):
                activities[name] = float(raw_value)
                idx += 2
                continue
            idx += 1
        return activities

    def _looks_like_activity_label(self, name: object) -> bool:
        label = str(name).strip().strip(',')
        if not label or self._is_number(label):
            return False
        return bool(re.search(r'[A-Za-z]', label))

    def _parse_single_point_stdout(self, output: str, *,
                                   requested_temperature_C: float,
                                   pressure_bar: float, fO2_log: float,
                                   total_input_kg: float,
                                   warnings=None,
                                   diagnostics: Optional[
                                       Mapping[str, object]
                                   ] = None,
                                   success_diagnostics: Optional[
                                       Mapping[str, object]
                                   ] = None,
                                   run_mode: AlphaMELTSSubprocessRunMode,
                                   system_output: str,
                                   fO2_constraint: Mapping[str, object],
                                   table_outputs: Optional[
                                       Mapping[str, str]
                                   ] = None,
                                   ) -> EquilibriumResult:
        physical_input_kg = float(total_input_kg)
        if not math.isfinite(physical_input_kg) or physical_input_kg <= 0.0:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT,
                f'invalid physical input mass {total_input_kg!r}',
            )
        executed_temperatures_C = self._parse_executed_temperatures_C(output)
        tables = dict(table_outputs or {})
        missing_tables = [
            table_name
            for table_name in (
                'System_main_tbl.txt',
                'Phase_main_tbl.txt',
                'Solid_comp_tbl.txt',
                'Bulk_comp_tbl.txt',
                'Liquid_comp_tbl.txt',
            )
            if table_outputs is not None
            and not str(tables.get(table_name) or '').strip()
        ]
        try:
            system_values = self._parse_system_main_output(system_output)
            phase_values = self._parse_phase_main_output(
                tables.get('Phase_main_tbl.txt', '')
            )
            solid_composition = self._parse_composition_table(
                tables.get('Solid_comp_tbl.txt', ''),
                table_name='Solid_comp_tbl.txt',
            )
            bulk_composition = self._parse_composition_table(
                tables.get('Bulk_comp_tbl.txt', ''),
                table_name='Bulk_comp_tbl.txt',
            )
            liquid_table_composition = self._parse_composition_table(
                tables.get('Liquid_comp_tbl.txt', ''),
                table_name='Liquid_comp_tbl.txt',
            )
        except ValueError as exc:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING,
                str(exc),
            ) from exc
        phase_thermo = dict(phase_values.get('phase_thermo') or {})
        phase_compositions = dict(
            phase_values.get('phase_compositions') or {}
        )
        phase_instances = [
            dict(instance)
            for instance in phase_values.get('phase_instances') or []
        ]
        if liquid_table_composition:
            phase_compositions['liquid'] = liquid_table_composition
        stable_verdict = re.search(r'<> Stable .+ assemblage achieved\.', output)

        phases_present: List[str] = []
        phase_masses_kg: Dict[str, float] = {}
        liquid_composition_wt_pct: Dict[str, float] = {}
        liquid_fraction: Optional[float] = None
        result_warnings = list(warnings or [])
        activity_coefficients = self._extract_subprocess_activity_mapping(output)
        liquidus_C = self._parse_liquidus_C(output)
        if liquidus_C is not None:
            result_warnings.append(f'AlphaMELTS liquidus_C={liquidus_C:.3f}')
        lines = output.splitlines()
        stable_indices = [
            index for index, line in enumerate(lines)
            if re.search(r'<> Stable .+ assemblage achieved\.', line)
        ]
        phase_lines = lines[stable_indices[-1]:] if stable_indices else lines
        phase_instance_masses_kg: Dict[str, float] = {}
        for idx, line in enumerate(phase_lines):
            stripped = line.strip()
            if stripped.startswith('liquid:'):
                if 'liquid' not in phases_present:
                    phases_present.append('liquid')
                headers = stripped.split(':', 1)[1].split()
                if idx + 1 < len(phase_lines):
                    values = phase_lines[idx + 1].split()
                    if len(values) >= 2 and values[1] == 'g':
                        try:
                            phase_masses_kg['liquid'] = float(values[0]) / 1000.0
                        except ValueError:
                            pass
                        for oxide, raw in zip(headers, values[2:]):
                            try:
                                liquid_composition_wt_pct[oxide] = float(raw)
                            except ValueError:
                                continue

            phase_match = re.match(
                r'^([A-Za-z][A-Za-z0-9_\-]*):\s+'
                r'([0-9.+\-Ee]+)\s+g\b',
                stripped,
            )
            if phase_match:
                phase_instance = phase_match.group(1)
                phase = re.sub(r'\d+$', '', phase_instance)
                if phase != 'liquid' and phase not in phases_present:
                    phases_present.append(phase)
                try:
                    mass_g = float(phase_match.group(2))
                except ValueError:
                    mass_g = 0.0
                if mass_g > 0.0:
                    mass_kg = mass_g / 1000.0
                    phase_masses_kg[phase] = (
                        phase_masses_kg.get(phase, 0.0) + mass_kg
                    )
                    phase_instance_masses_kg[phase_instance] = (
                        phase_instance_masses_kg.get(phase_instance, 0.0)
                        + mass_kg
                    )

            melt_match = re.search(
                r'Melt fraction\s*=\s*([0-9.+\-Ee]+)', stripped)
            if melt_match:
                liquid_fraction = float(melt_match.group(1))

        failure_reason = self._subprocess_failure_reason(output)
        if failure_reason is not None:
            result_warnings.append(failure_reason)
            if not executed_temperatures_C:
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_EXECUTED_T_MISSING,
                    failure_reason,
                )
            if (
                run_mode is AlphaMELTSSubprocessRunMode.ISOTHERMAL
                and any(
                    not math.isclose(
                        value,
                        float(requested_temperature_C),
                        rel_tol=0.0,
                        abs_tol=ALPHAMELTS_EXECUTED_T_TOLERANCE_C,
                    )
                    for value in executed_temperatures_C
                )
            ):
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_EXECUTED_T_MISMATCH,
                    f'requested={float(requested_temperature_C):.9g} C; '
                    f'executed={executed_temperatures_C!r} C',
                )
            failure_temperature_C = executed_temperatures_C[-1]
            return self._emit_equilibrium_result(
                temperature_C=failure_temperature_C,
                requested_temperature_C=requested_temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                warnings=result_warnings,
                status='out_of_domain',
                diagnostics=self._diagnostics_with_backend_status_reason(
                    diagnostics,
                    backend_status='out_of_domain',
                    reason=ALPHAMELTS_REASON_NO_CONVERGENCE,
                ),
            )
        if not phases_present:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT,
                'no parseable phase assemblage',
            )
        if missing_tables:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING,
                'AlphaMELTS table suite missing: '
                + ', '.join(missing_tables),
            )
        if not executed_temperatures_C:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_EXECUTED_T_MISSING
            )
        executed_temperature_C = executed_temperatures_C[-1]
        if run_mode is AlphaMELTSSubprocessRunMode.ISOTHERMAL:
            mismatches = [
                value for value in executed_temperatures_C
                if not math.isclose(
                    value,
                    float(requested_temperature_C),
                    rel_tol=0.0,
                    abs_tol=ALPHAMELTS_EXECUTED_T_TOLERANCE_C,
                )
            ]
            if mismatches:
                raise _alphamelts_backend_failure_error(
                    ALPHAMELTS_REASON_EXECUTED_T_MISMATCH,
                    f'requested={float(requested_temperature_C):.9g} C; '
                    f'executed={mismatches!r} C; '
                    f'tolerance={ALPHAMELTS_EXECUTED_T_TOLERANCE_C:g} C',
                )
        if not system_values:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING,
                'System_main_tbl.txt missing or unparseable',
            )
        table_temperature_C = system_values.get('temperature_C')
        if (
            table_temperature_C is not None
            and not math.isclose(
                float(table_temperature_C),
                executed_temperature_C,
                rel_tol=0.0,
                abs_tol=ALPHAMELTS_EXECUTED_T_TOLERANCE_C,
            )
        ):
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_EXECUTED_T_MISMATCH,
                f'stdout={executed_temperature_C:.9g} C; '
                f'system_table={float(table_temperature_C):.9g} C',
            )
        property_diagnostics = {
            key: system_values[key]
            for key in (
                'liquid_density_kg_m3',
                'liquid_viscosity_Pa_s',
                'system_enthalpy',
                'system_entropy',
                'system_volume',
                'system_heat_capacity_Cp',
                'system_dVdP',
                'system_dVdT',
                'system_fO2_delta_QFM',
                'system_solid_density_rhos',
                'system_phi',
                'system_chisqr',
            )
            if key in system_values
        }
        result_diagnostics = self._merge_diagnostics(
            success_diagnostics,
            {
                'requested_temperature_C': float(requested_temperature_C),
                'executed_temperature_C': executed_temperature_C,
                'subprocess_run_mode': run_mode.value,
                'fO2_constraint': dict(fO2_constraint),
                'liquid_properties_source': 'System_main_tbl.txt',
                **property_diagnostics,
                'phase_instance_masses_solver_basis_kg': (
                    phase_instance_masses_kg
                ),
            },
        )
        fO2_header = str(system_values.get('fO2_header') or '')
        if (
            not fO2_header.lower().startswith('fo2(absolute)')
            or 'fO2_value' not in system_values
        ):
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING,
                'System_main_tbl.txt lacks absolute engine fO2 echo',
            )
        executed_fO2_log = float(system_values['fO2_value'])
        result_diagnostics['intrinsic_fO2_log'] = executed_fO2_log
        result_diagnostics['engine_reported_fO2_log'] = executed_fO2_log
        result_diagnostics['engine_reported_fO2_header'] = fO2_header
        if not math.isclose(
            executed_fO2_log,
            float(fO2_log),
            rel_tol=0.0,
            abs_tol=ALPHAMELTS_FO2_ECHO_TOLERANCE_LOG10,
        ):
            result_diagnostics.update({
                'operating_point_clamped': True,
                'fO2_clamped': True,
                'requested_fO2_log': float(fO2_log),
                'solved_fO2_log': executed_fO2_log,
                'authoritative_for_requested_conditions': False,
                'authoritative_for_solved_conditions': True,
            })
            result_warnings.append(
                'AlphaMELTS solved at a different fO2: '
                f'requested={float(fO2_log):g}, solved={executed_fO2_log:g}'
            )
        if stable_verdict is None:
            result_warnings.append(
                'AlphaMELTS stable assemblage banner absent; accepted parseable phase rows'
            )
        solver_basis_kg = sum(phase_masses_kg.values())
        if not math.isfinite(solver_basis_kg) or solver_basis_kg <= 0.0:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT,
                'phase rows have zero total solver-basis mass',
            )
        system_mass_g = system_values.get('system_mass_g')
        if system_mass_g is None:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_SYSTEM_OUTPUT_MISSING,
                'System_main_tbl.txt lacks engine system mass',
            )
        system_mass_kg = float(system_mass_g) / 1000.0
        if not math.isclose(
            solver_basis_kg,
            system_mass_kg,
            rel_tol=1.0e-6,
            # Phase_main_tbl prints mass to 0.001 g while System_main_tbl
            # retains more digits.  Half one printed unit is the tightest
            # closure tolerance justified by the engine-owned text.
            abs_tol=5.0e-7,
        ):
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_PHASE_MASS_INCOMPLETE,
                f'parsed={solver_basis_kg * 1000.0:.9g} g; '
                f'system={float(system_mass_g):.9g} g',
            )
        normalized_phase_thermo = {}
        for phase, raw_values in phase_thermo.items():
            # alphaMELTS Phase_main_tbl extensive values use the solver's
            # modal-mass basis: H is J, S/Cp are J/K, and V is cm3. Convert
            # cm3 * 1e-6 = m3 and retain the pre-rescale phase mass so these
            # values cannot be mistaken for the physical batch basis below.
            normalized_phase_thermo[phase] = {
                'enthalpy_J': raw_values.get('enthalpy'),
                'entropy_J_K': raw_values.get('entropy'),
                'volume_m3': (
                    None
                    if raw_values.get('volume') is None
                    else float(raw_values['volume']) * 1.0e-6
                ),
                'heat_capacity_J_K': raw_values.get('heat_capacity_Cp'),
                'density_kg_m3': raw_values.get('density_kg_m3'),
                'reference_mass_kg': raw_values.get('reference_mass_kg'),
                'reference_basis': 'alphamelts_solver_phase_amount',
            }
        phase_thermo = normalized_phase_thermo
        system_volume_native = system_values.get('system_volume')
        system_volume_m3 = (
            None
            if system_volume_native is None
            else float(system_volume_native) * 1.0e-6
        )
        result_diagnostics['thermodynamic_basis'] = {
            'reference_basis': 'alphamelts_solver_system_amount',
            'reference_mass_kg': system_mass_kg,
            'system_enthalpy': {'units': 'J'},
            'system_entropy': {'units': 'J/K'},
            'system_volume': {'units': 'm3', 'source_units': 'cm3'},
            'system_heat_capacity_Cp': {'units': 'J/K'},
        }
        # Premise: alphaMELTS phase rows are modal masses on an arbitrary
        # solver basis; the adapter input carries the physical batch mass.
        # Algebra: m_i,physical = (m_i,basis / sum(m_basis)) * m_input.
        # Unit check: kg / kg * kg = kg. Sanity: phase masses now sum to the
        # caller's batch mass without changing modal fractions.
        mass_scale = physical_input_kg / solver_basis_kg
        phase_masses_kg = {
            phase: mass_kg * mass_scale
            for phase, mass_kg in phase_masses_kg.items()
        }
        for instance in phase_instances:
            instance['physical_mass_kg'] = (
                float(instance['solver_basis_mass_kg']) * mass_scale
            )
        phase_species_mol, phase_species_kg = (
            self._phase_species_from_instances(phase_instances)
        )
        eq = self._emit_equilibrium_result(
            temperature_C=executed_temperature_C,
            requested_temperature_C=requested_temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=executed_fO2_log,
            phases_present=phases_present,
            phase_masses_kg=phase_masses_kg,
            phase_species_mol=phase_species_mol,
            phase_species_kg=phase_species_kg,
            phase_instances=phase_instances,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_composition_wt_pct,
            liquid_viscosity_Pa_s=float(
                system_values['liquid_viscosity_Pa_s']
            ) if 'liquid_viscosity_Pa_s' in system_values else None,
            liquid_density_kg_m3=float(
                system_values['liquid_density_kg_m3']
            ) if 'liquid_density_kg_m3' in system_values else None,
            system_enthalpy=system_values.get('system_enthalpy'),
            system_entropy=system_values.get('system_entropy'),
            system_volume=system_volume_m3,
            system_heat_capacity_Cp=system_values.get(
                'system_heat_capacity_Cp'
            ),
            system_dVdP=system_values.get('system_dVdP'),
            system_dVdT=system_values.get('system_dVdT'),
            system_fO2_delta_QFM=system_values.get(
                'system_fO2_delta_QFM'
            ),
            system_solid_density_rhos=system_values.get(
                'system_solid_density_rhos'
            ),
            system_phi=system_values.get('system_phi'),
            system_chisqr=system_values.get('system_chisqr'),
            phase_thermo=phase_thermo,
            phase_compositions=phase_compositions,
            solid_composition_wt_pct=solid_composition,
            bulk_composition_wt_pct=bulk_composition,
            activity_coefficients=activity_coefficients,
            warnings=result_warnings,
            status='ok',
            diagnostics=result_diagnostics,
        )
        if liquidus_C is not None:
            eq.liquidus_T_C = float(liquidus_C)
        return eq

    def _subprocess_failure_reason(self, output: str) -> Optional[str]:
        if re.search(r'Quadratic convergence failure\. Aborting\.', output):
            return (
                'AlphaMELTS subprocess reported convergence failure: '
                'Quadratic convergence failure. Aborting.'
            )
        if re.search(r'Initial calculation failed', output):
            return (
                'AlphaMELTS subprocess reported convergence failure: '
                'Initial calculation failed.'
            )
        return None

    def _parse_petthermotools_result(self, results, *, temperature_C: float,
                                     pressure_bar: float, fO2_log: float,
                                     comp_wt: dict, total_input_kg: float = 0.1,
                                     require_solved_fo2: bool = False,
                                     warnings=None,
                                     diagnostics: Optional[
                                         Mapping[str, object]
                                     ] = None,
                                     ) -> EquilibriumResult:
        run_result = self._select_petthermotools_run(results)
        conditions = self._first_row_mapping(run_result.get('Conditions', {}))
        total_mass = self._first_number(conditions, ('mass', 'Mass'))
        solved_pressure_bar = self._first_number(
            conditions,
            ('P_bar', 'pressure_bar', 'Pressure_bar'),
        )
        if solved_pressure_bar is None:
            solved_pressure_bar = float(pressure_bar)
        solved_fO2_log = self._first_number(
            conditions,
            ('fO2_log', 'logfO2', 'log_fO2'),
        )
        if require_solved_fo2 and solved_fO2_log is None:
            return self._unapplied_absolute_fo2_result(
                temperature_C=temperature_C,
                pressure_bar=solved_pressure_bar,
                fO2_log=fO2_log,
                transport='python_api',
                warnings=list(warnings or []),
            )
        result_fO2_log = (
            float(solved_fO2_log)
            if solved_fO2_log is not None
            else float(fO2_log)
        )
        fO2_diagnostics: dict[str, object] = {}
        fO2_warnings: list[str] = []
        if (
            solved_fO2_log is not None
            and not math.isclose(
                result_fO2_log,
                float(fO2_log),
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
        ):
            fO2_diagnostics = {
                'operating_point_clamped': True,
                'fO2_clamped': True,
                'requested_fO2_log': float(fO2_log),
                'solved_fO2_log': result_fO2_log,
                'authoritative_for_requested_conditions': False,
                'authoritative_for_solved_conditions': True,
            }
            fO2_warnings.append(
                'PetThermoTools solved at a different fO2: '
                f'requested={float(fO2_log):g}, solved={result_fO2_log:g}'
            )

        phases_present: List[str] = []
        phase_masses_g: Dict[str, float] = {}
        for key, value in run_result.items():
            if not self._is_petthermotools_phase_key(key, value):
                continue
            phase = str(key)
            prop = self._first_row_mapping(run_result.get(f'{phase}_prop', {}))
            mass_g = self._first_number(prop, ('mass', 'Mass'))
            if mass_g is None:
                row = self._first_row_mapping(value)
                mass_g = self._first_number(row, ('mass', 'Mass'))
            if mass_g is not None and mass_g > 0.0:
                phase_name = phase[:-1] if phase.endswith('_Liq') else phase
                if phase_name not in phases_present:
                    phases_present.append(phase_name)
                phase_masses_g[phase_name] = mass_g

        physical_input_kg = float(total_input_kg)
        solver_basis_g = total_mass or sum(phase_masses_g.values())
        if (
            not math.isfinite(physical_input_kg)
            or physical_input_kg <= 0.0
            or not solver_basis_g
            or not math.isfinite(float(solver_basis_g))
            or float(solver_basis_g) <= 0.0
        ):
            raise MeltBackendError(
                'PetThermoTools phase mass scaling requires positive '
                'physical input and solver-basis masses'
            )
        # Premise: PetThermoTools phase masses are grams on its solver basis.
        # Algebra: m_i,physical = m_i,basis / m_total,basis * m_input.
        # Unit check: g / g * kg = kg. Sanity: changing batch size scales every
        # phase mass linearly while leaving liquid_fraction unchanged.
        phase_masses_kg = {
            phase: mass_g / float(solver_basis_g) * physical_input_kg
            for phase, mass_g in phase_masses_g.items()
        }
        liquid_fraction: Optional[float] = None
        liquid_composition_wt_pct = {}
        liquid_key = self._select_liquid_phase_key(run_result)
        if liquid_key is not None:
            liquid_row = self._first_row_mapping(run_result.get(liquid_key, {}))
            liquid_composition_wt_pct = self._extract_liquid_composition(
                liquid_row)
            liquid_mass = phase_masses_g.get(liquid_key)
            if liquid_mass is None:
                liquid_mass = phase_masses_g.get(
                    liquid_key[:-1] if liquid_key.endswith('_Liq') else liquid_key)
            if liquid_mass is not None and total_mass and total_mass > 0.0:
                liquid_fraction = liquid_mass / total_mass

        result_warnings = [*(warnings or []), *fO2_warnings]
        activity_coefficients, activity_diagnostics = (
            self._extract_activity_mapping(
                run_result,
                liquid_composition_wt_pct=liquid_composition_wt_pct,
            )
        )
        if not activity_coefficients:
            activity_coefficients = (
                self._extract_activities_from_chemical_potentials(
                    run_result,
                    temperature_C=temperature_C,
                )
            )
            if activity_coefficients:
                activity_diagnostics = {
                    'diagnostic_activity_source': 'chemical_potential_mu_minus_mu0'
                }
        if not activity_coefficients:
            result_warnings.append(
                'PetThermoTools chemical potentials absent; '
                'activity-scaled Antoine fallback skipped'
            )
        return self._emit_equilibrium_result(
            temperature_C=temperature_C,
            pressure_bar=solved_pressure_bar,
            fO2_log=result_fO2_log,
            phases_present=phases_present,
            phase_masses_kg=phase_masses_kg,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_composition_wt_pct,
            activity_coefficients=activity_coefficients,
            warnings=result_warnings,
            status='ok',
            diagnostics=self._merge_diagnostics(
                diagnostics,
                fO2_diagnostics,
                activity_diagnostics,
            ),
        )

    def _select_petthermotools_run(self, results) -> dict:
        if isinstance(results, tuple) and results:
            results = results[0]
        if not isinstance(results, dict):
            return {'All': results}
        if any(key in results for key in ('Conditions', 'Mass', 'Input')):
            return results
        for value in results.values():
            if isinstance(value, tuple) and value:
                value = value[0]
            if isinstance(value, dict):
                return value
        return results

    def _first_row_mapping(self, table) -> dict:
        if table is None:
            return {}
        if isinstance(table, Mapping):
            return dict(table)
        try:
            if hasattr(table, 'empty') and bool(table.empty):
                return {}
            if hasattr(table, 'iloc'):
                row = table.iloc[0]
                if hasattr(row, 'to_dict'):
                    return dict(row.to_dict())
            if hasattr(table, 'to_dict'):
                value = table.to_dict()
                if isinstance(value, Mapping):
                    if all(isinstance(v, Mapping) for v in value.values()):
                        return {
                            key: next(iter(v.values()))
                            for key, v in value.items()
                            if v
                        }
                    return dict(value)
        except (IndexError, KeyError, TypeError, ValueError):
            return {}
        return {}

    def _first_number(self, values: Mapping[str, object],
                      keys: tuple[str, ...]) -> Optional[float]:
        for key in keys:
            if key in values and self._is_number(values[key]):
                return float(values[key])
        return None

    def _is_number(self, value) -> bool:
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def _is_petthermotools_phase_key(self, key: str, value) -> bool:
        name = str(key)
        if name in PETTHERMOTOOLS_NON_PHASE_KEYS:
            return False
        if name.endswith('_prop') or name.endswith('_keys'):
            return False
        if hasattr(value, 'empty'):
            return not bool(value.empty)
        if isinstance(value, Mapping):
            return bool(value)
        return False

    def _select_liquid_phase_key(self, results: Mapping[str, object]) -> Optional[str]:
        for key in results:
            name = str(key)
            if name.lower().startswith('liquid') and not name.endswith('_prop'):
                return name
        return None

    def _extract_liquid_composition(self, row: Mapping[str, object]) -> dict:
        composition = {}
        for key, value in row.items():
            if not self._is_number(value):
                continue
            # _canonical_oxide_name strips a trailing '_Liq' internally.
            oxide = self._canonical_oxide_name(str(key))
            if oxide == 'FeO_total':
                oxide = 'FeO'
            if oxide in MELTS_OXIDE_BASIS:
                composition[oxide] = float(value)
        return composition

    def _extract_activity_mapping(
        self,
        results: Mapping[str, object],
        *,
        liquid_composition_wt_pct: Mapping[str, float],
    ) -> tuple[dict, dict[str, object]]:
        for key in ('melt_oxide_activities',):
            if key not in results:
                continue
            row = self._first_row_mapping(results[key])
            mapped = self._finite_activity_mapping(row)
            if mapped:
                return mapped, {'diagnostic_activity_source': key}

        for key in ('activities', 'Activities'):
            if key not in results:
                continue
            row = self._liquid_activity_row_mapping(results[key])
            mapped = self._finite_activity_mapping(row)
            if mapped:
                return mapped, {'diagnostic_activity_source': key}

        if 'activity_coefficients' in results:
            row = self._liquid_activity_row_mapping(
                results['activity_coefficients']
            )
            if not row and isinstance(results['activity_coefficients'], Mapping):
                raw_coefficients = dict(results['activity_coefficients'])
                if raw_coefficients and all(
                    not isinstance(value, Mapping)
                    for value in raw_coefficients.values()
                ):
                    row = raw_coefficients
            mapped = self._activities_from_coefficients(
                row,
                liquid_composition_wt_pct,
            )
            if mapped:
                return mapped, {
                    'diagnostic_activity_source': (
                        'activity_coefficients_times_oxide_mole_fraction'
                    )
                }

        activities: Dict[str, float] = {}
        for key, value in results.items():
            name = str(key)
            if not name.endswith('_prop'):
                continue
            phase_name = name[: -len('_prop')]
            if not self._is_liquid_activity_phase(phase_name):
                continue
            row = self._first_row_mapping(value)
            for prop_name, prop_value in row.items():
                if not self._is_number(prop_value):
                    continue
                prop = str(prop_name)
                prop_lower = prop.lower()
                if prop_lower.endswith('_activity'):
                    species = prop[: -len('_activity')]
                elif prop_lower.startswith('activity_'):
                    species = prop[len('activity_'):]
                else:
                    continue
                if self._looks_like_activity_label(species):
                    activities[species] = float(prop_value)
        mapped = self._finite_activity_mapping(activities)
        if mapped:
            return mapped, {'diagnostic_activity_source': 'phase_activity_fields'}
        return {}, {}

    def _activities_from_coefficients(
        self,
        coefficients: Mapping[str, object],
        liquid_composition_wt_pct: Mapping[str, float],
    ) -> dict[str, float]:
        mole_amounts: dict[str, float] = {}
        for raw_name, raw_wt_pct in dict(liquid_composition_wt_pct or {}).items():
            oxide = canonical_melt_oxide_activity_name(raw_name)
            if oxide is None:
                continue
            wt_pct = float(raw_wt_pct)
            if not math.isfinite(wt_pct) or wt_pct < 0.0:
                raise MeltBackendError(
                    f'invalid liquid composition for activity conversion: '
                    f'{raw_name}={raw_wt_pct!r}'
                )
            molar_mass = resolve_species_formula(
                oxide
            ).molar_mass_kg_per_mol()
            mole_amounts[oxide] = wt_pct / molar_mass
        total_moles = sum(mole_amounts.values())
        if total_moles <= 0.0:
            return {}

        activities: dict[str, float] = {}
        for raw_name, raw_gamma in dict(coefficients or {}).items():
            oxide = canonical_melt_oxide_activity_name(raw_name)
            if oxide is None or oxide not in mole_amounts:
                continue
            gamma = float(raw_gamma)
            if not math.isfinite(gamma) or gamma <= 0.0:
                continue
            mole_fraction = mole_amounts[oxide] / total_moles
            # Premise: thermodynamic activity is a_i = gamma_i * x_i.
            # Algebra uses oxide mole fraction from wt_i / M_i normalized by
            # sum(wt_j / M_j). Unit check: gamma and x are dimensionless, so
            # activity is dimensionless. Sanity: gamma=0.5 and x=0.02 gives
            # a=0.01, not the 0.5 value that inflated vapor flux by 50x.
            activities[str(raw_name)] = gamma * mole_fraction
        return activities

    def _finite_activity_mapping(self, values: Mapping[str, object]) -> dict:
        activities: Dict[str, float] = {}
        for raw_name, raw_value in dict(values or {}).items():
            if not self._looks_like_activity_label(raw_name):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value > 0.0 and math.isfinite(value):
                activities[str(raw_name)] = value
        return activities

    def _liquid_activity_row_mapping(self, table) -> dict:
        for row in self._activity_row_mappings(table):
            phase = self._activity_row_phase(row)
            if phase is None or not self._is_liquid_activity_phase(phase):
                continue
            return {
                key: value
                for key, value in row.items()
                if str(key).lower() not in {
                    'phase', 'phase_name', 'phasename', 'name',
                }
            }
        return {}

    def _activity_row_mappings(self, table) -> list[dict]:
        if table is None:
            return []
        if isinstance(table, Mapping):
            nested = [
                (key, value)
                for key, value in table.items()
                if isinstance(value, Mapping)
            ]
            if nested:
                rows = []
                for phase, row in nested:
                    mapped = dict(row)
                    mapped.setdefault('phase', phase)
                    rows.append(mapped)
                return rows
            return [dict(table)]
        if isinstance(table, (list, tuple)):
            return [dict(row) for row in table if isinstance(row, Mapping)]
        try:
            if hasattr(table, 'empty') and bool(table.empty):
                return []
            if hasattr(table, 'to_dict'):
                try:
                    records = table.to_dict('records')
                except TypeError:
                    records = None
                if isinstance(records, list):
                    return [
                        dict(row) for row in records
                        if isinstance(row, Mapping)
                    ]
            if hasattr(table, 'iterrows'):
                return [
                    dict(row.to_dict() if hasattr(row, 'to_dict') else row)
                    for _, row in table.iterrows()
                ]
        except (TypeError, ValueError):
            return []
        return []

    def _activity_row_phase(self, row: Mapping[str, object]) -> Optional[str]:
        for key in ('phase', 'Phase', 'phase_name', 'Phase Name', 'name', 'Name'):
            if key in row and row[key] is not None:
                return str(row[key])
        return None

    def _is_liquid_activity_phase(self, phase: object) -> bool:
        name = str(phase).strip().lower()
        return name == 'liq' or name.startswith('liquid')

    def _extract_activities_from_chemical_potentials(
        self,
        results: Mapping[str, object],
        *,
        temperature_C: float,
    ) -> dict:
        mu = self._extract_potential_mapping(
            results,
            (
                'chemical_potentials',
                'chem_potentials',
                'mu',
                'mu_oxides',
                'oxide_mu',
            ),
            ('_mu', '_chemical_potential'),
        )
        mu0 = self._extract_potential_mapping(
            results,
            (
                'standard_chemical_potentials',
                'pure_chemical_potentials',
                'reference_chemical_potentials',
                'mu0',
                'mu0_oxides',
                'oxide_mu0',
            ),
            ('_mu0', '_pure_mu', '_standard_mu', '_reference_mu'),
        )
        if not mu or not mu0:
            return {}
        T_K = float(temperature_C) + 273.15
        raw_activities: Dict[str, float] = {}
        for species, mu_i in mu.items():
            if species not in mu0:
                continue
            try:
                activity = activity_from_chem_potential(mu_i, mu0[species], T_K)
            except (OverflowError, ValueError):
                continue
            if activity > 0.0 and math.isfinite(activity):
                raw_activities[str(species)] = activity
        return raw_activities

    def _extract_potential_mapping(
        self,
        results: Mapping[str, object],
        keys: tuple[str, ...],
        suffixes: tuple[str, ...],
    ) -> dict:
        for key in keys:
            if key in results:
                row = self._first_row_mapping(results[key])
                mapped = {
                    str(species): float(value)
                    for species, value in row.items()
                    if self._is_number(value)
                }
                if mapped:
                    return mapped
        potentials: Dict[str, float] = {}
        for key, value in results.items():
            name = str(key)
            if not name.endswith('_prop'):
                continue
            row = self._first_row_mapping(value)
            for prop_name, prop_value in row.items():
                if not self._is_number(prop_value):
                    continue
                prop = str(prop_name)
                prop_lower = prop.lower()
                for suffix in suffixes:
                    if prop_lower.endswith(suffix):
                        species = prop[: -len(suffix)]
                        if species:
                            potentials[species] = float(prop_value)
                        break
        return potentials

    def decompression_path(self, T_C: float, P_start_bar: float,
                           P_end_bar: float, dp_bar: float, *,
                           composition_kg: Optional[Dict[str, float]] = None,
                           composition_mol: Optional[Dict[str, float]] = None,
                           composition_mol_by_account: Optional[
                               Mapping[str, Mapping[str, float]]
                           ] = None,
                           species_formula_registry: Optional[
                               Mapping[str, object]
                           ] = None,
                           fO2_log: float = -9.0) -> list[EquilibriumResult]:
        if composition_mol_by_account is not None:
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                return [
                    self._domain_gate_result(
                        T_C,
                        P_start_bar,
                        fO2_log,
                        [
                            'unsupported ledger accounts present: '
                            + ', '.join(
                                f'{account}={species}'
                                for account, species in sorted(unsupported.items())
                            )
                        ],
                    )
                ]
            composition_mol = dict(
                composition_mol_by_account.get('process.cleaned_melt', {}))
        if composition_mol is not None:
            composition_kg = {
                species: float(mol)
                * resolve_species_formula(
                    species,
                    species_formula_registry,
                ).molar_mass_kg_per_mol()
                for species, mol in composition_mol.items()
                if float(mol) > 0.0
            }
        if composition_kg is None:
            raise ValueError('decompression_path requires composition input')
        total_input_kg = sum(
            float(mass_kg)
            for mass_kg in composition_kg.values()
            if math.isfinite(float(mass_kg)) and float(mass_kg) > 0.0
        )

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=T_C,
            pressure_bar=P_start_bar,
            fO2_log=fO2_log,
        )
        if domain_rejection is not None:
            return [domain_rejection]
        comp_wt = self._normalize_composition_to_melts_basis(raw_comp_wt)
        ptt = self._require_petthermotools_runtime()
        ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
        if not hasattr(ptt, 'isothermal_decompression'):
            raise AttributeError(
                'PetThermoTools isothermal_decompression API not found'
            )
        results = ptt.isothermal_decompression(
            Model=self._model,
            bulk=ptt_comp,
            T_C=T_C,
            P_start_bar=P_start_bar,
            P_end_bar=P_end_bar,
            dp_bar=dp_bar,
            fO2_buffer=self._redox_buffer,
            fO2_offset=self._fo2_offset,
            multi_processing=False,
        )
        if isinstance(results, Mapping) and all(
            isinstance(value, Mapping) for value in results.values()
        ):
            runs = list(results.values())
        else:
            runs = [results]
        return [
            self._parse_petthermotools_result(
                run,
                temperature_C=T_C,
                pressure_bar=P_start_bar,
                fO2_log=fO2_log,
                comp_wt=comp_wt,
                total_input_kg=total_input_kg,
                require_solved_fo2=True,
                warnings=self._last_normalization_warnings,
            )
            for run in runs
        ]

    # ------------------------------------------------------------------
    # Vapor pressure helpers
    # ------------------------------------------------------------------

    def _builtin_vapor_projection_for_subprocess(
        self,
        eq: EquilibriumResult,
    ) -> tuple[Dict[str, float], Dict[str, str], Dict[str, object]]:
        if float(eq.liquid_fraction or 0.0) <= 0.0:
            return {}, {}, {'vapor_pressure_zero_reason': 'no_liquid_phase'}
        if not eq.liquid_composition_wt_pct:
            raise RuntimeError(
                'AlphaMELTS subprocess vapor projection missing solved liquid '
                'composition for positive liquid fraction'
            )

        import yaml
        from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView

        provider = self._subprocess_vapor_pressure_provider
        if provider is None:
            data_path = (
                Path(__file__).resolve().parents[2]
                / 'data'
                / 'vapor_pressures.yaml'
            )
            with data_path.open(encoding='utf-8') as handle:
                vapor_data = yaml.safe_load(handle) or {}
            provider = BuiltinVaporPressureProvider(vapor_data)
            self._subprocess_vapor_pressure_provider = provider
        melt_mol = {}
        for species, wt_pct in eq.liquid_composition_wt_pct.items():
            mass_kg = float(wt_pct)
            if mass_kg <= 0.0:
                continue
            melt_mol[str(species)] = (
                mass_kg
                / resolve_species_formula(str(species)).molar_mass_kg_per_mol()
            )
        request = IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={'process.cleaned_melt': melt_mol},
                species_formula_registry={},
            ),
            temperature_C=eq.temperature_C,
            pressure_bar=eq.pressure_bar,
            fO2_log=eq.fO2_log,
            control_inputs={
                'pO2_bar': max(10.0 ** float(eq.fO2_log), 1e-30),
                'intrinsic_fO2_log': eq.fO2_log,
            },
        )
        result = provider.dispatch(request)
        if result.status != 'ok':
            raise RuntimeError(
                'builtin subprocess vapor projection failed: '
                f'status={result.status!r}; warnings={list(result.warnings)!r}'
            )
        diagnostic = dict(result.diagnostic or {})
        return (
            dict(diagnostic.get('vapor_pressures_Pa') or {}),
            dict(diagnostic.get('vapor_pressures_source') or {}),
            diagnostic,
        )

    def _vapor_pressures_via_vaporock_or_antoine(
        self,
        *,
        T_C: float,
        solved_melt_wt_pct: Mapping[str, float],
        liquid_fraction: Optional[float],
        fO2_log: float,
        pressure_bar: float,
        activities: Mapping[str, float],
    ) -> tuple[Dict[str, float], str]:
        """
        Vapor partial pressures (Pa) for the post-equilibrium melt.

        Delegates to the real, tested ``VapoRockBackend`` helper —
        alphaMELTS does NOT re-implement oxide projection, the K /
        log10(bar)->Pa conversion, or the ``(g)``-suffix normalisation.
        The helper's ``EquilibriumResult.vapor_pressures_Pa`` is ALREADY
        in Pa; it is returned as-is (no second bar->Pa scale, no routing
        through any normalizer — a stray 1e5 here silently inflates the
        Hertz-Knudsen evaporation flux).

        fO2 convention: the simulator's ``fO2_log`` is absolute
        log10(fO2/bar) (``core.py::_compute_intrinsic_melt_fO2``), and
        VapoRock's ``System.eval_gas_abundances(T_K, logfO2)`` consumes
        the same absolute log10(fO2/bar).  No buffer/offset conversion is
        applied — the alphaMELTS ``fO2_buffer``/``fO2_offset`` are a
        SEPARATE config input to the MELTS silicate solve, not this
        absolute quantity, and are deliberately NOT forwarded here.

        The melt fed to VapoRock is the composition alphaMELTS SOLVED
        (the equilibrium liquid), never the pre-equilibrium bulk. A zero
        ``liquid_fraction`` is a physical no-liquid state and returns an
        empty pressure map labelled ``no_liquid_phase``. A positive
        ``liquid_fraction`` with an empty solved liquid composition is a
        fail-loud solver/API contract violation.

        Returns ``(pressures_Pa, source_label)``.  FAIL-LOUD: never
        returns an empty dict for a volatile-bearing melt (an empty dict
        zeroes evaporation flux).  On any non-``ok`` VapoRock outcome it
        logs a WARN and explicitly calls the Antoine fallback; a genuine
        library exception is re-raised as a labelled ``RuntimeError``.
        """
        try:
            liquid_fraction_value = float(liquid_fraction)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                'VapoRock vapor bridge liquid_fraction_invalid: '
                f'{liquid_fraction!r}'
            ) from exc
        if (
            not math.isfinite(liquid_fraction_value)
            or liquid_fraction_value < 0.0
            or liquid_fraction_value > 1.0
        ):
            raise RuntimeError(
                'VapoRock vapor bridge liquid_fraction_invalid: '
                f'{liquid_fraction!r}'
            )
        if liquid_fraction_value == 0.0:
            return {}, 'no_liquid_phase'

        # Composition alphaMELTS actually solved, projected to the
        # VapoRock helper by the canonical oxide names it filters on.
        # wt% are relative masses, so handing them in as composition_kg
        # is identical to real kg (project_melt_to_oxide_projection
        # renormalises to 100%); no separate mol path is needed.
        melt_wt = {
            str(oxide): float(value)
            for oxide, value in (solved_melt_wt_pct or {}).items()
            if self._is_number(value) and float(value) > 0.0
        }
        if not melt_wt:
            raise RuntimeError(
                'VapoRock vapor bridge missing_solved_liquid_composition: '
                'liquid_fraction > 0 but solved_melt_wt_pct is empty; '
                'refusing bulk-composition vapor fallback'
            )

        helper = self._vaporock_helper
        if helper is None or not helper.is_available():
            pressures = self._activities_times_antoine_or_fail(
                T_C,
                dict(activities),
                dict(melt_wt),
                pO2_bar=max(10.0 ** float(fO2_log), 1e-30),
                context='VapoRock helper unavailable',
            )
            return (
                pressures,
                self._antoine_vapor_pressure_source_by_species(
                    'antoine_fallback_from_vaporock',
                    pressures,
                )
                if pressures
                else 'no_volatile_species',
            )

        try:
            result = helper.equilibrate(
                temperature_C=float(T_C),
                composition_kg=melt_wt,
                fO2_log=float(fO2_log),
                pressure_bar=float(pressure_bar),
            )
        except Exception as exc:  # noqa: BLE001 - re-raised labelled below
            raise RuntimeError(
                f'VapoRock vapor bridge failed: {exc}'
            ) from exc

        pressures = dict(result.vapor_pressures_Pa or {})
        if result.status != 'ok' or not pressures:
            detail = '; '.join(result.warnings) if result.warnings else (
                f'status={result.status}, empty vapor_pressures_Pa')
            warnings.warn(
                'VapoRock returned no usable vapor pressures '
                f'({detail}); using activity x Antoine fallback rows '
                '(pure-component only when fit_target=pure_component_psat; '
                'pseudo rows are backsolved VapoRock curve-fits).',
                stacklevel=2,
            )
            pressures = self._activities_times_antoine_or_fail(
                T_C,
                dict(activities),
                dict(melt_wt),
                pO2_bar=max(10.0 ** float(fO2_log), 1e-30),
                context=f'VapoRock status {result.status!r}',
            )
            return (
                pressures,
                self._antoine_vapor_pressure_source_by_species(
                    'antoine_fallback_from_vaporock',
                    pressures,
                )
                if pressures
                else 'no_volatile_species',
            )

        # ALREADY Pa — do not re-scale, do not normalize.
        return pressures, 'vaporock'

    def _activities_times_antoine_or_fail(
        self,
        T_C: float,
        activities: dict,
        comp_wt: dict,
        *,
        pO2_bar: float | None = None,
        context: str,
    ) -> Dict[str, float]:
        table = self._load_vapor_pressure_table()
        if not table:
            raise RuntimeError(
                'AlphaMELTS vapor pressure fallback failed: vapor pressure '
                'data table is empty; refusing empty vapor_pressures_Pa '
                'because it would silently zero evaporation flux'
            )

        pressures = self._activities_times_antoine(
            T_C,
            activities,
            comp_wt,
            pO2_bar=pO2_bar,
        )
        if pressures:
            return pressures

        if not self._melt_has_antoine_vapor_precursor(comp_wt, table):
            return {}

        if not activities:
            reason = 'no activity coefficients were available'
        else:
            reason = 'no activity coefficients matched vapor-pressure species'
        raise RuntimeError(
            'AlphaMELTS vapor pressure fallback failed: '
            f'{reason} for volatile-bearing melt ({context}); refusing '
            'empty vapor_pressures_Pa because it would silently zero '
            'evaporation flux'
        )

    def _antoine_vapor_pressure_source_by_species(
        self,
        base_source: str,
        pressures: Mapping[str, float],
    ) -> Dict[str, str]:
        from engines.builtin.vapor_pressure import vapor_pressure_source_label

        table = self._load_vapor_pressure_table()
        return {
            str(species): vapor_pressure_source_label(
                base_source,
                table.get(str(species), {}),
            )
            for species in pressures
        }

    @staticmethod
    def _vapor_pressure_source_map(
        pressures: Mapping[str, float],
        source: str | Mapping[str, str],
    ) -> Dict[str, str]:
        if isinstance(source, Mapping):
            return {
                str(species): str(
                    source.get(str(species))
                    or source.get(species)
                    or 'unknown_vapor_pressure_source'
                )
                for species in pressures
            }
        return {
            str(species): str(source)
            for species in pressures
        }

    @staticmethod
    def _vapor_pressure_zero_diagnostics(
        diagnostics: Optional[Mapping[str, object]],
        pressures: Mapping[str, float],
        source: str | Mapping[str, str],
    ) -> dict[str, object]:
        payload = dict(diagnostics or {})
        if not pressures and source == 'no_volatile_species':
            payload.setdefault('vapor_pressure_zero_reason', 'no_volatile_species')
        return payload

    @staticmethod
    def _vapor_pressure_degraded_to_antoine(
        source: str | Mapping[str, str],
    ) -> bool:
        labels = source.values() if isinstance(source, Mapping) else (source,)
        return any(
            str(label).startswith('antoine_fallback_from_vaporock')
            for label in labels
        )

    def _vapor_pressure_diagnostics(
        self,
        diagnostics: Optional[Mapping[str, object]],
        pressures: Mapping[str, float],
        source: str | Mapping[str, str],
    ) -> dict[str, object]:
        payload = self._vapor_pressure_zero_diagnostics(
            diagnostics,
            pressures,
            source,
        )
        if self._vapor_pressure_degraded_to_antoine(source):
            # Facet-scoped honesty: the equilibrium solve answered the
            # requested conditions, so parent status/backend_status stay
            # intact; only the vapor-pressure facet lost VapoRock authority.
            # (magemin's out_of_domain precedent is for clamped operating
            # points, where the solve itself missed the request.)
            payload['vapor_pressure_backend_status'] = 'fallback'
            payload['vapor_pressure_backend_status_reason'] = (
                'vaporock_to_antoine_fallback'
            )
            payload['vapor_pressure_fallback_source'] = (
                'antoine_fallback_from_vaporock'
            )
            payload['authoritative_for_requested_vapor_pressure'] = False
        elif not self._vaporock_available:
            # Mark 'not_attempted' whenever VapoRock was never available — INCLUDING the
            # empty-pressures case. The complementary zero_reason (from
            # _vapor_pressure_zero_diagnostics above) explains WHY pressures are empty; this
            # facet backend_status separately reports the VapoRock backend was never tried, so
            # an operator can distinguish "unavailable (never attempted)" from authoritative.
            payload['vapor_pressure_backend_status'] = 'not_attempted'
            payload['vapor_pressure_backend_status_reason'] = (
                'vaporock_unavailable_not_attempted'
            )
        return payload

    def _activities_times_antoine(self, T_C: float,
                                    activities: dict,
                                    _comp_wt: dict,
                                    *,
                                    pO2_bar: float | None = None) -> Dict[str, float]:
        """
        Compute vapor pressures as thermodynamic activity x Antoine-row P(T).

        Fallback when VapoRock is not available. Uses Antoine equation rows
        from vapor_pressures.yaml. Only fit_target=pure_component_psat rows
        are pure-component / first-principles. Rows with
        fit_target=pseudo_psat_backsolved_from_vaporock are backsolved
        VapoRock fallbacks (curve-fits), with residual_dex/confidence_tier
        metadata. Activities must already be pure-endmember-referenced
        values from
        ``activity_from_chem_potential(mu, mu0, T_K)``.

        P_i = a_i x P_reference_i(T)

        If activities are unavailable, no pressure is emitted.
        """
        if not activities:
            return {}
        table = self._load_vapor_pressure_table()
        if not table:
            return {}
        T_K = float(T_C) + 273.15
        pressures: Dict[str, float] = {}
        from engines.builtin.vapor_pressure import (
            COEFF_BLOCK_ANTOINE,
            FIT_TARGET_STANDARD_REACTION,
            vapor_pressure_antoine_coefficients,
            warn_pseudo_vapor_pressure_fallback,
        )

        for species, spec in table.items():
            raw_activity = self._activity_for_vapor_species(species, activities)
            if raw_activity is None:
                continue
            if not self._is_number(raw_activity):
                continue
            coeffs, coefficient_block = vapor_pressure_antoine_coefficients(
                spec,
                temperature_K=T_K,
            )
            if not all(key in coeffs for key in ('A', 'B', 'C')):
                continue
            activity_i = float(raw_activity)
            p_reference_i = 10.0 ** (
                float(coeffs['A']) - float(coeffs['B']) / (T_K + float(coeffs['C']))
            )
            p_i = activity_i * p_reference_i
            if str(spec.get('fit_target', '') or '') == FIT_TARGET_STANDARD_REACTION:
                activity_exponent = float(
                    spec.get('oxide_activity_exponent', 1.0) or 1.0
                )
                p_i = (max(activity_i, 0.0) ** activity_exponent) * p_reference_i
                pO2_exponent = float(spec.get('pO2_exponent', 0.0) or 0.0)
                if pO2_exponent:
                    if pO2_bar is None:
                        raise RuntimeError(
                            'AlphaMELTS Antoine fallback cannot evaluate '
                            f'{species} standard_reaction_term without pO2_bar; '
                            'refusing activity-only vapor pressure'
                        )
                    pO2_reference_bar = max(
                        1e-30,
                        float(spec.get('pO2_reference_bar', 1.0) or 1.0),
                    )
                    pO2_value = max(float(pO2_bar), 1e-30)
                    p_i *= (pO2_value / pO2_reference_bar) ** pO2_exponent
            if p_i > 0.0 and math.isfinite(p_i):
                pressures[str(species)] = p_i
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        str(species),
                        spec,
                        self._pseudo_vapor_pressure_warning_seen,
                        stacklevel=3,
                    )
        return pressures

    def _melt_has_antoine_vapor_precursor(
        self,
        comp_wt: Mapping[str, float],
        table: Mapping[str, Mapping[str, object]],
    ) -> bool:
        precursor_keys = set()
        for species in table:
            precursor_keys.update(
                ACTIVITY_KEYS_BY_VAPOR_SPECIES.get(str(species), (str(species),))
            )
        for species, wt_pct in (comp_wt or {}).items():
            if (
                str(species) in precursor_keys
                and self._is_number(wt_pct)
                and float(wt_pct) > 0.0
            ):
                return True
        return False

    @staticmethod
    def _activity_for_vapor_species(species: str, activities: dict) -> Optional[float]:
        # ThermoEngine reports liquid oxide/endmember activities, not vapor
        # species activities. Direct vapor keys win; mapped oxide/endmember
        # keys are proxy activities for this non-authoritative fallback.
        for key in ACTIVITY_KEYS_BY_VAPOR_SPECIES.get(str(species), (str(species),)):
            if key in activities:
                return activities[key]
        return None

    def _load_vapor_pressure_table(self) -> dict:
        if self._vapor_pressure_table is not None:
            return self._vapor_pressure_table
        import yaml

        path = (
            Path(__file__).parent.parent.parent
            / 'data'
            / 'vapor_pressures.yaml'
        )
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        table = {}
        for group in ('metals', 'oxide_vapors'):
            for species, spec in (data.get(group) or {}).items():
                table[str(species)] = dict(spec or {})
        self._vapor_pressure_table = table
        return table

    def _oxide_mole_fractions(self, comp_wt: Mapping[str, float]) -> Dict[str, float]:
        moles: Dict[str, float] = {}
        for oxide, wt in comp_wt.items():
            if not self._is_number(wt) or float(wt) <= 0.0:
                continue
            try:
                molar_mass = resolve_species_formula(
                    oxide, None).molar_mass_kg_per_mol()
            except Exception:
                continue
            if molar_mass > 0.0:
                moles[str(oxide)] = float(wt) / molar_mass
        total_moles = sum(moles.values())
        if total_moles <= 0.0:
            return {}
        return {
            oxide: mol / total_moles
            for oxide, mol in moles.items()
        }


class AlphaMELTSBackend(_MELTSBackendSupport):
    """alphaMELTS subprocess/PetThermoTools backend."""

    backend_name = 'alphamelts'
