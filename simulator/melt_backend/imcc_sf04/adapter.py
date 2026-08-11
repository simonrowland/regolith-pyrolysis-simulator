"""IMCC-SF04 domain adapter: JSON datapack loader and caller-facing API.

Chunk 3 of the upstream IMCC-SF04 lane. Wraps the kernel from
``simulator.melt_backend.imcc_sf04.kernel`` with a JSON datapack loader, a
wt-to-mol basis converter, and a canonical trust-vocabulary label block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

import numpy as np

from simulator.backend_names import canonical_backend_name
from simulator.fidelity_vocabulary import backend_name_denies_authority
from simulator.melt_backend.imcc_sf04.kernel import (
    ImccComponentOutsideDomainError,
    ImccCompositionIncompleteError,
    ImccDatapack,
    ImccFerricInputUnsupportedError,
    ImccNonconvergenceError,
    ImccRefusal,
    ImccResult,
    ImccTOutsideDatapackDomainError,
    solve_imcc_sf04,
)


# Stable oxide molar masses (g/mol) for the 8 IMCC-SF04 parent oxides.
# Values from the project physical-constants table (CIAAW/NIST derived).
_OXIDE_MOLAR_MASS_G_MOL = MappingProxyType(
    {
        "SiO2": 60.0843,
        "MgO": 40.3044,
        "FeO": 71.844,
        "CaO": 56.0774,
        "Al2O3": 101.9613,
        "TiO2": 79.866,
        "Na2O": 61.9789,
        "K2O": 94.196,
    }
)

_EXPECTED_PARENT_OXIDES = (
    "SiO2",
    "MgO",
    "FeO",
    "CaO",
    "Al2O3",
    "TiO2",
    "Na2O",
    "K2O",
)

# Canonical trust vocabulary for the IMCC-SF04 diagnostic shadow.
# The spec r2.1 names this evidence class "diagnostic-shadow". The repo's
# canonicalization surface (``simulator.backend_names.canonical_backend_name``)
# and the structural certification denylist
# (``simulator.fidelity_vocabulary.CERTIFICATION_DENYLIST``) use
# ``internal-analytical`` as the equivalent denylisted token. We route through
# the real surface and adapt to it.
_IMCC_EVIDENCE_CLASS_INPUT = "internal-analytical"
_IMCC_EVIDENCE_CLASS_CANONICAL = canonical_backend_name(_IMCC_EVIDENCE_CLASS_INPUT)
assert _IMCC_EVIDENCE_CLASS_CANONICAL is not None
assert backend_name_denies_authority(_IMCC_EVIDENCE_CLASS_CANONICAL)


class ImccMalformedDatapackError(ImccRefusal):
    """Raised when a datapack JSON file is malformed or schema-invalid."""

    code = "imcc_malformed_datapack"


@dataclass(frozen=True)
class ImccLoadedDatapack:
    """Adapter-level loaded datapack: kernel datapack plus provenance metadata."""

    kernel_datapack: ImccDatapack
    version: str
    parent_oxides: Sequence[str]
    domain_basis: Sequence[str]


@dataclass(frozen=True)
class ImccAdapterLabels:
    """Three-field label block required by spec section 7."""

    identity: Mapping[str, str]
    coverage: Mapping[str, str]
    trust: str


def _as_fraction(value: Any) -> Fraction:
    """Parse a JSON numeric value as an exact rational."""
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, (int, float)):
        return Fraction(str(value))
    raise TypeError(f"cannot parse {value!r} as a rational")


def _build_nu_vector(
    parent_oxides: Sequence[str], nu: Mapping[str, Any]
) -> list[float]:
    """Return a nu column aligned to parent_oxides, preserving exact rationals."""
    return [float(_as_fraction(nu.get(name, 0))) for name in parent_oxides]


def _wt_to_mol(vector: np.ndarray, parent_oxides: Sequence[str]) -> np.ndarray:
    """Convert a mass-fraction vector (g) to a mole vector (mol)."""
    molar_masses = np.array(
        [_OXIDE_MOLAR_MASS_G_MOL[name] for name in parent_oxides],
        dtype=float,
    )
    return vector / molar_masses


def load_datapack(path: str | Path) -> ImccLoadedDatapack:
    """Load an IMCC-SF04 datapack JSON into the kernel datapack object.

    Validates the schema: 8 parent oxides, 38 complex rows, a version string,
    per-row ``T_domain_K`` intervals, and their ``T_domain_basis``.
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ImccMalformedDatapackError(
            f"datapack JSON at {path} is not valid JSON"
        ) from exc
    except FileNotFoundError as exc:
        raise ImccMalformedDatapackError(
            f"datapack file not found: {path}"
        ) from exc

    version = data.get("imcc_sf04_datapack_version")
    if not isinstance(version, str) or not version:
        raise ImccMalformedDatapackError(
            "datapack missing 'imcc_sf04_datapack_version' string"
        )

    parents = data.get("parents")
    if not isinstance(parents, list) or parents != list(_EXPECTED_PARENT_OXIDES):
        raise ImccMalformedDatapackError(
            f"datapack parents {parents!r} do not match expected "
            f"{_EXPECTED_PARENT_OXIDES!r}"
        )
    parents = tuple(parents)

    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != 38:
        raise ImccMalformedDatapackError(
            "datapack must contain exactly 38 rows, got "
            f"{len(rows) if isinstance(rows, list) else None}"
        )

    reactions: list[str] = []
    nu_cols: list[list[float]] = []
    A: list[float] = []
    B: list[float] = []
    domains: list[tuple[float, float]] = []
    domain_basis: list[str] = []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ImccMalformedDatapackError(f"row {idx} is not an object")

        complex_name = row.get("complex")
        if not isinstance(complex_name, str):
            raise ImccMalformedDatapackError(
                f"row {idx} missing 'complex' string"
            )
        reactions.append(complex_name)

        nu = row.get("nu")
        if not isinstance(nu, dict):
            raise ImccMalformedDatapackError(
                f"row {idx} missing 'nu' object"
            )
        nu_cols.append(_build_nu_vector(parents, nu))

        A_val = row.get("A")
        B_val = row.get("B")
        if not isinstance(A_val, (int, float)) or not isinstance(
            B_val, (int, float)
        ):
            raise ImccMalformedDatapackError(
                f"row {idx} A/B must be numeric"
            )
        A.append(float(A_val))
        B.append(float(B_val))

        t_domain = row.get("T_domain_K")
        if not isinstance(t_domain, list) or len(t_domain) != 2:
            raise ImccMalformedDatapackError(
                f"row {idx} missing valid T_domain_K [low, high]"
            )
        domains.append((float(t_domain[0]), float(t_domain[1])))

        basis = row.get("T_domain_basis")
        if not isinstance(basis, str):
            raise ImccMalformedDatapackError(
                f"row {idx} missing T_domain_basis string"
            )
        domain_basis.append(basis)

    # nu_cols is (n_complexes, n_parents); transpose to kernel shape.
    nu_array = np.array(nu_cols, dtype=float).T

    kernel_datapack = ImccDatapack(
        reactions=reactions,
        nu=nu_array,
        A=np.array(A, dtype=float),
        B=np.array(B, dtype=float),
        domains=domains,
        version=version,
        parent_oxides=parents,
    )

    return ImccLoadedDatapack(
        kernel_datapack=kernel_datapack,
        version=version,
        parent_oxides=parents,
        domain_basis=tuple(domain_basis),
    )


def evaluate(
    composition: Mapping[str, float] | Sequence[float],
    T_K: float,
    pack: ImccLoadedDatapack | ImccDatapack,
    *,
    basis: float | None = None,
    basis_type: str = "mol",
    extra_mol: Mapping[str, float] | None = None,
    allow_extrapolation: bool = False,
    tol: float = 1.0e-12,
    max_iter: int = 100,
) -> ImccResult:
    """Caller-facing IMCC-SF04 evaluation.

    Accepts a composition on either a mol or wt basis with a declared basis.
    Converts wt-to-mol before any cache boundary, validates the FeO-equivalent
    contract, and delegates to the kernel.

    Parameters
    ----------
    composition:
        Dict keyed by parent-oxide name, or a numeric vector aligned with the
        pack's parent oxides.
    T_K:
        Temperature in Kelvin.
    pack:
        Loaded datapack (``ImccLoadedDatapack``) or a raw kernel datapack.
    basis:
        Declared normalization basis in the same units as ``basis_type``. If
        ``None``, the composition sum is used.
    basis_type:
        ``"mol"`` or ``"wt"``.
    extra_mol:
        Additional components in mol (e.g. Tier-B/C screens). Positive Fe2O3
        raises ``ImccFerricInputUnsupportedError``; other positives outside the
        parent basis raise ``ImccComponentOutsideDomainError``.
    allow_extrapolation:
        If ``True``, evaluate outside declared T domains and mark the result
        as extrapolated.
    tol:
        Infinity-norm residual convergence tolerance passed to the kernel.
    max_iter:
        Newton iteration ceiling passed to the kernel.

    Returns
    -------
    ImccResult
        Kernel result with the adapter's three-field label block installed.
    """
    # Resolve the kernel datapack and metadata.
    if isinstance(pack, ImccLoadedDatapack):
        kernel_pack = pack.kernel_datapack
        pack_version = pack.version
        parent_oxides = pack.parent_oxides
    else:
        kernel_pack = pack
        pack_version = pack.version
        parent_oxides = pack.parent_oxides

    if basis_type not in ("mol", "wt"):
        raise ImccCompositionIncompleteError(
            f"basis_type must be 'mol' or 'wt', got {basis_type!r}"
        )

    # Normalize composition to a numeric vector aligned with parent_oxides.
    if isinstance(composition, Mapping):
        # Ferric refusal at the adapter boundary (FeO-equivalent contract).
        if "Fe2O3" in composition and composition["Fe2O3"] != 0:
            raise ImccFerricInputUnsupportedError(
                "Fe2O3 input is unsupported; convert to FeO under the "
                "caller's redox model before calling IMCC-SF04"
            )
        unknown = set(composition.keys()) - set(parent_oxides)
        if unknown:
            raise ImccComponentOutsideDomainError(
                f"component(s) outside IMCC-SF04 domain: {sorted(unknown)}"
            )
        vector = np.array(
            [float(composition.get(name, 0.0)) for name in parent_oxides],
            dtype=float,
        )
    else:
        vector = np.asarray(composition, dtype=float)
        if vector.ndim != 1:
            raise ImccCompositionIncompleteError(
                "composition vector must be 1-D"
            )
        if vector.shape[0] != len(parent_oxides):
            if vector.shape[0] > len(parent_oxides):
                raise ImccComponentOutsideDomainError(
                    f"composition vector length {vector.shape[0]} exceeds "
                    f"the {len(parent_oxides)}-oxide basis"
                )
            raise ImccCompositionIncompleteError(
                f"composition vector length {vector.shape[0]} does not match "
                f"the {len(parent_oxides)}-oxide basis"
            )

    if not np.all(np.isfinite(vector)):
        raise ImccCompositionIncompleteError(
            "composition contains non-finite values"
        )
    if np.any(vector < 0.0):
        raise ImccCompositionIncompleteError(
            "composition contains negative values"
        )

    total = float(vector.sum())
    if total <= 0.0:
        raise ImccCompositionIncompleteError("composition total is zero")

    if basis is None:
        basis = total
    else:
        basis = float(basis)
        if basis <= 0.0:
            raise ImccCompositionIncompleteError(
                "declared basis must be positive"
            )
        if abs(total - basis) > 1.0e-6 * basis:
            raise ImccCompositionIncompleteError(
                f"composition sum {total:.12g} does not match declared basis "
                f"{basis:.12g} within 1e-6 relative"
            )

    # Convert wt to mol before the cache boundary (E8).
    if basis_type == "wt":
        vector = _wt_to_mol(vector, parent_oxides)
        basis = float(vector.sum())

    # Cache / kernel registration point (NOT wired in chunk 3).
    # Future integration: register this adapter as a shadow provider for
    # ChemistryIntent.SILICATE_EQUILIBRIUM via
    # simulator.chemistry.kernel.registry.ProviderRegistry.register(...).
    # All wt-to-mol conversion and basis validation happens above this boundary.

    result = solve_imcc_sf04(
        vector,
        T_K,
        kernel_pack,
        basis=basis,
        extra_mol=extra_mol,
        allow_extrapolation=allow_extrapolation,
        tol=tol,
        max_iter=max_iter,
    )

    # Build the spec section 7 label block with three separate typed fields.
    identity: Mapping[str, str] = {
        "model_id": "IMCC-SF04",
        "datapack_version": pack_version,
    }
    coverage: Mapping[str, str] = {
        name: "A-published-imcc" for name in result.species_names
    }
    trust = _IMCC_EVIDENCE_CLASS_CANONICAL

    adapter_labels = ImccAdapterLabels(
        identity=identity,
        coverage=coverage,
        trust=trust,
    )

    return replace(result, labels=adapter_labels)
