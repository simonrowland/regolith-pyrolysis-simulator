"""Source-basis pins for VapoRock gas rows and internal formation rows.

The publication has to print the basis. A sample row from each family is
scored as a series cell. Rows whose publication does not print a basis stay
on the missing-input refusal.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from simulator.battery.compilation_tier import (
    _iter_compilation_series_cells,
    predict_thermo_attempt,
)
from simulator.battery.enums import Engine, PerBasis, Quantity, RefusalReason
from simulator.battery.identity import atm_to_pa, quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    _identity_from_plain,
    _state_from_plain,
    compilation_column_series_from_record,
    migrate,
    to_plain,
)
from simulator.battery.records import State
from simulator.state_types import StateTag

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


def _attempt(engine: Engine, cell):
    return predict_thermo_attempt(engine, cell)


def _record_doc(source_id: str, filename: str) -> dict:
    path = (
        REPO_ROOT
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "records"
        / filename
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _printed_b677_token(document: dict, temperature: str, column: int) -> str:
    for row in document["source_table_rows_raw"]:
        if isinstance(row, list) and row and str(row[0]) == temperature:
            return str(row[column])
    raise AssertionError(temperature)


def _assert_cell_matches_reader(
    observation,
    document: dict,
    quantity: Quantity,
    temperature: str,
    *,
    printed_token: str,
    column: int,
) -> None:
    """Stored magnitude is the reader's lift of the printed cell, not a recomputed factor."""

    assert _printed_b677_token(document, temperature, column) == printed_token
    series = next(
        item
        for item in compilation_column_series_from_record(document)
        if item.quantity is quantity
    )
    assert series.value.series is not None
    magnitude = next(
        value
        for point_temperature, value in series.value.series
        if point_temperature == Decimal(temperature)
    )
    assert _cell_at(observation, temperature).value.point == magnitude


def test_b677_potassium_gas_uses_printed_ideal_gas_basis(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B677, "table-0096.json")
    heat_capacity = _observation(result, B677, "table-0096", Quantity.CP)
    entropy = _observation(result, B677, "table-0096", Quantity.S)
    enthalpy = _observation(result, B677, "table-0096", Quantity.H_MINUS_H298)
    gibbs = _observation(result, B677, "table-0096", Quantity.DELTA_FG)
    assert heat_capacity.identity.per.value is PerBasis.MOL_SPECIES
    assert "pankratz-1984-usbm-b677-p001-p040.md:677" in heat_capacity.identity.per.reason
    assert "101325" in heat_capacity.identity.per.reason
    assert heat_capacity.identity.standard_pressure_Pa.value == atm_to_pa(1)
    # Identity.__post_init__ rewrites decimal axes with State.of, which drops a reason.
    assert heat_capacity.identity.standard_pressure_Pa.reason is None
    assert heat_capacity.identity.subtype.is_not_applicable
    assert entropy.identity.per.value is PerBasis.MOL_SPECIES
    assert entropy.identity.subtype.is_not_applicable
    assert enthalpy.identity.subtype.value == "H(T)-H(298.15 K)"
    assert ":149" in enthalpy.identity.subtype.reason
    assert _per_is_unknown(gibbs)
    document = _record_doc(B677, "table-0096.json")
    _assert_cell_matches_reader(
        heat_capacity, document, Quantity.CP, "1600", printed_token="4.975", column=1
    )
    _assert_cell_matches_reader(
        enthalpy, document, Quantity.H_MINUS_H298, "1600", printed_token="6.469", column=3
    )
    restored = _identity_from_plain(to_plain(heat_capacity.identity))
    assert restored.per.reason == heat_capacity.identity.per.reason
    assert restored.standard_pressure_Pa.value == atm_to_pa(1)
    admitted = _attempt(Engine.VAPOROCK, _cell_at(heat_capacity, "1600"))
    assert admitted.refusal_reason is None
    assert admitted.value is not None
    outside = _attempt(Engine.VAPOROCK, _cell_at(heat_capacity, "298"))
    assert outside.value is None
    assert outside.refusal_detail["reason"] == "vaporock-temperature-outside-janaf-row"
    assert _cell_at(heat_capacity, "298").identity.per.value is PerBasis.MOL_SPECIES


def test_b677_mgo_formation_maps_mol_species_without_a_reaction(tmp_path: Path) -> None:
    result = _migrate_record(tmp_path, B677, "table-1203.json")
    gibbs = _observation(result, B677, "table-1203", Quantity.DELTA_FG)
    heat_capacity = _observation(result, B677, "table-1203", Quantity.CP)
    assert gibbs.identity.per.value is PerBasis.MOL_SPECIES
    assert "pankratz-1984-usbm-b677-p001-p040.md:175" in gibbs.identity.per.reason
    assert "0.5" in gibbs.identity.per.reason
    assert gibbs.identity.standard_pressure_Pa.value == atm_to_pa(1)
    assert gibbs.identity.reaction.is_unknown
    assert _per_is_unknown(heat_capacity)
    document = _record_doc(B677, "table-1203.json")
    _assert_cell_matches_reader(
        gibbs,
        document,
        Quantity.DELTA_FG,
        "1200",
        printed_token="-112.322",
        column=5,
    )
    inside = _attempt(Engine.INTERNAL_ANALYTICAL, _cell_at(gibbs, "1200"))
    assert inside.value is None
    assert inside.refusal_detail["reason"] == "standard-pressure-transform-reaction-unknown"
    below = _attempt(Engine.INTERNAL_ANALYTICAL, _cell_at(gibbs, "298"))
    assert below.value is None
    assert below.refusal_detail["reason"] == "ellingham-formation-outside-segment"


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


def test_cited_value_reason_survives_plain_reload() -> None:
    cited = State(StateTag.VALUE, value=PerBasis.MOL_SPECIES, reason="quoted basis")
    restored = _state_from_plain(to_plain(cited), lambda value: PerBasis(str(value)))
    assert restored.value is PerBasis.MOL_SPECIES
    assert restored.reason == "quoted basis"
    bare = _state_from_plain(
        to_plain(State.of(PerBasis.MOL_SPECIES)),
        lambda value: PerBasis(str(value)),
    )
    assert bare.reason is None


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


