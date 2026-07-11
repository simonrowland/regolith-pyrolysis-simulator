"""Thermodynamic equilibrium helpers for PyrolysisSimulator."""

from __future__ import annotations

import math

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_GAS,
    ELLINGHAM_THERMO as _CANONICAL_ELLINGHAM_THERMO,
    ellingham_authority_diagnostic,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_extrapolation,
    ellingham_fit_range_K,
    ellingham_metal_phase_kind,
    ellingham_stoichiometry,
)
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.fe_redox import (
    calphad_ferrous_feo_activity_diagnostic,
    kress91_furnace_activity_pressure_bar,
    kress91_ferrous_feo_activity,
)
from simulator.environment import vacuum_floor_bar_for_environment
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import GAS_CONSTANT, MOLAR_MASS, Atmosphere

# Atmosphere modes where a turbine/bleed loop actively holds a commanded pO₂
# setpoint. Only in these modes may the setpoint act as a floor on the
# effective pO₂ -- an uncontrolled hard-vacuum / pN₂ run must not get a
# synthetic O₂ floor.
_O2_CONTROLLED_ATMOSPHERES = frozenset({
    Atmosphere.CONTROLLED_O2,
    Atmosphere.CONTROLLED_O2_FLOW,
    Atmosphere.O2_BACKPRESSURE,
})
_ELLINGHAM_STANDARD_PRESSURE_PA = 100000.0

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

        Toggle-off preserves the legacy commanded-pO₂ path. Toggle-on reads
        the finite-headspace O₂ partial pressure from the
        OVERHEAD_GAS_EQUILIBRIUM diagnostic provider.

        Resolution:
          - ``overhead.composition['O2']`` is itself
            ``max(gas O2, melt.pO2_mbar)`` written by ``overhead.py`` --
            structurally the commanded setpoint, not a tracked gas
            inventory.  (The melt-evaporation O₂ coproduct is credited to
            ``terminal.oxygen_melt_offgas_stored``, never to
            ``process.overhead_gas``, and ``process.overhead_gas`` is
            drained to ``terminal.offgas`` every tick.)
          - The commanded setpoint (``melt.pO2_mbar``) is applied again as
            an explicit *floor*, and only when the atmosphere is an
            actively O₂-controlled mode (turbine + bleed holding the
            setpoint).  An uncontrolled HARD_VACUUM / PN2_SWEEP run gets no
            synthetic floor -- its effective pO₂ collapses to the
              numerical vacuum floor below for the whole campaign.
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
            # via 1/sqrt(pO2). Uncontrolled HARD_VACUUM / PN2_SWEEP runs get
            # NO synthetic floor — they collapse to the environment floor.
            if self.melt.atmosphere in _O2_CONTROLLED_ATMOSPHERES:
                pO2_bar = max(pO2_bar, self.melt.pO2_mbar / 1000.0)
            return max(pO2_bar, self._vacuum_floor_bar())

        pO2_bar = self.overhead.composition.get('O2', 0.0) / 1000.0
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
    #   K = exp(ΔG_f / (R × T))   [K < 1 since ΔG_f < 0]       [ELLI-2]
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
    # This naturally captures the full Ellingham hierarchy:
    #   Na, K (volatile, weak oxides):   high P_metal → easy pyrolysis
    #   Fe, Mn, Cr (moderate oxides):    P_metal depends on T and pO₂
    #   Mg (refractory):                 significant only at high T, low pO₂
    #   Ca, Al, Ti (very refractory):    negligible P_metal → need MRE/thermite
    #
    # Data: NIST-JANAF Thermochemical Tables, Kubaschewski et al.
    # Cross-verified against setpoints.yaml Ellingham values at 1600°C.
    #
    # Vapor-pressure convention contract (`data/vapor_pressures.yaml`):
    # - Metals with `fit_target: pure_component_psat` have raw Antoine
    #   evaluated as `P_sat_pure`, then multiplied by Ellingham `a_M` --
    #   single-counted.
    # - Metals with `fit_target: pseudo_psat_backsolved_from_vaporock` have raw
    #   Antoine evaluated as a pseudo-standard term such that
    #   `a_M * 10^(A-B/T) ~= VapoRock_partial_pressure` on the calibration
    #   grid. The convention is single-counted by construction but assumes
    #   proximity to that grid.
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
               K = exp(ΔG_f / (R × T))   [< 1 since ΔG_f < 0]

        3. Solve for equilibrium liquid metal activity:           [ELLI-3]
               a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)

        4. Get pure-metal vapor pressure from Antoine:
               P_sat = 10^(A − B/(T+C))   (Pa)

        5. Effective vapor pressure above the oxide melt:         [ELLI-4]
               P_metal = a_M(l) × P_sat

        This correctly captures:
        - Temperature dependence of BOTH oxide stability AND metal
          volatility (the two factors that control pyrolysis yield).
        - pO₂ dependence: higher pO₂ pushes equilibrium toward oxide,
          suppressing metal vapor.  This is the physics behind pO₂-
          managed campaigns (C2B, C3, C4).
        - Composition dependence: as an oxide is depleted, its activity
          drops and evaporation rate decreases.
        - The full Ellingham hierarchy emerges naturally:
            Na, K   → ΔG_f ≈ −320 kJ → high P_metal (easy pyrolysis)
            Fe      → ΔG_f ≈ −370 kJ → moderate P_metal (C2A/C2B target)
            Mn, Cr  → ΔG_f ≈ −460..−500 kJ → minor byproducts
            Mg      → ΔG_f ≈ −830 kJ → significant only at very high T
            Ca, Al  → ΔG_f ≈ −720..−900 kJ → negligible (need MRE/thermite)

        SiO vapor uses a separate equilibrium pathway because it
        evaporates as an oxide gas (SiO₂ → SiO + ½O₂), not as a
        metal.  The Antoine equation + √pO₂ correction is used.  [THERMO-8]
        """
        from simulator.melt_backend.base import EquilibriumResult
        from engines.builtin.vapor_pressure import (
            COEFF_BLOCK_ANTOINE,
            FIT_TARGET_PSEUDO_VAPOROCK,
            FIT_TARGET_STANDARD_REACTION,
            _is_noncertifying_pseudo_vapor_pressure_runtime,
            _metadata_value,
            _pow10_pressure_or_raise,
            _require_finite_vapor_value,
            reject_noncertifying_vapor_pressure_row,
            vapor_pressure_source_label,
            vapor_pressure_antoine_coefficients,
            vapor_pressure_valid_range_K,
            warn_pseudo_vapor_pressure_fallback,
        )

        T_K = self.melt.temperature_C + CELSIUS_TO_KELVIN_OFFSET
        if T_K < 400:
            # Builtin path ran and correctly found no significant
            # evaporation below 400 K - a converged 'ok' outcome, not a
            # failure or an unavailable engine.
            return EquilibriumResult(
                temperature_C=self.melt.temperature_C,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
                liquid_fraction=None,
                phase_assemblage_available=False,
                status='ok',
            )

        vapor_pressures = {}
        vapor_pressure_sources = {}
        activities = {}
        metal_extrapolations = {}
        ellingham_extrapolations = {}
        warnings = []
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
        try:
            melt_dissociation_pO2_bar = 10.0 ** intrinsic_fO2_log
        except OverflowError:
            melt_dissociation_pO2_bar = 1e300
        melt_dissociation_pO2_bar = min(
            max(melt_dissociation_pO2_bar, 1e-30),
            1e300,
        )
        feo_activity_pressure_bar = kress91_furnace_activity_pressure_bar(
            floor_bar=self._vacuum_floor_bar(),
        )

        # --- Melt composition for oxide activities ---
        comp_wt = self.melt.composition_wt_pct()
        atom_ledger = getattr(self, "atom_ledger", None)
        mol_by_account = getattr(atom_ledger, "mol_by_account", None)
        if callable(mol_by_account):
            melt_account_mol = dict(mol_by_account("process.cleaned_melt") or {})
        else:
            melt_account_mol = {
                oxide: float(wt_pct) / MOLAR_MASS[oxide] * 1000.0
                for oxide, wt_pct in comp_wt.items()
                if oxide in MOLAR_MASS and float(wt_pct) > 0.0
            }
        feo_activity_diagnostic = calphad_ferrous_feo_activity_diagnostic(
            comp_wt=comp_wt,
            fO2_log=intrinsic_fO2_log,
            T_K=T_K,
            pressure_bar=feo_activity_pressure_bar,
            floor_bar=self._vacuum_floor_bar(),
        )

        # ================================================================
        # METAL SPECIES: Ellingham equilibrium + Antoine               [ELLI]
        # ================================================================
        #
        # For each metal, combine the oxide decomposition equilibrium
        # (how much liquid metal is "freed") with an Antoine reference term.
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

            # --- Antoine reference pressure ---
            #
            # We extrapolate the Clausius-Clapeyron equation beyond its
            # validated range because:
            #   1. The form log10(P) = A - B/T is physically meaningful
            #      (Clausius-Clapeyron) even below the metal melting point
            #   2. The Ellingham K_decomp already provides the dominant
            #      physical constraint (K → 0 at low T), so extrapolation
            #      of P_sat introduces only a minor secondary error
            #   3. At low T, the product a_M × P_sat is negligible anyway
            #      because K_decomp is extremely small
            #
            # For Fe (mp 1538°C = 1811K), this allows computing meaningful
            # vapor pressures at 1400-1538°C where FeO decomposition in
            # the silicate melt IS physically real, even though pure solid
            # Fe has a slightly lower sublimation pressure. That
            # pure-component rationale applies only to
            # fit_target=pure_component_psat rows.
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

            if A > 0 and T_K > 300:
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
                            f"{T_K:.2f} K"
                        )
                log_P = A - B / (T_K + C)
                P_reference_Pa = _pow10_pressure_or_raise(
                    log_P,
                    species=species,
                    field="P_reference_Pa",
                )
            else:
                continue

            if str(sp_data.get("fit_target", "") or "") == FIT_TARGET_STANDARD_REACTION:
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    continue
                activities[species] = oxide_activity.activity

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
                continue

            # --- Oxide activity ---                              [ELLI-5]
            if parent_oxide == 'FeO':
                a_oxide = kress91_ferrous_feo_activity(
                    comp_wt=comp_wt,
                    fO2_log=intrinsic_fO2_log,
                    T_K=T_K,
                    pressure_bar=feo_activity_pressure_bar,
                )
                oxide_activity = None
            else:
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                )
                if oxide_activity is None:
                    continue
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
                continue

            ellingham_extrapolation = ellingham_fit_extrapolation(
                T_K,
                species=species,
                consumer='legacy-equilibrium-fallback',
            )
            if ellingham_extrapolation is not None:
                ellingham_extrapolations[species] = ellingham_extrapolation
                valid_low, valid_high = ellingham_fit_range_K(species)
                warnings.append(
                    f"{species} Ellingham JANAF high-T fit extrapolated beyond "
                    f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                    f"{T_K:.2f} K"
                )

            activities[species] = (
                a_oxide if oxide_activity is None else oxide_activity.activity
            )

            # --- Ellingham decomposition equilibrium ---          [ELLI-1..3]
            #
            # ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)
            dG_f_kJ = ellingham_delta_g_kj_per_mol_o2(
                species,
                T_K,
            )   # negative (formation favorable)

            # K_decomp = exp(ΔG_f / (R × T))
            # ΔG_f in kJ, R in J/(mol·K) → multiply by 1000
            K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))

            # a_M(row basis) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)
            dissociation_pO2_bar = (
                pO2_bar if parent_oxide == 'FeO' else melt_dissociation_pO2_bar
            )
            # Melt-dissolved non-FeO oxide dissociation sees the melt's
            # oxygen chemical potential; headspace pO2 remains reserved for
            # gas transport/backpressure. FeO already carries melt redox
            # through its Kress91 activity and is intentionally unchanged.
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
            if ellingham_metal_phase_kind(species, T_K) == ELLINGHAM_METAL_PHASE_GAS:
                P_effective_Pa = metal_activity_root * _ELLINGHAM_STANDARD_PRESSURE_PA
            else:
                P_effective_Pa = min(metal_activity_root, 1.0) * P_reference_Pa

            P_effective_Pa = _require_finite_vapor_value(
                P_effective_Pa,
                species=species,
                field="P_effective_Pa",
            )

            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa
                source_label = vapor_pressure_source_label(
                    'builtin_authoritative',
                    sp_data,
                    coefficient_block=coefficient_block,
                    temperature_K=T_K,
                    authority_limited_by_ellingham_fit_range=(
                        species in ellingham_extrapolations
                    ),
                )
                if species in metal_extrapolations:
                    source_label = (
                        f'{source_label}:'
                        'extrapolated_beyond_valid_range_K'
                    )
                if species in ellingham_extrapolations:
                    source_label = (
                        f'{source_label}:'
                        'extrapolated_beyond_ellingham_fit_range_K'
                    )
                vapor_pressure_sources[species] = source_label
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        species,
                        sp_data,
                        pseudo_warning_seen,
                        stacklevel=3,
                    )

        # ================================================================
        # OXIDE VAPOR SPECIES (SiO, CrO2)                        [THERMO-8]
        # ================================================================
        #
        # These evaporate as oxide gases, not as metals. Fe is intentionally
        # modeled through the metallic-Fe path above, not as FeO vapor.
        # SiO₂(melt) → SiO(g) + ½O₂(g), with p(SiO) ∝ 1/√pO₂.
        # The Antoine equation gives the reference vapor pressure,
        # then the √pO₂ suppression and oxide activity are applied.

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
                continue

            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                oxide_activity = melt_oxide_activity(
                    parent_oxide,
                    melt_account_mol,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    continue
                a_ox = oxide_activity.equivalent_parent_activity(
                    activity_exponent
                )
                activities[name] = oxide_activity.activity
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

            # SiO suppression by pO₂: p(SiO) ∝ 1/√pO₂         [THERMO-8]
            #
            # The Antoine equation gives P_SiO at the environment vacuum
            # floor.  At higher pO₂, the equilibrium
            # shifts toward SiO₂, suppressing SiO vapor:
            #   At floor:     suppression = 1.0  (reference)
            #   At 10^-6 bar: suppression follows sqrt(floor / pO2)
            vacuum_floor_bar = self._vacuum_floor_bar()
            if (
                name == 'SiO'
                and not pO2_exponent
                and pO2_bar > vacuum_floor_bar
            ):
                suppression = math.sqrt(vacuum_floor_bar / pO2_bar)
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

        return EquilibriumResult(
            temperature_C=self.melt.temperature_C,
            pressure_bar=self.melt.p_total_mbar / 1000.0,
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
            diagnostics={
                'a_FeO_calphad': feo_activity_diagnostic,
                'ellingham_authority': ellingham_authority_diagnostic(
                    ellingham_extrapolations,
                    consumer='legacy-equilibrium-fallback',
                ),
            },
        )
