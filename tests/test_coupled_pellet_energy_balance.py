"""Coupled quasi-steady pellet balance.

The root is absorbed power against radiation, holder conduction,
continuum argon, and the evaporative enthalpy at the same temperature.
The enthalpy owner is ``evaporation_enthalpy_budget``. The flux in the
SiO fixture is the series-resistance owner. Expected watts come from
the JANAF reaction enthalpy and ``MOLAR_MASS``, not from a pasted copy
of the watt conversion.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import (
    _evaluate_alpha_control,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from engines.builtin.vapor_pressure import BUILTIN_VAPOR_BELOW_400K_LIMIT_K
from simulator.evaporation import _load_evaporation_alpha_by_species
from simulator.physical_constants import GAS_CONSTANT_6SIG
from simulator.state import MOLAR_MASS
from simulator.thermal_budget import (
    COLD_BOUNDARY_K,
    SOLAR_ABSORPTANCE_DEFAULT,
    PowerTemperatureRefusal,
    _argon_loss_W,
    _radiation_exchange_W,
    _side_node_temperature_K,
    _slab_conductance_W_per_K,
    evaporative_enthalpy_sink_W,
    evaporation_enthalpy_budget,
    pellet_face_areas_m2,
    solve_power_driven_melt_temperature_C,
)


_REPO = Path(__file__).resolve().parents[1]
_FOCAL_W = 1468.819694
_DIAMETER_M = 0.013
_HEIGHT_M = 0.010
_SPOT_M = 0.020
_MASS_KG = 0.00338
_STEADY_S = 1.0e12
# NIST-JANAF Chase 1998, independent of the coefficient table:
# ΔfH°[SiO2, α-quartz] = -910.94 kJ/mol, ΔfH°[SiO(g)] = -100.42 kJ/mol.
# SiO2 -> SiO(g) + 1/2 O2 = -100.42 - (-910.94).
_JANAF_SIO_KJ_PER_MOL = -100.42 - (-910.94)
# heat-balance-audit.md at this seat's power-model fold: about 2019 °C,
# P_eq(SiO) = 4382 Pa, chamber 1300 Pa. The van't Hoff below is that
# anchor plus the budget's dH. It is not a production pressure law.
_ANCHOR_K = 2019.0 + 273.15
_ANCHOR_P_PA = 4382.0
_CHAMBER_PA = 1300.0
_RELATIVE_CLOSURE = 1.0e-6


def _sio_molar_mass_kg() -> float:
    return float(MOLAR_MASS["SiO"]) / 1000.0


def _sio_reaction_J_per_mol() -> float:
    """Joules per mole from the production budget at one mole per hour.

    kWh per hour at 1 mol/h is kJ/mol divided by 3600. Multiplying the
    budget's kWh by 3.6e6 J/kWh recovers J/mol.
    """

    kg_per_h = _sio_molar_mass_kg()
    budget = evaporation_enthalpy_budget({"SiO": kg_per_h})
    return float(budget["evaporation_thermal_kWh"]) * 3.6e6


def _watts_for_mg_per_min(mg_per_min: float) -> float:
    """JANAF watts. kg/h = mg/min * 1e-6 kg/mg * 60 min/h."""

    kg_per_h = mg_per_min * 1.0e-6 * 60.0
    mol_per_h = kg_per_h / _sio_molar_mass_kg()
    return mol_per_h * _JANAF_SIO_KJ_PER_MOL * 1000.0 / 3600.0


def _areas() -> dict[str, float]:
    return pellet_face_areas_m2(_DIAMETER_M, _HEIGHT_M)


def _solve(**overrides):
    payload = dict(
        power_W=_FOCAL_W,
        previous_temperature_C=25.0,
        dt_s=_STEADY_S,
        mass_kg=_MASS_KG,
        area_m2=None,
        emissivity=0.85,
        pellet_diameter_m=_DIAMETER_M,
        pellet_height_m=_HEIGHT_M,
        focal_spot_diameter_m=_SPOT_M,
        radiating_face_set="hot_upper",
        solar_absorptance=SOLAR_ABSORPTANCE_DEFAULT,
        window_transmittance=1.0,
        conductivity_W_m_K=1.0,
    )
    payload.update(overrides)
    return solve_power_driven_melt_temperature_C(**payload)


def _lumped_hot_upper(**overrides):
    payload = dict(overrides)
    payload["area_m2"] = _areas()["hot_upper"]
    payload["radiating_face_set"] = None
    return _solve(**payload)


def _alpha_spec():
    document = yaml.safe_load(
        (_REPO / "data" / "vapor_pressures.yaml").read_text()
    )
    return _load_evaporation_alpha_by_species(document)["SiO"]


def _kinetic_sio_kg_hr(temperature_K: float, alpha_spec) -> dict[str, float]:
    """Booked SiO kg/h on the top face, from the series-resistance owner.

    P_eq is the audit anchor run through van't Hoff at the budget's dH.
    Gas resistance is off, so this probe is the owner's interface law.
    Production evaporation still includes gas resistance; this fixture
    does not reimplement that term. Below 400 K the evaporation owner
    returns a physical zero and this callback is not the place that
    decides the floor: the balance already skips the callback there.
    """

    if temperature_K < 400.0:
        return {}
    enthalpy_J = _sio_reaction_J_per_mol()
    exponent = (
        -enthalpy_J
        / GAS_CONSTANT_6SIG
        * (1.0 / temperature_K - 1.0 / _ANCHOR_K)
    )
    # A trial far above the anchor must stay a finite pressure. The
    # bisection's first upper probe is a few thousand kelvin, where
    # this exponent is about 25. Clamping at 80 keeps P_eq finite
    # (exp(80) is 5.5e34) without moving the root.
    p_eq = _ANCHOR_P_PA * math.exp(min(80.0, exponent))
    alpha, _evaluation = _evaluate_alpha_control(
        "SiO",
        temperature_K,
        alpha_spec,
    )
    flux = _series_resistance_evaporation_flux_kg_m2_s(
        "SiO",
        p_eq,
        _CHAMBER_PA,
        temperature_K,
        _sio_molar_mass_kg(),
        alpha,
        knudsen_number=math.inf,
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    )
    kg_per_h = float(flux.flux_kg_s_m2) * _areas()["top"] * 3600.0
    if not math.isfinite(kg_per_h) or kg_per_h <= 0.0:
        return {}
    return {"SiO": kg_per_h}


def _two_node_sinks_W(record, rates_at) -> float:
    """External watts from the term owners, not from the solver's report.

    The side temperature is recomputed. The evaporative term is
    ``evaporative_enthalpy_sink_W`` on the callback's kg/h. A root that
    ignored the callback returns the no-evaporation temperature, and
    this sum then exceeds absorbed power.
    """

    top_K = float(record["temperature_K"])
    boundary_K = float(record["cold_boundary_K"])
    emissivity = float(record["emissivity"])
    top_m2 = float(record["top_area_m2"])
    side_m2 = float(record["side_area_m2"])
    half = 2.0 * _slab_conductance_W_per_K(
        float(record["conductivity_W_m_K"]),
        top_m2,
        _HEIGHT_M,
    )
    side_K = _side_node_temperature_K(
        top_K,
        side_area_m2=side_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
        half_conductance_W_per_K=half,
    )
    return (
        _radiation_exchange_W(
            top_K,
            area_m2=top_m2,
            emissivity=emissivity,
            boundary_K=boundary_K,
        )
        + _argon_loss_W(
            top_K,
            diameter_m=_DIAMETER_M,
            boundary_K=boundary_K,
        )
        + evaporative_enthalpy_sink_W(top_K, rates_at(top_K))
        + _radiation_exchange_W(
            side_K,
            area_m2=side_m2,
            emissivity=emissivity,
            boundary_K=boundary_K,
        )
        + half * (side_K - boundary_K)
    )


def _one_node_sinks_W(record, rates_at) -> float:
    temperature_K = float(record["temperature_K"])
    boundary_K = float(record["cold_boundary_K"])
    emissivity = float(record["emissivity"])
    conduction = _slab_conductance_W_per_K(
        float(record["conductivity_W_m_K"]),
        float(record["top_area_m2"]),
        _HEIGHT_M,
    ) * (temperature_K - boundary_K)
    return (
        _radiation_exchange_W(
            temperature_K,
            area_m2=float(record["area_m2"]),
            emissivity=emissivity,
            boundary_K=boundary_K,
        )
        + conduction
        + _argon_loss_W(
            temperature_K,
            diameter_m=_DIAMETER_M,
            boundary_K=boundary_K,
        )
        + evaporative_enthalpy_sink_W(temperature_K, rates_at(temperature_K))
    )


def _relative_gap(power_W: float, sinks_W: float) -> float:
    return abs(power_W - sinks_W) / power_W


def test_sio_enthalpy_matches_janaf_and_the_audit_watt_sanity():
    assert _sio_reaction_J_per_mol() / 1000.0 == pytest.approx(
        _JANAF_SIO_KJ_PER_MOL
    )
    # 30.2 mg/min is the audit's 9.25 W example. 401 mg/min is the
    # one-minute dump, 122.8 W. Both are the JANAF product above.
    small = _watts_for_mg_per_min(30.2)
    dump = _watts_for_mg_per_min(401.0)
    assert small == pytest.approx(9.25, abs=0.05)
    assert dump == pytest.approx(122.8, abs=0.5)
    kg_small = 30.2 * 1.0e-6 * 60.0
    assert evaporative_enthalpy_sink_W(
        _ANCHOR_K,
        {"SiO": kg_small},
    ) == pytest.approx(small, rel=1.0e-9)
    assert COLD_BOUNDARY_K == pytest.approx(300.0)


def test_coupled_root_closes_and_a_dropped_sink_does_not():
    alpha_spec = _alpha_spec()

    def rates(temperature_K: float) -> dict[str, float]:
        return _kinetic_sio_kg_hr(temperature_K, alpha_spec)

    coupled = _solve(species_rate_kg_hr_at_K=rates)
    dropped = _solve()
    closed = _two_node_sinks_W(coupled, rates)
    opened = _two_node_sinks_W(dropped, rates)

    assert coupled["nodes"] == "two_node"
    assert _relative_gap(coupled["power_W"], closed) < _RELATIVE_CLOSURE
    # Mutation: the no-evaporation root is what the solver returns when
    # the sink is left out of the residual. The independent sum still
    # adds the sink, so that state does not close.
    assert _relative_gap(dropped["power_W"], opened) > _RELATIVE_CLOSURE
    assert 0.0 < dropped["temperature_C"] - coupled["temperature_C"] < 2000.0
    assert coupled["evaporative_sink_W"] > 0.0
    assert dropped["evaporative_sink_W"] == pytest.approx(0.0)


def test_lumped_sio_dump_is_not_a_closed_state():
    # The one-node lumped area already balances absorbed power with no
    # evaporation. Adding the 401 mg/min dump at that temperature does
    # not close. The coupled two-node root, with the series-resistance
    # rate at the trial temperature, does close, and it is colder than
    # the two-node root that omits the sink. The booked rate at the
    # coupled root is the allowance. This does not assert that allowance
    # is under 401 mg/min: top-face radiation cannot reject the beam on
    # its own, so the kinetic rate at the root can exceed the old dump.
    alpha_spec = _alpha_spec()

    def kinetic(temperature_K: float) -> dict[str, float]:
        return _kinetic_sio_kg_hr(temperature_K, alpha_spec)

    def dump(temperature_K: float) -> dict[str, float]:
        if temperature_K < 400.0:
            return {}
        return {"SiO": 401.0 * 1.0e-6 * 60.0}

    lumped = _lumped_hot_upper()
    bare_two = _solve()
    coupled = _solve(species_rate_kg_hr_at_K=kinetic)
    lumped_closed = _one_node_sinks_W(lumped, lambda _temperature: {})
    lumped_dump = _one_node_sinks_W(lumped, dump)
    coupled_closed = _two_node_sinks_W(coupled, kinetic)

    assert _relative_gap(lumped["power_W"], lumped_closed) < _RELATIVE_CLOSURE
    assert _relative_gap(lumped["power_W"], lumped_dump) > _RELATIVE_CLOSURE
    assert _relative_gap(coupled["power_W"], coupled_closed) < _RELATIVE_CLOSURE
    assert coupled["temperature_C"] < bare_two["temperature_C"]
    booked = kinetic(coupled["temperature_K"])
    assert booked["SiO"] > 0.0
    assert coupled["evaporative_sink_W"] == pytest.approx(
        evaporative_enthalpy_sink_W(coupled["temperature_K"], booked)
    )
    flash = _one_node_sinks_W(lumped, kinetic)
    assert _relative_gap(lumped["power_W"], flash) > _RELATIVE_CLOSURE


def test_high_conductivity_merges_the_two_nodes_with_the_one_node_bound():
    # k → ∞ sends both the two-node top and the one-node full side to
    # the holder. The half-slab series is the one-node holder term, so
    # the temperatures meet. k = 1e8 W m-1 K-1 is that limit for this
    # pellet: the drop P/G is well under 0.05 °C.
    alpha_spec = _alpha_spec()

    def rates(temperature_K: float) -> dict[str, float]:
        return _kinetic_sio_kg_hr(temperature_K, alpha_spec)

    for callback in (None, rates):
        two = _solve(
            species_rate_kg_hr_at_K=callback,
            conductivity_W_m_K=1.0e8,
        )
        one = _solve(
            species_rate_kg_hr_at_K=callback,
            conductivity_W_m_K=1.0e8,
            radiating_face_set="full_side",
        )
        assert two["nodes"] == "two_node"
        assert one["nodes"] == "one_node"
        assert abs(two["temperature_C"] - two["temperature_side_C"]) < 0.05
        assert abs(two["temperature_C"] - one["temperature_C"]) < 0.05


def test_the_flux_callback_is_not_called_below_the_evaporation_floor():
    def rates(temperature_K: float) -> dict[str, float]:
        if temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K:
            raise AssertionError(temperature_K)
        return {"SiO": 0.006}

    coupled = _solve(species_rate_kg_hr_at_K=rates)
    bare = _solve()
    assert coupled["temperature_C"] < bare["temperature_C"]


def test_a_malformed_rate_callback_refuses():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        _solve(species_rate_kg_hr_at_K=lambda _temperature: [0.1])
    assert refused.value.reason == "evaporative_rate_callback_malformed"


def test_the_tonne_sky_does_not_spend_the_pellet_sink():
    def rates(_temperature_K: float) -> dict[str, float]:
        raise AssertionError("the sky balance called the pellet flux")

    record = solve_power_driven_melt_temperature_C(
        power_W=1740.1373551492054,
        previous_temperature_C=1800.0,
        dt_s=60.0,
        mass_kg=1.0,
        area_m2=None,
        emissivity=0.85,
        species_rate_kg_hr_at_K=rates,
    )

    assert record["nodes"] == "sky"
    assert record["evaporative_sink_W"] is None
    assert record["temperature_C"] == pytest.approx(1800.0, abs=1.0e-4)
    notice = next(
        item
        for item in record["notices"]
        if item["knob"] == "species_rate_kg_hr_at_K"
    )
    assert notice["kind"] == "typed_unknown"
    assert notice["value"] == "not_applied"
