# E4 kems alloy/glass — printed starting compositions (t-954)

Branch: `empirical/e4-kems-alloy-glass-2026-09-22` (from `origin/work-v064-green`)
Worktree: `/workspace/repos/wt/slot-06`
Tip: `b764bca08566c8acbfce1ba58ffa659ccdd22e3b`

## Per-source outcomes

### kems-001-homma-1966 — LANDED

- Source: Tables 1–3 (journal pp. 517–518), mother-alloy elemental wt% rows `%Mn` / `%Cu` / `%Sn`; Fig. 1 caption carbon for Fe–C–Mn melts 6501-11 / 6501-12 (4.85% / 4.69%).
- Not oxides. Fe balance not printed and not invented. Other mother alloys stated as carbon of order 10⁻² wt% (not a per-melt table) — not landed as numbers.
- Landing: split former `alloy-vacuum-evaporation-series` into twelve per-melt experiments (`fe-mn-6502-1`, `fe-mn-6502-2`, `fe-mn-6412-14`, `fe-c-mn-6501-11`, `fe-c-mn-6501-12`, `fe-cu-6503-12`, `fe-cu-6503-11`, `fe-cu-6503-16`, `fe-cu-6503-28`, `fe-sn-6503-15`, `fe-sn-6503-14`, `fe-sn-6504-1`); each `sample.printed_composition` is the printed solute elemental wt% map; melt/alloy labels in locator notes.
- Observation FKs: three multi-melt rows that pointed at the lumped series had their experiment FK removed (shared protocol, not one charge).
- Note: solute-only maps are exact as printed; `engine_point` may treat single-species charges as not applicable until Fe is printed elsewhere — no Fe invented.
- Validator: `python tools/validate_literature_extracts.py data/literature/extracts/kems-001-homma-1966.yaml` → OK.
- Commit: `645058e79` — extracts: land Homma 1966 mother-alloy elemental wt% on per-melt experiments

### kems-027-plante-hastie-1983 — LANDED

- Source: Table 1, report p. 3 (PDF p. 9), Simulated Nuclear Waste (SNW) Glass Composition.
- Landing: both **nominal** and **analytical** Weight % maps on `kms-vacuum-glass-series` and `tms-n2-glass-series` `sample.printed_composition`, nested under labels `nominal` and `analytical` as printed; Mole % columns not copied; footnotes a (Soper 1982) and b (selection / nominal for thermo estimates) noted.
- Values (Weight %): nominal SiO₂ 52.00 … Re₂O₇ 0.10; analytical SiO₂ 53.76 … Re₂O₇ 0.03 (full 15-compound vectors).
- Note: nested labelled form stores both prints. Consumers that need a flat oxide map should select `analytical` (measured glass) or `nominal` (thermo estimates) explicitly.
- Validator: OK.
- Commit: `811af9a5a` — extracts: land Plante-Hastie 1983 Table 1 nominal and analytical glass wt%

### kems-036-sesko-2024 — LANDED

- Source: Table 4.1, thesis p. 39 (PDF p. 54). Oxide wt% column **EAC-1** (caption: EAC-1A numbers from [25]; column header EAC-1). Experimental charge is EAC-1A.
- Landing: replaced free-text label on `solar-exposure-12` with the EAC-1 oxide map; 0.00 cells retained; Total 100.00 omitted; Apollo soil columns not used as this charge.
- Validator: OK.
- Commit: `b764bca08` — extracts: land Sesko 2024 Table 4.1 EAC-1 oxides on solar-exposure-12

## Push

Pushed: yes (`origin/empirical/e4-kems-alloy-glass-2026-09-22`).
