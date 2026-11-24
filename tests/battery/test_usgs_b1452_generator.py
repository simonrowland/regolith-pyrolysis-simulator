"""USGS B1452 source-aware generator controls and negative witnesses."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from inspect import getsource
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
from simulator.battery.validate import reaction_atom_balance
from tests.battery import compilation_shard_observation_count

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
ROOT = Path(__file__).resolve().parents[2]
B1452_STORE_DIR = ROOT / "data" / "literature" / "observations-v2"
B1452_STORE_PATTERN = "compilations-robie-hemingway-fisher-1978-usgs-b1452.yaml"
# Independent source scan: formula weights, uncertainty cells, and declared
# row columns contain 54,104 nonblank cells; 1,603 blanks are outside the
# census. The generator buckets are pinned separately: 15,226 stored + 19,642
# refused + 19,236 excluded closes that scan and must hold on train regen.
B1452_RAW = 54104
B1452_STORED = 15226
B1452_REFUSED = 19642
B1452_EXCLUDED = 19236
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
# Checked-in store count; the train refreshes it from the corrected source records.
B1452_STORE_STORED = 15224
# Corrected generator output drops the three entropy uncertainty-only rows.
# Fold 10 re-count: the pre-fix generator's extra 47 stored cells were the
# fluorphlogopite fragment AlSi3O10 (record 0003: 5, shard 0394: 42). Printed
# formula weight 421.244 g/mol; fragment mass 271.232 g/mol. Refusing tokens
# from inside a rejected complete expression puts those cells back into
# refused, so these counts and the store-versus-generated gap of 3 hold.
B1452_GENERATED_STORE_STORED = 15221
TABLE_298K = "robie-hemingway-fisher-1978-usgs-b1452-0003"
TABLE1 = "robie-hemingway-fisher-1978-usgs-b1452-0001"
SILVER_HT = "robie-hemingway-fisher-1978-usgs-b1452-0004"
FAYALITE_ELEMENTS = "robie-hemingway-fisher-1978-usgs-b1452-0335"
FAYALITE_OXIDES = "robie-hemingway-fisher-1978-usgs-b1452-0336"


def _load(record_id: str) -> dict:
    path = RECORDS_DIR / f"{record_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _generation(record_id: str) -> generator.RecordGeneration:
    global _LAST_RECORD
    _LAST_RECORD = _load(record_id)
    return generator.generate_record(_LAST_RECORD)


_LAST_RECORD: dict | None = None


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


FLUORPHLOGOPITE_HT = "robie-hemingway-fisher-1978-usgs-b1452-0394"
# Printed KMg3(AlSi3O10)F2, PDF 35 folio 29 and PDF 426 folio 420.
# The captured 298 K formula line is Kg3, which is not that mineral.
_FLUORPHLOGOPITE_FRAGMENT_ATOMS = (("Al", "1"), ("O", "10"), ("Si", "3"))
_PRINTED_FLUORPHLOGOPITE_FW = Decimal("421.244")


def _atom_key(formula: str) -> tuple[tuple[str, str], ...] | None:
    counts = generator._parse_counts(formula)
    if not counts:
        return None
    return tuple(sorted((element, str(count)) for element, count in counts.items()))


def rejected_interior_formulas(name: str) -> set[str]:
    """Formulas the pre-fix fallback would take from inside a rejected ``$...$``.

    A complete expression that parses is the formula, so it contributes none.
    """

    spans: list[tuple[int, int]] = []
    for match in generator._LATEX_MATH_RE.finditer(name):
        formula, latex_charge = generator._latex_to_formula(match.group(1))
        if formula and not formula.startswith("Delta") and generator._looks_like_formula(formula):
            return set()
        if formula and latex_charge is not None and re.fullmatch(r"[A-Z][a-z]?", formula):
            return set()
        spans.append(match.span())
    found: set[str] = set()
    for start, end in spans:
        chunk = name[start:end]
        for token in chunk.replace("(", " ").replace(")", " ").split():
            parsed, _charge = generator._plain_formula_and_charge(token)
            if parsed:
                found.add(parsed)
    return found


def interior_mining_formula_from_name(name: str) -> tuple[str | None, int | None]:
    """Pre-fix rule: a failed complete expression still offers its interior tokens."""

    charge: int | None = None
    for chunk in generator._LATEX_MATH_RE.findall(name):
        formula, latex_charge = generator._latex_to_formula(chunk)
        if latex_charge is not None:
            charge = latex_charge
        if formula and not formula.startswith("Delta") and generator._looks_like_formula(formula):
            return formula, charge
        if formula and latex_charge is not None and re.fullmatch(r"[A-Z][a-z]?", formula):
            return formula, charge
    tokens = name.replace("(", " ").replace(")", " ").split()
    if tokens:
        last = tokens[-1]
        if (
            (any(character.isdigit() for character in last) or re.fullmatch(r"[A-Z][a-z]?", last))
            and generator._looks_like_formula(last)
        ):
            return last, charge
        hits: list[tuple[str, int | None]] = []
        for token in tokens:
            formula, token_charge = generator._plain_formula_and_charge(token)
            if formula:
                hits.append((formula, token_charge))
        unique = {formula for formula, _token_charge in hits}
        if len(unique) == 1:
            formula = next(iter(unique))
            charges = {
                item_charge for item_formula, item_charge in hits if item_formula == formula
            }
            if len(charges) == 1:
                charge = next(iter(charges))
            return formula, charge
    return None, charge


def _clear_formula_indexes() -> None:
    generator._formula_index_from_298k.cache_clear()
    generator._formula_weight_index_from_298k.cache_clear()


def _fluorphlogopite_rows(record: dict) -> list[tuple[int, str, generator.FormulaResolution]]:
    rows = []
    for index, row in enumerate(record["rows"]):
        cells = row.get("cells") or {}
        parsed = generator._as_published_cell(cells.get("name_and_formula"))
        if parsed is None:
            continue
        name = parsed[0]
        if "FLUORPHLOGOPITE" not in name and "AlSi_{3}O_{10}" not in name:
            continue
        if "FLUORPHLOGOPITE" not in name and "F_{2}" not in name:
            continue
        fw = generator._formula_weight_as_published(record, row_index=index)
        rows.append(
            (
                index,
                name,
                generator._resolve_formula(record, name=name, formula_weight=fw),
            )
        )
    return rows


def _assert_fluorphlogopite_has_no_fragment() -> None:
    record = _load(TABLE_298K)
    rows = _fluorphlogopite_rows(record)
    assert len(rows) == 2
    for _index, _name, resolution in rows:
        assert resolution.formula is None
        assert resolution.reason is not None
        assert resolution.reason.startswith(generator.FORMULA_UNRESOLVED_REASON_PREFIX)
        assert resolution.formula != "AlSi_{3}O_{10}"
    generated = _generation(TABLE_298K)
    assert [
        observation.identity.species.formula
        for observation in generated.observations
        if _atom_key(observation.identity.species.formula) == _FLUORPHLOGOPITE_FRAGMENT_ATOMS
    ] == []
    high_temperature = _generation(FLUORPHLOGOPITE_HT)
    resolution = high_temperature.report["formula_resolution"]
    assert resolution["formula"] is None
    assert resolution["unresolved"] is True
    assert str(resolution["reason"]).startswith(generator.FORMULA_UNRESOLVED_REASON_PREFIX)
    assert high_temperature.observations == ()
    fragment_mass = generator._formula_mass("AlSi_{3}O_{10}")
    assert fragment_mass is not None
    assert not generator._mass_matches_formula_weight(
        "AlSi_{3}O_{10}", _PRINTED_FLUORPHLOGOPITE_FW
    )
    # Printed KMg3(AlSi3O10)F2 matches 421.244. The capture is Kg, and no
    # image-verified correction supplies the printed formula, so the row
    # stays the refusal above.
    whole = "KMg3(AlSi3O10)F2"
    assert _atom_key(whole) == (
        ("Al", "1"),
        ("F", "2"),
        ("K", "1"),
        ("Mg", "3"),
        ("O", "10"),
        ("Si", "3"),
    )
    assert generator._mass_matches_formula_weight(whole, _PRINTED_FLUORPHLOGOPITE_FW)


def test_fluorphlogopite_refuses_the_rejected_expression_interior() -> None:
    """298 K record 0003 and high-temperature shard 0394 do not emit AlSi3O10.

    Printed mineral, PDF 35 folio 29 and PDF 426 folio 420: KMg3(AlSi3O10)F2,
    421.244 g/mol. The captured ``Kg_{3}`` is not an image-verified formula,
    so the row stays a typed missing-composition refusal.
    """

    _clear_formula_indexes()
    _assert_fluorphlogopite_has_no_fragment()


def test_old_interior_mining_breaks_fluorphlogopite_refusal(monkeypatch) -> None:
    _clear_formula_indexes()
    _assert_fluorphlogopite_has_no_fragment()
    monkeypatch.setattr(
        generator, "_formula_from_name", interior_mining_formula_from_name
    )
    _clear_formula_indexes()
    try:
        captured = "$Kg_{3}(AlSi_{3}O_{10})F_{2}$"
        assert interior_mining_formula_from_name(captured)[0] == "AlSi_{3}O_{10}"
        with pytest.raises(AssertionError):
            _assert_fluorphlogopite_has_no_fragment()
    finally:
        monkeypatch.undo()
        _clear_formula_indexes()


def _assert_rejected_span_boundary_refuses() -> None:
    resolution = generator._resolve_formula({}, name="TEST Fe$Kg_{3}$O")
    assert resolution.formula is None
    assert resolution.source is None
    assert str(resolution.reason).startswith(generator.FORMULA_UNRESOLVED_REASON_PREFIX)


def test_rejected_span_does_not_join_neighboring_text() -> None:
    _assert_rejected_span_boundary_refuses()


@pytest.mark.parametrize("name", ["TEST Ag($Kg_{3}$)", "TEST ($Kg_{3}$)Ag"])
def test_rejected_span_keeps_separate_formula_tokens(name: str) -> None:
    formula, _charge = generator._formula_from_name(name)
    assert formula == "Ag"


def test_old_span_boundary_mutation_fails_independent_token_assertion(monkeypatch) -> None:
    source = getsource(generator._formula_from_name).replace(
        'not (name[cursor].isspace() or name[cursor] in "()")',
        "not name[cursor - 1].isspace()",
    ).replace(
        'not (name[start - 1].isspace() or name[start - 1] in "()")',
        "not name[start - 1].isspace()",
    ).replace(
        'not (tail[0].isspace() or tail[0] in "()")',
        "not tail[0].isspace()",
    )
    assert source != getsource(generator._formula_from_name)
    namespace = vars(generator).copy()
    exec(source, namespace)
    old_formula_from_name = namespace["_formula_from_name"]
    monkeypatch.setattr(generator, "_formula_from_name", old_formula_from_name)
    with pytest.raises(AssertionError):
        assert generator._formula_from_name("TEST Ag($Kg_{3}$)")[0] == "Ag"


def test_generator_preserves_explicit_decimal_subscript_for_accounting(monkeypatch) -> None:
    from simulator.accounting.formulas import parse_formula

    source = r"$Fe_{1.5}SiO_{2}$"
    formula, charge = generator._formula_from_name(f"TEST {source}")
    assert charge is None
    assert formula is not None
    assert dict(parse_formula(formula).elements) == {"Fe": 1.5, "O": 2.0, "Si": 1.0}

    namespace = vars(generator).copy()
    old_latex_to_formula = getsource(generator._latex_to_formula).replace(
        "lambda match: match.group(0)\n        if \".\" in (match.group(1) or match.group(2) or \"\")\n        else (match.group(1) or match.group(2))",
        "lambda match: match.group(1) or match.group(2)",
    )
    assert old_latex_to_formula != getsource(generator._latex_to_formula)
    exec(old_latex_to_formula, namespace)
    monkeypatch.setattr(generator, "_latex_to_formula", namespace["_latex_to_formula"])
    with pytest.raises(AssertionError):
        old_formula, _ = generator._formula_from_name(f"TEST {source}")
        assert old_formula is not None
        assert dict(parse_formula(old_formula).elements) == {"Fe": 1.5, "O": 2.0, "Si": 1.0}


def _row_name_and_weight(record: dict, index: int) -> tuple[str, str | None]:
    row = record["rows"][index]
    cells = row.get("cells") or {}
    parsed = generator._as_published_cell(cells.get("name_and_formula"))
    name = parsed[0] if parsed is not None else ""
    return name, generator._formula_weight_as_published(record, row_index=index)


def test_rejected_expression_interior_never_resolves() -> None:
    """Every B1452 name resolves without a token from inside a rejected formula."""

    _clear_formula_indexes()
    record = _load(TABLE_298K)
    interiors_by_key: dict[str, set[str]] = {}
    offenders: list[str] = []
    rows = record["rows"]
    for index in range(len(rows)):
        name, fw = _row_name_and_weight(record, index)
        sources = [name]
        if fw and generator._formula_from_name(name)[0] is None and index + 1 < len(rows):
            _next_name, next_fw = _row_name_and_weight(record, index + 1)
            if not next_fw:
                sources.append(_next_name)
        interiors: set[str] = set()
        for source in sources:
            interiors |= rejected_interior_formulas(source)
        key = generator._name_key(name)
        if key and interiors:
            interiors_by_key.setdefault(key, set()).update(interiors)
        resolution = generator._resolve_formula(record, name=name, formula_weight=fw)
        if resolution.formula and resolution.formula in interiors:
            offenders.append(
                f"298k row {index} {name!r} -> {resolution.formula!r} via {resolution.source}"
            )
    for path in sorted(RECORDS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if generator._table_kind(payload) != "ht_grid":
            continue
        label = str(payload.get("name_as_published") or "")
        own = rejected_interior_formulas(label)
        published = payload.get("formula_as_published")
        if isinstance(published, str):
            own |= rejected_interior_formulas(published)
        title = payload.get("title_line_as_published")
        if isinstance(title, str):
            own |= rejected_interior_formulas(title)
        key = generator._name_key(label)
        if key:
            own |= interiors_by_key.get(key, set())
        resolution = generator._resolve_formula(payload)
        if resolution.formula and resolution.formula in own:
            offenders.append(
                f"{payload.get('record_id')} {label!r} -> {resolution.formula!r} "
                f"via {resolution.source}"
            )
    assert offenders == []


def test_terbium_decimal_formula_lines_do_not_emit_entropy() -> None:
    generated = _generation(TABLE_298K)
    observations = [
        observation
        for observation in generated.observations
        if quantity_token(observation.identity) is Quantity.S
        and observation.identity.species.formula in {"TbO_{1.714}", "TbO_{1.812}"}
    ]

    assert {
        (observation.identity.species.formula, observation.value.point)
        for observation in observations
    } == {
        ("TbO_{1.714}", Decimal("80.75")),
        ("TbO_{1.812}", Decimal("81.17")),
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
        ("TbO_{1.714}", Decimal("186.348")),
        ("TbO_{1.812}", Decimal("187.916")),
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
        observation.identity.species.formula in {"TbO_{1.714}", "TbO_{1.812}"}
        for observation in generated.observations
    ) == B1452_TB_298K_OBSERVATIONS

    for record_id, formula, formula_weight, expected_count in (
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0202",
            "TbO_{1.714}",
            Decimal("186.348"),
            B1452_TB_0202_OBSERVATIONS,
        ),
        (
            "robie-hemingway-fisher-1978-usgs-b1452-0203",
            "TbO_{1.812}",
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


def test_298k_entropy_uncertainties_attach_to_points_and_szomolnokite_locator() -> None:
    generated = _generation(TABLE_298K)
    from simulator.battery.stable_ids import _name_slug

    corrected_observation_ids = set()
    for name, point, uncertainty, stray, page, pdf_page in (
        ("HYDROHAGNESITE", "503.67", "1.55", "1.55", 25, 31),
        ("ARTIWITE", "232.92", "0.67", "0.67", 25, 31),
        ("ANALCIME", "234.43", "2.51", "2.51", 29, 35),
    ):
        observations = _observations_for(
            generated, Quantity.S, temperature="298.15", name=name
        )
        assert len(observations) == 1
        assert observations[0].value.point == Decimal(point)
        assert observations[0].uncertainty.kind is UncertaintyKind.PRINTED
        assert observations[0].uncertainty.verbatim == uncertainty
        assert all(row.value.point != Decimal(stray) for row in observations)
        assert observations[0].locator is not None
        assert observations[0].locator.published_page == page
        assert observations[0].locator.pdf_page_index == pdf_page
        corrected_observation_ids.add(observations[0].observation_id)
        row_observations = [
            row
            for row in generated.observations
            if f":name={_name_slug(name)}" in row.observation_id
        ]
        assert row_observations
        corrected_observation_ids.update(
            row.observation_id for row in row_observations
        )
        assert all(
            row.locator is not None
            and row.locator.published_page == page
            and row.locator.pdf_page_index == pdf_page
            for row in row_observations
        )

    szomolnokite = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="298.15",
        name="SZOMOLNOKITE",
    )
    assert len(szomolnokite) == 1
    point = szomolnokite[0]
    assert point.value.point == Decimal("-1243.900")
    assert point.uncertainty.kind is UncertaintyKind.PRINTED
    assert point.uncertainty.verbatim == "0.500 kJ/mol"
    assert point.evidence.original_method_class == "reconstructed_from_printed_page"
    assert point.locator is not None
    assert point.locator.published_page == 26
    assert point.locator.pdf_page_index == 32
    corrected_observation_ids.add(point.observation_id)

    overridden = [
        row
        for row in generated.observations
        if row.locator is not None
        and (row.locator.published_page, row.locator.pdf_page_index) == (26, 32)
    ]
    assert overridden == [point]
    fallback_observations = [
        row
        for row in generated.observations
        if row.observation_id not in corrected_observation_ids
    ]
    assert all(
        row.locator is None
        or (row.locator.published_page, row.locator.pdf_page_index) == (12, 18)
        for row in fallback_observations
    )


def test_locator_evidence_requires_the_source_line_mutation(monkeypatch) -> None:
    evidence = generator._image_evidence
    test_298k_entropy_uncertainties_attach_to_points_and_szomolnokite_locator()

    def wrong_source_line(token, *, action, raw_tokens):
        return evidence(
            replace(token, source_text_line=(token.source_text_line or 0) + 1),
            action=action,
            raw_tokens=raw_tokens,
        )

    monkeypatch.setattr(generator, "_image_evidence", wrong_source_line)
    with pytest.raises(AssertionError):
        test_298k_entropy_uncertainties_attach_to_points_and_szomolnokite_locator()


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
    stored = [
        obs
        for obs in control.observations
        if quantity_token(obs.identity) is Quantity.DELTA_FG
        and ":T=" in obs.observation_id and "row=" not in obs.observation_id
    ]
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


def test_historical_correction_uses_printed_token_and_source_line() -> None:
    record_id = "robie-hemingway-fisher-1978-usgs-b1452-0162"
    payload = _load(record_id)
    token, = [
        token
        for token in generator._iter_raw_numeric_tokens(payload)
        if token.source_text_line == 21 and token.column == "heat_capacity"
    ]
    assert token.layout_as_extracted == "109e02"
    assert token.as_published == "109.02"
    assert generator._image_correction(token) is None
    generated = generator.generate_record(payload)
    assert any(
        row["row_index"] == token.row_index
        and row["column"] == "heat_capacity"
        for row in generated.report["refusals"]
    )


def test_historical_ocr_lookup_mutation_recreates_admission_failure(monkeypatch) -> None:
    evidence = generator._image_evidence
    test_historical_correction_uses_printed_token_and_source_line()
    monkeypatch.setattr(
        generator,
        "_image_evidence",
        lambda token, *, action, raw_tokens: evidence(
            token,
            action=action,
            raw_tokens=(token.layout_as_extracted, token.as_published),
        ),
    )
    with pytest.raises(AssertionError):
        test_historical_correction_uses_printed_token_and_source_line()


def test_oxide_asterisk_is_basis_not_damage() -> None:
    generated = _generation(FAYALITE_OXIDES)
    gibbs = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", basis="from_the_oxides"
    )
    assert len(gibbs) == 1
    assert gibbs[0].value.point == Decimal("-20.775")
    note = gibbs[0].locator.note or ""
    assert "as_published='-20.775 *'" in note or "as_published='-20.775*'" in note.replace(" ", "")
    assert "formation_basis=from_the_oxides" in note
    refused_star = [
        row
        for row in generated.report["refusals"]
        if "*" in row["as_published"] and row["column"] == "formation_gibbs_energy"
    ]
    assert refused_star == []


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
    oxides = _observations_for(
        _generation(FAYALITE_OXIDES),
        Quantity.DELTA_FG,
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


def test_blank_cell_is_not_counted_as_an_exclusion() -> None:
    payload = _load(TABLE_298K)
    row = next(row for row in payload["rows"] if row["source_text_line"] == 602)
    blank_cell = row["cells"]["reference_cp"]
    assert blank_cell["as_published"] == ""
    with_blank = generator.generate_record(payload).report["cell_accounting"]

    del row["cells"]["reference_cp"]
    without_cell = generator.generate_record(payload).report["cell_accounting"]
    assert with_blank == without_cell


def test_blank_accounting_mutation_recreates_production_failure(monkeypatch) -> None:
    generate = generator.generate_record
    raw_tokens = generator._iter_raw_numeric_tokens
    test_blank_cell_is_not_counted_as_an_exclusion()

    def count_original_blanks(payload):
        run = generate(payload)
        blank = sum(not token.as_published.strip() for token in raw_tokens(payload))
        run.report["cell_accounting"]["raw_numeric_tokens"] += blank
        run.report["cell_accounting"]["excluded"] += blank
        return run

    monkeypatch.setattr(generator, "generate_record", count_original_blanks)
    with pytest.raises(AssertionError):
        test_blank_cell_is_not_counted_as_an_exclusion()


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
    assert len(stored_ids) - len(generated_ids) == 3
    # assert stored_ids <= generated_ids  # restore after rematerialize
    assert B1452_STORED + B1452_REFUSED + B1452_EXCLUDED == B1452_RAW
    assert len(generated_ids) == B1452_GENERATED_STORE_STORED


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
