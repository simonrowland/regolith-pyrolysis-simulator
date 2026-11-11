"""A spawned runner must expose the freeze-gate fallback it takes."""

from collections import Counter
import multiprocessing
import queue
from unittest.mock import patch


def _run_lunar_with_unavailable_gate_engine(result_queue):
    from simulator.chemistry.kernel import ProviderUnavailableError
    from simulator.core import PyrolysisSimulator
    from tests.test_runner_smoke import SCENARIOS, _flux_ledger_pins, _run_scenario

    try:
        with patch.object(
            PyrolysisSimulator,
            "_freeze_gate_curve_record",
            side_effect=ProviderUnavailableError("test gate engine unavailable"),
        ):
            payload, capture = _run_scenario(SCENARIOS[0], capture=True)
        result = {
            "status": payload["status"],
            "gate_fallback": payload["melt_redox_gate_floor_fallback_engagement"],
            "flux_count": len(capture["flux_maps"]),
            "flux_pins": _flux_ledger_pins(capture),
            "active_hour_temperatures": [
                float(row["T_C"])
                for row in payload["per_hour_summary"]
                if int(
                    ((row.get("vapour_batch_summary") or {}).get("n_flux_active", 0))
                    or 0
                )
                > 0
            ],
            "dispatch_temperatures": capture["flux_dispatch_temperatures"],
        }
        result_queue.put(result)
    except BaseException as exc:  # send child failures back to the test process
        result_queue.put({"error": repr(exc)})


def test_spawned_runner_flags_unavailable_gate_fallback_and_keeps_flux_capture():
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_run_lunar_with_unavailable_gate_engine,
        args=(result_queue,),
    )
    process.start()
    try:
        result = result_queue.get(timeout=240)
    except queue.Empty:
        process.terminate()
        process.join(timeout=10)
        raise AssertionError("spawned lunar fallback witness exceeded 240 seconds")
    finally:
        result_queue.close()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert "error" not in result, result.get("error")
    assert result["status"] == "ok"
    assert result["flux_count"] > 0

    fallback = result["gate_fallback"]
    assert fallback["engaged"] is True
    assert fallback["total_count"] > 0
    assert fallback["by_hour"]
    assert sum(int(row["count"]) for row in fallback["by_hour"]) == (
        fallback["total_count"]
    )
    assert all(
        {"campaign", "hour", "campaign_hour", "count"}.issubset(row)
        for row in fallback["by_hour"]
    )

    dispatches = Counter(result["dispatch_temperatures"])
    assert all(
        dispatches[temperature] >= count
        for temperature, count in Counter(
            result["active_hour_temperatures"]
        ).items()
    )
