# SiO Alpha Surface Convergence

Date: 2026-05-19

Scope: Phase 1 of the SiO characterization campaign. This lands the
YAML-backed Hertz-Knudsen evaporation-alpha surface only. It does not refresh
Phase 2 yield goldens.

## Alpha Surface

| species | alpha | source | T_band_K | envelope |
|---|---:|---|---|---|
| SiO | 0.04 | SF2004 Table 10 SiO2(liq), Hashimoto 1990 | [1873, 2373] | [0.003, 0.048] |
| Fe | 0.5 | SF2004 Table 10 Fe(liq) | [1873, 2373] | [0.3, 0.7] |
| Mg | 0.8 | SF2004 Table 10 Mg(liq) | [1873, 2373] | [0.6, 1.0] |
| Na | 1.0 | SF2004 Table 10 Na(g) over silicate | [1700, 2273] | [0.9, 1.0] |
| K | 1.0 | SF2004 Table 10 K(g) over silicate, analogous to Na | [1700, 2273] | [0.9, 1.0] |

The values are constants by species for this chunk. The envelope is a sanity
constraint, not a fitting target. Tests assert the YAML value remains inside
the literature envelope and that each alpha block carries source provenance.

## Chemistry Boundary

Phase 1 alpha correction does NOT move §25-bis-SiO pass count by design.
§25-bis is a VAPOR_PRESSURE benchmark; alpha is a flux correction. The correct
Phase 1 result is that §25-bis remains 1/25 at the existing vapor-pressure
acceptance gate. Widening the 1-decade vapor-pressure tolerance would confuse a
kinetic flux correction with P_sat calibration.

CJ2015 mixed-sign residuals at 1700-2000 K remain classified as VapoRock
activity-coefficient drift, not a kinetic miss. This alpha surface is an
upper-envelope diagnostic on Hertz-Knudsen yield, not a point-fit against those
vapor-pressure anchors.

Phase 2 goldens refresh is intentionally deferred to the controller-mediated
follow-up after the Phase 2 worktree lands. That later refresh should show the
expected yield drop from alpha(SiO)=0.04 without changing the §25-bis vapor
pressure pass accounting.
