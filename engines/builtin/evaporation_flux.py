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
from typing import Any

from engines.builtin._common import (
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.alpha_kinetics import (
    ALPHA_AUTHORITY_STATUS_FIELD,
    ANALYTICAL_UPPER_BOUND_ALPHA_STATUS,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.config_flags import bool_feature_flag
from simulator.transport_regime import (
    DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA,
    DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    _EVAPORATION_LJ_PROXY_PARAMS,
    EvaporationFluxConfigurationError,
    SeriesEvaporationFlux,
    SurfaceFlow,
    _finite_float,
    hertz_knudsen_mass_conductance,
    series_evaporation_flux,
)
from simulator.scalar_boundary import is_declared_real_scalar


_DEFAULT_EVAPORATION_ALPHA = 1.0
_NONTRIVIAL_FLUX_KG_HR = 1.0e-12


def _coerce_alpha_by_species(alpha_control) -> dict[str, Any]:
    if isinstance(alpha_control, Mapping):
        coerced: dict[str, Any] = {}
        for species, value in alpha_control.items():
            if isinstance(value, Mapping):
                coerced[str(species)] = dict(value)
                continue
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError(f"alpha for {species} must be numeric")
            coerced[str(species)] = float(value)
        return coerced
    if alpha_control is None:
        return {}
    if not is_declared_real_scalar(alpha_control, allow_numeric_str=True):
        raise TypeError("alpha_control must be numeric or a mapping")
    return {"*": float(alpha_control)}


def _evaluate_alpha_control(
    species: str,
    T_K: float,
    alpha_spec: Any,
) -> tuple[float, dict[str, Any]]:
    from simulator.condensation import alpha_s

    context: dict[str, Any] = {'coefficient_spec': alpha_spec}
    value = alpha_s(species, T_K, context)
    evaluation = dict(context.get('alpha_s_evaluation', {}))
    if (
        isinstance(alpha_spec, Mapping)
        and alpha_spec.get(ALPHA_AUTHORITY_STATUS_FIELD)
        == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    ):
        evaluation[ALPHA_AUTHORITY_STATUS_FIELD] = (
            ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
        )
    return value, evaluation


def _alpha_is_unmeasured(
    species: str, alpha_by_species: Mapping[str, Any]
) -> bool:
    return species not in alpha_by_species and "*" not in alpha_by_species


def _unmeasured_alpha_fallback_permitted(
    species: str,
    *,
    allow_unmeasured_alpha_fallback: bool,
    unmeasured_alpha_fallback_species_allowlist: set[str] | None,
) -> bool:
    return bool(allow_unmeasured_alpha_fallback) and (
        unmeasured_alpha_fallback_species_allowlist is None
        or species in unmeasured_alpha_fallback_species_allowlist
    )


def _missing_alpha_record(
    *,
    P_eq_Pa: float,
    P_bulk_Pa: float,
    baseline_alpha_1_rate_kg_hr: float,
) -> dict[str, float | str]:
    return {
        "policy": "fail_loud_missing_alpha",
        "fallback_control": "chemistry_kernel.allow_unmeasured_alpha_fallback",
        "P_eq_Pa": P_eq_Pa,
        "P_bulk_Pa": P_bulk_Pa,
        "baseline_alpha_1_rate_kg_hr": baseline_alpha_1_rate_kg_hr,
    }


def _attach_missing_alpha_records(
    diagnostic: dict[str, Any],
    missing_alpha: Mapping[str, Mapping[str, float | str]],
    stoich_by_species: Mapping[str, Any],
) -> None:
    if not missing_alpha:
        return
    species_refusals: dict[str, dict[str, float | str]] = {
        str(species): dict(refusal)
        for species, refusal in (
            diagnostic.get("species_refusals") or {}
        ).items()
        if isinstance(refusal, Mapping)
    }
    for species, refusal in missing_alpha.items():
        species_refusals[str(species)] = {
            **dict(refusal),
            "status": "refused",
            "reason": "missing_evaporation_alpha",
            "disposition": "retained_in_condensed_parent_oxide",
            "parent_oxide": str(
                (stoich_by_species.get(species) or {}).get(
                    "parent_oxide", "unknown"
                )
            ),
        }
    diagnostic["missing_alpha"] = dict(missing_alpha)
    diagnostic["species_refusals"] = species_refusals


def _coerce_alpha_envelope_by_species(alpha_envelope_control) -> dict[str, tuple[float, float]]:
    if not isinstance(alpha_envelope_control, Mapping):
        return {}

    envelopes: dict[str, tuple[float, float]] = {}
    for species, envelope in alpha_envelope_control.items():
        if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
            continue
        low_raw, high_raw = envelope
        if not is_declared_real_scalar(
            low_raw,
            allow_numeric_str=True,
        ) or not is_declared_real_scalar(high_raw, allow_numeric_str=True):
            raise TypeError(f"alpha envelope for {species} must be numeric")
        low, high = float(low_raw), float(high_raw)
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


def _stir_was_clamped(value, applied: float) -> bool:
    if isinstance(value, bool):
        return True
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(raw):
        return True
    return raw != applied


def _validated_stir_factor(value: Any, *, axis: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        raw = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaporationFluxConfigurationError(
            f"{axis}_stir_factor must be finite and non-negative"
        ) from exc
    if not math.isfinite(raw) or raw < 0.0:
        raise EvaporationFluxConfigurationError(
            f"{axis}_stir_factor must be finite and non-negative"
        )
    return raw


def _series_pressure_provenance_diagnostic(
    *,
    species: str,
    P_eq_Pa: float,
    P_bulk_Pa: float,
    pressure_provenance_by_species: Mapping[str, object],
    vapor_pressure_sources: Mapping[str, object],
    vapor_pressure_activities: Mapping[str, object],
    pO2_bar: object,
) -> dict[str, float | str | None]:
    provenance = pressure_provenance_by_species.get(species)
    provenance_map = provenance if isinstance(provenance, Mapping) else {}
    source_label = str(
        provenance_map.get("source_label")
        or vapor_pressure_sources.get(species)
        or "control_inputs:vapor_pressures_Pa"
    )
    activity_factor = provenance_map.get(
        "activity_factor", vapor_pressure_activities.get(species, 1.0)
    )
    try:
        activity_factor_value = float(activity_factor)
    except (TypeError, ValueError):
        activity_factor_value = 1.0
    provenance_pO2 = provenance_map.get("pO2_bar", pO2_bar)
    try:
        pO2_bar_value = float(provenance_pO2)
    except (TypeError, ValueError):
        pO2_bar_value = None
    diagnostic: dict[str, float | str | None] = {
        "pressure_kind": str(
            provenance_map.get("pressure_kind") or "effective_equilibrium"
        ),
        "P_eq_Pa": float(provenance_map.get("P_eq_Pa", P_eq_Pa)),
        "P_bulk_Pa": float(P_bulk_Pa),
        "pO2_bar": pO2_bar_value,
        "activity_factor": activity_factor_value,
        "source_label": source_label,
    }
    P_reference_Antoine_Pa = provenance_map.get("P_reference_Antoine_Pa")
    if P_reference_Antoine_Pa is not None:
        diagnostic["P_reference_Antoine_Pa"] = float(P_reference_Antoine_Pa)
    return diagnostic


def _coerce_frozen_skull_stir_ceiling(
    cold_skull_envelope: Mapping[str, float] | None,
) -> tuple[float, float | str]:
    from simulator.state import MAX_STIR_FACTOR, clamp_stir_factor

    if not isinstance(cold_skull_envelope, Mapping):
        return float(MAX_STIR_FACTOR), "not_certified"
    for key in (
        "frozen_skull_stir_ceiling",
        "max_axial_stir_factor",
        "axial_stir_ceiling",
    ):
        if key in cold_skull_envelope:
            ceiling = clamp_stir_factor(cold_skull_envelope.get(key))
            return ceiling, ceiling
    return float(MAX_STIR_FACTOR), "not_certified"


def _series_resistance_evaporation_flux_kg_m2_s(
    species: str,
    P_eq_pa: float,
    P_bulk_pa: float,
    T_surface_K: float,
    molar_mass_kg_mol: float,
    alpha_i: float,
    knudsen_number: float | None = None,
    pipe_diameter_m: float = 0.12,
    overhead_pressure_pa: float | None = None,
    axial_stir_factor: float | None = 0.0,
    radial_stir_factor: float | None = 1.0,
    cold_skull_envelope: Mapping[str, float] | None = None,
    carrier_gas: str = "N2",
    *,
    T_gas_K: float | None = None,
    melt_resistance_enabled: bool = False,
    melt_surface_renewal_base_kg_s_m2_pa: float = (
        DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
    ),
    melt_surface_renewal_source: str = DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    gas_resistance_enabled: bool = True,
    extrapolate_continuum: bool = False,
    melt_surface_area_m2: float | None = None,
    surface_flow: SurfaceFlow | None = None,
) -> SeriesEvaporationFlux:
    """Translate operator controls to the shared L1 series owner."""
    from simulator.state import clamp_stir_factor

    axial = _validated_stir_factor(axial_stir_factor, axis="axial")
    radial = _validated_stir_factor(radial_stir_factor, axis="radial")
    axial_clamped = clamp_stir_factor(axial)
    radial_clamped = clamp_stir_factor(radial)
    ceiling, ceiling_diag = _coerce_frozen_skull_stir_ceiling(cold_skull_envelope)
    applied = min(axial_clamped, ceiling)
    return series_evaporation_flux(
        species, P_eq_pa, P_bulk_pa, T_surface_K, molar_mass_kg_mol, alpha_i,
        knudsen_number, pipe_diameter_m, overhead_pressure_pa,
        axial_stir_factor, radial_stir_factor, cold_skull_envelope, carrier_gas,
        T_gas_K=T_gas_K, melt_resistance_enabled=melt_resistance_enabled,
        melt_surface_renewal_base_kg_s_m2_pa=melt_surface_renewal_base_kg_s_m2_pa,
        melt_surface_renewal_source=melt_surface_renewal_source,
        gas_resistance_enabled=gas_resistance_enabled,
        extrapolate_continuum=extrapolate_continuum,
        melt_surface_area_m2=melt_surface_area_m2,
        surface_flow=surface_flow,
        stir_diagnostic={
            "axial_stir_applied": applied, "radial_stir_applied": radial_clamped,
            "axial_stir_clamped": _stir_was_clamped(axial, axial_clamped),
            "radial_stir_clamped": _stir_was_clamped(radial, radial_clamped),
            "frozen_skull_stir_clamped": applied < axial_clamped,
            "frozen_skull_stir_ceiling": ceiling_diag,
        },
    )


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
        from simulator.state import MOLAR_MASS

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
        controls = unpack_controls(request)
        # Uncertified melt-resistance model is refused at any T so the
        # hostile config cannot ride a cold early-return (T<400) as silent ok.
        series_config_early = controls.get("evaporation_series_resistance") or {}
        if not isinstance(series_config_early, Mapping):
            series_config_early = {}
        melt_resistance_enabled_early = bool(
            series_config_early.get(
                "melt_resistance_enabled",
                controls.get("melt_resistance_enabled", False),
            )
        )
        if melt_resistance_enabled_early:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "uncertified_melt_resistance_model",
                    "detail": (
                        "requires species- and state-specific D_i, k_L,i, "
                        "and dp_eq/dC_i"
                    ),
                },
            )
        if T_K < 400:
            # Mirrors _calculate_evaporation: below 400 K, no significant
            # evaporation -- return an empty flux dict with ok status.
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="ok",
                control_audit=control_audit,
                diagnostic={"evaporation_flux_kg_hr": {}},
            )

        # VR-11: sole flux pressure input is the batch-consumer map.
        # Compatibility vapor_pressures_Pa is reporting-only and must never
        # drive HKL (DESIGN-REV5 §1.2 / §7.4). Key presence, not truthiness:
        # an explicit empty batch map is empty flux, not a legacy fallthrough.
        if "vapour_batch_flux_pressures_Pa" not in controls:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "missing_vapour_batch_flux_pressures_Pa",
                    "detail": (
                        "EVAPORATION_FLUX requires vapour_batch_flux_pressures_Pa; "
                        "legacy vapor_pressures_Pa is not a flux input"
                    ),
                },
            )
        _batch_flux_raw = controls.get("vapour_batch_flux_pressures_Pa")
        vapor_pressures = dict(_batch_flux_raw or {})
        overhead_partials = dict(controls.get("overhead_partials_Pa") or {})
        vapor_pressure_sources = dict(controls.get("vapor_pressures_source") or {})
        pressure_provenance_by_species = dict(
            controls.get("vapor_pressure_numerator_provenance") or {}
        )
        vapor_pressure_activities = dict(
            controls.get("vapor_pressure_activities")
            or controls.get("activities")
            or {}
        )
        pO2_bar = controls.get("pO2_bar")
        molar_masses_kg_mol = dict(controls.get("molar_mass_kg_mol") or {})
        stoich_by_species = dict(controls.get("stoich_by_species") or {})
        available_oxide_kg = dict(controls.get("available_oxide_kg") or {})

        for mapping_name, values in (
            ("vapour_batch_flux_pressures_Pa", vapor_pressures),
            ("overhead_partials_Pa", overhead_partials),
            ("molar_mass_kg_mol", molar_masses_kg_mol),
            ("available_oxide_kg", available_oxide_kg),
        ):
            if any(
                not is_declared_real_scalar(value, allow_numeric_str=True)
                for value in values.values()
            ):
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={"reason": f"invalid_{mapping_name}"},
                )
        for species, stoich in stoich_by_species.items():
            oxide_per_product_kg = (
                stoich.get("oxide_per_product_kg")
                if isinstance(stoich, Mapping)
                else None
            )
            if oxide_per_product_kg is not None and not is_declared_real_scalar(
                oxide_per_product_kg,
                allow_numeric_str=True,
            ):
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={
                        "reason": "invalid_stoich_by_species",
                        "species": str(species),
                    },
                )

        try:
            raw_melt_surface_area_m2 = controls.get("melt_surface_area_m2")
            if not is_declared_real_scalar(
                raw_melt_surface_area_m2,
                allow_numeric_str=True,
            ):
                raise TypeError
            melt_surface_area_m2 = float(raw_melt_surface_area_m2)
        except (TypeError, ValueError):
            melt_surface_area_m2 = math.nan
        if not math.isfinite(melt_surface_area_m2) or melt_surface_area_m2 < 0.0:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": ("missing_melt_surface_area_m2" if controls.get("melt_surface_area_m2") is None else "invalid_melt_surface_area_m2"),
                    "value": repr(controls.get("melt_surface_area_m2")),
                },
            )
        _stir_control = controls.get("stir_factor", 0.0)
        if isinstance(_stir_control, Mapping):
            axial_stir_factor = _stir_control.get("axial", 0.0)
            radial_stir_factor = _stir_control.get("radial", 1.0)
        else:
            axial_stir_factor = _stir_control
            radial_stir_factor = 1.0
        try:
            axial_stir_factor = _validated_stir_factor(
                axial_stir_factor,
                axis="axial",
            )
            radial_stir_factor = _validated_stir_factor(
                radial_stir_factor,
                axis="radial",
            )
        except EvaporationFluxConfigurationError as exc:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "invalid_stir_factor",
                    "detail": str(exc),
                },
            )
        series_config = controls.get("evaporation_series_resistance") or {}
        if not isinstance(series_config, Mapping):
            series_config = {}
        pipe_diameter_m = _finite_float(controls.get("pipe_diameter_m"), 0.12)
        overhead_pressure_pa = _finite_float(
            controls.get("overhead_pressure_pa"),
            max(0.0, float(request.pressure_bar) * 100000.0),
        )
        gas_temperature_K = _finite_float(controls.get("gas_temperature_K"), T_K)
        carrier_gas = str(controls.get("carrier_gas") or "N2")
        cold_skull_envelope = controls.get("cold_skull_envelope")
        melt_resistance_enabled = bool(
            series_config.get(
                "melt_resistance_enabled",
                controls.get("melt_resistance_enabled", False),
            )
        )
        gas_resistance_enabled = bool_feature_flag(
            series_config,
            "gas_resistance_enabled",
            bool_feature_flag(controls, "gas_resistance_enabled", True),
        )
        hkl_upper_bound_transport_species = frozenset(
            str(species)
            for species in (
                controls.get("hkl_upper_bound_transport_species", ()) or ()
            )
        )
        from simulator.condensation import (
            _knudsen_number as _kn_eval,
        )

        alpha_by_species = _coerce_alpha_by_species(controls.get("alpha"))
        alpha_envelope_by_species = _coerce_alpha_envelope_by_species(
            controls.get("alpha_envelope")
        )
        allow_unmeasured_alpha_fallback = bool(
            controls.get("allow_unmeasured_alpha_fallback", False)
        )
        fallback_species_raw = controls.get(
            "unmeasured_alpha_fallback_species"
        )
        if fallback_species_raw is None:
            unmeasured_alpha_fallback_species_allowlist = None
        elif isinstance(fallback_species_raw, (str, bytes)):
            unmeasured_alpha_fallback_species_allowlist = {
                str(fallback_species_raw)
            }
        else:
            unmeasured_alpha_fallback_species_allowlist = {
                str(species) for species in fallback_species_raw
            }
        missing_alpha: dict[str, dict[str, float | str]] = {}
        active_gas_transport_species: list[str] = []
        if (
            gas_resistance_enabled
            and melt_surface_area_m2 > 0.0
            and pipe_diameter_m > 0.0
        ):
            for species, pressure in vapor_pressures.items():
                species = str(species)
                P_eq_Pa = float(pressure)
                if P_eq_Pa <= 0.0:
                    continue
                if species in hkl_upper_bound_transport_species:
                    continue
                # α=1 is the Hertz-Knudsen ceiling used only as a marked
                # screening bound; record the same missing_alpha payload.
                alpha_is_unmeasured = _alpha_is_unmeasured(
                    species, alpha_by_species
                )
                alpha_spec = alpha_by_species.get(
                    species,
                    alpha_by_species.get("*", _DEFAULT_EVAPORATION_ALPHA),
                )
                alpha, _alpha_evaluation = _evaluate_alpha_control(
                    species,
                    T_K,
                    alpha_spec,
                )
                if alpha <= 0.0:
                    continue
                P_bulk_Pa = float(overhead_partials.get(species, 0.0))
                if P_eq_Pa <= P_bulk_Pa:
                    continue
                delta_p_Pa = P_eq_Pa - P_bulk_Pa
                stoich = stoich_by_species.get(species) or {}
                if float(stoich.get("oxide_per_product_kg") or 0.0) <= 0.0:
                    continue
                if species in available_oxide_kg:
                    available_parent_kg = float(
                        available_oxide_kg.get(species, 0.0) or 0.0
                    )
                    if available_parent_kg <= 1.0e-12:
                        continue
                else:
                    available_parent_kg = 0.0
                M_kg_mol = molar_masses_kg_mol.get(species)
                if M_kg_mol is None or M_kg_mol <= 0.0:
                    molar_mass_g_mol = MOLAR_MASS.get(species)
                    if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
                        continue
                    M_kg_mol = molar_mass_g_mol / 1000.0
                hkl_upper_bound_rate_kg_hr = (
                    alpha
                    * hertz_knudsen_mass_conductance(M_kg_mol, T_K)
                    * delta_p_Pa
                    * melt_surface_area_m2
                    * 3600.0
                )
                if hkl_upper_bound_rate_kg_hr <= _NONTRIVIAL_FLUX_KG_HR:
                    continue
                if (
                    alpha_is_unmeasured
                    and not _unmeasured_alpha_fallback_permitted(
                        species,
                        allow_unmeasured_alpha_fallback=(
                            allow_unmeasured_alpha_fallback
                        ),
                        unmeasured_alpha_fallback_species_allowlist=(
                            unmeasured_alpha_fallback_species_allowlist
                        ),
                    )
                    and available_parent_kg > 1.0e-12
                ):
                    missing_alpha[species] = _missing_alpha_record(
                        P_eq_Pa=P_eq_Pa,
                        P_bulk_Pa=P_bulk_Pa,
                        baseline_alpha_1_rate_kg_hr=(
                            hkl_upper_bound_rate_kg_hr
                        ),
                    )
                active_gas_transport_species.append(species)
        melt_surface_renewal_raw = series_config.get(
            "melt_surface_renewal_base_kg_s_m2_pa",
            controls.get("melt_surface_renewal_base_kg_s_m2_pa"),
        )
        if melt_surface_renewal_raw is None:
            melt_surface_renewal_base = (
                DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
            )
        else:
            melt_surface_renewal_base = _finite_float(
                melt_surface_renewal_raw,
                math.nan,
            )
        melt_surface_renewal_source = str(
            series_config.get(
                "melt_surface_renewal_source",
                controls.get(
                    "melt_surface_renewal_source",
                    DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
                ),
            )
        )
        from simulator.evaporation import (
            viscous_p_bulk_out_of_domain_diagnostic,
        )
        from simulator.transport_regime import continuum_validity_refuses

        # When gas resistance is disabled, the executed model is the HKL-only
        # path and does not use viscous Poiseuille P_bulk. Batch carriers can
        # raise the diagnostic overhead pressure into transitional Kn without
        # making that deliberately disabled transport model authoritative.
        commanded_pressure_pa = _finite_float(
            controls.get("commanded_pressure_pa"),
            overhead_pressure_pa,
        )
        campaign_name_raw = controls.get("campaign_name")
        campaign_name = (
            None
            if campaign_name_raw is None
            else (str(campaign_name_raw).strip() or None)
        )
        domain_refusal = None
        continuum_extrapolation = None
        if gas_resistance_enabled and overhead_pressure_pa > 0.0:
            kn_domain = _kn_eval(
                overhead_pressure_pa,
                max(gas_temperature_K, 1.0),
                max(pipe_diameter_m, 0.0) or 0.12,
                carrier_gas=carrier_gas,
            )
            domain_refusal = viscous_p_bulk_out_of_domain_diagnostic(
                knudsen_number=kn_domain,
                overhead_pressure_pa=overhead_pressure_pa,
                commanded_pressure_pa=commanded_pressure_pa,
                pipe_diameter_m=pipe_diameter_m,
                gas_temperature_K=gas_temperature_K,
                carrier_gas=carrier_gas,
                affected_species=tuple(active_gas_transport_species),
                campaign_name=campaign_name,
                asking_site="engines.builtin.evaporation_flux",
            )
            if (
                active_gas_transport_species
                and domain_refusal is not None
            ):
                if domain_refusal.get("reason") == "viscous_p_bulk_transport_out_of_domain":
                    continuum_extrapolation = {
                        **{key: value for key, value in domain_refusal.items()
                           if key not in ("silent_zero_notes", "evaporation_flux_kg_hr")},
                        "status": "extrapolated",
                        "authority_level": "extrapolated",
                        "authority_class": "extrapolated",
                        "doctrine_category": 2,
                        "ledger_yields_authorized": True,
                        "evaporation_flux_status": "extrapolated",
                        "detail": domain_refusal["detail"] + "; finite continuation of the same continuum fit; not certified transitional transport",
                    }
            if (
                active_gas_transport_species
                and continuum_validity_refuses(domain_refusal)
                and continuum_extrapolation is None
            ):
                domain_diagnostic = dict(domain_refusal)
                _attach_missing_alpha_records(
                    domain_diagnostic,
                    missing_alpha,
                    stoich_by_species,
                )
                domain_warnings = [
                    "viscous_p_bulk_transport_out_of_domain: "
                    "evaporation flux not evaluated in transitional Kn",
                ]
                if missing_alpha:
                    domain_warnings.append(
                        "per-species evaporation refusal; retained condensed "
                        "because evaporation_alpha is missing: "
                        + ", ".join(sorted(missing_alpha))
                    )
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic=domain_diagnostic,
                    warnings=tuple(domain_warnings),
                )

        flux_kg_hr: dict[str, float] = {}
        below_threshold_flux_kg_hr: dict[str, float] = {}
        alpha_used_by_species: dict[str, float] = {}
        alpha_evaluations_by_species: dict[str, dict[str, Any]] = {}
        alpha_authority_status_by_species: dict[str, str] = {}
        flux_uncertainty_pct: dict[str, float] = {}
        series_flux_diagnostics: dict[
            str, dict[str, float | str | bool | None]
        ] = {}
        unmeasured_alpha_fallback_species: list[str] = []
        missing_molar_mass: dict[str, dict[str, float | str]] = {}
        missing_transport_parameters: dict[str, dict[str, float | str]] = {}
        computable_transport_species: set[str] = set()

        for species, P_eq_Pa in vapor_pressures.items():
            P_eq_Pa = float(P_eq_Pa)
            if P_eq_Pa <= 0:
                continue

            # Molar mass: prefer the per-species value the caller looked
            # up from vapor_pressures.yaml; fall back to the grounded global
            # MOLAR_MASS table. Never guess a molar mass for HK flux.
            M_kg_mol = molar_masses_kg_mol.get(species)
            if M_kg_mol is None or M_kg_mol <= 0.0:
                molar_mass_g_mol = MOLAR_MASS.get(species)
                if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
                    missing_molar_mass[species] = {
                        "policy": "fail_loud_missing_molar_mass",
                        "data_file": "data/vapor_pressures.yaml",
                        "control": "molar_mass_kg_mol",
                        "P_eq_Pa": P_eq_Pa,
                    }
                    continue
                M_kg_mol = molar_mass_g_mol / 1000.0

            stoich = stoich_by_species.get(species) or {}
            oxide_per_product_kg = float(stoich.get("oxide_per_product_kg") or 0.0)
            if oxide_per_product_kg <= 0.0:
                # Caller didn't supply stoich for this species -- skip
                # rather than emit a flux we can't deplete. Matches the
                # legacy AccountingError surface (the caller raises
                # there); here we skip silently since the kernel-level
                # error surface is owned by the caller.
                continue

            uses_gas_resistance = (
                gas_resistance_enabled
                and species not in hkl_upper_bound_transport_species
                and melt_surface_area_m2 > 0.0
            )
            # The reconstructed HKL upper bound is a free-evaporation boundary:
            # when gas resistance is disabled, viscous Poiseuille P_bulk is not
            # part of the executed model and must not suppress its pressure drive.
            P_bulk_Pa = (
                float(overhead_partials.get(species, 0.0))
                if uses_gas_resistance
                else 0.0
            )
            delta_p_Pa = max(0.0, P_eq_Pa - P_bulk_Pa)
            if delta_p_Pa <= 0:
                continue
            alpha_is_unmeasured = _alpha_is_unmeasured(
                species, alpha_by_species
            )
            alpha_fallback_permitted = _unmeasured_alpha_fallback_permitted(
                species,
                allow_unmeasured_alpha_fallback=(
                    allow_unmeasured_alpha_fallback
                ),
                unmeasured_alpha_fallback_species_allowlist=(
                    unmeasured_alpha_fallback_species_allowlist
                ),
            )
            alpha_spec = alpha_by_species.get(
                species,
                alpha_by_species.get("*", _DEFAULT_EVAPORATION_ALPHA),
            )
            alpha, alpha_evaluation = _evaluate_alpha_control(
                species,
                T_K,
                alpha_spec,
            )
            if alpha_evaluation:
                alpha_evaluations_by_species[species] = alpha_evaluation
            alpha_authority_status = alpha_evaluation.get(
                ALPHA_AUTHORITY_STATUS_FIELD
            )

            baseline_alpha_1 = _series_resistance_evaporation_flux_kg_m2_s(
                species=species,
                P_eq_pa=P_eq_Pa,
                P_bulk_pa=P_bulk_Pa,
                T_surface_K=T_K,
                molar_mass_kg_mol=M_kg_mol,
                alpha_i=1.0,
                melt_surface_area_m2=melt_surface_area_m2,
                surface_flow=controls.get("surface_flow"),
                knudsen_number=math.inf,
                pipe_diameter_m=pipe_diameter_m,
                overhead_pressure_pa=overhead_pressure_pa,
                axial_stir_factor=axial_stir_factor,
                radial_stir_factor=radial_stir_factor,
                cold_skull_envelope=cold_skull_envelope,
                carrier_gas=carrier_gas,
                T_gas_K=gas_temperature_K,
                gas_resistance_enabled=False,
                melt_resistance_enabled=False,
            )
            baseline_rate_kg_hr = (
                baseline_alpha_1.flux_kg_s_m2 * melt_surface_area_m2 * 3600.0
            )
            available_parent_kg = float(available_oxide_kg.get(species, 0.0) or 0.0)
            if (
                domain_refusal is not None
                and species in available_oxide_kg
                and available_parent_kg <= 1.0e-12
            ):
                continue
            if (
                alpha_is_unmeasured
                and not alpha_fallback_permitted
                and available_parent_kg > 1.0e-12
                and baseline_rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR
            ):
                missing_alpha[species] = _missing_alpha_record(
                    P_eq_Pa=P_eq_Pa,
                    P_bulk_Pa=P_bulk_Pa,
                    baseline_alpha_1_rate_kg_hr=baseline_rate_kg_hr,
                )
                continue

            if alpha_is_unmeasured and alpha_fallback_permitted:
                unmeasured_alpha_fallback_species.append(species)

            alpha_used_by_species[species] = alpha
            uncertainty_pct = _flux_uncertainty_pct(
                alpha,
                alpha_envelope_by_species.get(species),
            )
            if uncertainty_pct is not None:
                flux_uncertainty_pct[species] = uncertainty_pct

            try:
                series_flux = _series_resistance_evaporation_flux_kg_m2_s(
                    species=species,
                    P_eq_pa=P_eq_Pa,
                    P_bulk_pa=P_bulk_Pa,
                    T_surface_K=T_K,
                    molar_mass_kg_mol=M_kg_mol,
                    alpha_i=alpha,
                    melt_surface_area_m2=melt_surface_area_m2,
                    surface_flow=controls.get("surface_flow"),
                    pipe_diameter_m=pipe_diameter_m,
                    overhead_pressure_pa=overhead_pressure_pa,
                    axial_stir_factor=axial_stir_factor,
                    radial_stir_factor=radial_stir_factor,
                    cold_skull_envelope=cold_skull_envelope,
                    carrier_gas=carrier_gas,
                    T_gas_K=gas_temperature_K,
                    melt_resistance_enabled=melt_resistance_enabled,
                    gas_resistance_enabled=uses_gas_resistance,
                    extrapolate_continuum=continuum_extrapolation is not None,
                    melt_surface_renewal_base_kg_s_m2_pa=melt_surface_renewal_base,
                    melt_surface_renewal_source=melt_surface_renewal_source,
                )
            except EvaporationFluxConfigurationError as exc:
                missing_transport_parameters[species] = {
                    "policy": "fail_loud_missing_transport_parameters",
                    "carrier_gas": carrier_gas,
                    "reason": str(exc),
                    "refusal_type": type(exc).__name__,
                    "status": "unavailable",
                    "authority_level": "unavailable",
                }
                continue
            computable_transport_species.add(species)
            J_kg_s_m2 = series_flux.flux_kg_s_m2

            series_diagnostic = series_flux.as_diagnostic()
            if continuum_extrapolation is not None and uses_gas_resistance:
                series_diagnostic["continuum_extrapolation_notice"] = dict(continuum_extrapolation)
            if alpha_authority_status == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS:
                series_diagnostic[ALPHA_AUTHORITY_STATUS_FIELD] = (
                    alpha_authority_status
                )
            if species in hkl_upper_bound_transport_species:
                series_diagnostic["gas_transport_model"] = (
                    "hkl_kinetic_upper_bound_no_chapman_enskog"
                )
                series_diagnostic["authority_status"] = (
                    "analytical_upper_bound_status_bearing_cannot_certify"
                )
            series_diagnostic.update(
                _series_pressure_provenance_diagnostic(
                    species=species,
                    P_eq_Pa=P_eq_Pa,
                    P_bulk_Pa=P_bulk_Pa,
                    pressure_provenance_by_species=(
                        pressure_provenance_by_species
                    ),
                    vapor_pressure_sources=vapor_pressure_sources,
                    vapor_pressure_activities=vapor_pressure_activities,
                    pO2_bar=pO2_bar,
                )
            )
            series_flux_diagnostics[species] = series_diagnostic
            if J_kg_s_m2 <= 0:
                continue

            rate_kg_hr = J_kg_s_m2 * melt_surface_area_m2 * 3600.0

            if rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR:
                if (
                    alpha_authority_status
                    == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
                ):
                    alpha_authority_status_by_species[species] = (
                        alpha_authority_status
                    )
                flux_kg_hr[species] = rate_kg_hr
            else:
                below_threshold_flux_kg_hr[species] = rate_kg_hr

        if missing_transport_parameters and not computable_transport_species:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="unavailable",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "missing_transport_parameters": missing_transport_parameters,
                    "temperature_C": T_C,
                    "evaporation_series_resistance": series_flux_diagnostics,
                },
                warnings=(
                    ("missing Chapman-Enskog transport parameters for sampled species: "
                     if all("Chapman-Enskog" in record["reason"] for record in missing_transport_parameters.values())
                     else "unavailable surface transport parameters for sampled species: ")
                    + ", ".join(sorted(missing_transport_parameters)),
                ),
            )

        diagnostic = {
            "evaporation_flux_kg_hr": flux_kg_hr,
            "alpha_used_by_species": alpha_used_by_species,
            "alpha_s_evaluation_by_species": alpha_evaluations_by_species,
            "flux_uncertainty_pct": flux_uncertainty_pct,
            "evaporation_series_resistance": series_flux_diagnostics,
            "temperature_C": T_C,
            # Matrix policy (i): source flux is HKL upper bound while R_m=0.
            "authority_class": "upper-bound",
            "authority_reason": (
                "missing-species-state-dependent-melt-transfer-inputs"
            ),
        }
        if below_threshold_flux_kg_hr:
            diagnostic["below_threshold_evaporation_flux"] = {
                "threshold_kg_hr": _NONTRIVIAL_FLUX_KG_HR,
                "fluxes_kg_hr": below_threshold_flux_kg_hr,
            }
        if alpha_authority_status_by_species:
            diagnostic["alpha_authority_status_by_species"] = (
                alpha_authority_status_by_species
            )
        _attach_missing_alpha_records(
            diagnostic, missing_alpha, stoich_by_species
        )
        if unmeasured_alpha_fallback_species:
            diagnostic["unmeasured_alpha_fallback_species"] = sorted(
                unmeasured_alpha_fallback_species
            )
        warning_messages: list[str] = []
        if missing_alpha:
            warning_messages.append(
                "per-species evaporation refusal; retained condensed because "
                "evaporation_alpha is missing: "
                + ", ".join(sorted(missing_alpha))
            )
        if unmeasured_alpha_fallback_species:
            warning_messages.append(
                "WARNING: alpha=1.0 prototype fallback used for unmeasured "
                "evaporation species: "
                + ", ".join(sorted(unmeasured_alpha_fallback_species))
            )
        if missing_transport_parameters:
            diagnostic["missing_transport_parameters"] = missing_transport_parameters
            warning_messages.append(
                "excluded evaporation species with missing Chapman-Enskog "
                "transport parameters: "
                + ", ".join(sorted(missing_transport_parameters))
            )
        if missing_molar_mass:
            diagnostic["missing_molar_mass"] = missing_molar_mass
            warning_messages.append(
                "missing molar_mass_g_mol for evaporation species in "
                "data/vapor_pressures.yaml: "
                + ", ".join(sorted(missing_molar_mass))
            )
        if continuum_extrapolation is not None:
            diagnostic["continuum_extrapolation_notice"] = continuum_extrapolation
            warning_messages.append(
                "viscous_p_bulk_transport_out_of_domain: EXTRAPOLATED finite continuum flux; "
                "source domain and evaluated Kn remain attached"
            )
        if (
            domain_refusal is not None
            and not continuum_validity_refuses(domain_refusal)
        ):
            from simulator.silent_zero import merge_notes_into_mapping

            diagnostic["p_bulk_transport_domain"] = domain_refusal.get(
                "p_bulk_transport_domain"
            )
            diagnostic["process_regime"] = domain_refusal.get("process_regime")
            diagnostic["campaign_name"] = domain_refusal.get("campaign_name")
            diagnostic["stage"] = domain_refusal.get("stage")
            diagnostic["asking_site"] = domain_refusal.get("asking_site")
            diagnostic["doctrine_category"] = domain_refusal.get(
                "doctrine_category"
            )
            diagnostic["continuum_formula_status"] = domain_refusal.get(
                "status"
            )
            diagnostic["authority_reason_continuum"] = domain_refusal.get(
                "authority_reason"
            )
            merge_notes_into_mapping(
                diagnostic, domain_refusal.get("silent_zero_notes") or ()
            )
            warning_messages.append(
                "continuum_formula_out_of_domain_marked: "
                "flagged continuum extension computed outside the certified "
                "continuum domain (kinetic t-1396/t-1397)"
            )

        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(warning_messages),
        )
