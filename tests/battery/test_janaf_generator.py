"""NIST-JANAF source-aware generator controls and negative witnesses."""

from __future__ import annotations

import os
import re
import shutil
import time
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import QUANTITY_UNITS, Phase, Quantity
from simulator.battery.generators import janaf as generator
from simulator.battery.migrate import iter_observation_store_paths
from tests.battery import compilation_shard_observation_count
from simulator.battery.identity import (
    Identity,
    log10K_from_delta_fG_kJ_mol,
    profile_for,
    quantity_token,
)
from simulator.battery.records import Species
from simulator.reference_data.janaf import (
    GRID_RANGE_REASON,
    INCONSISTENT_LAYOUT_REASON,
    NON_DATA_MARKER_KIND,
    STRUCTURED_LAYOUT_REASON,
    TABLES_DIR,
    TRAILING_EMPTY_LAYOUT_REASON,
    iter_table_paths,
    load_table_document,
    parse_janaf_txt,
    table_printed_temperatures,
)

ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURES = ROOT / "tests" / "fixtures" / "janaf"
CURRENT_STORE_DIR = ROOT / "data" / "literature" / "observations-v2"
CURRENT_STORE_PATTERN = "compilations-janaf.yaml"
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
_CONCATENATED_T_CP_RE = re.compile(
    r"^([+-]?\d+\.\d{3})"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)$"
)
_CRYSTAL_POLYMORPH_LABELS = {"ALPHA", "BETA", "GAMMA", "DELTA", "I", "II", "III"}


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
    boundaries = []
    for item in ambiguities:
        fields = item["raw_line"].split("\t")
        while fields and fields[-1] == "":
            fields.pop()
        temperature_token = fields[0] if fields else ""
        if len(fields) == 5:
            concatenated = _CONCATENATED_T_CP_RE.fullmatch(temperature_token)
            if concatenated is not None:
                temperature_token = concatenated.group(1)
        transition = _PHASE_CHANGE_RE.fullmatch(fields[-1]) if fields else None
        if transition is None:
            continue
        state = table["index_entry"]["state"]
        named_crystal_transition = {
            transition.group(1).strip().upper(),
            transition.group(2).strip().upper(),
        } <= _CRYSTAL_POLYMORPH_LABELS
        if state in _COMBINED_STATES or (
            state == "cr" and named_crystal_transition
        ):
            boundaries.append((Decimal(temperature_token), item["line_number"]))
    boundaries.sort()
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
        if point[1] != 0:
            continue
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


def _logk_pair_violates_printed_rounding(
    temperature_token: str, delta_fG_token: str, log10_Kf_token: str
) -> bool:
    temperature = Decimal(temperature_token)
    delta_fG = Decimal(delta_fG_token)
    log10_Kf = Decimal(log10_Kf_token)

    def grain(token: str) -> Decimal:
        value = Decimal(token)
        return Decimal(1).scaleb(value.as_tuple().exponent)

    calculated = log10K_from_delta_fG_kJ_mol(
        delta_fG,
        temperature,
        gas_constant_J_per_mol_K=generator.JANAF_R_J_PER_MOL_K,
    )
    temperature_radius = grain(temperature_token) / 2
    per_delta_fG = abs(
        log10K_from_delta_fG_kJ_mol(
            Decimal("1"),
            temperature - temperature_radius,
            gas_constant_J_per_mol_K=generator.JANAF_R_J_PER_MOL_K,
        )
    )
    tolerance = (
        grain(log10_Kf_token) / 2
        + grain(delta_fG_token) / 2 * per_delta_fG
        + abs(calculated) * temperature_radius / (temperature - temperature_radius)
    )
    return abs(log10_Kf - calculated) > tolerance


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
    assert {obs.identity.species.formula for obs in charged.observations} == {"Al"}
    assert {obs.identity.species.charge.value for obs in charged.observations} == {1}
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


def test_janaf_phase_labels_use_closed_tokens_without_collapsing_a_span() -> None:
    from simulator.battery.generators.janaf import janaf_extract_phase
    from simulator.battery.migrate import map_phase

    barium = _generation("Ba-001")
    alpha_beta = next(
        obs
        for obs in barium.observations
        if "transition_temperature:T=" in obs.observation_id
        and getattr(obs.identity.subtype, "is_value", False)
        and obs.identity.subtype.value == "alpha-beta"
    )
    assert alpha_beta.identity.species.phase.is_value
    assert alpha_beta.identity.species.phase.value is Phase.CR
    assert alpha_beta.identity.species.polymorph.is_unknown
    assert "alpha -> beta" in (alpha_beta.identity.species.polymorph.reason or "")
    assert alpha_beta.value.point == Decimal("582.530")

    crystal_liquid = next(
        obs
        for obs in _generation("Al-001").observations
        if "transition_temperature:T=" in obs.observation_id
        and getattr(obs.identity.subtype, "is_value", False)
        and obs.identity.subtype.value == "crystal-liquid"
    )
    assert crystal_liquid.identity.species.phase.is_unknown
    assert crystal_liquid.identity.species.phase.reason == (
        'transition spans phases named by "CRYSTAL <--> LIQUID" (cr -> l); '
        "v2.1 species.phase has one phase axis and cannot hold both"
    )
    assert crystal_liquid.value.point == Decimal("933.450")

    sodium = _generation("Na-020")
    cp = _quantity_observations(sodium, Quantity.CP)
    assert [obs.observation_id for obs in cp] == [
        "nist-janaf-4th:Na-020:cp:phase-window:whole"
    ]
    temperatures = [point[0] for point in cp[0].value.series or ()]
    assert Decimal("514.000") in temperatures
    assert Decimal("1157.000") in temperatures
    roman = next(
        obs
        for obs in sodium.observations
        if "transition_temperature:T=" in obs.observation_id
        and getattr(obs.identity.subtype, "is_value", False)
        and obs.identity.subtype.value == "iv-i"
    )
    assert roman.identity.species.phase.value is Phase.CR
    assert "iv -> i" in (roman.identity.species.polymorph.reason or "")
    melting = next(
        obs
        for obs in sodium.observations
        if "transition_temperature:T=" in obs.observation_id
        and getattr(obs.identity.subtype, "is_value", False)
        and obs.identity.subtype.value == "i-liquid"
    )
    assert melting.identity.species.phase.is_unknown
    assert "(cr -> l)" in (melting.identity.species.phase.reason or "")

    sulfate = _generation("Na-025")
    segments = sulfate.report["phase_segments"]
    assert segments[0]["phase"]["value"] == "cr"
    assert segments[0]["polymorph"]["value"] == "v"
    assert segments[1]["phase"]["value"] == "cr"
    assert segments[1]["polymorph"]["value"] == "iv"
    sulfate_cp = _quantity_observations(sulfate, Quantity.CP)
    assert sulfate_cp[0].observation_id == (
        "nist-janaf-4th:Na-025:cp:phase-window:T=open..458.000"
    )
    assert sulfate_cp[1].observation_id == (
        "nist-janaf-4th:Na-025:cp:phase-window:T=458.000..514.000"
    )
    assert (Decimal("458.000"), Decimal("153.331")) in (sulfate_cp[0].value.series or ())
    assert (Decimal("514.000"), Decimal("160.712")) in (sulfate_cp[1].value.series or ())

    epsilon = next(
        segment
        for segment in _generation("Al-051").report["phase_segments"]
        if segment["phase"]["tag"] != "value"
    )
    assert "EPSILON" in segment_reason(epsilon)
    fluid = _generation("H-068").report["phase_segments"][0]["phase"]
    assert fluid["tag"] == "unknown"
    assert fluid["reason"] == "JANAF state 'fl' is not a schema v2.1 Phase token"

    refused, spelling = map_phase("ideal_gas")
    assert refused.is_unknown and spelling == "ideal_gas"
    assert janaf_extract_phase("janaf-4th", "ideal_gas") is Phase.G
    assert janaf_extract_phase("kems-041-sossi-fegley-2018", "ideal_gas") is None
    assert janaf_extract_phase("janaf-4th", "solid") is None
    assert janaf_extract_phase("janaf-4th", "phosphate_melt_to_PO_g") is None


def test_liquid_glass_region_is_not_stamped_liquid() -> None:
    """Glass-side rows of a liquid table are not labelled phase l.

    The printed GLASS <--> LIQUID temperature is the only boundary. The
    series stays one observation (no new segment id). A two-phase transition
    row stays unknown.
    """

    label = "supercooled liquid / glass-transition region"
    magnesium = _generation("Mg-013")
    series = [
        observation
        for observation in magnesium.observations
        if quantity_token(observation.identity) is not Quantity.TRANSITION_TEMPERATURE
    ]
    assert [observation.observation_id for observation in series] == [
        f"nist-janaf-4th:Mg-013:{quantity}:phase-window:whole"
        for quantity in (
            "cp",
            "S",
            "H_minus_H298",
            "delta_fH",
            "delta_fG",
            "log10_Kf",
        )
    ]
    assert len(magnesium.report["phase_segments"]) == 1
    for observation in series:
        phase = observation.identity.species.phase
        assert phase.is_unknown
        assert phase.value is None
        reason = phase.reason or ""
        assert label in reason
        assert 'printed "GLASS <--> LIQUID" at 900.000 K' in reason
        assert "not stamped liquid" in reason
        assert label in observation.derivation.relation
        assert observation.identity.species.polymorph.is_not_applicable
    heat_capacity = _quantity_observations(magnesium, Quantity.CP)[0]
    assert [point for point in heat_capacity.value.series or () if point[0] == Decimal("900")] == [
        (Decimal("900"), Decimal("119.675")),
        (Decimal("900.000"), Decimal("119.675")),
        (Decimal("900.000"), Decimal("146.440")),
    ]
    assert (Decimal("3000"), Decimal("146.440")) in (heat_capacity.value.series or ())
    glass_liquid = next(
        observation
        for observation in magnesium.observations
        if "transition_temperature:T=" in observation.observation_id
        and getattr(observation.identity.subtype, "is_value", False)
        and observation.identity.subtype.value == "glass-liquid"
    )
    assert glass_liquid.identity.species.phase.is_unknown
    assert glass_liquid.identity.species.phase.reason == (
        'transition spans phases named by "GLASS <--> LIQUID" (glass -> l); '
        "v2.1 species.phase has one phase axis and cannot hold both"
    )
    crystal_liquid = next(
        observation
        for observation in magnesium.observations
        if "transition_temperature:T=" in observation.observation_id
        and getattr(observation.identity.subtype, "is_value", False)
        and observation.identity.subtype.value == "iii-liquid"
    )
    assert crystal_liquid.identity.species.phase.is_unknown
    assert "(cr -> l)" in (crystal_liquid.identity.species.phase.reason or "")

    tungstate = _generation("W-003")
    tungstate_cp = _quantity_observations(tungstate, Quantity.CP)
    assert [observation.observation_id for observation in tungstate_cp] == [
        "nist-janaf-4th:W-003:cp:phase-window:whole"
    ]
    tungstate_reason = tungstate_cp[0].identity.species.phase.reason or ""
    assert tungstate_cp[0].identity.species.phase.is_unknown
    assert label in tungstate_reason
    assert 'printed "GLASS <--> LIQ" at ' in tungstate_reason


def segment_reason(segment: dict) -> str:
    return str(segment["phase"].get("reason") or "")


def test_janaf_extract_ideal_gas_maps_only_for_that_source(tmp_path: Path) -> None:
    import copy

    from simulator.battery.migrate import Migrator
    from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree

    def run(source_id: str):
        doc = copy.deepcopy(FIXTURE_EXTRACT)
        doc["source_id"] = source_id
        doc["species"]["Na"]["observations"][0]["phase"] = "ideal_gas"
        doc["species"]["Na"]["observations"][0]["observation_id"] = "ideal_gas_row"
        root = tmp_path / source_id
        _write_min_tree(root, doc)
        index = yaml.safe_load((root / "data/literature/INDEX.yaml").read_text())
        index["sources"][0]["source_id"] = source_id
        (root / "data/literature/INDEX.yaml").write_text(yaml.safe_dump(index))
        return Migrator(root=root).run()

    janaf = run("janaf-4th")
    rows = [
        obs
        for obs in janaf.observations.values()
        if obs.observation_id.startswith("janaf-4th::ideal_gas_row::T=1200.0:")
    ]
    assert len(rows) == 1
    assert rows[0].identity.species.phase.value is Phase.G
    assert not any(
        "ideal_gas_row" in entry.observation_id and "phase" in entry.axes
        for entry in janaf.queue
    )
    other = run("fixture-source")
    other_rows = [
        obs for obs in other.observations.values() if "ideal_gas_row" in obs.observation_id
    ]
    assert other_rows
    assert all(obs.identity.species.phase.is_unknown for obs in other_rows)
    assert any(
        "ideal_gas_row" in entry.observation_id and "phase" in entry.axes
        for entry in other.queue
    )


def test_concatenated_transition_temperature_and_cp_rows_are_recovered() -> None:
    for table_id, expected_polymorphs in {
        "C-083": ["i", "ii", "iii"],
        "C-085": ["i", "ii", "iii", None],
    }.items():
        generated = _generation(table_id)
        assert any(
            row["temperature_as_published"] == "288.500"
            and row["label"] == "II <--> III"
            for row in generated.report["transition_rows"]
        )
        assert generated.report["refused_concatenated_rows"] == []
        assert len(generated.report["recovered_concatenated_rows"]) == 2
        actual_polymorphs = [
            segment["polymorph"].get("value")
            if segment["polymorph"]["tag"] == "value"
            else None
            for segment in generated.report["phase_segments"]
        ]
        assert actual_polymorphs == expected_polymorphs
        cp_observations = _quantity_observations(generated, Quantity.CP)
        assert (Decimal("288.500"), Decimal("1548.088")) in (
            cp_observations[1].value.series or ()
        )
        assert (Decimal("288.500"), Decimal("1548.129")) in (
            cp_observations[2].value.series or ()
        )


def test_concatenated_transition_pair_is_refused_when_temperatures_differ() -> None:
    document = load_table_document(TABLES_DIR / "C-083.yaml")
    companion = next(
        row
        for row in document["table"]["parse_ambiguities"]
        if row.get("line_number") == 9
    )
    companion["raw_line"] = companion["raw_line"].replace(
        "288.5001548.129", "289.5001548.129"
    )
    generated = generator.generate_table(document)
    assert len(generated.report["refused_concatenated_rows"]) == 2
    assert all(
        row["reason"] == generator.CONCATENATED_ROW_REFUSAL_REASON
        for row in generated.report["refused_concatenated_rows"]
    )
    assert not any(
        row["temperature_as_published"] == "288.500"
        and row["label"] == "II <--> III"
        for row in generated.report["transition_rows"]
    )


def test_native_parser_preserves_concatenated_numeric_first_fields() -> None:
    payload = (
        "Sodium Cyanide (NaCN)\tC1N1Na1(cr)\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tdelta-f H\tdelta-f G\tlog Kf\n"
        "0\t0.\t0.\tINFINITE\t-19.422\t-98.299\t-98.299\tINFINITE\n"
        "288.5001548.088\t111.062\t118.520\t-2.152\tII <--> III\n"
        "288.5001548.129\t111.063\t118.520\t-2.151\tTRANSITION\t\t\t\t\t\t\n"
    )
    parsed = parse_janaf_txt(
        payload,
        table_id="C-test",
        url="https://example.invalid/C-test.html",
        download_url="https://example.invalid/C-test.txt",
    )
    assert [row["line_number"] for row in parsed.parse_ambiguities] == [4, 5]


def test_all_labelled_transition_temperatures_use_three_decimals() -> None:
    transition_count = 0
    for path in iter_table_paths():
        table = load_table_document(path)["table"]
        for ambiguity in table.get("parse_ambiguities", []):
            fields = str(ambiguity.get("raw_line", "")).split("\t")
            while fields and fields[-1] == "":
                fields.pop()
            if not fields or _PHASE_CHANGE_RE.fullmatch(fields[-1]) is None:
                continue
            transition_count += 1
            temperature_field = fields[0]
            match = _CONCATENATED_T_CP_RE.fullmatch(temperature_field)
            temperature_token = match.group(1) if match else temperature_field
            assert re.fullmatch(r"[+-]?\d+\.\d{3}", temperature_token), (
                table["table_id"],
                ambiguity["line_number"],
                temperature_field,
            )
    assert transition_count == 979


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
    broken = generator.generate_table(broken_doc)
    kinds = {row["identity"] for row in broken.report["transcription_identity_failures"]}
    assert kinds == {"negative_gibbs_enthalpy_function"}
    for quantity in Quantity.CP, Quantity.S, Quantity.H_MINUS_H298, Quantity.LOG10_KF:
        assert len(_series(broken, quantity)) == len(_series(healthy, quantity))


def test_hf004_printed_enthalpy_erratum_is_refused() -> None:
    generated = _generation("Hf-004")
    enthalpy = _series(generated, Quantity.H_MINUS_H298)
    assert (Decimal("2500"), Decimal("63.307")) in enthalpy
    assert (Decimal("2500.000"), Decimal("66.307")) not in enthalpy
    assert generated.report["stored_cell_errata"] == [
        {
            **generator.STORED_CELL_ERRATA[0],
            "disposition": "refused",
            "reason": generator.PRINTED_CELL_ERRATUM_REASON,
        }
    ]
    accounting = generated.report["cell_accounting"]["enthalpy_increment"]
    assert accounting["excluded_numeric"] == {
        generator.PRINTED_CELL_ERRATUM_REASON: 1
    }


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


def test_al002_nonzero_merged_formation_cells_are_refused() -> None:
    refused = _generation("Al-002")
    for quantity, value in {
        Quantity.DELTA_FH: Decimal("10.585"),
        Quantity.DELTA_FG: Decimal("0.760"),
        Quantity.LOG10_KF: Decimal("0.040"),
    }.items():
        assert (Decimal("1000"), value) not in _series(refused, quantity)
    refused_row = next(
        row
        for row in refused.report["merged_formation_rows"]
        if row["temperature_as_published"] == "1000"
    )
    assert refused_row["raw_text"] == "10.585 0.760  0.040"
    assert refused_row["assigned_segment"] == "segment-0"
    assert {
        column: disposition["disposition"]
        for column, disposition in refused_row["cell_dispositions"].items()
    } == {
        "formation_enthalpy": "refused",
        "formation_gibbs_energy": "refused",
        "log10_formation_equilibrium_constant": "refused",
    }
    assert all(
        disposition["reason"] == generator.MERGED_FORMATION_REFUSAL_REASON
        for disposition in refused_row["cell_dispositions"].values()
    )


def test_al003_merged_formation_zeros_are_stored_in_the_right_segment() -> None:
    zeros = _generation("Al-003")
    for quantity in (Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF):
        assert (Decimal("1000"), Decimal("0")) in _series(zeros, quantity)
    zero_row = next(
        row
        for row in zeros.report["merged_formation_rows"]
        if row["line_number"] == 20
    )
    assert zero_row["raw_text"] == "0. 0. 0."
    assert zero_row["assigned_segment"] == "segment-0"
    assert {
        column: disposition["disposition"]
        for column, disposition in zero_row["cell_dispositions"].items()
    } == {
        "formation_enthalpy": "stored_zero",
        "formation_gibbs_energy": "stored_zero",
        "log10_formation_equilibrium_constant": "stored_zero",
    }

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


def test_fe004_mixed_merged_formation_cells_are_classified_individually() -> None:
    mixed = _generation("Fe-004")
    assert (Decimal("1700"), Decimal("0.001")) not in _series(
        mixed, Quantity.DELTA_FH
    )
    assert (Decimal("1700"), Decimal("0")) in _series(mixed, Quantity.DELTA_FG)
    assert (Decimal("1700"), Decimal("0")) in _series(mixed, Quantity.LOG10_KF)
    mixed_row = next(
        row
        for row in mixed.report["merged_formation_rows"]
        if row["temperature_as_published"] == "1700"
    )
    assert {
        column: disposition["disposition"]
        for column, disposition in mixed_row["cell_dispositions"].items()
    } == {
        "formation_enthalpy": "refused",
        "formation_gibbs_energy": "stored_zero",
        "log10_formation_equilibrium_constant": "stored_zero",
    }


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


_DELIMITER_SAMPLE_TABLES = ("Al-006", "Al-001", "O-029")
_DELIMITER_SAMPLE_TEMPERATURES = ("100", "200", "298.15", "6000")
_FIELD_COUNT_REASON = "expected 8 tab-separated values; found 7"


def _expected_delimiter_reason(table_id: str, temperature: str, tab_index: int) -> str:
    """Pin the refusal that actually fires, not merely that some refusal fired.

    Al-006 100/200 K rows are content-5 with trailing empties (raw_n=11).
    Tab 0 concatenates T+Cp and still reaches eight fields via leftover
    empties (TRAILING_EMPTY_LAYOUT_REASON). Tabs 1–3 drop an interior
    content delimiter (STRUCTURED_LAYOUT_REASON). Tabs 4–9 drop a trailing
    empty delimiter so raw_n leaves the modal 11 (INCONSISTENT_LAYOUT_REASON).
    Full eight-field rows drop raw_n 8→7 (_FIELD_COUNT_REASON). None of
    these sampled delimiter losses hit GRID_RANGE_REASON.
    """

    if table_id == "Al-006" and temperature in {"100", "200"}:
        if tab_index == 0:
            return TRAILING_EMPTY_LAYOUT_REASON
        if tab_index <= 3:
            return STRUCTURED_LAYOUT_REASON
        return INCONSISTENT_LAYOUT_REASON
    return _FIELD_COUNT_REASON
_NON_DATA_MARKER_LINES = (
    ("Ba-001", (11, 16, 22, 36)),
    ("Ba-002", (14, 19)),
    ("Ba-003", (14,)),
    ("Ba-004", (14, 19, 25)),
    ("Ca-010", (13,)),
    ("Ca-011", (16,)),
    ("Ca-014", (20,)),
    ("Ca-015", (16,)),
    ("Ca-016", (20, 25)),
    ("Ca-024", (13,)),
    ("Ca-025", (16,)),
    ("Ca-028", (27,)),
    ("Ca-029", (38,)),
    ("Cr-014", (9,)),
    ("Cr-015", (24,)),
    ("Cr-016", (9, 35)),
    ("Cu-011", (13,)),
    ("Cu-012", (17,)),
    ("Cu-020", (16,)),
    ("Cu-021", (21,)),
    ("S-021", (18,)),
)
_JANAF_SOURCE_DIR = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
) / "raw" / "janaf-nist-txt"


def _raw_table_path(table_id: str) -> Path:
    fixture = RAW_FIXTURES / f"{table_id}.txt"
    return fixture if fixture.is_file() else _JANAF_SOURCE_DIR / f"{table_id}.txt"


def _require_raw_table(table_id: str) -> Path:
    """Return the raw table path, or skip when only a private corpus copy exists."""
    path = _raw_table_path(table_id)
    if not path.is_file():
        pytest.skip(f"JANAF raw table {table_id!r} is absent ({path})")
    return path


def _parse_raw_document(table_id: str, payload: bytes):
    return generator.parse_table(
        payload,
        {
            "table_id": table_id,
            "url": f"https://janaf.nist.gov/tables/{table_id}.html",
            "download_url": f"https://janaf.nist.gov/tables/{table_id}.txt",
        },
        _raw_table_path(table_id),
    )


def _cp_temperatures(generated: generator.TableGeneration) -> list[str]:
    return [
        str(temperature)
        for observation in generated.observations
        if quantity_token(observation.identity) is Quantity.CP
        for temperature, _value in (observation.value.series or ())
    ]


def _row_for_temperature(text: str, temperature: str) -> tuple[int, str]:
    prefix = temperature + "\t"
    for index, line in enumerate(text.splitlines()):
        if line.startswith(prefix):
            return index, line
    raise AssertionError(f"no {temperature} K row")


def test_raw_numeric_census_detects_a_deleted_tab() -> None:
    payload = (RAW_FIXTURES / "Al-006.txt").read_bytes().replace(
        b"298.15\t20.786", b"298.1520.786", 1
    )
    document = _parse_raw_document("Al-006", payload)
    generated = generator.generate_table(document)
    refused = generated.report["refused_layout_rows"]
    assert refused
    assert any("298.1520.786" in str(row["raw_text"]) for row in refused)
    assert "298.15" not in _cp_temperatures(generated)


def test_delimiter_loss_is_refused_for_integer_and_decimal_rows() -> None:
    red_before_fix = []
    for table_id in _DELIMITER_SAMPLE_TABLES:
        payload = _raw_table_path(table_id).read_bytes()
        text = payload.decode("utf-8")
        control = generator.generate_table(_parse_raw_document(table_id, payload))
        assert control.report["refused_layout_rows"] == []
        assert all(
            temperature in _cp_temperatures(control)
            for temperature in _DELIMITER_SAMPLE_TEMPERATURES
            if f"{temperature}\t" in text
        )
        for temperature in _DELIMITER_SAMPLE_TEMPERATURES:
            if f"{temperature}\t" not in text:
                continue
            line_index, line = _row_for_temperature(text, temperature)
            tab_positions = [match.start() for match in re.finditer("\t", line)]
            assert tab_positions
            trailing_empty = line.endswith("\t") or "\t\t" in line
            for tab_index, position in enumerate(tab_positions):
                lines = text.splitlines()
                lines[line_index] = line[:position] + line[position + 1 :]
                mutated = ("\n".join(lines) + "\n").encode("utf-8")
                assert mutated != payload
                generated = generator.generate_table(
                    _parse_raw_document(table_id, mutated)
                )
                refused = generated.report["refused_layout_rows"]
                assert refused, (
                    f"{table_id} {temperature} K tab {tab_index} was accepted"
                )
                expected_reason = _expected_delimiter_reason(
                    table_id, temperature, tab_index
                )
                reasons = [row.get("reason") for row in refused]
                assert expected_reason in reasons, (
                    f"{table_id} {temperature} K tab {tab_index}: "
                    f"expected {expected_reason!r}, got {reasons!r}"
                )
                assert GRID_RANGE_REASON not in reasons, (
                    f"{table_id} {temperature} K tab {tab_index} "
                    f"refused as grid, not layout"
                )
                assert any(
                    row.get("raw_text") == lines[line_index]
                    or str(row.get("raw_text", "")).startswith(lines[line_index][:20])
                    for row in refused
                )
                temperatures = _cp_temperatures(generated)
                assert temperature not in temperatures
                if table_id == "Al-006" and temperature in {"100", "200"}:
                    red_before_fix.append(
                        f"{table_id} {temperature} K tab {tab_index} "
                        f"{'trailing-empty row' if trailing_empty else 'full row'}"
                    )
    assert red_before_fix


def test_printed_temperature_set_is_per_table() -> None:
    b133 = [str(temperature) for temperature in range(0, 3001, 100)] + [
        "298.15",
        "4000",
    ]
    printed = table_printed_temperatures(b133)
    assert Decimal("3000") in printed
    assert Decimal("298.15") in printed
    assert Decimal("4000") not in printed
    ta003 = [str(temperature) for temperature in range(0, 5601, 100)] + [
        "298.15",
        "6000",
    ]
    assert Decimal("6000") in table_printed_temperatures(ta003)
    with_50k = [str(temperature) for temperature in range(0, 1001, 100)] + [
        "250",
        "350",
        "450",
        "298.15",
    ]
    fifty = table_printed_temperatures(with_50k)
    assert Decimal("250") in fifty
    assert Decimal("450") in fifty


def test_off_grid_temperature_with_intact_layout_is_refused() -> None:
    table_id = "B-133"
    payload = _require_raw_table(table_id).read_bytes()
    control = generator.generate_table(_parse_raw_document(table_id, payload))
    assert control.report["refused_layout_rows"] == []
    assert "3000" in _cp_temperatures(control)
    text = payload.decode("utf-8")
    line_index, line = _row_for_temperature(text, "3000")
    rest = line.split("\t", 1)[1]
    lines = text.splitlines()
    lines[line_index] = "4000\t" + rest
    mutated = ("\n".join(lines) + "\n").encode("utf-8")
    generated = generator.generate_table(_parse_raw_document(table_id, mutated))
    refused = generated.report["refused_layout_rows"]
    assert refused
    assert any(
        row.get("reason") == GRID_RANGE_REASON
        and str(row.get("temperature_as_published") or row.get("raw_text", "")).startswith(
            "4000"
        )
        for row in refused
    )
    temperatures = _cp_temperatures(generated)
    assert "4000" not in temperatures
    assert "3000" not in temperatures


def _neuter_printed_set_to_all_grid_like(tokens):
    """Drop the per-table connected-component filter; keep every grid-like T."""

    from simulator.reference_data import janaf as parser

    values = []
    for token in tokens:
        parsed = parser._decimal_temperature_token(str(token))
        if parsed is not None and parser.is_printed_grid_temperature(parsed):
            values.append(parsed)
    return frozenset(values)


def test_grid_membership_is_distinct_from_field_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.reference_data.janaf.table_printed_temperatures",
        _neuter_printed_set_to_all_grid_like,
    )
    monkeypatch.setattr(generator, "table_printed_temperatures", _neuter_printed_set_to_all_grid_like)
    table_id = "B-133"
    payload = _require_raw_table(table_id).read_bytes()
    text = payload.decode("utf-8")
    line_index, line = _row_for_temperature(text, "3000")
    rest = line.split("\t", 1)[1]
    lines = text.splitlines()
    lines[line_index] = "4000\t" + rest
    mutated = ("\n".join(lines) + "\n").encode("utf-8")
    generated = generator.generate_table(_parse_raw_document(table_id, mutated))
    assert generated.report["refused_layout_rows"] == []
    assert "4000" in _cp_temperatures(generated)

    field_payload = _require_raw_table("Al-001").read_bytes()
    field_text = field_payload.decode("utf-8")
    line_index, line = _row_for_temperature(field_text, "100")
    tab_positions = [match.start() for match in re.finditer("\t", line)]
    field_lines = field_text.splitlines()
    field_lines[line_index] = line[: tab_positions[0]] + line[tab_positions[0] + 1 :]
    field_mutated = ("\n".join(field_lines) + "\n").encode("utf-8")
    field_generated = generator.generate_table(
        _parse_raw_document("Al-001", field_mutated)
    )
    field_reasons = [row.get("reason") for row in field_generated.report["refused_layout_rows"]]
    assert _FIELD_COUNT_REASON in field_reasons
    assert "100" not in _cp_temperatures(field_generated)


def test_non_data_marker_lines_are_recorded() -> None:
    observed: list[tuple[str, int]] = []
    for table_id, line_numbers in _NON_DATA_MARKER_LINES:
        parsed = parse_janaf_txt(
            _require_raw_table(table_id).read_bytes(),
            table_id=table_id,
            url=f"https://janaf.nist.gov/tables/{table_id}.html",
            download_url=f"https://janaf.nist.gov/tables/{table_id}.txt",
        )
        markers = [
            item
            for item in parsed.parse_ambiguities
            if item.get("kind") == NON_DATA_MARKER_KIND
        ]
        assert [item["line_number"] for item in markers] == list(line_numbers)
        generated = generator.generate_table(
            _parse_raw_document(table_id, _require_raw_table(table_id).read_bytes())
        )
        reported = generated.report["non_data_marker_lines"]
        assert [row["line_number"] for row in reported] == list(line_numbers)
        assert all(row["raw_text"].strip() for row in reported)
        observed.extend((table_id, line_number) for line_number in line_numbers)
    assert len(observed) == 29


def test_b133_300k_is_a_documented_source_disagreement() -> None:
    generated = _generation("B-133")
    failures = generated.report["stored_pair_identity_failures"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["temperature_as_published"] == "300"
    assert failure["kind"] == "source_disagreement"
    assert "repeats the 298.15 K log Kf" in failure["reason"]
    evidence = failure["quoted_evidence"]
    assert evidence["298.15"]["log10_Kf"] == evidence["300"]["log10_Kf"] == "966.926"
    assert evidence["298.15"]["delta_fG"] != evidence["300"]["delta_fG"]
    assert _series_has_temperature(generated, Quantity.LOG10_KF, "300")
    assert generated.report["source_disagreements"] == [
        generator.SOURCE_DISAGREEMENTS[0]
    ]


def test_stored_pair_identity_reports_its_denominator() -> None:
    al006 = _generation("Al-006")
    assert al006.report["stored_pair_identity_denominator"] == {
        "eligible_delta_fG_T_gt_0": 61,
        "eligible_log10_Kf_T_gt_0": 61,
        "checked_intersection": 61,
        "passed": 61,
        "failed": 0,
    }
    b133 = _generation("B-133")
    assert b133.report["stored_pair_identity_denominator"] == {
        "eligible_delta_fG_T_gt_0": 28,
        "eligible_log10_Kf_T_gt_0": 28,
        "checked_intersection": 28,
        "passed": 27,
        "failed": 1,
    }


def test_stored_pair_identity_reads_the_emitted_series() -> None:
    generated = _generation("Al-006")
    assert generated.report["stored_pair_identity_failures"] == []
    log_k = _quantity_observations(generated, Quantity.LOG10_KF)[0]
    series = list(log_k.value.series or ())
    index, (temperature, value) = next(
        (i, point)
        for i, point in enumerate(series)
        if point[0] == Decimal("298.15")
    )
    series[index] = (temperature, -value)
    mutated = replace(
        generated,
        observations=tuple(
            replace(log_k, value=replace(log_k.value, series=tuple(series)))
            if observation is log_k
            else observation
            for observation in generated.observations
        ),
    )
    failures = generator._stored_pair_identity_failures_from_observations(
        mutated.observations, "Al-006"
    )
    assert failures
    assert any(
        row["temperature_as_published"] in {"298.15", "298.150"}
        and row["printed"].startswith("-") is False
        for row in failures
    )


def _al006_logk_298_document(mutate):
    document = deepcopy(load_table_document(TABLES_DIR / "Al-006.yaml"))
    for row in document["table"]["values"]:
        if row["temperature"]["as_published"] != "298.15":
            continue
        cell = row["log10_formation_equilibrium_constant"]
        original = Decimal(cell["as_published"])
        mutated = mutate(original)
        cell["as_published"] = format(mutated, "f")
        cell["value"] = float(mutated)
        return document, original, mutated
    raise AssertionError("Al-006 298.15 K log Kf cell missing")


def _series_has_temperature(generated: generator.TableGeneration, quantity: Quantity, temperature: str) -> bool:
    target = Decimal(temperature)
    return any(point[0] == target for point in _series(generated, quantity))


def test_seeded_10x_residual_is_refused_and_unmutated_control_stores() -> None:
    control = _generation("Al-006")
    assert _series_has_temperature(control, Quantity.LOG10_KF, "298.15")
    assert _series_has_temperature(control, Quantity.DELTA_FG, "298.15")
    assert control.report["scale_error_refusals"] == []
    document, original, mutated = _al006_logk_298_document(lambda value: value * 10)
    assert mutated == original * 10
    generated = generator.generate_table(document)
    assert generated.report["scale_error_refusals"]
    assert all(
        row["reason"] == generator.SCALE_ERROR_REFUSAL_REASON
        for row in generated.report["scale_error_refusals"]
    )
    assert any(
        row["temperature_as_published"] in {"298.15", "298.150"}
        for row in generated.report["scale_error_refusals"]
    )
    assert not _series_has_temperature(generated, Quantity.LOG10_KF, "298.15")
    assert not _series_has_temperature(generated, Quantity.DELTA_FG, "298.15")
    assert _series_has_temperature(generated, Quantity.DELTA_FH, "298.15")


def test_seeded_dropped_minus_is_refused_and_unmutated_control_stores() -> None:
    control = _generation("Al-006")
    assert _series_has_temperature(control, Quantity.LOG10_KF, "298.15")
    document, original, mutated = _al006_logk_298_document(lambda value: -value)
    assert mutated == -original
    generated = generator.generate_table(document)
    assert generated.report["scale_error_refusals"]
    assert all(
        row["reason"] == generator.SCALE_ERROR_REFUSAL_REASON
        for row in generated.report["scale_error_refusals"]
    )
    assert not _series_has_temperature(generated, Quantity.LOG10_KF, "298.15")
    assert not _series_has_temperature(generated, Quantity.DELTA_FG, "298.15")
    assert control.report["neighbour_sign_scan"]["status"] == "advisory-only"
    assert "SCALE_ERROR_REFUSAL_REASON" in control.report["neighbour_sign_scan"]["caught_by"]
    assert control.report["gibbs_identity_scan"]["status"] == "advisory-only"


def test_concatenated_labelled_row_requires_a_concatenated_transition_companion() -> None:
    document = load_table_document(TABLES_DIR / "C-083.yaml")
    companion = next(
        row
        for row in document["table"]["parse_ambiguities"]
        if row.get("line_number") == 9
    )
    companion["raw_line"] = (
        "288.500\t1548.129\t111.063\t118.520\t-2.151\tTRANSITION"
    )
    generated = generator.generate_table(document)
    assert generated.report["recovered_concatenated_rows"] == []
    assert len(generated.report["refused_concatenated_rows"]) == 2


def _load_janaf_store_observations() -> list[dict]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    observations: list[dict] = []
    paths = iter_observation_store_paths(CURRENT_STORE_DIR, CURRENT_STORE_PATTERN)
    assert paths, "JANAF observation store is missing"
    for path in paths:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}
        observations.extend(payload.get("observations") or [])
    return observations


def test_full_corpus_control_cell_accounting_and_transcription_report() -> None:
    expected: dict[tuple[str, Quantity], list[tuple[Decimal, Decimal]]] = {}
    for observation in _load_janaf_store_observations():
        quantity_text = observation["identity"]["quantity"]["value"]
        if quantity_text not in {Quantity.DELTA_FG.value, Quantity.LOG10_KF.value}:
            continue
        key = (observation["locator"]["table"], Quantity(quantity_text))
        series = (observation.get("value") or {}).get("series") or ()
        expected.setdefault(key, []).extend(
            (Decimal(temperature), Decimal(value)) for temperature, value in series
        )

    points = Counter()
    observations = Counter()
    phases = Counter()
    polymorphs = Counter()
    cells: dict[str, Counter] = defaultdict(Counter)
    raw_numeric_accounting = Counter()
    transcription_checks = Counter()
    failures = Counter()
    refused_merged_pair_checks = 0
    stored_cell_errata = 0
    recovered_concatenated_rows = 0
    refused_concatenated_rows = 0
    refused_layout_rows = 0
    scale_error_refusals = 0
    non_data_marker_lines = 0
    merged_rows = 0
    merged_extras = Counter()
    stored_pair_violations: list[tuple[str, str]] = []
    refused_merged_pair_violations: list[tuple[str, str]] = []
    reported_stored_pair_violations: list[tuple[str, str]] = []
    reported_refused_pair_violations: list[tuple[str, str]] = []
    stored_pair_denominator: Counter[str] = Counter()
    stored_nonzero_merged_cells = 0
    stored_zero_merged_cells = 0
    refused_merged_cells = 0
    merged_disposition_mismatches = 0
    segment_control_mismatches = 0
    store_series_mismatches = 0
    seen_control: set[tuple[str, Quantity]] = set()
    started = time.monotonic()
    paths = list(iter_table_paths())
    assert len(paths) == 1655
    for index, path in enumerate(paths, start=1):
        document = load_table_document(path)
        generated = generator.generate_table(document)
        table_id = generated.report["table_id"]
        structured_points: dict[Quantity, Counter[tuple[Decimal, Decimal]]] = {
            quantity: Counter() for quantity in _FORMATION_COLUMNS
        }
        for row in document["table"]["values"]:
            temperature = Decimal(row["temperature"]["as_published"])
            for quantity, column in _FORMATION_COLUMNS.items():
                cell = row[column]
                if cell["value"] is not None:
                    structured_points[quantity][
                        (temperature, Decimal(cell["as_published"]))
                    ] += 1
            delta_fG = row["formation_gibbs_energy"]
            log10_Kf = row["log10_formation_equilibrium_constant"]
            if (
                temperature > 0
                and delta_fG["value"] is not None
                and log10_Kf["value"] is not None
                and _logk_pair_violates_printed_rounding(
                    row["temperature"]["as_published"],
                    delta_fG["as_published"],
                    log10_Kf["as_published"],
                )
            ):
                stored_pair_violations.append(
                    (table_id, row["temperature"]["as_published"])
                )

        emitted_merged: dict[Quantity, Counter[tuple[Decimal, Decimal]]] = {}
        for quantity in _FORMATION_COLUMNS:
            emitted = Counter(_series(generated, quantity))
            emitted.subtract(structured_points[quantity])
            emitted_merged[quantity] = +emitted
        report_rows = {
            row["line_number"]: row
            for row in generated.report["merged_formation_rows"]
        }
        for ambiguity in document["table"].get("parse_ambiguities", []):
            fields = str(ambiguity.get("raw_line", "")).split("\t")
            if len(fields) != 6:
                continue
            tokens = fields[5].split()
            if len(tokens) != 3:
                continue
            try:
                values = [Decimal(token) for token in tokens]
            except InvalidOperation:
                continue
            emitted_cells: list[bool] = []
            for (quantity, _column), value in zip(
                _FORMATION_COLUMNS.items(), values, strict=True
            ):
                point = (Decimal(fields[0]), value)
                emitted = emitted_merged[quantity][point] > 0
                emitted_cells.append(emitted)
                if emitted:
                    emitted_merged[quantity][point] -= 1
                if value != 0 and emitted:
                    stored_nonzero_merged_cells += 1
            if (
                Decimal(fields[0]) > 0
                and _logk_pair_violates_printed_rounding(
                    fields[0], tokens[1], tokens[2]
                )
            ):
                target = (
                    stored_pair_violations
                    if emitted_cells[1] and emitted_cells[2]
                    else refused_merged_pair_violations
                )
                target.append((table_id, fields[0]))
            reported = report_rows[ambiguity["line_number"]]
            for column, token in zip(_FORMATION_COLUMNS.values(), tokens, strict=True):
                expected_disposition = (
                    "stored_zero" if Decimal(token) == 0 else "refused"
                )
                actual_disposition = (
                    reported.get("cell_dispositions", {})
                    .get(column, {})
                    .get("disposition")
                )
                if actual_disposition != expected_disposition:
                    merged_disposition_mismatches += 1
                if actual_disposition == "stored_zero":
                    stored_zero_merged_cells += 1
                elif actual_disposition == "refused":
                    refused_merged_cells += 1

        stored_pair_denominator.update(
            generated.report.get("stored_pair_identity_denominator") or {}
        )
        reported_stored_pair_violations.extend(
            (row["table_id"], row["temperature_as_published"])
            for row in generated.report.get("stored_pair_identity_failures", [])
        )
        reported_refused_pair_violations.extend(
            (row["table_id"], row["temperature_as_published"])
            for row in generated.report.get(
                "refused_merged_pair_identity_failures", []
            )
        )
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
                if list(observation.value.series or ()) != source_series:
                    segment_control_mismatches += 1
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
            stored = Counter(expected.get(key, ()))
            if actual != stored:
                store_series_mismatches += 1
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
            polymorph = segment["polymorph"]
            polymorphs[
                polymorph.get("value", polymorph["tag"])
                if polymorph["tag"] == "value"
                else polymorph["tag"]
            ] += 1
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
        transcription_checks.update(generated.report["transcription_checks"])
        refused_merged_pair_checks += generated.report.get(
            "refused_merged_pair_checks", 0
        )
        stored_cell_errata += len(generated.report.get("stored_cell_errata", []))
        raw_numeric_accounting.update(generated.report["raw_numeric_accounting"])
        recovered_concatenated_rows += len(
            generated.report["recovered_concatenated_rows"]
        )
        refused_concatenated_rows += len(
            generated.report["refused_concatenated_rows"]
        )
        refused_layout_rows += len(generated.report.get("refused_layout_rows") or ())
        scale_error_refusals += len(generated.report.get("scale_error_refusals") or ())
        non_data_marker_lines += len(
            generated.report.get("non_data_marker_lines") or ()
        )
        if index % 100 == 0:
            print(
                f"JANAF test audit: {index}/1655 tables in "
                f"{time.monotonic() - started:.1f}s",
                flush=True,
            )

    assert stored_nonzero_merged_cells == 0, (
        f"stored {stored_nonzero_merged_cells} sign-ambiguous merged cells"
    )
    assert stored_pair_violations == [("B-133", "300")], (
        f"expected one stored-pair identity failure, got "
        f"{len(stored_pair_violations)} "
        f"({len(stored_pair_violations) - 1} excess)"
    )
    assert reported_stored_pair_violations == stored_pair_violations
    assert len(refused_merged_pair_violations) == 437
    assert reported_refused_pair_violations == refused_merged_pair_violations
    assert merged_disposition_mismatches == 0
    assert segment_control_mismatches == 0
    assert store_series_mismatches == 0
    assert seen_control == set(expected)
    assert points[Quantity.CP.value] == 77542
    assert points[Quantity.S.value] == 77540
    assert points[Quantity.H_MINUS_H298.value] == 77539
    assert points[Quantity.DELTA_FH.value] == 74914
    assert points[Quantity.DELTA_FG.value] == 74755
    assert points[Quantity.LOG10_KF.value] == 73775
    assert points[Quantity.TRANSITION_TEMPERATURE.value] == 979
    assert merged_rows == 508
    assert merged_extras == {"delta_fG": 71, "log10_Kf": 71}
    assert {
        column: row["structured_numeric"] for column, row in cells.items()
    } == STRUCTURED_NUMERIC_COUNTS
    assert all(row["unexplained_numeric"] == 0 for row in cells.values())
    assert cells["heat_capacity"]["stored_points"] == 77542
    assert cells["entropy"]["stored_points"] == 77540
    assert cells["enthalpy_increment"]["stored_points"] == 77539
    assert cells["formation_enthalpy"]["stored_points"] == 74914
    assert cells["formation_gibbs_energy"]["stored_points"] == 74755
    assert cells["log10_formation_equilibrium_constant"]["stored_points"] == 73775
    assert cells["formation_enthalpy"]["excluded_numeric_total"] == 438
    assert cells["formation_gibbs_energy"]["excluded_numeric_total"] == 437
    assert cells["log10_formation_equilibrium_constant"]["excluded_numeric_total"] == 437
    # 508 merged rows × 3 formation columns = 1524 cells.
    # 438 + 437 + 437 = 1312 sign-ambiguous nonzero tokens stay refused.
    # 1524 − 1312 = 212 exact zeros stay stored.
    assert (
        cells["formation_enthalpy"]["excluded_numeric_total"]
        + cells["formation_gibbs_energy"]["excluded_numeric_total"]
        + cells["log10_formation_equilibrium_constant"]["excluded_numeric_total"]
    ) == 1312
    assert stored_zero_merged_cells == 212
    assert refused_merged_cells == 1312
    assert observations == {
        "cp": 2117,
        "S": 2117,
        "H_minus_H298": 2117,
        "delta_fH": 2117,
        "delta_fG": 2117,
        "log10_Kf": 2117,
        "transition_temperature": 979,
    }
    # 128 liquid tables store a printed GLASS <--> LIQUID/LIQ row. Those series
    # are not stamped l (one phase axis cannot hold glass and liquid).
    assert phases == {"cr": 796, "l": 316, "g": 874, "unknown": 131}
    assert polymorphs == {
        "unknown": 415,
        "not_applicable": 1320,
        "alpha": 99,
        "beta": 99,
        "gamma": 22,
        "delta": 11,
        "i": 54,
        "ii": 52,
        "iii": 18,
        "iv": 2,
        "v": 2,
        "kappa": 1,
        "corundum": 1,
        "andalusite": 1,
        "kyanite": 1,
        "sillimanite": 1,
        "mullite": 1,
        "beta_rhombohedral": 1,
        "wustite": 1,
        "marcasite": 1,
        "pyrite": 1,
        "magnetite": 1,
        "cristobalite_high": 1,
        "cristobalite_low": 1,
        "anatase": 1,
        "rutile": 1,
        "red_iv": 1,
        "red_v": 1,
        "black": 1,
        "monohydrate": 1,
        "dihydrate": 1,
        "trihydrate": 1,
        "tetrahydrate": 1,
        "hemihexahydrate": 1,
    }
    assert transcription_checks == {
        "negative_gibbs_enthalpy_function": 76293,
        "log10_Kf_from_delta_fG": 73668,
    }
    assert dict(stored_pair_denominator) == {
        "eligible_delta_fG_T_gt_0": 73668,
        "eligible_log10_Kf_T_gt_0": 73668,
        "checked_intersection": 73668,
        "passed": 73667,
        "failed": 1,
    }
    assert refused_merged_pair_checks == 437
    assert stored_cell_errata == 1
    assert raw_numeric_accounting == {
        "numeric_source_tokens": 533672,
        "accounted_numeric_cells": 533672,
        "refused_concatenated_numeric_tokens": 0,
        "refused_layout_numeric_tokens": 0,
        "unexplained_numeric_tokens": 0,
    }
    assert recovered_concatenated_rows == 4
    assert refused_concatenated_rows == 0
    assert refused_layout_rows == 0
    assert scale_error_refusals == 0
    assert non_data_marker_lines == 29
    assert failures == {
        "negative_gibbs_enthalpy_function": 18,
        "log10_Kf_from_delta_fG": 1,
    }


def test_janaf_lift_is_the_generator() -> None:
    src = (ROOT / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    assert "def _lift_janaf_table" not in src
    assert "def _lift_janaf_from_generator" in src
    assert "generate_table" in src


def test_janaf_store_is_element_sharded() -> None:
    single = CURRENT_STORE_DIR / "compilations-janaf.yaml"
    shard_dir = CURRENT_STORE_DIR / "compilations-janaf"
    assert not single.exists()
    assert shard_dir.is_dir()
    paths = iter_observation_store_paths(CURRENT_STORE_DIR, CURRENT_STORE_PATTERN)
    assert paths
    assert all(path.parent.name == "compilations-janaf" for path in paths)
    assert all(
        path.name.startswith("janaf-") and path.name.endswith(".yaml") for path in paths
    )
    # 2117 segments × 6 tabulated quantities + 979 transitions = 13681
    n = compilation_shard_observation_count(ROOT, paths, CURRENT_STORE_DIR)
    assert n == 13681


def test_janaf_store_keeps_circularity_and_compilation_class() -> None:
    observations = _load_janaf_store_observations()
    assert len(observations) == 13681
    warning = generator.CIRCULARITY_WARNING
    for observation in observations:
        evidence = (observation.get("evidence") or {}).get("class") or {}
        assert evidence.get("value") == "compilation_assessed"
        relation = (observation.get("derivation") or {}).get("relation") or ""
        assert "scoring_eligible=false" in relation
        assert warning in relation

def test_janaf_raw_table_gate_skips_without_private_corpus(tmp_path, monkeypatch) -> None:
    """Mutation proof: missing private fallthrough must skip, not FileNotFoundError."""
    import tests.battery.test_janaf_generator as mod

    monkeypatch.setattr(mod, "_JANAF_SOURCE_DIR", tmp_path / "janaf-nist-txt")
    # Ensure no fixture shadows the missing private table.
    assert not (mod.RAW_FIXTURES / "Ba-001.txt").is_file()
    with pytest.raises(pytest.skip.Exception):
        mod._require_raw_table("Ba-001")
