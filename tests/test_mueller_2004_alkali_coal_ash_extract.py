from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/mueller-2004-alkali-oxide-activity-coal-ash.yaml"
)


def test_mueller_2004_text_anchors_and_table_rows_match_rendered_page(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    }

    expected = {
        "mueller_2004_na_pressure_untreated_1400c": ("Na", 0.6),
        "mueller_2004_na_pressure_tio2_1400c": ("Na", 0.42),
        "mueller_2004_k_pressure_untreated_1400c": ("K", 0.59),
        "mueller_2004_k_pressure_tio2_1400c": ("K", 0.375),
    }
    assert len(rows) == 4
    for observation_id, (gas_species, pressure) in expected.items():
        row = rows[observation_id]
        assert row["locator"]["page"] == 2  # rendered physical page; printed p. 616
        assert row["values"]["gas_species"] == gas_species
        point = row["values"]["points"][0]
        assert point["p_Pa"] == pressure
        assert point["T_C"] == 1400
        assert point["point_conditions"]["temperature"]["locator"]["page"] == 2

    table_context = document["species"]["Slag"]["context"]
    assert len(table_context[1]["values"]["rows"]) == 6
    assert len(table_context[0]["values"]["components_as_printed"]) == 10
    assert table_context[1]["values"]["rows"][3]["NBO_T_as_printed"] == -0.025
    assert table_context[1]["values"]["rows"][5]["NBO_T_as_printed"] == -0.064

    # The paper gives only a global pressure upper bound, not an exact pressure,
    # and gives no independent fO2 condition.
    assert all(
        point["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        and point["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 1
        and "fO2" not in point["point_conditions"]
        for row in rows.values()
        for point in row["values"]["points"]
    )

    # Run the production migrator on an isolated copy. The source does not
    # state exact total pressure or independent fO2. 742826248 (t-1609)
    # identifies CO2, so the untreated Table I map is stored whole, including
    # CO2 and SO3. The TiO2 series prints no complete mixture, so its
    # composition stays unknown.
    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(
        tmp_path, "mueller-2004-alkali-oxide-activity-coal-ash.yaml", write=True
    )
    assert len(migrated.observations) == 4
    printed = table_context[0]["values"]["components_as_printed"]
    printed_total = sum(
        (Decimal(str(amount)) for amount in printed.values()), Decimal("0")
    )
    assert printed_total == Decimal("93.136")
    for observation in migrated.observations.values():
        assert observation.identity.temperature_K.value == Decimal("1673.15")
        assert observation.identity.total_pressure_Pa.tag.value == "unknown"
        assert observation.identity.fO2_Pa.tag.value == "unknown"
        stem = next(
            observation_id
            for observation_id in expected
            if f"::{observation_id}::" in observation.observation_id
        )
        composition = observation.identity.composition
        if stem.endswith("_untreated_1400c"):
            assert composition.is_unknown
            assert composition.reason.endswith(
                "kept sum=93.136 wt% is below 95 wt%"
            )
        else:
            assert composition.tag.value == "unknown"
            assert composition.reason == "no composition mapped from source"
    composition_gaps = [
        issue.path
        for issue in migrated.validation.issues
        if str(issue.path).endswith("identity.composition")
    ]
    # The two untreated rows now retain green's below-95 wt% composition
    # refusal as identity-incomplete issues.
    assert len(migrated.validation.issues) == 12
    assert len(composition_gaps) == 4
    assert {"tio2" in str(path) for path in composition_gaps} == {False, True}
    assert len(migrated.registry_issues) == 0
    # Green's grounded-unprinted-input handling keeps these four derivations
    # on the observations instead of leaving derived_from queue entries.
    assert migrated.queue == []
    assert all(observation.derivation is not None for observation in migrated.observations.values())
