"""MAGEMin matlab oxide rows become phase compositions in wt%.

The SYS bulk-echo reader still returns weight fractions. A bad non-SYS
row does not drop SYS. Expected wt% values are the printed fraction
times 100, written as literals.
"""

from __future__ import annotations

from simulator.melt_backend.magemin import (
    MAGEMinBackend,
    _assert_magemin_bulk_echo,
    _attach_magemin_oxide_compositions,
    _parse_magemin_oxide_block,
    _parse_magemin_sys_oxide_row,
)


_OXIDES = ("SiO2", "MgO", "FeO")


def _block(*rows: tuple[str, ...]) -> str:
    header = "  " + "  ".join(f"{name:>8}" for name in _OXIDES)
    body = [f"Oxide compositions [wt fr]:\n{header}"]
    for row in rows:
        body.append("   " + "  ".join(f"{token:>8}" for token in row))
    return "\n".join(body) + "\n"


def test_sys_row_stays_weight_fraction_and_a_bad_phase_row_does_not_drop_it():
    text = _block(
        ("ol", "nan", "0.60000", "0.40000"),
        ("SYS", "0.50000", "0.25000", "0.25000"),
    )
    sys_row = _parse_magemin_sys_oxide_row(text)
    assert sys_row == {"SiO2": 0.5, "MgO": 0.25, "FeO": 0.25}
    _assert_magemin_bulk_echo({"SiO2": 50.0, "MgO": 25.0, "FeO": 25.0}, text)
    rows, notices = _parse_magemin_oxide_block(text)
    assert rows is not None
    assert "ol" not in rows
    assert any(
        notice["kind"] == "phase_composition_unparsable" and notice["phase"] == "ol"
        for notice in notices
    )


def test_sys_nan_is_still_unparseable():
    text = _block(("SYS", "nan", "0.50000", "0.50000"))
    assert _parse_magemin_sys_oxide_row(text) is None


def test_phase_rows_are_weight_percent_and_buffers_are_skipped():
    text = _block(
        ("liq", "0.50000", "0.25000", "0.25000"),
        ("ol", "0.40000", "0.60000", "0.00000"),
        ("qfm", "0.00000", "0.00000", "0.00000"),
        ("SYS", "0.50000", "0.25000", "0.25000"),
    )
    phases = {
        "liq": {"mass_kg": 0.8},
        "ol": {"mass_kg": 0.2},
        "qfm": {"mass_kg": 0.01},
    }
    notices = _attach_magemin_oxide_compositions(phases, text)
    assert notices == ()
    assert phases["liq"]["composition_wt_pct"] == {
        "SiO2": 50.0,
        "MgO": 25.0,
        "FeO": 25.0,
    }
    assert phases["ol"]["composition_wt_pct"] == {
        "SiO2": 40.0,
        "MgO": 60.0,
        "FeO": 0.0,
    }
    assert "composition_wt_pct" not in phases["qfm"]


def test_duplicate_phase_row_is_omitted_not_averaged():
    text = _block(
        ("liq", "0.20000", "0.30000", "0.50000"),
        ("liq", "0.80000", "0.10000", "0.10000"),
        ("SYS", "0.50000", "0.25000", "0.25000"),
    )
    phases = {"liq": {"mass_kg": 1.0}}
    notices = _attach_magemin_oxide_compositions(phases, text)
    assert "composition_wt_pct" not in phases["liq"]
    assert _parse_magemin_sys_oxide_row(text)["SiO2"] == 0.5
    assert any(
        notice["kind"] == "duplicate_phase_composition" and notice["phase"] == "liq"
        for notice in notices
    )


def test_missing_oxide_block_keeps_the_mode_and_flags():
    phases = {"liq": {"mass_kg": 1.0}}
    notices = _attach_magemin_oxide_compositions(phases, "Phase : liq\nMode : 1\n")
    assert phases == {"liq": {"mass_kg": 1.0}}
    assert notices[0]["kind"] == "phase_composition_not_printed"


def test_extract_drops_nonpositive_and_two_liquids_do_not_guess():
    raw = {
        "phases": {
            "liq": {
                "mass_kg": 0.5,
                "composition_wt_pct": {"SiO2": 50.0, "MgO": 0.0},
            },
            "liquid": {
                "mass_kg": 0.5,
                "composition_wt_pct": {"SiO2": 80.0, "MgO": 20.0},
            },
        }
    }
    (
        _present,
        _masses,
        compositions,
        _fraction,
        liquid,
    ) = MAGEMinBackend()._phase_assemblage_payload(raw)
    assert compositions["liq"] == {"SiO2": 50.0}
    assert "MgO" not in compositions["liq"]
    assert compositions["liquid"]["SiO2"] == 80.0
    assert liquid == {}
