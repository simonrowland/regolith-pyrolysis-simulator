"""Compare historical store locations with the source's structured coordinates."""

from dataclasses import fields
import json
from pathlib import Path

import yaml

from simulator.battery.migrate import _locator_from_plain, to_plain
from simulator.battery.records import Locator


def _type_sensitive_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _type_sensitive_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_type_sensitive_value(child) for child in value]
    return (type(value).__name__, value)


def _type_tag_observation_locators(payload: object) -> object:
    if isinstance(payload, dict):
        observation_row = "observation_id" in payload
        return {
            key: (
                _type_sensitive_value(value)
                if observation_row and key in {"locator", "source_locator"}
                else _type_tag_observation_locators(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_type_tag_observation_locators(value) for value in payload]
    return payload


def historical_source_locations(payload: object, extract: dict) -> object:
    # The committed siblings predate these five fields. Match their entire
    # historical locator to its source, including the old flattened note;
    # never discard locations or infer a row from an ungrounded note fragment.
    legacy_fields = {item.name for item in fields(Locator)} - {
        "row", "column", "table_row_number", "table_column_number", "extras",
    }
    source_rows: dict[str, dict] = {}
    pending = [extract]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if "observation_id" in node:
                source_rows[node["observation_id"]] = node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)

    def locations(source: dict) -> dict[str, object]:
        replacements: dict[str, object] = {}
        pending = [source]
        while pending:
            node = pending.pop()
            if not isinstance(node, (dict, list)):
                continue
            if isinstance(node, list):
                pending.extend(node)
                continue
            for key, raw in node.items():
                if key in {"locator", "source_locator"} and isinstance(raw, dict):
                    legacy = {
                        name: value for name, value in raw.items()
                        if name in legacy_fields and value is not None and value != ""
                    }
                    promoted = [
                        f"{name}={value}" for name, value in raw.items()
                        if name not in legacy_fields and value is not None and value != ""
                    ]
                    canonical = to_plain(_locator_from_plain(raw, refuse_invalid=True))
                    # The old persisted/Located reader dropped unknown axes;
                    # the old extract reader flattened them into the note.
                    signature = json.dumps(to_plain(legacy), sort_keys=True)
                    if signature not in replacements:
                        replacements[signature] = canonical
                    elif replacements[signature] != canonical:
                        replacements[signature] = None
                    if promoted:
                        legacy["note"] = "; ".join(
                            [str(legacy.get("note") or ""), *promoted]
                        ).strip("; ")
                        signature = json.dumps(to_plain(legacy), sort_keys=True)
                        assert replacements.get(signature, canonical) == canonical
                        replacements[signature] = canonical
                pending.append(raw)
        return replacements

    def normalize(value: object, source: object, replacements: dict[str, object]) -> object:
        if isinstance(value, dict):
            if "observation_id" in value:
                parent_id = value["observation_id"].split("::")[1]
                source = source_rows[parent_id]
            if isinstance(source, dict):
                replacements = {**replacements, **locations(source)}
            return {
                key: (
                    replacements.get(json.dumps(item, sort_keys=True))
                    or to_plain(_locator_from_plain(item, refuse_invalid=True))
                ) if key in {"locator", "source_locator"} else normalize(
                    item, source.get(key) if isinstance(source, dict) else None, replacements,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [normalize(item, None, replacements) for item in value]
        return value

    return _type_tag_observation_locators(normalize(payload, None, {}))


def regenerated_source_locations(payload: object) -> object:
    """Keep each emitted observation locator exact; normalize embedded locators."""
    if isinstance(payload, dict):
        observation_row = "observation_id" in payload
        normalized: dict[str, object] = {}
        for key, value in payload.items():
            if key in {"locator", "source_locator"}:
                normalized[key] = (
                    value
                    if observation_row
                    else to_plain(_locator_from_plain(value, refuse_invalid=True))
                )
            else:
                normalized[key] = regenerated_source_locations(value)
        return (
            _type_tag_observation_locators(normalized)
            if observation_row
            else normalized
        )
    if isinstance(payload, list):
        return [regenerated_source_locations(value) for value in payload]
    return payload


def mutate_first_serialized_locator(path: Path, *, mutation: str) -> None:
    """Mutate one emitted locator to exercise exact fixed-point comparisons."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    for observation in document.get("observations", []):
        locator = observation.get("locator") or {}
        if mutation == "page_alias" and "page" in locator:
            locator["pdf_page"] = locator.pop("page")
            break
        if mutation == "drop_coordinate":
            coordinate = next(
                (
                    key for key in (
                        "row", "column", "table_row_number",
                        "table_column_number", "table",
                    )
                    if key in locator
                ),
                None,
            )
            if coordinate is not None:
                del locator[coordinate]
                break
        if mutation == "integer_to_float":
            coordinate = next(
                (
                    key for key in ("table_row_number", "table_column_number")
                    if type(locator.get(key)) is int
                ),
                None,
            )
            if coordinate is not None:
                locator[coordinate] = float(locator[coordinate])
                break
    else:
        raise LookupError(f"no locator field available for {mutation} mutation")
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
