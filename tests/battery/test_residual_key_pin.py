"""Pin the residual-key spellings before the field split moves.

Expected strings are the characters ``residual_key`` writes and
``_residual_key_quantity`` reads back. They are not built by a second
copy of the formatter.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.enums import Engine, Quantity, Rail
from simulator.battery.score import _residual_key_quantity, residual_key


def test_residual_key_writes_quantity_rail_and_identity_temperature() -> None:
    plain = residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=Engine.OPENIMCC,
        rail=Rail.MELT_ACTIVITY,
    )
    at_temperature = residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=Engine.OPENIMCC,
        rail=Rail.MELT_ACTIVITY,
        temperature_K=Decimal("1000"),
    )
    trimmed = residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=Engine.OPENIMCC,
        rail=Rail.MELT_ACTIVITY,
        temperature_K=Decimal("1000.50"),
    )
    whole = residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=Engine.OPENIMCC,
        rail=Rail.MELT_ACTIVITY,
        temperature_K=Decimal("1000.00"),
    )

    assert plain == "obs-1::activity::melt_activity::openimcc"
    assert at_temperature == "obs-1:T=1000::activity::melt_activity::openimcc"
    assert trimmed == "obs-1:T=1000.5::activity::melt_activity::openimcc"
    assert whole == "obs-1:T=1000::activity::melt_activity::openimcc"


def test_residual_key_missing_quantity_and_rail_use_the_fixed_tokens() -> None:
    key = residual_key(
        reference_id="obs-2",
        quantity=None,
        engine=Engine.MAGEMIN,
        rail=None,
        temperature_K=Decimal("1100"),
    )

    assert key == "obs-2:T=1100::quantity_unknown::none::magemin"


def test_residual_key_quantity_reads_the_quantity_field() -> None:
    assert (
        _residual_key_quantity("obs-1:T=1000::activity::melt_activity::openimcc")
        == "activity"
    )
    assert (
        _residual_key_quantity("obs-2:T=1100::quantity_unknown::none::magemin")
        == "quantity_unknown"
    )
    # A reference head may itself contain ``::``. The quantity is the third
    # field from the right.
    assert (
        _residual_key_quantity("obs::child:T=1000::p_sat::vapour::magemin")
        == "p_sat"
    )
    assert _residual_key_quantity("a::::melt_activity::openimcc") == "quantity_unknown"
    assert _residual_key_quantity("a::b::c") == "quantity_unknown"
    assert _residual_key_quantity("only-a-head") == "quantity_unknown"
    assert _residual_key_quantity("") == "quantity_unknown"
    assert _residual_key_quantity(None) == "quantity_unknown"
