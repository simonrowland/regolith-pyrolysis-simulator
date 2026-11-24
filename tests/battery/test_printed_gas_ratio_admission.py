from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.enums import Authority, NoticeKind
from simulator.battery.migrate import (
    Migrator,
    _bare_log_frame_decision,
    _janaf_gas_formation_inputs,
    _quoted_atmosphere_values,
)
from simulator.battery.oxygen_identity import (
    _printed_sccm_gas_mixture,
    _row_printed_atmospheres,
    log10_fo2_to_pa,
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
    *,
    check_methane: bool = False,
):
    mix = _printed_sccm_gas_mixture(atmosphere)
    assert mix is not None, atmosphere
    species_mol, _precision = mix
    formulas = ("CO", "CO2", "H2", "H2O", "O2", "CH4") if check_methane else (
        "CO", "CO2", "H2", "H2O", "O2"
    )
    gibbs, gas_constant = _janaf_gas_formation_inputs(formulas, temperature_K)
    return _full_balance_fo2_with_janaf_band(
        {name: float(amount) for name, amount in species_mol.items()},
        float(temperature_K),
        gibbs,
        gas_constant,
        load_buffer_polynomials(),
        initial_total_pressure_bar=total_pressure_bar,
        methane_formation_gibbs_J_per_mol=(gibbs["CH4"] if check_methane else None),
    )


def test_sossi_and_doyle_real_rows_respect_total_pressure_availability() -> None:
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
    assert doyle_decision.state.value == log10_fo2_to_pa(Decimal("-11.92"))
    assert doyle_decision.notice_kind is NoticeKind.INPUT_OMITTED
    assert "total pressure" in doyle_decision.notice_reason
    assert not any(
        notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        for notice in doyle_decision.notices
    )
    assert Decimal(str(doyle_result.log10_fO2)).quantize(Decimal("0.0001")) == Decimal("-11.9193")
    assert Decimal(str(doyle_band)).quantize(Decimal("0.0001")) == Decimal("0.0004")


def test_located_pure_oxygen_label_reaches_the_bare_log_frame() -> None:
    """A categorical O2 label is atmosphere evidence at the printed total pressure.

    Sossi Per-fO2(1) prints O2 and 1 bar. CO2 is not air or pure oxygen, so
    the quote owner still does not forward it.
    """

    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    experiments = {
        experiment["experiment_id"]: experiment for experiment in sossi["experiments"]
    }
    pure = experiments["sossi-per-f-o2-1"]
    labels = _quoted_atmosphere_values(pure["conditions"], pure["locator"])
    assert labels == ("O2",)
    decision = _bare_log_frame_decision(
        Decimal("0.00"),
        Decimal("2173.15"),
        printed_atmospheres=labels,
        total_pressure_pa=Decimal("100000"),
    )
    assert decision.state.is_value
    assert decision.state.value == Decimal("100000")
    assert decision.notice_kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE

    carbon_dioxide = experiments["sossi-per-f-o2-2"]
    assert _quoted_atmosphere_values(
        carbon_dioxide["conditions"], carbon_dioxide["locator"]
    ) == ()
    refused = _bare_log_frame_decision(
        Decimal("0.00"),
        Decimal("2173.15"),
        printed_atmospheres=(),
        total_pressure_pa=Decimal("100000"),
    )
    assert refused.state.is_unknown
    assert refused.state.reason == _BARE_LOG_SCALE_UNKNOWN


def test_inherited_sossi_log_uses_gas_evaluation_temperature() -> None:
    from simulator.battery.migrate import (
        PrintedOxygenFacts,
        _scoped_oxygen_decision,
        printed_oxygen_decision,
    )
    from simulator.battery.records import Located, Locator, Value
    from simulator.state_types import State

    log_locator = Locator(page=14, table="S4")
    facts = PrintedOxygenFacts(
        log_fO2=Located(State.of(Decimal("-2.53")), locator=log_locator),
        bare_log=True,
    )
    evaluation_temperature = Located(
        State.of(Value.point_of("2173.15")),
        locator=log_locator,
    )
    run_temperature = Decimal("2134.15")
    atmosphere = "99CO2:1H2"
    shared = {
        "run_temperature_K": run_temperature,
        "total_pressure_pa": Decimal("100000"),
        "printed_atmospheres": (atmosphere,),
        "gas_fO2_evaluation_temperature_K": evaluation_temperature,
    }

    row_decision = printed_oxygen_decision(
        facts,
        raw_item={},
        point_conditions={},
        scope="row",
        **shared,
    )
    inherited_decision, used_fallback = _scoped_oxygen_decision(
        facts=facts,
        from_fallback=True,
        raw_item={},
        row_point_conditions={},
        **shared,
    )

    assert used_fallback
    assert row_decision is not None
    assert inherited_decision == row_decision


@pytest.mark.parametrize("pressure_state", ("absent", "unknown", "invalid"))
def test_gas_mix_without_valid_total_pressure_keeps_log_but_withholds_frame(
    pressure_state: str,
) -> None:
    from simulator.battery.migrate import PrintedOxygenFacts, printed_oxygen_decision
    from simulator.battery.records import Located, Locator
    from simulator.state_types import State

    locator = Locator(page=1)
    point_conditions = {}
    if pressure_state == "unknown":
        point_conditions["total_pressure_Pa"] = Located(
            State.unknown("not_published"), locator=locator
        )
    elif pressure_state == "invalid":
        point_conditions["total_pressure_Pa"] = Located(
            State.of(Decimal("0")), locator=locator
        )
    facts = PrintedOxygenFacts(
        log_fO2=Located(State.of(Decimal("-10")), locator=locator),
        bare_log=True,
    )

    decision = printed_oxygen_decision(
        facts,
        raw_item={},
        point_conditions=point_conditions,
        scope="row",
        run_temperature_K=Decimal("1600"),
        printed_atmospheres=(
            "H2O 1.000 × 1.00 SCCM + H2 1.000 × 1.00 SCCM",
        ),
    )

    assert decision is not None
    assert decision.state.is_value
    assert decision.state.value == log10_fo2_to_pa(Decimal("-10"))
    assert decision.notice_kind is NoticeKind.INPUT_OMITTED
    assert "gas-mixture consistency check unavailable" in decision.notice_reason
    assert "total pressure" in decision.notice_reason
    assert decision.notice_authority is None
    assert not any(
        notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        for notice in decision.notices
    )


def test_real_sossi_and_doyle_migrations_preserve_gas_fugacity_identity() -> None:
    sossi = _source_doc("sossi-2020-magma-ocean-redox.yaml")
    doyle = _source_doc("doyle-1989-tio2-feo-activity-magma.yaml")
    root = Path(__file__).resolve().parents[2]
    source_paths = (
        root / "data/literature/extracts/sossi-2020-magma-ocean-redox.yaml",
        root / "data/literature/extracts/doyle-1989-tio2-feo-activity-magma.yaml",
    )
    migrator = Migrator(root)
    for path in source_paths:
        migrator._migrate_extract(path)
    migrator.finalize()

    def point(field: dict) -> Decimal:
        value = field["state"]["value"]
        if isinstance(value, dict) and value.get("kind") == "point":
            value = value["point"]
        return Decimal(str(value))

    sossi_experiments = {
        experiment["experiment_id"]: experiment
        for experiment in sossi["experiments"]
    }
    sossi_rows = [
        row
        for row in sossi["species"]["Fe"]["observations"]
        if ":" in sossi_experiments[row["experiment"]]["conditions"][
            "gas_mixture_as_printed"
        ]["state"]["value"]
    ]
    assert len(sossi_rows) == 10
    green_fO2_Pa = {
        "sossi_2020_per_f_o2_3_xanes": Decimal("295.1209226666"),
        "sossi_2020_per_f_o2_6_xanes": Decimal("263.0267991895"),
        "sossi_2020_per_f_o2_7_xanes": Decimal("190.5460717963"),
        "sossi_2020_per_f_o2_8_xanes": Decimal("100"),
        "sossi_2020_per_f_o2_9_xanes": Decimal("12.0226443462"),
        "sossi_2020_per_f_o2_10_xanes": Decimal("0.4786300923226383439220877393"),
        "sossi_2020_per_f_o2_11_xanes": Decimal("0.0181970085861"),
        "sossi_2020_per_f_o2_12_xanes": Decimal("0.00138038426460"),
        "sossi_2020_per_ts1_xanes": Decimal("190.5460717963"),
        "sossi_2020_per_ts2_xanes": Decimal("190.5460717963"),
    }
    assert set(green_fO2_Pa) == {row["observation_id"] for row in sossi_rows}
    for row in sossi_rows:
        experiment = sossi_experiments[row["experiment"]]
        conditions = experiment["conditions"]
        run_temperature_K = point(conditions["temperature_K"])
        evaluation_temperature_K = point(
            conditions["gas_fO2_evaluation_temperature_K"]
        )
        assert evaluation_temperature_K == Decimal("2173.15")
        observation_id = f"sossi-2020-magma-ocean-redox::{row['observation_id']}"
        observation = migrator.result.observations[observation_id]
        assert observation.identity.temperature_K.value == run_temperature_K

        printed_log_fO2 = point(conditions["log_fO2"])
        expected_fO2_Pa = log10_fo2_to_pa(printed_log_fO2)
        assert observation.identity.fO2_Pa.value == expected_fO2_Pa
        assert abs(expected_fO2_Pa - green_fO2_Pa[row["observation_id"]]) < Decimal(
            "0.000000001"
        )

        atmosphere = _quoted_atmosphere_values(conditions, experiment["locator"])[0]
        balance, _cea_log_fO2, band = _resolution(
            atmosphere, evaluation_temperature_K, 1.0
        )
        assert abs(printed_log_fO2 - Decimal(str(balance.log10_fO2))) <= (
            Decimal("0.01") + Decimal(str(band))
        )
        frame_notices = [
            notice
            for notice in observation.notices
            if notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        ]
        assert len(frame_notices) == 1
        assert frame_notices[0].authority is Authority.CERTIFIED

    doyle_contexts = {
        f"doyle-1989-tio2-feo-activity-magma::context::{row['observation_id']}": row
        for block in doyle["species"].values()
        for row in block.get("context", [])
        if row.get("type") == "typed_context"
        and "CO2:H2:N2" in row.get("values", {}).get("gas_mixture_as_printed", "")
    }
    assert len(doyle_contexts) == 5
    linked_doyle_rows = [
        (observation, context_id)
        for observation in migrator.result.observations.values()
        if observation.source_id == "doyle-1989-tio2-feo-activity-magma"
        for context_id in observation.derived_from
        if context_id in doyle_contexts
    ]
    assert linked_doyle_rows
    assert {context_id for _observation, context_id in linked_doyle_rows} == set(
        doyle_contexts
    )
    for observation, context_id in linked_doyle_rows:
        values = doyle_contexts[context_id]["values"]
        printed_log_fO2 = -Decimal(str(values["minus_log10_fO2_as_printed"]))
        assert observation.identity.temperature_K.value == Decimal("1600")
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.value == log10_fo2_to_pa(printed_log_fO2)
        assert any(
            notice.kind is NoticeKind.INPUT_OMITTED
            and "gas-mixture consistency check unavailable" in notice.reason
            for notice in observation.notices
        ), tuple(
            (notice.kind.value, notice.reason) for notice in observation.notices
        )
        assert not any(
            notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
            for notice in observation.notices
        )


def test_run_temperature_fallback_for_gas_mix_is_visible() -> None:
    decision = _bare_log_frame_decision(
        Decimal("-2.53"),
        Decimal("2134.15"),
        printed_atmospheres=("99CO2:1H2",),
        total_pressure_pa=Decimal("100000"),
    )

    assert decision.state.is_unknown
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "run temperature 2134.15 K used" in notice.reason
        for notice in decision.notices
    )


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

    co2_rich = "CO2:H2:N2 = 99.0:1.0:0.0 c.c.s/min"
    co2_rich_diluted = "CO2:H2:N2 = 99.0:1.0:900.0 c.c.s/min"
    hot_rich, _, _ = _resolution(co2_rich, Decimal("2173"))
    hot_rich_diluted, _, _ = _resolution(co2_rich_diluted, Decimal("2173"))
    assert abs(hot_rich_diluted.log10_fO2 - hot_rich.log10_fO2) == pytest.approx(
        0.6, abs=0.12
    )
    cool_rich, _, _ = _resolution(co2_rich, Decimal("1000"))
    cool_rich_diluted, _, _ = _resolution(co2_rich_diluted, Decimal("1000"))
    assert cool_rich_diluted.log10_fO2 == pytest.approx(
        cool_rich.log10_fO2, abs=1e-7
    )


def test_jayasuriya_fev15_full_balance_band_at_explicit_pressure() -> None:
    jayasuriya = _source_doc("jayasuriya-2004-iron-mossbauer.yaml")
    observation = next(
        item
        for item in jayasuriya["species"]["Fe"]["observations"]
        if item["observation_id"] == "jayasuriya_2004_rt_1pct"
    )
    point = next(
        point
        for point in observation["values"]["points"]
        if point["sample_as_published"] == "FeV15"
    )
    atmosphere = point["gas_mix_as_printed"]
    parsed = _printed_sccm_gas_mixture(atmosphere)
    assert parsed is not None
    species_mol, relative_precision = parsed
    gibbs, gas_constant = _janaf_gas_formation_inputs(
        ("CO", "CO2", "H2", "H2O", "O2"), Decimal("1682.15")
    )
    result, _cea, band = _full_balance_fo2_with_janaf_band(
        {name: float(amount) for name, amount in species_mol.items()},
        1682.15,
        gibbs,
        gas_constant,
        load_buffer_polynomials(),
        initial_total_pressure_bar=1.01325,
        relative_precision=relative_precision,
    )
    printed = Decimal(str(point["log_fO2_as_published"]))
    printed_tolerance = Decimal("0.01")
    combined_allowance = printed_tolerance + Decimal(str(band))
    mismatch = abs(printed - Decimal(str(result.log10_fO2)))
    assert result.log10_fO2 == pytest.approx(-3.949, abs=0.005)
    assert float(mismatch) == pytest.approx(0.039, abs=0.002)
    assert mismatch <= combined_allowance


@pytest.mark.parametrize("sample", ("FeV2", "FeV12", "FeV15", "FeV19", "FeV22"))
def test_jayasuriya_real_mixtures_keep_log_without_pressure_certification(
    sample: str,
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
    temperature = Decimal(str(point["T_C"])) + Decimal("273.15")
    decision = _bare_log_frame_decision(
        Decimal(str(point["log_fO2_as_published"])),
        temperature,
        printed_atmospheres=(atmosphere,),
    )
    assert decision.state.is_value
    assert decision.notice_kind is NoticeKind.INPUT_OMITTED
    assert "total pressure" in decision.notice_reason
    assert not any(
        notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        for notice in decision.notices
    )
    assert not any("graphite saturation" in notice.reason for notice in decision.notices)


def test_graphite_saturated_match_keeps_value_but_downgrades_authority() -> None:
    atmosphere = "9CO:1CO2"
    result, _cea, _band = _resolution(atmosphere, Decimal("1000"))
    decision = _bare_log_frame_decision(
        Decimal(str(result.log10_fO2)),
        Decimal("1000"),
        printed_atmospheres=(atmosphere,),
        total_pressure_pa=Decimal("101325"),
    )
    assert decision.state.is_value
    assert decision.notice_authority.value == "extrapolated"
    assert any("graphite saturation a_C=" in notice.reason for notice in decision.notices)


def test_cold_hydrogen_rich_mix_downgrades_when_methane_moves_fo2() -> None:
    atmosphere = "1CO2:9H2"
    result, _cea, band = _resolution(
        atmosphere, Decimal("700"), check_methane=True
    )
    decision = _bare_log_frame_decision(
        Decimal(str(result.log10_fO2)),
        Decimal("700"),
        printed_atmospheres=(atmosphere,),
        total_pressure_pa=Decimal("101325"),
    )
    assert decision.state.is_value
    assert decision.notice_authority.value == "extrapolated"
    methane_notice = next(
        notice.reason for notice in decision.notices
        if "CH4 not negligible" in notice.reason
    )
    assert "versus a" in methane_notice and "dex" in methane_notice
    assert f"{band:.3g} dex" in methane_notice


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
    oxygen_scale = 1.0 - eq["O2"]
    assert complete == pytest.approx(initial_oxygen_atoms * oxygen_scale, abs=1e-12)
    assert abs(dropped_o2 - initial_oxygen_atoms * oxygen_scale) > 1e-4


@pytest.mark.parametrize(
    ("sample", "expected_log_fO2"),
    [("FeV15", "-3.9489"), ("FeV19", "-12.9442")],
)
def test_jayasuriya_full_balance_pins_do_not_certify_missing_pressure(
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
    result, _cea, _band = _resolution(atmosphere, temperature_K, 1.01325)

    decision = _bare_log_frame_decision(
        printed,
        temperature_K,
        printed_atmospheres=(atmosphere,),
    )

    assert Decimal(str(result.log10_fO2)).quantize(Decimal("0.0001")) == Decimal(expected_log_fO2)
    assert decision.state.is_value
    assert decision.state.value == log10_fo2_to_pa(printed)
    assert decision.notice_kind is NoticeKind.INPUT_OMITTED
    assert "total pressure" in decision.notice_reason


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
