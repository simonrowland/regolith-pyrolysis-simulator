"""Source-condition selection for the Rego and Ryś sodium vapour rows."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, Phase, Quantity
from simulator.battery.fo2_ladder import resolve_row_fo2
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Species, State, Value
from tests.battery import factories as F

ROOT = Path(__file__).resolve().parents[2]
REGO_SOURCE = "rego-1985-na2o-sio2-transpiration"
RYS_SOURCE = "rys-2007-alkali-oxide-thermodynamics"


def _na_partial_row(
    *,
    source_id: str,
    observation_id: str,
    experiment_id: str,
    temperature_K: str,
    pressure_Pa: str,
    total_pressure_Pa: str | None = None,
    composition: Composition | None = None,
):
    identity = Identity(
        quantity=State.of(Quantity.P_PARTIAL),
        species=Species("Na", Phase.G),
        temperature_K=State.of(Decimal(temperature_K)),
        composition=None if composition is None else State.of(composition),
        fO2_Pa=State.unknown("no printed oxygen value on this row"),
        total_pressure_Pa=(
            None
            if total_pressure_Pa is None
            else State.of(Decimal(total_pressure_Pa))
        ),
    )
    return F.observation(
        observation_id,
        experiment_id,
        identity,
        Decimal(pressure_Pa),
        source_id=source_id,
    )


def test_rego_table_i_row_selects_flagged_two_species_c_co_condition() -> None:
    # Table I run 1: 1300 C, P=1.015 atm, pNa=0.00224 atm. The migrated
    # source values are 102844.875 Pa total and 226.968 Pa sodium.
    experiment = F.tabulation_experiment(
        "rego-run-01",
        T_K=Decimal("1573.15"),
        total_P=Decimal("102844.875"),
    )
    row = _na_partial_row(
        source_id=REGO_SOURCE,
        observation_id="rego-run-01-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1573.15",
        pressure_Pa="226.968",
        total_pressure_Pa="102844.875",
    )

    selected = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
    )

    assert selected is not None
    assert selected.route == "rego_c_co_two_species_approximation"
    assert selected.tier == 3
    assert selected.log10_fO2_bar == pytest.approx(-16.6550589855, abs=1e-10)

    # Mutation proof: the C/CO condition follows the row's printed Table I
    # total pressure. Increasing it raises pCO and therefore raises fO2.
    changed_identity = replace(
        row.identity,
        total_pressure_Pa=State.of(Decimal("103844.875")),
    )
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, identity=changed_identity),
        quantity=Quantity.P_PARTIAL,
    )
    assert changed is not None
    assert changed.log10_fO2_bar > selected.log10_fO2_bar


def test_rys_na_row_without_oxygen_selects_flagged_congruent_condition() -> None:
    # Ryś Table 10a, NaKAlSi_1 row 8 (1742.385 K, 0.01475 Pa Na).
    composition = Composition(
        "oxides",
        (
            ("Na2O", Decimal("0.0380")),
            ("Al2O3", Decimal("0.0647")),
            ("SiO2", Decimal("0.8659")),
            ("K2O", Decimal("0.0314")),
        ),
        AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment("rys-na-row-8")
    row = _na_partial_row(
        source_id=RYS_SOURCE,
        observation_id="rys-na-row-8-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1742.385",
        pressure_Pa="0.01475",
        composition=composition,
    )

    selected = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
    )

    assert selected is not None
    assert selected.route == "rys_congruent_vaporization_predicted"
    assert selected.tier == 3
    assert selected.physics_rung == 3
    assert selected.closure is False
    assert selected.po2_bar == pytest.approx(4.35036944144e-8, rel=1e-12)
    assert selected.log10_fO2_bar == pytest.approx(-7.361473860386677, abs=1e-12)

    # Mutation proof: doubling the measured Na pressure doubles predicted O2.
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, value=Value.point_of(row.value.point * 2)),
        quantity=Quantity.P_PARTIAL,
    )
    assert changed is not None
    assert changed.po2_bar == pytest.approx(2.0 * selected.po2_bar, rel=1e-12)


def test_rys_table10a_child_restores_nakalsi_1_row_8() -> None:
    legacy_path = (
        ROOT
        / "data/literature/extracts/rys-2007-alkali-oxide-thermodynamics.yaml"
    )
    legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
    table10a = next(
        row
        for row in legacy["species"]["Na"]["observations"]
        if row["observation_id"] == "rys_2007_appendix_na_partial_pressures_table10a"
    )
    row8 = [
        point
        for point in table10a["values"]["points"]
        if point.get("source_row") == 8
    ]
    assert len(row8) == 1
    assert row8[0]["sample_as_printed"] == "NaKAlSi_1"
    assert row8[0]["T_K"] == "1742.385"
    assert row8[0]["pressure_Pa"] == "0.01475"

    migrated_path = (
        ROOT
        / "data/literature/extracts-v2/rys-2007-alkali-oxide-thermodynamics.yaml"
    )
    migrated = migrated_path.read_text(encoding="utf-8")
    child_ref = re.search(
        r"(?m)^  - (rys-2007-alkali-oxide-thermodynamics::"
        r"rys_2007_appendix_na_partial_pressures_table10a::"
        r"T=1742\.385:h=[0-9a-f]{12})$",
        migrated,
    )
    assert child_ref is not None
    assert migrated.count(child_ref.group(1)) >= 2
