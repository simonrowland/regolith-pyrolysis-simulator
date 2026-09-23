# W2 — IMPLEMENT: JANAF pure-phase reaction catalogue

**Branch:** `empirical/w2-reaction-catalogue-2026-09-22`
**Base:** `7d11e6d60` (`origin/review/janaf-p4a2-score`)
**Status:** implemented

## Intent

Extend `simulator/melt_backend/pure_phase_janaf_score.py` from two hard-coded
reactions to a small declarative **reaction catalogue** of condensed-phase
reactions that NIST-JANAF tabulates and both MAGEMin / ThermoEngine carry.
Polymorph must match or the row is refused typed (existing rule). Honor R10:
do not score Mg-012 past I↔II @ 903 K / II↔III @ 1258 K as monolithic
clinoenstatite.

## Catalogue entries

| reaction_id | equation | JANAF tables | TE phases | MAGEMin phases |
|---|---|---|---|---|
| `2MgO+SiO2->Mg2SiO4` | 2 MgO + SiO2(qz) → Mg2SiO4 | Mg-008, O-037, Mg-028 | Per, Qz, Fo | per, q, fo |
| `MgO+SiO2->MgSiO3` | MgO + SiO2(qz) → MgSiO3(clino) | Mg-008, O-037, Mg-012 | Per, Qz, cEn | per, q, en† |
| `MgO+Al2O3->MgAl2O4` | MgO + Al2O3(α) → MgAl2O4 | Mg-008, Al-096, Al-089 | Per, Co, Sp | per, cor, sp |
| `Al2O3+SiO2->Al2SiO5(andalusite)` | Al2O3(α) + SiO2(qz) → andalusite | Al-096, O-037, Al-102 | Co, Qz, a | cor, q, and |
| `Al2O3+SiO2->Al2SiO5(kyanite)` | → kyanite | Al-096, O-037, Al-103 | Co, Qz, Ky | cor, q, ky |
| `Al2O3+SiO2->Al2SiO5(sillimanite)` | → sillimanite | Al-096, O-037, Al-104 | Co, Qz, Sil | cor, q, sill |

† MAGEMin `en` is orthoenstatite → static preflight refuses typed (unchanged).

**Omitted (with reason):**

- `CaO+SiO2->CaSiO3`, `2CaO+SiO2->Ca2SiO4` — compilation has CaO (`Ca-027`) but **no** CaSiO3 / Ca2SiO4 tables.
- FeO / Fe2O3 condensed pairs — Fe-001 is non-stoichiometric wüstite (`Fe0.947O`); hematite (`Fe-030`) alone is not a balanced condensed-only reaction both engines expose as pure FeO + Fe2O3. Tables registered for future use only.

Data shape: `REACTION_CATALOGUE: Tuple[ReactionSpec, ...]` (declarative).
`REACTIONS` remains an alias for existing runners.

## R10 (Mg-012)

- Token `janaf_solid_solid_transition`; bound `MG012_CLINOENSTATITE_MAX_K = 903.0`.
- `mg012_clino_temperature_refusal(...)` refuses T ≥ 903 K; detail names II↔III when T ≥ 1258 K.
- Default TE `cEn` phase plan restricted to `(298.15, 500.0)`.
- Runner `scripts/janaf_pure_phase_score.py` skips Mg-012-backed reaction/phase rows past the bound into typed refusals.

## Engine map extensions (so both engines “carry” catalogue phases)

- TE `_TE_PURE_PHASE_POLYMORPH`: `Lm`, `Co`, `Sp`, `a`, `Ky`, `Sil`.
- MAGEMin `_MAGEMIN_PURE_PHASES`: `cor`, `sp` (host `spn`), `and`, `ky`, `sill` with stoichiometric bulks.

Live MAGEMin/ThermoEngine not run on this box; controller runs live.

## JANAF-side anchors (hand-checked, kJ/mol Δ_rG)

| reaction | 1000 K |
|---|---:|
| spinel | −32.525 |
| andalusite | −5.462 |

## Tests

```text
PYTHONPATH=. .venv/bin/pytest tests/test_pure_phase_janaf_score.py -o addopts=
# 29 passed (was 18 on tip 7d11e6d60; +11 catalogue/R10)
```

Coverage includes: catalogue membership + omissions, spinel/andalusite
reaction_sum anchors, builder JANAF side with fakes, preflight match/mismatch
for Al2SiO5 polymorphs, engine polymorph map reads, real-table loads, R10
Mg-012 temperature refusal.

## Files

- `simulator/melt_backend/pure_phase_janaf_score.py` — catalogue + R10 + tables
- `scripts/janaf_pure_phase_score.py` — catalogue loop + Mg-012 T refusals
- `engines/alphamelts/thermoengine.py` — TE polymorph labels
- `simulator/melt_backend/magemin.py` — MAGEMin pure-phase specs
- `tests/test_pure_phase_janaf_score.py` — catalogue + R10 tests

## Push

**Yes** — `origin/empirical/w2-reaction-catalogue-2026-09-22` @ `55f912e5e`.

## Tip

`55f912e5ecebc6517cb8eb3fcd351079f2b2c89d`
