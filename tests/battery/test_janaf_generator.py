"""NIST-JANAF source-aware generator controls and negative witnesses."""

from __future__ import annotations

import re
import shutil
import time
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import QUANTITY_UNITS, Phase, Quantity
from simulator.battery.generators import janaf as generator
from simulator.battery.identity import (
    Identity,
    log10K_from_delta_fG_kJ_mol,
    profile_for,
    quantity_token,
)
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
_COMBINED_STATES = {"cr,l", "ref", "l,g"}
_FORMATION_COLUMNS = {
    Quantity.DELTA_FH: "formation_enthalpy",
    Quantity.DELTA_FG: "formation_gibbs_energy",
    Quantity.LOG10_KF: "log10_formation_equilibrium_constant",
}
_PHASE_CHANGE_RE = re.compile(r"^\s*(.+?)\s*<-->\s*(.+?)\s*$")


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


def _quantity_observations(
    generated: generator.TableGeneration, quantity: Quantity
) -> list:
    return [
        observation
        for observation in generated.observations
        if quantity_token(observation.identity) is quantity
    ]


def _source_formation_segments(
    document: dict, quantity: Quantity
) -> tuple[list[list[tuple[Decimal, Decimal]]], Counter[tuple[Decimal, Decimal]]]:
    """Build a line-order control independently of the generator's segment_index."""

    table = document["table"]
    ambiguities = [
        item
        for item in table.get("parse_ambiguities", [])
        if isinstance(item.get("line_number"), int) and item.get("raw_line")
    ]
    occupied_lines = {item["line_number"] for item in ambiguities}
    boundaries = sorted(
        (Decimal(item["raw_line"].split("\t")[0]), item["line_number"])
        for item in ambiguities
        if table["index_entry"]["state"] in _COMBINED_STATES
        and len(item["raw_line"].split("\t")) == 6
        and _PHASE_CHANGE_RE.fullmatch(item["raw_line"].split("\t")[5])
    )
    segments: list[list[tuple[Decimal, Decimal, int]]] = [
        [] for _ in range(len(boundaries) + 1)
    ]
    column = _FORMATION_COLUMNS[quantity]
    line_number = 3
    for row in table["values"]:
        while line_number in occupied_lines:
            line_number += 1
        cell = row[column]
        if cell["value"] is not None:
            temperature = Decimal(row["temperature"]["as_published"])
            segment = sum(
                boundary_temperature < temperature
                for boundary_temperature, _line in boundaries
            )
            segments[segment].append(
                (
                    temperature,
                    Decimal(cell["as_published"]),
                    line_number,
                )
            )
        line_number += 1

    merged = Counter()
    for item in ambiguities:
        fields = item["raw_line"].split("\t")
        if len(fields) != 6:
            continue
        tokens = fields[5].split()
        if len(tokens) != 3:
            continue
        try:
            values = [Decimal(token) for token in tokens]
        except InvalidOperation:
            continue
        index = list(_FORMATION_COLUMNS).index(quantity)
        point = (Decimal(fields[0]), values[index])
        segment = sum(
            boundary_temperature < point[0]
            or (
                boundary_temperature == point[0]
                and boundary_line < item["line_number"]
            )
            for boundary_temperature, boundary_line in boundaries
        )
        segments[segment].append((*point, item["line_number"]))
        merged[point] += 1

    return [
        [(temperature, value) for temperature, value, _line in sorted(rows)]
        for rows in segments
    ], merged


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


def test_generated_observations_state_the_schema_output_unit() -> None:
    generated = _generation("Al-003")
    for observation in generated.observations:
        quantity = quantity_token(observation.identity)
        assert observation.derivation.output_unit == QUANTITY_UNITS[quantity]


def test_formation_basis_names_the_janaf_convention_without_an_invented_locator() -> None:
    assert generator.FORMATION_BASIS_REASON == (
        "JANAF formation from the elements in their reference states, as defined in "
        "JANAF Thermochemical Tables, 4th edition, introduction; schema v2.1 has no "
        "closed token for this convention, and the table does not print a "
        "temperature-specific formation reaction"
    )
    for quantity in (Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF):
        observation = _quantity_observations(_generation("Al-003"), quantity)[0]
        assert observation.identity.reaction.reason == generator.FORMATION_BASIS_REASON
        assert (
            observation.identity.formation_elements.reason
            == generator.FORMATION_BASIS_REASON
        )
        assert (
            f"formation_basis={generator.FORMATION_BASIS_REASON}"
            in observation.derivation.relation
        )


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

    br = _generation("Br-078")
    assert _phase_values(br) == ["cr", "l"]
    br_cp = _quantity_observations(br, Quantity.CP)
    assert br_cp[0].value.series[-2:] == (
        (Decimal("900"), Decimal("93.621")),
        (Decimal("900.000"), Decimal("93.621")),
    )
    assert br_cp[1].value.series[0] == (Decimal("900.000"), Decimal("91.002"))

    barium = _generation("Ba-001")
    barium_cp = _quantity_observations(barium, Quantity.CP)
    assert barium_cp[2].identity.species.polymorph.value == "gamma"
    assert barium_cp[2].value.series[-2:] == (
        (Decimal("1000"), Decimal("39.066")),
        (Decimal("1000.000"), Decimal("39.066")),
    )
    assert barium_cp[3].value.series[0] == (Decimal("1000.000"), Decimal("43.304"))

    iron = _generation("Fe-003")
    assert _phase_values(iron) == ["cr", "cr", "cr", "l", "g"]
    iron_cp = _quantity_observations(iron, Quantity.CP)
    assert [obs.identity.species.polymorph.value for obs in iron_cp[:3]] == [
        "alpha",
        "gamma",
        "delta",
    ]

    numbered = _generation("B-043")
    assert _phase_values(numbered) == ["cr", "cr", "l"]
    assert [
        obs.identity.species.polymorph.value
        for obs in _quantity_observations(numbered, Quantity.CP)[:2]
    ] == ["i", "ii"]

    assert "JANAF documented reference-phase convention" in oxygen.report[
        "phase_segments"
    ][0]["phase_basis"]

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
    assert all("table declared standard state" in obs.derivation.relation for obs in transitions)
    assert {row["label"] for row in al.report["non_transition_rows"]} == {
        "TRANSITION",
        "FUGACITY = 1 bar",
    }
    assert [gap["printed_jump_H_minus_H298_kJ_mol"] for gap in al.report["vocabulary_gaps"]] == [
        "10.711",
        "294.002",
    ]
    assert all("absent from schema v2.1" in gap["gap"] for gap in al.report["vocabulary_gaps"])
    assert al.report["adjacent_condition_rows"] == [
        {
            "temperature_as_published": "2790.812",
            "label": "FUGACITY = 1 bar",
            "line_number": 35,
            "transition_label": "LIQUID <--> IDEAL GAS",
            "transition_line_number": 34,
            "assigned_segment": "segment-2",
        }
    ]

    pressure_witnesses = {
        "H-065": Decimal("100000"),
        "H-066": Decimal("1000000"),
        "H-067": Decimal("10000000"),
    }
    for table_id, pressure in pressure_witnesses.items():
        generated = _generation(table_id)
        transition = _quantity_observations(
            generated, Quantity.TRANSITION_TEMPERATURE
        )[0]
        assert transition.identity.total_pressure_Pa.value == pressure
        assert "adjacent JANAF row" in transition.derivation.relation
        condition = generated.report["adjacent_condition_rows"]
        assert len(condition) == 1
        assert condition[0]["label"].startswith("PRESSURE = ")
        for quantity, expected in {
            Quantity.CP: Decimal("46.063") if table_id == "H-066" else None,
            Quantity.S: Decimal("181.988") if table_id == "H-066" else None,
            Quantity.H_MINUS_H298: Decimal("48.138") if table_id == "H-066" else None,
        }.items():
            if expected is not None:
                assert _quantity_observations(generated, quantity)[1].value.series[0] == (
                    Decimal("453.070"),
                    expected,
                )
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


def test_janaf_identity_uses_the_source_gas_constant() -> None:
    assert generator.JANAF_R_J_PER_MOL_K == Decimal("8.31441")
    modern = log10K_from_delta_fG_kJ_mol(Decimal("-5582.653"), Decimal("298.15"))
    source = log10K_from_delta_fG_kJ_mol(
        Decimal("-5582.653"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.JANAF_R_J_PER_MOL_K,
    )
    assert modern != source
    assert not any(
        failure["identity"] == "log10_Kf_from_delta_fG"
        and failure["temperature_as_published"] == "298.15"
        for failure in _generation("B-132").report["transcription_identity_failures"]
    )
    assert any(
        failure["identity"] == "log10_Kf_from_delta_fG"
        and failure["temperature_as_published"] == "300"
        for failure in _generation("B-133").report["transcription_identity_failures"]
    )


def test_readable_merged_formation_triples_are_stored_and_reported_once() -> None:
    generated = _generation("Al-003")
    for quantity, value in {
        Quantity.DELTA_FH: Decimal("0"),
        Quantity.DELTA_FG: Decimal("0"),
        Quantity.LOG10_KF: Decimal("0"),
    }.items():
        assert (Decimal("1000"), value) in _series(generated, quantity)
    assert [
        row
        for row in generated.report["merged_formation_rows"]
        if row["line_number"] == 20
    ] == [
        {
            "temperature_as_published": "1000",
            "line_number": 20,
            "raw_text": "0. 0. 0.",
            "assigned_segment": "segment-0",
            "values_as_published": {
                "formation_enthalpy": "0.",
                "formation_gibbs_energy": "0.",
                "log10_formation_equilibrium_constant": "0.",
            },
        }
    ]

    malformed_document = load_table_document(TABLES_DIR / "Al-003.yaml")
    malformed_row = next(
        row
        for row in malformed_document["table"]["parse_ambiguities"]
        if row.get("line_number") == 20
    )
    malformed_row["raw_line"] = malformed_row["raw_line"].replace(
        "0. 0. 0.", "0. unreadable 0."
    )
    malformed = generator.generate_table(malformed_document)
    assert (Decimal("1000"), Decimal("0")) not in _series(
        malformed, Quantity.DELTA_FG
    )
    assert any(
        row["raw_text"] == "0. unreadable 0."
        for row in malformed.report["non_transition_rows"]
    )


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


def test_cell_accounting_negative_witness_breaks_generator_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_columns = dict(generator._COLUMN_QUANTITIES)
    broken_columns.pop("entropy")
    monkeypatch.setattr(generator, "_COLUMN_QUANTITIES", broken_columns)
    with pytest.raises(AssertionError, match="unexplained entropy cells"):
        _generation("Al-001")


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
    merged_rows = 0
    merged_extras = Counter()
    seen_control: set[tuple[str, Quantity]] = set()
    started = time.monotonic()
    paths = list(iter_table_paths())
    assert len(paths) == 1655
    for index, path in enumerate(paths, start=1):
        document = load_table_document(path)
        generated = generator.generate_table(document)
        table_id = generated.report["table_id"]
        for quantity in (Quantity.DELTA_FG, Quantity.LOG10_KF):
            key = (table_id, quantity)
            source_segments, expected_merged = _source_formation_segments(
                document, quantity
            )
            quantity_observations = _quantity_observations(generated, quantity)
            assert len(quantity_observations) == len(source_segments)
            for observation, source_series, segment in zip(
                quantity_observations,
                source_segments,
                generated.report["phase_segments"],
                strict=True,
            ):
                assert list(observation.value.series or ()) == source_series
                assert (
                    observation.identity.species.phase.tag.value
                    == segment["phase"]["tag"]
                )
                if segment["phase"]["tag"] == "value":
                    assert (
                        observation.identity.species.phase.value.value
                        == segment["phase"]["value"]
                    )
            actual = Counter(_series(generated, quantity))
            legacy = Counter(expected[key])
            assert not legacy - actual
            assert actual - legacy == expected_merged
            merged_extras[quantity.value] += sum(expected_merged.values())
            seen_control.add(key)
        merged_rows += len(generated.report["merged_formation_rows"])
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
            print(
                f"JANAF test audit: {index}/1655 tables in "
                f"{time.monotonic() - started:.1f}s",
                flush=True,
            )

    assert seen_control == set(expected)
    assert points[Quantity.DELTA_FH.value] == 75352
    assert points[Quantity.DELTA_FG.value] == 75192
    assert points[Quantity.LOG10_KF.value] == 74212
    assert merged_rows == 508
    assert merged_extras == {"delta_fG": 508, "log10_Kf": 508}
    assert {
        column: row["structured_numeric"] for column, row in cells.items()
    } == STRUCTURED_NUMERIC_COUNTS
    assert all(row["unexplained_numeric"] == 0 for row in cells.values())
    assert cells["heat_capacity"]["stored_points"] == 77538
    assert cells["entropy"]["stored_points"] == 77536
    assert cells["enthalpy_increment"]["stored_points"] == 77536
    assert cells["formation_enthalpy"]["stored_points"] == 75352
    assert cells["formation_gibbs_energy"]["stored_points"] == 75192
    assert cells["log10_formation_equilibrium_constant"]["stored_points"] == 74212
    assert cells["formation_enthalpy"]["excluded_numeric_total"] == 0
    assert cells["formation_gibbs_energy"]["excluded_numeric_total"] == 0
    assert cells["log10_formation_equilibrium_constant"]["excluded_numeric_total"] == 0
    assert observations == {
        "cp": 2013,
        "S": 2013,
        "H_minus_H298": 2013,
        "delta_fH": 2013,
        "delta_fG": 2013,
        "log10_Kf": 2013,
        "transition_temperature": 977,
    }
    assert phases == {"cr": 690, "l": 444, "g": 874, "unknown": 5}
    assert failures == {
        "negative_gibbs_enthalpy_function": 18,
        "log10_Kf_from_delta_fG": 438,
    }
