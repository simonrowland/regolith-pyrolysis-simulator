"""Derive the binary silica mole fractions for Charles's printed liquidus rows."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

import yaml


OXIDE_BY_TABLE = {"I": "Li2O", "II": "Na2O", "III": "K2O"}
QUANTITIES = {"activity", "activity_coefficient", "partial_molar_enthalpy"}


def _composition_id(table: str, temperature: str) -> str:
    return f"charles_1967_table_{table.lower()}_liquidus_composition_{temperature}"


def _output_id(table: str, quantity: str, temperature: str) -> str:
    return f"charles_1967_table_{table.lower()}_sio2_{quantity}_{temperature}"


def _parents(table: str, quantity: str, temperature: str) -> list[str]:
    composition_id = _composition_id(table, temperature)
    if quantity == "activity":
        return [composition_id]
    if quantity == "activity_coefficient":
        return [
            _output_id(table, "activity", temperature),
            composition_id,
        ]
    if quantity == "partial_molar_enthalpy":
        return [_output_id(table, "activity_coefficient", temperature)]
    raise ValueError(f"unexpected Charles table quantity: {quantity}")


def _derivation(table: str, quantity: str) -> str:
    if quantity == "activity":
        return (
            f"Charles (1967) Eqs. (5)-(7), cryoscopic calculation from the Table "
            f"{table} liquidus composition and silica thermochemical inputs"
        )
    if quantity == "activity_coefficient":
        return "Charles (1967) Eq. (4), log gamma2 = log a2 - log X2"
    if quantity == "partial_molar_enthalpy":
        return "Charles (1967) Eq. (8), partial molar heat from log gamma2 at the liquidus"
    raise ValueError(f"unexpected Charles table quantity: {quantity}")


def _printed_decimal(text: str) -> Decimal:
    cleaned = str(text).strip().strip("*").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return Decimal(cleaned)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _composition_observation(
    table: str, temperature: str, x1_as_printed: str, locator: dict
) -> dict:
    oxide = OXIDE_BY_TABLE[table]
    x_alkali = _printed_decimal(x1_as_printed)
    x_silica = Decimal("1") - x_alkali
    alkali_text = _decimal_text(x_alkali)
    silica_text = _decimal_text(x_silica)
    composition = {
        "basis": "printed_mole_fraction",
        oxide: alkali_text,
        "SiO2": silica_text,
    }
    conditions = {
        "total_pressure_Pa": {
            "state": {"tag": "unknown", "reason": "not_published"},
            "locator": dict(locator),
        },
        "fO2_Pa": {
            "state": {"tag": "unknown", "reason": "not_published"},
            "locator": dict(locator),
        },
        "temperature_K": {
            "state": {"tag": "value", "value": temperature},
            "locator": dict(locator),
        },
        "composition": {
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_mole_fraction",
                    "amount_basis": "mole_fraction",
                    "components": [[oxide, alkali_text], ["SiO2", silica_text]],
                },
            },
            "locator": dict(locator),
        },
    }
    return {
        "observation_id": _composition_id(table, temperature),
        "type": "equilibrium_phase_composition",
        "locator": dict(locator),
        "point_conditions": conditions,
        "phase": "l",
        "units": "mole fraction",
        "values": {
            "quantity": "equilibrium_phase_composition",
            "phase_label": "melt",
            "basis": "mole_fraction",
            "component_amount": alkali_text,
            "component_amount_as_published": str(x1_as_printed),
            "component_name_as_published": "X1 (liquidus composition)",
            "temperature_K_as_published": temperature,
            "T_K": temperature,
            "composition_mol": composition,
            "composition_derivation": (
                "The source prints X1 for the binary liquidus; SiO2 is 1 - X1, "
                "calculated at full Decimal precision by the committed audit script."
            ),
            "method_class": "quoted_attributed",
            "admission_status": "admitted",
            "attribution": (
                f"Charles (1967), Table {table}; the source identifies this column "
                f"as liquidus composition from the {oxide}-SiO2 phase diagram."
            ),
        },
    }


def derive_compositions(document: dict) -> int:
    """Populate exact binary conditions and composition input observations."""

    output_rows = document["species"]["SiO2"]["observations"]
    composition_rows: dict[tuple[str, str], dict] = {}
    generated: dict[str, dict[str, dict]] = {
        oxide: {} for oxide in OXIDE_BY_TABLE.values()
    }

    for observation in output_rows:
        values = observation["values"]
        quantity = values.get("quantity")
        if quantity not in QUANTITIES:
            continue
        table = str(observation["locator"]["table"])
        oxide = OXIDE_BY_TABLE[table]
        temperature = str(values["T_K"])
        x1_as_printed = str(values["x1_as_printed"])
        locator = dict(observation["locator"])
        key = (table, temperature)
        if key not in composition_rows:
            composition_rows[key] = _composition_observation(
                table, temperature, x1_as_printed, locator
            )
        composition_id = _composition_id(table, temperature)
        composition_value = composition_rows[key]["point_conditions"]["composition"]["state"]["value"]
        conditions = observation.setdefault("point_conditions", {})
        for condition in ("total_pressure_Pa", "fO2_Pa"):
            conditions.setdefault(
                condition,
                {
                    "state": {"tag": "unknown", "reason": "not_published"},
                    "locator": dict(locator),
                },
            )
        conditions["temperature_K"] = {
            "state": {"tag": "value", "value": temperature},
            "locator": dict(locator),
        }
        conditions["composition"] = {
            "state": {"tag": "value", "value": composition_value},
            "locator": dict(locator),
        }
        values["point_conditions"] = conditions
        values["composition_mol"] = composition_rows[key]["values"]["composition_mol"]
        parents = _parents(table, quantity, temperature)
        values["derived_from"] = parents
        values["derivation"] = {
            "relation": _derivation(table, quantity),
            "inputs": parents,
            "output_unit": observation.get("units") or "as_published",
        }
        values["admission_status"] = "admitted"
        if quantity in {"activity", "activity_coefficient"}:
            # The printed cells are log10(a) and log10(gamma). printed_form tells the migrator to store
            # 10**x (simulator/battery/migrate.py::_printed_scalar_selection); without it the log itself
            # migrates as the activity (ror-chne P0, ror-chdy P1). Keep both fields.
            values["printed_form"] = "log10"
            values["printed_form_as_published"] = "log10"
            observation["standard_state"] = "pure liquid SiO2"
        generated[oxide][composition_id] = composition_rows[key]

    for oxide, by_id in generated.items():
        rows = list(by_id.values())
        if not rows:
            continue
        species = document["species"].setdefault(oxide, {"observations": []})
        existing = species.setdefault("observations", [])
        generated_ids = {row["observation_id"] for row in rows}
        existing[:] = [row for row in existing if row.get("observation_id") not in generated_ids]
        existing.extend(rows)
    return len(output_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("extract", type=Path)
    args = parser.parse_args()
    document = yaml.safe_load(args.extract.read_text(encoding="utf-8"))
    count = derive_compositions(document)
    args.extract.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8",
    )
    print(f"bound conditions and derived compositions for {count} table observations")


if __name__ == "__main__":
    main()
