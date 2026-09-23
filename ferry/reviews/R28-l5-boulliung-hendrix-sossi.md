# R28 — L5 near-ready boulliung / hendrix / sossi

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`origin/work-v064-green`) ≡ `2e9e17c3d` (L5 base)  
**Commit under review:** `aa33a2c2c` on `origin/empirical/l5-near-ready-boulliung-hendrix-sossi-2026-09-22`  
**Stack (base→tip):** single commit `aa33a2c2c` extracts: close printed composition (and Sossi T/fO2) on L5 near-ready sources

**Files:**  
- `data/literature/extracts/hendrix-2024-reactivity-reduced-simulants.yaml`  
- `data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml`  
- `data/literature/extracts/boulliung-2025-mercury-volatile-metals-magmatic.yaml`  

**Intent:** Close printed engine_point waypoints on three assigned sources. Land only printed evidence. Honor d-041 (no Sossi recipe→composition). No vacuum-as-fO2. Boulliung all GAPs refused honestly.

**Attack surface:** invented fO2; recipe→calc composition (d-041); wrong oxide maps; vacuum-as-fO2; numbers changed on non-touched sources; READY count overclaimed.

**Method:** Worktree `/workspace/repos/wt/slot-09` detached at `aa33a2c2c`. Static diff vs `2e9e17c3d`. Hendrix Table 1 vs PDF `pdftotext` (published p.2489 / PDF p.4). Sossi PDF corpus files are HTML interstitials — stamp-integrity only for majors. Live `Migrator._migrate_extract` (write=False) on the three sources; `consumer_readiness` / `collect_consumer_inputs` for engine_point, oxygen route/authority, composition route, T/fO2 identity. Focused pytest + extract validator. Mode: **ran-tests**.

---

## Findings

No P0 / P1 / P2 / P3 on the named claim.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Invented fO2 | **Fail (safe).** Hendrix: 0/8 obs with `point_conditions.fO2_*`; oxygen GAP (`missing_evidence`); extract notes pure H2 / fO2 not printed (`hendrix…yaml:276`, `:475`). Boulliung: 0/9 fo2 in pc; `fO2_control` None; total_pressure_Pa stays `unknown`/`not_published`; tip only appends `notes`/`refused` (no numeric oxygen). Sossi READY rows select `observation_fO2_log` / **printed** (70/70); values bit-identical to extract `logfO2` (0 mismatches). Pre-existing experiment `fO2_control` is CO–CO2 mix prose (unchanged vs base), not a numeric invent; does not close oxygen on non-Table-1 rows. |
| Recipe→calc composition (d-041) | **Fail (safe).** Sossi `printed_composition` is measured mean majors map (`:56–75`); recipe An42Di58 + Fo + Fe2O3 retained under `characterization` + `starting_material_recipe` prose only (`:82–90`, `:218`). No calculated oxide map from recipe. |
| Wrong oxide maps | **Fail (safe) on Hendrix.** PDF Table 1 matches tip maps for JSC-1A / LMS-1 / LHS-1 (SiO2…P2O5/K2O); SO3 0.11/0.1 omitted with note (not in oxide vocabulary); Cr2O3/P2O5 absences match PDF blanks. Sossi majors: cannot page-check (corpus PDFs are Cloudflare/HTML interstitials); Table 1 run digits unchanged under `rows→series` / `temperature_C→T_C` rename (0 line mismatches on Cu/Zn measured + logKstar + delta65Cu). |
| Vacuum-as-fO2 | **Fail (safe).** Boulliung refused vacuum/evacuation→oxygen and ~2.7 mbar→`total_pressure_Pa` (`:455–465`); live pressure still unpublished. Hendrix vacuum-pump-oil / desiccator notes explicitly non-run-pressure (`:103`, `:272–273`). |
| Numbers changed on non-touched sources | **Fail (safe).** `git diff 2e9e17c3d...aa33a2c2c` = exactly the three extract paths above. |
| READY count overclaimed | **Fail (safe) under stated probe.** Write-up 50/51 = unique `(T_K, fO2_log)` × observation-base among READY (25 Cu + 25 Zn) plus one collapsed empty-pc GAP. Live: **70/79** observation-points READY (all Table-1 measured); **9** GAP (alpha / logKstar / zoning — no T/fO2 in pc). Hendrix **0/3** experiments READY (comp closed; O2+P still GAP). Boulliung **0** READY (`unsupported_print_form` + O2 + P). |

---

## Live READY (engine_point)

| Source | Obs READY | Notes |
| --- | --- | --- |
| **hendrix-2024** | **0/8** (0/3 exps) | Comp: `normalized_printed_composition` / DERIVED on all 8; oxygen + pressure `missing_evidence` |
| **sossi-2020** | **70/79** (claim contexts **50/51**) | Oxygen: `observation_fO2_log` / PRINTED; T: `observation_temperature_K` from `T_C+273.15` (0 identity misses); pressure 101325 Pa printed (pre-existing) |
| **boulliung-2025** | **0/9** | Comp remains prose string → `unsupported_print_form`; no fo2 invent |

### What looks sound

- Hendrix Table 1 oxide maps closed; O2+P honestly left open under pure H2 flow with no printed fO2/P.
- Sossi: `rows→series` + `T_C` unlocks per-run `point_conditions.temperature_K` + `fO2_log`; model `logKstar` blocks stay `rows` (no false series expansion).
- d-041: recipe not used as closed composition.
- Boulliung: documentation-only tip (notes + refused); GAPs unchanged in substance.
- Scope: three extracts only; derived store not regenerated (extract-only as claimed).

### Residual notes (not severity)

- Sossi article PDF still not readable in corpus (ASK already filed); majors map trusted as prior-extract/scout transcription — no contradictory evidence found in-repo.
- Boulliung validator: 6 pre-existing errors (absolute `provenance_path`, equipment shape, fidelity_samples); tip does not add/remove them.

---

## Tests

Tip worktree `aa33a2c2c` (`/workspace/repos/wt/slot-09`), repo `.venv`, `-o addopts=`:

- `tests/battery/test_waypoints.py` + `tests/battery/test_printed_fo2.py`: **68 passed**
- Live migrate probes (READY, fo2 authority printed, T/fO2 identity, hendrix PDF oxides, d-041 recipe placement, boulliung refusals, cross-scope file list): **passed / attacks failed**
- `tools/validate_literature_extracts.py`: hendrix + sossi **OK**; boulliung 6 pre-existing

No fix patch. Do not push. Do not commit to mailbox.

---

## Verdict rationale

Claim holds under adversarial live migrate and Hendrix PDF check: compositions land without inventing fO2 or recipe calc; Sossi Table-1 points get printed per-run T/logfO2; boulliung refusals match live GAPs; READY 50/51 matches the write-up’s unique-context probe; no cross-scope drift.

VERDICT: R28 | LAND | P0=0 P1=0 P2=0 P3=0 | ran-tests

READY: hendrix 0/3 (comps closed); sossi 70/79 obs (50/51 contexts); boulliung 0 (refused)
