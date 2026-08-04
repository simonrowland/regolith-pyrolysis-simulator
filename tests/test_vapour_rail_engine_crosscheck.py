from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.vapour_rail_engine_crosscheck import write_reports
from simulator.vapour_rail.engine_crosscheck import (
    CrosscheckComposition,
    EngineCrosscheckError,
    build_crosscheck_report,
    divergence_label,
    render_crosscheck_markdown,
    run_engine_crosscheck,
    validate_fo2_grid,
    validate_temperature_grid,
)


COMPOSITION = CrosscheckComposition(
    composition_id="test_melt",
    composition_wt_pct={"SiO2": 90.0, "Na2O": 10.0},
    composition_mol={"SiO2": 9.0, "Na2O": 1.0},
)


class _FakeRailProvider:
    def __init__(self) -> None:
        self.requests = []

    def dispatch(self, request):
        self.requests.append(request)
        log_fo2 = float(request.fO2_log)
        temperature_K = float(request.temperature_C) + 273.15
        temperature_factor = 10.0 ** ((temperature_K - 1350.0) / 1000.0)
        return SimpleNamespace(
            status="ok",
            diagnostic={
                "vapor_pressures_Pa": {
                    "SiO": temperature_factor * 10.0 ** (-0.5 * log_fo2),
                    "Na": 4.0 * temperature_factor,
                    "RailOnly": 2.0,
                },
                "vapor_pressures_source": {
                    "SiO": "fake_rail",
                    "Na": "fake_rail",
                    "RailOnly": "fake_rail",
                },
            },
        )


class _FakeWarmVapoRock:
    def __init__(self, *, warm: bool = True) -> None:
        self.uses_warm_pool = warm
        self.calls = []

    def is_available(self) -> bool:
        return True

    def get_vapor_species(self):
        return ["SiO", "Na", "VROnly"]

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        log_fo2 = float(kwargs["fO2_log"])
        temperature_K = float(kwargs["temperature_C"]) + 273.15
        temperature_factor = 10.0 ** ((temperature_K - 1350.0) / 1000.0)
        rail_sio = temperature_factor * 10.0 ** (-0.5 * log_fo2)
        return SimpleNamespace(
            status="non_authoritative",
            warnings=["pressure control is diagnostic-only"],
            vaporock_full_speciation_Pa={
                "SiO": rail_sio / 1000.0,
                "Na": temperature_factor * 10.0 ** (-0.25 * log_fo2),
                "VROnly": 3.0,
            },
            vapor_pressures_Pa={},
        )

    def close(self) -> None:
        raise AssertionError("injected backend must not be closed by the runner")


def test_domain_grid_refuses_before_any_engine_call():
    with pytest.raises(EngineCrosscheckError, match="validated"):
        validate_temperature_grid([1349.999, 1500.0])
    with pytest.raises(EngineCrosscheckError, match="validated"):
        validate_temperature_grid([1500.0, 1950.001])
    assert validate_temperature_grid([1950.0, 1350.0]) == (1350.0, 1950.0)

    rail = _FakeRailProvider()
    vaporock = _FakeWarmVapoRock()
    with pytest.raises(EngineCrosscheckError, match="validated"):
        run_engine_crosscheck(
            composition=COMPOSITION,
            temperatures_K=[1349.0],
            rail_provider=rail,
            rail_declared_species=["SiO"],
            vaporock_backend=vaporock,
            vaporock_declared_species=["SiO"],
        )
    assert rail.requests == []
    assert vaporock.calls == []


def test_fo2_grid_refuses_floor_substitution():
    with pytest.raises(EngineCrosscheckError, match="floor"):
        validate_fo2_grid([-10.0, -9.0])
    assert validate_fo2_grid([-7.0, -9.0, -8.0]) == (-9.0, -8.0, -7.0)


def test_runner_hard_requires_vr5_warm_pool():
    with pytest.raises(Exception, match="warm pool"):
        run_engine_crosscheck(
            composition=COMPOSITION,
            temperatures_K=[1350.0],
            rail_provider=_FakeRailProvider(),
            rail_declared_species=["SiO"],
            vaporock_backend=_FakeWarmVapoRock(warm=False),
            vaporock_declared_species=["SiO"],
        )


def test_runner_reports_magnitude_slopes_and_asymmetries_without_verdict():
    rail = _FakeRailProvider()
    vaporock = _FakeWarmVapoRock()
    report = run_engine_crosscheck(
        composition=COMPOSITION,
        temperatures_K=[1350.0, 1450.0],
        fo2_log10_bar=[-9.0, -8.0, -7.0],
        rail_provider=rail,
        rail_declared_species=["SiO", "Na", "RailOnly"],
        vaporock_backend=vaporock,
        vaporock_declared_species=["SiO", "Na", "VROnly"],
        generated_at="2026-08-03T00:00:00+00:00",
    )

    assert report["verdict"] is None
    assert report["certifies"] is False
    assert report["calibrates"] is False
    summaries = {row["species"]: row for row in report["species_summaries"]}
    assert summaries["SiO"]["matched_point_count"] == 6
    assert summaries["SiO"]["median_delta_dex"] == pytest.approx(3.0)
    assert summaries["SiO"]["divergence_label"] == "wild_ge_2_dex"
    assert summaries["SiO"]["fo2_dependence"]["median_rail_slope"] == pytest.approx(
        -0.5
    )
    assert summaries["SiO"]["fo2_dependence"][
        "median_vaporock_slope"
    ] == pytest.approx(-0.5)
    assert summaries["Na"]["fo2_dependence"][
        "median_slope_difference"
    ] == pytest.approx(0.25)
    assert summaries["RailOnly"]["rail_answered_vaporock_not_count"] == 6
    assert summaries["VROnly"]["vaporock_answered_rail_not_count"] == 6

    assert len(rail.requests) == 6
    assert len(vaporock.calls) == 6
    for request, call in zip(rail.requests, vaporock.calls, strict=True):
        assert request.fO2_log == call["fO2_log"]
        assert request.control_inputs["intrinsic_fO2_log"] == call["fO2_log"]
        assert request.control_inputs["pO2_bar"] == pytest.approx(
            10.0 ** call["fO2_log"]
        )
        assert request.account_view.accounts["process.cleaned_melt"] == dict(
            call["composition_mol"]
        )
        assert "liquid_fraction" not in call


def test_censored_pressures_remain_intervals_and_do_not_enter_slopes():
    raw_cells = [
        {
            "temperature_K": 1500.0,
            "fo2_log10_bar": -9.0,
            "rail": {
                "status": "ok",
                "reason": None,
                "pressures_Pa": {"SiO": 1.0e-31},
            },
            "vaporock": {
                "status": "non_authoritative",
                "reason": None,
                "pressures_Pa": {"SiO": 1.0},
            },
        },
        {
            "temperature_K": 1500.0,
            "fo2_log10_bar": -8.0,
            "rail": {"status": "ok", "reason": None, "pressures_Pa": {"SiO": 2.0}},
            "vaporock": {
                "status": "non_authoritative",
                "reason": None,
                "pressures_Pa": {"SiO": 1.0},
            },
        },
    ]
    report = build_crosscheck_report(
        composition=COMPOSITION,
        temperatures_K=[1500.0],
        fo2_log10_bar=[-9.0, -8.0],
        raw_cells=raw_cells,
        rail_declared_species=["SiO"],
        vaporock_declared_species=["SiO"],
        p_floor_Pa=1.0e-30,
        generated_at="fixed",
    )
    summary = report["species_summaries"][0]
    assert summary["matched_point_count"] == 1
    assert summary["fo2_dependence"]["per_temperature"] == []
    censored = next(row for row in report["rows"] if row["fo2_log10_bar"] == -9.0)
    assert censored["rail"]["kind"] == "censored_sub_floor"
    assert censored["delta_log10_rail_minus_vaporock_dex"] is None


def test_wild_disagreement_is_plainly_rendered_and_reports_are_deterministic(
    tmp_path: Path,
):
    assert divergence_label(2.0) == "wild_ge_2_dex"
    report = run_engine_crosscheck(
        composition=COMPOSITION,
        temperatures_K=[1350.0],
        rail_provider=_FakeRailProvider(),
        rail_declared_species=["SiO", "Na", "RailOnly"],
        vaporock_backend=_FakeWarmVapoRock(),
        vaporock_declared_species=["SiO", "Na", "VROnly"],
        generated_at="fixed",
    )
    markdown = render_crosscheck_markdown(report)
    assert "**SiO**" in markdown
    assert "3.000 dex" in markdown
    assert "cannot pass or fail" in markdown

    json_path, markdown_path = write_reports(report, tmp_path)
    assert json.loads(json_path.read_text()) == report
    assert markdown_path.read_text() == markdown
