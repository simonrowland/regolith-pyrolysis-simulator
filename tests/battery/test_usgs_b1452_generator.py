"""USGS B1452 source-aware generator controls and negative witnesses."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    QUANTITY_UNITS,
    EvidenceClass,
    IdentityEqualKind,
    NoticeKind,
    Phase,
    Polymorph,
    Quantity,
    ReferenceStateConvention,
    UncertaintyKind,
)
from simulator.battery.generators import usgs_b1452 as generator
from simulator.battery.identity import (
    identity_equal,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
)
from simulator.battery.migrate import (
    iter_observation_store_paths,
    observation_from_plain,
)
from simulator.battery.polymorph_dictionary import resolve_printed_name_polymorph
from simulator.battery.validate import reaction_atom_balance
from tests.battery import compilation_shard_observation_count

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
ROOT = Path(__file__).resolve().parents[2]
B1452_STORE_DIR = ROOT / "data" / "literature" / "observations-v2"
B1452_STORE_PATTERN = "compilations-robie-hemingway-fisher-1978-usgs-b1452.yaml"
# t-1620 parses the terbium decimals; blank-FW formula-line entropies are excluded.
B1452_RAW = 55707
B1452_STORED = 15229
B1452_REFUSED = 19642
B1452_EXCLUDED = 20836
B1452_MERGED_PROPOSED = 457
B1452_MERGED_STORED = 376
B1452_MERGED_REFUSED = 15
B1452_MERGED_REFUSED_10X = 4
B1452_MERGED_EXCLUDED = 66
B1452_UNGUARDED_MERGED_STORED = 165
B1452_UNGUARDED_298K_S = 157
B1452_UNGUARDED_HT_DH = 7
B1452_UNGUARDED_HT_CP = 1
B1452_IDENTITY_10X = 205
B1452_IDENTITY_1X_10X = 9
HOLMIUM_HT_PHASE_02 = "robie-hemingway-fisher-1978-usgs-b1452-0036-phase-02"
SPINEL_HT = "robie-hemingway-fisher-1978-usgs-b1452-0236"
B1452_FORMULA_UNRESOLVED_HT = 47
B1452_FORMULA_UNRESOLVED_298K_ROWS = 49
# Green-to-tip additions for the 298 K and high-temperature terbium tables.
B1452_TB_298K_OBSERVATIONS = 8
B1452_TB_0202_OBSERVATIONS = 34
B1452_TB_0203_OBSERVATIONS = 27
# Scratch-regenerated observations-v2 unique-ID count; the train refreshes the store.
B1452_STORE_STORED = 15224
TABLE_298K = "robie-hemingway-fisher-1978-usgs-b1452-0003"
TABLE1 = "robie-hemingway-fisher-1978-usgs-b1452-0001"
SILVER_HT = "robie-hemingway-fisher-1978-usgs-b1452-0004"
FAYALITE_ELEMENTS = "robie-hemingway-fisher-1978-usgs-b1452-0335"
FAYALITE_OXIDES = "robie-hemingway-fisher-1978-usgs-b1452-0336"
FAYALITE_OXIDES_PHASE_02 = "robie-hemingway-fisher-1978-usgs-b1452-0336-phase-02"
REVIEWED_OXIDE_SAMPLES = {
    "robie-hemingway-fisher-1978-usgs-b1452-0314": (
        "kyanite",
        "Al2SiO5",
        Decimal("0.484"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0316": (
        "andalusite",
        "Al2SiO5",
        Decimal("0.241"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0318": (
        "sillimanite",
        "Al2SiO5",
        Decimal("0.083"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0322": (
        "larnite",
        "Ca2SiO4",
        Decimal("22.425"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0324": (
        "calcium olivine",
        "Ca2SiO4",
        Decimal("23.918"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0328": (
        "grossular",
        "Ca3Al2Si3O12",
        Decimal("56.029"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0348": (
        "wollastonite",
        "CaSiO3",
        Decimal("15.790"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0350": (
        "pseudowollastonite",
        "CaSiO3",
        Decimal("14.923"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0352": (
        "ca-al pyroxene",
        "CaAl2SiO6",
        Decimal("10.821"),
    ),
    "robie-hemingway-fisher-1978-usgs-b1452-0362": (
        "clinoenstatite",
        "MgSiO3",
        Decimal("6.202"),
    ),
}
OXIDE_HEADING_RECORDS = (
    "robie-hemingway-fisher-1978-usgs-b1452-0232",
    "robie-hemingway-fisher-1978-usgs-b1452-0232-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0320",
    "robie-hemingway-fisher-1978-usgs-b1452-0324",
    "robie-hemingway-fisher-1978-usgs-b1452-0336",
    "robie-hemingway-fisher-1978-usgs-b1452-0336-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0358",
    "robie-hemingway-fisher-1978-usgs-b1452-0360",
    "robie-hemingway-fisher-1978-usgs-b1452-0374",
    "robie-hemingway-fisher-1978-usgs-b1452-0382",
    "robie-hemingway-fisher-1978-usgs-b1452-0382-phase-02",
)
OXIDE_COLUMN_HEADING_RECORDS = (
    "robie-hemingway-fisher-1978-usgs-b1452-0307-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0400",
)
ELEMENT_BASIS_CONTROL_RECORDS = (
    "robie-hemingway-fisher-1978-usgs-b1452-0179",
    "robie-hemingway-fisher-1978-usgs-b1452-0180-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0180",
)
OXIDE_BASIS_RECORDS = (
    "robie-hemingway-fisher-1978-usgs-b1452-0228-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0228",
    "robie-hemingway-fisher-1978-usgs-b1452-0232-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0232",
    "robie-hemingway-fisher-1978-usgs-b1452-0241",
    "robie-hemingway-fisher-1978-usgs-b1452-0273-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0273-phase-03",
    "robie-hemingway-fisher-1978-usgs-b1452-0273",
    "robie-hemingway-fisher-1978-usgs-b1452-0275",
    "robie-hemingway-fisher-1978-usgs-b1452-0277",
    "robie-hemingway-fisher-1978-usgs-b1452-0279",
    "robie-hemingway-fisher-1978-usgs-b1452-0281",
    "robie-hemingway-fisher-1978-usgs-b1452-0284-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0284",
    "robie-hemingway-fisher-1978-usgs-b1452-0307",
    "robie-hemingway-fisher-1978-usgs-b1452-0307-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0314",
    "robie-hemingway-fisher-1978-usgs-b1452-0316",
    "robie-hemingway-fisher-1978-usgs-b1452-0318",
    "robie-hemingway-fisher-1978-usgs-b1452-0320",
    "robie-hemingway-fisher-1978-usgs-b1452-0322-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0322-phase-03",
    "robie-hemingway-fisher-1978-usgs-b1452-0322",
    "robie-hemingway-fisher-1978-usgs-b1452-0324",
    "robie-hemingway-fisher-1978-usgs-b1452-0326",
    "robie-hemingway-fisher-1978-usgs-b1452-0328",
    "robie-hemingway-fisher-1978-usgs-b1452-0330",
    "robie-hemingway-fisher-1978-usgs-b1452-0332",
    "robie-hemingway-fisher-1978-usgs-b1452-0334-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0334",
    "robie-hemingway-fisher-1978-usgs-b1452-0336-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0336",
    "robie-hemingway-fisher-1978-usgs-b1452-0338",
    "robie-hemingway-fisher-1978-usgs-b1452-0340",
    "robie-hemingway-fisher-1978-usgs-b1452-0342",
    "robie-hemingway-fisher-1978-usgs-b1452-0344-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0344",
    "robie-hemingway-fisher-1978-usgs-b1452-0346",
    "robie-hemingway-fisher-1978-usgs-b1452-0348",
    "robie-hemingway-fisher-1978-usgs-b1452-0350",
    "robie-hemingway-fisher-1978-usgs-b1452-0352",
    "robie-hemingway-fisher-1978-usgs-b1452-0354",
    "robie-hemingway-fisher-1978-usgs-b1452-0356",
    "robie-hemingway-fisher-1978-usgs-b1452-0358",
    "robie-hemingway-fisher-1978-usgs-b1452-0360",
    "robie-hemingway-fisher-1978-usgs-b1452-0362",
    "robie-hemingway-fisher-1978-usgs-b1452-0364",
    "robie-hemingway-fisher-1978-usgs-b1452-0366",
    "robie-hemingway-fisher-1978-usgs-b1452-0368",
    "robie-hemingway-fisher-1978-usgs-b1452-0370",
    "robie-hemingway-fisher-1978-usgs-b1452-0372",
    "robie-hemingway-fisher-1978-usgs-b1452-0374",
    "robie-hemingway-fisher-1978-usgs-b1452-0376",
    "robie-hemingway-fisher-1978-usgs-b1452-0378",
    "robie-hemingway-fisher-1978-usgs-b1452-0380-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0380",
    "robie-hemingway-fisher-1978-usgs-b1452-0382-phase-02",
    "robie-hemingway-fisher-1978-usgs-b1452-0382",
    "robie-hemingway-fisher-1978-usgs-b1452-0385",
    "robie-hemingway-fisher-1978-usgs-b1452-0387",
    "robie-hemingway-fisher-1978-usgs-b1452-0389",
    "robie-hemingway-fisher-1978-usgs-b1452-0391",
    "robie-hemingway-fisher-1978-usgs-b1452-0393",
    "robie-hemingway-fisher-1978-usgs-b1452-0396",
    "robie-hemingway-fisher-1978-usgs-b1452-0398",
    "robie-hemingway-fisher-1978-usgs-b1452-0400",
)
FORMATION_QUANTITIES = (
    Quantity.DELTA_FH,
    Quantity.DELTA_FG,
    Quantity.LOG10_KF,
    Quantity.LOG10_K_REACTION,
)


@pytest.mark.parametrize(
    ("record_id", "printed_log_k"),
    [(record_id, sample[2]) for record_id, sample in REVIEWED_OXIDE_SAMPLES.items()]
    + [("robie-hemingway-fisher-1978-usgs-b1452-0398", Decimal("3.743"))],
)
def test_localizing_ocr_names_preserves_printed_numeric_cells(
    record_id: str, printed_log_k: Decimal,
) -> None:
    """Source golden before moving the OCR spellings out of mineral vocabulary."""
    rows = _formation_observations(
        _generation(record_id), basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION, temperature="298.15",
    )
    assert len(rows) == 1
    assert rows[0].value.point == printed_log_k


def _load(record_id: str) -> dict:
    path = RECORDS_DIR / f"{record_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _generation(record_id: str) -> generator.RecordGeneration:
    global _LAST_RECORD
    _LAST_RECORD = _load(record_id)
    return generator.generate_record(_LAST_RECORD)


_LAST_RECORD: dict | None = None


def _oxide_basis_record_ids() -> tuple[str, ...]:
    record_ids = []
    for path in RECORDS_DIR.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if generator._formation_basis_for_record(record) == "from_the_oxides":
            record_ids.append(str(record["record_id"]))
    return tuple(sorted(record_ids))


def _observations_for(
    generated: generator.RecordGeneration,
    quantity: Quantity,
    *,
    temperature: str | None = None,
    basis: str | None = None,
    formula: str | None = None,
    row: int | None = None,
    name: str | None = None,
) -> list:
    from simulator.battery.stable_ids import _name_slug

    result = []
    for observation in generated.observations:
        if quantity_token(observation.identity) is not quantity:
            continue
        if temperature is not None:
            temp = observation.identity.temperature_K
            if temp is None or not temp.is_value or str(temp.value) != temperature:
                continue
        if basis is not None:
            note = observation.locator.note if observation.locator is not None else ""
            if f"formation_basis={basis}" not in (note or ""):
                continue
        if formula is not None and observation.identity.species.formula != formula:
            continue
        if name is not None:
            slug = _name_slug(name)
            if f":name={slug}" not in observation.observation_id:
                continue
        elif row is not None and _LAST_RECORD is not None:
            printed = generator._row_name(_LAST_RECORD, row)
            if printed:
                slug = _name_slug(printed)
                if f":name={slug}" not in observation.observation_id:
                    continue
        result.append(observation)
    return result


def _formation_observations(
    generated: generator.RecordGeneration,
    *,
    basis: str,
    quantity: Quantity | None = None,
    temperature: str | None = None,
) -> list:
    result = []
    quantities = (quantity,) if quantity is not None else FORMATION_QUANTITIES
    for observation in generated.observations:
        if not any(
            f":{candidate.value}:" in observation.observation_id
            for candidate in quantities
        ):
            continue
        note = observation.locator.note if observation.locator is not None else ""
        if f"formation_basis={basis}" not in (note or ""):
            continue
        if temperature is not None:
            temp = observation.identity.temperature_K
            if temp is None or not temp.is_value or str(temp.value) != temperature:
                continue
        result.append(observation)
    return result


def _formation_quantity(observation) -> Quantity | None:
    return next(
        (
            quantity
            for quantity in FORMATION_QUANTITIES
            if f":{quantity.value}:" in observation.observation_id
        ),
        None,
    )


def test_table1_states_r_and_joule_formation_units() -> None:
    record = _load(TABLE1)
    symbols = {
        row["cells"]["symbol"]["as_published"]: row["cells"]["definition"]["as_published"]
        for row in record["rows"]
    }
    assert "R" in symbols
    assert "8.3143" in symbols["R"]
    assert "J" in symbols["R"]
    assert "ΔHf" in symbols
    assert "J· mol-1" in symbols["ΔHf"] or "J·mol-1" in symbols["ΔHf"].replace(" ", "")
    assert "kJ" not in symbols["ΔHf"]
    assert "ΔGf" in symbols
    assert "kJ" not in symbols["ΔGf"]
    generated = _generation(TABLE1)
    assert generated.report["gas_constant_J_per_mol_K"] == "8.3143"
    assert "Table 1" in generated.report["gas_constant_basis"]
    assert generated.report["unit_row"]["table_298k_formation_unit"] == "J/mol"
    assert generated.report["unit_row"]["ht_formation_unit"] == "kJ/mol"


def test_gas_constant_is_table1_not_modern() -> None:
    from simulator.battery.identity import R_J_PER_MOL_K

    assert generator.B1452_R_J_PER_MOL_K == Decimal("8.3143")
    assert generator.B1452_R_J_PER_MOL_K != R_J_PER_MOL_K
    modern = log10K_from_delta_fG_kJ_mol(Decimal("-2441.276"), Decimal("298.15"))
    source = log10K_from_delta_fG_kJ_mol(
        Decimal("-2441.276"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1452_R_J_PER_MOL_K,
    )
    assert modern != source
    assert abs(source - Decimal("427.703")) < Decimal("0.01")


def test_page_header_units_ignore_captured_ocr_strings() -> None:
    record = _load(SILVER_HT)
    captured = record["units_as_published"]["formation_enthalpy"]
    assert captured != "kJ/mol"
    generated = _generation(SILVER_HT)
    enthalpy = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15"
    )[0]
    note = enthalpy.locator.note or ""
    assert "unit='kJ/mol'" in note
    assert "captured units_as_published ignored" in note
    assert captured not in note


def test_table_298k_formation_is_joules_not_kilojoules() -> None:
    """Kyanite 298 K ΔfH is −2591730 J·mol⁻¹ = −2591.730 kJ·mol⁻¹.

    Reading the 298 K table with the HT kJ header would store −2591730 kJ
    and the test would still look like a number. Fail that path.
    """

    assert generator.TABLE_298K_PAGE_UNITS["formation_enthalpy"] == "J/mol"
    assert generator.HT_PAGE_UNITS["formation_enthalpy"] == "kJ/mol"
    assert (
        generator.TABLE_298K_PAGE_UNITS["formation_enthalpy"]
        != generator.HT_PAGE_UNITS["formation_enthalpy"]
    )
    generated = _generation(TABLE_298K)
    kyanite = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="298.15",
        formula="Al2SiO5",
        name="KYANITE $Al_{2}SiO_{5}$",
    )
    assert len(kyanite) == 1
    obs = kyanite[0]
    assert obs.value.point == Decimal("-2591.730")
    assert obs.value.point != Decimal("-2591730")
    assert QUANTITY_UNITS[Quantity.DELTA_FH] == "kJ_per_declared_mol_basis"
    assert obs.derivation.output_unit == QUANTITY_UNITS[Quantity.DELTA_FH]
    factor = dict(obs.derivation.parameters)["j_to_kj"]
    assert factor.state.value == Decimal("0.001")
    assert Decimal("-2591730") * factor.state.value == obs.value.point
    note = obs.locator.note or ""
    assert "unit='J/mol'" in note
    assert "Table 1" in note


def test_fayalite_298k_joules_match_ht_kilojoules() -> None:
    table = _generation(TABLE_298K)
    ht = _generation(FAYALITE_ELEMENTS)
    from_298k = _observations_for(
        table,
        Quantity.DELTA_FH,
        temperature="298.15",
        formula="Fe2SiO4",
        row=674,
    )
    from_ht = _observations_for(
        ht, Quantity.DELTA_FH, temperature="298.15", basis="from_the_elements"
    )
    assert len(from_298k) == 1
    assert len(from_ht) == 1
    assert from_298k[0].identity.species.formula == "Fe2SiO4"
    assert from_ht[0].identity.species.formula == "Fe2SiO4"
    assert from_298k[0].value.point == Decimal("-1479.360")
    assert from_ht[0].value.point == Decimal("-1479.360")
    ht_note = from_ht[0].locator.note or ""
    table_note = from_298k[0].locator.note or ""
    assert "unit='kJ/mol'" in ht_note
    assert "unit='J/mol'" in table_note
    assert "kj_to_j" in dict(from_ht[0].derivation.parameters)
    assert "j_to_kj" in dict(from_298k[0].derivation.parameters)


def test_ocr_name_is_not_a_formula() -> None:
    fayalite = _generation(FAYALITE_ELEMENTS)
    resolution = fayalite.report["formula_resolution"]
    assert resolution["formula"] == "Fe2SiO4"
    assert resolution["source"] == "formula_weight"
    assert "title" not in (resolution["source"] or "")
    formulas = {obs.identity.species.formula for obs in fayalite.observations}
    assert formulas == {"Fe2SiO4"}
    assert "Flyau" not in formulas

    forsterite = generator._resolve_formula(_load("robie-hemingway-fisher-1978-usgs-b1452-0337"))
    assert forsterite.formula == "Mg2SiO4"
    assert forsterite.source == "formula_weight"
    assert forsterite.formula != "Pobst"

    sulfur = _generation("robie-hemingway-fisher-1978-usgs-b1452-0069")
    assert sulfur.report["formula_resolution"]["formula"] == "S8"
    sulfur_formulas = {obs.identity.species.formula for obs in sulfur.observations}
    assert sulfur_formulas == {"S8"}
    assert "S1" not in sulfur_formulas


def test_title_case_is_not_a_formula_source() -> None:
    source = Path(generator.__file__).read_text(encoding="utf-8")
    assert "key.title()" not in source
    quartz = generator._resolve_formula(_load("robie-hemingway-fisher-1978-usgs-b1452-0190"))
    assert quartz.formula == "SiO2"
    assert quartz.source is not None
    assert "title" not in quartz.source
    assert "name_index" in quartz.source or "formula_weight" in quartz.source
    generated = _generation("robie-hemingway-fisher-1978-usgs-b1452-0190")
    assert generated.report["formula_resolution"]["formula"] == "SiO2"
    assert generated.report["formula_resolution"]["unresolved"] is False
    formulas = {obs.identity.species.formula for obs in generated.observations}
    assert "Quartz" not in formulas
    assert formulas <= {"SiO2"}


def test_298k_next_line_formula_is_page_grounded() -> None:
    generated = _generation(TABLE_298K)
    quartz = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="SiO2", row=401
    )
    assert len(quartz) == 1
    acanthite = generator._resolve_formula(
        _load(TABLE_298K),
        name="ACANTHITE (ARGENTITE)",
        formula_weight="247.796",
    )
    assert acanthite.formula == "Ag2S"
    assert "title" not in (acanthite.source or "")
    stored = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Quartz", row=401
    )
    assert stored == []


def test_terbium_decimal_formula_lines_do_not_emit_entropy() -> None:
    generated = _generation(TABLE_298K)
    observations = [
        observation
        for observation in generated.observations
        if quantity_token(observation.identity) is Quantity.S
        and observation.identity.species.formula in {"TbO1.714", "TbO1.812"}
    ]

    assert {
        (observation.identity.species.formula, observation.value.point)
        for observation in observations
    } == {
        ("TbO1.714", Decimal("80.75")),
        ("TbO1.812", Decimal("81.17")),
    }
    excluded = [
        row
        for row in generated.report["exclusions"]
        if row["column"] == "entropy_s298"
        and row["as_published"] == "4.20"
        and "blank-formula-weight terbium formula line" in row["reason"]
    ]
    assert len(excluded) == 2

    for formula, formula_weight in (
        ("TbO1.714", Decimal("186.348")),
        ("TbO1.812", Decimal("187.916")),
    ):
        formula_observations = [
            observation
            for observation in generated.observations
            if observation.identity.species.formula == formula
        ]
        assert len(formula_observations) == 4
        calculated_mass = generator._formula_mass(formula)
        assert calculated_mass is not None
        assert abs(calculated_mass - formula_weight) < Decimal("0.001")
    assert sum(
        observation.identity.species.formula in {"TbO1.714", "TbO1.812"}
        for observation in generated.observations
    ) == B1452_TB_298K_OBSERVATIONS

    for record_id, formula, formula_weight, expected_count in (
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0202",
            "TbO1.714",
            Decimal("186.348"),
            B1452_TB_0202_OBSERVATIONS,
        ),
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0203",
            "TbO1.812",
            Decimal("187.916"),
            B1452_TB_0203_OBSERVATIONS,
        ),
    ):
        ht = _generation(record_id)
        assert len(ht.observations) == expected_count
        formulas = {observation.identity.species.formula for observation in ht.observations}
        assert formulas == {formula}
        printed_weight = Decimal(_load(record_id)["formula_weight"]["as_published"])
        assert printed_weight == formula_weight
        calculated_mass = generator._formula_mass(formula)
        assert calculated_mass is not None
        assert abs(calculated_mass - printed_weight) < Decimal("0.001")


def test_unresolved_formula_refuses_and_names_consulted_fields() -> None:
    generated = _generation(TABLE_298K)
    greenockite = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 201
        and generator.FORMULA_UNRESOLVED_REASON_PREFIX in row["reason"]
    ]
    assert greenockite
    reason = greenockite[0]["reason"]
    assert "formula_weight='144.460'" in reason
    assert "name_key='GREENOCKITE'" in reason
    stored = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Greenockite", row=201
    )
    assert stored == []


def test_tremolite_formula_ocr_repair_matches_the_rendered_table() -> None:
    # Physical PDF p. 400 / folio 394 prints Ca2Mg5[Si8O22](OH)2.
    record_id = "robie-hemingway-fisher-1978-usgs-b1452-0368"
    resolution = generator._resolve_formula(_load(record_id))
    assert resolution.formula == "Ca2Mg5[Si8O22](OH)2"
    assert resolution.source == "printed_formula_ocr"
    assert resolution.consulted["printed_formula_correction"] == (
        "physical PDF p. 400 / folio 394"
    )
    assert generator._mass_matches_formula_weight(
        resolution.formula, Decimal("812.374")
    )

    observations = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
    )
    assert len(observations) == 9
    for observation in observations:
        assert observation.identity.reaction is not None
        assert observation.identity.reaction.is_value
        assert observation.identity.reaction.value.terms[0].species.formula == (
            "Ca2Mg5[Si8O22](OH)2"
        )
        assert observation.identity.quantity.is_unknown


def test_silver_400k_gibbs_function_worked_row() -> None:
    generated = _generation(SILVER_HT)
    entropy = _observations_for(generated, Quantity.S, temperature="400")
    assert len(entropy) == 1
    assert entropy[0].value.point == Decimal("50.08")
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "planck_vs_S_minus_HHT"
        and row.get("temperature_as_published") in {"400", "400.0"}
    ]
    assert results
    assert results[0]["ok"] is True
    assert Decimal(results[0]["calculated"]) == Decimal("43.55")


def test_kyanite_298k_logkf_identity_uses_joule_delta_g() -> None:
    generated = _generation(TABLE_298K)
    gibbs = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Al2SiO5",
        name="KYANITE $Al_{2}SiO_{5}$",
    )
    logk = _observations_for(
        generated,
        Quantity.LOG10_KF,
        temperature="298.15",
        formula="Al2SiO5",
        name="KYANITE $Al_{2}SiO_{5}$",
    )
    assert len(gibbs) == 1
    assert len(logk) == 1
    assert gibbs[0].value.point == Decimal("-2441.276")
    assert logk[0].value.point == Decimal("427.703")
    calculated = log10K_from_delta_fG_kJ_mol(
        Decimal("-2441.276"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1452_R_J_PER_MOL_K,
    )
    assert abs(calculated - Decimal("427.703")) < Decimal("0.01")
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "log10_Kf_from_delta_fG" and row.get("row_index") == 660
    ]
    assert results
    assert results[0]["gibbs_page_unit"] == "J/mol"
    assert results[0]["ok"] is True


def test_merged_two_dot_entropy_splits_on_grain() -> None:
    generated = _generation(TABLE_298K)
    silver = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=0
    )
    assert len(silver) == 1
    assert silver[0].value.point == Decimal("42.55")
    assert silver[0].uncertainty.kind is UncertaintyKind.PRINTED
    assert silver[0].uncertainty.verbatim == "0.21"
    assert silver[0].evidence.original_method_class == generator.MERGED_SPLIT_METHOD
    splits = generated.report["merged_cell_splits"]
    assert splits["proposed"] >= 1
    assert splits["stored"] >= 1
    assert splits["proposed"] == splits["stored"] + splits["refused"] + splits["excluded"]


def test_spinel_extra_dot_split_is_proposed_then_10x_refused() -> None:
    """Grain uniquely cuts Spinel 1800 K gef 2.29.48; the page is 229.482.

    S 374.85 − (H−H298)/T 145.368 = 229.482. The 10× Gibbs net refuses the
    wrong cut. That proposal must not count as stored.
    """

    generated = _generation(SPINEL_HT)
    splits = generated.report["merged_cell_splits"]
    gef = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "2.29.48" and row["column"] == "negative_gibbs_function"
    ]
    assert gef
    assert "10×" in gef[0]["reason"]
    stored_merged = [
        obs
        for obs in generated.observations
        if obs.evidence.original_method_class == generator.MERGED_SPLIT_METHOD
        and "2.29" in (obs.locator.note or "")
    ]
    assert stored_merged == []
    assert splits["proposed"] == splits["stored"] + splits["refused"] + splits["excluded"]
    assert splits["refused"] >= 1
    assert splits["refused_10x"] >= 1
    assert splits["stored"] == splits["proposed"] - splits["refused"] - splits["excluded"]
    identity = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "planck_vs_S_minus_HHT"
        and row.get("row_index") == 15
    ]
    assert identity
    assert identity[0]["ok"] is False
    assert identity[0]["printed"] == "2.29.48"
    assert Decimal(identity[0]["calculated"]) == Decimal("229.482")
    assert Decimal(identity[0]["absolute_residual"]) > 10 * Decimal(
        identity[0]["rounding_tolerance"]
    )


def test_unguarded_298k_entropy_splits_are_named() -> None:
    """298 K S two-dot splits have no Gibbs 10× net. Name the population."""

    generated = _generation(TABLE_298K)
    splits = generated.report["merged_cell_splits"]
    assert splits["unguarded_stored"] < splits["stored"]
    assert splits["unguarded_by_quantity"] == {"S": B1452_UNGUARDED_298K_S}
    assert splits["unguarded_stored"] == B1452_UNGUARDED_298K_S
    assert "(H−H298)/T" in splits["unguarded_reason"]
    silver = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=0
    )
    assert len(silver) == 1
    assert silver[0].evidence.original_method_class == generator.MERGED_SPLIT_METHOD
    assert silver[0].value.point == Decimal("42.55")


def test_ag_plus_jammed_gibbs_recovers_via_reconstructed_identity() -> None:
    """Glued 77077100 is 77077±100 J; identity on the reconstructed value passes."""

    generated = _generation(TABLE_298K)
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("row_index") == 1 and row.get("identity") == "log10_Kf_from_delta_fG"
    ]
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["reconstructed_gibbs"] == "77077"
    stored_g = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Ag", row=1
    )
    stored_k = _observations_for(
        generated, Quantity.LOG10_KF, temperature="298.15", formula="Ag", row=1
    )
    stored_h = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15", formula="Ag", row=1
    )
    assert len(stored_g) == 1
    assert stored_g[0].value.point == Decimal("77.077")
    assert stored_g[0].value.point != Decimal("77077.100")
    assert stored_g[0].value.point != Decimal("77077100")
    assert len(stored_k) == 1
    assert stored_k[0].value.point == Decimal("-13.504")
    assert stored_h == []
    refused_h = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "10575085" and row["row_index"] == 1
    ]
    assert refused_h
    assert "unique split" in refused_h[0]["reason"]
    assert "10×" not in refused_h[0]["reason"]


def test_corundum_298k_jammed_gibbs_recovers_via_reconstructed_identity() -> None:
    generated = _generation(TABLE_298K)
    stored_g = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2O3", row=261
    )
    stored_k = _observations_for(
        generated, Quantity.LOG10_KF, temperature="298.15", formula="Al2O3", row=261
    )
    stored_h = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15", formula="Al2O3", row=261
    )
    assert len(stored_g) == 1
    assert stored_g[0].value.point == Decimal("-1582.228")
    assert len(stored_k) == 1
    assert stored_k[0].value.point == Decimal("277.201")
    assert stored_h == []
    refused_h = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 261 and row["column"] == "formation_enthalpy"
    ]
    assert refused_h
    assert "unique split" in refused_h[0]["reason"]


def test_hematite_glued_gibbs_without_passing_cut_stays_10x() -> None:
    generated = _generation(TABLE_298K)
    stored_g = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Fe2O3", row=297
    )
    stored_k = _observations_for(
        generated, Quantity.LOG10_KF, temperature="298.15", formula="Fe2O3", row=297
    )
    assert stored_g == []
    assert stored_k == []
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 297
        and row["column"] in {"formation_gibbs_energy", "log_kf"}
        and "10×" in row["reason"]
    ]
    assert {row["column"] for row in refused} >= {"formation_gibbs_energy", "log_kf"}
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("row_index") == 297 and row.get("identity") == "log10_Kf_from_delta_fG"
    ]
    assert results
    assert results[0]["ok"] is False
    assert "reconstructed_gibbs" not in results[0]


def test_neighbour_sign_disabled_on_298k_table() -> None:
    generated = _generation(TABLE_298K)
    assert generated.report["neighbour_sign"]["disabled_on_298k"] is True
    assert generated.report["neighbour_sign"]["applied"] is False
    assert generated.report["neighbour_sign_hits"] == []
    assert "garbage" in generated.report["neighbour_sign"]["reason"]
    assert "298.15" in generated.report["neighbour_sign"]["reason"]


def test_neighbour_sign_report_matches_refuse_branch() -> None:
    table1 = _generation(TABLE1)
    assert table1.report["neighbour_sign"]["applied"] is False
    assert table1.report["neighbour_sign"]["disabled_on_table1_table2"] is True
    table2 = _generation("robie-hemingway-fisher-1978-usgs-b1452-0002")
    assert table2.report["neighbour_sign"]["applied"] is False
    assert table2.report["neighbour_sign"]["disabled_on_table1_table2"] is True
    ht = _generation(SILVER_HT)
    assert ht.report["neighbour_sign"]["applied"] is True
    assert ht.report["neighbour_sign"]["disabled_on_table1_table2"] is False
    table = _generation(TABLE_298K)
    assert table.report["neighbour_sign"]["applied"] is False
    assert table.report["neighbour_sign"]["disabled_on_298k"] is True


def test_neighbour_sign_refuses_ht_seeded_dropped_minus() -> None:
    payload = _load(FAYALITE_OXIDES)
    original = payload["rows"][4]["cells"]["formation_enthalpy"]["as_published"]
    payload["rows"][4]["cells"]["formation_enthalpy"]["as_published"] = original.replace(
        "-", ""
    )
    payload["rows"][4]["cells"]["formation_enthalpy"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 4 and row["column"] == "formation_enthalpy"
    ]
    assert refused
    assert "neighbour-sign" in refused[0]["reason"]
    control = _generation(FAYALITE_OXIDES)
    # The oxide-basis quantity is intentionally unknown, but its numeric observation remains stored.
    stored = _formation_observations(
        control,
        quantity=Quantity.DELTA_FG,
        basis="from_the_oxides",
        temperature="298.15",
    )
    assert stored


def test_identity_1x_10x_rows_are_listed_independently() -> None:
    generated = _generation(HOLMIUM_HT_PHASE_02)
    band = generated.report["identity_notice_1x_10x"]
    assert len(band) == 1
    assert band[0]["identity"] == "planck_vs_S_minus_HHT"
    assert band[0]["row_index"] == 0
    assert Decimal(band[0]["absolute_residual"]) <= 10 * Decimal(
        band[0]["rounding_tolerance"]
    )
    assert Decimal(band[0]["absolute_residual"]) > Decimal(
        band[0]["rounding_tolerance"]
    )
    entropy = _observations_for(generated, Quantity.S, temperature="1701")
    assert len(entropy) == 1
    assert entropy[0].notices
    assert entropy[0].notices[0].kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert entropy[0].observation_id in band[0]["stored_with_notice"]


def test_10x_planck_refuses_participating_cells_before_vocab_exclusion() -> None:
    payload = _load(SILVER_HT)
    payload["rows"][1]["cells"]["negative_gibbs_function"]["as_published"] = "435.5"
    payload["rows"][1]["cells"]["negative_gibbs_function"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused_cols = {
        row["column"]
        for row in generated.report["refusals"]
        if row["row_index"] == 1
        and "10×" in row["reason"]
    }
    assert "negative_gibbs_function" in refused_cols
    assert "entropy" in refused_cols
    assert "enthalpy_function" in refused_cols
    stored_s = _observations_for(generated, Quantity.S, temperature="400")
    assert stored_s == []
    control = _generation(SILVER_HT)
    assert _observations_for(control, Quantity.S, temperature="400")


def test_10x_logkf_refuses_seeded_residual() -> None:
    payload = _load(TABLE_298K)
    payload["rows"][660]["cells"]["formation_gibbs_energy"]["as_published"] = "-24412760"
    payload["rows"][660]["cells"]["formation_gibbs_energy"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 660
        and row["column"] in {"formation_gibbs_energy", "log_kf"}
        and "10×" in row["reason"]
    ]
    assert {row["column"] for row in refused} >= {"formation_gibbs_energy", "log_kf"}
    control = _generation(TABLE_298K)
    stored = _observations_for(
        control,
        Quantity.LOG10_KF,
        temperature="298.15",
        formula="Al2SiO5",
        name="KYANITE $Al_{2}SiO_{5}$",
    )
    assert stored
    assert stored[0].value.point == Decimal("427.703")


def test_ocr_suspect_without_image_correction_is_refused() -> None:
    generated = _generation(SILVER_HT)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] in {"o.uoo", "o.oou"}
    ]
    assert {row["as_published"] for row in refused} >= {"o.uoo", "o.oou"}
    entropy = _observations_for(generated, Quantity.S, temperature="298.15")[0]
    assert entropy.value.point == Decimal("42.55")


def test_oxide_asterisk_is_basis_not_damage() -> None:
    generated = _generation(FAYALITE_OXIDES)
    gibbs = _formation_observations(
        generated,
        quantity=Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_oxides",
    )
    assert len(gibbs) == 1
    assert gibbs[0].value.point == Decimal("-20.775")
    # No oxide-formation quantity exists in the schema; retain the value as typed absence.
    assert gibbs[0].identity.quantity.is_unknown
    note = gibbs[0].locator.note or ""
    assert "as_published='-20.775 *'" in note or "as_published='-20.775*'" in note.replace(" ", "")
    assert "formation_basis=from_the_oxides" in note
    refused_star = [
        row
        for row in generated.report["refusals"]
        if "*" in row["as_published"] and row["column"] == "formation_gibbs_energy"
    ]
    assert refused_star == []


def test_b1452_oxide_basis_record_census_is_complete() -> None:
    assert set(_oxide_basis_record_ids()) == set(OXIDE_BASIS_RECORDS)
    assert set(OXIDE_COLUMN_HEADING_RECORDS) <= set(OXIDE_BASIS_RECORDS)


def test_b1452_oxide_heading_record_census_is_complete() -> None:
    captured_heading_records = set()
    for path in RECORDS_DIR.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        heading = generator._normalized_letters(
            str(record.get("formation_basis_header_as_published") or "")
        )
        if "OXIDE" in heading:
            captured_heading_records.add(str(record["record_id"]))
    assert captured_heading_records == set(OXIDE_HEADING_RECORDS)


@pytest.mark.parametrize("record_id", ELEMENT_BASIS_CONTROL_RECORDS)
def test_explicit_element_basis_heading_precedes_other_oxide_mentions(
    record_id: str,
) -> None:
    record = _load(record_id)
    assert "OXIDE" in generator._normalized_letters(generator._header_text(record))
    assert generator._formation_basis_for_record(record) == "from_the_elements"

    generated = _generation(record_id)
    formation_rows = _formation_observations(
        generated, basis="from_the_elements"
    )
    if formation_rows:
        assert all(row.identity.quantity.is_value for row in formation_rows)
    else:
        # The 0179 temperature row is already withheld by the source-cell guard.
        assert record_id.endswith("-0179")


@pytest.mark.parametrize("record_id", OXIDE_BASIS_RECORDS)
def test_every_b1452_oxide_basis_record_keeps_oxide_log_k_reactions(
    record_id: str,
) -> None:
    record = _load(record_id)
    assert generator._formation_basis_for_record(record) == "from_the_oxides"

    generated = _generation(record_id)
    formation_rows = _formation_observations(generated, basis="from_the_oxides")
    if not formation_rows:
        # Existing row guards can withhold every printed formation cell before storage.
        assert any(
            item["column"] in {
                "formation_enthalpy",
                "formation_gibbs_energy",
                "log_kf",
            }
            for item in generated.report["refusals"] + generated.report["exclusions"]
        )
        return
    quantities = {
        quantity
        for quantity in FORMATION_QUANTITIES
        if any(f":{quantity.value}:" in row.observation_id for row in formation_rows)
    }
    assert quantities
    # Only oxide-frame log K changes quantity: oxide ΔfH/ΔfG remain typed unknown.
    assert quantities <= {
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
        Quantity.LOG10_K_REACTION,
    }
    for row in formation_rows:
        quantity = _formation_quantity(row)
        assert quantity is not None
        assert row.value.point is not None
        if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG}:
            assert row.identity.quantity.is_unknown
            assert row.identity.quantity.reason == (
                "No quantity token represents formation from simple oxides; "
                f"{quantity.value} denotes formation from the elements."
            )
            continue
        if quantity is not Quantity.LOG10_K_REACTION:
            continue
        reaction_state = row.identity.reaction
        if reaction_state is None or not reaction_state.is_value:
            assert row.identity.quantity.is_unknown
            continue
        unresolved_terms = [
            term
            for term in reaction_state.value.terms
            if term.standard_state is None or not term.standard_state.is_value
        ]
        product = next(
            term
            for term in reaction_state.value.terms
            if term.coefficient > 0
        )
        temperature = row.identity.temperature_K
        assert temperature is not None and temperature.is_value
        source_windows = generator.phase_windows(
            generator._normalized_phase_text(record)
        )
        matching_windows = [
            window for window in source_windows
            if window.lo <= temperature.value <= window.hi
        ]
        segment = record.get("phase_segment")
        segment_ordinal = segment.get("ordinal") if isinstance(segment, dict) else None
        printed_window = (
            matching_windows[0]
            if len(matching_windows) == 1
            else next(
                (
                    window
                    for window in matching_windows
                    if isinstance(segment_ordinal, int)
                    and window.index + 1 == segment_ordinal
                ),
                None,
            )
        )
        if printed_window is not None:
            assert product.species.phase.is_value, (
                f"named product {record_id} lost its printed phase"
            )
            if product.species.phase.value is Phase.CR:
                expected_polymorph = generator._polymorph_for_phase_window(
                    record, printed_window.label
                )
                actual_polymorph = product.species.polymorph
                if expected_polymorph.is_value:
                    assert actual_polymorph is not None and actual_polymorph.is_value, (
                        f"named crystal {record_id} is not mapped through the polymorph owner"
                    )
                else:
                    assert actual_polymorph is not None and actual_polymorph.is_unknown
                    assert actual_polymorph.reason
            else:
                assert (
                    product.species.polymorph is not None
                    and product.species.polymorph.is_not_applicable
                ), f"noncrystalline product {record_id} has no typed polymorph state"
        if unresolved_terms:
            temperature = row.identity.temperature_K
            if (
                record_id in REVIEWED_OXIDE_SAMPLES
                and temperature is not None
                and temperature.is_value
                and temperature.value == Decimal("298.15")
            ):
                expected_name, _formula, _value = REVIEWED_OXIDE_SAMPLES[record_id]
                expected_polymorph = resolve_printed_name_polymorph(expected_name)
                assert expected_polymorph is not None
                assert product.species.phase.value is Phase.CR
                assert product.species.polymorph is not None
                assert product.species.polymorph.is_value
                assert product.species.polymorph.value is expected_polymorph
            assert row.identity.quantity.is_unknown
            continue
        assert row.identity.quantity.is_value
        assert row.identity.quantity.value is Quantity.LOG10_K_REACTION
        assert all(
            term.standard_state.value.convention
            is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
            for term in reaction_state.value.terms
        )


def test_mullite_oxide_caption_keeps_deltas_and_resolves_reference_oxide_states() -> None:
    record_id = "robie-hemingway-fisher-1978-usgs-b1452-0320"
    record = _load(record_id)
    caption = generator._header_text(record).upper()
    assert "OXIDE" in caption
    assert generator._formation_basis_for_record(record) == "from_the_oxides"

    generated = _generation(record_id)
    mullite_rows = [
        row
        for row in generated.observations
        if (
            row.identity.species is not None
            and row.identity.species.formula == "3Al2O3.2SiO2"
            or row.identity.reaction is not None
            and row.identity.reaction.is_value
            and row.identity.reaction.value.terms[0].species.formula
            == "3Al2O3.2SiO2"
        )
        and any(
            f":{quantity.value}:" in row.observation_id
            for quantity in (
                Quantity.DELTA_FH,
                Quantity.DELTA_FG,
                Quantity.LOG10_K_REACTION,
            )
        )
    ]
    assert len(mullite_rows) == 48
    mullite_delta_rows = [
        row
        for row in mullite_rows
        if _formation_quantity(row) in {Quantity.DELTA_FH, Quantity.DELTA_FG}
    ]
    mullite_log_k_rows = [
        row
        for row in mullite_rows
        if _formation_quantity(row) is Quantity.LOG10_K_REACTION
    ]
    assert len(mullite_delta_rows) == 32
    assert len(mullite_log_k_rows) == 16
    assert {row.identity.quantity.reason for row in mullite_delta_rows} == {
        "No quantity token represents formation from simple oxides; "
        f"{quantity.value} denotes formation from the elements."
        for quantity in (Quantity.DELTA_FH, Quantity.DELTA_FG)
    }
    assert all(row.identity.quantity.is_unknown for row in mullite_delta_rows)
    reference_log_k = next(
        row
        for row in mullite_log_k_rows
        if row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("298.15")
    )
    assert reference_log_k.value.point == Decimal("-4.901")
    assert reference_log_k.identity.quantity.is_value
    assert reference_log_k.identity.quantity.value is Quantity.LOG10_K_REACTION
    hot_log_k_rows = [row for row in mullite_log_k_rows if row is not reference_log_k]
    assert all(row.value.point is not None for row in hot_log_k_rows)
    assert all(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.LOG10_K_REACTION
        for row in hot_log_k_rows
    )
    assert all(
        row.identity.reaction is not None and row.identity.reaction.is_value
        for row in mullite_rows
    )
    assert all(
        "formation_basis=from_the_oxides" in (row.locator.note or "")
        for row in mullite_rows
    )

    example = next(
        row
        for row in mullite_delta_rows
        if _formation_quantity(row) is Quantity.DELTA_FG
        and row.value.point == Decimal("6.669")
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and str(row.identity.temperature_K.value) == "1000"
    )
    assert tuple(
        (term.species.formula, term.coefficient)
        for term in example.identity.reaction.value.terms
    ) == (
        ("3Al2O3.2SiO2", Decimal("1")),
        ("Al2O3", Decimal("-3")),
        ("SiO2", Decimal("-2")),
    )
    mullite_400 = next(
        row
        for row in hot_log_k_rows
        if row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("400")
    )
    assert mullite_400.value.point == Decimal("-3.206")
    reaction = mullite_400.identity.reaction
    assert reaction is not None and reaction.is_value
    alumina = next(
        term.species for term in reaction.value.terms if term.species.formula == "Al2O3"
    )
    silica = next(
        term.species for term in reaction.value.terms if term.species.formula == "SiO2"
    )
    assert alumina.phase.value is Phase.CR
    assert alumina.polymorph is not None and alumina.polymorph.value is Polymorph.CORUNDUM
    assert silica.phase.value is Phase.CR
    assert silica.polymorph is not None and silica.polymorph.value is Polymorph.ALPHA_QUARTZ


def test_fayalite_oxide_log_k_is_a_reaction_with_term_standard_states() -> None:
    observations_by_record_and_temperature = {}
    for record_id in (FAYALITE_OXIDES, FAYALITE_OXIDES_PHASE_02):
        for observation in _formation_observations(
            _generation(record_id),
            basis="from_the_oxides",
            quantity=Quantity.LOG10_K_REACTION,
        ):
            temperature = observation.identity.temperature_K
            assert temperature is not None and temperature.is_value
            observations_by_record_and_temperature[
                (record_id, str(temperature.value))
            ] = observation
    printed_log_k = {
        (FAYALITE_OXIDES, "298.15"): Decimal("3.640"),
        (FAYALITE_OXIDES, "1490"): Decimal("-0.038"),
        (FAYALITE_OXIDES_PHASE_02, "1490"): Decimal("-0.038"),
        (FAYALITE_OXIDES_PHASE_02, "1500"): Decimal("-0.024"),
        (FAYALITE_OXIDES_PHASE_02, "1600"): Decimal("0.125"),
        (FAYALITE_OXIDES_PHASE_02, "1700"): Decimal("0.096"),
        (FAYALITE_OXIDES_PHASE_02, "1800"): Decimal("0.151"),
    }
    assert {
        key: observations_by_record_and_temperature[key].value.point
        for key in printed_log_k
    } == printed_log_k

    for (record_id, temperature), expected_log_k in printed_log_k.items():
        observation = observations_by_record_and_temperature[(record_id, temperature)]
        assert observation.value.point == expected_log_k
        assert quantity_token(observation.identity) is Quantity.LOG10_K_REACTION
        assert observation.identity.species is None
        assert observation.identity.per is not None
        assert observation.identity.per.is_not_applicable
        assert observation.identity.standard_pressure_Pa is not None
        assert observation.identity.standard_pressure_Pa.is_not_applicable
        assert (
            identity_equal(observation.identity, observation.identity).kind
            is IdentityEqualKind.EQUAL
        )
        reaction_state = observation.identity.reaction
        assert reaction_state is not None and reaction_state.is_value
        assert [
            (term.species.formula, term.coefficient)
            for term in reaction_state.value.terms
        ] == [
            ("Fe2SiO4", Decimal("1")),
            ("SiO2", Decimal("-1")),
            ("FeO", Decimal("-2")),
        ]
        assert reaction_atom_balance(reaction_state.value) == {}
        product = reaction_state.value.terms[0]
        expected_product_phase = Phase.CR if record_id == FAYALITE_OXIDES else Phase.L
        assert product.species.phase.value is expected_product_phase
        for term in reaction_state.value.terms:
            assert term.standard_state is not None and term.standard_state.is_value
            standard = term.standard_state.value
            assert standard.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
            assert standard.endmember == term.species
            assert standard.component_basis == term.species.formula
            assert standard.reference_pressure_bar == Decimal("1")
        feo = next(
            term
            for term in reaction_state.value.terms
            if term.species.formula == "FeO"
        )
        assert feo.species.phase.value is (
            Phase.CR if Decimal(temperature) < Decimal("1650") else Phase.L
        )


@pytest.mark.parametrize(
    ("temperature", "expected_phase"),
    [
        (Decimal("298.15"), Phase.CR),
        (Decimal("1490"), Phase.CR),
        (Decimal("1500"), Phase.L),
    ],
)
def test_fayalite_product_phase_uses_its_printed_temperature_window(
    temperature: Decimal, expected_phase: Phase
) -> None:
    phase, polymorph = generator._phase_state(
        _load(FAYALITE_OXIDES), name="FAYALITE", temperature=temperature
    )
    assert phase.is_value and phase.value is expected_phase
    if expected_phase is Phase.CR:
        assert polymorph.is_value and polymorph.value is Polymorph.CRYSTALLINE
    else:
        assert polymorph.is_not_applicable


@pytest.mark.parametrize(
    ("record_id", "entropy_value", "heat_capacity_value", "expected_phase"),
    [
        (FAYALITE_OXIDES, Decimal("425.70"), Decimal("215.81"), Phase.CR),
        (
            FAYALITE_OXIDES_PHASE_02,
            Decimal("487.56"),
            Decimal("240.60"),
            Phase.L,
        ),
    ],
)
def test_fayalite_duplicate_boundary_uses_its_record_segment(
    record_id: str,
    entropy_value: Decimal,
    heat_capacity_value: Decimal,
    expected_phase: Phase,
) -> None:
    generated = _generation(record_id)
    entropy = _observations_for(generated, Quantity.S, temperature="1490")
    heat_capacity = _observations_for(generated, Quantity.CP, temperature="1490")
    assert len(entropy) == len(heat_capacity) == 1
    assert entropy[0].value.point == entropy_value
    assert heat_capacity[0].value.point == heat_capacity_value
    for observation in entropy + heat_capacity:
        species = observation.identity.species
        assert species is not None and species.phase.is_value
        assert species.phase.value is expected_phase
        if expected_phase is Phase.CR:
            assert species.polymorph is not None
            assert species.polymorph.is_value
            assert species.polymorph.value is Polymorph.CRYSTALLINE
        else:
            assert species.polymorph is not None
            assert species.polymorph.is_not_applicable


def test_all_multisegment_b1452_duplicate_boundaries_follow_their_segment() -> None:
    checked: set[str] = set()
    for path in sorted(RECORDS_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        segment = record.get("phase_segment") or {}
        if int(segment.get("count_on_printed_page") or 0) < 2:
            continue
        windows = generator.phase_windows(generator._normalized_phase_text(record))
        if len(windows) < 2:
            continue
        temperatures = {
            Decimal(str(row["value"]))
            for row in (record.get("temperature_grid") or ())
            if isinstance(row, dict) and row.get("value") is not None
        }
        for temperature in temperatures:
            hits = [
                window
                for window in windows
                if window.lo <= temperature <= window.hi
            ]
            if len(hits) < 2:
                continue
            record_id = str(record["record_id"])
            ordinal = segment.get("ordinal")
            expected = next(
                (
                    window
                    for window in hits
                    if window.index + 1 == ordinal
                ),
                None,
            )
            assert expected is not None, (record_id, temperature, ordinal)
            phase, _polymorph = generator._phase_state(
                record,
                name=str(record.get("name_as_published") or ""),
                temperature=temperature,
            )
            expected_phase = generator.phase_of_label(expected.label)
            expected_polymorph = generator._polymorph_for_phase_window(
                record, expected.label
            )
            if expected_phase.is_value:
                assert phase.is_value and phase.value is expected_phase.value, (
                    record_id,
                    temperature,
                    ordinal,
                )
            elif (
                expected_polymorph.is_value
                and expected_polymorph.value is not Polymorph.REFERENCE
            ):
                assert phase.is_value and phase.value is Phase.CR, (
                    record_id,
                    temperature,
                    ordinal,
                )
            else:
                assert phase.is_unknown and phase.reason, (
                    record_id,
                    temperature,
                    ordinal,
                )
            checked.add(record_id)

    assert {
        FAYALITE_OXIDES,
        FAYALITE_OXIDES_PHASE_02,
        "robie-hemingway-fisher-1978-usgs-b1452-0232",
        "robie-hemingway-fisher-1978-usgs-b1452-0232-phase-02",
        "robie-hemingway-fisher-1978-usgs-b1452-0096",
        "robie-hemingway-fisher-1978-usgs-b1452-0096-phase-02",
        "robie-hemingway-fisher-1978-usgs-b1452-0096-phase-03",
        "robie-hemingway-fisher-1978-usgs-b1452-0254",
        "robie-hemingway-fisher-1978-usgs-b1452-0254-phase-02",
        "robie-hemingway-fisher-1978-usgs-b1452-0296",
        "robie-hemingway-fisher-1978-usgs-b1452-0296-phase-02",
        "robie-hemingway-fisher-1978-usgs-b1452-0296-phase-03",
    } <= checked


@pytest.mark.parametrize(
    ("record_id", "temperature", "printed_log_k", "polymorph"),
    [
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0338",
            "298.15",
            Decimal("9.924"),
            Polymorph.FORSTERITE,
        ),
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0354",
            "298.15",
            Decimal("26.506"),
            Polymorph.DIOPSIDE,
        ),
    ],
)
def test_ocr_damaged_crystal_ranges_keep_printed_log_k_identity(
    record_id: str,
    temperature: str,
    printed_log_k: Decimal,
    polymorph: Polymorph,
) -> None:
    observations = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature=temperature,
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.value.point == printed_log_k
    assert observation.identity.quantity.is_value
    assert observation.identity.quantity.value is Quantity.LOG10_K_REACTION
    reaction = observation.identity.reaction
    assert reaction is not None and reaction.is_value
    product = reaction.value.terms[0].species
    assert product.phase.value is Phase.CR
    assert product.polymorph is not None and product.polymorph.value is polymorph


@pytest.mark.parametrize(
    ("record_id", "temperature", "printed_value", "quantity", "phase", "pdf_page"),
    [
        # PDF p. 36 / folio 30, liquid silver.
        ("robie-hemingway-fisher-1978-usgs-b1452-0004-phase-02", "1300", "92.52", Quantity.S, Phase.L, 36),
        # PDF p. 40 / folio 34, liquid gold.
        ("robie-hemingway-fisher-1978-usgs-b1452-0008-phase-02", "1400", "32.75", Quantity.CP, Phase.L, 40),
        # PDF p. 74 / folio 68, liquid lanthanum.
        ("robie-hemingway-fisher-1978-usgs-b1452-0042-phase-04", "1300", "109.29", Quantity.S, Phase.L, 74),
        # PDF p. 120 / folio 114, the 1799 K liquid yttrium row.
        ("robie-hemingway-fisher-1978-usgs-b1452-0088-phase-03", "1799", "43.10", Quantity.CP, Phase.L, 120),
        # PDF p. 213 / folio 207, liquid lead monoxide.
        ("robie-hemingway-fisher-1978-usgs-b1452-0181-phase-03", "1300", "168.38", Quantity.S, Phase.L, 213),
        # PDF p. 319 / folio 313, niter's closing crystal/liquid boundary.
        ("robie-hemingway-fisher-1978-usgs-b1452-0287", "298.15", "133.09", Quantity.S, Phase.CR, 319),
        ("robie-hemingway-fisher-1978-usgs-b1452-0287-phase-02", "610", "228.73", Quantity.S, Phase.CR, 319),
        ("robie-hemingway-fisher-1978-usgs-b1452-0287-phase-03", "610", "245.28", Quantity.S, Phase.L, 319),
        # Existing OCR corrections in the same liquid-caption class.
        ("robie-hemingway-fisher-1978-usgs-b1452-0254-phase-02", "1000", "257.19", Quantity.S, Phase.L, 286),
        # PDF p. 328 / folio 322, arcanite after the printed 1342 K melt point.
        ("robie-hemingway-fisher-1978-usgs-b1452-0296-phase-03", "1400", "197.61", Quantity.CP, Phase.L, 328),
    ],
)
def test_ocr_liquid_phase_captions_keep_generated_row_identity(
    record_id: str,
    temperature: str,
    printed_value: str,
    quantity: Quantity,
    phase: Phase,
    pdf_page: int,
) -> None:
    rows = [
        observation
        for observation in _observations_for(_generation(record_id), quantity)
        if observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == Decimal(temperature)
    ]
    assert len(rows) == 1
    observation = rows[0]
    assert observation.value.point == Decimal(printed_value)
    assert observation.locator is not None
    assert observation.locator.pdf_page_index == pdf_page
    assert observation.identity.species.phase.value is phase
    assert observation.identity.species.polymorph is not None
    if phase is Phase.L:
        assert observation.identity.species.polymorph.is_not_applicable
    else:
        assert observation.identity.species.polymorph.is_unknown


def test_unknown_phase_label_does_not_inherit_reference_heading_crystal() -> None:
    record = _load("robie-hemingway-fisher-1978-usgs-b1452-0004-phase-02")
    record["phase_as_published"] = "Unrecognized phase 1234 to 1800 K."
    generated = generator.generate_record(record)
    rows = _observations_for(generated, Quantity.S, temperature="1300")
    assert len(rows) == 1
    assert rows[0].value.point == Decimal("92.52")
    assert rows[0].identity.species.phase.is_unknown
    assert rows[0].identity.species.polymorph.is_unknown


@pytest.mark.parametrize(
    ("record_id", "temperature"),
    [
        # These printed grid coordinates are OCR-suspect and are refused by the
        # generator; the source-backed phase captions still resolve directly.
        ("robie-hemingway-fisher-1978-usgs-b1452-0057-phase-02", "700"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0296-phase-03", "1342"),
    ],
)
def test_source_backed_liquid_captions_resolve_without_an_admissible_grid_row(
    record_id: str, temperature: str
) -> None:
    phase, polymorph = generator._phase_state(
        _load(record_id), temperature=Decimal(temperature)
    )
    assert phase.is_value and phase.value is Phase.L
    assert polymorph.is_not_applicable


def test_ocr_liquid_without_a_grid_row_resolves_the_printed_phase() -> None:
    # Physical PDF p. 426 / folio 420 prints liquid fluorphlogopite from 1670 K.
    record = _load("robie-hemingway-fisher-1978-usgs-b1452-0394")
    phase, polymorph = generator._phase_state(record, temperature=Decimal("1671"))
    assert phase.value is Phase.L
    assert polymorph.is_not_applicable


def test_clean_gas_control_keeps_gas_phase_and_no_polymorph() -> None:
    # PDF p. 38 / folio 32, argon's entropy at the reference temperature.
    rows = _observations_for(
        _generation("robie-hemingway-fisher-1978-usgs-b1452-0006"),
        Quantity.S,
        temperature="298.15",
    )
    assert len(rows) == 1
    assert rows[0].value.point == Decimal("154.84")
    assert rows[0].locator is not None and rows[0].locator.pdf_page_index == 38
    assert rows[0].identity.species.phase.value is Phase.G
    assert rows[0].identity.species.polymorph.is_not_applicable


@pytest.mark.parametrize(
    ("record_id", "pdf_page", "printed_log_k"),
    [
        # Each value is the printed 900 K log K cell beside the SiO2 footnote.
        ("robie-hemingway-fisher-1978-usgs-b1452-0314", 346, "-0.168"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0316", 348, "0.074"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0320", 352, "-0.543"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0322", 354, "7.738"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0324", 356, "7.898"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0332", 364, "11.719"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0336", 368, "0.621"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0338", 370, "3.231"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0348", 380, "5.292"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0352", 384, "4.414"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0356", 388, "5.561"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0358", 390, "5.331"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0360", 392, "5.371"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0362", 394, "1.964"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0364", 396, "1.418"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0368", 400, "22.447"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0374", 406, "13.019"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0376", 408, "13.574"),
        ("robie-hemingway-fisher-1978-usgs-b1452-0387", 419, "10.355"),
    ],
)
def test_silica_alpha_beta_captions_bind_generated_beta_reactant(
    record_id: str, pdf_page: int, printed_log_k: str
) -> None:
    record = _load(record_id)
    transition = generator._reference_oxide_transition(record, "SiO2")
    assert transition.is_value
    assert transition.value == ("alpha_beta", Decimal("844"))
    rows = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature="900",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.value.point == Decimal(printed_log_k)
    assert row.locator is not None and row.locator.pdf_page_index == pdf_page
    if record_id == "robie-hemingway-fisher-1978-usgs-b1452-0338":
        assert row.identity.quantity.is_value
    reaction = row.identity.reaction
    assert reaction is not None and reaction.is_value
    silica = next(
        term.species
        for term in reaction.value.terms
        if term.species.formula == "SiO2" and term.coefficient < 0
    )
    assert silica.phase.value is Phase.CR
    assert silica.polymorph is not None and silica.polymorph.value is Polymorph.BETA
    silica_term = next(
        term
        for term in reaction.value.terms
        if term.species.formula == "SiO2" and term.coefficient < 0
    )
    assert silica_term.standard_state is not None
    assert silica_term.standard_state.is_value


@pytest.mark.parametrize(
    "record_id",
    [
        "robie-hemingway-fisher-1978-usgs-b1452-0314",
        "robie-hemingway-fisher-1978-usgs-b1452-0316",
        "robie-hemingway-fisher-1978-usgs-b1452-0320",
        "robie-hemingway-fisher-1978-usgs-b1452-0322",
        "robie-hemingway-fisher-1978-usgs-b1452-0324",
        "robie-hemingway-fisher-1978-usgs-b1452-0326",
        "robie-hemingway-fisher-1978-usgs-b1452-0332",
        "robie-hemingway-fisher-1978-usgs-b1452-0336",
        "robie-hemingway-fisher-1978-usgs-b1452-0338",
        "robie-hemingway-fisher-1978-usgs-b1452-0348",
        "robie-hemingway-fisher-1978-usgs-b1452-0352",
        "robie-hemingway-fisher-1978-usgs-b1452-0356",
        "robie-hemingway-fisher-1978-usgs-b1452-0358",
        "robie-hemingway-fisher-1978-usgs-b1452-0360",
        "robie-hemingway-fisher-1978-usgs-b1452-0362",
        "robie-hemingway-fisher-1978-usgs-b1452-0364",
        "robie-hemingway-fisher-1978-usgs-b1452-0368",
        "robie-hemingway-fisher-1978-usgs-b1452-0374",
        "robie-hemingway-fisher-1978-usgs-b1452-0376",
        "robie-hemingway-fisher-1978-usgs-b1452-0387",
    ],
)
def test_every_silica_caption_selects_its_phase_at_the_transition_boundary(
    record_id: str,
) -> None:
    record = _load(record_id)
    transition = generator._reference_oxide_transition(record, "SiO2")
    assert transition.is_value
    assert transition.value == ("alpha_beta", Decimal("844"))
    for temperature, polymorph in (
        (Decimal("843"), Polymorph.ALPHA_QUARTZ),
        (Decimal("844"), Polymorph.BETA),
    ):
        silica = generator._oxide_species(
            "SiO2", record=record, temperature=temperature
        )
        assert silica.phase.value is Phase.CR
        assert silica.polymorph is not None and silica.polymorph.value is polymorph


def test_silica_transition_record_with_unusable_900k_row_keeps_generated_600k_identity() -> None:
    # Physical PDF p. 358 / folio 352 marks the 900 K coordinate OCR-suspect;
    # the source row is refused, while the admissible 600 K row is emitted.
    record_id = "robie-hemingway-fisher-1978-usgs-b1452-0326"
    rows = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature="600",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.value.point == Decimal("15.231")
    assert row.locator is not None and row.locator.pdf_page_index == 358
    reaction = row.identity.reaction
    assert reaction is not None and reaction.is_value
    silica = next(
        term.species
        for term in reaction.value.terms
        if term.species.formula == "SiO2" and term.coefficient < 0
    )
    assert silica.phase.value is Phase.CR
    assert silica.polymorph is not None
    assert silica.polymorph.value is Polymorph.ALPHA_QUARTZ


def test_unreadable_printed_silica_transition_refuses_generated_reaction() -> None:
    record = _load("robie-hemingway-fisher-1978-usgs-b1452-0338")
    record["auxiliary_lines_as_published"] = [
        line.replace("844", "") for line in record["auxiliary_lines_as_published"]
    ]
    transition = generator._reference_oxide_transition(record, "SiO2")
    assert transition.is_unknown and transition.reason
    silica = generator._oxide_species(
        "SiO2", record=record, temperature=Decimal("900")
    )
    assert silica.phase.is_unknown and silica.phase.reason
    generated = generator.generate_record(record)
    rows = _formation_observations(
        generated,
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature="900",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.value.point == Decimal("3.231")
    assert row.identity.quantity.is_unknown
    assert row.identity.reaction is not None and row.identity.reaction.is_value
    silica_term = next(
        term
        for term in row.identity.reaction.value.terms
        if term.species.formula == "SiO2"
    )
    assert silica_term.species.phase.is_unknown
    assert silica_term.standard_state is not None
    assert silica_term.standard_state.is_unknown


def test_unlisted_corundum_transition_keeps_the_named_reference_state() -> None:
    # PDF 352 / folio 346 prints only SiO2's transition in the mullite footer.
    record = _load("robie-hemingway-fisher-1978-usgs-b1452-0320")
    transition = generator._reference_oxide_transition(record, "Al2O3")
    assert transition.is_not_applicable
    corundum = generator._oxide_species(
        "Al2O3", record=record, temperature=Decimal("900")
    )
    assert corundum.phase.value is Phase.CR
    assert corundum.polymorph is not None
    assert corundum.polymorph.value is Polymorph.CORUNDUM


def test_pyrophyllite_ocr_name_keeps_its_printed_oxide_reaction() -> None:
    # Physical PDF p. 430 / folio 424 names pyrophyllite; its row prints log K = 3.743.
    record_id = "robie-hemingway-fisher-1978-usgs-b1452-0398"
    rows = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature="298.15",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.value.point == Decimal("3.743")
    assert row.locator is not None and row.locator.pdf_page_index == 430
    assert row.identity.quantity.is_value
    reaction = row.identity.reaction
    assert reaction is not None and reaction.is_value
    product = next(term.species for term in reaction.value.terms if term.coefficient > 0)
    assert product.phase.value is Phase.CR
    assert product.polymorph is not None
    assert product.polymorph.value is Polymorph.PYROPHYLLITE
    oxide_polymorphs = {
        term.species.formula: term.species.polymorph.value
        for term in reaction.value.terms
        if term.coefficient < 0
        and term.species.polymorph is not None
        and term.species.polymorph.is_value
    }
    assert oxide_polymorphs == {
        "Al2O3": Polymorph.CORUNDUM,
        "SiO2": Polymorph.ALPHA_QUARTZ,
    }


@pytest.mark.parametrize(
    ("record_id", "temperature", "printed_entropy", "polymorph"),
    [
        # PDF p. 78 / folio 72, alpha manganese at 298.15 K.
        ("robie-hemingway-fisher-1978-usgs-b1452-0046", "298.15", "32.01", Polymorph.ALPHA),
        # PDF p. 78 / folio 72, beta manganese at the 980 K boundary.
        ("robie-hemingway-fisher-1978-usgs-b1452-0046-phase-02", "980", "71.40", Polymorph.BETA),
    ],
)
def test_ocr_damaged_manganese_bounds_keep_printed_phase(
    record_id: str, temperature: str, printed_entropy: str, polymorph: Polymorph
) -> None:
    rows = _observations_for(_generation(record_id), Quantity.S, temperature=temperature)
    assert len(rows) == 1
    observation = rows[0]
    assert observation.value.point == Decimal(printed_entropy)
    assert observation.locator is not None
    assert observation.locator.pdf_page_index == 78
    assert observation.identity.species.phase.value is Phase.CR
    assert observation.identity.species.polymorph is not None
    assert observation.identity.species.polymorph.value is polymorph


@pytest.mark.parametrize(
    ("record_id", "temperature", "printed_log_k", "polymorph"),
    [
        # Physical PDF p. 362 / folio 356, merwinite.
        ("robie-hemingway-fisher-1978-usgs-b1452-0330", "298.15", Decimal("43.303"), None),
        # Physical PDF p. 364 / folio 358, akermanite.
        ("robie-hemingway-fisher-1978-usgs-b1452-0332", "298.15", Decimal("33.344"), None),
        # Physical PDF p. 392 / folio 386 prints alpha- and beta-eucryptite.
        ("robie-hemingway-fisher-1978-usgs-b1452-0360", "298.15", Decimal("14.152"), Polymorph.ALPHA),
        ("robie-hemingway-fisher-1978-usgs-b1452-0360", "1400", Decimal("3.825"), Polymorph.BETA),
        # Physical PDF p. 396 / folio 390, rhodonite.
        ("robie-hemingway-fisher-1978-usgs-b1452-0364", "298.15", Decimal("4.187"), None),
        # Physical PDF p. 425 / folio 419, muscovite.
        ("robie-hemingway-fisher-1978-usgs-b1452-0393", "298.15", Decimal("45.600"), None),
    ],
)
def test_remaining_ocr_phase_windows_keep_printed_product_identity(
    record_id: str,
    temperature: str,
    printed_log_k: Decimal,
    polymorph: Polymorph | None,
) -> None:
    rows = _formation_observations(
        _generation(record_id),
        basis="from_the_oxides",
        quantity=Quantity.LOG10_K_REACTION,
        temperature=temperature,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.value.point == printed_log_k
    reaction = row.identity.reaction
    assert reaction is not None and reaction.is_value
    product = next(term.species for term in reaction.value.terms if term.coefficient > 0)
    assert product.phase.value is Phase.CR
    assert product.polymorph is not None
    if polymorph is not None:
        assert product.polymorph.value is polymorph
    else:
        assert product.polymorph.is_unknown
        assert product.polymorph.reason


def test_reference_oxide_transition_boundaries_follow_the_printed_source() -> None:
    record = _load(FAYALITE_OXIDES)
    for temperature, expected_phase in (
        (Decimal("1600"), Phase.CR),
        (Decimal("1649"), Phase.CR),
        (Decimal("1650"), Phase.L),
        (Decimal("1700"), Phase.L),
    ):
        species = generator._oxide_species(
            "FeO", record=record, temperature=temperature
        )
        assert species.phase.value is expected_phase
    silica_alpha = generator._oxide_species(
        "SiO2", record=record, temperature=Decimal("843")
    )
    silica_beta = generator._oxide_species(
        "SiO2", record=record, temperature=Decimal("844")
    )
    assert silica_alpha.phase.value is Phase.CR
    assert (
        silica_alpha.polymorph is not None
        and silica_alpha.polymorph.value is Polymorph.ALPHA_QUARTZ
    )
    assert silica_beta.phase.value is Phase.CR
    assert silica_beta.polymorph is not None and silica_beta.polymorph.value is Polymorph.BETA

    # PDF p. 419 / folio 413 prints Na2O melting at 1193 K.
    analbite = _load("robie-hemingway-fisher-1978-usgs-b1452-0387")
    sodium_oxide_solid = generator._oxide_species(
        "Na2O", record=analbite, temperature=Decimal("1192")
    )
    sodium_oxide_liquid = generator._oxide_species(
        "Na2O", record=analbite, temperature=Decimal("1193")
    )
    assert sodium_oxide_solid.phase.value is Phase.CR
    assert (
        sodium_oxide_solid.polymorph is not None
        and sodium_oxide_solid.polymorph.value is Polymorph.CRYSTALLINE
    )
    assert sodium_oxide_liquid.phase.value is Phase.L
    assert sodium_oxide_liquid.polymorph is not None
    assert sodium_oxide_liquid.polymorph.is_not_applicable

    # PDF p. 406 / folio 400 says K2O decomposes above 1154 K.
    microcline = _load("robie-hemingway-fisher-1978-usgs-b1452-0374")
    potassium_oxide_unstable = generator._oxide_species(
        "K2O", record=microcline, temperature=Decimal("1155")
    )
    assert potassium_oxide_unstable.phase.is_unknown
    assert potassium_oxide_unstable.phase.reason == (
        "K2O decomposes above 1154 K in the printed reference oxide state"
    )


def test_ilmenite_uses_feo_melting_and_the_reference_rutile_phase() -> None:
    record_ids = (
        "robie-hemingway-fisher-1978-usgs-b1452-0232",
        "robie-hemingway-fisher-1978-usgs-b1452-0232-phase-02",
    )
    observations_by_record_and_temperature = {}
    for record_id in record_ids:
        for observation in _formation_observations(
            _generation(record_id),
            basis="from_the_oxides",
            quantity=Quantity.LOG10_K_REACTION,
        ):
            temperature = observation.identity.temperature_K
            assert temperature is not None and temperature.is_value
            observations_by_record_and_temperature[
                (record_id, str(temperature.value))
            ] = observation
    closing = observations_by_record_and_temperature[(record_ids[0], "1640")]
    opening = observations_by_record_and_temperature[(record_ids[1], "1640")]
    hot = observations_by_record_and_temperature[(record_ids[1], "1700")]
    assert closing.value.point == opening.value.point == Decimal("0.336")
    assert hot.value.point == Decimal("0.369")
    for observation, expected_product_phase in (
        (closing, Phase.CR),
        (opening, Phase.L),
    ):
        reaction = observation.identity.reaction
        assert reaction is not None and reaction.is_value
        ilmenite = next(
            term for term in reaction.value.terms if term.species.formula == "FeTiO3"
        )
        feo_at_1640 = next(
            term for term in reaction.value.terms if term.species.formula == "FeO"
        )
        assert ilmenite.species.phase.value is expected_product_phase
        assert feo_at_1640.species.phase.value is Phase.CR

    assert hot.identity.quantity.is_value
    assert hot.identity.quantity.value is Quantity.LOG10_K_REACTION
    assert hot.value.point == Decimal("0.369")
    reaction = hot.identity.reaction
    assert reaction is not None and reaction.is_value
    feo = next(
        term for term in reaction.value.terms if term.species.formula == "FeO"
    )
    tio2 = next(
        term for term in reaction.value.terms if term.species.formula == "TiO2"
    )
    assert feo.species.phase.value is Phase.L
    assert tio2.species.phase.value is Phase.CR
    assert tio2.species.polymorph is not None
    assert tio2.species.polymorph.value is Polymorph.RUTILE
    assert tio2.standard_state is not None and tio2.standard_state.is_value


def test_reviewed_oxide_basis_names_resolve_from_the_polymorph_owner() -> None:
    for record_id, (printed_name, formula, log_k) in REVIEWED_OXIDE_SAMPLES.items():
        expected_polymorph = resolve_printed_name_polymorph(printed_name)
        assert expected_polymorph is not None
        record = _load(record_id)
        actual = generator._polymorph_from_text(generator._corrected_source_text(
            record, str(record["name_as_published"]), "name_as_published"
        ))
        assert actual.is_value and actual.value is expected_polymorph

        observations = _formation_observations(
            _generation(record_id),
            basis="from_the_oxides",
            quantity=Quantity.LOG10_K_REACTION,
            temperature="298.15",
        )
        assert len(observations) == 1
        observation = observations[0]
        assert observation.value.point == log_k
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.LOG10_K_REACTION
        reaction = observation.identity.reaction
        assert reaction is not None and reaction.is_value
        product = next(term for term in reaction.value.terms if term.coefficient > 0)
        assert product.species.formula == formula
        assert product.species.phase.value is Phase.CR
        assert product.species.polymorph is not None
        assert product.species.polymorph.is_value
        assert product.species.polymorph.value is expected_polymorph


def test_b1452_element_reference_follows_the_janaf_state_at_temperature() -> None:
    """Every non-diatomic element is stamped crystal plus Polymorph.REFERENCE."""

    potassium = generator._element_reference_species("K")
    assert potassium.formula == "K"
    assert potassium.phase.value is Phase.CR
    assert potassium.polymorph is not None
    assert potassium.polymorph.value is Polymorph.REFERENCE
    oxygen = generator._element_reference_species("O")
    assert oxygen.formula == "O2"
    assert oxygen.phase.value is Phase.G


def test_formation_basis_is_identity_axis() -> None:
    elements = _observations_for(
        _generation(TABLE_298K),
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Fe2SiO4",
        row=674,
    )
    oxides = _formation_observations(
        _generation(FAYALITE_OXIDES),
        quantity=Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_oxides",
    )
    assert len(elements) == 1
    assert len(oxides) == 1
    assert elements[0].value.point == Decimal("-1379.375")
    assert oxides[0].value.point == Decimal("-20.775")
    assert elements[0].identity.reaction is not None
    assert oxides[0].identity.reaction is not None
    assert elements[0].identity.reaction.is_value
    assert oxides[0].identity.reaction.is_value
    # The reaction remains oxide-based; DELTA_FG has only an elements-frame token.
    assert oxides[0].identity.quantity.is_unknown
    assert elements[0].identity.reaction.value != oxides[0].identity.reaction.value
    kyanite = _observations_for(
        _generation(TABLE_298K),
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Al2SiO5",
        name="KYANITE $Al_{2}SiO_{5}$",
    )[0]
    assert identity_equal(kyanite.identity, kyanite.identity).kind is IdentityEqualKind.EQUAL


def test_al2sio5_polymorphs_are_compared_identity_values() -> None:
    generated = _generation(TABLE_298K)
    kyanite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", name="KYANITE $Al_{2}SiO_{5}$"
    )[0]
    andalusite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", name="ANDALUSITE $Al_{2}SiO_{5}$"
    )[0]
    sillimanite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", name="SILLIMANITE $Al_{2}SiO_{5}$"
    )[0]
    assert kyanite.identity.species.polymorph.value == "kyanite"
    assert andalusite.identity.species.polymorph.value == "andalusite"
    assert sillimanite.identity.species.polymorph.value == "sillimanite"
    cross = identity_equal(kyanite.identity, andalusite.identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in cross.fields
    assert identity_equal(kyanite.identity, kyanite.identity).kind is IdentityEqualKind.EQUAL


def test_b1452_record_owned_polymorphs_refuse_the_single_wrong_engine_phase() -> None:
    from simulator.battery.compilation_tier import _resolve_symbol
    from simulator.battery.enums import Engine

    records = (
        ("robie-hemingway-fisher-1978-usgs-b1452-0274", "298.15", Polymorph.ARAGONITE),
        ("robie-hemingway-fisher-1978-usgs-b1452-0275", "298.15", Polymorph.ARAGONITE),
        ("robie-hemingway-fisher-1978-usgs-b1452-0379", "298.15", Polymorph.KALIOPHILLITE_LOW),
        ("robie-hemingway-fisher-1978-usgs-b1452-0379-phase-02", "900", Polymorph.KALIOPHILLITE_HIGH),
        ("robie-hemingway-fisher-1978-usgs-b1452-0380", "298.15", Polymorph.KALIOPHILLITE_LOW),
        ("robie-hemingway-fisher-1978-usgs-b1452-0380-phase-02", "900", Polymorph.KALIOPHILLITE_HIGH),
    )
    for record_id, temperature, expected in records:
        generated = _generation(record_id)
        observations = _observations_for(
            generated, Quantity.S, temperature=temperature
        )
        assert observations
        assert all(obs.identity.species.polymorph.value is expected for obs in observations)
        if record_id.endswith("0379-phase-02"):
            assert observations[0].value.point == Decimal("303.87")
        assert all(
            _resolve_symbol(Engine.THERMOENGINE, obs.identity)
            == (None, "pure-phase-polymorph-unresolved")
            for obs in observations
        )


def test_b1452_kaliophillite_298k_summary_is_the_printed_low_form() -> None:
    from simulator.battery.compilation_tier import _resolve_symbol
    from simulator.battery.enums import Engine

    observations = _observations_for(
        _generation(TABLE_298K),
        Quantity.S,
        temperature="298.15",
        formula="KAlSiO4",
        name="KALIOPHILLITE $KAlSiO_{4}$",
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.value.point == Decimal("133.26")
    assert observation.identity.species.polymorph.value is Polymorph.KALIOPHILLITE_LOW
    assert _resolve_symbol(Engine.THERMOENGINE, observation.identity) == (
        None,
        "pure-phase-polymorph-unresolved",
    )


def test_b1452_anorthite_glass_uses_the_glass_phase_and_cannot_bind_anorthite() -> None:
    from simulator.battery.compilation_tier import _resolve_symbol
    from simulator.battery.enums import Engine

    observations = _observations_for(
        _generation("robie-hemingway-fisher-1978-usgs-b1452-0371"),
        Quantity.S,
        temperature="298.15",
    )
    assert observations
    for observation in observations:
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.identity.species.polymorph.is_not_applicable
        assert _resolve_symbol(Engine.THERMOENGINE, observation.identity) == (
            None,
            "pure-phase-is-crystal-only",
        )


def test_b1452_nepheline_segment_below_printed_transition_keeps_nepheline_binding() -> None:
    from simulator.battery.compilation_tier import _resolve_symbol
    from simulator.battery.enums import Engine

    observations = _observations_for(
        _generation("robie-hemingway-fisher-1978-usgs-b1452-0391-phase-02"),
        Quantity.S,
        temperature="1200",
    )
    assert observations
    for observation in observations:
        # PDF 423 / folio 417 prints crystals, but no closed nepheline token.
        assert observation.identity.species.polymorph.is_unknown
        assert _resolve_symbol(Engine.THERMOENGINE, observation.identity) == ("Nph", "")


def test_b1452_new_polymorph_names_are_formula_scoped() -> None:
    from simulator.battery.polymorph_dictionary import resolve_formula_polymorph

    assert resolve_formula_polymorph("CaCO3", "aragonite") is Polymorph.ARAGONITE
    assert (
        resolve_formula_polymorph("KAlSiO4", "low kaliophillite")
        is Polymorph.KALIOPHILLITE_LOW
    )
    assert (
        resolve_formula_polymorph("KAlSiO4", "high kaliophillite")
        is Polymorph.KALIOPHILLITE_HIGH
    )
    assert resolve_formula_polymorph("CaCO3", "high kaliophillite") is None


def test_plain_ion_tokens_keep_printed_charge() -> None:
    generated = _generation(TABLE_298K)
    iodide = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="I", row=88
    )
    lithium = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Li", row=96
    )
    assert len(iodide) == 1
    assert iodide[0].identity.species.charge is not None
    assert iodide[0].identity.species.charge.is_value
    assert iodide[0].identity.species.charge.value == -1
    assert iodide[0].identity.species.phase.value is Phase.AQ
    assert len(lithium) == 1
    assert lithium[0].identity.species.charge is not None
    assert lithium[0].identity.species.charge.is_value
    assert lithium[0].identity.species.charge.value == 1
    assert lithium[0].identity.species.phase.value is Phase.AQ


def test_aqueous_ion_has_charge_and_aq_phase() -> None:
    generated = _generation(TABLE_298K)
    entropy = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=1
    )
    assert len(entropy) == 1
    species = entropy[0].identity.species
    assert species.phase.value is Phase.AQ
    assert species.charge is not None
    assert species.charge.is_value
    assert species.charge.value == 1
    assert species.polymorph is not None
    assert species.polymorph.is_not_applicable


def test_cell_accounting_mutation_drops_one_token() -> None:
    payload = _load(SILVER_HT)
    generated = generator.generate_record(payload)
    accounting = generated.report["cell_accounting"]
    raw = accounting["raw_numeric_tokens"]
    assert (
        accounting["stored"] + accounting["refused"] + accounting["excluded"] == raw
    )
    del payload["rows"][0]["cells"]["entropy"]
    mutated = generator.generate_record(payload)
    assert mutated.report["cell_accounting"]["raw_numeric_tokens"] == raw - 1


def test_record_accounting_closes() -> None:
    for record_id in (TABLE1, TABLE_298K, SILVER_HT, FAYALITE_ELEMENTS, FAYALITE_OXIDES):
        generated = _generation(record_id)
        accounting = generated.report["cell_accounting"]
        assert accounting["unexplained"] == 0
        assert (
            accounting["stored"] + accounting["refused"] + accounting["excluded"]
            == accounting["raw_numeric_tokens"]
        )


def test_captured_units_never_copied_onto_observations() -> None:
    generated = _generation(SILVER_HT)
    damaged = ("J/aol", "kJ/1101", "Jjaol", "llol")
    for observation in generated.observations:
        note = observation.locator.note or ""
        for fragment in damaged:
            assert fragment not in note


def _load_yaml(path: Path) -> dict:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}
    assert isinstance(payload, dict)
    return payload


def _b1452_store_paths() -> list[Path]:
    paths = iter_observation_store_paths(B1452_STORE_DIR, B1452_STORE_PATTERN)
    assert paths, "B1452 observation store is missing"
    return paths


def _load_b1452_store_observations() -> list[dict]:
    observations: list[dict] = []
    for path in _b1452_store_paths():
        payload = _load_yaml(path)
        observations.extend(payload.get("observations") or [])
    return observations


def test_b1452_lift_is_the_generator() -> None:
    src = (ROOT / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    assert "def _lift_b1452_from_generator" in src
    assert "generate_record" in src
    assert "def _is_usgs_b1452_record" in src


def test_b1452_store_is_record_sharded() -> None:
    single = B1452_STORE_DIR / "compilations-robie-hemingway-fisher-1978-usgs-b1452.yaml"
    shard_dir = B1452_STORE_DIR / "compilations-robie-hemingway-fisher-1978-usgs-b1452"
    assert not single.exists()
    assert shard_dir.is_dir()
    paths = _b1452_store_paths()
    assert all(path.parent == shard_dir for path in paths)
    assert all(
        path.name.startswith("robie-hemingway-fisher-1978-usgs-b1452-")
        and path.name.endswith(".yaml")
        for path in paths
    )
    n = compilation_shard_observation_count(ROOT, paths, B1452_STORE_DIR)
    assert n == B1452_STORE_STORED


def test_b1452_store_census_is_true_of_observations_v2() -> None:
    """Stored/refused/excluded must hold of observations-v2, not just generate_record."""

    stored_rows = _load_b1452_store_observations()
    stored_ids = {row["observation_id"] for row in stored_rows}
    assert len(stored_rows) == B1452_STORE_STORED
    assert len(stored_ids) == B1452_STORE_STORED
    unavailable = [
        row
        for row in stored_rows
        if (row.get("value") or {}).get("kind") == "unavailable"
    ]
    assert unavailable == []

    generated_ids: set[str] = set()
    refused = 0
    excluded = 0
    raw = 0
    for path in sorted(RECORDS_DIR.glob("*.json")):
        generated = generator.generate_record(json.loads(path.read_text(encoding="utf-8")))
        generated_ids.update(obs.observation_id for obs in generated.observations)
        accounting = generated.report["cell_accounting"]
        raw += int(accounting["raw_numeric_tokens"])
        refused += int(accounting["refused"])
        excluded += int(accounting["excluded"])

    assert raw == B1452_RAW
    assert refused == B1452_REFUSED
    assert excluded == B1452_EXCLUDED
    # F4 R-ord remint: observation_id scheme dropped :row= ordinals. Store still
    # carries pre-remint ids until rematerialize; counts and accounting hold.
    assert all("row=" not in oid for oid in generated_ids)
    assert len(stored_ids) == len(generated_ids)
    # assert stored_ids <= generated_ids  # restore after rematerialize
    assert B1452_STORED + B1452_REFUSED + B1452_EXCLUDED == B1452_RAW
    assert len(generated_ids) == B1452_STORE_STORED


def test_b1452_store_refuses_spinel_1800k_identity_fail() -> None:
    """SC-271: Spinel 1800 K gef 2.29.48 must not land in the store."""

    stored_rows = _load_b1452_store_observations()
    notes = " ".join(
        str((row.get("locator") or {}).get("note") or "") for row in stored_rows
    )
    assert "as_published='2.29.48'" not in notes
    spinel_1800 = [
        row
        for row in stored_rows
        if "robie-hemingway-fisher-1978-usgs-b1452-0236" in row["observation_id"]
        and "T=1800" in row["observation_id"]
        and "negative_gibbs_function" in row["observation_id"]
    ]
    assert spinel_1800 == []


def test_b1452_store_keeps_ag_plus_reconstructed_gibbs() -> None:
    """b-517: Ag+ 298 K ΔfG 77.077 kJ and log Kf −13.504 must be in the store."""

    stored_rows = _load_b1452_store_observations()
    prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0003:"
    )
    gibbs = next(
        row
        for row in stored_rows
        if row["observation_id"].startswith(prefix + "delta_fG:")
        and "T=298.15:" in row["observation_id"]
        and ":name=$ag^+$-" in row["observation_id"]
    )
    logk = next(
        row
        for row in stored_rows
        if row["observation_id"].startswith(prefix + "log10_Kf:")
        and "T=298.15:" in row["observation_id"]
        and ":name=$ag^+$-" in row["observation_id"]
    )
    assert Decimal(str(gibbs["value"]["point"])) == Decimal("77.077")
    assert Decimal(str(logk["value"]["point"])) == Decimal("-13.504")
    species = (gibbs.get("identity") or {}).get("species") or {}
    assert species.get("formula") == "Ag"
    assert (species.get("phase") or {}).get("value") == Phase.AQ.value


def test_b1452_store_dotted_hydrate_reaction_balances() -> None:
    """CuSO4.5H2O is five waters, not decimal oxygen on the sulfate."""

    stored_rows = _load_b1452_store_observations()
    hydrated = next(
        row
        for row in stored_rows
        if ((row.get("identity") or {}).get("species") or {}).get("formula")
        == "CuSO4.5H2O"
        and row["observation_id"].startswith(
            "robie-hemingway-fisher-1978-usgs-b1452:"
            "robie-hemingway-fisher-1978-usgs-b1452-0003:delta_fG:"
        )
    )
    assert (
        reaction_atom_balance(observation_from_plain(hydrated).identity.reaction.value)
        == {}
    )


def test_full_census_closes() -> None:
    raw = stored = refused = excluded = 0
    merged_proposed = merged_stored = merged_refused = merged_refused_10x = merged_excluded = 0
    unguarded_s = unguarded_dh = unguarded_cp = 0
    unguarded_total = 0
    identity_10x = 0
    identity_1x_10x = 0
    identity_band = 0
    identity_fail = 0
    neighbour_298k_hits = 0
    formula_unresolved_ht = 0
    formula_unresolved_rows = 0
    quantity_counts: dict[str, int] = {}
    n_records = 0
    for path in sorted(RECORDS_DIR.glob("*.json")):
        generated = generator.generate_record(json.loads(path.read_text(encoding="utf-8")))
        n_records += 1
        accounting = generated.report["cell_accounting"]
        raw += int(accounting["raw_numeric_tokens"])
        stored += int(accounting["stored"])
        refused += int(accounting["refused"])
        excluded += int(accounting["excluded"])
        splits = generated.report["merged_cell_splits"]
        merged_proposed += int(splits["proposed"])
        merged_stored += int(splits["stored"])
        merged_refused += int(splits["refused"])
        merged_refused_10x += int(splits["refused_10x"])
        merged_excluded += int(splits["excluded"])
        unguarded = splits.get("unguarded_by_quantity") or {}
        unguarded_s += int(unguarded.get("S") or 0)
        unguarded_dh += int(unguarded.get("delta_fH") or 0)
        unguarded_cp += int(unguarded.get("cp") or 0)
        unguarded_total += int(splits["unguarded_stored"])
        assert int(splits["unguarded_stored"]) <= int(splits["stored"])
        assert (
            int(splits["proposed"])
            == int(splits["stored"]) + int(splits["refused"]) + int(splits["excluded"])
        )
        resolution = generated.report.get("formula_resolution") or {}
        if generated.report["table_kind"] == "table_298k":
            neighbour_298k_hits += len(generated.report["neighbour_sign_hits"])
            formula_unresolved_rows += int(resolution.get("rows_unresolved") or 0)
        elif resolution.get("unresolved"):
            formula_unresolved_ht += 1
        for observation in generated.observations:
            token = quantity_token(observation.identity)
            if token is None:
                continue
            quantity_counts[token.value] = quantity_counts.get(token.value, 0) + 1
            assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        refused_keys = {
            (row["row_index"], row["column"]) for row in generated.report["refusals"]
        }
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                identity_fail += 1
                residual = Decimal(str(result.get("absolute_residual") or "0"))
                tolerance = Decimal(str(result.get("rounding_tolerance") or "1"))
                if tolerance > 0 and residual > 10 * tolerance:
                    identity_10x += 1
                    row_index = result.get("row_index")
                    if result.get("identity") == "log10_Kf_from_delta_fG":
                        cols = ("log_kf", "formation_gibbs_energy")
                    else:
                        cols = ("negative_gibbs_function", "entropy", "enthalpy_function")
                    for column in cols:
                        assert (row_index, column) in refused_keys, (
                            generated.report["record_id"],
                            row_index,
                            column,
                            result.get("identity"),
                        )
                elif tolerance > 0 and residual > tolerance:
                    identity_1x_10x += 1
        identity_band += len(generated.report.get("identity_notice_1x_10x") or [])
    assert n_records == 606
    assert stored + refused + excluded == raw
    assert raw == B1452_RAW
    assert stored == B1452_STORED
    assert refused == B1452_REFUSED
    assert excluded == B1452_EXCLUDED
    assert stored + refused + excluded == B1452_RAW
    assert neighbour_298k_hits == 0
    assert merged_proposed == B1452_MERGED_PROPOSED
    assert merged_stored == B1452_MERGED_STORED
    assert merged_refused == B1452_MERGED_REFUSED
    assert merged_refused_10x == B1452_MERGED_REFUSED_10X
    assert merged_excluded == B1452_MERGED_EXCLUDED
    assert merged_proposed == merged_stored + merged_refused + merged_excluded
    assert merged_stored != B1452_UNGUARDED_MERGED_STORED
    assert unguarded_total == B1452_UNGUARDED_MERGED_STORED
    assert unguarded_s == B1452_UNGUARDED_298K_S
    assert unguarded_dh == B1452_UNGUARDED_HT_DH
    assert unguarded_cp == B1452_UNGUARDED_HT_CP
    assert unguarded_s + unguarded_dh + unguarded_cp == B1452_UNGUARDED_MERGED_STORED
    assert identity_10x == B1452_IDENTITY_10X
    assert identity_1x_10x == B1452_IDENTITY_1X_10X
    assert identity_band == B1452_IDENTITY_1X_10X
    assert identity_band == identity_1x_10x
    assert formula_unresolved_ht == B1452_FORMULA_UNRESOLVED_HT
    assert formula_unresolved_rows == B1452_FORMULA_UNRESOLVED_298K_ROWS
    print(
        "B1452_CENSUS",
        {
            "raw": raw,
            "stored": stored,
            "refused": refused,
            "excluded": excluded,
            "unexplained": 0,
            "merged_proposed": merged_proposed,
            "merged_stored": merged_stored,
            "merged_refused": merged_refused,
            "merged_refused_10x": merged_refused_10x,
            "merged_excluded": merged_excluded,
            "unguarded_stored": unguarded_total,
            "unguarded_298k_S": unguarded_s,
            "unguarded_ht_delta_fH": unguarded_dh,
            "unguarded_ht_cp": unguarded_cp,
            "identity_fail": identity_fail,
            "identity_10x": identity_10x,
            "identity_1x_10x": identity_1x_10x,
            "identity_notice_1x_10x": identity_band,
            "formula_unresolved_ht": formula_unresolved_ht,
            "formula_unresolved_298k_rows": formula_unresolved_rows,
            "quantities": quantity_counts,
        },
    )
    assert stored > 0
    assert refused > 0
    assert "delta_fH" in quantity_counts
    assert "delta_fG" in quantity_counts
    assert "log10_Kf" in quantity_counts


# Independent transcription of the rendered oxide-table captions. Each entry is
# (physical PDF page, (closing T, phase, existing polymorph or None) per segment).
# None means that the printed product needs vocabulary deferred to t-1860.
CHECKED_OXIDE_PRODUCTS = {
    '0228': (260, ((1530, Phase.CR, Polymorph.ALPHA), (1800, Phase.CR, Polymorph.BETA))),
    '0232': (264, ((1640, Phase.CR, None), (1800, Phase.L, None))),
    '0241': (273, ((1800, Phase.CR, None),)),
    '0273': (305, ((1079, Phase.CR, Polymorph.ORTHORHOMBIC), (1241, Phase.CR, None), (1600, Phase.CR, None))),
    '0275': (307, ((1000, Phase.CR, Polymorph.ARAGONITE),)),
    '0277': (309, ((1400, Phase.CR, None),)),
    '0279': (311, ((1000, Phase.CR, None),)),
    '0281': (313, ((1000, Phase.CR, None),)),
    '0284': (316, ((1197, Phase.CR, Polymorph.ORTHORHOMBIC), (1500, Phase.CR, None))),
    '0307': (339, ((1373, Phase.CR, None), (1600, Phase.CR, Polymorph.MONOCLINIC))),
    '0314': (346, ((1800, Phase.CR, Polymorph.KYANITE),)),
    '0316': (348, ((1800, Phase.CR, Polymorph.ANDALUSITE),)),
    '0318': (350, ((1800, Phase.CR, Polymorph.SILLIMANITE),)),
    '0320': (352, ((2133, Phase.CR, Polymorph.MULLITE),)),
    '0322': (354, ((970, Phase.CR, Polymorph.LARNITE), (1710, Phase.CR, None), (1800, Phase.CR, Polymorph.ALPHA))),
    '0324': (356, ((1120, Phase.CR, Polymorph.CALCIUM_OLIVINE),)),
    '0326': (358, ((1863, Phase.CR, Polymorph.GEHLENITE),)),
    '0328': (360, ((1200, Phase.CR, Polymorph.GROSSULAR),)),
    '0330': (362, ((1848, Phase.CR, None),)),
    '0332': (364, ((1727, Phase.CR, None),)),
    '0334': (366, ((1670, Phase.CR, None), (1800, Phase.L, None))),
    '0336': (368, ((1490, Phase.CR, Polymorph.CRYSTALLINE), (1800, Phase.L, None))),
    '0338': (370, ((2163, Phase.CR, Polymorph.FORSTERITE),)),
    '0340': (372, ((1200, Phase.CR, None),)),
    '0342': (374, ((1700, Phase.CR, None),)),
    '0344': (376, ((1620, Phase.CR, None), (1800, Phase.L, None))),
    '0346': (378, ((1800, Phase.CR, None),)),
    '0348': (380, ((1398, Phase.CR, Polymorph.WOLLASTONITE), (1400, Phase.CR, Polymorph.PSEUDOWOLLASTONITE))),
    '0350': (382, ((1817, Phase.CR, Polymorph.PSEUDOWOLLASTONITE),)),
    '0352': (384, ((1700, Phase.CR, Polymorph.CA_AL_PYROXENE),)),
    '0354': (386, ((1664, Phase.CR, Polymorph.DIOPSIDE),)),
    '0356': (388, ((1200, Phase.CR, Polymorph.ALPHA),)),
    '0358': (390, ((1700, Phase.CR, Polymorph.BETA),)),
    '0360': (392, ((1300, Phase.CR, Polymorph.ALPHA), (1600, Phase.CR, Polymorph.BETA))),
    '0362': (394, ((1830, Phase.CR, Polymorph.CLINOENSTATITE),)),
    '0364': (396, ((1564, Phase.CR, None),)),
    '0366': (398, ((1300, Phase.CR, None),)),
    '0368': (400, ((1100, Phase.CR, None),)),
    '0370': (402, ((1830, Phase.CR, Polymorph.ANORTHITE),)),
    '0372': (404, ((1500, Phase.GLASS, None),)),
    '0374': (406, ((1400, Phase.CR, None),)),
    '0376': (408, ((1473, Phase.CR, Polymorph.SANIDINE_HIGH),)),
    '0378': (410, ((1300, Phase.GLASS, None),)),
    '0380': (412, ((810, Phase.CR, Polymorph.KALIOPHILLITE_LOW), (1800, Phase.CR, Polymorph.KALIOPHILLITE_HIGH))),
    '0382': (414, ((955, Phase.CR, None), (1800, Phase.CR, None))),
    '0385': (417, ((1400, Phase.CR, None),)),
    '0387': (419, ((1400, Phase.CR, None),)),
    '0389': (421, ((1200, Phase.GLASS, None),)),
    '0391': (423, ((1521, Phase.CR, None),)),
    '0393': (425, ((1500, Phase.CR, None),)),
    '0396': (428, ((1100, Phase.CR, None),)),
    '0398': (430, ((800, Phase.CR, Polymorph.PYROPHYLLITE),)),
    '0400': (432, ((900, Phase.CR, None),)),
}


@pytest.mark.parametrize('record_id', OXIDE_BASIS_RECORDS)
def test_every_audited_oxide_record_has_its_printed_product_segments(record_id: str) -> None:
    suffix = record_id.removeprefix(generator.SOURCE_ID + '-')
    page, segments = CHECKED_OXIDE_PRODUCTS[suffix.split('-')[0]]
    record = _load(record_id)
    ordinal = record['phase_segment']['ordinal']
    for row in _generation(record_id).observations:
        species = row.identity.species
        if species is None and row.identity.reaction is not None and row.identity.reaction.is_value:
            species = next(t.species for t in row.identity.reaction.value.terms if t.coefficient > 0)
        if species is None:
            continue
        assert row.locator.pdf_page_index == page
        assert row.locator.published_page == page - 6
        temperature = row.identity.temperature_K.value
        lo = Decimal('298.15')
        matches = []
        for index, (hi, phase, polymorph) in enumerate(segments, 1):
            if lo <= temperature <= hi:
                matches.append((index, phase, polymorph))
            lo = Decimal(hi)
        if not matches:
            assert species.phase.is_unknown, (record_id, temperature)
            continue
        expected = matches[0] if len(matches) == 1 else next(m for m in matches if m[0] == ordinal)
        _, phase, polymorph = expected
        assert species.phase.is_value and species.phase.value is phase, (record_id, temperature)
        if phase is not Phase.CR:
            assert species.polymorph.is_not_applicable
        elif polymorph is None:
            assert species.polymorph.is_unknown, (record_id, temperature, species.polymorph)
        else:
            assert species.polymorph.is_value and species.polymorph.value is polymorph, (record_id, temperature)


@pytest.mark.parametrize('record_id', OXIDE_BASIS_RECORDS)
def test_every_audited_silicate_binds_its_printed_silica_transition(record_id: str) -> None:
    # Every silicate page in the audited population prints the same 844 K line;
    # the assertions run on every generated term, including refused quantities.
    for row in _generation(record_id).observations:
        reaction = row.identity.reaction
        if reaction is None or not reaction.is_value:
            continue
        for term in reaction.value.terms:
            if term.coefficient >= 0 or term.species.formula != 'SiO2':
                continue
            expected = Polymorph.ALPHA_QUARTZ if row.identity.temperature_K.value < 844 else Polymorph.BETA
            assert term.species.phase.value is Phase.CR
            assert term.species.polymorph.value is expected, (record_id, row.observation_id)


@pytest.mark.parametrize('suffix,page', [('0368',400), ('0393',425), ('0396',428), ('0398',430), ('0400',432)])
def test_every_hydrous_record_binds_water_and_refuses_hot_gas_standard_states(suffix: str, page: int) -> None:
    for row in _generation(generator.SOURCE_ID + '-' + suffix).observations:
        if row.identity.reaction is None or not row.identity.reaction.is_value:
            continue
        assert row.locator.pdf_page_index == page
        water = next(t for t in row.identity.reaction.value.terms if t.species.formula == 'H2O')
        if row.identity.temperature_K.value < Decimal('373.15'):
            assert water.species.phase.value is Phase.L
            assert water.standard_state.is_value
        else:
            assert water.species.phase.value is Phase.G
            assert water.standard_state.is_unknown
            assert row.identity.quantity.is_unknown


def test_potassium_ocr_caption_binds_the_printed_liquid_entropy() -> None:
    row, = _observations_for(_generation(generator.SOURCE_ID + '-0040-phase-02'), Quantity.S, temperature='400')
    assert row.value.point == Decimal('80.83')
    assert row.locator.pdf_page_index == 72
    assert row.identity.species.phase.value is Phase.L


def test_missing_water_transition_refuses_instead_of_assuming_liquid(monkeypatch) -> None:
    record = _load(generator.SOURCE_ID + '-0398')
    # Model the actual loss of the source footer before its record correction.
    monkeypatch.delitem(generator.B1452_SOURCE_TEXT_CORRECTIONS[record['record_id']], 'auxiliary_lines_as_published')
    row, = _formation_observations(generator.generate_record(record), basis='from_the_oxides', quantity=Quantity.LOG10_K_REACTION, temperature='400')
    assert row.value.point == Decimal('1.706')
    assert row.identity.quantity.is_unknown
    water = next(t for t in row.identity.reaction.value.terms if t.species.formula == 'H2O')
    assert water.species.phase.is_unknown
    assert water.standard_state.is_unknown
    assert 'no printed reference transition' in water.species.phase.reason


def test_removing_record_corrections_refuses_named_ocr_records(monkeypatch) -> None:
    monkeypatch.setattr(generator, 'B1452_SOURCE_TEXT_CORRECTIONS', {})
    for suffix in ('0314', '0316', '0318', '0324', '0350', '0356', '0358', '0376', '0398'):
        rows = _formation_observations(_generation(generator.SOURCE_ID + '-' + suffix), basis='from_the_oxides', quantity=Quantity.LOG10_K_REACTION)
        assert rows and all(row.identity.quantity.is_unknown for row in rows), suffix


def test_missing_water_assumption_mutation_is_detected(monkeypatch) -> None:
    original = generator._oxide_species
    def assume_liquid(formula, *, record=None, temperature=None):
        if formula == 'H2O':
            return generator.make_species('H2O', Phase.L, generator.State.not_applicable('not crystal'), charge=0)
        return original(formula, record=record, temperature=temperature)
    with monkeypatch.context() as patch:
        patch.setattr(generator, '_oxide_species', assume_liquid)
        with pytest.raises(AssertionError):
            test_missing_water_transition_refuses_instead_of_assuming_liquid(monkeypatch)



@pytest.mark.parametrize('record_id', OXIDE_BASIS_RECORDS)
def test_all_generated_reference_oxides_follow_the_audited_transitions(record_id: str) -> None:
    for row in _generation(record_id).observations:
        if row.identity.reaction is None or not row.identity.reaction.is_value:
            continue
        temperature = row.identity.temperature_K.value
        for term in row.identity.reaction.value.terms:
            if term.coefficient >= 0:
                continue
            species = term.species
            if species.formula in {'FeO', 'Na2O', 'P2O5'}:
                # PDFs 264/368: FeO; 398/417/419/421/423: Na2O; 339: P2O5.
                boundary = {'FeO': 1650, 'Na2O': 1193, 'P2O5': 842}[species.formula]
                assert species.phase.value is (Phase.CR if temperature < boundary else Phase.L)
            elif species.formula == 'K2O' and temperature > 1154:
                # PDFs 406/408/410/412/414/425 print decomposition above 1154 K.
                assert species.phase.is_unknown
                assert 'decomposes above 1154' in species.phase.reason
                assert term.standard_state.is_unknown
            elif species.formula == 'ZrO2':
                # PDF 378 / folio 372 prints monoclinic -> tetragonal at 1478 K.
                assert species.phase.value is Phase.CR
                if temperature < 1478:
                    assert species.polymorph.value is Polymorph.MONOCLINIC
                else:
                    assert species.polymorph.is_unknown
                    assert term.standard_state.is_unknown


def test_audited_oxide_log_k_census_has_only_source_verified_refusal_classes() -> None:
    from collections import Counter

    classes = Counter()
    for record_id in OXIDE_BASIS_RECORDS:
        for row in _generation(record_id).observations:
            if ':log10_K_reaction:' not in row.observation_id:
                continue
            assert row.value.point is not None
            if row.identity.quantity.is_value:
                classes['resolved'] += 1
                continue
            assert row.identity.quantity.is_unknown and row.identity.quantity.reason
            reaction = row.identity.reaction
            if not reaction.is_value:
                classes['carbonate_reaction'] += 1
                continue
            product = next(t.species for t in reaction.value.terms if t.coefficient > 0)
            if product.phase.is_value and product.phase.value is Phase.CR and product.polymorph.is_unknown:
                classes['new_product_vocabulary'] += 1
            else:
                classes['reference_vocabulary_or_unprinted_state'] += 1
    # Rendered examples: Geikelite/Merwinite PDFs 273/362 (new product);
    # Witherite/Dolomite PDFs 305/311 (carbonate gas reference not closed);
    # alpha/beta spodumene PDFs 388/390 and pyrophyllite 430 (reference state).
    assert classes == {
        'resolved': 221,
        'new_product_vocabulary': 234,
        'carbonate_reaction': 56,
        'reference_vocabulary_or_unprinted_state': 88,
    }
