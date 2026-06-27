from pathlib import Path

import pytest
import yaml

from simulator.core import CampaignPhase
from simulator.furnace_materials import (
    load_furnace_materials,
    resolve_furnace_max_T_C,
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

# Provisional furnace options with no certified wall_materials.yaml service-temp anchor.
UNANCHORED_FURNACE_MATERIALS = {
    "sintered_regolith",
    "graphite_inert",
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


def test_furnace_material_caps_track_wall_material_temperature_anchors():
    furnace_materials = _load_yaml(DATA_DIR / "furnace_materials.yaml")[
        "furnace_materials"
    ]
    wall_materials = _load_yaml(DATA_DIR / "wall_materials.yaml")["materials"]

    assert set(furnace_materials) == (
        set(FURNACE_WALL_SERVICE_TEMP_ANCHORS) | UNANCHORED_FURNACE_MATERIALS
    )

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


def test_catalog_disabled_materials_are_not_selectable():
    catalog = load_furnace_materials()

    assert catalog["fused_silica"]["enabled"] is False
    assert catalog["sintered_regolith"]["enabled"] is False
    assert catalog["graphite_inert"]["enabled"] is False


def test_resolver_clamps_requested_cap_to_material_max():
    assert resolve_furnace_max_T_C("dense_alumina_continuous", 1800) == 1700
    assert resolve_furnace_max_T_C("zirconia_ysz", 1800) == 1800


def test_resolver_distinguishes_service_rating_from_applied_ceiling():
    from simulator.furnace_materials import resolve_furnace_temperature_caps

    caps = resolve_furnace_temperature_caps("zirconia_ysz", 1800)

    assert caps["service_rating_T_C"] == pytest.approx(2200)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(1800)
    assert caps["requested_ceiling_T_C"] == pytest.approx(1800)


def test_resolver_defaults_to_material_max_when_no_request():
    assert resolve_furnace_max_T_C("dense_alumina_continuous") == 1700


def test_resolver_clamps_applied_ceiling_to_runtime_envelope_when_uncapped():
    # BUG-076: an enabled material rated above the runtime envelope (zirconia_ysz at
    # 2200 C) must not emit a runtime-inadmissible applied ceiling when no cap is
    # requested. The raw service rating is preserved; the applied ceiling is clamped
    # to FURNACE_MAX_T_BOUNDS_C[1] so CampaignManager accepts it.
    from simulator.furnace_materials import (
        FURNACE_MAX_T_BOUNDS_C,
        resolve_furnace_temperature_caps,
    )

    caps = resolve_furnace_temperature_caps("zirconia_ysz")  # no requested cap

    assert caps["service_rating_T_C"] == pytest.approx(2200)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(FURNACE_MAX_T_BOUNDS_C[1])
    assert caps["effective_applied_ceiling_T_C"] < caps["service_rating_T_C"]
    assert resolve_furnace_max_T_C("zirconia_ysz") == pytest.approx(FURNACE_MAX_T_BOUNDS_C[1])


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


def test_resolver_clamps_over_envelope_requested_cap():
    # A requested cap above the envelope max but below the material rating clamps to the
    # envelope max (zirconia_ysz rated 2200: cap 2100 -> applied 2000), so the resolver
    # stays runtime-admissible on the capped path too, not only the uncapped path.
    from simulator.furnace_materials import (
        FURNACE_MAX_T_BOUNDS_C,
        resolve_furnace_temperature_caps,
    )

    caps = resolve_furnace_temperature_caps("zirconia_ysz", 2100)

    assert caps["requested_ceiling_T_C"] == pytest.approx(2100)
    assert caps["effective_applied_ceiling_T_C"] == pytest.approx(FURNACE_MAX_T_BOUNDS_C[1])
    assert caps["service_rating_T_C"] == pytest.approx(2200)


def test_resolver_fails_loud_for_unknown_material():
    with pytest.raises(ValueError, match="unknown furnace material"):
        resolve_furnace_max_T_C("unknown_material")


def test_resolver_fails_loud_for_disabled_material():
    with pytest.raises(ValueError, match="not selectable yet"):
        resolve_furnace_max_T_C("fused_silica", 1200)


def test_resolver_fails_loud_for_enabled_material_with_non_numeric_cap():
    # An enabled material whose max_service_T_C is null/non-numeric must fail
    # loud rather than silently resolving to a bad ceiling.
    bad_catalog = {
        "bogus": {"id": "bogus", "enabled": True, "max_service_T_C": None},
    }
    with pytest.raises(ValueError, match="must be numeric"):
        resolve_furnace_max_T_C("bogus", 1800, catalog=bad_catalog)


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
