"""MAGEMin composition-domain gate.

MAGEMin and alphaMELTS share the 14-oxide MELTS basis (see
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §4). Anything
non-silicate (native Fe, halides, sulfides, elemental S, etc.) must be
rejected before the chemistry call — these species violate MAGEMin's
solid-solution model and produce silent garbage on output.

This module exposes a single class, :class:`MAGEMinDomainGate`, that
returns ``(valid, warnings)`` and never raises. The caller (the kernel
planner post-carve-out; the today-hook adapter pre-carve-out) is
responsible for routing rejected compositions elsewhere (e.g. builtin
Stage 0 cleanup).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Tuple

# Canonical MELTS 14-oxide basis. Must match
# ``simulator.state.OXIDE_SPECIES`` and the basis declared in
# ``engines/magemin/provider.py::MAGEMinShadowProvider.capability_profile``.
_MELTS_OXIDE_BASIS: Tuple[str, ...] = (
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
    'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
)
_MELTS_OXIDE_SET = frozenset(_MELTS_OXIDE_BASIS)

# Species that disqualify the composition outright (regardless of amount).
# These appear in regolith feedstocks but break MAGEMin's solid-solution
# assumptions. Include common spellings.
_FORBIDDEN_SPECIES: Tuple[str, ...] = (
    # Native metals
    'Fe', 'Ni', 'Co', 'Cu', 'Cr', 'Mn', 'Si', 'Al', 'Mg', 'Ca',
    'Na', 'K', 'Ti',
    # Halides
    'Cl', 'F', 'Br', 'I',
    'NaCl', 'KCl', 'CaCl2', 'MgCl2', 'FeCl2', 'FeCl3', 'AlCl3',
    'NaF', 'KF', 'CaF2', 'MgF2',
    # Sulfides / elemental sulfur
    'S', 'S2', 'FeS', 'FeS2', 'NiS', 'Ni3S2', 'Cu2S', 'CuS',
    'CaS', 'MgS', 'Na2S', 'K2S',
    # Perchlorates / chlorates (Mars regolith)
    'NaClO4', 'KClO4', 'Mg(ClO4)2', 'Ca(ClO4)2',
    # Carbonates / nitrates (Stage 0 territory, not melt)
    'CaCO3', 'MgCO3', 'Na2CO3', 'K2CO3', 'FeCO3',
    'NaNO3', 'KNO3', 'Ca(NO3)2',
)
_FORBIDDEN_SET = frozenset(_FORBIDDEN_SPECIES)

# Tolerance constants. Keep aligned with the AlphaMELTS DomainGate (see
# \goal ALPHAMELTS-HARDENING checklist item 5).
_SIO2_MIN_WT_PCT = 30.0
_SIO2_MAX_WT_PCT = 80.0
_MAJOR_OXIDE_MIN_TOTAL_WT_PCT = 95.0


class MAGEMinDomainGate:
    """Validate a melt composition against MAGEMin's 14-oxide MELTS basis.

    The gate does **not** raise. It returns ``(valid, warnings)`` so the
    caller can decide whether to route the composition to a different
    engine, run Stage 0 cleanup first, or fail loudly through the
    planner. Returning warnings rather than raising matches the
    binding-spec requirement that engine failures route through the
    planner instead of bypassing intent authority.
    """

    @staticmethod
    def validate(
        composition_wt_pct: Mapping[str, float],
    ) -> Tuple[bool, List[str]]:
        """Validate ``composition_wt_pct`` against MAGEMin's input contract.

        Parameters
        ----------
        composition_wt_pct:
            Mapping from species name to wt%. Expected to be the cleaned
            silicate-oxide composition from ``MeltState``. Non-oxide
            species in this mapping are treated as a domain violation
            (Stage 0 cleanup must run first).

        Returns
        -------
        ``(valid, warnings)`` — ``valid`` is ``True`` iff the composition
        passes every check; ``warnings`` is the list of human-readable
        reasons (empty when ``valid`` is True).

        Checks
        ------
        1. Composition mapping is non-empty.
        2. No forbidden species (native metals, halides, sulfides,
           perchlorates, carbonates, nitrates, elemental S).
        3. SiO2 ∈ [30, 80] wt%.
        4. Sum of major oxides > 95 wt% (i.e. trace species must not
           dominate).
        5. All species in the input map are in the 14-oxide MELTS basis.
        """
        warnings: List[str] = []

        if not composition_wt_pct:
            warnings.append(
                'MAGEMinDomainGate: empty composition; cannot equilibrate.'
            )
            return False, warnings

        normalized = {
            str(species).strip(): float(value)
            for species, value in composition_wt_pct.items()
            if _is_finite(value)
        }
        if not normalized:
            warnings.append(
                'MAGEMinDomainGate: no finite-valued species in composition.'
            )
            return False, warnings

        # Check 2: forbidden non-oxide species.
        forbidden_present = sorted(
            species
            for species, amount in normalized.items()
            if amount > 0.0 and species in _FORBIDDEN_SET
        )
        if forbidden_present:
            warnings.append(
                'MAGEMinDomainGate: non-oxide species present '
                f'(must route through Stage 0 first): {forbidden_present}'
            )

        # Check 5: every species recognised.
        unknown = sorted(
            species
            for species, amount in normalized.items()
            if amount > 0.0
            and species not in _MELTS_OXIDE_SET
            and species not in _FORBIDDEN_SET
        )
        if unknown:
            warnings.append(
                'MAGEMinDomainGate: unrecognised species outside MELTS '
                f'14-oxide basis: {unknown}'
            )

        # Check 3: SiO2 in [30, 80] wt%.
        sio2 = normalized.get('SiO2', 0.0)
        if sio2 < _SIO2_MIN_WT_PCT or sio2 > _SIO2_MAX_WT_PCT:
            warnings.append(
                f'MAGEMinDomainGate: SiO2 = {sio2:.2f} wt% outside MELTS '
                f'calibration range [{_SIO2_MIN_WT_PCT}, '
                f'{_SIO2_MAX_WT_PCT}] wt%.'
            )

        # Check 4: major oxides total > 95 wt%.
        major_total = sum(
            normalized.get(species, 0.0)
            for species in _MELTS_OXIDE_BASIS
        )
        if major_total < _MAJOR_OXIDE_MIN_TOTAL_WT_PCT:
            warnings.append(
                f'MAGEMinDomainGate: major-oxide total = {major_total:.2f} '
                f'wt% below {_MAJOR_OXIDE_MIN_TOTAL_WT_PCT} wt%; '
                'composition is dominated by non-oxide species.'
            )

        return (not warnings), warnings

    @staticmethod
    def oxide_basis() -> Tuple[str, ...]:
        """Return the canonical MELTS 14-oxide basis."""
        return _MELTS_OXIDE_BASIS

    @staticmethod
    def forbidden_species() -> Iterable[str]:
        """Return the species that disqualify a composition from MAGEMin."""
        return _FORBIDDEN_SPECIES


def _is_finite(value: object) -> bool:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return numeric == numeric and numeric not in (float('inf'), float('-inf'))
