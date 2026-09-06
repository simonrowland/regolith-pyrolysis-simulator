#!/usr/bin/env python3
"""Build INDEX.yaml + INDEX.md by walking --root. Canonical source_id = extract stem."""
from __future__ import annotations

import argparse, hashlib, json, os, re, subprocess, sys
from collections import defaultdict
from pathlib import Path
import yaml

SCHEMA = "literature_index.v1"
SIDECAR_FIELDS = ("citation", "doi", "license_or_oa_basis", "sha256", "retrieved_date", "retrieval_url")
ID_KEYS = ("source_id", "paper_id", "paper_citation_id", "citation_id")
OA_HOSTS = ("jstage.jst.go.jp", "ntrs.nasa.gov", "nist.gov", "aanda.org", "arxiv.org", "janaf.nist.gov", "webbook.nist.gov")
ROW_KEYS = (
    "source_id", "aliases", "citation", "doi", "report_number", "identifier", "pdf_status", "pdf_path",
    "pdf_last_seen", "pdf_sha256", "pdf_tracked", "sidecar_path", "sidecar_missing_fields",
    "access_status", "extracts", "battery_datasets", "hunt_ids", "copies", "measurement_sets",
    "corpus_status", "corpus",
)
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
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
        stages = (load_yaml(ledger).get("stages") or {}) if ledger.is_file() else {}
    except yaml.YAMLError as exc:
        stages = {}
        ledger_error = f"Invalid YAML: {exc.problem} at line {exc.problem_mark.line + 1}"
    last = max(stages, key=lambda stage: (str((stages[stage] or {}).get("date") or ""),
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
    write_index(build_index(root, private_roots=list(args.private_root), hunt_json=args.hunt_json, root_label="."),
                args.out_dir or (root / "data/literature"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
