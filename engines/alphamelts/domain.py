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
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

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
# Default SiO2 calibration band. Within [CRASH_FLOOR, 100] it is a trust
# window the rail owns; BELOW the floor it is not a trust question at all,
# because the engine dies.
#
# MEASURED 2026-08-16 (docs-private/research/2026-08-16-rump-hotwire/report.md):
# with the band lowered, alphaMELTS SIGABRTs on ALL 40 multi-component
# sub-30 wt% points, in 2-4.5 s, reproduced in fresh isolated processes.
# Alkali basalt, dunite and komatiite die at 34.0 wt%; tholeiite returns at
# 33.96 (with no activities) and dies at 31.65. This was NOT the
# two-component alkali-silica guard -- these are full multi-oxide rump
# compositions, and the crash happens anyway.
#
# So the earlier comment here, that outside the band MELTS can still compute
# and the rail merely distrusts it, is FALSE below the floor and has been
# removed. A configurable band without a floor would let an operator set
# 28 wt% on the strength of that claim and take a SIGABRT.
#
# The floor is set at the most conservative observed death (34.0), not the
# most permissive (31.65): tholeiite survives slightly lower, but a floor
# must hold for every composition the rail can present, and three of four
# basalts die at 34.
_SIO2_CRASH_FLOOR_WT_PCT = 34.0
DEFAULT_SIO2_MIN_WT_PCT = 30.0
DEFAULT_SIO2_MAX_WT_PCT = 80.0
DEFAULT_SILICATE_NETWORK_BAND_WT_PCT: Tuple[float, float] = (
    DEFAULT_SIO2_MIN_WT_PCT,
    DEFAULT_SIO2_MAX_WT_PCT,
)
_MAJOR_OXIDE_MIN_TOTAL_WT_PCT = 95.0

CONSTRAINT_OXIDE_BASIS = 'oxide_basis'
CONSTRAINT_SILICATE_NETWORK_BAND = 'silicate_network_band'
CONSTRAINT_MAJOR_OXIDE_SUM = 'major_oxide_sum'

# Halides / sulfur that, if encountered as elements in a species name,
# disqualify the species outright (mirrors
# ``alphamelts._is_non_oxide_species_name``).
_NON_OXIDE_ELEMENT_FLAGS = frozenset({'Cl', 'F', 'Br', 'I', 'S'})


@dataclass(frozen=True)
class DomainGateAssessment:
    """Structured domain-gate result, including every failed constraint."""

    valid: bool
    warnings: Tuple[str, ...]
    reason: str | None
    failed_constraints: Tuple[str, ...]
    silicate_network_band_wt_pct: Tuple[float, float]


def _resolve_silicate_network_band(
    silicate_network_band: Sequence[float] | None,
) -> Tuple[float, float]:
    if silicate_network_band is None:
        # The DEFAULT is grandfathered and deliberately NOT floor-checked.
        # Note what that means: the default lower bound is 30.0, which is
        # BELOW the measured 34.0 crash floor, so the default band admits a
        # 30-34 wt% sliver in which three of four basalts SIGABRT. That is a
        # PRE-EXISTING hazard, not one the rail-owned band introduced, and
        # narrowing the default is golden-affecting -- it must be a separate,
        # deliberate decision rather than a side effect of adding the floor.
        # Filed rather than silently changed.
        return DEFAULT_SILICATE_NETWORK_BAND_WT_PCT
    if len(silicate_network_band) != 2:
        raise ValueError(
            'silicate_network_band must be a (min_wt_pct, max_wt_pct) pair'
        )
    minimum = float(silicate_network_band[0])
    maximum = float(silicate_network_band[1])
    if (
        minimum != minimum
        or maximum != maximum
        or minimum in (float('inf'), float('-inf'))
        or maximum in (float('inf'), float('-inf'))
        or minimum > maximum
    ):
        raise ValueError(
            'silicate_network_band must be a finite min<=max wt% interval, '
            f'got {tuple(silicate_network_band)!r}'
        )
    # Non-bypassable for an EXPLICIT band. Below the floor alphaMELTS SIGABRTs rather than
    # returning a distrusted number, so this is doctrine category (1) -- the
    # engine CANNOT compute -- not category (2) out-of-domain physics. It is
    # the same class as the adapter's two-component SIGSEGV guard and is
    # equally not a knob. Widening the band is a trust decision; lowering it
    # past the floor is a crash.
    # Passing the default explicitly must behave exactly like passing nothing;
    # an API where the documented default is itself rejected is a trap.
    if (minimum, maximum) == DEFAULT_SILICATE_NETWORK_BAND_WT_PCT:
        return DEFAULT_SILICATE_NETWORK_BAND_WT_PCT
    if minimum < _SIO2_CRASH_FLOOR_WT_PCT:
        raise ValueError(
            f'silicate_network_band lower bound {minimum} wt% is below the '
            f'measured alphaMELTS crash floor of {_SIO2_CRASH_FLOOR_WT_PCT} '
            'wt%. All 40 multi-component sub-30 wt% points SIGABRT '
            '(2026-08-16 rump-hotwire); three of four basalts die at 34.0. '
            'The rump is MELTS-INOPERABLE, not MELTS-untrusted -- route it '
            'to IMCC, which returns on 38 of the same 40 points.'
        )
    return (minimum, maximum)


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
    3. **Silicate-network criteria** — SiO2 inside the rail-owned
       calibration band (default [30, 80] wt%); sum of major oxides
       > 95 wt%. The band is a trust window the caller controls DOWN TO
       the measured crash floor at 34.0 wt%; below that alphaMELTS
       SIGABRTs and the band stops being a trust question.
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
        *,
        silicate_network_band: Sequence[float] | None = None,
    ) -> Tuple[bool, List[str]]:
        """Validate ``composition_wt_pct`` against the MELTS 14-oxide basis.

        Parameters
        ----------
        composition_wt_pct:
            Mapping ``species_name -> wt%``. Must be derived from the
            silicate-oxide melt projection (``MeltState.composition_wt_pct``
            or the kernel's account-view oxide projection). Non-oxide
            species in this mapping are treated as a domain violation.
        silicate_network_band:
            Rail-owned ``(min, max)`` SiO2 wt% interval. ``None`` uses
            :data:`DEFAULT_SILICATE_NETWORK_BAND_WT_PCT` ([30, 80]).

        Returns
        -------
        ``(valid, warnings)`` -- ``valid`` is ``True`` iff every check
        passed; ``warnings`` lists the human-readable rejection reasons.
        """
        assessment = AlphaMELTSDomainGate.assess(
            composition_wt_pct,
            silicate_network_band=silicate_network_band,
        )
        return assessment.valid, list(assessment.warnings)

    @staticmethod
    def validate_with_reason(
        composition_wt_pct: Mapping[str, float],
        *,
        silicate_network_band: Sequence[float] | None = None,
    ) -> Tuple[bool, List[str], str | None]:
        """Validate and return the structured out-of-domain reason code."""
        assessment = AlphaMELTSDomainGate.assess(
            composition_wt_pct,
            silicate_network_band=silicate_network_band,
        )
        return assessment.valid, list(assessment.warnings), assessment.reason

    @staticmethod
    def assess(
        composition_wt_pct: Mapping[str, float],
        *,
        silicate_network_band: Sequence[float] | None = None,
    ) -> DomainGateAssessment:
        """Validate and report every failed constraint by name.

        ``failed_constraints`` distinguishes the rail-owned silicate-
        network band from the oxide-basis and major-oxide-sum checks.
        ``reason`` keeps the existing first-wins ``OutOfDomainReason``
        token so current callers stay golden-neutral.
        """
        band = _resolve_silicate_network_band(silicate_network_band)
        sio2_min_wt_pct, sio2_max_wt_pct = band
        warnings: List[str] = []
        failed: List[str] = []
        reason: OutOfDomainReason | None = None

        if not composition_wt_pct:
            warnings.append(
                'AlphaMELTSDomainGate: empty composition; cannot equilibrate.'
            )
            return DomainGateAssessment(
                valid=False,
                warnings=tuple(warnings),
                reason=OutOfDomainReason.MAJOR_SUM.value,
                failed_constraints=(CONSTRAINT_MAJOR_OXIDE_SUM,),
                silicate_network_band_wt_pct=band,
            )

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
            if wt < 0.0:
                reason = reason or OutOfDomainReason.MAJOR_SUM
                if CONSTRAINT_MAJOR_OXIDE_SUM not in failed:
                    failed.append(CONSTRAINT_MAJOR_OXIDE_SUM)
                warnings.append(
                    f'AlphaMELTSDomainGate: negative wt% for {raw_name!r}'
                )
                continue
            if wt == 0.0:
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
                # basis member. Keep it in the canonical accounting map so
                # the major-oxide gate sees valid total-iron basalt while the
                # adapter's redox split still runs cleanly downstream.
                canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt
            else:
                canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt

        if non_oxides:
            reason = OutOfDomainReason.FORBIDDEN_SPECIES
            failed.append(CONSTRAINT_OXIDE_BASIS)
            warnings.append(
                'AlphaMELTSDomainGate: non-oxide species present '
                f'(metal / sulfide / halide -- must route through Stage 0 '
                f'first): {sorted(non_oxides)}'
            )
        if unrecognised:
            reason = reason or OutOfDomainReason.FORBIDDEN_SPECIES
            if CONSTRAINT_OXIDE_BASIS not in failed:
                failed.append(CONSTRAINT_OXIDE_BASIS)
            warnings.append(
                'AlphaMELTSDomainGate: unrecognised species outside MELTS '
                f'14-oxide basis: {sorted(unrecognised)}'
            )

        sio2_pct = canonical_wt.get('SiO2', 0.0)
        if sio2_pct < sio2_min_wt_pct or sio2_pct > sio2_max_wt_pct:
            reason = reason or OutOfDomainReason.SILICATE_WINDOW
            failed.append(CONSTRAINT_SILICATE_NETWORK_BAND)
            warnings.append(
                f'AlphaMELTSDomainGate: SiO2 = {sio2_pct:.3f} wt% outside '
                f'MELTS calibration range '
                f'[{sio2_min_wt_pct}, {sio2_max_wt_pct}] wt%.'
            )

        # Major oxide sum: MELTS 14-oxide basis members plus FeO_total.
        # FeO_total is not sent to MELTS directly; it is admitted into the
        # silicate-network criterion so the downstream explicit redox split
        # can reject or project it under the redox-policy gate instead of this
        # composition-only gate undercounting valid total-iron basalt.
        major_total = (
            sum(canonical_wt.get(oxide, 0.0) for oxide in MELTS_OXIDE_BASIS)
            + canonical_wt.get('FeO_total', 0.0)
        )
        if major_total <= _MAJOR_OXIDE_MIN_TOTAL_WT_PCT:
            reason = reason or OutOfDomainReason.MAJOR_SUM
            if CONSTRAINT_MAJOR_OXIDE_SUM not in failed:
                failed.append(CONSTRAINT_MAJOR_OXIDE_SUM)
            warnings.append(
                f'AlphaMELTSDomainGate: major-oxide sum = {major_total:.3f} '
                f'wt% <= {_MAJOR_OXIDE_MIN_TOTAL_WT_PCT} wt%; composition '
                'is dominated by non-MELTS species.'
            )

        if warnings and reason is None:
            reason = OutOfDomainReason.MAJOR_SUM
            if CONSTRAINT_MAJOR_OXIDE_SUM not in failed:
                failed.append(CONSTRAINT_MAJOR_OXIDE_SUM)
        return DomainGateAssessment(
            valid=not warnings,
            warnings=tuple(warnings),
            reason=reason_value(reason),
            failed_constraints=tuple(failed),
            silicate_network_band_wt_pct=band,
        )

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


def canonical_melt_oxide_activity_name(name: object) -> str | None:
    """Map an exact oxide-basis activity label to a canonical key.

    This helper deliberately does not convert endmember, cation, vapor, or
    component labels such as ``Na``, ``Na2SiO3``, or ``Mg2SiO4`` into oxide
    activities. Those labels can be reported diagnostically, but without a
    thermodynamic basis conversion they are not ``Na2O``/``MgO`` activities.
    """

    key = str(name).strip().strip('"\'')
    if not key:
        return None
    match = re.match(r'^(?:a|activity)\(([^)]+)\)$', key, flags=re.IGNORECASE)
    if match:
        key = match.group(1).strip()
    if key.endswith('_Liq'):
        key = key[:-4]
    oxide = _canonical_oxide_name(key)
    if oxide == 'FeO_total':
        return 'FeO'
    if oxide in _MELTS_OXIDE_SET:
        return oxide
    return None


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
    'CONSTRAINT_MAJOR_OXIDE_SUM',
    'CONSTRAINT_OXIDE_BASIS',
    'CONSTRAINT_SILICATE_NETWORK_BAND',
    'DEFAULT_SILICATE_NETWORK_BAND_WT_PCT',
    'DEFAULT_SIO2_MAX_WT_PCT',
    'DEFAULT_SIO2_MIN_WT_PCT',
    'DomainGateAssessment',
    'MELTS_OXIDE_BASIS',
    'canonical_melt_oxide_activity_name',
)
