#!/usr/bin/env python3
"""Build the tracked empirical-corpus index (INDEX.yaml + INDEX.md).

Rerunnable and deterministic: sources sorted by source_id, lists sorted,
YAML dumped with sort_keys. Does not download, move, delete, or edit extracts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

SCHEMA = "literature_index.v1"
SIDECAR_FIELDS = (
    "citation",
    "doi",
    "license_or_oa_basis",
    "sha256",
    "retrieved_date",
    "retrieval_url",
)
DEFAULT_HUNT_JSON = Path(
    "/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/"
    "Regolith Processing/regolith-pyrolysis-simulator/docs-private/research/"
    "2026-09-05-owner-decisions-sweep/RESEARCH-HUNT.json"
)
MAIN_CHECKOUT = Path(
    "/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/"
    "Regolith Processing/regolith-pyrolysis-simulator"
)
PAPERS_LIB = "docs-private/deep-research/literature"
KEMS_CORPUS = "docs-private/research/kems-corpus/pdfs"
OCR_REPAIR = "docs-private/research/2026-08-27-ocr-repair"
OCR_ART = "docs-private/research/ocr-artifacts"

# Frozen hunt map used when RESEARCH-HUNT.json is not readable (CI).
# Regenerated from that file with the same regexes as parse_hunt_json().
HUNT_CITATIONS_FALLBACK = {
    "kems-001-homma-1966": ["RH-65"],
    "kems-002-ohno-1967": ["RH-66"],
    "kems-003-pound-1972": ["RH-67"],
    "kems-005-fedkin-2006": ["RH-02", "RH-06"],
    "kems-006-zhang-2021": ["RH-44"],
    "kems-007-costa-2015": ["RH-27"],
    "kems-008-schaefer-fegley-2004": ["RH-02", "RH-05", "RH-07", "RH-38", "RH-99", "RH-102"],
    "kems-009-safarian-2013": ["RH-01", "RH-28"],
    "kems-010-richter-2007": ["RH-29"],
    "kems-011-wetzel-gail-2013": ["RH-01", "RH-30"],
    "kems-012-sossi-2019": ["RH-04", "RH-45"],
    "kems-014-drowart-2005": ["RH-10", "RH-37", "RH-46"],
    "kems-015-hashimoto-1983": ["RH-01", "RH-02", "RH-09", "RH-31", "RH-38"],
    "kems-016-stolyarova-1992": ["RH-11"],
    "kems-017-stolyarova-2013": ["RH-32"],
    "kems-018-stolyarova-2012": ["RH-33"],
    "kems-020-hastie-1981-nbsir": ["RH-47"],
    "kems-021-plante-1992-feo": ["RH-04", "RH-68", "RH-102"],
    "kems-022-demaria-1971": ["RH-02", "RH-05", "RH-12", "RH-81"],
    "kems-027-plante-hastie-1983": ["RH-48"],
    "kems-031-halwax-2024": ["RH-69"],
    "kems-032-copland-jacobson-2010": ["RH-34"],
    "kems-035-sauerborn-2005": ["RH-49"],
    "kems-036-sesko-2024": ["RH-50"],
    "kems-037-richter-2002": ["RH-35"],
    "kems-038-matchett-2006": ["RH-70"],
    "kems-040-stolyarova-2015": ["RH-36"],
    "kems-041-sossi-fegley-2018": ["RH-04", "RH-05", "RH-13", "RH-102"],
    "kems-042-nbs-sp561-v1-hastie-1979": ["RH-51"],
    "kems-042-plante-1979": ["RH-51"],
    "kems-ms2000-044": ["RH-04", "RH-14", "RH-81"],
    "lamoreaux-hildenbrand-1984": ["RH-08"],
    "ms2000-044": ["RH-04", "RH-14", "RH-81"],
    "nasa-cea-thermo": ["RH-08", "RH-39"],
    "sf04-magma-companion-workbook": ["RH-81"],
    "ts1985": ["RH-15"],
    "yam1983": ["RH-16"],
}

# Last-seen locations for PDFs not in the tracked 99-kems-langmuir tree.
# Paths are repo-relative from the main checkout unless noted.
KNOWN_LAST_SEEN = {
    "kems-006-zhang-2021": [
        "docs/references/pdfs/99-kems-langmuir/kems-006-zhang-2021.pdf (cited by extract; ABSENT)",
        f"{OCR_REPAIR}/kems-006-zhang-2021/ (extract provenance_path; directory missing)",
    ],
    "kems-014-drowart-2005": [
        f"{OCR_REPAIR}/kems-014-drowart-2005/ (extract provenance_path; directory missing)",
    ],
    "kems-020-hastie-1981-nbsir": [
        f"{OCR_REPAIR}/kems-020-hastie-1981-nbsir/ (extract provenance_path; directory missing)",
    ],
    "kems-021-plante-1992-feo": [
        f"{OCR_REPAIR}/kems-021-plante-1992-feo/ (extract provenance_path; directory missing)",
    ],
    "kems-023-rammensee-1982": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-024-chastel-1987": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-025-mathieu": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-026-charles-1967": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-028-markova-1986": [
        "docs-private/RESUME-NOTES-2026-08-27.md (listed in the 8-OA wave; later confirmed-not-OA)",
    ],
    "kems-029": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-030": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-033-costa-2017": [
        "docs-private/RESUME-NOTES-2026-08-27.md (OA acquired 2026-08-27; PDF not on disk now)",
    ],
    "kems-035-sauerborn-2005": [
        f"{OCR_REPAIR}/kems-035-sauerborn-2005/ (extract provenance_path; directory missing)",
        f"{PAPERS_LIB}/sauerborn-2004-dlr-solar-furnace/source.pdf (papers library; present)",
    ],
    "kems-036-sesko-2024": [
        f"{OCR_REPAIR}/kems-036-sesko-2024/ (extract provenance_path; directory missing)",
        f"{PAPERS_LIB}/sesko-2022-thesis-solar-vapor-pyrolysis/source.pdf (papers library; present)",
    ],
    "kems-038-matchett-2006": [
        f"{OCR_REPAIR}/kems-038-matchett-2006/ (extract provenance_path; directory missing)",
        f"{PAPERS_LIB}/matchett-2006-vacuum-pyrolysis-thesis/source.pdf (papers library; present)",
    ],
    "kems-040-stolyarova-2015": [
        f"{OCR_REPAIR}/kems-040-stolyarova-2015/ (extract provenance_path; directory missing)",
    ],
    "kems-042-plante-1979": [
        "docs/references/pdfs/99-kems-langmuir/kems-042-nbs-sp561-v1-hastie-1979.pdf (cited by extract; ABSENT)",
    ],
    "kems-042-nbs-sp561-v1-hastie-1979": [
        "docs/references/pdfs/99-kems-langmuir/kems-042-nbs-sp561-v1-hastie-1979.pdf (cited by extract kems-042-plante-1979; ABSENT)",
    ],
    "smales-1971-lpsc-12022": [
        "docs/references/pdfs/99-kems-langmuir/smales-1971-lpsc-12022.pdf (main checkout, untracked; absent from this worktree)",
        f"{OCR_REPAIR}/smales-1971-lpsc-12022/tables.md (extract provenance_path; missing)",
    ],
    "stebbins-carmichael-weill-1983": [
        "docs/references/pdfs/02-thermochemistry/stebbins-carmichael-weill-1983.pdf (main checkout, untracked; absent from this worktree)",
        f"{OCR_REPAIR}/stebbins-carmichael-weill-1983/tables.md (extract provenance_path; missing)",
    ],
}

KNOWN_COPIES = {
    "kems-007-costa-2015": [
        f"{KEMS_CORPUS}/kems-007-costa-2015.pdf",
        f"{PAPERS_LIB}/costa-jacobson-2015-olivine-kems/source.pdf",
    ],
    "costa-jacobson-2015": [
        f"{PAPERS_LIB}/costa-jacobson-2015-olivine-kems/source.pdf",
    ],
    "kems-008-schaefer-fegley-2004": [
        f"{KEMS_CORPUS}/kems-008-schaefer-fegley-2004.pdf",
        f"{PAPERS_LIB}/schaefer-fegley-2004-io-lava/source.pdf",
    ],
    "kems-012-sossi-2019": [
        f"{KEMS_CORPUS}/kems-012-sossi-2019.pdf",
        f"{PAPERS_LIB}/sossi-2019-gca-hkl-evaporation/source.pdf",
    ],
    "sossi-et-al-2019": [
        f"{PAPERS_LIB}/sossi-2019-gca-hkl-evaporation/source.pdf",
    ],
    "kems-022-demaria-1971": [
        f"{KEMS_CORPUS}/kems-022-demaria-1971.pdf",
        f"{PAPERS_LIB}/demaria-1971-apollo-12022-kems/source.pdf",
    ],
    "kems-037-richter-2002": [
        f"{KEMS_CORPUS}/kems-037-richter-2002.pdf",
        f"{PAPERS_LIB}/richter-2002-gca-evaporation-isotope-frac/source.pdf",
    ],
    "kems-041-sossi-fegley-2018": [
        f"{KEMS_CORPUS}/kems-041-sossi-fegley-2018.pdf",
        f"{PAPERS_LIB}/sossi-fegley-2018-volatility/source.pdf",
    ],
    "sossi-fegley-2018": [
        f"{PAPERS_LIB}/sossi-fegley-2018-volatility/source.pdf",
    ],
    "oneill-eggins-2002": [
        "docs-private/research/2026-08-18-bench-candidates/pdfs/oneill-eggins-2002.pdf",
    ],
}

# Aug 27 campaign: 25 -> 43 KEMS PDFs. The 18 extra identities.
RECONCILIATION_18 = [
    ("kems-006-zhang-2021", "extract cites docs/references/pdfs/99-kems-langmuir/kems-006-zhang-2021.pdf (ABSENT); last seen " + OCR_REPAIR + "/kems-006-zhang-2021/ (missing)"),
    ("kems-014-drowart-2005", "last seen " + OCR_REPAIR + "/kems-014-drowart-2005/ (missing)"),
    ("kems-020-hastie-1981-nbsir", "last seen " + OCR_REPAIR + "/kems-020-hastie-1981-nbsir/ (missing)"),
    ("kems-021-plante-1992-feo", "last seen " + OCR_REPAIR + "/kems-021-plante-1992-feo/ (missing)"),
    ("kems-023-rammensee-1982", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-024-chastel-1987", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-025-mathieu", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-026-charles-1967", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-028-markova-1986", "listed in the 8-OA wave; later confirmed-not-OA"),
    ("kems-029", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-030", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-033-costa-2017", "OA wave 2026-08-27; PDF not on disk now"),
    ("kems-035-sauerborn-2005", "ocr-repair missing; papers-library copy at " + PAPERS_LIB + "/sauerborn-2004-dlr-solar-furnace/source.pdf"),
    ("kems-036-sesko-2024", "ocr-repair missing; papers-library copy at " + PAPERS_LIB + "/sesko-2022-thesis-solar-vapor-pyrolysis/source.pdf"),
    ("kems-038-matchett-2006", "ocr-repair missing; papers-library copy at " + PAPERS_LIB + "/matchett-2006-vacuum-pyrolysis-thesis/source.pdf"),
    ("kems-040-stolyarova-2015", "last seen " + OCR_REPAIR + "/kems-040-stolyarova-2015/ (missing)"),
    ("kems-042-plante-1979", "extract cites docs/references/pdfs/99-kems-langmuir/kems-042-nbs-sp561-v1-hastie-1979.pdf (ABSENT)"),
    ("smales-1971-lpsc-12022", "untracked pair at main docs/references/pdfs/99-kems-langmuir/smales-1971-lpsc-12022.pdf (absent from this worktree)"),
]

KNOWN_UNTRACKED = [
    "docs/references/pdfs/99-kems-langmuir/smales-1971-lpsc-12022.pdf",
    "docs/references/pdfs/99-kems-langmuir/smales-1971-lpsc-12022.md",
    "docs/references/pdfs/02-thermochemistry/stebbins-carmichael-weill-1983.pdf",
    "docs/references/pdfs/02-thermochemistry/stebbins-carmichael-weill-1983.md",
    "docs/references/pdfs/02-thermochemistry/README.md",
]

SIDECAR_SHA256 = {
    "smales-1971-lpsc-12022": "7af2b6b0f870ba5b92a3ec6f490449eb7c9fc2f20f069a8bab9a21762e014fec",
    "stebbins-carmichael-weill-1983": "5246cf5b6bfd79f16961aac8264757af04da1d308e5d6b860aac747a44b2d8d6",
}

KEMS_CORPUS_STEMS = {
    "kems-001-homma-1966",
    "kems-002-ohno-1967",
    "kems-003-pound-1972",
    "kems-005-fedkin-2006",
    "kems-007-costa-2015",
    "kems-008-schaefer-fegley-2004",
    "kems-009-safarian-2013",
    "kems-010-richter-2007",
    "kems-011-wetzel-gail-2013",
    "kems-012-sossi-2019",
    "kems-015-hashimoto-1983",
    "kems-016-stolyarova-1992",
    "kems-017-stolyarova-2013",
    "kems-018-stolyarova-2012",
    "kems-022-demaria-1971",
    "kems-027-plante-hastie-1983",
    "kems-031-halwax-2024",
    "kems-032-copland-jacobson-2010",
    "kems-037-richter-2002",
    "kems-041-sossi-fegley-2018",
    "kems-045-sossi-2018-pnas-cr",
}

PDF_STEM_ALIASES = {
    "kems-ms2000-044": "ms2000-044",
    "ms2000-044": "kems-ms2000-044",
}

KNOWN_CITATIONS = {
    "kems-045-sossi-2018-pnas-cr": (
        "Sossi, P. A., Moynier, F. & van Zuilen, K. (2018), "
        "PNAS 115, 10920–10925, DOI 10.1073/pnas.1809060115"
    ),
}

ROW_KEYS = (
    "source_id",
    "citation",
    "doi",
    "report_number",
    "identifier",
    "pdf_status",
    "pdf_path",
    "pdf_last_seen",
    "pdf_sha256",
    "pdf_tracked",
    "sidecar_path",
    "sidecar_missing_fields",
    "access_status",
    "extracts",
    "battery_datasets",
    "hunt_ids",
    "copies",
    "measurement_sets",
)

COMPILATION_ID_ALIASES = {
    "nist_janaf_4th": "janaf-4th",
    "nist_webbook_srd69": "nist-webbook",
    "nasa_cea": "nasa-cea-thermo",
    "ivtanthermo_glushko": "ivtan-mno-coo-thermo",
}

ACCESS_TO_ENUM = (
    (("open_public",), "OA"),
    (("copyrighted", "commercial_license", "subscription"), "paywalled"),
)


def posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_tracked(root: Path, rel: str) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.returncode == 0


def hunt_sort_key(hid: str):
    match = re.match(r"RH-(\d+)$", hid)
    return (int(match.group(1)) if match else 10**9, hid)


def parse_hunt_json(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text())
    hits: dict[str, set[str]] = defaultdict(set)
    pat_extract = re.compile(r"data/literature/extracts/([A-Za-z0-9_.-]+)\.yaml")
    pat_pdf = re.compile(r"docs/references/pdfs/[^\"'\s]+/([A-Za-z0-9_.-]+)\.pdf")
    pat_kems = re.compile(r"kems-[0-9]{3}-[A-Za-z0-9-]+")
    pat_other = re.compile(
        r"\b(ts1985|yam1983|ms2000-044|janaf-4th|nist-webbook|nasa-cea-thermo|"
        r"lamoreaux-hildenbrand-1984|stebbins-carmichael-weill-1983|"
        r"smales-1971-lpsc-12022|sf04-magma-companion-workbook)\b"
    )
    for hunt in data:
        hid = hunt.get("id")
        if not hid:
            continue
        blob = json.dumps(hunt)
        for match in pat_extract.finditer(blob):
            hits[match.group(1)].add(hid)
        for match in pat_pdf.finditer(blob):
            hits[match.group(1)].add(hid)
        for match in pat_kems.finditer(blob):
            hits[match.group(0)].add(hid)
        for match in pat_other.finditer(blob):
            hits[match.group(1)].add(hid)
    return {key: sorted(values, key=hunt_sort_key) for key, values in hits.items()}


def load_hunt_citations(root: Path) -> dict[str, list[str]]:
    env = os.environ.get("LITERATURE_HUNT_JSON", "").strip()
    candidates = [
        Path(env) if env else None,
        DEFAULT_HUNT_JSON,
        root / "docs-private/research/2026-09-05-owner-decisions-sweep/RESEARCH-HUNT.json",
        MAIN_CHECKOUT / "docs-private/research/2026-09-05-owner-decisions-sweep/RESEARCH-HUNT.json",
    ]
    for path in candidates:
        if path is not None and path.is_file():
            return parse_hunt_json(path)
    return {key: list(values) for key, values in HUNT_CITATIONS_FALLBACK.items()}


def map_compilation_access(raw: str) -> str:
    text = (raw or "").lower()
    for needles, label in ACCESS_TO_ENUM:
        if any(needle in text for needle in needles):
            return label
    return "unknown"


def doi_of(source: dict) -> str | None:
    doi = source.get("doi")
    if doi not in (None, "", "null", "none"):
        return str(doi).strip()
    citation = str(source.get("citation") or "")
    match = re.search(r"10\.\d{4,9}/[^\s,;]+", citation)
    if match:
        return match.group(0).rstrip(").,")
    return None


def report_number(source: dict) -> str | None:
    citation = " ".join(
        str(source.get(key) or "")
        for key in ("citation", "identifier", "url")
    )
    patterns = (
        r"NASA/[A-Z]+[—\-]\d{4}-\d+",
        r"NTRS\s+\d+",
        r"NBSIR\s+\d+-\d+",
        r"NBS Special Publication \d+",
        r"NIST Standard Reference Database \d+",
        r"ADA\d+",
        r"Monograph \d+",
    )
    for pattern in patterns:
        match = re.search(pattern, citation, re.I)
        if match:
            return match.group(0)
    return None


def parse_sidecar(path: Path) -> dict:
    text = path.read_text(errors="replace")
    fields = {key: None for key in SIDECAR_FIELDS}
    cite = re.search(r"\*\*Citation:\*\*\s*(.+)", text)
    if cite:
        fields["citation"] = cite.group(1).strip()
    doi = re.search(r"\b(?:DOI|doi):\s*([0-9.]+/[^\s]+)", text)
    if doi:
        fields["doi"] = doi.group(1).rstrip(".")
    sha = re.search(r"SHA-256\s+`([0-9a-f]{64})`", text, re.I)
    if sha:
        fields["sha256"] = sha.group(1)
    url = re.search(r"https?://[^\s)]+", text)
    if url:
        fields["retrieval_url"] = url.group(0).rstrip(").,")
    if re.search(r"\b(open archive|OA|CC BY|NASA ADS open|J-STAGE OA|NTRS open)\b", text, re.I):
        fields["license_or_oa_basis"] = "OA (sidecar prose)"
    date = re.search(r"retrieved(?: date)?:?\s*(\d{4}-\d{2}-\d{2})", text, re.I)
    if date:
        fields["retrieved_date"] = date.group(1)
    missing = [key for key, value in fields.items() if not value]
    fields["missing_fields"] = missing
    return fields


def load_extracts(root: Path) -> dict[str, dict]:
    extracts: dict[str, dict] = {}
    directory = root / "data/literature/extracts"
    if not directory.is_dir():
        return extracts
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        doc = yaml.safe_load(path.read_text()) or {}
        if doc.get("schema_version") != "literature_extract.v1":
            continue
        source_id = str(doc.get("source_id") or path.stem)
        rel = posix(path.relative_to(root))
        extracts.setdefault(source_id, {"source_id": source_id, "files": [], "doc": doc})
        extracts[source_id]["files"].append(
            {"path": rel, "review_status": doc.get("review_status") or "unknown", "filename_stem": path.stem}
        )
        if "doc" not in extracts[source_id] or path.stem == source_id:
            extracts[source_id]["doc"] = doc
    return extracts


def load_pdfs(root: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    directory = root / "docs/references/pdfs"
    if not directory.is_dir():
        return found
    for path in sorted(directory.rglob("*.pdf")):
        rel = posix(path.relative_to(root))
        stem = path.stem
        sidecar = path.with_suffix(".md")
        sidecar_rel = posix(sidecar.relative_to(root)) if sidecar.is_file() else None
        found[stem] = {
            "path": rel,
            "sha256": sha256_file(path),
            "tracked": git_tracked(root, rel),
            "sidecar_path": sidecar_rel,
            "sidecar": parse_sidecar(sidecar) if sidecar.is_file() else None,
        }
    return found


def load_access_status(root: Path) -> dict:
    path = root / "data/literature/compilations/access-status.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_measurement_sources(root: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = defaultdict(list)
    directory = root / "data/literature"
    if not directory.is_dir():
        return mapping
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        rel = posix(path.relative_to(root))
        sources = doc.get("sources")
        if isinstance(sources, dict):
            for name in sources:
                mapping[str(name)].append(rel)
        measurements = doc.get("measurements")
        if isinstance(measurements, dict):
            for body in measurements.values():
                if not isinstance(body, dict):
                    continue
                for field in ("source_id", "paper_id", "citation_id"):
                    if body.get(field):
                        mapping[str(body[field])].append(rel)
                paper = body.get("paper_citation")
                if isinstance(paper, dict) and paper.get("citation_id"):
                    mapping[str(paper["citation_id"])].append(rel)
    return {key: sorted(set(values)) for key, values in mapping.items()}


def load_preset_tokens(root: Path) -> list[tuple[str, str, dict]]:
    rows = []
    directory = root / "data/presets"
    if not directory.is_dir():
        return rows
    for path in sorted(directory.rglob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        rel = posix(path.relative_to(root))
        rows.append((rel, path.stem, doc if isinstance(doc, dict) else {}))
    return rows


def battery_datasets_for(root: Path, tokens: list[str], battery_text: str, presets: list[tuple[str, str, dict]]) -> list[str]:
    hits: set[str] = set()
    tokens = [tok for tok in tokens if tok]
    for tok in tokens:
        if tok in battery_text:
            hits.add("scripts/calibration_battery.py")
            break
    if any(tok.endswith(".yaml") and Path(tok).name.startswith("kems-") for tok in tokens):
        hits.add("extract-reproduction")
        hits.add("scripts/calibration_battery.py")
    vp_aliases = {
        "costa-jacobson-2015",
        "kems-007-costa-2015",
        "sossi-fegley-2018",
        "kems-041-sossi-fegley-2018",
        "schaefer-fegley-2004",
        "kems-008-schaefer-fegley-2004",
    }
    if any(tok in vp_aliases or "costa-jacobson-2015-olivine-kems" in tok for tok in tokens):
        if "vp30" in battery_text:
            hits.add("vp30")
    if any("demaria" in tok for tok in tokens) and "na-paired" in battery_text:
        hits.add("na-paired")
    for rel, stem, doc in presets:
        blob = rel + "\n" + stem + "\n" + yaml.safe_dump(doc, sort_keys=True)
        if any(tok in blob for tok in tokens):
            hits.add(rel)
            case_id = doc.get("case_id") or doc.get("paper_id") or doc.get("measurement_id") or stem
            hits.add(str(case_id))
    return sorted(hits)


def access_for(row: dict, compilations: dict, has_pdf: bool) -> str:
    source_id = row["source_id"]
    sources = compilations.get("sources") or {}
    for key, body in sources.items():
        alias = COMPILATION_ID_ALIASES.get(key, key.replace("_", "-"))
        if source_id in {key, alias} or alias == source_id:
            return map_compilation_access(str(body.get("access") or ""))
        if source_id in str(body.get("existing_path") or "") or source_id in str(body.get("harvested_path") or ""):
            return map_compilation_access(str(body.get("access") or ""))
    if has_pdf:
        return "held"
    sidecar = row.get("sidecar") or {}
    if sidecar.get("license_or_oa_basis"):
        return "OA"
    url = str((row.get("extract_source") or {}).get("url") or "")
    if any(part in url for part in ("jstage.jst.go.jp", "ntrs.nasa.gov", "nist.gov", "aanda.org", "arxiv.org")):
        return "OA"
    if source_id in KNOWN_LAST_SEEN and "OA acquired" in " ".join(KNOWN_LAST_SEEN[source_id]):
        return "OA"
    return "unknown"


def collect_source_ids(extracts, pdfs, compilations, measurements, presets) -> list[str]:
    ids: set[str] = set()
    ids.update(extracts)
    ids.update(pdfs)
    ids.update(KNOWN_LAST_SEEN)
    ids.update(KNOWN_COPIES)
    ids.update(COMPILATION_ID_ALIASES.values())
    for key in (compilations.get("sources") or {}):
        ids.add(COMPILATION_ID_ALIASES.get(key, key.replace("_", "-")))
    for key in measurements:
        ids.add(key)
    for _rel, _stem, doc in presets:
        for field in ("source_id", "paper_id", "paper_citation_id"):
            if doc.get(field):
                ids.add(str(doc[field]))
    drop = {key for key, alias in COMPILATION_ID_ALIASES.items() if alias in ids}
    return sorted(sid for sid in ids if sid not in drop)


def collision_groups(extracts: dict[str, dict], pdfs: dict[str, dict], all_ids: list[str] | None = None) -> list[dict]:
    by_doi: dict[str, set[str]] = defaultdict(set)
    by_stem: dict[str, set[str]] = defaultdict(set)
    for source_id, payload in extracts.items():
        doc = payload.get("doc") or {}
        doi = str((doc.get("source") or {}).get("doi") or "").strip().lower()
        if doi and doi not in {"null", "none"}:
            by_doi[doi].add(source_id)
        for item in payload.get("files") or []:
            if item["filename_stem"] != source_id:
                by_stem[source_id].add(item["filename_stem"])
                by_stem[item["filename_stem"]].add(source_id)
    # kems-NNN-rest vs unprefixed rest / contained rest
    ids = sorted(set(extracts) | set(pdfs))
    suffix_groups: dict[str, set[str]] = defaultdict(set)
    for source_id in ids:
        rest = re.sub(r"^kems-\d+-", "", source_id)
        if rest != source_id and rest:
            for other in ids:
                if other == source_id:
                    continue
                other_rest = re.sub(r"^kems-\d+-", "", other)
                if rest == other or rest == other_rest or rest in other or other_rest in source_id:
                    if min(len(rest), len(other_rest if other_rest else other)) >= 8:
                        suffix_groups[rest].update({source_id, other})
    collisions = []
    for doi, members in sorted(by_doi.items()):
        if len(members) > 1:
            collisions.append({"type": "same_doi", "key": doi, "source_ids": sorted(members)})
    for rest, members in sorted(suffix_groups.items()):
        if len(members) > 1:
            collisions.append({"type": "source_id_alias", "key": rest, "source_ids": sorted(members)})
    if {"kems-ms2000-044", "ms2000-044"} <= (set(extracts) | set(pdfs)):
        collisions.append(
            {"type": "filename_stem_mismatch", "key": "ms2000-044", "source_ids": ["kems-ms2000-044", "ms2000-044"]}
        )
    if {"kems-042-plante-1979", "kems-042-nbs-sp561-v1-hastie-1979"} <= (set(extracts) | set(pdfs) | set(KNOWN_LAST_SEEN)):
        collisions.append(
            {
                "type": "pdf_stem_vs_extract",
                "key": "kems-042",
                "source_ids": ["kems-042-plante-1979", "kems-042-nbs-sp561-v1-hastie-1979"],
            }
        )
    extra_pairs = (
        ("costa-2015", ["costa-jacobson-2015", "kems-007-costa-2015"]),
        ("safarian-2013", ["kems-009-safarian-2013", "safarian-engh-2013-si-pure-langmuir"]),
        ("schaefer-fegley-2004", ["kems-008-schaefer-fegley-2004", "sf04-magma-companion-workbook"]),
        ("halwax-2024", ["kems-031-halwax-2024", "halwax_sergeev_mueller_schenk_2024"]),
        ("sesko-2024", ["kems-036-sesko-2024", "sesko_2024"]),
    )
    present = set(extracts) | set(pdfs) | set(all_ids or [])
    for key, members in extra_pairs:
        have = [sid for sid in members if sid in present]
        if len(have) > 1:
            collisions.append({"type": "source_id_alias", "key": key, "source_ids": have})
    # Deduplicate identical member sets
    seen = set()
    unique = []
    for item in collisions:
        key = (item["type"], tuple(item["source_ids"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def build_index(root: Path) -> dict:
    root = root.resolve()
    extracts = load_extracts(root)
    pdfs = load_pdfs(root)
    compilations = load_access_status(root)
    measurements = load_measurement_sources(root)
    presets = load_preset_tokens(root)
    hunts = load_hunt_citations(root)
    battery_path = root / "scripts/calibration_battery.py"
    battery_text = battery_path.read_text() if battery_path.is_file() else ""

    source_ids = collect_source_ids(extracts, pdfs, compilations, measurements, presets)

    rows = []
    for source_id in source_ids:
        extract = extracts.get(source_id) or extracts.get(PDF_STEM_ALIASES.get(source_id, ""))
        pdf = pdfs.get(source_id) or pdfs.get(PDF_STEM_ALIASES.get(source_id, ""))
        doc = (extract or {}).get("doc") or {}
        src = dict(doc.get("source") or {})
        compilation = {}
        sources_block = compilations.get("sources") or {}
        for key, body in sources_block.items():
            alias = COMPILATION_ID_ALIASES.get(key, key.replace("_", "-"))
            if source_id in {key, alias} and isinstance(body, dict):
                compilation = body
                break
        citation = src.get("citation") or compilation.get("citation") or KNOWN_CITATIONS.get(source_id)
        doi = doi_of(src) or doi_of(compilation) or doi_of({"citation": citation or ""})
        measure_meta = None
        for rel in measurements.get(source_id) or []:
            measure_path = root / rel
            if not measure_path.is_file():
                continue
            measure_doc = yaml.safe_load(measure_path.read_text()) or {}
            body = (measure_doc.get("sources") or {}).get(source_id)
            if isinstance(body, dict):
                measure_meta = body
                break
        if measure_meta:
            citation = citation or measure_meta.get("citation")
            doi = doi or doi_of(measure_meta)
        if pdf and pdf.get("sidecar") and not citation:
            citation = pdf["sidecar"].get("citation")
            doi = doi or pdf["sidecar"].get("doi")
        identifier = doi or report_number(src) or report_number(compilation) or report_number({"citation": citation or ""})
        extract_files = []
        if extract:
            extract_files = [
                {"path": item["path"], "review_status": item["review_status"]}
                for item in extract["files"]
            ]
        pdf_status = "present" if pdf else "ABSENT"
        pdf_path = pdf["path"] if pdf else None
        sha = (pdf or {}).get("sha256") or SIDECAR_SHA256.get(source_id)
        last_seen = list(KNOWN_LAST_SEEN.get(source_id) or [])
        if pdf:
            last_seen = [pdf["path"]] + [item for item in last_seen if item != pdf["path"]]
        copies = sorted(set(KNOWN_COPIES.get(source_id) or []))
        if source_id in KEMS_CORPUS_STEMS:
            copies.append(f"{KEMS_CORPUS}/{source_id}.pdf")
            copies.append(f"{OCR_ART}/{source_id}/")
            copies = sorted(set(copies))
        sidecar_path = (pdf or {}).get("sidecar_path")
        sidecar_info = (pdf or {}).get("sidecar")
        if sidecar_path is None:
            # sibling expected even when PDF is absent
            guessed = None
            for candidate in (
                f"docs/references/pdfs/99-kems-langmuir/{source_id}.md",
                f"docs/references/pdfs/02-thermochemistry/{source_id}.md",
            ):
                if (root / candidate).is_file():
                    guessed = candidate
                    sidecar_info = parse_sidecar(root / candidate)
                    break
            sidecar_path = guessed
        missing_sidecar_fields = list(SIDECAR_FIELDS) if sidecar_info is None else list(sidecar_info.get("missing_fields") or [])
        tokens = [source_id]
        tokens.extend(item["path"] for item in extract_files)
        tokens.extend(Path(item["path"]).stem for item in extract_files)
        if pdf_path:
            tokens.append(pdf_path)
            tokens.append(Path(pdf_path).stem)
        tokens.extend(measurements.get(source_id) or [])
        battery = battery_datasets_for(root, tokens, battery_text, presets)
        for rel in measurements.get(source_id) or []:
            if rel not in battery:
                battery.append(rel)
        battery = sorted(set(battery))
        hunt_ids = sorted(set(hunts.get(source_id) or []), key=hunt_sort_key)
        # filename-stem hunt aliases
        if extract:
            for item in extract["files"]:
                hunt_ids = sorted(set(hunt_ids) | set(hunts.get(item["filename_stem"]) or []), key=hunt_sort_key)
        if source_id == "kems-ms2000-044":
            hunt_ids = sorted(set(hunt_ids) | set(hunts.get("ms2000-044") or []), key=hunt_sort_key)
        tracked = None
        if pdf_path:
            tracked = pdf.get("tracked")
        row = {
            "source_id": source_id,
            "citation": citation,
            "doi": doi,
            "report_number": report_number(src) or report_number(compilation) or report_number({"citation": citation or ""}),
            "identifier": identifier,
            "pdf_status": pdf_status,
            "pdf_path": pdf_path,
            "pdf_last_seen": last_seen if pdf_status == "ABSENT" else last_seen[:1],
            "pdf_sha256": sha,
            "pdf_tracked": tracked,
            "sidecar_path": sidecar_path,
            "sidecar_missing_fields": missing_sidecar_fields,
            "access_status": "unknown",
            "extracts": extract_files,
            "battery_datasets": battery,
            "hunt_ids": hunt_ids,
            "copies": copies,
            "measurement_sets": measurements.get(source_id) or [],
        }
        row["extract_source"] = src
        row["access_status"] = access_for(row, compilations, bool(pdf))
        del row["extract_source"]
        if not row["report_number"]:
            row["report_number"] = None
        rows.append(row)

    pdfs_without_extract = sorted(
        row["source_id"]
        for row in rows
        if row["pdf_status"] == "present" and not row["extracts"]
    )
    extracts_without_pdf = sorted(
        row["source_id"]
        for row in rows
        if row["extracts"] and row["pdf_status"] == "ABSENT"
    )
    untracked = []
    for row in rows:
        if row["pdf_path"] and row["pdf_tracked"] is False:
            untracked.append(row["pdf_path"])
        if row["sidecar_path"] and git_tracked(root, row["sidecar_path"]) is False:
            untracked.append(row["sidecar_path"])
    for rel in KNOWN_UNTRACKED:
        if rel not in untracked and not (root / rel).is_file():
            untracked.append(rel + " (main checkout; not in this worktree)")
        elif rel not in untracked and git_tracked(root, rel) is False:
            untracked.append(rel)
    untracked = sorted(set(untracked))
    sidecars_missing = []
    for row in rows:
        if row["pdf_status"] != "present" and not row["sidecar_path"]:
            continue
        if row["sidecar_missing_fields"]:
            sidecars_missing.append(
                {
                    "source_id": row["source_id"],
                    "sidecar_path": row["sidecar_path"],
                    "missing_fields": row["sidecar_missing_fields"],
                }
            )
    collisions = collision_groups(extracts, pdfs, source_ids)

    n_extracts = len({item["path"] for row in rows for item in row["extracts"]})
    n_pdfs_present = len({row["pdf_path"] for row in rows if row["pdf_status"] == "present" and row["pdf_path"]})
    index = {
        "schema_version": SCHEMA,
        "generated_by": "data/literature/build_index.py",
        "counts": {
            "sources": len(rows),
            "extracts": n_extracts,
            "pdfs_present_in_worktree": n_pdfs_present,
            "pdfs_tracked_99_kems_langmuir": len(
                {
                    row["pdf_path"]
                    for row in rows
                    if row["pdf_path"]
                    and str(row["pdf_path"]).startswith("docs/references/pdfs/99-kems-langmuir/")
                    and row["pdf_tracked"]
                }
            ),
            "pdfs_without_extract": len(pdfs_without_extract),
            "extracts_without_pdf": len(extracts_without_pdf),
        },
        "reconciliation_43_vs_25": {
            "tracked_99_kems_langmuir_git": 24,
            "main_checkout_99_kems_langmuir_including_untracked_smales": 25,
            "aug27_campaign_census": 43,
            "missing_from_tracked_dir": 18,
            "per_file": [{"source_id": sid, "last_seen": where} for sid, where in RECONCILIATION_18],
        },
        "gaps": {
            "pdfs_without_extract": pdfs_without_extract,
            "extracts_without_pdf": extracts_without_pdf,
            "untracked_files": untracked,
            "sidecars_missing_fields": sidecars_missing,
            "source_id_collisions": collisions,
        },
        "sources": rows,
    }
    return index


def render_md(index: dict) -> str:
    counts = index["counts"]
    recon = index["reconciliation_43_vs_25"]
    gaps = index["gaps"]
    lines = [
        "# Empirical literature index",
        "",
        "Generated by `data/literature/build_index.py`. Do not edit by hand.",
        "",
        "## Counts",
        "",
        f"- Sources: {counts['sources']}",
        f"- Extracts (`literature_extract.v1`): {counts['extracts']}",
        f"- PDFs present in this worktree: {counts['pdfs_present_in_worktree']}",
        f"- Tracked PDFs in `docs/references/pdfs/99-kems-langmuir/`: {counts['pdfs_tracked_99_kems_langmuir']}",
        f"- PDFs with no extract: {counts['pdfs_without_extract']}",
        f"- Extracts with no PDF: {counts['extracts_without_pdf']}",
        "",
        "## 43-vs-25 reconciliation",
        "",
        "An earlier census (RESUME-NOTES-2026-08-27 §13.1) reported **43 KEMS PDFs** at campaign close, up from **25** at the start of the 2026-08-27 corpus campaign. Git currently tracks **24** PDFs under `docs/references/pdfs/99-kems-langmuir/`. The main checkout adds the untracked `smales-1971-lpsc-12022` pair, which is the **25** in that folder. The other **18** never landed as tracked files in that directory.",
        "",
        "| source_id | last seen |",
        "|---|---|",
    ]
    for item in recon["per_file"]:
        lines.append(f"| `{item['source_id']}` | {item['last_seen']} |")
    lines.extend(
        [
            "",
            "Related but **not** one of those 18: untracked `docs/references/pdfs/02-thermochemistry/stebbins-carmichael-weill-1983.pdf` (calorimetry, not KEMS). Duplicate copies of already-tracked PDFs live in `docs-private/research/kems-corpus/pdfs/` (21 files) and in `docs-private/deep-research/literature/*/source.pdf` for Costa, De Maria, Schaefer–Fegley, Sossi 2019/2018, Richter 2002.",
            "",
            "## Sources",
            "",
            "| source_id | citation | DOI / report | PDF | sha256 | access | extract (review) | battery | hunts |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in index["sources"]:
        cite = (row.get("citation") or "").replace("|", "\\|")
        if len(cite) > 90:
            cite = cite[:87] + "..."
        ident = row.get("doi") or row.get("report_number") or row.get("identifier") or ""
        if row["pdf_status"] == "present":
            pdf = f"`{row['pdf_path']}`"
        elif row.get("pdf_last_seen"):
            pdf = "ABSENT; " + row["pdf_last_seen"][0].replace("|", "\\|")
        else:
            pdf = "ABSENT"
        sha = (row.get("pdf_sha256") or "")[:12]
        extracts = ", ".join(
            f"`{item['path']}` ({item['review_status']})" for item in row.get("extracts") or []
        ) or "—"
        battery = ", ".join(f"`{item}`" for item in row.get("battery_datasets") or []) or "—"
        hunts = ", ".join(row.get("hunt_ids") or []) or "—"
        lines.append(
            f"| `{row['source_id']}` | {cite} | {ident} | {pdf} | `{sha}` | {row.get('access_status')} | {extracts} | {battery} | {hunts} |"
        )
    lines.extend(["", "## Gap report", "", "### PDFs with no extract", ""])
    if gaps["pdfs_without_extract"]:
        for sid in gaps["pdfs_without_extract"]:
            lines.append(f"- `{sid}`")
    else:
        lines.append("(none)")
    lines.extend(["", "### Extracts with no PDF", ""])
    if gaps["extracts_without_pdf"]:
        for sid in gaps["extracts_without_pdf"]:
            lines.append(f"- `{sid}`")
    else:
        lines.append("(none)")
    lines.extend(["", "### Untracked files", ""])
    if gaps["untracked_files"]:
        for rel in gaps["untracked_files"]:
            lines.append(f"- `{rel}`")
    else:
        lines.append("(none)")
    lines.extend(["", "### Sidecars missing fields", ""])
    lines.append("Required sibling fields: citation, DOI, license/OA basis, sha256, retrieved date, retrieval URL.")
    lines.append("")
    for item in gaps["sidecars_missing_fields"]:
        path = item["sidecar_path"] or "(no sidecar)"
        missing = ", ".join(item["missing_fields"]) or "(complete)"
        lines.append(f"- `{item['source_id']}` — `{path}` — missing: {missing}")
    lines.extend(["", "### source_id naming collisions", ""])
    if gaps["source_id_collisions"]:
        for item in gaps["source_id_collisions"]:
            members = ", ".join(f"`{sid}`" for sid in item["source_ids"])
            lines.append(f"- {item['type']} `{item['key']}`: {members}")
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)


def ordered_row(row: dict) -> dict:
    return {key: row.get(key) for key in ROW_KEYS}


def dump_yaml(index: dict) -> str:
    payload = {
        "schema_version": index["schema_version"],
        "generated_by": index["generated_by"],
        "counts": index["counts"],
        "reconciliation_43_vs_25": index["reconciliation_43_vs_25"],
        "gaps": index["gaps"],
        "sources": [ordered_row(row) for row in index["sources"]],
    }
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )


def write_index(index: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "INDEX.yaml").write_text(dump_yaml(index))
    (out_dir / "INDEX.md").write_text(render_md(index))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Repository root")
    parser.add_argument("--out-dir", type=Path, default=None, help="Directory for INDEX.yaml and INDEX.md")
    args = parser.parse_args(argv)
    root = args.root or Path(__file__).resolve().parents[2]
    out_dir = args.out_dir or (root / "data/literature")
    index = build_index(root)
    write_index(index, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
