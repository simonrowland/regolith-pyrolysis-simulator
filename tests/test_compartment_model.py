from __future__ import annotations

import math

from simulator.accounting.formulas import resolve_species_formula
from simulator.compartment_model import diagnose_compartment_hour
from simulator.condensation import (
    _authoritative_vapour_catalog_payload,
    _wall_deposition_driving_pressure_pa,
    _try_antoine_psat_pa,
)
from simulator.runner import PyrolysisRun
from simulator.state import PipeSegment
from simulator.vapour_rail.catalog import compiled_catalog_for


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
               species: str = "Fe", regime: str = "viscous") -> dict:
    formula = resolve_species_formula(species)
    mol_s = kg_hr / formula.molar_mass_kg_per_mol() / 3600.0
    return {
        "species_partial_pressure_pa": pressure_pa,
        "mol_s": mol_s,
        "transport": {"regime_label": regime, "Kn": 0.001},
        "surface": {"wall_temperature_K": wall_temperature_K},
    }


def _hour(*, species: str = "Fe", rate_kg_hr: float = 1.0) -> dict:
    return {
        "hour": 12,
        "campaign": "C2A",
        "pO2_bar": 1.0e-3,
        "vapor_species_kg_hr": {species: rate_kg_hr},
    }


def _operating_snapshot(candidates: dict[str, dict]) -> dict:
    return {
        "species_partial_pressures_mbar": {"Fe": 0.01, "O2": 0.001},
        "wall_deposition_rate_shadow_candidate": {
            segment: (
                record if "mol_s" not in record else {"Fe": record}
            )
            for segment, record in candidates.items()
        },
    }


class _RouteOwner:
    def __init__(self, segments: list[PipeSegment], partials: dict | None = None) -> None:
        self.segments = segments
        self.wall_species_partial_pressures_pa_by_segment = partials or {}

    def _mixed_temperature_wall_candidate_segments(self, _species: str) -> list[PipeSegment]:
        return self.segments


def test_hot_wall_limit_has_no_wall_deposit_and_condenser_gets_remainder() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    segments = [
        _pipe(
            "stage_0_to_stage_1",
            line_of_sight=True,
            wall_temperature_C=wall_temperature_K - 273.15,
        ),
    ]
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 1.0},
        _operating_snapshot({
            "stage_0_to_stage_1": _candidate(
                kg_hr=0.0,
                pressure_pa=p_sat_pa / 2.0,
                wall_temperature_K=wall_temperature_K,
            ),
        }),
        pipe_segments=segments,
        condensation_model=_RouteOwner(segments),
        condenser_capture_by_stage_species_kg={(1, "Fe"): 1.0},
    )
    balance = report["species_balances"]["Fe"]
    assert balance["deposited_by_segment_kg_hr"] == {
        "stage_0_to_stage_1": 0.0,
    }
    assert balance["actual_condenser_capture_by_stage_carrier_kg"] == {"1": 1.0}
    assert balance["designated_condenser_capture_by_stage_kg"] == {"1": 1.0}
    assert balance["actual_condenser_capture_by_stage_species_kg"] == {
        "1": {"Fe": 1.0},
    }
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
    segments = [
        _pipe("los", line_of_sight=True, wall_temperature_C=1926.85),
        _pipe("blocked", line_of_sight=False, wall_temperature_C=1926.85),
    ]
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
                kg_hr=0.0,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
                regime="free_molecular",
            ),
        }),
        pipe_segments=segments,
        condensation_model=_RouteOwner(segments),
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


def test_zero_carrier_allows_owner_supported_ballistic_condenser_capture() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    segments = [
        _pipe("los", line_of_sight=True, wall_temperature_C=1926.85),
        _pipe("blocked", line_of_sight=False, wall_temperature_C=1926.85),
    ]
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
                kg_hr=0.0,
                pressure_pa=p_sat_pa * 2.0,
                wall_temperature_K=wall_temperature_K,
                regime="free_molecular",
            ),
        }),
        pipe_segments=segments,
        condensation_model=_RouteOwner(segments),
        condenser_capture_by_stage_species_kg={(1, "Fe"): 0.8},
    )
    balance = report["species_balances"]["Fe"]
    assert balance["status"] == "ballistic_line_of_sight_with_condenser_capture"
    assert balance["deposited_by_segment_kg_hr"] == {"los": 0.2, "blocked": 0.0}
    assert balance["actual_condenser_capture_kg"] == 0.8
    assert math.isclose(
        math.fsum(balance["deposited_by_segment_kg_hr"].values())
        + balance["actual_condenser_capture_kg"],
        1.0,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    )


def test_deposits_plus_condenser_capture_conserve_evaporated_mass() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    segments = [
        _pipe("first", line_of_sight=True, wall_temperature_C=1926.85),
        _pipe("second", line_of_sight=True, wall_temperature_C=1926.85),
    ]
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
        pipe_segments=segments,
        condensation_model=_RouteOwner(segments),
        condenser_capture_by_stage_species_kg={(3, "Fe"): 0.7},
    )
    balance = report["species_balances"]["Fe"]
    accounted = math.fsum((
        *balance["deposited_by_segment_kg_hr"].values(),
        balance["actual_condenser_capture_kg"],
        balance["unassigned_after_condenser_kg_hr"],
    ))
    assert math.fsum((
        *balance["deposited_by_segment_kg_hr"].values(),
        balance["actual_condenser_capture_kg"],
    )) == 1.0
    assert math.isclose(accounted, 1.0, rel_tol=1.0e-12, abs_tol=0.0)
    assert abs(balance["relative_mass_balance_error"]) <= 1.0e-12
    assert balance["designated_condenser_capture_by_stage_kg"] == {}
    assert balance["actual_condenser_capture_by_stage_species_kg"] == {
        "3": {"Fe": 0.7},
    }


def test_candidate_names_without_route_owner_do_not_prove_complete_coverage() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    segment = _pipe("observed", line_of_sight=True, wall_temperature_C=1926.85)
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 1.0},
        _operating_snapshot({
            "observed": _candidate(
                kg_hr=0.1,
                pressure_pa=2.0 * p_sat_pa,
                wall_temperature_K=wall_temperature_K,
            ),
        }),
        pipe_segments=[segment],
        condenser_capture_by_stage_species_kg={(1, "Fe"): 0.7},
    )

    row = report["nodes"]["duct_segments"][0]["species"]["Fe"]
    balance = report["species_balances"]["Fe"]
    assert row["status"] == "computed"
    assert row["deposit_candidate_kg_hr"] == 0.1
    assert balance["status"] == "unavailable"
    assert balance["deposited_by_segment_kg_hr"] is None
    assert balance["unassigned_after_condenser_kg_hr"] is None
    assert balance["relative_mass_balance_error"] is None


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


def test_missing_applicable_segment_candidate_withholds_species_balance() -> None:
    wall_temperature_K = 2200.0
    p_sat_pa, refused = _try_antoine_psat_pa("Fe", wall_temperature_K)
    assert not refused and p_sat_pa is not None
    segments = [
        _pipe("observed", line_of_sight=True, wall_temperature_C=1926.85),
        _pipe("missing", line_of_sight=True, wall_temperature_C=1926.85),
    ]
    report = diagnose_compartment_hour(
        {**_hour(), "carrier_flow_mol_s": 1.0},
        _operating_snapshot({
            "observed": _candidate(
                kg_hr=0.1,
                pressure_pa=p_sat_pa / 2.0,
                wall_temperature_K=wall_temperature_K,
            ),
        }),
        pipe_segments=segments,
        condensation_model=_RouteOwner(
            segments,
            {"missing": {"Fe": 2.0 * p_sat_pa}},
        ),
        condenser_capture_by_stage_species_kg={(1, "Fe"): 0.7},
    )
    rows = {
        row["name"]: row
        for row in report["nodes"]["duct_segments"]
    }
    assert rows["missing"]["species"]["Fe"]["status"] == "unavailable"
    assert rows["missing"]["species"]["Fe"]["deposit_candidate_kg_hr"] is None
    assert report["species_balances"]["Fe"]["status"] == "unavailable"


def test_missing_geometry_and_wall_temperature_are_typed_unavailable() -> None:
    class Segment:
        def __init__(self, name: str, wall_temperature_C: float | None,
                     surface_area_m2: float | None) -> None:
            self.name = name
            self.wall_temperature_C = wall_temperature_C
            self.surface_area_m2 = surface_area_m2
            self.line_of_sight_to_melt = True
            self.view_factor_from_melt = None

    cases = (
        Segment("missing_geometry", 1926.85, None),
        Segment("missing_wall_temperature", None, 1.0),
    )
    for segment in cases:
        report = diagnose_compartment_hour(
            {**_hour(), "carrier_flow_mol_s": 1.0},
            {
                "species_partial_pressures_mbar": {"Fe": 0.01, "O2": 0.001},
                "wall_deposition_rate_shadow_candidate": {},
            },
            pipe_segments=[segment],
            condensation_model=_RouteOwner(
                [segment], {segment.name: {"Fe": 5.0}},
            ),
            condenser_capture_by_stage_species_kg={(1, "Fe"): 1.0},
        )
        row = report["nodes"]["duct_segments"][0]["species"]["Fe"]
        assert row["status"] == "unavailable"
        assert report["species_balances"]["Fe"]["status"] == "unavailable"
        assert row["wall_temperature_K"] is None if segment.wall_temperature_C is None else (
            row["line_of_sight_area_proxy_m2"] is None
        )


def test_sio_product_capture_is_attributed_through_evaporation_owner() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    sio_data = (
        sim.vapor_pressures.get("metals", {}).get("SiO")
        or sim.vapor_pressures.get("oxide_vapors", {}).get("SiO")
        or {}
    )
    products = sim._condensed_products_kg("SiO", 1.0, sio_data)
    assert set(products) == {"Si", "SiO2"}
    captured_carrier_kg = 0.7
    capture = {
        (3, product): product_kg * captured_carrier_kg
        for product, product_kg in products.items()
    }
    wall_temperature_K = 1800.0
    segments = [
        _pipe("sio_duct", line_of_sight=True, wall_temperature_C=1526.85),
    ]
    report = diagnose_compartment_hour(
        {**_hour(species="SiO"), "carrier_flow_mol_s": 1.0},
        {
            "species_partial_pressures_mbar": {"SiO": 0.01, "O2": 1.0},
            "wall_deposition_rate_shadow_candidate": {
                "sio_duct": {
                    "SiO": _candidate(
                        species="SiO",
                        kg_hr=0.3,
                        pressure_pa=1.0,
                        wall_temperature_K=wall_temperature_K,
                    ),
                },
            },
        },
        pipe_segments=segments,
        condensation_model=_RouteOwner(
            segments, {"sio_duct": {"SiO": 1.0, "O2": 1.0}}
        ),
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
        condenser_capture_by_stage_species_kg=capture,
        evaporation_owner=sim,
    )
    balance = report["species_balances"]["SiO"]
    assert balance["actual_condenser_capture_by_stage_carrier_kg"] == {
        "3": captured_carrier_kg,
    }
    assert balance["actual_condenser_capture_kg"] == captured_carrier_kg
    assert balance["designated_condenser_capture_kg"] == captured_carrier_kg
    assert math.isclose(
        balance["deposited_by_segment_kg_hr"]["sio_duct"]
        + balance["actual_condenser_capture_kg"],
        1.0,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    )
    assert math.isclose(
        balance["deposited_by_segment_kg_hr"]["sio_duct"]
        + balance["actual_condenser_capture_kg"]
        + balance["unassigned_after_condenser_kg_hr"],
        1.0,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    )


def test_product_capture_without_carrier_attribution_is_not_zero() -> None:
    segments = [
        _pipe("sio_duct", line_of_sight=True, wall_temperature_C=1526.85),
    ]
    report = diagnose_compartment_hour(
        {**_hour(species="SiO"), "carrier_flow_mol_s": 1.0},
        {
            "species_partial_pressures_mbar": {"SiO": 0.01, "O2": 1.0},
            "wall_deposition_rate_shadow_candidate": {
                "sio_duct": {
                    "SiO": _candidate(
                        species="SiO",
                        kg_hr=0.1,
                        pressure_pa=20.0,
                        wall_temperature_K=1800.0,
                    ),
                },
            },
        },
        pipe_segments=segments,
        condensation_model=_RouteOwner(
            segments, {"sio_duct": {"SiO": 20.0, "O2": 0.1}}
        ),
        condenser_capture_by_stage_species_kg={(3, "SiO2"): 0.7},
    )
    balance = report["species_balances"]["SiO"]
    assert balance["actual_condenser_capture_kg"] is None
    assert balance["designated_condenser_capture_kg"] is None
    assert balance["status"] == "unavailable"
    assert balance["reason"] == "condenser_product_capture_cannot_be_attributed_without_owner"


def test_sio_wall_pressure_uses_route_o2_and_keeps_extrapolation_notice() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    wall_temperature_K = 1200.0
    candidate = _candidate(
        species="SiO",
        kg_hr=0.1,
        pressure_pa=20.0,
        wall_temperature_K=wall_temperature_K,
    )
    segments = [
        _pipe("sio_duct", line_of_sight=True, wall_temperature_C=926.85),
    ]
    report = diagnose_compartment_hour(
        {**_hour(species="SiO"), "pO2_bar": 1.0e-3},
        {
            "species_partial_pressures_mbar": {"SiO": 0.2, "O2": 0.001},
            "wall_deposition_rate_shadow_candidate": {
                "sio_duct": {"SiO": candidate},
            },
        },
        pipe_segments=segments,
        condensation_model=_RouteOwner(
            segments, {"sio_duct": {"SiO": 20.0, "O2": 0.1}}
        ),
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
    )
    row = report["nodes"]["duct_segments"][0]["species"]["SiO"]
    assert report["nodes"]["headspace"]["pO2_bar"] == 1.0e-6
    payload = _authoritative_vapour_catalog_payload(
        sim.condensation_model.vapor_pressure_data
    )
    assert payload is not None
    evaluator = compiled_catalog_for(
        payload, emit_u0_request_rules=False
    ).evaluator_for_hot_train("SiO")
    expected = evaluator.evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=1.0e-6,
    )
    stale_output_state = evaluator.evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=1.0e-3,
    )
    assert row["wall_saturation_pressure_pa"] == expected.pressure_pa
    assert row["wall_saturation_pressure_pa"] != stale_output_state.pressure_pa
    assert row["wall_saturation_pressure_notice"]["out_of_range"] is True
    assert row["wall_saturation_pressure_notice"]["status"] == (
        "out_of_range_conservative_continuation"
    )
    assert row["wall_saturation_pressure_notice"]["acquisition_flag"]
    assert row["wall_saturation_pressure_notice"]["validation_status"]
    assert row["deposition_propensity_margin_notice"][
        "wall_saturation_pressure_notice"
    ] == row["wall_saturation_pressure_notice"]

    no_o2 = diagnose_compartment_hour(
        _hour(species="SiO"),
        {
            "species_partial_pressures_mbar": {"SiO": 0.2},
            "wall_deposition_rate_shadow_candidate": {
                "sio_duct": {"SiO": candidate},
            },
        },
        pipe_segments=segments,
        condensation_model=_RouteOwner(segments, {"sio_duct": {"SiO": 20.0}}),
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
    )
    no_o2_row = no_o2["nodes"]["duct_segments"][0]["species"]["SiO"]
    assert no_o2_row["wall_saturation_pressure_pa"] is None
    assert no_o2_row["deposition_propensity_margin"] is None
    assert no_o2_row["unavailable_reason"] == (
        "segment_oxygen_partial_pressure_unavailable"
    )


def test_sio_wall_pressure_uses_same_segment_o2_after_mixture_change() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    wall_temperature_K = 1800.0
    segment = _pipe("sio_duct", line_of_sight=True, wall_temperature_C=1526.85)
    local_pressures_pa = {"SiO": 500.0, "O2": 10.0}
    report = diagnose_compartment_hour(
        {
            **_hour(species="SiO"),
            "pO2_bar": 1.0e-3,
            "carrier_flow_mol_s": 1.0,
        },
        {
            "species_partial_pressures_mbar": {"SiO": 0.5, "O2": 0.01},
            "wall_deposition_rate_shadow_candidate": {
                "sio_duct": {
                    "SiO": _candidate(
                        species="SiO",
                        kg_hr=0.1,
                        pressure_pa=local_pressures_pa["SiO"],
                        wall_temperature_K=wall_temperature_K,
                    ),
                },
            },
        },
        pipe_segments=[segment],
        condensation_model=_RouteOwner(
            [segment], {"sio_duct": local_pressures_pa}
        ),
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
    )

    payload = _authoritative_vapour_catalog_payload(
        sim.condensation_model.vapor_pressure_data
    )
    assert payload is not None
    evaluator = compiled_catalog_for(
        payload, emit_u0_request_rules=False
    ).evaluator_for_hot_train("SiO")
    same_segment = evaluator.evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=local_pressures_pa["O2"] / 100_000.0,
    )
    inlet_snapshot = evaluator.evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=1.0 / 100_000.0,
    )
    late_hour_output = evaluator.evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=1.0e-3,
    )
    row = report["nodes"]["duct_segments"][0]["species"]["SiO"]
    assert report["flags"]
    assert row["wall_saturation_pressure_pa"] == same_segment.pressure_pa
    assert row["wall_saturation_pressure_pa"] != inlet_snapshot.pressure_pa
    assert row["wall_saturation_pressure_pa"] != late_hour_output.pressure_pa
    assert row["deposition_propensity_margin_notice"]["pO2_source"] == (
        "route_segment_hour_partial_pressure"
    )


def test_missing_reactive_sio_candidate_is_unavailable_below_source_saturation() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    wall_temperature_K = 1200.0
    local_pressure_pa = 2.484013785384831e-14
    local_o2_pa = 0.1
    owner_driving_pressure = _wall_deposition_driving_pressure_pa(
        "SiO",
        local_pressure_pa,
        wall_temperature_K,
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        diagnostic_out={},
    )
    payload = _authoritative_vapour_catalog_payload(
        sim.condensation_model.vapor_pressure_data
    )
    assert payload is not None
    source_saturation = compiled_catalog_for(
        payload, emit_u0_request_rules=False
    ).evaluator_for_hot_train("SiO").evaluate(
        wall_temperature_K,
        source_activity=1.0,
        pO2_bar=local_o2_pa / 100_000.0,
    )
    assert local_pressure_pa < source_saturation.pressure_pa
    assert owner_driving_pressure > 0.0

    segment = _pipe("sio_duct", line_of_sight=True, wall_temperature_C=926.85)
    report = diagnose_compartment_hour(
        {**_hour(species="SiO"), "carrier_flow_mol_s": 1.0},
        {
            "species_partial_pressures_mbar": {"SiO": 0.2, "O2": 0.001},
            "wall_deposition_rate_shadow_candidate": {},
        },
        pipe_segments=[segment],
        condensation_model=_RouteOwner(
            [segment],
            {"sio_duct": {"SiO": local_pressure_pa, "O2": local_o2_pa}},
        ),
        vapor_pressure_data=sim.condensation_model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
    )

    row = report["nodes"]["duct_segments"][0]["species"]["SiO"]
    assert row["wall_saturation_pressure_pa"] == source_saturation.pressure_pa
    assert row["deposition_propensity_margin"] < 1.0
    assert row["status"] == "unavailable"
    assert row["unavailable_reason"] == "applicable_segment_candidate_missing"
    assert row["deposit_candidate_kg_hr"] is None
    assert report["species_balances"]["SiO"]["status"] == "unavailable"


def test_owner_wall_pressure_refusal_stays_unavailable_without_candidate() -> None:
    owner_refusal: dict = {}
    owner_driving_pressure = _wall_deposition_driving_pressure_pa(
        "Mg", 20.0, 115.0, diagnostic_out=owner_refusal
    )
    assert owner_driving_pressure == 0.0
    assert owner_refusal["wall_saturation_pressure_refused"] is True

    segment = _pipe(
        "refused", line_of_sight=True, wall_temperature_C=115.0 - 273.15
    )
    route_owner = _RouteOwner([segment], {"refused": {"Mg": 20.0}})
    route_owner.last_sticking_alpha_provenance_notice = {
        "wall_saturation_pressure_refusals_by_species": {
            "Mg": {
                "refused": {
                    "status": "refused",
                    "reason": owner_refusal[
                        "wall_saturation_pressure_refusal_reason"
                    ],
                    "wall_temperature_K": 115.0,
                    "wall_saturation_pressure_pa": None,
                },
            },
        },
    }
    report = diagnose_compartment_hour(
        {**_hour(species="Mg"), "carrier_flow_mol_s": 1.0},
        {"wall_deposition_rate_shadow_candidate": {}},
        pipe_segments=[segment],
        condensation_model=route_owner,
    )

    row = report["nodes"]["duct_segments"][0]["species"]["Mg"]
    assert row["status"] == "unavailable"
    assert row["wall_saturation_pressure_pa"] is None
    assert row["wall_saturation_pressure_notice"]["status"] == "refused"
    assert row["unavailable_reason"] == owner_refusal[
        "wall_saturation_pressure_refusal_reason"
    ]
    assert row["deposit_candidate_kg_hr"] is None
    assert report["species_balances"]["Mg"]["status"] == "unavailable"
