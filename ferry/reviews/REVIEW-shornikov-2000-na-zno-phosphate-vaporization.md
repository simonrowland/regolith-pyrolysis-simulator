# REVIEW (first review of record): shornikov-2000-na-zno-phosphate-vaporization @ e3f610335802c67ad84fba8e134dfea5d963be80

**From:** regolith-empirical (corpus review seat, batch 13)   **To:** regolith-main   **At:** 2026-10-05 ~23:10 ET
**Source:** Shornikov, S. I., and Archakov, I. Yu. (2000). Mass Spectrometric Study of Thermodynamic Properties and Vaporization
Processes in the Na2O–ZnO–P2O5 System. Glastech. Ber. Glass Sci. Technol. 73 C2, 58–65 (running head: "Advances in Fusion &
Processing of Glass – 6th Internat. Conf. 2000").
**Branch / tip:** mirror `mac-studio-256-1:Repos/regolith-corpus.git` `hunt/shornikov-2000-na-zno-phosphate-vaporization` =
`e3f610335802c67ad84fba8e134dfea5d963be80` (fetched 22:50 ET; FETCH_HEAD == origin tip == assigned sha). Commits under review:
15a83ac0 (claim) and e3f61033 (extract + ledger, claim removed). Changed files: `extracts/shornikov-2000-na-zno-phosphate-vaporization.yaml`
(358 lines), `ledger/shornikov-2000-na-zno-phosphate-vaporization.yaml` (58 lines), `claims/...claim` (deleted). No tables/ directory.
**Seat:** sparse review worktree on the Mac `~/Repos/regolith-corpus/worktrees/rev-shornikov-2000-na-zno-phosphate-vaporization`
(`git worktree add --no-checkout` detached at the tip; sparse set = `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*`,
`/text/<sid>/*`; 50 MB; no text/<sid> exists on the branch). Readers: read-only green clone `~/ci-scratch/regolith-green-ro` at
61ec839da3ba288c5df4a80f6d3ef142bd8ab461. Never ran build_index.py or migrate_pilot_extracts.py. Mac free disk at start: 63 GB.

## Main's question: were printed numbers missed? Answer: NO printed data number was missed.

All 8 PDF pages were rendered with `pdftoppm -r 220` (3569 x 5549 px) and every page image was read; digits of every numeric
statement were re-read on crops at full resolution. The MinerU OCR (`raw/<sid>/ocr-mineru/.../*.md`) was used only as a lead.
**The paper prints no numeric table of any kind** (no partial pressures, ion currents, activities, activity coefficients, Gibbs
energies, compositions or K values in numeric form). The two "tables" in the MinerU markdown (`<summary>scatter</summary>` for Fig. 2,
`<summary>line</summary>` for Fig. 3) are VLM digitisations of the figures, not printed tables; the extract correctly did not carry them
(and they would be figure_only even if digitised; e.g. the "line" table puts curve 2 at -10.0 for x(ZnO)=0, read off a curve).

### Page-by-page inventory of every printed number (published page / pdf_page_index)

| p. (idx) | Location | Printed numeric content | Carried? |
|---|---|---|---|
| 58 (0) | Introduction ¶1 | "nine ternary compounds" [1-5] | yes, `numeric_context.ternary_compounds_reported_in_prior_literature: 9` |
| 58 (0) | Introduction ¶1 | Na2O·ZnO·P2O5 and 4Na2O·6ZnO·5P2O5 melt congruently at "1055±5 K and 1068±5 K correspondingly [1, 3, 5]" | yes, `cited_congruent_melting_temperatures` 1055/5, 1068/5, attribution refs 1, 3, 5 (digits match) |
| 58 (0) | Introduction ¶2 | study range "950-1550 K" | yes, experiment `conditions.temperature_K` interval 950–1550 and `numeric_context.temperature_range_K` |
| 58 (0) | Experimental | sample formulas Na2O·ZnO·P2O5, 4Na2O·6ZnO·5P2O5 (stoichiometric integers) | yes, `sample.printed_composition` and Fig. 2 series |
| 59 (1) | Experimental (top) | "ratio of evaporation to effusion squares more than 500"; instrument "MI 1201T" | yes, bench `other_facts` bound `>` 500; `apparatus_family` MI 1201T |
| 59 (1) | Results ¶1 | "ionization energy equal to 25.0±0.1 eV" | yes, bench `energy_eV` 25.0, `energy_uncertainty_eV` 0.1, also in context (digits match) |
| 59 (1) | Results | reactions (1)–(8) with stoichiometric coefficients | yes, all 8 verbatim in `activity_derivations...equations` / `experimental_species...` |
| 60 (2) | Fig. 1 caption | (c) present study "at 1100 K"; (d) [13] "at 1300 K"; oxide legend 1 Na2O, 2 ZnO, 3 P2O5, 4 SnO; phase-field legend 1–5 for panel (b) | 1100/1300 K yes; oxide legend yes; phase-field legend 1–5 not carried (categorical labels, see optional O3) |
| 60 (2) | Fig. 1 axes | T 700–1300 K; log a -4 to -16 (c), right axis -3 to -8 (d); x(ZnO) ticks 0–60 / 60–0 | carried as axis ticks, but see F2 (one endpoint not printed) |
| 61 (3) | Results | reaction (9), K_ri; reactions (10), (11); equations (12), (13) | yes, verbatim; (9)'s check result carried |
| 62 (4) | Results ¶1 | equation (14); "at 1100 K (Fig. 1a)"; lower integration limit "16.6±0.2 (or 56.0±0.2) ZnO mole %" | yes, `[16.6, 56.0]`, `[0.2, 0.2]`, 1100 K (digits match); attribution to [1] missing, see F6 |
| 62 (4) | Results ¶2 | third congruent compound 6Na2O·4ZnO·5P2O5 | yes, inside the verbatim quote |
| 62 (4) | Conclusion | "at 950-1550 K" | yes |
| 63 (5) | Fig. 2 | caption series 1 / 2; axes x(Na2O) 25–50, x(ZnO) ticks 10–40; ~25 plotted points | series yes; points correctly not digitised; ZnO ticks mis-stated, see F2 |
| 63 (5) | Fig. 3 | caption "1 - in the present study at 1100 K and 2 - at 1300 K [13]"; axes ΔfG_T/RT -35 to -5, x(ZnO) ticks 0–60; plotted points | 1100/1300 K yes; points correctly not digitised; x ticks mis-stated, see F2 |
| 64–65 (6–7) | References 1–21 | bibliographic volume/page/year numbers only | n/a (bibliography); ref 16 Mann (1967) used as cross-section source, correct |

Printed data numbers not carried: **0**. Figure-only content (Fig. 1c present-study activity points for Na2O/ZnO/P2O5 at 1100 K;
Fig. 2 composition-drift points; Fig. 3 present-study ΔfG/RT points at 1100 K) is correctly held as figure_only context without
digitisation. If main wants numbers from this paper, the only route is figure digitisation (Fig. 1c, Fig. 3 curve 1), which is outside
the extraction contract.

## Fidelity findings (null hypothesis tested; REQUIRED changes)

**F1. Four comma-split unquoted flow-style scalars (the brief's explicit YAML bounce rule); the migrator receives truncated values.**
Verified with `yaml.safe_load` and in the serialised migrated work (green 61ec839da):
- line 108, `experiments[0].sample.characterization.locator.note`: "...composition change only, without numeric before/after assay." is
  split; migrated note ends "...qualitative composition change only" and a null key "without numeric before/after assay." is added.
- line 124, `experiments[0].apparatus.calibration.cross_section_source.state`: `{tag: value, value: Mann (1967), reference 16}` is split;
  migrated value is "Mann (1967)" and a null key "reference 16" is added (the extraction report's own payload table shows this as
  "Mann (1967)", so the loss went unnoticed).
- line 296, `context[5]` (`shornikov_2000_numeric_context`) locator: `section: Introduction, Experimental, Results and Discussion, Conclusion`
  becomes `section: Introduction` plus null keys `Experimental`, `Results and Discussion`, `Conclusion` (visible in the migrated row).
- line 321, `numeric_context.values.partial_pressure_numeric_results.locator.note`: "...are described, but no numerical partial-pressure
  values are tabulated or stated." is split at the comma.
Fix: quote each scalar (or use block style). Then re-check the migrated payload for these four strings.

**F2. Axis-tick values labelled `plotted_axis_ticks_as_printed` that are not printed** (checked on full-resolution crops):
- Fig. 1 (p. 60), `ZnO_mole_percent_panel_c: [0, 65]` → printed tick labels run 0, 10, ..., 60 (panel c); `ZnO_mole_percent_panel_d: [65, 0]`
  → printed 60, 50, ..., 0 (panel d). 65 is not printed (the Zn2P2O7 edge is unlabelled). Use [0, 60] / [60, 0], or note the
  unlabelled end at the Zn2P2O7 composition without a number.
- Fig. 2 (p. 63), `ZnO_mole_percent: [5, 45]` → printed y ticks are 10, 20, 30, 40. Use [10, 40].
- Fig. 3 (p. 63), `ZnO_mole_percent: [0, 70]` → printed x ticks are 0, 10, ..., 60. Use [0, 60].
(Other ticks verified correct: Fig. 1 T 700–1300 K, panel c log a -16 to -4, panel d right axis -8 to -3; Fig. 2 x(Na2O) 25–50;
Fig. 3 y -35 to -5.)

**F3. Fig. 1 panel (d) species mis-stated** (`context[1].values.panels.d.quantity: activities of pure Na2O, ZnO, P2O5, and SnO`). The panel (d)
legend (p. 60) shows only ● 2 (ZnO), ▲ 3 (P2O5), ▼ 4 (SnO); the Sn2P2O7–Zn2P2O7 system has no Na2O. Fix: "activities of pure ZnO, P2O5,
and SnO" (legend 2, 3, 4). Panel (c) legend ■ 1, ● 2, ▲ 3 (Na2O, ZnO, P2O5) is correct as encoded.

**F4. Wrong page locator.** The quote "The obtained data coincide with the recommended values [11] within the experimental error." is
located `page: 60, pdf_page_index: 2`; it is printed on **p. 61 (pdf_page_index 3)**, first paragraph, after reaction (9) (p. 60 is the
Fig. 1 page only). Same for `shornikov_2000_experimental_species_and_pressure_methods.values.equation_for_pressure_consistency_check`
(reaction (9)) and `comparison_result`, which sit in a row located p. 59: add a locator note that reaction (9) and the K_r check are p. 61.

**F5. Standard-state phase mis-stated as "not specified"; the printed notation sentence is not carried.** p. 59 prints, right after
reaction (4): "(the round brackets indicate the gas phase, the square - the solid)." The activity-defining reactions are all written with
square brackets: (1) [ZnO] = (Zn) + (O); (10) [Na2O] = 2(Na) + (O); (2) and (11) [P4O10] (pp. 59, 61). The extract
(`qualifying_comparative_statements.method_qualifications[3]`, and report) says "the physical phase of each standard is not specified".
Fix: carry the bracket-notation sentence verbatim with locator p. 59 (idx 1), and replace "not specified" with what is printed: the
standard-state sentence (p. 61, "Taking the pure oxides which form the ternary system as the standard states") names pure oxides only, and
the authors' notation writes the condensed oxides in the activity-defining reactions (1), (2), (10), (11) as solid ("the square - the
solid"). Do not go further than that (the paper does not otherwise state a standard-state phase; note that reaction (4) also uses square
brackets for Na2O·P2O5 although the samples are melts, so record the notation as printed rather than infer a thermodynamic phase).

**F6. Attribution missing on the integration limits.** p. 62: "According to the data [1] in the studied section ... at 1100 K (Fig. 1a) the
lower integration limit in equation (14) corresponds to the concentration values equal to 16.6±0.2 (or 56.0±0.2) ZnO mole % (for different
integrating paths)." The limits come from the phase diagram of reference 1 (Fig. 1a), not from this study's measurements. Add
`attribution: reference 1 (phase diagram, Fig. 1a)` beside `lower_integration_limits_reported` and
`lower_integration_limits_mole_percent_ZnO_at_1100_K` (located p. 62, idx 4).

### Optional (not blocking)
- O1. Quote capitalisation/trim: p. 58 prints "the information on them is rather contradictory." (lower-case, mid-sentence); p. 59 prints
  "Thus the predominant processes ..." (extract drops "Thus"); p. 62 prints "(Fig.3)". Mark trims with "..." or match the print.
- O2. Fig. 3 y-axis label is printed Δ_fG_T/RT (subscript f, formation); extract `y_axis_as_printed: ΔG_T/RT`.
- O3. Fig. 1 caption phase-field legend for panel (b) (1 liquid, 2 β-Zn2P2O7 + liquid, 3 β-Zn2P2O7 + Sn2P2O7-I, 4 Sn2P2O7-I + liquid,
  5 Sn2P2O7-II + liquid) is not carried; harmless as context, can be added to `panels.b`.
- O4. `shornikov_2000_numeric_context` has no `experiment:` link while every other context row has one.
- O5. Citation could add the proceedings identity from the running head ("Advances in Fusion & Processing of Glass – 6th Internat.
  Conf. 2000"). The extraction report's locators "p. 60" for the [11] quote and "pp. 60–61, equations (1), (10), (12)–(14)" are wrong
  (eq. 1 p. 59, eq. 14 p. 62); report only, no extract change.

## Row / fact check (digit by digit against the page images)

Bench (20 facts): apparatus MI 1201T p. 59 ✓ ("using the MI 1201T mass spectrometer modified for the high temperature investigations [9]");
method p. 58 ✓ ("we used the high temperature mass spectrometric Knudsen effusion method"); cell Mo / description p. 59 ✓ ("from the
molybdenum effusion cells"); liner not stated ✓; orifice diameter and channel length typed unknown ✓ (only the area ratio is printed);
T sensor, T calibration, T uncertainty typed unknown ✓ (no pyrometer/thermocouple/calibration anywhere in pp. 58–62); ionisation energy
25.0 / 0.1 eV ✓; multiplier and isotope corrections typed unknown ✓; area ratio > 500 ✓; appearance-energy standards Ag and Zn [11] ✓
(p. 59 "the appearance energies values of Ag and Zn [11] were used as the standards"); ion list Na+, NaPO2+, NaPO3+, Zn+, P2+, PO+, PO2+ ✓;
cross-section source Mann [16] ✓ (value text truncated by F1); background pressure, sweep gas, regime typed unknown ✓ (not printed).
Experiment (13 facts): knudsen_effusion ✓; 950–1550 K ✓; printed formulas ✓; initial composition / characterisation typed unknown ✓
(no assays, no starting materials or purity printed; note truncated by F1); calibration method "Hertz-Knudsen equation taking into account
the values of partial evaporation coefficients [14, 15]" + comparison method ✓; reference substances Ag [17], Zn [9, 12] ✓; partial
evaporation coefficients refs 14, 15 ✓; pressure-environment typed unknowns ✓.
Neutral species with Hertz-Knudsen pressures (PO, PO2, P2, Zn, Na, NaPO3) ✓; atomic O from equilibria (5)–(8) ✓.
Equations (1)–(14): all 14 checked verbatim incl. coefficients and charges ✓ (eq. 12 p³(PO)p(PO2)/(p(P2)√K_r2); eq. 13 p³(PO2)/(p(PO)√K_r11);
eq. 14 with −3 coefficients and limits x* to x).
Activities: ZnO from reaction (1) with data [12] ✓; Na2O from reaction (10) with data [11] ✓; P2O5 from p(P2), p(PO), p(PO2), reactions
(2), (11), eqs (12)–(13), and Gibbs–Duhem (14) ✓; no reduced rows, so no derivation/derived_from to check. Standard states: see F5.
Quotes: 12 checked; text matches the print apart from O1 trims; one wrong page (F4). Directional statements all in the right direction
("more negative values of the Gibbs energies ... less deviations from the ideality", "predominant evaporation of the zinc-phosphate
ingredients ... to the direction of the 2Na2O·P2O5 field", "influence ... sodium-phosphate system is not too large") ✓.
Numeric context values: 16 checked (9; 1055, 5; 1068, 5; 950, 1550; 500; 25.0, 0.1; 1100; 1300; 16.6, 0.2; 56.0, 0.2) ✓ all digits.
Figure rows: 3 checked; no digitised coordinates anywhere ✓; F2/F3 issues.

## Source integrity
- `shasum -a 256 raw/<sid>/<sid>.pdf` = 235e29ac33be76ba90c1188166f2cf97c4f7ef3770ea0e3dfb157bb84f3ec4bb = sidecar `sha256` = extract
  `corpus_sha256` ✓. Citation matches the print (title, authors, Glastech. Ber. Glass Sci. Technol. 73 C2 (2000) 58–65 from the page
  footers) ✓. Licence "owner-supplied copy" ✓.
- `rg -n '/Users/|/private/'` over both changed files: no matches ✓.
- Ledger stages located/acquired/decoded/transcribed/extracted present; `tables: []`, figures 1–3; completeness `numbered_tables:
  true-absence` and `numeric_partial_pressure_values: true-absence` are correct per the page images.

## Acceptance (Mac, green clone ~/ci-scratch/regolith-green-ro @ 61ec839da, `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=~/ci-scratch/regolith-green-ro`, cwd = green clone)
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/<sid>.yaml)` then `finalize()`: completes;
  works 1, experiments 1, benches 1, observations 0, context rows 7; validation issues total 0, **hard 0**; queue 1
  (`why='extract yielded no observations'`, extract-level typed absence: expected for a zero-row source).
- `evidence_for('author_estimate')` and `evidence_for('figure_only')`: both known (AUTHOR_ESTIMATE, FIGURE_ONLY).
- `tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/<sid>.yaml` (absolute path to the sparse corpus
  worktree's extract, validator from the green clone): `OK: 1 extract file(s) valid`.
- Corpus worktree `python -m pytest -q -p no:cacheprovider tools/test_ledgers_valid.py`: **709 passed**.
- Note: the validators pass even though F1 truncates four values: the comma-split keys are silently accepted, so passing validators do
  not clear F1.
- `engines/engines.local.toml` exists in the green clone ~/ci-scratch/regolith-green-ro (read-only; not used by these checks).

## Verdict

The zero-table encoding is correct: the paper prints no numeric data table and no numeric pressure/activity value, and every printed
number in the text is carried. The extract is not landable as-is because of the comma-split YAML (F1, an explicit bounce rule that truncates
four migrated values), four "as printed" axis values that are not printed (F2), one wrong species set (F3), one wrong page (F4), a
standard-state statement that contradicts the printed bracket notation (F5) and a missing attribution (F6). All are small text edits;
no new numbers are needed. A grok confirm after the fix is sufficient.

VERDICT ON COMMIT: FIX-FIRST
Required changes: F1 (extract lines 108, 124, 296, 321: quote the comma-bearing scalars), F2 (Fig. 1 p. 60 ticks [0, 60]/[60, 0]; Fig. 2
p. 63 ZnO ticks [10, 40]; Fig. 3 p. 63 ZnO ticks [0, 60]), F3 (Fig. 1 p. 60 panel d: ZnO, P2O5, SnO only), F4 (quote "coincide with the
recommended values [11]" and reaction (9) check: p. 61, idx 3), F5 (carry p. 59 "(the round brackets indicate the gas phase, the square -
the solid)" and restate the standard-state phase as printed), F6 (p. 62 integration limits attributed to reference 1 / Fig. 1a).

rows checked 78, mismatches 12, printed numbers not carried 0
(78 = 33 bench/experiment facts + 14 equations + 12 quotes + 16 numeric context values + 3 figure rows; 12 = F1 4 truncated encodings +
F2 4 unprinted tick values + F3 1 + F4 1 + F5 1 + F6 1.)

Worktree `~/Repos/regolith-corpus/worktrees/rev-shornikov-2000-na-zno-phosphate-vaporization` removed (`git worktree remove` + prune; only my own
tools/__pycache__ from the ledger test was deleted first). Nothing was committed or pushed to the corpus mirror.

!COMPLETE: rev-shornikov-2000-na-zno-phosphate-vaporization — FIX-FIRST, pages read 8, rows checked 78, mismatches 12, printed numbers not carried 0
