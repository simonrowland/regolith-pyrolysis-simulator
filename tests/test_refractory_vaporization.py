from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.accounting.formulas import parse_formula
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.refractory_vaporization import (
    CongruentVaporizationError,
    refractory_log10_kf,
    refractory_vapor_species,
    refractory_vapor_species_gaps,
    solve_congruent_vaporization,
)


PA_PER_ATM = 101_325.0
PA_PER_BAR = 100_000.0
DATA_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "refractory_vapor_species.yaml"
)
VALIDATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "refractory_vaporization_validation.yaml"
)


@pytest.fixture(scope="module")
def literature() -> dict:
    with VALIDATION_PATH.open() as handle:
        return yaml.safe_load(handle)


@pytest.mark.parametrize(
    "species",
    ["O", "Al", "AlO", "Al2O", "CaO", "MgO", "SiO", "TiO2"],
)
def test_refractory_species_match_held_out_named_janaf_1800k_nodes(
    species: str,
    literature: dict,
) -> None:
    anchor = literature["nist_janaf_named_nodes"]
    expected = anchor["log10_kf"][species]["value"]
    value, range_use = refractory_log10_kf(species, anchor["temperature_K"])
    assert value == pytest.approx(expected, abs=5.0e-13)
    assert range_use.status == "in_range"


def test_alumina_imposed_oxygen_pressure_matches_janaf_equilibrium_algebra(
    literature: dict,
) -> None:
    result = solve_congruent_vaporization(
        "Al2O3",
        1800.0,
        oxygen_mode="imposed",
        imposed_pO2_pa=0.1,
    )
    anchors = literature["nist_janaf_named_nodes"]
    gas = anchors["log10_kf"]
    condensed = anchors["condensed_log10_kf"]["Al2O3"]["value"]
    oxygen_log = math.log10(0.1 / PA_PER_BAR)
    aluminum_activity_log = (-condensed - 1.5 * oxygen_log) / 2.0
    expected_logs = {
        "Al": gas["Al"]["value"] + aluminum_activity_log,
        "AlO": gas["AlO"]["value"] + aluminum_activity_log + 0.5 * oxygen_log,
        "Al2O": gas["Al2O"]["value"] + 2.0 * aluminum_activity_log + 0.5 * oxygen_log,
    }
    for species, expected_log in expected_logs.items():
        assert result.species_pressure_pa(species) == pytest.approx(
            PA_PER_BAR * 10.0**expected_log,
            rel=2.0e-12,
        )
    assert result.surface_pO2_pa == pytest.approx(0.1, rel=2.0e-12)


def test_open_vacuum_alumina_self_buffers_and_closes_mass_flux() -> None:
    result = solve_congruent_vaporization("Al2O3", 1800.0)
    assert result.oxygen_mode == "self_buffered"
    assert result.ambient_pO2_pa == 0.0
    assert result.imposed_pO2_pa is None
    assert 0.0 < result.surface_pO2_pa < result.species_pressure_pa("O")
    assert result.condensed_mass_flux_kg_m2_s == pytest.approx(
        result.total_gas_mass_flux_kg_m2_s,
        rel=2.0e-6,
    )
    assert result.solver_residual_norm < 2.0e-7


def test_alumina_Al_bearing_vapor_crosses_nasa_screen(
    literature: dict,
) -> None:
    anchor = literature["alumina_vacuum_screen"]
    assert anchor["aggregate"] == "total_Al_bearing_vapor"
    target_pa = anchor["pressure_bar"] * PA_PER_BAR
    low_K, high_K = 1650.0, 1900.0
    for _ in range(40):
        midpoint_K = 0.5 * (low_K + high_K)
        pressure_pa = solve_congruent_vaporization(
            "Al2O3", midpoint_K
        ).cation_bearing_surface_pressure_pa
        if pressure_pa < target_pa:
            low_K = midpoint_K
        else:
            high_K = midpoint_K
    crossing_K = 0.5 * (low_K + high_K)
    assert crossing_K == pytest.approx(
        anchor["crossing_temperature_K"],
        abs=anchor["plotted_temperature_tolerance_K"],
    )
    assert anchor["known_missing_species"] in refractory_vapor_species_gaps("Al2O3")


def test_cao_kems_raw_points_are_preserved_and_fit_within_reported_uncertainty(
    literature: dict,
) -> None:
    anchor = literature["cao_reducing_cell_kems"]
    fit = anchor["fitted_log10_pCa_atm"]
    for point in anchor["raw_pCa"]:
        fitted = 10.0 ** (
            fit["intercept"]
            + fit["inverse_temperature_coefficient_K"] / point["temperature_K"]
        )
        assert fitted == pytest.approx(
            point["pressure_atm"],
            rel=anchor["pressure_uncertainty_relative_max"],
        )


def _cao_kems_recession(
    temperature_K: float,
    literature: dict,
) -> float:
    anchor = literature["cao_reducing_cell_kems"]
    fit = anchor["fitted_log10_pCa_atm"]
    screen = anchor["recession_screen"]
    p_ca_pa = 10.0 ** (
        fit["intercept"] + fit["inverse_temperature_coefficient_K"] / temperature_K
    ) * PA_PER_ATM
    ca = parse_formula("Ca").molar_mass_kg_per_mol()
    cao = parse_formula("CaO").molar_mass_kg_per_mol()
    ca_mass_flux = (
        screen["evaporation_coefficient"]
        * p_ca_pa
        * hertz_knudsen_k_kg_s_m2_pa(temperature_K, ca)
    )
    cao_mass_flux = ca_mass_flux * cao / ca
    return cao_mass_flux * 3.6e9 / screen["density_kg_m3"]


def test_cao_kems_pressure_converts_to_held_out_recession_screen(
    literature: dict,
) -> None:
    screen = literature["cao_reducing_cell_kems"]["recession_screen"]
    for point in screen["points"]:
        assert _cao_kems_recession(point["temperature_K"], literature) == pytest.approx(
            point["recession_mm_per_1000h"],
            rel=0.015,
        )


def test_cao_multispecies_model_reduces_old_23x_gap_but_reports_disagreement(
    literature: dict,
) -> None:
    screen = literature["cao_reducing_cell_kems"]["recession_screen"]
    for point in screen["points"]:
        modeled = solve_congruent_vaporization(
            "CaO", point["temperature_K"]
        ).recession_mm_per_1000h(screen["density_kg_m3"])
        underprediction = point["recession_mm_per_1000h"] / modeled
        assert 1.0 < underprediction < 23.0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "open-vacuum JANAF boundary underpredicts reducing-cell KEMS; "
        "the KEMS oxygen boundary is not reported and must not be tuned"
    ),
)
def test_open_vacuum_cao_matches_reducing_cell_kems_if_boundaries_were_equivalent(
    literature: dict,
) -> None:
    screen = literature["cao_reducing_cell_kems"]["recession_screen"]
    modeled = [
        solve_congruent_vaporization("CaO", point["temperature_K"]).recession_mm_per_1000h(
            screen["density_kg_m3"]
        )
        for point in screen["points"]
    ]
    expected = [point["recession_mm_per_1000h"] for point in screen["points"]]
    assert modeled == pytest.approx(expected, rel=0.2)


def test_imposed_oxygen_overrides_self_buffer_and_audits_reservoir_flux() -> None:
    self_buffered = solve_congruent_vaporization("CaO", 1900.0)
    imposed = solve_congruent_vaporization(
        "CaO",
        1900.0,
        oxygen_mode="imposed",
        imposed_pO2_pa=1.0,
    )
    assert imposed.surface_pO2_pa == pytest.approx(1.0)
    assert imposed.imposed_pO2_pa == pytest.approx(1.0)
    assert imposed.ambient_pO2_pa == 0.0
    assert imposed.species_pressure_pa("Ca") < self_buffered.species_pressure_pa("Ca")
    assert imposed.species_pressure_pa("O2") == pytest.approx(1.0)
    assert next(
        item.net_molar_flux_mol_m2_s for item in imposed.species if item.species == "O2"
    ) == pytest.approx(0.0)
    assert next(
        item.net_molar_flux_mol_m2_s for item in imposed.species if item.species == "O"
    ) > 0.0
    assert math.isfinite(imposed.external_oxygen_atom_flux_mol_m2_s)
    assert imposed.external_oxygen_atom_flux_mol_m2_s != 0.0


def test_imposed_cao_obeys_dilute_oxygen_equilibrium_exponent() -> None:
    low = solve_congruent_vaporization(
        "CaO", 1900.0, oxygen_mode="imposed", imposed_pO2_pa=1.0e-4
    )
    high = solve_congruent_vaporization(
        "CaO", 1900.0, oxygen_mode="imposed", imposed_pO2_pa=1.0e-2
    )
    assert high.species_pressure_pa("Ca") / low.species_pressure_pa("Ca") == pytest.approx(
        0.1,
        rel=2.0e-12,
    )
    assert high.species_pressure_pa("CaO") == pytest.approx(
        low.species_pressure_pa("CaO"),
        rel=2.0e-12,
    )


def test_oxygen_boundary_inputs_refuse_ambiguous_or_nonphysical_combinations() -> None:
    with pytest.raises(ValueError, match="open vacuum only"):
        solve_congruent_vaporization("Al2O3", 1800.0, ambient_pO2_pa=1.0e-9)
    with pytest.raises(ValueError, match="must be zero"):
        solve_congruent_vaporization(
            "Al2O3",
            1800.0,
            oxygen_mode="imposed",
            ambient_pO2_pa=1.0e-9,
            imposed_pO2_pa=1.0,
        )
    with pytest.raises(ValueError, match="finite and positive"):
        solve_congruent_vaporization(
            "Al2O3",
            1800.0,
            oxygen_mode="imposed",
            imposed_pO2_pa=0.0,
        )


def test_t406_range_extension_reports_input_distances_without_false_loss_claim() -> None:
    value, range_use = refractory_log10_kf("AlO", 2400.0)
    assert value == pytest.approx(2.152666666666667)
    assert range_use.status == "extrapolated_input_projection_within_t406_distance_bounds"
    assert range_use.within_t406_input_distance_bounds
    assert range_use.source_temperature_max_K == 2300.0
    projected = solve_congruent_vaporization("Al2O3", 2400.0)
    assert "model_limited_extrapolation" in projected.certification_blockers

    _, far_use = refractory_log10_kf("AlO", 1000.0)
    assert far_use.status == "extrapolated_model_limited"
    assert not far_use.within_t406_input_distance_bounds
    assert far_use.delta_temperature_K == pytest.approx(500.0)
    assert far_use.delta_inverse_temperature_K_inv > 2.0e-4


@pytest.mark.parametrize("material", ["Al2O3", "CaO", "MgO", "SiO2", "TiO2"])
@pytest.mark.parametrize("temperature_K", [1500.0, 1800.0, 2300.0, 2400.0])
def test_self_buffered_root_is_unique_finite_and_physical_across_grid(
    material: str,
    temperature_K: float,
) -> None:
    result = solve_congruent_vaporization(material, temperature_K)
    assert math.isfinite(result.surface_pO2_pa)
    assert result.surface_pO2_pa > 0.0
    assert result.solver_residual_norm < 2.0e-7
    assert result.self_buffered_root_count == 1
    assert all(
        math.isfinite(item.partial_pressure_pa) and item.partial_pressure_pa >= 0.0
        for item in result.species
    )


@pytest.mark.parametrize(
    ("material", "minimum_species"),
    [
        ("Al2O3", {"Al", "AlO", "Al2O", "O", "O2"}),
        ("CaO", {"Ca", "CaO", "O", "O2"}),
        ("MgO", {"Mg", "MgO", "O", "O2"}),
        ("SiO2", {"Si", "Si2", "Si3", "SiO", "SiO2", "O", "O2"}),
        ("TiO2", {"Ti", "TiO", "TiO2", "O", "O2"}),
        ("MgAl2O4", {"Mg", "MgO", "Al", "AlO", "Al2O", "O", "O2"}),
        ("CaAl2O4", {"Ca", "CaO", "Al", "AlO", "Al2O", "O", "O2"}),
        ("CaAl4O7", {"Ca", "CaO", "Al", "AlO", "Al2O", "O", "O2"}),
        ("CaAl12O19", {"Ca", "CaO", "Al", "AlO", "Al2O", "O", "O2"}),
        ("Ca3Al2O6", {"Ca", "CaO", "Al", "AlO", "Al2O", "O", "O2"}),
        ("Ca12Al14O33", {"Ca", "CaO", "Al", "AlO", "Al2O", "O", "O2"}),
    ],
)
def test_refractory_phase_species_registry_covers_required_carriers(
    material: str,
    minimum_species: set[str],
) -> None:
    assert minimum_species <= set(refractory_vapor_species(material))


@pytest.mark.parametrize(
    "material",
    ["MgAl2O4", "CaAl2O4", "CaAl4O7", "CaAl12O19", "Ca3Al2O6", "Ca12Al14O33"],
)
def test_unresolved_multication_phases_refuse_instead_of_inventing_activities(
    material: str,
) -> None:
    with pytest.raises(CongruentVaporizationError, match="refused"):
        solve_congruent_vaporization(material, 1800.0)


def test_unavailable_complex_species_are_exposed_not_silently_dropped() -> None:
    assert refractory_vapor_species_gaps("Al2O3") == ("Al2O3",)
    assert refractory_vapor_species_gaps("SiO2") == ("Si2O2",)
    assert refractory_vapor_species_gaps("MgAl2O4") == ("Al2O3", "MgAlO")
    assert refractory_vapor_species_gaps("CaAl2O4") == ("Al2O3", "CaAlO")


def test_results_are_typed_as_included_carrier_sum_not_total_bound() -> None:
    alumina = solve_congruent_vaporization("Al2O3", 1800.0)
    assert alumina.evaporation_coefficient == 1.0
    # alpha=1 bounds only included carriers; Al2O3(g) is deliberately omitted.
    assert alumina.flux_classification == "included_carrier_equilibrium_effusion_sum"
    assert "Al2O3" in alumina.unmodeled_species
    assert alumina.certification_status == "provisional_incomplete_species"
    assert alumina.transport_applicability == "requires_external_Knudsen_number"
    silica = solve_congruent_vaporization("SiO2", 1800.0)
    assert silica.certification_status == "provisional_incomplete_species_and_source_conflict"
    assert silica.source_conflicts
    assert "Si2O2" in silica.unmodeled_species
    extrapolated = solve_congruent_vaporization("Al2O3", 2400.0)
    assert set(extrapolated.certification_blockers) == {
        "provisional_incomplete_species",
        "model_limited_extrapolation",
    }


def test_brentq_nonconvergence_raises_typed_congruent_error(monkeypatch) -> None:
    def _force_nonconvergence(*_args, **_kwargs):
        raise RuntimeError("Failed to converge after 200 iterations.")

    monkeypatch.setattr(
        "simulator.refractory_vaporization.brentq",
        _force_nonconvergence,
    )
    with pytest.raises(CongruentVaporizationError, match="root solver failed") as info:
        solve_congruent_vaporization("Al2O3", 1800.0)
    message = str(info.value)
    assert "Al2O3" in message
    assert "1800" in message
    assert isinstance(info.value.__cause__, RuntimeError)


def test_every_runtime_thermo_grid_has_inline_source_and_range() -> None:
    with DATA_PATH.open() as handle:
        data = yaml.safe_load(handle)
    kinetic = data["kinetic_model"]
    assert kinetic["classification"] == "included_carrier_equilibrium_effusion_sum"
    assert "NOT an absolute upper bound" in kinetic["note"]
    for section in ("gas_species", "condensed_phases"):
        for entry in data[section].values():
            assert entry["source"]
            assert entry["source_url"].startswith("https://")
            assert len(entry["valid_temperature_K"]) == 2
            points = sorted(float(key) for key in entry["log10_kf"])
            assert points[0] == entry["valid_temperature_K"][0]
            assert points[-1] == entry["valid_temperature_K"][1]
            assert all(math.isfinite(float(value)) for value in entry["log10_kf"].values())
    for entry in data["materials"].values():
        assert entry["certification_status"]
        for candidate in entry.get("screening_candidates", {}).values():
            assert candidate["status"]
            assert candidate["evidence"]


def test_held_out_literature_sidecar_is_separate_and_provenanced(
    literature: dict,
) -> None:
    assert VALIDATION_PATH != DATA_PATH
    assert "nist_janaf_named_nodes" in literature
    assert literature["alumina_vacuum_screen"]["url"].startswith("https://")
    assert literature["cao_reducing_cell_kems"]["doi"].startswith("10.")
    screen = literature["cao_reducing_cell_kems"]["recession_screen"]
    # Derived recession points depend linearly on density; without a recoverable
    # title/edition/page the screen must stay evidence-class-downgraded.
    assert screen["density_citation_status"] == "uncited"
    assert screen["evidence_class"] == "derived_screen_uncited_density"
    assert "uncited" in screen["classification"]
