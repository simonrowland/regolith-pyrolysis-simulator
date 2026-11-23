from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from math import isclose, log, log10
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from simulator.battery import score
from simulator.battery.enums import Engine, NoticeKind, Phase, Quantity, RefusalReason
from simulator.battery.fo2_ladder import has_printed_fo2
from simulator.battery.migrate import (
    Migrator,
    _printed_point_phase_kind,
    _printed_point_phase_text,
)
from simulator.battery.records import ApproxPressure, BandBasis, Located, State
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kennedy-1948-ferric-ferrous-silicate-melts.yaml"


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
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []

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
            # Green's missing-oxygen gate applies until the canonical default lands.
            backend.reset_mock()
            for row in missing_oxygen:
                residual, _ = score.compile_residual(row, engine, context=context)
                assert residual.numeric is None
                assert residual.refusal is not None
                assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
                assert residual.refusal.detail["input"] == "printed_fO2"
            backend.assert_not_called()


def test_kennedy_fractional_oxygen_requires_located_printed_evidence(tmp_path: Path):
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    control = source["species"]["Fe"]["observations"][0]["values"]["rows"][0][
        "point_conditions"
    ]["fO2_control"]
    original = dict(control)
    path = tmp_path / EXTRACT.name
    for keep_locator, inferred in (
        (True, False), (False, False), (True, True), (False, True)
    ):
        control.clear()
        control.update(original)
        if not keep_locator:
            del control["locator"]
        if inferred:
            control["inferred"] = True
        path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
        row = _row_at(_migrate_extract(path).observations.values(), "1430", "23")
        printed = keep_locator and not inferred
        assert has_printed_fo2(row, row.identity) == printed
        if printed:
            pressure = row.point_conditions["fO2_Pa"]
            assert pressure.locator.published_page == 532
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
