from __future__ import annotations

import math

from simulator.accounting.formulas import resolve_species_formula
from simulator.compartment_model import diagnose_compartment_hour
from simulator.condensation import _try_antoine_psat_pa
from simulator.state import PipeSegment


def _pipe(name: str, *, line_of_sight: bool, wall_temperature_C: float) -> PipeSegment:
    return PipeSegment(
        name=name,
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=wall_temperature_C,
        length_m=1.0,
        inner_diameter_m=0.12,
        line_of_sight_to_melt=line_of_sight,
    )


def _candidate(*, kg_hr: float, pressure_pa: float, wall_temperature_K: float,
               regime: str = "viscous") -> dict:
    formula = resolve_species_formula("Fe")
    mol_s = kg_hr / formula.molar_mass_kg_per_mol() / 3600.0
    return {
        "species_partial_pressure_pa": pressure_pa,
        "mol_s": mol_s,
        "transport": {"regime_label": regime, "Kn": 0.001},
        "surface": {"wall_temperature_K": wall_temperature_K},
    }


def _hour() -> dict:
    return {
        "hour": 12,
        "campaign": "C2A",
        "pO2_bar": 1.0e-3,
        "vapor_species_kg_hr": {"Fe": 1.0},
    }


def _operating_snapshot(candidates: dict[str, dict]) -> dict:
    return {
        "species_partial_pressures_mbar": {"Fe": 0.01, "O2": 0.001},
        "wall_deposition_rate_shadow_candidate": {
            segment: {"Fe": record} for segment, record in candidates.items()
        },
    }


def test_hot_wall_limit_has_no_wall_deposit_and_condenser_gets_remainder() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 1.0},
        _operating_snapshot({
            "stage_0_to_stage_1": _candidate(
                kg_hr=0.0,
                pressure_pa=p_sat_pa / 2.0,
                wall_temperature_K=wall_temperature_K,
            ),
        }),
        pipe_segments=[
            _pipe(
                "stage_0_to_stage_1",
                line_of_sight=True,
                wall_temperature_C=wall_temperature_K - 273.15,
            ),
        ],
        condenser_capture_by_stage_species_kg={(1, "Fe"): 1.0},
    )
    balance = report["species_balances"]["Fe"]
    assert balance["deposited_by_segment_kg_hr"] == {
        "stage_0_to_stage_1": 0.0,
    }
    assert balance["designated_condenser_capture_by_stage_kg"] == {"1": 1.0}
    assert balance["designated_condenser_capture_kg"] == 1.0
    assert balance["condenser_feed_residual_kg_hr"] == 1.0
    assert balance["unassigned_after_condenser_kg_hr"] == 0.0
    assert balance["relative_mass_balance_error"] == 0.0
    margin = report["nodes"]["duct_segments"][0]["species"]["Fe"][
        "deposition_propensity_margin"
    ]
    assert margin == 0.5


def test_zero_carrier_limit_keeps_only_free_molecular_line_of_sight_path() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 0.0},
        _operating_snapshot({
            "los": _candidate(
                kg_hr=0.2,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
                regime="free_molecular",
            ),
            "blocked": _candidate(
                kg_hr=0.3,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
                regime="free_molecular",
            ),
        }),
        pipe_segments=[
            _pipe("los", line_of_sight=True, wall_temperature_C=1926.85),
            _pipe("blocked", line_of_sight=False, wall_temperature_C=1926.85),
        ],
    )
    assert report["carrier_flow"] == {"status": "reported_input", "flow_mol_s": 0.0}
    balance = report["species_balances"]["Fe"]
    assert balance["status"] == "ballistic_line_of_sight_only"
    assert balance["deposited_by_segment_kg_hr"]["los"] == 0.2
    assert balance["deposited_by_segment_kg_hr"]["blocked"] == 0.0
    assert balance["designated_condenser_capture_kg"] is None
    assert math.isclose(
        balance["deposited_by_segment_kg_hr"]["los"]
        + balance["deposited_by_segment_kg_hr"]["blocked"]
        + balance["gas_remaining_without_advective_capture_kg_hr"],
        1.0,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    )


def test_deposits_plus_condenser_capture_conserve_evaporated_mass() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 1.0},
        _operating_snapshot({
            "first": _candidate(
                kg_hr=0.1,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
            ),
            "second": _candidate(
                kg_hr=0.2,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
            ),
        }),
        pipe_segments=[
            _pipe("first", line_of_sight=True, wall_temperature_C=1926.85),
            _pipe("second", line_of_sight=True, wall_temperature_C=1926.85),
        ],
        condenser_capture_by_stage_species_kg={(3, "Fe"): 0.7},
    )
    balance = report["species_balances"]["Fe"]
    accounted = math.fsum((
        *balance["deposited_by_segment_kg_hr"].values(),
        balance["designated_condenser_capture_kg"],
        balance["unassigned_after_condenser_kg_hr"],
    ))
    assert math.isclose(accounted, 1.0, rel_tol=1.0e-12, abs_tol=0.0)
    assert abs(balance["relative_mass_balance_error"]) <= 1.0e-12


def test_missing_crO_wall_pressure_is_typed_not_zero() -> None:
    report = diagnose_compartment_hour(
        {
            **_hour(),
            "vapor_species_kg_hr": {"CrO": 0.4},
        },
        {
            "wall_deposition_rate_shadow_candidate": {
                "stage_0_to_stage_1": {
                    "CrO": {
                        "species_partial_pressure_pa": 20.0,
                        "mol_s": 0.01,
                        "transport": {"regime_label": "viscous"},
                        "surface": {"wall_temperature_K": 1800.0},
                    },
                },
            },
        },
        pipe_segments=[
            _pipe(
                "stage_0_to_stage_1",
                line_of_sight=True,
                wall_temperature_C=1526.85,
            ),
        ],
    )
    node = report["nodes"]["duct_segments"][0]["species"]["CrO"]
    assert node["wall_saturation_status"] == "CrO_has_no_independent_wall_P_sat"
    assert node["wall_saturation_pressure_pa"] is None
    assert node["deposition_propensity_margin"] is None
    assert report["species_balances"]["CrO"]["status"] == "unavailable"
    assert all(
        row["status"] == "not_modelled_in_stage_1"
        for row in report["reactive_sinks"]
    )
