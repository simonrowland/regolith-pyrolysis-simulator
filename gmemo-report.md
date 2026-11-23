# Freeze-gate liquidus curve memo

## Liquidus input trace

`PyrolysisSimulator._melt_redox_liquidus_gate_record` in `simulator/core.py` resolves the current pressure and referenced melt fO2/redox state, builds the keyed memo lookup, then calls `_freeze_gate_curve_record` on a miss. That method in `simulator/evaporation.py` builds its composition from the `process.cleaned_melt` ledger account. `_freeze_gate_curve_from_gate_dispatch` dispatches `GATE_LIQUID_FRACTION`; the provider account filter exposes only that cleaned melt.

Both `MAGEMinShadowProvider._run_liquidus_finder` and `AlphaMELTSProvider._run_liquidus_finder` forward `pressure_bar`, `fO2_log`, `redox_state`, `composition_mol_by_account`, and `species_formula_registry` to `find_liquidus_solidus`. The backends supply their selected model, mode, database, binary, and finder controls. The request contains no SulfSat gate, sulfur inventory, or sulfur-side provider selection. The per-batch Langmuir redox-capacity caller can request the liquidus curve, but it adds no SulfSat input to the finder.

The existing curve key retains the cleaned-melt oxide fractions at the 1e-4 quantum, water fraction, formula registry identity, inert species, pressure, referenced fO2, and redox-state identity. Producer configuration retains namespace, vapor provider selection, model, mode, database, and redox identity. Selected engine/binary identity remains in producer evidence checked by binding admission.

## SulfSat history

`git log -S sulfsat_available -- simulator/reduced_real_determinism.py` traces the full sulfur-side dependency vector to replay determinism work (`90e53d409e`). `a75b19d951` projected that vector into the gate-curve producer configuration and retained SulfSat availability/provider selection; its regression test required a different SulfSat gate to reject memo admission. `b463a8a0f4` preserved sulfur-side state in replay scope. That history guards replay identity, not a liquidus input.

The change removes only SulfSat fields from `gate_curve_memo_producer_configuration`. `record_dependency_vector` still carries the complete `sulfur_side` for replay identity, as the updated regression test checks.

## Clone sharing and memo eligibility

`_build_c0_forecast_context` now places the live `_freeze_gate_liquid_fraction_curve_memo` dict in the deepcopy identity map. The forecast clone and live simulator therefore use the same successful-curve memo; the clone still gets its copied state, private ledger, independent provider registry, and forecast store. Clone construction requires a fresh backend of the same type and engine name. Memo admission still compares producer configuration and evidence, so a different model/mode/database, selected provider, redox basis, or binary identity cannot reuse an entry. No other mutable forecast state is shared. The deliberate `_sulfsat_gate = None` clone change does not reach the finder input list above.

The memo remains limited to solved curves. `_freeze_gate_curve_is_solved` rejects Kress91 floor curves and records carrying `gate_provider_refusal`; held records and typed refusals remain outside the solved memo. Tick-local cache behavior is retained for a stable floor within its current tick. The same solved-curve predicate guards the core memo and caller-owned shared curve cache. `gate_curve_memo_is_cacheable` remains for the distinct tick-local and PT0 replay/capture rules.

The cache-rule consolidation is in `EvaporationMixin._freeze_gate_curve_is_solved`, used by both core and evaporation memo paths. `record_dependency_vector` remains the sole full replay dependency vector; its sulfur fields are intentionally retained.

## Tests and expected results

- `tests/test_session.py` wraps `MAGEMinBackend.find_liquidus_solidus` with a counter. It asserts one search across startup forecast, `step()`, and direct reforecast, then a second search after the Na2O transfer changes composition. It also compares the startup clone's curve with the live memo curve. The test is marked `live_engine`, `serial`, and the serial xdist group because it exercises the live MAGEMin path.
- `tests/chemistry/test_evaporation_freeze_gate.py` checks that SulfSat remains in the full replay vector while no longer splitting gate-curve producer configuration, and that a stable out-of-domain Kress floor stays out of the solved/shared memo.
- No pytest or remote CI was run: the brief's CI HOLD explicitly forbids both. The controller can run these exact whole-file commands when the hold lifts:

  ```sh
  "$GOALFLIGHT_REMOTE_RUN" --kind gate --cmd-id regolith-focused-tests -- python3 -m pytest -q -p no:cacheprovider tests/test_session.py
  "$GOALFLIGHT_REMOTE_RUN" --kind gate --cmd-id regolith-focused-tests -- python3 -m pytest -q -p no:cacheprovider tests/chemistry/test_evaporation_freeze_gate.py
  ```

No golden or digest files changed. The expected golden move is **NONE**; byte-identical receipts remain for the controller to verify after the hold lifts. `engines/engines.local.toml` was copied into this seat and remains gitignored. This dispatch started no native engine. Both required process checks (`pgrep -fl alphamelts` and `pgrep -fl MAGEMin`) were refused by the host with `sysmond service not found` / `Cannot get process list`; the controller must perform the process check.
