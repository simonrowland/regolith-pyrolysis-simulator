from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2023-cai-melt-evaporation.yaml"


def test_shornikov_2023_table_and_deferred_figures_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    contexts = [
        context
        for work_contexts in result.context_by_work.values()
        for context in work_contexts
    ]
    assert len(result.observations) == 0
    assert len(contexts) == 12
    assert not result.validation.hard_issues

    table = next(
        context
        for context in document["species"]["CaO"]["context"]
        if context["observation_id"]
        == "shornikov_2023_table1_initial_compositions"
    )
    rows = table["values"]["rows"]
    assert len(rows) == 4
    assert table["values"]["numeric_cell_count"] == 12
    assert table["locator"]["page"] == 4
    assert [row["oxide"] for row in rows] == ["CaO", "MgO", "Al2O3", "SiO2"]
    # Values were checked against the printed table on PDF page 4 at 600 dpi.
    assert rows[0]["sample_compositions_wt_pct"] == {
        "A": "36.2",
        "B": "27.6",
        "M": "4.4",
    }
    assert rows[1]["sample_compositions_wt_pct"]["A"] == "5.0"
    assert rows[2]["sample_compositions_wt_pct"]["B"] == "32.3"
    assert rows[3]["sample_compositions_wt_pct"]["M"] == "45.9"
    assert all(row["method_class"] == "quoted_attributed" for row in rows)

    cai_contexts = document["species"]["CaO"]["context"]
    modal = next(
        context
        for context in cai_contexts
        if context["observation_id"] == "shornikov_2023_cai_modal_compositions"
    )
    assert modal["locator"]["page"] == 4
    assert modal["locator"]["published_page"] == 9
    assert modal["locator"]["pdf_page_index"] == 3
    assert modal["values"]["type_A"]["other_mineral_formula_as_printed"] == (
        "Ca2(Mg,Al)[(Al.Si)2O7]"
    )

    age = next(
        context
        for context in cai_contexts
        if context["observation_id"] == "shornikov_2023_cai_age_context"
    )
    assert age["locator"]["page"] == 3
    assert age["locator"]["published_page"] == 8
    assert age["values"]["numeric_facts"] == [
        {
            "name": "CAI_age_as_printed",
            "value": 4.567,
            "unit": "billion years",
            "locator": {
                "page": 3,
                "published_page": 8,
                "pdf_page_index": 2,
                "section": "Introduction",
            },
        }
    ]

    method = next(
        context
        for context in cai_contexts
        if context["observation_id"] == "shornikov_2023_experimental_method_context"
    )
    assert method["values"]["studied_sample_as_printed"] == (
        "natural inclusions of the Efremovka meteorite"
    )
    assert method["values"]["studied_meteorite_class_as_printed"] == "CV3"
    assert method["values"]["comparison_sample_as_printed"] == "Murchison chondrite"
    assert method["values"]["comparison_meteorite_class_as_printed"] == "CM2"

    results = next(
        context
        for context in cai_contexts
        if context["observation_id"] == "shornikov_2023_results_summary_context"
    )
    assert results["values"]["temperature_ranges_K_as_printed"] == {
        "silicon_component_remains_in_CAIs": ">2500"
    }
    assert results["values"]["statements"] == [
        "It is possible to notice the predominance of vapor species corresponding to a higher content of calcium and aluminum oxides in the melt in the case of CAIs type A (Fig. 2A), and for the case of CAIs type B – the predominance of vapor species corresponding to a higher content of magnesium and silicon oxides in the melt (Fig. 2B).",
        "The high-temperature evaporation process occurs in an oxygen-enriched atmosphere.",
        "The authors suggest that initial CAI mineral condensation also occurs in an oxygen environment.",
    ]

    murchison = next(
        context
        for context in cai_contexts
        if context["observation_id"]
        == "shornikov_2023_murchison_comparison_context"
    )
    assert murchison["values"]["method_class"] == "author_estimate"
    assert "attribution" not in murchison["values"]
    assert murchison["locator"]["note"] == (
        "This article's comparison of its CAI type A/B curves with the "
        "Murchison curve in Fig. 2C (Murchison data: Yakovlev et al. 1987, PDF p4)."
    )
    assert murchison["values"]["sample_as_printed"] == "Murchison chondrite (CM2)"
    assert murchison["values"]["temperature_ranges_K_as_printed"] == {
        "higher_volatility": "1800–2100",
        "almost_complete_evaporation": "2200–2300",
    }
    assert murchison["values"]["comparison_as_printed"] == (
        "A comparison of the temperature dependencies of partial pressures "
        "of vapor species over CAIs melts of types A and B with that for the "
        "case of Murchison chondrite (Fig. 2C) shows that the latter has a "
        "slightly higher volatility at 1800–2100 K, due to the high content "
        "of magnesium and silicon oxides in the melt (compared with CAIs "
        "types A and B) and almost completely evaporates at 2200–2300 K."
    )

    figure_records = [
        context
        for record in document["species"].values()
        for context in record["context"]
        if context["type"] == "figure_record"
    ]
    assert len(figure_records) == 6
    assert {
        item["observation_id"]: item["values"]["marker_count_per_series"]
        for item in figure_records
    } == {
        "shornikov_2023_fig1_temperature_regimes": {"A": 31, "B": 26, "M": 27},
        "shornikov_2023_fig2_ca_partial_pressure": {"A": 14, "B": 12, "C": 9},
        "shornikov_2023_fig2_mg_partial_pressure": {"A": 12, "B": 11, "C": 13},
        "shornikov_2023_fig2_al_partial_pressure": {"A": 8, "B": 11, "C": 9},
        "shornikov_2023_fig2_sio_partial_pressure": {"A": 16, "B": 20, "C": 10},
        "shornikov_2023_fig3_oxygen_pressure": {"A": 19, "B": 24, "M": 17},
    }
    assert {item["locator"]["figure"] for item in figure_records} == {
        "1",
        "2",
        "3",
    }
    for figure in figure_records:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["quantity"]
        assert values["species_as_printed"]
        assert values["phase_as_printed"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["series_list"]
        series_keys = {
            next(iter(entry))
            if isinstance(entry, dict)
            else entry.split(":", maxsplit=1)[0]
            for entry in values["series_list"]
        }
        assert set(values["marker_count_per_series"]) == {
            str(key) for key in series_keys
        }
        assert all(
            isinstance(count, int) and count > 0
            for count in values["marker_count_per_series"].values()
        )
        assert "points" not in values
