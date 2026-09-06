# Burcat / Ruscic Third Millennium thermochemical compilation

Assessed NASA-7 polynomial coefficients for gas and condensed species, with
per-species comment blocks (literature source of each fit, uncertainties,
ATcT-updated enthalpies, dates). This is a **reference function** the engine
may consume. It is **not** a validation source and produces no battery scoring
rows (`gibbs_table_not_runtime_observable`).

## Provenance

- Burcat, A. and Ruscic, B., *Third Millennium Ideal Gas and Condensed Phase
  Thermochemical Database for Combustion with updates from Active
  Thermochemical Tables*, ANL-05/20 and TAE 960, Argonne National Laboratory /
  Technion, September 2005. Current electronic authors: Elke Goos, Alexander
  Burcat and Branko Ruscic.
- Source file: `BURCAT.THR.txt`, species last added 3 January 2023.
- Official / mirror URL: <https://respecth.elte.hu/burcat.php>
  (Technion host `burcat.technion.ac.il` was down at retrieval).
- Licence: free for non-commercial use with citation. Quoted from the THR
  header: it is strictly forbidden to include this database as-is or in part
  in any commercial database, software, firmware or hardware without written
  permission from the authors. Printed 2005 report is US DOE ANL-05/20.
- Local snapshot: `source/BURCAT.THR.txt`
- SHA-256: `42a597ff852d1a2995f81d4fdca069f3cdcaef77ead5e3c66e02e80ed6699101`
- XML cross-check: `source/BURCAT_THR.xml` (Thermodyne2XML snapshot; READ.ME
  states it is from September 2005 and **outdated** relative to THR).
  SHA-256: `0f4cfd21cfa19f620110bbe63bbb96beff112a2c5e8cf38c885c6d7b3d00a3af`
- Sidecar (licence quote and retrieval log): `source/sidecar.yaml`
- `Archives.zip` is **not** committed.

Harvest: `python tools/harvest_burcat_compilation.py`. Loader:
`simulator/reference_data/burcat.py`. NASA-7 field parsing reuses
`simulator/reference_data/nasa_glenn.py` (`parse_coeff_fields` width 15,
`parse_nasa7_header_line`, `parse_nasa7_coefficient_lines`).

## Native format (no conversion)

Each `records/BU-NNNN.json` is one BURCAT.THR species/phase record:

1. Comment block — CAS identifier line plus the preceding prose (source
   references, HF298 notes, uncertainties). Kept verbatim.
2. NASA-7 1-line header — name cols 1–18; date 19–24; four (A2,A3)
   composition slots 25–44; phase G/S/L/C col 45; T_low / T_high (2F10.3);
   quality letter + molecular weight + card `1`.
3. Three coefficient cards — five 15-character Fortran fields each: seven
   high-T `a` coefficients, seven low-T `a` coefficients, then H298/R.
   Published order matches the XML tags `range_1000_to_Tmax`,
   `range_Tmin_to_1000`, `hf298_div_r`. T_common is **not printed** on the
   header (not invented).

Comment-only CAS stanzas (HF298 notes with no 4-line polynomial) are kept as
`record_kind: comment_only` with `coefficient_count: 0`. Multiple 4-line
blocks that share a name (extra T ranges, solid vs liquid) stay separate
records. Phases are not merged. Units are not converted.

## What the engine may consume it for

Cp°/R, H°/RT and S°/R from the published NASA-7 coefficients over the
declared T_low–T_high window, plus H298/R and molecular weight as printed.
Do not score this compilation in the measurement battery.

## Known gaps and recorded ambiguities

- Phase labels: `phase_as_published` is the parenthetical name suffix when
  present (e.g. `solid`, `liquid`), else the NASA-7 G/S/L/C card character
  (also stored as `phase_card_as_published`). `phase` is the derived
  normalized label. Suffixes outside the closed allow-list map to
  `condensed` and are listed as `phase_suffix_normalized_to_condensed`.
- 39 comment-only CAS stanzas (no polynomial). Kept.
- 5 records with `N/A` in the H298/R field; 5 coefficient-card misalignments
  (including a leading `0` on `Li3+` card 4 and `E 00` missing exponent sign).
- Quality marks `?` (W) and `Bx` (C2F3O) recorded, not normalized.
- AIR and a few others print `WARNING!` in the formula slots (4-element
  NASA-7 limit). Nonstandard tokens kept.
- XML 2005 snapshot: 1364 phases; 678 coefficient matches, 111 mismatches
  (THR updated since 2005), 575 XML-only names. THR is the authority.
- Feedstock REE/PGM elements with no Burcat polynomial: As, Ce, Dy, Er, Eu,
  Gd, Hf, Ho, La, Lu, Nd, Pr, Se, Sm, Tb, Tm, Y, Yb; also Cs, Nb, Rb, Sc, Sr, U.

Nothing in this compilation is typed `measured`.
