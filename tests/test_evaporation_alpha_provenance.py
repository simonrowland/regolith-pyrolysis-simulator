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
    },
    ("metals", "Mg"): {
        "value": 0.20,
        "envelope": (0.10, 0.21),
        "source": (
            "Richter et al. 2002 Mg/SiO alpha~0.1-0.2 in vacuum at "
            "1800 C; SF2004 Table 10 Mg2SiO4(l), Hashimoto 1990, "
            "alpha_s=0.20-0.21"
        ),
    },
    ("metals", "Na"): {
        "value": 1.0,
        "envelope": (0.9, 1.0),
        "source": (
            "Sossi et al. 2019 GCA 260:204, Na alpha_e~1 near-ideal "
            "evaporation from ferrobasalt FCMAS melt"
        ),
    },
    ("metals", "K"): {
        "value": 1.0,
        "envelope": (0.9, 1.0),
        "source": (
            "Sossi et al. 2019 GCA 260:204 alkali near-ideal by analogy "
            "to Na; Sossi & Fegley 2018 liquids commonly near unity"
        ),
    },
    ("oxide_vapors", "SiO"): {
        "value": 0.04,
        "envelope": (0.003, 0.048),
        "source": (
            "SF2004 Table 10 SiO2(liq), Hashimoto 1990, "
            "alpha_s=0.038-0.048; Costa & Jacobson 2015 olivine "
            "SiO+ 0.003-0.036 cross-check"
        ),
    },
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

    false_sf2004_labels = {
        "Fe": "SF2004 Table 10 Fe(liq)",
        "Mg": "SF2004 Table 10 Mg(liq)",
        "Na": "SF2004 Table 10 Na(g) over silicate",
        "K": "SF2004 Table 10 K(g) over silicate",
    }
    for species, old_label in false_sf2004_labels.items():
        source = data["metals"][species]["evaporation_alpha"]["source"]
        assert old_label not in source
