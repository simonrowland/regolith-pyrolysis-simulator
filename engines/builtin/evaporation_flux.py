"""Builtin EVAPORATION_FLUX provider (Hertz-Knudsen-Langmuir).

Kernel-registered provider that owns the ``EVAPORATION_FLUX`` intent.
Mirrors the kinetic flux math in
:meth:`simulator.evaporation.EvaporationMixin._calculate_evaporation`
exactly -- this is a refactor of where the per-species flux dict is
computed, not a re-derivation of how the Hertz-Knudsen-Langmuir equation
works. The provider:

- reads ``process.cleaned_melt`` from the account view (only declared
  account; satisfies binding-spec §4 even though analytic depletion and
  availability capping happen in the simulator integration layer),
- reads T from ``request.temperature_C``,
- reads per-species vapor pressures via
  ``request.control_inputs['vapor_pressures_Pa']`` -- caller passes them
  (the kernel has already produced them via the VAPOR_PRESSURE intent in
  the same tick; the provider does NOT call the kernel recursively, that
  would couple intents inside a provider),
- reads per-species overhead partials via
  ``request.control_inputs['overhead_partials_Pa']``,
- treats the supplied vapor pressures as already-equilibrated ``P_eq``;
  pO2 dependence belongs to the VAPOR_PRESSURE intent, not this flux
  intent,
- reads melt surface area, stir factor, evaporation coefficient via
  ``control_inputs['melt_surface_area_m2']``,
  ``control_inputs['stir_factor']``, ``control_inputs['alpha']``
  (a per-species mapping),
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

from engines.builtin._common import (
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider


_DEFAULT_EVAPORATION_ALPHA = 1.0
_NONTRIVIAL_FLUX_KG_HR = 1.0e-12


def _coerce_alpha_by_species(alpha_control) -> dict[str, float]:
    if isinstance(alpha_control, Mapping):
        return {
            str(species): float(value)
            for species, value in alpha_control.items()
        }
    if alpha_control is None:
        return {}
    return {"*": float(alpha_control)}


def _coerce_alpha_envelope_by_species(alpha_envelope_control) -> dict[str, tuple[float, float]]:
    if not isinstance(alpha_envelope_control, Mapping):
        return {}

    envelopes: dict[str, tuple[float, float]] = {}
    for species, envelope in alpha_envelope_control.items():
        if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
            continue
        low, high = float(envelope[0]), float(envelope[1])
        envelopes[str(species)] = (low, high)
    return envelopes


def _flux_uncertainty_pct(
    alpha: float,
    envelope: tuple[float, float] | None,
) -> float | None:
    if envelope is None or alpha <= 0.0:
        return None
    low, high = envelope
    relative_span = max(abs(alpha - low), abs(high - alpha)) / alpha
    return relative_span * 100.0


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
        # Lazy import: simulator.state pulls in simulator/__init__ which
        # re-enters this module during package init -- see
        # engines/builtin/__init__.py for the cycle description.
        from simulator.state import GAS_CONSTANT, MOLAR_MASS

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.EVAPORATION_FLUX
        )
        if wrong_intent is not None:
            return wrong_intent

        # Kinetic flux math runs against the request's T/P/fO2 directly;
        # no independent feedback. Diagnostic-only audit.
        control_audit = diagnostic_control_audit(request)

        T_C = request.temperature_C
        T_K = T_C + 273.15
        if T_K < 400:
            # Mirrors _calculate_evaporation: below 400 K, no significant
            # evaporation -- return an empty flux dict with ok status.
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="ok",
                control_audit=control_audit,
                diagnostic={"evaporation_flux_kg_hr": {}},
            )

        controls = unpack_controls(request)
        vapor_pressures = dict(controls.get("vapor_pressures_Pa") or {})
        overhead_partials = dict(controls.get("overhead_partials_Pa") or {})
        molar_masses_kg_mol = dict(controls.get("molar_mass_kg_mol") or {})
        stoich_by_species = dict(controls.get("stoich_by_species") or {})
        available_oxide_kg = dict(controls.get("available_oxide_kg") or {})

        melt_surface_area_m2 = float(controls.get("melt_surface_area_m2", 0.0))
        # 0.5.3 Phase B: stir_factor accepts either a scalar (legacy
        # axial-only signal from a pre-Phase-B caller) or a mapping
        # with an ``axial`` key (new 2-axis caller; see
        # ``simulator/state.py::StirState``). Evaporation H-K-L is
        # driven by the AXIAL axis only — vertical EM stirring renews
        # the melt-side surface, which is what the linear multiplier
        # encodes. The radial axis drives the gas-side boundary-layer
        # Sherwood in ``simulator/condensation.py``; it does NOT enter
        # here. A mapping without ``axial`` falls back to 0.0 (halt-
        # evap signal, mirrors the pre-Phase-B "no key" semantics).
        #
        # 0.5.4 W3 (0.5.3 Phase B P3 #1 + post-push P3 deferral):
        # apply ``clamp_stir_factor`` defensively in BOTH branches.
        # The main sim path through ``simulator/evaporation.py::_pack_
        # controls`` clamps before sending, but direct-provider callers
        # (tests, ACP probes, ad-hoc IntentRequest construction) bypass
        # that clamp. A bare ``{"axial": float('nan')}`` or
        # ``{"axial": 1000.0}`` would otherwise propagate to
        # ``stir_factor`` and contaminate downstream H-K-L flux. The
        # clamp is idempotent on already-sanitised input, so the
        # canonical sim path pays nothing extra. See ``simulator/
        # state.py::clamp_stir_factor`` for the full defensive contract
        # (bool/NaN/inf/negative/over-MAX → fail-closed 0.0 or
        # MAX_STIR_FACTOR).
        from simulator.state import clamp_stir_factor as _clamp_stir
        _stir_control = controls.get("stir_factor", 0.0)
        if isinstance(_stir_control, Mapping):
            stir_factor = _clamp_stir(_stir_control.get("axial", 0.0))
        else:
            stir_factor = _clamp_stir(_stir_control)
        alpha_by_species = _coerce_alpha_by_species(controls.get("alpha"))
        alpha_envelope_by_species = _coerce_alpha_envelope_by_species(
            controls.get("alpha_envelope")
        )
        allow_unmeasured_alpha_fallback = bool(
            controls.get("allow_unmeasured_alpha_fallback", False)
        )

        flux_kg_hr: dict[str, float] = {}
        alpha_used_by_species: dict[str, float] = {}
        flux_uncertainty_pct: dict[str, float] = {}
        unmeasured_alpha_fallback_species: list[str] = []
        missing_alpha: dict[str, dict[str, float | str]] = {}

        for species, P_sat_Pa in vapor_pressures.items():
            P_sat_Pa = float(P_sat_Pa)
            if P_sat_Pa <= 0:
                continue

            # Molar mass: prefer the per-species value the caller looked
            # up from vapor_pressures.yaml; fall back to the global
            # MOLAR_MASS table; final fallback to 50 g/mol mirrors the
            # legacy default for unknown species.
            M_kg_mol = molar_masses_kg_mol.get(species)
            if M_kg_mol is None or M_kg_mol <= 0.0:
                M_kg_mol = MOLAR_MASS.get(species, 50.0) / 1000.0

            stoich = stoich_by_species.get(species) or {}
            oxide_per_product_kg = float(stoich.get("oxide_per_product_kg") or 0.0)
            if oxide_per_product_kg <= 0.0:
                # Caller didn't supply stoich for this species -- skip
                # rather than emit a flux we can't deplete. Matches the
                # legacy AccountingError surface (the caller raises
                # there); here we skip silently since the kernel-level
                # error surface is owned by the caller.
                continue

            # Hertz-Knudsen mass flux (kg/s per m^2).            [HK-1]
            P_ambient_Pa = float(overhead_partials.get(species, 0.0))
            net_pressure_Pa = P_sat_Pa - P_ambient_Pa
            if net_pressure_Pa <= 0:
                continue

            denominator = math.sqrt(2 * math.pi * M_kg_mol * GAS_CONSTANT * T_K)
            alpha_is_unmeasured = (
                species not in alpha_by_species
                and "*" not in alpha_by_species
            )
            alpha = alpha_by_species.get(
                species,
                alpha_by_species.get("*", _DEFAULT_EVAPORATION_ALPHA),
            )

            baseline_rate_kg_hr = (
                net_pressure_Pa / denominator
                * melt_surface_area_m2
                * stir_factor
                * 3600.0
            )
            available_parent_kg = float(available_oxide_kg.get(species, 0.0) or 0.0)
            if (
                alpha_is_unmeasured
                and not allow_unmeasured_alpha_fallback
                and available_parent_kg > 1.0e-12
                and baseline_rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR
            ):
                missing_alpha[species] = {
                    "policy": "fail_loud_missing_alpha",
                    "fallback_control": "chemistry_kernel.allow_unmeasured_alpha_fallback",
                    "p_sat_Pa": P_sat_Pa,
                    "p_ambient_Pa": P_ambient_Pa,
                    "baseline_alpha_1_rate_kg_hr": baseline_rate_kg_hr,
                }
                continue

            if alpha_is_unmeasured:
                unmeasured_alpha_fallback_species.append(species)

            alpha_used_by_species[species] = alpha
            uncertainty_pct = _flux_uncertainty_pct(
                alpha,
                alpha_envelope_by_species.get(species),
            )
            if uncertainty_pct is not None:
                flux_uncertainty_pct[species] = uncertainty_pct

            J_kg_s_m2 = alpha * net_pressure_Pa / denominator

            if J_kg_s_m2 <= 0:
                continue

            # Rate over the melt opening + stirring uplift.       [HK-1]
            rate_kg_hr = J_kg_s_m2 * melt_surface_area_m2 * stir_factor * 3600.0

            if rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR:
                flux_kg_hr[species] = rate_kg_hr

        if missing_alpha:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="unavailable",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "alpha_used_by_species": alpha_used_by_species,
                    "flux_uncertainty_pct": flux_uncertainty_pct,
                    "missing_alpha": missing_alpha,
                    "temperature_C": T_C,
                },
                warnings=(
                    "missing evaporation_alpha for sampled species: "
                    + ", ".join(sorted(missing_alpha)),
                ),
            )

        diagnostic = {
            "evaporation_flux_kg_hr": flux_kg_hr,
            "alpha_used_by_species": alpha_used_by_species,
            "flux_uncertainty_pct": flux_uncertainty_pct,
            "temperature_C": T_C,
        }
        if unmeasured_alpha_fallback_species:
            diagnostic["unmeasured_alpha_fallback_species"] = sorted(
                unmeasured_alpha_fallback_species
            )

        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostic,
        )
