"""Borisov et al. (2004): table-cell binding and isolated migration pins."""

from __future__ import annotations

import copy
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase
from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
SID = "borisov-2004-tio2-pd-ni-fe-solubilities"
EXTRACT_NAME = f"{SID}.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
CORPUS = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", Path.home() / "Repos" / "regolith-corpus")
)
TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"
OXIDES = ("SiO2", "TiO2", "Al2O3", "MgO", "CaO")

# Independently transcribed from the 600 dpi render of physical PDF page 2.
# Table 1 retains the literal ditto and dash marks as printed.
TABLE1_RENDER = tuple(
    tuple(line.split("|"))
    for line in """\
1|DA-20|0.68*|1301|66|50.73|0.00|15.80|10.59|23.72|100.84|251|18|214|2
2|DAT3-20|"|"|"|49.74|2.23|15.30|10.32|23.24|100.83|262|3|219|5
3|DAT5-20|"|"|"|48.59|4.35|14.79|10.08|22.44|100.25|255|26|244|6
4|DAT10-20|"|"|"|45.90|8.55|14.10|9.59|21.07|99.22|370|5|302|7
5|DAT17-20|"|"|"|42.34|15.99|12.79|8.74|19.19|99.05|466|44|425|9
6|DAT32-20|"|"|"|38.02|24.99|11.29|7.64|17.07|99.00|609|35|609|14
7|DA-21|3.00|1301|64|50.27|0.00|16.06|10.86|23.78|100.97|41.5|1.1|-|-
8|DAT3-21|"|"|"|49.20|2.27|15.53|10.59|23.38|100.97|42.7|0.8|-|-
9|DAT5-21|"|"|"|49.13|4.15|15.09|10.12|22.48|100.98|39.9|3.2|-|-
10|DAT10-21|"|"|"|46.25|8.62|14.31|9.74|21.09|100.02|53.2|1.3|-|-
11|DAT17-21|"|"|"|42.08|16.27|12.98|8.90|19.20|99.42|75.0|3.2|-|-
12|DAT32-21|"|"|"|37.86|25.06|11.52|7.86|17.18|99.48|110.7|1.0|-|-
13|DAT17-6|9.84|1300|64|41.62|17.17|12.52|8.72|18.99|99.02|2.30|0.27|-|-
14|DAT32-6|"|"|"|37.01|26.29|10.95|7.82|17.12|99.18|2.57|0.17|-|-
15|DAT10-7|10.44|1300|65|45.63|9.33|13.91|9.61|20.88|99.36|1.24|0.11|-|-
16|DAT32-7|"|"|"|37.49|26.54|10.98|7.81|17.09|99.92|2.10|0.14|-|-
17|DAT10-4|10.61|1300|50|44.97|9.89|13.81|9.57|20.77|99.01|1.03|0.02|-|-
18|DAT17-4|"|"|"|41.53|16.99|12.74|8.76|18.99|99.01|1.34|0.01|-|-
19|DAT17-1|10.91|1301|46|41.68|16.77|12.70|8.75|19.12|99.01|1.06|0.02|-|-
20|DAT32-17|10.75|1300|68|36.87|26.69|10.93|7.58|16.98|99.05|1.86|0.03|-|-
21|DAT32-11|10.99|1301|67|36.70|27.46|10.66|7.72|17.35|99.88|1.82|0.06|-|-
22|DAT32-5|11.26*|1298|65|37.05|26.99|10.95|7.77|17.33|100.09|1.41|0.05|-|-
23|DAT32-5a|"|"|"|38.22|26.35|11.02|7.20|17.68|100.47|1.48|0.05|-|-
24|DAT32-12|11.47|1300|67|37.85|26.57|10.90|6.78|17.99|100.10|1.55|0.08|-|-
""".strip().splitlines()
)

# Table 2 has 12 rows: numbered samples, two oxide loop blocks, and one printed
# (s.d.) pair per MeO cell. Values remain separated here to check the assay and SD.
TABLE2_RENDER = tuple(
    tuple(line.split("|"))
    for line in """\
1|50.04|0.00|15.57|10.45|23.27|1.62|0.03|100.98|0.0000|0.0129|3.16
2|48.48|2.23|15.23|10.47|22.93|1.65|0.04|100.99|0.0167|0.0132|3.10
3|47.76|4.25|14.93|10.17|22.09|1.64|0.03|100.84|0.0320|0.0132|3.09
4|45.33|8.12|14.10|9.63|20.82|1.76|0.01|99.75|0.0624|0.0145|2.82
5|41.53|15.04|12.79|8.67|18.90|2.07|0.03|99.00|0.1187|0.0175|2.33
6|35.66|26.00|10.91|7.39|16.36|2.73|0.03|99.05|0.2117|0.0237|1.72
7|46.91|0.00|14.52|9.81|21.76|7.95|0.05|100.96|0.0000|0.0664|1.50
8|45.47|2.17|14.26|9.67|21.16|8.21|0.07|100.95|0.0164|0.0690|1.44
9|44.34|3.93|13.88|9.41|20.50|8.57|0.04|100.63|0.0299|0.0727|1.37
10|41.73|8.11|12.91|8.81|19.13|9.34|0.09|100.02|0.0629|0.0806|1.24
11|37.69|14.45|11.80|7.99|17.36|10.47|0.12|99.76|0.1146|0.0924|1.08
12|31.62|25.09|9.66|6.36|14.94|13.26|0.14|100.93|0.2034|0.1196|0.83
""".strip().splitlines()
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations") or []
    }


def _container_for_t1(sample: str) -> str:
    if not sample.startswith("DAT32-") or sample == "DAT32-5a":
        return "SiO2"
    return "TiO2"


def _assert_transcribed_grids(document: dict, table1, table2) -> None:
    observations = _observations(document)
    last_f = last_t = last_h = None
    for cells in table1:
        no, sample, printed_f, printed_t, printed_h = cells[:5]
        is_ditto = lambda cell: cell in {'"', '“'}
        f_value = last_f if is_ditto(printed_f) else printed_f.rstrip("*")
        temperature = last_t if is_ditto(printed_t) else printed_t
        duration = last_h if is_ditto(printed_h) else printed_h
        last_f, last_t, last_h = f_value, temperature, duration
        composition = dict(zip(OXIDES, cells[5:10]))
        composition["total"] = cells[10]
        expected_container = _container_for_t1(sample)
        oid = f"borisov2004_t1_{int(no):02d}_pd_icp"
        container, obs = observations[oid]
        values = obs["values"]
        assert container == expected_container
        assert obs["locator"]["table"] == "1"
        assert obs["locator"]["row"] == f"{no} {sample}"
        assert values["sample_id"] == sample
        assert values["negative_log_fO2_as_published"] == ('“' if printed_f == '"' else printed_f)
        assert values["T_C"] == temperature
        assert values["T_C_as_published"] == ('“' if printed_t == '"' else printed_t)
        assert values["duration_h_as_published"] == ('“' if printed_h == '"' else printed_h)
        assert values["composition_wt_pct"] == composition
        assert values["composition_total_wt_pct_as_published"] == cells[10]
        assert values["value_as_published"] == cells[11]
        assert values["value"] == cells[11]
        assert obs["uncertainty"]["as_published"] == cells[12]
        assert obs["point_conditions"]["sample_id"]["state"]["value"] == sample
        assert obs["point_conditions"]["fO2_log"]["state"]["value"] == f"-{f_value}"
        assert obs["point_conditions"]["duration_h"]["state"]["value"] == duration
        emp_id = f"borisov2004_t1_{int(no):02d}_pd_emp"
        if cells[13] == "-":
            assert emp_id not in observations
            assert cells[14] == "-"
        else:
            emp_container, emp = observations[emp_id]
            assert emp_container == expected_container
            assert emp["values"]["value_as_published"] == cells[13]
            assert emp["values"]["value"] == cells[13]
            assert emp["uncertainty"]["as_published"] == cells[14]

    for cells in table2:
        no = int(cells[0])
        component = "NiO" if no <= 6 else "FeO"
        local_no = no if no <= 6 else no - 6
        sample = f"{component} loop row {local_no}"
        container_expected = "TiO2" if local_no == 6 else "SiO2"
        comp_id = f"borisov2004_t2_{component.lower()}_row{local_no:02d}_composition"
        gamma_id = f"borisov2004_t2_{component.lower()}_row{local_no:02d}_activity_coefficient"
        comp_container, comp = observations[comp_id]
        gamma_container, gamma = observations[gamma_id]
        composition = dict(zip(OXIDES, cells[1:6]))
        composition[component] = cells[6]
        composition["total"] = cells[8]
        assert comp_container == gamma_container == container_expected
        assert comp["locator"]["table"] == gamma["locator"]["table"] == "2"
        assert comp["locator"]["row"] == gamma["locator"]["row"] == str(no)
        assert comp["values"]["sample_id"] == gamma["values"]["sample_id"] == sample
        assert comp["values"]["composition_wt_pct"] == composition
        assert comp["values"]["composition_total_wt_pct_as_published"] == cells[8]
        assert comp["values"]["value_as_published"] == f"{cells[6]} ({cells[7]})"
        assert comp["uncertainty"]["as_published"] == f"({cells[7]})"
        gvalues = gamma["values"]
        assert gvalues["gamma"] == cells[11]
        assert gvalues["gamma_as_published"] == cells[11]
        assert gvalues["X_TiO2_as_published"] == cells[9]
        assert gvalues["X_MeO_as_published"] == cells[10]
        assert gamma["standard_state"].startswith(f"Pure liquid {component}")
        assert gamma["derived_from"] == [comp_id]


def _normalized_tokens(line: str) -> list[str]:
    return [token.replace("“", '"').replace("”", '"').replace("−", "-") for token in line.split()]


def test_render_transcription_grids_match_primary_text():
    if not TEXT.is_file():
        pytest.skip(f"corpus source text absent: {TEXT}")
    text = TEXT.read_text(encoding="utf-8")
    page = [line for line in text.splitlines()]
    start1 = next(index for index, line in enumerate(page) if "TABLE 1." in line)
    start2 = next(index for index, line in enumerate(page) if "TABLE 2." in line)
    end2 = next(index for index in range(start2 + 1, len(page)) if "Note: MeO" in page[index])
    source_t1 = [
        _normalized_tokens(line)
        for line in page[start1 + 1 : start2]
        if len(line.split()) == 15 and re.match(r"\s*\d+\s+(?:DA|DAT)", line)
    ]
    source_t2 = [
        _normalized_tokens(line)
        for line in page[start2 + 1 : end2]
        if len(line.split()) == 12 and re.match(r"\s*\d+\s+\d", line)
    ]
    expected_t1 = [
        [cell.replace('“', '"') for cell in row]
        for row in TABLE1_RENDER
    ]
    expected_t2 = []
    for row in TABLE2_RENDER:
        expected_t2.append([row[0], *row[1:6], row[6], f"({row[7]})", *row[8:]])
    assert len(source_t1) == len(TABLE1_RENDER) == 24
    assert len(source_t2) == len(TABLE2_RENDER) == 12
    assert source_t1 == expected_t1
    assert source_t2 == expected_t2
    assert "TABLE 1." in page[start1]
    assert "TABLE 2." in page[start2]


def test_independent_render_grid_binds_every_table_cell_and_mutations_fail():
    document = _document()
    _assert_transcribed_grids(document, TABLE1_RENDER, TABLE2_RENDER)
    assert len(TABLE1_RENDER) == 24
    assert len(TABLE2_RENDER) == 12

    changed_grid = [list(row) for row in TABLE1_RENDER]
    changed_grid[0][11] = "252"
    with pytest.raises(AssertionError):
        _assert_transcribed_grids(document, changed_grid, TABLE2_RENDER)

    changed_extract = copy.deepcopy(document)
    _, first_pd = _observations(changed_extract)["borisov2004_t1_01_pd_icp"]
    first_pd["values"]["value"] = "252"
    with pytest.raises(AssertionError):
        _assert_transcribed_grids(changed_extract, TABLE1_RENDER, TABLE2_RENDER)


def test_isolated_migration_preserves_species_conditions_and_activity_standards(tmp_path):
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
    assert len(migrated) == 57

    for observation_id, (container, source_row) in raw.items():
        observation = migrated[observation_id]
        values = source_row["values"]
        expected_formula = values.get("formula") or container
        assert observation.identity.species.formula == expected_formula
        locator = source_row["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        if values.get("T_C") is not None:
            expected_t = Decimal(str(values["T_C"])) + Decimal("273.15")
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == expected_t
            assert observation.point_conditions["temperature_K"].state.value == expected_t
            assert observation.point_conditions["composition"].state.is_value
        elif source_row.get("phase") == "glass":
            assert observation.point_conditions["temperature_K"].state.is_unknown
        if "fO2_log" in source_row.get("point_conditions", {}):
            expected_f = source_row["point_conditions"]["fO2_log"]["state"]["value"]
            assert observation.point_conditions["fO2_log"].state.is_value
            assert observation.point_conditions["fO2_log"].state.value == Decimal(expected_f)
        if source_row.get("phase") == "l":
            assert observation.identity.species.phase.value is Phase.L
            assert observation.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
            assert observation.point_conditions["sample_id"].state.is_value
        if source_row["type"] == "activity_coefficient":
            assert observation.identity.reference_state is not None
            assert observation.identity.reference_state.is_value
            assert observation.derived_from

    figures = [
        row
        for block in document["species"].values()
        for row in block.get("context") or []
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 3
    assert all(row["values"]["numeric_points"] == "none" for row in figures)
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
