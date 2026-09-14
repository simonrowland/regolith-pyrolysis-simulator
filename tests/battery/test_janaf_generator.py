"""NIST-JANAF source-aware generator controls and negative witnesses."""

from __future__ import annotations

import shutil
import time
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import QUANTITY_UNITS, Phase, Quantity
from simulator.battery.generators import janaf as generator
from simulator.battery.identity import Identity, profile_for, quantity_token
from simulator.battery.records import Species
from simulator.reference_data.janaf import (
    TABLES_DIR,
    iter_table_paths,
    load_table_document,
)

ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURES = ROOT / "tests" / "fixtures" / "janaf"
CURRENT_STORE = (
    ROOT / "data" / "literature" / "observations-v2" / "compilations-janaf.yaml"
)
FIXTURE_IDS = (
    "Al-001",
    "Al-006",
    "Al-070",
    "B-043",
    "Cl-099",
    "H-065",
    "K-022",
    "O-029",
)
STRUCTURED_NUMERIC_COUNTS = {
    "heat_capacity": 75324,
    "entropy": 75322,
    "negative_gibbs_enthalpy_function": 74076,
    "enthalpy_increment": 75322,
    "formation_enthalpy": 74844,
    "formation_gibbs_energy": 74684,
    "log10_formation_equilibrium_constant": 73704,
}


def _generation(table_id: str) -> generator.TableGeneration:
    return generator.generate_table(load_table_document(TABLES_DIR / f"{table_id}.yaml"))


def _phase_values(generated: generator.TableGeneration) -> list[str]:
    result = []
    for segment in generated.report["phase_segments"]:
        phase = segment["phase"]
        result.append(phase.get("value") if phase["tag"] == "value" else "unknown")
    return result


def _series(
    generated: generator.TableGeneration, quantity: Quantity
) -> list[tuple[Decimal, Decimal]]:
    result: list[tuple[Decimal, Decimal]] = []
    for observation in generated.observations:
        if quantity_token(observation.identity) is quantity:
            result.extend(observation.value.series or ())
    return result


def _assert_control(
    generated: generator.TableGeneration,
    expected: dict[Quantity, list[tuple[Decimal, Decimal]]],
) -> None:
    for quantity, points in expected.items():
        assert _series(generated, quantity) == points


def _assert_cell_accounting(report: dict) -> None:
    for column, row in report["cell_accounting"].items():
        assert row["numeric_source_cells"] == (
            row["stored_points"] + row["excluded_numeric_total"]
        ), column
        assert row["unexplained_numeric"] == 0, column


def test_delta_fH_is_an_additive_formation_quantity() -> None:
    delta_h = Identity(quantity=Quantity.DELTA_FH, species=Species("Al2O3", Phase.CR))
    delta_g = Identity(quantity=Quantity.DELTA_FG, species=Species("Al2O3", Phase.CR))
    assert Quantity.DELTA_FH.value == "delta_fH"
    assert QUANTITY_UNITS[Quantity.DELTA_FH] == QUANTITY_UNITS[Quantity.DELTA_FG]
    assert profile_for(delta_h) == profile_for(delta_g)


def test_units_formula_and_token_mismatches_stop_loudly() -> None:
    healthy = load_table_document(TABLES_DIR / "Al-006.yaml")
    bad_unit = deepcopy(healthy)
    bad_unit["table"]["units_as_published"]["heat_capacity"] = "cal mol^-1 K^-1"
    with pytest.raises(ValueError, match="heat_capacity unit"):
        generator.generate_table(bad_unit)
    bad_formula = deepcopy(healthy)
    bad_formula["table"]["index_entry"]["formula_normalised"] = "NotAnElement"
    with pytest.raises(ValueError, match="closed element parse"):
        generator.generate_table(bad_formula)
    bad_token = deepcopy(healthy)
    bad_token["table"]["values"][1]["entropy"]["value"] = 999.0
    with pytest.raises(ValueError, match="token/value mismatch"):
        generator.generate_table(bad_token)


def test_segmentation_witnesses_and_transition_row_assignment() -> None:
    al = _generation("Al-001")
    assert _phase_values(al) == ["cr", "l", "g"]
    al_cp = [
        observation
        for observation in al.observations
        if quantity_token(observation.identity) is Quantity.CP
    ]
    assert al_cp[0].value.series[-1] == (Decimal("933.450"), Decimal("32.959"))
    assert al_cp[1].value.series[0] == (Decimal("933.450"), Decimal("31.751"))
    assert al_cp[1].value.series[-1] == (Decimal("2790.812"), Decimal("31.751"))
    assert al_cp[2].value.series[0] == (Decimal("2790.812"), Decimal("20.795"))

    oxide = _generation("Al-070")
    assert _phase_values(oxide) == ["cr", "l"]
    water = _generation("H-065")
    assert _phase_values(water) == ["l", "g"]
    charged = _generation("Al-006")
    assert _phase_values(charged) == ["g"]
    assert {obs.identity.species.formula for obs in charged.observations} == {"Al+"}
    oxygen = _generation("O-029")
    assert _phase_values(oxygen) == ["g"]

    numbered = _generation("B-043")
    assert _phase_values(numbered) == ["unknown", "unknown", "l"]
    unknown_reasons = [
        segment["phase"]["reason"]
        for segment in numbered.report["phase_segments"][:2]
    ]
    assert all('"I <--> II"' in reason for reason in unknown_reasons)

    broken = load_table_document(TABLES_DIR / "Al-070.yaml")
    broken["table"]["index_entry"]["state"] = "l"
    with pytest.raises(AssertionError):
        assert _phase_values(generator.generate_table(broken)) == ["cr", "l"]


def test_transition_observations_reports_and_vocabulary_gaps() -> None:
    al = _generation("Al-001")
    transitions = [
        observation
        for observation in al.observations
        if quantity_token(observation.identity) is Quantity.TRANSITION_TEMPERATURE
    ]
    assert [obs.identity.subtype.value for obs in transitions] == [
        "crystal-liquid",
        "liquid-ideal-gas",
    ]
    assert [obs.value.point for obs in transitions] == [
        Decimal("933.450"),
        Decimal("2790.812"),
    ]
    assert all(obs.identity.total_pressure_Pa.value == Decimal("100000") for obs in transitions)
    assert all('"' in obs.derivation.relation for obs in transitions)
    assert {row["label"] for row in al.report["non_transition_rows"]} == {
        "TRANSITION",
        "FUGACITY = 1 bar",
    }
    assert [gap["printed_jump_H_minus_H298_kJ_mol"] for gap in al.report["vocabulary_gaps"]] == [
        "10.711",
        "294.002",
    ]
    assert all("absent from schema v2.1" in gap["gap"] for gap in al.report["vocabulary_gaps"])
    maximum_witnesses = {
        "Cl-099": "Cp LAMBDA MAXIMUM",
        "K-022": "Cp HUMP MAXIMUM",
    }
    for table_id, maximum_label in maximum_witnesses.items():
        generated = _generation(table_id)
        assert maximum_label in {row["label"] for row in generated.report["non_transition_rows"]}
        assert all(
            maximum_label not in observation.derivation.relation
            for observation in generated.observations
            if quantity_token(observation.identity) is Quantity.TRANSITION_TEMPERATURE
        )


def test_printed_rounding_checks_flag_broken_rows_without_dropping_points() -> None:
    healthy_doc = load_table_document(TABLES_DIR / "Al-006.yaml")
    healthy = generator.generate_table(healthy_doc)
    assert healthy.report["transcription_identity_failures"] == []
    broken_doc = deepcopy(healthy_doc)
    row = next(
        row
        for row in broken_doc["table"]["values"]
        if row["temperature"]["as_published"] == "298.15"
    )
    row["negative_gibbs_enthalpy_function"].update(as_published="999.999", value=999.999)
    row["log10_formation_equilibrium_constant"].update(as_published="999.999", value=999.999)
    broken = generator.generate_table(broken_doc)
    kinds = {row["identity"] for row in broken.report["transcription_identity_failures"]}
    assert kinds == {"negative_gibbs_enthalpy_function", "log10_Kf_from_delta_fG"}
    for quantity in Quantity.CP, Quantity.S, Quantity.H_MINUS_H298, Quantity.LOG10_KF:
        assert len(_series(broken, quantity)) == len(_series(healthy, quantity))


def test_raw_and_tables_paths_are_byte_identical_and_hash_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table_dir = tmp_path / "tables"
    table_dir.mkdir()
    for table_id in FIXTURE_IDS:
        shutil.copyfile(TABLES_DIR / f"{table_id}.yaml", table_dir / f"{table_id}.yaml")
    calls = 0
    real_parse_table = generator.parse_table

    def counted_parse_table(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_parse_table(*args, **kwargs)

    monkeypatch.setattr(generator, "parse_table", counted_parse_table)
    raw_out = tmp_path / "raw-out"
    tables_out = tmp_path / "tables-out"
    assert generator.main(["--raw", str(RAW_FIXTURES), "--out", str(raw_out)]) == 0
    assert calls == len(FIXTURE_IDS)
    assert generator.main(["--tables", str(table_dir), "--out", str(tables_out)]) == 0
    raw_files = sorted(path.relative_to(raw_out) for path in raw_out.rglob("*.yaml"))
    table_files = sorted(path.relative_to(tables_out) for path in tables_out.rglob("*.yaml"))
    assert raw_files == table_files
    assert all((raw_out / path).read_bytes() == (tables_out / path).read_bytes() for path in raw_files)

    corrupt = tmp_path / "corrupt"
    shutil.copytree(RAW_FIXTURES, corrupt)
    target = corrupt / "Al-006.txt"
    target.write_bytes(target.read_bytes().replace(b"20.786", b"20.787", 1))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        list(generator.documents_from_raw(corrupt))


def test_control_negative_witness_detects_changed_formation_point() -> None:
    healthy_doc = load_table_document(TABLES_DIR / "Al-006.yaml")
    healthy = generator.generate_table(healthy_doc)
    expected = {
        Quantity.DELTA_FG: _series(healthy, Quantity.DELTA_FG),
        Quantity.LOG10_KF: _series(healthy, Quantity.LOG10_KF),
    }
    broken_doc = deepcopy(healthy_doc)
    cell = broken_doc["table"]["values"][1]["formation_gibbs_energy"]
    cell.update(as_published="999.999", value=999.999)
    with pytest.raises(AssertionError):
        _assert_control(generator.generate_table(broken_doc), expected)
    _assert_control(healthy, expected)


def test_cell_accounting_negative_witness_detects_unexplained_point() -> None:
    healthy = deepcopy(_generation("Al-001").report)
    _assert_cell_accounting(healthy)
    broken = deepcopy(healthy)
    broken["cell_accounting"]["entropy"]["stored_points"] -= 1
    broken["cell_accounting"]["entropy"]["unexplained_numeric"] += 1
    with pytest.raises(AssertionError):
        _assert_cell_accounting(broken)


def test_full_corpus_control_cell_accounting_and_transcription_report() -> None:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    current = yaml.load(CURRENT_STORE.read_text(encoding="utf-8"), Loader=loader)
    expected: dict[tuple[str, Quantity], list[tuple[Decimal, Decimal]]] = {}
    for observation in current["observations"]:
        quantity_text = observation["identity"]["quantity"]["value"]
        if quantity_text not in {Quantity.DELTA_FG.value, Quantity.LOG10_KF.value}:
            continue
        expected[(observation["locator"]["table"], Quantity(quantity_text))] = [
            (Decimal(temperature), Decimal(value))
            for temperature, value in observation["value"]["series"]
        ]

    points = Counter()
    observations = Counter()
    phases = Counter()
    cells: dict[str, Counter] = defaultdict(Counter)
    failures = Counter()
    seen_control: set[tuple[str, Quantity]] = set()
    started = time.monotonic()
    paths = list(iter_table_paths())
    assert len(paths) == 1655
    for index, path in enumerate(paths, start=1):
        generated = generator.generate_table(load_table_document(path))
        table_id = generated.report["table_id"]
        for quantity in (Quantity.DELTA_FG, Quantity.LOG10_KF):
            key = (table_id, quantity)
            assert _series(generated, quantity) == expected[key]
            seen_control.add(key)
        for observation in generated.observations:
            quantity = quantity_token(observation.identity)
            observations[quantity.value] += 1
            points[quantity.value] += len(observation.value.series or ())
            if observation.value.point is not None:
                points[quantity.value] += 1
        for segment in generated.report["phase_segments"]:
            phase = segment["phase"]
            phases[phase.get("value", "unknown") if phase["tag"] == "value" else "unknown"] += 1
        _assert_cell_accounting(generated.report)
        for column, row in generated.report["cell_accounting"].items():
            for key in (
                "structured_numeric",
                "short_row_numeric",
                "numeric_source_cells",
                "stored_points",
                "excluded_numeric_total",
                "unexplained_numeric",
            ):
                cells[column][key] += row[key]
        for failure in generated.report["transcription_identity_failures"]:
            failures[failure["identity"]] += 1
        if index % 100 == 0:
            print(f"JANAF test audit: {index}/1655 tables in {time.monotonic() - started:.1f}s", flush=True)

    assert seen_control == set(expected)
    assert points[Quantity.DELTA_FG.value] == 74684
    assert points[Quantity.LOG10_KF.value] == 73704
    assert {column: row["structured_numeric"] for column, row in cells.items()} == STRUCTURED_NUMERIC_COUNTS
    assert all(row["unexplained_numeric"] == 0 for row in cells.values())
    assert cells["heat_capacity"]["stored_points"] == 77538
    assert cells["entropy"]["stored_points"] == 77536
    assert cells["enthalpy_increment"]["stored_points"] == 77536
    assert cells["formation_enthalpy"]["stored_points"] == 74844
    assert cells["formation_gibbs_energy"]["excluded_numeric_total"] == 508
    assert cells["log10_formation_equilibrium_constant"]["excluded_numeric_total"] == 508
    assert observations == {
        "cp": 2013,
        "S": 2013,
        "H_minus_H298": 2013,
        "delta_fH": 2013,
        "delta_fG": 2013,
        "log10_Kf": 2013,
        "transition_temperature": 977,
    }
    assert phases == {"cr": 533, "l": 444, "g": 874, "unknown": 162}
    assert failures == {
        "negative_gibbs_enthalpy_function": 18,
        "log10_Kf_from_delta_fG": 794,
    }
