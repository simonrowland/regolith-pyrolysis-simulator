# STATUS: fix de-maria-1969-rend-lincei (corpus batch 6, section A)

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~21:25 ET
review applied: `regolith-main-corpus-batch6-2026-10-05/de-maria-1969-rend-lincei/review.md` (grok; 150 rows, 1 mismatch, 1 printed number not carried; FIX-FIRST on 6dff2a54)

## Tips
| Field | Value |
|---|---|
| Branch | `hunt/de-maria-1969-rend-lincei` on the mirror (mac-studio-256-1:Repos/regolith-corpus.git) |
| Start tip (verified = origin before work) | `6dff2a540eb336532cfa162b7ee7f6202c436e13` |
| **New tip (ls-remote verified)** | **`4b423e05cb40ddd59be05c27bf1ad61f80299f86`** (one commit, parent 6dff2a54; fast-forward, no force-push) |
| Files changed | `extracts/de-maria-1969-rend-lincei.yaml`, `ledger/de-maria-1969-rend-lincei.yaml` (explicit pathspecs, no trailers) |
| Extract sha256 at new tip | `28b00bafd4569052782d3562f6687233d2bfa25e4114b54837b356b9e8d2e5a7` (the file validated and migrated below) |
| Worktree | sparse `~/Repos/regolith-corpus/worktrees/fix-b57-de-maria-1969-rend-lincei` (~51 MB, SEAT-COMMON recipe), removed after delivery |
| Mirror main | not touched (main merges) |

**items fixed 2/2, corrections 2 (1 reclass + 1 carried number), hard issues 0.**

Main merges directly after this fix, with no confirm review. **I checked every item myself** against fresh `pdftoppm -png -r 250` renders of PDF pp. 13-14 (printed 535-536), plus the reference list on p. 536 for refs. 6 and 17.

## Per-item table
| # | Review item | What changed | Page evidence (read from the 250 dpi render) |
|---|---|---|---|
| 1 | p. 536 (PDF 14): carry the 1700 K in the conclusions sentence | New context row `demaria1969_oxygen_free_vaporization_conclusion` (type `author_conclusion`, experiment `demaria1969-mo-kems-stepwise`, locator page 14 / published 536 / Conclusions, with a note that the sentence starts on p. 535). It holds the full sentence verbatim, `temperature_K_as_printed: 1700`, qualifier "as low as" (author's argument for neutral conditions, not a measured onset). `method_class`/`evidence_class: author_estimate`. `why_context_not_scored`: no O was detected (p. 535) and the Mo cell took up the sample oxygen. Ledger note updated to 141 context rows (6 prose/figure rows). | p. 535 bottom → p. 536 top: "Nevertheless it is legitimate to argue that a respectable amount of the oxygen in the sample could, in neutral conditions, vaporize in free form at a temperature as low as 1700° K. Furthermore, if the possible recombination of oxygen with other molecular species in its collision with the surface of the container is not excessive, oxygen could be obtained in elemental form." |
| 2 | p. 535 (PDF 13): `demaria1969_partial_pressure_activity_estimates` was classed measured_direct; reclass as calculated with relation, inputs and derived_from | `method_class: calculated`, `evidence_class: calculated` (was measured_direct/measured_direct). Added a row-level `derivation`. Its `relation` is a_i ≈ p_i / p_i°(T), with p_i° the pure-element vapour pressure from Nesmeyanov ref. 17. It says the relation is implied by the print, and that no equation, no p_i° values and no arithmetic are printed or supplied. Its `inputs` are the printed orders (Na 10^-5 atm and K 10^-4 atm at 1200 K, "relative values"; Fe 5·10^-5 atm at 2000 K), the procedure of ref. 6 (not printed here) and the Nesmeyanov ref. 17 vapour pressures (not printed). `output_unit` is set. Also added: `derived_from` pointing at the p. 535 Results prose; `partial_pressure_basis_as_printed` ("estimated following the procedure described in reference 6"); `activity_standard_state: pure element (ref. 17)`. Every printed number on the row is unchanged: Na/K/Fe pressures and temperatures, and activities 1e-5 / 1e-6 / 4e-2. No intermediate step was invented. | p. 535: "Partial pressures of the observed species were estimated following the procedure described in reference 6. The relative values for Na and K at 1200° K are of the order 10^-5 atm. and 10^-4 atm. and for Fe one obtains 5·10^-5 atm. at 2000° K. From these values one calculates an order of 10^-5, 10^-6 and 4·10^-2 for the activity of Na, K and Fe respectively, utilizing the vapor pressure data revised by Nesmeyanov [17]." p. 536 refs: [6] G. De Maria, M. Guido, L. Malaspina and B. Pesce, J. Chem. Phys. 43, 4449 (1965); [17] A. N. Nesmeyanov, *Vapor Pressure of the Chemical Elements*, Elsevier, Amsterdam 1963. |

The review found no other mismatches. All 133 Table I cells, the 11 Table II rows, the bench, the typed absences, the citation, the licence and the sha256 matched, and I did not touch them.

Notes on how I recorded the changes:
- On a context row the migrator copies `derived_from` and `derivation` through verbatim (`_lift_extract_context`). So the prose `derived_from` does not mint an unresolvable pointer or raise a queue entry, and the class change scores nothing. The row stays context-only, as before.
- I did not resolve or comment on whether the printed activities follow from the printed pressures, because the p_i° values are not printed. That is left to the reader.

## Checks (Mac; green read-only checkout `/Users/simonrowland/ci-scratch/regolith-green-ro` @ `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`; interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH` = that green checkout, `PYTHONDONTWRITEBYTECODE=1`, cwd = the sparse corpus worktree)
| Check | Result |
|---|---|
| `tools/validate_literature_extracts.py extracts/de-maria-1969-rend-lincei.yaml --check-fidelity-match` | **OK: 1 extract file(s) valid** |
| `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(...)`, then `finalize()` | observations 0; context **141**; benches 1; experiments 1; registry issues 0; validation issues 0; **hard issues 0**; queue 1 ("extract yielded no observations", expected and unchanged) |
| `python -m pytest tools/test_ledgers_valid.py -q` (corpus worktree) | **715 passed** |
| `rg "/Users/|/private/"` in changed files | no matches |
| `engines/engines.local.toml` in the green checkout | **exists** (`/Users/simonrowland/ci-scratch/regolith-green-ro/engines/engines.local.toml`) |
| `tools/build_index.py`, `tools/migrate_pilot_extracts.py` | not run (sparse tree); INDEX not regenerated, so regenerate it in a full clone at merge |

counts: items fixed 2/2, corrections 2, rows now 151 (133 Table I + 11 Table II + 7 other context rows), mismatches open 0, printed numbers not carried open 0, hard issues 0.

## D-062 REVIEWER CHECKLIST (data only)
1. Second copy of logic? No code changed; not applicable.
2. Rule or physics in the presentation/wiring layer? Not applicable; no code.
3. Forbidden import or cycle? Not applicable; no code.
4. Behaviour-preserving move pinned first? Not applicable.
5. Relaxed a guard or added a baseline entry? No.

## P0 / blocked
None.

— regolith-empirical
