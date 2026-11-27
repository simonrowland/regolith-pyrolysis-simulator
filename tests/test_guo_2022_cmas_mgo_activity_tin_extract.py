from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.helpers.corpus_paths import regolith_corpus_root
from simulator.battery import consumer_inputs
from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import AmountBasis, Phase, Quantity
from simulator.battery.generators.bench import melt_activity_requests
from simulator.battery.migrate import Migrator
from simulator.policy.enums import MethodToken
from tests.literature_extract_test_utils import activity_test_bench, isolated_root
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "guo-2022-cmas-mgo-activity-tin"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits" / f"{SOURCE_ID}.cells.csv"
PDF_SHA256 = "c17a271673e773127b2a9fa319249b40568370145171a65c77ea168b9381e3c8"
EXPECTED_ACTIVITIES = {
    "guo22_t3_row_1_1773k": "0.1726",
    "guo22_t3_row_1_1823k": "0.2405",
    "guo22_t3_row_1_1873k": "0.2917",
    "guo22_t3_row_2_1773k": "0.2186",
    "guo22_t3_row_2_1823k": "0.3174",
    "guo22_t3_row_2_1873k": "0.3383",
    "guo22_t3_row_3_1773k": "0.3067",
    "guo22_t3_row_3_1823k": "0.3725",
    "guo22_t3_row_3_1873k": "0.4301",
    "guo22_t3_row_4_1773k": "0.3949",
    "guo22_t3_row_4_1823k": "0.4316",
    "guo22_t3_row_4_1873k": "0.4928",
}
EXPECTED_COMPOSITIONS = {
    "1#": {"CaO": "47.02", "SiO2": "36.17", "MgO": "4.8", "Al2O3": "12"},
    "2#": {"CaO": "44.65", "SiO2": "34.34", "MgO": "6.0", "Al2O3": "15"},
    "3#": {"CaO": "42.27", "SiO2": "32.52", "MgO": "7.2", "Al2O3": "18"},
    "4#": {"CaO": "40.69", "SiO2": "31.30", "MgO": "8.0", "Al2O3": "20"},
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _isolated_migration(tmp_path: Path, document: dict):
    root = isolated_root(tmp_path, document)
    path = root / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
    migrator = Migrator(root=root, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _source_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["MgO"]["observations"]
    }


def _assert_activity_consumer(inputs) -> None:
    assert inputs.melt_activity
    assert inputs.activity_quantity == Quantity.ACTIVITY.value
    assert inputs.measured_species == "MgO"


def test_table2_and_table3_locator_lines_contain_printed_values() -> None:
    document = _document()
    transcription = (
        regolith_corpus_root()
        / "text"
        / SOURCE_ID
        / "pdftotext-layout.txt"
    ).read_text(encoding="utf-8")
    lines = transcription.splitlines()

    table2 = next(
        row
        for row in document["species"]["MgO"]["context"]
        if row["observation_id"] == "guo22_t2_reference_xmg_1773k"
    )
    table2_line = lines[int(table2["locator"]["line_range"]) - 1]
    assert "1773 K" in table2_line
    assert table2["values"]["x_ref_Mg_in_Sn_as_printed"] in table2_line

    table3 = next(
        row
        for row in document["species"]["MgO"]["observations"]
        if row["observation_id"] == "guo22_t3_row_1_1773k"
    )
    table3_line = lines[int(table3["locator"]["line_range"]) - 1]
    assert table3["locator"]["row"] in table3_line
    assert table3["values"]["activity_as_printed"] in table3_line


def test_table3_cells_standard_state_fidelity_and_validator_coverage(monkeypatch) -> None:
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(regolith_corpus_root()))
    document = _document()
    observations = document["species"]["MgO"]["observations"]
    assert document["extraction"]["audit_file"] == (
        f"data/literature/extracts/audits/{SOURCE_ID}.cells.csv"
    )
    with CELL_AUDIT.open(newline="", encoding="utf-8") as stream:
        cell_audit = list(csv.DictReader(stream))
    assert len(cell_audit) == 43
    cells = {
        (cell["printed_row"], cell["printed_column"]): cell["printed_value"]
        for cell in cell_audit
        if cell["printed_row"] != "column heading"
    }
    assert len(cells) == 40
    assert {
        cell["printed_value"]
        for cell in cell_audit
        if cell["printed_row"] == "column heading"
    } == {"1773", "1823", "1873"}

    assert document["source"]["sha256"] == PDF_SHA256
    assert document["source"]["corpus_sha256"] == PDF_SHA256
    assert len(observations) == 12
    assert len(document["fidelity_samples"]) == 24
    observation_by_id = {row["observation_id"]: row for row in observations}
    expected_sample_keys = {
        (row["observation_id"], field)
        for row in observations
        for field in ("activity", "x_Mg_in_Sn_as_printed")
    }
    assert {
        (sample["observation_id"], sample["field"])
        for sample in document["fidelity_samples"]
    } == expected_sample_keys
    for sample in document["fidelity_samples"]:
        observation = observation_by_id[sample["observation_id"]]
        printed_row = observation["locator"]["row"]
        temperature = observation["values"]["T_K"]
        printed_column = (
            f"a(MgO) at {temperature} K"
            if sample["field"] == "activity"
            else f"x[Mg] in Sn at {temperature} K"
        )
        locator = sample["locator"]
        assert isinstance(sample["value"], str)
        assert sample["value"] == cells[(printed_row, printed_column)]
        assert sample["observable"] == "activity_coefficient"
        assert sample["reviewed_by"] == "w1126attest"
        assert sample["reviewed_on"] == "2026-11-26"
        assert locator["row"] == printed_row
        assert locator["column"] == printed_column
        assert locator["line_range"] == {
            "1#": "218",
            "2#": "219",
            "3#": "220",
            "4#": "221",
        }[printed_row]
        assert locator["source_path"] == (
            "text/guo-2022-cmas-mgo-activity-tin/pdftotext-layout.txt"
        )
        assert locator["source_sha256"] == (
            "093d97a044671b84de3b0e32087e42fc5bfd8dd003f3e3e5400d743d74e56709"
        )
    assert {
        row: {
            oxide: cells[(row, f"{oxide} (wt%)")]
            for oxide in components
        }
        for row, components in EXPECTED_COMPOSITIONS.items()
    } == EXPECTED_COMPOSITIONS
    assert {
        row["observation_id"]: row["values"]["activity"] for row in observations
    } == EXPECTED_ACTIVITIES
    for observation in observations:
        values = observation["values"]
        printed_row = observation["locator"]["row"]
        temperature = values["T_K"]
        assert cells[(printed_row, f"x[Mg] in Sn at {temperature} K")] == values[
            "x_Mg_in_Sn_as_printed"
        ]
        assert cells[(printed_row, f"a(MgO) at {temperature} K")] == values[
            "activity_as_printed"
        ]
    for observation in observations:
        composition = dict(
            observation["point_conditions"]["composition"]["state"]["value"][
                "components"
            ]
        )
        printed_row = observation["locator"]["row"]
        assert {
            oxide: cells[(printed_row, f"{oxide} (wt%)")]
            for oxide in composition
        } == EXPECTED_COMPOSITIONS[printed_row]
    assert {row["values"]["T_K"] for row in observations} == {
        "1773",
        "1823",
        "1873",
    }
    assert {row["values"]["sample_id"] for row in observations} == {
        "guo22_table3_row_1",
        "guo22_table3_row_2",
        "guo22_table3_row_3",
        "guo22_table3_row_4",
    }
    assert all(row["values"]["method_class"] == "measured_reduced" for row in observations)
    assert all(
        row["standard_state"]["state"]["value"]["endmember"]["phase"]
        == {"tag": "value", "value": "cr"}
        and row["standard_state"]["state"]["value"]["endmember"]["polymorph"]
        == {
            "tag": "unknown",
            "reason": "The paper states a pure solid reference but does not name a polymorph.",
        }
        for row in observations
    )
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    fidelity_warnings = []
    errors = validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SOURCE_ID,
        check_fidelity_match=True,
        fidelity_warnings=fidelity_warnings,
        pre_policy_ids=set(),
        artifact_root=ROOT,
    )
    assert errors == []
    # Draft extracts may leave other numeric leaves uncovered; the activity and
    # xMg pins above must still be attested and backed by the opened source.
    assert {warning.kind: warning.count for warning in fidelity_warnings} == {
        "uncovered_observation": 12
    }


def test_isolated_migration_carries_each_temperature_and_composition_to_activity_requests(
    tmp_path: Path,
) -> None:
    document = _document()
    source_rows = _source_rows(document)
    result = _isolated_migration(tmp_path, document)
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]

    assert len(migrated) == 12
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    for observation in migrated:
        source = source_rows[observation.observation_id.rsplit("::", 1)[-1]]
        printed_t_k = Decimal(source["values"]["T_K"])
        printed_t_c = Decimal(source["values"]["T_C"])
        row_label = source["locator"]["row"]
        source_composition = source["point_conditions"]["composition"]["state"]["value"]
        assert source_composition["amount_basis"] == "mass_percent"
        if "composition_wt_pct" in source["values"]:
            assert dict(source_composition["components"]) == source["values"][
                "composition_wt_pct"
            ]

        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.identity.species is not None
        assert observation.identity.species.formula == "MgO"
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K.value == printed_t_k
        assert observation.identity.reference_state.is_value
        assert observation.point_conditions["T_C"].state.value == printed_t_c
        assert observation.point_conditions["temperature_K"].state.value == printed_t_k
        temperature_locator = source["point_conditions"]["T_C"]["locator"]
        for condition_name in ("T_C", "temperature_K"):
            locator = observation.point_conditions[condition_name].locator
            assert locator.page == temperature_locator["page"]
            assert str(locator.table) == temperature_locator["table"]
            assert locator.line_range == temperature_locator["line_range"]
        composition = observation.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        assert set(dict(composition.state.value.components)) == set(
            formula for formula, _ in source_composition["components"]
        )
        composition_locator = source["point_conditions"]["composition"]["locator"]
        assert composition.locator.page == composition_locator["page"]
        assert str(composition.locator.table) == composition_locator["table"]
        assert composition.locator.line_range == composition_locator["line_range"]
        assert row_label == source["point_conditions"]["composition"]["locator"]["row"]
        assert source["locator"]["column"]

        experiment = result.experiments[observation.experiment_id]
        assert experiment.method.value is MethodToken.QUENCH_EQUILIBRATION
        bench = activity_test_bench(experiment.work_id)
        inputs = collect_consumer_inputs(experiment, bench, observation)
        _assert_activity_consumer(inputs)
        requests = melt_activity_requests(
            inputs, experiment=experiment, bench=bench, observation=observation
        )
        assert len(requests) == 5
        assert {request.readiness.engine for request in requests} == {
            "alphamelts",
            "thermoengine",
            "openimcc",
            "internal-analytical",
            "magemin",
        }
        assert all(request.readiness.consumer == "melt_activity" for request in requests)


def test_mutating_the_activity_classifier_makes_every_new_row_fail_its_consumer_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    result = _isolated_migration(tmp_path, document)
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]

    for observation in migrated:
        experiment = result.experiments[observation.experiment_id]
        bench = activity_test_bench(experiment.work_id)
        with monkeypatch.context() as mutation:
            mutation.setattr(consumer_inputs, "is_melt_activity_observation", lambda _row: False)
            changed = collect_consumer_inputs(experiment, bench, observation)
            with pytest.raises(AssertionError):
                _assert_activity_consumer(changed)
