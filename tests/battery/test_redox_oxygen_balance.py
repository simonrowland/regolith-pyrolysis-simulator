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
    _inherit_residue_pressure_contract,
    _make_residue_pressure_model,
    _mass_loss_corner_bounds,
    _pressure_with_fe_speciation,
    _residue_oxygen_balance,
    integrate_knudsen_residue_inventory,
    integrate_residue_inventory,
)
from simulator.fe_redox import _engine_fe_speciation
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
    pressure.declares_redox_active = q is not None
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


def test_pressure_response_reads_nested_fe_speciation_from_this_call(monkeypatch) -> None:
    from types import SimpleNamespace

    from simulator.battery import residue

    response = SimpleNamespace(
        fe_redox_split={"fe3_over_sum_fe": 0.75, "activity_used_for_pressure": 0.12},
        diagnostics={
            "vapor_pressure_diagnostic": {
                "vapor_pressure_numerator_provenance": {
                    "Fe": {"activity_used_for_pressure": 0.29},
                },
                "a_FeO_calphad": {
                    "a_FeO_calphad": 0.37,
                    "a_FeO_authoritative": 0.41,
                    "kress91_split": {
                        "fe3_over_sigma_fe": 0.5,
                        "x_feo": 0.37,
                    },
                },
            }
        }
    )
    monkeypatch.setattr(
        residue,
        "_kress91_fe_speciation",
        lambda *_args, **_kwargs: pytest.fail("same-call speciation must win"),
    )
    flags: set[str] = set()
    evaluated = _pressure_with_fe_speciation(
        SimpleNamespace(),
        {"Fe": 1.0},
        {"FeO": 1.0},
        -8.0,
        temperature_K=1800.0,
        total_pressure_Pa=100_000.0,
        engine_flags=flags,
        engine_response=response,
    )
    assert _engine_fe_speciation(response) == (0.5, 0.29)
    assert _engine_fe_speciation(SimpleNamespace(fe_redox_split=response.fe_redox_split)) == (0.75, 0.12)
    assert evaluated.fe3_over_sum_fe == 0.5
    assert evaluated.fe_activity == 0.29
    assert evaluated.speciation_source == "engine"
    assert flags == set()

    high_temperature_response = SimpleNamespace(
        diagnostics={
            "vapor_pressure_diagnostic": {
                "vapor_pressure_numerator_provenance": {
                    "Fe": {"activity_used_for_pressure": 0.41},
                },
                "a_FeO_calphad": {
                    "a_FeO_calphad": 0.37,
                    "a_FeO_authoritative": 0.41,
                    "kress91_split": {
                        "fe3_over_sigma_fe": 0.5,
                        "x_feo": 0.29,
                    },
                },
            },
        }
    )
    assert _engine_fe_speciation(high_temperature_response) == (0.5, 0.41)


def test_reporting_limit_omission_is_honoured_by_the_exponent_probe() -> None:
    channels = (
        ResidueChannel("Fe", "Fe", "FeO", 1.0, "Fe/O2 root"),
        ResidueChannel("MgO", "MgO", "MgO", 1.0, "documented omission"),
        ResidueChannel("O2", "O2", None, 1.0, "Fe/O2 root"),
    )
    omission = {
        "species": "MgO",
        "reporting_threshold_Pa": 1.0e-8,
        "computed_pressure_Pa": 9.0e-9,
        "provenance": "BuiltinVaporPressureProvider.below_reporting_threshold_omissions",
    }

    def pressure(logp: float, *, unexplained_lower_zero: bool = False) -> PressureEvaluation:
        pO2_bar = 10.0**logp
        pressures = {
            "Fe": 2.114e-7 * pO2_bar**-0.5,
            "MgO": 1.0e-8,
            "O2": 1.0e5 * pO2_bar,
        }
        omissions = {}
        if logp > -8.02:
            pressures["MgO"] = 0.0
            omissions["MgO"] = omission
        if unexplained_lower_zero and logp < -8.10:
            pressures["MgO"] = 0.0
        return PressureEvaluation(
            pressures,
            below_threshold_omissions=omissions,
        )

    pressure.declares_redox_active = True
    balance = _residue_oxygen_balance(
        _channel_terms(channels), {"FeO": 1.0, "MgO": 1.0}, pressure, 1800.0,
    )
    assert balance.pO2_bar > 0.0
    assert balance.pressures_Pa["MgO"] == 1.0e-8
    with pytest.raises(ValueError, match="not positive around the root"):
        _residue_oxygen_balance(
            _channel_terms(channels),
            {"FeO": 1.0, "MgO": 1.0},
            lambda logp: pressure(logp, unexplained_lower_zero=True),
            1800.0,
        )


def test_generic_balance_failure_reaches_finite_step_no_flux_check() -> None:
    channels = (
        ResidueChannel("Fe", "Fe", "FeO", 1.0, "no-flux discriminator"),
        ResidueChannel("O2", "O2", None, 1.0, "no-flux discriminator"),
    )
    calls = 0

    def pressure(_inventory, _logp):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("synthetic generic oxygen solver failure")
        return {"Fe": 0.0, "O2": 0.0}

    result = integrate_residue_inventory(
        {"FeO": 1.0}, channels, pressure,
        temperature_K=1800.0, duration_s=1.0, area_evolution_m2=(1.0e-4,),
    )
    assert result.pO2_bar_by_step == (None,)
    assert calls == 2


def test_zero_oxygen_boundary_uses_redox_dependent_feo_demand() -> None:
    channels = (
        ResidueChannel("FeO", "FeO", "FeO", 1.0, "FeO vapour"),
        ResidueChannel("O2", "O2", None, 1.0, "oxygen coproduct"),
    )

    def pressure(logp: float) -> PressureEvaluation:
        return PressureEvaluation(
            {"FeO": 1.0, "O2": 100_000.0 * 10.0**logp},
            fe3_over_sum_fe=0.5,
            speciation_source="test",
        )

    pressure.declares_redox_active = True
    balance = _residue_oxygen_balance(
        _channel_terms(channels), {"FeO": 1.0}, pressure, 1800.0
    )
    # At q=0.5, o_bar=1.25, but FeO(g) carries one O atom, so its net
    # contribution is -0.25 J_FeO. The root obeys 2 J_O2=0.25 J_FeO;
    # the HKL molecular-mass factors give the independent 8.3424e-7 bar.
    assert balance.pO2_bar == pytest.approx(8.3424e-7, rel=1e-4)
    assert balance.parent_oxygen_demand_at_root["FeO"] == pytest.approx(1.25)
    assert balance.bracket_boundary is None


def test_true_zero_oxygen_limit_preserves_q_at_the_boundary() -> None:
    channels = (
        ResidueChannel("FeO2", "FeO2", "FeO", 1.0, "oxygen-rich Fe vapour"),
        ResidueChannel("O2", "O2", None, 1.0, "oxygen coproduct"),
    )

    def pressure(logp: float) -> PressureEvaluation:
        pO2_bar = 10.0**logp
        return PressureEvaluation(
            {"FeO2": 10.0 * pO2_bar, "O2": 100_000.0 * pO2_bar},
            fe3_over_sum_fe=0.5,
            speciation_source="test",
        )

    pressure.declares_redox_active = True
    balance = _residue_oxygen_balance(
        _channel_terms(channels), {"FeO": 1.0}, pressure, 1800.0
    )
    assert balance.pO2_bar == 0.0
    assert balance.pressures_Pa["FeO2"] == 0.0
    assert balance.pressures_Pa["O2"] == 0.0
    assert balance.parent_oxygen_demand_at_root["FeO2"] == 1.25
    assert balance.relative_residual == 0.0


def test_varying_q_multichannel_root_moves_up_with_more_ferric_iron() -> None:
    channels = (
        ResidueChannel("Fe", "Fe", "FeO", 1.0, "Fe metal vapour"),
        ResidueChannel("FeO", "FeO", "FeO", 1.0, "FeO vapour"),
        ResidueChannel("O2", "O2", None, 1.0, "oxygen coproduct"),
    )

    def balance(q_offset: float):
        def pressure(logp: float) -> PressureEvaluation:
            q = q_offset + 0.2 * (logp + 30.0) / 30.0
            pO2 = 10.0**logp
            return PressureEvaluation(
                {
                    "Fe": 1.0e-4 * pO2**-0.5,
                    "FeO": 1.0e-5,
                    "O2": 100_000.0 * pO2,
                },
                fe3_over_sum_fe=q,
                speciation_source="varying-q fixture",
            )

        pressure.declares_redox_active = True
        return _residue_oxygen_balance(
            _channel_terms(channels), {"FeO": 1.0}, pressure, 1800.0
        )

    less_ferric = balance(0.05)
    more_ferric = balance(0.55)
    assert more_ferric.pO2_bar > less_ferric.pO2_bar


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
    assert corner.value.reason == "residue_pressure_law_exponent_mismatch"
    assert "pressure law exponent mismatch for 'Fe'" in corner.value.detail


def test_fixed_activity_matching_exponent_keeps_the_root() -> None:
    balance = _balance(q=None, fixed=True)
    assert balance.pressure_law_diagnostics == ()
    assert balance.pO2_bar > 0.0


def test_undeclared_pressure_law_keeps_the_hard_exponent_check() -> None:
    def pressure(logp: float):
        return _law(
            logp,
            q=None,
            activity_slope=0.0,
            iron_exponent=-0.4,
        )

    with pytest.raises(ResidueInventoryRefusal) as refusal:
        _residue_oxygen_balance(
            _channel_terms(_channels()),
            {"FeO": 1.0},
            pressure,
            1800.0,
        )
    assert refusal.value.reason == "residue_pressure_law_exponent_mismatch"


def test_pressure_contract_forwarding_keeps_factory_declaration() -> None:
    source = lambda *_args: {}
    source.declares_fixed_activities = True
    source.declares_redox_active = True
    flags: set[str] = set()
    source.engine_flags = flags
    target = lambda *_args: {}

    _inherit_residue_pressure_contract(target, source)
    assert target.declares_fixed_activities is True
    assert target.declares_redox_active is True
    assert target.engine_flags is flags


def test_q_positive_demand_is_the_valence_mean_and_moves_the_root() -> None:
    """Restoring a fixed 1 O per Fe (the old 1/2 O2) makes this assertion fail."""

    q = 0.5
    expected_oxygen = _valence_oxygen_per_iron(q)
    assert expected_oxygen == 1.25
    # Same pressure law as q = 0. Only the oxygen demand can move this root.
    redox = _balance(q=q)
    stoichiometric = _balance(q=0.0)
    assert redox.parent_oxygen_demand_at_root["Fe"] == expected_oxygen
    assert redox.pO2_bar.hex() == "0x1.45f6bce8ef4d2p-21"
    assert stoichiometric.pO2_bar.hex() == "0x1.18e83802627cap-21"
    assert not math.isclose(
        redox.pO2_bar, stoichiometric.pO2_bar, rel_tol=1.0e-4, abs_tol=0.0
    )
    sloped = _balance(q=q, activity_slope=-0.098)
    fe = _fe_diagnostic(sloped)
    assert fe.declared == -0.5
    assert fe.expected == pytest.approx(-0.598, abs=1.0e-9)
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
        starting_inventory={"FeO": 1.0, "SiO2": 1.0},
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
    with pytest.raises(ResidueInventoryRefusal) as after_priming:
        model({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert after_priming.value.reason == "residue_pressure_channels_missing"
    unseen = _make_residue_pressure_model(
        "internal-analytical",
        channels,
        None,
        temperature_K=1800.0,
        total_pressure_Pa=0.0,
        engine_metadata={},
        engine_flags=set(),
        starting_inventory={"FeO": 1.0, "SiO2": 1.0},
        missing_channels_reason="residue_pressure_channels_missing",
        caller="unseen iron channel",
    )
    with pytest.raises(ResidueInventoryRefusal) as before_priming:
        unseen({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    drop_iron["value"] = False
    primed = unseen({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert primed["Fe"] == pytest.approx(3.0)
    drop_iron["value"] = True
    with pytest.raises(ResidueInventoryRefusal) as after_priming_same_callback:
        unseen({"FeO": 1.0, "SiO2": 1.0}, -8.0)
    assert before_priming.value.reason == "residue_pressure_channels_missing"
    assert (
        before_priming.value.detail
        == after_priming.value.detail
        == after_priming_same_callback.value.detail
        == "Fe, FeO"
    )
