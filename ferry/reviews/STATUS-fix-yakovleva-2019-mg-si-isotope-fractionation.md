# STATUS: fix of yakovleva-2019-mg-si-isotope-fractionation (batch 7)

**From:** regolith-empirical (fix-b57 seat)   **To:** regolith-main   **At:** 2026-10-05 ~21:35 ET
Review applied: `review-r2.md` (r2 FIX-FIRST on 460832df; 8 rows, 3 mismatches C1–C3, 0 printed numbers not carried).

- Mirror: `mac-studio-256-1:Repos/regolith-corpus.git`, branch `hunt/yakovleva-2019-mg-si-isotope-fractionation`
- Start tip (verified on origin before work): `460832dfbc2c1bcf774ed55f7f1c72b9eb0010db`
- **New tip (pushed, ls-remote verified): `d1eaaa3b562888cf224a200c839342dd55add860`** (one commit, fast-forward over 460832df;
  author Simon Rowland, no trailers; explicit pathspecs: extract, ledger, sidecar only)
- After this fix: main runs a grok confirm review (per REQ batch 7).

**items fixed 4/4** (required changes 1–4 of review-r2; mismatches C1, C2, C3 all addressed) + 1 advisory carried
**corrections 9** (string edits: extract 6 [2 attribution, citation_provenance, ideal_and_experimental_comparisons, qualifications, differential_derivation] + 1 advisory [equations_30_and_31], ledger note 1, sidecar citation 1)
**hard issues 0** (migrator after finalize); fidelity validator OK; tools/test_ledgers_valid.py 688 passed.
rows 8 (7 Mg + 1 Si context), observations 0, tables 0; no rows added, removed or renamed; no ids changed; source_id not renamed (main's call).

## Per-item table

| review-r2 item | What changed (at d1eaaa3b) | Page evidence (read from 220 dpi renders + 450 dpi crops) |
|---|---|---|
| 1 / C1 author name | `Mg.context[0].attribution` and `Mg.context[5].attribution`: "Yakovleva and Shornikov" → "Yakovlev and Shornikov". `Mg.context[0].citation_provenance` rewritten: p. 777 prints the author line "О. И. Яковлев^а,*, С. И. Шорников^а,**" (superscript "а" = affiliation mark; the PDF text layer merges it into "Яковлева"); even-page running heads pp. 778–792 print "ЯКОВЛЕВ, ШОРНИКОВ"; e-mail yakovlev@geokhi.ru; p. 793 prints Geokhimia and "O. I. Yakovlev, S. I. Shornikov"; "yakovleva" in source_id/observation ids is a legacy intake text-layer key, not the printed name or a transliteration; source_id not renamed. Sidecar `citation`: "Yakovleva, O. I." → "Yakovlev, O. I.", the "transliteration of the Russian first-author name" phrase deleted, bracket now says the yakovleva key is an intake text-layer misreading of Яковлев^а. | p. 777 author line, 450 dpi crop: "О. И. Яковлев^{а,*}, С. И. Шорников^{а,**}" — the "а" is superscript, same mark as on Шорников (pdftotext indeed yields "Яковлева,*"). p. 778 running head crop: "ЯКОВЛЕВ, ШОРНИКОВ". p. 777 e-mail "*yakovlev@geokhi.ru". p. 793 English block: "© 2019 O. I. Yakovlev, S. I. Shornikov"; "For citation: Yakovlev O.I., Shornikov S.I. … Geokhimia. 2019;64(8):777–793". |
| 2 / C2 √(44/45) label | `Mg.context[3].ideal_and_experimental_comparisons`: "source_internally_inconsistent, neither is silently corrected" removed. Now: experimentally α_Mg and α_Si are considerably closer to 1 than the ideal factors sqrt(24/25) and sqrt(44/45) (silica evaporating as SiO), attributed to Davis et al. (1990), Wang et al. (2001), Richter et al. (2002), Knight et al. (2009); eq. (6), p. 780, uses sqrt(44/46) for 30SiO/28SiO; with 16O, 44/45/46 are the 28/29/30SiO masses and the paper does not state which Si pair sqrt(44/45) refers to; both kept as printed; the print does not contradict itself. Potassium (39/41)^0.5 / ^0.43 (Richter et al. 2011) and the right-column φ_i/φ_k ≠ 1 sentence unchanged. | p. 782 left column: "…α_Mg и α_Si значительно ближе к единице, чем «идеальные» факторы, рассчитанные как √(24/25) и √(44/45) (кремнезем испаряется из расплава в форме SiO) (Davis et al., 1990; Wang et al., 2001; Richter et al., 2002; Knight et al., 2009)". p. 780 eq. (6): J30SiO/J28SiO = (P30SiO/P28SiO)√(m28SiO/m30SiO) ≈ (P30SiO/P28SiO)√(44/46); right column "~99% кислорода состоит из изотопа 16O". |
| 3 / C3 printed K'_D | `Mg.context[3].qualifications`: now "P. 785, left column: the Rayleigh equation is derived under the condition of constancy of K_D_prime (printed K'_D, capital-D subscript; the subsequent lines and equation (21) print K'_d, carried here as K_d_prime), which the paper calls a serious assumption, and also under constant temperature, i.e. isothermal evaporation; …" (rest unchanged). `Mg.context[3].differential_derivation`: the step now reads "…the left side is expression (20), i.e. x_24Mg^V, which can be written x_24Mg^V = K_D_prime*x_24Mg^L (printed K'_D, capital-D subscript, left column; the following lines and equation (21) print K'_d, carried as K_d_prime; the paper does not comment on the subscript change); hence K_d_prime*x_24Mg^L = (N^L/dN^L)*dx_24Mg^L + x_24Mg^L; …" — this also carries the printed intermediate "K'_d x^L = (N^L/dN^L)dx^L + x^L" line that was previously collapsed. All other algebra unchanged. | p. 785 left column, 450 dpi crops: "Уравнение Рэлея выводится при условии постоянства K'_D, что, вообще говоря, является серьезным допущением. Кроме того, уравнение выводится при условии постоянства температуры"; "что можно представить в виде x^V_24Mg = K'_D x^L_24Mg. Таким образом, получим: K'_d x^L_24Mg = N^L/dN^L dx^L_24Mg + x^L_24Mg"; right column x^L(K'_d − 1) = …, (21) F^(K'_d − 1) — lower-case d. |
| 4 ledger / report wording | Ledger `stages.transcribed.note`: "no remaining formula defect" sentence kept; appended "Fix round 2 (fix-b57, review-r2 C1-C3 …)" describing the author-name correction, the removed false inconsistency label, the carried printed K'_D, and the p. 789 β/λ meanings. (The extraction report is not a tracked corpus file; this STATUS supersedes its "Yakovleva … transliteration" wording.) | — |
| advisory (non-blocking) | `Mg.context[5].equations_30_and_31`: appended "P. 789 defines beta as the degree of dissociation of MgO in the melt and lambda as the degree of association of SiO2 in the melt; the minus sign of lambda_SiO2 means that silica in the melt is not a donor of oxygen ions but their acceptor." | p. 789 left column: "где β – степень диссоциации MgO в расплаве"; "где λ – степень ассоциации SiO2 в расплаве"; "Знак минус у λ_SiO2 означает, что оксид кремния в расплаве является не донором ионов кислорода, а их акцептором." |

Residual checks: no "Yakovleva" (Latin) left in extract/ledger/sidecar outside id keys; "source_internally_inconsistent" count 0; all three YAML files parse.

## Gates (executed on the Mac)

- PDF sha256 a948acf7…d15e2 = sidecar (unchanged); 17 pages, pp. 777–793, re-rendered `pdftoppm -r 220 -png` and read; 450 dpi crops of p. 777 author line, p. 778 running head, p. 785 left column (both K'_D places).
- Green: `/Users/simonrowland/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (clean, read-only; PYTHONDONTWRITEBYTECODE=1, PYTHONPATH = that checkout, simulator .venv python). `engines/engines.local.toml` **exists** there.
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<abs sparse-worktree extract>)` then `finalize()`: works 1, experiments 0, observations 0, queue 1 (expected "extract yielded no observations"); validation ok, **hard issues 0**, registry issues 0; contexts 8 source / 8 migrated, 0 differing value maps.
- `tools/validate_literature_extracts.py --check-fidelity-match <abs path of sparse-worktree extract>` (positional, run from green): **OK: 1 extract file(s) valid**, exit 0.
- Corpus `tools/test_ledgers_valid.py` from the sparse worktree (`-c /dev/null -o addopts= -p no:cacheprovider`): **688 passed**.
- `rg '/Users/|/private/|/tmp/'` over extract, ledger, sidecar: no matches.
- Never ran build_index.py / migrate_pilot_extracts.py. Did not merge to mirror main. Did not touch yakovleva-2019-ca-al-isotope-fractionation or any other worktree/branch.

## Worktree

Sparse worktree `~/Repos/regolith-corpus/worktrees/fix-b57-yakovleva-2019-mg-si-isotope-fractionation` (48 MB, main's recipe, branch hunt/<sid> tracking origin) removed after push; local branch left at d1eaaa3b (= origin) for main to delete at merge.

## Note for main (same as r2's, not acted on)

The text-layer merge Яковлев^а → "Яковлева" likely also produced the "yakovleva" keys of yakovleva-2019-ca-al-isotope-fractionation and yakovleva-2022-alkali-chondrule-evaporation; renaming source_ids is main's call.

!COMPLETE: fix-b57-yakovleva-2019-mg-si-isotope-fractionation — new tip d1eaaa3b562888cf224a200c839342dd55add860, items fixed 4/4, corrections 9, hard issues 0
