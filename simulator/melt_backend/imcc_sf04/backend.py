"""IMCC-SF04 MeltBackend glue.

The simulator-facing half of the IMCC-SF04 adapter: the two ``MeltBackend``
implementations that ``resolve_backend`` selects, plus the trust-vocabulary
assertions that bind them to this repository's certification denylist.

Split out of ``adapter.py`` so the model half stays free of simulator policy.
Everything here is *this project's* concern — backend naming, evidence class,
``EquilibriumResult`` shape — and none of it is part of the IMCC model. The
model half (``adapter.py`` + ``kernel.py`` + ``gas.py``) imports nothing from
this module, which is what makes the package extractable.

Shadow / diagnostic only. Promotion into ``REAL_MELT_BACKEND_NAMES`` and active
recipe eligibility is a separate owner-gated change after the battery result
(t-890).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Optional

from simulator.backend_names import (
    IMCC_SF04_BACKEND_NAME,
    IMCC_SF04_EXT_BACKEND_NAME,
    canonical_backend_name,
)
from simulator.fidelity_vocabulary import backend_name_denies_authority
from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    split_cleaned_melt_account,
)
from simulator.melt_backend.imcc_sf04.adapter import (
    ImccLoadedDatapack,
    _EXT_DATAPACK_PATH,
    _KELVIN_OFFSET,
    _PUBLISHED_DATAPACK_PATH,
    evaluate,
    load_datapack,
)
from simulator.melt_backend.imcc_sf04.kernel import (
    ImccNonconvergenceError,
    ImccRefusal,
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


class ImccSf04Backend(MeltBackend):
    """Thin MeltBackend wrapper around the published IMCC-SF04 adapter.

    Diagnostic / shadow only. ``equilibrate`` reports parent activities on
    the legacy ``activity_coefficients`` field and does not emit a ledger
    transition or a phase assemblage.
    """

    name = IMCC_SF04_BACKEND_NAME
    backend_name = IMCC_SF04_BACKEND_NAME
    _default_datapack_path = _PUBLISHED_DATAPACK_PATH
    _enable_sp_extension = False

    def __init__(self) -> None:
        self._available = False
        self._config: dict[str, Any] = {}
        self._pack: ImccLoadedDatapack | None = None
        self._last_error: str | None = None

    def initialize(self, config: dict) -> bool:
        self._available = False
        self._pack = None
        self._last_error = None
        self._config = dict(config or {})
        raw_path = self._config.get("datapack_path")
        path = Path(raw_path) if raw_path else self._default_datapack_path
        try:
            self._pack = load_datapack(path)
        except ImccRefusal as exc:
            self._last_error = str(exc)
            return False
        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available and self._pack is not None

    def get_vapor_species(self) -> list[str]:
        return []

    def capabilities(self) -> Dict[str, bool]:
        return dict(DEFAULT_BACKEND_CAPABILITIES)

    def ledger_account_policies(self) -> tuple[Any, ...]:
        return ()

    def get_engine_version(self) -> str:
        if self._pack is None:
            return "unavailable"
        return f"{self._pack.model_id} {self._pack.version}"

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Optional[Dict[str, float]] = None,
        fO2_log: Optional[float] = -9.0,
        pressure_bar: float = 1e-6,
        *,
        composition_mol: Optional[Dict[str, float]] = None,
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ) -> EquilibriumResult:
        _ = species_formula_registry
        if not self.is_available() or self._pack is None:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="unavailable",
                warnings=["IMCC-SF04 backend not initialized"],
                phase_assemblage_available=False,
            )

        if composition_mol_by_account is not None:
            composition_mol, _ = split_cleaned_melt_account(
                composition_mol_by_account
            )

        if composition_mol:
            composition: Mapping[str, float] = {
                str(name): float(value) for name, value in composition_mol.items()
            }
            basis_type = "mol"
        elif composition_kg:
            composition = {
                str(name): float(value) for name, value in composition_kg.items()
            }
            basis_type = "wt"
        else:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="out_of_domain",
                warnings=["IMCC-SF04 received empty melt composition"],
                phase_assemblage_available=False,
            )

        temperature_K = float(temperature_C) + _KELVIN_OFFSET
        try:
            result = evaluate(
                composition,
                temperature_K,
                self._pack,
                basis_type=basis_type,
                enable_sp_extension=self._enable_sp_extension,
            )
        except ImccNonconvergenceError as exc:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="not_converged",
                warnings=[str(exc)],
                phase_assemblage_available=False,
            )
        except ImccRefusal as exc:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="out_of_domain",
                warnings=[str(exc)],
                phase_assemblage_available=False,
            )

        activities = {
            str(name): float(value)
            for name, value in zip(
                result.parent_oxides, result.parent_activity, strict=True
            )
        }
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            status="ok",
            activity_coefficients=activities,
            phase_assemblage_available=False,
            liquid_fraction=None,
        )


class ImccSf04ExtBackend(ImccSf04Backend):
    """Thin MeltBackend wrapper around the IMCC-SF04-EXT datapack."""

    name = IMCC_SF04_EXT_BACKEND_NAME
    backend_name = IMCC_SF04_EXT_BACKEND_NAME
    _default_datapack_path = _EXT_DATAPACK_PATH
    _enable_sp_extension = True
