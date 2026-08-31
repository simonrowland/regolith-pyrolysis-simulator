"""SC-171: present null on conservative boolean flags must not defeat the default."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.config_flags import (
    CONSERVATIVE_BOOL_FEATURE_FLAGS,
    bool_feature_flag,
)
from simulator.core import PyrolysisSimulator
from simulator.cost_ledger import CostImportContext
from simulator.wall_advisor import (
    _reactive_verdict,
    resolve_wall_operating_point,
)


_SIO_VISCOUS = {
    "species": "SiO",
    "P_eq_pa": 100.0,
    "P_bulk_pa": 1.0,
    "T_surface_K": 1800.0,
    "molar_mass_kg_mol": 0.044085,
    "alpha_i": 1.0,
    "knudsen_number": 0.004,
    "pipe_diameter_m": 0.12,
    "overhead_pressure_pa": 100.0,
    "axial_stir_factor": 1.0,
    "radial_stir_factor": 1.0,
    "carrier_gas": "N2",
    "T_gas_K": 1800.0,
    "melt_resistance_enabled": False,
}


def test_bool_feature_flag_three_way_absent_null_explicit_false():
    """Pin the distinction a later 'fix' must not collapse: explicit false stays false."""

    assert bool_feature_flag({}, "gas_resistance_enabled", True) is True
    assert bool_feature_flag({"gas_resistance_enabled": None}, "gas_resistance_enabled", True) is True
    assert bool_feature_flag({"gas_resistance_enabled": False}, "gas_resistance_enabled", True) is False


def test_bool_feature_flag_refuses_unlisted_key():
    with pytest.raises(ValueError, match="not an admitted conservative boolean feature flag"):
        bool_feature_flag({}, "allow_fallback_vapor", True)


def test_bool_feature_flag_refuses_permissive_constant_default():
    with pytest.raises(ValueError, match="audited conservative setting True"):
        bool_feature_flag({}, "needs_experiment", False)


def _provider_gas_resistance(series_config: dict, extra_controls: dict | None = None):
    captured: dict[str, bool] = {}
    real = _series_resistance_evaporation_flux_kg_m2_s

    def _wrapped(*args, **kwargs):
        captured["gas_resistance_enabled"] = kwargs["gas_resistance_enabled"]
        return real(*args, **kwargs)

    controls = {
        "vapour_batch_flux_pressures_Pa": {"SiO": 100.0},
        "overhead_partials_Pa": {"SiO": 1.0},
        "molar_mass_kg_mol": {"SiO": 0.044085},
        "stoich_by_species": {
            "SiO": {
                "parent_oxide": "SiO2",
                "oxide_per_product_kg": 1.363,
            }
        },
        "available_oxide_kg": {"SiO": 10.0},
        "melt_surface_area_m2": 1.0,
        "stir_factor": 1.0,
        "alpha": {"SiO": 1.0},
        "pipe_diameter_m": 0.12,
        "overhead_pressure_pa": 100.0,
        "gas_temperature_K": 1800.0,
        "carrier_gas": "N2",
        "evaporation_series_resistance": series_config,
    }
    if extra_controls:
        controls.update(extra_controls)
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1526.85,
        pressure_bar=0.001,
        fO2_log=None,
        control_inputs=controls,
    )
    with patch(
        "engines.builtin.evaporation_flux._series_resistance_evaporation_flux_kg_m2_s",
        side_effect=_wrapped,
    ):
        result = BuiltinEvaporationFluxProvider().dispatch(request)
    return captured, result


def test_present_null_gas_resistance_keeps_default_on():
    """Site 1: null must not drop the viscous gas-film resistance (~180x flux)."""

    on = _series_resistance_evaporation_flux_kg_m2_s(
        **_SIO_VISCOUS, gas_resistance_enabled=True
    )
    off = _series_resistance_evaporation_flux_kg_m2_s(
        **_SIO_VISCOUS, gas_resistance_enabled=False
    )
    assert on.r_gas > 0.0
    assert off.r_gas == 0.0
    assert off.flux_kg_s_m2 / on.flux_kg_s_m2 > 10.0

    captured_absent, result_absent = _provider_gas_resistance({})
    captured_null, result_null = _provider_gas_resistance(
        {"gas_resistance_enabled": None}
    )
    captured_false, result_false = _provider_gas_resistance(
        {"gas_resistance_enabled": False}
    )

    assert captured_absent["gas_resistance_enabled"] is True
    assert captured_null["gas_resistance_enabled"] is True
    assert captured_false["gas_resistance_enabled"] is False
    assert result_absent.status == "ok"
    assert result_null.status == "ok"
    flux_absent = result_absent.diagnostic["evaporation_flux_kg_hr"]["SiO"]
    flux_null = result_null.diagnostic["evaporation_flux_kg_hr"]["SiO"]
    flux_false = result_false.diagnostic["evaporation_flux_kg_hr"]["SiO"]
    assert flux_null == pytest.approx(flux_absent, rel=1e-12)
    assert flux_false > flux_absent * 5.0


def test_gas_resistance_lookups_route_through_helper():
    """Site 1 has three production lookups; all must share the helper."""

    for rel in (
        "simulator/core.py",
        "engines/builtin/evaporation_flux.py",
        "simulator/extraction.py",
    ):
        text = Path(rel).read_text(encoding="utf-8")
        assert "bool_feature_flag(" in text
        assert "series_config.get('gas_resistance_enabled', True)" not in text
        assert 'series_config.get("gas_resistance_enabled", True)' not in text


def _oxygen_exchange_k(config: dict, T_K: float = 1800.0) -> tuple[float, str]:
    sim = SimpleNamespace(_oxygen_exchange_config=lambda: config)
    return PyrolysisSimulator._oxygen_exchange_k_m_s(sim, T_K)


def test_present_null_temperature_dependence_keeps_arrhenius_on():
    """Site 2: null must not disable sso_r.oxygen_exchange temperature dependence."""

    k_absent, source_absent = _oxygen_exchange_k({})
    k_null, source_null = _oxygen_exchange_k(
        {"temperature_dependence_enabled": None}
    )
    k_false, source_false = _oxygen_exchange_k(
        {"temperature_dependence_enabled": False}
    )

    assert k_null == pytest.approx(k_absent, rel=0.0)
    assert source_null == source_absent
    assert "arrhenius" in source_absent
    assert k_false != pytest.approx(k_absent, rel=1e-12)
    assert "temperature_dependence_disabled" in source_false


def test_present_null_needs_experiment_stays_raised():
    """Site 3: null must not clear the wall-materials experiment flag."""

    operating_point = resolve_wall_operating_point()

    def _verdict(needs_experiment):
        cell = {
            "regime": "reducing_vacuum",
            "sign": "volatile_or_revolatilizing",
            "product_phase": "SiO(g)",
            "net_liner_delta": "thinning",
            "wall_property_effect": {"basis": "sc-171 fixture"},
        }
        if needs_experiment is not Ellipsis:
            cell["needs_experiment"] = needs_experiment
        return _reactive_verdict("SiO", [cell], operating_point)

    absent = _verdict(Ellipsis)
    present_null = _verdict(None)
    explicit_false = _verdict(False)

    assert absent.needs_experiment is True
    assert present_null.needs_experiment is True
    assert explicit_false.needs_experiment is False
    assert present_null.matched is True
    assert present_null.verdict == "hazardous"


def test_present_null_import_flag_keeps_computed_bootstrap_default():
    """Site 4: null must not discard the computed bootstrap import-penalty default."""

    absent = CostImportContext.from_config({"mode": "bootstrap_narrative"})
    present_null = CostImportContext.from_config(
        {"mode": "bootstrap_narrative", "import_flag_enabled": None}
    )
    explicit_false = CostImportContext.from_config(
        {"mode": "bootstrap_narrative", "import_flag_enabled": False}
    )
    mature_null = CostImportContext.from_config(
        {"mode": "mature", "import_flag_enabled": None}
    )

    assert absent.import_flag_enabled is True
    assert present_null.import_flag_enabled is True
    assert explicit_false.import_flag_enabled is False
    assert mature_null.import_flag_enabled is False
    assert absent.classify("Mg") == "import_penalty"
    assert present_null.classify("Mg") == "import_penalty"
    assert explicit_false.classify("Mg") == "isru_local"


def test_admitted_flags_are_exactly_the_audited_four():
    assert CONSERVATIVE_BOOL_FEATURE_FLAGS == frozenset(
        {
            "gas_resistance_enabled",
            "temperature_dependence_enabled",
            "needs_experiment",
            "import_flag_enabled",
        }
    )


# --- quoted-false regression (peer-reported after 92769d93) ------------------

@pytest.mark.parametrize("spelling", ["false", "False", "FALSE ", " no", "off", "0"])
def test_a_quoted_false_does_not_read_as_true(spelling):
    """The null bug's mirror image, and in the more dangerous direction.

    ``bool("false")`` is True, and so are "no", "off" and "0" -- four spellings
    that invert the operator's intent with no exception. Where a present null
    gave the CONSERVATIVE default, this gave the PERMISSIVE opposite of what was
    asked: Arrhenius temperature dependence ON for an operator who wrote it off.

    Unquoted ``false`` parses as a real YAML boolean and never reaches this
    path, so the bug only bites the quoted or numeric-as-string spelling --
    which is exactly the spelling nobody writes a test for.
    """
    key = "temperature_dependence_enabled"
    assert bool_feature_flag({key: spelling}, key, True) is False


@pytest.mark.parametrize("spelling", ["true", "yes", "on", "1", "anything"])
def test_a_non_false_string_still_reads_as_true(spelling):
    key = "temperature_dependence_enabled"
    assert bool_feature_flag({key: spelling}, key, True) is True


def test_the_three_way_distinction_survives_the_string_handling():
    """absent -> default, null -> default, explicit false -> FALSE.

    Re-pinned here because the string branch is new: a later "simplification"
    that folded strings back into bool() would restore the inversion while
    leaving the original three-way test green.
    """
    key = "temperature_dependence_enabled"
    assert bool_feature_flag({}, key, True) is True
    assert bool_feature_flag({key: None}, key, True) is True
    assert bool_feature_flag({key: False}, key, True) is False


def test_an_uninterpretable_value_refuses_rather_than_guessing():
    """bool() on a container answers by emptiness, which is not an opinion."""
    key = "temperature_dependence_enabled"
    for value in ([], ["x"], {}, {"a": 1}, object()):
        with pytest.raises(ValueError, match="must be a boolean"):
            bool_feature_flag({key: value}, key, True)
