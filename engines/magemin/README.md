# `engines/magemin/` — MAGEMin shadow provider

Source tree for the MAGEMin kernel-shadow provider. See
[binding spec §4 (MAGEMin)](../../docs-private/chemistry-engine-binding-spec-2026-05-14.md)
for the I/O contract and §3 for the authority matrix. Promotion path:
`\goal MAGEMIN-SHADOW-PARITY` in
[codex-goal-queue-2026-05-14.md](../../docs-private/codex-goal-queue-2026-05-14.md).

## Two paths, one chemistry call

| Path | Location | Status |
|------|----------|--------|
| **Today-hook adapter** | `simulator/melt_backend/magemin.py` | Live via `simulator/core.py::_get_equilibrium`. Subclass of `MeltBackend`. Calls MAGEMin binary. |
| **Kernel-shadow provider** | `engines/magemin/provider.py` | Scaffold. Wired post-`\goal CHEMISTRY-KERNEL-CARVE-OUT`. Delegates to the today-hook — no duplicate chemistry. |

`MAGEMinShadowProvider.delegate_to_adapter()` lazy-imports the today-hook so
both paths converge on one call site when the kernel intercepts.

## Authority

Shadow only. `is_authoritative_for(intent)` → `False` always. Provider
never emits `LedgerTransitionProposal`. AlphaMELTS retains authority for
`SILICATE_LIQUIDUS` / `SILICATE_EQUILIBRIUM` per binding spec §3.

## Parity tolerance

`MAGEMinParityComparator` (`parity.py`) vs the authoritative engine:

- liquidus delta: ±50 K
- modal abundance delta: ±2 wt% per phase

Disagreement → `agreement=False` + warnings. Never raises, never silently
averages (binding spec §7).

## Binary install

Compiled `MAGEMin` binary + libs under `engines/magemin/bin/` and
`engines/magemin/lib/` are gitignored. Only the provider/domain/parity
Python modules here are tracked.

Canonical local subprocess path: `engines/magemin/bin/MAGEMin`. Build the
MAGEMin source tree outside synced repo storage, then copy only the executable
to that path so `simulator/melt_backend/magemin.py::initialize()` finds it.
