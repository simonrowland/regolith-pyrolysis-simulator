from __future__ import annotations

from textwrap import dedent

from simulator.ceramic_classifier import classify_ceramic_rump


def test_forsterite_point_anchor_classifies_with_explicit_tolerance():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 42.7},
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "forsterite"
    assert result.match.composition_kind == "point-anchor"


def test_off_window_composition_returns_no_match():
    result = classify_ceramic_rump(
        {"Na2O": 100.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "no-match"
    assert result.match is None


def test_point_anchor_does_not_accept_broad_extra_oxide_composition():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 40.7, "Al2O3": 2.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "no-match"
    assert result.match is None


def test_window_does_not_accept_doloma_with_extra_silica():
    result = classify_ceramic_rump(
        {"CaO": 42.0, "MgO": 32.0, "SiO2": 26.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "no-match"
    assert result.match is None


def test_window_does_not_accept_mullite_with_extra_magnesia():
    result = classify_ceramic_rump(
        {"Al2O3": 72.0, "SiO2": 27.0, "MgO": 20.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "no-match"
    assert result.match is None


def test_overlapping_source_windows_return_ambiguous(tmp_path):
    data_path = tmp_path / "ceramic_types.yaml"
    data_path.write_text(
        dedent(
            """
            ceramics:
              alpha_window:
                label: "Alpha window"
                composition:
                  kind: window
                  defining_oxides: ["CaO", "Al2O3"]
                  wt_pct_window:
                    CaO: [20.0, 30.0]
                    Al2O3: [70.0, 80.0]
              beta_anchor:
                label: "Beta anchor"
                composition:
                  kind: point-anchor
                  defining_oxides: ["CaO", "Al2O3"]
                  wt_pct: {CaO: 25.0, Al2O3: 75.0}
            """
        )
    )

    result = classify_ceramic_rump(
        {"CaO": 25.0, "Al2O3": 75.0},
        data_path=data_path,
    )

    assert result.status == "ambiguous"
    assert result.match is None
    assert "alpha_window" in result.reason
    assert "beta_anchor" in result.reason


def test_melting_only_service_temp_is_not_usable_service_rating():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 42.7},
        tolerance_wt_pct=0.1,
    )

    assert result.match is not None
    assert result.match.service_temp.kind == "melting-only"
    assert result.match.service_temp.value_C == 1890
    assert result.match.service_temp.usable_service_C is None
