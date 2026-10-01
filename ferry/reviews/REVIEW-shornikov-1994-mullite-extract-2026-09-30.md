# REVIEW — review/shornikov-1994-mullite-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** no worktree checkout. Commit read from the object store (`git show 9bb714cf`) into `/tmp/mullite-review`. In-flight slots (including slot-b565, slot-n1, slot-r29, slot-z14, slot-z15, slot-z2) were not touched.
- **Tip:** `9bb714cf13bbf59cb20ac9f6f5fba0dc3ace36f8` (parent `bba8ef156161d32235e105606db553b15519ec49`)
- **Commit:** `Add Shornikov 1994 mullite KEMS extract` — two files only: `data/literature/extracts/shornikov-1994-mullite-kems.yaml` and `data/literature/extracts-v2/shornikov-1994-mullite-kems.yaml`
- **Date:** 2026-09-30 ~20:11 ET
- **Mode:** read-only; extract not edited; not landed onto green

## Corpus

Page images, PDF, and OCR for Shornikov, Stolyarova & Shultz, Rapid Commun. Mass Spectrom. 8 (1994) 478–480 are **not on this machine**.

Searched `/workspace` (including `/workspace/ferry-inbox/reviews/_req-2026-09-30/`, which has kits for 1995, 1996, and 1997 but not this paper) and the repo trees (green `6afd8f990`, parent `bba8ef156`, this tip). No `raw/shults-1994-mullite-evaporation` directory and no Doklady 1994 mullite extract. **No image or text-layer check was done.** Numbers below are the extract’s own cells and internal scale arithmetic, not a page confirmation.

## Census (extract, not the page)

29 admitted observations:

| Block | n | T (K) | evidence (v2) |
| --- | ---: | --- | --- |
| Table 2 p_Al | 4 | 1833, 1933, 1983, 2033 | measured_direct |
| Table 2 p_AlO | 3 | 1933, 1983, 2033 | measured_direct |
| Table 2 p_Al2O | 3 | 1933, 1983, 2033 | measured_direct |
| Table 2 p_AlSiO | 3 | 1933, 1983, 2033 | measured_direct |
| Table 2 p_SiO | 4 | 1833, 1933, 1983, 2033 | measured_direct |
| Table 2 p_O | 4 | 1833, 1933, 1983, 2033 | model_derived |
| Table 6 a_Al2O3 | 4 | same four T | model_derived |
| Table 6 a_SiO2 | 4 | same four T | model_derived |

All four temperatures in the file are below the incongruent-melting figure the extract quotes (2163 ± 4 K). That melting figure was not checked against a page.

Printed ± is retained on every point, including all four `a_SiO2` cells (`0.37 ± 0.09`, `0.19 ± 0.02`, `0.18 ± 0.02`, `0.17 ± 0.01` as stored). `pressure_atm` matches printed coefficient × the scale written in `units` (Al/AlO/Al2O `10^-8`, AlSiO `10^-10`, SiO `10^-5`, O `10^-9`) to floating-point noise. v2 Pa points match `pressure_atm × 101325`. Activity points and sigmas match the printed coefficients. This is internal consistency only.

## Check 1 — phase, not melt

**Pass on what is typed.** Every v1 row `phase` is `mullite (3Al2O3·2SiO2); phase not specified by the article`. Provenance `sample_phase` says the paper does not label solid or liquid and that all Table 2 and Table 6 temperatures are below the stated incongruent melting point. No row is tagged solid, liquid, or melt.

v2: the eight oxide activities keep species phase `unknown` (that phase string is not in the closed map). The 21 partial pressures are species phase `g` (the vapor), not a condensed melt. `reference_state` on the activities is `unknown`, so `_fusion_comparison_reference` does not rewrite them onto a liquid pure-oxide standard. Composition is untyped, and this commit has no work/experiment file, so the melt-activity contract refuses (`composition_incomplete` / unresolved experiment) rather than emitting a melt residual. Quantity `activity` still sits on the melt-activity rail by the scorer’s quantity map; that is the rail for every activity, not a phase tag of melt.

## Check 2 — a_SiO2 relation and standard state

v1 derivation, same text on all four SiO2 rows:

`a_SiO2 = (p_SiO × p_O) / (p_SiO° × p_O°)`

with `p_i` over mullite and `p_i°` over the pure oxide, pure-oxide pressures not printed in Table 2. Standard-state prose: “SiO2 activity relative to the pure oxide reference defined by p_i° in equations (12)–(13).” Al2O3 uses `a_Al2O3 = (p_Al^2 × p_O^3) / ((p_Al°)^2 × (p_O°)^3)` and the same pure-oxide wording.

v2 `derived_from` for those activities resolves inside the file: a_SiO2 → same-T `p_SiO` + `p_O`; a_Al2O3 → same-T `p_Al` + `p_O`. v2 `derivation.relation` is only `as_published` (the equation string is not copied). v2 `reference_state` is unknown, not a typed Raoultian crystal or liquid. Stored activity values are the printed Table 6 numbers, not a recomputation. Equation (12)–(13) was **not** checked against the paper.

## Check 3 — +4 conditional_field

**Legitimate ancestry gap. Not a wrong live number. P2.**

The only `model_derived` observations with `derived_from` absent are the four `p_O` rows. v2 derivation on those rows is `atm_to_Pa` only. v1 does carry a prose derivation (p_O calculated from vapor-phase equilibria, including MoO3 ⇌ MoO2 + O and O2 ⇌ 2O, reactions (1)–(6)) and `method_class: derived`, but no parent observation ids. J02 (`validate.py`: model_derived requires `derived_from`) therefore emits one hard `conditional_field` per O row → **+4**. The eight activities are model_derived and do have resolvable `derived_from`, so they are not part of the +4.

`_printed_pressure_uncertainty_dex` does not consume the absolute `verbatim.sigma` (no printed percent envelope), so the atm-unit sigma copied beside the Pa point does not enter the KEMS dex band. Noted, not raised.

## Check 4 — Doklady 1994 overlap

No second row set. `raw/shults-1994-mullite-evaporation` is absent from this machine, from green, and from parent `bba8ef156`. Green extracts have no Doklady 1994 mullite source. This tip is the only `shornikov-1994-mullite-kems` file. Its provenance names Shul'ts, Shornikov & Stolyarova, Doklady Physical Chemistry 336 (1994) 95–98, says Doklady Table 2 repeats the Al, AlO, Al2O, and AlSiO rows, and says not to merge or rescale (Doklady Al at 1833 K rounded; Al2O scale label differs). That overlap note was not checked against a Doklady file, because none is here. Nothing in the store double-counts these rows today.

## P1 / P2

- **P1:** none
- **P2:** Add structured `derived_from` (equilibrium parents, not only `atm_to_Pa`) on the four author-calculated O observations `shornikov_1994_o_partial_pressure_{1833,1933,1983,2033}k`, so the migrated points clear the J02 `conditional_field`. Do not type the condensed sample as melt, and do not type a solid pure-oxide `reference_state` unless the page actually says crystal: an untyped reference state is what currently blocks the solid→liquid fusion rewrite.

Page images were not available, so this revise is not a claim that any printed coefficient is wrong.

VERDICT: REVISE
