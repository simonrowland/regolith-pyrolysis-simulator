# ThermoEngine patches

This is the simulator project's upstream contribution list for defects found at
the ThermoEngine boundary. Each entry separates the upstream defect from the
local compatibility code so fixes can be submitted without importing simulator
policy into ThermoEngine.

## 1. Oxygen-buffer options are accepted but not applied

**Symptom.** `MELTSmodel.equilibrate_tp()` builds calculation options containing
`imposeBuffer`, `buffer`, and `bufferOffset`, but changing them does not impose a
requested oxygen fugacity. A Fe-bearing equilibrium continues to follow its
closed-system ferric inventory.

**Minimal reproducer.** Run the same FeO/Fe2O3-bearing bulk at one T/P twice,
with `imposeBuffer=true`, `buffer=QFM`, and substantially different offsets.
Recover liquid FeO/Fe2O3 and calculate its Kress91 fO2. The requested offsets
do not produce the corresponding fO2 values. In source, `Equilibrate.m` defines
and stores the three keys, while `execute` reads only the ordinate and abscissa
options before constructing the nonlinear problem. The Python wrapper also
constructs `bufferOffset` with `numberWithBool`, not a floating-point NSNumber.

**Root cause.** The native solve never translates the stored redox options into
an open-O2/buffer constraint. The Python API therefore advertises dead
configuration.

**Local fix.** Commit `0d1796539437a36eff10199c75e76bb6b12534df` added a
Fe-conserving ferric-fraction bracket/root solve in
`engines/alphamelts/thermoengine.py`. It varies only FeO/Fe2O3, fully
equilibrates each trial, and requires the solved liquid Kress91 echo to match
the requested absolute fO2 within `1e-3`. Commit
`33beb2c0cc9391dda049eb5578e92c16db186a34` hardened seeds, roundoff handling,
and fail-closed diagnostics; `31d9176d8a26dad3a0226f4ef6c90b1424448f4e`
corrected the cross-engine parity gate.

**Upstream status.** Reproducer and source diagnosis ready; native patch not
yet implemented. The upstream change should apply the buffer constraint in
`Equilibrate.m::execute`, accept caller-selected options, construct a floating
offset, and regression-test that changing an offset changes equilibrium
chemistry and the solved fO2 echo. Intrinsic mode must remain unchanged.

## 2. Reused process emits history-dependent chemical potentials and formulas

**Symptom.** A 24-point cold-process versus reused-process gate differed in the
Spinel `chromite` chemical potential at points 8 and 14. Repeated observations
ranged from zero to about `1e302` J/mol. The same solved native state, queried
through a retained vector, returned physical values near `-1.93e6` J/mol.
Formula strings for absent phases also changed with process history. Phase
fractions, phase and liquid compositions, activities, fO2, numeric affinities,
and all other thermodynamic fields were identical.

**Minimal reproducer.** At 1360 C and 500 bar, equilibrate the basalt used by
`tests/test_engine_worker_live_determinism.py` at `log10(fO2/bar)=-8`. Compare
`get_thermo_properties_of_phase_components(root, "Spinel", mode="mu")` across
fresh processes, then compare it with a direct call on the same phase wrapper
while retaining the returned `DoubleVector`. The XML path produces allocator
garbage; the retained call is finite and stable. The committed 24-point gate
is enabled with `REGOLITH_RUN_ENGINE_DETERMINISM=1`.

**Root cause.** Eleven Objective-C solution classes take `pointerToDouble` from
a temporary `DoubleVector`, then dereference that pointer in later statements.
ARC may release the backing object after the full expression. The affected
implementations are listed in [the prepared upstream patch](thermoengine-mu-lifetime.patch).
Separately, several phase families use class-global `NSCountedSet` instance
counters keyed by `operationParent`. Phase objects initialize that property to
an empty string, then every Python `EquilibrateUsingMELTS*` constructor replaces
it with the same `"Python"` namespace. New `MELTSmodel` objects therefore share
coexistence/affinity state. No public reset or clear API exists, and
`initialize=True` resets T/P/options rather than these class globals.

**Flush/reset tests.** A 100% SiO2 point at 1500 C, a high-T all-liquid basalt
point, and a fixed reference point did not make native XML output match fresh
processes. Median flush costs were about 0.009 s, 0.012 s, and 0.086 s per call,
respectively. Fresh model construction was already in use and did not close
either native lifetime.

**Local fix.** The adapter now recomputes solution chemical potentials from the
same solved native phase while retaining the elements, moles, and chemical-
potential vector owners. Each external call also gets an isolated
`operationParent` namespace; final assemblage counts are balanced before that
namespace is discarded. Native signed-zero formula text is canonicalized. No
phase solution, mass balance, pin, or golden dataset is changed.

**Upstream status.** The 11-site chemical-potential patch is ready in
`docs/thermoengine-mu-lifetime.patch` (apply with
`git apply --unidiff-zero`). It copies values through the retained
`DoubleVector` API and avoids escaping a raw pointer. It requires an optimized
Release-build regression because ordinary ARC locals do not guarantee precise
lifetime. A follow-up native patch should give every `Equilibrate` instance a
private phase-counter namespace and balance counts on teardown, or expose an
explicit reset API. Submission and upstream CI are pending.
