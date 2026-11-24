"""Restore one canon-fold-6 defect and require its pin to fail.

One mutation per fix. ``anchors`` only checks that each replacement still
matches the tree. A named mutation rewrites that fix back to the defect,
runs the pin, and restores the files.

    python tests/mutations/canon_fold6_restore.py anchors
    python tests/mutations/canon_fold6_restore.py oxygen-provenance

The pytest command uses a node id, so it is not a remote-wrapper command.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]

_OXYGEN_PROVENANCE = (
    (
        ROOT / "simulator/battery/fo2_ladder.py",
        """    for route in oxygen.routes:
        if _is_tier1(route, observation) and _log_point(route.value) is not None:
            return route
""",
        """    for route in oxygen.routes:
        if _log_point(route.value) is not None and (
            _is_tier1(route, observation)
            or route.route == "observation_fO2_Pa_to_log_fO2"
        ):
            return route
""",
    ),
    (
        ROOT / "simulator/battery/score.py",
        """    fo2_log = (
        _printed_fo2_log10_bar(resolved_identity)
        if _dp112_receipts(resolved_observation)
        else None
    )
""",
        """    fo2_log = _printed_fo2_log10_bar(resolved_identity)
""",
    ),
)

_RUN_OXYGEN = (
    (
        ROOT / "simulator/battery/waypoints.py",
        """    for name, route_name in (
        ("fO2_Pa", ROUTE_EXPERIMENT_FO2_PA),
        (
            "oxygen_partial_pressure_Pa",
            ROUTE_EXPERIMENT_OXYGEN_PARTIAL_PRESSURE_PA,
        ),
    ):
        printed_pa = _printed_run_oxygen_pa(experiment, name)
        if printed_pa is None:
            continue
        value = _log_pressure(printed_pa)
        if value is None:
            continue
        routes.append(Waypoint(
            "oxygen_condition",
            value,
            route_name,
            WaypointAuthority.DERIVED,
            (f"experiment.conditions.{name}",),
        ))
""",
        "",
    ),
)

_AIR = (
    (
        ROOT / "simulator/battery/oxygen_identity.py",
        """    atmosphere = _atmosphere_kind(normalized_quote)
    if atmosphere is not None and atmosphere[0] == "air":
        return source_quote
    if _AIR_NEGATION.search(normalized_quote):
        return None
""",
        "",
    ),
)

_PRESSURE = (
    (
        ROOT / "simulator/battery/score.py",
        """        waypoint = _prediction_pressure_waypoint(
            pressure_boundary(experiment, bench, observation),
            experiment.method,
        )
""",
        """        waypoint = pressure_boundary(experiment, bench, observation).selected
""",
    ),
    (
        ROOT / "simulator/battery/score.py",
        """        policy_pa, policy_notice = _dp002_pressure_default(
            experiment.ambient_pressure_evidence,
            quantity,
            atmosphere_condition=(observation.point_conditions or {}).get(
                "atmosphere"
            ),
        )
        if policy_pa is not None:
            return (
                replace(identity, total_pressure_Pa=State.of(policy_pa)),
                policy_pa / PA_PER_BAR_DEC,
                () if policy_notice is None else (policy_notice,),
            )
""",
        "",
    ),
)

_PHASE_CENSUS = (
    (
        ROOT / "simulator/battery/engine_capability.py",
        """    if quantity in PHASE_ROW_QUANTITIES and not phase_route_entered(
        quantity, phase_basis
    ):
        return False
    return engine_covers_scoring_route(engine, quantity)
""",
        """    del phase_basis
    return engine_covers_scoring_route(engine, quantity)
""",
    ),
)

_LIQUID_PAIR = (
    (
        ROOT / "simulator/battery/score.py",
        """    if label.casefold() not in _LIQUID_PAIR_CONJUGATES:
        return None
    return label, _LIQUID_PAIR_CONJUGATES[label.casefold()]
""",
        """    assemblage = provenance.get("phase_assemblage_as_printed")
    source_text = " ".join(
        value.casefold().replace("-", " ")
        for value in (assemblage, locator_note)
        if isinstance(value, str)
    )
    marker_present = any(
        marker in source_text
        for marker in ("immiscib", "coexisting liquid", "two liquid", "liquid pair")
    )
    if label.casefold() not in _LIQUID_PAIR_CONJUGATES and not marker_present:
        return None
    if not label:
        label = "reported liquid"
    return label, _LIQUID_PAIR_CONJUGATES.get(
        label.casefold(), "the other coexisting liquid"
    )
""",
    ),
)

_OXYGEN_FRAME = (
    (
        ROOT / "simulator/battery/fo2_ladder.py",
        """        return (
            route.authority is WaypointAuthority.PRINTED
            and absolute_oxygen_frame(_identity(observation))
        )
""",
        """        return route.authority is WaypointAuthority.PRINTED
""",
    ),
)

_BUFFER_RECEIPT = (
    (
        ROOT / "simulator/battery/fo2_ladder.py",
        """        marker = True
    return marker
""",
        """        marker = True
    return False
""",
    ),
)

_CONVERSION = (
    (
        ROOT / "simulator/battery/waypoints.py",
        """    located = experiment.conditions.get(name)
    if located is None or not located.state.is_value:
        return None
    from simulator.battery.source_oxygen import pressure_has_only_unit_conversion

    if not pressure_has_only_unit_conversion(located):
        return None
    note = located.locator.note if located.locator is not None else None
""",
        """    located = experiment.conditions.get(name)
    if located is None or not located.state.is_value:
        return None
    note = located.locator.note if located.locator is not None else None
""",
    ),
)

_AIR_FREE = (
    (
        ROOT / "simulator/battery/oxygen_identity.py",
        """    sample_air = re.search(
        r"\\b(?:sample|melt|glass|charge)\\b(?:\\s+\\w+){0,8}\\s+"
        r"(?:in|under|atmosphere)\\s+(?:ambient\\s+)?air\\b(?!\\s+free\\b)",
        normalized_quote,
    )
    equilibration_air = re.search(
        r"\\bequilibrat\\w*(?:\\s+\\w+){0,5}\\s+(?:in|under)\\s+(?:ambient\\s+)?air\\b"
        r"(?!\\s+free\\b)",
        normalized_quote,
    )
    sensor_or_room = re.search(r"\\b(?:sensor|room)\\b", normalized_quote)
    positive = sample_air if sample_air is not None else equilibration_air
    if positive is None or sensor_or_room is not None:
        return None
    if re.search(
        r"\\b(?:argon|quenched|quench|displaced|replaced|before|after|then)\\b",
        positive.group(0),
    ):
        return None
    return source_quote
""",
        """    equilibration_air = re.search(
        r"\\bequilibrat\\w*(?:\\s+\\w+){0,5}\\s+(?:in|under)\\s+(?:ambient\\s+)?air\\b",
        normalized_quote,
    )
    air_during_equilibration = re.search(
        r"\\bair\\b.{0,60}\\b(?:during|throughout)\\s+equilibrat\\w*",
        normalized_quote,
    )
    if (
        equilibration_air is not None
        and not re.search(
            r"\\b(?:argon|quenched|quench|displaced|replaced|before|after|then)\\b",
            equilibration_air.group(0),
        )
    ) or air_during_equilibration is not None:
        return source_quote
""",
    ),
)

_PHASE_LABEL = (
    (
        ROOT / "simulator/battery/score.py",
        """        raw_label = distinct[0] if len(distinct) == 1 else None
""",
        """        raw_label = distinct[0] if distinct else None
""",
    ),
)

_UNCOVERED_COUNT = (
    (
        ROOT / "simulator/battery/engine_capability.py",
        """    uncovered_pair_total = omitted_emission_pairs
""",
        """    uncovered_pair_total = sum(
        row["n_observations"] for row in rows if not row["covered"]
    )
""",
    ),
)

_SUMMARY_ORDER = (
    (
        ROOT / "simulator/battery/score.py",
        """                json.dumps(
                    [
                        stable_order[0],
                        stable_order[1],
                        stable_order[2],
                        stable_order[3],
                        numeric_value,
                        band_value,
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
""",
        """                json.dumps(
                    list(stable_order),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
""",
    ),
)

MUTATIONS = {
    "oxygen-provenance": (
        _OXYGEN_PROVENANCE,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_xu_lvp1_phase_row_uses_dp010_and_detects_missing_fo2_mutation",
        "derived_log",
    ),
    "run-oxygen": (
        _RUN_OXYGEN,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_xu_lvp1_phase_row_uses_dp010_and_detects_missing_fo2_mutation",
        "experiment_log",
    ),
    "air-negation": (
        _AIR,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_dp002_applicability_uses_registered_pressure_evidence",
        "Air",
    ),
    "pressure-precedence": (
        _PRESSURE,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_registered_method_pressure_precedes_the_unbound_default",
        "DP-002",
    ),
    "phase-census": (
        _PHASE_CENSUS,
        "tests/battery/test_engine_capability.py::"
        "test_phase_route_census_matches_production_dispatch",
        "callable_observations",
    ),
    "liquid-pair": (
        _LIQUID_PAIR,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_borisov_veksler_two_liquid_target_gets_the_real_capability_refusal",
        "unlabelled",
    ),
    "oxygen-frame": (
        _OXYGEN_FRAME,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands",
        "printed_fO2_log",
    ),
    "buffer-receipt": (
        _BUFFER_RECEIPT,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands",
        "buffer_relation",
    ),
    "conversion": (
        _CONVERSION,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands",
        "original_unit",
    ),
    "air-free": (
        _AIR_FREE,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_dp002_applicability_uses_registered_pressure_evidence",
        "air-free",
    ),
    "phase-label": (
        _PHASE_LABEL,
        "tests/battery/test_printed_oxygen_identity.py::"
        "test_borisov_veksler_two_liquid_target_gets_the_real_capability_refusal",
        "phase Lfe and phase Lsi",
    ),
    "uncovered-count": (
        _UNCOVERED_COUNT,
        "tests/battery/test_engine_capability.py::"
        "test_phase_route_census_matches_production_dispatch",
        "uncovered_pair_total",
    ),
    "summary-order": (
        _SUMMARY_ORDER,
        "tests/battery/test_score_workers.py::"
        "test_parallel_scoring_matches_serial_rows_and_summary",
        "parallel_summary",
    ),
}


def _count(path: Path, needle: str) -> int:
    return path.read_text(encoding="utf-8").count(needle)


def anchors_hold() -> None:
    for name, (edits, _node, _marker) in MUTATIONS.items():
        for path, old, _new in edits:
            found = _count(path, old)
            if found != 1:
                raise SystemExit(f"{name}: {path.name} anchor count {found}, expected 1")
    print(f"anchors: {len(MUTATIONS)} mutations, each anchor once")


def _apply(edits) -> dict[Path, str]:
    saved: dict[Path, str] = {}
    for path, old, new in edits:
        if path not in saved:
            saved[path] = path.read_text(encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != 1:
            raise SystemExit(f"{path.name}: replacement count {found}, expected 1")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return saved


def _restore(saved: dict[Path, str]) -> None:
    for path, text in saved.items():
        path.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"anchors", *MUTATIONS}:
        names = ", ".join(("anchors", *MUTATIONS))
        raise SystemExit(f"usage: canon_fold6_restore.py {{{names}}}")
    if argv[0] == "anchors":
        anchors_hold()
        return 0
    edits, node, marker = MUTATIONS[argv[0]]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    saved = _apply(edits)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        _restore(saved)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        raise SystemExit(f"{argv[0]}: pin did not fail\n" + output[-2000:])
    if marker in output and "AssertionError" in output:
        print(f"PASS: {argv[0]} restores the defect and fails {node}")
        return 0
    raise SystemExit(
        f"{argv[0]}: collection, infrastructure, or a different assertion\n"
        + output[-2000:]
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
