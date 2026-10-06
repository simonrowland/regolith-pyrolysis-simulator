"""Translate Fegley 2023 Table 2 fits into the vapour-rail gamma table.

The runtime must not import the battery extract schema. This tool copies
each activity_coefficient_temperature_fit row, including the printed
standard-state phrase and, when the extract carries them, the stated
convention, phase, mole-fraction basis, and cite. It does not decide
whether those fields match a caller and it does not invent them. Origin
labels (published versus proxy) are classified by the activity owner,
not here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


EXTRACT_RELATIVE = (
    "data/literature/extracts-v2/"
    "fegley-2023-chemical-equilibrium-calculations-bu.yaml"
)
TABLE_RELATIVE = "data/vapour_rail/fegley2023_table2_gamma.json"
QUANTITY = "activity_coefficient_temperature_fit"
ROOT = Path(__file__).resolve().parents[1]


def _quantity(observation: dict[str, Any]) -> str:
    identity = observation.get("identity") or {}
    quantity = identity.get("quantity") or {}
    if isinstance(quantity, dict):
        return str(quantity.get("value") or "")
    return str(quantity)


def _formula(observation: dict[str, Any]) -> str:
    identity = observation.get("identity") or {}
    species = identity.get("species") or {}
    return str(species.get("formula") or "")


def _parameter_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"activity-coefficient parameter is not numeric: {value!r}")
    return format(value, ".16g")


def _parameters(observation: dict[str, Any]) -> dict[str, str]:
    value = observation.get("value") or {}
    raw = value.get("expression_parameters") or ()
    return {str(name): _parameter_text(number) for name, number in raw}


_COPIED_DOMAIN_TEXT = (
    "stated_convention",
    "stated_phase",
    "mole_fraction_basis",
    "stated_basis_cite",
)


def _optional_text(domain: dict[str, Any], key: str) -> str | None:
    value = domain.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _domain(observation: dict[str, Any]) -> dict[str, Any]:
    value = observation.get("value") or {}
    raw = value.get("expression_domain")
    if isinstance(raw, str):
        parsed = json.loads(raw)
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def build_table(extract_path: Path) -> dict[str, Any]:
    payload = extract_path.read_bytes()
    document = yaml.safe_load(payload)
    rows: list[dict[str, Any]] = []
    for observation in document.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        if _quantity(observation) != QUANTITY:
            continue
        if str((observation.get("value") or {}).get("expression_text") or "") != (
            "log10 γ = A + B/T"
        ):
            raise ValueError(
                f"{observation.get('observation_id')}: unexpected fit expression"
            )
        parameters = _parameters(observation)
        if "A" not in parameters or "B" not in parameters:
            raise ValueError(
                f"{observation.get('observation_id')}: fit is missing A or B"
            )
        domain = _domain(observation)
        band = domain.get("validity_range_K")
        if band is not None:
            band = [float(band[0]), float(band[1])]
        formula = _formula(observation)
        if not formula:
            raise ValueError(
                f"{observation.get('observation_id')}: fit has no formula"
            )
        printed_state = domain.get("standard_state_as_printed")
        if not isinstance(printed_state, str) or not printed_state.strip():
            raise ValueError(
                f"{observation.get('observation_id')}: fit has no printed "
                "standard state"
            )
        row = {
            "source_row_id": str(observation.get("observation_id") or ""),
            "formula": formula,
            "A": parameters["A"],
            "B": parameters["B"],
            "validity_range_K": band,
            "notes_as_printed": str(domain.get("notes_as_printed") or ""),
            "standard_state_as_printed": printed_state.strip(),
        }
        for key in _COPIED_DOMAIN_TEXT:
            copied = _optional_text(domain, key)
            if copied is not None:
                row[key] = copied
        rows.append(row)
    rows.sort(key=lambda row: (row["formula"], row["source_row_id"]))
    if not rows:
        raise ValueError(f"{extract_path}: no {QUANTITY} rows")
    return {
        "provenance": {
            "extract": EXTRACT_RELATIVE,
            "extract_sha256": hashlib.sha256(payload).hexdigest(),
            "quantity": QUANTITY,
            "generator": "tools/build_fegley2023_gamma_table.py",
            "row_count": len(rows),
        },
        "rows": rows,
    }


def table_text(table: dict[str, Any]) -> str:
    return json.dumps(table, indent=2, sort_keys=True) + "\n"


def main() -> None:
    extract_path = ROOT / EXTRACT_RELATIVE
    destination = ROOT / TABLE_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(table_text(build_table(extract_path)), encoding="utf-8")


if __name__ == "__main__":
    main()
