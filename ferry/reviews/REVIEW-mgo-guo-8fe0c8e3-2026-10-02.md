# REVIEW — MgO activity reference conversion + Guo 2021 typed periclase (tip 8fe0c8e3)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` (`.slot-busy` cleared after STATUS ship)
- **Date:** 2026-10-02 ~00:52 ET
- **Mode:** read-only on tip; extracts not edited; tip not rebased onto green
- **Policy gate:** use-values-first / wrong-number + printed-source guards only
- **REQ:** `REQ-review-mgo-guo-8fe0c8e3-2026-10-02.md`
- **Corpus:** `/workspace/ferry-inbox/acquired/guo-2021-mgo-activity-cmas.pdf` (ISIJ Int. 61(11):2724–2730); JANAF Mg-008 / Mg-009 YAML at tip
- **Green base:** `c9b6e545d14b7703f3825b1e8cc1ce761660d6a7`
- **Prior HOLD:** batch-2 Guo `a580172d6b8d36aa1c4edfd2a56cc56a370530d6` (values-LAND; finalize HOLD awaiting this delta)

Tip `8fe0c8e377ac0a982f2df494af781419a3231187` = merge of `d5e021572` (MgO scorer via `3d7a1cf69`) + `88b92ffae` (typed Guo on `bb2b6fe1d` on `a580172d6`) onto green.

---

## 1) MgO solid→liquid offset from JANAF (scorer `3d7a1cf69`)

### Node interpolation (formation Gibbs, kJ/mol)
Independent recompute from tip tables `data/literature/compilations/janaf/tables/Mg-008.yaml` (cr, periclase) and `Mg-009.yaml` (l), linear between 1800–1900 K nodes (fraction 0.73 at 1873 K), R = JANAF 8.31441 J mol⁻¹ K⁻¹:

| T (K) | G_s° (Mg-008) | G_l° (Mg-009) | ΔG_fus (kJ/mol) | Δlog₁₀(a) (dex) |
|---:|---:|---:|---:|---:|
| 1800 | −360.851 | −330.816 | +30.035 | — |
| **1873** | **−345.91812** | **−317.44751** | **+28.47061** | **+0.793984** |
| 1900 | −340.395 | −312.503 | +27.892 | — |
| 2000 | −320.021 | −294.274 | +25.747 | +0.672434 |
| 3100 | −100.697 | −100.572 | +0.125 | +0.002106 |
| **3104.945598** | ≈ equal | ≈ equal | **→ 0** | **→ 0** |

- Sign: **positive** below T_fus (solid more stable) — converting a solid-ref activity to liquid-ref **increases** log₁₀(a) by +ΔG_fus/(RT ln 10).
- Crossing from cr/l overlap zero of ΔG_fus: **3104.945598 K** (matches tip comment / test pin; ≈ JANAF T_fus 3105 K). Accepted melting pin remains 3100 K (policy), distinct from the table crossing used for the shift→0 sanity.
- Node-set SHA256 pins match tip `_FUSION_NODE_SET_SHA256`: Mg-008 `d90e9f5d…c2505e`, Mg-009 `1444ecc5…620a7e`.
- Production API `janaf_fusion_energy("MgO", …)` at tip returns the same ΔG_fus / T_m / table ids (verified via PYTHONPATH import; full pytest env not synced on 16GB box).
- Scorer wiring: `"MgO": ("Mg-008","Mg-009")` in `_FUSION_TABLES`; expected polymorph `Mg-008` → `PERICLASE`; wrong polymorph refuses conversion (test asserts spinel refuse).

### Nothing invented
- Offset is table-derived, not fitted. Mg-009 glass↔liquid phase-unknown caveat retained (same class as Al-100 / Ca-028).

---

## 2) Guo delta vs batch-2 LAND (`a580172d6` → `88b92ffae`)

### Wrong-number guards (reconfirm vs PDF; values unchanged)
Deep walk of extract excluding `standard_state`: **0 numeric/string diffs** vs `a580172d6`.

Table 5 p.2728 — 8 a(MgO) + slag wt% + x[Al/Si/Ca/Mg] in Sn at 1873 K vs PDF:

| No | a(MgO) page | tip | CaO | MgO | match |
|---|---:|---:|---:|---:|:---:|
| 1 | 0.4478 | 0.4478 | 35.41 | 9.26 | yes |
| 2 | 0.5121 | 0.5121 | 36.62 | 9.46 | yes |
| 3 | 0.5379 | 0.5379 | 38.33 | 9.51 | yes |
| 4 | 0.6376 | 0.6376 | 39.32 | 9.77 | yes |
| 5 | 0.4236 | 0.4236 | 41.87 | 4.63 | yes |
| 6 | 0.4751 | 0.4751 | 40.64 | 6.54 | yes |
| 7 | 0.5716 | 0.5716 | 39.22 | 8.30 | yes |
| 8 | 0.6472 | 0.6472 | 38.58 | 10.22 | yes |

**8/8 Table 5 PASS** (all oxide wt% + Sn mole-fraction cells + activity).

Table 6 p.2730 — γ(Mg in Sn) + a[Mg](R) + x[Mg]:

| x[Mg] | a[Mg](R) | γ page | tip γ | match |
|---:|---:|---:|---:|:---:|
| 0.001355 | 0.004297 | 3.1713 | 3.1713 | yes |
| 0.00155 | 0.004914 | 3.1704 | 3.1704 | yes |
| 0.001628 | 0.005162 | 3.1706 | 3.1706 | yes |
| 0.001929 | 0.006118 | 3.1718 | 3.1718 | yes |
| 0.001282 | 0.004065 | 3.1707 | 3.1707 | yes |
| 0.001438 | 0.004559 | 3.1704 | 3.1704 | yes |
| 0.00173 | 0.005485 | 3.1706 | 3.1706 | yes |
| 0.001959 | 0.006211 | 3.1703 | 3.1703 | yes |

**8/8 Table 6 PASS.**

### Typed reference only (the delta)
`88b92ffae` replaces prose `standard_state: pure solid MgO; the paper does not name a polymorph` with structured:

- `convention: raoultian_pure_endmember`
- endmember MgO / phase cr / polymorph **periclase**
- `inferred: false` with locator Table 5 p.2728
- Printed phrase retained: `reference_state_as_printed: relative to pure solid` (paper §3.4 / Table 5)

At 1873 K solid MgO has a single polymorph (periclase / rock salt) — typing is a fact, not an inference. Method remains `quench_equilibration`.

---

## 3) `bb2b6fe1d` vs green — migrate.py no-op + meaningful pin

- `git diff c9b6e545d bb2b6fe1d -- simulator/battery/migrate.py` → **0 lines** (also 0 at merge tip `8fe0c8e37` vs green). Confirms REQ: migrator change already landed via `0f8aaec7c` on green.
- Tip adds only `tests/battery/test_migrate.py` (+64) vs green for this concern:
  - `test_stolyarova_table3_137_row_ids_and_reference_states_unchanged` — migrates real Stolyarova 1996 extract; asserts **137** `stolyarova_1996_table3_*` observation ids and pins `sha256` of `(oid, reference_state)` payload = `5e605d6e940f020c15e0e931b7c9a706c2b0874ceeaf6ee285a370f5a3557961`. **Meaningful regression:** any migrator drift that reshuffles Stolyarova typed refs fails the digest.
  - `test_guo_structured_standard_state_lifts_periclase_reference` — asserts migrator lifts the typed periclase block onto 8 Guo Table-5 observations (RAOULTIAN_PURE_ENDMEMBER / CR / PERICLASE).

---

## 4) NOT-FIXED lens (engine/scorer gaps, not extract wrong-numbers)

- **Mg-in-Sn γ:** correctly unsupported for OpenIMCC / melt-activity scoring (model_derived metal-solvent γ). Controller studio proof: 8/8 refuse unsupported — expected.
- **Mg-009 phase-unknown** around glass↔liquid transition — documented caveat shared with Al-100 / Ca-028; not a wrong number.
- **Controller studio proof** (not re-run on 16GB): 8 a(MgO) numeric residuals median −0.082 dex with 8/8 fusion-conversion notices after this tip — consistent with +0.794 dex solid→liquid shift applied to solid-ref measurements vs liquid-ref engine.
- Full W3 / battery_migrate / store regen: **skipped** on ~16GB VPS (values + JANAF guards complete).

### Nothing invented
- No extract edits; iso-activity contour figures not digitised; Table 4 reference-slag x[Mg] replicates retained as printed.

---

VERDICT: LAND 8fe0c8e377ac0a982f2df494af781419a3231187
