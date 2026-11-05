# Kale and Srikanth sodium ferrite extraction audit

## Scope and result

The requested NaFeO2 empirical composition is represented by the paper's Na2Fe2O4 formula. The extraction retains the paper's Na2Fe2O4 formula and J/mol values unchanged; it does not divide the equations or values by two. These Gibbs-energy equations are from Na2O and Fe2O3, not elemental standard formation energies.

Table I has seven printed Na2Fe2O4 equation rows. The source also gives Equation (10), yielding eight stored thermochemical observations. Table I and Equation (10) report coefficient-only Gibbs-energy functions, not point data. They are preserved as typed refusals with the printed equations, units, intervals, formula basis, phase labels when present, and references. No absolute pressure, oxygen fugacity, or single-point temperature is supplied for the quoted fits; no such condition was added.

The paper prints no standard formation enthalpy at 298 K, S298, Cp(T) function or table, H(T)-H298, intrinsic α/β/γ/δ transition temperature or enthalpy, δ phase near 1498 K, or fusion temperature or enthalpy. Figure-only curve values are not digitized.

## Table I full-cell audit

MinerU and the exact born-digital pdftotext layer agree on the seven equations and seven temperature ranges. The draft flags all seven en-dash/minus cases; the second Zhang equation also carries a superscript double-dagger marker. The 400 dpi render of absolute PDF page 2 was used for every equation and range cell (verification method b), and once for row and column assignment. The rows and printed ranges are:

| Table I row | Equation, J/mol | T range, K | Confirmation |
|---|---|---:|---|
| Shaiu et al. [11] | −171 970 − 9.456T | 795–1048 | Page 2 render; MinerU and pdftotext agree on digits |
| Yamaguchi et al. [8] | −160 200 − 3.909T | 930–1047 | Page 2 render; MinerU and pdftotext agree on digits |
| Yamaguchi et al. [8] | −152 700 − 1.332T | 1047–1278 | Page 2 render; MinerU and pdftotext agree on digits |
| Yamaguchi et al. [8] | −147 300 − 13.37T | 1278–1405 | Page 2 render; MinerU and pdftotext agree on digits |
| Zhang et al. [1] | −237 425 + 83.1T | 635–785 | Page 2 render; MinerU and pdftotext agree on digits |
| Zhang et al. [1] | −247 086 + 89.435T‡ | 834–1004 | Page 2 render confirms superscript ‡; MinerU and pdftotext agree on digits |
| Dai et al. [10] | −86 000 − 61.89T | 773–1030 | Page 2 render; MinerU and pdftotext agree on digits |

The printed table count is seven target rows. Table II has three Na3Fe5O9 rows and is excluded. The marker † in the Table I header invokes the default reference-electrode footnote; the second Zhang row's ‡ says the Na2O activity was taken from Kale [12]. The equations' polymorph is not stated in Table I.

## Other stored numeric cells

| Cell group | Printed location | Printed count | Audit method and result |
|---|---|---:|---|
| Equation (10), equation, temperature-range, and uncertainty cells | absolute PDF page 4, printed page 178 | 5 numeric cells | Rendered page 4 confirmed `−232 582 + 69.61T`, 930–998 K, and `±5500 J/mol`; pdftotext also retains the standard-error statement. The confidence level is not stated. |
| Table III temperature and time cells | absolute PDF page 3, printed page 177 | 14 rows x 2 numeric cells | Table QA digit agreement was 0.771 and the MinerU draft counted a repeated header as a body row. The 400 dpi render confirmed the row/column layout and all 28 values. The rendered table and pdftotext have 14 actual data rows. |
| Table III initial-composition cells | absolute PDF page 3, printed page 177 | 14 rows | Rendered row assignment checked once. Thirteen rows involve Na2Fe2O4 as initial or final phase and are retained as context; the 1273 K Na2CO3/Fe2O3 row lists only Na3Fe5O9 and Fe2O3 and is out of scope. |

For the 13 retained Table III context rows, the stored temperature/time pairs in body-row order are 923/24, 948/48, 973/24, 998/48, 1023/24, 1048/24, 1073/48, 1098/48, 1123/48, 1148/48, 1173/48, 1198/48, and 973/72 (K/h). All were render checked. Table III is phase-equilibration evidence, not standard-state transition enthalpy data.

## Side-by-side Na2Fe2O4 Gibbs-energy values

| Cited source | T range (K) | Gibbs-energy equation as printed (J/mol) | Source's comparison |
|---|---:|---|---|
| Shaiu et al. [11] | 795–1048 | −171 970 − 9.456T | Kale and Srikanth call it much lower than their value, Zhang, and Yamaguchi; they question its reliability because of the non-coexisting phase assemblage and electronic conduction in the zirconia electrolyte. |
| Yamaguchi et al. [8] | 930–1047 | −160 200 − 3.909T | The 930–998 K overlap agrees with the present Gibbs energy within ±5 kJ/mol. |
| Yamaguchi et al. [8] | 1047–1278 | −152 700 − 1.332T | Separate higher-temperature Table I fit; no separate present Na2Fe2O4 point series is tabulated. |
| Yamaguchi et al. [8] | 1278–1405 | −147 300 − 13.37T | Separate higher-temperature Table I fit; no separate present Na2Fe2O4 point series is tabulated. |
| Zhang et al. [1] | 635–785 | −237 425 + 83.1T | The paper compares the present cell-I result with Zhang's re-derived emf data and reports the high-level agreement; it does not print a numeric residual for this interval. |
| Zhang et al. [1] | 834–1004 | −247 086 + 89.435T‡ | This Table I fit uses Kale [12] Na2O activity; the paper describes the present result as agreeing with Zhang and Yamaguchi in the measurement range. |
| Dai et al. [10] | 773–1030 | −86 000 − 61.89T | The paper says Dai's Na2Fe2O4 Gibbs energy is much more positive than the present result, Yamaguchi, and Zhang. |
| Present study, Eq. (10) | 930–998 | −232 582 + 69.61T (±5500 J/mol) | Measured cell-I emf reduced with Kale [12] reference activity. The printed ±5500 J/mol is the sum of the reference-electrode-data and emf-measurement errors; confidence level not stated. |

The source says the Yamaguchi Gibbs energies agree within ±5 kJ/mol in the present measurement range, while the second-law formation enthalpy and entropy differ considerably. It describes Dai's derived second-law entropy as highly positive and the present and Zhang results as negative with similar magnitude. It gives no numeric Na2Fe2O4 ΔfH or S values in this paper, so none were calculated from the equation slopes or intercepts.

## Phase-boundary comparison and exclusions

The paper reports 1048 ± 10 K from phase equilibration, 1028 ± 5 K from the intersection of two emf segments, and 1030 ± 5 K from Thery [5]. It treats these as the boundary between lower-temperature Na2Fe2O4 + Fe2O3 and higher-temperature Na3Fe5O9 + Fe2O3 assemblages, not as an intrinsic NaFeO2 α-to-β or δ transition. Table III lists minor β-Na2Fe2O4 at 1048 and 1073 K. The authors say their result places Na3Fe5O9 as unstable below 1073 ± 10 K, unlike Yamaguchi's reported stability down to 850 K. No 1498 K δ phase or fusion property is given.

## Schema and migration audit

`tools/validate_literature_extracts.py::OBSERVATION_TYPES` permits `gibbs_table`; `simulator/battery/migrate.py::TYPE_QUANTITY` and `QUANTITY_ALIASES` map the declared `delta_fG`; `simulator/battery/migrate.py::PHASE_MAP` maps the condensed-solid token `cr`; and `simulator/battery/enums.py::Polymorph` supports `alpha` for Eq. (10). These candidates are supported, so none was relabeled as a descriptive unknown. The typed refusal is limited to the coefficient-only equation form: although `simulator/battery/enums.py::ValueKind` includes `EXPRESSION`, `simulator/battery/migrate.py::select_declared_source` does not interpret these printed coefficient strings as numeric Gibbs-energy points. The extract follows the existing `chatain-2021-nacro2-thermodynamics.yaml` treatment of attributed coefficient-only Gibbs fits. The authors' own Eq. (10) is marked measured_reduced; its plotted numeric emf inputs are declared not_printed at Figure 2, absolute PDF page 3, and the printed reduction relation is located at Equations (8)-(10), absolute PDF page 4.

Migration acceptance is pinned by the focused test: eight observations, eight unavailable values (typed equation refusals), zero hard issues, no unknown quantity or phase token, and no invented point temperature, pressure, or fO2. Each temperature interval is forwarded to the temperature-axis queue without a midpoint. For the first three rows, identity `composition`, `total_pressure_Pa`, and `fO2_Pa` are `not_applicable` for the `delta_fG` profile; `temperature_K` is unknown only as a point and carries the printed range in its reason and queue entry. The printed formula remains Na2Fe2O4. Table I has no stated polymorph; Eq. (10)'s α polymorph is mapped with the existing `condensed_form.polymorph` path.

The committed MinerU compare CSV has only its header and the tool reports `matched=0`, `mismatch=0`, `unverifiable=0`, `total=0`: the comparison tool compares numeric point cells, and all eight stored observations preserve coefficient equations as typed refusals. Their source cells are audited above against the render.
