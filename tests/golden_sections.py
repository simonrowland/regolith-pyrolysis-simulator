"""Shadow runner storage and exact declared moves; no simulator rules or authority.

Legacy runner fixtures remain authoritative until the separate consumer cutover.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import quote

PRODUCER_CONTRACT = "section-goldens-v1"
MISSING = {"kind": "missing"}


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _pointer(key):
    return str(key).replace("~", "~0").replace("/", "~1")


def _filename(key):
    # Percent itself is escaped, unlike RFC 6901. Prefixing avoids state/envelope
    # group collisions and dot/parent path components for arbitrary source keys.
    return "field-" + quote(str(key), safe="")


def partition(payload: dict) -> dict[str, bytes]:
    """Return ordered fragment files and the shape needed for byte reassembly."""
    sections, shape = {}, {}

    def branch(pointer, value):
        shape[pointer] = ({"kind": "object", "keys": list(value)} if isinstance(value, dict)
                          else {"kind": "array", "length": len(value)})

    def cut(pointer, value, section):
        sections.setdefault(section, {})[pointer] = value

    branch("", payload)
    diagnostics = {"run_metadata", "vapour_rail_instrumentation",
                   "thermal_train_report", "degraded_path_engagement"}
    for key, value in payload.items():
        p = "/" + _pointer(key)
        if (key == "per_hour_summary" and isinstance(value, list)
                and all(isinstance(row, dict) for row in value)):
            branch(p, value)
            for i, row in enumerate(value):
                q = p + "/" + str(i)
                branch(q, row)
                for k, v in row.items():
                    name = _filename(k) if isinstance(v, (dict, list)) else "state"
                    cut(q + "/" + _pointer(k), v, f"hours/{i:04d}/{name}.json")
        elif key in {"final_state", "stage_purity_report"} and isinstance(value, dict):
            branch(p, value)
            directory = "accounts" if key == "final_state" else "stages"
            for k, v in value.items():
                cut(p + "/" + _pointer(k), v, f"{directory}/{_filename(k)}.json")
        elif key in diagnostics and isinstance(value, dict):
            branch(p, value)
            for k, v in value.items():
                name = _filename(k) if isinstance(v, (dict, list)) else "state"
                cut(p + "/" + _pointer(k), v, f"diagnostics/{_filename(key)}/{name}.json")
        else:
            name = _filename(key) if isinstance(value, (dict, list)) else "envelope"
            cut(p, value, name + ".json")
    files = {name: json_bytes(sections[name]) for name in sorted(sections)}
    files["index.json"] = json_bytes({"sections": list(files), "shape": shape})
    return files


def reassemble(files: dict[str, bytes]) -> bytes:
    index = load_json(files["index.json"])
    names = index["sections"]
    if len(names) != len(set(names)) or set(files) != set(names) | {"index.json"}:
        raise ValueError("missing, extra or duplicate section files")
    values = {}
    for name in names:
        for pointer, value in load_json(files[name]).items():
            if pointer in values:
                raise ValueError(f"duplicate ownership: {pointer}")
            values[pointer] = value
    shape, used_values, used_shape = index["shape"], set(), set()

    def build(pointer):
        if pointer in values:
            used_values.add(pointer)
            return values[pointer]
        record = shape[pointer]
        used_shape.add(pointer)
        if record["kind"] == "object":
            return {k: build(pointer + "/" + _pointer(k)) for k in record["keys"]}
        if record["kind"] == "array":
            return [build(pointer + "/" + str(i)) for i in range(record["length"])]
        raise ValueError(f"invalid shape: {pointer}")

    try:
        result = json_bytes(build(""))
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("incomplete section coverage") from exc
    if used_values != set(values) or used_shape != set(shape):
        raise ValueError("overlapping or unused section ownership")
    # A shadow tree must be exactly the owning partition, including its index.
    if partition(load_json(result)) != files:
        raise ValueError("noncanonical section layout")
    return result


def read_sections(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def write_sections(directory: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def flux_sections(flux_maps: list) -> dict[str, bytes]:
    """Canonicalize captured evidence AFTER dispatch, never arithmetic inputs."""
    canonical = json.loads(json.dumps(flux_maps, sort_keys=True, allow_nan=False))
    names = [f"flux/{i:04d}.json" for i in range(len(canonical))]
    return {**{name: json_bytes(value) for name, value in zip(names, canonical)},
            "flux/index.json": json_bytes(names)}


def reassemble_flux(files: dict[str, bytes]) -> list:
    names = load_json(files["flux/index.json"])
    if len(names) != len(set(names)) or set(files) != set(names) | {"flux/index.json"}:
        raise ValueError("invalid flux dispatch index")
    return [load_json(files[name]) for name in names]


def _token(value):
    if value is None:
        return {"kind": "null"}
    if type(value) is bool:
        return {"kind": "boolean", "value": value}
    if type(value) is int:
        return {"kind": "integer", "value": value}
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite golden node")
        return {"kind": "f64", "hex": value.hex()}
    if type(value) is str:
        return {"kind": "string", "value": value}
    if type(value) is dict:
        return {"kind": "object", "keys": list(value)}
    if type(value) is list:
        return {"kind": "array", "length": len(value)}
    raise ValueError(f"not a JSON node: {type(value)}")


def nodes(payload) -> dict[str, dict]:
    result = {}

    def visit(pointer, value):
        result[pointer] = _token(value)
        children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
        for key, child in children:
            visit(pointer + "/" + _pointer(key), child)
    visit("", payload)
    return result


def _owners(payload):
    files = partition(payload)
    result = {p: "index.json" for p in load_json(files["index.json"])["shape"]}
    for name, raw in files.items():
        if name != "index.json":
            for p, subtree in load_json(raw).items():
                for suffix in nodes(subtree):
                    result[p + suffix] = name
    return result


def diff_nodes(artifact: str, old: dict, new: dict) -> list[dict]:
    before, after = nodes(old), nodes(new)
    old_owners, new_owners = _owners(old), _owners(new)
    return [{"artifact": artifact, "section": new_owners.get(p, old_owners.get(p)),
             "path": p, "old": before.get(p, MISSING), "new": after.get(p, MISSING)}
            for p in sorted(before.keys() | after.keys())
            if before.get(p, MISSING) != after.get(p, MISSING)]


def _validate_token(token):
    kind = token.get("kind") if type(token) is dict else None
    fields = {"missing": (), "null": (), "boolean": ("value",), "integer": ("value",),
              "string": ("value",), "f64": ("hex",), "object": ("keys",), "array": ("length",)}
    if kind not in fields or set(token) != {"kind", *fields[kind]}:
        raise ValueError("malformed typed node")
    if kind in {"boolean", "integer", "string"}:
        if type(token["value"]) is not {"boolean": bool, "integer": int, "string": str}[kind]:
            raise ValueError("malformed scalar")
    elif kind == "f64":
        try:
            value = float.fromhex(token["hex"])
            if not math.isfinite(value) or value.hex() != token["hex"]:
                raise ValueError("noncanonical f64")
        except (TypeError, OverflowError) as exc:
            raise ValueError("malformed f64") from exc
    elif kind == "array":
        if type(token["length"]) is not int or token["length"] < 0:
            raise ValueError("malformed array")
    elif kind == "object":
        keys = token["keys"]
        if type(keys) is not list or any(type(k) is not str for k in keys) or len(keys) != len(set(keys)):
            raise ValueError("malformed object")


def check_manifest(old: dict[str, dict], new: dict[str, dict], manifest_raw: bytes,
                   expected_raw: bytes, *, base_green: str) -> None:
    """Exact one-offer D == M shadow check. Approval binding is the train owner.

    This validates data/preconditions, never grants authority from a self-written
    owner_decision. Batch composition and approval lookup belong to step 3.
    """
    manifest = load_json(manifest_raw)
    if (manifest["format"] != "golden-moves-v1" or manifest["base_green"] != base_green
            or manifest["producer_contract"] != PRODUCER_CONTRACT
            or manifest["expected_diff"] != "expected.jsonl" or not manifest["offer"]):
        raise ValueError("manifest identity mismatch")
    if set(old) != set(new):
        raise ValueError("artifact inventory mismatch")
    classes = manifest["classes"]
    counts = dict.fromkeys(classes, 0)
    for cls in classes.values():
        if (not cls["cause"] or not cls["owner_decision"] or not cls["summary"]
                or type(cls["count"]) is not int or cls["count"] < 0):
            raise ValueError("invalid class")
    rows = [load_json(line) for line in expected_raw.splitlines()]
    old_nodes = {artifact: nodes(payload) for artifact, payload in old.items()}
    seen, declared = set(), []
    for row in rows:
        if set(row) != {"artifact", "section", "path", "old", "new", "cause"}:
            raise ValueError("invalid expected row")
        key = row["artifact"], row["path"]
        if key in seen or row["artifact"] not in old or row["cause"] not in classes:
            raise ValueError("duplicate, unknown artifact or unknown cause")
        seen.add(key)
        counts[row["cause"]] += 1
        _validate_token(row["old"])
        _validate_token(row["new"])
        if row["old"] == row["new"]:
            raise ValueError("no-op declaration")
        if old_nodes[row["artifact"]].get(row["path"], MISSING) != row["old"]:
            raise ValueError("wrong old value")
        declared.append({k: v for k, v in row.items() if k != "cause"})
    if [(r["artifact"], r["path"]) for r in rows] != sorted(seen):
        raise ValueError("expected rows must be sorted")
    if any(counts[k] != cls["count"] for k, cls in classes.items()):
        raise ValueError("class count mismatch")
    observed = [row for artifact in sorted(old)
                for row in diff_nodes(artifact, old[artifact], new[artifact])]
    if declared != observed:
        raise ValueError("D != M: undeclared, missing or different move")
