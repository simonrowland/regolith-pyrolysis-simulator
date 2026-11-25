from __future__ import annotations

import json
import subprocess
import warnings
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import Authority, Engine, NoticeKind, Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.residue import (
    ResidueInventoryRefusal,
    ResidueChannel,
    _hashimoto_integrate_geometry,
    _time_forward_thermal_segments,
    predict_mass_loss_fraction,
)
from simulator.battery.score import ScoreContext, score_store


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    (
        "zhang-2021-acsesc-si",
        106,
        "data/literature/extracts/zhang-2021-acsesc-si.yaml",
    ),
    (
        "ivanova-2018-spinel-rich-cai-evaporation",
        32,
        "data/literature/extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml",
    ),
)


def _residue_context(source_id: str):
    source_path = ROOT / next(path for sid, _count, path in SOURCES if sid == source_id)
    migrator = Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(source_path)
    migrator.finalize()
    migrated = migrator.result
    source_observations = {
        row.observation_id: row
        for row in migrated.observations.values()
        if row.source_id == source_id
    }
    observations = {
        observation_id: row
        for observation_id, row in source_observations.items()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    }
    return (
        ScoreContext(
            works=migrated.works,
            experiments=migrated.experiments,
            observations=observations,
            root=ROOT,
        ),
        observations,
        migrated.experiments,
        source_observations,
    )


def _assert_openimcc_pin() -> None:
    import openimcc

    module_path = Path(openimcc.__file__).resolve()
    result = subprocess.run(
        ["sh", "-c", "find . -name '*.py' | sort | xargs shasum | shasum"],
        cwd=module_path.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    tree_hash = result.stdout.split()[0][:12]
    warnings.warn(
        "OPENIMCC_PIN "
        + json.dumps(
            {"module": str(module_path), "tree_hash": tree_hash}, sort_keys=True
        ),
        UserWarning,
        stacklevel=1,
    )
    assert tree_hash == "308672269d6e"


def test_zhang_zero_time_path_applies_the_printed_hold_only_at_1200c() -> None:
    source_id = "zhang-2021-acsesc-si"
    _context, observations, _experiments, _source_observations = _residue_context(
        source_id
    )
    zero_time_by_temperature = {}
    for observation in observations.values():
        temperature_K = float(observation.identity.temperature_K.value)
        time_min = float(observation.point_conditions["time_min"].state.value)
        if time_min == 0.0:
            celsius = round(temperature_K - 273.15, 6)
            if celsius in {1200.0, 1400.0}:
                zero_time_by_temperature[celsius] = observation
    assert set(zero_time_by_temperature) == {1200.0, 1400.0}

    path_1200 = _time_forward_thermal_segments(
        source_id,
        zero_time_by_temperature[1200.0],
        float(zero_time_by_temperature[1200.0].identity.temperature_K.value),
    )
    path_1400 = _time_forward_thermal_segments(
        source_id,
        zero_time_by_temperature[1400.0],
        float(zero_time_by_temperature[1400.0].identity.temperature_K.value),
    )
    assert any(
        at_K == 1200.0 + 273.15 and seconds == 300.0
        for at_K, seconds in path_1200
    )
    assert not any(seconds >= 300.0 for _at_K, seconds in path_1400)
    assert round(path_1400[-1][0] - 273.15, 6) == 1400.0
    assert path_1400[-1][1] == 0.0


def test_zhang_area_endpoints_use_linear_time_interpolation() -> None:
    seen_areas = []
    integration = _hashimoto_integrate_geometry(
        {"MgO": 1.0},
        (ResidueChannel("O2", "O2", None, 1.0, "test oxygen channel"),),
        lambda _inventory, _log10_pO2_bar: {},
        temperature_K=1473.15,
        duration_s=2.0,
        steps=2,
        geometry_policy_id="linear_time_endpoints",
        initial_area_m2=2.0e-4,
        final_area_m2=4.0e-4,
        on_step=lambda _inventory, _channels, area_m2, _seconds: seen_areas.append(
            area_m2
        ),
    )
    assert seen_areas == pytest.approx([2.5e-4, 3.5e-4])
    assert integration.inventory_mol == {"MgO": 1.0}


@pytest.mark.parametrize("source_id, expected_count, _path", SOURCES)
def test_time_forward_residue_rows_reach_the_engine_and_emit_residuals(
    source_id: str,
    expected_count: int,
    _path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_openimcc_pin()
    context, observations, experiments, source_observations = _residue_context(source_id)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        include_diagnostics=False,
    )

    assert len(observations) == expected_count
    assert len(residuals) == expected_count
    if source_id == "zhang-2021-acsesc-si":
        assert all(
            residual.numeric is None
            and residual.refusal is not None
            and residual.refusal.reason.value == "outside_supported_species"
            and residual.refusal.detail["engine_reason"]
            == "imcc_component_outside_domain"
            and "Rb2O" in str(residual.refusal.detail)
            for residual in residuals
        )
        mass_loss_observations = {
            observation_id: observation
            for observation_id, observation in source_observations.items()
            if observation.identity.quantity.is_value
            and observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        }
        mass_loss_context = ScoreContext(
            works=context.works,
            experiments=experiments,
            observations=mass_loss_observations,
            root=ROOT,
        )
        mass_loss_residuals, _mass_loss_candidates = score_store(
            mass_loss_context,
            engines=(Engine.OPENIMCC,),
            include_diagnostics=False,
        )
        assert len(mass_loss_observations) == 12
        assert len(mass_loss_residuals) == 12
        assert all(
            row.numeric is None
            and row.refusal is not None
            and row.refusal.reason.value == "outside_supported_species"
            for row in mass_loss_residuals
        )
        for residual in (*residuals, *mass_loss_residuals):
            reasons = [notice.reason for notice in residual.notices]
            assert any(
                notice.kind is NoticeKind.ALPHA_ASSUMED
                and notice.policy is not None
                and notice.policy.id == "DP-005"
                for notice in residual.notices
            )
            assert any("mass_loss_pO2_not_printed" in reason for reason in reasons)
            assert any(
                "mass_loss_oxygen_escape_assumption" in reason
                for reason in reasons
            )
            assert any("area_uncertainty_not_printed" in reason for reason in reasons)

        mass_loss_residual_by_id = {
            residual.reference: residual for residual in mass_loss_residuals
        }
        for observation_id, observation in mass_loss_observations.items():
            residual = mass_loss_residual_by_id[observation_id]
            reasons = [notice.reason for notice in residual.notices]
            initial_area = observation.point_conditions.get(
                "exposed_area_initial_m2"
            )
            final_area = observation.point_conditions.get("exposed_area_final_m2")
            if initial_area is not None and final_area is not None:
                assert any(
                    "surface_area_interpolated_between_printed_endpoints" in reason
                    for reason in reasons
                )
                mass_before = float(
                    observation.point_conditions["sample_mass_kg"].state.value
                )
                mass_after = float(
                    observation.point_conditions["sample_mass_after_kg"].state.value
                )
                area_growth_with_mass_loss = (
                    float(final_area.state.value) > float(initial_area.state.value)
                    and mass_after < mass_before
                )
                assert any(
                    "area_shape_conflicts_with_self_similar" in reason
                    for reason in reasons
                ) is area_growth_with_mass_loss
            else:
                assert not any(
                    "surface_area_interpolated_between_printed_endpoints" in reason
                    for reason in reasons
                )
                assert any(
                    notice.authority is Authority.CONVENTION
                    and notice.policy is not None
                    and notice.policy.id == "DP-116"
                    and "exposed_area_not_printed" in notice.reason
                    for notice in residual.notices
                )
        warnings.warn(
            "ZHANG_MASS_LOSS_DIAGNOSTICS "
            + json.dumps(
                {
                    "rows": len(mass_loss_residuals),
                    "refusal_reason": "outside_supported_species",
                    "sample_detail": str(mass_loss_residuals[0].refusal.detail),
                },
                sort_keys=True,
            ),
            UserWarning,
            stacklevel=1,
        )
        import simulator.battery.residue as residue_module

        measured_a_over_m = []
        for observation in mass_loss_observations.values():
            area = observation.point_conditions.get("exposed_area_initial_m2")
            mass = observation.point_conditions["sample_mass_kg"]
            if area is not None:
                measured_a_over_m.append(
                    float(area.state.value) / float(mass.state.value)
                )
        missing_area = next(
            row
            for row in mass_loss_observations.values()
            if row.locator is not None and "Bas1400C0m2" in row.locator.note
        )
        printed_mass = float(
            missing_area.point_conditions["sample_mass_kg"].state.value
        )
        derived_geometry = residue_module._langmuir_default_geometry(printed_mass)
        measured_range = [min(measured_a_over_m), max(measured_a_over_m)]
        warnings.warn(
            "ZHANG_C0M2_GEOMETRY "
            + json.dumps(
                {
                    "sample_mass_kg": printed_mass,
                    "derived_initial_area_m2": derived_geometry["area_m2"],
                    "derived_A_over_m_m2_per_kg": derived_geometry[
                        "area_to_mass_m2_per_kg"
                    ],
                    "measured_other_11_A_over_m_range_m2_per_kg": measured_range,
                    "derived_ratio_outside_measured_range": not (
                        measured_range[0]
                        <= derived_geometry["area_to_mass_m2_per_kg"]
                        <= measured_range[1]
                    ),
                },
                sort_keys=True,
            ),
            UserWarning,
            stacklevel=1,
        )
        return
    assert all(residual.numeric is not None for residual in residuals), [
        repr(residual) for residual in residuals if residual.numeric is None
    ]
    for residual in residuals:
        assert any(
            notice.kind is NoticeKind.ALPHA_ASSUMED
            and notice.policy is not None
            and notice.policy.id == "DP-005"
            for notice in residual.notices
        )
    if source_id == "ivanova-2018-spinel-rich-cai-evaporation":
        for residual in residuals:
            reasons = [notice.reason for notice in residual.notices]
            assert any(
                notice.kind is NoticeKind.KNOB_DEFAULTED
                and notice.authority is Authority.CONVENTION
                and notice.policy is not None
                and notice.policy.id == "DP-116"
                for notice in residual.notices
            )
            assert any("sample_mass_not_printed" in reason for reason in reasons)
            assert any("exposed_area_not_printed" in reason for reason in reasons)
            assert any("phase_state_not_printed" in reason for reason in reasons)
            assert any("zero_time_ramp_not_modelled" in reason for reason in reasons)
    common_flags = sorted(
        {
            (
                notice.policy.id
                if notice.policy is not None
                else notice.kind.value
                if notice.kind is NoticeKind.APPROXIMATE_PRESSURE
                else notice.reason.split(":", 1)[0]
            )
            for residual in residuals
            for notice in residual.notices
        }
    )
    table = []
    for residual in residuals:
        reference = observations[residual.reference]
        table.append(
            {
                "point": reference.observation_id.rsplit("::point:", 1)[-1],
                "time_min": str(
                    reference.point_conditions["time_min"].state.value
                ),
                "oxide": reference.identity.species.formula,
                "observed_wt_pct": str(reference.value.point),
                "predicted_wt_pct": format(
                    float(reference.value.point + residual.numeric.value), ".6g"
                ),
                "residual": format(float(residual.numeric.value), ".6g"),
            }
        )
    warnings.warn(
        "RESIDUAL_TABLE "
        + json.dumps(
            {source_id: {"flags": common_flags, "rows": table}}, sort_keys=True
        ),
        UserWarning,
        stacklevel=1,
    )
    if source_id == "ivanova-2018-spinel-rich-cai-evaporation":
        import simulator.battery.residue as residue_module

        sensitivity_observations = {
            observation_id: observation
            for observation_id, observation in observations.items()
            if observation.identity.species.formula in {"MgO", "SiO2"}
        }
        sensitivity_context = ScoreContext(
            works=context.works,
            experiments=context.experiments,
            observations=sensitivity_observations,
            root=ROOT,
        )
        baseline = {
            residual.reference: residual.numeric.value
            for residual in residuals
            if residual.reference in sensitivity_observations
        }
        original_geometry = residue_module._langmuir_default_geometry
        sensitivity_rows = []
        for factor in (0.5, 2.0):
            def scaled_geometry(sample_mass_kg=None, factor=factor):
                geometry = original_geometry(sample_mass_kg)
                # The time-forward inventory depends on geometry through A/m;
                # changing the ratio captures the stated diameter sweep without
                # changing the composition basis or any other model input.
                geometry["area_m2"] *= factor
                geometry["area_to_mass_m2_per_kg"] *= factor
                return geometry

            monkeypatch.setattr(
                residue_module, "_langmuir_default_geometry", scaled_geometry
            )
            swept, _swept_candidates = score_store(
                sensitivity_context,
                engines=(Engine.OPENIMCC,),
                include_diagnostics=False,
            )
            for residual in swept:
                assert residual.numeric is not None
                sensitivity_rows.append(
                    {
                        "A_over_m_factor": factor,
                        "observation_id": residual.reference.rsplit("::point:", 1)[-1],
                        "time_min": str(
                            observations[residual.reference]
                            .point_conditions["time_min"]
                            .state.value
                        ),
                        "oxide": observations[residual.reference].identity.species.formula,
                        "base_residual": format(
                            float(baseline[residual.reference]), ".6g"
                        ),
                        "swept_residual": format(
                            float(residual.numeric.value), ".6g"
                        ),
                        "delta": format(
                            float(
                                residual.numeric.value
                                - baseline[residual.reference]
                            ),
                            ".6g",
                        ),
                    }
                )
        warnings.warn(
            "IVANOVA_A_OVER_M_SENSITIVITY "
            + json.dumps(sensitivity_rows, sort_keys=True),
            UserWarning,
            stacklevel=1,
        )
        for factor in (0.5, 2.0):
            assert any(
                row["A_over_m_factor"] == factor and row["delta"] != "0"
                for row in sensitivity_rows
            )


@pytest.mark.parametrize("source_id, _count, _path", SOURCES)
def test_restoring_the_old_method_lock_drops_the_residue_route(
    source_id: str, _count: int, _path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.battery.score as score_module

    context, observations, experiments, _source_observations = _residue_context(source_id)
    first = next(iter(observations.values()))
    context = ScoreContext(
        works=context.works,
        experiments={first.experiment_id: experiments[first.experiment_id]},
        observations={first.observation_id: first},
        root=ROOT,
    )
    original = score_module._extent_residue_method

    def old_selector(reference, experiment, quantity):
        if reference.source_id in {sid for sid, _n, _p in SOURCES}:
            return None
        return original(reference, experiment, quantity)

    monkeypatch.setattr(score_module, "_extent_residue_method", old_selector)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        include_diagnostics=False,
    )
    with pytest.raises(AssertionError):
        assert len(residuals) == 1


def test_time_series_cache_key_includes_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.battery.score as score_module

    context, observations, _experiments, _source_observations = _residue_context(
        "ivanova-2018-spinel-rich-cai-evaporation"
    )
    magnesium_rows = {
        observation_id: observation
        for observation_id, observation in observations.items()
        if observation.identity.species.formula == "MgO"
        and float(observation.point_conditions["time_min"].state.value)
        in {0.0, 5.0}
    }
    assert len(magnesium_rows) == 2
    times = {
        observation_id: float(
            observation.point_conditions["time_min"].state.value
        )
        for observation_id, observation in magnesium_rows.items()
    }
    narrowed_context = ScoreContext(
        works=context.works,
        experiments=context.experiments,
        observations=magnesium_rows,
        root=ROOT,
    )

    def assert_time_changes_prediction() -> None:
        residuals, _candidates = score_store(
            narrowed_context,
            engines=(Engine.OPENIMCC,),
            include_diagnostics=False,
        )
        assert len(residuals) == 2
        predicted_by_time = {
            times[row.reference]: (
                magnesium_rows[row.reference].value.point + row.numeric.value
            )
            for row in residuals
            if row.numeric is not None
        }
        assert set(predicted_by_time) == {0.0, 5.0}
        assert predicted_by_time[0.0] != predicted_by_time[5.0], (
            "time-forward rows must not share a cached extent"
        )

    assert_time_changes_prediction()

    original_cache_entry = score_module._residue_prediction_cache_entry

    def old_time_insensitive_cache_key(
        cache, source_id, engine, selected_experiment_ids=None, *, value=None
    ):
        if source_id.startswith("langmuir:ivanova-"):
            source_id = source_id.partition(":{")[0]
        return original_cache_entry(
            cache,
            source_id,
            engine,
            selected_experiment_ids,
            value=value,
        )

    with monkeypatch.context() as mutation:
        mutation.setattr(
            score_module,
            "_residue_prediction_cache_entry",
            old_time_insensitive_cache_key,
        )
        with pytest.raises(
            AssertionError, match="time-forward rows must not share a cached extent"
        ):
            assert_time_changes_prediction()


@pytest.mark.parametrize("source_id, expected_count, _path", SOURCES)
def test_internal_analytical_route_emits_score_events(
    source_id: str,
    expected_count: int,
    _path: str,
) -> None:
    context, observations, experiments, source_observations = _residue_context(
        source_id
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=False,
    )

    assert len(residuals) == expected_count
    assert all(
        (residual.numeric is None) != (residual.refusal is None)
        for residual in residuals
    )
    refusal_counts: dict[str, int] = {}
    for row in residuals:
        if row.refusal is not None:
            reason = row.refusal.reason.value
            refusal_counts[reason] = refusal_counts.get(reason, 0) + 1
    numeric_rows = [row for row in residuals if row.numeric is not None]
    if source_id == "ivanova-2018-spinel-rich-cai-evaporation":
        assert numeric_rows, "time-forward analytical route must score its supported cells"
        assert len(numeric_rows) == 12
        assert sum(row.refusal is not None for row in residuals) == 20
    else:
        assert not numeric_rows
        assert all(
            row.refusal is not None
            and row.refusal.reason.value == "unsupported"
            for row in residuals
        )
    warnings.warn(
        "INTERNAL_ANALYTICAL_CAPABILITY "
        + json.dumps(
            {
                "source_id": source_id,
                "scored_rows": len(residuals),
                "refusal_counts": refusal_counts,
                "numeric_rows": len(numeric_rows),
                "sample_detail": next(
                    (
                        str(row.refusal.detail)
                        for row in residuals
                        if row.refusal is not None
                    ),
                    None,
                ),
            },
            sort_keys=True,
        ),
        UserWarning,
        stacklevel=1,
    )
    if source_id == "ivanova-2018-spinel-rich-cai-evaporation":
        warnings.warn(
            "INTERNAL_ANALYTICAL_IVANOVA_TABLE "
            + json.dumps(
                [
                    {
                        "point": row.reference.rsplit("::point:", 1)[-1],
                        "time_min": str(
                            observations[row.reference]
                            .point_conditions["time_min"]
                            .state.value
                        ),
                        "oxide": observations[row.reference].identity.species.formula,
                        "observed_wt_pct": str(
                            observations[row.reference].value.point
                        ),
                        "predicted_wt_pct": format(
                            float(
                                observations[row.reference].value.point
                                + row.numeric.value
                            ),
                            ".6g",
                        ),
                        "residual": format(float(row.numeric.value), ".6g"),
                    }
                    for row in numeric_rows
                ],
                sort_keys=True,
            ),
            UserWarning,
            stacklevel=1,
        )
    if source_id == "zhang-2021-acsesc-si":
        mass_loss_observations = {
            observation_id: observation
            for observation_id, observation in source_observations.items()
            if observation.identity.quantity.is_value
            and observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        }
        mass_loss_context = ScoreContext(
            works=context.works,
            experiments=experiments,
            observations=mass_loss_observations,
            root=ROOT,
        )
        mass_loss_residuals, _mass_loss_candidates = score_store(
            mass_loss_context,
            engines=(Engine.INTERNAL_ANALYTICAL,),
            include_diagnostics=False,
        )
        assert len(mass_loss_residuals) == len(mass_loss_observations) == 12
        assert all(
            (row.numeric is None) != (row.refusal is None)
            for row in mass_loss_residuals
        )
        assert all(
            row.numeric is None
            and row.refusal is not None
            and row.refusal.reason.value == "attempted_unavailable"
            for row in mass_loss_residuals
        )
        refusal_counts = {}
        for row in mass_loss_residuals:
            if row.refusal is not None:
                reason = row.refusal.reason.value
                refusal_counts[reason] = refusal_counts.get(reason, 0) + 1
        warnings.warn(
            "ZHANG_INTERNAL_ANALYTICAL_MASS_LOSS "
            + json.dumps(
                {
                    "rows": len(mass_loss_residuals),
                    "numeric_rows": sum(
                        row.numeric is not None for row in mass_loss_residuals
                    ),
                    "refusal_counts": refusal_counts,
                    "sample_detail": next(
                        (
                            str(row.refusal.detail)
                            for row in mass_loss_residuals
                            if row.refusal is not None
                        ),
                        None,
                    ),
                },
                sort_keys=True,
            ),
            UserWarning,
            stacklevel=1,
        )


@pytest.mark.parametrize("source_id, _count, _path", SOURCES)
def test_time_forward_path_does_not_substitute_the_scored_residue_for_feed(
    source_id: str,
    _count: int,
    _path: str,
) -> None:
    context, observations, _experiments, _source_observations = _residue_context(
        source_id
    )
    reference = next(iter(observations.values()))
    experiment = context.experiments[reference.experiment_id]
    assert reference.identity.composition is not None
    assert reference.identity.composition.is_value
    sample = replace(experiment.sample, initial_composition=None)
    missing_feed = replace(experiment, sample=sample)
    assert missing_feed.sample.initial_composition is None
    with pytest.raises(ResidueInventoryRefusal) as refusal:
        predict_mass_loss_fraction(
            Engine.OPENIMCC.value,
            missing_feed,
            temperature_K=float(reference.identity.temperature_K.value),
            observation=reference,
        )
    assert refusal.value.reason == "initial_composition_not_stated"
