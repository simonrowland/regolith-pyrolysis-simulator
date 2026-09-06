# SGTE Unary 5.0 — free pure-elements database

This directory is a **compilation home**: assessed Gibbs-energy functions the
engine may consume as reference input. It is **not** a validation source and
produces **no** battery scoring rows
(`battery_refusal: gibbs_table_not_runtime_observable`).

## Provenance

- **Database:** SGTE Pure Element Database (UNARY) version 5.0, 2 June 2009.
- **File:** `unary50.tdb` (Thermo-Calc TDB), 118 145 bytes.
- **Official URL:** https://www.sgte.net/en/free-pure-elements-database
- **Download:** `unary50.tdb` (115.4 KiB) from sgte.net without login.
- **Citation:** Dinsdale, A. T. (1991). SGTE data for pure elements. CALPHAD 15, 317–425. DOI 10.1016/0364-5916(91)90011-S. The 1991 paper is Elsevier-paywalled and was **not** downloaded; this ingest is the official free TDB only.
- **Local copy:** `source/unary50.tdb` (sha256 `8e38dcefbeaad1f8ed83ed1f8ccceb0e1701fb584f1bf3798f217488253d4b3f`) and the corpus sidecar `source/sidecar.yaml`.
- **Licence (verbatim from the official SGTE free-pure-elements page):**
  > This database can only be used for extracting data for assessment work or to tabulate or plot data for the pure elements.

## Native structure (no conversion)

Records keep Thermo-Calc TDB form:

- `ELEMENT` symbol, reference phase, mass, H298−H0, S298 as published.
- `FUNCTION` temperature intervals with the original expression **string** plus a parsed polynomial `a + b T + c T ln T + Σ k_n T**n + Σ f_i FUNCTION_i` and the exponent set.
- `PHASE` / `CONSTITUENT` declarations (including magnetic `TYPE_DEFINITION` AF, p when present).
- `PARAMETER G(phase,element;0)` (and `TC`, `BM`, `BMAGN`) with the same interval encoding.

No unit conversion, no smoothing, no merging of phases. Ions are not in this unary file; condensed metastable phases, GAS N2/O2, ELECTRON_GAS, and VACUUM are ingested.

One YAML file per `(element, phase)` in `records/`. The harvester is `tools/harvest_sgte_unary_compilation.py`; the loader is `simulator/chemistry/sgte_unary.py`.

## Known gaps and ambiguities

Ambiguities are fields, not guesses:

- ELEMENT reference-phase tokens that are not PHASE names: `N` `1/2_MOLE_N2(G)`, `O` `1/2_MOLE_O2(G)`, `NP` `ORTHORHOMBIC_AC` vs PHASE `ORTHO_AC`, `SM` `RHOMBOHEDRAL_SM` vs PHASE `RHOMB_C19`.
- PHASE declared `LIQUID:L` while PARAMETER identifiers use `LIQUID`.
- Magnetic moment identifier published as both `BM` and `BMAGN`.
- Commented-out superseded `PARAMETER G` for `OS` and `RU` BCC_A2; live statement ingested, comment kept.
- Boron `GBCCB` / `GFCCB` / `GHCPB` remain as FUNCTIONs after PARAMETER G for those phases was removed (file header: data removed for B BCC_A2, FCC_A1, HCP_A3). Functions are ingested as `(B, phase)` records with `function_without_g_parameter`.
- `/-` ELECTRON_GAS and `VA` VACUUM have ELEMENT lines and no G parameter.
- SPECIES `N2` / `O2` (GAS) are not ELEMENT symbols.

**Feedstock coverage:** unary50.tdb has a record for every feedstock element in `data/feedstocks.yaml` except **H, F, Cl, Br, I** (those five are absent from the TDB). The engine may consume this compilation for pure-element SER Gibbs energies, lattice stabilities, and magnetic TC/BM of the metals and metalloids it does contain — not as a measurement of vapour pressure or melt activity.

## Engine use

Consume as CALPHAD unary reference functions (Dinsdale 1991 lattice stabilities, SGTE 5.0 coefficients). Do not score the battery against these tables.
