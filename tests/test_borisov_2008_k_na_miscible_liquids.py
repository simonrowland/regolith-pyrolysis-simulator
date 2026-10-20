"""Borisov (2008): independent render grids and isolated migration pins."""

from __future__ import annotations

import copy
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
SID = "borisov-2008-k-na-miscible-liquids"
EXTRACT_NAME = f"{SID}.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", Path.home() / "Repos" / "regolith-corpus"))
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

# These grids were transcribed independently from 600 dpi renders of the printed tables.
TABLE1_RENDER = tuple(tuple(line.split("|")) for line in """\
CSA6|57.8|6.7|–|35.5
CSA12|53.7|13.3|–|32.9
CSA18|49.7|19.9|–|30.3
CSA25|45.6|26.7|–|27.7
DA|50.2|15.7|10.3|23.7
DAS25|58.5|13.2|8.6|19.7
DAS50|66.8|10.5|6.9|15.8
DAS70|70.4|9.4|6.2|14.1
DAS100|75.0|7.9|5.2|11.9
DAS200|83.0|5.4|3.5|8.1
DAS400|90.8|2.8|1.9|4.5
""".strip().splitlines())
TABLE2_RENDER = tuple(tuple(line.split("|")) for line in """\
3|CSAiNa-3|8.91|3.0|N2S-3|65.3|33.9|–|99.2|79.4|20.8|–|100.3
4|CSAiNa-4|0.68|3.0|N2S-4|65.3|33.9|–|99.2|77.6|22.7|–|100.3
5|CSAiNa-5|6.89|3.0|N2S-5|65.3|33.9|–|99.2|77.8|22.4|–|100.2
6|CSAiNa-6|2.84|3.0|N2S-6|65.3|33.9|–|99.2|78.2|22.6|–|100.8
8|DANa-8, DASiNa-8|10.71|3.0|N2S-8|65.3|33.9|–|99.2|78.8|22.1|–|100.9
13|DANa-13, DASiNa-13|8.92|16.5|N4S-13|82.7|16.9|–|99.6|88.7|11.2|–|99.9
23|DAK-23, DASiK-23*|8.92|16.0|K2S-23|48.9|–|51.1|100.0|88.9|–|10.1|98.9
24|DAK-24, DASiK-24|8.93|16.0|K4S-24|76.3|–|22.2|98.5|85.9|–|13.1|99.0
25|CSAiK-25|8.93|7.5|K4S-25|76.3|–|22.2|98.5|84.3|–|16.0|100.3
26|DANK-26, DASiNK-26|8.93|16.0|NKS-26|75.7|10.1|13.3|99.1|85.7|5.4|7.7|98.8
27|DANK-27, DASiNK-27|10.74|22.0|NKS-27|75.7|10.1|13.3|99.1|80.8|8.5|11.0|100.2
""".strip().splitlines())
TABLE3_RENDER = tuple(tuple(line.split("|")) for line in """\
CSA6Na-3|53.80|6.30|–|33.55|6.69|0.11|–|–|100.34
CSA12Na-3|50.19|12.58|–|31.18|6.78|0.15|–|–|100.73
CSA18Na-3|46.17|18.75|–|28.79|6.87|0.13|–|–|100.58
CSA25Na-3|42.58|25.12|–|26.48|6.84|0.11|–|–|101.02
CSA6Na-4|56.50|6.54|–|34.86|2.08|0.08|–|–|99.99
CSA12Na-4|52.99|13.34|–|32.53|1.85|0.06|–|–|100.71
CSA18Na-4|49.06|19.90|–|30.04|1.62|0.07|–|–|100.63
CSA25Na-4|45.32|26.65|–|27.65|1.33|0.06|–|–|100.94
CSA6Na-5|54.69|6.27|–|33.11|5.71|0.13|–|–|99.78
CSA12Na-5|50.70|12.54|–|30.81|5.76|0.08|–|–|99.82
CSA18Na-5|47.10|18.85|–|28.43|5.84|0.10|–|–|100.22
CSA25Na-5|43.20|25.23|–|25.90|6.07|0.10|–|–|100.41
CSA6Na-6|56.57|6.53|–|34.69|1.33|0.04|–|–|99.11
CSA12Na-6|52.79|13.06|–|32.00|1.77|0.06|–|–|99.62
CSA18Na-6|48.81|19.64|–|29.57|1.75|0.08|–|–|99.77
CSA25Na-6|45.43|26.65|–|26.92|1.51|0.06|–|–|100.50
CSA6K-25|54.64|6.18|–|33.26|–|–|5.73|0.09|99.81
CSA12K-25|50.65|12.33|–|31.05|–|–|5.88|0.08|99.91
CSA18K-25|46.84|18.31|–|28.38|–|–|5.84|0.08|99.38
CSA25K-25|42.56|24.31|–|25.92|–|–|6.43|0.10|99.22
DANa-8|46.93|14.83|9.70|22.28|6.47|0.10|–|–|100.20
DAS25Na-8|53.22|12.01|7.87|17.99|9.52|0.14|–|–|100.60
DAS50Na-8|59.05|9.47|6.18|14.07|11.57|0.13|–|–|100.34
DAS70Na-8|62.14|8.29|5.43|12.36|12.52|0.17|–|–|100.74
DANa-13|47.25|14.86|9.69|22.42|5.58|0.10|–|–|99.80
DAS25Na-1|53.81|12.10|7.91|18.18|8.07|0.12|–|–|100.07
DAS50Na-13|60.02|9.50|6.24|14.22|9.98|0.15|–|–|99.96
DAS100Na-13|66.26|7.14|4.68|10.69|11.02|0.16|–|–|99.79
DAS200Na-13|73.50|4.72|3.16|7.15|11.56|0.12|–|–|100.08
DAK-23|48.17|14.89|9.84|22.65|–|–|5.47|0.09|101.03
DAS100K-23|65.38|6.80|4.58|10.67|–|–|13.01|0.11|100.44
DAS200K-23|71.68|4.70|2.97|7.02|–|–|13.83|0.17|100.20
DAS400K-23|79.04|2.56|1.70|3.89|–|–|13.30|0.26|100.50
DAK-24|48.04|14.78|9.76|22.43|–|–|5.80|0.09|100.81
DAS50K-24|59.64|9.20|6.10|14.03|–|–|11.64|0.14|100.60
DAS100K-24|65.58|6.73|4.47|10.42|–|–|13.32|0.14|100.51
DAS400K-24|79.09|2.30|1.65|3.96|–|–|13.38|0.16|100.37
DANK-26|48.28|14.90|9.84|22.47|2.98|0.06|2.62|0.07|101.10
DAS50NK-26|60.51|9.37|6.23|14.17|4.73|0.10|6.08|0.11|101.09
DAS100NK-26|66.65|6.89|4.56|10.39|5.05|0.11|7.42|0.09|100.97
DAS400NK-26|80.66|2.59|1.65|3.94|4.35|0.11|7.64|0.10|100.83
DANK-27|47.44|14.76|9.79|22.35|3.71|0.11|3.08|0.07|101.13
DAS25NK-27|53.54|11.93|7.92|17.98|4.85|0.07|4.94|0.05|101.16
DAS50NK-27|59.79|9.29|6.17|14.00|5.57|0.09|6.29|0.09|101.11
DAS100NK-27|65.89|6.97|4.55|10.38|5.78|0.10|7.59|0.09|101.15
""".strip().splitlines())
TABLE4_RENDER = tuple(tuple(line.split("|")) for line in """\
Standard 8|0.450|0.055|0.000|1.000|–
DANa-8|0.468|0.063|0.000|0.873|–
DAS25Na-8|0.529|0.092|0.000|0.595|–
DAS50Na-8|0.590|0.112|0.000|0.487|–
DAS70Na-8|0.618|0.121|0.000|0.452|–
Sandard 13|0.450|0.047|0.000|1.000|–
DANa-13|0.473|0.054|0.000|0.871|–
DAS25Na-13|0.538|0.078|0.000|0.603|–
DAS50Na-13|0.601|0.097|0.000|0.487|–
DAS100Na-13|0.665|0.107|0.000|0.440|–
DAS200Na-13|0.736|0.112|0.000|0.421|–
Standard 23|0.450|0.000|0.027|–|1.000
DAK-23|0.485|0.000|0.035|–|0.771
DAS100K-23|0.682|0.000|0.087|–|0.313
DAS200K-23|0.753|0.000|0.093|–|0.292
DAS400K-23|0.826|0.000|0.089|–|0.305
Standard 24|0.450|0.000|0.029|–|1.000
DAK-24|0.485|0.000|0.037|–|0.771
DAS50K-24|0.617|0.000|0.077|–|0.375
DAS100K-24|0.684|0.000|0.089|–|0.325
DAS400K-24|0.827|0.000|0.089|–|0.323
Standard 26|0.450|0.024|0.013|1.000|1.000
DANK-26|0.481|0.029|0.017|0.847|0.759
DAS50NK-26|0.612|0.046|0.039|0.526|0.323
DAS100NK-26|0.678|0.050|0.048|0.489|0.263
DAS400NK-26|0.824|0.043|0.050|0.567|0.255
Standard 27|0.450|0.032|0.016|1.000|1.000
DANK-27|0.473|0.036|0.020|0.885|0.833
DAS25NK-27|0.538|0.047|0.032|0.671|0.515
DAS50NK-27|0.605|0.055|0.041|0.581|0.402
DAS100NK-27|0.670|0.057|0.049|0.557|0.331
""".strip().splitlines())

COLUMNS3 = ("SiO2", "Al2O3", "MgO", "CaO", "Na2O", "Na2O_s.d.", "K2O", "K2O_s.d.", "Total")
COLUMNS4 = ("Sample", "X SiO2", "X Na2O", "X K2O", "γ Na2O", "γ K2O")
DASHES = {"–", "-", "—"}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (container, row)
        for container, block in document["species"].items()
        for row in block.get("observations") or []
    }


def _assert_render_grids(document: dict, table1, table2, table3, table4) -> None:
    observations = _observations(document)
    context = [record for block in document["species"].values() for record in block.get("context") or []]

    for row in table1:
        sample, *cells = row
        expected_components = {oxide: value for oxide, value in zip(("SiO2", "Al2O3", "MgO", "CaO"), cells) if value not in DASHES}
        expected_missing = [oxide for oxide, value in zip(("SiO2", "Al2O3", "MgO", "CaO"), cells) if value in DASHES]
        matches = [record for _, record in observations.values() if record.get("values", {}).get("sample_id") == sample and record["locator"].get("table") == "1"]
        assert len(matches) == len(expected_components)
        for oxide, value in expected_components.items():
            oid = f"borisov2008_t1_{re.sub(r'[^a-z0-9]+', '_', sample.lower()).strip('_')}_{oxide.lower()}"
            container, record = observations[oid]
            assert container == "SiO2"
            assert record["values"]["formula"] == oxide
            assert record["values"]["value"] == value
            assert record["values"]["row_cells_as_published"] == list(row)
            assert record["values"]["composition_wt_pct_as_published"] == expected_components
            assert record["values"]["composition_missing_cells_as_published"] == expected_missing
            assert record["locator"]["row"] == sample

    for row in table2:
        series, sample_labels, neg_f, duration, source, *comp = row
        for stage, values in (("initial", comp[:4]), ("final", comp[4:])):
            components = ("SiO2", "Na2O", "K2O")
            present = {oxide: value for oxide, value in zip(components, values[:3]) if value not in DASHES}
            if series == "23" and stage == "initial":
                reference = next(record for record in context if record["observation_id"] == "borisov2008_t2_series23_initial_source_model")
                assert reference["values"]["row_cells_as_published"] == list(row)
                assert reference["values"]["composition_wt_pct_as_published"] == dict(zip(("SiO2", "Na2O", "K2O", "total"), values))
                continue
            for oxide, value in present.items():
                oid = f"borisov2008_t2_series{series}_{stage}_{oxide.lower()}"
                container, record = observations[oid]
                assert container == "SiO2"
                assert record["values"]["formula"] == oxide
                assert record["values"]["value"] == value
                assert record["values"]["row_cells_as_published"] == list(row)
                assert record["values"]["sample_id"] == source
                assert record["values"]["stage_as_published"] == stage
            assert sum(1 for _, record in observations.values() if record.get("values", {}).get("row_cells_as_published") == list(row) and record.get("values", {}).get("stage_as_published") == stage) == len(present)

    for row in table3:
        sample, *cells = row
        values = dict(zip(COLUMNS3, cells))
        expected = {oxide: values[oxide] for oxide in ("SiO2", "Al2O3", "MgO", "CaO", "Na2O", "K2O") if values[oxide] not in DASHES}
        actual = [record for _, record in observations.values() if record.get("values", {}).get("sample_id") == sample and record["locator"].get("table") == "3"]
        assert len(actual) == len(expected)
        for oxide, value in expected.items():
            oid = f"borisov2008_t3_{re.sub(r'[^a-z0-9]+', '_', sample.lower()).strip('_')}_{oxide.lower()}"
            container, record = observations[oid]
            assert container == "SiO2"
            assert record["values"]["formula"] == oxide
            assert record["values"]["value"] == value
            if oxide in {"Na2O", "K2O"}:
                assert record["uncertainty"]["as_published"] == f"{values[oxide + '_s.d.']} wt%"
            else:
                assert "uncertainty" not in record
            assert record["values"]["row_cells_as_published"] == list(row)
            assert record["values"]["composition_as_published"] == dict(zip(COLUMNS3, cells))
            assert record["point_conditions"]["sample_id"]["state"]["value"] == sample

    for row in table4:
        sample, x_sio2, x_na, x_k, gamma_na, gamma_k = row
        if sample.startswith(("Standard", "Sandard")):
            expected = {oxide: gamma for oxide, gamma in (("Na2O", gamma_na), ("K2O", gamma_k)) if gamma not in DASHES}
            related = [record for record in context if record.get("values", {}).get("source_row_as_published") == sample]
            assert len(related) == len(expected)
            for oxide, gamma in expected.items():
                record = next(item for item in related if item["values"]["formula"] == oxide)
                assert record["values"]["gamma_as_published"] == gamma
                assert record["values"]["row_cells_as_published"] == list(row)
                assert record["values"]["X_SiO2_as_published"] == x_sio2
            continue
        for oxide, gamma in (("Na2O", gamma_na), ("K2O", gamma_k)):
            if gamma in DASHES:
                continue
            oid = f"borisov2008_t4_{re.sub(r'[^a-z0-9]+', '_', sample.lower()).strip('_')}_{oxide.lower()}_gamma"
            container, record = observations[oid]
            assert container == "SiO2"
            assert record["values"]["formula"] == oxide
            assert record["values"]["gamma"] == gamma
            assert record["values"]["row_cells_as_published"] == list(row)
            assert record["point_conditions"]["sample_id"]["state"]["value"] == sample
            assert record["values"]["standard_state_as_published"].startswith("Virtual reference melt with X SiO2 = 0.45")


def _source_table3(text: str) -> list[tuple[str, ...]]:
    start = text.index("Table 3. Compositions of experimental glasses, wt %")
    end = text.index("Note: s.d. is one standard deviation.", start)
    return [tuple(line.split()) for line in text[start:end].splitlines() if len(line.split()) == 10 and line.split()[0] != "Sample"]


def _source_table4(text: str) -> list[tuple[str, ...]]:
    sample = r"(?:Standard|Sandard)\s+\d+|(?:DANa|DAK|DANK|DAS\d+(?:Na|K|NK)?)-\d+"
    value = r"(?:\d+\.\d+|–|-)"
    pattern = re.compile(rf"({sample})\s+({value})\s+({value})\s+({value})\s+({value})\s+({value})\s*$")
    return [tuple(match.groups()) for line in text.splitlines() if (match := pattern.search(line))]


def test_independent_render_grids_bind_every_cell_and_mutations_fail():
    document = _document()
    assert len(TABLE1_RENDER) == 11
    assert len(TABLE2_RENDER) == 11
    assert len(TABLE3_RENDER) == 45
    assert len(TABLE4_RENDER) == 31
    _assert_render_grids(document, TABLE1_RENDER, TABLE2_RENDER, TABLE3_RENDER, TABLE4_RENDER)

    changed_grid = list(TABLE3_RENDER)
    changed_grid[0] = (changed_grid[0][0], "53.81", *changed_grid[0][2:])
    with pytest.raises(AssertionError):
        _assert_render_grids(document, TABLE1_RENDER, TABLE2_RENDER, changed_grid, TABLE4_RENDER)

    changed_extract = copy.deepcopy(document)
    first = _observations(changed_extract)["borisov2008_t3_csa6na_3_na2o"][1]
    first["values"]["value"] = "6.70"
    with pytest.raises(AssertionError):
        _assert_render_grids(changed_extract, TABLE1_RENDER, TABLE2_RENDER, TABLE3_RENDER, TABLE4_RENDER)


def test_independent_render_grids_match_primary_text_when_corpus_is_present():
    if not TEXT.is_file():
        pytest.skip(f"corpus source text absent: {TEXT}")
    text = TEXT.read_text(encoding="utf-8")
    table3 = [tuple(row) for row in TABLE3_RENDER]
    table4 = [tuple(row) for row in TABLE4_RENDER]
    assert _source_table3(text) == table3
    assert _source_table4(text) == table4
    assert "Table 1. Compositions of starting glasses, wt %" in text
    assert "Table 2. Experimental conditions and the compositions of melts" in text


def test_isolated_migration_preserves_species_conditions_quench_and_activity_basis(tmp_path):
    document = _document()
    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    raw = _observations(document)
    migrated = {
        observation.observation_id.split("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(migrated) == set(raw)
    assert len(migrated) == 332

    for observation_id, (container, source_row) in raw.items():
        observation = migrated[observation_id]
        values = source_row["values"]
        expected_formula = values.get("formula") or container
        assert observation.identity.species.formula == expected_formula
        locator = source_row["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        conditions = source_row.get("point_conditions", {})
        if values.get("T_C") is not None:
            expected_t = Decimal(str(values["T_C"])) + Decimal("273.15")
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == expected_t
            assert observation.point_conditions["temperature_K"].state.is_value
            assert observation.point_conditions["temperature_K"].state.value == expected_t
        elif "temperature_K" in conditions:
            assert observation.point_conditions["temperature_K"].state.is_unknown
        if "fO2_log" in conditions:
            if conditions["fO2_log"]["state"]["tag"] == "value":
                f_value = Decimal(conditions["fO2_log"]["state"]["value"])
                assert observation.point_conditions["fO2_log"].state.is_value
                assert observation.point_conditions["fO2_log"].state.value == f_value
            else:
                assert observation.point_conditions["fO2_log"].state.is_unknown
        if "total_pressure_Pa" in conditions:
            if conditions["total_pressure_Pa"]["state"]["tag"] == "value":
                expected_pressure = Decimal(conditions["total_pressure_Pa"]["state"]["value"])
                assert observation.point_conditions["total_pressure_Pa"].state.is_value
                assert observation.point_conditions["total_pressure_Pa"].state.value == expected_pressure
            else:
                assert observation.point_conditions["total_pressure_Pa"].state.is_unknown
        if "composition" in conditions:
            assert observation.point_conditions["composition"].state.is_unknown
        if source_row.get("phase") == "l":
            assert observation.identity.species.phase.value == "l"
            assert source_row["quench_statement"]["preservation"] == "asserted_preserved"

    gammas = [row for _, row in raw.values() if row["type"] == "activity_coefficient"]
    assert len(gammas) == 33
    assert all("standard_state_as_published" in row["values"] for row in gammas)
    assert all(row["derived_from"] for row in gammas)
    assert len([row for row in document["species"]["SiO2"]["context"] if row["type"] == "figure_record"]) == 10
    assert all(row["values"]["numeric_points"] == "none" for row in document["species"]["SiO2"]["context"] if row["type"] == "figure_record")
