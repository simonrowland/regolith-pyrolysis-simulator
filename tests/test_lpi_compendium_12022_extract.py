"""Pins Meyer (2005) LPI Lunar Sample Compendium 12022 Table 1a/1b cells."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lpi-compendium-12022.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_lpi_12022_tables_and_context_match_the_printed_compendium() -> None:
    document = _document()
    assert document["source_id"] == "lpi-compendium-12022"
    assert document["source"]["year"] == 2005
    assert document["review_status"] == "draft"
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    block = document["species"]["lunar_sample_12022"]
    obs = {row["observation_id"]: row for row in block["observations"]}
    assert set(obs) == {
        "lpi_12022_table1a_chemical_composition",
        "lpi_12022_table1b_chemical_composition",
    }
    assert all(row["type"] == "composition_series" for row in obs.values())
    assert all(row["values"]["method_class"] == "secondary_compilation" for row in obs.values())

    t1a = obs["lpi_12022_table1a_chemical_composition"]["values"]["composition"]
    majors = t1a["wt_percent"]
    assert majors["SiO2"]["Kushiro71"] == "42.33"
    assert majors["SiO2"]["Kushiro71_technique"] == "a"
    assert majors["SiO2"]["LSPET70"] == "36"
    assert majors["SiO2"]["Engel71"] == "43.2"
    assert majors["FeO"]["Kushiro71"] == "22.06"
    assert majors["K2O"]["Weismann75"] == "0.065"
    assert majors["P2O5"]["Engel71"] == "0.13"
    assert "LSPET70" not in majors["P2O5"]
    traces = t1a["traces"]
    assert traces["Cr"]["Kushiro71"] == "3831"
    assert traces["Yb"]["Haskin71"] == "5.34"
    assert traces["Yb"]["Engel71"] == "10"
    assert traces["Hf"]["Weismann75"] == "0.18"
    assert traces["Th"]["Tats71"] == "0.71"
    assert traces["U"]["Tats71"] == "0.198"
    assert traces["Sn"]["unit"] == "ppb"

    t1b = obs["lpi_12022_table1b_chemical_composition"]["values"]["composition"]
    assert t1b["wt_percent"]["SiO2"]["Snyder97"] == "43.2"
    assert t1b["traces"]["Zn"]["Baedecker71"] == "2"
    assert t1b["traces"]["Ga"]["Snyder97"] == "4.24"
    assert t1b["traces"]["Ir"]["Baedecker71"] == "0.09"
    assert t1b["traces"]["Ir"]["unit"] == "ppb"
    assert t1b["traces"]["Tb"]["Snyder97"] == "1.66"
    assert t1b["traces"]["Th"]["Snyder97"] == "0.987"
    assert "Snyder97" not in t1b["traces"]["Ir"]

    ctx = {row["observation_id"]: row for row in block["context"]}
    assert set(ctx) == {
        "lpi_12022_sample_mass_class_and_age",
        "lpi_12022_mineralogical_mode",
        "lpi_12022_carbon_sulfur_nitrogen",
        "lpi_12022_data_figures",
    }
    ident = ctx["lpi_12022_sample_mass_class_and_age"]["values"]
    assert ident["sample_mass_as_printed"] == "1864.3 grams"
    assert ident["intro_age_as_printed"] == "It has been dated at 3.2 b.y."
    assert ident["argon_plateau_age_as_printed"] == "3.18 ±0.04 b.y."
    assert ident["exposure_ages_as_printed"] == "Not reported !"

    mode = ctx["lpi_12022_mineralogical_mode"]["values"]["mode"]
    assert mode["Olivine"]["Neal_et_al_1994"] == "19.5"
    assert mode["Pyroxene"]["Brett_et_al_1971"] == "58.6"
    assert mode["Opaques"]["Neal_et_al_1994"] == "~9"
    assert mode["silica"]["Neal_et_al_1994"] == "0.2"
    assert mode["mesostasis"]["McGee_et_al_1977"] == "1"

    csn = ctx["lpi_12022_carbon_sulfur_nitrogen"]["values"]
    assert "20 ppm carbon and 800 ppm sulfur" in csn["Kaplan_and_Petrowski_1971_as_printed"]
    assert "914 ppm sulfur" in csn["Rees_and_Thode_1972_as_printed"]

    figures = ctx["lpi_12022_data_figures"]
    assert figures["type"] == "figure_record"
    assert figures["values"]["digitisation"] == "deferred to a figure pass"
    assert figures["values"]["numeric_points"] == "none"
    assert {row["id"] for row in figures["values"]["figures_as_printed"]} == {
        "4", "5", "6", "7", "8"
    }

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert len(migrator.result.observations) == 2
    assert not migrator.result.validation.hard_issues
