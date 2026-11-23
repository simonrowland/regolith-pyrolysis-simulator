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
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.evaporation_flux import (
    _evaluate_alpha_control,
    _series_resistance_evaporation_flux_kg_m2_s,
    species_drive_bulk_Pa,
)
from engines.builtin.vapor_pressure import BUILTIN_VAPOR_BELOW_400K_LIMIT_K
from simulator.core import PyrolysisSimulator
from simulator.evaporation import (
    EvaporationMixin,
    _load_evaporation_alpha_by_species,
)
from simulator.physical_constants import GAS_CONSTANT_6SIG
from simulator.reference_data.janaf import (
    TABLES_DIR,
    load_table_document,
)
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
from simulator.lab_geometry import resolve_lab_gas_film_geometry


_REPO = Path(__file__).resolve().parents[1]
_FOCAL_W = 1468.819694
_DIAMETER_M = 0.013
_HEIGHT_M = 0.010
_SPOT_M = 0.020
_MASS_KG = 0.00338
_STEADY_S = 1.0e12
# Rounded 298.15 K quartz arithmetic kept for a budget call that omits
# temperature. The printed cells are read below; they are not these two
# numbers. -100.42 - (-910.94) = 810.52.
_JANAF_SIO_KJ_PER_MOL = -100.42 - (-910.94)
# heat-balance-audit.md at this seat's power-model fold: about 2019 °C,
# P_eq(SiO) = 4382 Pa, chamber 1300 Pa. The van't Hoff below is that
# anchor plus the budget's dH. It is not a production pressure law.
_ANCHOR_K = 2019.0 + 273.15
_ANCHOR_P_PA = 4382.0
_RELATIVE_CLOSURE = 1.0e-6


def _sio_molar_mass_kg() -> float:
    return float(MOLAR_MASS["SiO"]) / 1000.0


def _printed_formation_kj(table_id: str, temperature_K: str) -> float:
    """The table's signed ΔfH° cell. ``value`` is the repaired token."""

    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    for item in table["values"]:
        temperature = item.get("temperature") or {}
        if str(temperature.get("as_published")) != temperature_K:
            continue
        cell = item.get("formation_enthalpy") or {}
        return float(cell["value"])
    raise AssertionError(f"{table_id} has no ΔfH° at {temperature_K} K")


def _printed_sio_reaction_kj(
    temperature_K: str,
    reactant_table: str,
    reactant_temperature_K: str,
) -> float:
    """Stoichiometric sum of printed cells. Not the production interpolator."""

    return (
        _printed_formation_kj("O-012", temperature_K)
        + 0.5 * _printed_formation_kj("O-029", temperature_K)
        - _printed_formation_kj(reactant_table, reactant_temperature_K)
    )


def _sio_mole_budget(temperature_K: float) -> tuple[float, dict]:
    budget = evaporation_enthalpy_budget(
        {"SiO": _sio_molar_mass_kg()},
        temperature_K=temperature_K,
    )
    return float(budget["evaporation_thermal_kWh"]) * 3600.0, budget


def _sio_reaction_J_per_mol(temperature_K: float | None = None) -> float:
    """Joules per mole from the production budget at one mole per hour.

    kWh per hour at 1 mol/h is kJ/mol divided by 3600. Multiplying the
    budget's kWh by 3.6e6 J/kWh recovers J/mol. Omitting temperature
    keeps the separate 298 K coefficient pin; supplying it selects the
    production temperature-dependent reaction enthalpy.
    """

    kg_per_h = _sio_molar_mass_kg()
    budget = evaporation_enthalpy_budget(
        {"SiO": kg_per_h},
        temperature_K=temperature_K,
    )
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


def _runtime_robinot_pressure_pair(*, gas_resistance_enabled: bool):
    """Use the runtime chamber floor and SiO partial-pressure policy."""
    runtime = object.__new__(PyrolysisSimulator)
    runtime.melt = SimpleNamespace(p_total_mbar=13.0)
    preset = yaml.safe_load(
        (_REPO / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml")
        .read_text()
    )
    p_o2_bulk_pa = (
        float(
            preset["pair"]["remediation"]["mitigation"]["pO2_cover"][
                "effective_pO2_achieved_mbar"
            ]
        )
        * 100.0
    )
    partials, total_pressure_pa = EvaporationMixin._langmuir_film(
        runtime,
        {"SiO": 0.0, "O2": p_o2_bulk_pa},
    )
    bulk_pressure_pa = species_drive_bulk_Pa(
        "SiO",
        partials.get("SiO", 0.0),
        gas_resistance_enabled=gas_resistance_enabled,
        hkl_upper_bound=False,
        surface_oxygen_bound=False,
    )
    return partials, bulk_pressure_pa, total_pressure_pa


def _kinetic_sio_kg_hr(
    temperature_K: float,
    alpha_spec,
    melt_surface_area_m2: float | None = None,
    *,
    gas_resistance_enabled: bool = False,
    gas_film_shape: str | None = None,
    transport_length_m: float = 0.12,
    carrier_gas: str = "Ar",
) -> dict[str, float]:
    """Booked SiO kg/h from the series-resistance owner.

    P_eq is the audit anchor run through van't Hoff at the budget's dH.
    Gas resistance is normally off for the interface-law fixture; the
    Robinot root enables production series resistance through the same
    owner. Below 400 K the evaporation owner returns a physical zero and
    this callback is not the place that decides the floor: the balance
    already skips the callback there.
    """

    if temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K:
        return {}
    enthalpy_J = _sio_reaction_J_per_mol(temperature_K)
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
    _partials, bulk_pressure_pa, total_pressure_pa = (
        _runtime_robinot_pressure_pair(
            gas_resistance_enabled=gas_resistance_enabled
        )
    )
    flux = _series_resistance_evaporation_flux_kg_m2_s(
        "SiO",
        p_eq,
        bulk_pressure_pa,
        temperature_K,
        _sio_molar_mass_kg(),
        alpha,
        knudsen_number=None if gas_resistance_enabled else math.inf,
        pipe_diameter_m=transport_length_m,
        overhead_pressure_pa=total_pressure_pa,
        stefan_total_pressure_pa=total_pressure_pa,
        carrier_gas=carrier_gas,
        T_gas_K=temperature_K,
        gas_resistance_enabled=gas_resistance_enabled,
        melt_resistance_enabled=False,
        gas_film_shape=gas_film_shape,
        oxygen_coproduct_moles_per_mole=0.5,
        oxygen_bulk_partial_pressure_pa=_partials.get("O2", 0.0),
    )
    area_m2 = (
        _areas()["top"]
        if melt_surface_area_m2 is None
        else float(melt_surface_area_m2)
    )
    kg_per_h = float(flux.flux_kg_s_m2) * area_m2 * 3600.0
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
    top_m2 = math.pi * (_DIAMETER_M / 2.0) ** 2
    half_wall_m2 = math.pi * _DIAMETER_M * _HEIGHT_M / 2.0
    half = 2.0 * _slab_conductance_W_per_K(
        float(record["conductivity_W_m_K"]),
        top_m2,
        _HEIGHT_M,
    )
    side_K = _side_node_temperature_K(
        top_K,
        side_area_m2=half_wall_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
        half_conductance_W_per_K=half,
    )
    return (
        _radiation_exchange_W(
            top_K,
            area_m2=top_m2 + half_wall_m2,
            emissivity=emissivity,
            boundary_K=boundary_K,
        )
        + _argon_loss_W(
            top_K,
            diameter_m=_DIAMETER_M,
            boundary_K=boundary_K,
        )
        + evaporative_enthalpy_sink_W(
            top_K,
            rates_at(top_K, record["node_1_area_m2"]),
        )
        + _radiation_exchange_W(
            side_K,
            area_m2=half_wall_m2,
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
        + evaporative_enthalpy_sink_W(
            temperature_K,
            rates_at(temperature_K, None),
        )
    )


def _relative_gap(power_W: float, sinks_W: float) -> float:
    return abs(power_W - sinks_W) / power_W


def test_sio_enthalpy_matches_janaf_and_the_audit_watt_sanity():
    assert _sio_reaction_J_per_mol() / 1000.0 == pytest.approx(
        _JANAF_SIO_KJ_PER_MOL
    )
    quartz_298 = _printed_sio_reaction_kj("298.15", "O-037", "298.15")
    liquid_298 = _printed_sio_reaction_kj("298.15", "O-038", "298.15")
    assert quartz_298 != pytest.approx(_JANAF_SIO_KJ_PER_MOL)
    assert _sio_mole_budget(298.15)[0] == pytest.approx(liquid_298)
    assert _sio_mole_budget(298.15)[0] != pytest.approx(quartz_298)
    # 30.2 mg/min is the audit's 9.25 W example. 401 mg/min is the
    # one-minute dump, 122.8 W. Both use the omitted-temperature coefficient.
    small = _watts_for_mg_per_min(30.2)
    dump = _watts_for_mg_per_min(401.0)
    assert small == pytest.approx(9.25, abs=0.05)
    assert dump == pytest.approx(122.8, abs=0.5)
    kg_small = 30.2 * 1.0e-6 * 60.0
    anchor = evaporation_enthalpy_budget(
        {"SiO": kg_small},
        temperature_K=_ANCHOR_K,
    )
    assert evaporative_enthalpy_sink_W(
        _ANCHOR_K,
        {"SiO": kg_small},
    ) == pytest.approx(float(anchor["evaporation_thermal_kWh"]) * 1000.0)
    assert float(anchor["evaporation_thermal_kWh"]) * 1000.0 != pytest.approx(
        small
    )
    low = _printed_sio_reaction_kj("2200", "O-038", "2200")
    high = _printed_sio_reaction_kj("2300", "O-038", "2300")
    chord = low + (high - low) * (_ANCHOR_K - 2200.0) / 100.0
    assert _sio_mole_budget(_ANCHOR_K)[0] == pytest.approx(chord)
    assert "sio_enthalpy" not in anchor["sources"]["oxide_vapor_reaction:SiO"]
    assert "SiO2->SiO(g)" in anchor["sources"]["oxide_vapor_reaction:SiO"]
    assert COLD_BOUNDARY_K == pytest.approx(300.0)


def test_sio_enthalpy_flags_an_unprinted_node_and_the_end_of_the_liquid_table():
    hole_kj, hole = _sio_mole_budget(1700.0)
    assert hole_kj == pytest.approx(
        _printed_formation_kj("O-012", "1700")
        + 0.5 * _printed_formation_kj("O-029", "1700")
        - _printed_formation_kj("O-038", "1600")
    )
    assert hole["sio_enthalpy_notice"]["kind"] == "typed_unknown"
    assert hole["sio_enthalpy_notice"]["knob"] == (
        "sio_enthalpy_unprinted_grid_node"
    )
    assert hole["sio_enthalpy_notice"]["value"] == "nearer_printed_liquid_row"
    above_kj, above = _sio_mole_budget(5000.0)
    assert above_kj == pytest.approx(
        _printed_formation_kj("O-012", "5000")
        + 0.5 * _printed_formation_kj("O-029", "5000")
        - _printed_formation_kj("O-038", "4500")
    )
    assert above["sio_enthalpy_notice"]["knob"] == (
        "sio_enthalpy_above_liquid_table"
    )
    assert above["sio_enthalpy_notice"]["value"] == (
        "held_at_last_printed_liquid_row"
    )
    notices: list[dict] = []
    evaporative_enthalpy_sink_W(
        5000.0,
        {"SiO": _sio_molar_mass_kg()},
        notices=notices,
    )
    assert [item["knob"] for item in notices] == [
        "sio_enthalpy_above_liquid_table"
    ]
    quiet: list[dict] = []
    evaporative_enthalpy_sink_W(
        _ANCHOR_K,
        {"SiO": _sio_molar_mass_kg()},
        notices=quiet,
    )
    assert quiet == []


def test_coupled_root_closes_and_a_dropped_sink_does_not():
    alpha_spec = _alpha_spec()
    evaporating_areas: list[float | None] = []

    def rates(
        temperature_K: float,
        melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        evaporating_areas.append(melt_surface_area_m2)
        return _kinetic_sio_kg_hr(
            temperature_K,
            alpha_spec,
            melt_surface_area_m2,
        )

    coupled = _solve(species_rate_kg_hr_at_K=rates)
    dropped = _solve()
    closed = _two_node_sinks_W(coupled, rates)
    opened = _two_node_sinks_W(dropped, rates)

    assert coupled["nodes"] == "two_node"
    assert coupled["area_m2"] == pytest.approx(coupled["node_1_area_m2"])
    assert evaporating_areas
    assert all(
        area == pytest.approx(coupled["node_1_area_m2"])
        for area in evaporating_areas
    )
    assert _relative_gap(coupled["power_W"], closed) < _RELATIVE_CLOSURE
    # Mutation: the no-evaporation root is what the solver returns when
    # the sink is left out of the residual. The independent sum still
    # adds the sink, so that state does not close.
    assert _relative_gap(dropped["power_W"], opened) > _RELATIVE_CLOSURE
    assert 0.0 < dropped["temperature_C"] - coupled["temperature_C"] < 2000.0
    assert coupled["evaporative_sink_W"] > 0.0
    assert coupled["temperature_C"] == pytest.approx(1876.38549444, abs=0.1)
    assert coupled["evaporative_sink_W"] == pytest.approx(104.52774172, abs=0.1)
    assert rates(
        coupled["temperature_K"],
        coupled["node_1_area_m2"],
    )["SiO"] == pytest.approx(0.02164878087, rel=0.01)
    assert dropped["evaporative_sink_W"] == pytest.approx(0.0)


def test_robinot_hot_upper_root_uses_sample_scale_argon_film():
    preset = yaml.safe_load(
        (_REPO / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml")
        .read_text()
    )
    film = resolve_lab_gas_film_geometry(preset["lab_geometry"])
    assert film is not None
    runtime_partials, runtime_bulk_pa, runtime_total_pa = (
        _runtime_robinot_pressure_pair(gas_resistance_enabled=True)
    )
    assert runtime_partials == {"O2": pytest.approx(0.01)}
    assert runtime_bulk_pa == pytest.approx(0.0)
    assert runtime_total_pa == pytest.approx(1300.0)
    alpha_spec = _alpha_spec()

    def rates(temperature_K: float, area_m2: float | None) -> dict[str, float]:
        return _kinetic_sio_kg_hr(
            temperature_K,
            alpha_spec,
            area_m2,
            gas_resistance_enabled=True,
            gas_film_shape=film.shape,
            transport_length_m=film.length_m,
            carrier_gas="Ar",
        )

    coupled = _solve(species_rate_kg_hr_at_K=rates)
    rate = rates(coupled["temperature_K"], coupled["node_1_area_m2"])["SiO"]
    pipe_rate = _kinetic_sio_kg_hr(
        coupled["temperature_K"],
        alpha_spec,
        coupled["node_1_area_m2"],
        gas_resistance_enabled=True,
        gas_film_shape=film.shape,
        transport_length_m=0.12,
        carrier_gas="Ar",
    )["SiO"]

    assert coupled["nodes"] == "two_node"
    assert film.length_m == pytest.approx(0.013)
    assert film.length_m != pytest.approx(0.12)
    assert coupled["temperature_C"] == pytest.approx(1920.92907849, abs=0.1)
    assert coupled["evaporative_sink_W"] == pytest.approx(73.73557378, abs=0.1)
    assert rate == pytest.approx(0.01529751151, rel=0.01)
    assert rate > pipe_rate * 5.0
    assert _relative_gap(
        coupled["power_W"], _two_node_sinks_W(coupled, rates)
    ) < _RELATIVE_CLOSURE


def test_other_face_set_uses_resolved_area_in_the_coupled_sink():
    sesko = yaml.safe_load(
        (_REPO / "data" / "presets" / "vacuum_pyrolysis" / "sesko_2024.yaml")
        .read_text()
    )
    film = resolve_lab_gas_film_geometry(sesko["lab_geometry"])
    assert film is not None
    resolved_area_m2 = film.area_m2
    alpha_spec = _alpha_spec()
    seen_areas: list[float | None] = []

    def rates(temperature_K: float, area_m2: float | None) -> dict[str, float]:
        seen_areas.append(area_m2)
        return _kinetic_sio_kg_hr(
            temperature_K,
            alpha_spec,
            area_m2,
        )

    record = _solve(
        radiating_face_set="full_side",
        evaporating_area_m2=resolved_area_m2,
        species_rate_kg_hr_at_K=rates,
    )

    assert record["nodes"] == "one_node"
    assert record["evaporating_area_m2"] == pytest.approx(resolved_area_m2)
    assert seen_areas
    assert all(area == pytest.approx(resolved_area_m2) for area in seen_areas)


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

    def kinetic(
        temperature_K: float,
        melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        return _kinetic_sio_kg_hr(
            temperature_K,
            alpha_spec,
            melt_surface_area_m2,
        )

    def dump(
        temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        if temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K:
            return {}
        return {"SiO": 401.0 * 1.0e-6 * 60.0}

    lumped = _lumped_hot_upper()
    bare_two = _solve()
    coupled = _solve(species_rate_kg_hr_at_K=kinetic)
    lumped_closed = _one_node_sinks_W(
        lumped,
        lambda _temperature, _area: {},
    )
    lumped_dump = _one_node_sinks_W(lumped, dump)
    coupled_closed = _two_node_sinks_W(coupled, kinetic)

    assert _relative_gap(lumped["power_W"], lumped_closed) < _RELATIVE_CLOSURE
    assert _relative_gap(lumped["power_W"], lumped_dump) > _RELATIVE_CLOSURE
    assert _relative_gap(coupled["power_W"], coupled_closed) < _RELATIVE_CLOSURE
    assert coupled["temperature_C"] < bare_two["temperature_C"]
    booked = kinetic(
        coupled["temperature_K"],
        coupled["node_1_area_m2"],
    )
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
    def rates(
        temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        if temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K:
            return {}
        return {"SiO": 0.006}

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


def test_node_1_radiates_the_top_and_the_upper_half_wall(monkeypatch):
    import simulator.thermal_budget as thermal_budget

    calls: list[tuple[float, float]] = []
    real = thermal_budget._radiation_exchange_W

    def spy(temperature_K, *, area_m2, emissivity, boundary_K):
        calls.append((float(temperature_K), float(area_m2)))
        return real(
            temperature_K,
            area_m2=area_m2,
            emissivity=emissivity,
            boundary_K=boundary_K,
        )

    monkeypatch.setattr(thermal_budget, "_radiation_exchange_W", spy)
    top = math.pi * (_DIAMETER_M / 2.0) ** 2
    half_wall = math.pi * _DIAMETER_M * _HEIGHT_M / 2.0
    node_1 = top + half_wall
    node_2 = half_wall
    full_wall = math.pi * _DIAMETER_M * _HEIGHT_M
    _watts, side_K, returned_node_1, returned_node_2 = (
        thermal_budget.two_node_pellet_loss_W(
            2300.0,
            top_area_m2=top,
            side_area_m2=full_wall,
            emissivity=0.85,
            conductivity_W_m_K=1.0,
            height_m=_HEIGHT_M,
            diameter_m=_DIAMETER_M,
        )
    )
    direct_calls = list(calls)
    record = _solve()
    assert returned_node_1 == pytest.approx(node_1)
    assert returned_node_2 == pytest.approx(node_2)
    assert record["node_1_area_m2"] == pytest.approx(node_1)
    assert record["node_2_area_m2"] == pytest.approx(node_2)
    assert direct_calls
    assert all(
        area == pytest.approx(node_1) or area == pytest.approx(node_2)
        for _temperature, area in direct_calls
    )
    assert any(area == pytest.approx(node_1) for _temperature, area in direct_calls)
    assert any(area == pytest.approx(node_2) for _temperature, area in direct_calls)
    assert all(
        area != pytest.approx(full_wall) for _temperature, area in direct_calls
    )
    side_areas = [
        area
        for temperature, area in direct_calls
        if temperature == pytest.approx(side_K)
    ]
    assert side_areas
    assert all(area == pytest.approx(node_2) for area in side_areas)
    top_areas = [
        area
        for temperature, area in direct_calls
        if temperature == pytest.approx(2300.0)
    ]
    assert any(area == pytest.approx(node_1) for area in top_areas)
    notice = next(
        item for item in record["notices"] if item["knob"] == "two_node_geometry"
    )
    assert notice["kind"] == "typed_unknown"
    assert notice["value"] == "top_face_plus_upper_half_wall"
    assert "moving melt front" in notice["source"]


def test_booked_two_node_root_is_closer_to_lumped_than_to_the_top_face():
    booked = _solve()
    lumped = _lumped_hot_upper()
    top_only = _solve(area_m2=_areas()["top"], radiating_face_set=None)
    assert top_only["nodes"] == "one_node"
    assert abs(booked["temperature_C"] - lumped["temperature_C"]) < abs(
        booked["temperature_C"] - top_only["temperature_C"]
    )


def test_the_flux_callback_is_not_called_below_the_evaporation_floor():
    def rates(
        temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        if temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K:
            raise AssertionError(temperature_K)
        return {"SiO": 0.006}

    coupled = _solve(species_rate_kg_hr_at_K=rates)
    bare = _solve()
    assert coupled["temperature_C"] < bare["temperature_C"]


def test_a_flux_refusal_inside_the_bracket_stays_a_flagged_temperature():
    from simulator.evaporation import EvaporationFluxRefusal

    def refuse_evaporation(
        temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        if temperature_K >= 3500.0:
            raise EvaporationFluxRefusal("probe_refused", {"temperature_K": temperature_K})
        return {}

    def refuse_power(
        temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        if temperature_K >= 3500.0:
            raise PowerTemperatureRefusal("evaporative_flux_refused", "probe")
        return {}

    for rates in (refuse_evaporation, refuse_power):
        record = _solve(species_rate_kg_hr_at_K=rates)
        assert math.isfinite(record["temperature_C"])
        assert any(
            item["knob"] == "evaporative_flux_inside_bracket"
            for item in record["notices"]
        )


def test_a_nonclosing_residual_returns_the_closest_bracket_temperature():
    from simulator.thermal_budget import _bisect_power_temperature_K

    temperature_K, flags = _bisect_power_temperature_K(
        power_W=1.0e6,
        previous_K=300.0,
        capacity_W_per_K=1.0e-9,
        loss_W=lambda _temperature: 0.0,
    )
    assert math.isfinite(temperature_K)
    assert temperature_K == pytest.approx(1.0e7)
    assert any(
        item["value"] == "power_temperature_balance_did_not_close"
        for item in flags
    )


def test_a_side_node_without_a_bracket_predicts_and_flags(monkeypatch):
    import simulator.thermal_budget as thermal_budget

    monkeypatch.setattr(
        thermal_budget,
        "_radiation_exchange_W",
        lambda *_args, **_kwargs: 1.0e12,
    )
    notices: list[dict] = []
    temperature_K = thermal_budget._side_node_temperature_K(
        2000.0,
        side_area_m2=1.0e-4,
        emissivity=0.85,
        boundary_K=300.0,
        half_conductance_W_per_K=1.0,
        notices=notices,
    )
    assert math.isfinite(temperature_K)
    assert notices[0]["knob"] == "side_node_temperature_K"
    assert notices[0]["value"] == "power_temperature_balance_did_not_close"
    assert notices[0]["kind"] == "typed_unknown"


def test_a_malformed_rate_callback_refuses():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        _solve(
            species_rate_kg_hr_at_K=lambda _temperature, _area: [0.1]
        )
    assert refused.value.reason == "evaporative_rate_callback_malformed"


def test_the_tonne_sky_does_not_spend_the_pellet_sink():
    def rates(
        _temperature_K: float,
        _melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
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


def _bind_power_balance_host(
    *,
    liquid_fraction_at,
    raw_sio_kg_hr: float,
    depletion: float,
    pump_scale: float | None,
    pipe_scale: float,
):
    """Real root callback, with the flux owner and the two caps stubbed.

    The equilibrium's liquid fraction stays the step-start value. The
    trial fraction is whatever ``liquid_fraction_at`` returns.
    """

    from types import SimpleNamespace

    from engines.builtin.vapor_pressure import BUILTIN_VAPOR_BELOW_400K_LIMIT_K
    from simulator.core import PyrolysisSimulator
    from simulator.state import EvaporationFlux
    from simulator.thermal_budget import EVAPORATIVE_BRACKET_CEILING_K

    host = object.__new__(PyrolysisSimulator)
    equilibrium = SimpleNamespace(liquid_fraction=0.0)
    calls = {"calc": 0, "gate": 0, "bakeout": 0, "trial_lf": []}

    class _Ledger:
        def kg_by_account(self, _name):
            return {"SiO2": 1.0}

    host.atom_ledger = _Ledger()
    host.melt = SimpleNamespace(temperature_C=25.0)
    host._restore_power_balance_host = lambda _snapshot: None
    host.process_dt_hr = lambda: 60.0 / 3600.0
    host._subsolidus_handed_off = False
    host._subsolidus_pending_release = None
    host._subsolidus_committed_kg_hr_by_species = {}
    host._subsolidus_bakeout_by_hour = []
    host._wall_supply_computed_kg_hr = {}
    host._last_subsolidus_bakeout = None

    def gate():
        calls["gate"] += 1
        return "cheap-curve"

    def fraction(temperature_K, *, gate_authority):
        assert gate_authority == "cheap-curve"
        value = float(liquid_fraction_at(float(temperature_K)))
        calls["trial_lf"].append(value)
        return value

    def calculate(trial, *, melt_surface_area_m2=None):
        calls["calc"] += 1
        calls["melt_surface_area_m2"] = melt_surface_area_m2
        trial_fraction = float(trial.liquid_fraction)
        if trial_fraction <= 0.0:
            return EvaporationFlux()
        return EvaporationFlux(species_kg_hr={"SiO": raw_sio_kg_hr})

    def deplete_flux(flux, dt_hr=1.0):
        assert dt_hr == pytest.approx(60.0 / 3600.0)
        out = EvaporationFlux(species_kg_hr={
            species: float(rate) * depletion
            for species, rate in flux.species_kg_hr.items()
        })
        out.update_totals()
        return out

    def bakeout(**_kwargs):
        calls["bakeout"] += 1
        diagnostic = {
            "provider_status": "ok" if pump_scale is not None else None,
            "pump_scale": pump_scale,
            "handoff": False,
            "committed": False,
        }
        host._last_subsolidus_bakeout = diagnostic
        return diagnostic

    def pipe_cap(flux):
        out = EvaporationFlux(species_kg_hr={
            species: float(rate) * pipe_scale
            for species, rate in flux.species_kg_hr.items()
            if float(rate) * pipe_scale > 1.0e-15
        })
        out.update_totals()
        return out

    host._power_balance_liquid_gate = gate
    host._melt_redox_liquid_fraction_factor = fraction
    host._get_equilibrium = lambda **_kwargs: equilibrium
    host._calculate_evaporation = calculate
    host._apply_analytic_evaporation_depletion = deplete_flux
    host._apply_c0_bakeout = bakeout
    host._cap_c0_joint_release_to_pipe_capacity = pipe_cap
    cache = {"host": {}, "step_start_C": 25.0}

    def rates(
        temperature_K: float,
        melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        return PyrolysisSimulator._power_balance_species_rates_kg_hr(
            host,
            temperature_K,
            floor_K=BUILTIN_VAPOR_BELOW_400K_LIMIT_K,
            ceiling_K=EVAPORATIVE_BRACKET_CEILING_K,
            cache=cache,
            melt_surface_area_m2=melt_surface_area_m2,
        )

    return host, equilibrium, calls, rates


def test_power_balance_rate_cache_includes_evaporating_area():
    host, _equilibrium, calls, rates = _bind_power_balance_host(
        liquid_fraction_at=lambda _temperature_K: 1.0,
        raw_sio_kg_hr=0.01,
        depletion=1.0,
        pump_scale=None,
        pipe_scale=1.0,
    )
    temperature_K = 1700.0

    rates(temperature_K, 1.0e-4)
    rates(temperature_K, 1.0e-4)
    assert calls["calc"] == 1

    rates(temperature_K, 2.0e-4)
    assert calls["calc"] == 2
    assert (temperature_K, 1.0e-4) in host._power_balance_root_booking
    assert (temperature_K, 2.0e-4) in host._power_balance_root_booking


def test_power_balance_passes_hot_node_area_to_evaporation_owner():
    host, _equilibrium, calls, rates = _bind_power_balance_host(
        liquid_fraction_at=lambda _temperature_K: 1.0,
        raw_sio_kg_hr=0.01,
        depletion=1.0,
        pump_scale=None,
        pipe_scale=1.0,
    )

    coupled = _solve(species_rate_kg_hr_at_K=rates)

    assert calls["melt_surface_area_m2"] == pytest.approx(
        coupled["node_1_area_m2"]
    )


def test_a_solid_start_minute_root_includes_the_sink():
    # Step-start liquid fraction is 0. The cheap curve is liquid only
    # above 1400 °C. The no-sink root is the hotter one; a root that
    # ignored the trial fraction would match it.
    solidus_K = 1400.0 + 273.15
    host, equilibrium, calls, rates = _bind_power_balance_host(
        liquid_fraction_at=lambda temperature_K: (
            0.0 if temperature_K < solidus_K else 1.0
        ),
        raw_sio_kg_hr=0.02,
        depletion=1.0,
        pump_scale=None,
        pipe_scale=1.0,
    )
    bare = _solve()
    coupled = _solve(species_rate_kg_hr_at_K=rates)
    cold = rates(500.0 + 273.15)

    assert equilibrium.liquid_fraction == 0.0
    assert cold == {}
    assert 0.0 in calls["trial_lf"]
    assert 1.0 in calls["trial_lf"]
    assert calls["gate"] == 1
    assert coupled["temperature_C"] < bare["temperature_C"] - 1.0
    booked = host._power_balance_root_booking[
        (float(coupled["temperature_K"]), coupled["node_1_area_m2"])
    ]
    assert booked["rates"]["SiO"] == pytest.approx(0.02)
    assert booked["liquid_fraction"] == pytest.approx(1.0)


def test_a_molten_minute_books_the_root_rate_times_dt():
    raw = 0.02
    depletion = 0.5
    pump = 0.4
    pipe = 0.25
    # The three stubs are the scales. The product is their composition,
    # not a copy of the pump-scale predicate.
    expected = raw * depletion * pump * pipe
    host, _equilibrium, calls, rates = _bind_power_balance_host(
        liquid_fraction_at=lambda _temperature_K: 1.0,
        raw_sio_kg_hr=raw,
        depletion=depletion,
        pump_scale=pump,
        pipe_scale=pipe,
    )
    coupled = _solve(species_rate_kg_hr_at_K=rates)
    root = host._power_balance_root_booking[
        (float(coupled["temperature_K"]), coupled["node_1_area_m2"])
    ]
    assert root["rates"]["SiO"] == pytest.approx(expected)
    dt_hr = 60.0 / 3600.0
    assert root["rates"]["SiO"] * dt_hr == pytest.approx(expected * dt_hr)

    from types import SimpleNamespace

    record = {
        "temperature_C": coupled["temperature_C"],
        "temperature_K": coupled["temperature_K"],
        "notices": (),
    }
    host.campaign_mgr = SimpleNamespace()
    host.campaign_mgr._power_temperature_for_step = record
    host.melt.temperature_C = float(coupled["temperature_C"])
    host._select_power_root_booking()
    assert host._power_balance_applied_booking is not None
    host.melt.temperature_C = float(coupled["temperature_C"]) - 10.0
    host._select_power_root_booking()
    assert host._power_balance_applied_booking is None
    host.melt.temperature_C = float(coupled["temperature_C"])
    host._select_power_root_booking()
    host._flag_frozen_power_balance_premise()
    host._flag_frozen_power_balance_premise()
    notices = [
        item
        for item in record["notices"]
        if item["knob"] == "frozen_step_start_activities_and_redox"
    ]
    assert len(notices) == 1
    assert notices[0]["kind"] == "typed_unknown"
    assert notices[0]["unit"] == "s"
    assert notices[0]["value"] == pytest.approx(60.0)

    before_calc = calls["calc"]
    before_bakeout = calls["bakeout"]
    booked, capacity = host._evaporation_booking_this_step(
        None,
        evaporation_campaign=True,
    )
    assert capacity is None
    assert calls["calc"] == before_calc
    assert calls["bakeout"] == before_bakeout
    assert booked.species_kg_hr["SiO"] == pytest.approx(expected)
    assert booked.species_kg_hr["SiO"] * dt_hr == pytest.approx(
        root["rates"]["SiO"] * dt_hr
    )


def test_a_second_evaluation_after_the_root_turns_red():
    host, equilibrium, calls, rates = _bind_power_balance_host(
        liquid_fraction_at=lambda _temperature_K: 1.0,
        raw_sio_kg_hr=0.01,
        depletion=1.0,
        pump_scale=None,
        pipe_scale=1.0,
    )
    coupled = _solve(species_rate_kg_hr_at_K=rates)
    host._power_balance_applied_booking = host._power_balance_root_booking[
        (float(coupled["temperature_K"]), coupled["node_1_area_m2"])
    ]
    before = calls["calc"]
    assert before > 0
    host._evaporation_booking_this_step(
        equilibrium,
        evaporation_campaign=True,
    )
    # A second ``_calculate_evaporation`` in the root-booking branch
    # increments this count. The branch returns the stored flux.
    assert calls["calc"] == before
