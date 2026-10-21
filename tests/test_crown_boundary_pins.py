"""Chunk-0 outputs captured from green a4608882d; parity, not physics evidence."""
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.accounting.formulas import parse_formula
from simulator.accounting.ledger import AtomLedger
from simulator.campaigns import CampaignManager
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    WallSaturationPressureRefusal,
    _antoine_psat_pa,
    _hkl_surface_deposition_flux_mol_m2_s,
)
from simulator.state import BatchRecord, CampaignPhase
from tests.chemistry.test_evaporation_series_resistance_flux import _evap

DATA = Path(__file__).resolve().parents[1] / "data"


def test_lunar_source_pressure_and_status_pin():
    feed = yaml.safe_load((DATA / "feedstocks.yaml").read_text())
    composition = feed["lunar_mare_low_ti"]["composition_wt_pct"]
    # Declared one-kg lunar composition; kg divided by kg/mol gives mol.
    amounts = {s: v / 100 / parse_formula(s).molar_mass_kg_per_mol()
               for s, v in composition.items()}
    provider = BuiltinVaporPressureProvider(
        yaml.safe_load((DATA / "vapor_pressures.yaml").read_text()))
    result = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": amounts}, species_formula_registry={}),
        temperature_C=1726.85, pressure_bar=1e-9, fO2_log=-9,
        control_inputs={"pO2_bar": 1e-9}))
    assert result.status == "ok"
    for species, expected in (("Mg", "0x1.4aa43ef7b20adp-3"),
                              ("Ca", "0x1.d853068b46be8p-17")):
        assert result.diagnostic["vapor_pressures_Pa"][species].hex() == expected
        assert result.diagnostic["vapor_pressures_source"][species] == (
            "builtin_authoritative:gas_standard_fugacity:openimcc")
        assert result.diagnostic["species_authority"][species] == {
            "authority_class": "uncertified"}


@pytest.mark.parametrize("kn", [10.0, 100.0])
def test_free_molecular_hkl_pin(kn):
    result = _evap(knudsen_number=kn)
    assert result.flux_kg_s_m2.hex() == "0x1.9c05954322b74p-8"
    assert result.r_gas.hex() == result.r_melt.hex() == "0x0.0p+0"


@pytest.mark.parametrize(("pressure", "expected"), [
    (0.0, "0x0.0p+0"), (100.0, "0x0.0p+0"),
    (2000.0, "0x1.a7b202d52dbf0p+3")])
def test_positive_only_wall_deposition_pin(pressure, expected):
    assert _hkl_surface_deposition_flux_mol_m2_s(
        "Mg", pressure, 1000.0, 1.0).hex() == expected


@pytest.mark.parametrize(("species", "temperature", "expected"), [
    ("Mg", 500.0, "0x1.8f7e06aa69f40p-17"),
    ("Mg", 600.0, "0x1.59c21edf6fa22p-7"),
    ("Ca", 500.0, "0x1.0f0813d0ddc16p-26")])
def test_cold_surface_pressure_pin(species, temperature, expected):
    notices = {}
    assert _antoine_psat_pa(
        species, temperature, antoine_extrapolations=notices,
        enforce_hot_train_applicability=False).hex() == expected
    assert notices[species]["status"] == "extrapolated"


def test_ca_600k_refusal_pin():
    with pytest.raises(WallSaturationPressureRefusal) as caught:
        _antoine_psat_pa("Ca", 600.0, enforce_hot_train_applicability=False)
    assert str(caught.value) == (
        "wall_saturation_pressure_refused: species=Ca temperature_K=600.000 "
        "reason=no extrapolation available: Antoine pressure is not representable "
        "as finite positive; valid_range_K=[1254, 1712]")


def test_campaign_successor_pins():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", branch="two")
    assert manager.get_next_campaign(CampaignPhase.C4, record) is CampaignPhase.C2A
    assert manager.get_next_campaign(CampaignPhase.C2A, record) is None
    record.feedstock_key = "debug_pure_feo"
    assert manager.get_next_campaign(CampaignPhase.C2A, record) is CampaignPhase.C6
    assert manager.get_next_campaign(CampaignPhase.C6, record) is CampaignPhase.COMPLETE
    manager.c5_enabled = True
    assert manager.get_next_campaign(CampaignPhase.C4, record) is CampaignPhase.C5
    assert manager.get_next_campaign(CampaignPhase.C5, record) is CampaignPhase.C6
    manager = CampaignManager({"campaigns": {"C7": {"enabled": True}}})
    assert manager.get_next_campaign(CampaignPhase.C6, record) is CampaignPhase.C7_CA_ALUMINOTHERMIC
    assert manager.get_next_campaign(
        CampaignPhase.C7_CA_ALUMINOTHERMIC, record) is CampaignPhase.COMPLETE


def test_vented_oxygen_accounting_pin():
    ledger = AtomLedger()
    stored = "terminal.oxygen_melt_offgas_stored"
    vented = "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ledger.load_external(stored, {"O2": 2.0}, source="pin", material_origin="feedstock")
    ledger.move("vent", stored, vented, {"O2": 1.0}, reason="controlled vent")
    assert ledger.kg_by_account(stored)["O2"].hex() == "0x1.0000000000000p+0"
    assert ledger.kg_by_account(vented)["O2"].hex() == "0x1.0000000000000p+0"
    assert ledger.kg_by_account("terminal.oxygen_mre_anode_stored") == {}
    ledger.assert_balanced()
