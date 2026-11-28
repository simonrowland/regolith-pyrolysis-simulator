#!/usr/bin/env python3
"""Probe one scored CaO activity row through the production battery path."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

from engines.alphamelts.domain import (
    MELTS_CASIO3_LIQUID_G0_FIELD,
    MELTS_CAO_EXCHANGE_MU_FIELD,
    MELTS_CAO_EXCHANGE_UNAVAILABLE,
)
from simulator.battery.enums import Engine, NoticeKind, Phase, Quantity
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import migrate
from simulator.battery.records import StandardState
from simulator.battery.score import predict_with_engine, temperature_of
from simulator.diagnostic_helpers import binary_pot_battery
from simulator.diagnostic_helpers.binary_pot_battery import (
    close_battery_engine_handles,
)
from simulator.melt_backend.alphamelts_contract import isothermal_run_kwargs


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = 'stolyarova-1996-cao-alumina-silica-kems'
CAO_FUSION_REASON = 'CaSiO3(l)-pWo fusion residual'


def _ca_bearing_cao_observation(migration):
    rows = []
    for observation in migration.observations.values():
        identity = observation.identity
        if (
            observation.source_id != SOURCE_ID
            or quantity_token(identity) is not Quantity.ACTIVITY
            or identity.species is None
            or identity.species.formula != 'CaO'
            or temperature_of(identity) != Decimal('1933')
            or identity.composition is None
            or not identity.composition.is_value
            or identity.reference_state is None
            or not identity.reference_state.is_value
        ):
            continue
        standard = identity.reference_state.value
        if not isinstance(standard, StandardState):
            continue
        endmember_phase = standard.endmember.phase
        composition = identity.composition.value
        if (
            not endmember_phase.is_value
            or endmember_phase.value is not Phase.CR
            or not any(
                name == 'CaO' and amount > 0
                for name, amount in composition.components
            )
        ):
            continue
        rows.append(observation)
    if not rows:
        raise RuntimeError(
            'no migrated Ca-bearing CaO activity row found for probe'
        )
    return sorted(rows, key=lambda row: row.observation_id)[0]


def _cell_values(cell) -> dict[str, float | None]:
    if cell is None:
        return {
            'mu_CaO_exchange_J_per_mol': None,
            'mu_CaSiO3_J_per_mol': None,
            'mu0_CaSiO3_J_per_mol': None,
        }
    return {
        'mu_CaO_exchange_J_per_mol': getattr(
            cell, MELTS_CAO_EXCHANGE_MU_FIELD, None
        ),
        'mu_CaSiO3_J_per_mol': getattr(
            cell, 'mu_CaSiO3_J_per_mol', None
        ),
        'mu0_CaSiO3_J_per_mol': getattr(
            cell, MELTS_CASIO3_LIQUID_G0_FIELD, None
        ),
    }


def main() -> None:
    migration = migrate(ROOT, write=False)
    observation = _ca_bearing_cao_observation(migration)
    experiment = migration.experiments[observation.experiment_id]
    bench = (
        None
        if experiment.bench_id is None
        else migration.benches.get(experiment.bench_id)
    )
    handles = {}
    captured_cells = []
    original_equilibrate_cell = binary_pot_battery.equilibrate_cell

    def capture_equilibrate_cell(*args, **kwargs):
        cell = original_equilibrate_cell(*args, **kwargs)
        captured_cells.append(cell)
        return cell

    binary_pot_battery.equilibrate_cell = capture_equilibrate_cell
    active_error = None
    prediction = None
    route = None
    run_mode = None
    try:
        prediction = predict_with_engine(
            Engine.ALPHAMELTS,
            observation,
            handles=handles,
            experiment=experiment,
            bench=bench,
        )
        handle = handles.get(Engine.ALPHAMELTS.value)
        backend = None if handle is None else getattr(handle, 'backend', None)
        route = None if backend is None else getattr(backend, '_mode', None)
        parameters = (
            {}
            if backend is None
            else inspect.signature(backend.equilibrate).parameters
        )
        run_mode = isothermal_run_kwargs(
            Engine.ALPHAMELTS.value, parameters
        ).get('subprocess_run_mode')
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        binary_pot_battery.equilibrate_cell = original_equilibrate_cell
        close_battery_engine_handles(handles, active_error=active_error)

    cell = captured_cells[-1] if captured_cells else None
    assert prediction is not None

    fusion_notice = next(
        (
            notice for notice in prediction.notices
            if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
            and CAO_FUSION_REASON in notice.reason
        ),
        None,
    )
    fusion_band = None if fusion_notice is None else fusion_notice.band
    fusion_refusal = (
        None
        if fusion_notice is None or fusion_band is not None
        else fusion_notice.reason
    )
    exchange_notice = next(
        (
            notice for notice in prediction.notices
            if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
            and MELTS_CAO_EXCHANGE_UNAVAILABLE in notice.reason
        ),
        None,
    )

    activity_refusal = None
    if prediction.value is None:
        activity_refusal = {
            'reason': (
                None if prediction.refusal_reason is None
                else prediction.refusal_reason.value
            ),
            'detail': dict(prediction.refusal_detail),
        }

    print(json.dumps({
        'engine': Engine.ALPHAMELTS.value,
        'observation_id': observation.observation_id,
        'route': route or 'unavailable',
        'run_mode': run_mode,
        'a_CaO': None if prediction.value is None else str(prediction.value),
        'a_CaO_refusal': activity_refusal,
        'cao_exchange_unavailable_reason': (
            None if exchange_notice is None else exchange_notice.reason
        ),
        'fusion_band': fusion_band,
        'fusion_band_refusal': fusion_refusal,
        'liquid_mu_mu0_fields_found': _cell_values(cell),
    }, sort_keys=True, allow_nan=False, default=str))


if __name__ == '__main__':
    main()
