from pathlib import Path

import pytest
import yaml

from simulator.core import CampaignPhase
from simulator.furnace_materials import (
    ALLOWED_FURNACE_GROUNDING_TIERS,
    CERTIFIED_WALL_ANCHORED_FURNACE_MATERIALS,
    load_furnace_materials,
    resolve_furnace_max_T_C,
    resolve_furnace_temperature_caps,
    validate_furnace_material_grounding,
)
from simulator.runner import PyrolysisRun

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Dense alumina intentionally has two furnace rows anchored to one wall material:
# no-load continuous service and formulation-dependent maximum service.
FURNACE_WALL_SERVICE_TEMP_ANCHORS = {
    "dense_alumina_continuous": ("dense_alumina", "continuous_C"),
    "dense_alumina_max": ("dense_alumina", "max_operating_C"),
    "zirconia_ysz": ("bulk_zirconia_ysz", "max_operating_C"),
    "plasma_sprayed_alumina": ("plasma_sprayed_alumina", "max_operating_C"),
    "fused_silica": ("fused_silica", "max_operating_C"),
}

LITERATURE_GROUNDED_FURNACE_MATERIALS = {
    "sintered_regolith",
}

# Provisional furnace options with no certified wall_materials.yaml service-temp anchor.
UNANCHORED_FURNACE_MATERIALS = {
    "graphite_inert",
}

# Disabled pure-oxide rows for liner-life / congruent-vaporization diagnostics
# (b-107). Not wall-anchored furnace products; not selectable.
PURE_OXIDE_DIAGNOSTIC_FURNACE_MATERIALS = {
    "pure_Al2O3",
    "pure_CaO",
    "pure_MgO",
    "pure_SiO2",
    "pure_TiO2",
}

# Enabled furnace materials with a finite service rating -- the set whose resolved
# applied ceiling must be admissible to CampaignManager (BUG-076 / BUG-108).
_ENABLED_FINITE_FURNACE_MATERIALS = sorted(
    material_id
    for material_id, row in load_furnace_materials().items()
    if isinstance(row, dict)
    and row.get("enabled") is True
    and row.get("max_service_T_C") is not None
)


def _load_yaml(path):
    with path.open() as handle:
        return yaml.safe_load(handle)


def test_catalog_loads_grounded_enabled_materials():
    catalog = load_furnace_materials()

    assert catalog["dense_alumina_continuous"]["max_service_T_C"] == 1700
    assert catalog["dense_alumina_continuous"]["enabled"] is True
    assert catalog["dense_alumina_max"]["max_service_T_C"] == 1843
    assert catalog["zirconia_ysz"]["max_service_T_C"] == 2200
    assert catalog["zirconia_ysz"]["enabled"] is True
    assert catalog["plasma_sprayed_alumina"]["max_service_T_C"] == 1650
    assert catalog["plasma_sprayed_alumina"]["enabled"] is True
    assert catalog["fused_silica"]["max_service_T_C"] == 1200
    assert catalog["fused_silica"]["enabled"] is True
    assert catalog["sintered_regolith"]["max_service_T_C"] == 1200
    assert catalog["sintered_regolith"]["enabled"] is True
    grounding = catalog["sintered_regolith"]["grounding"]
    assert grounding["tier"] == "proxy-sintering"
    assert grounding["source"] == "Warren et al. 2022 (arXiv:2205.06855)"
    assert "not a certified refractory hot-face" in grounding["caveat"]


def test_furnace_material_caps_track_wall_material_temperature_anchors():
    furnace_materials = _load_yaml(DATA_DIR / "furnace_materials.yaml")[
        "furnace_materials"
    ]
    wall_materials = _load_yaml(DATA_DIR / "wall_materials.yaml")["materials"]

    assert set(FURNACE_WALL_SERVICE_TEMP_ANCHORS) == (
        CERTIFIED_WALL_ANCHORED_FURNACE_MATERIALS
    )
    assert set(furnace_materials) == (
        set(FURNACE_WALL_SERVICE_TEMP_ANCHORS)
        | LITERATURE_GROUNDED_FURNACE_MATERIALS
        | UNANCHORED_FURNACE_MATERIALS
        | PURE_OXIDE_DIAGNOSTIC_FURNACE_MATERIALS
    )
    for pure_id in PURE_OXIDE_DIAGNOSTIC_FURNACE_MATERIALS:
        row = furnace_materials[pure_id]
        assert row["enabled"] is False
        assert isinstance(row.get("liner_life_diagnostic"), dict)
        diag = row["liner_life_diagnostic"]
        assert diag.get("refractory_material")
        # Static sidecar provenance (held-out; never loaded at runtime).
        assert (
            diag.get("validation_sidecar_path")
            == "data/literature/refractory_vaporization_validation.yaml"
        )
        assert diag.get("validation_sidecar_anchor")
        assert diag.get("density_citation_status") == "uncited"
        assert "refractory_vaporization_validation.yaml" in str(
            diag.get("density_provenance") or ""
        )

    # Every wired liner_life_diagnostic row (enabled walls + pure-oxide diagnostics)
    # carries static validation-sidecar path/anchor provenance (cx P2).
    for material_id, row in furnace_materials.items():
        diag = row.get("liner_life_diagnostic")
        if not isinstance(diag, dict):
            continue
        assert (
            diag.get("validation_sidecar_path")
            == "data/literature/refractory_vaporization_validation.yaml"
        ), f"{material_id} missing validation_sidecar_path"
        assert diag.get("validation_sidecar_anchor"), (
            f"{material_id} missing validation_sidecar_anchor"
        )
        assert diag.get("density_citation_status") == "uncited"

    for furnace_id, (
        wall_id,
        service_temp_key,
    ) in FURNACE_WALL_SERVICE_TEMP_ANCHORS.items():
        wall_service_temp = wall_materials[wall_id]["service_temp"]

        assert furnace_materials[furnace_id]["max_service_T_C"] == wall_service_temp[
            service_temp_key
        ], f"{furnace_id} must track {wall_id}.service_temp.{service_temp_key}"

    for furnace_id in UNANCHORED_FURNACE_MATERIALS:
        assert furnace_materials[furnace_id]["max_service_T_C"] is None

    for furnace_id in LITERATURE_GROUNDED_FURNACE_MATERIALS:
        row = furnace_materials[furnace_id]
        grounding = row.get("grounding")

        validate_furnace_material_grounding(furnace_id, row)
        assert isinstance(grounding, dict), f"{furnace_id} needs structured grounding"
        assert grounding.get("tier") in ALLOWED_FURNACE_GROUNDING_TIERS
        assert grounding.get("source"), f"{furnace_id} needs grounding.source"
        if grounding.get("tier") == "proxy-sintering":
            assert grounding.get("caveat"), f"{furnace_id} proxy tier needs caveat"

    sintered_grounding = furnace_materials["sintered_regolith"]["grounding"]
    assert furnace_materials["sintered_regolith"]["max_service_T_C"] == 1200
    assert sintered_grounding["source"] == "Warren et al. 2022 (arXiv:2205.06855)"


def test_catalog_disabled_materials_are_not_selectable():
    catalog = load_furnace_materials()

    assert catalog["graphite_inert"]["enabled"] is False


def test_resolver_clamps_requested_cap_to_material_max():
    assert resolve_furnace_max_T_C("dense_alumina_continuous", 1800) == 1700
    assert resolve_furnace_max_T_C("zirconia_ysz", 1800) == 1800


def test_resolver_distinguishes_service_rating_from_applied_ceiling():
    caps = resolve_furnace_temperature_caps("zirconia_ysz", 1800)

    assert caps["service_rating_T_C"] == pytest.approx(2200)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(1800)
    assert caps["requested_ceiling_T_C"] == pytest.approx(1800)


def test_resolver_allows_grounded_sub_1300_material_floor():
    from simulator.campaigns import CampaignManager
    from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C

    caps = resolve_furnace_temperature_caps("fused_silica", 1300)

    assert FURNACE_MAX_T_BOUNDS_C[0] == pytest.approx(1200)
    assert caps["service_rating_T_C"] == pytest.approx(1200)
    assert caps["requested_ceiling_T_C"] == pytest.approx(1300)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(1200)
    assert resolve_furnace_max_T_C("fused_silica", 1300) == pytest.approx(1200)
    CampaignManager({"furnace_max_T_C": caps["effective_applied_ceiling_T_C"], "campaigns": {}})


def test_resolver_defaults_to_material_max_when_no_request():
    assert resolve_furnace_max_T_C("dense_alumina_continuous") == 1700
    assert resolve_furnace_max_T_C("fused_silica") == 1200


def test_resolver_allows_sintered_regolith_bootstrap_floor():
    caps = resolve_furnace_temperature_caps("sintered_regolith", 1300)

    assert caps["service_rating_T_C"] == pytest.approx(1200)
    assert caps["requested_ceiling_T_C"] == pytest.approx(1300)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(1200)
    assert resolve_furnace_max_T_C("sintered_regolith") == pytest.approx(1200)


def test_uncapped_material_applies_its_full_service_rating():
    # BUG-076's invariant is that the resolver must not emit a ceiling CampaignManager
    # would reject -- the resolver and the consumer must agree on admissibility. That
    # still holds, and is asserted below.
    #
    # What this test USED to assert additionally was that the applied ceiling is
    # STRICTLY BELOW the service rating (zirconia 2200 -> applied 2000). That derate
    # was not a materials fact: FURNACE_MAX_T_BOUNDS_C[1] was the literal 2000.0, a
    # constant whose own consumer comment records that "a literature derivation is
    # unestablished here" and whose cited research file is absent from the repo. An
    # undocumented number was truncating a documented material rating by 200 C.
    #
    # The envelope max now DERIVES from the catalog (highest enabled max_service_T_C),
    # so admissibility is preserved by construction while nothing is derated. Asserting
    # applied == rating is the stronger claim: it fails if any hidden clamp returns.
    from simulator.furnace_materials import (
        FURNACE_MAX_T_BOUNDS_C,
        resolve_furnace_temperature_caps,
    )

    caps = resolve_furnace_temperature_caps("zirconia_ysz")  # no requested cap

    assert caps["service_rating_T_C"] == pytest.approx(2200)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(2200)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(caps["service_rating_T_C"])
    # ... and still runtime-admissible, which is BUG-076's actual requirement.
    assert caps["effective_applied_ceiling_T_C"] <= FURNACE_MAX_T_BOUNDS_C[1]
    assert resolve_furnace_max_T_C("zirconia_ysz") == pytest.approx(2200)


@pytest.mark.parametrize("material_id", _ENABLED_FINITE_FURNACE_MATERIALS)
def test_resolver_output_is_accepted_by_campaign_manager(material_id):
    # BUG-108 (follows BUG-076): every enabled material's resolved applied ceiling --
    # with no requested cap -- must be admissible to CampaignManager, i.e. within the
    # shared runtime envelope. Guards the resolver/consumer envelope agreement so a
    # new high-rated material cannot pass the resolver yet be rejected downstream.
    from simulator.campaigns import CampaignManager
    from simulator.furnace_materials import (
        FURNACE_MAX_T_BOUNDS_C,
        resolve_furnace_temperature_caps,
    )

    effective = resolve_furnace_temperature_caps(material_id)[
        "effective_applied_ceiling_T_C"
    ]
    lo, hi = FURNACE_MAX_T_BOUNDS_C
    assert lo <= effective <= hi
    # End-to-end: CampaignManager must accept the resolved ceiling without raising.
    CampaignManager({"furnace_max_T_C": effective, "campaigns": {}})


def test_resolver_fails_loud_for_sub_floor_requested_cap():
    # BUG-076 (codex run-the-exploit catch): a requested cap BELOW the runtime envelope
    # floor must fail loud, not silently leak a runtime-inadmissible ceiling that
    # CampaignManager later rejects (the cross-layer admissibility disagreement this bug
    # is about). The floor is fail-loud, NOT clamped up -- silently raising a sub-floor
    # request would run the furnace hotter than the operator asked.
    from simulator.furnace_materials import (
        FURNACE_MAX_T_BOUNDS_C,
        resolve_furnace_temperature_caps,
    )

    floor, ceiling = FURNACE_MAX_T_BOUNDS_C
    with pytest.raises(ValueError, match="below the runtime envelope floor"):
        resolve_furnace_temperature_caps("zirconia_ysz", floor - 1)
    with pytest.raises(ValueError, match="below the runtime envelope floor"):
        resolve_furnace_max_T_C("zirconia_ysz", 1000)
    # Boundary: exactly the floor and exactly the ceiling stay admissible (no raise).
    assert resolve_furnace_max_T_C("zirconia_ysz", floor) == pytest.approx(floor)
    assert resolve_furnace_max_T_C("zirconia_ysz", ceiling) == pytest.approx(ceiling)


def test_requested_cap_derates_but_can_never_exceed_the_material():
    # Two directions, and only one of them is a safety property.
    #
    # DOWNWARD (this is operator intent and must be honoured): a cap below the material
    # rating applies as asked. Previously a 2100 cap on zirconia returned 2000, because
    # the cap was clamped to a 2000 envelope -- the operator asked for 2100, got 2000,
    # and nothing said so. Silently REDUCING a request is less dangerous than silently
    # raising one, but it is still a rewrite of intent.
    #
    # UPWARD (the safety property, and the one worth guarding): a cap ABOVE the material
    # rating must clamp DOWN to the rating. Nothing may run a vessel hotter than the
    # material it is made of. Neither of the two tests this replaced covered that case.
    from simulator.furnace_materials import resolve_furnace_temperature_caps

    below = resolve_furnace_temperature_caps("zirconia_ysz", 2100)
    assert below["requested_ceiling_T_C"] == pytest.approx(2100)
    assert below["service_rating_T_C"] == pytest.approx(2200)
    assert below["effective_applied_ceiling_T_C"] == pytest.approx(2100)

    above = resolve_furnace_temperature_caps("zirconia_ysz", 2500)
    assert above["service_rating_T_C"] == pytest.approx(2200)
    assert above["effective_applied_ceiling_T_C"] == pytest.approx(2200)
    assert above["effective_applied_ceiling_T_C"] <= above["service_rating_T_C"]


def test_resolver_fails_loud_for_unknown_material():
    with pytest.raises(ValueError, match="unknown furnace material"):
        resolve_furnace_max_T_C("unknown_material")


def test_resolver_fails_loud_for_disabled_material():
    with pytest.raises(ValueError, match="not selectable yet"):
        resolve_furnace_max_T_C("graphite_inert", 1200)


def test_resolver_fails_loud_for_enabled_material_with_non_numeric_cap():
    # An enabled material whose max_service_T_C is null/non-numeric must fail
    # loud rather than silently resolving to a bad ceiling.
    bad_catalog = {
        "bogus": {
            "id": "bogus",
            "enabled": True,
            "max_service_T_C": None,
            "grounding": {
                "tier": "proxy-sintering",
                "source": "bench note",
                "caveat": "not certified",
            },
        },
    }
    with pytest.raises(ValueError, match="must be numeric"):
        resolve_furnace_max_T_C("bogus", 1800, catalog=bad_catalog)


@pytest.mark.parametrize(
    ("grounding", "message"),
    [
        (None, "grounding must be a mapping"),
        (
            {
                "tier": "unsupported",
                "source": "bench note",
                "caveat": "not certified",
            },
            "grounding.tier must be one of",
        ),
        (
            {
                "tier": "proxy-sintering",
                "source": "",
                "caveat": "not certified",
            },
            "grounding.source must be a non-empty string",
        ),
        (
            {
                "tier": "proxy-sintering",
                "source": "bench note",
            },
            "proxy tier needs grounding.caveat",
        ),
    ],
)
def test_load_furnace_materials_rejects_enabled_material_with_bad_grounding(
    tmp_path,
    grounding,
    message,
):
    row = {
        "id": "bad_proxy",
        "display_name": "Bad proxy material",
        "max_service_T_C": 1200,
        "enabled": True,
    }
    if grounding is not None:
        row["grounding"] = grounding
    catalog_path = tmp_path / "furnace_materials.yaml"
    catalog_path.write_text(
        yaml.safe_dump({"furnace_materials": {"bad_proxy": row}}),
        encoding="utf-8",
    )

    load_furnace_materials.cache_clear()
    with pytest.raises(ValueError, match=message):
        load_furnace_materials(catalog_path)
    load_furnace_materials.cache_clear()


def test_setpoints_patch_cap_flows_through_existing_campaign_manager():
    session = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=0,
        setpoints_patch={"furnace_max_T_C": 1700},
    )._start_session()

    target_T, _ = session.simulator.campaign_mgr.get_temp_target(
        CampaignPhase.C2A,
        0,
        session.simulator.melt,
    )

    assert session.simulator.campaign_mgr.furnace_max_T_C == pytest.approx(1700)
    assert target_T <= 1700
