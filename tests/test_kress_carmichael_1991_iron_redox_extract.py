"""Render-grounded checks for Kress and Carmichael (1991)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, PhaseRole, Quantity
from simulator.battery.migrate import (
    Migrator,
    fe3_fe2_ratio_from_oxide_wt_pct,
    wt_pct_to_mole_fraction,
)
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "kress-carmichael-1991-iron-redox-calibration.yaml"
)
SOURCE_ID = "kress-carmichael-1991-iron-redox-calibration"
TABLE3_SAMPLED_TEXT = (
    "Melts were sampled at high temperature with a Pt loop quenched into ice-water before, and after each set. "
    "Micron-scale quench crystals in many of the Pt-loop samples reduced the precision of bulk-composition analyses "
    "to about 0.7 wt% absolute."
)
TABLE4_METHOD_TEXT = (
    "In order to account for the effect of Al2O3 contamination in NAFS and CAFS experiments, and the high iron "
    "contents considered in the natural compositions, 33 additional Pt-loop drop-quench experiments were "
    "performed on compositions and conditions spanning those explored in the ultrasonic experiments."
)
TABLE3_OXIDES = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "MnO",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
)

# Initial/final pairs transcribed independently from the 600 dpi PDF page 4
# render (printed folio 85), in the printed oxide-column order above.
TABLE3_RENDER = """
0011-0014 initial 46.87 0.00 0.00 28.78 0.00 0.00 25.60 0.00 0.00
0011-0014 final 46.45 0.00 2.03 26.08 0.00 0.00 24.81 0.00 0.00
0021-0025 initial 46.45 0.00 2.03 26.08 0.00 0.00 24.81 0.00 0.00
0021-0025 final 43.01 0.00 7.08 25.48 0.00 0.00 23.43 0.00 0.00
021-023 initial 39.61 0.00 11.94 23.65 0.00 0.00 22.18 0.00 0.00
021-023 final 38.67 0.00 18.11 22.84 0.00 0.00 20.96 0.00 0.00
031-036 initial 38.67 0.00 18.11 22.84 0.00 0.00 20.96 0.00 0.00
031-036 final 37.41 0.00 21.14 21.65 0.00 0.00 20.31 0.00 0.00
041 initial 37.41 0.00 21.14 21.65 0.00 0.00 20.31 0.00 0.00
041 final 34.62 0.00 27.07 20.27 0.00 0.00 18.95 0.00 0.00
051-056 initial 66.69 0.00 0.00 22.58 0.00 0.00 0.00 10.57 0.00
051-056 final 66.33 0.00 0.00 22.23 0.00 0.00 0.00 10.09 0.00
081-086 initial 65.31 0.00 0.01 22.26 0.00 0.00 0.00 10.47 0.00
081-086 final 62.60 0.00 6.24 19.26 0.00 0.00 0.00 10.45 0.00
091-0910 initial 32.80 0.00 0.00 37.70 0.00 0.00 0.00 29.50 0.00
091-0910 final 32.62 0.00 0.52 37.55 0.00 0.00 0.00 27.48 0.00
101-107 initial 32.62 0.00 0.52 37.55 0.00 0.00 0.00 27.48 0.00
101-107 final 29.27 0.00 9.21 34.25 0.00 0.00 0.00 26.13 0.00
111-117 initial 32.80 0.00 0.00 37.70 0.00 0.00 0.00 29.50 0.00
111-117 final 32.44 0.00 1.15 36.90 0.00 0.00 0.00 27.46 0.00
121-126 initial 32.44 0.00 1.15 36.90 0.00 0.00 0.00 27.46 0.00
121-126 final 31.45 0.00 4.52 35.94 0.00 0.00 0.00 26.56 0.00
131-137 initial 51.18 0.00 0.00 21.60 0.00 0.00 0.00 27.22 0.00
131-137 final 47.82 0.00 0.00 22.74 0.00 0.00 0.00 28.11 0.00
141-147 initial 47.82 0.00 0.00 22.74 0.00 0.00 0.00 28.11 0.00
141-147 final 47.97 0.00 0.96 22.66 0.00 0.00 0.00 27.49 0.00
151-157 initial 51.18 0.00 0.00 21.60 0.00 0.00 0.00 27.22 0.00
151-157 final 49.16 0.00 0.16 22.79 0.00 0.00 0.00 27.69 0.00
161-167 initial 49.16 0.00 0.16 22.79 0.00 0.00 0.00 27.69 0.00
161-167 final 47.80 0.00 3.02 22.06 0.00 0.00 0.00 26.44 0.00
171-174 initial 44.77 2.97 12.20 22.58 N.A. 5.36 8.90 2.70 0.61
171-174 final 43.46 2.66 17.98 19.99 0.15 5.04 8.45 2.35 0.59
181-182 initial 43.46 2.66 17.98 19.99 0.15 5.04 8.45 2.35 0.59
181-182 final 40.38 2.58 22.57 19.68 0.16 4.82 8.06 2.22 0.55
201-206 initial 47.25 3.44 14.07 18.74 N.A. 4.71 7.38 3.35 1.06
201-206 final 44.83 2.78 19.94 16.75 0.22 4.29 7.89 2.93 0.85
211-213 initial 47.25 3.44 14.07 18.74 N.A. 4.71 7.38 3.35 1.06
211-213 final 45.59 3.37 17.48 17.59 0.23 4.33 7.08 3.43 1.04
"""

# Printed composition cells transcribed from the PDF page 5 render (published
# page 86) and checked against the pdftotext and MinerU tables.
TABLE4_OXIDES = (
    "SiO2", "TiO2", "Al2O3", "Fe2O3", "FeO", "MnO", "MgO", "CaO",
    "Na2O", "K2O", "P2O5",
)
TABLE4_RENDER_COMPOSITIONS = """
Q01 38.86 0.00 17.00 16.93 5.41 0.00 0.00 20.89 0.00 0.00 0.00
Q02 38.94 0.00 17.02 16.81 5.41 0.00 0.00 20.82 0.00 0.00 0.00
Q03 37.73 0.00 17.67 16.73 5.29 0.00 0.00 21.09 0.00 0.00 0.00
Q04 39.20 0.00 17.60 16.61 5.29 0.00 0.00 20.95 0.00 0.00 0.00
Q05 68.61 0.00 0.00 20.45 1.58 0.00 0.00 0.03 8.30 0.00 0.00
Q06 66.02 0.00 1.83 20.08 1.83 0.00 0.00 0.01 8.87 0.00 0.00
Q11 66.97 0.00 0.01 19.46 2.70 0.00 0.00 0.01 9.98 0.00 0.00
Q12 68.44 0.00 0.00 19.07 2.69 0.00 0.00 0.01 9.87 0.00 0.00
Q13 66.38 0.00 0.00 19.06 2.87 0.00 0.00 0.01 10.60 0.00 0.00
Q14 62.32 0.00 5.93 17.45 2.84 0.00 0.00 0.02 10.65 0.00 0.00
Q15 67.72 0.00 0.02 19.40 2.89 0.00 0.00 0.00 9.09 0.00 0.00
Q18 55.79 0.00 1.13 22.08 3.39 0.00 0.00 0.01 16.88 0.00 0.00
Q19 52.17 0.00 4.02 19.63 2.45 0.00 0.00 0.02 18.44 0.00 0.00
Q21 66.87 0.00 0.04 22.10 0.14 0.00 0.00 0.04 9.56 0.00 0.00
Q22 65.80 0.00 0.00 21.99 0.23 0.00 0.00 0.01 10.27 0.00 0.00
Q23 66.19 0.00 0.00 21.44 0.34 0.00 0.00 0.01 10.69 0.00 0.00
Q27 33.16 0.00 2.71 37.58 0.28 0.00 0.00 0.04 24.55 0.00 0.00
Q29 47.98 0.00 3.92 22.06 0.27 0.00 0.00 0.02 24.76 0.00 0.00
Q31 27.97 0.00 1.83 33.31 1.98 0.00 0.00 33.64 0.09 0.00 0.00
Q32 42.32 0.00 11.91 19.38 2.77 0.00 0.00 22.48 0.09 0.00 0.00
Q34 42.86 0.00 6.66 22.58 2.91 0.00 0.00 23.45 0.05 0.00 0.00
Q41 28.74 0.00 1.63 28.73 5.98 0.00 0.00 34.18 0.01 0.00 0.00
Q42 40.04 0.00 12.09 16.46 7.20 0.00 0.00 22.40 0.08 0.00 0.00
Q43 38.54 0.00 18.77 14.84 6.69 0.00 0.00 20.85 0.07 0.00 0.00
Q44 43.23 0.00 6.75 17.34 7.69 0.00 0.00 23.82 0.04 0.00 0.00
Q51 44.71 3.00 13.00 17.05 5.10 0.18 5.46 9.06 0.68 0.54 0.31
Q52 46.56 3.56 15.25 13.75 5.05 0.25 4.65 7.48 1.10 0.85 0.38
Q53 45.56 2.95 13.02 16.72 5.00 0.16 5.45 9.11 0.70 0.57 0.31
Q61 45.36 2.98 12.77 15.50 6.97 0.16 5.47 9.09 0.74 0.46 0.32
Q62 47.91 3.60 15.23 11.89 6.33 0.22 4.59 7.45 1.31 0.74 0.26
Q64 46.66 3.51 15.35 12.44 6.36 0.26 4.69 7.52 1.36 0.70 0.26
Q65 46.86 3.40 18.19 11.30 5.82 0.21 4.17 7.01 2.06 0.79 0.24
Q66 46.66 3.29 19.32 10.59 5.83 0.21 4.11 6.91 1.98 0.80 0.26
"""
TABLE5_OXIDES = (
    "SiO2", "TiO2", "Al2O3", "Fe2O3", "FeO", "MnO", "MgO", "CaO",
    "Na2O", "K2O",
)
TABLE5_RENDER_COMPOSITIONS = """
B27 50.46 1.88 13.87 9.54 3.42 0.17 6.84 10.65 2.68 0.19
B34 50.64 1.89 13.93 10.01 2.83 0.17 6.76 10.66 2.66 0.19
B44 50.61 1.90 14.03 8.39 4.45 0.17 6.66 10.61 2.43 0.17
B54 51.09 1.89 13.94 8.41 4.43 0.17 6.77 10.67 2.43 0.17
B64 51.00 1.92 13.96 7.59 5.13 0.17 6.86 10.69 2.32 0.16
B74 50.85 1.89 13.73 6.82 5.56 0.17 6.75 10.78 2.20 0.16
B76 51.24 1.91 13.81 7.56 5.14 0.17 6.78 10.52 2.67 0.21
B79 50.90 1.91 13.85 8.18 4.71 0.17 6.82 10.61 2.69 0.20
B82 50.38 1.93 13.70 8.91 3.99 0.17 6.93 10.54 2.70 0.20
B105 52.41 2.00 14.26 0.52 10.89 0.22 6.79 11.17 0.56 0.13
B108 50.19 1.89 13.50 2.11 12.61 0.22 6.54 10.53 1.48 0.15
B118 52.27 1.95 13.95 1.82 10.39 0.21 6.69 10.90 1.23 0.14
B120 52.83 1.95 13.95 1.80 10.38 0.23 6.87 10.93 0.87 0.13
"""


def _expected_table45_compositions() -> dict[str, dict[str, str]]:
    expected = {}
    for table, oxides, render in (
        ("4", TABLE4_OXIDES, TABLE4_RENDER_COMPOSITIONS),
        ("5", TABLE5_OXIDES, TABLE5_RENDER_COMPOSITIONS),
    ):
        for line in render.strip().splitlines():
            row, *cells = line.split()
            assert len(cells) == len(oxides)
            expected[f"kc91_fe3_t{table}_{row}"] = dict(zip(oxides, cells, strict=True))
    return expected


def _actual_table45_compositions(document: dict) -> dict[str, dict | None]:
    return {
        observation["observation_id"]: observation.get("values", {}).get("composition_wt_pct")
        for observation in document["species"]["Fe"]["observations"]
        if observation["observation_id"].startswith("kc91_fe3_t")
    }


def _assert_table45_compositions_match(document: dict, expected: dict) -> None:
    observations = {
        observation["observation_id"]: observation
        for observation in document["species"]["Fe"]["observations"]
        if observation["observation_id"].startswith("kc91_fe3_t")
    }
    assert _actual_table45_compositions(document) == expected
    for observation_id in expected:
        table, row = observation_id.removeprefix("kc91_fe3_t").split("_", 1)
        locator = observations[observation_id]["locator"]
        assert locator["page"] == 5
        assert locator["table"] == table
        assert locator["row"] == row


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _expected_table3() -> dict[tuple[str, str], str]:
    expected = {}
    for line in TABLE3_RENDER.strip().splitlines():
        row = line.split()
        sample = " ".join(row[:2])
        assert len(row[2:]) == len(TABLE3_OXIDES)
        expected.update(zip(((sample, oxide) for oxide in TABLE3_OXIDES), row[2:]))
    return expected


def _actual_table3(document: dict) -> dict[tuple[str, str], str]:
    actual = {}
    for oxide in TABLE3_OXIDES:
        for observation in document["species"][oxide]["observations"]:
            locator = observation["locator"]
            if locator.get("table") != "3":
                continue
            values = observation["values"]
            printed = (
                values["unavailable"]["verbatim"]
                if "unavailable" in values
                else str(values["value"])
            )
            actual[(locator["row"], oxide)] = printed
    return actual


def _assert_table3_matches(document: dict, expected: dict) -> None:
    assert _actual_table3(document) == expected


def _migrated() -> tuple[dict, Migrator]:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator


def _assert_phase_scoring_inputs(document: dict, records: dict) -> None:
    target = {
        observation["observation_id"]: (formula, observation)
        for formula, body in document["species"].items()
        for observation in body.get("observations", [])
        if observation.get("values", {}).get("quantity")
        == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
        and observation["observation_id"].startswith(("kc91_t3_", "kc91_t4_", "kc91_t5_"))
    }
    assert len(target) == 835

    composition_count = 0
    fo2_count = 0
    migrated_composition_count = 0
    migrated_fo2_count = 0
    migrated_temperature_count = 0
    for observation_id, (_formula, observation) in target.items():
        values = observation["values"]
        assert values["phase_label"] == "melt"
        assert values["basis"] == "wt_percent"
        temperature = values["T_C"]
        if isinstance(temperature, dict):
            assert temperature.get("value") not in (None, "")
            assert temperature.get("units") == "°C"
            assert temperature.get("locator", {}).get("table") == "2"
        else:
            assert temperature not in (None, "")

        conditions = observation["point_conditions"]
        assert "composition" in conditions
        assert "state" in conditions["composition"]
        assert conditions["composition"].get("locator")
        assert "fO2_log" in conditions
        assert "state" in conditions["fO2_log"]
        assert conditions["fO2_log"].get("locator")
        composition_count += conditions["composition"]["state"]["tag"] == "value"
        fo2_count += conditions["fO2_log"]["state"]["tag"] == "value"

        record = records[f"{SOURCE_ID}::{observation_id}"]
        assert record.identity.phase_role.is_value
        assert record.identity.phase_role.value is PhaseRole.MELT
        assert record.identity.species.phase.is_value
        assert record.identity.species.phase.value is Phase.L
        assert record.identity.temperature_K.is_value
        migrated_temperature_count += record.identity.temperature_K.is_value
        assert record.point_conditions is not None
        assert all(condition.locator is not None for condition in record.point_conditions.values())
        assert record.point_conditions["temperature_K"].state.is_value
        assert record.point_conditions["composition"].state.is_unknown
        assert record.point_conditions["fO2_log"].state.is_value or record.point_conditions["fO2_log"].state.is_unknown
        assert record.identity.composition is None or record.identity.composition.is_unknown
        migrated_composition_count += bool(
            record.identity.composition and record.identity.composition.is_value
        )
        fo2_state = record.point_conditions["fO2_log"].state
        assert fo2_state.is_value or fo2_state.is_unknown
        migrated_fo2_count += fo2_state.is_value

    assert composition_count == 0
    assert fo2_count == 130
    assert migrated_composition_count == 0
    assert migrated_fo2_count == 130
    assert migrated_temperature_count == 835

    observations = {
        observation["observation_id"]: observation
        for _formula, body in document["species"].items()
        for observation in body.get("observations", [])
    }
    initial = observations["kc91_t3_0011_0014_initial_SiO2"]
    final = observations["kc91_t3_0011_0014_final_SiO2"]
    assert initial["phase_as_published"] == "glass"
    assert final["phase_as_published"] == "glass"
    assert initial["quench_statement"]["text"] == TABLE3_SAMPLED_TEXT
    assert initial["point_conditions"]["composition"]["state"]["tag"] == "unknown"
    assert "not reused as run composition" in initial["point_conditions"]["composition"]["state"]["reason"]
    assert initial["point_conditions"]["fO2_log"]["state"]["reason"] == (
        "fo2_set_by_in_air_not_printed_numerically"
    )
    assert initial["point_conditions"]["method_detail_as_printed"]["state"]["value"] == (
        "Measurements were performed in air, between 945 and 1530 °C, "
        "at frequencies between 2 and 10 mHz."
    )
    assert initial["values"]["T_C"]["value"] == "1260"
    assert initial["values"]["T_C"]["locator"]["row"] == "0012"
    assert final["values"]["T_C"]["value"] == "1260"
    assert final["values"]["T_C"]["locator"]["row"] == "0014"

    expected_table3 = _expected_table3()
    assert initial["values"]["value_as_published"] == expected_table3[("0011-0014 initial", "SiO2")]
    assert final["values"]["value_as_published"] == expected_table3[("0011-0014 final", "SiO2")]
    final_run_composition = final["point_conditions"]["composition"]
    assert final_run_composition["state"] == {
        "tag": "unknown",
        "reason": "input_is_same_run_analysed_melt",
    }
    assert final_run_composition["locator"]["row"] == "0011-0014 initial"
    final_record = records[f"{SOURCE_ID}::kc91_t3_0011_0014_final_SiO2"]
    assert final_record.identity.composition.is_unknown

    na_final = observations["kc91_t3_171_174_final_MnO"]
    assert na_final["point_conditions"]["composition"]["state"]["tag"] == "unknown"
    assert na_final["point_conditions"]["composition"]["state"]["reason"] == (
        "input_is_same_run_analysed_melt"
    )

    table4 = observations["kc91_t4_Q01_SiO2"]["point_conditions"]
    assert table4["composition"]["state"]["tag"] == "unknown"
    assert "no starting/bulk composition" in table4["composition"]["state"]["reason"]
    assert table4["fO2_log"]["state"]["tag"] == "unknown"
    assert "neither a numeric fO2 nor a method" in table4["fO2_log"]["state"]["reason"]
    assert "air" not in table4["fO2_log"]["state"]["reason"].casefold()
    assert observations["kc91_t4_Q01_SiO2"]["quench_statement"]["text"] == TABLE4_METHOD_TEXT

    table5 = observations["kc91_t5_B27_SiO2"]["point_conditions"]
    assert table5["composition"]["state"]["tag"] == "unknown"
    assert table5["fO2_log"]["state"]["tag"] == "value"
    assert table5["fO2_log"]["state"]["value"] == "-0.68"
    assert table5["fO2_log"]["locator"]["table"] == "5"
    assert table5["fO2_log"]["locator"]["row"] == "B27"
    assert records[f"{SOURCE_ID}::kc91_t5_B27_SiO2"].identity.fO2_Pa.is_value

    assemblage_notes = [
        row
        for row in document["species"]["SiO2"]["context"]
        if row.get("type") == "phase_assemblage_evidence"
    ]
    assert len(assemblage_notes) == 19
    assert {row["experiment"] for row in assemblage_notes} == {
        f"kc91_t3_{run_id}"
        for run_id in (
            "0011_0014",
            "0021_0025",
            "021_023",
            "031_036",
            "041",
            "051_056",
            "081_086",
            "091_0910",
            "101_107",
            "111_117",
            "121_126",
            "131_137",
            "141_147",
            "151_157",
            "161_167",
            "171_174",
            "181_182",
            "201_206",
            "211_213",
        )
    }
    for note in assemblage_notes:
        assert note["values"]["evidence_type"] == "sampled-from-liquid"
        assert note["values"]["run_products_as_printed"] == TABLE3_SAMPLED_TEXT
        assert note["locator"]["page"] == 3
        assert note["locator"]["published_page"] == 84
    assert all(note["experiment"].startswith("kc91_t3_") for note in assemblage_notes)


def test_phase_scoring_input_deletions_fail_the_focused_pins() -> None:
    document = _document()
    _, migrator = _migrated()
    records = migrator.result.observations
    _assert_phase_scoring_inputs(document, records)

    mutations = (
        lambda doc: doc["species"]["SiO2"]["observations"][0]["values"].pop("phase_label"),
        lambda doc: doc["species"]["SiO2"]["observations"][1]["point_conditions"].pop("composition"),
        lambda doc: doc["species"]["SiO2"]["observations"][0]["point_conditions"]["fO2_log"]["state"].pop("reason"),
        lambda doc: doc["species"]["SiO2"]["observations"][0]["values"]["T_C"].pop("value"),
        lambda doc: doc["species"]["SiO2"]["context"].pop(
            next(
                index
                for index, row in enumerate(doc["species"]["SiO2"]["context"])
                if row.get("type") == "phase_assemblage_evidence"
            )
        ),
    )
    for mutate in mutations:
        changed = deepcopy(document)
        mutate(changed)
        with pytest.raises((AssertionError, KeyError)):
            _assert_phase_scoring_inputs(changed, records)


def test_extract_validates_and_table3_matches_independent_render_grid() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    expected = _expected_table3()
    actual = _actual_table3(document)
    assert len(expected) == 38 * 9
    assert actual == expected

    # Three render pins have printed physical-page locators (PDF page 4).
    first = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"] == "kc91_t3_0011_0014_initial_SiO2"
    )
    assert first["values"]["value"] == "46.87"
    assert first["locator"]["page"] == first["locator"]["pdf_page_index"] + 1 == 4
    assert actual[("021-023 initial", "Fe2O3")] == "23.65"
    assert actual[("211-213 final", "Na2O")] == "3.43"


def test_table3_render_grid_and_extract_cell_mutations_fail() -> None:
    document = _document()
    expected = _expected_table3()
    _assert_table3_matches(document, expected)

    mutated_grid = deepcopy(expected)
    mutated_grid[("0011-0014 initial", "SiO2")] = "46.88"
    with pytest.raises(AssertionError):
        _assert_table3_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    cell = next(
        observation
        for observation in mutated_extract["species"]["SiO2"]["observations"]
        if observation["observation_id"] == "kc91_t3_0011_0014_initial_SiO2"
    )
    cell["values"]["value"] = "46.88"
    with pytest.raises(AssertionError):
        _assert_table3_matches(mutated_extract, expected)


def test_table45_full_compositions_match_independent_render_cells_and_locators() -> None:
    document = _document()
    expected = _expected_table45_compositions()

    assert len(expected) == 46
    assert Counter(
        "t4" if observation_id.startswith("kc91_fe3_t4_") else "t5"
        for observation_id in expected
    ) == {
        "t4": 33,
        "t5": 13,
    }
    _assert_table45_compositions_match(document, expected)


def test_table45_composition_cell_map_and_locator_mutations_fail() -> None:
    document = _document()
    expected = _expected_table45_compositions()
    _assert_table45_compositions_match(document, expected)

    mutated_extract = deepcopy(document)
    q01 = next(
        observation
        for observation in mutated_extract["species"]["Fe"]["observations"]
        if observation["observation_id"] == "kc91_fe3_t4_Q01"
    )
    q01["values"]["composition_wt_pct"]["SiO2"] = "38.87"
    with pytest.raises(AssertionError):
        _assert_table45_compositions_match(mutated_extract, expected)

    mutated_extract = deepcopy(document)
    b34 = next(
        observation
        for observation in mutated_extract["species"]["Fe"]["observations"]
        if observation["observation_id"] == "kc91_fe3_t5_B34"
    )
    b34["values"].pop("composition_wt_pct")
    with pytest.raises(AssertionError):
        _assert_table45_compositions_match(mutated_extract, expected)

    mutated_extract = deepcopy(document)
    b34 = next(
        observation
        for observation in mutated_extract["species"]["Fe"]["observations"]
        if observation["observation_id"] == "kc91_fe3_t5_B34"
    )
    b34["locator"]["row"] = "B27"
    with pytest.raises(AssertionError):
        _assert_table45_compositions_match(mutated_extract, expected)


def test_isolated_migration_preserves_species_conditions_and_reduced_ratios() -> None:
    document, migrator = _migrated()
    records = migrator.result.observations
    assert len(records) == 881
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    formula_by_id = {}
    rows_by_id = {}
    typed_count = 0
    unavailable_count = 0
    for formula, body in document["species"].items():
        for observation in body.get("observations", []):
            observation_id = observation["observation_id"]
            formula_by_id[observation_id] = formula
            values = observation.get("values", {})
            if values.get("quantity") or observation.get("type") == "concentration_series":
                typed_count += 1
            if "unavailable" in values:
                unavailable_count += 1
            rows_by_id[observation_id] = observation

    assert typed_count == 881
    assert unavailable_count == 3
    for key, record in records.items():
        source_observation_id = key.split("::", 1)[1].split("::", 1)[0]
        assert record.identity.species.formula == formula_by_id[source_observation_id]
        assert record.identity.temperature_K.is_value or record.identity.temperature_K.is_unknown
        assert record.point_conditions is not None
        assert record.point_conditions["temperature_K"].locator is not None
        assert all(condition.locator is not None for condition in record.point_conditions.values())

    for observation_id, observation in rows_by_id.items():
        values = observation.get("values", {})
        prefix = f"{SOURCE_ID}::{observation_id}"
        migrated = [record for key, record in records.items() if key == prefix or key.startswith(prefix + "::")]
        if not migrated:
            continue
        if "T_C" in values:
            raw_temperature = values["T_C"]
            if isinstance(raw_temperature, dict):
                expected_temperature = Decimal(raw_temperature["value"]) + Decimal("273.15")
                assert raw_temperature["locator"]["table"] == "2"
            else:
                expected_temperature = Decimal(raw_temperature) + Decimal("273.15")
            assert all(record.identity.temperature_K.value == expected_temperature for record in migrated)
        elif observation.get("locator", {}).get("table") == "3":
            assert all(record.identity.temperature_K.is_value for record in migrated)
            assert all(
                record.point_conditions["temperature_K"].state.is_value
                and record.point_conditions["temperature_K"].locator.page == 3
                for record in migrated
            )
            assert all(record.identity.total_pressure_Pa.is_not_applicable for record in migrated)

        if "quench_statement" in observation:
            assert observation["quench_statement"]["preservation"] == "not_stated"

    quantities = Counter(
        record.identity.quantity.value for record in records.values()
    )
    assert quantities == {
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 835,
        Quantity.FE3_FE2_RATIO: 46,
    }
    _assert_phase_scoring_inputs(document, records)
    reduced = [
        record
        for key, record in records.items()
        if "kc91_fe3_t4_" in key or "kc91_fe3_t5_" in key
    ]
    assert len(reduced) == 46
    assert all(record.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for record in reduced)
    expected_compositions = _expected_table45_compositions()
    assert len(expected_compositions) == 46
    for observation_id, expected_composition in expected_compositions.items():
        fe2o3 = expected_composition["Fe2O3"]
        feo = expected_composition["FeO"]
        source = rows_by_id[observation_id]
        assert source["values"]["composition_wt_pct"] == expected_composition
        assert source["values"]["Fe2O3_wt_pct"] == fe2o3
        assert source["values"]["FeO_wt_pct"] == feo
        if observation_id.startswith("kc91_fe3_t4_"):
            run = observation_id.removeprefix("kc91_fe3_t4_")
            for oxide, printed in (("Fe2O3", fe2o3), ("FeO", feo)):
                direct = rows_by_id[f"kc91_t4_{run}_{oxide}"]
                assert direct["values"]["value_as_published"] == printed
        record = next(item for item in reduced if observation_id in item.observation_id)
        expected_ratio = fe3_fe2_ratio_from_oxide_wt_pct(
            Decimal(fe2o3), Decimal(feo)
        )
        assert expected_ratio is not None
        assert record.value.point == expected_ratio
        expected_mole_fractions = wt_pct_to_mole_fraction(
            {oxide: Decimal(amount) for oxide, amount in expected_composition.items()}
        )
        assert record.identity.composition.is_value
        assert record.identity.composition.value.as_map() == expected_mole_fractions.as_map()
    assert all(record.identity.composition.is_value for record in reduced)
    assert all(
        record.identity.total_pressure_Pa.is_value
        and record.identity.total_pressure_Pa.value == Decimal("101325")
        for record in reduced
    )

    table4_ratio = next(record for record in reduced if "kc91_fe3_t4_Q01" in record.observation_id)
    assert table4_ratio.point_conditions["fO2_log"].state.is_unknown

    table5_ratio = next(record for record in reduced if "kc91_fe3_t5_B27" in record.observation_id)
    assert table5_ratio.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert table5_ratio.point_conditions["fO2_log"].locator.page == 5
    assert table5_ratio.point_conditions["fO2_log"].locator.table == "5"
    assert table5_ratio.identity.fO2_Pa.is_value


def test_context_provenance_and_deferred_figures_are_explicit() -> None:
    document = _document()
    context = [
        item
        for body in document["species"].values()
        for item in body.get("context", [])
    ]
    equation7 = next(
        item for item in context
        if item["observation_id"] == "kress_1991_equation7_fit_scope"
    )
    assert equation7["locator"]["page"] == 6
    assert equation7["locator"]["published_page"] == 87
    assert equation7["values"]["fit_data_scope"]["data_count"] == 228
    assert equation7["values"]["fit_data_scope"]["equation"] == 7
    fit_scope = equation7["values"]["fit_data_scope"]
    assert fit_scope["regression"] == "weighted stepwise-linear"
    assert "weighted stepwise-linear regression" in equation7["values"]["source_wording"]
    assert len(fit_scope["sources"]) == 7
    assert all(
        source["fit_locator"]["published_page"] == 87
        for source in fit_scope["sources"]
    )

    table7_e = next(item for item in context if item["observation_id"] == "kc91_t7_e")
    assert table7_e["values"]["evidence_class"] == "quoted_attributed"
    assert table7_e["values"]["attribution"] == "Stebbins et al. (1984)"

    table9_rows = [item for item in context if item["observation_id"].startswith("kc91_t9_")]
    cited = [item for item in table9_rows if "column_2_value" in item["values"]]
    assert len(cited) == 20
    assert all(item["values"]["column_2_evidence_class"] == "quoted_attributed" for item in cited)
    assert all(item["values"]["column_2_attribution"] == "Lange and Carmichael (1987)" for item in cited)
    current = [item for item in table9_rows if "column_3_value" in item["values"]]
    assert all(item["values"]["column_3_evidence_class"] == "model_derived" for item in current)

    figures = [item for item in context if item.get("type") == "figure_record"]
    assert {item["observation_id"] for item in figures} == {"kc91_fig1", "kc91_fig2", "kc91_fig3"}
    assert all(item["values"]["numeric_points"] == "none" for item in figures)
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert all(
        all(series["marker_count"] != "not counted" for series in item["values"]["series"])
        for item in figures
    )
    assert all("species_as_printed" in item["values"] for item in figures)

    acoustic_rows = [item for item in context if item.get("type") == "acoustic_velocity_row"]
    count_note = next(item for item in context if item.get("observation_id") == "kc91_t2_count_note")
    assert len(acoustic_rows) == 83
    assert count_note["values"]["printed_count"] == "86"
    assert "density, viscosity, cp" in count_note["values"]["schema_gap"]

    for observation_id, printed in (
        ("kc91_fe3_t5_B105", "-6,29"),
        ("kc91_fe3_t5_B120", "-4,74"),
    ):
        ratio = next(
            item
            for item in document["species"]["Fe"]["observations"]
            if item["observation_id"] == observation_id
        )
        assert ratio["point_conditions"]["fO2_log"]["as_published"] == printed
        assert ratio["values"]["series"][0]["log_fO2"]["as_published"] == printed
        assert ratio["point_conditions"]["fO2_log"]["state"]["value"] in {"-6.29", "-4.74"}
        assert ratio["locator"]["page"] == 5
