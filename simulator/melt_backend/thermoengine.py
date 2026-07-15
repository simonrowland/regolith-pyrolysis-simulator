"""Standalone ThermoEngine MELTS backend."""

from __future__ import annotations

from typing import List, Mapping, Optional
import warnings

from engines.alphamelts.thermoengine import ThermoEngineTransport
from simulator.melt_backend.alphamelts import (
    _MELTSBackendSupport,
    activity_from_chem_potential,
)
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.vaporock import VapoRockBackend


THERMOENGINE_MIN_PRESSURE_BAR = 1.0e-6


class ThermoEngineBackend(_MELTSBackendSupport):
    """First-class MeltBackend using ENKI ThermoEngine's spawned worker."""

    backend_name = 'thermoengine'
    supports_intrinsic_fO2 = True

    def __init__(self) -> None:
        super().__init__()
        self._thermoengine_transport: Optional[ThermoEngineTransport] = None
        self._thermoengine_import_error: Optional[BaseException] = None
        self._health_timeout_s = 8.0

    @property
    def transport(self) -> Optional[ThermoEngineTransport]:
        return self._thermoengine_transport

    def initialize(self, config: dict) -> bool:
        config = self._thermoengine_config(config)
        requested_mode = str(config.get('mode') or 'thermoengine').strip().lower()
        if requested_mode not in {'auto', 'thermoengine'}:
            raise ValueError(
                f'unsupported ThermoEngine mode: {config.get("mode")}'
            )

        self.close()
        self._mode = None
        self._engine_version = None
        self._thermoengine_transport = None
        self._thermoengine_import_error = None
        self._model = str(config.get('model', self._model))
        self._health_timeout_s = float(
            config.get('thermoengine_health_timeout_s', 8.0)
        )
        self._initialize_vaporock_delegate()

        try:
            transport = ThermoEngineTransport(
                model_name=self._model,
                activity_converter=activity_from_chem_potential,
                equilibrate_timeout_s=float(
                    config.get('thermoengine_equilibrate_timeout_s', 60.0)
                ),
            )
            self._thermoengine_transport = transport
            transport.initialize()
            health_check = getattr(transport, 'health_check', None)
            if callable(health_check):
                ok, reason = health_check(timeout_s=self._health_timeout_s)
                if not ok:
                    raise ImportError(reason)
            self._engine_version = transport.engine_version
            self._mode = 'thermoengine'
            return True
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            self._thermoengine_import_error = exc
            self._close_after_failure(exc)
            raise ImportError(
                f'ThermoEngine transport unavailable: {exc}'
            ) from exc

    @staticmethod
    def _thermoengine_config(config: dict) -> dict:
        if not isinstance(config, Mapping):
            return {}
        merged = dict(config)
        for key in ('alphamelts', 'thermoengine'):
            nested = config.get(key)
            if isinstance(nested, Mapping):
                merged.update(nested)
        return merged

    def _initialize_vaporock_delegate(self) -> None:
        if self._vaporock_helper is None:
            self._vaporock_helper = VapoRockBackend()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self._vaporock_helper.initialize({})
        self._vaporock_available = self._vaporock_helper.is_available()
        if not self._vaporock_available and not self._vaporock_unavailable_logged:
            warnings.warn(
                'VapoRock vapor-melt library unavailable; ThermoEngine vapor '
                'pressures fall back to activity x Antoine rows; '
                'vapor_pressures_source distinguishes pure-component '
                'first-principles rows from backsolved VapoRock curve-fit rows.',
                stacklevel=2,
            )
            self._vaporock_unavailable_logged = True

    def close(self) -> None:
        transport = self._thermoengine_transport
        self._thermoengine_transport = None
        self._mode = None
        if transport is not None:
            transport.close()

    def _close_after_failure(self, primary_error: BaseException) -> None:
        try:
            self.close()
        except Exception as cleanup_error:  # noqa: BLE001 - preserve primary
            primary_error.add_note(
                f'ThermoEngine cleanup also failed: {cleanup_error}'
            )

    def is_available(self) -> bool:
        return self._thermoengine_transport is not None and self._mode == 'thermoengine'

    def get_engine_version(self) -> str:
        if self._thermoengine_transport is not None:
            self._engine_version = getattr(
                self._thermoengine_transport,
                'engine_version',
                self._engine_version,
            )
        return self._engine_version or 'unavailable'

    def health_check(self, timeout_s: float | None = None) -> tuple[bool, str]:
        if self._thermoengine_transport is None:
            return False, 'ThermoEngine transport not initialized'
        return self._thermoengine_transport.health_check(
            timeout_s=self._health_timeout_s if timeout_s is None else float(timeout_s)
        )

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Optional[dict[str, float]] = None,
        fO2_log: Optional[float] = None,
        pressure_bar: float = 1.0e-6,
        *,
        composition_mol: Optional[dict[str, float]] = None,
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        species_formula_registry: Optional[Mapping[str, object]] = None,
    ) -> EquilibriumResult:
        return super().equilibrate(
            temperature_C=temperature_C,
            composition_kg=composition_kg,
            fO2_log=fO2_log,
            pressure_bar=pressure_bar,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
            species_formula_registry=species_formula_registry,
        )

    def _equilibrate_prepared(
        self,
        *,
        temperature_C: float,
        comp_wt: Mapping[str, float],
        fO2_log: Optional[float],
        pressure_bar: float,
        warnings: List[str],
        **_unused: object,
    ) -> EquilibriumResult:
        return self._equilibrate_thermoengine(
            temperature_C,
            comp_wt,
            fO2_log,
            pressure_bar,
            warnings,
        )

    def _equilibrate_thermoengine(
        self,
        temperature_C: float,
        comp_wt: Mapping[str, float],
        fO2_log: Optional[float],
        pressure_bar: float,
        warnings: Optional[List[str]] = None,
    ) -> EquilibriumResult:
        """Use ENKI ThermoEngine MELTS for equilibrium + first-class mu."""
        if self._thermoengine_transport is None:
            self._mode = None
            raise ImportError('ThermoEngine transport not initialized')
        try:
            solved_pressure_bar = max(
                float(pressure_bar), THERMOENGINE_MIN_PRESSURE_BAR
            )
            clamp_diagnostics, result_warnings = (
                self._clamped_operating_point_context(
                    requested_temperature_C=temperature_C,
                    requested_pressure_bar=pressure_bar,
                    solved_temperature_C=temperature_C,
                    solved_pressure_bar=solved_pressure_bar,
                    transport='thermoengine',
                    warnings=warnings,
                )
            )
            payload = self._thermoengine_transport.equilibrate(
                temperature_C=temperature_C,
                pressure_bar=solved_pressure_bar,
                comp_wt=comp_wt,
                fO2_log=fO2_log,
                warnings=tuple(result_warnings),
            )
            if payload.solved_fO2_log is None:
                raise RuntimeError(
                    'ThermoEngine equilibrium did not report a solved fO2'
                )
            solved_fO2_log = float(payload.solved_fO2_log)
            if fO2_log is None:
                fO2_transport = 'thermoengine_intrinsic_closed'
            else:
                echo_delta = abs(solved_fO2_log - float(fO2_log))
                if echo_delta >= 1.0e-3:
                    raise RuntimeError(
                        'ThermoEngine absolute fO2 echo outside tolerance: '
                        f'requested={float(fO2_log):g}, '
                        f'solved={solved_fO2_log:g}'
                    )
                fO2_transport = 'thermoengine_oxygen_root'
                clamp_diagnostics['requested_fO2_log'] = float(fO2_log)
                clamp_diagnostics['fO2_echo_abs_delta'] = echo_delta
            clamp_diagnostics.update({
                'solved_fO2_log': solved_fO2_log,
                'fO2_transport': fO2_transport,
                'thermoengine_default_phase_universe_size': (
                    payload.phase_universe_size
                ),
                'thermoengine_fO2_solve_count': payload.fO2_solve_count,
                'authoritative_for_requested_conditions': not bool(
                    clamp_diagnostics.get('operating_point_clamped')
                ),
                'authoritative_for_solved_conditions': True,
            })
            status = 'ok' if payload.phases_present else 'not_converged'
            if self._vaporock_available:
                vapor_pressures, vapor_pressure_source = (
                    self._vapor_pressures_via_vaporock_or_antoine(
                        T_C=temperature_C,
                        solved_melt_wt_pct=payload.liquid_composition_wt_pct,
                        liquid_fraction=payload.liquid_fraction,
                        fO2_log=solved_fO2_log,
                        pressure_bar=solved_pressure_bar,
                        activities=payload.activity_coefficients,
                    )
                )
            else:
                vapor_pressures = self._activities_times_antoine_or_fail(
                    temperature_C,
                    payload.activity_coefficients,
                    comp_wt,
                    context='ThermoEngine VapoRock fallback unavailable',
                )
                vapor_pressure_source = (
                    self._antoine_vapor_pressure_source_by_species(
                        'thermoengine', vapor_pressures
                    )
                    if vapor_pressures
                    else 'no_volatile_species'
                )
            eq = self._emit_equilibrium_result(
                temperature_C=temperature_C,
                pressure_bar=solved_pressure_bar,
                fO2_log=solved_fO2_log,
                phases_present=list(payload.phases_present),
                phase_masses_kg=payload.phase_masses_kg,
                liquid_fraction=payload.liquid_fraction,
                liquid_composition_wt_pct=payload.liquid_composition_wt_pct,
                phase_compositions=payload.phase_compositions,
                phase_thermo=payload.phase_thermo,
                chem_potentials=payload.chem_potentials,
                phase_affinities=payload.phase_affinities,
                activity_coefficients=payload.activity_coefficients,
                vapor_pressures_Pa=vapor_pressures,
                vapor_pressures_source=self._vapor_pressure_source_map(
                    vapor_pressures, vapor_pressure_source
                ),
                warnings=list(payload.warnings),
                status=status,
                diagnostics=self._vapor_pressure_diagnostics(
                    clamp_diagnostics,
                    vapor_pressures,
                    vapor_pressure_source,
                ),
            )
            eq.fe_redox_split = dict(payload.fe_redox_split)
            return self._fail_closed_on_clamped_operating_point(eq)
        except ImportError as exc:
            self._close_after_failure(exc)
            raise
        except Exception as exc:
            self._close_after_failure(exc)
            raise RuntimeError(
                f'ThermoEngine equilibrium failed: {exc}'
            ) from exc


__all__ = ['ThermoEngineBackend']
