"""Simulator trust label on top of the openimcc dependency adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np
import openimcc
from openimcc import (
    ImccComponentOutsideDomainError,
    ImccCompositionIncompleteError,
    ImccCompositionOutsideValidatedEnvelopeError,
    ImccDatapack,
    ImccFerricInputUnsupportedError,
    ImccLoadedDatapack,
    ImccMalformedDatapackError,
    ImccNonconvergenceError,
    ImccRefusal,
    ImccResult,
    ImccSPComponentRequiresExtensionError,
    ImccTOutsideDatapackDomainError,
    ImccUnprovenDatapackError,
    label_research_datapack,
    load_datapack,
)
from simulator.backend_names import canonical_backend_name


_STRICT_ENVELOPE_PARENT_MOLAR_MASSES_G_MOL = {
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
_STRICT_ENVELOPE_OXIDES = tuple(_STRICT_ENVELOPE_PARENT_MOLAR_MASSES_G_MOL)[:8]


@dataclass(frozen=True, init=False)
class ImccAdapterLabels:
    """Package labels with the simulator's positional trust contract."""

    identity: Mapping[str, str]
    coverage: Mapping[str, str]
    trust: str
    envelope_status: str
    flags: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    acid_sink_ratio: float | None = None

    def __init__(
        self,
        identity: Mapping[str, str],
        coverage: Mapping[str, str],
        trust: str,
        envelope_status: str,
        flags: tuple[str, ...] = (),
        notices: tuple[str, ...] = (),
        acid_sink_ratio: float | None = None,
    ) -> None:
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "trust", trust)
        object.__setattr__(self, "envelope_status", envelope_status)
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "notices", notices)
        object.__setattr__(self, "acid_sink_ratio", acid_sink_ratio)


def evaluate(*args: Any, **kwargs: Any) -> ImccResult:
    """Call openimcc and attach the simulator-only trust vocabulary."""
    allow_out_of_envelope = bool(kwargs.get("allow_out_of_envelope", False))
    composition = args[0] if args else kwargs.get("composition")
    basis_type = kwargs.get("basis_type", "mol")
    strict_x_me2o: float | None = None
    pack = args[2] if len(args) > 2 else kwargs.get("pack")
    kernel_pack = getattr(pack, "kernel_datapack", pack)
    pack_parents = getattr(kernel_pack, "parent_oxides", None)
    model_id = getattr(kernel_pack, "model_id", "IMCC-SF04" if pack is None else None)
    pack_identity_is_proven = pack is None or bool(
        getattr(kernel_pack, "identity_is_proven", False)
    )
    enable_sp_extension = bool(kwargs.get("enable_sp_extension", False))
    supplied_sp = False
    if isinstance(composition, Mapping):
        try:
            supplied_sp = any(
                float(composition.get(name, 0.0)) != 0.0
                for name in ("S", "P2O5")
            )
        except (TypeError, ValueError, OverflowError):
            pass
    elif composition is not None:
        try:
            supplied_sp = len(composition) > 8
        except TypeError:
            pass
    extra_mol = kwargs.get("extra_mol")
    if isinstance(extra_mol, Mapping):
        try:
            supplied_sp = supplied_sp or any(
                float(extra_mol.get(name, 0.0)) != 0.0
                for name in ("S", "P2O5")
            )
        except (TypeError, ValueError, OverflowError):
            pass
    extension_refusal_precedes_envelope = (
        model_id == "IMCC-SF04-EXT" and not enable_sp_extension
    ) or (
        model_id != "IMCC-SF04-EXT" and (enable_sp_extension or supplied_sp)
    )
    if (
        basis_type in ("mol", "wt")
        and pack_identity_is_proven
        and not extension_refusal_precedes_envelope
    ):
        try:
            if isinstance(composition, Mapping):
                supported_parents = set(
                    pack_parents or _STRICT_ENVELOPE_PARENT_MOLAR_MASSES_G_MOL
                )
                if set(composition) <= supported_parents:
                    amounts = np.asarray(
                        [
                            float(composition.get(name, 0.0))
                            for name in _STRICT_ENVELOPE_PARENT_MOLAR_MASSES_G_MOL
                        ],
                        dtype=float,
                    )
                else:
                    amounts = np.asarray([], dtype=float)
            else:
                amounts = np.asarray(composition, dtype=float)
                if amounts.ndim != 1 or amounts.size not in (8, 10):
                    amounts = np.asarray([], dtype=float)

            if amounts.size:
                raw_total = float(amounts.sum())
                declared_basis = kwargs.get("basis")
                basis_matches = declared_basis is None or (
                    np.isnan(float(declared_basis))
                    or (
                        float(declared_basis) > 0.0
                        and abs(raw_total - float(declared_basis))
                        <= 1.0e-6 * float(declared_basis)
                    )
                )
                if basis_type == "wt":
                    amounts = amounts / np.asarray(
                        tuple(_STRICT_ENVELOPE_PARENT_MOLAR_MASSES_G_MOL.values())[
                            : amounts.size
                        ]
                    )
                canonical_total = float(amounts[:8].sum())
                if (
                    basis_matches
                    and np.isfinite(amounts).all()
                    and np.all(amounts >= 0.0)
                    and canonical_total > 0.0
                ):
                    strict_x_me2o = float((amounts[6] + amounts[7]) / canonical_total)
                    if strict_x_me2o > 0.5 and not allow_out_of_envelope:
                        raise ImccCompositionOutsideValidatedEnvelopeError(
                            f"X_Me2O={strict_x_me2o:.12g} exceeds validated bound 0.5"
                        )
        except (TypeError, ValueError, OverflowError):
            # Let openimcc retain its typed validation for malformed inputs.
            pass
    package_kwargs = dict(kwargs)
    package_kwargs["allow_out_of_envelope"] = True
    result = openimcc.evaluate(*args, **package_kwargs)
    labels = result.labels
    if labels is None:
        return result
    parent_mol = dict(zip(result.parent_oxides, result.parent_mol, strict=True))
    canonical_oxide_mol = sum(
        float(parent_mol[name])
        for name in _STRICT_ENVELOPE_OXIDES
    )
    x_me2o = strict_x_me2o
    if x_me2o is None:
        x_me2o = (
            float(parent_mol["Na2O"]) + float(parent_mol["K2O"])
        ) / canonical_oxide_mol
    outside_validated_envelope = x_me2o > 0.5
    if outside_validated_envelope and not allow_out_of_envelope:
        raise ImccCompositionOutsideValidatedEnvelopeError(
            f"X_Me2O={x_me2o:.12g} exceeds validated bound 0.5"
        )
    trust = canonical_backend_name("internal-analytical")
    if trust is None:  # pragma: no cover - fixed repository vocabulary
        raise RuntimeError("internal-analytical trust label is not registered")
    return replace(
        result,
        labels=ImccAdapterLabels(
            identity=labels.identity,
            coverage=labels.coverage,
            envelope_status=(
                "outside_validated" if outside_validated_envelope else "inside"
            ),
            trust=trust,
            flags=labels.flags,
            notices=labels.notices,
            acid_sink_ratio=labels.acid_sink_ratio,
        ),
    )
