from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery.residue import (
    _SOSSI_GEOMETRIES,
    _sossi_geometry_areas,
    _sossi_initial_inventory,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def test_sossi_start_inventory_contains_table1_host_and_starting_trace() -> None:
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace_ppm = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    inventory, sample_mass_kg, inventory_mass_kg = _sossi_initial_inventory(
        experiment, starting_trace_ppm
    )

    assert sample_mass_kg == 25.0e-6
    assert {
        "SiO2",
        "Al2O3",
        "MgO",
        "FeO",
        "CaO",
        "MnO",
        "TiO2",
    } <= set(inventory)
    assert inventory["MnO"] > 0.0
    assert inventory["TiO2"] > 0.0
    assert inventory_mass_kg > 0.0
    assert experiment["sample"]["printed_composition"]["locator"]["table"] == "1"
    assert experiment["sample"]["printed_composition"]["state"]["value"] == {
        "SiO2": 40.6,
        "Al2O3": 10.67,
        "MgO": 15.4,
        "FeO": 16.26,
        "CaO": 16.82,
    }


def test_sossi_geometry_is_declared_from_bead_mass_without_residue_inputs() -> None:
    areas = _sossi_geometry_areas(25.0e-6)

    assert tuple(areas) == _SOSSI_GEOMETRIES
    assert all(value > 0.0 for value in areas.values())
    assert areas["pt_loop_bead_sphere_constant"] == areas[
        "pt_loop_bead_sphere_shrinking"
    ]
    radius_m = (3.0 * 25.0e-6 / (4.0 * math.pi * 2700.0)) ** (1.0 / 3.0)
    assert areas["pt_loop_bead_sphere_constant"] == pytest.approx(
        4.0 * math.pi * radius_m**2
    )
    assert areas["pt_loop_bead_disk_equivalent_volume"] != areas[
        "pt_loop_bead_sphere_constant"
    ]


def test_sossi_prediction_provenance_pins_engine_binding_identity(monkeypatch) -> None:
    pytest.importorskip("openimcc")
    import openimcc

    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    def no_evaporation(inventory, *_args, **_kwargs):
        return SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        catalog,
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        code_revision="pack-digest-pin-test",
        engine="openimcc",
    )[0]

    gas_pack = openimcc.load_gas_datapack()
    package_identity = openimcc.engine_binding_identity(
        openimcc_bridge._load_pack("v1.0.2"), gas_pack
    )
    expected_identity = {
        "engine_binding_digest": package_identity.digest,
        "melt_binding_digest": package_identity.melt_binding_digest,
        "condensate_table_digest": package_identity.condensate_table_digest,
        "gas_table_digest": package_identity.gas_table_digest,
    }
    assert prediction.provenance["engine_binding_identity"] == expected_identity
    assert "gas_pack_digest" not in prediction.provenance
    assert "liquid_pack_digest" not in prediction.provenance


def test_sossi_scorer_projects_mn_and_types_unsupported_elements(monkeypatch) -> None:
    from simulator.battery.enums import Engine, MetricOperation, RefusalReason
    from simulator.battery.score import (
        _sossi_residue_prediction,
        load_score_context,
        metric_operation_for_identity,
        point_magnitude,
    )
    import simulator.battery.residue as residue

    context = load_score_context(sources=("kems-012-sossi-2019",))
    rows = [
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
        and row.identity.species.formula in {"Mn", "Gd"}
    ]
    manganese = next(row for row in rows if row.identity.species.formula == "Mn")
    gadolinium = next(row for row in rows if row.identity.species.formula == "Gd")
    expected_run = manganese.experiment_id.rsplit("::", 1)[-1]

    def fake_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=item["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={
                    "integration": {"primary": {"status": "converged"}},
                    "code_revision": "fixture-revision",
                },
            )
            for item in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", fake_cohort)
    cache = {}
    prediction = _sossi_residue_prediction(
        context, manganese, Engine.OPENIMCC, cache
    )
    refusal = _sossi_residue_prediction(
        context, gadolinium, Engine.OPENIMCC, cache
    )

    assert expected_run in cache[("kems-012-sossi-2019", Engine.OPENIMCC)]
    assert prediction.execution.state.value == "produced"
    assert prediction.value == 900.0
    assert prediction.identity == manganese.identity
    assert any(
        notice.reason == "bc_open_furnace_langmuir_limit_diagnostic"
        for notice in prediction.notices
    )
    assert metric_operation_for_identity(manganese.identity) is MetricOperation.DEX
    assert refusal.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert refusal.refusal_detail["reason"] == "channel_missing"


def test_mixed_hashimoto_and_sossi_score_keep_separate_engine_cohorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.battery.enums import Engine, Quantity, Rail
    from simulator.battery.score import (
        load_score_context,
        point_magnitude,
        quantity_token,
        score_store,
    )
    import simulator.battery.residue as residue

    hashimoto_source = "kems-015-hashimoto-1983"
    sossi_source = "kems-012-sossi-2019"
    context = load_score_context(sources=(hashimoto_source, sossi_source))

    def hashimoto_cohort(experiments, *_args, **_kwargs):
        rows = []
        for experiment in experiments:
            for alpha_arm in (
                residue._HASHIMOTO_PRIMARY_ALPHA_ARM,
                "alpha_runtime_catalog",
            ):
                rows.append(
                    SimpleNamespace(
                        experiment_id=experiment["experiment_id"],
                        alpha_arm=alpha_arm,
                        primary_geometry_policy_id=residue._HASHIMOTO_PRIMARY_GEOMETRY,
                        primary_oxide_wt_pct={
                            oxide: 1.0 for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                        },
                        sensitivity_band_wt_pct={
                            oxide: (0.9, 1.1)
                            for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                        },
                        geometry_oxide_wt_pct={
                            residue._HASHIMOTO_PRIMARY_GEOMETRY: {
                                oxide: 1.0
                                for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                            }
                        },
                        provenance={
                            "geometry_refusal_by_geometry": {},
                            "integration": {"refinement_status": "converged"},
                            "code_revision": "fixture-revision",
                        },
                    )
                )
        return tuple(rows)

    def sossi_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=experiment["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={
                    "integration": {"primary": {"status": "converged"}},
                    "code_revision": "fixture-revision",
                },
            )
            for experiment in experiments
        )

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", hashimoto_cohort)
    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", sossi_cohort)

    residuals, candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    pinned_score = next(
        row
        for row in residuals
        if row.reference
        == "kems-012-sossi-2019::sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::T=1573.15:h=0eaa2bb3c525"
    )
    assert pinned_score.key == (
        "kems-012-sossi-2019::sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::"
        "T=1573.15:h=0eaa2bb3c525:T=1573.15::residue_component_composition::"
        "residue_composition::openimcc"
    )
    assert pinned_score.candidate == (
        "engine:openimcc:kems-012-sossi-2019::"
        "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::"
        "T=1573.15:h=0eaa2bb3c525"
    )
    assert candidates[pinned_score.candidate].observation_id == pinned_score.candidate
    assert pinned_score.numeric is not None
    assert float(pinned_score.numeric.value).hex() == "0x1.88fea8359f38fp-4"
    hashimoto_ids = {
        row.observation_id
        for row in context.observations.values()
        if row.source_id == hashimoto_source
        and "hashimoto_1983_table3_residue_composition_series" in row.observation_id
        and row.admission.status.value == "admitted"
        and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and point_magnitude(row.value) is not None
    }
    sossi_ti_ids = {
        row.observation_id
        for row in context.observations.values()
        if row.source_id == sossi_source
        and row.identity.species.formula == "Ti"
        and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
    }
    numeric_references = {
        residual.reference for residual in residuals if residual.numeric is not None
    }

    assert len(hashimoto_ids) == 120
    assert len(sossi_ti_ids) == 43
    assert hashimoto_ids <= numeric_references
    assert sossi_ti_ids <= numeric_references
