# Barin (1995), NaFeO2 audit

## Scope and locators

The only Barin 1995 NaFeO2 entry is printed as `FeNaO2`, “IRON SODIUM DIOXIDE,”
on print page 708 (PDF page 823; zero-based PDF page index 822). The heading is
not a numbered table caption, so the extract records the printed heading and
does not assign a table number. The separate “References” block is on the same
PDF page. The MinerU blocks `p823-t4465` and `p823-t4466` are cited here only as
draft-tool identifiers, not as printed table numbers.

Source order was MinerU table HTML and markdown, then the pdftotext layout. The
full table row/column layout and every retained numeric cell below were checked
against the 400 dpi render of PDF page 823. The page contains 22 body rows: 16
SOL-B temperature rows, one transition-increment row without a printed T cell,
and 5 LIQ temperature rows (21 temperature rows total). The formula heading
assigns all rows to FeNaO2. The extract retains the requested Cp, S, ΔHf, and
H−H298 columns, plus transition and fusion values; the other printed columns
−(G−H298)/T, H, G, ΔGf, and log Kf are outside the requested property set.

| Draft grid row | Printed row | T (K) | Cp (J/(K mol)) | S (J/(K mol)) | ΔfH (kJ/mol) | H−H298 (kJ/mol) | Transition ΔH (kJ/mol) |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | SOL-B | 298.15 | 84.517 | 88.299 | −698.787 | 0.000 | |
| 4 | SOL-B | 300.00 | 84.542 | 88.822 | −698.783 | 0.156 | |
| 5 | SOL-B | 400.00 | 85.872 | 113.325 | −701.481 | 8.677 | |
| 6 | SOL-B | 500.00 | 87.203 | 132.630 | −701.840 | 17.331 | |
| 7 | SOL-B | 600.00 | 88.533 | 148.647 | −702.317 | 26.118 | |
| 8 | SOL-B | 700.00 | 89.864 | 162.394 | −702.933 | 35.038 | |
| 9 | SOL-B | 800.00 | 91.194 | 174.481 | −703.742 | 44.090 | |
| 10 | SOL-B | 900.00 | 92.525 | 185.299 | −704.881 | 53.276 | |
| 11 | SOL-B | 1000.00 | 93.855 | 195.116 | −706.687 | 62.595 | |
| 12 | SOL-B | 1100.00 | 95.186 | 204.124 | −709.202 | 72.048 | |
| 13 | SOL-B | 1200.00 | 96.517 | 212.463 | −808.006 | 81.633 | |
| 14 | SOL-B | 1300.00 | 97.847 | 220.241 | −807.392 | 91.351 | |
| 15 | SOL-B | 1400.00 | 99.178 | 227.541 | −806.760 | 101.202 | |
| 16 | SOL-B | 1500.00 | 100.508 | 234.429 | −806.107 | 111.186 | |
| 17 | SOL-B | 1600.00 | 101.839 | 240.958 | −805.430 | 121.304 | |
| 18 | SOL-B | 1620.00 | 102.105 | 242.225 | −805.291 | 123.343 | |
| 19 | SOL-B transition increment | — | — | 30.476 | — | — | 49.371 |
| 20 | LIQ | 1620.00 | 125.520 | 272.701 | −755.920 | 172.714 | |
| 21 | LIQ | 1700.00 | 125.520 | 278.751 | −754.477 | 182.756 | |
| 22 | LIQ | 1800.00 | 125.520 | 285.926 | −751.917 | 195.308 | |
| 23 | LIQ | 1900.00 | 125.520 | 292.712 | −763.561 | 207.860 | |
| 24 | LIQ | 2000.00 | 125.520 | 299.150 | −761.454 | 220.412 | |

The 1200 K ΔfH cell is printed `−808.006` kJ/mol. The render confirms the
leading 8. This produces a discontinuity from −709.202 kJ/mol at 1100 K to
−808.006 kJ/mol at 1200 K, followed by −807.392 kJ/mol at 1300 K. It is retained
as printed and flagged; no correction is inferred. The ΔfH cells at 1900 and
2000 K are also preserved exactly as printed.

The pdftotext layer dropped the leading `1` from H−H298 at SOL-B 1600 K
(`21.304`), SOL-B 1620 K (`23.343`), LIQ 1620 K (`72.714`), and LIQ 1700 K
(`82.756`). The page render confirmed `121.304`, `123.343`, `172.714`, and
`182.756`. The render also confirmed the transition increments `30.476` under S
and `49.371` under H; the latter is the fusion enthalpy at the SOL-B/LIQ
transition. The separate References block's `TPT= 870., 1270.` cell is text,
not a numeric MinerU cell, and both temperatures were checked against the
render.

## Reference and disagreement check

The References block prints, for SOL-B, H/S sources `Nbl,Tkl`, Cp source `e`,
and the remark source `Tkl` with `TPT=870.,1270.`. For LIQ it prints H/S source
`Tkl` and Cp source `e`. The page prints no alternate numeric NaFeO2 dfH298,
S298, Cp, heat-content, or fusion value alongside these citations. Its
reference key labels `e` as estimated data; no additional value is supplied in
the References block.

| Property | Barin (1995) value and locator | Barin–Knacke–Kubaschewski (1977) evidence and locator | Difference |
| --- | --- | --- | --- |
| ΔfH298 | −698.787 kJ/mol, SOL-B at 298.15 K; printed p. 708 / PDF p. 823, FeNaO2 row 298.15, ΔHf column | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |
| S298 | 88.299 J/(K mol), SOL-B at 298.15 K; printed p. 708 / PDF p. 823, FeNaO2 row 298.15, S column | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |
| Cp(T) | SOL-B, 84.517–102.105 J/(K mol), 298.15–1620 K; LIQ, 125.520 J/(K mol), 1620–2000 K; printed p. 708 / PDF p. 823, Cp column | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |
| H−H298 | Full table above; printed p. 708 / PDF p. 823, H−H298 column | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |
| Solid-state transition temperatures | TPT 870. and 1270. K; printed p. 708 / PDF p. 823, References, SOL-B row, Remarks column | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |
| High-temperature phase near 1498 K | No delta phase or 1498 K transition on printed p. 708 / PDF p. 823 | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | No 1498 K comparison; the index pointer is to the 1973 volume, p. 551 |
| Fusion | 1620.00 K; ΔHfus 49.371 kJ/mol; ΔSfus 30.476 J/(K mol); printed p. 708 / PDF p. 823, transition-increment row, H and S columns | No numerical value; index alias `Na2O·Fe2O3`, printed p. LXVIII / PDF p. 68, V.=1, P.=551; volume key printed p. LIII / PDF p. 53 | Not comparable; the index pointer is to the 1973 volume, p. 551 |

The corpus search for numerical entries in the 1977 supplement covered
`NaFeO2`, `FeNaO2`, the pdftotext OCR form `FeNa02`, “sodium ferrite,”
“iron sodium,” and the oxide form `Na2O·Fe2O3`. Its index entry is at printed
p. LXVIII / PDF p. 68, with V.=1, P.=551. The volume
key at printed p. LIII / PDF p. 53 identifies volume 1 as the 1973 edition, so
the index points to that edition's p. 551 rather than to a numerical NaFeO2
table in the 1977 supplement. The 1973 volume is not present in the corpus
`raw/` or `text/` source directories; they contain the 1977 supplement and
Barin 1995, but no Barin 1973 volume. No numerical 1977 value is available to
compare with the 1995 values, and the 1995 page does not support a delta phase
near 1498 K.

## Cell checks

| Stored values | Page / printed row and column | Confirmation |
| --- | --- | --- |
| Every SOL-B Cp, S, ΔfH, and H−H298 value | PDF p. 823 / SOL-B rows, columns 3, 4, 9, and 7 | 400 dpi page render; row/column layout checked once, all values checked |
| Every LIQ Cp, S, ΔfH, and H−H298 value | PDF p. 823 / LIQ rows, columns 3, 4, 9, and 7 | 400 dpi page render; row/column layout checked once, all values checked |
| 30.476 and 49.371 | PDF p. 823 / transition-increment row, columns S and H | 400 dpi page render |
| TPT 870. and 1270. | PDF p. 823 / References, SOL-B row, Remarks | 400 dpi page render; MinerU combines both values in a text cell |

The extract's fidelity samples independently pin Cp 84.517, S298 88.299, ΔfH
−808.006, H−H298 123.343, both fusion increments, and both TPT values to PDF
page 823. The focused test also mutates a pinned value and removes a table row
to prove those checks fail. The `mineru_table_draft.py compare` output has 67
matches and 20 mismatches: all 20 flagged cells are the LIQ values, whose
printed values match the page render. The comparator's phase filter accepts
`solid`, `liquid`, and `gas`, but the extract uses canonical phase token `l`,
so it does not select those candidate cells. The page render resolves these
20 tool mismatches; the comparison CSV preserves them for review. Its numeric
matcher also does not split the References cell `TPT=870., 1270.`, so those two
values were verified directly against the render.

The printed Cp and entropy unit is `J/(K mol)`. The extract uses canonical
unit token `J/mol/K` for migration and retains the printed spelling in
`units_note`; no numeric unit conversion was made.

## Migration and stored delta

The extraction uses the existing `cp`, `S`, `delta_fH`, `H_minus_H298`,
`transition_temperature`, `heat_of_fusion`, and `entropy_of_fusion` tokens.
The solid and liquid phase labels remain as printed in `phase_label_as_printed`
and `condensed_form`; migrator phases are `cr` and `l`. Only printed
temperatures are carried into point conditions; the page prints no total
pressure, oxygen fugacity, or additional composition condition. The focused
test runs the isolated real migrator and checks all conditions and phases. The
isolated migration produced 89 observations, 0 hard issues, and 0 unavailable
values. All 89 observations carry a page-located temperature condition; the 86
observations whose quantity profile uses the identity temperature have that
axis populated from the same source value. The three transition-temperature
observations use `T_m_K` as the value and correctly mark identity temperature
not applicable.

| Migrated row | composition | total_pressure_Pa | fO2_Pa | identity temperature_K | point-condition T |
| --- | --- | --- | --- | --- | --- |
| SOL-B Cp at 298.15 K | not applicable | not applicable | not applicable | 298.15 | 298.15, PDF p. 823 |
| SOL-B S at 298.15 K | not applicable | not applicable | not applicable | 298.15 | 298.15, PDF p. 823 |
| TPT at 870 K | not applicable | unknown, source does not state numeric pressure | not applicable | not applicable | 870, PDF p. 823 |

Expected generated-store delta: 89 observations from this extract and its
source metadata. No generated battery store is committed or regenerated in
this change.
