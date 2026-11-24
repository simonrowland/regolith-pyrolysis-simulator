"""Compare historical and current pressure/oxygen inputs at the engine call."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from simulator.battery.enums import Engine, Quantity
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Bench, Observation
from simulator.diagnostic_helpers import binary_pot_battery
from tests.battery.factories import migrate_one_extract

ROUND18 = "5ac2ecd4b5"
SOURCES = (
    "borisov-2018-compilation",
    "kress-carmichael-1988-iron-oxidation-stoichiometry",
    "sossi-2020-magma-ocean-redox",
    "aithala-2026-magmatic-iron-redox-to-2100c",
)
REDOX_QUANTITIES = {
    Quantity.FE3_FE2_RATIO,
    Quantity.VALENCE_RATIO,
    Quantity.FUGACITY,
    Quantity.P_PARTIAL,
}


class _BackendBoundaryReached(BaseException):
    pass


class _NativeEngineOpened(BaseException):
    pass


def _load_revision_module(root: Path, revision: str, path: str, name: str):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=root, text=True,
    )
    module = types.ModuleType(name)
    module.__file__ = f"<{name}>"
    module.__package__ = "simulator.battery"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _load_round18(root: Path):
    fields = _load_revision_module(
        root, ROUND18, "simulator/battery/redox_fields.py", "redox_fields_round18",
    )
    with patch.dict(sys.modules, {"simulator.battery.redox_fields": fields}):
        ladder = _load_revision_module(
            root, ROUND18, "simulator/battery/fo2_ladder.py", "fo2_ladder_round18",
        )
        with patch.dict(sys.modules, {"simulator.battery.fo2_ladder": ladder}):
            score = _load_revision_module(
                root, ROUND18, "simulator/battery/score.py", "score_round18",
            )
    return {"simulator.battery.redox_fields": fields,
            "simulator.battery.fo2_ladder": ladder}, score


def actual_engine_request(
    score_module,
    observation: Observation,
    experiment,
    bench: Bench | None,
    *,
    revision_modules: dict[str, types.ModuleType] | None = None,
) -> tuple:
    """Stop at the production adapter boundary and return its actual inputs.

    The patched adapter is the last scorer/backend boundary before native
    work. A pre-opened inert handle and a forbidden engine opener make the
    census incapable of invoking a native engine.
    """

    requests: list[tuple] = []
    engine = Engine.ALPHAMELTS
    handle = binary_pot_battery.EngineHandle(
        name=engine.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=True,
    )

    def capture(_handle, _pot, *, po2, **kwargs):
        requests.append((
            "backend_call",
            kwargs.get("physical_pressure_bar"),
            getattr(getattr(po2, "mode", None), "value", getattr(po2, "mode", None)),
            getattr(po2, "po2_bar", None),
            getattr(po2, "cell_material", None),
        ))
        raise _BackendBoundaryReached()

    def forbid_native_open(*_args, **_kwargs):
        raise _NativeEngineOpened()

    oxygen_assemblies = []
    original_oxygen_request = score_module._oxygen_input_request

    def record_oxygen_request(**kwargs):
        decision = original_oxygen_request(**kwargs)
        notices = tuple(
            (
                getattr(notice.kind, "value", notice.kind),
                notice.reason,
                None if notice.policy is None else notice.policy.id,
            )
            for notice in kwargs["input_notices"]
        )
        oxygen_assemblies.append((
            getattr(decision, "mode", None),
            getattr(decision, "po2_bar", None),
            getattr(getattr(decision, "refusal_reason", None), "value", None),
            notices,
        ))
        return decision

    with (
        patch.object(binary_pot_battery, "equilibrate_cell", capture),
        patch.object(binary_pot_battery, "open_battery_engine", forbid_native_open),
        patch.object(score_module, "_oxygen_input_request", record_oxygen_request),
        patch.dict(sys.modules, revision_modules or {}),
    ):
        try:
            prediction = score_module.predict_with_engine(
                engine,
                observation,
                experiment=experiment,
                bench=bench,
                isolated=False,
                handles={engine.value: handle},
            )
        except _BackendBoundaryReached:
            pass
        else:
            if requests:
                raise AssertionError("backend boundary was crossed more than once")
            execution = getattr(prediction, "execution", None)
            refusal = getattr(prediction, "refusal_reason", None)
            requests.append((
                "not_called",
                None if execution is None else getattr(execution.state, "value", execution.state),
                None if refusal is None else getattr(refusal, "value", refusal),
                json.dumps(getattr(prediction, "refusal_detail", None), sort_keys=True, default=str),
                None if getattr(prediction, "value", None) is None else str(prediction.value),
            ))
    if len(requests) != 1:
        raise AssertionError(f"expected one backend outcome, got {requests!r}")
    return (*requests[0], tuple(oxygen_assemblies))


def _request_was_called(request: tuple) -> bool:
    return request[0] == "backend_call"


def _pressure_input(request: tuple):
    return request[1] if _request_was_called(request) else ("omitted",)


def _oxygen_input(request: tuple):
    if _request_was_called(request):
        return request[2:5] + (request[5],)
    return "not_called", request[5]


def census_source(
    old_modules: dict[str, types.ModuleType],
    old_score,
    current_score,
    root: Path,
    source: str,
) -> dict:
    path = root / "data/literature/extracts" / f"{source}.yaml"
    migrator = migrate_one_extract(root, path)
    migrator.finalize()
    rows = old_calls = current_calls = old_omitted = current_omitted = 0
    changed = pressure_changed = oxygen_changed = 0
    old_backend_modes: Counter[str] = Counter()
    current_backend_modes: Counter[str] = Counter()
    changed_rows = []
    for row in migrator.result.observations.values():
        if not isinstance(row.identity, Identity):
            continue
        quantity = quantity_token(row.identity)
        if quantity not in REDOX_QUANTITIES:
            continue
        experiment = migrator.result.experiments.get(row.experiment_id)
        if experiment is None:
            continue
        rows += 1
        old_bench = old_score._bench_for_score(experiment, migrator.result.benches)
        current_bench = current_score._bench_for_score(experiment, migrator.result.benches)
        previous = actual_engine_request(
            old_score, row, experiment, old_bench, revision_modules=old_modules,
        )
        current = actual_engine_request(
            current_score, row, experiment, current_bench,
        )
        old_calls += int(_request_was_called(previous))
        current_calls += int(_request_was_called(current))
        old_omitted += int(not _request_was_called(previous))
        current_omitted += int(not _request_was_called(current))
        changed += int(previous != current)
        pressure_changed += int(_pressure_input(previous) != _pressure_input(current))
        oxygen_changed += int(_oxygen_input(previous) != _oxygen_input(current))
        if _request_was_called(previous):
            old_backend_modes[str(previous[2])] += 1
        if _request_was_called(current):
            current_backend_modes[str(current[2])] += 1
        if previous != current:
            changed_rows.append({
                "observation_id": row.observation_id,
                "quantity": quantity.value,
                "round18": previous,
                "current": current,
            })
    return {
        "source": source,
        "rows": rows,
        "round18_backend_calls": old_calls,
        "current_backend_calls": current_calls,
        "round18_omitted": old_omitted,
        "current_omitted": current_omitted,
        "changed_requests": changed,
        "changed_pressure_inputs": pressure_changed,
        "changed_oxygen_inputs": oxygen_changed,
        "round18_backend_modes": dict(sorted(old_backend_modes.items())),
        "current_backend_modes": dict(sorted(current_backend_modes.items())),
        "changed_rows": changed_rows,
    }


def census_table(root: Path | None = None) -> list[dict]:
    from simulator.battery import score as current_score

    root = Path(__file__).resolve().parents[2] if root is None else root
    old_modules, old_score = _load_round18(root)
    return [
        census_source(old_modules, old_score, current_score, root, source)
        for source in SOURCES
    ]
