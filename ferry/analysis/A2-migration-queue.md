# A2 — Migration-queue analysis for `regolith-empirical`

Generated 2026-09-22 21:11 EDT from `data/battery/migration-queue.yaml` at detached `origin/review/r6-r8-fix` (`1f8df6cbe`). No product files were changed and no product commit was made.

## Executive readout

| Measure | Count |
|---|---:|
| Queue `why` entries | **130,257** |
| Distinct exact reason strings | **21,490** |
| Top-20 coverage | **61,740 (47.4%)** |
| Top-20 ENGINEERING / DATA / PERMANENT | **14 / 3 / 3** |

The queue is dominated by a small number of systematic migration-boundary issues, not by 130k independent extraction failures. Counts below are exact reason-string counts; axes are not merged when the reason text differs. In particular, ranks 5–6 are the same underlying vaporization-reaction gap emitted on `quantity` and `value`.

Classification means:

- **ENGINEERING** — existing source payload/provenance is sufficient; mapper, schema projection, or index resolution can close it without re-extraction.
- **DATA** — page/source grounding or re-extraction is needed before a trustworthy value can be admitted.
- **PERMANENT** — the source has a true absence or invalid/nonphysical input; retain a typed unknown/refusal rather than inventing a value.

## Top 20 reasons and proposed fixes

| Rank | Count | Axis | Class | Exact reason | Proposed fix |
|---:|---:|---|---|---|---|
| 1 | 24,742 | `phase` | **PERMANENT** | `source does not state phase` | Preserve `State.unknown` and the queue item. Do not infer phase from formula, section, or neighboring rows; only close if a source-grounded extraction later states it. |
| 2 | 14,156 | `quantity` | **ENGINEERING** | `printed compilation columns are not mapped to a closed quantity` | Add a compilation-column/record-kind mapping (or a source-specific generator) for the already printed columns; emit the selected closed `Quantity` and keep ancillary columns as non-selected metadata. |
| 3 | 3,442 | `quantity` | **ENGINEERING** | `ATcT formation enthalpy is not a v2.1 Quantity token` | Add an explicit ATcT formation-enthalpy projection/quantity token, or make the existing expression representation first-class in the v2.1 adapter; retain published units and provenance. |
| 4 | 2,111 | `quantity` | **ENGINEERING** | `delta_fH is not a v2.1 Quantity token; stored as expression` | Define the canonical `delta_fH` schema projection and update the selector/validator so the stored expression is an admitted observation rather than a migration queue failure. |
| 5 | 1,880 | `quantity` | **ENGINEERING** | `the table value is the Gibbs energy of the vaporization reaction and the reaction identity is not lifted` | Lift the reaction identity already described by the ledger note/rail metadata into structured reactants/products and reaction basis; map the table value to the reaction-aware quantity. |
| 6 | 1,880 | `value` | **ENGINEERING** | `'unknown: the table value is the Gibbs energy of the vaporization reaction and the reaction identity is not lifted'` | Reuse the rank-5 reaction-aware value resolver and stop wrapping its typed refusal as a second opaque value reason once the structured reaction is present. |
| 7 | 1,855 | `read_from` | **ENGINEERING** | `locator source_path 'data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json' has no matching INDEX asset` | Teach INDEX/source-file resolution to register compilation record assets (or resolve a record path to its compilation asset) before falling back to `unknown`; no PDF re-extraction is needed for an existing record. |
| 8 | 1,797 | `phase` | **ENGINEERING** | `phase string '(g)' is not in the closed automatic map` | Add a lossless alias for `(g)` to the canonical gas phase, with a regression test covering the published spelling. |
| 9 | 1,634 | `value` | **PERMANENT** | `'delta_fG: printed field table_kJ_mol is null; absence is not a measured zero'` | Keep the value unavailable and explicitly absent. Do not coerce null to zero; only a later source-grounded value can close it. |
| 10 | 1,409 | `temperature_K` | **PERMANENT** | `nonpositive temperature is not a physical T; stored unknown` | Retain the typed invalid-temperature refusal. Verify during any future extraction, but do not substitute 0 K, 298.15 K, or another midpoint. |
| 11 | 1,181 | `phase` | **ENGINEERING** | `phase string '(c)' is not in the closed automatic map` | Add a lossless alias for `(c)` to the canonical condensed phase and test it alongside `(g)`. |
| 12 | 994 | `method` | **DATA** | `regime 'kems_effusion' is not a closed method token (kems_effusion alone is insufficient)` | Re-extract/page-ground the experimental regime (for example, equilibrium, open-furnace, or another closed token). If the source truly gives only `kems_effusion`, leave it unknown after the data pass. |
| 13 | 912 | `temperature_K` | **ENGINEERING** | `source T_range_K [300.0, 6000.0]; no midpoint invented` | Preserve the stated range as a range/domain condition and add range-aware identity/selection support; never manufacture a point temperature from the interval. |
| 14 | 756 | `derived_from` | **DATA** | `derived evidence class with unstated ancestry; queued for page-grounding` | Page-ground the derivation and record resolvable parent observation IDs (or an explicit source derivation statement); do not mint ancestry from prose alone. |
| 15 | 712 | `derivation` | **DATA** | `derived evidence class with unstated derivation; queued for page-grounding` | Re-extract the derivation equation/assumptions and attach the source locator and inputs; retain the refusal until that evidence exists. |
| 16 | 609 | `value` | **ENGINEERING** | `'source column census (T: 10 numeric cells, $Cp^o$: 10 numeric cells, $S^o$: 10 numeric cells, $H^o-H_{298}^o$: 10 numeric cells, $\Delta Hf^o$: 10 numeric cells, $\Delta Gf^o$: 10 numeric cells); declared quantity is unknown; no mapped coordinate/value pair selected'` | Add deterministic label-to-quantity mappings for this compilation table and route each declared column to its own observation; never select the first numeric column. |
| 17 | 458 | `value` | **ENGINEERING** | `'source column census (substance: 0 numeric cells, cp_10_k: 1 numeric cells, cp_25_k: 1 numeric cells, cp_50_k: 1 numeric cells, cp_100_k: 1 numeric cells, cp_150_k: 1 numeric cells, cp_200_k: 1 numeric cells, cp_298_15_k: 1 numeric cells, entropy_third_law: 1 numeric cells, entropy_spectrographic_or_molecular_constants: 0 numeric cells, entropy_other_sources: 0 numeric cells, entropy_recommended: 1 numeric cells); declared quantity is unknown; no mapped coordinate/value pair selected'` | Add the source-specific quantity/column schema for heat-capacity and entropy columns, then explode each unambiguous printed series with its published temperature basis. |
| 18 | 421 | `phase` | **ENGINEERING** | `transition spans phases named by "CRYSTAL <--> LIQUID" (cr -> l); v2.1 species.phase has one phase axis and cannot hold both` | Add a transition representation with `from_phase`/`to_phase`, or have the transition generator emit two explicitly linked endpoint states; do not collapse the pair into one phase. |
| 19 | 419 | `value` | **ENGINEERING** | `'source column census (substance: 0 numeric cells, cp_10_k: 0 numeric cells, cp_25_k: 0 numeric cells, cp_50_k: 0 numeric cells, cp_100_k: 0 numeric cells, cp_150_k: 0 numeric cells, cp_200_k: 0 numeric cells, cp_298_15_k: 1 numeric cells, entropy_third_law: 0 numeric cells, entropy_spectrographic_or_molecular_constants: 1 numeric cells, entropy_other_sources: 0 numeric cells, entropy_recommended: 1 numeric cells); declared quantity is unknown; no mapped coordinate/value pair selected'` | Add the corresponding entropy-column mapping and require a declared quantity before selecting a value; keep the zero-cell columns out of the emitted observation. |
| 20 | 372 | `phase` | **ENGINEERING** | `phase string 'K2O-SiO2_binary_silicate_melt' is not in the closed automatic map` | Add a canonical system/melt phase representation or a source-specific normalization for this published system label, preserving the original spelling and composition context. |

## Ranked recommendations

1. **Close the compilation adapter boundary first (ranks 2–4, 16–17; 20,776 entries).** Add explicit quantity/column mappings and canonical projections. This is the largest clearly engineering-resolvable cluster and should eliminate the unsafe “pick a number” path without re-extracting the tables.
2. **Preserve reaction-aware vaporization observations (ranks 5–6; 3,760 entries).** Promote the existing rail/ledger reaction notes to structured identity and deduplicate the quantity/value refusal pair.
3. **Normalize phase vocabulary and transition identity (ranks 8, 11, 18, 20; 3,771 entries).** Add aliases plus a two-phase transition/system representation; keep original spellings in provenance.
4. **Repair source-asset resolution (rank 7; 1,855 entries).** Index compilation records as readable assets or add a deterministic compilation-path resolver, then rerun the provenance validator.
5. **Run a focused data-grounding pass (ranks 12, 14–15; 2,462 entries), while retaining permanent refusals (ranks 1, 9–10).** Re-extract only method/derivation/ancestry claims. Do not spend extraction effort trying to recover values the source explicitly omits or invalidates.

## Method and scope

The queue was scanned directly from `data/battery/migration-queue.yaml` after detaching to `origin/review/r6-r8-fix`. Each top-level `why` field is one counted item; folded YAML continuation lines were joined and whitespace-normalized. Counts are not deduplicated by `work_id`, observation, source, or axis. The report is analysis-only and intentionally leaves the worktree product state unchanged.
