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

from simulator.battery.enums import EvidenceClass
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
    ("ammin-76-904-lange-1991.yaml", "ammin_76_904_quoted_comparator_enthalpy_values"): "Stebbins et al. (1983); Richet and Bottinga (1984); Ferrier (1968); Stebbins et al. (1984); Adamkovicova et al. (1980)",
    ("hashimoto-nakano-2021-bubbles-to-chondrites-ii.yaml", "hashimoto_2021_appendix_sample_provenance"): "Jarosewich (1990)",
    ("lpi-compendium-15013.yaml", "lpi_15013_maturity_and_grain_size"): "Morris 1978 (for Is/FeO = 77)",
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
