import pytest

from simulator.thermal_budget import (
    ASSUMED,
    CITED,
    UNCERTIFIED,
    evaporation_enthalpy_budget,
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


def test_evaporation_enthalpy_budget_adds_latent_and_dissociation_sinks():
    result = evaporation_enthalpy_budget(
        {"Na": 1.0},
        vapor_pressures={
            "metals": {
                "Na": {
                    "parent_oxide": "Na2O",
                    "molar_mass_g_mol": 22.98976928,
                }
            }
        },
    )

    assert result["latent_kWh"] > 0.0
    assert result["dissociation_kWh"] > 0.0
    assert result["evaporation_thermal_kWh"] == pytest.approx(
        result["latent_kWh"] + result["dissociation_kWh"]
    )
    assert result["energy_scope"] == "electrical_plus_known_evaporation_enthalpy"
    assert result["furnace_heat_status"] == "partial"
    assert result["heat_flows_kWh"]["product_vapor_enthalpy_sink"] == pytest.approx(
        result["latent_kWh"]
    )
    assert "solar_thermal_kWh" not in result
    assert "thermal_total_kWh" not in result
    assert "NIST-JANAF" in result["sources"]["latent:Na"]
    assert "Na2O" in result["sources"]["dissociation:Na"]


def test_evaporation_enthalpy_budget_fails_loud_for_uncited_species():
    with pytest.raises(ValueError, match="missing cited latent"):
        evaporation_enthalpy_budget(
            {"Unobtainium": 1.0},
            vapor_pressures={
                "metals": {
                    "Unobtainium": {
                        "parent_oxide": "UnobtainiumO",
                        "molar_mass_g_mol": 1.0,
                    }
                }
            },
        )


def test_evaporation_enthalpy_budget_oxide_vapor_uses_single_reaction_not_double_charge():
    # SiO forms via the single reaction SiO2(melt) -> SiO(g) + 1/2 O2.  It must
    # NOT be charged metal latent (337.60) PLUS full SiO2->Si+O2 dissociation
    # (910.94): that double-counts (~54% high) and routes through elemental Si.
    molar_mass = 44.0849
    result = evaporation_enthalpy_budget(
        {"SiO": 1.0},
        vapor_pressures={
            "oxide_vapors": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "molar_mass_g_mol": molar_mass,
                    "stoich_oxide_per_vapor": 1.362920787587333,
                }
            }
        },
    )

    product_mol = 1.0 * 1000.0 / molar_mass
    expected_reaction_kWh = product_mol * 810.52 / 3600.0
    # No metal latent leg for an oxide vapor.
    assert result["latent_by_species_kWh"]["SiO"] == 0.0
    # Single melt-oxide -> oxide-vapor reaction, booked as the reaction sink.
    assert result["dissociation_by_species_kWh"]["SiO"] == pytest.approx(
        expected_reaction_kWh
    )
    assert result["evaporation_thermal_kWh"] == pytest.approx(expected_reaction_kWh)
    assert "SiO2->SiO(g)" in result["sources"]["oxide_vapor_reaction:SiO"]
    # Regression guard: the old double-charge exceeded 7.8 kWh here.
    assert result["evaporation_thermal_kWh"] < 6.0


def test_evaporation_enthalpy_budget_cro2_uses_single_reaction_not_fail_loud():
    # CrO2 evaporates as a trace flux from Cr-bearing basalts, so it MUST compute
    # (fail-loud crashes real runs).  It uses the single oxidation reaction
    # 1/2 Cr2O3 + 1/4 O2 -> CrO2(g), not metal latent + full Cr2O3 dissociation.
    molar_mass = 83.9948
    result = evaporation_enthalpy_budget(
        {"CrO2": 1.0},
        vapor_pressures={
            "oxide_vapors": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "molar_mass_g_mol": molar_mass,
                    "stoich_oxide_per_vapor": 0.904761167748687,
                }
            }
        },
    )

    product_mol = 1.0 * 1000.0 / molar_mass
    # NIST WebBook SRD 69 / Chase 1998 ΔfH[CrO2(g)]=-75.31 -> reaction +494.54.
    expected_reaction_kWh = product_mol * 494.54 / 3600.0
    assert result["latent_by_species_kWh"]["CrO2"] == 0.0
    assert result["dissociation_by_species_kWh"]["CrO2"] == pytest.approx(
        expected_reaction_kWh
    )
    assert result["evaporation_thermal_kWh"] == pytest.approx(expected_reaction_kWh)
    assert "CrO2(g)" in result["sources"]["oxide_vapor_reaction:CrO2"]


def test_evaporation_enthalpy_budget_fails_loud_for_uncited_oxide_vapor(monkeypatch):
    # An oxide-vapor species with no cited reaction enthalpy must fail loud rather
    # than fall back to a metal double-charge or a fabricated value.
    import simulator.thermal_budget as tb

    monkeypatch.setattr(
        tb, "_OXIDE_VAPOR_SPECIES", frozenset(tb._OXIDE_VAPOR_SPECIES | {"FakeO"})
    )
    with pytest.raises(ValueError, match="missing cited oxide-vapor formation"):
        evaporation_enthalpy_budget(
            {"FakeO": 1.0},
            vapor_pressures={
                "oxide_vapors": {
                    "FakeO": {
                        "parent_oxide": "Fe2O3",
                        "molar_mass_g_mol": 71.8,
                    }
                }
            },
        )


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
