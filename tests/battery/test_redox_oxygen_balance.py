"""Redox oxygen per evaporated iron atom in the congruent-vapour balance.

The expected oxygen count is the valence mean written here,
``(1 - q) * (2/2) + q * (3/2)``. It is not a call to the solver's helper.
"""

from __future__ import annotations

import math

import pytest

from simulator.battery.residue import (
    ResidueChannel,
    ResidueInventoryRefusal,
    _channel_terms,
    _make_residue_pressure_model,
    _mass_loss_corner_bounds,
    _residue_oxygen_balance,
    integrate_knudsen_residue_inventory,
    integrate_residue_inventory,
)
from simulator.vacuum_oxygen_balance import PressureEvaluation


def _valence_oxygen_per_iron(q: float) -> float:
    """O atoms per Fe atom from the Fe2+/Fe3+ valence average. q = Fe3+/sumFe."""

    return (1.0 - q) * (2.0 / 2.0) + q * (3.0 / 2.0)


def _channels() -> tuple[ResidueChannel, ...]:
    return (
        ResidueChannel("Fe", "Fe", "FeO", 1.0, "synthetic congruent vapour"),
        ResidueChannel("O2", "O2", None, 1.0, "synthetic congruent vapour"),
    )


def _law(log10_pO2_bar: float, *, q: float | None, activity_slope: float, iron_exponent: float):
    activity = 10.0 ** (activity_slope * log10_pO2_bar)
    partial = 10.0 ** log10_pO2_bar
    pressures = {
        "Fe": activity * partial ** iron_exponent,
        "O2": 1.0e5 * partial,
    }
    if q is None:
        return pressures
    return PressureEvaluation(
        pressures,
        fe3_over_sum_fe=q,
        fe_activity=activity,
        speciation_source="test",
    )


def _balance(*, q: float | None, activity_slope: float = 0.0, iron_exponent: float = -0.5, fixed: bool = False):
    def pressure(log10_pO2_bar: float):
        return _law(
            log10_pO2_bar,
            q=q,
            activity_slope=activity_slope,
            iron_exponent=iron_exponent,
        )

    pressure.declares_fixed_activities = fixed
    return _residue_oxygen_balance(
        _channel_terms(_channels()),
        {"FeO": 1.0},
        pressure,
        1800.0,
    )


def _fe_diagnostic(balance):
    rows = [row for row in balance.pressure_law_diagnostics if row.species == "Fe"]
    assert len(rows) == 1
    return rows[0]


def test_q_zero_is_one_oxygen_per_iron_and_exponent_minus_one_half() -> None:
    q = 0.0
    expected_oxygen = _valence_oxygen_per_iron(q)
    assert expected_oxygen == 1.0
    redox = _balance(q=q)
    stoichiometric = _balance(q=None)
    assert redox.parent_oxygen_demand_at_root["Fe"] == expected_oxygen
    assert redox.pO2_bar == pytest.approx(stoichiometric.pO2_bar, rel=1.0e-12)
    fe = _fe_diagnostic(redox)
    assert fe.reason == "residue_pressure_law_redox_active"
    assert fe.declared == -0.5
    assert fe.expected == -0.5
    assert fe.observed == pytest.approx(-0.5, abs=1.0e-9)


def test_fixed_activity_wrong_exponent_still_refuses() -> None:
    with pytest.raises(ResidueInventoryRefusal) as refusal:
        _balance(q=None, iron_exponent=-0.4, fixed=True)
    assert refusal.value.reason == "residue_pressure_law_exponent_mismatch"
    assert refusal.value.detail.startswith(
        "pressure law exponent mismatch for 'Fe': declared -0.5, observed "
    )

    def pressure(inventory, log10_pO2_bar, temperature=None):
        return _law(
            float(log10_pO2_bar),
            q=None,
            activity_slope=0.0,
            iron_exponent=-0.4,
        )

    pressure.declares_fixed_activities = True
    with pytest.raises(ResidueInventoryRefusal) as knudsen:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0},
            _channels(),
            pressure,
            temperature_path=((0.05, 1800.0),),
            measured_mass_loss_fraction=0.05,
            near_saturated=True,
            source_fO2_log_by_interval=(None,),
        )
    assert knudsen.value.reason == "residue_pressure_law_exponent_mismatch"
    with pytest.raises(ResidueInventoryRefusal) as inventory:
        integrate_residue_inventory(
            {"FeO": 1.0},
            _channels(),
            pressure,
            temperature_K=1800.0,
            duration_s=1.0,
            area_evolution_m2=(1.0e-4,),
        )
    assert inventory.value.reason == "residue_pressure_law_exponent_mismatch"
    with pytest.raises(ResidueInventoryRefusal) as corner:
        _mass_loss_corner_bounds(
            {"FeO": 1.0},
            _channels(),
            pressure,
            unsourced=("Fe",),
            temperature_K=1800.0,
            area_m2=1.0e-4,
            duration_s=1.0,
        )
    assert corner.value.reason == "residue_oxygen_balance_failed"
    assert "pressure law exponent mismatch for 'Fe'" in corner.value.detail


def test_fixed_activity_matching_exponent_keeps_the_root() -> None:
    balance = _balance(q=None, fixed=True)
    assert balance.pressure_law_diagnostics == ()
    assert balance.pO2_bar > 0.0


def test_q_positive_demand_is_the_valence_mean_and_moves_the_root() -> None:
    """Restoring a fixed 1 O per Fe (the old 1/2 O2) makes this assertion fail."""

    q = 0.5
    expected_oxygen = _valence_oxygen_per_iron(q)
    assert expected_oxygen == 1.25
    # Same pressure law as q = 0. Only the oxygen demand can move this root.
    redox = _balance(q=q)
    stoichiometric = _balance(q=0.0)
    assert redox.parent_oxygen_demand_at_root["Fe"] == expected_oxygen
    assert not math.isclose(
        redox.pO2_bar, stoichiometric.pO2_bar, rel_tol=1.0e-4, abs_tol=0.0
    )
    sloped = _balance(q=q, activity_slope=-0.05)
    fe = _fe_diagnostic(sloped)
    assert fe.declared == -0.5
    assert fe.expected == pytest.approx(-0.55, abs=1.0e-9)
    assert fe.observed == pytest.approx(fe.expected, abs=1.0e-8)
    assert fe.reason == "residue_pressure_law_redox_active"


def test_exhausted_iron_parent_omits_its_vapour_as_zero_pressure(monkeypatch) -> None:
    """The engine drops Fe vapour once FeO is gone. That is zero flux, not a missing channel."""

    from types import SimpleNamespace

    from simulator.diagnostic_helpers import binary_pot_battery as battery

    drop_iron = {"value": False}

    def response(**kwargs):
        composition = kwargs["composition_mol"]
        pressures = {}
        if composition.get("FeO", 0.0) > 0.0 and not drop_iron["value"]:
            pressures["Fe(g)"] = 3.0
            pressures["FeO(g)"] = 1.0
        return SimpleNamespace(vapor_pressures_Pa=pressures, diagnostics={})

    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(battery, "internal_analytical_residue_vapor_pressure", response)
    monkeypatch.setattr(
        battery,
        "_gas_formula_for_oxygen_balance",
        lambda channel: str(channel).removesuffix("(g)"),
    )
    channels = (("Fe", ("FeO", 1.0, 0.5)), ("FeO", ("FeO", 1.0, 0.0)), ("O2", ("", 1.0, -1.0)))
    flags: set[str] = set()
    model = _make_residue_pressure_model(
        "internal-analytical",
        channels,
        None,
        temperature_K=1800.0,
        total_pressure_Pa=0.0,
        engine_metadata={},
        engine_flags=flags,
        missing_channels_reason="residue_pressure_channels_missing",
        caller="exhausted parent regression",
    )
    present = model({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert present["Fe"] == pytest.approx(3.0)
    assert present["FeO"] == pytest.approx(1.0)
    assert getattr(present, "speciation_source", None) == "kress91_fallback"
    absent = model({"SiO2": 1.0}, -8.0)
    assert absent["Fe"] == 0.0
    assert absent["FeO"] == 0.0
    assert "residue_fe_speciation_kress91_fallback" in flags
    drop_iron["value"] = True
    omitted = model({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert omitted["Fe"] == 0.0
    assert omitted["FeO"] == 0.0
    assert "residue_omitted_vapour_pressure_zero" in flags
    unseen = _make_residue_pressure_model(
        "internal-analytical",
        channels,
        None,
        temperature_K=1800.0,
        total_pressure_Pa=0.0,
        engine_metadata={},
        engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing",
        caller="unseen iron channel",
    )
    with pytest.raises(ResidueInventoryRefusal) as missing:
        unseen({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert missing.value.reason == "residue_pressure_channels_missing"
    assert missing.value.detail == "Fe, FeO"
