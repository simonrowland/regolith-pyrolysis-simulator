import pytest

from simulator.thermal_budget import (
    ASSUMED,
    CITED,
    UNCERTIFIED,
    furnace_material_context,
    thermal_budget_decomposition,
)


BASE_THERMAL_BUDGET_ARGS = {
    "wall_area_m2": 1.0,
    "wall_thickness_m": 0.10,
    "wall_conductivity_W_m_K": 1.5,
    "wall_inner_solidus_T_C": 1050.0,
    "wall_outer_T_C": 0.0,
    "T_sky_K": 3.0,
    "view_factor": 1.0,
}


def test_cold_skull_reference_case_matches_bootstrap_report_band():
    result = thermal_budget_decomposition(
        wall_area_m2=2.27,
        wall_thickness_m=0.10,
        wall_conductivity_W_m_K=1.5,
        wall_inner_solidus_T_C=1050.0,
        wall_outer_T_C=0.0,
        T_sky_K=3.0,
        view_factor=1.0,
        heat_in_kW=136.0,
        feed_sensible_fusion_enthalpy_kW=0.0,
        reaction_disproportionation_enthalpy_kW=0.0,
        product_vapor_enthalpy_kW=0.0,
        melt_T_C=1750.0,
        melt_surface_area_m2=0.2,
        source_tags={
            "wall_area_m2": {
                "status": CITED,
                "source": "bootstrap heat-balance reference: 1 t melt / about 2.27 m2",
            },
            "wall_thickness_m": {
                "status": CITED,
                "source": "bootstrap heat-balance reference: 10 cm wall",
            },
            "wall_inner_solidus_T_C": {
                "status": CITED,
                "source": "bootstrap heat-balance reference: solidus about 1050 C",
            },
        },
    )

    cold_skull = result["cold_skull"]

    assert cold_skull["q_to_wall_kW_per_m2"] == pytest.approx(15.75)
    assert 11.0 <= cold_skull["cold_skull_cooling_flux_kW_per_m2"] <= 17.0
    assert 24.0 <= cold_skull["cold_skull_cooling_flux_kW_min"] <= 38.0
    assert cold_skull["cold_skull_cooling_flux_kW_min"] == pytest.approx(
        35.135,
        abs=0.01,
    )
    assert result["figures"]["wall_conductivity_W_m_K"]["status"] == CITED
    assert "process gas" in result["notices"][0].lower()


@pytest.mark.parametrize(
    ("key", "default_equivalent_value", "non_default_value", "expected_source"),
    [
        (
            "emissivity",
            0.17 * 5,
            0.5,
            "simulator.equipment.EquipmentDesigner.MELT_EMISSIVITY",
        ),
        (
            "view_factor",
            1.0 - 1e-12,
            0.5,
            "simulator.accounting.queries._wall_geometry_conductance_weight view_factor_from_melt default",
        ),
        (
            "wall_conductivity_W_m_K",
            1.5 + 1e-12,
            1.0,
            "bootstrap heat-balance reference: wall conductivity 1.5 W/(m K)",
        ),
        (
            "wall_inner_solidus_T_C",
            1050.0 + 1e-7,
            1000.0,
            "bootstrap heat-balance reference: solidus about 1050 C",
        ),
    ],
)
def test_caller_overridable_constants_are_cited_only_for_default_values(
    key,
    default_equivalent_value,
    non_default_value,
    expected_source,
):
    default_args = dict(BASE_THERMAL_BUDGET_ARGS)
    default_args[key] = default_equivalent_value
    default_result = thermal_budget_decomposition(
        **default_args,
        source_tags={key: {"status": ASSUMED, "source": "caller override"}},
    )

    override_args = dict(BASE_THERMAL_BUDGET_ARGS)
    override_args[key] = non_default_value
    override_result = thermal_budget_decomposition(
        **override_args,
        source_tags={key: {"status": CITED, "source": expected_source}},
    )

    default_tag = default_result["figures"][key]
    override_tag = override_result["figures"][key]

    assert default_tag["status"] == CITED
    assert default_tag["source"] == expected_source
    assert override_tag["status"] == ASSUMED
    assert override_tag["status"] != CITED
    assert override_tag["source"] == "caller supplied"


def test_uncertified_gaps_are_structured_and_status_flagged():
    result = thermal_budget_decomposition(
        wall_area_m2=1.0,
        wall_thickness_m=0.10,
        wall_conductivity_W_m_K=1.0,
        wall_inner_solidus_T_C=1050.0,
        wall_outer_T_C=0.0,
        T_sky_K=3.0,
        view_factor=1.0,
    )

    gaps = {gap["name"]: gap for gap in result["uncertified_gaps"]}

    assert {"creep", "thermal-shock", "mbar-h"} <= set(gaps)
    for name in ("creep", "thermal-shock", "mbar-h"):
        assert gaps[name]["status"] == UNCERTIFIED
        assert gaps[name]["reason"]


def test_cold_skull_active_cooling_is_zero_when_radiation_covers_wall_heat():
    result = thermal_budget_decomposition(
        wall_area_m2=2.0,
        wall_thickness_m=0.10,
        wall_conductivity_W_m_K=0.05,
        wall_inner_solidus_T_C=100.0,
        wall_outer_T_C=99.0,
        T_sky_K=3.0,
        view_factor=1.0,
    )

    cold_skull = result["cold_skull"]

    assert cold_skull["outer_wall_radiative_capacity_kW_per_m2"] > (
        cold_skull["q_to_wall_kW_per_m2"]
    )
    assert cold_skull["cold_skull_cooling_flux_kW_per_m2"] == 0.0
    assert cold_skull["cold_skull_cooling_flux_kW_min"] == 0.0


def test_heat_flow_decomposition_omits_gas_cooling_terms():
    result = thermal_budget_decomposition(
        wall_area_m2=1.0,
        wall_thickness_m=0.10,
        wall_conductivity_W_m_K=1.0,
        wall_inner_solidus_T_C=1050.0,
        wall_outer_T_C=0.0,
        T_sky_K=3.0,
        view_factor=1.0,
        heat_in_kW=100.0,
        feed_sensible_fusion_enthalpy_kW=10.0,
        reaction_disproportionation_enthalpy_kW=20.0,
        product_vapor_enthalpy_kW=5.0,
        melt_T_C=1500.0,
        melt_surface_area_m2=0.1,
    )

    serialized_keys = " ".join(
        list(result["heat_flows_kW"].keys()) + list(result["cold_skull"].keys())
    )

    assert "gas" not in serialized_keys.lower()
    assert "po2" not in serialized_keys.lower()
    assert result["heat_flows_kW"]["net_unallocated"]["value"] is not None


def test_furnace_material_context_marks_missing_conductivity_uncertified():
    context = furnace_material_context("sintered_regolith")

    assert context["conductivity_W_m_K"]["value"] is None
    assert context["conductivity_W_m_K"]["status"] == UNCERTIFIED
    assert context["max_service_T_C"]["status"] == UNCERTIFIED
