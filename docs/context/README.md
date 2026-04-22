# Oxygen Shuttle — Compressed Context Files

## Purpose
These files compress the full Oxygen Shuttle whitepaper (~32k tokens) and Appendices G–J (~13k tokens)
into ~7.8k tokens of simulator-relevant context — an **83% reduction** (5.8× compression).

All quantitative parameters, thermodynamic relations, setpoints, and feedstock compositions are preserved.
Design rationale, prose justification, and background literature have been stripped.

## File Structure & Loading Strategy

| File | Tokens | Load When |
|------|--------|-----------|
| `context-core.md` | ~930 | **Always** — process overview, campaign ladder, decision tree |
| `context-setpoints.yaml` | ~1,900 | Implementing campaign logic, simulator parameters |
| `context-thermo.md` | ~1,500 | FactSAGE/MELTS integration, equilibrium calculations |
| `context-feedstocks.yaml` | ~1,500 | Working on feedstock-specific processing |
| `context-deltas.md` | ~1,960 | Asteroid/Mars adaptations (highland, S-type, M-type, Mars, CI) |

**Recommended default load:** `context-core.md` + whichever domain file(s) the current task needs.
Typical working context: 2.5–5k tokens.

## Reference Tag System
`context-thermo.md` uses `[THERMO-N]` tags. Reference these in code or prompts:
- `[THERMO-1]` — SiO suppression equation
- `[THERMO-3]` — Ellingham hierarchy table
- `[THERMO-9]` — MRE voltage thresholds
- etc.

## Source Documents
- `The_Oxygen_Shuttle_Whitepaper_Rev5.docx` (Feb 2026, Simon Rowland)
- `Oxygen_Shuttle_Appendices_G-J_v6_1.docx`

## What Was Cut
- Design rationale and motivation ("why" sections)
- Literature review and bibliography
- Corrosion discussion (Section 6) — one-line summary retained in core
- Furnace/plant design prose (Section 10) — parameters extracted to setpoints
- Ceramics recipes (Appendix E) — not needed for thermodynamic simulator
- Glass fibre recipes (Section E.10) — not needed for simulator
- Condensation train prose (Appendix D) — parameters extracted to setpoints
- Mission ground-truth narratives — feedstock confidence levels retained as tags
