#!/usr/bin/env python3
"""Reproduce the solid-wustite NASA-minus-JANAF G residual column.

NASA source: NASA Glenn/CEA thermo.inp, Fe.947O(cr), lines 12239-12243;
NASA TP-2002-211556. These are the low-temperature NASA-9 coefficients
(a1..a7,b1,b2), valid from 298.15 to 1652 K. The NASA record's composition is
Fe0.95O (n_Fe=0.95), despite its Fe.947O label.

JANAF source: Chase, NIST-JANAF 4th ed. (1998), Fe-001, printed p. 1219,
rows listed below; the tracked machine transcription is
data/literature/compilations/janaf/tables/Fe-001.yaml. It is Fe0.947O(cr).
Values are T (K), S (J mol-1 K-1), H-H(298.15) (kJ mol-1).

The returned residual is NASA G - JANAF G in kJ/mol. NASA G is
R*T*(H/RT-S/R), where H/RT and S/R are the NASA-9 functions. JANAF G is
delta_f_H(298.15) + (H-H(298.15)) - T*S/1000, with delta_f_H=-266.270
kJ/mol. At 1650 K, interpolate JANAF S and H-H(298.15) linearly between
the stored 1600 and 1700 K rows.
"""

from __future__ import annotations

import math

R = 8.314462618  # J mol-1 K-1
NASA_A = (
    -1.179193966e4,
    1.388393372e2,
    2.999841854,
    1.274527210e-2,
    -1.883886065e-5,
    1.274258345e-8,
    -3.042206479e-12,
)
NASA_B1, NASA_B2 = -3.417350500e4, -1.284759120e1
JANAF_DH298_KJ_MOL = -266.270
# (T K, S J mol-1 K-1, H-H(298.15) kJ mol-1), Fe-001, p. 1219.
JANAF = {
    1000: (121.048, 37.386),
    1100: (126.510, 43.118),
    1200: (131.573, 48.938),
    1300: (136.300, 54.844),
    1400: (140.741, 60.837),
    1500: (144.934, 66.915),
    1600: (148.912, 73.079),
    1700: (152.699, 79.325),
}
TEMPERATURES = (1000, 1100, 1200, 1300, 1400, 1500, 1600, 1650)
PRINTED_G_RESIDUALS = (0.00088, 0.00817, 0.02278, 0.04272, 0.06385, 0.08301, 0.09934, 0.05971)


def nasa_g_kj_mol(t: float) -> float:
    a1, a2, a3, a4, a5, a6, a7 = NASA_A
    h_over_rt = -a1 / t**2 + a2 * math.log(t) / t + a3 + a4 * t / 2 + a5 * t**2 / 3 + a6 * t**3 / 4 + a7 * t**4 / 5 + NASA_B1 / t
    s_over_r = -a1 / (2 * t**2) - a2 / t + a3 * math.log(t) + a4 * t + a5 * t**2 / 2 + a6 * t**3 / 3 + a7 * t**4 / 4 + NASA_B2
    return R * t * (h_over_rt - s_over_r) / 1000


def janaf_g_kj_mol(t: int) -> float:
    if t == 1650:
        low, high = JANAF[1600], JANAF[1700]
        fraction = (t - 1600) / 100
        entropy = low[0] + fraction * (high[0] - low[0])
        enthalpy_increment = low[1] + fraction * (high[1] - low[1])
    else:
        entropy, enthalpy_increment = JANAF[t]
    return JANAF_DH298_KJ_MOL + enthalpy_increment - t * entropy / 1000


def main() -> None:
    calculated = tuple(nasa_g_kj_mol(t) - janaf_g_kj_mol(t) for t in TEMPERATURES)
    for t, value, expected in zip(TEMPERATURES, calculated, PRINTED_G_RESIDUALS):
        printed = f"{value:+.5f}"
        assert printed == f"{expected:+.5f}", (t, printed, expected)
        print(f"| {t} | {printed} | ")


if __name__ == "__main__":
    main()
