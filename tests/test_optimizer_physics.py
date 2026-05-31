from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.optimize.physics import (
    GATE_ORDER,
    PhysicsConstraintSet,
    ThresholdSpec,
)
from simulator.state import CampaignPhase, HourSnapshot
from simulator.state import MOLAR_MASS
from simulator.trace import PhysicsTrace


def _kn_snapshot(
    *,
    temperature_C: float = 1600.0,
    campaign: CampaignPhase = CampaignPhase.C2A,
    knudsen_number: float = 0.001,
    knudsen_regime_summary: dict[str, object] | None = None,
) -> HourSnapshot:
    return HourSnapshot(
        hour=1,
        campaign=campaign,
        temperature_C=temperature_C,
        knudsen_regime_summary=knudsen_regime_summary or {
            "status": "ok",
            "regime": "viscous",
            "knudsen_number": knudsen_number,
            "segments": [
                {
                    "name": "hot_wall",
                    "knudsen_number": knudsen_number,
                    "regime": "viscous",
                }
            ],
        },
    )


def _trace(
    *,
    condensed: tuple[dict[tuple[int, str], float], ...],
    products: dict[str, float] | None = None,
    rump: dict[str, float] | None = None,
    wall: tuple[dict[tuple[str, str], float], ...] | None = None,
    temperature_C: float = 1600.0,
    knudsen_number: float = 0.001,
    ) -> PhysicsTrace:
    snapshots = tuple(
        _kn_snapshot(temperature_C=temperature_C, knudsen_number=knudsen_number)
        for _ in condensed
    )
    return PhysicsTrace(
        snapshots=snapshots,
        product_ledger_kg={"SiO": 95.0} if products is None else products,
        terminal_rump_by_species_kg={"SiO2": 1.0} if rump is None else rump,
        condensed_by_stage_species_delta=condensed,
        wall_deposit_by_segment_species_delta=wall or tuple({} for _ in condensed),
    )


def _valid_trace_object(**overrides: object) -> SimpleNamespace:
    fields = {
        "snapshots": (_kn_snapshot(),),
        "product_ledger_kg": {"SiO": 95.0},
        "terminal_rump_by_species_kg": {"SiO2": 1.0},
        "condensed_by_stage_species_delta": ({(3, "SiO"): 20.0},),
        "wall_deposit_by_segment_species_delta": ({},),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_rev5_same_stage_mixed_stream_recipe_is_infeasible_by_purity_only() -> None:
    trace = _trace(condensed=({(3, "SiO"): 19.0, (3, "Fe"): 2.0},))

    result = PhysicsConstraintSet().evaluate(trace)

    assert not result.feasible
    assert result.failing_gates == ("delivered_stream_purity",)
    assert result.margins["delivered_stream_purity"].margin < 0.0
    for gate in (
        "coating",
        "extraction_completeness",
        "knudsen_viscous",
        "furnace_temperature",
    ):
        assert result.margins[gate].feasible
        assert result.margins[gate].margin >= 0.0


def test_clean_selective_recipe_trace_is_feasible_with_signed_margins() -> None:
    trace = _trace(condensed=({(1, "Fe"): 5.0, (3, "SiO"): 20.0},))

    result = PhysicsConstraintSet().evaluate(trace)

    assert result.feasible
    assert tuple(result.margins) == GATE_ORDER
    assert not result.failing_gates
    for margin in result.margins.values():
        assert margin.feasible
        assert isinstance(margin.margin, float)
        assert margin.margin >= -margin.threshold.tolerance


def test_all_five_gates_are_computed_from_physics_trace() -> None:
    constraints = PhysicsConstraintSet(allowable_wall_deposit_kg={
        ("hot_wall", "SiO"): ThresholdSpec(
            id="allowable_wall_deposit_kg.hot_wall.SiO",
            value=1.0,
            units="kg",
            source="engineering_envelope",
            source_ref="test profile coating capacity",
        )
    })
    trace = _trace(
        condensed=({(3, "SiO"): 20.0},),
        wall=({("hot_wall", "SiO"): 0.05},),
    )

    result = constraints.evaluate(trace)

    assert result.feasible
    assert set(result.margins) == set(GATE_ORDER)
    assert result.margins["coating"].observed == pytest.approx(20.0)


def test_thresholds_are_non_null_and_have_declared_provenance() -> None:
    constraints = PhysicsConstraintSet()
    allowed_sources = {
        "literature",
        "materials.yaml",
        "profile",
        "engineering_envelope",
    }

    for threshold in constraints.thresholds:
        assert threshold.value is not None
        assert math.isfinite(threshold.value)
        assert threshold.source in allowed_sources
        assert threshold.source_ref

    rows = constraints.threshold_provenance_table()
    assert {row[0] for row in rows} == set(GATE_ORDER)
    assert all(row[2] in allowed_sources for row in rows)


@pytest.mark.parametrize(
    ("gate", "trace"),
    (
        (
            "delivered_stream_purity",
            _valid_trace_object(condensed_by_stage_species_delta=()),
        ),
        (
            "coating",
            _valid_trace_object(wall_deposit_by_segment_species_delta=None),
        ),
        (
            "extraction_completeness",
            _valid_trace_object(product_ledger_kg={}, terminal_rump_by_species_kg={}),
        ),
        (
            "knudsen_viscous",
            _valid_trace_object(
                snapshots=(
                    _kn_snapshot(
                        knudsen_regime_summary={
                            "status": "ok",
                            "regime": "viscous",
                            "knudsen_number": 0.001,
                        }
                    ),
                )
            ),
        ),
        (
            "furnace_temperature",
            _valid_trace_object(
                snapshots=(),
                condensed_by_stage_species_delta=(),
                wall_deposit_by_segment_species_delta=(),
            ),
        ),
    ),
)
def test_missing_required_trace_data_fails_closed_for_all_gates(
    gate: str,
    trace: object,
) -> None:
    result = PhysicsConstraintSet().evaluate(trace)

    assert not result.feasible
    assert not result.margins[gate].feasible
    assert result.margins[gate].margin < 0.0
    assert "fail-closed" in result.margins[gate].detail


@pytest.mark.parametrize(
    "condensed",
    (
        ({},),
        ({(3, "SiO"): 0.0},),
    ),
)
def test_delivered_stream_purity_empty_or_zero_stream_fails_closed(
    condensed: tuple[dict[tuple[int, str], float], ...],
) -> None:
    margin = PhysicsConstraintSet().delivered_stream_purity(_trace(condensed=condensed))

    assert not margin.feasible
    assert margin.margin < 0.0
    assert "fail-closed" in margin.detail


def test_delivered_stream_purity_delta_count_mismatch_fails_closed() -> None:
    trace = _valid_trace_object(condensed_by_stage_species_delta=())

    margin = PhysicsConstraintSet().delivered_stream_purity(trace)

    assert not margin.feasible
    assert margin.margin < 0.0
    assert "does not match snapshots" in margin.detail


def test_knudsen_viscous_global_only_summary_fails_closed() -> None:
    trace = _valid_trace_object(
        snapshots=(
            _kn_snapshot(
                knudsen_regime_summary={
                    "status": "ok",
                    "regime": "viscous",
                    "knudsen_number": 0.001,
                }
            ),
        )
    )

    margin = PhysicsConstraintSet().knudsen_viscous(trace)

    assert not margin.feasible
    assert margin.margin < 0.0
    assert "fail-closed" in margin.detail


def test_knudsen_viscous_bad_segment_is_infeasible() -> None:
    trace = _valid_trace_object(
        snapshots=(
            _kn_snapshot(
                knudsen_regime_summary={
                    "status": "ok",
                    "regime": "viscous",
                    "knudsen_number": 0.001,
                    "segments": [
                        {
                            "name": "hot_wall",
                            "knudsen_number": 0.02,
                            "regime": "transition",
                        }
                    ],
                }
            ),
        )
    )

    margin = PhysicsConstraintSet().knudsen_viscous(trace)

    assert not margin.feasible
    assert margin.margin < 0.0


def test_extraction_completeness_counts_cr2o3_as_two_cr_equivalent_mol() -> None:
    constraints = PhysicsConstraintSet(
        target_species=("Cr",),
        residual_species_by_target={"Cr": ("Cr2O3", "Cr")},
    )
    trace = _trace(
        condensed=({(1, "Cr"): 1.0},),
        products={"Cr": MOLAR_MASS["Cr"] / 1000.0},
        rump={"Cr2O3": MOLAR_MASS["Cr2O3"] / 1000.0},
    )

    margin = constraints.extraction_completeness(trace)

    assert not margin.feasible
    assert margin.observed == pytest.approx(1.0 / 3.0)
    assert "denominator_target_equiv_mol=3" in margin.detail


def test_extraction_completeness_zero_denominator_fails_closed() -> None:
    margin = PhysicsConstraintSet().extraction_completeness(
        _trace(condensed=({(3, "SiO"): 20.0},), products={}, rump={})
    )

    assert not margin.feasible
    assert margin.margin < 0.0
    assert "fail-closed" in margin.detail


def test_physics_gates_do_not_restore_ellingham_or_mre_gate() -> None:
    assert all("ellingham" not in gate for gate in GATE_ORDER)
    assert all("mre" not in gate.lower() for gate in GATE_ORDER)
