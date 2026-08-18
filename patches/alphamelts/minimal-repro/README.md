# alphaMELTS 2.3.1 Fe-free + imposed-absolute-fO2 crash

Run from this directory on Apple Silicon macOS:

```shell
../../../engines/alphamelts/alphamelts-app-2.3.1-macos-arm64/alphamelts_macos 1 < stdin.txt
```

Observed with executable SHA-256
`d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85`:
process exits by `SIGSEGV` (`-11`) in about 0.02 s.

The input is the exact file emitted by the simulator adapter for Tsaplin
fixture `TSAPLIN00-T1-NA-02`: x(Na2O)=0.247, x(SiO2)=0.753,
T=1273 K, P=1 bar, absolute log10(fO2/bar)=-9.
