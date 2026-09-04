"""Hourly simulation_tick payload must not grow with accumulated VR blobs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module
import web.events as web_events
from simulator.diagnostics import (
    condensation_authority_diagnostic,
    condensation_refusals_diagnostic,
    vapour_rail_instrumentation_diagnostic,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from web.advisory import (
    _compact_condensation_refusals,
    _compact_record,
    condensation_refusals_panel_payload,
    vapour_rail_instrumentation_panel_payload,
)
from web.events import _clear_simulation_state, _simulations


_ROOT = Path(__file__).resolve().parents[1]


def _nbytes(obj) -> int:
    return len(json.dumps(obj, default=str))


def _force_internal_analytical(monkeypatch) -> list:
    captured_tasks: list = []

    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    monkeypatch.setattr(
        app_module.socketio, "start_background_task", capture_background_task
    )
    monkeypatch.setattr(web_events, "_safe_log", lambda _message: None)
    return captured_tasks


# Slope bound for SAWTOOTH growth, in bytes/hour. The two checks below do
# different jobs and neither subsumes the other:
#   * the interval check catches MONOTONIC growth of any size, down to
#     1 byte/hour, and needs no threshold;
#   * this slope bound catches a rising TREND that a single dip would otherwise
#     excuse, which the interval check cannot see at all.
#
# CALIBRATED FROM MEASURED DATA, not chosen for tidiness. A first attempt at
# 1.0 byte/hour fired on the real series -- [(3, 46133), (4, 46151), (5, 46149),
# (6, 46147)], slope 4.0 -- which is a flat ~46.1 KB payload with an 18-byte
# content step and three of four intervals NEGATIVE. That is correct work, and a
# guard that fires on correct work gets deleted. The 1.0 figure came from a
# perfectly monotonic counterfactual, where the interval check already fires.
#
# 100 bytes/hour sits ~25x above the measured jitter slope and ~74x below the
# sawtooth case pinned below (7428). Over a 35-hour run it admits 3.5 KB on a
# 46 KB payload (7.6%) while rejecting anything that would rebuild the megabyte
# payloads that made runs unusable.
SAWTOOTH_SLOPE_BYTES_PER_HOUR = 100.0


def _assert_hourly_payload_slope_is_bounded(
    event: str,
    samples: list[tuple[int, int]],
) -> None:
    ordered = sorted(samples)
    assert len(ordered) >= 4, (event, ordered)
    hours = [hour for hour, _size in ordered]
    sizes = [size for _hour, size in ordered]
    mean_hour = sum(hours) / len(hours)
    mean_size = sum(sizes) / len(sizes)
    denominator = sum((hour - mean_hour) ** 2 for hour in hours)
    assert denominator > 0, (event, ordered)
    slope = sum(
        (hour - mean_hour) * (size - mean_size)
        for hour, size in ordered
    ) / denominator
    interval_slopes = [
        (next_size - size) / (next_hour - hour)
        for (hour, size), (next_hour, next_size) in zip(ordered, ordered[1:])
    ]
    # The interval check alone is a SAWTOOTH HOLE: it passes as soon as ONE
    # interval is non-positive, whatever the trend. A payload climbing
    # 10 KB/hour with a single one-byte dip -- 10 KB to 50 KB over six hours,
    # least-squares slope 7428 bytes/hour -- passed this gate unchanged, and the
    # slope it needed was already computed above and used only in the message.
    # Real payload sizes jitter with content, so a non-positive interval is the
    # NORMAL case, which made the guard close to vacuous in production exactly
    # where it mattered: unbounded tick payloads are what made runs unusable.
    #
    # Assert the computed trend. Both checks are kept deliberately: the interval
    # check still rejects a monotonic climb far too gentle to reach this bound
    # (1 byte/hour), and this bound catches a steep trend the interval check
    # excuses. See SAWTOOTH_SLOPE_BYTES_PER_HOUR for the calibration.
    assert slope < SAWTOOTH_SLOPE_BYTES_PER_HOUR, (
        f"{event} payload grew {slope:.1f} bytes/hour "
        f"(sawtooth bound {SAWTOOTH_SLOPE_BYTES_PER_HOUR}); samples={ordered}"
    )
    assert not all(interval_slope > 0.0 for interval_slope in interval_slopes), (
        f"{event} payload grew {slope:.1f} bytes/hour; samples={ordered}"
    )


def test_hourly_payload_gate_rejects_linear_1000_bytes_per_hour() -> None:
    with pytest.raises(AssertionError, match="grew 1000.0 bytes/hour"):
        _assert_hourly_payload_slope_is_bounded(
            "counterfactual",
            [(hour, 10_000 + 1_000 * hour) for hour in range(1, 7)],
        )


def test_hourly_payload_gate_rejects_smallest_sustained_growth_and_allows_jitter(
) -> None:
    with pytest.raises(AssertionError, match="grew 1.0 bytes/hour"):
        _assert_hourly_payload_slope_is_bounded(
            "counterfactual",
            [(hour, 10_000 + hour) for hour in range(1, 7)],
        )

    _assert_hourly_payload_slope_is_bounded(
        "bounded-jitter",
        [
            (1, 10_000),
            (2, 10_001),
            (3, 9_999),
            (4, 9_999),
            (5, 10_001),
            (6, 10_000),
        ],
    )


def test_advisory_js_fetches_on_demand_full_records() -> None:
    source = (_ROOT / "web/static/js/simulator-advisory.js").read_text(
        encoding="utf-8"
    )
    assert "advisory_panel_detail" in source
    assert "appendOnDemandAdvisoryDetail(" in source
    assert "renderVapourRailInstrumentationPanel(" in source
    assert "renderCondensationRefusalsPanel(" in source
    html = (_ROOT / "web/templates/simulator.html").read_text(encoding="utf-8")
    assert 'id="vapour-rail-instrumentation-panel"' in html
    assert 'id="condensation-refusals-panel"' in html


def test_advisory_panel_detail_http_requires_browser_session() -> None:
    app = app_module.create_app()
    client = app.test_client()
    missing = client.get(
        "/api/advisory-panel-detail?panel=condensation_refusals_panel"
    )
    assert missing.status_code == 400
    assert client.get("/").status_code == 200
    no_run = client.get(
        "/api/advisory-panel-detail?panel=condensation_refusals_panel"
    )
    assert no_run.status_code == 404


def test_condensation_refusals_tick_panel_keeps_counts_without_nested_blobs():
    raw = {
        "X": {
            "status": "refused",
            "reason": "antoine_data_unavailable",
            "output_status": "status_bearing",
            "carrier_authority": {
                "pressure": {"kind": "refusal", "code": "test_refusal"},
                "padding": "x" * 8000,
            },
            "stage_outcomes": [{"stage": 3, "status": "pass_through"}],
        }
    }
    sim = SimpleNamespace(
        condensation_model=SimpleNamespace(
            last_condensation_refusals_by_species=raw
        )
    )
    panel = condensation_refusals_panel_payload(sim)
    assert panel["n_species"] == 1
    assert panel["has_refusals"] is True
    assert set(panel["by_species"]) == {"X"}
    assert panel["by_species"]["X"]["reason"] == "antoine_data_unavailable"
    assert "carrier_authority" not in panel["by_species"]["X"]
    assert "stage_outcomes" not in panel["by_species"]["X"]
    assert _nbytes(panel["by_species"]) < 500
    from web.advisory import sc50_vr_socket_panel_detail

    detail = sc50_vr_socket_panel_detail(sim, "condensation_refusals_panel")
    assert detail["n_species"] == 1
    assert detail["by_species"]["X"]["carrier_authority"]["pressure"]["code"] == (
        "test_refusal"
    )
    assert detail["by_species"]["X"]["stage_outcomes"][0]["status"] == (
        "pass_through"
    )


def test_compact_condensation_refusals_does_not_invent_absent_answers():
    compact = _compact_condensation_refusals({
        "by_species": {
            "SiO": {"status": "refused", "reason": "test_refusal"},
        },
    })

    assert compact["n_species"] == 1
    assert compact["by_species"]["SiO"]["status"] == "refused"
    assert "has_refusals" not in compact
    assert compact["has_refusals_status"] == "unavailable"

    proven_zero = _compact_condensation_refusals({
        "has_refusals": False,
        "by_species": {},
    })
    assert proven_zero["has_refusals"] is False
    assert "has_refusals_status" not in proven_zero


def test_compact_record_does_not_invent_success_for_invalid_shape():
    assert _compact_record("not-a-record", ("status", "reason")) == {
        "status": "unavailable",
        "reason": "non_mapping_record",
    }


def test_vapour_rail_tick_panel_keeps_channel_index_without_full_answers():
    channel = {
        "species_id": "Na",
        "is_flux_active": True,
        "is_refused": False,
        "refusal_code": None,
        "pressure": {"kind": "ok", "padding": "p" * 4000},
        "flux": {"kind": "ok", "padding": "f" * 4000},
        "extra": {"blob": "e" * 4000},
    }
    sim = SimpleNamespace(
        _last_vapour_batch=None,
        _last_vapour_batch_report={
            "n_requested": 1,
            "n_flux_active": 1,
            "n_refused": 0,
            "solve_bundle_ids": {},
            "channels_by_species": {"Na": channel},
            "refusals_by_species": {},
        },
        _last_vapour_batch_flux_overlay={},
        _last_vapour_batch_resolve_error={},
        condensation_model=SimpleNamespace(
            last_condensation_refusals_by_species={},
            last_condensation_authority_by_species={},
        ),
    )
    panel = vapour_rail_instrumentation_panel_payload(sim)
    assert panel["n_requested"] == 1
    assert panel["n_flux_active"] == 1
    assert panel["n_refused"] == 0
    assert "Na" in panel["channels_by_species"]
    assert panel["channels_by_species"]["Na"]["is_flux_active"] is True
    assert "pressure" not in panel["channels_by_species"]["Na"]
    assert "flux" not in panel["channels_by_species"]["Na"]
    assert _nbytes(panel["channels_by_species"]) < 400


def test_simulation_tick_payload_does_not_grow_with_hour_count(monkeypatch):
    captured_tasks = _force_internal_analytical(monkeypatch)
    original_drive_session = web_events.drive_session
    original_emit = app_module.socketio.emit
    tick_samples: list[tuple[int, int]] = []
    wire_samples: list[tuple[str, int | None, int, str]] = []
    mismatches: list[str] = []

    def drive_with_large_hourly_diagnostics(*args, **kwargs):
        for result in original_drive_session(*args, **kwargs):
            summary = dict(result.per_hour_summary)
            hour = int(summary["hour"])
            summary["condensation_refusals_by_species"] = {
                "X": {
                    "status": "refused",
                    "reason": "antoine_data_unavailable",
                    "carrier_authority": {
                        "padding": "x" * (600_000 + 1_000 * hour),
                    },
                },
            }
            summary["vapour_batch_summary"] = {
                "n_requested": 1,
                "n_flux_active": 0,
                "n_refused": 1,
                "metadata": {
                    "padding": "y" * (600_000 + 1_000 * hour),
                },
            }
            yield replace(result, per_hour_summary=summary)

    monkeypatch.setattr(
        web_events,
        "drive_session",
        drive_with_large_hourly_diagnostics,
    )

    def instrumented_emit(event, payload, *args, **kwargs):
        if isinstance(payload, dict):
            emitted = dict(payload)
            serialized = json.dumps(emitted, default=str)
            hour_value = emitted.get("hour")
            hour = int(hour_value) if hour_value is not None else None
            wire_samples.append((event, hour, len(serialized), serialized))
        if event == "simulation_tick" and isinstance(payload, dict):
            emitted = dict(payload)
            tick_samples.append((int(emitted["hour"]), _nbytes(emitted)))
            sid = kwargs.get("room")
            state = _simulations.get(sid)
            sim = state["session"].simulator if state else None
            if sim is not None:
                _assert_tick_preserves_vr_totals(emitted, sim, mismatches)
        if event == "per_hour_summary" and isinstance(payload, dict):
            sid = kwargs.get("room")
            state = _simulations.get(sid)
            summary_count = sum(
                1 for name, _hour, _size, _serialized in wire_samples
                if name == "per_hour_summary"
            )
            if summary_count >= 6 and state is not None:
                state["running"] = False
        return original_emit(event, payload, *args, **kwargs)

    monkeypatch.setattr(app_module.socketio, "emit", instrumented_emit)

    app = app_module.create_app()
    http = app.test_client()
    assert http.get("/").status_code == 200
    client = app_module.socketio.test_client(app, flask_test_client=http)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        rounds = 0
        while len(tick_samples) < 6 and rounds < 40:
            rounds += 1
            if not captured_tasks:
                break
            target = captured_tasks.pop(0)
            target()
            for event in client.get_received():
                if event["name"] == "decision_required":
                    decision = event["args"][0]
                    client.emit(
                        "make_decision",
                        {"choice": decision.get("recommendation")},
                    )
        assert len(tick_samples) >= 6, tick_samples
        for event, hour, size, _serialized in wire_samples:
            assert size < 120_000, (event, hour, size)
        stable_tail = [size for _hour, size in tick_samples[2:6]]
        assert max(stable_tail) - min(stable_tail) <= 1024, tick_samples
        for event in ("simulation_tick", "per_hour_summary"):
            hourly = [
                (hour, size)
                for name, hour, size, _serialized in wire_samples
                if name == event and hour is not None
            ][:6]
            _assert_hourly_payload_slope_is_bounded(event, hourly[2:6])
        assert all(
            '"padding"' not in serialized
            for event, _hour, _size, serialized in wire_samples
            if event in {"simulation_tick", "per_hour_summary"}
        )
        active_sids = set(_simulations) - before
        assert len(active_sids) == 1
        state = _simulations[active_sids.pop()]
        captured_summary = state["last_recipe_capture"]["per_hour_summary"]
        assert '"padding"' in json.dumps(captured_summary, default=str)
        assert mismatches == []
    finally:
        if client.is_connected():
            client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def _assert_tick_preserves_vr_totals(tick: dict, sim, mismatches: list[str]) -> None:
    hour = tick.get("hour")
    panel = tick.get("vapour_rail_instrumentation_panel") or {}
    refusals_panel = tick.get("condensation_refusals_panel") or {}
    diag = vapour_rail_instrumentation_diagnostic(sim)
    batch = diag.get("vapour_batch") or {}
    if not isinstance(batch, dict):
        batch = {}
    condensation = condensation_refusals_diagnostic(sim)
    authority = condensation_authority_diagnostic(sim)

    def check(label: str, left, right) -> None:
        if left != right:
            mismatches.append(f"hour={hour} {label}: {left!r} != {right!r}")

    check("n_requested", panel.get("n_requested"), batch.get("n_requested", 0))
    check("n_flux_active", panel.get("n_flux_active"), batch.get("n_flux_active", 0))
    check("n_refused", panel.get("n_refused"), batch.get("n_refused", 0))
    check(
        "channel_species",
        set((panel.get("channels_by_species") or {})),
        set((batch.get("channels_by_species") or {})),
    )
    check(
        "refused_species",
        set((panel.get("refusals_by_species") or {})),
        set((batch.get("refusals_by_species") or {})),
    )
    check(
        "condensation_n_species",
        refusals_panel.get("n_species"),
        condensation.get("n_species", 0),
    )
    check(
        "condensation_has_refusals",
        bool(refusals_panel.get("has_refusals")),
        bool(condensation.get("has_refusals")),
    )
    check(
        "condensation_species",
        set((refusals_panel.get("by_species") or {})),
        set((condensation.get("by_species") or {})),
    )
    wire_authority = panel.get("condensation_authority") or {}
    check(
        "authority_n_species",
        wire_authority.get("n_species"),
        authority.get("n_species", 0),
    )
    check(
        "authority_status_counts",
        dict(wire_authority.get("status_counts") or {}),
        dict(authority.get("status_counts") or {}),
    )
    for record in (refusals_panel.get("by_species") or {}).values():
        if isinstance(record, dict) and "carrier_authority" in record:
            mismatches.append(
                f"hour={hour} condensation_refusals_panel still carries "
                "carrier_authority on the tick"
            )
            break


def test_hourly_payload_gate_rejects_sawtooth_growth() -> None:
    """A single non-positive interval must not excuse a rising trend.

    Regression: the gate asserted only that NOT ALL intervals were positive, so
    one one-byte dip defeated it entirely. This series climbs 10 KB to 50 KB in
    six hours -- least-squares slope 7428 bytes/hour -- and passed unchanged.
    Real payload sizes jitter with content, so a non-positive interval is the
    normal case; that made the guard close to vacuous exactly where unbounded
    tick payloads had already made runs unusable.
    """
    with pytest.raises(AssertionError, match="grew 7428.3 bytes/hour"):
        _assert_hourly_payload_slope_is_bounded(
            "sawtooth",
            [
                (1, 10_000),
                (2, 20_000),
                (3, 30_000),
                (4, 29_999),  # the single dip that used to excuse everything
                (5, 39_999),
                (6, 49_999),
            ],
        )


def test_hourly_payload_gate_still_allows_flat_jitter() -> None:
    """Anti-over-correction: the bound must not fire on correct work.

    A guard that rejects ordinary jitter gets deleted, so this holds the other
    side of the same edit -- flat content with +/-1 noise has slope 0.0 and must
    keep passing.
    """
    _assert_hourly_payload_slope_is_bounded(
        "flat-jitter",
        [(1, 46_000), (2, 46_002), (3, 45_998), (4, 46_001), (5, 45_999), (6, 46_000)],
    )
