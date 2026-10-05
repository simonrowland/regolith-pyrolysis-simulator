"""t1139 full reactant vector: every condensed reactant, signed O2."""

from __future__ import annotations

import math
from collections import defaultdict
from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from simulator.accounting import AccountingError
from simulator.accounting.formulas import parse_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.evaporation import EvaporationMixin
from simulator.vapour_rail.stoich import (
    oxygen_coproduct_account,
    oxygen_fugacity_plane,
)


def _kg_per_kg_vapor(formula: str, nu: float, vapor: str, vapor_nu: float = 1.0) -> float:
    """kg of ``formula`` per kg of vapor from stoichiometric coefficients."""

    return (
        float(nu) * parse_formula(formula).molar_mass_kg_per_mol()
        / (float(vapor_nu) * parse_formula(vapor).molar_mass_kg_per_mol())
    )


def _atoms_for_kg(formula: str, kg: float) -> dict[str, float]:
    if kg == 0.0:
        return {}
    parsed = parse_formula(formula)
    return parsed.atom_moles(abs(kg) / parsed.molar_mass_kg_per_mol())


def _reaction(reaction_id: str, reactants: list[tuple[str, float]], products: list[tuple[str, float]]) -> dict:
    return {
        "id": reaction_id,
        "reactants": [
            {"formula": formula, "stoichiometry": nu} for formula, nu in reactants
        ],
        "products": [
            {"formula": formula, "stoichiometry": nu} for formula, nu in products
        ],
    }


def _species_data(species: str, parent: str, reaction: dict, **extra) -> dict:
    data = {
        "parent_oxide": parent,
        "formula": species,
        "source_reactions": [reaction],
    }
    data.update(extra)
    return data


NABO = _reaction(
    "nabo",
    [("Na2O", 0.5), ("B2O3", 0.5)],
    [("NaBO", 1.0), ("O2", 0.5)],
)
NAK = _reaction(
    "nak",
    [("Na2O", 0.5), ("K2O", 0.5)],
    [("NaK", 1.0), ("O2", 0.5)],
)
NABO2 = _reaction(
    "nabo2",
    [("Na2O", 0.5), ("B2O3", 0.5)],
    [("NaBO2", 1.0)],
)
NABO3 = _reaction(
    "nabo3",
    [("Na2O", 0.5), ("B2O3", 0.5), ("O2", 0.5)],
    [("NaBO3", 1.0)],
)


def _host(vapor_pressures: dict | None = None) -> SimpleNamespace:
    host = SimpleNamespace(
        vapor_pressures=vapor_pressures or {"metals": {}, "oxide_vapors": {}},
        species_formula_registry={},
        atom_ledger=None,
    )
    for name in (
        "_analytic_evaporation_depletion_rates",
        "_limit_multi_reactant_rates",
        "_evaporation_stoich",
        "_multi_reactant_stoich",
        "_validate_multi_reactant_atoms",
        "_validate_evaporation_stoich_atoms",
        "_atom_moles_for_kg",
        "_evaporation_reactant_stock_kg",
    ):
        method = getattr(EvaporationMixin, name)
        setattr(host, name, method.__get__(host, SimpleNamespace))
    return host


def _catalog() -> dict:
    return {
        "metals": {
            "NaK": _species_data("NaK", "Na2O", NAK),
        },
        "oxide_vapors": {
            "NaBO": _species_data("NaBO", "Na2O", NABO),
            "NaBO2": _species_data("NaBO2", "Na2O", NABO2),
            "NaBO3": _species_data("NaBO3", "Na2O", NABO3),
        },
    }


def _assert_vector_matches_coefficients(stoich: dict, vapor: str, reactants: list[tuple[str, float]], o2_nu: float) -> None:
    vector = stoich["reactants_kg_per_vapor"]
    assert set(vector) == {formula for formula, _nu in reactants}
    for formula, nu in reactants:
        assert vector[formula] == pytest.approx(
            _kg_per_kg_vapor(formula, nu, vapor), rel=1e-12
        )
    o2 = _kg_per_kg_vapor("O2", o2_nu, vapor) if o2_nu else 0.0
    assert stoich["O2_per_product_kg"] == pytest.approx(o2, rel=1e-12, abs=1e-15)
    assert stoich["oxide_per_product_kg"] == pytest.approx(
        sum(vector.values()), rel=1e-12
    )
    assert stoich["oxide_per_product_kg"] == pytest.approx(
        1.0 + stoich["O2_per_product_kg"], rel=1e-12, abs=1e-15
    )
    debit: dict[str, float] = defaultdict(float)
    credit: dict[str, float] = defaultdict(float)
    for formula, kg in vector.items():
        for element, moles in _atoms_for_kg(formula, kg).items():
            debit[element] += moles
    for element, moles in _atoms_for_kg(vapor, 1.0).items():
        credit[element] += moles
    if o2 >= 0.0:
        for element, moles in _atoms_for_kg("O2", o2).items():
            credit[element] += moles
    else:
        for element, moles in _atoms_for_kg("O2", o2).items():
            debit[element] += moles
    for element in set(debit) | set(credit):
        assert debit[element] == pytest.approx(credit[element], rel=1e-9, abs=1e-12)


def _proposal_sides(proposal) -> tuple[dict[str, float], dict[str, float], float, float]:
    debit: dict[str, float] = defaultdict(float)
    credit: dict[str, float] = defaultdict(float)
    debit_kg = 0.0
    credit_kg = 0.0
    for species_mol in proposal.debits.values():
        for species, mol in species_mol.items():
            parsed = parse_formula(species)
            kg = float(mol) * parsed.molar_mass_kg_per_mol()
            debit_kg += kg
            for element, moles in parsed.atom_moles(float(mol)).items():
                debit[element] += moles
    for species_mol in proposal.credits.values():
        for species, mol in species_mol.items():
            parsed = parse_formula(species)
            kg = float(mol) * parsed.molar_mass_kg_per_mol()
            credit_kg += kg
            for element, moles in parsed.atom_moles(float(mol)).items():
                credit[element] += moles
    return debit, credit, debit_kg, credit_kg


def _assert_proposal_closes(proposal) -> None:
    debit, credit, debit_kg, credit_kg = _proposal_sides(proposal)
    for element in set(debit) | set(credit):
        assert debit[element] == pytest.approx(credit[element], rel=1e-9, abs=1e-12)
    assert debit_kg == pytest.approx(credit_kg, rel=1e-9, abs=1e-12)
    for element, net in dict(proposal.atom_balance_proof).items():
        assert net == pytest.approx(0.0, abs=1e-9)


def _dispatch(species: str, stoich: dict, sp_data: dict | None = None, *, rate: float = 1.0, remaining: float | None = None, available: float = 10.0):
    if remaining is None:
        remaining = rate
    provider = BuiltinEvaporationTransitionProvider()
    return provider.dispatch(IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1600.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {},
                "process.overhead_gas": {"O2": 10.0},
                "process.condensation_train": {},
                "reservoir.fo2_buffer": {},
            },
            species_formula_registry={},
        ),
        control_inputs={
            "species": species,
            "stoich": dict(stoich),
            "sp_data": dict(sp_data or {}),
            "rate_kg_hr": rate,
            "remaining_kg_hr": remaining,
            "dt_hr": 1.0,
            "available_kg": available,
        },
    ))


def _first_order_rate(raw_kg_hr: float, total_draw_kg_hr: float, available_kg: float, dt_hr: float = 1.0) -> float:
    """Product kg/hr when one reactant pool is shared first-order."""

    if available_kg <= 1e-12 or total_draw_kg_hr <= 1e-12:
        return 0.0
    fraction = -math.expm1(-(total_draw_kg_hr / available_kg) * dt_hr)
    fraction = max(0.0, min(math.nextafter(1.0, 0.0), fraction))
    return raw_kg_hr * (available_kg * fraction) / total_draw_kg_hr


def test_oxide_vapour_vector_closes_and_credits_overhead_oxygen() -> None:
    host = _host(_catalog())
    stoich = host._evaporation_stoich("NaBO", host.vapor_pressures["oxide_vapors"]["NaBO"])
    _assert_vector_matches_coefficients(
        stoich, "NaBO", [("Na2O", 0.5), ("B2O3", 0.5)], 0.5
    )
    result = _dispatch("NaBO", stoich)
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    assert set(proposal.debits["process.cleaned_melt"]) == {"Na2O", "B2O3"}
    assert set(proposal.credits) == {"process.overhead_gas"}
    assert "O2" in proposal.credits["process.overhead_gas"]
    assert "NaBO" in proposal.credits["process.overhead_gas"]
    _assert_proposal_closes(proposal)
    assert oxygen_coproduct_account(None, vapor_oxygen_atoms=1.0) == (
        "process.overhead_gas"
    )
    assert oxygen_fugacity_plane(vapor_oxygen_atoms=1.0) == "transport_headspace"


def test_metal_vapour_vector_credits_fo2_buffer() -> None:
    host = _host(_catalog())
    stoich = host._evaporation_stoich("NaK", host.vapor_pressures["metals"]["NaK"])
    _assert_vector_matches_coefficients(
        stoich, "NaK", [("Na2O", 0.5), ("K2O", 0.5)], 0.5
    )
    result = _dispatch("NaK", stoich)
    proposal = result.transition
    assert proposal is not None
    assert set(proposal.debits["process.cleaned_melt"]) == {"Na2O", "K2O"}
    assert proposal.credits["reservoir.fo2_buffer"]["O2"] > 0.0
    assert "O2" not in proposal.credits["process.overhead_gas"]
    assert "NaK" in proposal.credits["process.overhead_gas"]
    _assert_proposal_closes(proposal)
    assert oxygen_coproduct_account(None, vapor_oxygen_atoms=0.0) == (
        "reservoir.fo2_buffer"
    )
    assert oxygen_fugacity_plane(vapor_oxygen_atoms=0.0) == "intrinsic_melt"


def test_zero_oxygen_metaborate_debits_both_parents() -> None:
    host = _host(_catalog())
    stoich = host._evaporation_stoich(
        "NaBO2", host.vapor_pressures["oxide_vapors"]["NaBO2"]
    )
    _assert_vector_matches_coefficients(
        stoich, "NaBO2", [("Na2O", 0.5), ("B2O3", 0.5)], 0.0
    )
    result = _dispatch("NaBO2", stoich)
    proposal = result.transition
    assert proposal is not None
    assert set(proposal.debits["process.cleaned_melt"]) == {"Na2O", "B2O3"}
    assert "O2" not in proposal.credits.get("process.overhead_gas", {})
    assert "reservoir.fo2_buffer" not in proposal.credits
    assert "O2" not in proposal.debits.get("process.overhead_gas", {})
    _assert_proposal_closes(proposal)


def test_consumed_oxygen_stays_an_overhead_debit() -> None:
    host = _host(_catalog())
    stoich = host._evaporation_stoich(
        "NaBO3", host.vapor_pressures["oxide_vapors"]["NaBO3"]
    )
    _assert_vector_matches_coefficients(
        stoich, "NaBO3", [("Na2O", 0.5), ("B2O3", 0.5)], -0.5
    )
    result = _dispatch("NaBO3", stoich)
    proposal = result.transition
    assert proposal is not None
    assert proposal.debits["process.overhead_gas"]["O2"] > 0.0
    assert "reservoir.fo2_buffer" not in proposal.credits
    assert "reservoir.fo2_buffer" not in proposal.debits
    _assert_proposal_closes(proposal)


def test_single_condensed_reactant_does_not_attach_a_vector() -> None:
    oxide = _kg_per_kg_vapor("Na2O", 1.0, "Na", 2.0)
    o2 = _kg_per_kg_vapor("O2", 0.5, "Na", 2.0)
    host = _host()
    balanced = host._evaporation_stoich("Na", {
        "parent_oxide": "Na2O",
        "formula": "Na",
        "stoich_oxide_per_vapor": oxide,
        "stoich_O2_per_vapor": o2,
    })
    assert "reactants_kg_per_vapor" not in balanced
    unbalanced = host._evaporation_stoich("Na", {
        "parent_oxide": "Na2O",
        "formula": "Na",
        "stoich_oxide_per_vapor": oxide,
        "stoich_O2_per_vapor": o2,
        "source_reactions": [_reaction(
            "unbalanced",
            [("Na2O", 5.0)],
            [("Na", 1.0)],
        )],
    })
    assert "reactants_kg_per_vapor" not in unbalanced
    assert unbalanced["oxide_per_product_kg"] == pytest.approx(oxide)
    result = _dispatch("Na", balanced)
    proposal = result.transition
    assert proposal is not None
    assert set(proposal.debits["process.cleaned_melt"]) == {"Na2O"}
    assert proposal.credits["reservoir.fo2_buffer"]["O2"] > 0.0
    assert "O2" not in proposal.credits["process.overhead_gas"]
    _assert_proposal_closes(proposal)
    declared = _dispatch("Na", balanced, {"oxygen_destination": "process.overhead_gas"})
    declared_proposal = declared.transition
    assert declared_proposal is not None
    assert "reservoir.fo2_buffer" not in declared_proposal.credits
    assert declared_proposal.credits["process.overhead_gas"]["O2"] > 0.0
    _assert_proposal_closes(declared_proposal)


def test_declared_vector_scalars_must_match() -> None:
    host = _host(_catalog())
    sp = dict(host.vapor_pressures["oxide_vapors"]["NaBO"])
    sp["stoich_oxide_per_vapor"] = 1.0
    sp["stoich_O2_per_vapor"] = 0.0
    with pytest.raises(AccountingError, match="compiled reactant vector"):
        host._evaporation_stoich("NaBO", sp)


def test_depletion_uses_every_reactant_and_one_shared_pool() -> None:
    host = _host(_catalog())
    nabo = host._evaporation_stoich("NaBO", host.vapor_pressures["oxide_vapors"]["NaBO"])
    nak = host._evaporation_stoich("NaK", host.vapor_pressures["metals"]["NaK"])
    abundant = host._analytic_evaporation_depletion_rates(
        {"NaBO": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"Na2O": 1.0e6, "B2O3": 1.0e6},
        available_o2_kg=0.0,
    )
    assert abundant["NaBO"] == pytest.approx(1.0, rel=1e-6)
    missing = host._analytic_evaporation_depletion_rates(
        {"NaBO": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"Na2O": 1.0e6},
        available_o2_kg=1.0e6,
    )
    assert "NaBO" not in missing
    sodium = 100.0
    shared = host._analytic_evaporation_depletion_rates(
        {"NaBO": 1.0, "NaK": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"Na2O": sodium, "B2O3": 1.0e6, "K2O": 1.0e6},
        available_o2_kg=1.0e6,
    )
    sodium_draw = nabo["reactants_kg_per_vapor"]["Na2O"] + nak["reactants_kg_per_vapor"]["Na2O"]
    expected = _first_order_rate(1.0, sodium_draw, sodium)
    assert shared["NaBO"] == pytest.approx(expected, rel=1e-12)
    assert shared["NaK"] == pytest.approx(expected, rel=1e-12)
    own = _first_order_rate(1.0, nabo["reactants_kg_per_vapor"]["Na2O"], sodium)
    assert expected < own
    no_o2 = host._analytic_evaporation_depletion_rates(
        {"NaBO3": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"Na2O": 1.0e6, "B2O3": 1.0e6},
        available_o2_kg=0.0,
    )
    assert "NaBO3" not in no_o2


def test_stock_is_the_scarcest_condensed_reactant() -> None:
    host = _host(_catalog())
    stoich = host._evaporation_stoich("NaBO", host.vapor_pressures["oxide_vapors"]["NaBO"])
    host.atom_ledger = SimpleNamespace(
        kg_by_account=lambda account: {"Na2O": 4.0, "B2O3": 0.25} if account == "process.cleaned_melt" else {}
    )
    assert host._evaporation_reactant_stock_kg(stoich) == pytest.approx(0.25)
    host.atom_ledger = SimpleNamespace(
        kg_by_account=lambda account: {"Na2O": 4.0} if account == "process.cleaned_melt" else {}
    )
    assert host._evaporation_reactant_stock_kg(stoich) == pytest.approx(0.0)


def test_subfloor_second_reactant_skips_the_transition() -> None:
    stoich = {
        "parent_oxide": "Na2O",
        "oxide_per_product_kg": 1.0 + 1.0e-13,
        "O2_per_product_kg": 0.0,
        "reactants_kg_per_vapor": {"Na2O": 1.0, "B2O3": 1.0e-13},
    }
    result = _dispatch("NaBO2", stoich, available=10.0)
    assert result.transition is None
    assert result.diagnostic["reason_skipped"] == (
        "coupled stoichiometric leg below numerical floor"
    )
    assert "reactant:B2O3" in result.diagnostic["subfloor_legs_kg"]
    assert "reactant:Na2O" not in result.diagnostic["subfloor_legs_kg"]
