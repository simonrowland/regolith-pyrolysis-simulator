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
)
from simulator.core import CondensationTrain, EvaporationFlux, MeltState
from simulator.condensation_routing import STAGE_KEY_BY_NUMBER
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


def test_missing_po2_refuses_instead_of_booking_disproportionation() -> None:
    with pytest.raises(WallSaturationPressureRefusal) as exc_info:
        _assign_declared_wall_capture(
            "SiO", 1.0, T_K=_TABLE_T_K, p_local_pa=1.0e6,
            pO2_Pa=None, oxygen_mol=1.0, vapor_pressure_data=None,
        )
    assert exc_info.value.reason == "wall_pO2_required"


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
