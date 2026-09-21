#!/usr/bin/env python3
"""Generate waypoint-backed consumer inputs and typed refusals, without running engines."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import load_migrated_store, load_migrated_benches, to_plain
from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.generators import engine_point_requests, kems_case, vacuum_pyrolysis_preset
from scripts.bench_readiness import _implicit_bench, _apparatus_references


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", choices=("kems", "rps", "engine_point"), required=True)
    parser.add_argument("--source")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("generated-bench-inputs"))
    parser.add_argument("--modelling-inputs", type=Path, help="Explicit operator inputs for RPS, JSON")
    args = parser.parse_args(argv)
    works, experiments, observations = load_migrated_store(args.root)
    if args.source and not any(args.source in work.source_ids for work in works.values()):
        parser.error(f"unknown source: {args.source}")
    benches = load_migrated_benches(args.root)
    references = _apparatus_references(args.root, works)
    by_experiment = defaultdict(list)
    for observation in observations.values():
        by_experiment[observation.experiment_id].append(observation)
    model = json.loads(args.modelling_inputs.read_text()) if args.modelling_inputs else None
    args.output.mkdir(parents=True, exist_ok=True)
    counts, reasons = Counter(), Counter()
    manifest = []
    for experiment in sorted(experiments.values(), key=lambda item: item.experiment_id):
        work = works.get(experiment.work_id)
        sources = work.source_ids if work else (experiment.work_id,)
        if args.source and args.source not in sources:
            continue
        bench = benches.get(experiment.bench_id) if experiment.bench_id else _implicit_bench(
            experiment, work, [ref for source in sources for ref in references.get(source, ())])
        if bench is None:
            counts["refused"] += 1
            reasons["bench"] += 1
            manifest.append({"experiment_id": experiment.experiment_id, "status": "refused", "missing": ["bench"]})
            continue
        contexts = [None] if args.consumer == "rps" else by_experiment.get(experiment.experiment_id, [None])
        for observation in contexts:
            inputs = collect_consumer_inputs(experiment, bench, observation)
            results = engine_point_requests(inputs) if args.consumer == "engine_point" else (
                kems_case(inputs) if args.consumer == "kems" else vacuum_pyrolysis_preset(inputs, modelling_inputs=model),)
            for result in results:
                status = "generated" if result.payload is not None else "refused"
                counts[status] += 1
                for gap in result.readiness.gaps:
                    reasons[gap.waypoint + ":" + gap.reason.value] += 1
                stem = f"{len(manifest):08d}"
                record = {"experiment_id": inputs.experiment_id, "observation_id": inputs.observation_id,
                          "engine": result.readiness.engine, "status": status, "readiness": to_plain(result.readiness)}
                if result.payload is not None:
                    (args.output / (stem + ".json")).write_text(json.dumps(result.payload, default=str, indent=2) + "\n")
                    record["file"] = stem + ".json"
                (args.output / (stem + ".evidence.json")).write_text(json.dumps(to_plain(result.provenance), indent=2) + "\n")
                record["evidence"] = stem + ".evidence.json"
                manifest.append(record)
    summary = {"consumer": args.consumer, "counts": dict(counts), "refusal_reasons": dict(reasons), "records": manifest}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"summary": str(args.output / "summary.json"), "counts": dict(counts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
