# t-1117 — attribution route check at `work-v064-green` `8089eadbf` (report only)

Author: regolith-empirical, 2026-10-04 ~17:00 ET. **Read-only; no reader/extract change in this commit.**

## Route today
`migrate.py:11393–11404` (`_migrate_extract_observation` → `evidence_for`, `migrate.py:5364`):
`attribution = values.attribution` (non-empty str) **else** `obs.quote` (str) **else** None.
`evidence_for` keeps a literal `quoted_attributed` method_class as QUOTED_ATTRIBUTED even when attribution is None;
other quoted-family method classes (`quoted_literature`, `quoted_prior_work`, `quoted_measurement`, `quoted_from_other_workers`…)
map to QUOTED_ATTRIBUTED only if attribution is truthy, else QUOTED_UNATTRIBUTED (`migrate.py:5444–5455`).
`validate.py:1130–1136` raises `conditional_field: quoted_attributed requires attribution` (hard).

The attribution-reader landing (values.attribution first) is in place. **One route remains open:** extracts that
name the quoted source under `values.source_attribution` or `values.quoted_from` (19 extracts, 74 rows) are not read.

## 1. quoted_attributed rows that name nobody (derived store at tip)
Scan of `extracts-v2/` + `observations-v2/` (236 `quoted_attributed` records):
- **23 records, attribution empty** (all hard `conditional_field` issues today; literal method_class `quoted_attributed`):

| extract | observation(s) | where the name actually sits |
|---|---|---|
| 2010zahnle-schaefer-fegley… | zahnle_2010_ci_volatile_composition, _impact_degas_velocity_threshold, _ordinary_chondrite_volatile_composition | `values.source_attribution` (str / list) |
| cooper-2007-sintering-lunar-simulant | _apollo14_glass_sintering_minutes_800c, _jsc1_sintered_brick_strength, _jsc1a_sintering_1050c_three_hours, _jsc1a_sintering_1100c_quoted | `values.quoted_from` |
| jsc-lunar-catalog-12023 | jsc_12023_glass_bead_ages, _provenance_and_characterisation, jsc_12024_coarse_particle_masses, _maturity_index | `values.quoted_from` |
| lpi-compendium-15013 | lpi_15013_lm_exhaust_release_context | `values.source_attribution` |
| jsc-lunar-catalog-12023 | jsc_12023_carbon_nitrogen_compilation (5 row children) | row-level `rows[].source` only |
| ammin-76-904-lange-1991 | ammin_76_904_quoted_comparator_enthalpy_values | `values.cited_values[].source` |
| hashimoto-nakano-2021-bubbles-to-chondrites-ii | hashimoto_2021_appendix_sample_provenance | `values.source` = token `Jarosewich_1990_method_description_as_reported_by_authors` |
| lpi-compendium-15013 | lpi_15013_maturity_and_grain_size | `values.maturity_index_source` |
| lpi-compendium-15013 | lpi_15013_carbon_and_nitrogen | **no name field at all** |
| lpi-compendium-65701 | lpi_compendium_65701_attributed_trace_and_exposure_values | six `values.*_source` keys |
| itoh-hino-banya-1998-spinel | itoh_1998_quoted_prior_interaction_parameters | **no name field at all** |

12 are closed by the alias route below; **11 need extract repair** (canonical `values.attribution`, page-checked) — not done here.
- **16 records** have attribution text with no author/year pattern on a regex screen; on reading, 15 (usgs-b1544 Table 1
  `helgeson_corrected`) do name "Helgeson and others"; **1 names nobody**: `tachibana-tsuchiyama-1998-forsterite-dust-lpsc::tachibana_1998_forsterite_prior_experiment_alpha_constraint`
  ("experimental studies [1-4] as summarized by the authors" — came from the `quote` fallback).

## 2. Blind run of the proposed rule (before any code)
Rule: `attribution = values.attribution` else `values.source_attribution` else `values.quoted_from` (str, or list of
non-empty str joined "; ") else `obs.quote`. Mappings and nested/row-level keys not read. Census: `t1117-alias-attribution-census.tsv`.

| outcome | v2 records |
|---|---|
| NOOP (canonical attribution or quote already wins) | 79 |
| FILL attribution, class unchanged | 23 (12 quoted_attributed hard issues cleared; 11 figure_only / model_derived / compilation_assessed) |
| **FLIP quoted_unattributed → quoted_attributed** | **95** (De Maria 1973 31, Schaefer & Fegley 2011 Table 2 38, Voropaev 2023 Table 4 8, Kato 1993 5, Turkdogan 2001 5, de Guzman 2026 3, Ohara 1987 2, Furukawa 1976 1, Yamada 1983 1, Nunoue 1987 1) |

Blind read of every alias value (74 rows): **correct 73 / refused 0 / WRONG 0 / ambiguous 1**
(`turkdogan_2001_p2o5_about_minus_15_to_18_quoted`: "author's recent publication Ref. 2" — a self-citation; model_derived, class unchanged).
Row-level `source_attribution: 'Per-row footnote attribution below; planets without a footnote are unattributed.'`
(schaefer-fegley-2011 rows) is **not** a name and is **not** read by the rule (values-level only).

## Ask before code
The 95 evidence-class flips change what scores as attributed quotation. Rule needed: (a) read the two alias keys in
the reader (one helper beside the existing route; pin first), or (b) rename the keys to `values.attribution` in the
19 extracts (extract-only, no reader change). Either way the 11 rows in §1 need extract repair from the page.
