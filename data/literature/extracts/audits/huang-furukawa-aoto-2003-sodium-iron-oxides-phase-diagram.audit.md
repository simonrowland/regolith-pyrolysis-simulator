# Huang, Furukawa and Aoto (2003) Part II extraction audit

## Table census and numeric cell audit

MinerU found the printed Table 1 on absolute PDF page 2 (printed page 68), with
13 body rows and six columns. Two body rows are merged formula headings, leaving
11 data rows and 55 standalone numeric cells. All 55 cells agree between the
MinerU HTML table and pdftotext-layout; the page 2 render confirms the row and
column assignments, signs, decimal places, and the five compound headings.
The full cell-by-cell record is in the companion cell-audit CSV.

MinerU QA flagged the table (70 numeric-like cells, 0.902 numeric agreement,
15 LaTeX flags and six numeric mismatches). Its numeric-like count includes
numbers embedded in the two merged formula rows and duplicate LaTeX expressions
in colspan cells. Those are not numeric data cells. The six digit mismatches
are the g4 and g5 formula-label digits repeated across the six cells of the
merged G(T) row; the text layer encodes the superscripts as glyphs, so the QA
digit search does not match those formula labels. They are not value cells.
The 55 standalone numeric cells are each independently reconciled to pdftotext
and checked against the render. No numeric PDF cell needed a hand correction.
The table reader's 70-cell count is therefore not the printed data-row count.

Table 1 has five columns of compounds: NaFeO2, Na4FeO3, Na3FeO3, Na5FeO4 and
Na8Fe2O7. It prints three standard thermodynamic properties per compound
(15 values) and 40 coefficients for Cp(T) and G(T). The 15 standard-property
values are migrated as observations. All 40 function coefficients are kept in
located context, with the printed formulas and authors' attributions; the
coefficients do not themselves represent a heat-capacity or formation-Gibbs
value. The exact candidate tokens checked were
`simulator/battery/enums.py::Quantity.CP` and
`simulator/battery/enums.py::Quantity.DELTA_FG`.

## Attribution and Part I overlap

- NaFeO2 thermodynamic values and equation (1) are attributed to the SGTE
  assessment. Equation (1) is transcribed exactly as printed: subsection 2.2.1
  is about NaFeO2, while the equation labels its left-hand side Na4FeO3.
- Na4FeO3 formation Gibbs energy is attributed to Bhat and Borgstedt [9]. Its
  formation enthalpy is the authors' equation (3) calculation from the printed
  Gibbs energy and entropy; entropy is attributed to Lindemer et al. [3]. The
  Cp parameters are adopted from MALT2 [12] and described as estimates from
  component oxides.
- Na3FeO3 formation Gibbs energy and vapor-pressure measurements repeat Part I
  [13]. Part I's 25 pressure and reaction rows remain in
  `huang-furukawa-aoto-2003-sodium-iron-oxides-kems`; they are not duplicated as
  Part II measurements. The Part II Table 1 formation Gibbs value is recorded
  as quoted from Part I. The Part II estimates of formation enthalpy and
  entropy retain located derivation notes.
- Na5FeO4 formation enthalpy and entropy are attributed to Lindemer et al. [3].
  The authors report no phase transitions to 1000 K and estimate heat capacity.
- Na8Fe2O7 formation enthalpy is attributed to Stuve et al. [14], and entropy
  to Lindemer et al. [3]. Its heat-capacity parameters are described as
  estimated from Na3FeO3 and Na5FeO4.
- Equation (4) and the Na3FeO3 formation-Gibbs fit are Part I overlap. The
  Part II formation enthalpy and entropy estimates are marked measured_reduced;
  their raw inputs are the authors' Part I vapor-pressure measurements, which
  are not printed in this Part II paper and are located at its section 2.2.3.
  Equations (7)–(10) are Part II results: the text says the authors
  confirmed them by vapor-pressure measurements and XRD in their laboratory,
  so they are marked measured_direct. Their reaction stoichiometry and ranges
  are checked against the PDF render on page 5. Equation (3) is retained as a
  printed relation; equations (1), (2), (4), (5), and (6) are transcribed
  exactly.

The controller ruled that the paper's computations from literature-attributed
thermodynamic inputs are model_derived. The Na4FeO3 Table 1 enthalpy is
recorded that way, with its equation (3) method and the Table 1 Gibbs-energy
and entropy inputs. For Na8Fe2O7, Table 1 governs the stored quantities:
−2524.3 is ΔfG° and is model_derived from Stuve's ΔfH° and Lindemer's S°;
−2746.0 is ΔfH° and is quoted_attributed to Stuve. The page 3 prose instead
labels −2524.3 ΔfH°. The 600 dpi render confirms the H glyph; this printed
discrepancy is attached to the −2524.3 ΔfG° row, not stored as a second
quantity.

## Phase equilibria and conditions

The paper contains no table of invariant or three-phase equilibrium data. The
Na–Fe–O diagrams and their invariant geometry are figures, deferred as required.
The prose reports Na(l), Fe(s), and Na4FeO3(s) coexisting over 694 K. It lists
four reaction equations over 1288–1380 K, 823–1023 K, 603–673 K, and 590–717 K.
It explicitly says oxygen partial pressures in those conditions were too low
to measure; no numeric oxygen potential is printed. No pressure or fO2 is
invented for the Table 1 thermodynamic values.

The other adopted data identified in the introduction and thermodynamic
sections are indexed in the extract context: Dai et al. [1,2], Lindemer et al.
[3], Seetharaman and Du [4], Sridharan et al. [5,6], Bhat and Borgstedt [9],
MALT2 [12], Part I [13], Stuve et al. [14], SGTE [19], and the excluded
Na2FeO2 and higher oxides. Figure-only phase boundaries and plotted Gibbs
comparisons are listed as figure-read candidates and not digitized.

## Migration and tests

The isolated real migration emits 15 observations: 15 point values, zero hard
issues, and zero quantity-unknown or phase-unknown observations. Each migrated
row carries 298 K and the Table 1 locator; pressure and fO2 are not assigned.
The profile-required identity axes were checked for NaFeO2 ΔfG, Na4FeO3 ΔfH,
and Na3FeO3 entropy: each has temperature_K = 298, while composition,
total_pressure_Pa, and fO2_Pa have no value. The latter two are not emitted as
point conditions.
The table-aware compare report matches all 15 migrated standard-property
values with zero mismatches or locator errors. The focused test pins all 15
standard-property values and all 40 coefficients,
checks five rendered locator samples, checks all migrated row identities and
conditions, and demonstrates the fidelity guard fails after a one-cell
mutation.

Expected regenerated store delta: +15 observations (15 point values). No
battery-store regeneration is committed.
