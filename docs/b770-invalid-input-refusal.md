# b-770 producer inventory

Inventory captured before implementation at `e4fc365b75527c1710906b8a7f42f50ea334691b`.

## Invalid declared input misclassified as `not_converged`

- `simulator/melt_backend/liquidus.py:344-354`, via `_not_converged`: bad scalar conversion, non-finite temperature/step/tolerance, inverted temperature window, non-positive scan step/tolerance, and invalid budget. These are caller-declared inputs and must be refusals.
- `simulator/melt_backend/liquidus.py:600`: inverted equilibrium-crystallization interval is also caller-declared invalid input.
- `simulator/melt_backend/magemin.py:1405`: the `_MAGEMinEngineCallError` catch currently returns `not_converged` for both declared scalar conversion errors from `_admit_magemin_call_scalars` and engine/output failures. The declared scalar subset is invalid input; the other failures remain `not_converged`.
- `simulator/melt_backend/magemin.py:1570, 1584, 1589`: invalid, non-numeric, non-finite, or non-positive configured liquidus budget is invalid input.

## Genuine engine/finder non-convergence to preserve

- `simulator/melt_backend/liquidus.py:486`: no solidus/liquidus bracket found.
- `simulator/melt_backend/liquidus.py:512`: finder aggregate budget exhausted during solve.
- `simulator/melt_backend/liquidus.py:540`: untyped sample/finder exception after typed refusal extraction.
- `simulator/melt_backend/liquidus.py:551`: computed liquidus falls below solidus.
- `simulator/melt_backend/liquidus.py:649`: untyped equilibrium-crystallization sampler exception.
- `simulator/melt_backend/magemin.py:1438`: bridge explicitly reports `converged=False`.
- `simulator/melt_backend/openimcc_bridge.py:869`: bridge engine call failure.
- `simulator/melt_backend/alphamelts.py:2337, 2345`: AlphaMELTS failure disposition/output status.
- `simulator/melt_backend/vaporock.py:1875, 1968`: projection/speciation did not produce a usable result. These are produced-result failures, not declared caller scalar admission.
- `simulator/melt_backend/liquidus.py:930`: shared status helper; its callers include both the invalid finder inputs above and runtime convergence failures, so the invalid-input branches must stop using it.

## Other `simulator/` status emitters

- `simulator/vapour_rail/engine_crosscheck.py:377` and `simulator/vapour_rail/calibration.py:1593` translate engine outcomes for crosscheck/calibration output; no declared invalid-input producer was found at these sites.
- `simulator/optimize/evaluate.py:1426, 1430, 1436` and `simulator/optimize/pool.py:1379` serialize solver/worker failure dispositions; these are consumers/translators, not raw declared-input validation sites.

No invalid-input-style member exists in `simulator/fidelity_vocabulary.py` or `simulator/battery/enums.py`. The existing canonical `refused` status is already accepted by the liquidus refusal vocabulary and scorer dispatch, and is the typed refusal outcome used for an engine that declines an invalid call. Implementation will use that status and preserve the reason in diagnostics rather than introduce a parallel status token.

## Audit addendum

The implementation pass also found that MAGEMin's scalar admission converted declared values but did not reject non-finite temperature, pressure, fO2, or explicit call-timeout values. Those values now fail admission as invalid input before invoking the engine. A zero remaining timeout continues to mean an exhausted aggregate budget and remains `not_converged`.
