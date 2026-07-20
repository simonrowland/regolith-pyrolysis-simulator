#!/usr/bin/env python3
"""Profile the deterministic operator-visible live web simulation path."""

from __future__ import annotations

import argparse
import cProfile
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pstats
import sys
import tempfile
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app as app_module
import simulator.runner as runner_module
import simulator.wall_advisor as wall_advisor_module
import web.events as web_events
import web.run_store as run_store_module
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from socketio import packet as socketio_packet


SETPOINT_OVERRIDES = {
    "C0": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C0B": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C2A": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C2B": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C3_K": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C3_NA": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C4": {"ramp_rate": 2000.0},
    "C5": {"ramp_rate": 2000.0},
}
FIXED_RUN_ID = "0123456789abcdef0123456789abcdef"
OUTPUT_EVENTS = {
    "campaign_complete_summary",
    "decision_required",
    "per_hour_summary",
    "simulation_complete",
    "simulation_persistence_failed",
    "simulation_status",
    "simulation_tick",
}


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 19, 12, 0, 0, tzinfo=tz)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _timed(name, function, totals, calls):
    def wrapper(*args, **kwargs):
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            totals[name] += perf_counter() - started
            calls[name] += 1

    return wrapper


def _legacy_wall_deposit_cumulative(sim, snapshot):
    cumulative = {}
    snapshots = tuple(getattr(getattr(sim, "record", None), "snapshots", ()) or ())
    found_snapshot = False
    for item in snapshots:
        if int(getattr(item, "hour", -1)) > int(snapshot.hour):
            break
        for key, kg in item.wall_deposit_by_segment_species_delta.items():
            cumulative[key] = cumulative.get(key, 0.0) + float(kg)
        if item is snapshot:
            found_snapshot = True
            break
    if not found_snapshot and snapshot not in snapshots:
        for key, kg in snapshot.wall_deposit_by_segment_species_delta.items():
            cumulative[key] = cumulative.get(key, 0.0) + float(kg)
    return runner_module._nested_species_kg_from_segment_species(cumulative)


def _profile_once(
    profile_path: Path,
    *,
    emulate_before: bool = False,
) -> dict[str, object]:
    totals: dict[str, float] = defaultdict(float)
    calls: dict[str, int] = defaultdict(int)
    captured_tasks: list[tuple[object, tuple, dict]] = []
    events: list[dict[str, object]] = []
    wire_packets: list[bytes] = []

    def force_internal_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return SimpleNamespace(task_number=len(captured_tasks))

    def drain(client):
        drained = client.get_received()
        for item in drained:
            args = item.get("args") or []
            events.append({
                "event": item.get("name"),
                "payload": args[0] if args else None,
            })
        return drained

    wall_cumulative = (
        _legacy_wall_deposit_cumulative
        if emulate_before
        else runner_module._wall_deposit_cumulative_kg_at_snapshot
    )
    originals = {
        "core.step": PyrolysisSimulator.step,
        "summary.build": runner_module.build_per_hour_summary,
        "summary.wall_cumulative": (
            wall_cumulative
        ),
        "web.tick_payload": web_events._tick_payload,
        "web.recipe_capture": web_events._record_last_recipe_capture,
        "web.completion_payload": web_events._completion_payload,
        "web.full_runner_payload": web_events._full_runner_payload,
        "web.persist_terminal": web_events._persist_terminal,
        "artifact.save": run_store_module.RunArtifactStore.save,
        "socket.emit": app_module.socketio.emit,
        "socket.packet_encode": socketio_packet.Packet.encode,
    }

    def encode_packet(packet):
        data = getattr(packet, "data", None)
        caller_path = sys._getframe(1).f_code.co_filename
        output_event = (
            isinstance(data, (list, tuple))
            and bool(data)
            and str(data[0]) in OUTPUT_EVENTS
            # Flask-SocketIO's test client decodes and re-encodes every packet
            # as validation. Count/hash only the production manager encode.
            and "flask_socketio/test_client.py" not in caller_path
        )
        encode_started = perf_counter()
        encoded = originals["socket.packet_encode"](packet)
        if output_event:
            totals["socket.packet_encode"] += perf_counter() - encode_started
            calls["socket.packet_encode"] += 1
            parts = encoded if isinstance(encoded, list) else [encoded]
            for part in parts:
                wire_packets.append(
                    part if isinstance(part, bytes) else str(part).encode("utf-8")
                )
        return encoded

    profiler = cProfile.Profile()
    wall_started = 0.0
    wall_completed = 0.0
    with tempfile.TemporaryDirectory(prefix="live-web-profile-") as artifact_dir:
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                web_events, "_get_backend", force_internal_backend
            ))
            stack.enter_context(patch.object(
                web_events, "_safe_log", lambda _message: None
            ))
            stack.enter_context(patch.object(
                app_module.socketio, "start_background_task", capture_task
            ))
            stack.enter_context(patch.object(
                app_module.socketio, "sleep", lambda _seconds=0: None
            ))
            stack.enter_context(patch.object(
                web_events.uuid,
                "uuid4",
                lambda: SimpleNamespace(hex=FIXED_RUN_ID),
            ))
            stack.enter_context(patch.object(
                web_events, "datetime", _FixedDateTime
            ))
            if emulate_before:
                stack.enter_context(patch.object(
                    wall_advisor_module,
                    "_wall_materials_data",
                    wall_advisor_module.load_wall_materials,
                ))
            stack.enter_context(patch.object(
                PyrolysisSimulator,
                "step",
                _timed("core.step", originals["core.step"], totals, calls),
            ))
            for name, owner, attribute in (
                ("summary.build", runner_module, "build_per_hour_summary"),
                (
                    "summary.wall_cumulative",
                    runner_module,
                    "_wall_deposit_cumulative_kg_at_snapshot",
                ),
                ("web.tick_payload", web_events, "_tick_payload"),
                ("web.recipe_capture", web_events, "_record_last_recipe_capture"),
                ("web.completion_payload", web_events, "_completion_payload"),
                ("web.full_runner_payload", web_events, "_full_runner_payload"),
                ("web.persist_terminal", web_events, "_persist_terminal"),
                ("artifact.save", run_store_module.RunArtifactStore, "save"),
                ("socket.emit", app_module.socketio, "emit"),
            ):
                stack.enter_context(patch.object(
                    owner,
                    attribute,
                    _timed(name, originals[name], totals, calls),
                ))
            stack.enter_context(patch.object(
                socketio_packet.Packet,
                "encode",
                encode_packet,
            ))

            app = app_module.create_app()
            app.config["RUN_ARTIFACT_DIR"] = artifact_dir
            http_client = app.test_client()
            assert http_client.get("/").status_code == 200
            client = app_module.socketio.test_client(
                app,
                flask_test_client=http_client,
            )
            client.get_received()
            wall_started = perf_counter()
            profiler.enable()
            try:
                client.emit("start_simulation", {
                    "backend": "internal-analytical",
                    "feedstock": "lunar_mare_low_ti",
                    "mass_kg": 1000.0,
                    "speed": 0,
                    "track": "pyrolysis",
                })
                drain(client)
                for campaign, fields in SETPOINT_OVERRIDES.items():
                    for field, value in fields.items():
                        client.emit("adjust_parameter", {
                            "param": "campaign_override",
                            "campaign": campaign,
                            "field": field,
                            "value": value,
                        })
                        drain(client)

                task_index = 0
                guard = 0
                while not any(
                    item["event"] == "simulation_complete" for item in events
                ):
                    guard += 1
                    if guard > 30:
                        raise RuntimeError("web run did not complete within 30 tasks")
                    if task_index >= len(captured_tasks):
                        raise RuntimeError("web run paused without a captured task")
                    target, args, kwargs = captured_tasks[task_index]
                    task_index += 1
                    target(*args, **kwargs)
                    drained = drain(client)
                    decisions = [
                        (item.get("args") or [None])[0]
                        for item in drained
                        if item.get("name") == "decision_required"
                    ]
                    for decision in decisions:
                        client.emit("make_decision", {
                            "choice": decision["recommendation"],
                        })
                        drain(client)
                wall_completed = perf_counter()
            finally:
                profiler.disable()
                state = next(iter(web_events._simulations.values()))
                run_id = str(state["run_id"])
                sim = state["session"].simulator
                artifact = state["run_store"].load(run_id)
                artifact_file_bytes = state["run_store"]._path(run_id).read_bytes()
                client.disconnect()
                for sid in list(web_events._simulations):
                    web_events._clear_simulation_state(sid)

    wall_s = wall_completed - wall_started
    profiler.dump_stats(profile_path)
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")
    top = []
    for key, value in sorted(
        stats.stats.items(), key=lambda item: item[1][3], reverse=True
    )[:40]:
        filename, line, function = key
        cc, nc, tt, ct, _callers = value
        top.append({
            "function": f"{Path(filename).name}:{line}:{function}",
            "primitive_calls": cc,
            "calls": nc,
            "self_s": tt,
            "cumulative_s": ct,
        })

    event_counts: dict[str, int] = defaultdict(int)
    for event in events:
        event_counts[str(event["event"])] += 1
    framed_wire_bytes = b"".join(
        len(packet).to_bytes(8, "big") + packet for packet in wire_packets
    )
    return {
        "scenario": {
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "track": "pyrolysis",
            "backend": "internal-analytical",
            "setpoint_overrides": SETPOINT_OVERRIDES,
        },
        "wall_s": wall_s,
        "hours": int(sim.melt.hour),
        "campaigns": list(dict.fromkeys(
            event["payload"].get("campaign")
            for event in events
            if event["event"] == "per_hour_summary"
        )),
        "events_sha256": _sha256(events),
        "events_bytes": len(_canonical_bytes(events)),
        "artifact_sha256": _sha256(artifact),
        "artifact_bytes": len(_canonical_bytes(artifact)),
        "artifact_file_sha256": hashlib.sha256(artifact_file_bytes).hexdigest(),
        "artifact_file_bytes": len(artifact_file_bytes),
        "socket_packet_stream_sha256": hashlib.sha256(
            framed_wire_bytes
        ).hexdigest(),
        "socket_packet_stream_bytes": sum(len(packet) for packet in wire_packets),
        "socket_packet_count": len(wire_packets),
        "event_counts": dict(sorted(event_counts.items())),
        "timers": {
            name: {"calls": calls[name], "wall_s": totals[name]}
            for name in sorted(totals)
        },
        "cprofile_top": top,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument("--emulate-before", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = args.output_dir / f"{args.label}.prof"
    result = _profile_once(profile_path, emulate_before=args.emulate_before)
    json_path = args.output_dir / f"{args.label}.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "artifact_sha256": result["artifact_sha256"],
        "artifact_file_sha256": result["artifact_file_sha256"],
        "events_sha256": result["events_sha256"],
        "hours": result["hours"],
        "profile": str(profile_path),
        "result": str(json_path),
        "socket_packet_stream_sha256": result["socket_packet_stream_sha256"],
        "wall_s": result["wall_s"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
