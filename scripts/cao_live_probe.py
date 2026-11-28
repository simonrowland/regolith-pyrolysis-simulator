#!/usr/bin/env python3
"""Probe CaO exchange and CaSiO3 fusion disclosure through alphaMELTS."""

from __future__ import annotations

import json
import math

from engines.alphamelts.domain import (
    MELTS_CASIO3_FUSION_BAND_UNAVAILABLE,
    MELTS_CASIO3_LIQUID_G0_FIELD,
    MELTS_CAO_EXCHANGE_MU_FIELD,
    MELTS_CAO_EXCHANGE_UNAVAILABLE,
    MELTS_PWO_G0_FIELD,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT
from simulator.melt_backend.pure_phase_janaf_score import (
    casio3_fusion_model_systematic_dex,
    cao_activity_from_melts_exchange_mu,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


def main() -> None:
    temperature_C = 1700.0
    temperature_K = temperature_C + CELSIUS_TO_KELVIN_OFFSET
    backend = AlphaMELTSBackend()
    try:
        if not backend.initialize({'mode': 'python_api'}):
            print(json.dumps({
                'engine': 'alphamelts',
                'transport': 'python_api',
                'status': 'unavailable',
                'initialization_reason': backend.unavailable_reason(),
                'liquid_mu_CaSiO3_J_per_mol': None,
                'liquid_mu0_CaSiO3_J_per_mol': None,
                'fusion_band_dex': None,
                'fusion_band_refusal': MELTS_CASIO3_FUSION_BAND_UNAVAILABLE,
                'a_CaO': None,
                'a_CaO_refusal': MELTS_CAO_EXCHANGE_UNAVAILABLE,
            }, sort_keys=True))
            return

        native_liquid_potentials: dict[str, float | None] = {}
        parse_result = backend._parse_petthermotools_result

        def capture_liquid_potentials(results, **kwargs):
            selected = backend._select_petthermotools_run(results)
            mu, mu0 = backend._selected_liquid_potential_mappings(selected)
            native_liquid_potentials.update({
                'mu_CaSiO3': mu.get('CaSiO3'),
                'mu0_CaSiO3': mu0.get('CaSiO3'),
            })
            return parse_result(results, **kwargs)

        backend._parse_petthermotools_result = capture_liquid_potentials
        result = backend.equilibrate(
            temperature_C=temperature_C,
            composition_kg={'CaO': 48.0, 'SiO2': 52.0},
            fO2_log=None,
            pressure_bar=1.0,
            redox_mode=REDOX_MODE_NOT_AN_INPUT,
        )
        diagnostics = result.diagnostics
        liquid_mu = native_liquid_potentials.get('mu_CaSiO3')
        liquid_mu0 = diagnostics.get(MELTS_CASIO3_LIQUID_G0_FIELD)
        g0_pwo = diagnostics.get(MELTS_PWO_G0_FIELD)
        fusion_band = None
        fusion_refusal = None
        if liquid_mu0 is None or g0_pwo is None:
            fusion_refusal = MELTS_CASIO3_FUSION_BAND_UNAVAILABLE
        else:
            try:
                fusion_band = casio3_fusion_model_systematic_dex(
                    float(liquid_mu0) - float(g0_pwo),
                    temperature_K,
                )
            except (OverflowError, TypeError, ValueError) as exc:
                fusion_refusal = (
                    f'{MELTS_CASIO3_FUSION_BAND_UNAVAILABLE}: '
                    f'{type(exc).__name__}: {exc}'
                )

        exchange_mu = diagnostics.get(MELTS_CAO_EXCHANGE_MU_FIELD)
        cao_activity = None
        cao_refusal = None
        if exchange_mu is None:
            cao_refusal = MELTS_CAO_EXCHANGE_UNAVAILABLE
        else:
            try:
                cao_activity, _extrapolated = cao_activity_from_melts_exchange_mu(
                    float(exchange_mu),
                    temperature_K,
                    phase='cr',
                )
                if cao_activity is None or not math.isfinite(cao_activity):
                    cao_activity = None
                    cao_refusal = MELTS_CAO_EXCHANGE_UNAVAILABLE
            except (OverflowError, TypeError, ValueError) as exc:
                cao_refusal = f'{MELTS_CAO_EXCHANGE_UNAVAILABLE}: {exc}'

        print(json.dumps({
            'engine': 'alphamelts',
            'transport': backend._mode,
            'status': result.status,
            'temperature_K': temperature_K,
            'liquid_mu_CaSiO3_J_per_mol': liquid_mu,
            'liquid_mu0_CaSiO3_J_per_mol': native_liquid_potentials.get(
                'mu0_CaSiO3'
            ),
            'fusion_band_dex': fusion_band,
            'fusion_band_refusal': fusion_refusal,
            'a_CaO': cao_activity,
            'a_CaO_refusal': cao_refusal,
        }, sort_keys=True, allow_nan=False))
    finally:
        backend.close()


if __name__ == '__main__':
    main()
