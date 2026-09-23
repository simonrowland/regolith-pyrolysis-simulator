# R24 — W2 catalogue hand-port (spinel + Al2SiO5 polymorphs)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `f7a79f2a32b1eb784bfc5639db483c5f7952bbe6` on `origin/review/w2-port` (detached; parent `48ca3f865`)  
**Worktree:** `/workspace/repos/wt/slot-01` @ detached `f7a79f2a3` (fetch+`rev-parse` match)  
**Files:** `simulator/melt_backend/pure_phase_janaf_score.py`, `simulator/melt_backend/magemin.py`, `engines/alphamelts/thermoengine.py`, `scripts/janaf_pure_phase_score.py`, `tests/test_pure_phase_janaf_score.py`

**Intent (claimed):** Hand-port the W2 reaction catalogue onto the printed-band tip: spinel + andalusite/kyanite/sillimanite; ThermoEngine Berman symbols **Crn / Spl / And** (live-checked; not Co/Sp/a); MAGEMin spinel **refused typed** (no plain MgAl2O4 endmember); corundum matched through the printed **ALPHA** band.

**Attack surface:** reaction stoichiometry / JANAF ΔrG anchors; polymorph band matching (Al-096 alpha, Al2SiO5 titles, mismatch rows); 1-bar metastability notes; TE symbol map; MAGEMin bulk wt% and spinel refusal; regression vs prior Mg-silicate / R10 behaviour.

**Method:** Diff tip vs parent `48ca3f865` and vs empirical W2 tip `55f912e5e` (wrong Co/Sp/a + `sp`@`spn`). Live JANAF table band/DfG probes for Al-089/096/102/103/104. Stoichiometry and metastability matrix probes. Pytest on tip. Mode: **ran-tests**.

---

## Findings

### P2 — Unary corundum phase notes claim a quartz exemption

**Evidence (file:line on `f7a79f2a3`):**

- `SAME_MINERAL_SUBFORMS` adds `'corundum': frozenset({'alpha'})` (`pure_phase_janaf_score.py:172–175`) so engine `corundum` matches Al-096's printed `ALPHA <--> LIQUID` band (live: title_polymorph=`corundum`, band=`alpha` to 2327 K).
- `_subform_note` (`pure_phase_janaf_score.py:549–560`) is still quartz-hardcoded: *"alpha/beta inside one quartz phase is not a mismatch"*.
- `build_phase_row` appends that note whenever engine ≠ band polymorph (`:1149–1153`).
- **Constructed trigger (live):** `build_phase_row(PhaseScoreRequest('thermoengine','Crn','Al-096'), 1000.0, …)` (same for `magemin`/`cor`) → notes include the quartz sentence. Reaction-path wording for the same exemption is already mineral-agnostic (`:1003–1008`); only the unary path lies.

**Why P2 (live note, not P0):** Residuals / ΔrG are unchanged. The scored PhaseRow note text is false for every default-T Crn/cor unary row this tip adds. Introduced by extending the subform map without generalising the note.

**Fix direction:** Drop the quartz-specific clause (mirror the reaction-path sentence), or branch on `title_polymorph` / mineral.

---

### P2 — Andalusite below the 1-bar Ky–And intercept is not flagged

**Evidence:**

- Cited 1-bar sequence (`:108–116`): Ky–And ~493 K (Anderson/Newton/Kleppa on the Holdaway family); And–Sil ~1043 K; mullite ~1473 K.
- `_al2sio5_stability_notes` (`:883–921`) flags: kyanite when `T > 493`; sillimanite when `T < 1043`; andalusite when `T > 1043`; all three when `T > 1473`.
- **Gap:** andalusite at `T ≤ 493` (kyanite field on that diagram) returns `()`. Probe: `_al2sio5_stability_notes('andalusite', 298.15) == ()` while sillimanite at the same T is flagged. Default catalogue T includes 298.15.

**Why P2 (weaker / notes-only):** Matched polymorphs still score by design. No wrong residual. Incomplete relative to the same 1-bar diagram the constants cite; asymmetric with sillimanite (flagged outside its field on the low-T side of And–Sil).

**Fix direction:** Flag andalusite when `T < KYANITE_METASTABLE_ABOVE_K` (and keep the existing And–Sil / mullite flags). Extend `test_al2sio5_metastability_flagged_like_quartz`.

---

### P3 — Al2SiO5 bulk wt% comment vs CIAAW division

**Evidence:** Comment (`magemin.py:478–480`) claims SiO2 37.0780 / Al2O3 62.9220 from CIAAW Al 26.9815385, Si 28.085, O 15.999. Exact division is SiO2 **37.078412…** / Al2O3 **62.921588…** (sums to 100). Tip rounds to 37.0780/62.9220; prior empirical W2 used 37.0781/62.9219. Δ ~4e-4 wt%.

**Why P3:** No demonstrated wrong G retrieval; token-scale vs periclase's 1e-6 SiO2 guard. Comment oversells exactness.

**Fix direction:** Store the exact rounded pair from the division (or note “rounded to 0.0001 wt%, sum 100”).

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Stoichiometry / JANAF ΔrG anchors | **Fail (safe).** Reactions element-balanced. Live Al-089/096/Mg-008 @1000 K → spinel ΔrG **−32.525**; Al-102/096/O-037 → andalusite **−5.462** (matches tests + prior W2 ferry). Ky/sil sums at default T finite; all default-T JANAF rows present. |
| TE symbols Co/Sp/a vs Crn/Spl/And | **Fail (safe) vs claim.** Tip maps Crn/Spl/And/Ky/Sil/Lm; commit message + test assert Co/Sp/a raise on live Berman `get_phase` (skipped here: no TE dylib). Empirical W2 `55f912e5e` still had Co/Sp/a — port corrected. |
| MAGEMin spinel scored as JANAF Al-089 | **Fail (safe).** `MgAl2O4: None` + `missing_endmember` → `NO_JUSTIFIED_ENGINE_ENDMEMBER`; no `sp`/`spn` registry entry (W2 wrongly used `sp`@`spn`). Preflight refuses every default T. |
| Corundum vs ALPHA band | **Fail (safe) for matching.** `corundum` matches alpha below 2327 K; refuse at 2327 K. **Hit (P2):** unary subform note says “quartz”. |
| Al2SiO5 polymorph mismatch | **Fail (safe).** Default requests And/and vs Al-104 → `POLYMORPH_MISMATCH`; matching And/Ky/Sil and/ky/sill vs own tables clear preflight at default T. |
| Metastability notes invent stability | **Partial hit (P2).** Flags do not refuse (by design). Kyanite>493 / sillimanite<1043 / andalusite>1043 / mullite>1473 behave as coded; andalusite≤493 unflagged. Quartz notes still gated on SiO2=O-037 (spinel has `sio2_polymorph is None`). |
| CaSiO3 / FeO catalogue creep | **Fail (safe).** Omitted; Ca-027 lime fallback only; no Fe-030 in `JANAF_TABLES`. |
| Mg-silicate / R10 regression | **Fail (safe).** Forsterite/enstatite unchanged; Mg-012 i/ii/iii refusals still covered (`-k 'mg012 or enstatite or clino or 903'`: 4 passed). |
| One bad engine row aborts the run | **Fail (safe).** `_consume_row` maps `PurePhaseUnknownSymbolError` / `PurePhaseAccessError` / `ValueError` → `ENGINE_PHASE_ACCESS`. |

---

## What looks sound

- Declarative `REACTION_CATALOGUE` with `REACTIONS` alias; spinel + three Al2SiO5 rows; TE Crn/Spl/And after live Berman check (vs broken empirical W2 symbols).
- MAGEMin spinel refusal is the right physics call: ig host `spl` endmembers are ordered/Fe-bearing (`nsp`, …), not calorimetric MgAl2O4.
- Al-096 corundum↔alpha via `SAME_MINERAL_SUBFORMS` is the right band rule; Al-089/Ca-027 crystal bands close at printed liquid.
- `sio2_polymorph: Optional[str]` + render `-` for spinel; quartz metastability / TE quartz adjustment only when SiO2 term is O-037/Qz.
- Engine access failures become typed refusals instead of aborting the script.

### Residual notes (not severity)

- Holdaway And–Sil 1-bar intercept is often ~775 °C (~1048 K); tip uses 770 °C / 1043 K — conventional rounding, notes-only.
- Live TE/MAGEMin catalogue resolution tests skipped on this box (32 passed, 2 skipped); claim’s “checked live” is in the test body for a machine with dylibs/binary.
- Kyanite note text says “Holdaway 1971 family” while the constant comment cites Anderson, Newton & Kleppa 1977 — soft attribution only.

---

## Tests

Tip worktree at `f7a79f2a3`, `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `PYTHONPATH=.`, `-o addopts=`:

- `tests/test_pure_phase_janaf_score.py`: **32 passed, 2 skipped** (TE + MAGEMin live)
- Live JANAF probes: Al-096 alpha band to 2327 K; Al-089 spinel to 2408 K; Al-102/103/104 title bands unbounded; spinel/andalusite 1000 K anchors −32.525 / −5.462
- Adversarial note probes: Crn unary quartz wording (P2); andalusite @ 298.15 unflagged (P2)

No push. No fix patch.

---

## Verdict rationale

Catalogue stoichiometry, TE symbol correction, MAGEMin spinel typed refusal, and corundum ALPHA matching hold under adversarial probes. Two note-path defects (quartz wording on corundum unary rows; missing andalusite-below-Ky–And flag) should be fixed before treating metastability/subform notes as trustworthy; neither invents a wrong ΔrG today.

VERDICT: R24 | LAND-WITH-FIXES | P0=0 P1=0 P2=2 P3=1 | ran-tests | SHA=f7a79f2a32b1eb784bfc5639db483c5f7952bbe6
