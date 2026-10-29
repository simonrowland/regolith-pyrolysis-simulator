"""Regenerate frozen per-parent activity parity bounds for the green oracle.

Run with the reviewed OpenIMCC source export on PYTHONPATH, for example:

    PYTHONPATH="$HOME/.cache/regolith-engine/oimcc-e1423d8/src" \
      python3 tests/helpers/generate_openimcc_activity_bounds.py

The generated bounds are committed data. Parity tests must read the sidecar
and must never call this derivation at test time.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import openimcc
from openimcc.kernel import active_residual_jacobian


REPO_ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = REPO_ROOT / "tests/fixtures/imcc_green_d9bd25f0b_activities.json"
OUTPUT_PATH = (
    REPO_ROOT / "tests/fixtures/imcc_green_d9bd25f0b_activity_bounds.json"
)
GENERATOR_PATH = "tests/helpers/generate_openimcc_activity_bounds.py"
OPENIMCC_COMMIT = "e1423d82237486f58dc8ffbb1f8c295f54f169c0"
ROUNDING_ALLOWANCE = 32.0 * np.finfo(np.float64).eps

DERIVATION = (
    "For oracle y_o and certified y_c, r_o=F(y_o), r_c=F(y_c), and J=DF(y_c). "
    "Taylor expansion gives y_c-y_o=-J^-1 r_o + J^-1 r_c + J^-1 R. "
    "The per-parent log-activity bound is |J^-1 r_o| + |J^-1 r_c| "
    "+ |J^-1| R_abs + 32*eps. R_abs uses the exponential Hessian envelope "
    "over the segment box verified to contain y_o-y_c."
)


def _read_json_with_header(path: Path) -> dict:
    return json.loads(
        "\n".join(
            line for line in path.read_text().splitlines()
            if not line.startswith("#")
        )
    )


def _root_error_terms(row: dict, pack, result) -> dict[str, dict[str, float]]:
    kernel = pack.kernel_datapack
    parent_x = np.asarray(result.parent_x, dtype=np.float64)
    active_parent = parent_x > 0.0
    active_complex = ~np.any(
        (kernel.nu > 0.0) & (~active_parent[:, None]), axis=0
    )
    nu = kernel.nu[active_parent, :][:, active_complex]
    x_target = parent_x[active_parent]
    stoichiometric_totals = nu.sum(axis=0)
    ln_k = math.log(10.0) * (
        kernel.A[active_complex]
        + kernel.B[active_complex] / float(row["temperature_K"])
    )
    names = [
        name
        for name, is_active in zip(result.parent_oxides, active_parent, strict=True)
        if is_active
    ]
    y_oracle = np.log(
        np.asarray(
            [float.fromhex(row["activities_hex"][name]) for name in names],
            dtype=np.float64,
        )
    )
    y_certified = np.log(
        np.asarray(result.parent_x_star, dtype=np.float64)[active_parent]
    )

    # For y=ln(free parent fraction), the kernel solves F(y)=0 and ln(activity)=y.
    # Taylor expansion at the certified root y_c gives
    # y_c-y_o = -J(y_c)^-1 F(y_o) + J(y_c)^-1 F(y_c) + J(y_c)^-1 R.
    # Keep both residual terms explicit; bound R with the Hessian envelope over
    # a box containing the measured y_c-y_o segment, then add the stated 32 eps.
    residual_oracle, _, _, _ = active_residual_jacobian(
        y_oracle, x_target, nu, ln_k, stoichiometric_totals
    )
    residual_certified, _, _, jacobian = active_residual_jacobian(
        y_certified, x_target, nu, ln_k, stoichiometric_totals
    )
    # Keep the certified root's own residual as a separate term. Its
    # contribution is J^-1 F(y_c), not a presumed zero from the certificate.
    oracle_term = -np.linalg.solve(jacobian, residual_oracle)
    certified_residual_term = np.linalg.solve(jacobian, residual_certified)
    radius = 2.0 * (
        np.abs(oracle_term) + np.abs(certified_residual_term)
    ) + ROUNDING_ALLOWANCE
    displacement = y_certified - y_oracle
    if not np.all(np.abs(displacement) <= radius):
        raise AssertionError(
            "oracle/certified root displacement exceeds the Taylor box for "
            f"{row['pack']} {row['composition']} {row['temperature_K']} K"
        )

    jacobian_inverse = np.linalg.solve(jacobian, np.eye(jacobian.shape[0]))
    upper_complex = np.exp(
        ln_k + nu.T @ y_certified + np.abs(nu).T @ radius
    )
    second_order_residual = np.empty(len(names), dtype=np.float64)
    for parent_index in range(len(names)):
        hessian_abs = np.zeros((len(names), len(names)), dtype=np.float64)
        hessian_abs[parent_index, parent_index] = math.exp(
            y_certified[parent_index] + radius[parent_index]
        )
        coefficient = np.abs(
            nu[parent_index]
            - x_target[parent_index] * (stoichiometric_totals - 1.0)
        ) * upper_complex
        hessian_abs += (nu * coefficient[None, :]) @ nu.T
        second_order_residual[parent_index] = 0.5 * np.sum(
            hessian_abs * radius[:, None] * radius[None, :]
        )
    second_order = np.abs(jacobian_inverse) @ second_order_residual

    return {
        name: {
            "oracle_residual_term": float(abs(oracle_term[index])),
            "certified_residual_term": float(
                abs(certified_residual_term[index])
            ),
            "second_order_allowance": float(second_order[index]),
            "floating_point_allowance": float(ROUNDING_ALLOWANCE),
            "total_log_activity_bound": float(
                abs(oracle_term[index])
                + abs(certified_residual_term[index])
                + second_order[index]
                + ROUNDING_ALLOWANCE
            ),
        }
        for index, name in enumerate(names)
    }


def main() -> None:
    # This source export has no Git metadata or distribution version. Print its
    # loaded path so the operator can verify the reviewed e1423d8 input.
    print(f"openimcc.__file__={openimcc.__file__}")
    sys.path[:0] = [str(REPO_ROOT / "tests"), str(REPO_ROOT)]
    import test_openimcc_bridge as bridge_tests

    oracle = _read_json_with_header(ORACLE_PATH)
    packs = {
        "v1.0.2": ("published-core", bridge_tests._load_pack("published-core")),
        "ext-v4": ("published-ext", bridge_tests._load_pack("published-ext")),
    }
    rows = []
    for row in oracle["rows"]:
        pack_name, pack = packs[row["pack"]]
        composition, basis_type = bridge_tests.COMPOSITIONS[row["composition"]]
        kwargs = {"basis_type": basis_type}
        if pack_name == "published-ext":
            kwargs["enable_sp_extension"] = True
        result = openimcc.evaluate(
            composition, row["temperature_K"], pack, **kwargs
        )
        rows.append(
            {
                "pack": row["pack"],
                "composition": row["composition"],
                "temperature_K": float(row["temperature_K"]),
                "parent_bounds": _root_error_terms(row, pack, result),
            }
        )

    output = {
        "schema": "imcc-green-activity-bounds.v1",
        "openimcc_commit": OPENIMCC_COMMIT,
        "generator": GENERATOR_PATH,
        "derivation": DERIVATION,
        "rounding_allowance_log": float(ROUNDING_ALLOWANCE),
        "rows": rows,
    }
    header = (
        "# Frozen per-parent log-activity bounds for the d9bd25f0b oracle.\n"
        f"# OpenIMCC commit: {OPENIMCC_COMMIT}\n"
        f"# Derivation: {DERIVATION}\n"
        f"# Generator: {GENERATOR_PATH}\n"
    )
    payload = header + json.dumps(output, indent=2, sort_keys=True) + "\n"
    OUTPUT_PATH.write_text(payload)
    print(
        f"wrote {len(rows)} rows to {OUTPUT_PATH}; "
        f"sha256={hashlib.sha256(payload.encode()).hexdigest()}"
    )


if __name__ == "__main__":
    main()
