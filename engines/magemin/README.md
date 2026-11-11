# `engines/magemin/` — MAGEMin provider

Source tree for the MAGEMin kernel provider.

| Path | Location | Role |
|------|----------|------|
| Backend adapter | `simulator/melt_backend/magemin.py` | `MeltBackend` subclass that calls the MAGEMin binary. |
| Kernel provider | `engines/magemin/provider.py` | `MAGEMinShadowProvider`, registered with the kernel; delegates to the backend adapter, so there is no duplicate chemistry. |

The provider can register without the MAGEMin binary present. It constructs
and initialises the backend adapter lazily on first dispatch, and returns
`status='unavailable'` if that fails.

## Authority

- Shadow for `SILICATE_LIQUIDUS` and `SILICATE_EQUILIBRIUM`. AlphaMELTS keeps
  authority for those intents.
- Fallback-authoritative for `GATE_LIQUID_FRACTION`.
- The provider never emits a `LedgerTransitionProposal`.

## Parity tolerance

`MAGEMinParityComparator` (`parity.py`) compares against the authoritative
engine:

- liquidus delta: ±50 K
- modal abundance delta: ±2 wt% per phase

A disagreement sets `agreement=False` and adds warnings. The comparator never
raises and never silently averages.

## Binary install

The source checkout and compiled artifacts live under
`~/repos/engine-bin/MAGEMin/<commit>/`, as declared in
`engines/engines.lock.toml`. Run `install-engines.py` to clone and build the
locked revision; it records the exact executable path in the checkout's
generated `engines/engines.local.toml`. Only the provider, domain and parity
Python modules in this directory are tracked.
