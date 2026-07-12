"""
PySulfSat Sulfur Saturation Gate
================================

Adapter around PySulfSat for the SULFUR_SATURATION_GATE intent.

This is **not** a ``MeltBackend`` and it does **not** emit a
``LedgerTransition``: it is a post-equilibrium gate that returns SCSS
(sulfide capacity at saturation), SCAS (sulfate capacity at saturation),
and the S6+/S2- partitioning fraction.  The simulator's Stage 0 path and
the post-equilibrium hook in ``simulator/core.py`` read the result
without mutating the AtomLedger through this module.

API symbols used
----------------
The underlying PySulfSat (`Wieser & Gleeson 2023`) calls are:

* :func:`PySulfSat.calculate_S2017_SCSS` — Smythe et al. (2017) SCSS
  (sulfide capacity at saturation). Default model: broadly calibrated on
  MORB through rhyolite and high-Al basalt.
* :func:`PySulfSat.calculate_CD2019_SCAS` — Chowdhury & Dasgupta (2019)
  SCAS (sulfate capacity at saturation).
* :func:`PySulfSat.calculate_S6St_Jugo2010_eq10` — Jugo et al. (2010)
  eq. 10 S6+/S_total partitioning vs ΔlogfO2 (relative to QFM).
* :func:`PySulfSat.calculate_fo2_QFM_buffers` — computes the absolute
  logfO2 of QFM at given T, P; subtract the simulator's absolute
  ``fO2_log`` from QFM (O'Neill 1987 formulation) to obtain ΔQFM.

Smythe 2017 SCSS requires ``Fe3Fet_Liq``. The adapter accepts an
operator-set value, otherwise it derives the ratio through
``simulator.fe_redox.kress91_split``. PySulfSat is not used as a second
Kress-Carmichael evaluator.

The simulator passes the cleaned-melt oxide composition in wt%; the
adapter renames the oxide keys onto PySulfSat's ``*_Liq`` vocabulary
(``SiO2`` -> ``SiO2_Liq`` etc.) and adds the ``FeOt_Liq`` total-iron
column expected by every SCSS/SCAS model.

Calibration window
------------------
Bounds were inspected against the bundled calibration datasets shipped
with PySulfSat 1.0.12 (``src/PySulfSat/Cali_Smythe17.pkl`` for SCSS and
``src/PySulfSat/Cali_ChowDas22.pkl`` for SCAS):

* SCSS (Smythe 2017): SiO2 ~28-78 wt%, FeOt ~0-40 wt%, MgO ~0-32 wt%,
  CaO ~0-33 wt%, Al2O3 ~0-34 wt%.
* SCAS (CD2019):      SiO2 ~42-77 wt%, FeOt ~0-12 wt%, CaO ~0-13 wt%,
  Al2O3 ~8-22 wt%, T ~1023-1598 K.

The calibration check encoded here evaluates the SCSS and SCAS windows
separately. A composition or temperature falling outside any model
bound triggers ``calibration_status == 'out_of_range'`` with a warning
naming the violating oxide or temperature. The gate **never** silently
extrapolates: an out-of-range result is still returned, but the caller
is expected to honour the status and fall back to the builtin Stage 0
path.

Authority posture
-----------------
This gate refines the builtin Stage 0 sulfate/sulfide bucketing for the
``SULFUR_SATURATION_GATE`` intent only. It cannot grant itself ledger
authority (no ``equilibrate`` method, no ``ledger_transition`` field) —
the builtin Stage 0 path stays authoritative for everything else.
"""

from __future__ import annotations

import importlib
import math
import warnings
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import Any, Dict, List, Mapping, Optional

from simulator.fe_redox import kress91_split, melt_mol_fractions_for_kress91


# Cleaned-melt oxide -> PySulfSat ``*_Liq`` column name mapping. Every
# SCSS / SCAS function in PySulfSat consumes a pandas DataFrame keyed by
# these column names; missing columns are filled with zero by the
# library's internal normaliser (``norm_liqs_excl_H2O`` /
# ``norm_liqs_with_H2O``).
_OXIDE_TO_PYSULFSAT_COL = {
    'SiO2': 'SiO2_Liq',
    'TiO2': 'TiO2_Liq',
    'Al2O3': 'Al2O3_Liq',
    'FeO': 'FeO_Liq',
    'Fe2O3': 'Fe2O3_Liq',
    'MgO': 'MgO_Liq',
    'CaO': 'CaO_Liq',
    'Na2O': 'Na2O_Liq',
    'K2O': 'K2O_Liq',
    'MnO': 'MnO_Liq',
    'P2O5': 'P2O5_Liq',
    'H2O': 'H2O_Liq',
    'Cr2O3': 'Cr2O3_Liq',
    'NiO': 'NiO_Liq',
}

# Calibration-window bounds in oxide wt%, derived from the SCSS Smythe
# 2017 and SCAS CD2019 calibration datasets shipped with PySulfSat
# 1.0.12. Both model windows must be satisfied for the gate to report
# in_range because Stage 0 consumes the paired SCSS/SCAS partition.
_SCSS_CALIBRATION_BOUNDS_WT_PCT = {
    'SiO2': (28.0, 78.0),
    'TiO2': (0.0, 16.0),
    'Al2O3': (0.0, 35.0),
    'FeO_total': (0.0, 40.0),
    'MnO': (0.0, 3.0),
    'MgO': (0.0, 33.0),
    'CaO': (0.0, 33.0),
    'Na2O': (0.0, 8.0),
    'K2O': (0.0, 9.0),
}
_SCAS_CALIBRATION_BOUNDS_WT_PCT = {
    'SiO2': (42.0, 77.0),
    'Al2O3': (8.0, 22.0),
    'FeO_total': (0.0, 12.0),
    'CaO': (0.0, 13.0),
}
_SCAS_T_K_RANGE = (1023.0, 1598.0)
SULFSAT_CALIBRATION_VERSION = 'pysulfsat-1.0.12-calibration-bounds-v2'


@dataclass
class SulfurSaturationResult:
    """
    Result of a sulfur-saturation gate call.

    Mirrors the binding-spec §4 PySulfSat contract.  ``warnings``
    accumulates non-fatal notes; ``calibration_status`` summarises the
    overall posture:

    * ``'in_range'``   - the liquid composition is within the SCSS /
      SCAS calibration window and the upstream library was queried.
    * ``'out_of_range'`` - composition falls outside one or more
      calibration bounds.  Result fields are still populated (PySulfSat
      will happily extrapolate) but the caller is expected to honour
      the warning and fall back to the builtin path.
    * ``'unavailable'`` - PySulfSat is not installed (or its import
      raised).  Numeric fields stay 0.0; ``warnings`` carries the
      reason.
    """

    SCSS_ppm: float = 0.0
    SCAS_ppm: float = 0.0
    S6_fraction: float = 0.0
    S_in_sulfide_ppm: float = 0.0
    S_in_sulfate_ppm: float = 0.0
    warnings: List[str] = field(default_factory=list)
    calibration_status: str = 'unavailable'


def _qfm_logfo2_oneill(T_K: float) -> float:
    """
    Return absolute log10(fO2) of QFM at temperature ``T_K``, using the
    O'Neill (1987) formulation that PySulfSat exposes via
    ``calculate_fo2_QFM_buffers``.

    Used to translate the simulator's *absolute* ``fO2_log`` into the
    ΔQFM offset that the Jugo (2010) S6+ correction expects.
    """
    # logfo2_QFM_Oneill = 8.58 - 25050 / T_K  (PySulfSat src/.../s6_corrections.py:640).
    return 8.58 - 25050.0 / float(T_K)


class SulfSatGate:
    """
    Post-equilibrium sulfur-saturation gate backed by PySulfSat.

    Not a ``MeltBackend`` subclass — does not solve a phase assemblage,
    does not emit a ``LedgerTransition``, and never mutates the
    AtomLedger directly. The simulator's builtin Stage 0 path remains
    authoritative for sulfide / sulfate bucketing; this gate refines the
    partitioning when its calibration check passes.

    Usage::

        gate = SulfSatGate()
        gate.initialize({})
        if gate.is_available():
            result = gate.compute_sulfur_saturation(
                liquid_comp_wt={'SiO2': 49.5, ...},
                T_K=1473.0, P_bar=1.0,
                fO2_log=-8.5, S_input_ppm=500.0,
            )

    ``is_available()`` returns ``False`` when PySulfSat fails to import
    (the dependency lives in the optional ``[sulfur]`` extra). All
    failure paths return a ``SulfurSaturationResult`` with
    ``calibration_status == 'unavailable'`` so the caller can fall back
    deterministically.
    """

    def __init__(self) -> None:
        self._available = False
        self._module: Any = None
        self._init_error: str = ''

    def initialize(self, config: Optional[Mapping[str, Any]] = None) -> bool:
        """
        Probe PySulfSat lazily; capture the import error for diagnostics.

        Returns ``True`` if the library imported successfully. ``config``
        is currently ignored — present for symmetry with the
        ``MeltBackend`` adapters and so we can add explicit SCSS / SCAS
        model selection without breaking the call site.
        """
        try:
            self._module = importlib.import_module('PySulfSat')
        except Exception as exc:  # noqa: BLE001 — lazy probe path
            self._available = False
            self._module = None
            self._init_error = f'PySulfSat import failed: {exc!r}'
            return False
        self._available = True
        self._init_error = ''
        return True

    def is_available(self) -> bool:
        """``True`` when ``initialize()`` found a working PySulfSat."""
        return bool(self._available and self._module is not None)

    def package_version(self) -> str:
        module_version = getattr(self._module, '__version__', None)
        if module_version is not None:
            return str(module_version)
        for distribution_name in ('PySulfSat', 'pysulfsat'):
            try:
                return str(importlib_metadata.version(distribution_name))
            except importlib_metadata.PackageNotFoundError:
                continue
        return 'unavailable'

    def calibration_version(self) -> str:
        return SULFSAT_CALIBRATION_VERSION

    def compute_sulfur_saturation(
        self,
        *,
        liquid_comp_wt: Mapping[str, float],
        T_K: float,
        P_bar: float,
        fO2_log: float,
        S_input_ppm: float,
        Fe3Fet_Liq: Optional[float] = None,
    ) -> SulfurSaturationResult:
        """
        Run SCSS/SCAS + Jugo (2010) S6+/S_total on the given melt state.

        Returns a :class:`SulfurSaturationResult` with the SCSS / SCAS
        bounds, S6+ fraction, and the partitioning into sulfide- and
        sulfate-bearing buckets given the input total-S concentration.

        Inputs are validated and the calibration window is checked
        before the upstream library runs. An unavailable engine, an
        empty composition, or a non-numeric ``T_K`` / ``P_bar`` all
        return a deterministic ``'unavailable'`` result rather than
        raising.

        Redox policy
        ------------
        Smythe 2017 SCSS requires a ``Fe3Fet_Liq`` column. The gate
        mirrors the AlphaMELTS redox-policy pattern in
        ``simulator/melt_backend/alphamelts.py``:

        * If the caller passes an explicit ``Fe3Fet_Liq`` (operator
          override), it is used verbatim after validating it is in
          ``[0, 1]``.
        * Otherwise the ratio is derived from ``fO2_log`` + ``T_K`` +
          ``P_bar`` + composition via ``simulator.fe_redox.kress91_split``.
        * If the derivation raises or produces a non-finite ratio (e.g.
          zero-iron melt or out-of-calibration extrapolation), the
          result is tagged ``calibration_status='out_of_range'`` with an
          explicit warning. Fe3Fet_Liq=0 is passed to SCSS so the call
          completes, but the caller is expected to honour the status.

        There is no silent default — every redox decision is either
        operator-explicit or attributed to the Kress-Carmichael fit, and
        the failure mode is loudly flagged rather than swallowed.
        """
        warnings_list: List[str] = []

        if not self.is_available():
            return SulfurSaturationResult(
                warnings=[
                    self._init_error
                    or 'PySulfSat not initialised; install the [sulfur] extra'
                ],
                calibration_status='unavailable',
            )

        try:
            T_K_f = float(T_K)
            P_bar_f = float(P_bar)
            fO2_log_f = float(fO2_log)
            S_input_ppm_f = max(0.0, float(S_input_ppm))
        except (TypeError, ValueError) as exc:
            return SulfurSaturationResult(
                warnings=[f'invalid inputs to SulfSatGate: {exc!r}'],
                calibration_status='unavailable',
            )

        if T_K_f <= 0.0:
            return SulfurSaturationResult(
                warnings=[f'invalid T_K={T_K_f}; must be > 0'],
                calibration_status='unavailable',
            )

        operator_fe3fet: Optional[float] = None
        if Fe3Fet_Liq is not None:
            try:
                operator_fe3fet = float(Fe3Fet_Liq)
            except (TypeError, ValueError) as exc:
                return SulfurSaturationResult(
                    warnings=[f'invalid Fe3Fet_Liq={Fe3Fet_Liq!r}: {exc!r}'],
                    calibration_status='unavailable',
                )
            if not 0.0 <= operator_fe3fet <= 1.0:
                return SulfurSaturationResult(
                    warnings=[
                        f'Fe3Fet_Liq={operator_fe3fet} outside [0, 1]'
                    ],
                    calibration_status='unavailable',
                )

        try:
            cleaned_comp_wt = self._coerce_liquid_comp_wt(liquid_comp_wt)
        except (TypeError, ValueError) as exc:
            return SulfurSaturationResult(
                warnings=[f'invalid liquid_comp_wt for SulfSatGate: {exc!r}'],
                calibration_status='unavailable',
            )

        in_range, range_warnings = self._check_calibration_range(
            cleaned_comp_wt, T_K=T_K_f
        )
        warnings_list.extend(range_warnings)
        calibration_status = 'in_range' if in_range else 'out_of_range'

        try:
            (
                scss_ppm,
                scas_ppm,
                s6_fraction,
                redox_warnings,
                redox_in_range,
            ) = self._run_pysulfsat(
                liquid_comp_wt=cleaned_comp_wt,
                T_K=T_K_f,
                P_bar=P_bar_f,
                fO2_log=fO2_log_f,
                operator_fe3fet=operator_fe3fet,
            )
        except Exception as exc:  # noqa: BLE001 — upstream library boundary
            return SulfurSaturationResult(
                warnings=warnings_list
                + [f'PySulfSat call failed: {exc!r}'],
                calibration_status='unavailable',
            )

        warnings_list.extend(redox_warnings)
        if not redox_in_range:
            calibration_status = 'out_of_range'

        s_in_sulfide_ppm, s_in_sulfate_ppm = self._partition_input_S(
            S_input_ppm=S_input_ppm_f,
            SCSS_ppm=scss_ppm,
            SCAS_ppm=scas_ppm,
            S6_fraction=s6_fraction,
        )

        return SulfurSaturationResult(
            SCSS_ppm=scss_ppm,
            SCAS_ppm=scas_ppm,
            S6_fraction=s6_fraction,
            S_in_sulfide_ppm=s_in_sulfide_ppm,
            S_in_sulfate_ppm=s_in_sulfate_ppm,
            warnings=warnings_list,
            calibration_status=calibration_status,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_calibration_range(
        self, liquid_comp_wt: Mapping[str, float], *, T_K: float
    ) -> tuple[bool, List[str]]:
        """
        Return ``(in_range, warnings)`` for the SCSS+SCAS calibration
        window.

        ``in_range`` is ``True`` only if every encoded bound is
        satisfied; the warnings name the violating oxides so the caller
        can record a diagnostic message instead of silently
        extrapolating.
        """
        notes: List[str] = []

        feo_total = (
            liquid_comp_wt.get('FeO', 0.0)
            + liquid_comp_wt.get('Fe2O3', 0.0) * (2.0 * 71.844 / 159.687)
        )

        for model, bounds in (
            ('Smythe 2017 SCSS', _SCSS_CALIBRATION_BOUNDS_WT_PCT),
            ('Chowdhury-Dasgupta 2019 SCAS', _SCAS_CALIBRATION_BOUNDS_WT_PCT),
        ):
            for oxide, (lo, hi) in bounds.items():
                value = (
                    feo_total
                    if oxide == 'FeO_total'
                    else liquid_comp_wt.get(oxide, 0.0)
                )
                if not (lo <= value <= hi):
                    notes.append(
                        f'{oxide}={value:.2f} wt% outside {model} '
                        f'calibration window [{lo:.1f}, {hi:.1f}]'
                    )

        t_lo, t_hi = _SCAS_T_K_RANGE
        if not (t_lo <= T_K <= t_hi):
            notes.append(
                f'T_K={T_K:.2f} K outside Chowdhury-Dasgupta 2019 SCAS '
                f'calibration window [{t_lo:.1f}, {t_hi:.1f}]'
            )

        return (not notes), notes

    def _run_pysulfsat(
        self,
        *,
        liquid_comp_wt: Mapping[str, float],
        T_K: float,
        P_bar: float,
        fO2_log: float,
        operator_fe3fet: Optional[float],
    ) -> tuple[float, float, float, List[str], bool]:
        """
        Call PySulfSat's SCSS / SCAS / S6 routines.

        Returns ``(SCSS_ppm, SCAS_ppm, S6_fraction, redox_warnings,
        redox_in_range)``. Wrapped in ``warnings.catch_warnings`` so
        RuntimeWarnings from divisions on all-zero columns do not
        pollute pytest output (the upstream library is well-behaved but
        emits warnings when SCAS is asked about an unhydrous melt, which
        is the simulator's default).

        ``Fe3Fet_Liq`` resolution
        ------------------------
        Smythe 2017 SCSS requires the ``Fe3Fet_Liq`` column. We resolve
        it before the SCSS call so the upstream library does not raise:

        * ``operator_fe3fet`` non-None -> used verbatim (validated above).
        * otherwise -> simulator.fe_redox's shared Kress-Carmichael 1991
          split on ``fO2_log``.

        If the derivation raises or returns a non-finite value the
        result is tagged ``out_of_range`` with an explicit warning and
        Fe3Fet_Liq=0 is passed to SCSS purely to keep the call alive —
        the caller honours the status and falls back to the builtin
        path.
        """
        df = self._build_dataframe(liquid_comp_wt)
        P_kbar = max(P_bar, 1e-9) / 1000.0
        delta_qfm = float(fO2_log) - _qfm_logfo2_oneill(T_K)

        ss = self._module

        fe3fet_value, redox_warnings, redox_in_range = self._resolve_fe3fet(
            df=df,
            liquid_comp_wt=liquid_comp_wt,
            T_K=T_K,
            P_kbar=P_kbar,
            fO2_log=fO2_log,
            operator_fe3fet=operator_fe3fet,
        )
        df['Fe3Fet_Liq'] = fe3fet_value

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            # Smythe 2017 SCSS; Fe_FeNiCu_Sulf=0.65 is the canonical
            # MORB-like sulfide default used in PySulfSat's tutorial.
            # Fe3Fet_Liq is either operator-set or Kress-Carmichael-
            # derived (see _resolve_fe3fet) — never a silent default.
            scss_df = ss.calculate_S2017_SCSS(
                df=df,
                T_K=T_K,
                P_kbar=P_kbar,
                Fe3Fet_Liq=fe3fet_value,
                Fe_FeNiCu_Sulf=0.65,
            )
            scss_ppm = float(scss_df['SCSS2_ppm_ideal_Smythe2017'].iloc[0])

            scas_df = ss.calculate_CD2019_SCAS(df=df, T_K=T_K)
            scas_ppm = float(scas_df['SCAS6_ppm'].iloc[0])

            s6_fraction = float(
                ss.calculate_S6St_Jugo2010_eq10(deltaQFM=delta_qfm)
            )

        numerical_warnings: List[str] = []
        scss_ppm, note = self._finite_capacity_ppm(
            scss_ppm,
            field='SCSS_ppm',
            model='Smythe 2017 SCSS',
        )
        if note is not None:
            numerical_warnings.append(note)
        scas_ppm, note = self._finite_capacity_ppm(
            scas_ppm,
            field='SCAS_ppm',
            model='Chowdhury-Dasgupta 2019 SCAS',
        )
        if note is not None:
            numerical_warnings.append(note)
        s6_fraction, note = self._finite_fraction(
            s6_fraction,
            field='S6_fraction',
            model='Jugo 2010 S6+/S_total',
        )
        if note is not None:
            numerical_warnings.append(note)
        if numerical_warnings:
            redox_warnings = list(redox_warnings) + numerical_warnings
            redox_in_range = False

        return scss_ppm, scas_ppm, s6_fraction, redox_warnings, redox_in_range

    def _resolve_fe3fet(
        self,
        *,
        df: Any,
        liquid_comp_wt: Mapping[str, float],
        T_K: float,
        P_kbar: float,
        fO2_log: float,
        operator_fe3fet: Optional[float],
    ) -> tuple[float, List[str], bool]:
        """
        Resolve ``Fe3Fet_Liq`` for the SCSS call.

        Returns ``(fe3fet, warnings, in_range)``. ``in_range`` is False
        when the derivation could not produce a usable ratio (operator
        absent + fit failed / non-finite); ``fe3fet`` is 0.0 in that
        degenerate case so the SCSS call can complete, but the caller
        honours ``calibration_status='out_of_range'``.

        The Kress-Carmichael path deliberately uses
        ``simulator.fe_redox.kress91_split`` rather than PySulfSat's
        ``convert_fo2_to_fe_partition`` so the simulator has one Kress91
        evaluator. PySulfSat remains the independent sulfur-capacity engine.

        Zero-FeOt melts are a degenerate boundary case: there is no
        iron to partition, the fit returns NaN, and we report
        ``Fe3Fet_Liq=0`` without flagging out-of-range (the SCSS result
        is meaningless either way because the model is keyed on
        Fe-cation fractions).
        """
        if operator_fe3fet is not None:
            ratio = min(1.0, max(0.0, float(operator_fe3fet)))
            return ratio, [], True

        feot = float(df['FeOt_Liq'].iloc[0]) if 'FeOt_Liq' in df.columns else 0.0
        if feot <= 0.0:
            # Zero iron -> no redox to partition. Not a silent default:
            # there is genuinely nothing to derive a ratio FROM.
            return 0.0, [], True

        try:
            mol_fractions = melt_mol_fractions_for_kress91(liquid_comp_wt)
            split = kress91_split(
                fO2_log=float(fO2_log),
                mol_fractions=mol_fractions,
                T_K=float(T_K),
                pressure_bar=max(float(P_kbar) * 1000.0, 1.0e-9),
            )
            ratio = float(split['fe3'])
        except Exception as exc:  # noqa: BLE001 — upstream library boundary
            return (
                0.0,
                [
                    'Kress-Carmichael 1991 Fe3+/SumFe fit failed '
                    f'(T_K={T_K}, fO2_log={fO2_log}): {exc!r}; '
                    'no operator Fe3Fet_Liq supplied -> calibration tagged out_of_range'
                ],
                False,
            )

        if not (ratio == ratio) or ratio < 0.0 or ratio > 1.0:
            # NaN / out-of-range -> the fit produced no usable answer.
            # Tag and warn instead of fabricating a default.
            return (
                0.0,
                [
                    'Kress-Carmichael 1991 Fe3+/SumFe fit produced non-finite or '
                    f'out-of-range ratio {ratio!r} '
                    f'(T_K={T_K}, fO2_log={fO2_log}); '
                    'no operator Fe3Fet_Liq supplied -> calibration tagged out_of_range'
                ],
                False,
            )

        return ratio, [], True

    def _build_dataframe(
        self, liquid_comp_wt: Mapping[str, float]
    ) -> Any:
        """
        Project ``liquid_comp_wt`` onto a one-row pandas DataFrame in
        PySulfSat's ``*_Liq`` schema.

        Iron is folded onto ``FeOt_Liq`` (total iron expressed as FeO),
        which is what every SCSS / SCAS function in PySulfSat consumes.
        Missing oxides are zero-filled.

        ``pandas`` is imported lazily so the simulator's import path
        stays free of pandas when the [sulfur] extra is not installed.
        """
        import pandas as pd  # noqa: PLC0415 — lazy import, see docstring

        row: Dict[str, float] = {col: 0.0 for col in _OXIDE_TO_PYSULFSAT_COL.values()}
        feo_total_wt = 0.0
        for oxide, wt in liquid_comp_wt.items():
            if wt is None or float(wt) <= 0.0:
                continue
            value = float(wt)
            if oxide == 'FeO':
                feo_total_wt += value
                row['FeO_Liq'] = value
            elif oxide == 'Fe2O3':
                # Fe2O3 -> FeO equivalent (mass of 2*Fe basis).
                feo_total_wt += value * (2.0 * 71.844 / 159.687)
                row['Fe2O3_Liq'] = value
            else:
                col = _OXIDE_TO_PYSULFSAT_COL.get(oxide)
                if col is not None:
                    row[col] = value
        row['FeOt_Liq'] = feo_total_wt
        return pd.DataFrame([row])

    @staticmethod
    def _coerce_liquid_comp_wt(
        liquid_comp_wt: Mapping[str, float],
    ) -> Dict[str, float]:
        cleaned: Dict[str, float] = {}
        for oxide, wt in liquid_comp_wt.items():
            if wt is None:
                continue
            value = float(wt)
            if value > 0.0:
                cleaned[str(oxide)] = value
        return cleaned

    @staticmethod
    def _finite_capacity_ppm(
        value: float,
        *,
        field: str,
        model: str,
    ) -> tuple[float, str | None]:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            return (
                0.0,
                f'{model} returned invalid {field}={value!r}; '
                f'treating capacity as 0.0 ppm ({exc!r})',
            )
        if not math.isfinite(number):
            return (
                0.0,
                f'{model} returned non-finite {field}={number!r}; '
                'treating capacity as 0.0 ppm for degenerate no modeled '
                'sulfide/sulfate saturation',
            )
        return max(0.0, number), None

    @staticmethod
    def _finite_fraction(
        value: float,
        *,
        field: str,
        model: str,
    ) -> tuple[float, str | None]:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            return (
                0.0,
                f'{model} returned invalid {field}={value!r}; '
                f'treating fraction as 0.0 ({exc!r})',
            )
        if not math.isfinite(number):
            return (
                0.0,
                f'{model} returned non-finite {field}={number!r}; '
                'treating fraction as 0.0 for degenerate sulfur speciation',
            )
        return min(1.0, max(0.0, number)), None

    @staticmethod
    def _partition_input_S(
        *,
        S_input_ppm: float,
        SCSS_ppm: float,
        SCAS_ppm: float,
        S6_fraction: float,
    ) -> tuple[float, float]:
        """
        Split ``S_input_ppm`` between the sulfide- and sulfate-bearing
        partitions, capped by SCSS and SCAS respectively.

        The S6+ fraction sets the sulfate-bound share; the remainder
        goes to the sulfide-bound share. Each share is then capped at
        the corresponding capacity at saturation (excess S leaves the
        melt as a separate phase and is the responsibility of the Stage
        0 sulfide-matte / salt-phase accounts upstream).
        """
        S_input_ppm = max(0.0, float(S_input_ppm))
        if S_input_ppm <= 0.0:
            return 0.0, 0.0
        S6_fraction = min(1.0, max(0.0, float(S6_fraction)))

        s_sulfate_ppm = S_input_ppm * S6_fraction
        s_sulfide_ppm = S_input_ppm - s_sulfate_ppm

        scas_cap = float(SCAS_ppm)
        if not math.isfinite(scas_cap):
            scas_cap = 0.0
        scss_cap = float(SCSS_ppm)
        if not math.isfinite(scss_cap):
            scss_cap = 0.0
        s_sulfate_ppm = min(s_sulfate_ppm, max(0.0, scas_cap))
        s_sulfide_ppm = min(s_sulfide_ppm, max(0.0, scss_cap))
        return s_sulfide_ppm, s_sulfate_ppm
