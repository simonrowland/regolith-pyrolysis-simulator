"""FastChem v3.1.3 mass-action fit compilation.

Pins come from the seated coefficient files and from peer compilation
records, not from a second copy of the fit.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, ValueKind
from simulator.battery.migrate import (
    compilation_quantity_from_record,
    migrate,
    select_declared_source,
)
from simulator.battery.score import COMPILATION_SOURCE_MARKERS, is_compilation_source
from simulator.reference_data.fastchem import (
    COMPILATION_ROOT,
    CONDENSATE_SOURCE_REL,
    EXTENDED_SOURCE_REL,
    FORM_AS_PUBLISHED,
    GAS_SOURCE_REL,
    SOURCE_DIRECTION_SENTENCE,
    SOURCE_LN_SENTENCE,
    SOURCE_NOT_FORMATION_SENTENCE,
    _nasa_composition,
    coefficients_match_raw_lines,
    composition_key,
    load_manifest,
    phase_classes,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = COMPILATION_ROOT / "source"
GAS_SHA = "dbf4d53afe7b68ac9504560843b90d304f04b8570ae4ab89bc88289455b78806"
COND_SHA = "c853a104385d627a5b35e9a0409fb1163d30596a08cca5b1368b9abe37f5635f"
EXT_SHA = "9bc4f8dd1d7d4cd29c4b7b33ab7bb00bb3f16d5fe39dedea5465939532064a3d"

# Printed tokens, copied from the seated files. The test also reads those
# lines back and requires the same tokens.
GAS_ALCL = (
    "6.0172641222860926e+04",
    "-9.8218922907517481e-01",
    "-5.8077521648508537e+00",
    "1.6577446947976804e-04",
    "-6.1119306479155187e-09",
)
GAS_NIO = (
    "4.934503768542845e+04",
    "-2.931035916300681e-01",
    "-1.096582771938455e+01",
    "-3.975231365136914e-04",
    "2.578387926407946e-08",
)
GAS_ZN_ANION = (
    "1.7232196990282414e+03",
    "-2.5092835574388204e+00",
    "1.4281706067404460e+01",
    "6.8019574853347975e-06",
    "-6.6534372585404459e-10",
)
EXT_SC_CATION = (
    "-7.630457131024679e+04",
    "1.939670870002992e+00",
    "-1.029000744137492e+01",
    "5.553202048467648e-04",
    "-4.992972245981864e-08",
)
EXT_U_DICAT = (
    "-2.0649144666068928e+05",
    "5.0000000000048281e+00",
    "-2.8808736449019580e+01",
    "-3.5351949991490057e-15",
    "2.8916451813299671e-19",
)
COND_AL = (
    "3.9561473031832546e+04",
    "-8.7867278579742214e-01",
    "-1.1676031649750231e+01",
    "2.1331616828954634e-03",
    "-3.4134061704048190e-07",
)
COND_NA2SIO3_SOLID_LIMIT = (
    "3.5560099192205293e+05",
    "-1.1489121259959131e+01",
    "-3.4281841880067880e+01",
    "1.7725220996838122e-02",
    "-3.1788517585021646e-06",
)
COND_NA2SIO3_SECOND = (
    "3.4551893714376399e+05",
    "-1.0063267984644996e+01",
    "-2.9101876888158635e+01",
    "8.4807980538160570e-03",
    "-7.2910305509192245e-07",
)
COND_SIO = (
    "1.4513201260869624e+05",
    "-1.9101397325062783e+00",
    "-2.0298667076790409e+01",
    "6.1851048655818552e-04",
    "-4.2163494709844927e-08",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lines(name: str) -> list[str]:
    return (SOURCE / name).read_text(encoding="utf-8").splitlines()


def _header_count(lines: list[str]) -> int:
    return sum(
        1
        for line in lines
        if line.strip() and not line.startswith("#") and ":" in line and "#" in line
    )


def _coeff_after(lines: list[str], header_prefix: str) -> tuple[str, ...]:
    matches = [
        index
        for index, line in enumerate(lines)
        if line.startswith(header_prefix)
    ]
    assert len(matches) == 1, header_prefix
    return tuple(lines[matches[0] + 1].split())


def _by_symbol(manifest: dict) -> dict[str, dict]:
    return {entry["symbol_as_published"]: entry for entry in manifest["entries"]}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def docs(manifest):
    loaded = {}
    for entry in manifest["entries"]:
        path = ROOT / entry["path"]
        loaded[entry["record_id"]] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def test_source_bytes_match_the_sidecar_and_manifest(manifest) -> None:
    gas = SOURCE / "fastchem-v3-1-3-gas-condensate-coefficients.dat"
    condensates = SOURCE / "logK_condensates.dat"
    extended = SOURCE / "logK_extended.dat"
    assert _sha256(gas) == GAS_SHA == manifest["source"]["sha256"]
    assert _sha256(condensates) == COND_SHA == manifest["source"]["condensate_sha256"]
    assert _sha256(extended) == EXT_SHA == manifest["source"]["extended_sha256"]
    # Custody hashes of the notes and sidecar must describe the committed bytes, and the
    # sidecar's own files[] entries must match every file it lists (catches note edits
    # that forget to re-hash).
    assert _sha256(SOURCE / "SOURCE.md") == manifest["source"]["source_md_sha256"]
    assert _sha256(SOURCE / "EXTENDED-SOURCE.md") == manifest["source"]["extended_source_md_sha256"]
    assert _sha256(SOURCE / "sidecar.yaml") == manifest["source"]["sidecar_sha256"]
    sidecar = yaml.safe_load((SOURCE / "sidecar.yaml").read_text(encoding="utf-8"))
    for item in sidecar["files"]:
        listed = SOURCE / item["name"]
        if listed.exists():
            assert _sha256(listed) == item["sha256"], item["name"]
            assert listed.stat().st_size == item["bytes"], item["name"]
    assert _lines("fastchem-v3-1-3-gas-condensate-coefficients.dat")[0] == FORM_AS_PUBLISHED
    assert _lines("logK_condensates.dat")[0] == FORM_AS_PUBLISHED
    source_md = (SOURCE / "SOURCE.md").read_text(encoding="utf-8")
    assert SOURCE_LN_SENTENCE in source_md
    assert SOURCE_DIRECTION_SENTENCE in source_md
    assert SOURCE_NOT_FORMATION_SENTENCE in source_md
    readme = (COMPILATION_ROOT / "README.md").read_text(encoding="utf-8")
    assert FORM_AS_PUBLISHED in readme
    assert SOURCE_LN_SENTENCE in readme
    assert SOURCE_DIRECTION_SENTENCE in readme
    assert "1 bar = 1e-6 dyn cm-2" in readme
    assert "10^5 Pa" in readme
    assert "natural logarithm of the dimensionless mass action constants" in readme
    assert "dissociative equilibrium is assumed" in readme
    assert "law of mass action" in readme
    assert "p° is equal to 1 bar" in readme
    assert "a0/T + a1 ln T + b0 + b1 T + b2 T^2" in readme
    assert "should not be confused with the corresponding" in readme
    assert "not inverted" in readme
    assert "stock-2022-fastchem2-methods" in readme
    assert "kitzmann-2024-fastchem-cond-methods" in readme


def test_record_counts_follow_the_files_not_a_second_copy_of_shared_rows(manifest) -> None:
    gas = _lines("fastchem-v3-1-3-gas-condensate-coefficients.dat")
    extended = _lines("logK_extended.dat")
    condensates = _lines("logK_condensates.dat")
    assert _header_count(gas) == 495
    assert _header_count(extended) == 675
    assert _header_count(condensates) == 186
    summary = manifest["summary"]
    assert summary["standard_gas_count"] == 495
    assert summary["extended_only_gas_count"] == 180
    assert summary["condensate_count"] == 186
    assert summary["record_count"] == 861
    assert summary["shared_header_or_coefficient_disagreements"] == 0
    groups = {}
    symbols = {}
    for entry in manifest["entries"]:
        groups.setdefault(entry["record_group"], set()).add(entry["symbol_as_published"])
        symbols.setdefault(entry["symbol_as_published"], []).append(entry["record_group"])
    assert len(groups["standard_gas"]) == 495
    assert len(groups["extended_gas"]) == 180
    assert groups["standard_gas"].isdisjoint(groups["extended_gas"])
    assert "Na4SiO4" not in symbols
    assert "Na6Si2O7" not in symbols
    gas_headers = {
        line: gas[index + 1].split()
        for index, line in enumerate(gas)
        if line.strip() and not line.startswith("#") and ":" in line and "#" in line
    }
    shared = 0
    for index, line in enumerate(extended):
        if line not in gas_headers:
            continue
        shared += 1
        assert extended[index + 1].split() == gas_headers[line]
    assert shared == 495


def _assert_row(docs, symbol: str, tokens: tuple[str, ...], *, row: int = 0) -> dict:
    manifest = load_manifest()
    entry = _by_symbol(manifest)[symbol]
    document = docs[entry["record_id"]]
    assert tuple(document["coefficients_as_published"][row]) == tokens
    lines = (ROOT / document["source_locator"]["path"]).read_text(encoding="utf-8").splitlines()
    raw_hits = [
        index for index, line in enumerate(lines) if tuple(line.split()) == tokens
    ]
    assert raw_hits, symbol
    line_no = document["source_locator"]["coefficient_lines"][row]
    assert line_no - 1 in raw_hits
    assert coefficients_match_raw_lines(document, lines)
    for stored in document["coefficients_as_published"][row]:
        assert isinstance(stored, str)
        assert "value" not in stored
    return document


def test_three_gas_rows_match_the_raw_text(docs) -> None:
    alcl = _assert_row(docs, "Al1Cl1", GAS_ALCL)
    assert alcl["formula"] == "AlCl"
    assert alcl["phase"] == "gas"
    assert alcl["charge"] == 0
    assert alcl["name_as_published"] == "Aluminum_Chloride"
    nio = _assert_row(docs, "Ni1O1", GAS_NIO)
    assert nio["name_as_published"] == ""
    assert nio["formula"] == "NiO"
    zn = _assert_row(docs, "Zn1-", GAS_ZN_ANION)
    assert zn["formula"] == "Zn-"
    assert zn["charge"] == -1
    assert zn["fit_basis"]["log_basis"] == "ln"
    assert zn["fit_basis"]["standard_pressure"] == "1 bar"
    assert zn["fit_basis"]["coefficients_inverted"] is False
    assert zn["fit_basis"]["reaction_direction_status"] == "not_resolved"
    assert "direction" not in zn["fit_basis"]
    gas_authority = zn["fit_basis"]["authorities"]["gas"]
    assert "10^5 Pa" in gas_authority["log_basis_and_pressure_quote"]
    assert "natural logarithm" in gas_authority["log_basis_and_pressure_quote"]
    assert "dissociation reaction" in gas_authority["log_basis_and_pressure_quote"]
    assert "law of mass action" in gas_authority["mass_action_quote"]
    assert "a0/T + a1 ln T + b0" in gas_authority["form_quote"]
    assert zn["fit_basis"]["form_as_published"] == FORM_AS_PUBLISHED
    assert zn["T_min_K"]["kind"] == "unknown"
    assert zn["T_max_K"]["kind"] == "unknown"
    assert zn["method_class"] == "fit"
    assert zn["evaluation_class"] == "compiled"
    assert zn["compilation_role"]["kind"] == "fit"
    assert zn["compilation_role"]["scoring_eligible"] is False
    assert "Chase, M. et al., JANAF thermochemical tables, 1998." in zn[
        "lineage_citation_as_published"
    ]


def test_three_extended_rows_match_the_raw_text(docs) -> None:
    shared = _assert_row(docs, "Al1Cl1", GAS_ALCL)
    assert shared["source_locator"]["path"] == GAS_SOURCE_REL
    scandium = _assert_row(docs, "Sc1+", EXT_SC_CATION)
    assert scandium["record_id"].startswith("FC-X-")
    assert scandium["source_locator"]["path"] == EXTENDED_SOURCE_REL
    assert scandium["formula"] == "Sc+"
    assert scandium["charge"] == 1
    assert "NASA/TP--1999-208523" in scandium["lineage_citation_as_published"]
    uranium = _assert_row(docs, "U1++", EXT_U_DICAT)
    assert uranium["formula"] == "U++"
    assert uranium["charge"] == 2
    assert uranium["T_min_K"]["kind"] == "unknown"


def test_three_condensate_rows_match_the_raw_text(docs) -> None:
    aluminium = _assert_row(docs, "Al(s)", COND_AL)
    assert aluminium["phase"] == "condensed"
    assert aluminium["phase_as_published"] == "s"
    condensed = aluminium["fit_basis"]["authorities"]["condensed"]
    assert condensed["pressure_quote"] == "For the data used in the FastChem code, p° is equal to 1 bar."
    assert "a0/T + a1 ln T + b0" in condensed["form_quote"]
    assert "should not be confused with the corresponding" in condensed["not_janaf_lnK_quote"]
    assert aluminium["fit_basis"]["coefficients_inverted"] is False
    assert aluminium["source_locator"]["path"] == CONDENSATE_SOURCE_REL
    assert aluminium["intervals"][0]["T_low_K"]["kind"] == "unknown"
    assert aluminium["intervals"][0]["T_high_K"]["as_published"] == "1200.0"
    silicate = _assert_row(docs, "Na2SiO3(s,l)", COND_NA2SIO3_SOLID_LIMIT, row=0)
    assert tuple(silicate["coefficients_as_published"][1]) == COND_NA2SIO3_SECOND
    assert silicate["phase_as_published"] == "sl"
    assert [item["T_high_K"]["as_published"] for item in silicate["intervals"]] == [
        "1362.0",
        "2500.0",
    ]
    assert silicate["temperature_limit_rule"] == "successive_upper_bounds"
    silica = _assert_row(docs, "SiO(s)", COND_SIO)
    assert silica["intervals"][0]["T_high_K"]["as_published"] == "6000.00"
    lines = _lines("logK_condensates.dat")
    assert lines[-1].split() == list(COND_SIO)
    carbonate = docs[_by_symbol(load_manifest())["NaCO3(s,l)"]["record_id"]]
    assert carbonate["formula"] == "CNa2O3"
    assert carbonate["symbol_as_published"] == "NaCO3(s,l)"
    assert any(
        item["kind"] == "symbol_stoichiometry_mismatch" for item in carbonate["ambiguities"]
    )
    difluoro = docs[_by_symbol(load_manifest())["F2Si1"]["record_id"]]
    assert difluoro["formula"] == "F2Si2"
    assert any(
        item["kind"] == "symbol_stoichiometry_mismatch" for item in difluoro["ambiguities"]
    )


def test_mutated_coefficient_token_fails_the_raw_line_check(docs) -> None:
    document = docs["FC-G-0001"]
    lines = _lines("fastchem-v3-1-3-gas-condensate-coefficients.dat")
    assert coefficients_match_raw_lines(document, lines)
    mutated = json.loads(json.dumps(document))
    original = mutated["coefficients_as_published"][0][0]
    mutated["coefficients_as_published"][0][0] = original[:-1] + (
        "0" if original[-1] != "0" else "1"
    )
    assert mutated["coefficients_as_published"][0][0] != original
    assert coefficients_match_raw_lines(mutated, lines) is False
    assert coefficients_match_raw_lines(document, lines) is True


def test_overlap_keeps_both_sides_and_does_not_cross_phase_class() -> None:
    manifest = load_manifest()
    overlap = manifest["overlap"]
    assert overlap["matched_rows"] == 2650
    assert overlap["fastchem_with_any_overlap"] == 708
    assert overlap["matched_species_by_peer"] == {
        "atct": 207,
        "burcat": 456,
        "janaf": 652,
        "nasa-glenn": 590,
    }
    assert overlap["preference"].startswith("none")
    text = (COMPILATION_ROOT / "overlap.tsv").read_text(encoding="utf-8")
    rows = text.splitlines()
    assert rows[0].split("\t")[0] == "fastchem_record_id"
    assert len(rows) - 1 == 2650
    assert "\tAl-001\t" in text
    assert "\tBU-0008\t" in text
    assert "\tBU-3409\t" in text
    assert "ZnE-1" in text
    assert "CNa2O3" in text and "Na2CO3" in text
    zinc_key = composition_key({"Zn": 1}, 1)
    assert zinc_key == composition_key({"Zn": Decimal("1.0")}, 1)
    assert zinc_key != composition_key({"Zn": 2}, 1)
    peer = json.loads(
        (ROOT / "data/literature/compilations/burcat/records/BU-3409.json").read_text(
            encoding="utf-8"
        )
    )
    parsed = _nasa_composition(peer)
    assert parsed is not None
    assert composition_key(*parsed) == zinc_key
    assert composition_key({"Zn": 2}, parsed[1]) != zinc_key
    assert phase_classes("gas").isdisjoint(phase_classes("cr"))
    assert phase_classes("ref") == frozenset({"gas", "condensed"})
    assert phase_classes("aq") == frozenset()
    for line in rows[1:]:
        columns = line.split("\t")
        phase, peer_phase = columns[3], columns[8]
        if phase == "gas":
            assert peer_phase not in {"cr", "s", "solid", "l", "liquid", "L", "c"}
        if phase == "condensed":
            assert peer_phase not in {"g", "gas", "G"}


def test_scorer_has_no_new_quantity_and_no_new_preference(docs) -> None:
    document = docs["FC-G-0001"]
    quantity, reason = compilation_quantity_from_record(document)
    assert quantity.is_unknown
    assert reason is not None and "not mapped to a closed quantity" in reason
    selection = select_declared_source(quantity, None, document)
    assert selection.value.kind is ValueKind.UNAVAILABLE
    assert "source prints evaluator" in (selection.value.unavailable_reason or "")
    assert "fastchem" not in COMPILATION_SOURCE_MARKERS
    assert is_compilation_source(
        "fastchem-v3-1-3", "compilations-fastchem-v3-1-3.yaml"
    )
    assert is_compilation_source("fastchem-v3-1-3", None) is False
    condensate = docs["FC-C-0001"]
    condensed_quantity, _condensed_reason = compilation_quantity_from_record(condensate)
    condensed_selection = select_declared_source(condensed_quantity, None, condensate)
    assert condensed_selection.value.kind is ValueKind.UNAVAILABLE


def test_isolated_migration_refuses_a_point_value(tmp_path: Path, docs) -> None:
    root = tmp_path / "tree"
    extracts = root / "data" / "literature" / "extracts"
    records = root / "data" / "literature" / "compilations" / "fastchem-v3-1-3" / "records"
    extracts.mkdir(parents=True)
    records.mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for record_id in ("FC-G-0001", "FC-C-0001"):
        (records / f"{record_id}.json").write_text(
            json.dumps(docs[record_id]) + "\n",
            encoding="utf-8",
        )
    result = migrate(root, write=False)
    observations = {
        key: value
        for key, value in result.observations.items()
        if key.startswith("fastchem-v3-1-3:")
    }
    assert set(observations) == {"fastchem-v3-1-3:FC-G-0001", "fastchem-v3-1-3:FC-C-0001"}
    for observation in observations.values():
        assert observation.identity.quantity.is_unknown
        assert "not mapped to a closed quantity" in (observation.identity.quantity.reason or "")
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert "source prints evaluator" in (observation.value.unavailable_reason or "")
        assert observation.identity.temperature_K.is_unknown
        assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        assert observation.evidence.original_method_class == "fit"
    gas = observations["fastchem-v3-1-3:FC-G-0001"]
    condensed = observations["fastchem-v3-1-3:FC-C-0001"]
    assert gas.identity.species.phase.value is Phase.G
    assert condensed.identity.species.phase.is_unknown
    assert "condensed" in (condensed.identity.species.phase.reason or "")
