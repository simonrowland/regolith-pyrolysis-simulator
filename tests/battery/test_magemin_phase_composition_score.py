"""One MAGEMin phase composition, through the scorer, on a known bulk.

Borisov & Veksler included rows have no engine bulk, so they cannot be
this call. The bulk is the golden-probe pot FeO-SiO2 40/60 wt% at 1700 K,
1 bar, and 10**-8 bar fO2. Mole fractions are weight over molar mass.
``composition_wt_pct`` must recover the pot before the binary runs. The
engine's SiO2 is not pinned: a weight fraction would be below 1, and a
wt% result of this liquid is above 1 and below 100.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.accounting.oxide_mass import oxide_molar_mass_g_mol
from simulator.battery.composition import composition_wt_pct
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    ExecutionState,
    Phase,
    PhaseRole,
    Quantity,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import predict_with_engine
from simulator.physical_constants import PA_PER_BAR
from tests.battery import factories as F
from tests.test_pure_phase_access import needs_magemin_binary


def _feo_sio2_40_60() -> Composition:
    weights = (("FeO", Decimal("40")), ("SiO2", Decimal("60")))
    moles = {
        name: weight / Decimal(str(oxide_molar_mass_g_mol(name)))
        for name, weight in weights
    }
    total = sum(moles.values(), Decimal(0))
    return Composition(
        basis="ordered_complete_mole_inventory",
        components=tuple((name, moles[name] / total) for name, _weight in weights),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


@needs_magemin_binary
def test_magemin_scores_sio2_from_the_feo_sio2_pot() -> None:
    composition = _feo_sio2_40_60()
    assert composition.input_provenance is None
    recovered = composition_wt_pct(composition)
    assert recovered is not None
    assert abs(recovered["FeO"] - 40.0) < 1e-4
    assert abs(recovered["SiO2"] - 60.0) < 1e-4

    bar = Decimal(str(PA_PER_BAR))
    identity = Identity(
        quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        species=Species("SiO2", Phase.L),
        temperature_K=State.of(Decimal("1700")),
        phase_role=State.of(PhaseRole.MELT),
        subtype=State.of("wt_percent"),
        composition=State.of(composition),
        fO2_Pa=State.of(Decimal("1e-8") * bar),
        total_pressure_Pa=State.of(bar),
    )
    observation = F.observation(
        "feo-sio2-40-60-sio2",
        "feo-sio2-40-60",
        identity,
        Decimal("60"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    prediction = predict_with_engine(Engine.MAGEMIN, observation)
    assert prediction.execution.state is ExecutionState.PRODUCED, (
        prediction.refusal_reason,
        dict(prediction.refusal_detail),
    )
    assert prediction.execution.call_evidence == "phase_assemblage"
    assert prediction.value is not None
    assert Decimal("1") < prediction.value < Decimal("100")
