"""Starting bulk is the engine input for equilibrium phase composition.

A closed-system experiment's printed starting composition is the system
composition. The analysed glass is the measurement, not that input.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import AmountBasis, NoticeKind, Quantity
from simulator.battery.migrate import migrate, wt_pct_to_mole_fraction
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree


REPO = Path(__file__).resolve().parents[2]
_OXIDES = (
    ("SiO2", "50.5"),
    ("TiO2", "0.9"),
    ("Al2O3", "16.0"),
    ("FeOT", "9.0"),
    ("MnO", "0.2"),
    ("MgO", "9.6"),
    ("CaO", "10.2"),
    ("P2O5", "0.2"),
    ("Na2O", "2.7"),
    ("K2O", "0.7"),
)
_GLASS_AL2O3 = "18.25"


def _initial(oxides: tuple[tuple[str, str], ...] = _OXIDES, **sample_fields: object) -> dict:
    sample: dict = {
        "initial_composition": {
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [[name, amount] for name, amount in oxides],
                },
            },
            "locator": {"page": 2, "table": "1", "row": "A"},
        }
    }
    sample.update(sample_fields)
    return sample


def _extract(
    sample: dict,
    *,
    quantity: str = "equilibrium_phase_composition",
    component: str = _GLASS_AL2O3,
    method: str | None = None,
    value_field: str = "component_amount",
    formula: str = "Al2O3",
) -> dict:
    experiment: dict = {"experiment_id": "run-1", "sample": sample}
    if method is not None:
        experiment["method"] = method
    values = {
        "quantity": quantity,
        "phase_label": "glass",
        "basis": "wt_percent",
        "T_C": 1030,
        value_field: component,
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "point_conditions": {
            "total_pressure_Pa": {
                "state": {"tag": "value", "value": "201000000"},
                "locator": {"page": 4, "table": "2"},
            },
            "fO2_log": {
                "state": {"tag": "value", "value": "-4.31"},
                "locator": {"page": 4, "table": "2"},
            },
        },
    }
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture, A. (2026), Test Journal 1:1"},
        "experiments": [experiment],
        "species": {
            formula: {
                "observations": [
                    {
                        "observation_id": "glass-al",
                        "experiment": "run-1",
                        "type": "concentration_series",
                        "units": "wt.%",
                        "locator": {"page": 6, "table": "3"},
                        "values": values,
                    }
                ]
            }
        },
    }


def _migrate_fixture(tmp_path: Path, document: dict):
    root = _write_min_tree(tmp_path, extract=document)
    return migrate(root, write=False).observations["fixture-source::glass-al"]


def _starting_mole_fraction():
    return wt_pct_to_mole_fraction(
        {name: Decimal(amount) for name, amount in _OXIDES}
    )


def test_closed_starting_bulk_becomes_identity_not_the_glass(tmp_path: Path) -> None:
    observation = _migrate_fixture(tmp_path, _extract(_initial()))
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert composition.value.components == _starting_mole_fraction().components
    assert observation.value.point == Decimal(_GLASS_AL2O3)
    assert all(
        amount != Decimal(_GLASS_AL2O3)
        for _name, amount in composition.value.components
    )
    reasons = [notice.reason for notice in observation.notices]
    assert not any(reason.startswith("open_system_starting_bulk:") for reason in reasons)
    assert any(
        notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
        for notice in observation.notices
    )


def test_open_system_carries_the_bulk_and_flags_it(tmp_path: Path) -> None:
    observation = _migrate_fixture(
        tmp_path,
        _extract(_initial(), method="langmuir_free_evaporation"),
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.components == _starting_mole_fraction().components
    open_notices = [
        notice
        for notice in observation.notices
        if notice.reason.startswith("open_system_starting_bulk:")
    ]
    assert len(open_notices) == 1
    assert "langmuir_free_evaporation" in open_notices[0].reason
    assert open_notices[0].kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert open_notices[0].band == "open_system_starting_bulk"


def test_open_crucible_text_is_a_typed_notice(tmp_path: Path) -> None:
    sample = _initial(
        container={
            "state": {"tag": "value", "value": "open crucible"},
            "locator": {"page": 2},
        }
    )
    observation = _migrate_fixture(tmp_path, _extract(sample))
    assert observation.identity.composition is not None
    assert observation.identity.composition.is_value
    assert any(
        notice.reason == "open_system_starting_bulk: open crucible"
        for notice in observation.notices
    )


def test_missing_starting_bulk_stays_missing(tmp_path: Path) -> None:
    observation = _migrate_fixture(tmp_path, _extract({}))
    composition = observation.identity.composition
    assert composition is None or not composition.is_value


def test_dash_in_starting_bulk_is_not_zero(tmp_path: Path) -> None:
    oxides = tuple(
        (name, "-" if name == "MnO" else amount) for name, amount in _OXIDES
    )
    try:
        observation = _migrate_fixture(tmp_path, _extract(_initial(oxides)))
    except (ArithmeticError, ValueError, TypeError):
        return
    composition = observation.identity.composition
    if composition is None or not composition.is_value:
        return
    amounts = dict(composition.value.components)
    assert amounts.get("MnO") != Decimal("0")
    without_mno = {
        "FeO" if name == "FeOT" else name
        for name, _amount in _OXIDES
        if name != "MnO"
    }
    assert set(amounts) != without_mno


def test_residue_row_does_not_take_the_starting_bulk(tmp_path: Path) -> None:
    document = _extract(
        _initial(),
        quantity="residue_component_composition",
        value_field="SiO2_wt_pct",
        component="35.1",
        formula="SiO2",
    )
    observation = _migrate_fixture(tmp_path, document)
    assert observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    composition = observation.identity.composition
    if composition is not None and composition.is_value:
        assert composition.value.components != _starting_mole_fraction().components


def test_cacciatore_starting_bulk_reaches_every_glass_row(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "cacciatore-zajacz-2026-v-sc-oxybarometer.yaml",
        write=False,
    )
    rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(rows) == 682
    with_composition = [
        observation
        for observation in rows
        if observation.identity.composition is not None
        and observation.identity.composition.is_value
    ]
    assert len(with_composition) == 682
    for observation in rows:
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        conditions = observation.point_conditions or {}
        assert conditions["total_pressure_Pa"].state.is_value
        assert conditions["fO2_log"].state.is_value
        assert not any(
            notice.reason.startswith("open_system_starting_bulk:")
            for notice in observation.notices
        )
        composition = observation.identity.composition.value
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        assert observation.value.point not in {
            amount for _name, amount in composition.components
        }

    shown = (
        (
            "cacciatore-zajacz-2026-v-sc-oxybarometer::"
            "cacciatore-zajacz-2026-v-sc-oxybarometer_e1_0_ii_h2_a_f_glass_al2o3",
            "Al2O3",
            Decimal("16.0"),
            Decimal("18.25"),
        ),
        (
            "cacciatore-zajacz-2026-v-sc-oxybarometer::"
            "cacciatore-zajacz-2026-v-sc-oxybarometer_e1_0_ii_h2_a_f_glass_sio2",
            "SiO2",
            Decimal("50.5"),
            Decimal("52.08"),
        ),
        (
            "cacciatore-zajacz-2026-v-sc-oxybarometer::"
            "cacciatore-zajacz-2026-v-sc-oxybarometer_e4_4_h2_b_glass_sio2",
            "SiO2",
            Decimal("60.8"),
            Decimal("61.54"),
        ),
    )
    for observation_id, oxide, starting_wt, glass_wt in shown:
        observation = result.observations[observation_id]
        assert observation.value.point == glass_wt
        starting = {
            name: Decimal(amount) for name, amount in _table1_wt(observation)
        }
        assert starting[oxide] == starting_wt
        expected = wt_pct_to_mole_fraction(starting)
        assert observation.identity.composition.value.components == expected.components
        assert glass_wt != starting_wt


def _table1_wt(observation) -> tuple[tuple[str, str], ...]:
    """Printed wt% retained on the starting-bulk derivation, not a second formula."""

    located = (observation.point_conditions or {}).get("composition")
    derivation = located.inference if located is not None else None
    assert derivation is not None
    weights = {}
    prefix = "original_"
    suffix = "_wt_pct"
    for name, parameter in derivation.parameters:
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        oxide = name[len(prefix) : -len(suffix)]
        weights[oxide] = str(parameter.state.value)
    assert weights
    return tuple(weights.items())


def test_wang_2001_residue_keeps_the_starting_charge_contextual() -> None:
    from simulator.battery.migrate import Migrator

    extract = REPO / "data/literature/extracts/wang-2001-solar-melt-evaporation-residues.yaml"
    migrator = Migrator(root=REPO, index={}, aliases={})
    migrator._migrate_extract(extract)
    migrator.finalize()
    residues = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and "sc12" in observation.observation_id.casefold()
    ]
    assert residues
    starting = wt_pct_to_mole_fraction(
        {
            "MgO": Decimal("24.8"),
            "Al2O3": Decimal("2.46"),
            "SiO2": Decimal("33.8"),
            "CaO": Decimal("2.04"),
            "TiO2": Decimal("0.110"),
            "FeO": Decimal("36.7"),
        }
    )
    for observation in residues:
        composition = observation.identity.composition
        if composition is None or not composition.is_value:
            continue
        assert composition.value.components != starting.components
        assert composition.value.proxy_flag != "initial_charge_only"
