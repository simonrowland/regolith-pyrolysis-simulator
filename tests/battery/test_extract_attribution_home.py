"""PIN (t-1117, ruling #17): one home for a quoted source's name, ``values.attribution``.

Option (b): the extracts are renamed; the reader keeps reading ``values.attribution``
only (no alias chain). These pins are red on ``work-v064-green`` 8089eadbf:

* no species observation ``values`` block may carry ``source_attribution`` or
  ``quoted_from`` (74 such blocks in 17 extracts at green);
* names that sat under those keys reach ``Evidence.attribution`` through the real
  migrator (class flips quoted_unattributed -> quoted_attributed, list values joined
  with "; ");
* the page-repaired records (t-1117 section 1, Tachibana 1998 and the Stebbins 1983
  Table 6/7 quotes) carry the name the page prints, and no extract the migrator walks
  leaves a ``quoted_attributed`` record without one (every ``extracts/*.yaml``, so a
  later extract of that shape fails here, not only the files this ruling touched).

Row-level keys are out of scope and must be left alone (Schaefer & Fegley 2011 context
prose and per-row footnote attributions).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, NoticeKind, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.test_migrate import _extract_observations, _migrate_real_extract

EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"
ALIASES = ("source_attribution", "quoted_from")

# Renamed by tools/rename_values_attribution.py: one flip per shape, one fill, one list join.
RENAMED = {
    ("kems-023-demaria-1973.yaml", "demaria_1973_fe_psat_table1_quoted_demaria_1971"): "De Maria et al. 1971 (ref 4)",
    ("schaefer-fegley-2011-vaporization-earth.yaml", "schaefer_2012_table2_bse_bulk_composition"): "Kargel_and_Lewis_1993",
    ("cooper-2007-sintering-lunar-simulant.yaml", "cooper_2007_jsc1a_sintering_1100c_quoted"): "Allen et al. (1992a)",
    ("2010zahnle-schaefer-fegley-cshperspect-ori-a004895-2019.yaml", "zahnle_2010_impact_degas_velocity_threshold"): "Lange et al. (1985); Tyburczy et al. (1986)",
}

# Written from the page (or the page's reference list); see the t-1117 rename report.
REPAIRED = {
    ("jsc-lunar-catalog-12023.yaml", "jsc_12023_carbon_nitrogen_compilation"): "Moore et al. (1971); Kaplan and Petrowski (1971); Kerridge et al. (1978); Norris et al. (1983)",
    ("ammin-76-904.yaml", "ammin_76_904_quoted_comparator_enthalpy_values"): "Stebbins et al. (1983)",
    ("hashimoto-nakano-2021-bubbles-to-chondrites-ii.yaml", "hashimoto_2021_appendix_sample_provenance"): "Jarosewich (1990)",
    ("lpi-compendium-15013.yaml", "lpi_15013_carbon_and_nitrogen"): "Moore et al. 1973; Des Marais et al. 1973; Kothari and Goel 1972; Muller 1973",
    ("lpi-compendium-65701.yaml", "lpi_compendium_65701_attributed_trace_and_exposure_values"): "Moore et al. (1973); Kerridge et al. (1975); Cirlin and Housley (1981); Wrigley (1973); Walton et al. (1973); Graf (1993), from data by Butler et al. (1973)",
    ("itoh-hino-banya-1998-spinel.yaml", "itoh_1998_quoted_prior_interaction_parameters"): "Mizin et al. (1983, ref 10) for e_Mg_Al_1873K; Sponseller and Flinn (1964, ref 11) for e_Ca_Al_1873K",
    ("tachibana-tsuchiyama-1998-forsterite-dust-lpsc.yaml", "tachibana_1998_forsterite_prior_experiment_alpha_constraint"): "experiments [1-4]: Hashimoto (1990) [1]; Wang et al. (1993) [2]; Nagahara and Ozawa (1996) [3]; Tsuchiyama et al., in preparation [4]",
    # Stebbins, Carmichael & Weill 1983, printed p. 723: the Table 6 row labels and the Table 7
    # anorthite footnotes 3-4, hoisted in table order from the nested per-estimate sources.
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_diopside_table_6_vitrification_quotes"): "Weill et al. (1980a); Navrotsky and Coons (1976); Ferrier (1968a); Tamman (1903)",
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_albite_table_6_vitrification_quotes"): "Weill et al. (1980a); Waldbaum and Robie (1971); Hlabse and Kleppa (1968), Holm and Kleppa (1968); Kracek and Neuvonen (1952)",
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_sanidine_table_6_vitrification_quotes"): "Waldbaum and Robie (1971); Tamman (1903)",
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_nepheline_table_6_vitrification_quotes"): "Navrotsky et al. (1980)",
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_anorthite_table_7_fusion_quotes"): "Weill et al. (1980b); Robie et al. (1978)",
}

CONTEXT_REPAIRED = {
    ("lpi-compendium-15013.yaml", "lpi_15013_maturity_and_grain_size"): "Morris 1978 (for Is/FeO = 77)",
}

TURKDOGAN_REF2 = (
    "kems-048-turkdogan-2001-sio2-gamma.yaml",
    "turkdogan_2001_p2o5_about_minus_15_to_18_quoted",
    "E. T. Turkdogan: ISIJ Int., 40 (2000), 964 (ref 2; the author's recent publication)",
)

ALL_EXTRACTS = sorted(path.name for path in EXTRACTS.glob("*.yaml"))


def _extract_values(name: str, raw_id: str) -> dict:
    for obs in _extract_observations(name):
        if obs.get("observation_id") == raw_id:
            return obs["values"]
    raise AssertionError(f"{raw_id} not found in {name}")


def _migrated(result, raw_id: str) -> list:
    found = [o for oid, o in result.observations.items() if oid.split("::")[1] == raw_id]
    assert found, f"no migrated records for {raw_id}"
    return found


def test_no_values_level_alias_attribution_keys_in_extracts() -> None:
    offenders = [
        (path.name, obs.get("observation_id"), key)
        for path in sorted(EXTRACTS.glob("*.yaml"))
        if any(alias in path.read_text(encoding="utf-8") for alias in ALIASES)
        for obs in _extract_observations(path.name)
        if isinstance(obs.get("values"), dict)
        for key in ALIASES
        if key in obs["values"]
    ]
    assert offenders == []


def test_row_level_attribution_keys_are_left_alone() -> None:
    doc = yaml.safe_load((EXTRACTS / "schaefer-fegley-2011-vaporization-earth.yaml").read_text(encoding="utf-8"))
    table1 = next(
        row
        for row in doc["species"]["planetary_context"]["context"]
        if row["observation_id"] == "schaefer_2012_table1_transiting_low_mass_planets"
    )
    assert table1["values"]["source_attribution"].startswith("Per-row footnote attribution below")
    assert "attribution" not in table1["values"]
    assert all("source_attribution" in row for row in table1["values"]["rows"])


@pytest.mark.parametrize(("key", "expected"), sorted(RENAMED.items()), ids=lambda v: v[1] if isinstance(v, tuple) else None)
def test_renamed_name_reaches_evidence_attribution(tmp_path: Path, key, expected: str) -> None:
    name, raw_id = key
    assert _extract_values(name, raw_id).get("attribution") == expected
    for obs in _migrated(_migrate_real_extract(tmp_path, name), raw_id):
        assert obs.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert obs.evidence.attribution == expected


@pytest.mark.parametrize(("key", "expected"), sorted(REPAIRED.items()), ids=lambda v: v[1] if isinstance(v, tuple) else None)
def test_page_repaired_name_reaches_evidence_attribution(tmp_path: Path, key, expected: str) -> None:
    name, raw_id = key
    assert _extract_values(name, raw_id).get("attribution") == expected
    for obs in _migrated(_migrate_real_extract(tmp_path, name), raw_id):
        assert obs.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert obs.evidence.attribution == expected


@pytest.mark.parametrize(
    ("key", "expected"),
    sorted(CONTEXT_REPAIRED.items()),
    ids=lambda value: value[1] if isinstance(value, tuple) else None,
)
def test_page_repaired_name_survives_on_context_record(
    tmp_path: Path, key, expected: str
) -> None:
    name, raw_id = key
    path = EXTRACTS / name
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = [
        row
        for block in doc["species"].values()
        for row in block.get("context", [])
        if row.get("observation_id") == raw_id
    ]
    assert len(rows) == 1
    assert rows[0]["values"]["attribution"] == expected

    migrated = _migrate_real_extract(tmp_path, name)
    migrated_rows = [
        row
        for context_rows in migrated.context_by_work.values()
        for row in context_rows
        if row.get("observation_id") == raw_id
    ]
    assert len(migrated_rows) == 1
    assert migrated_rows[0]["values"]["attribution"] == expected


def test_turkdogan_self_citation_names_reference_2() -> None:
    name, raw_id, expected = TURKDOGAN_REF2
    assert _extract_values(name, raw_id).get("attribution") == expected


@pytest.mark.parametrize("name", ALL_EXTRACTS)
def test_no_quoted_attributed_record_without_attribution(tmp_path: Path, name: str) -> None:
    result = _migrate_real_extract(tmp_path, name)
    missing = [
        issue.path
        for issue in result.validation.hard_issues
        if issue.detail == "quoted_attributed requires attribution"
    ]
    assert missing == []


def test_lange_split_comparator_quotes_migrate_with_their_typed_payloads(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "ammin-76-904.yaml")
    expected = {
        "ammin_76_904_quoted_comparator_enthalpy_values": (
            "CaMgSi2O6",
            Quantity.ENTHALPY_OF_FUSION,
            ValueKind.UNAVAILABLE,
            "enthalpy_of_fusion_kJ_per_mol",
            138.1,  # Printed p. 909 / PDF p. 6: Stebbins et al. (1983), diopside.
            "kJ_per_mol",
            "Stebbins et al. (1983)",
            909,
            6,
            1665,
        ),
        "ammin_76_904_quoted_richet_bottinga_diopside_fusion": (
            "CaMgSi2O6",
            Quantity.ENTHALPY_OF_FUSION,
            ValueKind.UNAVAILABLE,
            "enthalpy_of_fusion_kJ_per_mol",
            137.7,  # Printed p. 909 / PDF p. 6: Richet and Bottinga (1984), diopside.
            "kJ_per_mol",
            "Richet and Bottinga (1984)",
            909,
            6,
            1665,
        ),
        "ammin_76_904_quoted_ferrier_diopside_fusion": (
            "CaMgSi2O6",
            Quantity.ENTHALPY_OF_FUSION,
            ValueKind.UNAVAILABLE,
            "enthalpy_of_fusion_kJ_per_mol",
            128,  # Printed p. 909 / PDF p. 6: Ferrier (1968), diopside.
            "kJ_per_mol",
            "Ferrier (1968)",
            909,
            6,
            1665,
        ),
        "ammin_76_904_quoted_richet_bottinga_liquid_heat_capacity": (
            "CaMgSi2O6",
            Quantity.CP,
            ValueKind.POINT,
            "cp",
            334,  # Printed p. 909 / PDF p. 6: Richet and Bottinga (1984).
            "J/mol K",
            "Richet and Bottinga (1984)",
            909,
            6,
            None,
        ),
        "ammin_76_904_quoted_stebbins_1984_liquid_heat_capacity": (
            "CaMgSi2O6",
            Quantity.CP,
            ValueKind.POINT,
            "cp",
            353,  # Printed p. 909 / PDF p. 6: Stebbins et al. (1984).
            "J/mol K",
            "Stebbins et al. (1984)",
            909,
            6,
            None,
        ),
        "ammin_76_904_quoted_adamkovicova_wollastonite_fusion": (
            "CaSiO3",
            Quantity.ENTHALPY_OF_FUSION,
            ValueKind.UNAVAILABLE,
            "enthalpy_of_fusion_kJ_per_mol",
            57.3,  # Printed p. 911 / PDF p. 8: Adamkovicova et al. (1980), wollastonite.
            "kJ_per_mol",
            "Adamkovicova et al. (1980)",
            911,
            8,
            1821,
        ),
    }

    for raw_id, (
        species_formula,
        quantity,
        value_kind,
        raw_value_key,
        raw_value,
        units,
        attribution,
        published_page,
        pdf_page,
        fusion_temperature,
    ) in expected.items():
        raw = next(
            obs
            for obs in _extract_observations("ammin-76-904.yaml")
            if obs.get("observation_id") == raw_id
        )
        assert raw["units"] == units
        assert raw["values"][raw_value_key] == raw_value
        assert raw["values"]["attribution"] == attribution
        assert raw["locator"]["published_page"] == published_page
        assert raw["locator"]["pdf_page"] == pdf_page
        if fusion_temperature is not None:
            assert raw["values"]["fusion_temperature_K"] == fusion_temperature

        observation = result.observations[f"ammin-76-904::{raw_id}"]
        actual_quantity = observation.identity.quantity
        assert actual_quantity.is_value is (quantity is not None)
        if quantity is None:
            assert "unsupported quantity 'heat_of_fusion'" in actual_quantity.reason
        else:
            assert actual_quantity.value is quantity
        assert observation.value.kind is value_kind
        assert observation.identity.species.formula == species_formula
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert observation.evidence.attribution == attribution
        if quantity is Quantity.CP:
            assert observation.value.point == raw_value
        else:
            assert observation.value.kind is ValueKind.UNAVAILABLE


def test_lange_quoted_cp_points_reach_scorer_with_notice(tmp_path: Path) -> None:
    from simulator.battery.score import (
        SCORE_ENGINE_SET,
        ScoreContext,
        comparison_candidates,
        headline_rows,
        score_store,
    )

    result = _migrate_real_extract(tmp_path, "ammin-76-904.yaml")
    quote_ids = {
        f"ammin-76-904::{raw_id}"
        for raw_id in (
            "ammin_76_904_quoted_comparator_enthalpy_values",
            "ammin_76_904_quoted_richet_bottinga_diopside_fusion",
            "ammin_76_904_quoted_ferrier_diopside_fusion",
            "ammin_76_904_quoted_richet_bottinga_liquid_heat_capacity",
            "ammin_76_904_quoted_stebbins_1984_liquid_heat_capacity",
            "ammin_76_904_quoted_adamkovicova_wollastonite_fusion",
        )
    }
    observations = {
        observation_id: result.observations[observation_id]
        for observation_id in quote_ids
    }
    context = ScoreContext(result.works, result.experiments, observations)

    available_cp = [
        observation
        for observation in observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.CP
        and observation.value.kind is ValueKind.POINT
    ]
    assert sorted(observation.value.point for observation in available_cp) == [334, 353]
    assert {row.observation_id for row in comparison_candidates(context)} == quote_ids

    residuals, _ = score_store(
        context,
        engines=SCORE_ENGINE_SET,
    )
    assert len(residuals) == len(quote_ids) * len(SCORE_ENGINE_SET)
    assert all(row.numeric is not None or row.refusal is not None for row in residuals)
    assert all(
        any(
            notice.kind is NoticeKind.FIGURE_ONLY
            and notice.reason == "evidence_class:quoted_attributed"
            for notice in row.notices
        )
        for row in residuals
    )
    assert all(
        row["n"] == 0
        for row in headline_rows(
            residuals,
            context=context,
            tier="measured",
            engines=SCORE_ENGINE_SET,
        )
    )
