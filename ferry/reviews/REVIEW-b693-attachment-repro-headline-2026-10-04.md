# Executable probes: headline and figure uncertainty at 9be2c17266eec54bd7b23c1768a73d2192911f05

Read-only probes ran with `PYTHONPATH=$PWD /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`; no source, tests, or generated store writes. All predictions were intentionally injected through the existing production `score_store` / `compile_residual` entry points so these probes isolate scorer/report policy rather than engine physics.

## Headline integration

Used `tests.battery.test_b693_b691_scorer_gates._demaria_shape_figure_rows()` and `_context()`: 30 admitted figure-only observations (20 Na, 10 K), Re bench, PRINTED scalar `±30%`. Ran `score_store(..., engines=(Engine.INTERNAL_ANALYTICAL,), include_diagnostics=True, predict=lambda engine, obs, **kw: _predict(Decimal('1e-5'), obs.identity))`. Built `_ResidualRowsPayloadView`, `_ScorePayloadAccumulator.from_rows`, and called all three headline owners plus `_render_score_report_from_payloads_legacy(..., store_stamp={}, _aggregate=aggregate)`.

Measured vapour outcomes:

| Path | n all numeric | signed median dex | IQR dex | flags/source counts |
|---|---:|---|---|---|
| `headline_records` typed | 30 | -0.73856062735983115 | 0.944626803319374050 | figure_only=30; source kems-022-demaria-1971 numeric=30, certified=0, flagged=30 |
| `_ScorePayloadAccumulator.headline_records` | 30 | -0.73856062735983115 | key absent | flag_class_counts and sources keys absent |
| `headline_payload_records` | no all_numeric tier | no all_numeric tier | no all_numeric tier | no all_numeric tier |

All paths gave certified/measured vapour n=0. Rendered report contained neither `all_numeric` nor `ALL NUMERIC`, neither `IQR` nor `iqr`, and no `kems-022-demaria-1971` source ID. Sections were Live candidate census, No headline rail, Measured tier, Flagged strata, Compilation tier, Refusal census, Admission, Notice backlog, Internal-consistency diagnostics, Pin failures, status_diff.

Production CLI uses the faulty accumulator summary and report renderer at `scripts/battery_score.py:319` and `:330`. Report-only uses `render_score_report_from_payloads` and `write_headline_summary_from_payloads_json` at `:219` and `:235` (the latter calls `headline_payload_records`).

Concrete locations: typed fields exist at `score.py:5669-5681`; streaming `_headline_payload_record` at `:6467-6522` has none; accumulator calls it at `:7789`; legacy payload records emit only measured/compilation at `:7167-7169`; production Markdown explicitly skips every tier other than measured at `:6267-6269`. The independent payload Markdown renderer still prints only `headline_payloads(measured_rows, engines)` at `:8055`.

This is incomplete integration across three preexisting headline paths. The typed helper adds the requested fields but actual CLI output omits them. Add regression assertions to the existing streaming parity fixture and the new headline pin covering the production report and both JSON paths. Existing `test_streamed_scoring_is_byte_identical_to_legacy_fixture` already byte-compares typed and streaming summaries at `tests/battery/test_score.py:4274-4289` and should catch the new mismatch.

Flag counts in the typed helper count unique flag classes per row using `flagged_strata`; a row with multiple classes contributes to each, so their sum can exceed numeric n. A source with zero certified rows is correctly included only in the typed path, as the executable fixture verifies.

## Figure-only reading band: absent and stored mapping

Used the first figure row from the same fixture, value `1e-5`; injected `_predict(Decimal('1.1e-5'), figure.identity)` into production `compile_residual`. Added, in the paired cases, an admitted measured KEMS observation using the exact existing fixture uncertainty from `test_score.py:608`: PRINTED verbatim mapping `{'temperature_quote': 'At 1500 K, the estimated 20 K error yields an error in K pressure of about 40 percent.'}`, source `kems-042-plante-1979`. The measured row had the same existing KEMS experiment/bench, a valid figure fixture identity, and reference pressure 10 Pa. Its scorer-derived global band was `0.1461280356782380259259551533` dex = log10(1.4).

The stored mapping case reproduces the uncertainty representation in committed `data/literature/extracts/kems-022-demaria-1971.yaml:1727`: PRINTED verbatim mapping with `sigma_log10P_dex_per_point: 0.3`, `dex: 0.3`, and note text. This does not imply those raw rejected rows are admitted typed committed observations today.

| Figure uncertainty | Other measured KEMS row in context | status | decision band dex |
|---|---|---|---|
| NONE | no | no_band | null |
| NONE | yes | match | 0.1461280356782380259259551533 |
| PRINTED mapping dex=0.3 | no | no_band | null |
| PRINTED mapping dex=0.3 | yes | match | 0.1461280356782380259259551533 |
| PRINTED mapping dex=0.3 plus typed value=0.3, basis=dex | no | no_band | null |
| PRINTED mapping dex=0.3 plus typed value=0.3, basis=dex | yes | match | 0.1461280356782380259259551533 |

All six numeric residuals were `0.04139268515822508`, flagged `figure_only`, and `score_eligible=False`. Every borrowed band had this exact rule:

> KEMS p_partial measured uncertainty: source-printed pressure envelope (RMS of admitted printed log10 pressure envelopes only); Plante width log10(1.40) from the printed page-280 1500 K sentence, using the upper multiplicative edge and accepting pressure ratios [1/1.40, 1.40] (-28.6%..+40%), not a symmetric +/-40% relative band; NOTICE: the single 1500 K figure is applied across the tabulated T range

Root cause: `compile_residual` initializes `cell_band` from the existing global KEMS band at `score.py:4454-4465`, then the new figure branch uses `_printed_uncertainty_band(...) or cell_band` at `:4477-4483`. `_printed_uncertainty_band` rejects every non-string verbatim at `:1897-1907` and ignores typed dex width; thus absent or real stored mapping uncertainty retains unrelated measured source uncertainty. Required result for NONE is always no_band; required result for stored dex=0.3 is that stored 0.3 band, with a figure_only label.

## Series and pooling

The new per-rail headline deliberately aggregates numeric residuals by `(rail, engine)` (`score.py:5778`, `:7433`). The fixture therefore combines 20 Na and 10 K residuals in its required global rail headline. That aggregation alone is not a defect: the brief explicitly asks for all-numeric n/median/IQR per rail.

No new raw-series folding or residual-distribution band calculation was added by the dual-headline change. Each fixture observation retained its own residual. The concrete forbidden borrowing is the band fallback above: a figure row without a reading uncertainty receives the global measured KEMS band, which can pool other sources. The raw De Maria fixture is insufficient to certify real per-series behavior: it contains one artificial source and no explicit sample/series split, while the raw extract has two Na sample series.

## Figure-only plus catalogue composition: valid reading band discarded

An additional production `compile_residual` probe used the existing admitted first synthetic figure row with valid PRINTED scalar `±30%`, reference pressure `1e-5`, and injected prediction `1.1e-5`. The stored reading converts to `0.1549019599857431692877837414` dex with rule `source-printed per-cell uncertainty`; numeric residual is `0.04139268515822508` in every case.

Applied the catalogue-composition representation from existing `tests/battery/test_score.py:2220`: replaced the existing composition's basis with `sample_catalog_proxy` and set `proxy_flag='composition_from_sample_catalog'`, `proxy_source='catalogue-10017'`, `analysis_selection_rule='first complete whole-sample analysis'`; component amounts stayed identical. Separately tested a stored `COMPOSITION_FROM_SAMPLE_CATALOG` notice carrying the same reason/source, without proxy fields. Predictions retained each reference's identity.

| Reference representation | Parsed printed band before compile | Band after compile | status | flag classes |
|---|---|---|---|---|
| figure only | 0.1549019599857431692877837414 | same | match | figure_only |
| figure + stored catalogue notice only | same | same | match | catalogue-composition, figure_only |
| figure + catalogue proxy composition | same | null | no_band | catalogue-composition, figure_only |
| figure + both representations | same | null | no_band | catalogue-composition, figure_only |

Every row remained `score_eligible=False`, as required for figure evidence. This is a reading-band defect, not a request to admit catalogue composition to the certified subset. `_catalogue_composition_notice` at `score.py:765-788` creates the proxy flag; `_flagged_stratum_notices` at `:954-964` includes it; the newly amended `has_no_band_flag` at `:4515-4534` only exempts FIGURE_ONLY, REACTIVE_CELL_NOT_MODELLED and CELL_MATERIAL_INFERRED, so its catalogue notice deletes the valid stored reading band. In contrast, a stored reference notice is absent from that `flagged_notices` collection, so the same semantic flag does not delete the band. For the newly requested figure-only behavior, preserving the stored reading band conflicts with this retained blanket catalogue-band deletion. This is directly in scope of b-693(a); preserving existing certified exclusion does not require stripping figure reading uncertainty.

Recommendation: FIX-FIRST — 9be2c17266eec54bd7b23c1768a73d2192911f05
