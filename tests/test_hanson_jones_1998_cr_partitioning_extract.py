from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "hanson-jones-1998-am-cr-partitioning-olivine-liquid"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

# Independent physical-page 600 dpi transcription of Table 3. The table has two
# measured phases, fourteen sample columns, and these printed component cells.
# A printed dash/limit is preserved as text and never turned into a point value.
T3_COMPONENTS = {
    "glass": ["SiO2", "TiO2", "Al2O3", "MgO", "MnO", "FeO", "CaO", "Na2O", "Cr2O3", "Sc2O3", "V2O3"],
    "olivine": ["SiO2", "TiO2", "Al2O3", "MgO", "MnO", "FeO", "CaO", "Cr2O3", "Sc2O3", "V2O3"],
}
T3_GRID = {
    "glass": {
        "PPG-1": "48.8(3)|0.67(2)|14.69(7)|8.82(3)|na|14.22(7)|12.34(3)|na|0.046(2)|0.543(2)|0.218(2)",
        "PPG-2": "49.3(2)|0.65(2)|14.38(6)|9.03(4)|na|13.9(1)|12.02(4)|na|0.053(2)|0.552(4)|0.225(4)",
        "YG-54": "45.6(3)|4.80(2)|10.70(9)|7.24(2)|0.27(5)|19.7(1)|10.74(2)|0.36(1)|0.078(2)|0|0.375(4)",
        "BG-54": "40.2(2)|11.80(4)|7.57(5)|7.68(6)|0.62(5)|24.7(1)|6.57(9)|0.29(1)|0.070(4)|0|0",
        "YG-56": "44.3(3)|3.89(4)|9.43(9)|9.46(4)|0.31(4)|21.85(7)|9.3(1)|0.31(1)|0.135(5)|0|0.417(4)",
        "YBG-58": "44.2(2)|3.83(1)|9.30(6)|9.89(3)|0.31(5)|21.5(2)|9.43(4)|0.32(1)|0.129(6)|0|0.422(3)",
        "Morb1": "52.8(1)|1.15(2)|14.3(2)|8.34(5)|0|9.45(5)|12.4(1)|1.05(7)|0.087(5)|0|0.164(3)",
        "YG-51a": "45.1(2)|4.09(3)|10.23(7)|7.64(5)|0.41(8)|20.4(1)|10.62(4)|0.34(3)|0.080(4)|0|0.373(5)",
        "YG-51b": "45.3(2)|4.12(3)|10.35(4)|7.55(6)|0.46(5)|20.4(1)|10.81(4)|0.34(1)|0.079(6)|0|0.374(1)",
        "BG-49": "40.0(2)|11.47(4)|7.69(4)|7.87(2)|0.4(6)|24.7(1)|6.76(4)|0.30(1)|0.077(3)|0|0",
        "PPG-3": "48.78(6)|0.646(8)|13.27(5)|9.64(3)|0.20(2)|13.8(2)|11.65(3)|<0.02|0.23(1)|1.369(6)|0.640(9)",
        "PPG-4": "48.9(3)|0.65(1)|14.75(7)|9.04(1)|0.19(2)|13.64(9)|11.98(4)|0.02|0.247(9)|0.550(5)|0.219(5)",
        "PPG-5d": "50.1(2)|0.68(2)|15.1(1)|8.17(5)|0.2(1)|13.4(2)|12.44(9)|0.21(7)|0.32(2)|0|0",
        "PPG-7a": "49.5(1)|0.65(1)|14.4(1)|8.84(4)|0.20(1)|13.7(1)|12.02(3)|0.015(5)|0.26(2)|0.551(6)|0.227(5)",
    },
    "olivine": {
        "PPG-1": "39.1(4)|0.010(9)|0.08(1)|40.3(3)|na|20.4(2)|0.26(1)|0.049(3)|0.099(1)|0.017(4)",
        "PPG-2": "39.2(2)|0.006(4)|0.08(2)|40.8(2)|na|19.7(2)|0.27(2)|0.050(3)|0.099(2)|0.016(2)",
        "YG-54": "37.6(1)|0.06(2)|0.050(4)|34.0(1)|na|27.1(1)|0.21(5)|0.047(5)|0|0.026(3)",
        "BG-54": "37.34(5)|0.36(9)|0.038(7)|33.4(1)|na|27.8(2)|0.05(1)|0.031(6)|0|0",
        "YG-56": "38.1(2)|0.07(7)|0.05(1)|36.7(2)|na|23.9(3)|0.08(1)|0.076(5)|0|0.029(6)",
        "YBG-58": "38.3(1)|0.05(2)|0.055(4)|37.4(1)|na|23.2(2)|0.29(3)|0.076(5)|0|0.022(4)",
        "Morb1": "40.1(7)|0.016(6)|0.06(1)|44.4(4)|0|15.0(2)|0.36(2)|0.085(6)|0|0.014(3)",
        "YG-51a": "37.5(2)|0.10(2)|0.049(4)|34.4(2)|na|26.7(1)|0.37(2)|0.060(7)|0|0.032(3)",
        "YG-51b": "37.8(2)|0.04(1)|0.05(1)|33.9(1)|na|27.2(1)|0.306(4)|0.047(4)|0|0.022(2)",
        "BG-49": "37.1(1)|0.31(8)|0.036(3)|33.7(1)|na|27.7(1)|0.168(6)|0.042(5)|0|0",
        "PPG-3": "37.5(4)|<0.02|0.12(1)|40.0(2)|0.20(2)|20.2(2)|0.28(1)|0.189(9)|0.338(6)|0.346(6)",
        "PPG-4": "38.1(2)|<0.02|0.09(2)|38.5(2)|0.22(2)|21.9(2)|0.29(4)|0.23(1)|0.109(7)|0.148(9)",
        "PPG-5d": "38.2(2)|0.08(6)|0.08(6)|39.1(6)|0.23(1)|20.87(3)|0.28(4)|0.33(2)|0|0",
        "PPG-7a": "38.2(2)|0.07(1)|0.07(1)|39.2(1)|0.21(2)|20.8(1)|0.26(2)|0.25(1)|0.100(5)|0.136(7)",
    },
}
T3_META = {
    "PPG-1": (1185, "QFM", 6), "PPG-2": (1200, "QFM", 6), "YG-54": (1160, "QFM", 6),
    "BG-54": (1160, "QFM", 6), "YG-56": (1225, "QFM", 6), "YBG-58": (1230, "QFM", 6),
    "Morb1": (1175, "QFM", 6), "YG-51a": (1175, "QFM", 7), "YG-51b": (1175, "QFM", 7),
    "BG-49": (1175, "QFM", 7), "PPG-3": (1225, "IW-1", 7), "PPG-4": (1200, "IW-1", 7),
    "PPG-5d": (1185, "IW-1", 7), "PPG-7a": (1210, "IW-1", 7),
}


def load_extract():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def migrate_extract():
    if not PDF.is_file():
        pytest.skip(f"corpus PDF is unavailable: {PDF}")
    index = {
        SID: {
            "corpus": {
                "raw": {"path": str(PDF), "present": True},
                "text": {"path": str(TEXT), "exists": TEXT.is_file()},
            }
        }
    }
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_table3_render_transcription_binds_each_cell_to_sample_phase_and_conditions():
    source = load_extract()
    for phase, components in T3_COMPONENTS.items():
        for col, component in enumerate(components):
            observation = next(
                row for row in source["species"][component]["observations"]
                if row["observation_id"] == f"hj98_t3_{phase}_{component.lower()}"
            )
            assert observation["phase"] == ("glass" if phase == "glass" else "cr")
            assert observation["phase_as_published"] == phase
            actual = {
                point["sample_id_as_published"]: point["value_as_published"]
                for point in observation["values"]["points"]
            }
            expected = {
                sample: values.split("|")[col]
                for sample, values in T3_GRID[phase].items()
            }
            assert actual == expected
            assert set(actual) == set(T3_META)
            for point in observation["values"]["points"]:
                sample = point["sample_id_as_published"]
                temp, buffer, page = T3_META[sample]
                assert point["phase_as_published"] == phase
                assert point["T_C"] == temp
                assert point["point_conditions"]["fO2_control"]["state"]["value"] == buffer
                assert point["locator"]["page"] == page
                assert point["locator"]["row"] == sample
                assert point["locator"]["column"] == component


def test_hanson_rows_migrate_with_species_temperature_and_printed_oxygen_conditions():
    result = migrate_extract()
    observations = [row for row in result.observations.values() if row.source_id == SID]
    source = load_extract()
    assert len(observations) == 402
    assert not result.validation.hard_issues
    assert all(row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION for row in observations)
    assert all(row.identity.temperature_K.is_value for row in observations)
    assert all("temperature_K" in row.point_conditions for row in observations)
    assert all(any(key in row.point_conditions for key in ("fO2_log", "fO2_control")) for row in observations)

    series_by_id = {
        row["observation_id"]: (formula, row)
        for formula, body in source["species"].items()
        for row in body["observations"]
    }
    for row in observations:
        extract_series_id = row.observation_id.split("::", 2)[1]
        expected_species, source_series = series_by_id[extract_series_id]
        assert row.identity.species.formula == expected_species
        published_phase = source_series.get("phase_as_published", source_series["phase"])
        expected_phase = Phase.GLASS if published_phase == "glass" else Phase.CR
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is expected_phase

    bounded = next(
        point
        for row in source["species"]["Cr2O3"]["observations"]
        if row["observation_id"] == "hj98_t2_glass_cr2o3_by_fugacity"
        for point in row["values"]["points"]
        if point.get("fO2_as_published") == "<=-18"
    )
    assert bounded["value"] == "0.060"
    assert bounded["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"
    assert bounded["fO2_as_published"] == "<=-18"

    unavailable = [
        point
        for body in source["species"].values()
        for row in body["observations"]
        for point in row.get("values", {}).get("points", [])
        if point.get("value") is None
    ]
    assert len(unavailable) == 16
    assert all("value_as_published" in point and point.get("unavailable_reason") for point in unavailable)
    assert all(row.value.kind is not ValueKind.UNAVAILABLE for row in observations)


def test_context_preserves_non_token_table_rows_and_deferred_figure_records():
    source = load_extract()
    context = source["species"]["Cr"]["context"]
    by_id = {row["observation_id"]: row for row in context}
    valid_quantity_tokens = {quantity.value for quantity in Quantity}
    for row in context:
        candidates = row.get("values", {}).get("candidate_tokens", [])
        assert set(candidates).issubset(valid_quantity_tokens)

    t2 = by_id["hj98_t2_partition_coefficients"]["values"]["rows"]
    assert sum(len(row["log_fO2_and_DCr_as_printed"]) for row in t2) == 25
    assert any("<=-18" in value for row in t2 for value in row["log_fO2_and_DCr_as_printed"])

    t3 = by_id["hj98_t3_derived_and_total_rows"]["values"]["rows"]
    assert len(t3) == 14
    assert [row["sample"] for row in t3] == list(T3_META)
    assert [row["DCr"] for row in t3] == [
        "1.05(7)", "0.95(8)", "0.60(6)", "0.44(9)", "0.56(4)", "0.59(5)", "0.98(8)",
        "0.75(9)", "0.59(6)", "0.54(7)", "0.83(6)", "0.94(8)", "1.04(6)", "0.9(1)",
    ]
    assert len(by_id["hj98_t5_cr_valence_fit"]["values"]["rows"]) == 7

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 14
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert "points" not in values
        assert len(values["marker_count_per_series"]) == len(values["series"])
        assert all(isinstance(count, int) and count >= 0 for count in values["marker_count_per_series"])
