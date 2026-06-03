# FactSAGE melt backend — attic

The FactSAGE / ChemApp melt backend was removed from the live tree but
**preserved here for later re-integration**. It is a real ChemApp adapter;
it is unreachable in this environment (no `chemapp` Python module, no `.cst`
datafile / license), so it was carrying no load while still wiring import
paths and a UI option that could be selected and silently fall back.

History is preserved: the four backend files were moved with `git mv`, so
`git log --follow` works on each.

## Files moved here (with their original live paths)

| File in `_attic/factsage/`   | Original live path                              |
|------------------------------|-------------------------------------------------|
| `factsage.py`                | `simulator/melt_backend/factsage.py`            |
| `factsage_config.py`         | `simulator/melt_backend/factsage_config.py`     |
| `factsage_doctor.py`         | `simulator/melt_backend/factsage_doctor.py`     |
| `test_factsage_backend.py`   | `tests/test_factsage_backend.py`                |

`simulator/melt_backend/installer.py` was **not** moved — it serves several
backends (PetThermoTools, VapoRock, alphaMELTS, ChemApp probe). Only its
FactSAGE-specific parts were edited out; that edit is captured in the patch.

## What was removed from the live wiring

The web autodetect fallback chain went from **AlphaMELTS → FactSAGE → Stub**
to **AlphaMELTS → Stub**. The runner-strict name switch and all `factsage`
imports were dropped, the `--backend` CLI `choices` lost `factsage`, and the
`factsage` UI `<option>` was removed.

After removal, an explicit request for backend `factsage` fails loudly,
consistent with any other unknown name:
- `--backend=factsage` (runner.py / session_cli.py) is rejected by argparse
  (`invalid choice`).
- `resolve_backend('factsage', RUNNER_STRICT)` raises
  `BackendUnavailableError: unknown backend 'factsage'`.
- Under `WEB_AUTODETECT`, `factsage` is an unknown name and falls through to
  the AlphaMELTS→Stub autodetect chain (the UI no longer offers it).

> Note: a generic `factsage_equilibrium_phase_update` ledger-transition name
> string still exists in `simulator/core.py` and two kernel tests. That is a
> backend-agnostic `BACKEND_EQUILIBRIUM` transition label, **not** the FactSAGE
> module — it was intentionally left untouched so the mol-native mass-balance
> closure stays intact. It is not part of the reintegration patch.

## Reintegration recipe

1. Restore the four moved files to their original live paths (table above),
   e.g. `git mv _attic/factsage/factsage.py simulator/melt_backend/factsage.py`
   (and the other three).
2. Reverse-apply the wiring patch from the repo root to re-add all shared-file
   wiring (imports, autodetect chain, CLI choices, UI option, test
   expectations):

   ```bash
   git apply -R _attic/factsage/REINTEGRATION.patch
   ```

   `REINTEGRATION.patch` is the `git diff` of the shared-file de-wiring only
   (the moved `.py` files are preserved verbatim here, so they are not in the
   patch). The reverse-apply was dry-run verified (`git apply -R --check`)
   clean at removal time.
3. Optionally drop `_attic` from `norecursedirs` in `pyproject.toml` and delete
   `_attic/factsage/` once everything is back in the live tree.
