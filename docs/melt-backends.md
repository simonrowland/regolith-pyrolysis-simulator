# Melt Chemistry Backends

The simulator can run without external thermodynamic software. The fallback path combines simplified Ellingham equilibrium logic with Antoine vapor-pressure data, which is useful for comparative exploration but not for validated melt chemistry.

## Backend Order

The alphaMELTS backend checks, in order:

1. `PetThermoTools` Python package.
2. Project-local alphaMELTS binary at `engines/alphamelts/run_alphamelts.command`.
3. `alphamelts` executable on `PATH`.

The VapoRock wrapper checks whether the `VapoRock` Python package is importable.

The FactSAGE/ChemApp backend is currently a stub. It documents the integration point but is not a working backend.

## Local alphaMELTS Path

For local binary use, put the executable at:

```text
engines/alphamelts/run_alphamelts.command
```

The `engines/` directory is ignored by git so local licensed or platform-specific binaries are not published.

## Python Packages

Install optional Python melt tooling with:

```bash
pip install -e ".[melts]"
```

The `melts` extra currently includes `PetThermoTools`. VapoRock may be installed separately if needed.

