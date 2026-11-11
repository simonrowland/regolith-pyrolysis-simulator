"""Regressions for b-135 fail-closed silent zeros (wide-gk L1–L3, A1–A5; rail T2–T3).

A zero is legitimate ONLY where the physics proves zero (no liquid, zero parent
inventory, certified nonvolatile) and the code can point at that proof.
Otherwise: best available analytical estimate (status-bearing) OR a typed
refusal that is visibly NOT zero downstream — never a silent zero, never a
dropped key.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.queries import (
    _stage0_foulant_partition_rows,
)
from simulator.wall_deposition import (
    _wall_geometry_conductance_weight,
)
from simulator.chemistry import ellingham_graph
from simulator.chemistry.ellingham_graph import EllinghamPressureRefusal
from simulator.melt_backend.alphamelts import (
    AlphaMELTSBackend,
    VaporPressureActivityRefusal,
)
from simulator.melt_backend.base import EquilibriumResult, MeltCompositionError
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.optimize.evaluate import (
    RumpTerminalAssessment,
    _rump_terminal_margin,
)
from simulator.accounting.completeness import extraction_completeness_by_target
from simulator.optimize.physics import (
    PhysicsConstraintSet,
    extraction_completeness_report,
)
from simulator.accounting.yield_disposition import YIELD_DISPOSITION_BINS
from simulator.state import MOLAR_MASS
from simulator.vapour_rail.batch import VapourRequestConstructionError
from simulator.vapour_rail.instrumentation import finite_live_pressure_map
from simulator.vapour_rail.request import (
    _account_mols,
    _positive_mol,
)


# ---------------------------------------------------------------------------
# L1 — Stage-0 foulant missing fraction must not disposition as 0 kg
# ---------------------------------------------------------------------------


def test_l1_stage0_missing_fraction_refuses_not_zero_kg() -> None:
    diagnostic = {
        "reaction_family": "volatilization",
        "carrier": "H2O",
        "feed_kg": 10.0,
        # fractions missing → previously escaped/retained/wall all 0 kg
    }
    with pytest.raises(AccountingError, match="missing required fraction"):
        _stage0_foulant_partition_rows(diagnostic, registry=None)


def test_l1_stage0_missing_extent_refuses_not_zero_decomp() -> None:
    diagnostic = {
        "reaction_family": "sulfate_decomp",
        "carrier": "CaSO4",
        "feed_kg": 5.0,
        # extent missing → previously escaped=0, retained=feed (no decomp)
    }
    with pytest.raises(AccountingError, match="extent"):
        _stage0_foulant_partition_rows(diagnostic, registry=None)


def test_l1_stage0_populated_fractions_still_partition() -> None:
    diagnostic = {
        "reaction_family": "volatilization",
        "carrier": "H2O",
        "feed_kg": 10.0,
        "cumulative_escaped_frac": 0.9,
        "cumulative_retained_frac": 0.1,
        "wall_deposit_frac": 0.2,
    }
    rows = _stage0_foulant_partition_rows(diagnostic, registry=None)
    assert len(rows) == 1
    row = rows[0]
    # wall is taken from escaped; escaped_nonwall = 0.9*10 - 0.2*10 = 7
    assert row["escaped_kg"] == pytest.approx(7.0)
    assert row["retained_kg"] == pytest.approx(1.0)
    assert row["wall_deposit_kg"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# L2 — optimizer extraction-completeness missing mol → not 0.0 product
# ---------------------------------------------------------------------------


def test_l2_missing_product_mol_refuses_completeness_not_zero_product() -> None:
    # Successor of _target_extraction_result_from_payload: a claimed
    # completeness fraction is not evidence. Completeness is the
    # yield-disposition left/not-left mol split. Omitting destination
    # mol-atoms (the old "missing product_target_equiv_mol") refuses the
    # fraction, leaves the basis mols None, and does not publish a product mol.
    product_kg = {"Na": 0.95 * MOLAR_MASS["Na"] / 1000.0}
    residual_kg = {"Na2O": 0.05 * MOLAR_MASS["Na2O"] / 1000.0}
    result = extraction_completeness_by_target(
        ("Na",),
        {"Na": ("Na2O", "Na")},
        product_kg,
        residual_kg,
        yield_disposition_fraction_table_rows=({"element": "Na"},),
    )["Na"]
    assert result.completeness_fraction is None
    assert "missing" in result.reason
    assert "destination" in result.reason
    assert result.left_melt_target_equiv_mol is None
    assert result.not_left_melt_target_equiv_mol is None
    assert result.denominator_target_equiv_mol is None
    # Known ledger product is kept, not replaced with a silent 0.
    assert result.product_target_equiv_mol == pytest.approx(0.95)
    assert result.completeness_fraction is not 0.95

    constraints = PhysicsConstraintSet(
        target_species=("Na",),
        residual_species_by_target={"Na": ("Na2O", "Na")},
        active_gates=("extraction_completeness",),
    )
    trace = SimpleNamespace(
        product_ledger_kg=product_kg,
        terminal_rump_by_species_kg=residual_kg,
        process_inventory_residual_kg={},
        yield_disposition_fraction_table_rows=({"element": "Na"},),
        yield_disposition_fraction_table_error="",
        snapshots=(),
    )
    report = extraction_completeness_report(trace, constraints)
    target = report["targets"]["Na"]
    assert target["completeness_fraction"] is None
    assert target["product_target_equiv_mol"] is None
    assert "missing" in target["reason"]
    assert "destination" in target["reason"]
    margin = constraints.extraction_completeness(trace)
    assert margin.feasible is False
    assert margin.status == "unavailable"
    assert margin.observed is None


def test_l2_explicit_zero_product_with_fraction_still_parses() -> None:
    destinations = {name: 0.0 for name in YIELD_DISPOSITION_BINS}
    destinations["melt_retained"] = 1.0
    result = extraction_completeness_by_target(
        ("Na",),
        {"Na": ("Na2O", "Na")},
        {},
        {"Na2O": MOLAR_MASS["Na2O"] / 1000.0},
        yield_disposition_fraction_table_rows=(
            {"element": "Na", "destination_mol_atoms": destinations},
        ),
    )["Na"]
    assert result.completeness_fraction == pytest.approx(0.0)
    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.left_melt_target_equiv_mol == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# L3 — rump-terminal margin: liquid_fraction None is not observed 0.0 solid
# ---------------------------------------------------------------------------


def test_l3_rump_terminal_none_lf_refuses_solid_margin() -> None:
    assessment = RumpTerminalAssessment(
        earned=False,
        reason="missing_crash_point",
        notes=(),
        trace_payload={},
        liquid_fraction=None,
    )
    margin = _rump_terminal_margin(assessment)
    assert margin.feasible is False
    assert margin.margin == -math.inf
    assert math.isinf(margin.observed) and margin.observed > 0.0
    assert margin.status == "unavailable"
    assert "unknown is not frozen" in margin.detail
    # Must NOT look like "perfectly solid" (observed=0, feasible=True, margin=1e-9)
    assert margin.observed != 0.0
    assert margin.feasible is not True


def test_l3_rump_terminal_real_lf_still_margins() -> None:
    assessment = RumpTerminalAssessment(
        earned=True,
        reason="earned_by_kernel_liquidus",
        notes=(),
        trace_payload={},
        liquid_fraction=0.0,
    )
    margin = _rump_terminal_margin(assessment)
    assert margin.feasible is True
    assert margin.observed == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# A1 — Ellingham: Antoine/missing-species refusal, not P_eff=0.0
# ---------------------------------------------------------------------------


def test_a1_unknown_species_refuses_not_zero_pressure() -> None:
    with pytest.raises(EllinghamPressureRefusal, match="not in metals"):
        ellingham_graph.effective_equilibrium_pressure_Pa(
            "Unobtainium",
            1800.0,
            1e-9,
        )


def test_a1_zero_antoine_A_computes_pressure() -> None:
    # The oxide-vapor reference uses log10(P/Pa) = A - B/(T + C).
    coefficients = {"A": 0.0, "B": 1.0, "C": 1.0}
    pressure = ellingham_graph._antoine_reference_pressure_Pa(
        coefficients, 1800.0
    )
    assert pressure == pytest.approx(10.0 ** (-1.0 / 1801.0))


# ---------------------------------------------------------------------------
# A2 — AlphaMELTS missing activity: refuse partial map, do not drop Na/K
# ---------------------------------------------------------------------------


def test_a2_partial_activity_map_refuses_not_drop_alkali() -> None:
    backend = AlphaMELTSBackend()
    # Melt has Na2O precursor; activities only cover FeO/SiO2 → Na would drop.
    refusal = backend._activities_times_antoine_or_fail(
        1600.0,
        {"FeO": 0.1, "SiO2": 0.5},
        {"FeO": 10.0, "SiO2": 50.0, "Na2O": 5.0},
        # 2026-08-05 MC-4 wave 1B: Si now carries a standard-reaction model
        # with a pO2 exponent, making pO2_bar a required input of this helper
        # (the raise on absence is the fail-loud caller-bug guard; production
        # callers thread it per the b-139 fix).
        pO2_bar=1.0e-9,
        context="b135-a2-partial-activity",
    )
    assert isinstance(refusal, VaporPressureActivityRefusal)
    assert refusal.missing_precursor_species == ("Na",)
    assert refusal.diagnostic()["status"] == "refused"


def test_a2_full_activity_map_still_emits_pressures() -> None:
    backend = AlphaMELTSBackend()
    pressures = backend._activities_times_antoine_or_fail(
        1600.0,
        {"FeO": 0.1, "SiO2": 0.5, "Na2O": 0.05},
        {"FeO": 10.0, "SiO2": 50.0, "Na2O": 5.0},
        # 2026-08-05 MC-4 wave 1B: Si now carries a standard-reaction model
        # with a pO2 exponent, making pO2_bar a required input of this helper
        # (the raise on absence is the fail-loud caller-bug guard; production
        # callers thread it per the b-139 fix).
        pO2_bar=1.0e-9,
        context="b135-a2-full-activity",
    )
    assert "Na" in pressures
    assert pressures["Na"] > 0.0


# ---------------------------------------------------------------------------
# A3 — AlphaMELTS liquid_fraction None must not freeze to zero map
# ---------------------------------------------------------------------------


def test_a3_none_liquid_fraction_refuses_frozen_zero_map() -> None:
    backend = AlphaMELTSBackend()
    # Construct a non-ok result that can carry None LF (ok status rejects None).
    eq = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=["liquid"],
        phase_masses_kg={"liquid": 1.0},
        liquid_fraction=0.0,  # placeholder; force None below
        liquid_composition_wt_pct={"SiO2": 50.0, "Na2O": 5.0},
        status="not_converged",
    )
    object.__setattr__(eq, "liquid_fraction", None)
    with pytest.raises(Exception, match="liquid_fraction is None"):
        backend._builtin_vapor_projection_for_subprocess(eq)


def test_a3_zero_liquid_fraction_still_physical_zero() -> None:
    backend = AlphaMELTSBackend()
    eq = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=[],
        phase_masses_kg={},
        liquid_fraction=0.0,
        liquid_composition_wt_pct={},
        status="ok",
    )
    pressures, _sources, diagnostics = backend._builtin_vapor_projection_for_subprocess(
        eq
    )
    assert pressures == {}
    assert diagnostics.get("vapor_pressure_zero_reason") == "no_liquid_phase"


# ---------------------------------------------------------------------------
# A4 — MAGEMin unrecognized mass key must not invent 0.0 mass
# ---------------------------------------------------------------------------


def test_a4_unrecognized_mass_key_refuses_not_zero() -> None:
    with pytest.raises(MeltCompositionError, match="unparseable_phase_mass"):
        MAGEMinBackend._extract_mass_kg({"Mass": 0.7, "composition": {"SiO2": 50.0}})


def test_a4_recognized_mass_key_still_reads() -> None:
    assert MAGEMinBackend._extract_mass_kg({"mass_kg": 0.3}) == 0.3
    assert MAGEMinBackend._extract_mass_kg(0.5) == 0.5


# ---------------------------------------------------------------------------
# A5 — corrupt view_factor must not weight conductance as 0
# ---------------------------------------------------------------------------


def test_a5_corrupt_view_factor_raises_not_weight_zero() -> None:
    segment = SimpleNamespace(
        surface_area_m2=1.0,
        view_factor_from_melt="high",
        line_of_sight_to_melt=True,
    )
    with pytest.raises(AccountingError, match="view_factor_from_melt"):
        _wall_geometry_conductance_weight(segment)


def test_a5_nonfinite_view_factor_raises() -> None:
    segment = SimpleNamespace(
        surface_area_m2=1.0,
        view_factor_from_melt=float("nan"),
        line_of_sight_to_melt=True,
    )
    with pytest.raises(AccountingError, match="non-finite"):
        _wall_geometry_conductance_weight(segment)


def test_a5_no_los_still_zero_by_proof() -> None:
    segment = SimpleNamespace(
        surface_area_m2=1.0,
        view_factor_from_melt=0.5,
        line_of_sight_to_melt=False,
    )
    assert _wall_geometry_conductance_weight(segment) == 0.0


def test_a5_finite_view_factor_weights_area() -> None:
    segment = SimpleNamespace(
        surface_area_m2=2.0,
        view_factor_from_melt=0.25,
        line_of_sight_to_melt=True,
    )
    assert _wall_geometry_conductance_weight(segment) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# T2 — unreadable inventory is unknown, not zero inventory
# ---------------------------------------------------------------------------


def test_t2_unreadable_mol_value_raises_not_absent() -> None:
    with pytest.raises(VapourRequestConstructionError, match="unreadable inventory"):
        _positive_mol({"Na2O": "not-a-number"}, "Na2O")


def test_t2_unreadable_account_mapping_raises_not_empty() -> None:
    class BadLedger:
        def mol_by_account(self, account: str) -> Any:
            return "corrupt"

    with pytest.raises(VapourRequestConstructionError, match="unreadable inventory"):
        _account_mols(BadLedger(), "process.cleaned_melt")


def test_t2_missing_species_still_false() -> None:
    assert _positive_mol({"FeO": 1.0}, "Na2O") is False


def test_t2_positive_species_still_true() -> None:
    assert _positive_mol({"Na2O": 1.0}, "Na2O") is True


# ---------------------------------------------------------------------------
# T3 — non-finite live pressure not silently dropped from seam
# ---------------------------------------------------------------------------


def test_t3_nonfinite_live_pressure_refuses_silent_drop() -> None:
    with pytest.raises(ValueError, match="non-finite live pressures"):
        finite_live_pressure_map({"Na": 1.0, "K": float("nan")})


def test_t3_finite_map_still_passes() -> None:
    assert finite_live_pressure_map({"Na": 1.0, "K": 2.0}) == {"Na": 1.0, "K": 2.0}
