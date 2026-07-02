"""AlphaMELTS composition-domain gate.

AlphaMELTS operates on the MELTS 14-oxide basis (see
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §7 -- "VapoRock
receiving metal/sulfide/salt accounts. Filter at entry."). Anything
non-silicate (native metals, halides, sulfides, elemental S, chlorates,
carbonates, nitrates, etc.) must be rejected before the chemistry call —
these species violate MELTS solid-solution / liquid models and produce
silent garbage on output.

Mirrors :meth:`simulator.melt_backend.alphamelts.AlphaMELTSBackend._domain_gate`
exactly (same thresholds, same non-oxide detection heuristic) so the
provider-side and adapter-side gates report identical rejection reasons
for the same composition. Centralising the rules here means a future
threshold change touches one place.

The gate does **not** raise. It returns ``(valid, warnings)`` so the
kernel planner / provider can decide whether to surface
``status='out_of_domain'`` or short-circuit with an empty diagnostic.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Tuple

from engines.domain_reason import OutOfDomainReason, reason_value

# Canonical MELTS 14-oxide basis. Sourced verbatim from
# ``simulator.melt_backend.alphamelts.MELTS_OXIDE_BASIS`` so the provider
# domain matches what the adapter actually feeds AlphaMELTS.
MELTS_OXIDE_BASIS: Tuple[str, ...] = (
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
    'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
)
_MELTS_OXIDE_SET = frozenset(MELTS_OXIDE_BASIS)

# Aliases the adapter recognises (same map as
# ``alphamelts.MELTS_OXIDE_ALIASES``); kept duplicated rather than
# imported because the provider package must stay importable without
# pulling in the legacy adapter module.
_OXIDE_ALIASES: Dict[str, str] = {oxide.lower(): oxide for oxide in MELTS_OXIDE_BASIS}
_OXIDE_ALIASES.update({
    'feo_total': 'FeO_total',
    'feot': 'FeO_total',
    'feototal': 'FeO_total',
    'feo_tot': 'FeO_total',
})

# Tolerance constants. Match the AlphaMELTS adapter DomainGate values. The
# MELTS binding spec uses a strict silicate-network admission criterion:
# major oxide sum must be >95.0 wt%, so an exact 95.0 wt% boundary is rejected.
_SIO2_MIN_WT_PCT = 30.0
_SIO2_MAX_WT_PCT = 80.0
_MAJOR_OXIDE_MIN_TOTAL_WT_PCT = 95.0

# Halides / sulfur that, if encountered as elements in a species name,
# disqualify the species outright (mirrors
# ``alphamelts._is_non_oxide_species_name``).
_NON_OXIDE_ELEMENT_FLAGS = frozenset({'Cl', 'F', 'Br', 'I', 'S'})


class AlphaMELTSDomainGate:
    """Validate a melt composition against MELTS' 14-oxide basis.

    The gate enforces the four constraints listed in goal #8 checklist
    item 2:

    1. **MELTS oxide basis** — species must canonicalise into the
       :data:`MELTS_OXIDE_BASIS` set (or its alias map). Non-oxide
       species (native metals, halides, sulfides, elemental S) are
       rejected.
    2. **Fe redox policy** — the gate does not encode the redox split
       (Fe3Fet ratio / fO2 buffer) itself, but flags compositions where
       Fe is supplied as elemental ``Fe`` (which violates the oxide
       basis); the redox enforcement is performed by
       ``AlphaMELTSBackend._normalize_composition_to_melts_basis`` which
       raises if FeO_total is supplied without an explicit redox policy.
    3. **Silicate-network criteria** — SiO2 in [30, 80] wt%; sum of
       major oxides > 95 wt%. Outside this range MELTS extrapolations
       are physically meaningless.
    4. **Composition-only gate** — operating-point checks live at the
       transport/provider layer where temperature and pressure are available.
       This validator has no T/P inputs and must not claim to certify them.

    Returns ``(valid, warnings)``; never raises. The caller routes the
    rejected composition elsewhere (e.g. Stage 0 cleanup) or surfaces
    the warning through the kernel diagnostic channel.
    """

    @staticmethod
    def validate(
        composition_wt_pct: Mapping[str, float],
    ) -> Tuple[bool, List[str]]:
        """Validate ``composition_wt_pct`` against the MELTS 14-oxide basis.

        Parameters
        ----------
        composition_wt_pct:
            Mapping ``species_name -> wt%``. Must be derived from the
            silicate-oxide melt projection (``MeltState.composition_wt_pct``
            or the kernel's account-view oxide projection). Non-oxide
            species in this mapping are treated as a domain violation.

        Returns
        -------
        ``(valid, warnings)`` -- ``valid`` is ``True`` iff every check
        passed; ``warnings`` lists the human-readable rejection reasons.
        """
        valid, warnings, _reason = AlphaMELTSDomainGate.validate_with_reason(
            composition_wt_pct
        )
        return valid, warnings

    @staticmethod
    def validate_with_reason(
        composition_wt_pct: Mapping[str, float],
    ) -> Tuple[bool, List[str], str | None]:
        """Validate and return the structured out-of-domain reason code."""
        warnings: List[str] = []
        reason: OutOfDomainReason | None = None

        if not composition_wt_pct:
            warnings.append(
                'AlphaMELTSDomainGate: empty composition; cannot equilibrate.'
            )
            return False, warnings, OutOfDomainReason.MAJOR_SUM.value

        canonical_wt: Dict[str, float] = {}
        non_oxides: List[str] = []
        unrecognised: List[str] = []
        for raw_name, raw_wt in composition_wt_pct.items():
            try:
                wt = float(raw_wt)
            except (TypeError, ValueError):
                warnings.append(
                    f'AlphaMELTSDomainGate: unparseable wt% for {raw_name!r}'
                )
                continue
            if wt != wt or wt in (float('inf'), float('-inf')):
                warnings.append(
                    f'AlphaMELTSDomainGate: non-finite wt% for {raw_name!r}'
                )
                continue
            if wt <= 0.0:
                continue
            oxide = _canonical_oxide_name(raw_name)
            if oxide is None:
                if _is_non_oxide_species_name(raw_name):
                    non_oxides.append(str(raw_name))
                else:
                    unrecognised.append(str(raw_name))
                continue
            if oxide == 'FeO_total':
                # FeO_total is recognised by the adapter (it triggers the
                # explicit-redox-policy gate) but is NOT a MELTS 14-oxide
                # basis member -- exclude from the canonical sum so the
                # adapter's redox split runs cleanly downstream.
                canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt
            else:
                canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt

        if non_oxides:
            reason = OutOfDomainReason.FORBIDDEN_SPECIES
            warnings.append(
                'AlphaMELTSDomainGate: non-oxide species present '
                f'(metal / sulfide / halide -- must route through Stage 0 '
                f'first): {sorted(non_oxides)}'
            )
        if unrecognised:
            reason = reason or OutOfDomainReason.FORBIDDEN_SPECIES
            warnings.append(
                'AlphaMELTSDomainGate: unrecognised species outside MELTS '
                f'14-oxide basis: {sorted(unrecognised)}'
            )

        sio2_pct = canonical_wt.get('SiO2', 0.0)
        if sio2_pct < _SIO2_MIN_WT_PCT or sio2_pct > _SIO2_MAX_WT_PCT:
            reason = reason or OutOfDomainReason.SILICATE_WINDOW
            warnings.append(
                f'AlphaMELTSDomainGate: SiO2 = {sio2_pct:.3f} wt% outside '
                f'MELTS calibration range '
                f'[{_SIO2_MIN_WT_PCT}, {_SIO2_MAX_WT_PCT}] wt%.'
            )

        # Major oxide sum: MELTS 14-oxide basis members only (FeO_total
        # is excluded; if present it indicates an upstream redox-policy
        # issue but does not count toward the silicate-network criterion).
        major_total = sum(
            canonical_wt.get(oxide, 0.0) for oxide in MELTS_OXIDE_BASIS
        )
        if major_total <= _MAJOR_OXIDE_MIN_TOTAL_WT_PCT:
            reason = reason or OutOfDomainReason.MAJOR_SUM
            warnings.append(
                f'AlphaMELTSDomainGate: major-oxide sum = {major_total:.3f} '
                f'wt% <= {_MAJOR_OXIDE_MIN_TOTAL_WT_PCT} wt%; composition '
                'is dominated by non-MELTS species.'
            )

        if warnings and reason is None:
            reason = OutOfDomainReason.MAJOR_SUM
        return (not warnings), warnings, reason_value(reason)

    @staticmethod
    def oxide_basis() -> Tuple[str, ...]:
        """Return the canonical MELTS 14-oxide basis."""
        return MELTS_OXIDE_BASIS

    @staticmethod
    def reject_unsupported_accounts(
        composition_mol_by_account: Mapping[str, Mapping[str, float]],
    ) -> List[str]:
        """Report unsupported ledger accounts present in the input.

        Mirrors :meth:`AlphaMELTSBackend._unsupported_accounts`: any
        account other than ``process.cleaned_melt`` that carries positive
        mol material is reported as a single warning string per
        ``account=species_list`` entry. Returns an empty list when only
        ``process.cleaned_melt`` is populated.

        The provider's :class:`CapabilityProfile.declared_accounts` set
        already filters non-cleaned-melt accounts out at the kernel
        level; this helper is a belt-and-braces gate the provider uses
        when the caller supplies an explicit account mapping (e.g. the
        legacy adapter path) so the same rejection text appears in both
        the kernel-routed and adapter-routed call sites.
        """
        unsupported = []
        for account, species_mol in composition_mol_by_account.items():
            if str(account) == 'process.cleaned_melt':
                continue
            species = sorted(
                str(sp) for sp, mol in (species_mol or {}).items()
                if _safe_float(mol) > 0.0
            )
            if species:
                unsupported.append(f'{account}={species}')
        return sorted(unsupported)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_oxide_name(name: object) -> str | None:
    """Map a species name to the MELTS oxide basis or return None.

    Mirrors :meth:`AlphaMELTSBackend._canonical_oxide_name`. Strips a
    trailing ``_Liq`` suffix (PetThermoTools convention) and lowercases
    before alias lookup.
    """
    key = str(name).strip()
    if key.endswith('_Liq'):
        key = key[:-4]
    return _OXIDE_ALIASES.get(key.lower())


def _is_non_oxide_species_name(name: object) -> bool:
    """Detect non-oxide species names (metals, halides, sulfides).

    Mirrors :meth:`AlphaMELTSBackend._is_non_oxide_species_name`:

    * No element regex match -> non-oxide-like.
    * No ``O`` element present -> non-oxide.
    * Any of {Cl, F, Br, I, S} present -> halide / sulfide / sulfate /
      chlorate / etc.

    Used by the domain gate to distinguish "wrong oxide name"
    (unrecognised) from "wrong species class" (non-oxide). The latter
    must route through Stage 0 cleanup.
    """
    text = str(name).strip()
    elements = re.findall(r'[A-Z][a-z]?', text)
    if not elements:
        return True
    if 'O' not in elements:
        return True
    return any(element in _NON_OXIDE_ELEMENT_FLAGS for element in elements)


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Re-export the non-oxide detector so tests can pin it directly.
__all__: Iterable[str] = (
    'AlphaMELTSDomainGate',
    'MELTS_OXIDE_BASIS',
)
