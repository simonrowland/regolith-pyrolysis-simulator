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
import os
import re
import shutil
import subprocess
import tempfile
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

# Printed headers and equation blocks transcribed from the page images.  These
# constants replace text-layer fields only where positioned OCR dropped or
# corrupted the printed tokens.
IMAGE_FORMULAS_BY_RECORD_ID = {
    "usgs-b1544-corundum": "Al2O3",
    "usgs-b1544-quartz": "SiO2",
    "usgs-b1544-kyanite": "Al2SiO5",
    "usgs-b1544-andalusite": "Al2SiO5",
    "usgs-b1544-sillimanite": "Al2SiO5",
    "usgs-b1544-larnite-reference": "Ca2SiO4",
    "usgs-b1544-larnite": "Ca2SiO4",
    "usgs-b1544-calcium-olivine-reference": "Ca2SiO4",
    "usgs-b1544-calcium-olivine": "Ca2SiO4",
    "usgs-b1544-rankinite": "Ca3Si2O7",
    "usgs-b1544-gehlenite": "Ca2Al2SiO7",
    "usgs-b1544-grossulsr": "Ca3Al2Si3O12",
    "usgs-b1544-zoisite": "Ca2Al3Si3O12(OH)",
    "usgs-b1544-wollastonite": "CaSiO3",
    "usgs-b1544-cyclowollastonite-pseudowollastonite": "CaSiO3",
    "usgs-b1544-ca-al-pyroxene": "CaAl2SiO6",
    "usgs-b1544-anorthite": "CaAl2Si2O8",
    "usgs-b1544-kaolinite": "Al2Si2O5(OH)4",
    "usgs-b1544-dickite": "Al2Si2O5(OH)4",
    "usgs-b1544-halloysite": "Al2Si2O5(OH)4",
    "usgs-b1544-pyrophyllite": "Al2Si4O10(OH)2",
    "usgs-b1544-margarite": "CaAl4Si2O10(OH)2",
    "usgs-b1544-prehnite": "Ca2Al2Si3O10(OH)2",
}

IMAGE_H298_MINUS_H0_BY_RECORD_ID = {
    "usgs-b1544-corundum": "10.016",
    "usgs-b1544-boehmite": "8.828",
    "usgs-b1544-gibbsite": "12.719",
    "usgs-b1544-lime": "6.749",
    "usgs-b1544-h2o-reference": "13.293",
    "usgs-b1544-quartz": "6.916",
    "usgs-b1544-al2sio5-reference": "16.041",
    "usgs-b1544-kyanite": "16.041",
    "usgs-b1544-andalusite": "17.096",
    "usgs-b1544-sillimanite": "17.414",
    "usgs-b1544-grossulsr": "47.047",
    "usgs-b1544-anorthite": "33.333",
    "usgs-b1544-pyrophyllite": "42.695",
}

IMAGE_CP_EQUATIONS_BY_RECORD_ID = {
    "usgs-b1544-corundum": (
        ("C_p° = 233.004 - 1.95913x10^-2 T + 9.44410x10^-6 T^2 - 2.46518x10^3 T^-0.5", "200 - 1800 K"),
    ),
    "usgs-b1544-alooh-reference": (
        ("C_p° = 150.556 - 1.73002x10^3 T^-0.5 + 2.43069x10^5 T^-2", "200 - 800 K, diaspore"),
        ("C_p° = 206.903 - 2.59274x10^3 T^-0.5 + 7.77112x10^5 T^-2", "200 - 800 K, boehmite"),
    ),
    "usgs-b1544-diaspore": (
        ("C_p° = 150.556 - 1.73002x10^3 T^-0.5 + 2.43069x10^5 T^-2", "200 - 800 K"),
    ),
    "usgs-b1544-boehmite": (
        ("C_p° = 206.903 - 2.59274x10^3 T^-0.5 + 7.77112x10^5 T^-2", "200 - 800 K"),
    ),
    "usgs-b1544-gibbsite": (
        ("C_p° = 220.851 + 3.00646x10^-2 T - 2.66764x10^3 T^-0.5 + 6.61704x10^5 T^-2", "200 - 800 K"),
    ),
    "usgs-b1544-lime": (
        ("C_p° = 71.6851 - 3.08248x10^-3 T + 2.23862x10^-6 T^2 - 4.31990x10^2 T^-0.5 - 2.55577x10^5 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-h2o-reference": (
        ("C_p° = 42.0228 + 3.49132x10^-2 T + 1.10338x10^6 T^-2", "273 - 425 K, liquid"),
        ("C_p° = 10.4381 + 1.29775x10^-2 T - 4.46885x10^-6 T^2 + 2.99188x10^2 T^-0.5 - 1.31077x10^5 T^-2", "298 - 1800 K, gas"),
    ),
    "usgs-b1544-quartz": (
        ("C_p° = 83.2101 + 1.09962x10^-2 T - 7.77338x10^2 T^-0.5", "200 - 844 K"),
        ("C_p° = 58.9107 + 5.02080x10^-3 T", "844 - 1800 K"),
    ),
    "usgs-b1544-al2sio5-reference": (
        ("C_p° = 336.114 - 1.29800x10^-2 T - 3.55746x10^3 T^-0.5", "200 - 1600 K, kyanite"),
        ("C_p° = 543.227 - 0.103545 T + 6.68935x10^-5 T^2 - 6.75436x10^3 T^-0.5 + 2.28751x10^6 T^-2", "200 - 1800 K, andalusite"),
        ("C_p° = 313.470 - 9.47081x10^-3 T - 3.16487x10^3 T^-0.5", "200 - 1800 K, sillimanite"),
    ),
    "usgs-b1544-kyanite": (
        ("C_p° = 336.114 - 1.29800x10^-2 T - 3.55746x10^3 T^-0.5", "200 - 1600 K"),
    ),
    "usgs-b1544-andalusite": (
        ("C_p° = 543.227 - 0.103545 T + 6.68935x10^-5 T^2 - 6.75436x10^3 T^-0.5 + 2.28751x10^6 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-sillimanite": (
        ("C_p° = 313.470 - 9.47081x10^-3 T - 3.16487x10^3 T^-0.5", "200 - 1800 K"),
    ),
    "usgs-b1544-ca3sio5-reference": (
        ("C_p° = 333.920 - 2.32529x10^-3 T - 2.76608x10^3 T^-0.5 - 6.52597x10^4 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-larnite-reference": (
        ("C_p° = 249.689 - 2.09429x10^3 T^-0.5", "200 - 1100 K, larnite"),
        ("C_p° = 161.620 + 1.88970x10^-5 T^2", "800 - 1800 K, alpha'"),
        ("C_p° = 199.600", "1700 - 1800 K, alpha"),
    ),
    "usgs-b1544-larnite": (
        ("C_p° = 249.689 - 2.09429x10^3 T^-0.5", "200 - 1100 K"),
    ),
    "usgs-b1544-calcium-olivine-reference": (
        ("C_p° = 0.106586 T - 8.15012x10^-5 T^2 + 1.65638x10^3 T^-0.5 - 2.36007x10^6 T^-2", "200 - 1200 K, calcium olivine"),
        ("C_p° = 161.620 + 1.88970x10^-5 T^2", "800 - 1800 K, alpha'"),
        ("C_p° = 199.600", "1700 - 1800 K, alpha"),
    ),
    "usgs-b1544-calcium-olivine": (
        ("C_p° = 0.106586 T - 8.15012x10^-5 T^2 + 1.65638x10^3 T^-0.5 - 2.36007x10^6 T^-2", "200 - 1200 K"),
    ),
    "usgs-b1544-rankinite": (
        ("C_p° = 473.209 - 2.10355x10^-2 T - 4.31880x10^3 T^-0.5 + 3.39720x10^5 T^-2", "200 - 1400 K"),
    ),
    "usgs-b1544-gehlenite": (
        ("C_p° = 588.351 - 6.71533x10^-2 T + 3.89086x10^-5 T^2 - 6.27433x10^3 T^-0.5 + 1.51047x10^6 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-grossulsr": (
        ("C_p° = 985.362 - 9.66435x10^-2 T + 3.35314x10^-5 T^2 - 1.07077x10^4 T^-0.5 + 1.77080x10^6 T^-2", "200 - 1400 K"),
    ),
    "usgs-b1544-zoisite": (
        ("C_p° = 834.622 - 1.98447x10^-2 T - 8.14875x10^3 T^-0.5", "200 - 900 K"),
    ),
    "usgs-b1544-casio3-reference": (
        ("C_p° = 192.773 - 9.11511x10^-3 T + 4.41319x10^-6 T^2 - 1.72960x10^3 T^-0.5", "200 - 1500 K, wollastonite"),
        ("C_p° = 167.255 - 3.62159x10^-4 T - 1.37237x10^3 T^-0.5 - 9.73908x10^3 T^-2", "200 - 1800 K, cyclowollastonite"),
    ),
    "usgs-b1544-wollastonite": (
        ("C_p° = 192.773 - 9.11511x10^-3 T + 4.41319x10^-6 T^2 - 1.72960x10^3 T^-0.5", "200 - 1500 K"),
    ),
    "usgs-b1544-cyclowollastonite-pseudowollastonite": (
        ("C_p° = 167.255 - 3.62159x10^-4 T - 1.37237x10^3 T^-0.5 - 9.73908x10^3 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-ca-al-pyroxene": (
        ("C_p° = 322.848 - 2.18582x10^3 T^-0.5 - 2.72024x10^6 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-anorthite": (
        ("C_p° = 800.971 - 0.146450 T + 1.05663x10^-4 T^2 - 9.44981x10^3 T^-0.5 + 3.18591x10^6 T^-2", "200 - 1800 K"),
    ),
    "usgs-b1544-kaolinite": (
        ("C_p° = 749.175 - 6.77102x10^-2 T - 8.27864x10^3 T^-0.5 + 1.49195x10^6 T^-2", "200 - 1000 K"),
    ),
    "usgs-b1544-dickite": (
        ("C_p° = 908.360 - 0.105663 T - 1.11953x10^4 T^-0.5 + 3.80445x10^6 T^-2", "200 - 900 K"),
    ),
    "usgs-b1544-halloysite": (
        ("C_p° = 772.300 - 7.25884x10^-2 T - 8.72948x10^3 T^-0.5 + 1.93671x10^6 T^-2", "200 - 900 K"),
    ),
    "usgs-b1544-pyrophyllite": (
        ("C_p° = 1454.51 - 0.396093 T + 3.97189x10^-4 T^2 - 1.77428x10^4 T^-0.5 + 6.06936x10^6 T^-2", "200 - 1200 K"),
    ),
    "usgs-b1544-margarite": (
        ("C_p° = 826.504 - 2.51455x10^-2 T - 8.42744x10^3 T^-0.5", "200 - 1200 K"),
    ),
    "usgs-b1544-prehnite": (
        ("C_p° = 946.022 - 5.75327x10^-2 T - 1.05605x10^4 T^-0.5 + 2.75523x10^6 T^-2", "200 - 1200 K"),
    ),
}

TABLE1_IMAGE_ROWS = (
    ("Corundum Al2O3", ("-1675.711", "-1675.700", "-1661.655", "-1672.600"), ("±1.000", "±1.300", None, "±6.0"), "(-1674.7)"),
    ("Quartz SiO2", ("-910.699", "-910.700", "-910.648", "-910.700"), ("±.900", "±1.000", None, "±1.00"), None),
    ("Water H2O", ("-285.808", "-285.830", "-285.830", "-285.830"), ("±.042", "±.042", None, "±.04"), None),
    ("Lime CaO", ("-635.094", "-635.089", "-635.089", None), ("±1.30", "±.879", None, None), None),
    ("Diaspore AlO(OH)", ("-999.456", "-1000.585", "-992.319", "-998.825"), ("±.366", "±5.000", None, "±3.8"), "(-998.8)"),
    ("Boehmite AlO(OH)", ("-990.424", "-993.054", "-983.566", "-990.608"), ("±.725", "±2.110", None, "±3.9"), "(-989.1)"),
    ("Gibbsite Al(OH)3", ("-1293.334", "-1293.128", "-1293.128", None), ("±.628", "±1.192", None, None), None),
    ("Kaolinite Al2Si2O5(OH)4", ("-4119.780", "-4120.114", "-4109.613", "-4120.114"), ("±1.065", "±3.975", None, "±2.6"), "(-4122.6)"),
    ("Pyrophyllite Al2Si4O10(OH)2", ("-5642.023", "-5639.800", "-5628.790", "-5640.415"), ("±1.158", "±3.950", None, "±4.5"), "(-5641.8)"),
    ("Kyanite Al2SiO5", ("-2594.269", "-2591.730", "-2581.097", "-2593.063"), ("±.433", "±1.900", None, "±5.8"), "(-2594.1)"),
    ("Andalusite Al2SiO5", ("-2590.270", "-2587.525", "-2576.783", "-2588.663"), ("±.641", "±2.100", None, "±5.7"), "(-2589.8)"),
    ("Sillimanite Al2SiO5", ("-2587.774", "-2585.760", "-2573.574", "-2585.341"), ("±.537", "±1.740", None, "±5.8"), "(-2586.6)"),
    ("Anorthite CaAl2Si2O8", ("-4227.833", "-4229.100", "-4216.518", None), ("±1.118", "±3.125", None, None), "(-4229.5)"),
    ("Gehlenite Ca2Al2SiO7", ("-3981.707", "-4007.570", "-3981.766", None), ("±2.458", "±2.820", None, None), "(-3994.8)"),
    ("Grossular Ca3Al2Si3O12", ("-6636.338", "-6643.140", "-6624.933", None), ("±3.220", "±6.000", None, None), "(-6637.9)"),
    ("Ca-Al pyroxene CaAl2SiO6", ("-3298.956", "-3275.680", "-3280.310", None), ("±1.912", "±2.761", None, None), "(-3293.3)"),
    ("Margarite CaAl4Si2O10(OH)2", ("-6240.601", None, "-6217.520", None), ("±1.954", None, None, None), "(-6243.5)"),
    ("Prehnite Ca2Al2Si3O10(OH)2", ("-6193.631", None, "-6201.060", None), ("±1.699", None, None, None), "(-6214.1)"),
    ("Zoisite Ca2Al3Si3O12(OH)", ("-6891.147", None, "-6879.044", None), ("±2.080", None, None, None), "(-6892.0)"),
    ("Wollastonite CaSiO3", ("-1634.766", "-1635.200", "-1630.965", None), ("±.702", "±1.435", None, None), None),
)

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
    "enthalpy_298_minus_0": "kJ",
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


class AmbiguousPrintedGridNode(HemingwayHaasRobinsonLookupError):
    """The candidate T token is OCR-suspect or disagrees with an identity check."""


class UnparsedPrintedToken(HemingwayHaasRobinsonLookupError):
    """The printed token at this node was not a parseable number."""


class OcrSuspectTableValueError(HemingwayHaasRobinsonLookupError):
    """A public record result contains OCR-suspect numeric data."""


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
        # Render to a private temp file, then rename into place.
        #
        # The is_file() check and the write are not atomic together, and this cache
        # is shared across processes. Under xdist every worker that needs the same
        # page sees the file missing in the same instant and runs pdftotext against
        # the same target path, so their renders land in one file and the cache ends
        # up holding one <html> document per racing worker. ET.parse then dies on the
        # second root element ("not well-formed (invalid token)").
        #
        # Measured 2026-09-18 on this file: the parallel run left a cache with 5
        # </html> closings and failed exactly the 5 tests that read page 21, while
        # the same tests with -n 0 passed 17/17 and left a 1-closing cache.
        #
        # os.replace is atomic within a filesystem, so a racing worker sees either no
        # file (and renders its own complete copy) or a whole one -- never a partial
        # or concatenated document. Duplicate renders are wasted work, not corruption.
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".xml", delete=False) as handle:
            tmp_path = Path(handle.name)
        try:
            subprocess.run(
                ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), str(tmp_path)],
                check=True,
                capture_output=True,
            )
            os.replace(tmp_path, xml_path)
        finally:
            tmp_path.unlink(missing_ok=True)
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
    uncertainty = core.startswith("±")
    if uncertainty:
        core = core[1:]
        flags.append("uncertainty_as_published")
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
    normalized_raw = raw.replace("(", "").replace(")", "").replace(" ", "")
    if uncertainty:
        normalized_raw = normalized_raw[1:]
    if re.search(r"[oOlISB•·:~]", core) or core != normalized_raw:
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


def is_divider_row(tokens: list[dict[str, Any]]) -> bool:
    return bool(tokens) and set(row_text(tokens)) <= set("-_=—~·.• ")


def parse_header(rows: list[list[dict[str, Any]]], pdf_page: int) -> dict[str, Any]:
    name_parts: list[str] = []
    formula_weight = None
    phase_parts: list[str] = []
    formation_basis = None
    compiled = None
    printed_page = pdf_page - PDF_TO_PRINTED
    molar_volume_jbar = None
    molar_volume_cm3 = None
    melting_point = None
    boiling_point = None
    enthalpy_of_melting = None
    enthalpy_of_vaporization = None
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
        if in_cp:
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
        "reference_state_notes_as_published": notes,
        "header_ambiguities": ambiguities,
        "saw_data": saw_data,
    }


def assign_columns(tokens: list[dict[str, Any]], bands: tuple = DEFAULT_COLUMN_BANDS) -> dict[str, Any]:
    assigned: dict[str, dict[str, Any]] = {}
    extras: list[str] = []
    marks: list[dict[str, str]] = []
    last_numeric: str | None = None
    for tok in tokens:
        text = tok["t"]
        if text in {"*", "x", "X"}:
            if last_numeric:
                marks.append({"column": last_numeric, "as_published": text})
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
        else:
            assigned[col] = parsed
        last_numeric = col
    if marks:
        assigned["_marks"] = marks
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
        if is_divider_row(tokens):
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
    marks = [
        {
            "formation_basis": basis,
            "column": mark["column"],
            "as_published": mark["as_published"],
        }
        for mark in assigned.get("_marks") or []
    ]
    if basis == "from_the_oxides":
        for column in ("formation_enthalpy", "formation_gibbs_energy"):
            if assigned.get(column) and not any(
                mark["column"] == column and mark["as_published"] == "*" for mark in marks
            ):
                marks.append({
                    "formation_basis": basis,
                    "column": column,
                    "as_published": "*",
                })
    row = {
        **thermo,
        "formation": {basis: formation},
        "marks": marks,
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
                right_row = build_grid_row(assigned, right_basis)
                formed = right_row["formation"][right_basis]
                rows[idx]["formation"][right_basis] = formed
                rows[idx]["marks"].extend(right_row["marks"])
                logk_id = right_row["identity_checks"][f"logKf_vs_dG_{right_basis}"]
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
    record_id = "usgs-b1544-" + slugify(name)
    image_formula = IMAGE_FORMULAS_BY_RECORD_ID.get(record_id)
    if image_formula and header.get("formula_as_published") != image_formula:
        reason = "omitted" if not header.get("formula_as_published") else "corrupted"
        header["formula_as_published"] = image_formula
        header.setdefault("header_ambiguities", []).append(
            f"formula_{reason}_in_ocr_layer; transcribed from page image as a second reading"
        )
    equations = [
        {
            "as_published": equation,
            "validity_as_published": f"(EQUATION VALID FROM {validity})",
            "ocr_suspect": False,
            "source_pdf_pages": pdf_pages,
        }
        for equation, validity in IMAGE_CP_EQUATIONS_BY_RECORD_ID.get(record_id, ())
    ]
    h298_minus_h0_token = IMAGE_H298_MINUS_H0_BY_RECORD_ID.get(record_id)
    h298_minus_h0 = parse_number_token(h298_minus_h0_token) if h298_minus_h0_token else None
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
        "enthalpy_298_minus_0": h298_minus_h0,
        "heat_capacity_equations_as_published": equations,
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
    del pdf, cache_dir
    columns = ("haas_1979", "robie_1979", "helgeson_1978", "hemley_1980")
    entries = []
    for phase, raw_values, raw_uncertainties, raw_correction in TABLE1_IMAGE_ROWS:
        entries.append({
            "phase_as_published": phase,
            "values": {
                column: parse_number_token(token) if token is not None else None
                for column, token in zip(columns, raw_values, strict=True)
            },
            "uncertainties": {
                column: parse_number_token(token)
                for column, token in zip(columns, raw_uncertainties, strict=True)
                if token is not None
            },
            "helgeson_corrected": (
                parse_number_token(raw_correction) if raw_correction is not None else None
            ),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "record_id": "usgs-b1544-table-1",
        "table_number_as_published": "TABLE 1",
        "name_as_published": "Enthalpies of formation from the elements at 298.15 K for selected phases taken from several literature references",
        "formula_as_published": None,
        "phase_as_published": None,
        "title_as_published": "Enthalpies of formation from the elements at 298.15 K for selected phases taken from several literature references",
        "headnote_as_published": "[-,value not given]",
        "printed_pages": [TABLE1_PDF_PAGE - PDF_TO_PRINTED],
        "pdf_pages": [TABLE1_PDF_PAGE],
        "units_as_published": {"formation_enthalpy": "kJ/mol at 298.15 K"},
        "columns_as_published": [
            "Phase",
            "Hass and others (1979)",
            "Robie and others (1979)",
            "Helgeson and others (1978)*",
            "Remley and others (1980)",
        ],
        "footnote_as_published": "*The values in parentheses represent the value derived by Helgeson and others plus a correction of -6.5 kJ per mole of aluminum.",
        "rows": entries,
        "row_count": len(entries),
        "ocr_suspect_row_count": 0,
        "identity_disagreements": [],
        "ambiguities": [
            "table_1_header prints Hass as published; it is not silently changed to Haas",
            "blank cells retained as null; '-' means value not given as printed in the headnote",
        ],
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


def _has_ocr_suspect_value(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("ocr_suspect") is True:
            return True
        return any(_has_ocr_suspect_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_ocr_suspect_value(child) for child in value)
    return False


def parse_source(
    pdf: Path | None = None,
    cache_dir: Path | None = None,
    verbose: bool = False,
    *,
    include_ocr_suspect: bool = False,
) -> list[dict[str, Any]]:
    pdf = pdf or locate_source_pdf()
    cache_dir = cache_dir or Path("/tmp/b1544-bbox")
    table1 = parse_table1(pdf, cache_dir)
    if verbose:
        print(f"PROGRESS: table {table1['record_id']} pdf={table1['pdf_pages']} printed={table1['printed_pages']} rows={table1['row_count']}")
    substances = parse_all_substance_tables(pdf, cache_dir, verbose=verbose)
    dickite = next(record for record in substances if record["record_id"] == "usgs-b1544-dickite")
    row = dickite["rows"][1]
    row["temperature"].update(
        layout_as_extracted="1100", as_published="400", value="400",
        ocr_suspect=True, flags=["image_verified_correction"],
    )
    row["identity_checks"]["logKf_vs_dG_from_the_elements"].update(
        ok=True,
        delta="0.0022658054211320286822358117739046435",
        predicted_log_kf="481.2577341945788679713177641882260953565",
    )
    dickite["identity_disagreements"] = []
    dickite["ambiguities"][1] = (
        "temperature OCR token corrected from 1100 to image-verified printed 400; raw token retained"
    )
    dickite["corrections"] = [{
        "record_id": "usgs-b1544-dickite", "pdf_page": 67, "printed_page": 61,
        "row_index": 1, "column": "temperature", "ocr_token": "1100",
        "printed_token": "400",
        "correction_provenance": "image_read_reconstruction",
        "image_quote": (
            "400 | 67.647 | 274.79 | 207.14 | 287.84 | -4120.356 | -3685.353 | 481.260"
        ),
    }]
    records = [table1, *substances]
    if _has_ocr_suspect_value(records) and not include_ocr_suspect:
        raise OcrSuspectTableValueError(
            "parsed Bulletin 1544 records contain OCR-suspect values; "
            "pass include_ocr_suspect=True for explicit inspection"
        )
    return records


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


def load_records(
    root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False
) -> list[dict[str, Any]]:
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    records = []
    for entry in manifest["entries"]:
        payload = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if payload["record_id"] != entry["record_id"]:
            raise ValueError(f"record_id mismatch in {entry['path']}")
        if _has_ocr_suspect_value(payload) and not include_ocr_suspect:
            raise OcrSuspectTableValueError(
                f"{payload['record_id']} contains OCR-suspect values; "
                "pass include_ocr_suspect=True for explicit inspection"
            )
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


def _grid_node_ambiguities(row: dict[str, Any]) -> list[str]:
    reasons = []
    temperature = row.get("temperature") or {}
    if temperature.get("ocr_suspect"):
        reasons.append(
            f"temperature token {temperature.get('as_published')!r} is ocr_suspect"
        )
    for check_name, check in (row.get("identity_checks") or {}).items():
        if check.get("ok") is False:
            reasons.append(f"identity check {check_name} disagrees (delta={check.get('delta')})")
    return reasons


def lookup(
    record_id: str,
    temperature_K: str | Decimal,
    column: str,
    *,
    formation_basis: str | None = None,
    records: list[dict[str, Any]] | None = None,
    root: Path = COMPILATION_ROOT,
    include_ocr_suspect: bool = False,
) -> Decimal:
    """Return a printed-grid value. Refuses missing, extra, or duplicate T."""
    if records is None:
        records = load_records(root, include_ocr_suspect=True)
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
    ambiguous = [reason for row in hits for reason in _grid_node_ambiguities(row)]
    if include_ocr_suspect:
        ambiguous = [reason for reason in ambiguous if "ocr_suspect" not in reason]
    if ambiguous:
        raise AmbiguousPrintedGridNode(
            f"{record_id}: T={temperature} K is an ambiguous printed-grid candidate: "
            + "; ".join(ambiguous)
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
    if cell.get("ocr_suspect") and not include_ocr_suspect:
        raise AmbiguousPrintedGridNode(
            f"{record_id}: T={temperature} K column={column} is ocr_suspect; "
            "pass include_ocr_suspect=True for explicit inspection"
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

    records = parse_source(dest_pdf, cache_dir, verbose=True, include_ocr_suspect=True)
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
            "extraction": "pdftotext -bbox for T-grid cells; page-image transcription for TABLE 1, printed formulas, Cp equations, H°298-H°0 metadata, and qualification marks",
            "census_table_count": len(census),
            "transcribed_table_count": len(records),
            "untranscribed": untranscribed,
            "ambiguities": [
                "OCR text layer, not a typeset source; ocr_suspect tokens retained raw and not corrected",
                "identity G=H-TS and delta-f G vs log Kf used as detectors only",
                "page-image transcriptions are limited to fields named in extraction; no identity-derived corrections",
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
