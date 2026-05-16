"""Builtin OVERHEAD_GAS_EQUILIBRIUM provider."""

from __future__ import annotations

from engines.builtin._common import (
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider


class BuiltinOverheadGasEquilibriumProvider(ChemistryProvider):
    """Read-only finite-headspace pressure diagnostic."""

    name = "builtin-overhead-gas-equilibrium"
    DECLARED_ACCOUNT = "process.overhead_gas"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM}
            ),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM
        )
        if wrong_intent is not None:
            return wrong_intent

        control_audit = diagnostic_control_audit(request, include_fO2=False)
        controls = unpack_controls(request)
        volume_m3 = max(0.0, float(controls.get("headspace_volume_m3") or 0.0))
        temperature_K = max(
            0.0,
            float(
                controls.get("headspace_temperature_K")
                or request.temperature_C + 273.15
            ),
        )
        holdup_mol = dict(
            request.account_view.accounts.get(self.DECLARED_ACCOUNT, {}) or {}
        )

        partials = self.compute_partial_pressures_bar(
            holdup_mol, volume_m3, temperature_K
        )
        return IntentResult(
            intent=ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "partial_pressures_bar": partials,
                "p_total_bar": sum(partials.values()),
                "p_O2_bar": partials.get("O2", 0.0),
                "n_total_mol": sum(
                    max(0.0, float(v)) for v in holdup_mol.values()
                ),
                "headspace_volume_m3": volume_m3,
                "headspace_temperature_K": temperature_K,
            },
        )

    @staticmethod
    def compute_partial_pressures_bar(
        holdup_mol: dict[str, float],
        volume_m3: float,
        temperature_K: float,
    ) -> dict[str, float]:
        if volume_m3 <= 0.0 or temperature_K <= 0.0:
            return {}
        from simulator.state import GAS_CONSTANT

        scale = GAS_CONSTANT * temperature_K / (volume_m3 * 1.0e5)
        return {
            str(species): max(0.0, float(mol)) * scale
            for species, mol in dict(holdup_mol or {}).items()
            if max(0.0, float(mol)) > 0.0
        }
