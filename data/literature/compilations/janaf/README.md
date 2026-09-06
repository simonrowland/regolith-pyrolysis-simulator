# NIST-JANAF 4th edition — compilation home

This directory is a **compilation home**: assessed thermochemical functions the
engine may consume as reference input. It is **not** a validation source and
produces **no** battery scoring rows
(`battery_refusal: gibbs_table_not_runtime_observable`).

## Provenance

- **Database:** NIST-JANAF Thermochemical Tables, Fourth Edition (Chase 1998), NIST Standard Reference Database 13.
- **Official URL:** https://janaf.nist.gov/
- **Formula index:** https://janaf.nist.gov/formula.html (1796 species rows observed 2026-09-06).
- **DOI:** 10.18434/T42S31
- **Citation:** Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, J. Phys. Chem. Ref. Data Monograph 9 (1998).
- **Licence (quoted):**
  - NIST SRD 13 data.nist.gov (https://data.nist.gov/od/id/ECBCC1C1301D2ED9E04306570681B10735): “These data are public.”
  - NIST copyrights (https://www.nist.gov/copyrights-disclaimers): “With the exception of material marked as copyrighted, information presented on NIST sites are considered public information and may be distributed or copied.”
- **Source bytes:** not in git. SHA-256 of each official `.txt` is in `source/sidecar.yaml`. The cache lives at `source-cache/` (gitignored; relative path `data/literature/compilations/janaf/source-cache/`).
- **Harvester:** `tools/harvest_janaf_compilation.py` parses the live NIST `.txt` format (`T(K)` header, tab-delimited). Table IDs come from per-element `tables/{El}-index.html` pages because live `formula.html` has no per-table links.
- **Loader:** `simulator/reference_data/janaf.py`.

## Native structure (no conversion)

One file per table in `tables/{El}-{NNN}.yaml`:

- 8-column rows as published: T, Cp, S, −[G−H(Tr)]/T, H−H(Tr), ΔfH, ΔfG, log Kf.
- Every number is stored as `as_published` plus a parsed `value`. `INFINITE` and blank cells are `value: null`, never guessed.
- Short rows (phase transitions, λ maxima, fugacity notes) stay under `parse_ambiguities` with the raw line.
- No unit conversion, no smoothing, no merging of phases.

Two harvest eras share this directory:

| era | files | date | source |
|---|---:|---|---|
| HTML | 380 | 2026-08-27 | NIST HTML table (JSON-in-`.yaml`) |
| `.txt` | 1275 | 2026-09-06 | NIST tab-delimited `.txt` |

The 380 HTML-era files were **not** overwritten by the `.txt` harvest. Current NIST `.txt` files contain additional temperature rows the HTML parse dropped. Overlapping published tokens match; missing rows are listed in `html-era-txt-divergence.yaml`. Both versions are kept: HTML YAML here, `.txt` bytes in `source-cache/`.

## Formulas

`formula` and `formula_as_published` are the printed formula, verbatim. Decimal subscripts are kept (wüstite is `Fe0.947O`, not `FeO`). `formula_normalised` strips trailing ion charge, flattens `_{n}` subscripts, and drops integer subscript-1; it does **not** integerise decimals. The eleven non-stoichiometric tables are listed on the manifest as `non_stoichiometric_formulas`.

## Known gaps

NIST-JANAF has **no** `{El}-index.html` (HTTP 404) for these feedstock elements — a JANAF corpus gap, not a harvest miss:

Ag, As, Au, Bi, Cd, Ce, Dy, Er, Eu, Gd, Ge, In, La, Lu, Nd, Pt, Sb, Sc, Se, Sm, Sn, Te, Th, U, Y, Yb

140 official-index tables contain Be, D, Hg, Kr, Xe, or Rn and were left out of this feedstock-element harvest.

The legacy extract `data/literature/extracts/janaf-4th.yaml` is still a battery input (b-481 / t-850). It is not this compilation and was not deleted.

## Engine use

Consume as assessed ΔfG / ΔfH / Cp / S / log Kf tables (Nernst, Ellingham, vapour-pressure reference). Do not score the battery against these tables.
