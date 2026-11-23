"""d-468: the evaporation law is symmetric through saturation.

Positive flux leaves the melt. Negative flux is vapour returning to
the melt. The reduced residual uses that signed rate. A ``max(0, .)``
clip on the series flux or on ``_HeadspaceLaw._flux_kg_hr`` makes the
one-sided derivatives disagree and the supersaturated side nonnegative,
so this module fails.
"""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import (
    _series_resistance_evaporation_flux_kg_m2_s,
)
from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from simulator.accounting.formulas import resolve_species_formula
from simulator.capacity_coupling import (
    _build_law,
    _manifold_rates,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.evaporation import _stoich_o2_is_overhead
from simulator.state import MOLAR_MASS
from simulator.thermal_train import NoColdTrain
from tests.chemistry.conftest import _build_sim, _load_yaml


_P_EQ_PA = 80.0
_T_K = 1800.0
_ALPHA = 0.13
_AREA_M2 = 1.0
_PIPE_D_M = 0.12
_PIPE_L_M = 1.0
_VOLUME_M3 = 0.085
_P_TOT_PA = 200.0
_DOWNSTREAM_PA = 20.0


def _series(p_bulk_pa: float, *, gas_resistance: bool):
    return _series_resistance_evaporation_flux_kg_m2_s(
        species="K",
        P_eq_pa=_P_EQ_PA,
        P_bulk_pa=p_bulk_pa,
        T_surface_K=_T_K,
        molar_mass_kg_mol=MOLAR_MASS["K"] / 1000.0,
        alpha_i=_ALPHA,
        knudsen_number=1.0,
        pipe_diameter_m=_PIPE_D_M,
        overhead_pressure_pa=1000.0,
        gas_resistance_enabled=gas_resistance,
        melt_resistance_enabled=False,
    )


def test_dilute_flux_matches_the_coefficient_owner_and_its_mirror():
    """Dilute J is alpha * k_HK * (P_eq - P_bulk), to round-off.

    The coefficient is the Hertz-Knudsen owner, not a second sqrt.
    The mirror across P_eq is the same magnitude with the opposite sign.
    Gas resistance is off so R is the interface term alone.
    """

    dilute = _series(_P_EQ_PA - 25.0, gas_resistance=False)
    rich = _series(_P_EQ_PA + 25.0, gas_resistance=False)
    k_hk = hertz_knudsen_k_kg_s_m2_pa(_T_K, MOLAR_MASS["K"] / 1000.0)
    expected = _ALPHA * 25.0 * k_hk
    assert dilute.flux_kg_s_m2 == pytest.approx(expected, rel=1e-12, abs=0.0)
    assert rich.flux_kg_s_m2 == pytest.approx(-expected, rel=1e-12, abs=0.0)
    assert rich.flux_kg_s_m2 == pytest.approx(
        -dilute.flux_kg_s_m2, rel=0.0, abs=0.0,
    )


def _law():
    masses = {
        "K": MOLAR_MASS["K"] / 1000.0,
        "N2": MOLAR_MASS["N2"] / 1000.0,
    }

    def flux(partials):
        series = _series(float(partials.get("K", 0.0)), gas_resistance=False)
        return {
            "K": series.flux_kg_s_m2 * _AREA_M2 * 3600.0,
            "N2": 0.0,
        }

    opening = {"K": 1.0e-6, "N2": 1.0e-4}
    built = _build_law(
        pre_holdup_mol=opening,
        molar_mass_kg_mol=masses,
        flux_kg_hr_at_partials=flux,
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        temperature_K=_T_K,
        volume_m3=_VOLUME_M3,
        dt_hr=1.0,
        downstream_pressure_Pa=_DOWNSTREAM_PA,
        pipe_diameter_m=_PIPE_D_M,
        pipe_length_m=_PIPE_L_M,
        outflow_cap_kg_s=None,
        redox_source_mol_s=None,
    )
    assert not hasattr(built, "reason")
    law, _opening = built
    return law


def _dn_k(law, mole_fraction: float) -> tuple[float, float]:
    """Reduced residual of K, and K's inflow, at one total pressure.

    ``dn_i = s_i - y_i * sum(s)`` is the reduced evaluator's residual.
    ``s`` is the production ``_manifold_rates`` inflow, which reads
    ``_flux_kg_hr``. A clip in that reader or in the series law changes
    these numbers.
    """

    fraction = float(mole_fraction)
    inflow, _redox, _gross = _manifold_rates(
        law,
        {"K": fraction, "N2": 1.0 - fraction},
        _P_TOT_PA,
        counted=False,
    )
    q_sum = math.fsum(inflow[name] for name in inflow)
    return inflow["K"] - fraction * q_sum, inflow["K"]


def test_reduced_rhs_is_c1_across_saturation():
    """One-sided derivatives of the reduced residual agree at p_b = p_eq.

    The step is a mole fraction. Partial pressure is that fraction times
    the fixed total, so the samples sit on either side of P_eq. A
    reinstated ``max(0, P_eq - P_bulk)`` makes the high-side inflow
    nonnegative and the right-hand derivative miss the left-hand one.
    """

    law = _law()
    y_star = _P_EQ_PA / _P_TOT_PA
    step = 1.0e-4
    left_dn, _left_in = _dn_k(law, y_star)
    low_dn, low_in = _dn_k(law, y_star - step)
    high_dn, high_in = _dn_k(law, y_star + step)
    left = (left_dn - low_dn) / step
    right = (high_dn - left_dn) / step
    assert low_in > 0.0
    assert high_in < 0.0
    assert right == pytest.approx(left, rel=1.0e-2, abs=1.0e-6)


def _species_row(sim, species: str) -> dict:
    metals = sim.vapor_pressures.get("metals", {}) or {}
    oxides = sim.vapor_pressures.get("oxide_vapors", {}) or {}
    row = metals.get(species) or oxides.get(species)
    assert isinstance(row, dict) and row, species
    return row


def _held(ledger, account: str, species: str) -> float:
    held = ledger.kg_by_account(account)
    return float(held.get(species, 0.0) or 0.0)


def _lot_kg(transition, side: str, account: str, species: str) -> float:
    total = 0.0
    for lot in getattr(transition, side):
        if getattr(lot, "account", None) != account:
            continue
        total += float(getattr(lot, "species_kg", {}).get(species, 0.0) or 0.0)
    return total


def _recondense(sim, species: str) -> None:
    row = _species_row(sim, species)
    stoich = sim._evaporation_stoich(species, row)
    parent = str(stoich["parent_oxide"])
    oxide_per = float(stoich["oxide_per_product_kg"])
    o2_per = float(stoich["O2_per_product_kg"])
    registry = sim.species_formula_registry
    formula = resolve_species_formula(species, registry)
    o2_formula = resolve_species_formula("O2", registry)
    vapour_mol = 1.0
    o2_mol = 1.0
    ledger = sim.atom_ledger
    ledger.load_external_mol(
        "process.overhead_gas",
        {species: vapour_mol, "O2": o2_mol},
        material_origin="feedstock",
    )
    ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": o2_mol},
        material_origin="feedstock",
    )
    rate = -0.01
    magnitude = -rate
    before_parent = _held(ledger, "process.cleaned_melt", parent)
    before_vapour = _held(ledger, "process.overhead_gas", species)
    before_overhead_o2 = _held(ledger, "process.overhead_gas", "O2")
    before_buffer_o2 = _held(ledger, "reservoir.fo2_buffer", "O2")
    _credited, transition = sim._credit_evaporation_transition(
        species,
        rate,
        rate,
        row,
        apply_evaporative_redox_source_terms=False,
        return_transition=True,
        post_subfloor_coupled_legs=True,
    )
    assert transition is not None
    assert str(transition.reason).startswith(f"recondense_{species}")
    assert _lot_kg(transition, "credits", "process.cleaned_melt", parent) > 0.0
    assert _lot_kg(transition, "debits", "process.cleaned_melt", parent) == 0.0
    assert _lot_kg(transition, "debits", "process.overhead_gas", species) > 0.0
    assert _lot_kg(transition, "credits", "process.overhead_gas", species) == 0.0
    for lot in tuple(transition.debits) + tuple(transition.credits):
        for kilograms in lot.species_kg.values():
            assert float(kilograms) > 0.0
    assert _held(ledger, "process.cleaned_melt", parent) == pytest.approx(
        before_parent + magnitude * oxide_per, rel=1e-9, abs=1e-12,
    )
    assert _held(ledger, "process.overhead_gas", species) == pytest.approx(
        before_vapour - magnitude, rel=1e-9, abs=1e-12,
    )
    destination = str(row.get("oxygen_destination") or "")
    vapour_oxygen = float(formula.elements.get("O", 0.0) or 0.0)
    probe = o2_per if o2_per > 0.0 else 0.0
    forward_overhead = (
        o2_per > 0.0
        and _stoich_o2_is_overhead(probe, destination, vapour_oxygen)
    )
    expected_o2 = -magnitude * o2_per
    if o2_per == 0.0:
        assert _held(ledger, "process.overhead_gas", "O2") == pytest.approx(
            before_overhead_o2, abs=1e-12,
        )
        assert _held(ledger, "reservoir.fo2_buffer", "O2") == pytest.approx(
            before_buffer_o2, abs=1e-12,
        )
    elif forward_overhead:
        assert _held(ledger, "process.overhead_gas", "O2") == pytest.approx(
            before_overhead_o2 + expected_o2, rel=1e-9, abs=1e-12,
        )
        assert _held(ledger, "reservoir.fo2_buffer", "O2") == pytest.approx(
            before_buffer_o2, abs=1e-12,
        )
    else:
        assert _held(ledger, "reservoir.fo2_buffer", "O2") == pytest.approx(
            before_buffer_o2 + expected_o2, rel=1e-9, abs=1e-12,
        )
        assert _held(ledger, "process.overhead_gas", "O2") == pytest.approx(
            before_overhead_o2, abs=1e-12,
        )
    # The credit is the parent mass the vapour formula requires. The
    # kilogram check above is that mass. The molar mass is only the
    # unit bridge back to the lot, unused as an expected value.
    assert o2_formula.molar_mass_kg_per_mol() > 0.0
    ledger.assert_balanced()


@pytest.mark.xdist_group("serial")
def test_supersaturated_species_recondenses(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """Melt gains, overhead loses, and the forward O2 account reverses.

    K's forward oxygen is the buffer. SiO's is overhead. CrO2's forward
    oxygen is an oxidative buffer debit, so the reverse credits it.
    The parent need not have been in the melt: the credit creates it.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1600.0
    for species in ("K", "SiO", "CrO2"):
        _recondense(sim, species)


@pytest.mark.xdist_group("serial")
def test_recondensation_creates_a_missing_parent_oxide(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """available_kg of 0 does not withhold a returning vapour.

    The proposal credits the parent and debits the vapour. No melt
    stock is an input.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    row = _species_row(sim, "K")
    stoich = sim._evaporation_stoich("K", row)
    parent = str(stoich["parent_oxide"])
    result = BuiltinEvaporationTransitionProvider().dispatch(IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {},
                "process.overhead_gas": {"K": 1.0},
                "process.condensation_train": {},
                "reservoir.fo2_buffer": {"O2": 1.0},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1600.0,
        pressure_bar=1.0e-6,
        control_inputs={
            "species": "K",
            "stoich": dict(stoich),
            "sp_data": dict(row),
            "rate_kg_hr": -0.01,
            "remaining_kg_hr": -0.01,
            "dt_hr": 1.0,
            "available_kg": 0.0,
            "post_subfloor_coupled_legs": True,
        },
    ))
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    assert proposal.reason == "recondense_K"
    assert proposal.credits["process.cleaned_melt"][parent] > 0.0
    assert parent not in proposal.debits.get("process.cleaned_melt", {})
    assert proposal.debits["process.overhead_gas"]["K"] > 0.0
    # The proof keeps only elements whose net exceeds 1e-15 mol.
    # A closed parcel therefore has an empty proof. A retained
    # element is an imbalance.
    for element, net in dict(proposal.atom_balance_proof).items():
        assert abs(float(net)) < 1e-9, element
