from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "wright-okamura-1977-makaopuhi-cooling"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"

# Independent transcription of Table 17's first three electron-microprobe
# columns from the 600 dpi render of physical PDF page 42.
TABLE17_RENDER_GRID = {
    ("SiO2", "68-1-7"): "75.0", ("SiO2", "68-1-14"): "75.1", ("SiO2", "A12-9"): "75.9",
    ("Al2O3", "68-1-7"): "12.6", ("Al2O3", "68-1-14"): "12.6", ("Al2O3", "A12-9"): "11.9",
    ("FeO", "68-1-7"): ".76", ("FeO", "68-1-14"): "1.64", ("FeO", "A12-9"): "1.1",
    ("MgO", "68-1-7"): ".03", ("MgO", "68-1-14"): ".05", ("MgO", "A12-9"): ".05",
    ("CaO", "68-1-7"): ".40", ("CaO", "68-1-14"): ".43", ("CaO", "A12-9"): ".54",
    ("Na2O", "68-1-7"): "3.25", ("Na2O", "68-1-14"): "3.44", ("Na2O", "A12-9"): "3.37",
    ("K2O", "68-1-7"): "5.82", ("K2O", "68-1-14"): "5.64", ("K2O", "A12-9"): "5.99",
    ("TiO2", "68-1-7"): ".45", ("TiO2", "68-1-14"): ".33", ("TiO2", "A12-9"): ".24",
}

# Independent transcription of the six multiphase whole-sample analyses in
# Table 10A from the 600 dpi render of physical PDF page 32.
TABLE10A_SAMPLES = ("M1-1G", "M-3", "M-8", "M-12", "M-18", "M-22")
TABLE10A_RENDER_ROWS = {
    "SiO2": ("50.06", "50.27", "50.29", "50.45", "50.33", "50.09"),
    "Al2O3": ("13.31", "13.50", "13.57", "13.63", "13.59", "13.34"),
    "Fe2O3": ("1.49", "1.41", "1.34", "1.24", "1.23", "1.32"),
    "FeO": ("9.81", "9.90", "9.91", "9.97", "10.01", "9.98"),
    "MgO": ("8.52", "8.14", "7.95", "7.59", "7.97", "8.56"),
    "CaO": ("10.72", "10.84", "10.90", "11.04", "10.95", "10.75"),
    "Na2O": ("2.23", "2.36", "2.29", "2.38", "2.34", "2.29"),
    "K2O": ("0.49", "0.55", "0.54", "0.52", "0.55", "0.53"),
    "H2O": ("0.42", "0.05", "0.07", "0.04", "0.06", "0.05"),
    "TiO2": ("2.60", "2.65", "2.70", "2.74", "2.68", "2.65"),
    "P2O5": ("0.27", "0.27", "0.27", "0.28", "0.26", "0.27"),
    "MnO": ("0.17", "0.17", "0.17", "0.17", "0.17", "0.17"),
    "CO2": ("0.01", "0.01", "0.01", "0.01", "0.01", "0.02"),
    "Cl": ("0.0", "0.01", "0.02", "0.0", "0.01", "0.0"),
    "F": ("0.0", "0.04", "0.04", "0.0", "0.04", "0.0"),
}
TABLE10A_RENDER_GRID = {
    (formula, sample): value
    for formula, values in TABLE10A_RENDER_ROWS.items()
    for sample, value in zip(TABLE10A_SAMPLES, values, strict=True)
}

# The final Table 11 column is missing from the MinerU draft. These cells were
# transcribed independently from the 600 dpi render of physical PDF page 33.
TABLE11_RENDER_GRID = {
    ("SiO2", "69-1-22"): "50.90", ("Al2O3", "69-1-22"): "12.97",
    ("Fe2O3", "69-1-22"): "1.65", ("FeO", "69-1-22"): "11.70",
    ("MgO", "69-1-22"): "5.18", ("CaO", "69-1-22"): "9.38",
    ("Na2O", "69-1-22"): "2.73", ("K2O", "69-1-22"): "0.80",
    ("H2O", "69-1-22"): "0.12", ("TiO2", "69-1-22"): "3.89",
    ("P2O5", "69-1-22"): "0.41", ("MnO", "69-1-22"): "0.20",
    ("CO2", "69-1-22"): "0.0", ("Cl", "69-1-22"): "0.03",
    ("F", "69-1-22"): "0.06",
}

TABLE10A_RATIOS = {
    "M1-1G": ".120", "M-3": ".114", "M-8": ".109",
    "M-12": ".101", "M-18": ".100", "M-22": ".107",
}
TABLE11_RATIO = ".122"


def all_observations(source):
    return [
        (formula, row)
        for formula, body in source["species"].items()
        for row in body.get("observations", [])
    ]


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_render_grids_bind_each_component_and_migrate_with_conditions():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = all_observations(source)
    by_id = {row["observation_id"]: (formula, row) for formula, row in rows}
    assert len(by_id) == len(rows)

    table17 = {
        (formula, row["values"]["sample_id_as_published"]): row["values"]["value"]
        for formula, row in rows
        if row["locator"].get("table") == "17"
    }
    table10a = {
        (formula, row["values"]["sample_id_as_published"]): row["values"]["value"]
        for formula, row in rows
        if row["locator"].get("table") == "10"
        and row["values"].get("quantity") == "equilibrium_phase_composition"
    }
    table11 = {
        (formula, row["values"]["sample_id_as_published"]): row["values"]["value"]
        for formula, row in rows
        if row["locator"].get("table") == "11"
        and row["values"].get("quantity") == "equilibrium_phase_composition"
    }
    assert table17 == TABLE17_RENDER_GRID
    assert table10a == {}
    assert table11 == TABLE11_RENDER_GRID
    table10_context = next(
        row for row in source["species"]["SiO2"]["context"]
        if row["observation_id"] == "wright_okamura_table_10_context"
    )
    context_samples = table10_context["values"]["sample_analyses_as_printed"]
    table10_context_grid = {
        (formula, row["sample_id_as_published"]): value
        for row in context_samples
        for formula, value in row["components_as_printed"].items()
    }
    table10_context_ratios = {
        row["sample_id_as_published"]: row["valence_ratio_as_printed"]
        for row in context_samples
    }
    assert table10_context_grid == TABLE10A_RENDER_GRID
    assert table10_context_ratios == TABLE10A_RATIOS
    assert "typed_glass_samples" not in table10_context["values"]
    assert "whole-sample bulk of a multiphase rock; not a single-phase composition" in table10_context["values"]["disposition_reason"]
    m18_context = next(row for row in context_samples if row["sample_id_as_published"] == "M-18")
    assert m18_context["temperature_as_printed"] == "¹≥1,160 °C (thermocouple reading during collection; Table 16)"
    assert all(row["values"].get("sample_id_as_published") != "M-18" for _, row in rows)
    assert all(row["locator"].get("table") != "10" for _, row in rows)

    typed_components = [
        (formula, row)
        for formula, row in rows
        if row["values"].get("quantity") == "equilibrium_phase_composition"
    ]
    assert len(typed_components) == 39
    assert all(row["values"]["component_as_published"] == formula for formula, row in typed_components)
    assert all(row["phase"] == "glass" for _, row in typed_components)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for _, row in typed_components)
    assert all(row["point_conditions"]["T_C"] for _, row in typed_components)

    table10a_ratios = {
        row["values"]["sample_id_as_published"]: row["values"]["value"]
        for formula, row in rows
        if formula == "Fe" and row["locator"].get("table") == "10"
    }
    table11_ratios = {
        row["values"]["sample_id_as_published"]: row["values"]["value"]
        for formula, row in rows
        if formula == "Fe" and row["locator"].get("table") == "11"
    }
    assert table10a_ratios == {}
    assert table11_ratios == {"69-1-22": TABLE11_RATIO}
    expected_sample_types = {
        "M1-1G": "Glassy skin on lava lake surface",
        "M-3": "Melt dipped from rising lava lake",
        "M-8": "Melt dipped from rising lava lake",
        "M-12": "Melt dipped from rising lava lake",
        "M-18": "Melt dipped from rising lava lake",
        "M-22": "Melt dipped from rising lava lake",
    }
    assert {
        row["sample_id_as_published"]: row["sample_type_as_printed"]
        for row in context_samples
    } == expected_sample_types
    table11_glass = next(
        row for _, row in rows
        if row["locator"].get("table") == "11"
        and row["values"].get("quantity") == "equilibrium_phase_composition"
        and row["values"].get("component_as_published") == "SiO2"
    )
    assert table11_glass["values"]["sample_type_as_printed"] == "Melt in core barrel"
    assert table11_glass["values"]["glass_status_as_printed"] == "glass (quenched melt)"

    index = {
        SID: {
            "corpus": {
                "raw": {"path": str(PDF), "present": True},
                "text": {"path": str(CORPUS / "text" / SID / "pdftotext-layout.txt"), "exists": True},
            }
        }
    }
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SID]
    assert len(migrated) == 40
    assert not result.validation.hard_issues
    assert all(row.value.kind is not ValueKind.UNAVAILABLE for row in migrated)
    source_formula_by_id = {row["observation_id"]: formula for formula, row in rows}
    source_row_by_id = {row["observation_id"]: row for _, row in rows}
    for row in migrated:
        source_id = row.observation_id.split("::", 2)[1]
        source_row = source_row_by_id[source_id]
        assert row.identity.species.formula == source_formula_by_id[source_id]
        assert row.identity.quantity.value in {Quantity.EQUILIBRIUM_PHASE_COMPOSITION, Quantity.VALENCE_RATIO}
        assert "T_C" in row.point_conditions
        assert row.point_conditions["T_C"].locator.page == source_row["point_conditions"]["T_C"]["locator"]["page"]
        assert "quench_state" in row.point_conditions
        expected_temp_tag = source_row["point_conditions"]["T_C"]["state"]["tag"]
        temp_condition = row.point_conditions["T_C"]
        assert temp_condition.state.tag.value == expected_temp_tag
        if expected_temp_tag == "value":
            assert str(temp_condition.state.value) == str(source_row["point_conditions"]["T_C"]["state"]["value"])


def test_every_typed_quantity_has_a_species_formula_and_all_tables_figures_are_audited():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = all_observations(source)
    typed = [(formula, row) for formula, row in rows if row["values"].get("quantity")]
    assert len(typed) == 40
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for _, row in typed)
    assert all(row["point_conditions"]["T_C"] for _, row in typed)
    assert all(row["point_conditions"]["quench_state"] for _, row in typed)
    for _, row in typed:
        table = row["locator"].get("table")
        sample_id = row["values"].get("sample_id_as_published")
        if table == "11":
            assert row["point_conditions"]["T_C"]["locator"]["page"] == 31
            assert row["point_conditions"]["quench_state"]["locator"]["page"] == 57
        elif table == "17":
            assert row["point_conditions"]["T_C"]["locator"]["page"] == 42
            assert row["point_conditions"]["quench_state"]["locator"]["page"] == 42
    source_by_id = {row["observation_id"]: row for _, row in rows}
    for formula, row in typed:
        quantity = row["values"]["quantity"]
        if quantity == "equilibrium_phase_composition":
            assert row["values"]["component_as_published"] == formula
        elif quantity == "valence_ratio":
            assert formula == "Fe"
            assert row["values"]["basis_as_printed"] == ".9 Fe2O3/(.9 Fe2O3 + FeO)"
            assert len(row["derived_from"]) == 2
            inputs = [source_by_id[observation_id] for observation_id in row["derived_from"]]
            assert {item["values"]["component_as_published"] for item in inputs} == {"Fe2O3", "FeO"}
            assert all(item["values"]["sample_id_as_published"] == row["values"]["sample_id_as_published"] for item in inputs)
        else:
            pytest.fail(f"unexpected typed quantity {quantity!r}")

    context = source["species"]["SiO2"]["context"]
    table_contexts = [
        row for row in context
        if row["type"] in {"measured_table_context", "method_context", "model_context"}
    ]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(table_contexts) == 29
    assert len(figures) == 28
    for figure in figures:
        values = figure["values"]
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["series"]
        assert len(values["marker_count_per_series"]) == len(values["series"])
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
