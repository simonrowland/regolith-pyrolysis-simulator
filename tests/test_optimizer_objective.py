from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    composition_targets_require_coating,
    composition_target_eval_metadata,
    compute_objectives,
    dominates,
    objective_importance_evidence,
    pareto_front,
    product_summary,
)


DEFINITIONS = (
    ObjectiveDefinition("oxygen_kg", "maximize", "kg", ordinal=0),
    ObjectiveDefinition("energy_kWh", "minimize", "kWh", ordinal=1),
)


def test_dominates_requires_strict_improvement() -> None:
    assert dominates(
        {"oxygen_kg": 2.0, "energy_kWh": 4.0},
        {"oxygen_kg": 1.0, "energy_kWh": 5.0},
        DEFINITIONS,
    )


def test_equal_objective_vectors_do_not_dominate() -> None:
    left = {"oxygen_kg": 1.0, "energy_kWh": 5.0}
    right = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert not dominates(left, right, DEFINITIONS)
    assert not dominates(right, left, DEFINITIONS)


def test_dominates_honors_mixed_minimize_maximize_directions() -> None:
    lower_energy = {"oxygen_kg": 1.0, "energy_kWh": 4.0}
    higher_energy = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert dominates(lower_energy, higher_energy, DEFINITIONS)
    assert not dominates(higher_energy, lower_energy, DEFINITIONS)


def test_missing_or_nonfinite_objectives_raise() -> None:
    with pytest.raises(ObjectiveComputationError, match="energy_kWh.*missing"):
        dominates({"oxygen_kg": 1.0}, {"oxygen_kg": 1.0, "energy_kWh": 5.0}, DEFINITIONS)

    with pytest.raises(ObjectiveComputationError, match="oxygen_kg is non-finite"):
        dominates(
            {"oxygen_kg": math.nan, "energy_kWh": 5.0},
            {"oxygen_kg": 1.0, "energy_kWh": 5.0},
            DEFINITIONS,
        )


def test_incomplete_objective_importance_evidence_raises_insufficient_evidence() -> None:
    profile = {
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
            }
        ]
    }

    with pytest.raises(
        ObjectiveProfileError,
        match="insufficient-evidence: objectives\\[0\\] 'oxygen_kg' missing rationale",
    ):
        objective_importance_evidence(profile)


def test_pareto_front_preserves_stable_non_dominated_order() -> None:
    items = (
        {"id": "d", "objectives": {"oxygen_kg": 1.5, "energy_kWh": 3.0}},
        {"id": "a", "objectives": {"oxygen_kg": 1.0, "energy_kWh": 5.0}},
        {"id": "c", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 4.0}},
        {"id": "b", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 5.0}},
    )

    front = pareto_front(
        items,
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == ["d", "c"]


class _FakeLedger:
    def kg_by_account(self, account: str) -> dict[str, float]:
        if account == "terminal.offgas":
            return {"H2O": 5.0}
        return {}


class _FakeProductSim:
    atom_ledger = _FakeLedger()

    def __init__(self) -> None:
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={"SiO": 40.0}),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key="lunar_mare_low_ti",
            batch_mass_kg=1000.0,
            additives_kg={"CaO": 1.5},
            snapshots=(
                SimpleNamespace(
                    mass_in_kg=1001.5,
                    mass_out_kg=1001.5,
                    mass_balance_error_pct=0.0,
                ),
            ),
        )

    def product_ledger(self) -> dict[str, float]:
        return {"Fe": 50.0, "SiO": 40.0, "H2O": 5.0}

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {"Al2O3": 80.0}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {
            "stored": 20.0,
            "vented": 0.0,
            "total": 20.0,
            "mre_anode_stored": 20.0,
        }


class _FakeUnclassifiedProductSim(_FakeProductSim):
    def product_ledger(self) -> dict[str, float]:
        ledger = dict(super().product_ledger())
        ledger["MysteryOxide"] = 7.0
        return ledger


def test_product_summary_includes_input_output_yield_table_and_mass_closure() -> None:
    summary = product_summary(
        SimpleNamespace(simulator=_FakeProductSim(), trace=None),
        {"feedstock": "lunar_mare_low_ti"},
    )

    table = summary["product_yield_table"]
    outputs = {row["id"]: row for row in table["outputs"]}

    assert [row["id"] for row in table["inputs"]] == ["feedstock", "additive:CaO"]
    assert set(outputs) == {
        "ingots_metals",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic_rump",
    }
    assert outputs["ingots_metals"]["kg"] == pytest.approx(50.0)
    assert outputs["glass"]["yield_pct"] == pytest.approx(40.0 / 1001.5 * 100.0)
    assert outputs["oxygen"]["partition_kg"]["mre_anode_stored"] == pytest.approx(20.0)
    assert outputs["captured_volatiles"]["kg_by_species"] == {"H2O": 5.0}
    assert table["mass_closure"]["status"] == "closed"
    assert table["mass_closure"]["tolerance_pct"] == pytest.approx(5e-12)


def test_unclassified_product_mass_makes_yield_table_inconclusive() -> None:
    summary = product_summary(
        SimpleNamespace(simulator=_FakeUnclassifiedProductSim(), trace=None),
        {"feedstock": "lunar_mare_low_ti"},
    )

    table = summary["product_yield_table"]

    assert table["mass_closure"]["status"] == "closed"
    assert table["status"] == "inconclusive"
    assert table["unclassified_product_mass"]["total_kg"] == pytest.approx(7.0)
    assert table["unclassified_product_mass"]["kg_by_species"]["MysteryOxide"] == (
        pytest.approx(7.0)
    )
    diagnostics = {row["id"]: row for row in table["diagnostics"]}
    assert diagnostics["unclassified_product_mass"]["kind"] == "diagnostic"
    assert "unclassified_product_mass" not in {
        row["id"] for row in table["outputs"]
    }


class _CompositionLedger:
    registry = {}

    def __init__(
        self,
        cleaned_melt: dict[str, float],
        extra_accounts: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._balances = {"process.cleaned_melt": cleaned_melt}
        self._balances.update(extra_accounts or {})

    def mol_by_account(self, account: str | None = None):
        if account is None:
            return {key: dict(value) for key, value in self._balances.items()}
        return dict(self._balances.get(account, {}))


class _CompositionSim:
    def __init__(
        self,
        cleaned_melt: dict[str, float],
        stage3_kg: dict[str, float],
        *,
        product_kg: dict[str, float] | None = None,
        extra_accounts: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.atom_ledger = _CompositionLedger(cleaned_melt, extra_accounts)
        self._product_kg = dict(product_kg or {})
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg=stage3_kg),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key="lunar_mare_low_ti",
            batch_mass_kg=1000.0,
            products_kg={},
            oxygen_stored_kg=0.0,
            oxygen_vented_kg=0.0,
            energy_total_kWh=1.0,
            total_hours=1,
        )
        self.melt = SimpleNamespace(hour=1)
        self.energy_cumulative_kWh = 1.0

    def product_ledger(self) -> dict[str, float]:
        return dict(self._product_kg)

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 0.0, "vented": 0.0, "total": 0.0}


def test_composition_target_uses_declared_pool_for_hard_window() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 10.0},
            stage3_kg={"SiO2": 100.0},
        ),
        trace=None,
    )

    captured = compute_objectives(
        _composition_score_profile("captured_stage_3_silica"),
        run,
    )
    residual = compute_objectives(
        _composition_score_profile("residual_rump_at_stop"),
        run,
    )

    assert captured.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert residual.as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_composition_target_hard_window_passes_or_fails() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
            "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
        },
    )
    failing = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
    )

    assert compute_objectives(profile, run).as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert compute_objectives(failing, run).as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_composition_target_hard_window_miss_zeroes_extraction_branch() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides={"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 0.6, "composition": 0.4},
    )

    assert compute_objectives(profile, run).as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_residual_rump_pool_missing_data_does_not_alias_terminal_trace() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(cleaned_melt={}, stage3_kg={}),
        trace=SimpleNamespace(
            rump_terminal={"status": "earned"},
            terminal_rump_by_species_kg={"SiO2": 100.0},
        ),
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="residual_rump_at_stop"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_requires_earned_status_in_compute_objectives() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=SimpleNamespace(
            rump_terminal={"status": "not_earned"},
            terminal_rump_by_species_kg={"CaO": 10.0},
        ),
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        species_vector={"Ca": "retain"},
        oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="terminal rump is not earned"):
        compute_objectives(profile, run)


def test_composition_target_eval_metadata_carries_tier_resolution_provenance() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Fe": "retain"},
        oxides={"Fe2O3": {"tier": "clear_container"}},
    )

    metadata = composition_target_eval_metadata(profile)
    row = metadata["target_provenance"]["composition_window"]["oxides"]["Fe2O3"]

    assert row["tier"] == "clear_container"
    assert row["needs_experiment"] is True
    assert row["min"] == pytest.approx(0.0)
    assert row["max"] == pytest.approx(1.0)
    assert "design-composition-target-objective-2026-06-10" in row["provenance"]


def test_composition_targets_require_coating_defaults_true_for_arbitrary_target_id() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )

    assert composition_targets_require_coating(profile) is True


def test_composition_targets_require_coating_honors_explicit_opt_out() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )
    profile["objectives"][0]["target"]["require_coating_gate"] = False

    assert composition_targets_require_coating(profile) is False


def _composition_score_profile(
    pool: str,
    *,
    target_id: str = "pool-test",
    oxides: dict[str, dict[str, float]] | None = None,
    species_vector: dict[str, str] | None = None,
    extraction: dict[str, object] | None = None,
    score_weights: dict[str, float] | None = None,
) -> dict:
    target = {
        "pool": pool,
        "species_vector": species_vector or {"Si": "retain"},
        "composition_window": {
            "pool": pool,
            "basis": "oxide_wt_pct",
            "mode": "hard_window",
            "oxides": oxides
            or {"SiO2": {"min": 99.0, "max": 100.0, "weight": 1.0}},
        },
        "score_weights": score_weights or {"extraction": 0.0, "composition": 1.0},
    }
    if extraction is not None:
        target["extraction"] = extraction
    return {
        "profile_id": "composition-target-score-test",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "type": "composition_target",
                "id": target_id,
                "metric": f"composition_target:{target_id}",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "test composition target score",
                "target": target,
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0, "backend_name": "stub"},
        "fidelities": {"stub": {"backend_name": "stub", "hours": 1}},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
    }
