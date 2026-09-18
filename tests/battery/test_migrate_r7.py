"""Source-grounded regression witnesses for migration round 7."""

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    compilation_family_from_store_path,
    iter_observation_store_paths,
    map_quantity,
    migrate,
    select_declared_source,
)
from simulator.battery.records import State
from tests.battery.test_migrate import (
    _copy_compilation_record, _copy_extract, _extract_observation, _write_min_tree,
    test_k01_value_constructions_live_inside_the_boundary as boundary_guard,
)


@pytest.mark.parametrize("body", [
    'return globals()["select_declared_source"](None, None, {})',
    'return globals()["Value"](None)',
    'return globals().get("Value")(None)',
    'constructor: object = Value\n    return constructor(None)',
    'selector: object = select_declared_source\n    return selector(None, None, {})',
    'return select_declared_source(None, None, {})',
    'return Value(None)',
    'constructor = Value\n    return constructor(None)',
    'selector = select_declared_source\n    return selector(None, None, {})',
    'return getattr(module, "Value")(None)',
    'return globals()("Value")(None)',
])
def test_g4_boundary_mutants(monkeypatch, body):
    target = REPO_ROOT / "simulator/battery/migrate.py"
    original = Path.read_text
    source = original(target)
    boundary_guard()
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw:
                        source + "\ndef unlisted_mutant():\n    " + body + "\n"
                        if self == target else original(self, *a, **kw))
    with pytest.raises(AssertionError):
        boundary_guard()


@pytest.mark.parametrize("form,units", [
    ("alpha = A * exp(-B/T)", "dimensionless alpha"),
    ("alpha = A * exp(-B/T)", "bar"),
])
def test_g3_evaluator_must_produce_pressure(form, units):
    state, reason = map_quantity("psat_series", {
        "source_form": form, "coefficients": {"A": 1, "B": 2},
    }, units=units)
    assert state.is_unknown, reason


@pytest.mark.parametrize("units", ["1", None])
def test_h3_pressure_evaluator_arguments_do_not_name_the_output(units):
    state, reason = map_quantity("psat_series", {
        "source_form": "alpha(P_bar, T_K) = A * exp(-B/T)",
        "coefficients": {"A": 1, "B": 2},
    }, units=units)
    assert state.is_unknown, reason
    control, _reason = map_quantity("psat_series", {
        "source_form": "log10(P_bar) = A - B/T",
        "coefficients": {"A": 1, "B": 2},
    }, units=units)
    assert control.is_value and control.value is Quantity.P_SAT


def test_g3_declared_psat_cannot_contradict_partial_pressure():
    state, reason = map_quantity("psat_series", {
        "quantity": "pure_Psat", "P_bar": 1,
        "note": "partial vapor pressure over silicate melt; not pure-component saturation pressure",
    }, units="bar")
    assert state.is_unknown, reason


@pytest.mark.parametrize("field,raw", [("table_kJ_mol", None), ("delta_fG", "not transcribed")])
def test_g1_present_non_numeric_fields_are_not_absent(field, raw):
    result = select_declared_source(Quantity.DELTA_FG, "kJ/mol", {field: raw})
    assert result.value.kind is ValueKind.UNAVAILABLE
    assert field in result.reason
    assert "does not name" not in result.reason
    assert ("null" if raw is None else "not numeric") in result.reason


def test_g1_unmapped_series_does_not_deny_numbers():
    result = select_declared_source(State.unknown("not mapped"), None,
                                    {"series": [{"T_K": 1000, "pressure_bar": .01}]})
    assert result.value.kind is ValueKind.UNAVAILABLE
    assert "quantity is unknown" in result.reason
    assert "T_K" in result.reason and "pressure_bar" in result.reason
    assert "no numeric" not in result.reason


def test_g2_plante_source_points_and_comparison_fence(tmp_path):
    root = _write_min_tree(tmp_path)
    _copy_extract(root, "kems-042-plante-1979.yaml")
    result = migrate(root, write=False)
    measured = [o for o in result.observations.values()
                if o.source_id == "kems-042-plante-1979" and quantity_token(o.identity) is Quantity.P_PARTIAL]
    assert len(measured) == 221
    assert all(o.value.kind is ValueKind.POINT for o in measured)
    assert sum(bool(o.notices) for o in measured) == 59
    for oid, temperature, pressure in [
        ("plante1979_table2_s1104_r001_quoted", "1302", "6.91e-7"),
        ("plante1979_table2_s1123_r030_quoted", "1356", "2.51e-7"),
        ("plante1979_table2_s1214_r001_quoted", "1315", "1.29e-7"),
    ]:
        source = _extract_observation("kems-042-plante-1979.yaml", oid)
        assert Decimal(str(source["values"]["P_K_atm_as_published"])) == Decimal(pressure)
        assert Decimal(str(source["values"]["T_K_as_published"])) == Decimal(temperature)
        obs = result.observations[f"kems-042-plante-1979::{oid}"]
        assert quantity_token(obs.identity) is Quantity.P_PARTIAL
        assert obs.value.kind is ValueKind.POINT
        assert obs.value.point == Decimal(pressure) * Decimal("101325")
        assert obs.identity.temperature_K.value == Decimal(temperature)
        if "s1104" not in oid:
            if "s1123" in oid:
                assert source["values"]["composition_K2O_wt_percent_as_published"] == 21.14
            assert any(source["values"]["reason"] in n.reason for n in obs.notices)
            assert obs.admission.status.value != "admitted"


def test_g1_jacobson_keeps_category_and_quantity_reason(tmp_path):
    root = _write_min_tree(tmp_path)
    _copy_extract(root, "kems-139-jacobson-2024.yaml")
    result = migrate(root, write=False)
    oid = "kems-139-jacobson-2024::jacobson_2024_t1_factsage_2500k_predicted_psat"
    obs = result.observations[oid]
    assert obs.identity.quantity.is_unknown
    assert "predicted_partial_pressure" in obs.identity.quantity.reason
    assert obs.value.kind is ValueKind.CATEGORICAL
    assert obs.value.categorical == "bound_not_point_ordering"


def test_g1_compilation_columns_grid_and_multi_phase(tmp_path):
    root = _write_min_tree(tmp_path)
    witnesses = [
        ("pankratz-1984-usbm-b677", "table-0001", "T"),
        ("kelley-1960-usbm-b584", "table-001", "heat_content"),
        ("kelley-king-1961-usbm-b592", "table-006-1261", "cp_10_k"),
        ("pankratz-1987-usbm-b689", "page-0005", "cp"),
        ("robie-hemingway-1995-usgs-b2131", "high-temperature-p253", "formation_gibbs"),
        ("robie-hemingway-1995-usgs-b2131", "atomic-weight-001", "atomic_weight"),
        ("hemingway-haas-robinson-1982-usgs-b1544", "usgs-b1544-table-1", None),
    ]
    for family, record, _column in witnesses:
        source = next((REPO_ROOT / "data/literature/compilations" / family).rglob(record + ".json"))
        dest = root / source.relative_to(REPO_ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
    result = migrate(root, write=False)
    for family, record, column in witnesses:
        if record == "usgs-b1544-table-1":
            prefix = f"{family}:{record}:"
            table1 = [
                obs
                for oid, obs in result.observations.items()
                if oid.startswith(prefix)
            ]
            assert table1
            assert f"{family}:{record}" not in result.observations
            assert all(obs.value.kind is ValueKind.POINT for obs in table1)
            assert all(
                obs.evidence.class_.is_value
                and obs.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
                for obs in table1
            )
            formulas = {obs.identity.species.formula for obs in table1}
            assert "Al2O3" in formulas and "SiO2" in formulas
            continue
        obs = result.observations[f"{family}:{record}"]
        assert obs.value.kind is ValueKind.UNAVAILABLE
        reason = obs.value.unavailable_reason
        assert "no numeric coordinate/value pairs" not in reason
        if column:
            assert column in reason
            assert "numeric" in reason
        if record == "atomic-weight-001":
            assert "scalar-only atomic_weight: 1 numeric cells and no coordinate" in reason
            assert obs.identity.quantity.is_unknown
        else:
            assert "temperature grid" in obs.identity.temperature_K.reason
        if record == "high-temperature-p253":
            assert quantity_token(obs.identity) is Quantity.DELTA_FG
            assert "awaiting the per-source compilation generator" in reason


@pytest.mark.parametrize(
    "units, expected, mapped",
    [
        ({"formation_gibbs": "kJ/mol"}, "kJ/mol", True),
        (["K ... kJ/mol", "second printed line"], "K ... kJ/mol", False),
        ("K ... kJ/mol", "K ... kJ/mol", False),
        ([], None, False),
    ],
)
def test_h1_compilation_units_preserve_every_printed_shape(
    tmp_path, units, expected, mapped
):
    root = _write_min_tree(tmp_path)
    path = _copy_compilation_record(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "high-temperature-p253.json",
    )
    record = json.loads(path.read_text())
    record["units_as_published"] = units
    path.write_text(json.dumps(record), encoding="utf-8")
    result = migrate(root, write=False)
    obs = result.observations[
        "robie-hemingway-1995-usgs-b2131:high-temperature-p253"
    ]
    reason = obs.value.unavailable_reason or ""
    if expected is None:
        assert "unit as printed not stated" in reason
        return
    assert expected in reason
    assert "unit as printed not stated" not in reason
    assert ("not mapped to column formation_gibbs" in reason) is not mapped


def test_h1_b2131_printed_unit_line_is_not_denied(tmp_path):
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "high-temperature-p253.json",
    )
    result = migrate(root, write=False)
    obs = result.observations[
        "robie-hemingway-1995-usgs-b2131:high-temperature-p253"
    ]
    reason = obs.value.unavailable_reason or ""
    assert "J•mol-1·K-1" in reason
    assert "unit as printed not stated" not in reason
    assert "not mapped to column formation_gibbs" in reason


@pytest.mark.parametrize(
    "filename, expected_count",
    [("janaf-4th.yaml", 21), ("nist-webbook.yaml", 43)],
)
def test_h2_transition_rows_explain_their_multiple_phases(
    tmp_path, filename, expected_count
):
    root = _write_min_tree(tmp_path)
    _copy_extract(root, filename)
    result = migrate(root, write=False)
    transitions = [
        obs
        for obs in result.observations.values()
        if obs.source_id == filename.removesuffix(".yaml")
        and quantity_token(obs.identity) is Quantity.TRANSITION_TEMPERATURE
    ]
    assert len(transitions) == expected_count
    for obs in transitions:
        phase = obs.identity.species.phase
        assert phase.is_unknown
        assert "transition" in (phase.reason or "")
        assert "property_kind" in (phase.reason or "")
        assert "one phase axis" in (phase.reason or "")
        assert "source does not state phase" not in (phase.reason or "")

    cr = result.observations.get("janaf-4th::Cr_melting_point")
    if cr is not None:
        reason = cr.identity.species.phase.reason or ""
        assert "crystal" in reason and "liquid" in reason
        assert "phase_from/phase_to" in reason
    mgcl2 = result.observations.get("janaf-4th::MgCl2_melting_point")
    if mgcl2 is not None:
        reason = mgcl2.identity.species.phase.reason or ""
        assert "solid -> liquid" in reason
        assert "CRYSTAL LIQUID 950.000 K" in reason
        assert "citation.primary" in reason


def test_g1_unmapped_composition_and_nested_admission_are_not_absent(tmp_path):
    root = _write_min_tree(tmp_path)
    for name in ("kems-105-yamada-1983.yaml", "schaefer-and-fegley-2007-icarus-outgassing-of-oc.yaml"):
        _copy_extract(root, name)
    result = migrate(root, write=False)
    yamada = result.observations["kems-105-yamada-1983::yamada_1983_epsilon_P_i_this_work_1600C"]
    assert yamada.identity.composition.is_unknown
    assert yamada.identity.composition.reason == "no composition mapped from source"
    schaefer = result.observations[
        "schaefer-and-fegley-2007-icarus-outgassing-of-oc::schaefer_2007_table2_h_solid_solutions"
    ]
    assert schaefer.admission.status.value == "pending"
    assert schaefer.admission.reason == "no observation admission_status mapped from source"


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _leaves(child, f"{prefix}[{i}]")
    else:
        yield prefix, value


def _nonempty_printed_paths(value, prefix=""):
    return [
        path
        for path, item in _leaves(value, prefix)
        if isinstance(item, str) and item.strip()
    ]


def _store_kind(path: Path) -> str | None:
    parts = path.parts
    if "extracts-v2" in parts:
        return "extracts-v2"
    if "observations-v2" in parts:
        return "observations-v2"
    return None


STORE_PATHS = sorted(
    path
    for directory in ("observations-v2", "extracts-v2")
    for path in iter_observation_store_paths(REPO_ROOT / "data/literature" / directory)
)


def absence_audit(paths=STORE_PATHS):
    """Independent source lookup; never calls migration's inference/census helpers."""
    judged, bad = Counter(), []
    for directory in ("observations-v2", "extracts-v2"):
        for path in (p for p in paths if _store_kind(p) == directory):
            store = yaml.load(path.read_text(), Loader=yaml.CSafeLoader)
            legacy = {}
            if directory == "extracts-v2":
                source_path = REPO_ROOT / "data/literature/extracts" / path.name
                source_doc = yaml.load(source_path.read_text(), Loader=yaml.CSafeLoader)
                for body in (source_doc.get("species") or {}).values():
                    for row in body.get("observations") or []:
                        legacy[str(row["observation_id"])] = row
            for obs in store.get("observations") or []:
                oid = obs["observation_id"]
                if directory == "observations-v2":
                    source = (obs.get("locator") or {}).get("source_path", "")
                    if not source.startswith("data/literature/compilations/"):
                        continue
                    raw = (REPO_ROOT / source).read_text()
                    record = json.loads(raw) if source.endswith(".json") else yaml.load(raw, Loader=yaml.CSafeLoader)
                else:
                    local = oid.split("::", 1)[1].split("::point:")[0]
                    assert local in legacy, oid
                    record = legacy[local]
                family = obs["source_id"]
                judged[family] += 1
                leaves = list(_leaves(record))
                numeric = [p for p, v in leaves if isinstance(v, (int, float)) and not isinstance(v, bool)
                           and not re.search(r"index|source_line|page|schema|year", p)]
                for reason_path, reason in _leaves(obs):
                    if not reason_path.endswith(("reason", "unavailable_reason")) or not isinstance(reason, str):
                        continue
                    contradiction = None
                    if "no numeric coordinate/value pairs" in reason and len(numeric) >= 2:
                        contradiction = numeric[:3]
                    elif "does not state temperature_K" in reason:
                        temperatures = [p for p, v in leaves if v not in (None, "") and
                                        re.search(r"temperature|(?:^|[.])T(?:_K|_C|$)|cp_\d+_k", p, re.I)]
                        if temperatures:
                            contradiction = temperatures[:3]
                    elif "does not state phase" in reason:
                        phases = [p for p, v in leaves if v not in (None, "") and
                                  re.search(r"(?:^|[.])(?:phase|phase_as_published|phase_state_as_published|state_note_as_published|phase_from|phase_to)(?:$|\[)", p)]
                        if phases:
                            contradiction = phases[:3]
                        elif record.get("type") == "transition_point":
                            transition_fields = [
                                p for p, value in leaves
                                if value not in (None, "")
                                and (
                                    p == "values.property_kind"
                                    or p.startswith("values.citation.")
                                    or p == "locator.note"
                                )
                            ]
                            if transition_fields:
                                contradiction = transition_fields[:3]
                    elif "unit as printed not stated" in reason:
                        units = _nonempty_printed_paths(
                            record.get("units_as_published"),
                            "units_as_published",
                        )
                        if units:
                            contradiction = units[:3]
                    elif match := re.search(r"does not name a (\w+) field", reason):
                        aliases = {
                            "delta_fG": ("delta_fG", "delta_f_G", "formation_gibbs", "table_kJ_mol", "Delta_f_G_298_kJ_mol"),
                            "p_partial": ("P_K_atm_as_published", "p_Ga_Pa", "p_In_Pa", "partial_pressure_pa"),
                            "log10_Kf": ("log_kf", "log_Kf", "table_log10_Kf", "log10_Kf"),
                        }.get(match[1], (match[1],))
                        found = [key for key in aliases if any(
                            re.search(r"(?:^|\.)" + re.escape(key) + r"(?:\.|\[|$)", p)
                            for p, _value in leaves
                        )]
                        if found:
                            contradiction = found
                    elif match := re.search(r"source does not state (?:a numeric )?(\w+)", reason):
                        found = [p for p, value in leaves if value not in (None, "") and
                                 re.search(r"(?:^|\.)" + re.escape(match[1]) + r"(?:\.|\[|$)", p)]
                        if found:
                            contradiction = found[:3]
                    if contradiction:
                        bad.append((family, oid, reason_path, reason, contradiction))
    return judged, bad


@pytest.mark.parametrize("path", STORE_PATHS, ids=lambda p: p.name)
def test_g1_whole_store_absence_claims_match_sources(path):
    judged, bad = absence_audit([path])
    if _store_kind(path) == "extracts-v2" or compilation_family_from_store_path(path):
        assert judged, "no source families judged"
    assert not bad, f"{len(bad)} false absence claims; first witnesses: {bad[:12]}"


_COUNT_LEAF_KEYS = ("value", "as_published", "raw")
_COUNT_SKIP_KEYS = {
    "ambiguities",
    "flags",
    "footnote_markers",
    "heading_token",
    "identity_checks",
    "marks",
    "ocr_reasons",
}
_EXPECTED_COUNT_CLAIMS = {
    "compilations-kelley-1960-usbm-b584.yaml": 2679,
    "compilations-kelley-king-1961-usbm-b592.yaml": 16231,
    "compilations-pankratz-1984-usbm-b677.yaml": 9235,
    "compilations-pankratz-1987-usbm-b689.yaml": 3362,
    "compilations-robie-hemingway-1995-usgs-b2131.yaml": 4083,
    "compilations-robie-hemingway-fisher-1978-usgs-b1452.yaml": 503,
    "compilations-robie-waldbaum-1968-usgs-b1259.yaml": 512,
}
_COMPILATION_STORE_PATHS = iter_observation_store_paths(
    REPO_ROOT / "data/literature/observations-v2", "compilations-*.yaml"
)


def _is_printed_number(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        Decimal(str(value).strip())
    except Exception:
        return False
    return True


def _source_numeric_column_counts(record):
    counts = {kind: Counter() for kind in ("present", "value", "raw")}
    columns = record.get("columns") if isinstance(record.get("columns"), list) else []
    headings = []
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            headings.append(str(index))
            continue
        heading = column.get("heading_as_published") or column.get("heading_raw")
        headings.append(str(heading) if heading not in (None, "") else str(index))

    def add(name, leaf):
        if not name:
            return
        if isinstance(leaf, dict):
            value = leaf.get("value")
            raw = leaf.get("raw", leaf.get("as_published"))
        else:
            value = raw = leaf
        counts["present"][name] += 1
        if _is_printed_number(value):
            counts["value"][name] += 1
        if _is_printed_number(raw):
            counts["raw"][name] += 1

    def walk(node, path):
        if isinstance(node, dict) and any(key in node for key in _COUNT_LEAF_KEYS):
            add(path, node)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key in _COUNT_SKIP_KEYS:
                    continue
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"column[{index}]")
        else:
            add(path, node)

    rows = record.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            cells = row.get("cells")
            if isinstance(cells, dict):
                for name, cell in cells.items():
                    walk(cell, str(name))
            elif isinstance(cells, list):
                for index, cell in enumerate(cells):
                    name = headings[index] if index < len(headings) else f"column[{index}]"
                    walk(cell, name)
            else:
                for key, child in row.items():
                    if key in {
                        "index",
                        "locator",
                        "raw",
                        "raster_reading",
                        "source_line",
                        "source_row_index",
                    } or key in _COUNT_SKIP_KEYS:
                        continue
                    walk(child, str(key))
    for key, child in record.items():
        if key not in {"rows", "columns"} and isinstance(child, dict) and any(
            leaf in child for leaf in _COUNT_LEAF_KEYS
        ):
            add(str(key), child)
    return counts


def _numeric_count_claims(reason, known_columns):
    claims, parse_errors = [], []
    for match in re.finditer(r"source column census \((.*?)\);", reason):
        body = match.group(1)
        spans = []
        for name in sorted(known_columns, key=len, reverse=True):
            pattern = re.compile(re.escape(name) + r": (\d+) numeric cells")
            for found in pattern.finditer(body):
                span = found.span()
                if any(span[0] < end and start < span[1] for start, end in spans):
                    continue
                spans.append(span)
                claims.append((name, int(found.group(1))))
        if len(spans) != len(re.findall(r"\d+ numeric cells", body)):
            parse_errors.append(body)
    for match in re.finditer(
        r"source prints (\d+) numeric formation Gibbs cells "
        r"\(column (.*?)(?:, unit as printed|\))",
        reason,
    ):
        claims.append((match.group(2), int(match.group(1))))
    for match in re.finditer(
        r"source prints scalar-only (.*?): (\d+) numeric cells", reason
    ):
        claims.append((match.group(1), int(match.group(2))))
    return claims, parse_errors


def _count_claim_audit(paths):
    judged, bad = Counter(), []
    cache = {}
    for path in paths:
        store = yaml.load(path.read_text(), Loader=yaml.CSafeLoader) or {}
        for obs in store.get("observations") or []:
            source = (obs.get("locator") or {}).get("source_path", "")
            if not source.startswith("data/literature/compilations/"):
                continue
            if source not in cache:
                raw = (REPO_ROOT / source).read_text()
                cache[source] = (
                    json.loads(raw)
                    if source.endswith(".json")
                    else yaml.load(raw, Loader=yaml.CSafeLoader)
                )
            record = cache[source]
            counts = _source_numeric_column_counts(record)
            known = set(counts["present"])
            for reason_path, reason in _leaves(obs):
                if not reason_path.endswith(("reason", "unavailable_reason")) or not isinstance(reason, str):
                    continue
                claims, parse_errors = _numeric_count_claims(reason, known)
                for body in parse_errors:
                    bad.append((obs["observation_id"], "unparsed", body))
                for column, stated in claims:
                    judged[path.name] += 1
                    if stated not in {
                        counts["value"][column],
                        counts["raw"][column],
                    }:
                        bad.append(
                            (
                                obs["observation_id"],
                                column,
                                stated,
                                counts["value"][column],
                                counts["raw"][column],
                            )
                        )
    return judged, bad


@pytest.mark.parametrize("path", _COMPILATION_STORE_PATHS, ids=lambda path: path.name)
def test_h5_whole_store_numeric_cell_claims_match_sources(path):
    judged, bad = _count_claim_audit([path])
    assert judged[path.name] == _EXPECTED_COUNT_CLAIMS.get(path.name, 0)
    assert not bad, f"false numeric-cell claims: {bad[:12]}"


def test_h5_numeric_cell_claim_inventory_and_mutation():
    assert sum(_EXPECTED_COUNT_CLAIMS.values()) == 36605
    path = (
        REPO_ROOT
        / "data/literature/observations-v2/compilations-pankratz-1984-usbm-b677.yaml"
    )
    store = yaml.load(path.read_text(), Loader=yaml.CSafeLoader)
    obs = next(
        item
        for item in store["observations"]
        if item["observation_id"].endswith("table-0004")
    )
    source = json.loads((REPO_ROOT / obs["locator"]["source_path"]).read_text())
    counts = _source_numeric_column_counts(source)
    reason = obs["value"]["unavailable_reason"]
    claims, errors = _numeric_count_claims(reason, set(counts["present"]))
    assert not errors
    assert {name for name, _count in claims} == {"T, K:", "$CO/CO_{2}$ :"}
    mutated = reason.replace("T, K:: 3 numeric cells", "T, K:: 4 numeric cells")
    claims, errors = _numeric_count_claims(mutated, set(counts["present"]))
    bad = [
        (name, stated)
        for name, stated in claims
        if stated not in {counts["value"][name], counts["raw"][name]}
    ]
    with pytest.raises(AssertionError, match="false numeric-cell claims"):
        assert not errors and not bad, f"false numeric-cell claims: {bad}"
