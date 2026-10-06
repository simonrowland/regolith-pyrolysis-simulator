# REVIEW (first, full fidelity): basu-2008-feo-steelmaking @ 2305fff9eff55079224c4dd9fcbb6aaef6217f2f

Reviewer: regolith-empirical seat (Grok Bot), 2026-10-05 21:53 ET. Corpus batch 9, sent by regolith-main.
Source: Basu, Lahiri & Seetharaman, "Activity of Iron Oxide in Steelmaking Slag", Metall. Mater. Trans. B 39B (June 2008) 447-456,
doi:10.1007/s11663-008-9148-4. 10 pp, born-digital (Arbortext/Distiller 2008).

VERDICT ON COMMIT: FIX-FIRST

rows checked 238, mismatches 26, printed numbers not carried 0

Also: printed directional/qualifying statements not carried **18** (list D below). The brief (item 6) and earlier-wave bounces make these FIX-FIRST.
Plus one structural finding (S1): the extract has 0 observations, because it parks the paper's scoreable a(FeO)/h_O rows in `context`.

## Setup (what I ran, and where)
- Mirror `mac-studio-256-1:Repos/regolith-corpus.git`: `git fetch origin hunt/basu-2008-feo-steelmaking` gave tip `2305fff9eff55079224c4dd9fcbb6aaef6217f2f`, the sha I was assigned
  (parent db997fab is the claim; merge base is e1c71da5 on mirror main). Commit author: Simon Rowland. No AI or co-author trailers.
- Sparse review worktree on the Mac: `~/Repos/regolith-corpus/worktrees/rev-basu-2008-feo-steelmaking`, made with `git worktree add --no-checkout --detach` and then
  sparse-checkout of `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*` and `/text/<sid>/*` (46 MB). df at start: 69 GB free, above the 35 GB floor.
  I did not run build_index.py or migrate_pilot_extracts.py. I wrote nothing tracked. I removed the worktree after delivery.
- Green: read-only detached checkout `/Users/simonrowland/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, using the interpreter
  `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`. Note: the extractor ran its acceptance on c52b926, not on 61ec839.
- `engines/engines.local.toml` **exists** in the green-ro checkout (untracked; 1331 B, dated Oct 4 04:26). It is not used by the checks below.
- Page images: `pdftoppm -r 250 -png` of all 10 pages. I read every page image in full. For digit-by-digit checks I read zoomed crops of
  Table I (p448), Table II (p451, plus the continuation on p452), Table III (p452), Eqs [1]-[3] and Sec. III. The PDF's text layer was a second, programmatic
  cross-check (pdftotext). Where the text layer drops glyphs (minus signs in Eq. [2], gamma rendered as 'c'), the image decides.

## Acceptance checks (green 61ec839)
| check | result |
|---|---|
| Migrator(root=green, index={}, aliases={})._migrate_extract(<worktree>/extracts/basu-2008-feo-steelmaking.yaml), then finalize() | completes. 1 work, 2 experiments, 1 bench, **0 observations**, 7 context rows. **hard issues after finalize: 0**. Queue 1: `extract yielded no observations` (typed absence, document axis). See S1. |
| evidence_for() on measured_tabulated / model_derived / quoted_attributed / quoted_unattributed / figure_only | all known values (no unknown) |
| `tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/basu-2008-feo-steelmaking.yaml` (run from green-ro with an absolute path to the corpus worktree; green-ro is a full clone, so the git-history check works) | `OK: 1 extract file(s) valid` (exit 0) |
| corpus worktree `tools/test_ledgers_valid.py` (script exit 0; also run under pytest) | exit 0; pytest 687 passed |
| payload survival | MgO enum; thermocouple/uniformity/PID named facts; Ar interval Decimal 0.15-0.20 (`mNm3 min-1`); tolerance -1..1 K; T 1873/1923 as Decimal; total pressure unknown/not_published; orifice not_applicable. All 108 composition records, all 108 activity records (spot: II-1, II-65, III-1, III-43) and Table I survive in context_by_work. |
| sidecar sha256 vs file | 6d54fb8e12375d83976b9a3cb7850684d45cb090fd4a8e4c0b600bb532252434 matches (558230 B). Citation, DOI, volume and pages match p447/456. Licence: "not recorded — verify" (sidecar predates this branch). |
| rg "/Users/" "/private/" in extract, ledger, tables, text, sidecar | no hits |

## A. Tables (every row, every cell)
Method:
- **CSV vs page image.** I read all 108 Table II/III rows digit by digit from zoomed 250 dpi crops (rows 1-21, 22-43, 43-63 and 64-65 of Table II; rows 1-23 and 23-43 of Table III).
- **CSV vs PDF text layer.** Programmatic: the 108 x 8 cells are identical.
- **YAML vs CSV.** Programmatic: every composition series row, every a_FeO/h_O table_row, and the solution and T_K bindings are identical. The Table I `table_transcription` equals t1.csv byte for byte.

| table | rows | cells | mismatches (values) | notes |
|---|---:|---:|---:|---|
| I (p448) t1.csv | 14 | 42 | 0 numeric | Every number matches: -31,200; 1.0; -7240; 4.67; 4860; 1.935; 1.8-2.0; 1808-2000 K; -111,250; 21.67; -23842; 15.26; -27,740; 11.66; 1823-1923 K; 0.1; 0.4; 1873 K; 2.2-4.7; 0.08; 1823 K; 0-5; 1.6-2.2; 1673 K; >1; 1573/1673 K. Cosmetic deviations only (advisory E5). |
| II (p451-452) t2.csv | 65 | 520 | 0 | e.g. sol 44 24.89/11.22/44.92/7.89/1.14/0.402/0.084; sol 64 29.49/12.42/40.97/8.76/3.83/0.524/0.11 |
| III (p452) t3.csv | 43 | 344 | 0 | e.g. sol 8 MgO 16.0; sol 35 25.82/8.55/48.6/10.08/1.74/0.394/0.101; sol 43 17.39/12.0/36.85/20.46/1.08/0.289/0.074 |

Units/standard states: the Table II/III footnotes say "All compositions are expressed in mass percent", "Standard state for calculation of a(FeO) is pure liquid 'FeO'" and
"Standard state for calculation of hO is the unit activity coefficient of [O] at infinite dilution". The extract carries all three (units, and `standard_states`). Eq. [1] and
ΔG° = -121,983.61 + 52.26T J/mol = -RT ln a(FeO)/h[O] (p449); h[O] = [mass pct O] x fO (p449); log10 fO = Σ e_i^O [mass pct i] (p450). All are correct in `derivation`.
Eqs [2] (p454: -0.7335, -0.2889, r²=0.93) and [3] (p455: 1262/T, -1.1302, +0.96, +0.123, -0.4198, r²=89 pct) are correct digit by digit and sign by sign against the image.

## B. Mismatches (26). Each is a required change.
**B1. Table II row locators (2).** Solutions 64 and 65 are printed on **p452 (pdf_page_index 5), "Table II. Continued"**. In the extract the
`basu_2008_table_ii_measured_compositions_1873k` series rows for Solution 64 and Solution 65 have `page: 451, pdf_page_index: 4`.
Fix: set page 452 / pdf_page_index 5 for those two rows.

**B2. Figure page locators (12 of 13 wrong).** The printed pages are: Figs 1-2 on **p449**; Figs 3-6 on **p453**; Figs 7-10 on **p454**; Figs 11-13 on **p455**. The extract has
1→448, 2→448, 3→449, 4→450, 5→451, 6→451, 7→452, 8→453, 9→453, 10→453, 11→454, 12→454 (only 13→455 is right). Fix all 12. The block locator `page: '448-455'` /
`pdf_page_index: '1-8'` becomes `'449-455'` / `'2-8'`. The Fig 2 subject should read "slag compositions at 1873 K and 1923 K on the (CaO+MgO)-(FeO+Fe2O3)-(SiO2+P2O5)
pseudo-ternary, with 1873 K liquidus curves [28]". The current wording drops the 1923 K points.

**B3. Quotation page locators and a spurious duplicate (10)**, in `basu_2008_qualifying_and_directional_statements`:
| claim (start) | extract page | printed page |
|---|---|---|
| "no definite correlation of γ(FeO) with CaO concentration can be observed." | 451 | **450** (Sec. IV.C, right column) |
| "At low concentrations of FeO, i.e., X(FeO) < 0.1 … no such trend is visible at X(FeO) exceeding 0.1." | 451 | **452** (text under Table III) |
| "a change of temperature within the range investigated appears to cause insignificant change …" | 451 | **452-453** |
| "all the workers, except Bodsworth, reported an increase …" | 451 | **453** (right column) |
| "the trend shown in Figure 8 should be treated with caution." (1st entry) | 453 | **454** |
| "the trend shown in Figure 8 should be treated with caution." (2nd entry) | 453 | **delete**: the paper prints it once (p454 only; text-layer count 1). The report's claim that it "is repeated in the source text" is false. |
| "This resulted in a negative correlation between X(FeO) and X(SiO2)." | 453 | **454** |
| "It is likely that a similar situation … Figure 10 …" | 453 | **454** |
| "The correlation coefficients of Eqs. [2] and [3] are practically the same (92 and 93 pct)." | 454 | **455** |
| "it can be concluded that γ(FeO) can be adequately estimated using Eq. [2] …" | 454 | **455** |
p451 is the full-page Table II and has no running text. Every page-451 quote is therefore mislocated.

**B4. Attribution stripped (1).** The p450 quote "the increase in basicity over the range X(CaO)/X(SiO2) = 2.0 to 6.0, at any level of FeO concentration, caused
only a marginal decrease in the activity coefficient of FeO." is carried under `quoted_unattributed`, which reads as the present authors' finding. The page attributes
it: "A similar trend was reported by Kishimoto and co-workers, who concluded that the increase in basicity … FeO.[22]" Fix: restore the attribution (quote from "A similar
trend was reported by Kishimoto and co-workers" and add ref [22]), or move it to a quoted_attributed home. Advisory, same pattern: "At higher concentrations of SiO2, the
activity of FeO exhibited positive deviation." (p450) continues Turkdogan and Pearson's [9] finding. Add that context.

**B5. method_class of the Table II/III activity record (1).** `basu_2008_table_ii_iii_author_calculated_activities` is `model_derived`. The page does not describe an author
model. The authors calculate h[O] from the analysed steel composition with standard interaction parameters [29], and a(FeO) from h[O] with ΔG°[25] (p449). They call the result
measured: abstract, "The present work experimentally measures the activity of FeO"; Conclusion 1, "The activity of FeO, measured by equilibration of liquid iron …".
This is a reduction of measured equilibria: `measured_reduced`, with derivation.relation = Eq. [1] + h[O] = [mass pct O] x fO, and inputs typed as unpublished
(the [mass pct O] and metal-composition inputs are not printed: "per-sample oxygen-analysis/model inputs absent", as the ledger already says). If main's precedent prefers
`calculated` (as in cho-suito-1994), use that, but not `model_derived`. See S1 for where the rows belong.

## S1. Structural: scoreable activities are parked in context (0 observations)
The extract puts all 108 a(FeO) values (standard state pure liquid FeO, each bound to T and an analysed mass-% composition), plus h_O, into `context` with `type: composition`.
The report's AMENDMENTS PROPOSED says the reason is that "At c52 there is no suitable Quantity". Green SCHEMA.md contradicts this at both c52b926 and 61ec839 (lines 190 and 203):
it has the observation types `activity_coefficient` ("γ or a as published") and `composition_series` ("Measured composition series across runs/samples"). The same SCHEMA's
d-032 context rules say "Context is not a shadow container for scored rows". Landed slag-activity precedents on mirror main carry these quantities as observations:
holzheid-1997-feo-nio-coo-activity-metal-saturated (37 `activity_coefficient`), guo-2021-mgo-activity-cmas-slag (26, with `measured_reduced`), cho-suito-1994-cas-slag-activities
(20, `quantity: activity`, `standard_state`, `values.composition_*`, `experiment:` FK). The migrator queues this extract as "extract yielded no observations".
Required change:
- Encode each Table II/III a(FeO) as an `activity_coefficient` observation: `quantity: activity`, `standard_state: pure liquid "FeO"` (as printed), T 1873/1923,
  `experiment:` FK, row locator (II-64/65 on p452), and the solution's printed mass-% composition (the oxide basis as printed; do not invent mole fractions unless derived and
  labelled as derived), with method_class per B5.
- Carry h_O likewise, as the activity of [O] in steel with the printed standard state "unit activity coefficient of [O] at infinite dilution". Point each a(FeO) row's
  derived_from/derivation.inputs at its h_O row.
- Keep the measured compositions as a `composition_series` observation, or keep them as context but repeat each row's composition in its activity observation.
- Main to confirm the exact encoding against the cho-suito/guo precedent. Re-run migrator+finalize (hard 0), the fidelity validator, and the ledger test on 61ec839.
- Delete the AMENDMENTS PROPOSED item about "no suitable Quantity". The Ar `mNm3 min-1` amendment can stay.

## C. Experiment / bench facts and typed absences (31 checked)
Correct as printed (p447 Sec. III unless noted): quench_equilibration method; "liquid steel and synthetic slag of previously determined quantity and composition were equilibrated in
sintered dense magnesia crucibles under a gentle stream (0.15 to 0.20 mNm³ min⁻¹) of Ar, using the MoS₂-heated horizontal tube furnace" ("MoS2" is printed; it is kept as printed, which
is right); 8 h (28,800 s, conversion only); ±1 K; two B-type thermocouples inside/just outside the tube; PID; withdrawal through one end of the tube; MgO-free start and MgO
saturation by crucible dissolution (p448); total pressure, purity, starting composition and T calibration typed not_published (correct: none is printed; the paper refers details to [26]);
orifice not_applicable (correct). Standard states: Fe pure liquid, [O] unit activity coefficient at infinite dilution (lim fO=1), FeO pure liquid (p450 and table footnotes). Correct.
Advisory (no count):
- E1. `characterization` (1873 K exp.) is located p448 and says "examined microscopically". The page (pp448-449) prints the techniques: "scanning electron microscope with energy dispersive
  X-ray spectroscopy and an electron probe microanalyzer". Name them, and locate as pp448-449.
- E2. `sweep_gas.flow_sccm: {tag: unknown, reason: not_numeric}`. The page prints a numeric flow, so `not_numeric` is the wrong reason. Use a reason that says unit not convertible
  without assumption (or not_applicable); the interval in bench.other_facts is fine.
- E3. Provenance `pdf_page_index` is 1-based (t2 '5-6', t3 6) while the extract is 0-based (II '4-5', III 5). Make them consistent.
- E4. Source-internal inconsistency not marked: Eq. [3] prints r² = 89 pct and Eq. [2] r² = 0.93, but p455 says "The correlation coefficients of Eqs. [2] and [3] are practically the same (92 and 93 pct)".
  Both are carried, but only the report flags the discrepancy. Add a `source_internally_inconsistent: …` note in the extract (the kems-053 pattern named in the brief). Likewise p450 says "the oxygen
  concentration in steel is expressed in parts per million (by mass)", yet Tables II-III print no ppm oxygen column (only h_O). Record this; it is why the reduction inputs are absent.
- E5. Table I cosmetic deviations from print: "(separately)" printed with parentheses; "X(P2O5) as well as" has no comma in print; "upto" printed (CSV "up to"); "1.8–2.0, for all FeO levels"
  has a comma in print; sentence-initial capitals added. Not numeric. Fix if the corpus wants verbatim cells.

## D. Printed directional/qualifying statements not carried (18). Carry each verbatim with its locator.
1. p448: "Similar results were obtained from the commercial thermodynamic packages THERMOCALC* and FACTSAGE.**" (on the solid fractions at 1873 K)
2. pp448-449: "Previous investigations have already confirmed the presence of two-phase slag in some of the samples, particularly those with higher basicity.[26,27]"
3. p447 Sec. II: "Deo and Boom observed that this nonstoichiometry is of negligible significance in steelmaking operations.[25] They suggested that the free energy of formation of "FeO" and that of FexO may be considered as the same. This convention has been followed up on in the present work." (This defines what "FeO" means in Tables II-III.)
4. p450: "Similar behavior of FeO activity was observed by several others as well.[18,22,31–34]"
5. p450: "A similar trend was reported by Bodsworth,[13] Kishimoto et al.,[22] and by Fetters and Chipman[5] as well as Turkdogan and Pearson,[9] whose findings are shown in Figure 7."
6. p450: "The results of Chipman[1] are an exception and show negative deviation over the entire range of FeO concentration."
7. p450: "Change in CaO concentration has a likewise minimal influence, but γ(FeO) is affected more significantly by the concentration of SiO2."
8. p450: "It is seen that γ(FeO) increases with the increase in the molar concentration of SiO2." (the present authors' own headline SiO2 direction, missing)
9. p450/452: "the results appear segregated into different ranges based on FeO concentration only."
10. p453: "The scatter in the value of γ(FeO), for any particular level of SiO2 (or CaO) concentration, may have resulted from variations in iron oxide concentration."
11. p453: "Turkdogan and Pearson observed that the activity of FeO showed a marginally negative deviation in silica-free steelmaking slags, which changed to positive deviation as the SiO2 content was increased.[9] A similar trend has been reported by others as well."
12. p453: "However, very little description of the effect of CaO concentration on γ(FeO) has been reported. It can be assumed that the earlier findings were probably similar to the absence of definite correlation with CaO concentration, as has been observed by the present authors."
13. p453: "It should be noted that the concentrations of SiO2 and FeO were not strictly independent variables in the samples investigated in the present work."
14. p454: "Figure 6 illustrates that the activity coefficient of FeO is practically a function of FeO concentration only, with a negligible influence of temperature. The relatively small temperature interval of only 50 K could have been partly responsible for this." (The extract carries only the first clause and the bare number 50.)
15. p455: "This conforms to the trend seen in Figure 5, which shows no definitive influence of basicity on γ(FeO)."
16. p455: "It can be seen in Figures 8 and 9 that the concentration of SiO2 has an effect on the activity coefficient of FeO, while CaO concentration has a much weaker influence."
17. p455: "Equation [3] shows that the activity coefficient of FeO is influenced by the concentrations of both CaO and SiO2, even though it is unaffected by basicity. In fact, the coefficients of X(CaO) and X(SiO2) have the same sign, which may explain why no perceptible dependence of γ(FeO) on basicity could be observed."
18. p450: "The concentrations of CaO, SiO2, FeO, P2O5, and MgO are considered in mass percent, and the oxygen concentration in steel is expressed in parts per million (by mass)." (see E4)
Background, advisory: p447 Sec. I, the earlier workers' assumption a_FeO = [mass pct O]/[mass pct O]satd. (oxygen dissolved as FeO, ideal Henrian). It contains no numbers.

## E. Statements checked and correct (47 carried quotations; 36 correct, 11 covered by B3/B4)
I checked every carried quotation against the page images for wording, direction and sign. The direction of every one is right (no reversed comparatives). Wording is verbatim apart
from typographic quotes and the ellipsis in the Conclusion 1 quote, which is correct. Section III/IV numbers carried in numeric_context (x 0.95-0.98; 94/6 mass %; FeO1.03; 1974;
1873/1923 K; 10-30 mass %; 1.2-3.5; 8 h; ±1 K; 50 K; Eqs [2]/[3]; 92/93 pct) are all correct.

## Printed numbers not carried: 0
Every numeric statement on pp447-456 (text, Table I, Tables II-III, Eqs [1]-[3], conclusions) is carried. Figure-only legends and axis labels (Fig 1 "x 100"; Fig 2 "Cristobalite 1600", "2CaO·SiO2 ~2130";
Fig 5 FeO bins <15/15-25/>25 %; Figs 8-9 X(FeO) bins) are figure-only, which is correct. Reference-list bibliographic numbers are not data.

## Count basis
Basis of the 238: 14 (Table I) + 65 (Table II) + 43 (Table III) table rows, each in CSV and both YAML homes; + 13 figure records + 47 quotations + 31 bench/experiment facts
+ 14 numeric_context entries + 2 derivation/standard-state records + 3 citation/DOI/sha + 6 acceptance checks.
Basis of the 26 mismatches: B1 2 + B2 12 + B3 10 + B4 1 + B5 1. No transcribed table value differs from print.

## Required changes (FIX-FIRST), in order
1. S1: re-encode a(FeO) and h_O as `activity_coefficient` observations bound to T, experiment and printed composition, using the precedents above. Drop the "no suitable Quantity" amendment.
2. B5: method_class `measured_reduced` (or main's `calculated` precedent), with derivation relation Eq. [1] + h[O] relation and typed-absent inputs.
3. B1: Table II solutions 64-65 → p452 / pdf_page_index 5.
4. B2: figure pages: 1-2 → 449; 3-6 → 453; 7-10 → 454; 11-12 → 455; block range 449-455; Fig 2 subject includes the 1923 K points.
5. B3: fix the 9 quote pages and delete the duplicate "Figure 8 … caution" entry.
6. B4: restore the Kishimoto [22] attribution (and the Turkdogan & Pearson [9] context for the SiO2 sentence).
7. D1-D18: carry the 18 statements verbatim with locators.
8. Advisory E1-E5 (SEM/EDS/EPMA; flow_sccm reason; pdf_page_index base; source_internally_inconsistent notes for r² and the missing ppm-O column; Table I verbatim typography).
Then re-run on green 61ec839: migrator + finalize (hard 0), `validate_literature_extracts.py --check-fidelity-match`, and `tools/test_ledgers_valid.py`.

!COMPLETE: rev-basu-2008-feo-steelmaking — FIX-FIRST, pages read 10, rows checked 238, mismatches 26, printed numbers not carried 0
