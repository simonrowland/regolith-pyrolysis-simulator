"""Duct sizing pins captured before d-231 changes its endpoint semantics."""
from simulator.config import load_config_bundle
from simulator.equipment import EquipmentDesigner


def test_integrated_duct_sizing_pin():
    pipe = EquipmentDesigner(load_config_bundle().setpoints).size_collection_pipe(0.01)
    assert pipe.diameter_m.hex() == '0x1.eb851eb851eb8p-4'
    assert pipe.conductance_kg_s.hex() == '0x1.c819ee7f2bf3dp-4'
