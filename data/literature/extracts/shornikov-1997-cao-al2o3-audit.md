# Shornikov 1997 CaO–Al2O3 fusion enthalpies — extraction audit

Source: Shornikov, S. I., V. L. Stolyarov, and M. M. Shul'ts (1997), The thermodynamic properties and estimation of the enthalpy of fusion of the components in the CaO–Al2O3 system, Russian Journal of Physical Chemistry, vol. 71, no. 1, pp. 23–27. Translated from Zhurnal Fizicheskoi Khimii, vol. 71, no. 1, pp. 28–32. PDF sha256 5fa6239f43e3ae8749d67aec19450cab9476bc6afa373015ab84660d5ce9ae7b. Five letter pages. pdfinfo has no Title. This is not Shornikov, Stolyarova, and Shul'ts, Russian Journal of Physical Chemistry 71(1):19–22, which is already extracted as shornikov-1997-cao-alumina-vapor.

## Tables

MinerU `p002-t01` was emitted (28 cells, skipped empty or dash = 0). Every emitted cell text matches `pdftotext-layout.txt` and the 600 dpi page. No `needs_render_check` flag. The ± widths were split after emit into a central value and `sigma`. The printed zero cells (ΔH_CaO and ΔS_CaO at x(CaO) = 0.750, and ΔH_CaO at 0.860) are stored as 0.0. They are not dashes.

Emit writes partial enthalpies on `values.value`. Migration reads `partial_molar_enthalpy` from one `partial_molar_enthalpy_*` numeric field, so the central value was moved to `partial_molar_enthalpy_kJ_mol` and the printed ± kept on `partial_molar_enthalpy_as_printed`. Partial entropies stay on `entropy.value` with nested units `J/mol/K`, because the printed header `J/(mol K)` does not match the heat-unit token list. The CaO entropy header prints an extra closing parenthesis, `J/(mol K))`. The Al2O3 entropy header prints `J/(mol K)`. Digits were not edited.

The table prints only x(CaO). x(Al2O3) on each row is the binary complement of that printed value. Temperature is the caption range 1833–2033 K (`T_range_K`), not a midpoint.

Table 2 (`p003-t02`) is a two-column page. The compound name is a spanning header, not a column, and MinerU folded the first right-column data row into the header. Emit cannot assign the compound. The eleven This-work rows were entered from the 600 dpi page, aligned to `pdftotext-layout.txt`. Other authors' rows were not entered. CaO·Al2O3 has no This-work formation row. Two method-V rows at 1833–2033 K for 3CaO·Al2O3 are both printed: 14.7 ± 0.9 kJ/mol in the left column and 23.0 ± 1.0 kJ/mol in the right column, the latter still above the 3CaO·Al2O3 header and before the CaO·2Al2O3 header. ΔfS is printed beside each ΔfH and has no quantity token, so it is text on the ΔfH row (`delta_fS_as_printed`) and is not stored as `S`.

Table 3 (`p004-t03`) likewise uses a spanning compound header. MinerU classified the first 3CaO·Al2O3 block as header rows, so those two This-study cells cannot be selected by a body-row list. They were read from the 600 dpi page: 21.3 ± 2.4 (superscript a, equation 7) and 17.3 ± 2.8 (superscript b, equation 8), both at 1808 ± 5 K, Refs. This study and a ditto. The other This-study pairs were read from the same page. Other-author rows were not entered. Tfus is cited from [2–4], not measured here.

One cell was hand-corrected. For 5CaO·3Al2O3, equation (8), MinerU stored `25.2\pm 3.b^b` and pdftotext stored `25.2 ± 3.bb`. A 600 dpi crop of PDF page 4, y 2260–2380 and x 1450–1760, shows a closed baseline 0 and a superscript b above its upper right. The stored value is 25.2 ± 3.0, superscript b.

## Standard state

Table 1 is the activity-derived dataset. The paper's equation, published p. 23, is Δμ_i,T = RT ln a_i,T = ΔH_i − TΔS_i. The paper does not state whether the activity is relative to the pure solid oxide or the pure liquid oxide, and it does not say the reference was measured over the pure oxide in the cell at the experiment temperature. `standard_state` says the paper does not state a reference state. The 2026-10-20 pure-oxide-at-T inference was not used. Migration lifts a reference state only for activity quantities, so these partial-enthalpy and partial-entropy rows stay without a Raoultian endmember.

Phase `l` on Table 1 is the measurement phase: the caption says melts. It is not the activity reference.

Table 2 phase is set only when the printed interval lies entirely on one side of that compound's Table 3 fusion temperature, using the published p. 26 sentence that the solid has negative Gibbs energy and enthalpy and the liquid has ΔH > 0. Intervals that include the fusion temperature have no phase. Table 3 phase text is `crystal to melt`.

## Figures

Fig. 1 is the Table 1 fit drawn as lines, with endpoint dots at the seven tabulated compositions. It was not digitized. Fig. 2 is the integral of those partials (equations 3 and 4), the Duhem–Margules integrals (equations 1 and 2), and the solid-state analytical fits (equations 5 and 6, ΔfH_T = −35420 x_CaO x_Al2O3 and ΔfS_T = 4570 x_CaO x_Al2O3, errors not exceeding 20%). Those curves were not digitized. Equations (5) and (6) are one context row, not a composition grid.

## Tool friction

Emit cannot carry a spanning compound header, cannot select the Table 3 header-row cells, and cannot place a ± cell on `partial_molar_enthalpy_kJ_mol`. The tool was not changed.
