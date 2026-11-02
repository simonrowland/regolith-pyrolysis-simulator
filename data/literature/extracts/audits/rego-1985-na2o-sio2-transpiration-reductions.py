"""Recompute Rego et al. Table I pNa and Na2O activity from stored inputs."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

import yaml


SID = "rego-1985-na2o-sio2-transpiration"
EXTRACT = Path(__file__).parents[1] / f"{SID}.yaml"
MOLAR_MASS_NA_G_MOL = Decimal("22.98976928")
PA_PER_ATM = Decimal("101325")
CO_MOL_PER_DISPLAYED_UNIT = Decimal("0.01")


@dataclass(frozen=True)
class Reduction:
    run: int
    pna_exact_atm: Decimal
    pco_exact_atm: Decimal
    activity_exact: Decimal
    pna_stored_atm: Decimal
    pco_stored_atm: Decimal
    activity_stored: Decimal


def significant_digits(text: str) -> int:
    token = text.strip().replace(",", ".")
    if token.startswith(("+", "-")):
        token = token[1:]
    token = token.replace(".", "").lstrip("0")
    return max(1, len(token))


def round_significant(value: Decimal, digits: int) -> Decimal:
    if value.is_zero():
        return value
    quantum = Decimal(1).scaleb(value.adjusted() - digits + 1)
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def stored_decimal(value: Decimal) -> str:
    return format(value, ".{}E".format(max(1, len(value.as_tuple().digits) - 1)))


def plain_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def reductions(document: dict) -> list[Reduction]:
    observations = document["species"]["Na"]["observations"]
    input_rows = {
        int(row["values"]["run_number_as_published"]): row["values"]
        for row in observations
        if row["observation_id"].endswith("_inputs")
    }
    if set(input_rows) != set(range(1, 32)):
        raise ValueError(f"expected Table I runs 1–31, got {sorted(input_rows)}")

    result = []
    with localcontext() as context:
        context.prec = 50
        ln10 = Decimal(10).ln()
        for run, values in sorted(input_rows.items()):
            total_pressure = Decimal(values["P_atm_as_published"])
            sodium_mg = Decimal(values["Na_mg_in_leached_solution_as_published"])
            co_displayed = Decimal(values["CO_moles_collected_x10_minus2_as_published"])
            sigfigs = min(
                significant_digits(values["P_atm_as_published"]),
                significant_digits(values["Na_mg_in_leached_solution_as_published"]),
                significant_digits(values["CO_moles_collected_x10_minus2_as_published"]),
            )

            n_na = (sodium_mg / Decimal(1000)) / MOLAR_MASS_NA_G_MOL
            n_co = co_displayed * CO_MOL_PER_DISPLAYED_UNIT
            pna = (n_na * total_pressure) / (Decimal("2.5") * n_na + n_co)
            pco = total_pressure - pna
            log_k = Decimal("3.279") if run <= 15 else Decimal("4.089")
            log_activity = (Decimal(2) * pna.ln() + pco.ln()) / ln10 - log_k
            activity = (log_activity * ln10).exp()

            result.append(
                Reduction(
                    run=run,
                    pna_exact_atm=pna,
                    pco_exact_atm=pco,
                    activity_exact=activity,
                    pna_stored_atm=round_significant(pna, sigfigs),
                    pco_stored_atm=round_significant(pco, sigfigs),
                    activity_stored=round_significant(activity, sigfigs),
                )
            )
    return result


def replace_stored_field(text: str, observation_id: str, field: str, value: str) -> str:
    marker = f"    - observation_id: {observation_id}\n"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"missing observation {observation_id}")
    next_observation = text.find("\n    - observation_id: ", start + len(marker))
    end = len(text) if next_observation < 0 else next_observation + 1
    block = text[start:end]
    pattern = re.compile(rf"(?m)^        {re.escape(field)}: '[^']*'$")
    updated, count = pattern.subn(f"        {field}: '{value}'", block, count=1)
    if count != 1:
        raise ValueError(f"expected one {field} in {observation_id}, replaced {count}")
    return text[:start] + updated + text[end:]


def apply(path: Path, rows: list[Reduction]) -> None:
    text = path.read_text(encoding="utf-8")
    for row in rows:
        suffix = f"{row.run:02d}"
        text = replace_stored_field(
            text,
            f"rego_1985_table1_run_{suffix}_pna_eq4",
            "pressure_atm",
            stored_decimal(row.pna_stored_atm),
        )
        text = replace_stored_field(
            text,
            f"rego_1985_table1_run_{suffix}_pna_eq4",
            "p_CO_atm_as_calculated",
            stored_decimal(row.pco_stored_atm),
        )
        text = replace_stored_field(
            text,
            f"rego_1985_table1_run_{suffix}_activity_eq6",
            "activity",
            stored_decimal(row.activity_stored),
        )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="update the three stored reductions per run")
    args = parser.parse_args()

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = reductions(document)
    if args.apply:
        apply(EXTRACT, rows)
    print("run,pNa_exact_atm,pCO_exact_atm,activity_exact,pNa_stored_atm,pNa_stored_Pa,pCO_stored_atm,activity_stored")
    for row in rows:
        print(
            f"{row.run},{row.pna_exact_atm},{row.pco_exact_atm},{row.activity_exact},"
            f"{stored_decimal(row.pna_stored_atm)},{plain_decimal(row.pna_stored_atm * PA_PER_ATM)},"
            f"{stored_decimal(row.pco_stored_atm)},{stored_decimal(row.activity_stored)}"
        )


if __name__ == "__main__":
    main()
