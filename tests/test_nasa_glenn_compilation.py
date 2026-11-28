"""NASA Glenn / CEA thermo.inp compilation ingest.

Round-trip: every manifest record reloads to the published numbers in
``source/thermo.inp``. Coverage: which ``data/feedstocks.yaml`` elements
have at least one record. Compilations are reference functions, not
validation measurements.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.reference_data.nasa_glenn import (
    COMPILATION_ROOT,
    NASA7_COEFF_FIELD_WIDTH,
    NASA9_COEFF_FIELD_WIDTH,
    SOURCE_REL,
    coverage_by_element,
    feedstock_element_symbols,
    is_nasa7_four_line_record,
    iter_record_paths,
    load_all_record_documents,
    load_manifest,
    parse_coeff_fields,
    parse_nasa7_coefficient_lines,
    parse_nasa7_header_line,
    parse_thermo_inp,
    published_float_pairs,
)
from simulator.reference_data import nasa_glenn

ROOT = Path(__file__).resolve().parents[1]
SOURCE = COMPILATION_ROOT / "source" / "thermo.inp"
EXTRACT = ROOT / "data" / "literature" / "extracts" / "nasa-cea-thermo.yaml"
MAJOR_FEEDSTOCK_ELEMENTS = ("Si", "Fe", "O", "Al", "Mg", "Ca", "Ti", "Na", "K")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def record_docs():
    return load_all_record_documents()


@pytest.fixture(scope="module")
def source_records():
    text = SOURCE.read_text(encoding="latin-1")
    return parse_thermo_inp(text, source_path=SOURCE_REL).records


def test_record_formula_weight_witness_accepts_air_and_refuses_inertair(
    source_records, monkeypatch
) -> None:
    from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, parse_formula

    records = {record.record_id: record for record in source_records}
    air = records["NG-2031"]
    inert_air = records["NG-2032"]

    def parse_record(record):
        return nasa_glenn.accepted_formula(record)

    air_formula, air_notice = parse_record(air)
    assert air_formula is not None
    assert air_notice is None
    assert dict(air_formula.elements) == {
        "Ar": 0.00937,
        "C": 0.00032,
        "N": 1.5617,
        "O": 0.41959,
    }
    assert round(air.molecular_weight.value, 4) == 28.9651
    air_tolerance, source_weight_tolerance = nasa_glenn._record_formula_mass_tolerance(
        air.composition_slots,
        air.molecular_weight,
        air_formula,
        parse_formula,
        ATOMIC_WEIGHTS_G_PER_MOL,
        nasa_glenn._nasa_glenn_atomic_weights(),
    )
    assert source_weight_tolerance > 0
    assert abs(air_formula.molar_mass_g_mol - air.molecular_weight.value) <= air_tolerance

    def assert_inert_air_refused() -> None:
        parsed, refusal = parse_record(inert_air)
        assert parsed is None
        assert refusal is not None
        assert refusal["kind"] == "formula_source_composition_disagreement"
        assert refusal["disposition"] == "refusal"
        assert refusal["identity_refusal"] is True
        assert refusal["formula_as_published"] == inert_air.formula
        assert refusal["printed_molecular_weight_g_mol"] == 28.9651159
        assert refusal["parsed_molecular_weight_g_mol"] == pytest.approx(155.87037859)
        assert refusal["difference_g_mol"] > refusal["precision_tolerance_g_mol"]
        assert any(
            item.get("kind") == "formula_source_composition_disagreement"
            for item in inert_air.ambiguities
        )

    assert_inert_air_refused()

    no_weight_record = replace(
        air,
        molecular_weight=nasa_glenn.PublishedNumber(as_published="", value=None),
    )
    no_weight, no_weight_notice = nasa_glenn.accepted_formula(no_weight_record)
    assert no_weight_notice is None
    assert no_weight is not None
    assert dict(no_weight.elements) == dict(air_formula.elements)

    with monkeypatch.context() as mutation:
        mutation.setattr(
            nasa_glenn,
            "accepted_formula",
            lambda record: (parse_formula(record.formula), None),
        )
        with pytest.raises(AssertionError):
            assert_inert_air_refused()


def test_record_formula_weight_witness_census(source_records, capsys) -> None:
    refusals = []
    for record in source_records:
        parsed, diagnostic = nasa_glenn.accepted_formula(record)
        if diagnostic is None or diagnostic.get("identity_refusal") is not True:
            continue
        row = diagnostic
        assert parsed is None
        assert row["difference_g_mol"] > row["precision_tolerance_g_mol"]
        refusals.append(
            {
                "source": "nasa-glenn",
                "record_id": record.record_id,
                "name": record.name_as_published,
                "formula": row["formula_as_published"],
                "printed_weight_g_mol": row["printed_molecular_weight_g_mol"],
                "parsed_weight_g_mol": row["parsed_molecular_weight_g_mol"],
                "difference_g_mol": row["difference_g_mol"],
                "precision_tolerance_g_mol": row["precision_tolerance_g_mol"],
            }
        )
    expected = [
        ("NG-0297", "InertCH4", "ICIH4", 16.04246, 269.85194),
        ("NG-0363", "InertC2H4", "IC2IH4", 28.05316, 281.86294),
        ("NG-0459", "InertC10H8,naph", "IC10IH8", 128.17052, 381.98294),
        ("NG-0635", "InertH", "IH", 1.00794, 127.91247),
        ("NG-0670", "InertH2", "IH2", 2.01588, 128.92047),
        ("NG-0929", "InertO", "IO", 15.9994, 142.90347),
        ("NG-0938", "InertO2", "IO2", 31.9988, 158.90247),
        (
            "NG-2032",
            "InertAir",
            "N1.5617IO.41959Ar.00937C.00032",
            28.9651159,
            155.87037859,
        ),
        ("NG-2070", "InertH2(L)", "IH2", 2.01588, 128.92047),
        ("NG-2074", "InertJP-4", "ICIH1.94", 13.9661036, 267.77546),
        ("NG-2076", "InertJP-5", "ICIH1.92", 13.9459448, 267.7553),
        ("NG-2078", "InertJP-7", "ICIH2.0044", 14.03102, 267.8403752),
        ("NG-2080", "InertJP-10(L)", "IC10IH16", 136.23404, 390.04694),
        ("NG-2082", "InertJP-10(g)", "IC10IH16", 136.23404, 390.04694),
        ("NG-2084", "InertJet-A(L)", "IC12IH23", 167.31102, 421.12494000000004),
        ("NG-2086", "InertJet-A(g)", "IC12IH23", 167.31102, 421.12494000000004),
        ("NG-2101", "InertO2(L)", "IO2", 31.9988, 158.90247),
        ("NG-2104", "InertRP-1", "ICIH1.95", 13.976183, 267.78554),
    ]
    actual_identity = [
        (
            row["record_id"],
            row["name"],
            row["formula"],
            row["printed_weight_g_mol"],
        )
        for row in refusals
    ]
    assert actual_identity == [row[:4] for row in expected], json.dumps(
        refusals, sort_keys=True
    )
    assert len(refusals) == 18
    for row, expected_row in zip(refusals, expected, strict=True):
        assert row["parsed_weight_g_mol"] == pytest.approx(expected_row[4])
    adn = next(record for record in source_records if record.record_id == "NG-2106")
    adn_formula, adn_notice = nasa_glenn.accepted_formula(adn)
    assert adn_formula is not None
    assert dict(adn_formula.elements) == {"H": 4.0, "N": 4.0, "O": 4.0}
    assert adn_notice is not None
    assert adn_notice["kind"] == "formula_molecular_weight_conflict"
    assert adn_notice["disposition"] == "notice"
    assert adn_notice["identity_refusal"] is False
    assert adn_notice["printed_molecular_weight_g_mol"] == 630.0
    assert adn_notice["parsed_molecular_weight_g_mol"] == pytest.approx(124.056)
    assert adn_notice["difference_g_mol"] > adn_notice["precision_tolerance_g_mol"]
    adn_document, bound_adn, bound_adn_notice = (
        nasa_glenn.accepted_observation_formula(
            "nasa-glenn:NG-2106",
            (
                "data/literature/compilations/nasa-glenn/records/NG-2106.json",
                "compilation_record:data/literature/compilations/nasa-glenn/records/NG-2106.json",
            ),
            adn.formula,
        )
    )
    assert adn_document is not None
    assert bound_adn is not None
    assert bound_adn_notice is not None
    assert bound_adn_notice["kind"] == "formula_molecular_weight_conflict"
    assert bound_adn_notice["identity_refusal"] is False
    air_document, air_formula, air_notice = nasa_glenn.accepted_observation_formula(
        "nasa-glenn:NG-2031",
        ("data/literature/compilations/nasa-glenn/records/NG-2031.json",),
        nasa_glenn.load_record_document_by_id("NG-2031")["formula"],
    )
    assert air_document is not None
    assert air_formula is not None
    assert air_notice is None
    with capsys.disabled():
        print("NASA_GLENN_FORMULA_WEIGHT_CENSUS " + json.dumps(refusals, sort_keys=True))
        print("NASA_GLENN_FORMULA_WEIGHT_NOTICES " + json.dumps([adn_notice], sort_keys=True))


@pytest.mark.parametrize(
    ("record_id", "valid"),
    (("NG-2032", True), ("ng-2032", False), ("NG-203", False), ("NG-20320", False)),
)
def test_nasa_glenn_record_id_grammar(record_id: str, valid: bool) -> None:
    assert nasa_glenn._is_nasa_glenn_record_id(record_id) is valid


def test_formula_rounding_bound_uses_heavier_source_atomic_weight() -> None:
    from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, parse_formula

    printed_count = 2.0
    actual_count = 2.00499999
    source_weight = nasa_glenn._nasa_glenn_atomic_weights()["O"]
    repository_weight = ATOMIC_WEIGHTS_G_PER_MOL["O"]
    printed_header = 32.0787968
    slot = nasa_glenn.CompositionSlot("O", "2.00", "O", printed_count, False)
    tolerance, _source_weight_tolerance = nasa_glenn._record_formula_mass_tolerance(
        [slot],
        nasa_glenn.PublishedNumber("32.0787968", printed_header),
        parse_formula("O2.00"),
        parse_formula,
        ATOMIC_WEIGHTS_G_PER_MOL,
        {"O": source_weight},
    )

    # The header is the source-era mass for the unrounded count. Comparing it
    # to the printed count at the repository weight gives this lower bound.
    observed_difference = abs(printed_header - printed_count * repository_weight)
    assert source_weight > repository_weight
    assert actual_count * source_weight == pytest.approx(printed_header)
    assert observed_difference == pytest.approx(0.0807968)
    assert tolerance >= observed_difference


def test_inertair_witness_refuses_both_battery_consumers_and_mutation_is_caught(
    monkeypatch,
) -> None:
    from simulator.battery.compilation_tier import (
        _nasa_cea_9_record_for_species,
        predict_thermo_attempt,
    )
    from simulator.battery.enums import Engine, Phase, Quantity, RefusalReason
    from simulator.battery.score import _parse_observation_species_formula
    from simulator.battery.records import Species, State
    from tests.battery import factories as F
    from simulator.accounting.formulas import parse_formula

    formula = "N1.5617IO.41959Ar.00937C.00032"
    identity = replace(
        F.o2_identity(),
        quantity=Quantity.CP,
        species=Species(formula, Phase.G),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observation = replace(
        F.observation(
            "nasa-glenn:NG-2032",
            "exp-1",
            identity,
            0,
            source_id="nasa-glenn",
        ),
        locator=F.loc(source_path="data/literature/compilations/nasa-glenn/records/NG-2032.json"),
    )
    species = observation.identity.species

    def assert_score_refuses() -> None:
        parsed, diagnostic = _parse_observation_species_formula(observation)
        assert parsed is None
        assert diagnostic is not None and diagnostic["identity_refusal"] is True

    def assert_nasa9_refuses() -> None:
        record, reason = _nasa_cea_9_record_for_species(species)
        assert record is None
        assert reason == "nasa-cea-source-formula-identity-mismatch"

    assert_score_refuses()
    assert_nasa9_refuses()
    attempt = predict_thermo_attempt(Engine.NASA_CEA_9, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH

    def bypass_witness(record):
        source_formula = record.get("formula") if isinstance(record, dict) else record.formula
        return parse_formula(source_formula), None

    with monkeypatch.context() as mutation:
        mutation.setattr(nasa_glenn, "accepted_formula", bypass_witness)
        with pytest.raises(AssertionError):
            assert_score_refuses()
        with pytest.raises(AssertionError):
            assert_nasa9_refuses()


def test_nasa9_observation_binding_uses_card_id_and_all_locator_claims(
    monkeypatch,
) -> None:
    from simulator.battery.compilation_tier import (
        _nasa_cea_9_record_for_species,
        predict_thermo_attempt,
    )
    from simulator.battery.enums import Engine, Phase, Quantity, RefusalReason
    from simulator.battery.score import _parse_observation_species_formula
    from simulator.battery.records import Species, State
    from tests.battery import factories as F
    from simulator.accounting.formulas import parse_formula

    air = nasa_glenn.load_record_document_by_id("NG-2031")
    assert air is not None
    identity = replace(
        F.o2_identity(),
        quantity=Quantity.CP,
        species=Species(air["formula"], Phase.G),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observation = replace(
        F.observation(
            "nasa-glenn:NG-2032",
            "exp-1",
            identity,
            0,
            source_id="nasa-glenn",
        ),
        locator=F.loc(
            source_path="data/literature/compilations/nasa-glenn/records/NG-2032.json"
        ),
        read_from=(
            "compilation_record:"
            "data/literature/compilations/nasa-glenn/records/NG-2032.json"
        ),
    )
    species = observation.identity.species

    def assert_score_refuses() -> None:
        parsed, diagnostic = _parse_observation_species_formula(observation)
        assert parsed is None
        assert diagnostic is not None
        assert diagnostic["identity_refusal"] is True

    def assert_nasa9_refuses() -> None:
        record, reason = _nasa_cea_9_record_for_species(
            species, observation=observation
        )
        assert record is None
        assert reason == "nasa-cea-source-formula-identity-mismatch"
        attempt = predict_thermo_attempt(Engine.NASA_CEA_9, observation)
        assert attempt.value is None
        assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH

    assert_score_refuses()
    assert_nasa9_refuses()

    wrong_locator = replace(
        observation,
        locator=F.loc(
            source_path="data/literature/compilations/nasa-glenn/records/NG-2031.json"
        ),
    )
    _parsed, competing_diagnostic = _parse_observation_species_formula(wrong_locator)
    assert competing_diagnostic is not None
    assert competing_diagnostic["kind"] == "formula_source_record_identity_disagreement"
    assert competing_diagnostic["identity_refusal"] is True
    wrong_locator_record, wrong_locator_reason = _nasa_cea_9_record_for_species(
        species, observation=wrong_locator
    )
    assert wrong_locator_record is None
    assert wrong_locator_reason == "nasa-cea-source-formula-identity-mismatch"
    wrong_locator_attempt = predict_thermo_attempt(
        Engine.NASA_CEA_9, wrong_locator
    )
    assert wrong_locator_attempt.value is None
    assert wrong_locator_attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH

    conflicting_claims = replace(
        observation,
        observation_id="nasa-glenn:NG-2031",
    )

    def assert_score_refuses_conflicting_claims() -> None:
        parsed, diagnostic = _parse_observation_species_formula(conflicting_claims)
        assert parsed is None
        assert diagnostic is not None
        assert diagnostic["kind"] == "formula_source_record_identity_disagreement"

    def assert_nasa9_refuses_conflicting_claims() -> None:
        record, reason = _nasa_cea_9_record_for_species(
            species, observation=conflicting_claims
        )
        assert record is None
        assert reason == "nasa-cea-source-formula-identity-mismatch"

    assert_score_refuses_conflicting_claims()
    assert_nasa9_refuses_conflicting_claims()

    from simulator.battery import records as battery_records

    with monkeypatch.context() as mutation:
        mutation.setattr(
            battery_records,
            "nasa_glenn_locator_claim_values",
            lambda _observation: (),
        )
        with pytest.raises(AssertionError):
            assert_score_refuses_conflicting_claims()
        with pytest.raises(AssertionError):
            assert_nasa9_refuses_conflicting_claims()

    def bypass_source_binding(_observation_id, _locator_values, _formula):
        return air, parse_formula(air["formula"]), None

    with monkeypatch.context() as mutation:
        mutation.setattr(
            nasa_glenn, "accepted_observation_formula", bypass_source_binding
        )
        with pytest.raises(AssertionError):
            assert_score_refuses()
        with pytest.raises(AssertionError):
            assert_nasa9_refuses()


def _source_sha256() -> str:
    digest = hashlib.sha256()
    with SOURCE.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_harvest_cli_requires_explicit_thermo_path(
    monkeypatch, capsys, tmp_path
) -> None:
    from tools import harvest_nasa_glenn_compilation as harvest

    monkeypatch.setattr("sys.argv", ["harvest_nasa_glenn_compilation.py"])
    with pytest.raises(SystemExit) as excinfo:
        harvest.cli()
    assert excinfo.value.code == 2
    assert "--thermo" in capsys.readouterr().err

    with pytest.raises(FileNotFoundError, match="--thermo"):
        harvest.resolve_thermo(None)
    with pytest.raises(FileNotFoundError, match="--thermo"):
        harvest.resolve_thermo(tmp_path / "missing-thermo.inp")


def test_source_snapshot_is_byte_identical_to_manifest_sha256(manifest) -> None:
    expected = manifest["source"]["sha256"]
    assert SOURCE.is_file()
    assert _source_sha256() == expected
    assert expected == "fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363"


def test_manifest_lists_every_record_file_and_no_orphans(manifest, record_docs) -> None:
    entries = manifest["entries"]
    assert manifest["summary"]["record_count"] == len(entries)
    files = {path.name: path for path in iter_record_paths()}
    docs = {doc["record_id"]: doc for doc in record_docs}
    assert len(files) == len(entries)
    assert set(docs) == {entry["record_id"] for entry in entries}
    for entry in entries:
        name = f"{entry['record_id']}.json"
        assert name in files
        doc = docs[entry["record_id"]]
        assert doc["name_as_published"] == entry["name_as_published"]
        assert doc.get("phase_ordinal") == entry.get("phase_ordinal")
        assert doc["compilation_role"]["scoring_eligible"] is False
        assert doc["compilation_role"]["validation_measurement"] is False
        assert doc["schema_version"] == "literature_compilation.v1"


def test_every_record_round_trips_to_thermo_inp_numbers(manifest, record_docs) -> None:
    """Null hypothesis: a harvested coefficient can drift from thermo.inp.

    Reloading each JSON and comparing source_text plus parsed values against
    a fresh fixed-column parse of the committed snapshot must stay exact.
    """
    text = SOURCE.read_text(encoding="latin-1")
    lines = text.splitlines()
    parsed = parse_thermo_inp(
        text,
        source_path=SOURCE_REL,
        source_sha256=manifest["source"]["sha256"],
    )
    assert len(parsed.records) == manifest["summary"]["record_count"]
    docs = {doc["record_id"]: doc for doc in record_docs}
    assert set(docs) == {rec.record_id for rec in parsed.records}

    mismatches: list[str] = []
    for rec in parsed.records:
        doc = docs[rec.record_id]
        name_line = lines[rec.name_line_number - 1]
        header_line = lines[rec.header_line_number - 1]
        if doc["source_text"]["name_line"] != name_line:
            mismatches.append(f"{rec.record_id} name_line")
        if doc["source_text"]["header_line"] != header_line:
            mismatches.append(f"{rec.record_id} header_line")
        expected_interval_lines: list[str] = []
        for iv in rec.intervals:
            expected_interval_lines.append(iv.raw_t_line)
            expected_interval_lines.extend(iv.raw_coeff_lines)
        if doc["source_text"]["interval_lines"] != expected_interval_lines:
            mismatches.append(f"{rec.record_id} interval_lines")
        if doc["name_as_published"] != rec.name_as_published:
            mismatches.append(f"{rec.record_id} name")
        if doc["n_intervals_declared"] != rec.n_intervals_declared:
            mismatches.append(f"{rec.record_id} n_intervals")
        if doc["coefficient_count"] != rec.coefficient_count:
            mismatches.append(f"{rec.record_id} coefficient_count")
        fresh = rec.to_dict()
        if published_float_pairs(doc) != published_float_pairs(fresh):
            mismatches.append(f"{rec.record_id} published_numbers")
        if doc["intervals"] != fresh["intervals"]:
            mismatches.append(f"{rec.record_id} published_coefficient_tokens")
        if len(mismatches) > 20:
            break
    assert mismatches == []


def test_complete_ingest_keeps_inverted_ions_reactants_and_assigned_enthalpy(
    record_docs,
) -> None:
    docs = record_docs
    by_name = {doc["name_as_published"]: [] for doc in docs}
    for doc in docs:
        by_name.setdefault(doc["name_as_published"], []).append(doc)
    br2 = by_name["Br2(cr)"][0]
    tmin = br2["intervals"][0]["T_min_K"]["value"]
    tmax = br2["intervals"][0]["T_max_K"]["value"]
    assert tmin == 300.0
    assert tmax == 265.9
    kinds = {item["kind"] for item in br2["ambiguities"]}
    assert "inverted_or_zero_width_T_interval" in kinds
    electron = by_name["e-"][0]
    assert electron["molecular_weight"]["value"] == 0.000548579903
    assert electron["phase"] == "gas"
    assert electron["phase_as_published"] == "0"
    sio2_aqz = by_name["SiO2(a-qz)"][0]
    assert sio2_aqz["phase_as_published"] == "a-qz"
    assert sio2_aqz["phase"] == "condensed"
    assert sio2_aqz["phase_ordinal"] == 1
    assert any(
        item["kind"] == "phase_suffix_normalized_to_condensed"
        for item in sio2_aqz["ambiguities"]
    )
    assert "Air" in by_name
    assert by_name["Air"][0]["cea_section"] == "reactants"
    assigned = [
        doc
        for doc in docs
        if doc["n_intervals_declared"] == 0 and doc["coefficient_count"] == 0
    ]
    assert len(assigned) == 54
    assert any(doc["name_as_published"] == "n-Butanol" for doc in assigned)


def test_feedstock_element_coverage_table(manifest, record_docs) -> None:
    elements = feedstock_element_symbols()
    assert "Si" in elements and "Fe" in elements and "O" in elements
    table = coverage_by_element(record_docs, elements)
    assert set(table) == set(elements)
    covered = [el for el, row in table.items() if row["has_record"]]
    uncovered = [el for el, row in table.items() if not row["has_record"]]
    for element in MAJOR_FEEDSTOCK_ELEMENTS:
        assert table[element]["has_record"], f"{element} has no NASA Glenn record"
        assert table[element]["record_count"] >= 1
    assert covered
    # Coverage is reported, not curated: REE/PGM feedstock elements with no
    # thermo.inp record stay listed as uncovered.
    for rare in ("Ce", "La", "Y", "Nd", "Hf"):
        assert rare in table
        assert table[rare]["has_record"] is False
        assert rare in uncovered
    assert manifest["feedstock_element_coverage"]["covered_elements"] == covered
    assert manifest["feedstock_element_coverage"]["uncovered_elements"] == uncovered


def test_held_extract_left_in_place_and_marked_incomplete(manifest) -> None:
    assert EXTRACT.is_file()
    missing = manifest["held_extract_missing_records"]
    assert len(missing) == 485
    assert sum(1 for row in missing if row["cea_section"] == "products") == 404
    assert sum(1 for row in missing if row["cea_section"] == "reactants") == 81
    names = {row["name_as_published"] for row in missing}
    assert "e-" in names
    assert "Air" in names
    assert "Br2(cr)" in names
    text = EXTRACT.read_text(encoding="utf-8")
    assert "schema_version: literature_extract.v1" in text
    assert "source_id: nasa-cea-thermo" in text


# Shared NASA-7 / NASA-9 coefficient field splitter (Burcat uses the 7-coeff form).
_NASA7_AG_SOLID = [
    "Ag (solid)        T 6/12AG 1.   0.   0.   0.S   200.000  1235.080  A 107.86820 1",
    " 2.07216824E+00 2.46393729E-03-1.34351116E-06 3.69321107E-10 0.00000000E+00    2",
    "-6.37725170E+02-7.18810718E+00 2.25225065E+00 5.43263008E-03-1.32153990E-05    3",
    " 1.50423505E-08-5.94991675E-12-8.23132027E+02-8.86835190E+00 0.00000000E+00    4",
]


def test_coeff_field_parser_serves_nasa9_and_nasa7_widths() -> None:
    """One splitter; 16-char CEA NASA-9 fields and 15-char NASA-7 fields."""
    nasa9 = " 0.000000000D+00 0.000000000D+00 2.500000000D+00 0.000000000D+00 0.000000000D+00"
    nasa9_fields = parse_coeff_fields(
        nasa9, field_width=NASA9_COEFF_FIELD_WIDTH, line_width=80
    )
    assert len(nasa9_fields) == 5
    assert nasa9_fields[2].strip() == "2.500000000D+00"
    nasa7_fields = parse_coeff_fields(
        _NASA7_AG_SOLID[1],
        field_width=NASA7_COEFF_FIELD_WIDTH,
        line_width=75,
    )
    assert len(nasa7_fields) == 5
    assert nasa7_fields[0].strip() == "2.07216824E+00"
    assert nasa7_fields[3].strip() == "3.69321107E-10"


def test_nasa7_four_line_variant_parses_burcat_header_and_fifteen_coeffs() -> None:
    assert is_nasa7_four_line_record(_NASA7_AG_SOLID, 0)
    header = parse_nasa7_header_line(_NASA7_AG_SOLID[0])
    assert header["name_as_published"] == "Ag (solid)"
    assert header["phase_as_published"] == "S"
    assert header["formula"] == "Ag"
    assert header["T_min_K"].value == 200.0
    assert header["T_max_K"].value == 1235.08
    assert header["molecular_weight"].value == 107.86820
    assert header["calc_quality_as_published"] == "A"
    coeffs, ambiguities = parse_nasa7_coefficient_lines(_NASA7_AG_SOLID[1:])
    assert ambiguities == []
    assert len(coeffs) == 15
    assert coeffs[0].as_published == "2.07216824E+00"
    assert coeffs[0].value == 2.07216824e00
    assert coeffs[6].value == -7.18810718e00
    assert coeffs[7].value == 2.25225065e00
    assert coeffs[14].value == 0.0


def test_same_formula_distinct_phase_census(record_docs) -> None:
    from collections import defaultdict

    groups = defaultdict(list)
    for doc in record_docs:
        header = doc["source_text"]["header_line"]
        flag = int(header[50:52])
        if flag == 0:
            assert doc["phase"] == "gas"
            assert "phase_ordinal" not in doc
        groups[(doc["formula"], doc["phase"])].append(doc)

    collision_groups = []
    collision_records = []
    for (formula, phase), docs in groups.items():
        flags = {doc["phase_flag"] for doc in docs}
        is_phase_collision = (
            len(docs) > 1
            and len(flags) > 1
            and all(isinstance(flag, int) and flag > 0 for flag in flags)
        )
        if is_phase_collision:
            collision_groups.append((formula, phase))
            collision_records.extend(doc["record_id"] for doc in docs)
            assert {doc["phase_ordinal"] for doc in docs} == flags
            assert len({(doc["phase"], doc["phase_ordinal"]) for doc in docs}) == len(docs)
        else:
            assert all("phase_ordinal" not in doc for doc in docs)

    assert len(collision_groups) == 14
    assert len(collision_records) == 30
    by_name = defaultdict(list)
    for doc in record_docs:
        by_name[doc["name_as_published"]].append(doc)
    assert by_name["Ag(cr)"][0]["phase"] == "cr"
    assert by_name["Ag(L)"][0]["phase"] == "L"
    assert "phase_ordinal" not in by_name["Ag(cr)"][0]
    assert [doc["phase_ordinal"] for doc in by_name["Co(b)"]] == [2, 3]
    assert [by_name[name][0]["phase_ordinal"] for name in (
        "InCL(crII)", "InCL(crI)"
    )] == [1, 2]


def test_round_trip_rejects_format_only_coefficient_mutation(manifest, record_docs) -> None:
    from copy import deepcopy

    mutated = deepcopy(record_docs)
    number = mutated[0]["intervals"][0]["a_coefficients"][0]
    assert number["as_published"] != repr(number["value"])
    number["as_published"] = repr(number["value"])
    with pytest.raises(AssertionError):
        test_every_record_round_trips_to_thermo_inp_numbers(manifest, mutated)
