# N13 NEW EXTRACT — fallout melt glass (BACKLOG 4)

**Branch:** `empirical/n13-fallout-glass-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `e3315d37b8f006bf0e237db337b70085aba0761a`
**Worktree:** `/workspace/repos/wt/slot-n13`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/` (private; not committed)

## Identify first

| Corpus dir | PDF p1 identity | Sidecar citation | Match? |
| --- | --- | --- | --- |
| `eppich-2014-fallout-melt-glass` | G. R. Eppich et al., *Constraints on fallout melt glass formation from a near-surface nuclear test*, LLNL-JRNL-650394 / JRNC (OSTI author manuscript, 39 pp) | Eppich et al. (2014) JRNC 302, 593-609; DOI 10.1007/s10967-014-3293-9 | Yes — title/authors/DOI; SHA-256 match |
| `bonamici-2017-glassy-fallout-debris` | Bonamici, C. E. et al., *A geochemical approach to constraining the formation of glassy fallout debris from nuclear tests*, LA-UR-15-24262 / CMP (LANL accepted manuscript, 50 pp) | Bonamici et al. (2017) CMP 172, 2; DOI 10.1007/s00410-016-1320-2 | Yes — title/authors/DOI; SHA-256 match |
| `ross-1948-optical-properties-alamogordo-glass` | Clarence S. Ross, *Optical properties of glass from Alamogordo, New Mexico*, Am. Mineral. 33, 360-362 (3 pp) | Ross, C. S. (1948) Am. Mineral. 33(5-6), 360-362; doi null | Yes — title/author/pages; SHA-256 match |

## STEP 0

| Paper | author/`rg` | DOI | PDF ↔ sidecar | Prior extract? |
| --- | --- | --- | --- | --- |
| `eppich-2014-fallout-melt-glass` | No Eppich / this title extract | 10.1007/s10967-014-3293-9 | SHA-256 match | No (Wimpenny 2019 cites fallout glass as comparison samples — different paper) |
| `bonamici-2017-glassy-fallout-debris` | No Bonamici / trinitite-glass extract | 10.1007/s00410-016-1320-2 | SHA-256 match | No |
| `ross-1948-optical-properties-alamogordo-glass` | No Ross 1948 / Alamogordo optical extract | none | SHA-256 match | No |

All three STEP 0 PASS → extract.

## Validate

```
OK: 3 extract file(s) valid   # --check-fidelity-match
```

0 new errors. Validator: `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <files> --check-fidelity-match`.

## Per-paper status

### 1. `eppich-2014-fallout-melt-glass` — LANDED (scored + context)

**Extract:** `data/literature/extracts/eppich-2014-fallout-melt-glass.yaml`

**Scored:**
- `composition_series` — Table 1 quadrupole ICP-MS major oxides for aerodynamic glasses **F-7…F-28** (22 measured rows; F-1…F-6 mass/shape only, oxides NM). Oxides: Na₂O, MgO, Al₂O₃, K₂O, CaO, FeO with expanded uncertainties (k=2). Spot pins: F-7 Na₂O **3.52±0.11** wt%; F-28 FeO **3.00±0.10** wt%.
- `composition_series` (DERIVED) — Table 2 glass/sediment major-oxide ratios for the same 22 samples (author-calculated from Table 1 ÷ six-sediment average); F-7 Na₂O ratio **0.98**.

**Context:**
- Six nominally uncontaminated sediment averages (prose): Na₂O ~3.6, MgO ~0.3, Al₂O₃ ~10.4, K₂O ~4.5, CaO ~1.5, FeO ~2.2 wt%; SiO₂ by difference ~72–75 wt% (semi-quantitative XRF confirmation).
- Author Na-volatilisation interpretation: if Na depletion is volatilisation, melts held above Na₂O boiling point **1950 °C** for significant duration (stated assumptions retained; not a measured run T).

**Not extracted:** SiO₂ ICP-MS (not measured); actinide Tables 3–5 / model ages (out of vapour-rail scope this lane); figure-only curves. DERIVED stamp only on Table 2 ratios.

### 2. `bonamici-2017-glassy-fallout-debris` — LANDED (context-only)

**Extract:** `data/literature/extracts/bonamici-2017-glassy-fallout-debris.yaml`

**Scored observations:** none.

**Context:**
- Table 1 **estimated** Trinity sediment bulk compositions from mineral modes (High/Low Quartz, High K-/Na-feldspar, High Calcite, #7 aggregate + min/max/average element wt%). Average Si **29.0**, K **4.1**, Na **2.7**, Al **7.1**, Ca **5.9**, Mg **0.7**, Fe **1.6**, O **47.5**, C **1.3** wt%. Marked `author_estimate` / not a direct bulk assay.
- Glass lineages in aerodynamic beads: silica (lechatelierite), alkali, CaMgFe; EPMA major-oxide **tables absent from main PDF** (figures / Supplemental Materials Table 1) → typed absence, not digitised.
- Model temperature bounds: condensation **1800–2200 K** (upper ~2200 K); Tg alkali/CaMgFe **>900 K**; Giordano et al. (2008) viscosity modelling 600–1800 K with stated calibration limit.

**Not extracted:** digitised EPMA composition clouds; viscosity curves; supplemental tables not present in this PDF ferry.

### 3. `ross-1948-optical-properties-alamogordo-glass` — LANDED (context-only)

**Extract:** `data/literature/extracts/ross-1948-optical-properties-alamogordo-glass.yaml`

**Scored observations:** none (optical `n` is not a vapour-rail scored type).

**Context:**
- Glass layer **1–2 cm**, pale bottle green, extremely vesicular; parent arkosic sand (quartz, microcline, plagioclase, minor calcite/hornblende/augite).
- Two glasses: feldspar–clay glass **n = 1.51 to 1.53** (to 1.54; ferromagnesian areas to 1.55); silica glass **n close to 1.46**; sharp boundary / little diffusion; strong viscosity contrast.
- Fusion-T prose: cristobalite fusion **1713 °C**; quartz direct fusion thought **slightly below 1470 °C**; Alamogordo explosion much hotter and sudden so quartz fused directly to silica glass. Copper pigment in oxblood-red areas (spectrographic; likely wire).

**Not extracted:** Fig. 1 photomicrograph grey levels (figure-only).

## Refusals

| Paper | Reason |
| --- | --- |
| (none) | — |

## Commit

```
e3315d37b extracts: N13 fallout melt glass (Eppich 2014, Bonamici 2017, Ross 1948)
```

Files added (3). Private PDFs not committed. Branch ahead of `origin/work-v064-green` by 1 (not pushed).

## Optional follow-ups (not done this lane)

- Pull Bonamici Supplemental Materials Table 1 (EPMA glass oxides) if/when obtained → scored `composition_series`.
- migrate + readiness for the three new `source_id`s once I1 is green.
- Y-audit seat against the same three PDFs (BACKLOG 4 Y-audits for N11–N14).
