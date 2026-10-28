from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).parents[1]
    / "data/literature/extracts/van-limpt-2006-sodium-evaporation.yaml"
)


def test_van_limpt_table_values_and_duplicate_mass_balance_are_preserved() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    context = extract["species"]["Na"]["context"]
    diffusion = next(row for row in context if row["observation_id"].endswith("table_i_gas_diffusion_coefficients"))
    mass_balance = next(row for row in context if row["observation_id"].endswith("table_ii_mass_balance_context"))

    assert diffusion["locator"]["page"] == 4
    assert diffusion["values"]["rows"][0]["D_NaOH_air"] == 3.12e-4
    assert diffusion["values"]["rows"][1]["D_NaOH_combustion_atmosphere"] == 2.73e-4
    assert mass_balance["locator"]["page"] == 7
    assert mass_balance["values"]["duplicate_source_observation_id"] == "van_limpt_2007_table44_sodium_mass_balance"
    assert mass_balance["values"]["rows"][4]["ratio_extractive_to_gravimetric"] == 1.00


def test_van_limpt_figures_are_inventory_only_and_have_physical_locators() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row
        for species in extract["species"].values()
        for row in species["context"]
        if row["type"] == "figure_record"
    ]

    assert [row["locator"]["figure"] for row in figures] == ["3", "4", "5", "6", "7", "8", "9"]
    assert [row["locator"]["page"] for row in figures] == [6, 6, 8, 8, 8, 8, 9]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])
