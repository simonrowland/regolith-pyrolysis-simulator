"""Evaporation and condensation-routing helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from simulator.accounting import AccountingError, resolve_species_formula
from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ProviderUnavailableError,
)
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX,
    STOICH_RATIOS,
    EvaporationFlux,
    clamp_stir_factor,
)


# NOTE: the evaporation alpha default lives at engines/builtin/evaporation_flux.py
# (_DEFAULT_EVAPORATION_ALPHA), which is the authoritative flux path; the former
# duplicate here was dead (unused, not imported) and was removed (SC-09 / BUG-051).
_EVAPORATION_ALPHA_GROUPS = ("metals", "oxide_vapors")
_FREEZE_GATE_ACCOUNT = 'process.cleaned_melt'
_FREEZE_GATE_EPSILON = 1.0e-12
_FREEZE_GATE_FRACTION_QUANTUM = 0.001
_FREEZE_GATE_PRESSURE_BAR_QUANTUM = 0.01
_FREEZE_GATE_FO2_LOG_QUANTUM = 0.1
_FREEZE_GATE_TRACE_FRACTION_CUTOFF = 0.001
_FREEZE_GATE_COMPOSITION_SPECIES = frozenset((
    'SiO2',
    'Al2O3',
    'FeO',
    'MgO',
    'CaO',
    'Na2O',
    'K2O',
    'TiO2',
    'Cr2O3',
    'MnO',
    'P2O5',
))


def _load_evaporation_alpha_by_species(vapor_pressure_data: dict) -> dict[str, float]:
    """Load per-species Hertz-Knudsen alpha values from vapor pressure data."""

    alpha_by_species: dict[str, float] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if not isinstance(alpha_data, dict) or "value" not in alpha_data:
                continue
            alpha_by_species[species] = float(alpha_data["value"])
    return alpha_by_species


def _load_evaporation_alpha_envelope_by_species(
    vapor_pressure_data: dict,
) -> dict[str, tuple[float, float]]:
    """Load per-species alpha envelopes for flux uncertainty diagnostics."""

    envelope_by_species: dict[str, tuple[float, float]] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if not isinstance(alpha_data, dict):
                continue
            envelope = alpha_data.get("envelope") or ()
            if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
                continue
            envelope_by_species[species] = (
                float(envelope[0]),
                float(envelope[1]),
            )
    return envelope_by_species


class EvaporationMixin:
    def _calculate_evaporation(self, equilibrium) -> EvaporationFlux:
        """
        Calculate evaporation flux using a series-resistance source.

        For each volatile species, the mass flux from the melt surface is:

            J_i = (P_eq_i - P_bulk_i) /
                  (1/(alpha_i*k_HK_i) + R_gas_i(Kn) + R_melt_i(stir))

        where:
            alpha_i     = intrinsic Hertz-Knudsen evaporation coefficient
            k_HK_i      = sqrt(M_i / (2*pi*R*T))
            R_gas_i     = gas-side Fuchs-Sutugin/Sherwood resistance
            R_melt_i    = melt-side surface-renewal resistance
            A_surface   = melt surface area (m²)
            P_eq_i      = effective equilibrium pressure from VAPOR_PRESSURE (Pa)
            P_bulk_i    = partial pressure above the melt (Pa)
            M_i         = molar mass (kg/mol)
            R           = gas constant (J/mol·K)
            T           = temperature (K)

        The SiO suppression under pO₂ control is handled automatically:
        when pO₂ is elevated, the equilibrium vapor pressure of SiO
        drops by the factor √(pO₂), reducing the driving force.

        EVAPORATION_FLUX intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), second flip. The
        BuiltinEvaporationFluxProvider is the authoritative source for
        per-species kg/hr fluxes; this method builds the auxiliary maps
        the provider needs (vapor pressures, overhead partials, stoich,
        available oxide masses, molar masses, melt geometry) and routes
        the loop to ``kernel.dispatch(EVAPORATION_FLUX, ...)``. The
        legacy Hertz-Knudsen loop body lives inside the provider now;
        this method owns the precompute + the result projection. Shadow
        parity is the parametrised test in
        ``tests/chemistry/test_builtin_evaporation_flux_provider.py``;
        per the goal spec, the shadow comparator was removed at flip
        time (comparing against the same source is moot).

        Returns:
            EvaporationFlux with species_kg_hr dict
        """
        T_K = self.melt.temperature_C + 273.15
        flux = EvaporationFlux()

        if T_K < 400:  # Below any significant evaporation
            return flux

        vapor_pressures = dict(equilibrium.vapor_pressures_Pa or {})
        if not vapor_pressures:
            return flux
        vapor_pressure_diagnostic = dict(
            getattr(self, '_last_vapor_pressure_diagnostic', {}) or {}
        )

        # Precompute the auxiliary maps the provider consumes via
        # control_inputs. This keeps the provider stateless: every
        # piece of caller-owned state (yaml lookups, stoich validation,
        # available-mass cap, overhead backpressure) arrives in the
        # request, so the provider holds no simulator references.
        (
            molar_masses_kg_mol,
            stoich_by_species,
            available_oxide_kg,
        ) = self._build_evaporation_aux_maps(vapor_pressures)

        # Overhead backpressure (Pa)                       [LOOP-1]
        # Uses the previous hour's overhead partial pressures as
        # backpressure. Gas pO2 has already been applied once upstream in
        # the equilibrium vapor pressures consumed here.
        overhead_partials_Pa = {
            species: self.overhead.composition.get(species, 0.0) * 100.0
            for species in vapor_pressures
        }

        # F-B1: EVAPORATION_FLUX is read-only -- no commit_batch follows.
        # The dispatch-only helper centralises melt-derived T/P plumbing
        # so this call site stays in lock-step with the rest of the
        # simulator's kernel callers.
        kernel_config = dict(
            getattr(self, 'setpoints', {}).get('chemistry_kernel', {}) or {}
        )
        series_resistance_config = dict(
            kernel_config.get('evaporation_series_resistance', {}) or {}
        )
        carrier_resolver = getattr(self, '_resolve_condensation_carrier_gas', None)
        carrier_gas = (
            carrier_resolver()
            if callable(carrier_resolver)
            else 'N2'
        )
        gas_temperature_K = float(
            getattr(self.overhead, 'headspace_temperature_K', 0.0) or T_K
        )
        kernel_result = self._dispatch_only(
            ChemistryIntent.EVAPORATION_FLUX,
            control_inputs={
                'vapor_pressures_Pa': vapor_pressures,
                'vapor_pressures_source': dict(
                    getattr(equilibrium, 'vapor_pressures_source', {}) or {}
                ),
                'vapor_pressure_numerator_provenance': dict(
                    vapor_pressure_diagnostic.get(
                        'vapor_pressure_numerator_provenance'
                    )
                    or {}
                ),
                'vapor_pressure_activities': dict(
                    getattr(equilibrium, 'activity_coefficients', {}) or {}
                ),
                'pO2_bar': vapor_pressure_diagnostic.get('pO2_bar'),
                'overhead_partials_Pa': overhead_partials_Pa,
                'molar_mass_kg_mol': molar_masses_kg_mol,
                'stoich_by_species': stoich_by_species,
                'available_oxide_kg': available_oxide_kg,
                'melt_surface_area_m2': float(self.melt.melt_surface_area_m2),
                'stir_factor': {
                    'axial': clamp_stir_factor(self.melt.stir_state.axial),
                    'radial': clamp_stir_factor(self.melt.stir_state.radial),
                },
                'pipe_diameter_m': float(
                    getattr(self.overhead_model, 'pipe_diameter_m', 0.12)
                ),
                'overhead_pressure_pa': float(
                    getattr(self.overhead, 'pressure_mbar', 0.0) or 0.0
                ) * 100.0,
                'gas_temperature_K': gas_temperature_K,
                'carrier_gas': carrier_gas,
                'evaporation_series_resistance': series_resistance_config,
                'alpha': _load_evaporation_alpha_by_species(
                    self.vapor_pressures
                ),
                'alpha_envelope': _load_evaporation_alpha_envelope_by_species(
                    self.vapor_pressures
                ),
                'allow_unmeasured_alpha_fallback': bool(
                    kernel_config.get('allow_unmeasured_alpha_fallback', False)
                ),
            },
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        self._last_evaporation_flux_diagnostic = diagnostic
        if str(kernel_result.status) != 'ok' and 'missing_alpha' in diagnostic:
            missing = ', '.join(sorted(diagnostic['missing_alpha']))
            raise ProviderUnavailableError(
                "missing evaporation_alpha for sampled species: "
                f"{missing}; set chemistry_kernel.allow_unmeasured_alpha_fallback "
                "for alpha=1.0 prototype fallback"
            )
        flux_kg_hr = diagnostic.get('evaporation_flux_kg_hr') or {}
        liquid_fraction_factor = 1.0
        if flux_kg_hr and self._freeze_gate_enabled():
            liquid_fraction_factor = self._freeze_gate_liquid_fraction_factor()
            if liquid_fraction_factor <= _FREEZE_GATE_EPSILON:
                flux.update_totals()
                return flux
        for species, rate_kg_hr in flux_kg_hr.items():
            gated_rate_kg_hr = float(rate_kg_hr) * liquid_fraction_factor
            if gated_rate_kg_hr > 1e-12:
                flux.species_kg_hr[species] = gated_rate_kg_hr

        flux.update_totals()
        return flux

    def _freeze_gate_liquid_fraction_factor(self) -> float:
        curve = self._freeze_gate_curve()
        factor = self._interpolate_freeze_gate_curve(
            curve,
            float(self.melt.temperature_C),
        )
        self._last_freeze_gate_diagnostic = {
            'enabled': True,
            'source': curve['source'],
            'solidus_T_C': curve['solidus_T_C'],
            'liquidus_T_C': curve['liquidus_T_C'],
            'liquid_fraction': factor,
        }
        return factor

    def _freeze_gate_curve(self) -> dict[str, Any]:
        pressure_bar = float(self.melt.p_total_mbar) / 1000.0
        fO2_log = float(self._compute_intrinsic_melt_fO2())
        key = self._freeze_gate_cache_key(
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        store_getter = getattr(self, '_pt0_store', None)
        store = store_getter() if callable(store_getter) else None
        if store is not None and getattr(store, 'replay_enabled', False):
            return store.replay_gate_curve(self, fO2_log=fO2_log)
        cache = getattr(self, '_freeze_gate_liquid_fraction_cache', None)
        if cache and cache.get('key') == key:
            curve = dict(cache['curve'])
            if store is not None and getattr(store, 'capture_enabled', False):
                store.capture_gate_curve(self, fO2_log=fO2_log, curve=curve)
            return curve

        reasons: list[str] = []
        curve = self._freeze_gate_curve_from_gate_dispatch(
            reasons,
            fO2_log=fO2_log,
        )
        if curve is None:
            curve = self._freeze_gate_curve_from_backend_liquidus(
                reasons,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )
        if curve is None:
            curve = self._freeze_gate_curve_from_kernel_liquidus(
                reasons,
                fO2_log=fO2_log,
            )
        if curve is None:
            detail = '; '.join(reasons[-6:]) or 'no liquidus engine available'
            raise RuntimeError(
                'freeze_gate.enabled requires a liquid_fraction(T) source; '
                'no liquidus engine produced usable solidus/liquidus bounds. '
                f'{detail}'
            )

        self._freeze_gate_liquid_fraction_cache = {
            'key': key,
            'curve': dict(curve),
        }
        self._freeze_gate_cache_rebuild_count = (
            int(getattr(self, '_freeze_gate_cache_rebuild_count', 0)) + 1
        )
        if store is not None and getattr(store, 'capture_enabled', False):
            store.capture_gate_curve(self, fO2_log=fO2_log, curve=curve)
        return curve

    def _freeze_gate_cache_key(
        self,
        *,
        pressure_bar: float,
        fO2_log: float,
    ) -> tuple:
        cleaned_mol = self.atom_ledger.mol_by_account(_FREEZE_GATE_ACCOUNT)
        relevant_mol: dict[str, float] = {}
        for species, mol in cleaned_mol.items():
            species_key = str(species)
            if species_key not in _FREEZE_GATE_COMPOSITION_SPECIES:
                continue
            mol_value = float(mol)
            if mol_value > _FREEZE_GATE_EPSILON:
                relevant_mol[species_key] = mol_value

        # Liquidus is stable to small per-tick evaporation drift; 0.1 mol-%
        # bins (0.001 fraction quantum) sit comfortably above mole-fraction
        # float-arithmetic jitter while still well inside the L1 finder ±30 K
        # tolerance, and still rebuild for campaign-scale major-oxide
        # composition shifts.
        composition_key = []
        total_mol = sum(relevant_mol.values())
        if total_mol > _FREEZE_GATE_EPSILON:
            for species, mol in relevant_mol.items():
                fraction = mol / total_mol
                if fraction < _FREEZE_GATE_TRACE_FRACTION_CUTOFF:
                    continue
                quantized_fraction = (
                    round(fraction / _FREEZE_GATE_FRACTION_QUANTUM)
                    * _FREEZE_GATE_FRACTION_QUANTUM
                )
                if quantized_fraction <= 0.0:
                    continue
                composition_key.append((species, round(quantized_fraction, 6)))
        pressure_bucket = (
            round(float(pressure_bar) / _FREEZE_GATE_PRESSURE_BAR_QUANTUM)
            * _FREEZE_GATE_PRESSURE_BAR_QUANTUM
        )
        fO2_bucket = (
            round(float(fO2_log) / _FREEZE_GATE_FO2_LOG_QUANTUM)
            * _FREEZE_GATE_FO2_LOG_QUANTUM
        )
        # Pressure is bucketed at 0.01 bar and fO2 at 0.1 log unit: coarse
        # enough to absorb per-tick float noise, fine enough to split
        # overhead-pressure and campaign/redox control changes.
        return (
            'oxide_mol_fraction_p_fO2_v2',
            round(pressure_bucket, 6),
            round(fO2_bucket, 6),
            tuple(sorted(composition_key)),
        )

    def _freeze_gate_curve_from_gate_dispatch(
        self,
        reasons: list[str],
        *,
        fO2_log: float,
    ) -> dict[str, Any] | None:
        register_gate_providers = getattr(
            self,
            '_register_freeze_gate_liquid_fraction_providers',
            None,
        )
        if callable(register_gate_providers):
            try:
                register_gate_providers()
            except Exception as exc:  # noqa: BLE001 - optional provider boundary
                reasons.append(f'gate provider registration failed: {exc}')
        try:
            result = self._dispatch_only(
                ChemistryIntent.GATE_LIQUID_FRACTION,
                control_inputs={},
                fO2_log=fO2_log,
                fe_redox_policy='intrinsic',
            )
        except ProviderUnavailableError as exc:
            reasons.append(f'gate liquid fraction unavailable: {exc}')
            return None

        diagnostic = dict(getattr(result, 'diagnostic', None) or {})
        status = str(
            getattr(result, 'status', None)
            or diagnostic.get('backend_status')
            or 'unavailable'
        )
        path = tuple(diagnostic.get('liquid_fraction_path') or ())
        source = 'gate_liquid_fraction'
        fallback_provider = diagnostic.get('kernel_fallback_used')
        if fallback_provider:
            source = f'gate_liquid_fraction:fallback:{fallback_provider}'
        if status != 'ok':
            reasons.append(
                'gate liquid fraction unavailable: '
                f'status={status}'
            )
            return None
        if path:
            curve = self._freeze_gate_curve_from_path(
                path,
                solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
                liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
                source=source,
            )
            if curve is not None:
                return curve
            reasons.append('gate liquid fraction table invalid')
        return self._freeze_gate_curve_from_bounds(
            solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
            liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
            source=source,
            reasons=reasons,
        )

    def _freeze_gate_curve_from_backend_liquidus(
        self,
        reasons: list[str],
        *,
        pressure_bar: float,
        fO2_log: float,
    ) -> dict[str, Any] | None:
        finder = getattr(self.backend, 'find_liquidus_solidus', None)
        if not callable(finder):
            reasons.append('backend liquidus finder unavailable')
            return None
        try:
            result = finder(
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                composition_mol_by_account={
                    _FREEZE_GATE_ACCOUNT: self.atom_ledger.mol_by_account(
                        _FREEZE_GATE_ACCOUNT
                    )
                },
                species_formula_registry=dict(
                    getattr(self, 'species_formula_registry', {}) or {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            reasons.append(f'backend liquidus finder failed: {exc}')
            return None
        return self._freeze_gate_curve_from_liquidus_result(
            result,
            source='liquidus_solidus:backend',
            reasons=reasons,
        )

    def _freeze_gate_curve_from_kernel_liquidus(
        self,
        reasons: list[str],
        *,
        fO2_log: float,
        temperature_C: float | None = None,
        pressure_bar: float | None = None,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        allow_parametric: bool = False,
    ) -> dict[str, Any] | None:
        composition_derived = bool(composition_mol_by_account)
        try:
            if composition_derived:
                result = self._require_chem_kernel().dispatch(
                    ChemistryIntent.SILICATE_LIQUIDUS,
                    temperature_C=(
                        float(self.melt.temperature_C)
                        if temperature_C is None
                        else float(temperature_C)
                    ),
                    pressure_bar=(
                        float(self.melt.p_total_mbar) / 1000.0
                        if pressure_bar is None
                        else float(pressure_bar)
                    ),
                    fO2_log=fO2_log,
                    fe_redox_policy='intrinsic',
                    control_inputs={
                        'composition_source': 'out_of_domain_crash_point',
                    },
                    account_mol_overrides=composition_mol_by_account,
                )
            else:
                result = self._dispatch_only(
                    ChemistryIntent.SILICATE_LIQUIDUS,
                    control_inputs={},
                    fO2_log=fO2_log,
                    fe_redox_policy='intrinsic',
                )
        except ProviderUnavailableError as exc:
            reasons.append(f'kernel liquidus unavailable: {exc}')
            if allow_parametric:
                return self._freeze_gate_curve_from_parametric_liquidus(reasons)
            return None

        diagnostic = dict(getattr(result, 'diagnostic', None) or {})
        status = str(
            getattr(result, 'status', None)
            or diagnostic.get('backend_status')
            or 'unavailable'
        )
        if status != 'ok':
            reasons.append(f'kernel liquidus unavailable: status={status}')
            if allow_parametric:
                return self._freeze_gate_curve_from_parametric_liquidus(reasons)
            return None
        curve = self._freeze_gate_curve_from_bounds(
            solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
            liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
            source=(
                'liquidus_solidus:kernel:composition_derived'
                if composition_derived
                else 'liquidus_solidus:kernel'
            ),
            reasons=reasons,
        )
        if curve is not None and composition_derived:
            curve = dict(curve)
            curve['composition_derived'] = True
        return curve

    def _freeze_gate_curve_from_parametric_liquidus(
        self,
        reasons: list[str],
    ) -> dict[str, Any] | None:
        cleaned_mol = self.atom_ledger.mol_by_account(_FREEZE_GATE_ACCOUNT)
        if not any(
            species in _FREEZE_GATE_COMPOSITION_SPECIES and float(mol) > 0.0
            for species, mol in cleaned_mol.items()
        ):
            reasons.append('parametric liquidus unavailable: no cleaned melt')
            return None
        return self._freeze_gate_curve_from_bounds(
            solidus_T_C=900.0,
            liquidus_T_C=1200.0,
            source='liquidus_solidus:kernel:parametric_dry_silicate_lower_bound',
            reasons=reasons,
        )

    def _freeze_gate_curve_from_liquidus_result(
        self,
        result: Any,
        *,
        source: str,
        reasons: list[str],
    ) -> dict[str, Any] | None:
        status = str(getattr(result, 'status', 'unavailable'))
        if status != 'ok':
            diagnostics = getattr(result, 'diagnostics', None)
            if isinstance(diagnostics, Mapping) and diagnostics:
                self._last_backend_diagnostics = dict(diagnostics)
                if status == 'out_of_domain':
                    self._last_out_of_domain_diagnostics = dict(diagnostics)
            warnings = '; '.join(tuple(getattr(result, 'warnings', ()) or ()))
            reasons.append(
                f'{source} unavailable: status={status}'
                + (f', warnings={warnings}' if warnings else '')
            )
            return None

        solidus_T_C = self._optional_float(getattr(result, 'solidus_T_C', None))
        liquidus_T_C = self._optional_float(getattr(result, 'liquidus_T_C', None))
        samples = []
        for sample in tuple(getattr(result, 'samples', ()) or ()):
            samples.append({
                'temperature_C': getattr(sample, 'temperature_C', None),
                'liquid_fraction': getattr(sample, 'frac_M', None),
            })
        if samples:
            curve = self._freeze_gate_curve_from_path(
                samples,
                solidus_T_C=solidus_T_C,
                liquidus_T_C=liquidus_T_C,
                source=source,
            )
            if curve is not None:
                return curve
        return self._freeze_gate_curve_from_bounds(
            solidus_T_C=solidus_T_C,
            liquidus_T_C=liquidus_T_C,
            source=source,
            reasons=reasons,
        )

    def _freeze_gate_curve_from_bounds(
        self,
        *,
        solidus_T_C: float | None,
        liquidus_T_C: float | None,
        source: str,
        reasons: list[str],
    ) -> dict[str, Any] | None:
        if (
            solidus_T_C is None
            or liquidus_T_C is None
            or not solidus_T_C < liquidus_T_C
        ):
            reasons.append(f'{source} invalid bounds')
            return None
        return {
            'source': source,
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
            'path': (
                (solidus_T_C, 0.0),
                (liquidus_T_C, 1.0),
            ),
        }

    def _freeze_gate_curve_from_path(
        self,
        path: tuple,
        *,
        solidus_T_C: float | None,
        liquidus_T_C: float | None,
        source: str,
    ) -> dict[str, Any] | None:
        points: list[tuple[float, float]] = []
        for point in path:
            if isinstance(point, Mapping):
                temperature_C = point.get('temperature_C', point.get('T_C'))
                liquid_fraction = point.get('liquid_fraction')
            else:
                temperature_C = getattr(point, 'temperature_C', None)
                liquid_fraction = getattr(point, 'liquid_fraction', None)
            temperature_C = self._optional_float(temperature_C)
            liquid_fraction = self._optional_float(liquid_fraction)
            if temperature_C is None or liquid_fraction is None:
                continue
            points.append(
                (temperature_C, max(0.0, min(1.0, liquid_fraction)))
            )
        if solidus_T_C is None and points:
            solidus_T_C = min(T for T, _ in points)
        if liquidus_T_C is None and points:
            liquidus_T_C = max(T for T, _ in points)
        if (
            solidus_T_C is None
            or liquidus_T_C is None
            or not solidus_T_C < liquidus_T_C
        ):
            return None
        dedup: dict[float, float] = {}
        for temperature_C, liquid_fraction in sorted(points):
            dedup[temperature_C] = liquid_fraction
        dedup[solidus_T_C] = 0.0
        dedup[liquidus_T_C] = 1.0
        ordered = tuple(sorted(dedup.items()))
        if len(ordered) < 2:
            return None
        previous = ordered[0][1]
        for _, liquid_fraction in ordered[1:]:
            if liquid_fraction + 1.0e-9 < previous:
                return None
            previous = liquid_fraction
        return {
            'source': source,
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
            'path': ordered,
        }

    @staticmethod
    def _interpolate_freeze_gate_curve(
        curve: Mapping[str, Any],
        temperature_C: float,
    ) -> float:
        solidus_T_C = float(curve['solidus_T_C'])
        liquidus_T_C = float(curve['liquidus_T_C'])
        if temperature_C <= solidus_T_C:
            return 0.0
        if temperature_C >= liquidus_T_C:
            return 1.0
        path = tuple(curve.get('path') or ())
        previous_T, previous_fraction = path[0]
        for next_T, next_fraction in path[1:]:
            previous_T = float(previous_T)
            next_T = float(next_T)
            if previous_T <= temperature_C <= next_T:
                span = max(next_T - previous_T, _FREEZE_GATE_EPSILON)
                weight = (temperature_C - previous_T) / span
                return max(
                    0.0,
                    min(
                        1.0,
                        float(previous_fraction)
                        + (float(next_fraction) - float(previous_fraction))
                        * weight,
                    ),
                )
            previous_T, previous_fraction = next_T, next_fraction
        span = max(liquidus_T_C - solidus_T_C, _FREEZE_GATE_EPSILON)
        return max(0.0, min(1.0, (temperature_C - solidus_T_C) / span))

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    def _build_evaporation_aux_maps(
        self, vapor_pressures: dict,
    ) -> tuple[dict, dict, dict]:
        """Precompute per-species auxiliary inputs for EVAPORATION_FLUX.

        Returns ``(molar_masses_kg_mol, stoich_by_species,
        available_oxide_kg)``.

        These three maps are everything the kernel provider needs that
        cannot be derived from the request DTOs alone: the
        ``vapor_pressures.yaml`` payload + the simulator's stoich
        validation (which raises ``AccountingError`` -- a caller-owned
        surface that does NOT belong inside the stateless provider).

        Side effects: This method intentionally invokes
        :meth:`_evaporation_stoich` for each species, which raises
        AccountingError on missing/inconsistent metadata. Preserving
        that error surface in the caller (not the provider) matches the
        legacy behaviour exactly -- the parity tests would otherwise
        observe a different error class.
        """

        molar_masses_kg_mol: dict[str, float] = {}
        stoich_by_species: dict[str, dict] = {}
        available_oxide_kg: dict[str, float] = {}

        metals_data = self.vapor_pressures.get('metals', {}) or {}
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {}) or {}

        cleaned_melt_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt')
        spent_reductant_residue_kg = self.atom_ledger.kg_by_account(
            SPENT_REDUCTANT_RESIDUE_ACCOUNT)
        projection_parity_tolerance_pct = 5.0e-12

        for species in vapor_pressures:
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            M_g_mol = sp_data.get('molar_mass_g_mol')
            if M_g_mol is None:
                M_g_mol = MOLAR_MASS.get(species)
            if M_g_mol is None:
                raise AccountingError(
                    f"vapor species {species!r} requires "
                    "molar_mass_g_mol metadata before evaporation flux "
                    "can be emitted"
                )
            molar_masses_kg_mol[species] = M_g_mol / 1000.0

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                raise AccountingError(
                    f"vapor species {species!r} requires parent_oxide "
                    "metadata before evaporation flux can be emitted"
            )
            stoich = self._evaporation_stoich(species, sp_data)
            stoich_by_species[species] = dict(stoich)
            # Mirrors core.py::_project_cleaned_melt_from_atom_ledger:
            # cleaned_melt + spent_reductant_residue define the projection.
            ledger_oxide_kg = (
                float(cleaned_melt_kg.get(parent_oxide, 0.0))
                + float(spent_reductant_residue_kg.get(parent_oxide, 0.0))
            )
            projection_oxide_kg = float(self.melt.composition_kg.get(
                parent_oxide, 0.0))
            scale_kg = max(abs(ledger_oxide_kg), abs(projection_oxide_kg), 1.0)
            divergence_pct = (
                abs(ledger_oxide_kg - projection_oxide_kg) / scale_kg * 100.0
            )
            if divergence_pct > projection_parity_tolerance_pct:
                raise AccountingError(
                    "cleaned_melt projection stale for parent oxide "
                    f"{parent_oxide!r}: atom_ledger={ledger_oxide_kg:.17g} kg "
                    f"melt_projection={projection_oxide_kg:.17g} kg "
                    f"divergence_pct={divergence_pct:.17g}"
                )
            available_oxide_kg[species] = ledger_oxide_kg

        return molar_masses_kg_mol, stoich_by_species, available_oxide_kg

    def _apply_analytic_evaporation_depletion(
        self, evap_flux: EvaporationFlux, dt_hr: float = 1.0,
    ) -> EvaporationFlux:
        """Apply sub-tick first-order depletion to raw HKL evaporation rates."""
        if dt_hr <= 0.0 or not evap_flux.species_kg_hr:
            return evap_flux

        metals_data = self.vapor_pressures.get('metals', {}) or {}
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {}) or {}
        cleaned_melt_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt')
        parent_groups: dict[str, list[dict]] = defaultdict(list)

        for species in sorted(evap_flux.species_kg_hr):
            raw_rate_kg_hr = float(evap_flux.species_kg_hr.get(species, 0.0))
            if raw_rate_kg_hr <= 1e-12:
                continue
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})
            stoich = self._evaporation_stoich(species, sp_data)
            parent_oxide = stoich['parent_oxide']
            oxide_per_product_kg = float(stoich['oxide_per_product_kg'])
            parent_draw_kg_hr = raw_rate_kg_hr * oxide_per_product_kg
            if parent_draw_kg_hr <= 1e-12:
                continue
            parent_groups[parent_oxide].append({
                'species': species,
                'sp_data': sp_data,
                'stoich': stoich,
                'raw_rate_kg_hr': raw_rate_kg_hr,
                'parent_draw_kg_hr': parent_draw_kg_hr,
            })

        effective_rates: dict[str, float] = {}
        max_fraction = math.nextafter(1.0, 0.0)
        for parent_oxide in sorted(parent_groups):
            entries = parent_groups[parent_oxide]
            available_parent_kg = float(cleaned_melt_kg.get(parent_oxide, 0.0))
            total_parent_draw_kg_hr = sum(
                entry['parent_draw_kg_hr'] for entry in entries)
            if available_parent_kg <= 1e-12 or total_parent_draw_kg_hr <= 1e-12:
                continue
            k_hr = total_parent_draw_kg_hr / available_parent_kg
            depletion_fraction = -math.expm1(-k_hr * dt_hr)
            depletion_fraction = max(
                0.0, min(max_fraction, depletion_fraction))
            parent_draw_kg = available_parent_kg * depletion_fraction
            for entry in entries:
                share = entry['parent_draw_kg_hr'] / total_parent_draw_kg_hr
                product_kg = (
                    parent_draw_kg
                    * share
                    / float(entry['stoich']['oxide_per_product_kg'])
                )
                if product_kg > 1e-12:
                    effective_rates[entry['species']] = product_kg / dt_hr

        self._apply_shared_o2_reactant_depletion(
            effective_rates, parent_groups, dt_hr)

        smoothed = EvaporationFlux(species_kg_hr=effective_rates)
        smoothed.update_totals()
        return smoothed

    def _apply_shared_o2_reactant_depletion(
        self,
        effective_rates: dict[str, float],
        parent_groups: dict[str, list[dict]],
        dt_hr: float,
    ) -> None:
        o2_draws: list[tuple[str, float]] = []
        for parent_oxide in sorted(parent_groups):
            for entry in parent_groups[parent_oxide]:
                species = entry['species']
                rate_kg_hr = float(effective_rates.get(species, 0.0))
                O2_per_product_kg = float(
                    entry['stoich'].get('O2_per_product_kg', 0.0))
                if rate_kg_hr > 1e-12 and O2_per_product_kg < -1e-12:
                    o2_draws.append(
                        (species, rate_kg_hr * abs(O2_per_product_kg)))
        total_o2_draw_kg_hr = sum(draw for _species, draw in o2_draws)
        if total_o2_draw_kg_hr <= 1e-12:
            return

        available_o2_kg = self.atom_ledger.kg_by_account(
            'process.overhead_gas').get('O2', 0.0)
        if available_o2_kg <= 1e-12:
            for species, _draw in o2_draws:
                effective_rates.pop(species, None)
            return

        max_fraction = math.nextafter(1.0, 0.0)
        k_hr = total_o2_draw_kg_hr / float(available_o2_kg)
        depletion_fraction = -math.expm1(-k_hr * dt_hr)
        depletion_fraction = max(0.0, min(max_fraction, depletion_fraction))
        allowed_o2_draw_kg = float(available_o2_kg) * depletion_fraction
        by_species_draw = dict(o2_draws)
        for species, required_o2_kg_hr in o2_draws:
            if required_o2_kg_hr <= 1e-12:
                continue
            allowed_draw_kg = (
                allowed_o2_draw_kg
                * required_o2_kg_hr
                / total_o2_draw_kg_hr
            )
            current_rate = float(effective_rates.get(species, 0.0))
            allowed_rate = current_rate * (
                allowed_draw_kg / by_species_draw[species])
            if allowed_rate <= 1e-12:
                effective_rates.pop(species, None)
            else:
                effective_rates[species] = min(current_rate, allowed_rate)

    def _route_to_condensation(self, evap_flux: EvaporationFlux):
        """
        Route evaporated species through the condensation train.

        Each species flows from Stage 0 (hot duct) downward through
        successive stages.  At each stage, a fraction condenses based
        on the condensation efficiency model:

            η = 1 - exp(-residence_time / τ_condensation)

        Species condense preferentially in stages where the stage
        temperature is well below the species' condensation temperature.

        The oxygen component of each evaporated metal oxide is
        released as O2 and credited to terminal oxygen storage.

        CONDENSATION_ROUTE intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), fourth flip and the
        SECOND authoritative intent in the migration. The legacy
        ``CondensationModel.route()`` still computes the per-stage
        deposition projection (η model, residence times), but the
        ledger transition that moves vapor from
        ``process.overhead_gas`` to ``process.condensation_train`` is
        now owned by the :class:`BuiltinCondensationRouteProvider` and
        committed through the kernel. The flow per species is:

        1. EVAPORATION_TRANSITION dispatched with ``remaining=rate`` --
           ALL vapor routed to ``process.overhead_gas`` (plus O2
           coproduct). No condensation_train credit from that intent.
        2. CONDENSATION_ROUTE dispatched with the analytically smoothed
           per-species condensed_kg derived from
           ``route_result.remaining_by_species`` -- debits
           ``process.overhead_gas[species]`` and credits
           ``process.condensation_train[products]`` (with SiO
           disproportionation when sp_data declares the product map).
        3. ``_project_condensed_stage_collection`` projects the actual
           credited_condensed_kg onto stage UI bookkeeping.

        End-of-tick ledger state is identical to the pre-flip behaviour:
        between the two kernel commits the vapor passes through
        overhead_gas, but the final per-account balances match the
        legacy single-step EVAPORATION_TRANSITION exactly (verified by
        the parametrised parity test in
        ``tests/chemistry/test_builtin_condensation_route_provider.py``).
        Per the goal spec, the shadow comparator was removed at flip
        time (the parity test owns the regression surface from now on).
        """
        route_result = self.condensation_model.route(
            evap_flux, self.melt)

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            stoich = self._evaporation_stoich(species, sp_data)
            if stoich is None:
                continue
            available_kg = self.atom_ledger.kg_by_account(
                'process.cleaned_melt').get(stoich['parent_oxide'], 0.0)
            if available_kg <= 1e-12:
                continue
            remaining_kg_hr = route_result.remaining_by_species.get(
                species, 0.0)
            if (
                remaining_kg_hr < -1e-12
                or remaining_kg_hr > rate_kg_hr + 1e-12
            ):
                raise AccountingError(
                    f"condensation route for {species!r} returned "
                    "unphysical remaining vapor mass"
                )

            # Step 1: EVAPORATION_TRANSITION (melt -> overhead_gas + O2).
            # Pass remaining=rate so the prior intent's wire-in routes
            # ALL vapor to overhead, leaving the deposit leg to
            # CONDENSATION_ROUTE.  The EVAPORATION_TRANSITION provider's
            # internal validation already rejects remaining > rate; equal
            # rates pass.
            self._credit_evaporation_transition(
                species, rate_kg_hr, rate_kg_hr, sp_data,
            )

            # Step 2: CONDENSATION_ROUTE (overhead_gas -> condensation_train).
            # condensed_kg mirrors the legacy
            # ``credited_condensed_kg = max(0.0, product_kg - remaining_kg)``
            # branch in _credit_evaporation_transition pre-flip: clamp
            # negative, take the vapor mass the route said would deposit.
            condensed_kg = max(
                0.0, rate_kg_hr - remaining_kg_hr,
            )
            credited_condensed_kg = self._dispatch_condensation_route(
                species, condensed_kg, sp_data, route_result,
            )

            # Step 3: stage UI projection (unchanged behaviour).
            product_projection = self._condensed_products_kg(
                species, credited_condensed_kg, sp_data)
            self._project_condensed_stage_collection(
                route_result, species, credited_condensed_kg,
                product_projection)

        self._sync_oxygen_kg_counters()

    def _dispatch_condensation_route(
        self,
        species: str,
        condensed_kg: float,
        sp_data: dict,
        route_result,
    ) -> float:
        """Dispatch CONDENSATION_ROUTE through the kernel + commit.

        F-B1 (Cluster B): the dispatch + commit interleave collapsed
        into :meth:`_dispatch_and_commit`.  The overhead ->
        condensation_train ledger write still happens ONLY through
        ``commit_batch`` (the helper's only writable path); a no-op
        dispatch (kernel returned ``transition is None``) increments
        the F-A4 ``_chem_no_op_dispatch_count`` counter so a replay
        tool can distinguish "kernel skipped" from "called and no-op".

        Returns ``credited_condensed_kg`` -- the amount of vapor
        actually deposited onto ``process.condensation_train``, used by
        the caller to drive ``_project_condensed_stage_collection``.
        """

        kernel_result = self._dispatch_and_commit(
            ChemistryIntent.CONDENSATION_ROUTE,
            control_inputs={
                'species': species,
                'condensed_kg': float(condensed_kg),
                'sp_data': dict(sp_data or {}),
                'wall_deposit_fraction': float(
                    route_result.wall_deposit_fraction_by_species.get(
                        species, 0.0)),
                'wall_deposit_account_fractions': dict(
                    route_result
                    .wall_deposit_account_fractions_by_species
                    .get(species, {})),
                'dt_hr': 1.0,
            },
        )
        if kernel_result.transition is None:
            return 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        self._record_wall_deposit_delta(species, diagnostic)
        return float(diagnostic.get('credited_condensed_kg', 0.0))

    def _record_wall_deposit_delta(
        self,
        species: str,
        diagnostic: Mapping[str, Any],
    ) -> None:
        # Delta provenance is the committed, post-validation kernel credit.
        # CondensationRouteResult is only the pre-commit projection; Phase-O
        # coating gates need the deposition that actually landed in the ledger.
        accounts_kg = diagnostic.get('credited_wall_deposit_accounts_kg') or {}
        if not isinstance(accounts_kg, Mapping):
            return
        for account, kg in accounts_kg.items():
            amount = max(0.0, float(kg))
            if amount <= 1e-12:
                continue
            account_name = str(account)
            if account_name.startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):
                segment = account_name[len(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):]
            else:
                segment = account_name
            key = (segment, species)
            deltas = self._last_wall_deposit_by_segment_species_delta
            deltas[key] = deltas.get(key, 0.0) + amount

    def _credit_evaporation_transition(
        self,
        species: str,
        rate_kg_hr: float,
        remaining_kg_hr: float,
        sp_data: dict,
    ) -> float:
        """Apply the per-species melt -> vapor transition via the kernel.

        EVAPORATION_TRANSITION intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), third flip and the FIRST
        authoritative intent in the migration. The
        BuiltinEvaporationTransitionProvider builds a
        :class:`LedgerTransitionProposal` (debit cleaned_melt, credit
        overhead_gas + condensation_train); the kernel's commit_batch
        applies it to the AtomLedger after re-validating atom balance
        and the account-filter. This method:

        1. Validates the input stoich and condensation-route output the
           same way the legacy code did (the AccountingError surface
           lives in the caller, not inside the stateless provider --
           matching the EVAPORATION_FLUX pattern).
        2. Receives an analytically smoothed rate whose parent-oxide and
           shared-O2 availability caps have already been applied.
        3. Dispatches EVAPORATION_TRANSITION through the kernel and
           commits the resulting proposal. After this flip,
           ``self.atom_ledger.apply(...)`` no longer fires from inside
           this method -- ``self._chem_kernel.commit_batch(...)`` is the
           only writable path.

        Returns:
            ``credited_condensed_kg`` -- the amount of vapor that
            condensed onto ``process.condensation_train``, used by the
            caller to drive stage-collection bookkeeping.
        """

        stoich = self._evaporation_stoich(species, sp_data)
        if stoich is None:
            return 0.0

        parent_oxide = stoich['parent_oxide']
        rate_kg_per_tick = rate_kg_hr * 1.0  # 1-hour tick (see core.py)
        oxide_removed = rate_kg_per_tick * stoich['oxide_per_product_kg']
        product_kg = rate_kg_per_tick

        if oxide_removed <= 1e-12:
            return 0.0

        available_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt').get(parent_oxide, 0.0)
        if available_kg <= 1e-12:
            return 0.0

        # Mirror the legacy validation: the AccountingError surface is
        # owned by the caller, not the provider. The provider receives
        # already-validated inputs (this matches the EVAPORATION_FLUX
        # flip pattern in _calculate_evaporation).
        if remaining_kg_hr < -1e-12 or remaining_kg_hr > rate_kg_hr + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} returned "
                "unphysical remaining vapor mass"
            )
        remaining_kg = max(0.0, remaining_kg_hr) * 1.0
        if remaining_kg > product_kg + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} exceeds credited vapor"
            )

        # F-B1: dispatch + commit through the shared helper.  The
        # kernel's commit_batch path is still the ONLY writable entry
        # into the AtomLedger for EVAPORATION_TRANSITION; the helper
        # re-runs the full pre-commit validator stack inside
        # commit_batch (defence in depth).
        kernel_result = self._dispatch_and_commit(
            ChemistryIntent.EVAPORATION_TRANSITION,
            control_inputs={
                'species': species,
                'stoich': dict(stoich),
                'sp_data': dict(sp_data or {}),
                'rate_kg_hr': float(rate_kg_hr),
                'remaining_kg_hr': float(remaining_kg_hr),
                'dt_hr': 1.0,
                'available_kg': float(available_kg),
            },
        )
        if kernel_result.transition is None:
            return 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        return float(diagnostic.get('credited_condensed_kg', 0.0))

    def _condensed_products_for_vapor(
        self, species: str, condensed_kg: float, sp_data: dict
    ):
        products_mol_per_mol = self._condensation_product_mol_ratios(
            species, sp_data)
        if products_mol_per_mol is None:
            return None, {species: condensed_kg} if condensed_kg > 0.0 else {}

        vapor_formula = resolve_species_formula(
            species, self.species_formula_registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        product_mol = {
            product: ratio * vapor_mol
            for product, ratio in products_mol_per_mol.items()
            if ratio * vapor_mol > 0.0
        }
        return product_mol, self._species_mol_to_kg(product_mol)

    def _condensed_products_kg(
        self, species: str, condensed_kg: float, sp_data: dict
    ) -> dict:
        _product_mol, product_kg = self._condensed_products_for_vapor(
            species, condensed_kg, sp_data)
        product_accounts = dict(
            (sp_data or {}).get('condensation_product_accounts') or {}
        )
        if product_accounts:
            product_kg = {
                product: kg
                for product, kg in product_kg.items()
                if product_accounts.get(product) != 'process.overhead_gas'
            }
        return product_kg

    def _condensation_product_mol_ratios(
        self, species: str, sp_data: dict
    ):
        ratios = sp_data.get('condensation_products_mol_per_mol_vapor')
        if ratios is None:
            declared = str(sp_data.get('condensation_product', '')).lower()
            if 'disproportion' in declared:
                raise AccountingError(
                    f"vapor species {species!r} declares condensation "
                    "disproportionation but lacks "
                    "condensation_products_mol_per_mol_vapor metadata"
                )
            return None
        if not isinstance(ratios, dict) or not ratios:
            raise AccountingError(
                f"vapor species {species!r} condensation products must be "
                "a non-empty mapping"
            )

        clean = {}
        for product, raw_ratio in ratios.items():
            ratio = float(raw_ratio)
            if ratio <= 0.0 or not math.isfinite(ratio):
                raise AccountingError(
                    f"vapor species {species!r} condensation product "
                    f"{product!r} requires a positive mol ratio"
                )
            clean[str(product)] = ratio
        self._validate_condensation_products_atoms(species, clean)
        return clean

    def _validate_condensation_products_atoms(
        self, vapor_species: str, products_mol_per_mol: dict
    ) -> None:
        debit_atoms = resolve_species_formula(
            vapor_species, self.species_formula_registry).atom_moles(1.0)
        credit_atoms = defaultdict(float)
        for product, mol in products_mol_per_mol.items():
            formula = resolve_species_formula(
                product, self.species_formula_registry)
            for element, moles in formula.atom_moles(mol).items():
                credit_atoms[element] += moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-9, abs_tol=1e-12):
                raise AccountingError(
                    f"vapor species {vapor_species!r} condensation products "
                    f"do not conserve {element} atoms"
                )

    def _species_mol_to_kg(self, species_mol: dict) -> dict:
        converted = {}
        for species, mol in species_mol.items():
            formula = resolve_species_formula(
                species, self.species_formula_registry)
            kg = float(mol) * formula.molar_mass_kg_per_mol()
            if kg > 0.0:
                converted[species] = kg
        return converted

    def _evaporation_stoich(self, species: str, sp_data: dict):
        parent_oxide = sp_data.get('parent_oxide', '')
        if not parent_oxide:
            raise AccountingError(
                f"vapor species {species!r} requires parent_oxide "
                "metadata before ledger routing"
            )

        has_oxide = sp_data.get('stoich_oxide_per_vapor') is not None
        has_o2 = sp_data.get('stoich_O2_per_vapor') is not None
        if has_oxide or has_o2:
            missing = []
            if not has_oxide:
                missing.append('stoich_oxide_per_vapor')
            if not has_o2:
                missing.append('stoich_O2_per_vapor')
            if missing:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    f"missing explicit stoich metadata: {', '.join(missing)}"
                )
            oxide_per_product = float(sp_data['stoich_oxide_per_vapor'])
            O2_per_product = float(sp_data['stoich_O2_per_vapor'])
            if oxide_per_product <= 0.0:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "requires positive stoich_oxide_per_vapor"
                )
            if not math.isclose(
                oxide_per_product,
                1.0 + O2_per_product,
                rel_tol=1e-6,
                abs_tol=1e-9,
            ):
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "stoich metadata must conserve mass: "
                    "stoich_oxide_per_vapor must equal "
                    "1 + stoich_O2_per_vapor"
                )
            self._validate_evaporation_stoich_atoms(
                parent_oxide,
                species,
                oxide_per_product,
                O2_per_product,
            )
            return {
                'parent_oxide': parent_oxide,
                'oxide_per_product_kg': oxide_per_product,
                'O2_per_product_kg': O2_per_product,
            }

        implied = OXIDE_TO_METAL.get(parent_oxide, ('', 0, 0))[0]
        if species != implied:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} requires "
                "explicit stoich_oxide_per_vapor and stoich_O2_per_vapor; "
                f"STOICH_RATIOS fallback only applies to elemental "
                f"{implied!r}"
            )
        fallback = STOICH_RATIOS.get(parent_oxide)
        if not fallback or fallback[0] <= 0:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} has no "
                "valid elemental stoich fallback"
            )
        kg_product_per_kg_oxide, kg_O2_per_kg_oxide = fallback
        oxide_per_product = 1.0 / kg_product_per_kg_oxide
        O2_per_product = kg_O2_per_kg_oxide / kg_product_per_kg_oxide
        if not math.isclose(
            oxide_per_product,
            1.0 + O2_per_product,
            rel_tol=1e-6,
            abs_tol=1e-9,
        ):
            raise AccountingError(
                f"STOICH_RATIOS[{parent_oxide!r}] does not conserve mass"
            )
        self._validate_evaporation_stoich_atoms(
            parent_oxide,
            species,
            oxide_per_product,
            O2_per_product,
        )
        return {
            'parent_oxide': parent_oxide,
            'oxide_per_product_kg': oxide_per_product,
            'O2_per_product_kg': O2_per_product,
        }

    def _validate_evaporation_stoich_atoms(
        self,
        parent_oxide: str,
        product_species: str,
        oxide_per_product_kg: float,
        O2_per_product_kg: float,
    ) -> None:
        debit_atoms = self._atom_moles_for_kg(
            parent_oxide, oxide_per_product_kg)
        credit_atoms = defaultdict(float)
        product_atoms = self._atom_moles_for_kg(product_species, 1.0)
        for element, moles in product_atoms.items():
            credit_atoms[element] += moles
        if O2_per_product_kg >= 0.0:
            oxygen_atoms = self._atom_moles_for_kg('O2', O2_per_product_kg)
            for element, moles in oxygen_atoms.items():
                credit_atoms[element] += moles
        else:
            oxygen_atoms = self._atom_moles_for_kg('O2', abs(O2_per_product_kg))
            for element, moles in oxygen_atoms.items():
                debit_atoms[element] = debit_atoms.get(element, 0.0) + moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-6, abs_tol=1e-9):
                raise AccountingError(
                    f"vapor species {product_species!r} from "
                    f"{parent_oxide!r} stoich metadata does not conserve "
                    f"{element} atoms"
                )

    def _atom_moles_for_kg(self, species: str, kg: float) -> dict:
        if kg <= 0.0:
            return {}
        formula = resolve_species_formula(
            species, self.species_formula_registry)
        species_moles = float(kg) / formula.molar_mass_kg_per_mol()
        return formula.atom_moles(species_moles)

    def _project_condensed_stage_collection(
        self, route_result, species: str, credited_condensed_kg: float,
        product_kg_by_species: dict | None = None,
    ) -> None:
        if credited_condensed_kg <= 1e-12:
            return
        product_kg_by_species = product_kg_by_species or {
            species: credited_condensed_kg}
        intended_condensed_kg = route_result.condensed_for_species(species)
        if intended_condensed_kg <= 1e-12:
            return
        scale = credited_condensed_kg / intended_condensed_kg
        product_scale = {
            product: kg / credited_condensed_kg
            for product, kg in product_kg_by_species.items()
            if kg > 1e-12
        }
        stages_by_number = {
            stage.stage_number: stage for stage in self.train.stages
        }
        for stage_number, stage_species in (
            route_result.condensed_by_stage_species.items()
        ):
            projected_kg = stage_species.get(species, 0.0) * scale
            if projected_kg <= 1e-12:
                continue
            stage = stages_by_number.get(stage_number)
            if stage is None:
                continue
            is_impurity = (
                route_result.impurity_by_stage_species
                .get(stage_number, {})
                .get(species, 0.0)
                > 0.0
            )
            for product, product_fraction in product_scale.items():
                stage_product_kg = projected_kg * product_fraction
                if stage_product_kg <= 1e-12:
                    continue
                delta_key = (stage_number, product)
                deltas = self._last_condensed_by_stage_species_delta
                deltas[delta_key] = deltas.get(delta_key, 0.0) + stage_product_kg
                if is_impurity:
                    impurity = self._last_impurity_delta
                    impurity[delta_key] = (
                        impurity.get(delta_key, 0.0) + stage_product_kg)
                stage.collected_kg.update({
                    product: (
                        stage.collected_kg.get(product, 0.0)
                        + stage_product_kg)
                })

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """Project the cleaned-melt account onto MeltState kg fields."""
        self._project_cleaned_melt_from_atom_ledger()
