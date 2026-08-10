"""
SulfLiq matte a_FeS provider
============================

Optional adapter around the ENKI ``SulfLiq`` extension (Kress associated-
solution model for O–S–Fe–Ni–Cu sulfide liquid) that returns the activity of
the FeS associated species, ``a_FeS``.

This is a **provider only**. It does not touch the AtomLedger, does not
participate in any vapour-rail evaluator, and is not a ``MeltBackend``. The
S-track (O'Neill SCSS chain slot ``LnS = LnCS2 + ΔG + Ln_a_FeS − Ln_a_FeO``)
is the intended consumer.

Model / citation
----------------
* Kress, V. (1997) Thermochemistry of sulfide liquids. I. The system O-S-Fe
  at 1 bar. Contrib. Mineral. Petrol. 127:176–186.
  DOI 10.1007/s004100050274
* Kress, V. (2000) Thermochemistry of sulfide liquids. II. Associated
  solution model for sulfide liquids in the system O-S-Fe.
  Contrib. Mineral. Petrol. 139:316–325. DOI 10.1007/s004100000143
* Kress, V. (2007) III: Ni-bearing liquids. Contrib. Mineral. Petrol.
  154:191–204. DOI 10.1007/s00410-007-0187-7
* Kress et al. (2008) IV: density + O-S-Fe-Ni-Cu. Contrib. Mineral. Petrol.
  156:785–797. DOI 10.1007/s00410-008-0315-z

Package: https://gitlab.com/ENKI-portal/sulfliq (pybind11 extension
``SulfLiq``). ThermoEngine's ``thermoengine.sulfide_liquid`` is the
coder-style wrapper over the same extension; this module talks to
``SulfLiq`` directly so it can run without TE.

``a_FeS`` definition
--------------------
FeS is an *associated species* of the Kress model (species index 8,
formula ``FeS``), not an endmember component. Activity is the species
Raoultian activity against the pure-species standard state coded in
SulfLiq:

    a_FeS = exp( (μ_FeS − μ0_FeS) / (R · T) )

with ``μ_FeS = getSpecMu(8)`` and ``μ0_FeS = getMu0(8)``. For pure
stoichiometric FeS liquid the free Fe + S dissociation leaves
``a_FeS ≈ 0.97`` (not exactly 1) — that is model physics, not a bug.

S3 / LnS consumer contract (read before wiring)
-----------------------------------------------
* ``a_FeS`` is dimensionless Raoultian species activity. Use natural log:
  ``Ln_a_FeS = math.log(result.a_FeS)`` only when
  ``result.calibration_status == 'in_range'`` and ``math.isfinite(result.a_FeS)``.
* On ``unavailable`` / bad input, ``a_FeS`` is ``float('nan')`` (not 0.0) so
  naive ``ln(a_FeS)`` yields NaN rather than ``-inf``. Prefer the
  ``ActivityResult.ln_a_FeS`` property, which is finite only for
  ``in_range`` successes.
* ``log_fO2`` / ``log_fS2`` are **log₁₀** (petrologic convention; matches
  SulfLiq ``getlogfo2`` / ``getlogfs2``). Do not treat them as natural log.
* ``matte_composition`` is bulk matte **O, S, Fe, Ni, Cu** (moles or mole
  fractions, renormalised). There is no ledger → 5-vector adapter here;
  the S-track caller must already own matte bulk speciation.
* Pure-FeS zero-O leaves ``log_fO2 is None`` (engine refuses fo2 with O≤0).

Adapter conventions (mirrors ``sulfsat.py``)
--------------------------------------------
* Optional import of ``SulfLiq``; failure → typed ``unavailable`` result.
* Never raises for missing engine or bad composition; returns
  ``ActivityResult`` with ``calibration_status`` and ``warnings``.
* No ledger mutation, no rail wiring.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

# Gas constant, J/(mol·K). Matches SulfLiq Phase::R in Phase.cc (8.31468),
# which is what getSpecMu / getMu0 / fugacity paths use internally.
# (CODATA 2018 is 8.314462618; Δa_FeS from the swap is ≪0.01% at 1500 K.)
_R_J_PER_MOL_K = 8.31468

# Endmember component order expected by SulfLiq.setComps: O, S, Fe, Ni, Cu.
_COMPONENT_ORDER = ('O', 'S', 'Fe', 'Ni', 'Cu')

# FeS associated-species index in SulfLiq (TTDEX / "tt liquid").
_FES_SPECIES_INDEX = 8

# Standard-state chemical potential of FeS liquid at 1300 K, 1 bar (J/mol).
# Equals SLSSData g0 + ge1300 for the "tt liquid" / FeS row in SulfLiq.cc:
#   g0(FeS, 1300 K) = −93139.49 J/mol
#   ge1300          = −164460.1212 J/mol
#   μ0 = g0 + ge1300 = −257599.6112 J/mol
# Parameters are those of the Kress associated-solution model (Kress 2000
# DOI 10.1007/s004100000143; open-source encoding in ENKI-portal/sulfliq).
FES_MU0_1300K_J_PER_MOL = -257599.6112

SULFLIQ_CALIBRATION_VERSION = 'kress-sulfliq-1.0.4-a_FeS-v2'


@dataclass
class ActivityResult:
    """
    Result of an a_FeS evaluation.

    ``calibration_status``:
    * ``'in_range'``    — SulfLiq ran; composition accepted; activity finite.
    * ``'out_of_range'`` — ran, but composition/T outside the documented
      model envelope (or activity non-positive / non-finite).
    * ``'unavailable'`` — SulfLiq not installed / import failed / bad inputs.
      ``a_FeS`` is NaN (not 0.0) so ``ln(a_FeS)`` does not silently go to −∞.

    ``log_fO2`` / ``log_fS2`` are **log₁₀** (base 10), or None when the
    engine cannot evaluate them (e.g. pure-FeS zero-O leaves log_fO2 None).
    """

    a_FeS: float = field(default_factory=lambda: float('nan'))
    T_K: float = 0.0
    P_bar: float = 1.0
    log_fO2: Optional[float] = None  # log10(fO2)
    log_fS2: Optional[float] = None  # log10(fS2)
    is_stable: Optional[bool] = None
    species_mole_fractions: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    calibration_status: str = 'unavailable'
    calibration_version: str = SULFLIQ_CALIBRATION_VERSION

    @property
    def ln_a_FeS(self) -> float:
        """
        Natural log of a_FeS for the O'Neill LnS chain slot.

        Returns NaN unless ``calibration_status == 'in_range'`` and
        ``a_FeS`` is finite and positive — never ``-inf`` from ln(0).
        """
        if self.calibration_status != 'in_range':
            return float('nan')
        if not math.isfinite(self.a_FeS) or self.a_FeS <= 0.0:
            return float('nan')
        return math.log(self.a_FeS)


class SulfLiqMatteProvider:
    """
    Lazy, optional provider for matte ``a_FeS`` via ENKI SulfLiq.

    Usage::

        provider = SulfLiqMatteProvider()
        provider.initialize()
        if provider.is_available():
            result = provider.a_FeS(
                T_K=1473.15,
                matte_composition={'S': 0.5, 'Fe': 0.5},
            )
            if result.calibration_status == 'in_range':
                ln_a = result.ln_a_FeS  # natural log; safe
    """

    def __init__(self) -> None:
        self._available = False
        self._module: Any = None
        self._init_error: str = ''

    def initialize(self, config: Optional[Mapping[str, Any]] = None) -> bool:
        """Probe ``SulfLiq`` lazily. ``config`` reserved for future knobs."""
        del config  # unused; API symmetry with SulfSatGate
        try:
            self._module = importlib.import_module('SulfLiq')
        except Exception as exc:  # noqa: BLE001 — lazy probe path
            self._available = False
            self._module = None
            self._init_error = f'SulfLiq import failed: {exc!r}'
            return False
        # Touch the constructor so a broken .so (wrong ABI) fails here.
        try:
            _ = self._module.pySulfLiq()
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._module = None
            self._init_error = f'SulfLiq.pySulfLiq() failed: {exc!r}'
            return False
        self._available = True
        self._init_error = ''
        return True

    def is_available(self) -> bool:
        return bool(self._available and self._module is not None)

    def package_version(self) -> str:
        if self._module is not None:
            module_version = getattr(self._module, '__version__', None)
            if module_version is not None:
                return str(module_version)
        for distribution_name in ('SulfLiq', 'sulfliq'):
            try:
                return str(importlib_metadata.version(distribution_name))
            except importlib_metadata.PackageNotFoundError:
                continue
        return 'unavailable'

    def calibration_version(self) -> str:
        return SULFLIQ_CALIBRATION_VERSION

    def calibration_identifiers(self) -> Dict[str, str]:
        """
        Identifiers matching ThermoEngine's sulfide_liquid calib surface.

        Available even when only the raw SulfLiq extension is importable
        (does not require TE).
        """
        return {
            'calib_identifier': 'Version_1_0_0',
            'calib_name': 'Sulfide Liquid',
            'model': 'Kress associated solution O-S-Fe-Ni-Cu',
            'package_version': self.package_version(),
            'calibration_version': self.calibration_version(),
            'fes_species_index': str(_FES_SPECIES_INDEX),
            'component_order': ','.join(_COMPONENT_ORDER),
            'log_f_base': '10',  # log_fO2 / log_fS2 are log10
            'R_J_per_mol_K': str(_R_J_PER_MOL_K),
        }

    def a_FeS(
        self,
        T_K: float,
        matte_composition: Union[Mapping[str, float], Sequence[float]],
        *,
        P_bar: float = 1.0,
    ) -> ActivityResult:
        """
        Evaluate ``a_FeS`` for a matte composition at ``T_K``.

        Parameters
        ----------
        T_K
            Temperature in kelvin.
        matte_composition
            Either a mapping keyed by ``O``, ``S``, ``Fe``, ``Ni``, ``Cu``
            (moles or mole fractions — renormalised internally) or a length-5
            sequence in that component order. This is **matte bulk**, not
            silicate melt oxides.
        P_bar
            Pressure in bar (default 1 bar; SulfLiq takes Pa internally).

        Returns
        -------
        ActivityResult
            ``a_FeS`` is NaN when status is ``unavailable``; never 0.0 as a
            stand-in for missing data. ``log_fO2`` / ``log_fS2`` are log₁₀.
        """
        if not self.is_available():
            return ActivityResult(
                T_K=_safe_float(T_K, 0.0),
                P_bar=_safe_float(P_bar, 1.0),
                warnings=[
                    self._init_error
                    or 'SulfLiq not initialised; build ENKI-portal/sulfliq '
                    'against this CPython ABI'
                ],
                calibration_status='unavailable',
            )

        try:
            T_K_f = float(T_K)
            P_bar_f = float(P_bar)
        except (TypeError, ValueError) as exc:
            return ActivityResult(
                warnings=[f'invalid T_K/P_bar: {exc!r}'],
                calibration_status='unavailable',
            )
        if not math.isfinite(T_K_f) or T_K_f <= 0.0:
            return ActivityResult(
                T_K=T_K_f,
                P_bar=P_bar_f,
                warnings=[f'invalid T_K={T_K_f}; must be finite and > 0'],
                calibration_status='unavailable',
            )
        if not math.isfinite(P_bar_f) or P_bar_f <= 0.0:
            return ActivityResult(
                T_K=T_K_f,
                P_bar=P_bar_f,
                warnings=[f'invalid P_bar={P_bar_f}; must be finite and > 0'],
                calibration_status='unavailable',
            )

        try:
            comps = _coerce_matte_composition(matte_composition)
        except (TypeError, ValueError) as exc:
            return ActivityResult(
                T_K=T_K_f,
                P_bar=P_bar_f,
                warnings=[f'invalid matte_composition: {exc!r}'],
                calibration_status='unavailable',
            )

        warnings_list: List[str] = []
        # Documented SulfLiq T envelope (SulfLiq.cc init: lowT=700, highT=4000).
        if T_K_f < 700.0 or T_K_f > 4000.0:
            warnings_list.append(
                f'T_K={T_K_f:.1f} outside SulfLiq documented envelope '
                f'[700, 4000] K'
            )
        # High-P envelope highP=6e9 Pa ≈ 60 kbar; warn only.
        if P_bar_f > 6.0e4:
            warnings_list.append(
                f'P_bar={P_bar_f:.3g} exceeds SulfLiq highP (~6e4 bar)'
            )

        try:
            sl = self._module.pySulfLiq()
            sl.setTK(T_K_f)
            sl.setPa(P_bar_f * 1.0e5)
            sl.setComps(list(comps))
            sl.setSpeciateTolerance(1.0e-16)
        except Exception as exc:  # noqa: BLE001
            return ActivityResult(
                T_K=T_K_f,
                P_bar=P_bar_f,
                warnings=warnings_list + [f'SulfLiq setup failed: {exc!r}'],
                calibration_status='unavailable',
            )

        try:
            mu0 = float(sl.getMu0(_FES_SPECIES_INDEX))
            mu = float(sl.getSpecMu(_FES_SPECIES_INDEX))
            a_fes = math.exp((mu - mu0) / (_R_J_PER_MOL_K * T_K_f))
        except Exception as exc:  # noqa: BLE001
            return ActivityResult(
                T_K=T_K_f,
                P_bar=P_bar_f,
                warnings=warnings_list
                + [f'SulfLiq a_FeS evaluation failed: {exc!r}'],
                calibration_status='unavailable',
            )

        log_fo2: Optional[float] = None
        log_fs2: Optional[float] = None
        is_stable: Optional[bool] = None
        species_frac: Dict[str, float] = {}
        try:
            is_stable = bool(sl.isStable())
        except Exception as exc:  # noqa: BLE001
            warnings_list.append(f'isStable failed: {exc!r}')
        try:
            # Pure FeS (zero O) makes getlogfo2 throw; leave None then.
            # Returned values are log10 (SulfLiq API / petrologic convention).
            if comps[0] > 0.0:
                log_fo2 = float(sl.getlogfo2())
            log_fs2 = float(sl.getlogfs2())
        except Exception as exc:  # noqa: BLE001
            warnings_list.append(f'logfO2/logfS2 unavailable: {exc!r}')
        try:
            specs = list(sl.getSpecs())
            total = sum(specs)
            if total > 0.0:
                nspec = int(sl.getNspec())
                for i in range(nspec):
                    name = str(sl.getSpecFormula(i))
                    species_frac[name] = float(specs[i] / total)
        except Exception as exc:  # noqa: BLE001
            warnings_list.append(f'species readout failed: {exc!r}')

        status = 'in_range'
        if warnings_list and any(
            'outside SulfLiq' in w or 'exceeds SulfLiq' in w for w in warnings_list
        ):
            status = 'out_of_range'
        if not math.isfinite(a_fes) or a_fes <= 0.0:
            warnings_list.append(f'a_FeS non-physical: {a_fes!r}')
            status = 'out_of_range'
            a_fes = float('nan')

        return ActivityResult(
            a_FeS=float(a_fes),
            T_K=T_K_f,
            P_bar=P_bar_f,
            log_fO2=log_fo2,
            log_fS2=log_fs2,
            is_stable=is_stable,
            species_mole_fractions=species_frac,
            warnings=warnings_list,
            calibration_status=status,
        )


def a_FeS(
    T_K: float,
    matte_composition: Union[Mapping[str, float], Sequence[float]],
    *,
    P_bar: float = 1.0,
    provider: Optional[SulfLiqMatteProvider] = None,
) -> ActivityResult:
    """
    Module-level convenience: ``a_FeS(T_K, matte_composition) -> ActivityResult``.

    Instantiates and initialises a provider when one is not supplied.
    See ``SulfLiqMatteProvider.a_FeS`` and the module docstring S3/LnS
    consumer contract before calling ``math.log`` on ``result.a_FeS``.
    """
    if provider is None:
        provider = SulfLiqMatteProvider()
        provider.initialize()
    elif not provider.is_available() and not provider._init_error:
        # Caller constructed but did not initialize.
        provider.initialize()
    return provider.a_FeS(T_K, matte_composition, P_bar=P_bar)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_matte_composition(
    matte_composition: Union[Mapping[str, float], Sequence[float]],
) -> List[float]:
    """Normalise to a length-5 mole-fraction vector (O, S, Fe, Ni, Cu)."""
    if isinstance(matte_composition, Mapping):
        comps = [
            float(matte_composition.get(name, 0.0)) for name in _COMPONENT_ORDER
        ]
    else:
        seq = list(matte_composition)
        if len(seq) != 5:
            raise ValueError(
                f'matte_composition sequence must have length 5 '
                f'(O,S,Fe,Ni,Cu); got {len(seq)}'
            )
        comps = [float(x) for x in seq]
    if any(not math.isfinite(x) for x in comps):
        raise ValueError(f'non-finite matte composition: {comps}')
    if any(x < 0.0 for x in comps):
        raise ValueError(f'negative matte composition component: {comps}')
    total = sum(comps)
    if total <= 0.0:
        raise ValueError('matte_composition sums to zero')
    return [x / total for x in comps]
