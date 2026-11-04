from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    Phase,
    ReferenceStateConvention,
    ValueKind,
)
from tests.battery.test_migrate import assert_extract_source_mutation_fails
from tools.validate_literature_extracts import check_all_fidelity_samples_match

ROOT = Path(__file__).resolve().parents[2]
SID = "eliezer-howald-1978-potassium-silicate-vapor-pressure"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"


def _doc():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(doc):
    return {
        observation["observation_id"]: observation
        for group in doc["species"].values()
        for observation in group.get("observations", [])
    }


def _source_context(doc):
    return {
        observation["observation_id"]: observation
        for group in doc["species"].values()
        for observation in group.get("context", [])
    }


def _migrated():
    doc = _doc()
    with patch.object(migrate_module, "discover_extracts", return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[1]: row
        for row in migrator.result.observations.values()
        if row.source_id == SID
    }
    return doc, rows, migrator


def test_eliezer_howald_source_cells_and_render_pins():
    doc = _doc()
    assert doc["source_id"] == SID
    assert check_all_fidelity_samples_match(doc) == []

    source = _source_observations(doc)
    contexts = _source_context(doc)
    assert len(source) == 17
    numeric_pins = {
        "eliezer_howald_1978_table1_x021537_hm827": "-82.7",
        "eliezer_howald_1978_table1_x033497_hm594": "-59.4",
        "eliezer_howald_1978_table1_x028548_hm632": "-63.2",
        "eliezer_howald_1978_table1_x034025_hm630": "-63.0",
        "eliezer_howald_1978_phase_diagram_x0102_loggamma2": "0.0212",
        "eliezer_howald_1978_phase_diagram_x0297_loggamma2": "0.0459",
        "eliezer_howald_1978_phase_diagram_x0102_sio2_activity": "0.94293",
        "eliezer_howald_1978_phase_diagram_x0297_sio2_activity": "0.78139",
        "eliezer_howald_1978_gibbs_duhem_x0293_loggamma2": "-0.012",
        "eliezer_howald_1978_tridymite_ko05_activity_1743": "0.7157e-6",
        "eliezer_howald_1978_table1_x0390_excess_enthalpy_at_1500k": "-15.0",
    }
    for observation_id, expected in numeric_pins.items():
        values = source[observation_id]["values"]
        actual = values.get("value", values.get("gamma", values.get("activity")))
        assert str(actual) == expected, observation_id

    assert source["eliezer_howald_1978_table1_x021537_hm827"]["locator"]["table"] == "I"
    assert source["eliezer_howald_1978_table1_x021537_hm827"]["locator"]["page"] == 3
    assert source["eliezer_howald_1978_tridymite_ko05_activity_1743"]["locator"]["page"] == 5
    assert contexts["eliezer_howald_1978_figure3_context"]["values"]["marker_legend_as_printed"].startswith(
        "Diamonds: nonequilibrium glass"
    )
    table3 = contexts["eliezer_howald_1978_table3_standard_state_context"]["values"]
    assert table3["printed_row_count"] == 8
    assert len(table3["rows_as_printed"]) == 8
    assert sum(
        value is not None
        for row in table3["rows_as_printed"]
        for key, value in row.items()
        if key != "material"
    ) == 47
    assert table3["rows_as_printed"][-1] == {
        "material": "K2Si2O5(s)",
        "H°298.15_cal_mol-1": "-609691.4",
        "H°1000_cal_mol-1": "-557657.4",
        "Phi°1000_cal_mol-1_K-1": "67.1074",
        "Cp_a": "51.638",
        "Cp_10^3b": "5.504",
        "Cp_10^6c": "-1.55864",
        "Cp_10^9d": None,
    }
    labels = contexts["eliezer_howald_1978_table2_enthalpy_coefficients_context"]["values"]["row_labels_as_printed"]
    assert labels[5] == "Corrected to 1500 K^{6,11}"


def test_every_migrated_numeric_value_and_conditions():
    doc, rows, migrator = _migrated()
    source = _source_observations(doc)
    assert len(rows) == 17
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()

    expected = {
        "eliezer_howald_1978_tridymite_ko05_activity_1743": (
            "activity", "cr", Decimal("7.157E-7"), "1743", "0.0234"
        ),
        "eliezer_howald_1978_phase_diagram_x0102_loggamma2": (
            "activity_coefficient", "l", Decimal("1.050025872110884390048590125"), "1784", "0.102"
        ),
        "eliezer_howald_1978_phase_diagram_x0297_loggamma2": (
            "activity_coefficient", "l", Decimal("1.111475771060506411027077244"), "1150", "0.297"
        ),
        "eliezer_howald_1978_phase_diagram_x0102_sio2_activity": (
            "activity", "l", Decimal("0.94293"), "1784", "0.102"
        ),
        "eliezer_howald_1978_phase_diagram_x0297_sio2_activity": (
            "activity", "l", Decimal("0.78139"), "1150", "0.297"
        ),
        "eliezer_howald_1978_gibbs_duhem_x0293_loggamma2": (
            "activity_coefficient", "l", Decimal("0.9727472237769651030569188396"), "1784", "0.293"
        ),
    }
    for observation_id, (quantity, phase, value, temperature, x_ko05) in expected.items():
        row = rows[observation_id]
        assert row.value.kind is ValueKind.POINT, observation_id
        assert row.value.point == value, observation_id
        assert row.identity.quantity.value.value == quantity, observation_id
        assert row.identity.species.phase.value.value == phase, observation_id
        temperature_condition = row.point_conditions["temperature_K"]
        assert temperature_condition.state.is_value, observation_id
        assert temperature_condition.state.value == Decimal(temperature), observation_id
        assert row.identity.temperature_K.is_value, observation_id
        assert row.identity.temperature_K.value == Decimal(temperature), observation_id
        assert row.identity.composition.is_value, observation_id
        composition = dict(row.identity.composition.value.components)
        assert composition["KO0.5"] == Decimal(x_ko05), observation_id
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value

    unavailable = {key for key, row in rows.items() if row.value.kind is ValueKind.UNAVAILABLE}
    assert len(unavailable) == 11
    unknown_quantity = {
        key for key, row in rows.items() if row.identity.quantity.is_unknown
    }
    assert unknown_quantity == {
        "eliezer_howald_1978_K1175_1p00455",
        "eliezer_howald_1978_K1175_0p99658",
        "eliezer_howald_1978_K1623_lt_0p99526",
        "eliezer_howald_1978_K1623_0p993",
        "eliezer_howald_1978_K_approx_0p98",
        "eliezer_howald_1978_table1_x0390_excess_enthalpy_at_1500k",
    }
    assert all(
        row.identity.species is not None and not row.identity.species.phase.is_unknown
        for row in rows.values()
    )

    for observation_id, row in rows.items():
        raw_conditions = source[observation_id]["point_conditions"]
        assert {"temperature_K", "fO2_log", "total_pressure_Pa", "composition"} <= set(raw_conditions)
        assert raw_conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert raw_conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert row.point_conditions["temperature_K"] is not None
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        if raw_conditions["composition"]["state"]["tag"] == "value":
            assert row.identity.composition.is_value, observation_id
        else:
            assert not row.identity.composition.is_value, observation_id

    enthalpy_ids = {
        key for key, observation in source.items()
        if observation["values"].get("quantity") == "partial_molar_enthalpy"
    }
    assert len(enthalpy_ids) == 5
    assert {
        observation_id: source[observation_id]["units"]
        for observation_id in enthalpy_ids
    } == {
        "eliezer_howald_1978_table1_x021537_hm827": "kcal mol-1",
        "eliezer_howald_1978_table1_x028548_hm632": "kcal mol-1",
        "eliezer_howald_1978_table1_x033497_hm594": "kcal mol-1",
        "eliezer_howald_1978_table1_x034025_hm630": "kcal mol-1",
        "eliezer_howald_1978_hbar2_x0297": "cal mol-1",
    }
    for observation_id in enthalpy_ids:
        values = source[observation_id]["values"]
        assert values["units_as_printed"] in {"kcal mol-1", "cal mol-1"}
        assert "partial_molar_enthalpy_kJ_mol-1" not in values
        assert rows[observation_id].value.kind is ValueKind.UNAVAILABLE
    hbar2 = source["eliezer_howald_1978_hbar2_x0297"]
    assert hbar2["uncertainty"]["basis"] == "cal mol-1"
    excess_id = "eliezer_howald_1978_table1_x0390_excess_enthalpy_at_1500k"
    assert rows[excess_id].identity.quantity.is_unknown
    assert rows[excess_id].value.kind is ValueKind.UNAVAILABLE


def test_activity_reference_and_method_evidence():
    doc, rows, migrator = _migrated()
    source = _source_observations(doc)

    ko_id = "eliezer_howald_1978_tridymite_ko05_activity_1743"
    ko_source = source[ko_id]
    ko_values = ko_source["values"]
    assert ko_values["activity_as_printed"] == "0.7157 × 10^-6"
    assert ko_source["standard_state_as_printed"] == (
        '"If the entropy of solution of KO0.5 is approximately ideal and R1 = '
        '-36 kcal mol-1, the solubility of KO0.5 in tridymite at 1743 K '
        '(activity of KO0.5(s) = 0.7157 × 10^-6) will be a mole fraction of '
        'about 0.0234."'
    )
    standard_state = ko_source["standard_state"]["state"]["value"]
    assert standard_state["convention"] == "raoultian_pure_endmember"
    assert standard_state["endmember"]["formula"] == "KO0.5"
    assert standard_state["endmember"]["phase"] == {"tag": "value", "value": "cr"}
    assert standard_state["endmember"]["polymorph"]["tag"] == "unknown"
    assert ko_source["standard_state"]["inferred"] is False
    assert ko_source["standard_state"]["locator"]["published_page"] == 1025
    assert "Table III" in ko_source["standard_state"]["note"]
    ko_row = rows[ko_id]
    assert ko_row.identity.reference_state.is_value
    assert (
        ko_row.identity.reference_state.value.convention
        is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    )
    assert ko_row.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert ko_row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert ko_row.derivation is not None
    assert ko_row.derived_from == (
        "eliezer-howald-1978-potassium-silicate-vapor-pressure::context::eliezer_howald_1978_table3_standard_state_context",
        "eliezer-howald-1978-potassium-silicate-vapor-pressure::context::eliezer_howald_1978_table4_redlich_kister_coefficients_context",
        "eliezer-howald-1978-potassium-silicate-vapor-pressure::context::eliezer_howald_1978_tridymite_solubility_context",
    )

    phase_diagram_ids = (
        "eliezer_howald_1978_phase_diagram_x0102_loggamma2",
        "eliezer_howald_1978_phase_diagram_x0297_loggamma2",
        "eliezer_howald_1978_phase_diagram_x0102_sio2_activity",
        "eliezer_howald_1978_phase_diagram_x0297_sio2_activity",
    )
    phase_diagram_quote = (
        '"Log γ2 is known at several temperatures and compositions from the '
        'phase diagram measurements."'
    )
    assert len(phase_diagram_ids) == 4
    for observation_id in phase_diagram_ids:
        observation = source[observation_id]
        values = observation["values"]
        assert values["method_as_printed"] == phase_diagram_quote
        assert values["method_class"] == "quoted_attributed"
        assert values["attribution"] == (
            "Phase-diagram measurements, reference 15, as cited by Eliezer et al."
        )
        assert "standard_state" not in observation
        assert rows[observation_id].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert source[phase_diagram_ids[2]]["values"]["activity"] == "0.94293"

    gibbs_duhem_id = "eliezer_howald_1978_gibbs_duhem_x0293_loggamma2"
    gibbs_source = source[gibbs_duhem_id]
    assert gibbs_source["values"]["method_class"] == "model_derived"
    assert "Gibbs-Duhem integration" in gibbs_source["values"]["derivation"]["relation"]
    assert rows[gibbs_duhem_id].evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert rows[gibbs_duhem_id].derived_from == (
        "eliezer-howald-1978-potassium-silicate-vapor-pressure::context::eliezer_howald_1978_table4_redlich_kister_coefficients_context",
    )
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert len(rows) == 17
    assert sum(row.admission.status is AdmissionStatus.ADMITTED for row in rows.values()) == 6
    assert sum(row.admission.status is AdmissionStatus.REJECTED for row in rows.values()) == 0
    assert sum(row.admission.status is AdmissionStatus.PENDING for row in rows.values()) == 11


def test_activity_evidence_mutation_fails(tmp_path, monkeypatch):
    assert_extract_source_mutation_fails(
        EXTRACT,
        "method_class: model_derived\n        derivation:\n          relation: >-\n            The authors use their Redlich-Kister",
        "method_class: author_derived\n        derivation:\n          relation: >-\n            The authors use their Redlich-Kister",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=test_activity_reference_and_method_evidence,
    )


def test_interior_activity_mutation_fails(tmp_path, monkeypatch):
    assert_extract_source_mutation_fails(
        EXTRACT,
        "activity: '0.94293'",
        "activity: '0.94294'",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=test_eliezer_howald_source_cells_and_render_pins,
    )
