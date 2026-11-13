from __future__ import annotations

from pathlib import Path
from decimal import Decimal

import yaml

from simulator.battery.enums import PhaseRole, Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "evans-2009-sulfur-mn-ni-w-xanes"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_xanes_tables_and_deferred_figures_preserve_printed_evidence() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    sulfur = document["species"]["S"]
    context = sulfur["context"]

    def record(observation_id: str) -> dict:
        return next(row for row in context if row["observation_id"] == observation_id)

    table_2a = record("evans_2009_table_2a_mn_glass_compositions")["values"]["rows_as_printed"]
    assert "B310706p1a |" in table_2a
    assert "B020806p6b |" in table_2a
    assert "bdl" in table_2a
    assert "101.5(5)" in table_2a
    table_2b = record("evans_2009_table_2b_w_glass_compositions")["values"]["rows_as_printed"]
    assert "B301205p3b |" in table_2b
    assert "B141106p1a |" in table_2b
    table_2c = record("evans_2009_table_2c_ni_glass_compositions")["values"]["rows_as_printed"]
    assert "B120406p6b |" in table_2c
    assert "B010806p1a |" in table_2c

    typed_vectors = {}
    all_source_points = []
    all_source_observations = []
    for formula, species_data in document["species"].items():
        for observation in species_data["observations"]:
            if observation["type"] != "equilibrium_phase_composition":
                continue
            all_source_observations.append(observation)
            for point in observation["values"]["points"]:
                all_source_points.append(point)
                typed_vectors.setdefault(point["sample"], {})[formula] = point["value_as_published"]

    assert len(all_source_points) == 381
    assert len(all_source_observations) == 24
    assert len(typed_vectors) == 50
    assert all(observation["phase"] == "glass" for observation in all_source_observations)
    assert all(
        observation["values"]["basis"] == "wt_percent"
        for observation in all_source_observations
    )
    assert all(
        observation["values"]["phase_label"] == "melt"
        for observation in all_source_observations
    )
    assert all(
        observation["values"]["phase_label_as_published"] == "glass"
        for observation in all_source_observations
    )
    assert all(
        observation["values"]["point_conditions"]["composition"]["state"]
        == {"tag": "unknown", "reason": "not_published"}
        for observation in all_source_observations
    )
    assert typed_vectors["B030106p2b"]["Si"] == "19.93(8)"
    assert typed_vectors["B030106p2b"]["S"] == "0.224(1)"
    assert typed_vectors["B030106p2b"]["Mn"] == "0.381(3)"
    assert typed_vectors["B301205p3b"]["Si"] == "19.8(1)"
    assert typed_vectors["B111006p2"]["Ca"] == "24.(1)"
    assert typed_vectors["B120406p6b"]["O"] == "41.(1)"
    assert typed_vectors["B220806p4"]["W"] == "2.23(3)"
    assert typed_vectors["B010806p2"]["Ni"] == "0.132(8)"
    expected_sigma = {
        ("B030106p2b", "S"): "0.001",
        ("B301205p3b", "Si"): "0.1",
        ("B111006p2", "Ca"): "1",
        ("B120406p6b", "O"): "1",
    }
    assert all("sigma" in point for point in all_source_points)
    for point in all_source_points:
        key = (point["sample"], point["species_as_printed"])
        if key in expected_sigma:
            assert point["sigma"] == expected_sigma[key]
    assert all(point["run_as_printed"] == point["sample"][:7] for point in all_source_points)
    assert all(
        point["locator"]["page"] in {7, 8}
        and point["locator"]["published_page"] == (6853 if point["locator"]["page"] == 7 else 6854)
        and point["point_conditions"]["T_C"]["state"]["value"] == "1400"
        for point in all_source_points
    )

    # Independent transcription from the 600 dpi rendering of PDF page 10, Table 3.
    table_3 = record("evans_2009_table_3_s_xanes_peak_proportions")["values"]["rows_as_printed"]
    measured = [
        tuple(part.strip() for part in line.split("|"))
        for line in table_3.splitlines()
        if line.strip()
    ]
    assert [(row[0], *row[7:11]) for row in measured] == [
        ("B310706p1", "0.11(3)", "0.34(7)", "0.36(5)", "0.19(2)"),
        ("B310706p2", "0.33(3)", "0.21(5)", "0.44(4)", "0.020(6)"),
        ("B310706p3", "0.26(3)", "0.26(6)", "0.36(4)", "0.11(1)"),
        ("B310706p4", "0.31(3)", "0.19(4)", "0.44(4)", "0.06(1)"),
        ("B310706p6", "0.03(4)", "0.22(7)", "0.06(4)", "0.68(6)"),
        ("B151106p1", "0.10(3)", "0.22(6)", "0.27(5)", "0.41(5)"),
        ("B151106p1N", "0.13(4)", "0.45(10)", "0.40(7)", "0.02(1)"),
        ("B151106p4", "0.19(4)", "0.40(9)", "0.41(6)", "n.a."),
        ("B151106p5", "0.15(4)", "0.43(9)", "0.40(6)", "0.03(1)"),
        ("B151106p6", "0.16(4)", "0.44(10)", "0.40(7)", "n.a."),
        ("B150906p1", "0.14(4)", "0.36(9)", "0.45(7)", "0.05(2)"),
        ("B150906p2", "0.15(3)", "0.38(8)", "0.47(6)", "n.a."),
        ("B150906p3", "0.17(4)", "0.34(8)", "0.46(6)", "0.04(1)"),
        ("B150906p4", "0.22(3)", "0.25(6)", "0.52(6)", "0.01(2)"),
        ("B150906p5", "0.16(3)", "0.34(8)", "0.49(6)", "n.a."),
        ("B141106p1", "0.11(4)", "0.3(1)", "0.49(8)", "n.a."),
        ("B141106p2a", "0.13(3)", "0.24(7)", "0.46(6)", "0.09(2)"),
        ("B141106p2b", "0.07(3)", "0.32(9)", "0.43(8)", "0.10(2)"),
        ("B140906p4", "0.15(3)", "0.38(8)", "0.47(6)", "n.a."),
        ("B140906p4D", "0.29(3)", "0.18(5)", "0.53(5)", "n.a."),
        ("B140906p5", "0.21(3)", "0.33(7)", "0.46(5)", "0.001(4)"),
    ]

    table_1 = record("evans_2009_table_1_run_conditions")["values"]["rows_as_printed"]
    assert "B020806 | Mn | 1–10 | -7.18 | -0.84 | -0.61 | -0.7" in table_1
    assert "B190906 | Ni | 1–10 | -3.09 | -11.88 | -1.52 | 3.3" in table_1
    run_table_rows = [
        [field.strip() for field in line.split("|")]
        for line in table_1.splitlines()
        if line.strip()
    ]
    run_fO2 = {
        fields[0]: fields[3]
        for fields in run_table_rows
    }
    assert len(run_fO2) == 20

    run_phase_evidence = record("evans_2009_table_1_run_conditions")["values"][
        "run_phase_evidence"
    ]
    product = run_phase_evidence["run_product_description"]
    assert product["as_printed"] == (
        "After 24 h the runs were quenched by release of the melt beads into water "
        "so quench was close to instantaneous."
    )
    assert product["locator"] == {
        "page": 3,
        "published_page": 6849,
        "pdf_page_index": 2,
        "section": "2.1. Preparation of synthetic glasses",
    }
    product_runs = product["applies_to_runs"]
    assert [item["run_as_printed"] for item in product_runs] == list(run_fO2)
    assert all(
        item["locator"]["table"] == "1"
        and item["locator"]["page"] == 4
        and item["locator"]["row"] == item["run_as_printed"]
        for item in product_runs
    )

    sulfide = run_phase_evidence["sulfide_phase"]
    assert sulfide["as_printed"] == (
        "and those for the Ni-bearing glasses were meaningless, because the loss of the "
        "immiscible sulphide phase led to a drop in S content to below the CMAS value."
    )
    assert sulfide["locator"] == {
        "page": 19,
        "published_page": 6865,
        "pdf_page_index": 18,
        "section": "4.6. Evidence for formation of metal–S melt species from metal XAS",
    }
    assert sulfide["condition_scope_as_printed"] == (
        "the experiments performed at a log10 fO2 of −9.6 and log10 fS2 of −1.91"
    )
    assert sulfide["condition_scope_locator"] == {
        "page": 18,
        "published_page": 6864,
        "pdf_page_index": 17,
        "section": "4.6. Evidence for formation of metal–S melt species from metal XAS",
        "note": "This sentence continues onto physical page 19.",
    }
    sulfide_runs = [item["run_as_printed"] for item in sulfide["applies_to_runs"]]
    assert sulfide_runs == [
        fields[0]
        for fields in run_table_rows
        if fields[1] == "Ni" and fields[3] == "-9.60" and fields[4] == "-1.91"
    ]
    assert sulfide_runs == [
        "B231205",
        "B220306",
        "B070406",
        "B010806",
        "B140906",
    ]
    assert all(
        item["locator"]["table"] == "1"
        and item["locator"]["page"] == 4
        and item["locator"]["row"] == item["run_as_printed"]
        for item in sulfide["applies_to_runs"]
    )

    run_results = run_phase_evidence["sulfide_phase_run_results"]
    assert run_results["as_printed"] == (
        "B010806 samples contained black specks inferred to be exsolved NiS "
        "while B140906 did not."
    )
    assert run_results["locator"] == {
        "page": 9,
        "published_page": 6855,
        "pdf_page_index": 8,
        "section": "3.2.2. Synthetic silicate glasses",
    }
    assert run_results["results_by_run"] == [
        {
            "run_as_printed": "B010806",
            "result_as_printed": "samples contained black specks inferred to be exsolved NiS",
        },
        {"run_as_printed": "B140906", "result_as_printed": "did not"},
    ]

    series_observations = run_phase_evidence["series_phase_observations"]
    assert [item["as_printed"] for item in series_observations] == [
        "Most of the samples formed translucent clear or yellow-coloured glass beads "
        "up to 5 mm in diameter and 0.05–0.15 g in weight.",
        "A number of samples contained bubbles and/or black specks. In most cases the "
        "black specks occurred on the margins of the sample, or showed dendritic form "
        "in fractures but appeared to be a surface feature, probably produced on quench, "
        "and of insufficient size for analysis. However, in some Ni-bearing samples "
        "specks were distributed through the glass and appeared to define flow patterns; "
        "probe analysis (Evans et al., 2008a, not shown here) revealed that this apparently "
        "exsolved material contained Ni and S with minor Fe.",
        "This is consistent with equilibration of the melt with a Ni sulphide phase, "
        "which was observed to have exsolved from the melt in many of the samples "
        "(Evans et al., 2008a).",
    ]
    assert all(
        item["locator"] == {
            "page": 6,
            "published_page": 6852,
            "pdf_page_index": 5,
            "section": "3.1. Characteristics of synthetic silicate glasses",
        }
        for item in series_observations
    )

    rows_by_run = {
        run: sum(point["run_as_printed"] == run for point in all_source_points)
        for run in run_fO2
    }
    ni_runs = [fields[0] for fields in run_table_rows if fields[1] == "Ni"]
    assert len(ni_runs) == 8
    assert rows_by_run["B010806"] == 30
    assert rows_by_run["B140906"] == 15
    ni_runs_without_sample_specific_speck_result = [
        run for run in ni_runs if run not in {"B010806", "B140906"}
    ]
    assert len(ni_runs_without_sample_specific_speck_result) == 6
    assert sum(
        rows_by_run[run] for run in ni_runs_without_sample_specific_speck_result
    ) == 62
    non_ni_runs = [fields[0] for fields in run_table_rows if fields[1] != "Ni"]
    assert len(non_ni_runs) == 12
    assert sum(rows_by_run[run] for run in non_ni_runs) == 274
    assert sum(rows_by_run.values()) == 381
    assert sum(rows_by_run[run] for run in sulfide_runs) == 76
    assert len(run_fO2) - len(sulfide_runs) == 15
    assert sum(rows_by_run[run] for run in run_fO2 if run not in sulfide_runs) == 305

    for point in all_source_points:
        run = point["run_as_printed"]
        condition = point["point_conditions"]["fO2_log"]
        assert condition["state"]["value"] == run_fO2[run]
        assert condition["units"] == "log10(bar)"
        assert condition["as_printed"] == f"−{run_fO2[run][1:]}"
        assert condition["locator"]["table"] == "1"
        assert condition["locator"]["row"] == run
        assert condition["locator"]["column"] == "Log10 fO2"
    assert run_fO2["B030106"] == "-9.60"
    assert run_fO2["B020806"] == "-7.18"
    assert run_fO2["B111006"] == "-7.59"
    assert run_fO2["B190906"] == "-3.09"

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 11
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["points"] == [] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 381
    assert migrator.result.validation.hard_issues == ()
    migrated = [
        row for row in migrator.result.observations.values()
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(migrated) == 381
    assert all(row.identity.temperature_K.value == Decimal("1673.15") for row in migrated)
    assert all(row.point_conditions["T_C"].state.value == Decimal("1400") for row in migrated)
    assert all(row.locator.page in {7, 8} and row.locator.published_page in {6853, 6854} for row in migrated)
    assert all(row.identity.phase_role.value is PhaseRole.MELT for row in migrated)
    assert all(
        row.point_conditions["composition"].state.is_unknown
        and row.point_conditions["composition"].state.reason == "not_published"
        for row in migrated
    )
    assert all(
        row.point_conditions["fO2_log"].state.is_value
        and row.point_conditions["fO2_log"].locator.table == "1"
        and row.point_conditions["fO2_log"].locator.published_page == 6850
        for row in migrated
    )
    migrated_by_row = {
        row.observation_id.rsplit("row=", 1)[-1].split(":", 1)[0].lower(): row
        for row in migrated
    }
    for sample, row in migrated_by_row.items():
        assert row.point_conditions["fO2_log"].state.value == Decimal(
            run_fO2[sample[:7].upper()]
        )
    for sample, expected_fO2 in {
        "B030106p2b": "-9.60",
        "B020806p1": "-7.18",
        "B111006p2": "-7.59",
        "B190906p5": "-3.09",
    }.items():
        assert (
            migrated_by_row[sample.lower()]
            .point_conditions["fO2_log"]
            .state.value
            == Decimal(expected_fO2)
        )
    sulfur = next(
        row for row in migrated
        if row.identity.species.formula == "S" and row.value.point == Decimal("0.224")
    )
    assert sulfur.uncertainty.verbatim["sigma"] == "0.001"
