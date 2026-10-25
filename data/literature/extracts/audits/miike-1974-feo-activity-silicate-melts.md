# Miike et al. (1974) extraction audit

## Source and transcription

The duplicate search found no extract for this paper and no Miike subject in the all-branch commit subjects. A Tranell (1999) thesis cites Morinaga, Suginohara, and Yanagase (1974) for a conductivity figure; that is a secondary citation, not this paper's extract.

The table reader converted four MinerU blocks on PDF page 3. The content lists contain duplicate OCR representations; the four unique blocks correspond to Table 1, the CaO and TiO2 panels of Table 2, and Table 3. The pdftotext layer contains captions but no numeric table cells, so the render of PDF page 3 (published page 413) was used to verify every numeric cell and each panel's row and column assignment. The two OCR reads were not treated as independent confirmation. The QA pre-screen's low digit agreement reflects the absent pdftotext table body.

Printed counts reconcile to 24 activity rows: Table 1 has 6, Table 2 has 10 (4 CaO and 6 TiO2), and Table 3 has 8 Al2O3 rows. Every sample's wt% composition is attached to its activity row and reaches the migrated identity; the printed mol% composition is retained alongside it. The cell audit CSV lists all 276 numeric source cells and their stored paths: both composition bases, temperature, e.m.f., P_O2, and a_FeO. The table-tool compare output reports 24 matched, 0 mismatched, 0 no-match, and 0 requiring scale mapping.

The table tool uses the emitted draft-block coordinate in its generated observation ID to distinguish MinerU panels that share a printed table number. It also recognizes the source phase token `l` as liquid during comparison. A focused tool regression covers both conditions.

## Conditions, method, and limits

The paper reports a fixed-ratio CO–CO2 gas mixture and row-wise P_O2 calculated from the measured e.m.f. Table temperatures, P_O2, and a_FeO are retained as printed. The numerical gas ratio is not printed with the activity rows. The apparatus section identifies a γ-Fe crucible made of SS-41 steel. The measured phase is liquid (`l`).

The paper gives a standard formation Gibbs energy for FeO in Eq. (1), but does not state the pure FeO reference phase. The extract preserves that reference state as unstated; migration therefore leaves the reference-state axis unknown and queued. The paper does not print total pressure, so it is absent from the extract and the migrated identity keeps total pressure unknown. No ambient, room-temperature, or default pressure was added.

No numerical gamma_FeO values are printed in Tables 1–3, and none were calculated from activity and composition. Figure-only candidates left undigitized are Fig. 5's activity comparison and Fig. 6's activities calculated by the authors using Gibbs–Duhem.

## Migration and test evidence

The isolated production migration produced 24 activity observations, 0 hard issues, 0 unavailable values, and 24 queue entries. Every queue entry is the same unresolved printed-form issue: the paper does not identify the pure-FeO reference state. This is preserved as unknown; no reference state is guessed. Row-level e.m.f./P_O2 context records provide the derivation inputs; the paper itself is not used as a derivation input.

The migrated identity axes were inspected directly for three rendered rows (composition is the migrator's mole-fraction representation of the row's printed wt% composition):

| Sample | Composition (mole fractions) | Total pressure (Pa) | fO2 (Pa) | Temperature (K) |
| --- | --- | --- | --- | --- |
| 1 | FeO 0.81464754; Fe2O3 0.03207512; SiO2 0.15327734 | unknown (not printed) | 5.0155875e-6 | 1623.15 |
| Ca-4 | FeO 0.50735136; Fe2O3 0.00641761; SiO2 0.34333203; CaO 0.14289900 | unknown (not printed) | 8.14653e-7 | 1573.15 |
| Al-8 | FeO 0.55448522; Fe2O3 0.00632671; SiO2 0.36934538; Al2O3 0.06984269 | unknown (not printed) | 5.2182375e-7 | 1573.15 |

The three render-pinned activities (0.93, 0.65, 0.52) are also fidelity samples. Mutation evidence: changing the first sample from 0.93 to 0.94 made `test_fidelity_sample_matches_extract[miike-1974-feo-activity-silicate-melts]` fail with a sample/extract mismatch; restoring the source value made that test pass.

The final focused run passed 53 cases, including both Miike extraction tests, all table-draft tests, and the Miike case from the repo fidelity suite. After adding explicit queue/unavailable assertions, the YAML flow-scalar guard and both Miike extraction tests passed again (19 passed across those files). The restored Miike fidelity case also passed separately (1 passed, 800 deselected). A full `tests/test_literature_extracts.py` run completed with 848 passed and 5 failed: one failure was Miike's path-format issue, which was corrected and verified above; the other four concern existing docs-private portability checks and extract references in unrelated files. The repo-wide failures remain outside this extraction's scope.

## Store effect

No battery store was regenerated or committed. The expected migration delta is 24 FeO activity observations and 24 located context records carrying the row-wise e.m.f. and P_O2 inputs.
