from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Engine, Rail
from simulator.battery.records import ApproxPressure, BandBasis
from simulator.battery.migrate import PrintedOxygenFacts, printed_oxygen_decision
from simulator.battery.score import (
    SAMPLE_PHASE_CRYSTALLINE_VS_LIQUID_ENGINE,
    rail_for_quantity,
    vapour_crystalline_sample_liquid_engine_refusal,
)
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = ROOT / "data/literature/extracts"


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row["observation_id"] == observation_id
    )


def _series_points(document: dict, observation_id: str) -> list[dict]:
    return _observation(document, observation_id)["values"]["points"]


def test_bischof_spike_links_render_fidelity_and_real_migration(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    documents = {
        sid: yaml.safe_load((EXTRACTS / f"{sid}.yaml").read_text())
        for sid in ("kems-137-bischof-2023", "kems-138-bischof-2023")
    }
    for sid, document in documents.items():
        assert check_all_fidelity_samples_match(document, label=sid) == []

    ga137 = _observation(
        documents["kems-137-bischof-2023"],
        "bischof_2023_ga_psat_s1_1low_polytherm",
    )
    in137 = _observation(
        documents["kems-137-bischof-2023"],
        "bischof_2023_in_psat_s1_1low_polytherm",
    )
    for row in (ga137, in137):
        assert row["experiment"] == "an-di-1-low"
        charge = row["values"]["initial_charge_as_published"]
        assert charge["role"] == "initial_charge_only"
        assert charge["nominal_base_wt_pct"] == {
            "SiO2": "50.34",
            "Al2O3": "15.40",
            "MgO": "10.80",
            "CaO": "23.46",
        }
        assert charge["spike_additions_ppm"] == {"Ga": "1000", "In": "1000", "Tl": "1000"}
        assert "mixed-basis" in charge["mixed_basis_refusal"]
    an_di_experiment = next(
        experiment
        for experiment in documents["kems-137-bischof-2023"]["experiments"]
        if experiment["experiment_id"] == "an-di-1-low"
    )
    liquid_form = an_di_experiment["sample"]["form"]
    assert liquid_form["locator"]["page"] == 1
    assert liquid_form["locator"]["section"] == "Abstract"
    assert liquid_form["locator"]["quote"] == "equilibrium partial pressures above the silicate liquid"
    assert len(_series_points(documents["kems-137-bischof-2023"], ga137["observation_id"])) == 11
    assert len(_series_points(documents["kems-137-bischof-2023"], in137["observation_id"])) == 11

    # These pins were read from the rendered Supplement Table S1, p. 2.
    assert _series_points(documents["kems-137-bischof-2023"], ga137["observation_id"])[0]["p_Pa"] == 4.04e-05
    assert _series_points(documents["kems-137-bischof-2023"], in137["observation_id"])[5]["p_Pa"] == 0.000123

    ga138 = _observation(documents["kems-138-bischof-2023"], "bischof_2023_psat_ga_1752k")
    in2o138 = _observation(documents["kems-138-bischof-2023"], "bischof_2023_table12_in2o_psat_this_work")
    for observation_id in (
        "bischof_2023_psat_ga2o_1752k",
        "bischof_2023_psat_ga_1752k",
        "bischof_2023_psat_gao_1752k",
    ):
        observation = _observation(documents["kems-138-bischof-2023"], observation_id)
        point = observation["values"]["points"][0]
        assert observation["locator"]["page"] == 8
        assert point["point_conditions"]["temperature_K"]["locator"]["page"] == 8
        assert point["run_link"]["locator"]["page"] == 8
    for observation_id in (
        "bischof_2023_psat_in2o_1611k",
        "bischof_2023_psat_in_1611k",
        "bischof_2023_psat_ino_1611k",
    ):
        observation = _observation(documents["kems-138-bischof-2023"], observation_id)
        point = observation["values"]["points"][0]
        assert observation["locator"]["page"] == 9
        assert point["point_conditions"]["temperature_K"]["locator"]["page"] == 9
        assert point["run_link"]["locator"]["page"] == 9
    assert _series_points(documents["kems-138-bischof-2023"], ga138["observation_id"])[0]["P_Pa"] == 0.11
    assert _series_points(documents["kems-138-bischof-2023"], in2o138["observation_id"])[1]["P_Pa"] == 0.327
    assert len(_series_points(documents["kems-138-bischof-2023"], ga138["observation_id"])) == 1
    assert len(_series_points(documents["kems-138-bischof-2023"], in2o138["observation_id"])) == 2

    expected_widths = {
        "bischof_2023_psat_ga2o_1752k": 0.3,
        "bischof_2023_psat_ga_1752k": 0.016,
        "bischof_2023_psat_gao_1752k": 0.002,
        "bischof_2023_psat_in2o_1611k": 0.52,
        "bischof_2023_psat_in_1611k": 0.05,
        "bischof_2023_psat_ino_1611k": 0.001,
    }
    for observation_id, width in expected_widths.items():
        parent = _observation(documents["kems-138-bischof-2023"], observation_id)
        assert parent["uncertainty"]["P_Pa"] == width
        assert parent["uncertainty"]["confidence_level"] == "not_stated"

    # Mutation evidence: an incorrect source point no longer matches its independent render pin.
    mutated = copy.deepcopy(documents["kems-138-bischof-2023"])
    _series_points(mutated, ga138["observation_id"])[0]["P_Pa"] = 0.12
    assert check_all_fidelity_samples_match(mutated, label="mutated-kems-138")

    migrated = {
        sid: _migrate_real_extract(tmp_path / sid, f"{sid}.yaml", write=True)
        for sid in documents
    }
    expected_counts = {"kems-137-bischof-2023": 22, "kems-138-bischof-2023": 10}
    target_rows = {}
    for sid, count in expected_counts.items():
        parent_ids = {
            "kems-137-bischof-2023": {
                "bischof_2023_ga_psat_s1_1low_polytherm",
                "bischof_2023_in_psat_s1_1low_polytherm",
            },
            "kems-138-bischof-2023": {
                "bischof_2023_psat_ga_1752k",
                "bischof_2023_psat_gao_1752k",
                "bischof_2023_psat_ga2o_1752k",
                "bischof_2023_psat_in_1611k",
                "bischof_2023_psat_ino_1611k",
                "bischof_2023_psat_in2o_1611k",
                "bischof_2023_table12_in_psat_this_work",
                "bischof_2023_table12_in2o_psat_this_work",
            },
        }[sid]
        rows = [
            row
            for row in migrated[sid].observations.values()
            if row.observation_id.split("::")[-2] in parent_ids
            and row.value.kind.value == "point"
        ]
        target_rows[sid] = rows
        assert len(rows) == count
        for row in rows:
            assert row.identity.quantity.is_value
            assert row.identity.quantity.value.value == "p_partial"
            assert row.identity.species.phase.is_value
            assert row.identity.species.phase.value.value == "g"
            assert row.identity.temperature_K is not None and row.identity.temperature_K.is_value
            assert row.point_conditions["temperature_K"].state.is_value
            assert row.point_conditions["total_pressure_Pa"].state.is_value
            assert row.identity.total_pressure_Pa is not None
            # d-399: the located "KC chamber about 10^-6 mbar" print (kems-137
            # p. 5; kems-138 p. 2) is an approximate point, so identity carries
            # a band, not unknown (t-1756 declared these rows unknown -> banded).
            #   10^-6 mbar x 100 Pa/mbar = 1e-4 Pa (the extract's point '0.0001').
            #   No interval is printed, so the band is half a decade each side:
            #   [1e-4 / sqrt(10), 1e-4 * sqrt(10)] = [3.1623e-5, 3.1623e-4] Pa.
            #   Check: high / low = 10 (one decade), low * high = point**2.
            assert row.identity.total_pressure_Pa.is_value
            band = row.identity.total_pressure_Pa.value
            assert isinstance(band, ApproxPressure)
            assert band.band_basis is BandBasis.HALF_DECADE_ORDER_OF_MAGNITUDE
            assert band.point == Decimal("0.0001")
            assert abs(band.low - Decimal("0.000031622776601683793319988935")) < Decimal("1e-30")
            assert abs(band.high - Decimal("0.00031622776601683793319988935")) < Decimal("1e-29")
            assert abs(band.high / band.low - 10) < Decimal("1e-25")
            assert row.point_conditions["composition"].state.is_unknown
            if sid == "kems-137-bischof-2023":
                temperature = row.identity.temperature_K.value
                printed_point = next(
                    point
                    for point in _series_points(
                        documents[sid], row.observation_id.split("::")[-2]
                    )
                    if Decimal(str(point["T_K"])) == temperature
                )
                delta_iw = Decimal(str(printed_point["delta_IW"]))
                control = row.point_conditions["fO2_control"]
                assert control.state.is_value
                assert control.state.value == (
                    f"IW buffer-relative; printed ΔIW={delta_iw}; "
                    "O’Neill and Pownceby, 1993"
                )
                # Supplement Table S1, p. 2, prints the row's ΔIW beside T;
                # e.g. "1586.4 ... ΔIW 1.66".
                assert control.locator.page == 2
                assert control.locator.table == "S1"
                relative_item = {
                    "delta_IW": str(delta_iw),
                    "oxygen_buffer_as_published": (
                        f"IW buffer-relative; printed ΔIW={delta_iw}; "
                        "O’Neill and Pownceby, 1993"
                    ),
                }
                # The conversion records the row's run pressure: the d-399 identity
                # point 1e-4 Pa = 1e-4 / 1e5 bar = 1E-9 bar. The O'Neill and Pownceby
                # 1993 equation is evaluated at its 1 bar reference with no pressure
                # correction, so the converted value is pressure-independent (the
                # 10 bar check below asserts the same state); only the notice text
                # records the pressure.
                run_pressure_bar = row.identity.total_pressure_Pa.value.point / Decimal(100000)
                assert run_pressure_bar == Decimal("1E-9")
                decision = printed_oxygen_decision(
                    PrintedOxygenFacts(),
                    raw_item=relative_item,
                    point_conditions=None,
                    scope="row",
                    run_temperature_K=temperature,
                    run_pressure_bar=run_pressure_bar,
                    source_id=sid,
                )
                assert decision is not None and decision.state.is_value
                if temperature == Decimal("1586.4"):
                    assert decision.notice_reason is not None
                    assert "oneill_pownceby_1993_iw_fe_feo" in decision.notice_reason
                    assert decision.state.value == Decimal(
                        "0.0001130368308746852372423477753"
                    )
                    pressure_decision = printed_oxygen_decision(
                        PrintedOxygenFacts(),
                        raw_item=relative_item,
                        point_conditions=None,
                        scope="row",
                        run_temperature_K=temperature,
                        run_pressure_bar=Decimal(10),
                        source_id=sid,
                    )
                    assert (
                        pressure_decision is not None
                        and pressure_decision.state == decision.state
                        and pressure_decision.notice_band == "±0.00493888 dex"
                        and pressure_decision.notice_reason is not None
                        and (
                            "band=±0.00493888 dex; "
                            "band_basis=O'Neill and Pownceby 1993 Fe-'FeO' "
                            "muO2 uncertainty; pressure=10 bar; "
                            "pressure correction=none; O'Neill and Pownceby "
                            "1993 equation is evaluated at its 1 bar reference"
                        )
                        in pressure_decision.notice_reason
                    )
                assert row.identity.fO2_Pa == decision.state
                conversion_notice = next(
                    notice
                    for notice in row.notices
                    if notice.reason.startswith("buffer_relative_fO2_converted:")
                )
                assert conversion_notice.kind.value == decision.notice_kind.value
                assert conversion_notice.reason == decision.notice_reason
                assert conversion_notice.band == decision.notice_band
            else:
                assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_unknown
            experiment = migrated[sid].experiments[row.experiment_id]
            assert rail_for_quantity(
                row.identity.quantity.value,
                species_formula=row.identity.species.formula,
            ) is Rail.VAPOUR
            phase_refusal = vapour_crystalline_sample_liquid_engine_refusal(
                row.identity,
                engine=Engine.INTERNAL_ANALYTICAL,
                experiment=experiment,
            )
            if sid == "kems-137-bischof-2023":
                assert phase_refusal is None
                assert row.point_conditions["temperature_K"].locator.page == 2
                assert row.point_conditions["total_pressure_Pa"].locator.page == 5
            else:
                assert phase_refusal is not None
                assert phase_refusal["reason"] == SAMPLE_PHASE_CRYSTALLINE_VS_LIQUID_ENGINE
                expected_page = (
                    17
                    if "table12" in row.observation_id
                    else 8
                    if "psat_ga" in row.observation_id
                    else 9
                )
                assert row.point_conditions["temperature_K"].locator.page == expected_page

    # The Table S1–S4 gamma rows must carry their cited buffer calibration
    # through migration, not only a synthetic citation passed to the resolver.
    migrated_137 = migrated["kems-137-bischof-2023"]
    gamma_parent_ids = {
        "bischof_2023_gao15_gamma_s1_1low_polytherm",
        "bischof_2023_gao15_gamma_s2_1low_isotherm",
        "bischof_2023_gao15_gamma_s4_3high_polytherm",
        "bischof_2023_gao15_gamma_s3_2low_polytherm",
        "bischof_2023_ino15_gamma_s1_1low_polytherm",
        "bischof_2023_ino15_gamma_s2_1low_isotherm",
        "bischof_2023_ino15_gamma_s4_3high_polytherm",
        "bischof_2023_ino15_gamma_s3_2low_polytherm",
    }
    expected_calibration = "oneill_pownceby_1993_iw_fe_feo"
    gamma_rows = [
        row
        for row in migrated_137.observations.values()
        if row.observation_id.split("::")[-2] in gamma_parent_ids
    ]
    assert len(gamma_rows) == 128
    for gamma_row in gamma_rows:
        assert gamma_row.identity.temperature_K is not None
        assert gamma_row.identity.temperature_K.is_value
        assert gamma_row.point_conditions["temperature_K"].state.is_value
        assert gamma_row.identity.fO2_Pa is not None
        assert gamma_row.identity.fO2_Pa.is_value
        assert any(
            expected_calibration in notice.reason
            for notice in gamma_row.notices
        )
    gamma_at_1741_9_delta_iw_1_51 = [
        row
        for row in gamma_rows
        if row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("1741.9")
        and row.identity.fO2_Pa is not None
        and row.identity.fO2_Pa.is_value
        and row.identity.fO2_Pa.value == Decimal("0.002752615375078678784221966689")
    ]
    assert {row.identity.species.formula for row in gamma_at_1741_9_delta_iw_1_51} == {
        "Ga",
        "In",
    }
    assert {
        row.observation_id.split("::")[-2]
        for row in gamma_at_1741_9_delta_iw_1_51
    } == {
        "bischof_2023_gao15_gamma_s2_1low_isotherm",
        "bischof_2023_ino15_gamma_s2_1low_isotherm",
    }
    for row in gamma_at_1741_9_delta_iw_1_51:
        assert any(
            expected_calibration in notice.reason
            and "predict_and_flag_outside_range_833_1644_K" in notice.reason
            for notice in row.notices
        )
    gamma_at_1586 = [
        row
        for row in migrated_137.observations.values()
        if row.observation_id.split("::")[-2] in gamma_parent_ids
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("1586.4")
    ]
    psat_at_1586 = [
        row
        for row in migrated_137.observations.values()
        if row.observation_id.split("::")[-2]
        in {
            "bischof_2023_ga_psat_s1_1low_polytherm",
            "bischof_2023_in_psat_s1_1low_polytherm",
        }
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("1586.4")
    ]
    assert len(gamma_at_1586) == 2
    assert len(psat_at_1586) == 2
    for gamma_row in gamma_at_1586:
        assert gamma_row.identity.fO2_Pa is not None
        assert gamma_row.identity.fO2_Pa.is_value
        assert any(
            expected_calibration in notice.reason
            for notice in gamma_row.notices
        )
        matching_psat = next(
            row
            for row in psat_at_1586
            if row.identity.species.formula == gamma_row.identity.species.formula
        )
        assert matching_psat.identity.fO2_Pa == gamma_row.identity.fO2_Pa
        assert any(
            expected_calibration in notice.reason
            for notice in matching_psat.notices
        )

    bare_iw = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"oxygen_buffer_as_published": "ΔIW"},
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal("1586.4"),
        run_pressure_bar=Decimal(1),
        source_id="kems-137-bischof-2023",
    )
    assert bare_iw is not None and bare_iw.state.is_unknown

    by_parent_temp = {
        (row.observation_id.split("::")[-2], row.identity.temperature_K.value): row
        for rows in target_rows.values()
        for row in rows
    }
    assert by_parent_temp[(ga137["observation_id"], Decimal("1586.4"))].value.point == Decimal("0.0000404")
    assert by_parent_temp[(in137["observation_id"], Decimal("1642.5"))].value.point == Decimal("0.000123")
    assert by_parent_temp[(ga138["observation_id"], Decimal("1752.0"))].value.point == Decimal("0.11")
    assert by_parent_temp[(in2o138["observation_id"], Decimal("1550.0"))].value.point == Decimal("0.327")

    uncertainty_counts = {
        sid: Counter(
            (
                row.uncertainty.kind.value,
                (row.uncertainty.verbatim or {}).get("confidence_level", "not_stated"),
            )
            for row in rows
        )
        for sid, rows in target_rows.items()
    }
    assert uncertainty_counts["kems-137-bischof-2023"] == Counter({("none", "not_stated"): 22})
    assert uncertainty_counts["kems-138-bischof-2023"] == Counter({("printed", "not_stated"): 10})
