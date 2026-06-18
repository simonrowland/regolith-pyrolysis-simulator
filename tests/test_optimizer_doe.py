from __future__ import annotations

import json
import math

import pytest

import simulator.optimize.doe as doe_module
from simulator.optimize.doe import (
    DEPENDENCY_FREE_LHC_SAMPLER,
    FIDELITY_CORRELATION_METRICS,
    SCIPY_SOBOL_SAMPLER,
    DoeSpec,
    FidelityCorrelationProtocol,
    FidelityCorrelationResult,
    PHASE_ORDER_OXIDIZE_FIRST,
    PHASE_ORDER_VACUUM_FIRST,
    phase_order_grid,
    sample_recipe_patch_at_index,
    sample_recipe_patches,
)
from simulator.optimize.recipe import KnobSpec, RecipePatch, RecipeSchema


def _canonical_patch_set(
    n_samples: int, seed: int, sampler_name: str | None = None
) -> tuple[str, ...]:
    schema = RecipeSchema()
    return tuple(
        patch.canonical_json()
        for patch in sample_recipe_patches(
            schema, n_samples=n_samples, seed=seed, sampler_name=sampler_name
        )
    )


def _small_schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(
                path=("campaigns", "C0", "temp_range_C"),
                kind="float",
                low=20,
                high=950,
            ),
            KnobSpec(
                path=("campaigns", "C3", "endpoint", "hold_time_min"),
                kind="int",
                low=15,
                high=60,
            ),
            KnobSpec(
                path=("campaigns", "C0", "mode"),
                kind="categorical",
                choices=("conservative", "nominal", "aggressive"),
            ),
        )
    )


def _assert_patch_values_match_specs(schema: RecipeSchema, patches: tuple) -> None:
    for patch in patches:
        assert set(patch.values) == {spec.path for spec in schema.search_allowlist}
        patch.validated(schema)
        for path, value in patch.values.items():
            assert not schema.is_forbidden(path)
            spec = schema.spec_for(path)
            if spec.kind == "categorical":
                assert spec.choices is not None
                assert value in spec.choices
            elif spec.kind == "int":
                assert isinstance(value, int)
                assert spec.low <= value <= spec.high
            elif spec.kind == "float":
                assert isinstance(value, float)
                assert spec.low <= value <= spec.high
            else:
                raise AssertionError(f"unsupported knob kind {spec.kind!r}")


def _assert_pressure_defaults_are_jointly_feasible(
    schema: RecipeSchema,
    patches: tuple[RecipePatch, ...],
) -> None:
    for patch in patches:
        for po2_path, total_path in schema.PRESSURE_COUPLED_DEFAULT_PAIRS:
            if po2_path not in patch.values or total_path not in patch.values:
                continue
            assert patch.values[po2_path] <= patch.values[total_path], (
                ".".join(po2_path),
                patch.values[po2_path],
                ".".join(total_path),
                patch.values[total_path],
            )


def test_sobol_sampler_is_deterministic_for_schema_n_and_seed() -> None:
    first = _canonical_patch_set(n_samples=16, seed=123)
    second = _canonical_patch_set(n_samples=16, seed=123)
    different_seed = _canonical_patch_set(n_samples=16, seed=124)

    assert first == second
    assert first != different_seed


def test_requested_scipy_sobol_sampler_is_deterministic_when_available() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")

    first = _canonical_patch_set(
        n_samples=16, seed=123, sampler_name=SCIPY_SOBOL_SAMPLER
    )
    second = _canonical_patch_set(
        n_samples=16, seed=123, sampler_name=SCIPY_SOBOL_SAMPLER
    )

    assert first == second


def test_requested_sampler_name_replays_serialized_doe_spec() -> None:
    schema = RecipeSchema()
    doe = DoeSpec(
        schema=schema,
        n_samples=8,
        seed=321,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )
    restored = DoeSpec.from_dict(doe.to_dict(), schema=schema)

    first = _canonical_patch_set(
        n_samples=restored.n_samples,
        seed=restored.seed,
        sampler_name=restored.sampler_name,
    )
    second = _canonical_patch_set(
        n_samples=restored.n_samples,
        seed=restored.seed,
        sampler_name=restored.sampler_name,
    )

    assert first == second


def test_unknown_sampler_name_raises_without_fallback() -> None:
    with pytest.raises(ValueError, match="unsupported DOE sampler"):
        sample_recipe_patches(
            RecipeSchema(), n_samples=4, seed=1, sampler_name="mystery-sampler"
        )


def test_unavailable_requested_scipy_sampler_raises_without_lhc_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doe_module, "_scipy_sobol_available", lambda: False)

    with pytest.raises(RuntimeError, match="scipy-sobol sampler requested"):
        sample_recipe_patches(
            RecipeSchema(), n_samples=4, seed=1, sampler_name=SCIPY_SOBOL_SAMPLER
        )


def test_dependency_free_lhc_canonical_json_vector_is_pinned() -> None:
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=("campaigns", "C0", "temp_range_C"),
                kind="float",
                low=20,
                high=950,
            ),
            KnobSpec(
                path=("campaigns", "C3", "endpoint", "hold_time_min"),
                kind="int",
                low=15,
                high=60,
            ),
        )
    )

    # Pin only dependency-free LHC; scipy-Sobol vectors are scipy-version fragile.
    assert tuple(
        patch.canonical_json()
        for patch in sample_recipe_patches(
            schema,
            n_samples=3,
            seed=8675309,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        )
    ) == (
        '[{"path":["campaigns","C0","temp_range_C"],"value":798.1766251556933},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":55}]',
        '[{"path":["campaigns","C0","temp_range_C"],"value":225.76036480263147},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":20}]',
        '[{"path":["campaigns","C0","temp_range_C"],"value":596.8221746252607},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":45}]',
    )


def test_sampler_outputs_all_allowlist_paths_in_bounds_and_no_forbidden_paths() -> None:
    for schema in (RecipeSchema(), _small_schema()):
        patches = sample_recipe_patches(
            schema,
            n_samples=8,
            seed=7,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        )

        assert len(patches) == 8
        _assert_patch_values_match_specs(schema, patches)


def test_sampler_couples_pressure_defaults_to_physical_partial_pressure_limit() -> None:
    schema = RecipeSchema()
    samplers = [DEPENDENCY_FREE_LHC_SAMPLER]
    if doe_module._scipy_sobol_available():
        samplers.append(SCIPY_SOBOL_SAMPLER)

    for sampler_name in samplers:
        patches = sample_recipe_patches(
            schema,
            n_samples=32,
            seed=20260610,
            sampler_name=sampler_name,
        )
        _assert_pressure_defaults_are_jointly_feasible(schema, patches)

    if doe_module._scipy_sobol_available():
        streaming = tuple(
            sample_recipe_patch_at_index(
                schema,
                index=sequence,
                seed=20260610,
                sampler_name=SCIPY_SOBOL_SAMPLER,
            )
            for sequence in range(16)
        )
        _assert_pressure_defaults_are_jointly_feasible(schema, streaming)


def test_sampler_varies_every_allowlisted_knob_across_samples() -> None:
    for schema in (RecipeSchema(), _small_schema()):
        patches = sample_recipe_patches(
            schema,
            n_samples=46,
            seed=42,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        )

        for spec in schema.search_allowlist:
            values = {patch.values[spec.path] for patch in patches}
            assert len(values) > 1, ".".join(spec.path)
            if spec.kind == "int":
                assert min(values) == spec.low
                assert max(values) == spec.high


def test_int_mapping_reaches_declared_bounds() -> None:
    spec = KnobSpec(
        path=("campaigns", "C3", "endpoint", "hold_time_min"),
        kind="int",
        low=15,
        high=60,
    )

    assert doe_module._map_unit_value(spec, 0.0) == 15
    assert doe_module._map_unit_value(spec, 1.0 - 1e-12) == 60


@pytest.mark.parametrize("n_samples", (3, 10))
@pytest.mark.parametrize(
    "sampler_name", (DEPENDENCY_FREE_LHC_SAMPLER, SCIPY_SOBOL_SAMPLER)
)
def test_non_power_of_two_sample_counts_are_explicitly_truncated_and_in_bounds(
    n_samples: int, sampler_name: str
) -> None:
    if sampler_name == SCIPY_SOBOL_SAMPLER and not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")
    schema = _small_schema()

    first = sample_recipe_patches(
        schema, n_samples=n_samples, seed=456, sampler_name=sampler_name
    )
    second = sample_recipe_patches(
        schema, n_samples=n_samples, seed=456, sampler_name=sampler_name
    )

    assert len(first) == n_samples
    assert len(second) == n_samples
    assert tuple(patch.canonical_json() for patch in first) == tuple(
        patch.canonical_json() for patch in second
    )
    _assert_patch_values_match_specs(schema, first)


def test_protocol_structures_round_trip_without_execution_hooks() -> None:
    schema = RecipeSchema()
    doe = DoeSpec(schema=schema, n_samples=16, seed=99)
    protocol = FidelityCorrelationProtocol(
        doe=doe,
        objective_names=("oxygen_recovery", "glass_yield"),
        top_k_values=(3, 5),
    )
    result = FidelityCorrelationResult(
        protocol=protocol,
        spearman_by_objective={"oxygen_recovery": 0.91, "glass_yield": None},
        feasible_infeasible_agreement=0.875,
        top_k_recall={3: 0.67, 5: 0.8},
        n_samples_compared=16,
        notes=("fixture only",),
    )

    assert protocol.metrics == FIDELITY_CORRELATION_METRICS
    assert set(protocol.metrics) == {
        "spearman_rank_correlation",
        "feasible_infeasible_agreement",
        "top_k_recall",
    }

    payload = json.loads(json.dumps(result.to_dict(), sort_keys=True))
    restored = FidelityCorrelationResult.from_dict(payload, schema=schema)

    assert restored.to_dict() == result.to_dict()


def test_fidelity_correlation_result_copies_input_mappings() -> None:
    schema = RecipeSchema()
    protocol = FidelityCorrelationProtocol(
        doe=DoeSpec(schema=schema, n_samples=4, seed=99),
        objective_names=("oxygen_recovery",),
        top_k_values=(3,),
    )
    spearman = {"oxygen_recovery": 0.91}
    top_k = {3: 0.67}
    result = FidelityCorrelationResult(
        protocol=protocol,
        spearman_by_objective=spearman,
        top_k_recall=top_k,
        n_samples_compared=4,
    )

    before = result.to_dict()
    spearman["oxygen_recovery"] = -1.0
    top_k[3] = -1.0

    assert result.to_dict() == before
    with pytest.raises(TypeError):
        result.spearman_by_objective["oxygen_recovery"] = -1.0


def test_phase_order_grid_covers_stage0_order_count_and_dwell_skeletons() -> None:
    grid = phase_order_grid()

    assert len(grid) == 8
    assert {
        (item.order, item.phase_count, item.dwell_h)
        for item in grid
    } == {
        (PHASE_ORDER_VACUUM_FIRST, 2, 0.5),
        (PHASE_ORDER_VACUUM_FIRST, 2, 1.0),
        (PHASE_ORDER_VACUUM_FIRST, 3, 0.5),
        (PHASE_ORDER_VACUUM_FIRST, 3, 1.0),
        (PHASE_ORDER_OXIDIZE_FIRST, 2, 0.5),
        (PHASE_ORDER_OXIDIZE_FIRST, 2, 1.0),
        (PHASE_ORDER_OXIDIZE_FIRST, 3, 0.5),
        (PHASE_ORDER_OXIDIZE_FIRST, 3, 1.0),
    }
    vacuum_first = next(
        item for item in grid
        if item.order == PHASE_ORDER_VACUUM_FIRST and item.phase_count == 3
    )
    oxidize_first = next(
        item for item in grid
        if item.order == PHASE_ORDER_OXIDIZE_FIRST and item.phase_count == 3
    )
    assert vacuum_first.sequence == ("vacuum", "oxidize", "vacuum")
    assert oxidize_first.sequence == ("oxidize", "vacuum", "oxidize")
    assert vacuum_first.continuous_knob_paths
    assert "continuous_knob_paths" in vacuum_first.to_dict()


# --- anchored (neighborhood) sampling -------------------------------------

_TEMP = ("campaigns", "C0", "temp_range_C")
_HOLD = ("campaigns", "C3", "endpoint", "hold_time_min")
_MODE = ("campaigns", "C0", "mode")
_C3_PO2_DEFAULT = ("campaigns", "C3", "pO2_mbar_default")
_C3_PTOTAL_DEFAULT = ("campaigns", "C3", "p_total_mbar_default")


def _anchored_schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(path=_TEMP, kind="float", low=20, high=950),
            KnobSpec(path=_HOLD, kind="int", low=15, high=60),
            KnobSpec(
                path=_MODE,
                kind="categorical",
                choices=("conservative", "nominal", "aggressive"),
            ),
        )
    )


def _anchor(temp: float = 500.0, hold: int = 30, mode: str = "nominal") -> RecipePatch:
    return RecipePatch({_TEMP: temp, _HOLD: hold, _MODE: mode})


def _anchored_pressure_schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(path=_C3_PO2_DEFAULT, kind="float", low=0.5, high=1.5),
            KnobSpec(path=_C3_PTOTAL_DEFAULT, kind="float", low=0.5, high=1.5),
        )
    )


def _pressure_anchor(po2: float = 1.0, total: float = 1.0) -> RecipePatch:
    return RecipePatch({_C3_PO2_DEFAULT: po2, _C3_PTOTAL_DEFAULT: total})


def _assert_anchored_c3_pressure_is_coupled(
    patches: tuple[RecipePatch, ...], delta_fraction: float
) -> None:
    low = 1.0 - delta_fraction
    high = 1.0 + delta_fraction
    for patch in patches:
        po2 = patch.values[_C3_PO2_DEFAULT]
        total = patch.values[_C3_PTOTAL_DEFAULT]
        assert low <= po2 <= high
        assert po2 <= total


def test_doe_spec_round_trips_anchor_and_delta_fraction() -> None:
    schema = _anchored_schema()
    anchor = _anchor()
    doe = DoeSpec(
        schema=schema,
        n_samples=8,
        seed=22,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
        delta_fraction=0.2,
    )

    payload = json.loads(json.dumps(doe.to_dict(), sort_keys=True))
    assert payload["delta_fraction"] == 0.2
    assert payload["anchor"] == [
        {"path": ["campaigns", "C0", "mode"], "value": "nominal"},
        {"path": ["campaigns", "C0", "temp_range_C"], "value": 500.0},
        {"path": ["campaigns", "C3", "endpoint", "hold_time_min"], "value": 30},
    ]

    restored = DoeSpec.from_dict(payload, schema=schema)
    assert restored.delta_fraction == pytest.approx(0.2)
    assert restored.anchor is not None
    assert dict(restored.anchor.values) == dict(anchor.values)
    assert restored.to_dict() == doe.to_dict()


def _assert_within_neighborhood(
    schema: RecipeSchema, patches: tuple, anchor: RecipePatch, delta_fraction: float
) -> None:
    for patch in patches:
        patch.validated(schema)
        for spec in schema.search_allowlist:
            value = patch.values[spec.path]
            center = anchor.values[spec.path]
            if spec.kind == "categorical":
                assert value == center
                continue
            low, high = float(spec.low), float(spec.high)
            half = delta_fraction * (high - low)
            # within the anchor neighborhood (small tolerance for int rounding)
            tol = 0.5 if spec.kind == "int" else 1e-9
            assert float(center) - half - tol <= float(value) <= float(center) + half + tol
            # and always inside the schema bounds
            assert low <= float(value) <= high


def test_anchored_sampling_count_matches_n_samples() -> None:
    schema = _anchored_schema()
    patches = sample_recipe_patches(
        schema,
        n_samples=20,
        seed=5,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=_anchor(),
        delta_fraction=0.15,
    )
    assert len(patches) == 20


def test_anchored_sampling_stays_in_neighborhood_and_bounds() -> None:
    schema = _anchored_schema()
    anchor = _anchor()
    delta = 0.15
    patches = sample_recipe_patches(
        schema,
        n_samples=64,
        seed=5,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
        delta_fraction=delta,
    )
    _assert_within_neighborhood(schema, patches, anchor, delta)
    # the anchor genuinely narrows the float spread vs the full schema range
    temps = [p.values[_TEMP] for p in patches]
    assert max(temps) - min(temps) < (950 - 20)


def test_anchored_sampling_clamps_when_center_near_bound() -> None:
    schema = _anchored_schema()
    # centers sit inside but within delta of the upper bound -> must clamp
    anchor = _anchor(temp=940.0, hold=59)
    delta = 0.15
    patches = sample_recipe_patches(
        schema,
        n_samples=64,
        seed=9,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
        delta_fraction=delta,
    )
    _assert_within_neighborhood(schema, patches, anchor, delta)
    assert max(p.values[_TEMP] for p in patches) <= 950.0
    assert max(p.values[_HOLD] for p in patches) <= 60


def test_anchored_pressure_coupling_stays_in_neighborhood_and_under_total() -> None:
    schema = _anchored_pressure_schema()
    delta = 0.05
    patches = sample_recipe_patches(
        schema,
        n_samples=64,
        seed=20260611,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=_pressure_anchor(),
        delta_fraction=delta,
    )

    _assert_anchored_c3_pressure_is_coupled(patches, delta)


def test_streaming_anchored_pressure_coupling_stays_in_neighborhood_and_under_total() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy Sobol sampler unavailable")
    schema = _anchored_pressure_schema()
    delta = 0.05
    patches = tuple(
        sample_recipe_patch_at_index(
            schema,
            index=sequence,
            seed=20260611,
            sampler_name=SCIPY_SOBOL_SAMPLER,
            anchor=_pressure_anchor(),
            delta_fraction=delta,
        )
        for sequence in range(32)
    )

    _assert_anchored_c3_pressure_is_coupled(patches, delta)


def test_anchored_sampling_is_deterministic_for_same_seed() -> None:
    schema = _anchored_schema()
    anchor = _anchor()
    kwargs = dict(
        n_samples=32,
        seed=77,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
        delta_fraction=0.2,
    )
    first = sample_recipe_patches(schema, **kwargs)
    second = sample_recipe_patches(schema, **kwargs)
    assert tuple(p.canonical_json() for p in first) == tuple(
        p.canonical_json() for p in second
    )


def test_anchored_sampling_differs_from_full_range() -> None:
    schema = _anchored_schema()
    full = sample_recipe_patches(
        schema, n_samples=32, seed=77, sampler_name=DEPENDENCY_FREE_LHC_SAMPLER
    )
    anchored = sample_recipe_patches(
        schema,
        n_samples=32,
        seed=77,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=_anchor(),
        delta_fraction=0.15,
    )
    assert tuple(p.canonical_json() for p in full) != tuple(
        p.canonical_json() for p in anchored
    )


def test_anchored_sampling_default_delta_fraction_is_used() -> None:
    schema = _anchored_schema()
    anchor = _anchor()
    explicit = sample_recipe_patches(
        schema,
        n_samples=16,
        seed=1,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
        delta_fraction=0.15,
    )
    implicit = sample_recipe_patches(
        schema,
        n_samples=16,
        seed=1,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=anchor,
    )
    assert tuple(p.canonical_json() for p in explicit) == tuple(
        p.canonical_json() for p in implicit
    )


def test_anchored_patch_at_index_matches_batch_row() -> None:
    schema = _anchored_schema()
    anchor = _anchor()
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable for streaming index sampling")
    batch = sample_recipe_patches(
        schema,
        n_samples=8,
        seed=4,
        sampler_name=SCIPY_SOBOL_SAMPLER,
        anchor=anchor,
        delta_fraction=0.1,
    )
    at_zero = sample_recipe_patch_at_index(
        schema,
        index=0,
        seed=4,
        sampler_name=SCIPY_SOBOL_SAMPLER,
        anchor=anchor,
        delta_fraction=0.1,
    )
    assert at_zero.canonical_json() == batch[0].canonical_json()
    _assert_within_neighborhood(schema, (at_zero,), anchor, 0.1)


def test_anchored_lhc_patch_at_index_fails_with_specific_error() -> None:
    schema = _anchored_schema()
    with pytest.raises(
        ValueError,
        match="anchored sample_recipe_patch_at_index.*dependency-free-lhc.*sample_recipe_patches",
    ):
        sample_recipe_patch_at_index(
            schema,
            index=0,
            seed=4,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(),
            delta_fraction=0.1,
        )


@pytest.mark.parametrize(
    "delta_fraction",
    [
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="inf"),
        pytest.param(0.0, id="zero"),
        pytest.param(1.1, id="above_one"),
        pytest.param(-0.1, id="negative"),
        pytest.param("foo", id="string"),
        pytest.param(None, id="none"),
    ],
)
def test_delta_fraction_rejects_bad_values_without_anchor(delta_fraction: object) -> None:
    schema = _anchored_schema()

    with pytest.raises(ValueError, match="delta_fraction"):
        DoeSpec(
            schema=schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=None,
            delta_fraction=delta_fraction,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="delta_fraction"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=None,
            delta_fraction=delta_fraction,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="delta_fraction"):
        sample_recipe_patch_at_index(
            schema,
            index=0,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=None,
            delta_fraction=delta_fraction,  # type: ignore[arg-type]
        )


def test_anchored_sampling_rejects_nonpositive_delta_fraction() -> None:
    schema = _anchored_schema()
    with pytest.raises(ValueError, match="delta_fraction"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(),
            delta_fraction=0.0,
        )


def test_anchored_sampling_rejects_delta_fraction_above_one() -> None:
    schema = _anchored_schema()
    with pytest.raises(ValueError, match="delta_fraction"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(),
            delta_fraction=1.5,
        )


def test_anchored_sampling_rejects_anchor_missing_a_sampled_knob() -> None:
    schema = _anchored_schema()
    partial = RecipePatch({_TEMP: 500.0, _MODE: "nominal"})  # missing hold_time_min
    with pytest.raises(ValueError, match="missing sampled knob"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=partial,
        )


def test_anchored_sampling_rejects_anchor_with_stray_path() -> None:
    schema = _anchored_schema()
    stray = RecipePatch(
        {
            _TEMP: 500.0,
            _HOLD: 30,
            _MODE: "nominal",
            ("campaigns", "C9", "unknown"): 1.0,
        }
    )
    with pytest.raises(ValueError, match="not in the sampled set"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=stray,
        )


def test_anchored_sampling_rejects_bad_categorical_anchor_value() -> None:
    schema = _anchored_schema()
    with pytest.raises(ValueError, match="not in choices"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(mode="turbo"),
        )


def test_anchored_sampling_rejects_float_anchor_for_int_knob() -> None:
    schema = _anchored_schema()
    with pytest.raises(ValueError, match="must be int"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(hold=30.0),  # type: ignore[arg-type]
        )


def test_anchored_sampling_rejects_anchor_value_out_of_bounds() -> None:
    schema = _anchored_schema()
    over = _anchor(temp=2000.0)  # above the 950 upper bound
    with pytest.raises(ValueError, match="outside bounds"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=over,
        )


def test_sampled_zero_range_numeric_knob_raises() -> None:
    schema = RecipeSchema(
        allowlist=(KnobSpec(path=_TEMP, kind="float", low=500.0, high=500.0),)
    )
    with pytest.raises(ValueError, match="invalid numeric bounds"):
        sample_recipe_patches(
            schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        )


def test_doe_spec_validates_anchor_eagerly() -> None:
    schema = _anchored_schema()
    # valid anchor on the DoeSpec is accepted
    DoeSpec(
        schema=schema,
        n_samples=8,
        seed=1,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=_anchor(),
        delta_fraction=0.15,
    )
    # bad delta_fraction on the spec raises at construction (fail loud)
    with pytest.raises(ValueError, match="delta_fraction"):
        DoeSpec(
            schema=schema,
            n_samples=8,
            seed=1,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(),
            delta_fraction=-0.1,
        )
