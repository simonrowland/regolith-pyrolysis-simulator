#!/usr/bin/env python3
"""Build INDEX.yaml + INDEX.md + SOURCE_STATUS.yaml by walking --root.

Canonical source_id = extract stem. SOURCE_STATUS.yaml stage tags are derived
from artefacts; never hand-set.
"""
from __future__ import annotations

import argparse, fnmatch, hashlib, json, os, re, subprocess, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import yaml

SCHEMA = "literature_index.v1"
STATUS_SCHEMA = "literature_source_status.v1"
SIDECAR_FIELDS = ("citation", "doi", "license_or_oa_basis", "sha256", "retrieved_date", "retrieval_url")
ID_KEYS = ("source_id", "paper_id", "paper_citation_id", "citation_id")
OA_HOSTS = ("jstage.jst.go.jp", "ntrs.nasa.gov", "nist.gov", "aanda.org", "arxiv.org", "janaf.nist.gov", "webbook.nist.gov")
ROW_KEYS = (
    "source_id", "aliases", "citation", "doi", "report_number", "identifier", "pdf_status", "pdf_path",
    "pdf_last_seen", "pdf_sha256", "pdf_tracked", "sidecar_path", "sidecar_missing_fields",
    "access_status", "extracts", "battery_datasets", "hunt_ids", "copies", "measurement_sets",
    "corpus_status", "corpus",
)
STATUS_ROW_KEYS = (
    "source_id", "stage", "evidence", "usable_rows", "total_rows", "inbox_exclusion_reason",
)
STATUS_STAGES = (
    "located", "inbox", "in_progress", "ingested_partial", "ingested_complete", "wired", "unknown",
)
# One weekly hunt cycle. A document that has sat this long with no extract and
# no claim is not in a live worker's hands; it is lost in the inbox. Distinct
# from the corpus claim-takeover window (12 h in tools/claim.py), which is a
# live-lock timeout rather than an acquisition-backlog threshold.
STALE_INBOX_DAYS = 7
COMPILATION_INBOX_EXCLUSION = (
    "compilation-path: source reaches the store through data/literature/compilations/ "
    "rather than a per-source extract; not counted as inbox"
)
# Closed v2.1 measured origin classes (simulator.battery.enums.MEASURED_EVIDENCE).
# Copied as strings so this builder stays a standalone script.
MEASURED_EVIDENCE_CLASSES = frozenset({"measured_direct", "measured_tabulated", "measured_reduced"})
NUMERIC_VALUE_KINDS = frozenset({"point", "series", "bound", "interval", "relative_series"})
CLAIM_STAGE_RANK = {
    "locate": 0, "located": 0, "acquire": 1, "acquired": 1, "decode": 2, "decoded": 2,
    "transcribe": 3, "transcribed": 3, "extract": 4, "extracted": 4, "adopt": 5, "adopted": 5,
    "score": 6, "scored": 6, "wired": 6,
}
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")


def iter_observation_store_paths(directory: Path, pattern: str = "*.yaml") -> list[Path]:
    """Observation YAML files, including compilation shard directories.

    Keep in lockstep with simulator.battery.migrate.iter_observation_store_paths.
    A family may live as compilations-janaf.yaml or compilations-janaf/janaf-Al.yaml.
    """

    if not directory.is_dir():
        return []
    paths = [path for path in sorted(directory.glob(pattern)) if path.is_file()]
    dir_pattern = pattern[: -len(".yaml")] if pattern.endswith(".yaml") else pattern
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith((".", "_")) or child.name.endswith("-reports"):
            continue
        if not fnmatch.fnmatch(child.name, dir_pattern):
            continue
        paths.extend(path for path in sorted(child.glob("*.yaml")) if path.is_file())
    return paths


def compilation_family_from_store_path(path: Path) -> str | None:
    if path.name.startswith("compilations-") and path.suffix in {".yaml", ".yml"}:
        return path.stem.removeprefix("compilations-")
    parent = path.parent.name
    if parent.startswith("compilations-") and not parent.endswith("-reports"):
        return parent.removeprefix("compilations-")
    return None
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;]+")
PDF_PATH_RE = re.compile(r"docs/references/pdfs/[^\s\"'<>]+/([A-Za-z0-9_.-]+)\.pdf")
HUNT_RES = (
    re.compile(r"data/literature/extracts/([A-Za-z0-9_.-]+)\.yaml"),
    re.compile(r"docs/references/pdfs/[^\"'\s]+/([A-Za-z0-9_.-]+)\.pdf"),
    re.compile(r"(kems-[0-9]{3}-[A-Za-z0-9-]+)"),
)
REPORT_PATTERNS = (
    r"NASA/[A-Z]+[—\-]\d{4}-\d+", r"NTRS\s+\d+", r"NBSIR\s+\d+-\d+", r"NBS Special Publication \d+",
    r"NIST Standard Reference Database \d+", r"ADA\d+", r"Monograph \d+",
)

def posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def rel_to(path: Path, root: Path) -> str:
    try:
        return posix(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return posix(path)

def resolve_against(path: Path, root: Path) -> Path:
    if path.is_absolute():
        return path
    candidate = (root / path).resolve()
    return candidate if candidate.exists() else path.resolve()

def hunt_sort_key(hid: str):
    match = re.match(r"RH-(\d+)$", hid)
    return (int(match.group(1)) if match else 10**9, hid)

def git_tracked_set(root: Path) -> tuple[str, set[str] | None]:
    try:
        inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, check=False)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return "unknown", None
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    except OSError:
        return "unknown", None
    if listed.returncode != 0:
        return "unknown", None
    return "git", {item.replace("\\", "/") for item in listed.stdout.decode().split("\0") if item}

def doi_of(source: dict) -> str | None:
    doi = source.get("doi")
    if doi not in (None, "", "null", "none"):
        return str(doi).strip().rstrip(".")
    match = DOI_RE.search(str(source.get("citation") or ""))
    return match.group(0).rstrip(").,") if match else None

def report_number(source: dict) -> str | None:
    blob = " ".join(str(source.get(key) or "") for key in ("citation", "identifier", "url", "number"))
    ntrs = re.search(r"ntrs\.nasa\.gov/citations/(\d+)", blob, re.I)
    if ntrs:
        return f"NTRS {ntrs.group(1)}"
    for pattern in REPORT_PATTERNS:
        match = re.search(pattern, blob, re.I)
        if match:
            return match.group(0)
    return None

def author_year(sid: str, citation: str = "") -> tuple[str, str] | None:
    text = re.sub(r"^kems-(?:ms)?\d+-", "", sid.replace("_", "-"))
    text = re.sub(r"^kems-", "", text)
    match = YEAR_RE.search(text)
    if match:
        before = text[: match.start()].strip("-")
        author = (before.split("-")[0] if before else "").lower()
        if len(author) >= 2 and author[0].isalpha():
            return author, match.group(1)
    if not citation:
        return None
    year, name = YEAR_RE.search(citation), re.search(r"([^\W\d_]{2,})", citation, re.UNICODE)
    return (name.group(1).lower(), year.group(1)) if year and name else None

def parse_sidecar(path: Path) -> dict:
    text = path.read_text(errors="replace")
    fields = {key: None for key in SIDECAR_FIELDS}
    cite = re.search(r"\*\*Citation:\*\*\s*(.+)", text)
    doi = re.search(r"\b(?:DOI|doi):\s*([0-9.]+/[^\s]+)", text)
    sha = re.search(r"SHA-256\s+`([0-9a-f]{64})`", text, re.I)
    url = re.search(r"https?://[^\s)]+", text)
    date = re.search(r"retrieved(?: date)?:?\s*(\d{4}-\d{2}-\d{2})", text, re.I)
    if cite:
        fields["citation"] = cite.group(1).strip()
    if doi:
        fields["doi"] = doi.group(1).rstrip(".")
    if sha:
        fields["sha256"] = sha.group(1)
    if url:
        fields["retrieval_url"] = url.group(0).rstrip(").,")
    if date:
        fields["retrieved_date"] = date.group(1)
    if re.search(r"\b(open archive|OA|CC BY|NASA ADS open|J-STAGE OA|NTRS open)\b", text, re.I):
        fields["license_or_oa_basis"] = "OA (sidecar prose)"
    aliases = {
        "citation": "citation", "doi": "doi", "sha256": "sha256",
        "retrieveddate": "retrieved_date", "retrievedat": "retrieved_date",
        "retrieved": "retrieved_date", "retrievaldate": "retrieved_date",
        "retrievedurl": "retrieval_url", "retrievalurl": "retrieval_url",
        "obtainedfrom": "retrieval_url",
        "officialopenurl": "retrieval_url", "url": "retrieval_url",
        "licenseoroabasis": "license_or_oa_basis", "licence": "license_or_oa_basis",
        "license": "license_or_oa_basis", "licencetext": "license_or_oa_basis",
        "access": "access",
    }
    declared = set()
    retrieval_priority = 0
    lines = text.splitlines()
    section = ""
    for number, line in enumerate(lines):
        heading = re.match(r"^##\s+(.+)", line)
        if heading:
            section = heading.group(1).lower()
            field = aliases.get(re.sub(r"[^a-z0-9]", "", section))
            if field and (field not in declared or field == "retrieval_url"):
                paragraph = "\n".join(lines[number + 1:]).lstrip().split("\n\n", 1)[0]
                line = f"{section}: {' '.join(paragraph.split())}"
            else:
                continue
        if section.startswith("file ("):
            continue
        if line.strip().startswith("|"):
            cells = line.strip().strip("|").split("|")
            if len(cells) == 2:
                line = f"{cells[0].strip()}: {cells[1].strip()}"
        line = re.sub(r"^\s*[-*]\s+", "", line).replace("**", "").strip().strip("`")
        key, sep, value = line.partition(":")
        key = re.sub(r"[^a-z0-9]", "", key.lower())
        field = aliases.get(key)
        priority = 2 if key in {"retrievedurl", "retrievalurl", "obtainedfrom"} else 1
        if not sep or not field:
            continue
        if field == "retrieval_url":
            if priority <= retrieval_priority:
                continue
            retrieval_priority = priority
        elif field in declared:
            continue
        declared.add(field)
        value = value.strip().strip("`<>\"'")
        if field == "access":
            value = re.sub(r"^access:\s*", "", value, flags=re.I).strip("`")
            value = re.split(r"[`\s]", value, maxsplit=1)[0]
            fields[field] = value
            continue
        if value.lower() in {"", "null", "none", "n/a", "unknown", "—"}:
            fields[field] = None
            continue
        if field in {"doi", "sha256", "retrieved_date", "retrieval_url"}:
            pattern = {"doi": DOI_RE, "sha256": re.compile(r"[0-9a-f]{64}", re.I),
                       "retrieved_date": re.compile(r"\d{4}-\d{2}-\d{2}"),
                       "retrieval_url": re.compile(r"https?://[^\s<>`]+")}[field]
            match = pattern.search(value)
            value = match.group(0).rstrip("`>),.") if match else None
        if value:
            fields[field] = value
        else:
            fields[field] = None
    fields["local_paths"] = re.findall(r"(docs(?:-private)?/[^\s)\"']+\.pdf)", text)
    fields["missing_fields"] = [key for key in SIDECAR_FIELDS if not fields[key]]
    return fields

def load_yaml(path: Path):
    return yaml.safe_load(path.read_text()) or {}

def collect_ids_and_dois(obj, ids: set[str], id_dois: dict[str, set[str]], nearby_doi: str | None = None) -> None:
    if isinstance(obj, dict):
        local_doi = nearby_doi
        raw = obj.get("doi")
        if isinstance(raw, str) and raw.strip():
            local_doi = raw.strip().rstrip(".")
        local_ids = [obj[k].strip() for k in ID_KEYS if isinstance(obj.get(k), str) and obj[k].strip()]
        ids.update(local_ids)
        if local_doi:
            for sid in local_ids:
                id_dois[sid].add(local_doi)
        sources = obj.get("sources")
        if isinstance(sources, dict):
            for name, body in sources.items():
                ids.add(str(name))
                collect_ids_and_dois(body, ids, id_dois, nearby_doi=None)
                if isinstance(body, dict) and doi_of(body):
                    id_dois[str(name)].add(doi_of(body))
        for key, value in obj.items():
            if key != "sources":
                collect_ids_and_dois(value, ids, id_dois, nearby_doi=local_doi)
    elif isinstance(obj, list):
        for item in obj:
            collect_ids_and_dois(item, ids, id_dois, nearby_doi=nearby_doi)

def load_extracts(root: Path) -> dict[str, dict]:
    extracts, directory = {}, root / "data/literature/extracts"
    _, tracked = git_tracked_set(root)
    if not directory.is_dir():
        return extracts
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        doc = load_yaml(path)
        if isinstance(doc, dict) and doc.get("schema_version") == "literature_extract.v1":
            extracts[path.stem] = {
                "doc": doc,
                "files": [{"path": posix(path.relative_to(root)), "review_status": doc.get("review_status") or "unknown",
                           "rows": sum(len(body.get("observations") or []) for body in (doc.get("species") or {}).values())}],
            }
            locators = [row.get("locator") or {} for body in (doc.get("species") or {}).values()
                        for row in body.get("observations") or []]
            if any("docs-private/" in str(value) for locator in locators for value in locator.values()):
                extracts[path.stem]["files"][0]["locator_status"] = "private_path"
            paths = {str(value) for locator in locators for key, value in locator.items()
                     if (key == "path" or key.endswith("_path")) and value}
            for locator in paths:
                if re.match(r"https?://", locator):
                    continue
                target = (root / locator).resolve()
                relative = rel_to(target, root)
                if ("docs-private" in target.parts or not target.is_relative_to(root)
                        or (tracked is not None and relative not in tracked)):
                    extracts[path.stem]["files"][0]["locator_status"] = "private_path"
                    break
    return extracts

def load_pdfs(root: Path) -> dict[str, dict]:
    found, directory = {}, root / "docs/references/pdfs"
    if not directory.is_dir():
        return found
    for path in sorted(directory.rglob("*.pdf")):
        sidecar = path.with_suffix(".md")
        found[path.stem] = {
            "path": posix(path.relative_to(root)), "sha256": sha256_file(path),
            "sidecar_path": posix(sidecar.relative_to(root)) if sidecar.is_file() else None,
            "sidecar": parse_sidecar(sidecar) if sidecar.is_file() else None,
        }
    for sidecar in sorted(directory.rglob("*.md")):
        if sidecar.stem in found or sidecar.name == "README.md":
            continue
        info = parse_sidecar(sidecar)
        if info.get("citation") or info.get("doi"):
            found[sidecar.stem] = {
                "path": None, "sha256": None,
                "sidecar_path": posix(sidecar.relative_to(root)), "sidecar": info,
            }
    return found

def corpus_available(corpus: Path, commit: str | None) -> bool:
    return bool(commit) or any((corpus / name).is_dir() for name in ("raw", "extracts", "ledger"))

def corpus_pointers(corpus: Path, source_id: str, pdf_sha: str | None,
                    extract_path: Path, commit: str | None) -> dict:
    if not corpus_available(corpus, commit):
        return dict.fromkeys(("raw", "sidecar", "text", "tables", "extract", "ledger", "commit"))
    raw = corpus / "raw" / source_id / f"{source_id}.pdf"
    sidecar = raw.parent / "sidecar.yaml"
    extract = corpus / "extracts" / f"{source_id}.yaml"
    ledger = corpus / "ledger" / f"{source_id}.yaml"
    raw_sha = sha256_file(raw) if raw.is_file() else None
    ledger_error = None
    try:
        loaded = load_yaml(ledger) if ledger.is_file() else {}
    except yaml.YAMLError as exc:
        loaded = {}
        ledger_error = f"Invalid YAML: {exc.problem} at line {exc.problem_mark.line + 1}"
    # Ledger stage shapes seen in the corpus: a mapping stage -> {date, ...},
    # a mapping stage -> str, a list of {stage, date, ...} entries, or nothing.
    # Normalise to an ordered mapping stage -> dict so no shape can crash the
    # index; anything unrecognisable is reported via ledger_error, never guessed.
    stages = {}
    raw_stages = loaded.get("stages") if isinstance(loaded, dict) else None
    if isinstance(raw_stages, dict):
        for name, value in raw_stages.items():
            stages[str(name)] = value if isinstance(value, dict) else {"note": value}
    elif isinstance(raw_stages, list):
        for i, entry in enumerate(raw_stages):
            if isinstance(entry, dict):
                stages[str(entry.get("stage") or f"stage_{i}")] = entry
            elif entry is not None:
                stages[f"stage_{i}"] = {"note": entry}
    elif raw_stages not in (None, {}, []) and ledger_error is None:
        ledger_error = f"Unrecognised stages shape: {type(raw_stages).__name__}"
    last = max(stages, key=lambda stage: (str(stages[stage].get("date") or ""),
                                         list(stages).index(stage))) if stages else None
    def directory(name):
        path = corpus / name / source_id
        return {"path": posix(path.relative_to(corpus)), "exists": path.is_dir(),
                "file_count": sum(p.is_file() for p in path.rglob("*")) if path.is_dir() else 0}
    return {
        "raw": {"path": posix(raw.relative_to(corpus)), "present": raw.is_file(), "sha256": raw_sha,
                "matches_simulator": raw_sha == pdf_sha if raw_sha and pdf_sha else None},
        "sidecar": {"path": posix(sidecar.relative_to(corpus)), "present": sidecar.is_file()},
        "text": directory("text"), "tables": directory("tables"),
        "extract": {"path": posix(extract.relative_to(corpus)), "present": extract.is_file(),
                    "matches_simulator": extract.read_bytes() == extract_path.read_bytes()
                    if extract.is_file() and extract_path.is_file() else None},
        "ledger": {"path": posix(ledger.relative_to(corpus)), "present": ledger.is_file(),
                   "error": ledger_error,
                   "last_stage": last, "date": str(stages[last].get("date"))
                   if last and stages[last].get("date") is not None else None},
        "commit": commit,
    }

def load_consumers(root: Path) -> tuple[list[dict], list[dict]]:
    def recs(paths):
        out = []
        for path in paths:
            doc = load_yaml(path)
            if not isinstance(doc, dict) or doc.get("schema_version") == SCHEMA:
                continue
            ids, id_dois = set(), defaultdict(set)
            collect_ids_and_dois(doc, ids, id_dois)
            out.append({"path": posix(path.relative_to(root)), "ids": ids, "id_dois": id_dois})
        return out
    lit = root / "data/literature"
    measurements = recs(p for p in sorted(lit.glob("*.yaml")) if p.name != "INDEX.yaml") if lit.is_dir() else []
    presets_dir = root / "data/presets"
    presets = recs(sorted(presets_dir.rglob("*.yaml"))) if presets_dir.is_dir() else []
    return measurements, presets

def walk_private_roots(root: Path, private_roots: list[Path]):
    scanned, missing, by_candidate = [], [], defaultdict(list)
    for raw in private_roots:
        resolved, label = resolve_against(raw, root), posix(raw)
        if not resolved.is_dir():
            missing.append(label)
            continue
        scanned.append(label)
        for path in sorted(resolved.rglob("*.pdf")):
            candidate = path.parent.name if path.name == "source.pdf" else path.stem
            by_candidate[candidate].append(rel_to(path, root))
    return scanned, missing, {key: sorted(set(vals)) for key, vals in by_candidate.items()}

def parse_hunt_json(path: Path) -> dict[str, list[str]]:
    hits: dict[str, set[str]] = defaultdict(set)
    for hunt in json.loads(path.read_text()):
        hid = hunt.get("id")
        if not hid:
            continue
        blob = json.dumps(hunt)
        for regex in HUNT_RES:
            for match in regex.finditer(blob):
                hits[match.group(1)].add(hid)
    return {key: sorted(values, key=hunt_sort_key) for key, values in hits.items()}

def candidate_matches(sid: str, candidate: str) -> bool:
    if candidate == sid or candidate.startswith(sid + "-") or candidate.startswith(sid + "_"):
        return True
    rest = re.sub(r"^kems-(?:ms)?\d+-", "", sid)
    return rest != sid and (candidate == rest or candidate.startswith(rest + "-") or candidate.startswith(rest + "_"))

def pdf_stem_aliases(extract_id: str, pdf_stems: set[str]) -> list[str]:
    return sorted(s for s in pdf_stems if s != extract_id and (extract_id == f"kems-{s}" or extract_id.endswith(f"-{s}")))

def provenance_stems(extract: dict) -> list[str]:
    doc = extract.get("doc") or {}
    blob = " ".join(str((doc.get(k) or {}).get(f) or "") for k, f in (("extraction", "method"), ("extraction", "provenance_path"), ("source", "url")))
    return sorted(set(PDF_PATH_RE.findall(blob)))

def access_for(source_id: str, src: dict, sidecar: dict | None, compilations: dict, has_pdf: bool) -> str:
    if has_pdf:
        return "held"
    for key, body in (compilations.get("sources") or {}).items():
        if source_id in {key, str(key).replace("_", "-")}:
            raw = str((body or {}).get("access") or "").lower()
            if "open_public" in raw:
                return "OA"
            if any(part in raw for part in ("copyrighted", "commercial_license", "subscription")):
                return "paywalled"
    if sidecar:
        access = str(sidecar.get("access") or "").lower().strip("`")
        if access in {"open", "oa"}:
            return "OA"
        if access in {"held", "paywalled", "unknown"}:
            return access
        if re.search(r"\b(OA|CC BY|Creative Commons|open access)\b", str(sidecar.get("license_or_oa_basis") or ""), re.I):
            return "OA"
    return "OA" if any(host in str(src.get("url") or "") for host in OA_HOSTS) else "unknown"

def build_alias_map(extracts, pdfs, measurements, presets):
    meta: dict[str, dict] = {}
    for sid in set(extracts) | set(pdfs):
        src = dict((extracts[sid].get("doc") or {}).get("source") or {}) if sid in extracts else {}
        if not src and sid in pdfs and pdfs[sid].get("sidecar"):
            sc = pdfs[sid]["sidecar"]
            src = {"citation": sc.get("citation"), "doi": sc.get("doi"), "url": sc.get("retrieval_url")}
        doi, report, ay = doi_of(src), report_number(src), author_year(sid, str(src.get("citation") or ""))
        meta[sid] = {"dois": {doi} if doi else set(), "reports": {report} if report else set(), "ay": {ay} if ay else set()}
    consumer_ids: set[str] = set()
    for rec in measurements + presets:
        consumer_ids.update(rec["ids"])
        for sid in rec["ids"]:
            body = meta.setdefault(sid, {"dois": set(), "reports": set(), "ay": set()})
            body["dois"].update(rec["id_dois"].get(sid, ()))
            ay = author_year(sid)
            if ay:
                body["ay"].add(ay)
    doi_g, report_g, ay_g = defaultdict(set), defaultdict(set), defaultdict(set)
    for sid, body in meta.items():
        for doi in body["dois"]:
            doi_g[doi.lower()].add(sid)
        for report in body["reports"]:
            report_g[report].add(sid)
        for ay in body["ay"]:
            ay_g[ay].add(sid)
    aliases: dict[str, set[str]] = defaultdict(set)
    for group in list(doi_g.values()) + list(report_g.values()):
        if len(group) > 1:
            for sid in group:
                if sid in extracts:
                    aliases[sid].update(group - {sid})
    for extract_id in extracts:
        aliases[extract_id].update(pdf_stem_aliases(extract_id, set(pdfs)))
        aliases[extract_id].update(s for s in provenance_stems(extracts[extract_id]) if s != extract_id)
        e_ay = next(iter(meta[extract_id]["ay"]), None)
        if e_ay:
            for cid in consumer_ids:
                if cid not in extracts and cid != extract_id and e_ay in meta.get(cid, {}).get("ay", set()):
                    aliases[extract_id].add(cid)
    collisions, seen = [], set()
    def add(ctype, ckey, members):
        member_list = tuple(sorted(members))
        if len(member_list) < 2 or member_list in seen or not any(n in extracts or n in pdfs for n in member_list):
            return
        seen.add(member_list)
        collisions.append({"type": ctype, "key": ckey, "source_ids": list(member_list), "resolution": "owner/controller"})
    for doi, group in doi_g.items():
        add("same_doi", doi, group)
    for report, group in report_g.items():
        add("same_report", report, group)
    for ay, group in ay_g.items():
        add("source_id_alias", f"{ay[0]}-{ay[1]}", group)
    for extract_id in extracts:
        stems = set(pdf_stem_aliases(extract_id, set(pdfs)))
        if stems:
            add("filename_stem_mismatch", extract_id, {extract_id, *stems})
        cited = [s for s in provenance_stems(extracts[extract_id]) if s != extract_id]
        if cited:
            add("pdf_stem_vs_extract", extract_id, {extract_id, *cited})
    collisions.sort(key=lambda item: (item["type"], item["key"], tuple(item["source_ids"])))
    return {key: sorted(vals) for key, vals in aliases.items() if vals}, collisions

def build_index(root: Path, *, private_roots: list[Path] | None = None, hunt_json: Path | None = None, root_label: str = ".") -> dict:
    root = root.resolve()
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
    corpus_commit = None
    if (corpus / ".git").exists():
        try:
            result = subprocess.run(["git", "-C", str(corpus), "rev-parse", "HEAD"], capture_output=True, text=True)
            corpus_commit = result.stdout.strip() if result.returncode == 0 else None
        except OSError:
            pass
    extracts, pdfs = load_extracts(root), load_pdfs(root)
    measurements, presets = load_consumers(root)
    access_path = root / "data/literature/compilations/access-status.yaml"
    compilations = load_yaml(access_path) if access_path.is_file() else {}
    compilations = compilations if isinstance(compilations, dict) else {}
    git_mode, tracked = git_tracked_set(root)
    scanned, not_scanned, private_copies = walk_private_roots(root, list(private_roots or []))
    notes, hunts, hunt_label = [], {}, None
    if hunt_json is None:
        notes.append("hunt_ids column empty; --hunt-json not provided")
    else:
        resolved, hunt_label = resolve_against(hunt_json, root), posix(hunt_json)
        hunts = parse_hunt_json(resolved) if resolved.is_file() else hunts
        if not resolved.is_file():
            notes.append("hunt_ids column empty; --hunt-json path not found"); not_scanned.append(hunt_label); hunt_label = None
    if git_mode != "git":
        notes.append("pdf_tracked is unknown; --root is not a git work tree")
    if not_scanned:
        notes.append("private_roots_not_scanned listed in scan header; copies were not invented")
    aliases_for, collisions = build_alias_map(extracts, pdfs, measurements, presets)
    attached = {}
    for extract_id in extracts:
        for stem in pdfs:
            if stem == extract_id or extract_id == f"kems-{stem}":
                attached.setdefault(extract_id, stem)
    source_ids = sorted(set(extracts) | {stem for stem in pdfs if stem not in extracts and stem not in attached.values()})
    battery_path = root / "scripts/calibration_battery.py"
    quoted = set(re.findall(r"[\"']([^\"']+)[\"']", battery_path.read_text())) if battery_path.is_file() else set()
    rows = []
    for source_id in source_ids:
        extract, aliases = extracts.get(source_id), list(aliases_for.get(source_id, []))
        pdf_stem = attached.get(source_id) or (source_id if source_id in pdfs else None)
        pdf = pdfs.get(pdf_stem) if pdf_stem else None
        src = dict(((extract or {}).get("doc") or {}).get("source") or {})
        if pdf and pdf.get("sidecar"):
            for key in ("citation", "doi"):
                if not src.get(key):
                    src[key] = pdf["sidecar"].get(key)
        citation, doi, report = src.get("citation"), doi_of(src), report_number(src)
        pdf_path, sidecar_info, sidecar_path = (pdf["path"] if pdf else None), (pdf or {}).get("sidecar"), (pdf or {}).get("sidecar_path")
        copies, names = [], [source_id, *aliases]
        for candidate, paths in private_copies.items():
            if any(candidate_matches(name, candidate) for name in names):
                copies.extend(paths)
        copies = sorted(set(copies))
        last_seen = list(copies)
        if sidecar_info:
            last_seen += [p for p in sidecar_info.get("local_paths") or [] if (root / p).is_file() and p != pdf_path]
        last_seen = sorted({p for p in last_seen if p != pdf_path})
        missing = list(sidecar_info["missing_fields"]) if sidecar_info else list(SIDECAR_FIELDS)
        if git_mode != "git":
            tracked_flag: bool | str | None = "unknown" if pdf_path else None
        else:
            tracked_flag = (pdf_path in tracked) if pdf_path and tracked is not None else None
        name_set = {source_id, *aliases}
        battery = [rec["path"] for rec in presets if rec["ids"] & name_set]
        measure_hits = [rec["path"] for rec in measurements if rec["ids"] & name_set]
        if name_set & quoted:
            battery.append("scripts/calibration_battery.py")
        hunt_ids = set(hunts.get(source_id) or [])
        for alias in aliases:
            hunt_ids.update(hunts.get(alias) or [])
        rows.append({
            "source_id": source_id, "aliases": aliases, "citation": citation, "doi": doi,
            "report_number": report, "identifier": doi or report,
            "pdf_status": "present" if pdf_path else "ABSENT", "pdf_path": pdf_path, "pdf_last_seen": last_seen,
            "pdf_sha256": (pdf or {}).get("sha256"), "pdf_tracked": tracked_flag, "sidecar_path": sidecar_path,
            "sidecar_missing_fields": missing,
            "access_status": access_for(source_id, src, sidecar_info, compilations, bool(pdf_path)),
            "extracts": list((extract or {}).get("files") or []),
            "battery_datasets": sorted(set(battery + measure_hits)),
            "hunt_ids": sorted(hunt_ids, key=hunt_sort_key), "copies": copies,
            "measurement_sets": sorted(set(measure_hits)),
            "corpus_status": "available" if corpus_available(corpus, corpus_commit) else "unavailable",
            "corpus": corpus_pointers(corpus, source_id, (pdf or {}).get("sha256"),
                                      root / "data/literature/extracts" / f"{source_id}.yaml", corpus_commit),
        })
    pdfs_wo = sorted(r["source_id"] for r in rows if r["pdf_status"] == "present" and not r["extracts"])
    extracts_wo = sorted(r["source_id"] for r in rows if r["extracts"] and r["pdf_status"] == "ABSENT")
    private_locators = [r["source_id"] for r in rows
                        if any(item.get("locator_status") == "private_path" for item in r["extracts"])]
    untracked = sorted({rel for r in rows for rel in (r["pdf_path"], r["sidecar_path"]) if rel and git_mode == "git" and tracked is not None and rel not in tracked})
    sidecars_missing = [{"source_id": r["source_id"], "sidecar_path": r["sidecar_path"], "missing_fields": r["sidecar_missing_fields"]}
                        for r in rows if (r["pdf_status"] == "present" or r["sidecar_path"]) and r["sidecar_missing_fields"]]
    n_extracts = len({item["path"] for r in rows for item in r["extracts"]})
    n_pdfs = len({r["pdf_path"] for r in rows if r["pdf_status"] == "present" and r["pdf_path"]})
    n_tracked = len({r["pdf_path"] for r in rows if r["pdf_path"] and str(r["pdf_path"]).startswith("docs/references/pdfs/99-kems-langmuir/") and r["pdf_tracked"] is True})
    return {
        "schema_version": SCHEMA, "generated_by": "data/literature/build_index.py",
        "scan": {"root": root_label, "private_roots_scanned": scanned, "private_roots_not_scanned": sorted(set(not_scanned)),
                 "hunt_json": hunt_label, "notes": sorted(set(notes)), "corpus_root": str(corpus)},
        "counts": {"sources": len(rows), "extracts": n_extracts, "pdfs_present_in_worktree": n_pdfs,
                   "extracts_with_private_locators": len(private_locators),
                   "pdfs_tracked_99_kems_langmuir": n_tracked, "pdfs_without_extract": len(pdfs_wo),
                   "extracts_without_pdf": len(extracts_wo), "alias_groups_needing_resolution": len(collisions)},
        "gaps": {"pdfs_without_extract": pdfs_wo, "extracts_without_pdf": extracts_wo, "untracked_files": untracked,
                 "extracts_with_private_locators": private_locators,
                 "sidecars_missing_fields": sidecars_missing, "source_id_collisions": collisions},
        "sources": rows,
    }

def _ticks(items) -> str:
    return ", ".join(f"`{item}`" for item in items) or "—"

def render_md(index: dict) -> str:
    counts, gaps, scan = index["counts"], index["gaps"], index["scan"]
    lines = [
        "# Empirical literature index", "",
        "Generated by `data/literature/build_index.py`. Do not edit by hand.",
        "Canonical `source_id` is the extract filename stem. Preset ids are aliases, never rows.",
        "", "## Scan", "",
        f"- root: `{scan['root']}`",
        f"- corpus root: `{scan['corpus_root']}` (raw/text/tables/ledger paths below are relative to this root)",
        "- Ledger stage: latest dated event; same-date ties use ledger insertion order.",
        f"- private_roots_scanned: {_ticks(scan['private_roots_scanned']).replace('—', '(none)')}",
        f"- private_roots_not_scanned: {_ticks(scan['private_roots_not_scanned']).replace('—', '(none)')}",
        f"- hunt_json: `{scan['hunt_json']}`" if scan.get("hunt_json") else "- hunt_json: (not provided)",
        *[f"- note: {note}" for note in scan.get("notes") or []],
        "", "## Counts", "",
        f"- Sources: {counts['sources']}", f"- Extracts (`literature_extract.v1`): {counts['extracts']}",
        f"- PDFs present in this worktree: {counts['pdfs_present_in_worktree']}",
        f"- Tracked PDFs in `docs/references/pdfs/99-kems-langmuir/`: {counts['pdfs_tracked_99_kems_langmuir']}",
        f"- PDFs with no extract: {counts['pdfs_without_extract']}", f"- Extracts with no PDF: {counts['extracts_without_pdf']}",
        f"- Alias groups needing owner/controller resolution: {counts['alias_groups_needing_resolution']}",
        f"- Extracts with private/non-public row locators: {counts['extracts_with_private_locators']}",
        "", "## Sources", "",
        "| source_id | citation | DOI / report | PDF (simulator, sha8) | corpus raw / text / tables | extract (rows, review) | ledger stage |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in index["sources"]:
        cite = (row.get("citation") or "").replace("|", "\\|")
        cite = " ".join(cite.split())
        ident = row.get("doi") or row.get("report_number") or row.get("identifier") or ""
        if row["pdf_status"] == "present":
            pdf = f"`{row['pdf_path']}`"
        elif row.get("pdf_last_seen"):
            pdf = "ABSENT; " + row["pdf_last_seen"][0].replace("|", "\\|")
        else:
            pdf = "ABSENT"
        extracts = ", ".join(f"`{i['path']}` ({i['rows']} rows, {i['review_status']}"
                             + (", private_path" if i.get("locator_status") == "private_path" else "")
                             + ")" for i in row.get("extracts") or []) or "—"
        corpus = row["corpus"]
        if row["corpus_status"] == "unavailable":
            pointers, ledger = "unavailable", "—"
        else:
            raw = corpus["raw"]
            pointers = f"`{raw['path']}` ({'present' if raw['present'] else 'ABSENT'}, {(raw['sha256'] or '')[:8]})"
            for name in ("text", "tables"):
                item = corpus[name]
                pointers += f"; `{item['path']}/` ({item['file_count']} files, {'exists' if item['exists'] else 'ABSENT'})"
            event = corpus["ledger"]
            ledger = f"`{event['path']}`: {event['last_stage'] or '—'} ({event['date'] or '—'})"
        lines.append(
            f"| `{row['source_id']}` | {cite} | {ident} | {pdf}, `{(row.get('pdf_sha256') or '')[:8]}` | "
            f"{pointers} | {extracts} | {ledger} |"
        )
    def block(title, items, fmt=lambda x: f"- `{x}`"):
        lines.extend(["", f"### {title}", ""])
        lines.extend(fmt(x) for x in items) if items else lines.append("(none)")
    block("PDFs with no extract", gaps["pdfs_without_extract"])
    block("Extracts with no PDF", gaps["extracts_without_pdf"])
    block("Extracts needing public row locators (b-477)", gaps["extracts_with_private_locators"])
    block("Untracked files", gaps["untracked_files"])
    lines += ["", "### Sidecars missing fields", "",
              "Required sibling fields: citation, DOI, license/OA basis, sha256, retrieved date, retrieval URL.", ""]
    if gaps["sidecars_missing_fields"]:
        for item in gaps["sidecars_missing_fields"]:
            lines.append(f"- `{item['source_id']}` — `{item['sidecar_path'] or '(no sidecar)'}` — missing: {', '.join(item['missing_fields'])}")
    else:
        lines.append("(none)")
    lines += ["", "### source_id alias groups (owner/controller resolution)", "",
              "The builder does not pick a winner.", ""]
    if gaps["source_id_collisions"]:
        for item in gaps["source_id_collisions"]:
            lines.append(f"- {item['type']} `{item['key']}`: {_ticks(item['source_ids'])} — needs owner/controller resolution")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _noise_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return "__pycache__" in parts or path.endswith(".pyc") or parts[-1] == ".DS_Store"


def _iter_named_dirs(directory: Path):
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if path.is_dir() and not path.name.startswith((".", "_")):
            yield path


def _files_under(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file() and not _noise_path(posix(path)))


def _document_files(raw_dir: Path) -> list[Path]:
    return [path for path in _files_under(raw_dir) if path.name != "sidecar.yaml"]


def _parse_iso_datetime(value) -> datetime | None:
    if value in (None, "", "unknown", "null", "none"):
        return None
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def load_corpus_claims(corpus: Path) -> dict[str, dict]:
    found, directory = {}, corpus / "claims"
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.claim")):
        first = (path.read_text(errors="replace").splitlines() or [""])[0].strip()
        parts = first.split()
        found[path.stem] = {
            "path": posix(path.relative_to(corpus)),
            "dispatch_id": parts[0] if parts else None,
            "stamped_at": parts[1] if len(parts) > 1 else None,
            "claimed_stage": parts[2] if len(parts) > 2 else None,
        }
    return found


def load_corpus_extract_stems(corpus: Path) -> set[str]:
    directory = corpus / "extracts"
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob("*.yaml") if not path.name.startswith("_")}


def load_compilation_identities(root: Path) -> tuple[set[str], dict[str, str], set[str]]:
    """Compilation-path ids (canonical, DOI map, match aliases including hyphen/underscore folds)."""
    canonical: set[str] = set()
    match_ids: set[str] = set()
    dois: dict[str, str] = {}

    def remember(sid: str | None, doi: str | None = None, *, row: bool = True) -> None:
        if not sid:
            return
        name = str(sid).strip()
        if not name:
            return
        match_ids.add(name)
        match_ids.add(name.replace("_", "-"))
        match_ids.add(name.replace("-", "_"))
        if row:
            canonical.add(name)
        if doi:
            dois[doi.lower()] = name

    compilations = root / "data/literature/compilations"
    if compilations.is_dir():
        for path in _iter_named_dirs(compilations):
            remember(path.name)
            manifest = path / "manifest.yaml"
            sid = path.name
            if manifest.is_file():
                try:
                    doc = load_yaml(manifest)
                except yaml.YAMLError:
                    doc = {}
                if isinstance(doc, dict):
                    sid = str(doc.get("source_id") or path.name)
                    remember(sid)
                    src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
                    remember(sid, doi_of(src or {}))
                    blob = yaml.safe_dump(doc)
                    for match in re.finditer(r"raw/([A-Za-z0-9_.-]+)/", blob):
                        remember(match.group(1))
            sidecar = path / "source" / "sidecar.yaml"
            if sidecar.is_file():
                try:
                    sc = load_yaml(sidecar)
                except yaml.YAMLError:
                    sc = {}
                if isinstance(sc, dict):
                    remember(sid, doi_of(sc))
    access_path = compilations / "access-status.yaml"
    if access_path.is_file():
        try:
            access = load_yaml(access_path)
        except yaml.YAMLError:
            access = {}
        if isinstance(access, dict):
            for key, body in (access.get("sources") or {}).items():
                # Access-status keys are catalog aliases, not corpus source_ids.
                remember(str(key), row=False)
                if not isinstance(body, dict):
                    continue
                remember(str(key), doi_of(body), row=False)
                for field in ("harvested_path", "existing_path", "source_path"):
                    raw = str(body.get(field) or "")
                    for match in re.finditer(r"(?:compilations|extracts|raw)/([A-Za-z0-9_.-]+)", raw):
                        remember(match.group(1).removesuffix(".yaml"))
    obs_dir = root / "data/literature/observations-v2"
    if obs_dir.is_dir():
        for path in iter_observation_store_paths(obs_dir, "compilations-*.yaml"):
            family = compilation_family_from_store_path(path)
            if family:
                remember(family)
    return canonical, dois, match_ids


def is_compilation_source(source_id: str, doi: str | None, ids: set[str], dois: dict[str, str]) -> bool:
    if source_id in ids or source_id.replace("_", "-") in ids or source_id.replace("-", "_") in ids:
        return True
    return bool(doi and doi.lower() in dois)


def observation_battery_usable(obs: dict) -> bool:
    """Measured evidence class AND a known quantity AND a stored numeric value."""
    if not isinstance(obs, dict):
        return False
    evidence = (obs.get("evidence") or {}).get("class") or {}
    if not isinstance(evidence, dict) or evidence.get("tag") != "value":
        return False
    if evidence.get("value") not in MEASURED_EVIDENCE_CLASSES:
        return False
    quantity = ((obs.get("identity") or {}).get("quantity") or {})
    if not isinstance(quantity, dict) or quantity.get("tag") != "value":
        return False
    if quantity.get("value") in (None, "", "unknown"):
        return False
    value = obs.get("value") or {}
    if not isinstance(value, dict) or value.get("kind") not in NUMERIC_VALUE_KINDS:
        return False
    kind = value["kind"]
    if kind == "point":
        return value.get("point") is not None
    if kind == "series":
        return bool(value.get("series"))
    if kind == "bound":
        return value.get("bound_value") is not None
    if kind == "interval":
        return value.get("interval_low") is not None or value.get("interval_high") is not None
    if kind == "relative_series":
        return bool(value.get("relative_series"))
    return False


def _count_store_rows(observations: list) -> tuple[int, int]:
    rows = [item for item in observations if isinstance(item, dict)]
    return sum(1 for item in rows if observation_battery_usable(item)), len(rows)


def load_v21_store(root: Path) -> dict[str, dict]:
    """Per-source v2.1 observation payloads. Residual pin ledgers are not source ingest."""
    store: dict[str, dict] = {}

    def add(source_id: str, path: Path, observations: list) -> None:
        usable, total = _count_store_rows(observations)
        current = store.get(source_id)
        if current:
            usable += current["usable_rows"]
            total += current["total_rows"]
            artefacts = list(current["artefacts"]) + [posix(path.relative_to(root))]
        else:
            artefacts = [posix(path.relative_to(root))]
        store[source_id] = {"usable_rows": usable, "total_rows": total, "artefacts": artefacts}

    extracts_v2 = root / "data/literature/extracts-v2"
    if extracts_v2.is_dir():
        for path in sorted(extracts_v2.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                doc = load_yaml(path)
            except yaml.YAMLError:
                doc = {}
            observations = doc.get("observations") if isinstance(doc, dict) else None
            add(path.stem, path, observations if isinstance(observations, list) else [])
    obs_dir = root / "data/literature/observations-v2"
    if obs_dir.is_dir():
        for path in iter_observation_store_paths(obs_dir):
            # Pin ledgers migrated as observation payloads are not per-source ingest.
            if path.name.startswith("_") or "ledger" in path.name or "differential" in path.name:
                continue
            # Compilation payloads are assessed functions, not measured evidence
            # (data/literature/compilations/README.md). Loading the multi-MB YAML
            # tree would not find battery-usable rows; store membership is the file
            # itself and total_rows is the observation_id count.
            family = compilation_family_from_store_path(path)
            if family is not None:
                targets = {family}
                manifest = root / "data/literature/compilations" / family / "manifest.yaml"
                if manifest.is_file():
                    try:
                        man = load_yaml(manifest)
                    except yaml.YAMLError:
                        man = {}
                    if isinstance(man, dict) and man.get("source_id"):
                        targets.add(str(man["source_id"]))
                total = path.read_text(errors="replace").count("\n- observation_id:")
                artefact = posix(path.relative_to(root))
                for sid in sorted(targets):
                    current = store.get(sid)
                    artefacts = list(current["artefacts"]) if current else []
                    if artefact not in artefacts:
                        artefacts.append(artefact)
                    store[sid] = {
                        "usable_rows": current["usable_rows"] if current else 0,
                        "total_rows": (current["total_rows"] if current else 0) + total,
                        "artefacts": artefacts,
                    }
                continue
            try:
                doc = load_yaml(path)
            except yaml.YAMLError:
                doc = {}
            if not isinstance(doc, dict):
                continue
            observations = doc.get("observations") if isinstance(doc.get("observations"), list) else []
            by_source: dict[str, list] = defaultdict(list)
            for item in observations:
                if not isinstance(item, dict):
                    continue
                sid = item.get("source_id")
                if isinstance(sid, str) and sid.strip():
                    by_source[sid.strip()].append(item)
            for sid, rows in by_source.items():
                if sid not in store:
                    add(sid, path, rows)
    return store


def _residual_is_scored(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if str(row.get("status") or "").lower() == "refused":
        return False
    numeric = row.get("numeric") if isinstance(row.get("numeric"), dict) else None
    if numeric is not None and numeric.get("value") is not None:
        return True
    residual = row.get("residual")
    if isinstance(residual, dict) and residual.get("value") is not None:
        return True
    if isinstance(residual, (int, float)):
        return True
    if isinstance(residual, str):
        try:
            float(residual)
            return True
        except ValueError:
            return False
    return False


def _residual_rows(doc) -> list[dict]:
    if isinstance(doc, list):
        return [item for item in doc if isinstance(item, dict)]
    if not isinstance(doc, dict):
        return []
    for key in ("residuals", "rows", "sources"):
        value = doc.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            out = []
            for sid, body in value.items():
                if isinstance(body, dict):
                    out.append({"source_id": sid, **body})
            return out
    if _residual_is_scored(doc) or doc.get("source_id"):
        return [doc]
    return []


def load_scored_residuals(root: Path) -> dict[str, str]:
    """Chunk-2 scorer ledger. A missing file is zero wired sources, never an invented score."""
    found: dict[str, str] = {}

    def absorb(path: Path, doc) -> None:
        relative = posix(path.relative_to(root))
        rows = _residual_rows(doc)
        if not rows and path.parent.name == "residuals-v2" and _residual_is_scored(doc if isinstance(doc, dict) else {}):
            found[path.stem] = relative
            return
        for row in rows:
            sid = row.get("source_id") or (path.stem if path.parent.name == "residuals-v2" else None)
            if sid and _residual_is_scored(row):
                found[str(sid)] = relative

    directory = root / "data/literature/residuals-v2"
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                absorb(path, load_yaml(path))
            except yaml.YAMLError:
                continue
    ledger = root / "data/literature/battery_residuals.yaml"
    if ledger.is_file():
        try:
            absorb(ledger, load_yaml(ledger))
        except yaml.YAMLError:
            pass
    return found


def git_uncommitted_paths(root: Path) -> tuple[list[str], str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-uall"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return [], "git unavailable"
    if result.returncode != 0:
        return [], "git status failed"
    ids = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.replace("\\", "/")
        if _noise_path(path):
            continue
        ids.append(path)
    return sorted(set(ids)), None


def git_unpushed_commits(root: Path) -> tuple[list[str], str | None]:
    for spec in ("@{u}..HEAD", "origin/main..HEAD"):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-list", spec],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            return [], "git unavailable"
        if result.returncode == 0:
            return [item for item in result.stdout.split() if item], None
    return [], "no upstream ref to compare"


def extract_mirror_parity(root: Path, corpus: Path) -> dict:
    sim_dir, cor_dir = root / "data/literature/extracts", corpus / "extracts"
    sim = {path.stem: path for path in sim_dir.glob("*.yaml")} if sim_dir.is_dir() else {}
    cor = {path.stem: path for path in cor_dir.glob("*.yaml")} if cor_dir.is_dir() else {}
    sim = {key: path for key, path in sim.items() if not path.name.startswith("_")}
    cor = {key: path for key, path in cor.items() if not path.name.startswith("_")}
    simulator_only = sorted(set(sim) - set(cor))
    corpus_only = sorted(set(cor) - set(sim))
    mismatch = sorted(sid for sid in set(sim) & set(cor) if sim[sid].read_bytes() != cor[sid].read_bytes())
    def pack(ids):
        return {"count": len(ids), "ids": ids}
    return {"simulator_only": pack(simulator_only), "corpus_only": pack(corpus_only), "byte_mismatch": pack(mismatch)}


def _claim_artefact_rank(source_id: str, corpus: Path, has_extract: bool, in_store: bool, wired: bool,
                         has_document: bool, has_folder: bool) -> int:
    if wired:
        return CLAIM_STAGE_RANK["score"]
    if in_store:
        return CLAIM_STAGE_RANK["adopt"]
    if has_extract:
        return CLAIM_STAGE_RANK["extract"]
    if _files_under(corpus / "tables" / source_id):
        return CLAIM_STAGE_RANK["transcribe"]
    if _files_under(corpus / "text" / source_id):
        return CLAIM_STAGE_RANK["decode"]
    if has_document:
        return CLAIM_STAGE_RANK["acquire"]
    if has_folder:
        return CLAIM_STAGE_RANK["locate"]
    return -1


def _sidecar_doi_and_acquired(raw_dir: Path) -> tuple[str | None, datetime | None]:
    sidecar = raw_dir / "sidecar.yaml"
    if not sidecar.is_file():
        return None, None
    try:
        doc = load_yaml(sidecar)
    except yaml.YAMLError:
        return None, None
    if not isinstance(doc, dict):
        return None, None
    # retrieved_at is acquisition evidence, not a stage tag. The `stage` key is ignored.
    return doi_of(doc), _parse_iso_datetime(doc.get("retrieved_at") or doc.get("retrieved_date"))


def build_source_status(root: Path, *, corpus: Path | None = None, now: datetime | None = None,
                        root_label: str = ".") -> dict:
    """Derive one registry row per source_id from artefacts. Never read a stage field."""
    root = root.resolve()
    corpus = Path(corpus) if corpus is not None else Path(
        os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    notes = []
    corpus_ok = corpus_available(corpus, None) or (corpus / ".git").exists() or corpus.is_dir()
    extracts = load_extracts(root)
    corpus_extracts = load_corpus_extract_stems(corpus) if corpus_ok else set()
    claims = load_corpus_claims(corpus) if corpus_ok else {}
    store = load_v21_store(root)
    residuals = load_scored_residuals(root)
    compilation_ids, compilation_dois, compilation_match = load_compilation_identities(root)
    if not residuals:
        notes.append("wired computes to 0: chunk-2 scorer residual ledger absent "
                     "(data/literature/residuals-v2/ or data/literature/battery_residuals.yaml); not faked")

    raw_dirs = {path.name: path for path in _iter_named_dirs(corpus / "raw")} if corpus_ok else {}
    text_dirs = {path.name for path in _iter_named_dirs(corpus / "text")} if corpus_ok else set()
    table_dirs = {path.name for path in _iter_named_dirs(corpus / "tables")} if corpus_ok else set()
    ledger_ids = set()
    if corpus_ok and (corpus / "ledger").is_dir():
        ledger_ids = {path.stem for path in (corpus / "ledger").glob("*.yaml") if not path.name.startswith("_")}

    source_ids = sorted(
        set(raw_dirs) | set(extracts) | corpus_extracts | set(claims) | set(store) | set(residuals)
        | text_dirs | table_dirs | ledger_ids | compilation_ids
    )
    rows = []
    stale_inbox, stale_claims = [], []
    for source_id in source_ids:
        raw_dir = raw_dirs.get(source_id)
        documents = _document_files(raw_dir) if raw_dir else []
        has_folder = raw_dir is not None or source_id in text_dirs or source_id in table_dirs or source_id in ledger_ids
        has_document = bool(documents)
        extract_here = source_id in extracts
        extract_corpus = source_id in corpus_extracts
        has_extract = extract_here or extract_corpus
        claim = claims.get(source_id)
        in_store = source_id in store
        wired = source_id in residuals
        doi, acquired = _sidecar_doi_and_acquired(raw_dir) if raw_dir else (None, None)
        compilation = is_compilation_source(source_id, doi, compilation_match, compilation_dois)
        usable = store[source_id]["usable_rows"] if in_store else 0
        total = store[source_id]["total_rows"] if in_store else 0
        inbox_exclusion = COMPILATION_INBOX_EXCLUSION if compilation and has_document and not has_extract else None

        artefacts: list[str] = []
        reason = None
        stage = "unknown"
        if wired:
            stage = "wired"
            artefacts = [residuals[source_id]]
            reason = "battery ledger row with a scored residual"
        elif in_store:
            artefacts = list(store[source_id]["artefacts"])
            if total > 0 and usable == total:
                stage = "ingested_complete"
                reason = f"v2.1 store present; {usable}/{total} rows battery-usable"
            elif usable >= 1:
                stage = "ingested_partial"
                reason = f"v2.1 store present; {usable}/{total} rows battery-usable"
            else:
                stage = "unknown"
                reason = (f"v2.1 store present but 0 of {total} rows are battery-usable "
                          "(measured evidence class AND known quantity AND stored numeric value); "
                          "not partial (needs ≥1) and not complete (needs all)")
        elif claim or has_extract:
            stage = "in_progress"
            if claim:
                artefacts.append(claim["path"])
            if extract_here:
                artefacts.append(posix(Path("data/literature/extracts") / f"{source_id}.yaml"))
            elif extract_corpus:
                artefacts.append(posix(Path("extracts") / f"{source_id}.yaml"))
            reason = "claim record" if claim and not has_extract else (
                "extract present; no v2.1 store work" if has_extract else "claim record")
        elif has_document:
            artefacts = [posix(path.relative_to(corpus)) for path in documents]
            if compilation:
                stage = "unknown"
                reason = inbox_exclusion
            else:
                stage = "inbox"
                reason = "document in corpus raw/; no extract"
        elif has_folder:
            stage = "located"
            if raw_dir is not None:
                artefacts = [posix(raw_dir.relative_to(corpus))]
            elif source_id in text_dirs:
                artefacts = [f"text/{source_id}"]
            elif source_id in table_dirs:
                artefacts = [f"tables/{source_id}"]
            elif source_id in ledger_ids:
                artefacts = [f"ledger/{source_id}.yaml"]
            reason = "source folder exists in the corpus, but no document"
        else:
            reason = "no corpus folder, document, extract, claim, or v2.1 store artefact"

        if (stage == "inbox" and acquired is not None and claim is None and not has_extract
                and (now - acquired) > timedelta(days=STALE_INBOX_DAYS)):
            stale_inbox.append(source_id)
        if claim:
            claimed_rank = CLAIM_STAGE_RANK.get(str(claim.get("claimed_stage") or "").lower())
            artefact_rank = _claim_artefact_rank(
                source_id, corpus, has_extract, in_store, wired, has_document, has_folder)
            if claimed_rank is None:
                finished = artefact_rank >= CLAIM_STAGE_RANK["decode"]
            else:
                finished = artefact_rank >= claimed_rank
            if finished:
                stale_claims.append(source_id)

        rows.append({
            "source_id": source_id,
            "stage": stage,
            "evidence": {
                "decided_by": stage,
                "artefacts": artefacts,
                "reason": reason,
            },
            "usable_rows": usable,
            "total_rows": total,
            "inbox_exclusion_reason": inbox_exclusion,
        })

    if corpus_ok:
        uncommitted, uncommitted_why = git_uncommitted_paths(corpus)
        unpushed, unpushed_why = git_unpushed_commits(corpus)
        parity = extract_mirror_parity(root, corpus)
    else:
        uncommitted, uncommitted_why = [], "corpus unavailable"
        unpushed, unpushed_why = [], "corpus unavailable"
        parity = extract_mirror_parity(root, corpus)
        notes.append("corpus unavailable; located/inbox/claim evidence cannot be derived")

    def pack(ids, extra_reason=None):
        body = {"count": len(ids), "ids": list(ids)}
        if extra_reason:
            body["could_not_derive"] = extra_reason
        return body

    by_stage = {name: 0 for name in STATUS_STAGES}
    for row in rows:
        by_stage[row["stage"]] = by_stage.get(row["stage"], 0) + 1
    anti_loss = {
        "corpus_uncommitted": pack(uncommitted, uncommitted_why),
        "corpus_unpushed": pack(unpushed, unpushed_why),
        "extract_mirror_parity": parity,
        "stale_inbox": pack(stale_inbox),
        "stale_claims": pack(stale_claims),
    }
    return {
        "schema_version": STATUS_SCHEMA,
        "generated_by": "data/literature/build_index.py",
        "scan": {
            "root": root_label,
            "corpus_root": str(corpus),
            "stale_inbox_days": STALE_INBOX_DAYS,
            "notes": notes,
        },
        "counts": {"sources": len(rows), "by_stage": by_stage},
        "anti_loss": anti_loss,
        "sources": rows,
    }


def write_source_status(status: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": status["schema_version"],
        "generated_by": status["generated_by"],
        "scan": status["scan"],
        "counts": status["counts"],
        "anti_loss": status["anti_loss"],
        "sources": [{key: row.get(key) for key in STATUS_ROW_KEYS} for row in status["sources"]],
    }
    (out_dir / "SOURCE_STATUS.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False)
    )


def write_index(index: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": index["schema_version"], "generated_by": index["generated_by"],
               "scan": index["scan"], "counts": index["counts"], "gaps": index["gaps"],
               "sources": [{k: row.get(k) for k in ROW_KEYS} for row in index["sources"]]}
    (out_dir / "INDEX.yaml").write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False))
    (out_dir / "INDEX.md").write_text(render_md(index))

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--private-root", action="append", default=[], type=Path)
    parser.add_argument("--hunt-json", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root or Path(__file__).resolve().parents[2]
    out_dir = args.out_dir or (root / "data/literature")
    write_index(build_index(root, private_roots=list(args.private_root), hunt_json=args.hunt_json, root_label="."),
                out_dir)
    write_source_status(build_source_status(root, root_label="."), out_dir)
    return 0

if __name__ == "__main__":
    sys.exit(main())
