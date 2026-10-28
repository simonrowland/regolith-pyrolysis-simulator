# Schumm (1981), nepheline table audit

- Printed page 13 (PDF page 19), table headed `NaAlSiO4 nepheline`.
- Printed count: 18 temperature rows; the 467 K and 1180 K transitions each have two rows.
- Stored columns: Cp, H−H298, ΔfH, and ΔfG, 18 cells each; S298 is one separately printed value. The log10 Kf column is excluded because it is outside this round's allowed quantities.
- Cell reads: MinerU table `p019-t06.csv` and pdftotext-layout table agree on the digits, signs, exponents, and cell assignments for the four stored columns. The whole page render was checked for layout and every stored value, including the paired transition rows. No unresolved cell disagreements.
- Printed source annotations: S298 and H298 cite `53KEL/TOD`; ΔfH298 cites `53KRA/NEU`. No separate Cp or ΔfG source annotation is printed. The bibliography expands these IDs to Kelley et al. (1953) and Kracek et al. (1953), respectively.
- Polymorph is observation-level: α through the first 467 K row, β from the second 467 K row through the first 1180 K row, and γ from the second 1180 K row onward. The migrated Cp anchors at 467 K are 169.24280 (α) and 142.71624 (β); at 1180 K they are 190.07912 (β) and 178.53128 (γ). Existing `Polymorph.ALPHA`, `.BETA`, and `.GAMMA` tokens are defined in `simulator/battery/enums.py:315-320`; `polymorph_from_extract` reads `condensed_form.polymorph` in `simulator/battery/migrate.py:8668-8700`.
- The printed `S−S298` column was not stored: this is an entropy increment, not absolute `S`. Schema check: `simulator/battery/enums.py`, `Quantity` (`enums.py:62-111`), defines `S` but no entropy-increment quantity; `simulator/battery/migrate.py`, `TYPE_QUANTITY` (`migrate.py:435-444`) and `QUANTITY_ALIASES` (`migrate.py:453-547`), has no exact alias for it. `tools/validate_literature_extracts.py:79-97` closes observation types and provides no alternate entropy-increment observation type. It is therefore not labeled as `S`.
- `S298 = 29.7 ± 0.3 cal/mol` and `ΔfH298 = −501.0 ± 1.0 kcal/mol` are copied as printed; confidence levels are not stated. The ΔfH uncertainty is attached to the 298 K anchor only.

| Printed T (K) | Stored numeric columns checked |
|---:|---|
| 298, 300, 400, 467 (two), 500, 600, 700, 800, 900, 1000, 1100, 1180 (two), 1200, 1300, 1400, 1500 | Cp, H−H298, ΔfH, ΔfG |
