# Static review: pin quality, changed tests, reactive oxygen path

Review target: `9be2c17266eec54bd7b23c1768a73d2192911f05`, compared with green `4355e163a` and pin `75e94a16c`. Read-only discovery; no tests run by this reviewer. Steer mailbox read repeatedly; empty throughout.

## Pin identity and quality

`git diff --stat 75e94a16c^ 75e94a16c` reports exactly one new file: `tests/battery/test_b693_b691_scorer_gates.py`, 478 insertions. `git diff 75e94a16c 9be2c1726 -- tests/battery/test_b693_b691_scorer_gates.py` is empty. Thus the pin is tests-only and was not edited afterwards. Parent reviewer must establish red/green results by execution.

The 8 pins use production `comparison_candidates`, `compile_residual`, `score_store`, `predict_with_engine`, `headline_rows`, and `headline_records`; none pastes the physics formula. However, they do not comprehensively pin the requested behavior:

- Figure candidate/count pins are real entry-point membership checks (test file:168,199).
- Band pin (178-196) injects a valid prediction and asserts only non-null band and `source-printed` rule, without asserting the numerical width. No absent-reading-uncertainty pin exists.
- Reactive compile pin (234-267) permits REFUSED provided the reason differs from `reactive_cell_oxygen_reservoir`. Direct prediction pin (270-293) similarly permits missing_fO2 and its `seen` assertion even permits `None` or any nonempty dict.
- The engine stub `_capture_cell` (test_silent_fills.py:67-97) ALWAYS returns status=refusal, reason=census_stop, no numbers. These pins cannot demonstrate a numeric reactive-cell prediction. The no-O2 fixture actually refuses missing_fO2 before opening the engine.
- Printed O2 pin (296-316) checks commanded mode and notice, but not the commanded number.
- Headline pins (325-478) construct Residual objects directly rather than score a Kume fixture. Membership/source-list assertions are valid aggregation tests; IQR is only checked for key existence, and no pin asserts both report lines printed for every rail/engine or unchanged certified bytes.
- No tests-only pin covers Ta/Nb/graphite/mixed sets, empty stored uncertainty, all headline implementations, or actual reactive-cell numeric prediction. Accordingly, not every behavior change was preceded by a strong failing pin.

## Fixture fidelity

The synthetic fixture (test_b693_b691_scorer_gates.py:110-160) has 20 Na+10 K figure rows, typed Re bench, injected +/-30% scalar-string uncertainty, no sample/series separation, and generic fully specified composition. `_partial_identity` (82-94) inherits `pref_identity`'s default fO2=100000 Pa (factories.py:281); F.observation stores that as a point condition (factories.py:522-523). These rows take the printed-O2 path and do not exercise the real no-O2 Knudsen case.

Measured via YAML parsing: committed raw De Maria 1971 extract contains 6 rejected digitized Na rows for sample 12022 and 10 for sample 12065, and zero digitized K rows. It records rejected_no_figure_reading, a mapping uncertainty with `dex:0.3` / `sigma_log10P_dex_per_point:0.3`, catalogue composition notices, and two distinct samples. See data/literature/extracts/kems-022-demaria-1971.yaml:1718 onward. The fixture is structurally suitable to isolate figure-only admission, but is not faithful enough to certify the extraction/uncertainty/series behavior.

## Every changed existing expectation

`tests/battery/test_score.py`: only `test_inferred_nonmodelled_cell_still_refuses_oxygen_balance` changed (green:7206; tip renamed at7206).

Before: production compile_residual with default production predictor; asserts status REFUSED, refusal present, exact reason reactive_cell_oxygen_reservoir, and CELL_MATERIAL_INFERRED notice.

After: supplied prediction stub manually unions `_reactive_cell_not_modelled_notice` (private production helper) into an injected prediction; asserts refusal absent OR reason differs from reactive_cell_oxygen_reservoir, preserves CELL_MATERIAL_INFERRED assertion, adds REACTIVE_CELL_NOT_MODELLED-kind OR reason-substring assertion (tip:7218-7253). Replacing obsolete refusal behavior is justified by b-693, but the replacement materially weakens production coverage: no status/numeric-success assertion, and notice origin is supplied by the test rather than discovered through the production predictor.

`tests/battery/test_silent_fills.py`: removes 8 parameter cases from the existing exact refusal test (green:404-422): Ta; Nb; C_graphite; Re; Ir+C_graphite; Ir+W; W+Re; Mo+Al2O3. Each previously asserted engine never opened and exact reason reactive_cell_oxygen_reservoir (green:445-446). Those eight move into new parameterized test at tip:429-465, which asserts reason differs from reactive_cell_oxygen_reservoir, required notice substring, and `(reason in {None, missing_fO2}) OR (seen.mode in {not_an_input, commanded})`.

The removal is justified by predict-and-flag policy, but the replacement is weaker: it never requires produced value, successful execution, exact missing-input refusal, no engine call for missing required oxygen, or exact pO2 forwarding. Since these fixtures are p_partial with no O2, the expected deterministic result is typed missing_fO2. The broad OR would allow unrelated refusals if a commanded/omitted mode was captured. Existing exact missing_fO2/default-input tests elsewhere in this file are unchanged. Remaining Al2O3, Pt+Al2O3, OTHER exact not_inert refusals are unchanged.

## Reactive classes and oxygen numbers

score.py:2564-2588 classification: no materials or any unavailable/non-typed material -> unknown; any W/Mo/Ta/Nb/C_graphite/Re in a fully typed set -> reactive; all Pt/Ir -> inert; everything else -> not_inert. `_MODELLED_REACTIVE_CELL` at2591 contains only W/Mo; `_uniform_modelled_reactive_cell` at2594-2615 requires every entry to be the same metal. Uniform W and uniform Mo remain modeled. Re/Ta/Nb/graphite and any mixed reactive set, including W+Mo, W+Pt, and Mo+Al2O3, are unmodeled and flagged.

Without printed O2: reactive unmodeled branch score.py:3230-3260 appends notice, then commands identity fO2_Pa /100000 if a value exists; otherwise oxygen-required inputs refuse typed IDENTITY_INCOMPLETE/missing_fO2, or oxygen-not-required chooses Po2Request(not_an_input,None) plus omission notice. P_PARTIAL profile always requires fO2 (identity.py:581-589), so truly missing O2 on the requested gas-pressure rows produces no prediction and uses NO numerical default. `_fo2_log_for_request` (binary_pot_battery.py:3335-3345) maps not_an_input to None; legacy -9 log10(bar)=1e-9 bar=1e-4 Pa default exists only for engine-default request, which scorer does not select. OpenIMCC's direct gas branch runs only with non-None fO2_log (binary_pot_battery.py:2034-2054); it does not silently manufacture an oxygen scalar in the omitted path.

The cell notice (score.py:2526-2551) states only unmodeled oxygen balance and material names, not an oxygen number. If `_has_printed_fo2` rejects a stored scalar as derived (2724-2735), new reactive branch nevertheless commands that scalar (3238-3241), with no additional provenance/number in this notice; parent should check actual changed store rows for this case.

Unknown no-O2 cells still refuse IDENTITY_INCOMPLETE/cell_material_unknown; this is genuine missing apparatus input to the oxygen solve. not_inert still refuses UNSUPPORTED/cell_material_not_inert (3261-3289), even for typed known Al2O3. That is a preexisting unsupported-apparatus gate; the report does not establish why it is permissible under the broad owner rule that out-of-domain physics predicts and flags. With printed O2 neither branch refuses for these classes; the material is no longer needed for an oxygen-balance solve.

## Additional band risk found statically

`_printed_uncertainty_band` (score.py:1897-1907) accepts only PRINTED scalar-string verbatim, not the stored mapping dex uncertainty above. For figure-only rows compile_residual does `printed_band or cell_band` (4477-4483). `cell_band` can be the global KEMS band derived from OTHER measured observations (4463-4465; also supplied by score_store:4842-4844). Therefore a figure-only row with absent/unparseable reading uncertainty can receive another source's derived band rather than NO_BAND. This needs an executable regression probe; no existing new pin checks it.
