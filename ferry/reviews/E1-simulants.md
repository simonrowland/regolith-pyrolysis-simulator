# E1 simulants — printed starting compositions (t-954)

Branch: `empirical/e1-simulants-2026-09-22` (from `origin/work-v064-green`)
Worktree: `/workspace/repos/wt/slot-01`
Tip: `2d7dc0fb81adfea9009c692587965093ca57329e`

## Per-source outcomes

### britt-2019-asteroid-simulants — LANDED

- Source table: Table 3 (PDF p. 9 / printed p. 2075), XRF major-element bulk oxide wt% for CI / CM / CR Simulant columns.
- Not used as sample composition: Table 2 mineral-recipe wt% (prototype and future-update columns).
- Landing: split former `tga-ega-simulant-series` into `ci-tga-ega`, `cm-tga-ega`, `cr-tga-ega`; each `sample.printed_composition` is the matching Table 3 oxide map (FeOT and SO3 retained as printed; Total row omitted). Labels kept in locator notes.
- Observation FKs: per-simulant TG mass-loss and Table 3 XRF rows retargeted; shared TG/EGA method rows left without an experiment FK (common protocol, not one charge).
- Validator: `python tools/validate_literature_extracts.py data/literature/extracts/britt-2019-asteroid-simulants.yaml` → OK.
- Commit: `47f16f28c` — extracts: land Britt 2019 Table 3 XRF oxides on per-simulant TG/EGA experiments

### deguzman-2026-simulant-physicochemical — BLOCKED

- Printed tables: Table 1 manufacturer mineral wt% (LHS-1/LHS-2 and LSP-2); Table 2 this paper's SEM-EDX elemental at% and wt% for LHS-2 and LSP-2 (not an oxide table).
- Stop reason: experiment→composition mapping is not 1:1. Existing registry experiments are multi-material series (`bet-surface-area-series`: LHS-2 plus five constituent minerals; `hydrogen-tpr-series`: LHS-2, LSP-2, ilmenite, olivine). Attaching one bulk analysis (or inventing per-row experiments) would guess which charge owns the series. Ferry rule: STOP rather than guess.
- Note: even if split, the only this-paper bulk analysis is elemental (Table 2), not oxide wt%; Table 1 is mineral-recipe, not sample bulk analysis under the simulant rule.
- No extract change; no commit. Pre-existing validator errors on this file were not touched.

### hendrix-2024-reactivity-reduced-simulants — LANDED

- Source table: Table 1 (PDF p. 4 / printed p. 2489), bulk oxide wt% columns for JSC-1A, LMS-1, LHS-1 (Apollo columns not experimental charges).
- Not used as sample composition: mineral-abundance block of Table 1.
- Landing: replaced free-text labels on `jsc-1a-h2-reduction`, `lms-1-h2-reduction`, `lhs-1-h2-reduction` with the printed oxide maps; blank oxide cells omitted; labels retained in locator notes; footnotes a/b (Exolith / Hill et al.) noted.
- Validator: OK.
- Commit: `2d7dc0fb8` — extracts: land Hendrix 2024 Table 1 bulk oxides on reduction experiments

## Push

Pushed: yes (`origin/empirical/e1-simulants-2026-09-22`).
