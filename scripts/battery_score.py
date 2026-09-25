#!/usr/bin/env python3
"""Score the v2.1 battery store against the explicit all-engine set.

Writes data/battery/residuals.jsonl (generated) and data/battery/score-report.md.
Pins are an independent baseline (data/battery/pins.yaml) and are never
regenerated from residuals.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import canonicalize_rail  # noqa: E402
from simulator.battery.pins import (  # noqa: E402
    load_pins,
    migrate_pin_records,
    pin_failures,
    write_pins,
)
from simulator.battery.score import (  # noqa: E402
    SCORE_ENGINE_SET,
    derive_store_stamp,
    emit_store_stamp_mismatch_warning,
    engines_from_names,
    load_legacy_score_rows,
    load_residuals_stamp,
    load_score_context,
    render_score_report,
    residual_to_plain,
    score_store,
    status_diff_rows,
    write_residuals_jsonl,
)

STUDIO_HOST = "mac-studio-256-1"
RESIDUALS_PATH = ROOT / "data" / "battery" / "residuals.jsonl"
REPORT_PATH = ROOT / "data" / "battery" / "score-report.md"


def _progress_wait(proc: subprocess.Popen[str], *, label: str) -> int:
    start = time.monotonic()
    while True:
        try:
            return proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            elapsed = int(time.monotonic() - start)
            print(f"studio wait {elapsed}s {label}", flush=True)


def _run_studio(engines: list[str], extra: list[str]) -> str | None:
    """Dispatch MELTS engines to Studio 1. Prints a progress line every 60 s."""

    remote_root = "/tmp/battery-score-impl-chunk2"
    print(f"rsync worktree → {STUDIO_HOST}:{remote_root}", flush=True)
    rsync = subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "--exclude",
            ".venv",
            "--exclude",
            ".git",
            "--exclude",
            "__pycache__",
            f"{ROOT}/",
            f"{STUDIO_HOST}:{remote_root}/",
        ],
        check=False,
    )
    if rsync.returncode != 0:
        print(f"studio rsync failed: {rsync.returncode}", flush=True)
        return None
    remote_py = "~/repos/regolith-pyrolysis-simulator/.venv/bin/python"
    engine_csv = ",".join(engines)
    extra_args = " ".join(extra)
    remote = (
        f"hostname; "
        f"cd {remote_root} && "
        f"{remote_py} scripts/battery_score.py "
        f"--engines {engine_csv} {extra_args}"
    )
    print(f"ssh {STUDIO_HOST} (detached MELTS arm)", flush=True)
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", STUDIO_HOST, remote],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    hostname = None
    start = time.monotonic()
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line:
            text = line.rstrip()
            print(f"studio: {text}", flush=True)
            if hostname is None and text and " " not in text:
                hostname = text
            continue
        if proc.poll() is not None:
            break
        elapsed = int(time.monotonic() - start)
        print(f"studio wait {elapsed}s ssh {STUDIO_HOST}", flush=True)
        time.sleep(60)
    code = proc.wait()
    if code != 0:
        print(f"studio score failed: {code}", flush=True)
        return hostname
    subprocess.run(
        [
            "rsync",
            "-a",
            f"{STUDIO_HOST}:{remote_root}/data/battery/residuals.jsonl",
            str(RESIDUALS_PATH),
        ],
        check=False,
    )
    subprocess.run(
        [
            "rsync",
            "-a",
            f"{STUDIO_HOST}:{remote_root}/data/battery/score-report.md",
            str(REPORT_PATH),
        ],
        check=False,
    )
    return hostname


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engines",
        default=",".join(e.value for e in SCORE_ENGINE_SET),
        help="comma-separated engine names (default: explicit full set)",
    )
    parser.add_argument("--rail", default=None, help="restrict to one rail id")
    parser.add_argument("--work", default=None, help="restrict to one work_id or source_id")
    parser.add_argument("--limit", type=int, default=None, help="max observations")
    parser.add_argument(
        "--studio",
        action="store_true",
        help="dispatch MELTS engines (alphamelts/thermoengine/magemin) to Studio 1",
    )
    parser.add_argument(
        "--write-pins",
        action="store_true",
        help="regenerate data/battery/pins.yaml from legacy pin sources (not residuals)",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild score-report.md from existing residuals.jsonl",
    )
    args = parser.parse_args(argv)

    engines = engines_from_names([p for p in args.engines.split(",") if p.strip()])
    rail = None if args.rail is None else canonicalize_rail(args.rail)

    if args.write_pins:
        payload = migrate_pin_records(args.root)
        write_pins(payload, args.root / "data" / "battery" / "pins.yaml")
        print(f"pins={len(payload['pin_band_records'])} key_map={len(payload['key_map'])}")

    residuals_path = args.root / "data" / "battery" / "residuals.jsonl"
    report_path = args.root / "data" / "battery" / "score-report.md"
    if args.report_only:
        from simulator.battery.score import (
            load_residuals_jsonl,
            load_score_context,
            render_score_report_from_payloads,
        )

        payloads = load_residuals_jsonl(residuals_path)
        context = load_score_context(args.root)
        recorded = load_residuals_stamp(residuals_path)
        live = derive_store_stamp(args.root)
        mismatch = emit_store_stamp_mismatch_warning(recorded, live)
        failures: list[dict] = []
        unmapped: list[str] = []
        diffs: list[dict] = []
        pins_file = args.root / "data" / "battery" / "pins.yaml"
        live_keys = {str(row.get("key") or "") for row in payloads}
        key_map: dict[str, str] = {}
        if pins_file.is_file():
            loaded = load_pins(pins_file)
            key_map = loaded["key_map"]
            for record in loaded["pin_band_records"]:
                if record.tombstone:
                    continue
                if record.key not in live_keys and not any(a in live_keys for a in record.aliases):
                    failures.append(
                        {
                            "key": record.key,
                            "reason": "coverage_failure",
                            "live": None,
                            "centre": None if record.centre is None else str(record.centre),
                            "pin_band": None
                            if record.pin_band_value is None
                            else str(record.pin_band_value),
                        }
                    )
        diffs, unmapped = status_diff_rows(
            old_rows=load_legacy_score_rows(args.root),
            new_rows=payloads,
            key_map=key_map,
        )
        report = render_score_report_from_payloads(
            payloads,
            engines=engines,
            hostname=socket.gethostname(),
            pin_failures=failures,
            status_diff=diffs,
            unmapped_legacy_keys=unmapped,
            store_stamp=recorded,
            mismatch_warning=mismatch,
            observations=context.observations,
            origins=context.origins,
        )
        report_path.write_text(report, encoding="utf-8")
        print(f"report-only residuals={len(payloads)} pin_failures={len(failures)}")
        return 0

    melts = [e.value for e in engines if e.value in {"alphamelts", "thermoengine", "magemin"}]
    laptop = [e for e in engines if e.value not in {"alphamelts", "thermoengine", "magemin"}]
    studio_hostname = None
    host = socket.gethostname()
    if args.studio and melts and STUDIO_HOST not in host and "studio" not in host.lower():
        extra = []
        if args.rail:
            extra.extend(["--rail", args.rail])
        if args.work:
            extra.extend(["--work", args.work])
        if args.limit is not None:
            extra.extend(["--limit", str(args.limit)])
        studio_hostname = _run_studio(melts, extra)
        engines = tuple(laptop)

    context = load_score_context(args.root)
    residuals, candidates = score_store(
        context,
        engines=engines,
        rail=rail,
        work_id=args.work,
        limit=args.limit,
    )
    residuals_path = args.root / "data" / "battery" / "residuals.jsonl"
    report_path = args.root / "data" / "battery" / "score-report.md"
    write_residuals_jsonl(residuals, candidates, residuals_path, root=args.root)

    failures: list[dict] = []
    unmapped: list[str] = []
    diffs: list[dict] = []
    pins_file = args.root / "data" / "battery" / "pins.yaml"
    key_map: dict[str, str] = {}
    if pins_file.is_file():
        loaded = load_pins(pins_file)
        key_map = loaded["key_map"]
        failures = pin_failures(residuals, loaded["pin_band_records"])
    diffs, unmapped = status_diff_rows(
        old_rows=load_legacy_score_rows(args.root),
        new_rows=[residual_to_plain(residual) for residual in residuals],
        key_map=key_map,
    )

    report = render_score_report(
        residuals,
        context=context,
        engines=engines if not args.studio else engines_from_names(args.engines.split(",")),
        pin_failures=failures,
        status_diff=diffs,
        unmapped_legacy_keys=unmapped,
        studio_hostname=studio_hostname,
        root=args.root,
    )
    report_path.write_text(report, encoding="utf-8")
    print(
        f"residuals={len(residuals)} scored={sum(1 for r in residuals if r.score_eligible)} "
        f"pin_failures={len(failures)} host={context.hostname}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
