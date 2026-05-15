"""Builtin EVAPORATION_FLUX provider (Hertz-Knudsen-Langmuir).

Kernel-registered provider that owns the ``EVAPORATION_FLUX`` intent.
Mirrors the kinetic flux math in
:meth:`simulator.evaporation.EvaporationMixin._calculate_evaporation`
exactly -- this is a refactor of where the per-species flux dict is
computed, not a re-derivation of how the Hertz-Knudsen-Langmuir equation
works. The provider:

- reads ``process.cleaned_melt`` from the account view (only declared
  account; satisfies binding-spec §4 even though the flux math operates
  on caller-supplied auxiliary data),
- reads T from ``request.temperature_C``,
- reads per-species vapor pressures via
  ``request.control_inputs['vapor_pressures_Pa']`` -- caller passes them
  (the kernel has already produced them via the VAPOR_PRESSURE intent in
  the same tick; the provider does NOT call the kernel recursively, that
  would couple intents inside a provider),
- reads per-species overhead partials via
  ``request.control_inputs['overhead_partials_Pa']``,
- reads melt surface area, stir factor, evaporation coefficient via
  ``control_inputs['melt_surface_area_m2']``,
  ``control_inputs['stir_factor']``, ``control_inputs['alpha']``,
- reads per-species stoichiometry and available parent-oxide mass via
  ``control_inputs['stoich_by_species']`` and
  ``control_inputs['available_oxide_kg']`` (precomputed by the caller --
  see :meth:`EvaporationMixin._evaporation_stoich` for the source; the
  provider cannot call instance methods, so the caller serialises the
  stoich map into the request),
- reads per-species molar masses via
  ``control_inputs['molar_mass_kg_mol']`` (caller pulls these from the
  same ``vapor_pressures.yaml`` payload the legacy path uses).

Returns an :class:`IntentResult` with ``transition=None`` (kinetic flux
is a *diagnostic* per binding spec §3; the atom-conserving ledger
transition belongs to the separate ``EVAPORATION_TRANSITION`` intent --
not yet migrated) and an ``evaporation_flux_kg_hr`` diagnostic dict.

Authority: authoritative for ``EVAPORATION_FLUX`` per binding spec §3
until a future Hertz-Knudsen replacement promotes a new provider.

Account declaration: ``process.cleaned_melt`` only -- the same account
the VAPOR_PRESSURE provider declares, and the same one the legacy
:meth:`_calculate_evaporation` mutates downstream via
``_credit_evaporation_transition``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider

# simulator.state is lazy-imported inside dispatch() to break the cycle
# simulator/__init__.py -> simulator.core -> engines.builtin.evaporation_flux
# -> simulator.state. The VAPOR_PRESSURE provider follows the same pattern.


class BuiltinEvaporationFluxProvider(ChemistryProvider):
    """Authoritative ``EVAPORATION_FLUX`` provider (Hertz-Knudsen-Langmuir).

    See module docstring. The provider is stateless -- every per-call
    input arrives through :class:`IntentRequest.control_inputs` so the
    same instance can serve every campaign / tick without holding
    simulator state.
    """

    name = "builtin-evaporation-flux"

    DECLARED_ACCOUNT = "process.cleaned_melt"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-evaporation-flux",
            intents=frozenset({ChemistryIntent.EVAPORATION_FLUX}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_FLUX}),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import to break the package-init cycle described in the
        # module header.
        from simulator.state import GAS_CONSTANT, MOLAR_MASS

        if request.intent is not ChemistryIntent.EVAPORATION_FLUX:
            # Defence in depth -- the registry shouldn't route a non-FLUX
            # intent here, but surface it cleanly if it ever does.
            return IntentResult(
                intent=request.intent,
                status="unsupported",
                diagnostic={
                    "reason": (
                        f"provider only serves "
                        f"{ChemistryIntent.EVAPORATION_FLUX.value!r}"
                    ),
                },
            )

        T_C = request.temperature_C
        T_K = T_C + 273.15
        if T_K < 400:
            # Mirrors _calculate_evaporation: below 400 K, no significant
            # evaporation -- return an empty flux dict with ok status.
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="ok",
                diagnostic={"evaporation_flux_kg_hr": {}},
            )

        controls = request.control_inputs or {}
        vapor_pressures = dict(controls.get("vapor_pressures_Pa") or {})
        overhead_partials = dict(controls.get("overhead_partials_Pa") or {})
        molar_masses_kg_mol = dict(controls.get("molar_mass_kg_mol") or {})
        stoich_by_species = dict(controls.get("stoich_by_species") or {})
        available_oxide_kg = dict(controls.get("available_oxide_kg") or {})

        melt_surface_area_m2 = float(controls.get("melt_surface_area_m2", 0.0))
        stir_factor = float(controls.get("stir_factor", 0.0))
        alpha = float(controls.get("alpha", 0.5))

        flux_kg_hr: dict[str, float] = {}

        for species, P_sat_Pa in vapor_pressures.items():
            if P_sat_Pa <= 0:
                continue

            # Molar mass: prefer the per-species value the caller looked
            # up from vapor_pressures.yaml; fall back to the global
            # MOLAR_MASS table; final fallback to 50 g/mol mirrors the
            # legacy default for unknown species.
            M_kg_mol = molar_masses_kg_mol.get(species)
            if M_kg_mol is None or M_kg_mol <= 0.0:
                M_kg_mol = MOLAR_MASS.get(species, 50.0) / 1000.0

            P_ambient_Pa = float(overhead_partials.get(species, 0.0))

            # Hertz-Knudsen mass flux (kg/s per m^2).            [HK-1]
            denominator = math.sqrt(2 * math.pi * M_kg_mol * GAS_CONSTANT * T_K)
            J_kg_s_m2 = alpha * (P_sat_Pa - P_ambient_Pa) / denominator

            if J_kg_s_m2 <= 0:
                continue

            # Rate over the melt opening + stirring uplift.       [HK-1]
            rate_kg_hr = J_kg_s_m2 * melt_surface_area_m2 * stir_factor * 3600.0

            # Cap at parent-oxide availability (same as legacy: don't
            # evaporate more parent oxide than the melt actually holds).
            stoich = stoich_by_species.get(species) or {}
            oxide_per_product_kg = float(stoich.get("oxide_per_product_kg") or 0.0)
            if oxide_per_product_kg <= 0.0:
                # Caller didn't supply stoich for this species -- skip
                # rather than emit a flux we can't cap. Matches the
                # legacy AccountingError surface (the caller raises
                # there); here we skip silently since the kernel-level
                # error surface is owned by the caller.
                continue
            parent_oxide_kg = float(available_oxide_kg.get(species, 0.0))
            max_product_kg = parent_oxide_kg / oxide_per_product_kg
            rate_kg_hr = min(rate_kg_hr, max_product_kg)

            if rate_kg_hr > 1e-12:
                flux_kg_hr[species] = rate_kg_hr

        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            status="ok",
            transition=None,
            diagnostic={
                "evaporation_flux_kg_hr": flux_kg_hr,
                "temperature_C": T_C,
            },
        )
