# Draft upstream issue: standalone alphaMELTS 2.3.1 crashes on Fe-free melts with imposed absolute fO2

Status: owner-gated draft; not filed.

## Summary

The Apple Silicon standalone `alphamelts2` 2.3.1 executable exits by signal
when given a Fe-free melt **and** an imposed absolute fO2. The crash is not
a sparse/binary-composition defect: three-component `{SiO2, Na2O, TiO2}`
and Fe-free CMAS also die; the same compositions plus a trace of FeO
complete. A six-component basaltic control using the same executable, menu
sequence, writer, T/P/fO2 fields, and subprocess transport completes
normally.

## Environment

- macOS arm64
- release tag: `v2.3.1`, commit `957b8f514b9740a6a2edcf0eefcef543087657a0`
- executable SHA-256: `d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85`
- model: Rhyolite-MELTS 1.0.2 / `ALPHAMELTS_CALC_MODE=MELTS`
- run mode: one-step isobaric path, P=1 bar, absolute log10(fO2/bar)=-9

## Minimal reproduction

Use `minimal-repro/input.melts` and `minimal-repro/stdin.txt`:

```shell
alphamelts2 1 < stdin.txt
```

Observed: `SIGSEGV` (`returncode -11`) in about 0.02 s.

Expected: a phase-equilibrium result or an informative domain/convergence
error; the process must not terminate by signal.

## Measured trigger and scope

Harness measurements (2026-08-17):

- `{SiO2, Na2O, TiO2 0.05}` (three-component, Fe-free) **crashes**
- the same plus 0.05 FeO is **ok**
- Fe-free CMAS **crashes**
- CMAS plus 0.01 FeO is **ok**

So "two-component alkali-silica" / "sparse binary" is neither necessary nor
operative. The measured trigger is **Fe-free melt AND an imposed absolute
fO2**. Do not treat "no Fe" as a general refusal: Fe-free melts without an
imposed absolute fO2 can compute.

A **separate** Fe-bearing crash family exists below 34 wt% SiO2
(`engines/alphamelts/domain.py`). That family is not this report.

The 24-point Tsaplin Na2O-SiO2 rail that first surfaced the bug is one
instance of the Fe-free + imposed-fO2 family, not evidence that the engine
cannot handle a two-oxide file.

`NA-02` converts exactly to 25.2822397 wt% Na2O and 74.7177603 wt%
SiO2. Both names are native MELTS oxide components; the file uses the same
adapter serialization that succeeds for the basaltic control. The local
mitigation refuses this family before launch so the signal does not become
a harness death.

## Local mitigation

The simulator refuses the reproduced Fe-free + imposed-absolute-fO2 family
(matching scope: two-component Na2O-SiO2 and K2O-SiO2) before launch and
types the refusal as an engine-crash / engine-defect, not as
`out_of_domain`. Independent signal exits remain caught and returned as
typed diagnostics carrying signal, return code, composition, and
operating point. The matching scope is deliberately not widened to every
Fe-free melt: that would refuse inputs the engine can compute.

## Source and licensing note

The upstream repository is public and AGPL-3.0, but this workspace has no
alphaMELTS source checkout; only the compiled 2.3.1 app bundle is installed.
Therefore this package contains a bug-report draft, not a speculative binary
patch. Any future redistribution of a patched executable/source fork is gated
on project decision `q-004`; this draft makes no licensing-posture decision.
