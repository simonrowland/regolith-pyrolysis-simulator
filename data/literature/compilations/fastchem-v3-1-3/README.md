# FastChem v3.1.3 mass-action fits

Frozen tag commit `51857a84e2b25d4246624c978cdebe988789a729` (v3.1.3, not current master). Compiled and
refitted literature constants, not independent experiments. The harvester
copies the seated files and stores each printed coefficient token. It does
not refit them and does not store a converted twin.

## Fit

File header, gas and condensate files, first line:

```
#logK = a1/T + a2 ln T + a3 + a4 T + a5 T^2 for FastChem:
```

Authority for the form, the logarithm, the standard pressure, and the
reaction direction is the seated method papers.

Stock, Kitzmann & Patzer (2022), FastChem 2, arXiv:2206.08247v2,
corpus `stock-2022-fastchem2-methods`, preprint p. 3, Eq. (8):

> The temperature dependent mass action constants for each molecule or ion are fitted as before ln Kbar_i(T) = a0/T + a1 ln T + b0 + b1 T + b2 T^2, with the five fit coefficients a0, a1, b0, b1, and b2.

Same page, the sentence around Eq. (9):

> The natural logarithm of the dimensionless mass action constants are related to the change in the Gibbs free energy of the dissociation reaction (1) with respect to the standard-state pressure p° = 1 bar (= 10^5 Pa)

Preprint p. 2: "dissociative equilibrium is assumed, where the dissociation reaction" (Eq. 1). The mass-action sentence is
"can be calculated using the law of mass action (Guldberg & Waage 1864, 1867, 1879)", and Eq. (5) transcribes as `n_i = K_i * product_j n_j**nu_ij, for all i in S excluding the elements`.
The paper continues: "where n_i is the number density of species i and K_i the temperature dependent mass action constant".

Kitzmann, Stock & Patzer (2024), FastChem Cond, arXiv:2309.02337v2,
corpus `kitzmann-2024-fastchem-cond-methods`, preprint p. 16:

> For the data used in the FastChem code, p° is equal to 1 bar.

Eq. (A6):

> As stated by Stock et al. (2018), we parameterise ln Kbar_c as a function of the temperature T in form of the polynomial ln Kbar_c(T) = a0/T + a1 ln T + b0 + b1 T + b2 T^2, where a_i and b_i are the corresponding fit coefficients that are tabulated in the FastChem input file for each species.

Preprint p. 15: "The dimension-less equilibrium constant Kbar_c(T) for a specific condensate c is calculated from the Gibbs free energy of reaction". "where Delta_r G_c is the change of Gibbs free energy for the dissociation reaction"
"of a condensate species into its elements" (Eq. A2). Eq. (A3) prints
`G_c(T) - sum_j nu_cj * G_j(T)`. "Thus, the ln Kbar used in FastChem and given in Table A1 should not be confused with the corresponding ln Kbar tabulated in thermochemical databases, such as the JANAF tables."

The stored tokens are the printed polynomial. They are not inverted to
force Eq. (5) and Eq. (9), or Eq. (A2) and Eq. (A3), onto one sign.
`reaction_direction_status` is `not_resolved`.

The header's `ln T` is a natural logarithm (`std::log` in tag
`molecule_struct.cpp` / `condensate_struct.cpp`). Column order on the
file is a1, a2, a3, a4, a5. Eq. (8) and Eq. (A6) name that same order
a0, a1, b0, b1, b2. Those paper names are not applied to the stored tokens.

The coefficient-packet SOURCE.md is not the authority above. It says:
Stored fits use natural logarithms, T in kelvin and standard pressure 1 bar. Operational mass action is n_i = K_i product(n_j^nu_ij): association of atomic components into a species, inverse to a conventional dissociation constant. Do not infer reaction direction solely from that prose or read coefficients as elemental-reference formation Gibbs energies.

Tag `calcNumberDensity` evaluates

`n_species = exp(ln K_adjusted + sum_j nu_j * ln n_j)`

with `nu_j` the printed stoichiometric powers. Electrons may be negative:
`charge = -stoich(e-)`, so `e- -1` is a cation and `e- 1` is an anion.

Standard pressure is 1 bar. Gas code comment: "adjusting log_K from its standard pressure (1 bar = 1e-6 dyn cm-2) to the actual pressure".
Implemented gas factor `1.0e-6 * CONST_K * temperature` with `sigma = 1 - sum(stoichiometric_vector)`.
That factor is `kT/1e6`, and 1 bar = 1e6 dyn cm^-2, so the parenthetical
`1e-6 dyn cm-2` is reversed. Condensates use `1.0e6 / (CONST_K * temperature)`
with `sigma = sum(stoichiometric_vector)`. Neither factor is applied to the stored
coefficients.

## Temperature bounds

Gas records print no per-species temperature bound. Solver checks at
100-6000 K are not stored as fit validity. Condensate numbers on the limit
line are successive upper bounds: the reader takes the first limit that is
`>= T`, and the activity helper can refuse `T` above the last limit. No
lower bound is printed, so `T_low_K` is unknown. `sl` is one species with
two coefficient rows, not two species. The file does not label row 0 solid
and row 1 liquid.

## What was ingested

861 records: 495 standard
gas (`FC-G`), 180 extended-only gas ions
(`FC-X`), 186 condensates (`FC-C`).
`logK_extended.dat` repeats the standard gas rows with the same headers and
coefficient tokens (0
disagreements) and adds the ions. Shared rows are stored once, from the
primary gas file. `logK_wo_ions.dat` is named in README.TXT and is not in
the seated packet.

Per-record citations are the text after `#`. Most gas and condensate rows
cite Chase, JANAF thermochemical tables, 1998. The extended ions are mostly
Saha/Kurucz/NIST; one cites Gordon and McBride, NASA/TP--1999-208523.
Two symbols disagree with the printed stoichiometry and both are kept:
`NaCO3(s,l)` is `C 1 Na 2 O 3`, and `F2Si1` (Difluorosilylene) is
`F 2 Si 2`. The formula used for identity is the stoichiometry, not the
symbol. Isomer tags such as `_1`, `_cnn`, `cis`, and `trans` stay on the
symbol; they are not a second formula. No `Na4SiO4` or `Na6Si2O7`.

## Scorer

`compilation_role.kind` is `fit`, `method_class` is `fit`,
`evaluation_class` is `compiled`, and `scoring_eligible` is false.
The record has no closed Quantity: the columns are a mass-action ln K
polynomial, not log10 Kf and not a Gibbs table. A later store rebuild
would write `observations-v2/compilations-fastchem-v3-1-3.yaml` with one
observation per record, quantity unknown, value unavailable because
`source_form` and `coefficients` are set, temperature unknown, evidence
`compilation_assessed`. Gas phase `gas` maps to phase G. Phase
`condensed` is not in the migrator's phase map, so those observations
stay phase-unknown. No scoring row is added, and no authority-ladder
preference was added. Engines are not wired to these coefficients.

## Overlap

`overlap.tsv` lists every FastChem species whose stoichiometry, charge,
and phase class also occur in JANAF, Burcat, NASA Glenn, or ATcT.
708 of 861
FastChem records meet at least one peer (2650 pairs;
species by peer: atct 207, burcat 456, janaf 652, nasa-glenn 590). Neither side is deleted. gas tokens g/gas/(g) and an ATcT leading 'g' plus an electronic-state qualifier; condensed tokens s/solid/l/liquid/liq/cr/c/condensed/crystal and the Burcat/NASA polymorph labels a/b/d/I-IV/gr/alpha/beta/gamma/am; JANAF phase ref meets both classes because that table prints crystal, liquid, and ideal-gas rows; aqueous and not_parsed meet neither. NASA-7 formula element E is the electron in Burcat and NASA Glenn composition slots. No compilation is preferred over another.

Unparsed peer rows are not matches and are not dropped from those
compilations: {'burcat': 80, 'nasa-glenn': 0, 'janaf': 0, 'atct': 1033}. ATcT parentheses
(`CH3C(O)OH`, `OS(O)O`) are structural formulas, not a flat element list.
Burcat rows in that count have no usable element composition (comment-only
notes and non-element slot tokens). Leaving them unmatched under-counts
overlap.
