# NASA Glenn / CEA `thermo.inp` compilation

Assessed NASA-9 polynomial coefficients for individual species. This is a
**reference function** the engine may consume. It is **not** a validation
source and produces no battery scoring rows
(`gibbs_table_not_runtime_observable`).

## Provenance

- McBride, B. J., Zehe, M. J. and Gordon, S., *NASA Glenn Coefficients for
  Calculating Thermodynamic Properties of Individual Species*,
  NASA/TP-2002-211556.
- Source file: CEA standalone `thermo.inp` dated 9/8/2021 (Snyder T-range
  and inert-species updates on the Glenn coefficient database).
- Official URL: <https://www.grc.nasa.gov/www/CEAWeb/>
- Licence: US government work; 17 U.S.C. § 105 (no U.S. copyright).
- Local snapshot: `source/thermo.inp`
- SHA-256: `fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363`

Harvest: `python tools/harvest_nasa_glenn_compilation.py`. Loader:
`simulator/reference_data/nasa_glenn.py`.

## Native format (no conversion)

Each `records/NG-NNNN.json` is one thermo.inp species/phase record:

1. Name line — cols 1–18 species name, remainder citation.
2. Header — `I2,1X,A6,1X,5(A2,F6.2),I2,F13.7,F15.3`: interval count,
   reference/date code, five element/count slots, phase flag, molecular
   weight, ΔfH°(298.15) as printed (J/mol).
3. Per temperature interval — `2F11.3,I1,8F5.1,2X,F15.3` then two 16-character
   coefficient lines: seven `a` coefficients, unused `a8` field, integration
   constants `b1` and `b2`. Exponent sets and H(298)−H(0) are stored as
   published.

`nint=0` assigned-enthalpy rows keep the dummy T-line and have no
coefficients. Inverted or zero-width T intervals are kept. Phases are not
merged. Units are not converted.

## What the engine may consume it for

Cp°/R, H°/RT, S°/R and G°/RT from the published NASA-9 coefficients over the
declared T intervals, plus ΔfH°(298.15) and molecular weight as printed.
Do not score this compilation in the measurement battery.

The runtime vapour-rail evaluator in `simulator/vapour_rail/nasa_cea.py` is
a separate consumer of *selected* coefficients. This compilation is the
complete, unfiltered database.

## Held extract (wrong home, left in place)

`data/literature/extracts/nasa-cea-thermo.yaml` (1615 species) is a filtered
MC-2 extract: gases plus feedstock-condensed species, ions / D-T isotopes /
nobles / condensed non-feedstock omitted, and the parser stopped at
`END PRODUCTS`. It remains in `extracts/` this round so existing consumers
(`simulator/chemistry/offgas_fo2.py`) keep their path. **It is superseded
by this compilation.**

The 2029-record figure from the vacuum scout is the products-only parse
after dropping inverted T-intervals (`Br2(cr)` skipped; ten other species
truncated). This harvest keeps every record: 2030 products + 81 reactants
= **2111**.

Against those 2111 records the held extract lacks **485** (404 products +
81 reactants). The brief's "403" is the product-side gap (404 here; the
extra one is `Br2(cr)`, which the scout parser dropped before the extract
filter). Full list: `manifest.yaml` key `held_extract_missing_records`.

### Product records the extract lacked (404)

e-, Ag+, Ag-, AL+, AL-, ALCL+, ALF+, ALF2-, ALF4-, ALO+, ALO-, ALOF2-, ALO2-, AL2O+, AL2O2+, Ar+, B+, B-, BCL+, BCL2+, BF2+, BF2-, BF4-, BO-, BO2-, Ba+, BaCL+, BaF+, BaO+, BaOH+, Be+, Be++, BeH+, BeOH+, Br+, Br-, C+, C-, CF+, CF2+, CF3+, CH+, CH2OH+, CN+, CN-, CO+, CO2+, C2+, C2-, C3H4,cyclo-, C3H6,cyclo-, C4H4,1,3-cyclo-, C4H6,cyclo-, C4H8,cyclo-, C5H6,1,3cyclo-, C5H8,cyclo-, C5H10,cyclo-, C6D5,phenyl, C6D6, C6H10,cyclo-, C6H12,cyclo-, Ca+, CaCL+, CaF+, CaO+, CaOH+, Cd+, CL+, CL-, Co+, Co-, Cr+, Cr-, CrO3-, Cs+, Cs-, Cs2O+, Cu+, Cu-, D, D+, D-, DBr, DCL, DF, DOCL, DO2, DO2-, D2, D2+, D2-, D2O, D2O2, D2S, F+, F-, Fe+, Fe-, Ga+, Ge+, Ge-, H+, H-, HBO+, HBS+, HCO+, HD, HD+, HDO, HDO2, HO2-, H2+, H2-, H2O+, H3O+, He+, Hg+, I+, I-, In+, K+, K-, K2+, K2O+, Kr+, Li+, Li-, Li2+, Li2O+, Li3+, Mg+, MgCL+, MgF+, MgF2+, MgOH+, Mn+, Mo+, Mo-, MoO3-, N+, N-, ND, ND2, ND3, NH+, NH4+, NO+, NO2-, NO3-, N2+, N2-, N2D2,cis, N2O+, Na+, Na-, NaOH+, Na2O+, Nb+, Nb-, Ne+, Ni+, Ni-, O+, O-, OD, OD-, OH+, OH-, O2+, O2-, P+, P-, PCL2-, PF+, PF-, PFCL-, PF2-, PH2-, PO-, PO2-, Pb+, Pb-, Rb+, Rb-, Rn, Rn+, S+, S-, SCL2+, SD, SF+, SF-, SF2+, SF2-, SF3+, SF3-, SF4+, SF4-, SF5+, SF5-, SF6-, SH-, SO-, SO2-, S2-, Sc+, Sc-, ScO+, Si+, Si-, SiH+, Sn+, Sn-, Sr+, SrCL+, SrF+, SrO+, SrOH+, Ta+, Ta-, Ti+, Ti-, TiO+, UF+, UF-, UF2+, UF2-, UF3+, UF3-, UF4+, UF4-, UF5+, UF5-, UF6-, UO+, UO2+, UO2-, UO3-, V+, V-, W+, W-, WO3-, Xe+, Zn+, Zr+, Zr-, ZrO+, Ag(cr), Ag(L), B(b), B(L), Ba(cr), Ba(L), BaBr2(cr), BaBr2(L), BaF2(a), BaF2(b), BaF2(c), BaF2(L), BaI2(cr), BaI2(L), Be(a), Be(b), Be(L), BeBr2(cr), BeBr2(L), BeF2(a), BeF2(b), BeF2(L), BeI2(cr), BeI2(L), Br2(cr), Br2(L), Cd(cr), Cd(L), Cs(cr), Cs(L), CsBr(cr), CsBr(L), CsF(cr), CsF(L), CsI(cr), CsI(L), Cu(cr), Cu(L), CuBr(a), CuBr(b), CuBr(c), CuBr(L), CuBr2(cr), CuF(cr), CuF2(cr), CuF2(L), CuI(a), CuI(b), CuI(c), CuI(L), Ga(cr), Ga(L), GaBr3(cr), GaBr3(L), GaF3(cr), GaI3(cr), GaI3(L), Ge(cr), Ge(L), Hg(cr), Hg(L), HgBr2(cr), HgBr2(L), I2(cr), I2(L), In(cr), In(L), InBr(cr), InBr(L), InBr3(cr), InBr3(L), InF3(cr), InF3(L), InI(cr), InI(L), InI2(crII), InI2(crI), InI2(L), InI3(cr), InI3(L), Li(cr), Li(L), LiBr(cr), LiBr(L), LiF(cr), LiF(L), LiI(cr), LiI(L), Mo(cr), Mo(L), Nb(cr), Nb(L), Pb(cr), Pb(L), PbBr2(cr), PbBr2(L), PbF2(II), PbF2(I), PbF2(L), PbI2(cr), PbI2(L), Rb(cr), Rb(L), RbBr(cr), RbBr(L), RbF(cr), RbF(L), RbI(cr), RbI(L), Sc(a), Sc(b), Sc(L), Sn(cr), Sn(L), SnBr2(cr), SnBr2(L), SnBr4(cr), SnBr4(L), SnF2(cr), SnF2(L), SnI2(cr), SnI2(L), SnI4(cr), SnI4(L), Sr(a), Sr(b), Sr(L), SrBr2(a), SrBr2(b), SrBr2(L), SrF2(a), SrF2(b), SrF2(L), SrI2(cr), SrI2(L), Ta(cr), Ta(L), U(a), U(b), U(c), U(L), UF3(cr), UF3(L), UF4(cr), UF4(L), UF5(b), UF5(a), UF5(L), UF6(cr), UF6(L), V(cr), V(L), W(cr), W(L), Zn(cr), Zn(L)

### Reactant-only records the extract lacked (81)

Air, InertAir, B2H6(L), B5H9(L), (CH2)x(cr), CH3NO2(L), CH4(L), CH3OH(L), CH6N2(L), C2H2(L),acetyle, CH3CN(L), C2H4(L), C2H4O(L),ethyle, C2H6(L), C2H5OH(L), C2H8N2(L),UDMH, C2N2(L), C3H6(L),propyle, C3H7NO3(L), C3H8(L), C4H8(L),1-buten, C4H10(L),n-buta, C4H10(L),isobut, C5H12(L),n-pent, C6H6(L), C6H5NH2(L), C6H14(L),n-hexa, C7H8(L), C7H16(L),n-hept, C8H18(L),n-octa, C8H18(L),isooct, CLF3(L), CLO3F, CLO3F(L), CL2(L), F2(L), F2O(L), HNO3(L), H2(L), InertH2(L), H2O2(L), IRFNA, JP-4, InertJP-4, JP-5, InertJP-5, JP-7, InertJP-7, JP-10(L), InertJP-10(L), JP-10(g), InertJP-10(g), Jet-A(L), InertJet-A(L), Jet-A(g), InertJet-A(g), LiCLO4(cr), NF3(L), NH3(L), NH4CLO4(I), NH4CLO4(II), NH4NO3(IV), NH4NO3(III), NH4NO3(II), NH4NO3(I), NH4NO3(L), N2(L), N2H4(L), N2O4(L), O2(L), InertO2(L), O3(L), RP-1, InertRP-1, Paraffin, ADN, Biodiesel, HAN, LMP-103S, n-Butanol, n-Butanol

## Known gaps and recorded ambiguities

- 11 inverted or zero-width polynomial T intervals (Snyder 2021 floor
  artifact), including `Br2(cr)` whose only interval is inverted. Kept.
- 54 `nint=0` assigned-enthalpy dummy T-lines (`T_max=0`, no coefficients).
- Electron `e-`: phase flag and MW share columns (`0.000548579903`).
- CEA inert codes `IO`/`IH`/`IC` are not IUPAC symbols.
- Duplicate `name_as_published` values (allotropes / gas vs condensed
  reactants) are separate records.
- 18-character name field truncates some comments (`C2H2(L),acetyle`).
- Feedstock REE/PGM elements with no thermo.inp record: As, Au, Bi, Ce, Dy,
  Er, Eu, Gd, Hf, Ho, Ir, La, Lu, Nd, Os, Pr, Pt, Sb, Se, Sm, Tb, Te, Tm,
  Y, Yb.

Nothing in this compilation is typed `measured`.
