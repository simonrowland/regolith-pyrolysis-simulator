# MAGEMin Build Convergence - 2026-05-20

## Build

- Source checkout: `/Users/simonrowland/.local/src/MAGEMin`
- Upstream ref: `v1.9.3` (`a55f476`)
- Build command: `make -j$(sysctl -n hw.ncpu) all`
- Build result: pass
- System installs performed: none

MAGEMin built with the upstream Darwin Makefile path: clang, macOS
Accelerate, Homebrew NLopt, and Homebrew MPI. No package install was required.

## Installed Binary

- Adapter binary path: `engines/magemin/bin/MAGEMin`
- Absolute path:
  `/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator/engines/magemin/bin/MAGEMin`
- Size: 36 MB
- SHA-256:
  `b6b68f98d1be22876c0c8826f17da2afa1c70a675087b11d142ea20045f7a580`
- Git status: ignored by `.gitignore`; not committed

The adapter needs no chemistry-logic change. Its existing discovery path checks
`engines/magemin/bin/MAGEMin`, and `MAGEMinBackend.initialize({})` now resolves
that binary with the subprocess bridge.

## Smoke Probe

Single-point CLI probe:

```shell
./MAGEMin --Verb=0 --db=ig --Temp=1200 --Pres=2 --sys_in=wt \
  --Bulk=49,14,11,9,10.9,0.8,2.5,1.5,0,0.2,0 \
  --buffer=qfm --buffer_n=0
```

Result: exit 0, parseable `Phase :` and `Mode :` block:

```text
Phase :      liq       ol      spl
Mode  :  0.96178  0.03608  0.00214
```

## Adapter Availability

`.venv/bin/python` probe:

- `initialize=True`
- `is_available=True`
- `bridge=subprocess`
- `binary_path=engines/magemin/bin/MAGEMin`

## Tests

- `tests/chemistry/test_magemin_shadow.py`: 15 passed, 0 skipped
- `tests/test_magemin_backend.py tests/chemistry/test_magemin_shadow.py`: 31 passed, 0 skipped
- Full suite: 765 passed, 96 skipped, 8 warnings in 91.74s

The MAGEMin availability-gated tests are un-skipped locally because collection
now finds the ignored binary at the adapter's canonical path. The skip guard
remains in source so environments without a local MAGEMin build still fail
closed.

## Shadow-vs-Authoritative Divergence

None observed in this build task. The real MAGEMin smoke tests verified
adapter execution and shadow-provider dispatch; no tolerance was changed and no
MAGEMin-vs-authoritative parity failure surfaced.
