"""Thermodynamic equilibrium helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_GAS,
    ELLINGHAM_THERMO as _CANONICAL_ELLINGHAM_THERMO,
    ellingham_authority_diagnostic,
    ellingham_authority_limit,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_range_K,
    ellingham_metal_phase_kind,
    ellingham_stoichiometry,
)
from simulator.chemistry.melt_activity import (
    melt_oxide_activity,
    single_cation_mole_fractions,
)
from simulator.fe_redox import (
    calphad_ferrous_feo_activity_diagnostic,
    kress91_furnace_activity_pressure_bar,
)
from simulator.environment import vacuum_floor_bar_for_environment
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.state import GAS_CONSTANT, MOLAR_MASS, Atmosphere

# Atmosphere modes where a turbine/bleed loop actively holds a commanded pO₂
# setpoint. Only in these modes may the setpoint act as a floor on the
# effective pO₂. Uncontrolled hard-vacuum / pN₂ runs do not apply that
# setpoint floor; they still use any real carried/upstream O₂ (see
# ``_commanded_pO2_bar``).
_O2_CONTROLLED_ATMOSPHERES = frozenset({
    Atmosphere.CONTROLLED_O2,
    Atmosphere.CONTROLLED_O2_FLOW,
    Atmosphere.O2_BACKPRESSURE,
})
_ELLINGHAM_STANDARD_PRESSURE_PA = 100000.0


def _internal_analytical_control_refusal(
    temperature_C: object,
    p_total_mbar: object,
) -> dict[str, Any] | None:
    """Return a refusal diagnostic for unusable T / total-P controls.

    Scoped to ``EquilibriumMixin._internal_analytical_equilibrium``.
    Admitted controls on that path: finite T_K > 0 and finite
    p_total_mbar >= 0 (0 mbar is true vacuum). Non-numeric, non-finite,
    non-positive kelvin, and negative total pressure do not yield a
    finite physical T/P for the vapor-pressure algebra, so this helper
    returns a refusal diagnostic instead of ``status='ok'`` or a foreign
    Ellingham ``ValueError``.
    """
    invalid: list[dict[str, Any]] = []
    if not is_declared_real_scalar(temperature_C):
        invalid.append({
            "field": "temperature_C",
            "reason": "not_numeric",
            "value": repr(temperature_C),
        })
    else:
        t_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        if not math.isfinite(t_K) or t_K <= 0.0:
            invalid.append({
                "field": "temperature_C",
                "reason": "kelvin_not_finite_and_positive",
                "value": repr(temperature_C),
                "temperature_K": t_K,
            })
    if not is_declared_real_scalar(p_total_mbar):
        invalid.append({
            "field": "p_total_mbar",
            "reason": "not_numeric",
            "value": repr(p_total_mbar),
        })
    else:
        p_mbar = float(p_total_mbar)
        if not math.isfinite(p_mbar) or p_mbar < 0.0:
            invalid.append({
                "field": "p_total_mbar",
                "reason": "not_finite_or_negative",
                "value": repr(p_total_mbar),
            })
    if not invalid:
        return None
    return {
        "internal_analytical_refusal": "invalid_scientific_controls",
        "invalid_controls": invalid,
    }


class EquilibriumMixin:
    def _get_equilibrium(self):
        raise NotImplementedError(
            "backend equilibrium must be supplied by the simulator class "
            "using AtomLedger mol inputs"
        )

    def _vacuum_floor_bar(self) -> float:
        ambient_pressure_bar = (
            float(getattr(self.melt, 'ambient_pressure_mbar', 0.0) or 0.0)
            / 1000.0
        )
        return vacuum_floor_bar_for_environment(
            body=getattr(self.melt, 'body', ''),
            ambient_pressure_bar=(
                ambient_pressure_bar if ambient_pressure_bar > 0.0 else None
            ),
        )

    def _commanded_pO2_bar(self) -> float:
        """
        Commanded oxygen partial pressure (bar) for this hour.

        Finite-headspace mode reads the provider's gas inventory diagnostic;
        non-finite mode reads the upstream melt-headspace partial projection.

        Resolution:
          - ``_melt_headspace_composition_mbar['O2']`` is the upstream
            melt-side partial. ``overhead.composition`` is downstream of
            condensation and cannot set authoritative equilibrium pO₂.
          - The commanded setpoint (``melt.pO2_mbar``) is applied again as
            an explicit *floor*, and only when the atmosphere is an
            actively O₂-controlled mode (turbine + bleed holding the
            setpoint). Uncontrolled HARD_VACUUM / PN2_SWEEP runs do not
            apply that setpoint. Effective pO₂ on those paths is
            ``max(real_upstream_or_headspace_O2, vacuum_floor)``, so any
            carried O₂ above the environmental floor still controls
            equilibrium. It does not collapse to the vacuum floor for the
            whole campaign.
          - A hard numerical floor (``self._vacuum_floor_bar()``) guards the
            1/√pO₂ and K/pO₂ divisions; it is not a setpoint.

        With finite headspace enabled, melt-offgas O₂ remains in
        ``process.overhead_gas`` until the OVERHEAD_BLEED provider moves it
        to melt-offgas terminal bins, so this helper sees real carried
        headspace pO₂ instead of a synthetic vacuum-floor setpoint.
        """
        enabled = getattr(self, '_overhead_headspace_enabled', lambda: False)()
        if enabled:
            diagnostic = getattr(
                self, '_overhead_gas_equilibrium_diagnostic', lambda: {}
            )()
            partials = dict(diagnostic.get('partial_pressures_bar') or {})
            pO2_bar = float(
                partials.get('O2', diagnostic.get('p_O2_bar', 0.0)) or 0.0
            )
            # 0.5.3 Phase A1 (2026-05-28): under finite-headspace ON, the
            # holdup-derived O2 partial pressure replaces the synthetic
            # commanded-pO2 setpoint from the legacy no-headspace branch.
            # Re-apply melt.pO2_mbar as a floor in actively-controlled
            # atmospheres so a recipe pO2 setpoint still gates SiO suppression
            # via 1/sqrt(pO2) above the SiO reference. Uncontrolled
            # HARD_VACUUM / PN2_SWEEP runs get no setpoint floor; they
            # still return max(real O2, vacuum floor), not the vacuum
            # floor alone.
            if self.melt.atmosphere in _O2_CONTROLLED_ATMOSPHERES:
                pO2_bar = max(pO2_bar, self.melt.pO2_mbar / 1000.0)
            return max(pO2_bar, self._vacuum_floor_bar())

        upstream_partials = getattr(
            self,
            '_melt_headspace_composition_mbar',
            {},
        ) or {}
        pO2_bar = max(
            0.0,
            float(upstream_partials.get('O2', 0.0) or 0.0) / 1000.0,
        )
        if self.melt.atmosphere in _O2_CONTROLLED_ATMOSPHERES:
            pO2_bar = max(pO2_bar, self.melt.pO2_mbar / 1000.0)
        return max(pO2_bar, self._vacuum_floor_bar())

    def _headspace_transport_pO2_bar(self) -> float:
        """O2 transport reservoir pO2 consumed by SiO/vapor transport."""

        reservoir = getattr(self.melt, 'oxygen_reservoir', None)
        if reservoir is not None:
            pO2_bar = float(
                getattr(reservoir, 'headspace_transport_pO2_bar', 0.0)
                or 0.0
            )
            if pO2_bar > 0.0:
                return max(pO2_bar, self._vacuum_floor_bar())
        return self._commanded_pO2_bar()

    # --- Ellingham thermodynamic data for oxide equilibrium ---        [ELLI]
    #
    # Standard-state formation enthalpy (ΔH_f) and entropy (ΔS_f)
    # per mol O₂ for each oxide.  Used to compute the temperature-
    # dependent Gibbs free energy of formation:
    #
    #   ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)               [ELLI-1]
    #
    # The decomposition equilibrium constant is:
    #
    #   K = exp(ΔG_f / (R × T))                                 [ELLI-2]
    #
    # Premise: ΔG_f is the formation energy of the oxide per mol O₂
    # (kJ/mol-O2); decomposition is the reverse reaction, so
    # K_decomp = exp(ΔG_f / RT) = exp(-ΔG_decomp / RT).
    # Algebra/units: this method multiplies ΔG_f by 1000 J/kJ before
    # dividing by R (J/(mol·K)) × T (K), so the exponent is
    # dimensionless. Limiting cases: ΔG_f = 0 → K = 1; ΔG_f < 0 → K < 1;
    # ΔG_f > 0 → K > 1. Current phase-segmented Na/K rows can have
    # ΔG_f > 0 inside their declared fit range, so K < 1 is a sign case,
    # not an invariant.
    #
    # For the decomposition reaction per mol O₂, the selected Ellingham row
    # supplies the metal standard state:
    #   n_ox × oxide(melt) → n_M × Metal(phase_basis) + O₂(gas)
    #
    # The equilibrium metal activity root is:
    #
    #   a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)          [ELLI-3]
    #
    # The effective metal vapor pressure above the melt is rail-specific:
    #
    #   condensed row: P_metal(g) = a_M(cond) × P_reference(T)   [ELLI-4a]
    #   gas row:       P_metal(g) = a_M(g) × p°                 [ELLI-4b]
    #
    # where P_reference comes from vapor_pressures.yaml. It is
    # pure-component / first-principles only when
    # fit_target=pure_component_psat; pseudo_psat_backsolved_from_vaporock
    # rows are backsolved VapoRock curve-fit fallback terms.
    #
    # Qualitative process narrative, not a single-T ΔG table: alkali metals
    # are volatile at recipe T; Ca/Al/Ti remain refractory at
    # furnace-survivable T. Live ΔG_f(T) is phase-segmented and is read
    # from ``ellingham_delta_g_kj_per_mol_o2``. Recalled grouped values
    # such as "Na,K ≈ −320 kJ; Fe ≈ −370 kJ" do not describe those rows
    # at 1600 °C or at the other process temperatures checked against
    # this function. ``data/setpoints.yaml`` ``ellingham_V1c_reference``
    # stores qualitative ordering plus crossover temperatures, not a
    # per-species 1600 °C ΔG table.
    #
    # Data: NIST-JANAF Thermochemical Tables, Kubaschewski et al.
    #
    # Vapor-pressure convention contract (`data/vapor_pressures.yaml`):
    # - Metals with `fit_target: pure_component_psat` have raw Antoine
    #   evaluated as `P_sat_pure`, then multiplied by Ellingham `a_M` --
    #   single-counted.
    # - Metals with `fit_target: pseudo_psat_backsolved_from_vaporock` have raw
    #   Antoine evaluated as a pseudo-standard term such that
    #   `a_M * 10^(A-B/(T+C)) ~= VapoRock_partial_pressure` on the
    #   calibration grid. The convention is single-counted by construction
    #   but assumes proximity to that grid.
    # - Metal or oxide vapor rows with `fit_target: standard_reaction_term`
    #   use raw Antoine as a ΔG-equivalent term, consumed with explicit
    #   oxide-activity + pO2 exponents -- single-counted via explicit reaction
    #   stoichiometry.
    #
    # Tuple: (ΔH_f kJ/mol_O₂, ΔS_f kJ/(mol·K), n_M, n_ox)
    #   n_M  = moles of metal per mol O₂ in the decomposition reaction
    #   n_ox = moles of oxide per mol O₂ in the decomposition reaction
    _ELLINGHAM_THERMO = _CANONICAL_ELLINGHAM_THERMO

    def _internal_analytical_equilibrium(self):
        """
        Fallback equilibrium using Ellingham thermodynamics + Antoine
        vapor pressures.

        When no melt backend (AlphaMELTS/VapoRock) is available, we
        compute metal vapor pressures above the oxide melt by combining
        the oxide decomposition equilibrium (Ellingham) with the pure-metal
        vaporization curve (Antoine).

        The approach for each metal species:

        1. Compute oxide stability at current T:                  [ELLI-1]
               ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)

        2. Get the decomposition equilibrium constant:            [ELLI-2]
               K = exp(ΔG_f / (R × T))
           K < 1 only when ΔG_f < 0; see the class-level [ELLI-2] note.

        3. Solve for equilibrium metal activity on the phase basis:[ELLI-3]
               a_M = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)

        4. For condensed-standard rows, get P_sat from Antoine:
               P_sat = 10^(A − B/(T+C))   (Pa)

        5. Select the phase-correct pressure rail:                 [ELLI-4]
               condensed: P_metal = min(a_M, 1) × P_sat
               gas:       P_metal = a_M(g) × p°

        This correctly captures:
        - Temperature dependence of BOTH oxide stability AND metal
          volatility (the two factors that control pyrolysis yield).
        - pO₂ dependence: higher pO₂ pushes equilibrium toward oxide,
          suppressing metal vapor.  This is the physics behind pO₂-
          managed campaigns (C2B, C3, C4).
        - Composition dependence: as an oxide is depleted, its activity
          drops and evaporation rate decreases.
        - Volatility ordering is a process narrative (alkalis first,
          refractories last), not a fixed ΔG table. Na/K and some
          liquid-oxide rows on this method take standard-reaction
          rails and ``continue`` before the legacy Ellingham
          ``a_M × P_sat`` block, so returned pressures are a mix of
          rails rather than that block alone.

        SiO vapor uses a separate equilibrium pathway because it
        evaporates as an oxide gas (SiO₂ → SiO + ½O₂), not as a
        metal. The Antoine row is the reference pressure; a √pO₂
        suppression is applied only above the row's pO₂ reference
        (see the oxide-vapor loop).  [THERMO-8]
        """
        from simulator.melt_backend.base import EquilibriumResult
        from engines.builtin.vapor_pressure import (
            COEFF_BLOCK_ANTOINE,
            FIT_TARGET_PSEUDO_VAPOROCK,
            FIT_TARGET_STANDARD_REACTION,
            GAS_RAIL_STANDARD_REACTION_KEY,
            RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY,
            _gas_rail_standard_reaction_block,
            _is_noncertifying_pseudo_vapor_pressure_runtime,
            _liquid_oxide_standard_reaction_block,
            _metadata_value,
            _o2_channel_term_and_potential,
            _pow10_pressure_or_raise,
            _range_tuple,
            _require_finite_vapor_value,
            _standard_reaction_pressure_Pa,
            physical_melt_dissociation_pO2_bar,
            reject_noncertifying_vapor_pressure_row,
            require_antoine_source_certified_temperature,
            vapor_pressure_authority_diagnostic,
            vapor_pressure_source_label,
            vapor_pressure_antoine_coefficients,
            vapor_pressure_valid_range_K,
            warn_pseudo_vapor_pressure_fallback,
        )
        from simulator.vapour_rail.channels import (
            REACTION_PLANE_MELT_INTERFACE,
        )

        refusal = _internal_analytical_control_refusal(
            self.melt.temperature_C,
            self.melt.p_total_mbar,
        )
        if refusal is not None:
            return EquilibriumResult(
                temperature_C=self.melt.temperature_C,
                pressure_bar=0.0,
                liquid_fraction=None,
                phase_assemblage_available=False,
                fO2_log=None,
                status='out_of_domain',
                diagnostics=refusal,
            )

        T_K = float(self.melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        if T_K < 400:
            # Reached only after the control guard: T_K is finite and > 0 K,
            # p_total_mbar is finite and >= 0. On this cold path the
            # vapor-pressure loop is skipped; empty vapors with
            # status='ok' is the intended sub-400 K outcome, not an
            # unavailable engine. The guard above already returned for
            # 0 K / NaN / inf / negative T.
            return EquilibriumResult(
                temperature_C=self.melt.temperature_C,
                pressure_bar=float(self.melt.p_total_mbar) / 1000.0,
                liquid_fraction=None,
                phase_assemblage_available=False,
                status='ok',
            )

        vapor_pressures = {}
        vapor_pressure_sources = {}
        activities = {}
        activity_provenance = {}
        metal_extrapolations = {}
        ellingham_extrapolations = {}
        vapor_pressure_authority_limits = {}
        warnings = []
        # b-149 instance 3: typed notes on the `_sz_omit` paths in this
        # method (missing activity, activity-threshold skip, P <= 1e-15 Pa).
        # Those omissions keep status='ok' with a note. Other continues in
        # this method (inactive row, missing parent, A<=0, oxide-vapor
        # outside valid_range_K) still omit without a note.
        from simulator.silent_zero import (
            CATEGORY_PROVEN_ZERO,
            CATEGORY_REFUSE,
            ZeroBecause,
            append_note,
            merge_notes_into_mapping,
            record_on_host,
        )

        silent_zero_notes: list = []

        def _sz_omit(
            species_id: str,
            because: ZeroBecause,
            *,
            field: str,
            detail: str,
            category: int,
        ) -> None:
            append_note(
                silent_zero_notes,
                because,
                site='equilibrium.internal_analytical',
                species=str(species_id),
                field=field,
                detail=detail,
                doctrine_category=category,
            )
            record_on_host(
                self,
                because,
                site='equilibrium.internal_analytical',
                species=str(species_id),
                field=field,
                detail=detail,
                doctrine_category=category,
            )

        def _sz_activity_skip(species_id: str, oxide_activity_obj) -> None:
            if oxide_activity_obj is None:
                _sz_omit(
                    species_id,
                    ZeroBecause.MISSING_ACTIVITY,
                    field='oxide_activity',
                    detail=(
                        'melt_oxide_activity returned None; species omitted '
                        'from vapor_pressures while status stays ok'
                    ),
                    category=CATEGORY_REFUSE,
                )
            else:
                _sz_omit(
                    species_id,
                    ZeroBecause.PROVEN_BELOW_THRESHOLD,
                    field='oxide_activity',
                    detail=(
                        'oxide activity <= 1e-10; species omitted by the '
                        'activity skip used on this path. That cutoff is an '
                        'activity filter, not derived from the 1e-15 Pa '
                        'vapor-pressure omit'
                    ),
                    category=CATEGORY_PROVEN_ZERO,
                )

        def _sz_below_threshold(species_id: str, p_pa: float, field: str) -> None:
            _sz_omit(
                species_id,
                ZeroBecause.PROVEN_BELOW_THRESHOLD,
                field=field,
                detail=(
                    f'{field}={p_pa!r} <= 1e-15 Pa; key omitted from '
                    f'vapor_pressures_Pa while status=ok (distinguishable '
                    f'proven-small zero)'
                ),
                category=CATEGORY_PROVEN_ZERO,
            )

        pseudo_warning_seen = getattr(
            self,
            '_pseudo_vapor_pressure_warning_seen',
            None,
        )
        if pseudo_warning_seen is None:
            pseudo_warning_seen = set()
            setattr(
                self,
                '_pseudo_vapor_pressure_warning_seen',
                pseudo_warning_seen,
            )

        # SSO-R keeps intrinsic melt fO2 and headspace transport pO2 as
        # coupled but distinct channels: Fe redox reads the melt reservoir;
        # SiO suppression reads the headspace reservoir.
        pO2_bar = self._headspace_transport_pO2_bar()
        vacuum_floor_bar = self._vacuum_floor_bar()
        reservoir = getattr(self.melt, "oxygen_reservoir", None)
        intrinsic_fO2_value = getattr(
            reservoir, "melt_intrinsic_fO2_log", None
        )
        if intrinsic_fO2_value is None:
            intrinsic_fO2_value = getattr(self.melt, 'melt_fO2_log', None)
        if intrinsic_fO2_value is None:
            current_fO2 = getattr(self, '_current_melt_redox_fO2_log', None)
            if callable(current_fO2):
                intrinsic_fO2_log = float(current_fO2())
            else:
                intrinsic_fO2_log = float(getattr(self.melt, 'fO2_log', -9.0))
        else:
            intrinsic_fO2_log = float(intrinsic_fO2_value)

        melt_dissociation_pO2_bar, melt_pO2_clamped = (
            physical_melt_dissociation_pO2_bar(intrinsic_fO2_log)
        )
        if melt_pO2_clamped:
            # b-148: do not feed the 1e300 float sentinel into mass action.
            warnings.append(
                "melt_dissociation_pO2_clamped_to_physical_envelope: "
                f"fO2_log={intrinsic_fO2_log:.6g} "
                f"pO2_bar={melt_dissociation_pO2_bar:g}"
            )
        feo_activity_pressure_bar = kress91_furnace_activity_pressure_bar(
            floor_bar=vacuum_floor_bar,
        )

        # --- Melt composition for oxide activities ---
        comp_wt = self.melt.composition_wt_pct()
        atom_ledger = getattr(self, "atom_ledger", None)
        project_account_mol = getattr(atom_ledger, "project_account_mol", None)
        mol_by_account = getattr(atom_ledger, "mol_by_account", None)
        if callable(project_account_mol):
            # Canonical balances retain signed sub-tolerance dust so ledger
            # closure stays unbiased. Physics consumers use the ledger-owned
            # projection, which clamps only policy-admissible negative dust and
            # still refuses a materially negative normal account.
            melt_account_mol = dict(
                project_account_mol("process.cleaned_melt") or {}
            )
        elif callable(mol_by_account):
            melt_account_mol = dict(mol_by_account("process.cleaned_melt") or {})
        else:
            melt_account_mol = {
                oxide: float(wt_pct) / MOLAR_MASS[oxide] * 1000.0
                for oxide, wt_pct in comp_wt.items()
                if oxide in MOLAR_MASS and float(wt_pct) > 0.0
            }
        cation_mol_fraction = single_cation_mole_fractions(melt_account_mol)
        feo_activity_diagnostic = calphad_ferrous_feo_activity_diagnostic(
            comp_wt=comp_wt,
            fO2_log=intrinsic_fO2_log,
            T_K=T_K,
            pressure_bar=feo_activity_pressure_bar,
            floor_bar=vacuum_floor_bar,
        )

        # ================================================================
        # METAL SPECIES: Ellingham equilibrium + Antoine               [ELLI]
        # ================================================================
        #
        # For each metal, combine the oxide decomposition equilibrium with
        # its phase-correct condensed-P_sat or gas-fugacity pressure rail.
        # Only fit_target=pure_component_psat rows are pure-component /
        # first-principles; pseudo rows are backsolved VapoRock curve-fits.

        metals_data = self.vapor_pressures.get('metals', {})

        for species in self._ELLINGHAM_THERMO:
            n_M, n_ox = ellingham_stoichiometry(species)
            sp_data = metals_data.get(species, {})
            if not sp_data:
                continue
            if str(sp_data.get('consumer_status', '')).lower() == 'inactive':
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue

            # --- Pressure reference rail ---
            #
            # Condensed-rail log10(P/Pa) = A - B/(T_K + C) (modified
            # Antoine, evaluated below). When C = 0 this is linear in 1/T
            # (Clausius-Clapeyron in log10). When C ≠ 0 the fit has a
            # pole at T_K = -C; that pole is not a physical continuation
            # of Clausius-Clapeyron, so the C = 0 form does not license
            # arbitrary low-T extrapolation of a nonzero-C row.
            #
            # K_decomp sets a_M, not a relative-error damper on P_sat.
            # Later on this path, condensed P_effective = min(a_M, 1) *
            # P_reference. Holding a_M fixed, d ln P_effective / d ln
            # P_reference = 1, so a 1-dex source error in P_reference
            # remains 1 dex in P_effective. A small K lowers the absolute
            # pressure via a_M; it does not make a fractional P_sat
            # extrapolation "minor."
            #
            # FeO decomposition in silicate melt is physically real below
            # the Fe melting point; pure-component P_sat below melt is
            # still an extrapolation. The Ellingham segment selector used
            # on this path (`ellingham_segment_for_temperature`) switches
            # Fe(delta) → Fe(l) at 1809 K, matching the JANAF Fe(cr,l)
            # DELTA <--> LIQUID boundary (NIST-JANAF Fe-007), not the
            # recalled 1538 °C = 1811.15 K conversion. That
            # pure-component rationale applies only to
            # fit_target=pure_component_psat rows.
            fit_target = str(sp_data.get("fit_target", "") or "")
            metal_phase_kind = ellingham_metal_phase_kind(species, T_K)
            gas_standard_rail = (
                fit_target != FIT_TARGET_STANDARD_REACTION
                and metal_phase_kind == ELLINGHAM_METAL_PHASE_GAS
            )
            gas_rail_rxn_early = _gas_rail_standard_reaction_block(sp_data)
            liquid_rxn_early = _liquid_oxide_standard_reaction_block(sp_data)
            coefficient_block: str | None = None
            P_reference_Pa: float | None = None
            reconstructed_vapor_limit: dict[str, Any] | None = None
            if not gas_standard_rail and liquid_rxn_early is None:
                antoine, coefficient_block = vapor_pressure_antoine_coefficients(
                    sp_data,
                    temperature_K=T_K,
                )
                if _is_noncertifying_pseudo_vapor_pressure_runtime(
                    species,
                    sp_data,
                    coefficient_block,
                    temperature_K=T_K,
                ):
                    warnings.append(
                        "non_certifying_vapor_pressure_fallback_omitted: "
                        f"species={species} "
                        f"fit_target={FIT_TARGET_PSEUDO_VAPOROCK} "
                        f"residual_dex={_metadata_value(sp_data, 'residual_dex')} "
                        f"confidence_tier={_metadata_value(sp_data, 'confidence_tier')}"
                    )
                    continue
                if bool(sp_data.get("interval_required")):
                    reject_noncertifying_vapor_pressure_row(
                        species,
                        sp_data,
                        coefficient_block,
                    )
                A = antoine.get('A', 0)
                B = antoine.get('B', 0)
                C = antoine.get('C', 0)
                if not (A > 0 and T_K > 300):
                    continue
                reconstructed_segment = sp_data.get(
                    RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY
                )
                reconstructed_bounds = (
                    _range_tuple(reconstructed_segment.get("range_K"))
                    if isinstance(reconstructed_segment, Mapping)
                    else None
                )
                if (
                    reconstructed_bounds is not None
                    and T_K >= reconstructed_bounds[0]
                ):
                    reconstructed_vapor_limit = (
                        require_antoine_source_certified_temperature(
                            species,
                            sp_data,
                            coefficient_block,
                            T_K,
                            consumer="legacy_condensed_rail",
                        )
                    )
                    if reconstructed_vapor_limit is not None:
                        vapor_pressure_authority_limits[species] = (
                            reconstructed_vapor_limit
                        )
                certified_range = (
                    antoine.get("source_certified_range_K")
                    or sp_data.get("source_certified_range_K")
                )
                if (
                    str(sp_data.get("extrapolation_policy", "")).lower()
                    == "refuse"
                    and isinstance(certified_range, (list, tuple))
                    and len(certified_range) == 2
                    and T_K < float(certified_range[0])
                ):
                    warnings.append(
                        "metal_vapor_pressure_out_of_source_certified_range: "
                        f"species={species} consumer=legacy_condensed_rail "
                        f"temperature_K={T_K:.3f} "
                        "source_certified_range_K="
                        f"[{float(certified_range[0]):g}, "
                        f"{float(certified_range[1]):g}]"
                    )
                    continue
                if reconstructed_vapor_limit is None:
                    require_antoine_source_certified_temperature(
                        species,
                        sp_data,
                        coefficient_block,
                        T_K,
                        consumer="legacy_condensed_rail",
                    )
                    valid_range = vapor_pressure_valid_range_K(
                        sp_data,
                        coefficient_block,
                        temperature_K=T_K,
                    )
                    if valid_range and len(valid_range) == 2:
                        valid_low = float(valid_range[0])
                        valid_high = float(valid_range[1])
                        if T_K < valid_low or T_K > valid_high:
                            metal_extrapolations[species] = {
                                'temperature_K': T_K,
                                'valid_range_K': (valid_low, valid_high),
                            }
                            warnings.append(
                                f"{species} metal Antoine fit extrapolated beyond "
                                f"valid_range_K [{valid_low:g}, {valid_high:g}] at "
                                f"{T_K:.3f} K"
                            )
                    log_P = A - B / (T_K + C)
                    P_reference_Pa = _pow10_pressure_or_raise(
                        log_P,
                        species=species,
                        field="P_reference_Pa",
                    )
                else:
                    P_reference_Pa = float(
                        reconstructed_vapor_limit["pressure_Pa"]
                    )
            if fit_target == FIT_TARGET_STANDARD_REACTION:
                assert P_reference_Pa is not None
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                    cation_mol_fraction=cation_mol_fraction,
                    temperature_K=T_K,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    _sz_activity_skip(species, oxide_activity)
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity
                activity_provenance[species] = oxide_activity.provenance()

                # provenance: k_mox_liquid_standard_reaction
                # Lamoreaux & Hildenbrand 1984 Tables 2/4
                # (DOI 10.1063/1.555706) supplies the liquid KO0.5 standard
                # term; DeMaria 1971 Table 1 is held-out pO2 validation only.
                activity_exponent = float(
                    sp_data.get("oxide_activity_exponent", 1.0) or 1.0
                )
                P_effective_Pa = _require_finite_vapor_value(
                    P_reference_Pa
                    * max(oxide_activity.activity, 0.0) ** activity_exponent,
                    species=species,
                    field="P_effective_activity",
                )
                pO2_exponent = float(sp_data.get("pO2_exponent", 0.0) or 0.0)
                if pO2_exponent:
                    # Melt-dissolved non-FeO oxide dissociation sees the
                    # melt's oxygen chemical potential; headspace pO2 is only
                    # the transport/backpressure channel.
                    pO2_reference_bar = max(
                        1e-30,
                        float(sp_data.get("pO2_reference_bar", 1.0) or 1.0),
                    )
                    P_effective_Pa = _require_finite_vapor_value(
                        P_effective_Pa
                        * (melt_dissociation_pO2_bar / pO2_reference_bar)
                        ** pO2_exponent,
                        species=species,
                        field="P_effective_pO2",
                    )
                if P_effective_Pa > 1e-15:
                    vapor_pressures[species] = P_effective_Pa
                    source_label = vapor_pressure_source_label(
                        'builtin_authoritative',
                        sp_data,
                        coefficient_block=coefficient_block,
                        temperature_K=T_K,
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f'{source_label}:'
                            'extrapolated_beyond_valid_range_K'
                        )
                    vapor_pressure_sources[species] = source_label
                else:
                    _sz_below_threshold(species, P_effective_Pa, 'P_effective_Pa')
                continue

            # Al/Ti/Cr/Mn: liquid-oxide standard reaction (pairing fix).
            liquid_rxn = liquid_rxn_early
            if liquid_rxn is not None and fit_target != FIT_TARGET_STANDARD_REACTION:
                antoine_liq = liquid_rxn.get("antoine", {}) or {}
                A_l = float(antoine_liq.get("A", 0.0) or 0.0)
                B_l = float(antoine_liq.get("B", 0.0) or 0.0)
                C_l = float(antoine_liq.get("C", 0.0) or 0.0)
                if not (A_l > 0.0 and T_K > 300.0):
                    continue
                valid_liq = _range_tuple(liquid_rxn.get("valid_range_K"))
                if valid_liq is not None:
                    vlo, vhi = valid_liq
                    if T_K < vlo or T_K > vhi:
                        metal_extrapolations[species] = {
                            "temperature_K": T_K,
                            "valid_range_K": (vlo, vhi),
                            "rail": "liquid_oxide_standard_reaction",
                        }
                        warnings.append(
                            f"{species} liquid-oxide standard reaction "
                            f"extrapolated beyond valid_range_K "
                            f"[{vlo:g}, {vhi:g}] at {T_K:.3f} K"
                        )
                log_P_liq = A_l - B_l / (T_K + C_l)
                P_reference_Pa = _pow10_pressure_or_raise(
                    log_P_liq,
                    species=species,
                    field="P_reference_liquid_oxide_standard_reaction_Pa",
                )
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                    cation_mol_fraction=cation_mol_fraction,
                    temperature_K=T_K,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    _sz_activity_skip(species, oxide_activity)
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity
                activity_provenance[species] = oxide_activity.provenance()
                activity_exponent = float(
                    liquid_rxn.get("oxide_activity_exponent", 1.0) or 1.0
                )
                pO2_exponent = float(
                    liquid_rxn.get("pO2_exponent", 0.0) or 0.0
                )
                pO2_reference_bar = max(
                    1e-30,
                    float(liquid_rxn.get("pO2_reference_bar", 1.0) or 1.0),
                )
                # t-571: O2 enters through channel #1 (owner-gated,
                # envelope-clamped) — bit-identical linear form.
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=melt_dissociation_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                )
                P_eq_raw, _af, _ps = _standard_reaction_pressure_Pa(
                    P_reference_Pa=P_reference_Pa,
                    oxide_activity_value=oxide_activity.activity,
                    activity_exponent=activity_exponent,
                    o2_term=o2_term,
                    o2_potential=o2_potential,
                )
                P_effective_Pa = _require_finite_vapor_value(
                    P_eq_raw,
                    species=species,
                    field="P_effective_liquid_oxide_standard_reaction",
                )
                if P_effective_Pa > 1e-15:
                    vapor_pressures[species] = P_effective_Pa
                    source_label = (
                        "builtin_authoritative:liquid_oxide_standard_reaction"
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f"{source_label}:extrapolated_beyond_valid_range_K"
                        )
                    vapor_pressure_sources[species] = source_label
                else:
                    _sz_below_threshold(species, P_effective_Pa, 'P_effective_Pa')
                continue

            # Ca/Mg gas rail: liquid-oxide standard reaction (pairing fix).
            gas_rail_rxn = gas_rail_rxn_early
            if gas_standard_rail and gas_rail_rxn is not None:
                antoine_gas = gas_rail_rxn.get("antoine", {}) or {}
                A_g = float(antoine_gas.get("A", 0.0) or 0.0)
                B_g = float(antoine_gas.get("B", 0.0) or 0.0)
                C_g = float(antoine_gas.get("C", 0.0) or 0.0)
                if not (A_g > 0.0 and T_K > 300.0):
                    continue
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                    cation_mol_fraction=cation_mol_fraction,
                    temperature_K=T_K,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    _sz_activity_skip(species, oxide_activity)
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity
                activity_provenance[species] = oxide_activity.provenance()
                if isinstance(
                    sp_data.get(RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY),
                    Mapping,
                ):
                    reconstructed_vapor_limit = (
                        require_antoine_source_certified_temperature(
                            species,
                            sp_data,
                            GAS_RAIL_STANDARD_REACTION_KEY,
                            T_K,
                            consumer="legacy_gas_rail",
                        )
                    )
                    if reconstructed_vapor_limit is not None:
                        vapor_pressure_authority_limits[species] = (
                            reconstructed_vapor_limit
                        )
                if reconstructed_vapor_limit is None:
                    valid_gas = _range_tuple(gas_rail_rxn.get("valid_range_K"))
                    if valid_gas is not None:
                        vlo, vhi = valid_gas
                        if T_K < vlo or T_K > vhi:
                            metal_extrapolations[species] = {
                                "temperature_K": T_K,
                                "valid_range_K": (vlo, vhi),
                                "rail": "gas_rail_standard_reaction",
                            }
                            warnings.append(
                                f"{species} gas-rail liquid-oxide standard reaction "
                                f"extrapolated beyond valid_range_K "
                                f"[{vlo:g}, {vhi:g}] at {T_K:.3f} K"
                            )
                    log_P_gas = A_g - B_g / (T_K + C_g)
                    P_reference_Pa = _pow10_pressure_or_raise(
                        log_P_gas,
                        species=species,
                        field="P_reference_gas_rail_standard_reaction_Pa",
                    )
                else:
                    P_reference_Pa = float(
                        reconstructed_vapor_limit["pressure_Pa"]
                    )
                activity_exponent = float(
                    gas_rail_rxn.get("oxide_activity_exponent", 1.0) or 1.0
                )
                pO2_exponent = float(
                    gas_rail_rxn.get("pO2_exponent", 0.0) or 0.0
                )
                pO2_reference_bar = max(
                    1e-30,
                    float(gas_rail_rxn.get("pO2_reference_bar", 1.0) or 1.0),
                )
                # t-571: O2 enters through channel #1 (owner-gated,
                # envelope-clamped) — bit-identical linear form.
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=melt_dissociation_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                )
                P_eq_raw, _activity_factor, _pO2_scaled = (
                    _standard_reaction_pressure_Pa(
                        P_reference_Pa=P_reference_Pa,
                        oxide_activity_value=oxide_activity.activity,
                        activity_exponent=activity_exponent,
                        o2_term=o2_term,
                        o2_potential=o2_potential,
                    )
                )
                P_effective_Pa = _require_finite_vapor_value(
                    P_eq_raw,
                    species=species,
                    field="P_effective_gas_rail_standard_reaction",
                )
                if P_effective_Pa > 1e-15:
                    vapor_pressures[species] = P_effective_Pa
                    source_label = (
                        "builtin_authority_limited:"
                        "gas_rail_liquid_oxide_standard_reaction:"
                        "reconstructed_vapor_pressure_segment"
                        if reconstructed_vapor_limit is not None
                        else (
                            "builtin_authoritative:"
                            "gas_rail_liquid_oxide_standard_reaction"
                        )
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f"{source_label}:extrapolated_beyond_valid_range_K"
                        )
                    vapor_pressure_sources[species] = source_label
                else:
                    _sz_below_threshold(species, P_effective_Pa, 'P_effective_Pa')
                continue

            # --- Oxide activity ---                              [ELLI-5]
            if parent_oxide == 'FeO':
                a_oxide = max(
                    0.0,
                    float(
                        feo_activity_diagnostic.get(
                            'a_FeO_authoritative',
                            0.0,
                        )
                        or 0.0
                    ),
                )
                oxide_activity = None
            else:
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                    cation_mol_fraction=cation_mol_fraction,
                    temperature_K=T_K,
                )
                if oxide_activity is None:
                    _sz_activity_skip(species, oxide_activity)
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                a_oxide = oxide_activity.equivalent_parent_activity(
                    n_ox / n_M
                )
            if (
                oxide_activity is None
                and a_oxide <= 1e-10
            ) or (
                oxide_activity is not None
                and oxide_activity.activity <= 1e-10
            ):
                if oxide_activity is None:
                    # FeO path: oxide_activity stays None by construction
                    # above, but a_oxide IS the computed authoritative FeO
                    # activity (kress_calphad_ferrous_feo, clamped >= 0).
                    # Below-threshold here is the same 1e-10 activity skip
                    # used on other metals, tagged cat-3 so it is not
                    # confused with a missing activity model (cat-1). The
                    # skip is not derived from the 1e-15 Pa vapor-pressure
                    # omit; tagging it missing_activity would still launder
                    # a computed activity into a missing-input story.
                    _sz_omit(
                        species,
                        ZeroBecause.PROVEN_BELOW_THRESHOLD,
                        field='a_FeO_authoritative',
                        detail=(
                            f'a_FeO_authoritative={a_oxide!r} <= 1e-10; '
                            'species omitted by the 1e-10 activity skip '
                            '(computed authoritative FeO activity, not missing '
                            'input; skip is not a 1e-15 Pa pressure proof)'
                        ),
                        category=CATEGORY_PROVEN_ZERO,
                    )
                else:
                    _sz_activity_skip(species, oxide_activity)
                continue

            ellingham_extrapolation = ellingham_authority_limit(
                T_K,
                species=species,
                consumer='legacy-equilibrium-fallback',
            )
            if ellingham_extrapolation is not None:
                ellingham_extrapolations[species] = ellingham_extrapolation
                valid_low, valid_high = ellingham_fit_range_K(species)
                if ellingham_extrapolation["authority_status"] == "extrapolation_limited":
                    warnings.append(
                        f"{species} Ellingham JANAF high-T fit extrapolated beyond "
                        f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                        f"{T_K:.2f} K"
                    )

            activities[species] = (
                a_oxide if oxide_activity is None else oxide_activity.activity
            )
            if oxide_activity is not None:
                activity_provenance[species] = oxide_activity.provenance()
            else:
                feo_sources = feo_activity_diagnostic.get('sources')
                activity_provenance[species] = {
                    'activity_evidence_ref': (
                        '; '.join(str(value) for value in feo_sources.values())
                        if isinstance(feo_sources, Mapping)
                        else 'REF-001 Kress & Carmichael 1991'
                    ),
                    'melt_oxide_activity_evidence_tier': 'ANALYTICAL_EXTERNAL_GROUNDED',
                    'melt_oxide_activity_model': 'kress_calphad_ferrous_feo',
                }

            # --- Ellingham decomposition equilibrium ---          [ELLI-1..3]
            #
            # ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)
            dG_f_kJ = ellingham_delta_g_kj_per_mol_o2(
                species,
                T_K,
            )   # sign follows the live row; not always negative

            # K_decomp = exp(ΔG_f / (R × T))
            # ΔG_f in kJ, R in J/(mol·K) → multiply by 1000
            K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))

            # a_M(row basis) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)
            dissociation_pO2_bar = (
                pO2_bar if parent_oxide == 'FeO' else melt_dissociation_pO2_bar
            )
            # Premise: this fallback solves the melt-supported source pressure;
            # the later surface-flux layer owns overhead species backpressure.
            # For MgO(l) -> Mg(g) + 1/2 O2,
            # K1=(f_Mg/p0)*(fO2/p0)^1/2/a_MgO. The per-mol-O2 JANAF row uses
            # K2=K1**2=(f_Mg/p0)**2*(fO2/p0)/a_MgO**2, therefore
            # root=(K2*a_oxide**n_ox/fO2_bar)**(1/n_M), n_ox=n_M=2.
            # Unit check: K, activity, and reduced fugacities are dimensionless;
            # the gas/condensed pressure rail supplies Pa below. Sanity/limit:
            # fO2 down by 100 raises Mg by 10; fO2 -> infinity suppresses Mg.
            # Thus non-FeO uses intrinsic melt fO2, while overhead pO2 remains
            # available to transport/backpressure. FeO already carries melt
            # redox through Kress91 activity and is intentionally unchanged.
            numerator = K_decomp * (a_oxide ** n_ox) / dissociation_pO2_bar

            if numerator <= 0:
                continue

            metal_activity_root = numerator ** (1.0 / n_M)

            # --- Effective vapor pressure ---                     [ELLI-4]
            #
            # Condensed-basis rows produce a Raoultian activity against the
            # pure-component vapor pressure and may saturate at a metal pool.
            # Gas-basis rows already produce f_M/p°; multiplying by P_sat again
            # would double-count the vaporization equilibrium.
            if metal_phase_kind == ELLINGHAM_METAL_PHASE_GAS:
                P_effective_Pa = metal_activity_root * _ELLINGHAM_STANDARD_PRESSURE_PA
            else:
                assert P_reference_Pa is not None
                P_effective_Pa = min(metal_activity_root, 1.0) * P_reference_Pa

            P_effective_Pa = _require_finite_vapor_value(
                P_effective_Pa,
                species=species,
                field="P_effective_Pa",
            )

            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa
                ellingham_limit = ellingham_extrapolations.get(species)
                fit_extrapolated = (
                    ellingham_limit is not None
                    and ellingham_limit["authority_status"] == "extrapolation_limited"
                )
                reconstructed_limited = (
                    ellingham_limit is not None
                    and ellingham_limit["authority_status"]
                    == "reconstructed_limited"
                )
                if gas_standard_rail:
                    base_source = (
                        'builtin_extrapolation_limited'
                        if fit_extrapolated
                        else (
                            'builtin_authority_limited'
                            if reconstructed_limited
                            else 'builtin_authoritative'
                        )
                    )
                    source_label = f'{base_source}:gas_standard_fugacity'
                else:
                    source_label = vapor_pressure_source_label(
                        'builtin_authoritative',
                        sp_data,
                        coefficient_block=coefficient_block,
                        temperature_K=T_K,
                        authority_limited_by_ellingham_fit_range=(
                            fit_extrapolated
                        ),
                    )
                    if reconstructed_limited:
                        source_label = source_label.replace(
                            'builtin_authoritative',
                            'builtin_authority_limited',
                            1,
                        )
                if species in metal_extrapolations:
                    source_label = (
                        f'{source_label}:'
                        'extrapolated_beyond_valid_range_K'
                    )
                if fit_extrapolated:
                    source_label = (
                        f'{source_label}:'
                        'extrapolated_beyond_ellingham_fit_range_K'
                    )
                elif reconstructed_limited:
                    source_label = f'{source_label}:reconstructed_ellingham_segment'
                if reconstructed_vapor_limit is not None:
                    source_label = source_label.replace(
                        'builtin_authoritative',
                        'builtin_authority_limited',
                        1,
                    )
                    source_label = (
                        f'{source_label}:reconstructed_vapor_pressure_segment'
                    )
                vapor_pressure_sources[species] = source_label
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        species,
                        sp_data,
                        pseudo_warning_seen,
                        stacklevel=3,
                    )

            else:
                _sz_below_threshold(species, P_effective_Pa, 'P_effective_Pa')
        # ================================================================
        # OXIDE VAPOR SPECIES (SiO, CrO2)                        [THERMO-8]
        # ================================================================
        #
        # These evaporate as oxide gases, not as metals. Fe is intentionally
        # modeled through the metallic-Fe path above, not as FeO vapor.
        # SiO₂(melt) → SiO(g) + ½O₂(g). Mass action at the calibration
        # reference is p(SiO) ∝ 1/√pO₂, but this loop applies that
        # square-root factor only when pO2_bar > the SiO row's
        # pO2_reference_bar. At and below that reference the pressure is
        # not enhanced; it is flat in pO₂. See the SiO branch below.

        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for name, data in oxide_vapors_data.items():
            antoine = data.get('antoine', {})
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            valid = data.get('valid_range_K', [0, 9999])

            if A > 0 and valid[0] <= T_K <= valid[1]:
                log_P = A - B / (T_K + C)
                P_sat = _pow10_pressure_or_raise(
                    log_P,
                    species=name,
                    field="P_sat",
                )
            else:
                # Out-of-range or A <= 0: omit without a silent-zero note
                # on this path (the `_sz_omit` helpers are not called).
                continue

            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                    cation_mol_fraction=cation_mol_fraction,
                    temperature_K=T_K,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    _sz_activity_skip(name, oxide_activity)
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                a_ox = oxide_activity.equivalent_parent_activity(
                    activity_exponent
                )
                activities[name] = oxide_activity.activity
                activity_provenance[name] = oxide_activity.provenance()
                P_sat = _require_finite_vapor_value(
                    P_sat * max(a_ox, 0.0) ** activity_exponent,
                    species=name,
                    field="P_sat_activity",
                )

            pO2_exponent = float(data.get('pO2_exponent', 0.0) or 0.0)
            if pO2_exponent:
                pO2_reference_bar = max(
                    1e-30, float(data.get('pO2_reference_bar', 1.0) or 1.0)
                )
                P_sat = _require_finite_vapor_value(
                    P_sat * (pO2_bar / pO2_reference_bar) ** pO2_exponent,
                    species=name,
                    field="P_sat_pO2",
                )

            # SiO pO2 correction is piecewise around the calibration
            # reference, not a global 1/sqrt(pO2) law.
            # Premise: SiO2(melt) → SiO(g) + 1/2 O2(g) has
            #   K = p_SiO * pO2^{1/2} / a_SiO2  (reduced pressures).
            # Algebra: p_SiO = K a_SiO2 / sqrt(pO2). Relative to the
            # fitted reference p_ref:
            #   P = P_ref * sqrt(p_ref / pO2)   when pO2 > p_ref.
            # Unit check: p_ref/pO2 is bar/bar, dimensionless.
            # Sanity: 100× higher pO2 above p_ref cuts p_SiO by 10.
            # At pO2 <= p_ref this branch does not run, so p_SiO stays
            # at the reference-calibrated value (no 1/sqrt enhancement).
            # Body vacuum floors below p_ref therefore do not retune
            # the SiO fit; they also do not raise p_SiO.
            sio_reference_bar = vacuum_floor_bar
            if name == 'SiO':
                sio_reference_bar = max(
                    1e-30,
                    float(data.get('pO2_reference_bar', vacuum_floor_bar) or vacuum_floor_bar),
                )
            if (
                name == 'SiO'
                and not pO2_exponent
                and pO2_bar > sio_reference_bar
            ):
                suppression = math.sqrt(sio_reference_bar / pO2_bar)
                P_sat = _require_finite_vapor_value(
                    P_sat * suppression,
                    species=name,
                    field="P_sat_suppressed",
                )

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat
                vapor_pressure_sources[name] = vapor_pressure_source_label(
                    'builtin_authoritative',
                    data,
                    coefficient_block=COEFF_BLOCK_ANTOINE,
                    temperature_K=T_K,
                    authority_limited_by_ellingham_fit_range=(
                        name in ellingham_extrapolations
                    ),
                )
                warn_pseudo_vapor_pressure_fallback(
                    name,
                    data,
                    pseudo_warning_seen,
                    stacklevel=3,
                )

            else:
                _sz_below_threshold(name, P_sat, 'P_sat')
        _eq_diagnostics = {
            'activities_provider': 'internal_analytical_equilibrium',
            'activities_standard_state': {
                'convention': 'raoultian_pure_endmember',
                'phase': 'liquid',
                'reference_pressure_bar': 1.0,
                'reference_temperature_K': None,
                'component_basis': 'raoultian_pure_endmember',
            },
            'activity_provenance': activity_provenance,
            'a_FeO_calphad': feo_activity_diagnostic,
            'ellingham_authority': ellingham_authority_diagnostic(
                ellingham_extrapolations,
                consumer='legacy-equilibrium-fallback',
            ),
            'vapor_pressure_authority': vapor_pressure_authority_diagnostic(
                vapor_pressure_authority_limits,
                consumer='legacy-equilibrium-fallback',
            ),
        }
        # b-149: merge notes collected on the `_sz_omit` paths. This
        # completed path sets status='ok'; invalid T/P already returned
        # out_of_domain above. Oxide-vapor valid_range_K misses remain
        # un-noted on this path.
        if silent_zero_notes:
            merge_notes_into_mapping(_eq_diagnostics, silent_zero_notes)
        return EquilibriumResult(
            temperature_C=self.melt.temperature_C,
            pressure_bar=float(self.melt.p_total_mbar) / 1000.0,
            liquid_fraction=None,
            phase_assemblage_available=False,
            vapor_pressures_Pa=vapor_pressures,
            vapor_pressures_source={
                species: vapor_pressure_sources.get(
                    species,
                    'builtin_authoritative',
                )
                for species in vapor_pressures
            },
            activity_coefficients=activities,
            fO2_log=intrinsic_fO2_log,
            warnings=warnings,
            status='ok',
            diagnostics=_eq_diagnostics,
        )
