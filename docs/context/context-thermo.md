# Oxygen Shuttle — Thermodynamic Context

## Reference Tags
Use `[TAG]` references in code/prompts to invoke specific relations.

## Core Equilibria

### SiO Suppression
[THERMO-1] `p(SiO) = K(T) × a(SiO₂) / √pO₂`
- Reaction: SiO₂(melt) → SiO(g) + ½O₂(g)
- At hard vacuum pO₂ ~1e-9 bar: SiO dominates offgas above ~1400°C
- At pO₂ = 1e-3 bar: suppression factor = √(1e-3/1e-9) = √1e6 ≈ 1000×
- Conservative real-world factor: >300× (accounts for non-ideality, evolving composition)
- SiO condenses as amorphous silica below ~1200°C (adhesive, corrosive)

[THERMO-2] Progressive Si conditioning: each campaign removing SiO₂ (metallothermic → Si metal) lowers a(SiO₂), reducing p(SiO) proportionally. 30–50% a(SiO₂) reduction opens C4 Mg pyrolysis window.

### Ellingham Diagram Values (per mol O₂ at 1600°C)
[THERMO-3] Standard-state ΔG°f hierarchy (more negative = more stable):

| Oxide | ΔG°f (kJ/mol O₂) | Reduction Method |
|-------|-------------------|------------------|
| Na₂O | ~–320 | Vacuum pyrolysis (C1) |
| K₂O | ~–320 | Vacuum pyrolysis (C1) |
| FeO | ~–370 | Pyrolysis (C2) or K shuttle (C3) |
| Cr₂O₃ | ~–500 | Na shuttle (C3) |
| SiO₂ | ~–560 | K shuttle conditioning (C3) or MRE (C5) |
| TiO₂ | ~–580 | Na shuttle (C3) or MRE (C5) |
| Al₂O₃ | ~–720 | Mg thermite (C6) |
| ZrO₂ | ~–780 | Unreduced (concentrates in slag) |
| MgO | ~–830 | Pyrolysis (C4) or MRE (Branch One) |
| CaO | ~–900 | Pyrolysis (marginal) or MRE |

[THERMO-4] **Critical Ellingham crossing:** MgO (–830) is below Al₂O₃ (–720) → Mg can reduce Al₂O₃.

### Shuttle Mechanism Reactions
[THERMO-5] Na/K injection (1200–1350°C):
- `2Na(g) + FeO(melt) → Na₂O(melt) + Fe(l)`
- `2Na(g) + TiO₂(melt) → Na₂O(melt) + Ti(l)` (accessibility uncertain — key experimental question)
- `2K(g) + FeO(melt) → K₂O(melt) + Fe(l)`
- Net: alkali reduces metal oxide; product alkali-oxide dissolves in silicate network

[THERMO-6] Bakeout (1520–1680°C, 0.5–1.5 mbar O₂):
- Na₂O(melt) → 2Na(g) + ½O₂(g)  [alkali p_vap >> pO₂]
- Recovery: 75–92% per cycle

[THERMO-7] Mg thermite:
- `3Mg(l) + Al₂O₃(melt) → 3MgO(slag) + 2Al(l)`
- ΔG° ≈ –110 kJ/mol Al₂O₃ at 1600°C (strongly favourable)

[THERMO-8] Back-reduction cascade (spontaneous):
- `4Al + 3SiO₂ → 2Al₂O₃ + 3Si`  (Al re-oxidises; Si survives as metal)
- Selectivity improves at lower equilibration temperature (wider Ellingham gaps)

### MRE Voltage Thresholds
[THERMO-9] Electrochemical reduction sequence:

| Species | Decomposition Voltage (V) | Campaign |
|---------|--------------------------|----------|
| Na₂O | <0.5 | C5 (opening) |
| K₂O | <0.5 | C5 (opening) |
| FeO | ~0.6 | C5 (should be pre-depleted) |
| Cr₂O₃, V₂O₅, MnO | 0.8–1.0 | C5 (trace cleanup) |
| SiO₂ | ~1.4 | C5 (primary target) |
| TiO₂ | ~1.5 | C5 (sweep or retain for C6 Al-Ti) |
| Al₂O₃ | ~1.9 | Branch One only |
| MgO | ~2.2 | Branch One only |
| CaO | ~2.5 | Branch One only (or dedicated batch) |

## Activity & Non-Ideality (FactSAGE/MELTS critical)

[THERMO-10] Na₂O activity in CMAS melt:
- γ(Na₂O) ≈ 10⁻² to 10⁻³ (melt strongly stabilises dissolved Na₂O)
- Effective ΔG shift: ~50–80 kJ/mol
- Consequence: Na may reduce oxides (Cr₂O₃, possibly TiO₂) inaccessible from standard-state data
- **This is the highest-priority FactSAGE modelling target**

[THERMO-11] SiO₂ activity evolution:
- a(SiO₂) starts near unity in fresh basalt melt
- Drops as metallothermic reduction converts SiO₂ → Si metal
- 30–50% reduction sufficient for C4 Mg pyrolysis headroom
- Path A (pN₂ sweep) removes 50%+ of SiO₂ inventory directly

[THERMO-12] MgO activity in different melts:
- CMAS basalt (lunar): a(MgO) moderate, stabilised in network
- Olivine-normative (S-type): a(MgO) approaches unity (forsterite stability field)
- Higher a(MgO) → faster/higher-yield Mg pyrolysis

## Vapour Pressures (key operating parameters)

[THERMO-13] Metal vapour pressures at process temperatures:

| Species | Temperature (°C) | p_vap (approx.) | Notes |
|---------|------------------|------------------|-------|
| Na | 1600 | several bar | >> any pO₂ setpoint |
| K | 1600 | > Na (more volatile) | >> any pO₂ setpoint |
| Fe | 1400–1600 | 0.01–0.1 mbar | adequate for selective harvest |
| Mg | 1600 | ~0.5 bar | >> pO₂ setpoints |
| Mg | 1500–1580 | ~0.1 bar | workable under pN₂ variant |
| Ca | 1484 (bp) | — | significant above ~1500°C |
| Al | 2519 (bp) | negligible <2500°C | not pyrolysable |
| Si | — | negligible | extracted only by MRE |
| SiO | 1600 (vacuum) | 0.5–2 mbar | the problem species |
| SiO | 1600 (1 mbar O₂) | <0.005 mbar | suppressed >300× |

[THERMO-14] Condensation temperatures at millibar partial pressures:
- Fe: 1100–1400°C (Stage 1)
- SiO: 900–1200°C (Stage 2, disproportionates: 2SiO → Si + SiO₂)
- Mg: 500–650°C (Stage 3)
- Na: 450–550°C (Stage 3)
- K: 400–500°C (Stage 3)

## Kinetic Parameters

[THERMO-15] Hertz-Knudsen evaporation flux:
- SiO at 1600°C, a(SiO₂)~1: ~60–70 g/(m²·s)
- For 0.2 m² crucible surface: ~13 g/s theoretical max
- Hot-wall pipe transport limit (12cm, 10 mbar N₂): 7–16 g/s SiO

[THERMO-16] Melt viscosity:
- FeO-fluxed basalt (fresh C2): 3–10 Pa·s
- Iron-depleted aluminosilicate (C5): higher, better for electrode stability

[THERMO-17] Induction stirring acceleration: 4–8× rate vs static melts
- 50–80°C thermal cycling provides continuous surface renewal

## Key Modelling Uncertainties (Priority for FactSAGE/MELTS)
1. Na₂O/K₂O activity coefficients across evolving melt compositions (C1→C6)
2. TiO₂ accessibility to Na reduction (the most consequential uncertainty for Ti yield)
3. SiO₂ activity evolution as Fe, Ti, Si are progressively removed
4. Mg bakeout kinetics from thermite-product MgO vs silicate-stabilised MgO
5. Back-reduction cascade equilibrium: Al/Si/Ti partitioning at 1500–1700°C
