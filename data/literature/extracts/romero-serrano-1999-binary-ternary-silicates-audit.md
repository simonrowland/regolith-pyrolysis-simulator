# Romero-Serrano & Pelton (1999) extraction audit

Source: `romero-serrano-1999-binary-ternary-silicates`, born-digital PDF, physical pages 1–10. The PDF sidecar prints no DOI. Duplicate checks across the local literature data found no earlier extract; the cited paper is the only match for this title and author-year.

## Printed-table disposition

| Table | Printed content | Candidate tokens | Disposition |
|---|---|---|---|
| 1, physical PDF page 2 (folio 400) | 20 pure phases with H/S baselines relative to the elements at 298.15 K and heat-capacity polynomial coefficients | `delta_fH`, `S`, `cp` | `context-model`: retained as untyped model-input context. No per-row attribution is printed, and the primary thermo compilations already have a canonical store location (d-068), so no duplicate numeric rows are emitted. |

The MinerU draft is `tables/tables/romero-serrano-1999-binary-ternary-silicates/index.yaml`, table `p002-t01`, 20 body rows. It was used to confirm table inventory and row count. No cell mapping or table emission was made because the controller ruled the table values must remain untyped context.

## Quoted measurements

Four prose-reported invariant temperatures are typed as `quoted_attributed` transition-temperature observations under the `SiO2` filing container. Each locator uses the physical PDF page; the page text was independently checked with `pdftotext -f N -l N -layout`.

| Observation | Temperature as printed | Printed composition retained as partial | Attribution | Physical page / folio |
|---|---:|---|---|---|
| invariant point a, SiO2–MnO–MgO | 1480 °C | 55 wt% SiO2; 25 wt% MnO; MgO omitted | Glasser and Osborn [19] | 6 / 404 |
| invariant point a, SiO2–FeO–MnO | 1185 °C | 39.9 wt% SiO2; 23.9 wt% MnO; FeO omitted | Riboud and Muan [20] | 7 / 405 |
| invariant point a, SiO2–FeO–MgO | 1470 °C | 56.4 wt% SiO2; 20.0 wt% FeO; MgO omitted | Osborn and Muan [22] | 8 / 406 |
| invariant point b, SiO2–FeO–MgO | 1305 °C | 45.3 wt% SiO2; 45.7 wt% FeO; MgO omitted | Osborn and Muan [22] | 8 / 406 |

The incomplete compositions remain unknown conditions with reasons; no balance oxide was reconstructed. The Glasser and Osborn invariant point also appears as an experimental marker in Fig. 8 (physical PDF page 7); this is one typed observation and the second locator is noted on its record.

The three cited primary references and DOI field (blank where none is printed) are in `/private/tmp/claude-501/main-review/fam19-1019/primaries.tsv` for controller retrieval.

## Figure inventory

All 13 figures are recorded under the `SiO2` context with no numeric points. Pages 3–9 were inspected at 600 dpi to identify captions, systems, axes, plotted series, and marker counts. Figure records use `transition_temperature` for phase boundaries and `activity` for FeO activity where a token exists. The interaction-energy schematic in Fig. 6 and Gibbs free energy of mixing in Fig. 13 have no suitable token; both remain unscored context, and Fig. 13 carries the explicit schema gap rather than using `delta_fG`.

The measured/quoted diagram series are attributed to the references printed in each caption: Figs. 1–5 [5–8], [6], [10–12], [14], [15], and [17]; Fig. 8 [19]; Fig. 10 [21]; Fig. 12 [23]. Other plotted curves and diagrams are present-work model output. Fig. 10 states a liquid standard state; that wording is kept on every series. The plotted markers in Fig. 4 appear circular in the 600 dpi render although the caption symbol is square; both statements are preserved in its record.

## Locator and migration evidence

- Physical PDF page locators are one-based, with `pdf_page_index = page - 1`; published folios are separate.
- `pdftotext -f 6 -l 6`, `-f 7 -l 7`, and `-f 8 -l 8` confirm the four cited attributions on their cited pages. On physical page 7 the text layer renders 1185 as `I 185`; the 600 dpi page render confirms the printed value is 1185 °C. On page 2 the table label is read as `Table l.` in the text layer; the render confirms Table 1.
- Physical pages 3–9 were rendered at 600 dpi for figure metadata. Physical page 10 was rendered at 600 dpi to transcribe references [19], [20], and [22].
- The focused test checks all locator index/page pairs, source text on each citation page, four cited values, incomplete composition reasons, 13 deferred figure records, and isolated migration of the four typed observations.
