from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from math import isclose, log, log10
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from simulator.battery import score
from simulator.battery.enums import Engine, NoticeKind, Phase, Quantity
from simulator.battery.fo2_ladder import has_printed_fo2
from simulator.battery.migrate import (
    Migrator,
    _printed_point_phase_kind,
    _printed_point_phase_text,
)
from simulator.battery.records import ApproxPressure, BandBasis, Located, State
from simulator.physical_constants import PA_PER_BAR
import tools.validate_literature_extracts as vle
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kennedy-1948-ferric-ferrous-silicate-melts.yaml"


def _assert_kennedy_regime_notes_match(source):
    rows = source["species"]["Fe"]["observations"][0]["values"]["rows"]
    regime_notes = [
        row["point_conditions"]["regime_as_printed"]["locator"]["note"]
        for row in rows[2:9]
    ]
    assert len(regime_notes) == 7
    assert all(note.startswith("Is it apparent from the values in Table II") for note in regime_notes)


def _migrate_extract(path: Path = EXTRACT):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _row_at(observations, temperature: str, duration: str):
    matches = [
        observation
        for observation in observations
        if observation.point_conditions["T_C"].state.value == Decimal(temperature)
        and observation.point_conditions["duration_h"].state.value == Decimal(duration)
    ]
    assert len(matches) == 1
    return matches[0]


def test_kennedy_table2_runs_migrate_with_printed_conditions_and_composition():
    source_text = EXTRACT.read_text(encoding="utf-8")
    source = yaml.safe_load(source_text)
    assert yaml_mapping_source_issues(source_text) == []
    _assert_kennedy_regime_notes_match(source)

    result = _migrate_extract()
    assert len(result.observations) == 22
    assert result.registry_issues == []
    assert len(result.queue) == 1
    assert result.queue[0].axes == ["phase"]
    assert result.queue[0].why == "missing phase"
    observations = list(result.observations.values())
    assert all(o.identity.quantity.value is Quantity.FE3_FE2_RATIO for o in observations)
    assert all(o.value.kind.value == "point" for o in observations)
    assert all(o.identity.temperature_K.is_value for o in observations)
    assert all(o.identity.composition.is_value for o in observations)
    assert all(o.admission.status.value == "pending" for o in observations)
    assert all(
        o.point_conditions["T_C"].state.is_value
        and o.point_conditions["T_C"].locator.pdf_page_index == 5
        and o.point_conditions["T_C"].locator.table == "II"
        for o in observations
    )
    without_printed_oxygen = 0
    for observation in observations:
        if observation.point_conditions["T_C"].state.value == Decimal("1100"):
            without_printed_oxygen += 1
            assert "fO2_control" not in observation.point_conditions
            assert "fO2_Pa" not in observation.point_conditions
            assert observation.identity.fO2_Pa.is_unknown
            assert not has_printed_fo2(observation, observation.identity)
            continue
        control = observation.point_conditions["fO2_control"]
        assert control.state.value == "1/5 atmosphere of oxygen"
        assert control.locator.pdf_page_index == 3
        assert observation.identity.fO2_Pa.value == Decimal("20265")
        pressure = observation.point_conditions["fO2_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value.point == Decimal("20265")
        assert pressure.inference is not None
        assert pressure.inference.relation == "atm_to_Pa"
        assert "P_Pa = P_atm × 101325" in pressure.inference.inputs
        assert "as_published=1/5 atmosphere of oxygen" in pressure.inference.inputs
        assert "printed=point_conditions.fO2_control" in pressure.inference.inputs
        assert "convention=ideal-gas:fO2=pO2" in pressure.inference.inputs
        factor = dict(pressure.inference.parameters)["factor"]
        assert factor.state.value == Decimal("101325")
        assert factor.locator.published_page == 532
        assert pressure.locator.pdf_page_index == 3
        assert pressure.locator.published_page == 532
        assert has_printed_fo2(observation, observation.identity)
        assert score._printed_fe_redox_fo2_log(
            observation, observation.identity
        ) == Decimal("-0.6932533919222882")
    assert without_printed_oxygen == 7
    assert all(
        o.point_conditions["duration_h"].state.is_value
        and o.point_conditions["duration_h"].locator.table == "II"
        for o in observations
    )
    assert len(source["species"]["Fe"]["observations"][0]["values"]["rows"]) == 22
    assert "regime" not in source["species"]["Fe"]["observations"][0]

    transient = _row_at(observations, "1300", "2")
    equilibrium = _row_at(observations, "1300", "16")
    crystal_bearing = _row_at(observations, "1200", "16")
    non_equilibrium = _row_at(observations, "1100", "240")
    assert transient.identity.species.phase.value is Phase.L
    assert "transient" in transient.point_conditions["regime_as_printed"].state.value
    assert (
        transient.point_conditions["regime_as_printed"].locator.published_page
        == 534
    )
    assert equilibrium.identity.species.phase.value is Phase.L
    assert equilibrium.point_conditions["regime_as_printed"].state.value == (
        "equilibrium was reached in essentially 16 hours"
    )
    assert (
        equilibrium.point_conditions["regime_as_printed"].locator.published_page
        == 534
    )
    assert crystal_bearing.identity.species.phase.is_unknown
    assert (
        "bulk_composition_in_two_phase_region"
        in crystal_bearing.identity.species.phase.reason
    )
    assert (
        "magnetite"
        in crystal_bearing.point_conditions["phase_as_printed"].state.value
    )
    assert (
        crystal_bearing.point_conditions["phase_as_printed"].locator.published_page
        == 541
    )
    assert "transient" in crystal_bearing.point_conditions["regime_as_printed"].state.value
    assert non_equilibrium.identity.species.phase.is_unknown
    assert non_equilibrium.point_conditions["regime_as_printed"].state.value == (
        "No equilibrium was reached at 1100°C even in runs of 240 hours."
    )
    assert (
        non_equilibrium.point_conditions["phase_as_printed"].locator.published_page
        == 534
    )
    assert all(
        o.admission.status.value == "pending"
        for o in (transient, crystal_bearing, non_equilibrium)
    )

    experiment = next(iter(result.experiments.values()))
    ambient = experiment.ambient_pressure_evidence
    assert ambient is not None
    assert ambient.locator.published_page == 539
    assert ambient.locator.pdf_page_index == 10
    assert "surface exposed to the air" in ambient.quote
    assert all(
        o.identity.total_pressure_Pa.value == Decimal("101325")
        for o in observations
    )
    validation_errors = validate_extract_file(EXTRACT, check_fidelity_match=True)
    samples = source["fidelity_samples"]
    pending_indices = {
        index
        for index, sample in enumerate(samples)
        if sample.get("locator", {}).get("source_sha256") == "PENDING_CORPUS"
    }
    assert not pending_indices
    assert validation_errors == []


def test_kennedy_regime_note_lock_detects_restored_transcription():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = source["species"]["Fe"]["observations"][0]["values"]["rows"]
    for row in rows[2:9]:
        note = row["point_conditions"]["regime_as_printed"]["locator"]["note"]
        row["point_conditions"]["regime_as_printed"]["locator"]["note"] = note.replace(
            "Is it apparent", "It is apparent", 1
        )
    with pytest.raises(AssertionError):
        _assert_kennedy_regime_notes_match(source)


def test_kennedy_regime_and_crystal_qualifications_reach_scoring(tmp_path: Path):
    result = _migrate_extract()
    context = score.ScoreContext(result.works, result.experiments, result.observations)
    observations = list(result.observations.values())
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    del source["species"]["Fe"]["observations"][0]["values"]["rows"][2][
        "point_conditions"
    ]["regime_as_printed"]
    mutated_path = tmp_path / EXTRACT.name
    mutated_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    mutated_result = _migrate_extract(mutated_path)
    mutated = _row_at(mutated_result.observations.values(), "1300", "2")
    transient = _row_at(observations, "1300", "2")
    assert "non_equilibrium_run" in score.flagged_strata(transient.notices)
    assert "non_equilibrium_run" not in score.flagged_strata(mutated.notices)
    cases = [
        (transient, {"non_equilibrium_run"}),
        (_row_at(observations, "1300", "16"), set()),
        (
            _row_at(observations, "1200", "16"),
            {"non_equilibrium_run", "crystal_bearing_bulk_composition"},
        ),
        (mutated, set()),
    ]
    missing_oxygen = [
        row for row in observations
        if row.point_conditions["T_C"].state.value == Decimal("1100")
    ]
    assert len(missing_oxygen) == 7
    last = _row_at(missing_oxygen, "1100", "240")
    pressure_band = ApproxPressure(
        Decimal("101325"), Decimal("100000"), Decimal("103000"), BandBasis.PRINTED
    )
    missing_oxygen.extend(
        (
            replace(
                last,
                identity=replace(last.identity, total_pressure_Pa=State.of(pressure_band)),
            ),
            replace(
                last,
                point_conditions={
                    **last.point_conditions,
                    "fO2_control": Located(
                        State.unknown("source does not print a numeric oxygen input"),
                        locator=last.point_conditions["T_C"].locator,
                    ),
                },
            ),
        )
    )
    imcc = SimpleNamespace(
        ferric_fe_mol=2.0,
        ferrous_fe_mol=1.0,
        extrapolated_redox_rows=(),
        active_row_band_intersection_K=(1200, 1800),
        flags=(),
        redox_pack_digest="probe",
    )
    with (
        patch("simulator.fe_redox.kress91_split", return_value={"ratio": 1.0}) as kress,
        patch(
            "simulator.melt_backend.openimcc_bridge.evaluate_redox", return_value=imcc
        ) as openimcc,
    ):
        for engine, backend in (
            (Engine.INTERNAL_ANALYTICAL, kress),
            (Engine.OPENIMCC, openimcc),
        ):
            for row, qualifiers in cases:
                backend.reset_mock()
                residual, _ = score.compile_residual(row, engine, context=context)
                backend.assert_called_once()
                assert residual.numeric is not None, residual.refusal
                assert set(score.flagged_strata(residual.notices)) & {
                    "non_equilibrium_run",
                    "crystal_bearing_bulk_composition",
                } == qualifiers
                oxygen_bar = 0.20265
                command = backend.call_args.kwargs
                if engine is Engine.INTERNAL_ANALYTICAL:
                    assert isclose(command["fO2_log"], log10(oxygen_bar))
                else:
                    assert isclose(command["lambda_imposed"], log(oxygen_bar))
                payload = score.residual_to_plain(residual)
                headline = score.headline_payloads([payload], [engine])
                assert [
                    item["n_scored"] for item in headline if item["rail"] == "redox"
                ] == [0]
                flagged = score.flagged_stratum_payloads([payload], [engine])
                assert {item["stratum"] for item in flagged} >= qualifiers | {
                    "approximate-pressure",
                }
            equilibrium = cases[1][0]
            # A comparison control with the same numeric pressure supplied explicitly.
            explicit_pressure = replace(
                equilibrium,
                notices=tuple(
                    n for n in equilibrium.notices
                    if n.kind is not NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
                ),
            )
            residual, _ = score.compile_residual(explicit_pressure, engine, context=context)
            payload = score.residual_to_plain(residual)
            headline = score.headline_payloads([payload], [engine])
            assert [
                item["n_scored"] for item in headline if item["rail"] == "redox"
            ] == [1]

            # The supplied identity takes precedence over the conflicting point
            # pressure on green. The backend command and candidate must agree.
            conflict = replace(
                equilibrium,
                identity=replace(
                    equilibrium.identity, fO2_Pa=State.of(Decimal("10000"))
                ),
            )
            backend.reset_mock()
            residual, candidate = score.compile_residual(conflict, engine, context=context)
            backend.assert_called_once()
            command = backend.call_args.kwargs
            if engine is Engine.INTERNAL_ANALYTICAL:
                assert command["fO2_log"] == -1.0
            else:
                assert isclose(command["lambda_imposed"], log(0.1))
            assert candidate is not None
            assert candidate.identity.fO2_Pa.value == Decimal("10000")
            assert residual.numeric is not None, residual.refusal

            # Missing oxygen uses the shared resolver's flagged default.
            backend.reset_mock()
            for row in missing_oxygen:
                residual, candidate = score.compile_residual(row, engine, context=context)
                backend.assert_called_once()
                assert residual.numeric is not None, residual.refusal
                oxygen_notices = [
                    notice for notice in residual.notices
                    if notice.origin == "fo2_ladder"
                ]
                assert oxygen_notices
                assert isinstance(oxygen_notices[0].kind, NoticeKind)
                assert "selected_fO2_Pa=" in oxygen_notices[0].reason
                assert "source does not print fO2 for this run" in oxygen_notices[0].reason
                assert row.identity.fO2_Pa.is_unknown
                assert {
                    "non_equilibrium_run",
                    "crystal_bearing_bulk_composition",
                } <= set(score.flagged_strata(residual.notices))
                assert candidate is not None
                if engine is Engine.INTERNAL_ANALYTICAL:
                    assert isclose(
                        backend.call_args.kwargs["fO2_log"],
                        log10(float(candidate.identity.fO2_Pa.value) / PA_PER_BAR),
                    )
                else:
                    assert isclose(
                        backend.call_args.kwargs["lambda_imposed"],
                        log(float(candidate.identity.fO2_Pa.value) / PA_PER_BAR),
                    )
                payload = score.residual_to_plain(residual)
                headline = score.headline_payloads([payload], [engine])
                assert [
                    item["n_scored"] for item in headline if item["rail"] == "redox"
                ] == [0]
                backend.reset_mock()

            # 1300 °C / 16 h is measured and in band once the ambient-pressure
            # caveat is removed. Missing oxygen must stay out of that headline.
            equilibrium_row = _row_at(observations, "1300", "16")
            assert "non_equilibrium_run" not in score.flagged_strata(
                equilibrium_row.notices
            )
            assert "crystal_bearing_bulk_composition" not in score.flagged_strata(
                equilibrium_row.notices
            )
            clean = replace(
                equilibrium_row,
                point_conditions={
                    key: value
                    for key, value in equilibrium_row.point_conditions.items()
                    if key not in {"fO2_control", "fO2_Pa", "fO2_log"}
                },
                identity=replace(
                    equilibrium_row.identity,
                    fO2_Pa=State.unknown(
                        "source does not print fO2 for this run"
                    ),
                ),
                notices=tuple(
                    notice
                    for notice in equilibrium_row.notices
                    if notice.kind
                    is not NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
                ),
            )
            backend.reset_mock()
            residual, candidate = score.compile_residual(
                clean, engine, context=context
            )
            backend.assert_called_once()
            assert residual.numeric is not None, residual.refusal
            oxygen_notices = [
                notice
                for notice in residual.notices
                if notice.origin == "fo2_ladder"
            ]
            assert len(oxygen_notices) == 1
            assert oxygen_notices[0].kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            assert Quantity.FE3_FE2_RATIO in oxygen_notices[0].affected_quantities
            assert "selected_fO2_Pa=" in oxygen_notices[0].reason
            assert (
                "source does not print fO2 for this run" in oxygen_notices[0].reason
            )
            assert "out_of_certified_band" in score.flagged_strata(residual.notices)
            payload = score.residual_to_plain(residual)
            headline = score.headline_payloads([payload], [engine])
            assert [
                item["n_scored"] for item in headline if item["rail"] == "redox"
            ] == [0]
            selected = score._selected_shared_ladder_oxygen(candidate)
            assert selected is not None
            _pressure, tier, _rung, route = selected
            assert tier == 4
            assert route == "intrinsic_melt_fO2"
            assert "kress_inverse" not in route
            assert "selected_fO2_Pa" not in route
            assert "original_reason" not in route
            assert "borrowed" not in route


def test_kennedy_fractional_oxygen_requires_located_printed_evidence(tmp_path: Path):
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    control = source["species"]["Fe"]["observations"][0]["values"]["rows"][0][
        "point_conditions"
    ]["fO2_control"]
    original = dict(control)
    path = tmp_path / EXTRACT.name
    for locator, inferred, printed in (
        (original["locator"], False, True),
        (None, False, False),
        (original["locator"], True, False),
        (None, True, False),
        ({"note": "oxygen statement"}, False, False),
        ({"published_page": 532}, False, False),
        ({"pdf_page_index": 3}, False, False),
        ({"published_page": 532, "pdf_page_index": True}, False, False),
        (
            {"published_page": 532, "pdf_page_index": 3},
            False,
            False,
        ),
        (
            {
                "published_page": "oxygen statement",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "page": 4,
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "published_page": "civil",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "published_page": "0",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "published_page": "SI-7",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            True,
        ),
        (
            {
                "published_page": "SI-10–SI-11",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            True,
        ),
        (
            {
                "published_page": "E02S13",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            True,
        ),
        (
            {
                "published_page": "4.pdf",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
        (
            {
                "published_page": "4 of 21",
                "pdf_page_index": 3,
                "section": "Experimental Method and Results",
            },
            False,
            False,
        ),
    ):
        control.clear()
        control.update(original)
        if locator is None:
            del control["locator"]
        else:
            control["locator"] = locator
        if inferred:
            control["inferred"] = True
        path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
        row = _row_at(_migrate_extract(path).observations.values(), "1430", "23")
        expected_published_page = locator.get("published_page") if locator else None
        assert has_printed_fo2(row, row.identity) == printed
        if printed:
            pressure = row.point_conditions["fO2_Pa"]
            assert pressure.locator.published_page == expected_published_page
            assert pressure.inference.relation == "atm_to_Pa"
            assert row.identity.fO2_Pa.value == Decimal("20265")
        else:
            assert "fO2_Pa" not in row.point_conditions
            assert row.identity.fO2_Pa.is_unknown


def test_kennedy_point_phase_requires_a_located_affirmative_assemblage():
    for text, expected in (
        ("melt without magnetite", None),
        ("magnetite forms only after quenching the melt", None),
        ("melt containing olivine and pyroxene", "two_phase"),
    ):
        statement = {"state": {"tag": "value", "value": text}}
        item = {"point_conditions": {"phase_as_printed": statement}}
        assert _printed_point_phase_text(item, None) is None
        assert _printed_point_phase_text({"phase_as_printed": text}, None) is None
        assert _printed_point_phase_kind(text) is None
        statement["locator"] = {"published_page": 541, "pdf_page_index": 12}
        located = _printed_point_phase_text(item, None)
        assert located == text
        assert _printed_point_phase_kind(located, located_statement=True) == expected


def test_kennedy_fidelity_pin_detects_a_changed_table2_cell(tmp_path: Path):
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(source)
    row = mutated["species"]["Fe"]["observations"][0]["values"]["rows"][0]
    row["FeO_wt_pct"] = "3.33"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")

    errors = validate_extract_file(path, check_fidelity_match=True)
    assert any("fidelity sample value mismatch" in error for error in errors)


def test_kennedy_scored_leaf_classification_keeps_facts_and_drops_metadata():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = source["species"]["Fe"]["observations"][0]
    measured, non_finite = vle._scored_evidence_leaves(observation)

    assert non_finite == []
    assert ("values", "method") not in measured
    assert ("values", "quantity") not in measured
    assert not any("cell_locators" in leaf for leaf in measured)
    assert not any("composition_column_locators" in leaf for leaf in measured)
    assert any(
        leaf[-3:] == ("fO2_control", "state", "value") for leaf in measured
    )
    assert not any(
        leaf[-3:] == ("regime_as_printed", "state", "value") for leaf in measured
    )
    assert any(
        leaf[-3:] == ("regime_as_printed", "locator", "note")
        for leaf in measured
    )
    assert any(leaf[-1] == "note" for leaf in measured)


def test_kennedy_table2_fidelity_pin_covers_the_observation():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table2_pins = [
        sample
        for sample in source["fidelity_samples"]
        if sample.get("path", "").startswith(
            "species.Fe.observations[kennedy_1948_table2_run_series].values.rows["
        )
        and sample.get("path", "").split("].values.rows[", 1)[-1].split("].", 1)[-1]
        in {"T_C", "duration_h", "FeO_wt_pct", "Fe2O3_wt_pct"}
        and sample.get("locator", {}).get("record", "").startswith("Table II row ")
        and sample.get("locator", {}).get("source_path")
        == "tables/kennedy-1948-ferric-ferrous-silicate-melts/table-text.txt"
    ]
    assert len(table2_pins) == 88
    assert {
        sample["locator"]["record"] for sample in table2_pins
    } == {f"Table II row {row}" for row in range(1, 23)}
    assert all(
        str(sample["locator"]["line_range"])
        == str(int(sample["locator"]["record"].rsplit(" ", 1)[-1]) + 16)
        for sample in table2_pins
    )
    assert all(
        sample["locator"]["source_path"]
        == "tables/kennedy-1948-ferric-ferrous-silicate-melts/table-text.txt"
        for sample in table2_pins
    )
    expected_table_digest = (
        "17fc27c8d52d571985978d880c4a60574b51d42042d7be99aba23830d8b42148"
    )
    assert all(
        sample["locator"]["source_sha256"] == expected_table_digest
        for sample in table2_pins
    )
    assert all("table" not in sample["locator"] for sample in table2_pins)
    assert all(
        sample.get("reviewed_by") and sample.get("reviewed_on")
        for sample in table2_pins
    )
    repeated_condition_pins = [
        sample
        for sample in source["fidelity_samples"]
        if sample.get("path", "").startswith(
            "species.Fe.observations[kennedy_1948_table2_run_series].values.rows["
        )
        and sample.get("path", "").split("].values.rows[", 1)[-1].split("].", 1)[-1]
        in {
            "point_conditions.T_C.state.value",
            "point_conditions.duration_h.state.value",
        }
        and sample.get("locator", {}).get("record", "").startswith("Table II row ")
    ]
    assert len(repeated_condition_pins) == 44
    assert all("table" not in sample["locator"] for sample in repeated_condition_pins)
    table1_composition_pins = [
        sample
        for sample in source["fidelity_samples"]
        if sample.get("path", "").startswith(
            "species.Fe.observations[kennedy_1948_table2_run_series].values.rows["
        )
        and sample.get("path", "").endswith(".oxide_wt_percent")
        and sample.get("locator", {}).get("table") == "I"
    ]
    assert len(table1_composition_pins) == 22
    assert all(
        sample["locator"]["line_range"] == "3-12"
        for sample in table1_composition_pins
    )
    row_1_composition = next(
        sample
        for sample in table1_composition_pins
        if sample["path"].endswith("values.rows[0].oxide_wt_percent")
    )
    assert row_1_composition["value"]["SiO2"] == "50.87"
    assert row_1_composition["locator"]["source_path"] == (
        "tables/kennedy-1948-ferric-ferrous-silicate-melts/table-text.txt"
    )
    assert row_1_composition["locator"]["source_sha256"] == expected_table_digest
    table_text_samples = [
        sample
        for sample in source["fidelity_samples"]
        if sample.get("locator", {}).get("source_path")
        == "tables/kennedy-1948-ferric-ferrous-silicate-melts/table-text.txt"
    ]
    assert len(table_text_samples) == 170
    assert all(
        sample["locator"]["source_sha256"] == expected_table_digest
        for sample in table_text_samples
    )
    assert all(
        sample["locator"]["line_range"] != "1-38"
        for sample in table_text_samples
    )
    oxygen_pins = [
        sample
        for sample in table_text_samples
        if sample.get("path", "").endswith(
            ".point_conditions.fO2_control.state.value"
        )
    ]
    assert len(oxygen_pins) == 15
    assert all(sample["locator"]["line_range"] == "40" for sample in oxygen_pins)
    prose_pins = [
        sample
        for sample in source["fidelity_samples"]
        if sample.get("locator", {}).get("source_path")
        == (
            "text/kennedy-1948-ferric-ferrous-silicate-melts/mineru/"
            "p001-p021/out/regolith-kennedy-1948-ferric-ferrous-silicate-melts-"
            "p001-p021/vlm/regolith-kennedy-1948-ferric-ferrous-silicate-melts-"
            "p001-p021.md"
        )
    ]
    assert len(prose_pins) == 41
    expected_prose_digest = (
        "6fe24b01ed68e4e84e602c9b772c621abfc96774862134e493a7d587754b0518"
    )
    assert all(
        sample["locator"]["source_sha256"] == expected_prose_digest
        for sample in prose_pins
    )
    assert {sample["locator"]["line_range"] for sample in prose_pins} == {
        "87",
        "89",
        "214",
    }
    warnings = vle.fidelity_source_warnings(source, label=EXTRACT.stem)
    coverage_warnings = {
        warning.kind for warning in warnings
    } & {"uncovered_observation"}
    assert not coverage_warnings, (
        f"Kennedy fidelity pin coverage failed: {sorted(coverage_warnings)}"
    )


def test_kennedy_uncertainty_pin_opens_exact_digest_artifact_and_rejects_mutation():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    pin = next(
        sample
        for sample in source["fidelity_samples"]
        if sample.get("path", "").endswith(
            ".values.temperature_uncertainty_as_printed"
        )
    )
    corpus_root = Path(
        os.environ.get(
            "REGOLITH_CORPUS_ROOT", str(Path.home() / "Repos" / "regolith-corpus")
        )
    )
    reason = vle._locator_source_blind_reason(
        pin, artifact_root=ROOT, corpus_root=corpus_root, doc=source
    )
    assert reason is None, None if reason is None else reason.detail

    mutated = deepcopy(source)
    mutated["species"]["Fe"]["observations"][0]["values"][
        "temperature_uncertainty_as_printed"
    ] = "± 3"
    reason = vle._locator_source_blind_reason(
        pin, artifact_root=ROOT, corpus_root=corpus_root, doc=mutated
    )
    assert reason is not None
    assert "cited_token_absent" in reason.reason_codes


def test_kennedy_oxygen_fidelity_pin_mutation_probe(monkeypatch):
    test_kennedy_table2_fidelity_pin_covers_the_observation()

    resolve_address = vle._sample_evidence_address
    oxygen_path = (
        "species.Fe.observations[kennedy_1948_table2_run_series].values.rows[0]."
        "point_conditions.fO2_control.state.value"
    )

    def drop_oxygen_pin(doc, sample):
        if sample.get("path") == oxygen_path:
            return None
        return resolve_address(doc, sample)

    monkeypatch.setattr(vle, "_sample_evidence_address", drop_oxygen_pin)
    with pytest.raises(AssertionError) as failure:
        test_kennedy_table2_fidelity_pin_covers_the_observation()
    assert "Kennedy fidelity pin coverage failed" in str(failure.value)
    assert "uncovered_observation" in str(failure.value)
