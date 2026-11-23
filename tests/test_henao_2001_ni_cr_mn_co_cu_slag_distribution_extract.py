"""Regression pins for Henao et al. (2001) typed tables and deferred figures."""

from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, Engine, NoticeKind
from simulator.battery.migrate import Migrator
from simulator.battery.score import _predict_with_engine


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/henao-2001-ni-cr-mn-co-cu-slag-distribution.yaml"
IRON_VALENCE_UNKNOWN_REASON = (
    "searched: Tables 1-3 and Methods; iron valence is not reported."
)
# Table 3's page render places these paired mass percentages under Fe and Ni.
TABLE3_ALLOY_FE_NI = {
    "C-1": ("8.8", "91.2"),
    "C-2": ("12.7", "87.2"),
    "C-3": ("20.4", "81.4"),
    "C-4": ("17.0", "79.1"),
    "C-5": ("15.9", "86.9"),
    "C-6": ("19.6", "77.5"),
    "C-7": ("25.2", "78.8"),
    "C-8": ("9.1", "86.5"),
    "C-9": ("12.4", "89.3"),
    "C-10": ("10.9", "80.4"),
    "C-11": ("11.5", "86.5"),
    "C-12": ("16.2", "79.5"),
    "C-13": ("18.9", "75.0"),
    "C-14": ("13.9", "88.7"),
    "C-15": ("18.2", "74.9"),
    "C-16": ("11.3", "89.9"),
    "C-17": ("29.4", "69.1"),
    "C-18": ("12.4", "90.3"),
    "C-19": ("16.9", "84.8"),
    "C-20": ("20.0", "75.4"),
    "C-21": ("19.0", "80.2"),
    "C-22": ("26.9", "73.0"),
    "C-23": ("21.0", "75.9"),
    "C-24": ("9.9", "85.8"),
    "C-25": ("18.8", "77.4"),
    "C-26": ("18.4", "75.6"),
    "C-27": ("23.7", "71.1"),
    "C-28": ("22.3", "72.6"),
}
TABLE3_ALLOY_TRACES = {
    "C-8": ("Cr", "0.0003", 21),
    "C-9": ("Cr", "0.0001", 21),
    "C-10": ("Cr", "0.0002", 21),
    "C-11": ("Cr", "0.0004", 21),
    "C-12": ("Cr", "0.0008", 21),
    "C-13": ("Cr", "0.0001", 21),
    "C-14": ("Cr", "0.0006", 21),
    "C-15": ("Cr", "0.0009", 21),
    "C-16": ("Cr", "0.0019", 21),
    "C-17": ("Cr", "0.0018", 21),
    "C-18": ("Co", "3.68", 22),
    "C-19": ("Co", "3.50", 22),
    "C-20": ("Co", "3.30", 22),
    "C-21": ("Co", "3.23", 22),
    "C-22": ("Co", "2.98", 22),
    "C-23": ("Co", "2.65", 22),
    "C-24": ("Co", "1.63", 23),
    "C-25": ("Co", "1.74", 23),
    "C-26": ("Co", "1.79", 23),
    "C-27": ("Co", "1.58", 23),
    "C-28": ("Co", "1.63", 23),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]


def _table_rows(document: dict, table: str) -> list[list[str]]:
    record = next(
        row
        for row in document["species"]["FeO"]["context"]
        if row["locator"].get("table") == table
    )
    return list(csv.reader(StringIO(record["values"]["printed_rows_csv"])))


def _assert_render_pins(document: dict) -> None:
    """Pin printed cells independently read from the PDF page renders."""
    observations = {row["observation_id"]: row for row in _observations(document)}
    assert len(observations) == 431
    assert observations["henao_2001_t1_A-1_slag_Fe"]["values"]["component_amount"] == "42.5"
    assert observations["henao_2001_t1_A-1_slag_Ni"]["values"]["component_amount"] == "4.50"
    assert observations["henao_2001_t1_A-1_slag_MgO"]["values"]["component_amount"] == "21.8"
    assert observations["henao_2001_t1_A-1_slag_SiO2"]["values"]["component_amount"] == "11.3"
    assert observations["henao_2001_t1_A-1_alloy_Fe"]["values"]["component_amount"] == "11.2"
    assert observations["henao_2001_t1_A-1_alloy_Ni"]["values"]["component_amount"] == "91.7"
    for sample, (iron, nickel) in TABLE3_ALLOY_FE_NI.items():
        assert (
            observations[f"henao_2001_t3_{sample}_alloy_Fe"]["values"]["component_amount"]
            == iron
        )
        assert (
            observations[f"henao_2001_t3_{sample}_alloy_Ni"]["values"]["component_amount"]
            == nickel
        )
    assert observations["henao_2001_t1_A-1_alloy_Fe"]["values"]["phase_label"] == "metal"
    first = observations["henao_2001_t1_A-1_slag_Fe"]["values"]
    assert first["log_fO2"]["value"] == "-6.8"
    assert first["log_fO2"]["units"] == "log10(atm)"
    assert "fO2_Pa" not in first
    assert len(_table_rows(document, "1")) == 18
    assert len(_table_rows(document, "2")) == 11
    assert len(_table_rows(document, "3")) == 29
    assert _table_rows(document, "1")[1][1:5] == ["A-1", "", "", ""]
    assert _table_rows(document, "1")[1][7:10] == ["", "", "2.2"]
    assert _table_rows(document, "1")[1][12:15] == ["0.78", "", ""]
    component_columns = {
        "1": (2, 3, 4, 5, 6, 7, 8, 13, 14, 15, 16),
        "2": (2, 3, 4, 5, 6, 7, 8, 9, 14, 15, 16, 17),
        "3": (2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 17, 18, 19, 20),
    }
    for table, columns in component_columns.items():
        assert all(
            row[column] == "" or row[column].replace(" ", "") == "<0.03"
            for row in _table_rows(document, table)[1:]
            for column in columns
        )
    bounds = [
        cell
        for table in ("1", "2", "3")
        for row in _table_rows(document, table)
        for cell in row
        if cell.replace(" ", "") == "<0.03"
    ]
    assert len(bounds) == 4
    table3_rows = {row[1]: row for row in _table_rows(document, "3")[1:]}
    for sample, (_component, amount, column) in TABLE3_ALLOY_TRACES.items():
        assert table3_rows[sample][column] == amount


def _assert_composition_bindings(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 431
    by_group: dict[tuple[str, str], dict[str, str]] = {}
    for observation in observations:
        values = observation["values"]
        assert values["composition_basis"] == "wt_percent"
        assert "composition_wt_pct" in values
        composition = values["composition_wt_pct"]
        component = values["component_as_published"]
        assert composition[component] == values["component_amount"]
        assert observation["locator"]["table"] in {"1", "2", "3"}
        key = (observation["experiment"], values["phase_label"])
        if key in by_group:
            assert composition == by_group[key]
        else:
            by_group[key] = composition

    assert len(by_group) == 110
    assert by_group[("henao:A-1", "slag")] == {
        "Fe": "42.5", "Ni": "4.50", "MgO": "21.8", "SiO2": "11.3"
    }
    assert by_group[("henao:A-1", "metal")] == {"Fe": "11.2", "Ni": "91.7"}
    assert by_group[("henao:B-5", "slag")]["Ni"] == "< 0.03"
    assert by_group[("henao:C-6", "slag")]["Ni"] == "< 0.03"
    assert by_group[("henao:C-10", "metal")] == {
        "Fe": "10.9", "Ni": "80.4", "Cr": "0.0002"
    }
    assert by_group[("henao:C-18", "metal")]["Co"] == "3.68"
    assert by_group[("henao:C-28", "metal")]["Co"] == "1.63"
    # The assay map preserves the printed `< 0.03` detection bound. Use that
    # independently of the metadata being guarded so metadata cannot define
    # its own expected rows.
    bounded_count = 0
    for observation in observations:
        values = observation["values"]
        if values["composition_wt_pct"].get("Ni") == "< 0.03":
            bounded_count += 1
            assert values.get("composition_missing_components") == [
                {
                    "component": "Ni",
                    "reason": "below_detection_limit",
                    "value_as_published": "<0.03",
                }
            ]
            assert values.get("note") == (
                "Ni was printed as <0.03 wt% and excluded. "
                "Tables 2-3, folio 1961."
            )
        else:
            assert "composition_missing_components" not in values
            assert "note" not in values
    assert bounded_count == 18
    assert all(
        "composition_status" not in observation["values"]
        for observation in observations
        if observation["values"].get("phase_label") == "metal"
    )


def _assert_slag_compositions_remain_unknown(document: dict) -> None:
    groups: dict[tuple[str, str], list[dict]] = {}
    for observation in _observations(document):
        values = observation["values"]
        if values.get("phase_label") == "slag":
            groups.setdefault((observation["experiment"], "slag"), []).append(
                observation
            )

    assert len(groups) == 55
    assert sum(len(rows) for rows in groups.values()) == 298
    for (experiment, _phase), rows in groups.items():
        values = rows[0]["values"]
        printed = values["composition_wt_pct"]
        for row in rows:
            assert row["values"]["composition_wt_pct"] == printed
            assert row["values"]["composition_status"] == "calculated"
            assert row["point_conditions"]["composition"] == rows[0][
                "point_conditions"
            ]["composition"]
        composition = rows[0]["point_conditions"]["composition"]
        assert composition["state"] == {
            "tag": "unknown",
            "reason": IRON_VALENCE_UNKNOWN_REASON,
        }
        assert "inference" not in composition
        for row in rows:
            locator = composition["locator"]
            assert locator["page"] == row["locator"]["page"]
            assert locator["table"] == row["locator"]["table"]
            assert locator["row"] == row["locator"]["row"]


def test_printed_phase_assays_are_bound_and_iron_valence_stays_unknown() -> None:
    document = _document()
    _assert_composition_bindings(document)
    _assert_slag_compositions_remain_unknown(document)

    # Keep the count at 18 while moving one bounded row's metadata and note
    # onto an assay with a printed Ni value. The printed assay set must reject it.
    mutated = deepcopy(document)
    observations = {
        row["observation_id"]: row for row in _observations(mutated)
    }
    donor = observations["henao_2001_t2_B-5_slag_Cr"]["values"]
    recipient = observations["henao_2001_t1_A-1_slag_Ni"]["values"]
    for key in ("composition_missing_components", "note"):
        recipient[key] = donor.pop(key)
    with pytest.raises(AssertionError):
        _assert_composition_bindings(mutated)


def test_henao_tables_migrate_as_typed_compositions_with_conditions() -> None:
    document = _document()
    _assert_render_pins(document)

    contexts = [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
    ]
    figures = [row for row in contexts if row["locator"].get("figure")]
    assert len(figures) == 10
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("numeric_points" not in row["values"] for row in figures)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = [row for row in result.observations.values() if row.source_id == EXTRACT.stem]
    assert len(observations) == 431
    assert all(row.admission.status.value == "admitted" for row in observations)
    assert not [entry for entry in result.queue if entry.source.endswith(EXTRACT.name)]
    assert all(row.identity.temperature_K.value == 1773 for row in observations)
    henao_experiments = [
        row
        for row in result.experiments.values()
        if row.experiment_id.startswith(
            "10.2320/matertrans.42.1959::experiment::henao:"
        )
    ]
    assert len(henao_experiments) == 55
    assert all(
        row.pressure_environment.total_pressure_Pa.state.is_value
        and row.pressure_environment.total_pressure_Pa.state.value == Decimal("101300")
        and row.pressure_environment.total_pressure_Pa.locator is not None
        and row.pressure_environment.total_pressure_Pa.locator.pdf_page_index == 0
        and row.pressure_environment.total_pressure_Pa.locator.published_page == "1959"
        for row in henao_experiments
    )
    assert all(row.point_conditions and "fO2_log" in row.point_conditions for row in observations)
    assert all(row.identity.fO2_Pa.tag.value == "value" for row in observations)
    # The unprinted slag iron valence leaves slag compositions unknown; alloy
    # assays retain their elemental mass-percent basis.
    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in observations}
    assert all(row.identity.composition is not None for row in observations)
    a1_slag = by_id["henao_2001_t1_A-1_slag_Ni"]
    assert a1_slag.identity.composition.is_unknown
    assert a1_slag.identity.composition.reason == IRON_VALENCE_UNKNOWN_REASON
    assert a1_slag.point_conditions["composition"].inference is None
    a1_prediction = _predict_with_engine(
        Engine.THERMOENGINE,
        a1_slag,
        experiment=result.experiments[a1_slag.experiment_id],
    )
    assert a1_prediction.execution.state.value == "not_probed"
    assert a1_prediction.refusal_detail == {
        "reason": "run_composition_unprinted_analysed_phase_echo_excluded",
        "input": "run_composition",
        "default_policy_id": "DP-017",
        "rule": "analysed liquid composition predicts other phases only",
    }
    a1_metal = by_id["henao_2001_t1_A-1_alloy_Ni"]
    assert a1_metal.identity.composition.is_value
    composition = a1_metal.identity.composition.value
    assert composition.amount_basis is AmountBasis.MASS_PERCENT
    assert sum(dict(composition.components).values()).quantize(Decimal("0.000001")) == 100
    assert composition.input_provenance is None
    renormalization = next(
        notice
        for notice in a1_metal.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "printed phase mass-percent total=102.9 wt%" in notice.reason
    )
    assert renormalization.original == Decimal("102.9")
    assert len([row for row in observations if row.identity.composition.is_value]) == 108
    assert len([row for row in observations if row.identity.composition.is_unknown]) == 323
    unresolved_slag = [
        row for row in observations if row.point_conditions and
        "_slag_" in row.observation_id
    ]
    assert len(unresolved_slag) == 298
    assert all(
        row.point_conditions["composition"].state.is_unknown
        and row.point_conditions["composition"].state.reason == IRON_VALENCE_UNKNOWN_REASON
        and row.point_conditions["composition"].inference is None
        for row in unresolved_slag
    )
    slag_predictions = [
        _predict_with_engine(
            Engine.THERMOENGINE,
            row,
            experiment=result.experiments[row.experiment_id],
        )
        for row in unresolved_slag
    ]
    assert len(slag_predictions) == 298
    assert all(
        prediction.refusal_detail
        == {
            "reason": "run_composition_unprinted_analysed_phase_echo_excluded",
            "input": "run_composition",
            "default_policy_id": "DP-017",
            "rule": "analysed liquid composition predicts other phases only",
        }
        for prediction in slag_predictions
    )
    assert all(
        row.identity.composition.value.input_provenance is None
        for row in observations
        if row.identity.composition.is_value
    )
    assert all(row.locator and row.locator.page and row.locator.published_page for row in observations)
    migrated_contexts = [
        context
        for rows in result.context_by_work.values()
        for context in rows
        if context["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == 14
    assert not result.validation.hard_issues


def test_typed_cell_mutation_is_detected_by_independent_pdf_pins() -> None:
    for observation_id, amount in (
        ("henao_2001_t1_A-1_slag_Fe", "42.6"),
        ("henao_2001_t1_A-1_slag_MgO", "21.9"),
        ("henao_2001_t3_C-1_alloy_Fe", "8.9"),
    ):
        mutated = deepcopy(_document())
        row = next(
            observation
            for observation in _observations(mutated)
            if observation["observation_id"] == observation_id
        )
        row["values"]["component_amount"] = amount
        with pytest.raises(AssertionError):
            _assert_render_pins(mutated)

    mutated = deepcopy(_document())
    rows = _table_rows(mutated, "1")
    rows[1][9] = "2.3"
    table = next(
        row
        for row in mutated["species"]["FeO"]["context"]
        if row["locator"].get("table") == "1"
    )
    table["values"]["printed_rows_csv"] = "\n".join(",".join(value for value in row) for row in rows)
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    mutated = deepcopy(_document())
    bound = next(
        observation
        for observation in _observations(mutated)
        if observation["observation_id"] == "henao_2001_t1_A-1_slag_Fe"
    )
    bound["values"].pop("composition_wt_pct")
    with pytest.raises(AssertionError):
        _assert_composition_bindings(mutated)

    mutated = deepcopy(_document())
    bound = next(
        observation
        for observation in _observations(mutated)
        if observation["observation_id"] == "henao_2001_t1_A-1_slag_Fe"
    )
    bound["values"]["composition_wt_pct"]["Fe"] = "42.6"
    with pytest.raises(AssertionError):
        _assert_composition_bindings(mutated)

    mutated = deepcopy(_document())
    bound = next(
        observation
        for observation in _observations(mutated)
        if observation["observation_id"] == "henao_2001_t1_A-1_slag_Fe"
    )
    bound["point_conditions"]["composition"]["state"]["tag"] = "value"
    with pytest.raises(AssertionError):
        _assert_slag_compositions_remain_unknown(mutated)
