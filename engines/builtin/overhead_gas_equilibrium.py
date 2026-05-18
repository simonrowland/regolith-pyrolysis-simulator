"""Builtin OVERHEAD_GAS_EQUILIBRIUM provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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


_OXIDE_MOLAR_MASS_G_MOL = {
    "SiO2": 60.0843,
    "MgO": 40.3044,
    "Al2O3": 101.9613,
    "TiO2": 79.866,
    "Fe2O3": 159.6882,
    "FeO": 71.844,
    "CaO": 56.0774,
    "Na2O": 61.9789,
    "K2O": 94.196,
}

_DEFAULT_MELT_SPECIATION = {
    "AlO": {
        "parent_oxide": "Al2O3",
        "reference_oxide": "Na2O",
        "reference_species": "Na",
        "activity_ratio_scale": 1.0e-9,
        "fraction": 1.0,
    },
}

_DEFAULT_ELEMENT_SPECIES = {
    "Na": {"Na": 1.0},
    "K": {"K": 1.0},
    "Al": {"AlO": 1.0, "Al": 1.0},
    "Si": {"SiO": 1.0},
    "Ca": {"Ca": 1.0},
    "Ti": {"TiO2": 1.0},
    "Fe": {"Fe": 1.0},
}


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

        ideal_partials = self.compute_partial_pressures_bar(
            holdup_mol, volume_m3, temperature_K
        )
        oxide_activities = self.oxide_activities_from_controls(controls)
        melt_partials = self.compute_melt_speciation_partials_bar(
            ideal_partials,
            controls=controls,
            oxide_activities=oxide_activities,
            temperature_K=temperature_K,
        )
        partials = dict(ideal_partials)
        for species, partial_bar in melt_partials.items():
            partials.setdefault(species, partial_bar)

        return IntentResult(
            intent=ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "partial_pressures_bar": partials,
                "ideal_gas_partial_pressures_bar": ideal_partials,
                "melt_speciation_partial_pressures_bar": melt_partials,
                "element_partial_pressures_bar": (
                    self.compute_element_partial_pressures_bar(
                        partials,
                        element_species=controls.get("element_species"),
                    )
                ),
                "p_total_bar": sum(partials.values()),
                "p_O2_bar": partials.get("O2", 0.0),
                "n_total_mol": sum(
                    max(0.0, float(v)) for v in holdup_mol.values()
                ),
                "headspace_volume_m3": volume_m3,
                "headspace_temperature_K": temperature_K,
                "oxide_activities": oxide_activities,
                "melt_speciation_model": (
                    "activity_ratio_fill_missing_species"
                    if melt_partials
                    else "ideal_gas_holdup_only"
                ),
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

    @classmethod
    def oxide_activities_from_controls(
        cls, controls: Mapping[str, Any]
    ) -> dict[str, float]:
        direct = (
            controls.get("oxide_activities")
            or controls.get("oxide_activities_gamma_1")
            or {}
        )
        activities = cls._positive_float_mapping(direct)
        if activities:
            return activities
        composition = (
            controls.get("melt_composition_wt_pct")
            or controls.get("melt_composition")
            or {}
        )
        return cls._oxide_activity_proxy_gamma_1(composition)

    @classmethod
    def compute_melt_speciation_partials_bar(
        cls,
        ideal_partials_bar: Mapping[str, float],
        *,
        controls: Mapping[str, Any],
        oxide_activities: Mapping[str, float],
        temperature_K: float,
    ) -> dict[str, float]:
        if temperature_K <= 0.0 or not oxide_activities:
            return {}
        species_specs = cls._melt_speciation_specs(controls, oxide_activities)
        if not species_specs:
            return {}

        reference_partials = dict(ideal_partials_bar or {})
        reference_partials.update(
            cls._positive_float_mapping(
                controls.get("reference_partial_pressures_bar") or {}
            )
        )

        generated: dict[str, float] = {}
        for species, spec in species_specs.items():
            species_key = str(species)
            if species_key in ideal_partials_bar:
                continue
            partial_bar = cls._speciated_partial_pressure_bar(
                spec,
                reference_partials=reference_partials,
                oxide_activities=oxide_activities,
                temperature_K=temperature_K,
            )
            if partial_bar > 0.0:
                generated[species_key] = partial_bar
        return generated

    @classmethod
    def compute_element_partial_pressures_bar(
        cls,
        partials_bar: Mapping[str, float],
        *,
        element_species: Any = None,
    ) -> dict[str, float]:
        mapping = cls._element_species_mapping(element_species)
        out: dict[str, float] = {}
        for element, species_map in mapping.items():
            total = 0.0
            for species, atom_count in species_map.items():
                partial = max(0.0, float(partials_bar.get(species, 0.0) or 0.0))
                total += partial * max(0.0, float(atom_count))
            if total > 0.0:
                out[str(element)] = total
        return out

    @classmethod
    def _melt_speciation_specs(
        cls,
        controls: Mapping[str, Any],
        oxide_activities: Mapping[str, float],
    ) -> dict[str, dict[str, float | str]]:
        raw = (
            controls.get("melt_speciation")
            or controls.get("vapor_species_speciation")
            or controls.get("gas_species_speciation")
            or {}
        )
        specs = cls._coerce_melt_speciation_specs(raw)
        if specs:
            return specs
        if (
            max(0.0, float(oxide_activities.get("Al2O3", 0.0) or 0.0)) > 0.0
            and max(0.0, float(oxide_activities.get("Na2O", 0.0) or 0.0)) > 0.0
        ):
            return {
                species: dict(spec)
                for species, spec in _DEFAULT_MELT_SPECIATION.items()
            }
        return {}

    @classmethod
    def _coerce_melt_speciation_specs(
        cls,
        raw: Any,
    ) -> dict[str, dict[str, float | str]]:
        if not isinstance(raw, Mapping):
            return {}
        specs: dict[str, dict[str, float | str]] = {}
        for species, payload in dict(raw).items():
            if not isinstance(payload, Mapping):
                continue
            species_key = str(species)
            parent_oxide = str(payload.get("parent_oxide") or "")
            reference_oxide = str(payload.get("reference_oxide") or "")
            reference_species = str(payload.get("reference_species") or "")
            if not parent_oxide or not reference_oxide or not reference_species:
                continue
            fraction_raw = payload.get("fraction", 1.0)
            spec = {
                "parent_oxide": parent_oxide,
                "reference_oxide": reference_oxide,
                "reference_species": reference_species,
                "activity_ratio_scale": max(
                    0.0, float(payload.get("activity_ratio_scale") or 0.0)
                ),
                "fraction": max(0.0, float(fraction_raw)),
            }
            specs[species_key] = spec
        return specs

    @staticmethod
    def _speciated_partial_pressure_bar(
        spec: Mapping[str, float | str],
        *,
        reference_partials: Mapping[str, float],
        oxide_activities: Mapping[str, float],
        temperature_K: float,
    ) -> float:
        reference_species = str(spec.get("reference_species") or "")
        parent_oxide = str(spec.get("parent_oxide") or "")
        reference_oxide = str(spec.get("reference_oxide") or "")
        reference_partial = max(
            0.0, float(reference_partials.get(reference_species, 0.0) or 0.0)
        )
        parent_activity = max(
            0.0, float(oxide_activities.get(parent_oxide, 0.0) or 0.0)
        )
        reference_activity = max(
            0.0, float(oxide_activities.get(reference_oxide, 0.0) or 0.0)
        )
        if (
            reference_partial <= 0.0
            or parent_activity <= 0.0
            or reference_activity <= 0.0
            or temperature_K <= 0.0
        ):
            return 0.0
        scale = max(0.0, float(spec.get("activity_ratio_scale") or 0.0))
        fraction = max(
            0.0,
            float(spec["fraction"] if "fraction" in spec else 1.0),
        )
        return (
            reference_partial
            * (parent_activity / reference_activity)
            * scale
            * fraction
        )

    @staticmethod
    def _oxide_activity_proxy_gamma_1(composition_wt_pct: Any) -> dict[str, float]:
        if not isinstance(composition_wt_pct, Mapping):
            return {}
        oxide_moles: dict[str, float] = {}
        for oxide, wt_pct in dict(composition_wt_pct).items():
            molar_mass = _OXIDE_MOLAR_MASS_G_MOL.get(str(oxide))
            if molar_mass is None:
                continue
            amount = max(0.0, float(wt_pct or 0.0))
            if amount > 0.0:
                oxide_moles[str(oxide)] = amount / molar_mass
        total = sum(oxide_moles.values())
        if total <= 0.0:
            return {}
        return {
            oxide: amount / total
            for oxide, amount in sorted(oxide_moles.items())
        }

    @staticmethod
    def _positive_float_mapping(raw: Any) -> dict[str, float]:
        if not isinstance(raw, Mapping):
            return {}
        return {
            str(key): max(0.0, float(value))
            for key, value in dict(raw).items()
            if max(0.0, float(value or 0.0)) > 0.0
        }

    @classmethod
    def _element_species_mapping(cls, raw: Any) -> dict[str, dict[str, float]]:
        if not isinstance(raw, Mapping):
            return {
                element: dict(species_map)
                for element, species_map in _DEFAULT_ELEMENT_SPECIES.items()
            }
        mapping: dict[str, dict[str, float]] = {}
        for element, species_payload in dict(raw).items():
            if isinstance(species_payload, Mapping):
                species_map = cls._positive_float_mapping(species_payload)
            elif isinstance(species_payload, (list, tuple, set)):
                species_map = {str(species): 1.0 for species in species_payload}
            else:
                species_map = {}
            if species_map:
                mapping[str(element)] = species_map
        return mapping
