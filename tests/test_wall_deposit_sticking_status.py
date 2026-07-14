from __future__ import annotations

import copy
import json
import pickle
from types import SimpleNamespace

import pytest

import simulator.condensation as condensation_module
from simulator.condensation import CondensationModel
from simulator.core import CondensationTrain, EvaporationFlux, MeltState
from simulator.diagnostics import wall_deposit_sticking_authority_status
from simulator.optimize.objective import _coating_product_summary
from simulator.optimize.physics import PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.study import (
    _coating_leaderboard_fields,
    _coating_leaderboard_row,
    _product_summary_mapping,
)
from simulator.runner import _wall_fouling_report
from simulator.state import HourSnapshot, PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX
from simulator.trace import PhysicsTrace
from web.routes import _coating_readout


pytestmark = pytest.mark.usefixtures("production_configured_condensation_route")


def _alpha_notice(species: str, *, cited: bool) -> dict[str, object]:
    return {
        "alpha_s_provenance_by_species": {
            species: {
                "hot_wall": {
                    "segment": "hot_wall",
                    "species": species,
                    "alpha_s": 0.02 if cited else 1.0,
                    "citation_status": "CITED" if cited else "UNCERTIFIED",
                    "status": "sourced" if cited else "proxy",
                    "output_status": (
                        "sourced_with_surface_proxy"
                        if cited
                        else "status_bearing"
                    ),
                }
            }
        }
    }


def _cited_missing_alpha_notice(species: str) -> dict[str, object]:
    return {
        "alpha_s_provenance_by_species": {
            species: {
                "hot_wall": {
                    "segment": "hot_wall",
                    "species": species,
                    "citation_status": "CITED",
                    "status": "sourced",
                    "output_status": "sourced_with_surface_proxy",
                }
            }
        }
    }


def _missing_record_notice(species: str) -> dict[str, object]:
    return {"alpha_s_provenance_by_species": {species: {}}}


def _sourced_missing_output_notice(species: str) -> dict[str, object]:
    return {
        "alpha_s_provenance_by_species": {
            species: {
                "hot_wall": {
                    "segment": "hot_wall",
                    "species": species,
                    "alpha_s": 0.02,
                    "citation_status": "CITED",
                    "status": "sourced",
                }
            }
        }
    }


def _fake_sim(
    wall: dict[tuple[str, str], float],
    notice: dict[str, object],
    *,
    delta_wall: dict[tuple[str, str], float] | None = None,
) -> SimpleNamespace:
    class _Ledger:
        def __init__(self, wall_deposit: dict[tuple[str, str], float]) -> None:
            self._accounts: dict[str, dict[str, float]] = {}
            for (segment, species), kg in wall_deposit.items():
                account = f"{PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX}{segment}"
                self._accounts.setdefault(account, {})[species] = kg

        def kg_by_account(self, account: str | None = None):
            if account is None:
                return {key: dict(value) for key, value in self._accounts.items()}
            return dict(self._accounts.get(str(account), {}))

        def total_kg_by_account(self, account: str) -> float:
            return sum(self.kg_by_account(account).values())

    snapshot = HourSnapshot(
        hour=1,
        wall_deposit_by_segment_species_delta=dict(
            wall if delta_wall is None else delta_wall
        ),
    )
    model = SimpleNamespace(
        last_sticking_alpha_provenance_notice=notice,
        operating_history=(
            {"hour": 1, "pipe_segment_temperatures_C": {"hot_wall": 1100.0}},
        ),
        pipe_segments=(),
        condensation_temperatures_C={},
    )
    sim = SimpleNamespace(
        atom_ledger=_Ledger(wall),
        record=SimpleNamespace(snapshots=(snapshot,)),
        condensation_model=model,
        train=SimpleNamespace(stages=()),
    )
    sim._unspent_additive_reagents_kg = lambda: {}
    sim._consumed_additive_reagents_kg = lambda: {}
    return sim


def _materials_with_stage_alpha(
    stage_number: int,
    species: str,
    entry: dict[str, object],
) -> dict[str, object]:
    materials = copy.deepcopy(condensation_module.MATERIALS_DATA)
    stage_config = materials["stages"][stage_number]
    stage_config["alpha_s_by_species"][species] = dict(entry)
    return materials


def _materials_with_wall_alpha(
    species: str,
    entry: dict[str, object],
) -> dict[str, object]:
    materials = copy.deepcopy(condensation_module.MATERIALS_DATA)
    materials["wall_surfaces"]["interstage_duct"]["alpha_s_by_species"][
        species
    ] = dict(entry)
    for liner_config in materials["liner_materials"].values():
        alpha_by_species = liner_config.get("alpha_s_by_species")
        if isinstance(alpha_by_species, dict):
            alpha_by_species[species] = None
    return materials


def _materials_with_liner_alpha(
    liner_material: str,
    species: str,
    entry: dict[str, object],
) -> dict[str, object]:
    materials = copy.deepcopy(condensation_module.MATERIALS_DATA)
    materials["liner_materials"][liner_material]["alpha_s_by_species"][
        species
    ] = dict(entry)
    return materials


def _analytical_value_ref_entry(source_class: str) -> dict[str, object]:
    return {
        "value_ref": (
            "data/literature/vacuum_pyrolysis_sticking.yaml::species.Fe.value"
        ),
        "citation_status": "CITED",
        "status": "sourced",
        "source_class": source_class,
        "source": "test::analytical_value_ref_alpha",
        "output_status": "sourced_with_surface_proxy",
    }


def _route_wall_deposit_authority(
    species: str,
    *,
    materials: dict[str, object] | None = None,
    wall_temperature_C: float = 900.0,
) -> tuple[object, dict[str, object]]:
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=wall_temperature_C,
        materials=materials,
    )
    # These tests isolate sticking-alpha authority. Configure the direct route
    # without importing production's independently status-bearing geometry.
    model.configure_operating_conditions(
        overhead_pressure_mbar=0.0,
        campaign_name="C0",
    )
    melt = MeltState()
    melt.temperature_C = 1700.0
    route = model.route(
        EvaporationFlux(species_kg_hr={species: 1.0}, total_kg_hr=1.0),
        melt,
    )
    authority = wall_deposit_sticking_authority_status(
        route.wall_deposit_by_segment_species,
        route.sticking_alpha_provenance_notice,
    )
    return route, authority


def _constraints(species: str) -> PhysicsConstraintSet:
    return PhysicsConstraintSet(
        allowable_wall_deposit_kg={
            ("hot_wall", species): ThresholdSpec(
                id=f"allowable_wall_deposit_kg.hot_wall.{species}",
                value=1.0,
                units="kg",
                source="engineering_envelope",
                source_ref="test profile coating capacity",
            )
        }
    )


def _coating_surfaces(
    species: str,
    notice: dict[str, object],
    *,
    kg: float = 0.05,
) -> dict[str, object]:
    wall = {("hot_wall", species): kg}
    sim = _fake_sim(wall, notice)
    trace = PhysicsTrace.from_simulator(sim)
    coating = _constraints(species).coating(trace)
    fouling = _wall_fouling_report({species: kg}, alpha_notice=notice)
    product_summary = _coating_product_summary(
        SimpleNamespace(trace=trace, simulator=sim)
    )
    readout = _coating_readout(product_summary)
    record = SimpleNamespace(product_summary=product_summary)
    leaderboard_fields = _coating_leaderboard_fields((record,))
    leaderboard_row = _coating_leaderboard_row(record, leaderboard_fields)
    return {
        "trace": trace,
        "coating": coating,
        "fouling": fouling,
        "product_summary": product_summary,
        "readout": readout,
        "leaderboard_row": leaderboard_row,
    }


def _stale_coating_summary(
    species: str,
    *,
    cited: bool,
) -> dict[str, object]:
    wall = {"hot_wall": {species: 0.05}}
    return {
        "wall_deposit_kg_by_segment_species": wall,
        "wall_deposit_kg_by_zone_species": {"Hot": {species: 0.05}},
        "campaigns_to_resinter": 20.0,
        "coating_status": "available",
        "coating_authoritative": True,
        "coating_output_status": "authoritative",
        "coating_status_reason": "",
        "wall_deposit_sticking_authority": wall_deposit_sticking_authority_status(
            wall,
            _alpha_notice(species, cited=cited),
        ),
    }


def test_uncertified_deposited_alpha_status_reaches_all_coating_surfaces() -> None:
    notice = _alpha_notice("K", cited=False)

    surfaces = _coating_surfaces("K", notice)
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]
    leaderboard_row = surfaces["leaderboard_row"]

    assert coating.feasible
    assert coating.status == "warning"
    assert coating.authoritative is False
    assert coating.output_status == "status_bearing"
    assert list(coating.status_payload["uncertified_alpha_species"]) == ["K"]
    assert "non-authoritative" in coating.detail

    assert fouling["status"] == "warning"
    assert fouling["authoritative_for_resinter"] is False
    assert fouling["verdict_authoritative"] is False
    assert fouling["verdict"] == "non-authoritative"
    assert fouling["nominal_verdict"] != "non-authoritative"

    assert readout["status"] == "warning"
    assert readout["authoritative"] is False
    assert readout["output_status"] == "status_bearing"
    assert "UNCERTIFIED" in readout["reason"]

    assert product_summary["coating_status"] == "warning"
    assert product_summary["coating_authoritative"] is False
    assert leaderboard_row["coating_status"] == "warning"
    assert leaderboard_row["coating_authoritative"] is False
    assert leaderboard_row["coating_output_status"] == "status_bearing"


def test_uncertified_stage_alpha_driving_wall_deposit_fails_closed() -> None:
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 1.0,
                "status": "UNCERTIFIED",
                "output_status": "status_bearing",
                "source_class": "test_uncertified_stage_alpha",
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    assert "Fe" in authority["uncertified_alpha_species"]
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]
    assert provenance["stage_1"]["source_class"] == (
        "test_uncertified_stage_alpha"
    )


def test_stage_alpha_without_certification_status_fails_closed() -> None:
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 1.0,
                "source_class": "test_missing_stage_alpha_certification",
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]["stage_1"]
    assert provenance["citation_status"] == "UNCERTIFIED"
    assert provenance["status"] == "UNCERTIFIED"
    assert provenance["output_status"] == "status_bearing"
    assert "CITED/UNCERTIFIED" in provenance["certification_status_reason"]


def test_internal_analytical_stage_alpha_cannot_certify_wall_deposit() -> None:
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 1.0,
                "status": "CITED",
                "source_class": "internal-analytical",
                "source": "test::internal_analytical_alpha",
                "output_status": "sourced_with_surface_proxy",
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]["stage_1"]
    assert provenance["citation_status"] == "UNCERTIFIED"
    assert provenance["status"] == "UNCERTIFIED"
    assert provenance["output_status"] == "status_bearing"
    assert provenance["certification_status_reason"] == (
        "materials.yaml per-stage alpha_s source_class "
        "internal-analytical cannot certify"
    )


@pytest.mark.parametrize(
    "source_class_variant",
    (
        " internal-analytical",
        "internal-analytical ",
        " INTERNAL-ANALYTICAL ",
        "Internal-Analytical",
    ),
)
def test_internal_analytical_stage_alpha_variants_cannot_certify(
    source_class_variant: str,
) -> None:
    # F0 fail-open caught by cert-gate review 2026-06-30: source_class was
    # compared raw (unlike the .upper()-normalized status), so whitespace/case
    # variants of 'internal-analytical' with citation_status CITED certified as
    # authoritative wall-deposit alpha. They must all be denied.
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 1.0,
                "status": "CITED",
                "source_class": source_class_variant,
                "source": "test::internal_analytical_variant",
                "output_status": "sourced_with_surface_proxy",
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]["stage_1"]
    assert provenance["citation_status"] == "UNCERTIFIED"


def test_uncertified_non_condensing_stage_alpha_fails_closed() -> None:
    # Regression for the codex-caught F0 fail-open (2026-06-27): a stage whose
    # alpha is 0.0 does NOT condense, yet it still drives the final wall deposit
    # by passing all its vapor downstream to the wall sink, so its (uncertified)
    # provenance must still demote the deposit. Pre-fold this returned
    # authoritative=True with no stage_1 provenance key.
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 0.0,
                "status": "UNCERTIFIED",
                "output_status": "status_bearing",
                "source_class": "test_uncertified_noncondensing_stage_alpha",
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    assert "Fe" in authority["uncertified_alpha_species"]
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]
    assert provenance["stage_1"]["source_class"] == (
        "test_uncertified_noncondensing_stage_alpha"
    )


def test_null_wall_liner_alpha_falls_back_to_grounded_sidecar() -> None:
    # D-class sibling (codex round-2 catch): an explicit null wall/liner alpha
    # entry must fall back to the grounded sidecar coefficient, NOT collapse to
    # 0.0 -- otherwise a null override silently zeroes the wall deposit. Mirrors
    # the stage-null fallback.
    route_default, _ = _route_wall_deposit_authority("Fe")
    default_dep = route_default.wall_deposit_by_species.get("Fe", 0.0)
    assert default_dep > 0.0

    materials = copy.deepcopy(condensation_module.MATERIALS_DATA)
    materials["liner_materials"]["fe_condenser_liner"][
        "alpha_s_by_species"
    ]["Fe"] = None
    route_null, _ = _route_wall_deposit_authority("Fe", materials=materials)
    null_dep = route_null.wall_deposit_by_species.get("Fe", 0.0)

    # null -> sidecar == the grounded default; pre-fix this collapsed to 0.0.
    assert null_dep == pytest.approx(default_dep)


def test_uncertified_capture_budget_alpha_driving_wall_deposit_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sticking_alpha_s = condensation_module._sticking_alpha_s
    original_capture_budget_alpha_record = (
        condensation_module._capture_budget_alpha_record
    )

    def fake_sticking_alpha_s(species: str, T_K: float) -> float:
        if species == "Fe":
            return 1.0
        return original_sticking_alpha_s(species, T_K)

    def fake_capture_budget_alpha_record(
        species: str,
        T_K: float | None = None,
    ) -> dict[str, object]:
        if species != "Fe":
            return original_capture_budget_alpha_record(species, T_K=T_K)
        return {
            "species": "Fe",
            "alpha_s": 1.0,
            "source": "test::capture_budget.Fe",
            "source_class": "test_uncertified_capture_budget_alpha",
            "status": "UNCERTIFIED",
            "citation_status": "UNCERTIFIED",
            "output_status": "status_bearing",
        }

    monkeypatch.setattr(
        condensation_module,
        "_sticking_alpha_s",
        fake_sticking_alpha_s,
    )
    monkeypatch.setattr(
        condensation_module,
        "_capture_budget_alpha_record",
        fake_capture_budget_alpha_record,
    )

    route, authority = _route_wall_deposit_authority("Fe")

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]
    assert provenance["capture_budget"]["source_class"] == (
        "test_uncertified_capture_budget_alpha"
    )


def test_grounded_stage_alpha_driving_wall_deposit_stays_authoritative() -> None:
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value_ref": (
                    "data/literature/vacuum_pyrolysis_sticking.yaml::"
                    "species.Fe.value"
                ),
            },
        ),
    )

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is True
    provenance = authority["alpha_s_provenance_by_species"]["Fe"]
    assert provenance["stage_1"]["citation_status"] == "CITED"
    assert provenance["capture_budget"]["citation_status"] == "CITED"


@pytest.mark.parametrize(
    ("materials_case", "provenance_key", "reason_label"),
    (
        ("stage", "stage_1", "materials.yaml per-stage alpha_s"),
        ("wall", "stage_0_to_stage_1", "materials.yaml wall alpha_s"),
        ("liner", "stage_0_to_stage_1", "materials.yaml liner alpha_s"),
    ),
)
@pytest.mark.parametrize(
    "source_class_variant",
    ("stub", "internal_analytical", "internal-analytical"),
)
def test_analytical_value_ref_material_alpha_cannot_certify_wall_deposit(
    materials_case: str,
    provenance_key: str,
    reason_label: str,
    source_class_variant: str,
) -> None:
    entry = _analytical_value_ref_entry(source_class_variant)
    if materials_case == "stage":
        materials = _materials_with_stage_alpha(1, "Fe", entry)
    elif materials_case == "wall":
        materials = _materials_with_wall_alpha("Fe", entry)
    else:
        materials = _materials_with_liner_alpha(
            "fe_condenser_liner",
            "Fe",
            entry,
        )

    route, authority = _route_wall_deposit_authority("Fe", materials=materials)

    assert route.wall_deposit_by_species["Fe"] > 0.0
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_uncertified"
    record = authority["alpha_s_provenance_by_species"]["Fe"][provenance_key]
    assert record["citation_status"] == "UNCERTIFIED"
    assert record["status"] == "UNCERTIFIED"
    assert record["output_status"] == "status_bearing"
    assert record["source_class"] == source_class_variant
    assert reason_label in record["certification_status_reason"]
    assert "cannot certify" in record["certification_status_reason"]


def test_zero_deposit_stage_alpha_status_stays_authoritative() -> None:
    route, authority = _route_wall_deposit_authority(
        "Fe",
        materials=_materials_with_stage_alpha(
            1,
            "Fe",
            {
                "value": 1.0,
                "status": "UNCERTIFIED",
                "output_status": "status_bearing",
                "source_class": "test_uncertified_stage_alpha",
            },
        ),
        wall_temperature_C=2000.0,
    )

    assert route.wall_deposit_by_species.get("Fe", 0.0) == pytest.approx(0.0)
    assert authority["authoritative_for_deposit_mass"] is True
    assert authority["deposited_species"] == []


def test_missing_output_status_is_status_bearing_for_deposited_alpha() -> None:
    surfaces = _coating_surfaces("K", _sourced_missing_output_notice("K"))
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]

    assert coating.status == "warning"
    assert coating.authoritative is False
    assert coating.status_payload["code"] == "wall_deposit_sticking_alpha_uncertified"
    assert fouling["status"] == "warning"
    assert fouling["authoritative_for_resinter"] is False
    assert product_summary["coating_status"] == "warning"
    assert product_summary["coating_authoritative"] is False
    assert readout["status"] == "warning"
    assert readout["authoritative"] is False


@pytest.mark.parametrize(
    ("notice", "reason_fragment"),
    (
        (_missing_record_notice("K"), "provenance is missing"),
        (_alpha_notice("SiO", cited=True), "provenance is missing"),
    ),
)
def test_missing_or_wrong_species_alpha_status_reaches_all_coating_surfaces(
    notice: dict[str, object],
    reason_fragment: str,
) -> None:
    surfaces = _coating_surfaces("K", notice)
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]
    leaderboard_row = surfaces["leaderboard_row"]

    assert coating.feasible
    assert coating.status == "warning"
    assert coating.authoritative is False
    assert coating.output_status == "status_bearing"
    assert coating.status_payload["code"] == (
        "wall_deposit_sticking_alpha_provenance_missing"
    )
    assert list(coating.status_payload["uncertified_alpha_species"]) == ["K"]
    assert reason_fragment in coating.status_reason

    assert fouling["status"] == "warning"
    assert fouling["authoritative_for_resinter"] is False
    assert fouling["verdict_authoritative"] is False
    assert fouling["verdict"] == "non-authoritative"
    assert fouling["nominal_verdict"] != "non-authoritative"

    assert product_summary["coating_status"] == "warning"
    assert product_summary["coating_authoritative"] is False
    assert product_summary["coating_output_status"] == "status_bearing"
    assert readout["status"] == "warning"
    assert readout["authoritative"] is False
    assert reason_fragment in readout["reason"]
    assert leaderboard_row["coating_status"] == "warning"
    assert leaderboard_row["coating_authoritative"] is False
    assert leaderboard_row["coating_output_status"] == "status_bearing"


def test_zero_deposit_uncertified_alpha_status_stays_authoritative() -> None:
    notice = _alpha_notice("K", cited=False)

    surfaces = _coating_surfaces("K", notice, kg=0.0)
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]
    leaderboard_row = surfaces["leaderboard_row"]

    assert coating.status == "available"
    assert coating.authoritative is True
    assert fouling["status"] == "available"
    assert fouling["authoritative_for_resinter"] is True
    assert product_summary["coating_status"] == "available"
    assert product_summary["coating_authoritative"] is True
    assert readout["status"] == "available"
    assert readout["authoritative"] is True
    assert leaderboard_row["coating_status"] == "available"
    assert leaderboard_row["coating_authoritative"] is True


def test_web_positive_deposit_without_authority_defaults_non_authoritative() -> None:
    readout = _coating_readout(
        {
            "wall_deposit_kg_by_segment_species": {
                "hot_wall": {"K": 0.05},
            },
            "campaigns_to_resinter": 20.0,
        }
    )
    zero_readout = _coating_readout(
        {
            "wall_deposit_kg_by_segment_species": {
                "hot_wall": {"K": 0.0},
            },
            "campaigns_to_resinter": "infinite",
        }
    )

    assert readout["status"] == "warning"
    assert readout["authoritative"] is False
    assert readout["output_status"] == "status_bearing"
    assert "authority missing" in readout["reason"]
    assert zero_readout["status"] == "available"
    assert zero_readout["authoritative"] is True


def test_web_readout_provenance_overrides_stale_true_summary_bool() -> None:
    readout = _coating_readout(_stale_coating_summary("K", cited=False))

    assert readout["status"] == "warning"
    assert readout["authoritative"] is False
    assert readout["output_status"] == "status_bearing"


def test_grounded_true_coating_authority_is_not_demoted() -> None:
    readout = _coating_readout(_stale_coating_summary("Fe", cited=True))

    assert readout["status"] == "available"
    assert readout["authoritative"] is True


def test_leaderboard_provenance_overrides_stale_true_summary_bool() -> None:
    record = SimpleNamespace(product_summary=_stale_coating_summary("K", cited=False))
    leaderboard_fields = _coating_leaderboard_fields((record,))
    row = _coating_leaderboard_row(record, leaderboard_fields)

    assert row["coating_status"] == "warning"
    assert row["coating_authoritative"] is False
    assert row["coating_output_status"] == "status_bearing"


def test_study_record_product_summary_provenance_overrides_stale_true_bool() -> None:
    reference = SimpleNamespace(product_summary=_stale_coating_summary("K", cited=False), trace={})
    summary = _product_summary_mapping(reference)

    assert summary["coating_status"] == "warning"
    assert summary["coating_authoritative"] is False
    assert summary["coating_output_status"] == "status_bearing"


@pytest.mark.parametrize("species", ("Fe", "SiO", "Mg", "Na"))
def test_cited_deposited_alpha_status_stays_authoritative(species: str) -> None:
    notice = _alpha_notice(species, cited=True)

    surfaces = _coating_surfaces(species, notice)
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]
    leaderboard_row = surfaces["leaderboard_row"]

    assert coating.status == "available"
    assert coating.authoritative is True
    assert list(coating.status_payload["uncertified_alpha_species"]) == []
    assert "non-authoritative" not in coating.detail

    assert fouling["status"] == "available"
    assert fouling["authoritative_for_resinter"] is True
    assert fouling["verdict_authoritative"] is True
    assert fouling["verdict"] != "non-authoritative"

    assert readout["status"] == "available"
    assert readout["authoritative"] is True
    assert product_summary["coating_status"] == "available"
    assert product_summary["coating_authoritative"] is True
    assert leaderboard_row["coating_status"] == "available"
    assert leaderboard_row["coating_authoritative"] is True


def test_cited_cumulative_authority_subset_deposit_stays_authoritative() -> None:
    notice = _alpha_notice("Fe", cited=True)
    notice["alpha_s_provenance_by_species"].update(
        _alpha_notice("Mg", cited=True)["alpha_s_provenance_by_species"]
    )
    sim = _fake_sim(
        {("hot_wall", "Fe"): 0.05, ("hot_wall", "Mg"): 0.02},
        notice,
        delta_wall={("hot_wall", "Fe"): 0.05},
    )
    trace = PhysicsTrace.from_simulator(sim)

    coating = _constraints("Fe").coating(trace)
    product_summary = _coating_product_summary(
        SimpleNamespace(trace=trace, simulator=sim)
    )

    assert product_summary["coating_authoritative"] is True
    assert coating.authoritative == product_summary["coating_authoritative"]
    assert coating.status == product_summary["coating_status"] == "available"


def test_forged_precomputed_authority_without_grounding_fails_closed() -> None:
    forged = {
        "authoritative": True,
        "authoritative_for_deposit_mass": True,
        "authoritative_for_coating": True,
        "authoritative_for_resinter": True,
        "output_status": "authoritative",
        "deposited_species": ["K"],
        "uncertified_alpha_species": [],
        "alpha_s_provenance_by_species": {
            "K": {
                "hot_wall": {
                    "segment": "hot_wall",
                    "species": "K",
                    "citation_status": "CITED",
                    "status": "sourced",
                    "output_status": "sourced_with_surface_proxy",
                }
            }
        },
    }

    surfaces = _coating_surfaces("K", forged)
    coating = surfaces["coating"]
    product_summary = surfaces["product_summary"]
    readout = surfaces["readout"]

    assert coating.authoritative is False
    assert coating.status_payload["code"] == (
        "wall_deposit_sticking_alpha_provenance_missing"
    )
    assert product_summary["coating_authoritative"] is False
    assert readout["authoritative"] is False


def test_segment_deposit_without_matching_alpha_record_fails_closed() -> None:
    payload = wall_deposit_sticking_authority_status(
        {
            "hot_wall": {"Fe": 0.05},
            "cold_wall": {"Fe": 0.02},
        },
        _alpha_notice("Fe", cited=True),
    )

    assert payload["authoritative_for_coating"] is False
    assert payload["code"] == "wall_deposit_sticking_alpha_provenance_missing"
    assert payload["uncertified_alpha_species"] == ["Fe"]
    assert payload["missing_alpha_segment_species"] == [
        {"segment": "cold_wall", "species": "Fe"}
    ]


def test_cited_deposited_alpha_without_value_fails_closed() -> None:
    surfaces = _coating_surfaces("Fe", _cited_missing_alpha_notice("Fe"))
    coating = surfaces["coating"]
    fouling = surfaces["fouling"]
    product_summary = surfaces["product_summary"]

    assert coating.authoritative is False
    assert coating.status == "warning"
    assert coating.status_payload["code"] == (
        "wall_deposit_sticking_alpha_provenance_missing"
    )
    assert fouling["authoritative_for_resinter"] is False
    assert product_summary["coating_authoritative"] is False


def test_authority_payload_with_sets_is_pickle_and_json_safe() -> None:
    payload = wall_deposit_sticking_authority_status(
        {("hot_wall", "Fe"): 0.05},
        {
            "alpha_s_provenance_by_species": {
                "Fe": {
                    "hot_wall": {
                        "segment": "hot_wall",
                        "species": "Fe",
                        "alpha_s": 0.02,
                        "citation_status": "CITED",
                        "status": "sourced",
                        "output_status": "sourced_with_surface_proxy",
                        "tags": {"beta", "alpha"},
                        "frozen": frozenset({"delta", "gamma"}),
                    }
                }
            }
        },
    )

    pickle.loads(pickle.dumps(payload))
    json.dumps(payload, sort_keys=True)
    record = payload["alpha_s_provenance_by_species"]["Fe"]["hot_wall"]
    assert record["tags"] == ["alpha", "beta"]
    assert record["frozen"] == ["delta", "gamma"]
