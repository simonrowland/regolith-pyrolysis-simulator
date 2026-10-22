from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root

SOURCE_ID = "nam-2025-cmas-vanadium-thesis"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
SOURCE_PDF = regolith_corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"

# Independent, full-cell transcription from 600 dpi physical-page renders.
# Grid: sample|EPMA phase|analysis count|T_C|composition cells in table column order.
TABLE_RENDER_GRIDS = {
    "6.1": """
1|V2O3|8|1500|1.31 (0.3500);98.69 (0.3500)
1|CaV2O4|8|1500|45.24 (0.2534);54.76 (0.2534)
2|V2O3|8|1100|4.28 (0.7823);95.72 (0.7823)
2|Ca3V2O8|9|1100|74.32 (0.1897);25.68 (0.1897)
3|V2O3|9|1300|3.79 (0.4098);96.21 (0.4098)
3|CaVO3|7|1300|67.39 (0.1761);32.61 (0.1761)
4|CaV2O4|8|1500|49.30 (0.7336);50.70 (0.7336)
4|CaVO3|8|1500|66.6 (0.2454);33.4 (0.2454)
5|CaV2O4|9|1500|46.64 (0.2676);53.36 (0.2676)
6|V2O3|9|1300|1.99 (0.3809);98.01 (0.3809)
6|CaVO3|9|1300|66.67 (0.1913);33.33 (0.1913)
7|CaVO3|10|1500|67.26 (0.1397);32.74 (0.1397)
8|V2O3|9|1100|5.78 (0.4302);94.22 (0.4302)
8|Ca3V2O8|9|1100|72.37 (0.3070);27.63 (0.3070)
9|CaVO3|10|1200|67.28 (0.1554);32.72 (0.1554)
9|Ca3V2O8|9|1200|74.93 (0.2039);25.07 (0.2039)
10|CaVO3|10|1300|67.28 (0.1554);32.72 (0.1554)
10|Ca3V2O8|9|1300|74.93 (0.2039);25.07 (0.2039)
11|CaVO3|9|1400|67.08 (0.1263);32.92 (0.1263)
11|L|10|1400|76.3 (0.1438);23.7 (0.1438)
12|CaVO3|10|1500|66.31 (0.1377);33.69 (0.1377)
12|L|10|1500|74.54 (0.2409);25.46 (0. 2409)
13|Ca3V2O8|9|1300|74.38 (0.4712);25.62 (0.4712)
14|L|9|1350|74.80 (0.7261);25.20 (0.7261)
15|Ca10V6O25|10|1200|76.96 (0.1502);23.04 (0.1502)
16|L|10|1350|77.09 (0.1124);22.91 (0.1124)
16|CaO|10|1350|99.99 (0.0057);0.01 (0.0057)
17|L|10|1400|76.94 (0.3339);23.06 (0.3339)
17|CaO|8|1400|99.99 (0.0084);0.01 (0.0084)
""",
    "7.1": """
1|MgO|5|1200|98.81 (0.2108);1.19 (0.2108)
1|Spinel|7|1200|56.08 (0.2534);43.92 (0.2858)
2|MgO|10|1500|93.55 (0.2108);6.45 (0.1112)
2|Spinel|8|1500|52.44 (0.2534);47.56 (0.2612)
3|Spinel|7|1200|51.69 (0.3362);48.31 (0.3362)
3|V2O3|8|1200|10.88 (0.2880);89.12 (0.2880)
4|Spinel|7|1400|50.12 (0.4284);49.88 (0.4284)
4|V2O3|8|1400|6.09 (0.2534);93.91 (0.2612)
""",
    "8.1": """
1|Al2O3|6|1400|92.84 (0.9893);7.16 (0.9893)
1|V2O3|10|1400|17.24 (0.1532);82.76 (0.1532)
2|Al2O3|5|1500|88.33 (0.6119);11.67 (0.6119)
2|V2O3|7|1500|27.99 (1.7150);78.11 (1.7150)
3|L|12|800|2.96 (0.2061);97.04 (0.2061)
3|Al2O3|8|800|99.37 (0.0466);0.63 (0.0466)
4|L|9|1000|5.11 (0.6193);94.89 (0.6193)
4|Al2O3|8|1000|99.28 (0.0588);0.72 (0.0588)
5|L#1|10|1400|6.06 (0.9148);93.94 (0.9148)
5|L#2|9|1400|6.74 (0.8695);93.26 (0.8695)
5|Al2O3|6|1400|99.33 (0.1230);0.67 (0.1230)
""",
    "9.2": """
1|L|9|1000|0 (0);9.23 (0.4466)
1|SiO2|6|1000|0 (0);99.80 (0.0562)
2|L|24|1200|0 (0);9.61 (0.8494)
2|SiO2|5|1200|0 (0);99.70 (0.0837)
3|L|15|1300|0 (0);11.47 (0.1604)
3|SiO2|10|1300|0 (0);99.73 (0.0684)
4|L|20|1400|0 (0);13.73 (0.2733)
4|SiO2|8|1400|0 (0);99.77 (0.0570)
5|L|10|1600|0 (0);96.33 (0.3153)
5|SiO2|9|1600|0 (0);99.84 (0.0693)
""",
    "10.1": """
1|L|29|1400|9.83 (1.3425);14.34 (1.5993);75.84 (2.6066)
1|M|5|1400|62.95 (0.2670);36.04 (0.2863);1.01 (0.0587)
1|Al2O3|4|1400|99.39 (0.0500);0.03 (0.0240);0.58 (0.0417)
2|L|29|1400|8.66 (1.2392);25.51 (1.8800);65.83 (2.2806)
2|M|8|1400|63.00 (0.3664);36.00 (0.4142);1.00 (0.0831)
3|L|14|1400|11.84 (0.2671);37.79 (0.5440);50.37 (0.7592)
3|M|6|1400|62.80 (0.2245);36.17 (0.2532);1.03 (0.0806)
4|L|15|1400|11.55 (0.3852);47.34 (0.5929);41.11 (0.2807)
4|M|9|1400|61.13 (1.9024);37.80 (1.9556);1.07 (0.1306)
5|L|14|1400|6.01 (0.0297);81.84 (0.1051);12.15 (0.1122)
5|M|8|1400|61.12 (1.4382);38.04 (1.5693);0.825 (0.1508)
6|L#1|19|1400|8.40 (0.1577);37.98 (0.9695);53.62 (0.8287)
6|L#2|15|1400|6.12 (0.2064);53.21 (1.8119);40.67 (1.6495)
6|SiO2|8|1400|0.05 (0.0084);99.65 (0.0466);0.30 (0.0437)
7|L|20|1400|7.23 (0.0754);71.18 (0.3120);21.59 (0.2748)
7|SiO2|10|1400|0.04 (0.0247);99.72 (0.0341);0.23 (0.0199)
""",
    "10.3": """
8|Sp1|12|1500|52.00 (1.0580);35.41 (1.1841);12.59 (0.7014)
8|Sp2|8|1500|51.78 (0.7480);23.98 (2.7783);24.24 (3.2057)
8|Mono|4|1500|95.20 (0.2299);1.67 (0.1858);3.13 (0.0890)
9|Sp1|10|1500|48.47 (0.5993);24.28 (2.6007);27.25 (2.7759)
9|Sp2|7|1500|49.56 (0.4236);35.90 (1.7936);14.54 (1.5381)
10|Sp|6|1500|1.69 (0.0597);22.54 (0.1861);75.77 (0.1945)
10|Cor1|9|1500|1.12 (0.8603);85.79 (1.4098);13.09 (1.0218)
10|Cor2|11|1500|32.25 (0.4035);52.24 (0.3492);15.51 (0.5175)
11|Sp|10|1500|46.04 (0.3161);19.83 (0.1356);34.13 (0.2574)
11|Cor|10|1500|3.19 (0.1677);6.12 (0.3003);90.70 (0.4242)
12|Sp|17|1500|44.75 (0.3064);35.43 (0.2646);19.82 (0.1932)
12|Cor|9|1500|2.42 (0.0674);12.48 (0.0530);85.10 (0.0939)
13|Sp|10|1500|50.81 (0.5166);30.86 (0.7963);18.33 (1.2082)
13|MgSiO3|-|1500|-;-;-
""",
}
FORMULAS = {
    "6.1": ("CaO", "V2O3"), "7.1": ("MgO", "V2O3"), "8.1": ("Al2O3", "V2O3"),
    "9.2": ("SiO2", "V2O3"), "10.1": ("Al2O3", "SiO2", "V2O5"),
    "10.3": ("MgO", "Al2O3", "V2O3"),
}


@pytest.fixture(autouse=True)
def _require_source_pdf() -> None:
    if not SOURCE_PDF.is_file():
        pytest.skip(f"source PDF missing: {SOURCE_PDF}")


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_grid(document: dict, table: str) -> list[str]:
    formulas = FORMULAS[table]
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for container, body in document["species"].items():
        for row in body.get("observations", []):
            if row.get("locator", {}).get("table") != table:
                continue
            values = row["values"]
            if values.get("quantity") != "equilibrium_phase_composition":
                continue
            sample = row["experiment"].rsplit("_s", 1)[-1]
            phase = str(values["phase_label_as_published"])
            key = (sample, phase)
            entry = grouped.setdefault(key, {
                "count": str(values["analysis_count_as_published"]),
                "temperature": str(values["T_C"]),
            })
            sigma = values.get("sigma_as_published")
            if sigma is None:
                sigma = "0" if values.get("uncertainty_as_published") else str(row.get("uncertainty", {}).get("sigma", ""))
            entry[str(values["formula"])] = f"{values['component_amount']} ({sigma})"
        for row in body.get("context", []):
            if row.get("locator", {}).get("table") != table:
                continue
            values = row.get("values", {})
            if values.get("quantity") != "equilibrium_phase_composition":
                continue
            sample = str(values["sample_id_as_printed"])
            phase = str(values["phase_as_printed"])
            key = (sample, phase)
            grouped[key] = {
                "count": str(values["analysis_count_as_printed"]),
                "temperature": str(values["temperature_C_as_printed"]),
                **{formula: str(cell) for formula, cell in values["composition_cells_as_printed"].items()},
            }
    result = []
    for (sample, phase), values in sorted(grouped.items(), key=lambda pair: (int(pair[0][0]), pair[0][1])):
        cells = ";".join(values.get(formula, "") for formula in formulas)
        result.append(f"{sample}|{phase}|{values['count']}|{values['temperature']}|{cells}")
    return result


def _expected_grid(table: str) -> list[str]:
    return ["|".join(line.split("|")) for line in TABLE_RENDER_GRIDS[table].strip().splitlines()]


@pytest.mark.parametrize("table", sorted(TABLE_RENDER_GRIDS))
def test_complete_render_grids_match_the_extract(table: str) -> None:
    actual = _table_grid(_document(), table)
    assert sorted(actual, key=lambda line: (int(line.split("|", 1)[0]), line.split("|", 2)[1])) == sorted(
        _expected_grid(table), key=lambda line: (int(line.split("|", 1)[0]), line.split("|", 2)[1])
    )


def test_render_grid_and_extract_cell_mutations_fail() -> None:
    document = _document()
    expected = _expected_grid("10.1")
    changed_grid = [line.replace("75.84 (2.6066)", "75.85 (2.6066)", 1) for line in expected]
    with pytest.raises(AssertionError):
        assert sorted(_table_grid(document, "10.1")) == sorted(changed_grid)

    mutated = deepcopy(document)
    raw = next(
        row for body in mutated["species"].values() for row in body.get("observations", [])
        if row.get("observation_id") == "nam2025_t101_s1_l_v2o5"
    )
    raw["values"]["component_amount"] = "75.85"
    with pytest.raises(AssertionError):
        assert sorted(_table_grid(mutated, "10.1")) == sorted(_expected_grid("10.1"))


def test_all_typed_rows_migrate_with_printed_species_temperature_and_oxygen() -> None:
    document = _document()
    source_rows = [
        (container, row)
        for container, body in document["species"].items()
        for row in body.get("observations", [])
        if row["values"].get("quantity") in {"equilibrium_phase_composition", "dta_transition_temperatures"}
    ]
    assert len(source_rows) == 71
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    migrated = migrator.result.observations
    assert len(migrated) == len(source_rows)

    for container, raw in source_rows:
        values = raw["values"]
        observation = migrated[f"{SOURCE_ID}::{raw['observation_id']}"]
        assert observation.value.kind is ValueKind.POINT, raw["observation_id"]
        expected = (
            Decimal(str(values["component_amount"]))
            if values["quantity"] == "equilibrium_phase_composition"
            else Decimal(str(values["T_C"])) + Decimal("273.15")
        )
        assert observation.value.point == expected, raw["observation_id"]
        assert not observation.notices, raw["observation_id"]
        formula = str(values.get("formula") or container)
        assert observation.identity.species.formula == formula
        if values["quantity"] == "equilibrium_phase_composition":
            assert observation.identity.temperature_K.value == Decimal(str(values["T_C"])) + Decimal("273.15")
            assert observation.point_conditions["temperature_K"].state.value == observation.identity.temperature_K.value
            assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            assert observation.identity.phase_role.value == values["phase_label"]
            assert observation.identity.subtype.value == values["basis"]
            if "log_fO2" in values:
                assert values["log_fO2"]["units"] == "log10(atm)"
                assert "fO2_log" not in values.get("point_conditions", {})
                assert observation.identity.fO2_Pa.value == Decimal("0.0000101325")
                assert observation.point_conditions["fO2_log"].state.value == Decimal("-10")
            elif "pO2_Pa" in values.get("point_conditions", {}):
                assert observation.identity.fO2_Pa.value == Decimal("21278.25")
                assert observation.point_conditions["pO2_Pa"].state.value == Decimal("21278.25")
            if raw.get("experiment") == "nam2025_t101_s3":
                assert not observation.identity.composition.is_value
                assert "sum 111 mol% as printed" in values["sample_composition_as_published"]
            elif "composition" in values.get("point_conditions", {}):
                assert observation.identity.composition.is_value
        else:
            assert observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE


def test_solid_only_tables_remain_context_and_dta_invariant_kind_is_preserved() -> None:
    document = _document()
    all_context = [(container, row) for container, body in document["species"].items() for row in body.get("context", [])]
    for table in ("6.1", "7.1", "8.1", "9.2", "10.1", "10.3"):
        solids = [row for _, row in all_context if row.get("locator", {}).get("table") == table and row.get("values", {}).get("quantity") == "equilibrium_phase_composition"]
        assert solids
        assert all(row["values"]["candidate_tokens"] == ["equilibrium_phase_composition"] for row in solids)
        assert all("PhaseRole" in row["values"]["schema_gap_or_disposition"] for row in solids)

    def solid_container(table: str, sample: str, phase: str) -> str:
        return next(container for container, row in all_context
                    if row.get("locator", {}).get("table") == table
                    and row.get("values", {}).get("sample_id_as_printed") == sample
                    and row.get("values", {}).get("phase_as_printed") == phase)

    assert solid_container("6.1", "1", "V2O3") == "V2O3"
    assert solid_container("6.1", "1", "CaV2O4") == "CaO"
    assert solid_container("10.3", "10", "Sp") == "V2O3"
    assert solid_container("10.3", "10", "Cor1") == "Al2O3"
    assert solid_container("10.3", "11", "Cor") == "V2O3"

    dta = [row for body in document["species"].values() for row in body.get("observations", []) if row.get("locator", {}).get("table") in {"8.2", "9.1"} and row["values"].get("quantity") == "dta_transition_temperatures"]
    assert len(dta) == 12
    assert all(row["values"]["property_kind"] == "invariant_reaction" for row in dta)
    assert not any(row["values"].get("property_kind") == "melting_point" for row in dta)
    dta_experiments = {row["experiment_id"]: row for row in document["experiments"] if row["method"] == "dta_dsc"}
    assert set(dta_experiments) == {"nam2025_t82_dta_sample10", "nam2025_t91_dta_sample10"}
    direct = [row for row in dta if row["values"]["method_class"] == "measured_direct"]
    assert len(direct) == 2
    assert all(row["regime"] == "dta_dsc" for row in direct)

    liquid_rows = [row for body in document["species"].values() for row in body.get("observations", [])
                   if row.get("locator", {}).get("table") == "6.1"]
    sample_11 = next(row for row in liquid_rows if row["experiment"] == "nam2025_t61_s11")
    sample_14 = next(row for row in liquid_rows if row["experiment"] == "nam2025_t61_s14")
    assert sample_11["phase_assemblage_as_printed"] == "CaVO3 + L"
    assert sample_14["phase_assemblage_as_printed"] == "L"


def test_figures_are_deferred_without_numeric_points() -> None:
    document = _document()
    figures = [row for body in document["species"].values() for row in body.get("context", []) if row.get("type") == "figure_context"]
    assert len(figures) == 9
    assert {row["locator"]["figure"] for row in figures} == {"6.2", "7.1", "8.1", "8.3", "8.4", "9.1", "9.2", "10.1", "10.2"}
    for row in figures:
        assert row["values"]["digitisation"] == "deferred to a figure pass"
        assert row["values"]["axes_as_printed"]
        assert row["values"]["series_list_as_printed"]
        markers = row["values"]["marker_count_by_series"]
        assert len(markers) == len(row["values"]["series_list_as_printed"])
        assert all("series" in marker and "marker_count" in marker for marker in markers)
        assert "numeric_points" not in row["values"]
    figure_10_1 = next(row for row in figures if row["locator"]["figure"] == "10.1")
    assert figure_10_1["locator"]["page"] == 180
