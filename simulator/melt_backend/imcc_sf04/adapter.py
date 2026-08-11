"""IMCC-SF04 domain adapter: JSON datapack loader and caller-facing API.

Chunk 3 of the upstream IMCC-SF04 lane. Wraps the kernel from
``simulator.melt_backend.imcc_sf04.kernel`` with a JSON datapack loader, a
wt-to-mol basis converter, and a canonical trust-vocabulary label block.
"""

from __future__ import annotations

import hashlib
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


# Stable molar masses (g/mol) for the IMCC-SF04 parent components.
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
        "S": 32.06,
        "P2O5": 141.9445,
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

# Frozen from datapack v1.0.2: the published FC87/SF04 species set,
# source-verified, with the three published A-sign corrections applied.
_PUBLISHED_CORE_MANIFEST = (
    ("Mg2SiO4", (1, 2, 0, 0, 0, 0, 0, 0)),
    ("MgSiO3", (1, 1, 0, 0, 0, 0, 0, 0)),
    ("MgAl2O4", (0, 1, 0, 0, 1, 0, 0, 0)),
    ("MgTiO3", (0, 1, 0, 0, 0, 1, 0, 0)),
    ("MgTi2O5", (0, 1, 0, 0, 0, 2, 0, 0)),
    ("Mg2TiO4", (0, 2, 0, 0, 0, 1, 0, 0)),
    ("Al6Si2O13", (2, 0, 0, 0, 3, 0, 0, 0)),
    ("CaAl2O4", (0, 0, 0, 1, 1, 0, 0, 0)),
    ("CaAl4O7", (0, 0, 0, 1, 2, 0, 0, 0)),
    ("Ca12Al14O33", (0, 0, 0, 12, 7, 0, 0, 0)),
    ("CaSiO3", (1, 0, 0, 1, 0, 0, 0, 0)),
    ("CaAl2Si2O8", (2, 0, 0, 1, 1, 0, 0, 0)),
    ("CaMgSi2O6", (2, 1, 0, 1, 0, 0, 0, 0)),
    ("Ca2MgSi2O7", (2, 1, 0, 2, 0, 0, 0, 0)),
    ("Ca2Al2SiO7", (1, 0, 0, 2, 1, 0, 0, 0)),
    ("CaTiO3", (0, 0, 0, 1, 0, 1, 0, 0)),
    ("Ca2SiO4", (1, 0, 0, 2, 0, 0, 0, 0)),
    ("CaTiSiO5", (1, 0, 0, 1, 0, 1, 0, 0)),
    ("FeTiO3", (0, 0, 1, 0, 0, 1, 0, 0)),
    ("Fe2SiO4", (1, 0, 2, 0, 0, 0, 0, 0)),
    ("FeAl2O4", (0, 0, 1, 0, 1, 0, 0, 0)),
    ("CaAl12O19", (0, 0, 0, 1, 6, 0, 0, 0)),
    ("Mg2Al4Si5O18", (5, 2, 0, 0, 2, 0, 0, 0)),
    ("Na2SiO3", (1, 0, 0, 0, 0, 0, 1, 0)),
    ("Na2Si2O5", (2, 0, 0, 0, 0, 0, 1, 0)),
    ("NaAlSiO4", (1, 0, 0, 0, 0.5, 0, 0.5, 0)),
    ("NaAlSi3O8", (3, 0, 0, 0, 0.5, 0, 0.5, 0)),
    ("NaAlO2", (0, 0, 0, 0, 0.5, 0, 0.5, 0)),
    ("Na2TiO3", (0, 0, 0, 0, 0, 1, 1, 0)),
    ("NaAlSi2O6", (2, 0, 0, 0, 0.5, 0, 0.5, 0)),
    ("K2SiO3", (1, 0, 0, 0, 0, 0, 0, 1)),
    ("K2Si2O5", (2, 0, 0, 0, 0, 0, 0, 1)),
    ("KAlSiO4", (1, 0, 0, 0, 0.5, 0, 0, 0.5)),
    ("KAlSi3O8", (3, 0, 0, 0, 0.5, 0, 0, 0.5)),
    ("KAlO2", (0, 0, 0, 0, 0.5, 0, 0, 0.5)),
    ("KAlSi2O6", (2, 0, 0, 0, 0.5, 0, 0, 0.5)),
    ("K2Si4O9", (4, 0, 0, 0, 0, 0, 0, 1)),
    ("KCaAlSi2O7", (2, 0, 0, 1, 0.5, 0, 0, 0.5)),
)
_PUBLISHED_CORE_MANIFEST_SHA256 = (
    "72740c3578059e6ee1a09e61fd727a1e2df5f62df84489e160152e553eb9c59f"
)

_SP_EXTENSION_MODEL_ID = "IMCC-SF04-EXT"
_SP_EXTENSION_PARENTS = ("S", "P2O5")
_SP_EXTENSION_TIER = "EXT-SP"
_SP_EXTENSION_FLAG = "enable_sp_extension"
_SP_EXTENSION_PROVENANCE_CLASS = "extension-compound-thermo"

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


def _published_core_manifest_hash(
    manifest: Sequence[tuple[str, Sequence[int | float]]],
) -> str:
    content = [
        {
            "complex": complex_name,
            "nu": dict(zip(_EXPECTED_PARENT_OXIDES, coefficients)),
        }
        for complex_name, coefficients in manifest
    ]
    payload = json.dumps(
        content,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


if _published_core_manifest_hash(_PUBLISHED_CORE_MANIFEST) != (
    _PUBLISHED_CORE_MANIFEST_SHA256
):
    raise RuntimeError("frozen IMCC-SF04 published-core manifest hash mismatch")


class ImccCompositionOutsideValidatedEnvelopeError(ImccRefusal):
    """Raised when the melt lies outside the validated Tier-A envelope."""

    code = "imcc_composition_outside_validated_envelope"


class ImccSPComponentRequiresExtensionError(ImccComponentOutsideDomainError):
    """Raised when S/P input lacks the EXT model plus explicit enable flag."""

    code = "imcc_sp_extension_required"


@dataclass(frozen=True)
class ImccLoadedDatapack:
    """Adapter-level loaded datapack: kernel datapack plus provenance metadata."""

    kernel_datapack: ImccDatapack
    version: str
    parent_oxides: Sequence[str]
    domain_basis: Sequence[str]
    model_id: str = "IMCC-SF04"
    extension_parents: Sequence[str] = ()
    extension_species: Sequence[str] = ()


@dataclass(frozen=True)
class ImccAdapterLabels:
    """Section 7 label block plus orthogonal composition-envelope status."""

    identity: Mapping[str, str]
    coverage: Mapping[str, str]
    trust: str
    envelope_status: str


def _as_fraction(value: Any) -> Fraction:
    """Parse a JSON numeric value as an exact rational."""
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, (int, float)):
        return Fraction(str(value))
    raise TypeError(f"cannot parse {value!r} as a rational")


def _validate_published_core(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(_PUBLISHED_CORE_MANIFEST):
        raise ImccMalformedDatapackError(
            "datapack must contain exactly 38 published-core rows, got "
            f"{len(rows) if isinstance(rows, list) else None}"
        )

    expected_parent_set = set(_EXPECTED_PARENT_OXIDES)
    for idx, (row, expected) in enumerate(zip(rows, _PUBLISHED_CORE_MANIFEST)):
        if not isinstance(row, dict):
            raise ImccMalformedDatapackError(
                f"published core row {idx} is not an object"
            )
        nu = row.get("nu")
        if not isinstance(nu, dict):
            raise ImccMalformedDatapackError(
                f"published core row {idx} missing 'nu' object"
            )
        unknown_nu = sorted(set(nu) - expected_parent_set)
        if unknown_nu:
            raise ImccMalformedDatapackError(
                f"published core row {idx} nu has unknown key {unknown_nu[0]!r}"
            )
        missing_nu = sorted(expected_parent_set - set(nu))
        if missing_nu:
            raise ImccMalformedDatapackError(
                f"published core row {idx} nu is missing key {missing_nu[0]!r}"
            )

        expected_name, expected_coefficients = expected
        complex_name = row.get("complex")
        if complex_name != expected_name:
            raise ImccMalformedDatapackError(
                f"published core row {idx} complex {complex_name!r} does not match "
                f"frozen manifest complex {expected_name!r}"
            )
        coefficients = tuple(nu[parent] for parent in _EXPECTED_PARENT_OXIDES)
        if coefficients != expected_coefficients:
            mismatch = next(
                parent
                for parent, actual, frozen in zip(
                    _EXPECTED_PARENT_OXIDES,
                    coefficients,
                    expected_coefficients,
                )
                if actual != frozen
            )
            raise ImccMalformedDatapackError(
                f"published core row {idx} nu[{mismatch!r}]={nu[mismatch]!r} "
                "does not match frozen manifest"
            )
        if _published_core_manifest_hash(((complex_name, coefficients),)) != (
            _published_core_manifest_hash(((expected_name, expected_coefficients),))
        ):
            raise ImccMalformedDatapackError(
                f"published core row {idx} numeric content does not match "
                "the frozen manifest encoding"
            )
    return rows


def _build_nu_vector(
    parent_oxides: Sequence[str],
    nu: Mapping[str, Any],
    *,
    row_label: str,
) -> list[float]:
    """Return a nu column aligned to parent_oxides, preserving exact rationals."""
    unknown_nu = sorted(set(nu) - set(parent_oxides))
    if unknown_nu:
        raise ImccMalformedDatapackError(
            f"{row_label} nu has unknown key {unknown_nu[0]!r}"
        )
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

    Validates the published 8-parent/38-row core. ``IMCC-SF04-EXT`` packs may
    add the separately labelled ``sp_extension`` section; the published core
    remains byte-for-byte and count-for-count distinct from extension rows.
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

    base_parents = data.get("parents")
    if not isinstance(base_parents, list) or base_parents != list(_EXPECTED_PARENT_OXIDES):
        raise ImccMalformedDatapackError(
            f"datapack parents {base_parents!r} do not match expected "
            f"{_EXPECTED_PARENT_OXIDES!r}"
        )
    base_parents = tuple(base_parents)

    rows = _validate_published_core(data.get("rows"))

    model_id = data.get("model_id", "IMCC-SF04")
    sp_extension = data.get("sp_extension")
    extension_parents: tuple[str, ...] = ()
    extension_rows: list[dict[str, Any]] = []
    if sp_extension is None:
        if model_id != "IMCC-SF04":
            raise ImccMalformedDatapackError(
                f"model_id {model_id!r} requires a recognized extension section"
            )
    else:
        if model_id != _SP_EXTENSION_MODEL_ID:
            raise ImccMalformedDatapackError(
                "sp_extension requires model_id='IMCC-SF04-EXT'"
            )
        if not isinstance(sp_extension, dict):
            raise ImccMalformedDatapackError("sp_extension must be an object")
        if sp_extension.get("enable_flag") != _SP_EXTENSION_FLAG:
            raise ImccMalformedDatapackError(
                "sp_extension enable_flag must be 'enable_sp_extension'"
            )
        if sp_extension.get("tier") != _SP_EXTENSION_TIER:
            raise ImccMalformedDatapackError("sp_extension tier must be 'EXT-SP'")
        if sp_extension.get("certification") != "denied":
            raise ImccMalformedDatapackError(
                "sp_extension certification must be explicitly denied"
            )
        raw_extension_parents = sp_extension.get("parents")
        if raw_extension_parents != list(_SP_EXTENSION_PARENTS):
            raise ImccMalformedDatapackError(
                f"sp_extension parents must be {list(_SP_EXTENSION_PARENTS)!r}"
            )
        raw_extension_rows = sp_extension.get("rows")
        if not isinstance(raw_extension_rows, list) or not raw_extension_rows:
            raise ImccMalformedDatapackError(
                "sp_extension rows must be a non-empty list"
            )
        for idx, row in enumerate(raw_extension_rows):
            if not isinstance(row, dict):
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} is not an object"
                )
            if row.get("provenance_class") != _SP_EXTENSION_PROVENANCE_CLASS:
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} provenance_class must be "
                    f"{_SP_EXTENSION_PROVENANCE_CLASS!r}"
                )
            if row.get("tier") != _SP_EXTENSION_TIER:
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} tier must be 'EXT-SP'"
                )
            if row.get("certification") != "denied":
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} certification must be denied"
                )
            provenance = row.get("provenance")
            if not isinstance(provenance, dict):
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} missing provenance object"
                )
            if not all(
                isinstance(provenance.get(field), str) and provenance[field]
                for field in ("source", "table_id")
            ):
                raise ImccMalformedDatapackError(
                    f"sp_extension row {idx} provenance requires source and table_id"
                )
            extension_nu = row.get("nu")
            if isinstance(extension_nu, dict):
                unknown_nu = set(extension_nu) - set(
                    base_parents + _SP_EXTENSION_PARENTS
                )
                if unknown_nu:
                    raise ImccMalformedDatapackError(
                        f"sp_extension row {idx} nu has unknown parents "
                        f"{sorted(unknown_nu)}"
                    )
                if not any(
                    _as_fraction(extension_nu.get(parent, 0)) > 0
                    for parent in _SP_EXTENSION_PARENTS
                ):
                    raise ImccMalformedDatapackError(
                        f"sp_extension row {idx} must consume S or P2O5"
                    )
            extension_rows.append(row)
        extension_parents = _SP_EXTENSION_PARENTS

    parents = base_parents + extension_parents
    all_rows = rows + extension_rows

    reactions: list[str] = []
    nu_cols: list[list[float]] = []
    A: list[float] = []
    B: list[float] = []
    domains: list[tuple[float, float]] = []
    domain_basis: list[str] = []

    for idx, row in enumerate(all_rows):
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
        row_label = (
            f"published core row {idx}"
            if idx < len(rows)
            else f"sp_extension row {idx - len(rows)}"
        )
        nu_cols.append(_build_nu_vector(parents, nu, row_label=row_label))

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

    if len(set(reactions)) != len(reactions):
        raise ImccMalformedDatapackError("datapack complex names must be unique")

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
        model_id=model_id,
        extension_parents=extension_parents,
        extension_species=tuple(
            str(row["complex"]) for row in extension_rows
        ),
    )


def _sp_extension_refusal(component_names: Sequence[str]) -> ImccSPComponentRequiresExtensionError:
    names = ", ".join(sorted(component_names)) or "S/P EXT component(s)"
    return ImccSPComponentRequiresExtensionError(
        f"{names} belong to the S/P EXT component class; unlock only with "
        "model_id='IMCC-SF04-EXT' and enable_sp_extension=True. Plain "
        "IMCC-SF04 intentionally excludes S/P speciation."
    )


def evaluate(
    composition: Mapping[str, float] | Sequence[float],
    T_K: float,
    pack: ImccLoadedDatapack | ImccDatapack,
    *,
    basis: float | None = None,
    basis_type: str = "mol",
    extra_mol: Mapping[str, float] | None = None,
    enable_sp_extension: bool = False,
    allow_extrapolation: bool = False,
    allow_out_of_envelope: bool = False,
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
    enable_sp_extension:
        Explicitly enable S and P2O5 parents in an ``IMCC-SF04-EXT`` pack.
        The flag alone never widens a plain ``IMCC-SF04`` pack.
    allow_extrapolation:
        If ``True``, evaluate outside declared T domains and mark the result
        as extrapolated.
    allow_out_of_envelope:
        If ``True``, evaluate outside the validated Tier-A composition
        envelope and mark the result ``outside_validated``.
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
        model_id = pack.model_id
        extension_parents = tuple(pack.extension_parents)
        extension_species = tuple(pack.extension_species)
    else:
        kernel_pack = pack
        pack_version = pack.version
        parent_oxides = pack.parent_oxides
        model_id = "IMCC-SF04"
        extension_parents = ()
        extension_species = ()

    supplied_sp_names: set[str] = set()
    if isinstance(composition, Mapping):
        supplied_sp_names.update(
            name for name in _SP_EXTENSION_PARENTS
            if float(composition.get(name, 0.0)) != 0.0
        )
    elif len(composition) > len(_EXPECTED_PARENT_OXIDES):
        supplied_sp_names.update(_SP_EXTENSION_PARENTS)
    if extra_mol:
        supplied_sp_names.update(
            name for name in _SP_EXTENSION_PARENTS
            if float(extra_mol.get(name, 0.0)) != 0.0
        )

    if model_id == _SP_EXTENSION_MODEL_ID:
        if not enable_sp_extension:
            raise _sp_extension_refusal(extension_parents)
    elif enable_sp_extension or supplied_sp_names:
        raise _sp_extension_refusal(supplied_sp_names)

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

    # X_Me2O = (n_Na2O + n_K2O) / sum(n_oxide) over the canonical
    # 8-oxide mol vector. The r2.2 Tier-A boundary is inclusive at 0.5.
    alkali_mol = sum(
        float(vector[parent_oxides.index(name)]) for name in ("Na2O", "K2O")
    )
    canonical_oxide_mol = sum(
        float(vector[parent_oxides.index(name)])
        for name in _EXPECTED_PARENT_OXIDES
    )
    if canonical_oxide_mol <= 0.0:
        raise ImccCompositionIncompleteError(
            "canonical 8-oxide composition total is zero"
        )
    x_me2o = alkali_mol / canonical_oxide_mol
    outside_validated_envelope = x_me2o > 0.5
    if outside_validated_envelope and not allow_out_of_envelope:
        raise ImccCompositionOutsideValidatedEnvelopeError(
            f"X_Me2O={x_me2o:.12g} exceeds validated bound 0.5"
        )

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
        "model_id": model_id,
        "datapack_version": pack_version,
    }
    sp_names = set(extension_parents) | set(extension_species)
    coverage: Mapping[str, str] = {
        name: (_SP_EXTENSION_TIER if name in sp_names else "A-published-imcc")
        for name in result.species_names
    }
    trust = _IMCC_EVIDENCE_CLASS_CANONICAL

    adapter_labels = ImccAdapterLabels(
        identity=identity,
        coverage=coverage,
        trust=trust,
        envelope_status=(
            "outside_validated"
            if outside_validated_envelope
            else "inside"
        ),
    )

    return replace(result, labels=adapter_labels)
