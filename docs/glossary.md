# Glossary

Alphabetised one-line definitions for project-specific terms. Standard thermodynamic and mineralogical terms (Ellingham, liquidus, HKL) are included only where the project usage is specific enough to warrant a note.

---

**AlphaMELTS** — silicate equilibrium engine based on the MELTS thermodynamic framework; accessed via PetThermoTools or the `engines/alphamelts/run_alphamelts.command` binary. Authoritative for silicate liquidus diagnostics; diagnostic-only (no ledger mutation). See [`docs/melt-backends.md`](melt-backends.md).

**AtomLedger** — the canonical mol-native store for all simulator state (`simulator/accounting/ledger.py`). Every balance sheet entry is in mol. Kg numbers are external projections only. See [`docs/architecture.md`](architecture.md).

**Bakeout** — volatile recovery hold at moderate temperature (C0 campaign, 20–950 °C, hard vacuum). Removes H₂O, CO₂, S₂, CHNOPS. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**Branch One** — post-C4 route that skips selective Mg pyrolysis and runs full-scope MRE (C5 at max 2.5 V); higher electrical energy (2650–4050 kWh/t) and shorter electrode life (2–3×) vs Branch Two. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**Branch Two** — preferred post-C4 route: C4 Mg pyrolysis + limited C5 MRE (max 1.6 V) + C6 Mg thermite. Lower electrical energy (1200–2000 kWh/t), electrode life 5–10× vs Branch One. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C0** — volatile bakeout and nanophase Fe⁰ separation campaign. Mandatory for all feedstocks. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C0b** — optional mild oxidative P-cleanup hold (1180–1320 °C, pO₂ 3–15 mbar). Recommended default for all lunar feedstocks. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C2A** — alkali / SiO / Fe co-extraction campaign family; two variants: `C2A_continuous` (single adaptive ramp, Path A default) and `C2A_staged` (discrete holds with cool Na FeO-cleanup stage). Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C2B** — pO₂-managed Fe pyrolysis (Path B); preserves CMAS glass in the melt. Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C3** — legacy alkali metallothermic polish campaign. Under the V1c JANAF refit, K/FeO is refused in the practical melt window and Na/FeO survives only as a cool ~1150 °C cleanup; Cr/Ti targets are refused at C3 temperatures. Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C4** — selective Mg pyrolysis campaign (Branch Two preferred). Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C5** — limited MRE under O₂ backpressure; Branch Two max 1.6 V targeting SiO₂ → Si metal. Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**C6** — Mg thermite reduction (3Mg + Al₂O₃ → 3MgO + 2Al); V1c keeps the equilibrium default below the ~1573 °C Mg/Al crossover, with hotter operation requiring a kinetic/local-heating justification. Source: `data/setpoints.yaml`. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**Cleaned melt** — silicate-only melt after Stage 0 removes volatiles, salts, native metals, halides, sulfates, and perchlorates. The input to the C1–C6 extraction sequence. Corresponds to `process.cleaned_melt` in the `AtomLedger`. See [`docs/process-model.md`](process-model.md).

**CMAS glass** — Ca–Mg–Al–Si silicate glass preserved in the melt when SiO₂ is not extracted (Path B or early-tap). A product of the industrial-glass mode. See [`docs/recipe-playbook.md`](recipe-playbook.md).

**Condensation train** — staged condenser array downstream of the furnace duct: Stage 0 hot duct (1400–1600 °C), Stage 1 Fe condenser (1100–1400 °C), Stage 2 Cr oxide harvester (1100–1300 °C), Stage 3 SiO/silica zone (900–1200 °C), Stage 4 alkali/Mg cyclone (350–700 °C), Stage 5 dust filter, Stage 6 turbine-compressor, Stage 7 O₂ accumulator. Source: `data/setpoints.yaml` §2.

**Ellingham** — the underlying Ellingham–Richardson plot (ΔG_f° of oxides per mol O₂ vs T). In this project the word is used in two distinct senses: (1) the **oxygen-affinity ladder** read at fixed T, which tells you which metal can reduce which oxide (the operative concept for the alkali shuttle and C6 thermite); and (2) the **pressure-modified Ellingham**, which tells you how each oxide's dissociation threshold shifts under non-standard pO₂ with a species-specific slope `−1/n_M` (the operative concept for evolution at millibar / microbar / nanotorr overhead pressures). Evolution order is *not* read directly off either Ellingham sense — it is `P_eff = a_M × P_sat`, where Sense 2 sets `a_M` and the Antoine equation sets `P_sat`. See [`docs/concepts.md`](concepts.md) §"Two senses of 'Ellingham' in this project".

**FactSAGE / ChemApp** — multiphase equilibrium engine (strict-config authoritative for full gas/metal/slag equilibrium). Requires a licensed local install and a user-exported `.cst`/`.dat` datafile. Stays diagnostic-only without a strict config. See [`docs/melt-backends.md`](melt-backends.md).

**Freeze-gate** — evaporation flux multiplier on `liquid_fraction(T)` that prevents sub-solidus evaporation. Currently default OFF (`freeze_gate.enabled: false` in `data/setpoints.yaml` §15); the default-on flip is gated on a milestone review of the blast-radius categorization (see `docs-private/`). See `data/setpoints.yaml:1076`.

**HKL (Hertz-Knudsen-Langmuir)** — the evaporation flux equation relating surface vapor pressure, temperature, and molecular weight to a kinetic evaporation rate. Implemented in `simulator/evaporation.py`. The simulator evaluates HKL once per tick at tick-start conditions and integrates depletion analytically within the tick.

**Hot wall** — upstream pipe and duct maintained above ~1400 °C to prevent premature condensation before vapor reaches its designated condenser stage. The design invariant that makes directional extraction possible. See [`docs/concepts.md`](concepts.md).

**Knudsen number (Kn)** — `λ / L` where λ is mean-free-path and L is pipe diameter. Must be ≪ 0.01 (viscous-flow regime) for directional vapor transport. The 5–15 mbar pN₂ band is calibrated to maintain this. See [`docs/concepts.md`](concepts.md).

**Kress91** — the fO₂-coupled Fe³⁺/Fe²⁺ melt redox model (Kress & Carmichael 1991). Not yet implemented; Fe²⁺/Fe³⁺ partitioning is a diagnostic estimate only in current builds. See [`docs/model-limitations.md`](model-limitations.md).

**Liquidus** — temperature above which a melt is fully liquid; the upper boundary of the mush region. Below the liquidus, crystallisation begins on cooling. Relevant to the freeze-gate and to the C6 self-terminating criterion (liquidus > 1700 °C when residual SiO₂ + Al₂O₃ < 15–20 wt%).

**MAGEMin** — open-source Gibbs free-energy minimiser for silicate phase equilibria; shadow-only for `SILICATE_LIQUIDUS` and `SILICATE_EQUILIBRIUM`. Does not hold ledger authority. See [`docs/melt-backends.md`](melt-backends.md).

**MELTS** — thermodynamic framework for silicate melt and phase equilibrium; accessed via ThermoEngine or PetThermoTools. The activity convention is `a_i = exp((μ_i − μ_i0) / RT)`. See [`docs/melt-backends.md`](melt-backends.md).

**MRE** — molten regolith electrolysis; applies Nernst + Faraday electrolysis to reduce melt oxides at voltages from 0.6 V (FeO) to 2.5 V (CaO). The `MRE_BASELINE` track models full electrolysis without pyrolysis pretreatment. See [`docs/process-model.md`](process-model.md).

**MRE_BASELINE** — runner track for full molten regolith electrolysis without pyrolysis pretreatment; the comparison point for quantifying what pretreatment saves. Invoked via `--track=mre_baseline`.

**Mush** — partially molten temperature region between solidus and liquidus where melt and crystals coexist. Relevant to freeze-gate behavior and melt viscosity estimates.

**Overhead** — gas headspace above the melt; carries total pressure, partial pressures, and sweep gas composition. The `overhead_headspace.enabled` toggle (default OFF) controls whether evaporation O₂ is routed through `process.overhead_gas` before bleeding to terminal accounts. See `data/setpoints.yaml:1032`.

**pN₂** — sweep gas partial pressure (canonical symbol — N₂, Ar, or CO₂ on Mars feedstocks); controls viscous-flow transport. Target band 5–15 mbar. See [`docs/concepts.md`](concepts.md).

**pO₂** — oxygen partial pressure; the control lever for the SiO₂ ⇌ SiO + ½O₂ equilibrium and for selective oxide reduction via the Ellingham ladder. See [`docs/concepts.md`](concepts.md).

**PetThermoTools** — Python API for alphaMELTS-family calculations; the preferred Python-side bridge to MELTS thermodynamics. Installed as an editable sibling clone by `install-engines.py`. See [`docs/melt-backends.md`](melt-backends.md).

**PySulfSat** — optional Python package for sulfur saturation calculations (SCSS via Smythe 2017, SCAS via Chowdhury & Dasgupta 2019). Gates the Stage 0 sulfur-saturation split when installed. See [`docs/process-model.md`](process-model.md).

**Residual floor** — the mass of melt that cannot be evaporated at survivable temperature; the physical source of the refractory ceramic rump. Ca, REE, TiO₂, and residual Al₂O₃ define the floor. See [`docs/concepts.md`](concepts.md).

**Rump** — terminal refractory ceramic residue after full extraction sequence: Ca-rich, Al-residual, REE (0.5–1.0 wt%), Ti. Approximately 10–15 kg per tonne for a low-Ti mare feedstock after C6. Not a recipe choice — a physical floor from oxide stability. See [`docs/concepts.md`](concepts.md).

**Shuttle** — Na/K loop that reduces target oxides metallothermically (K + FeO → K₂O + Fe; Na + FeO → Na₂O + Fe) and recycles alkali back into the melt as O₂ is baked out. Dual role: oxygen reductant and selectivity tool. See [`docs/concepts.md`](concepts.md).

**SiO suppression law** — `p(SiO) = K(T) × a(SiO₂) / √pO₂`; suppression factor ~300× conservative at 1 mbar pO₂ vs hard vacuum. Source: `data/setpoints.yaml:940`.

**Solidus** — temperature below which a melt is fully crystalline on cooling; the lower boundary of the mush region.

**Stage 0** — pretreatment cycle (C0 ± C0b) that converts raw feedstock to a cleaned silicate melt. Removes volatiles, halides, sulfates, perchlorates, organics, native metals, and sulfides. See [`docs/process-model.md`](process-model.md).

**StubBackend** — the always-available fallback melt backend using the builtin Ellingham/Antoine path; selected when neither AlphaMELTS nor FactSAGE are configured. See [`docs/melt-backends.md`](melt-backends.md).

**ThermoEngine** — ENKI's Python MELTS API providing first-class melt activities via compiled Objective-C/C dylibs. Required by VapoRock. Installed by `install-engines.py` (macOS arm64 only). See [`docs/melt-backends.md`](melt-backends.md).

**VapoRock** — silicate-melt evaporation speciation package; vapor-pressure authority for the `VAPOR_PRESSURE` kernel intent when installed. Rides ThermoEngine. Not eligible as the active `MeltBackend`; operates as a `ChemistryProvider` at the kernel level. See [`docs/melt-backends.md`](melt-backends.md).

**Viscous-flow regime** — gas transport condition where Kn ≪ 0.01 and molecules follow bulk flow toward condensers. Maintained by the 5–15 mbar pN₂ band. Prerequisite for directional vapor transport. See [`docs/concepts.md`](concepts.md).

**Wall deposit** — species condensing on cold walls (upstream piping, cold spots) instead of reaching designated condenser stages; tracked per-species in `wall_deposit_kg`. SiO is the worst offender. See [`docs/concepts.md`](concepts.md).
