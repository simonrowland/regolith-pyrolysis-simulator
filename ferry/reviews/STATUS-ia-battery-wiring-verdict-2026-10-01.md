# STATUS — ia-battery-wiring verdict (regolith-empirical)

- **Tip:** `76b55001909ecdebc1a335ac6d45337870448075` (`review/ia-battery-wiring`)
- **Base:** `72cb7d960a71e7bb81430ed733a15f99edfa330b`
- **Seat:** `/workspace/repos/wt/slot-b565` (cleared idle after review)
- **Range:** `72cb7d960..76b550019` (one commit: Wire internal analytical battery scoring)
- **VERDICT LAND 76b55001909ecdebc1a335ac6d45337870448075**
- **Deliverable:** `REVIEW-ia-battery-wiring-regolith-physics-2026-10-01.md`
- **Date:** 2026-10-01 ~21:03 ET
- **Mailbox tip:**  on  (artifacts ).

IA battery producer wires core `VAPOR_PRESSURE` (parity to 1e-12); effusion pO2 uses shared openimcc oxygen-balance condition only; minimal `OXYGEN_BALANCE_EFFUSION_ENGINES` score hook merge-clean vs refconv/effusion-stratum; typed refusals + report rail/reason table OK; Plante 162 K numbers arithmetic-checked (full re-score ASK Mac if needed). 1 ms isolated-cell race: VPS 10/10 pass tip+base; Mac flake pre-existing — not tip regression. No P1 REVISE items.

— regolith-empirical
