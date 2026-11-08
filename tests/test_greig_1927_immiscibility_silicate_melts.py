from collections import Counter
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/greig-1927-ajs-immiscibility-silicate-melts-part1.yaml"


def _context_by_id(document):
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("context", [])
    }


def test_greig_1927_binary_rows_pin_render_cells_and_migrate_without_unknown_axes():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = _context_by_id(document)
    source_rows = {
        row["observation_id"]: row
        for row in document["species"]["SiO2"]["observations"]
    }

    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.queue) == 0
    assert len(migrator.result.observations) == 3
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in migrator.result.observations.values()
    ) == 0

    migrated = {
        key.rsplit("::", 1)[-1]: observation
        for key, observation in migrator.result.observations.items()
    }
    assert set(migrated) == {
        "greig1927_cristobalite_melting_point",
        "greig1927_t3_cao_sio2_cristobalite_two_liquids_T",
        "greig1927_t3_mgo_sio2_cristobalite_two_liquids_T",
    }
    assert [
        source_rows[row_id]["values"]["T_C"]
        for row_id in (
            "greig1927_cristobalite_melting_point",
            "greig1927_t3_cao_sio2_cristobalite_two_liquids_T",
            "greig1927_t3_mgo_sio2_cristobalite_two_liquids_T",
        )
    ] == [1713, 1698, 1695]
    assert [
        str(migrated[row_id].value.point)
        for row_id in (
            "greig1927_cristobalite_melting_point",
            "greig1927_t3_cao_sio2_cristobalite_two_liquids_T",
            "greig1927_t3_mgo_sio2_cristobalite_two_liquids_T",
        )
    ] == ["1986.15", "1971.15", "1968.15"]

    for row_id, observation in migrated.items():
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value.value == "transition_temperature"
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value.value == "glass"
        assert observation.identity.composition.is_not_applicable
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.is_not_applicable
        assert observation.identity.temperature_K.is_not_applicable
        raw = source_rows[row_id]
        assert raw["phase"] == "glass"
        assert raw["quench_statement"]["preservation"] == "not_stated"
        conditions = raw["point_conditions"]
        assert all("state" in item and "locator" in item for item in conditions.values())
        assert conditions["composition_wt_pct"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert conditions["fO2_Pa"]["state"]["tag"] == "unknown"

    uncertainty_counts = Counter(
        observation.uncertainty.kind.value
        for observation in migrated.values()
    )
    assert uncertainty_counts == Counter({"none": 2, "printed": 1})
    confidence_counts = Counter(
        observation.uncertainty.verbatim.get("confidence_level", "none")
        if isinstance(observation.uncertainty.verbatim, dict)
        else "none"
        for observation in migrated.values()
    )
    assert confidence_counts == Counter({"none": 2, "not_stated": 1})
    melting_uncertainty = migrated["greig1927_cristobalite_melting_point"].uncertainty
    assert melting_uncertainty.verbatim["value"] == "5"
    assert melting_uncertainty.verbatim["units"] == "°C"
    assert melting_uncertainty.verbatim["confidence_level"] == "not_stated"

    # Table III p. 14: these OCR-sensitive temperatures and products match the render.
    assert context["greig1927_t3_run4_15cao_85sio2"]["values"]["temperature_C_as_printed"] == 1697
    assert context["greig1927_t3_run13_15cao_85sio2"]["values"]["temperature_C_as_printed"] == 1696
    assert context["greig1927_t3_run13_15cao_85sio2"]["values"]["run_products_as_published"] == "Not sintered. Cristobalite + CaO—rich glass"
    table3_binary_cells = [
        row for row in context.values()
        if row["observation_id"].startswith("greig1927_t3_run")
        and any(
            marker in row["observation_id"]
            for marker in ("_15cao_", "_5cao_", "_15mgo_")
        )
    ]
    assert len(table3_binary_cells) == 18
    assert all(
        row["quench_statement"]["preservation"] == "not_stated"
        and row["quench_statement"]["locator"]["page"] == 4
        for row in table3_binary_cells
    )

    # Table IV p. 17: keep the printed charge results as brackets, not fitted limb points.
    table4a = [row for row in context.values() if row["observation_id"].startswith("greig1927_t4a_")]
    table4b = [row for row in context.values() if row["observation_id"].startswith("greig1927_t4b_")]
    assert len(table4a) == 10
    assert len(table4b) == 15
    assert all(
        row["quench_statement"]["preservation"] == "not_stated"
        and row["quench_statement"]["locator"]["page"] == 4
        for row in table4a + table4b
    )
    ca_two_glasses = context["greig1927_t4b_26p5cao_73p5sio2_q1"]["values"]
    ca_one_glass = context["greig1927_t4b_26p5cao_73p5sio2_q2"]["values"]
    assert (ca_two_glasses["temperature_C_as_printed"], ca_two_glasses["duration_min_as_printed"], ca_two_glasses["run_products_as_published"]) == (1700, 30, "2 glasses")
    assert (ca_one_glass["temperature_C_as_printed"], ca_one_glass["duration_min_as_printed"], ca_one_glass["run_products_as_published"]) == (1740, 20, "1 glass")
    assert "1710°" in ca_two_glasses["text_discrepancy_note"]

    # Table V p. 19: rendered composition cells, fused footnotes, and direction marks.
    mgo_silica = context["greig1927_t5_mgo_siliceous_glass"]["values"]
    cao_silica = context["greig1927_t5_cao_siliceous_glass"]["values"]
    assert mgo_silica["composition_mass_percent_as_published"] == {"MgO": "0.8", "SiO2": "99.2"}
    assert mgo_silica["refractive_index_sodium_light_as_printed"]["1744"] == "1.462"
    assert cao_silica["composition_mass_percent_as_published"] == {"CaO": "0.6", "SiO2": "99.4"}
    assert cao_silica["refractive_index_sodium_light_as_printed"]["1710"] == "1.460+"
    assert cao_silica["refractive_index_sodium_light_as_printed"]["1744"] == "1.462−"
    mgo_uncertainty = context["greig1927_t5_mgo_siliceous_glass"]["uncertainty"]
    cao_uncertainty = context["greig1927_t5_cao_siliceous_glass"]["uncertainty"]
    assert mgo_uncertainty["range"] == ["-0.5", "0.5"]
    assert mgo_uncertainty["value_as_published"] == "better than 0.5 per cent"
    assert cao_uncertainty["confidence_level"] == "not_stated"
    assert context["greig1927_t5_mgo_siliceous_glass"]["phase"] == "glass"
    assert context["greig1927_t5_mgo_siliceous_glass"]["quench_statement"]["preservation"] == "not_stated"
    assert len([row for row in context.values() if row["observation_id"].startswith("greig1927_t5_") and row["values"].get("system") in {"CaO-SiO2", "MgO-SiO2"}]) == 2

    silica_bounds = context["greig1927_cao_mgo_siliceous_liquid_bounds"]["values"]
    assert silica_bounds["systems"] == {
        "MgO-SiO2": {"silica_mass_percent_as_printed": "> 98"},
        "CaO-SiO2": {"silica_mass_percent_as_printed": "> 98"},
    }

    # Page 20 Fig. 12 prose and page 44 comparison are retained as separate charges.
    ribbon_5_95 = context["greig1927_ribbon_two_liquids"]
    assert ribbon_5_95["values"]["composition_mass_percent"] == {"CaO": 5, "SiO2": 95}
    assert ribbon_5_95["quench_statement"]["preservation"] == "asserted_altered"
    ten_gram = context["greig1927_ten_gram_quench_comparison"]["values"]
    assert ten_gram["composition_mass_percent"] == {"CaO": 37, "SiO2": 63}
    assert ten_gram["charge_mass_g_as_printed"] == 10
    assert "1700° is a viscosity-comparison condition" in ten_gram["author_comparison"]

    # Page 44 prose is kept as printed approximate liquid data, not promoted to a conjugate endpoint.
    ribbon = context["greig1927_cao_sio2_27p5_liquid_liquidus_text"]["values"]
    assert ribbon["composition_mass_percent_as_published"] == {"CaO": "about 27.5", "SiO2": "72.5"}
    assert ribbon["ribbon_temperature_C_as_printed"] == "about 1725"
    assert ribbon["liquidus_temperature_C_as_printed"] == 1698
    assert context["greig1927_cao_sio2_27p5_liquid_liquidus_text"]["quench_statement"]["preservation"] == "asserted_altered"

    # The interpolation inputs have no exact scored refractive-index quantity, so these rows remain refused context.
    assert "greig1927_t5_mgo_siliceous_glass" not in migrated
    assert context["greig1927_t5_mgo_siliceous_glass"]["type"] == "phase_composition_reduction_lineage_refused"
    assert "greig1927_t5_cao_siliceous_glass" not in migrated
    assert context["greig1927_t5_cao_siliceous_glass"]["type"] == "phase_composition_reduction_lineage_refused"
