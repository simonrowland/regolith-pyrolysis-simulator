# N8 NEW EXTRACT — NTRS reports (BACKLOG 3)

**Branch:** `empirical/n8-ntrs-reports-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `4e6ccf2a97b1eadd9082e680a2d98144696ae1b0`
**Worktree:** `/workspace/repos/wt/slot-10`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/` (private; not committed)

## Identify first

| Corpus dir | PDF p1 identity | Sidecar citation | Match? |
| --- | --- | --- | --- |
| `ntrs-20210022801` | Aaron D.S. Olson (NASA KSC), *Lunar Helium-3: Mining Concepts, Extraction Research, and Potential ISRU Synergies*, AIAA ASCEND 2021 full paper (15 pp) | truncated `Lunar Helium-3: Mining Concepts,` | Yes — title truncated on sidecar; PDF is the full Olson ASCEND paper |
| `ntrs-20210012628` | Same Olson title/author, AIAA ASCEND 2021 Abstract (2 pp) | `2021 ASCEND Abstract AIAA` | Yes — abstract companion of `20210022801` |
| `ntrs-20120000846` | Nathan Jacobson (NASA GRC) & Dwight Myers (East Central Univ.), *The Vaporization of B₂O₃(l) to B₂O₃(g) and B₂O₂(g)* (Poster), ECS 220th Meeting, Boston, Oct 2011; footer PS–00625–1011 | `PS-00625-0911` | Yes — same poster ID family; sidecar month stamp 09 vs footer 10 |

## STEP 0

| Paper | author/`rg` | DOI | PDF ↔ sidecar | Prior extract? |
| --- | --- | --- | --- | --- |
| `ntrs-20210022801` | No Olson / lunar He-3 mining extract | none (NTRS) | SHA-256 match | No |
| `ntrs-20210012628` | No Olson ASCEND abstract extract | none | SHA-256 match | No |
| `ntrs-20120000846` | Other Jacobson extracts exist (`costa-jacobson-2015`, `kems-032`, `kems-139`) — **different papers** | none on poster (journal "in press" cited, not this PDF) | SHA-256 match | No B₂Oₓ poster / NTRS 20120000846 |

All three STEP 0 PASS → extract.

## Validate

```
OK: 1 extract file(s) valid   # ntrs-20210022801.yaml
OK: 1 extract file(s) valid   # ntrs-20210012628.yaml
OK: 1 extract file(s) valid   # ntrs-20120000846.yaml
OK: 3 extract file(s) valid   # --check-fidelity-match
```

0 new errors. Validator: `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py`.

## Per-paper status

### 1. `ntrs-20210022801` — LANDED

**Extract:** `data/literature/extracts/ntrs-20210022801.yaml`

**Scored:** `concentration_series` — SWIM-implanted JSC-1A residual ⁴He after HEAT HPHX agitation at room temperature:

| Flow | After implant | After HEAT (control-corrected) | Removed | Remaining |
| --- | --- | --- | --- | --- |
| 1.5 g/s | 2.7 ppb | 0.7 ppb | 70% | 29% |
| 9.0 g/s | 2.1 ppb | 0.1 ppb | 96% | 4% |

**Context (d-032 Option A):** SWIM / HEAT / SCAN apparatus; Mark-III design throughput and ³He collection claims; Table 1 Apollo 10086.16 design-basis volatiles at 700 °C (quoted — not this campaign's measurement).

**Not extracted:** Figs 16–18 curves (figure-only); absolute implanted inventories; control ⁴He blank (not printed). DERIVED stamps only on T_K = T_C + 273.15 companions.

### 2. `ntrs-20210012628` — LANDED (context-only)

**Extract:** `data/literature/extracts/ntrs-20210012628.yaml`

Abstract of the same Olson 2021 talk. Context only (Senior-1991 Option A pattern): Mark-III design numbers (1258 t/h excavate, 556 t/h process, HPHX to 700 °C, 85% recuperation, 66 kg ³He/yr at 20 ppb); qualitative FTI/KSC SWIM→HEAT→vacuum-furnace/MS campaign (2 kg JSC-1A; agitation loss rises with flow). **No scored residual-He ppb / % removed** — those print only in the full paper.

### 3. `ntrs-20120000846` — LANDED

**Extract:** `data/literature/extracts/ntrs-20120000846.yaml`

**Scored:** `gibbs_table` ΔfH°(298.15 K):

| Species | ΔfH° / kJ·mol⁻¹ | Uncertainty | Locator |
| --- | --- | --- | --- |
| B₂O₂(g) | −479.9 | ±25.7 | Conclusions (p2) |
| B₂O₂(g) | −479.9 | ±41.5 | Title-strip (p1) — same central value; **not averaged** |
| B₂O₃(g) | −833.4 | ±13.1 | Title-strip + Conclusions (consistent) |

**Context:** FeB/Fe₂B boron activity fix; 1:1:1 FeB:Fe₂B:B₂O₃; KEMS; W1BD note; working reaction `4/3 FeB(s) + 2/3 B₂O₃(l) = B₂O₂(g) + 2/3 Fe₂B(s)`.

**Not extracted:** per-run second/third-law table cells (poster OCR columns unreliable); *J. Phys. Chem.* 2011 full paper (cited "in press", not this PDF).

## Refusals

| Paper | Reason |
| --- | --- |
| *(none)* | All three STEP 0 PASS; all three LANDED |

## Commit

```
4e6ccf2a9 extracts: N8 Olson 2021 He-3 ASCEND paper+abstract + Jacobson/Myers 2011 B2Ox poster
```

3 extract YAML files added. Private PDFs not committed. Pushed to `origin/empirical/n8-ntrs-reports-2026-09-22`.

## Optional follow-ups (not done)

- migrate + readiness / engine_point for the new `source_id`s once I1 is green
- Obtain Jacobson & Myers *J. Phys. Chem.* 2011 article for a richer KEMS series under a separate `source_id`
- Close SOURCE_STATUS inbox rows for the three corpus ids once owner remaps STATUS
