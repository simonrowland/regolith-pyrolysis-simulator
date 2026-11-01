from __future__ import annotations

import csv
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import to_plain
from simulator.battery.enums import EvidenceClass, ValueKind
from simulator.battery.generators.bench import activity_request_for_engine
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

    table1_initial_s = {
        "15": ".290", "16": ".003", "20": ".294", "19": ".003", "24": ".574",
        "22": ".010", "29": ".994", "28": ".003", "98": ".994", "99": ".574",
        "100": ".574", "101": ".994", "151": ".003", "152": ".003", "105": ".574",
        "106": ".574", "107": ".574", "108": ".994", "153": ".003", "154": ".003",
    }
    table1_final_s = {
        "15": ".0013", "16": ".0012", "20": ".0029", "19": ".0024", "24": ".0346",
        "22": ".0080", "29": ".511", "28": ".0386", "98": ".359", "99": ".0421",
        "100": ".0121", "101": ".0034", "151": ".0194", "152": ".0113", "105": ".164",
        "106": ".0299", "107": ".0095", "108": ".0032", "153": ".0196", "154": ".0093",
    }
    table3_initial_s = {
        "102": ".574", "103": ".574", "104": ".574", "155": ".994", "156": ".994", "157": ".994",
    }
    table3_final_s = {
        "102": ".0151", "103": ".0294", "104": ".0489", "155": ".0046", "156": ".0074", "157": ".0096",
    }
    contexts = {row["observation_id"]: row for row in doc["species"]["Na2O"]["context"]}
    table1_start = contexts["inoue_suito_1982_table1_starting_compositions"]["values"]["table_transcription"]
    table3_start = contexts["inoue_suito_1982_table3_starting_compositions"]["values"]["table_transcription"]
    assert {row["sample_id"]: row["metal_S"] for row in table1_start} == table1_initial_s
    assert {row["sample_id"]: row["metal_S"] for row in table3_start} == table3_initial_s

    source_rows = {
        row["observation_id"]: row
        for species in doc["species"].values()
        for row in species.get("observations", [])
    }
    table1_final_rows = {
        sample_id: source_rows[f"inoue_suito_1982_t1_sample_{sample_id}_metal_S_metal_s_wt"]
        for sample_id in table1_final_s
    }
    table3_final_rows = {
        sample_id: source_rows[f"inoue_suito_1982_t3_sample_{sample_id}_metal_S_metal_s_wt"]
        for sample_id in table3_final_s
    }
    assert {sample_id: row["values"]["value"] for sample_id, row in table1_final_rows.items()} == table1_final_s
    assert {sample_id: row["values"]["value"] for sample_id, row in table3_final_rows.items()} == table3_final_s
    assert all(row["values"]["phase_label"] == "metal" for row in (*table1_final_rows.values(), *table3_final_rows.values()))
    with (ROOT / "data/literature/extracts/audits/inoue-suito-1982-na-silicate-sulfur-partitions.audit.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        audit_rows = list(csv.DictReader(stream))
    assert {
        row["row"]: row["printed_cell"]
        for row in audit_rows
        if row["pdf_page"] == "5" and row["printed_table"] == "1"
        and row["column"] == "metal S, wt%" and row["row"] in {"151", "152"}
    } == {"151": ".0194", "152": ".0113"}

    final_total_s = {
        **{
            "15": ".909", "16": ".845", "20": ".847", "19": ".761", "24": "1.19", "22": ".588",
            "29": "1.08", "28": ".308",
            "98": "2.35", "99": "2.03", "100": "2.12", "101": "3.84", "151": ".314", "152": ".496",
            "105": "1.55", "106": "1.97", "107": "1.85", "108": "3.85", "153": ".369", "154": ".521",
        },
        "102": "2.04", "103": "2.19", "104": "2.82", "155": "3.68", "156": "3.70", "157": "3.41",
    }
    total_s_rows = {
        row["values"]["sample_id"]: row
        for row in doc["species"]["S"]["observations"]
        if row["observation_id"].endswith("_slag_S_slag_total_sulfur_wt")
    }
    assert {sample_id: row["values"]["value"] for sample_id, row in total_s_rows.items()} == final_total_s
    audit_cells = {
        (row["pdf_page"], row["printed_table"], row["row"], row["column"]): row["printed_cell"]
        for row in audit_rows
    }
    for sample_id, value in final_total_s.items():
        page, table = ("7", "3") if sample_id in table3_final_s else ("5", "1")
        assert audit_cells[(page, table, sample_id, "slag total sulfur, wt%")] == value

    expected_ratios = {
        "98": ".32", "99": ".58", "100": ".90", "101": "1.23", "151": ".36", "105": ".31", "106": ".56",
    }
    table2_rows = [
        row for species in doc["species"].values() for row in species.get("observations", [])
        if row["observation_id"].startswith("inoue_suito_1982_t2_sample_")
    ]
    assert len(table2_rows) == 19
    assert {
        (row["values"]["sample_id"], row["values"]["composition_ratio_Na2O_to_SiO2_as_published"])
        for row in table2_rows
    } == set(expected_ratios.items())
    assert all(
        row["values"]["composition_ratio_locator"]
        == {
            "page": 6,
            "table": "2",
            "row_label": row["values"]["sample_id"],
            "column_header": "Na2O/SiO2 (mole ratio)",
            "printed_cell": row["values"]["composition_ratio_Na2O_to_SiO2_as_published"],
        }
        for row in table2_rows
    )
    assert {
        (sample_id, audit_cells[("6", "2", sample_id, "Na2O/SiO2 (mole ratio)")])
        for sample_id in expected_ratios
    } == set(expected_ratios.items())

    absent_components = {
        **{sample_id: "Al2O3" for sample_id in ("15", "16", "20", "19", "24", "22", "29", "28")},
        **{sample_id: "MgO" for sample_id in ("99", "100", "101", "151", "152", "105", "106", "107", "108", "153", "154")},
    }
    assert len(absent_components) == 19
    for sample_id, component in absent_components.items():
        composition = source_rows[
            f"inoue_suito_1982_t1_sample_{sample_id}_normalized_sodium_loss_rate"
        ]["point_conditions"]["composition"]["state"]["value"]
        assert component not in {name for name, _amount in composition["components"]}

    expected_rates = {
        ("1", "15"): ("2.73e-3", "2.73×10^-3"), ("1", "16"): ("0.00118", "1.18"),
        ("1", "20"): ("0.00234", "2.34"), ("1", "19"): ("0.00198", "1.98"),
        ("1", "24"): ("0.00056", ".56"), ("1", "22"): ("0.00054", ".54"),
        ("1", "29"): ("0.00026", ".26"), ("1", "28"): ("0.00017", ".17"),
        ("1", "99"): ("0.00123", "1.23"), ("1", "100"): ("0.00159", "1.59"),
        ("1", "101"): ("0.00601", "6.01"), ("1", "151"): ("0.00055", ".55"),
        ("1", "152"): ("0.00018", ".18"), ("1", "105"): ("0.00163", "1.63"),
        ("1", "106"): ("0.00273", "2.73"), ("1", "107"): ("0.00511", "5.11"),
        ("1", "108"): ("0.0117", "11.7"), ("1", "153"): ("0.00099", ".99"),
        ("1", "154"): ("0.00058", ".58"), ("3", "102"): ("1.52e-3", "1.52×10^-3"),
        ("3", "155"): ("0.00428", "4.28"), ("3", "156"): ("0.00246", "2.46"),
        ("3", "157"): ("0.00238", "2.38"),
    }
    rate_rows = {
        (match[1], match[2]): row
        for row in source_rows.values()
        if (match := re.match(
            r"inoue_suito_1982_t([13])_sample_(\d+)_normalized_sodium_loss_rate$", row["observation_id"]
        ))
    }
    assert set(rate_rows) == set(expected_rates)
    for key, (normalized, printed) in expected_rates.items():
        row = rate_rows[key]
        assert Decimal(row["values"]["value"]) == Decimal(normalized)
        assert row["values"]["value_as_published"] == printed
        assert row["locator"]["printed_cell"] == printed
        assert row["values"]["note"] == "The first numeric cell prints ×10^-3 as the column multiplier; apply it to every row."

    callow_citation = "29) R.J. Callow: Trans. Faraday Soc., 46 (1950), 663."
    charles_citation = "30) R. J. Charles: J. Amer. Ceram. Soc., 50 (1967), 631."
    sanders_citation = "22) D. M. Sanders and W. K. Haller: J. Amer. Ceram. Soc., 60 (1977), 138."
    not_printed = {"state": "unknown", "reason": "not_printed"}
    derived_compositions = {
        "Na2O·2SiO2": {
            "components": {
                "Na2O": "0.3333333333333333333333333333",
                "SiO2": "0.6666666666666666666666666667",
            },
            "derivation": "identity-derived: Na2O·2SiO2 formula-unit ratio 1:2; x(Na2O)=1/3 and x(SiO2)=2/3.",
        },
        "xSiO2 = 0.5": {
            "components": {"Na2O": "0.5", "SiO2": "0.5"},
            "derivation": "identity-derived binary complement: x(Na2O)=1-x(SiO2) from printed x(SiO2)=0.5.",
        },
        "xSiO2 = 0.67": {
            "components": {"Na2O": "0.33", "SiO2": "0.67"},
            "derivation": "identity-derived binary complement: x(Na2O)=1-x(SiO2) from printed x(SiO2)=0.67.",
        },
    }
    derived_basis = "identity_derived_binary_mole_fraction"
    haller_standard_state = {
        "state": "value",
        "value": "relative to the liquid standard state for Na2O·2SiO2 glass melt",
    }
    expected_quote_rows = {
        "inoue_suito_1982_na2o_activity_haller_1250": {
            "species": "Na2O", "activity": "6.56e-8", "composition": "Na2O·2SiO2",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": haller_standard_state,
            "citation": sanders_citation, "attribution": "Sanders and Haller", "page": "2",
            "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_na2o_activity_haller_1350": {
            "species": "Na2O", "activity": "1.98e-7", "composition": "Na2O·2SiO2",
            "temperature": "1 350 °C", "T_C": "1350", "standard_state": haller_standard_state,
            "citation": sanders_citation, "attribution": "Sanders and Haller", "page": "2",
            "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_na2o_activity_callow_xsio2_050": {
            "species": "Na2O", "activity": "1.1e-7", "composition": "xSiO2 = 0.5",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": callow_citation, "attribution": "Inoue and Suito calculation from Callow silica activity",
            "page": "3", "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_na2o_activity_callow_xsio2_067": {
            "species": "Na2O", "activity": "6.6e-9", "composition": "xSiO2 = 0.67",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": callow_citation, "attribution": "Inoue and Suito calculation from Callow silica activity",
            "page": "3", "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_na2o_activity_charles_xsio2_050": {
            "species": "Na2O", "activity": "1.6e-6", "composition": "xSiO2 = 0.5",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": charles_citation, "attribution": "Inoue and Suito calculation from Charles silica activity",
            "page": "3", "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_na2o_activity_charles_xsio2_067": {
            "species": "Na2O", "activity": "1.7e-8", "composition": "xSiO2 = 0.67",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": charles_citation, "attribution": "Inoue and Suito calculation from Charles silica activity",
            "page": "3", "composition_page": "3", "column": "Na2O activity",
        },
        "inoue_suito_1982_sio2_activity_callow_xsio2_050": {
            "species": "SiO2", "activity": ".13", "composition": "xSiO2 = 0.5",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": callow_citation, "attribution": "Callow", "page": "3",
            "composition_page": "3", "column": "SiO2(l) activity",
        },
        "inoue_suito_1982_sio2_activity_callow_xsio2_067": {
            "species": "SiO2", "activity": ".6", "composition": "xSiO2 = 0.67",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": callow_citation, "attribution": "Callow", "page": "3",
            "composition_page": "3", "column": "SiO2(l) activity",
        },
        "inoue_suito_1982_sio2_activity_charles_xsio2_050": {
            "species": "SiO2", "activity": ".009", "composition": "xSiO2 = 0.5",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": charles_citation, "attribution": "Charles", "page": "3",
            "composition_page": "3", "column": "SiO2(l) activity",
        },
        "inoue_suito_1982_sio2_activity_charles_xsio2_067": {
            "species": "SiO2", "activity": ".38", "composition": "xSiO2 = 0.67",
            "temperature": "1 250 °C", "T_C": "1250", "standard_state": not_printed,
            "citation": charles_citation, "attribution": "Charles", "page": "3",
            "composition_page": "3", "column": "SiO2(l) activity",
        },
    }
    mapping_quote_rows = {
        row["observation_id"]: row
        for row in mapping["quoted_activity_provenance"]["observations"]
    }
    assert set(mapping_quote_rows) == set(expected_quote_rows)
    activity_audit_rows = {
        (row["column"], row["printed_cell"]): row
        for row in audit_rows
        if row["column"] in {"Na2O activity", "SiO2(l) activity"}
    }
    assert len(activity_audit_rows) == 10
    for observation_id, expected in expected_quote_rows.items():
        row = source_rows[observation_id]
        assert row["values"]["activity"] == expected["activity"]
        assert row["locator"]["printed_cell"] == expected["activity"]
        assert str(row["locator"]["page"]) == expected["page"]
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"] == expected["attribution"]
        assert row["values"]["T_C"] == expected["T_C"]
        assert row["values"]["melt_composition_as_printed"] == expected["composition"]
        composition_map = derived_compositions[expected["composition"]]
        assert row["values"]["composition_mol"] == composition_map["components"]
        assert row["values"]["composition_mol_basis"] == derived_basis
        assert row["values"]["composition_derivation"] == composition_map["derivation"]
        assert row["values"]["temperature_as_printed"] == expected["temperature"]
        assert row["values"]["standard_state_as_printed"] == expected["standard_state"]
        assert row["values"]["reference_list_entry_as_printed"] == expected["citation"]
        assert row["standard_state"] == (
            "liquid Na2O"
            if expected["standard_state"]["state"] == "value"
            else "not printed"
        )
        composition = row["point_conditions"]["composition"]
        composition_value = composition["state"]["value"]
        assert composition_value["basis"] == derived_basis
        assert composition_value["amount_basis"] == "mole_fraction"
        assert dict(composition_value["components"]) == composition_map["components"]
        assert composition["locator"]["page"] == int(expected["composition_page"])
        if expected["composition_page"] != expected["page"]:
            assert composition["locator"]["printed_cell"] == expected["composition"]
        else:
            assert composition["locator"]["printed_cell"] == expected["activity"]
        assert composition["inference"]["relation"] == derived_basis
        inference_inputs = " ".join(composition["inference"]["inputs"]).casefold()
        if expected["composition"] == "Na2O·2SiO2":
            assert "1:2" in inference_inputs
            assert "xna2o=1/3" in inference_inputs
            assert "xsio2=2/3" in inference_inputs
        else:
            assert "1-xsio2" in inference_inputs
            assert expected["composition"].casefold() in inference_inputs
        if expected["composition_page"] != expected["page"]:
            assert composition["locator"]["paragraph"] == "Na2O activity calculation"
            assert composition["locator"]["printed_cell"] == expected["composition"]
        printed_temperature = row["values"]["point_conditions"]["temperature_C"]
        assert printed_temperature["state"]["value"] == expected["T_C"]
        assert printed_temperature["locator"]["page"] == int(expected["page"])
        mapped = mapping_quote_rows[observation_id]
        assert mapped["activity_species"] == expected["species"]
        assert mapped["page"] == int(expected["page"])
        assert mapped["value_as_printed"] == expected["activity"]
        assert mapped["melt_composition_as_printed"] == expected["composition"]
        assert mapped["temperature_as_printed"] == expected["temperature"]
        assert mapped["standard_state_as_printed"] == expected["standard_state"]
        assert mapped["reference_list_entry_as_printed"] == expected["citation"]
        if expected["composition_page"] != expected["page"]:
            assert mapped["melt_composition_locator"] == {
                "page": int(expected["composition_page"]),
                "paragraph": "Na2O activity calculation",
                "printed_cell": expected["composition"],
            }
        audited = activity_audit_rows[(expected["column"], expected["activity"])]
        assert audited["pdf_page"] == expected["page"]
        assert expected["citation"].split(" ", 1)[1] in audited["confirmation"]
        if expected["standard_state"]["state"] == "value":
            assert expected["standard_state"]["value"] in audited["confirmation"]
        else:
            assert "standard state not printed" in audited["confirmation"]
        if expected["composition_page"] != expected["page"]:
            assert f"melt composition at PDF p. {expected['composition_page']}: {expected['composition']}" in audited["confirmation"]
    rate_audit = [row for row in audit_rows if row["column"] == "ΔNa/(Na_init·t)"]
    assert len(rate_audit) == 23
    assert all("inherits ×10^-3 column multiplier" in row["confirmation"] for row in rate_audit)

    captions = mapping["figures"]["captions"]
    assert [caption["figure"] for caption in captions] == list(range(1, 12))
    assert [caption["page"] for caption in captions] == [2, 3, 3, 4, 4, 6, 6, 6, 8, 9, 9]
    figure_candidates = [
        context["values"]["figure_candidates"]
        for species in doc["species"].values()
        for context in species.get("context", [])
        if "figure_candidates" in context.get("values", {})
    ]
    assert figure_candidates == [["Figs. 1–11; no plotted values digitized"]]
    assert "activities of Na2O" in captions[1]["caption"]
    assert "Sulfide capacities" in captions[6]["caption"]
    audit_md = (ROOT / "data/literature/extracts/audits/inoue-suito-1982-na-silicate-sulfur-partitions.audit.md").read_text(
        encoding="utf-8"
    )
    assert "Fig. 2 gives sulfur distribution ratios" not in audit_md
    assert "Fig. 5 gives sulfide capacity" not in audit_md

    migrator = migrate_one_extract(
        ROOT,
        SOURCE,
        index={SOURCE_ID: {**doc["source"], "source_id": SOURCE_ID}},
        aliases={},
    )
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 267
    assert not result.validation.hard_issues

    by_id = {row.observation_id.split("::", 1)[-1]: row for row in result.observations.values()}
    assert sum(row.value.kind is ValueKind.POINT for row in result.observations.values()) == 240
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()) == 27
    assert set(by_id) == set(source_rows)
    for row in result.observations.values():
        plain = to_plain(row)
        assert plain["identity"]["temperature_K"]["tag"] == "value"
        assert plain["point_conditions"]
        assert any("state" in condition and "locator" in condition for condition in plain["point_conditions"].values())
    for sample_id, expected in {"151": ".0194", "152": ".0113"}.items():
        migrated = by_id[f"inoue_suito_1982_t1_sample_{sample_id}_metal_S_metal_s_wt"]
        assert migrated.value.point == Decimal(expected)
    for sample_id, expected in final_total_s.items():
        table = "3" if sample_id in table3_final_s else "1"
        migrated = by_id[f"inoue_suito_1982_t{table}_sample_{sample_id}_slag_S_slag_total_sulfur_wt"]
        assert migrated.value.point == Decimal(expected)
    for observation_id, expected in expected_quote_rows.items():
        migrated = by_id[observation_id]
        assert migrated.value.point == Decimal(expected["activity"])
        assert migrated.evidence.class_.value == "quoted_attributed"
        plain = to_plain(migrated)
        assert plain["identity"]["temperature_K"]["value"] == str(
            Decimal(expected["T_C"]) + Decimal("273.15")
        )
        reference_state = plain["identity"]["reference_state"]
        if expected["standard_state"]["state"] == "value":
            assert reference_state["tag"] == "value"
            assert reference_state["value"]["convention"] == "raoultian_pure_endmember"
            assert reference_state["value"]["endmember"]["formula"] == "Na2O"
            assert reference_state["value"]["endmember"]["phase"] == {"tag": "value", "value": "l"}
        else:
            assert reference_state["tag"] == "unknown"
        composition = plain["point_conditions"]["composition"]
        composition_value = composition["state"]["value"]
        composition_map = derived_compositions[expected["composition"]]
        assert composition_value["basis"] == derived_basis
        assert composition_value["amount_basis"] == "mole_fraction"
        assert dict(composition_value["components"]) == composition_map["components"]
        assert composition["locator"]["page"] == int(expected["composition_page"])
        request = activity_request_for_engine(
            result.experiments[migrated.experiment_id], migrated, "openimcc"
        )
        readiness = to_plain(request.readiness)
        assert not any(gap["waypoint"] == "normalized_composition" for gap in readiness["gaps"])
        if expected["standard_state"]["state"] == "value":
            assert readiness["status"] == "ready"
            assert request.payload["composition_mol"] == {
                name: float(value) for name, value in composition_map["components"].items()
            }
        else:
            assert readiness["status"] == "not_applicable"
            assert readiness["gaps"] == [
                {
                    "waypoint": "reference_state",
                    "reason": "missing_evidence",
                    "missing": ["identity.reference_state"],
                }
            ]
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
    final_composition_rows = {}
    for observation_id, source_row in source_rows.items():
        if not observation_id.startswith(("inoue_suito_1982_t1_sample_", "inoue_suito_1982_t2_sample_")):
            continue
        composition = (source_row.get("point_conditions") or {}).get("composition") or {}
        state = composition.get("state") or {}
        value = state.get("value") or {}
        if state.get("tag") == "value" and value.get("amount_basis") == "mass_percent":
            final_composition_rows[observation_id] = composition
    assert len(final_composition_rows) == 185
    for observation_id, composition in final_composition_rows.items():
        sample_id = re.search(r"_t[12]_sample_(\d+)_", observation_id).group(1)
        locator = composition["locator"]
        assert locator["page"] == 6
        assert locator["table"] == "1"
        assert locator["row_label"] == sample_id
        source_components = composition["state"]["value"]["components"]
        assert all(Decimal(amount) != 0 for _, amount in source_components)
        migrated_conditions = to_plain(by_id[observation_id])["point_conditions"]
        migrated_composition = migrated_conditions["composition"]
        assert migrated_composition["locator"]["page"] == 6
        assert migrated_composition["locator"]["table"] == "1"
        migrated_components = to_plain(by_id[observation_id])["identity"]["composition"]["value"]["components"]
        assert all(Decimal(amount) != 0 for _, amount in migrated_components)
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
