# REPORT: batch6 B2, Pedley & Marshall (1983), r2 round 1: Tables 6–7 (periodic-table dissociation energies)

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~22:35 ET
REQ: REQ-corpus-batch6-fix-and-extraction-from-regolith-main-2026-10-05.md, item B2 ("remaining rounds").

## Branch, tip, base
| Field | Value |
|---|---|
| Branch | `hunt/pedley-marshall-1983-gaseous-monoxides-r2` (new; pushed to the mirror, no force-push) |
| Tip | `d048b4207eda6f774e7d400b210a263747a99eed` (one commit; author Simon Rowland; no trailers) |
| Base | mirror `origin/main` `0dac82636dbdd8f0efb529b7862657dce8a6a243`. It contains the Tables 4–5 LAND commit `284e86a3` (ancestor check passed) and `extracts/pedley-marshall-1983-gaseous-monoxides.yaml`. The Tables 4–5 extract, CSVs and ledger on main equal `284e86a3`; `git diff` shows no differences. |
| Worktree | Mac `~/Repos/regolith-corpus/worktrees/pedley-marshall-1983-gaseous-monoxides-r2`, sparse (main's recipe; 56 MB) |
| Files changed | `extracts/<sid>.yaml` (+2 context records, scope lines), `ledger/<sid>.yaml` (t6/t7 entries, completeness, round note), new `tables/<sid>/t6.csv`, `t6.provenance.yaml`, `t7.csv`, `t7.provenance.yaml`. No PDF, no INDEX, no build_index/migrate_pilot. |
| Green | `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, read-only checkout `~/ci-scratch/regolith-green-ro` (HEAD verified; `engines/engines.local.toml` **present** there) |

## What r2 means here
This is not a confirm review or a fix. The Tables 4–5 slice already passed (r1 FIX-FIRST on 7478283f → r2 confirm LAND 284e86a3, both in the package). B2 continues the extraction on a new `-r2` branch from mirror main, one STATUS and one pushed tip per round. Rounds: (1) Tables 6–7 **this round**; (2) Tables 8–9; (3) Tables 10–11; (4) Appendix II; plus Tables 1–3 and Appendices I and III as located context.

## Pages and method
PDF sha256 `ff94c550a358c5255a90ed3f7a23d3f42daf8937c9bdfc2ce29df79ee8f3825a`, which equals the sidecar; 66 pages. I rendered all 66 pages at 220 dpi. pp. 984–985 (PDF pages 19–20) were also rendered at 450 dpi. The native scan is 323 ppi, 1-bit JBIG2. Each periodic-table block (main group, transition, lanthanides, actinides, footnotes) was read from a separate crop. Questionable glyphs were viewed at native resolution or upscaled ×3 (Au 52.3, lanthanide rows). The OCR text layer was used only to locate content, and it garbles many cells (e.g. "92.:2", "63.S", "SOl", "(5#)").

## Encoding (Tables 4–5 conventions kept)
- Two new `compilation_table` context records under `species.gaseous_monoxide_assessments.context`: `pedley_marshall_1983_table_6_periodic_dissociation_energies` (p. 984, kcal mol⁻¹) and `..._table_7_...` (p. 985, kJ mol⁻¹).
- One row per printed value cell: 82 per table, 164 in total. Columns are `block, element, formula, D0_printed, D0_<unit>, D0_relation, D0_uncertainty_<unit>, uncertainty_printed, footnote, estimated, row_method_class, attribution`.
  - `D0_printed` keeps the literal token, e.g. `(70)`, `<75.6`.
  - Parentheses are stored as `estimated: true` with `row_method_class: author_estimate`.
  - A printed `<` goes in `D0_relation` and the numeric bound goes in the value column (In, Zn).
  - Footnote a (C, N, O, F, S, Cl, Br, I) and footnote b (As) mean "Huber and Herzberg, reference [3]". These rows are stored as `quoted_attributed` with the attribution text. They print no bracketed uncertainty, so `uncertainty_printed: false` and no uncertainty key is set (typed absence, not 0).
  - Footnote b is an upper limit, but the As value prints no `<` sign. The printed `=` is kept, and the upper-limit meaning is carried in footnote/attribution and a context note.
  - Integers stay integers and decimals stay decimals, as printed.
- Dash cells (Ne, Ar, Kr, Xe, Po, At, Rn) get no row. They are listed in `no_value_printed`.
- The periodic cell "O" is the O–O molecule. It is carried as formula `O2`, and a note marks this as a layout interpretation.
- Title and all four footnotes are quoted verbatim in `title_quote` / `footnotes_quoted`. Table 6 says "Values in parenthesis are estimated by the authors."; Table 7 says "Values in curved brackets ...".
- **No new observations.** D0 has no observation home: `gibbs_table` → `Quantity.DELTA_FG`, and migrate.py:4215 refuses any "dissociation energ" note. The titles of Tables 6–7 print no temperature subscript, so none is inferred. Tables 4–5 head the same column D°0.

## Cross-table checks (scripted, against the merged t4/t5 CSVs)
- **Table 7 vs Table 5:** all 68 shared formulas match exactly in D0 value, `<` relation, estimate parentheses and bracketed uncertainty.
  - Fe prints a clean **386 [17]** in Table 7, while Table 5 prints the displaced tokens `38 617`. This corroborates the interpretation recorded in r1 item 3.
- **Table 6 vs Table 4:** D0, relations and estimate flags match for all 68, except these, kept as printed:
  - **Au 52.3** in Table 6 vs AuO 52.5 in Table 4. The ×3 crop shows the round-topped 3, next to the flat-topped 5 of Pt 92.5. Table 7 Au 219 fits either value.
  - Cd (55) and Re (149) vs Table 4 (55.4) and (148.8). Table 7 Cd (232) follows 55.4.
  - 21 bracketed uncertainties are printed to fewer digits than in Table 4: Be, Al, Si, Se, Ba, Sc, Ti, V, Fe, Co, Y, Zr, La, Hf, Ir, Ce, Er, Tm, Th, U, Pu. Examples: Sc [2.5] vs 2.7, V [5] vs 4.5, Th [3] vs 2.9.
  - S has footnote a and no bracket here, while Table 4 SO prints ± 1.0, class 1.
- **Table 6 × 4.184 vs Table 7:** every value agrees within rounding except Cd, noted above. This includes the 14 species absent from Tables 4–5: CO, NO, O2, FO, ClO, AsO, BrO, RbO, IO, TlO, TcO, HgO, PmO, PaO. Uncertainties that disagree after conversion are 8 of the rounded-bracket cases (Al, Sc, Ti, V, Y, La, Th, Pu), consistent with Table 7 being converted from the unrounded Table 4 values.
- All of these notes are carried in each context's `cross_table_notes`.

## Acceptance (green 61ec839da; PYTHONDONTWRITEBYTECODE=1)
| Check | Result |
|---|---|
| Fidelity: `cd ~/ci-scratch/regolith-green-ro && PYTHONPATH=. .venv-python tools/validate_literature_extracts.py --check-fidelity-match <abs corpus worktree>/extracts/pedley-marshall-1983-gaseous-monoxides.yaml` | `OK: 1 extract file(s) valid`, exit 0. Note: path-based fidelity samples that point at `context[...]` rows are refused ("metadata-only paths refused"), so this round adds none. The two existing observation samples still pin. |
| Migrator `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(...)` then `finalize()`, cwd = corpus worktree | hard issues **0**; observations 132 (unchanged); experiments 1; context records **4** (was 2) |
| Enthalpy child temperature | 298 on 132/132 (unchanged) |
| Payload survival | Migrated T6 and T7 context: 82 rows each. CSV == migrated context on every cell, with type-strict comparison: 0 mismatches. Numeric D0 + uncertainty cells per table: 155. Non-numeric: 0. `no_value_printed`, `footnotes_quoted`, `notes` and `cross_table_notes` all survive. First/last rows: T6 Li `78.7 [2]` → `{'D0_kcal_per_mol': 78.7, 'D0_uncertainty_kcal_per_mol': 2, ...}`; T7 Pu `712 [34]` → `{'D0_kJ_per_mol': 712, 'D0_uncertainty_kJ_per_mol': 34, ...}` |
| evidence_for | `compilation_assessed`, `author_estimate`, `quoted_attributed` all map to their closed class, reason None |
| Ledger: `python -m pytest tools/test_ledgers_valid.py -q -p no:cacheprovider` (corpus worktree) | `723 passed`, exit 0 |
| Absolute paths: `rg '/Users/\|/private/'` over extract, ledger, tables/<sid>/ | no matches (exit 1) |

## FACTS THE READER DROPS
None new. The Tables 6–7 rows are context-only, by design (no D0 quantity). The r1 limits on the 132 enthalpy observations are unchanged: uncertainty none, per unknown, approximate false.

## AMENDMENTS PROPOSED
None required. Optional: a dissociation-energy (D°0) quantity home would let Tables 4–7 D0 values be scored rather than stay context. It is not needed for this extract.

## Remaining rounds
Tables 8–9 (pp. 986–996), Tables 10–11 (pp. 997–1007), Appendix II (pp. 1017–1021). Tables 1–3 and Appendices I and III are to be carried as located context.

!COMPLETE: pedley-marshall-1983-gaseous-monoxides r2 round 1 — d048b4207eda6f774e7d400b210a263747a99eed, tables 2 (6, 7), rows 164 (+14 dash cells), cell absent, calibration absent; migrator completes, hard issues 0
