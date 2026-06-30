"""Guard calibrated evaporation-alpha values and provenance labels."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"

EXPECTED_ALPHA = {
    ("metals", "Fe"): {
        "value": 0.02,
        "envelope": (0.011, 0.020),
        "source": (
            "REF-016 Costa & Jacobson 2015 KEMS Fo93Fa7 olivine, Fe+ "
            "alpha=0.011-0.020 at 1700-1800 K; Ebel 2005 calculated "
            "Fe/FeO alpha~0.2 noted as non-measured high-side proxy"
        ),
        "tier": 2,
    },
    ("metals", "Mg"): {
        "value": 0.20,
        "envelope": (0.10, 0.21),
        "source": (
            "REF-015 REF-018 Richter et al. 2002 Mg/SiO alpha~0.1-0.2 in vacuum at "
            "1800 C; SF2004 Table 10 Mg2SiO4(l), Hashimoto 1990, "
            "alpha_s=0.20-0.21"
        ),
        "tier": 2,
    },
    ("metals", "Na"): {
        "value": 1.0,
        "envelope": (0.9, 1.0),
        "source": (
            "OWNER-RATIFY source_class=open_furnace_apparent_not_intrinsic: "
            "REF-013 Sossi et al. 2019 GCA 260:204, Na alpha_e~1 near-ideal "
            "open-furnace evaporation from ferrobasalt FCMAS melt; retained "
            "pending owner ratification against competing Fedkin intrinsic 0.13"
        ),
        "tier": 2,
    },
    ("metals", "K"): {
        "value": 0.13,
        "envelope": (0.10, 0.16),
        "source": (
            "REF-014 Fedkin et al. 2006 LPSC 37:#2249 KEMS sealed-chamber "
            "intrinsic K alpha_e~0.13; replaces prior Na open-furnace analogy "
            "for series-resistance intrinsic-alpha model"
        ),
        "tier": 2,
    },
    ("metals", "Ca"): {
        "value": 0.90,
        "envelope": (0.48, 1.20),
        "source": (
            "OWNER-RATIFY proxy_not_intrinsic: Zhang et al. 2014 GCA "
            "140:365-380 CaTiO3 melt at 2005 C; Ca activity proxy"
        ),
        "tier": 2,
    },
    ("metals", "Al"): {
        "value": 0.30,
        "envelope": (0.03, 1.00),
        "source": (
            "OWNER-RATIFY proxy_not_intrinsic: REF-018 Schaefer & Fegley "
            "2004 Icarus 169:216-241 Table 10 plus Shahar & Young 2007 "
            "CAI modeling; conflicting Al proxy coverage"
        ),
        "tier": 2,
    },
    ("metals", "Si"): {
        "value": 1.0,
        "envelope": (0.84, 1.00),
        "source": (
            "REF-017 Safarian & Engh 2013 Metall. Mater. Trans. A 44:747-753 "
            "pure-Si vacuum evaporation; pure elemental Si branch only"
        ),
        "tier": 2,
    },
    ("metals", "Ti"): {
        "value": 0.80,
        "envelope": (0.39, 1.00),
        "source": (
            "OWNER-RATIFY proxy_not_intrinsic: Zhang et al. 2014 GCA "
            "140:365-380 CaTiO3 melt at 2005 C; Ti activity proxy"
        ),
        "tier": 2,
    },
    ("oxide_vapors", "SiO"): {
        "value": {
            "form": "arrhenius",
            "A": 0.52,
            "B": 3685.0,
            "valid_range_K": (1000, 1800),
            "prior_scalar": 0.04,
        },
        "envelope": (0.003, 0.067),
        "source": (
            "Wetzel & Gail 2013 A&A 553 A92 Arrhenius compilation "
            "alpha_s_SiO(T)=0.52*exp(-3685/T), reaction-rate-limited "
            "SiO evaporation coefficient. HOT evaporation interface uses "
            "alpha_s(T) at source T; microscopic reversibility applies there. "
            "COLD-WALL condensation below valid_range_K floor uses the grounded "
            "Pound 1972 JPCRD 1:135 DOI 10.1063/1.3253096 unity condensation "
            "coefficient; alpha_e != alpha_c off-equilibrium at high "
            "supersaturation."
        ),
        "tier": 2,
    },
}

EXPECTED_OWNER_RATIFY_ALPHA = {
    ("metals", "Na"),
    ("metals", "Ca"),
    ("metals", "Al"),
    ("metals", "Ti"),
    ("foulant_vapor", "NaCl"),
    ("foulant_vapor", "KCl"),
}

EXPECTED_MISSING_ALPHA_POLICY = {
    ("metals", "Cr"): "Fedkin et al. 2006",
    ("metals", "Mn"): "Sossi et al. 2019",
    ("oxide_vapors", "CrO2"): "Fedkin et al. 2006",
}


def _vapor_pressure_data() -> dict:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())


def test_calibrated_evaporation_alpha_values_sources_and_envelopes():
    data = _vapor_pressure_data()

    for (section, species), expected in EXPECTED_ALPHA.items():
        alpha = data[section][species]["evaporation_alpha"]
        envelope = tuple(alpha["envelope"])

        if isinstance(expected["value"], dict):
            value = alpha["value"]
            assert value["form"] == expected["value"]["form"]
            assert value["A"] == pytest.approx(expected["value"]["A"])
            assert value["B"] == pytest.approx(expected["value"]["B"])
            assert tuple(value["valid_range_K"]) == pytest.approx(
                expected["value"]["valid_range_K"]
            )
            assert value["prior_scalar"]["value"] == pytest.approx(
                expected["value"]["prior_scalar"]
            )
            t_mid = sum(alpha["T_band_K"]) / 2.0
            evaluated = value["A"] * math.exp(-value["B"] / t_mid)
            assert envelope[0] <= evaluated <= envelope[1]
        else:
            assert alpha["value"] == pytest.approx(expected["value"])
            assert envelope[0] <= alpha["value"] <= envelope[1]
        assert envelope == pytest.approx(expected["envelope"])
        assert alpha["source"] == expected["source"]
        assert alpha["tier"] == expected["tier"]

    false_sf2004_labels = {
        "Fe": "SF2004 Table 10 Fe(liq)",
        "Mg": "SF2004 Table 10 Mg(liq)",
        "Na": "SF2004 Table 10 Na(g) over silicate",
        "K": "SF2004 Table 10 K(g) over silicate",
    }
    for species, old_label in false_sf2004_labels.items():
        source = data["metals"][species]["evaporation_alpha"]["source"]
        assert old_label not in source

    for section, species in EXPECTED_OWNER_RATIFY_ALPHA:
        source = data[section][species]["evaporation_alpha"]["source"]
        assert "OWNER-RATIFY" in source
        assert "intrinsic" in source


def test_tier_3_species_have_fail_loud_policy_not_placeholder_alpha():
    data = _vapor_pressure_data()

    for (section, species), source_marker in EXPECTED_MISSING_ALPHA_POLICY.items():
        species_data = data[section][species]
        policy = species_data["evaporation_alpha_policy"]

        assert "evaporation_alpha" not in species_data
        assert policy["tier"] == 3
        assert policy["policy"] == "fail_loud_missing_alpha"
        assert source_marker in policy["source"]
