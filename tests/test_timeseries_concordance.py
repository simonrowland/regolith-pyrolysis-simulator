from __future__ import annotations

import pytest

from simulator.diagnostic_helpers.timeseries_concordance import (
    dual_concordance,
    trapezoid_integral,
)


def test_integral_concordance_uses_exact_trapezoid_totals_per_species() -> None:
    report = dual_concordance(
        observed_trajectory={
            "K": [(0.0, 1.0), (2.0, 1.0)],
            "Na": [(0.0, 2.0), (2.0, 2.0)],
        },
        model_trajectory={
            "K": [(0.0, 1.0), (2.0, 1.0)],
            "Na": [(0.0, 1.0), (2.0, 1.0)],
        },
        trajectory_integral_is_rate=True,
    )
    by_species = {item.species: item for item in report.species}

    assert trapezoid_integral([(0.0, 0.0), (2.0, 2.0), (4.0, 0.0)]) == pytest.approx(4.0)
    assert by_species["Na"].observed_integral == pytest.approx(4.0)
    assert by_species["Na"].model_integral == pytest.approx(2.0)
    assert by_species["Na"].integral_score == pytest.approx(0.5)
    assert by_species["K"].observed_integral == pytest.approx(2.0)
    assert by_species["K"].integral_score == pytest.approx(1.0)
    assert report.integral_score == pytest.approx((4.0 * 0.5 + 2.0 * 1.0) / 6.0)


def test_saturated_cumulative_yield_separates_from_bad_rate_trajectory() -> None:
    report = dual_concordance(
        observed_trajectory={"Na": [(0.0, 1.0), (1.0, 1.0), (2.0, 1.0)]},
        model_trajectory={"Na": [(0.0, 3.5), (1.0, 3.5), (2.0, 3.5)]},
        observed_cumulative={"Na": [(0.0, 0.0), (1.0, 90.0), (2.0, 100.0)]},
        model_cumulative={"Na": [(0.0, 0.0), (1.0, 99.0), (2.0, 100.0)]},
        inventory={"Na": 100.0},
    )
    na = report.species[0]

    assert na.observed_integral == pytest.approx(100.0)
    assert na.model_integral == pytest.approx(100.0)
    assert report.headline_yield_score == pytest.approx(1.0)
    assert na.time_series_error_factor == pytest.approx(3.5)
    assert report.process_fidelity_score == pytest.approx(1.0 / 3.5)


def test_multi_species_time_series_uses_pointwise_geometric_error_factor() -> None:
    report = dual_concordance(
        observed_trajectory={
            "perfect": [(0.0, 1.0)],
            "wrong": [(0.0, 1.0)],
        },
        model_trajectory={
            "perfect": [(0.0, 1.0)],
            "wrong": [(0.0, 100.0)],
        },
        observed_integral={"perfect": 1.0, "wrong": 1.0},
        model_integral={"perfect": 1.0, "wrong": 1.0},
    )
    by_species = {item.species: item for item in report.species}

    assert by_species["perfect"].time_series_error_factor == pytest.approx(1.0)
    assert by_species["wrong"].time_series_error_factor == pytest.approx(100.0)
    assert report.time_series_error_factor == pytest.approx(10.0)
    assert report.process_fidelity_score == pytest.approx(0.1)


def test_time_series_metric_interpolates_model_to_observed_times() -> None:
    report = dual_concordance(
        observed_trajectory={"Mg": [(0.0, 2.0), (1.0, 2.0), (2.0, 2.0)]},
        model_trajectory={"Mg": [(0.0, 4.0), (2.0, 4.0)]},
        observed_integral={"Mg": 10.0},
        model_integral={"Mg": 9.0},
        inventory={"Mg": 10.0},
    )
    mg = report.species[0]

    assert mg.integral_relative_error == pytest.approx(0.1)
    assert report.integral_score == pytest.approx(0.9)
    assert mg.time_series_error_factor == pytest.approx(2.0)
    assert mg.time_series_score == pytest.approx(0.5)


def test_integral_only_dataset_reports_no_process_fidelity_score() -> None:
    report = dual_concordance(
        observed_cumulative={"B": [(0.0, 0.0), (10.0, 5.0)]},
        model_cumulative={"B": [(0.0, 0.0), (10.0, 4.5)]},
        inventory={"B": 10.0},
    )
    boron = report.species[0]

    assert boron.integral_absolute_error == pytest.approx(0.5)
    assert report.integral_relative_error == pytest.approx(0.05)
    assert report.integral_score == pytest.approx(0.95)
    assert boron.time_series_score is None
    assert report.process_fidelity_score is None


def test_trajectory_only_integral_source_requires_explicit_rate_flag() -> None:
    with pytest.raises(ValueError, match="trajectory_integral_is_rate=True"):
        dual_concordance(
            observed_trajectory={"SiO": [(0.0, 1.0), (1.0, 1.0)]},
            model_trajectory={"SiO": [(0.0, 1.0), (1.0, 1.0)]},
        )


def test_missing_model_trajectory_fails_closed() -> None:
    with pytest.raises(ValueError, match="incomplete time-series pair"):
        dual_concordance(
            observed_trajectory={"SiO": [(0.0, 1.0), (1.0, 1.0)]},
            observed_integral={"SiO": 1.0},
            model_integral={"SiO": 1.0},
        )
