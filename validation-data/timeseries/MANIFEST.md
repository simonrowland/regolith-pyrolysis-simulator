# Time-Series Validation Data Lake

Canonical CSV columns are listed in `catalog.yaml`. This lake is artifact-backed; unavailable DS-002 and DS-012 are explicit gaps.

| ID | status | rows | time rows | T rows | signals | gap |
|---|---|---:|---:|---:|---|---|
| DS-001 | validated-scale-free | 1288 | 1288 | 1288 | residue_wt_pct | time-series residue depletion; geometry/inventory absent |
| DS-002 | unavailable-automation | 0 | 0 | 0 |  | HTTP 403 / paywalled SI artifact; documented gap, skipped |
| DS-003 | validated-scale-free | 335 | 335 | 335 | isotope_delta, mass_loss_pct, residue_wt_pct | time-series residue and mass-loss depletion; geometry/inventory absent |
| DS-004 | not-time-series | 205 | 0 | 205 | isotope_delta, mass_loss_pct, residue_wt_pct | numeric residues but no time column |
| DS-005 | validated-scale-free | 114 | 114 | 114 | isotope_delta, mass_loss_pct | time-series Na/K mass-loss; K effective p_eq uses builtin provider with synthetic single-parent-oxide activity and fixed validation pO2 context |
| DS-006 | validated-scale-free | 150 | 150 | 150 | mass_loss_pct, residue_wt_pct | time-series bulk/Fe residue depletion; geometry/inventory absent |
| DS-007 | validated-alpha | 16 | 0 | 16 | evaporation_coefficient | direct evaporation-coefficient alpha rows |
| DS-010 | validated-direct-flux | 8 | 0 | 8 | evaporation_flux_molecules_cm2_s | direct forsterite formula-unit evaporation flux; compared dimensionally to runtime SiO volatile proxy with no H2O/H2-to-pO2 conversion or fitting |
| DS-012 | unavailable-automation | 0 | 0 | 0 |  | Mendeley download is session-gated / landing-page HTML; documented gap, skipped |
| DS-014 | not-time-series | 2815 | 0 | 0 | residue_wt_pct | numeric residues but no time/T/fO2 condition map |
