"""VR-9 KEMS/Langmuir regime separation + alpha provenance guards.

Ground truth is experimental-regime vocabulary and owner R1 §7 (KEMS and
Langmuir remain distinct; alpha never fit from VapoRock). Diagnostic only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.vapour_rail.kinetics_anchors import (
    KineticsAnchorError,
    KineticsExperimentalRegime,
    alpha_provenance_from_mapping,
    assert_alpha_source_not_vaporock,
    load_kems_anchors,
    load_langmuir_anchors,
    regimes_remain_distinct,
)


ROOT = Path(__file__).resolve().parents[1]
KEMS_PATH = ROOT / "data" / "literature" / "vapour_rail_kems_anchors.yaml"
LANGMUIR_PATH = ROOT / "data" / "literature" / "vapour_rail_langmuir_anchors.yaml"
VAPOR_PRESSURES = ROOT / "data" / "vapor_pressures.yaml"


def test_kems_and_langmuir_sidecars_load_as_distinct_regimes():
    kems = load_kems_anchors(KEMS_PATH)
    langmuir = load_langmuir_anchors(LANGMUIR_PATH)
    assert kems
    assert langmuir
    assert all(r.regime is KineticsExperimentalRegime.KEMS_EFFUSION for r in kems)
    assert all(
        r.regime is KineticsExperimentalRegime.LANGMUIR_FREE_EVAPORATION
        for r in langmuir
    )
    assert regimes_remain_distinct(kems, langmuir) is True
    # No record may certify.
    assert all(r.certifies is False for r in (*kems, *langmuir))


def test_sidecar_yaml_declares_regime_and_never_certifies():
    for path, expected in (
        (KEMS_PATH, "kems_effusion"),
        (LANGMUIR_PATH, "langmuir_free_evaporation"),
    ):
        payload = yaml.safe_load(path.read_text())
        assert payload["experimental_regime"] == expected
        assert payload["certifies"] is False
        assert payload["authority"] == "diagnostic_only"
        for record in payload["records"]:
            assert record["regime"] == expected
            assert record.get("certifies", False) is False


def test_mixed_regime_sidecar_is_rejected(tmp_path: Path):
    bad = tmp_path / "mixed.yaml"
    bad.write_text(
        """
schema_version: 1
experimental_regime: kems_effusion
certifies: false
records:
  - record_id: x
    species: Fe
    regime: langmuir_free_evaporation
    temperature_range_K: [1700, 1800]
    citation: "example"
    certifies: false
"""
    )
    with pytest.raises(KineticsAnchorError, match="regime"):
        load_kems_anchors(bad)


def test_alpha_source_rejects_vaporock_fit():
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock("vaporock_fit")
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock("fit_from_vaporock")
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock("pseudo_psat_backsolved_from_vaporock")
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock("analytical:vaporock_calibrated")
    # Bare identity
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock("vaporock")


@pytest.mark.parametrize(
    "bypass",
    [
        # Whitewash allowlist phrase + smuggled fit marker (denylist must win).
        "not a vaporock fit; actually backsolved_from_vaporock",
        "no vaporock here; fit_from_vaporock dense grid",
        # Token-gap prose that previously passed (compact againstvaporock /
        # vaporockderived / residual vaporock without rejection coverage).
        "calibrated against vaporock output",
        "vaporock-derived evaporation alpha",
    ],
)
def test_alpha_source_rejects_whitewash_and_token_gap_bypasses(bypass: str):
    """P1-2 regressions: allowlist-before-denylist and missing compact markers."""

    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        assert_alpha_source_not_vaporock(bypass)


def test_alpha_source_allows_cited_kems_or_explicit_rejection():
    assert_alpha_source_not_vaporock(
        "Costa & Jacobson 2015 KEMS Fo93Fa7 olivine Fe+ alpha=0.011-0.020"
    )
    assert_alpha_source_not_vaporock(
        "The coefficient is not a VapoRock fit; KEMS sealed-chamber pin"
    )
    # Pure rejection with zero residual fit markers still allowed.
    assert_alpha_source_not_vaporock("never fit from vaporock; KEMS pin only")


def test_alpha_provenance_from_mapping_round_trip():
    prov = alpha_provenance_from_mapping(
        "Fe",
        {
            "value": 0.02,
            "source": "REF-016 Costa & Jacobson 2015 KEMS Fo93Fa7 olivine",
            "tier": 2,
            "envelope": [0.011, 0.020],
            "temperature_range_K": [1700, 1800],
        },
        regime=KineticsExperimentalRegime.KEMS_EFFUSION,
    )
    assert prov.value == pytest.approx(0.02)
    assert prov.tier == 2
    assert prov.regime is KineticsExperimentalRegime.KEMS_EFFUSION
    assert prov.may_certify() is False
    assert prov.authority is False


def test_alpha_provenance_rejects_vaporock_mapping():
    with pytest.raises(KineticsAnchorError, match="VapoRock"):
        alpha_provenance_from_mapping(
            "Na",
            {"value": 1.0, "source": "fit_from_vaporock dense grid"},
        )


def test_calibrated_runtime_alphas_are_not_vaporock_fits():
    """Spot-check live schema-v2 vaporisation alphas for forbidden fit tokens."""

    payload = yaml.safe_load(VAPOR_PRESSURES.read_text())
    families = payload.get("families") or {}
    checked = 0
    for family_id, family in families.items():
        if not isinstance(family, dict):
            continue
        kinetics = family.get("vaporisation_coefficients") or {}
        alpha = kinetics.get("evaporation_alpha")
        if not isinstance(alpha, dict):
            continue
        if alpha.get("status") in {"no_data", "absent", "unmeasured"}:
            # Policy rows still must not name a VapoRock fit as the alpha source.
            source = alpha.get("source") or alpha.get("policy") or "policy_no_data"
            if isinstance(source, str):
                assert_alpha_source_not_vaporock(source)
            continue
        source = alpha.get("source")
        if source is None:
            continue
        assert_alpha_source_not_vaporock(str(source))
        prov = alpha_provenance_from_mapping(str(family_id), alpha)
        assert prov.may_certify() is False
        checked += 1
    assert checked >= 5


def test_anchor_records_cite_external_literature_not_self():
    kems = load_kems_anchors()
    langmuir = load_langmuir_anchors()
    for record in (*kems, *langmuir):
        assert "doi.org" in (record.doi_or_url or "") or "ntrs.nasa" in (
            record.doi_or_url or ""
        ) or "doi" in record.citation.lower() or "REF-" in record.citation
        assert record.species
        assert record.temperature_range_K[0] <= record.temperature_range_K[1]


def test_fedkin_k_alpha_is_langmuir_not_kems():
    """P0-1: Fedkin/Yu α_K = 0.13 is vacuum free-evaporation, not KEMS."""

    kems = load_kems_anchors()
    langmuir = load_langmuir_anchors()
    assert all(r.species != "K" for r in kems)
    assert all("fedkin" not in r.record_id.lower() for r in kems)
    fedkin = [r for r in langmuir if "fedkin" in r.record_id.lower()]
    assert len(fedkin) == 1
    record = fedkin[0]
    assert record.species == "K"
    assert record.regime is KineticsExperimentalRegime.LANGMUIR_FREE_EVAPORATION
    assert record.alpha_value == pytest.approx(0.13)
    assert record.material == "Yu_et_al_2003_C1_silicate_melt"
    assert "KEMS" not in (record.material or "")
    assert "potassium_silicate_vacuum" in (record.extraction_note or "")
    assert "langmuir_knudsen_flux_validation" in (record.extraction_note or "")
