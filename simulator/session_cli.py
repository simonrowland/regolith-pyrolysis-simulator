"""Non-interactive SimSession script harness."""

from __future__ import annotations

import argparse
from enum import Enum
import json
from pathlib import Path
import shlex
import sys
from typing import Any, Iterable, Mapping, TextIO

from simulator.backends import BackendSelectionPolicy
from simulator.config import load_config_bundle
from simulator.runner import DATA_DIR, RunnerError, build_per_hour_summary
from simulator.session import SimSession, SimSessionConfig
from simulator.state import DecisionPoint


PROTOCOL_VERSION = "1.0.0"


class _ScriptParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


class SessionScriptRunner:
    """Drive one SimSession from parsed script commands."""

    def __init__(self) -> None:
        self.session = SimSession()
        self.started = False

    def execute(self, tokens: list[str], cmd: str) -> dict[str, Any]:
        if not tokens:
            raise ValueError("empty command")
        verb = tokens[0]
        args = tokens[1:]
        if verb == "start":
            return self._start(args)
        if verb == "advance":
            return self._advance(args)
        if verb == "decide":
            return self._decide(args)
        if verb == "adjust":
            return self._adjust(args)
        if verb == "pause":
            self._expect_no_args(verb, args)
            self.session.pause()
            return {"frame_type": "pause", "paused": True}
        if verb == "resume":
            self._expect_no_args(verb, args)
            self.session.resume()
            return {"frame_type": "resume", "paused": False}
        if verb == "snapshot":
            self._expect_no_args(verb, args)
            return self._snapshot()
        if verb == "quit":
            self._expect_no_args(verb, args)
            return {"frame_type": "quit"}
        raise ValueError(f"unknown command {verb!r}")

    def _start(self, args: list[str]) -> dict[str, Any]:
        parsed = _start_parser().parse_args(_normalize_start_args(args))
        additives = _parse_kv_pairs(parsed.additive)
        runtime_campaign_overrides = _parse_setpoint_overrides(
            parsed.setpoint,
            parsed.setpoints_overrides,
            parsed.runtime_campaign_overrides,
        )
        try:
            bundle = load_config_bundle(DATA_DIR)
        except FileNotFoundError as exc:
            raise RunnerError(str(exc)) from exc
        config = SimSessionConfig(
            feedstock_id=parsed.feedstock,
            feedstocks=bundle.feedstocks,
            setpoints=bundle.setpoints,
            vapor_pressures=bundle.vapor_pressures,
            materials=bundle.materials,
            campaign=parsed.campaign,
            backend_name=parsed.backend,
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
            mass_kg=float(parsed.mass_kg),
            additives_kg=additives,
            runtime_campaign_overrides=runtime_campaign_overrides,
            track=parsed.track,
            c4_max_temp=parsed.c4_max_temp,
            unavailable_error_cls=RunnerError,
        )
        self.session.start(config)
        self.started = True
        return {
            "frame_type": "start",
            "protocol_version": PROTOCOL_VERSION,
            "feedstock_id": parsed.feedstock,
            "campaign": parsed.campaign,
            "mass_kg": float(parsed.mass_kg),
            "backend": parsed.backend,
            "backend_active": type(self.session.simulator.backend).__name__,
            "track": parsed.track,
        }

    def _advance(self, args: list[str]) -> dict[str, Any]:
        if len(args) > 1:
            raise ValueError("advance accepts at most one count")
        count = 1 if not args else int(args[0])
        if count < 1:
            raise ValueError("advance count must be >= 1")

        steps: list[dict[str, Any]] = []
        decision = self.session.pending_decision()
        if decision is not None:
            return self._decision_frame(steps, decision)
        if self.session.is_complete():
            return {"frame_type": "complete", "steps": steps}

        for _ in range(count):
            result = self.session.advance()
            steps.append(result.per_hour_summary)
            if result.decision_event is not None:
                return {
                    "frame_type": "decision_required",
                    "steps": steps,
                    "decision": result.decision_event,
                }
            if self.session.is_complete():
                return {"frame_type": "complete", "steps": steps}

        if len(steps) == 1:
            return {"frame_type": "step", "step": steps[0], "steps": steps}
        return {"frame_type": "advance", "steps": steps}

    def _decide(self, args: list[str]) -> dict[str, Any]:
        if len(args) != 1:
            raise ValueError("decide requires exactly one choice")
        decision = self.session.pending_decision()
        payload = _decision_payload(decision) if decision is not None else None
        self.session.decide(args[0])
        return {
            "frame_type": "decide",
            "choice": args[0],
            "decision": payload,
        }

    def _adjust(self, args: list[str]) -> dict[str, Any]:
        if len(args) < 2:
            raise ValueError("adjust requires a parameter and value")
        param = args[0]
        if param == "campaign_override":
            if len(args) < 4:
                raise ValueError(
                    "adjust campaign_override requires campaign field value"
                )
            campaign, field = args[1], args[2]
            value = " ".join(args[3:])
            self.session.adjust(
                "campaign_override",
                float(value),
                campaign=campaign,
                field=field,
            )
            return {
                "frame_type": "adjust",
                "param": param,
                "campaign": campaign,
                "field": field,
                "value": float(value),
            }
        if len(args) != 2:
            raise ValueError(f"adjust {param} requires exactly one value")
        value = args[1]
        self.session.adjust(param, float(value))
        return {
            "frame_type": "adjust",
            "param": param,
            "value": float(value),
        }

    def _snapshot(self) -> dict[str, Any]:
        snapshot = self.session.snapshot()
        summary = build_per_hour_summary(self.session.simulator, snapshot)
        return {
            "frame_type": "snapshot",
            "snapshot": summary,
            "complete": self.session.is_complete(),
            "paused": bool(getattr(self.session, "_paused", False)),
        }

    def _decision_frame(
        self,
        steps: list[dict[str, Any]],
        decision: DecisionPoint,
    ) -> dict[str, Any]:
        return {
            "frame_type": "decision_required",
            "steps": steps,
            "decision": _decision_payload(decision),
        }

    @staticmethod
    def _expect_no_args(verb: str, args: list[str]) -> None:
        if args:
            raise ValueError(f"{verb} does not accept operands")


def run_script(
    lines: Iterable[str],
    *,
    strict: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    runner = SessionScriptRunner()
    seq = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        seq += 1
        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
            cmd = " ".join(tokens)
            payload = runner.execute(tokens, cmd)
            frame = {"seq": seq, "cmd": cmd, "ok": True, **payload}
        except Exception as exc:  # noqa: BLE001 -- errors are protocol frames
            cmd = _safe_command_text(stripped)
            frame = {
                "seq": seq,
                "cmd": cmd,
                "ok": False,
                "frame_type": "error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
            print(f"session: {cmd}: {type(exc).__name__}: {exc}", file=err)
            _write_frame(out, frame)
            if strict:
                return 1
            continue

        _write_frame(out, frame)
        if payload.get("frame_type") == "quit":
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m simulator session",
        description=(
            "Run a non-interactive SimSession recipe and emit one NDJSON "
            "frame per command."
        ),
    )
    parser.add_argument("--script", required=True, help="Recipe path, or '-' for stdin")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on the first command error",
    )
    parser.add_argument(
        "--started-at-utc",
        default=None,
        help="Accepted for parity with runner deterministic invocations; not emitted",
    )
    parser.add_argument(
        "--kernel-commit-sha",
        default=None,
        help="Accepted for parity with runner deterministic invocations; not emitted",
    )
    args = parser.parse_args(argv)

    if args.script == "-":
        return run_script(sys.stdin, strict=args.strict)
    path = Path(args.script)
    with path.open(encoding="utf-8") as f:
        return run_script(f, strict=args.strict)


def _start_parser() -> argparse.ArgumentParser:
    parser = _ScriptParser(prog="start", add_help=False)
    parser.add_argument("--feedstock", required=True)
    parser.add_argument("--campaign", default="C0")
    parser.add_argument("--mass-kg", dest="mass_kg", type=float, default=1000.0)
    parser.add_argument(
        "--backend",
        default="stub",
        choices=("stub", "alphamelts"),
    )
    parser.add_argument(
        "--track",
        default="pyrolysis",
        choices=("pyrolysis", "mre_baseline"),
    )
    parser.add_argument("--additive", action="append", default=[])
    parser.add_argument("--c4-max-temp", dest="c4_max_temp", type=float, default=None)
    parser.add_argument("--setpoint", action="append", default=[])
    parser.add_argument("--setpoints-overrides", default=None)
    parser.add_argument("--runtime-campaign-overrides", default=None)
    return parser


def _normalize_start_args(args: list[str]) -> list[str]:
    normalized: list[str] = []
    key_aliases = {
        "feedstock": "feedstock",
        "campaign": "campaign",
        "mass_kg": "mass-kg",
        "mass-kg": "mass-kg",
        "backend": "backend",
        "track": "track",
        "additive": "additive",
        "c4_max_temp": "c4-max-temp",
        "c4-max-temp": "c4-max-temp",
        "setpoint": "setpoint",
        "setpoints_overrides": "setpoints-overrides",
        "setpoints-overrides": "setpoints-overrides",
        "runtime_campaign_overrides": "runtime-campaign-overrides",
        "runtime-campaign-overrides": "runtime-campaign-overrides",
    }
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            normalized.append(token)
            i += 1
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            alias = key_aliases.get(key)
            if alias:
                normalized.append(f"--{alias}={value}")
                i += 1
                continue
        alias = key_aliases.get(token)
        if alias and i + 1 < len(args):
            normalized.extend([f"--{alias}", args[i + 1]])
            i += 2
            continue
        normalized.append(token)
        i += 1
    return normalized


def _parse_kv_pairs(items: list[str]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected KEY=FLOAT pair, got {item!r}")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError(f"expected non-empty key in {item!r}")
        parsed[key] = float(value)
    return parsed


def _parse_setpoint_overrides(
    setpoints: list[str],
    legacy_raw_json: str | None,
    runtime_raw_json: str | None,
) -> dict[str, dict[str, float]]:
    overrides: dict[str, dict[str, float]] = {}
    legacy = _parse_override_json(
        legacy_raw_json,
        flag_name="--setpoints-overrides",
    )
    runtime = _parse_override_json(
        runtime_raw_json,
        flag_name="--runtime-campaign-overrides",
    )
    if legacy is not None and runtime is not None and legacy != runtime:
        raise ValueError(
            "--runtime-campaign-overrides conflicts with deprecated "
            "--setpoints-overrides alias"
        )
    parsed_json = runtime if runtime is not None else legacy
    if parsed_json:
        overrides.update(parsed_json)
    for item in setpoints:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            raise ValueError(
                f"expected CAMPAIGN.FIELD=FLOAT setpoint, got {item!r}"
            )
        lhs, value = item.split("=", 1)
        campaign, field = lhs.split(".", 1)
        if not campaign or not field:
            raise ValueError(
                f"expected CAMPAIGN.FIELD=FLOAT setpoint, got {item!r}"
            )
        overrides.setdefault(campaign, {})[field] = float(value)
    return overrides


def _parse_override_json(
    raw_json: str | None,
    *,
    flag_name: str,
) -> dict[str, dict[str, float]] | None:
    if not raw_json:
        return None
    loaded = json.loads(raw_json)
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{flag_name} must decode to an object")
    overrides: dict[str, dict[str, float]] = {}
    for campaign, fields in loaded.items():
        if not isinstance(fields, Mapping):
            raise ValueError(f"{flag_name}[{campaign!r}] must be an object")
        target = overrides.setdefault(str(campaign), {})
        for field, value in fields.items():
            target[str(field)] = float(value)
    return overrides


def _decision_payload(decision: DecisionPoint) -> dict[str, Any]:
    return {
        "type": decision.decision_type.name,
        "options": list(decision.options),
        "recommendation": decision.recommendation,
        "context": _jsonable(decision.context),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _safe_command_text(raw: str) -> str:
    try:
        return " ".join(shlex.split(raw, comments=False, posix=True))
    except ValueError:
        return raw


def _write_frame(out: TextIO, frame: dict[str, Any]) -> None:
    out.write(json.dumps(frame, sort_keys=True, separators=(",", ":")))
    out.write("\n")
    out.flush()


if __name__ == "__main__":
    raise SystemExit(main())
