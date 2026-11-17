"""Compare historical store locations with the source's structured coordinates."""

from dataclasses import fields
import json

from simulator.battery.migrate import _locator_from_plain, to_plain
from simulator.battery.records import Locator


def canonical_source_locations(payload: object, extract: dict) -> object:
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

    return normalize(payload, None, {})
