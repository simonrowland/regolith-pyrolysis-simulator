"""Rendered-source pins and isolated migration for Allendorf et al. (2005)."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/allendorf-2005-sand1196-refractory-corrosion.yaml"
SOURCE_ID = "allendorf-2005-sand1196-refractory-corrosion"


def _source_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    }


def _axis_has_no_reported_value(axis: object) -> bool:
    return axis is None or axis.is_unknown


def test_every_printed_point_has_located_conditions_and_no_assumed_pressure() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for _species, observation in _source_observations(document).values():
        for point in observation["values"]["series"]:
            conditions = point["point_conditions"]
            assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
            temperature_axis = next(
                axis for axis in ("temperature_K", "temperature_C") if axis in conditions
            )
            assert conditions[temperature_axis]["locator"].get("page") is not None
            for axis in (
                "temperature_K",
                "temperature_C",
                "fO2_Pa",
                "total_pressure_Pa",
                "composition",
            ):
                if axis not in conditions:
                    continue
                assert conditions[axis]["state"]["tag"] in {"value", "unknown"}
                assert conditions[axis]["locator"].get("page") is not None


def test_printed_measurements_and_table5_1_rows_match_rendered_source() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = _source_observations(document)
    assert len(observations) == 9
    assert document["fidelity_samples"] == [
        {
            "path": "species.Na.observations[allendorf_2005_table5_1_extractive_total_sodium].values.series[3].value",
            "value": "221",
            "note": (
                "Page-99 render confirms 221 in Table 5-1 row 5, extraction-sample Na column; "
                "pdftotext separates the final digit as 22 1."
            ),
            "locator": {"page": 99, "table": "5-1", "row": 5},
        }
    ]

    table_rows = document["extraction"]["printed_table_rows"]
    assert document["extraction"]["printed_table_row_count"] == 6
    assert len(table_rows) == 6
    assert [row["port"] for row in table_rows] == [
        "Right side midtank port",
        "Right side exhaust port",
        "Right side uptank port",
        "Fill end",
        "Left side downtank port",
        "Left side refiner-entrance",
    ]
    assert [row["extraction_sample_na_ppm"] for row in table_rows] == [
        "185", "202", "158", "n.a.", "221", "17"
    ]
    assert [row["naoh_elif_ppm"] for row in table_rows] == [
        "125", "117", "143", "90", "31", "n.a."
    ]
    assert observations["allendorf_2005_table5_1_extractive_total_sodium"][1]["locator"]["page"] == 99
    assert observations["allendorf_2005_table5_1_elif_naoh"][1]["locator"]["page"] == 99
    assert (
        observations["allendorf_2005_table5_1_extractive_total_sodium"][1]["values"]["method_class"]
        == "measured_direct"
    )
    assert (
        observations["allendorf_2005_table5_1_elif_naoh"][1]["values"]["method_class"]
        == "measured_direct"
    )
    extractive_values = observations[
        "allendorf_2005_table5_1_extractive_total_sodium"
    ][1]["values"]["series"]
    elif_values = observations["allendorf_2005_table5_1_elif_naoh"][1]["values"]["series"]
    assert [point["value"] for point in extractive_values] == [
        "185", "202", "158", "221", "17"
    ]
    assert [point["value"] for point in elif_values] == [
        "125", "117", "143", "90", "31"
    ]
    assert [point["locator"]["row"] for point in extractive_values] == [1, 2, 3, 5, 6]
    assert [point["locator"]["row"] for point in elif_values] == [1, 2, 3, 4, 5]

    faber = observations["allendorf_2005_faber_beerkens_naoh_concentrations"][1]
    assert faber["locator"]["page"] == 56
    assert [point["value"] for point in faber["values"]["series"]] == [
        {"kind": "interval", "interval_low": "50", "interval_high": "60"},
        {"kind": "interval", "interval_low": "140", "interval_high": "200"},
        {"kind": "interval", "interval_low": "220", "interval_high": "260"},
    ]

    walsh = observations["allendorf_2005_walsh_naoh_concentration"][1]
    assert walsh["locator"]["page"] == 66
    assert walsh["values"]["value"] == "125"
    assert walsh["uncertainty"]["value"] == "25"
    assert "confidence level not stated" in walsh["uncertainty"]["note"]

    monofrax = observations["allendorf_2005_monofrax_naoh_concentration"][1]
    assert monofrax["locator"]["page"] == 68
    assert monofrax["values"]["value"] == "640"
    assert monofrax["uncertainty"]["value"] == "65"
    assert monofrax["values"]["composition_wt_pct"] == {
        "SiO2": "72.7",
        "Na2O": "13.92",
        "K2O": "0.5",
        "CaO": "8.6",
        "MgO": "4.21",
    }
    assert monofrax["values"]["series"][0]["T_K"] == "1883"
    assert monofrax["values"]["series"][0]["point_conditions"]["duration_h"]["state"]["value"] == "75"

    wereszczak = observations["allendorf_2005_werezczak_recession_distances"][1]
    assert wereszczak["locator"]["page"] == 62
    assert [point["value"] for point in wereszczak["values"]["series"]] == ["6", "13"]
    assert [point["exposure_time_years"] for point in wereszczak["values"]["series"]] == ["10", "6.5"]

    faber_verheijen = observations["allendorf_2005_faber_verheijen_recession_rates"][1]
    assert faber_verheijen["values"]["series"][0]["value"] == "100"
    assert faber_verheijen["values"]["series"][0].get("approximate", False) is False
    assert faber_verheijen["values"]["series"][1]["value"] == {
        "kind": "interval", "interval_low": "5", "interval_high": "10"
    }
    assert faber_verheijen["values"]["series"][1]["T_range_C"] == ["1530", "1580"]
    later_walsh = observations["allendorf_2005_walsh_later_naoh_concentration"][1]
    assert later_walsh["values"]["value"] == "125"
    assert later_walsh["values"]["approximate"] is True
    assert later_walsh["locator"]["page"] == 82
    assert (
        "Walsh, Moore, Neufeld, Lemings, Brown, and Wu (2001)"
        in later_walsh["values"]["attribution"]
    )
    van_limpt = observations["allendorf_2005_van_limpt_unfavourable_burner_naoh_concentration"][1]
    assert van_limpt["locator"]["page"] == 61
    assert van_limpt["values"]["method_class"] == "quoted_attributed"
    assert van_limpt["values"]["value"] == {
        "kind": "interval", "interval_low": "350", "interval_high": "400"
    }
    assert van_limpt["values"]["value_as_published"] == "as high as 350–400 ppm"
    assert "unfavourable burner orientation" in van_limpt["values"]["qualifier"]
    assert "van Limpt" in van_limpt["values"]["attribution"]


def test_isolated_migration_keeps_all_points_typed_refused_with_known_phases() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_observations = _source_observations(document)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    result = migrator.result
    records = list(result.observations.values())
    assert len(records) == 21
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert sum(record.value.kind is ValueKind.UNAVAILABLE for record in records) == 21
    assert all(record.identity.quantity.is_unknown for record in records)
    assert all(not record.identity.species.phase.is_unknown for record in records)
    for record in records:
        conditions = record.point_conditions
        temperature = conditions.get("temperature_K") or conditions.get("temperature_C")
        assert temperature is not None
        assert temperature.state is not None
        assert temperature.locator is not None
        for axis in ("fO2_Pa", "composition"):
            assert axis in conditions, record.observation_id
            assert conditions[axis].state is not None
            assert conditions[axis].locator is not None
        assert _axis_has_no_reported_value(record.identity.total_pressure_Pa)

    parent_ids = {source_id for source_id, (_species, _obs) in source_observations.items()}
    migrated_parent_ids = {
        record.observation_id.split("::", 2)[1]
        for record in records
    }
    assert migrated_parent_ids == parent_ids

    by_parent = {
        parent_id: [
            record
            for record in records
            if record.observation_id.split("::", 2)[1] == parent_id
        ]
        for parent_id in parent_ids
    }
    assert len(by_parent["allendorf_2005_table5_1_extractive_total_sodium"]) == 5
    assert len(by_parent["allendorf_2005_table5_1_elif_naoh"]) == 5
    assert len(by_parent["allendorf_2005_faber_beerkens_naoh_concentrations"]) == 3

    monofrax = by_parent["allendorf_2005_monofrax_naoh_concentration"][0]
    assert monofrax.identity.temperature_K.is_value
    assert monofrax.identity.temperature_K.value == Decimal("1883")
    assert monofrax.identity.composition.is_value
    assert _axis_has_no_reported_value(monofrax.identity.total_pressure_Pa)
    assert _axis_has_no_reported_value(monofrax.identity.fO2_Pa)
    condition_composition = monofrax.point_conditions["composition"]
    assert condition_composition.state.is_value
    assert condition_composition.locator is not None
    assert condition_composition.locator.page == 67

    wereszczak = by_parent["allendorf_2005_werezczak_recession_distances"]
    assert {str(row.identity.temperature_K.value) for row in wereszczak} == {"1873.15"}
    assert all(
        _axis_has_no_reported_value(row.identity.total_pressure_Pa)
        for row in wereszczak
    )
    assert all(
        _axis_has_no_reported_value(row.identity.fO2_Pa) for row in wereszczak
    )

    table_rows = [
        record
        for parent in (
            "allendorf_2005_table5_1_extractive_total_sodium",
            "allendorf_2005_table5_1_elif_naoh",
        )
        for record in by_parent[parent]
    ]
    assert all(_axis_has_no_reported_value(row.identity.temperature_K) for row in table_rows)
    assert all(
        _axis_has_no_reported_value(row.identity.total_pressure_Pa)
        for row in table_rows
    )
    assert all(
        _axis_has_no_reported_value(row.identity.fO2_Pa) for row in table_rows
    )
    assert all(
        row.point_conditions["temperature_K"].state.is_unknown
        for row in table_rows
    )
