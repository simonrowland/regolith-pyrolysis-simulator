"""Migration pins: representative source rows and byte-exact whole-store output.

The representative pins exercise individual source shapes through
``migrate(..., write=False)`` without ``observation_from_plain`` or
``load_migrated_store``. The whole-store pin runs the complete migration and
compares the writer's complete output map against the checked-in derived store.
"""

from __future__ import annotations

import base64
import json
import lzma
import re
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import yaml

from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _store_output_file_bytes,
    _store_regeneration_differences,
    migrate,
    to_plain,
    write_outputs,
)


_EXTRACT_ROWS = (
    ("kems-022-demaria-1971.yaml", "demaria_1971_al2o_psat_table4"),
    ("kems-022-demaria-1971.yaml", "demaria_1971_o2_psat_table1_alkali_range"),
    ("kems-042-plante-1979.yaml", "plante1979_table2_k2o_s1115_000_1259K"),
    ("kems-042-plante-1979.yaml", "plante1979_table2_s1214_r037_quoted"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_table1_fresnel_pyrolysis_log"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_fig6_hsc_1e-1_Torr_figure_only"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_fig7_hsc_1e-7_Torr_figure_only"),
    (
        "kems-026-markova-1984.yaml",
        "markova_1984_table2_residual_melt_VI_lherzolite",
        "::mass_loss_fraction::T=2156.15:h=e9110f31a14b",
    ),
)
_COMPILATION = (
    "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
    "records/robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04.json"
)
_FAST_SAFE_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _one_extract_row(tmp_path: Path, filename: str, observation_id: str):
    source_path = REPO_ROOT / "data/literature" / "extracts" / filename
    document = yaml.load(
        source_path.read_text(encoding="utf-8"), Loader=_FAST_SAFE_YAML_LOADER
    )
    matches = []
    species = {}
    for formula, payload in document["species"].items():
        observations = payload.get("observations", [])
        selected = [row for row in observations if row.get("observation_id") == observation_id]
        if selected:
            matches.extend(selected)
            species[formula] = {**payload, "observations": selected}
    assert len(matches) == 1, observation_id
    document["species"] = species

    root = tmp_path / observation_id
    literature = root / "data" / "literature"
    extracts = literature / "extracts"
    extracts.mkdir(parents=True)
    (literature / "compilations").mkdir()
    (extracts / filename).write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    source = document.get("source") or {}
    (literature / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [{
                    "source_id": document["source_id"],
                    "citation": source.get("citation") or document["source_id"],
                    "doi": source.get("doi"),
                }],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return migrate(root, write=False), f"{document['source_id']}::{observation_id}"


def _one_compilation_table(tmp_path: Path):
    source_path = REPO_ROOT / _COMPILATION
    root = tmp_path / "b1452"
    records = root / "data" / "literature" / "compilations" / "b1452" / "records"
    records.mkdir(parents=True)
    (records / source_path.name).write_bytes(source_path.read_bytes())
    migrator = Migrator(root, index={})
    migrator.migrate_compilations(records)
    result = migrator.result
    observation_id = sorted(result.observations)[0]
    return result, observation_id


def _state_output(state: object, axis: str) -> object:
    plain = to_plain(state)
    if isinstance(plain, dict) and plain.get("tag") == "not_applicable":
        return {"tag": "not_applicable"}
    if (
        axis == "composition"
        and isinstance(plain, dict)
        and plain.get("tag") == "value"
        and isinstance(plain.get("value"), dict)
    ):
        composition = plain["value"]
        plain["value"] = {
            key: composition.get(key)
            for key in (
                "amount_basis",
                "basis",
                "components",
                "proxy_flag",
                "proxy_source",
                "analysis_selection_rule",
                "input_provenance",
            )
        }
    return plain


def _pinned_output(result, observation_id: str) -> dict[str, object]:
    observation = result.observations[observation_id]
    queued = [entry for entry in result.queue if entry.observation_id == observation_id]
    identity = {
        key: _state_output(value, key)
        for key, value in to_plain(observation.identity).items()
    }

    point_conditions = (
        {
            key: {
                "state": _state_output(value.state, key),
                "locator": to_plain(value.locator),
                **({"inference": to_plain(value.inference)} if value.inference else {}),
            }
            for key, value in sorted(observation.point_conditions.items())
        }
        if observation.point_conditions is not None
        else None
    )
    return {
        "identity": identity,
        "point_conditions": point_conditions,
        "evidence_class": to_plain(observation.evidence.class_),
        "admission_status": observation.admission.status.value,
        "locator": to_plain(observation.locator),
        "queue": {
            "queued": bool(queued),
            "why": [entry.why for entry in queued] if queued else ["no queue entry"],
        },
    }


def _observation_rows(output: bytes) -> dict[str, dict[str, object]]:
    document = yaml.load(output, Loader=_FAST_SAFE_YAML_LOADER)
    if not isinstance(document, dict) or not isinstance(document.get("observations"), list):
        return {}
    return {
        str(row["observation_id"]): row
        for row in document["observations"]
        if isinstance(row, dict) and row.get("observation_id") is not None
    }


def _field_differences(path: str, stored: object, regenerated: object) -> list[str]:
    return [
        f"{change['field']}: stored={json.dumps(change['old'], sort_keys=True, default=str)}; "
        f"regenerated={json.dumps(change['new'], sort_keys=True, default=str)}"
        for change in _field_change_records(path, stored, regenerated)
    ]


def _field_change_records(path: str, stored: object, regenerated: object) -> list[dict[str, object]]:
    if isinstance(stored, dict) and isinstance(regenerated, dict):
        differences = []
        for key in sorted(stored.keys() | regenerated.keys(), key=str):
            if key not in stored or key not in regenerated:
                old = stored[key] if key in stored else "<absent>"
                new = regenerated[key] if key in regenerated else "<absent>"
                differences.append({"field": f"{path}.{key}", "old": old, "new": new})
                continue
            differences.extend(_field_change_records(
                f"{path}.{key}", stored[key], regenerated[key],
            ))
        return differences
    if isinstance(stored, list) and isinstance(regenerated, list):
        if len(stored) != len(regenerated):
            return [{"field": path, "old": stored, "new": regenerated}]
        differences = []
        for index, (old, new) in enumerate(zip(stored, regenerated)):
            differences.extend(_field_change_records(f"{path}[{index}]", old, new))
        return differences
    if stored == regenerated:
        return []
    return [{"field": path, "old": stored, "new": regenerated}]


def _compact_difference_paths(differences: list[str], prefix: str) -> str:
    fields = [difference.split(": stored=", 1)[0] for difference in differences]
    indexed: dict[str, list[int]] = {}
    compact = []
    marker = f"{prefix}.experiments["
    for field in fields:
        if not field.startswith(marker):
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        closing = field.find("]", len(marker))
        if closing == -1:
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        try:
            index = int(field[len(marker):closing])
        except ValueError:
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        suffix = field[closing + 1:]
        indexed.setdefault(suffix, []).append(index)
    for suffix, indices in sorted(indexed.items()):
        ordered = sorted(set(indices))
        ranges = []
        start = previous = ordered[0]
        for index in ordered[1:]:
            if index == previous + 1:
                previous = index
                continue
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = index
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        compact.append(f"experiments[{','.join(ranges)}]{suffix}")
    return ", ".join(compact)


def _compact_observation_ids(observation_ids: list[str]) -> list[dict[str, object]]:
    remaining = set(observation_ids)
    compact = []
    while remaining:
        templates: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for observation_id in sorted(remaining):
            matches = list(re.finditer(r"\d+", observation_id))
            if not matches:
                continue
            pieces = []
            numbers = []
            previous = 0
            for match in matches:
                pieces.extend((observation_id[previous:match.start()], "{n}"))
                numbers.append(match.group())
                previous = match.end()
            pieces.append(observation_id[previous:])
            template = "".join(pieces)
            templates.setdefault(template, []).append((tuple(numbers), observation_id))
        candidates = {
            template: members
            for template, members in templates.items()
            if len(members) > 1
        }
        if not candidates:
            break
        template, members = max(
            candidates.items(),
            key=lambda item: len(item[1]),
        )
        number_tuples = sorted({numbers for numbers, _ in members})
        one_numeric_token = all(len(numbers) == 1 for numbers in number_tuples)
        canonical_numbers = one_numeric_token and all(
            number == "0" or not number.startswith("0")
            for numbers in number_tuples
            for number in numbers
        )
        if canonical_numbers:
            numbers = sorted(int(number_tuple[0]) for number_tuple in number_tuples)
            ranges = []
            start = previous = numbers[0]
            for number in numbers[1:]:
                if number == previous + 1:
                    previous = number
                    continue
                ranges.append(str(start) if start == previous else f"{start}-{previous}")
                start = previous = number
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            compact.append({"template": template, "numbers": ranges})
        else:
            compact.append({
                "template": template,
                "values": [list(numbers) for numbers in number_tuples],
            })
        remaining.difference_update(observation_id for _, observation_id in members)
    if remaining:
        compact.append({"ids": sorted(remaining)})
    return compact


def _store_difference_payload(
    stored: dict[str, bytes], regenerated: dict[str, bytes], paths: list[str]
) -> dict[str, object]:
    details = {
        "changed_store_paths": len(paths),
        "observations": {},
        "work": [],
        "other": [],
    }
    for path in paths:
        stored_bytes = stored.get(path)
        regenerated_bytes = regenerated.get(path)
        if stored_bytes is None or regenerated_bytes is None:
            present_bytes = stored_bytes if stored_bytes is not None else regenerated_bytes
            details["other"].append({"path": path, "fields": [
                "missing from store" if stored_bytes is None else "unexpected in store"
            ]})
            if present_bytes is not None:
                observation_ids = sorted(_observation_rows(present_bytes))
                details["observations"][path] = {
                    "source_ids": [Path(path).stem],
                    "fields": [
                        "row missing from store" if stored_bytes is None else "unexpected row"
                    ],
                    "ids": _compact_observation_ids(
                        [
                            observation_id.removeprefix(f"{Path(path).stem}::")
                            for observation_id in observation_ids
                        ]
                    ),
                    "rows": [],
                }
            continue
        if path.endswith(".md"):
            details["other"].append({"path": path, "fields": [
                f"bytes differ ({len(stored_bytes)} stored, {len(regenerated_bytes)} regenerated)"
            ]})
            continue
        if path.startswith("data/literature/works/"):
            stored_work = yaml.load(stored_bytes, Loader=_FAST_SAFE_YAML_LOADER)
            regenerated_work = yaml.load(regenerated_bytes, Loader=_FAST_SAFE_YAML_LOADER)
            differences = _field_differences("work", stored_work, regenerated_work)
            work = stored_work.get("work") if isinstance(stored_work, dict) else None
            if not isinstance(work, dict) and isinstance(regenerated_work, dict):
                work = regenerated_work.get("work")
            source_ids = work.get("source_ids", []) if isinstance(work, dict) else []
            work_id = work.get("work_id") if isinstance(work, dict) else None
            source_label = (
                ",".join(map(str, source_ids))
                if source_ids
                else f"work_id={work_id}"
            )
            details["work"].append({
                "path": path,
                "source": source_label,
                "source_ids": list(map(str, source_ids)),
                "fields": _compact_difference_paths(differences, "work")
                if differences
                else "bytes differ without a YAML field difference",
                "changes": _field_change_records("work", stored_work, regenerated_work),
            })
            continue
        stored_rows = _observation_rows(stored_bytes)
        regenerated_rows = _observation_rows(regenerated_bytes)
        changed_rows = [
            observation_id
            for observation_id in sorted(stored_rows.keys() | regenerated_rows.keys())
            if stored_rows.get(observation_id) != regenerated_rows.get(observation_id)
        ]
        if changed_rows:
            source_fields = set()
            row_details = []
            for observation_id in changed_rows:
                changes = _field_change_records(
                    "observation",
                    stored_rows.get(observation_id),
                    regenerated_rows.get(observation_id),
                )
                field_paths = [str(change["field"]) for change in changes]
                if not field_paths:
                    field_paths = ["bytes differ without a YAML field difference"]
                source_fields.update(field_paths)
                row = regenerated_rows.get(observation_id) or stored_rows.get(observation_id) or {}
                source_id = str(row.get("source_id") or Path(path).stem)
                row_details.append({
                    "id": observation_id.removeprefix(f"{Path(path).stem}::"),
                    "source_id": source_id,
                    "locator": row.get("locator"),
                    "changes": changes,
                })
            details["observations"][path] = {
                "source_ids": sorted({str(row["source_id"]) for row in row_details}),
                "fields": sorted(source_fields),
                "ids": _compact_observation_ids(
                    [
                        observation_id.removeprefix(f"{Path(path).stem}::")
                        for observation_id in changed_rows
                    ]
                ),
                "rows": row_details,
            }
        else:
            details["other"].append({"path": path, "fields": [
                f"bytes differ ({len(stored_bytes)} stored, {len(regenerated_bytes)} regenerated); "
                "no observation-row difference"
            ]})
    return details


def _store_difference_details(
    stored: dict[str, bytes], regenerated: dict[str, bytes], paths: list[str]
) -> str:
    details = _store_difference_payload(stored, regenerated, paths)
    raw = json.dumps(details, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(lzma.compress(raw, preset=9)).decode("ascii")
    return f"store_delta_lzma_base64={payload}"


@lru_cache(maxsize=32)
def _source_extract_rows(source_id: str) -> dict[str, dict[str, object]]:
    path = REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    if not path.is_file():
        return {}
    document = yaml.load(path.read_bytes(), Loader=_FAST_SAFE_YAML_LOADER)
    rows = {}
    for species in document.get("species", {}).values():
        for row in species.get("observations", []):
            local_id = row.get("observation_id")
            if isinstance(local_id, str):
                rows[local_id] = row
    return rows


def _source_extract_row(source_id: str, observation_id: str) -> dict[str, object] | None:
    local_id = observation_id.removeprefix(f"{source_id}::")
    return _source_extract_rows(source_id).get(local_id)


def test_source_extract_rows_load_each_source_once(monkeypatch) -> None:
    source_id = "zhao-2019-ce2o3-ternary-thermodynamics"
    _source_extract_rows.cache_clear()
    original_load = yaml.load
    load_count = 0

    def counted_load(*args, **kwargs):
        nonlocal load_count
        load_count += 1
        return original_load(*args, **kwargs)

    monkeypatch.setattr(yaml, "load", counted_load)
    assert _source_extract_row(
        source_id, f"{source_id}::zhao_2019_table_iv_sample_301_ce2o3_activity"
    ) is not None
    assert _source_extract_row(
        source_id, f"{source_id}::zhao_2019_table_iv_sample_302_ce2o3_activity"
    ) is not None
    assert load_count == 1


def _source_cell_basis(
    source_row: dict[str, object] | None, field: str
) -> tuple[object, list[object], object]:
    if source_row is None:
        return "source extract row unavailable", [], None
    values = source_row.get("values")
    values = values if isinstance(values, dict) else {}
    field_name = field.removeprefix("observation.")
    candidates: list[object] = []
    if field_name == "value.point":
        quantity = values.get("quantity")
        if isinstance(quantity, str) and quantity in values:
            candidates.append(values[quantity])
            as_published = values.get(f"{quantity}_as_published")
            if as_published is not None:
                candidates.append(as_published)
        cell = {
            "quantity": quantity,
            "printed_value": values.get(f"{quantity}_as_published") if quantity else None,
            "scale": values.get(f"printed_{quantity}_scale") if quantity else None,
            "extracted_value": values.get(quantity) if quantity else None,
            "units": source_row.get("units"),
            "locator": source_row.get("locator"),
        }
    elif "temperature_K" in field_name or field_name.endswith(".T_C"):
        if source_row.get("T_C") is not None:
            candidates.append(source_row["T_C"])
        cell = {
            "T_C": source_row.get("T_C"),
            "printed_condition": source_row.get("point_conditions", {}).get("T_C")
            if isinstance(source_row.get("point_conditions"), dict) else None,
            "locator": source_row.get("locator"),
        }
    elif "fO2" in field_name or "oxygen" in field_name.lower():
        oxygen = values.get("oxygen_partial_pressure")
        if isinstance(oxygen, dict):
            candidates.append(oxygen.get("value"))
        cell = {
            "oxygen_partial_pressure": oxygen,
            "locator": source_row.get("locator"),
        }
    else:
        leaf = field_name.rsplit(".", 1)[-1]
        for container in (values, source_row):
            if leaf in container:
                candidates.append(container[leaf])
        cell = {
            "source_values": values,
            "T_C": source_row.get("T_C"),
            "units": source_row.get("units"),
            "locator": source_row.get("locator"),
        }
    basis = source_row.get("derivation") or source_row.get("values", {}).get(
        f"{values.get('quantity', '')}_derivation", None
    ) if isinstance(source_row.get("values"), dict) else source_row.get("derivation")
    return cell, candidates, basis


def _side_matches_cell(side: object, candidates: list[object]) -> bool:
    if side in candidates:
        return True
    if isinstance(side, (str, int, float)) and not isinstance(side, bool):
        try:
            side_number = float(side)
        except (TypeError, ValueError):
            return False
        for candidate in candidates:
            try:
                if side_number == float(candidate):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _field_locator(
    row: dict[str, object], source_row: dict[str, object] | None, field: str
) -> object:
    prefix = "observation.point_conditions."
    if field.startswith(prefix) and source_row is not None:
        condition = field[len(prefix):].split(".", 1)[0]
        conditions = source_row.get("point_conditions")
        if isinstance(conditions, dict):
            condition_value = conditions.get(condition)
            if isinstance(condition_value, dict) and condition_value.get("locator"):
                return condition_value["locator"]
    return (source_row or {}).get("locator") or row.get("locator")


def _format_store_delta_report(
    details: dict[str, object], mode: str, source_filter: set[str]
) -> str:
    observations = details["observations"]
    work = details["work"]
    other = details["other"]
    assert isinstance(observations, dict) and isinstance(work, list) and isinstance(other, list)
    grouped: dict[str, dict[str, object]] = {}

    def source_group(source_id: str) -> dict[str, object]:
        return grouped.setdefault(source_id, {
            "observation_paths": set(), "work_paths": set(), "score_rows": set(),
            "fields": set(), "value_fields": set(), "out_of_scope": False,
        })

    for path, record in observations.items():
        assert isinstance(record, dict)
        for row in record.get("rows", []):
            assert isinstance(row, dict)
            group = source_group(str(row["source_id"]))
            group["observation_paths"].add(path)
            group["score_rows"].add(str(row["id"]))
            for change in row.get("changes", []):
                field = str(change["field"]).removeprefix("observation.")
                group["fields"].add(field)
                if field.endswith((".value", ".point")):
                    group["value_fields"].add(field)
                if not _field_is_fold22_scope(
                    str(change["field"]), change, str(row["source_id"]),
                    str(row["id"]), row.get("changes", []),
                ):
                    group["out_of_scope"] = True
        if not record.get("rows"):
            for source_id in record.get("source_ids", [Path(path).stem]):
                source_group(str(source_id))["observation_paths"].add(path)
    for record in work:
        assert isinstance(record, dict)
        source_ids = record.get("source_ids") or ["__work_without_source_id__"]
        for source_id in source_ids:
            source_group(str(source_id))["work_paths"].add(str(record["path"]))
            group = source_group(str(source_id))
            for field in record.get("fields", []):
                if isinstance(field, str):
                    group["fields"].add(field)
            if record.get("changes"):
                group["out_of_scope"] = True
    for record in other:
        assert isinstance(record, dict)
        source_group("__store_global__").setdefault("other_paths", set()).add(str(record["path"]))

    if mode == "summary":
        output = [
            "STORE DELTA SUMMARY: score rows are changed observation IDs; value-field count is distinct changed leaves ending .value or .point."
        ]
        for source_id in sorted(grouped):
            if source_filter and not _source_in_report_filter(source_id, source_filter):
                continue
            group = grouped[source_id]
            output.append(" | ".join((
                source_id,
                "observation_paths=" + ",".join(sorted(group["observation_paths"])),
                "work_metadata_paths=" + ",".join(sorted(group["work_paths"])),
                "other_paths=" + ",".join(sorted(group.get("other_paths", set()))),
                "changed_score_rows=" + str(len(group["score_rows"])),
                "changed_value_fields=" + str(len(group["value_fields"])),
                "out_of_scope=" + str(bool(group["out_of_scope"])).lower(),
            )))
        unique_paths = set(observations) | {str(item["path"]) for item in work + other}
        output.append(f"TOTAL changed_store_paths={len(unique_paths)}")
        return "\n".join(output)

    decoded: list[str] = []
    for path, record in observations.items():
        assert isinstance(record, dict)
        for row in record.get("rows", []):
            assert isinstance(row, dict)
            source_id = str(row["source_id"])
            if mode == "source" and source_filter and source_id not in source_filter:
                continue
            if mode == "scope" and source_filter and not _source_in_report_filter(source_id, source_filter):
                continue
            source_row = _source_extract_row(source_id, str(row["id"]))
            for change in row.get("changes", []):
                field = str(change["field"])
                if mode == "scope" and _field_is_fold22_scope(
                    field, change, source_id, str(row["id"]), row.get("changes", [])
                ):
                    continue
                cell, candidates, basis = _source_cell_basis(source_row, field)
                old_match = _side_matches_cell(change["old"], candidates)
                new_match = _side_matches_cell(change["new"], candidates)
                matched = "both" if old_match and new_match else "old" if old_match else "new" if new_match else "neither"
                locator = _field_locator(row, source_row, field)
                if mode == "scope":
                    printed_cell = cell
                    if isinstance(cell, dict) and "source_values" in cell:
                        printed_cell = candidates if candidates else "<none>"
                    basis_text = "source extract row; see locator and printed cell"
                    decoded.append(" | ".join((
                        source_id,
                        json.dumps(locator, sort_keys=True, default=str),
                        field,
                        "old=" + json.dumps(change["old"], sort_keys=True, default=str),
                        "new=" + json.dumps(change["new"], sort_keys=True, default=str),
                        "printed_cell=" + json.dumps(printed_cell, sort_keys=True, default=str),
                        "matched_side=" + matched,
                        "basis=" + basis_text,
                    )))
                    continue
                decoded.append(" | ".join((
                    source_id,
                    json.dumps(locator, sort_keys=True, default=str),
                    field,
                    "old=" + json.dumps(change["old"], sort_keys=True, default=str),
                    "new=" + json.dumps(change["new"], sort_keys=True, default=str),
                    "printed_cell=" + json.dumps(cell, sort_keys=True, default=str),
                    "matched_side=" + matched,
                    "basis=" + json.dumps(basis, sort_keys=True, default=str),
                )))
    for record in work:
        assert isinstance(record, dict)
        source_ids = list(map(str, record.get("source_ids", [])))
        if mode == "scope":
            report_source_ids = [
                source_id
                for source_id in source_ids
                if not source_filter or _source_in_report_filter(source_id, source_filter)
            ]
            if not report_source_ids:
                continue
        else:
            if mode == "source" and source_filter and not (source_filter & set(source_ids)):
                continue
            report_source_ids = [
                next(iter(sorted(source_filter & set(source_ids))),
                     source_ids[0] if source_ids else "__work_without_source_id__")
            ]
        source_bytes = REPO_ROOT / str(record["path"])
        stored_bytes = source_bytes.read_bytes() if source_bytes.is_file() else b""
        work_doc = yaml.load(stored_bytes, Loader=_FAST_SAFE_YAML_LOADER) if stored_bytes else {}
        work_meta = work_doc.get("work", {}) if isinstance(work_doc, dict) else {}
        if mode == "scope" and not any(
            not _field_is_fold22_scope(str(change["field"]), change, source_id)
            for source_id in report_source_ids
            for change in record.get("changes", [])
        ):
            continue
        for source_id in report_source_ids:
            for change in record.get("changes", []):
                if mode == "scope" and _field_is_fold22_scope(str(change["field"]), change, source_id):
                    continue
                if mode == "scope":
                    decoded.append(" | ".join((
                        source_id,
                        "locator=null",
                        str(change["field"]),
                        "old=" + json.dumps(change["old"], sort_keys=True, default=str),
                        "new=" + json.dumps(change["new"], sort_keys=True, default=str),
                        "printed_cell=<none>",
                        "matched_side=neither",
                        "basis=work metadata; no measurement cell",
                    )))
                    continue
                decoded.append(" | ".join((
                    source_id,
                    json.dumps(work_meta.get("locator"), sort_keys=True, default=str),
                    str(change["field"]),
                    "old=" + json.dumps(change["old"], sort_keys=True, default=str),
                    "new=" + json.dumps(change["new"], sort_keys=True, default=str),
                    "printed_cell=" + json.dumps({"citation": work_meta.get("citation"), "locator": work_meta.get("locator")}, sort_keys=True, default=str),
                    "matched_side=neither",
                    "basis=bibliographic/work metadata, not a measurement cell",
                )))
    header = f"STORE DELTA {mode.upper()} changed_paths={details['changed_store_paths']} decoded_rows={len(decoded)}"
    return "\n".join([header, *decoded])


def _field_is_fold22_scope(
    field: str,
    change: dict[str, object],
    source_id: str,
    observation_id: str = "",
    row_changes: list[dict[str, object]] | None = None,
) -> bool:
    representative_rows = {row[1] for row in _EXTRACT_ROWS}
    representative_rows.update({
        "hastie_1981_fig4_illite_K_SiO_digitized",
        "hastie_1981_fig5_glass_Na_this_work_digitized",
        "hastie_1981_fig11_k1_slag_k2o_gamma_digitized",
        "hastie_1981_fig11_k1_slag_k2o_gamma_tms_open_square",
    })
    if any(
        observation_id == scoped_id or observation_id.startswith(f"{scoped_id}::")
        for scoped_id in representative_rows
    ):
        return True
    normalized = field.removeprefix("observation.")
    rows = row_changes or [change]
    scoped_notice_tokens = (
        "CONDITION_EXCLUDED_TARGET_DEPENDENCY",
        "gas_ratio_requires_bench_but_none_was_supplied",
        "buffer_requires_bench_but_none_was_supplied",
        "DP-010",
    )
    for row_change in rows:
        if not str(row_change.get("field", "")).endswith("notices"):
            continue
        text = json.dumps((row_change.get("old"), row_change.get("new")), default=str)
        if any(token in text for token in scoped_notice_tokens):
            if normalized.startswith(("notices", "identity.fO2_Pa", "value.point")):
                return True
    return False


def _source_in_report_filter(source_id: str, source_filter: set[str]) -> bool:
    for group in source_filter:
        if len(group) == 3 and group[1] == "-" and group[0].isalpha() and group[2].isalpha():
            first = source_id[:1].upper()
            if not first.isalpha():
                first = "A"
            if group[0].upper() <= first <= group[2].upper():
                return True
        elif len(group) == 1 and group.isalpha():
            first = source_id[:1].upper()
            if first == group.upper() or not first.isalpha() and group.upper() == "A":
                return True
        elif source_id == group:
            return True
    return False


def test_representative_migration_rows_match_literal_baseline(tmp_path: Path) -> None:
    actual = {}
    for row in _EXTRACT_ROWS:
        filename, observation_id, *child_suffix = row
        result, parent_id = _one_extract_row(tmp_path, filename, observation_id)
        candidates = sorted(
            oid for oid in result.observations
            if oid == parent_id or oid.startswith(f"{parent_id}::")
        )
        assert candidates, parent_id
        migrated_id = (
            parent_id + child_suffix[0]
            if child_suffix
            else candidates[0]
        )
        assert migrated_id in candidates, migrated_id
        actual[migrated_id] = _pinned_output(result, migrated_id)
    result, observation_id = _one_compilation_table(tmp_path)
    actual[observation_id] = _pinned_output(result, observation_id)

    # Replaced with literal typed output from the first Studio run.
    expected = json.loads(r'''
{
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_fig6_hsc_1e-1_Torr_figure_only": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "model_derived"
    },
    "identity": {
      "quantity": {
        "reason": "unsupported quantity 'equilibrium_composition'",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MLS-1A",
        "phase": {
          "reason": "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "temperature_K": {
        "reason": "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "tag": "unknown"
      }
    },
    "locator": {
      "figure": "6",
      "note": "Figure 6 Equilibrium composition of MLS-1A at 10^{-1} Torr",
      "pdf_page_index": 6
    },
    "point_conditions": null,
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'equilibrium_composition'",
        "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
        "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "regime 'HSC_gibbs_minimization_model' is not a closed method token (kems_effusion alone is insufficient)",
        "derived evidence class with unstated ancestry; queued for page-grounding",
        "derived evidence class with unstated derivation; queued for page-grounding"
      ]
    }
  },
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_fig7_hsc_1e-7_Torr_figure_only": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "model_derived"
    },
    "identity": {
      "quantity": {
        "reason": "unsupported quantity 'equilibrium_composition'",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MLS-1A",
        "phase": {
          "reason": "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "temperature_K": {
        "reason": "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "tag": "unknown"
      }
    },
    "locator": {
      "figure": "7",
      "note": "Figure 7 Equilibrium composition of MLS-1A at 10^{-7} Torr",
      "pdf_page_index": 6
    },
    "point_conditions": null,
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'equilibrium_composition'",
        "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
        "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "regime 'HSC_gibbs_minimization_model' is not a closed method token (kems_effusion alone is insufficient)",
        "derived evidence class with unstated ancestry; queued for page-grounding",
        "derived evidence class with unstated derivation; queued for page-grounding"
      ]
    }
  },
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_table1_fresnel_pyrolysis_log::T=1345.15:row=mgsio3:h=c69dcb1e3dc7": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "reason": "no composition mapped from source",
        "tag": "unknown"
      },
      "exposure": {
        "reason": "no exposure mapped from source",
        "tag": "unknown"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "reason": "no per mapped from source",
        "tag": "unknown"
      },
      "quantity": {
        "tag": "value",
        "value": "mass_loss_fraction"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "reason": "no reservoir mapped from source",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "reason": "no sample_mass_kg mapped from source",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MgSiO3",
        "phase": {
          "reason": "phase string 'fresnel_lens_vacuum_pyrolysis_mixed_simulants' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "reason": "no sweep_gas mapped from source",
        "tag": "unknown"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1345.15"
      },
      "total_pressure_Pa": {
        "reason": "no total_pressure_Pa mapped from source",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
      "pdf_page_index": 3,
      "table": "1"
    },
    "point_conditions": {
      "temperature_K": {
        "inference": {
          "inputs": [
            "T_K = T_C + 273.15",
            "original_unit=C"
          ],
          "output_unit": "K",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "1072"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "273.15"
                }
              }
            ]
          ],
          "references": [],
          "relation": "celsius_to_kelvin"
        },
        "locator": {
          "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
          "pdf_page_index": 3,
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "1345.15"
        }
      },
      "total_pressure_Pa": {
        "inference": {
          "inputs": [
            "P_Pa = P_Torr × 101325 / 760",
            "original_unit=Torr",
            "as_published=0.23 Torr",
            "printed=P_test_Torr"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "0.23"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "133.3223684210526315789473684"
                }
              }
            ]
          ],
          "references": [],
          "relation": "Torr_to_Pa"
        },
        "locator": {
          "note": "TABLE 1. Results of 12 tests with the prototype system from 2005; printed_cell=[\"0.23 Torr\", \"Torr\"]",
          "pdf_page_index": 3,
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "30.66414473684210526315789473"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'vacuum_pyrolysis_experiment_summary'",
        "phase string 'fresnel_lens_vacuum_pyrolysis_mixed_simulants' is not in the closed automatic map",
        "source T_range_K [821.15, 2140.15]; no midpoint invented"
      ]
    }
  },
  "kems-022-demaria-1971::demaria_1971_al2o_psat_table4::T=2060.0:row=12022:h=24f36e6113b1": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "analysis_selection_rule": "use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed",
          "basis": "sample_catalog_proxy",
          "components": [
            [
              "SiO2",
              "0.4370300404472189490300406296"
            ],
            [
              "Al2O3",
              "0.04992363851472767186779541665"
            ],
            [
              "FeO",
              "0.186631857820954309618158003"
            ],
            [
              "MgO",
              "0.1832826449540607504498940327"
            ],
            [
              "CaO",
              "0.09962773800078939244754301791"
            ],
            [
              "Na2O",
              "0.00230361043344815193559034437"
            ],
            [
              "K2O",
              "0.0004019960173842589283052064581"
            ],
            [
              "TiO2",
              "0.03808571527364136642564305475"
            ],
            [
              "MnO",
              "0.002712758537775149297030294361"
            ]
          ],
          "proxy_flag": "composition_from_sample_catalog",
          "proxy_source": "Smales et al. 1971, Tables 1–3, pp. 1254–1255; source observation smales_1971_apollo_12022_bulk_composition_tables1_2_3. Selection rule: use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed.",
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "Al2O",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "Al2O",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "reason": "source condensed reservoir not grounded",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "Al2O",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "2060"
      },
      "total_pressure_Pa": {
        "reason": "in_cell_total_pressure_not_derivable",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "line_range": "284-286",
      "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
      "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
      "table": "4"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "42.3"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ],
            [
              "original_Al2O3_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "8.2"
                }
              }
            ],
            [
              "M_Al2O3_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "101.960077"
                }
              }
            ],
            [
              "original_FeO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "21.6"
                }
              }
            ],
            [
              "M_FeO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "71.844"
                }
              }
            ],
            [
              "original_MgO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "11.9"
                }
              }
            ],
            [
              "M_MgO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "40.304"
                }
              }
            ],
            [
              "original_CaO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "9"
                }
              }
            ],
            [
              "M_CaO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "56.077000000000005"
                }
              }
            ],
            [
              "original_Na2O_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.23"
                }
              }
            ],
            [
              "M_Na2O_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "61.978538560000004"
                }
              }
            ],
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.061"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_TiO2_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "4.9"
                }
              }
            ],
            [
              "M_TiO2_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "79.865"
                }
              }
            ],
            [
              "original_MnO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.31"
                }
              }
            ],
            [
              "M_MnO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "70.937044"
                }
              }
            ]
          ],
          "references": [],
          "relation": "wt_pct_to_mole_fraction"
        },
        "locator": {
          "line_range": "284-286",
          "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "4"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "analysis_selection_rule": "use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed",
            "basis": "sample_catalog_proxy",
            "components": [
              [
                "SiO2",
                "0.4370300404472189490300406296"
              ],
              [
                "Al2O3",
                "0.04992363851472767186779541665"
              ],
              [
                "FeO",
                "0.186631857820954309618158003"
              ],
              [
                "MgO",
                "0.1832826449540607504498940327"
              ],
              [
                "CaO",
                "0.09962773800078939244754301791"
              ],
              [
                "Na2O",
                "0.00230361043344815193559034437"
              ],
              [
                "K2O",
                "0.0004019960173842589283052064581"
              ],
              [
                "TiO2",
                "0.03808571527364136642564305475"
              ],
              [
                "MnO",
                "0.002712758537775149297030294361"
              ]
            ],
            "proxy_flag": "composition_from_sample_catalog",
            "proxy_source": "Smales et al. 1971, Tables 1–3, pp. 1254–1255; source observation smales_1971_apollo_12022_bulk_composition_tables1_2_3. Selection rule: use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed.",
            "input_provenance": null
          }
        }
      },
      "temperature_K": {
        "locator": {
          "line_range": "284-286",
          "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "4"
        },
        "state": {
          "tag": "value",
          "value": "2060"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "source T_range_K [2060.0, 2500.0]; no midpoint invented",
        "locator source_path 'text/kems-022-demaria-1971/mineru/source/vlm/source.md' has no matching INDEX asset"
      ]
    }
  },
  "kems-022-demaria-1971::demaria_1971_o2_psat_table1_alkali_range::T=1396.0:row=12022:h=3dd0a0a3319e": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "reason": "no composition mapped from source",
        "tag": "unknown"
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "O2",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "O2",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "tag": "not_applicable"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "O2",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1396"
      },
      "total_pressure_Pa": {
        "reason": "in_cell_total_pressure_not_derivable",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "line_range": "106-108",
      "paragraph": "Partial pressures of O2 corresponding approximately to alkali metal vaporization",
      "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
      "table": "1"
    },
    "point_conditions": {
      "temperature_K": {
        "locator": {
          "line_range": "106-108",
          "paragraph": "Partial pressures of O2 corresponding approximately to alkali metal vaporization",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "1396"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "source T_range_K [1396.0, 1499.0]; no midpoint invented",
        "locator source_path 'text/kems-022-demaria-1971/mineru/source/vlm/source.md' has no matching INDEX asset"
      ]
    }
  },
  "kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite::mass_loss_fraction::T=2156.15:h=e9110f31a14b": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "SiO2",
              "0.3821173839269460708474485548"
            ],
            [
              "Al2O3",
              "0.02332561240102551057000802657"
            ],
            [
              "FeO",
              "0.05836653496472179772464472096"
            ],
            [
              "MgO",
              "0.4715509074964332951841453868"
            ],
            [
              "CaO",
              "0.06463956121087332567375331089"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "reason": "no exposure mapped from source",
        "tag": "unknown"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "reason": "no per mapped from source",
        "tag": "unknown"
      },
      "quantity": {
        "tag": "value",
        "value": "mass_loss_fraction"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "reason": "no reservoir mapped from source",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "reason": "no sample_mass_kg mapped from source",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "unknown",
        "phase": {
          "tag": "value",
          "value": "l"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "reason": "no sweep_gas mapped from source",
        "tag": "unknown"
      },
      "temperature_K": {
        "tag": "value",
        "value": "2156.15"
      },
      "total_pressure_Pa": {
        "reason": "no total_pressure_Pa mapped from source",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
      "pdf_page_index": 1,
      "published_page": 510,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "reason": "mass-percent composition is a partial analysis: kept sum=91.43 wt% is below 95 wt%",
          "tag": "unknown"
        }
      },
      "duration_h": {
        "locator": {
          "note": "15 minutes for each printed temperature step.",
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "tag": "value",
          "value": "0.25"
        }
      },
      "fO2_Pa": {
        "locator": {
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "reason": "No point p(O2), fO2, buffer, or gas mix is printed; p(O2) is listed as a result, not an imposed condition.",
          "tag": "unknown"
        }
      },
      "pressure_kind": {
        "locator": {
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "reason": "No vacuum, Knudsen-cell interior pressure, or carrier-gas pressure condition is stated.",
          "tag": "unknown"
        }
      },
      "printed_composition": {
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "Al2O3": "13.09",
            "CaO": "16.34",
            "FeO": "1.79",
            "MgO": "46.61",
            "SiO2": "13.6"
          }
        }
      },
      "starting_composition": {
        "locator": {
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "Al2O3": "4.56",
            "CaO": "6.95",
            "FeO": "8.04",
            "MgO": "36.44",
            "SiO2": "44.02"
          }
        }
      },
      "temperature_K": {
        "inference": {
          "inputs": [
            "T_K = T_C + 273.15",
            "original_unit=C"
          ],
          "output_unit": "K",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
                  "pdf_page_index": 1,
                  "published_page": 510,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "1883"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
                  "pdf_page_index": 1,
                  "published_page": 510,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "273.15"
                }
              }
            ]
          ],
          "references": [],
          "relation": "celsius_to_kelvin"
        },
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "2156.15"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "kems-042-plante-1979::plante1979_table2_k2o_s1115_000_1259K": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_reduced"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "K2O",
              "0.2503139289921102343627073385"
            ],
            [
              "SiO2",
              "0.7496860710078897656372926616"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "tag": "value",
        "value": {
          "terms": [
            {
              "coefficient": "2",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "K",
                "phase": {
                  "tag": "value",
                  "value": "g"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            },
            {
              "coefficient": "1/2",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "O2",
                "phase": {
                  "tag": "value",
                  "value": "g"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            },
            {
              "coefficient": "-1",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "K2O",
                "phase": {
                  "tag": "value",
                  "value": "l"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            }
          ]
        }
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "K",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "K",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "tag": "value",
        "value": {
          "charge": {
            "tag": "value",
            "value": 0
          },
          "formula": "K2O",
          "phase": {
            "tag": "value",
            "value": "l"
          },
          "polymorph": {
            "reason": "not crystal",
            "tag": "not_applicable"
          }
        }
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "K",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1259"
      },
      "total_pressure_Pa": {
        "tag": "value",
        "value": "0.0124244715"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
      "pdf_page_index": 300,
      "published_page": 276,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "34.36"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "65.64"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ]
          ],
          "references": [],
          "relation": "wt_pct_to_mole_fraction"
        },
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "basis": "printed_oxides",
            "components": [
              [
                "K2O",
                "0.2503139289921102343627073385"
              ],
              [
                "SiO2",
                "0.7496860710078897656372926616"
              ]
            ],
            "proxy_flag": null,
            "proxy_source": null,
            "analysis_selection_rule": null,
            "input_provenance": null
          }
        }
      },
      "fO2_Pa": {
        "inference": {
          "inputs": [
            "values.po2_over_pK_as_published",
            "values.P_K_atm_as_published",
            "values.oxygen_partial_pressure",
            "assumption=congruent_vaporization"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "po2_over_pK_as_published",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.226"
                }
              }
            ],
            [
              "P_K_atm_as_published",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000001"
                }
              }
            ],
            [
              "M_O2_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "32"
                }
              }
            ],
            [
              "M_K_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "39.1"
                }
              }
            ],
            [
              "oxygen_partial_pressure",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000000226"
                }
              }
            ],
            [
              "original",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000000226"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "101325"
                }
              }
            ]
          ],
          "references": [],
          "relation": "congruent_vaporization_P_O2_atm=po2_over_pK_as_published*P_K_atm_as_published;P_O2/P_K=(1/4)*sqrt(M_O2/M_K)=0.2262;Plante_printed_ratio=0.226;atm_to_Pa"
        },
        "locator": {
          "equation": "7",
          "note": "DERIVED condition: congruent vaporization P_O2=0.226 P_K; not a measurement",
          "published_page": 280
        },
        "state": {
          "tag": "value",
          "value": {
            "approximate": false,
            "kind": "point",
            "point": "0.002289945"
          }
        }
      },
      "temperature_K": {
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "1259"
        }
      },
      "total_pressure_Pa": {
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "0.0124244715"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "kems-042-plante-1979::plante1979_table2_s1214_r037_quoted": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_reduced"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "K2O",
              "0.04420099031668051797116203944"
            ],
            [
              "SiO2",
              "0.9557990096833194820288379604"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "reason": "source gas standard state not grounded",
        "tag": "unknown"
      },
      "reservoir": {
        "reason": "source condensed reservoir not grounded",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "K",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1335"
      },
      "total_pressure_Pa": {
        "tag": "value",
        "value": "0.012548716215"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
      "published_page": 278,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct",
            "SiO2 = declared K2O-SiO2 binary complement (100 - K2O wt%)"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "6.76"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "93.24"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ]
          ],
          "references": [],
          "relation": "SiO2_wt_pct=100-K2O_wt_pct;wt_pct_to_mole_fraction"
        },
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "basis": "printed_oxides",
            "components": [
              [
                "K2O",
                "0.04420099031668051797116203944"
              ],
              [
                "SiO2",
                "0.9557990096833194820288379604"
              ]
            ],
            "proxy_flag": null,
            "proxy_source": null,
            "analysis_selection_rule": null,
            "input_provenance": null
          }
        }
      },
      "fO2_Pa": {
        "inference": {
          "inputs": [
            "values.po2_over_pK_as_published",
            "values.P_K_atm_as_published",
            "values.oxygen_partial_pressure",
            "assumption=congruent_vaporization"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "po2_over_pK_as_published",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.226"
                }
              }
            ],
            [
              "P_K_atm_as_published",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000101"
                }
              }
            ],
            [
              "M_O2_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "32"
                }
              }
            ],
            [
              "M_K_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "39.1"
                }
              }
            ],
            [
              "oxygen_partial_pressure",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000022826"
                }
              }
            ],
            [
              "original",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000022826"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "101325"
                }
              }
            ]
          ],
          "references": [],
          "relation": "congruent_vaporization_P_O2_atm=po2_over_pK_as_published*P_K_atm_as_published;P_O2/P_K=(1/4)*sqrt(M_O2/M_K)=0.2262;Plante_printed_ratio=0.226;atm_to_Pa"
        },
        "locator": {
          "equation": "7",
          "note": "DERIVED condition: congruent vaporization P_O2=0.226 P_K; not a measurement",
          "published_page": 280
        },
        "state": {
          "tag": "value",
          "value": {
            "approximate": false,
            "kind": "point",
            "point": "0.00231284445"
          }
        }
      },
      "printed_composition": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "K2O": "6.76",
            "SiO2": "93.24"
          }
        }
      },
      "temperature_K": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "1335"
        }
      },
      "total_pressure_Pa": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "0.012548716215"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "robie-hemingway-fisher-1978-usgs-b1452:robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04:S:shared:T=1517:col=entropy:v=d630877923:poly=reference": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "compilation_assessed"
    },
    "identity": {
      "composition": {
        "tag": "not_applicable"
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "tag": "not_applicable"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "value",
        "value": "mol_species"
      },
      "quantity": {
        "tag": "value",
        "value": "S"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "tag": "not_applicable"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "Mn",
        "phase": {
          "tag": "value",
          "value": "cr"
        },
        "polymorph": {
          "tag": "value",
          "value": "reference"
        }
      },
      "standard_pressure_Pa": {
        "tag": "value",
        "value": "100000"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1517"
      },
      "total_pressure_Pa": {
        "tag": "not_applicable"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "as_published='91.86'; unit='J/mol·K' quoted from PDF page 36 (printed p. 30) unit row; captured units_as_published ignored; standard_pressure=1 bar from title (298.15 K and 1 bar (10^5 pascals) pressure)",
      "pdf_page_index": 78,
      "published_page": 72,
      "record": "robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04",
      "source_path": "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04.json",
      "table": "robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04"
    },
    "point_conditions": null,
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  }
}
    ''')
    differences = _field_differences("observations", expected, actual)
    assert not differences, "\n".join(differences)


def _assert_complete_store_regeneration_matches_checked_in_bytes(
    report_mode: str | None = None, source_filter: tuple[str, ...] = ()
) -> None:
    result = migrate(REPO_ROOT, write=False)
    stored = _store_output_file_bytes(REPO_ROOT)
    regenerated: dict[str, bytes] = {}

    def capture_writer_output(result, root: Path) -> None:
        write_outputs(result, root)
        regenerated.update(_store_output_file_bytes(root))

    with patch(
        "simulator.battery.migrate.write_outputs",
        side_effect=capture_writer_output,
    ):
        helper_differences = _store_regeneration_differences(REPO_ROOT, result)

    byte_differences = sorted(
        path
        for path in stored.keys() | regenerated.keys()
        if stored.get(path) != regenerated.get(path)
    )
    assert helper_differences == byte_differences

    report = regenerated["data/battery/migration-report.md"].decode("utf-8")
    headline = {
        "rows in": sum(count.rows_in for count in result.source_counts.values()),
        "records out (observations)": len(result.observations),
        "works": len(result.works),
        "experiments": len(result.experiments),
        "queue size": len(result.queue),
        "hard issues": len(result.validation.hard_issues),
    }
    for label, count in headline.items():
        assert f"{label}: {count}" in report

    if report_mode:
        payload = _store_difference_payload(stored, regenerated, byte_differences)
        report = _format_store_delta_report(payload, report_mode, set(source_filter))
        if report_mode == "scope":
            compressed = lzma.compress(report.encode("utf-8"), preset=9)
            print("STORE DELTA SCOPE LZMA BASE64 " + base64.b64encode(compressed).decode("ascii"))
        else:
            print(report)
        assert len(byte_differences) == 376
        raise AssertionError("intentional store delta report follows")
    assert byte_differences == [], _store_difference_details(
        stored, regenerated, byte_differences
    )
