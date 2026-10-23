"""Source-basis pins for VapoRock gas rows and internal formation rows.

The publication has to print the basis. A sample row from each family is
scored as a series cell. Rows whose publication does not print a basis stay
on the missing-input refusal.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.compilation_tier import (
    _iter_compilation_series_cells,
    predict_thermo_attempt,
)
from simulator.battery.enums import Engine, Quantity, RefusalReason
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import migrate
from simulator.battery.records import State

from tests.battery.test_migrate import (
    _copy_compilation_record,
    _migrate_real_extract,
    _write_min_tree,
)

B677 = "pankratz-1984-usbm-b677"
B592 = "kelley-king-1961-usbm-b592"
B689 = "pankratz-1987-usbm-b689"
B696 = "pankratz-1994-usbm-b696"
B2131 = "robie-hemingway-1995-usgs-b2131"

_MISSING_GAS = "vaporock-gas-thermo-requires-mol-species-basis"
_MISSING_PER = "per-basis-unsupported"


def _migrate_record(tmp_path: Path, source_id: str, filename: str):
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, source_id, filename)
    return migrate(root, write=False)


def _observation(result, source_id: str, record_id: str, quantity: Quantity, *, contains: str | None = None):
    prefix = f"{source_id}:{record_id}"
    found = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith(prefix)
        and quantity_token(obs.identity) is quantity
        and (contains is None or contains in obs.observation_id)
    ]
    assert len(found) == 1, [obs.observation_id for obs in found]
    return found[0]


def _cell_at(observation, temperature: str):
    target = Decimal(temperature)
    found = [
        cell
        for cell in _iter_compilation_series_cells(observation)
        if isinstance(cell.identity.temperature_K, State)
        and cell.identity.temperature_K.is_value
        and cell.identity.temperature_K.value == target
    ]
    assert found, [
        (
            cell.observation_id,
            getattr(cell.identity.temperature_K, "value", None),
        )
        for cell in _iter_compilation_series_cells(observation)
    ]
    return found[0]


def _per_is_unknown(observation) -> bool:
    per = observation.identity.per
    return per is None or per.is_unknown


def _pressure_is_unknown(observation) -> bool:
    pressure = observation.identity.standard_pressure_Pa
    return pressure is None or pressure.is_unknown


def _attempt(engine: Engine, cell):
    return predict_thermo_attempt(engine, cell)


def test_b677_potassium_gas_refuses_missing_mol_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B677, "table-0096.json")
    heat_capacity = _observation(result, B677, "table-0096", Quantity.CP)
    assert _per_is_unknown(heat_capacity)
    assert _pressure_is_unknown(heat_capacity)
    cell = _cell_at(heat_capacity, "1600")
    attempt = _attempt(Engine.VAPOROCK, cell)
    assert attempt.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert attempt.refusal_detail["reason"] == _MISSING_GAS
    enthalpy = _observation(result, B677, "table-0096", Quantity.H_MINUS_H298)
    anchor = enthalpy.identity.subtype
    assert anchor is None or not anchor.is_value


def test_b677_mgo_formation_refuses_missing_per_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B677, "table-1203.json")
    gibbs = _observation(result, B677, "table-1203", Quantity.DELTA_FG)
    assert _per_is_unknown(gibbs)
    assert _pressure_is_unknown(gibbs)
    cell = _cell_at(gibbs, "298")
    attempt = _attempt(Engine.INTERNAL_ANALYTICAL, cell)
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == _MISSING_PER


def test_b592_alo_refuses_missing_mol_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B592, "table-006-0018.json")
    heat_capacity = _observation(result, B592, "table-006-0018", Quantity.CP)
    assert _per_is_unknown(heat_capacity)
    cell = _cell_at(heat_capacity, "298.15")
    attempt = _attempt(Engine.VAPOROCK, cell)
    assert attempt.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert attempt.refusal_detail["reason"] == _MISSING_GAS


def test_b2131_cao_logkf_refuses_missing_per_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B2131, "high-temperature-p182.json")
    log_kf = _observation(result, B2131, "high-temperature-p182", Quantity.LOG10_KF)
    assert _per_is_unknown(log_kf)
    cell = _cell_at(log_kf, "298.15")
    attempt = _attempt(Engine.INTERNAL_ANALYTICAL, cell)
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == _MISSING_PER


def test_b689_ags_gas_stays_missing_mol_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B689, "page-0003.json")
    enthalpy = _observation(result, B689, "page-0003", Quantity.H_MINUS_H298)
    assert _per_is_unknown(enthalpy)
    cell = _cell_at(enthalpy, "298.15")
    attempt = _attempt(Engine.VAPOROCK, cell)
    assert attempt.refusal_detail["reason"] == _MISSING_GAS


def test_b696_ago_gas_stays_missing_mol_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B696, "page-0006.json")
    heat_capacity = _observation(result, B696, "page-0006", Quantity.CP)
    assert _per_is_unknown(heat_capacity)
    cell = _cell_at(heat_capacity, "1000")
    attempt = _attempt(Engine.VAPOROCK, cell)
    assert attempt.refusal_detail["reason"] == _MISSING_GAS


def test_b677_silver_stays_unmapped(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B677, "table-0008.json")
    heat_capacity = _observation(result, B677, "table-0008", Quantity.CP)
    gibbs = _observation(result, B677, "table-0008", Quantity.DELTA_FG)
    assert _per_is_unknown(heat_capacity)
    assert _per_is_unknown(gibbs)
    gas = _attempt(Engine.VAPOROCK, _cell_at(heat_capacity, "1000"))
    formation = _attempt(Engine.INTERNAL_ANALYTICAL, _cell_at(gibbs, "1000"))
    assert gas.refusal_detail["reason"] == _MISSING_GAS
    assert formation.refusal_detail["reason"] == _MISSING_PER


def test_b592_aluminum_gas_outside_interval_stays_unmapped(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B592, "table-006-0003.json")
    entropy = _observation(
        result,
        B592,
        "table-006-0003",
        Quantity.S,
        contains="entropy_recommended",
    )
    assert _per_is_unknown(entropy)
    attempt = _attempt(Engine.VAPOROCK, _cell_at(entropy, "298.15"))
    assert attempt.refusal_detail["reason"] == _MISSING_GAS


def test_b2131_silver_stays_unmapped(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B2131, "high-temperature-p067.json")
    log_kf = _observation(result, B2131, "high-temperature-p067", Quantity.LOG10_KF)
    assert _per_is_unknown(log_kf)
    attempt = _attempt(Engine.INTERNAL_ANALYTICAL, _cell_at(log_kf, "1600"))
    assert attempt.refusal_detail["reason"] == _MISSING_PER


def test_shornikov_solid_k2o_stays_missing_per_basis(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "shornikov-2013-potassium-silicate-thermodynamics.yaml",
    )
    found = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith("shornikov_2013_t3_solid_k2o")
        and quantity_token(obs.identity) is Quantity.DELTA_FG
    ]
    assert len(found) == 1
    oxide = found[0]
    assert _per_is_unknown(oxide)
    assert oxide.value.point == Decimal("-161.387")
    attempt = _attempt(Engine.INTERNAL_ANALYTICAL, oxide)
    assert attempt.refusal_detail["reason"] == _MISSING_PER


