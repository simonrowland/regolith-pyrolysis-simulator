from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.enums import NoticeKind
from simulator.battery.migrate import (
    _bare_log_frame_decision,
    _janaf_gas_formation_inputs,
    _printed_sccm_gas_mixture,
    _quoted_atmosphere_values,
    _row_printed_atmospheres,
)
from simulator.chemistry.offgas_fo2 import (
    _full_balance_fo2_with_janaf_band,
    _full_element_balance_fo2,
    load_buffer_polynomials,
)


ROOT = Path(__file__).resolve().parents[2]


def _source_doc(name: str) -> dict:
    with (ROOT / "data/literature/extracts" / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _resolution(
    atmosphere: str,
    temperature_K: Decimal,
    total_pressure_bar: float = 1.01325,
):
    mix = _printed_sccm_gas_mixture(atmosphere)
    assert mix is not None, atmosphere
    species_mol, _precision = mix
    gibbs, gas_constant = _janaf_gas_formation_inputs(
        ("CO", "CO2", "H2", "H2O", "O2"), temperature_K
    )
    return _full_balance_fo2_with_janaf_band(
        {name: float(amount) for name, amount in species_mol.items()},
        float(temperature_K),
        gibbs,
        gas_constant,
        load_buffer_polynomials(),
        initial_total_pressure_bar=total_pressure_bar,
    )


def test_sossi_and_doyle_real_rows_reach_bare_log_admission_with_pins() -> None:
    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    sossi_experiment = next(
        experiment
        for experiment in sossi["experiments"]
        if experiment["experiment_id"] == "sossi-per-f-o2-12"
    )
    sossi_conditions = sossi_experiment["conditions"]
    sossi_mix = _quoted_atmosphere_values(
        sossi_conditions, sossi_experiment["locator"]
    )[0]
    sossi_result, _sossi_cea, sossi_band = _resolution(
        sossi_mix, Decimal("2173.15"), 1.0
    )
    sossi_decision = _bare_log_frame_decision(
        Decimal("-7.86"),
        Decimal("2173.15"),
        printed_atmospheres=(sossi_mix,),
        total_pressure_pa=Decimal("100000"),
    )
    assert sossi_decision.state.is_value
    assert sossi_decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
    assert Decimal(str(sossi_result.log10_fO2)).quantize(Decimal("0.0001")) == Decimal("-7.8604")
    assert Decimal(str(sossi_band)).quantize(Decimal("0.0001")) == Decimal("0.0028")

    doyle = _source_doc("doyle-1989-tio2-feo-activity-magma.yaml")
    doyle_row = next(
        row
        for block in doyle["species"].values()
        for row in block.get("context", [])
        if row.get("observation_id") == "doyle_1989_table1b_setpoint_020"
    )
    doyle_mix = _row_printed_atmospheres(doyle_row, ())[0]
    doyle_result, _doyle_cea, doyle_band = _resolution(doyle_mix, Decimal("1600"))
    doyle_decision = _bare_log_frame_decision(
        Decimal("-11.92"),
        Decimal("1600"),
        printed_atmospheres=(doyle_mix,),
    )
    assert doyle_decision.state.is_value
    assert doyle_decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
    assert Decimal(str(doyle_result.log10_fO2)).quantize(Decimal("0.0001")) == Decimal("-11.9193")
    assert Decimal(str(doyle_band)).quantize(Decimal("0.0001")) == Decimal("0.0004")


def test_all_sossi_ratio_and_doyle_context_records_are_admitted_or_typed_mismatch() -> None:
    records: list[tuple[str, Decimal, Decimal, str]] = []
    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    for experiment in sossi["experiments"]:
        conditions = experiment.get("conditions", {})
        mixture = conditions.get("gas_mixture_as_printed", {})
        state = mixture.get("state", {}) if isinstance(mixture, dict) else {}
        ratio = state.get("value") if isinstance(state, dict) else None
        if not isinstance(ratio, str) or ":" not in ratio:
            continue
        log_state = conditions["log_fO2_as_printed"]["state"]["value"]
        candidates = _quoted_atmosphere_values(conditions, experiment["locator"])
        records.append((experiment["experiment_id"], Decimal(str(log_state)), Decimal("2173.15"), candidates[0]))

    doyle = _source_doc("doyle-1989-tio2-feo-activity-magma.yaml")
    for block in doyle["species"].values():
        for row in block.get("context", []):
            if row.get("type") != "typed_context":
                continue
            values = row.get("values", {})
            mixture = values.get("gas_mixture_as_printed")
            if not isinstance(mixture, str) or "CO2:H2:N2" not in mixture:
                continue
            records.append(
                (
                    row["observation_id"],
                    Decimal(str(values["minus_log10_fO2_as_printed"])) * -1,
                    Decimal("1600"),
                    _row_printed_atmospheres(row, ())[0],
                )
            )

    assert len(records) == 15
    admitted = 0
    refused = 0
    for record_id, printed, temperature, atmosphere in records:
        decision = _bare_log_frame_decision(
            printed,
            temperature,
            printed_atmospheres=(atmosphere,),
            total_pressure_pa=(Decimal("100000") if record_id.startswith("sossi") else None),
        )
        if decision.state.is_value:
            assert decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
            admitted += 1
        else:
            assert decision.state.is_unknown
            assert decision.state.reason.startswith("gas_mix_fo2_band_mismatch:")
            assert all(
                notice.kind is not NoticeKind.PROBABLE_SOURCE_MISPRINT
                for notice in decision.notices
            )
            assert "mismatch=" in decision.state.reason
            assert "dex" in decision.state.reason
            refused += 1
    assert (admitted, refused) == (15, 0)


def test_n2_dilution_only_cancels_when_dissociation_is_negligible_and_ratio_moves_fo2() -> None:
    ratio = _printed_sccm_gas_mixture("99CO2:1H2")
    assert ratio is not None
    assert ratio[0] == {"CO2": Decimal("0.99"), "H2": Decimal("0.01")}
    co_ratio = _printed_sccm_gas_mixture("9CO2:1CO")
    assert co_ratio is not None
    assert co_ratio[0] == {"CO2": Decimal("0.9"), "CO": Decimal("0.1")}

    atmosphere = "CO2:H2:N2 = 15.0:100.0:150.0 c.c.s/min"
    parsed = _printed_sccm_gas_mixture(atmosphere)
    assert parsed is not None
    mole_fractions, _precision = parsed
    assert sum(mole_fractions.values()) == Decimal(1)
    assert mole_fractions["N2"] == Decimal(150) / Decimal(265)

    low_with_n2, _, _ = _resolution(atmosphere, Decimal("1000"))
    low_without_n2, _, _ = _resolution(
        "CO2 15 × 1 SCCM + H2 100 × 1 SCCM", Decimal("1000")
    )
    assert low_with_n2.log10_fO2 == pytest.approx(low_without_n2.log10_fO2, abs=1e-7)

    hot_with_n2, _, _ = _resolution(atmosphere, Decimal("2173.15"))
    hot_without_n2, _, _ = _resolution(
        "CO2 15 × 1 SCCM + H2 100 × 1 SCCM", Decimal("2173.15")
    )
    assert abs(hot_with_n2.log10_fO2 - hot_without_n2.log10_fO2) > 1e-8

    changed_ratio, _, _ = _resolution(
        "CO2:H2:N2 = 16.0:100.0:150.0 c.c.s/min", Decimal("1600")
    )
    original_ratio, _, _ = _resolution(atmosphere, Decimal("1600"))
    assert changed_ratio.log10_fO2 != original_ratio.log10_fO2


def test_real_sossi_record_oxygen_balance_fails_when_free_o2_term_is_dropped() -> None:
    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    experiment = next(
        experiment for experiment in sossi["experiments"]
        if experiment["experiment_id"] == "sossi-per-f-o2-3"
    )
    mixture = _quoted_atmosphere_values(
        experiment["conditions"], experiment["locator"]
    )[0]
    parsed = _printed_sccm_gas_mixture(mixture)
    assert parsed is not None
    species_mol, _precision = parsed
    gibbs, gas_constant = _janaf_gas_formation_inputs(
        ("CO", "CO2", "H2", "H2O", "O2"), Decimal("2173.15")
    )
    result = _full_element_balance_fo2(
        {name: float(amount) for name, amount in species_mol.items()},
        2173.15,
        gibbs,
        gas_constant,
        initial_total_pressure_bar=1.0,
    )
    initial_oxygen_atoms = 2 * float(species_mol["CO2"])
    eq = result.equilibrium_partial_pressures_bar
    complete = 2 * eq["CO2"] + eq["CO"] + eq["H2O"] + 2 * eq["O2"]
    dropped_o2 = 2 * eq["CO2"] + eq["CO"] + eq["H2O"]
    assert complete == pytest.approx(initial_oxygen_atoms, abs=1e-12)
    assert abs(dropped_o2 - initial_oxygen_atoms) > 1e-4


@pytest.mark.parametrize(
    ("sample", "expected_log_fO2"),
    [("FeV15", "-3.9488"), ("FeV19", "-12.9442")],
)
def test_real_jayasuriya_mixtures_use_full_balance(
    sample: str, expected_log_fO2: str
) -> None:
    jayasuriya = _source_doc("jayasuriya-2004-iron-mossbauer.yaml")
    observation = next(
        item
        for item in jayasuriya["species"]["Fe"]["observations"]
        if item["observation_id"] == "jayasuriya_2004_rt_1pct"
    )
    point = next(
        point
        for point in observation["values"]["points"]
        if point["sample_as_published"] == sample
    )
    atmosphere = point["gas_mix_as_printed"]
    printed = Decimal(str(point["log_fO2_as_published"]))
    temperature_K = Decimal(str(point["T_C"])) + Decimal("273.15")
    result, _cea, _band = _resolution(atmosphere, temperature_K)

    decision = _bare_log_frame_decision(
        printed,
        temperature_K,
        printed_atmospheres=(atmosphere,),
    )

    assert Decimal(str(result.log10_fO2)).quantize(Decimal("0.0001")) == Decimal(expected_log_fO2)
    assert decision.state.is_value
    assert decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
    assert f"expected JANAF full-balance log10(fO2/bar)={expected_log_fO2}" in decision.notice_reason


def test_deliberately_wrong_printed_log_is_refused_with_dex_mismatch() -> None:
    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    experiment = next(
        experiment
        for experiment in sossi["experiments"]
        if experiment["experiment_id"] == "sossi-per-f-o2-12"
    )
    atmosphere = _quoted_atmosphere_values(
        experiment["conditions"], experiment["locator"]
    )[0]
    decision = _bare_log_frame_decision(
        Decimal("-30.00"),
        Decimal("2173.15"),
        printed_atmospheres=(atmosphere,),
        total_pressure_pa=Decimal("100000"),
    )
    assert decision.state.is_unknown
    assert decision.state.reason.startswith("gas_mix_fo2_band_mismatch:")
    assert not any(
        notice.kind is NoticeKind.PROBABLE_SOURCE_MISPRINT
        for notice in decision.notices
    )
