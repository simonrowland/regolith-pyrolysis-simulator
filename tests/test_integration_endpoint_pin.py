"""Pre-d-230 numeric endpoint pin; changed separately from its capture."""
from tests.test_c2a_deadline import _manager
from simulator.state import CampaignPhase, MeltState


def test_integrated_c2a_endpoint_pin():
    manager = _manager()
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A).hex() == float(10_000).hex()
