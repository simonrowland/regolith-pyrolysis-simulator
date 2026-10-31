# Witthohn et al. (2000) extraction reconciliation audit

Compared green `f69c0da13` with the reviewed wave at `38274ad6d`, its two
review records (`ror-nawitthohn00-na-k.md` and `ror-nawitthohn00-na-k-r2.md`),
green's `3890e28c1` change, and the rendered PDF pages 15–18. The final wave
review is the authority for its changes; the earlier FIX-FIRST record is kept
below where it explains a corrected locator or quantity token.

## Disagreements and resolutions

| Field | Green | Wave or review record | Resolution |
|---|---|---|---|
| Fig. 4 page | Page 17 | The initial `7badb18ff` draft said page 18; final `38274ad6d` and its review say page 17. | Keep page 17 in both the retained figure context and the new refusal observation. The page-17 render contains Fig. 4; page 18 contains Figs. 5–6. |
| Figure quantity | Green figure contexts use `figure_record`. | The final wave uses `partial_pressure` for Na/K and `activity` in `values.quantity` for Na2O/K2O; its extract `type` for oxide rows is `activity_coefficient`. | Keep green's figure records and add the wave's separate typed refusal observations. `partial_pressure` migrates to `p_partial`; `activity` migrates to `activity`. No numeric plot values are added. |
| Figure evidence | Green context records use `figure_only`. | Wave pressure rows use `measured_reduced`; its activity rows use `quoted_attributed`. | Preserve both layers. `figure_only` says the plot was not digitised; the new observation class records the source of the plotted series. Every new value remains unavailable and rejected. |
| Table 1 evidence | Green's bulk-table context said `measured_direct`. | Wave's seven sample contexts omitted a method class. | Use `measured_tabulated` on the bulk-table and sample contexts, following Kim et al. (2018), Table 5: a measured whole-sample composition retained as table context, not a controlled equilibrium phase. |
| Table 1 ash cells | Green's aggregate transcription omitted the printed `ad` prefix. | Wave sample contexts store `ad 9.6` through `ad 8.4`. | The page-15 render shows `ad` before A–E. Preserve it in the aggregate transcription and sample contexts. |
| Table 1 D / CO2 | Green preserves the dash and footnote as `– (3; not analyzed)`. | Wave's D context says `not analyzed`. | These agree semantically. The render shows `3)-`; footnote 3 says “not analyzed.” Keep the raw table token and the descriptive gap in the structured D context. |
| Oxide phase | Green's figure context says the plotted mixture phase is unspecified. | The final reviewed wave uses the closed phase token `l` on activity observations. | Keep `l` on the typed activity observations as reviewed. It is the observation phase; Green's figure context and pure-oxide standard-state inference remain separate and unchanged. |
| KEMS locator | Green locates the calibration relation on page 5. | The wave method context locates the `Experimental` section heading on page 4; the equation is on page 5. | Keep green's page-5 locator for the relation and record page 4 as the section-heading locator. Do not move the equation to page 4. |
| Fig. 5 temperature | Green's figure context records 1343 K. | The wave refusal row had no temperature field. | Add the printed `T_K: 1343` coordinate to the Fig. 5 row. For figure-level rows that span temperatures, carry an explicit unknown `temperature_K` condition with the figure locator instead of selecting a plotted point. |

All other Table 1 numeric cells agree with the page-15 render and the final
wave review. Green's Na2O standard-state phase inference from commit
`3890e28c1` is retained: those pure-oxide reference phases are distinct from
the activity-bearing slag phase and do not become plotted numeric values.

## Added and retained rows

All twelve green context IDs remain. Seven wave sample contexts are added under
Na, with the printed sample group, sample form, temperature, ash content, and
composition cells. The wave's separate KEMS and pure-component reference
method contexts duplicate facts already present in green's KEMS and figure
contexts; they are merged there instead of creating duplicate context rows.
The page-4 section-heading locator is added to green's existing KEMS context.

The seven added observations are figure-level refusal records. Their condition
states retain the lack of one point temperature or composition and carry the
figure locator. Fig. 5 has the single printed temperature 1343 K; the other six
rows span a temperature axis or multiple temperature series. No sample
composition, total pressure, or fO2 is inferred for a multi-series figure.

| Observation | Page / figure | Migrated quantity | Evidence | Temperature condition | Composition condition |
|---|---|---|---|---|---|
| `witthohn_2000_na_g_fig2_figure_only` | 16 / 2 | `p_partial` | `measured_reduced` | Unknown; plotted temperature varies | Unknown; samples A–D |
| `witthohn_2000_k_g_fig2_figure_only` | 16 / 2 | `p_partial` | `measured_reduced` | Unknown; plotted temperature varies | Unknown; samples A–D |
| `witthohn_2000_na_g_fig3_figure_only` | 17 / 3 | `p_partial` | `measured_reduced` | Unknown; plotted temperature varies | Unknown; samples E–G |
| `witthohn_2000_k_g_fig3_figure_only` | 17 / 3 | `p_partial` | `measured_reduced` | Unknown; plotted temperature varies | Unknown; samples E–G |
| `witthohn_2000_na2o_fig4_figure_only` | 17 / 4 | `activity` | `quoted_attributed` | Unknown; six temperatures | Unknown; X(Na2O) varies |
| `witthohn_2000_na2o_fig5_figure_only` | 18 / 5 | `activity` | `quoted_attributed` | 1343 K | Unknown; CaO and Na2O:SiO2 vary |
| `witthohn_2000_k2o_fig6_model_figure_only` | 18 / 6 | `activity` | `quoted_attributed` | Unknown; five temperatures | Unknown; SiO2 and K2O fractions vary |

The isolated candidate migration adds seven unavailable/rejected observations
and seven sample contexts to green's twelve contexts (19 total), with zero
hard issues. No battery-store regeneration is committed. Expected store delta
versus green: +7 rejected observations, +7 context rows, +1 experiment, no
additional work or benches, and one updated bulk-table context payload.
