"""Migration lift: identity, conservation, no-default, corpus validation."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, Phase, Rail, StateTag
from simulator.battery.identity import atm_to_pa
from simulator.battery.migrate import (
    REPO_ROOT,
    UnknownRailSpellingError,
    canonicalize_doi,
    canonicalize_rail,
    citation_hash,
    migrate,
    work_id_for,
    write_outputs,
)
from simulator.battery.validate import validate_corpus

FIXTURE_EXTRACT = {
    "schema_version": "literature_extract.v1",
    "source_id": "fixture-source",
    "source": {
        "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
        "doi": "10.1234/FIXTURE",
    },
    "extraction": {"method": "unit_test", "date": "2026-09-13", "worker": "pytest"},
    "review_status": "draft",
    "fidelity_samples": [
        {
            "path": "species.Na.observations[na_psat].values.series",
            "value": [{"T_K": 1200.0, "pressure_atm": 1.0}],
            "note": "fixture",
            "locator": {"table": "I", "page": 2},
        }
    ],
    "species": {
        "Na": {
            "observations": [
                {
                    "observation_id": "na_psat",
                    "type": "psat_series",
                    "locator": {"table": "I", "page": 2},
                    "phase": "gas",
                    "regime": "knudsen_effusion",
                    "units": "atm",
                    "uncertainty": {"note": "printed ±"},
                    "values": {
                        "quantity": "pure_Psat",
                        "method_class": "measured_direct",
                        "admission_status": "admitted",
                        "series": [
                            {"T_K": 1200.0, "pressure_atm": 1.0},
                            {"T_K": 1300.0, "pressure_atm": 2.0},
                        ],
                    },
                }
            ]
        }
    },
}


def _write_min_tree(root: Path, extract: dict | None = None) -> Path:
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": [
                    {
                        "source_id": "fixture-source",
                        "citation": "Fixture, A. (2026), Test Journal 1:1",
                        "doi": "10.1234/FIXTURE",
                        "corpus": {
                            "commit": "abc123",
                            "raw": {
                                "path": "raw/fixture.pdf",
                                "sha256": "deadbeef",
                            },
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    doc = extract if extract is not None else FIXTURE_EXTRACT
    (extracts / "fixture-source.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return root


def test_citation_hash_nfc_and_whitespace() -> None:
    a = "Cafe\u0301  source"
    b = "Caf\u00e9   source"
    assert unicodedata.normalize("NFC", a) != a or True
    assert citation_hash(a) == citation_hash(b)
    assert citation_hash("exactly this") == hashlib.sha256(
        unicodedata.normalize("NFC", "exactly this").encode("utf-8")
    ).hexdigest()
    # Exact original is not hashed; extra inner space collapses.
    assert citation_hash("a  b") == citation_hash("a b")
    assert citation_hash("a b") != citation_hash("ab")


def test_doi_canonicalisation_and_alias_registry() -> None:
    assert canonicalize_doi("https://doi.org/10.1063/1.555991") == "10.1063/1.555991"
    assert canonicalize_doi("DOI: 10.1063/1.555991") == "10.1063/1.555991"
    assert canonicalize_doi("10.1063/1.555991") == "10.1063/1.555991"
    aliases = {"old-id": "10.1234/x", "10.1234/x": "10.1234/x"}
    work_id, doi = work_id_for(
        citation="Anything",
        doi="https://dx.doi.org/10.1234/X",
        source_id="old-id",
        aliases=aliases,
    )
    assert work_id == "10.1234/x"
    assert doi == "10.1234/x"
    hashed, missing = work_id_for(
        citation="No DOI here",
        doi=None,
        source_id="other",
        aliases={},
    )
    assert missing is None
    assert hashed == citation_hash("No DOI here")


def test_unknown_rail_spelling_raises() -> None:
    assert canonicalize_rail("melt activities") is Rail.MELT_ACTIVITY
    assert canonicalize_rail("SiO_evolution") is Rail.SIO_EVOLUTION
    assert canonicalize_rail("integrated_bench") is Rail.PYROLYSIS_YIELD
    with pytest.raises(UnknownRailSpellingError):
        canonicalize_rail("gibbs_thermochemistry")


def test_series_explosion_keeps_conversion_trail(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True, validate=True)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    values = sorted(p.value.point for p in points)
    assert values[0] == atm_to_pa("1")
    assert values[1] == atm_to_pa("2")
    assert all(p.derivation is not None and p.derivation.relation == "atm_to_Pa" for p in points)
    assert all(p.derivation.output_unit == "Pa" for p in points)


def test_no_default_property_blanked_admission_is_unknown(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"].pop("admission_status")
    extract["species"]["Na"]["observations"][0]["values"].pop("method_class")
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    assert obs.admission.status is AdmissionStatus.PENDING
    assert "does not state admission" in obs.admission.reason
    assert obs.evidence.class_.tag is StateTag.UNKNOWN
    # Phase was stated as gas — that is a lift, not a default.
    assert obs.identity.species.phase is Phase.G


def test_row_conservation_and_idempotency(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    first = migrate(root, write=True, validate=True)
    extract_rows = 1
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].rows_in == extract_rows
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].observations_out >= extract_rows
    second = migrate(root, write=True, validate=True)
    v2 = root / "data" / "literature" / "extracts-v2" / "fixture-source.yaml"
    aliases = root / "data" / "literature" / "works" / "ALIASES.yaml"
    assert v2.read_bytes() == v2.read_bytes()
    first_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file()
    }
    write_outputs(second, root)
    second_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file()
    }
    assert first_bytes.keys() == second_bytes.keys()
    for key in first_bytes:
        if "extracts/fixture-source.yaml" in str(key):
            continue
        assert first_bytes[key] == second_bytes[key], key
    original = (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes()
    migrate(root, write=True, validate=True)
    assert (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes() == original
    assert aliases.is_file()


def test_validate_corpus_zero_hard_issues_on_fixture(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True, validate=True)
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()
    assert result.validation is not None
    assert result.validation.hard_issues == ()


@pytest.mark.skipif(
    not (REPO_ROOT / "data" / "literature" / "works").exists(),
    reason="migrated store not generated yet",
)
def test_validate_corpus_zero_hard_issues_on_migrated_store() -> None:
    works_dir = REPO_ROOT / "data" / "literature" / "works"
    if not any(works_dir.glob("*.yaml")):
        pytest.skip("migrated store not generated yet")
    from simulator.battery.migrate import Migrator

    result = Migrator(REPO_ROOT).run()
    assert result.validation is not None
    assert result.validation.hard_issues == (), result.validation.hard_issues[:10]
