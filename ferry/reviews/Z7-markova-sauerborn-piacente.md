# Z7 — FIDELITY AUDIT markova-1984 / sauerborn-2005 / piacente-1975 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z7-markova-sauerborn-piacente-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Fix tip:** `5d0baf6c5` — `fix(kems-026): correct Table 1 sample V Al2O3 19.29 → 18.29`  
**Worktree:** `/workspace/repos/wt/slot-z7`  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/` (**never committed**)  
**Date:** 2026-09-22 (America/Toronto, EDT)  
**Auditor:** Z7 fidelity seat (BACKLOG 4)

| extract | PDF sha256 (prefix) | pages | nested obs | verdict |
|---|---|---:|---:|---|
| `kems-026-markova-1984` | `4fbf0e84…fe31` | 2 | 15 (+144 Table 1–2 cells) | **P0=1 fixed** |
| `kems-035-sauerborn-2005` | `76484556…1a027` | 151 | 60 (incl. deep2 twins) | **P0=0** |
| `kems-093-piacente-1975` | `5cd60861…8797` | 11 | 34 | **P0=0** |

**Validator:** `tools/validate_literature_extracts.py` → `OK: 3 extract file(s) valid` (post-fix).

## Overall verdict

- **Markova:** one P0 (Table 1 sample V `Al2O3` 19.29→**18.29**); tip advanced by fix commit; **no-push** (await V-style verify).  
- **Sauerborn / Piacente:** clean on sampled cells (≥20 each); tip unchanged for those files.  
- Coverage notes below are non-P0.

---

## 1. kems-026-markova-1984

**PDF identity:** Markova et al. (1984), LPSC XV 509–510 (LPI/ADS 2009 Paper Capture scan, 2 letter pp).  
**Extract:** `data/literature/extracts/kems-026-markova-1984.yaml`

### Inventory

| Layer | Count |
|---|---|
| Nested observations | 15 (method, Table 1, Table 2×2, text windows, dissipation models, figure-only) |
| Table 1 stored oxide cells | 60 (+4 intentional omissions) |
| Table 2 residual-melt points | 12 VI + 12 V (= 144 oxide/T/M cells) |
| `fidelity_samples` | 4 |
| Prior glyph corrections (kept) | FeO VI T=0 4.04→8.04; CaO VI 1726 4.79→8.79; M% VI 1960 36.05→86.05; T V 1540→1640; MgO V 1719 3.11→8.11; T V 1309→1809 |

### Method

1. `pdftotext -layout` (noisy OCR — not used as data).  
2. 200/400 dpi `pdftoppm` of both pages; Table 1–2 cell zooms + sum / Table-2-recast constraints.  
3. Spot method numbers (ΔT=70 °C, 15 min), dissipation prose (Na 14 / K 3 / SiO 2 %; recent-Moon Na ~2 %), ANT window 1600–1700 °C / 15–35 % mass loss.  
4. Re-checked prior `corrections:` against monotonic T / M% and Table 1→Table 2 five-oxide recast.

### Sampled checks (≥20)

**Table 1 (sample I–VII oxides) — P0 found & fixed**

| check | result |
|---|---|
| SiO2 I–VII | 42.94 / 45.40 / 42.36 / 42.43 / 50.01 / 41.94 / 41.10 — OK |
| Sample VI full oxide set vs sum 95.9 | OK |
| Sample V oxide sum with landed Al2O3 | **was 101.09 with 19.29; 100.09 with 18.29** |
| Sample V → Table 2 T=0 five-oxide recast | **only Al2O3=18.29 reproduces 52.32 / 19.13 / 9.37 / 6.25 / 12.93** |
| Intentional omissions (TiO2 I, Al2O3 I/II, FeO IV) | OK (damaged glyphs; not guessed) |

**P0 fix:** `samples[V].oxides_wt_pct.Al2O3` **19.29 → 18.29** (+ `corrections` entry). Damaged 8/9 glyph; sum + Table-2 recast select 18.29.

**Table 2 VI (left) — ≥12 rows sampled**

| T °C | M% / key oxides | vs extract | notes |
|---:|---|---|---|
| 0 | 44.02 / 4.56 / **8.04** / 36.44 / 6.95 | OK | FeO prior correction; matches Table 1 VI all-Fe-as-FeO renorm |
| 1475–1632 | 1.46…9.74; FeO 7.01→3.23 | OK | T, M%, major oxides |
| 1726 | CaO **8.79** | OK | prior 4.79→8.79; closes five-oxide sum |
| 1796 | 38.35; MgO 44.67; CaO 11.27 | OK | |
| 1960 | M% **86.05** | OK | prior 36.05→86.05 (monotonic 65.21…89.25) |
| 2038–2291 | high-Al / high-Ca residues | OK | |

**Table 2 V (right) — ≥12 rows sampled**

| T °C | note | match |
|---:|---|---|
| 0 | 52.32 / 19.13 / 9.37 / 6.25 / 12.93 | OK (recast of fixed Table 1 V) |
| 1640 / 1719 | prior T and MgO=8.11 corrections | OK |
| 1809 | prior T 1309→1809 | OK |
| 1957–2255 | Si→0, Al↑, Ca↓ | OK |

**Method / prose**

| pin | printed | extract | match |
|---|---|---|---|
| temperature step | 70 °C | 70.0 | OK |
| step duration | 15 min | 15.0 | OK |
| ANT window | 1600–1700 °C; mass loss 15–35 % | same | OK |
| dissipation @1500 °C (reduced mass) | SiO 2 %, K 3 %, Na 14 % | same | OK |
| recent Moon Na dissipation | ~2 % | 2.0 | OK |

### Coverage (non-P0)

- Fig. 1 / Fig. 2 figure-only (not digitised) — intentional.  
- Partial pressures / activities for I–IV referred to Markova 1983 LPS — not in this abstract.  
- Sample III oxide sum of landed cells (87.66) ≠ printed 99.25 with Al2O3=20.72 as read; no alternate glyph forced (not treated as P0 without a unique reading).  
- Sample VII sum 97.57 vs printed 97.17 (±0.4) — within damaged-glyph noise; not changed.

---

## 2. kems-035-sauerborn-2005

**PDF identity:** Sauerborn (2005), Bonn dissertation *Pyrolyse von Metalloxiden und Silikaten…* (bonndoc OA, 151 pp).  
**Extract:** `data/literature/extracts/kems-035-sauerborn-2005.yaml`

### Inventory

| Layer | Count |
|---|---|
| Nested observations | 60 (many `*_deep2` superseder twins) |
| Species | Na, Fe, O2 |
| Tables on locators | 2.1–2.3, 3.1–3.3, 4.1, 5.1–5.8, 7.1–7.3 |
| `fidelity_samples` | 11 |

### Method

`pdftotext -layout` per published page for Tables 2.1, 2.2, 5.1, 5.3–5.6, 5.8; 150–200 dpi rasters for Table 5.6 (FeO 112.52) and run-log pages; compare heating/cooling grid cell-by-cell.

### Sampled checks (≥20 observations / ≫20 cells)

**Table 2.1 lunar soil element wt% — 4×9 = 36 cells OK**  
Apollo 14 O/Si/Al/Ca/Na/Mg/Fe/Ti/übrige = 45.0 / 22.5 / 9.3 / 7.9 / 0.5 / 5.5 / 8.1 / 1.0 / 0.3; Apollo 16 / 12 / Luna rows match PDF p. 15.

**Table 2.2 mineral vol% — 10 cells OK**  
Apollo 16: Plag 45 / Pyx 4 / Ol 1 / Ilm 1 / Gläser 49; Apollo 17: 16 / 31 / 1 / 6 / 46.

**Abb. 2.2 / JSC-1 pie — OK**  
JSC-1 Na2O **2.7** wt%; Apollo 14 14163 SiO2 47.3 … Na2O 0.7; MELTS liquidus **1264 °C** (p. 17).

**Table 5.1 reference runs — OK**  
Tmax: ohne Probe 800* / CaO **1638** / SiO2 **1400** / ZnO **1188** °C; high-T durations 33 / 60 / 84 / 101 s.

**Table 5.3 run log + full heating/cooling grid — OK (64+ rate cells)**  

| run | Tmax °C | mass g | high-T s | t>1400 s | heat max °C/s | cool max °C/s |
|---|---:|---:|---:|---:|---:|---:|
| MS1 | 1542 | 0.717 | 77 | 31 | 58.9 | −57.6 |
| MS2 | 1563 | 0.991 | 55 | 30 | 92.5 | −87.5 |
| MS4 | 1425 | 1.002 | 38 | 10 | 70.0 | −75.0 |
| MS5 | 1580 | 7.856 | 174 | 45 | 27.3 | −38.4 |

All TVH→800/1000/Tmax and shutter→1000/800/600 rates match PDF p. 79.

**Table 5.4 MS1 cold-trap EDX — OK**  
Foil without splashes Na **9.6±1.7**; blank Na 0.6±0.6; splash Na 42.0 / P 9.9 / Cu 43.4; O omitted by author.

**Table 5.5 MS2 cold-trap EDX — OK**  
MS2 foil Na **3.5±1.1**; untreated mean O 17.4 / Na 0.3; MS2 O 39.4 / Ta 55.

**Table 5.6 MS5c EPMA — 29 spots OK vs PDF p. 103 raster**  
Incl. Nr.4 Olivin SiO2 **40.34** / FeO 15.39 / MgO 42.79 / Σ 99.2; Nr.7 Plag; Nr.1 Mischanalyse FeO **112.52** / Σ 124.6 (printed mixed analysis — not a transcription error).

**Mass-loss prose — OK**  
MS2 **3.2** wt% (p. 88); MS1 2.6; SiO2 ref 1.1; MS5 2.9 (+ competing 43 % melted vs Abb. 5.47 54 % residual — documented, not averaged).

### Coverage (non-P0)

- QMS time-series figures typed figure-only / blocked (intentional).  
- `*_deep2` duplicates mirror parents (deepening policy).  
- Table 5.3 vs 5.8 MS1 Tmax 1542 vs 1560 contradiction preserved on 5.8 obs.  
- Some appendix grids live in `tables.md` sidecars referenced by obs notes.

**P0: 0.** Tip unchanged for this file.

---

## 3. kems-093-piacente-1975

**PDF identity:** Piacente et al. (1975), *Silikáty* č. 4, 289–299 (IRSM OA scan, 11 pp).  
**Extract:** `data/literature/extracts/kems-093-piacente-1975.yaml`

### Inventory

| Layer | Count |
|---|---|
| Nested observations | 34 (geometry, Table I×4 species, CC eqs, conclusions means, figure-only, absences) |
| `fidelity_samples` | 8 |
| Species | Fe, SiO, Mg, O2, FeO, Fe2SiO4 |

### Method

400 dpi render of PDF p. 4 (Table I); `pdftotext` for eqs (2)–(3) and CONCLUSIONS; cross-check T ranges / crucible masses in prose.

### Sampled checks (≥20)

**Table I second-law ΔH_sub (kJ/mole) @ 1800 K — all cells OK**

| Crucible | T [K] | Fe | SiO | Mg | O2 |
|---|---|---:|---:|---:|---:|
| ThO2 | 1680–1920 | 407.4±9.2 | 456.8±13.0 | 545.5±13.4 | — |
| ZrO2 | 1673–1925 | 414.9±13.4 | 463.9±11.3 | **540.5±12.6** | — |
| Re | 1699–1920 | 410.3±7.9 | 461.4±8.8 | 553.9±10.0 | 473.1±13.0 |
| mean | | 410.3±12.6(a) | 460.5±12.6(a) | 548.5±12.6(a) | 473.1±13.0(b) |

**Clausius–Clapeyron averaged log10 P (kPa) — OK**  
Fe: A=9.34±0.72, B=21.4±0.8; SiO: 10.21±0.36 / 24.0±0.8; Mg: 12.18±0.68 / 28.6±0.7; O2 eq.3: 10.4±0.9 / 24.7±1.1.

**CONCLUSIONS competing means — OK (not averaged)**  
420.3 / 460.5 / 548.5 / 473.1 vs Table I means 410.3 / … — both landed with `competing_observation_do_not_average`.

**Geometry / calibration — OK**  
45 eV ionizing energy; σ(O2)=1.83, σ(SiO)=4.37; 10 % fayalite; ThO2 masses 160 / 363 mg; ZrO2 388 mg; AP Fe/SiO/Mg 8.4 / 2.0 / 7.4 eV.

**Typed absences — OK**  
O2 blank in ThO2/ZrO2 cells; alumina discarded; Pt 1400–1445 K insufficient; FeO+/Re oxides high-T Re detected not tabulated.

### Coverage (non-P0)

- Figs 2–6 ion-current / P(T) series figure-only (no digitised points).  
- No raw I⁺(T) table in the paper — only second-law slopes + averaged CC coeffs.  
- Eq.4 Mueller fayalite mechanism typed model_derived.

**P0: 0.** Tip unchanged for this file.

---

## Fix commit

```
5d0baf6c5 fix(kems-026): correct Table 1 sample V Al2O3 19.29 → 18.29
```

File touched: `data/literature/extracts/kems-026-markova-1984.yaml` only. **No PDFs in commit.** Branch ahead of `origin/work-v064-green` by 1; **no-push** pending V-style verify.

## Ready for V-style verify?

| extract | ready? |
|---|---|
| kems-026-markova-1984 | **Yes — after verifying the Al2O3=18.29 fix** |
| kems-035-sauerborn-2005 | **Yes — clean** |
| kems-093-piacente-1975 | **Yes — clean** |
