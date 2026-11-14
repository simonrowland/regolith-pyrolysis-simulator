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
from simulator.chemistry.offgas_fo2 import imposed_fo2, load_buffer_polynomials


ROOT = Path(__file__).resolve().parents[2]


def _source_doc(name: str) -> dict:
    with (ROOT / "data/literature/extracts" / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _resolution(atmosphere: str, temperature_K: Decimal):
    mix = _printed_sccm_gas_mixture(atmosphere)
    assert mix is not None, atmosphere
    species_mol, _precision = mix
    gibbs, gas_constant = _janaf_gas_formation_inputs(
        ("CO", "CO2", "H2", "H2O", "O2"), temperature_K
    )
    return imposed_fo2(
        {name: float(amount) for name, amount in species_mol.items()},
        float(temperature_K),
        load_buffer_polynomials(),
        janaf_formation_gibbs_J_per_mol=gibbs,
        janaf_gas_constant_J_per_mol_K=gas_constant,
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
    sossi_result = _resolution(sossi_mix, Decimal("2173.15"))
    sossi_decision = _bare_log_frame_decision(
        Decimal("-7.86"),
        Decimal("2173.15"),
        printed_atmospheres=(sossi_mix,),
        total_pressure_pa=Decimal("100000"),
    )
    assert sossi_decision.state.is_value
    assert sossi_decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
    assert sossi_result.uncertainty_band_dex is not None

    doyle = _source_doc("doyle-1989-tio2-feo-activity-magma.yaml")
    doyle_row = next(
        row
        for block in doyle["species"].values()
        for row in block.get("context", [])
        if row.get("observation_id") == "doyle_1989_table1b_setpoint_020"
    )
    doyle_mix = _row_printed_atmospheres(doyle_row, ())[0]
    doyle_result = _resolution(doyle_mix, Decimal("1600"))
    doyle_decision = _bare_log_frame_decision(
        Decimal("-11.92"),
        Decimal("1600"),
        printed_atmospheres=(doyle_mix,),
    )
    assert doyle_decision.state.is_value
    assert doyle_decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
    assert doyle_result.uncertainty_band_dex is not None


@pytest.mark.parametrize("chunk_index", range(3))
def test_all_sossi_ratio_and_doyle_context_records_are_admitted_or_typed_mismatch(
    chunk_index: int,
) -> None:
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
    decision_table = []
    for record_id, printed, temperature, atmosphere in records:
        result = _resolution(atmosphere, temperature)
        decision = _bare_log_frame_decision(
            printed,
            temperature,
            printed_atmospheres=(atmosphere,),
            total_pressure_pa=(Decimal("100000") if record_id.startswith("sossi") else None),
        )
        if decision.state.is_value:
            assert decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
            outcome = "admitted"
        else:
            assert decision.state.is_unknown
            assert decision.notices[0].kind is NoticeKind.PROBABLE_SOURCE_MISPRINT
            assert "mismatch=" in decision.state.reason
            assert "dex" in decision.state.reason
            outcome = f"mismatch={printed - Decimal(str(result.log10_fO2)):+.4f} dex"
        decision_table.append(
            (
                record_id,
                str(printed),
                f"{result.log10_fO2:+.4f}",
                f"{result.uncertainty_band_dex:.4f}",
                outcome,
            )
        )
    start = chunk_index * 5
    assert False, "\n".join(",".join(row) for row in decision_table[start : start + 5])


def test_inert_n2_dilution_is_in_the_total_but_cancels_and_ratio_mutation_moves_fo2() -> None:
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

    with_n2 = _resolution(atmosphere, Decimal("1600"))
    without_n2 = _resolution("CO2 15 × 1 SCCM + H2 100 × 1 SCCM", Decimal("1600"))
    assert with_n2.log10_fO2 == without_n2.log10_fO2

    changed_ratio = _resolution(
        "CO2:H2:N2 = 16.0:100.0:150.0 c.c.s/min", Decimal("1600")
    )
    assert changed_ratio.log10_fO2 != with_n2.log10_fO2


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
    assert decision.notices[0].kind is NoticeKind.PROBABLE_SOURCE_MISPRINT
    assert "mismatch=" in decision.state.reason and "dex" in decision.state.reason
