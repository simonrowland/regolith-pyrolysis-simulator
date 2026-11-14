from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "greig-1927-immiscibility-part2"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
PART_I_EXTRACT = (
    ROOT / "data/literature/extracts/greig-1927-ajs-immiscibility-silicate-melts-part1.yaml"
)
RENDER_PINS = {
    ("CaO", "greig1927_cao_silica_rich_cao"): ("0.6", 21, None),
    ("CaO", "greig1927_cao_less_siliceous_cao"): ("27.5", 21, None),
    ("MgO", "greig1927_mgo_less_siliceous_mgo"): ("31", 21, None),
    ("SiO2", "greig1927_cao_less_siliceous_sio2"): ("72.5", 21, None),
    ("SiO2", "greig1927_mgo_less_siliceous_sio2"): ("70", 21, None),
    ("SiO2", "greig1927_sro_less_siliceous_sio2"): ("69", 21, None),
    ("SrO", "greig1927_sro_less_siliceous_sro"): ("30", 21, None),
    ("FeO", "greig1927_table8_feo_rich_a_feo"): ("38.3", 3, "VIII"),
    ("SiO2", "greig1927_table8_feo_rich_a_sio2"): ("55.8", 3, "VIII"),
    ("SiO2", "greig1927_table8_feo_rich_b_sio2"): ("55.3", 3, "VIII"),
}
INTERPOLATED = {
    "greig1927_cao_silica_rich_cao": (
        "CaO",
        "greig1927_t5_cao_siliceous_glass",
        {"CaO": "0.6", "SiO2": "99.4"},
        "1698",
    ),
    "greig1927_cao_silica_rich_sio2": (
        "SiO2",
        "greig1927_t5_cao_siliceous_glass",
        {"CaO": "0.6", "SiO2": "99.4"},
        "1698",
    ),
    "greig1927_mgo_silica_rich_mgo": (
        "MgO",
        "greig1927_t5_mgo_siliceous_glass",
        {"MgO": "0.8", "SiO2": "99.2"},
        "1695",
    ),
    "greig1927_mgo_silica_rich_sio2": (
        "SiO2",
        "greig1927_t5_mgo_siliceous_glass",
        {"MgO": "0.8", "SiO2": "99.2"},
        "1695",
    ),
    "greig1927_sro_silica_rich_sro": (
        "SrO",
        "greig1927_t5_sro_siliceous_glass",
        {"SrO": "2.4", "SiO2": "97.6"},
        "1693",
    ),
    "greig1927_sro_silica_rich_sio2": (
        "SiO2",
        "greig1927_t5_sro_siliceous_glass",
        {"SrO": "2.4", "SiO2": "97.6"},
        "1693",
    ),
}
EXTRAPOLATED_LESS_SILICEOUS = {
    "greig1927_cao_less_siliceous_cao": (
        "CaO",
        "27.5",
        {"CaO": "27.5", "SiO2": "72.5"},
        "1698",
    ),
    "greig1927_cao_less_siliceous_sio2": (
        "SiO2",
        "72.5",
        {"CaO": "27.5", "SiO2": "72.5"},
        "1698",
    ),
    "greig1927_mgo_less_siliceous_mgo": (
        "MgO",
        "31",
        {"MgO": "31", "SiO2": "70"},
        "1695",
    ),
    "greig1927_mgo_less_siliceous_sio2": (
        "SiO2",
        "70",
        {"MgO": "31", "SiO2": "70"},
        "1695",
    ),
    "greig1927_sro_less_siliceous_sro": (
        "SrO",
        "30",
        {"SrO": "30", "SiO2": "69"},
        "1693",
    ),
    "greig1927_sro_less_siliceous_sio2": (
        "SiO2",
        "69",
        {"SrO": "30", "SiO2": "69"},
        "1693",
    ),
}
EXTRAPOLATION_METHOD = (
    "By extrapolating the liquidus curve so obtained until it intersects the horizontal determined by the "
    "quenches of Table III, the composition of the less siliceous of the two liquids which can coexist in "
    "equilibrium with cristobalite is found. In each case the extrapolation is short."
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, body in document["species"].items()
        for row in body.get("observations", [])
    }


def _context_rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, body in document["species"].items()
        for row in body.get("context", [])
    }


def _record_rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {**_context_rows(document), **_source_rows(document)}


def _assert_render_pins(document: dict) -> None:
    observations = _source_rows(document)
    records = _record_rows(document)
    assert len(observations) == 11
    assert len(document["fidelity_samples"]) == 20
    for (formula, observation_id), (expected, page, table) in RENDER_PINS.items():
        actual_formula, row = records[observation_id]
        assert actual_formula == formula
        assert row["values"].get("component_amount", row["values"].get("value")) == expected
        assert row["locator"]["page"] == page
        assert row["locator"].get("table") == table


def _assert_table9_binding(document: dict) -> None:
    table9 = _context_rows(document)[
        "greig1927_table9_other_binary_silica_run_products"
    ][1]["values"]
    assert table9["table_body_row_count"] == 9
    assert len(table9["rows"]) == 8
    pb = next(row for row in table9["rows"] if row["row"] == "8-9")
    assert pb["physical_rows"] == ["8", "9"]
    assert pb["charge_composition_wt_percent"] == {"PbO": "11", "SiO2": "89"}
    assert pb["heat_treatment_as_published"] == (
        "Charge first heated 24 hrs. at 1100°, then at 1710°."
    )
    assert pb["heat_treatment_steps"] == [
        {"duration_hours": "24", "temperature_C": "1100"},
        {"duration_hours": "not printed", "temperature_C": "1710"},
    ]
    assert pb["result_as_published"] == (
        "Single clear glass. 9PbO 91SiO2. No sign of immiscibility."
    )
    assert pb["additional_composition_as_published"] == "9PbO 91SiO2"
    assert "not resolved" in pb["additional_composition_role"].lower()
    assert pb["reagent_description_as_published"] == "PbO as glass; PbSiO3¹"
    assert "Render confirms PbSiO3¹" in table9["pb_formula_render_check"]


def _assert_less_siliceous_extrapolation_evidence(document: dict) -> None:
    contexts = _context_rows(document)
    part_i = yaml.safe_load(PART_I_EXTRACT.read_text(encoding="utf-8"))
    method = _context_rows(part_i)["greig1927_less_siliceous_liquid_method"][1]
    method_values = method["values"]
    assert method_values["less_siliceous_liquid_as_printed"] == EXTRAPOLATION_METHOD
    assert method["locator"]["page"] == 16

    extrapolated_ids = set(EXTRAPOLATED_LESS_SILICEOUS)
    assert extrapolated_ids <= set(contexts)
    assert not (extrapolated_ids & set(_source_rows(document)))
    for observation_id, (formula, amount, composition, temperature) in (
        EXTRAPOLATED_LESS_SILICEOUS.items()
    ):
        actual_formula, record = contexts[observation_id]
        values = record["values"]
        assert actual_formula == formula
        assert record["locator"]["page"] == 21
        assert values["component_amount"] == amount
        assert values["composition_as_printed"] == composition
        assert values["method_class"] == "author_derived"
        assert values["extrapolation_method_as_published"] == EXTRAPOLATION_METHOD
        assert values["method_source_id"] == (
            "greig-1927-ajs-immiscibility-silicate-melts-part1"
        )
        assert values["method_source_observation_id"] == (
            "greig1927_less_siliceous_liquid_method"
        )
        assert values["method_source_locator_as_printed"] == (
            'Part I physical and published p. 16, section '
            '"Immiscibility in the binary systems".'
        )
        assert values["phase_assignment_as_published"] == "less-siliceous liquid"
        assert values["T_C_as_assigned"] == temperature
        assert "phase" not in record
        assert "phase_as_analyzed" not in values
        assert "phase_label" not in values


def test_render_transcriptions_and_row_counts_are_pinned() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_table9_binding(document)

    contexts = _context_rows(document)
    table8 = contexts["greig1927_table8_charge_and_c_analysis_context"][1]
    table10 = contexts["greig1927_table10_b2o3_sio2_runs"][1]
    assert table8["values"]["table_body_row_count"] == 9
    assert table8["values"]["C_analysis_wt_percent"] == {
        "FeO": "2.94",
        "Fe2O3": ".13",
        "SiO2_by_difference": "96.9",
    }
    assert table10["values"]["rows"][2]["heat_treatment_steps"][1][
        "duration_hours_as_published"
    ] == "1¼"


def test_less_siliceous_compositions_keep_located_extrapolation_evidence() -> None:
    _assert_less_siliceous_extrapolation_evidence(_document())


def test_interpolation_repeats_and_transition_repeats_keep_part_i_provenance() -> None:
    contexts = _context_rows(_document())
    part_i = yaml.safe_load(PART_I_EXTRACT.read_text(encoding="utf-8"))
    part_i_records = _record_rows(part_i)
    assert set(INTERPOLATED) <= contexts.keys()
    for observation_id, (formula, part_i_id, composition, summary_temperature) in INTERPOLATED.items():
        actual_formula, record = contexts[observation_id]
        values = record["values"]
        assert actual_formula == formula
        assert values["method_class"] == "author_derived"
        assert values["evidence_class_as_published"] == (
            "interpolated in Part I Table V and quoted again in Part II."
        )
        assert values["originating_part_i_observation_id"] == part_i_id
        assert part_i_id in part_i_records
        assert values["originating_temperature_C"] == "1710"
        assert values["part_ii_summary_temperature_C"] == summary_temperature
        assert values["composition_as_printed"] == composition

    repeats = {
        "greig1927_cao_cristobalite_two_liquids_transition_1698":
            "greig1927_t3_cao_sio2_cristobalite_two_liquids_T",
        "greig1927_mgo_cristobalite_two_liquids_transition_1695":
            "greig1927_t3_mgo_sio2_cristobalite_two_liquids_T",
    }
    for observation_id, part_i_id in repeats.items():
        values = contexts[observation_id][1]["values"]
        assert values["source_relationship"] == "repeated_from_part_i"
        assert values["originating_part_i_observation_id"] == part_i_id
        assert part_i_id in part_i_records
        assert values["evidence_class_as_published"] == (
            "repeat of a measured Part I transition observation"
        )


def test_table8_silica_closures_lineage_and_phase_description_are_pinned() -> None:
    source = _source_rows(_document())
    expected = {
        "greig1927_table8_feo_rich_a_sio2": (
            "55.8", "38.3", "5.9", "greig1927_table8_feo_rich_a"
        ),
        "greig1927_table8_feo_rich_b_sio2": (
            "55.3", "41.3", "3.4", "greig1927_table8_feo_rich_b"
        ),
    }
    for observation_id, (silica, feo, fe2o3, prefix) in expected.items():
        formula, row = source[observation_id]
        values = row["values"]
        assert formula == "SiO2"
        assert values["method_class"] == "measured_reduced"
        assert values["phase_label"] == "melt"
        assert Decimal("100") - Decimal(feo) - Decimal(fe2o3) == Decimal(silica)
        parents = [f"{prefix}_feo", f"{prefix}_fe2o3"]
        assert row["derived_from"] == parents
        assert row["derivation"]["inputs"] == parents
        assert values["component_amount"] == silica

    table8_ids = {
        "greig1927_table8_feo_rich_a_feo",
        "greig1927_table8_feo_rich_a_fe2o3",
        "greig1927_table8_feo_rich_a_sio2",
        "greig1927_table8_feo_rich_b_feo",
        "greig1927_table8_feo_rich_b_fe2o3",
        "greig1927_table8_feo_rich_b_sio2",
    }
    for observation_id in table8_ids:
        values = source[observation_id][1]["values"]
        row = source[observation_id][1]
        assert row["phase"] == "l"
        assert "two liquids" in values["phase_assemblage_as_printed"]
        assert values["phase_label"] == "melt"
        assert "cristobalite and two liquids" not in values["phase_assemblage_as_printed"]
        assert "fine-grained mass" in values["quench_provenance_as_printed"]
        assert "high birefringence" in values["quench_provenance_as_printed"]
        assert "glass" not in values["phase_as_analyzed"].lower()
        assert "quenched glass" not in values["phase_as_analyzed"].lower()
        assert "post-quench microscopy" in values["measurement_note"]


def test_p21_row_layout_tentative_feo_limits_and_reduced_context_are_pinned() -> None:
    document = _document()
    source = _source_rows(document)
    contexts = _context_rows(document)
    layout = contexts["greig1927_p21_interleaved_summary_layout"][1]["values"]
    assert layout["printed_rows"][0]["MgO_less_siliceous"] == "31 MgO, 70 SiO2"
    assert layout["printed_rows"][0]["temperature_as_published"] == "1698° ± 5°"
    assert layout["printed_rows"][1]["CaO_less_siliceous"] == "27.5 CaO, 72.5 SiO2"
    assert layout["printed_rows"][1]["temperature_as_published"] == "1695° ± 5°"
    assert "share row 1" in layout["inconsistency_flag"]
    assert "share row 2" in layout["inconsistency_flag"]
    assert layout["reconciled_pairing_basis"]["CaO_temperature_observation_id"] == (
        "greig1927_t3_cao_sio2_cristobalite_two_liquids_T"
    )
    assert layout["reconciled_pairing_basis"]["MgO_temperature_observation_id"] == (
        "greig1927_t3_mgo_sio2_cristobalite_two_liquids_T"
    )

    feo_limit_ids = (
        "greig1927_feo_silica_rich_feo",
        "greig1927_feo_silica_rich_sio2",
        "greig1927_feo_less_siliceous_feo",
        "greig1927_feo_less_siliceous_sio2",
    )
    for observation_id in feo_limit_ids:
        values = source[observation_id][1]["values"]
        assert values["method_class"] == "author_estimate"
        assert "Tentatively placed" in values["qualifier_as_published"]
        assert "oxidation state was not fixed" in values["qualifier_as_published"]
        assert values["source_context_id"] == "greig1927_feo_text_limits_and_fe2o3_effect"

    p4 = contexts["greig1927_feo_text_transition_1689_plusminus10"][1]["values"]
    assert p4["method_class"] == "measured_reduced"
    assert p4["control_temperature_bracket_as_published"] == [
        {"system": "CaO-SiO2", "composition": "15CaO, 85SiO2", "temperature_C": "1698"},
        {"system": "BaO-SiO2", "composition": "15BaO, 85SiO2", "temperature_C": "1679"},
    ]
    assert "not unprinted" in p4["bracket_interpretation"]
    p21 = contexts["greig1927_feo_cristobalite_two_liquids_transition_1689"][1]["values"]
    assert p21["method_class"] == "measured_reduced"
    assert p21["originating_context_id"] == "greig1927_feo_text_transition_1689_plusminus10"


def test_interpolation_and_pb_treatment_mutations_are_detected() -> None:
    document = _document()
    changed = copy.deepcopy(document)
    changed["species"]["CaO"]["context"][0]["values"]["component_amount"] = "0.7"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed)

    changed = copy.deepcopy(document)
    pb = next(
        row
        for row in _context_rows(changed)[
            "greig1927_table9_other_binary_silica_run_products"
        ][1]["values"]["rows"]
        if row["row"] == "8-9"
    )
    pb["heat_treatment_as_published"] = "24 hrs. at 1100°"
    with pytest.raises(AssertionError):
        _assert_table9_binding(changed)

    changed = copy.deepcopy(document)
    extrapolated = _context_rows(changed)[
        "greig1927_cao_less_siliceous_cao"
    ][1]["values"]
    extrapolated["method_class"] = "measured_direct"
    with pytest.raises(AssertionError):
        _assert_less_siliceous_extrapolation_evidence(changed)

    changed = copy.deepcopy(document)
    extrapolated = _context_rows(changed)[
        "greig1927_cao_less_siliceous_cao"
    ][1]["values"]
    extrapolated["extrapolation_method_as_published"] = (
        "Direct chemical analysis."
    )
    with pytest.raises(AssertionError):
        _assert_less_siliceous_extrapolation_evidence(changed)


def test_isolated_migration_binds_temperature_conditions_and_evidence() -> None:
    document = _document()
    source_rows = _source_rows(document)
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert len(result.validation.hard_issues) == 0
    assert not result.queue
    assert len(result.observations) == len(source_rows) == 11
    migrated = {
        observation_id.removeprefix(f"{SOURCE_ID}::"): observation
        for observation_id, observation in result.observations.items()
    }
    assert set(migrated) == set(source_rows)
    assert all(row.value.kind.value != "unavailable" for row in migrated.values())
    assert all(row.identity.quantity.is_value for row in migrated.values())
    assert all(row.identity.species.phase.is_value for row in migrated.values())

    for observation_id, (formula, source) in source_rows.items():
        item = migrated[observation_id]
        assert item.identity.species.formula == formula
        assert item.locator.page == source["locator"]["page"]
        conditions = source["point_conditions"]
        for axis in ("T_C", "composition", "total_pressure_Pa", "fO2_Pa"):
            assert axis in conditions, observation_id
            assert conditions[axis]["locator"] is not None, observation_id
        temperature = Decimal(conditions["T_C"]["state"]["value"])
        assert conditions["T_C"]["state"]["tag"] == "value"
        assert item.identity.total_pressure_Pa.is_unknown
        assert item.identity.fO2_Pa.is_unknown or item.identity.fO2_Pa.is_not_applicable
        mapped_conditions = item.point_conditions or {}

        if source["values"].get("quantity") == "equilibrium_phase_composition":
            assert item.value.kind.value == "point"
            assert item.value.point == Decimal(source["values"]["component_amount"])
            assert item.identity.species.phase.value.value == source["phase"]
            assert item.identity.subtype.value == "wt_percent"
            if source["locator"].get("table") == "VIII":
                assert item.identity.phase_role.is_value
                assert item.identity.phase_role.value.value == "melt"
            assert item.identity.composition.is_value
            assert item.identity.temperature_K.is_value
            assert item.identity.temperature_K.value == temperature + Decimal("273.15")
            for axis in ("temperature_K", "composition", "total_pressure_Pa", "fO2_Pa"):
                assert axis in mapped_conditions, observation_id
                assert mapped_conditions[axis].locator is not None, observation_id
            assert mapped_conditions["temperature_K"].state.is_value
            assert mapped_conditions["temperature_K"].state.value == temperature + Decimal("273.15")
            assert mapped_conditions["total_pressure_Pa"].state.is_unknown
            assert mapped_conditions["fO2_Pa"].state.is_unknown
            assert mapped_conditions["composition"].state.is_value
        else:
            for axis in ("T_C", "temperature_K", "composition", "fO2_Pa"):
                assert axis in mapped_conditions, observation_id
                assert mapped_conditions[axis].locator is not None, observation_id
            assert mapped_conditions["T_C"].state.value == temperature
            assert mapped_conditions["temperature_K"].state.is_value
            assert mapped_conditions["temperature_K"].state.value == temperature + Decimal("273.15")
            assert mapped_conditions["composition"].state.is_unknown
            assert mapped_conditions["fO2_Pa"].state.is_unknown

    silica_a = migrated["greig1927_table8_feo_rich_a_sio2"]
    silica_b = migrated["greig1927_table8_feo_rich_b_sio2"]
    for row, expected in ((silica_a, "55.8"), (silica_b, "55.3")):
        assert row.evidence.class_.value.value == "measured_reduced"
        assert row.value.point == Decimal(expected)
        assert row.derived_from is not None
        assert len(row.derived_from) == 2

    transitions = [
        row for row in migrated.values()
        if row.identity.quantity.value.value == "transition_temperature"
    ]
    assert len(transitions) == 1
    assert transitions[0].observation_id.endswith(
        "greig1927_sro_cristobalite_two_liquids_transition_1693"
    )
    assert transitions[0].value.kind.value == "point"
    assert transitions[0].value.point == Decimal("1693") + Decimal("273.15")
    assert transitions[0].uncertainty.kind.value == "printed"
    assert transitions[0].uncertainty.verbatim["confidence_level"] == "not_stated"
