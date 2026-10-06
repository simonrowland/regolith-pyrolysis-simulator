"""t-1139 Build B2 pins of the trace-parent activity verdict after the ladder.

The pre-ladder pin (ideal activity equal to the mole fraction, no rung) is
the parent commit. These literals are the production resolver's verdict at
mole fraction 1e-6. Live channel pin tables stay the Build A digest.
"""

from __future__ import annotations

import hashlib
import json

from simulator.trace_oxide_parents import ACTIVITY_BASIS, LIQUID_PARENT_OXIDE
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    CondensedPhaseActivityProvider,
    StandardStateIdentity,
)
from tests.test_t1139_b1_ledger_pins import _LIVE_CHANNEL_PIN_DIGEST
from tests.test_t1139_build_a_pins import (
    FIRST_BATCH_EXISTING,
    PRESSURE_PINS,
    STOICH_PINS,
)


PIN_TEMPERATURES_K = (1500.0, 1673.0)
PIN_MOLE_FRACTION = 1e-6

# Recorded from CondensedPhaseActivityProvider after the ladder. Gamma hex is
# the fit coefficient on the requested component; value hex is the activity.
# A parent row uses a = gamma * X on the molecular basis passed in. An
# activity-basis spelling keeps the pure-liquid reference coefficient in
# gamma and sets the activity by a_single = a_parent ** (1/c). A Table 2
# row that does not state the caller's standard state is status-bearing,
# not a point. Published rows that no band selects are the source's stated
# nominal when one is recorded (Cu, Altman 1978), otherwise the candidate
# envelope (GeO2, geometric mean). The unity upper bound remains only where
# no published row was measured.
_AFTER_LADDER = json.loads(
    r"""
{
  "B2O3": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": {
      "authority_level": "extrapolated",
      "certified_band": {
        "temperature_K": [
          1800.0,
          2200.0
        ]
      },
      "reason": "temperature outside the fit validity range"
    },
    "flag": "extrapolated",
    "gamma_hex": {
      "1500": "0x1.4a5ef73f587cep-23",
      "1673": "0x1.30c4cd9b7e70ap-23"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "published_gamma_extrapolated",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=cfa04d163c6b",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.5a6b478373699p-43",
      "1673": "0x1.3f92bddf42251p-43"
    },
    "verdict": "StatusBearingValue"
  },
  "Cs2O": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "standard_state_basis_unestablished",
    "gamma_hex": {
      "1500": "0x1.137459234191cp-27",
      "1673": "0x1.d957152e02175p-25"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "standard_state_basis_unestablished",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=88048d19eeb1",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.20d5c0153d76ap-47",
      "1673": "0x1.f0554896df580p-45"
    },
    "verdict": "StatusBearingValue"
  },
  "Cu2O": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "source_stated_nominal",
    "gamma_hex": {
      "1500": "0x1.c606a1a8023e8p+0",
      "1673": "0x1.abe807e222328p+0"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "source_stated_nominal",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 4,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=fa5591300e55",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.dc14a68f4dbc6p-20",
      "1673": "0x1.c0b13ddbbc1d6p-20"
    },
    "verdict": "StatusBearingValue"
  },
  "CuO0.5": {
    "authority": false,
    "bound": null,
    "coefficient_formula": "Cu2O",
    "extrapolation_notice": null,
    "flag": "source_stated_nominal",
    "gamma_hex": {
      "1500": "0x1.54ed175ec4c24p+0",
      "1673": "0x1.4af97008ebc1fp+0"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "source_stated_nominal",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 4,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=fa5591300e55",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.5d1bbdd1aedb6p-10",
      "1673": "0x1.52eaf1b31e78cp-10"
    },
    "verdict": "StatusBearingValue"
  },
  "Ga2O3": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "proxy_estimate",
    "gamma_hex": {
      "1500": "0x1.030417c184bc4p-14",
      "1673": "0x1.60d5b11eab48bp-13"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "proxy_estimate",
    "provider": "trace_parent_activity_ladder",
    "reason": "proxy_gamma_estimate",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=d8fd97ad84fe",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.0f9913205853cp-34",
      "1673": "0x1.71f95a4969ecep-33"
    },
    "verdict": "StatusBearingValue"
  },
  "GaO1.5": {
    "authority": false,
    "bound": null,
    "coefficient_formula": "Ga2O3",
    "extrapolation_notice": null,
    "flag": "proxy_estimate",
    "gamma_hex": {
      "1500": "0x1.0180ea8096155p-7",
      "1673": "0x1.a907f224cddc0p-7"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "proxy_estimate",
    "provider": "trace_parent_activity_ladder",
    "reason": "proxy_gamma_estimate",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=d8fd97ad84fe",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.07af049c3f92cp-17",
      "1673": "0x1.b33b5629c88f6p-17"
    },
    "verdict": "StatusBearingValue"
  },
  "GeO2": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "envelope_midpoint",
    "gamma_hex": {
      "1500": "0x1.03e7223198b04p-2",
      "1673": "0x1.ad5ac6bebcc0bp-2"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "envelope_midpoint",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 3,
    "source_row_id": null,
    "tier": "B",
    "value_hex": {
      "1500": "0x1.108724eb70dedp-22",
      "1673": "0x1.c235ff1c2efdfp-22"
    },
    "verdict": "StatusBearingValue"
  },
  "In2O3": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "standard_state_basis_unestablished",
    "gamma_hex": {
      "1500": "0x1.71736527bbfbdp-15",
      "1673": "0x1.0496141e2f5f6p-13"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "standard_state_basis_unestablished",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=aaa9829cdabd",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.8365af0fdc11bp-35",
      "1673": "0x1.113e965d6b437p-33"
    },
    "verdict": "StatusBearingValue"
  },
  "InO1.5": {
    "authority": false,
    "bound": null,
    "coefficient_formula": "In2O3",
    "extrapolation_notice": null,
    "flag": "standard_state_basis_unestablished",
    "gamma_hex": {
      "1500": "0x1.b2ec84119b89ap-8",
      "1673": "0x1.6d4469e3ed039p-7"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "standard_state_basis_unestablished",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=aaa9829cdabd",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.bd5cb032cc53ap-18",
      "1673": "0x1.76089d956d950p-17"
    },
    "verdict": "StatusBearingValue"
  },
  "Li2O": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": {
      "authority_level": "extrapolated",
      "certified_band": {
        "temperature_K": [
          1800.0,
          2200.0
        ]
      },
      "reason": "temperature outside the fit validity range"
    },
    "flag": "extrapolated",
    "gamma_hex": {
      "1500": "0x1.dac7e44ced722p-29",
      "1673": "0x1.a2f58db850c9ep-27"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "published_gamma_extrapolated",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=5697cec1c522",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.f1d80204df396p-49",
      "1673": "0x1.b74f809108ce5p-47"
    },
    "verdict": "StatusBearingValue"
  },
  "PbO": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": {
      "authority_level": "extrapolated",
      "certified_band": {
        "temperature_K": [
          1800.0,
          2200.0
        ]
      },
      "reason": "temperature outside the fit validity range"
    },
    "flag": "extrapolated",
    "gamma_hex": {
      "1500": "0x1.3174f82b756bfp-2",
      "1673": "0x1.9a9e4e7b7e00ep-2"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "published_gamma_extrapolated",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=dd462de290dd",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.404b7724693bfp-22",
      "1673": "0x1.ae90888efdcabp-22"
    },
    "verdict": "StatusBearingValue"
  },
  "Rb2O": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "standard_state_basis_unestablished",
    "gamma_hex": {
      "1500": "0x1.407beea69aaacp-30",
      "1673": "0x1.501d1a32d102cp-27"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "standard_state_basis_unestablished",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=13d25d543cb0",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.500d4c5c2ff36p-50",
      "1673": "0x1.6070d4485b1b3p-47"
    },
    "verdict": "StatusBearingValue"
  },
  "SnO": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": {
      "authority_level": "extrapolated",
      "certified_band": {
        "temperature_K": [
          1800.0,
          2200.0
        ]
      },
      "reason": "temperature outside the fit validity range"
    },
    "flag": "extrapolated",
    "gamma_hex": {
      "1500": "0x1.48cf071b5f6a2p+1",
      "1673": "0x1.4d8f9f6b3607ap+1"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "published",
    "provider": "trace_parent_activity_ladder",
    "reason": "published_gamma_extrapolated",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=d9adb021bd8a",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.58c7e9f600dc5p-19",
      "1673": "0x1.5dc39b2f9ec69p-19"
    },
    "verdict": "StatusBearingValue"
  },
  "V2O3": {
    "authority": false,
    "bound": null,
    "coefficient_formula": null,
    "extrapolation_notice": null,
    "flag": "proxy_estimate",
    "gamma_hex": {
      "1500": "0x1.fd72aa196d134p-42",
      "1673": "0x1.cba7842db7682p-38"
    },
    "homologue": null,
    "may_certify": false,
    "origin": "proxy_estimate",
    "provider": "trace_parent_activity_ladder",
    "reason": "proxy_gamma_estimate",
    "report_label": "status-bearing-not-point",
    "rung": 2,
    "source_row_count": 1,
    "source_row_id": "fegley-2023-chemical-equilibrium-calculations-bu::fegley_2023_table_02_model::rows_as_printed:h=ec257950b80d",
    "tier": "B",
    "value_hex": {
      "1500": "0x1.0b18f0d4d279cp-61",
      "1673": "0x1.e1fb87246b50dp-58"
    },
    "verdict": "StatusBearingValue"
  }
}
"""
)


def _trace_parent_formulas() -> tuple[str, ...]:
    return tuple(
        sorted(set(LIQUID_PARENT_OXIDE.values()) | set(ACTIVITY_BASIS.values()))
    )


def _resolve(formula: str, temperature_K: float):
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id=formula,
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="liquid",
            reference_pressure_bar=1.0,
        ),
        activity_model="source_reaction_activity",
        allow_henrian_upper_bound=True,
    )
    return provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        mole_fraction=PIN_MOLE_FRACTION,
        temperature_K=temperature_K,
    )


def test_trace_parent_activity_verdicts_match_the_ladder_pin() -> None:
    formulas = _trace_parent_formulas()
    assert formulas == tuple(_AFTER_LADDER)
    for formula in formulas:
        expected = _AFTER_LADDER[formula]
        for temperature_K in PIN_TEMPERATURES_K:
            answer = _resolve(formula, temperature_K)
            label = f"{formula} @ {temperature_K}"
            key = str(int(temperature_K))
            assert answer.verdict.value == expected["verdict"], label
            assert (None if answer.tier is None else answer.tier.value) == expected["tier"], label
            assert answer.reason == expected["reason"], label
            assert answer.provider == expected["provider"], label
            assert answer.report_label == expected["report_label"], label
            assert answer.authority is expected["authority"], label
            assert answer.may_certify() is expected["may_certify"], label
            bound = None if answer.bound_direction is None else answer.bound_direction.value
            assert bound == expected["bound"], label
            derivation = answer.derivation
            assert derivation["flag"] == expected["flag"], label
            assert derivation["rung"] == expected["rung"], label
            assert derivation["origin"] == expected["origin"], label
            assert derivation["homologue"] == expected["homologue"], label
            assert derivation["coefficient_formula"] == expected["coefficient_formula"], label
            assert derivation["source_row_id"] == expected["source_row_id"], label
            assert len(derivation["source_row_ids"]) == expected["source_row_count"], label
            assert derivation["extrapolation_notice"] == expected["extrapolation_notice"], label
            gamma = derivation["gamma"]
            assert (None if gamma is None else float(gamma).hex()) == expected["gamma_hex"][key], label
            assert (None if answer.value is None else float(answer.value).hex()) == expected["value_hex"][key], label


def test_live_channel_pin_tables_stay_at_the_build_a_digest() -> None:
    payload = {
        "pressure": {key: list(value) for key, value in sorted(PRESSURE_PINS.items())},
        "stoich": {key: list(value) for key, value in sorted(STOICH_PINS.items())},
        "dormant": list(FIRST_BATCH_EXISTING),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == _LIVE_CHANNEL_PIN_DIGEST
