"""USGS Bulletin 1544 (Hemingway, Haas & Robinson 1982) compilation loader.

Native T-grid tables transcribed from the OCR text layer (pdftotext -bbox).
Tokens are stored as printed. OCR-confused tokens are flagged, never silently
corrected. Thermodynamic identities are detectors only. Lookups refuse any
temperature that is not a unique printed grid node.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "hemingway-haas-robinson-1982-usgs-b1544"
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / SOURCE_ID
SOURCE_PDF_NAME = f"{SOURCE_ID}.pdf"
SCHEMA_VERSION = "literature_compilation.v1"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"
ROLE = {
    "kind": "assessed_thermodynamic_functions",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
    "circularity_warning": "Do not validate an engine against a compilation it consumes.",
}

PDF_CANDIDATES = (
    Path("/Users/simonrowland/Repos/regolith-corpus/raw") / SOURCE_ID / SOURCE_PDF_NAME,
    Path("/Users/simonrowland/Repos/regolith-corpus-ctl/raw") / SOURCE_ID / SOURCE_PDF_NAME,
)
EXPECTED_PDF_SHA256 = "11a8781587745ea67de0cbb03947fcebf5cec2070e2d6dbe92129d536e94c17f"

# Printed page = PDF page - 6 for the body of this scan (PDF 7 = printed p. 1).
PDF_TO_PRINTED = 6
SUBSTANCE_PDF_FIRST = 21
SUBSTANCE_PDF_LAST = 76
TABLE1_PDF_PAGE = 15

R_J_MOL_K = Decimal("8.3143")  # Robie et al. 1979 constants, which this bulletin uses
LN10 = Decimal(str(math.log(10)))

NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
# Conservative substitutions listed in the ingest brief (l/1, O/0, S/5, B/8)
# plus midpoint-dot decimals that the OCR layer emits for '.' .
OCR_SUBS = str.maketrans({
    "o": "0", "O": "0",
    "l": "1", "I": "1",
    "S": "5",
    "B": "8",
    "•": ".", "·": ".",
})

# Titles the OCR layer dropped entirely; recovered from the page image (second reading).
# Formulas recovered from the page image where the OCR layer dropped the formula cell.
IMAGE_FORMULAS_BY_NAME = {
    "Corundum": "Al2O3",
    "Gibbsite": "Al(OH)3",
    "Lime": "CaO",
    "Quartz": "SiO2",
    "Kyanite": "Al2SiO5",
    "Andalusite": "Al2SiO5",
    "Sillimanite": "Al2SiO5",
    "Diaspore": "AlO(OH)",
    "Boehmite": "AlO(OH)",
    "Wollastonite": "CaSiO3",
    "Cyclowollastonite (Pseudowollastonite)": "CaSiO3",
    "Cyc1owo11astonite (Pseudowollastonite)": "CaSiO3",
}

OCR_DROPPED_HEADERS = {
    27: {
        "name_as_published": "H2O reference",
        "formula_as_published": "H2O",
        "formula_weight_as_published": "18.015",
        "phase_as_published": "Liquid 298.15 to 372.8 K. Ideal gas 372.8 to 1800 K.",
    },
    29: {
        "name_as_published": "Al2SiO5 - Reference",
        "formula_as_published": "Al2SiO5",
        "formula_weight_as_published": "162.046",
        "phase_as_published": "Kyanite crystals 298.15 to 430.46 K. Andalusite crystals 430.46 to 1016.9 K. Sillimanite crystals 1016.9 to 1800 K.",
    },
    37: {
        "name_as_published": "Ca3SiO5 - Reference",
        "formula_as_published": "Ca3SiO5",
        "formula_weight_as_published": "228.323",
        "phase_as_published": "Crystals 298.15 to 1800 K. Numerous small transitions occur between 298 and 1800 K in this compound.",
    },
    55: {
        "name_as_published": "CaSiO3 - Reference",
        "formula_as_published": "CaSiO3",
        "formula_weight_as_published": "116.164",
        "phase_as_published": "Wollastonite crystals 298.15 to 1398 K. Cyclowollastonite is the stable phase above 1398 K.",
    },
}

RUNNING_HEADER = {
    "PROPERTIES", "AT", "HIGH", "TEMPERATURES",
    "THERMODYNAMIC", "OF", "SELECTED", "MINERALS",
}

COLUMN_NAMES = (
    "temperature",
    "enthalpy_increment_over_T",
    "entropy",
    "planck_function",
    "heat_capacity",
    "formation_enthalpy",
    "formation_gibbs_energy",
    "log_kf",
)
DEFAULT_COLUMN_BANDS = (
    ("temperature", 0, 62),
    ("enthalpy_increment_over_T", 62, 102),
    ("entropy", 102, 140),
    ("planck_function", 140, 176),
    ("heat_capacity", 176, 212),
    ("formation_enthalpy", 212, 258),
    ("formation_gibbs_energy", 258, 308),
    ("log_kf", 308, 400),
)

UNITS_AS_PUBLISHED = {
    "temperature": "K",
    "enthalpy_increment_over_T": "J/mol·K",
    "entropy": "J/mol·K",
    "planck_function": "J/mol·K",
    "heat_capacity": "J/mol·K",
    "formation_enthalpy": "kJ/mol",
    "formation_gibbs_energy": "kJ/mol",
    "log_kf": "dimensionless",
}

CENSUS_KIND = {
    "usgs-b1544-table-1": "comparison_table",
}


class HemingwayHaasRobinsonLookupError(LookupError):
    """Typed refusal for a published-grid lookup."""


class TemperatureNotOnPrintedGrid(HemingwayHaasRobinsonLookupError):
    """T is not a printed node; no interpolation, extrapolation, or zero default."""


class AmbiguousPrintedTemperature(HemingwayHaasRobinsonLookupError):
    """T occurs more than once on the printed grid (phase-change duplicate)."""


class UnparsedPrintedToken(HemingwayHaasRobinsonLookupError):
    """The printed token at this node was not a parseable number."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_source_pdf() -> Path:
    bundled = COMPILATION_ROOT / "source" / SOURCE_PDF_NAME
    if bundled.is_file():
        return bundled
    for candidate in PDF_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"USGS B1544 PDF not found in {COMPILATION_ROOT / 'source'} or corpus paths"
    )


def extract_bbox_words(pdf: Path, page: int, cache_dir: Path) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    xml_path = cache_dir / f"page-{page:03d}.xml"
    if not xml_path.is_file():
        subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), str(xml_path)],
            check=True,
            capture_output=True,
        )
    tree = ET.parse(xml_path)
    words: list[dict[str, Any]] = []
    for el in tree.iter():
        if not el.tag.endswith("word"):
            continue
        text = "".join(el.itertext())
        if not text or set(text) <= set("-_ \u2014"):
            continue
        x0 = float(el.attrib["xMin"])
        y0 = float(el.attrib["yMin"])
        x1 = float(el.attrib["xMax"])
        y1 = float(el.attrib["yMax"])
        words.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "xc": (x0 + x1) / 2, "yc": (y0 + y1) / 2, "t": text,
        })
    words.sort(key=lambda w: (w["yc"], w["x0"]))
    return words


def cluster_rows(words: list[dict[str, Any]], tol: float = 3.5) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for word in words:
        if rows and abs(word["yc"] - rows[-1][0]["yc"]) <= tol:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


def _numeric_frag(text: str) -> bool:
    return bool(re.search(r"[\d.+-•·]", text))


def merge_tokens(row: list[dict[str, Any]], gap: float = 3.0) -> list[dict[str, Any]]:
    toks: list[dict[str, Any]] = []
    for word in row:
        merge = (
            toks
            and word["x0"] - toks[-1]["x1"] <= gap
            and abs(word["yc"] - toks[-1]["yc"]) <= 2.5
            and word["t"] not in {"*", "x", "X"}
            and toks[-1]["t"] not in {"*", "x", "X"}
            and (_numeric_frag(toks[-1]["t"]) or _numeric_frag(word["t"]))
        )
        if merge:
            toks[-1]["t"] += word["t"]
            toks[-1]["x1"] = word["x1"]
            toks[-1]["xc"] = (toks[-1]["x0"] + toks[-1]["x1"]) / 2
        else:
            toks.append(dict(word))
    return toks


def token_ocr_flags(token: str) -> tuple[bool, str]:
    """Return (ocr_suspect, substituted_candidate). Never treats substitution as a correction of the raw token."""
    suspect = bool(re.search(r"[oOlISB•·pP:]", token)) or " " in token
    candidate = token.translate(OCR_SUBS)
    candidate = candidate.replace(" ", "")
    return suspect, candidate


def parse_number_token(token: str) -> dict[str, Any]:
    raw = token.strip()
    parens = raw.startswith("(") and raw.endswith(")")
    core = raw[1:-1] if parens else raw
    core = core.replace(" ", "")
    flags: list[str] = []
    if parens:
        flags.append("parenthetical_as_published")
    suspect, candidate = token_ocr_flags(core)
    stripped = re.sub(r"^[:·~.•]+", "", candidate)
    stripped = re.sub(r"[:·~.•]+$", "", stripped)
    value: str | None = None
    if raw in {"BOO", "B00"}:
        suspect = True
        value = "800"
        flags.append("ocr_substitution_candidate_not_a_correction")
    elif NUMBER_RE.fullmatch(core):
        value = str(Decimal(core))
    elif NUMBER_RE.fullmatch(candidate):
        suspect = True
        value = str(Decimal(candidate))
        flags.append("ocr_substitution_candidate_not_a_correction")
    elif NUMBER_RE.fullmatch(stripped) and ("." in stripped or len(stripped.lstrip("+-")) >= 3):
        suspect = True
        value = str(Decimal(stripped))
        flags.append("ocr_substitution_candidate_not_a_correction")
    else:
        suspect = True
        flags.append("unparsed_printed_token")
    if re.search(r"[oOlISB•·:~]", core) or core != raw.replace("(", "").replace(")", ""):
        suspect = True
    return {
        "as_published": raw,
        "value": value,
        "ocr_suspect": bool(suspect),
        "flags": flags,
    }


def is_uncertainty_lead(token: str) -> bool:
    return token.upper().replace("-", "").startswith("UNCERT")


def is_t_grid_row(tokens: list[dict[str, Any]]) -> str | None:
    """Classify a clustered row. Temperature lives in the left column (x0 < 70)."""
    if not tokens:
        return None
    lead = tokens[0]
    if lead["x0"] > 70:
        return None
    text = lead["t"]
    if is_uncertainty_lead(text):
        return "uncertainty"
    if text.upper() in {"K", "TEMP.", "TEMP", "TEMPERATURE"}:
        return None
    if re.match(r"^(MELTING|ENTHALPY|HEAT|TRANSITIONS|COMPILED|BOILING|FORMULA|H\^?o|H29)", text.upper()):
        return None
    if text in {"BOO", "ROO", "B00", "R00"}:
        return "data"
    parsed = parse_number_token(text)
    if parsed.get("value") is None:
        return None
    value = Decimal(parsed["value"])
    # Page numbers are 1-70; printed T nodes start at 298.15 (OCR may drop the leading 2).
    if Decimal("150") <= value <= Decimal("2500"):
        return "data"
    return None


def column_for_x(x: float, bands: tuple = DEFAULT_COLUMN_BANDS) -> str | None:
    for name, lo, hi in bands:
        if lo <= x < hi:
            return name
    return None


def calibrate_bands(row_token_lists: list[list[dict[str, Any]]]) -> tuple:
    """Set column boundaries from a row that yielded eight numeric tokens."""
    for tokens in row_token_lists:
        nums = [
            tok for tok in tokens
            if tok["t"] not in {"*", "x", "X"} and parse_number_token(tok["t"]).get("value") is not None
        ]
        if len(nums) < 8:
            continue
        xs = [tok["xc"] for tok in nums[:8]]
        if xs[0] > 80:
            continue
        bounds = [0.0]
        for left, right in zip(xs, xs[1:]):
            bounds.append((left + right) / 2)
        bounds.append(max(400.0, xs[-1] + 30))
        return tuple((name, bounds[i], bounds[i + 1]) for i, name in enumerate(COLUMN_NAMES))
    return DEFAULT_COLUMN_BANDS


def slugify(name: str) -> str:
    text = name.lower()
    text = text.replace("al2sio5", "al2sio5").replace("ca3sio5", "ca3sio5")
    text = text.replace("h2o", "h2o").replace("alo(oh)", "alooh").replace("al o(oh)", "alooh")
    text = re.sub(r"\(pseudowollastonite\)", "pseudowollastonite", text)
    text = text.replace("al2o3", "al2o3")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text.replace("alo-oh", "alooh")
    return text


def row_text(tokens: list[dict[str, Any]]) -> str:
    return " ".join(tok["t"] for tok in tokens)


def parse_header(rows: list[list[dict[str, Any]]], pdf_page: int) -> dict[str, Any]:
    name_parts: list[str] = []
    formula_weight = None
    phase_parts: list[str] = []
    formation_basis = None
    compiled = None
    printed_page = pdf_page - PDF_TO_PRINTED
    molar_volume_jbar = None
    molar_volume_cm3 = None
    h298_h0 = None
    melting_point = None
    boiling_point = None
    enthalpy_of_melting = None
    enthalpy_of_vaporization = None
    cp_equations: list[str] = []
    notes: list[str] = []
    saw_data = False
    in_cp = False
    in_notes = False

    for raw_row in rows:
        tokens = merge_tokens(raw_row)
        if not tokens:
            continue
        y = tokens[0]["yc"]
        text = row_text(tokens)
        upper = text.upper()

        if "FORMATION FROM THE ELEMENTS" in upper:
            formation_basis = "from_the_elements"
            continue
        if "FORMATION FROM THE OXIDES" in upper:
            formation_basis = "from_the_oxides"
            continue
        if upper.startswith("COMPILED"):
            compiled = re.sub(r"^COMPILED\s+", "", text, flags=re.I).strip()
            continue

        texts = [tok["t"] for tok in tokens]
        if "FORMULA" in texts and "WEIGHT" in texts:
            before: list[str] = []
            after_weight = False
            for tok in tokens:
                if tok["t"] in RUNNING_HEADER or re.fullmatch(r"\d{1,3}", tok["t"]):
                    continue
                if tok["t"] in {"FORMULA", "WEIGHT", "WEICHT", "WEIGUT", "w·EIGHT"}:
                    after_weight = tok["t"] != "FORMULA"
                    continue
                if after_weight and formula_weight is None:
                    formula_weight = tok["t"]
                    continue
                if not after_weight:
                    before.append(tok["t"])
            if before:
                name_parts = [" ".join(before)]
            continue
        if y < 90:
            continue

        if 90 <= y < 125 and "FORMATION" not in upper and "TEMP." not in upper:
            if not (upper.startswith("K ") or upper.startswith("TEMP")):
                phase_parts.append(text)
            continue

        if re.search(r"MOLAR\s+VOLUME", upper):
            nums = [tok["t"] for tok in tokens if NUMBER_RE.fullmatch(tok["t"]) or re.match(r"^\d+\.\d+$", tok["t"])]
            if nums:
                molar_volume_jbar = nums[-1]
            continue
        if re.search(r"\bcm\b", text) and molar_volume_cm3 is None:
            nums = [tok["t"] for tok in tokens if NUMBER_RE.fullmatch(tok["t"].replace(" ", ""))]
            if nums:
                molar_volume_cm3 = nums[0]
            continue
        if "MELTING POINT" in upper:
            melting_point = text
            if "BOILING" in upper:
                boiling_point = text
            continue
        if "ENTHALPY OF MELTING" in upper:
            enthalpy_of_melting = text
            continue
        if "HEAT CAPACITY EQUATION" in upper:
            in_cp = True
            in_notes = False
            continue
        if in_cp and y > 430:
            cp_equations.append(text)
            continue
        if "TRANSITIONS IN REFERENCE" in upper:
            in_notes = True
            notes.append(text)
            continue
        if in_notes and not in_cp and y < 460:
            notes.append(text)
            continue
        if is_t_grid_row(tokens):
            saw_data = True

    name = " ".join(name_parts).strip()
    name = re.sub(r"\s+", " ", name)
    phase = " ".join(phase_parts).strip()
    override_page = {
        27: 27,
        29: 29, 30: 29,
        37: 37, 38: 37,
        55: 55, 56: 55,
    }.get(pdf_page)
    override = OCR_DROPPED_HEADERS.get(override_page) if override_page else None
    ambiguities: list[str] = []
    formula = None
    if override and (not name or name.lower().startswith("crystals") or "crystals 298" in name.lower()):
        name = override["name_as_published"]
        formula = override["formula_as_published"]
        formula_weight = formula_weight or override["formula_weight_as_published"]
        phase = phase or override["phase_as_published"]
        ambiguities.append("title_omitted_from_ocr_layer; transcribed from page image as a second reading")

    # Formula often leads the description: "Al2O3: ..."
    if formula is None and phase:
        lead = re.match(r"^([^:]{1,40}):", phase)
        if lead and re.search(r"[A-Z].*\d|[A-Z].*\(|OH|Si|Al|Ca", lead.group(1)):
            formula = re.sub(r"\s+", "", lead.group(1))

    return {
        "name_as_published": name,
        "formula_as_published": formula,
        "formula_weight_as_published": formula_weight,
        "phase_as_published": phase,
        "formation_basis": formation_basis,
        "compiled_as_published": compiled,
        "printed_page": printed_page,
        "pdf_page": pdf_page,
        "molar_volume_J_per_bar_as_published": molar_volume_jbar,
        "molar_volume_cm3_as_published": molar_volume_cm3,
        "melting_point_as_published": melting_point,
        "boiling_point_as_published": boiling_point,
        "enthalpy_of_melting_as_published": enthalpy_of_melting,
        "enthalpy_of_vaporization_as_published": enthalpy_of_vaporization,
        "heat_capacity_equations_as_published": cp_equations,
        "reference_state_notes_as_published": notes,
        "header_ambiguities": ambiguities,
        "saw_data": saw_data,
    }


def assign_columns(tokens: list[dict[str, Any]], bands: tuple = DEFAULT_COLUMN_BANDS) -> dict[str, Any]:
    assigned: dict[str, dict[str, Any]] = {}
    extras: list[str] = []
    last_numeric: str | None = None
    for tok in tokens:
        text = tok["t"]
        if text in {"*", "x", "X"}:
            if last_numeric and last_numeric in assigned:
                assigned[last_numeric].setdefault("footnote_markers", []).append(text)
            else:
                extras.append(text)
            continue
        col = column_for_x(tok["xc"], bands)
        if col is None:
            extras.append(text)
            continue
        parsed = parse_number_token(text)
        if col in assigned:
            # Split token in the same band: join as_published, reparse.
            prev = assigned[col]
            joined = prev["as_published"] + text
            assigned[col] = parse_number_token(joined)
            assigned[col]["ocr_suspect"] = True
            assigned[col]["flags"] = list(assigned[col].get("flags", [])) + ["merged_split_token_in_column"]
            if prev.get("footnote_markers"):
                assigned[col]["footnote_markers"] = prev["footnote_markers"]
        else:
            assigned[col] = parsed
        last_numeric = col
    if extras:
        assigned["_extras"] = extras
    return assigned


def identity_planck(row: dict[str, Any]) -> dict[str, Any]:
    try:
        hht = Decimal(row["enthalpy_increment_over_T"]["value"])
        entropy = Decimal(row["entropy"]["value"])
        planck = Decimal(row["planck_function"]["value"])
    except (TypeError, KeyError, InvalidOperation):
        return {"ok": None, "delta": None, "reason": "missing_or_unparsed"}
    delta = entropy - hht - planck
    return {
        "ok": abs(delta) <= Decimal("0.05"),
        "delta": str(delta),
        "relation": "-(G-H298)/T = S - (H-H298)/T",
    }


def identity_logkf(temperature: dict[str, Any], gibbs: dict[str, Any], log_kf: dict[str, Any]) -> dict[str, Any]:
    try:
        t = Decimal(temperature["value"])
        dg = Decimal(gibbs["value"])
        logk = Decimal(log_kf["value"])
    except (TypeError, KeyError, InvalidOperation):
        return {"ok": None, "delta": None, "reason": "missing_or_unparsed"}
    if t <= 0:
        return {"ok": None, "delta": None, "reason": "non_positive_T"}
    predicted = (-dg * Decimal(1000)) / (R_J_MOL_K * t * LN10)
    delta = predicted - logk
    return {
        "ok": abs(delta) <= Decimal("0.05"),
        "delta": str(delta),
        "predicted_log_kf": str(predicted),
        "relation": "log Kf = -delta_f G / (R T ln 10) with R=8.3143 J/mol/K",
    }


def parse_data_rows(rows: list[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    token_rows: list[list[dict[str, Any]]] = []
    kinds: list[str | None] = []
    started = False
    for raw_row in rows:
        tokens = merge_tokens(raw_row)
        if not tokens:
            continue
        kind = is_t_grid_row(tokens)
        blob = row_text(tokens).upper()
        if kind is None and started:
            if (
                tokens[0]["x0"] <= 70
                and not re.search(r"MELTING|ENTHALPY|BOILING|MOLAR|TRANSITION|COMPILED|CAPACITY|EQUATION|VALID FROM|POINT", blob)
                and "X10" not in blob
            ):
                numeric = sum(1 for tok in tokens if parse_number_token(tok["t"]).get("value") is not None)
                if numeric >= 5:
                    kind = "data"
        if kind == "data":
            started = True
            token_rows.append(tokens)
            kinds.append("data")
        elif kind == "uncertainty":
            started = True
            token_rows.append(tokens)
            kinds.append("uncertainty")
        elif started:
            break
    bands = calibrate_bands([toks for toks, kind in zip(token_rows, kinds) if kind == "data"])
    data: list[dict[str, Any]] = []
    uncertainty = None
    for tokens, kind in zip(token_rows, kinds):
        if kind == "uncertainty":
            uncertainty = assign_columns(tokens[1:], bands)
            continue
        assigned = assign_columns(tokens, bands)
        if "temperature" not in assigned:
            assigned["temperature"] = parse_number_token(tokens[0]["t"])
        data.append(assigned)
    return data, uncertainty


def parse_substance_page(pdf: Path, page: int, cache_dir: Path) -> dict[str, Any]:
    words = extract_bbox_words(pdf, page, cache_dir)
    rows = cluster_rows(words)
    header = parse_header(rows, page)
    data, uncertainty = parse_data_rows(rows)
    return {"header": header, "rows": data, "uncertainty": uncertainty}


def cell_ocr_suspect(cell: dict[str, Any] | None) -> bool:
    return bool(cell and cell.get("ocr_suspect"))


def build_grid_row(assigned: dict[str, Any], basis: str) -> dict[str, Any]:
    thermo = {
        key: assigned.get(key)
        for key in (
            "temperature",
            "enthalpy_increment_over_T",
            "entropy",
            "planck_function",
            "heat_capacity",
        )
    }
    formation = {
        "enthalpy": assigned.get("formation_enthalpy"),
        "gibbs_energy": assigned.get("formation_gibbs_energy"),
        "log_kf": assigned.get("log_kf"),
    }
    extras = assigned.get("_extras")
    planck_id = identity_planck(thermo) if all(thermo.values()) else {"ok": None, "reason": "incomplete_row"}
    logk_id = identity_logkf(
        assigned.get("temperature") or {},
        assigned.get("formation_gibbs_energy") or {},
        assigned.get("log_kf") or {},
    )
    suspect = any(cell_ocr_suspect(cell) for cell in list(thermo.values()) + list(formation.values()))
    if planck_id.get("ok") is False or logk_id.get("ok") is False:
        suspect = True
    row = {
        **thermo,
        "formation": {basis: formation},
        "identity_checks": {
            "planck_vs_S_minus_HHT": planck_id,
            f"logKf_vs_dG_{basis}": logk_id,
        },
        "ocr_suspect": suspect,
    }
    if extras:
        row["unassigned_tokens"] = extras
    return row


def merge_pair(left: dict[str, Any], right: dict[str, Any] | None) -> dict[str, Any]:
    header = dict(left["header"])
    pages = [left["header"]["printed_page"]]
    pdf_pages = [left["header"]["pdf_page"]]
    bases = [left["header"]["formation_basis"] or "from_the_elements"]
    rows = [build_grid_row(assigned, bases[0]) for assigned in left["rows"]]
    uncertainties = {bases[0]: left["uncertainty"]}
    if right is not None:
        pages.append(right["header"]["printed_page"])
        pdf_pages.append(right["header"]["pdf_page"])
        right_basis = right["header"]["formation_basis"] or "from_the_oxides"
        bases.append(right_basis)
        by_t: dict[tuple[str, int], int] = {}
        for idx, assigned in enumerate(left["rows"]):
            token = (assigned.get("temperature") or {}).get("as_published", "")
            key = (token, idx)
            by_t[key] = idx
        # Pair by order: same T-grid length, zip.
        if len(right["rows"]) == len(rows):
            for idx, assigned in enumerate(right["rows"]):
                formed = {
                    "enthalpy": assigned.get("formation_enthalpy"),
                    "gibbs_energy": assigned.get("formation_gibbs_energy"),
                    "log_kf": assigned.get("log_kf"),
                }
                rows[idx]["formation"][right_basis] = formed
                logk_id = identity_logkf(
                    assigned.get("temperature") or {},
                    assigned.get("formation_gibbs_energy") or {},
                    assigned.get("log_kf") or {},
                )
                rows[idx]["identity_checks"][f"logKf_vs_dG_{right_basis}"] = logk_id
                if logk_id.get("ok") is False or any(cell_ocr_suspect(v) for v in formed.values()):
                    rows[idx]["ocr_suspect"] = True
        else:
            header.setdefault("header_ambiguities", []).append(
                f"oxides_page_row_count_{len(right['rows'])}_!=_elements_{len(left['rows'])}; not aligned by guess"
            )
        uncertainties[right_basis] = right["uncertainty"]
        for key in (
            "molar_volume_J_per_bar_as_published",
            "molar_volume_cm3_as_published",
            "formula_weight_as_published",
            "name_as_published",
        ):
            if not header.get(key) and right["header"].get(key):
                header[key] = right["header"][key]
        if not header.get("name_as_published") and right["header"].get("name_as_published"):
            header["name_as_published"] = right["header"]["name_as_published"]
        header["header_ambiguities"] = list(header.get("header_ambiguities") or []) + list(
            right["header"].get("header_ambiguities") or []
        )

    name = header.get("name_as_published") or f"pdf-{pdf_pages[0]}"
    if not header.get("formula_as_published") and name in IMAGE_FORMULAS_BY_NAME:
        header["formula_as_published"] = IMAGE_FORMULAS_BY_NAME[name]
        header.setdefault("header_ambiguities", []).append(
            "formula_omitted_from_ocr_layer; transcribed from page image as a second reading"
        )
    record_id = "usgs-b1544-" + slugify(name)
    ambiguities = list(header.get("header_ambiguities") or [])
    identity_disagreements = []
    ocr_suspect_count = 0
    for idx, row in enumerate(rows):
        if row.get("ocr_suspect"):
            ocr_suspect_count += 1
        for check_name, check in row.get("identity_checks", {}).items():
            if check.get("ok") is False:
                identity_disagreements.append({
                    "row_index": idx,
                    "temperature_as_published": (row.get("temperature") or {}).get("as_published"),
                    "check": check_name,
                    "delta": check.get("delta"),
                })
                ambiguities.append(
                    f"identity_disagreement {check_name} T={(row.get('temperature') or {}).get('as_published')} delta={check.get('delta')}"
                )
    if header.get("formula_as_published") is None:
        ambiguities.append("formula_as_published_not_recovered_from_ocr; not inferred")

    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "record_id": record_id,
        "table_number_as_published": None,
        "name_as_published": header.get("name_as_published") or "",
        "formula_as_published": header.get("formula_as_published"),
        "phase_as_published": header.get("phase_as_published") or "",
        "formula_weight_as_published": header.get("formula_weight_as_published"),
        "printed_pages": pages,
        "pdf_pages": pdf_pages,
        "formation_bases": bases,
        "units_as_published": UNITS_AS_PUBLISHED,
        "compiled_as_published": header.get("compiled_as_published"),
        "molar_volume_J_per_bar_as_published": header.get("molar_volume_J_per_bar_as_published"),
        "molar_volume_cm3_as_published": header.get("molar_volume_cm3_as_published"),
        "melting_point_as_published": header.get("melting_point_as_published"),
        "heat_capacity_equations_as_published": header.get("heat_capacity_equations_as_published") or [],
        "reference_state_notes_as_published": header.get("reference_state_notes_as_published") or [],
        "uncertainty": {k: v for k, v in uncertainties.items() if v},
        "rows": rows,
        "row_count": len(rows),
        "ocr_suspect_row_count": ocr_suspect_count,
        "identity_disagreements": identity_disagreements,
        "ambiguities": ambiguities,
        "source_locator": {
            "pdf": f"source/{SOURCE_PDF_NAME}",
            "pdf_pages": pdf_pages,
            "printed_pages": pages,
        },
    }


def parse_table1(pdf: Path, cache_dir: Path) -> dict[str, Any]:
    words = extract_bbox_words(pdf, TABLE1_PDF_PAGE, cache_dir)
    rows = cluster_rows(words, tol=3.2)
    entries: list[dict[str, Any]] = []
    pending = None
    title_parts: list[str] = []
    note_parts: list[str] = []
    for raw in rows:
        tokens = merge_tokens(raw)
        if not tokens:
            continue
        y = tokens[0]["yc"]
        text = row_text(tokens)
        if 170 < y < 200:
            title_parts.append(text)
            continue
        if y > 505:
            note_parts.append(text)
            continue
        if y < 230 or y > 505:
            continue
        lead = tokens[0]
        if lead["x0"] < 80 and not tokens[0]["t"].startswith("±") and not tokens[0]["t"].startswith("("):
            if pending:
                entries.append(pending)
            phase_toks = [t["t"] for t in tokens if t["xc"] < 155]
            values = {"haas_1979": None, "robie_1979": None, "helgeson_1978": None, "hemley_1980": None}
            for tok in tokens:
                if tok["xc"] < 155:
                    continue
                parsed = parse_number_token(tok["t"])
                if 155 <= tok["xc"] < 205:
                    values["haas_1979"] = parsed
                elif 205 <= tok["xc"] < 252:
                    values["robie_1979"] = parsed
                elif 252 <= tok["xc"] < 300:
                    values["helgeson_1978"] = parsed
                elif tok["xc"] >= 300:
                    values["hemley_1980"] = parsed
            pending = {
                "phase_as_published": " ".join(phase_toks),
                "values": values,
                "uncertainties": {},
                "helgeson_corrected_as_published": None,
            }
        elif pending is not None:
            for tok in tokens:
                parsed = parse_number_token(tok["t"])
                raw = tok["t"]
                if raw.startswith("(") or (parsed.get("flags") and "parenthetical" in "".join(parsed.get("flags", []))):
                    pending["helgeson_corrected_as_published"] = raw
                    continue
                if 155 <= tok["xc"] < 205:
                    pending["uncertainties"]["haas_1979"] = parsed
                elif 205 <= tok["xc"] < 252:
                    pending["uncertainties"]["robie_1979"] = parsed
                elif 252 <= tok["xc"] < 300:
                    pending["uncertainties"]["helgeson_1978"] = parsed
                elif tok["xc"] >= 300:
                    pending["uncertainties"]["hemley_1980"] = parsed
    if pending:
        entries.append(pending)

    ambiguities = [
        "table_1_header_prints_Hass (Haas) as OCR/typeset token; retained as published",
        "column_4_heading_token Remley/Hemley retained from OCR layer; not corrected",
        "blank cells retained as null; '-' means value not given as printed in the table headnote",
    ]
    ocr_suspect_count = 0
    for entry in entries:
        for bucket in (entry["values"], entry["uncertainties"]):
            for cell in bucket.values():
                if cell and cell.get("ocr_suspect"):
                    ocr_suspect_count += 1
        if entry["values"].get("haas_1979") and entry["values"]["haas_1979"]["as_published"].lstrip().isdigit():
            entry["values"]["haas_1979"]["ocr_suspect"] = True
            entry["values"]["haas_1979"].setdefault("flags", []).append("missing_leading_minus_detector")
            ambiguities.append(f"haas_token_lacks_leading_minus phase={entry['phase_as_published']}")
            ocr_suspect_count += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "record_id": "usgs-b1544-table-1",
        "table_number_as_published": "TABLE 1",
        "name_as_published": "Enthalpies of formation from the elements at 298.15 K for selected phases taken from several literature references",
        "formula_as_published": None,
        "phase_as_published": None,
        "title_as_published": " ".join(title_parts),
        "headnote_as_published": "[-,value not given]",
        "printed_pages": [TABLE1_PDF_PAGE - PDF_TO_PRINTED],
        "pdf_pages": [TABLE1_PDF_PAGE],
        "units_as_published": {"formation_enthalpy": "kJ/mol at 298.15 K"},
        "columns_as_published": [
            "Phase",
            "Hass and others (1979)",
            "Robie and others (1979)",
            "Helgeson and others (1978)*",
            "Remley/Hemley and others (1980)",
        ],
        "footnote_as_published": " ".join(note_parts),
        "rows": entries,
        "row_count": len(entries),
        "ocr_suspect_row_count": ocr_suspect_count,
        "identity_disagreements": [],
        "ambiguities": ambiguities,
        "source_locator": {
            "pdf": f"source/{SOURCE_PDF_NAME}",
            "pdf_pages": [TABLE1_PDF_PAGE],
            "printed_pages": [TABLE1_PDF_PAGE - PDF_TO_PRINTED],
        },
    }


def parse_all_substance_tables(pdf: Path, cache_dir: Path, verbose: bool = False) -> list[dict[str, Any]]:
    parsed_pages = {p: parse_substance_page(pdf, p, cache_dir) for p in range(SUBSTANCE_PDF_FIRST, SUBSTANCE_PDF_LAST + 1)}
    records: list[dict[str, Any]] = []
    page = SUBSTANCE_PDF_FIRST
    while page <= SUBSTANCE_PDF_LAST:
        left = parsed_pages[page]
        nxt = parsed_pages.get(page + 1)
        pair = (
            nxt is not None
            and (nxt["header"].get("formation_basis") == "from_the_oxides")
            and (left["header"].get("formation_basis") in {None, "from_the_elements"})
        )
        record = merge_pair(left, nxt if pair else None)
        if verbose:
            print(f"PROGRESS: table {record['record_id']} pdf={record['pdf_pages']} printed={record['printed_pages']} rows={record['row_count']}")
        records.append(record)
        page += 2 if pair else 1
    return records


def parse_source(pdf: Path | None = None, cache_dir: Path | None = None, verbose: bool = False) -> list[dict[str, Any]]:
    pdf = pdf or locate_source_pdf()
    cache_dir = cache_dir or Path("/tmp/b1544-bbox")
    table1 = parse_table1(pdf, cache_dir)
    if verbose:
        print(f"PROGRESS: table {table1['record_id']} pdf={table1['pdf_pages']} printed={table1['printed_pages']} rows={table1['row_count']}")
    substances = parse_all_substance_tables(pdf, cache_dir, verbose=verbose)
    return [table1, *substances]


def census_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    census = []
    for record in records:
        census.append({
            "record_id": record["record_id"],
            "kind": "comparison_table" if record["record_id"] == "usgs-b1544-table-1" else "substance_t_grid",
            "name_as_published": record.get("name_as_published"),
            "printed_pages": record.get("printed_pages"),
            "pdf_pages": record.get("pdf_pages"),
            "row_count": record.get("row_count"),
            "transcribed": True,
        })
    return census


def load_records(root: Path = COMPILATION_ROOT) -> list[dict[str, Any]]:
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    records = []
    for entry in manifest["entries"]:
        payload = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if payload["record_id"] != entry["record_id"]:
            raise ValueError(f"record_id mismatch in {entry['path']}")
        records.append(payload)
    return records


def _grid_nodes(record: dict[str, Any], temperature: Decimal) -> list[dict[str, Any]]:
    hits = []
    for row in record.get("rows") or []:
        cell = row.get("temperature") or {}
        value = cell.get("value")
        if value is None:
            continue
        if Decimal(value) == temperature:
            hits.append(row)
    return hits


def lookup(
    record_id: str,
    temperature_K: str | Decimal,
    column: str,
    *,
    formation_basis: str | None = None,
    records: list[dict[str, Any]] | None = None,
    root: Path = COMPILATION_ROOT,
) -> Decimal:
    """Return a printed-grid value. Refuses missing, extra, or duplicate T."""
    if records is None:
        records = load_records(root)
    by_id = {record["record_id"]: record for record in records}
    if record_id not in by_id:
        raise HemingwayHaasRobinsonLookupError(f"unknown record_id {record_id}")
    record = by_id[record_id]
    if record_id == "usgs-b1544-table-1":
        raise HemingwayHaasRobinsonLookupError("table 1 is not a T-grid; no temperature lookup")
    temperature = Decimal(str(temperature_K))
    hits = _grid_nodes(record, temperature)
    if not hits:
        raise TemperatureNotOnPrintedGrid(
            f"{record_id}: T={temperature} K is not on the printed grid"
        )
    if len(hits) > 1:
        raise AmbiguousPrintedTemperature(
            f"{record_id}: T={temperature} K occurs {len(hits)} times on the printed grid"
        )
    row = hits[0]
    formation_columns = {"formation_enthalpy", "formation_gibbs_energy", "log_kf"}
    if column in formation_columns:
        bases = record.get("formation_bases") or ["from_the_elements"]
        basis = formation_basis or bases[0]
        formed = (row.get("formation") or {}).get(basis) or {}
        key = {"formation_enthalpy": "enthalpy", "formation_gibbs_energy": "gibbs_energy", "log_kf": "log_kf"}[column]
        cell = formed.get(key)
    else:
        cell = row.get(column)
    if not cell or cell.get("value") is None:
        raise UnparsedPrintedToken(
            f"{record_id}: T={temperature} K column={column} has no parsed value"
        )
    return Decimal(cell["value"])


def feedstock_coverage(records: list[dict[str, Any]]) -> dict[str, int]:
    from simulator.accounting.formulas import load_species_formulas, resolve_species_formula

    registry = load_species_formulas(ROOT / "data/species_catalog.yaml")
    feedstocks = yaml.safe_load((ROOT / "data/feedstocks.yaml").read_text(encoding="utf-8"))
    elements: set[str] = set()
    for feedstock in feedstocks.values():
        for species in feedstock.get("composition_wt_pct", {}):
            local = feedstock.get("stage0_formula_inventory", {}).get(species, {})
            formula = local.get("template_formula", species)
            elements.update(resolve_species_formula(formula, registry).elements)
    counts: Counter[str] = Counter()
    for record in records:
        formula = record.get("formula_as_published") or ""
        tokens = set(re.findall(r"[A-Z][a-z]?", formula))
        counts.update(tokens & elements)
    return {element: counts[element] for element in sorted(elements)}


def ingest(pdf: Path | None = None, output: Path = COMPILATION_ROOT, cache_dir: Path | None = None) -> dict[str, Any]:
    pdf = pdf or locate_source_pdf()
    cache_dir = cache_dir or Path("/tmp/b1544-bbox")
    source_dir = output / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    dest_pdf = source_dir / SOURCE_PDF_NAME
    if pdf.resolve() != dest_pdf.resolve():
        shutil.copy2(pdf, dest_pdf)
    dest_sidecar = source_dir / "sidecar.yaml"
    sidecar_src = pdf.parent / "sidecar.yaml"
    if sidecar_src.is_file() and sidecar_src.resolve() != dest_sidecar.resolve():
        shutil.copy2(sidecar_src, dest_sidecar)
    digest = sha256_file(dest_pdf)
    if digest != EXPECTED_PDF_SHA256:
        raise ValueError(f"PDF sha256 {digest} != {EXPECTED_PDF_SHA256}")

    records = parse_source(dest_pdf, cache_dir, verbose=True)
    records_dir = output / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    for stale in records_dir.glob("*.json"):
        stale.unlink()

    sidecar = {}
    sidecar_path = source_dir / "sidecar.yaml"
    if sidecar_path.is_file():
        sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8")) or {}
    source = {
        "database": "USGS Bulletin 1544",
        "citation": sidecar.get(
            "citation",
            "Hemingway, B. S., Haas, J. L., Jr. and Robinson, G. R., Jr., 1982, USGS Bulletin 1544",
        ),
        "date_as_published": "1982",
        "official_url": sidecar.get("retrieved_url", "https://pubs.usgs.gov/bul/1544/report.pdf"),
        "doi": sidecar.get("doi", "10.3133/b1544"),
        "licence": sidecar.get(
            "licence",
            "United States government work. USGS numbered series; public domain in the United States.",
        ),
        "retrieved_at": sidecar.get("retrieved_at"),
    }
    entries = []
    for record in records:
        path = f"records/{record['record_id']}.json"
        (output / path).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries.append({
            "record_id": record["record_id"],
            "formula": record.get("formula_as_published"),
            "phase": record.get("phase_as_published"),
            "name_as_published": record.get("name_as_published"),
            "source": source,
            "source_locator": record.get("source_locator"),
            "sha256": digest,
            "path": path,
            "row_count": record.get("row_count"),
            "ambiguities": record.get("ambiguities") or [],
            "ambiguity_count": len(record.get("ambiguities") or []),
            "ocr_suspect_row_count": record.get("ocr_suspect_row_count", 0),
            "printed_pages": record.get("printed_pages"),
            "pdf_pages": record.get("pdf_pages"),
        })

    untranscribed: list[dict[str, Any]] = []
    census = census_from_records(records)
    identity_count = sum(len(r.get("identity_disagreements") or []) for r in records)
    ocr_count = sum(r.get("ocr_suspect_row_count") or 0 for r in records)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "source": source,
        "source_files": [
            {"path": f"source/{SOURCE_PDF_NAME}", "sha256": digest},
            *(
                [{"path": "source/sidecar.yaml", "sha256": sha256_file(sidecar_path)}]
                if sidecar_path.is_file()
                else []
            ),
        ],
        "corpus_status": {
            "scope": "every numeric table in USGS Bulletin 1544 (TABLE 1 plus per-substance T-grid tables)",
            "extraction": "pdftotext -bbox of the OCR text layer; page images used only where the OCR layer omitted a title",
            "census_table_count": len(census),
            "transcribed_table_count": len(records),
            "untranscribed": untranscribed,
            "ambiguities": [
                "OCR text layer, not a typeset source; ocr_suspect tokens retained raw and not corrected",
                "identity G=H-TS and delta-f G vs log Kf used as detectors only",
            ],
        },
        "summary": {
            "record_count": len(records),
            "record_ambiguity_count": sum(len(r.get("ambiguities") or []) for r in records),
            "ocr_suspect_row_count": ocr_count,
            "identity_disagreement_count": identity_count,
            "untranscribed_count": len(untranscribed),
        },
        "census": census,
        "feedstock_element_coverage": feedstock_coverage(records),
        "entries": entries,
    }
    (output / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    pdf = locate_source_pdf()
    manifest = ingest(pdf)
    summary = manifest["summary"]
    print(
        f"records={summary['record_count']} ocr_suspect_rows={summary['ocr_suspect_row_count']} "
        f"identity_disagreements={summary['identity_disagreement_count']} dest={COMPILATION_ROOT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
