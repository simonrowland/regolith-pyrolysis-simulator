"""Tests for literature extract store (t-508): validator + extract_merge + fidelity.

Each acceptance claim has a red-under-reversion pin. Null-hypotheses are stated
in the test docstrings where a prior defect is being locked out.
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_merge as em  # noqa: E402
import migrate_pilot_extracts as mig  # noqa: E402
import validate_literature_extracts as vle  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_extract(**overrides):
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture et al. (2026), Test Journal 1:1"},
        "extraction": {
            "method": "unit_test",
            "date": "2026-08-01",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[fe_alpha_1].values.alpha",
                "value": 0.24,
                "note": "fixture fidelity sample",
                "locator": {"page": 2, "table": "1"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fe_alpha_1",
                        "type": "alpha",
                        "locator": {"table": "1", "page": 2},
                        "T_range_K": [1700.0, 1800.0],
                        "phase": "silicate_melt",
                        "regime": "langmuir_free_evaporation",
                        "units": "dimensionless",
                        "uncertainty": {"note": "fixture ±10% band", "relative": 0.1},
                        "values": {"alpha": 0.24},
                    }
                ]
            }
        },
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def tmp_extracts(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    (d / "_source_priority.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_source_priority.v1",
                "source_priority": {
                    # Non-lexical order: other-source must win over fixture-source
                    # so a pure lexical sort would pick the wrong winner.
                    "alpha": ["other-source", "fixture-source"],
                    "psat_series": ["fixture-source", "other-source"],
                    "gibbs_table": ["fixture-source", "other-source"],
                    "activity_coefficient": ["fixture-source"],
                    "rate_series": ["fixture-source"],
                    "transition_point": ["fixture-source", "other-source"],
                },
            }
        ),
        encoding="utf-8",
    )
    return d


def _write_extract(directory: Path, doc: dict) -> Path:
    path = directory / f"{doc['source_id']}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------


def test_minimal_extract_valid():
    errs = vle.validate_extract_document(
        _minimal_extract(), expected_source_id="fixture-source"
    )
    assert errs == []


def test_refuses_bad_schema_version():
    doc = _minimal_extract(schema_version="v0")
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("schema_version" in e for e in errs)


def test_refuses_source_id_filename_mismatch(tmp_extracts: Path):
    doc = _minimal_extract()
    path = tmp_extracts / "wrong-name.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    errs = vle.validate_extract_file(path)
    assert any("does not match filename stem" in e for e in errs)


def test_refuses_unknown_observation_type():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["type"] = "antoine_fit"
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("type must be one of" in e for e in errs)


def test_refuses_observation_without_locator():
    doc = _minimal_extract()
    del doc["species"]["Fe"]["observations"][0]["locator"]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("locator is required" in e for e in errs)


def test_refuses_equipment_without_locator():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"value": 1.0e-7, "units": "m2"}  # no locator
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("missing locator" in e for e in errs)
    assert any("equipment" in e for e in errs)


def test_refuses_equipment_without_value():
    """Null-hypothesis (P2): locator-only equipment used to validate green."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"locator": {"page": 1}}  # no value
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("missing value" in e for e in errs)


def test_refuses_equipment_null_value():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"value": None, "locator": {"page": 1}}
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("must not be null" in e for e in errs)


def test_refuses_bare_scalar_equipment():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "cell_material": "Mo"  # bare scalar
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("bare" in e or "object with value+locator" in e for e in errs)


def test_refuses_misplaced_equipment_field():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["orifice_area"] = 1.0e-7
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("must live under observation.equipment" in e for e in errs)


def test_accepts_equipment_with_per_field_locator():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 3.14e-7,
            "units": "m2",
            "locator": {"figure": "1b", "page": 4},
        },
        "sample_surface_area": {
            "value": 1.0e-4,
            "units": "m2",
            "inferred": True,
            "inference": "π(d/2)^2 from stated crucible ID 10 mm",
            "locator": {"paragraph": "experimental section", "page": 4},
        },
        "cell_material": {
            "value": "Mo",
            "locator": {"paragraph": "Knudsen cell machined from Mo", "page": 3},
        },
        "multi_orifice_series": {
            "value": False,
            "locator": {"note": "single orifice stated in apparatus"},
        },
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == []


def test_inferred_equipment_requires_derivation_note():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 1e-7,
            "units": "m2",
            "inferred": True,
            "locator": {"page": 1},
            # missing inference/note
        }
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("inferred" in e for e in errs)


def test_inferred_truthy_string_requires_note():
    """Null-hypothesis (P3): identity `is True` let truthy strings bypass the rule."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 1e-7,
            "units": "m2",
            "inferred": "yes",
            "locator": {"page": 1},
        }
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("inferred" in e for e in errs)


def test_values_require_units():
    doc = _minimal_extract()
    del doc["species"]["Fe"]["observations"][0]["units"]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("units required" in e for e in errs)


def test_refuses_empty_values_payload():
    """Null-hypothesis (P1 empty winners): values:{} validated green and could win."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {}
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("empty" in e or "null-only" in e for e in errs)


def test_duplicate_observation_id_refused():
    doc = _minimal_extract()
    obs = copy.deepcopy(doc["species"]["Fe"]["observations"][0])
    doc["species"]["Fe"]["observations"].append(obs)
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in errs)


def test_duplicate_observation_id_cross_species_refused():
    """Null-hypothesis (P2): seen-id set was per-species, so Fe/Na shared ids passed."""
    doc = _minimal_extract()
    doc["species"]["Na"] = {
        "observations": [
            {
                "observation_id": "fe_alpha_1",  # same as Fe
                "type": "alpha",
                "locator": {"page": 1},
                "units": "dimensionless",
                "values": {"alpha": 0.1},
            }
        ]
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in errs)


def test_refuses_missing_fidelity_samples():
    doc = _minimal_extract()
    del doc["fidelity_samples"]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("fidelity_samples" in e for e in errs)


def test_refuses_absolute_provenance_path():
    doc = _minimal_extract()
    doc["extraction"]["provenance_path"] = "/Users/someone/Dropbox/docs-private/x.md"
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("repository-relative" in e for e in errs)


def test_repo_extracts_validate_green():
    """All pilot extracts in the tree must be validator-green."""
    errors = vle.validate_all()
    assert errors == [], "\n".join(errors[:20])


def test_source_priority_file_present_and_valid():
    errs = vle.validate_source_priority_file()
    assert errs == []
    doc = yaml.safe_load((EXTRACTS / "_source_priority.yaml").read_text())
    for fam in vle.OBSERVATION_TYPES:
        assert fam in doc["source_priority"]
        assert doc["source_priority"][fam], f"empty family {fam}"


def test_source_priority_refuses_empty_family(tmp_path: Path):
    p = tmp_path / "_source_priority.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_source_priority.v1",
                "source_priority": {
                    "alpha": [],
                    "psat_series": ["x"],
                    "gibbs_table": ["x"],
                    "activity_coefficient": ["x"],
                    "rate_series": ["x"],
                    "transition_point": ["x"],
                },
            }
        ),
        encoding="utf-8",
    )
    errs = vle.validate_source_priority_file(p)
    assert any("alpha" in e and "non-empty" in e for e in errs)


# ---------------------------------------------------------------------------
# extract_merge
# ---------------------------------------------------------------------------


def test_disagreement_dex_ratio():
    dex = em.disagreement_dex([1.0, 100.0])
    assert dex == pytest.approx(2.0)


def test_disagreement_dex_single_is_none():
    """Null-hypothesis (P1): singleton groups used to receive non-null dex."""
    assert em.disagreement_dex([1.0]) is None
    assert em.disagreement_dex([]) is None


def test_disagreement_dex_no_linear_span_invent():
    """Null-hypothesis (P1): non-positive spreads used to become log10(|Δ|)."""
    assert em.disagreement_dex([-5.0, 3.0]) is None
    assert em.disagreement_dex([0.0, 0.0]) is None


def test_comparable_scalar_not_cross_unit_bag():
    """Null-hypothesis (P1): CEA row with MW+ΔfH+P° must not yield a bag dex scalar set."""
    values = {
        "molecular_weight_g_per_mol": 26.98,
        "delta_f_H_298_15_J_per_mol": 330000.0,
        "reference_pressure_Pa": 100000.0,
    }
    # gibbs prefers delta_f_H — one scalar, not three
    sc = em.comparable_scalar(values, "gibbs_table")
    assert sc == pytest.approx(330000.0)


def test_by_species_retains_competitors_and_marks_winner(tmp_extracts: Path):
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_alpha_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_other].values.alpha",
            "value": 0.02,
            "note": "other",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)

    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)

    fe = view["species"]["Fe"]
    assert len(fe["observations"]) == 2
    winners = [o for o in fe["observations"] if o.get("is_priority_winner")]
    assert len(winners) == 1
    # priority list is [other-source, fixture-source] — non-lexical
    assert winners[0]["source_id"] == "other-source"

    groups = fe["observable_groups"]
    assert len(groups) == 1
    dex = groups[0]["disagreement_dex"]
    assert dex is not None
    assert dex == pytest.approx(math.log10(0.24 / 0.02))


def test_unlisted_source_cannot_win_lexically(tmp_extracts: Path):
    """Null-hypothesis (P1 VALUE-PRECEDENCE fail-open): unlisted → lexical winner."""
    a = _minimal_extract()
    a["source_id"] = "zzz-unlisted"
    a["species"]["Fe"]["observations"][0]["observation_id"] = "fe_z"
    b = _minimal_extract()
    b["source_id"] = "aaa-unlisted"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_a"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.01}
    b["fidelity_samples"] = [
        {
            "path": "x",
            "value": 1,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    a["fidelity_samples"] = b["fidelity_samples"]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)
    winners = [o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")]
    assert winners == []  # neither listed in alpha priority


def test_empty_payload_cannot_win(tmp_extracts: Path):
    """Null-hypothesis (P1): empty Ga2O-like row outranked populated competitor."""
    empty = _minimal_extract()
    empty["source_id"] = "other-source"  # higher priority
    empty["species"]["Fe"]["observations"][0]["observation_id"] = "fe_empty"
    # Bypass validator for this unit: inject empty after load path via build direct
    empty["species"]["Fe"]["observations"][0]["values"] = {"alpha": None, "note": None}
    # actually null-only leaves fail _has_payload_leaf if all null
    empty["species"]["Fe"]["observations"][0]["values"] = {}
    # Build without validator
    populated = _minimal_extract()
    populated["source_id"] = "fixture-source"
    extracts = [empty, populated]
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)
    winners = [o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")]
    assert len(winners) == 1
    assert winners[0]["source_id"] == "fixture-source"
    assert winners[0]["values"] == {"alpha": 0.24}


def test_regime_difference_not_merged_as_one_conflict(tmp_extracts: Path):
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_alpha_kems"
    b["species"]["Fe"]["observations"][0]["regime"] = "kems_effusion"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {"path": "x", "value": 1, "note": "n", "locator": {"page": 1}}
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)

    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(extracts)
    groups = view["species"]["Fe"]["observable_groups"]
    assert len(groups) == 2  # different regimes → separate groups


def test_phase_and_property_split_observables(tmp_extracts: Path):
    """Null-hypothesis (P1-M1): (type, regime) only mixed mp+bp / gas+solid."""
    a = _minimal_extract()
    a["species"]["Fe"]["observations"] = [
        {
            "observation_id": "fe_mp",
            "type": "transition_point",
            "locator": {"page": 1},
            "units": "K",
            "phase": "solid_liquid",
            "values": {"property": "melting_point", "T_K": 1809.0},
        },
        {
            "observation_id": "fe_bp",
            "type": "transition_point",
            "locator": {"page": 1},
            "units": "K",
            "phase": "liquid_gas",
            "values": {"property": "boiling_point", "T_K": 3134.0},
        },
    ]
    a["fidelity_samples"] = [
        {"path": "x", "value": 1809, "note": "n", "locator": {"page": 1}}
    ]
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(
        extracts, source_priority=em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    )
    groups = view["species"]["Fe"]["observable_groups"]
    assert len(groups) == 2
    assert all(g.get("disagreement_dex") is None for g in groups)


def test_uncertainty_propagated_to_by_species(tmp_extracts: Path):
    """Owner 2026-08-02: uncertainty is first-class; merge propagates verbatim."""
    a = _minimal_extract()
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(
        extracts, source_priority=em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    )
    obs = view["species"]["Fe"]["observations"][0]
    assert obs["uncertainty"] == {"note": "fixture ±10% band", "relative": 0.1}
    group = view["species"]["Fe"]["observable_groups"][0]
    assert group["winner_uncertainty"] == obs["uncertainty"]
    assert group["uncertainties"]
    assert group["uncertainties"][0]["uncertainty"]["relative"] == 0.1


def test_consistency_report_auto_computes_disagreement(tmp_extracts: Path):
    """Owner 2026-08-02: computed disagreement_dex, no hand curation."""
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {"path": "x", "value": 0.02, "note": "n", "locator": {"page": 1}}
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    report = em.build_consistency_report(extracts, source_priority=priority)
    assert report["kind"] == "cross_source_consistency"
    assert report["n_multi_source_groups"] >= 1
    assert report["n_with_disagreement_dex"] >= 1
    conflict = report["conflicts"][0]
    assert conflict["species_id"] == "Fe"
    assert conflict["disagreement_dex"] == pytest.approx(math.log10(0.24 / 0.02))
    assert "uncertainties" in conflict


def test_coverage_payload_aware(tmp_extracts: Path):
    """Null-hypothesis (P2-M2 / KM-M2): empty / pointer payloads counted as found.

    Empty observation lists and empty/null-only values report **empty**
    (species present, no usable datum), distinct from **absent** (species not
    listed on the source). Pending/pointer acquisition stubs also report empty.
    """
    a = _minimal_extract()
    # empty payload species via direct build (bypass validator)
    empty_doc = {
        "source_id": "empty-src",
        "species": {
            "As4O6": {
                "observations": [
                    {
                        "observation_id": "pending",
                        "type": "psat_series",
                        "values": {},
                    }
                ]
            },
            # Empty observation list: present but unusable → empty, not found.
            "Bi2O3": {"observations": []},
        },
    }
    # Structured pointer stub (quantity + planned T window) — no measured datum.
    pending_doc = {
        "source_id": "pending-as2o3-second-primary",
        "species": {
            "As4O6": {
                "observations": [
                    {
                        "observation_id": "anchor_As4O6_pure_Psat",
                        "type": "psat_series",
                        "locator": {
                            "record": "PENDING#As2O3_second_primary",
                            "note": "planned second primary",
                        },
                        "units": "as published",
                        "values": {
                            "quantity": "pure_Psat",
                            "T_range_K": [369, 730],
                            "phase": "solid_As2O3_arsenolite_to_As4O6_g",
                            "standard_state": "As4O6(g); condensed arsenolite",
                        },
                    }
                ]
            }
        },
    }
    # Explicit pointer semantics (real acquired source, pointer-only row).
    pointer_doc = {
        "source_id": "pointer-src",
        "species": {
            "NaCl": {
                "observations": [
                    {
                        "observation_id": "nacl_side",
                        "type": "gibbs_table",
                        "values": {
                            "role": "monomer_side",
                            "semantics": "pointer_to_dimer_equilibrium",
                        },
                    }
                ]
            }
        },
    }
    cov = em.build_coverage(
        [a, empty_doc, pending_doc, pointer_doc],
        manifest_ids=["Fe", "As4O6", "Bi2O3", "NaCl", "SiO"],
    )
    by_id = {r["species_id"]: r["sources"] for r in cov["rows"]}
    assert by_id["Fe"]["fixture-source"] == "found"
    # Empty values payload → empty (not found, not absent).
    assert by_id["As4O6"]["empty-src"] == "empty"
    # Empty observation list → empty, distinct from absent.
    assert by_id["Bi2O3"]["empty-src"] == "empty"
    assert by_id["Bi2O3"]["fixture-source"] == "absent"
    # Pending stub with structured pointer payload → empty, not found.
    assert by_id["As4O6"]["pending-as2o3-second-primary"] == "empty"
    # Explicit pointer semantics → empty.
    assert by_id["NaCl"]["pointer-src"] == "empty"
    # Species never listed on a source → absent.
    assert by_id["SiO"]["fixture-source"] == "absent"
    assert by_id["SiO"]["empty-src"] == "absent"
    assert cov["cells_empty"] >= 4


def test_coverage_found_absent(tmp_extracts: Path):
    a = _minimal_extract()
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    cov = em.build_coverage(extracts, manifest_ids=["Fe", "Na", "SiO"])
    assert cov["source_ids"] == ["fixture-source"]
    by_id = {r["species_id"]: r["sources"] for r in cov["rows"]}
    assert by_id["Fe"]["fixture-source"] == "found"
    assert by_id["Na"]["fixture-source"] == "absent"
    assert by_id["SiO"]["fixture-source"] == "absent"


def test_coverage_pending_stubs_not_found():
    """Null-hypothesis (KM-M2 STILL-OPEN): all 13 pending-* stubs report found.

    Pending acquisition stubs carry structured pointer payloads (quantity label
    + planned T_range_K) that pass ``_payload_present``; coverage must not
    overstate acquisition where acquisition is still pending.
    """
    extracts = em.load_extracts(EXTRACTS, require_valid=True)
    cov = em.build_coverage(extracts)
    pending_srcs = [s for s in cov["source_ids"] if str(s).startswith("pending-")]
    assert len(pending_srcs) == 13, f"expected 13 pending stubs, got {pending_srcs}"
    found_pending = [
        (row["species_id"], src, state)
        for row in cov["rows"]
        for src, state in row["sources"].items()
        if str(src).startswith("pending-") and state == "found"
    ]
    assert found_pending == [], f"pending stubs still report found: {found_pending}"
    # At least one pending cell is empty (species listed, no measured datum).
    empty_pending = [
        (row["species_id"], src)
        for row in cov["rows"]
        for src, state in row["sources"].items()
        if str(src).startswith("pending-") and state == "empty"
    ]
    assert len(empty_pending) == 13, empty_pending


def test_merge_cli_writes_and_uses_custom_priority(tmp_path: Path, tmp_extracts: Path):
    """Null-hypothesis (P2): custom extracts dir silently used repo-global priority."""
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {"path": "x", "value": 0.02, "note": "n", "locator": {"page": 1}}
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    outdir = tmp_path / "out"
    rc = em.main(
        [
            "--by-species",
            "--coverage",
            "--consistency",
            "--outdir",
            str(outdir),
            "--extracts-dir",
            str(tmp_extracts),
        ]
    )
    assert rc == 0
    assert (outdir / "by_species.yaml").is_file()
    assert (outdir / "coverage.yaml").is_file()
    assert (outdir / "consistency.yaml").is_file()
    view = yaml.safe_load((outdir / "by_species.yaml").read_text())
    winners = [
        o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")
    ]
    assert winners[0]["source_id"] == "other-source"  # custom priority, not lexical


def test_merge_refuses_invalid_extracts(tmp_extracts: Path):
    bad = _minimal_extract()
    bad["schema_version"] = "nope"
    _write_extract(tmp_extracts, bad)
    with pytest.raises(SystemExit):
        em.load_extracts(tmp_extracts, require_valid=True)


def test_pilot_extract_count_exact_and_merge_smoke():
    """Pilot tree count is pinned; merge produces species + coverage + consistency."""
    files = vle.discover_extracts()
    # 63 original + new correctly-attributed sources from F3 routing
    assert len(files) >= 63
    assert len(files) == 68  # exact pilot census after F3 re-route
    extracts = em.load_extracts(require_valid=True)
    assert len(extracts) == len(files)
    view = em.build_by_species(extracts)
    assert "species" in view and len(view["species"]) >= 1
    if "Fe" in view["species"]:
        types = {o["type"] for o in view["species"]["Fe"]["observations"]}
        assert "alpha" in types or "gibbs_table" in types or "transition_point" in types
    cov = em.build_coverage(extracts)
    assert cov["source_count"] == len(files)
    assert cov["cells_found"] >= 1
    report = em.build_consistency_report(extracts)
    assert report["kind"] == "cross_source_consistency"
    assert "conflicts" in report


# ---------------------------------------------------------------------------
# Migrator fidelity pins (P1-F1 / F2 / F3 / Datz / CEA)
# ---------------------------------------------------------------------------


def test_alpha_form_retained_in_pilot_extracts():
    """Null-hypothesis (P1-F1): alpha_form dropped → null alphas for Wetzel/Richter."""
    w = yaml.safe_load(
        (EXTRACTS / "wetzel-gail-2013-sio-arrhenius.yaml").read_text(encoding="utf-8")
    )
    form = w["species"]["SiO"]["observations"][0]["values"].get("alpha_form")
    assert form is not None
    assert form["A"] == pytest.approx(0.52)
    assert form["B_K"] == pytest.approx(3685.0)
    unc = w["species"]["SiO"]["observations"][0].get("uncertainty") or {}
    assert unc.get("uncertainty_envelope") == [0.003, 0.067]

    r = yaml.safe_load((EXTRACTS / "richter-et-al-2007.yaml").read_text(encoding="utf-8"))
    assert r["source"]["year"] == 2007
    mg = r["species"]["Mg"]["observations"][0]["values"]["alpha_form"]
    assert mg["c0"] == pytest.approx(143.0)


def test_janaf_species_not_list_index_keys():
    """Null-hypothesis (P1-F2): PO rows keyed as tabulated_delta_fG_kJ_mol[0]."""
    j = yaml.safe_load((EXTRACTS / "janaf-4th.yaml").read_text(encoding="utf-8"))
    assert "PO" in j["species"]
    assert not any("[" in k or k.startswith("tabulated_") for k in j["species"])
    po_obs = j["species"]["PO"]["observations"]
    tab = next(
        o
        for o in po_obs
        if "formation_tabulation" in str(o.get("observation_id"))
        or "tabulated_delta_fG" in str(o.get("values"))
    )
    assert tab["type"] == "gibbs_table"
    assert "tabulated_delta_fG_kJ_mol" in (tab.get("values") or {})
    assert tab["locator"].get("table") or tab["locator"].get("note")
    assert tab["locator"].get("note") != "{}"


def test_datz_recorded_disagreement_retained():
    """Null-hypothesis (P1): competing JANAF/WebBook observation dropped pre-merge."""
    d = yaml.safe_load(
        (EXTRACTS / "datz-and-smith-1961.yaml").read_text(encoding="utf-8")
    )
    ids = [o["observation_id"] for o in d["species"]["Na2Cl2"]["observations"]]
    assert "Datz1961_TableII_Kd" in ids
    assert any("JANAF" in i or "Shomate" in i for i in ids)


def test_cea_source_ref_and_relative_provenance():
    """Null-hypothesis (P2): Ag source_ref_code truncated; absolute Dropbox path."""
    c = yaml.safe_load((EXTRACTS / "nasa-cea-thermo.yaml").read_text(encoding="utf-8"))
    assert c["species"]["Ag"]["observations"][0]["values"]["source_ref_code"] == "g10/97"
    prov = c["extraction"]["provenance_path"]
    assert not prov.startswith("/")
    assert "docs-private" in prov


def test_lh84_top_level_payloads_not_empty():
    """Null-hypothesis (P1-F3): top-level coefficients/points migrated as values:{}."""
    # Stull Se Antoine must not live under LH84 (wrong-source); coefficients retained
    # under nist-webbook or a dedicated extract.
    nist = yaml.safe_load((EXTRACTS / "nist-webbook.yaml").read_text(encoding="utf-8"))
    # Se may be under Se_n_ladder
    found_coeff = False
    for sid, block in nist.get("species", {}).items():
        for obs in block.get("observations") or []:
            vals = obs.get("values") or {}
            if isinstance(vals, dict) and "coefficients" in vals:
                found_coeff = True
                coeffs = vals["coefficients"]
                assert coeffs  # non-empty
    br = EXTRACTS / "behrens-rosenblatt-1972.yaml"
    assert br.is_file()
    br_doc = yaml.safe_load(br.read_text(encoding="utf-8"))
    as4 = br_doc["species"]["As4O6"]["observations"][0]["values"]
    assert "coefficients" in as4
    assert as4["coefficients"].get("A") is not None or "A" in str(as4["coefficients"])

    lh = yaml.safe_load(
        (EXTRACTS / "lamoreaux-hildenbrand-1984.yaml").read_text(encoding="utf-8")
    )
    for sid, block in lh["species"].items():
        for obs in block.get("observations") or []:
            assert obs.get("values"), f"empty values under LH84 {sid} {obs.get('observation_id')}"


def test_corr_payload_helper_merges_top_level():
    """Unit pin for P1-F3 payload collection."""
    corr = {
        "id": "x",
        "kind": "pure_component_antoine",
        "coefficients": {"A": 6.3, "B": 6500, "C": 80},
        "source": {"citation": "Stull"},
        "values": {},
    }
    payload = mig._corr_payload(corr)
    assert payload["coefficients"]["A"] == pytest.approx(6.3)


def test_every_extract_has_fidelity_sample():
    for path in vle.discover_extracts():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        n_obs = sum(
            len(b.get("observations") or [])
            for b in (doc.get("species") or {}).values()
            if isinstance(b, dict)
        )
        if n_obs:
            assert doc.get("fidelity_samples"), f"{path.name} missing fidelity_samples"


def test_singleton_series_disagreement_is_none():
    """Null-hypothesis (P1): 157 singleton groups received non-null dex."""
    # Build a one-observation group and ensure dex is None
    extracts = [
        {
            "source_id": "only-src",
            "species": {
                "Fe": {
                    "observations": [
                        {
                            "observation_id": "one",
                            "type": "alpha",
                            "regime": "langmuir_free_evaporation",
                            "units": "dimensionless",
                            "values": {"alpha": 0.24},
                            "uncertainty": {"note": "x"},
                        }
                    ]
                }
            },
        }
    ]
    view = em.build_by_species(extracts, source_priority={"alpha": ["only-src"]})
    g = view["species"]["Fe"]["observable_groups"][0]
    assert g["disagreement_dex"] is None
