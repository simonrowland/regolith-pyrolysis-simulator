# B2 — thomas-2022-chlorine-bonding-silicate-melts bench registry (t-966)

**Lane:** BENCH registry B2  
**Branch:** `empirical/b2-thomas-2022-bench-2026-09-22`  
**Base:** `origin/work-v064-green` (`2e9e17c3d`)  
**Tip:** `cdf80a4e7f6d4984a1c5c0db2431dfdb0f7296a8`  
**Commit:** `cdf80a4e7` — extracts: land Thomas 2022 piston-cylinder bench registry for 43 quench-glass runs  
**Push:** origin/`empirical/b2-thomas-2022-bench-2026-09-22` (clean; extract YAML only; PDF not committed)  
**Source:** `data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml`  
**PDF (private, not committed):** `/workspace/batch-z/pdfs/thomas-2022-chlorine-bonding-silicate-melts.pdf`

## What landed

Top-level `benches:` / `experiments:` registry patterned on Rusiecka–Wood 2025 / SCHEMA Furukawa example. One instrument + one series experiment covering the 43 tabulated run IDs (Tables 1–2). Per-run T / P / duration / log fO2 stay in observation rows (no invented midpoints).

| Registry | Local id | Printed facts used |
|---|---|---|
| Bench | `oxford-piston-cylinder` | End-loaded Boyd-and-England-type ½″ piston-cylinder at Oxford (§2.1, pub. p. 121270); graphite and Pt capsules, total thickness &lt;2 mm (§2.2, pub. p. 121274); CAMECA SX-Five-FE EPMA; Ag+AgCl+AgI fCl2 buffer; pressure range 5–20 kbar |
| Experiment | `quench-glass-series` | `method: quench_equilibration`; sealed-capsule form; thermal span 1300–1400 °C → 1573.15–1673.15 K and durations 1–3 h → 3600–10800 s (unit conversion only); quench to glass |

Observation FKs (`experiment: quench-glass-series`): table1 EPMA compositions, table1 Cl concentrations, table2 conditions/XAF context, apparatus context. Left unlinked: author chloride-capacity model, salt-standard XAFS, qualitative conclusions.

## Typed absences (not invented)

| Topic | Treatment | Why |
|---|---|---|
| Heating / furnace type | `heating_method: unknown / not_published` | Paper only says heated above the liquidus; no graphite-furnace / heater type in this work (do not borrow Rusiecka) |
| Sweep / gas flow / atmosphere | `sweep_gas` + `regime_class: not_applicable` | Sealed HP capsules; no carrier/sweep gas printed |
| Series T / P | `conditions.temperature_K` and `total_pressure_Pa: unknown` | 43 distinct Table 2 setpoints (0.5–2.0 GPa, 1300–1400 °C); no single series value |
| Sample mass | `mass_kg: unknown` | Not printed |
| Orifice geometry | `not_applicable` | Not a Knudsen cell |
| fO2 channel / assembly | `channel: unknown`; buffer text = “controlled oxygen fugacity; exact buffer assembly not stated in this paper” | Per-run log fO2 already printed in Table 2; assembly not printed here |

## Validation

```
python tools/validate_literature_extracts.py data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml
```

**Before = after: 3 errors** (pre-existing; no new errors gained):

1. absolute `extraction.provenance_path`
2. scored-container `model_comparison` type on the author regression observation
3. fidelity_samples path pins `context[...]` rather than `observations[...]`

Battery `bench_from_plain` / `experiment_from_plain` parse of the new blocks succeeds with injected `work_id`.

## Notes / remaining gaps for engine_point

- Bench waypoint gap closed at extract level (registry + FKs present).
- Series-level T/P remain typed unknown; consumers that need per-run conditions must read Table 2 observation rows (already carry printed log fO2).
- Exact oxygen-buffer assembly still absent in this paper (same as pre-existing extract note); O1 C–CO route does not apply without a printed graphite/C–CO statement + P_CO here.
- Glasses are from Thomas and Wood (2021, 2022); apparatus description used here is the prose printed in *this* Chem. Geol. paper (`identity.basis: described_in_this_work`).
