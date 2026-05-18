"""Builtin VAPOR_PRESSURE provider (Antoine + Ellingham).

Kernel-registered provider that originally owned the ``VAPOR_PRESSURE``
intent (goal #7 ``BUILTIN-ENGINE-EXTRACTION``) and was demoted to the
**fallback** slot under goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``.
:class:`engines.vaporock.provider.VapoRockProvider` is now the
authoritative provider; the kernel consults this builtin only when
VapoRock is unavailable AND the simulator was constructed with
``allow_fallback_vapor=True`` (the flag is read at
:meth:`PyrolysisSimulator.__init__` time and threaded into
:class:`ChemistryKernel.allow_fallback_intents`).

The provider:

- reads ``process.cleaned_melt`` from the account view (the only
  account it declares),
- looks up Antoine coefficients from the ``vapor_pressures.yaml``
  payload passed at construction time,
- combines Ellingham oxide-decomposition equilibrium with pure-metal
  Antoine vaporization to compute per-species saturation pressures at
  the request's ``temperature_C`` and the caller-supplied commanded
  ``pO2_bar`` (via ``control_inputs``),
- returns an :class:`IntentResult` with ``transition=None``
  (diagnostic; VAPOR_PRESSURE owns no ledger mutation -- that belongs
  to ``EVAPORATION_TRANSITION``) and a ``vapor_pressures_Pa``
  diagnostic.

The :class:`CapabilityProfile` still declares the intent as
authority-capable so the registry will accept this provider in the
fallback slot (a fallback that is not authority-capable would only
produce diagnostic shadow output -- legal but useless as a real
backup).  Registry slot vs. capability is intentionally separate: the
profile says "I CAN be authoritative"; the kernel wiring decides
whether this build session actually uses this provider as the
authority or as fallback.

Account declaration: ``process.cleaned_melt`` only.  The provider must
not see gas / metal / sulfide / salt accounts -- the kernel filter
enforces this.  Mirrors the same constraint AlphaMELTS has (binding
spec §7).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    composition_wt_pct_from_account_view,
    diagnostic_control_audit,
    reject_wrong_intent,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider


# Mirrors EquilibriumMixin._ELLINGHAM_THERMO -- the canonical table.
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
    """Fallback ``VAPOR_PRESSURE`` provider (Antoine + Ellingham).

    See module docstring.  Originally registered as authoritative
    under goal #7 and demoted to the fallback slot under goal #10
    when VapoRock took over the authoritative role.  The provider
    still declares VAPOR_PRESSURE in
    :attr:`CapabilityProfile.is_authoritative_for` so the registry's
    fallback slot accepts it (an authority-capable provider sitting
    in the fallback slot can take over the authoritative role
    cleanly when VapoRock is unavailable and the simulator opted in
    via ``allow_fallback_vapor=True``).

    ``vapor_pressure_data`` is the parsed
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
        # Lazy import: simulator.state pulls in simulator/__init__ which
        # re-enters this module during package init -- see
        # engines/builtin/__init__.py for the cycle description.
        from simulator.state import GAS_CONSTANT

        wrong_intent = reject_wrong_intent(request, ChemistryIntent.VAPOR_PRESSURE)
        if wrong_intent is not None:
            return wrong_intent

        # The Antoine + Ellingham math runs verbatim against the request's
        # T/P/fO2 with no independent feedback. Audit reports applied ==
        # requested with the diagnostic-only note documented in
        # diagnostic_control_audit.
        control_audit = diagnostic_control_audit(request)

        T_C = request.temperature_C
        T_K = T_C + 273.15
        if T_K < 400:
            # Mirrors _stub_equilibrium: below 400 K, no significant
            # evaporation. Return an empty vapor-pressure dict with an
            # 'ok' status -- this is a converged outcome, not a failure.
            return IntentResult(
                intent=ChemistryIntent.VAPOR_PRESSURE,
                status="ok",
                control_audit=control_audit,
                diagnostic={"vapor_pressures_Pa": {}, "activities": {}},
            )

        pO2_bar = self._resolve_pO2_bar(request)
        comp_wt = composition_wt_pct_from_account_view(
            request.account_view, self.DECLARED_ACCOUNT
        )

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
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                P_sat *= max(a_ox, 0.0) ** activity_exponent

            pO2_exponent = float(data.get('pO2_exponent', 0.0) or 0.0)
            if pO2_exponent:
                pO2_reference_bar = max(
                    1e-30, float(data.get('pO2_reference_bar', 1.0) or 1.0)
                )
                P_sat *= (pO2_bar / pO2_reference_bar) ** pO2_exponent

            # SiO suppression by pO2: p(SiO) ~ 1/sqrt(pO2). Reference is
            # 1e-9 bar (lunar hard vacuum).
            if name == 'SiO' and not pO2_exponent and pO2_bar > 1e-9:
                suppression = math.sqrt(1e-9 / pO2_bar)
                P_sat *= suppression

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat

        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            transition=None,
            control_audit=control_audit,
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
