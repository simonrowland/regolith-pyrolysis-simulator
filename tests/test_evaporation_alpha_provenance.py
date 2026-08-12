"""Guard calibrated evaporation-alpha values and provenance labels."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator import condensation
from simulator.chemistry.langmuir_knudsen import grounded_alpha
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.diagnostic_helpers.extract_reproduction import _engine_alpha
from simulator.diagnostics import pressure_coating_pareto_diagnostic
from simulator.evaporation import (
    _load_evaporation_alpha_by_species,
    _load_evaporation_alpha_envelope_by_species,
)
from simulator.fidelity_vocabulary import EvidenceClass
from simulator.optimize.honesty import optimizer_tier_label
from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY
from simulator.vapour_rail.catalog import (
    compiled_catalog_for,
    vapor_pressure_legacy_view,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
STICKING_PATH = (
    REPO_ROOT / "data" / "literature" / "vacuum_pyrolysis_sticking.yaml"
)
SETPOINTS_PATH = REPO_ROOT / "data" / "setpoints.yaml"

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
        "envelope": (0.0, 1.0),
        "source": (
            "2026-08-11 evidence recovery: unsupported corpus-created Sossi Na alpha "
            "interval WITHDRAWN. Sossi et al. 2019 report a gamma-derived 0.3-1.7 "
            "inference at 1400 C that conflicts with the physical alpha_e<=1 bound, "
            "then separately adopt alpha_e=1. No grounded intrinsic melt-carrier "
            "coefficient remains. "
            "Hertz-Knudsen ideal alpha=1 is retained only as an explicit upper-bound "
            "kinetic ceiling (status-bearing, never certifies; true flux <= this), not "
            "a measured coefficient and not a silent default."
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
    ("metals", "Cr"): {
        "value": 0.90,
        "T_band_K": (1318, 1563),
        "envelope": (0.80, 1.00),
        "source": (
            "REF-040 Pound 1972 J. Phys. Chem. Ref. Data 1:135-146 Table 1, "
            "DOI 10.1063/1.3253096, selected McCabe, Hudson & Paxton 1958 "
            "Trans. Met. Soc. AIME 212:102 Langmuir-vs-Knudsen result for "
            "99.9% polycrystalline solid Cr and monoatomic Cr(g)"
        ),
        "tier": 1,
    },
    ("metals", "Mn"): {
        "value": 1.0,
        "T_band_K": (1519, 2334.526),
        "envelope": (0.5, 1.0),
        "source": (
            "REF-040 Pound 1972 and REF-017 Safarian & Engh 2013 class basis: "
            "owner-ratified monoatomic-class PROXY, no Mn-specific measurement "
            "exists; Safarian & Engh 2013 full text read = zero Mn"
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
            "Wetzel & Gail 2013 A&A 553 A92 use 0.52*exp(-3685/T) as a solid-SiO "
            "particle-growth coefficient, not silicate-melt free-evaporation "
            "evidence. Runtime retains the form only as an explicitly UNCERTIFIED "
            "hot-source proxy pending a grounded SiO melt coefficient. COLD-WALL "
            "condensation below valid_range_K separately uses the Pound 1972 JPCRD "
            "1:135 DOI 10.1063/1.3253096 high-supersaturation unity condensation "
            "coefficient."
        ),
        "tier": 2,
    },
}

EXPECTED_OWNER_RATIFY_ALPHA = {
    ("metals", "Al"),
    ("metals", "Mn"),
    ("foulant_vapor", "NaCl"),
    ("foulant_vapor", "KCl"),
}

# b-136 / t-559: Zhang 2014 CaTiO3 perovskite proxy WITHDRAWN for these
# carriers. Posture rework: missing α is not a missing pressure → Hertz-Knudsen
# ideal α=1.0 as an explicit status-bearing upper bound (never certifies), so
# the seven rail channels stay live. The single-point [2278,2278] measured-band
# fiction and the mis-tagged proxy values must not return.
HKL_UPPER_BOUND_CA_TI_ALPHA = {
    ("metals", "Ca"),
    ("metals", "Ti"),
    ("oxide_vapors", "CaO_gas"),
    ("oxide_vapors", "TiO"),
    ("oxide_vapors", "TiO2_gas"),
}

EXPECTED_MISSING_ALPHA_POLICY = {}


def _vapor_pressure_data() -> dict:
    return vapor_pressure_legacy_view(
        yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())
    )


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
        if "T_band_K" in expected:
            assert tuple(alpha["T_band_K"]) == expected["T_band_K"]
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
        if species == "Mn":
            assert "owner-ratified" in source
            assert "PROXY" in source
            assert "no Mn-specific measurement exists" in source
            assert "zero Mn" in source
        else:
            assert "OWNER-RATIFY" in source
            assert "intrinsic" in source

    cro2_alpha = data["oxide_vapors"]["CrO2"]["evaporation_alpha"]
    assert cro2_alpha["tag"] == "broad_proxy_not_intrinsic"
    assert "no CrO2-specific alpha" in cro2_alpha["source"]


def test_tier_3_species_have_fail_loud_policy_not_placeholder_alpha():
    data = _vapor_pressure_data()

    for (section, species), source_marker in EXPECTED_MISSING_ALPHA_POLICY.items():
        species_data = data[section][species]
        policy = species_data["evaporation_alpha_policy"]

        assert "evaporation_alpha" not in species_data
        assert policy["tier"] == 3
        assert policy["policy"] == "fail_loud_missing_alpha"
        assert source_marker in policy["source"]


def test_sossi_sodium_family_withdrawal_reaches_every_runtime_store():
    raw = yaml.safe_load(VAPOR_PRESSURES_PATH.read_text()) or {}
    families = raw["families"]
    family_by_species = {
        "Na": "metals_na_family",
        "Na2": "oxide_vapors_na2_cluster_family",
        "Na2O_gas": "oxide_vapors_na2o_family",
    }

    def forbidden_range_paths(value, path=()):
        paths = []
        if isinstance(value, dict):
            for key, child in value.items():
                paths.extend(forbidden_range_paths(child, (*path, str(key))))
        elif isinstance(value, list):
            if value == [0.9, 1.0]:
                paths.append(".".join(path))
            for index, child in enumerate(value):
                paths.extend(forbidden_range_paths(child, (*path, str(index))))
        return paths

    sodium_families = {
        family_id: families[family_id]
        for family_id in family_by_species.values()
    }
    assert forbidden_range_paths(sodium_families) == []

    for species, family_id in family_by_species.items():
        kinetics = families[family_id]["vaporisation_coefficients"]
        alpha = kinetics["evaporation_alpha"]
        assert alpha["value"] == pytest.approx(1.0), species
        assert alpha["status"] == "analytical_upper_bound", species
        assert alpha["tag"] == "hkl_ideal_upper_bound_status_bearing", species
        assert tuple(alpha["envelope"]) == (0.0, 1.0), species
        assert tuple(
            kinetics["alpha_domain_and_uncertainty"]["envelope"]
        ) == (0.0, 1.0), species
        assert "withdrawn_range" not in alpha, species

    data = _vapor_pressure_data()
    compiled = compiled_catalog_for(raw)
    loaded_alpha = _load_evaporation_alpha_by_species(raw)
    loaded_envelope = _load_evaporation_alpha_envelope_by_species(raw)
    legacy_location = {
        "Na": ("metals", "Na"),
        "Na2": ("oxide_vapors", "Na2"),
        "Na2O_gas": ("oxide_vapors", "Na2O_gas"),
    }
    for species, (section, legacy_species) in legacy_location.items():
        compiled_kinetics = compiled.species[
            species
        ].vaporisation_coefficients
        assert "withdrawn_range" not in compiled_kinetics.evaporation_alpha, species
        assert tuple(compiled_kinetics.evaporation_alpha["envelope"]) == (
            0.0,
            1.0,
        ), species
        assert tuple(
            compiled_kinetics.alpha_domain_and_uncertainty["envelope"]
        ) == (0.0, 1.0), species
        compiled_alpha = data[section][legacy_species]["evaporation_alpha"]
        assert "withdrawn_range" not in compiled_alpha, species
        assert tuple(compiled_alpha["envelope"]) == (0.0, 1.0), species
        spec = loaded_alpha[species]
        assert spec["form"] == "scalar", species
        assert spec["value"] == pytest.approx(1.0), species
        assert spec["alpha_authority_status"] == "analytical_upper_bound", species
        assert loaded_envelope[species] == (0.0, 1.0), species

    withdrawal_path = (
        REPO_ROOT
        / "validation-data"
        / "pin-evidence"
        / "na_mass_spec_analytical_ceiling_withdrawal_2026-08-11.yaml"
    )
    withdrawal = yaml.safe_load(withdrawal_path.read_text()) or {}
    assert withdrawal["withdrawn_range"]["former_alpha_range"] == [0.9, 1.0]
    assert (
        withdrawal["withdrawn_range"]["reason"]
        == "source_does_not_report_this_interval"
    )
    assert withdrawal["withdrawn_range"]["source_inference_at_1400C"] == [
        0.3,
        1.7,
    ]
    assert (
        withdrawal["withdrawn_range"]["source_inference_status"]
        == "conflicts_with_physical_alpha_upper_bound"
    )
    assert withdrawal["withdrawn_range"]["affected_runtime_species"] == [
        "Na",
        "Na2",
        "Na2O_gas",
    ]

    sticking = yaml.safe_load(STICKING_PATH.read_text()) or {}
    sticking_na = sticking["species"]["Na"]
    assert sticking_na["value"] == pytest.approx(1.0)
    assert sticking_na["status"] == "UNCERTIFIED"
    assert sticking_na["source_class"] == "hkl_ideal_upper_bound"
    assert tuple(sticking_na["envelope"]) == (0.0, 1.0)
    assert forbidden_range_paths({"Na": sticking_na}) == []
    assert "[0.9,1.0]" not in sticking_na["source"]

    runtime_sticking_na = condensation.STICKING_DATA["species"]["Na"]
    assert runtime_sticking_na == sticking_na
    assert condensation.alpha_s("Na", 1700.0, {}) == pytest.approx(1.0)


def test_sossi_and_wetzel_withdrawals_reach_runtime_and_user_facing_docs():
    data = _vapor_pressure_data()
    sio = data["oxide_vapors"]["SiO"]["evaporation_alpha"]
    assert sio["value"]["status"] == "UNCERTIFIED"
    assert sio["tag"] == "solid_film_growth_proxy_not_evaporation_evidence"
    assert "particle-growth coefficient" in sio["source"]
    assert "not silicate-melt free-evaporation evidence" in sio["source"]

    docs = "\n".join(
        (REPO_ROOT / path).read_text()
        for path in (
            "docs/chemistry-methods.md",
            "docs/model-limitations.md",
            "docs/lab-validation-whitepaper.md",
            "docs/references/index.html",
            "docs/references/topics/evaporation.html",
        )
    )
    stale_claims = (
        "Na `alpha=1.0`, envelope `[0.9,1.0]`",
        "sodium value (α ≈ 1.0, envelope 0.9–1.0",
        "HOT source evaporation uses the Wetzel",
        "for source evaporation; Pound 1972",
        "OWNER-RATIFY source_class=open_furnace_apparent_not_intrinsic",
        "reports near-ideal Na alpha_e~1 from mass-spec isotope fractionation",
    )
    for claim in stale_claims:
        assert claim not in docs


def test_zhang_2014_catio3_proxy_withdrawn_hkl_upper_bound_posture():
    """b-136 / t-559: Zhang proxy withdrawn; HKL ideal α=1 upper-bound posture.

    The perovskite CaTiO3 datum is not melt-carrier HKL α (withdrawal stays).
    But α is a kinetic correction on a pressure we already have, so missing α
    is NOT the missing-input refuse class (that class is for missing pressure,
    e.g. NaF). Chosen posture: Hertz-Knudsen ideal α=1.0 as an explicit
    status-bearing upper bound (true flux ≤ this; never certifies). This keeps
    the seven Ca/Ti rail channels live — refuse would convert an unjustified
    coefficient into an unjustified silent zero (b-139/b-149 class).

    Guards:
    - tag is hkl_ideal_upper_bound_status_bearing, value exactly 1.0
    - source records WITHDRAWN b-136 and upper-bound language
    - former [2278,2278] single-point band does not return
    - former proxy values 0.9/0.8 do not return
    - loader surfaces executable 1.0 (channel contract complete)
    - Ca2 cascade included (same parent defect class)
    """

    raw = yaml.safe_load(VAPOR_PRESSURES_PATH.read_text()) or {}
    families = raw["families"]
    family_by_species = {
        "Ca": "metals_ca_family",
        "Ti": "metals_ti_family",
        "CaO_gas": "oxide_vapors_cao_family",
        "TiO": "oxide_vapors_tio_family",
        "TiO2_gas": "oxide_vapors_tio2_family",
        "Ca2": "oxide_vapors_ca2_family",
    }
    for species, family_id in family_by_species.items():
        alpha = families[family_id]["vaporisation_coefficients"]["evaporation_alpha"]
        assert alpha.get("value") == pytest.approx(1.0), species
        assert alpha.get("tag") == "hkl_ideal_upper_bound_status_bearing", species
        assert alpha.get("status") != "no_data", species
        assert alpha.get("policy") != "refuse_nonzero_flux", species
        source = str(alpha.get("source") or "")
        assert "WITHDRAWN" in source and "b-136" in source, species
        assert "upper-bound" in source or "upper bound" in source, species
        assert "status_bearing" in source or "status-bearing" in source or (
            "never certifies" in source
        ), species
        # Single-point [2278, 2278] measured-band fiction must not return.
        t_band = tuple(alpha.get("T_band_K") or ())
        assert t_band != (2278, 2278), species
        # Former unjustified proxy scalars must not return as the live value.
        assert alpha.get("value") not in (0.8, 0.9), species
        withdrawn = alpha.get("withdrawn_proxy") or {}
        assert "former_value" in withdrawn, species
        assert withdrawn["former_T_band_K"] is not None, species

    data = _vapor_pressure_data()
    for section, species in HKL_UPPER_BOUND_CA_TI_ALPHA:
        species_data = data[section][species]
        alpha = species_data["evaporation_alpha"]
        assert alpha["value"] == pytest.approx(1.0), (section, species)
        assert alpha["tag"] == "hkl_ideal_upper_bound_status_bearing", (
            section,
            species,
        )
        blob = yaml.safe_dump(species_data)
        assert "CaTiO3 melt at 2005" not in blob, (section, species)
        # Zhang citation may appear only as WITHDRAWN provenance, not as live
        # OWNER-RATIFY proxy wording.
        if "Zhang et al. 2014 GCA 140:365-380" in blob:
            assert "WITHDRAWN" in blob, (section, species)

    loaded = _load_evaporation_alpha_by_species(raw)
    for species in family_by_species:
        alpha = families[family_by_species[species]]["vaporisation_coefficients"][
            "evaporation_alpha"
        ]
        assert alpha["status"] == "analytical_upper_bound", species
        spec = loaded[species]
        assert spec["form"] == "scalar", species
        assert spec["value"] == pytest.approx(1.0), species
        assert spec["alpha_authority_status"] == "analytical_upper_bound", species


def test_ca_ti_alpha_ceiling_survives_flux_serialization_replay_and_honesty():
    raw = yaml.safe_load(VAPOR_PRESSURES_PATH.read_text()) or {}
    alpha_by_species = _load_evaporation_alpha_by_species(raw)
    species = ("Ca", "Ti")
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"CaO": 10.0, "TiO2": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {name: 100.0 for name in species},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"Ca": 0.040078, "Ti": 0.047867},
            "stoich_by_species": {
                "Ca": {
                    "parent_oxide": "CaO",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
                "Ti": {
                    "parent_oxide": "TiO2",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
            },
            "available_oxide_kg": {"Ca": 10.0, "Ti": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["alpha_used_by_species"] == {
        "Ca": pytest.approx(1.0),
        "Ti": pytest.approx(1.0),
    }
    assert result.diagnostic["alpha_authority_status_by_species"] == {
        "Ca": "analytical_upper_bound",
        "Ti": "analytical_upper_bound",
    }
    serialized_flux = json.loads(json.dumps(dict(result.diagnostic)))
    assert serialized_flux["alpha_authority_status_by_species"] == {
        "Ca": "analytical_upper_bound",
        "Ti": "analytical_upper_bound",
    }

    knudsen = {
        "gas_temperature_C": 1000.0,
        "carrier_gas": "N2",
        "overhead_pressure_mbar": 10.0,
        "segments": [
            {
                "name": "main",
                "characteristic_length_m": 0.12,
                "knudsen_number": 0.001,
                "regime": "viscous",
            }
        ],
    }
    sim = SimpleNamespace(
        condensation_model=SimpleNamespace(
            last_knudsen_regime_diagnostic=knudsen,
            gas_temperature_C=1000.0,
            carrier_gas="N2",
        ),
        melt=SimpleNamespace(temperature_C=1700.0),
        overhead=SimpleNamespace(pressure_mbar=10.0),
        vapor_pressures=vapor_pressure_legacy_view(raw),
        _last_evaporation_flux_diagnostic=serialized_flux,
        _alpha_authority_status_by_species_engaged=serialized_flux[
            "alpha_authority_status_by_species"
        ],
    )
    replay = pressure_coating_pareto_diagnostic(
        sim,
        target_species=species,
    )
    artifact = json.loads(
        json.dumps({"run_metadata": {"pressure_coating_pareto_diagnostic": replay}})
    )

    assert replay["alpha_authority_status_by_species"] == {
        "Ca": "analytical_upper_bound",
        "Ti": "analytical_upper_bound",
    }
    for name in species:
        assert replay["by_species"][name]["alpha_intrinsic"] == pytest.approx(1.0)
        assert replay["by_species"][name]["alpha_authority_status"] == (
            "analytical_upper_bound"
        )

    label = optimizer_tier_label(
        {
            "cache_state": "live_fill",
            "backend_name": "alphamelts",
            "evidence_class": EvidenceClass.MELTS.value,
            "backend_status": "ok",
            "backend_authoritative": True,
        },
        artifact,
    )
    assert label["ux_label"] == "UNVERIFIED"
    assert label["certification_allowed"] is False
    assert label["canonical"]["certification_allowed"] is False
    assert label["alpha_ceiling_species"] == ["Ca", "Ti"]


@pytest.mark.parametrize("species", ("Ca", "Ti"))
def test_ca_ti_alpha_ceiling_survives_grounded_report_consumer(species):
    value, grounded_context = grounded_alpha(species, 1700.0)

    assert value == pytest.approx(1.0)
    assert grounded_context["alpha_s"] == pytest.approx(1.0)
    assert grounded_context["alpha_authority_status"] == (
        "analytical_upper_bound"
    )

    report_value, refusal, report_context = _engine_alpha(species, 1700.0)
    serialized = json.loads(json.dumps({"alpha_context": report_context}))

    assert refusal is None
    assert report_value == pytest.approx(1.0)
    assert serialized["alpha_context"]["alpha_authority_status"] == (
        "analytical_upper_bound"
    )


def test_default_setpoints_refuse_unmeasured_alpha_fallback():
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text()) or {}

    assert (
        setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"]
        is False
    )
