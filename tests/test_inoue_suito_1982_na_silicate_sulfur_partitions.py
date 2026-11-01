from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import to_plain
from simulator.battery.enums import EvidenceClass, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/literature/extracts/inoue-suito-1982-na-silicate-sulfur-partitions.yaml"
SOURCE_ID = "inoue-suito-1982-na-silicate-sulfur-partitions"


def test_inoue_suito_table_cells_and_migration_are_pinned_to_printed_pages(tmp_path: Path) -> None:
    doc = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    mapping = yaml.safe_load(
        (ROOT / "data/literature/extracts/audits/inoue-suito-1982-na-silicate-sulfur-partitions.mapping.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert [table["printed_rows"] for table in mapping["tables"]] == [20, 7, 6, 4]
    assert len(doc["species"]["S"]["observations"]) >= 40

    migrator = migrate_one_extract(
        ROOT,
        SOURCE,
        index={SOURCE_ID: {**doc["source"], "source_id": SOURCE_ID}},
        aliases={},
    )
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 251
    assert not result.validation.hard_issues

    by_id = {row.observation_id.split("::", 1)[-1]: row for row in result.observations.values()}
    table1 = by_id["inoue_suito_1982_t1_sample_15_metal_S_metal_s_wt"]
    table2 = next(row for key, row in by_id.items() if key.startswith("inoue_suito_1982_t2_sample_98_metal_Si_observed"))
    table3 = next(row for key, row in by_id.items() if key.startswith("inoue_suito_1982_t3_sample_102_metal_S_metal_s_wt"))
    table4 = next(row for key, row in by_id.items() if key.startswith("inoue_suito_1982_t4_sample__3_l_FeO_gamma_feo"))
    table4_n_feo = by_id["inoue_suito_1982_t4_sample__3_l_FeO_n_feo"]
    table2_calculated_si = by_id[
        "inoue_suito_1982_t2_sample_98_metal_Si_charles_calculation_si_in_metal_"
    ]

    # Render-checked cells: PDF pp. 5, 6, 7, and 8, respectively.
    assert table1.value.point == Decimal("0.0013")
    assert table1.locator.page == 5 and table1.locator.table == "1"
    assert table2.value.point == Decimal("0.079")
    assert table2.locator.page == 6 and table2.locator.table == "2"
    assert table3.value.point == Decimal("0.0151")
    assert table3.locator.page == 7 and table3.locator.table == "3"
    assert table4.value.point == Decimal("0.5")
    assert table4.locator.page == 8 and table4.locator.table == "4"
    assert table4_n_feo.value.point == Decimal("0.003")
    assert table4_n_feo.locator.page == 8 and table4_n_feo.locator.table == "4"
    calculated_si_conditions = to_plain(table2_calculated_si)
    assert calculated_si_conditions["point_conditions"]["atmosphere"]["state"]["value"] == (
        "C(aC=1)-CO (pCO=1 atm)"
    )
    assert calculated_si_conditions["point_conditions"]["atmosphere"]["locator"]["page"] == 5
    assert calculated_si_conditions["identity"]["total_pressure_Pa"]["tag"] == "unknown"
    calculated_n_feo_conditions = to_plain(table4_n_feo)
    assert calculated_n_feo_conditions["point_conditions"]["atmosphere"]["state"]["value"] == (
        "C(aC=1)-CO (pCO=1 atm); aFe(l)=0.82"
    )
    assert calculated_n_feo_conditions["point_conditions"]["atmosphere"]["locator"]["page"] == 7
    assert calculated_n_feo_conditions["identity"]["total_pressure_Pa"]["tag"] == "unknown"
    table2_calculations = [
        item
        for item in doc["species"]["Si"]["observations"]
        if "_charles_calculation_" in item["observation_id"]
        or "_callow_calculation_" in item["observation_id"]
    ]
    assert len(table2_calculations) == 14
    assert all(
        item["values"]["point_conditions"]["atmosphere"]["state"]["value"]
        == "C(aC=1)-CO (pCO=1 atm)"
        and item["values"]["point_conditions"]["atmosphere"]["locator"]["page"] == 5
        for item in table2_calculations
    )
    table4_n_feo_rows = [
        item
        for item in doc["species"]["FeO"]["observations"]
        if item["locator"].get("column_header") == "N_FeO"
    ]
    assert len(table4_n_feo_rows) == 4
    assert all(
        item["values"]["point_conditions"]["atmosphere"]["state"]["value"]
        == "C(aC=1)-CO (pCO=1 atm); aFe(l)=0.82"
        and item["values"]["point_conditions"]["atmosphere"]["locator"]["page"] == 7
        for item in table4_n_feo_rows
    )
    table4_composition = to_plain(table4)["identity"]["composition"]
    assert table4_composition["tag"] == "value"
    assert table4_composition["value"]["components"] == [
        ["Na2O", "0.2307692307692307692307692308"],
        ["SiO2", "0.7692307692307692307692307692"],
    ]
    assert [
        item["values"]["composition_ratio_Na2O_to_SiO2_as_published"]
        for item in doc["species"]["FeO"]["observations"]
    ] == [".3", ".3", ".6", ".6", ".9", ".9", "1.3", "1.3"]

    capacities = {
        observation_id: by_id[observation_id]
        for observation_id in (
            "inoue_suito_1982_pure_na2o_log_Cs",
            "inoue_suito_1982_pure_cao_log_Cs",
            "inoue_suito_1982_prior_ratio05_nagashima_log_Cs",
            "inoue_suito_1982_prior_ratio05_denier_log_Cs",
            "inoue_suito_1982_prior_ratio10_nagashima_log_Cs",
            "inoue_suito_1982_prior_ratio10_denier_log_Cs",
        )
    }
    assert [row.value.point for row in capacities.values()] == [
        Decimal("3.44"), Decimal("0.23"), Decimal("-4"), Decimal("-4"),
        Decimal("-3.0"), Decimal("-2.0"),
    ]
    assert all(
        row.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
        for row in capacities.values()
    )
    sodium_pressure_differences = {
        observation_id: by_id[observation_id]
        for observation_id in (
            "inoue_suito_1982_delta_log_pNa_1250C",
            "inoue_suito_1982_delta_log_pNa_1350C",
        )
    }
    assert [
        doc["species"]["Na"]["observations"][
            next(
                i
                for i, item in enumerate(doc["species"]["Na"]["observations"])
                if item["observation_id"] == observation_id
            )
        ]["values"]["value_as_published"]
        for observation_id in sodium_pressure_differences
    ] == ["Δ log P_Na = 1.44", "Δ log P_Na = 1.57"]
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in sodium_pressure_differences.values())
    assert all(row.identity.quantity.is_unknown for row in sodium_pressure_differences.values())
    assert all(row.locator.page == 3 for row in sodium_pressure_differences.values())

    identity = to_plain(table1)["identity"]
    assert identity["temperature_K"]["value"] == "1523.15"
    assert identity["composition"]["tag"] == "value"
    assert identity["composition"]["value"]["components"][0][0] == "Na2O"
    assert identity["composition"]["value"]["components"][0][1] == (
        "0.5243123222611378361025552212"
    )
    assert identity["total_pressure_Pa"]["tag"] == "unknown"
    assert identity["fO2_Pa"]["tag"] == "unknown"
    for row in (table1, table2, table3):
        axes = to_plain(row)["identity"]
        assert axes["temperature_K"]["tag"] == "value"
        assert axes["composition"]["tag"] == "value"
        assert axes["total_pressure_Pa"]["tag"] == "unknown"
        assert axes["fO2_Pa"]["tag"] == "unknown"
    unknown_quantities = {
        row.observation_id.split("::", 1)[-1]
        for row in result.observations.values()
        if row.identity.quantity.is_unknown
    }
    expected_schema_gaps = {
        f"inoue_suito_1982_t{table}_sample_{sample}_normalized_sodium_loss_rate"
        for table, samples in (
            ("1", ("15", "16", "20", "19", "24", "22", "29", "28", "99", "100", "101", "151", "152", "105", "106", "107", "108", "153", "154")),
            ("3", ("102", "155", "156", "157")),
        )
        for sample in samples
    }
    expected_schema_gaps.update(
        {"inoue_suito_1982_log_fS_1250C", "inoue_suito_1982_log_fS_1350C"}
    )
    expected_schema_gaps.update(
        {
            "inoue_suito_1982_delta_log_pNa_1250C",
            "inoue_suito_1982_delta_log_pNa_1350C",
        }
    )
    assert unknown_quantities == expected_schema_gaps
    assert all(not row.identity.species.phase.is_unknown for row in result.observations.values())
    assert all(
        to_plain(row)["identity"]["temperature_K"]["tag"] == "value"
        for row in result.observations.values()
    )
    unknown_compositions = {
        row.observation_id.split("::", 1)[-1]
        for row in result.observations.values()
        if to_plain(row)["identity"].get("composition", {"tag": "unknown"})["tag"] != "value"
    }
    assert unknown_compositions == {
        "inoue_suito_1982_log_fS_1250C",
        "inoue_suito_1982_log_fS_1350C",
        "inoue_suito_1982_delta_log_pNa_1250C",
        "inoue_suito_1982_delta_log_pNa_1350C",
        "inoue_suito_1982_eq20_pNa_1300C",
        "inoue_suito_1982_eq20_pNa_1400C",
    }

    # Mutation pin: changing the source cell changes the migrated value, so the
    # render-backed assertion above will fail if this transcription drifts.
    mutated = deepcopy(doc)
    mutated_row = next(
        row
        for row in mutated["species"]["S"]["observations"]
        if row["observation_id"] == "inoue_suito_1982_t1_sample_15_metal_S_metal_s_wt"
    )
    mutated_row["values"]["value"] = "0.0014"
    mutated_row["values"]["value_as_published"] = "0.0014"
    mutated_row["locator"]["printed_cell"] = "0.0014"
    mutated_path = tmp_path / SOURCE.name
    mutated_path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")
    mutated_migrator = migrate_one_extract(
        ROOT,
        mutated_path,
        index={SOURCE_ID: {**doc["source"], "source_id": SOURCE_ID}},
        aliases={},
    )
    mutated_migrator.finalize()
    changed = next(
        row
        for row in mutated_migrator.result.observations.values()
        if row.observation_id.endswith("inoue_suito_1982_t1_sample_15_metal_S_metal_s_wt")
    )
    assert changed.value.point == Decimal("0.0014")
