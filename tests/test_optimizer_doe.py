from __future__ import annotations

from dataclasses import replace
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
from simulator.optimize.recipe import (
    FURNACE_MAX_T_C_PATH,
    KnobSpec,
    RecipePatch,
    RecipeSchema,
    _default_setpoint_value,
)

C5_PO2_DEFAULT = tuple("campaigns.C5.pO2_mbar_default".split("."))
C5_PTOTAL_DEFAULT = tuple("campaigns.C5.p_total_mbar_default".split("."))


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
    c2a_pairs = schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2
    pressure_pairs = tuple(schema.PRESSURE_COUPLED_DEFAULT_PAIRS) + tuple(
        c2a_pairs.items()
    )
    for patch in patches:
        for po2_path, total_path in pressure_pairs:
            if po2_path not in patch.values or total_path not in patch.values:
                continue
            po2 = patch.values[po2_path]
            total = patch.values[total_path]
            if po2_path in c2a_pairs:
                mode_path = po2_path[:-1] + ("gas_cover_mode",)
                mode = patch.values.get(mode_path, "pn2_sweep")
                if mode == "pn2_sweep":
                    assert po2 < total, (
                        ".".join(po2_path),
                        po2,
                        ".".join(total_path),
                        total,
                        ".".join(mode_path),
                        mode,
                    )
                    continue
            assert po2 <= total, (
                ".".join(po2_path),
                po2,
                ".".join(total_path),
                total,
            )


def _single_knob_schema(path: tuple[str, ...]) -> RecipeSchema:
    base = RecipeSchema()
    return RecipeSchema(allowlist=(base.spec_for(path),))


def _schema_with_replaced_spec(
    schema: RecipeSchema,
    path: tuple[str, ...],
    **changes: object,
) -> RecipeSchema:
    return RecipeSchema(
        allowlist=tuple(
            replace(spec, **changes) if spec.path == path else spec
            for spec in schema.allowlist
        ),
        recipe_schema_version=schema.recipe_schema_version,
        allowlist_version=schema.allowlist_version,
    )


def test_sobol_sampler_is_deterministic_for_schema_n_and_seed() -> None:
    first = _canonical_patch_set(n_samples=16, seed=123)
    second = _canonical_patch_set(n_samples=16, seed=123)
    different_seed = _canonical_patch_set(n_samples=16, seed=124)

    assert first == second
    assert first != different_seed


def test_pressure_conditioned_index_sampling_is_seed_stable() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable for streaming index sampling")
    schema = RecipeSchema()

    first = tuple(
        sample_recipe_patch_at_index(
            schema,
            index=index,
            seed=17,
            sampler_name=SCIPY_SOBOL_SAMPLER,
        )
        for index in range(64)
    )
    second = tuple(
        sample_recipe_patch_at_index(
            schema,
            index=index,
            seed=17,
            sampler_name=SCIPY_SOBOL_SAMPLER,
        )
        for index in range(64)
    )

    assert tuple(patch.canonical_json() for patch in first) == tuple(
        patch.canonical_json() for patch in second
    )
    _assert_pressure_defaults_are_jointly_feasible(schema, first)


def test_pressure_conditioning_uses_default_when_only_one_side_is_sampled() -> None:
    po2_schema = _single_knob_schema(C5_PO2_DEFAULT)
    total_schema = _single_knob_schema(C5_PTOTAL_DEFAULT)
    fixed_total = float(_default_setpoint_value(C5_PTOTAL_DEFAULT))
    fixed_po2 = float(_default_setpoint_value(C5_PO2_DEFAULT))

    po2_patches = sample_recipe_patches(
        po2_schema,
        n_samples=64,
        seed=11,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )
    total_patches = sample_recipe_patches(
        total_schema,
        n_samples=64,
        seed=11,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )

    assert all(patch.values[C5_PO2_DEFAULT] <= fixed_total for patch in po2_patches)
    assert all(patch.values[C5_PTOTAL_DEFAULT] >= fixed_po2 for patch in total_patches)


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


def test_doe_spec_from_dict_rejects_allowlist_version_mismatch() -> None:
    base = _small_schema()
    old_schema = RecipeSchema(
        allowlist=base.allowlist,
        allowlist_version="allowlist-old",
    )
    new_schema = RecipeSchema(
        allowlist=base.allowlist,
        allowlist_version="allowlist-new",
    )
    payload = DoeSpec(schema=old_schema, n_samples=8, seed=321).to_dict()

    with pytest.raises(ValueError, match="allowlist_version"):
        DoeSpec.from_dict(payload, schema=new_schema)


def test_doe_spec_to_dict_always_writes_bounds_digest() -> None:
    schema = RecipeSchema()
    payload = DoeSpec(schema=schema, n_samples=8, seed=321).to_dict()

    assert "bounds_digest" in payload
    assert payload["bounds_digest"] == schema.bounds_digest


def test_doe_spec_from_dict_rejects_bounds_digest_mismatch() -> None:
    schema = RecipeSchema()
    changed = _schema_with_replaced_spec(schema, FURNACE_MAX_T_C_PATH, low=1300.0)
    payload = DoeSpec(schema=schema, n_samples=8, seed=321).to_dict()

    with pytest.raises(ValueError, match="bounds_digest"):
        DoeSpec.from_dict(payload, schema=changed)
    with pytest.raises(ValueError, match="bounds_digest"):
        DoeSpec.from_dict({**payload, "bounds_digest": None}, schema=schema)


def test_doe_spec_from_dict_warns_when_legacy_payload_lacks_bounds_digest() -> None:
    schema = RecipeSchema()
    payload = DoeSpec(schema=schema, n_samples=8, seed=321).to_dict()
    del payload["bounds_digest"]

    with pytest.warns(
        RuntimeWarning,
        match="bounds_digest.*replay invalidation caveat",
    ):
        restored = DoeSpec.from_dict(payload, schema=schema)

    assert restored.bounds_digest == schema.bounds_digest


def test_furnace_floor_bound_edit_moves_same_seed_samples() -> None:
    schema = RecipeSchema()
    changed = _schema_with_replaced_spec(schema, FURNACE_MAX_T_C_PATH, low=1300.0)
    before = sample_recipe_patches(
        schema,
        n_samples=8,
        seed=20260707,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )
    after = sample_recipe_patches(
        changed,
        n_samples=8,
        seed=20260707,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )

    assert changed.bounds_digest != schema.bounds_digest
    assert tuple(patch.values[FURNACE_MAX_T_C_PATH] for patch in after) != tuple(
        patch.values[FURNACE_MAX_T_C_PATH] for patch in before
    )


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
_RAIL_FLOAT = ("rail", "float")
_RAIL_INT = ("rail", "int")


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


def _rail_schema(path: tuple[str, ...], kind: str) -> RecipeSchema:
    return RecipeSchema(allowlist=(KnobSpec(path=path, kind=kind, low=0, high=100),))


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


def test_anchored_float_near_upper_bound_remaps_without_rail_pileup() -> None:
    schema = _rail_schema(_RAIL_FLOAT, "float")
    patches = sample_recipe_patches(
        schema,
        n_samples=10_000,
        seed=144,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=RecipePatch({_RAIL_FLOAT: 95.0}),
        delta_fraction=0.15,
    )

    values = [patch.values[_RAIL_FLOAT] for patch in patches]
    assert min(values) >= 80.0
    assert max(values) <= 100.0
    assert values.count(100.0) == 0
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    assert max(counts.values()) / len(values) <= 0.001


def test_anchored_int_near_upper_bound_uses_fair_clipped_buckets() -> None:
    schema = _rail_schema(_RAIL_INT, "int")
    patches = sample_recipe_patches(
        schema,
        n_samples=10_000,
        seed=144,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        anchor=RecipePatch({_RAIL_INT: 95}),
        delta_fraction=0.15,
    )

    values = [patch.values[_RAIL_INT] for patch in patches]
    assert min(values) == 80
    assert max(values) == 100
    counts: dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    assert set(counts) == set(range(80, 101))
    assert max(counts.values()) / len(values) <= 0.06


def test_anchored_interior_unit_mapping_matches_legacy_formula() -> None:
    float_spec = KnobSpec(path=_RAIL_FLOAT, kind="float", low=0, high=100)
    int_spec = KnobSpec(path=_RAIL_INT, kind="int", low=0, high=100)
    center = 50
    delta = 0.15

    for unit in (0.0, 0.125, 0.5, 0.875, math.nextafter(1.0, 0.0)):
        legacy_value = center + (2.0 * unit - 1.0) * 15.0
        assert doe_module._map_unit_value_anchored(
            float_spec, unit, float(center), delta
        ) == pytest.approx(legacy_value)
        assert doe_module._map_unit_value_anchored(
            int_spec, unit, center, delta
        ) == int(round(legacy_value))


def test_anchored_interior_dependency_free_lhc_canonical_json_matches_legacy() -> None:
    schema = _anchored_schema()

    # Recorded from the legacy interior-anchor expression:
    # center + (2 * unit - 1) * half_width.
    assert tuple(
        patch.canonical_json()
        for patch in sample_recipe_patches(
            schema,
            n_samples=8,
            seed=144,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=_anchor(),
            delta_fraction=0.15,
        )
    ) == (
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":453.40763271253763},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":36}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":517.3343123700043},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":29}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":543.0543134472375},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":34}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":419.2745649641729},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":24}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":577.4789490504673},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":27}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":466.4803891941184},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":32}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":364.7420667677326},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":26}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":613.0671952710939},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":31}]',
    )


def test_anchored_interior_scipy_sobol_canonical_json_matches_legacy() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")

    schema = _anchored_schema()

    # Recorded from the legacy interior-anchor expression:
    # center + (2 * unit - 1) * half_width.
    assert tuple(
        patch.canonical_json()
        for patch in sample_recipe_patches(
            schema,
            n_samples=8,
            seed=144,
            sampler_name=SCIPY_SOBOL_SAMPLER,
            anchor=_anchor(),
            delta_fraction=0.15,
        )
    ) == (
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":589.6556176226586},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":26}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":477.6095311231911},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":31}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":415.90245443955064},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":27}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":512.9525380562991},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":35}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":554.9749413356185},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":30}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":372.78716417588294},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":35}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":450.8525401148945},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":24}]',
        '[{"path":["campaigns","C0","mode"],"value":"nominal"},{"path":["campaigns","C0","temp_range_C"],"value":617.4994091466069},{"path":["campaigns","C3","endpoint","hold_time_min"],"value":33}]',
    )


def test_anchored_near_rail_sampling_respects_bounds() -> None:
    for path, kind, center in (
        (_RAIL_FLOAT, "float", 5.0),
        (_RAIL_FLOAT, "float", 95.0),
        (_RAIL_INT, "int", 5),
        (_RAIL_INT, "int", 95),
    ):
        schema = _rail_schema(path, kind)
        patches = sample_recipe_patches(
            schema,
            n_samples=256,
            seed=144,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=RecipePatch({path: center}),
            delta_fraction=0.15,
        )
        values = [patch.values[path] for patch in patches]
        assert all(0 <= value <= 100 for value in values)


def test_anchored_int_clipped_endpoint_buckets_are_reachable() -> None:
    spec = KnobSpec(path=_RAIL_INT, kind="int", low=0, high=100)

    assert doe_module._map_unit_value_anchored(spec, 0.0, 95, 0.15) == 80
    assert (
        doe_module._map_unit_value_anchored(
            spec, math.nextafter(1.0, 0.0), 95, 0.15
        )
        == 100
    )


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
