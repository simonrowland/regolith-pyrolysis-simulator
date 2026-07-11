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

### Common flags (`simulator/runner.py`)

| flag | default | meaning |
|---|---|---|
| `--feedstock` | *(required)* | key from `data/feedstocks.yaml` |
| `--output` | *(required)* | path for the JSON result document |
| `--campaign` | `C0` | campaign / recipe phase (see §4) |
| `--hours` | `24` | simulated hours to advance |
| `--mass-kg` | `1000.0` | batch mass |
| `--backend` | `internal-analytical` | `internal-analytical` (legacy alias `stub`) or `alphamelts` (see §5) |
| `--track` | `pyrolysis` | or `mre_baseline` |
| `--additive` | *(none)* | repeatable, e.g. `--additive=C=30` |
| `--engine` / `--engines` | *(none)* | per-intent engine override / config YAML |
| `--started-at-utc`, `--kernel-commit-sha` | *(none)* | determinism pins for golden fixtures |

### Output document

Top-level keys (schema pinned by [`docs/runner-output-schema.md`](runner-output-schema.md)):
`schema_version`, `run_metadata`, `final_state`, `per_hour_summary`,
`stage_purity_report`, `vapor_pressure_source_report`, `shuttle_refusal_history`,
`shadow_trace`, `status`, `reason`, `error_message`.

**Check the exit code, not just the file:** a failed or refused run still writes a full
JSON envelope (`status: "failed"` / `"refused"`) and exits non-zero.

---

## 2. The product ledger in three lines (in-process)

For the North-Star "pot of dirt → products" view, `simulator.three_product_runner.run`
returns the classified product ledger directly:

```python
from simulator.three_product_runner import run
ledger = run(feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=24)
# dict: metals_plus_O2, pure_silica_glass, industrial_mixed_glass,
#       refractory_ceramic_rump, unclassified
```

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

**Feedstocks** (`data/feedstocks.yaml`): `lunar_mare_low_ti`, `lunar_mare_high_ti`,
`lunar_highland`, `lunar_pkt_kreep_average`, `lunar_spa_kreep_influenced`,
`targeted_super_kreep_ore`, `s_type_asteroid_silicate`, `m_type_metallic_phase`,
`m_type_silicate_phase`, `v_type_vesta_hed`, `e_type_enstatite_aubrite`,
`ci_carbonaceous_chondrite`, `cm_carbonaceous_chondrite`, `ceres_regolith`,
`comet_nucleus`, `mars_basalt`, `mars_sulfate_rich`, `mars_phyllosilicate_clay`,
`mars_perchlorate_rich`.

**Campaigns** (`data/setpoints.yaml`): `C0`, `C0b_p_cleanup`, `C2A_continuous`,
`C2A_staged`, `C2B`, `C3`, `C4`, `C5`, `C6`, `mre_baseline`. The session layer also
accepts the alias `C2A` → `C2A_continuous`.

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
  the stable `stub` token in `backend_name`.)
- **`--backend alphamelts`**: adds the real MELTS-family **melt solution model** on top of
  that vapor physics (liquidus, phase fractions, non-ideal activities; the diagnostic
  authority). Accurate but slow (~6+ min per equilibrium, and the liquidus search is a
  multi-point bracket/bisect), so a full campaign can take hours; opt-in.
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
| `python -m simulator.optimize.cli --feedstock <id> --strategy {bayes,nsga2,random,screen,staged} --fidelity {stub,fast,high,auto} --budget N` | Phase-O recipe optimizer |
| `python scripts/populate_reduced_real_cache.py --profile <p> --feedstock <id> --campaign <c> --db <path>` | build the reduced-real equilibrium cache from real trajectories |
| `python scripts/cal_threshold_calibration.py --feedstock <id> --campaign <c> --output-dir <d>` | SG-3 vapor yield-threshold calibration (default `--backend alphamelts`; `--allow-internal-analytical` to use the `internal-analytical` model; legacy flag alias `--allow-stub`) |
| `python scripts/vaporock_antoine_shadow_matrix.py` | record alphaMELTS/VapoRock vs Antoine shadow vapor pressures |

---

## Gotchas

- **No root `runner.py`.** Use the module form `-m simulator.runner`. Stale copies under
  `.claude/worktrees/` are not the live code.
- **Always `./.venv/bin/python`**, not a bare `python`.
- **Run from the repo root** so `data/` and `engines/` resolve; `--output` is relative to
  the current directory.
- **The default backend is `internal-analytical`** (legacy alias `stub`) — fast, with real Ellingham/Antoine *extraction*
  thermodynamics but **without the silicate-melt solution model**. Pass
  `--backend alphamelts` for the full melt-phase equilibrium (slow). Each run records its
  `backend_name` + a `vapor_pressure_source_report`, so the fidelity used is never hidden.
- **Failed/refused runs still produce a JSON file and a non-zero exit** — gate on the exit
  code (or `status` field), not file existence.
