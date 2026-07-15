from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
import simulator.optimize.objective as objective_module
from scripts import sso_r_validation_map as validation_map
from simulator.cost_parameters import CostParameters
from simulator.optimize.objective import (
    CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    LEGACY_ENERGY_KWH_METRIC,
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    composition_targets_require_coating,
    composition_target_eval_metadata,
    compute_objectives,
    dominates,
    objective_definitions,
    objective_scores,
    objective_importance_evidence,
    pareto_front,
    product_summary,
    target_spec_digest,
    _metric_value,
)
from simulator.optimize.sso2_evidence import (
    SSO2_CERTIFIED_PN2_MBAR,
    SSO2_CERTIFIED_PO2_MBAR,
    SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
    SSO2_CHUNK3B_READER_HANDOFF,
    SSO2_OWNER_RECIPE_ID,
    build_sso2_owner_recipe_execution,
    sso2_owner_recipe_evidence,
    sso2_owner_recipe_objective_reader,
    sso2_owner_recipe_setpoints_patch,
)
from scripts.sso2_owner_recipe_report import _markdown_report
from simulator.pumping_cost import MARS_DATUM_AMBIENT_PA, estimate_subambient_pump_cost


DEFINITIONS = (
    ObjectiveDefinition("oxygen_kg", "maximize", "kg", ordinal=0),
    ObjectiveDefinition(ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC, "minimize", "kWh", ordinal=1),
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


def test_nullable_objective_scores_preserve_metric_positions() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )

    assert objective_scores(
        {"primary": None, "middle": 4.0, "last": 9.0},
        definitions,
    ) == (None, -4.0, 9.0)
    assert objective_scores(
        {"primary": 2.0, "middle": None, "last": 9.0},
        definitions,
    ) == (2.0, None, 9.0)
    assert objective_scores(
        {"primary": 2.0, "middle": 4.0, "last": None},
        definitions,
    ) == (2.0, -4.0, None)


def test_dominates_skips_unavailable_objectives_by_metric_not_position() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )

    assert dominates(
        {"primary": None, "middle": 4.0, "last": 10.0},
        {"primary": 100.0, "middle": 5.0, "last": 10.0},
        definitions,
    )
    assert dominates(
        {"primary": 10.0, "middle": None, "last": 10.0},
        {"primary": 9.0, "middle": 1.0, "last": 10.0},
        definitions,
    )
    assert dominates(
        {"primary": 10.0, "middle": 4.0, "last": None},
        {"primary": 10.0, "middle": 5.0, "last": 100.0},
        definitions,
    )


def test_target_spec_digest_excludes_derived_thermal_window_disposition() -> None:
    target = {
        "pool": "residual_rump_at_stop",
        "species_vector": {"Ca": "retain"},
        "composition_window": {
            "pool": "residual_rump_at_stop",
            "basis": "oxide_wt_pct",
            "mode": "hard_window",
            "oxides": {"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        },
        "maturity": {"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
    }
    with_display_disposition = {
        **target,
        "thermal_window": "campaign-without-explicit-temperature-window",
    }

    assert target_spec_digest(with_display_disposition) == target_spec_digest(target)
    assert target_spec_digest(
        {"targets": ({"id": "a", "target": with_display_disposition},)}
    ) == target_spec_digest({"targets": ({"id": "a", "target": target},)})


def test_missing_or_nonfinite_objectives_raise() -> None:
    with pytest.raises(
        ObjectiveComputationError,
        match=f"{ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC}.*missing",
    ):
        dominates({"oxygen_kg": 1.0}, {"oxygen_kg": 1.0, "energy_kWh": 5.0}, DEFINITIONS)

    with pytest.raises(ObjectiveComputationError, match="oxygen_kg is non-finite"):
        dominates(
            {"oxygen_kg": math.nan, "energy_kWh": 5.0},
            {"oxygen_kg": 1.0, "energy_kWh": 5.0},
            DEFINITIONS,
        )


def test_legacy_energy_objective_alias_scores_under_canonical_definition() -> None:
    profile = {
        "objectives": [
            {
                "metric": LEGACY_ENERGY_KWH_METRIC,
                "sense": "minimize",
                "units": "kWh",
            }
        ]
    }

    definitions = objective_definitions(profile)

    assert definitions[0].metric == ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    assert objective_scores({LEGACY_ENERGY_KWH_METRIC: 4.0}, definitions) == (-4.0,)
    assert objective_scores(
        {ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: 4.0},
        (ObjectiveDefinition(LEGACY_ENERGY_KWH_METRIC, "minimize", "kWh"),),
    ) == (-4.0,)


def test_energy_alias_set_is_derived_from_canonical_alias_mapping() -> None:
    expected = frozenset(
        {
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            *(
                alias
                for alias, canonical in objective_module._OBJECTIVE_METRIC_ALIASES.items()
                if canonical == ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
            ),
        }
    )
    assert objective_module.ENERGY_ELECTRICAL_PLUS_EVAPORATION_ALIASES == expected
    assert set(objective_module.objective_metric_aliases(LEGACY_ENERGY_KWH_METRIC)) == expected


def test_energy_component_and_per_product_metrics_read_scoped_energy() -> None:
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 6.0,
            "evaporation_thermal": 12.0,
            "latent": 5.0,
            "dissociation": 7.0,
            "electrical_plus_evaporation": 18.0,
        },
        record=SimpleNamespace(
            energy_electrical_plus_evaporation_kWh=18.0,
            energy_electrical_kWh=6.0,
            energy_evaporation_thermal_kWh=12.0,
            energy_latent_kWh=5.0,
            energy_dissociation_kWh=7.0,
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 3.0}}

    assert _metric_value(
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC, sim, {}, product_classes
    ) == pytest.approx(18.0)
    assert _metric_value(LEGACY_ENERGY_KWH_METRIC, sim, {}, product_classes) == pytest.approx(18.0)
    assert _metric_value(
        "electrical_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(6.0)
    assert _metric_value(
        "evaporation_thermal_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(12.0)
    assert _metric_value(
        "energy_electrical_plus_evaporation_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx(6.0)
    assert _metric_value(
        "dissociation_energy_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx(7.0 / 3.0)


def test_energy_objective_adds_staged_pumping_once_without_readding_turbine() -> None:
    # Helper anchor: Mars datum 610 Pa -> 500 Pa, 0.01 mol/s, 300 K, 1 h.
    # Its staged electrical result is ~0.008100986 kWh (the 5 mbar anchor in
    # test_pumping_cost), and it is disjoint from turbine-compressed O2.
    pumping = estimate_subambient_pump_cost(
        target_pressure_pa=500.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 6.0,
            "evaporation_thermal": 12.0,
            "electrical_plus_evaporation": 18.0,
        },
        record=SimpleNamespace(
            cost_rollup={
                "pumping_diagnostic": {
                    "status": "pumping_feasibility_unresolved",
                    "pumping_electrical_kWh": pumping.energy_kWh,
                },
                # This aggregate already includes 2 kWh turbine work. Reading
                # it would re-add turbine energy already present in the 6 kWh
                # electrical baseline; the objective must read pump only.
                "auxiliary_electrical_diagnostic": {
                    "components_kWh": {
                        "turbine": 2.0,
                        "pumping": pumping.energy_kWh,
                    },
                    "auxiliary_electrical_kWh": 2.0 + pumping.energy_kWh,
                },
            },
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 3.0}}

    assert pumping.energy_kWh == pytest.approx(0.008100986135507714)
    assert _metric_value(
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
        sim,
        {},
        product_classes,
    ) == pytest.approx(18.0 + pumping.energy_kWh)
    assert _metric_value(
        "electrical_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(6.0 + pumping.energy_kWh)
    assert _metric_value(
        "evaporation_thermal_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(12.0)
    assert _metric_value(
        "energy_electrical_plus_evaporation_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx((18.0 + pumping.energy_kWh) / 3.0)


def test_certified_pumping_diagnostic_without_energy_fails_closed() -> None:
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
    )
    execution = SimpleNamespace(
        certified_pumping_diagnostic={"status": "ok", "feasible": True},
    )

    with pytest.raises(
        ObjectiveComputationError,
        match="pumping_diagnostic.pumping_electrical_kWh is not numeric",
    ):
        _metric_value(
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            sim,
            {},
            {},
            run_execution=execution,
        )


def test_throughput_cost_metrics_read_cost_rollup_and_lifespan_rate(monkeypatch) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 2.0)
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=2.0,
        furnace_resinter_cost_usd=100.0,
        depreciation_expense_per_run=7.0,
        generic_reagent_cost_per_kg=5.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=10.0,
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {
                    "physical_cost": {
                        "thermal_flux_h": 1234.0,
                        "furnace_h": 6.0,
                    },
                    "owner_ratify_money_projection": 1294.0,
                }
            }
        )
    )
    run_execution = SimpleNamespace(
        simulator=sim,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={
                ("stage_0_to_stage_1", "SiO"): 0.20,
                ("stage_0_to_stage_1", "Na"): 0.05,
            }
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 1.0}}

    assert _metric_value(
        "solar_thermal_flux_h",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(1234.0)
    assert _metric_value(
        "furnace_time_h",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(6.0)
    assert _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        {"consumed_Fe_reagent": 2.0},
        product_classes,
        run_execution=run_execution,
        cost_parameters=cost_parameters,
    ) == pytest.approx(42.5)
    assert _metric_value(
        "furnace_lifespan_consumed_fraction",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(0.125)


def test_lifespan_cost_metric_is_not_costed_when_threshold_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: None)
    sim = SimpleNamespace()
    run_execution = SimpleNamespace(
        simulator=sim,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={
                ("stage_0_to_stage_1", "SiO"): 0.20,
                ("stage_0_to_stage_1", "Na"): 0.05,
            }
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 1.0}}

    assert _metric_value(
        "furnace_lifespan_consumed_fraction",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) is None
    summary = objective_module._furnace_lifespan_cost_summary(run_execution, sim)
    assert summary["lifespan_cost_status"] == "threshold_unavailable"
    assert summary["furnace_lifespan_consumed_fraction"] is None
    assert summary["wall_deposit_total_kg"] == pytest.approx(0.25)
    assert summary["wall_deposit_kg_by_species"] == {
        "Na": pytest.approx(0.05),
        "SiO": pytest.approx(0.20),
    }


def test_marginal_cost_uses_depreciation_default_and_shuttle_replacement_rate() -> None:
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=7.0,
        furnace_resinter_cost_usd=999.0,
        depreciation_expense_per_run=11.0,
        generic_reagent_cost_per_kg=5.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 13.0,
            "K": 17.0,
            "Mg": 19.0,
            "Ca": 23.0,
        },
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=1.0,
        record=SimpleNamespace(cost_rollup={}),
    )

    assert _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        {
            "consumed_Fe_reagent": 2.0,
            "consumed_Na_reagent": 3.0,
        },
        {},
        run_execution=SimpleNamespace(simulator=sim),
        cost_parameters=cost_parameters,
    ) == pytest.approx(67.0)


def test_cost_objective_ranking_prices_pumping_sidecar() -> None:
    definitions = (
        ObjectiveDefinition(
            "throughput_cost_owner_ratify_usd",
            "minimize",
            "usd",
            ordinal=0,
        ),
    )

    cost_parameters = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=0.0,
        depreciation_expense_per_run=0.0,
        generic_reagent_cost_per_kg=0.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 0.0,
            "K": 0.0,
            "Mg": 0.0,
            "Ca": 0.0,
        },
    )

    def sim_with(base_energy_kWh: float, *, pumping_electrical_kWh: float | None = None):
        cost_rollup = {
            "run_input_cost": {
                "physical_cost": {
                    "thermal_flux_h": 1234.0,
                    "furnace_h": 6.0,
                },
                "owner_ratify_money_projection": 0.0,
            }
        }
        if pumping_electrical_kWh is not None:
            cost_rollup["pumping_diagnostic"] = {
                "status": "ok",
                "pumping_electrical_kWh": pumping_electrical_kWh,
            }
        return SimpleNamespace(
            energy_electrical_plus_evaporation_cumulative_kWh=base_energy_kWh,
            record=SimpleNamespace(cost_rollup=cost_rollup),
        )

    def score(sim: SimpleNamespace) -> tuple[float, ...]:
        value = _metric_value(
            "throughput_cost_owner_ratify_usd",
            sim,
            {},
            {},
            run_execution=SimpleNamespace(simulator=sim),
            cost_parameters=cost_parameters,
        )
        return objective_scores(
            {"throughput_cost_owner_ratify_usd": value},
            definitions,
        )

    without_pumping = (
        ("lower_cost", sim_with(100.0)),
        ("higher_cost", sim_with(200.0)),
    )
    with_pumping = (
        ("lower_cost", sim_with(100.0, pumping_electrical_kWh=1_000_000.0)),
        ("higher_cost", sim_with(200.0)),
    )

    rank_without = [
        name for name, sim in sorted(
            without_pumping,
            key=lambda item: score(item[1]),
            reverse=True,
        )
    ]
    rank_with = [
        name for name, sim in sorted(
            with_pumping,
            key=lambda item: score(item[1]),
            reverse=True,
        )
    ]

    assert rank_without == ["lower_cost", "higher_cost"]
    assert rank_with == ["higher_cost", "lower_cost"]


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


def test_pareto_front_keeps_nullable_objective_identity() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )
    items = (
        {"id": "primary-null", "objectives": {"primary": None, "middle": 4.0, "last": 1.0}},
        {"id": "middle-null", "objectives": {"primary": 10.0, "middle": None, "last": 1.0}},
        {"id": "last-null", "objectives": {"primary": 10.0, "middle": 4.0, "last": None}},
        {"id": "dominated", "objectives": {"primary": 9.0, "middle": 5.0, "last": 1.0}},
    )

    front = pareto_front(
        items,
        definitions,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == [
        "primary-null",
        "middle-null",
        "last-null",
    ]


class _FakeLedger:
    def kg_by_account(self, account: str) -> dict[str, float]:
        if account == "terminal.offgas":
            return {"H2O": 5.0}
        if account == "process.metal_phase":
            return {"Fe": 50.0}
        return {}


class _FakeProductSim:
    atom_ledger = _FakeLedger()

    def __init__(self) -> None:
        self._feedstock_recovered_reagent_kg_by_species = {}
        self._non_feedstock_reagent_element_kg_by_account = {}
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
            initial_inventory=SimpleNamespace(
                melt_oxide_kg={"FeO": 80.0, "SiO2": 100.0},
            ),
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

    def _unspent_additive_reagents_kg(self) -> dict[str, float]:
        return {}

    def _c3_alkali_credit_outstanding_kg_by_species(self) -> dict[str, float]:
        return {}


class _FakeUnclassifiedProductSim(_FakeProductSim):
    def product_ledger(self) -> dict[str, float]:
        ledger = dict(super().product_ledger())
        ledger["MysteryOxide"] = 7.0
        return ledger


class _FakeSso2Transition:
    def __init__(self, name: str = "native_fe_saturation_split") -> None:
        self.name = name


class _FakeSso2Ledger:
    def __init__(
        self,
        accounts: dict[str, dict[str, float]],
        *,
        split_present: bool = True,
        transition_name: str = "native_fe_saturation_split",
    ) -> None:
        self._accounts = accounts
        self.transitions = (
            (_FakeSso2Transition(transition_name),) if split_present else ()
        )

    def kg_by_account(self, account: str) -> dict[str, float]:
        return dict(self._accounts.get(account, {}))


def _fake_sso2_stage_gas() -> dict[str, float | str]:
    return {
        "stage_name": "sio_window",
        "gas_cover_mode": "pn2_sweep",
        "atmosphere": "PN2_SWEEP",
        "pO2_mbar": SSO2_CERTIFIED_PO2_MBAR,
        "pN2_mbar": SSO2_CERTIFIED_PN2_MBAR,
        "p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
        "requested_p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
    }


def _fake_sso2_partition_basis(
    *,
    native_fe_source_account: str = "process.cleaned_melt",
    native_fe_split_commit_status: str = "ok",
) -> dict[str, float | str]:
    return {
        "native_fe_pool_mol": 12.0,
        "native_fe_tap_mol": 11.5,
        "native_fe_vapor_mol": 0.5,
        "native_fe_vapor_escape_fraction_of_pool": 0.5 / 12.0,
        "native_fe_vapor_capacity_mol_hr": 0.5,
        "temperature_K": 1923.15,
        "overhead_pressure_pa": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR * 100.0,
        "overhead_pressure_source": "melt.p_total_mbar",
        "carrier_gas": "N2",
        "source_label": "fake native Fe partition basis",
        "native_fe_source_account": native_fe_source_account,
        "native_fe_split_commit_status": native_fe_split_commit_status,
        "native_fe_vapor_route_status": "committed",
    }


def _sso2_execution(
    *,
    condensed_delta: dict[tuple[int, str], float] | None = None,
    ledger: _FakeSso2Ledger | None = None,
    snapshot_partition_present: bool | None = None,
    snapshot_partition_source_account: str = "process.cleaned_melt",
    snapshot_partition_commit_status: str = "ok",
):
    split_present = bool(getattr(ledger, "transitions", ())) if ledger is not None else False
    if snapshot_partition_present is None:
        snapshot_partition_present = split_present
    snapshot = SimpleNamespace(
        hour=1,
        campaign="C2A_staged",
        mass_balance_error_pct=1.2e-13,
        c2a_staged_gas=_fake_sso2_stage_gas(),
        fe_redox_split=(
            {"native_fe_partition": _fake_sso2_partition_basis(
                native_fe_source_account=snapshot_partition_source_account,
                native_fe_split_commit_status=snapshot_partition_commit_status,
            )}
            if snapshot_partition_present
            else {}
        ),
    )
    trace = SimpleNamespace(
        snapshots=(snapshot,),
        condensed_by_stage_species_delta=(
            dict(condensed_delta or {}),
        ),
        wall_deposit_by_segment_species_delta=({},),
        wall_deposit_by_segment_species_kg={},
        wall_zone_by_segment={},
        wall_deposit_sticking_authority={},
    )
    sim = SimpleNamespace(
        atom_ledger=ledger,
        species_formula_registry={},
        product_ledger=lambda: {"Fe": 8.0},
    )
    return SimpleNamespace(
        simulator=sim,
        snapshots=(snapshot,),
        trace=trace,
    )


def test_sso2_owner_recipe_patch_is_named_allowlisted_fe_then_sio() -> None:
    patch = sso2_owner_recipe_setpoints_patch()
    staged = patch["campaigns"]["C2A_staged"]
    stages = {stage["name"]: stage for stage in staged["stages"]}

    assert staged["order"] == "fe_then_sio"
    assert stages["fe_hot_hold"]["gas_cover_mode"] == "pn2_sweep"
    assert stages["fe_hot_hold"]["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert stages["fe_hot_hold"]["p_total_mbar"] == pytest.approx(
        SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR
    )
    assert stages["sio_window"]["target_C"] == pytest.approx(1650.0)
    assert stages["sio_window"]["gas_cover_mode"] == "pn2_sweep"
    assert stages["sio_window"]["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert stages["sio_window"]["p_total_mbar"] == pytest.approx(
        SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR
    )
    assert (
        stages["sio_window"]["p_total_mbar"] - stages["sio_window"]["pO2_mbar"]
    ) == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)


def test_sso2_owner_execution_uses_certified_na_dose_and_partition_path() -> None:
    execution = build_sso2_owner_recipe_execution(hours=9)
    evidence = sso2_owner_recipe_evidence(execution)
    calibration = validation_map.calibrate_dose()

    surface = evidence["certified_sso_r_surface"]
    partition = evidence["fe_drain_vapor_partition_dependency"]
    stage_gas = surface["stage_gas_snapshot"]
    partition_basis = partition["native_fe_partition_basis"]

    assert execution.status in {"ok", "partial"}
    assert surface["dose_species"] == "Na"
    assert surface["dose_kg"] == pytest.approx(calibration.full_feo_equiv_dose_kg)
    assert surface["dose_transition_count"] >= 1
    assert surface["declared_pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert surface["declared_pN2_mbar"] == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)
    assert surface["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert surface["pN2_mbar"] == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)
    assert surface["pO2_mbar"] == pytest.approx(stage_gas["pO2_mbar"])
    assert surface["pN2_mbar"] == pytest.approx(stage_gas["pN2_mbar"])
    # adf1059's authoritative metallic-tap provider commits
    # native_fe_metal_partition from process.metal_phase, while the hour
    # snapshot carries the matching drain/vapor basis.  Both ledger commit and
    # snapshot basis are required before this evidence may report available.
    assert partition["status"] == "available"
    assert partition["status_reason"] == ""
    assert partition["stage_gas_snapshot"]["pO2_mbar"] == pytest.approx(
        surface["pO2_mbar"]
    )
    assert partition_basis["status"] == "available"
    assert partition_basis["native_fe_source_account"] == "process.metal_phase"
    assert partition_basis["native_fe_split_commit_status"] == "ok"
    assert partition_basis["native_fe_vapor_route_status"] == "committed"
    assert partition["native_fe_saturation_split_count"] == 0
    assert partition["native_fe_metal_partition_count"] >= 1
    assert partition["native_fe_partition_transition_count"] >= 1
    assert partition["tap_basis"]["status"] == "available"
    assert evidence["fe_tap"]["status"] == "available"
    # The tap drained the source metal phase, so the terminal tap and aggregate
    # product ledger now carry Fe while the source account is empty.
    assert evidence["metal_product_path"]["status"] == "drained_to_tap"
    assert evidence["metal_product_path"]["Fe_kg"] == pytest.approx(0.0)
    assert evidence["metal_product_path"]["product_ledger_Fe_kg"] > 0.0
    fallback = evidence["prototype_alpha_fallback_provenance"]
    assert fallback == {
        "severity": "warning",
        "status": "engaged",
        "policy": "alpha=1.0 prototype fallback",
        "scope": "SSO-2 trace CrO2 species lacking grounded evaporation alpha",
        "permitted_species": ["CrO2"],
        "engaged_species": ["CrO2"],
        "total_engagement_count": fallback["total_engagement_count"],
    }
    assert fallback["total_engagement_count"] > 0
    report = _markdown_report(evidence, execution)
    assert "WARNING prototype_alpha_fallback" in report
    assert "alpha=1.0 prototype fallback" in report
    assert "permitted_species=`CrO2`" in report


def test_sso2_evidence_reports_stage3_fe_and_delivered_purity_margin() -> None:
    execution = _sso2_execution(
        condensed_delta={
            (1, "Fe"): 5.0,
            (1, "SiO"): 0.25,
            (3, "SiO2"): 9.0,
            (3, "Si"): 1.0,
            (3, "Fe"): 1.0,
        },
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0, "Si": 0.2},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "stage_stream_purity_failed"
    assert evidence["stage_3"]["accepted_species"] == ["Si", "SiO", "SiO2"]
    assert evidence["stage_3"]["silica_species_kg"]["SiO2"] == pytest.approx(9.0)
    assert evidence["stage_3"]["silica_species_mol"]["SiO2"] > 0.0
    assert evidence["stage_3"]["Fe_kg"] == pytest.approx(1.0)
    assert evidence["stage_3"]["Fe_wt_pct"] == pytest.approx(100.0 / 11.0)
    assert evidence["delivered_stream_purity"]["feasible"] is False
    assert evidence["delivered_stream_purity"]["observed"] == pytest.approx(10.0 / 11.0)
    assert evidence["fe_tap"]["Fe_kg"] == pytest.approx(7.0)
    assert evidence["fe_tap"]["SiO_Si_impurity_wt_pct"] == pytest.approx(0.2 / 7.2 * 100.0)
    assert "chunk 3b" in evidence["reader_handoff_chunk3b"]
    assert "Stage 3 Fe contamination" in SSO2_CHUNK3B_READER_HANDOFF


def test_sso2_evidence_empty_fe_tap_account_fails_closed_without_zero_alias() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None
    assert evidence["fe_tap"]["total_kg"] is None
    assert evidence["fe_tap"]["species_kg"] == {}


def test_sso2_evidence_missing_partition_preempts_empty_fe_tap_status() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 1.0}}, split_present=False),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_drain_vapor_partition"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None


def test_sso2_partition_basis_requires_committed_partition_transition() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger(
            {"terminal.drain_tap_material": {"Fe": 1.0}},
            split_present=False,
        ),
        snapshot_partition_present=True,
    )

    evidence = sso2_owner_recipe_evidence(execution)
    dependency = evidence["fe_drain_vapor_partition_dependency"]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_partition_transition_count"] == 0
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert "no committed native Fe partition transition" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_partition_basis_accepts_committed_metallic_tap_transition() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger(
            {"terminal.drain_tap_material": {"Fe": 1.0}},
            transition_name="native_fe_metal_partition",
        ),
        snapshot_partition_source_account="process.metal_phase",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "available"
    assert dependency["native_fe_saturation_split_count"] == 0
    assert dependency["native_fe_metal_partition_count"] == 1
    assert dependency["native_fe_partition_transition_count"] == 1
    assert dependency["native_fe_partition_basis"]["status"] == "available"


def test_sso2_partition_basis_rejects_mismatched_transition_source() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 1.0},
        }),
        snapshot_partition_source_account="process.metal_phase",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_saturation_split_count"] == 1
    assert dependency["native_fe_metal_partition_count"] == 0
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert "no committed native_fe_metal_partition transition" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_partition_basis_rejects_uncommitted_snapshot_diagnostic() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 1.0},
        }),
        snapshot_partition_commit_status="refused",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_saturation_split_count"] == 1
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert dependency["native_fe_partition_basis"][
        "native_fe_split_commit_status"
    ] == "refused"
    assert "lacks a committed split" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_evidence_negative_condensed_kg_fails_closed_without_raise() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): -1.0},
        ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "invalid_stage_purity_trace"
    assert evidence["stage_3"]["status"] == "invalid_stage_purity_trace"
    assert evidence["stage_3"]["Fe_kg"] is None


def test_sso2_evidence_negative_fe_tap_kg_fails_closed_without_raise() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": -1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None
    assert evidence["fe_tap"]["species_kg"] == {}


def test_sso2_evidence_negative_product_fe_kg_fails_closed_without_raise() -> None:
    # SC-49 sibling: the optional product-ledger field must not escape a bare
    # ValueError from the evidence surface on a corrupt (negative) product kg.
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=1.2e-13)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": -1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(
            snapshots=(snapshot,),
            condensed_by_stage_species_delta=({(3, "SiO2"): 1.0},),
        ),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["metal_product_path"]["product_ledger_Fe_kg"] is None


def test_sso2_evidence_nonfinite_mass_balance_fails_closed_without_raise() -> None:
    # SC-49 sibling: a non-finite mass-balance value is invalid evidence, not a
    # pass, and must fail closed rather than raise out of the evidence surface.
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=float("nan"))
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(
            snapshots=(snapshot,),
            condensed_by_stage_species_delta=({(3, "SiO2"): 1.0},),
        ),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["mass_balance"]["status"] == "invalid_mass_balance_trace"
    assert evidence["mass_balance"]["max_abs_error_pct"] is None


def test_sso2_evidence_missing_stage_trace_fails_closed_without_zero_alias() -> None:
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=0.0)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(snapshots=(snapshot,)),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_stage_purity_trace"
    assert evidence["stage_3"]["Fe_kg"] is None
    assert evidence["stage_3"]["silica_species_kg"] == {
        "SiO": None,
        "SiO2": None,
        "Si": None,
    }
    assert evidence["delivered_stream_purity"]["detail"].startswith("fail-closed:")


def _sso2_objective_profile(*, stream_purity_min: float = 0.95) -> dict:
    return {
        "objectives": [
            {
                "metric": SSO2_OWNER_RECIPE_ID,
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "SSO-2 Fe-free Stage 3 silica and Fe tap purity reader",
            }
        ],
        "constraints": {
            "gates": ["delivered_stream_purity"],
            "stream_purity_min": stream_purity_min,
        },
    }


def test_sso2_objective_reader_score_changes_with_stage3_fe_contamination() -> None:
    clean = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )
    contaminated = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0, (3, "Fe"): 0.1},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )

    clean_objectives = compute_objectives(_sso2_objective_profile(), clean)
    dirty_objectives = compute_objectives(_sso2_objective_profile(), contaminated)

    clean_score = clean_objectives.as_mapping()[SSO2_OWNER_RECIPE_ID]
    dirty_score = dirty_objectives.as_mapping()[SSO2_OWNER_RECIPE_ID]
    assert clean_score == pytest.approx(1.0)
    assert 0.0 < dirty_score < clean_score
    reader = dirty_objectives.evidence[SSO2_OWNER_RECIPE_ID]
    assert reader["reader"] == SSO2_OWNER_RECIPE_ID
    assert reader["status"] == "stage_3_fe_contamination_penalized"
    assert reader["score_components"]["stage_3_fe_kg"] == pytest.approx(0.1)
    assert reader["score_components"]["fe_tap_Fe_kg"] == pytest.approx(7.0)
    assert "delivered_stream_purity.margin" in reader["consumed_fields"]


def test_sso2_objective_reader_fails_closed_on_missing_fe_tap_evidence() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 3.0}}),
    )

    objectives = compute_objectives(_sso2_objective_profile(), execution)

    assert objectives.as_mapping()[SSO2_OWNER_RECIPE_ID] == pytest.approx(0.0)
    reader = objectives.evidence[SSO2_OWNER_RECIPE_ID]
    assert reader["status"] == "missing_fe_tap_evidence"
    assert reader["evidence"]["fe_tap"]["Fe_kg"] is None


def test_sso2_objective_reader_fails_closed_on_missing_stage_purity() -> None:
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=0.0)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(snapshots=(snapshot,)),
    )

    score, reader = sso2_owner_recipe_objective_reader(execution)

    assert score == pytest.approx(0.0)
    assert reader["status"] == "missing_stage_purity_trace"
    assert reader["evidence"]["stage_3"]["Fe_kg"] is None


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
    target_yield = summary["target_species_yield_report"]
    assert target_yield["gate_status"] == "skipped_pending_physics"
    assert "product_summary.target_species_yield_report" in target_yield["consumer"]
    assert target_yield["targets"]["K"]["status"] == "not-applicable"
    assert target_yield["targets"]["Fe"]["yield_fraction"] is not None


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
            energy_electrical_plus_evaporation_kWh=1.0,
            total_hours=1,
        )
        self.melt = SimpleNamespace(hour=1)
        self.energy_electrical_plus_evaporation_cumulative_kWh = 1.0

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


def test_residual_rump_pool_includes_spent_reductant_residue_account() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={
                SPENT_REDUCTANT_RESIDUE_ACCOUNT: {"Na2O": 1.0},
            },
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"Na2O": {"min": 40.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["resolved_composition"]["oxide_wt_pct"]["Na2O"] > 40.0


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


def test_composition_target_soft_rows_rank_only_after_strict_pass() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0},
            "Al2O3": {"min": 15.0, "max": 20.0, "strict": False, "weight": 2.0},
        },
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    soft_row = next(row for row in evidence["rows"] if row["id"] == "Al2O3")

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(
        soft_row["score"]
    )
    assert evidence["certified_envelope"][0]["id"] == "SiO2"
    assert evidence["preference_score"] == pytest.approx(soft_row["score"])


def test_composition_target_hard_miss_skips_soft_rows() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 99.0, "max": 100.0, "strict": True, "weight": 1.0},
            "Al2O3": {"min": 15.0, "max": 20.0, "strict": False, "weight": 2.0},
        },
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    soft_row = next(row for row in evidence["rows"] if row["id"] == "Al2O3")

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)
    assert soft_row["score"] is None
    assert soft_row["reason"] == "hard_gate_failed_soft_not_computed"


def test_composition_target_ratio_row_scores_after_pool_projection() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
        ratios=[
            {
                "ratio": {
                    "numerator": "CaO",
                    "denominator": "Al2O3",
                    "min": 0.45,
                    "max": 0.75,
                    "strict": True,
                    "weight": 1.0,
                }
            }
        ],
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["resolved_composition"]["ratios"]["CaO/Al2O3"] == pytest.approx(
        0.54999,
        rel=1e-5,
    )


def test_composition_target_ratio_zero_denominator_fails_loud() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
        ratios=[
            {
                "ratio": {
                    "numerator": "CaO",
                    "denominator": "Al2O3",
                    "min": 0.45,
                    "max": 0.75,
                    "strict": True,
                    "weight": 1.0,
                }
            }
        ],
    )

    with pytest.raises(ObjectiveComputationError, match="denominator.*missing"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_scores_completed_run_residue_with_provenance() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
        ),
        trace=None,
        backend_status="ok",
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["terminal_rump_source"] == "completed_run"


def test_terminal_rump_pool_rejects_trace_only_out_of_domain_as_completed_run() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=SimpleNamespace(backend_status="out_of_domain"),
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="cannot use completed_run"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_unknown_completion_status_fails_closed() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="positive completion evidence"):
        compute_objectives(profile, run)


@pytest.mark.parametrize(
    ("product_kg", "oxides"),
    [
        (
            {"SiO2": 1000.0},
            {
                "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
                "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
            },
        ),
        (
            {},
            {
                "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
                "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
            },
        ),
        (
            {"SiO2": 1000.0},
            {"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
        ),
    ],
)
def test_composition_target_valid_unit_weights_keep_score_0_1(
    product_kg: dict[str, float],
    oxides: dict[str, dict[str, float]],
) -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
            product_kg=product_kg,
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract", "Ca": "retain"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides=oxides,
        score_weights={"extraction": 0.5, "composition": 0.5},
    )

    score = compute_objectives(profile, run).as_mapping()["composition_target:pool-test"]

    assert math.isfinite(score)
    assert 0.0 <= score <= 1.0


def test_composition_target_extraction_skips_unknown_product_bookkeeping_species() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0, "unspent_K_reagent": 1.0},
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
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 1.0, "composition": 0.0},
    )

    objectives = compute_objectives(profile, run)

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert objectives.evidence["composition_target:pool-test"][
        "captured_product_bookkeeping_exclusions"
    ] == ("unspent_K_reagent",)
    assert objectives.evidence["composition_target:pool-test"]["notes"] == (
        "excluded captured-products bookkeeping species from extraction credit: "
        "unspent_K_reagent",
    )


def test_composition_target_unknown_non_bookkeeping_product_species_raises() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0, "MysteryProduct": 1.0},
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
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 1.0, "composition": 0.0},
    )

    with pytest.raises(ObjectiveComputationError, match="MysteryProduct"):
        compute_objectives(profile, run)


def test_captured_product_bookkeeping_pattern_constant_pinned() -> None:
    assert CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS == ("unspent_*_reagent",)


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
    profile["objectives"][0]["target"]["thermal_window"] = "C2B window 1260-1480 C"

    metadata = composition_target_eval_metadata(profile)
    row = metadata["target_provenance"]["composition_window"]["oxides"]["Fe2O3"]

    assert metadata["target_provenance"]["thermal_window"] == "C2B window 1260-1480 C"
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


def test_best_tap_digest_changes_when_enabled() -> None:
    base = _composition_score_profile("residual_rump_at_stop")
    enabled = _composition_score_profile(
        "residual_rump_at_stop",
        maturity={"best_tap": {"enabled": True}},
    )

    assert composition_target_eval_metadata(base)["target_spec_digest"] != (
        composition_target_eval_metadata(enabled)["target_spec_digest"]
    )


def test_best_tap_selects_single_intermediate_hour_with_grade_report() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 80.0, "CaO": 20.0}, stage_delta={(1, "Fe"): 1.0}),
        _tap_snapshot(
            2,
            {"SiO2": 50.0, "CaO": 50.0},
            stage_delta={(3, "SiO2"): 3.0, (4, "Na"): 1.0, (4, "Mg"): 1.0},
        ),
        _tap_snapshot(3, {"SiO2": 20.0, "CaO": 80.0}),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    grade = evidence["tap_grade_report"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["tap_hour"] == 2
    assert evidence["configured_hours"] == 3
    assert evidence["tap_provenance"] == "tap_truncated"
    assert evidence["pool_snapshot_hour"] == 2
    assert evidence["operator_instruction"]["phase_at_tap"] == "C2A"
    assert evidence["operator_instruction"]["pN2_mbar"] == pytest.approx(10.0)
    assert evidence["operator_instruction"]["sweep_setting"] == "millibar_sweep"
    assert evidence["knife_edge"] is True
    assert evidence["certified"] is False
    assert [entry["hour"] for entry in evidence["tap_score_curve"]] == [1, 2, 3]
    assert sum(grade["melt_tap"]["oxide_wt_pct"].values()) == pytest.approx(100.0)
    assert "2" not in grade["distillation_train_taps"]
    stage3 = grade["distillation_train_taps"]["3"]
    assert stage3["dominant_species"] == "SiO2"
    assert stage3["dominant_species_purity_pct"] == pytest.approx(100.0)
    stage4 = grade["distillation_train_taps"]["4"]
    assert sum(stage4["species_wt_pct"].values()) == pytest.approx(100.0)

    truncated_profile = {
        **profile,
        "run": {**profile["run"], "hours": 2},
        "fidelities": {"stub": {**profile["fidelities"]["stub"], "hours": 2}},
    }
    truncated_run = _tap_run(snapshots[:2], configured_hours=2)
    reproduced = compute_objectives(truncated_profile, truncated_run)
    reproduced_evidence = reproduced.evidence["composition_target:pool-test"][
        "composition_target"
    ]
    assert reproduced.as_mapping()["composition_target:pool-test"] == pytest.approx(
        objectives.as_mapping()["composition_target:pool-test"]
    )
    assert reproduced_evidence["resolved_composition"] == evidence["resolved_composition"]
    assert [row["pass"] for row in reproduced_evidence["rows"]] == [
        row["pass"] for row in evidence["rows"]
    ]


def test_best_tap_dwell_can_prefer_later_certified_tie() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(3, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(4, {"SiO2": 80.0, "CaO": 20.0}),
    )
    run = _tap_run(snapshots, configured_hours=4)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 4)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 3
    assert evidence["certified"] is True
    assert evidence["knife_edge"] is False


def test_best_tap_nonterminal_captured_pool_fails_loud_without_note() -> None:
    snapshots = (
        _tap_snapshot(1, {}, melt_mass_kg=0.0, stage_delta={(3, "SiO2"): 10.0}),
        _tap_snapshot(2, {}, melt_mass_kg=0.0),
        _tap_snapshot(3, {}, melt_mass_kg=0.0),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _captured_extraction_profile(
        {"best_tap": {"enabled": True, "tap_stability_hours": 2}},
    )
    _set_profile_hours(profile, 3)

    with pytest.raises(ObjectiveComputationError, match="non-terminal captured-pool"):
        compute_objectives(profile, run)


def test_best_tap_nonterminal_captured_pool_can_emit_explicit_note() -> None:
    snapshots = (
        _tap_snapshot(1, {}, melt_mass_kg=0.0, stage_delta={(3, "SiO2"): 10.0}),
        _tap_snapshot(2, {}, melt_mass_kg=0.0),
        _tap_snapshot(3, {}, melt_mass_kg=0.0),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _captured_extraction_profile(
        {
            "best_tap": {
                "enabled": True,
                "tap_stability_hours": 2,
                "captured_pool_nonterminal_policy": "allow_with_note",
            }
        },
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 2
    assert evidence["tap_provenance"] == "tap_truncated"
    assert evidence["nonterminal_captured_pool_note"] == (
        "target pool-test selected captured-pool tap for pool captured_products "
        "at hour 2 of configured 3"
    )


def test_best_tap_coating_summary_uses_tap_hour_not_terminal_deposit() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 80.0, "CaO": 20.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 0.001},
        ),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(
            3,
            {"SiO2": 20.0, "CaO": 80.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 2
    assert coating["wall_deposit_kg_by_segment_species"]["stage_1_to_stage_2"]["SiO"] == pytest.approx(0.001)
    assert coating["wall_deposit_kg_by_zone_species"]["Hot"]["SiO"] == pytest.approx(0.001)
    assert "0.001" in coating["campaigns_to_resinter"]
    assert "100" not in coating["campaigns_to_resinter"]


def test_best_tap_clean_coating_summary_emits_complete_empty_fields() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(
            2,
            {"SiO2": 20.0, "CaO": 80.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=2)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 2)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 1
    assert coating["campaigns_to_resinter"] == "infinite"
    assert coating["wall_deposit_kg_by_segment_species"] == {}
    assert coating["wall_deposit_kg_by_zone_species"] == {}


def test_best_tap_coating_summary_carries_violation_present_at_tap_hour() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 80.0, "CaO": 20.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(3, {"SiO2": 20.0, "CaO": 80.0}),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 2
    assert coating["wall_deposit_kg_by_segment_species"]["stage_1_to_stage_2"]["SiO"] == pytest.approx(100.0)
    assert "100" in coating["campaigns_to_resinter"]


def test_best_tap_missing_requested_grid_hour_fails_loud() -> None:
    snapshots = (_tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),)
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True, "tap_grid": [1, 2]}},
    )
    _set_profile_hours(profile, 3)

    with pytest.raises(ObjectiveComputationError, match=r"missing hours: \[2\]"):
        compute_objectives(profile, run)


def test_best_tap_grade_basis_divergence_fails_loud() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            inventory_melt_oxide_kg={"SiO2": 90.0, "CaO": 10.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=1)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 1)

    with pytest.raises(ObjectiveComputationError, match="grade basis diverges"):
        compute_objectives(profile, run)


def test_best_tap_grade_basis_tolerates_float_noise_edge() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            inventory_melt_oxide_kg={"SiO2": 50.000002, "CaO": 49.999998},
        ),
    )
    run = _tap_run(snapshots, configured_hours=1)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 1)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 1


def _tap_snapshot(
    hour: int,
    composition_wt_pct: dict[str, float],
    *,
    melt_mass_kg: float = 100.0,
    stage_delta: dict[tuple[int, str], float] | None = None,
    wall_delta: dict[tuple[str, str], float] | None = None,
    inventory_melt_oxide_kg: dict[str, float] | None = None,
):
    return SimpleNamespace(
        hour=hour,
        campaign=SimpleNamespace(name="C2A"),
        temperature_C=1200.0 + hour,
        melt_mass_kg=melt_mass_kg,
        composition_wt_pct=composition_wt_pct,
        inventory=SimpleNamespace(melt_oxide_kg=dict(inventory_melt_oxide_kg or {})),
        overhead=SimpleNamespace(composition={"O2": 0.25, "N2": 10.0}),
        condensed_by_stage_species_delta=dict(stage_delta or {}),
        wall_deposit_by_segment_species_delta=dict(wall_delta or {}),
        sweep_setting="millibar_sweep",
    )


def _tap_run(snapshots, *, configured_hours: int):
    sim = _CompositionSim(cleaned_melt={"SiO2": 1.0}, stage3_kg={})
    sim.record.total_hours = configured_hours
    return SimpleNamespace(
        simulator=sim,
        snapshots=tuple(snapshots),
        trace=SimpleNamespace(
            snapshots=tuple(snapshots),
            wall_zone_by_segment={"stage_1_to_stage_2": "Hot"},
        ),
        per_hour=tuple(
            {
                "hour": snapshot.hour,
                "campaign": snapshot.campaign.name,
                "T_C": snapshot.temperature_C,
                "reduced_real_cache_state": "cached_exact",
            }
            for snapshot in snapshots
        ),
        backend_status="ok",
    )


def _captured_extraction_profile(maturity: dict[str, object]) -> dict:
    profile = _composition_score_profile(
        "captured_products",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.0001},
        },
        oxides=None,
        score_weights={"extraction": 1.0, "composition": 0.0},
        maturity=maturity,
    )
    del profile["objectives"][0]["target"]["composition_window"]
    return profile


def _set_profile_hours(profile: dict, hours: int) -> None:
    profile["run"]["hours"] = hours
    profile["fidelities"]["stub"]["hours"] = hours


def _composition_score_profile(
    pool: str,
    *,
    target_id: str = "pool-test",
    oxides: dict[str, dict[str, float]] | None = None,
    ratios: list[dict] | None = None,
    species_vector: dict[str, str] | None = None,
    extraction: dict[str, object] | None = None,
    score_weights: dict[str, float] | None = None,
    maturity: dict[str, object] | None = None,
) -> dict:
    window = {
        "pool": pool,
        "basis": "oxide_wt_pct",
        "mode": "hard_window",
        "oxides": oxides
        or {"SiO2": {"min": 99.0, "max": 100.0, "weight": 1.0}},
    }
    if ratios is not None:
        window["ratios"] = ratios
    target = {
        "pool": pool,
        "species_vector": species_vector or {"Si": "retain"},
        "composition_window": window,
        "score_weights": score_weights or {"extraction": 0.0, "composition": 1.0},
    }
    if extraction is not None:
        target["extraction"] = extraction
    if maturity is not None:
        target["maturity"] = maturity
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
