"""Tests for literature extract store (t-508): validator + extract_merge + fidelity.

Each acceptance claim has a red-under-reversion pin. Null-hypotheses are stated
in the test docstrings where a prior defect is being locked out.

t-510 fidelity gate:
* ENFORCED_FOR_NEW sample presence (pre-policy allowlist is shrink-only)
* parameterized match of every fidelity sample against extract content
* mutation of a pinned value must go RED
"""

from __future__ import annotations

import copy
import hashlib
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"
FIDELITY_POLICY = EXTRACTS / "_fidelity_pre_policy_allowlist.yaml"
FIDELITY_GRADUATION_LEDGER = EXTRACTS / "_fidelity_graduation_ledger.yaml"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_merge as em  # noqa: E402
import migrate_pilot_extracts as mig  # noqa: E402
import validate_literature_extracts as vle  # noqa: E402

# Frozen closed-set hash at t-510 policy adoption (sorted source_ids joined by \n).
# Null-hypothesis: an extract can be ADDED to the allowlist later → closed set
# grows → this hash drifts and the shrink-only test goes RED.
CLOSED_SET_SHA256 = (
    "7784107b6656e0ab1c1e2ef9c33fe95a6108b6585a91e4e8ca1b5021960c8e9b"
)
# Canonical append-only graduation history hash (sorted ids joined by \n).
# Updating this pin is an explicit review event; removing a prior tombstone
# must never be bundled into a policy rewrite.
GRADUATION_LEDGER_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


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
    """Null-hypothesis (t-510): new extracts without samples used to validate green."""
    doc = _minimal_extract()
    del doc["fidelity_samples"]
    # pre_policy_ids=None → samples required (non-allowlisted fixture)
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("fidelity_samples" in e for e in errs)


def test_pre_policy_allowlist_exempts_missing_samples():
    """Grandfathering: pilot source_ids may omit samples under ENFORCED_FOR_NEW."""
    doc = _minimal_extract()
    doc["source_id"] = "costa-jacobson-2015"
    del doc["fidelity_samples"]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="costa-jacobson-2015",
        pre_policy_ids={"costa-jacobson-2015"},
    )
    assert errs == [], errs


def test_non_allowlisted_still_refuses_missing_samples():
    """Null-hypothesis: allowlist exemption must not leak to new source_ids."""
    doc = _minimal_extract()
    doc["source_id"] = "brand-new-ocr-source-2026"
    del doc["fidelity_samples"]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="brand-new-ocr-source-2026",
        pre_policy_ids={"costa-jacobson-2015"},  # not this one
    )
    assert any("fidelity_samples" in e for e in errs)


def test_structured_fidelity_sample_accepted():
    """OCR-style structured samples (species/observable/value/locator) validate."""
    doc = _minimal_extract()
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "field": "alpha",
            "value": 0.24,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == []
    match_errs = vle.check_all_fidelity_samples_match(doc, label="fixture")
    assert match_errs == []


def test_structured_sample_requires_locator():
    doc = _minimal_extract()
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "value": 0.24,
            # no locator
        }
    ]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("locator" in e for e in errs)


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
            "path": "species.Fe.observations[fe_a].values.alpha",
            "value": 0.01,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    a["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_z].values.alpha",
            "value": 0.24,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
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
        {
            "path": "species.Fe.observations[fe_alpha_kems].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
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
        {
            "path": "species.Fe.observations[fe_mp].values.T_K",
            "value": 1809.0,
            "note": "n",
            "locator": {"page": 1},
        }
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
        {
            "path": "species.Fe.observations[fe_other].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
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
        {
            "path": "species.Fe.observations[fe_other].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
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
    """Pilot census (68) remains; live corpus may grow with post-policy extracts.

    Merge produces species + coverage + consistency over the full live set.
    """
    files = vle.discover_extracts()
    policy = _fidelity_policy_doc()
    closed = set(policy["closed_set_source_ids"])
    stems = {p.stem for p in files}
    assert len(closed) == 68  # frozen pilot census
    assert closed <= stems  # pilot files still present
    assert len(files) >= 68  # live corpus may include t-509 OCR extracts
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
    """Pilot corpus currently carries path-based pins; keep them as drift fuses.

    ENFORCED_FOR_NEW permits allowlisted extracts to omit samples, but the
    landed pilot set still has auto-migrated pins — assert they remain so a
    silent strip does not go unnoticed.
    """
    for path in vle.discover_extracts():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        n_obs = sum(
            len(b.get("observations") or [])
            for b in (doc.get("species") or {}).values()
            if isinstance(b, dict)
        )
        if n_obs:
            assert doc.get("fidelity_samples"), f"{path.name} missing fidelity_samples"


# ---------------------------------------------------------------------------
# t-510 fidelity gate: policy + parameterized match + mutation red
# ---------------------------------------------------------------------------


def _fidelity_policy_doc() -> dict:
    assert FIDELITY_POLICY.is_file(), f"missing {FIDELITY_POLICY}"
    doc = yaml.safe_load(FIDELITY_POLICY.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def test_fidelity_policy_file_valid():
    """Policy file loads; active ⊆ closed; partition with graduated; fields correct."""
    policy, errs = vle.load_fidelity_policy()
    assert errs == [], errs
    assert policy["policy"] == "ENFORCED_FOR_NEW"
    assert policy["effective_date"] == "2026-08-03"
    closed = set(policy["closed_set_source_ids"])
    active = set(policy["active_pre_policy_source_ids"])
    graduated = set(policy.get("graduated_pre_policy_source_ids") or [])
    assert active <= closed
    assert graduated <= closed
    assert active & graduated == set()
    assert active | graduated == closed
    assert len(closed) == 68


def test_fidelity_graduation_ledger_matches_policy_and_hash():
    """Canonical history mirrors tombstones and is independently hash-pinned."""
    ledger, ledger_errs = vle.load_fidelity_graduation_ledger(
        FIDELITY_GRADUATION_LEDGER
    )
    assert ledger_errs == [], ledger_errs
    policy = _fidelity_policy_doc()
    assert ledger == set(policy.get("graduated_pre_policy_source_ids") or [])
    payload = "\n".join(sorted(ledger)).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == GRADUATION_LEDGER_SHA256


def test_fidelity_closed_set_hash_pinned():
    """Null-hypothesis: closed set can grow (extract ADDED to allowlist later).

    The closed set is frozen at policy adoption. Any addition changes the
    SHA-256 of the sorted joined id list and this test goes RED.
    """
    policy = _fidelity_policy_doc()
    closed = policy["closed_set_source_ids"]
    payload = "\n".join(sorted(closed)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == CLOSED_SET_SHA256, (
        f"closed_set_source_ids changed (shrink-only violation or hash drift).\n"
        f"expected={CLOSED_SET_SHA256}\nactual={digest}\n"
        f"count={len(closed)}"
    )


def test_fidelity_allowlist_rejects_active_not_subset(tmp_path: Path):
    """Null-hypothesis: active can include ids outside closed (growth via active)."""
    bad = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a", "sneaky-new-extract"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    p = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    _policy, errs = vle.load_fidelity_policy(p)
    assert any("shrink-only" in e or "not ⊆" in e for e in errs), errs


def test_fidelity_allowlist_rejects_remove_then_readd(tmp_path: Path):
    """Null-hypothesis (P2): graduated id can return to active after removal.

    Shrink without tombstone, then re-add, must go RED — either because the
    graduated set still contains the id, or because a silent remove without
    graduation breaks the closed-set partition.
    """
    # Step 1: legal graduation of b.
    graduated = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    p = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    ledger_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
                "graduated_pre_policy_source_ids": ["b"],
            }
        ),
        encoding="utf-8",
    )
    p.write_text(yaml.safe_dump(graduated), encoding="utf-8")
    _ok, errs_ok = vle.load_fidelity_policy(p)
    assert errs_ok == [], errs_ok

    # Step 2: illegal re-activation of b while still tombstoned.
    reactivated = dict(graduated)
    reactivated["active_pre_policy_source_ids"] = ["a", "b"]
    p.write_text(yaml.safe_dump(reactivated), encoding="utf-8")
    _bad, errs = vle.load_fidelity_policy(p)
    assert any("re-activation" in e or "∩" in e for e in errs), errs

    # Silent remove without tombstone also refused (partition incomplete).
    silent = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": [],
    }
    p.write_text(yaml.safe_dump(silent), encoding="utf-8")
    _silent, silent_errs = vle.load_fidelity_policy(p)
    assert any("graduate" in e or "missing" in e for e in silent_errs), silent_errs


def test_fidelity_allowlist_rejects_tombstone_delete_then_reactivate(tmp_path: Path):
    """Null-hypothesis (P2): deleting a tombstone permits reactivation.

    Committed Git history is independent of both mutable current-state files.
    Once ``b`` is recorded, no rewrite may make it active again, including
    deleting it from both the policy tombstones and canonical ledger together.
    """
    policy_path = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    graduated = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    ledger = {
        "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
        "graduated_pre_policy_source_ids": ["b"],
    }
    policy_path.write_text(yaml.safe_dump(graduated), encoding="utf-8")
    ledger_path.write_text(yaml.safe_dump(ledger), encoding="utf-8")
    _ok, ok_errs = vle.load_fidelity_policy(
        policy_path,
        graduated_ledger_path=ledger_path,
        prior_graduated_source_ids={"b"},
    )
    assert ok_errs == [], ok_errs

    reactivated = dict(graduated)
    reactivated["active_pre_policy_source_ids"] = ["a", "b"]
    reactivated["graduated_pre_policy_source_ids"] = []
    policy_path.write_text(yaml.safe_dump(reactivated), encoding="utf-8")
    # Delete the mutable ledger entry too. Only external prior history can
    # distinguish this recreated initial-looking state from a legitimate one.
    ledger["graduated_pre_policy_source_ids"] = []
    ledger_path.write_text(yaml.safe_dump(ledger), encoding="utf-8")
    _bad, errs = vle.load_fidelity_policy(
        policy_path,
        graduated_ledger_path=ledger_path,
        prior_graduated_source_ids={"b"},
    )
    assert any("prior Git history" in e or "prior canonical" in e for e in errs), errs


def test_default_policy_validation_uses_committed_graduation_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Null-hypothesis (P2): prior-state checking exists only in a test-only API."""
    policy_path = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_policy.v1",
                "policy": "ENFORCED_FOR_NEW",
                "effective_date": "2026-08-03",
                "closed_set_source_ids": ["a", "b"],
                "active_pre_policy_source_ids": ["a", "b"],
                "graduated_pre_policy_source_ids": [],
            }
        ),
        encoding="utf-8",
    )
    ledger_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
                "graduated_pre_policy_source_ids": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vle, "FIDELITY_POLICY_PATH", policy_path)
    monkeypatch.setattr(vle, "FIDELITY_GRADUATION_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(
        vle,
        "load_committed_fidelity_graduation_history",
        lambda _path: ({"b"}, []),
    )
    _policy, errs = vle.load_fidelity_policy()
    assert any("prior Git history" in e or "prior canonical" in e for e in errs), errs


def test_fidelity_allowlist_covers_pilot_census():
    """Frozen pilot census remains present; live corpus may grow past it.

    Null-hypothesis (P1): equating live stems to the closed set blocks the
    first compliant t-509 extract. Require closed ⊆ stems instead, and allow
    additional non-allowlisted stems that carry samples.
    """
    policy = _fidelity_policy_doc()
    closed = set(policy["closed_set_source_ids"])
    active = set(policy["active_pre_policy_source_ids"])
    stems = {p.stem for p in vle.discover_extracts()}
    # Frozen pilot set still present in the live corpus.
    assert closed <= stems, f"missing pilot extracts: {sorted(closed - stems)}"
    assert active <= closed
    # New (post-policy) stems are allowed and must not be on the active allowlist.
    new_stems = stems - closed
    assert new_stems.isdisjoint(active)


def test_new_extract_with_valid_sample_admitted():
    """Acceptance: a 69th non-allowlisted extract with a valid sample validates.

    Null-hypothesis (P1 census): the suite treated live corpus == closed set,
    so any new stem made the suite red even with reviewer-verified samples.
    """
    doc = _minimal_extract()
    doc["source_id"] = "brand-new-ocr-source-2026"
    # Structured observation pin (OCR form) — required for new extracts.
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "field": "alpha",
            "value": 0.24,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="brand-new-ocr-source-2026",
        pre_policy_ids=set(),  # not allowlisted
        check_fidelity_match=True,
    )
    assert errs == [], errs


def test_new_extract_metadata_only_path_sample_refused():
    """Null-hypothesis (P1): path samples can pin metadata instead of evidence.

    A non-allowlisted extract with only a path pin on source_id used to
    validate green under ENFORCED_FOR_NEW without checking a publication value.
    """
    doc = _minimal_extract()
    doc["source_id"] = "fixture-source"
    doc["fidelity_samples"] = [
        {
            "path": "source_id",
            "value": "fixture-source",
            "note": "not an observation pin",
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("observation evidence" in e or "metadata" in e for e in errs), errs


def test_new_extract_mixed_metadata_path_and_structured_sample_refused():
    """Null-hypothesis (P1): structured decoration legitimizes a metadata path.

    Addressing mode must be unambiguous and every present path must identify
    observation evidence, independent of any additional sample fields.
    """
    doc = _minimal_extract()
    doc["source_id"] = "fixture-source"
    doc["fidelity_samples"] = [
        {
            "path": "source_id",
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "alpha",
            "value": "fixture-source",
            "locator": {"page": 2},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("addressing mode" in e or "observation evidence" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(
        doc, doc["fidelity_samples"][0], label="mixed-addressing"
    )
    assert any("addressing mode" in e for e in direct_errs), direct_errs


@pytest.mark.parametrize(
    "conflicting_keys",
    [
        {"value_key": "bogus"},
        {"draft_value": 999.0},
        {"index": 0, "T_K": 1700.0},
        {"T_K": 1700.0, "T": 1800.0},
    ],
    ids=["field-value_key", "value-draft_value", "index-temperature", "T_K-T"],
)
def test_fidelity_rejects_conflicting_structured_selectors(conflicting_keys: dict):
    """Null-hypothesis (P1): precedence silently ignores contradictory selectors."""
    doc = _minimal_extract()
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "alpha",
        "field": "alpha",
        "value": 0.24,
        "locator": {"page": 2},
        **conflicting_keys,
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("exactly one" in e or "not both" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample), sample


def test_fidelity_observation_id_and_observable_must_agree():
    """Null-hypothesis (P1): observation_id precedence ignores wrong observable."""
    doc = _minimal_extract()
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "psat_series",
        "field": "alpha",
        "value": 0.24,
        "locator": {"page": 2},
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("not requested observable" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample)


@pytest.mark.parametrize(
    "source_locator",
    [{"page": 999}, None],
    ids=["contradictory", "null-decoration"],
)
def test_fidelity_rejects_locator_alias_decoration(source_locator):
    """Null-hypothesis (P1): locator precedence ignores source_locator."""
    doc = _minimal_extract()
    sample = copy.deepcopy(doc["fidelity_samples"][0])
    sample["source_locator"] = source_locator
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("exactly one" in e or "source_locator" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample)


@pytest.mark.parametrize(
    "rel_tol", [True, float("inf"), float("nan"), -0.1, None, "0.1"]
)
def test_fidelity_rejects_invalid_relative_tolerance(rel_tol):
    """Null-hypothesis (P2): invalid tolerance bypasses or crashes exact match."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 20.0,
        "rel_tol": rel_tol,
        "note": "invalid tolerance",
        "locator": {"page": 1},
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("rel_tol" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(doc, sample)
    assert any("rel_tol" in e for e in direct_errs), direct_errs


def test_fidelity_accepts_bounded_relative_tolerance_without_type_coercion():
    """A finite explicit tolerance relaxes magnitude only for equal numeric types."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.001,
        "rel_tol": 0.001,
        "note": "bounded tolerance",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample) == []
    sample["value"] = 10
    assert vle.check_fidelity_sample_matches(doc, sample), "rel_tol must not coerce int"


def test_new_extract_null_sample_value_refused():
    """Null-hypothesis (P1): a null pin satisfies the mandatory fidelity gate."""
    doc = _minimal_extract()
    # Add a null sibling field and pin it.
    doc["species"]["Fe"]["observations"][0]["values"]["sibling_null"] = None
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "sibling_null",
            "value": None,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("null" in e for e in errs), errs


@pytest.mark.parametrize("empty_scalar", ["", "   "])
def test_new_extract_empty_scalar_sample_value_refused(empty_scalar: str):
    """Null-hypothesis (P1): empty scalar pins satisfy the fidelity gate."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"]["empty_scalar"] = empty_scalar
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "empty_scalar",
            "value": empty_scalar,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("non-null observed pin" in e or "empty" in e for e in errs), errs


@pytest.mark.parametrize(
    "empty_scalar",
    [
        yaml.safe_load("!!binary ''"),
        yaml.safe_load("!!binary ICAg"),
        yaml.safe_load("!!set {}"),
    ],
    ids=["empty-binary", "whitespace-binary", "empty-set"],
)
def test_new_extract_serialized_empty_scalar_sample_value_refused(empty_scalar):
    """Null-hypothesis (P1): alternate YAML empty scalar types stay green."""
    doc = _minimal_extract()
    body_value = copy.deepcopy(empty_scalar)
    doc["species"]["Fe"]["observations"][0]["values"] = {
        "empty_scalar": body_value
    }
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "alpha",
        "field": "empty_scalar",
        "value": copy.deepcopy(empty_scalar),
        "locator": {"page": 2},
    }
    doc["fidelity_samples"] = [sample]
    loaded = yaml.safe_load(yaml.safe_dump(doc, sort_keys=False))
    errs = vle.validate_extract_document(
        loaded,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("non-null observed pin" in e or "empty" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(
        loaded, loaded["fidelity_samples"][0]
    )
    assert any("non-null observed pin" in e or "empty" in e for e in direct_errs)


def test_fidelity_shared_alias_identity_rejected():
    """Null-hypothesis (P1): YAML-aliased sample co-mutates with body → always green."""
    doc = _minimal_extract()
    values = doc["species"]["Fe"]["observations"][0]["values"]
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": values,  # shared object identity (alias)
            "note": "aliased pin",
            "locator": {"page": 2},
        }
    ]
    # Match helper must refuse shared identity even though values "equal".
    match_errs = vle.check_all_fidelity_samples_match(doc, label="alias")
    assert any("identity" in e or "alias" in e for e in match_errs), match_errs
    # Shape path on new extracts also refuses.
    shape_errs = vle.validate_extract_document(
        doc, expected_source_id="fixture-source", pre_policy_ids=set()
    )
    assert any("identity" in e or "alias" in e for e in shape_errs), shape_errs


def test_fidelity_nested_serialized_alias_identity_rejected():
    """Null-hypothesis (P1): nested YAML aliases co-mutate and stay green.

    Alias identity is forbidden between the complete pin and resolved body
    object graphs, not merely between their roots.
    """
    doc = _minimal_extract()
    values = {"series": [{"T_K": 1700.0, "alpha": 0.02}]}
    doc["species"]["Fe"]["observations"][0]["values"] = values
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": {"series": values["series"]},
            "note": "nested aliased pin",
            "locator": {"page": 2},
        }
    ]
    serialized = yaml.safe_dump(doc, sort_keys=False)
    assert "&id" in serialized and "*id" in serialized
    loaded = yaml.safe_load(serialized)
    expected = loaded["fidelity_samples"][0]["value"]
    actual = loaded["species"]["Fe"]["observations"][0]["values"]
    assert expected is not actual
    assert expected["series"] is actual["series"]

    baseline_errs = vle.check_all_fidelity_samples_match(loaded, label="nested-alias")
    assert any("identity" in e or "alias" in e for e in baseline_errs), baseline_errs
    shape_errs = vle.validate_extract_document(
        loaded,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("identity" in e or "alias" in e for e in shape_errs), shape_errs
    actual["series"].append({"T_K": 1800.0, "alpha": 0.03})
    mutation_errs = vle.check_all_fidelity_samples_match(loaded, label="nested-alias")
    assert any("identity" in e or "alias" in e for e in mutation_errs), mutation_errs


def test_fidelity_nested_serialized_mutable_set_alias_rejected():
    """Null-hypothesis (P1): nested YAML set aliases co-mutate and stay green."""
    doc = _minimal_extract()
    labels = {"published", "reviewed"}
    values = {"labels": labels}
    doc["species"]["Fe"]["observations"][0]["values"] = values
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": {"labels": labels},
            "note": "nested set alias",
            "locator": {"page": 2},
        }
    ]
    loaded = yaml.safe_load(yaml.safe_dump(doc, sort_keys=False))
    expected = loaded["fidelity_samples"][0]["value"]
    actual = loaded["species"]["Fe"]["observations"][0]["values"]
    assert expected is not actual
    assert expected["labels"] is actual["labels"]
    baseline_errs = vle.check_all_fidelity_samples_match(loaded, label="set-alias")
    assert any("identity" in e or "alias" in e for e in baseline_errs), baseline_errs
    actual["labels"].add("mutated")
    mutation_errs = vle.check_all_fidelity_samples_match(loaded, label="set-alias")
    assert any("identity" in e or "alias" in e for e in mutation_errs), mutation_errs


def test_migrator_fidelity_deepcopy_isolates_pin():
    """Null-hypothesis (P1): reverting migrator deepcopy re-introduces aliases.

    ``_add_fidelity`` must copy the value so mutating the observation payload
    after sampling does not co-update the pin.
    """
    doc = mig._base_extract(
        "mig-fixture",
        citation="Mig et al.",
        doi=None,
        url=None,
        year=2026,
        method="test",
        date="2026-08-03",
        worker="pytest",
        provenance_path="docs-private/x.md",
    )
    values = {"alpha": 0.02, "material": "olivine"}
    mig._add_fidelity(
        doc,
        path="species.Fe.observations[x].values",
        value=values,
        note="pin",
    )
    assert doc["fidelity_samples"][0]["value"] is not values
    values["alpha"] = 99.0
    assert doc["fidelity_samples"][0]["value"]["alpha"] == 0.02


def test_fidelity_exact_match_rejects_bool_int_and_float_drift():
    """Null-hypothesis (P2): isclose / bool==int let type-drift pins stay green."""
    doc = _minimal_extract()
    # False vs 0
    doc["species"]["Fe"]["observations"][0]["values"] = {"flag": False}
    sample_bool = {
        "path": "species.Fe.observations[fe_alpha_1].values.flag",
        "value": 0,
        "note": "type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_bool), "False must not match 0"
    # True vs 1
    doc["species"]["Fe"]["observations"][0]["values"] = {"flag": True}
    sample_true = {
        "path": "species.Fe.observations[fe_alpha_1].values.flag",
        "value": 1,
        "note": "type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_true), "True must not match 1"
    # Small float mutation
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample_float = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.000000005,
        "note": "float drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(
        doc, sample_float
    ), "float isclose drift must go red"
    # Exact match still green
    sample_ok = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.0,
        "note": "exact",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_ok) == []


@pytest.mark.parametrize(("expected", "actual"), [(10, 10.0), (10.0, 10)])
def test_fidelity_exact_match_rejects_int_float_type_drift(expected, actual):
    """Null-hypothesis (P2): Python numeric equality hides int/float drift."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": actual}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": expected,
        "note": "numeric type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample), (
        f"{type(expected).__name__} must not match {type(actual).__name__}"
    )


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ({10: "x"}, {10.0: "x"}),
        ({10.0: "x"}, {10: "x"}),
        ({False: "x"}, {0: "x"}),
        ({0: "x"}, {False: "x"}),
    ],
    ids=["key-int-float", "key-float-int", "key-bool-int", "key-int-bool"],
)
def test_fidelity_exact_match_rejects_mapping_key_type_drift(expected, actual):
    """Null-hypothesis (P2): Python mapping-key equality hides type drift."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = actual
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values",
        "value": expected,
        "note": "mapping key type drift",
        "locator": {"page": 1},
    }
    loaded = yaml.safe_load(yaml.safe_dump({"doc": doc, "sample": sample}))
    assert vle.check_fidelity_sample_matches(loaded["doc"], loaded["sample"])


@pytest.mark.parametrize(("expected", "actual"), [([1], (1,)), ((1,), [1])])
def test_fidelity_exact_match_rejects_sequence_type_drift(expected, actual):
    """Null-hypothesis (P2): list and tuple structures compare as one type."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"series": actual}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.series",
        "value": expected,
        "note": "sequence type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample)


def _extract_paths_with_samples() -> list[Path]:
    paths: list[Path] = []
    for path in vle.discover_extracts():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        samples = doc.get("fidelity_samples") or []
        if samples:
            paths.append(path)
    return paths


@pytest.mark.parametrize(
    "extract_path",
    _extract_paths_with_samples(),
    ids=lambda p: p.stem,
)
def test_fidelity_sample_matches_extract(extract_path: Path):
    """Every fidelity sample still matches extract content (extraction-drift fuse).

    Null-hypothesis: a silent rewrite of a pinned field (bad regeneration,
    migrator regression) leaves samples stale while the file stays
    schema-valid — this test must go RED.
    """
    doc = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    errs = vle.check_all_fidelity_samples_match(doc, label=extract_path.stem)
    assert errs == [], "\n".join(errs)


def test_fidelity_sample_mutation_reds():
    """Prove the gate: mutate one extract value in memory → match test REDS.

    Pins must be independent literals (no YAML alias). Mutate the body without
    deepcopying samples — a co-mutated alias would keep the match green.
    """
    path = EXTRACTS / "costa-jacobson-2015.yaml"
    assert path.is_file(), "costa-jacobson-2015 extract required for mutation pin"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples = doc.get("fidelity_samples") or []
    assert samples, "costa extract must carry at least one fidelity sample"
    # Baseline green with independent (non-aliased) pins.
    assert vle.check_all_fidelity_samples_match(doc, label="costa") == []
    # Prove samples do not share identity with body values.
    for i, s in enumerate(samples):
        exp = s.get("value") if "value" in s else s.get("draft_value")
        act = vle.resolve_fidelity_sample(doc, s)
        if isinstance(exp, (dict, list)):
            assert exp is not act, (
                f"sample[{i}] still shares identity with body — re-dump without aliases"
            )

    # Mutate the Fe alpha pin in the extract body (samples left untouched).
    fe_obs = doc["species"]["Fe"]["observations"]
    target = None
    for obs in fe_obs:
        if obs.get("observation_id") == "costa_jacobson_2015_fe_olivine_kems":
            target = obs
            break
    assert target is not None, "expected costa Fe KEMS observation"
    original = copy.deepcopy(target["values"])
    # Flip a numeric field the sample pins (whole values dict on the pilot sample).
    if isinstance(target["values"], dict) and "alpha" in target["values"]:
        target["values"]["alpha"] = float(target["values"]["alpha"]) + 0.5
    else:
        target["values"] = {"__mutated__": True}

    errs = vle.check_all_fidelity_samples_match(doc, label="costa-mutated")
    assert errs, (
        "mutation of pinned extract value must turn fidelity match RED; "
        f"original={original!r}"
    )
    assert any("mismatch" in e for e in errs), errs


def test_fidelity_resolve_structured_series_index():
    """Structured sample with index resolves a series point."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0] = {
        "observation_id": "fe_psat",
        "type": "psat_series",
        "locator": {"table": "2"},
        "units": "Pa",
        "values": [
            {"T_K": 1700.0, "P_Pa": 1.0},
            {"T_K": 1800.0, "P_Pa": 10.0},
        ],
    }
    sample = {
        "species": "Fe",
        "observation_id": "fe_psat",
        "observable": "psat_series",
        "index": 1,
        "field": "P_Pa",
        "value": 10.0,
        "locator": {"table": "2"},
    }
    assert vle.resolve_fidelity_sample(doc, sample) == pytest.approx(10.0)
    sample_t = {
        "species": "Fe",
        "observation_id": "fe_psat",
        "T_K": 1700.0,
        "field": "P_Pa",
        "value": 1.0,
        "locator": {"table": "2"},
    }
    assert vle.resolve_fidelity_sample(doc, sample_t) == pytest.approx(1.0)
    assert vle.check_fidelity_sample_matches(doc, sample_t) == []


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
