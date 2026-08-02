# CI vs local golden / pin divergence

**Doctrine (5c6c015):** CI is the truth surface for machine-sensitive
engine-routed goldens and code pins. Laptop-regenerated fixtures for those
paths go red on the studio gate. Always regenerate under the studio CI engine
config via `scripts/studio-regen.sh` (mirrors `studio-ci.sh`: rsync, grind
`engines.local.toml`, venv, PATH/ulimit).

This document records which train11-class targets, after a studio-config
regeneration at tip `0de9c6d`, are **expected-divergent on a laptop** when the
committed goldens/pins match the studio grind overlay.

## Why values differ

The studio grind engine overlay (MAGEMin / ThermoEngine paths and related
routing) is not bit-identical to a typical laptop engine config. Engine-routed
campaign outputs (C0/C2A SiO yield, runner fixtures, coating diagnostic SHA,
staged bakeout Stage-3 capture, SiO chain/step pins) therefore land at
slightly different numbers. Relative moves observed in this regen are ppm to
sub-percent — not wild physics flips — and passed the honesty gate in
`scripts/emit_studio_pin_values.py`.

**CI is authoritative.** A green laptop against laptop-regenerated goldens is
not a gate pass; the studio gate is.

## Open design item (do not implement here)

A future **config-keyed pin** design (e.g. separate pin tables keyed by engine
config identity, or dual goldens) could restore dual-green (laptop + CI)
without treating either surface as fake. That is intentionally **not** built
in this change; pins and fixtures track the CI surface only.

## Studio regen at tip `0de9c6d` (2026-08-02)

Harness: `scripts/studio-regen.sh 0de9c6d coating runner sio_yield cache pins`

### Honesty gate

| Pin | Laptop (pre-regen) | Studio | Rel Δ | Verdict |
|-----|--------------------|--------|-------|---------|
| capacity total_kg_hr | 2.6213753068336443 | identical | 0 | no patch needed |
| capacity transport_sat % | 1161978.521915791 | identical | 0 | no patch needed |
| capacity melt_mass_kg | 997.3707383784229 | identical | 0 | no patch needed |
| sio_evolved_kg | 1.03187282595e-05 | 1.03186545664e-05 | −7.1e-6 | regen |
| sio_stage3_silica_kg | 6.73119341581e-06 | 6.73114533926e-06 | −7.1e-6 | regen |
| sio_wall_deposit_1050_kg | 4.439481519259e-06 | 4.439448640582e-06 | −7.4e-6 | regen |
| staged_silica_kg | 0.10262754045817979 | 0.10246923985526701 | −1.5e-3 | regen |
| staged_product_sio_kg | 0.011456288948428558 | 0.01143878479198185 | −1.5e-3 | regen |

`finding_count = 0` (no wild-magnitude / physics-incoherent moves).

### Expected divergent on laptop after studio regen

Recorded with `pytest -n0` on the local machine after installing studio
outputs (11 failed, 35 passed in the focused set).

| Test node | Family | Why engine-routed |
|-----------|--------|-------------------|
| `tests/test_coating_rate.py::test_coating_diagnostic_default_output_is_byte_identical_to_golden` | coating | Full C0 run SHA depends on engine melt path |
| `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[lunar_mare_low_ti_C0_24h]` | runner | Fixture regenerated under studio engines |
| `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[mars_basalt_C2A_12h]` | runner | Fixture regenerated under studio engines |
| `tests/test_sio_yield_regression.py::test_sio_yield_cli_matches_golden[lunar_mare_low_ti-lunar_mare_low_ti_c2a.json]` | sio_yield | CLI report vs studio fixture |
| `tests/test_sio_yield_regression.py::test_sio_yield_cli_matches_golden[mars_basalt-mars_basalt_c2a.json]` | sio_yield | CLI report vs studio fixture |
| `tests/test_recipe_io.py::test_no_recipe_run_matches_committed_golden_text` | recipe_io | Binds lunar runner fixture text |
| `tests/test_cost_ledger.py::test_cost_rollup_metadata_is_golden_neutral_for_runner_fixture` | cost_ledger | Binds lunar runner fixture metadata |
| `tests/test_staged_bakeout.py::test_c2a_staged_is_deterministic_and_keeps_sio_stage_capture` | staged_bakeout | Stage-3 silica / product SiO pins |
| `tests/chemistry/test_sio_chain_coherence.py::test_sio_evolved_is_invariant_to_wall_temperature_at_fixed_po2_mode` | sio pins | `PHASE3BIS_SIO_EVOLVED_KG` |
| `tests/chemistry/test_sio_step_condensation.py::test_subfloor_sio_does_not_create_unmaterialized_stage3_product` | sio pins | Stage-3 silica pin |
| `tests/chemistry/test_sio_step_wall_deposit.py::test_wall_deposit_is_rebaselined_after_corrected_hkl_mass_flux` | sio pins | Cold-liner wall deposit pin |

**Locally-divergent count: 11** (test nodes).

### Not expected-divergent on laptop (same focused run)

| Test / artifact | Note |
|-----------------|------|
| `tests/test_capacity_coupling.py::test_default_off_preserves_hot_fe_redox_split_head_result` | Head-result trio bit-identical studio ↔ laptop at this tip |
| `tests/test_cache_convert.py::test_cache_identity_golden_matches_executable_generators` | Dictionary/schema golden; not engine melt-routed |
| `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[ci_carbonaceous_chondrite_C2B_12h]` | Studio pullback reported UNCHANGED; local green |

## Operator procedure

```bash
# regenerate under CI engine config (never laptop for engine-routed families)
scripts/studio-regen.sh HEAD coating runner sio_yield cache pins

# optional dry-run of the plan only
scripts/studio-regen.sh --dry-run HEAD coating runner sio_yield cache pins
```

After pullback, patch any code pins listed in `studio-pin-report.json` with the
doctrine comment:

```text
# regenerated under CI engine config (studio grind overlay) per the 5c6c015
# doctrine; laptop-config value was <old>
```

Then stage explicit paths and commit separately. The harness never commits.

## Train12 round 3 (2026-08-02) — serialrerun 14-node set @ tip `1a6ad25`

Serial rerun (`/tmp/ci-train12-serialrerun.xml` on Mac-Studio-256-1) listed
14 nodes as persistent studio-divergent. Investigation under the grind
`engines.local.toml` found a **configuration defect**, not 14 independent
pin drifts:

### Root cause (infra)

`~/repos/regolith-grind-0a3d5e9/engines/engines.local.toml` pointed
`magemin_binary_path` at a **laptop Dropbox path that does not exist on the
studio**. `configured_magemin_binary_path()` returned `None` → freeze-gate
liquidus unavailable → Kress91 floor fallback (`lf = 0` for T ≤ 1200 °C).
That zeroed C3 redox capacity at 1150 °C, halved SSO-R `native_fe_pool_mol`
(788 vs 1576), zeroed `native_split_o2_mol`, and shifted wall-Fe residual
paths.

**Repair (studio host, not repo content):** rewrite grind
`magemin_binary_path` to
`/Users/simonrowland/repos/regolith-grind-0a3d5e9/engines/magemin/bin/MAGEMin`
(backup: `engines.local.toml.bak-2026-08-02-studio-regen3`).
`scripts/studio-regen.sh` now fails loud if the toml path is missing.

### Per-node disposition after MAGEMin path repair

| File / nodes | Serialrerun under broken path | After repair | Action |
|---|---|---|---|
| `tests/test_yield_root_cause.py` (na2o pin) | 4.106 vs 4.094 | **PASSED** (laptop pin holds) | no pin repin |
| `tests/test_sso_r_r20_state.py` ×2 | capacity=0 at 1150 | **PASSED** (lf≈0.51 via magemin-shadow) | no pin repin |
| `tests/test_sso_r_validation_map.py` ×5 | half pool / split_o2=0 / first_T=None | **PASSED** (pool 1576, split_o2≈787, first_T=1600) | no golden regen |
| `tests/test_condensation_parallel_determinism.py` ×3 | `vapour_rail_catalog_missing` | **PASSED** after schema_v2 fixture fix | fixture repair (not pin) |
| `tests/chemistry/test_builtin_condensation_route_provider.py` wall Fe ×3 | Fe pins diverged | **PASSED** all 3 under fixed MAGEMin (`--timeout=7200`, 2h35m) | no pin repin |

### Condensation parallel fixture repair (shipped in this change)

Legacy synthetic `metals`/`oxide_vapors`-only vapor_pressures no longer
compile a VR-11 catalog (`schema_version != 2` →
`vapour_rail_catalog_missing` on `advance()`). Fix:

1. Load production `data/vapor_pressures.yaml` (schema_version 2).
2. Restore pre-route melt temperature after the forced cold-wall tick so
   multi-tick `advance()` does not inherit 1700 °C pure-SiO2 evaporation.

Wall-attribution pin `sio_flux_mol_m2_s = 0.062205888833975716` is
**unchanged** under studio (matches laptop).

### Honesty gate (train12 round 3)

Under liquidus-unavailable contamination the SSO-R half-pool and stage-3 Fe
wt% jump (0.012 → 32) would have been **FINDINGs**, not repins. After the
MAGEMin path repair those values return to the committed anchors — the
serialrerun divergence was env contamination, not CI-truth physics.

### Operator note

Before any studio gate or studio-regen run, confirm:

```bash
ssh mac-studio-256-1 'test -x "$(python3 -c "
from pathlib import Path
import re
t=Path.home()/\"repos/regolith-grind-0a3d5e9/engines/engines.local.toml\"
m=re.search(r\"magemin_binary_path\\s*=\\s*\\\"([^\\\"]+)\\\"\", t.read_text())
print(m.group(1) if m else \"\")
")" && echo MAGEMIN_OK || echo MAGEMIN_BROKEN'
```
