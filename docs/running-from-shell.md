# Running simulations from the shell

The web app (`regolith-pyrolysis-run.py`, served on `http://localhost:3000/`) is the
interactive entry point. But the simulator is fully scriptable from the shell — useful
for batch runs, reproducible experiments, CI, and cluster work.

All commands assume you are **in the repo root** and use the project venv
(`./.venv/bin/python`). Create it with `python3 install-dependencies.py` (and
`install-engines.py` for the real thermochemistry engines).

---

## 1. Fire one simulation (the canonical command)

There is **no top-level `runner.py`** — the CLI is the `simulator.runner` module:

```bash
./.venv/bin/python -m simulator.runner \
  --feedstock lunar_mare_low_ti \
  --campaign C2A \
  --hours 24 \
  --output runs/my_run.json
```

`--feedstock` and `--output` are **required**. The run writes a single JSON result
document to `--output` (parent dirs are created automatically); nothing useful goes to
stdout. Convention is to drop outputs under `runs/`.

### Common flags (`simulator/runner/__init__.py`)

`simulator.runner` is a **package** (`simulator/runner/`), not a single module file; the
CLI and its argument parser live in its `__init__.py`.

| flag | default | meaning |
|---|---|---|
| `--feedstock` | *(required unless `--preset` supplies one)* | key from `data/feedstocks.yaml` |
| `--preset` / `--leg` | *(none)* / `faithful` | load a vacuum-pyrolysis distribution recipe and select its leg |
| `--compare` / `--observations` | off / preset sidecar | compare selected preset observables against an independent literature sidecar |
| `--output` | *(required)* | path for the JSON result document |
| `--campaign` | `C0` | campaign / recipe phase (see §4) |
| `--hours` | `24` | simulated hours to advance |
| `--mass-kg` | `1000.0` | batch mass |
| `--backend` | `internal-analytical` | `internal-analytical` (legacy alias `stub`), `alphamelts`, or `thermoengine` (see §5) |
| `--track` | `pyrolysis` | or `mre_baseline` |
| `--additive` | *(none)* | repeatable, e.g. `--additive=C=30` |
| `--engine` / `--engines` | *(none)* | per-intent engine override / config YAML |
| `--started-at-utc`, `--kernel-commit-sha` | *(none)* | determinism pins for golden fixtures |

### Output document

The schema is pinned by [`docs/runner-output-schema.md`](runner-output-schema.md) — read that for
the authoritative key list. The ones you reach for most often:
`schema_version`, `run_metadata`, `final_state`, `per_hour_summary`,
`stage_purity_report`, `vapor_pressure_source_report`, `shuttle_refusal_history`,
`shadow_trace`, `status`, `reason`, `error_message`.

A current run also emits `product_classification`, `terminal_product_taxonomy`,
`yield_disposition`, `thermal_train_report`, `condensation_refusals_by_species`,
`pO2_enforcement_by_hour`, `vapour_rail_instrumentation`, `degraded_path_engagement`,
`melt_redox_gate_floor_fallback_engagement`, `c7_product_report` and
`c7_refusal_diagnostic`. Do not treat any hand-copied list here as complete —
enumerate the keys off an actual artifact.

**Check the exit code, not just the file:** a failed or refused run still writes a full
JSON envelope (`status: "failed"` / `"refused"`) and exits non-zero.

### Literature preset comparison

```bash
./.venv/bin/python -m simulator.runner \
  --preset data/presets/vacuum_pyrolysis/pomeroy_cardiff_2006.yaml \
  --compare --backend alphamelts \
  --allow-fallback-vapor --allow-unmeasured-alpha-fallback \
  --output runs/pomeroy-2006.json
```

`--compare` requires `--preset`. The recipe declares its independent observation
sidecar; `--observations PATH` is an explicit override. The run JSON envelope
stays unchanged. Residual records and recipe/source/result digests are written
to `runs/pomeroy-2006.comparison.json`, with a concise Markdown report at
`runs/pomeroy-2006.comparison.md`. Assumed recipe fields force
`assumed-input`; absent pump-outlet or species surfaces produce typed
unsupported statuses rather than substituted values.

---

## 2. The product ledger in three lines (in-process)

For the North-Star "pot of dirt → products" view, `simulator.three_product_runner.run`
returns the classified product ledger directly:

```python
from simulator.three_product_runner import run
ledger = run(feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=24)
# The four CLAUDE.md §5 product classes:
#   metals_plus_O2, pure_silica_glass, industrial_mixed_glass,
#   refractory_ceramic_rump
# plus the supporting breakdown:
#   ingots_metals, oxygen, glass, captured_volatiles,
#   process_inventory_spent_reductant, unclassified
```

`unclassified` is the honesty account: mass the classifier could not assign to a product
class. A growing `unclassified` is a finding about the classifier, not a product.

Its CLI twin (writes a markdown or JSON report; diagnostic — no threshold enforcement):

```bash
./.venv/bin/python -m simulator.three_product_runner \
  --feedstock lunar_mare_low_ti --campaign C2A --hours 24 \
  --output report.md --format markdown        # or --format json; --early-tap; --backend
```

## 3. Per-hour control (in-process via `SimSession`)

```python
from simulator.config import load_config_bundle
from simulator.session import SimSession, SimSessionConfig, drive_auto_apply

b = load_config_bundle()
cfg = SimSessionConfig(
    feedstock_id="lunar_mare_low_ti",
    feedstocks=b.feedstocks, setpoints=b.setpoints, vapor_pressures=b.vapor_pressures,
    campaign="C2A_continuous", hours=24,
)
s = SimSession().start(cfg)
for _ in drive_auto_apply(s, 24):
    pass
rows = s.per_hour_summaries()   # T_C, P_total_bar, pO2_bar, metal_yields_kg,
                                # condensation_train_kg, O2_yield_kg_cumulative,
                                # O2_source_side_potential_kg_cumulative,
                                # O2_metric_label, mass_balance_pct, hour, campaign
```

> Note: `s.result_document()` raises unless a `result_document_factory` is configured.
> For the full result envelope use the `simulator.runner` CLI (§1); in-process, read
> `per_hour_summaries()` / `snapshot()`.

### NDJSON script harness

Drive a session with one JSON command frame per line (verbs: `start, advance, decide,
adjust, pause, resume, snapshot, quit`):

```bash
printf 'start --feedstock lunar_mare_low_ti --campaign C2A\nadvance\nadvance\nsnapshot\nquit\n' \
  | ./.venv/bin/python -m simulator session --script -
```

---

## 4. Available identifiers

**Feedstocks** (`data/feedstocks.yaml`, 29 keys). Modelled in-situ compositions
(no `class` key):

`lunar_mare_low_ti`, `lunar_mare_high_ti`, `lunar_highland`,
`lunar_pkt_kreep_average`, `lunar_spa_kreep_influenced`, `targeted_super_kreep_ore`,
`s_type_asteroid_silicate`, `m_type_metallic_phase`, `m_type_silicate_phase`,
`v_type_vesta_hed`, `e_type_enstatite_aubrite`, `ci_carbonaceous_chondrite`,
`cm_carbonaceous_chondrite`, `ceres_regolith`, `comet_nucleus`, `mars_basalt`,
`mars_sulfate_rich`, `mars_phyllosilicate_clay`, `mars_perchlorate_rich`.

Terrestrial simulant compositions, each carrying a `class` key
(`lunar_simulant` / `mars_simulant`) and a cited XRF provenance block. These exist so
lab experiments can be reproduced against the material actually used in them — they
are not stand-ins for in-situ regolith:
`lunar_highlands_lhs1`, `lunar_highlands_lhs1_yu_2025_reference`, `lunar_mare_lms1`,
`lunar_mare_oprl2n`, `lunar_highlands_nuw_lht_5m`, `lunar_highlands_nu_lht_2m`,
`lunar_mare_jsc_1a_legacy`, `lunar_eac_1a`, `lunar_mls_1a`, `mars_global_mgs1`.

The list goes stale as feedstocks land; enumerate `data/feedstocks.yaml` rather than
trusting this transcription.

**Campaigns** (`data/setpoints.yaml`, 11 keys): `C0`, `C0b_p_cleanup`,
`C2A_continuous`, `C2A_staged`, `C2B`, `C3`, `C4`, `C5`, `C6`, `C7`, `mre_baseline`.
`C7` (aluminothermic Ca recovery) is **default-off**.

Campaign *phases* the engine actually advances through are a different vocabulary
(`simulator.state.CampaignPhase`): `IDLE`, `C0`, `C0B`, `C2A`, `C2A_STAGED`, `C2B`,
`C3_K`, `C3_NA`, `C4`, `C5`, `C6`, `C7_CA_ALUMINOTHERMIC`, `MRE_BASELINE`, `COMPLETE`.
The session layer maps the setpoints keys onto them, so `C2A_continuous` → `C2A`,
`C0b_p_cleanup` → `C0B`, `C2A_staged` → `C2A_STAGED`; the phase names are accepted
directly too, which is why `--campaign C2A` works.

`data/vapor_pressures.yaml` is loaded automatically (not CLI-selectable).

---

## 5. Backend selection (fidelity)

- **`--backend internal-analytical`** (legacy alias `stub`; default): fast, deterministic, and
  *physically grounded for the extraction side* — `_internal_analytical_equilibrium` uses first-principles
  **Ellingham oxide-stability + Antoine vapor-pressure** thermodynamics (the real pO₂ /
  temperature / composition levers, including the `SiO₂ ⇌ SiO + ½O₂` pathway). What it does
  **not** include is a silicate-**melt solution model** — no liquidus, melt/solid phase
  fractions, or non-ideal melt activities (`liquid_fraction` is left unsolved). So the
  extraction sequence and product ledger are meaningful under `internal-analytical`; the
  melt-phase numbers are idealized. (Both names resolve to the same backend; runs serialize
  the canonical `internal-analytical` token in `backend_name`.)
- **`--backend alphamelts`**: adds the real MELTS-family **melt solution model** on top of
  that vapor physics (liquidus, phase fractions, non-ideal activities; the diagnostic
  authority). Accurate but slow (~6+ min per equilibrium, and the liquidus search is a
  multi-point bracket/bisect), so a full campaign can take hours; opt-in.
- **`--backend thermoengine`**: the ENKI ThermoEngine MELTS backend as a first-class
  selection (`ThermoEngineBackend`, `real_backend_family = THERMOENGINE`) rather than a
  transport mode underneath `alphamelts`. Unlike the AlphaMELTS path it advertises
  `supports_intrinsic_fO2`. Same cost profile as `alphamelts`, and it needs the native
  ThermoEngine build from `install-engines.py`; without it the backend resolves
  unavailable and the run fails loudly rather than silently downgrading.
- A *fast real-fidelity* path is in progress (the reduced-real MAGEMin cache + `cached-real`
  backend) — the intent is to make the real melt-phase fidelity fast enough to be the
  default.
- `magemin` / `vaporock` are not selectable as the active melt backend from this flag;
  the reduced-real cached path (`cached-real`) is configured in-process via
  `reduced_real_cache`, not the runner flag.
- Reduced-real cache identity uses the deliberate corpus tag in
  `data/corpus_version.yaml`. `corpus_version` is the cache-invalidation lever;
  bump it when the analytical corpus changes. `interoperable_versions` lists
  older tags that are safe to replay under the current corpus. Engine version,
  server, path, and digest remain provenance only; they do not invalidate
  cached-real replay by themselves.

---

## 6. Other runnable entry points

| command | purpose |
|---|---|
| `python -m simulator.optimize --feedstock <id> --strategy {bayes,nsga2,random,screen,staged} --fidelity {internal-analytical,fast,high,auto} --budget N` | Phase-O recipe optimizer (`simulator.optimize.cli` is the same entry point; `--fidelity stub` is still accepted and canonicalises to `internal-analytical`) |
| `python scripts/populate_reduced_real_cache.py --profile <p> --feedstock <id> --campaign <c> --db <path>` | build the reduced-real equilibrium cache from real trajectories |
| `python scripts/cal_threshold_calibration.py --feedstock <id> --campaign <c> --output-dir <d>` | SG-3 vapor yield-threshold calibration (default `--backend alphamelts`; `--allow-internal-analytical` to use the `internal-analytical` model; legacy flag alias `--allow-stub`) |
| `python scripts/vaporock_antoine_shadow_matrix.py` | record alphaMELTS/VapoRock vs Antoine shadow vapor pressures |

---

## Gotchas

- **No root `runner.py`.** Use the module form `-m simulator.runner`. `simulator.runner` is
  a package directory (`simulator/runner/`), which also carries `sio_yield`, `sio_tsweep`
  and `sio_wall_sweep` submodules. Stale copies under `.claude/worktrees/` are not the
  live code.
- **Always `./.venv/bin/python`**, not a bare `python`.
- **Run from the repo root** so `data/` and `engines/` resolve; `--output` is relative to
  the current directory.
- **The default backend is `internal-analytical`** (legacy alias `stub`) — fast, with real Ellingham/Antoine *extraction*
  thermodynamics but **without the silicate-melt solution model**. Pass
  `--backend alphamelts` for the full melt-phase equilibrium (slow). Each run records
  `run_metadata.backend` (plus `backend_status`, `backend_real_active`,
  `backend_authoritative`, `evidence_class`, and `engines_used`) alongside a
  `vapor_pressure_source_report`, so the fidelity used is never hidden. Note the runner
  artifact spells this field `backend`; `backend_name` is the optimizer's EvalSpec field,
  not a run-document key.
- **Failed/refused runs still produce a JSON file and a non-zero exit** — gate on the exit
  code (or `status` field), not file existence.
- **Machine-sensitive goldens regenerate on Studio 1 only.** Engine-touched fixtures
  (runner smoke, sio_yield, coating diagnostic SHA, staged-bakeout / capacity pins) are
  sensitive to `engines/engines.local.toml` + the Studio engine binaries. Laptop regens
  have red-on-gate history (train11). Use `scripts/studio-regen.sh` — it rsyncs a tip-pinned
  worktree to `mac-studio-256-1` with the same config/venv/PATH/ulimit stanzas as
  `~/Repos/studio-ci.sh`, runs the family regenerator there, and pulls **only** the
  regenerated outputs back into the local worktree (no commit/push). Examples:

  ```bash
  scripts/studio-regen.sh --dry-run HEAD coating
  scripts/studio-regen.sh HEAD coating          # pilot / SHA pin
  scripts/studio-regen.sh HEAD runner sio_yield # fixture families
  ```

  Refuses if the local target paths are dirty. Entry points: coating →
  `scripts/regenerate_coating_diagnostic_golden.py`; runner →
  `scripts/regenerate_runner_goldens.py`; sio_yield →
  `python -m simulator.runner.sio_yield` (form in commit `4fce2f0`).
