# Shornikov 1997 CaO–Al2O3 fusion enthalpies — extraction audit

Source: Shornikov, S. I., V. L. Stolyarov, and M. M. Shul'ts (1997), The thermodynamic properties and estimation of the enthalpy of fusion of the components in the CaO–Al2O3 system, Russian Journal of Physical Chemistry, vol. 71, no. 1, pp. 23–27. Translated from Zhurnal Fizicheskoi Khimii, vol. 71, no. 1, pp. 28–32. PDF sha256 5fa6239f43e3ae8749d67aec19450cab9476bc6afa373015ab84660d5ce9ae7b. Five letter pages. pdfinfo has no Title. This is not Shornikov, Stolyarova, and Shul'ts, Russian Journal of Physical Chemistry 71(1):19–22, which is already extracted as shornikov-1997-cao-alumina-vapor.

## Tables

MinerU `p002-t01` was emitted (28 cells, skipped empty or dash = 0). Every emitted cell text matches `pdftotext-layout.txt` and the 600 dpi page. No `needs_render_check` flag. The ± widths on the enthalpy cells were split after emit into a central value and `sigma`. The printed zero cells (H_CaO at x(CaO) = 0.750 and at 0.860) are stored as 0.0. They are not dashes.

The page prints the Table 1 headers as H_CaO, S_CaO, H_Al2O3, and S_Al2O3. The text layer inserts a delta that the 600 dpi header does not show (PDF page 2, crop x 500–4666, y 4792–5417). Column headers in the extract follow the page.

The fourteen partial-entropy cells are context, type `partial_molar_entropy_refused`. `Quantity` has `partial_molar_enthalpy` and `S`, and no partial-molar entropy (`enums.py`). The defining equation is μ_i,T = H_i − T S_i, so these are partial quantities. They are not stored as absolute `S`, and they have no `entropy.value`.

`QUANTITY_SOURCE_FIELDS` (`migrate.py` around line 8239) has no `Quantity.PARTIAL_MOLAR_ENTHALPY` entry. `map_quantity` still accepts the token, because it is a `Quantity` value. `select_declared_source` then finds no source field. These rows also carry `T_range_K`, so the unavailable reason is that no mapped numeric partial_molar_enthalpy was selected and the temperature range is not the observable. The fourteen `partial_molar_enthalpy_kJ_mol` values fail closed and queue. This audit does not claim that migration reads them.

The table prints only x(CaO). x(Al2O3) on each enthalpy row is the binary complement of that printed value. Temperature is the caption range 1833–2033 K (`T_range_K`), not a midpoint.

Table 2 (`p003-t02`) is a two-column page. The compound name is a spanning header, not a column. Reading order is down the left column, then down the right column. The left column finishes 3CaO·Al2O3, 12CaO·7Al2O3, and 5CaO·3Al2O3, and begins CaO·Al2O3 at the bottom (600 dpi page 3, crop x 200–2600, y 4200–6200). The right column has no new compound header until CaO·2Al2O3 (crop x 2500–4950, y 200–2200). The This-work rows in that opening right-column block, −7.1 ± 0.6 kJ/mol at 1600–1877 K and 23.0 ± 1.0 kJ/mol at 1833–2033 K, belong to CaO·Al2O3. The 1600–1877 K bound is the fusion temperature of CaO·Al2O3, not of 3CaO·Al2O3. Other authors' rows were not entered. The printed formation entropy has no quantity token, so it is text on the formation-enthalpy row (`delta_fS_as_printed`).

Table 3 (`p004-t03`) uses a spanning compound header. MinerU classified the first 3CaO·Al2O3 block as header rows, so those two This-study cells were read from the 600 dpi page: 21.3 ± 2.4 (superscript a, equation 7) and 17.3 ± 2.8 (superscript b, equation 8), both at 1808 ± 5 K. The other This-study pairs were read from the same page. Other-author rows were not entered. Tfus is cited from [2–4], not measured here.

The 5CaO·3Al2O3 equation (8) cell does not resolve to 25.2 ± 3.0. The central value 25.2 is printed. At 600 dpi (PDF page 4, x 1450–1760, y 2240–2400) and at 1200 dpi (x 2900–3600, y 4480–4800) the mark after "3." is a baseline letter b, with a second b as the equation superscript. The row above prints a closed 0 in 27.4 ± 3.0. The numeric uncertainty is not stored. MinerU had `25.2\pm 3.b^b` and pdftotext had `25.2 ± 3.bb`.

## Standard state

Table 1 is the activity-derived dataset. Published p. 23 prints μ_i,T = RT ln a_i,T = H_i − T S_i (600 dpi PDF page 1, crop x 667–4667, y 3750–4417). The text layer writes a delta in front of μ, H, and S; the page does not. The paper does not state whether the activity is relative to the pure solid oxide or the pure liquid oxide, and it does not say the reference was measured over the pure oxide in the cell at the experiment temperature. The 2026-10-20 pure-oxide-at-T inference was not used. These rows are partial enthalpies, not activity quantities, so `reference_state_from_extract` is not called. The prose does not contain "raoultian". Phase `l` on Table 1 is the melt in the caption.

Published p. 24 prints equations (5) and (6) as _f H_T = −35420 x_CaO x_Al2O3 and _f S_T = 4570 x_CaO x_Al2O3, errors not exceeding 20%. Published p. 26 prints _f G < 0, H > 0, and S > 0 for the liquid, and says that in the solid state the Gibbs energies and enthalpies are negative. Equations (7) and (8) print H_fus = H_T^liq − H_T^sol and H_fus = T_fus (S_T^liq − S_T^sol).

Table 2 phase is set only when the printed interval lies entirely outside that compound's Table 3 fusion temperature, including its printed uncertainty. CaO·2Al2O3 intervals that reach 2033 K sit inside Tfus = 2035 ± 10 K and have no phase. CaO·Al2O3 intervals that include 1877 ± 5 K have no phase. Table 3 phase text is `crystal to melt`.

## Figures

Fig. 1 is the Table 1 fit drawn as lines. The axis label is −μ_i,T, not a delta. It was not digitized. Fig. 2 is the integral of those partials and the solid-state fits (5) and (6). Those curves were not digitized. Equations (5) and (6) are one context row, not a composition grid.

## Tool friction

Emit cannot carry a spanning compound header and cannot place a ± cell on `partial_molar_enthalpy_kJ_mol`. The tool was not changed. No mapping for partial molar enthalpy was added to migration.
