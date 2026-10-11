from simulator.reference_data import (
    kelley_1960_usbm_b584_loader,
    pankratz_1984_usbm_b677_loader,
)
from simulator.reference_data.published_curve_gates import three_cell_gate_passes
from simulator.reference_data import kelley_1960_usbm_b584_loader as b584
from simulator.reference_data import pankratz_1984_usbm_b677_loader as b677


def test_three_cell_predicate_has_one_owner_called_by_both_bulletins(monkeypatch):
    assert three_cell_gate_passes([{"residual": 1, "tolerance": 1}] * 3)
    assert not three_cell_gate_passes([{"residual": 1, "tolerance": 0}] * 3)
    b584_calls = []
    b677_calls = []

    def observe(rows, calls):
        calls.append(len(rows))
        return three_cell_gate_passes(rows)

    monkeypatch.setattr(kelley_1960_usbm_b584_loader, "three_cell_gate_passes",
                        lambda rows: observe(rows, b584_calls))
    monkeypatch.setattr(pankratz_1984_usbm_b677_loader, "three_cell_gate_passes",
                        lambda rows: observe(rows, b677_calls))
    kelley_1960_usbm_b584_loader.load_published_curves()
    pankratz_1984_usbm_b677_loader.load_published_curves()
    assert b584_calls
    assert len(b677_calls) == 4


def test_b584_gate_reports_temperature_and_duplicate_phase_binding_failures():
    records = {record["record_id"]: record for record in b584.load_records(include_ocr_suspect=True)}
    curve = next(item for item in b584.load_published_curves()
                 if item["formula"] == "Fe0.947O" and item["phase"] == "l"
                 and item["quantity"] == "heat_content_increment")
    wrong_temperature = {**curve, "gate_cells": [{"temperature_K": 1600, "source_row_index": 9}]}
    wrong_phase = {**curve, "gate_cells": [{"temperature_K": 1650, "source_row_index": 7}]}
    assert b584._gate_heat_content(wrong_temperature, records["table-397"])["gate"]["reason"] == "gate_row_temperature_mismatch"
    assert b584._gate_heat_content(wrong_phase, records["table-397"])["gate"]["reason"] == "gate_row_phase_mismatch"


def test_b677_gate_reports_duplicate_phase_binding_failure(monkeypatch):
    import json

    record = next(b677.load_records())
    curves = json.loads((b677.COMPILATION_ROOT / "source/published-curves.json").read_text())
    liquid = next(item for item in curves if item["phase"] == "l" and item["quantity"] == "heat_capacity")
    liquid["source_cell_rows"][0] = 4
    monkeypatch.setattr(b677, "load_records", lambda root=b677.COMPILATION_ROOT: iter([record]))
    monkeypatch.setattr(b677.json, "loads", lambda value: curves)
    result = next(item for item in b677.load_published_curves() if item["phase"] == "l")
    assert result["gate"].get("reason") == "gate_row_phase_mismatch", result["gate"]


def test_enthalpy_gates_report_all_in_range_table_cells():
    b584_curves = b584.load_published_curves()
    b584_heat = [curve for curve in b584_curves if curve["quantity"] == "heat_content_increment"]
    assert b584_heat and all("full_range_check" in curve for curve in b584_heat)
    glass = next(curve for curve in b584_heat if curve["formula"] == "SiO2" and curve["phase"] == "gl")
    assert glass["full_range_check"]["n_cells"] > 3
    assert glass["full_range_check"]["n_outside_tolerance"] > 0
    b677_heat = [curve for curve in b677.load_published_curves()
                 if curve["quantity"] == "heat_content_increment"]
    assert b677_heat and all(curve["full_range_check"]["n_cells"] >= 3 for curve in b677_heat)
