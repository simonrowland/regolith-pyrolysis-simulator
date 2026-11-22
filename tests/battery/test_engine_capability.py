"""Golden pin of the predict-time capability ladder and the thermo table.

The expected sets are the membership at the pin, not a second copy of the
ladder. A later move of the thermo table has to keep both answers.
"""

from __future__ import annotations

from simulator.battery.compilation_tier import _engine_thermo_quantities
from simulator.battery.enums import Engine, Quantity
from simulator.battery.score import _engine_lacks_quantity_capability

# Thermochemistry slice each engine declared before the table moved.
_THERMO_PIN: dict[Engine, frozenset[Quantity]] = {
    Engine.INTERNAL_ANALYTICAL: frozenset(
        {
            Quantity.DELTA_FG,
            Quantity.LOG10_KF,
            Quantity.ENTHALPY_OF_FUSION,
            Quantity.ENTROPY_OF_FUSION,
        }
    ),
    Engine.VAPOROCK: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.THERMOENGINE: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.MAGEMIN: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.NASA_CEA_9: frozenset(
        {
            Quantity.CP,
            Quantity.S,
            Quantity.H_MINUS_H298,
            Quantity.DELTA_FH,
            Quantity.DELTA_FG,
        }
    ),
}

# Quantities the predict-time ladder treats as callable on every engine,
# including engines that later refuse inside predict with no numeric value.
_LACKS_FALSE_EVERY_ENGINE = frozenset(
    {
        Quantity.MASS_LOSS_FRACTION,
        Quantity.FE3_FE2_RATIO,
        Quantity.VALENCE_RATIO,
        Quantity.WALL_DEPOSIT_AREAL_RATE,
        Quantity.TRANSITION_TEMPERATURE,
        Quantity.SULFIDE_CAPACITY,
        Quantity.CHLORIDE_CAPACITY,
        Quantity.DISTRIBUTION_RATIO,
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.ENTHALPY_OF_FUSION,
        Quantity.ENTROPY_OF_FUSION,
    }
)


def test_engine_thermo_quantities_match_the_pin() -> None:
    for engine in Engine:
        assert _engine_thermo_quantities(engine) == _THERMO_PIN.get(
            engine, frozenset()
        )


def test_predict_time_capability_ladder_matches_the_pin() -> None:
    """Phase basis changes only equilibrium phase composition.

    ``wt_percent`` is a closed phase basis. Free text is not. MAGEMIN vapour
    and fusion on every engine stay False here: this is the predict-time
    ladder, which still enters predict and returns a non-numeric refusal.
    """

    for engine in Engine:
        thermo = _THERMO_PIN.get(engine, frozenset())
        for quantity in Quantity:
            for basis, phase_composition_lacks in (
                (None, True),
                ("wt_percent", False),
                ("free text basis", True),
            ):
                got = _engine_lacks_quantity_capability(engine, quantity, basis)
                if quantity is Quantity.LOG10_K_REACTION:
                    expected = True
                elif quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
                    expected = phase_composition_lacks
                elif quantity in _LACKS_FALSE_EVERY_ENGINE:
                    expected = False
                else:
                    expected = quantity not in thermo
                assert got is expected, (engine, quantity, basis, got, expected)
