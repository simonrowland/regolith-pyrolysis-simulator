#!/usr/bin/env python3
"""Report existing calibration comparisons; never fit, certify, or gate the engine.

Run from a checkout with its existing test dependencies. The VP and Na adapters
intentionally reuse the source bindings in the regression harnesses.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import resource
import shlex
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AUTHORITIES = ("certified", "bridge", "extrapolated", "refused")
# Production adapters never query engine intent-and-condition certification.
# envelope() can still emit the token when a caller passes it explicitly.
CERTIFIED_DERIVABLE = False
CLOSURES = {
    "vapour": ("Independent pressure, composition/phase, per-point pO2 and standard state; verified source transcription", "t-769, t-623; ADR-001"),
    "SiO evolution": ("Matched silicate melt T/composition/fO2, exposed area and measured SiO pressure/flux or Si loss", "t-099, t-204, t-104, t-205"),
    "SiO/Fe wall deposition": ("Same-surface incident flux, wall T/material/area/time and measured SiO and Fe deposit mass/rate with uncertainty", "t-838, t-486, t-014, t-413, b-171"),
    "redox": ("Independent Fe3+/Fe2+ at measured composition/T/fO2, total Fe and uncertainty", "q-006, d-018, t-198, b-203"),
    "alkali shuttle": ("Matched Na/K dose, time/T, Fe reduction/recovery, product speciation and alkali recycling balance", "t-394, t-024, t-198, b-203"),
    "melt activities": ("Same phase/reference state; independent composition/activity tables and disjoint validation", "d-006, b-205, b-309, b-310, t-761"),
    "integrated bench": ("Chamber geometry/pump boundary, time history, surface temperatures and independent O2/mass observations", "t-103, t-104"),
    "thermochemistry": ("Source thermochemical table and an existing matching runtime observable", "ADR-001"),
}
PROXIES = {
    "SiO/Fe wall deposition": "SiO: Robinot apparatus masses/qualitative Si; Sesko film thickness/composition; Wetzel solid-film growth (different surface). Fe: Robinot qualitative Fe in deposits; Sauerborn integral deposition; source evaporation alpha (different surface)",
    "redox": "SSO historical native-Fe diagnostics (no independent Fe3+/Fe2+ anchor)",
    "alkali shuttle": "SSO Na-dose diagnostics and equilibrium dG (no measured shuttle yield/time)",
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def dump(path, value):
    path.write_text(json.dumps(clean(value), indent=2, default=str, allow_nan=False) + "\n")


def normalize_uncertainty(value):
    if not isinstance(value, dict):
        return {"status": "missing", "source_value": value}
    if value.get("defaulted") or value.get("reported_status") == "not_reported" or value.get("observable_status") == "not_reported":
        return {"status": "missing", "source_value": value}
    if not any(finite(v) or isinstance(v, (list, dict)) and bool(v) for v in value.values()):
        return {"status": "missing", "source_value": value}
    return {"status": "reported", **value}


def envelope(*, dataset_id, observation_id, species, observable, units,
             measured, predicted, status, conditions, raw, uncertainty=None,
             rail="vapour", evidence="direct experiment", split="unassigned",
             source_doi=None, run_id=None, notices=(), authority="bridge",
             selected=True, score_allowed=True, execution=None):
    """Private report schema. Structured failure precedes numeric interpretation."""
    if authority not in AUTHORITIES:
        raise ValueError(f"unknown authority: {authority}")
    flags = list(notices)
    refused = status in {
        "refused", "failed-to-run", "unsupported-observable", "unsupported-speciation",
        "blocked", "skipped", "out_of_range", "missing_species", "ordering-not-evaluable",
    } or authority == "refused" or not finite(predicted)
    raw_prediction = predicted
    if refused:
        authority, predicted = "refused", None
        flags.append(status if status else "missing-prediction")
    elif status == "out-of-domain":
        authority = "extrapolated"
        flags.append("source-domain extrapolation")
    if authority == "bridge":
        flags.append("no intent-and-condition certification supplied by comparator")
    if status == "self-agreement-excluded":
        split = "training"
        flags.append("self-agreement; excluded from validation")
        score_allowed = False
    qualitative = status.startswith("ordering-") or units == "ordering_pairs" or evidence == "qualitative"
    if qualitative:
        evidence, score_allowed = "qualitative", False
        selected = False
    unc = normalize_uncertainty(uncertainty)
    if unc["status"] == "missing":
        flags.append("source uncertainty unavailable; unweighted residual only")
    eligible = score_allowed and authority != "refused" and finite(measured)
    residual = {"absolute": None, "relative": None, "dex": None}
    metric = None
    if eligible:
        residual["absolute"] = predicted - measured
        if measured != 0:
            residual["relative"] = (predicted - measured) / measured
        if predicted > 0 and measured > 0 and units != "K":
            residual["dex"] = math.log10(predicted / measured)
            metric = "dex"
        elif predicted == 0 and measured > 0 and units != "K":
            residual["dex"] = "-inf"
            metric = "dex"
        else:
            metric = "absolute"
    if status == "self-agreement-excluded":
        score_reason = "self-agreement excluded from validation"
    elif eligible:
        score_reason = "unweighted same-quantity pair"
    elif not selected:
        score_reason = "source-inadmissible diagnostic"
    else:
        score_reason = "no admissible numeric comparison"
    raw_clean = clean(raw)
    engine = raw_clean.get("engine") if isinstance(raw_clean, dict) else None
    engine_fallback = bool(isinstance(raw_clean, dict) and raw_clean.get("vaporock_error")
                           and engine == "builtin-antoine")
    score_eligible = eligible and selected
    if not selected:
        terminal = "outside-selected"
    elif authority == "refused":
        terminal = "refused"
    elif score_eligible:
        terminal = "scored"
    else:
        terminal = "excluded"
    row = {
        "schema_version": 1, "dataset_id": dataset_id, "source_id": dataset_id, "observation_id": observation_id,
        "run_id": run_id or dataset_id, "correlation_group": run_id or dataset_id,
        "rail": rail, "species": species, "observable": observable, "units": units,
        "source_doi": source_doi, "measurement_kind": evidence, "split": split,
        "conditions": clean(conditions), "measured": measured, "predicted": predicted,
        "raw_prediction": clean(raw_prediction), "uncertainty": unc,
        "signed_residual": residual, "residual_metric": metric, "authority": authority,
        "comparator_status": status, "notices": list(dict.fromkeys(str(f) for f in flags if f)),
        "categorical_outcome": status if qualitative else None,
        "selected": selected, "score_eligible": score_eligible,
        "score_reason": score_reason,
        "terminal_bucket": terminal,
        "exclusion_reason": (status if status == "self-agreement-excluded" else score_reason) if terminal == "excluded" else None,
        "engine": engine, "engine_fallback": engine_fallback,
        "raw": raw_clean, "execution": execution,
        "closure": {"data_required": CLOSURES[rail][0], "projects": CLOSURES[rail][1],
                    "design": "../BATTERY-DESIGN.md#rails-and-closure-projects", "status": "open"},
    }
    return row


def from_record(record, **metadata):
    return envelope(
        observation_id=f"{record.observable_id}@{json.dumps(dict(record.coordinate), sort_keys=True)}",
        species=record.species, observable=metadata.pop("observable", record.observable_id), units=record.units,
        measured=record.expected_value, predicted=record.actual_value, status=record.status,
        uncertainty=record.expected_uncertainty, raw=record.as_dict(), **metadata)


def rail_for(species, obs_type):
    if obs_type == "activity_coefficient":
        return "melt activities"
    if obs_type in {"gibbs_table", "transition_point"}:
        return "thermochemistry"
    return "SiO evolution" if species == "SiO" else "vapour"


def inventory_targets(obs):
    """Enumerate retained raw table rows without inventing an observable parser."""
    for field in ("rows", "points", "series", "runs", "selected_spots", "samples"):
        values = obs.values.get(field)
        if isinstance(values, list) and values:
            return [(f"{field}[{i}]", value) for i, value in enumerate(values)]
    return [("payload", obs.values)]


def extract_rows():
    from simulator.diagnostic_helpers import extract_reproduction as e
    observations = e.load_adopted_observations()
    vp = e.load_vapor_pressure_data()
    rows, seen, physical = [], {}, {}
    for obs in observations:
        # All canonical KEMS records plus the adopted bench/reference pins.
        try:
            result = e.evaluate_observation(obs, vapor_pressure_data=vp)
        except Exception as exc:
            result = e.ObservationEvaluation(observation=obs, skip_reason=f"failed-to-run: {type(exc).__name__}: {exc}")
        source = asdict(obs)
        notices = result.findings + result.runtime_notes + result.skip_reasons
        if result.skip_reason:
            notices.append(result.skip_reason)
        value_status = str(obs.values.get("status", ""))
        admission = str(obs.values.get("admission_status", ""))
        selected = not any(t in value_status + admission for t in ("rejected", "inadmissible", "withdrawn"))
        if not selected:
            notices.append(f"source admission: {value_status}; {admission}")
        # These are source roles, not guesses based on residual or test outcome.
        kind = "derived measurement" if obs.obs_type == "activity_coefficient" else "direct experiment"
        if obs.obs_type in {"gibbs_table", "transition_point"}:
            kind = "literature correlation"
        if obs.source_id in {"kems-005-fedkin-2006", "kems-008-schaefer-fegley-2004", "kems-041-sossi-fegley-2018"}:
            kind = "literature correlation"
        method = str(obs.values.get("method_class", ""))
        quantity = str(obs.values.get("quantity", ""))
        if obs.values.get("alpha_form") or obs.values.get("fit") or method in {"review_compilation", "secondary_compilation"}:
            kind = "literature correlation"
        if quantity in {"log10_P_K_atm_empirical_fit", "log10_P_NaCl_atm"}:
            kind = "literature correlation"
        if method.startswith("model_derived") or obs.source_id == "kems-005-fedkin-2006":
            kind = "derived measurement"
        if "qualitative" in method or quantity.startswith("qualitative_"):
            kind = "qualitative"
        if obs.values.get("evidence_class") == "thermodynamic_model_parameter" or quantity in {"SOLGASMIX_alkali_speciation", "equilibrium_molecular_oxygen_yield"}:
            kind = "model reference"
        if method == "method_only" or obs.values.get("semantics") == "method_geometry_reference_not_measured_species_observation":
            selected = False
            notices.append("method metadata; outside quantitative selection")
        adopted_model = obs.values.get("alpha_role") == "authors_adopted_model_value_not_measurement"
        if adopted_model:
            kind, selected = "model reference", False
            notices.append("authors-adopted model ceiling; not a measured alpha target")
        conditions = {"T_range_K": obs.T_range_K, "phase": obs.phase,
                      "regime": obs.regime, "standard_state": obs.standard_state,
                      "equipment": obs.equipment, "source_values": obs.values,
                      "pO2_bar": e.resolve_pO2_bar(obs)[0],
                      "total_pressure_Pa": e.resolve_chamber_pressure_pa(obs)[0]}
        records = result.records
        if not records:
            failed = (result.skip_reason or "").startswith("failed-to-run")
            parsers = {"psat_series": (e._literature_pressure_points, "P_Pa", "Pa", "partial_pressure"),
                       "alpha": (e._literature_alpha_points, "alpha", "alpha", "evaporation_alpha"),
                       "rate_series": (e._literature_rate_points, "rate_mol_m2_s", "mol/m2/s", "evaporation_rate")}
            if failed and obs.obs_type in parsers:
                parser, value_key, units, quantity = parsers[obs.obs_type]
                points, _, _ = parser(obs)
                if points:
                    for i, point in enumerate(points):
                        rows.append(envelope(dataset_id=obs.source_id, observation_id=f"{obs.observation_id}:point[{i}]",
                            species=obs.species_id, observable=quantity, units=units, measured=point[value_key],
                            predicted=None, status="failed-to-run", conditions={**conditions, **point},
                            raw={"observation": source, "point": point, "error": result.skip_reason},
                            uncertainty=obs.uncertainty, rail=rail_for(obs.species_id, obs.obs_type),
                            evidence=kind, selected=selected, source_doi=obs.source_doi, notices=notices))
                    continue
            for coordinate, target in inventory_targets(obs):
                row = envelope(dataset_id=obs.source_id, observation_id=f"{obs.observation_id}:{coordinate}",
                    species=obs.species_id, observable=obs.obs_type, units=obs.units or "unknown",
                    measured=target, predicted=None,
                    status="failed-to-run" if (result.skip_reason or "").startswith("failed-to-run") else "unsupported-observable",
                    conditions={**conditions, "atomic_coordinate": coordinate},
                    uncertainty=obs.uncertainty, raw={"observation": source, "evaluation": asdict(result)},
                    rail=rail_for(obs.species_id, obs.obs_type), evidence=kind, selected=False,
                    source_doi=obs.source_doi, notices=notices + ["inventory target; no registered scalar comparator; outside quantitative selection"])
                rows.append(row)
        for record in records:
            key = (record.source_id, record.observable_id, record.species,
                   json.dumps(dict(record.coordinate), sort_keys=True))
            if key in seen:
                seen[key].setdefault("observation_aliases", []).append(obs.observation_id)
                continue
            incompatible = obs.obs_type in {"alpha", "activity_coefficient"} and record.status == "out-of-domain"
            if obs.obs_type == "activity_coefficient" and result.skip_reason and record.status != "self-agreement-excluded":
                incompatible = True
            authority = "refused" if incompatible else "bridge"
            domain = {}
            if obs.obs_type == "alpha" and finite(record.actual_value):
                _, _, domain = e._engine_alpha(record.species, record.coordinate.get("temperature_K"))
                if not incompatible and domain.get("alpha_s_extrapolated"):
                    authority = "extrapolated"
            point_selected = selected and (record.expected_value is not None or record.status.startswith("ordering-"))
            point_notices = notices + (["incompatible system/form or missing activity/reference-state capability; raw numeric audit is not a matching prediction"] if incompatible else [])
            row = from_record(record, dataset_id=obs.source_id,
                observable={"psat_series": "partial_pressure", "rate_series": "evaporation_rate",
                            "alpha": "evaporation_alpha", "activity_coefficient": "activity_coefficient",
                            "transition_point": record.coordinate.get("property_kind", "transition_temperature")}.get(obs.obs_type, obs.obs_type),
                conditions={**conditions, **dict(record.coordinate)},
                rail=rail_for(record.species or obs.species_id, obs.obs_type),
                evidence=kind, source_doi=obs.source_doi, notices=point_notices, selected=point_selected,
                authority=authority, score_allowed=not adopted_model)
            row["raw"]["alpha_context"] = clean(domain)
            if obs.values.get("alpha_kind") in {"condensation_growth_not_evaporation", "condensation_sticking_not_evaporation"}:
                row["observable"] = obs.values["alpha_kind"]
            row["raw"]["source_observation"] = clean(source)
            row["source_observation_id"] = obs.observation_id
            row["source_evidence_scope"] = record.evidence_scope
            # Class-axis re-transcriptions have different IDs but identical
            # source, physical conditions and numeric targets. Keep aliases.
            signature = (obs.source_id, obs.species_id, obs.obs_type, obs.values.get("sample"),
                         json.dumps(clean(record.coordinate), sort_keys=True), record.expected_value,
                         record.actual_value, json.dumps(clean(obs.T_range_K)))
            if (finite(record.expected_value) and signature in physical and
                    ("_class" in obs.observation_id or "_class" in physical[signature]["source_observation_id"])):
                original = physical[signature]
                original.setdefault("observation_aliases", []).append(row["observation_id"])
                row["duplicate_of"] = original["observation_id"]
                row["selected"] = row["score_eligible"] = False
                row["notices"].append("duplicate transcription; retained outside selected denominator")
            elif finite(record.expected_value):
                physical[signature] = row
            seen[key] = row
            rows.append(row)
    return rows


def vp_rows():
    import yaml
    from tests.chemistry import test_corpus_anchored_parity as c
    from tests.chemistry.corpus_fixtures import grid_25_anchors
    data = [yaml.safe_load((ROOT / "data" / f).read_text()) for f in
            ("vapor_pressures.yaml", "setpoints.yaml", "feedstocks.yaml")]
    anchors = grid_25_anchors()
    # Historical §25 residual report is the VapoRock peer. ThermoEngine/VapoRock
    # is host-optional; if that peer cannot initialize, score the same 30 cells
    # with builtin-antoine (runtime VP authority). Do not invent a third engine.
    vaporock_error = None
    engine = "vaporock"
    try:
        result = c._evaluate_grid_25("vaporock", *data)
    except Exception as exc:
        vaporock_error = f"{type(exc).__name__}: {exc}"
        result = None
    if result is None or not any(finite((entry or {}).get("observed_Pa")) for entry in result.values()):
        if vaporock_error is None:
            vaporock_error = "VapoRock grid produced no finite predictions"
        try:
            result = c._evaluate_grid_25("builtin-antoine", *data)
            engine = "builtin-antoine"
        except Exception as exc:
            fallback_error = f"{type(exc).__name__}: {exc}"
            result = {a.anchor_id: {"status": "failed-to-run", "observed_Pa": None,
                      "error": vaporock_error, "builtin_error": fallback_error} for a in anchors}
            engine = "failed-to-run"
    rows, identities = [], {}
    for anchor in anchors:
        record = dict(result[anchor.anchor_id])
        record["engine"] = engine
        if vaporock_error:
            record["vaporock_error"] = vaporock_error
        # Grid uses peer MAGMA/reference equations and assumed IW bindings.
        key = (anchor.T_K, anchor.species, anchor.expected_Pa,
               json.dumps(dict(anchor.composition_wt_pct), sort_keys=True))
        duplicate = identities.get(key)
        identities[key] = anchor.anchor_id
        candidate = "sf2018_fig3" in anchor.melt_id
        self_consistency = anchor.species == "O2" and ("EAC-1A" in anchor.melt_id or "12022_proxy" in anchor.melt_id)
        measured_equation = "cj_fo93fa7" in anchor.melt_id
        evidence = "literature correlation" if measured_equation else "model reference"
        source_id = ("costa-jacobson-2015-olivine-kems" if measured_equation else
                     "sossi-fegley-2018-volatility" if candidate else
                     "visscher-fegley-2013-debris-disks" if "vf2013" in anchor.melt_id else
                     "schaefer-fegley-2004-io-lava" if "tholeiite" in anchor.melt_id else
                     "grid25-lunar-eac-proxy")
        notices = [
            f"engine={engine}; §25 grid vs MAGMA/SF/source equations; assumed IW fO2 retained",
            "grid tolerance is not measurement uncertainty",
        ]
        if vaporock_error and engine == "builtin-antoine":
            notices.append(
                "VapoRock peer unavailable on this host; scored builtin runtime VP authority. "
                + vaporock_error
            )
        row = envelope(dataset_id="vp30", observation_id=anchor.anchor_id,
            run_id=f"vp30:{anchor.melt_id}", species=anchor.species,
            observable="partial_pressure", units="Pa", measured=anchor.expected_Pa,
            predicted=record["observed_Pa"], status=record["status"],
            conditions={**asdict(anchor), "temperature_K": anchor.T_K, "pO2_bar": 10 ** anchor.fO2_log,
                        "fO2_standard_state": "bar"}, raw=record, uncertainty=None,
            evidence=evidence, split="training" if self_consistency else "model comparison",
            selected=not candidate,
            notices=notices +
                    ([f"duplicate physical target of {duplicate}"] if duplicate else []) +
                    (["source-inadmissible SF2018 figure reading; diagnostic only"] if candidate else []) +
                    (["IW O2 input self-consistency; excluded from validation"] if self_consistency else []),
            score_allowed=not duplicate and not self_consistency)
        row["duplicate_of"] = duplicate
        row["source_id"] = source_id
        row["notices"].append("DOI not supplied by tracked VP fixture; source identity and original citation retained")
        rows.append(row)
    return rows


def kems_rows():
    from simulator.diagnostic_helpers import kems
    from simulator.diagnostic_helpers.extract_reproduction import load_vapor_pressure_data
    sidecar = kems.load_kems_observations(ROOT / "data/literature/kems_measurements.yaml")
    adapter = kems.KEMSAdapter(load_vapor_pressure_data())
    rows = []
    for path in sorted((ROOT / "data/presets/kems").glob("*.yaml")):
        case = kems.load_kems_case(path)
        source = sidecar["sources"][case["source_id"]]
        try:
            result = adapter.evaluate(case, sidecar)
        except Exception as exc:
            for i, point in enumerate(sidecar["cases"][case["case_id"]]["points"]):
                rows.append(envelope(dataset_id=case["case_id"], observation_id=f"{point['observable_id']}:{i}",
                    species=point.get("species"), observable=point["observable_id"], units="source units retained in raw point",
                    measured=None, predicted=None, status="failed-to-run", conditions={"case": case, "point": point},
                    raw={"source": source, "point": point}, source_doi=source.get("doi"),
                    notices=[f"{type(exc).__name__}: {exc}"]))
            continue
        for record, runtime in zip(result.records, result.runtime_rows, strict=True):
            row = from_record(record, dataset_id=case["case_id"], conditions={"case": case, "runtime": runtime},
                source_doi=source.get("doi"), notices=runtime.get("warnings", []))
            row["raw"]["runtime"] = runtime
            row["raw"]["source"] = source
            rows.append(row)
    return rows


def na_rows():
    from simulator.diagnostic_helpers import extract_reproduction as e
    from tests.chemistry import test_builtin_vapor_pressure_provider as n
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view
    vp = e.load_vapor_pressure_data()
    sources = {str(o.values["sample"]): o for o in e.load_adopted_observations()
               if o.source_id == "kems-022-demaria-1971" and o.species_id == "Na" and "sample" in o.values}
    rows = []
    for heldout in vapor_pressure_legacy_view(vp)["metals"]["Na"]["reaction"]["heldout_demaria_comparison"]:
        sample, temperature = heldout["sample"], heldout["T_K"]
        obs = sources[sample]
        composition = n._DEMARIA_12022_WT_PCT if sample == "12022" else obs.values["sample_oxide_composition_wt_pct"]
        pO2 = 10 ** (n._demaria_12022_log10_po2_bar(temperature) if sample == "12022" else heldout["log10_pO2_bar"])
        predicted, refusal, runtime = e._engine_melt_psat_pa("Na", temperature, pO2, vp,
            account_mol=n._wt_pct_to_mol_account(composition))
        rows.append(envelope(dataset_id="na-paired", observation_id=f"{sample}@{temperature}K:Na",
            run_id=f"demaria:{sample}", species="Na", observable="partial_pressure", units="Pa",
            measured=heldout["measured_pNa_Pa"], predicted=predicted,
            status="refused" if refusal else "out-of-domain", authority="extrapolated",
            evidence="derived measurement", split="holdout", selected=False,
            conditions={"temperature_K": temperature, "pO2_bar": pO2, "total_pressure_Pa": pO2 * 1e5,
                        "composition_wt_pct": composition, "partial_melt": True},
            raw={"runtime": runtime, "source_binding": heldout, "source_observation": asdict(obs)},
            uncertainty={"kind": "log10_decades", "value": 0.30, "role": "digitization; correlated cell offset; extrapolated line points have larger unknown uncertainty"},
            source_doi=obs.source_doi,
            notices=[refusal, "out_of_gamma_domain: gamma domain [1673,1673] K",
                     "historical t-383 candidate: rejected_no_figure_reading; excluded from empirical scores",
                     "existing extract helper assumes total pressure equals pO2", heldout.get("note")]))
    return rows


def run_command(name, args, out, timeout=240):
    directory = out / name
    directory.mkdir(parents=True, exist_ok=True)
    command = [str(sys.executable), *[str(a).replace("{out}", str(directory)) for a in args]]
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
            start_new_session=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)})
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            code = 124
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    error = (directory / "stderr.log").read_text()[-6000:]
    if code == 124:
        timeout_note = f"timeout after {timeout}s"
        error = f"{error.rstrip()}\n{timeout_note}" if error.strip() else timeout_note
    receipt = {"name": name, "command": shlex.join(command), "cwd": str(ROOT), "exit_code": code,
               "wall_seconds": time.monotonic() - start, "cpu_user_seconds": after.ru_utime - before.ru_utime,
               "cpu_system_seconds": after.ru_stime - before.ru_stime, "error": error,
               "stdout": str(directory / "stdout.log"), "stderr": str(directory / "stderr.log")}
    dump(directory / "execution.json", receipt)
    return receipt


def bench_rows(out, receipts):
    import yaml
    from simulator.diagnostic_helpers.vacuum_pyrolysis import evaluate_vacuum_pyrolysis_comparison, load_vacuum_pyrolysis_observations
    sidecar = load_vacuum_pyrolysis_observations(ROOT / "data/literature/vacuum_pyrolysis_measurements.yaml")
    feedstocks = yaml.safe_load((ROOT / "data/feedstocks.yaml").read_text())
    rows = []
    for path in sorted((ROOT / "data/presets/vacuum_pyrolysis").glob("*.yaml")):
        name = "bench-" + path.stem
        receipt = run_command(name, ["-m", "simulator.runner", "--preset", str(path), "--compare", "--output", "{out}/run.json"], out)
        receipts.append(receipt)
        preset = yaml.safe_load(path.read_text())
        measurement = sidecar["measurements"][preset["measurement_id"]]
        result_path = out / name / "run.json"
        runtime = json.loads(result_path.read_text()) if result_path.exists() else {"status": "failed"}
        failed = receipt["exit_code"] != 0 or runtime.get("status") in {"failed", "refused"}
        if failed:
            runtime_error = str(runtime.get("error_message") or runtime.get("error") or runtime.get("reason") or "")
            receipt["error"] = runtime_error or receipt["error"] or "failed runtime; see run.json"
            dump(out / name / "execution.json", receipt)
        try:
            result = evaluate_vacuum_pyrolysis_comparison(preset, sidecar, runtime, feedstocks=feedstocks)
        except Exception as exc:
            receipt["comparison_error"] = f"{type(exc).__name__}: {exc}"
            points = measurement.get("comparison_points") or [{"observable_id": "unregistered-comparison", "expected_value": None}]
            for i, point in enumerate(points):
                rows.append(envelope(dataset_id=name, observation_id=f"{point['observable_id']}:{i}",
                    species=point.get("species"), observable=point["observable_id"], units=point.get("units", "unknown"),
                    measured=point.get("expected_value"), predicted=None, status="failed-to-run",
                    rail="integrated bench", conditions=preset, raw={"runtime": runtime, "source_point": point},
                    source_doi=measurement.get("doi"), uncertainty=point.get("uncertainty"),
                    selected=point.get("expected_value") is not None,
                    notices=[receipt["error"], receipt["comparison_error"]], execution=name))
            continue
        for record in result.records:
            row = from_record(record, dataset_id=name, rail="integrated bench", conditions=preset,
                source_doi=measurement.get("doi"), notices=[receipt["error"], str(runtime.get("error", ""))],
                authority="refused" if failed else "bridge", execution=name)
            if failed:
                row["comparator_status"] = "failed-to-run"
            rows.append(row)
    return rows


def engine_label(row):
    if row.get("engine_fallback"):
        return "builtin-antoine (fallback; VapoRock unavailable)"
    if row.get("engine"):
        return row["engine"]
    if row.get("dataset_id") == "vp30":
        return "builtin-antoine"
    if row.get("rail") == "integrated bench":
        return "simulator.runner"
    return "extract/KEMS"


def assign_terminal(row):
    if not row["selected"]:
        terminal = "outside-selected"
    elif row["authority"] == "refused":
        terminal = "refused"
    elif row["score_eligible"]:
        terminal = "scored"
    else:
        terminal = "excluded"
    row["terminal_bucket"] = terminal
    if terminal == "excluded":
        row["exclusion_reason"] = (
            row["comparator_status"] if row["comparator_status"] == "self-agreement-excluded"
            else row.get("exclusion_reason") or row["score_reason"]
        )
    else:
        row["exclusion_reason"] = None
    return row


def mark_alpha_rate_twins(rows):
    """Hashimoto Table 3 alpha re-filed as evaporation_rate is the same point, not a flux."""
    alphas = {}
    for row in rows:
        if row.get("observable") == "evaporation_alpha" and row.get("units") == "alpha" and row.get("selected"):
            key = (row["dataset_id"], row["species"],
                   (row.get("conditions") or {}).get("temperature_K"),
                   row["measured"], row["predicted"])
            alphas[key] = row
    for row in rows:
        if not (row.get("observable") == "evaporation_rate" and row.get("units") == "alpha" and row.get("selected")):
            continue
        key = (row["dataset_id"], row["species"],
               (row.get("conditions") or {}).get("temperature_K"),
               row["measured"], row["predicted"])
        original = alphas.get(key)
        if original is None:
            continue
        row["duplicate_of"] = original["observation_id"]
        row["selected"] = False
        row["score_eligible"] = False
        row["score_reason"] = "duplicate alpha transcription labeled as rate"
        row["notices"] = list(dict.fromkeys(
            list(row.get("notices") or []) + [
                "duplicate Hashimoto Table 3 alpha scored as evaporation_rate; alpha is not a flux; retained outside selected denominator"
            ]))
        assign_terminal(row)
    return rows


def finalize_rows(rows):
    mark_alpha_rate_twins(rows)
    for row in rows:
        raw = row.get("raw")
        if not row.get("engine") and isinstance(raw, dict) and raw.get("engine"):
            row["engine"] = raw["engine"]
        if isinstance(raw, dict) and raw.get("vaporock_error") and row.get("engine") == "builtin-antoine":
            row["engine_fallback"] = True
        assign_terminal(row)
    return rows


def species_coverage(rows, rail):
    coverage = []
    species = sorted({r["species"] for r in rows if r["rail"] == rail and r["selected"] and r["species"]})
    for name in species:
        cohort = [r for r in rows if r["rail"] == rail and r["species"] == name and r["selected"]]
        counts = Counter(r["authority"] for r in cohort)
        n = len(cohort)
        n_scored = sum(r["score_eligible"] for r in cohort)
        n_refused = counts["refused"]
        n_excluded = sum(r["terminal_bucket"] == "excluded" for r in cohort)
        if n != n_scored + n_refused + n_excluded:
            raise ValueError(
                f"{rail}/{name}: selected {n} != scored {n_scored} + refused {n_refused} + excluded {n_excluded}")
        scored = [r for r in cohort if r["score_eligible"]]
        coverage.append({
            "species": name, "N": n, "N_scored": n_scored, "N_refused": n_refused, "N_excluded": n_excluded,
            "authorities": {a: counts[a] for a in AUTHORITIES},
            "authorities_scored": {a: sum(r["authority"] == a for r in scored) for a in AUTHORITIES},
            "authorities_population": "scored",
            "refused_fraction": counts["refused"] / n if n else None,
            "extrapolated_fraction": counts["extrapolated"] / n if n else None,
            "exclusion_reasons": dict(Counter(r["exclusion_reason"] for r in cohort if r["terminal_bucket"] == "excluded")),
        })
    return coverage


def summarize(rows):
    finalize_rows(rows)
    groups = defaultdict(list)
    for row in rows:
        key = (row["rail"], row["species"] or "unspecified", row["observable"], row["units"],
               row["measurement_kind"], row["split"])
        groups[key].append(row)
    scores = []
    for key, cohort in sorted(groups.items()):
        for authority in AUTHORITIES:
            bucket = [r for r in cohort if r["authority"] == authority and r["selected"]]
            eligible = [r for r in bucket if r["score_eligible"]]
            metrics = {}
            for metric in ("dex", "relative", "absolute"):
                values = [r["signed_residual"][metric] for r in eligible if finite(r["signed_residual"][metric])]
                infinite = sum(r["signed_residual"][metric] == "-inf" for r in eligible)
                source_mse, run_mse = defaultdict(list), defaultdict(list)
                for r in eligible:
                    value = r["signed_residual"][metric]
                    if finite(value):
                        source_mse[r["source_doi"] or r["source_id"]].append(value ** 2)
                        run_mse[r["run_id"]].append(value ** 2)
                metrics[metric] = {"N": len(values), "zero_prediction_count": infinite,
                    "finite_RMSE": math.sqrt(sum(v*v for v in values)/len(values)) if values else None,
                    "RMSE": "infinite" if infinite else (math.sqrt(sum(v*v for v in values)/len(values)) if values else None),
                    "bias": sum(values)/len(values) if values else None,
                    "source_balanced_finite_RMSE": math.sqrt(sum(sum(v)/len(v) for v in source_mse.values())/len(source_mse)) if source_mse else None,
                    "run_balanced_finite_RMSE": math.sqrt(sum(sum(v)/len(v) for v in run_mse.values())/len(run_mse)) if run_mse else None}
            engines = sorted({engine_label(r) for r in bucket} or {engine_label(r) for r in eligible})
            scores.append(dict(zip(("rail", "species", "observable", "units", "measurement_kind", "split"), key),
                authority=authority, N_selected=len(bucket), N_scored=len(eligible), metrics=metrics,
                engines=engines,
                sources=sorted({r["source_doi"] or r["source_id"] for r in bucket})))
    coverage = []
    for rail in CLOSURES:
        candidates = [r for r in rows if r["rail"] == rail]
        selected = [r for r in candidates if r["selected"]]
        scored = [r for r in selected if r["score_eligible"]]
        counts = Counter(r["authority"] for r in selected)
        scored_counts = Counter(r["authority"] for r in scored)
        n = len(selected)
        n_scored = len(scored)
        n_refused = counts["refused"]
        n_excluded = sum(r["terminal_bucket"] == "excluded" for r in selected)
        if n != n_scored + n_refused + n_excluded:
            raise ValueError(
                f"{rail}: selected {n} != scored {n_scored} + refused {n_refused} + excluded {n_excluded}")
        fallback_all = [r for r in candidates if r.get("engine_fallback")]
        fallback_selected = [r for r in selected if r.get("engine_fallback")]
        fallback_scored = [r for r in scored if r.get("engine_fallback")]
        coverage.append({
            "rail": rail, "N": n, "N_scored": n_scored, "N_refused": n_refused, "N_excluded": n_excluded,
            "authorities": {a: counts[a] for a in AUTHORITIES},
            "authorities_scored": {a: scored_counts[a] for a in AUTHORITIES},
            "authorities_population": "scored",
            "certified_derivable": CERTIFIED_DERIVABLE,
            "refused_fraction": n_refused / n if n else None,
            "extrapolated_fraction": counts["extrapolated"] / n if n else None,
            "outside_selected_N": len(candidates) - n,
            "categorical_outcomes": dict(Counter(r["categorical_outcome"] for r in candidates if r["categorical_outcome"])),
            "status": "observations available" if n else "no direct comparator; proxies: " + PROXIES.get(rail, "none selected"),
            "refusal_subtypes": dict(Counter(r["comparator_status"] for r in selected if r["authority"] == "refused")),
            "exclusion_reasons": dict(Counter(r["exclusion_reason"] for r in selected if r["terminal_bucket"] == "excluded")),
            "engines_scored": dict(Counter(engine_label(r) for r in scored)),
            "engine_fallback_N": len(fallback_all),
            "engine_fallback_N_selected": len(fallback_selected),
            "engine_fallback_N_scored": len(fallback_scored),
            "marker": "builtin-antoine fallback" if fallback_all else "",
            "closure_projects": CLOSURES[rail][1], "closure_data": CLOSURES[rail][0]})
    return {"coverage": coverage, "vapour_by_species": species_coverage(rows, "vapour"),
            "sio_evolution_by_species": species_coverage(rows, "SiO evolution"), "scores": scores,
            "certified_derivable": CERTIFIED_DERIVABLE,
            "accounting": {"N_catalogued_targets": len(rows), "N_selected": sum(r["selected"] for r in rows),
                "N_predicted": sum(r["selected"] and r["authority"] != "refused" for r in rows),
                "N_no_prediction": sum(r["selected"] and r["authority"] == "refused" for r in rows),
                "N_excluded": sum(r["selected"] and r["terminal_bucket"] == "excluded" for r in rows),
                "N_execution_errors": sum(r["comparator_status"] == "failed-to-run" for r in rows),
                "N_qualitative": sum(r["measurement_kind"] == "qualitative" for r in rows),
                "N_missing_uncertainty": sum(r["uncertainty"]["status"] == "missing" for r in rows)}}


def headline(report):
    lines = [
        "| Rail | N selected | N scored | N refused | N excluded | Certified (scored; not yet derivable) | Bridge (scored) | Extrapolated (scored) | Refused fraction | Extrapolated fraction | Marker |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    fmt = lambda x: "no data" if x is None else f"{x:.3f}"
    for r in report["coverage"]:
        a = r["authorities_scored"]
        reasons = ",".join(f"{k}:{v}" for k, v in sorted((r.get("exclusion_reasons") or {}).items())) or "—"
        excluded = f"{r['N_excluded']}" + (f" ({reasons})" if r["N_excluded"] else "")
        marker = r.get("marker") or "—"
        lines.append(
            f"| {r['rail']} | {r['N']} | {r['N_scored']} | {r['N_refused']} | {excluded} | {a['certified']} | {a['bridge']} | {a['extrapolated']} | {fmt(r['refused_fraction'])} | {fmt(r['extrapolated_fraction'])} | {marker} |")
    return "\n".join(lines)


def headline_notes(report):
    lines = [
        "N counts selected atomic comparator targets (including no-prediction records), not independent experiments. Source-inadmissible diagnostics are outside N. Selected = scored + refused + excluded. Authority columns count the **scored** population (header: scored), not all selected rows.",
        "Certified (scored) is **not yet derivable**: this battery does not query the engine's certification for that intent and those conditions. A good residual, CITED tag, or validated verdict does not promote a row. The column is therefore structurally zero, not a measured finding that nothing is certified.",
    ]
    fallback_all = sum(r.get("engine_fallback_N") or 0 for r in report["coverage"])
    fallback_selected = sum(r.get("engine_fallback_N_selected") or 0 for r in report["coverage"])
    fallback_scored = sum(r.get("engine_fallback_N_scored") or 0 for r in report["coverage"])
    if fallback_all:
        lines.append(
            f"Host missing provider: VapoRock/ThermoEngine (LiquidMelts) is unavailable on this host. {fallback_all} VP anchors ran on the **builtin-antoine fallback** ({fallback_selected} selected, {fallback_scored} scored). These are not VapoRock numbers.")
    vapour = next((r for r in report["coverage"] if r["rail"] == "vapour"), None)
    if vapour and vapour.get("engines_scored"):
        mix = ", ".join(f"{engine} {n}" for engine, n in sorted(vapour["engines_scored"].items()))
        lines.append(f"Vapour N scored {vapour['N_scored']} mixes engines: {mix}.")
    return lines


def score_table(report):
    groups = defaultdict(dict)
    for s in report["scores"]:
        key = tuple(s[k] for k in ("rail", "species", "observable", "units", "measurement_kind", "split"))
        if s["N_selected"]:
            groups[key][s["authority"]] = s
    lines = ["| Rail/species | Quantity / units | Evidence / split | Engine | Certified N / RMSE | Bridge N / RMSE | Extrapolated N / RMSE |",
             "|---|---|---|---|---:|---:|---:|"]
    for (rail, species, observable, units, evidence, split), buckets in sorted(groups.items()):
        cells = []
        engines = sorted({e for s in buckets.values() for e in (s.get("engines") or ())})
        engine_text = ", ".join(engines) if engines else "unspecified"
        for authority in AUTHORITIES[:-1]:
            s = buckets.get(authority)
            metric = "absolute" if units == "K" else "dex"
            if s and not s["metrics"][metric]["N"] and not s["metrics"][metric]["zero_prediction_count"] and s["metrics"]["absolute"]["N"]:
                metric = "absolute"
            value = s["metrics"][metric]["RMSE"] if s else None
            text = f"{value:.5g}" if finite(value) else str(value or "no data")
            count = s["metrics"][metric]["N"] + s["metrics"][metric]["zero_prediction_count"] if s else 0
            cell = f"{count} / {text} ({metric}{' '+units if metric == 'absolute' else ''})"
            if s and metric == "dex" and s["N_scored"] > count:
                absolute = s["metrics"]["absolute"]
                cell += f"; absolute {absolute['N']} / {absolute['RMSE']:.5g} {units}"
            cells.append(cell)
        lines.append(f"| {rail}/{species} | {observable} / {units} | {evidence} / {split} | {engine_text} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def failure_row(name, receipt, rail, species=None):
    return envelope(dataset_id=name, observation_id=name+":execution", species=species,
        observable="harness execution", units="unavailable", measured=None, predicted=None,
        status="failed-to-run" if receipt["exit_code"] else "unsupported-observable",
        rail=rail, evidence="model reference", split="model comparison", selected=False,
        conditions={"command": receipt["command"]}, raw=receipt, execution=name,
        notices=[receipt.get("error"), "harness diagnostic; no independent measured target; outside quantitative selection"])


def residual_plots(out, rows):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    groups = defaultdict(list)
    for row in rows:
        if row["score_eligible"] and finite(row["signed_residual"]["dex"]):
            groups[(row["rail"], row["species"], row["observable"], row["units"], row["measurement_kind"], row["split"])].append(row)
    paths = []
    for index, (key, cohort) in enumerate(sorted(groups.items())):
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
        for authority, marker in zip(AUTHORITIES[:-1], ("o", "s", "^")):
            bucket = [r for r in cohort if r["authority"] == authority]
            if not bucket:
                continue
            label = f"{authority}, N={len(bucket)}"
            axes[1].scatter([r["measured"] for r in bucket], [r["predicted"] for r in bucket], marker=marker, label=label)
            for axis, field in ((axes[0], "temperature_K"), (axes[2], "pO2_bar")):
                pairs = [(r["conditions"].get(field, r["conditions"].get("T_K") if field == "temperature_K" else None), r["signed_residual"]["dex"]) for r in bucket]
                pairs = [(x, y) for x, y in pairs if finite(x) and (field != "pO2_bar" or x > 0)]
                if pairs:
                    axis.scatter(*zip(*pairs), marker=marker, label=label)
        axes[0].set(xlabel="Temperature (K)", ylabel="Signed residual (dex)")
        axes[1].set(xlabel=f"Measured ({key[3]})", ylabel=f"Predicted ({key[3]})", xscale="log", yscale="log")
        axes[2].set(xlabel="pO2 (bar)", ylabel="Signed residual (dex)", xscale="log")
        axes[1].legend(fontsize=7)
        fig.suptitle(" / ".join(str(v) for v in key) + f"; N={len(cohort)}", fontsize=9)
        fig.tight_layout()
        path = out / f"residuals-{index:03d}.svg"
        fig.savefig(path)
        plt.close(fig)
        paths.append(path.name)
    return paths


def write_report(out, rows, receipts, manifest):
    report = {"manifest": manifest, **summarize(rows), "executions": receipts, "observations": rows}
    dump(out / "report.json", report)
    dump(out / "manifest.json", manifest)
    with (out / "rail-summary.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rail", "species", "observable", "units", "evidence", "split", "authority", "N_selected", "N_scored", "RMSE_dex", "RMSE_absolute"])
        for s in report["scores"]:
            writer.writerow([s[k] for k in ("rail", "species", "observable", "units", "measurement_kind", "split", "authority", "N_selected", "N_scored")] + [s["metrics"][m]["RMSE"] for m in ("dex", "absolute")])
    (out / "observations.jsonl").write_text("".join(json.dumps(clean(r), default=str, allow_nan=False)+"\n" for r in rows))
    backlog = [{"dataset_id": r["dataset_id"], "observation_id": r["observation_id"], "rail": r["rail"],
                "notices": r["notices"], "conditions": r["conditions"], "result": r["predicted"],
                "authority": r["authority"], "group_key": r["rail"]+":"+r["dataset_id"], **r["closure"]}
               for r in rows if r["notices"] or r["authority"] != "certified"]
    (out / "certification-backlog.jsonl").write_text("".join(json.dumps(clean(r), default=str)+"\n" for r in backlog))
    lines = ["# Calibration battery — stage 2", "", "Report only; no fitting, certification grant, runtime change or gate.", "",
        f"Engine revision: `{manifest['engine_head']}`. Command: `{manifest['command']}`.", "", headline(report), ""]
    for note in headline_notes(report):
        lines.extend([note, ""])
    lines.extend([
        "## Separated scores", "", "Each row fixes species, observable, units, evidence, split and engine before authority. RMSE is dex except Kelvin temperatures (absolute K). No cross-authority or cross-observable RMSE exists. JSON also reports signed bias, absolute/relative metrics, source/run-balanced errors and refusal subtypes. Sources/series are correlated; no confidence intervals or weighted scores are claimed.", "", score_table(report),
        "", "## Vapour coverage by species", "", "| Species | N selected | N scored | Refused fraction | Extrapolated fraction |", "|---|---:|---:|---:|---:|"])
    for s in report["vapour_by_species"]:
        lines.append(f"| {s['species']} | {s['N']} | {s['N_scored']} | {s['refused_fraction']} | {s['extrapolated_fraction']} |")
    lines.extend(["", "## SiO evolution coverage by species", "",
                  "| Species | N selected | N scored | Refused fraction | Extrapolated fraction |",
                  "|---|---:|---:|---:|---:|"])
    for s in report["sio_evolution_by_species"]:
        lines.append(f"| {s['species']} | {s['N']} | {s['N_scored']} | {s['refused_fraction']} | {s['extrapolated_fraction']} |")
    lines.extend(["", "## Candidate diagnostics outside empirical selection", "", "| Dataset / observation | Authority | Signed dex | Notice |", "|---|---|---:|---|"])
    for r in rows:
        if not r["selected"] and finite(r["signed_residual"]["dex"]) and not r.get("duplicate_of"):
            lines.append(f"| {r['dataset_id']} / {r['observation_id']} | {r['authority']} | {r['signed_residual']['dex']:.6g} | source admission unresolved; no empirical score |")
    lines.extend(["", "## Coverage and closure projects", ""])
    for r in report["coverage"]:
        extra = ""
        if r["N_excluded"]:
            reasons = ", ".join(f"{k} {v}" for k, v in sorted(r["exclusion_reasons"].items()))
            extra = f" Excluded {r['N_excluded']} ({reasons})."
        if r.get("marker"):
            extra += f" Engine marker: {r['marker']}."
        lines.append(f"- **{r['rail']}**: {r['status']}. {r['closure_data']}. Projects: {r['closure_projects']}.{extra}")
    lines.extend(["", "[Row-level backlog](certification-backlog.jsonl) · [Raw observations](observations.jsonl) · [Full JSON report](report.json) · [Score CSV](rail-summary.csv)",
                  "", "Residual plots: " + (", ".join(f"[{p}]({p})" for p in manifest.get("plots", [])) or "pending/no finite scored pairs"),
                  "", "Plot facets fix species, quantity, source role and split. Sparse/empty temperature or pO2 panels expose unavailable conditions; source composition remains in each observation. No measured wall-deposition points exist to plot.", "", "## Executions", "",
                  "| Harness | Exit | Wall s | CPU user s | CPU system s |", "|---|---:|---:|---:|---:|"])
    for r in receipts:
        lines.append(f"| {r['name']} | {r['exit_code']} | {r['wall_seconds']:.3f} | {r['cpu_user_seconds']:.3f} | {r['cpu_system_seconds']:.3f} |")
    lines.extend(["", "Exact commands, errors and raw log paths are in report.json/executions. Failed harness diagnostics never become experimental zeroes. Synthetic bench test fixtures are verification only."])
    (out / "REPORT.md").write_text("\n".join(lines)+"\n")
    return report


def main(argv=None):
    runner_start = time.monotonic()
    self_before = resource.getrusage(resource.RUSAGE_SELF)
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--datasets", nargs="+", choices=("vp30", "extracts", "kems", "na", "bench", "failed-probes"),
                        default=["vp30", "extracts", "kems", "na", "bench", "failed-probes"])
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(out / "mpl"))
    manifest = {"engine_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "command": shlex.join([sys.executable, *sys.argv]), "datasets": args.datasets,
                "report_only": True, "holdout_policy": "preserve pre-existing splits; unassigned extracts never claimed disjoint",
                "uncertainty_policy": "source only; defaults and not_reported placeholders are missing"}
    rows, receipts = [], []
    for name in args.datasets:
        start = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_SELF)
        try:
            if name == "bench":
                result = bench_rows(out, receipts)
            elif name == "failed-probes":
                result = []
                for probe, command in [
                    ("sso-smoke", ["scripts/sso_r_validation_map.py", "--smoke", "--out-dir", "{out}"]),
                    ("mc5-peer-cell", ["scripts/vapour_rail_engine_crosscheck.py", "--temperatures-K", "1673.15", "--fo2-log10-bar", "-8", "--output-dir", "{out}"])]:
                    receipt = run_command(probe, command, out)
                    receipts.append(receipt)
                    result.append(failure_row(probe, receipt, "redox" if probe == "sso-smoke" else "vapour"))
                    if probe == "sso-smoke":
                        result[-1]["related_rails"] = ["alkali shuttle", "SiO evolution"]
            else:
                result = {"vp30": vp_rows, "extracts": extract_rows, "kems": kems_rows, "na": na_rows}[name]()
            for row in result:
                row["execution"] = row["execution"] or name
            rows.extend(result)
            code, error = 0, None
        except Exception:
            code, error = 1, traceback.format_exc()
            rows.append(failure_row(name, {"exit_code": code, "command": manifest["command"], "error": error}, "vapour"))
        after = resource.getrusage(resource.RUSAGE_SELF)
        receipts.append({"name": name, "command": f"{name} adapter in {manifest['command']}",
            "exit_code": code, "error": error, "wall_seconds": time.monotonic()-start,
            "cpu_user_seconds": after.ru_utime-before.ru_utime, "cpu_system_seconds": after.ru_stime-before.ru_stime})
        write_report(out, rows, receipts, manifest)
        print(f"{name}: exit={code}, rows={len(rows)}, wall={time.monotonic()-start:.3f}s", flush=True)
    try:
        manifest["plots"] = residual_plots(out, rows)
    except ImportError as exc:
        manifest["plot_error"] = str(exc)
    code = 0 if all(r["exit_code"] == 0 for r in receipts if r["name"] in args.datasets) else 1
    self_after = resource.getrusage(resource.RUSAGE_SELF)
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    manifest["execution"] = {"exit_code": code, "wall_seconds": time.monotonic()-runner_start,
        "cpu_user_seconds": self_after.ru_utime-self_before.ru_utime+child_after.ru_utime-child_before.ru_utime,
        "cpu_system_seconds": self_after.ru_stime-self_before.ru_stime+child_after.ru_stime-child_before.ru_stime,
        "timing_scope": "runner and children through final report preparation; excludes final serialization"}
    write_report(out, rows, receipts, manifest)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
