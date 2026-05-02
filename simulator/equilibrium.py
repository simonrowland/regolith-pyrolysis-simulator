"""Thermodynamic equilibrium helpers for PyrolysisSimulator."""

from __future__ import annotations

import math

from simulator.state import GAS_CONSTANT


class EquilibriumMixin:
    def _get_equilibrium(self):
        """
        Query the melt backend for thermodynamic equilibrium.

        Returns an EquilibriumResult with phase assemblage,
        activity coefficients, and vapor pressures.  If the
        backend is unavailable, returns a stub result using
        Antoine-equation vapor pressures.
        """
        if self.backend is not None and self.backend.is_available():
            return self.backend.equilibrate(
                temperature_C=self.melt.temperature_C,
                composition_kg=self.melt.composition_kg,
                fO2_log=self.melt.fO2_log,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
            )
        else:
            # Stub: use Antoine equations from vapor_pressures.yaml
            # with activity = 1.0 (ideal approximation)
            return self._stub_equilibrium()

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
    # For the decomposition reaction per mol O₂:
    #   n_ox × oxide(melt) → n_M × Metal(liquid) + O₂(gas)
    #
    # The equilibrium liquid metal activity in the melt is:
    #
    #   a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)          [ELLI-3]
    #
    # The effective metal vapor pressure above the melt is:
    #
    #   P_metal(g) = a_M(l) × P_sat_pure(T)                     [ELLI-4]
    #
    # where P_sat_pure comes from Antoine equation (vapor_pressures.yaml).
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
    # Tuple: (ΔH_f kJ/mol_O₂, ΔS_f kJ/(mol·K), n_M, n_ox)
    #   n_M  = moles of metal per mol O₂ in the decomposition reaction
    #   n_ox = moles of oxide per mol O₂ in the decomposition reaction

    _ELLINGHAM_THERMO = {
        'Na': (-836.0, -0.275, 4, 2),      # 4Na + O₂ → 2Na₂O,  ΔG(1600°C) ≈ -321
        'K':  (-740.0, -0.225, 4, 2),      # 4K  + O₂ → 2K₂O,   ΔG(1600°C) ≈ -319
        'Fe': (-536.0, -0.088, 2, 2),      # 2Fe + O₂ → 2FeO,   ΔG(1600°C) ≈ -371
        'Mn': (-770.0, -0.165, 2, 2),      # 2Mn + O₂ → 2MnO,   ΔG(1600°C) ≈ -461
        'Cr': (-756.0, -0.137, 4/3, 2/3),  # 4/3Cr + O₂ → 2/3Cr₂O₃, ΔG ≈ -499
        'Mg': (-1200.0, -0.198, 2, 2),     # 2Mg + O₂ → 2MgO,   ΔG(1600°C) ≈ -829
        'Ca': (-1270.0, -0.198, 2, 2),     # 2Ca + O₂ → 2CaO,   ΔG(1600°C) ≈ -899
        'Al': (-1120.0, -0.214, 4/3, 2/3), # 4/3Al + O₂ → 2/3Al₂O₃, ΔG ≈ -719
        'Ti': (-945.0, -0.195, 1, 1),      # Ti + O₂ → TiO₂,    ΔG(1600°C) ≈ -580
    }

    def _stub_equilibrium(self):
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

        T_K = self.melt.temperature_C + 273.15
        if T_K < 400:
            return EquilibriumResult(
                temperature_C=self.melt.temperature_C,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
            )

        vapor_pressures = {}
        activities = {}

        # --- Determine the oxygen partial pressure (bar) ---
        #
        # The pO₂ at the melt surface enters the decomposition
        # equilibrium.  We use the highest of:
        #   - Actual overhead O₂ (from gas transport model)
        #   - Campaign setpoint pO₂ (turbine-managed)
        #   - Hard vacuum floor (10⁻⁹ bar ≈ lunar surface)
        pO2_bar = max(
            self.overhead.composition.get('O2', 0.0) / 1000.0,
            self.melt.pO2_mbar / 1000.0,
            1e-9,
        )

        # --- Melt composition for oxide activities ---
        comp_wt = self.melt.composition_wt_pct()

        # ================================================================
        # METAL SPECIES: Ellingham equilibrium + Antoine               [ELLI]
        # ================================================================
        #
        # For each metal, combine the oxide decomposition equilibrium
        # (how much liquid metal is "freed") with the pure-metal
        # vaporization (how much of that liquid metal enters the gas).

        metals_data = self.vapor_pressures.get('metals', {})

        for species, (dH_f, dS_f, n_M, n_ox) in self._ELLINGHAM_THERMO.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue

            # --- Pure-metal P_sat from Antoine ---
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
            # Fe has a slightly lower sublimation pressure.
            antoine = sp_data.get('antoine', {})
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)

            if A > 0 and T_K > 300:
                # Antoine: log10(P_Pa) = A - B / (T_K + C)
                log_P = A - B / (T_K + C)
                P_sat_pure_Pa = 10.0 ** log_P
            else:
                continue

            # --- Oxide activity (wt fraction proxy) ---           [ELLI-5]
            #
            # Without AlphaMELTS, we approximate the oxide activity
            # as the weight fraction.  This is crude but captures the
            # key behaviour: as an oxide depletes, its activity drops
            # and evaporation slows.  Real activities differ significantly
            # (e.g., γ(Na₂O) ≈ 10⁻² in CMAS melts [THERMO-10]), which
            # is why AlphaMELTS is preferred for quantitative work.
            a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
            if a_oxide <= 1e-10:
                continue

            activities[species] = a_oxide

            # --- Ellingham decomposition equilibrium ---          [ELLI-1..3]
            #
            # ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)
            dG_f_kJ = dH_f - T_K * dS_f   # negative (formation favorable)

            # K_decomp = exp(ΔG_f / (R × T))
            # ΔG_f in kJ, R in J/(mol·K) → multiply by 1000
            K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))

            # a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)
            numerator = K_decomp * (a_oxide ** n_ox) / pO2_bar

            if numerator <= 0:
                continue

            a_M_liquid = numerator ** (1.0 / n_M)

            # Clamp to physical range (activity can't exceed 1.0 for
            # a pure substance, and metal pool formation changes regime)
            a_M_liquid = min(a_M_liquid, 1.0)

            # --- Effective vapor pressure ---                     [ELLI-4]
            #
            # P_metal = a_M(l) × P_sat_pure(T)
            P_effective_Pa = a_M_liquid * P_sat_pure_Pa

            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa

        # ================================================================
        # OXIDE VAPOR SPECIES (SiO, FeO_vapor)                   [THERMO-8]
        # ================================================================
        #
        # These evaporate as oxide gases, not as metals.
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
                P_sat = 10.0 ** log_P
            else:
                continue

            # Oxide activity proxy (weight fraction)
            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                a_ox = comp_wt.get(parent_oxide, 0.0) / 100.0
                activities[name] = a_ox
                P_sat *= max(a_ox, 0.0)

            # SiO suppression by pO₂: p(SiO) ∝ 1/√pO₂         [THERMO-8]
            #
            # The Antoine equation gives P_SiO at hard vacuum
            # (pO₂ ≈ 10⁻⁹ bar).  At higher pO₂, the equilibrium
            # shifts toward SiO₂, suppressing SiO vapor:
            #   At 10⁻⁹ bar:  suppression = 1.0  (reference)
            #   At 10⁻⁶ bar:  suppression ≈ 0.032 (31× suppression)
            #   At 10⁻³ bar:  suppression ≈ 0.001 (1000× suppression)
            if name == 'SiO' and pO2_bar > 1e-9:
                suppression = math.sqrt(1e-9 / pO2_bar)
                P_sat *= suppression

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat

        return EquilibriumResult(
            temperature_C=self.melt.temperature_C,
            pressure_bar=self.melt.p_total_mbar / 1000.0,
            vapor_pressures_Pa=vapor_pressures,
            activity_coefficients=activities,
            fO2_log=math.log10(max(pO2_bar, 1e-20)),
        )
