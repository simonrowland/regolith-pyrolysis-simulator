# Stolyarova 1992 numeric activity table audit

## Result

No numeric component activity table is printed in this paper, so this repair adds **0 activity rows**. The text says activities were determined, but the printed numeric tables contain other quantities. There are no activity figures to digitize. The existing extract already preserves the printed Gibbs energy and chemical potential tables; their row ids and values are unchanged.

## Table inventory and column alignment

Pages below are absolute PDF pages. The rendered page was checked once for each table's row and column assignment. Printed row counts exclude headings and subtotals.

| Printed table | PDF / printed page | Printed rows | Printed columns, left to right | Alignment and relevance |
| --- | --- | ---: | --- | --- |
| 1 | 2 / 186 | 6 | composition; T (K); experimental HTMS −ΔG; Kohler −ΔG; Wilson −ΔG | The first four rows share 1933 K; the final two print 1833 K and 1933 K. These are integral Gibbs energies, not component activities. The SiO₂:CaO:MgO composition is printed `0.50:0.25:25`; render confirms the missing decimal is in the source, so the existing extract leaves composition unavailable rather than repairing it. |
| 2 | 3 / 187 | 7 | binary system; T (K); λ₁₂; λ₁₂₀; λ₂₁; λ₂₁₀ | Wilson model parameters, not activity values. The table's temperature entries are 1300 or 1900 K. |
| 3 | 3 / 187 | 5 | SiO₂:K₂O:Na₂O mole fractions; HTMS −ΔG; Wilson −ΔG | Integral Gibbs energies. The table does not print a temperature, and none is inferred. |
| 4 | 3 / 187 | 5 | Na₂O mole fraction; Wilson chemical potential (a); experimental chemical potential (b) | Chemical potentials at 1273 K per caption, not activities and outside the requested 1833/1933 K conditions. |

The two machine reads were pdftotext-layout and MinerU. Their table row assignments were checked against the page renders: Table 1 on PDF p. 2 and Tables 2–4 on PDF p. 3. The page render confirms the displayed table headers and numeric columns. Table 1's malformed composition is retained verbatim. Table 2 and Table 3 were flagged by `tools.mineru_table_qa.py` for OCR/text disagreement; the PDF renders confirm their column assignments and the values currently transcribed in the extract. Tables 1 and 4 had 1.000 numeric agreement in the QA report.

`tools/mineru_table_draft.py convert` was attempted on the provided MinerU directory, but could not infer its chunk start page because the content-list path has no start-page marker. No draft was used to author or alter rows. The table QA pre-screen did read all four blocks.

## Activity coverage

The overview says component activities were determined, and the methods section says oxide activities were determined by the Belton–Fruehan equation or comparison method. The paper provides equations and model outputs, but no numeric component activity table or numeric activity figure. Table 1's −ΔG cells, Table 2's Wilson parameters, Table 3's −ΔG cells, and Table 4's chemical potentials cannot be relabeled as activity. Therefore the existing `numeric_activity_published: false` statement remains accurate, and the extract gains no rows.

## Cell-level render pins

| Table / row | PDF page | Render-confirmed cells |
| --- | ---: | --- |
| 1 / SiO₂:CaO:Al₂O₃ 0.50:0.43:0.07 | 2 | 1933; 8.3; 8.4; 7.4 |
| 1 / SiO₂:CaO:MgO | 2 | `0.50:0.25:25`; 1833; 10.3; 6.2; dash |
| 3 / 0.85:0:0.15 | 3 | 8.4; 11.7 |
| 4 / x(Na₂O)=0.44 | 3 | 1273 K (caption); 50.6; 56.0 |

No values or observation ids were removed or rewritten. The only extract edits add absolute PDF page locators for existing tables whose locator lacked a page.

## Isolated migration

The real migrator reports 0 observations, 0 hard issues, 0 unavailable values, 0 unknown quantities, and 0 unknown phases. All records in this extract remain context and do not create battery observations, so the expected battery-store delta is 0 observations. The focused test and the required YAML guard / repo-wide extract suite were submitted together to the configured remote gate. Four attempts were refused for candidate capacity; the fourth retry then failed at snapshot setup. No pytest result is available.
