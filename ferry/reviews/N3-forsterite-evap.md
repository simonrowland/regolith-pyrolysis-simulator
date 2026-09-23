# N3 — forsterite evaporation NEW EXTRACT (BACKLOG 3)

Branch: `empirical/n3-forsterite-evaporation-2026-09-22` (from `origin/work-v064-green`)
Worktree: `/workspace/repos/wt/slot-04`
Tip: `0e3aef4b20420685ed751c6849a4cbec1ebb8c2c`

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `1e594b273` | kuroda-hashimoto-2002-forsterite-hydrogen | Table 1 Jexp + author Ea / rate law |
| `5e15d8928` | tsuchiyama-1998-forsterite-mg2sio4-h2 | Table 1 mass-loss + Arrhenius jFo + alpha |
| `0e3aef4b2` | ta-shirai-2000-lpsc | Printed alpha* and jNa–pO2 slopes (Na melt) |

## STEP 0

| Corpus id | Duplicate extract? | PDF p1 ↔ sidecar |
|-----------|--------------------|------------------|
| kuroda-hashimoto-2002-forsterite-hydrogen | No (no Kuroda / DOI `10.15094/00006017` hit) | Yes — title/authors/AMR 15, 152–164 match |
| tsuchiyama-1998-forsterite-mg2sio4-h2 | No separate paper (existing `tachibana-tsuchiyama-1998-forsterite-dust-lpsc` is a different LPSC abstract) | Yes — title/authors/Min. J. 20, 113–126 match (encrypted PDF; p1 via render) |
| ta-shirai-2000-lpsc | No | Yes — LPSC XXXI 1610; **Na₂O–SiO₂ melt**, not forsterite (sidecar had no citation; p1 used) |

PDFs remain under `ferry/pdfs-n/` (gitignored); not committed.

## Per-source

### kuroda-hashimoto-2002-forsterite-hydrogen — LANDED

- **Locator:** Table 1 (published p. 157); Abstract / eq. (3) for real Ea and rate law.
- **Landed:** 36-row `rate_series` of printed Jexp with T, PH₂, PH, duration, areas, weights, porosities; context apparatus (Mo cell, W catalyst, Deltech furnace, pump).
- **DERIVED:** Author real Ea 32.6±4.8 kcal/mol, apparent Ea 86.5±4.8 kcal/mol, and printed rate law (eq. 3) stamped `derived: true` / author-derived method_class — not raw Jexp.
- **Not used:** Fig. 3–8 intercepts/curves (not digitized).
- **Validator:** OK (`--check-fidelity-match`).

### tsuchiyama-1998-forsterite-mg2sio4-h2 — LANDED

- **Locator:** Table 1 (published p. 116); Abstract for Arrhenius + alpha.
- **Landed:** 18-run `mass_loss` Table 1; apparatus context (Mo crucible 16×124 mm, Ta heater, pH₂=1.4×10⁻⁵ bar, {010}).
- **DERIVED:** Abstract Arrhenius `jFo = 2480 exp(-372 kJ mol⁻¹ / RT)` and alpha 0.04–0.12 / recommended ~0.1 stamped derived (from Fig. 2 slopes / ideal HK — figures not digitized).
- **Validator:** OK.

### ta-shirai-2000-lpsc — LANDED (lane note: not forsterite)

- **Locator:** Experiments §; Fig. 1a/b printed alpha*; Fig. 2 printed slopes.
- **Landed:** Six printed alpha* (1300/1400 °C × pO₂ 10⁻⁸/10⁻⁹/10⁻¹⁰ bar); two log jNa–log pO₂ slopes; wire-loop / H₂–CO₂ apparatus context; start Na₂O 22–23 wt%.
- **DERIVED:** alpha* and slopes are author least-squares / model-derived from concentration–time curves (curves themselves not digitized).
- **Validator:** OK.

## Validation / hygiene

- `uv run python tools/validate_literature_extracts.py <file> --check-fidelity-match`: all three OK.
- Printed values only; derived quantities stamped; absence not zeroed.
- Pathspec-only YAML adds; no PDFs on branch (`*.pdf` gitignored).

## Push

Pushed: yes (`origin/empirical/n3-forsterite-evaporation-2026-09-22` @ `0e3aef4b2`).
