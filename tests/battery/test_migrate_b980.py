"""b-980: the legacy standard-state override must not replace a primary opinion."""

from decimal import Decimal

from simulator.battery import migrate as migrate_mod
from simulator.battery.enums import Phase, ReferenceStateConvention
from tests.battery.test_migrate import _write_min_tree

_CAO_NOT_STATED = (
    "Raoultian pure solid CaO; reference state and phase are not stated."
)
_CAO_PRINTED_BAR = "Raoultian pure solid CaO at 1 bar"
_FE_PRINTED_LIQUID = "Raoultian pure liquid Fe"


def _migrate_activity(tmp_path, standard_state: str, species: str):
    root = _write_min_tree(
        tmp_path,
        extract={
            "schema_version": "literature_extract.v1",
            "source_id": "fixture-source",
            "source": {"citation": "Fixture, A. (2026), Test Journal 1:1"},
            "experiments": [{"experiment_id": "activity-run"}],
            "species": {
                species: {
                    "observations": [
                        {
                            "observation_id": "row",
                            "experiment": "activity-run",
                            "type": "activity",
                            "phase": "silicate_melt",
                            "standard_state": standard_state,
                            "T_range_K": [1873.0, 1873.0],
                            "values": {
                                "quantity": "activity",
                                "activity": 0.2,
                                "T_K": 1873.0,
                            },
                        }
                    ]
                }
            },
        },
    )
    result = migrate_mod.migrate(root, write=False)
    return result.observations["fixture-source::row"]


def test_b980_unprinted_cao_unknown_is_not_overridden(tmp_path):
    """A primary UNKNOWN stays unknown. The override must not replace it."""

    primary = migrate_mod.reference_state_from_extract(
        _CAO_NOT_STATED, species_formula="CaO", values={}
    )
    assert primary is not None and primary.is_unknown
    assert (
        migrate_mod._standard_state_from_extract_text(
            _CAO_NOT_STATED, "CaO", primary=primary
        )
        is None
    )
    stored = _migrate_activity(tmp_path, _CAO_NOT_STATED, "CaO")
    reference = stored.identity.reference_state
    assert reference is not None and reference.is_unknown
    assert reference.reason == primary.reason


def test_b980_printed_one_bar_keeps_its_pressure(tmp_path):
    """A bar number the source prints is kept. An absent number is not invented."""

    parsed = migrate_mod._standard_state_from_extract_text(
        _CAO_PRINTED_BAR, "CaO", primary=None
    )
    assert parsed is not None
    assert parsed.reference_pressure_bar == Decimal("1")
    assert parsed.endmember.formula == "CaO"
    assert parsed.endmember.phase.value is Phase.CR

    stored = _migrate_activity(tmp_path, _CAO_PRINTED_BAR, "CaO")
    reference = stored.identity.reference_state
    assert reference is not None and reference.is_value
    assert reference.value.reference_pressure_bar == Decimal("1")
    assert reference.value.endmember.formula == "CaO"
    assert reference.value.endmember.phase.value is Phase.CR


def test_b980_no_opinion_still_applies_printed_override():
    """Primary None still takes printed endmember and phase, and no default pressure."""

    parsed = migrate_mod._standard_state_from_extract_text(
        _FE_PRINTED_LIQUID, "Fe", primary=None
    )
    assert parsed is not None
    assert parsed.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert parsed.endmember.formula == "Fe"
    assert parsed.endmember.phase.value is Phase.L
    assert parsed.component_basis == "Fe"
    assert parsed.reference_pressure_bar is None
