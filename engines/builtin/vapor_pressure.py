"""Builtin VAPOR_PRESSURE provider (Antoine + Ellingham).

Kernel-registered provider that owns the ``VAPOR_PRESSURE`` intent. Mirrors
the math in :meth:`simulator.equilibrium.EquilibriumMixin._stub_equilibrium`
exactly — this is a refactor of where the computation lives, not a
re-derivation of how it works. The provider:

- reads ``process.cleaned_melt`` from the account view (the only account it
  declares),
- looks up Antoine coefficients from the ``vapor_pressures.yaml`` payload
  passed at construction time,
- combines Ellingham oxide-decomposition equilibrium with pure-metal
  Antoine vaporization to compute per-species saturation pressures at the
  request's ``temperature_C`` and the caller-supplied commanded ``pO2_bar``
  (via ``control_inputs``),
- returns an :class:`IntentResult` with ``transition=None`` (diagnostic;
  VAPOR_PRESSURE owns no ledger mutation — that belongs to
  ``EVAPORATION_TRANSITION``) and a ``vapor_pressures_Pa`` diagnostic.

Authority: authoritative for ``VAPOR_PRESSURE`` per binding spec §3 until
``\\goal VAPOROCK-AUTHORITY-PROMOTION`` (#10) lands.

Account declaration: ``process.cleaned_melt`` only. The provider must not
see gas / metal / sulfide / salt accounts — the kernel filter enforces
this. Mirrors the same constraint AlphaMELTS will have (binding spec §7).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider

# simulator.accounting.formulas + simulator.state are lazy-imported in
# the methods below. Importing them at module load would create a cycle:
# simulator/__init__.py -> simulator.core -> engines.builtin.vapor_pressure
# -> simulator.accounting.formulas (which goes through simulator/__init__).


# Mirrors EquilibriumMixin._ELLINGHAM_THERMO — the canonical table.
# Tuple: (dH_f kJ/mol_O2, dS_f kJ/(mol*K), n_M, n_ox)
_ELLINGHAM_THERMO: dict[str, tuple[float, float, float, float]] = {
    'Na': (-836.0, -0.275, 4, 2),
    'K':  (-740.0, -0.225, 4, 2),
    'Fe': (-536.0, -0.088, 2, 2),
    'Mn': (-770.0, -0.165, 2, 2),
    'Cr': (-756.0, -0.137, 4/3, 2/3),
    'Mg': (-1200.0, -0.198, 2, 2),
    'Ca': (-1270.0, -0.198, 2, 2),
    'Al': (-1120.0, -0.214, 4/3, 2/3),
    'Ti': (-945.0, -0.195, 1, 1),
}


class BuiltinVaporPressureProvider(ChemistryProvider):
    """Authoritative ``VAPOR_PRESSURE`` provider (Antoine + Ellingham).

    See module docstring. ``vapor_pressure_data`` is the parsed
    ``data/vapor_pressures.yaml`` payload (keys: ``metals``,
    ``oxide_vapors``).
    """

    name = "builtin-vapor-pressure"

    DECLARED_ACCOUNT = "process.cleaned_melt"

    def __init__(
        self,
        vapor_pressure_data: Mapping[str, Any],
    ) -> None:
        self._vapor_pressure_data = dict(vapor_pressure_data or {})

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-vapor-pressure",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import to break the package-init cycle described in this
        # module's header.
        from simulator.state import GAS_CONSTANT

        if request.intent is not ChemistryIntent.VAPOR_PRESSURE:
            # Defence in depth: the registry shouldn't route a non-VP
            # intent to this provider, but if it does, surface that as a
            # planner-level unsupported result rather than a silent
            # mis-answer.
            return IntentResult(
                intent=request.intent,
                status="unsupported",
                diagnostic={"reason": f"provider only serves {ChemistryIntent.VAPOR_PRESSURE.value!r}"},
            )

        T_C = request.temperature_C
        T_K = T_C + 273.15
        if T_K < 400:
            # Mirrors _stub_equilibrium: below 400 K, no significant
            # evaporation. Return an empty vapor-pressure dict with an
            # 'ok' status — this is a converged outcome, not a failure.
            return IntentResult(
                intent=ChemistryIntent.VAPOR_PRESSURE,
                status="ok",
                diagnostic={"vapor_pressures_Pa": {}, "activities": {}},
            )

        pO2_bar = self._resolve_pO2_bar(request)
        comp_wt = self._composition_wt_pct_from_view(request.account_view)

        vapor_pressures: dict[str, float] = {}
        activities: dict[str, float] = {}

        metals_data = self._vapor_pressure_data.get('metals', {}) or {}
        for species, (dH_f, dS_f, n_M, n_ox) in _ELLINGHAM_THERMO.items():
            sp_data = metals_data.get(species, {}) or {}
            if not sp_data:
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue

            antoine = sp_data.get('antoine', {}) or {}
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            if not (A > 0 and T_K > 300):
                continue
            log_P = A - B / (T_K + C)
            P_sat_pure_Pa = 10.0 ** log_P

            a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
            if a_oxide <= 1e-10:
                continue

            activities[species] = a_oxide

            # Ellingham: dG_f(T) = dH_f - T * dS_f (kJ/mol O2)
            dG_f_kJ = dH_f - T_K * dS_f
            # K_decomp = exp(dG_f * 1000 / (R * T))
            K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))
            numerator = K_decomp * (a_oxide ** n_ox) / pO2_bar
            if numerator <= 0:
                continue

            a_M_liquid = numerator ** (1.0 / n_M)
            a_M_liquid = min(a_M_liquid, 1.0)
            P_effective_Pa = a_M_liquid * P_sat_pure_Pa
            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa

        oxide_vapors_data = self._vapor_pressure_data.get('oxide_vapors', {}) or {}
        for name, data in oxide_vapors_data.items():
            antoine = (data or {}).get('antoine', {}) or {}
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            valid = data.get('valid_range_K', [0, 9999])
            if not (A > 0 and valid[0] <= T_K <= valid[1]):
                continue
            log_P = A - B / (T_K + C)
            P_sat = 10.0 ** log_P

            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                a_ox = comp_wt.get(parent_oxide, 0.0) / 100.0
                activities[name] = a_ox
                P_sat *= max(a_ox, 0.0)

            # SiO suppression by pO2: p(SiO) ~ 1/sqrt(pO2). Reference is
            # 1e-9 bar (lunar hard vacuum).
            if name == 'SiO' and pO2_bar > 1e-9:
                suppression = math.sqrt(1e-9 / pO2_bar)
                P_sat *= suppression

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat

        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            transition=None,
            diagnostic={
                "vapor_pressures_Pa": vapor_pressures,
                "activities": activities,
                "pO2_bar": pO2_bar,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_pO2_bar(self, request: IntentRequest) -> float:
        """Pick up the commanded pO2 (bar) from the caller.

        The kernel's standard ``fO2_log`` channel carries the absolute
        log10(fO2/bar); the simulator computes the commanded pO2 in
        :meth:`EquilibriumMixin._commanded_pO2_bar` and passes it through
        ``control_inputs['pO2_bar']`` to keep parity with the legacy
        ``_stub_equilibrium`` (which uses that value directly, not the
        fO2_log channel). If neither is supplied, fall back to the
        numerical vacuum floor.
        """

        pO2 = request.control_inputs.get('pO2_bar') if request.control_inputs else None
        if pO2 is not None:
            return max(float(pO2), 1e-9)
        if request.fO2_log is not None:
            return max(10.0 ** float(request.fO2_log), 1e-9)
        return 1e-9

    def _composition_wt_pct_from_view(self, view) -> dict[str, float]:
        """Convert the cleaned-melt mol view into weight-percent oxides.

        Mirrors :meth:`MeltState.composition_wt_pct` so the activity proxy
        (a_oxide = wt fraction) matches the legacy
        :meth:`_stub_equilibrium` exactly.
        """

        # Lazy imports to break the package-init cycle (see module header).
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.state import OXIDE_SPECIES

        species_mol = dict(view.accounts.get(self.DECLARED_ACCOUNT, {}) or {})
        registry = view.species_formula_registry
        kg_by_species: dict[str, float] = {}
        total_kg = 0.0
        for species, mol in species_mol.items():
            try:
                formula = resolve_species_formula(species, registry)
            except Exception:
                # An unknown species in the registry means we can't weight
                # it — skip rather than crash, matching the legacy path's
                # tolerance for unconventional species in cleaned_melt.
                continue
            mass_kg = float(mol) * formula.molar_mass_kg_per_mol()
            if mass_kg <= 0.0:
                continue
            kg_by_species[species] = kg_by_species.get(species, 0.0) + mass_kg
            total_kg += mass_kg

        comp_wt: dict[str, float] = {sp: 0.0 for sp in OXIDE_SPECIES}
        if total_kg <= 0.0:
            return comp_wt
        for species, kg in kg_by_species.items():
            # Only the OXIDE_SPECIES list ends up in composition_wt_pct().
            # Other ledger material in cleaned_melt (rare) contributes to
            # the total but not to oxide activity — same as the legacy
            # MeltState.composition_wt_pct over OXIDE_SPECIES.
            if species in OXIDE_SPECIES:
                comp_wt[species] = (kg / total_kg) * 100.0
        return comp_wt
