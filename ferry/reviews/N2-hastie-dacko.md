# N2 — hastie-1979 + ta-dacko-conradt (BACKLOG 3) — 2026-09-22

Branch: `empirical/n2-hastie-dacko-2026-09-22` (from `origin/work-v064-green` @ `2e9e17c3d`)

Worktree: `/workspace/repos/wt/slot-03` (pre-claimed N2 slot; avoided slot-02 / w3b-green)

Tip: `2f92f46b9` — two commits, one per source; `validate_literature_extracts` OK on both; no PDFs committed.

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `f17bb62a2` | hastie-1979-characterization-of-high-temperature | Bonnell & Hastie 1979 TMS chapter Tables 1–4 |
| `2f92f46b9` | ta-dacko-conradt-low-p-transpiration | Dacko–Wilsmann–Conradt 2004 low-P transpiration |

## STEP 0

### hastie-1979-characterization-of-high-temperature

- **PDF↔sidecar:** PDF is NBS Special Publication 561 Vol. 1 (848 pp, scanned). Sidecar cites Bonnell & Hastie (1979) "Transpiration Mass Spectrometry of High Temperature Vapors", p. 357. Confirmed: chapter title page at PDF p. 381 = printed p. 357.
- **DOI:** `10.6028/NBS.SP.561v1` — **volume** DOI already used by `kems-042-plante-1979` and `kems-184-behrens-1979` for **different chapters**. Not a duplicate of the Bonnell & Hastie TMS chapter. `doi_scope` notes this.
- **Author grep:** No existing extract for Bonnell & Hastie 1979 TMS. `kems-020-hastie-1981-nbsir` is a different report (NBSIR 81-2279) that *cites* this chapter as `original_source`. Proceeded.
- **Model:** `kems-020-hastie-1981-nbsir` / `kems-042-plante-1979`.

### ta-dacko-conradt-low-p-transpiration

- **PDF↔sidecar:** 8-page Glastech. Ber. Glass Sci. Technol. 77 C (2004) 205–212. Authors Dacko / Wilsmann / Conradt; title matches low-P borosilicate transpiration. Sidecar has no DOI (held / corpus-recovery).
- **DOI:** none printed; Crossref bibliographic query returned no match. `doi: null` + `doi_status` recorded.
- **Author grep:** No Dacko/Wilsmann extract; `SOURCE_STATUS` lists this `source_id` as inbox / no extract. Proceeded.
- **Model:** `kems-046-van-limpt-2007` (transpiration / glass melt).

## Per-source

### hastie-1979-characterization-of-high-temperature (Bonnell & Hastie chapter)

- **Scope:** Printed Tables 1–4 only. Figs 8–12 vapour-pressure / dimerization plots → `figure_only` ledger (not digitized).
- **Table 1 (p. 376):** Matheson calibration-gas mixture fractions + 30 eV ionization cross sections (N2 host + He/Ne/Ar/Kr/Xe).
- **Table 2 (p. 381):** Typical NaCl mass-spectral ion intensities (Knudsen + transpiration rows); N2 carrier footnotes retained.
- **Table 3 (p. 385):** NaCl absolute pressure calibration — measured mass-loss inputs + **DERIVED** `P_NaCl_total = 2.96(±0.6)×10^-2 atm`, monomer `2.28(±0.4)×10^-2 atm`.
- **Table 4 (p. 394):** Dimerization fit parameters (Knudsen / TMS / JANAF / combined) — **DERIVED** second-law fits; JANAF row tagged compilation.

### ta-dacko-conradt-low-p-transpiration

- **Sample:** mol% 79 SiO2 / 7 Na2O / 10 B2O3 / 3 Al2O3 / 1 minor constituents (as printed).
- **Bench:** Low-P transpiration; Pt shuttle 100×10×10 mm³; min P 200 hPa; 60% RH (room-T reference).
- **Table 1 (p. 207):** Full 18×2-run Δq (mg/cm²) grid — **measured**.
- **Eqs (2)–(4):** Author log-r fits on quasi-stationary rates — **DERIVED** (EA 206.2 / 201.6 / 172.4 kJ/mol as printed).
- **Table 2:** a(Na2O)/a(B2O3) from Conradt model [3] — **model**; r_calc — **model**; r_exp — **DERIVED** from weight-loss campaign.
- **Table 4:** log Ki coefficients — **model** (context).
- **Table 5 (p. 211):** Condensate chemical analysis (ΔmB, ΔmNa, B/Na) — **measured**. Abstract prose "Na2O/B2O3 1.25–1.4" vs printed column header `B/Na` noted; values transcribed as printed.
- **Fig. 2:** figure_only (not digitized).

## Validation / hygiene

- `uv run python tools/validate_literature_extracts.py` on both files → `OK: 2 extract file(s) valid`.
- Pathspec-only adds; `ferry/pdfs-n/*.pdf` gitignored; `ferry/` left untracked.
- Printed values only; derived / model / figure_only stamped; absence never zeroed.

## Push

Pushed: `origin/empirical/n2-hastie-dacko-2026-09-22` @ `2f92f46b9` (matches FETCH_HEAD).
