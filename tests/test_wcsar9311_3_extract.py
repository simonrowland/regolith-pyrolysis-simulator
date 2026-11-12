"""Source-grounded checks for the WCSAR-TR-AR3-9311-3 design-report extract."""

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/wcsar9311-3.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context() -> list[dict]:
    return _document()["species"]["lunar_maria_regolith"]["context"]


def _row(observation_id: str) -> dict:
    for row in _context():
        if row["observation_id"] == observation_id:
            return row
    raise AssertionError(f"missing {observation_id}")


def test_context_only_extract_preserves_printed_design_numbers_and_defers_figures() -> None:
    document = _document()
    assert document["species"]["lunar_maria_regolith"]["observations"] == []
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    abundances = _row("wcsar9311_3_cited_volatile_concentrations_and_handling_loss")["values"]
    assert abundances["H2_concentration_as_printed"] == "50-60 ppm"
    assert abundances["He3_concentration_as_printed"] == "10−2 ppm"
    assert "20-30%" in abundances["sieving_He_loss_as_printed"]

    conductivity = _row("wcsar9311_3_thermal_conductivity_inputs")["values"]
    assert conductivity["lunar_surface_k_as_printed"] == "10−3 W/m·K at the surface"
    assert conductivity["simulated_soil_k_with_He_as_printed"] == "0.6-0.7 W/m·K at 1 atm pressure"
    assert conductivity["predicted_bed_k_as_printed"] == "1.03 W/m·K"
    assert conductivity["simulated_soil_k_attribution"] == "Horai, 1981"

    heating = _row("wcsar9311_3_heater_design_and_feht_results")["values"]
    assert heating["fig1_result_as_printed"] == "the boundary temperature approaches 900 K in 320 hr"
    assert heating["volatile_release_evidence_class"] == "author_estimate"
    assert "∼ 90%" in heating["volatile_release_estimate_as_printed"]
    assert "only 4% of the N2" in heating["volatile_release_estimate_as_printed"]

    masses = _row("wcsar9311_3_mass_inventory")["values"]
    assert masses["dome_mass_as_printed"] == "80 tonnes"
    assert masses["total_transported_mass_as_printed"] == "∼ 226 tonnes"
    assert masses["field_as_printed"].endswith("produce 10 kg of 3He/yr")

    energy = _row("wcsar9311_3_energy_cost_and_payback")["values"]
    assert energy["transportation_energy_per_kg_as_printed"] == "3.4 × 10^4 GJ/kg of 3He"
    assert energy["core_drill_energy_as_printed"] == "6.3 × 10^{−2} kWh/hole"
    assert energy["operational_energy_total_as_printed"] == "1890 GJ/10 kg of 3He produced, i.e., 189 GJ/kg of 3He"
    assert energy["payback_as_printed"] == "∼ 8.5 MWe·yr/MW·yr (thermal)"
    assert "10^{−4}%" in energy["discussion_comparators"]["soil_lifted_fraction_as_printed"]

    transmission = _row("wcsar9311_3_gas_flow_model_and_transmission")["values"]
    assert transmission["transmission_equation_as_printed"] == "log λ = −1.11 d (cm)"
    assert transmission["transmission_probability_as_printed"] == "∼ 10^{−111}/m"
    assert "(2 − 5) × 10^3 Pa" in transmission["fig3_4m_as_printed"]

    figures = [row for row in _context() if row["type"] == "figure_record"]
    assert [row["locator"]["figure"] for row in figures] == ["1", "2", "3"]
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert figure["values"]["numeric_points"] == "none"
        assert "points" not in figure["values"]
    assert figures[0]["values"]["printed_text_result"] == "the boundary temperature approaches 900 K in 320 hr"
    assert figures[0]["values"]["x_ticks_labelled"] == [0.0, 80.0, 160.0, 240.0, 320.0, 400.0]


def test_digitising_figure1_breaks_the_context_only_pin() -> None:
    document = _document()
    fig1 = next(
        row
        for row in document["species"]["lunar_maria_regolith"]["context"]
        if row["observation_id"] == "wcsar9311_3_figure1"
    )
    fig1["values"]["numeric_points"] = ["900"]
    with pytest.raises(AssertionError):
        assert fig1["values"]["numeric_points"] == "none"


def test_isolated_migration_has_no_scored_observations_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 10
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
