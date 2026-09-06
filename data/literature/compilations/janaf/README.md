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
- **Source bytes:** not in git. SHA-256 of each official `.txt` is in `source/sidecar.yaml`; each record's `extraction.source_cache_path` locates available bytes. Round 3 verified 1461/1655 hashes (1081/1275 TXT-era, all 380 HTML-era comparison sources) in recovered local caches. The requested regolith-corpus raw/text directories supplied none; 194 sources remain unavailable and explicitly unverified.
- **Harvester:** `tools/harvest_janaf_compilation.py` parses the live NIST `.txt` format (`T(K)` header, tab-delimited). Table IDs come from per-element `tables/{El}-index.html` pages because live `formula.html` has no per-table links.
- **Loader:** `simulator/reference_data/janaf.py`.

## Native structure (no conversion)

One file per table in `tables/{El}-{NNN}.yaml`:

- 8-column rows as published: T, Cp, S, −[G−H(Tr)]/T, H−H(Tr), ΔfH, ΔfG, log Kf.
- Every number is stored as `as_published` plus a parsed `value`. `INFINITE` and blank cells are `value: null`, never guessed.
- Short rows (phase transitions, λ maxima, fugacity notes) stay under `parse_ambiguities` with the raw line.
- No unit conversion, no smoothing, no merging of phases.

Original harvest provenance:

| era | files | date | source |
|---|---:|---|---|
| HTML | 380 | 2026-08-27 | NIST HTML table (JSON-in-`.yaml`) |
| `.txt` | 1275 | 2026-09-06 | NIST tab-delimited `.txt` |

All 380 HTML-era loadable records now come from hash-verified current TXT. All 2641 documented missing temperature points were restored, plus 325 rows with blank formation cells. The unmatched K-024 HTML annotation row is retained only in historical provenance; the current source line remains a parse ambiguity. Historical HTML-derived records remain in git at `c4aebdeb64d978b66df64cf09cae97b041042fc0`; their original HTML bytes remain unverified. `html-era-txt-divergence.yaml` preserves the historical comparison. Current loadable census: 1655 tables, 76549 rows. The 194 unavailable TXT-era records retain their prior values with explicit source-unavailable ambiguities.

## Formulas

`formula` and `formula_as_published` come verbatim from the table title, never an element index. The parenthesized name formula is used when its composition agrees with the title's explicit composition token; otherwise that explicit printed token is used (including hydrates). Unparseable formulas are refused and reported as manifest ambiguities. Decimal subscripts are kept (wüstite is `Fe0.947O`, not `FeO`). `formula_normalised` removes only the terminal charge sign, flattens `_{n}` subscripts, and drops integer subscript-1; it never deletes molecular subscripts or integerises decimals. `charge` stores -1, 0 or +1 separately. The eleven non-stoichiometric tables are listed on the manifest as `non_stoichiometric_formulas`.

Source round-trip tests compare freshly parsed source tokens and row multiplicity against loadable records, then check numeric serialization and manifest counts. Missing source files produce explicit test skips, not source-fidelity certification. Negative controls cover a broken parser, stale token/value pairs, empty stored rows, and a corrupted token in a scratch copy of Al-006's real source.

## Known gaps

NIST-JANAF has **no** `{El}-index.html` (HTTP 404) for these feedstock elements — a JANAF corpus gap, not a harvest miss:

Ag, As, Au, Bi, Cd, Ce, Dy, Er, Eu, Gd, Ge, In, La, Lu, Nd, Pt, Sb, Sc, Se, Sm, Sn, Te, Th, U, Y, Yb

140 official-index tables contain Be, D, Hg, Kr, Xe, or Rn and were left out of this feedstock-element harvest.

The legacy extract `data/literature/extracts/janaf-4th.yaml` is still a battery input (b-481 / t-850). It is not this compilation and was not deleted.

## Engine use

Consume as assessed ΔfG / ΔfH / Cp / S / log Kf tables (Nernst, Ellingham, vapour-pressure reference). Do not score the battery against these tables.
