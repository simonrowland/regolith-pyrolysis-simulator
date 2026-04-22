# Oxygen Shuttle Process — Core Context

## Overview
Hybrid pyrometallurgical ISRU process for extracting metals + O₂ from regolith.
Combines: solar-thermal vacuum pyrolysis, controlled-pO₂ selective pyrolysis, alkali metal oxygen shuttle catalysis, limited MRE, and Mg thermite reduction.

**Central insight:** Millibar O₂ backpressure suppresses SiO offgassing >300× (√pO₂ dependence) while metal vapour pressures remain orders of magnitude above the backpressure — selective SiO filter.

## Six-Campaign Ladder (1-tonne batch, lunar mare baseline)

| Campaign | T (°C) | Atmosphere | Target | Method | Electricity |
|----------|---------|------------|--------|--------|-------------|
| C0 | <950 | hard vacuum | Volatiles (CHNOPS) | Thermal bakeoff | None |
| C1 | 1050–1240 | hard vacuum (<5e-9 bar) | Na, K, early Fe | Vacuum pyrolysis | None |
| C2A | 1320–1600 | pN₂ 5–15 mbar (pO₂→0) | Fe + SiO₂ glass | N₂ sweep co-extraction | None |
| C2B | 1320–1480 | pO₂ 0.8–2.3 mbar | Fe (preserves CMAS glass) | pO₂-managed pyrolysis | None |
| C3 | 1200–1680 | pO₂ 0.5–1.5 mbar (bakeout) | Ti, residual Fe, Si conditioning | Na/K metallothermic shuttle | None |
| C4 | 1580–1670 | pO₂ 0.08–0.35 mbar | Mg | Selective pyrolysis | None |
| C5 | 1500–1650 | pO₂ 10–100 mbar (MRE) | Si (± Ti by voltage hold) | Molten regolith electrolysis | 600–1200 kWh/t |
| C6 | 1500–1700 | pO₂ 0.08–0.35 mbar (bakeout) | Al (via Mg thermite) | 3Mg + Al₂O₃ → 3MgO + 2Al | None (solar thermal) |

**Total batch time:** 4–9 days. **Total electrical:** ~1200–2000 kWh/t (Branch Two).

## Decision Tree

```
                   ┌── Path A — Continuous Adaptive pN₂ Ramp (default when SiO₂ extraction desired)
Campaign 2 ────────┤   (seamless C1→C2A metered ramp 1050–1600 °C under recirculating pN₂)
                   │   → Fe + fused silica glass byproduct
                   │   → C3 scope halved, C5 MRE scope dramatically reduced
                   │   → 8–20 % shorter full batch cycle (IR-feedback dT/dt control)
                   │   **Preferred default unless preserving CMAS glass for industrial glass or terminal ceramics recipes**
                   └── Path B (pO₂-managed): Fe + preserved CMAS glass
                       → C3 full scope (use only when glass preservation required)
```
**Path A Implementation Note (Continuous Adaptive pN₂ Ramp)**  
When the process goal is SiO₂ co-extraction as fused-silica glass (most feedstocks, all cases except deliberate CMAS-glass preservation for downstream industrial glass or terminal ceramics recipes), run a single continuous solar-thermal ramp from ~1050 °C (Na/K onset) to 1600 °C under constant recirculating pN₂ (8–12 mbar optimal). Meter temperature rise rate adaptively to keep total vapor flux (especially SiO above 1400 °C) within 60–80 % of hot-wall pipe transport capacity (7–16 g s⁻¹). Control law uses real-time IR spectroscopy (Stage 0 hot-duct: Na 589 nm → K 766 nm → Fe 370 nm → SiO 230 nm UV) plus pressure feedback. Induction stirring throughout. This eliminates all transition overhead, maximises recovery, and delivers the full downstream benefits of aggressive a(SiO₂) reduction.

```
                   ┌── Branch Two (preferred): C4 Mg pyrolysis + C6 Mg thermite
Campaign 4+ ───────┤   MRE ≤1.6V, ~1200–2000 kWh/t, electrode life 5–10×
                   └── Branch One (fallback): Skip C4, MRE to 2.5V for Mg+Al+Ca
                       ~2650–4050 kWh/t, electrode life 2–3×

Campaign 5 option: Retain TiO₂ (stop MRE before Ti sweep) → C6 produces Al-Ti alloy
```

***Path & Branch Selection Guidance**

| Mission priority / Feedstock              | Recommended Path                  | Recommended Branch | Rationale |
|-------------------------------------------|-----------------------------------|--------------------|-----------|
| Maximum throughput / electrode life / fused silica | A (continuous pN₂)               | Two                | 8–20 % faster cycles, halved C3, 5–10× electrode life |
| Structural CMAS glass or specific ceramics recipes | B (pO₂-managed)                  | Two                | Preserves melt for direct tapping as Material 1 |
| Minimal electrical power budget           | A                                 | Two                | Lowest total kWh/t |
| Al-Ti alloy production                    | A or B (stop C5 before Ti)        | Two                | Retain TiO₂ for thermite |
| S-type / CI asteroid (Mg-dominant)        | A                                 | Two                | C4-dominant; benefits from early SiO₂ removal |
| Mars (no vacuum possible)                 | Merged continuous (CO₂)           | Two                | Environment forces it |
| Bootstrapping phase (first 3–6 batches)   | A                                 | Two                | Maximises early Mg harvest |

## Three-Tier Shuttle Architecture
Each tier: inject reductant → reduce target oxide → tap metal → pO₂ bakeout → recover reductant.

| Tier | Reductant | Targets | Ellingham ΔG°f (kJ/mol O₂) |
|------|-----------|---------|----------------------------|
| 1 | K | Fe, Si conditioning | –320 to –560 |
| 2 | Na | Ti, Cr | ~–580 |
| 3 | Mg | Al | ~–720 (vs MgO ~–830) |

All tiers are solar-thermal with zero net electricity.

## Mass Balance (mare, Branch Two, per tonne)
- Metals: ~530–600 kg (Fe 85–130, Ti 12–15, Mg 18–42, Si 30–100, Al 65–80)
- O₂: ~380–440 kg
- Terminal slag: 10–15 kg (REE-enriched CaO-MgO ceramic, 0.5–1.0 wt% REE)
- Process losses: ~5–15 kg

**Mg Inventory Bootstrapping Protocol (Branch Two only)**  
Mare batches are Mg-surplus: run 3–6 full C4 campaigns first to accumulate 150–300 kg Mg inventory before the first C6 thermite run. Highland batches are inventory-neutral (requires ≥80 % bakeout recovery per cycle). S-type asteroid and CI chondrite are immediate surplus (C4 can be run on batch 1). Track cumulative Mg in plant storage (cold-trapped or ingot). C6 stoichiometric draw is 50–60 kg (mare) or 100–115 kg (highland). After bootstrapping, the process becomes self-sustaining with >99 % net Mg recovery across the full chain.

**Terminal Byproducts**
- **Fused silica glass** (Path A only): 100–160 kg per tonne captured on removable Stage 2 cartridge. High-purity material for optics, fibre drawing, or solar concentrator elements.
- **Terminal slag** (post-C6): 10–15 kg REE-enriched CaO-MgO ceramic (0.5–1.0 wt% total REE + Zr). Self-terminating; valuable for advanced ceramics, phosphors, or dedicated REE extraction.
- Process losses: 5–15 kg (minor vapor carryover + dust).

## Key Constraints for Simulation
- Alkali slag solubility: 8–12 wt% Na₂O/K₂O equivalent per cycle → 2–3 cycles needed
- Na/K recovery: 75–92% per bakeout cycle, >99% across full process chain
- Mg inventory: mare batches are Mg-surplus; highland ~neutral at ≥80% bakeout recovery
- Mixed alkali effect: strict K-first, Na-second sequencing in C3 (MAE reduces diffusion up to 10× at equimolar)
- Self-terminating liquidus in C6: process stalls when residual SiO₂+Al₂O₃ < ~15–20% (liquidus exceeds ~1700°C)
- Induction stirring assumed throughout: 4–8× rate acceleration + 50–80°C thermal cycling
