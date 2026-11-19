"""The wall-reaction ledger books the winning stoichiometry.

Expected products are the production resolver's, checked against the
design table for the named reactions. The writer is the existing
condensation-route provider. Atom closure is an independent formula sum.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import pytest

from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from simulator.accounting.formulas import (
    load_species_formulas,
    resolve_species_formula,
)
from simulator.accounting.ledger import AtomLedger
from simulator.account_ids import ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    WALL_DEPOSIT_ACCOUNT,
    CondensationModel,
    CondensationRouteResult,
    WallSaturationPressureRefusal,
    _assign_declared_wall_capture,
    _keep_declared_wall_deposit,
    _species_vapor_data,
    _segment_species_partial_pressures_pa,
    _molar_flow_partials_pa,
    _inlet_carrier_molar_flow_mol_s,
    _partial_pressure_closure_diagnostic,
)
from simulator.condensation_routing import (
    K_TRAP_STAGE_KEY,
    K_TRAP_STAGE_NUMBER,
    K_TRAP_SUB_ORDER,
    STAGE_KEY_BY_NUMBER,
    stage_index_for_visit_identity,
)
from simulator.core import (
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PyrolysisSimulator,
)
from simulator.state import MOLAR_MASS
from simulator.vapour_rail.nasa_cea import WallSaturationLawRefusal
from simulator.vapour_rail.wall_saturation import (
    ranked_wall_saturations,
    resolve_wall_saturation,
)
from tests.chemistry.test_wall_saturation_law import (
    _HOT_TRAIN,
    _MISSING_THERMO,
    _PO2_PA,
    _TABLE_T_K,
)

_REPO = Path(__file__).resolve().parents[2]
_CHROMIA = BuiltinCondensationRouteProvider.CHROMIUM_CONDENSED_ACCOUNT
_TRAIN = "process.condensation_train"
_OVERHEAD = "process.overhead_gas"

# Design section 3.4. Ratios are mol of product per mol of vapour.
_DESIGN_WINNERS = (
    ("SiO", _PO2_PA, {"SiO2": 1.0}, 0.5),
    ("SiO", 0.0, {"Si": 0.5, "SiO2": 0.5}, 0.0),
    ("CrO", _PO2_PA, {"Cr2O3": 0.5}, 0.25),
    ("CrO", 0.0, {"Cr": 1.0 / 3.0, "Cr2O3": 1.0 / 3.0}, 0.0),
    ("CrO2", _PO2_PA, {"Cr2O3": 0.5}, -0.25),
    ("MgO_gas", None, {"MgO": 1.0}, 0.0),
)

_STATIC_SP_DATA = {
    "SiO": {
        "condensation_products_mol_per_mol_vapor": {"Si": 0.5, "SiO2": 0.5},
    },
    "CrO2": {
        "condensation_products_mol_per_mol_vapor": {"Cr2O3": 0.5, "O2": 0.25},
        "condensation_product_accounts": {
            "Cr2O3": _CHROMIA,
            "O2": _OVERHEAD,
        },
    },
}


@pytest.fixture(scope="module")
def formula_registry() -> dict:
    return load_species_formulas(_REPO / "data" / "species_catalog.yaml")


def _product_map(result) -> dict[str, float]:
    return {species: float(mol) for species, mol in result.products_mol_per_mol_vapour}


def _winner(species: str, pO2_Pa: float | None):
    row = _species_vapor_data(species)
    return resolve_wall_saturation(
        row, species_id=species, T_K=_TABLE_T_K, pO2_Pa=pO2_Pa,
    )


def _atom_net(proposal, registry) -> dict[str, float]:
    net: dict[str, float] = defaultdict(float)
    for side, sign in ((proposal.debits, -1.0), (proposal.credits, +1.0)):
        for _account, species_mol in side.items():
            for name, mol in species_mol.items():
                formula = resolve_species_formula(name, registry)
                for element, atoms in formula.atom_moles(float(mol)).items():
                    net[element] += sign * float(atoms)
    return net


def _dispatch_booking(registry, species: str, booking: dict, condensed_kg: float, *,
                      wall_fraction: float = 0.0):
    provider = BuiltinCondensationRouteProvider()
    controls = {
        "species": species,
        "condensed_kg": condensed_kg,
        "sp_data": dict(_STATIC_SP_DATA.get(species) or {}),
        "wall_reaction_booking": booking,
        "dt_hr": 1.0,
    }
    if wall_fraction > 0.0:
        controls["wall_deposit_fraction"] = wall_fraction
        controls["wall_deposit_account_fractions"] = {WALL_DEPOSIT_ACCOUNT: 1.0}
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                _OVERHEAD: {species: 10.0, "O2": 10.0},
                _TRAIN: {},
                WALL_DEPOSIT_ACCOUNT: {},
                _CHROMIA: {},
            },
            species_formula_registry=registry,
        ),
        temperature_C=1226.85,
        pressure_bar=1e-6,
        control_inputs=controls,
    )
    result = provider.dispatch(request)
    assert result.status == "ok", result.diagnostic
    assert result.transition is not None
    return result.transition


def _book(registry, species: str, *, pO2_Pa: float | None, p_local_pa: float,
          captured_mol: float, oxygen_mol: float, surface: str = "baffle"):
    molar_mass = resolve_species_formula(species, registry).molar_mass_kg_per_mol()
    bookings: dict = {}
    kwargs = {
        "surface": surface,
        "T_K": _TABLE_T_K,
        "p_local_pa": p_local_pa,
        "pO2_Pa": pO2_Pa,
        "oxygen_mol": oxygen_mol,
        "vapor_pressure_data": None,
        "registry": registry,
        "bookings": bookings,
    }
    if surface == "wall":
        kwargs["account"] = WALL_DEPOSIT_ACCOUNT
    else:
        kwargs["stage_key"] = STAGE_KEY_BY_NUMBER[3]
    kept_kg, oxygen_left = _keep_declared_wall_deposit(
        species, captured_mol * molar_mass, **kwargs,
    )
    return bookings.get(species), kept_kg, oxygen_left, molar_mass


@pytest.mark.parametrize(
    ("species", "pO2_Pa", "products", "o2_per"),
    _DESIGN_WINNERS,
    ids=["SiO-ox", "SiO-disp", "CrO-ox", "CrO-disp", "CrO2-reductive", "MgO-congruent"],
)
def test_design_table_matches_the_production_resolver(
    formula_registry, species, pO2_Pa, products, o2_per,
) -> None:
    winner = _winner(species, pO2_Pa)
    assert math.isfinite(winner.pressure_Pa) and winner.pressure_Pa > 0.0
    assert _product_map(winner) == pytest.approx(products)
    assert winner.o2_mol_per_mol_vapour == pytest.approx(o2_per)
    oxygen = 10.0 if o2_per > 0.0 else 0.0
    booking, kept_kg, _oxygen_left, molar_mass = _book(
        formula_registry, species,
        pO2_Pa=pO2_Pa,
        p_local_pa=winner.pressure_Pa * 4.0,
        captured_mol=1.0,
        oxygen_mol=oxygen,
    )
    assert kept_kg == pytest.approx(molar_mass, rel=1e-12)
    proposal = _dispatch_booking(formula_registry, species, booking, kept_kg)
    assert proposal.debits[_OVERHEAD][species] == pytest.approx(1.0, rel=1e-12)
    for product, ratio in products.items():
        account = _CHROMIA if product == "Cr2O3" else _TRAIN
        assert proposal.credits[account][product] == pytest.approx(ratio)
        if product != "Cr2O3":
            assert product not in proposal.credits.get(_CHROMIA, {})
    if "Cr" in products and "Cr2O3" in products:
        assert proposal.credits[_TRAIN]["Cr"] == pytest.approx(products["Cr"])
        assert "Cr" not in proposal.credits.get(_CHROMIA, {})
    if o2_per > 0.0:
        assert proposal.debits[_OVERHEAD]["O2"] == pytest.approx(o2_per)
    elif o2_per < 0.0:
        assert proposal.credits[_OVERHEAD]["O2"] == pytest.approx(-o2_per)
        assert "O2" not in proposal.debits[_OVERHEAD]
    else:
        assert "O2" not in proposal.debits[_OVERHEAD]
        assert "O2" not in proposal.credits.get(_OVERHEAD, {})
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-9


@pytest.mark.parametrize("species", sorted(_HOT_TRAIN))
def test_winner_books_through_the_writer_with_atom_balance(
    formula_registry, species: str,
) -> None:
    if species in _MISSING_THERMO:
        with pytest.raises(WallSaturationPressureRefusal) as exc_info:
            _assign_declared_wall_capture(
                species, 1.0, T_K=_TABLE_T_K, p_local_pa=1.0,
                pO2_Pa=_PO2_PA, oxygen_mol=10.0, vapor_pressure_data=None,
            )
        assert exc_info.value.reason == "wall_condensation_thermo_missing"
        return

    winner = _winner(species, _PO2_PA)
    assert math.isfinite(winner.pressure_Pa) and winner.pressure_Pa > 0.0
    oxygen = 10.0 if winner.o2_mol_per_mol_vapour > 0.0 else 0.0
    booking, kept_kg, _oxygen_left, molar_mass = _book(
        formula_registry, species,
        pO2_Pa=_PO2_PA,
        p_local_pa=winner.pressure_Pa * 4.0,
        captured_mol=1.0,
        oxygen_mol=oxygen,
    )
    assert booking is not None
    assert kept_kg == pytest.approx(molar_mass, rel=1e-12)
    proposal = _dispatch_booking(formula_registry, species, booking, kept_kg)
    assert proposal.debits[_OVERHEAD][species] == pytest.approx(1.0, rel=1e-12)

    expected = _product_map(winner)
    credited = {
        name: mol
        for account, species_mol in proposal.credits.items()
        for name, mol in species_mol.items()
    }
    # One product species is credited once. A static map that also credits
    # O2 would double the CrO2 oxygen release.
    assert set(credited) == set(expected) | (
        {"O2"} if winner.o2_mol_per_mol_vapour < 0.0 else set()
    )
    for product, ratio in expected.items():
        assert credited[product] == pytest.approx(ratio, rel=1e-12, abs=1e-12)
        account = _CHROMIA if product == "Cr2O3" else _TRAIN
        assert proposal.credits[account][product] == pytest.approx(
            ratio, rel=1e-12, abs=1e-12,
        )
    if winner.o2_mol_per_mol_vapour > 0.0:
        assert proposal.debits[_OVERHEAD]["O2"] == pytest.approx(
            winner.o2_mol_per_mol_vapour, rel=1e-12,
        )
        assert "O2" not in proposal.credits.get(_OVERHEAD, {})
    elif winner.o2_mol_per_mol_vapour < 0.0:
        assert proposal.credits[_OVERHEAD]["O2"] == pytest.approx(
            -winner.o2_mol_per_mol_vapour, rel=1e-12,
        )
        assert "O2" not in proposal.debits[_OVERHEAD]
    else:
        assert "O2" not in proposal.debits[_OVERHEAD]
        assert "O2" not in proposal.credits.get(_OVERHEAD, {})
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-9, f"{species} {element} net {net}"
    for element, net in dict(proposal.atom_balance_proof).items():
        assert abs(net) < 1e-9


def test_static_sio_map_still_books_when_no_winner_is_passed(formula_registry) -> None:
    """No booking leaves the yaml disproportionation map in force."""

    molar_mass = resolve_species_formula("SiO", formula_registry).molar_mass_kg_per_mol()
    provider = BuiltinCondensationRouteProvider()
    result = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={_OVERHEAD: {"SiO": 10.0}, _TRAIN: {}},
            species_formula_registry=formula_registry,
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "SiO",
            "condensed_kg": molar_mass,
            "sp_data": _STATIC_SP_DATA["SiO"],
            "dt_hr": 1.0,
        },
    ))
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    assert proposal.credits[_TRAIN]["Si"] == pytest.approx(0.5)
    assert proposal.credits[_TRAIN]["SiO2"] == pytest.approx(0.5)
    assert "O2" not in proposal.debits[_OVERHEAD]
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-12


def test_wall_chromia_stays_on_the_wall_account(formula_registry) -> None:
    winner = _winner("CrO", _PO2_PA)
    booking, kept_kg, _oxygen_left, _molar_mass = _book(
        formula_registry, "CrO",
        pO2_Pa=_PO2_PA,
        p_local_pa=winner.pressure_Pa * 4.0,
        captured_mol=1.0,
        oxygen_mol=1.0,
        surface="wall",
    )
    proposal = _dispatch_booking(
        formula_registry, "CrO", booking, kept_kg, wall_fraction=1.0,
    )
    assert proposal.credits[WALL_DEPOSIT_ACCOUNT]["Cr2O3"] == pytest.approx(0.5)
    assert _CHROMIA not in proposal.credits
    assert proposal.debits[_OVERHEAD]["O2"] == pytest.approx(0.25)
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-12


def test_wall_sio_oxidative_overrides_the_static_split(formula_registry) -> None:
    winner = _winner("SiO", _PO2_PA)
    booking, kept_kg, _oxygen_left, _molar_mass = _book(
        formula_registry, "SiO",
        pO2_Pa=_PO2_PA,
        p_local_pa=winner.pressure_Pa * 4.0,
        captured_mol=1.0,
        oxygen_mol=1.0,
        surface="wall",
    )
    proposal = _dispatch_booking(
        formula_registry, "SiO", booking, kept_kg, wall_fraction=1.0,
    )
    assert proposal.credits[WALL_DEPOSIT_ACCOUNT]["SiO2"] == pytest.approx(1.0)
    assert "Si" not in proposal.credits.get(WALL_DEPOSIT_ACCOUNT, {})
    assert _TRAIN not in proposal.credits
    assert proposal.debits[_OVERHEAD]["O2"] == pytest.approx(0.5)
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-12


@pytest.mark.parametrize("species", sorted(_HOT_TRAIN - _MISSING_THERMO))
def test_undersaturated_capture_books_nothing(formula_registry, species: str) -> None:
    winner = _winner(species, _PO2_PA)
    booking, kept_kg, oxygen_left, _molar_mass = _book(
        formula_registry, species,
        pO2_Pa=_PO2_PA,
        p_local_pa=winner.pressure_Pa,
        captured_mol=1.0,
        oxygen_mol=10.0,
    )
    assert booking is None
    assert kept_kg == 0.0
    assert oxygen_left == pytest.approx(10.0)


def test_oxidant_limit_books_the_next_reaction_only_when_it_is_supersaturated(
    formula_registry,
) -> None:
    """SiO at 1500 K. Oxidative Psat is far below disproportionation.

    A short oxygen budget takes the oxidative leg first. The residual
    disproportionates only while that reaction's own Psat is below the
    local pressure. Otherwise the residual stays vapour.
    """

    row = _species_vapor_data("SiO")
    ladder = ranked_wall_saturations(
        row, species_id="SiO", T_K=_TABLE_T_K, pO2_Pa=_PO2_PA,
    )
    oxidative, disproportionation = ladder
    assert oxidative.o2_mol_per_mol_vapour == pytest.approx(0.5)
    assert _product_map(disproportionation) == pytest.approx({"Si": 0.5, "SiO2": 0.5})
    assert disproportionation.pressure_Pa > oxidative.pressure_Pa
    oxygen = 0.1
    p_both = disproportionation.pressure_Pa * 4.0
    p_oxidative_only = math.sqrt(
        oxidative.pressure_Pa * disproportionation.pressure_Pa,
    )
    assert oxidative.pressure_Pa < p_oxidative_only < disproportionation.pressure_Pa

    both, both_kg, both_oxygen, molar_mass = _book(
        formula_registry, "SiO",
        pO2_Pa=_PO2_PA, p_local_pa=p_both, captured_mol=1.0, oxygen_mol=oxygen,
    )
    assert both is not None
    assert both_oxygen >= 0.0
    assert both_oxygen <= oxygen
    # 0.1 mol O2 books 0.2 mol of the oxidative leg. The other 0.8 mol
    # disproportionates: 0.4 mol Si. The one-ULP oxidant rounding moves
    # that only in the last digits.
    assert both["baffle_products_mol"]["Si"] == pytest.approx(0.4, rel=1e-6)
    assert both["baffle_products_mol"]["SiO2"] > both["baffle_products_mol"]["Si"]
    assert both["baffle_oxygen_debit_mol"] <= oxygen
    assert both["baffle_oxygen_debit_mol"] == pytest.approx(oxygen, rel=1e-6)
    assert both_kg == pytest.approx(molar_mass, rel=1e-9)
    proposal = _dispatch_booking(formula_registry, "SiO", both, both_kg)
    assert proposal.debits[_OVERHEAD]["SiO"] == pytest.approx(1.0, rel=1e-12)
    assert proposal.credits[_TRAIN]["Si"] == pytest.approx(
        both["baffle_products_mol"]["Si"], rel=1e-12,
    )
    assert proposal.debits[_OVERHEAD]["O2"] == pytest.approx(
        both["baffle_oxygen_debit_mol"], rel=1e-12,
    )
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-9

    limited, limited_kg, limited_oxygen, _molar = _book(
        formula_registry, "SiO",
        pO2_Pa=_PO2_PA, p_local_pa=p_oxidative_only,
        captured_mol=1.0, oxygen_mol=oxygen,
    )
    assert limited is not None
    assert "Si" not in limited["baffle_products_mol"]
    assert limited["baffle_products_mol"]["SiO2"] == pytest.approx(
        limited["baffle_oxygen_debit_mol"] / 0.5, rel=1e-6,
    )
    assert limited_kg == pytest.approx(
        limited["baffle_products_mol"]["SiO2"] * molar_mass, rel=1e-6,
    )
    assert limited_kg < both_kg
    assert limited_oxygen >= 0.0
    proposal = _dispatch_booking(formula_registry, "SiO", limited, limited_kg)
    assert "Si" not in proposal.credits.get(_TRAIN, {})
    for element, net in _atom_net(proposal, formula_registry).items():
        assert abs(net) < 1e-9


def test_missing_po2_books_flagged_cold_sio_disproportionation(formula_registry) -> None:
    bookings = {}
    molar_mass = resolve_species_formula("SiO", formula_registry).molar_mass_kg_per_mol()
    kept, oxygen = _keep_declared_wall_deposit(
        "SiO", molar_mass, surface="baffle", T_K=313.15,
        p_local_pa=1.0, pO2_Pa=None, oxygen_mol=0.0,
        vapor_pressure_data=None, registry=formula_registry, bookings=bookings,
        stage_key=K_TRAP_STAGE_KEY,
    )
    assert kept == pytest.approx(molar_mass)
    assert oxygen == 0.0
    booking = bookings["SiO"]
    assert booking["baffle_products_mol"] == {"Si": 0.5, "SiO2": 0.5}
    leg = booking["reaction_legs"][0]
    assert leg["reaction_id"] == "disproportionation"
    assert leg["notices"] == ("wall_pO2_unknown_pO2_free_route_used",)
    proposal = _dispatch_booking(formula_registry, "SiO", booking, kept)
    assert all(abs(net) < 1e-9 for net in _atom_net(proposal, formula_registry).values())


def test_species_without_a_wall_block_is_unchanged(formula_registry) -> None:
    bookings: dict = {}
    kept, oxygen = _keep_declared_wall_deposit(
        "Mg", 0.25, surface="baffle", T_K=_TABLE_T_K, p_local_pa=10.0,
        pO2_Pa=None, oxygen_mol=1.0, vapor_pressure_data=None,
        registry=formula_registry, bookings=bookings,
        stage_key=STAGE_KEY_BY_NUMBER[1],
    )
    assert kept == 0.25
    assert oxygen == 1.0
    assert bookings == {}


@pytest.mark.usefixtures("production_configured_condensation_route")
def test_through_route_books_the_sio_winner(formula_registry) -> None:
    """A stage-3-through SiO parcel carries its evaporation oxygen.

    The route records the winner's products for whatever mass it keeps.
    Silicon in those products equals the kept vapour, and the oxygen
    difference is the booking's debit or credit.
    """

    model = CondensationModel(CondensationTrain.create_default())
    model.train.collection_route["stage3_route"] = "through"
    melt = MeltState()
    melt.temperature_C = 1600.0
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )
    kept_kg = route.condensed_for_species("SiO") + route.wall_deposit_by_species.get(
        "SiO", 0.0,
    )
    assert kept_kg > 0.0
    booking = route.wall_reaction_booking_by_species["SiO"]
    molar_mass = resolve_species_formula("SiO", formula_registry).molar_mass_kg_per_mol()
    vapour_mol = kept_kg / molar_mass
    products: dict[str, float] = defaultdict(float)
    for product, mol in booking["baffle_products_mol"].items():
        products[product] += float(mol)
    for _account, species_mol in booking["wall_products_mol_by_account"].items():
        for product, mol in species_mol.items():
            products[product] += float(mol)
    silicon = products.get("Si", 0.0) + products.get("SiO2", 0.0)
    assert silicon == pytest.approx(vapour_mol, rel=1e-9)
    oxygen_from_products = 2.0 * products.get("SiO2", 0.0)
    oxygen_from_vapour = vapour_mol
    oxygen_movement = (
        float(booking["baffle_oxygen_debit_mol"])
        + float(booking["wall_oxygen_debit_mol"])
        - float(booking["baffle_oxygen_credit_mol"])
        - float(booking["wall_oxygen_credit_mol"])
    )
    assert oxygen_from_products - oxygen_from_vapour == pytest.approx(
        2.0 * oxygen_movement, rel=1e-9, abs=1e-9,
    )
    assert route.silica_capture_kg() == pytest.approx(
        booking["baffle_products_mol"].get("SiO2", 0.0) * MOLAR_MASS["SiO2"] / 1000.0,
    )


def test_oxidative_silica_mass_is_the_booked_mole_not_the_static_half() -> None:
    vapour_kg = MOLAR_MASS["SiO"] / 1000.0
    static = CondensationRouteResult(condensed_by_stage_species={
        STAGE_KEY_BY_NUMBER[3]: {"SiO": vapour_kg},
    })
    oxidative = CondensationRouteResult(
        condensed_by_stage_species={
            STAGE_KEY_BY_NUMBER[3]: {"SiO": vapour_kg},
        },
        wall_reaction_booking_by_species={
            "SiO": {
                "baffle_products_mol": {"SiO2": 1.0},
                "baffle_products_mol_by_stage": {
                    STAGE_KEY_BY_NUMBER[3]: {"SiO2": 1.0},
                },
            },
        },
    )
    assert static.silica_capture_kg() == pytest.approx(
        0.5 * MOLAR_MASS["SiO2"] / 1000.0,
    )
    assert oxidative.silica_capture_kg() == pytest.approx(MOLAR_MASS["SiO2"] / 1000.0)
    assert oxidative.silica_capture_kg(3) == pytest.approx(oxidative.silica_capture_kg())
    assert oxidative.silica_capture_kg(2) == 0.0
    assert oxidative.silica_capture_kg() == pytest.approx(2.0 * static.silica_capture_kg())


def test_general_oxidant_limit_reaches_a_non_oxidative_sibling(formula_registry) -> None:
    """Every hot-train species whose lowest Psat consumes oxygen."""

    seen = 0
    for species in sorted(_HOT_TRAIN - _MISSING_THERMO):
        row = _species_vapor_data(species)
        try:
            ladder = ranked_wall_saturations(
                row, species_id=species, T_K=_TABLE_T_K, pO2_Pa=_PO2_PA,
            )
        except WallSaturationLawRefusal:
            continue
        winner = ladder[0]
        if winner.o2_mol_per_mol_vapour <= 0.0:
            continue
        later = next(
            (
                item for item in ladder[1:]
                if item.o2_mol_per_mol_vapour <= 0.0
                and math.isfinite(item.pressure_Pa)
                and item.pressure_Pa > winner.pressure_Pa
            ),
            None,
        )
        if later is None:
            continue
        seen += 1
        oxygen = winner.o2_mol_per_mol_vapour * 0.2
        p_both = max(item.pressure_Pa for item in ladder if math.isfinite(item.pressure_Pa)) * 4.0
        p_only = math.sqrt(winner.pressure_Pa * later.pressure_Pa)
        both, both_kg, oxygen_left, molar_mass = _book(
            formula_registry, species,
            pO2_Pa=_PO2_PA, p_local_pa=p_both, captured_mol=1.0, oxygen_mol=oxygen,
        )
        assert both is not None
        assert oxygen_left >= 0.0
        assert both["baffle_oxygen_debit_mol"] <= oxygen * (1.0 + 1e-9)
        # The residual is booked, so the kept mass is the whole capture.
        assert both_kg == pytest.approx(molar_mass, rel=1e-6)
        later_only = set(_product_map(later)) - set(_product_map(winner))
        assert later_only <= set(both["baffle_products_mol"])
        only, only_kg, only_oxygen, _molar_mass = _book(
            formula_registry, species,
            pO2_Pa=_PO2_PA, p_local_pa=p_only, captured_mol=1.0, oxygen_mol=oxygen,
        )
        assert only is not None
        assert only_oxygen >= 0.0
        assert only["baffle_oxygen_debit_mol"] <= oxygen * (1.0 + 1e-9)
        for product in later_only:
            assert product not in only["baffle_products_mol"]
        assert only_kg < 0.5 * molar_mass
        proposal = _dispatch_booking(formula_registry, species, only, only_kg)
        for element, net in _atom_net(proposal, formula_registry).items():
            assert abs(net) < 1e-9, f"{species} {element} net {net}"
    assert seen >= 2


def _bare_projector(formula_registry, ledger: AtomLedger):
    sim = object.__new__(PyrolysisSimulator)
    sim.atom_ledger = ledger
    sim.train = CondensationTrain.create_default()
    sim.species_formula_registry = formula_registry
    sim.vapor_pressures = {}
    sim._last_condensed_by_stage_species_delta = {}
    sim._last_impurity_delta = {}
    sim._stage_collection_kg_by_source = {}
    sim._LEDGER_KG_TOL = 1e-9
    return sim


def test_k_trap_swing_moves_booked_silica_not_static_silicon(formula_registry) -> None:
    """Oxidative SiO credits SiO2 only. The cartridge swing must not
    withdraw the static disproportionation's elemental Si.
    """
    ledger = AtomLedger()
    ledger.load_external_mol(
        _TRAIN,
        {"SiO2": 1.0},
        source="oxidative SiO booking",
        material_origin="feedstock",
    )
    silica_kg = ledger.kg_by_account(_TRAIN)["SiO2"]
    vapour_kg = MOLAR_MASS["SiO"] / 1000.0
    sim = _bare_projector(formula_registry, ledger)
    sim._last_condensation_baffle_product_kg_by_account = {
        _TRAIN: {"SiO2": silica_kg},
    }
    route = CondensationRouteResult(
        condensed_by_stage_species={K_TRAP_STAGE_KEY: {"SiO": vapour_kg}},
        capture_kg_by_stage_identity={
            (K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER): {"SiO": vapour_kg},
        },
        wall_reaction_booking_by_species={
            "SiO": {"baffle_products_mol": {"SiO2": 1.0}},
        },
    )
    projected = sim._project_routed_condensate(
        route, "SiO", vapour_kg,
        {"condensation_products_mol_per_mol_vapor": {"Si": 0.5, "SiO2": 0.5}},
    )
    assert projected == {"SiO2": pytest.approx(silica_kg)}
    assert ledger.kg_by_account(_TRAIN).get("Si", 0.0) == 0.0
    assert ledger.kg_by_account(_TRAIN).get("SiO2", 0.0) == pytest.approx(0.0)
    residue = ledger.kg_by_account(ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT)
    assert residue.get("SiO2", 0.0) == pytest.approx(silica_kg)
    trap_idx = stage_index_for_visit_identity(
        sim.train, K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER,
    )
    collected = sim.train.stages[trap_idx].collected_kg
    assert collected.get("SiO2", 0.0) == pytest.approx(silica_kg)
    assert collected.get("Si", 0.0) == 0.0


def test_booked_chromia_is_projected_without_a_train_swing(formula_registry) -> None:
    ledger = AtomLedger()
    ledger.load_external_mol(
        _CHROMIA,
        {"Cr2O3": 0.5},
        source="oxidative CrO booking",
        material_origin="feedstock",
    )
    chromia_kg = ledger.kg_by_account(_CHROMIA)["Cr2O3"]
    vapour_kg = resolve_species_formula(
        "CrO", formula_registry,
    ).molar_mass_kg_per_mol()
    sim = _bare_projector(formula_registry, ledger)
    sim._last_condensation_baffle_product_kg_by_account = {
        _CHROMIA: {"Cr2O3": chromia_kg},
    }
    route = CondensationRouteResult(
        condensed_by_stage_species={
            STAGE_KEY_BY_NUMBER[2]: {"CrO": vapour_kg},
        },
        capture_kg_by_stage_identity={(2, 0): {"CrO": vapour_kg}},
        wall_reaction_booking_by_species={
            "CrO": {"baffle_products_mol": {"Cr2O3": 0.5}},
        },
    )
    projected = sim._project_routed_condensate(
        route, "CrO", vapour_kg, {},
    )
    assert projected == {"Cr2O3": pytest.approx(chromia_kg)}
    assert ledger.kg_by_account(_TRAIN) == {}
    assert ledger.kg_by_account(_CHROMIA)["Cr2O3"] == pytest.approx(chromia_kg)
    stage_idx = stage_index_for_visit_identity(sim.train, 2, 0)
    assert sim.train.stages[stage_idx].collected_kg["Cr2O3"] == pytest.approx(
        chromia_kg,
    )
    assert sim._stage_collection_kg_by_source[
        (_CHROMIA, stage_idx, "Cr2O3")
    ] == pytest.approx(chromia_kg)


def test_empty_booking_does_not_fall_through_to_the_static_silicon_split(
    formula_registry,
) -> None:
    ledger = AtomLedger()
    sim = _bare_projector(formula_registry, ledger)
    sim._last_condensation_baffle_product_kg_by_account = {}
    vapour_kg = MOLAR_MASS["SiO"] / 1000.0
    route = CondensationRouteResult(
        condensed_by_stage_species={K_TRAP_STAGE_KEY: {"SiO": vapour_kg}},
        capture_kg_by_stage_identity={
            (K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER): {"SiO": vapour_kg},
        },
        wall_reaction_booking_by_species={"SiO": {}},
    )
    projected = sim._project_routed_condensate(
        route, "SiO", vapour_kg,
        {"condensation_products_mol_per_mol_vapor": {"Si": 0.5, "SiO2": 0.5}},
    )
    assert projected == {}
    assert ledger.kg_by_account() == {}
    assert sim.train.total_by_species() == {}


def test_stage_baffle_snapshot_keeps_train_and_chromia_solids(formula_registry) -> None:
    sim = object.__new__(PyrolysisSimulator)
    sim.species_formula_registry = formula_registry
    proposal = type("Proposal", (), {})()
    proposal.credits = {
        _TRAIN: {"SiO2": 1.0, "O2": 0.1},
        _CHROMIA: {"Cr2O3": 0.5},
        "process.wall_deposit": {"SiO2": 0.2},
        "process.overhead_gas": {"O2": 0.25},
        "process.condensation_retained_holdup": {"SiO": 0.01},
    }
    snapped = sim._stage_baffle_product_kg_from_credits(proposal)
    assert set(snapped) == {_TRAIN, _CHROMIA}
    assert set(snapped[_TRAIN]) == {"SiO2"}
    sio2 = resolve_species_formula("SiO2", formula_registry)
    chromia = resolve_species_formula("Cr2O3", formula_registry)
    assert snapped[_TRAIN]["SiO2"] == pytest.approx(sio2.molar_mass_kg_per_mol())
    assert snapped[_CHROMIA]["Cr2O3"] == pytest.approx(
        0.5 * chromia.molar_mass_kg_per_mol(),
    )


def test_supplied_oxygen_partial_survives_segment_depletion() -> None:
    partials = _segment_species_partial_pressures_pa(
        {"SiO": 20.0, "O2": 30.0}, 100.0,
        {"SiO": 1.0}, {"stage": {"SiO": 0.5}},
    )["stage"]
    # Unit inlet basis: .1 SiO + .3 O2 + .5 carrier = .9 total.
    assert partials == pytest.approx({"SiO": 100.0 / 9.0, "O2": 100.0 / 3.0})


@pytest.mark.usefixtures("production_configured_condensation_route")
def test_stage_wall_law_uses_supplied_stream_oxygen(formula_registry) -> None:
    model = CondensationModel(CondensationTrain.create_default())
    model.train.collection_route["stage3_route"] = "divert"
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0, pipe_diameter_m=0.12,
        gas_temperature_C=1600.0, carrier_gas="Kr",
        species_partial_pressures_mbar={"SiO": 1.0, "O2": 1.0},
    )
    # A finite, existing parcel budget; pO2 comes from the supplied composition.
    model.retained_vapor_mol["O2"] = 100.0
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0), MeltState(),
    )
    booking = route.wall_reaction_booking_by_species["SiO"]
    assert booking["reaction_legs"]
    assert any(leg["reaction_id"] == "oxidative" for leg in booking["reaction_legs"])
    assert all("wall_pO2_unknown_pO2_free_route_used" not in leg["notices"]
               for leg in booking["reaction_legs"])
    assert all("O2" in partials for partials in
               model.wall_species_partial_pressures_pa_by_segment.values())


@pytest.mark.usefixtures("production_configured_condensation_route")
@pytest.mark.parametrize("species", ["CrO", "CrO2", "CrO3"])
def test_flagged_chromium_carriers_capture_at_stage2_with_lineage(
    formula_registry, vapor_pressure_data, species,
) -> None:
    carrier = {
        "species_id": species,
        "pressure": {"kind": "value", "pa": 100.0},
        "flux": {"kind": "eligible", "alpha_ref": "test:alpha"},
        "is_flux_active": True,
        "validation_status": "modeled-PENDING",
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
    }
    model = CondensationModel(CondensationTrain.create_default(),
                              vapor_pressure_data=vapor_pressure_data)
    flux = EvaporationFlux(
        species_kg_hr={species: 1.0}, total_kg_hr=1.0,
        carrier_authority_by_species={species: carrier},
    )
    melt = MeltState()
    melt.temperature_C = 1600.0
    # The production fixture supplies the real transport/collector geometry.
    generated_oxygen = model.route(flux, melt)
    assert model.operating_history[-1]["mixture_input_path"] == (
        "committed_oxygen_with_transport_carrier"
    )
    for partials in model.wall_species_partial_pressures_pa_by_segment.values():
        assert math.fsum(partials.values()) == pytest.approx(
            model.overhead_pressure_mbar * 100.0, rel=1e-9,
        )
    if species == "CrO":
        assert generated_oxygen.condensed_by_stage_species[
            STAGE_KEY_BY_NUMBER[2]
        ][species] > 0.0
    model.configure_operating_conditions(
        species_partial_pressures_mbar={
            species: model.overhead_pressure_mbar * 0.1,
            "O2": model.overhead_pressure_mbar * 0.1,
        },
    )
    model.retained_vapor_mol["O2"] = 100.0
    route = model.route(flux, melt)
    capture = route.condensed_by_stage_species.get(STAGE_KEY_BY_NUMBER[2], {}).get(species, 0.0)
    assert capture > 0.0, str(route.condensation_refusals_by_species)
    authority = route.condensation_authority_by_species[species]
    assert authority["carrier_authority"] == carrier
    assert authority["authoritative_for_condensation"] is False
    assert authority["condensed_mass_kg_hr"] > 0.0
    assert route.carrier_authority_by_species[species] == carrier
    booking = route.wall_reaction_booking_by_species[species]
    legs = booking["reaction_legs"]
    assert any(leg["stage_key"] == STAGE_KEY_BY_NUMBER[2] for leg in legs)
    condensed = route.condensed_for_species(species)
    wall = route.wall_deposit_by_species.get(species, 0.0)
    proposal = _dispatch_booking(
        formula_registry, species, booking, condensed + wall,
        wall_fraction=wall / (condensed + wall),
    )
    assert all(abs(net) < 1e-9 for net in _atom_net(proposal, formula_registry).values())


@pytest.mark.parametrize("carrier", ["Kr", "Ar", "N2", "O2"])
def test_complete_dalton_mixture_closes_at_every_segment(carrier) -> None:
    flows = {"SiO": 2.0, "CrO": 1.0, "O2": 3.0}
    flows[carrier] = flows.get(carrier, 0.0) + 4.0
    partials = _molar_flow_partials_pa(flows, 900.0)
    assert math.fsum(partials.values()) == pytest.approx(900.0, rel=1e-9)
    assert partials["SiO"] / partials["CrO"] == pytest.approx(2.0)
    stages = _segment_species_partial_pressures_pa(
        partials, 900.0, {"SiO": 1.0, "CrO": 1.0},
        {"first": {"SiO": 0.5, "CrO": 0.8},
         "second": {"SiO": 0.1, "CrO": 0.1}},
    )
    for local in stages.values():
        assert math.fsum(local.values()) == pytest.approx(900.0, rel=1e-9)
        assert local["O2"] > 0.0


def test_flagged_cro_commits_condensate_with_upstream_flag_and_reaction(
    vapor_pressure_data, feedstocks_data, setpoints_data,
) -> None:
    from tests.chemistry.conftest import _build_sim
    sim = _build_sim(
        "lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data,
    )
    sim.melt.temperature_C = 1600.0
    carrier = {
        "species_id": "CrO",
        "pressure": {"kind": "value", "pa": 100.0},
        "flux": {"kind": "eligible", "alpha_ref": "test:alpha"},
        "is_flux_active": True,
        "validation_status": "modeled-PENDING",
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
    }
    flux = EvaporationFlux(
        species_kg_hr={"CrO": 0.001}, total_kg_hr=0.001,
        carrier_authority_by_species={"CrO": carrier},
    )
    sim._configure_condensation_operating_conditions(flux)
    route = sim.condensation_model.route(flux, sim.melt)
    assert route.condensed_for_species("CrO") + route.wall_deposit_by_species.get("CrO", 0.0) > 0.0
    outcome = sim._route_evaporated_species_to_condensation(
        route, "CrO", 0.001, apply_evaporative_redox_source_terms=False,
    )
    assert outcome["evaporation_transition"] is not None
    lots = [lot for transition in sim.atom_ledger.transitions
            for lot in transition.credits if "condensation_authority" in lot.meta]
    assert lots
    for lot in lots:
        assert lot.meta["condensation_authority"]["carrier_authority"] == carrier
        assert lot.meta["condensation_authority"]["authoritative_for_condensation"] is False
        assert lot.meta["wall_reaction_booking"]["reaction_legs"]


def test_complete_mixture_does_not_invent_roundoff_carrier_after_capture() -> None:
    partials = _molar_flow_partials_pa(
        {"Fe": 5.0, "Na": 3.14159, "Kr": 0.0, "O2": 0.0}, 900.0,
    )
    downstream = _segment_species_partial_pressures_pa(
        partials, 900.0, {"Fe": 1.0, "Na": 1.0},
        {"cold": {"Fe": 1e-10, "Na": 1e-10}},
    )["cold"]
    # Uniform removal changes flow, not mole fraction. An inlet rounding
    # residual cannot become a physical carrier when vapours disappear.
    assert downstream == pytest.approx(partials, rel=1e-9, abs=1e-12)
    assert math.fsum(downstream.values()) == pytest.approx(900.0, rel=1e-9)

@pytest.mark.usefixtures("production_configured_condensation_route")
def test_wall_capture_spends_only_committed_oxygen(formula_registry) -> None:
    model = CondensationModel(CondensationTrain.create_default())
    model.train.collection_route["stage3_route"] = "divert"
    model.retained_vapor_mol["O2"] = 0.01
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1600.0),
    )
    booking = route.wall_reaction_booking_by_species["SiO"]
    debit = booking["baffle_oxygen_debit_mol"] + booking["wall_oxygen_debit_mol"]
    credit = booking["baffle_oxygen_credit_mol"] + booking["wall_oxygen_credit_mol"]
    assert debit <= 0.01 + credit
    assert any(leg["reaction_id"] == "disproportionation"
               for leg in booking["reaction_legs"])
    assert any(leg["reaction_id"] == "oxidative"
               for leg in booking["reaction_legs"])


def test_zero_flow_closure_is_not_applicable() -> None:
    for partials in ({}, {"O2": 0.0, "Kr": 0.0}):
        assert _partial_pressure_closure_diagnostic(partials, 900.0) == {
            "status": "not_applicable", "reason": "no_advective_parcel",
            "relative_error": None,
        }


def test_uncommitted_reductive_credit_is_not_lent_to_next_capture() -> None:
    legs, oxygen_left, unbooked = _assign_declared_wall_capture(
        "CrO2", 1.0, T_K=_TABLE_T_K, p_local_pa=1.0e6,
        pO2_Pa=50.0, oxygen_mol=0.0, vapor_pressure_data=None,
    )
    assert unbooked == 0.0
    assert sum(leg["oxygen_credit_mol"] for leg in legs) == pytest.approx(0.25)
    assert oxygen_left == 0.0


def test_transport_does_not_promote_complete_map_roundoff_to_carrier() -> None:
    partials = _molar_flow_partials_pa({"Fe": 5.0, "Na": 3.14159}, 900.0)
    carrier, _ = _inlet_carrier_molar_flow_mol_s(
        {"Fe": 1.0, "Na": 1.0}, partials, 900.0, vapor_pressure_data=None,
    )
    assert carrier == 0.0


@pytest.mark.usefixtures("production_configured_condensation_route")
def test_zero_flow_route_counts_na_closure_without_deposit() -> None:
    model = CondensationModel(CondensationTrain.create_default())
    result = model.route(EvaporationFlux(), MeltState(temperature_C=1600.0))
    assert result.wall_deposit_by_species == {}
    assert result.condensed_by_stage_species == {}
    stages = model.last_stage_bulk_gas
    assert stages
    history = model.operating_history[-1]
    walls = history["segment_partial_pressure_closure"]
    for closure in [*walls.values(), *(row["partial_pressure_closure"] for row in stages)]:
        assert closure == {
            "status": "not_applicable", "reason": "no_advective_parcel",
            "relative_error": None,
        }
    assert history["no_advective_parcel_count"] == len(walls) + len(stages)
