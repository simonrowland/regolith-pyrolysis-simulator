from __future__ import annotations

import copy
import math

import pytest

from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from simulator.accounting import AtomLedger, LedgerTransition
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import CondensationModel
from simulator.equipment import EquipmentDesigner
from simulator.lab_geometry import LabGeometryError, parse_lab_geometry
from simulator.runner import _wall_deposit_mol_by_species, _wall_deposit_report_kg
from simulator.state import (
    CondensationTrain,
    register_wall_deposit_accounts,
    unregister_wall_deposit_accounts,
)
from simulator.trace import wall_deposit_by_segment_species_kg


def robinot_geometry_fixture() -> dict:
    """
    Robinot anchor #1 geometry capture before coding:

    | item | stated by Robinot/finding | runtime disposition |
    | glass reactor | stated | chamber material fact; dimensions unstated |
    | water-cooled steel holder | stated | holder surface, temperature/area assumptions |
    | refrigerated Cu condenser above reactor | stated | condenser surface, temperature/area assumptions |
    | porous SS filter before pump | stated | filter surface, temperature/area assumptions |
    | window deposit location | stated by per-location species | window surface, area assumption |
    | condenser/window/filter temps | missing | sensitivity-marked assumptions |
    | per-location masses, view factors, sticking | missing | sensitivity-marked assumptions |
    """

    return {
        "id": "robinot_2026_geometry_test",
        "scale": "gram_lab",
        "equipment_sizing": "lab_fixed_geometry",
        "sample": {"mass_g": 3.38},
        "surfaces": [
            {
                "id": "holder",
                "role": "holder",
                "area_m2": 0.00024,
                "temperature_C": 25.0,
                "view_factor_from_melt": 0.12,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "holder_area_sweep",
                "extraction_note": "Robinot holder stated; area/T/view factor assumed for sensitivity sweep",
                "equivalent_diameter_m": 0.018,
            },
            {
                "id": "window",
                "role": "window",
                "area_m2": 0.00018,
                "temperature_C": 80.0,
                "view_factor_from_melt": 0.08,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "window_area_sweep",
                "extraction_note": "Window deposit location stated; area/T/view factor assumed",
                "equivalent_diameter_m": 0.018,
            },
            {
                "id": "condenser",
                "role": "condenser",
                "area_m2": 0.0005,
                "temperature_C": 5.0,
                "view_factor_from_melt": 0.55,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "condenser_area_sweep",
                "extraction_note": "Refrigerated Cu condenser stated; geometry values assumed",
                "equivalent_diameter_m": 0.02,
            },
            {
                "id": "filter",
                "role": "filter",
                "area_m2": 0.00032,
                "temperature_C": 20.0,
                "view_factor_from_melt": 0.25,
                "line_of_sight_to_melt": False,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "filter_area_sweep",
                "extraction_note": "Porous SS filter stated; blocked LOS and area assumed",
                "equivalent_diameter_m": 0.016,
            },
            {
                "id": "chamber_wall",
                "role": "chamber_wall",
                "area_m2": 0.0012,
                "temperature_C": 80.0,
                "view_factor_from_melt": 0.0,
                "line_of_sight_to_melt": False,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "chamber_wall_area_sweep",
                "extraction_note": "Glass chamber wall stated; geometry values assumed",
                "equivalent_diameter_m": 0.03,
            },
        ],
    }


def test_gram_lab_equipment_bypass_uses_declared_lab_surface_area() -> None:
    raw = robinot_geometry_fixture()
    geometry = parse_lab_geometry(raw)

    design = EquipmentDesigner().design_for_batch(
        1000.0,
        {},
        lab_geometry=geometry,
    )

    declared_area = sum(row["area_m2"] for row in raw["surfaces"])
    assert design.lab_geometry_id == raw["id"]
    assert design.batch_mass_kg == pytest.approx(0.00338)
    assert design.pipe.surface_area_m2 == pytest.approx(declared_area)
    assert design.pipe.diameter_m == pytest.approx(0.016)
    assert design.pipe.diameter_m < 0.12
    assert design.pipe.length_m == pytest.approx(
        declared_area / (math.pi * design.pipe.diameter_m)
    )


def test_unknown_lab_surface_role_is_named_refusal() -> None:
    raw = robinot_geometry_fixture()
    raw["surfaces"][0]["role"] = "mystery_baffle"

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == "unknown_lab_surface_role"
    assert "holder" in str(excinfo.value)


def test_lab_surface_provenance_gates_accept_robinot_fixture() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())

    assert geometry is not None
    assert geometry.surfaces[0].source_class == "assumption_with_sensitivity_marker"
    assert geometry.surfaces[0].sensitivity_marker == "holder_area_sweep"
    assert geometry.surfaces[0].extraction_note


@pytest.mark.parametrize(
    "mutator, refusal_code",
    [
        (
            lambda raw: raw["surfaces"][0].__setitem__("source_class", "diagram_guess"),
            "invalid_lab_geometry_source_class",
        ),
        (
            lambda raw: raw["surfaces"][0].pop("sensitivity_marker"),
            "missing_lab_geometry_sensitivity_marker",
        ),
        (
            lambda raw: raw["surfaces"][0].__setitem__("extraction_note", " "),
            "missing_lab_geometry_extraction_note",
        ),
    ],
)
def test_lab_surface_provenance_poison_pairs_are_named_refusals(
    mutator,
    refusal_code: str,
) -> None:
    raw = copy.deepcopy(robinot_geometry_fixture())
    mutator(raw)

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == refusal_code


def test_provider_routes_registered_lab_surface_accounts() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())
    accounts = geometry.wall_deposit_accounts
    register_wall_deposit_accounts(accounts)
    try:
        provider = BuiltinCondensationRouteProvider()
        holder_account = geometry.surfaces[0].wall_deposit_account
        condenser_account = geometry.surfaces[2].wall_deposit_account
        request = IntentRequest(
            intent=ChemistryIntent.CONDENSATION_ROUTE,
            account_view=ProviderAccountView(
                accounts={
                    "process.overhead_gas": {"Na": 1.0},
                    "process.condensation_train": {},
                    "process.wall_deposit": {},
                    holder_account: {},
                    condenser_account: {},
                },
                species_formula_registry={},
            ),
            temperature_C=1100.0,
            pressure_bar=1e-6,
            control_inputs={
                "species": "Na",
                "condensed_kg": 0.01,
                "sp_data": {},
                "wall_deposit_fraction": 1.0,
                "wall_deposit_account_fractions": {
                    holder_account: 0.25,
                    condenser_account: 0.75,
                },
                "dt_hr": 1.0,
            },
        )

        result = provider.dispatch(request)

        assert result.status == "ok"
        assert result.transition is not None
        assert "process.wall_deposit" not in result.transition.credits
        assert result.transition.credits[holder_account]["Na"] > 0.0
        assert result.transition.credits[condenser_account]["Na"] > 0.0
        assert result.diagnostic["credited_wall_deposit_accounts_kg"][
            holder_account
        ] == pytest.approx(0.0025)
        assert result.diagnostic["credited_wall_deposit_accounts_kg"][
            condenser_account
        ] == pytest.approx(0.0075)
    finally:
        unregister_wall_deposit_accounts(accounts)


def test_provider_refuses_unregistered_lab_surface_account() -> None:
    bad_account = "process.wall_deposit_segment_unregistered_poison"
    provider = BuiltinCondensationRouteProvider()
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
                bad_account: {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {},
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {bad_account: 1.0},
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "undeclared_wall_deposit_account"
    assert result.diagnostic["account"] == bad_account


def test_wall_allocation_uses_view_factor_and_line_of_sight() -> None:
    raw = {
        "id": "geometry_sensitivity",
        "scale": "gram_lab",
        "equipment_sizing": "lab_fixed_geometry",
        "surfaces": [
            {
                "id": "holder",
                "role": "holder",
                "area_m2": 0.001,
                "temperature_C": 25.0,
                "view_factor_from_melt": 0.9,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "holder_view_factor_sweep",
                "extraction_note": "Synthetic sensitivity fixture for R5.6 allocation",
            },
            {
                "id": "condenser",
                "role": "condenser",
                "area_m2": 0.001,
                "temperature_C": 25.0,
                "view_factor_from_melt": 0.1,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "condenser_view_factor_sweep",
                "extraction_note": "Synthetic sensitivity fixture for R5.6 allocation",
            },
        ],
    }
    model = CondensationModel(CondensationTrain.create_default())
    geometry = model.configure_lab_geometry(parse_lab_geometry(raw))
    try:
        supply = {segment.name: 0.01 for segment in model.pipe_segments}

        base = model._wall_deposit_candidates_by_segment_kg(
            species="SiO",
            rate_kg_hr=0.01,
            T_cond_C=900.0,
            melt_temperature_C=1700.0,
            supply_by_segment_kg=supply,
        )
        raw["surfaces"][0]["view_factor_from_melt"] = 0.1
        raw["surfaces"][1]["view_factor_from_melt"] = 0.9
        model.configure_lab_geometry(parse_lab_geometry(raw))
        flipped = model._wall_deposit_candidates_by_segment_kg(
            species="SiO",
            rate_kg_hr=0.01,
            T_cond_C=900.0,
            melt_temperature_C=1700.0,
            supply_by_segment_kg=supply,
        )

        assert base["holder"] > base["condenser"]
        assert flipped["condenser"] > flipped["holder"]

        raw["surfaces"][1]["line_of_sight_to_melt"] = False
        model.configure_lab_geometry(parse_lab_geometry(raw))
        blocked = model._wall_deposit_candidates_by_segment_kg(
            species="SiO",
            rate_kg_hr=0.01,
            T_cond_C=900.0,
            melt_temperature_C=1700.0,
            supply_by_segment_kg=supply,
        )
        assert blocked.get("condenser", 0.0) == pytest.approx(0.0)
    finally:
        unregister_wall_deposit_accounts(geometry.wall_deposit_accounts)


def test_lab_surface_deposit_accounts_conserve_and_roll_up_by_surface() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())
    ledger = AtomLedger(
        initial_balances={
            "process.overhead_gas": {
                "SiO": 0.0012,
                "Na": 0.00008,
            }
        }
    )
    before_kg = sum(ledger.total_kg_by_account().values())

    for account, species_kg in (
        (geometry.surfaces[0].wall_deposit_account, {"Na": 0.00002}),
        (geometry.surfaces[1].wall_deposit_account, {"SiO": 0.0002}),
        (geometry.surfaces[2].wall_deposit_account, {"SiO": 0.0007}),
        (geometry.surfaces[3].wall_deposit_account, {"SiO": 0.0003}),
        (geometry.surfaces[3].wall_deposit_account, {"Na": 0.00006}),
    ):
        transition = LedgerTransition.move(
            "toy_lab_surface_deposit",
            "process.overhead_gas",
            account,
            species_kg,
        )
        transition.validate_conservation()
        ledger.apply(transition)

    state = ledger.mol_by_account()
    after_kg = sum(ledger.total_kg_by_account().values())
    assert after_kg == pytest.approx(before_kg, abs=1.0e-15)
    assert ledger.total_kg_by_account("process.overhead_gas") == pytest.approx(0.0)

    report = _wall_deposit_report_kg(state)
    report_mol = _wall_deposit_mol_by_species(state)
    trace_surface_kg = wall_deposit_by_segment_species_kg(ledger)

    assert report["SiO"] == pytest.approx(0.0012)
    assert report["Na"] == pytest.approx(0.00008)
    assert report_mol["SiO"] > 0.0
    assert trace_surface_kg[("window", "SiO")] == pytest.approx(0.0002)
    assert trace_surface_kg[("condenser", "SiO")] == pytest.approx(0.0007)
    assert trace_surface_kg[("filter", "Na")] == pytest.approx(0.00006)
