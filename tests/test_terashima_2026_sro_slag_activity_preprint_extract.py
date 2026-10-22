"""Migration and render transcription pins for Terashima et al. (2026)."""

from decimal import Decimal
from pathlib import Path
import subprocess

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "terashima-2026-sro-slag-activity-preprint"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
PDF = regolith_corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"

TABLE4 = {
    "Ueda et al.[14]": [
        ("1473", "9.06 × 10^{-18}", "3.94 × 10^{-8}", "1.37 × 10^{-6}", "0.029"),
        ("1523", "1.66 × 10^{-17}", "1.64 × 10^{-7}", "2.67 × 10^{-6}", "0.062"),
        ("1573", "2.93 × 10^{-17}", "6.23 × 10^{-7}", "5.58 × 10^{-6}", "0.112"),
        ("1623", "4.98 × 10^{-17}", "2.18 × 10^{-6}", "2.50 × 10^{-5}", "0.087"),
        ("1673", "8.21 × 10^{-17}", "7.10 × 10^{-6}", "3.95 × 10^{-5}", "0.180"),
        ("1723", "1.31 × 10^{-16}", "2.15 × 10^{-5}", "7.57 × 10^{-5}", "0.285"),
        ("1773", "2.05 × 10^{-16}", "6.14 × 10^{-5}", "1.49 × 10^{-4}", "0.412"),
    ],
    "A-0": [("1873", "4.64 × 10^{-16}", "4.22 × 10^{-4}", "6.74 × 10^{-4}", "0.626")],
}

TABLE6 = {
    "A-0": {"Si": "0", "Sr": None},
    "A-1": {"Si": "2.28 × 10^{-2}", "Sr": "5.09 × 10^{-4}"},
    "A-2": {"Si": "4.46 × 10^{-2}", "Sr": "3.78 × 10^{-4}"},
    "A-3": {"Si": "7.65 × 10^{-2}", "Sr": "2.14 × 10^{-4}"},
}

TABLE7 = {
    "B-1": {"SiO2": "38.13", "CaO": "34.38", "Al2O3": "9.38", "SrO": "9.46", "MgO": "4.50", "MnO": "4.50"},
    "B-2": {"SiO2": "44.50", "CaO": "26.12", "Al2O3": "10.43", "SrO": "9.95", "MgO": "4.50", "MnO": "4.50"},
    "B-3": {"SiO2": "47.32", "CaO": "22.53", "Al2O3": "9.94", "SrO": "11.21", "MgO": "4.50", "MnO": "4.50"},
    "B-4": {"SiO2": "53.19", "CaO": "15.75", "Al2O3": "10.60", "SrO": "11.46", "MgO": "4.50", "MnO": "4.50"},
    "B-5": {"SiO2": "29.36", "CaO": "35.10", "Al2O3": "17.69", "SrO": "8.85", "MgO": "4.50", "MnO": "4.50"},
    "B-6": {"SiO2": "34.27", "CaO": "27.59", "Al2O3": "19.15", "SrO": "9.99", "MgO": "4.50", "MnO": "4.50"},
    "B-7": {"SiO2": "37.57", "CaO": "22.01", "Al2O3": "20.18", "SrO": "11.24", "MgO": "4.50", "MnO": "4.50"},
    "B-8": {"SiO2": "43.91", "CaO": "14.94", "Al2O3": "20.17", "SrO": "11.98", "MgO": "4.50", "MnO": "4.50"},
}

TABLE8 = {
    "B-1": {"Cu": "0.895", "Si": "0.070", "Mn": "0.035"},
    "B-2": {"Cu": "0.868", "Si": "0.103", "Mn": "0.029"},
    "B-3": {"Cu": "0.875", "Si": "0.096", "Mn": "0.029"},
    "B-4": {"Cu": "0.895", "Si": "0.103", "Mn": "0.029"},
    "B-5": {"Cu": "0.920", "Si": "0.041", "Mn": "0.039"},
    "B-6": {"Cu": "0.906", "Si": "0.067", "Mn": "0.027"},
    "B-7": {"Cu": "0.859", "Si": "0.115", "Mn": "0.026"},
    "B-8": {"Cu": "0.877", "Si": "0.093", "Mn": "0.030"},
}

TABLE9 = {
    "B-1": {"Sr": "3.30 × 10^{-6}", "SrO": "0.29"},
    "B-2": {"Sr": "5.65 × 10^{-6}", "SrO": "0.77"},
    "B-3": {"Sr": "8.58 × 10^{-6}", "SrO": "0.93"},
    "B-4": {"Sr": "1.15 × 10^{-5}", "SrO": "1.34"},
    "B-5": {"Sr": "8.19 × 10^{-6}", "SrO": "0.48"},
    "B-6": {"Sr": "4.44 × 10^{-6}", "SrO": "0.34"},
    "B-7": {"Sr": "6.43 × 10^{-6}", "SrO": "0.88"},
    "B-8": {"Sr": "8.29 × 10^{-6}", "SrO": "0.76"},
}


@pytest.fixture(scope="module")
def document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def migrated(document):
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    return migrator.result


def observations(document):
    return [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]


def observation_for(document, table, sample, formula, quantity, temperature=None):
    matches = [
        observation
        for observation in observations(document)
        if observation["locator"].get("table") == str(table)
        and observation["values"].get("sample_id_as_printed") == sample
        and observation["values"].get("formula") == formula
        and observation["values"].get("quantity") == quantity
        and (
            temperature is None
            or str(observation["values"].get("T_K")) == str(temperature)
        )
    ]
    assert len(matches) == 1, (table, sample, formula, quantity, temperature, len(matches))
    return matches[0]


def stored_amount(observation):
    values = observation["values"]
    quantity = values["quantity"]
    if quantity == "equilibrium_phase_composition":
        raw = values["component_amount"]["value"]
    elif quantity == "activity":
        raw = values["activity"]
    else:
        raw = values["gamma"]
    return Decimal(str(raw))


def printed_decimal(text):
    text = text.replace("×", "*").replace(" ", "")
    if "*10^" not in text:
        return Decimal(text)
    mantissa, exponent = text.split("*10^")
    return Decimal(mantissa) * (Decimal(10) ** int(exponent.strip("{}")))


def test_render_transcription_matches_independent_table_grids(document):
    for sample, rows in TABLE4.items():
        for temperature, p_o2, activity, x_sr, gamma in rows:
            expected = {
                ("activity", "Sr"): activity,
                ("equilibrium_phase_composition", "Sr"): x_sr,
                ("activity_coefficient", "Sr"): gamma,
            }
            for (quantity, formula), printed in expected.items():
                observation = observation_for(
                    document, 4, sample, formula, quantity, temperature
                )
                assert Decimal(str(observation["values"]["T_K"])) == Decimal(temperature)
                assert stored_amount(observation) == printed_decimal(printed)
                if sample == "A-0":
                    assert printed_decimal(
                        observation["values"]["table4_pO2_as_published"]
                    ) == printed_decimal(p_o2)
                    assert observation["values"]["table4_pO2_unit_inferred"] == "atm"
                    assert observation["values"]["oxygen_partial_pressure"]["value"] == "4.70e-11"
                    assert observation["values"]["oxygen_partial_pressure"]["units"] == "Pa"
                else:
                    oxygen = observation["values"]["oxygen_partial_pressure"]
                    assert printed_decimal(oxygen["value"]) == printed_decimal(p_o2)
                    assert oxygen["units"] == "atm"
                    assert oxygen["inferred"] is True
                    assert (
                        "A-0 abstract 4.70e-11 Pa / Table 4 4.64e-16 = "
                        "1.013e5 Pa per unit = 1 atm"
                    ) in oxygen["derivation"]

    for sample, components in TABLE6.items():
        for formula, printed in components.items():
            if printed is None:
                continue
            observation = observation_for(document, 6, sample, formula, "equilibrium_phase_composition")
            assert stored_amount(observation) == printed_decimal(printed)
    assert not any(
        observation["locator"].get("table") == "6"
        and observation["values"].get("sample_id_as_printed") == "A-0"
        and observation["values"].get("formula") == "Sr"
        for observation in observations(document)
    )

    for sample, components in TABLE7.items():
        for formula, printed in components.items():
            observation = observation_for(document, 7, sample, formula, "equilibrium_phase_composition")
            assert stored_amount(observation) == Decimal(printed)
    for sample, components in TABLE8.items():
        for formula, printed in components.items():
            observation = observation_for(document, 8, sample, formula, "equilibrium_phase_composition")
            assert stored_amount(observation) == Decimal(printed)
    for sample, components in TABLE9.items():
        for formula, printed in components.items():
            quantity = "equilibrium_phase_composition" if formula == "Sr" else "activity_coefficient"
            observation = observation_for(document, 9, sample, formula, quantity)
            assert stored_amount(observation) == printed_decimal(printed)


def test_every_typed_row_migrates_as_its_printed_point_with_identity_and_conditions(document, migrated):
    assert not migrated.validation.hard_issues
    typed = observations(document)
    assert len(typed) == 119
    assert len(migrated.observations) == len(typed)
    source_prefix = f"{SOURCE_ID}::"
    for raw in typed:
        observation = migrated.observations[source_prefix + raw["observation_id"]]
        assert observation.value.kind.value == "point"
        assert observation.notices == ()
        assert observation.identity.species.formula == raw["values"]["formula"]
        assert observation.value.point == stored_amount(raw)
        assert observation.identity.temperature_K.value == Decimal(str(raw["values"]["T_K"]))
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == Decimal(str(raw["values"]["T_K"]))
        assert temperature.locator.page == raw["locator"]["page"]
        if raw["locator"].get("table") == "4" and raw["values"]["sample_id_as_printed"] != "A-0":
            assert observation.identity.fO2_Pa.is_unknown
            assert "fO2_Pa" not in observation.point_conditions
        else:
            oxygen = observation.point_conditions["fO2_Pa"]
            assert oxygen.state.value.point == Decimal("4.70e-11")
            assert oxygen.locator.page == 2
        if raw["values"]["quantity"] == "equilibrium_phase_composition":
            assert observation.identity.phase_role.value.value == raw["values"]["phase_label"]
            assert observation.identity.subtype.value == raw["values"]["basis"]


def test_context_captures_unscored_tables_and_figures_without_digitized_points(document):
    contexts = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in contexts}
    for table_number in (1, 2, 3, 5):
        table = by_id[f"terashima_2026_table{table_number}_context"]
        assert table["values"]["rows_as_printed"]
        assert table["values"]["reason"]
    residues = by_id["terashima_2026_table5_context"]["values"]["rows_as_printed"]
    assert residues[1][2:4] == ["N.D.", "N.D."]

    oxygen_context = by_id["terashima_2026_table4_oxygen_unit_inference_context"]["values"]
    assert oxygen_context["rows_as_printed"] == [
        ["Ueda et al.[14]", "1473", "9.06 × 10^{-18}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1523", "1.66 × 10^{-17}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1573", "2.93 × 10^{-17}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1623", "4.98 × 10^{-17}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1673", "8.21 × 10^{-17}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1723", "1.31 × 10^{-16}", "atm inferred by A-0 cross-check"],
        ["Ueda et al.[14]", "1773", "2.05 × 10^{-16}", "atm inferred by A-0 cross-check"],
        ["A-0", "1873", "4.64 × 10^{-16}", "4.70e-11 Pa abstract canonical; raw table value retained"],
    ]
    assert "1.013e5 Pa per Table 4 unit = 1 atm" in oxygen_context["reason"]

    gamma = by_id["terashima_2026_table9_gamma_sr_cu_context"]["values"]
    assert len(gamma["rows_as_printed"]) == 8
    assert gamma["rows_as_printed"][0] == ["B-1", "2.13"]
    epsilon = by_id["terashima_2026_table6_interaction_parameter_context"]["values"]
    assert epsilon["value_as_printed"] == "15.1"
    assert epsilon["composition_range_as_printed"] == "0 ≤ mol% Si ≤ 7.65"

    figures = [item for item in contexts if item.get("type") == "figure_context"]
    assert {item["locator"]["figure"] for item in figures} == {str(i) for i in range(1, 8)}
    assert len(figures) == 7
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["species_as_printed"]
        assert all("marker_count" in series for series in values["series"])
        assert not any(key in values for key in ("value", "points", "numeric_points"))
    counts = {item["locator"]["figure"]: [s["marker_count"] for s in item["values"]["series"]] for item in figures}
    assert counts["4"] == [7, 1]
    assert counts["5"] == [4, 0]
    assert counts["6"] == [3, 3, 3, 3, 3, 3]
    assert counts["7"] == [4, 4]


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_table_locators_name_the_physical_pdf_pages():
    for table_number, page in {1: 9, 2: 11, 3: 12, 4: 13, 5: 14, 6: 15, 7: 18, 8: 19, 9: 20}.items():
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"Table {table_number}." in result.stdout
