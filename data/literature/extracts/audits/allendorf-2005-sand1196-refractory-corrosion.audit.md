# Allendorf et al. (2005), SAND2005-1196 extraction audit

## Scope and counts

The measured extraction contains 20 points: 10 numerical cells from the six printed data rows of Table 5-1; three Faber and Beerkens NaOH ranges; the Walsh-attributed NaOH value and uncertainty; the Monofrax/Vega NaOH concentration and uncertainty; two Wereszczak recession distances; two Faber and Verheijen recession rates; and the later Buckley-attributed NaOH concentration. The two Table 5-1 `n.a.` cells are preserved in the extract's `printed_table_rows` metadata, not replaced with zero or counted as measured points.

Only Table 5-1 is a target table. The other printed tables are inventoried in the mapping file: Tables 2-1 through 2-4 and 3-1 through 3-3 contain model inputs, species lists, or thermodynamic parameters; Appendix Table A-1 contains absorptivity fit parameters. Table 2-1 and Table 3-1's model-input pressure of 1 bar is not an experimental furnace condition and is not assigned to measured rows.

The Allendorf/Vega test is the only included observation with a complete melt composition and explicit test temperature. Its source gives 72.7 wt.% SiO2, 13.92 wt.% Na2O, 0.5 wt.% K2O, 8.6 wt.% CaO, and 4.21 wt.% MgO, a stoichiometric natural-gas/oxygen mixture, total gas flow of 4.25 slpm, 1883 K, and 75 hours. The flow is not a pressure. Total pressure and numerical fO2 are not printed. The complete oxide map is stored under `values.composition_wt_pct` and the located `point_conditions.composition` so the existing migrator binds the printed composition to the identity.

For the remaining points, temperature, numeric fO2, total pressure, and complete melt composition are marked unknown unless the report prints the condition for that same measurement. “Air-fired” and “oxy-fired” are retained as atmosphere labels; no gas ratios or oxygen pressures are inferred. The partial soda content printed for two Faber and Beerkens measurements is retained as context and not promoted to a complete melt composition.

## Cell and prose audit

The companion `.cells.csv` maps each stored number to its absolute PDF page and printed cell or prose locator. `a` means MinerU and pdftotext agree on the digits and sign; the page render confirmed the table's row/column assignment. `b` means a render was required because the machine reads differed or the cell carried a flagged glyph.

Table 5-1 has six printed data rows. Its 10 numeric cells were transcribed. The Fill-end extraction cell and refiner-entrance ELIF cell are `n.a.` for physical-access constraints. MinerU reads the left-side downtank extraction cell as `221`; pdftotext splits it as `22 1`; the page-99 render confirms `221`. All other numeric table cells agree across machine reads, and the page-99 render confirms row and column placement.

The extract's fidelity sample pins the rendered `221` cell by its observation-series path and the printed Table 5-1 row-5 locator.

Page 56's Faber and Beerkens concentration ranges and partial soda values, pages 61-62's recession prose, page 67's full test composition and flow, page 68's `±` concentration and test conditions, and page 82's approximate 125 ppm at 1.0 m were checked against renders. Page 82 confirms the source's approximate sign. The `±` values on pages 66 and 68 were checked in renders; the uncertainty widths are preserved as printed and confidence level is not stated.

The saved table-draft compare output reports 72 locator errors because its caption parser reads printed `Table 5-1` as `5`; the page-99 render confirms the caption is `Table 5-1`, so that exact source locator is retained. Those errors are locator paths for the two table observations and their row/condition records, not digit mismatches; each of the ten numeric cells is separately reconciled in the cell audit. The three compare `no-match` rows are the prose values on pages 66, 68, and 82, which have no MinerU table cell to match.

## Evidence classes and schema gap

Faber and Beerkens, Walsh, Wereszczak, Faber and Verheijen, and the later Buckley measurement are `quoted_attributed`, with each printed attribution retained. Table 5-1's sodium concentration depends on the withdrawn gas volume and collected sodium amount, neither tabulated per sample; its ELIF concentrations depend on the Figure 5-3 calibration, while the underlying signals are not tabulated. Those Table 5-1 values are therefore `quoted_attributed`, not given a fabricated lineage. The Monofrax/Vega value is the report's own extractive-sampling measurement and is `measured_direct`; its printed method does not provide a numeric reduction formula or raw input amounts.

Schema-gap symbols are `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/enums.py::Quantity`. `concentration_series` and `rate_series` are registered observation types, but the closed quantity enum and alias map have no exact concentration-in-ppm or refractory surface-recession distance/rate token. The available `p_partial` token is not a concentration token and cannot be populated without an explicitly printed total pressure. `mass_loss_rate` does not describe a printed length or length/time quantity. All 20 observations therefore preserve the printed number with a descriptive non-token `values.quantity`; isolated migration must report exactly these 20 as quantity-unknown and unavailable. Their phases map to `g` for vapour and `cr` for silica refractory.

## Figures and other excluded material

- Figures 2-3 and 2-5 contain experimental Na2O and K2O activity comparisons; figure-only points are not digitized. No measured activity table was found.
- Figure 4-6 contains measured Na2O corrosion-product profiles, outside the requested vapour concentration and recession quantities. Figure 4-14 compares measured furnace temperature, NaOH concentration, and corrosion-rate profiles; its plotted data are not digitized.
- Figure 1-3's caption describes an approximately 800 ppm, 75-hour, 1610 °C Monofrax exposure; this is a figure-caption exposure description, not a measured concentration or printed corrosion rate. The separate prose-reported 640 ± 65 ppm Vega measurement is extracted.
- Figure 5-2's 130 ppm calibration sample, Figures 5-7 and 5-8's ELIF signals, and Figure 5-10's signal-time traces are calibration or figure-only records, not additional printed field concentration cells. No KOH or atomic K concentration result was found in the measured target tables or reviewed prose.
- The p.56 anecdotal 100–250 ppm oxy-fuel range, p.61 reported possible 350–400 ppm near an unfavorably oriented burner, p.61 anecdotal 25 mm/year field value, p.62 author-selected 2.5 mm/year comparison value, and p.82 general 0–30 mm/year field range lack the attribution or measurement description needed to treat them as measured observations. They are not stored as measured data.
- Numeric model predictions and equilibrium activities are not included as measurements. Model-derived corrosion series and the figures' plotted values remain figure-read candidates.

## Expected generated-store change

No store regeneration was run. The expected migration/store delta is 20 unavailable observations, with 20 quantity-unknown records, zero quantity-unknown phase records, and no model-derived observations. Values remain in printed units and are not converted.
