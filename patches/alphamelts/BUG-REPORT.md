# Draft upstream issue: standalone alphaMELTS 2.3.1 crashes on binary alkali-silica inputs

Status: owner-gated draft; not filed.

## Summary

The Apple Silicon standalone `alphamelts2` 2.3.1 executable exits by signal
when given syntactically valid two-component Na2O-SiO2 or K2O-SiO2 `.melts`
inputs. A six-component basaltic control using the same executable, menu
sequence, writer, T/P/fO2 fields, and subprocess transport completes normally.

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

## Broader evidence

The 24-point Tsaplin binary-alkali rail produced:

- 3 clean simulator refusals before engine launch (SiO2 or temperature gate),
- 6 `SIGSEGV` exits (`NA-02` through `NA-07`),
- 15 `SIGABRT` exits (the remaining engine-invoked Na/K points).

`NA-02` converts exactly to 25.2822397 wt% Na2O and 74.7177603 wt%
SiO2. Both names are native MELTS oxide components; the file uses the same
adapter serialization that succeeds for the basaltic control. This localizes
the defect to the engine's handling of a sparse/binary composition rather than
to malformed `.melts` syntax.

## Local mitigation

The simulator now refuses the reproduced two-component Na2O-SiO2 and
K2O-SiO2 boundary before launch. Independent signal exits remain caught and
returned as typed diagnostics carrying signal, return code, composition, and
operating point.

## Source and licensing note

The upstream repository is public and AGPL-3.0, but this workspace has no
alphaMELTS source checkout; only the compiled 2.3.1 app bundle is installed.
Therefore this package contains a bug-report draft, not a speculative binary
patch. Any future redistribution of a patched executable/source fork is gated
on project decision `q-004`; this draft makes no licensing-posture decision.
