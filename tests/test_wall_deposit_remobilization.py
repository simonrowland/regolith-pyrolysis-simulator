from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest

from simulator.condensation import CONDENSATION_TEMPS_C
from simulator.diagnostics import wall_deposit_remobilization_by_segment_species
from simulator.optimize.objective import _coating_product_summary


def _snapshot(
    hour: int,
    *,
    wall_delta: dict[tuple[str, str], float] | None = None,
):
    return SimpleNamespace(
        hour=hour,
        wall_deposit_by_segment_species_delta=dict(wall_delta or {}),
    )


def _sim(
    *,
    operating_history: list[dict],
    snapshots: tuple,
):
    return SimpleNamespace(
        condensation_model=SimpleNamespace(
            operating_history=operating_history,
            condensation_temperatures_C=dict(CONDENSATION_TEMPS_C),
        ),
        record=SimpleNamespace(snapshots=snapshots),
    )


def _assert_threshold_row_semantics(row: dict) -> None:
    assert row["re_evaporated_kg"] is None
    assert row["pressure_and_flux_modeled"] is False
    assert "remobilized" not in row
    assert "thermal_remobilization_threshold_exceeded" in row


def test_threshold_exceeded_true_when_later_temperature_exceeds_condensation_setpoint() -> None:
    segment = "stage_1_to_stage_2"
    species = "Na"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C + 25.0

    sim = _sim(
        snapshots=(
            _snapshot(1, wall_delta={(segment, species): 0.01}),
            _snapshot(2),
            _snapshot(3),
        ),
        operating_history=[
            {
                "hour": 1,
                "pipe_segment_temperatures_C": {segment: condensation_T_C - 50.0},
            },
            {
                "hour": 2,
                "pipe_segment_temperatures_C": {segment: later_T_C},
            },
            {
                "hour": 3,
                "pipe_segment_temperatures_C": {segment: condensation_T_C - 10.0},
            },
        ],
    )

    result = wall_deposit_remobilization_by_segment_species(sim)
    row = result[segment][species]

    assert row["deposited_kg"] == pytest.approx(0.01)
    assert row["deposit_last_hour"] == 1
    assert row["condensation_T_C"] == pytest.approx(condensation_T_C)
    assert row["later_max_T_C"] == pytest.approx(later_T_C)
    assert row["thermal_remobilization_threshold_exceeded"] is True
    _assert_threshold_row_semantics(row)


def test_threshold_exceeded_false_when_later_temperature_stays_below_setpoint() -> None:
    segment = "stage_1_to_stage_2"
    species = "Na"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C - 25.0

    sim = _sim(
        snapshots=(
            _snapshot(1, wall_delta={(segment, species): 0.02}),
            _snapshot(2),
        ),
        operating_history=[
            {
                "hour": 1,
                "pipe_segment_temperatures_C": {segment: condensation_T_C - 100.0},
            },
            {
                "hour": 2,
                "pipe_segment_temperatures_C": {segment: later_T_C},
            },
        ],
    )

    result = wall_deposit_remobilization_by_segment_species(sim)
    row = result[segment][species]

    assert row["later_max_T_C"] == pytest.approx(later_T_C)
    assert row["later_max_T_C"] < condensation_T_C
    assert row["thermal_remobilization_threshold_exceeded"] is False
    _assert_threshold_row_semantics(row)


def test_no_later_hours_yields_none_temperature_and_threshold_not_exceeded() -> None:
    segment = "duct_hot"
    species = "Fe"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])

    sim = _sim(
        snapshots=(_snapshot(3, wall_delta={(segment, species): 0.5}),),
        operating_history=[
            {
                "hour": 3,
                "pipe_segment_temperatures_C": {segment: condensation_T_C + 500.0},
            },
        ],
    )

    result = wall_deposit_remobilization_by_segment_species(sim)
    row = result[segment][species]

    assert row["deposit_last_hour"] == 3
    assert row["later_max_T_C"] is None
    assert row["thermal_remobilization_threshold_exceeded"] is False
    _assert_threshold_row_semantics(row)


def test_missing_segment_temperature_is_graceful() -> None:
    segment = "duct_hot"
    species = "Mg"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])

    sim = _sim(
        snapshots=(
            _snapshot(1, wall_delta={(segment, species): 0.03}),
            _snapshot(2),
        ),
        operating_history=[
            {"hour": 1, "pipe_segment_temperatures_C": {segment: 400.0}},
            {"hour": 2, "pipe_segment_temperatures_C": {}},
        ],
    )

    result = wall_deposit_remobilization_by_segment_species(sim)
    row = result[segment][species]

    assert row["condensation_T_C"] == pytest.approx(condensation_T_C)
    assert row["later_max_T_C"] is None
    assert row["thermal_remobilization_threshold_exceeded"] is False
    _assert_threshold_row_semantics(row)


def test_through_hour_limits_later_temperature_window() -> None:
    segment = "stage_1_to_stage_2"
    species = "K"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])

    sim = _sim(
        snapshots=(
            _snapshot(1, wall_delta={(segment, species): 0.04}),
            _snapshot(2),
            _snapshot(3),
        ),
        operating_history=[
            {"hour": 1, "pipe_segment_temperatures_C": {segment: 300.0}},
            {"hour": 2, "pipe_segment_temperatures_C": {segment: condensation_T_C - 5.0}},
            {"hour": 3, "pipe_segment_temperatures_C": {segment: condensation_T_C + 100.0}},
        ],
    )

    result = wall_deposit_remobilization_by_segment_species(
        sim,
        through_hour=2,
    )
    row = result[segment][species]

    assert row["later_max_T_C"] == pytest.approx(condensation_T_C - 5.0)
    assert row["thermal_remobilization_threshold_exceeded"] is False
    _assert_threshold_row_semantics(row)


def test_pressure_knudsen_invariant_threshold_flag_is_temperature_only() -> None:
    segment = "stage_1_to_stage_2"
    species = "Na"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C + 50.0
    snapshots = (
        _snapshot(1, wall_delta={(segment, species): 0.01}),
        _snapshot(2),
    )

    low_pressure_history = [
        {
            "hour": 1,
            "pipe_segment_temperatures_C": {segment: 400.0},
            "pressure_mbar": 1e-9,
            "knudsen_number": 1e9,
            "regime_factor": 1.0,
        },
        {
            "hour": 2,
            "pipe_segment_temperatures_C": {segment: later_T_C},
            "pressure_mbar": 1e-9,
            "knudsen_number": 1e9,
            "regime_factor": 1.0,
        },
    ]
    high_pressure_history = [
        {
            "hour": 1,
            "pipe_segment_temperatures_C": {segment: 400.0},
            "pressure_mbar": 1000.0,
            "knudsen_number": 1e-6,
            "regime_factor": 0.0,
        },
        {
            "hour": 2,
            "pipe_segment_temperatures_C": {segment: later_T_C},
            "pressure_mbar": 1000.0,
            "knudsen_number": 1e-6,
            "regime_factor": 0.0,
        },
    ]

    row_low = wall_deposit_remobilization_by_segment_species(
        _sim(snapshots=snapshots, operating_history=low_pressure_history),
    )[segment][species]
    row_high = wall_deposit_remobilization_by_segment_species(
        _sim(snapshots=snapshots, operating_history=high_pressure_history),
    )[segment][species]

    assert row_low["thermal_remobilization_threshold_exceeded"] is True
    assert row_high["thermal_remobilization_threshold_exceeded"] is True
    assert row_low["later_max_T_C"] == row_high["later_max_T_C"]
    _assert_threshold_row_semantics(row_low)
    _assert_threshold_row_semantics(row_high)


def test_coating_product_summary_surfaces_threshold_field_not_mass_transfer() -> None:
    segment = "hot_wall"
    species = "SiO"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C + 75.0
    run = SimpleNamespace(
        simulator=_sim(
            snapshots=(
                _snapshot(1, wall_delta={(segment, species): 0.25}),
                _snapshot(2),
            ),
            operating_history=[
                {"hour": 1, "pipe_segment_temperatures_C": {segment: 900.0}},
                {"hour": 2, "pipe_segment_temperatures_C": {segment: later_T_C}},
            ],
        ),
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={(segment, species): 0.25},
            wall_zone_by_segment={segment: "Hot"},
        ),
    )

    coating = _coating_product_summary(run)

    assert coating["wall_deposit_kg_by_segment_species"][segment][species] == pytest.approx(
        0.25
    )
    remobilization = coating["wall_deposit_remobilization_by_segment_species"]
    assert isinstance(remobilization, MappingProxyType)
    row = remobilization[segment][species]
    assert row["condensation_T_C"] == pytest.approx(condensation_T_C)
    assert row["later_max_T_C"] == pytest.approx(later_T_C)
    assert row["thermal_remobilization_threshold_exceeded"] is True
    _assert_threshold_row_semantics(row)


def test_campaign_hour_resolution_matches_production_path() -> None:
    # Real runs append operating_history with `campaign_hour` (float), NOT `hour`
    # (condensation route passes campaign_hour=float(self.melt.campaign_hour)). This
    # exercises the live production hour-resolution branch the other fixtures skip.
    segment = "stage_1_to_stage_2"
    species = "Na"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C + 25.0

    sim = _sim(
        snapshots=(
            _snapshot(1, wall_delta={(segment, species): 0.01}),
            _snapshot(2),
            _snapshot(3),
        ),
        operating_history=[
            {"campaign_hour": 1.0, "pipe_segment_temperatures_C": {segment: condensation_T_C - 50.0}},
            {"campaign_hour": 2.0, "pipe_segment_temperatures_C": {segment: later_T_C}},
            {"campaign_hour": 3.0, "pipe_segment_temperatures_C": {segment: condensation_T_C - 10.0}},
        ],
    )

    row = wall_deposit_remobilization_by_segment_species(sim)[segment][species]

    assert row["deposit_last_hour"] == 1
    assert row["later_max_T_C"] == pytest.approx(later_T_C)
    assert row["thermal_remobilization_threshold_exceeded"] is True
    _assert_threshold_row_semantics(row)


def test_index_fallback_resolution_when_no_explicit_hour() -> None:
    # Latent guard: operating_history entries with neither `hour` nor `campaign_hour`
    # fall back to index-alignment with the snapshot hour sequence. Use nontrivial
    # snapshot hours (5,6,7) so a naive 0-based fallback would mis-tag.
    segment = "stage_1_to_stage_2"
    species = "Na"
    condensation_T_C = float(CONDENSATION_TEMPS_C[species])
    later_T_C = condensation_T_C + 40.0

    sim = _sim(
        snapshots=(
            _snapshot(5, wall_delta={(segment, species): 0.01}),
            _snapshot(6),
            _snapshot(7),
        ),
        operating_history=[
            {"pipe_segment_temperatures_C": {segment: condensation_T_C - 60.0}},  # -> hour 5
            {"pipe_segment_temperatures_C": {segment: later_T_C}},                # -> hour 6
            {"pipe_segment_temperatures_C": {segment: condensation_T_C - 20.0}},  # -> hour 7
        ],
    )

    row = wall_deposit_remobilization_by_segment_species(sim)[segment][species]

    assert row["deposit_last_hour"] == 5
    assert row["later_max_T_C"] == pytest.approx(later_T_C)
    assert row["thermal_remobilization_threshold_exceeded"] is True
    _assert_threshold_row_semantics(row)