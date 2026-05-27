# Recipe Playbook

This document covers the campaign catalog, how to choose and configure a recipe, and three worked workflows. For the physics behind the levers, see [`docs/concepts.md`](concepts.md). For the output fields produced by a run, see [`docs/output-interpretation.md`](output-interpretation.md).

## The campaign catalog

All setpoints live in `data/setpoints.yaml`. The campaigns are listed in extraction order; a batch normally runs C0, then one or more later campaigns in sequence.

### C0 — Vacuum Bakeoff & Volatile Recovery

**Temperature**: 20–950 °C, 50 °C/hr ramp.  
**Atmosphere**: hard vacuum.  
**Target species**: H₂O, CO₂, S₂, CHNOPS.  
**Endpoint**: IR signal decay to <5 % of peak for all target species.

Removes solar-wind-implanted volatiles and separates nanophase Fe⁰ by magnetic tapping (zero additional energy cost). Volatile release is complete by ~700 °C; alkali onset is >900 °C, thermally separated from the volatile window. C0 is mandatory for all feedstocks. For carbonaceous or Mars feedstocks the Stage 0 profile is more complex (see `data/feedstocks.yaml` per-feedstock `stage0_profile` keys and [`docs/feedstocks.md`](feedstocks.md)).

Source: `data/setpoints.yaml` lines 19–43.

### C0b — P-Cleanup (Mild Oxidative Hold)

**Temperature**: 1180–1320 °C, IR-endpoint controlled.  
**Atmosphere**: controlled O₂ flow (pO₂ 3–15 mbar); suppresses Na/K/SiO.  
**Target species**: PO, POₓ.  
**Typical duration**: 0.5–2.5 h.  
**Endpoint**: P-species IR signal decay to <5 % of peak.

Recommended for all lunar feedstocks as a default; required when P₂O₅ > 0.04 wt% or persistent P IR signal remains after 1050 °C vacuum hold. Captures 85–98 % of phosphorus as clean Ca/Mg/alkali phosphates in a 350–650 °C trap. The controlled-O₂ cover suppresses alkali and SiO evolution during this hold, so C0b does not consume shuttle stock.

Operator note: beneficiation (magnetic pre-separation) is preferred before C0b to avoid absorbing O₂ into metallic Fe. Cool back to hard vacuum after C0b before starting C2A.

Source: `data/setpoints.yaml` lines 45–73.

### C2A_continuous — Continuous Adaptive pN₂ Ramp (Path A default)

**Temperature**: 1050–1600 °C, adaptive ramp.  
**Atmosphere**: recirculating pN₂ sweep (5–15 mbar total, 8–12 mbar optimal); Fe-granule sorbent holds pO₂ < 1×10⁻⁸ bar.  
**Target species**: Na, K, Fe, SiO, CrO₂.  
**Typical duration**: 18–28 h.  
**Endpoint**: Na/K/Fe signals decay to <5 % peak; SiO signal reaches target a(SiO₂) reduction.

This is the preferred default. A single continuous solar-thermal ramp meters dT/dt to keep instantaneous outgassing flux ≤ 60–80 % of hot-wall pipe capacity, monitored by Stage 0 IR (Na 589 nm early, Fe 370 nm mid, SiO 230 nm UV dominant above 1400 °C). Co-extracts Na, K, Fe, and SiO in a single ramp without discrete atmosphere switches.

Downstream effects: C3 shuttle scope is halved (0–1 K cycle + 1 Na cycle); C5 MRE scope is reduced 20–40 %; overall batch cycle is 8–20 % shorter than Path B.

Product yields for a typical `lunar_mare_low_ti` batch: Na 2.3–4.0 kg, K 0.8–1.35 kg, Fe 85–130 kg, SiO₂ glass 100–160 kg, O₂ 48–70 kg.

Source: `data/setpoints.yaml` lines 79–138.

### C2A_staged — Staged pN₂ Bakeout (Path A staged variant)

**Temperature**: 1250–1750 °C (furnace ceiling 1800 °C).  
**Atmosphere**: pN₂ sweep, same as C2A_continuous.  
**Target species**: Na, K, SiO, Fe (in sequence).  
**Default hold temperature**: 1750 °C.

An alternative to C2A_continuous that uses explicit stage holds for sequential product collection rather than a single adaptive ramp. The four stages are:

1. **alkali_early_fe** — ramp to 1250 °C, 600 °C/hr, 4 h: Na and K.
2. **sio_window** — ramp to 1600 °C, 175 °C/hr, 3 h: SiO with minor Fe co-evolution.
3. **fe_hot_hold** — ramp to 1750 °C, 150 °C/hr, 1 h: Fe thermal depletion to ~88 % (residual FeO ~20–24 kg).
4. **cool_for_na_shuttle** — cool to 1150 °C before C3_NA (Na-only) shuttle dose. The K shuttle is no longer used at this stage under V1c-JANAF Ellingham: K/Fe crossover dropped to ~832 °C, well below any practical melt T, so engine refuses K→FeO reduction. C2A_staged now advances to C3_NA (not C3_K) and uses Na as the sole reductant in the cool window where Na/Fe margin is still positive (Na/Fe crossover @ 1173 °C).

**Operator knobs** (via session script):

| Knob | Default | Command form | Role |
|---|---|---|---|
| `hold_temp_C` | 1750 | `adjust campaign_override C2A_staged hold_temp_C <C>` | Cycle-time lever; bounds 1650–1800 °C |
| `Na_additive_kg` | 12 | `start --additive=Na=<kg>` | Yield-ceiling lever for cool Na shuttle |

Note on `hold_temp_C`: this is a cycle-time lever, not a thermal-yield ceiling breaker. Running hotter within the 1650–1800 °C bounds increases the thermal Fe extraction fraction but does not remove the physical FeO floor that requires the shuttle.

**Engine policy (post-V1c)**: the shuttle T-acceptance gate (S1b) refuses any K→FeO reduction (margin negative everywhere in practical melt T per V1c-JANAF) and refuses Na→FeO above the 1173 °C crossover. The C2A_staged cool window @ 1150 °C is the only physically defended Na-shuttle T; the engine reports `status="refused"` with structured diagnostic if the operator overrides T above the crossover. K added via `--additive=K=<kg>` will not produce Fe from the shuttle — operators wanting K product should expect it from C2A_continuous evaporation only, not C3.

Source: `data/setpoints.yaml` lines 139–248.

### C2B — pO₂-Managed Fe Pyrolysis (Path B)

**Temperature**: 1320–1480 °C.  
**Atmosphere**: controlled O₂ (pO₂ 0.8–2.3 mbar, ramped with temperature).  
**Target species**: Fe.  
**Use when**: CMAS glass preservation is required (structural glass or terminal ceramic recipes).

Path B deliberately holds pO₂ high enough to suppress SiO while still extracting Fe. This preserves the CMAS (Ca–Mg–Al–Si) glass in the melt for direct tapping as an industrial glass product. The tradeoff: no SiO extraction, full C3 scope required downstream, and an overall cycle 8–20 % longer than Path A.

Source: `data/setpoints.yaml` lines 251–277.

### C3 — Na/K Metallothermic Polish

**Temperature**: inject 1200–1350 °C, bakeout 1520–1680 °C.  
**Atmosphere**: Fe-granule sorbent + precision O₂ micro-bleed (pO₂ 0.5–1.5 mbar bakeout).  
**Sequencing**: K-first, then Na (strict; mixed alkali effect reduces diffusion up to 10× at equimolar).

The three-tier shuttle architecture: inject reductant → reduce target oxide → tap metal → pO₂ bakeout → recover reductant. K cycles handle residual Fe and SiO₂ activity conditioning; Na cycles handle Ti and final conditioning.

Cycles after Path A: 0–1 K + 1 Na. Cycles after Path B: 2 K + 2 Na. Na₂O solubility cap in melt is 8–12 wt%.

Source: `data/setpoints.yaml` lines 279–322.

### C4 — Selective Mg Pyrolysis (Branch Two)

**Temperature**: 1580–1670 °C, IR-controlled.  
**Atmosphere**: controlled O₂ (pO₂ 0.08–0.35 mbar).  
**Prerequisite**: SiO₂ activity reduced 30–50 % by C3 conditioning; otherwise SiO co-extraction is 10–40× higher than under pure vacuum.

Mg vapor pressure is ~0.5 bar at 1600 °C, far above any pO₂ setpoint, so once the pO₂ window is opened for Mg it evolves strongly. Product yield: 18–42 kg Mg per tonne for a typical low-Ti mare batch (35–65 % of remaining Mg).

Under the pN₂ variant (pO₂ → 0), Mg can be extracted at 1500–1580 °C at ~0.1 bar vapor pressure, at the cost of uncontrolled SiO co-evolution. Use only when residual SiO₂ is not needed for ceramics.

Branch One fallback: skip C4, electrolyse Mg in C5 at up to 2.5 V. This costs 5–10× electrode life and 2650–4050 kWh/t versus 1200–2000 kWh/t for Branch Two (C4 + C6).

Source: `data/setpoints.yaml` lines 324–363.

### C5 — Limited MRE Under O₂ Backpressure (Branch Two)

**Temperature**: 1500–1650 °C.  
**Atmosphere**: O₂ backpressure from accumulator (pO₂ 0.01–0.1 bar) — total pyrolysis suppression.  
**Branch Two max voltage**: 1.6 V; targets Si as liquid metal.  
**Electrical energy**: 600–1200 kWh/t (Branch Two).

The MRE voltage sequence proceeds: FeO at 0.6 V (should be pre-depleted), Cr₂O₃/MnO at 0.8–1.0 V, SiO₂ at 1.4 V (primary C5 target; permanently removes the SiO source from the melt), TiO₂ at 1.5 V. To produce Al–Ti alloy in C6 instead of pure Al, stop C5 before the TiO₂ sweep at 1.5–1.6 V and retain TiO₂ for the thermite.

Electrode materials: Ir or Pt-alloy anode, Mo or W cathode. Branch Two extends electrode life 5–10× compared to full-scope MRE.

Source: `data/setpoints.yaml` lines 365–399.

### C6 — Mg Thermite Reduction

**Temperature**: 1500–1700 °C, reaction-controlled.  
**Reaction**: 3Mg(l) + Al₂O₃(melt) → 3MgO(slag) + 2Al(l); ΔG ≈ −110 kJ/mol at 1600 °C.  
**Mg demand**: 50–60 kg/tonne (stoichiometric, for low-Ti mare).  
**Self-terminating criterion**: liquidus exceeds 1700 °C when residual SiO₂ + Al₂O₃ < 15–20 wt%.

Mg rods are injected via a SiC bottom port; buoyancy drives the reaction column. The back-reduction cascade (4Al + 3SiO₂ → 2Al₂O₃ + 3Si) is spontaneous by oxide stability order but separates Al from residual SiO₂ cleanly. Terminal slag from C6 is 10–15 kg/tonne with 0.5–1.0 wt% REE enrichment — synthetic doloma suitable for refractory liners.

Bootstrapping: accumulate 3–6 C4 batches (150–300 kg Mg inventory) before the first thermite run. The net Mg balance for low-Ti mare is positive; for highland, neutral at ≥80 % bakeout recovery.

Source: `data/setpoints.yaml` lines 401–442.

### MRE_BASELINE

Full electrolysis comparison path without pyrolysis pretreatment. Runs the complete MRE voltage sequence from FeO (0.6 V) through CaO (2.5 V). Used to quantify what pyrolysis pretreatment saves in electrical energy, electrode life, and corrosion exposure.

## Operator decision points

The simulator pauses for operator input at certain branch points. In the web UI these appear as decision panels; in the CLI runner, decisions are auto-applied with the recommended default and recorded in the `shadow_trace` array of the output JSON. In the session harness, use `decide <choice>` after an `advance` command returns a `decision_required` frame.

Key decisions:

| Decision type | Options | Default | Note |
|---|---|---|---|
| `PATH_AB` | `A`, `A_staged`, `B` | `A` | C2 path selection |
| Branch selection | `one`, `two` | `two` | C4 Mg pyrolysis or full MRE |
| C6 yes/no | `yes`, `no` | `yes` | Thermite reduction |
| Ti retention in C5 | stop before Ti sweep | operator choice | Produces Al–Ti alloy instead of pure Al |

See [`docs/session-script-protocol.md`](session-script-protocol.md) for the full decision grammar.

## Three example workflows

### Metals-extraction mode (the default)

Objective: maximum metals + O₂ yield from a lunar mare feedstock.

Sequence: **C0 → C0b → C2A_continuous → C3 → C4 → C5 (Branch Two) → C6**

- C0b is recommended for all lunar feedstocks even at low P₂O₅; it adds only 0.5–2.5 h and prevents P carryover into C2.
- C2A_continuous at Path A extracts Na, K, Fe, and SiO in a single ramp; this halves C3 scope and reduces C5.
- C3_NA (Na-only post-V1c) cleans residual FeO at the cool ~1150 °C window; the legacy K-shuttle path is refused by the engine under V1c-JANAF and is no longer used here.
- C4 extracts Mg thermally (18–42 kg/t); accumulate over 3–6 batches before first C6 run.
- C5 Branch Two at max 1.6 V removes Si by electrolysis, permanently eliminating the SiO source from the melt.
- C6 thermite yields 65–80 kg Al/t; terminal slag (10–15 kg/t, 0.5–1 wt% REE) is the refractory ceramic rump.

Expected final-state outputs for a `lunar_mare_low_ti` 1-tonne batch: Fe 85–130 kg, Na 2.3–4.0 kg, K 0.8–1.35 kg, SiO₂ glass 100–160 kg, Mg 18–42 kg, Si 30–100 kg, Al 65–80 kg, O₂ 380–440 kg total, refractory slag 10–15 kg. Source: `data/setpoints.yaml` §11.

### Industrial-glass mode

Objective: preserve the CMAS melt for direct tapping as structural glass or ceramic.

Sequence: **C0 → C0b → C2B (Path B) → partial C3 → early melt tap**

- C2B at pO₂ 0.8–2.3 mbar extracts Fe while holding SiO₂ in the melt.
- A partial C3_NA shuttle cleans residual FeO at the cool window (full 2-cycle scope; Path B does not pre-deplete). The K-shuttle path is not used post-V1c — see §C2A_staged.
- Tap the melt before the SiO release window.
- The result is a Ca–Mg–Al–Si glass with controlled alkali content.

Honest note: the Kress91 ferric/ferrous glass model is not yet implemented; Fe²⁺/Fe³⁺ partitioning in the preserved glass is a diagnostic estimate only. See `docs/model-limitations.md`.

### Ceramic-bootstrap mode

Objective: demonstrate that refractory liner material is available as a by-product of any full-sequence run.

Sequence: any full-sequence run (C0 → C2A → C3 → C4 → C5 → C6) produces the ceramic rump by physics.

For a `lunar_mare_low_ti` batch the C6 terminal slag contains Ca, Al-residual, REE (0.5–1.0 wt%), and Ti, in a ~10–15 kg doloma-equivalent per tonne. This is the natural feedstock for hot-duct liners and crucible refractory. The bootstrap is not a recipe achievement — it is a physical consequence of oxide stability: CaO, residual Al₂O₃, TiO₂, and REE oxides do not vaporise at any temperature the furnace itself survives.

REE-enriched feedstocks (KREEP variants, targeted super-KREEP ore) yield proportionally richer terminal slag. See `data/feedstocks.yaml` for trace element ranges per feedstock.

## Tuning advice

**Hold temperature in C2A_staged** (`hold_temp_C`, default 1750 °C): a cycle-time lever. Running hotter within the 1650–1800 °C bounds shortens the time to reach target Fe depletion but does not raise the extraction ceiling — the C3_NA cool-window shuttle handles the residual FeO floor regardless. Do not expect higher hold temperatures to substitute for the shuttle dose.

**Alkali dose** (`Na_additive_kg`, default 12 kg): the yield-ceiling lever for the cool C3_NA shuttle. The shuttle cannot reduce more FeO than the additive dose permits (subject to the Na₂O solubility cap of ~10 wt% in the melt). Increasing the dose raises the Fe extraction ceiling; decreasing it leaves more residual FeO for C5. (`K_additive_kg` is accepted by the runner but ignored by the post-V1c shuttle gate, which refuses K→FeO at any practical melt T.)

**pN₂ setpoint** (5–15 mbar band): controls viscous-flow regime and transport capacity. The transport limit for the hot-wall pipe at 8–12 mbar is 7–16 g/s SiO. Reducing pN₂ below ~5 mbar risks transitioning into the molecular-flow regime where directional sweep fails and cold-wall fouling increases sharply.

## Diagnostic patterns

- **Rump approaching 10–15 kg**: the refractory floor is being reached after a full C0–C6 sequence on low-Ti mare.
- **`wall_deposit_kg[SiO]` growing fast**: the pN₂ sweep is insufficient or a cold wall is upstream of Stage 3. Check pN₂ setpoint and liner temperature schedule (see `data/setpoints.yaml` §14 `overhead_headspace.liner_temperature_C`).
- **Fe yield plateau around 88–90 % in C2A_staged**: expected thermal extraction limit; residual FeO (20–24 kg) is the shuttle's feed. This is correct behavior, not a failure.
- **Very low non-refractory yields on first run without shuttle dose**: the shuttle is reagent-limited when K/Na additives are not loaded. Confirm `--additive=K=26` and `--additive=Na=12` are present for C2A_staged, or that shuttle stock has accumulated from prior C2 cycles.
