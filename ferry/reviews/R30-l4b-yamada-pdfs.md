# R30 — L4b Yamada PDF fill (kems-087 / kems-105; ichise kept)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`origin/work-v064-green`) ≡ `2e9e17c3d` (L4/L4b base)  
**Commit under review:** `8a3343690ce5076a53e08f9719fa0d7ca90d7b89` on `origin/empirical/l4b-yamada-pdfs-2026-09-22`  
**Stack (L4 tip→L4b tip):** `c49479211` close L4 printed composition on kems-087/105 → `8a3343690` series pressure GAP note + L4b provenance  
**L4 parent (Ichise landings):** `b5774475083320210321535e9cc749d81885237c`

**Files:**  
- `data/literature/extracts/kems-087-yamada-kato-1980.yaml`  
- `data/literature/extracts/kems-105-yamada-1983.yaml`  
- `data/literature/extracts/kems-112-ichise-1989.yaml` (**byte-identical** to L4 tip; blob `b464b5b41267bbcb4dad5041ee60373c07eae0a9`)

**Intent:** With ferry PDFs present, land **only** printed composition / oxygen / pressure on Yamada 1980/1983. Vacuum KEMS without printed fO2 → oxygen GAP. Figure-only refuse. Keep L4 Ichise Table 1/5 landings untouched.

**Claim under test:** kems-087 Fig.9 1.52 wt% P comps; kems-105 bench + 1 wt% P series; no invented fO2; ichise kept from L4.

**Attack surface:** invented fO2 from dissolved-O / Al–Be deoxidation; chamber P from ~1×10⁻⁹ atm P vapor-pressure estimate; wrong Fig.9 mole fractions (wt% vs at%); digitizing Figs 4/5/8 or kems-105 Figs 1–4 into charges; cross-scope drift on kems-112; READY overclaim.

**Method:** Worktree `/workspace/repos/wt/slot-11` at `8a3343690`. Static diff vs L4 `b57744750`. PDF page-check: ferry `kems-087-yamada-kato-1980.pdf` / `kems-105-yamada-1983.pdf` (`pdftotext` + `pdftoppm` p.244–248 / p.51–53). Live `Migrator._migrate_extract` (write=False) on the three sources at tip and with L4 extract blobs swapped; first `engine_point` consumer per observation via `engine_point_requests` / composition / oxygen / pressure / T. Focused pytest + extract validator. Mode: **ran-tests**.

---

## Findings

No P0 / P1 / P2 / P3 on the named claim.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Invented fO2 (dissolved O / Al–Be deox) | **Fail (safe).** Live tip: `oxygen_condition` selected **0/16** (087), **0/13** (105), **0/49** (112); `fo2_in_point_conditions_obs=0`; `fO2_control` None on all linked experiments. Dissolved O stays context only (`oxygen_after_melting_ppm_less_than: 50.0` on 087 geometry context; 105 `O_in_pure_iron_ppm_range` / Al 0.2 wt% / Be 0.01 wt% deoxidizer notes) — never `fO2_*` / buffer / pO2. Extract notes explicitly “Vacuum KEMS: no fO2 invented” (`kems-087…yaml:20`, `kems-105…yaml:22`). |
| Chamber P from ~1e-9 atm P vapor estimate | **Fail (safe).** Tip `pressure_environment.total_pressure_Pa` is `state: unknown` / `not_published` on both `fep-kems-1600c-series` and `fep-1p52wt-fig9` (`kems-087…yaml:84–86`, `:141–143`) with locator note that ~1e-9 atm is **sample vapor pressure**, not chamber total. Live: `pressure_boundary:missing_evidence` **16/16**. 105 likewise unknown (`:64–66`); live **13/13** pressure GAP. The 1e-9 atm value remains only on observation `yamada_kato_1980_p_vapor_pressure_estimate_1wt` (species partial-pressure quote), not as run pressure. |
| Wrong Fig.9 mole fractions | **Fail (safe).** PDF p.248 body: “sample containing **1.52 wt% (2.71 at%) P**” for Fig. 9. Tip lands `mole_fraction` `P=0.0271`, `Fe=0.9729` (2.71 at% → X_P; Fe complement; not renormalised) on `fep-1p52wt-fig9` (`:111–124`); 1.52 wt% kept on `printed_composition` string only. Live waypoint on the two retargeted obs (`…_P_plus_count_rate_1p52wt`, `…_fig9_P_plus_peak_figure_only`): `normalized_initial_composition` / **derived** → `{'P': 0.0271, 'Fe': 0.9729}`. Series retains range string **0.7–3.2 wt% P (1.3–5.7 at%)** only (`:66`) — matches synopsis p.244; **14/16** still `unsupported_print_form`. Figs 4/5/8 stay `admission_status: figure_only`. |
| kems-105 invent X_i / structured ternary | **Fail (safe).** PDF p.52: determinations with samples containing **1 wt% P** and various concentrations of solute i at 1600 °C; no charge table. Tip: bench `yamada-kato-rm6k` (BeO for Fe–P–Al/Ti, alumina else — matches PDF p.51) + experiment `fep-i-1wtP-series` with string `printed_composition` only (`kems-105…yaml:28–54`); **no** `initial_composition` map. All 13 obs retargeted to that experiment (closes L4 `missing_bench` 13/13 → 0). Live composition: **0/13** `unsupported_print_form`. Figs 1–4 remain figure-only; refused block covers axis digitization + numeric chamber P (`:793–802`). T **13/13** selected (1873.15 K from 1600 °C). |
| Ichise / cross-scope drift | **Fail (safe).** `git diff b57744750..8a3343690` = **exactly** the two Yamada extract paths. kems-112 blob hash identical tip↔L4. Live 112 counts bit-same: 49 obs; composition 40/49; oxygen 0/49; pressure 47/49; READY 0/49. |
| READY overclaim | **Fail (safe).** Live matches write-up: 087 **0/16**, 105 **0/13**, 112 **0/49** `engine_point` READY (O₂+P still GAP on Yamada; O₂ on Ichise). Composition closed only on 2/16 Fig.9-linked 087 rows as claimed. |

---

## Live READY (first engine_point consumer / obs)

| Source | Before (L4 `b57744750`) | After (L4b `8a3343690`) | Notes |
| --- | --- | --- | --- |
| **kems-087** | READY 0/16; comp 0/16; O₂ 0; P 0; T 16/16 | READY **0/16**; comp **2/16**; O₂ 0; P 0; T 16/16 | `fep-1p52wt-fig9` mole_fraction; series range still string |
| **kems-105** | READY 0/13; missing_bench stub 13; T 10/13 | READY **0/13**; comp 0/13 `unsupported_print_form`; missing_bench **0**; T **13/13** | bench+series landed; structured X_i refused |
| **kems-112** | READY 0/49; comp 40; O₂ 0; P 47; T 46 | **unchanged** | L4 Ichise kept |

### What looks sound

- Fig.9 prose is the only printable discrete charge; at%→mole_fraction with Fe complement; wt% not mis-converted.
- Vacuum KEMS honesty: no fO2 from <50 ppm O / 20–30 ppm O / Al–Be deoxidizers; chamber total pressure left unknown despite vapor-pressure estimate and “high vacuum” Be step.
- 105 closes the L4 missing_bench hole without inventing a ternary map.
- Scope: two Yamada YAMLs only; derived store not regenerated; validator OK; 68 waypoint/fo2 tests green.

### Residual notes (not severity)

- 105 `printed_composition` string softens PDF p.52 “1 wt% P” with “about 1 wt%” (elsewhere in the paper “about 1 wt%” appears for the Fe–P ln K host). String-only; no numeric land — not a wrong number.
- `normalized_*_composition` authority is DERIVED by construction even when the source map is printed (same as prior L reviews).
- Series-level ~1.5 g sample mass is copied onto `fep-1p52wt-fig9` with `approximate: true` (PDF: each alloy ~1.5 g) — documentation only; not an engine_point closer.

---

## Tests

Tip worktree `8a3343690` (`/workspace/repos/wt/slot-11`), repo `.venv`, `-o addopts=`:

- `tests/battery/test_waypoints.py` + `tests/battery/test_printed_fo2.py`: **68 passed**
- Live migrate probes (Fig.9 P/Fe map, fo2 absence, vapor-P≠chamber-P, 105 bench close, 112 bit-identity tip↔L4, READY counts): **passed / attacks failed**
- `tools/validate_literature_extracts.py` on kems-087 / 105 / 112: **OK**

No fix patch. Do not push. Do not commit to mailbox.

---

## Verdict rationale

Claim holds under adversarial live migrate and PDF page-check: Fig.9 1.52 wt% (2.71 at%) → X_P=0.0271 / X_Fe=0.9729 on two obs only; 105 gets bench + 1 wt% P series label without structured X_i; no fO2 or chamber pressure invented; kems-112 untouched from L4. READY stays honestly 0 on all three (vacuum KEMS oxygen open).

VERDICT: R30 | LAND | P0=0 P1=0 P2=0 P3=0 | ran-tests

READY: kems-087 0/16 (comp 2/16 Fig.9); kems-105 0/13 (bench closed; comp string); kems-112 0/49 unchanged
