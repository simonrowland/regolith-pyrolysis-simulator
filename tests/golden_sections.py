"""Shadow runner storage and exact declared moves; no simulator rules or authority.

Legacy runner fixtures remain authoritative until the separate consumer cutover.
"""
from __future__ import annotations

import json
import math
import gzip
import hashlib
import subprocess
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


def manifest_rows(manifest_raw: bytes, expected_raw: bytes, *, base_green: str) -> tuple[dict, list]:
    """Validate the shared single-offer/batch declaration format."""
    manifest = load_json(manifest_raw)
    if (manifest["format"] != "golden-moves-v1" or manifest["base_green"] != base_green
            or manifest["producer_contract"] != PRODUCER_CONTRACT
            or Path(manifest["expected_diff"]).name != manifest["expected_diff"]
            or not manifest["expected_diff"].endswith((".jsonl", ".jsonl.gz"))
            or not manifest["offer"]):
        raise ValueError("manifest identity mismatch")
    classes = manifest["classes"]
    counts = dict.fromkeys(classes, 0)
    for cls in classes.values():
        if (not cls["cause"] or not cls["owner_decision"] or not cls["summary"]
                or type(cls["count"]) is not int or cls["count"] < 0):
            raise ValueError("invalid class")
    rows = [load_json(line) for line in expected_raw.splitlines()]
    seen = set()
    for row in rows:
        if set(row) != {"artifact", "section", "path", "old", "new", "cause"}:
            raise ValueError("invalid expected row")
        key = row["artifact"], row["path"]
        if key in seen or row["cause"] not in classes:
            raise ValueError("duplicate, unknown artifact or unknown cause")
        if (type(row["artifact"]) is not str or type(row["path"]) is not str
                or type(row["section"]) is not str):
            raise ValueError("invalid row address")
        seen.add(key)
        counts[row["cause"]] += 1
        _validate_token(row["old"])
        _validate_token(row["new"])
        if row["old"] == row["new"]:
            raise ValueError("no-op declaration")
    if [(r["artifact"], r["path"]) for r in rows] != sorted(seen):
        raise ValueError("expected rows must be sorted")
    if any(counts[k] != cls["count"] for k, cls in classes.items()):
        raise ValueError("class count mismatch")
    return manifest, rows


def check_manifest(old: dict[str, dict], new: dict[str, dict], manifest_raw: bytes,
                   expected_raw: bytes, *, base_green: str) -> None:
    """Exact one-offer D == M; approval binding remains the train owner's job."""
    _, rows = manifest_rows(manifest_raw, expected_raw, base_green=base_green)
    if set(old) != set(new):
        raise ValueError("artifact inventory mismatch")
    old_nodes = {artifact: nodes(payload) for artifact, payload in old.items()}
    for row in rows:
        if row["artifact"] not in old:
            raise ValueError("duplicate, unknown artifact or unknown cause")
        if old_nodes[row["artifact"]].get(row["path"], MISSING) != row["old"]:
            raise ValueError("wrong old value")
    declared = [{k: v for k, v in row.items() if k != "cause"} for row in rows]
    observed = [row for artifact in sorted(old)
                for row in diff_nodes(artifact, old[artifact], new[artifact])]
    if declared != observed:
        raise ValueError("D != M: undeclared, missing or different move")


ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = "tests/fixtures/runner"
COVERAGE_EXCEPTION = "COVERAGE-EXCEPTION: sio_yield (tolerant); non-runner families are outside this runner check"


def runner_payloads(source: str) -> dict[str, dict]:
    """Read legacy fixture bytes from a revision or a producer output directory.

    Historical census directories use runner-<fixture>.json.gz. No physics runs
    here: required-engine admission and approval binding remain train gates.
    """
    directory = Path(source)
    if directory.is_dir() and (directory / RUNNER_DIR).is_dir():
        directory /= RUNNER_DIR
    names = {p.name for p in (ROOT / RUNNER_DIR).glob("*.json")}
    raw = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not path.name.endswith((".json", ".json.gz")):
                continue
            name = path.name.removesuffix(".gz").removeprefix("runner-")
            if path.name.startswith("sio-"):
                continue
            if name not in names or name in raw:
                raise ValueError(f"unknown or duplicate runner fixture: {path}")
            content = path.read_bytes()
            raw[name] = gzip.decompress(content) if path.suffix == ".gz" else content
    else:
        inventory = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", f"{source}:{RUNNER_DIR}"],
            cwd=ROOT, stderr=subprocess.PIPE).decode().splitlines()
        if set(inventory) != names:
            raise ValueError(f"runner artifact inventory mismatch: {inventory}")
        for name in sorted(names):
            raw[name] = subprocess.check_output(
                ["git", "show", f"{source}:{RUNNER_DIR}/{name}"], cwd=ROOT, stderr=subprocess.PIPE)
    if set(raw) != names:
        raise ValueError(f"runner artifact inventory mismatch: missing {sorted(names - raw.keys())}")
    result = {"runner/" + Path(name).stem: load_json(content) for name, content in raw.items()}
    for artifact, payload in result.items():
        if type(payload) is not dict or payload.get("status") != "ok":
            raise ValueError(f"runner output refused/unavailable: {artifact} status={payload.get('status') if isinstance(payload, dict) else None!r}")
        nodes(payload)  # Reject overflow/nonfinite producer numbers before admission.
    return result


def snapshot_id(payloads: dict[str, dict]) -> str:
    digest = hashlib.sha256()
    for artifact, payload in sorted(payloads.items()):
        digest.update(json_bytes([artifact, payload]))
    return "sha256:" + digest.hexdigest()


def source_id(source: str, payloads: dict[str, dict]) -> str:
    if Path(source).is_dir():
        return snapshot_id(payloads)
    return subprocess.check_output(["git", "rev-parse", "--verify", source + "^{commit}"],
                                   cwd=ROOT, stderr=subprocess.PIPE).decode().strip()


def load_manifest(path: Path) -> tuple[dict, list, dict]:
    """Accept a JSON summary or its JSONL[.gz] file; hash both original files."""
    summary = (path.with_name(path.name.removesuffix(".gz").removesuffix(".jsonl") + ".json")
               if path.name.endswith((".jsonl", ".jsonl.gz")) else path)
    manifest_raw = summary.read_bytes()
    manifest = load_json(manifest_raw)
    name = manifest["expected_diff"]
    if Path(name).name != name:
        raise ValueError("expected_diff must be a sibling file")
    rows_path = summary.parent / name
    if path != summary and path != rows_path:
        raise ValueError("summary does not reference selected JSONL")
    expected_raw = rows_path.read_bytes()
    identities = {str(p): hashlib.sha256(raw).hexdigest()
                  for p, raw in ((summary, manifest_raw), (rows_path, expected_raw))}
    _, rows = manifest_rows(manifest_raw, gzip.decompress(expected_raw)
                            if rows_path.suffix == ".gz" else expected_raw,
                            base_green=manifest["base_green"])
    return manifest, rows, identities


def _from_nodes(tokens):
    """Rebuild an atomic offer and reject orphan/missing/ill-shaped descendants."""
    used = set()

    def build(pointer):
        token = tokens[pointer]
        used.add(pointer)
        kind = token["kind"]
        if kind == "object":
            return {k: build(pointer + "/" + _pointer(k)) for k in token["keys"]}
        if kind == "array":
            return [build(pointer + "/" + str(i)) for i in range(token["length"])]
        if kind == "null":
            return None
        if kind == "f64":
            return float.fromhex(token["hex"])
        return token["value"]

    try:
        result = build("")
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("incomplete declared structure") from exc
    if used != set(tokens) or type(result) is not dict:
        raise ValueError("orphan or invalid declared structure")
    return result


def _apply_rows(incoming, rows):
    candidate = {a: dict(t) for a, t in incoming.items()}
    for row in rows:
        if row["new"] == MISSING:
            candidate[row["artifact"]].pop(row["path"], None)
        else:
            candidate[row["artifact"]][row["path"]] = row["new"]
    return {a: _from_nodes(t) for a, t in candidate.items()}


def compare_manifests(old: dict, new: dict, offers: list[tuple[dict, list]]) -> dict:
    """Compose measured offers atomically; compare complete typed D and E.

    A base snapshot binds all preconditions, not just the overlapping token.
    Independent offers may share green. A continuation must name a measured
    predecessor snapshot; unrelated disjoint offers need not be in it. Causal truth
    and digest-bound approvals still require worker review on the train.
    """
    if set(old) != set(new):
        raise ValueError("artifact inventory mismatch")
    current, initial_id = old, snapshot_id(old)
    measured_states = {initial_id: old}
    chains, classes = {}, []
    report = {k: [] for k in ("UNDECLARED", "MISSING", "WRONG-OLD", "OVERLAP", "CANCELLED")}
    for manifest, rows in offers:
        classes.append({"offer": manifest["offer"], "classes": manifest["classes"]})
        incoming_id = snapshot_id(current)
        measured = manifest["base_green"]
        if not measured.startswith("sha256:"):
            measured = snapshot_id(runner_payloads(measured))
        if not rows and measured not in measured_states:
            raise ValueError("manifest base mismatch")
        measured_nodes = {a: nodes(p) for a, p in measured_states.get(measured, {}).items()}
        incoming = {a: nodes(p) for a, p in current.items()}
        prior_errors = len(report["WRONG-OLD"]) + len(report["OVERLAP"])
        for row in rows:
            artifact, pointer = row["artifact"], row["path"]
            if artifact not in incoming:
                raise ValueError(f"unknown artifact: {artifact}")
            key = artifact, pointer
            actual_old = incoming[artifact].get(pointer, MISSING)
            overlap = key in chains
            wrong_base = (artifact not in measured_nodes
                          or measured_nodes[artifact].get(pointer, MISSING) != row["old"]
                          # Returning to green cannot license an independent
                          # green-based overlap after a cancelled chain.
                          or (overlap and measured == initial_id))
            if actual_old != row["old"] or wrong_base:
                violation = dict(row, offer=manifest["offer"], actual_old=actual_old,
                                 measured_base=measured, incoming_base=incoming_id)
                report["OVERLAP" if overlap else "WRONG-OLD"].append(violation)
        if len(report["WRONG-OLD"]) + len(report["OVERLAP"]) != prior_errors:
            continue
        candidate = _apply_rows(incoming, rows)
        # Reuse the single-offer owner for complete structure/section validation.
        check_manifest(current, candidate, json_bytes(manifest),
                       b"".join(json.dumps(r).encode() + b"\n" for r in rows),
                       base_green=manifest["base_green"])
        measured_candidate = candidate if measured == incoming_id else _apply_rows(measured_nodes, rows)
        if measured != incoming_id:
            check_manifest(measured_states[measured], measured_candidate, json_bytes(manifest),
                           b"".join(json.dumps(r).encode() + b"\n" for r in rows),
                           base_green=manifest["base_green"])
        measured_states[snapshot_id(measured_candidate)] = measured_candidate
        measured_states[snapshot_id(candidate)] = candidate
        for row in rows:
            chains.setdefault((row["artifact"], row["path"]), []).append(
                dict(row, offer=manifest["offer"]))
        current = candidate
    observed = [r for a in sorted(old) for r in diff_nodes(a, old[a], new[a])]
    expected = [r for a in sorted(old) for r in diff_nodes(a, old[a], current[a])]
    d = {(r["artifact"], r["path"]): r for r in observed}
    e = {(r["artifact"], r["path"]): r for r in expected}
    report["UNDECLARED"] = [r for key, r in d.items() if e.get(key) != r]
    report["MISSING"] = [r for key, r in e.items() if d.get(key) != r]
    report["CANCELLED"] = [chain for key, chain in chains.items() if key not in e]
    counts = {}
    for key in d:
        chain = chains.get(key, [])
        label = (chain[-1]["offer"] + ":" + chain[-1]["cause"]) if chain else "UNDECLARED"
        counts[label] = counts.get(label, 0) + 1
    report.update(observed_count=len(d), expected_count=len(e), classes=classes,
                  observed_classes=counts, chains=[c for c in chains.values() if len(c) > 1])
    report["ok"] = not any(report[k] for k in ("UNDECLARED", "MISSING", "WRONG-OLD", "OVERLAP"))
    return report


def make_manifest(old: dict, new: dict, rules: dict, *, base_green: str, expected_diff: str):
    """Classify the owner's measured rows, using ordered first-match rules.

    Class IDs and causal prose come from the existing offer vocabulary. Rules
    select artifact, path_prefix and/or old_kind/new_kind; they never set values.
    """
    classes = {k: dict(v, count=0) for k, v in rules["classes"].items()}
    for rule in rules["rules"]:
        if set(rule) - {"cause", "artifact", "path_prefix", "old_kind", "new_kind"}:
            raise ValueError("unknown class-map selector")
        if rule["cause"] not in classes:
            raise ValueError("unknown class-map cause")
    rows = []
    if set(old) != set(new):
        raise ValueError("artifact inventory mismatch")
    for artifact in sorted(old):
        for row in diff_nodes(artifact, old[artifact], new[artifact]):
            for rule in rules["rules"]:
                if (rule.get("artifact", artifact) == artifact
                        and row["path"].startswith(rule.get("path_prefix", ""))
                        and row["old"]["kind"] in rule.get("old_kind", [row["old"]["kind"]])
                        and row["new"]["kind"] in rule.get("new_kind", [row["new"]["kind"]])):
                    row["cause"] = rule["cause"]
                    classes[row["cause"]]["count"] += 1
                    rows.append(row)
                    break
            else:
                raise ValueError(f"unclassified measured row: {artifact} {row['path']}")
    manifest = {"format": "golden-moves-v1", "offer": rules["offer"],
                "base_green": base_green, "producer_contract": PRODUCER_CONTRACT,
                "expected_diff": expected_diff, "classes": classes}
    raw = b"".join(json.dumps(r, allow_nan=False).encode() + b"\n" for r in rows)
    check_manifest(old, new, json_bytes(manifest), raw, base_green=base_green)
    return manifest, raw
