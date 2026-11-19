"""Surface-law log pressure is never a silent zero.

Invariant. Every finite log10(P/Pa) the scaled mass-action law or the
condensed-metal cap publishes is one of two results, and both laws
reach that result through ``_surface_log_pressure``:

- the representable power ``10**log10(P)``, with no continuation flag,
  including powers below the old ``-300`` cutoff and above the old
  ``+300`` cutoff;
- the IEEE minimum positive pressure, flagged
  ``numerical_underflow_floor``, when the log is below that minimum.

A non-finite log and a log whose power is not a finite positive float
raise on the surface laws. They are not published as 0. That set
includes the overflow endpoint ``log10`` of the largest finite float:
the strict comparison against itself is false, and ``10**`` of the
rounded logarithm raises ``OverflowError``. The catalog helper turns
that overflow into the flagged ceiling. The surface laws turn the
ceiling into ``ValueError``. The adjacent float below the endpoint is
the representable power 1.7976931348620926e+308 Pa. The oracle below
is this rule written out. It does not call ``realize_log10_pressure_pa``
or ``_surface_log_pressure``.

The mutation that puts ``if log_p < -300.0: return 0.0, None`` back at
the start of ``_surface_log_pressure`` fails
``representable_below_minus_300`` on both branches: the published
pressure is 0.0 instead of 3.162277660168379e-303 Pa.

The mutation that deletes the ``OverflowError`` handler in
``realize_log10_pressure_pa`` fails ``endpoint`` on every path: the
helper and both laws raise ``OverflowError`` instead of the ceiling
flag and ``ValueError`` (``langmuir pressure law overflowed``).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from simulator.langmuir_surface_oxygen import (
    _Carrier,
    _capped_pressure_Pa,
    _scaled_pressure_Pa,
    _surface_log_pressure,
    apply_langmuir_surface_oxygen,
)

# Reviewer's inputs: P_pub = 1e-290 Pa, p_ref = 1e-30 bar, n = -1/2,
# surface = 1e-5 bar. log10(P) = -290 - 12.5 = -302.5. The power is
# representable. The old cutoff published 0.
_REVIEWER_PA = 3.162277660168379e-303
_UNDERFLOW = "numerical_underflow_floor"

# name, branch, and the inputs that branch reads. ``expect`` selects the
# independent outcome. Capped rows carry raw activity and P_sat. Scaled
# rows carry the published pressure.
_CASES = (
    {
        "name": "ordinary",
        "branch": "scaled",
        "pressure": 1000.0,
        "exponent": -0.5,
        "p_ref": 1.0e-9,
        "surface": 1.0e-12,
        "expect": "power",
    },
    {
        "name": "ordinary",
        "branch": "capped",
        "p_sat": 1000.0,
        "raw": 0.25,
        "exponent": -0.5,
        "p_ref": 1.0e-9,
        "surface": 1.0e-6,
        "expect": "power",
    },
    {
        "name": "representable_below_minus_300",
        "branch": "scaled",
        "pressure": 1.0e-290,
        "exponent": -0.5,
        "p_ref": 1.0e-30,
        "surface": 1.0e-5,
        "expect": "reviewer",
    },
    {
        "name": "representable_below_minus_300",
        "branch": "capped",
        "p_sat": 1.0e-10,
        "raw": 1.0e-280,
        "exponent": -0.5,
        "p_ref": 1.0e-30,
        "surface": 1.0e-5,
        "expect": "reviewer",
    },
    {
        "name": "representable_above_plus_300",
        "branch": "scaled",
        "pressure": 1.0e300,
        "exponent": -0.5,
        "p_ref": 1.0,
        "surface": 1.0e-12,
        "expect": "power",
    },
    {
        "name": "representable_above_plus_300",
        "branch": "capped",
        "p_sat": 1.0e306,
        "raw": 0.1,
        "exponent": -0.5,
        "p_ref": 1.0e-9,
        "surface": 1.0e-9,
        "expect": "power",
    },
    {
        "name": "underflow",
        "branch": "scaled",
        "pressure": 1.0e-290,
        "exponent": -1.0,
        "p_ref": 1.0e-40,
        "surface": 1.0,
        "expect": "underflow",
    },
    {
        "name": "underflow",
        "branch": "capped",
        "p_sat": 1.0e-200,
        "raw": 1.0e-100,
        "exponent": -1.0,
        "p_ref": 1.0e-30,
        "surface": 1.0,
        "expect": "underflow",
    },
    {
        "name": "overflow",
        "branch": "scaled",
        "pressure": 1.0e300,
        "exponent": -1.0,
        "p_ref": 1.0,
        "surface": 1.0e-20,
        "expect": "raise",
    },
    {
        "name": "nonfinite",
        "branch": "scaled",
        "pressure": 1.0,
        "exponent": -0.5,
        "p_ref": 1.0e-300,
        "surface": 1.0e300,
        "expect": "raise",
    },
    {
        "name": "nonfinite",
        "branch": "capped",
        "p_sat": 1.0,
        "raw": 1.0,
        "exponent": -0.5,
        "p_ref": 1.0e-300,
        "surface": 1.0e300,
        "expect": "raise",
    },
    {
        "name": "cap_binds",
        "branch": "capped",
        "p_sat": 4000.0,
        "raw": 1.0e20,
        "exponent": -0.5,
        "p_ref": 1.0e-30,
        "surface": 1.0e-5,
        "expect": "cap",
    },
    {
        "name": "exponent_zero",
        "branch": "scaled",
        "pressure": 12.5,
        "exponent": 0.0,
        "p_ref": 1.0e-30,
        "surface": 1.0e-5,
        "expect": "identity",
    },
    {
        "name": "surface_equals_pref",
        "branch": "scaled",
        "pressure": 12.5,
        "exponent": -0.5,
        "p_ref": 1.0e-9,
        "surface": 1.0e-9,
        "expect": "identity",
    },
)


def _minimum_positive_pa() -> float:
    return math.nextafter(0.0, math.inf)


def _maximum_finite_pa() -> float:
    return math.nextafter(sys.float_info.max, 0.0)


def _case_log(case: dict) -> float | None:
    """log10(P/Pa) the branch forms, or None when it does not take a log."""

    exponent = case["exponent"]
    surface = case["surface"]
    p_ref = case["p_ref"]
    if case["branch"] == "scaled":
        if exponent == 0.0 or surface == p_ref:
            return None
        return math.log10(case["pressure"]) + exponent * math.log10(
            surface / p_ref
        )
    log_raw = math.log10(case["raw"]) + exponent * math.log10(surface / p_ref)
    if log_raw >= 0.0:
        return None
    return math.log10(case["p_sat"]) + log_raw


def _exponentiation_class(
    log_p: float,
) -> tuple[str, float | None, str | None]:
    """Independent outcome of ``10**log_p``.

    ``('value', pressure, status)`` or ``('raise', None, None)``. This
    does not call ``realize_log10_pressure_pa``. A log above the largest
    finite float, and the endpoint equal to ``log10`` of that float,
    are refusals: ``10**`` of the endpoint raises ``OverflowError``
    because the rounded logarithm sits above every finite power of ten.
    The adjacent float toward zero is a finite power.
    """

    if not math.isfinite(log_p) or log_p > math.log10(_maximum_finite_pa()):
        return "raise", None, None
    if log_p < math.log10(_minimum_positive_pa()):
        return "value", _minimum_positive_pa(), _UNDERFLOW
    try:
        pressure = 10.0 ** log_p
    except OverflowError:
        return "raise", None, None
    if not math.isfinite(pressure) or pressure <= 0.0:
        return "raise", None, None
    return "value", pressure, None


def _oracle(case: dict) -> tuple[str, float | None, str | None]:
    """Independent outcome: ('value', pressure, status) or ('raise', None, None)."""

    if case["expect"] == "identity":
        return "value", case["pressure"], None
    if case["expect"] == "cap":
        return "value", case["p_sat"], None
    log_p = _case_log(case)
    assert log_p is not None
    if case["expect"] == "reviewer":
        assert log_p == -302.5
        assert log_p < -300.0
        assert _REVIEWER_PA > _minimum_positive_pa()
        return "value", _REVIEWER_PA, None
    return _exponentiation_class(log_p)


def _call(case: dict) -> tuple[float, str | None]:
    if case["branch"] == "scaled":
        return _scaled_pressure_Pa(
            case["pressure"],
            case["exponent"],
            case["p_ref"],
            case["surface"],
        )
    carrier = _Carrier(
        species="Zn",
        pressure_Pa=case.get("pressure", case["p_sat"]),
        reaction_exponent=case["exponent"],
        p_ref_bar=case["p_ref"],
        inverted=False,
        transport_channel=False,
        activity_capped=False,
        raw_activity=case["raw"],
        cap_psat_Pa=case["p_sat"],
    )
    return _capped_pressure_Pa(carrier, case["surface"])


@pytest.mark.parametrize("case", _CASES, ids=lambda case: f"{case['branch']}-{case['name']}")
def test_surface_log_pressure_is_a_power_or_a_flagged_floor(case: dict) -> None:
    """Both branches. A positive log-pressure is never a silent 0."""

    kind, expected, status = _oracle(case)
    if kind == "raise":
        with pytest.raises(ValueError, match="langmuir pressure law overflowed"):
            _call(case)
        return
    assert expected is not None
    pressure, continuation = _call(case)
    assert pressure == expected
    assert pressure > 0.0
    assert continuation == status
    if case["name"] == "representable_below_minus_300":
        assert pressure == _REVIEWER_PA
        assert math.log10(pressure) < -300.0
    if case["name"] == "representable_above_plus_300":
        assert _case_log(case) > 300.0
    if case["expect"] == "underflow":
        assert pressure == _minimum_positive_pa()
        assert continuation == _UNDERFLOW


def test_finite_logs_share_the_catalog_endpoints() -> None:
    """The surface chokepoint does not keep a second cutoff.

    Overflow stays a refusal here, including the endpoint whose power
    raises ``OverflowError``. The catalog evaluator ceilings that
    overflow. Underflow and every representable power agree, including
    the old ±300 guards and the float adjacent to the endpoint.
    """

    from simulator.vapour_rail.catalog import realize_log10_pressure_pa

    endpoint = math.log10(_maximum_finite_pa())
    adjacent_below = math.nextafter(endpoint, 0.0)
    adjacent_above = math.nextafter(endpoint, math.inf)
    for log_p in (
        -330.0,
        -323.4,
        -323.0,
        -302.5,
        -300.0,
        0.0,
        300.0,
        306.0,
        adjacent_below,
    ):
        kind, expected, status = _exponentiation_class(log_p)
        assert kind == "value"
        catalog_pressure, catalog_status = realize_log10_pressure_pa(log_p)
        surface_pressure, surface_status = _surface_log_pressure(log_p)
        assert catalog_pressure == expected
        assert surface_pressure == catalog_pressure
        assert surface_status == catalog_status == status
        assert surface_pressure > 0.0
    assert adjacent_below < endpoint < adjacent_above
    assert 10.0 ** adjacent_below == 1.7976931348620926e308
    for log_p in (endpoint, adjacent_above, 400.0):
        assert _exponentiation_class(log_p)[0] == "raise"
        with pytest.raises(ValueError, match="langmuir pressure law overflowed"):
            _surface_log_pressure(log_p)
        ceiling, ceiling_status = realize_log10_pressure_pa(log_p)
        assert ceiling == _maximum_finite_pa()
        assert ceiling_status == "numerical_overflow_ceiling"


def _overflow_endpoint_cases() -> tuple[dict, ...]:
    """Every path through the shared helper at the overflow boundary.

    The capped law enters the helper only when ``log_raw < 0``. ``log10``
    of a finite ``P_sat`` is at most the endpoint, so a negative
    ``log_raw`` cannot form the adjacent float above it. That cell is
    not an input. The endpoint itself is formable: a tiny negative
    ``log_raw`` rounds away when added to ``log10(P_sat)``.
    """

    endpoint = math.log10(_maximum_finite_pa())
    logs = {
        "adjacent_below": math.nextafter(endpoint, 0.0),
        "endpoint": endpoint,
        "adjacent_above": math.nextafter(endpoint, math.inf),
    }
    cases: list[dict] = []
    for name, log_p in logs.items():
        cases.append({"path": "helper", "name": name, "log": log_p})
        cases.append({"path": "surface", "name": name, "log": log_p})
        # n = -1/2 and surface/p_ref = 1e-2 contribute +1. P = 10**(log-1)
        # makes the formed log the boundary float.
        cases.append(
            {
                "path": "scaled",
                "name": name,
                "log": log_p,
                "branch": "scaled",
                "pressure": 10.0 ** (log_p - 1.0),
                "exponent": -0.5,
                "p_ref": 1.0,
                "surface": 1.0e-2,
            }
        )
    for name, p_sat in (
        ("adjacent_below", 1.7976931348620926e308),
        ("endpoint", _maximum_finite_pa()),
    ):
        cases.append(
            {
                "path": "capped",
                "name": name,
                "log": logs[name],
                "branch": "capped",
                "p_sat": p_sat,
                "raw": 1.0,
                "exponent": -1.0,
                "p_ref": 1.0,
                "surface": math.nextafter(1.0, math.inf),
            }
        )
    return tuple(cases)


@pytest.mark.parametrize(
    "case",
    _overflow_endpoint_cases(),
    ids=lambda case: f"{case['path']}-{case['name']}",
)
def test_overflow_endpoint_is_a_typed_ceiling_not_an_abort(case: dict) -> None:
    """Both laws and the helper. Exponentiation overflow is never an abort.

    The helper publishes the flagged ceiling. The surface laws refuse
    with ``ValueError``. The adjacent float below stays the power.
    """

    from simulator.vapour_rail.catalog import realize_log10_pressure_pa

    kind, expected, status = _exponentiation_class(case["log"])
    if case["name"] == "adjacent_below":
        assert kind == "value"
        assert expected == 1.7976931348620926e308
        assert status is None
    else:
        assert kind == "raise"

    if case["path"] == "helper":
        got, got_status = realize_log10_pressure_pa(case["log"])
        if kind == "raise":
            assert got == _maximum_finite_pa()
            assert got_status == "numerical_overflow_ceiling"
        else:
            assert got == expected
            assert got_status is None
        return

    if case["path"] in ("scaled", "capped"):
        assert _case_log(case) == case["log"]

    def call() -> tuple[float, str | None]:
        if case["path"] == "surface":
            return _surface_log_pressure(case["log"])
        return _call(case)

    if kind == "raise":
        with pytest.raises(ValueError, match="langmuir pressure law overflowed"):
            call()
        return
    pressure, continuation = call()
    assert pressure == expected
    assert continuation is None
    assert pressure == 1.7976931348620926e308


def test_log_pressure_cutoff_has_one_chokepoint() -> None:
    """Neither law keeps its own zero cutoff."""

    text = Path(__file__).resolve().parents[1].joinpath(
        "simulator/langmuir_surface_oxygen.py"
    ).read_text()
    scaled = text.split("def _scaled_pressure_Pa", 1)[1].split(
        "\ndef _capped_pressure_Pa", 1
    )[0]
    capped = text.split("def _capped_pressure_Pa", 1)[1].split(
        "\ndef _pressure_at_surface", 1
    )[0]
    assert "_surface_log_pressure(" in scaled
    assert "_surface_log_pressure(" in capped
    assert "return 0.0" not in scaled
    assert "return 0.0" not in capped
    assert "realize_log10_pressure_pa" in text


def _trace_catalog() -> dict:
    return {
        "metals": {
            "Ca": {
                "formula": "Ca",
                "molar_mass_g_mol": 40.08,
                "evaporation_alpha": {"value": 1.0},
                "pO2_exponent": -0.5,
            },
            "Zn": {
                "formula": "Zn",
                "evaporation_alpha": {"value": 0.0},
                "pO2_exponent": -0.5,
            },
            "Cd": {
                "formula": "Cd",
                "evaporation_alpha": {"value": 0.0},
                "pO2_exponent": -1.0,
            },
        },
        "oxide_vapors": {
            "O2": {"formula": "O2", "molar_mass_g_mol": 31.998},
        },
    }


def _close(pressures: dict, provenance: dict):
    return apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=2473.15,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=1.0e-9,
        legacy_catalog=_trace_catalog(),
        compiled_catalog=None,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
    )


def test_open_gate_publishes_trace_powers_and_flags_the_floor() -> None:
    """A zero-alpha trace does not move the Ca root.

    Zn is the reviewer's representable sub-cutoff power at the solved
    surface. Cd is below the IEEE minimum there, so the published
    pressure is the flagged floor. Neither is 0, and neither enters
    the residual (measured alpha 0).
    """

    ca_only = _close(
        {"Ca": 1000.0},
        {"Ca": {"pO2_bar": 1.0e-9, "pO2_exponent": -0.5}},
    )
    result = _close(
        {"Ca": 1000.0, "Zn": 1.0e-290, "Cd": 1.0e-310},
        {
            "Ca": {"pO2_bar": 1.0e-9, "pO2_exponent": -0.5},
            "Zn": {"pO2_bar": 1.0e-30, "pO2_exponent": -0.5},
            "Cd": {"pO2_bar": 1.0e-20, "pO2_exponent": -1.0},
        },
    )
    assert result.applied is True
    assert result.surface_pO2_bar == ca_only.surface_pO2_bar
    assert result.pressures_Pa["Ca"] == ca_only.pressures_Pa["Ca"]
    assert result.pressures_Pa["Ca"] > 1.0
    surface = result.surface_pO2_bar
    assert surface is not None

    zn_log = math.log10(1.0e-290) + (-0.5) * math.log10(surface / 1.0e-30)
    cd_log = math.log10(1.0e-310) + (-1.0) * math.log10(surface / 1.0e-20)
    assert -323.3 < zn_log < -300.0
    assert cd_log < math.log10(_minimum_positive_pa())
    assert result.pressures_Pa["Zn"] == 10.0 ** zn_log
    assert result.pressures_Pa["Zn"] > 0.0
    assert "Zn" not in result.pressure_continuations
    assert result.pressures_Pa["Cd"] == _minimum_positive_pa()
    assert result.pressure_continuations["Cd"] == _UNDERFLOW
    notice = result.as_diagnostic()
    assert notice["pressure_continuations"]["Cd"] == _UNDERFLOW
    assert "numerical_underflow_floor=Cd" in str(result.warning)
    assert "Zn" not in result.refused_species
    assert "Cd" not in result.refused_species
