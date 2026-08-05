"""VR-7 — dormant transcription of volatile/P/S/halide/molecular evidence.

Null-hypothesis style: every source is dispositioned; DRAFT gates survive;
legacy metals/oxide_vapors/foulant runtime-driving rows stay identical in
identity and Antoine coefficients; dormant families never compile evaluators;
NO is a string key; charge aliases canonicalize; P2O5_gas cannot stand in for
PO/P4On; dimer relations are explicit; monomer partials are labeled.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from simulator.vapour_rail.catalog import (
    CHARGE_ALIAS_CANONICAL,
    OUT_OF_RANGE_STATUS,
    canonicalize_charge_alias,
    clear_vapor_pressure_view_caches,
    compile_vapour_rail_catalog,
    validate_species_catalog,
    vapor_pressure_legacy_view,
)

DATA = Path(__file__).resolve().parents[1] / "data"
ROOT = DATA.parent
FIXTURES = ROOT / "tests" / "fixtures" / "vr7"
SOURCE_KEYS_MANIFEST = FIXTURES / "source_keys_manifest.yaml"

# Optional in-tree research paths only (no absolute author-machine fallbacks).
T431_SOURCES = [
    ROOT
    / "docs-private"
    / "research"
    / "2026-07-26-vp-acquire"
    / "vp-t14-rows-DRAFT.yaml",
    ROOT
    / "docs-private"
    / "research"
    / "2026-07-27-vp-acquire-2"
    / "draft-rows-DRAFT.yaml",
    ROOT
    / "docs-private"
    / "research"
    / "2026-07-27-vp-acquire-3"
    / "draft-rows-DRAFT.yaml",
]
T425 = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-07-25-trace-vp-refs"
    / "vp-volatile-rows-DRAFT.yaml"
)


def _vp() -> dict:
    return yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())


def _disposition() -> dict:
    return yaml.safe_load(
        (DATA / "literature" / "vr7_transcription_disposition.yaml").read_text()
    )


def _catalog():
    return compile_vapour_rail_catalog(_vp())


def test_production_catalog_compiles_with_dormant_projection() -> None:
    payload = _vp()
    assert payload["schema_version"] == 2
    catalog = compile_vapour_rail_catalog(payload)
    legacy = catalog.legacy_view()
    assert set(legacy["metals"]) == {
        "Na",
        "K",
        "Mg",
        "Fe",
        "Ca",
        "Al",
        "Si",
        "Ti",
        "Cr",
        "Mn",
    }
    assert set(legacy["oxide_vapors"]) == {
        "SiO", "CrO2", "TiO", "TiO2_gas", "CaO_gas", "AlO", "Al2O", "CrO3"
    }
    assert set(legacy["foulant_vapor"]) == {"NaCl", "KCl", "NaF"}
    assert "dormant_acquisition" in legacy
    assert len(legacy["dormant_acquisition"]) >= 30


def test_legacy_runtime_antoine_coefficients_unchanged() -> None:
    """Null hypothesis: VR-7 must not retune live NaCl/KCl/metal Antoine."""
    legacy = _catalog().legacy_view()
    # Pins from pre-VR-7 production rows (schema migration values).
    assert legacy["metals"]["K"]["antoine"]["A"] == pytest.approx(10.641294)
    assert legacy["foulant_vapor"]["NaCl"]["antoine"]["A"] == pytest.approx(
        11.1773865
    )
    assert legacy["foulant_vapor"]["KCl"]["antoine"]["A"] == pytest.approx(9.78236)
    # NaF remains unavailable (no executable Antoine).
    assert "antoine" not in legacy["foulant_vapor"]["NaF"]


def test_dormant_families_have_no_compiled_evaluator() -> None:
    catalog = _catalog()
    dormant = [
        sp
        for sp in catalog.species.values()
        if sp.code_metadata.compatibility_projection == "dormant_acquisition"
    ]
    assert dormant, "expected VR-7 dormant families"
    for sp in dormant:
        assert sp.evaluator is None, sp.species_id
        assert sp.validation_status.value == "pending_validation"


def test_no_is_quoted_string_species_key() -> None:
    payload = _vp()
    # Round-trip: YAML 1.1 must not coerce NO → False.
    raw = (DATA / "vapor_pressures.yaml").read_text()
    assert "'NO'" in raw or '"NO"' in raw
    fam = payload["families"]["volatile_nitrogen_NO_family"]
    species = fam["physical_properties"]["species"]
    assert "NO" in species
    assert False not in species
    assert species["NO"]["formula"] == "NO"
    catalog = compile_vapour_rail_catalog(payload)
    assert "NO" in catalog.species
    assert catalog.species["NO"].formula == "NO"


def test_charge_aliases_canonicalize() -> None:
    assert canonicalize_charge_alias("ClO4") == "ClO4"
    assert canonicalize_charge_alias("ClO4-") == "ClO4"
    assert canonicalize_charge_alias("ClO4_minus") == "ClO4"
    assert canonicalize_charge_alias("ClO4_anion") == "ClO4"
    assert canonicalize_charge_alias("perchlorate") == "ClO4"
    # Non-alias passes through.
    assert canonicalize_charge_alias("NaCl") == "NaCl"
    # P2-1: map only folds to catalog IDs that exist; unknown oxyanion
    # spellings pass through rather than inventing SO4/NO3/CO3 keys.
    sc = yaml.safe_load((DATA / "species_catalog.yaml").read_text())
    by_id = {row["id"]: row for row in sc["species"]}
    for alias, target in CHARGE_ALIAS_CANONICAL.items():
        assert target in by_id, f"alias {alias!r} targets missing catalog id {target!r}"
        assert canonicalize_charge_alias(alias) == target
    for unmapped in ("NO3-", "NO3_minus", "SO4-", "SO4-2", "CO3-", "CO3-2", "ClO3-"):
        assert canonicalize_charge_alias(unmapped) == unmapped
    clo4 = by_id["ClO4"]
    aliases = set(clo4.get("charge_aliases") or clo4.get("aliases") or [])
    assert {"ClO4-", "ClO4_minus", "ClO4_anion"} <= aliases
    validate_species_catalog(sc)


def test_p2o5_gas_cannot_substitute_for_true_p_carriers() -> None:
    payload = _vp()
    p2 = payload["families"]["phosphorus_P2O5_gas_family"]["physical_properties"][
        "species"
    ]["P2O5_gas"]
    assert p2.get("carrier_eligible") is False
    assert p2.get("substitutes_for_carriers") is False
    children = set(p2["child_expansion"]["children"])
    assert children >= {"PO", "PO2", "P4O6", "P4O10", "P2", "P4"}
    for carrier in ("PO", "PO2", "P4O6", "P4O10", "P2", "P4"):
        fam_id = f"phosphorus_{carrier}_family"
        assert fam_id in payload["families"]
        sp = payload["families"][fam_id]["physical_properties"]["species"][carrier]
        assert "P2O5_gas" in (sp.get("not_substitutable_by") or [])
        assert sp["validation"]["status"] == "pending_validation"
        assert sp["pressure_models"][0]["availability"] == (
            "unavailable_pending_acquisition"
        )


def test_chloride_dimer_relations_explicit() -> None:
    payload = _vp()
    for dimer, monomer, fam in (
        ("Na2Cl2", "NaCl", "halide_association_Na2Cl2_family"),
        ("K2Cl2", "KCl", "halide_association_K2Cl2_family"),
        ("Mg2Cl4", "MgCl2", "halide_association_Mg2Cl4_family"),
    ):
        sp = payload["families"][fam]["physical_properties"]["species"][dimer]
        rel = sp["association_relation"]
        assert rel["monomer_species_id"] == monomer
        assert rel["dimer_species_id"] == dimer
        assert rel["K_definition"]
        assert sp["pressure_models"][0]["pressure_kind"] == (
            "association_partial_pressure"
        )
        assert sp["pressure_models"][0]["species_basis"] == "dimer"
        honesty = sp.get("observable_honesty") or {}
        assert honesty.get("rule") == (
            "monomer_partial_must_not_masquerade_as_total_mixture_pressure"
        )
    # Live foulant monomers point at dormant dimers without coefficient change.
    nacl = payload["families"]["foulant_vapor_nacl_family"]["physical_properties"][
        "species"
    ]["NaCl"]
    assert nacl["association_relation"]["dimer_species_id"] == "Na2Cl2"
    assert nacl["pressure_models"][0]["coefficients"]["A"] == pytest.approx(
        11.1773865
    )


def test_monomer_partials_labeled_not_as_totals() -> None:
    payload = _vp()
    mg = payload["families"]["halide_salt_MgCl2_family"]["physical_properties"][
        "species"
    ]["MgCl2"]
    assert mg["pressure_models"][0]["species_basis"] == "monomer"
    assert mg.get("observable_honesty")
    s_total = payload["families"]["volatile_sulfur_S_total_family"][
        "physical_properties"
    ]["species"]["S_total"]
    assert s_total["pressure_models"][0]["pressure_kind"] == "total_mixture_pressure"
    assert s_total["pressure_models"][0]["species_basis"] == "total_mixture"
    s2 = payload["families"]["volatile_sulfur_S2_family"]["physical_properties"][
        "species"
    ]["S2"]
    assert s2["pressure_models"][0]["pressure_kind"] == (
        "equilibrium_partial_pressure"
    )
    no2 = payload["families"]["volatile_nitrogen_NO2_family"]["physical_properties"][
        "species"
    ]["NO2"]
    assert no2["pressure_models"][0]["pressure_kind"] == "total_mixture_pressure"


def test_feedstock_presence_covered_rows_have_literature_and_status() -> None:
    payload = _vp()
    catalog = compile_vapour_rail_catalog(payload)
    u0 = yaml.safe_load((DATA / "vapour_rail_u0_manifest.yaml").read_text())
    covered = []
    for row in u0["species"]:
        if not row.get("feedstock_presence"):
            continue
        sid = row["id"]
        if sid not in catalog.species:
            continue
        sp = catalog.species[sid]
        if sp.code_metadata.compatibility_projection != "dormant_acquisition":
            # Live foulant NaCl etc. already carry pure_component_antoine.
            if sid in ("NaCl", "KCl", "NaF"):
                fam = payload["families"][sp.family_id]
                phys = fam["physical_properties"]["species"][sid]
                assert phys["validation"]["status"] in {
                    "pending_validation",
                    "validated",
                }
            continue
        fam = payload["families"][sp.family_id]
        phys = fam["physical_properties"]["species"][sid]
        status = phys["validation"]["status"]
        assert status in {"pending_validation", "validated"}
        if status == "validated":
            assert phys["validation"].get("anchor_refs")
        has_lit = bool(
            phys.get("literature_values")
            or phys.get("pure_component_antoine")
            or phys.get("literature_correlation")
            or phys.get("literature_candidate_correlations")
            or phys.get("child_expansion")
            or phys.get("correlations")
            or phys.get("janaf")
            or phys.get("p_carrier_draft")
        )
        assert has_lit, f"{sid} missing literature values / child expansion"
        covered.append(sid)
    # Core volatile feedstock set must be present.
    for must in ("CH4", "CO", "CO2", "H2O", "NH3", "HCN", "P2O5_gas", "ClO4"):
        assert must in covered or must in catalog.species


def test_draft_gates_survive_on_transcribed_rows() -> None:
    payload = _vp()
    for fam_id, fam in payload["families"].items():
        code = fam["code_metadata"]
        if code.get("compatibility_projection") != "dormant_acquisition":
            continue
        for sid, sp in fam["physical_properties"]["species"].items():
            assert sp["flux_dormant"] is True
            assert sp["validation"]["status"] == "pending_validation"
            # P2-2: empty default must NOT vacuous-pass when note is absent.
            assert "DRAFT" in (sp["validation"].get("note") or "")
            assert sp["pressure_models"][0]["availability"] == (
                "unavailable_pending_acquisition"
            )
            assert code["hot_train_applicability"] == "not_applicable"


def test_disposition_bidirectional_catalog_closure() -> None:
    """P2-3: disposition ↔ catalog joins, portable (no author-machine paths)."""
    payload = _vp()
    disposition = _disposition()
    rows = disposition["rows"]
    assert disposition.get("row_count") == len(rows)
    assert len(rows) >= 64

    sources = {row["source"] for row in rows}
    # Checked-in source-key manifest (exact match, both directions).
    manifest = yaml.safe_load(SOURCE_KEYS_MANIFEST.read_text())
    expected_sources = set(manifest["source_keys"])
    assert sources == expected_sources, (
        f"disposition sources drift from manifest: "
        f"only_in_disposition={sorted(sources - expected_sources)} "
        f"only_in_manifest={sorted(expected_sources - sources)}"
    )

    # disposition → catalog: every row resolves to an existing family+species.
    for row in rows:
        fam_id = row["family_id"]
        sid = row["species_id"]
        assert fam_id in payload["families"], f"missing family {fam_id} for {row['source']}"
        species_map = payload["families"][fam_id]["physical_properties"]["species"]
        assert sid in species_map, f"missing species {sid} in {fam_id} for {row['source']}"

    # catalog → disposition: every dormant family has at least one row.
    dormant_families = {
        fam_id
        for fam_id, fam in payload["families"].items()
        if fam["code_metadata"].get("compatibility_projection")
        == "dormant_acquisition"
    }
    disposition_families = {row["family_id"] for row in rows}
    missing = sorted(dormant_families - disposition_families)
    assert not missing, f"dormant families without disposition rows: {missing}"

    # Optional live research sources (exact tag match when present in-tree).
    if T425.is_file():
        t425 = yaml.safe_load(T425.read_text())["volatile_species_DRAFT_FOR_REVIEW"][
            "rows"
        ]
        for key in t425:
            assert any(
                s == f"t-425:{key}"
                or s == f"t-425:{key}_ice"
                or s == f"t-425:{key}_frost"
                for s in sources
            ), f"t-425 {key} not dispositioned"
    _T431_TRANCHE = {
        "2026-07-26-vp-acquire": "t14",
        "2026-07-27-vp-acquire-2": "t5",
        "2026-07-27-vp-acquire-3": "t6",
    }
    for path in T431_SOURCES:
        if not path.is_file():
            continue
        tranche = next(
            (t for marker, t in _T431_TRANCHE.items() if marker in str(path)),
            None,
        )
        assert tranche is not None, f"unknown t-431 path layout: {path}"
        doc = yaml.safe_load(path.read_text())
        root = next(iter(doc.values()))
        for sid in root["rows"]:
            tag = f"t-431:{tranche}:{sid}"
            assert tag in sources, f"t-431 {sid} not dispositioned as {tag}"

    # P carriers + dimers always required (transcribed into families).
    for must in (
        "PO",
        "PO2",
        "P4O6",
        "P4O10",
        "P2",
        "P4",
        "P2O5_gas",
        "Na2Cl2",
        "K2Cl2",
        "Mg2Cl4",
        "S_total",
        "S2",
        "NO",
        "ClO4",
    ):
        assert must in compile_vapour_rail_catalog(payload).species


def test_legacy_view_memoized_per_payload_identity() -> None:
    """P1-1: schema-v2 legacy view must not recompile+deepcopy per call."""
    from simulator.vapour_rail.catalog import _content_digest

    clear_vapor_pressure_view_caches()
    payload = _vp()
    # Warm + identity stability (content digest walks current payload).
    first = vapor_pressure_legacy_view(payload)
    second = vapor_pressure_legacy_view(payload)
    assert first is second
    assert "metals" in first and "Na" in first["metals"]
    # Owner-boundary pattern: digest once, pass content_key — warm hits are
    # pure dict returns (no re-serialize of the production payload).
    clear_vapor_pressure_view_caches()
    content_key = _content_digest(payload)
    t0 = time.perf_counter()
    vapor_pressure_legacy_view(payload, content_key=content_key)
    cold_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    for _ in range(50):
        vapor_pressure_legacy_view(payload, content_key=content_key)
    warm_s = (time.perf_counter() - t1) / 50
    # Warm hits are pure dict returns; allow generous CI slack but require
    # orders-of-magnitude cheaper than a full catalog compile.
    assert warm_s < 0.005, f"warm legacy view too slow: {warm_s:.4f}s (cold {cold_s:.4f}s)"
    assert warm_s * 20 < cold_s + 0.05


def test_runtime_evaluator_presence_excludes_unavailable_melt_psat() -> None:
    catalog = _catalog()
    expect_eval = {
        "Na",
        "K",
        "Mg",
        "Fe",
        "Ca",
        "Al",
        "Ti",
        "Cr",
        "Mn",
        "SiO",
        "CrO2",
        "NaCl",
        "KCl",
    }
    for sid in expect_eval:
        assert catalog.species[sid].evaluator is not None
    # Si's retained pure-component sidecar is not a valid melt-source answer;
    # it remains unavailable until an activity-bearing reaction model lands.
    assert catalog.species["Si"].evaluator is None
    assert catalog.species["NaF"].evaluator is None
