"""Admission-refusal and flux_dormant must not inventory-debit.

P1-1 / P1-2 compose at the condensation debit-status chokepoint: both
must map onto refused so ``_route_evaporated_species_to_condensation``
withholds the melt debit. These cases fail on HEAD 72e6d3a8, where
admission-refused remaining vapour and flux_dormant kg/hr still call
``_credit_evaporation_transition``.
"""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from simulator.state import EvaporationFlux
from simulator.vapour_rail.catalog import (
    compiled_catalog_for,
    vapor_pressure_compatibility_view,
)
from simulator.vapour_rail.instrumentation import (
    VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
    VAPOUR_CARRIER_AUTHORITY_MISSING,
    VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
    VAPOUR_CARRIER_AUTHORITY_REFUSED,
    VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
)
from simulator.vapour_rail.request import REFUSAL_INAPPLICABLE_PREDICATE

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "rail_debit_chem_conftest", _CONFTEST
)
_conftest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_conftest)
_build_sim = _conftest._build_sim


DEBITING_CARRIER_STATUSES = (
    VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
    VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
    VAPOUR_CARRIER_AUTHORITY_MISSING,
)
NON_DEBITING_CARRIER_STATUSES = (
    VAPOUR_CARRIER_AUTHORITY_REFUSED,
    VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
)


def _flip_hot_train_inapplicable(vapor_pressure_data, species_id: str):
    payload = deepcopy(vapor_pressure_data.catalog_payload)
    family_id = compiled_catalog_for(
        payload, emit_u0_request_rules=False
    ).species[species_id].family_id
    payload["families"][family_id]["code_metadata"][
        "hot_train_applicability"
    ] = "not_applicable"
    return vapor_pressure_compatibility_view(payload)


def _carrier_record(species: str, status: str) -> dict[str, dict]:
    if status == VAPOUR_CARRIER_AUTHORITY_REFUSED:
        pressure = {"kind": "refusal", "code": "test_refusal"}
        flux = {"kind": "refusal", "code": "test_refusal"}
    elif status == VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO:
        pressure = {"kind": "zero_by_physics", "evidence_ref": "test:zero"}
        flux = {"kind": "eligible", "alpha_ref": "test:alpha"}
    else:
        pressure = {"kind": "value", "pa": 1.0}
        flux = {"kind": "eligible", "alpha_ref": "test:alpha"}
    authoritative = status == VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE
    record = {
        "species_id": species,
        "pressure": pressure,
        "flux": flux,
        "is_refused": status == VAPOUR_CARRIER_AUTHORITY_REFUSED,
        "is_union_flux_eligible": status
        in {
            VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
            VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
        },
        "is_flux_active": status
        in {
            VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
            VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
        },
        "validation_status": (
            "validated" if authoritative else "modeled-PENDING"
        ),
        "verdict_status": (
            "authoritative"
            if authoritative
            else "status_bearing_non_authoritative"
        ),
        "certification_ceiling": (
            "validated_point" if authoritative else "never"
        ),
    }
    return {species: record}


def _route_species(sim, species: str, rate_kg_hr: float, *, status: str | None):
    carrier = (
        {}
        if status in {None, VAPOUR_CARRIER_AUTHORITY_MISSING}
        else _carrier_record(species, status)
    )
    return sim._route_to_condensation(
        EvaporationFlux(
            species_kg_hr={species: rate_kg_hr},
            total_kg_hr=rate_kg_hr,
            carrier_authority_by_species=carrier,
        )
    )


def test_admission_refusal_does_not_debit_parent_oxide(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    flipped = _flip_hot_train_inapplicable(vapor_pressure_data, "Fe")
    sim = _build_sim(
        "lunar_mare_low_ti",
        flipped,
        feedstocks_data,
        setpoints_data,
    )
    rate_kg_hr = 1.0e-6
    feo_before = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get("FeO", 0.0)
    )
    assert feo_before > 0.0
    transition_count_before = len(sim.atom_ledger.transitions)

    _route_species(sim, "Fe", rate_kg_hr, status=None)

    authority = sim.condensation_model.last_condensation_authority_by_species[
        "Fe"
    ]
    refusal = sim.condensation_model.last_condensation_refusals_by_species["Fe"]
    assert authority["status"] == VAPOUR_CARRIER_AUTHORITY_REFUSED
    assert refusal["reason"] == REFUSAL_INAPPLICABLE_PREDICATE
    assert refusal["mass_disposition"] == "retained_in_source_pending_authority"
    assert refusal["remaining_mass_kg_hr"] == pytest.approx(0.0)
    assert refusal["retained_in_source_mass_kg_hr"] == pytest.approx(rate_kg_hr)
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "FeO", 0.0
    ) == pytest.approx(feo_before)
    assert [
        transition.name
        for transition in sim.atom_ledger.transitions[transition_count_before:]
        if transition.name.startswith("evaporate_")
    ] == []


@pytest.mark.parametrize(
    "species,parent,incoming_status",
    (
        ("MnO_gas", "MnO", None),
        ("MnO_gas", "MnO", VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING),
        ("MnO_gas", "MnO", VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE),
        ("FeO_association_gas", "FeO", None),
    ),
)
def test_flux_dormant_carrier_does_not_debit_when_kg_hr_positive(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    species,
    parent,
    incoming_status,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.condensation_model.condensation_temperatures_C[species] = 1400.0
    rate_kg_hr = 1.0e-6
    parent_before = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get(parent, 0.0)
    )
    assert parent_before > 0.0
    transition_count_before = len(sim.atom_ledger.transitions)

    _route_species(sim, species, rate_kg_hr, status=incoming_status)

    authority = sim.condensation_model.last_condensation_authority_by_species[
        species
    ]
    refusal = sim.condensation_model.last_condensation_refusals_by_species[
        species
    ]
    assert authority["status"] == VAPOUR_CARRIER_AUTHORITY_REFUSED
    assert refusal["reason"] == "flux_dormant_never_inventory_debit"
    assert refusal["mass_disposition"] == "retained_in_source_pending_authority"
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        parent, 0.0
    ) == pytest.approx(parent_before)
    assert [
        transition.name
        for transition in sim.atom_ledger.transitions[transition_count_before:]
        if transition.name.startswith("evaporate_")
    ] == []


@pytest.mark.parametrize("status", DEBITING_CARRIER_STATUSES)
def test_admitted_certifying_species_still_debit(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    status,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    rate_kg_hr = 1.0e-6
    feo_before = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get("FeO", 0.0)
    )

    _route_species(sim, "Fe", rate_kg_hr, status=status)

    authority = sim.condensation_model.last_condensation_authority_by_species[
        "Fe"
    ]
    assert authority["status"] == status
    assert (
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get("FeO", 0.0)
        < feo_before
    )
    assert any(
        transition.name == "evaporate_Fe"
        for transition in sim.atom_ledger.transitions
    )


@pytest.mark.parametrize("status", NON_DEBITING_CARRIER_STATUSES)
def test_upstream_non_debiting_statuses_still_withhold(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    status,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    rate_kg_hr = 1.0e-6
    feo_before = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get("FeO", 0.0)
    )

    _route_species(sim, "Fe", rate_kg_hr, status=status)

    authority = sim.condensation_model.last_condensation_authority_by_species[
        "Fe"
    ]
    assert authority["status"] == status
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "FeO", 0.0
    ) == pytest.approx(feo_before)
