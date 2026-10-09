#!/usr/bin/env python3
"""Read-only extract sweep for experiment-level composition inheritance."""

from __future__ import annotations

import csv
import json
import re
import socket
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import simulator.battery.migrate as migrate
from simulator.battery.identity import quantity_token
from simulator.battery.score import ScoreContext, comparison_candidates
from simulator.battery.waypoints import normalized_composition


ROOT = Path.cwd()
OUT = Path("b718-sweep")
CSV_PATH = OUT / "class-b-rows.csv"
REPORT_PATH = OUT / "report.md"
SKIP = migrate._WALK_SKIP_KEYS


def fingerprint(raw: object) -> tuple[tuple[str, str], ...] | None:
    """Unwrap Located/State/Composition, then defer to the migrator's owner.

    Map identity has one owner, ``migrate._printed_fingerprint`` (ROR-b718
    hygiene: no second copy of the equality rule in this script).
    """
    if isinstance(raw, migrate.Located):
        if not raw.state.is_value:
            return None
        raw = raw.state.value
    if isinstance(raw, migrate.State):
        if not raw.is_value:
            return None
        raw = raw.value
    if isinstance(raw, migrate.Composition):
        raw = raw.as_map()
    return migrate._printed_fingerprint(raw)


def locator_key(locator: object) -> tuple[tuple[str, str], ...] | None:
    if locator is None:
        return None
    if is_dataclass(locator):
        raw = asdict(locator)
    elif isinstance(locator, Mapping):
        raw = dict(locator)
    else:
        return None
    return tuple(sorted((str(k), str(v)) for k, v in raw.items() if v is not None))


def map_nodes(roots, vocabulary, fallback_locator):
    """Mirror the migrator's map walk and retain the nested row path."""
    names = {e.printed for e in vocabulary if e.field == "sample.printed_composition"}
    found = []

    def metadata(obj):
        if not isinstance(obj, Mapping):
            return {}
        return {
            key: obj[key]
            for key in ("system_as_printed", "notes", "row_id", "id", "run_id", "sample_id")
            if key in obj and obj[key] not in (None, "")
        }

    def walk(obj, parent_loc, path, depth):
        if depth > 14:
            return
        if isinstance(obj, Mapping):
            loc = migrate.locator_from_mapping(obj.get("locator")) or parent_loc
            meta = metadata(obj)
            for key, value in obj.items():
                name = str(key)
                if name in names and isinstance(value, Mapping):
                    fp = migrate._printed_fingerprint(value)
                    if fp is not None and loc is not None:
                        found.append({
                            "fingerprint": fp,
                            "locator": locator_key(loc),
                            "name": name,
                            "row": path + "." + name,
                            "description": meta,
                        })
                if name not in SKIP and name != "locator":
                    walk(value, loc, path + "." + name, depth + 1)
        elif isinstance(obj, list):
            for index, item in enumerate(obj):
                walk(item, parent_loc, f"{path}[{index}]", depth + 1)

    for index, root in enumerate(roots):
        if isinstance(root, tuple) and len(root) == 2 and isinstance(root[0], str):
            name, obj = root
        else:
            name, obj = f"root{index}", root
        walk(obj, fallback_locator, name, 0)
    return found


def canonical_declared_experiments(doc, work_id):
    raw = doc.get("experiments") or ()
    if isinstance(raw, Mapping):
        raw = [dict(value, experiment_id=key) for key, value in raw.items()]
    if not isinstance(raw, (list, tuple)):
        return ()
    out = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        local_id = item.get("experiment_id") or item.get("id")
        if local_id is None:
            continue
        token = str(local_id).strip()
        experiment_id = token if "::" in token else f"{work_id}::experiment::{token}"
        sample_raw = item.get("sample") or item.get("charge") or {}
        try:
            sample = migrate._sample_from_plain(sample_raw)
        except (ArithmeticError, TypeError, ValueError):
            continue
        if sample.printed_composition is None or not sample.printed_composition.state.is_value:
            continue
        out.append({
            "experiment_id": experiment_id,
            "fingerprint": fingerprint(sample.printed_composition),
            "source_id": str(doc.get("source_id") or ""),
            "row": "experiment.sample.printed_composition",
            "description": {},
        })
    return tuple(out)


def own_compositions(observation):
    fps = set()
    if observation.identity.composition is not None:
        fp = fingerprint(observation.identity.composition)
        if fp:
            fps.add(fp)
    for key in ("printed_composition", "composition"):
        located = (observation.point_conditions or {}).get(key)
        fp = fingerprint(located)
        if fp:
            fps.add(fp)
    return fps


def quantity_name(observation):
    quantity = quantity_token(observation.identity)
    return quantity.value if quantity is not None else "unknown"


def evidence_name(observation):
    state = observation.evidence.class_
    if not state.is_value:
        return state.tag.value
    value = state.value
    return getattr(value, "value", str(value))


def raw_observation_lookup(docs):
    lookup = defaultdict(list)
    for source_id, doc in docs.items():
        for formula, raw in migrate.iter_extract_observations(doc):
            rid = str(raw.get("observation_id") or "")
            lookup[source_id].append((rid, formula, raw))
    return lookup


def source_for_observation(observation):
    return str(observation.source_id or "")


def match_raw_observation(observation, lookup):
    source_id = source_for_observation(observation)
    raw_id = observation.observation_id
    prefix = source_id + "::"
    local_id = raw_id[len(prefix):] if raw_id.startswith(prefix) else raw_id
    rows = lookup.get(source_id, ())
    exact = [item for item in rows if item[0] == local_id]
    if exact:
        return exact[0][2]
    nested = [item for item in rows if local_id.startswith(item[0] + "::") or local_id.startswith(item[0] + "-")]
    if nested:
        return max(nested, key=lambda item: len(item[0]))[2]
    if observation.locator is not None:
        wanted = locator_key(observation.locator)
        matching = []
        for _, _, raw in rows:
            loc = migrate.locator_from_mapping(raw.get("locator"))
            if locator_key(loc) == wanted:
                matching.append(raw)
        if len(matching) == 1:
            return matching[0]
    return None


def raw_observation_row(observation, raw):
    """Find a raw series/points/rows item that emitted this child observation."""
    if not isinstance(raw, Mapping):
        return None
    source_id = source_for_observation(observation)
    raw_id = str(raw.get("observation_id") or "")
    prefix = source_id + "::"
    parent_id = raw_id if raw_id.startswith(prefix) else prefix + raw_id
    values = raw.get("values")
    if not isinstance(values, Mapping):
        return None
    wanted_id = observation.observation_id
    wanted_locator = locator_key(observation.locator)
    matches = []
    for container_name in ("series", "rows", "points"):
        items = values.get(container_name)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            descriptor = {"item": item, "index": index, "container": container_name}
            coord = migrate.select_declared_source(
                migrate.AXIS_TEMPERATURE_K, None, item
            ).amount
            row_extra = migrate.series_row_extra(item, include_locator=False)
            stable_extra = migrate.series_row_extra(item, include_locator=True)
            candidates = set()
            if coord is not None or stable_extra:
                candidates.add(migrate._exploded_point_id(
                    parent_id, descriptor, coord, item, stable_extra
                ))
            if coord is not None:
                candidates.add(migrate.series_point_id(
                    parent_id, temperature=coord, extra=row_extra
                ))
            else:
                legacy_id = migrate._rekey_ordinal_point_observation_id(
                    f"{parent_id}::point:{index}", temperature=None
                )
                if legacy_id.endswith(f"::point:{index}"):
                    printed = item.get("as_published") or item.get("value")
                    if printed is not None:
                        legacy_id = f"{parent_id}::printed:{printed}"
                candidates.add(legacy_id)
            row_locator = locator_key(migrate.locator_from_mapping(item.get("locator")))
            if wanted_id in candidates or (wanted_locator is not None and row_locator == wanted_locator):
                matches.append(item)
    return matches[0] if len(matches) == 1 else None


def raw_row_at_path(raw, path):
    """Resolve the mapping containing the composition key recorded in origin_row."""
    if not isinstance(raw, Mapping) or not isinstance(path, str):
        return None
    tokens = [match.group(1) or int(match.group(2))
              for match in re.finditer(r"([^\.\[\]]+)|\[(\d+)\]", path)]
    current = raw
    for token in tokens[:-1]:
        if isinstance(token, int) and isinstance(current, list) and token < len(current):
            current = current[token]
        elif isinstance(token, str) and isinstance(current, Mapping):
            current = current.get(token)
        else:
            return None
    return current if isinstance(current, Mapping) else None


def text_fields(raw, row=None):
    if not isinstance(raw, Mapping):
        return "system_as_printed: (not supplied); notes: (not supplied)"
    values = raw.get("values")
    sources = [raw]
    if isinstance(values, Mapping):
        sources.append(values)
    if isinstance(row, Mapping):
        sources.append(row)

    def collect(keys):
        out = []
        for source in sources:
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    value = " ".join(value.strip().split())[:600]
                    if value not in out:
                        out.append(value)
        return out

    system_as_printed = collect(("system_as_printed",))
    notes = collect(("notes", "note"))
    phases = collect(("phase",))
    materials = collect(("material", "system", "system_class", "composition", "composition_source", "composition_basis", "used_for"))
    quotes = collect(("quote",))
    parts = [
        "system_as_printed: " + (" | ".join(system_as_printed) if system_as_printed else "(not supplied)"),
        "notes: " + (" | ".join(notes) if notes else "(not supplied)"),
    ]
    if phases:
        parts.append("phase: " + " | ".join(phases))
    if materials:
        parts.append("material/system: " + " | ".join(materials))
    if quotes:
        parts.append("extract quote: " + " | ".join(quotes))
    return "; ".join(parts)


def material_verdict(origin_description, inheritor_description, origin_id="", inheritor_id="", origin_row=""):
    def field_values(description, field):
        values = set()
        for part in description.split("; "):
            prefix = field + ":"
            if part.startswith(prefix):
                tail = part[len(prefix):]
                values.update(" ".join(piece.strip().casefold().split())
                              for piece in tail.split(" | ")
                              if piece.strip() and piece.strip() != "(not supplied)")
        return values

    origin_text = origin_description.casefold()
    inheritor_text = inheritor_description.casefold()
    origin_label = origin_id.casefold().replace("_", " ").replace("-", " ")
    inheritor_label = inheritor_id.casefold().replace("_", " ").replace("-", " ")
    if "starting_composition" in origin_row and "measured residue" in inheritor_text and "not the initial recipe" in inheritor_text:
        return "yes"
    if "natural cai" in inheritor_text and "laboratory evaporation residues" in inheritor_text and "type_b_cai" in origin_text:
        return "yes"
    if "apollo #12022" in origin_text and any(
        token in inheritor_text for token in ("lms-1", "lms 1", "feO system", "al2o3-k2o system", "al2o3-cao-na2o system")
    ):
        return "yes"
    explicit_material_pairs = (
        ("lunar basalt", "bulk silicate earth"),
        ("lunar basalt", "bse"),
        ("apollo 12022", "lms 1"),
        ("apollo 12022", "snyder"),
    )
    for left, right in explicit_material_pairs:
        if (left in origin_label and right in inheritor_label) or (right in origin_label and left in inheritor_label):
            return "yes"

    origin_printed = field_values(origin_description, "system_as_printed")
    inheritor_printed = field_values(inheritor_description, "system_as_printed")
    if origin_printed and inheritor_printed:
        return "no" if origin_printed & inheritor_printed else "yes"
    origin_phase = field_values(origin_description, "phase")
    inheritor_phase = field_values(inheritor_description, "phase")
    generic_phase_tokens = {
        "abundance", "and", "condensed", "gas", "glass", "liquid", "melt",
        "model", "phase", "reference", "solid", "stage", "the",
    }
    if origin_phase and inheritor_phase:
        origin_tokens = {
            token for phase in origin_phase
            for token in re.split(r"[^a-z0-9]+", phase)
            if token and token not in generic_phase_tokens
        }
        inheritor_tokens = {
            token for phase in inheritor_phase
            for token in re.split(r"[^a-z0-9]+", phase)
            if token and token not in generic_phase_tokens
        }
        if origin_phase & inheritor_phase or origin_tokens & inheritor_tokens:
            return "no"
        if origin_tokens and inheritor_tokens:
            return "yes"
    origin_material = field_values(origin_description, "material/system")
    inheritor_material = field_values(inheritor_description, "material/system")
    if origin_material and inheritor_material:
        if origin_material == inheritor_material or origin_material & inheritor_material:
            return "no"
        if "silicate_melt" not in origin_material | inheritor_material:
            return "yes"
    if origin_label and inheritor_label and origin_label == inheritor_label:
        return "no"
    if origin_description == inheritor_description and origin_description != "(not supplied in extract)":
        return "no"
    return "cannot tell from the extract"


def markdown_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = migrate.discover_extracts(ROOT / "data" / "literature" / "extracts")
    docs = {}
    source_by_path = {}
    for path in paths:
        doc = migrate.load_yaml(path)
        if not isinstance(doc, Mapping):
            continue
        source_id = str(doc.get("source_id") or path.stem)
        docs[source_id] = doc
        source_by_path[path.relative_to(ROOT).as_posix()] = source_id
    raw_lookup = raw_observation_lookup(docs)

    events = []
    declarations = defaultdict(list)
    context = {}
    original_printed = migrate._printed_composition_from_roots
    original_ensure = migrate.Migrator._ensure_experiment
    original_registries = migrate.Migrator._lift_extract_registries

    def tracked_printed(roots, vocabulary, *, fallback_locator=None):
        result = original_printed(roots, vocabulary, fallback_locator=fallback_locator)
        if context and result is not None and result.state.is_value:
            nodes = map_nodes(roots, vocabulary, fallback_locator)
            fp = fingerprint(result)
            wanted_loc = locator_key(result.locator)
            matching = [n for n in nodes if n["fingerprint"] == fp]
            selected = next((n for n in matching if n["locator"] == wanted_loc), None)
            if selected is None and matching:
                selected = matching[0]
            events.append({
                **context,
                "fingerprint": fp,
                "locator": wanted_loc,
                "origin_row": selected["row"] if selected else "(row location unavailable)",
                "origin_description": (
                    text_fields(selected["description"]) if selected and selected["description"]
                    else "(not supplied in extract)"
                ),
            })
        return result

    def tracked_ensure(self, *args, **kwargs):
        bound = __import__("inspect").signature(original_ensure).bind(self, *args, **kwargs)
        data = bound.arguments
        source_path = str(data.get("source") or "")
        source_id = source_by_path.get(source_path, Path(source_path).stem if source_path else "")
        prior = dict(context)
        context.clear()
        if source_id:
            context.update({
                "experiment_id": str(data.get("experiment_id") or ""),
                "source_id": source_id,
                "origin_observation": str(data.get("observation_id") or ""),
                "source_path": source_path,
            })
        try:
            return original_ensure(self, *args, **kwargs)
        finally:
            context.clear()
            context.update(prior)

    def tracked_registries(self, doc, *, work, source_key):
        for item in canonical_declared_experiments(doc, work.work_id):
            if not item["source_id"]:
                item["source_id"] = source_by_path.get(source_key, Path(source_key).stem)
            declarations[item["experiment_id"]].append(item)
        return original_registries(self, doc, work=work, source_key=source_key)

    migrate._printed_composition_from_roots = tracked_printed
    migrate.Migrator._ensure_experiment = tracked_ensure
    migrate.Migrator._lift_extract_registries = tracked_registries
    migrator = migrate.Migrator(root=Path.cwd(), index={}, aliases={})
    for path in paths:
        migrator._migrate_extract(path)
    migrator.finalize()

    result = migrator.result
    context_for_score = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        origins={},
        extract_review={},
        hostname=socket.gethostname(),
        benches=result.benches,
    )
    candidate_ids = {o.observation_id for o in comparison_candidates(context_for_score)}
    observations_by_experiment = defaultdict(list)
    for observation in result.observations.values():
        observations_by_experiment[observation.experiment_id].append(observation)

    source_events = defaultdict(list)
    for event in events:
        source_events[event["experiment_id"]].append(event)

    lifted = []
    for experiment_id, experiment in result.experiments.items():
        located = experiment.sample.printed_composition
        if located is None or not located.state.is_value:
            continue
        final_fp = fingerprint(located)
        final_loc = locator_key(located.locator)
        declared = [item for item in declarations.get(experiment_id, ())
                    if item["fingerprint"] == final_fp]
        matching_events = [item for item in source_events.get(experiment_id, ())
                           if item["fingerprint"] == final_fp and item["locator"] == final_loc]
        if declared:
            origin = dict(declared[0])
            origin.update({"origin_observation": "", "origin_row": "experiment.sample.printed_composition",
                           "origin_description": "(experiment/sample declaration)", "kind": "experiment/sample declaration"})
        elif matching_events:
            origin = dict(matching_events[0])
            origin["kind"] = "extract observation row"
        else:
            same_fp_events = [item for item in source_events.get(experiment_id, ())
                              if item["fingerprint"] == final_fp]
            if not same_fp_events:
                continue
            origin = dict(same_fp_events[0])
            origin["kind"] = "extract observation row (locator did not match final merge)"

        rows = sorted(observations_by_experiment.get(experiment_id, ()), key=lambda o: o.observation_id)
        raw_owner_ids = {
            event["origin_observation"]
            for event in source_events.get(experiment_id, ())
            if event["fingerprint"] == final_fp
        }
        own_by_id = {
            o.observation_id: own_compositions(o)
            | ({final_fp} if o.observation_id in raw_owner_ids else set())
            for o in rows
        }
        inherited = []
        for observation in rows:
            if own_by_id[observation.observation_id]:
                continue
            normalized = normalized_composition(experiment, None, observation)
            selected = normalized.selected
            if selected is not None and selected.route == "normalized_printed_composition":
                inherited.append(observation)
        own_count = sum(bool(own_by_id[o.observation_id]) for o in rows)
        all_rows_same = bool(rows) and all(final_fp in own_by_id[o.observation_id] for o in rows)
        if inherited and not (origin["kind"].startswith("experiment/sample declaration") or all_rows_same):
            class_name = "B"
        else:
            class_name = "A"
        origin_source = origin.get("source_id") or (rows[0].source_id if rows else "")
        lifted.append({
            "experiment_id": experiment_id,
            "source_id": origin_source,
            "origin": origin,
            "experiment": experiment,
            "rows": rows,
            "own_count": own_count,
            "inherited": inherited,
            "class": class_name,
            "fingerprint": final_fp,
            "all_rows_same": all_rows_same,
        })

    csv_rows = []
    b_entries = []
    for item in lifted:
        if item["class"] != "B":
            continue
        origin = item["origin"]
        origin_raw = None
        origin_observation_id = origin.get("origin_observation", "")
        if origin_observation_id:
            origin_raw = match_raw_observation(
                type("Origin", (), {
                    "source_id": origin.get("source_id", ""),
                    "observation_id": origin_observation_id,
                    "locator": None,
                })(), raw_lookup,
            )
        origin_description = text_fields(origin_raw) if origin_raw is not None else origin.get(
            "origin_description", "(not supplied in extract)"
        )
        if origin_raw is not None:
            origin_description = text_fields(
                origin_raw, raw_row_at_path(origin_raw, origin.get("origin_row", ""))
            )
        for observation in item["inherited"]:
            raw = match_raw_observation(observation, raw_lookup)
            description = text_fields(raw, raw_observation_row(observation, raw))
            different = material_verdict(
                origin_description,
                description,
                origin_observation_id,
                observation.observation_id,
                origin.get("origin_row", ""),
            )
            candidate = observation.observation_id in candidate_ids
            row = {
                "source_id": source_for_observation(observation),
                "experiment_id": item["experiment_id"],
                "origin_observation": origin.get("origin_observation", ""),
                "origin_row": origin.get("origin_row", ""),
                "inheriting_observation_id": observation.observation_id,
                "quantity": quantity_name(observation),
                "species": observation.identity.species.formula,
                "evidence": evidence_name(observation),
                "different_material": different,
                "is_comparison_candidate": "yes" if candidate else "no",
            }
            csv_rows.append(row)
            b_entries.append({
                **row,
                "description": description,
                "origin_description": origin_description,
                "candidate": candidate,
            })

    columns = ["source_id", "experiment_id", "origin_observation", "origin_row",
               "inheriting_observation_id", "quantity", "species", "evidence",
               "different_material", "is_comparison_candidate"]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_rows)

    class_counts = Counter(item["class"] for item in lifted)
    b_inheriting_count = sum(len(item["inherited"]) for item in lifted if item["class"] == "B")
    b_candidate_count = sum(entry["candidate"] for entry in b_entries)
    b_source_counts = Counter(entry["source_id"] for entry in b_entries)
    b_lost_by_source = defaultdict(list)
    for entry in b_entries:
        b_lost_by_source[entry["source_id"]].append(entry["inheriting_observation_id"])

    lines = [
        "# Experiment sample composition inheritance sweep",
        "",
        f"Worktree: `.` at `{__import__('subprocess').check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}`.",
        f"`engines/engines.local.toml` exists: **{(ROOT / 'engines/engines.local.toml').exists()}**.",
        f"Migrated {len(paths)} extracts with `Migrator(root=Path.cwd(), index={{}}, aliases={{}})._migrate_extract(path)` and one `finalize()`.",
        "",
        "## Code path",
        "",
        "- `migrate.py:8728-8784`, `_printed_composition_from_roots`, walks each supplied root to depth 14. It accepts keys whose vocabulary field is `sample.printed_composition`, keeps numeric children, and requires a locator. Its fingerprint is the sorted `(species, normalized decimal string)` map. More than one fingerprint returns `None`, except a unique fingerprint among `_CHARGE_PRINTED_COMPOSITION_NAMES` wins; exactly one fingerprint returns the first found map and locator.",
        "- `migrate.py:8790-8799`, `_lab_roots`, supplies observation equipment and values. `migrate.py:9096-9152`, `sample_from_equipment`, calls the root walker and stores the result as `Sample.printed_composition`; for oxide maps it may also create `initial_composition`.",
        "- `migrate.py:10290-10337`, `_ensure_experiment`, invokes `sample_from_equipment` for an extract observation's equipment/values; existing samples merge in `_merge_experiment_lab_params` at `migrate.py:8992-9032`. `_prefer_located` at `migrate.py:8847-8884` keeps equal numeric maps, but returns `None` for unequal value maps. The experiment registry path is separate: `_lift_extract_registries` reads declared `sample`/`charge` at `migrate.py:10739-10885` through `experiment_from_plain` / `_sample_from_plain` (`migrate.py:1611-1649, 1992-2043`).",
        "- An observation-row map therefore becomes `experiment.sample.printed_composition` when one `_ensure_experiment` call sees exactly one accepted fingerprint (or a unique preferred charge fingerprint) in that observation's equipment/values roots, and the merged sample slot is empty or has an equal value. A later unequal row map clears the merged slot; a row with no map does not. This is a per-call fingerprint rule followed by merge behavior, not a corpus-wide count of all maps.",
        "- `waypoints.py:716-734`, `normalized_composition`, chooses `observation.point_conditions` when that key exists and otherwise falls back to `experiment.sample`; it also normalizes the map at lines 745-908. An inherited selected route is `normalized_printed_composition`.",
        "- `waypoints.py:953-975`, `identity_composition_waypoint`, exposes only a row identity `Composition` on mole-fraction or mol-inventory basis. `consumer_inputs.py:69-90, 124-125, 184-190` gathers the normalized waypoint and that separate identity waypoint. `bench.py:315-323` lets an observation route or identity composition override the sample route for melt-activity inputs; `bench.py:373-401` passes `normalized_composition` directly to engine-point requests and records its route.",
        "- `waypoints.py:2018-2030` combines engine-point and melt-activity readiness. Validity does not call `normalized_composition`: `validity.py:902-917` uses observation identity composition, then point `composition`/`sample_composition`, then `experiment.sample.initial_composition`; the bulk-species gate uses that result at `validity.py:991-1035`. Scoring's candidate resolver uses identity/point composition (`score.py:2595-2657`, `3061-3125`); its validity checks call `run_validity_gates` (`score.py:1678-1684`). `comparison_candidates` (`score.py:4779-4790`) itself filters only measured evidence and admitted/pending status.",
        "",
        "## Results",
        "",
        f"- Final sample printed compositions attributed to the extract root path or a declared experiment/sample: **{len(lifted)} experiments**; A: **{class_counts['A']}**, B: **{class_counts['B']}**. A includes experiments with no inheriting observations; the class rule is applied to all lifted experiments so the counts add to the total.",
        f"- Class B inheriting observations: **{b_inheriting_count}** across **{len(b_source_counts)} sources**; comparison candidates: **{b_candidate_count}**.",
        f"- Null hypothesis: **{'refuted' if class_counts['B'] else 'holds'}** for the migrated extract store.",
        "",
        "### Lifted experiments",
        "",
        "| Class | Source ID | Experiment ID | Origin | Observations | Own row/identity composition | Inheriting only |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for item in sorted(lifted, key=lambda x: (x["source_id"], x["experiment_id"])):
        origin = item["origin"]
        where = origin.get("origin_row", "")
        if origin.get("origin_observation"):
            where = f"{origin['origin_observation']} / {where}"
        else:
            where = "experiment/sample declaration"
        lines.append(
            f"| {item['class']} | {markdown_cell(item['source_id'])} | {markdown_cell(item['experiment_id'])} | {markdown_cell(where)} | {len(item['rows'])} | {item['own_count']} | {len(item['inherited'])} |"
        )

    lines.extend(["", "### Class B inheriting observations", ""])
    if not b_entries:
        lines.append("None.")
    else:
        lines.extend([
            "Each description below comes from the extract YAML only; no PDFs were opened. `system_as_printed` and `notes` are shown when present; the row's own `phase`, `material`/`system_class`, `quote`, or usage text is included when those are the available descriptions. `different_material` is `yes` only when extract text/row labels identify incompatible systems, `no` when they identify the same material/system, and otherwise `cannot tell from the extract`.",
            "",
            "| Source | Experiment | Origin observation / row | Inheriting observation | Quantity | Species | Evidence | system_as_printed / notes | Different material? | Comparison candidate? |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ])
        for entry in b_entries:
            origin_label = f"{entry['origin_observation']} / {entry['origin_row']}"
            lines.append(
                "| " + " | ".join(markdown_cell(value) for value in (
                    entry["source_id"], entry["experiment_id"], origin_label,
                    entry["inheriting_observation_id"], entry["quantity"], entry["species"],
                    entry["evidence"], entry["description"], entry["different_material"],
                    entry["is_comparison_candidate"],
                )) + " |"
            )

    lines.extend(["", "### Rows that would lose the sample fallback under the proposed rule", ""])
    if b_lost_by_source:
        for source_id in sorted(b_lost_by_source):
            ids = sorted(set(b_lost_by_source[source_id]))
            lines.append(f"- `{source_id}`: {len(ids)} observations — " + ", ".join(f"`{oid}`" for oid in ids))
    else:
        lines.append("None.")
    lines.extend([
        "",
        "Smallest reader rule: retain a sample-level fallback only for an explicit experiment/sample declaration or when the same map is present on every observation row in that experiment; if it is present on a strict subset, keep it local to those rows. This would remove the inherited sample route only from the listed Class B rows; their own row-local/identity maps are unaffected.",
        "",
        "## Artifacts",
        "",
        f"- `{CSV_PATH}` — Class B inherited rows in the requested column order.",
        f"- `docs/battery/b718-sweep.py` — instrumentation script used for this sweep.",
    ])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "extracts_migrated": len(paths),
        "lifted_experiments": len(lifted),
        "A": class_counts["A"],
        "B": class_counts["B"],
        "class_b_inheriting_rows": b_inheriting_count,
        "class_b_sources": len(b_source_counts),
        "comparison_candidates": b_candidate_count,
        "events": len(events),
        "artifacts": [str(REPORT_PATH), str(CSV_PATH), "docs/battery/b718-sweep.py"],
    }, indent=2))


if __name__ == "__main__":
    main()
