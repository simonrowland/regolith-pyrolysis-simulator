from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.capacity_coupling as capacity_coupling
import simulator.chemistry.melt_activity as melt_activity
from engines.builtin.evaporation_flux import _NONTRIVIAL_FLUX_KG_HR
from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    OXYGEN_SPECIES,
    compressible_pressure_capacity_fraction,
)
from simulator.accounting import resolve_species_formula
from simulator.alpha_kinetics import ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
from simulator.capacity_coupling import (
    CapacityCouplingRefusalError,
    CapacityShadowRefusal,
    CapacityShadowResult,
    combined_saturation,
    partition_melt_oxygen,
    solve_capacity_shadow,
)
from simulator.condensation_routing import (
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
)
from simulator.core import PyrolysisSimulator
from simulator.evaporation import (
    EvaporationFluxRefusal,
    EvaporationMixin,
    PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID,
    _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS,
    _pre_rg_effective_pressure_source,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.state import CampaignPhase, EvaporationFlux
from simulator.vapour_rail.activity import ActivityVerdictKind
from simulator.vapour_rail.batch import (
    FluxEligible,
    PressureRefusal,
    PressureValue,
    ZeroByPhysics,
)
from simulator.vapour_rail.catalog import OUT_OF_RANGE_STATUS
from simulator.vapour_rail.instrumentation import (
    EffectivePressureSource,
    TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
    VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
    compare_live_shadow_to_batch_flux,
    flux_pressures_from_batch,
    serialize_vapour_batch,
    vapour_carrier_authority_status,
)
from simulator.thermal_train import (
    FiniteCapacity,
    NoColdTrain,
    capacity_from_hardware,
    thermal_train_parameters_from_mapping,
)
from tests.chemistry.conftest import _build_sim


M_O2 = 0.032
M_N2 = 0.028
T_FOR_1000_PA_PER_MOL = 1000.0 / GAS_CONSTANT
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _real_capacity_sim():
    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


@pytest.mark.parametrize("path", ["live", "capacity"])
def test_invalid_molar_mass_is_species_local(path, monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = copy.deepcopy(sim._get_equilibrium())
    pressures = {
        str(species): 0.0
        for species in equilibrium.vapor_pressures_Pa
    }
    pressures.update({"Fe": 100.0, "Na": 100.0})
    equilibrium.vapor_pressures_Pa.clear()
    equilibrium.vapor_pressures_Pa.update(pressures)
    sim._last_vapor_pressure_diagnostic[
        "backend_vapor_pressures_Pa"
    ] = dict(pressures)
    sim.vapor_pressures["metals"]["Fe"]["molar_mass_g_mol"] = "invalid"

    if path == "capacity":
        capacity = FiniteCapacity(0.05)
        cold_train = SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        )
        monkeypatch.setattr(
            sim,
            "_cold_train_capacity_policy",
            lambda: (capacity, cold_train),
        )
        result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim, equilibrium
        )
        assert isinstance(result, CapacityShadowResult)
        rates = result.evaporation_flux_kg_hr
    else:
        result = sim._calculate_evaporation(equilibrium)
        rates = result.species_kg_hr

    assert rates.get("Na", 0.0) > 0.0
    assert "Fe" not in rates
    overlay = sim._last_vapour_batch_flux_overlay
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        and notice.get("reason") == "missing_molar_mass"
        for notice in overlay.get("notices", ())
        if isinstance(notice, dict)
    )
    assert any(
        notice.get("kind") == "transport_parameters_estimated"
        and notice.get("species") == "Fe"
        for notice in overlay.get("notices", ())
        if isinstance(notice, dict)
    )
    if path == "capacity":
        bound = next(
            notice
            for notice in overlay["notices"]
            if notice.get("kind")
            == "capacity_solve_excludes_unavailable_species"
        )
        assert bound["excluded_species"] == [{
            "species": "Fe",
            "reason": "missing_molar_mass",
        }]


@pytest.mark.xdist_group("serial")
def test_b1318_two_carrier_state_matrix_keeps_peer_outcomes_independent():
    """Drive channel states through production resolution, closure, and Picard."""
    from itertools import product

    from simulator.vapour_rail.catalog import VapourRailCatalog
    from simulator.vapour_rail.instrumentation import flux_pressures_from_batch
    from simulator.state import MOLAR_MASS

    species_ids = ("Fe", "Si")
    states = (
        "resolved",
        "catalog_proven_zero",
        "closure_proven_zero",
        "unavailable_catalog",
        "unavailable_parent",
        "unavailable_transport",
    )
    cases = tuple(
        (
            f"{left_state}+{right_state}",
            dict(zip(species_ids, (left_state, right_state))),
        )
        for left_state, right_state in product(states, repeat=2)
    ) + (
        ("resolved+not_requested", {
            "Fe": "resolved",
            "Si": "not_requested",
        }),
        ("not_requested+resolved", {
            "Fe": "not_requested",
            "Si": "resolved",
        }),
        ("rung5_unavailable_sole_carrier", {
            "Fe": "unavailable_transport",
            "Si": "not_requested",
        }),
        ("provider_refusal+resolved", {
            "Fe": "provider_refusal",
            "Si": "resolved",
        }),
        ("resolved+provider_refusal", {
            "Fe": "resolved",
            "Si": "provider_refusal",
        }),
        ("provider_refusal+provider_refusal", {
            "Fe": "provider_refusal",
            "Si": "provider_refusal",
        }),
    ) + tuple(
        (
            f"{left}+{right}",
            dict(zip(species_ids, (left, right))),
        )
        for peer_state in (
            "catalog_proven_zero",
            "closure_proven_zero",
            "unavailable_catalog",
            "unavailable_parent",
            "unavailable_transport",
        )
        for left, right in (
            ("provider_refusal", peer_state),
            (peer_state, "provider_refusal"),
        )
    )
    basis = _real_capacity_sim()
    basis.start_campaign(CampaignPhase.C0)
    basis.melt.temperature_C = 1600.0
    basis.vapor_pressures["metals"]["Fe"].update({
        "critical_temperature_K": 6000.0,
        "critical_pressure_atm": 500.0,
    })
    equilibrium = basis._get_equilibrium()
    vapor_pressure_diagnostic = copy.deepcopy(
        basis._last_vapor_pressure_diagnostic
    )
    assert vapor_pressure_diagnostic.get("source_reaction_fO2_bar")

    capacity = FiniteCapacity(0.05)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    baseline_capacity_rates = None
    baseline_capacity_partials = None
    live_rates_by_case = {}
    capacity_rates_by_case = {}
    capacity_partials_by_case = {}

    for case_name, state_by_species in cases:
        sim = _real_capacity_sim()
        sim.start_campaign(CampaignPhase.C0)
        sim.melt.temperature_C = 1600.0
        sim.vapor_pressures["metals"]["Fe"].update({
            "critical_temperature_K": 6000.0,
            "critical_pressure_atm": 500.0,
        })
        sim._last_vapor_pressure_diagnostic = copy.deepcopy(
            vapor_pressure_diagnostic
        )
        capacity_sim = _real_capacity_sim()
        capacity_sim.start_campaign(CampaignPhase.C0)
        capacity_sim.melt.temperature_C = 1600.0
        capacity_sim.vapor_pressures["metals"]["Fe"].update({
            "critical_temperature_K": 6000.0,
            "critical_pressure_atm": 500.0,
        })
        capacity_sim._last_vapor_pressure_diagnostic = copy.deepcopy(
            vapor_pressure_diagnostic
        )
        step_sim = _real_capacity_sim()
        step_sim.start_campaign(CampaignPhase.C0)
        step_sim.melt.temperature_C = 1600.0
        step_sim.vapor_pressures["metals"]["Fe"].update({
            "critical_temperature_K": 6000.0,
            "critical_pressure_atm": 500.0,
        })
        step_sim._last_vapor_pressure_diagnostic = copy.deepcopy(
            vapor_pressure_diagnostic
        )
        case_equilibrium = copy.deepcopy(equilibrium)
        sim._freeze_gate_enabled = lambda: False
        sim._cold_train_capacity_policy = lambda: (capacity, cold_train)
        sim._headspace_bleed_conductance_kg_s = lambda **_kwargs: 0.0
        capacity_sim._freeze_gate_enabled = lambda: False
        capacity_sim._cold_train_capacity_policy = lambda: (capacity, cold_train)
        capacity_sim._headspace_bleed_conductance_kg_s = lambda **_kwargs: 0.0
        step_sim._freeze_gate_enabled = lambda: False
        step_sim._cold_train_capacity_policy = lambda: (capacity, cold_train)
        step_sim._headspace_bleed_conductance_kg_s = lambda **_kwargs: 0.0
        provider_statuses = []
        provider_request_maps = []
        provider_request_maps_by_path = {
            "capacity": [],
            "live": [],
            "step": [],
        }
        active_provider_path = ["capacity"]
        provider_stoich_memberships = []
        closure_publications = []
        closure_publications_by_host = {}
        booked_by_production_step = set()
        case_should_refuse = not any(
            state in {"resolved", "catalog_proven_zero", "closure_proven_zero"}
            for state in state_by_species.values()
        )
        expected_refusal_reason = (
            "capacity_member_closure_failure"
            if case_should_refuse
            and "unavailable_parent" in state_by_species.values()
            and "provider_refusal" in state_by_species.values()
            else "vapour_batch_no_debiting_pressure_outcome"
        )

        for target in (sim, capacity_sim, step_sim):
            closure_partial_mbar = {}
            if "closure_proven_zero" in state_by_species.values():
                for zero_species, state in state_by_species.items():
                    if state == "closure_proven_zero":
                        moles = 1.0e-3
                        target.atom_ledger.load_external(
                            "process.overhead_gas",
                            {
                                zero_species: (
                                    moles
                                    * float(MOLAR_MASS[zero_species])
                                    / 1000.0
                                ),
                            },
                            source="b1318 production closure-zero input",
                            material_origin="feedstock",
                        )
                        partial_pa = capacity_coupling._headspace_partial_pressures_Pa(
                            {zero_species: moles},
                            species=(zero_species,),
                            temperature_K=target._headspace_temperature_K(),
                            volume_m3=target._headspace_volume_m3(),
                        )[zero_species]
                        closure_partial_mbar[zero_species] = partial_pa / 100.0
                provenance = dict(
                    target._last_vapor_pressure_diagnostic.get(
                        "vapor_pressure_numerator_provenance", {}
                    )
                )
                for zero_species in closure_partial_mbar:
                    provenance[zero_species] = {
                        "pressure_rail": "condensed_raoult_psat",
                        "activity_factor": 1.0,
                        "raw_metal_activity_root": 2.0,
                        "P_reference_Antoine_Pa": 1.0,
                    }
                target._last_vapor_pressure_diagnostic[
                    "vapor_pressure_numerator_provenance"
                ] = provenance
                # A high real headspace partial plus a no-buffer redox input
                # makes the surface solver refuse this carrier; its typed cap
                # then proves zero. No closure function or classifier is stubbed.
                target._melt_redox_source_capacity_mol_per_ln_fO2 = (
                    lambda **_kwargs: 0.0
                )
                target._melt_headspace_composition_mbar = closure_partial_mbar

        with pytest.MonkeyPatch.context() as local:
            for species, state in state_by_species.items():
                if state == "not_requested":
                    # Peer-not-requested removes it from both the actual
                    # request and the real equilibrium source map.
                    case_equilibrium.vapor_pressures_Pa.pop(species, None)
                elif state in {"unavailable_transport", "catalog_proven_zero"}:
                    from simulator.condensation import _LENNARD_JONES_PARAMS

                    local.delitem(
                        _LENNARD_JONES_PARAMS,
                        species,
                        raising=False,
                    )
                for target in (sim, capacity_sim, step_sim):
                    if state == "unavailable_catalog":
                        catalog = target.vapour_rail_catalog
                        compiled = dict(catalog.species)
                        compiled.pop(species)
                        legacy_projection = catalog.legacy_view()
                        for rows in legacy_projection.values():
                            if isinstance(rows, dict):
                                rows.pop(species, None)
                        target.vapour_rail_catalog = VapourRailCatalog(
                            species=compiled,
                            legacy_projection=legacy_projection,
                            request_rules=catalog.request_rules,
                            catalog_payload=target.vapor_pressure_catalog_data,
                        )
                    elif state == "not_requested":
                        for key in (
                            "vapor_pressures_Pa",
                            "backend_vapor_pressures_Pa",
                        ):
                            source = target._last_vapor_pressure_diagnostic.get(key)
                            if isinstance(source, dict):
                                source.pop(species, None)
                        catalog = target.vapour_rail_catalog
                        target.vapour_rail_catalog = VapourRailCatalog(
                            species=catalog.species,
                            legacy_projection=catalog.legacy_view(),
                            request_rules=tuple(
                                rule for rule in catalog.request_rules
                                if rule.species_id != species
                            ),
                            catalog_payload=target.vapor_pressure_catalog_data,
                        )
                    elif state == "unavailable_transport" and species == "Fe":
                        target.vapor_pressures["metals"]["Fe"].pop(
                            "critical_temperature_K", None
                        )
                        target.vapor_pressures["metals"]["Fe"].pop(
                            "critical_pressure_atm", None
                        )
                    elif state == "catalog_proven_zero":
                        # Production has no input/configuration that constructs
                        # ZeroByPhysics today. Keep the real catalog request,
                        # then use this narrow typed-answer seam; the production
                        # selector, closure, provider, and Picard loop still run.
                        real_builder = target.build_vapour_batch

                        def build_with_proven_zero(
                            *,
                            _species=species,
                            _real_builder=real_builder,
                            **kwargs,
                        ):
                            batch = _real_builder(**kwargs)
                            channels = dict(batch.channels_by_species)
                            answer = channels[_species]
                            zero = ZeroByPhysics(
                                "b1318_narrow_catalog_zero_test_seam"
                            )
                            channels[_species] = replace(
                                answer,
                                pressure=zero,
                                selected_runtime_pressure=zero,
                            )
                            return replace(
                                batch,
                                channels_by_species=channels,
                                flux_active_species_ids=(
                                    batch.flux_active_species_ids - {_species}
                                ),
                            )

                        target.build_vapour_batch = build_with_proven_zero

            if "unavailable_parent" in state_by_species.values():
                missing_parent_species = {
                    species
                    for species, state in state_by_species.items()
                    if state == "unavailable_parent"
                }
                for target in (sim, capacity_sim, step_sim):
                    real_resolve = target._resolve_evaporation_batch_flux_state

                    def resolve_then_remove_parent(
                        *args,
                        _target=target,
                        _real_resolve=real_resolve,
                        _missing=missing_parent_species,
                        _removed=[False],
                        **kwargs,
                    ):
                        resolved = _real_resolve(*args, **kwargs)
                        if not _removed[0]:
                            for missing_species in _missing:
                                for group in ("metals", "oxide_vapors"):
                                    row = _target.vapor_pressures.get(
                                        group, {}
                                    ).get(missing_species)
                                    if isinstance(row, dict):
                                        row.pop("parent_oxide", None)
                            _removed[0] = True
                        return resolved

                    target._resolve_evaporation_batch_flux_state = (
                        resolve_then_remove_parent
                    )

            if (
                case_name.startswith("provider_refusal+")
                or case_name.endswith("+provider_refusal")
                or "unavailable_parent" in state_by_species.values()
                or "closure_proven_zero" in state_by_species.values()
            ):
                from engines.builtin.evaporation_flux import (
                    BuiltinEvaporationFluxProvider,
                )
                import simulator.state as state_module

                real_dispatch = BuiltinEvaporationFluxProvider.dispatch
                refused_species_ids = {
                    species
                    for species, state in state_by_species.items()
                    if state == "provider_refusal"
                }
                for refused_species in refused_species_ids:
                    local.delitem(
                        state_module.MOLAR_MASS,
                        refused_species,
                        raising=False,
                    )

                def capture_and_refuse_provider_channel(provider, request):
                    controls = dict(request.control_inputs)
                    requested = dict(
                        controls.get("vapour_batch_flux_pressures_Pa", {}) or {}
                    )
                    provider_request_maps.append(set(requested))
                    provider_request_maps_by_path[
                        active_provider_path[0]
                    ].append(set(requested))
                    provider_stoich_memberships.append(
                        set(controls.get("stoich_by_species", {}) or {})
                    )
                    if not refused_species_ids.intersection(requested):
                        return real_dispatch(provider, request)
                    molar_masses = dict(
                        controls.get("molar_mass_kg_mol", {}) or {}
                    )
                    for refused_species in refused_species_ids:
                        molar_masses.pop(refused_species, None)
                    controls["molar_mass_kg_mol"] = molar_masses
                    result = real_dispatch(
                        provider,
                        replace(request, control_inputs=controls),
                    )
                    diagnostic = dict(result.diagnostic or {})
                    provider_statuses.append({
                        "status": result.status,
                        "missing_molar_mass": dict(
                            diagnostic.get("missing_molar_mass", {}) or {}
                        ),
                    })
                    return result

                local.setattr(
                    BuiltinEvaporationFluxProvider,
                    "dispatch",
                    capture_and_refuse_provider_channel,
                )

            if any(
                state in {
                    "unavailable_parent",
                    "provider_refusal",
                    "closure_proven_zero",
                }
                for state in state_by_species.values()
            ):
                real_close = EvaporationMixin._closed_langmuir_pressures_for_partials

                def capture_closure_publication(host, publication_Pa, **kwargs):
                    if host is sim or host is capacity_sim or host is step_sim:
                        closure_publications.append(set(publication_Pa))
                        closure_publications_by_host.setdefault(
                            id(host), []
                        ).append(set(publication_Pa))
                    return real_close(host, publication_Pa, **kwargs)

                local.setattr(
                    EvaporationMixin,
                    "_closed_langmuir_pressures_for_partials",
                    capture_closure_publication,
                )
            ledger_before_capacity = capacity_sim.atom_ledger.mol_by_account()
            transitions_before_capacity = len(capacity_sim.atom_ledger.transitions)
            capacity_result = None
            capacity_refusal = None
            active_provider_path[0] = "capacity"
            try:
                capacity_result = capacity_sim._compute_capacity_coupling_shadow_impl(
                    case_equilibrium
                )
            except EvaporationFluxRefusal as exc:
                capacity_refusal = exc
            capacity_overlay = copy.deepcopy(
                getattr(capacity_sim, "_last_vapour_batch_flux_overlay", {}) or {}
            )
            capacity_surface = copy.deepcopy(
                getattr(capacity_sim, "_last_langmuir_surface_oxygen", {}) or {}
            )
            assert capacity_sim.atom_ledger.mol_by_account() == ledger_before_capacity
            assert len(capacity_sim.atom_ledger.transitions) == transitions_before_capacity

            live_flux = None
            live_refusal = None
            active_provider_path[0] = "live"
            try:
                live_flux = sim._calculate_evaporation(case_equilibrium)
            except EvaporationFluxRefusal as exc:
                live_refusal = exc
            live_overlay = copy.deepcopy(
                getattr(sim, "_last_vapour_batch_flux_overlay", {}) or {}
            )
            live_surface = copy.deepcopy(
                getattr(sim, "_last_langmuir_surface_oxygen", {}) or {}
            )
            step_sim._get_equilibrium = lambda: case_equilibrium
            transition_start = len(step_sim.atom_ledger.transitions)
            active_provider_path[0] = "step"
            try:
                step_sim.step()
            except EvaporationFluxRefusal as exc:
                assert case_should_refuse, case_name
                assert exc.reason == expected_refusal_reason, case_name
            else:
                assert not case_should_refuse, case_name
            booked_by_production_step = {
                species
                for transition in step_sim.atom_ledger.transitions[transition_start:]
                for lot in (*transition.debits, *transition.credits)
                for species in lot.species_kg
            }

        unavailable = {
            species
            for species, state in state_by_species.items()
            if state in {
                "unavailable_catalog",
                "unavailable_parent",
                "unavailable_transport",
                "provider_refusal",
            }
        }
        zero_by_physics = {
            species
            for species, state in state_by_species.items()
            if state in {"catalog_proven_zero", "closure_proven_zero"}
        }
        should_refuse = case_should_refuse
        assert (live_refusal is not None) is should_refuse, case_name
        assert (capacity_refusal is not None) is should_refuse, case_name
        if should_refuse:
            assert live_refusal.reason == expected_refusal_reason, case_name
            assert capacity_refusal.reason == expected_refusal_reason, case_name

        excluded_booking_species = set(unavailable)
        if "Fe" in unavailable:
            excluded_booking_species.add("FeO")
        if "Si" in unavailable:
            excluded_booking_species.update({"SiO", "SiO2"})
        for zero_species in zero_by_physics:
            excluded_booking_species.add(zero_species)
            if zero_species == "Fe":
                excluded_booking_species.add("FeO")
            elif zero_species == "Si":
                excluded_booking_species.update({"SiO", "SiO2"})
        assert excluded_booking_species.isdisjoint(
            booked_by_production_step
        ), (
            case_name,
            booked_by_production_step,
        )

        if case_name.startswith("provider_refusal+") or case_name.endswith(
            "+provider_refusal"
        ):
            refused_species_ids = {
                species
                for species, state in state_by_species.items()
                if state == "provider_refusal"
            }
            for refused_species in refused_species_ids:
                assert any(
                    refused.get("missing_molar_mass", {}).get(
                        refused_species
                    )
                    for refused in provider_statuses
                ), (case_name, refused_species, provider_statuses)
            refused_species = next(iter(refused_species_ids))
            assert provider_request_maps, case_name
            first_refusal_request = next(
                index
                for index, request_species in enumerate(provider_request_maps)
                if refused_species in request_species
            )
            remaining_resolved_peer = any(
                species != refused_species and state == "resolved"
                for species, state in state_by_species.items()
            )
            if remaining_resolved_peer:
                assert any(
                    refused_species not in request_species
                    for request_species in provider_request_maps[
                        first_refusal_request + 1:
                    ]
                ), (case_name, provider_request_maps)
                assert any(
                    refused_species in publication
                    for publication in closure_publications
                ), (case_name, closure_publications)
                assert capacity_surface.get("applied") is True, case_name
                assert live_surface.get("applied") is True, case_name
            elif should_refuse:
                assert live_refusal is not None, case_name
                assert capacity_refusal is not None, case_name
                assert live_refusal.reason == expected_refusal_reason, case_name
                assert capacity_refusal.reason == expected_refusal_reason, case_name
            else:
                assert live_flux is not None, case_name
                assert capacity_result is not None, case_name
        if "unavailable_parent" in state_by_species.values():
            parent_species = {
                species
                for species, state in state_by_species.items()
                if state == "unavailable_parent"
            }
            for host in (capacity_sim, sim, step_sim):
                publications = closure_publications_by_host.get(id(host), [])
                assert publications, (case_name, id(host))
                assert parent_species.issubset(publications[0]), (
                    case_name,
                    publications,
                )
                assert all(
                    parent_species.isdisjoint(publication)
                    for publication in publications[1:]
                ), (case_name, publications)
                restarted = set(
                    host._vapour_batch_hour_availability.get(
                        "restarted_unavailable_species", ()
                    )
                )
                assert parent_species <= restarted, (case_name, restarted)
            if len(parent_species) == len(state_by_species):
                assert should_refuse, case_name
                assert live_refusal is not None, case_name
                assert capacity_refusal is not None, case_name
            assert all(
                parent_species.isdisjoint(membership)
                for membership in provider_stoich_memberships
            ), (case_name, provider_stoich_memberships)
            assert all(
                parent_species.isdisjoint(request_species)
                for request_species in provider_request_maps
            ), (case_name, provider_request_maps)
        if should_refuse:
            for refusal in (live_refusal, capacity_refusal):
                assert refusal.reason == expected_refusal_reason, case_name
                refusal_overlay = refusal.diagnostic[
                    "vapour_batch_flux_overlay"
                ]
                refused_species = {
                    str(notice.get("species"))
                    for notice in refusal_overlay.get("notices", ())
                    if isinstance(notice, dict)
                    and notice.get("kind") == "vapour_batch_unresolved"
                }
                assert unavailable == refused_species, (case_name, refusal_overlay)
                assert not any(
                    notice.get("kind")
                    == "capacity_solve_excludes_unavailable_species"
                    for notice in refusal_overlay.get("notices", ())
                    if isinstance(notice, dict)
                ), case_name
            continue

        assert isinstance(live_flux, EvaporationFlux), case_name
        assert isinstance(capacity_result, CapacityShadowResult), case_name
        if case_name in {
            "resolved+resolved",
            "unavailable_transport+resolved",
        }:
            assert capacity_surface.get("applied") is True, case_name
            assert live_surface.get("applied") is True, case_name
        if case_name == "unavailable_transport+resolved":
            assert "Fe" not in live_surface.get("refused_species", {}), case_name
            assert sim._vapour_batch_hour_availability[
                "unavailable_by_species"
            ].get("Fe") == "missing_transport_parameters_possible_continuum"
            assert any(
                notice.get("kind") == "vapour_batch_unresolved"
                and notice.get("species") == "Fe"
                and notice.get("reason")
                == "missing_transport_parameters_possible_continuum"
                for notice in live_overlay.get("notices", ())
                if isinstance(notice, dict)
            ), case_name
            assert all(
                "Fe" not in publication
                for publication in closure_publications
            ), (case_name, closure_publications)
        if "closure_proven_zero" in state_by_species.values():
            zero_species = next(
                species
                for species, state in state_by_species.items()
                if state == "closure_proven_zero"
            )
            assert closure_publications
            assert all(
                zero_species in publication
                for publication in closure_publications
            ), (case_name, closure_publications)
            assert provider_stoich_memberships
            assert all(
                zero_species in membership
                for membership in provider_stoich_memberships
            ), (case_name, provider_stoich_memberships)
            assert provider_request_maps
            assert provider_request_maps_by_path["capacity"]
            assert all(
                zero_species in request_species
                for request_species in provider_request_maps_by_path["capacity"]
            ), (case_name, provider_request_maps_by_path["capacity"])
            live_dispatch_expected = any(
                species != zero_species
                and state in {"resolved", "provider_refusal"}
                for species, state in state_by_species.items()
            )
            if live_dispatch_expected:
                assert provider_request_maps_by_path["live"]
                assert all(
                    zero_species not in request_species
                    for request_species in provider_request_maps_by_path["live"]
                ), (case_name, provider_request_maps_by_path["live"])
            else:
                assert provider_request_maps_by_path["live"] == [], case_name
                assert live_flux.species_kg_hr == {}, case_name
                assert sim._last_evaporation_flux_diagnostic["reason"] == (
                    "vapour_batch_all_channels_non_debiting"
                ), case_name
        live_rates_by_case[case_name] = dict(live_flux.species_kg_hr)
        capacity_rates_by_case[case_name] = dict(
            capacity_result.evaporation_flux_kg_hr
        )
        capacity_partials_by_case[case_name] = dict(
            capacity_result.partial_pressures_Pa
        )
        live_outcome_overlay = (
            live_refusal.diagnostic["vapour_batch_flux_overlay"]
            if live_refusal is not None
            else live_overlay
        )
        capacity_outcome_overlay = (
            capacity_refusal.diagnostic["vapour_batch_flux_overlay"]
            if capacity_refusal is not None
            else capacity_overlay
        )
        for overlay in (live_outcome_overlay, capacity_outcome_overlay):
            notices = [
                notice
                for notice in overlay.get("notices", ())
                if isinstance(notice, dict)
                and notice.get("kind") == "vapour_batch_unresolved"
            ]
            unavailable_noticed = {
                str(notice.get("species")) for notice in notices
            }
            assert unavailable == unavailable_noticed, (case_name, overlay)
            for species in zero_by_physics:
                assert overlay["batch_channel_states"][species] == "zero_by_physics"
                assert species not in unavailable_noticed

        live_flux_species = set(live_flux.species_kg_hr)
        assert unavailable.isdisjoint(live_flux_species), case_name
        for species, state in state_by_species.items():
            if state == "resolved":
                assert live_flux.species_kg_hr.get(species, 0.0) > 0.0, case_name
                assert capacity_result.evaporation_flux_kg_hr.get(species, 0.0) > 0.0, case_name
                assert live_overlay["selected_runtime_pa_by_species"][species] > 0.0, case_name
            elif species in unavailable:
                assert species not in live_flux_species, case_name
                assert species not in capacity_result.evaporation_flux_kg_hr, case_name

        if baseline_capacity_rates is None and all(
            state == "resolved" for state in state_by_species.values()
        ):
            baseline_capacity_rates = dict(
                capacity_result.evaporation_flux_kg_hr
            )
            baseline_capacity_partials = dict(
                capacity_result.partial_pressures_Pa
            )
        elif baseline_capacity_rates is not None:
            for species, state in state_by_species.items():
                peer = next(item for item in species_ids if item != species)
                if (
                    species in unavailable
                    and state_by_species[peer] == "resolved"
                ):
                    assert baseline_capacity_rates.get(species, 0.0) > 0.0, (
                        "the full-capacity comparator must supply the excluded "
                        "species at positive flux",
                        case_name,
                        species,
                    )
                    assert capacity_result.evaporation_flux_kg_hr.get(peer, 0.0) + 1e-12 >= (
                        baseline_capacity_rates.get(peer, 0.0)
                    ), (case_name, peer)

        bounds = [
            notice
            for notice in capacity_outcome_overlay.get("notices", ())
            if isinstance(notice, dict)
            and notice.get("kind") == "capacity_solve_excludes_unavailable_species"
        ]
        assert len(bounds) == int(bool(unavailable)), case_name
        if unavailable:
            bound = bounds[0]
            unresolved_reasons = {
                str(notice.get("species")): str(notice.get("reason"))
                for notice in capacity_outcome_overlay.get("notices", ())
                if isinstance(notice, dict)
                and notice.get("kind") == "vapour_batch_unresolved"
            }
            assert bound["excluded_species"] == [
                {"species": species, "reason": unresolved_reasons[species]}
                for species in sorted(unavailable)
            ], case_name
            assert bound["bound_direction"] == {
                "resolved_fluxes": "upper",
                "headspace_pressure": "lower",
            }

        for species, state in state_by_species.items():
            if state == "not_requested":
                batch = getattr(sim, "_last_vapour_batch", None)
                assert batch is not None
                assert species not in batch.requested_species_ids, case_name

            if state == "catalog_proven_zero":
                assert species not in sim._vapour_batch_hour_availability[
                    "transport_eligibility_by_species"
                ], case_name
                authority = live_flux.carrier_authority_by_species[species]
                assert authority["pressure"] == {
                    "kind": "zero_by_physics",
                    "evidence_ref": "b1318_narrow_catalog_zero_test_seam",
                }, case_name

    for case_name, state_by_species in cases:
        unavailable = {
            species
            for species, state in state_by_species.items()
            if state in {
                "unavailable_catalog",
                "unavailable_parent",
                "unavailable_transport",
                "provider_refusal",
            }
        }
        if len(unavailable) != 1:
            continue
        excluded_species = next(iter(unavailable))
        peer = next(item for item in species_ids if item != excluded_species)
        if state_by_species[peer] != "resolved":
            continue
        baseline_case = (
            "resolved+not_requested"
            if peer == "Fe"
            else "not_requested+resolved"
        )
        assert live_rates_by_case[case_name].get(peer, 0.0) == pytest.approx(
            live_rates_by_case[baseline_case].get(peer, 0.0),
            rel=1e-12,
            abs=1e-15,
        ), (case_name, peer)
        assert sum(capacity_partials_by_case[case_name].values()) <= (
            sum(baseline_capacity_partials.values()) + 1e-9
        ), case_name
        assert capacity_rates_by_case[case_name].get(peer, 0.0) == pytest.approx(
            capacity_rates_by_case[baseline_case].get(peer, 0.0),
            rel=1e-12,
            abs=1e-15,
        ), (case_name, peer)

    # The exact source id set normally feeds both PRE-RG activation and the
    # selector, so the seam-gap combination cannot arise through the public
    # builder. Exercise the narrowest real selector with the real catalog batch
    # and one omitted source value; the published peer pressure must survive.
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    sim._last_vapor_pressure_diagnostic = copy.deepcopy(vapor_pressure_diagnostic)
    from simulator.evaporation import _pre_rg_effective_pressure_source

    full_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures,
        equilibrium,
    )
    assert {"Fe", "Si"} <= full_source.species_ids
    silicon_pressure = full_source.pressure_pa("Si")
    assert silicon_pressure is not None
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=full_source,
    )
    assert batch is not None
    assert {"Fe", "Si"} <= batch.flux_active_species_ids
    partial_pressures, seam_overlay = flux_pressures_from_batch(
        batch,
        effective_pressure_source=EffectivePressureSource(
            PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID,
            {"Si": silicon_pressure},
        ),
    )
    assert partial_pressures == {"Si": silicon_pressure}
    assert seam_overlay["unresolved_missing_effective_pressure_species"] == ["Fe"]
    assert seam_overlay["batch_channel_states"]["Si"] == "eligible"
    assert seam_overlay["batch_channel_states"]["Fe"] == (
        TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH
    )


def test_langmuir_probe_uses_hkl_without_lj_at_free_molecular_pressure(
    monkeypatch,
):
    from simulator.condensation import _LENNARD_JONES_PARAMS
    from simulator.langmuir_surface_oxygen import (
        LangmuirTransport,
        _publisher_net_law_with_transport,
    )

    monkeypatch.delitem(_LENNARD_JONES_PARAMS, "Si", raising=False)
    transport = LangmuirTransport(
        temperature_K=1500.0,
        pipe_diameter_m=0.12,
        gas_temperature_K=1500.0,
        carrier_gas="N2",
        axial_stir_factor=0.0,
        radial_stir_factor=1.0,
        overhead_pressure_Pa=1.0e-9,
        gas_resistance_enabled=True,
        hkl_upper_bound_species=frozenset(),
        extrapolate_continuum=False,
        lennard_jones_property_rows_by_species={"Si": {}},
        lennard_jones_parameters_by_species={},
        transport_eligibility_by_species={"Si": "unavailable"},
    )

    conductance, bulk = _publisher_net_law_with_transport(
        species="Si",
        alpha=0.5,
        molar_mass_kg_per_mol=0.028085,
        bulk=0.0,
        transport=transport,
    )

    assert conductance > 0.0
    assert bulk == 0.0


def test_load_batch_clears_the_hourly_vapour_availability_cache():
    sim = _real_capacity_sim()
    feedstock_key = sim._batch_feedstock_key
    sim._vapour_batch_hour_availability = {
        "hour": 0,
        "member_species": ("Fe",),
        "admitted_member_species": (),
        "unavailable_by_species": {"Fe": "old-batch-exclusion"},
    }

    sim.load_batch(feedstock_key, mass_kg=1000.0)

    assert sim.melt.hour == 0
    assert sim._vapour_batch_hour_availability is None


def test_nonfinite_effective_pressure_is_unresolved_per_species():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = copy.deepcopy(sim._get_equilibrium())
    equilibrium.vapor_pressures_Pa["Fe"] = math.nan
    equilibrium.vapor_pressures_Pa["Na"] = 100.0
    sim._last_vapor_pressure_diagnostic[
        "backend_vapor_pressures_Pa"
    ] = dict(equilibrium.vapor_pressures_Pa)

    pressures, overlay, batch, error, _diagnostic, _source = (
        sim._resolve_evaporation_batch_flux_state(
            equilibrium,
            temperature_K=equilibrium.temperature_C + 273.15,
        )
    )

    assert not error
    assert batch is not None
    assert {"Fe", "Na"} <= batch.requested_species_ids
    assert "Fe" not in pressures
    assert pressures["Na"] > 0.0
    assert overlay["batch_channel_states"]["Na"] == "eligible"
    assert any(
        notice["kind"] == "vapour_batch_unresolved"
        and notice["species"] == "Fe"
        and notice["reason"] == "nonfinite_effective_pressure"
        for notice in overlay["notices"]
    )
    flux = sim._calculate_evaporation(equilibrium)
    assert flux.species_kg_hr.get("Na", 0.0) > 0.0


@pytest.mark.parametrize("path", ["live", "capacity"])
def test_negative_effective_pressure_is_unresolved_per_species(path, monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = copy.deepcopy(sim._get_equilibrium())
    pressures = {
        str(species): 0.0
        for species in equilibrium.vapor_pressures_Pa
    }
    pressures.update({"Fe": -1.0, "Na": 100.0})
    equilibrium.vapor_pressures_Pa.clear()
    equilibrium.vapor_pressures_Pa.update(pressures)
    sim._last_vapor_pressure_diagnostic[
        "backend_vapor_pressures_Pa"
    ] = dict(pressures)

    if path == "capacity":
        capacity = FiniteCapacity(0.05)
        cold_train = SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        )
        monkeypatch.setattr(
            sim,
            "_cold_train_capacity_policy",
            lambda: (capacity, cold_train),
        )
        result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim, equilibrium
        )
        assert isinstance(result, CapacityShadowResult)
        rates = result.evaporation_flux_kg_hr
    else:
        result = sim._calculate_evaporation(equilibrium)
        rates = result.species_kg_hr

    assert rates.get("Na", 0.0) > 0.0
    assert "Fe" not in rates
    overlay = sim._last_vapour_batch_flux_overlay
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        and notice.get("reason") == "negative_effective_pressure"
        for notice in overlay.get("notices", ())
        if isinstance(notice, dict)
    )


def test_aux_map_missing_molar_mass_is_species_local():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.vapor_pressures["metals"]["Unobtainium"] = {
        "parent_oxide": "Na2O",
    }

    aux = sim._build_evaporation_aux_maps({
        "Na": 0.0,
        "Unobtainium": 0.0,
    })

    assert aux.unavailable_by_species == {
        "Unobtainium": "missing_molar_mass",
    }
    assert aux.molar_masses_kg_mol["Na"] == pytest.approx(
        resolve_species_formula(
            "Na", sim.species_formula_registry
        ).molar_mass_kg_per_mol()
    )
    assert "Unobtainium" not in aux.molar_masses_kg_mol
    assert "Na" in aux.stoich_by_species


@pytest.mark.parametrize("path", ["live", "capacity"])
def test_zero_pressure_eligible_peer_reaches_real_provider_for_zero_proof(
    path,
    monkeypatch,
):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = sim._get_equilibrium()
    assert {"Fe", "Na"} <= set(equilibrium.vapor_pressures_Pa)
    equilibrium.vapor_pressures_Pa.update({
        species: 0.0 for species in equilibrium.vapor_pressures_Pa
    })
    equilibrium.vapor_pressures_Pa["Na"] = math.nan
    provider_calls = []
    real_dispatch = BuiltinEvaporationFluxProvider.dispatch

    def capture_real_provider(provider, request):
        result = real_dispatch(provider, request)
        provider_calls.append((
            dict(request.control_inputs[CONTROL_FLUX_PRESSURES_KEY]),
            dict(result.diagnostic or {}),
        ))
        return result

    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider, "dispatch", capture_real_provider
    )
    if path == "live":
        flux = sim._calculate_evaporation(
            equilibrium,
            overhead_partials_override_Pa={},
        )
    else:
        capacity = FiniteCapacity(0.05)
        cold_train = SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        )
        monkeypatch.setattr(
            sim,
            "_cold_train_capacity_policy",
            lambda: (capacity, cold_train),
        )
        result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim,
            equilibrium,
        )

    zero_pressure_calls = [
        call for call in provider_calls if call[0].get("Fe") == 0.0
    ]
    assert zero_pressure_calls
    assert all("Na" not in call[0] for call in zero_pressure_calls)
    assert any(
        call[1].get("zero_net_evaporation_flux", {}).get("Fe") == 0.0
        for call in zero_pressure_calls
    )
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Na"
        for notice in sim._last_vapour_batch_flux_overlay["notices"]
    )
    if path == "live":
        assert flux.species_kg_hr == {}
    else:
        assert isinstance(result, CapacityShadowResult)
        assert result.evaporation_flux_kg_hr == {}


def _record_transport_estimates(sim):
    """Record each owner estimate. Does not compute a conductance."""
    calls = []
    real_estimate = sim.overhead_model.estimate_transport_state

    def _estimate(flux, melt, *args, **kwargs):
        state = real_estimate(flux, melt, *args, **kwargs)
        calls.append(
            {
                "demand_kg_hr": float(flux.total_kg_hr),
                "capacity_kg_hr": float(state["pipe_conductance_kg_hr"]),
                "species_kg_hr": {
                    str(species): float(rate)
                    for species, rate in flux.species_kg_hr.items()
                    if float(rate) > 0.0
                },
            }
        )
        return state

    sim.overhead_model.estimate_transport_state = _estimate
    return calls


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


def _o2_fixture(dt_hr: float = 1.0, *, max_iterations: int = 50):
    pre_mol = 0.5
    source_intercept_kg_hr = 0.064
    source_slope_kg_hr_pa = 6.4e-6
    capacity = FiniteCapacity(0.032)

    def hk_flux(partials):
        return {
            "O2": max(
                0.0,
                source_intercept_kg_hr
                - source_slope_kg_hr_pa * partials["O2"],
            )
        }

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": pre_mol},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=capacity,
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
        max_iterations=max_iterations,
    )
    slope = source_slope_kg_hr_pa * dt_hr / M_O2 * 1000.0
    intercept = (
        pre_mol
        + source_intercept_kg_hr * dt_hr / M_O2
        - capacity.value_kg_hr * dt_hr / M_O2
    ) * 1000.0
    expected = intercept / (1.0 + slope)
    return result, expected


@pytest.mark.parametrize("dt_hr", [1.0, 0.5, 0.25])
def test_o2_only_picard_matrix_matches_hand_derived_fixed_point(dt_hr):
    result, expected = _o2_fixture(dt_hr)

    assert isinstance(result, CapacityShadowResult)
    assert result.partial_pressures_Pa["O2"] == pytest.approx(
        expected, rel=1.0e-9, abs=1.0e-6
    )
    assert result.oxygen.admitted_mol == pytest.approx(dt_hr)
    assert result.oxygen.relieved_mol == 0.0
    assert result.oxygen.held_mol >= 0.0
    assert result.oxygen.debited_mol == pytest.approx(
        result.oxygen.external_mol
        + result.oxygen.admitted_mol
        + result.oxygen.relieved_mol
    )
    assert all(
        later <= earlier + 1.0e-12
        for earlier, later in zip(
            result.max_delta_history_Pa,
            result.max_delta_history_Pa[1:],
        )
    )
    assert result.mass_closure_error_pct <= 5.0e-12
    assert result.authoritative is False


@pytest.mark.parametrize("dt_hr", [1.0, 0.5, 0.25])
def test_two_species_binding_fixture_preserves_non_o2_head_partition(dt_hr):
    frozen_pre = {"O2": 0.25, "N2": 1.0}
    frozen_copy = dict(frozen_pre)

    def hk_flux(partials):
        return {
            "O2": max(0.0, 0.048 - 4.0e-6 * partials["O2"]),
            "N2": max(0.0, 0.014 - 1.0e-6 * partials["N2"]),
        }

    result = solve_capacity_shadow(
        pre_holdup_mol=frozen_pre,
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=FiniteCapacity(0.008),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    assert frozen_pre == frozen_copy
    slope = 4.0e-6 * dt_hr / M_O2 * 1000.0
    intercept = (
        0.25 + 0.048 * dt_hr / M_O2 - 0.008 * dt_hr / M_O2
    ) * 1000.0
    assert result.partial_pressures_Pa == pytest.approx(
        {"N2": 0.0, "O2": intercept / (1.0 + slope)},
        rel=1.0e-9,
        abs=1.0e-6,
    )
    assert result.terminal_offgas_mol["N2"] == pytest.approx(
        result.bled_species_mol["N2"]
    )
    expected_n2_bled = (
        frozen_pre["N2"]
        + max(0.0, 0.014 - 1.0e-6 * 0.0) * dt_hr / M_N2
    )
    assert result.terminal_offgas_mol["N2"] == pytest.approx(
        expected_n2_bled
    )
    assert result.oxygen.held_mol >= 0.0
    assert result.oxygen.relieved_mol <= (
        result.bled_species_mol["O2"] - result.oxygen.external_mol
        - result.oxygen.admitted_mol + 1.0e-15
    )
    assert all(
        later <= earlier + 1.0e-9
        for earlier, later in zip(
            result.max_delta_history_Pa,
            result.max_delta_history_Pa[1:],
        )
    )
    assert result.mass_closure_error_pct <= 5.0e-12


@pytest.mark.xdist_group("serial")
def test_excluding_unavailable_species_bounds_resolved_capacity_flux():
    from simulator.condensation import _LENNARD_JONES_PARAMS

    def new_simulator():
        sim = _real_capacity_sim()
        sim.start_campaign(CampaignPhase.C0)
        sim.melt.temperature_C = 1600.0
        capacity = FiniteCapacity(0.05)
        cold_train = SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        )
        sim._cold_train_capacity_policy = lambda: (capacity, cold_train)
        return sim

    def solve_capacity(*, exclude_si):
        sim = new_simulator()
        with pytest.MonkeyPatch.context() as local:
            if exclude_si:
                local.delitem(_LENNARD_JONES_PARAMS, "Si", raising=False)
            equilibrium = sim._get_equilibrium()
            result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
                sim,
                equilibrium,
            )
            assert isinstance(result, CapacityShadowResult)
            if exclude_si:
                assert "Si" not in result.evaporation_flux_kg_hr
                overlay = sim._last_vapour_batch_flux_overlay
                exclusion = next(
                    notice
                    for notice in overlay["notices"]
                    if notice["kind"]
                    == "capacity_solve_excludes_unavailable_species"
                )
                assert exclusion["excluded_species"] == [{
                    "species": "Si",
                    "reason": "missing_transport_parameters_possible_continuum",
                }]
            return sim, result

    def run_live(*, exclude_si):
        sim = new_simulator()
        transitions_before = len(sim.atom_ledger.transitions)
        with pytest.MonkeyPatch.context() as local:
            if exclude_si:
                local.delitem(_LENNARD_JONES_PARAMS, "Si", raising=False)
            snapshot = sim.step()
        return sim, snapshot.evap_flux, sim.atom_ledger.transitions[
            transitions_before:
        ]

    excluded_sim, excluded = solve_capacity(exclude_si=True)
    _supplied_sim, supplied = solve_capacity(exclude_si=False)
    live_excluded_sim, live_excluded, live_transitions = run_live(
        exclude_si=True
    )
    _live_supplied_sim, live_supplied, _ = run_live(exclude_si=False)

    assert isinstance(excluded, CapacityShadowResult)
    assert isinstance(supplied, CapacityShadowResult)
    assert excluded.evaporation_flux_kg_hr["Na"] > 0.0
    assert supplied.evaporation_flux_kg_hr["Si"] > 0.0
    assert excluded.evaporation_flux_kg_hr["Na"] >= (
        supplied.evaporation_flux_kg_hr["Na"]
    )
    assert sum(excluded.partial_pressures_Pa.values()) <= sum(
        supplied.partial_pressures_Pa.values()
    )
    assert live_excluded.species_kg_hr["Na"] > 0.0
    assert live_excluded.species_kg_hr["Na"] >= live_supplied.species_kg_hr.get(
        "Na", 0.0
    )
    assert "Si" not in live_excluded.species_kg_hr
    assert all(
        not (
            debit.account == "process.cleaned_melt"
            and "SiO2" in debit.species_kg
        )
        for transition in live_transitions
        for debit in transition.debits
    )
    assert all(
        not {"Si", "SiO"}.intersection(lot.species_kg)
        for transition in live_transitions
        for lot in (*transition.debits, *transition.credits)
    )
    live_exclusion = next(
        notice
        for notice in live_excluded_sim._last_vapour_batch_flux_overlay[
            "notices"
        ]
        if notice["kind"] == "vapour_batch_unresolved"
        and notice.get("species") == "Si"
    )
    assert live_exclusion["reason"] == (
        "missing_transport_parameters_possible_continuum"
    )
    excluded_flag = next(
        notice
        for notice in excluded_sim._last_vapour_batch_flux_overlay["notices"]
        if notice["kind"] == "capacity_solve_excludes_unavailable_species"
    )
    assert excluded_flag["excluded_species"] == [{
        "species": "Si",
        "reason": "missing_transport_parameters_possible_continuum",
    }]


@pytest.mark.xdist_group("serial")
def test_disabled_gas_resistance_keeps_missing_lj_carrier_available(monkeypatch):
    from simulator.condensation import _LENNARD_JONES_PARAMS

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    kernel = sim.setpoints.setdefault("chemistry_kernel", {})
    kernel["evaporation_series_resistance"] = {
        "gas_resistance_enabled": False,
    }
    monkeypatch.delitem(_LENNARD_JONES_PARAMS, "Si", raising=False)

    equilibrium = sim._get_equilibrium()
    assert "Si" in equilibrium.vapor_pressures_Pa
    sim._calculate_evaporation(equilibrium)

    availability = sim._vapour_batch_hour_availability
    assert "Si" in availability["member_species"]
    assert "Si" in availability["admitted_member_species"]
    assert availability["transport_eligibility_by_species"]["Si"] == (
        "gas_resistance_disabled"
    )
    assert "Si" not in availability["unavailable_by_species"]
    assert not any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Si"
        for notice in sim._last_vapour_batch_flux_overlay["notices"]
    )


@pytest.mark.xdist_group("serial")
def test_live_langmuir_and_provider_share_lj_collision_converter(monkeypatch):
    from simulator import condensation
    from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = math.log10(
        DEFAULT_VACUUM_FLOOR_BAR
    )
    sim._sync_oxygen_reservoir_mirror()
    sim.vapor_pressures["metals"]["Fe"].update({
        "critical_temperature_K": 6000.0,
        "critical_pressure_atm": 500.0,
    })
    fe_mass = resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Fe": fe_mass},
        source="shared LJ conversion production-path pin",
        material_origin="feedstock",
    )
    sim._melt_headspace_composition_mbar = {"Fe": 0.001}
    equilibrium = sim._get_equilibrium()

    callers = []
    resolve_collision_parameters = (
        condensation._lennard_jones_collision_parameters
    )

    def record_collision_parameter_owner(*args, **kwargs):
        import sys

        callers.append(sys._getframe(1).f_code.co_name)
        return resolve_collision_parameters(*args, **kwargs)

    monkeypatch.setattr(
        condensation,
        "_lennard_jones_collision_parameters",
        record_collision_parameter_owner,
    )
    sim._calculate_evaporation(equilibrium)

    assert "_publisher_net_law_with_transport" in callers
    assert "dispatch" in callers
    assert "_series_resistance_evaporation_flux_kg_m2_s" in callers


def test_relief_is_capped_by_post_admission_remainder():
    partition = partition_melt_oxygen(
        bled_o2_mol=10.0,
        overhead_o2_mol=10.0,
        external_o2_holdup_mol=2.0,
        capacity=FiniteCapacity(M_O2),
        dt_hr=1.0,
        p_o2_Pa=1.0e6,
        k_relief_kg_hr_Pa=1.0,
        p_open_Pa=1.0,
        molar_mass_kg_mol=M_O2,
    )

    assert partition.external_mol == pytest.approx(2.0)
    assert partition.admitted_mol == pytest.approx(1.0)
    assert partition.relieved_mol == pytest.approx(7.0)
    assert partition.held_mol == 0.0
    assert partition.debited_mol == pytest.approx(10.0)


def test_accumulator_off_is_finite_capacity_partition_parity():
    controls = {
        "bled_o2_mol": 10.0,
        "overhead_o2_mol": 10.0,
        "external_o2_holdup_mol": 2.0,
        "capacity": FiniteCapacity(M_O2),
        "dt_hr": 1.0,
        "p_o2_Pa": 1.0e6,
        "k_relief_kg_hr_Pa": 1.0,
        "p_open_Pa": 1.0,
        "molar_mass_kg_mol": M_O2,
    }

    p2_3 = partition_melt_oxygen(**controls)
    accumulator_off = partition_melt_oxygen(
        **controls,
        accumulator_enabled=False,
        cistern_fill_kg=3.0,
        cavern_capacity_kg=4.0,
    )

    assert accumulator_off == p2_3
    assert accumulator_off.accumulated_mol == 0.0


def test_accumulator_precedes_relief_and_full_cistern_restores_relief():
    controls = {
        "bled_o2_mol": 10.0,
        "overhead_o2_mol": 10.0,
        "external_o2_holdup_mol": 0.0,
        "capacity": FiniteCapacity(M_O2),
        "dt_hr": 1.0,
        "p_o2_Pa": 1.0e6,
        "k_relief_kg_hr_Pa": 1.0,
        "p_open_Pa": 1.0,
        "molar_mass_kg_mol": M_O2,
        "accumulator_enabled": True,
        "cavern_capacity_kg": 5.0 * M_O2,
    }

    filling = partition_melt_oxygen(**controls, cistern_fill_kg=0.0)
    full = partition_melt_oxygen(
        **controls,
        cistern_fill_kg=5.0 * M_O2,
    )

    assert filling.admitted_mol == pytest.approx(1.0)
    assert filling.accumulated_mol == pytest.approx(5.0)
    assert filling.relieved_mol == pytest.approx(4.0)
    assert filling.held_mol == 0.0
    assert full.admitted_mol == pytest.approx(1.0)
    assert full.accumulated_mol == 0.0
    assert full.relieved_mol == pytest.approx(9.0)
    assert full.held_mol == 0.0


def test_relief_is_additional_to_zero_conductance_and_avoids_vessel_refusal():
    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 10.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _partials: {},
        capacity=FiniteCapacity(M_O2),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=M_O2 * 5.0e-4,
        p_open_Pa=9000.0,
        vessel_rating_Pa=9800.0,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.bled_species_mol["O2"] == pytest.approx(
        result.oxygen.relieved_mol
    )
    assert result.oxygen.external_mol == 0.0
    assert result.oxygen.admitted_mol == 0.0
    assert result.oxygen.relieved_mol > 0.0
    assert result.partial_pressures_Pa["O2"] < 9800.0
    assert result.mass_closure_error_pct <= 5.0e-12


def test_picard_source_uses_frozen_inventory_depletion_vector():
    sim = _real_capacity_sim()
    raw = EvaporationFlux(
        species_kg_hr={"Fe": 1000.0},
        alpha_authority_status_by_species={
            "Fe": "analytical_upper_bound",
        },
    )
    raw.update_totals()
    frozen_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    effective_rates = sim._analytic_evaporation_depletion_rates(
        raw.species_kg_hr,
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg=frozen_melt,
        available_o2_kg=0.0,
    )
    live_effective = sim._apply_analytic_evaporation_depletion(raw)

    assert 0.0 < effective_rates["Fe"] < raw.species_kg_hr["Fe"]
    assert live_effective.species_kg_hr == pytest.approx(effective_rates)
    assert live_effective.alpha_authority_status_by_species == {
        "Fe": "analytical_upper_bound",
    }

    fe_molar_mass = resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"Fe": fe_molar_mass},
        flux_kg_hr_at_partials=lambda _partials: dict(effective_rates),
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.evaporation_flux_kg_hr == pytest.approx(effective_rates)
    assert result.mass_closure_error_pct <= 5.0e-12


def test_non_convergent_picard_returns_typed_refusal_without_last_iterate():
    result, _ = _o2_fixture(max_iterations=1)

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == "picard_non_convergence"
    assert result.iterations == 1
    assert result.authoritative is False


@pytest.mark.parametrize("refusal_site", ("flux", "overhead_source"))
def test_evaporation_refusal_is_not_swallowed_by_shadow_callbacks(refusal_site):
    diagnostic = {
        "reason": "viscous_p_bulk_transport_out_of_domain",
        "evaporation_flux_status": "not_evaluated",
        "evaporation_flux_kg_hr": None,
    }

    def raise_refusal(_partials):
        raise EvaporationFluxRefusal(diagnostic["reason"], diagnostic)

    flux_callback = (
        raise_refusal if refusal_site == "flux" else lambda _partials: {"O2": 0.0}
    )
    overhead_callback = (
        raise_refusal
        if refusal_site == "overhead_source"
        else lambda _partials: {"O2": 0.0}
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        solve_capacity_shadow(
            pre_holdup_mol={"O2": 0.0},
            molar_mass_kg_mol={"O2": M_O2},
            flux_kg_hr_at_partials=flux_callback,
            capacity=FiniteCapacity(0.032),
            head_bled_species_mol={},
            external_o2_holdup_mol=0.0,
            temperature_K=T_FOR_1000_PA_PER_MOL,
            volume_m3=1.0,
            dt_hr=1.0,
            bleed_conductance_kg_s=1.0,
            downstream_pressure_Pa=0.0,
            k_relief_kg_hr_Pa=1.0e-30,
            p_open_Pa=1.0e9,
            overhead_source_mol_hr_at_partials=overhead_callback,
            vessel_rating_Pa=1.0e12,
        )

    assert exc_info.value.reason == diagnostic["reason"]
    assert exc_info.value.diagnostic == diagnostic


def test_core_refusal_leaves_record_and_ledger_close_report_unchanged(
    monkeypatch,
):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    cold_train = thermal_train_parameters_from_mapping(payload).cold_train
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity_from_hardware(cold_train), cold_train),
    )
    equilibrium = sim._get_equilibrium()
    record_before = _canonical_bytes(asdict(sim.record))
    ledger_before = _canonical_bytes(sim.atom_ledger.close_report())

    monkeypatch.setattr(
        capacity_coupling,
        "solve_capacity_shadow",
        lambda **_kwargs: CapacityShadowRefusal(
            reason="picard_non_convergence",
            iterations=50,
        ),
    )
    result = sim._compute_capacity_coupling_shadow(equilibrium)

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert _canonical_bytes(asdict(sim.record)) == record_before
    assert _canonical_bytes(sim.atom_ledger.close_report()) == ledger_before


def test_capacity_shadow_refuses_unresolved_vapour_before_empty_source_solve(
    monkeypatch,
):
    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    sim._cold_train_capacity_policy = lambda: (
        FiniteCapacity(1.0),
        SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        ),
    )
    sim._overhead_holdup_mol = lambda: {}
    overlay = {
        "selection_source": TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "Na",
            "hour": 3,
            "reason": TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
        }],
    }
    resolve_error = {}
    sim._resolve_evaporation_batch_flux_state = lambda *_args, **_kwargs: (
        {}, overlay, None, resolve_error, {}, None
    )

    def forbidden_controls(*_args, **_kwargs):
        pytest.fail("capacity path built controls for an unresolved source")

    def forbidden_solve(**_kwargs):
        pytest.fail("capacity path solved an empty vapour source")

    sim._evaporation_flux_control_inputs = forbidden_controls
    monkeypatch.setattr(capacity_coupling, "solve_capacity_shadow", forbidden_solve)

    with pytest.raises(EvaporationFluxRefusal) as refusal:
        PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim,
            SimpleNamespace(
                liquid_fraction=1.0,
                vapor_pressures_Pa={"Na": 1.0},
            ),
        )

    assert refusal.value.reason == (
        "vapour_batch_no_debiting_pressure_outcome"
    )
    assert refusal.value.diagnostic["evaporation_flux_kg_hr"] == {}
    assert refusal.value.diagnostic["vapour_batch_flux_overlay"] == overlay


@pytest.mark.parametrize(
    ("temperature_C", "liquid_fraction"),
    ((1600.0, 0.0), (350.0, 1.0)),
)
def test_capacity_zero_liquid_precedes_interpolation_guard(
    monkeypatch,
    temperature_C,
    liquid_fraction,
):
    sim = _real_capacity_sim()
    sim.melt.temperature_C = temperature_C
    capacity = FiniteCapacity(1.0)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    sim._last_vapor_pressure_diagnostic = {
        "source": "served_not_executed",
        "served_from": {
            "source": "cached_interpolated",
            "interpolation_mode": "linear",
        },
    }

    class InterpolatedEquilibrium:
        def __init__(self):
            self.liquid_fraction = liquid_fraction
            self.temperature_C = temperature_C
            self.status = "ok"
            self.vapor_pressures_Pa = {}
            self.activity_coefficients = {}
            self.vapor_pressures_source = {}
            self.diagnostics = {}

    equilibrium = InterpolatedEquilibrium()
    if liquid_fraction == 0.0:
        real_solve = capacity_coupling.solve_capacity_shadow
        solve_calls = []

        def record_capacity_solve(**kwargs):
            solve_calls.append(kwargs)
            return real_solve(**kwargs)

        monkeypatch.setattr(
            capacity_coupling,
            "solve_capacity_shadow",
            record_capacity_solve,
        )
        monkeypatch.setattr(
            sim,
            "_resolve_evaporation_batch_flux_state",
            lambda *_args, **_kwargs: pytest.fail(
                "zero-liquid capacity must not resolve interpolated vapour"
            ),
        )
        result = sim._compute_capacity_coupling_shadow_impl(equilibrium)
        assert isinstance(result, CapacityShadowResult)
        assert result.evaporation_flux_kg_hr == {}
        assert solve_calls
        live_flux = sim._calculate_evaporation(equilibrium)
        assert isinstance(live_flux, EvaporationFlux)
        assert live_flux.species_kg_hr == {}
    else:
        monkeypatch.setattr(
            sim,
            "_evaporation_flux_control_inputs",
            lambda *_args, **_kwargs: pytest.fail(
                "interpolated flux guard must precede capacity controls"
            ),
        )
        with pytest.raises(EvaporationFluxRefusal) as refusal:
            sim._compute_capacity_coupling_shadow_impl(equilibrium)

        assert refusal.value.reason == "interpolated_equilibrium_not_flux_eligible"
        notices = refusal.value.diagnostic["vapour_batch_flux_overlay"]["notices"]
        assert notices
        assert all(
            notice["reason"] == "interpolated_equilibrium_not_flux_eligible"
            for notice in notices
        )


def test_zero_liquid_capacity_never_reads_interpolated_vapour_inputs(monkeypatch):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    sim._last_vapor_pressure_diagnostic = {
        "source": "served_not_executed",
        "served_from": {"source": "cached_interpolated"},
        "activities": {"Fe": 0.5},
    }
    capacity = FiniteCapacity(1.0)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    monkeypatch.setattr(
        sim,
        "_headspace_bleed_conductance_kg_s",
        lambda **_kwargs: 0.0,
    )

    class InterpolatedZeroLiquid:
        liquid_fraction = 0.0

        @property
        def vapor_pressures_Pa(self):
            raise AssertionError("zero-liquid guard read interpolated pressures")

        @property
        def vapor_pressures_source(self):
            raise AssertionError("zero-liquid guard read pressure provenance")

        @property
        def activity_coefficients(self):
            raise AssertionError("zero-liquid guard read interpolated activities")

        @property
        def diagnostics(self):
            raise AssertionError("zero-liquid guard read interpolation diagnostics")

    requests = []

    def capture_dispatch(_self, request):
        requests.append(dict(request.control_inputs))
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider,
        "dispatch",
        capture_dispatch,
    )
    result = sim._compute_capacity_coupling_shadow_impl(
        InterpolatedZeroLiquid()
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.evaporation_flux_kg_hr == {}
    assert requests
    for controls in requests:
        assert controls["vapour_batch_flux_pressures_Pa"] == {}
        assert controls["vapor_pressures_Pa"] == {}
        assert controls["vapor_pressures_source"] == {}
        assert controls["vapor_pressure_activities"] == {}


def test_zero_liquid_capacity_checks_retained_holdup_vessel_rating(monkeypatch):
    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {
            "Na": resolve_species_formula(
                "Na", sim.species_formula_registry
            ).molar_mass_kg_per_mol()
        },
        source="b1318 retained headspace fixture",
        material_origin="feedstock",
    )
    sim._headspace_bleed_conductance_kg_s = lambda **_kwargs: 0.0
    capacity = FiniteCapacity(1.0)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 0.5,
            "vessel_rating_Pa": 1.0,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    equilibrium = SimpleNamespace(
        liquid_fraction=0.0,
        temperature_C=1600.0,
        status="ok",
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        activity_coefficients={},
        diagnostics={},
    )

    result = sim._compute_capacity_coupling_shadow_impl(equilibrium)

    assert isinstance(result, CapacityShadowRefusal)
    assert result.reason.startswith("vessel_total_pressure_exceeds_rating:")
    assert result.iterations > 0


def test_step_provenance_guard_does_not_evaluate_interpolated_pressure_property(
    monkeypatch,
):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    capacity = FiniteCapacity(1.0)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    monkeypatch.setattr(
        sim,
        "_headspace_bleed_conductance_kg_s",
        lambda **_kwargs: 0.0,
    )

    class InterpolatedZeroLiquid:
        liquid_fraction = 0.0
        temperature_C = 1600.0
        status = "ok"
        warnings = []

        @property
        def vapor_pressures_Pa(self):
            raise AssertionError("step evaluated interpolated pressure property")

        @property
        def vapor_pressures_source(self):
            raise AssertionError("step evaluated interpolated source property")

        @property
        def activity_coefficients(self):
            raise AssertionError("step evaluated interpolated activities")

        @property
        def diagnostics(self):
            raise AssertionError("step evaluated interpolated diagnostics")

    equilibrium = InterpolatedZeroLiquid()

    def get_interpolated_equilibrium():
        sim._last_vapor_pressure_diagnostic = {
            "source": "served_not_executed",
            "served_from": {"source": "cached_interpolated"},
        }
        return equilibrium

    monkeypatch.setattr(sim, "_get_equilibrium", get_interpolated_equilibrium)
    shadow_results = []
    real_shadow = sim._compute_capacity_coupling_shadow

    def capture_shadow(value):
        result = real_shadow(value)
        shadow_results.append(result)
        return result

    monkeypatch.setattr(sim, "_compute_capacity_coupling_shadow", capture_shadow)
    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider,
        "dispatch",
        lambda _self, _request: SimpleNamespace(
            status="ok", diagnostic={"evaporation_flux_kg_hr": {}}
        ),
    )

    sim.step()

    assert len(shadow_results) == 1
    assert isinstance(shadow_results[0], CapacityShadowResult)
    assert shadow_results[0].evaporation_flux_kg_hr == {}


def test_estimated_lennard_jones_inputs_drive_continuum_hour_with_flag(
    monkeypatch,
):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.condensation import (
        _LENNARD_JONES_PARAMS,
        _resolve_lennard_jones_parameters,
    )
    from simulator.transport_constants import VISCOUS_KNUDSEN_MAX

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    sim.vapor_pressures["metals"]["Fe"].update({
        "critical_temperature_K": 6000.0,
        "critical_pressure_atm": 500.0,
    })
    monkeypatch.delitem(_LENNARD_JONES_PARAMS, "Fe")
    equilibrium = sim._get_equilibrium()
    provider_controls = []
    provider_transport_sidecars = []
    real_dispatch = BuiltinEvaporationFluxProvider.dispatch

    def capture_estimated_transport(provider, request):
        provider_controls.append(dict(request.control_inputs))
        provider_transport_sidecars.append(
            getattr(
                request.control_inputs.get("vapour_batch_flux_overlay"),
                "transport_sidecar",
                {},
            )
        )
        return real_dispatch(provider, request)

    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider,
        "dispatch",
        capture_estimated_transport,
    )

    flux = sim._calculate_evaporation(
        equilibrium,
        overhead_partials_override_Pa={"N2": 1.0e4},
    )

    assert isinstance(flux, EvaporationFlux)
    assert flux.species_kg_hr.get("Fe", 0.0) > 0.0
    assert provider_controls
    transport_sidecar = provider_transport_sidecars[0]
    property_row = transport_sidecar[
        "lennard_jones_property_rows_by_species"
    ]["Fe"]
    expected = _resolve_lennard_jones_parameters(
        "Fe", property_row
    )
    assert expected["status"] == "estimated"
    assert expected["flag"] == "lj_estimated:tc_pc"
    assert property_row["critical_temperature_K"] == 6000.0
    assert property_row["critical_pressure_atm"] == 500.0
    overlay = provider_controls[0]["vapour_batch_flux_overlay"]
    assert "lennard_jones_property_rows_by_species" not in provider_controls[0]
    assert "lennard_jones_parameters_by_species" not in provider_controls[0]
    assert "transport_eligibility_by_species" not in provider_controls[0]
    assert not any(
        notice.get("kind") == "transport_parameters_estimated"
        for notice in overlay.get("notices", ())
        if isinstance(notice, dict)
    )
    assert any(
        notice.get("kind") == "transport_parameters_estimated"
        and notice.get("species") == "Fe"
        and notice.get("method") == "tc_pc"
        and notice.get("flag") == "lj_estimated:tc_pc"
        and notice.get("inputs", {}).get("critical_temperature_K") == 6000.0
        and notice.get("inputs", {}).get("critical_pressure_atm") == 500.0
        for notice in sim._last_vapour_batch_flux_overlay.get("notices", ())
        if isinstance(notice, dict)
    )
    series = (
        sim._last_evaporation_flux_diagnostic[
            "evaporation_series_resistance"
        ]["Fe"]
    )
    assert 0.0 < series["knudsen_number"] < VISCOUS_KNUDSEN_MAX
    assert series["d_ab_m2_s"] > 0.0


def test_lennard_jones_owner_uses_critical_point_preference():
    from simulator.condensation import _resolve_lennard_jones_parameters

    estimate = _resolve_lennard_jones_parameters(
        "Xe",
        {
            "critical_temperature_K": 100.0,
            "critical_pressure_atm": 10.0,
        },
    )

    assert estimate["status"] == "estimated"
    assert estimate["species"] == "Xe"
    # Independent pin: 100/10=10, cube-root(10)=2.15443469, then
    # 2.44*2.15443469=5.25682064 Å; 0.77*100 K=77 K.
    assert estimate["sigma_angstrom"] == pytest.approx(5.256821)
    assert estimate["epsilon_over_k_K"] == pytest.approx(77.0)
    assert estimate["method"] == "tc_pc"
    assert estimate["flag"] == "lj_estimated:tc_pc"
    assert estimate["inputs"] == {
        "critical_temperature_K": 100.0,
        "critical_pressure_atm": 10.0,
    }
    invalid_mass_estimate = _resolve_lennard_jones_parameters(
        "Xe",
        {
            "critical_temperature_K": 100.0,
            "critical_pressure_atm": 10.0,
            "molar_mass_g_mol": "invalid",
        },
    )
    assert invalid_mass_estimate["status"] == "estimated"
    assert invalid_mass_estimate["molar_mass_g_mol"] is None


def test_lennard_jones_owner_uses_boiling_density_and_supported_le_bas():
    from simulator.condensation import _resolve_lennard_jones_parameters

    bsl = _resolve_lennard_jones_parameters(
        "Fe",
        {
            "formula": "Fe",
            "molar_mass_g_mol": 55.845,
            "boiling_point_C": 2862.0,
            "liquid_density_g_cm3": 7.0,
        },
    )
    assert bsl["method"] == "bsl_boiling_density"
    assert bsl["flag"] == "lj_estimated:bsl_boiling_density"
    # Independent pin: 55.845/7=7.977857 cm^3/mol, cube-root≈1.998155,
    # and 1.18*1.998155≈2.357823 Å; Tb=3135.15 K, 1.15*Tb=3605.4225 K.
    assert bsl["sigma_angstrom"] == pytest.approx(2.357823)
    assert bsl["epsilon_over_k_K"] == pytest.approx(3605.4225)

    le_bas = _resolve_lennard_jones_parameters(
        "H2O",
        {"formula": "H2O", "boiling_point_C": 100.0},
    )
    assert le_bas["method"] == "le_bas+bsl_boiling"
    assert le_bas["flag"] == "lj_estimated:le_bas+bsl_boiling"
    assert le_bas["inputs"]["le_bas_molar_volume_cm3_mol"] == pytest.approx(14.8)


def test_lennard_jones_owner_uses_sourced_and_legacy_proxy_rows():
    from simulator.condensation import (
        _LENNARD_JONES_PARAMS,
        _resolve_lennard_jones_parameters,
    )

    assert _resolve_lennard_jones_parameters("Na", {})["status"] == "tabulated"
    class_proxy = _resolve_lennard_jones_parameters("CrO2", {})
    class_proxy_with_critical_data = _resolve_lennard_jones_parameters(
        "CrO2",
        {"critical_temperature_K": 3000.0, "critical_pressure_atm": 100.0},
    )
    assert class_proxy["status"] == "estimated"
    assert class_proxy["method"] == "legacy_code_proxy"
    assert class_proxy["flag"] == "lj_estimated:legacy_code_proxy"
    assert class_proxy_with_critical_data == class_proxy
    assert (
        class_proxy["sigma_angstrom"],
        class_proxy["epsilon_over_k_K"],
        class_proxy["molar_mass_g_mol"],
    ) == (3.374, 71.4, 83.9941)
    for species in ("Fe", "Mg", "Mn", "Cr", "Al", "Ti", "Ca", "SiO"):
        estimate = _resolve_lennard_jones_parameters(species, {})
        row = f"simulator.condensation._LENNARD_JONES_PARAMS[{species!r}]"
        assert estimate["status"] == "estimated", species
        assert estimate["method"] == "legacy_code_proxy", species
        assert estimate["flag"] == "lj_estimated:legacy_code_proxy", species
        assert estimate["provenance"]["row"] == row, species
        assert estimate["inputs"]["legacy_row"] == row, species
        assert (
            estimate["sigma_angstrom"],
            estimate["epsilon_over_k_K"],
            estimate["molar_mass_g_mol"],
        ) == _LENNARD_JONES_PARAMS[species]


def test_capacity_resolution_decision_is_owned_by_evaporation() -> None:
    overlay = {
        "selection_source": TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
        "batch_channel_states": {"Na": "refusal", "SiO": "eligible"},
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "Na",
            "hour": 3,
            "reason": TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
        }],
    }

    assert EvaporationMixin._vapour_batch_resolution_refusal(
        SimpleNamespace(),
        {},
        overlay,
        flux_pressures={"SiO": 1.0},
        include_unresolved_notices=True,
    ) is None
    assert isinstance(
        EvaporationMixin._vapour_batch_resolution_refusal(
            SimpleNamespace(),
            {},
            overlay,
            flux_pressures={"Na": 1.0},
            include_unresolved_notices=True,
            capacity_solve=True,
        ),
        EvaporationFluxRefusal,
    )
    refusal = EvaporationMixin._vapour_batch_resolution_refusal(
        SimpleNamespace(),
        {},
        overlay,
        flux_pressures={},
        include_unresolved_notices=True,
        capacity_solve=True,
    )

    assert isinstance(refusal, EvaporationFluxRefusal)
    assert refusal.reason == "vapour_batch_no_debiting_pressure_outcome"
    assert refusal.diagnostic["vapour_batch_flux_overlay"] == overlay

    empty_pre_rg = {
        "effective_pressure_zero_reason": (
            "pre_rg_backend_below_effective_pressure_threshold"
        ),
        "batch_pa_by_species": {"SiO": 1.0},
        "batch_channel_states": {"SiO": "refusal"},
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "SiO",
            "hour": 3,
            "reason": "catalog_refusal",
        }],
    }
    from simulator.evaporation import _provider_flux_outcome_refusal

    assert _provider_flux_outcome_refusal(
        SimpleNamespace(),
        "ok",
        {},
        {"SiO": "provider_flux_outcome_unavailable"},
        empty_pre_rg,
        selector_overlay={},
    ) is None
    refusal = EvaporationMixin._vapour_batch_resolution_refusal(
        SimpleNamespace(),
        {},
        empty_pre_rg,
        flux_pressures={},
        include_unresolved_notices=True,
        capacity_solve=True,
    )
    assert isinstance(refusal, EvaporationFluxRefusal)
    assert refusal.reason == "vapour_batch_no_debiting_pressure_outcome"


@pytest.mark.parametrize("path", ["live", "capacity"])
def test_live_keeps_empty_unresolved_hour_capacity_refuses(path):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = copy.deepcopy(sim._get_equilibrium())
    pressures = {"Fe": math.nan}
    equilibrium.vapor_pressures_Pa.clear()
    equilibrium.vapor_pressures_Pa.update(pressures)
    sim._last_vapor_pressure_diagnostic[
        "backend_vapor_pressures_Pa"
    ] = dict(pressures)

    if path == "capacity":
        capacity = FiniteCapacity(0.05)
        cold_train = SimpleNamespace(
            accumulator_enabled=False,
            relief={
                "k_relief_kg_hr_Pa": 1.0e-30,
                "p_open_Pa": 1.0e9,
                "vessel_rating_Pa": 1.0e12,
            },
        )
        sim._cold_train_capacity_policy = lambda: (capacity, cold_train)

    if path == "live":
        flux = sim._calculate_evaporation(equilibrium)
        assert isinstance(flux, EvaporationFlux)
        overlay = sim._last_vapour_batch_flux_overlay
    else:
        with pytest.raises(EvaporationFluxRefusal) as refusal:
            PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
                sim,
                equilibrium,
            )
        assert refusal.value.reason == "vapour_batch_no_debiting_pressure_outcome"
        overlay = refusal.value.diagnostic["vapour_batch_flux_overlay"]
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        and notice.get("reason") == "nonfinite_effective_pressure"
        for notice in overlay["notices"]
        if isinstance(notice, dict)
    )


def test_provider_terminal_classifier_rejects_series_only_peer():
    from simulator import evaporation as evaporation_module

    diagnostic = {
        "evaporation_series_resistance": {"Na": {"flux_kg_hr": 0.25}},
    }
    overlay = {
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "K",
            "hour": 3,
            "reason": "k_catalog_channel_refused",
        }],
    }
    classify = evaporation_module._provider_flux_outcome_refusal
    refusal = classify(
        SimpleNamespace(),
        "ok",
        diagnostic,
        {"K": "k_catalog_channel_refused"},
        overlay,
        capacity_solve=True,
    )
    assert isinstance(refusal, EvaporationFluxRefusal)
    assert refusal.reason == "vapour_batch_no_debiting_pressure_outcome"

    # A provider-level unavailable status still proceeds when the overlay
    # proves another channel's physical zero. The missing channel remains
    # unavailable in the overlay instead of turning the zero into a refusal.
    zero_and_unavailable_overlay = {
        "batch_channel_states": {"Fe": "zero_by_physics", "K": "refusal"},
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "K",
            "hour": 3,
            "reason": "alpha authority unavailable",
        }],
    }
    assert classify(
        SimpleNamespace(),
        "unavailable",
        {
            "missing_transport_parameters": {
                "K": {
                    "status": "unavailable",
                    "reason": "transport input unavailable",
                },
            },
            "missing_alpha": {"K": {"reason": "alpha authority unavailable"}},
        },
        {"K": "alpha authority unavailable"},
        zero_and_unavailable_overlay,
        capacity_solve=True,
    ) is None
    same_species_unavailable = classify(
        SimpleNamespace(),
        "ok",
        {},
        {"Fe": "conflicting unavailable notice"},
        {
            "batch_channel_states": {"Fe": "zero_by_physics"},
            "notices": [{
                "kind": "vapour_batch_unresolved",
                "species": "Fe",
                "hour": 3,
                "reason": "conflicting unavailable notice",
            }],
        },
        capacity_solve=True,
    )
    assert isinstance(same_species_unavailable, EvaporationFluxRefusal)
    assert same_species_unavailable.reason == (
        "vapour_batch_no_debiting_pressure_outcome"
    )


def test_unresolved_detail_stays_in_notice_while_terminal_reason_is_stable() -> None:
    detail = "missing_transport_parameters"
    overlay = {
        "selection_source": detail,
        "batch_channel_states": {"Fe": "eligible"},
        "notices": [{
            "kind": "vapour_batch_unresolved",
            "species": "Fe",
            "hour": 4,
            "reason": detail,
        }],
    }
    selector_overlay = {}

    refusal = EvaporationMixin._vapour_batch_resolution_refusal(
        SimpleNamespace(),
        {},
        overlay,
        selector_overlay=selector_overlay,
        flux_pressures={},
        include_unresolved_notices=True,
        capacity_solve=True,
    )

    assert isinstance(refusal, EvaporationFluxRefusal)
    assert refusal.reason == "vapour_batch_no_debiting_pressure_outcome"
    assert refusal.diagnostic["vapour_batch_flux_overlay"]["notices"][0][
        "reason"
    ] == detail


def test_capacity_picard_restarts_when_production_cap_proof_expires(monkeypatch):
    sim = _real_capacity_sim()
    sim.vapor_pressures = {
        "metals": {"Fe": sim.vapor_pressures["metals"]["Fe"]},
        "oxide_vapors": {},
    }
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    fe_mass = resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Fe": fe_mass},
        source="production cap-expiry capacity fixture",
        material_origin="feedstock",
    )
    sim._melt_headspace_composition_mbar = {"Fe": 0.001}
    sim._melt_redox_source_capacity_mol_per_ln_fO2 = (
        lambda **_kwargs: 0.0
    )
    capacity = FiniteCapacity(0.05)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    monkeypatch.setattr(
        sim,
        "_headspace_bleed_conductance_kg_s",
        lambda **_kwargs: 1.0e6,
    )
    monkeypatch.setattr(sim, "_headspace_downstream_pressure_bar", lambda: 0.0)

    equilibrium = sim._get_equilibrium()
    initial_partials = capacity_coupling._headspace_partial_pressures_Pa(
        sim._overhead_holdup_mol(),
        species=("Fe",),
        temperature_K=sim._headspace_temperature_K(),
        volume_m3=sim._headspace_volume_m3(),
    )
    assert initial_partials["Fe"] > 0.0

    resolver_overlays = []
    resolver_pressures = []
    real_resolve = sim._resolve_evaporation_batch_flux_state

    def capture_real_resolve(*args, **kwargs):
        resolved = real_resolve(*args, **kwargs)
        resolver_pressures.append(dict(resolved[0]))
        resolver_overlays.append(copy.deepcopy(resolved[1]))
        return resolved

    monkeypatch.setattr(
        sim, "_resolve_evaporation_batch_flux_state", capture_real_resolve
    )
    solver_calls = 0
    real_solver = capacity_coupling.solve_capacity_shadow

    def count_real_solver_calls(**kwargs):
        nonlocal solver_calls
        solver_calls += 1
        return real_solver(**kwargs)

    monkeypatch.setattr(
        capacity_coupling, "solve_capacity_shadow", count_real_solver_calls
    )

    with pytest.raises(EvaporationFluxRefusal) as refusal:
        PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim,
            equilibrium,
        )

    assert refusal.value.reason == "vapour_batch_no_debiting_pressure_outcome"
    assert refusal.value.diagnostic["reason"] == (
        "vapour_batch_no_debiting_pressure_outcome"
    )
    assert not any(
        notice.get("kind") == "capacity_solve_excludes_unavailable_species"
        for notice in refusal.value.diagnostic[
            "vapour_batch_flux_overlay"
        ]["notices"]
    )
    assert any(
        notice.get("kind") == "transport_parameters_estimated"
        and notice.get("species") == "Fe"
        and notice.get("flag") == "lj_estimated:legacy_code_proxy"
        for notice in refusal.value.diagnostic[
            "vapour_batch_flux_overlay"
        ]["notices"]
    )
    assert solver_calls == 1
    assert len(resolver_overlays) == 2
    assert resolver_overlays[0]["batch_channel_states"]["Fe"] == (
        "zero_by_physics"
    )
    assert "Fe" not in resolver_pressures[1]
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        and notice.get("reason")
        == "langmuir_surface_oxygen_balance_not_closed"
        for notice in resolver_overlays[1]["notices"]
    )
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        and notice.get("reason")
        == "langmuir_surface_oxygen_balance_not_closed"
        for notice in refusal.value.diagnostic[
            "vapour_batch_flux_overlay"
        ]["notices"]
    )
    availability = sim._vapour_batch_hour_availability
    assert availability["late_unavailability_restart_used"] is True
    assert availability["restarted_unavailable_species"] == ("Fe",)
    assert availability["unavailable_by_species"]["Fe"] == (
        "langmuir_surface_oxygen_balance_not_closed"
    )


def test_capacity_picard_reclose_keeps_peer_when_surface_refuses_fe(monkeypatch):
    from simulator.langmuir_surface_oxygen import (
        LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
    )
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    overhead_mass = {}
    for species in ("Fe", "Na"):
        formula = resolve_species_formula(species, sim.species_formula_registry)
        overhead_mass[species] = formula.molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        overhead_mass,
        source="capacity peer-channel fixture",
        material_origin="feedstock",
    )
    capacity = FiniteCapacity(0.05)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    monkeypatch.setattr(
        sim,
        "_headspace_bleed_conductance_kg_s",
        lambda **_kwargs: 0.0,
    )
    overlay = {
        "batch_channel_states": {"Fe": "eligible", "Na": "eligible"},
        "selected_runtime_pa_by_species": {"Fe": 1.0, "Na": 2.0},
        "notices": [],
    }
    pressure_diagnostic = {
        "vapor_pressure_numerator_provenance": {
            "Fe": {
                "pressure_rail": "condensed_raoult_psat",
                "activity_factor": 1.0,
                "raw_metal_activity_root": 2.0,
                "P_reference_Antoine_Pa": 1.0,
            },
        },
    }
    sim._vapour_batch_hour_availability = {
        "batch_identity": sim._vapour_batch_identity,
        "hour": int(sim.melt.hour),
        "member_species": ("Fe", "Na"),
        "unavailable_by_species": {},
        "restarted_unavailable_species": (),
    }

    def resolve_capacity_flux_state(*_args, **_kwargs):
        current_overlay = copy.deepcopy(overlay)
        unavailable = sim._vapour_batch_hour_availability[
            "unavailable_by_species"
        ]
        pressures = {"Fe": 1.0, "Na": 2.0}
        if "Fe" in unavailable:
            pressures.pop("Fe")
            current_overlay["batch_channel_states"]["Fe"] = "unavailable"
            current_overlay["selected_runtime_pa_by_species"].pop("Fe")
            current_overlay["notices"].append({
                "kind": "vapour_batch_unresolved",
                "species": "Fe",
                "hour": int(sim.melt.hour),
                "reason": unavailable["Fe"],
            })
        sim._last_vapour_batch_selector_overlay = copy.deepcopy(
            current_overlay
        )
        return (
            pressures,
            current_overlay,
            None,
            {},
            pressure_diagnostic,
            None,
        )

    monkeypatch.setattr(
        sim,
        "_resolve_evaporation_batch_flux_state",
        resolve_capacity_flux_state,
    )

    def reclose(_partials):
        return SimpleNamespace(
            applied=True,
            surface_pO2_bar=0.1,
            pressures_Pa={"Na": 2.0},
            refused_species={"Fe": "surface closure could not resolve Fe"},
            refusal_codes_by_species={
                "Fe": LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
            },
            as_diagnostic=lambda: {
                "applied": True,
                "surface_pO2_bar": 0.1,
                "refused_species": {"Fe": "surface closure could not resolve Fe"},
                "refusal_codes_by_species": {
                    "Fe": LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
                },
                "warning": "surface closure could not resolve Fe",
            },
        )

    monkeypatch.setattr(sim, "_reclose_langmuir_pressures", reclose)
    captured = []
    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider,
        "dispatch",
        lambda _self, request: captured.append(
            request.control_inputs
        ) or SimpleNamespace(
            status="ok", diagnostic={"evaporation_flux_kg_hr": {"Na": 1.0e-9}}
        ),
    )
    result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
        sim,
        SimpleNamespace(
            liquid_fraction=1.0,
            vapor_pressures_Pa={"Fe": 1.0, "Na": 2.0},
        ),
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.capacity is capacity
    assert result.evaporation_flux_kg_hr["Na"] > 0.0
    assert captured
    assert captured[0]["vapour_batch_flux_pressures_Pa"] == {"Na": 2.0}
    control_notices = captured[0]["vapour_batch_flux_overlay"]["notices"]
    assert any(
        notice.get("kind") == "vapour_batch_unresolved"
        and notice.get("species") == "Fe"
        for notice in control_notices
    )
    assert any(
        notice.get("kind") == "capacity_solve_excludes_unavailable_species"
        for notice in control_notices
    )
    assert "Fe" in sim._vapour_batch_hour_availability[
        "unavailable_by_species"
    ]


def test_capacity_picard_excludes_fe_after_initial_closure_failure(
    monkeypatch,
):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.langmuir_surface_oxygen import (
        LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
    )

    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    registry = sim.species_formula_registry
    molar_mass = {
        species: resolve_species_formula(
            species, registry
        ).molar_mass_kg_per_mol()
        for species in ("Fe", "Na")
    }
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Na": 0.01 * molar_mass["Na"]},
        source="Fe recovery Picard fixture",
        material_origin="feedstock",
    )
    capacity = FiniteCapacity(0.05)
    cold_train = SimpleNamespace(
        accumulator_enabled=False,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-30,
            "p_open_Pa": 1.0e9,
            "vessel_rating_Pa": 1.0e12,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity, cold_train),
    )
    monkeypatch.setattr(
        sim,
        "_headspace_bleed_conductance_kg_s",
        lambda **_kwargs: 1.0e-30,
    )
    monkeypatch.setattr(sim, "_headspace_downstream_pressure_bar", lambda: 0.0)
    selector_overlay = {
        "batch_channel_states": {"Fe": "eligible", "Na": "eligible"},
        "selected_runtime_pa_by_species": {"Fe": 1.0, "Na": 2.0},
        "notices": [],
    }
    EvaporationMixin._classify_hour_vapour_availability(
        sim,
        SimpleNamespace(temperature_C=1600.0),
        {"Fe": 1.0, "Na": 2.0},
        selector_overlay,
        {},
    )
    assert set(sim._vapour_batch_hour_availability["member_species"]) == {
        "Fe",
        "Na",
    }
    assert set(
        sim._vapour_batch_hour_availability["admitted_member_species"]
    ) == {"Fe", "Na"}
    frozen_transport_eligibility = dict(
        sim._vapour_batch_hour_availability[
            "transport_eligibility_by_species"
        ]
    )
    def resolve_capacity_flux_state(*_args, **_kwargs):
        sim._last_vapour_batch_selector_overlay = copy.deepcopy(
            selector_overlay
        )
        return (
            {"Fe": 0.0, "Na": 2.0},
            copy.deepcopy(selector_overlay),
            None,
            {},
            {},
            None,
        )

    monkeypatch.setattr(
        sim,
        "_resolve_evaporation_batch_flux_state",
        resolve_capacity_flux_state,
    )
    reclose_calls = 0

    def reclose(partials):
        nonlocal reclose_calls
        reclose_calls += 1
        refused = {"Fe": "surface closure detail"} if reclose_calls == 1 else {}
        pressures = {"Na": 2.0}
        if not refused:
            pressures["Fe"] = 1.0
        diagnostic = {
            "applied": False,
            "surface_pO2_bar": None,
            "refused_species": refused,
            "refusal_codes_by_species": (
                {"Fe": LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED}
                if refused
                else {}
            ),
            "overhead_partials_Pa": dict(partials),
            "warning": "surface closure detail" if refused else None,
        }
        return SimpleNamespace(
            applied=False,
            surface_pO2_bar=None,
            pressures_Pa=pressures,
            refused_species=refused,
            refusal_codes_by_species=diagnostic["refusal_codes_by_species"],
            as_diagnostic=lambda: diagnostic,
        )

    monkeypatch.setattr(sim, "_reclose_langmuir_pressures", reclose)
    monkeypatch.setattr(
        sim,
        "_analytic_evaporation_depletion_rates",
        lambda rates, **_kwargs: dict(rates),
    )
    volume_m3 = sim._headspace_volume_m3()
    temperature_K = sim._headspace_temperature_K()
    current_partials = capacity_coupling._headspace_partial_pressures_Pa(
        sim._overhead_holdup_mol(),
        species=("Fe", "Na"),
        temperature_K=temperature_K,
        volume_m3=volume_m3,
    )
    pressure_targets = {"Na": current_partials["Na"] + 0.1}
    source_response_gain = 0.1
    dispatch_pressure_maps = []

    def dispatch(_provider, request):
        controls = request.control_inputs
        pressure_map = dict(controls["vapour_batch_flux_pressures_Pa"])
        dispatch_pressure_maps.append(pressure_map)
        partials = controls["overhead_partials_Pa"]
        rates = {}
        for species in pressure_map:
            target = pressure_targets[species]
            source_mol_hr = (
                source_response_gain
                * max(0.0, target - float(partials.get(species, 0.0)))
                * volume_m3
                / (GAS_CONSTANT * temperature_K)
            )
            rates[species] = source_mol_hr * molar_mass[species]
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": rates},
        )

    monkeypatch.setattr(BuiltinEvaporationFluxProvider, "dispatch", dispatch)
    monkeypatch.setattr(
        sim,
        "_project_evaporation_overhead_source_mol_hr",
        lambda rates, _stoich: {
            species: rate / molar_mass[species]
            for species, rate in rates.items()
        },
    )
    result = PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
        sim,
        SimpleNamespace(
            liquid_fraction=1.0,
            vapor_pressures_Pa={"Fe": 1.0, "Na": 2.0},
            vapor_pressures_source={},
            activity_coefficients={},
            diagnostics={},
        ),
    )

    assert isinstance(
        result, CapacityShadowResult
    ), "the tuned Na target must converge before accounting is checked"
    assert result.iterations < 20
    assert result.iterations < 50
    assert reclose_calls >= 2
    assert dispatch_pressure_maps
    assert all("Fe" not in pressure_map for pressure_map in dispatch_pressure_maps)
    assert result.evaporation_flux_kg_hr["Na"] > 0.0
    assert "Fe" not in result.evaporation_flux_kg_hr
    assert "Fe" not in result.partial_pressures_Pa
    assert "Fe" not in result.bled_species_mol
    assert result.mass_closure_error_pct == pytest.approx(0.0, abs=1.0e-10)
    assert sim._vapour_batch_hour_availability[
        "unavailable_by_species"
    ]["Fe"] == "surface closure detail"
    bound = next(
        notice
        for notice in sim._last_vapour_batch_flux_overlay["notices"]
        if notice.get("kind") == "capacity_solve_excludes_unavailable_species"
    )
    assert bound["bound_direction"] == {
        "resolved_fluxes": "upper",
        "headspace_pressure": "lower",
    }
    assert sim._vapour_batch_hour_availability[
        "transport_eligibility_by_species"
    ] == frozen_transport_eligibility
    assert frozen_transport_eligibility["Fe"] != "zero_by_physics"


def test_real_finite_capacity_result_drives_live_short_session_once(monkeypatch):
    active = _real_capacity_sim()
    active.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = active._cold_train_capacity_policy()
    monkeypatch.setattr(
        active,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    active.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="binding capacity integration fixture",
        material_origin="feedstock",
    )

    capacity = capacity_from_hardware(
        thermal_train_parameters_from_mapping().cold_train
    )
    assert isinstance(capacity, FiniteCapacity)

    shadow_results = []
    real_shadow = active._compute_capacity_coupling_shadow

    def observed_shadow(equilibrium):
        result = real_shadow(equilibrium)
        shadow_results.append(result)
        return result

    active._compute_capacity_coupling_shadow = observed_shadow
    active.step()

    assert len(shadow_results) == 1
    assert isinstance(shadow_results[0], CapacityShadowResult)
    assert sum(
        transition.reason == "overhead_bleed"
        for transition in active.atom_ledger.transitions
    ) == 1
    assert shadow_results[0].mass_closure_error_pct <= 5.0e-12


def test_all_zero_capacity_flux_preserves_and_routes_live_carrier_authority(
    monkeypatch,
):
    active = _real_capacity_sim()
    active.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = active._cold_train_capacity_policy()
    monkeypatch.setattr(
        active,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    active.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="capacity carrier authority fixture",
        material_origin="feedstock",
    )

    rate_kg_hr = 0.0
    carrier_record = {
        "species_id": "Fe",
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {"kind": "eligible", "alpha_ref": "test:alpha"},
        "is_refused": False,
        "is_union_flux_eligible": True,
        "is_flux_active": True,
        "validation_status": "validated",
        "verdict_status": "authoritative",
        "certification_ceiling": "validated_point",
    }
    refused_na_record = {
        "species_id": "Na",
        "pressure": {"kind": "refusal", "code": "test_refusal"},
        "flux": {"kind": "refusal", "code": "test_refusal"},
        "is_refused": True,
        "is_union_flux_eligible": False,
        "is_flux_active": False,
        "validation_status": "modeled-PENDING",
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
    }
    live_flux = EvaporationFlux(
        species_kg_hr={},
        carrier_authority_by_species={
            "Fe": carrier_record,
            "Na": refused_na_record,
        },
    )
    live_flux.update_totals()

    real_shadow = active._compute_capacity_coupling_shadow

    def solved_shadow(equilibrium):
        result = real_shadow(equilibrium)
        assert isinstance(result, CapacityShadowResult)
        return replace(
            result,
            evaporation_flux_kg_hr={},
        )

    monkeypatch.setattr(active, "_compute_capacity_coupling_shadow", solved_shadow)
    monkeypatch.setattr(
        active,
        "_calculate_evaporation",
        lambda equilibrium, **kwargs: live_flux,
    )
    monkeypatch.setattr(
        active,
        "_apply_analytic_evaporation_depletion",
        lambda flux: flux,
    )

    routed_fluxes = []
    real_route = active.condensation_model.route

    def observed_route(evap_flux, melt):
        routed_fluxes.append(evap_flux)
        return real_route(evap_flux, melt)

    monkeypatch.setattr(active.condensation_model, "route", observed_route)
    active.step()

    assert routed_fluxes
    routed = routed_fluxes[0]
    assert routed.species_kg_hr == {}
    assert routed.carrier_authority_by_species == {
        "Fe": carrier_record,
        "Na": refused_na_record,
    }
    assert active.condensation_model.last_sticking_alpha_provenance_notice[
        "vapour_carrier_authority_by_species"
    ] == {
        "Fe": carrier_record,
        "Na": refused_na_record,
    }
    assert active.condensation_model.last_condensation_authority_by_species[
        "Fe"
    ]["carrier_authority"] == carrier_record
    assert active.condensation_model.last_condensation_authority_by_species[
        "Na"
    ]["carrier_authority"] == refused_na_record
    assert (
        vapour_carrier_authority_status(
            routed.carrier_authority_by_species["Fe"],
            expected_species_id="Fe",
        )
        == VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE
    )


def test_default_runtime_policy_is_no_cold_train():
    sim = _real_capacity_sim()

    capacity, cold_train = sim._cold_train_capacity_policy()

    assert isinstance(capacity, NoColdTrain)
    assert capacity.reason == "runtime_enforcement_disabled"
    assert cold_train.runtime_enforcement is False


def test_missing_gamma_ideal_assertion_reaches_activity_seam(monkeypatch):
    coefficients = dict(melt_activity.MELT_OXIDE_ACTIVITY_COEFFICIENTS)
    coefficients.pop("K2O")
    monkeypatch.setattr(
        melt_activity,
        "MELT_OXIDE_ACTIVITY_COEFFICIENTS",
        coefficients,
    )

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    # 1600 K is inside the K pressure-model domain but outside its 1500 K
    # activity-anchor domain. This isolates t-523: activity uncertainty must
    # remain numeric and flux-driving.
    sim.melt.temperature_C = 1600.0 - 273.15
    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures, equilibrium
    )

    provenance = sim._last_vapor_pressure_diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["K"]
    assert provenance["melt_oxide_activity_model"] == (
        melt_activity.MELT_OXIDE_IDEAL_SOLUTION_MODEL
    )
    assert provenance["melt_oxide_activity_evidence_tier"] == (
        melt_activity.MELT_OXIDE_IDEAL_ASSERTION_TIER
    )
    assert "declared_ideal_solution_activity_assertion" in provenance[
        "melt_oxide_activity_warning"
    ]

    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=effective_source,
    )
    assert batch is not None
    answer = batch.channel("K")
    activity = answer.source_reaction_activity
    assert activity is not None
    assert activity.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert activity.reason == "declared_ideal_solution_activity"
    assert activity.evidence_tier == (
        melt_activity.MELT_OXIDE_IDEAL_ASSERTION_TIER
    )
    assert "declared_ideal_solution_activity_assertion" in (activity.detail or "")
    assert activity.evidence_ref is None
    assert answer.is_flux_active


@pytest.mark.parametrize("temperature_K", [1800.0, 2000.0])
def test_flux_activity_operands_retain_every_pressure_carrier(temperature_K):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = temperature_K - 273.15
    equilibrium = sim._get_equilibrium()
    diagnostic = sim._last_vapor_pressure_diagnostic

    controls, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa={},
        vapour_batch_flux_pressures_Pa={},
    )

    operands = controls["vapor_pressure_activities"]
    assert set(diagnostic["vapor_pressures_Pa"]) <= set(operands)
    assert operands == diagnostic["activities"]
    assert operands != equilibrium.activity_coefficients


def test_catalog_probe_is_activity_corrected_while_flux_source_stays_pre_rg():
    """RG-1 gateway: catalog P_eff class without a flux-value cutover.

    Each species' band is derived from its verifier-probed effective-vs-pure
    separation so every pure-component reversion is independently red.
    """

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures, equilibrium
    )
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=effective_source,
    )
    assert batch is not None

    verifier_probe_pa = {
        # MC-5 Ca (2026-08-07): catalog Pref retargeted TE Pref_GR -> JANAF
        # Pref_GF (live move +2.465 dex at this probe's 1873.15 K). The PIN
        # moves +3.096 dex (4.6e-9 -> 5.740e-6 Pa) because the assertion is
        # band-based (within_probe_band ~1.43 dex), so the old pin sat below
        # the old live value. SIGN CHECK: pressure UP, matching more high-T
        # Ca volatilisation. See docs-private/research/2026-08-07-mc5-ca-fix/report.md.
        # b-136 / t-559 posture rework 2026-08-08: Zhang CaTiO3 α remains
        # WITHDRAWN; channel stays live on Hertz-Knudsen ideal α=1.0 as an
        # explicit status-bearing upper bound (missing kinetic α ≠ missing
        # pressure). Ca pressure probe retained. docs-private/research/2026-08-08-rp3-b136/report.md.
        "Ca": 5.740329129667688e-6,
        # b-147 Mg (2026-08-07): TE gas_rail demoted; Pref_GF path. Live catalog
        # probe at 1873.15 K moves 7.3e-3 -> 0.09398 Pa (+1.110 dex). Pref_GF-GR
        # at this T is only +0.565 dex; the extra pin motion is band-based
        # (old pin sat below old live). SIGN CHECK: pressure UP.
        # docs-private/research/2026-08-07-mgfix/report.md
        "Mg": 0.09397692153837497,
        # Constant table gamma (no T*/T / pseudo-binary mid-range).
        # t-538: seam pin unchanged (pre-rg Builtin path); catalog OOR value
        # moves via physical 1/T tangent (see K branch below).
        "K": 0.469,
        "Al": 1.4e-8,
    }
    pure_component_probe_pa = {
        "Ca": 8.35e5,
        "Mg": 6.87e5,
        "K": 4.51e6,
        "Al": 110.2,
    }
    # Premise: the legacy probes give separation R_i = P_pure,i / P_eff,i.
    # Algebra: admit one tenth of that measured log separation,
    # b_i = log10(R_i) / 10. The band therefore admits ratios
    # [R_i^-0.1, R_i^0.1] around P_eff,i, not the full raw separation.
    # Catalog a=1 answers also pass through source-reaction/fO2 evaluation;
    # the isolated re-evaluations below prove none can fit its species' band.
    probe_band_decades = {
        species_id: abs(math.log10(pure_component_probe_pa[species_id] / probe_pa))
        / 10.0
        for species_id, probe_pa in verifier_probe_pa.items()
    }

    def within_probe_band(
        species_id: str, actual: float, reference: float
    ) -> bool:
        return (
            abs(math.log10(actual / reference))
            <= probe_band_decades[species_id]
        )

    temperature_K = equilibrium.temperature_C + 273.15
    original_equilibrium_activities = dict(equilibrium.activity_coefficients)
    original_activities = dict(sim._last_vapor_pressure_diagnostic["activities"])
    original_diagnostic_activities = dict(original_activities)
    assert original_equilibrium_activities == sim._last_high_t_melt_activity["selected_parent_activities"]
    assert original_equilibrium_activities != original_activities
    baseline_catalog_pa: dict[str, float] = {}
    activity_exponents: dict[str, float] = {}
    for species_id, probe_pa in verifier_probe_pa.items():
        answer = batch.channel(species_id)
        assert isinstance(answer.pressure, PressureValue)
        catalog_pa = float(answer.pressure.pa)
        baseline_catalog_pa[species_id] = catalog_pa
        seam_pa = effective_source.pressure_pa(species_id)
        assert seam_pa is not None and seam_pa > 0.0
        assert within_probe_band(species_id, float(seam_pa), probe_pa)
        if species_id == "K":
            # K fit domain ends at 1600 K; this probe is at 1873.15 K → OOR.
            # t-538: OOR value is the physical 1/T-tangent continuation of the
            # Antoine reference (C≠0), not attenuated linear-T slope invention.
            # SIGN CHECK: unit-a log10 Pref at 1873.15 K drops ~0.81 dex vs the
            # prior ×1.5 heating amplification (5.766 → 4.960). Activity-scaled
            # catalog pressure therefore moves DOWN toward the pre-rg seam
            # (~0.71 vs ~0.69 Pa) — they can sit in the same probe band now.
            # Separation catalog≠seam is no longer required; OOR status-bearing
            # mark + a=1 reversion scale (below) still prove the catalog path.
            assert answer.extra.get("out_of_range") is True
            assert answer.extra["status"] == OUT_OF_RANGE_STATUS
            assert answer.extra.get("acquisition_flag")
            assert answer.verdict_status == "status_bearing_non_authoritative"
            assert answer.certification_ceiling == "never"
            assert math.isfinite(catalog_pa) and 0.0 < catalog_pa
            assert catalog_pa < pure_component_probe_pa[species_id]
            # Physical OOR can land near the pre-rg seam; pin the live value
            # so a future regression that re-inflates heating OOR is visible.
            assert catalog_pa == pytest.approx(0.7101916366096568, rel=1.0e-9)
        else:
            assert not answer.extra.get("out_of_range", False)
            assert within_probe_band(species_id, catalog_pa, probe_pa)
            assert within_probe_band(species_id, catalog_pa, float(seam_pa))
        activity = answer.source_reaction_activity
        assert activity is not None
        assert activity.provider == "BuiltinVaporPressureProvider"
        if species_id == "K":
            # b-121: OOD table gamma is status-bearing (not POINT) while the
            # HEAD oodfix seam still publishes PressureValue + FluxEligible.
            assert (
                activity.verdict
                is ActivityVerdictKind.STATUS_BEARING_VALUE
            )
            assert activity.reason == "out_of_gamma_domain"
            assert answer.is_flux_active
        else:
            assert activity.verdict is ActivityVerdictKind.POINT
            assert activity.value == pytest.approx(
                original_activities[species_id], rel=0.0, abs=0.0
            )
        assert activity.evidence_ref is not None
        # Structured external-evidence provenance (INCOMING activity landing).
        assert "DOI " in activity.evidence_ref
        assert "_last_vapor_pressure_diagnostic" not in activity.evidence_ref
        evaluator = sim.vapour_rail_catalog.species[species_id].evaluator
        assert evaluator is not None
        activity_exponents[species_id] = evaluator.activity_exponent
    try:
        for species_id in verifier_probe_pa:
            # Mutate one species at a time from the same baseline. This proves
            # every activity=1 reversion independently leaves its own band.
            mutated_activities = dict(original_activities)
            mutated_activities[species_id] = 1.0
            equilibrium.activity_coefficients = dict(mutated_activities)
            sim._last_vapor_pressure_diagnostic["activities"] = dict(
                mutated_activities
            )
            a1_reversion_batch = sim._resolve_evaporation_vapour_batch(
                equilibrium,
                temperature_K=temperature_K,
                effective_pressure_source=effective_source,
            )
            assert a1_reversion_batch is not None
            a1_answer = a1_reversion_batch.channel(species_id)
            assert isinstance(a1_answer.pressure, PressureValue)
            assert a1_answer.source_reaction_activity is not None
            assert a1_answer.source_reaction_activity.value == pytest.approx(1.0)
            if batch.channel(species_id).extra.get("out_of_range", False):
                exponent = activity_exponents[species_id]
                assert exponent > 0.0
                expected_scale = (1.0 / original_activities[species_id]) ** exponent
                assert (
                    float(a1_answer.pressure.pa) / baseline_catalog_pa[species_id]
                ) == pytest.approx(expected_scale, rel=1.0e-12, abs=0.0)
            seam_pa = effective_source.pressure_pa(species_id)
            assert seam_pa is not None and seam_pa > 0.0
            assert not within_probe_band(
                species_id,
                float(a1_answer.pressure.pa),
                float(seam_pa),
            )
    finally:
        equilibrium.activity_coefficients = original_equilibrium_activities
        sim._last_vapor_pressure_diagnostic["activities"] = (
            original_diagnostic_activities
        )

    # Source-reaction oxygen fugacity is intrinsic melt fO2, not transport
    # headspace pO2. Varying only transport pO2 must leave catalog P_eff fixed.
    original_catalog_pa = {
        species_id: float(batch.channel(species_id).pressure.pa)
        for species_id in verifier_probe_pa
    }
    sim._last_vapor_pressure_diagnostic["pO2_bar"] = 1.0e-3
    transport_mutation_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective_source,
    )
    assert transport_mutation_batch is not None
    for species_id, expected_pa in original_catalog_pa.items():
        assert transport_mutation_batch.channel(species_id).pressure.pa == (
            pytest.approx(expected_pa, rel=0.0, abs=0.0)
        )

    # The exact intrinsic fO2 used by the pressure solve is carried on that
    # solve's diagnostic. A later/mismatched EquilibriumResult.fO2_log must not
    # retune catalog P_eff while activities and the effective seam stay fixed.
    assert sim._last_vapor_pressure_diagnostic["source_reaction_fO2_bar"] > 0.0
    equilibrium_fO2_log = equilibrium.fO2_log
    equilibrium.fO2_log = float(equilibrium_fO2_log) + 6.0
    result_mutation_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective_source,
    )
    equilibrium.fO2_log = equilibrium_fO2_log
    assert result_mutation_batch is not None
    for species_id, expected_pa in original_catalog_pa.items():
        assert result_mutation_batch.channel(species_id).pressure.pa == (
            pytest.approx(expected_pa, rel=0.0, abs=0.0)
        )

    # Golden-neutral cutover guard: batch channels own eligibility, but the
    # numeric operand still comes from the typed equilibrium-backend seam.
    flux_pressures, flux_report = flux_pressures_from_batch(
        batch,
        effective_pressure_source=effective_source,
    )
    assert flux_report["selection_source"] == (
        "equilibrium_backend_effective_pressure_pre_rg"
    )
    for species_id in verifier_probe_pa:
        assert flux_pressures[species_id] == pytest.approx(
            effective_source.pressure_pa(species_id), rel=0.0, abs=0.0
        )
        assert flux_report["selected_pressure_source_by_species"][species_id] == (
            effective_source.source_id
        )
    # b-145: TiO/TiO2_gas status-bearing OOR flux-eligible. b-136 posture
    # rework keeps them live via Hertz-Knudsen ideal α=1.0 upper bound after
    # Zhang cascade proxy withdrawal (refuse would zero Ti flux and negate
    # the observable effect of the b-145 pressure fix).
    for species_id in ("TiO", "TiO2_gas"):
        answer = batch.channel(species_id)
        assert isinstance(answer.pressure, PressureValue)
        assert isinstance(answer.flux, FluxEligible)
        assert answer.extra.get("out_of_range") is True
        assert answer.extra["status"] == OUT_OF_RANGE_STATUS
        assert answer.extra.get("acquisition_flag")
        assert answer.verdict_status == "status_bearing_non_authoritative"
        assert answer.certification_ceiling == "never"
        seam_pa = effective_source.pressure_pa(species_id)
        assert seam_pa is not None and math.isfinite(seam_pa) and seam_pa > 0.0
        assert flux_pressures[species_id] == pytest.approx(
            seam_pa, rel=0.0, abs=0.0
        )
        assert species_id in flux_report["extrapolated_flux_species"]
        assert flux_report["selected_pressure_source_by_species"][species_id] == (
            effective_source.source_id
        )
    assert batch.channel("K").extra.get("out_of_range") is True
    assert flux_pressures["K"] != pytest.approx(
        batch.channel("K").pressure.pa, rel=1.0e-6
    )
    assert "K" in flux_report["extrapolated_flux_species"]

    assert effective_source.source_id == (
        "equilibrium_backend_effective_pressure_pre_rg"
    )


def test_catalog_intrinsic_fo2_refuses_without_exact_solve_diagnostic():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = sim._get_equilibrium()
    temperature_K = equilibrium.temperature_C + 273.15
    diagnostic = dict(sim._last_vapor_pressure_diagnostic)
    assert diagnostic["pO2_bar"] > 0.0
    assert math.isfinite(float(equilibrium.fO2_log))

    # Keep valid transport and EquilibriumResult redox values available. The
    # catalog must still refuse when the exact intrinsic fO2 carried by the
    # pressure solve is absent or malformed; neither is an admissible fallback.
    for mutation in ("missing", "malformed"):
        mutated_diagnostic = dict(diagnostic)
        if mutation == "missing":
            mutated_diagnostic.pop("source_reaction_fO2_bar")
        else:
            mutated_diagnostic["source_reaction_fO2_bar"] = "bogus"
        sim._last_vapor_pressure_diagnostic = mutated_diagnostic
        batch = sim._resolve_evaporation_vapour_batch(
            equilibrium,
            temperature_K=temperature_K,
            effective_pressure_source=EffectivePressureSource(
                "test_no_flux_activation",
                {},
            ),
        )
        assert batch is not None
        for species_id in ("Ca", "Mg", "K", "Al"):
            answer = batch.channel(species_id)
            assert isinstance(answer.pressure, PressureRefusal)
            assert "intrinsic_melt oxygen fugacity" in (
                answer.extra.get("detail") or ""
            )


def test_default_off_preserves_hot_fe_redox_split_head_result(monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    monkeypatch.setattr(
        sim,
        "_compute_capacity_coupling_shadow",
        lambda _equilibrium: (_ for _ in ()).throw(
            AssertionError("default-off live path must not compute capacity")
        ),
    )

    snapshot = sim.step()
    # d-252's 1 mbar Ar cover and 200 C/h ramp move this first step from
    # 1600 C to 1400 C. The old hard-vacuum step was 1.39e6% over transport
    # capacity; the new Ar-film diffusion-limited value is 2.47%.
    # Physics seq 1003 classifies Ti below the 1e-12 kg/hr negligibility
    # threshold here, so it is absent from snapshot flux/status and is a
    # legitimate no-flux carrier for this pin.
    ceiling_flux_species = {"Ca", "CaO_gas", "TiO", "TiO2_gas"}
    from tests.studio_pin_support import capacity_pin_values

    pins = capacity_pin_values(snapshot)
    # 5a7121241, brought in by bef9af1e7 (last old first-parent: 4b6555249),
    # executes flagged broad-proxy alphas instead of the unmeasured fallback
    # of 1. At this 1400 C step CrO and AlO equilibrium pressure, activity,
    # and pO2 are unchanged. The executed alphas are the declared proxies:
    # CrO 0.9 and AlO 0.3. Series flux is J = dP / (r_interface + r_gas),
    # with r_interface = 1/(alpha * k_HK) and r_gas independent of alpha
    # (engines/builtin/evaporation_flux.py), so the rates do not scale by
    # those alpha ratios. Saturation and melt mass move with the two rates.
    assert pins["non_phosphorus_kg_hr"] == pytest.approx(
        0.06423669542196676, rel=1e-12, abs=1e-12
    )
    assert ceiling_flux_species <= set(
        snapshot.evap_flux.alpha_authority_status_by_species
    )
    assert {
        snapshot.evap_flux.alpha_authority_status_by_species[species]
        for species in ceiling_flux_species
    } == {"analytical_upper_bound"}
    assert sim._alpha_authority_status_by_species_engaged == (
        snapshot.evap_flux.alpha_authority_status_by_species
    )

    assert sim._equipment is not None
    assert snapshot.overhead.initial_throat_area_m2 == pytest.approx(
        sim._equipment.pipe.initial_throat_area_m2
    )

    assert tuple(
        pins[key]
        for key in (
            "hour",
            "temperature_C",
            "total_kg_hr",
            "transport_saturation_pct",
            "melt_mass_kg",
        )
    ) == pytest.approx(
        # Same proxy-alpha step as the non-phosphorus pin above. Hour and
        # temperature stay on the d-252 1 mbar Ar, 200 C/h ramp.
        (
            1,
            1400.0,
            0.06423669542196676,
            2.4701307118784706,
            998.7072055627636,
        ),
        rel=1.0e-12,
        abs=1.0e-12,
    )
    # The split adds collector and trap captures after their evaporations.
    # b1113 also condenses Ca, Cr, and Mn on cold stages. The freeze-out
    # cartridge then swings each trap deposit: solids leave the alkali
    # product, and liquid Na and K return to the train. A Ca residual
    # still reaches terminal offgas; the assert below keeps that.
    transition_reasons = tuple(
        transition.reason for transition in sim.atom_ledger.transitions
    )
    expected_transition_order = (
        "evaporate_SiO",
        "condense_SiO",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Mg",
        "condense_Mg",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Fe",
        "condense_Fe",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Na",
        "condense_Na",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out returns liquid alkali to the train",
        "evaporate_K",
        "condense_K",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out returns liquid alkali to the train",
        "evaporate_AlO",
        "evaporate_Ca",
        "condense_Ca",
        "evaporate_CaO_gas",
        "evaporate_Cr",
        "condense_Cr",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_CrO",
        "evaporate_K2",
        "evaporate_K2O_gas",
        "evaporate_MgO_gas",
        "evaporate_Mn",
        "condense_Mn",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Na2",
        "evaporate_Na2O_gas",
        "evaporate_SiO2_gas",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
        "fe_redox_respeciation",
        "overhead_bleed",
    )
    # d-402 adds accounted wall holdup for reactive vapours. The established
    # evaporation/condensation order remains intact around those transitions.
    actual_order = iter(transition_reasons)
    assert all(
        any(actual == expected for actual in actual_order)
        for expected in expected_transition_order
    )
    assert "retain_condensation_holdup_AlO" in transition_reasons
    assert sim.atom_ledger.project_account_kg("terminal.offgas").get(
        "Ca", 0.0
    ) > 0.0
    # d-237 retains phosphorus in C0, omitting these carriers from the ledger.
    assert {"evaporate_P2", "evaporate_PO", "evaporate_PO2"}.isdisjoint(
        transition.reason for transition in sim.atom_ledger.transitions
    )
    assert "Ti" not in snapshot.evap_flux.species_kg_hr
    assert "Ti" not in snapshot.evap_flux.alpha_authority_status_by_species
    assert snapshot.mass_balance_error_pct <= 5.0e-12


def test_default_off_c0_pipe_cap_preserves_hot_fe_redox_split_head_result(monkeypatch):
    sim = _real_capacity_sim()
    sim.campaign_mgr.campaigns["C0"]["p_total_mbar_default"] = 0.0
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    monkeypatch.setattr(
        sim,
        "_compute_capacity_coupling_shadow",
        lambda _equilibrium: (_ for _ in ()).throw(
            AssertionError("default-off live path must not compute the capacity shadow")
        ),
    )

    phosphorus_carriers = _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS
    p2o5_source_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")["P2O5"]
    p2o5_projection_kg = sim.melt.composition_kg["P2O5"]
    source_before_kg = float(
        sim.atom_ledger.total_kg_by_account("process.cleaned_melt")
    )
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    # Default-off skips the optional shadow. This step still hits the
    # mandatory C0 pipe cap: one estimate is over capacity, and the
    # admitted release matches that estimate within the flux floor.
    binding = [
        call
        for call in transport_calls
        if call["demand_kg_hr"] > call["capacity_kg_hr"]
    ]
    assert len(binding) == 1
    demand_kg_hr = binding[0]["demand_kg_hr"]
    capacity_kg_hr = binding[0]["capacity_kg_hr"]
    demand_species = binding[0]["species_kg_hr"]
    cap_record = sim._last_subsolidus_bakeout["transport_cap"]
    assert cap_record["demand_kg_hr"] == pytest.approx(
        demand_kg_hr, rel=0.0, abs=0.0
    )
    assert cap_record["capacity_kg_hr"] == pytest.approx(
        capacity_kg_hr, rel=0.0, abs=0.0
    )
    assert cap_record["scale"] == pytest.approx(
        capacity_kg_hr / demand_kg_hr, rel=0.0, abs=0.0
    )
    released_kg_hr = snapshot.evap_flux.total_kg_hr
    released_species = {
        str(species): float(rate)
        for species, rate in snapshot.evap_flux.species_kg_hr.items()
        if float(rate) > 0.0
    }
    assert abs(released_kg_hr - capacity_kg_hr) <= _NONTRIVIAL_FLUX_KG_HR
    assert demand_kg_hr - capacity_kg_hr > _NONTRIVIAL_FLUX_KG_HR

    def _committed_vapor_kg(species: str) -> float:
        credited = 0.0
        for transition in sim.atom_ledger.transitions:
            if transition.reason != f"evaporate_{species}":
                continue
            for lot in transition.credits:
                credited += float(lot.species_kg.get(species, 0.0))
        return credited

    retained_total_kg = 0.0
    for species in set(demand_species) | set(released_species):
        demand_i = demand_species.get(species, 0.0)
        released_i = released_species.get(species, 0.0)
        committed_i = _committed_vapor_kg(species)
        retained_i = demand_i - committed_i
        assert abs(committed_i - released_i) <= _NONTRIVIAL_FLUX_KG_HR
        assert abs(retained_i - (demand_i - released_i)) <= _NONTRIVIAL_FLUX_KG_HR
        retained_total_kg += retained_i
    assert abs(
        retained_total_kg - (demand_kg_hr - released_kg_hr)
    ) <= _NONTRIVIAL_FLUX_KG_HR
    # Overhead bleed credits O2 after the cap. That credit is not in the
    # admitted vapour flux, and it is the only extra cleaned-melt debit.
    bleed_o2_kg = 0.0
    for transition in sim.atom_ledger.transitions:
        if transition.reason != "overhead_bleed":
            continue
        for lot in transition.credits:
            bleed_o2_kg += float(lot.species_kg.get(OXYGEN_SPECIES, 0.0))
    source_after_kg = float(
        sim.atom_ledger.total_kg_by_account("process.cleaned_melt")
    )
    assert abs(snapshot.melt_mass_kg - source_after_kg) <= _NONTRIVIAL_FLUX_KG_HR
    assert abs(
        (source_before_kg - source_after_kg) - (released_kg_hr + bleed_o2_kg)
    ) <= _NONTRIVIAL_FLUX_KG_HR
    assert sim._subsolidus_pending_release is None
    assert snapshot.mass_balance_error_pct <= 5.0e-12
    assert snapshot.overhead.transport_saturation_pct == pytest.approx(
        100.0,
        rel=0.0,
        abs=100.0 * _NONTRIVIAL_FLUX_KG_HR / capacity_kg_hr,
    )
    assert snapshot.hour == 1

    assert set(phosphorus_carriers).isdisjoint(snapshot.evap_flux.species_kg_hr)
    reasons = {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert {
        f"evaporate_{carrier}" for carrier in phosphorus_carriers
    }.isdisjoint(reasons)
    assert {
        f"condense_{carrier}" for carrier in phosphorus_carriers
    }.isdisjoint(reasons)
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")["P2O5"] == (
        p2o5_source_kg
    )
    assert sim.melt.composition_kg["P2O5"] == p2o5_projection_kg
    for carrier in phosphorus_carriers:
        authority = sim.condensation_model.last_condensation_authority_by_species.get(
            carrier
        )
        if authority is None:
            continue
        assert authority.get("mass_disposition") != "declared_cleanup_offgas"
        evidence = authority.get("routing_authorization") or {}
        assert evidence.get("stage") != "stage0_p_carriers"
        assert authority.get("remaining_mass_kg_hr") in (None, 0.0)
    assert "evaporate_Fe" in reasons
    assert "condense_Fe" in reasons
    assert "fe_redox_respeciation" in reasons

    assert sim._equipment is not None
    assert snapshot.overhead.initial_throat_area_m2 == pytest.approx(
        sim._equipment.pipe.initial_throat_area_m2
    )

    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    params = thermal_train_parameters_from_mapping(payload)
    monkeypatch.setattr(
        "simulator.thermal_train.thermal_train_parameters_from_mapping",
        lambda: params,
    )
    enforced = _real_capacity_sim()
    enforced.start_campaign(CampaignPhase.C0)
    enforced.melt.temperature_C = 1600.0
    calls = []

    def finite_capacity_engaged(_equilibrium):
        calls.append(True)
        return CapacityShadowRefusal("finite_capacity_engaged", 0)

    monkeypatch.setattr(
        enforced,
        "_compute_capacity_coupling_shadow",
        finite_capacity_engaged,
    )
    with pytest.raises(CapacityCouplingRefusalError, match="finite_capacity_engaged"):
        enforced.step()
    assert calls == [True]


def test_default_off_c0_cover_does_not_apply_pipe_cap():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.background_gas_species == "Ar"
    sim.melt.temperature_C = 1600.0
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    assert transport_calls
    owner_demand_kg_hr = transport_calls[0]["demand_kg_hr"]
    for owner_result in transport_calls:
        assert owner_result["demand_kg_hr"] == pytest.approx(owner_demand_kg_hr)
        assert owner_result["demand_kg_hr"] < owner_result["capacity_kg_hr"]
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert snapshot.evap_flux.total_kg_hr == pytest.approx(
        owner_demand_kg_hr
    )


def test_hot_non_c0_campaign_keeps_analytical_ceiling_flux_species():
    """Ca/Ti ceiling flux stays live on a hot campaign the C0 cap does not scale."""
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C4)
    sim.melt.temperature_C = 1600.0

    snapshot = sim.step()

    ceiling_flux_species = {"Ca", "CaO_gas", "Ti", "TiO", "TiO2_gas"}
    status = snapshot.evap_flux.alpha_authority_status_by_species
    assert ceiling_flux_species <= set(status)
    assert {
        status[species] for species in ceiling_flux_species
    } == {ANALYTICAL_UPPER_BOUND_ALPHA_STATUS}
    assert sim._alpha_authority_status_by_species_engaged == status
    assert sim.melt.campaign == CampaignPhase.C4
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert snapshot.overhead.transport_saturation_pct > 100.0

    reasons = [transition.reason for transition in sim.atom_ledger.transitions]
    reason_set = set(reasons)
    for required in (
        "evaporate_Ca",
        "evaporate_CaO_gas",
        "evaporate_Ti",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
        "condense_Ca",
    ):
        assert required in reason_set, required

    # The old single-Stage-4 condensate roster does not describe the split:
    # read the production route's stage inventories for the designated alkalis
    # and preserve unassigned oxide vapors in the terminal offgas projection.
    stages = {stage.stage_key: stage for stage in sim.train.stages}
    na_collector = stages[NA_COLLECTOR_STAGE_KEY].collected_kg
    k_trap = stages[K_TRAP_STAGE_KEY].collected_kg
    assert na_collector["Na"] > 0.0
    assert na_collector["Mg"] > 0.0
    assert k_trap["K"] > 0.0
    offgas = sim.atom_ledger.project_account_kg("terminal.offgas")
    for species in (
        "Al2O",
        "AlO",
        "CaO_gas",
        "CrO",
        "MgO_gas",
        "SiO2_gas",
        "TiO",
        "TiO2_gas",
    ):
        assert offgas.get(species, 0.0) > 0.0, species


def test_unscaled_c0_commits_reversible_si_condensation():
    """Vacuum and configured throat geometry admit reversible Si condensation."""
    sim = _real_capacity_sim()
    sim.campaign_mgr.campaigns["C0"]["p_total_mbar_default"] = 0.0
    sim.campaign_mgr.campaigns["C0"]["condenser_geometry"] = {
        "initial_throat_area_m2": 10.0
    }
    sim.start_campaign(CampaignPhase.C0)
    assert sim.melt.p_total_mbar == 0.0
    assert (
        sim._overhead_condenser_geometry_config["initial_throat_area_m2"]
        == 10.0
    )
    sim.melt.temperature_C = 1600.0
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    assert transport_calls
    owner_demand_kg_hr = transport_calls[0]["demand_kg_hr"]
    assert all(
        call["demand_kg_hr"] <= call["capacity_kg_hr"]
        for call in transport_calls
    )
    assert snapshot.evap_flux.total_kg_hr == pytest.approx(
        owner_demand_kg_hr
    )
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert "condense_Si" in {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert sim.melt.campaign == CampaignPhase.C0


def test_explicit_runtime_enforcement_uses_configured_finite_capacity(monkeypatch):
    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    params = thermal_train_parameters_from_mapping(payload)
    monkeypatch.setattr(
        "simulator.thermal_train.thermal_train_parameters_from_mapping",
        lambda: params,
    )
    sim = _real_capacity_sim()

    capacity, cold_train = sim._cold_train_capacity_policy()

    assert isinstance(capacity, FiniteCapacity)
    assert capacity.value_kg_hr == pytest.approx(9.856)
    assert cold_train.runtime_enforcement is True


def test_live_overhead_bleed_routes_binding_capacity_surge_to_accumulator(
    monkeypatch,
):
    sim = _real_capacity_sim()
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    cold_train = SimpleNamespace(
        accumulator_enabled=True,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-3,
            "p_open_Pa": 800000.0,
            "vessel_rating_Pa": 1000000.0,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(o2_molar_mass), cold_train),
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="live accumulator surge fixture",
        material_origin="feedstock",
    )

    result = sim._dispatch_overhead_bleed(
        force_drain_all=True,
    )
    diagnostic = dict(result.diagnostic or {})

    assert diagnostic["o2_admitted_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_accumulated_mol"] == pytest.approx(9.0)
    assert diagnostic["o2_relieved_mol"] == 0.0
    assert sim.atom_ledger.mol_by_account(
        "reservoir.oxygen_cistern_liquid_inventory"
    )["O2"] == pytest.approx(9.0)


def test_live_and_picard_share_evaporation_control_construction(monkeypatch):
    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 5.0
    sim.overhead.pressure_mbar = 12.0
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 100.0},
        vapor_pressures_source={},
        activity_coefficients={},
        liquid_fraction=1.0,
        diagnostics={},
    )
    sim._last_vapor_pressure_diagnostic = {}
    partials = {"Fe": 100.0, "N2": 200.0}
    effective_pressure_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures,
        equilibrium,
    )
    vapour_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=sim.melt.temperature_C + 273.15,
        effective_pressure_source=effective_pressure_source,
    )
    batch_pressures, batch_overlay = flux_pressures_from_batch(
        vapour_batch,
        effective_pressure_source=effective_pressure_source,
    )
    batch_overlay.update(
        compare_live_shadow_to_batch_flux(
            batch=vapour_batch,
            live_pressures_Pa=None,
            batch_flux_pressures_Pa=batch_pressures,
        )
    )
    expected_partials = dict(partials)
    for species in batch_pressures:
        expected_partials.setdefault(species, 0.0)
    expected, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa=expected_partials,
        overhead_pressure_pa=sim._evaporation_overhead_total_pressure_Pa(
            expected_partials
        ),
        vapour_batch_flux_pressures_Pa=batch_pressures,
        vapour_batch_report=serialize_vapour_batch(vapour_batch),
        vapour_batch_flux_overlay=batch_overlay,
    )
    monkeypatch.setattr(
        sim,
        "_resolve_evaporation_vapour_batch",
        lambda _equilibrium, *, temperature_K, effective_pressure_source: vapour_batch,
    )
    captured = []

    def capture_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(control_inputs)
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    monkeypatch.setattr(sim, "_dispatch_only", capture_dispatch)
    sim._calculate_evaporation(
        equilibrium,
        overhead_partials_override_Pa=partials,
    )

    assert captured == [expected]
    assert "vapour_batch_flux_pressures_Pa" in captured[0]
    assert captured[0]["vapour_batch_flux_pressures_Pa"] == batch_pressures
    assert captured[0]["overhead_pressure_pa"] == pytest.approx(500.0)


def test_live_and_picard_transport_sidecar_match_at_same_state(monkeypatch):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.capacity_coupling import _headspace_partial_pressures_Pa
    from simulator.condensation import _LENNARD_JONES_PARAMS
    from simulator.evaporation import (
        CONTROL_FLUX_PRESSURES_KEY,
        _load_hkl_upper_bound_transport_species,
    )

    monkeypatch.delitem(_LENNARD_JONES_PARAMS, "Fe")
    captures = {"live": [], "capacity": []}
    active_path = ["live"]
    real_dispatch = BuiltinEvaporationFluxProvider.dispatch

    def capture_sidecar(provider, request):
        control_inputs = request.control_inputs
        overlay = control_inputs.get("vapour_batch_flux_overlay")
        captures[active_path[0]].append({
            "controls": dict(control_inputs),
            "film": {
                "partials": copy.deepcopy(
                    control_inputs.get("overhead_partials_Pa")
                ),
                "total_pressure": control_inputs.get("overhead_pressure_pa"),
                "flux_pressures": copy.deepcopy(
                    control_inputs.get(CONTROL_FLUX_PRESSURES_KEY)
                ),
                "oxygen_drive": control_inputs.get("surface_oxygen_bound"),
            },
            "sidecar": copy.deepcopy(
                getattr(overlay, "transport_sidecar", {})
            ),
        })
        return real_dispatch(provider, request)

    monkeypatch.setattr(
        BuiltinEvaporationFluxProvider,
        "dispatch",
        capture_sidecar,
    )

    simulators = {}
    equilibria = {}
    for path in ("live", "capacity"):
        sim = _real_capacity_sim()
        sim.start_campaign(CampaignPhase.C0)
        sim.melt.temperature_C = 1600.0
        sim.vapor_pressures["metals"]["Fe"].update({
            "critical_temperature_K": 6000.0,
            "critical_pressure_atm": 500.0,
        })
        overhead_mol = {"N2": 0.1}
        sim.atom_ledger.load_external(
            "process.overhead_gas",
            {"N2": overhead_mol["N2"] * M_N2 / 1000.0},
            source="b1318 live/Picard sidecar parity fixture",
            material_origin="feedstock",
        )
        partials = _headspace_partial_pressures_Pa(
            overhead_mol,
            species=overhead_mol,
            temperature_K=sim._headspace_temperature_K(),
            volume_m3=sim._headspace_volume_m3(),
        )
        sim._melt_headspace_composition_mbar = {
            species: pressure_pa / 100.0
            for species, pressure_pa in partials.items()
        }
        simulators[path] = sim
        equilibria[path] = sim._get_equilibrium()

    active_path[0] = "live"
    live_flux = simulators["live"]._calculate_evaporation(
        equilibria["live"]
    )
    active_path[0] = "capacity"
    capacity_result = simulators[
        "capacity"
    ]._compute_capacity_coupling_shadow_impl(
        equilibria["capacity"]
    )

    assert isinstance(live_flux, EvaporationFlux)
    assert isinstance(capacity_result, CapacityShadowResult)
    assert captures["live"]
    assert captures["capacity"]
    live_request = captures["live"][0]
    capacity_request = captures["capacity"][0]
    assert live_request["sidecar"] == capacity_request["sidecar"]
    assert live_request["film"] == capacity_request["film"]
    sidecar = live_request["sidecar"]
    assert sidecar["transport_eligibility_by_species"]["Fe"] == (
        "chapman_enskog"
    )
    assert sidecar["lennard_jones_parameters_by_species"]["Fe"]["flag"] == (
        "lj_estimated:tc_pc"
    )
    for request in (live_request, capacity_request):
        assert "lennard_jones_property_rows_by_species" not in request[
            "controls"
        ]
        assert "transport_eligibility_by_species" not in request["controls"]
        assert "hkl_upper_bound_transport_species" in request["controls"]
        assert (
            "hkl_upper_bound_transport_species" not in request["sidecar"]
        )
        overlay = request["controls"]["vapour_batch_flux_overlay"]
        assert not any(
            notice.get("kind") == "transport_parameters_estimated"
            for notice in overlay.get("notices", ())
            if isinstance(notice, dict)
        )
    assert any(
        notice.get("kind") == "transport_parameters_estimated"
        and notice.get("species") == "Fe"
        for notice in overlay.get("notices", ())
        if isinstance(notice, dict)
    )

    no_overlay_sim = _real_capacity_sim()
    no_overlay_sim.start_campaign(CampaignPhase.C0)
    no_overlay_sim.melt.temperature_C = 1600.0
    no_overlay_equilibrium = no_overlay_sim._get_equilibrium()
    no_overlay_controls, _ = no_overlay_sim._evaporation_flux_control_inputs(
        no_overlay_equilibrium,
        overhead_partials_Pa={},
    )
    assert "vapour_batch_flux_overlay" not in no_overlay_controls
    assert no_overlay_controls["hkl_upper_bound_transport_species"] == tuple(
        sorted(_load_hkl_upper_bound_transport_species(
            no_overlay_sim.vapor_pressures
        ))
    )
    for path in ("live", "capacity"):
        published_overlay = simulators[path]._last_vapour_batch_flux_overlay
        assert any(
            notice.get("kind") == "transport_parameters_estimated"
            and notice.get("species") == "Fe"
            and notice.get("flag") == "lj_estimated:tc_pc"
            for notice in published_overlay.get("notices", ())
            if isinstance(notice, dict)
        )


def test_no_cold_train_reuses_head_bleed_without_evaluating_shadow_flux():
    head = {"O2": 1.25, "N2": 0.75}

    def forbidden_flux(_partials):
        raise AssertionError("NoColdTrain must not recompute HEAD operations")

    result = solve_capacity_shadow(
        pre_holdup_mol=head,
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=forbidden_flux,
        capacity=NoColdTrain(),
        head_bled_species_mol=head,
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.bled_species_mol == head
    assert result.terminal_offgas_mol == {"N2": 0.75}
    assert result.iterations == 0


def test_invalid_capacity_tag_is_typed_refusal_before_flux_arithmetic():
    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _: (_ for _ in ()).throw(
            AssertionError("flux arithmetic must not run")
        ),
        capacity=SimpleNamespace(value_kg_hr=math.nan),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == (
        "cold_train_capacity must be NoColdTrain or FiniteCapacity"
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, 0.0, -1.0])
def test_malformed_finite_capacity_is_refused_before_arithmetic(value):
    capacity = object.__new__(FiniteCapacity)
    object.__setattr__(capacity, "value_kg_hr", value)
    object.__setattr__(capacity, "p_ref_Pa", None)
    object.__setattr__(capacity, "T_ref_K", None)

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _: (_ for _ in ()).throw(
            AssertionError("flux arithmetic must not run")
        ),
        capacity=capacity,
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == "cold_train_capacity must be finite and positive"


def test_combined_saturation_uses_max_and_pipe_wins_exact_tie():
    tie = combined_saturation(
        total_evaporation_kg_hr=2.0,
        oxygen_evaporation_kg_hr=1.0,
        pipe_capacity_kg_hr=2.0,
        capacity=FiniteCapacity(1.0),
    )
    oxygen = combined_saturation(
        total_evaporation_kg_hr=1.0,
        oxygen_evaporation_kg_hr=2.0,
        pipe_capacity_kg_hr=2.0,
        capacity=FiniteCapacity(1.0),
    )

    assert tie.combined == 1.0
    assert tie.binding_cause == "pipe"
    assert oxygen.combined == 2.0
    assert oxygen.binding_cause == "oxygen"


@pytest.mark.parametrize(
    ("total_rate", "oxygen_rate", "pipe_capacity", "oxygen_capacity", "label"),
    [
        (2.0, 0.1, 1.0, 1.0, "pipe saturated"),
        (1.0, 2.0, 10.0, 1.0, "O2 capacity saturated"),
        (2.0, 2.0, 1.0, 1.0, "pipe saturated"),
    ],
    ids=["pipe-win", "oxygen-win", "exact-tie-pipe"],
)
def test_loop3_renders_live_binding_cause(
    total_rate,
    oxygen_rate,
    pipe_capacity,
    oxygen_capacity,
    label,
):
    saturation = combined_saturation(
        total_evaporation_kg_hr=total_rate,
        oxygen_evaporation_kg_hr=oxygen_rate,
        pipe_capacity_kg_hr=pipe_capacity,
        capacity=FiniteCapacity(oxygen_capacity),
    )
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        temperature_C=1500.0,
        campaign=CampaignPhase.C2A,
        campaign_hour=0.0,
    )
    sim.overhead = SimpleNamespace(
        transport_saturation_pct=saturation.combined * 100.0,
        transport_binding_cause=saturation.binding_cause,
        turbine_limited=False,
    )
    sim.campaign_mgr = SimpleNamespace(
        get_temp_target=lambda *_args: (1600.0, 100.0)
    )

    sim._update_temperature()

    assert label in sim._last_throttle_reason


def test_total_pressure_vessel_rating_refuses_after_convergence():
    flux_calls = 0

    def hk_flux(_partials):
        nonlocal flux_calls
        flux_calls += 1
        return {"O2": 0.032}

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0, "N2": 2.0},
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=FiniteCapacity(0.032),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-12,
        p_open_Pa=1.0,
        vessel_rating_Pa=1500.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert result.reason.startswith("vessel_total_pressure_exceeds_rating:")
    assert result.iterations > 0
    assert flux_calls == result.iterations


def test_zero_bleed_saturation_boundary_refuses_on_picard_non_convergence():
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"N2": M_N2},
        flux_kg_hr_at_partials=lambda partials: {
            "N2": 1.0e-9 if partials["N2"] < 1.0e-6 else 0.0
        },
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=100.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
        max_iterations=3,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert result.reason == "picard_non_convergence"
    assert result.iterations == 3


def test_live_capacity_refusal_precedes_overhead_bleed_commit(monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = sim._cold_train_capacity_policy()
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="binding capacity refusal fixture",
        material_origin="feedstock",
    )
    before_bleeds = sum(
        transition.reason == "overhead_bleed"
        for transition in sim.atom_ledger.transitions
    )
    monkeypatch.setattr(
        capacity_coupling,
        "solve_capacity_shadow",
        lambda **_kwargs: CapacityShadowRefusal(
            reason="vessel_total_pressure_exceeds_rating:2000>1500",
            iterations=3,
        ),
    )

    with pytest.raises(CapacityCouplingRefusalError) as exc_info:
        sim.step()

    assert exc_info.value.refusal.iterations == 3
    assert sum(
        transition.reason == "overhead_bleed"
        for transition in sim.atom_ledger.transitions
    ) == before_bleeds


@pytest.mark.parametrize(
    ("species", "rate_kg_hr", "capacity_kg_hr", "rating_Pa"),
    [
        ("O2", 2.0e-6, 1.0e-6, 1.0e9),
        ("SiO", 1.0e-3, 1.0e-6, 1.0e9),
        ("N2", 2.8e-5, 1.0, 1.0e-3),
    ],
)
def test_current_hour_source_cannot_bypass_capacity_or_vessel_refusal(
    monkeypatch,
    species,
    rate_kg_hr,
    capacity_kg_hr,
    rating_Pa,
):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    cold_train = SimpleNamespace(relief={
        "k_relief_kg_hr_Pa": 1.0e-3,
        "p_open_Pa": min(1.0, rating_Pa / 2.0),
        "vessel_rating_Pa": rating_Pa,
    })
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(capacity_kg_hr), cold_train),
    )
    flux = EvaporationFlux(species_kg_hr={species: rate_kg_hr})
    flux.update_totals()
    monkeypatch.setattr(sim, "_calculate_evaporation", lambda *_a, **_k: flux)
    calls = []

    def refused(**kwargs):
        calls.append(kwargs)
        return CapacityShadowRefusal("vessel_or_capacity_refusal", 2)

    monkeypatch.setattr(capacity_coupling, "solve_capacity_shadow", refused)

    with pytest.raises(CapacityCouplingRefusalError):
        sim.step()

    assert len(calls) == 1


@pytest.mark.parametrize("downstream_pressure_Pa", [500.0, 2000.0])
def test_pipe_saturation_uses_executable_pressure_ratio_capacity(
    downstream_pressure_Pa,
):
    conductance = 1.0e-8
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"N2": M_N2},
        flux_kg_hr_at_partials=lambda _partials: {"N2": M_N2},
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=conductance,
        downstream_pressure_Pa=downstream_pressure_Pa,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    total_pressure_Pa = sum(result.partial_pressures_Pa.values())
    fraction = compressible_pressure_capacity_fraction(
        total_pressure_Pa / 100000.0,
        downstream_pressure_Pa / 100000.0,
    )
    executable_capacity = conductance * fraction * 3600.0
    expected = (
        M_N2 / executable_capacity if executable_capacity > 0.0 else math.inf
    )
    assert result.saturation.pipe == pytest.approx(expected)
    provider_bled = BuiltinOverheadBleedProvider._bled_species_mol(
        {"N2": 1.0},
        total_mol=1.0,
        total_kg=M_N2,
        controls={
            "bleed_conductance_kg_s": conductance,
            "p_total_bar": total_pressure_Pa / 100000.0,
            "p_downstream_bar": downstream_pressure_Pa / 100000.0,
            "dt_hr": 1.0,
        },
    )
    assert result.bled_species_mol == pytest.approx(provider_bled)


def test_picard_source_includes_stoichiometric_oxide_vapor_oxygen():
    sim = _real_capacity_sim()
    sp_data = sim.vapor_pressures["oxide_vapors"]["SiO"]
    stoich = sim._evaporation_stoich("SiO", sp_data)
    sio_rate_kg_hr = 1.0e-3
    sio_molar_mass = resolve_species_formula(
        "SiO", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()

    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"SiO": sio_molar_mass, "O2": o2_molar_mass},
        flux_kg_hr_at_partials=lambda _partials: {"SiO": sio_rate_kg_hr},
        overhead_source_mol_hr_at_partials=lambda _partials: (
            sim._project_evaporation_overhead_source_mol_hr(
                {"SiO": sio_rate_kg_hr},
                {"SiO": stoich},
            )
        ),
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0e-10,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    expected_o2_mol = (
        sio_rate_kg_hr
        * stoich["O2_per_product_kg"]
        / o2_molar_mass
    )
    assert 0.0 < result.partial_pressures_Pa["O2"] < expected_o2_mol * 1000.0
    expected_o2_kg_hr = expected_o2_mol * o2_molar_mass
    assert result.saturation.oxygen == pytest.approx(expected_o2_kg_hr)
    assert result.saturation.pipe == pytest.approx(
        (sio_rate_kg_hr + expected_o2_kg_hr) / (1.0e-10 * 3600.0)
    )
    assert result.saturation.combined == result.saturation.pipe


def test_binding_cold_train_does_not_turn_redox_no_capacity_into_fo2_move(
    monkeypatch,
):
    sim = _real_capacity_sim()
    capacity = capacity_from_hardware(
        thermal_train_parameters_from_mapping().cold_train
    )
    assert isinstance(capacity, FiniteCapacity)
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = 1873.15
    sim._sync_oxygen_reservoir_mirror()
    before_transitions = len(sim.atom_ledger.transitions)
    monkeypatch.setattr(
        sim,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 0.0,
    )

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_metal_loss": 1.0},
        exchange_direction="redox_source:evaporative_loss",
        temperature_K=1873.15,
    )

    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert reservoir.redox_source_terms_applied is False
    assert reservoir.redox_source_skip_reason == "no_melt_redox_capacity"
    assert len(sim.atom_ledger.transitions) == before_transitions


@pytest.mark.parametrize("finite_headspace_enabled", [False, True])
def test_overhead_model_leaves_provider_partition_mirrors_unset(
    finite_headspace_enabled,
):
    sim = _real_capacity_sim()
    sim.overhead_model._finite_headspace_enabled = finite_headspace_enabled
    if finite_headspace_enabled:
        sim.overhead_model._headspace_volume_m3 = 1.0

    gas = sim.overhead_model.update(
        EvaporationFlux(),
        sim.melt,
        sim.train,
        actual_O2_kg_hr=1.0,
        actual_O2_mol_hr=1.0 / M_O2,
        overhead_holdup_mol={},
        cold_train_capacity=FiniteCapacity(0.1),
    )

    assert gas.turbine_limited is False
    assert gas.O2_vented_kg_hr == 0.0
    assert gas.turbine_flow_kg_hr == 0.0
    assert gas.melt_offgas_O2_mol_hr == pytest.approx(1.0 / M_O2)


def test_hour_classifier_keeps_parentless_base_species_available():
    sim = _real_capacity_sim()
    sim.vapor_pressures["metals"]["Na"].pop("parent_oxide")
    sim.melt.hour = 4
    overlay = {"batch_channel_states": {"Na": "eligible"}, "notices": []}

    unavailable = EvaporationMixin._classify_hour_vapour_availability(
        sim,
        SimpleNamespace(),
        {"Na": 1.0},
        overlay,
        {},
    )

    assert unavailable == {}
    availability = sim._vapour_batch_hour_availability
    assert availability["member_species"] == ("Na",)
    assert availability["admitted_member_species"] == ("Na",)
    assert overlay["notices"] == []


def test_aux_map_owner_returns_parentless_species_failure():
    sim = _real_capacity_sim()
    sim.vapor_pressures["metals"]["Na"].pop("parent_oxide")

    aux_maps = sim._build_evaporation_aux_maps({"Na": 1.0})

    assert aux_maps.unavailable_by_species == {
        "Na": "missing_evaporation_stoichiometry",
    }
    molar_masses, stoich, available_oxide = aux_maps
    assert "Na" not in molar_masses
    assert "Na" not in stoich
    assert "Na" not in available_oxide


def test_vapour_availability_does_not_cross_batch_identity():
    sim = _real_capacity_sim()
    sim.melt.hour = 2
    EvaporationMixin._classify_hour_vapour_availability(
        sim,
        SimpleNamespace(),
        {"Na": 1.0},
        {"batch_channel_states": {"Na": "eligible"}, "notices": []},
        {},
    )
    previous_identity = sim._vapour_batch_identity
    assert sim._vapour_batch_hour_availability["member_species"] == ("Na",)

    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    assert sim._vapour_batch_identity != previous_identity
    assert sim._vapour_batch_hour_availability is None


def test_live_zero_liquid_checks_interpolation_before_equilibrium_vapour():
    sim = _real_capacity_sim()
    reads = []

    class ProvenanceDiagnostic(dict):
        def get(self, key, *args):
            reads.append(key)
            return super().get(key, *args)

    sim._last_vapor_pressure_diagnostic = ProvenanceDiagnostic({
        "source": "cached_interpolated",
    })

    class InterpolatedZeroLiquid:
        @property
        def liquid_fraction(self):
            assert reads == ["source"]
            return 0.0

        @property
        def vapor_pressures_Pa(self):
            raise AssertionError("zero-liquid live path read cached pressures")

        @property
        def vapor_pressures_source(self):
            raise AssertionError("zero-liquid live path read cached sources")

        @property
        def activity_coefficients(self):
            raise AssertionError("zero-liquid live path read cached activities")

        @property
        def diagnostics(self):
            raise AssertionError("zero-liquid live path read cached diagnostics")

    flux = sim._calculate_evaporation(InterpolatedZeroLiquid())

    assert reads == ["source"]
    assert flux.species_kg_hr == {}


def test_capacity_zero_liquid_checks_interpolation_before_equilibrium_vapour():
    sim = _real_capacity_sim()
    reads = []

    class ProvenanceDiagnostic(dict):
        def get(self, key, *args):
            reads.append(key)
            return super().get(key, *args)

    sim._last_vapor_pressure_diagnostic = ProvenanceDiagnostic({
        "source": "cached_interpolated",
    })

    class InterpolatedZeroLiquid:
        status = "ok"

        @property
        def liquid_fraction(self):
            assert reads == ["source"]
            return 0.0

        @property
        def vapor_pressures_Pa(self):
            raise AssertionError("zero-liquid capacity read cached pressures")

        @property
        def vapor_pressures_source(self):
            raise AssertionError("zero-liquid capacity read cached sources")

        @property
        def activity_coefficients(self):
            raise AssertionError("zero-liquid capacity read cached activities")

        @property
        def diagnostics(self):
            raise AssertionError("zero-liquid capacity read cached diagnostics")

    result = sim._compute_capacity_coupling_shadow_impl(
        InterpolatedZeroLiquid()
    )

    assert reads == ["source"]
    assert isinstance(result, CapacityShadowResult)


def test_free_molecular_legacy_lj_estimate_still_publishes_its_notice():
    from simulator.evaporation import _publish_transport_parameter_notices

    sim = _real_capacity_sim()
    capacity, cold_train = sim._cold_train_capacity_policy()
    relief = dict(cold_train.relief)
    relief.update({"p_open_Pa": 5.0e-7, "vessel_rating_Pa": 1.0e-6})
    sim._cold_train_capacity_policy = lambda: (
        capacity,
        replace(cold_train, relief=relief),
    )
    sim.melt.hour = 1
    overlay = {"batch_channel_states": {"Fe": "eligible"}, "notices": []}

    unavailable = EvaporationMixin._classify_hour_vapour_availability(
        sim,
        SimpleNamespace(),
        {"Fe": 1.0},
        overlay,
        {},
    )

    availability = sim._vapour_batch_hour_availability
    estimate = availability["lennard_jones_parameters_by_species"]["Fe"]
    assert unavailable == {}
    assert estimate["flag"] == "lj_estimated:legacy_code_proxy"
    assert availability["transport_eligibility_by_species"]["Fe"] == (
        "free_molecular"
    )
    estimate_notice = availability[
        "transport_parameter_notices_by_species"
    ]["Fe"]
    assert estimate_notice["kind"] == "transport_parameters_estimated"
    assert estimate_notice["flag"] == "lj_estimated:legacy_code_proxy"
    _publish_transport_parameter_notices(overlay, availability)
    assert any(
        notice.get("kind") == "transport_parameters_estimated"
        and notice.get("species") == "Fe"
        and notice.get("flag") == "lj_estimated:legacy_code_proxy"
        for notice in overlay["notices"]
    )
