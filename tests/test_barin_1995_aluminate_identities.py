"""Source-wide checks for Barin's six aluminate and spinel tables."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
import yaml
from simulator.battery import identity as battery_identity
from tests.helpers.corpus_paths import regolith_corpus_root


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/barin-1995.yaml"
CORPUS_TRANSCRIPTION_RELATIVE = (
    "tables/barin-1995-aluminates/barin-1995-aluminate-tables.md"
)
CORPUS_TRANSCRIPTION = regolith_corpus_root() / CORPUS_TRANSCRIPTION_RELATIVE
ROW_COUNTS = {
    "CaAl2O4": 17,
    "CaAl4O7": 19,
    "Ca3Al2O6": 17,
    "Ca12Al14O33": 16,
    "NaAlO2": 20,
    "MgAl2O4": 26,
}
IDENTITY_QUANTITIES = (
    "negative_gibbs_function_over_T_from_H298",
    "S",
    "H_minus_H298",
    "delta_fG",
    "log10_Kf",
)
# Barin §2.2.6 defines log Kf as the “Logarithm of the equilibrium constant
# of the reaction for the formation of B from the elements.” The formation
# reaction uses the elements' reference phases at 1 bar. On physical p. 557
# (folio 442), CaAl2O4 SOL at 298.15 K prints ΔfG = −2208.820 kJ/mol and
# log Kf = 386.976. With R = 8.3143 J/(mol K),
# −(−2208.820 kJ/mol) × 1000 J/kJ /
# (8.3143 J/(mol K) × 298.15 K × ln 10) = 386.97609, which rounds to 386.976.
# The constants page (physical p. 104) separately prints R = 8.31441 J/(mol K);
# that gives 386.97097. Thus 8.3143 is the inferred table convention, supported
# by closure of all 115 printed rows within their input-rounding intervals.
R_BARIN_TABULATION_J_PER_MOL_K = Decimal("8.3143")
PHYSICAL_PAGE_BY_SPECIES = {
    "CaAl2O4": 557,
    "CaAl4O7": 558,
    "Ca3Al2O6": 560,
    "Ca12Al14O33": 560,
    "NaAlO2": 1248,
    "MgAl2O4": 1131,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _half_last_digit(value: object) -> Decimal:
    printed = Decimal(str(value))
    return Decimal(1).scaleb(printed.as_tuple().exponent) / 2


def _points_by_quantity(species: dict) -> dict[str, dict[tuple[str, Decimal], dict]]:
    points_by_quantity: dict[str, dict[tuple[str, Decimal], dict]] = {
        quantity: {} for quantity in IDENTITY_QUANTITIES
    }
    for observation in species["observations"]:
        values = observation.get("values", {})
        quantity = values.get("quantity")
        if quantity not in points_by_quantity:
            continue
        phase = observation["phase_label_as_printed"]
        for point in values["points"]:
            key = (phase, Decimal(point["T_K"]))
            assert key not in points_by_quantity[quantity]
            points_by_quantity[quantity][key] = point
    return points_by_quantity


def _identity_failures(document: dict) -> tuple[list[tuple[str, int, str]], int, int]:
    failures: list[tuple[str, int, str]] = []
    checked_rows = 0
    reference_rows = 0
    with localcontext() as context:
        context.prec = 50
        for species_id, expected_rows in ROW_COUNTS.items():
            points = _points_by_quantity(document["species"][species_id])
            reference = points["negative_gibbs_function_over_T_from_H298"]
            checked_rows += len(reference)
            if len(reference) != expected_rows:
                failures.append((species_id, 0, "wrong Gibbs row count"))
            reference_keys = set(reference)
            for quantity in IDENTITY_QUANTITIES[1:]:
                if set(points[quantity]) != reference_keys:
                    failures.append((species_id, 0, f"row set for {quantity}"))

            for phase, temperature in sorted(reference_keys):
                key = (phase, temperature)
                if any(key not in points[quantity] for quantity in IDENTITY_QUANTITIES):
                    continue
                gibbs_point = reference[key]
                entropy_point = points["S"][key]
                enthalpy_point = points["H_minus_H298"][key]
                delta_g_point = points["delta_fG"][key]
                log_k_point = points["log10_Kf"][key]
                t_raw = gibbs_point["T_K"]
                t = Decimal(t_raw)
                eps_t = _half_last_digit(t_raw)
                row_number = gibbs_point["locator"]["table_row_number"]
                row_number = int(row_number)
                locators = [
                    points[quantity][key]["locator"]
                    for quantity in IDENTITY_QUANTITIES
                ]
                for quantity, locator in zip(IDENTITY_QUANTITIES, locators):
                    if (
                        locator["page"] != PHYSICAL_PAGE_BY_SPECIES[species_id]
                        or int(locator["table_row_number"]) != row_number
                    ):
                        failures.append((species_id, row_number, f"locator {quantity}"))

                gibbs_function = Decimal(gibbs_point["value"])
                entropy = Decimal(entropy_point["value"])
                enthalpy = Decimal(enthalpy_point["value"])
                identity_one_residual = (
                    gibbs_function - entropy + Decimal(1000) * enthalpy / t
                )
                # For q rounded to half its last printed unit, independent
                # input intervals add in the worst case. For H/T, interval
                # propagation is bounded by eps_H/(T-eps_T) plus
                # |H|*eps_T/(T*(T-eps_T)); the factor 1000 converts kJ to J.
                identity_one_allowance = (
                    _half_last_digit(gibbs_point["value"])
                    + _half_last_digit(entropy_point["value"])
                    + Decimal(1000)
                    * (
                        _half_last_digit(enthalpy_point["value"])
                        / (t - eps_t)
                        + abs(enthalpy)
                        * eps_t
                        / (t * (t - eps_t))
                    )
                )
                if abs(identity_one_residual) > identity_one_allowance:
                    failures.append((species_id, row_number, "(i)"))

                delta_g = Decimal(delta_g_point["value"])
                log_k = Decimal(log_k_point["value"])
                calculated_log_k = battery_identity.log10K_from_delta_fG_kJ_mol(
                    delta_g,
                    t,
                    gas_constant_J_per_mol_K=R_BARIN_TABULATION_J_PER_MOL_K,
                )
                identity_two_residual = log_k - calculated_log_k
                delta_g_eps = _half_last_digit(delta_g_point["value"])
                possible_log_ks = (
                    battery_identity.log10K_from_delta_fG_kJ_mol(
                        bounded_delta_g,
                        bounded_temperature,
                        gas_constant_J_per_mol_K=R_BARIN_TABULATION_J_PER_MOL_K,
                    )
                    for bounded_delta_g in (delta_g - delta_g_eps, delta_g + delta_g_eps)
                    for bounded_temperature in (t - eps_t, t + eps_t)
                )
                identity_two_allowance = _half_last_digit(log_k_point["value"]) + max(
                    abs(possible_log_k - calculated_log_k)
                    for possible_log_k in possible_log_ks
                )
                if abs(identity_two_residual) > identity_two_allowance:
                    failures.append((species_id, row_number, "(ii)"))

            for phase, temperature in reference_keys:
                if temperature != Decimal("298.15"):
                    continue
                reference_rows += 1
                key = (phase, temperature)
                gibbs_point = reference[key]
                entropy_point = points["S"][key]
                enthalpy_point = points["H_minus_H298"][key]
                enthalpy = Decimal(enthalpy_point["value"])
                if abs(enthalpy) > _half_last_digit(enthalpy_point["value"]):
                    failures.append((species_id, 3, "(iii) H-H298"))
                difference = Decimal(gibbs_point["value"]) - Decimal(
                    entropy_point["value"]
                )
                allowance = _half_last_digit(gibbs_point["value"]) + _half_last_digit(
                    entropy_point["value"]
                )
                if abs(difference) > allowance:
                    failures.append((species_id, 3, "(iii) Gibbs function/S"))
    return failures, checked_rows, reference_rows


def _assert_naalo2_800_k_render_pin(document: dict) -> None:
    observation = next(
        observation
        for observation in document["species"]["NaAlO2"]["observations"]
        if observation["observation_id"]
        == "barin95_naalo2_sol_b_negative_gibbs_function_over_T_from_H298"
    )
    point = next(
        point for point in observation["values"]["points"] if point["T_K"] == "800.00"
    )
    # Direct pin from the corpus transcription at
    # tables/barin-1995-aluminates/barin-1995-aluminate-tables.md;
    # physical PDF page 1248, printed folio 1132.
    assert point["value"] == "101.036"
    assert point["locator"]["page"] == 1248
    assert point["locator"]["published_page"] == 1132
    assert point["locator"]["row"] == "SOL-B 800.00"
    assert point["locator"]["column"] == "-(G-H298)/T"
    assert point["locator"]["table_row_number"] == 12
    assert point["locator"]["table_column_number"] == 5
    assert point["point_conditions"]["temperature_K"]["locator"][
        "table_row_number"
    ] == 12


def test_naalo2_render_pin_is_present_in_corpus_transcription() -> None:
    if not CORPUS_TRANSCRIPTION.is_file():
        pytest.skip(f"missing corpus transcription: {CORPUS_TRANSCRIPTION}")

    transcription = CORPUS_TRANSCRIPTION.read_text(encoding="utf-8")
    assert (
        "| 12 | SOL-B | 800.00 | 98.581 | 159.133 | 101.036 | 46.477 | "
        "-1134.022 | -958.096 | 62.557 |"
    ) in transcription


def test_rendered_naalo2_800_k_pin_rejects_the_previous_transcription() -> None:
    document = _document()
    _assert_naalo2_800_k_render_pin(document)

    mutated = deepcopy(document)
    observation = next(
        observation
        for observation in mutated["species"]["NaAlO2"]["observations"]
        if observation["observation_id"]
        == "barin95_naalo2_sol_b_negative_gibbs_function_over_T_from_H298"
    )
    point = next(
        point for point in observation["values"]["points"] if point["T_K"] == "800.00"
    )
    point["value"] = "101.0836"
    with pytest.raises(AssertionError):
        _assert_naalo2_800_k_render_pin(mutated)


def test_barin_source_identity_closes_all_115_rows() -> None:
    failures, checked_rows, reference_rows = _identity_failures(_document())

    assert checked_rows == sum(ROW_COUNTS.values()) == 115
    assert reference_rows == 6
    assert failures == []


def test_barin_log_k_mutation_changes_the_identity_result() -> None:
    document = _document()
    original_failures, _, _ = _identity_failures(document)

    mutated = deepcopy(document)
    points = _points_by_quantity(mutated["species"]["CaAl2O4"])
    _, point = sorted(points["log10_Kf"].items())[0]
    row_number = int(point["locator"]["table_row_number"])
    point["value"] = "999.999"

    mutated_failures, _, _ = _identity_failures(mutated)

    assert mutated_failures != original_failures
    assert mutated_failures == [("CaAl2O4", row_number, "(ii)")]


def test_barin_identity_check_uses_shared_conversion_for_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_conversion = battery_identity.log10K_from_delta_fG_kJ_mol
    observed_calls: list[tuple[Decimal, Decimal, Decimal]] = []

    def record_conversion(
        delta_fG_kJ_mol: object,
        T_K: object,
        *,
        gas_constant_J_per_mol_K: object,
    ) -> Decimal:
        observed_calls.append(
            (
                Decimal(str(delta_fG_kJ_mol)),
                Decimal(str(T_K)),
                Decimal(str(gas_constant_J_per_mol_K)),
            )
        )
        return original_conversion(
            delta_fG_kJ_mol,
            T_K,
            gas_constant_J_per_mol_K=gas_constant_J_per_mol_K,
        )

    monkeypatch.setattr(
        battery_identity, "log10K_from_delta_fG_kJ_mol", record_conversion
    )
    document = _document()
    _identity_failures(document)

    expected_rows = Counter(
        (
            Decimal(point["value"]),
            Decimal(temperature),
        )
        for species_id in ROW_COUNTS
        for (_phase, temperature), point in _points_by_quantity(
            document["species"][species_id]
        )["delta_fG"].items()
    )
    observed_rows = Counter(
        (delta_g, temperature) for delta_g, temperature, _ in observed_calls
    )

    assert observed_rows >= expected_rows
    assert {gas_constant for _, _, gas_constant in observed_calls} == {
        R_BARIN_TABULATION_J_PER_MOL_K
    }
