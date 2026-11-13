from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, PerBasis, Phase, Quantity, ValueKind
from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/stuve-pankratz-richardson-1971-ri7535-sodium-ferrite.yaml"
OBSERVATION_ID = "stuve_1971_table8_nafeo2_delta_fG_1000K"
DISCUSSION_ID = "stuve_1971_discussion_nafeo2_koehler_citation"


def test_nafeo2_table8_value_and_conditions_survive_isolated_migration(
    tmp_path: Path,
) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = doc["species"]["NaFeO2"]["observations"]
    assert len(rows) == 1
    source_row = rows[0]

    # Independent pins from the PDF page 18 render (printed page 11), Table 8.
    assert source_row["values"]["value"] == "-60.1"
    assert source_row["values"]["T_K"] == "1000"
    assert source_row["values"]["per_as_printed"] == "per gram-atom of oxygen"
    assert source_row["values"]["attribution"] == "Koehler and coworkers (10)."
    assert source_row["phase"] == "cr"
    assert source_row["values"]["phase_as_printed"] == "NaFeO₂(c)"
    assert source_row["locator"]["page"] == 18
    assert source_row["locator"]["published_page"] == 11
    assert source_row["locator"]["table"] == "8"
    assert source_row["values"]["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "1000"
    assert set(source_row["values"]["point_conditions"]) == {"temperature_K"}

    # The rendered Table 8 has five body rows; only body row 3 is NaFeO2.
    # The extract stores exactly the one in-scope NaFeO2 row.
    assert len(rows) == 1

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=doc,
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 1
    observation = next(iter(result.observations.values()))
    assert observation.observation_id.endswith(f"::{OBSERVATION_ID}")
    assert observation.identity.quantity.value is Quantity.DELTA_FG
    assert observation.identity.species.formula == "NaFeO2"
    assert observation.identity.species.phase.value is Phase.CR
    assert observation.identity.per.value is PerBasis.MOL_ATOM
    assert observation.value.kind is ValueKind.POINT
    # The migrator converts the printed kcal value using the report's stated
    # 1 cal = 4.1840 J; the source value remains pinned above as -60.1.
    assert observation.value.point == Decimal("-251.4584")
    assert observation.identity.temperature_K.value == Decimal("1000")
    condition_temperature = observation.point_conditions["temperature_K"].state.value
    assert condition_temperature.kind is ValueKind.POINT
    assert condition_temperature.point == Decimal("1000")
    assert observation.point_conditions["temperature_K"].locator.page == 18
    assert observation.point_conditions["temperature_K"].locator.table == "8"
    assert observation.locator.page == 18
    assert observation.locator.published_page == 11
    assert observation.locator.table == "8"
    assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert observation.evidence.attribution == "Koehler and coworkers (10)."

    # The discussion's distinct page/section locator independently supports
    # the attribution and is carried into the isolated work context.
    context_rows = [row for rows in result.context_by_work.values() for row in rows]
    context = next(
        row for row in context_rows if row["context_id"].endswith(DISCUSSION_ID)
    )
    assert context["locator"]["page"] == 18
    assert context["locator"]["section"] == "DISCUSSION"
    assert context["values"]["statement_as_printed"] == (
        "The thermodynamic properties of a simpler sodium iron(III) ferrite, "
        "NaFeO₂(c), were investigated by Koehler and coworkers (10)."
    )
    assert {
        (observation.locator.page, observation.locator.table),
        (context["locator"]["page"], context["locator"].get("table")),
    } == {
        (18, "8"),
        (18, None),
    }

    # The source prints no composition, total pressure, fO2, or standard
    # pressure condition. The delta_fG identity profile may mark unused axes
    # not applicable, but none may acquire a numeric value.
    assert observation.identity.composition is not None
    assert not observation.identity.composition.is_value
    assert not observation.identity.total_pressure_Pa.is_value
    assert not observation.identity.fO2_Pa.is_value
    assert not observation.identity.standard_pressure_Pa.is_value
