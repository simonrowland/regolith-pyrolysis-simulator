from __future__ import annotations

import json

import pytest

import simulator.optimize.doe as doe_module
from simulator.optimize.doe import (
    DEPENDENCY_FREE_LHC_SAMPLER,
    FIDELITY_CORRELATION_METRICS,
    SCIPY_SOBOL_SAMPLER,
    DoeSpec,
    FidelityCorrelationProtocol,
    FidelityCorrelationResult,
    sample_recipe_patches,
)
from simulator.optimize.recipe import KnobSpec, RecipeSchema


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
        assert set(patch.values) == {spec.path for spec in schema.allowlist}
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


def test_sampler_varies_every_allowlisted_knob_across_samples() -> None:
    for schema in (RecipeSchema(), _small_schema()):
        patches = sample_recipe_patches(
            schema,
            n_samples=46,
            seed=42,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
        )

        for spec in schema.allowlist:
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
