#!/usr/bin/env python3
"""Build Kume & Morita 2000 melt-activity bench points from transcribed tables.

Every numeric slag composition and activity in TABLES below was read from the
printed ISIJ tables (Paper B, DOI 10.2355/isijinternational.40.561). Weight
percents for Tables 1-3 are derived from printed mole fractions with the
arithmetic shown in each composition_note. Table 4 mass% is used as printed.
No activity is interpolated or reconstructed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "data/melt_activity/basalt-bench-set-v1.yaml"
STANDALONE = REPO / "data/melt_activity/kume2000-cmas-bench.yaml"
LEDGER = REPO / "docs-private/research/2026-08-18-kume-transcription/ledger.yaml"

# Project NIST/IUPAC molar masses (g/mol), same sources as the existing
# tsaplin/yamaguchi conversions. AlO1.5 is Al2O3/2 so alumina mass is unchanged.
M = {
    "SiO2": 60.083,
    "CaO": 56.077,
    "AlO1.5": 101.960077 / 2.0,
    "MgO": 40.304,
}

DOI_B = "10.2355/isijinternational.40.561"
DOI_A = "10.2355/isijinternational.40.554"
SHA_B = "8df8e1f670bbc57ec79b308021820905d89c4f62f0e7a83076613f8352455689"
SHA_A = "f4d38726dcce0a78f924b439cbe4a989a212e1bd21ef7084195167f5e0499088"
CITE_B = (
    "Kume, K.; Morita, K.; Miki, T.; Sano, N. (2000), Activity measurement "
    "of CaO–SiO2–AlO1.5–MgO slags equilibrated with molten silicon alloys, "
    "ISIJ International 40(6), 561–566."
)
CITE_A = (
    "Morita, K.; Kume, K.; Sano, N. (2000), A newly developed method for "
    "determining SiO2 activity of the silicate slags equilibrated with molten "
    "silicon alloys, ISIJ International 40(6), 554–560."
)

# Paper B Table 1. CaO–SiO2. Columns: sample, X_SiO2, X_CaO, a_SiO2, a_CaO, T_K
# metal mass% Ca is recorded but not scored.
TABLE1 = [
    (1, 0.645, 0.355, 1, 0.00174, 1823),
    (2, 0.597, 0.403, 0.781, 0.00231, 1823),
    (3, 0.547, 0.453, 0.561, 0.00280, 1823),
    (4, 0.506, 0.494, 0.351, 0.00586, 1823),
    (5, 0.451, 0.549, 0.134, 0.0131, 1823),
    (6, 0.665, 0.335, 1, 0.00101, 1873),
    (7, 0.604, 0.396, 0.733, 0.00161, 1873),
    (8, 0.559, 0.441, 0.535, 0.00286, 1873),
    (9, 0.510, 0.490, 0.321, 0.00460, 1873),
    (10, 0.494, 0.506, 0.256, 0.00505, 1873),
    (11, 0.445, 0.555, 0.109, 0.0132, 1873),
    (12, 0.444, 0.556, 0.106, 0.0140, 1873),
]

# Paper B Table 2. CaO–SiO2–AlO1.5 at 1823 K.
# sample, X_SiO2, X_CaO, X_AlO1.5, a_SiO2, a_CaO, a_AlO1.5
TABLE2 = [
    (101, 0.876, 0.039, 0.085, 1, 0.000228, 0.487),
    (102, 0.818, 0.092, 0.091, 1, 0.000507, 0.172),
    (103, 0.761, 0.154, 0.085, 1, 0.000758, 0.059),
    (104, 0.706, 0.248, 0.046, 1, 0.000497, 0.015),
    (105, 0.489, 0.149, 0.362, 0.428, 0.000167, 0.344),
    (106, 0.542, 0.146, 0.312, 0.505, 0.000217, 0.327),
    (107, 0.602, 0.145, 0.253, 0.606, 0.000835, 0.264),
    (108, 0.660, 0.142, 0.198, 0.714, 0.00115, 0.248),
    (109, 0.705, 0.145, 0.150, 0.820, 0.000519, 0.149),
    (110, 0.412, 0.186, 0.402, 0.315, 0.000265, 0.478),
    (111, 0.462, 0.197, 0.341, 0.400, 0.000505, 0.374),
    (112, 0.497, 0.232, 0.271, 0.480, 0.000929, 0.281),
    (113, 0.538, 0.257, 0.205, 0.578, 0.00119, 0.175),
    (114, 0.561, 0.286, 0.153, 0.656, 0.000807, 0.083),
    (115, 0.635, 0.263, 0.101, 0.845, 0.00178, 0.054),
    (116, 0.314, 0.254, 0.432, 0.158, 0.00151, 0.515),
    (117, 0.364, 0.266, 0.370, 0.234, 0.00120, 0.319),
    (118, 0.389, 0.249, 0.361, 0.284, 0.000877, 0.299),
    (119, 0.406, 0.280, 0.314, 0.313, 0.00144, 0.260),
    (120, 0.415, 0.314, 0.271, 0.328, 0.00196, 0.221),
    (121, 0.461, 0.317, 0.222, 0.432, 0.00184, 0.160),
    (122, 0.478, 0.350, 0.172, 0.487, 0.00218, 0.091),
    (123, 0.536, 0.402, 0.062, 0.646, 0.00260, 0.062),
    (124, 0.263, 0.272, 0.465, 0.0893, 0.00249, 0.524),
    (125, 0.278, 0.305, 0.417, 0.0955, 0.00423, 0.376),
    (126, 0.297, 0.314, 0.389, 0.120, 0.00356, 0.401),
    (127, 0.315, 0.345, 0.339, 0.131, 0.00393, 0.264),
    (128, 0.340, 0.376, 0.284, 0.152, 0.00472, 0.222),
    (129, 0.361, 0.404, 0.235, 0.162, 0.00629, 0.202),
    (130, 0.398, 0.415, 0.187, 0.225, 0.00617, 0.189),
    (131, 0.389, 0.476, 0.135, 0.133, 0.00624, 0.099),
    (132, 0.450, 0.438, 0.112, 0.332, 0.00414, 0.116),
    (133, 0.453, 0.483, 0.064, 0.288, 0.00614, 0.032),
    (134, 0.212, 0.324, 0.463, 0.0345, 0.00876, 0.591),
    (135, 0.242, 0.349, 0.409, 0.0382, 0.00920, 0.430),
    (136, 0.254, 0.384, 0.362, 0.0292, 0.00929, 0.282),
    (137, 0.265, 0.422, 0.313, 0.0227, 0.0109, 0.226),
    (138, 0.296, 0.444, 0.259, 0.0350, 0.0173, 0.273),
    (139, 0.322, 0.470, 0.208, 0.0439, 0.0182, 0.220),
    (140, 0.342, 0.503, 0.155, 0.0500, 0.0146, 0.125),
    (141, 0.368, 0.521, 0.111, 0.0646, 0.0179, 0.127),
    (142, 0.411, 0.529, 0.060, 0.117, 0.0123, 0.062),
    (143, 0.417, 0.177, 0.406, 0.321, 0.000319, 0.356),
    (144, 0.356, 0.208, 0.436, 0.232, 0.000679, 0.367),
    (145, 0.166, 0.489, 0.345, 0.00186, 0.0768, 0.223),
    (146, 0.094, 0.432, 0.474, 0.00090, 0.0465, 0.294),
    (147, 0.071, 0.367, 0.562, 0.00085, 0.0228, 0.431),
]

# Paper B Table 3. CaO–SiO2–MgO at 1873 K.
# sample, X_SiO2, X_CaO, X_MgO, a_SiO2, a_CaO, a_MgO
TABLE3 = [
    (201, 0.635, 0.258, 0.107, 1, 0.000970, 0.0393),
    (202, 0.625, 0.280, 0.095, 0.944, 0.00120, 0.0421),
    (203, 0.523, 0.397, 0.080, 0.476, 0.00403, 0.0705),
    (204, 0.517, 0.376, 0.107, 0.475, 0.00291, 0.101),
    (205, 0.449, 0.465, 0.085, 0.158, 0.0168, 0.162),
    (206, 0.433, 0.513, 0.054, 0.100, 0.0277, 0.160),
    (207, 0.427, 0.470, 0.102, 0.103, 0.00768, 0.103),
    (208, 0.427, 0.446, 0.127, 0.109, 0.0105, 0.211),
    (209, 0.617, 0.190, 0.193, 1, 0.000674, 0.0801),
    (210, 0.586, 0.219, 0.195, 0.875, 0.000848, 0.0940),
    (211, 0.509, 0.271, 0.220, 0.522, 0.00176, 0.171),
    (212, 0.430, 0.383, 0.188, 0.135, 0.00711, 0.289),
    (213, 0.425, 0.376, 0.199, 0.129, 0.00664, 0.280),
    (214, 0.604, 0.101, 0.295, 1, 0.000635, 0.152),
    (215, 0.553, 0.124, 0.323, 0.872, 0.000770, 0.159),
    (216, 0.494, 0.181, 0.325, 0.583, 0.00111, 0.236),
    (217, 0.453, 0.224, 0.323, 0.346, 0.00193, 0.304),
    (218, 0.438, 0.220, 0.342, 0.300, 0.00395, 0.501),
    (219, 0.425, 0.260, 0.315, 0.192, 0.00526, 0.452),
    (220, 0.411, 0.324, 0.265, 0.114, 0.00565, 0.351),
    (221, 0.418, 0.310, 0.272, 0.162, 0.00945, 0.351),
    (222, 0.488, 0.068, 0.444, 0.763, 0.000869, 0.334),
    (223, 0.450, 0.125, 0.425, 0.521, 0.00119, 0.414),
    (224, 0.423, 0.095, 0.482, 0.500, 0.00121, 0.460),
    (225, 0.356, 0.224, 0.420, 0.0630, 0.00559, 0.765),
    (226, 0.565, 0.0, 0.435, 0.980, 0.0, 0.228),
    (227, 0.490, 0.0, 0.510, 0.858, 0.0, 0.254),
]

# Paper B Table 4. CMAS, printed mass%, 1873 K.
# sample, SiO2, CaO, AlO1.5, MgO, a_SiO2_Rein, a_CaO, a_AlO1.5, a_MgO
TABLE4 = [
    (301, 32.1, 33.4, 27.8, 6.7, 0.0800, 0.00345, 0.0878, 0.214),
    (302, 31.7, 32.4, 27.3, 8.6, 0.0830, 0.00390, 0.1168, 0.237),
    (303, 32.1, 32.6, 28.1, 7.3, 0.0780, 0.00344, 0.0852, 0.214),
    (304, 30.2, 33.1, 27.7, 9.0, 0.0650, 0.00589, 0.1390, 0.295),
    (305, 34.6, 19.6, 39.0, 6.8, 0.0360, 0.0136, 0.0253, 0.327),
    (306, 39.2, 8.8, 46.9, 5.2, 0.0360, 0.0223, 0.0076, 0.362),
    (307, 38.1, 31.5, 22.0, 8.4, 0.200, 0.00169, 0.1563, 0.188),
    (308, 41.0, 19.3, 29.7, 10.1, 0.185, 0.00483, 0.0530, 0.261),
    (309, 44.3, 8.9, 38.6, 8.2, 0.161, 0.00792, 0.0092, 0.237),
    (310, 46.1, 30.8, 9.9, 13.2, 0.470, 0.000583, 0.1811, 0.110),
    (311, 47.2, 19.4, 19.8, 13.6, 0.441, 0.00119, 0.0178, 0.146),
    (312, 48.2, 9.3, 29.7, 12.9, 0.400, 0.00418, 0.0159, 0.221),
    (313, 59.8, 18.3, 9.2, 12.7, 0.711, 0.000224, 0.0160, 0.0590),
    (314, 56.7, 10.3, 19.3, 13.7, 0.684, 0.000890, 0.0063, 0.103),
    (315, 57.2, 31.5, 0.0, 11.4, 0.640, 0.0, 0.1249, 0.0280),
    (316, 70.8, 19.3, 0.0, 9.9, 0.830, 0.0, 0.0697, 0.0477),
    (317, 82.1, 9.4, 0.0, 8.6, 1, 0.0, 0.0579, 0.137),
    (318, 76.0, 4.7, 10.0, 9.2, 1, 0.000491, 0.0012, 0.113),
]

OXIDE_SPECIES = {"SiO2": "SiO", "CaO": "Ca", "Al2O3": "Al", "MgO": "Mg"}


def fmt_num(value: float) -> str:
    if isinstance(value, int) or (isinstance(value, float) and value == int(value) and abs(value) >= 1):
        return str(int(value))
    text = f"{value:.12g}"
    return text


def x_to_wt(x_map: dict[str, float]) -> dict[str, float]:
    masses = {key: x_map[key] * M[key] for key in x_map if x_map[key] > 0.0}
    total = sum(masses.values())
    wt: dict[str, float] = {}
    for key, mass in masses.items():
        out_key = "Al2O3" if key == "AlO1.5" else key
        wt[out_key] = 100.0 * mass / total
    return wt


def mass_to_wt(mass_map: dict[str, float]) -> dict[str, float]:
    wt: dict[str, float] = {}
    for key, value in mass_map.items():
        if value <= 0.0:
            continue
        out_key = "Al2O3" if key == "AlO1.5" else key
        wt[out_key] = float(value)
    return wt


def checksum_x(x_map: dict[str, float]) -> float:
    return sum(x_map.values())


def emit_wt_block(wt: dict[str, float], indent: int) -> list[str]:
    pad = " " * indent
    order = ["SiO2", "CaO", "Al2O3", "MgO"]
    lines = [f"{pad}composition_wt_pct:"]
    for key in order:
        if key in wt:
            lines.append(f"{pad}  {key}: {wt[key]:.6f}")
    return lines


def common_point_fields(
    *,
    sample: int,
    table: str,
    page: str,
    temperature_K: int,
    wt: dict[str, float],
    published_x: dict[str, float] | None,
    published_mass: dict[str, float] | None,
) -> list[str]:
    lines = [
        f"    population: kume2000_slag_si_alloy",
        f"    composition_id: kume2000_s{sample}",
        f"    material_class: cmas_slag",
        f"    temperature_K: {float(temperature_K)}",
        f"    measurement_technique: slag_si_alloy_equilibration",
        f"    fO2_condition: not_independently_pinned",
        f"    fO2_note: >-",
        f"      No gas buffer. fO2 is implied by the Si/SiO2 couple under",
        f"      deoxidized argon (Paper B §2.1 / Paper A §2). Not applicable",
        f"      as an independent pin for these oxide activities.",
    ]
    lines.extend(emit_wt_block(wt, 4))
    if published_x is not None:
        lines.append("    published_mole_fraction:")
        for key in ("SiO2", "CaO", "AlO1.5", "MgO"):
            if key in published_x:
                lines.append(f"      {key}: {fmt_num(published_x[key])}")
    if published_mass is not None:
        lines.append("    published_mass_pct:")
        for key in ("SiO2", "CaO", "AlO1.5", "MgO"):
            if key in published_mass:
                lines.append(f"      {key}: {fmt_num(published_mass[key])}")
    return lines


def emit_activity_point(
    *,
    sample: int,
    table: str,
    page: str,
    temperature_K: int,
    parent: str,
    measured: float,
    wt: dict[str, float],
    published_x: dict[str, float] | None,
    published_mass: dict[str, float] | None,
    printed_field: str,
    published_alo15: float | None = None,
    omit_reason: str | None = None,
) -> list[str]:
    oxide_tag = {"SiO2": "sio2", "CaO": "cao", "Al2O3": "al2o3", "MgO": "mgo"}[parent]
    point_id = f"kume2000_s{sample}_a_{oxide_tag}_{temperature_K}"
    lines = [f"  - id: {point_id}"]
    lines.extend(
        common_point_fields(
            sample=sample,
            table=table,
            page=page,
            temperature_K=temperature_K,
            wt=wt,
            published_x=published_x,
            published_mass=published_mass,
        )
    )
    lines.extend(
        [
            f"    parent_oxide: {parent}",
            f"    species: {OXIDE_SPECIES[parent]}",
            f"    observable: activity",
            f"    measured: {fmt_num(measured)}",
            f"    units: dimensionless",
        ]
    )
    if published_alo15 is not None:
        lines.append(f"    published_a_AlO1.5: {fmt_num(published_alo15)}")
        lines.append(
            "    activity_basis_conversion: a(Al2O3,s) = a(AlO1.5,s)^2"
        )
    if omit_reason:
        lines.extend(
            [
                "    score: false",
                "    scoring_status: HELD-NOT-SCORED",
                f"    scoring_status_reason: {omit_reason!r}",
            ]
        )
    else:
        lines.extend(
            [
                "    score: true",
                "    scoring_status: SCORED-ELIGIBLE",
            ]
        )
    lines.extend(
        [
            "    reduction_class: slag_si_alloy_equilibration",
            "    convention: >-",
            "      Printed activity against the authors' pure-solid oxide",
            "      standard state (column headers a SiO2(s), a CaO(s),",
            "      a AlO1.5(s), a MgO(s)). Entered as printed. Alumina is",
            "      stored on the Al2O3 formula-unit basis as a(AlO1.5)^2;",
            "      the printed a(AlO1.5) is retained in published_a_AlO1.5.",
            "      No solid-to-liquid conversion is applied.",
            "    provenance:",
            f"      source_citation: >-",
            f"        {CITE_B}",
            f"      source_doi: {DOI_B}",
            f"      companion_method_doi: {DOI_A}",
            f"      source_sha256: {SHA_B}",
            f"      table: {table!r}",
            f"      row: sample {sample}, {printed_field}",
            f"      page: {page!r}",
            "      transcriber: t692-kume-bench",
            '      transcription_date: "2026-08-18"',
            "      printed_versus_derived: >-",
            "        Activity is the printed table cell. Composition wt% is",
            "        either the printed mass% (Table 4) or the mole-fraction",
            "        conversion documented on the composition record.",
        ]
    )
    return lines


def emit_composition(
    *,
    sample: int,
    system: str,
    table: str,
    temperature_K: int,
    wt: dict[str, float],
    published_x: dict[str, float] | None,
    published_mass: dict[str, float] | None,
    note: str,
) -> list[str]:
    lines = [
        f"  kume2000_s{sample}:",
        "    material_class: cmas_slag",
        "    source: >-",
        f"      {CITE_B} {table}, sample {sample}, {temperature_K} K, {system}.",
    ]
    if published_x is not None:
        lines.append("    published_mole_fraction:")
        for key in ("SiO2", "CaO", "AlO1.5", "MgO"):
            if key in published_x:
                lines.append(f"      {key}: {fmt_num(published_x[key])}")
        xsum = checksum_x(published_x)
        lines.append(f"    published_mole_fraction_sum: {xsum:.6f}")
    if published_mass is not None:
        lines.append("    published_mass_pct:")
        for key in ("SiO2", "CaO", "AlO1.5", "MgO"):
            if key in published_mass:
                lines.append(f"      {key}: {fmt_num(published_mass[key])}")
        lines.append(
            f"    published_mass_pct_sum: {sum(published_mass.values()):.6f}"
        )
    lines.extend(emit_wt_block(wt, 4))
    lines.append("    composition_note: >-")
    for part in note.split("\n"):
        lines.append(f"      {part}")
    return lines


def build() -> tuple[list[str], list[str], list[dict]]:
    compositions: list[str] = []
    points: list[str] = []
    omitted: list[dict] = []
    seen_comp: set[int] = set()

    def add_comp(sample: int, block: list[str]) -> None:
        if sample in seen_comp:
            return
        seen_comp.add(sample)
        compositions.extend(block)

    # Table 1
    for sample, x_s, x_c, a_s, a_c, t in TABLE1:
        x_map = {"SiO2": x_s, "CaO": x_c}
        wt = x_to_wt(x_map)
        note = (
            "Printed mole fractions converted to oxide wt% using "
            f"M(SiO2)={M['SiO2']} g/mol and M(CaO)={M['CaO']} g/mol: "
            "wt_i=100*x_i*M_i/sum(x_j*M_j). No other oxides printed."
        )
        add_comp(
            sample,
            emit_composition(
                sample=sample,
                system="CaO-SiO2",
                table="Table 1",
                temperature_K=t,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                note=note,
            ),
        )
        for parent, measured, field in (
            ("SiO2", a_s, "a SiO2(s)"),
            ("CaO", a_c, "a CaO(s)"),
        ):
            points.extend(
                emit_activity_point(
                    sample=sample,
                    table="Table 1",
                    page="PDF page 2 (journal page 562)",
                    temperature_K=t,
                    parent=parent,
                    measured=measured,
                    wt=wt,
                    published_x=x_map,
                    published_mass=None,
                    printed_field=field,
                )
            )

    # Table 2
    for sample, x_s, x_c, x_a, a_s, a_c, a_al in TABLE2:
        x_map = {"SiO2": x_s, "CaO": x_c, "AlO1.5": x_a}
        wt = x_to_wt(x_map)
        note = (
            "Printed mole fractions on the AlO1.5 (single-cation alumina) "
            f"basis converted to oxide wt% using M(SiO2)={M['SiO2']}, "
            f"M(CaO)={M['CaO']}, M(AlO1.5)=M(Al2O3)/2={M['AlO1.5']} g/mol: "
            "wt_i=100*x_i*M_i/sum(x_j*M_j). Al2O3 wt% equals AlO1.5 mass% "
            "(2 AlO1.5 = Al2O3 by mass)."
        )
        add_comp(
            sample,
            emit_composition(
                sample=sample,
                system="CaO-SiO2-AlO1.5",
                table="Table 2",
                temperature_K=1823,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                note=note,
            ),
        )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 2",
                page="PDF page 3 (journal page 563)",
                temperature_K=1823,
                parent="SiO2",
                measured=a_s,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                printed_field="a SiO2(s)",
            )
        )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 2",
                page="PDF page 3 (journal page 563)",
                temperature_K=1823,
                parent="CaO",
                measured=a_c,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                printed_field="a CaO(s)",
            )
        )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 2",
                page="PDF page 3 (journal page 563)",
                temperature_K=1823,
                parent="Al2O3",
                measured=a_al * a_al,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                printed_field="a AlO1.5(s)",
                published_alo15=a_al,
            )
        )

    # Table 3
    for sample, x_s, x_c, x_m, a_s, a_c, a_m in TABLE3:
        x_map = {"SiO2": x_s, "CaO": x_c, "MgO": x_m}
        wt = x_to_wt(x_map)
        note = (
            "Printed mole fractions converted to oxide wt% using "
            f"M(SiO2)={M['SiO2']}, M(CaO)={M['CaO']}, M(MgO)={M['MgO']} g/mol: "
            "wt_i=100*x_i*M_i/sum(x_j*M_j)."
        )
        add_comp(
            sample,
            emit_composition(
                sample=sample,
                system="CaO-SiO2-MgO",
                table="Table 3",
                temperature_K=1873,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                note=note,
            ),
        )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 3",
                page="PDF page 4 (journal page 564)",
                temperature_K=1873,
                parent="SiO2",
                measured=a_s,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                printed_field="a SiO2(s)",
            )
        )
        if a_c <= 0.0 or x_c <= 0.0:
            omitted.append(
                {
                    "sample": sample,
                    "table": "Table 3",
                    "observable": "a(CaO)",
                    "reason": "printed a(CaO)=0 and/or X_CaO=0; log residual undefined",
                }
            )
        else:
            points.extend(
                emit_activity_point(
                    sample=sample,
                    table="Table 3",
                    page="PDF page 4 (journal page 564)",
                    temperature_K=1873,
                    parent="CaO",
                    measured=a_c,
                    wt=wt,
                    published_x=x_map,
                    published_mass=None,
                    printed_field="a CaO(s)",
                )
            )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 3",
                page="PDF page 4 (journal page 564)",
                temperature_K=1873,
                parent="MgO",
                measured=a_m,
                wt=wt,
                published_x=x_map,
                published_mass=None,
                printed_field="a MgO(s)",
            )
        )

    # Table 4
    for sample, sio2, cao, alo, mgo, a_s_rein, a_c, a_al, a_m in TABLE4:
        mass_map = {"SiO2": sio2, "CaO": cao, "AlO1.5": alo, "MgO": mgo}
        wt = mass_to_wt(mass_map)
        note = (
            "Printed mass% used as-is. AlO1.5 mass% is stored as Al2O3 wt% "
            "because 2 AlO1.5 = Al2O3 by mass (no numeric change). "
            "a(SiO2) in this table is Rein & Chipman, not a Kume measurement, "
            "and is not ingested."
        )
        add_comp(
            sample,
            emit_composition(
                sample=sample,
                system="CaO-SiO2-AlO1.5-MgO 10 mass% MgO plane",
                table="Table 4",
                temperature_K=1873,
                wt=wt,
                published_x=None,
                published_mass=mass_map,
                note=note,
            ),
        )
        omitted.append(
            {
                "sample": sample,
                "table": "Table 4",
                "observable": "a(SiO2)",
                "printed": a_s_rein,
                "reason": "column labeled a SiO2(s) (Rein); Rein & Chipman, not a Kume measurement",
            }
        )
        if a_c <= 0.0:
            omitted.append(
                {
                    "sample": sample,
                    "table": "Table 4",
                    "observable": "a(CaO)",
                    "reason": "printed a(CaO)=0; log residual undefined",
                }
            )
        else:
            points.extend(
                emit_activity_point(
                    sample=sample,
                    table="Table 4",
                    page="PDF page 5 (journal page 565)",
                    temperature_K=1873,
                    parent="CaO",
                    measured=a_c,
                    wt=wt,
                    published_x=None,
                    published_mass=mass_map,
                    printed_field="a CaO(s)",
                )
            )
        if alo <= 0.0:
            omitted.append(
                {
                    "sample": sample,
                    "table": "Table 4",
                    "observable": "a(AlO1.5)",
                    "printed": a_al,
                    "reason": "printed AlO1.5 mass%=0; alumina activity not assigned a melt composition",
                }
            )
        else:
            points.extend(
                emit_activity_point(
                    sample=sample,
                    table="Table 4",
                    page="PDF page 5 (journal page 565)",
                    temperature_K=1873,
                    parent="Al2O3",
                    measured=a_al * a_al,
                    wt=wt,
                    published_x=None,
                    published_mass=mass_map,
                    printed_field="a AlO1.5(s)",
                    published_alo15=a_al,
                )
            )
        points.extend(
            emit_activity_point(
                sample=sample,
                table="Table 4",
                page="PDF page 5 (journal page 565)",
                temperature_K=1873,
                parent="MgO",
                measured=a_m,
                wt=wt,
                published_x=None,
                published_mass=mass_map,
                printed_field="a MgO(s)",
            )
        )

    return compositions, points, omitted


def patch_bench(compositions: list[str], points: list[str]) -> None:
    text = BENCH.read_text(encoding="utf-8")
    if "kume2000_s1:" in text:
        raise SystemExit("bench set already contains kume2000 compositions")

    old_note_end = (
        "    Yamaguchi-1983 supplies 28 scored SiO2 activity targets from seven\n"
        "    Na2O-SiO2 binary melts at four frozen temperature nodes. The published\n"
        "    Tridymite-reference activities are retained alongside controller-directed\n"
        "    liquid-reference conversions. Its 42 measured Na2O activities are retained\n"
        "    but held from scoring pending an authoritative oxide-basis engine result."
    )
    new_note_end = old_note_end + (
        "\n    Kume-2000 supplies CMAS/CAS/CMS/CS slag activities at 1823 and 1873 K\n"
        "    from slag–Si-alloy equilibration (ISIJ 40, 561). Each point carries an\n"
        "    explicit composition_wt_pct vector. Table 4 a(SiO2) is Rein & Chipman\n"
        "    and is excluded. Point ids use sample numbers, not encoded composition."
    )
    if old_note_end not in text:
        raise SystemExit("could not find provenance note block to extend")
    text = text.replace(old_note_end, new_note_end, 1)

    old_sources = (
        "    - docs/references/pdfs/99-kems-langmuir/yam1983.pdf\n"
    )
    new_sources = old_sources + (
        "    - docs-private/research/2026-08-18-bench-candidates/pdfs/kume-2000-cmas.pdf\n"
        "    - docs-private/research/2026-08-18-bench-candidates/pdfs/kume-2000-sio2.pdf\n"
        "    - data/melt_activity/kume2000-cmas-bench.yaml\n"
    )
    if old_sources not in text:
        raise SystemExit("could not find sources block to extend")
    text = text.replace(old_sources, new_sources, 1)

    marker = "composition_probes:\n"
    if marker not in text:
        raise SystemExit("composition_probes marker missing")
    text = text.replace(
        marker,
        "\n".join(compositions) + "\n" + marker,
        1,
    )

    excl = "exclusions:\n"
    if excl not in text:
        raise SystemExit("exclusions marker missing")
    extra_excl = (
        "  - source: Kume 2000 Paper B Table 4 a(SiO2) (Rein)\n"
        "    reason: >-\n"
        "      Quaternary a(SiO2) is Rein & Chipman, not a Kume measurement.\n"
        "      Ingested a(CaO), a(AlO1.5), a(MgO) only on that plane.\n"
        "  - source: Kume 2000 Paper A Tables 2-4 a(SiO2)\n"
        "    reason: >-\n"
        "      Same experimental campaign as Paper B Tables 1-3 a(SiO2)\n"
        "      columns (Paper A Table 2 matches Paper B Table 1 exactly).\n"
        "      Not double-ingested.\n"
        "  - source: Kume 2000 printed a=0 cells\n"
        "    reason: >-\n"
        "      Non-positive activity cannot form a log residual. Table 3\n"
        "      samples 226-227 a(CaO) and Table 4 samples 315-317 a(CaO).\n"
        "  - source: Kume 2000 Table 4 samples 315-317 a(AlO1.5)\n"
        "    reason: >-\n"
        "      Printed AlO1.5 mass% is 0; alumina activity has no melt\n"
        "      composition to assign.\n"
    )
    text = text.replace(excl, "\n".join(points) + "\n" + excl + extra_excl, 1)
    BENCH.write_text(text, encoding="utf-8")


def write_standalone(compositions: list[str], points: list[str]) -> None:
    header = f"""schema_version: melt-activity-bench.v1
title: Kume and Morita 2000 CMAS slag activities (1823/1873 K)
provenance:
  note: >-
    Isolated Kume-2000 fixture for engine scoring. Same points as appended
    to basalt-bench-set-v1.yaml. Every activity is transcribed from Paper B
    tables. Table 4 a(SiO2) is Rein & Chipman and is not present.
  sources:
    - docs-private/research/2026-08-18-bench-candidates/pdfs/kume-2000-cmas.pdf
    - docs-private/research/2026-08-18-bench-candidates/pdfs/kume-2000-sio2.pdf
packs:
  imcc-published: data/melt_activity/imcc/imcc-sf04-v1.0.2.json
  imcc-ext: data/melt_activity/imcc/imcc-sf04-ext-v1.json
reference_anchors:
  imcc_magma:
    path: data/melt_activity/reference/imcc-magma-model-v1.csv
    source_sha256: 81187b97fda64519033ac677aa748591836c328a43939df904035a6e0145bf61
    tracked_sha256: 513fef7b801f09b1c7a34b5f7b13b53c0832a94dec32141bd1c55ad0e5f7b265
    selection: SF04 tho/aba/kom/dun; SiO2/FeO/Fe/Mg/SiO/K/Na/O2
  vaporock_magma_kems:
    path: data/melt_activity/reference/vaporock-magma-kems-v1.csv
    source_sha256: eac513d6bfbc93fb595776b381d2a8b4c217bd693037f5a54d001b2a29bc2ccc
    tracked_sha256: 822c7a881c4f7ee2820c43421e430dc04455457d110c7a6480a42dfe1753151d
    selection: SF04 model_model_MAGMA rows plus all experimental_KEMS rows
  shared_join: [sheet, species, T_K]
  expected_shared_cells: 288
  expected_non_alkali_pooled_rmse_dex:
    imcc: 0.274
    vaporock: 0.503
fair_comparison:
  residual: log10(predicted/measured)
  direct_activity: Compare pure-liquid-standard-state activity directly.
  activity_coefficient: >-
    Compare gamma=a/x on the named parent-oxide formula-unit basis.
compositions:
"""
    STANDALONE.write_text(
        header + "\n".join(compositions) + "\npoints:\n" + "\n".join(points) + "\n",
        encoding="utf-8",
    )


def write_ledger(omitted: list[dict], n_comp: int, n_points: int) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "title: Kume 2000 transcription ledger",
        "doi_activity_paper: 10.2355/isijinternational.40.561",
        "doi_method_paper: 10.2355/isijinternational.40.554",
        f"source_sha256_activity_paper: {SHA_B}",
        f"source_sha256_method_paper: {SHA_A}",
        "transcription_rule: printed cells only; mole-fraction to wt% shown in composition_note",
        f"compositions_added: {n_comp}",
        f"points_added: {n_points}",
        "omitted:",
    ]
    for row in omitted:
        lines.append(f"  - {row!r}")
    LEDGER.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_checksums() -> None:
    for row in TABLE1:
        s = row[1] + row[2]
        if abs(s - 1.0) > 0.001:
            raise SystemExit(f"Table 1 sample {row[0]} X sum {s}")
    for row in TABLE2:
        s = row[1] + row[2] + row[3]
        if abs(s - 1.0) > 0.002:
            raise SystemExit(f"Table 2 sample {row[0]} X sum {s}")
    for row in TABLE3:
        s = row[1] + row[2] + row[3]
        if abs(s - 1.0) > 0.002:
            raise SystemExit(f"Table 3 sample {row[0]} X sum {s}")
    for row in TABLE4:
        s = row[1] + row[2] + row[3] + row[4]
        if abs(s - 100.0) > 0.15:
            raise SystemExit(f"Table 4 sample {row[0]} mass sum {s}")


def main() -> None:
    validate_checksums()
    compositions, points, omitted = build()
    n_comp = sum(1 for line in compositions if line.startswith("  kume2000_s"))
    n_points = sum(1 for line in points if line.startswith("  - id:"))
    write_standalone(compositions, points)
    patch_bench(compositions, points)
    write_ledger(omitted, n_comp, n_points)
    print(f"compositions={n_comp} points={n_points} omitted={len(omitted)}")
    print(f"wrote {STANDALONE}")
    print(f"patched {BENCH}")
    print(f"wrote {LEDGER}")


if __name__ == "__main__":
    main()
