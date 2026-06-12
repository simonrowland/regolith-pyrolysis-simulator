from __future__ import annotations

import math
import pickle
from types import SimpleNamespace

import pytest

from simulator.optimize.physics import (
    GATE_ORDER,
    PhysicsConstraintSet,
    ThresholdSpec,
    extraction_completeness_report,
    physics_constraints_digest,
)
from simulator.optimize.objective import product_summary
from simulator.optimize.profiles import physics_constraints_from_profile
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
    wall_zone_by_segment: dict[str, str] | None = None,
    temperature_C: float = 1600.0,
    knudsen_number: float = 0.001,
    ) -> PhysicsTrace:
    snapshots = tuple(
        _kn_snapshot(temperature_C=temperature_C, knudsen_number=knudsen_number)
        for _ in condensed
    )
    wall_delta = wall or tuple({} for _ in condensed)
    inferred_zones = {
        str(segment): "Hottest"
        for tick in wall_delta
        for segment, _species in tick
    }
    inferred_zones.update(wall_zone_by_segment or {})
    return PhysicsTrace(
        snapshots=snapshots,
        product_ledger_kg={"SiO": 95.0} if products is None else products,
        terminal_rump_by_species_kg={"SiO2": 1.0} if rump is None else rump,
        condensed_by_stage_species_delta=condensed,
        wall_deposit_by_segment_species_delta=wall_delta,
        wall_zone_by_segment=inferred_zones,
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


def _assert_insufficient_target_evidence_is_unknown(
    target: dict[str, object],
) -> None:
    assert target["status"] == "insufficient-evidence"
    assert target["completeness_fraction"] is None
    assert target["allowed_residual"]["target_equiv_mol"] is None
    for key in (
        "product_target_equiv_mol",
        "residual_target_equiv_mol",
        "denominator_target_equiv_mol",
    ):
        assert key in target
        assert target[key] is None


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


def test_delivered_stream_purity_zero_delivery_is_infeasible_not_nan() -> None:
    trace = _trace(condensed=({(3, "SiO"): 0.0},))

    result = PhysicsConstraintSet().evaluate(trace)

    assert not result.feasible
    margin = result.margins["delivered_stream_purity"]
    assert not margin.feasible
    assert margin.observed == pytest.approx(0.0)
    assert margin.margin == pytest.approx(-margin.threshold.value)
    assert math.isfinite(margin.observed)
    assert math.isfinite(margin.margin)


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


def test_profile_furnace_temperature_threshold_drives_gate() -> None:
    constraints = physics_constraints_from_profile(
        {
            "constraints": {
                "gates": list(GATE_ORDER),
                "furnace_T_max_C": 1300.0,
            }
        },
        source="profiles/test.yaml",
    )

    hot = constraints.evaluate(_trace(condensed=({(3, "SiO"): 20.0},), temperature_C=1300.1))
    cool = constraints.evaluate(_trace(condensed=({(3, "SiO"): 20.0},), temperature_C=1299.9))

    assert hot.failing_gates == ("furnace_temperature",)
    assert cool.feasible
    assert constraints.furnace_T_max_C.source == "profile"
    assert constraints.furnace_T_max_C.source_ref == "profiles/test.yaml:constraints.furnace_T_max_C"


def test_profile_gates_limit_active_physics_evaluation() -> None:
    constraints = physics_constraints_from_profile(
        {
            "constraints": {
                "gates": ["delivered_stream_purity"],
            }
        },
        source="profiles/test.yaml",
    )
    trace = _trace(
        condensed=({(3, "SiO"): 20.0},),
        temperature_C=2500.0,
        knudsen_number=10.0,
    )

    result = constraints.evaluate(trace)

    assert result.feasible
    assert tuple(result.margins) == ("delivered_stream_purity",)
    assert constraints.active_gates == ("delivered_stream_purity",)


def test_physics_constraint_set_is_picklable_for_parallel_evaluation() -> None:
    restored = pickle.loads(pickle.dumps(PhysicsConstraintSet()))

    assert isinstance(restored, PhysicsConstraintSet)
    assert restored.furnace_T_max_C.value == pytest.approx(1800.0)


def test_thresholds_are_non_null_and_have_declared_provenance() -> None:
    constraints = PhysicsConstraintSet()
    allowed_sources = {
        "code_default",
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
    hardcoded_defaults = (
        constraints.extraction_min_fraction,
        constraints.knudsen_max,
        constraints.furnace_T_max_C,
    )
    assert {threshold.source for threshold in hardcoded_defaults} == {"code_default"}
    assert all("profile.feasibility" not in threshold.source_ref for threshold in hardcoded_defaults)


def test_clean_zero_wall_deposit_coating_margin_is_feasible_infinity() -> None:
    result = PhysicsConstraintSet().evaluate(_trace(condensed=({(3, "SiO"): 20.0},)))

    coating = result.margins["coating"]
    assert result.feasible
    assert coating.feasible
    assert coating.detail == "no wall deposit"
    assert coating.margin == math.inf
    assert coating.observed == math.inf


def test_coating_readout_reports_wall_deposit_margin_without_hard_gate() -> None:
    constraints = PhysicsConstraintSet(allowable_wall_deposit_kg={
        ("hot_wall", "SiO"): ThresholdSpec(
            id="allowable_wall_deposit_kg.hot_wall.SiO",
            value=0.01,
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
    coating = result.margins["coating"]

    assert result.feasible
    assert result.failing_gates == ()
    assert coating.feasible
    assert coating.margin < 0.0
    assert coating.observed == pytest.approx(0.2)
    assert "reported-only" in coating.detail
    assert "Hottest/hot_wall/SiO" in coating.detail


def test_coating_and_segment_allowable_readout_reports_small_wall_deposit_margin() -> None:
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
        wall_zone_by_segment={"hot_wall": "Hottest"},
    )

    result = constraints.evaluate(trace)
    coating = result.margins["coating"]

    assert result.feasible
    assert result.failing_gates == ()
    assert coating.feasible
    assert coating.observed == pytest.approx(20.0)
    assert coating.margin > 0.0
    assert "Hottest/hot_wall/SiO" in coating.detail


@pytest.mark.skip(
    reason="needs-interface: coating is reported-only; input data cannot make it fail"
)
def test_coating_blocking_green_path_needs_interface_poison_pair() -> None:
    pass


@pytest.mark.skip(
    reason=(
        "needs-interface: allowable_wall_deposit_kg feeds reported-only coating; "
        "input data cannot make it fail"
    )
)
def test_allowable_wall_deposit_blocking_green_path_needs_interface_poison_pair() -> None:
    pass


@pytest.mark.parametrize(
    ("trace", "expected_detail"),
    (
        (
            _valid_trace_object(
                wall_deposit_by_segment_species_delta=(
                    {("hot_wall", "SiO"): 0.05},
                ),
            ),
            "wall_zone_by_segment trace is missing for wall deposit",
        ),
        (
            _valid_trace_object(
                wall_deposit_by_segment_species_delta=(
                    {("hot_wall", "SiO"): 0.05},
                ),
                wall_zone_by_segment={},
            ),
            "missing wall zone for segment hot_wall",
        ),
    ),
)
def test_coating_positive_wall_deposit_requires_real_zone_trace(
    trace: object,
    expected_detail: str,
) -> None:
    coating = PhysicsConstraintSet().coating(trace)

    assert not coating.feasible
    assert "fail-closed" in coating.detail
    assert expected_detail in coating.detail
    assert "unbucketed" not in coating.detail


@pytest.mark.parametrize(
    ("segment", "zone"),
    (
        ("hot_wall", "Hottest"),
        ("mid_wall", "Hot"),
        ("cool_wall", "Rest"),
    ),
)
def test_coating_readout_uses_declared_wall_zone_buckets(
    segment: str,
    zone: str,
) -> None:
    constraints = PhysicsConstraintSet(allowable_wall_deposit_kg={
        (segment, "SiO"): ThresholdSpec(
            id=f"allowable_wall_deposit_kg.{segment}.SiO",
            value=0.01,
            units="kg",
            source="engineering_envelope",
            source_ref="test profile coating capacity",
        )
    })
    trace = _trace(
        condensed=({(3, "SiO"): 20.0},),
        wall=({(segment, "SiO"): 0.05},),
        wall_zone_by_segment={segment: zone},
    )

    coating = constraints.coating(trace)

    assert coating.feasible
    assert coating.margin < 0.0
    assert "reported-only" in coating.detail
    assert f"{zone}/{segment}/SiO" in coating.detail
    assert "unbucketed" not in coating.detail


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
    if gate == "extraction_completeness":
        assert result.margins[gate].detail.startswith("not-applicable:")
    else:
        assert "fail-closed" in result.margins[gate].detail


@pytest.mark.parametrize(
    "condensed",
    (
        ({},),
        ({(3, "SiO"): 0.0},),
    ),
)
def test_delivered_stream_purity_empty_or_zero_stream_is_finite_infeasible(
    condensed: tuple[dict[tuple[int, str], float], ...],
) -> None:
    margin = PhysicsConstraintSet().delivered_stream_purity(_trace(condensed=condensed))

    assert not margin.feasible
    assert margin.margin < 0.0
    assert margin.observed == pytest.approx(0.0)
    assert math.isfinite(margin.margin)
    assert math.isfinite(margin.observed)
    assert "zero delivered stream" in margin.detail


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


def test_extraction_completeness_uses_per_species_minimum() -> None:
    def fe_threshold(value: float) -> ThresholdSpec:
        return ThresholdSpec(
            id="extraction_completeness_min[Fe]",
            value=value,
            units="fraction",
            source="profile",
            source_ref="test target.extraction.completeness_min.Fe",
        )

    products = {"Fe": 0.86 * MOLAR_MASS["Fe"] / 1000.0}
    rump = {"FeO": 0.14 * MOLAR_MASS["FeO"] / 1000.0}
    trace = _trace(
        condensed=({(1, "Fe"): 1.0},),
        products=products,
        rump=rump,
    )
    loose = PhysicsConstraintSet(
        target_species=("Fe",),
        extraction_min_fraction_by_species={"Fe": fe_threshold(0.85)},
    )
    tight = PhysicsConstraintSet(
        target_species=("Fe",),
        extraction_min_fraction_by_species={"Fe": fe_threshold(0.95)},
    )

    loose_margin = loose.extraction_completeness(trace)
    tight_margin = tight.extraction_completeness(trace)

    assert loose_margin.feasible
    assert loose_margin.observed == pytest.approx(0.86)
    assert loose_margin.margin == pytest.approx(0.01)
    assert loose_margin.threshold.value == pytest.approx(0.85)
    assert not tight_margin.feasible
    assert tight_margin.observed == pytest.approx(0.86)
    assert tight_margin.margin == pytest.approx(-0.09)
    assert tight_margin.threshold.value == pytest.approx(0.95)


def test_per_species_extraction_thresholds_must_cover_targets() -> None:
    threshold = ThresholdSpec(
        id="extraction_completeness_min[Fe]",
        value=0.85,
        units="fraction",
        source="profile",
        source_ref="test target.extraction.completeness_min.Fe",
    )

    with pytest.raises(ValueError, match="missing thresholds"):
        PhysicsConstraintSet(
            target_species=("Fe", "K"),
            extraction_min_fraction_by_species={"Fe": threshold},
        )


def test_extraction_completeness_reports_target_denominator_residual_and_product_bin() -> None:
    constraints = PhysicsConstraintSet(
        target_species=("Cr",),
        residual_species_by_target={"Cr": ("Cr2O3", "Cr")},
    )
    trace = _trace(
        condensed=({(1, "Cr"): 1.0},),
        products={"Cr": MOLAR_MASS["Cr"] / 1000.0},
        rump={"Cr2O3": MOLAR_MASS["Cr2O3"] / 1000.0},
    )

    report = extraction_completeness_report(trace, constraints)
    target = report["targets"]["Cr"]

    assert report["status"] == "reported"
    assert report["conclusion"] == "reported"
    assert report["worst_target_species"] == "Cr"
    assert report["completeness_fraction"] == pytest.approx(1.0 / 3.0)
    assert target["target_species"] == "Cr"
    assert target["status"] == "reported"
    assert target["denominator_account"] == {
        "product": "product_ledger_kg",
        "residual": "terminal_rump_by_species_kg",
    }
    assert target["denominator_basis"] == "target_equivalent_mol"
    assert target["allowed_residual"]["account"] == "terminal_rump_by_species_kg"
    assert target["allowed_residual"]["species"] == ("Cr2O3", "Cr")
    assert target["allowed_residual"]["fraction"] == pytest.approx(0.05)
    assert target["allowed_residual"]["target_equiv_mol"] == pytest.approx(0.15)
    assert target["product_bin"] == "Cr"
    assert target["product_account"] == "product_ledger_kg"
    assert target["product_target_equiv_mol"] == pytest.approx(1.0)
    assert target["residual_target_equiv_mol"] == pytest.approx(2.0)
    assert target["denominator_target_equiv_mol"] == pytest.approx(3.0)

    missing = extraction_completeness_report(
        _trace(condensed=({(1, "Cr"): 1.0},), products={}, rump={}),
        constraints,
    )
    missing_target = missing["targets"]["Cr"]
    assert missing["status"] == "insufficient-evidence"
    assert missing["conclusion"] == "inconclusive"
    assert missing["completeness_fraction"] is None
    _assert_insufficient_target_evidence_is_unknown(missing_target)
    assert missing_target["reason"] == "not-applicable: zero input basis for Cr"

    sim = SimpleNamespace(
        train=SimpleNamespace(stages=()),
        product_ledger=lambda: {"Cr": MOLAR_MASS["Cr"] / 1000.0},
        _terminal_rump_by_species=lambda: {
            "Cr2O3": MOLAR_MASS["Cr2O3"] / 1000.0,
        },
    )
    summary = product_summary(
        SimpleNamespace(simulator=sim, trace=trace),
        {"constraints": {"target_species": ["Cr"]}},
    )

    assert summary["extraction_completeness"]["targets"]["Cr"]["product_bin"] == "Cr"


@pytest.mark.parametrize(
    ("trace", "reason_fragment"),
    (
        (None, "trace missing product_ledger_kg"),
        (
            SimpleNamespace(product_ledger_kg={"Cr": MOLAR_MASS["Cr"] / 1000.0}),
            "trace missing terminal_rump_by_species_kg",
        ),
    ),
)
def test_extraction_completeness_report_missing_evidence_never_zero_fills(
    trace: object,
    reason_fragment: str,
) -> None:
    constraints = PhysicsConstraintSet(
        target_species=("Cr",),
        residual_species_by_target={"Cr": ("Cr2O3", "Cr")},
    )

    report = extraction_completeness_report(trace, constraints)
    target = report["targets"]["Cr"]

    assert report["status"] == "insufficient-evidence"
    assert report["conclusion"] == "inconclusive"
    assert report["completeness_fraction"] is None
    assert reason_fragment in report["reason"]
    assert reason_fragment in target["reason"]
    _assert_insufficient_target_evidence_is_unknown(target)


def test_extraction_completeness_report_unknown_residual_map_never_zero_fills() -> None:
    constraints = PhysicsConstraintSet(
        target_species=("Cr",),
        residual_species_by_target={},
    )
    trace = _trace(
        condensed=({(1, "Cr"): 1.0},),
        products={"Cr": MOLAR_MASS["Cr"] / 1000.0},
        rump={"Cr2O3": MOLAR_MASS["Cr2O3"] / 1000.0},
    )

    report = extraction_completeness_report(trace, constraints)
    target = report["targets"]["Cr"]

    assert report["status"] == "insufficient-evidence"
    assert report["conclusion"] == "inconclusive"
    assert "no residual species map for target" in report["reason"]
    assert "no residual species map for target" in target["reason"]
    _assert_insufficient_target_evidence_is_unknown(target)


def test_constraint_digest_changes_when_threshold_changes() -> None:
    base = PhysicsConstraintSet()
    tightened = PhysicsConstraintSet(
        furnace_T_max_C=ThresholdSpec(
            id="furnace_T_max_C",
            value=1700.0,
            units="degC",
            source="code_default",
            source_ref="test tightened furnace ceiling",
        )
    )

    assert physics_constraints_digest(tightened) != physics_constraints_digest(base)


def test_extraction_completeness_gate_margin_matches_7913470_golden() -> None:
    """GateMargin byte-identity vs pre-refactor inline loop (7913470)."""

    constraints = PhysicsConstraintSet()
    threshold = constraints.extraction_min_fraction

    zero_denom = constraints.extraction_completeness(
        _trace(condensed=({(3, "SiO"): 20.0},), products={}, rump={})
    )
    assert zero_denom.gate == "extraction_completeness"
    assert zero_denom.feasible is False
    assert zero_denom.margin == -math.inf
    assert zero_denom.observed == math.inf
    assert zero_denom.threshold == threshold
    assert zero_denom.detail == "not-applicable: zero input basis for SiO"

    cr_constraints = PhysicsConstraintSet(
        target_species=("Cr",),
        residual_species_by_target={"Cr": ("Cr2O3", "Cr")},
    )
    cr_trace = _trace(
        condensed=({(1, "Cr"): 1.0},),
        products={"Cr": MOLAR_MASS["Cr"] / 1000.0},
        rump={"Cr2O3": MOLAR_MASS["Cr2O3"] / 1000.0},
    )
    cr_margin = cr_constraints.extraction_completeness(cr_trace)
    assert cr_margin.feasible is False
    assert cr_margin.margin == pytest.approx(-0.6166666666666667)
    assert cr_margin.observed == pytest.approx(1.0 / 3.0)
    assert cr_margin.detail == (
        "Cr: product_target_equiv_mol=1, residual_target_equiv_mol=2, "
        "denominator_target_equiv_mol=3"
    )

    sio_margin = constraints.extraction_completeness(
        _trace(condensed=({(3, "SiO"): 20.0},))
    )
    assert sio_margin.feasible is True
    assert sio_margin.margin == pytest.approx(0.04233584186562911)
    assert sio_margin.observed == pytest.approx(0.9923358418656291)
    assert sio_margin.detail == (
        "SiO: product_target_equiv_mol=2154.98, "
        "residual_target_equiv_mol=16.6436, "
        "denominator_target_equiv_mol=2171.62"
    )

    multi_constraints = PhysicsConstraintSet(
        target_species=("Na", "K", "Fe", "SiO"),
    )
    multi_trace = _trace(
        condensed=({(1, "Fe"): 5.0, (3, "SiO"): 20.0},),
        products={"SiO": 95.0, "Fe": 10.0, "Na": 1.0, "K": 1.0},
        rump={"SiO2": 0.1, "FeO": 0.1, "Na2O": 0.01, "K2O": 0.01},
    )
    multi_margin = multi_constraints.extraction_completeness(multi_trace)
    assert multi_margin.feasible is True
    assert multi_margin.margin == pytest.approx(0.04176683470705078)
    assert multi_margin.observed == pytest.approx(0.9917668347070507)
    assert multi_margin.detail == (
        "K: product_target_equiv_mol=25.5766, residual_target_equiv_mol=0.212324, "
        "denominator_target_equiv_mol=25.7889"
    )

    exc_constraints = PhysicsConstraintSet(
        target_species=("Fe",),
        residual_species_by_target={"Fe": ("NotARealFormula",)},
    )
    exc_margin = exc_constraints.extraction_completeness(
        _trace(condensed=({(1, "Fe"): 1.0},), products={}, rump={"NotARealFormula": 1.0})
    )
    assert exc_margin.feasible is False
    assert exc_margin.margin == -math.inf
    assert math.isnan(exc_margin.observed)
    assert exc_margin.detail == (
        "fail-closed: 'missing molar mass for NotARealFormula'"
    )


def test_physics_gates_do_not_restore_ellingham_or_mre_gate() -> None:
    assert all("ellingham" not in gate for gate in GATE_ORDER)
    assert all("mre" not in gate.lower() for gate in GATE_ORDER)
