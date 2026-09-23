# N4b FILL — Y4 P1 omissions on N4 CAI-liquid evaporation

**Branch:** `empirical/n4b-y4-omissions-2026-09-22`
**Base tip:** `700e1aeef61eaf4962e0238da00a665eb14a0313` (`empirical/n4-cai-liquid-evaporation-2026-09-22`)
**Tip SHA:** `53e1e9c3577aab8f36fcde3cf04b727d54374dd4`
**Worktree:** `/workspace/repos/wt/slot-05`
**Y4 report:** `/workspace/ferry-inbox/reviews/Y4-n4-cai-liquid-evap.md`
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/`
**Date:** 2026-09-22 (America/Toronto / EDT)

## Scope

Fill **only** the two Y4 P1 omissions. No other papers touched. No curve digitisation. No invented numbers.

| Y4 item | Action |
| --- | --- |
| P1-1 ta-mendybaev-2002 multi-T rates + Ea | **LANDED** |
| P1-2 richter-2008 Fig.2 annotation α | **LANDED** (`figure_only`) |
| ta-mendybaev-2020 | untouched |
| Y4 P3 hygiene / sidecar citation | not in FILL scope |

## Validate

```
OK: 3 extract file(s) valid
  ta-mendybaev-2002-lpsc.yaml
  ta-mendybaev-2020-lpsc.yaml
  richter-2008-cai-like-liquids-lpsc-abstract.yaml
```

PDF ↔ sidecar sha256: both filled sources YES
(`7f9ef6f0c2…` mendybaev-2002; `a6f675576c…` richter-2008).

## 1. ta-mendybaev-2002-lpsc — multi-T rates + Ea

**Proof (PDF p2 Results, pdftotext + 200 dpi raster):**

> The weight loss rates of 1.0 mm samples evaporated at 1800ºC, 1700ºC and 1600ºC are ~1.1x10⁻⁵, ~2x10⁻⁶ and ~3x10⁻⁷ g/mm²/min, respectively, which corresponds to an apparent activation energy of evaporation Ea =580 kJ/mole …

| field | landed | locator |
| --- | --- | --- |
| rate @ 1700 °C, 1.0 mm | `~2x10^-6` g/mm²/min (`approximate: true`) | p2 Results |
| rate @ 1600 °C, 1.0 mm | `~3x10^-7` g/mm²/min (`approximate: true`) | p2 Results |
| Ea | `580` kJ/mole (`method_class: model_derived`) | p2 Results |
| rate triad (on Ea row) | ~1.1e-5 / ~2e-6 / ~3e-7 | same sentence |
| forsterite comparators | 628±16 [4]; 584±28 [3] as `quoted_attributed` | same sentence |

**Not re-scored:** the triad’s 1800 °C `~1.1x10^-5` — already present as the more precise `(1.1±0.2)x10^-5` row from p1.

**Also fixed:** multi-T experiment locator note that wrongly said “numeric rates printed only for 1800 C” (Y4 flag on N4 write-up).

New observation_ids:
- `mendybaev_2002_weight_loss_rate_1700C_1mm_loops`
- `mendybaev_2002_weight_loss_rate_1600C_1mm_loops`
- `mendybaev_2002_apparent_Ea_1mm_multi_T`

## 2. richter-2008-cai-like-liquids-lpsc-abstract — Fig.2 α

**Proof (PDF p2 Fig.2 raster @ 200 dpi):** readable text boxes (no curve digitisation):

- **α = 0.98797±0.00022** (fit to this-work solid + [2] open)
- **α = 0.97980** (ideal comparator, same panel)

| field | landed | method |
| --- | --- | --- |
| Mg kinetic α | `0.98797±0.00022` (`alpha: 0.98797`, unc `0.00022`) | `method_class: figure_only` |
| ideal comparator | `0.97980` on same row as printed context | not scored as measured |

New observation_id: `richter_2008_fig2_mg_isotope_alpha_annotation` under `species.Mg`.
Curve points remain `not_digitized`. Body-prose “approximately the same / slightly above” stance unchanged.

## Commit / push

```
53e1e9c3577aab8f36fcde3cf04b727d54374dd4
extracts: N4b fill Y4 P1 omissions (2002 multi-T/Ea, Richter Fig.2 alpha)
```

Files modified (2):
- `data/literature/extracts/ta-mendybaev-2002-lpsc.yaml`
- `data/literature/extracts/richter-2008-cai-like-liquids-lpsc-abstract.yaml`

Pushed: yes (`origin/empirical/n4b-y4-omissions-2026-09-22` @ `53e1e9c35`).

## Report line

**N4b FILL DONE · tip `53e1e9c35` · landed: 2002 multi-T (~2e-6/@1700, ~3e-7/@1600) + Ea=580; Richter Fig.2 α=0.98797±0.00022 (figure_only) · pushed**
