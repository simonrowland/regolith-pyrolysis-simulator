"""Migration lift: identity, conservation, no-default, corpus validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    PerBasis,
    AdmissionStatus,
    BenchIdentityBasis,
    AssetRole,
    EvidenceClass,
    IdentityEqualKind,
    MethodToken,
    NoticeKind,
    Phase,
    Polymorph,
    Quantity,
    Rail,
    RefusalReason,
    StateTag,
    ValueKind,
)
from simulator.battery.identity import atm_to_pa, identity_equal, quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    DuplicateContextIdError,
    QUEUE_SCHEMA_VERSION,
    DuplicateObservationIdError,
    Migrator,
    UnknownRailSpellingError,
    UnresolvedEquipmentContextError,
    canonicalize_doi,
    canonicalize_rail,
    citation_hash,
    compilation_family_from_store_path,
    convert_area_to_m2,
    convert_mass_to_kg,
    convert_pressure_to_pa,
    convert_temperature_to_k,
    dump_yaml,
    iter_observation_store_paths,
    lineage_parents_from_source,
    load_migrated_context,
    map_phase,
    map_quantity,
    compilation_quantity_from_record,
    choose_read_from,
    is_compilation_record_path,
    compilation_record_asset_id,
    compilation_column_series_from_record,
    load_migrated_store,
    expand_queue_entries,
    group_queue_entries,
    migrate,
    migration_queue_document,
    lift_vaporization_reaction_from_ledger_note,
    pressure_from_equipment,
    resolve_equipment_context,
    select_declared_source,
    to_plain,
    work_id_for,
    write_outputs,

)
from simulator.battery.records import Bench, BenchIdentity, Reaction, Species, State, as_decimal
from tests.battery import load_observation_store_summary
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


def _run_migrate_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_module_form_is_not_a_silent_success(tmp_path: Path) -> None:
    """python -m simulator.battery.migrate must actually run the lift.

    The unguarded module used to import and exit 0 with empty stdout, which
    made ``git status`` after a no-op look like a current store.
    """

    root = _write_min_tree(tmp_path)
    module = _run_migrate_cli(
        [
            sys.executable,
            "-m",
            "simulator.battery.migrate",
            "--dry-run",
            "--root",
            str(root),
        ]
    )
    script = _run_migrate_cli(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "battery_migrate.py"),
            "--dry-run",
            "--root",
            str(root),
        ]
    )
    assert "rows_in=" in module.stdout, (
        "module form must run the lift (or fail loud); "
        f"stdout={module.stdout!r} stderr={module.stderr!r} rc={module.returncode}"
    )
    assert module.stdout == script.stdout
    assert module.returncode == script.returncode
    assert not (root / "data" / "literature" / "observations-v2").exists()
    assert not (root / "data" / "literature" / "works").exists()


def test_citation_hash_nfc_and_whitespace() -> None:
    a = "Cafe\u0301  source"
    b = "Caf\u00e9   source"
    assert unicodedata.normalize("NFC", a) != a
    assert citation_hash(a) == citation_hash(b)
    assert citation_hash("exactly this") == hashlib.sha256(
        unicodedata.normalize("NFC", "exactly this").encode("utf-8")
    ).hexdigest()
    # Exact original is not hashed; extra inner space collapses.
    assert citation_hash("a  b") == citation_hash("a b")
    assert citation_hash("a b") != citation_hash("ab")


def test_h12_costa_2015_reviewed_alias_collapses_three_ids() -> None:
    from simulator.battery.migrate import REVIEWED_ALIASES

    sources = [
        (
            "costa-jacobson-2015",
            "Costa, G. C. C. & Jacobson, N. S. (2015), Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry, NASA NTRS 20150002321",
        ),
        (
            "kems-007-costa-2015",
            'Costa, G. C. C. & Jacobson, N. S. (2015), "Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry", NASA NTRS 20150002321',
        ),
        (
            "REF-016",
            "Costa & Jacobson (2015), Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry, NASA NTRS 20150002321",
        ),
    ]
    without = {
        work_id_for(citation=c, doi=None, source_id=s, aliases={})[0] for s, c in sources
    }
    assert len(without) == 3
    with_alias = {
        work_id_for(citation=c, doi=None, source_id=s, aliases=REVIEWED_ALIASES)[0]
        for s, c in sources
    }
    assert len(with_alias) == 1
    steurer_85, _ = work_id_for(
        citation="Lunar Oxygen Production by Vapor Phase Pyrolysis",
        doi=None,
        source_id="steurer-1985",
        aliases=REVIEWED_ALIASES,
    )
    steurer_92, _ = work_id_for(
        citation="Vapor Phase Pyrolysis",
        doi=None,
        source_id="steurer-1992",
        aliases=REVIEWED_ALIASES,
    )
    assert steurer_85 != steurer_92


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
    from simulator.battery.migrate import RAIL_MAP

    assert canonicalize_rail("melt activities") is Rail.MELT_ACTIVITY
    assert canonicalize_rail("SiO_evolution") is Rail.SIO_EVOLUTION
    assert canonicalize_rail("integrated_bench") is Rail.PYROLYSIS_YIELD
    assert RAIL_MAP.get("vapour") is Rail.VAPOUR
    assert canonicalize_rail("vapour") is Rail.VAPOUR
    with pytest.raises(UnknownRailSpellingError):
        canonicalize_rail("gibbs_thermochemistry")


def test_j05_vapour_spec_spelling_is_in_rail_map() -> None:
    from simulator.battery.migrate import RAIL_MAP

    assert "vapour" in RAIL_MAP
    assert RAIL_MAP["vapour"] is Rail.VAPOUR
    assert canonicalize_rail("vapour") is Rail.VAPOUR


def test_h11_missing_extraction_date_is_unspecified(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["extraction"].pop("date", None)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    decided = obs.admission.decided_by
    assert decided is not None
    assert decided.date != "1970-01-01"
    assert decided.date in {"unspecified", "unknown"}


def test_h10_migrate_has_no_validation_bypass() -> None:
    import inspect

    assert "validate" not in inspect.signature(migrate).parameters
    assert "validate" not in inspect.signature(Migrator.run).parameters
    assert "validate" not in inspect.signature(Migrator.finalize).parameters


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
        migrate(root, write=False)


def test_g13_unknown_rail_spelling_raises_during_migrate(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["rail"] = "gibbs_thermochemistry"
    root = _write_min_tree(tmp_path, extract)
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def _write_janaf_table_compilation(
    root: Path,
    *,
    doc_rail: str | None = None,
    table_rail: str | None = None,
    row_rail: str | None = None,
) -> Path:
    dest = root / "data" / "literature" / "compilations" / "janaf-fixture"
    dest.mkdir(parents=True, exist_ok=True)
    row: dict = {
        "temperature": {"value": 298.15},
        "formation_gibbs_energy": {"value": 0.0},
        "log10_formation_equilibrium_constant": {"value": 0.0},
    }
    if row_rail is not None:
        row["rail"] = row_rail
    table: dict = {
        "table_id": "Na-001",
        "index_entry": {"formula": "Na", "state": "g"},
        "standard_state_as_published": "p° = 0.1 MPa",
        "values": [row],
    }
    if table_rail is not None:
        table["rail"] = table_rail
    doc: dict = {
        "schema_version": "literature_compilation.v1",
        "source_id": "janaf-fixture",
        "source": {"citation": "JANAF fixture"},
        "table": table,
    }
    if doc_rail is not None:
        doc["rail"] = doc_rail
    (dest / "Na-001.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return root


def test_j03_compilation_document_unknown_rail_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, doc_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j03_compilation_table_unknown_rail_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, table_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j03_compilation_nested_row_unknown_rail_still_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, row_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j04_named_source_fallthrough_includes_unmapped_token(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "named-source-fallthrough",
                        "source_id": "janaf",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 298.15,
                        "table_kJ_mol": 0.0,
                        "method_class": "independent_tabulation",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["named-source-fallthrough"]
    from simulator.battery.enums import EvidenceClass

    assert obs.evidence.class_.is_value
    assert obs.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    assert obs.evidence.original_method_class == "independent_tabulation"


def test_series_explosion_keeps_conversion_trail(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True)
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
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.admission.status is AdmissionStatus.PENDING
    assert obs.admission.reason == "no observation admission_status mapped from source"
    assert obs.evidence.class_.tag is StateTag.UNKNOWN
    # Phase was stated as gas — that is a lift, not a default.
    assert obs.identity.species.phase.is_value
    assert obs.identity.species.phase.value is Phase.G


def test_h07_write_outputs_prunes_stale_work_files(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
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
    migrate(root, write=True)
    assert not stale.is_file()
    aliases = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))["aliases"]
    assert "stale-source" not in aliases


def test_row_conservation_and_idempotency(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    first = migrate(root, write=True)
    extract_rows = 1
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].rows_in == extract_rows
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].observations_out >= extract_rows
    first_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    second = migrate(root, write=True)
    second_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    assert first_bytes.keys() == second_bytes.keys()
    for key in first_bytes:
        assert first_bytes[key] == second_bytes[key], key
    original = (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes()
    migrate(root, write=True)
    assert (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes() == original
    assert (root / "data" / "literature" / "works" / "ALIASES.yaml").is_file()


def test_merge_located_mapping_order_is_hash_seed_stable() -> None:
    """b-549: set(old) | set(new) leaked PYTHONHASHSEED-dependent key order
    into serialized output (dump_yaml writes with sort_keys=False). The merge
    must be insertion-ordered: existing keys keep position, new keys append.
    Subprocesses are required — the hash seed is fixed at interpreter start.
    """

    probe = (
        "from simulator.battery.migrate import _merge_located_mapping\n"
        "from simulator.battery.records import Located, State\n"
        "old = {k: Located(State.of(k)) for k in ('alpha', 'beta', 'gamma')}\n"
        "new = {k: Located(State.of(k)) for k in ('delta', 'epsilon', 'zeta')}\n"
        "print(','.join(_merge_located_mapping(old, new).keys()))\n"
    )
    orders = set()
    for seed in ("0", "1", "2", "42"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(REPO_ROOT)
        out = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()
        orders.add(out)
    assert orders == {"alpha,beta,gamma,delta,epsilon,zeta"}


def test_lineage_parents_from_source_never_invents_pointer() -> None:
    """b-549: bare prose in derived_from must not get a source prefix slapped
    on — it can never resolve by construction. It is returned as prose so the
    caller can route it to the queue instead of minting a dangling pointer.
    """

    parents, prose = lineage_parents_from_source(
        {"derived_from": "author geometric calculation from assumed cube dimensions"},
        {},
        "src",
        set(),
    )
    assert parents == ()
    assert prose == ("author geometric calculation from assumed cube dimensions",)

    parents, prose = lineage_parents_from_source(
        {"derived_from": ["local_a", "other::obs", "prose note", "src::local_b"]},
        {},
        "src",
        {"local_a", "local_b"},
    )
    assert parents == ("src::local_a", "other::obs", "src::local_b")
    assert prose == ("prose note",)

    assert lineage_parents_from_source({}, {}, "src", {"local_a"}) == ((), ())


def test_prose_derived_from_is_queued_never_pointed(tmp_path: Path) -> None:
    """b-549 end-to-end: a prose derived_from on a derived observation yields
    no pointer on the record, no referential_integrity hard issue, and a queue
    entry carrying the prose plus locator (the text is never silently dropped).
    """

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    values = extract["species"]["Na"]["observations"][0]["values"]
    values["method_class"] = "model_derived"
    values["derived_from"] = "author estimate from assumed grain population"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    assert result.observations
    for obs in result.observations.values():
        assert not obs.derived_from
    queued = [
        e
        for e in result.queue
        if "author estimate from assumed grain population" in e.why
    ]
    assert len(queued) == 1
    # _resolve_queue_ids re-points entries to the realised observation id.
    assert queued[0].observation_id.startswith("fixture-source::na_psat")
    assert queued[0].axes == ["derived_from"]
    assert queued[0].locator  # locator rides along — the text keeps its address
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    reasons = {(i.path.rsplit(".", 1)[-1], i.reason.value) for i in report.hard_issues}
    assert ("derived_from", "referential_integrity") not in reasons
    # Unstated ancestry is still a hard conditional_field — surfaced, not hidden.
    assert ("derived_from", "conditional_field") in reasons


def test_validate_corpus_zero_hard_issues_on_fixture(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True)
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
    # J02 restored C(derived) derived_from+derivation. Unstated ancestry is a
    # hard conditional_field, not a silent pass. Other reasons must stay zero.
    for issue in report.hard_issues:
        assert issue.reason.value == "conditional_field", issue
        assert issue.path.endswith(".derived_from") or issue.path.endswith(".derivation"), issue
    assert extracts_v2.is_dir() or obs_dir.is_dir()


def test_h05_corrupted_extracts_v2_yaml_fails_store_load(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    report = validate_corpus(works, experiments, observations, residuals=None)
    assert report.hard_issues == ()
    dest = next((root / "data" / "literature" / "extracts-v2").glob("*.yaml"))
    dest.write_text("invalid: [yaml\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_migrated_store(root)


def _phase_state(obs):
    return obs.identity.species.phase


def test_unrecognised_polymorph_is_counted_and_queued(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    observations = extract["species"]["Na"]["observations"]
    observations[0]["phase"] = "condensed_solid"
    observations[0]["condensed_form"] = {"polymorph": "not-a-real-form"}
    observations[0]["values"]["series"] = [{"T_K": 1200.0, "pressure_atm": 1.0}]
    observations.append(
        {
            "observation_id": "na_kyanite",
            "type": "psat_series",
            "locator": {"table": "I", "page": 3},
            "phase": "condensed_solid",
            "condensed_form": {"polymorph": "Kyanite"},
            "regime": "knudsen_effusion",
            "units": "atm",
            "values": {
                "quantity": "pure_Psat",
                "method_class": "measured_direct",
                "admission_status": "admitted",
                "series": [{"T_K": 1400.0, "pressure_atm": 3.0}],
            },
        }
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    unknown_rows = [
        o
        for o in result.observations.values()
        if "na_psat" in o.observation_id
    ]
    known_rows = [
        o
        for o in result.observations.values()
        if "na_kyanite" in o.observation_id
    ]
    assert unknown_rows
    assert known_rows
    for row in unknown_rows:
        assert row.identity.species.polymorph.is_unknown
        assert "not-a-real-form" in (row.identity.species.polymorph.reason or "")
    for row in known_rows:
        assert row.identity.species.polymorph.is_value
        assert row.identity.species.polymorph.value is Polymorph.KYANITE
    assert result.unrecognised_polymorphs == {"not-a-real-form": len(unknown_rows)}
    queued = [
        e
        for e in result.queue
        if e.observation_id is not None
        and "na_psat" in e.observation_id
        and "species.polymorph" in (e.axes or ())
    ]
    assert len(queued) == len(unknown_rows)
    assert all("not-a-real-form" in e.why for e in queued)
    report = (root / "data" / "battery" / "migration-report.md").read_text(encoding="utf-8")
    assert "## Unrecognised polymorph tokens" in report
    assert f"degradations: {len(unknown_rows)}" in report
    assert "`not-a-real-form`" in report
    known_queued = [
        e
        for e in result.queue
        if e.observation_id is not None
        and "na_kyanite" in e.observation_id
        and "species.polymorph" in (e.axes or ())
    ]
    assert known_queued == []


def test_g01_blank_phase_is_unknown_not_gas(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = ""
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)

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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
    obs = result.observations["kems_no_method"]
    exp = result.experiments[obs.experiment_id]
    assert exp.method.is_unknown
    assert obs.evidence.class_.is_unknown
    assert obs.evidence.class_.value is not EvidenceClass.FIGURE_ONLY


def test_g09_queue_ids_resolve_in_store(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = "silicate_melt"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert not str(obs.read_from).startswith("pdf:")
    assert "unknown" in str(obs.read_from)
    assert any("ocr" in (e.why or "").lower() or "source_path" in (e.why or "")
               for e in result.queue)
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()


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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.evidence.class_.is_value
    assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert not obs.derived_from


def test_j02_model_derived_without_parent_is_conditional_field(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"]["method_class"] = "model_derived"
    extract["species"]["Na"]["observations"][0]["values"].pop("series", None)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert not obs.derived_from
    assert obs.derivation is None
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    paths = [i.path for i in report.hard_issues]
    assert any(p.endswith(".derived_from") for p in paths), report.hard_issues
    assert any(p.endswith(".derivation") for p in paths), report.hard_issues
    assert all(
        i.reason.value == "conditional_field"
        for i in report.hard_issues
        if i.path.endswith(".derived_from") or i.path.endswith(".derivation")
    )
    assert any(
        "derived_from" in (e.axes or ()) or "derivation" in (e.axes or ())
        for e in result.queue
    )


def test_j02_source_stated_derived_from_is_stored(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    parent = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    parent["observation_id"] = "raw_parent"
    parent["values"]["method_class"] = "measured_direct"
    parent["values"].pop("series", None)
    child = yaml.safe_load(yaml.safe_dump(parent))
    child["observation_id"] = "derived_child"
    child["values"]["method_class"] = "model_derived"
    child["values"]["derived_from"] = "raw_parent"
    extract["species"]["Na"]["observations"] = [parent, child]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    child_obs = next(
        o for o in result.observations.values() if "derived_child" in o.observation_id
    )
    assert child_obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert child_obs.derived_from == ("fixture-source::raw_parent",)


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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    assert quantity_token(psat.identity) is not Quantity.P_SAT
    assert (psat.identity.quantity.reason or "") == (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )


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
    result = migrate(root, write=False)
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
    result = migrate(root, write=True)
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
        migrate(root, write=False)


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
    result = migrate(root, write=False)
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
    assert tiny.total_pressure_Pa.state.value.point == as_decimal("1.0e-05") * as_decimal(
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


def test_h13_canonical_phase_tokens_are_reviewed_identity() -> None:
    from simulator.battery.migrate import PHASE_MAP

    for spelling, token in (
        ("aq", Phase.AQ),
        ("glass", Phase.GLASS),
        ("supercooled_l", Phase.SUPERCOOLED_L),
        ("g", Phase.G),
        ("cr", Phase.CR),
        ("l", Phase.L),
    ):
        assert PHASE_MAP[spelling] is token
        mapped, why = map_phase(spelling)
        assert mapped.is_value and mapped.value is token and why is None
    mapped, why = map_phase("silicate_melt")
    assert mapped.is_unknown and why is not None


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


def test_g2_paren_phase_aliases_map_gas_and_condensed() -> None:
    mapped, why = map_phase("(g)")
    assert mapped.is_value and mapped.value is Phase.G and why is None
    mapped, why = map_phase("(c)")
    assert mapped.is_value and mapped.value is Phase.CR and why is None
    # Multi-phase spans stay unknown in this lane.
    mapped, why = map_phase("(c,l)")
    assert mapped.is_unknown and why == "(c,l)"
    mapped, why = map_phase("(c, l)")
    assert mapped.is_unknown and why == "(c, l)"


def test_g2_paren_phase_alias_mutation_proof() -> None:
    from simulator.battery import migrate as migrate_mod

    live_g, _ = map_phase("(g)")
    live_c, _ = map_phase("(c)")
    assert live_g.is_value and live_g.value is Phase.G
    assert live_c.is_value and live_c.value is Phase.CR

    saved_g = migrate_mod.PHASE_MAP.pop("(g)", None)
    saved_c = migrate_mod.PHASE_MAP.pop("(c)", None)
    try:
        mutant_g, why_g = map_phase("(g)")
        mutant_c, why_c = map_phase("(c)")
        assert mutant_g.is_unknown and "not in the closed automatic map" in (mutant_g.reason or "")
        assert mutant_c.is_unknown and "not in the closed automatic map" in (mutant_c.reason or "")
        assert why_g == "(g)" and why_c == "(c)"
    finally:
        if saved_g is not None:
            migrate_mod.PHASE_MAP["(g)"] = saved_g
        if saved_c is not None:
            migrate_mod.PHASE_MAP["(c)"] = saved_c

    restored_g, _ = map_phase("(g)")
    restored_c, _ = map_phase("(c)")
    assert restored_g.is_value and restored_g.value is Phase.G
    assert restored_c.is_value and restored_c.value is Phase.CR


def test_g2_paren_phase_migrate_kelley_record(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "kelley-1960-usbm-b584", "table-332.json")
    result = migrate(root, write=False)
    obs_list = [
        obs
        for oid, obs in result.observations.items()
        if "table-332" in oid or "kelley-1960-usbm-b584" in oid
    ]
    assert obs_list
    phases = [obs.identity.species.phase for obs in obs_list]
    assert all(phase.is_value and phase.value is Phase.G for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )


def test_h01_bare_series_T_P_without_units_stay_unknown(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = ""
    row["values"]["series"] = [{"T": 1200, "P": 1}]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
    result = migrate(root, write=False)
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
            if o.observation_id.startswith(f"fixture-source::{suffix}::")
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
    first_ids = {
        "bischof_2023_gao15_gamma_s1_1low_polytherm": "T=1586.4:h=d85a6d52fc92",
        "bischof_2023_gao15_gamma_s2_1low_isotherm": "T=1741.9:h=9b2744505ed4",
        "bischof_2023_ino15_gamma_s1_1low_polytherm": "T=1586.4:h=5d7ac09ddf55",
        "bischof_2023_ino15_gamma_s2_1low_isotherm": "T=1741.9:h=4913a4df11f4",
    }
    by_id = {o["observation_id"]: o for o in stored["observations"]}
    for suffix, gamma in first.items():
        oid = f"kems-137-bischof-2023::{suffix}::{first_ids[suffix]}"
        obs = by_id[oid]
        q = obs["identity"]["quantity"]
        assert q.get("value") == "activity_coefficient"
        assert Decimal(str(obs["value"]["point"])) == gamma
    source_gammas: list[Decimal] = []
    source_pressures: set[Decimal] = set()
    n_series_gamma = 0
    species = source.get("species") or {}
    for body in species.values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations") or []:
            values = row.get("values") or {}
            if values.get("quantity") != "activity_coefficient":
                continue
            if "gamma" in values:
                source_gammas.append(Decimal(str(values["gamma"])))
            for item in values.get("series") or []:
                if not isinstance(item, dict):
                    continue
                if "gamma" in item:
                    source_gammas.append(Decimal(str(item["gamma"])))
                    n_series_gamma += 1
                for pk in ("p_Ga_Pa", "p_In_Pa"):
                    if pk in item:
                        source_pressures.add(Decimal(str(item[pk])))
    stored_vals = []
    stored_series = 0
    for obs in stored["observations"]:
        q = obs.get("identity", {}).get("quantity", {})
        if q.get("value") != "activity_coefficient":
            continue
        if obs.get("value", {}).get("kind") != "point":
            continue
        stored_vals.append(Decimal(str(obs["value"]["point"])))
        if "::T=" in str(obs.get("observation_id") or ""):
            stored_series += 1
    assert n_series_gamma == 128
    assert stored_series == 128
    # Two preferred scalar γ averages were previously stored as T_range intervals.
    assert len(stored_vals) == 130
    gamma_set = set(source_gammas)
    for val in stored_vals:
        assert val in gamma_set
        assert val not in source_pressures


def _j01_series_fixture(quantity: str, series: list[dict]) -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = ""
    row["values"]["quantity"] = quantity
    row["values"]["series"] = series
    return extract


def test_j01_missing_declared_psat_does_not_take_gamma_or_value(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "pure_Psat", [{"T_K": 1200, "gamma": 0.06, "value": 0.7}]
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.point is None or obs.value.point != as_decimal("0.06")
    assert obs.value.point != as_decimal("0.7")
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_missing_declared_activity_does_not_take_gamma(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "activity", [{"T_K": 1200, "gamma": 0.06, "value": 0.7}]
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_missing_declared_alpha_does_not_take_gamma(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "evaporation_coefficient_alpha",
        [{"T_K": 1200, "gamma": 0.06, "value": 0.7}],
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_explicit_pressure_atm_control_still_lifts(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    assert all(quantity_token(p.identity) is Quantity.P_SAT for p in points)
    assert all(p.value.kind is ValueKind.POINT for p in points)
    values = sorted(p.value.point for p in points)
    assert values[0] == atm_to_pa("1")
    assert values[1] == atm_to_pa("2")


def test_j01_fedkin_alpha_series_not_mass_loss_rate(tmp_path: Path) -> None:
    src = REPO_ROOT / "data" / "literature" / "extracts" / "fedkin-grossman-ghiorso-2006.yaml"
    extract = yaml.safe_load(src.read_text(encoding="utf-8"))
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "extracts" / "fedkin-grossman-ghiorso-2006.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "data" / "literature" / "extracts" / "fixture-source.yaml").unlink()
    result = migrate(root, write=False)
    alpha_points = [
        o
        for o in result.observations.values()
        if "per_T_alpha_series::T=" in o.observation_id
    ]
    assert len(alpha_points) == 12
    for obs in alpha_points:
        assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        assert quantity_token(obs.identity) is not Quantity.MASS_LOSS_RATE
        assert obs.value.kind is ValueKind.POINT
    fe0 = next(
        o
        for o in alpha_points
        if "fe_hashimoto_langmuir_per_T_alpha_series::T=1973" in o.observation_id
    )
    assert fe0.value.point == as_decimal("0.23")
    assert float(fe0.identity.temperature_K.value) == 1973.0


# Source-contract tables for the series census. Duplicated here on purpose:
# the test must not import the production selector as its expected-value oracle.
_CENSUS_QUANTITY_ALIASES = {
    "pure_Psat": "p_sat",
    "vapor_pressure": "p_sat",
    "partial_pressure": "p_partial",
    "potassium_partial_pressure_as_published": "p_partial",
    "deltafG": "delta_fG",
    "delta_fG": "delta_fG",
    "delta_fG_kJ_mol": "delta_fG",
    "log10_Kf": "log10_Kf",
    "log10_kf": "log10_Kf",
    "activity": "activity",
    "activity_coefficient": "activity_coefficient",
    "activity_coefficient_this_work": "activity_coefficient",
    "wagner_interaction_parameter": "interaction_parameter",
    "literature_vaporization_coefficient": "evaporation_coefficient_alpha",
    "alpha": "evaporation_coefficient_alpha",
    "evaporation_coefficient_alpha": "evaporation_coefficient_alpha",
    "o2_yield": "o2_yield",
    "mass_loss_fraction": "mass_loss_fraction",
    "bulk_mass_loss_wt_pct": "mass_loss_fraction",
    "non_condensed_mass_loss_fraction": "mass_loss_fraction",
    "ion_current_ratio": "ion_intensity_ratio",
    "ion_intensity_ratio": "ion_intensity_ratio",
}
_CENSUS_CLOSED_QUANTITIES = {
    "p_sat",
    "p_partial",
    "p_reference",
    "log10_Kf",
    "activity",
    "activity_coefficient",
    "evaporation_coefficient_alpha",
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
    "fe3_fe2_ratio",
    "ion_intensity_ratio",
    "delta_fG",
    "H_minus_H298",
    "partial_molar_enthalpy",
    "enthalpy_of_vaporization_2nd_law",
    "enthalpy_of_vaporization_3rd_law",
    "cp",
    "S",
    "evaporation_rate",
    "mass_loss_rate",
    "wall_deposit_mass",
    "transition_temperature",
    "viscosity",
    "density",
    "electrical_conductivity",
    "isotope_delta",
    "condensate_composition",
    "liquidus_composition",
    "evolved_gas_yield",
    "ion_intensity",
    "interaction_parameter",
}
_CENSUS_TYPE_QUANTITY = {
    "psat_series": "p_sat",
    "gibbs_table": "delta_fG",
    "activity_coefficient": "activity_coefficient",
    "alpha": "evaporation_coefficient_alpha",
    "rate_series": "mass_loss_rate",
    "transition_point": "transition_temperature",
}
_CENSUS_UNIT_QUANTITY = {
    "dimensionless alpha vs t_k": "evaporation_coefficient_alpha",
    "dimensionless activity": "activity",
}
_CENSUS_PERCENT_FRACTION_QUANTITIES = {
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
}
_CENSUS_PRESSURE_FIELDS = (
    ("pressure_atm", "atm"),
    ("p_atm", "atm"),
    ("P_atm", "atm"),
    ("pressure_bar", "bar"),
    ("P_bar", "bar"),
    ("p_bar", "bar"),
    ("P_Pa", "Pa"),
    ("pressure_Pa", "Pa"),
    ("p_Pa", "Pa"),
    ("Pb_Torr", "Torr"),
    ("Pbar_Torr", "Torr"),
    ("Po_Torr", "Torr"),
)
_ATM = as_decimal("101325")
_BAR = as_decimal("100000")
_TORR = _ATM / as_decimal("760")


def _census_declared_quantity(obs_type: str | None, values: dict, units: str) -> str | None:
    raw = values.get("quantity") if isinstance(values, dict) else None
    if isinstance(raw, str) and raw in _CENSUS_QUANTITY_ALIASES:
        return _CENSUS_QUANTITY_ALIASES[raw]
    if isinstance(raw, str) and raw in _CENSUS_CLOSED_QUANTITIES:
        return raw
    if raw is None or raw == "":
        unit_key = str(units or "").strip().lower()
        if unit_key in _CENSUS_UNIT_QUANTITY:
            return _CENSUS_UNIT_QUANTITY[unit_key]
        if obs_type in _CENSUS_TYPE_QUANTITY:
            return _CENSUS_TYPE_QUANTITY[obs_type]
        return None
    return None


def _census_expected_point(item: dict, q_token: str | None, units: str):
    from decimal import Decimal, InvalidOperation

    def _num(raw):
        if raw is None or raw == "" or isinstance(raw, bool):
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            return None

    if q_token is None:
        return None
    if q_token == "activity_coefficient":
        if "gamma" in item:
            return _num(item.get("gamma"))
        if "activity_coefficient" in item:
            return _num(item.get("activity_coefficient"))
        return None
    if q_token == "activity":
        return _num(item["activity"]) if "activity" in item else None
    if q_token == "evaporation_coefficient_alpha":
        return _num(item["alpha"]) if "alpha" in item else None
    if q_token == "evaporation_rate":
        candidates = [
            (key, value)
            for key, value in item.items()
            if isinstance(key, str)
            and (key == q_token or key.startswith(f"{q_token}_"))
        ]
        if len(candidates) != 1:
            return None
        return _num(candidates[0][1])
    if q_token in _CENSUS_PERCENT_FRACTION_QUANTITIES:
        candidates = [
            (key, value)
            for key, value in item.items()
            if isinstance(key, str)
            and (
                (
                    q_token in {"mass_loss_fraction", "mass_loss_fraction_vs_T"}
                    and "mass_loss" in key.lower()
                )
                or (
                    q_token == "yield_fraction"
                    and key in {"yield_fraction", "mass_yield_percent"}
                )
                or (
                    q_token == "o2_yield"
                    and key in {"o2_yield", "fraction_of_feedstock_oxygen_percent"}
                )
            )
        ]
        if not candidates:
            return None
        key, raw = candidates[0]
        amount = _num(raw)
        if amount is None:
            return None
        key_lower = key.lower()
        unit_lower = str(units or "").strip().lower().replace(" ", "")
        if key_lower.endswith(("_pct", "_percent", "_wt_pct")) or unit_lower in {
            "percent",
            "pct",
            "wt_percent",
            "wt%",
            "wt_pct",
            "%",
        }:
            return amount / as_decimal("100")
        return amount
    if q_token == "delta_fG":
        if "delta_fG" in item:
            return _num(item.get("delta_fG"))
        if "value" in item:
            return _num(item.get("value"))
        return None
    if q_token in {"p_sat", "p_partial"}:
        for key, unit in _CENSUS_PRESSURE_FIELDS:
            if key not in item:
                continue
            amount = _num(item.get(key))
            if amount is None:
                return None
            if unit == "atm":
                return amount * _ATM
            if unit == "bar":
                return amount * _BAR
            if unit == "Torr":
                return amount * _TORR
            return amount
        if q_token == "p_partial":
            for key in ("p_Ga_Pa", "p_In_Pa", "p_O2_calc_Pa"):
                if key in item:
                    return _num(item.get(key))
        if "P" in item or "p" in item:
            amount = _num(item.get("P", item.get("p")))
            unit = str(units or "").strip().lower().replace(" ", "")
            if amount is None or not unit:
                return None
            if unit in {"pa", "pascal", "pascals"}:
                return amount
            if unit in {"atm", "atmosphere", "atmospheres"}:
                return amount * _ATM
            if unit in {"bar"}:
                return amount * _BAR
            if unit in {"torr", "mmhg"}:
                return amount * _TORR
            return None
        return None
    return None


def _series_census(extracts: Path, extracts_v2: Path) -> tuple[dict[str, int], list[str], int, int]:
    from decimal import Decimal

    census: dict[str, int] = {}
    mismatches: list[str] = []
    n_numeric = 0
    n_unavailable = 0
    for src_path in sorted(extracts.glob("*.yaml")):
        source = yaml.safe_load(src_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            continue
        store_path = extracts_v2 / src_path.name
        if not store_path.is_file():
            continue
        stored = yaml.safe_load(store_path.read_text(encoding="utf-8"))
        stored_observations = [
            o
            for o in (stored.get("observations") or [])
            if isinstance(o, dict)
        ]
        by_id = {
            o["observation_id"]: o
            for o in stored_observations
        }
        source_id = str(source.get("source_id") or src_path.stem)
        used_stable_ids: set[str] = set()
        species = source.get("species") or {}
        for body in species.values():
            if not isinstance(body, dict):
                continue
            for row in body.get("observations") or []:
                values = row.get("values") or {}
                series = values.get("series") if isinstance(values, dict) else None
                if not isinstance(series, list) or not series:
                    continue
                obs_type = row.get("type") if isinstance(row.get("type"), str) else None
                units = str(row.get("units") or "")
                q_token = _census_declared_quantity(obs_type, values, units)
                raw_id = str(row.get("observation_id") or "")
                for index, item in enumerate(series):
                    if not isinstance(item, dict):
                        continue
                    oid = f"{source_id}::{raw_id}::point:{index}"
                    expected = _census_expected_point(item, q_token, units)
                    stored_obs = by_id.get(oid)
                    if stored_obs is None:
                        stable_points = [
                            observation
                            for observation in stored_observations
                            if str(observation.get("observation_id") or "").startswith(
                                f"{source_id}::{raw_id}::"
                            )
                            and str(observation.get("observation_id") or "")
                            not in used_stable_ids
                        ]
                        if expected is not None:
                            stored_obs = next(
                                (
                                    observation
                                    for observation in stable_points
                                    if (observation.get("value") or {}).get("kind")
                                    == "point"
                                    and Decimal(
                                        str((observation.get("value") or {}).get("point"))
                                    )
                                    == expected
                                ),
                                None,
                            )
                        elif stable_points:
                            stored_obs = stable_points[0]
                    if stored_obs is None:
                        mismatches.append(f"missing stored point {oid}")
                        continue
                    used_stable_ids.add(str(stored_obs["observation_id"]))
                    stored_q = (stored_obs.get("identity") or {}).get("quantity") or {}
                    stored_val = stored_obs.get("value") or {}
                    if expected is None:
                        n_unavailable += 1
                        if stored_val.get("kind") == "point":
                            mismatches.append(
                                f"{oid} stored numeric {stored_val.get('point')} "
                                f"but declared quantity {q_token} has no source field"
                            )
                        continue
                    n_numeric += 1
                    label = q_token if q_token is not None else "unknown"
                    census[label] = census.get(label, 0) + 1
                    if stored_q.get("value") != label:
                        mismatches.append(
                            f"{oid} stored quantity {stored_q.get('value')!r} != declared {label}"
                        )
                    if stored_val.get("kind") != "point":
                        mismatches.append(
                            f"{oid} declared field present but stored {stored_val.get('kind')}"
                        )
                        continue
                    got = Decimal(str(stored_val.get("point")))
                    if got != expected:
                        mismatches.append(f"{oid} stored {got} != source {expected} for {label}")
    return census, mismatches, n_numeric, n_unavailable


def test_j01_store_census_series_numeric_matches_declared_field() -> None:
    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not extracts_v2.is_dir():
        pytest.skip("migrated store not generated yet")
    census, mismatches, n_numeric, n_unavailable = _series_census(extracts, extracts_v2)
    assert not mismatches, mismatches[:20]
    assert n_numeric == sum(census.values())
    assert census.get("activity_coefficient") == 128
    assert census.get("p_partial") == 18
    assert census.get("p_sat") == 21
    assert census.get("evaporation_coefficient_alpha") == 12
    # Re-pinned with the d-032 store regen. _series_census skips a source with no
    # extracts-v2 sibling, and the pre-regen store was missing 26 sources' derived
    # files, so their series went uncounted: evaporation_rate 21->25,
    # mass_loss_fraction 9->19, n_numeric 209->223. The counts moved because the
    # data became visible, not because a check was relaxed - mismatches stays 0.
    assert census.get("evaporation_rate") == 25
    assert census.get("mass_loss_fraction") == 19
    assert census.get("mass_loss_rate", 0) == 0
    assert n_numeric == 223, (n_numeric, census, n_unavailable)


def test_j01_declared_quantity_accepts_one_decorated_source_field() -> None:
    selection = select_declared_source(
        Quantity.EVAPORATION_RATE,
        "mol/s/m2",
        {"evaporation_rate_1200C": "4.6e-06"},
    )
    assert selection.amount == as_decimal("4.6e-06")
    assert selection.field_name == "evaporation_rate_1200C"


def test_k04_census_goes_red_when_stored_alpha_is_corrupted(tmp_path: Path) -> None:
    import shutil
    from decimal import Decimal

    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not extracts_v2.is_dir():
        pytest.skip("migrated store not generated yet")
    dest = tmp_path / "extracts-v2"
    shutil.copytree(extracts_v2, dest)
    fedkin = dest / "fedkin-grossman-ghiorso-2006.yaml"
    stored = yaml.safe_load(fedkin.read_text(encoding="utf-8"))
    n_mutated = 0
    for obs in stored.get("observations") or []:
        ident = obs.get("identity") or {}
        q = ident.get("quantity") or {}
        if q.get("value") != "evaporation_coefficient_alpha":
            continue
        t_state = ident.get("temperature_K") or {}
        try:
            t = Decimal(str(t_state.get("value")))
        except Exception:
            continue
        if t <= Decimal("1973"):
            continue
        val = obs.get("value") or {}
        if val.get("kind") != "point":
            continue
        val["point"] = str(Decimal(str(val["point"])) + 1)
        n_mutated += 1
    assert n_mutated == 9, n_mutated
    fedkin.write_text(yaml.safe_dump(stored, sort_keys=False), encoding="utf-8")
    _census, mismatches, _n_numeric, _n_unavailable = _series_census(extracts, dest)
    assert mismatches, "census must go red when stored alpha points are corrupted"


def _scalar_extract(*, quantity: str, units: str, values: dict, obs_type: str = "psat_series") -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["type"] = obs_type
    row["units"] = units
    row["values"] = values
    return extract


def test_k01_scalar_psat_does_not_take_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="",
        values={"quantity": "pure_Psat", "alpha": 0.23, "method_class": "measured_direct"},
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(o for o in result.observations.values() if "na_psat" in o.observation_id)
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.point is None
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_scalar_activity_does_not_take_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="",
        values={"quantity": "activity", "alpha": 0.23, "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_scalar_alpha_does_not_take_coefficient(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="evaporation_coefficient_alpha",
        units="",
        values={
            "quantity": "evaporation_coefficient_alpha",
            "activity_coefficient": 0.06,
            "method_class": "measured_direct",
        },
        obs_type="alpha",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_source_activity_is_quantity_activity(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="dimensionless activity",
        values={"activity": "7.19e-10", "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert quantity_token(obs.identity) is not Quantity.ACTIVITY_COEFFICIENT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("7.19e-10")


def test_k01_dimensionless_activity_field_without_activity_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="dimensionless",
        values={"activity": 1.0, "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("1")


def test_k01_tsaplin_store_activity_not_coefficient() -> None:
    path = REPO_ROOT / "data" / "literature" / "extracts-v2" / "kems-ms2000-044.yaml"
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = [
        o
        for o in stored.get("observations") or []
        if o.get("observation_id", "").endswith("ms2000_044_na2o_activity_xsio2_0805_t1473")
    ]
    assert len(rows) == 1
    obs = rows[0]
    q = (obs.get("identity") or {}).get("quantity") or {}
    assert q.get("value") == "activity"
    assert obs.get("value", {}).get("kind") == "point"
    assert as_decimal(obs["value"]["point"]) == as_decimal("7.19e-10")


def test_k01_value_constructions_live_inside_the_boundary() -> None:
    import ast

    from simulator.battery.migrate import BOUNDARY_INGEST_CALLERS

    src = (REPO_ROOT / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed_value = {
        "select_declared_source",
        "_unavailable_selection",
        "_point_selection",
        "_interval_selection",
        "_series_selection_from_items",
        "_emit_series_or_point",
        "_value_from_plain",
    }
    func_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            func_stack.append(node.name)
            self.generic_visit(node)
            func_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Name):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if node.value.id in {"Value", "select_declared_source"}:
                        assert False, (
                            f"alias {target.id} = {node.value.id} at line {node.lineno}"
                        )
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.value, ast.Name):
                assert node.value.id not in {"Value", "select_declared_source"}, (
                    f"annotated boundary alias at line {node.lineno}"
                )
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "Value":
                    name = f"Value.{node.func.attr}"
            elif isinstance(node.func, ast.Call):
                inner = node.func
                inner_name = None
                if isinstance(inner.func, ast.Name):
                    inner_name = inner.func.id
                elif isinstance(inner.func, ast.Attribute):
                    inner_name = inner.func.attr
                if inner_name in {"getattr", "globals"}:
                    assert False, (
                        f"getattr/globals constructor bypass at line {node.lineno}"
                    )
                if isinstance(inner.func, ast.Attribute) and inner.func.attr == "get":
                    receiver = inner.func.value
                    assert not (isinstance(receiver, ast.Call)
                                and isinstance(receiver.func, ast.Name)
                                and receiver.func.id == "globals"), (
                        f"globals().get constructor bypass at line {node.lineno}"
                    )
            elif isinstance(node.func, ast.Subscript):
                receiver = node.func.value
                assert not (isinstance(receiver, ast.Call)
                            and isinstance(receiver.func, ast.Name)
                            and receiver.func.id == "globals"), (
                    f"globals subscript constructor bypass at line {node.lineno}"
                )
            owner = func_stack[-1] if func_stack else "<module>"
            if name in {"Value", "point_of", "Value.point_of"}:
                assert owner in allowed_value, (
                    f"Value constructed in {owner}:{node.lineno} outside the boundary"
                )
            if name == "select_declared_source":
                from simulator.battery.migrate import _BOUNDARY_WRAPPERS

                assert owner in BOUNDARY_INGEST_CALLERS or owner in _BOUNDARY_WRAPPERS, (
                    f"select_declared_source called from unlisted {owner}:{node.lineno}"
                )
            self.generic_visit(node)

    Visitor().visit(tree)


def test_k01_boundary_records_ingest_callers(tmp_path: Path) -> None:
    from simulator.battery.migrate import (
        BOUNDARY_INGEST_CALLERS,
        reset_boundary_served,
        boundary_served_callers,
    )

    reset_boundary_served()
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="atm",
        values={
            "quantity": "pure_Psat",
            "series": [{"T_K": 1200, "pressure_atm": 1.0}],
            "method_class": "measured_direct",
        },
    )
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "ledger-unknown-phase",
                        "source_id": "fixture-source",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 1200,
                        "table_kJ_mol": 1,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {"fixture-source": {"citation": "Fixture"}},
                "cases": {
                    "c1": {
                        "source_id": "fixture-source",
                        "points": [
                            {
                                "species": "Na",
                                "coordinate": {"temperature_K": 1200},
                                "partial_pressure_pa": 1.0,
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "mre_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "m1": {
                        "paper_citation": {"title": "MRE"},
                        "cases": {
                            "one": {
                                "comparison_points": [
                                    {
                                        "observable_id": "mre_applied_charge_C",
                                        "expected_value": 1.0,
                                        "species": "O2",
                                    }
                                ]
                            }
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {"range": [0.011, 0.02]},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "vacuum_pyrolysis_measurements.v1",
                "measurements": {
                    "pomeroy_cardiff_2006_measurements": {
                        "evidence_class": "experiment-grade",
                        "paper_citation": {"label": "Pomeroy fixture"},
                        "conditions_reported": {
                            "feedstock": {"species": "MLS-1A"},
                            "peak_hold_temperature_C": {"reported_value": 1400},
                        },
                        "comparison_points": [
                            {
                                "observable_id": "pomeroy_non_condensed_mass_loss_fraction",
                                "expected_value": 0.0117,
                                "units": "mass_fraction",
                                "source_locator": {"source_location": "results"},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "refractory_vaporization_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "nist_janaf_named_nodes": {
                    "temperature_K": 1800,
                    "log10_kf": {"Al": {"value": 1.0, "table": "Al-005"}},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_compilation(
        root,
        "janaf-comp",
        "table-1",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "janaf-comp",
            "record_id": "table-1",
            "table": {
                "table_id": "Al-005",
                "standard_state_as_published": "0.1 MPa",
                "index_entry": {"formula": "Al", "state": "g"},
                "values": [
                    {
                        "temperature": {"value": 298.15},
                        "delta_fG": 0.0,
                        "log10_Kf": 0.0,
                    }
                ],
            },
        },
    )
    _write_compilation(
        root,
        "robie-comp",
        "atomic-weight-001",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-comp",
            "record_id": "atomic-weight-001",
            "record_kind": "atomic_weight",
            "rows": [{"atomic_weight": {"value": 227}}],
        },
    )
    migrate(root, write=False)
    served = {name for _file, _line, name in boundary_served_callers()}
    assert served
    unexpected = served - BOUNDARY_INGEST_CALLERS
    assert not unexpected, unexpected
    missing = BOUNDARY_INGEST_CALLERS - served
    assert not missing, missing


def test_k02_t_range_is_domain_not_value(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="",
        values={
            "quantity": "pure_Psat",
            "T_range_K": [1200, 1300],
            "method_class": "measured_direct",
        },
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.interval_low is None
    reason_blob = " ".join(
        [
            obs.value.unavailable_reason or "",
            getattr(obs.identity.temperature_K, "reason", None) or "",
            " ".join(e.why or "" for e in result.queue),
        ]
    )
    assert "1200" in reason_blob and "1300" in reason_blob
    assert any("value" in (e.axes or ()) for e in result.queue)
    assert any("temperature_K" in (e.axes or ()) for e in result.queue)


def test_k02_zr_th_and_pending_anchors_are_unavailable_domains() -> None:
    cases = [
        ("ref-032-zr.yaml", "anchor_Zr_pure_Psat", "1800", "2500"),
        ("ref-032-th.yaml", "anchor_Th_pure_Psat", "1800", "2500"),
        ("pending-bi2o3-kems.yaml", "anchor_Bi2O3_pure_Psat", "1163", "1400"),
        ("pending-mgcl2-measured-vp.yaml", "anchor_MgCl2_pure_Psat", "1100", "1700"),
        ("pending-dual-primary-oso4.yaml", "anchor_OsO4_pure_Psat", "298", "400"),
        ("pending-teo2-vp.yaml", "anchor_TeO2_pure_Psat", "884", "987"),
    ]
    for fname, local_id, lo, hi in cases:
        path = REPO_ROOT / "data" / "literature" / "extracts-v2" / fname
        if not path.is_file():
            pytest.skip("migrated store not generated yet")
        stored = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = [
            o
            for o in stored.get("observations") or []
            if o.get("observation_id", "").endswith(local_id)
        ]
        assert len(rows) == 1, fname
        obs = rows[0]
        assert obs.get("value", {}).get("kind") == "unavailable", fname
        blob = yaml.safe_dump(obs)
        assert lo in blob and hi in blob, fname


def test_k03_ledger_missing_phase_is_queued(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "ledger-unknown-phase",
                        "source_id": "fixture-source",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 1200,
                        "table_kJ_mol": 1,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["ledger-unknown-phase"]
    assert obs.identity.species.phase.is_unknown
    phase_entries = [
        e
        for e in result.queue
        if e.observation_id == "ledger-unknown-phase" and "phase" in (e.axes or ())
    ]
    assert phase_entries
    assert any("phase" in (e.why or "").lower() for e in phase_entries)


def _quantity_state(obs) -> tuple[object, str | None]:
    ident = obs.identity
    token = quantity_token(ident)
    reason = None
    q = ident.quantity
    if hasattr(q, "reason"):
        reason = q.reason
    return token, reason


def _queue_blob(result, obs_id: str) -> str:
    return " ".join(
        e.why or ""
        for e in result.queue
        if e.observation_id == obs_id or (e.observation_id or "").endswith(obs_id)
    )


def test_l01_type_does_not_assign_contradicted_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={
            "method_class": "model_derived",
            "olette_alpha_theoretical": 1095,
            "not_hkl_langmuir_coefficient": True,
            "note": "Not an experimental HKL coefficient",
        },
        obs_type="alpha",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = (
        "homma_1966_mn_olette_theoretical_quoted_deep"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, reason = _quantity_state(obs)
    assert token is None
    blob = " ".join(filter(None, [reason, _queue_blob(result, obs.observation_id)]))
    assert "not_hkl_langmuir_coefficient" in blob or "HKL" in blob
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_gibbs_type_does_not_assign_dissociation_note(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="kJ/mol and J/(mol·K) for formation; over_R in kK as published",
        values={
            "gas_species": "EuO",
            "note": "dissociation energies of gaseous REE monoxides; numeric D0 not transcribed",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, reason = _quantity_state(obs)
    assert token is None
    blob = " ".join(filter(None, [reason, _queue_blob(result, obs.observation_id)]))
    assert "dissociation" in blob.lower()
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_gibbs_type_does_not_assign_vapor_pressure_equations(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="kJ/mol and J/(mol·K) for formation; over_R in kK as published",
        values={
            "note": "equations for partial vapor pressures over VO; numeric A,B not recovered",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_activity_type_does_not_assign_ordering_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless ordering (not a numeric gamma)",
        values={"semantics": "bound_not_point_ordering", "speciation_note": "VO(g) > V(g)"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_rate_type_does_not_assign_partial_pressure_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="as published (partial pressure; Fig. 5 lg P scale)",
        values={"gas_species": "SiO(g)", "semantics": "bound_not_point_ordering"},
        obs_type="rate_series",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_alpha_field_is_not_hkl_when_flagged(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={
            "alpha": 115,
            "not_hkl_langmuir_coefficient": True,
            "method_class": "measured_direct",
        },
        obs_type="alpha",
    )
    extract["species"]["Na"]["observations"][0]["regime"] = (
        "olette_relative_evaporation_coefficient"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_janaf_evaluator_gibbs_table_stays_delta_fg(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="NASA CEA polynomial",
        values={
            "evaluator_family": "nasa_cea_9",
            "reference_pressure_Pa": 100000.0,
            "segments": [{"T_min_K": 300.0, "T_max_K": 1000.0}],
            "method_class": "compilation_calculated_table",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is Quantity.DELTA_FG


def test_l01_costa_olivine_alpha_stays_evaporation_coefficient(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={"alpha": 0.02, "method_class": "measured_direct", "gas_species": "Fe(g)"},
        obs_type="alpha",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("0.02")


def test_l01_map_quantity_direct_witnesses() -> None:
    cases = [
        (
            "alpha",
            {"not_hkl_langmuir_coefficient": True, "note": "Not an experimental HKL coefficient"},
            "dimensionless",
            None,
        ),
        (
            "gibbs_table",
            {"note": "dissociation energies of gaseous REE monoxides including EuO"},
            "kJ/mol and J/(mol·K) for formation; over_R in kK as published",
            None,
        ),
        (
            "gibbs_table",
            {"note": "equations for partial vapor pressures over VO"},
            "kJ/mol and J/(mol·K) for formation; over_R in kK as published",
            None,
        ),
        (
            "activity_coefficient",
            {"semantics": "bound_not_point_ordering"},
            "dimensionless ordering (not a numeric gamma)",
            None,
        ),
        (
            "rate_series",
            {"semantics": "bound_not_point_ordering"},
            "partial pressure; Fig. 5 lg P scale",
            None,
        ),
    ]
    for obs_type, values, units, expected in cases:
        state, reason = map_quantity(obs_type, values, units=units)
        assert not state.is_value, (obs_type, state, reason)
        assert reason

    ok, _reason = map_quantity(
        "gibbs_table",
        {"evaluator_family": "nasa_cea_9", "reference_pressure_Pa": 100000, "segments": [{}]},
        units="NASA CEA polynomial",
    )
    assert ok.is_value and ok.value is Quantity.DELTA_FG
    ok, _reason = map_quantity("alpha", {"alpha": 0.02}, units="dimensionless")
    assert ok.is_value and ok.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    bad, reason = map_quantity(
        "alpha",
        {"alpha": 115, "not_hkl_langmuir_coefficient": True},
        units="dimensionless",
        row={"regime": "olette_relative_evaporation_coefficient"},
    )
    assert not bad.is_value
    assert reason and "not_hkl" in reason or "Olette" in (reason or "") or "outside" in (reason or "")


_TYPE_CONTRADICTIONS = [
    ("ames-walsh-white-1967.yaml", "Ames67_EuO_dissociation"),
    ("ames-walsh-white-1967.yaml", "Ames67_YbO_dissociation"),
    ("banchor-matsui-naito-1986.yaml", "Ban86_equations"),
    ("datz-and-smith-1961.yaml", "Datz1961_TableII_Kd"),
    ("datz-and-smith-1961.yaml", "JANAF1998_WebBook_Shomate"),
    ("datz-and-smith-1961.yaml", "Datz1961_TableII_Kd_nacl_side"),
    ("habermann-daane-1964.yaml", "Hab64_Eu_metal_third_law"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_theoretical_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_theoretical_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_theoretical_quoted_deep"),
    ("kems-011-wetzel-gail-2013.yaml", "wetzel_gail_2013_sio_growth_alpha_arrhenius"),
    ("kems-011-wetzel-gail-2013.yaml", "wetzel_gail_2013_sio_growth_class_b1"),
    ("kems-022-demaria-1971.yaml", "demaria_1971_sio_lunar_basalt_kems_main_cell"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_T1_P4O10_KEMS_dominance"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_sf18_speciation_VO_VO2"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_Eu2O3_window"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_Yb2O3_window"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si_activation_enthalpy"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si_equilibrium_enthalpy_and_barrier"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si2c_formation_enthalpies"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_activation_enthalpy"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_equilibrium_enthalpy_and_barrier"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_formation_enthalpies"),
    ("nist-webbook.yaml", "Rau74_critical_constants"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_diopside_calorimetry_tables_1_7"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_albite_analbite_calorimetry_tables_1_7"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_sanidine_calorimetry_tables_3_7"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_nepheline_calorimetry_tables_3_7"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_anorthite_calorimetry_tables_1_7"),
    ("wetzel-gail-2013-sio-arrhenius.yaml", "wetzel_gail_2013_sio_arrhenius"),
]
_FIELD_ALPHA_CONTRADICTIONS = [
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_alpha_exp_table1"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_class_fence_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_alpha_exp_table2"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_class_fence_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_alpha_exp_table3"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_class_fence_quoted_deep"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_mn_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_cu_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_sn_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_cr_olette_alpha_table3"),
]


def _extract_observation(fname: str, observation_id: str) -> dict:
    source = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / fname).read_text(encoding="utf-8")
    )
    for body in (source.get("species") or {}).values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations") or []:
            if isinstance(row, dict) and row.get("observation_id") == observation_id:
                return row
    raise AssertionError(f"missing extract row {fname}::{observation_id}")


def test_l05c1_type_contradictions_are_quantity_unknown() -> None:
    assert len(_TYPE_CONTRADICTIONS) == 42
    for fname, oid in _TYPE_CONTRADICTIONS:
        row = _extract_observation(fname, oid)
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        state, reason = map_quantity(
            row.get("type"), values, units=row.get("units"), row=row
        )
        assert not state.is_value, (oid, state, reason)
        assert reason


def test_l05c1_olette_alpha_fields_are_quantity_unknown() -> None:
    assert len(_FIELD_ALPHA_CONTRADICTIONS) == 16
    for fname, oid in _FIELD_ALPHA_CONTRADICTIONS:
        row = _extract_observation(fname, oid)
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        state, reason = map_quantity(
            row.get("type"), values, units=row.get("units"), row=row
        )
        assert not state.is_value, (oid, state, reason)
        assert reason


def test_l05c1_alpha_outside_unit_interval_is_unknown() -> None:
    state, reason = map_quantity("alpha", {"alpha": 115}, units="dimensionless")
    assert not state.is_value
    assert reason and "outside" in reason
    state, _reason = map_quantity("alpha", {"alpha": 0.02}, units="dimensionless")
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA


def _write_compilation(root: Path, source_id: str, record_id: str, doc: dict) -> Path:
    path = (
        root
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "auxiliary"
        / f"{record_id}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_l05g0_atomic_weight_rows_are_not_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_compilation(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "atomic-weight-001",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-hemingway-1995-usgs-b2131",
            "record_id": "atomic-weight-001",
            "record_kind": "atomic_weight",
            "formula": "Ac",
            "rows": [{"atomic_weight": {"value": 227}}],
        },
    )
    result = migrate(root, write=False)
    obs = result.observations["robie-hemingway-1995-usgs-b2131:atomic-weight-001"]
    token, reason = _quantity_state(obs)
    assert token is None
    assert reason and "closed quantity" in reason
    assert any(
        e.observation_id == obs.observation_id and "quantity" in (e.axes or ())
        for e in result.queue
    )


def test_l05g0_janaf_style_delta_fg_cells_stay_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_compilation(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "al-ht",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-hemingway-1995-usgs-b2131",
            "record_id": "al-ht",
            "table_kind": "high_temperature",
            "formula": "Al",
            "rows": [
                {
                    "temperature": {"value": 298.15},
                    "delta_f_G": {"value": 0.0},
                    "delta_fG": 0.0,
                }
            ],
        },
    )
    result = migrate(root, write=False)
    obs = next(
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith("robie-hemingway-1995-usgs-b2131:al-ht")
    )
    token, _reason = _quantity_state(obs)
    assert token is Quantity.DELTA_FG


def test_l05g1a_qualified_activity_token_lifts_activity(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity_CsBO2",
        units="dimensionless",
        values={
            "quantity": "activity_CsBO2",
            "activity": 4.0e-6,
            "T_K": 1200.0,
            "method_class": "measured_direct",
        },
        obs_type="activity_coefficient",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = (
        "plante_hastie_1983_csbo2_activity_1200K"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert quantity_token(obs.identity) is not Quantity.ACTIVITY_COEFFICIENT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("4e-06")
    assert obs.identity.species.formula == "CsBO2"


def test_l05g1a_table_qualifier_leaves_reference_state_unknown(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity_vapor_reference_eq7",
        units="dimensionless",
        values={
            "quantity": "activity_vapor_reference_eq7",
            "activity": 0.12,
            "method_class": "measured_direct",
        },
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.POINT
    assert obs.identity.reference_state is not None
    assert obs.identity.reference_state.is_unknown
    assert "vapor_reference_eq7" in (obs.identity.reference_state.reason or "")
    assert any("reference_state" in (e.axes or ()) for e in result.queue)


def test_l05g1a_does_not_cross_condensation_or_log_pressure() -> None:
    state, reason = map_quantity(
        None, {"quantity": "condensation_coefficient", "activity": 0.2}
    )
    assert not state.is_value
    state, reason = map_quantity(
        None,
        {"quantity": "log10_Psat_over_P0", "table_kJ_mol": 316.034, "activity": 1},
    )
    assert not state.is_value
    assert "unsupported" in (reason or "")


def test_l02_value_k_lifts_transition_temperature(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="K",
        values={
            "property_kind": "melting_point",
            "value_K": 370.96,
            "phase_from": "solid",
            "phase_to": "liquid",
            "pressure_basis": "near 1 atm",
            "method_class": "compilation_calculated_table",
        },
        obs_type="transition_point",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = "Na_melting_point"
    boil = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    boil["observation_id"] = "Na_normal_boiling_point"
    boil["values"] = {
        "property_kind": "normal_boiling_point",
        "value_K": 1156.0,
        "pressure_basis_Pa": 101325,
        "method_class": "compilation_calculated_table",
    }
    extract["species"]["Na"]["observations"].append(boil)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    melt = result.observations["fixture-source::Na_melting_point"]
    assert quantity_token(melt.identity) is Quantity.TRANSITION_TEMPERATURE
    assert melt.value.kind is ValueKind.POINT
    assert melt.value.point == as_decimal("370.96")
    assert melt.identity.subtype.is_value and melt.identity.subtype.value == "melting_point"
    assert melt.identity.temperature_K is None or melt.identity.temperature_K.is_not_applicable
    assert melt.identity.total_pressure_Pa.is_unknown
    assert any(
        e.observation_id == melt.observation_id and "total_pressure_Pa" in (e.axes or ())
        for e in result.queue
    )
    nbp = result.observations["fixture-source::Na_normal_boiling_point"]
    assert nbp.value.kind is ValueKind.POINT
    assert nbp.value.point == as_decimal("1156.0")
    assert nbp.identity.subtype.value == "normal_boiling_point"
    assert nbp.identity.total_pressure_Pa.is_value
    assert nbp.identity.total_pressure_Pa.value == as_decimal("101325")


def test_l05g0_rows_list_alone_does_not_name_delta_fg() -> None:
    state, reason = compilation_quantity_from_record(
        {"record_kind": "atomic_weight", "rows": [{"atomic_weight": {"value": 227}}]}
    )
    assert not state.is_value
    assert reason == "printed compilation columns are not mapped to a closed quantity"
    state, reason = compilation_quantity_from_record(
        {
            "table_kind": "high_temperature",
            "rows": [{"temperature": {"value": 1100}, "delta_f_G": {"value": -100.0}}],
        }
    )
    assert state.is_value and state.value is Quantity.DELTA_FG


def test_g1_formation_enthalpy_maps_to_delta_fh_quantity() -> None:
    """Printed ATcT / NASA-Glenn formation enthalpy is Quantity.DELTA_FH."""
    atct = {
        "record_id": "atct-fixture",
        "formula": "H2",
        "phase": "g",
        "formation_enthalpy_298_15_K_as_published": "0",
        "units_as_published": "kJ/mol",
    }
    state, reason = compilation_quantity_from_record(atct)
    assert state.is_value and state.value is Quantity.DELTA_FH and reason is None
    sel = select_declared_source(state, None, atct)
    assert sel.amount == as_decimal("0")
    assert sel.value.kind is ValueKind.POINT
    assert sel.field_name == "formation_enthalpy_298_15_K_as_published"

    nasa = {
        "record_id": "NG-fixture",
        "formula": "CO2",
        "phase": "gas",
        "delta_f_H_298_15": {
            "as_published": "-393510.000",
            "value": -393510.0,
            "units_as_published": "J/mol",
        },
        "intervals": [{"range_as_published": "200 1000"}],
    }
    state, reason = compilation_quantity_from_record(nasa)
    assert state.is_value and state.value is Quantity.DELTA_FH and reason is None
    sel = select_declared_source(state, None, nasa)
    assert sel.amount == as_decimal("-393.51")
    assert sel.value.kind is ValueKind.POINT
    assert sel.field_name == "delta_f_H_298_15"


def test_g1_delta_fh_migrate_admits_point_without_token_queue(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "atct", "atct-1.222-0001.json")
    _copy_compilation_record(root, "nasa-glenn", "NG-0467.json")
    result = migrate(root, write=False)

    atct_obs = [
        obs
        for oid, obs in result.observations.items()
        if "atct-1.222-0001" in oid
    ]
    assert atct_obs
    assert all(quantity_token(obs.identity) is Quantity.DELTA_FH for obs in atct_obs)
    assert all(obs.value.kind is ValueKind.POINT for obs in atct_obs)
    assert all(obs.value.point == as_decimal("0") for obs in atct_obs)

    ng_obs = [
        obs
        for oid, obs in result.observations.items()
        if "NG-0467" in oid
    ]
    assert ng_obs
    assert all(quantity_token(obs.identity) is Quantity.DELTA_FH for obs in ng_obs)
    assert all(obs.value.kind is ValueKind.POINT for obs in ng_obs)
    assert all(obs.value.point == as_decimal("-103.772885") for obs in ng_obs)

    for entry in result.queue:
        assert "not a v2.1 Quantity token" not in (entry.why or "")


def test_g1_delta_fh_mapping_mutation_proof() -> None:
    """Removing formation-enthalpy cell maps restores the pre-fix unknown reason."""
    from simulator.battery import migrate as migrate_mod

    doc = {
        "record_id": "atct-mut",
        "formation_enthalpy_298_15_K_as_published": "12.5",
    }
    live, live_reason = compilation_quantity_from_record(doc)
    assert live.is_value and live.value is Quantity.DELTA_FH and live_reason is None

    saved = dict(migrate_mod._COMPILATION_CELL_QUANTITY)
    try:
        for key in (
            "formation_enthalpy_298_15_K_as_published",
            "delta_f_H_298_15",
            "delta_f_H",
            "delta_fH",
            "deltafH",
            "formation_enthalpy",
        ):
            migrate_mod._COMPILATION_CELL_QUANTITY.pop(key, None)
        mutant, mutant_reason = compilation_quantity_from_record(doc)
        assert not mutant.is_value
        assert mutant_reason == "printed compilation columns are not mapped to a closed quantity"
    finally:
        migrate_mod._COMPILATION_CELL_QUANTITY.clear()
        migrate_mod._COMPILATION_CELL_QUANTITY.update(saved)

    restored, restored_reason = compilation_quantity_from_record(doc)
    assert restored.is_value and restored.value is Quantity.DELTA_FH and restored_reason is None
def test_g5_compilation_column_series_maps_pankratz_and_kelley() -> None:
    """Multi-column Cp/S/H/ΔHf/ΔGf censuses explode to one series per Quantity."""
    pankratz = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1447.json"
        ).read_text(encoding="utf-8")
    )
    series = compilation_column_series_from_record(pankratz)
    quantities = [item.quantity for item in series]
    assert quantities == [
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    ]
    assert all(item.value.kind is ValueKind.SERIES for item in series)
    assert all(len(item.value.series or ()) == 4 for item in series)
    # Never first-numeric-cell: T column stays out of the emitted quantities.
    assert Quantity.TRANSITION_TEMPERATURE not in quantities
    cp = next(item for item in series if item.quantity is Quantity.CP)
    assert cp.value.series[0] == (as_decimal("298"), as_decimal("317.984"))

    kelley = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0007.json"
        ).read_text(encoding="utf-8")
    )
    k_series = compilation_column_series_from_record(kelley)
    k_by_key = {item.series_key: item for item in k_series}
    assert "cp_temperature_grid" in k_by_key
    assert k_by_key["cp_temperature_grid"].quantity is Quantity.CP
    assert k_by_key["cp_temperature_grid"].value.kind is ValueKind.SERIES
    assert len(k_by_key["cp_temperature_grid"].value.series or ()) == 7
    assert k_by_key["entropy_third_law"].quantity is Quantity.S
    assert k_by_key["entropy_third_law"].value.kind is ValueKind.POINT
    assert k_by_key["entropy_third_law"].value.point == as_decimal("111.21072")
    assert k_by_key["entropy_recommended"].value.point == as_decimal("111.21072")


def test_pankratz_source_units_convert_and_missing_units_refuse() -> None:
    page = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1987-usbm-b689/records/page-0003.json"
        ).read_text(encoding="utf-8")
    )
    by_quantity = {
        item.quantity: item.value.series[0][1]
        for item in compilation_column_series_from_record(page)
    }
    assert by_quantity[Quantity.CP] == as_decimal("35.9824")
    assert by_quantity[Quantity.DELTA_FH] == as_decimal("393.296")

    for record in ("table-0001.json", "table-0002.json"):
        doc = json.loads(
            (
                REPO_ROOT
                / "data/literature/compilations/pankratz-1984-usbm-b677/records"
                / record
            ).read_text(encoding="utf-8")
        )
        assert compilation_column_series_from_record(doc) == ()

    selection = select_declared_source(
        Quantity.CP, None, {"cp": 25}
    )
    assert selection.value.kind is ValueKind.UNAVAILABLE
    assert selection.value.unavailable_reason == (
        "missing printed unit for cp; refusing ungrounded numeric value"
    )


def test_joule_heat_capacity_unit_is_identity() -> None:
    selection = select_declared_source(Quantity.CP, "J/mol/K", {"cp": 25})
    assert selection.value.kind is ValueKind.POINT
    assert selection.value.point == as_decimal("25")


def test_g5_compilation_column_explode_migrate(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "pankratz-1984-usbm-b677", "table-1447.json")
    _copy_compilation_record(root, "kelley-king-1961-usbm-b592", "table-006-0007.json")
    _copy_compilation_record(root, "kelley-king-1961-usbm-b592", "table-006-0923.json")
    result = migrate(root, write=False)

    pank = [
        obs
        for oid, obs in result.observations.items()
        if "table-1447" in oid
    ]
    pank_q = {quantity_token(obs.identity) for obs in pank}
    assert pank_q == {
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    }
    assert all(obs.value.kind is ValueKind.SERIES for obs in pank)
    # Identity assertion: ΔHf series is not the first numeric column (T or Cp).
    dh = next(obs for obs in pank if quantity_token(obs.identity) is Quantity.DELTA_FH)
    assert dh.value.series is not None
    # table-1447 row0 ΔHf is column index 4, not the first numeric cell (T=298 or Cp=76).
    assert dh.value.series[0] == (as_decimal("298"), as_decimal("-2895.516280"))

    kel = [
        obs
        for oid, obs in result.observations.items()
        if "table-006-0007" in oid
    ]
    kel_q = {quantity_token(obs.identity) for obs in kel}
    assert Quantity.CP in kel_q and Quantity.S in kel_q
    assert any(
        obs.value.kind is ValueKind.SERIES and quantity_token(obs.identity) is Quantity.CP
        for obs in kel
    )
    assert sum(1 for obs in kel if quantity_token(obs.identity) is Quantity.S) == 2

    kel19 = [
        obs
        for oid, obs in result.observations.items()
        if "table-006-0923" in oid
    ]
    assert any(quantity_token(obs.identity) is Quantity.CP for obs in kel19)
    assert any(
        quantity_token(obs.identity) is Quantity.S and "entropy_spectrographic" in oid
        for oid, obs in result.observations.items()
        if "table-006-0923" in oid
    )

    for entry in result.queue:
        why = entry.why or ""
        if "table-1447" in (entry.observation_id or "") or "table-006-0007" in (
            entry.observation_id or ""
        ):
            assert "source column census" not in why
            assert "printed compilation columns are not mapped" not in why


def test_g5_compilation_column_mapping_mutation_proof() -> None:
    """Clearing label maps restores the pre-fix empty explode / census path."""
    from simulator.battery import migrate as migrate_mod

    doc = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1447.json"
        ).read_text(encoding="utf-8")
    )
    live = compilation_column_series_from_record(doc)
    assert len(live) == 5
    assert {item.quantity for item in live} == {
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    }

    saved_heading = dict(migrate_mod._COMPILATION_HEADING_QUANTITY)
    saved_cell = dict(migrate_mod._COMPILATION_CELL_QUANTITY)
    try:
        migrate_mod._COMPILATION_HEADING_QUANTITY.clear()
        # Keep non-G5 cell keys so unrelated paths stay intact; drop multi-column keys.
        for key in list(migrate_mod._COMPILATION_CELL_QUANTITY):
            if key in {
                "cp",
                "heat_capacity",
                "entropy",
                "enthalpy_increment",
                "delta_h",
                "delta_g",
                "log_k",
                "cp_10_k",
                "cp_25_k",
                "cp_50_k",
                "cp_100_k",
                "cp_150_k",
                "cp_200_k",
                "cp_298_15_k",
                "entropy_third_law",
                "entropy_spectrographic_or_molecular_constants",
                "entropy_other_sources",
                "entropy_recommended",
                "delta_f_H",
                "delta_fH",
                "deltafH",
                "formation_enthalpy",
            }:
                migrate_mod._COMPILATION_CELL_QUANTITY.pop(key, None)
        mutant = compilation_column_series_from_record(doc)
        assert mutant == ()
        # First-numeric-cell mutant would invent a quantity from T/Cp; we require empty.
        assert not any(item.quantity is Quantity.CP for item in mutant)
    finally:
        migrate_mod._COMPILATION_HEADING_QUANTITY.clear()
        migrate_mod._COMPILATION_HEADING_QUANTITY.update(saved_heading)
        migrate_mod._COMPILATION_CELL_QUANTITY.clear()
        migrate_mod._COMPILATION_CELL_QUANTITY.update(saved_cell)

    restored = compilation_column_series_from_record(doc)
    assert len(restored) == 5




def test_l05c1_costa_control_is_not_condensation() -> None:
    row = _extract_observation(
        "costa-jacobson-2015.yaml", "costa_jacobson_2015_fe_olivine_kems"
    )
    values = row.get("values") if isinstance(row.get("values"), dict) else {}
    state, reason = map_quantity(
        row.get("type"), values, units=row.get("units"), row=row
    )
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert reason is None


def test_l03_per_mol_o2_ledger_lifts_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "comparison_quantity": "delta_fG_kJ_mol",
                "points": [
                    {
                        "key": "janaf::Al-096:T=1100::ellingham::delta_fG_kJ_per_mol_O2",
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "delta_fG_kJ_per_mol_O2",
                        "temperature_K": 1100,
                        "table_kJ_mol": -885.524,
                        "note": "rescaled 2*dfG/n_O with n_O=3.0 via OXIDE_TO_METAL['Al2O3'] -> Al",
                        "provenance_class": "independent_tabulation",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations[
        "janaf::Al-096:T=1100::ellingham::delta_fG_kJ_per_mol_O2"
    ]
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert obs.identity.per.is_value
    from simulator.battery.enums import PerBasis

    assert obs.identity.per.value is PerBasis.MOL_O2
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("-885.524")
    assert obs.derivation is not None
    assert "rescaled" in obs.derivation.relation
    assert obs.evidence.class_.is_value
    from simulator.battery.enums import EvidenceClass

    assert obs.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED


def test_l04_log10_psat_over_p0_is_not_a_pressure(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::Al-005:T=100::log10_Psat_over_P0",
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 100,
                        "table_kJ_mol": 316.034,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["janaf::Al-005:T=100::log10_Psat_over_P0"]
    assert quantity_token(obs.identity) is not Quantity.P_SAT
    assert quantity_token(obs.identity) is not Quantity.P_PARTIAL
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any(reason == (e.why or "") for e in result.queue if e.observation_id == obs.observation_id)




def test_g3_vaporization_note_lifts_reaction_and_delta_fg() -> None:
    """Printed dfG(g)-dfG(cr|l) note lifts reaction identity onto delta_fG."""
    note_cr = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(cr)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-002"
    )
    lifted = lift_vaporization_reaction_from_ledger_note("Al", note_cr)
    assert lifted is not None
    reaction, p0 = lifted
    assert p0 == as_decimal("100000")
    assert isinstance(reaction, Reaction)
    phases = {term.species.phase.value for term in reaction.terms}
    assert phases == {Phase.G, Phase.CR}
    coeffs = {
        term.species.phase.value: term.coefficient for term in reaction.terms
    }
    assert coeffs[Phase.G] == 1
    assert coeffs[Phase.CR] == -1

    note_l = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(l)]/(R T); P0=100000 Pa"
    )
    lifted_l = lift_vaporization_reaction_from_ledger_note("Al", note_l)
    assert lifted_l is not None
    phases_l = {term.species.phase.value for term in lifted_l[0].terms}
    assert phases_l == {Phase.G, Phase.L}

    assert lift_vaporization_reaction_from_ledger_note("Al", "janaf p°=0.1 MPa") is None
    assert lift_vaporization_reaction_from_ledger_note("Al", "") is None


def test_g3_vaporization_ledger_admits_delta_fg_with_reaction(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    note = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(cr)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-002; "
        "a_melt=1; mechanism=compilation_disagreement"
    )
    oid = "janaf::Al-005:T=100::vapour_rail_psat::log10_Psat_over_P0"
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": oid,
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 100,
                        "table_kJ_mol": 316.034,
                        "note": note,
                        "provenance_class": "independent_tabulation",
                        "engine_channel": "vapour_rail_psat",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations[oid]
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert quantity_token(obs.identity) is not Quantity.P_SAT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("316.034")
    assert obs.identity.species.phase.is_value
    assert obs.identity.species.phase.value is Phase.G
    assert obs.identity.reaction is not None and obs.identity.reaction.is_value
    reaction = obs.identity.reaction.value
    assert reaction is not None
    phases = {term.species.phase.value for term in reaction.terms}
    assert phases == {Phase.G, Phase.CR}
    assert obs.identity.per is not None and obs.identity.per.is_value
    assert obs.identity.per.value is PerBasis.MOL_SPECIES
    assert obs.identity.standard_pressure_Pa is not None
    assert obs.identity.standard_pressure_Pa.is_value
    assert obs.identity.standard_pressure_Pa.value == as_decimal("100000")
    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    assert not any(
        reason == (e.why or "")
        for e in result.queue
        if e.observation_id == obs.observation_id
    )
    assert not any(
        set(e.axes or ()) >= {"quantity", "value"}
        and reason in (e.why or "")
        for e in result.queue
        if e.observation_id == obs.observation_id
    )


def test_g3_vaporization_lift_mutation_proof(tmp_path: Path) -> None:
    """Stripping the reaction lift restores the paired quantity/value refusal."""
    import simulator.battery.migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    note = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(l)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-003"
    )
    oid = "janaf::Al-005:T=1200::vapour_rail_psat::log10_Psat_over_P0"
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": oid,
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 1200,
                        "table_kJ_mol": 250.0,
                        "note": note,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    live = migrate(root, write=False)
    live_obs = live.observations[oid]
    assert quantity_token(live_obs.identity) is Quantity.DELTA_FG
    assert live_obs.value.kind is ValueKind.POINT

    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    saved = migrate_mod.lift_vaporization_reaction_from_ledger_note
    try:
        migrate_mod.lift_vaporization_reaction_from_ledger_note = (
            lambda formula, note: None  # type: ignore[assignment]
        )
        mutant = migrate(root, write=False)
    finally:
        migrate_mod.lift_vaporization_reaction_from_ledger_note = saved

    mutant_obs = mutant.observations[oid]
    assert mutant_obs.identity.quantity.is_unknown
    assert quantity_token(mutant_obs.identity) is not Quantity.DELTA_FG
    assert mutant_obs.value.kind is ValueKind.UNAVAILABLE
    assert any(
        reason == (e.why or "")
        for e in mutant.queue
        if e.observation_id == mutant_obs.observation_id
    )

    restored = migrate(root, write=False)
    restored_obs = restored.observations[oid]
    assert quantity_token(restored_obs.identity) is Quantity.DELTA_FG
    assert restored_obs.value.kind is ValueKind.POINT



def test_l05c3_tm_k_and_delta_f_g_298_and_table_log10_kf(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="K and kK as published",
        values={
            "property_kind": "melting_point_and_enthalpy_of_fusion",
            "T_m_K": 1405.0,
            "T_m_uncertainty_K": 100.0,
            "T_range_K": [1405.0, 1405.0],
            "method_class": "compilation_calculated_table",
        },
        obs_type="transition_point",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = "LH84_Na2O_fusion"
    se = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    se["observation_id"] = "NEA05_Se2_g"
    se["type"] = "gibbs_table"
    se["units"] = "kJ/mol"
    se["values"] = {
        "quantity": "delta_fG",
        "Delta_f_G_298_kJ_mol": 92.4,
        "method_class": "compilation_calculated_table",
    }
    extract["species"]["Na"]["observations"].append(se)
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::B-133:T=300::table_self_check::log10_Kf",
                        "source_id": "fixture-source",
                        "species": "B",
                        "comparison_quantity": "log10_Kf",
                        "temperature_K": 300,
                        "table_kJ_mol": -5516.922,
                        "table_log10_Kf": 966.926,
                        "provenance_class": "engine_own_input",
                        "status": "mismatch",
                        "finding_class": "compilation_table_self_check",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    fusion = result.observations["fixture-source::LH84_Na2O_fusion"]
    assert quantity_token(fusion.identity) is Quantity.TRANSITION_TEMPERATURE
    assert fusion.value.kind is ValueKind.POINT
    assert fusion.value.point == as_decimal("1405.0")
    assert fusion.identity.subtype.value == "melting_point_and_enthalpy_of_fusion"
    assert fusion.identity.temperature_K is None or fusion.identity.temperature_K.is_not_applicable
    assert fusion.identity.total_pressure_Pa.is_unknown
    assert fusion.uncertainty.kind.value == "printed"
    assert "100" in str(fusion.uncertainty.verbatim)
    se2 = result.observations["fixture-source::NEA05_Se2_g"]
    assert quantity_token(se2.identity) is Quantity.DELTA_FG
    assert se2.value.kind is ValueKind.POINT
    assert se2.value.point == as_decimal("92.4")
    assert se2.identity.temperature_K.is_value
    assert se2.identity.temperature_K.value == as_decimal("298.15")
    logk = result.observations["janaf::B-133:T=300::table_self_check::log10_Kf"]
    assert quantity_token(logk.identity) is Quantity.LOG10_KF
    assert logk.value.kind is ValueKind.POINT
    assert logk.value.point == as_decimal("966.926")
    assert logk.value.point != as_decimal("-5516.922")
    assert logk.notices
    assert "compilation_table_self_check" in logk.notices[0].reason


def test_l05g2b_langmuir_range_is_in_temperature_reason(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {
                            "range": [0.011, 0.02]
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["iron_olivine_kems"]
    reason = getattr(obs.identity.temperature_K, "reason", "") or ""
    assert "1700" in reason and "1800" in reason


def test_l05g3_expected_value_is_not_a_generic_closed_quantity_field(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="delta_fG",
        units="kJ_per_mol",
        values={
            "quantity": "delta_fG",
            "expected_value": -100.0,
            "method_class": "measured_direct",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert obs.value.kind is ValueKind.UNAVAILABLE


def test_l05g0_store_atomic_weight_is_not_delta_fg() -> None:
    path = (
        REPO_ROOT
        / "data"
        / "literature"
        / "observations-v2"
        / "compilations-robie-hemingway-1995-usgs-b2131.yaml"
    )
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = [
        o
        for o in stored.get("observations") or []
        if str(o.get("observation_id") or "").endswith("atomic-weight-001")
    ]
    assert rows
    q = (rows[0].get("identity") or {}).get("quantity") or {}
    assert q.get("tag") == "unknown"
    assert q.get("value") != "delta_fG"


def test_observation_store_reader_unions_file_and_shard_directory(tmp_path: Path) -> None:
    obs_dir = tmp_path / "observations-v2"
    obs_dir.mkdir()
    (obs_dir / "compilations-janaf.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    shard_dir = obs_dir / "compilations-janaf"
    shard_dir.mkdir()
    (shard_dir / "janaf-Al.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    (obs_dir / "compilations-janaf-reports").mkdir()
    (obs_dir / "compilations-janaf-reports" / "janaf-Al.yaml").write_text("audit: true\n", encoding="utf-8")
    (obs_dir / "kems_measurements.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    names = {path.name for path in iter_observation_store_paths(obs_dir)}
    assert names == {"compilations-janaf.yaml", "janaf-Al.yaml", "kems_measurements.yaml"}
    janaf = iter_observation_store_paths(obs_dir, "compilations-janaf.yaml")
    assert {path.name for path in janaf} == {"compilations-janaf.yaml", "janaf-Al.yaml"}
    families = {compilation_family_from_store_path(path) for path in janaf}
    assert families == {"janaf"}
    assert compilation_family_from_store_path(obs_dir / "kems_measurements.yaml") is None
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "literature_index", REPO_ROOT / "data" / "literature" / "build_index.py"
    )
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    assert builder.iter_observation_store_paths(obs_dir) == iter_observation_store_paths(obs_dir)
    assert builder.compilation_family_from_store_path(janaf[0]) == compilation_family_from_store_path(
        janaf[0]
    )


_OBS_V2 = REPO_ROOT / "data" / "literature" / "observations-v2"
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _account_compilation_shard(path: Path, summary: dict[str, dict]) -> None:
    rel = path.relative_to(_OBS_V2).as_posix()
    cached = summary.get(rel)
    assert cached is not None, f"observation_store_summary missing {rel}"
    assert cached["size"] == path.stat().st_size, rel


def _load_store_if_needle(path: Path, needle: bytes, summary: dict[str, dict]) -> dict | None:
    """YAML-load a store file only when the needle is present.

    Compilation shards without the needle are not parsed; the derived summary
    accounts for them (size-matched). Same observations are compared when the
    needle is present.
    """
    raw = path.read_bytes()
    family = compilation_family_from_store_path(path)
    if family is not None:
        _account_compilation_shard(path, summary)
        if needle not in raw:
            return None
    elif needle not in raw:
        return None
    stored = yaml.load(raw.decode("utf-8"), Loader=_YAML_LOADER)
    return stored if isinstance(stored, dict) else {}


def test_l04_store_never_lifts_log10_psat_as_pressure() -> None:
    obs_dir = _OBS_V2
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    bad = []
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"log10_Psat_over_P0"
    for path in iter_observation_store_paths(obs_dir):
        stored = _load_store_if_needle(path, needle, summary)
        if stored is None:
            continue
        for obs in stored.get("observations") or []:
            oid = str(obs.get("observation_id") or "")
            if "log10_Psat_over_P0" not in oid:
                continue
            q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
            if q in {"p_sat", "p_partial"}:
                bad.append(f"{oid} stored {q}")
    assert not bad, bad[:10]


def test_l05c1_store_alpha_values_lie_in_unit_interval() -> None:
    roots = [
        REPO_ROOT / "data" / "literature" / "extracts-v2",
        REPO_ROOT / "data" / "literature" / "observations-v2",
    ]
    if not roots[0].is_dir():
        pytest.skip("migrated store not generated yet")
    bad = []
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"evaporation_coefficient_alpha"
    for folder in roots:
        for path in iter_observation_store_paths(folder):
            stored = _load_store_if_needle(path, needle, summary)
            if stored is None:
                continue
            for obs in stored.get("observations") or []:
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
                if q != "evaporation_coefficient_alpha":
                    continue
                val = obs.get("value") or {}
                if val.get("kind") != "point":
                    continue
                amount = as_decimal(val.get("point"))
                if not (as_decimal("0") < amount <= as_decimal("1")):
                    bad.append(f"{obs.get('observation_id')} alpha={amount}")
    assert not bad, bad[:20]


def test_l05c5_store_unavailable_values_are_queued() -> None:
    queue_path = REPO_ROOT / "data" / "battery" / "migration-queue.yaml"
    roots = [
        REPO_ROOT / "data" / "literature" / "extracts-v2",
        REPO_ROOT / "data" / "literature" / "observations-v2",
    ]
    if not queue_path.is_file() or not roots[0].is_dir():
        pytest.skip("migrated store not generated yet")
    queued = yaml.load(queue_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}
    entries = queued.get("entries") or queued.get("queue") or queued
    if isinstance(entries, dict):
        entries = entries.get("items") or []
    by_id: dict[str, list[str]] = {}
    # Grouped files keep observation_id under each group's observations list.
    flat_entries = expand_queue_entries(entries) if isinstance(entries, list) else []
    for e in flat_entries:
        oid = str(e.get("observation_id") or "")
        why = str(e.get("why") or "")
        axes = e.get("axes") or []
        by_id.setdefault(oid, []).append(why + " " + " ".join(str(a) for a in axes))
    missing = []
    scanned = 0
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"kind: unavailable"
    for folder in roots:
        for path in iter_observation_store_paths(folder):
            stored = _load_store_if_needle(path, needle, summary)
            if stored is None:
                continue
            for obs in stored.get("observations") or []:
                val = obs.get("value") or {}
                if val.get("kind") != "unavailable":
                    continue
                scanned += 1
                oid = str(obs.get("observation_id") or "")
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value") or (
                    ((obs.get("identity") or {}).get("quantity") or {}).get("reason") or ""
                )
                blob = " ".join(by_id.get(oid) or [])
                if not blob:
                    missing.append(oid)
                    continue
                token = str(q)
                if token and token not in blob and "field" not in blob.lower() and "value" not in blob.lower():
                    missing.append(f"{oid} queue={blob!r} token={token!r}")
    assert scanned > 0
    assert not missing, missing[:20]


def _queue_fingerprints(entries: list[dict]) -> list[str]:
    return [
        json.dumps(
            {
                "work_id": entry["work_id"],
                "source": entry["source"],
                "why": entry["why"],
                "axes": list(entry["axes"]),
                "observation_id": entry["observation_id"],
                "locator": entry["locator"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        for entry in entries
    ]


def _reason_and_axis_counts(entries: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    reasons: dict[str, int] = {}
    axes: dict[str, int] = {}
    for entry in entries:
        why = entry["why"]
        reasons[why] = reasons.get(why, 0) + 1
        for axis in entry["axes"]:
            axes[axis] = axes.get(axis, 0) + 1
    return reasons, axes


def test_queue_group_roundtrip_is_lossless(tmp_path: Path) -> None:
    """Grouping then expanding returns the same entries, reasons, and axes.

    Two writes of the same entries, in different input order, are the same bytes.
    A long extract path stays one contiguous substring so the freshness check
    can still see it.
    """

    long_source = "data/literature/extracts/" + ("long-extract-stem-" * 6) + "tail.yaml"
    assert len(long_source) > 120
    shared = {
        "work_id": "w-shared",
        "source": long_source,
        "why": "source does not state phase",
        "axes": ["phase"],
    }
    locator_page = {"figure": "10", "published_page": 18, "note": "same page"}
    locator_other = {"note": "other page", "published_page": 19}
    entries = [
        {
            "work_id": "w-other",
            "source": "data/literature/extracts/other.yaml",
            "why": "declared quantity is unknown",
            "axes": ["quantity", "value"],
            "observation_id": "obs-two-axes",
            "locator": {"table": "2", "page": "4"},
        },
        {**shared, "observation_id": "obs-b", "locator": dict(locator_other)},
        {
            **shared,
            "observation_id": "obs-a",
            "locator": {"note": "same page", "published_page": 18, "figure": "10"},
        },
        {**shared, "observation_id": "obs-a", "locator": dict(locator_page)},
        {**shared, "observation_id": "obs-a", "locator": dict(locator_other)},
    ]
    expected = expand_queue_entries(entries)
    grouped = group_queue_entries(entries)
    expanded = expand_queue_entries(grouped)

    assert all("observations" in group and "observation_id" not in group for group in grouped)
    phase_groups = [group for group in grouped if group["why"] == shared["why"]]
    assert len(phase_groups) == 1
    assert len(phase_groups[0]["observations"]) == 4
    assert {item["observation_id"] for item in phase_groups[0]["observations"]} == {
        "obs-a",
        "obs-b",
    }

    assert set(_queue_fingerprints(expanded)) == set(_queue_fingerprints(expected))
    assert sorted(_queue_fingerprints(expanded)) == sorted(_queue_fingerprints(expected))
    assert _reason_and_axis_counts(expanded) == _reason_and_axis_counts(expected)
    page_fingerprint = _queue_fingerprints(
        [{**shared, "observation_id": "obs-a", "locator": locator_page}]
    )[0]
    # Same observation and locator twice must stay two entries.
    assert _queue_fingerprints(expanded).count(page_fingerprint) == 2

    legacy = expand_queue_entries(
        [
            {
                "work_id": "w-legacy",
                "locator": {"page": "1"},
                "axes": ["value"],
                "why": "missing value",
                "source": "data/literature/extracts/legacy.yaml",
                "observation_id": "obs-legacy",
            }
        ]
    )
    assert legacy[0]["observation_id"] == "obs-legacy"
    assert legacy[0]["why"] == "missing value"
    assert legacy[0]["axes"] == ["value"]

    aliases = [
        {
            "observation_id": "alias-1",
            "source": long_source,
            "row_indices": [3, 1],
        }
    ]
    first = tmp_path / "queue-a.yaml"
    second = tmp_path / "queue-b.yaml"
    dump_yaml(migration_queue_document(entries, aliases), first)
    dump_yaml(migration_queue_document(list(reversed(entries)), aliases), second)
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert text.startswith(f"schema_version: {QUEUE_SCHEMA_VERSION}\n")
    filename = long_source.split("extracts/", 1)[1]
    assert f"extracts/{filename}" in text


def test_committed_migration_queue_is_canonical_grouped_form() -> None:
    path = REPO_ROOT / "data" / "battery" / "migration-queue.yaml"
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    assert isinstance(document, dict)
    assert document.get("schema_version") == QUEUE_SCHEMA_VERSION
    entries = document.get("entries") or []
    expanded = expand_queue_entries(entries)
    rebuilt = migration_queue_document(expanded, document.get("dedupe_aliases") or [])
    assert rebuilt["entries"] == entries
    assert rebuilt["dedupe_aliases"] == document.get("dedupe_aliases")
    assert expanded
    assert len(expanded) == sum(len(group["observations"]) for group in entries)


def test_l05c5_unavailable_value_is_queued(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="delta_fG",
        units="",
        values={"quantity": "delta_fG", "method_class": "measured_direct"},
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "ledger-logk",
                        "source_id": "fixture-source",
                        "species": "B",
                        "comparison_quantity": "log10_Kf",
                        "temperature_K": 300,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    logk = result.observations["ledger-logk"]
    assert logk.value.kind is ValueKind.UNAVAILABLE
    assert any(
        e.observation_id == "ledger-logk"
        and "value" in (e.axes or ())
        and ("log10_Kf" in (e.why or "") or "field" in (e.why or ""))
        for e in result.queue
    )


_USGS_F1_FAMILIES = (
    "robie-hemingway-fisher-1978-usgs-b1452",
    "hemingway-haas-robinson-1982-usgs-b1544",
    "robie-waldbaum-1968-usgs-b1259",
)
_ABSENT_DELTA_FG_CLAIM = re.compile(
    r"does not name a delta_fG field|series list had no numeric coordinate/value pairs|"
    r"source does not name a delta_fG",
    re.I,
)


def _copy_compilation_record(root: Path, source_id: str, filename: str) -> Path:
    src = (
        REPO_ROOT
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "records"
        / filename
    )
    dest = (
        root
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "records"
        / filename
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def _copy_extract(root: Path, filename: str) -> Path:
    src = REPO_ROOT / "data" / "literature" / "extracts" / filename
    dest = root / "data" / "literature" / "extracts" / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def _record_has_numeric_gibbs(doc: dict) -> bool:
    from simulator.battery.migrate import _census_formation_gibbs

    census = _census_formation_gibbs(doc)
    return any(info["numeric"] for info in census.values())


def test_f1_usgs_unavailable_reasons_match_printed_gibbs_cells(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    copies = [
        (
            "robie-hemingway-fisher-1978-usgs-b1452",
            "robie-hemingway-fisher-1978-usgs-b1452-0004.json",
        ),
        (
            "robie-hemingway-fisher-1978-usgs-b1452",
            "robie-hemingway-fisher-1978-usgs-b1452-0011-phase-02.json",
        ),
        (
            "hemingway-haas-robinson-1982-usgs-b1544",
            "usgs-b1544-al2sio5-reference.json",
        ),
        (
            "robie-waldbaum-1968-usgs-b1259",
            "b1259-ht-0001-silver-reference-state.json",
        ),
        (
            "robie-waldbaum-1968-usgs-b1259",
            "b1259-298k-0001-silver.json",
        ),
    ]
    for source_id, filename in copies:
        _copy_compilation_record(root, source_id, filename)
    result = migrate(root, write=False)

    b1452_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0004:"
    )
    b1452 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1452_prefix)
    ]
    assert b1452
    assert f"{b1452_prefix.rstrip(':')}" not in result.observations
    gibbs_0004 = [
        obs for obs in b1452 if quantity_token(obs.identity) is Quantity.DELTA_FG
    ]
    assert gibbs_0004
    assert all(obs.value.kind is ValueKind.POINT for obs in gibbs_0004)
    assert all(obs.identity.species.formula == "Ag" for obs in gibbs_0004)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in gibbs_0004
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in gibbs_0004
    )

    ocr_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0011-phase-02:"
    )
    ocr = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(ocr_prefix)
    ]
    # Generator stored 0: every numeric cell is OCR-suspect or excluded.
    # The generic placeholder must not remain.
    assert ocr == []
    assert f"{ocr_prefix.rstrip(':')}" not in result.observations

    b1544_prefix = (
        "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-al2sio5-reference:"
    )
    b1544 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1544_prefix)
    ]
    assert b1544
    assert f"{b1544_prefix.rstrip(':')}" not in result.observations
    gibbs = [obs for obs in b1544 if quantity_token(obs.identity) is Quantity.DELTA_FG]
    assert gibbs
    assert all(obs.value.kind is ValueKind.POINT for obs in gibbs)
    assert all(obs.identity.species.formula == "Al2SiO5" for obs in gibbs)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in gibbs
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in gibbs
    )

    ht_prefix = (
        "robie-waldbaum-1968-usgs-b1259:b1259-ht-0001-silver-reference-state:"
    )
    ht = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(ht_prefix)
    ]
    assert ht
    assert f"{ht_prefix.rstrip(':')}" not in result.observations
    assert all(obs.value.kind is ValueKind.POINT for obs in ht)
    assert all(obs.identity.species.formula == "Ag" for obs in ht)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in ht
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in ht
    )

    silver_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(silver_prefix)
    ]
    assert silver
    assert f"{silver_prefix.rstrip(':')}" not in result.observations
    assert all(obs.value.kind is ValueKind.POINT for obs in silver)
    assert all(obs.identity.species.formula == "Ag" for obs in silver)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in silver
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in silver
    )
    assert all(
        "as_published=" in (obs.locator.note or "")
        and "calories stay as published" in (obs.locator.note or "")
        for obs in silver
    )


def test_f1_store_usgs_reasons_do_not_deny_printed_gibbs() -> None:
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    bad: list[str] = []
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for source_id in _USGS_F1_FAMILIES:
        paths = iter_observation_store_paths(obs_dir, f"compilations-{source_id}.yaml")
        records_dir = (
            REPO_ROOT / "data" / "literature" / "compilations" / source_id / "records"
        )
        for path in paths:
            stored = yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}
            for obs in stored.get("observations") or []:
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
                if q != "delta_fG":
                    continue
                val = obs.get("value") or {}
                if val.get("kind") != "unavailable":
                    continue
                reason = str(val.get("unavailable_reason") or "")
                loc = obs.get("locator") or {}
                record_id = loc.get("record") or str(obs.get("observation_id") or "").split(":", 1)[-1]
                rec_path = records_dir / f"{record_id}.json"
                if not rec_path.is_file():
                    continue
                doc = json.loads(rec_path.read_text(encoding="utf-8"))
                if not _record_has_numeric_gibbs(doc):
                    continue
                if _ABSENT_DELTA_FG_CLAIM.search(reason):
                    bad.append(f"{obs.get('observation_id')} reason={reason!r}")
    assert not bad, bad[:12]


def test_f2_phase_quotes_printed_text_and_keeps_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0004.json",
    )
    _copy_compilation_record(
        root,
        "hemingway-haas-robinson-1982-usgs-b1544",
        "usgs-b1544-al2sio5-reference.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0001-silver.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0002-ag-aqueous-ion.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0048-li-aqueous-ion.json",
    )
    result = migrate(root, write=False)

    b1452_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0004:"
    )
    b1452 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1452_prefix)
    ]
    assert b1452
    phases = [obs.identity.species.phase for obs in b1452]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Ag" for obs in b1452)

    b1544_prefix = (
        "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-al2sio5-reference:"
    )
    b1544 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1544_prefix)
    ]
    assert b1544
    phases = [obs.identity.species.phase for obs in b1544]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Al2SiO5" for obs in b1544)

    silver_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(silver_prefix)
    ]
    assert silver
    phases = [obs.identity.species.phase for obs in silver]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Ag" for obs in silver)

    aqueous_prefix = (
        "robie-waldbaum-1968-usgs-b1259:b1259-298k-0002-ag-aqueous-ion:"
    )
    aqueous = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(aqueous_prefix)
    ]
    assert aqueous
    phases = [obs.identity.species.phase for obs in aqueous]
    assert all(phase.is_value and phase.value is Phase.AQ for phase in phases)
    assert all(obs.identity.species.formula == "Ag" for obs in aqueous)

    li_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0048-li-aqueous-ion:"
    lithium = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(li_prefix)
    ]
    assert lithium
    assert all(obs.identity.species.formula == "Li" for obs in lithium)
    assert all(
        obs.identity.species.phase.is_value
        and obs.identity.species.phase.value is Phase.AQ
        for obs in lithium
    )
    assert "Li aqueous" not in {
        obs.identity.species.formula for obs in lithium
    }


def test_f3_compilation_locator_names_the_record_file(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0001-silver.json",
    )
    result = migrate(root, write=False)
    prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(prefix)
    ]
    assert silver
    rel = dest.relative_to(root).as_posix()
    assert all(obs.locator.source_path == rel for obs in silver)
    assert dest.is_file()


def test_f3_store_every_compilation_observation_has_existing_source_path() -> None:
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    missing: list[str] = []
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for path in iter_observation_store_paths(obs_dir, "compilations-*.yaml"):
        stored = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
        for obs in stored.get("observations") or []:
            loc = obs.get("locator") or {}
            rel = loc.get("source_path")
            oid = obs.get("observation_id")
            if not rel:
                missing.append(f"{oid} missing source_path")
                continue
            if not (REPO_ROOT / str(rel)).is_file():
                missing.append(f"{oid} source_path {rel!r} does not exist")
    assert not missing, missing[:20]


def test_f4_antoine_and_points_and_range_restore_corroborated_quantity(
    tmp_path: Path,
) -> None:
    br72 = _extract_observation(
        "behrens-rosenblatt-1972.yaml", "NIST_BR72_arsenolite_As4O6"
    )
    state, _reason = map_quantity(
        br72.get("type"), br72.get("values"), units=br72.get("units"), row=br72
    )
    assert state.is_value and state.value is Quantity.P_SAT

    hab = _extract_observation("habermann-daane-1964.yaml", "Hab64_Yb_metal_antoine")
    state, _reason = map_quantity(
        hab.get("type"), hab.get("values"), units=hab.get("units"), row=hab
    )
    assert state.is_value and state.value is Quantity.P_SAT

    stull = _extract_observation("nist-webbook.yaml", "NIST_Stull47_As2O3_highT")
    state, _reason = map_quantity(
        stull.get("type"), stull.get("values"), units=stull.get("units"), row=stull
    )
    assert state.is_value and state.value is Quantity.P_SAT

    se = _extract_observation("nist-webbook.yaml", "NIST_Stull_Se_total_P")
    state, _reason = map_quantity(
        se.get("type"), se.get("values"), units=se.get("units"), row=se
    )
    assert state.is_unknown
    assert "total vapour pressure over a multi-species vapour" in state.reason
    assert (se.get("values") or {}).get("gas_basis") == "TOTAL_PRESSURE_not_species"

    bic = _extract_observation(
        "berkowitz-chupka-inghram-1957.yaml", "BIC57_VO_absolute_points"
    )
    state, _reason = map_quantity(
        bic.get("type"), bic.get("values"), units=bic.get("units"), row=bic
    )
    assert state.is_value and state.value is Quantity.P_SAT

    costa = _extract_observation(
        "costa-jacobson-2015.yaml", "costa_jacobson_2015_sio_olivine_kems"
    )
    state, _reason = map_quantity(
        costa.get("type"), costa.get("values"), units=costa.get("units"), row=costa
    )
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA

    sf04 = _extract_observation(
        "sf04-magma-companion-workbook.yaml", "sf04_workbook_tho_fe_pressure_series"
    )
    state, _reason = map_quantity(
        sf04.get("type"), sf04.get("values"), units=sf04.get("units"), row=sf04
    )
    assert state.is_value and state.value is Quantity.P_PARTIAL

    root = _write_min_tree(tmp_path)
    for fname in (
        "behrens-rosenblatt-1972.yaml",
        "habermann-daane-1964.yaml",
        "nist-webbook.yaml",
        "berkowitz-chupka-inghram-1957.yaml",
        "costa-jacobson-2015.yaml",
        "sf04-magma-companion-workbook.yaml",
    ):
        _copy_extract(root, fname)
    result = migrate(root, write=False)

    br72_obs = result.observations[
        "behrens-rosenblatt-1972::NIST_BR72_arsenolite_As4O6"
    ]
    assert quantity_token(br72_obs.identity) is Quantity.P_SAT
    assert br72_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_bar) = A" in (br72_obs.value.unavailable_reason or "")

    hab_obs = result.observations["habermann-daane-1964::Hab64_Yb_metal_antoine"]
    assert quantity_token(hab_obs.identity) is Quantity.P_SAT
    assert hab_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_mmHg) = A" in (hab_obs.value.unavailable_reason or "")

    stull_obs = result.observations["nist-webbook::NIST_Stull47_As2O3_highT"]
    assert quantity_token(stull_obs.identity) is Quantity.P_SAT
    assert stull_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_bar) = A" in (stull_obs.value.unavailable_reason or "")

    se_obs = result.observations["nist-webbook::NIST_Stull_Se_total_P"]
    assert se_obs.identity.quantity.is_unknown
    assert "no total-vapour-pressure identity" in se_obs.identity.quantity.reason
    assert se_obs.value.kind is ValueKind.UNAVAILABLE
    assert "total vapour pressure" in (se_obs.value.unavailable_reason or "")

    bic_rows = [
        o
        for o in result.observations.values()
        if "BIC57_VO_absolute_points" in o.observation_id
    ]
    assert bic_rows
    assert all(quantity_token(o.identity) is Quantity.P_SAT for o in bic_rows)
    amounts = sorted(
        float(o.value.point)
        for o in bic_rows
        if o.value.kind is ValueKind.POINT and o.value.point is not None
    )
    series_parent = [
        o for o in bic_rows if o.value.kind is ValueKind.SERIES and o.value.series
    ]
    assert amounts == [0.116, 0.155] or (
        series_parent
        and {float(p) for _t, p in series_parent[0].value.series} == {0.116, 0.155}
    )

    costa_obs = result.observations[
        "costa-jacobson-2015::costa_jacobson_2015_sio_olivine_kems"
    ]
    assert quantity_token(costa_obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert costa_obs.value.kind is ValueKind.INTERVAL
    assert costa_obs.value.interval_low == as_decimal("0.003")
    assert costa_obs.value.interval_high == as_decimal("0.036")

    sf04_rows = [
        o
        for o in result.observations.values()
        if "sf04_workbook_tho_fe_pressure_series" in o.observation_id
    ]
    assert sf04_rows
    assert all(quantity_token(o.identity) is Quantity.P_PARTIAL for o in sf04_rows)
    assert any(
        o.value.kind is ValueKind.SERIES
        or (o.value.kind is ValueKind.POINT and o.value.point is not None)
        for o in sf04_rows
    )
    assert all(
        o.evidence.class_.is_value
        and o.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        for o in sf04_rows
    )
    assert all(
        o.evidence.class_.value is not EvidenceClass.MEASURED_DIRECT
        and o.evidence.class_.value is not EvidenceClass.MEASURED_REDUCED
        and o.evidence.class_.value is not EvidenceClass.MEASURED_TABULATED
        for o in sf04_rows
    )


def test_pyrolysis_yield_quantities_are_not_collapsed() -> None:
    """O2/feedstock, O2/sample, and bulk mass loss stay distinct closed tokens."""

    bulk, _reason = map_quantity(
        "rate_series",
        {"quantity": "bulk_mass_loss_wt_pct", "mass_loss_wt_pct": 1.1},
        units="wt_percent",
    )
    assert bulk.is_value and bulk.value is Quantity.MASS_LOSS_FRACTION

    sidecar, _reason = map_quantity(
        "rate_series",
        {
            "quantity": "non_condensed_mass_loss_fraction",
            "non_condensed_mass_loss_fraction": 0.0117,
        },
        units="mass_fraction",
    )
    assert sidecar.is_value and sidecar.value is Quantity.MASS_LOSS_FRACTION

    mixed, reason = map_quantity(
        "rate_series",
        {
            "quantity": "measured_oxygen_yield",
            "oxygen_mass_mg": 35,
            "mass_yield_percent": 1.05,
            "fraction_of_feedstock_oxygen_percent": 2.47,
        },
        units="as published",
    )
    assert not mixed.is_value
    assert reason and "measured_oxygen_yield" in reason

    outgassing, reason = map_quantity(
        "rate_series",
        {"quantity": "total_mass_loss", "total_mass_loss_wt_pct": 0.40},
        units="wt_percent",
    )
    assert not outgassing.is_value
    assert reason and "total_mass_loss" in reason

    summary, reason = map_quantity(
        "rate_series",
        {"quantity": "vacuum_pyrolysis_experiment_summary", "tests": []},
        units="as_published",
    )
    assert not summary.is_value
    assert reason and "vacuum_pyrolysis_experiment_summary" in reason

    model, reason = map_quantity(
        "rate_series",
        {"quantity": "oxygen_yield_wt_pct", "O2_yield_pct_of_oxide": 19.3},
        units="wt_percent",
    )
    assert not model.is_value


def test_sauerborn_mass_loss_points_explode_with_point_t(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="bulk_mass_loss_wt_pct",
        units="wt_percent",
        values={
            "quantity": "bulk_mass_loss_wt_pct",
            "method_class": "measured_direct",
            "points": [
                {
                    "id": "SiO2",
                    "mass_loss_wt_pct": 1.1,
                    "Tmax_C": 1400,
                    "Tmax_K": 1673.15,
                    "mass_g": 0.6719,
                    "locator": {"page": 76},
                },
                {
                    "id": "MS2",
                    "mass_loss_wt_pct": 3.2,
                    "Tmax_C": 1563,
                    "Tmax_K": 1836.15,
                    "mass_g": 0.991,
                    "locator": {"page": 88},
                },
            ],
        },
        obs_type="rate_series",
    )
    extract["species"]["Na"]["observations"][0]["regime"] = "solar_vacuum_pyrolysis"
    extract["species"]["Na"]["observations"][0]["phase"] = "l"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if "::T=" in o.observation_id
    ]
    assert len(points) == 2
    by_value = {o.value.point: o for o in points}
    sio2 = by_value[as_decimal("0.011")]
    assert sio2.identity.species.formula == "SiO2"
    assert quantity_token(sio2.identity) is Quantity.MASS_LOSS_FRACTION
    assert sio2.value.kind is ValueKind.POINT
    assert float(sio2.identity.temperature_K.value) == 1673.15
    assert sio2.admission.status is AdmissionStatus.ADMITTED
    ms2 = by_value[as_decimal("0.032")]
    assert ms2.identity.species.formula == "unknown"
    assert float(ms2.identity.temperature_K.value) == 1836.15
    assert ms2.admission.status is AdmissionStatus.ADMITTED


def test_robinot_measured_oxygen_yield_splits_and_keeps_t_range(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="measured_oxygen_yield",
        units="percent",
        values={
            "quantity": "measured_oxygen_yield",
            "method_class": "measured_direct",
            "oxygen_mass_mg": 35,
            "mass_yield_percent": 1.05,
            "fraction_of_feedstock_oxygen_percent": 2.47,
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [1473.15, 2073.15]
    row["regime"] = "solar_vacuum_pyrolysis_free_evaporation"
    row["phase"] = "g"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    rows = list(result.observations.values())
    tokens = {quantity_token(o.identity): o for o in rows}
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    assert Quantity.MASS_LOSS_FRACTION not in tokens
    yield_frac = tokens[Quantity.YIELD_FRACTION]
    assert yield_frac.value.kind is ValueKind.POINT
    assert yield_frac.value.point == as_decimal("0.0105")
    o2_yield = tokens[Quantity.O2_YIELD]
    assert o2_yield.value.point == as_decimal("0.0247")
    for obs in (yield_frac, o2_yield):
        assert obs.identity.temperature_K.is_unknown
        assert "no midpoint invented" in (obs.identity.temperature_K.reason or "")
        assert "1473.15" in (obs.identity.temperature_K.reason or "")
        assert obs.admission.status is AdmissionStatus.ADMITTED
        local = obs.observation_id.split("::", 1)[1]
        if "::field:" in local:
            local = local.split("::field:")[0]
        elif "::point:" in local:
            local = local.split("::point:")[0]
        assert local == "na_psat"


def test_cardiff_tests_explode_without_inventing_bound_t(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="vacuum_pyrolysis_experiment_summary",
        units="as_published",
        values={
            "quantity": "vacuum_pyrolysis_experiment_summary",
            "method_class": "measured_direct",
            "contradiction_vs_matchett_2006_table3": {
                "test_2b_mass_loss": {"cardiff_table1": "16.00%", "matchett_table3": "0.16%"},
                "resolution": "none_both_printed_this_extract_keeps_cardiff_as_printed",
            },
            "tests": [
                {
                    "test": "2b",
                    "sample": "FeTiO3",
                    "Tmax_C": None,
                    "Tmax_C_as_printed": ">800",
                    "mass_loss_pct": 16.0,
                },
                {
                    "test": 3,
                    "sample": "FeTiO3",
                    "Tmax_C": 700.0,
                    "mass_loss_pct": 37.0,
                },
                {
                    "test": 11,
                    "sample": "MLS-1a",
                    "Tmax_C": 1474.0,
                    "mass_loss_pct": 10.1,
                },
                {
                    "test": 12,
                    "sample": "MLS-1a",
                    "Tmax_C": 684.0,
                    "mass_loss_pct": None,
                    "mass_loss_as_printed": "-",
                },
            ],
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [821.15, 2140.15]
    row["regime"] = "solar_fresnel_continuously_pumped_vacuum_pyrolysis"
    row["phase"] = "l"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
    ]
    assert len(points) == 3
    by_value = {float(o.value.point): o for o in points}
    mls = by_value[0.101]
    assert mls.identity.species.formula == "unknown"
    assert float(mls.identity.temperature_K.value) == 1474.0 + 273.15
    assert mls.admission.status is AdmissionStatus.ADMITTED
    bound = by_value[0.16]
    assert bound.identity.species.formula == "FeTiO3"
    assert bound.identity.temperature_K.is_unknown
    assert bound.admission.status is AdmissionStatus.PENDING
    disagreed = by_value[0.37]
    assert disagreed.admission.status is AdmissionStatus.PENDING
    assert "16.00/37.00" in disagreed.admission.reason
    assert any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in disagreed.notices)
    assert any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in bound.notices)
    assert not any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in mls.notices)


def test_live_pyrolysis_extracts_map_distinct_yield_quantities(tmp_path: Path) -> None:
    extracts_src = REPO_ROOT / "data" / "literature" / "extracts"
    names = [
        "kems-044-robinot-2026.yaml",
        "kems-035-sauerborn-2005.yaml",
        "cardiff-2007-vacuum-pyrolysis-gsfc.yaml",
        "kems-038-matchett-2006.yaml",
        "wilkerson-2023-jsc1a-outgassing.yaml",
        "steurer-1985-vapor-phase-pyrolysis.yaml",
    ]
    dest = tmp_path / "data" / "literature" / "extracts"
    dest.mkdir(parents=True)
    (tmp_path / "data" / "literature" / "compilations").mkdir(parents=True)
    sources = []
    for name in names:
        src = extracts_src / name
        (dest / name).write_bytes(src.read_bytes())
        sources.append({"source_id": src.stem, "citation": src.stem})
    (tmp_path / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": sources,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(tmp_path, write=False)
    robinot = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-044-robinot-2026"
        and quantity_token(o.identity) in {Quantity.YIELD_FRACTION, Quantity.O2_YIELD}
    ]
    tokens = {quantity_token(o.identity) for o in robinot}
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    assert all(
        o.identity.temperature_K.is_unknown
        and "no midpoint invented" in (o.identity.temperature_K.reason or "")
        for o in robinot
    )
    sauerborn = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-035-sauerborn-2005"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    assert {float(o.value.point) for o in sauerborn} >= {0.011, 0.026, 0.032, 0.029}
    assert all(
        o.evidence.class_.is_value
        and o.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for o in sauerborn
        if o.admission.status is not AdmissionStatus.SUPERSEDED
    )
    cardiff = [
        o
        for o in result.observations.values()
        if o.source_id == "cardiff-2007-vacuum-pyrolysis-gsfc"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    cardiff_vals = {float(o.value.point) for o in cardiff}
    assert 0.101 in cardiff_vals
    assert 0.16 in cardiff_vals
    assert 0.37 in cardiff_vals
    matchett = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-038-matchett-2006"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    matchett_vals = {float(o.value.point) for o in matchett}
    assert 0.101 in matchett_vals
    assert 0.0016 in matchett_vals
    assert 0.0037 in matchett_vals
    forbidden = {
        o.source_id
        for o in result.observations.values()
        if o.source_id
        in {"wilkerson-2023-jsc1a-outgassing", "steurer-1985-vapor-phase-pyrolysis"}
        and quantity_token(o.identity)
        in {
            Quantity.MASS_LOSS_FRACTION,
            Quantity.YIELD_FRACTION,
            Quantity.O2_YIELD,
        }
    }
    assert not forbidden


def test_robinot_peak_about_is_not_invented_as_point_t(tmp_path: Path) -> None:
    """Robinot prints 'around 1800 °C' as a punctual pyrometer peak, not a hold."""

    extract = _scalar_extract(
        quantity="yield_fraction",
        units="percent",
        values={
            "quantity": "yield_fraction",
            "method_class": "measured_direct",
            "mass_yield_percent": 1.05,
            "peak_T_C_about": 1800,
            "peak_T_K_converted": 2073.15,
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [1473.15, 2073.15]
    row["phase"] = "g"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(
        o
        for o in result.observations.values()
        if quantity_token(o.identity) is Quantity.YIELD_FRACTION
    )
    assert obs.identity.temperature_K.is_unknown
    reason = obs.identity.temperature_K.reason or ""
    assert "source T_range_K [1473.15, 2073.15]; no midpoint invented" == reason
    assert obs.identity.temperature_K.value is None


def test_store_pyrolysis_yield_census_is_honest() -> None:
    from simulator.battery.migrate import observation_from_plain
    from simulator.battery.score import MEASURED_EVIDENCE, rail_for_quantity

    files = [
        REPO_ROOT / "data/literature/extracts-v2/kems-044-robinot-2026.yaml",
        REPO_ROOT / "data/literature/extracts-v2/kems-035-sauerborn-2005.yaml",
        REPO_ROOT / "data/literature/extracts-v2/cardiff-2007-vacuum-pyrolysis-gsfc.yaml",
        REPO_ROOT / "data/literature/extracts-v2/kems-038-matchett-2006.yaml",
        REPO_ROOT / "data/literature/observations-v2/vacuum_pyrolysis_measurements.yaml",
    ]
    rows = []
    for path in files:
        stored = yaml.safe_load(path.read_text(encoding="utf-8"))
        for raw in stored.get("observations") or []:
            obs = observation_from_plain(raw)
            q = quantity_token(obs.identity)
            formula = obs.identity.species.formula
            if q is None:
                continue
            if rail_for_quantity(q, species_formula=formula) is Rail.PYROLYSIS_YIELD:
                rows.append(obs)
    assert len(rows) == 30
    tokens = {quantity_token(o.identity) for o in rows}
    assert Quantity.MASS_LOSS_FRACTION in tokens
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    candidates = [
        o
        for o in rows
        if o.evidence.class_.is_value
        and o.evidence.class_.value in MEASURED_EVIDENCE
        and o.admission.status in {AdmissionStatus.ADMITTED, AdmissionStatus.PENDING}
    ]
    assert len(candidates) == 26
    admitted = [o for o in candidates if o.admission.status is AdmissionStatus.ADMITTED]
    pending = [o for o in candidates if o.admission.status is AdmissionStatus.PENDING]
    assert len(admitted) == 18
    assert len(pending) == 8
    disagreed = [
        o
        for o in pending
        if any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in o.notices)
    ]
    assert len(disagreed) == 4
    from simulator.battery.score import decision_band_for
    from simulator.battery.enums import SourceRelation

    assert decision_band_for(Quantity.MASS_LOSS_FRACTION, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.O2_YIELD, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.YIELD_FRACTION, SourceRelation.INDEPENDENT) is None


def test_vacuum_pyrolysis_sidecar_loads_pomeroy_not_robinot_duplicates(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(tmp_path)
    sidecar = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").read_text(
            encoding="utf-8"
        )
    )
    (root / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").write_text(
        yaml.safe_dump(sidecar, sort_keys=False),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    pomeroy = result.observations[
        "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction"
    ]
    assert quantity_token(pomeroy.identity) is Quantity.MASS_LOSS_FRACTION
    assert pomeroy.value.kind is ValueKind.POINT
    assert pomeroy.value.point == as_decimal("0.0117")
    assert float(pomeroy.identity.temperature_K.value) == 1400.0 + 273.15
    assert pomeroy.admission.status is AdmissionStatus.PENDING
    assert "sample mass" in pomeroy.admission.reason or "not reported" in pomeroy.admission.reason or pomeroy.admission.reason.startswith("no observation admission_status")
    work = result.works[result.experiments[pomeroy.experiment_id].work_id]
    asset_ids = {f.asset_id for f in work.source_files.files}
    assert pomeroy.read_from in asset_ids
    assert not any(
        oid.startswith("robinot_2026_deposit_measurements:")
        for oid in result.observations
    )


def test_d032_context_rows_carried_not_observed(tmp_path: Path) -> None:
    """d-032 ruling: extract ``context`` rows reach the work record verbatim
    and never become Observation records in the v2.1 store."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    context_row = {
        "observation_id": "na_bench_note",
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "phase": "gas",
        "regime": "knudsen_effusion",
        "units": "as printed",
        "values": {
            "quantity": "apparatus_note",
            "method_class": "method_only",
            "furnace": "muffle",
            "sample_mass_g": 90,
        },
    }
    extract["species"]["Na"]["context"] = [context_row]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    qualified = "fixture-source::context::na_bench_note"
    assert qualified not in result.observations
    assert not any(
        oid.endswith("::na_bench_note") for oid in result.observations
    )
    work_id = next(iter(result.context_by_work))
    carried = result.context_by_work[work_id]
    assert len(carried) == 1
    record = carried[0]
    assert record["context_id"] == qualified
    assert record["work_id"] == work_id
    assert record["source_id"] == "fixture-source"
    assert record["species"] == "Na"
    assert record["type"] == "apparatus"
    assert record["values"]["sample_mass_g"] == 90
    assert record["locator"]["page"] == 3
    # The scored row still migrates exactly as before (series explodes to points).
    assert any(
        oid.startswith("fixture-source::na_psat") for oid in result.observations
    )
    # Works payload carries the container; the store holds no observation for it.
    works_docs = [
        d
        for d in (
            yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in (root / "data" / "literature" / "works").glob("*.yaml")
        )
        if isinstance(d, dict)
    ]
    context_entries = [
        row for doc in works_docs for row in (doc.get("context") or [])
    ]
    assert [row["context_id"] for row in context_entries] == [qualified]
    works, experiments, observations = load_migrated_store(root)
    assert qualified not in observations
    assert not any(oid.endswith("::na_bench_note") for oid in observations)


def test_d036_equipment_fk_resolves_through_context_row(tmp_path: Path) -> None:
    """d-036 ruling: boulliung's declared experiment reaches
    ``boulliung_2025_ssas_apparatus_and_run_conditions`` BY REFERENCE.
    Equipment values are read through the FK and never copied onto the
    experiment record."""
    extract = yaml.safe_load(
        (
            REPO_ROOT
            / "data/literature/extracts/boulliung-2025-mercury-volatile-metals-magmatic.yaml"
        ).read_text(encoding="utf-8")
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    experiment_id = "10.1016/j.chemgeo.2025.123018::experiment::ssas-hg-volatile-series"
    context_id = (
        "boulliung-2025-mercury-volatile-metals-magmatic"
        "::context::boulliung_2025_ssas_apparatus_and_run_conditions"
    )
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == context_id

    # The reference resolves against the work record's context rows, and the
    # equipment values are readable through it.
    context = load_migrated_context(root)
    row = resolve_equipment_context(experiment, context)
    assert row is not None
    assert row["context_id"] == context_id
    assert row["type"] == "laboratory_conditions"
    assert row["equipment"]["cell_material"] == "silica tube"
    assert row["equipment"]["crucible_material"] == "alumina"
    assert row["equipment"]["sample_container"]["value"] == "alumina crucible"
    assert row["equipment"]["ampoule_volume_m3"]["value"] == "4.4 x 10^-6"
    assert row["values"]["apparatus"]["furnace"] == "muffle furnace"

    # No duplication: the experiment record carries none of the row's
    # equipment keys or values; they exist in exactly one place.
    assert experiment.apparatus is not None
    assert experiment.apparatus.cell_material_and_liner is None
    experiment_text = yaml.safe_dump(to_plain(experiment))
    for key in (
        "cell_material",
        "crucible_material",
        "sample_container",
        "ampoule_volume_m3",
        "chamber_length_cm",
        "thermocouple",
        "silica tube",
    ):
        assert key not in experiment_text
    work_file = root / "data/literature/works/10.1016_j.chemgeo.2025.123018.yaml"
    work_text = work_file.read_text(encoding="utf-8")
    assert work_text.count("ampoule_volume_m3") == 1
    work_doc = yaml.safe_load(work_text)
    assert [row["context_id"] for row in work_doc["context"]] == [
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_ag_trace_doping_and_comparator",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_hg_oxidation_state_and_fugacity",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_ssas_apparatus_and_run_conditions",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_starting_material_hg_and_trace_doping",
    ]

    # The FK round-trips through the persisted store.
    _, experiments, _ = load_migrated_store(root)
    assert experiments[experiment_id].equipment_context_id == context_id
    assert experiments[experiment_id].apparatus.cell_material_and_liner is None


def test_d036_migrator_links_equipment_context_rows(tmp_path: Path) -> None:
    """Fixture-level link rules: an equipment-bearing context row that names a
    declared experiment sets the FK; a context row without equipment evidence
    does not; a second equipment row for the same experiment is a typed
    registry issue, never a silent overwrite."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["experiments"] = [
        {
            "experiment_id": "fixture-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    extract["species"]["Na"]["context"] = [
        {
            "observation_id": "apparatus_row",
            "experiment": "fixture-series",
            "type": "laboratory_conditions",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "alumina"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        },
        {
            "observation_id": "characterization_row",
            "experiment": "fixture-series",
            "type": "characterization",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "values": {"quantity": "sample_note", "method_class": "method_only"},
        },
        {
            "observation_id": "second_apparatus_row",
            "experiment": "fixture-series",
            "type": "apparatus",
            "locator": {"page": 4, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "platinum"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    experiment_id = next(
        eid for eid in result.experiments if eid.endswith("::experiment::fixture-series")
    )
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == "fixture-source::context::apparatus_row"
    # The row without equipment evidence is context, not an equipment record.
    assert experiment.equipment_context_id != "fixture-source::context::characterization_row"
    # The second equipment row is a typed conflict, not a silent overwrite.
    conflicts = [
        issue
        for issue in result.registry_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(conflicts) == 1
    assert conflicts[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "second_apparatus_row" in conflicts[0].detail
    assert "apparatus_row" in conflicts[0].detail
    row = resolve_equipment_context(experiment, load_migrated_context(root))
    assert row["equipment"]["cell_material"] == "alumina"


def test_d036_equipment_context_row_naming_undeclared_experiment_refuses(
    tmp_path: Path,
) -> None:
    """A context row whose ``experiment:`` names no declared experiment is a
    typed registry refusal naming the missing id — never a manufactured
    pointer."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["context"] = [
        {
            "observation_id": "apparatus_row",
            "experiment": "ghost-series",
            "type": "laboratory_conditions",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "alumina"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        }
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    assert not any(
        experiment.equipment_context_id is not None
        for experiment in result.experiments.values()
    )
    issues = [
        issue
        for issue in result.registry_issues
        if issue.path == "context[fixture-source::context::apparatus_row].experiment"
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "ghost-series" in issues[0].detail


def test_d036_dangling_equipment_context_fk_refuses_typed(tmp_path: Path) -> None:
    """A stored equipment_context_id that names no context row is a typed
    refusal naming the missing id — at resolution and at validation."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    assert experiment.equipment_context_id is None
    # Absence is not an error: no reference resolves to None.
    assert resolve_equipment_context(experiment, {}) is None

    dangling = "fixture-source::context::absent_equipment_row"
    ghost = replace(experiment, equipment_context_id=dangling)
    with pytest.raises(UnresolvedEquipmentContextError, match=re.escape(dangling)):
        resolve_equipment_context(ghost, {})
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={}
    )
    issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert dangling in issues[0].detail
    # A store carrying the row resolves clean.
    row = {"context_id": dangling, "equipment": {"cell_material": "alumina"}}
    assert resolve_equipment_context(ghost, {dangling: row}) is row
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={dangling: row}
    )
    assert not any(
        issue.path.endswith(".equipment_context_id") for issue in report.issues
    )


def test_species_rail_janaf_token_migrates_under_ledger_label(tmp_path: Path) -> None:
    """The species-rail ledger's ``source_id: janaf`` names the compilation
    compared against, not a battery work; the migrated work is the ledger
    itself, labelled ``species-rail-differential``."""
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::Mg-001:T=1363::nasa_cea_9::delta_fG_kJ_mol",
                        "source_id": "janaf",
                        "observation_id": "Mg-001",
                        "species": "Mg",
                        "comparison_quantity": "delta_fG_kJ_mol",
                        "temperature_K": 1363,
                        "table_kJ_mol": -500.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    ledger_works = [
        work for work in result.works.values() if "species-rail-differential" in work.source_ids
    ]
    assert len(ledger_works) == 1
    work = ledger_works[0]
    assert work.source_ids == ("species-rail-differential",)
    assert work.citation == "species-rail-differential"
    assert all("janaf" not in candidate.source_ids for candidate in result.works.values())
    assert "janaf" not in result.aliases
    assert result.aliases["species-rail-differential"] == work.work_id
    observation = result.observations["janaf::Mg-001:T=1363::nasa_cea_9::delta_fG_kJ_mol"]
    assert observation.source_id == "species-rail-differential"
    experiment_ids = result.experiments_by_work[work.work_id]
    assert experiment_ids == [f"{work.work_id}::species-rail-differential"]


def test_committed_aliases_pin_species_rail_ledger_not_janaf() -> None:
    """The alias registry resolves the ledger label to the historical work id
    (keeping experiment ids stable) and no longer claims a ``janaf`` work."""
    from simulator.battery.migrate import load_aliases

    aliases = load_aliases()
    assert "janaf" not in aliases
    assert aliases["species-rail-differential"] == citation_hash("janaf")
    assert aliases["janaf-4th"] == aliases["nist-janaf-4th"]

def test_species_rail_pankratz_token_also_files_under_ledger_label(tmp_path: Path) -> None:
    """Comparison-compilation tokens other than janaf must not land ledger
    residuals on the real compilation work (USBM B689)."""
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "pankratz-1987-usbm-b689::page-0003:T=298.15::nasa_cea_9::delta_fG_kJ_mol",
                        "source_id": "pankratz-1987-usbm-b689",
                        "observation_id": "page-0003",
                        "species": "MgO",
                        "comparison_quantity": "delta_fG_kJ_mol",
                        "temperature_K": 298.15,
                        "table_kJ_mol": -500.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    ledger_works = [
        work for work in result.works.values()
        if "species-rail-differential" in work.source_ids
    ]
    assert len(ledger_works) == 1
    work = ledger_works[0]
    assert all(
        "pankratz-1987-usbm-b689" not in candidate.source_ids
        for candidate in result.works.values()
    )
    observation = result.observations[
        "pankratz-1987-usbm-b689::page-0003:T=298.15::nasa_cea_9::delta_fG_kJ_mol"
    ]
    assert observation.source_id == "species-rail-differential"
    assert result.experiments_by_work[work.work_id] == [
        f"{work.work_id}::species-rail-differential"
    ]

# ---------------------------------------------------------------------------
# 2026-09-22 hardening (latent, none live in the corpus): cross-work
# equipment FK refusal, extracts-v2 orphan siblings on delete/rename, and a
# typed error for duplicate context ids.
# ---------------------------------------------------------------------------


def _named_extract(source_id: str, doi: str) -> dict:
    doc = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    doc["source_id"] = source_id
    doc["source"]["doi"] = doi
    doc["source"]["citation"] = f"{source_id}, A. (2026), J 1:1, DOI {doi}"
    return doc


def _write_multi_extract_tree(root: Path, extracts: list[tuple[str, dict]]) -> Path:
    """_write_min_tree with several named extracts, each declared in INDEX."""
    _write_min_tree(root, extracts[0][1])
    extracts_dir = root / "data" / "literature" / "extracts"
    (extracts_dir / "fixture-source.yaml").unlink()
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    template = index["sources"].pop(0)
    index["sources"] = []
    for stem, doc in extracts:
        (extracts_dir / f"{stem}.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
        )
        entry = yaml.safe_load(yaml.safe_dump(template))
        entry["source_id"] = doc["source_id"]
        entry["doi"] = doc["source"]["doi"]
        entry["citation"] = doc["source"]["citation"]
        index["sources"].append(entry)
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    return root


def _equipment_context_row(observation_id: str, experiment: object) -> dict:
    return {
        "observation_id": observation_id,
        "experiment": experiment,
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "units": "as printed",
        "equipment": {"cell_material": "alumina"},
        "values": {"quantity": "apparatus_note", "method_class": "method_only"},
    }


def test_equipment_fk_refuses_a_cross_work_context_row(tmp_path: Path) -> None:
    """An equipment-bearing context row naming another work's experiment
    through a qualified ``::`` id must not set that experiment's FK. The
    registry records a typed referential_integrity refusal instead."""
    victim = _named_extract("aaa-victim", "10.1234/VICTIM")
    victim["experiments"] = [
        {
            "experiment_id": "vic-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    root = _write_multi_extract_tree(tmp_path, [("aaa-victim", victim)])
    victim_experiment_id = next(
        eid
        for eid in migrate(root, write=False).experiments
        if eid.endswith("::experiment::vic-series")
    )
    attacker = _named_extract("zzz-attacker", "10.1234/ATTACK")
    attacker["species"]["Na"]["context"] = [
        _equipment_context_row("evil_eq", victim_experiment_id)
    ]
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-victim", victim), ("zzz-attacker", attacker)]
    )
    result = migrate(root, write=True)
    experiment = result.experiments[victim_experiment_id]
    assert experiment.equipment_context_id is None
    issues = [
        issue
        for issue in result.registry_issues
        if issue.path == "context[zzz-attacker::context::evil_eq].experiment"
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert victim_experiment_id in issues[0].detail
    assert experiment.work_id in issues[0].detail
    # No FK means nothing resolves to the foreign row.
    assert resolve_equipment_context(experiment, load_migrated_context(root)) is None


def test_equipment_fk_allows_a_same_work_qualified_reference(tmp_path: Path) -> None:
    """The cross-work refusal must not over-fire: two extracts aliasing one
    work (same DOI) may share an experiment through its qualified id."""
    first = _named_extract("aaa-first", "10.1234/SHARED")
    first["experiments"] = [
        {
            "experiment_id": "shared-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    root = _write_multi_extract_tree(tmp_path, [("aaa-first", first)])
    experiment_id = next(
        eid
        for eid in migrate(root, write=False).experiments
        if eid.endswith("::experiment::shared-series")
    )
    second = _named_extract("bbb-second", "10.1234/SHARED")
    second["species"]["Na"]["context"] = [
        _equipment_context_row("shared_apparatus", experiment_id)
    ]
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-first", first), ("bbb-second", second)]
    )
    result = migrate(root, write=True)
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == "bbb-second::context::shared_apparatus"
    row = resolve_equipment_context(experiment, load_migrated_context(root))
    assert row["equipment"]["cell_material"] == "alumina"


def test_validate_flags_equipment_fk_work_mismatch(tmp_path: Path) -> None:
    """validate_corpus reports a stored FK whose context row belongs to a
    different work — the same invariant the migrator refuses to write."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    fk = "other-source::context::foreign_equipment"
    ghost = replace(experiment, equipment_context_id=fk)
    foreign_row = {
        "context_id": fk,
        "work_id": "10.9999/OTHER",
        "equipment": {"cell_material": "alumina"},
    }
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={fk: foreign_row}
    )
    issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "10.9999/OTHER" in issues[0].detail
    assert experiment.work_id in issues[0].detail
    # A same-work row stays clean.
    own_row = dict(foreign_row, work_id=experiment.work_id)
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={fk: own_row}
    )
    assert not any(
        issue.path.endswith(".equipment_context_id") for issue in report.issues
    )


def test_write_outputs_unlinks_extracts_v2_orphan_on_delete(tmp_path: Path) -> None:
    """Deleting an extract must remove its extracts-v2 sibling: extracts-v2
    has no wipe-all path, so a stale sibling would re-enter the store on
    every load."""
    keep = _named_extract("aaa-keep", "10.1234/KEEP")
    gone = _named_extract("bbb-gone", "10.1234/GONE")
    root = _write_multi_extract_tree(
        tmp_path, [("aaa-keep", keep), ("bbb-gone", gone)]
    )
    migrate(root, write=True)
    extracts_v2 = root / "data" / "literature" / "extracts-v2"
    assert (extracts_v2 / "aaa-keep.yaml").is_file()
    assert (extracts_v2 / "bbb-gone.yaml").is_file()
    (root / "data" / "literature" / "extracts" / "bbb-gone.yaml").unlink()
    migrate(root, write=True)
    assert not (extracts_v2 / "bbb-gone.yaml").exists()
    assert (extracts_v2 / "aaa-keep.yaml").is_file()
    _, _, observations = load_migrated_store(root)
    assert not any(oid.startswith("bbb-gone") for oid in observations)
    assert sum(1 for oid in observations if oid.startswith("aaa-keep")) == 2


def test_write_outputs_unlinks_extracts_v2_orphan_on_rename(tmp_path: Path) -> None:
    """Renaming an extract (same DOI, new stem and source_id) must remove the
    old extracts-v2 sibling. Experiment ids are work-scoped, so a surviving
    sibling would load the same printed data twice under two source ids."""
    old = _named_extract("bbb-old", "10.1234/SAME")
    root = _write_multi_extract_tree(tmp_path, [("bbb-old", old)])
    migrate(root, write=True)
    extracts_v2 = root / "data" / "literature" / "extracts-v2"
    assert (extracts_v2 / "bbb-old.yaml").is_file()
    (root / "data" / "literature" / "extracts" / "bbb-old.yaml").unlink()
    new = _named_extract("ccc-new", "10.1234/SAME")
    (root / "data" / "literature" / "extracts" / "ccc-new.yaml").write_text(
        yaml.safe_dump(new, sort_keys=False), encoding="utf-8"
    )
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    for source in index["sources"]:
        if source["source_id"] == "bbb-old":
            source["source_id"] = "ccc-new"
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    migrate(root, write=True)
    assert not (extracts_v2 / "bbb-old.yaml").exists()
    assert (extracts_v2 / "ccc-new.yaml").is_file()
    _, experiments, observations = load_migrated_store(root)
    old_obs = [oid for oid in observations if oid.startswith("bbb-old")]
    new_obs = [oid for oid in observations if oid.startswith("ccc-new")]
    assert old_obs == []
    assert len(new_obs) == 2
    # No double count: each loaded row resolves to a live experiment.
    assert all(observations[oid].experiment_id in experiments for oid in new_obs)


def test_duplicate_context_id_raises_typed(tmp_path: Path) -> None:
    """Two context rows under one work reusing an observation_id abort the
    run with a typed DuplicateContextIdError — never a silent last-wins row
    loss that could point an equipment FK at the wrong row."""
    extract = _named_extract("dup-ctx", "10.1234/DUP")
    extract["experiments"] = [
        {
            "experiment_id": "s",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    extract["species"]["Na"]["context"] = [_equipment_context_row("same_id", "s")]
    extract["species"]["K"] = {
        "context": [
            {
                "observation_id": "same_id",
                "type": "characterization",
                "locator": {"page": 4, "section": "experimental"},
                "units": "as printed",
                "values": {"quantity": "sample_note", "method_class": "method_only"},
            }
        ]
    }
    root = _write_multi_extract_tree(tmp_path, [("dup-ctx", extract)])
    with pytest.raises(DuplicateContextIdError, match="dup-ctx::context::same_id"):
        migrate(root, write=False)


def _bench_experiment_extract(source_id: str, doi: str, bench: str, experiment: str) -> dict:
    doc = _named_extract(source_id, doi)
    doc["benches"] = [
        {"id": bench, "identity": {"basis": "described_in_this_work"}}
    ]
    doc["experiments"] = [
        {
            "experiment_id": experiment,
            "method": "knudsen_effusion",
            "bench_id": bench,
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    return doc


def test_cross_work_bench_and_experiment_ids_are_refused(tmp_path: Path) -> None:
    """A qualified bench or experiment id from another work is not a link.

    The registry records referential_integrity and writes neither the bench
    entry nor the foreign key. The victim records stay owned by the victim.
    """
    victim = _bench_experiment_extract(
        "aaa-victim", "10.1234/VICTIM", "vic-bench", "vic-series"
    )
    root = _write_multi_extract_tree(tmp_path, [("aaa-victim", victim)])
    lifted = migrate(root, write=False)
    victim_bench_id = next(bid for bid in lifted.benches if bid.endswith("::bench::vic-bench"))
    victim_experiment_id = next(
        eid for eid in lifted.experiments if eid.endswith("::experiment::vic-series")
    )
    stolen = "10.1234/victim::bench::stolen"
    attacker = _named_extract("zzz-attacker", "10.1234/ATTACK")
    attacker["benches"] = [
        {"id": stolen, "identity": {"basis": "described_in_this_work"}}
    ]
    attacker["experiments"] = [
        {
            "experiment_id": victim_experiment_id,
            "method": "knudsen_effusion",
            "locator": {"page": 1, "section": "experimental"},
        },
        {
            "experiment_id": "atk",
            "method": "knudsen_effusion",
            "bench_id": victim_bench_id,
            "locator": {"page": 2, "section": "experimental"},
        },
    ]
    attacker["species"]["Na"]["observations"][0]["experiment"] = victim_experiment_id
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-victim", victim), ("zzz-attacker", attacker)]
    )
    result = migrate(root, write=False)
    assert stolen not in result.benches
    assert result.benches[victim_bench_id].work_id != next(
        exp.work_id
        for eid, exp in result.experiments.items()
        if eid.endswith("::experiment::atk")
    )
    attacker_experiment = next(
        exp for eid, exp in result.experiments.items() if eid.endswith("::experiment::atk")
    )
    assert attacker_experiment.bench_id is None
    assert victim_experiment_id not in result.experiments_by_work[attacker_experiment.work_id]
    attacker_obs = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "zzz-attacker"
    ]
    assert attacker_obs
    assert all(obs.experiment_id != victim_experiment_id for obs in attacker_obs)
    issues = [
        issue
        for issue in result.registry_issues
        if issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and "outside work" in issue.detail
    ]
    paths = {issue.path for issue in issues}
    assert f"bench[{stolen}]" in paths
    assert any(path.endswith(".bench_id") and "atk" in path for path in paths)
    assert any(path.startswith("observation[zzz-attacker::") and path.endswith(".experiment") for path in paths)
    assert any(path == f"experiment[{victim_experiment_id}]" for path in paths)
    assert all(victim_bench_id in issue.detail or stolen in issue.detail or victim_experiment_id in issue.detail for issue in issues)


def test_same_work_qualified_bench_and_experiment_resolve(tmp_path: Path) -> None:
    """Two extracts of one work may name that work's bench and experiment
    by the qualified id. The cross-work refusal must not fire."""
    first = _bench_experiment_extract(
        "aaa-first", "10.1234/SHARED", "shared-bench", "shared-series"
    )
    root = _write_multi_extract_tree(tmp_path, [("aaa-first", first)])
    lifted = migrate(root, write=False)
    bench_id = next(bid for bid in lifted.benches if bid.endswith("::bench::shared-bench"))
    experiment_id = next(
        eid for eid in lifted.experiments if eid.endswith("::experiment::shared-series")
    )
    second = _named_extract("bbb-second", "10.1234/SHARED")
    second["experiments"] = [
        {
            "experiment_id": "second-series",
            "method": "knudsen_effusion",
            "bench_id": bench_id,
            "locator": {"page": 3, "section": "experimental"},
        }
    ]
    second["species"]["Na"]["observations"][0]["experiment"] = experiment_id
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-first", first), ("bbb-second", second)]
    )
    result = migrate(root, write=False)
    second_experiment = next(
        exp for eid, exp in result.experiments.items() if eid.endswith("::experiment::second-series")
    )
    assert second_experiment.bench_id == bench_id
    assert result.benches[bench_id].work_id == second_experiment.work_id
    second_obs = [
        obs for obs in result.observations.values() if obs.source_id == "bbb-second"
    ]
    assert second_obs
    assert all(obs.experiment_id == experiment_id for obs in second_obs)
    assert not any("outside work" in issue.detail for issue in result.registry_issues)


def test_validate_flags_cross_work_bench_and_observation(tmp_path: Path) -> None:
    """A hand-edited store whose bench or experiment belongs to another work
    is the same referential_integrity failure the migrator refuses to write."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    related = [
        obs for obs in observations.values() if obs.experiment_id == experiment.experiment_id
    ]
    obs = related[0]
    foreign_bench = Bench(
        id="10.9999/OTHER::bench::foreign",
        work_id="10.9999/OTHER",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    mismatched = replace(experiment, bench_id=foreign_bench.id)
    report = validate_corpus(
        works,
        [mismatched],
        related,
        benches={foreign_bench.id: foreign_bench},
    )
    bench_issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".bench_id") and "belongs to work" in issue.detail
    ]
    assert len(bench_issues) == 1
    assert bench_issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "10.9999/OTHER" in bench_issues[0].detail
    assert experiment.work_id in bench_issues[0].detail
    own_bench = replace(foreign_bench, id="own-bench", work_id=experiment.work_id)
    report = validate_corpus(
        works,
        [replace(experiment, bench_id=own_bench.id)],
        related,
        benches={own_bench.id: own_bench},
    )
    assert not any(
        issue.path.endswith(".bench_id") and "belongs to work" in issue.detail
        for issue in report.issues
    )

    own = next(work for work in works.values() if obs.source_id in work.source_ids)
    foreign_work = replace(
        own, work_id="10.9999/OTHER", source_ids=("other-source",), doi="10.9999/OTHER"
    )
    foreign_exp = replace(
        experiment,
        experiment_id="10.9999/OTHER::experiment::foreign",
        work_id=foreign_work.work_id,
    )
    ghost = replace(obs, experiment_id=foreign_exp.experiment_id)
    report = validate_corpus(
        [own, foreign_work], [experiment, foreign_exp], [ghost]
    )
    obs_issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".experiment_id") and "belongs to work" in issue.detail
    ]
    assert len(obs_issues) == 1
    assert obs_issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert foreign_work.work_id in obs_issues[0].detail
    assert own.work_id in obs_issues[0].detail
    report = validate_corpus(works, experiments, observations)
    assert not any("belongs to work" in issue.detail for issue in report.issues)
def test_g4_compilation_record_path_predicate() -> None:
    assert is_compilation_record_path(
        "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
        "records/robie-hemingway-fisher-1978-usgs-b1452-0003.json"
    )
    assert not is_compilation_record_path(
        "data/literature/compilations/janaf/tables/Al-001.yaml"
    )
    assert not is_compilation_record_path("ocr/missing.md")
    assert not is_compilation_record_path("raw/source/file.pdf")


def test_g4_compilation_record_registers_index_asset(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0003.json",
    )
    result = migrate(root, write=False)
    rel = dest.relative_to(root).as_posix()
    asset_id = compilation_record_asset_id(rel)
    obs_list = [
        obs
        for oid, obs in result.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    ]
    assert obs_list
    assert all(obs.read_from == asset_id for obs in obs_list)
    assert all(obs.locator.source_path == rel for obs in obs_list)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in result.queue
    )
    works = [
        work
        for work in result.works.values()
        if "robie-hemingway-fisher-1978-usgs-b1452" in work.source_ids
    ]
    assert len(works) == 1
    assets = {f.asset_id: f for f in works[0].source_files.files}
    assert asset_id in assets
    assert assets[asset_id].role is AssetRole.COMPILATION_RECORD
    assert assets[asset_id].path == rel
    assert all(obs.read_from in assets for obs in obs_list)


def test_g4_compilation_record_asset_mutation_proof(tmp_path: Path) -> None:
    from simulator.battery import migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0003.json",
    )
    rel = dest.relative_to(root).as_posix()

    live = migrate(root, write=False)
    live_obs = next(
        obs
        for oid, obs in live.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    )
    assert live_obs.read_from == compilation_record_asset_id(rel)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in live.queue
    )

    saved = migrate_mod.is_compilation_record_path
    migrate_mod.is_compilation_record_path = lambda _path: False  # type: ignore[assignment]
    try:
        mutant = migrate(root, write=False)
        assert any(
            "has no matching INDEX asset" in (entry.why or "") for entry in mutant.queue
        )
        mutant_obs = next(
            obs
            for oid, obs in mutant.observations.items()
            if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
        )
        assert str(mutant_obs.read_from).startswith("unknown")
    finally:
        migrate_mod.is_compilation_record_path = saved

    restored = migrate(root, write=False)
    restored_obs = next(
        obs
        for oid, obs in restored.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    )
    assert restored_obs.read_from == compilation_record_asset_id(rel)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in restored.queue
    )


def test_g4_choose_read_from_compilation_does_not_fall_through_to_pdf() -> None:
    from simulator.battery.records import Locator, SourceFile, SourceFiles, Work, State

    work = Work(
        work_id="w",
        citation="c",
        source_ids=("robie-hemingway-fisher-1978-usgs-b1452",),
        source_files=SourceFiles(
            corpus_repo="regolith-corpus",
            corpus_commit=State.unknown("test"),
            files=(
                SourceFile(
                    asset_id="pdf:robie-hemingway-fisher-1978-usgs-b1452",
                    role=AssetRole.PDF,
                    path="raw/robie-hemingway-fisher-1978-usgs-b1452/file.pdf",
                    sha256=State.unknown("test"),
                ),
                SourceFile(
                    asset_id="unknown:robie-hemingway-fisher-1978-usgs-b1452",
                    role=AssetRole.PDFTOTEXT,
                    path="unknown",
                    sha256=State.unknown("test"),
                ),
            ),
        ),
    )
    locator = Locator(
        source_path=(
            "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
            "records/robie-hemingway-fisher-1978-usgs-b1452-0003.json"
        )
    )
    read_from = choose_read_from(work, locator)
    assert str(read_from).startswith("unknown")
    assert not str(read_from).startswith("pdf:")
