"""Migration lift: identity, conservation, no-default, corpus validation."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    IdentityEqualKind,
    MethodToken,
    Phase,
    Quantity,
    Rail,
    StateTag,
    ValueKind,
)
from simulator.battery.identity import atm_to_pa, identity_equal, quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    DuplicateObservationIdError,
    UnknownRailSpellingError,
    canonicalize_doi,
    canonicalize_rail,
    citation_hash,
    convert_area_to_m2,
    convert_mass_to_kg,
    convert_pressure_to_pa,
    convert_temperature_to_k,
    map_phase,
    load_migrated_store,
    migrate,
    pressure_from_equipment,
    work_id_for,
    write_outputs,
)
from simulator.battery.records import Species, State, as_decimal
from simulator.battery.validate import validate_corpus
from tests.battery import factories as F

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


def test_h06_ledger_unknown_rail_spelling_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "gibbs-rail-probe",
                        "source_id": "janaf",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 298.15,
                        "table_kJ_mol": 0.0,
                        "rail": "gibbs_thermochemistry",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False, validate=True)


def test_g13_unknown_rail_spelling_raises_during_migrate(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["rail"] = "gibbs_thermochemistry"
    root = _write_min_tree(tmp_path, extract)
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False, validate=False)


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
    assert obs.identity.species.phase.is_value
    assert obs.identity.species.phase.value is Phase.G


def test_h07_write_outputs_prunes_stale_work_files(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True, validate=True)
    works_dir = root / "data" / "literature" / "works"
    stale = works_dir / "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef.yaml"
    stale.write_text(
        "schema_version: battery_work.v2.1\nwork:\n  citation: stale\n  source_ids: [stale]\n",
        encoding="utf-8",
    )
    aliases_path = works_dir / "ALIASES.yaml"
    doc = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
    doc["aliases"]["stale-source"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    aliases_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    migrate(root, write=True, validate=True)
    assert not stale.is_file()
    aliases = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))["aliases"]
    assert "stale-source" not in aliases


def test_row_conservation_and_idempotency(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    first = migrate(root, write=True, validate=True)
    extract_rows = 1
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].rows_in == extract_rows
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].observations_out >= extract_rows
    first_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    second = migrate(root, write=True, validate=True)
    second_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    assert first_bytes.keys() == second_bytes.keys()
    for key in first_bytes:
        assert first_bytes[key] == second_bytes[key], key
    original = (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes()
    migrate(root, write=True, validate=True)
    assert (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes() == original
    assert (root / "data" / "literature" / "works" / "ALIASES.yaml").is_file()


def test_validate_corpus_zero_hard_issues_on_fixture(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True, validate=True)
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()
    assert result.validation is not None
    assert result.validation.hard_issues == ()


def test_validate_corpus_zero_hard_issues_on_migrated_store() -> None:
    report_path = REPO_ROOT / "data" / "battery" / "migration-report.md"
    works_dir = REPO_ROOT / "data" / "literature" / "works"
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not report_path.is_file() or not any(works_dir.glob("*.yaml")):
        pytest.skip("migrated store not generated yet")
    works, experiments, observations = load_migrated_store(REPO_ROOT)
    assert works
    assert observations
    report = validate_corpus(works, experiments, observations, residuals=None)
    assert report.hard_issues == ()
    assert extracts_v2.is_dir() or obs_dir.is_dir()


def test_h05_corrupted_extracts_v2_yaml_fails_store_load(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True, validate=True)
    works, experiments, observations = load_migrated_store(root)
    report = validate_corpus(works, experiments, observations, residuals=None)
    assert report.hard_issues == ()
    dest = next((root / "data" / "literature" / "extracts-v2").glob("*.yaml"))
    dest.write_text("invalid: [yaml\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_migrated_store(root)


def _phase_state(obs):
    return obs.identity.species.phase


def test_g01_blank_phase_is_unknown_not_gas(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = ""
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    phase = _phase_state(obs)
    assert phase.is_unknown, phase
    assert phase.value is None
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()


def test_g01_unmapped_and_sidecar_phases_are_unknown(tmp_path: Path) -> None:
    """Grok P0-1 table: unmapped/missing source phases must not become g."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    rows = extract["species"]["Na"]["observations"]
    base = rows[0]
    mislifts = [
        ("solid_arsenolite", {"condensed_form": {"polymorph": "arsenolite"}}),
        ("silicate_melt", {}),
        ("liquid_Fe_Mn_alloy", {}),
        ("solid_metal", {}),
        ("liquid_H2O_to_H2O_g", {}),
        ("solid_O2_to_O2_g", {}),
        ("", {}),
        ("steelmaking_silicate_slag", {}),
        ("graphite", {}),
        ("ref", {}),
        ("cr,l", {}),
    ]
    rows.clear()
    for i, (phase, extra) in enumerate(mislifts):
        row = yaml.safe_load(yaml.safe_dump(base))
        row["observation_id"] = f"mislift_{i}"
        row["phase"] = phase
        row.update(extra)
        rows.append(row)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)

    def by_prefix(suffix: str):
        matches = [o for o in result.observations.values() if suffix in o.observation_id]
        assert matches, sorted(result.observations)
        return matches[0]

    arsenolite = by_prefix("mislift_0")
    assert arsenolite.identity.species.phase.is_value
    assert arsenolite.identity.species.phase.value is Phase.CR
    assert arsenolite.identity.species.polymorph is not None
    assert arsenolite.identity.species.polymorph.is_value
    assert arsenolite.identity.species.polymorph.value == "arsenolite"
    for i, (phase, _extra) in enumerate(mislifts[1:], start=1):
        obs = by_prefix(f"mislift_{i}")
        stored = _phase_state(obs)
        assert stored.is_unknown, (phase, stored)
        assert stored.value is not Phase.G
        assert stored.value is not Phase.CR


def test_g01_sidecar_without_phase_is_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    lit = root / "data" / "literature"
    (lit / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {
                    "kems-src": {
                        "citation": "KEMS sidecar fixture",
                        "doi": "10.1234/KEMS",
                    }
                },
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "kems_no_phase",
                                "species": "Ca",
                                "coordinate": {"temperature_K": 2000.0},
                                "partial_pressure_pa": 1.0,
                                "source_locator": {"figure": 1},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    kems = result.observations["kems_no_phase"]
    assert kems.identity.species.phase.is_unknown


def test_h09_quantity_not_applicable_is_not_unknown() -> None:
    from dataclasses import replace

    known = F.psat_identity("Na")
    na = replace(known, quantity=State.not_applicable("test"))
    both_na = identity_equal(na, na)
    assert both_na.kind is IdentityEqualKind.INVALID_IDENTITY
    vs_known = identity_equal(known, na)
    assert vs_known.kind is IdentityEqualKind.IDENTITY_MISMATCH
    unk = replace(known, quantity=State.unknown("missing quantity"))
    assert identity_equal(unk, unk).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert identity_equal(known, unk).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    exp = F.tabulation_experiment()
    report = validate_corpus(
        [F.work()],
        [exp],
        [F.observation("na-qty", exp.experiment_id, na, 1)],
    )
    assert any(i.reason.value == "invalid_identity" for i in report.hard_issues)


def test_g01_identity_unknown_phase_never_equals() -> None:
    from dataclasses import replace

    known = F.psat_identity("Na")
    unknown_species = Species(
        "Na",
        State.unknown("source does not state phase"),
        polymorph=State.unknown("phase unknown"),
    )
    unknown = replace(known, species=unknown_species)
    outcome = identity_equal(known, unknown)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "species.phase" in outcome.fields
    both_unknown = identity_equal(unknown, unknown)
    assert both_unknown.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    exp = F.tabulation_experiment()
    report = validate_corpus(
        [F.work()],
        [exp],
        [F.observation("unk-phase", exp.experiment_id, unknown, 1)],
    )
    assert report.hard_issues == ()


def test_g02_iron_olivine_kems_is_knudsen_not_langmuir(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "langmuir_knudsen_flux_validation.v1",
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "material": "Fo93Fa7_olivine",
                        "regime": "knudsen_effusion_mass_spectrometry",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {"range": [0.011, 0.020]},
                        "source": {
                            "citation_id": "REF-016",
                            "citation": "Costa & Jacobson (2015)",
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    obs = result.observations["iron_olivine_kems"]
    exp = result.experiments[obs.experiment_id]
    assert exp.method.is_value
    assert exp.method.value is MethodToken.KNUDSEN_EFFUSION
    assert exp.method.value is not MethodToken.LANGMUIR_FREE_EVAPORATION
    assert obs.evidence.class_.is_unknown
    assert obs.evidence.class_.value is not EvidenceClass.MEASURED_DIRECT


def test_g02_kems_row_without_method_is_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {"kems-src": {"citation": "KEMS sidecar fixture"}},
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "kems_no_method",
                                "species": "Ca",
                                "status": "absent",
                                "coordinate": {"temperature_K": 2000.0},
                                "partial_pressure_pa": None,
                                "source_locator": {"figure": 1},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    obs = result.observations["kems_no_method"]
    exp = result.experiments[obs.experiment_id]
    assert exp.method.is_unknown
    assert obs.evidence.class_.is_unknown
    assert obs.evidence.class_.value is not EvidenceClass.FIGURE_ONLY


def test_g09_queue_ids_resolve_in_store(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = "silicate_melt"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    for entry in result.queue:
        assert entry.work_id in result.works, entry
        if entry.observation_id is not None:
            assert entry.observation_id in result.observations, entry


def test_g10_kems_uncertainty_is_retained(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {"kems-src": {"citation": "KEMS sidecar fixture"}},
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "cao_p_ca_isothermal",
                                "species": "Ca",
                                "coordinate": {"temperature_K": 2077.0},
                                "partial_pressure_pa": None,
                                "uncertainty": {"temperature_K": 5, "observable_status": "not_reported"},
                                "source_locator": {"figure": 7},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    obs = result.observations["cao_p_ca_isothermal"]
    assert obs.uncertainty.kind.value == "printed"
    assert obs.uncertainty.verbatim is not None


def test_h04_ocr_locator_does_not_fall_through_to_pdf(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["locator"] = {
        "source_path": "ocr/missing.md",
        "table": "I",
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    assert not str(obs.read_from).startswith("pdf:")
    assert "unknown" in str(obs.read_from)
    assert any("ocr" in (e.why or "").lower() or "source_path" in (e.why or "")
               for e in result.queue)


def test_g11_read_from_is_unknown_without_index_asset(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": "fixture-source",
                        "citation": "Fixture, A. (2026), Test Journal 1:1",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    assert not str(obs.read_from).startswith("pdf:")
    assert "unknown" in str(obs.read_from)


def test_g12_metadata_files_are_not_observation_rows(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    comp = root / "data" / "literature" / "compilations" / "janaf-4th"
    comp.mkdir(parents=True)
    (comp / "manifest.yaml").write_text(
        yaml.safe_dump({"source_id": "janaf-4th", "source": {"citation": "JANAF"}}, sort_keys=False),
        encoding="utf-8",
    )
    (comp / "sidecar.yaml").write_text("schema_version: sidecar\n", encoding="utf-8")
    result = migrate(root, write=False, validate=True)
    manifest = result.source_counts["data/literature/compilations/janaf-4th/manifest.yaml"]
    sidecar = result.source_counts["data/literature/compilations/janaf-4th/sidecar.yaml"]
    assert manifest.metadata_in == 1
    assert manifest.rows_in == 0
    assert sidecar.metadata_in == 1
    assert sidecar.rows_in == 0
    index = result.source_counts["data/literature/INDEX.yaml"]
    assert index.index_works_in >= 1
    assert index.rows_in == 0


def test_g08_model_derived_keeps_table_destination(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"]["method_class"] = "model_derived"
    extract["species"]["Na"]["observations"][0]["values"].pop("series", None)
    extract["species"]["Na"]["observations"][0]["values"]["quantity"] = "pure_Psat"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    assert obs.evidence.class_.is_value
    assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED


def test_h08_fourteen_token_table_destinations_are_stored(tmp_path: Path) -> None:
    from simulator.battery.migrate import METHOD_CLASS_MAP, evidence_for

    tokens = [
        "model_derived",
        "model_derived_inverse_fit",
        "model_derived_from_Kstar_and_external_gamma",
        "model_derived_from_Kstar_with_alpha_e_adopted_unity",
        "model_derived_assumption",
        "model",
        "derived_from_measured_kems_hertz_knudsen",
        "derived_third_law_from_measured_kems_and_janaf_fef",
        "derived_least_squares",
        "derived_gibbs_duhem_integration",
        "authors_preferred_average_of_kems_derived_gammas",
        "directly_reduced_measurement",
        "model_derived_second_law_fit",
        "derived_gibbs_duhem",
    ]
    assert len(tokens) == 14
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    rows = []
    for i, token in enumerate(tokens):
        row = yaml.safe_load(yaml.safe_dump(base))
        row["observation_id"] = f"token_{i}"
        row["values"]["method_class"] = token
        row["values"].pop("series", None)
        rows.append(row)
    extract["species"]["Na"]["observations"] = rows
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    for i, token in enumerate(tokens):
        dest = METHOD_CLASS_MAP[token]
        helper, _reason = evidence_for(token)
        assert helper.class_.is_value, token
        assert helper.class_.value is dest, token
        matches = [o for o in result.observations.values() if f"token_{i}" in o.observation_id]
        assert matches, token
        assert matches[0].evidence.class_.is_value, token
        assert matches[0].evidence.class_.value is dest, token


def test_g07_unsupported_quantity_is_unknown_not_relabeled(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "mre_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mre_measurements.v1",
                "measurements": {
                    "yu_2025_hollow_anode_measurements": {
                        "paper_citation": {"title": "MRE fixture", "doi": "10.1234/MRE"},
                        "cases": {
                            "one_hour": {
                                "comparison_points": [
                                    {
                                        "observable_id": "mre_applied_charge_C",
                                        "expected_value": 1800.0,
                                        "units": "C",
                                        "source_locator": {"section": "2.2.2"},
                                    }
                                ]
                            }
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "janaf::Mg-nbp-sanity:T=1363::vapour_rail_psat::log10_Psat_over_P0",
                        "source_id": "janaf",
                        "species": "Mg",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 1363,
                        "table_kJ_mol": 0.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False, validate=True)
    charge = result.observations[
        "yu_2025_hollow_anode_measurements:one_hour:mre_applied_charge_C"
    ]
    assert charge.identity.quantity.is_unknown
    assert quantity_token(charge.identity) is not Quantity.MASS_LOSS_FRACTION
    psat = result.observations[
        "janaf::Mg-nbp-sanity:T=1363::vapour_rail_psat::log10_Psat_over_P0"
    ]
    assert psat.identity.quantity.is_unknown
    assert quantity_token(psat.identity) is not Quantity.DELTA_FG
    assert "log10_Psat_over_P0" in (psat.identity.quantity.reason or "")


def test_g06_p_atm_and_unliftable_series_explode(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "behrens_p_atm",
            "type": "psat_series",
            "locator": {"table": "2"},
            "phase": "gas",
            "units": "atm",
            "values": {
                "quantity": "pure_Psat",
                "method_class": "measured_direct",
                "series": [
                    {"T_K": 1200.0, "p_atm": 1.0},
                    {"T_K": 1300.0, "p_atm": 2.0},
                ],
            },
        },
        {
            "observation_id": "unliftable_row",
            "type": "psat_series",
            "locator": {"table": "3"},
            "phase": "gas",
            "values": {
                "quantity": "pure_Psat",
                "series": [{"quote": "text only", "locator": {"line_range": "4-5"}}],
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    atm_points = [
        o
        for o in result.observations.values()
        if "behrens_p_atm" in o.observation_id
    ]
    assert len(atm_points) == 2
    assert all(p.derivation is not None and p.derivation.relation == "atm_to_Pa" for p in atm_points)
    unlift = [
        o
        for o in result.observations.values()
        if "unliftable_row" in o.observation_id
    ]
    assert len(unlift) == 1
    assert unlift[0].value.kind.value == "unavailable"
    assert result.measured.series == 2
    assert result.measured.tabulated_lists == 0


def test_g05_identical_duplicate_keys_are_aliased(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    point = {
        "key": "pankratz-dup::T=336.35::delta_fG_kJ_mol",
        "source_id": "pankratz-1987-usbm-b689",
        "species": "AgS",
        "temperature_K": 336.35,
        "table_kJ_mol": 1.0,
    }
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "points": [point, dict(point)]}, sort_keys=False),
        encoding="utf-8",
    )
    result = migrate(root, write=True, validate=True)
    oid = point["key"]
    assert oid in result.observations
    assert len(result.observations) == 3  # fixture extract 2 points + 1 unique ledger
    aliases = [a for a in result.dedupe_aliases if a.observation_id == oid]
    assert aliases
    assert aliases[0].row_indices == (0, 1)
    ledger_count = result.source_counts["data/literature/gibbs_battery_residual_ledger.yaml"]
    assert ledger_count.rows_in == 2
    assert ledger_count.observations_out == 1
    store_obs = sum(c.observations_out for c in result.source_counts.values())
    assert store_obs == len(result.observations)


def test_g05_conflicting_duplicate_key_is_hard_error(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    a = {
        "key": "conflict-key",
        "source_id": "pankratz-1987-usbm-b689",
        "species": "AgS",
        "temperature_K": 300,
        "table_kJ_mol": 1.0,
    }
    b = dict(a)
    b["table_kJ_mol"] = 2.0
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "points": [a, b]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(DuplicateObservationIdError):
        migrate(root, write=False, validate=True)


def test_g04_supersedes_marks_the_old_row(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "homma_1966_mn_olette_alpha_exp_table1",
            "type": "alpha",
            "locator": {"table": "1"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        },
        {
            "observation_id": "homma_1966_mn_olette_experimental_quoted_deep",
            "supersedes": "homma_1966_mn_olette_alpha_exp_table1",
            "type": "alpha",
            "locator": {"table": "1", "page": 517},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        },
        {
            "observation_id": "newer_list",
            "supersedes": [
                "homma_1966_mn_olette_experimental_quoted_deep",
                "missing_target",
            ],
            "type": "alpha",
            "locator": {"table": "2"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "admission_status": "admitted",
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    old = result.observations["fixture-source::homma_1966_mn_olette_alpha_exp_table1"]
    new = result.observations["fixture-source::homma_1966_mn_olette_experimental_quoted_deep"]
    newer = result.observations["fixture-source::newer_list"]
    assert old.admission.status is AdmissionStatus.SUPERSEDED
    assert old.admission.superseded_by == new.observation_id
    assert new.admission.status is AdmissionStatus.SUPERSEDED
    assert new.admission.superseded_by == newer.observation_id
    assert newer.admission.status is AdmissionStatus.ADMITTED
    assert newer.admission.superseded_by is None
    assert any("missing_target" in (e.why or "") for e in result.queue)


def test_h03_pressure_conversion_keeps_derivation_trail() -> None:
    env = pressure_from_equipment(
        {"chamber_pressure": {"value": 1, "units": "Torr", "locator": {"table": "1"}}}
    )
    located = env.total_pressure_Pa
    assert located.state.is_value
    assert located.inference is not None
    assert located.inference.relation == "Torr_to_Pa"
    joined = " ".join(located.inference.inputs)
    assert "101325" in joined and "760" in joined
    params = dict(located.inference.parameters)
    assert "factor" in params
    assert params["factor"].state.is_value
    tiny = pressure_from_equipment(
        {
            "chamber_pressure": {
                "value": "1.0e-05",
                "units": "Torr",
                "locator": {"figure": "8"},
            }
        }
    )
    assert tiny.total_pressure_Pa.inference is not None
    assert tiny.total_pressure_Pa.inference.relation == "Torr_to_Pa"
    assert tiny.total_pressure_Pa.state.value == as_decimal("1.0e-05") * as_decimal(
        "101325"
    ) / as_decimal("760")


def test_g03_blank_pressure_unit_is_unknown() -> None:
    pa, why = convert_pressure_to_pa(1, "")
    assert pa is None
    assert why is not None and "unit" in why.lower()
    env = pressure_from_equipment(
        {"chamber_pressure": {"value": 1, "units": "", "locator": {"table": "1"}}}
    )
    assert env.total_pressure_Pa.state.is_unknown
    pa_ok, trail = convert_pressure_to_pa(1, "Pa")
    assert pa_ok == 1
    assert trail == "identity:Pa"
    t, why_t = convert_temperature_to_k(1200, "")
    assert t is None
    assert why_t is not None and "unit" in why_t.lower()
    area, why_a = convert_area_to_m2(1, "")
    assert area is None and why_a is not None
    mass, why_m = convert_mass_to_kg(1, "")
    assert mass is None and why_m is not None


def test_g01_map_phase_refuses_heuristics() -> None:
    mapped, why = map_phase("gas")
    assert mapped.is_value and mapped.value is Phase.G and why is None
    mapped, why = map_phase("condensed_solid")
    assert mapped.is_value and mapped.value is Phase.CR and why is None
    mapped, why = map_phase("")
    assert mapped.is_unknown and why is not None
    mapped, why = map_phase("ref")
    assert mapped.is_unknown
    mapped, why = map_phase("cr,l")
    assert mapped.is_unknown
    mapped, why = map_phase("silicate_melt")
    assert mapped.is_unknown


def test_h01_bare_series_T_P_without_units_stay_unknown(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = ""
    row["values"]["series"] = [{"T": 1200, "P": 1}]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    temp = obs.identity.temperature_K
    assert temp is not None and temp.is_unknown, temp
    assert temp.value != 1200
    assert obs.value.kind is not ValueKind.POINT or obs.value.point != 1
    assert obs.value.kind.value in {"unavailable", "unknown"} or (
        obs.value.kind is ValueKind.POINT and obs.derivation is None
    )
    assert obs.value.kind is ValueKind.UNAVAILABLE
    axes = {(e.observation_id, tuple(e.axes), e.why) for e in result.queue}
    assert any("temperature" in (why or "").lower() or "temperature_K" in axes_
               for _oid, axes_, why in axes)
    assert any("value" in axes_ or "unit" in (why or "").lower()
               for _oid, axes_, why in axes)


def test_h01_explicit_T_K_pressure_atm_still_converts(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=False, validate=True)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    assert all(p.identity.temperature_K is not None and p.identity.temperature_K.is_value
               for p in points)
    assert {p.identity.temperature_K.value for p in points} == {1200, 1300} or (
        {float(p.identity.temperature_K.value) for p in points} == {1200.0, 1300.0}
    )
    assert all(p.value.kind is ValueKind.POINT for p in points)
    assert all(p.derivation is not None and p.derivation.relation == "atm_to_Pa" for p in points)


def test_h01_blank_sample_area_units_queued_and_sample_transferred(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "sample_surface_area": {"value": 1, "units": "", "locator": {"table": "I"}},
        "sample": {
            "mass": {"value": 10, "units": "g", "locator": {"table": "I"}},
            "form": "powder",
        },
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    obs = next(iter(result.observations.values()))
    exp = result.experiments[obs.experiment_id]
    area = exp.apparatus.geometry.exposed_area_m2 if exp.apparatus and exp.apparatus.geometry else None
    assert area is not None and area.state.is_unknown
    assert any(
        "exposed_area" in (e.axes or ()) or "area" in (e.why or "").lower()
        for e in result.queue
    )
    assert exp.sample.mass_kg is not None
    assert exp.sample.mass_kg.state.is_value
    assert exp.sample.form is not None
    assert exp.sample.form.state.is_value
    assert exp.sample.form.state.value == "powder"


def test_h02_activity_coefficient_uses_gamma_not_pressure(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "gao15_gamma_s1_1low_polytherm",
            "type": "activity_coefficient",
            "locator": {"table": "S1"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "method_class": "measured_direct",
                "series": [
                    {
                        "T_K": 1586.4,
                        "gamma": 0.0632,
                        "gamma_SD": 0.0558,
                        "p_Ga_Pa": 4.04e-05,
                        "p_O2_calc_Pa": 0.000112,
                        "K_Ga": 4.54e-15,
                        "delta_IW": 1.66,
                    }
                ],
            },
        },
        {
            "observation_id": "gao15_gamma_s2_1low_isotherm",
            "type": "activity_coefficient",
            "locator": {"table": "S2"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1741.9, "gamma": 0.0353, "p_Ga_Pa": 0.000881}],
            },
        },
        {
            "observation_id": "ino15_gamma_s1_1low_polytherm",
            "type": "activity_coefficient",
            "locator": {"table": "S1b"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1586.4, "gamma": 0.0527, "p_In_Pa": 6.69e-05}],
            },
        },
        {
            "observation_id": "ino15_gamma_s2_1low_isotherm",
            "type": "activity_coefficient",
            "locator": {"table": "S2b"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1741.9, "gamma": 0.0211, "p_In_Pa": 0.00032}],
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False, validate=True)
    expected = {
        "gao15_gamma_s1_1low_polytherm": "0.0632",
        "gao15_gamma_s2_1low_isotherm": "0.0353",
        "ino15_gamma_s1_1low_polytherm": "0.0527",
        "ino15_gamma_s2_1low_isotherm": "0.0211",
    }
    for suffix, gamma in expected.items():
        points = [
            o
            for o in result.observations.values()
            if suffix in o.observation_id and "::point:0" in o.observation_id
        ]
        assert len(points) == 1, suffix
        obs = points[0]
        assert quantity_token(obs.identity) is Quantity.ACTIVITY_COEFFICIENT
        assert obs.value.kind is ValueKind.POINT
        assert obs.value.point == as_decimal(gamma)
        pressures = []
        # The stored coefficient must not equal a pressure column from its row.
        assert obs.value.point != as_decimal("4.04e-05")
        assert obs.value.point != as_decimal("0.000881")
        assert obs.value.point != as_decimal("6.69e-05")
        assert obs.value.point != as_decimal("0.00032")
        del pressures
    queued_axes = " ".join(e.why or "" for e in result.queue)
    assert "p_Ga_Pa" in queued_axes or "ancillary" in queued_axes.lower()


def test_h02_bischof_stored_gammas_match_source() -> None:
    from decimal import Decimal

    src_path = REPO_ROOT / "data" / "literature" / "extracts" / "kems-137-bischof-2023.yaml"
    store_path = (
        REPO_ROOT / "data" / "literature" / "extracts-v2" / "kems-137-bischof-2023.yaml"
    )
    if not src_path.is_file() or not store_path.is_file():
        pytest.skip("Bischof extract or v2 store not present")
    source = yaml.safe_load(src_path.read_text(encoding="utf-8"))
    stored = yaml.safe_load(store_path.read_text(encoding="utf-8"))
    first = {
        "bischof_2023_gao15_gamma_s1_1low_polytherm": Decimal("0.0632"),
        "bischof_2023_gao15_gamma_s2_1low_isotherm": Decimal("0.0353"),
        "bischof_2023_ino15_gamma_s1_1low_polytherm": Decimal("0.0527"),
        "bischof_2023_ino15_gamma_s2_1low_isotherm": Decimal("0.0211"),
    }
    by_id = {o["observation_id"]: o for o in stored["observations"]}
    for suffix, gamma in first.items():
        oid = f"kems-137-bischof-2023::{suffix}::point:0"
        obs = by_id[oid]
        q = obs["identity"]["quantity"]
        assert q.get("value") == "activity_coefficient"
        assert Decimal(str(obs["value"]["point"])) == gamma
    source_gammas: list[Decimal] = []
    source_pressures: set[Decimal] = set()
    species = source.get("species") or {}
    for body in species.values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations") or []:
            values = row.get("values") or {}
            if values.get("quantity") != "activity_coefficient":
                continue
            for item in values.get("series") or []:
                if not isinstance(item, dict):
                    continue
                if "gamma" in item:
                    source_gammas.append(Decimal(str(item["gamma"])))
                for pk in ("p_Ga_Pa", "p_In_Pa"):
                    if pk in item:
                        source_pressures.add(Decimal(str(item[pk])))
    stored_vals = []
    for obs in stored["observations"]:
        q = obs.get("identity", {}).get("quantity", {})
        if q.get("value") != "activity_coefficient":
            continue
        if obs.get("value", {}).get("kind") != "point":
            continue
        stored_vals.append(Decimal(str(obs["value"]["point"])))
    assert len(stored_vals) == 128
    gamma_set = set(source_gammas)
    for val in stored_vals:
        assert val in gamma_set
        assert val not in source_pressures
