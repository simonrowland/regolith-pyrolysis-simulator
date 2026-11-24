"""Restore one t-1969 regression and require its expected regression to fail.

Run this only in a disposable Studio test tree, with the required native-library,
matplotlib, and MPI temp-directory environment set before starting Python.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / "simulator/battery/migrate.py"
PROVENANCE = ROOT / "simulator/battery/input_provenance.py"
SCORE = ROOT / "simulator/battery/score.py"
HENAO_TEST = ROOT / "tests/test_henao_2001_ni_cr_mn_co_cu_slag_distribution_extract.py"
ABDEYAZDAN_TEST = (
    ROOT / "tests/test_abdeyazdan_2026_na_sn_si_phase_thermodynamics_extract.py"
)
LI_TEST = ROOT / "tests/test_li_2005_na_al_dephos_extract.py"
TANIGUCHI_TEST = ROOT / "tests/test_taniguchi_1997_feo_cas_extract.py"
PHASE_ROWS_TEST = ROOT / "tests/battery/test_phase_composition_rows.py"
MASS_PERCENT_ORDER_TEST = ROOT / "tests/battery/test_t1969_malformed_mass_percent_order.py"
CHARGE_TEST = ROOT / "tests/battery/test_slag_metal_charge.py"

TESTS_BY_MUTATION = {
    "phase-label": (ABDEYAZDAN_TEST,),
    "phase-unknown": (LI_TEST, TANIGUCHI_TEST),
    "phase-role": (HENAO_TEST,),
    "basis-before-conversion": (PHASE_ROWS_TEST,),
    "alloy-capability": (HENAO_TEST,),
    "charge-precedence": (CHARGE_TEST,),
    "malformed-metal-mass-percent": (MASS_PERCENT_ORDER_TEST,),
    "role-provenance": (HENAO_TEST,),
    "test-local": (HENAO_TEST,),
    "locator-row": (HENAO_TEST,),
}
EXPECTED_FAILURES = {
    "phase-label": (
        (
            "test_isolated_migration_preserves_phase_conditions_and_printed_values",
            "explicit source phase must outrank phase role",
        ),
    ),
    "phase-unknown": (
        (
            "test_li_2005_tables_render_values_and_migrated_conditions",
            "explicit source phase must remain unknown",
        ),
        (
            "test_isolated_migration_retains_typed_rows_and_located_conditions",
            "explicit source phase must remain unknown",
        ),
    ),
    "phase-role": (
        (
            "test_henao_tables_migrate_as_typed_compositions_with_conditions",
            "phase roles must fill only rows whose source-phase field is absent",
        ),
    ),
    "basis-before-conversion": (
        (
            "test_mass_percent_basis_precedes_unparseable_component_conversion",
            "mass-percent basis refusal must precede composition conversion",
        ),
        (
            "test_all_zero_mass_percent_phase_keeps_basis_refusal",
            "all-zero mass-percent phase must retain its basis refusal",
        ),
        (
            "test_metal_capability_precedes_unparseable_component_conversion",
            "metal-alloy capability refusal must precede composition conversion",
        ),
    ),
    "alloy-capability": (
        (
            "test_henao_tables_migrate_as_typed_compositions_with_conditions",
            "usable alloy map must reach the adapter capability refusal",
        ),
    ),
    "charge-precedence": (
        (
            "test_oxide_engines_refuse_the_metal_without_calling_the_assemblage",
            "slag-metal charge refusal must precede alloy capability refusal",
        ),
    ),
    "malformed-metal-mass-percent": (
        (
            "test_malformed_metal_mass_percent_keeps_basis_refusal",
            "mass-percent validation must not call composition_wt_pct",
        ),
    ),
    "role-provenance": (
        (
            "test_henao_tables_migrate_as_typed_compositions_with_conditions",
            "ROLE fallback must be role-derived provenance, never printed_phase",
        ),
    ),
    "test-local": (
        (
            "test_henao_tables_migrate_as_typed_compositions_with_conditions",
            "UnboundLocalError",
        ),
    ),
    "locator-row": (
        (
            "test_henao_tables_migrate_as_typed_compositions_with_conditions",
            "printed composition locator must match the observation row",
        ),
    ),
}


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected one mutation anchor in {path}")
    path.write_text(text.replace(old, new, 1))


def _phase_role_fallback_block() -> str:
    return (
        "            if (\n"
        "                phase_role_token is not None\n"
        "                and source_phase_state is _PhaseSourceState.ABSENT\n"
        "            ):\n"
        "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n"
        "                unmapped_phase = None\n"
    )


def restore(mutation: str) -> None:
    if mutation == "phase-label":
        replace_once(
            MIGRATE,
            _phase_role_fallback_block(),
            "            if phase_role_token is not None:\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n"
            "                unmapped_phase = None\n",
        )
    elif mutation == "phase-unknown":
        replace_once(
            MIGRATE,
            _phase_role_fallback_block(),
            "            if phase_role_token is not None and phase.is_unknown:\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n"
            "                unmapped_phase = None\n",
        )
    elif mutation == "phase-role":
        replace_once(
            MIGRATE,
            _phase_role_fallback_block(),
            "            if phase_raw is None or phase_raw == \"\":\n"
            "                assert phase_role_token is not None\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n"
            "                unmapped_phase = None\n",
        )
    elif mutation == "basis-before-conversion":
        conversion = (
            "    wt = composition_wt_pct(composition_state.value)\n"
            "    if not wt:\n"
            "        return refused(\n"
            "            RefusalReason.IDENTITY_INCOMPLETE,\n"
            '            {"reason": "missing_input", "input": "composition"},\n'
            "            execution=ExecutionState.NOT_PROBED,\n"
            "        )\n"
        )
        replace_once(SCORE, conversion, "")
        capability_gate = (
            "    if role is PhaseRole.METAL:\n"
            "        from simulator.melt_backend.base import battery_adapter_class\n"
        )
        replace_once(SCORE, capability_gate, conversion + capability_gate)
    elif mutation == "alloy-capability":
        replace_once(
            SCORE,
            "    if role is PhaseRole.METAL:\n        from simulator.melt_backend.base import battery_adapter_class\n",
            "    if role is PhaseRole.METAL and not (\n"
            "        composition.amount_basis is AmountBasis.MASS_PERCENT\n"
            "        and sum(\n"
            "            (amount for _name, amount in composition.components), Decimal(0)\n"
            "        ) != Decimal(\"100\")\n"
            "    ):\n"
            "        from simulator.melt_backend.base import battery_adapter_class\n",
        )
    elif mutation == "charge-precedence":
        capability_gate = (
            "    if role is PhaseRole.METAL:\n"
            "        from simulator.melt_backend.base import battery_adapter_class\n"
        )
        capability_end = (
            "                    \"detail\": \"adapter does not declare metal-alloy phase support\",\n"
            "                },\n"
            "            )\n"
        )
        if SCORE.read_text().count(capability_gate) != 1:
            raise RuntimeError("expected one alloy capability gate")
        source = SCORE.read_text()
        start = source.index(capability_gate)
        end = source.index(capability_end, start) + len(capability_end)
        block = source[start:end]
        replace_once(SCORE, block, "")
        replace_once(
            SCORE,
            "    metal_charge = _unaccepted_metal_charge(engine, composition)\n",
            block + "    metal_charge = _unaccepted_metal_charge(engine, composition)\n",
        )
    elif mutation == "malformed-metal-mass-percent":
        validation = (
            "        valid_mass_percent_components = (\n"
            "            sum((amount for _name, amount in composition.components), Decimal(0)) > 0\n"
            "            and all(\n"
            "                parse_species_formula(name) is not None\n"
            "                for name, _ in composition.components\n"
            "            )\n"
            "        )\n"
        )
        replace_once(
            SCORE,
            validation,
            "        valid_mass_percent_components = composition_wt_pct(composition) is not None\n",
        )
    elif mutation == "role-provenance":
        replace_once(
            MIGRATE,
            "        observation_provenance = _provenance_with_printed_phase(\n"
            "            observation_provenance, source_phase_raw\n"
            "        )\n",
            "        observation_provenance = _provenance_with_printed_phase(\n"
            "            observation_provenance, phase_raw\n"
            "        )\n",
        )
        role_provenance = (
            "        if (\n"
            "            source_phase_raw in (None, \"\")\n"
            "            and isinstance(role_phase_raw, str)\n"
            "            and role_phase_raw.strip()\n"
            "        ):\n"
            "            selected_provenance = dict(observation_provenance or {})\n"
            "            selected_provenance[PHASE_SOURCE_KIND_KEY] = ROLE_DERIVED_PHASE_KIND\n"
            "            observation_provenance = selected_provenance\n"
        )
        replace_once(MIGRATE, role_provenance, "")
        replace_once(
            MIGRATE,
            "            phase_text=_phase_evidence_text(\n"
            "                source_phase_raw,\n",
            "            phase_text=_phase_evidence_text(\n"
            "                phase_raw,\n",
        )
    elif mutation == "test-local":
        census = (
            "    henao_experiments = [\n"
            "        row\n"
            "        for row in result.experiments.values()\n"
            "        if row.experiment_id.startswith(\n"
            '            "10.2320/matertrans.42.1959::experiment::henao:"\n'
            "        )\n"
            "    ]\n"
        )
        replace_once(
            HENAO_TEST,
            census,
            "    first_sweep = henao_experiments[0].pressure_environment.sweep_gas\n"
            + census,
        )
    elif mutation == "locator-row":
        replace_once(
            PROVENANCE,
            "if row_key is None or row_key != locator_row_identity(map_locator):",
            "if row_key is None:",
        )
    else:
        raise ValueError(mutation)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutation", choices=tuple(TESTS_BY_MUTATION))
    args = parser.parse_args()
    if not os.environ.get("TMPDIR"):
        parser.error("TMPDIR must point to the dispatch scratch directory")

    touched = {
        "phase-label": (MIGRATE,),
        "phase-unknown": (MIGRATE,),
        "phase-role": (MIGRATE,),
        "basis-before-conversion": (SCORE,),
        "alloy-capability": (SCORE,),
        "charge-precedence": (SCORE,),
        "malformed-metal-mass-percent": (SCORE,),
        "role-provenance": (MIGRATE,),
        "test-local": (HENAO_TEST,),
        "locator-row": (PROVENANCE,),
    }[args.mutation]
    test_paths = TESTS_BY_MUTATION[args.mutation]
    with tempfile.TemporaryDirectory(prefix="t1969-mutation-") as scratch:
        backups = {path: Path(scratch, path.name) for path in touched}
        for path, backup in backups.items():
            backup.write_bytes(path.read_bytes())

        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "4",
            "-p",
            "no:cacheprovider",
            *(str(test_path) for test_path in test_paths),
        ]

        def run_test() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        try:
            baseline = run_test()
            if baseline.returncode != 0:
                print(
                    f"unmodified regression tests failed for {args.mutation}:\n"
                    f"{baseline.stdout}{baseline.stderr}",
                    file=sys.stderr,
                )
                return 1
            print(
                "unmodified regression tests passed: "
                + ", ".join(test_path.name for test_path in test_paths)
            )

            restore(args.mutation)
            result = run_test()
            output = result.stdout + result.stderr
            if result.returncode == 0:
                print(f"mutation survived: {args.mutation}", file=sys.stderr)
                return 1
            failures = [
                line for line in output.splitlines() if line.startswith("FAILED ")
            ]
            errors = [
                line for line in output.splitlines() if line.startswith("ERROR ")
            ]
            expected_failures = EXPECTED_FAILURES[args.mutation]
            matched_failures = all(
                any(node in line and reason in line for line in failures)
                for node, reason in expected_failures
            )
            if (
                len(failures) != len(expected_failures)
                or errors
                or not matched_failures
            ):
                print(
                    f"mutation failed for an unexpected reason: {args.mutation}; "
                    f"expected failures {expected_failures!r}\n{output}",
                    file=sys.stderr,
                )
                return 1
            print(f"mutation detected: {args.mutation}")
            return 0
        finally:
            for path, backup in backups.items():
                path.write_bytes(backup.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
