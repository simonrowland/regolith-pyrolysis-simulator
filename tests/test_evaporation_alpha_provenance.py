"""Guard calibrated evaporation-alpha values and provenance labels."""

from __future__ import annotations

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
            "Costa & Jacobson 2015 KEMS Fo93Fa7 olivine, Fe+ "
            "alpha=0.011-0.020 at 1700-1800 K; Ebel 2005 calculated "
            "Fe/FeO alpha~0.2 noted as non-measured high-side proxy"
        ),
        "tier": 2,
    },
    ("metals", "Mg"): {
        "value": 0.20,
        "envelope": (0.10, 0.21),
        "source": (
            "Richter et al. 2002 Mg/SiO alpha~0.1-0.2 in vacuum at "
            "1800 C; SF2004 Table 10 Mg2SiO4(l), Hashimoto 1990, "
            "alpha_s=0.20-0.21"
        ),
        "tier": 2,
    },
    ("metals", "Na"): {
        "value": 1.0,
        "envelope": (0.9, 1.0),
        "source": (
            "Sossi et al. 2019 GCA 260:204, Na alpha_e~1 near-ideal "
            "evaporation from ferrobasalt FCMAS melt"
        ),
        "tier": 2,
    },
    ("metals", "K"): {
        "value": 1.0,
        "envelope": (0.9, 1.0),
        "source": (
            "Sossi et al. 2019 GCA 260:204 alkali near-ideal by analogy "
            "to Na; Sossi & Fegley 2018 liquids commonly near unity"
        ),
        "tier": 2,
    },
    ("metals", "Ca"): {
        "value": 0.90,
        "envelope": (0.48, 1.20),
        "source": (
            "Zhang et al. 2014 GCA 140:365-380 CaTiO3 melt at "
            "2005 C; Ca activity proxy"
        ),
        "tier": 2,
    },
    ("metals", "Al"): {
        "value": 0.30,
        "envelope": (0.03, 1.00),
        "source": (
            "Schaefer & Fegley 2004 Icarus 169:216-241 Table 10 plus "
            "Shahar & Young 2007 CAI modeling; conflicting Al proxy coverage"
        ),
        "tier": 2,
    },
    ("metals", "Si"): {
        "value": 1.0,
        "envelope": (0.84, 1.00),
        "source": (
            "Safarian & Engh 2013 Metall. Mater. Trans. A 44:747-753 "
            "pure-Si vacuum evaporation; pure elemental Si branch only"
        ),
        "tier": 2,
    },
    ("metals", "Ti"): {
        "value": 0.80,
        "envelope": (0.39, 1.00),
        "source": (
            "Zhang et al. 2014 GCA 140:365-380 CaTiO3 melt at "
            "2005 C; Ti activity proxy"
        ),
        "tier": 2,
    },
    ("oxide_vapors", "SiO"): {
        "value": 0.04,
        "envelope": (0.003, 0.048),
        "source": (
            "SF2004 Table 10 SiO2(liq), Hashimoto 1990, "
            "alpha_s=0.038-0.048; Costa & Jacobson 2015 olivine "
            "SiO+ 0.003-0.036 cross-check"
        ),
        "tier": 2,
    },
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

        assert alpha["value"] == pytest.approx(expected["value"])
        assert envelope == pytest.approx(expected["envelope"])
        assert envelope[0] <= alpha["value"] <= envelope[1]
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


def test_tier_3_species_have_fail_loud_policy_not_placeholder_alpha():
    data = _vapor_pressure_data()

    for (section, species), source_marker in EXPECTED_MISSING_ALPHA_POLICY.items():
        species_data = data[section][species]
        policy = species_data["evaporation_alpha_policy"]

        assert "evaporation_alpha" not in species_data
        assert policy["tier"] == 3
        assert policy["policy"] == "fail_loud_missing_alpha"
        assert source_marker in policy["source"]
