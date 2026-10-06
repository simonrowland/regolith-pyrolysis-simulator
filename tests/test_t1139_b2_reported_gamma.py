"""Reported gamma copies the ladder. The reporter does not choose a rung."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import SimpleNamespace

from simulator.diagnostic_helpers.binary_pot_battery import (
    reported_activity_coefficients,
    trace_parent_activity_coefficient_emission,
)
from simulator.vapour_rail.activity import (
    ActivityVerdictKind,
    StandardStateIdentity,
    resolve_trace_parent_activity,
    trace_parent_formulas,
)


_SS = StandardStateIdentity(
    convention="raoultian_pure_endmember",
    phase="liquid",
    reference_pressure_bar=1.0,
)


def _row(
    formula: str,
    *,
    row_id: str,
    A: str = "0",
    B: str = "0",
    notes: str = "",
    band: list[float] | None = None,
) -> dict[str, object]:
    return {
        "source_row_id": row_id,
        "formula": formula,
        "A": A,
        "B": B,
        "validity_range_K": band,
        "notes_as_printed": notes,
    }


def _owner(
    formula: str,
    temperature_K: float,
    rows: list[dict[str, object]] | tuple | None,
):
    return resolve_trace_parent_activity(
        formula,
        temperature_K=temperature_K,
        activity_exponent=1.0,
        standard_state=_SS,
        rows=rows,
    )


def _point_only(detail: Mapping[str, object]) -> float | None:
    if detail.get("verdict") != ActivityVerdictKind.POINT.value:
        return None
    value = detail.get("value")
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _assert_copies_owner(formula: str, detail: Mapping[str, object], owner) -> None:
    derivation = owner.derivation
    notice = derivation.get("extrapolation_notice")
    certified_band = notice.get("certified_band") if isinstance(notice, Mapping) else None
    assert detail["value"] == derivation.get("gamma")
    assert detail["rung"] == derivation.get("rung")
    assert detail["flag"] == derivation.get("flag")
    assert detail["source_row_id"] == derivation.get("source_row_id")
    assert detail["source_row_ids"] == list(derivation.get("source_row_ids") or ())
    assert detail["origin"] == derivation.get("origin")
    assert detail["homologue"] == derivation.get("homologue")
    assert detail["verdict"] == owner.verdict.value
    assert detail["certified_band"] == certified_band
    assert detail["coefficient_formula"] == derivation.get("coefficient_formula")
    assert detail["source_basis"] == {
        "component": derivation.get("row_formula"),
        "standard_state_as_printed": derivation.get("standard_state_as_printed"),
        "convention": derivation.get("source_convention"),
        "phase": derivation.get("source_phase"),
    }
    assert detail["target_basis"] == {
        "convention": derivation.get("target_convention", owner.standard_state.convention),
        "phase": derivation.get("target_phase", owner.standard_state.phase),
        "component_basis": formula,
    }
    assert "coefficient_basis" not in detail
    if derivation.get("basis_established") is True:
        assert detail["standard_state"] == {
            "convention": derivation["source_convention"],
            "phase": "l",
            "component_basis": formula,
        }
    else:
        assert "standard_state" not in detail


def test_production_report_copies_every_trace_parent() -> None:
    formulas = trace_parent_formulas()
    for temperature_K in (1500.0, 1673.0):
        gammas, details = trace_parent_activity_coefficient_emission(temperature_K)
        assert set(details) == set(formulas)
        for formula in formulas:
            owner = _owner(formula, temperature_K, None)
            detail = details[formula]
            _assert_copies_owner(formula, detail, owner)
            gamma = owner.derivation.get("gamma")
            if isinstance(gamma, (int, float)) and math.isfinite(gamma) and gamma > 0.0:
                assert gammas[formula] == gamma
            else:
                assert formula not in gammas
                assert detail["rung"] == 2
                assert detail["source_row_ids"]
        copied = reported_activity_coefficients(
            SimpleNamespace(reported_activity_coefficients=gammas)
        )
        assert copied == gammas


def test_homologue_is_copied_before_unity() -> None:
    rows = [
        _row("K2O", row_id="k2o", B="-13400", notes="one published fit"),
        _row("Na2O", row_id="na2o", B="-8000", notes="one published fit"),
    ]
    gammas, details = trace_parent_activity_coefficient_emission(1500.0, rows=rows)
    followed = {"Rb2O": "K2O", "Cs2O": "K2O", "Li2O": "Na2O"}
    for formula in followed:
        owner = _owner(formula, 1500.0, rows)
        unity = _owner(formula, 1500.0, ())
        detail = details[formula]
        _assert_copies_owner(formula, detail, owner)
        assert detail["rung"] == 3
        assert detail["flag"] == owner.derivation["flag"]
        assert detail["flag"] != unity.derivation["flag"]
        assert detail["homologue"] == followed[formula]
        assert gammas[formula] == owner.derivation["gamma"]
        assert gammas[formula] != 1.0
        assert detail["verdict"] != ActivityVerdictKind.UPPER_BOUND.value


def test_printed_phrase_is_not_a_standard_state_claim() -> None:
    """A caller-matching row may claim a basis. A printed phrase may not."""

    bare = _row("SnO", row_id="bare", B="-1000", notes="one published fit")
    printed = {
        **_row("SnO", row_id="printed", B="-1000", notes="one published fit"),
        "standard_state_as_printed": "not stated in Table 2 row",
    }
    for rows, established in (([bare], True), ([printed], False)):
        _gammas, details = trace_parent_activity_coefficient_emission(
            1500.0, rows=rows
        )
        owner = _owner("SnO", 1500.0, rows)
        _assert_copies_owner("SnO", details["SnO"], owner)
        assert owner.derivation["basis_established"] is established
        assert (details["SnO"].get("standard_state") is not None) is established


def test_empty_table_upper_bound_is_not_a_point() -> None:
    gammas, details = trace_parent_activity_coefficient_emission(1500.0, rows=())
    assert set(details) == set(trace_parent_formulas())
    for formula, detail in details.items():
        owner = _owner(formula, 1500.0, ())
        _assert_copies_owner(formula, detail, owner)
        assert detail["rung"] == 4
        assert detail["verdict"] == ActivityVerdictKind.UPPER_BOUND.value
        assert detail["verdict"] != ActivityVerdictKind.POINT.value
        assert detail["value"] == 1.0
        assert gammas[formula] == 1.0
        assert _point_only(detail) is None
    copied = reported_activity_coefficients(
        SimpleNamespace(reported_activity_coefficients=gammas)
    )
    assert copied == gammas
