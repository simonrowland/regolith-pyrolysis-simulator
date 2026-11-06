from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
HIT_ROWS = {
    "zaitsev-2006-magnesium-silicate-thermodynamics": {
        "zaitsev_2006_table4_enstatite_experimental_delta_fG": (
            "delta_fG",
            "ΔfG(298 K)",
            "formation from crystalline MgO and β-cristobalite",
            "−32 143",
        ),
        "zaitsev_2006_table4_enstatite_calculated_delta_fG": (
            "delta_fG",
            "ΔfG(298 K)",
            "formation from crystalline MgO and β-cristobalite",
            "−32 150",
        ),
        "zaitsev_2006_table4_protoenstatite_experimental_delta_fG": (
            "delta_fG",
            "ΔfG(298 K)",
            "formation from crystalline MgO and β-cristobalite",
            "−23 914",
        ),
        "zaitsev_2006_table4_protoenstatite_calculated_delta_fG": (
            "delta_fG",
            "ΔfG(298 K)",
            "formation from crystalline MgO and β-cristobalite",
            "−23 882",
        ),
    }
}
FROM_OXIDES_SCHEMA_GAP = (
    "No quantity token represents formation from simple oxides; "
    "delta_fG denotes formation from the elements."
)


def _records(document):
    result = {}

    def visit(node):
        if isinstance(node, dict):
            if "observation_id" in node:
                result[node["observation_id"]] = node
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return result


def test_zaitsev_table4_from_oxides_gibbs_rows_migrate_unavailable():
    for source_id, expected in HIT_ROWS.items():
        path = ROOT / "data/literature/extracts" / f"{source_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = _records(document)
        assert {gap["observation_id"] for gap in document["schema_gaps"]} == set(
            expected
        )
        for observation_id, (
            old_token,
            printed,
            reference,
            printed_value,
        ) in expected.items():
            values = rows[observation_id]["values"]
            assert old_token not in values
            assert values["quantity_as_printed"] == printed
            assert values["reference_state_as_printed"] == reference
            assert values["schema_gap"] == FROM_OXIDES_SCHEMA_GAP
            assert values["value_as_printed"] == printed_value

        migrator = Migrator(root=ROOT, index={}, aliases={})
        migrator._migrate_extract(path)
        migrator.finalize()
        for observation_id in expected:
            migrated = migrator.result.observations[
                f"{source_id}::{observation_id}"
            ]
            assert not migrated.identity.quantity.is_value
            assert migrated.value.kind.value == "unavailable"
