from simulator.reference_data import (
    kelley_1960_usbm_b584_loader,
    pankratz_1984_usbm_b677_loader,
)
from simulator.reference_data.published_curve_gates import three_cell_gate_passes


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
