from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import NoticeKind, Phase
from simulator.battery.migrate import (
    Migrator,
    _printed_fO2_identity_state,
    collect_printed_oxygen,
)
from tests.literature_extract_test_helpers import assert_all_physical_locators
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document, species, observation_id):
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _printed_interval(value, *, scale=Decimal("1")):
    printed = Decimal(str(value))
    half_last_place = Decimal(1).scaleb(printed.as_tuple().exponent) / 2
    return (
        (printed - half_last_place) * scale,
        (printed + half_last_place) * scale,
    )


def test_activity_tables_and_appendix_compositions_are_typed_and_located():
    document = _document()
    assert_all_physical_locators(document)

    context = document["species"]["CrO"]["context"]
    table_ii = next(row for row in context if row["observation_id"].endswith("table_2_context"))
    table_iii = next(row for row in context if row["observation_id"].endswith("table_3_context"))
    assert "printed_rows" not in table_ii["values"]
    assert "printed_rows" not in table_iii["values"]
    assert table_ii["locator"]["source_path"] == (
        "tables/pretorius-muan-1992-chromium-oxide-activities/cited-pages.txt"
    )
    assert table_ii["locator"]["page"] == 3
    assert table_ii["locator"]["published_page"] == 1366
    assert table_iii["locator"]["page"] == 7
    assert table_iii["locator"]["published_page"] == 1370

    observations = document["species"]["CrO"]["observations"]
    assert len(observations) == 386
    activity_rows = [
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row.get("values", {}).get("quantity") == "activity"
    ]
    assert len(activity_rows) == 135
    assert all(
        row["values"]["method_class"] == "model_derived"
        and (
            row["locator"]["page"],
            row["locator"]["published_page"],
            row["locator"]["table"],
        )
        in {(3, 1366, "II"), (7, 1370, "III")}
        for row in activity_rows
    )
    mutated = deepcopy(document)
    mutated_activity_rows = [
        row
        for species in mutated["species"].values()
        for row in species["observations"]
        if row.get("values", {}).get("quantity") == "activity"
    ]
    mutated_activity_rows[0]["values"]["method_class"] = "measured_direct"
    assert not all(
        row["values"]["method_class"] == "model_derived"
        for row in mutated_activity_rows
    )
    activity = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_activity",
    )
    mole_fraction = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_mole_fraction",
    )
    gamma = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma",
    )
    assert activity["values"]["activity"] == "0.245"
    assert activity["T_C"] == "1500"
    assert activity["point_conditions"]["fO2_log"]["state"]["value"] == "-9.56"
    assert mole_fraction["values"]["value"] == "0.0126"
    assert mole_fraction["values"]["value_as_published"] == "1.26"
    assert gamma["values"]["gamma"] == "19.44"
    assert gamma["values"]["method_class"] == "measured_reduced"
    assert gamma["derived_from"] == [activity["observation_id"], mole_fraction["observation_id"]]
    assert gamma["locator"]["page"] == 3
    assert gamma["locator"]["published_page"] == 1366

    table_iv = next(
        row for row in observations if row["observation_id"].endswith("tIV_cacrs_4_cro_alloy")
    )
    assert table_iv["values"]["method_class"] == "model_derived"
    assert table_iv["locator"]["page"] == 8
    assert table_iv["locator"]["published_page"] == 1371

    appendix = [
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row["locator"].get("appendix") in {"Appendix A", "Appendix C"}
    ]
    assert len(appendix) == 285
    appendix_table_by_page = {
        10: r"Appendix A EMPA of Equilibrium Phases in the System CaO-CrO $_{x}$ -SiO $_{2}$ \*",
        11: "Appendix A (continued)",
        12: r"Appendix C EMPA of Equilibrium Phases in the System $\mathrm{CaO - Al_2O_3 - CrO_x - SiO_2^*}$",
        13: "Appendix C (continued)",
    }
    assert all(
        row["locator"]["table"] == appendix_table_by_page[row["locator"]["page"]]
        for row in appendix
    )
    glass = next(
        row
        for row in appendix
        if row["locator"].get("appendix") == "Appendix C"
        and row["locator"].get("row", "").startswith("CACrS 4, Glass, SiO2")
    )
    assert glass["values"]["value_as_published"] == "54.24(0.18)"
    assert glass["uncertainty"]["type"] == "standard_deviation"

    sweep_quote = next(
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row["locator"].get("row") == "CACrS 16, Glass, Al2O3"
    )
    assert sweep_quote["values"]["value"] == "19.50"
    assert sweep_quote["T_C"] == "1502"
    assert sweep_quote["locator"]["page"] == 12
    assert sweep_quote["locator"]["published_page"] == 1375


def test_isolated_migration_has_no_hard_issues_and_reaches_temperature_and_fugacity():
    document = _document()
    source_observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 704
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(
        observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        for observation in result.observations.values()
    )
    converted_fo2_observations = []
    for observation in result.observations.values():
        assert observation.point_conditions is not None
        for name in ("temperature_C", "fO2_log"):
            condition = observation.point_conditions[name]
            assert condition.state.is_value
            assert condition.locator is not None
        source = source_observations[observation.observation_id.split("::", 1)[1]]
        printed_fo2 = source["point_conditions"]["fO2_log"]
        assert printed_fo2["units"] == "log10(bar)"
        assert printed_fo2["locator"]["column"] == "log pO2"
        assert observation.point_conditions["fO2_log"].state.value == Decimal(
            printed_fo2["state"]["value"]
        )
        facts = collect_printed_oxygen(
            ({"point_conditions": {"fO2_log": printed_fo2}},),
            observation.point_conditions["fO2_log"].locator,
        )
        if (
            observation.identity.fO2_Pa is not None
            and observation.identity.fO2_Pa.is_value
        ):
            assert _printed_fO2_identity_state(facts) == observation.identity.fO2_Pa
            # Premise: the article's page 1 definition is 1 atm = 1 × 10^5 Pa.
            # Algebra: pO2 = 10^(−9.56) atm × 10^5 Pa/atm.
            # Unit check: the printed atm cancels, leaving Pa.
            # Sanity: the result is 2.7542 × 10^-5 Pa, below 1 Pa as −9.56 implies.
            log_value = Decimal(printed_fo2["state"]["value"])
            assert observation.identity.fO2_Pa.value == (
                Decimal(10) ** log_value * Decimal("100000")
            )
            converted_fo2_observations.append(observation)
    assert converted_fo2_observations

    ccrs1 = result.observations[
        "pretorius-muan-1992-chromium-oxide-activities::"
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma"
    ]
    assert ccrs1.identity.fO2_Pa is not None and ccrs1.identity.fO2_Pa.is_value
    # The reference product is quoted to 17 significant digits; Decimal's
    # power operation retains its additional working precision.
    assert abs(
        ccrs1.identity.fO2_Pa.value - Decimal("2.7542287033381663E-5")
    ) < Decimal("2E-21")
    ccrs1_source = source_observations[
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma"
    ]
    mutated_log = deepcopy(ccrs1_source["point_conditions"]["fO2_log"])
    mutated_log["units"] = "log10(atm)"
    wrong_atm_state = _printed_fO2_identity_state(
        collect_printed_oxygen(
            ({"point_conditions": {"fO2_log": mutated_log}},),
            ccrs1.point_conditions["fO2_log"].locator,
        )
    )
    assert wrong_atm_state is not None and wrong_atm_state.is_value
    assert wrong_atm_state.value != ccrs1.identity.fO2_Pa.value
    metal = result.observations[
        "pretorius-muan-1992-chromium-oxide-activities::"
        "pretorius_muan_1992_chromium_oxide_activities_appendixa_ccrs_12_metal_cr"
    ]
    assert metal.identity.species.phase.is_unknown
    assert metal.identity.species.phase.reason == (
        "phase string 'Metal' is not in the closed automatic map"
    )
    prefix = "pretorius-muan-1992-chromium-oxide-activities::pretorius_muan_1992_chromium_oxide_activities_"
    formulas = {
        row.identity.species.formula for row in result.observations.values()
    }
    assert formulas >= {"CrO", "CrO1.5"}

    observations = [
        row
        for species in document["species"].values()
        for row in species["observations"]
    ]
    by_id = {row["observation_id"]: row for row in observations}
    gamma_rows = [
        row
        for row in observations
        if row.get("values", {}).get("method_class") == "measured_reduced"
        and row.get("values", {}).get("quantity") == "activity_coefficient"
    ]
    assert len(gamma_rows) == 102
    failures = []
    for gamma in gamma_rows:
        stem = gamma["observation_id"].removesuffix("_gamma")
        activity = by_id[f"{stem}_activity"]
        mole_fraction = by_id[f"{stem}_mole_fraction"]
        activity_value = activity["values"].get(
            "activity_as_published", activity["values"]["activity"]
        )
        mole_fraction_value = mole_fraction["values"]["value_as_published"]
        scale_exponent = int(mole_fraction["values"]["scale_factor"]["exponent"])
        scale = Decimal(1).scaleb(scale_exponent)
        activity_interval = _printed_interval(activity_value)
        mole_fraction_interval = _printed_interval(mole_fraction_value, scale=scale)
        gamma_interval = _printed_interval(gamma["values"]["gamma_as_published"])
        product_interval = (
            gamma_interval[0] * mole_fraction_interval[0],
            gamma_interval[1] * mole_fraction_interval[1],
        )
        if max(activity_interval[0], product_interval[0]) > min(
            activity_interval[1], product_interval[1]
        ):
            failures.append((gamma, activity, mole_fraction))

    assert [row[0]["observation_id"] for row in failures] == [
        "pretorius_muan_1992_chromium_oxide_activities_tii_r06_cro1_5_gamma"
    ]
    gamma, activity, mole_fraction = failures[0]
    assert activity["values"]["activity"] == "0.054"
    assert mole_fraction["values"]["value"] == "0.0005"
    assert mole_fraction["values"]["value_as_published"] == "0.05"
    assert gamma["values"]["gamma"] == "11.08"
    assert gamma["derived_from"] == [
        activity["observation_id"],
        mole_fraction["observation_id"],
    ]
    assert "central values do not satisfy" in gamma["derivation"]["relation"]
    assert "remains as printed without recalculation" in gamma["derivation"]["relation"]
    assert gamma["reason"].startswith("source_internally_inconsistent:")
    ratio = Decimal(activity["values"]["activity"]) / Decimal(
        mole_fraction["values"]["value"]
    )
    discrepancy_dex = (ratio / Decimal(gamma["values"]["gamma"])).log10()
    assert ratio == Decimal("108")
    assert abs(discrepancy_dex - Decimal("0.989")) < Decimal("0.001")

    native = result.observations[
        f"{document['source_id']}::{gamma['observation_id']}"
    ]
    notice = next(
        notice
        for notice in native.notices
        if notice.kind is NoticeKind.SOURCE_DISAGREEMENT
    )
    assert notice.reason == gamma["reason"]
    by_formula = {}
    for row in result.observations.values():
        by_formula.setdefault(row.identity.species.formula, 0)
        by_formula[row.identity.species.formula] += 1
    assert by_formula["CrO1.5"] == 193
    assert by_formula["CrO"] == 193
    expected_formula = {
        "tii_r01_cro1_5_activity": "CrO1.5",
        "tii_r01_cro1_5_mole_fraction": "CrO1.5",
        "tii_r01_cro1_5_gamma": "CrO1.5",
        "tii_r01_cro_activity": "CrO",
        "tii_r01_cro_mole_fraction": "CrO",
        "tii_r01_cro_gamma": "CrO",
        "tiii_r01_cro1_5_activity": "CrO1.5",
        "tiii_r01_cro_activity": "CrO",
        "tIV_cacrs_4_cro1_5_alloy": "CrO1.5",
        "tIV_cacrs_4_cro_alloy": "CrO",
    }
    for suffix, formula in expected_formula.items():
        assert result.observations[prefix + suffix].identity.species.formula == formula
    sesquioxide = result.observations[
        "pretorius-muan-1992-chromium-oxide-activities::"
        "pretorius_muan_1992_chromium_oxide_activities_appendixc_cacrs_4_ses_al2o3"
    ]
    assert sesquioxide.identity.species.phase.value is Phase.CR


def test_fidelity_pin_detects_a_mutated_gamma_cell():
    document = _document()
    assert check_all_fidelity_samples_match(document) == []

    mutated = deepcopy(document)
    gamma = _observation(
        mutated,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma",
    )
    gamma["values"]["gamma"] = "19.45"
    assert check_all_fidelity_samples_match(mutated)


def test_all_fourteen_figures_have_deferred_inventory_and_physical_locators():
    document = _document()
    figures = [
        row
        for row in document["species"]["CrO"]["context"]
        if row.get("type") == "figure_only"
    ]

    assert len(figures) == 14
    assert {row["locator"]["figure"] for row in figures} == {
        str(number) for number in range(1, 15)
    }
    for figure in figures:
        values = figure["values"]
        assert values["typed_quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["x_axis_as_printed"]
        assert values["y_axis_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["series"]
        assert all(isinstance(series["marker_count"], int) for series in values["series"])
