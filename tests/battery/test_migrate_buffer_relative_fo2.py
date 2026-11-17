"""Buffer-relative fO2 conversion uses the shared oxygen-buffer owner."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from decimal import Decimal
from itertools import product
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import NoticeKind, ValueKind
from simulator.battery.migrate import (
    PrintedOxygenFacts,
    _PrintedOxygenRefusalCode,
    _delta_iw_ideal_ratio_notice,
    _log_fO2_to_pa,
    _classify_buffer_phrase,
    _buffer_request_from_row,
    _printed_fo2_interval,
    _point_condition_from_plain,
    _printed_relative_oxygen,
    _relative_buffer_for_field,
    _scoped_oxygen_decision,
    printed_oxygen_decision,
)
from simulator.battery.records import Located, Value
from simulator.state_types import State
from simulator.oxygen_buffers import (
    frost_buffer_log10_fO2_bar,
)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.timeout(300)

# Buffer-label grammar (the Cartesian product below is the contract):
# family := IW | iron-wustite[ buffer] | QFM | FMQ | NNO | Mo-MoO2 |
#           MH | M-H | magnetite-hematite | MW | CO-CO2 (case insensitive;
#           spaces/hyphens and the printed umlaut in wüstite are aliases).
# marker := delta | dlog | log | d | Δ | ∆ | relative to | -relative
# label  := marker family | family marker | glued(marker, family)
# number := a finite decimal, including signed/unsigned zero; it follows
#           '=' or a sign, is a distance above/below family, or occupies a
#           separate numeric field. Citation years are NOT offsets.
# Decoration (units, parenthetical prose, punctuation, whitespace, case)
# cannot remove a marker or turn an absent number into a printed zero.
# Field-name validation remains strict; prose and units use marker detection.
#
# | Number on row | Relative marker on row | Outcome                    |
# | nonzero / 0   | either                 | that numeric offset        |
# | absent       | yes                    | typed missing-offset refusal|
# | absent       | no; bare family        | at buffer, 0 by statement  |
# A bare sibling family never supplies a missing relative offset. A numeric
# sibling (including 0) does. Inconsistent families/numbers still refuse.
_GRAMMAR_FAMILIES = (
    "IW", "iron-wustite", "iron wüstite buffer", "FMQ", "QFM", "NNO",
    "Mo-MoO2", "MH", "M-H", "magnetite-hematite", "magnetite hematite",
    "MW", "CO-CO2",
)
_GRAMMAR_MARKERS = ("delta", "dlog", "log", "d", "Δ", "∆", "relative to", "relative")
_GRAMMAR_PLACEMENTS = ("prefix", "suffix", "relation", "buffer-label", "separate-field", "glued-field")
_GRAMMAR_DECORATIONS = ("plain", "units", "citation", "punctuation", "whitespace", "case")
_GRAMMAR_OFFSETS = ("+1.25", "0", None)


def _grammar_row(family, marker, placement, decoration, offset, bare_sibling):
    label = f"{family}-{marker}" if placement == "suffix" else f"{marker}-{family}"
    if placement == "glued-field":
        label = f"{marker}{family}"
    if placement in {"prefix", "suffix", "relation", "buffer-label"} and offset is not None:
        label += f" = {offset}"
    if decoration == "units":
        label += " (log units)"
    elif decoration == "citation":
        label += " (Frost 1991)"
    elif decoration == "punctuation":
        label += ", log units"
    elif decoration == "whitespace":
        label = " \t" + label.replace(" ", " \t ") + "\t "
    elif decoration == "case":
        label = label.swapcase()
    raw = {}
    if bare_sibling:
        raw["fO2_control" if placement == "buffer-label" else "oxygen_buffer_as_published"] = family
    if placement == "separate-field":
        raw["log_fO2"] = {"units": label}
        if offset is not None:
            raw["log_fO2"]["value"] = offset
    elif placement == "glued-field":
        raw[f"{marker}{family}"] = offset
        raw["fO2_control"] = label
    elif placement == "relation":
        raw["oxygen_fugacity_buffer_relation"] = label
    elif placement == "buffer-label":
        raw["oxygen_buffer_as_published"] = label
    else:
        raw["fO2_control"] = label
    return raw


@pytest.mark.parametrize(
    ("family", "marker", "placement", "decoration", "offset", "bare_sibling"),
    product(_GRAMMAR_FAMILIES, _GRAMMAR_MARKERS, _GRAMMAR_PLACEMENTS,
            _GRAMMAR_DECORATIONS, _GRAMMAR_OFFSETS, (False, True)),
)
def test_buffer_label_grammar_matrix(family, marker, placement, decoration, offset, bare_sibling):
    raw = _grammar_row(family, marker, placement, decoration, offset, bare_sibling)
    actual = _decision(raw)
    assert actual is not None, raw
    if offset is None:
        assert actual.state.is_unknown, (raw, actual)
        assert actual.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
        assert "not a point offset" in actual.state.reason, (raw, actual)
    else:
        # Preserve calibration provenance and existing uncalibrated-family
        # refusals while comparing syntax against its canonical spelling.
        citation = " (Frost 1991)" if decoration == "citation" else ""
        if placement == "glued-field":
            expected_raw = {f"delta_{family}": offset, "fO2_control": f"delta-{family}{citation}"}
            parsed = _printed_relative_oxygen(raw, None, None)
            assert parsed is not None and parsed[1] == Decimal(offset)
        else:
            expected_raw = {"fO2_control": f"{family} = {offset}{citation}"}
        if bare_sibling:
            expected_raw["oxygen_buffer_as_published"] = family
        expected = _decision(expected_raw)
        assert expected is not None
        if expected.state.is_unknown and "refused:" in expected.state.reason:
            # An unavailable cited calibration keeps the printed label in
            # its reason. Compare the family/offset refusal, not that spelling.
            assert actual.state.is_unknown
            assert actual.refusal_code is expected.refusal_code
            assert actual.state.reason.split("refused:")[0] == expected.state.reason.split("refused:")[0]
        else:
            assert actual.state == expected.state, (raw, actual, expected)
        if actual.buffer_request is not None:
            assert actual.buffer_request.offset == Decimal(offset)
            assert actual.buffer_request.offset_as_printed is not None


@pytest.mark.parametrize("family", _GRAMMAR_FAMILIES)
@pytest.mark.parametrize("field", ("fO2_control", "oxygen_buffer_as_published"))
def test_bare_family_is_zero_by_statement(family, field):
    classified, offset, printed, prose = _classify_buffer_phrase(family)
    assert classified and offset == Decimal(0) and printed is None and not prose
    actual = _decision({field: family})
    explicit = _decision({"fO2_control": f"{family} = 0"})
    assert actual is not None and explicit is not None
    assert actual.state == explicit.state
    if actual.buffer_request is not None:
        assert "at buffer by statement" in actual.buffer_request.offset_as_printed


@pytest.mark.parametrize("family", _GRAMMAR_FAMILIES)
@pytest.mark.parametrize("frame", ("{}", "log({})", "log10({})"))
def test_log_field_buffer_units_cannot_supply_an_absent_offset(family, frame):
    raw = {"log_fO2": {"units": frame.format(family)}, "oxygen_buffer_as_published": family}
    decision = _decision(raw)
    assert decision is not None and decision.state.is_unknown
    assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    assert "not a point offset" in decision.state.reason


# Literal inputs from all four review rounds (including the exploratory zero
# spellings, now explicitly part of the grammar). The generated matrix above
# complements these probes rather than replacing their integration coverage.
@pytest.mark.parametrize(
    ("raw", "expected_raw"),
    [
        ({"fO2_control": "delta-IW"}, None),
        ({"fO2_control": "delta-IW", "oxygen_buffer_as_published": "IW"}, None),
        ({"fO2_control": "delta-FMQ", "oxygen_buffer_as_published": "FMQ"}, None),
        *[
            ({"oxygen_buffer_as_published": family, **relative}, None)
            for family in ("IW", "FMQ")
            for relative in (
                {"fO2_control": f"delta-{family} (Frost 1991)"},
                {"log_fO2": {"units": f"log(delta-{family})"}},
            )
        ],
        *[
            ({"fO2_control": label, "oxygen_buffer_as_published": "IW"}, None)
            for label in ("delta-IW (log units)", "ΔIW, log units", "IW-relative")
        ],
        ({"DFMQ": "+1.85", "oxygen_buffer_as_published": "FMQ"}, {"delta_FMQ": "+1.85"}),
        ({"delta_FMQ": "+1.85", "oxygen_buffer_as_published": "FMQ"}, {"delta_FMQ": "+1.85"}),
        *[
            ({field: {key: "+1.85"}, **sibling}, {field: "+1.85"})
            for field, family in (("DIW", "IW"), ("DFMQ", "FMQ"), ("DNNO", "NNO"))
            for key in ("value", "delta", "delta_as_published")
            for sibling in ({}, {"oxygen_buffer_as_published": family})
        ],
        ({"DFMQ": "+1.85", "delta_FMQ": "+0.5"}, None),
        *[
            ({"fO2_control": "delta-IW", "oxygen_buffer_as_published": zero}, {"delta_IW": "0"})
            for zero in ("IW = 0", "IW = +0", "IW = 0.0", "0 log units above IW",
                         "0 log units below IW", "0 above IW", "0.0 log units above IW")
        ],
        ({"fO2_control": "ΔIW = 0"}, {"delta_IW": "0"}),
        ({"delta_IW": 0}, {"delta_IW": "0"}),
    ],
)
def test_verbatim_review_probes(raw, expected_raw):
    actual = _decision(raw)
    assert actual is not None
    if expected_raw is None:
        assert actual.state.is_unknown, (raw, actual)
        assert actual.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    else:
        expected = _decision(expected_raw)
        assert expected is not None and expected.state.is_value
        assert actual.state == expected.state, (raw, actual, expected)


@pytest.mark.parametrize("zero", (0, "0", "+0", "-0", "0.0", "-0.00", "0e0"))
@pytest.mark.parametrize("key", ("value", "delta", "delta_as_published"))
def test_structured_numeric_zero_beside_missing_relative_label(zero, key):
    actual = _decision({"fO2_control": "delta-IW (log units)", "delta_IW": {key: zero}})
    expected = _decision({"delta_IW": "0"})
    assert actual is not None and expected is not None and expected.state.is_value
    assert actual.state == expected.state


@pytest.mark.parametrize("mutation", ("marker-detection", "numeric-zero"))
def test_grammar_regressions_detect_reverted_changes(monkeypatch, mutation):
    from simulator.battery import migrate as migration

    source = inspect.getsource(migration._buffer_request_from_row)
    if mutation == "marker-detection":
        source = source.replace("_has_relative_buffer_marker(", "_relative_buffer_for_field(")
        cases = product(_GRAMMAR_FAMILIES, _GRAMMAR_MARKERS, ("prefix", "suffix"),
                        ("units", "citation", "punctuation"), (None,), (True,))
    else:
        source = source.replace('bare_name=printed is None', 'bare_name=printed == "0"')
        # The relative sibling is essential: it distinguishes a printed zero
        # from the implicit bare-name zero that the old code conflated with it.
    assert source != inspect.getsource(migration._buffer_request_from_row)
    namespace = dict(vars(migration))
    exec(compile(source, "<reverted buffer request>", "exec"), namespace)
    if mutation == "numeric-zero":
        raws = [
            {"fO2_control": "delta-IW", "oxygen_buffer_as_published": zero}
            for zero in ("ΔIW = 0", "IW = 0", "0 log units above IW",
                         "0 log units below IW", "0 above IW")
        ]
        expected = _decision({"delta_IW": "0"})
        assert expected is not None and expected.state.is_value
        for raw in raws:
            actual = _decision(raw)
            assert actual is not None and actual.state == expected.state
        monkeypatch.setattr(migration, "_buffer_request_from_row", namespace["_buffer_request_from_row"])
        for raw in raws:
            with pytest.raises(AssertionError):
                actual = _decision(raw)
                assert actual is not None and actual.state == expected.state
    else:
        cases = list(cases)
        for case in cases:
            test_buffer_label_grammar_matrix(*case)
        monkeypatch.setattr(migration, "_buffer_request_from_row", namespace["_buffer_request_from_row"])
        failures = 0
        for case in cases:
            try:
                test_buffer_label_grammar_matrix(*case)
            except AssertionError:
                failures += 1
        assert failures == len(cases)


def test_review_air_fraction_mutation_is_caught(monkeypatch):
    from simulator.battery import migrate as migration

    monkeypatch.setattr(migration, "_AIR_O2_MOLE_FRACTION", Decimal("0.1"))
    with pytest.raises(AssertionError):
        test_real_air_condition_is_retained_as_degenerate_interval()


def _decision(raw: dict, *, temperature_K: int | Decimal = 1000, **kwargs):
    return printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item=raw,
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal(temperature_K),
        **kwargs,
    )


def _source_mapping_with_key(source_id: str, key: str) -> Mapping:
    source = yaml.safe_load(
        (ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if key in value:
                return value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    raise AssertionError(f"{source_id} has no source mapping with {key!r}")


def test_real_air_condition_is_retained_as_degenerate_interval() -> None:
    source = yaml.safe_load(
        (
            ROOT
            / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
        ).read_text(encoding="utf-8")
    )
    pending = [source]
    air_text = None
    while pending:
        value = pending.pop()
        if isinstance(value, str) and "equilibrated in air" in value.casefold():
            air_text = value
            break
        if isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert air_text is not None
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"atmosphere_as_published": air_text},
        point_conditions=None,
        scope="row",
        total_pressure_pa=Decimal("100000"),
    )

    assert decision is not None and decision.kind == "interval"
    condition = decision.interval_condition
    assert condition is not None and condition.state.is_value
    value = condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.INTERVAL
    assert value.interval_low == value.interval_high
    assert abs(value.interval_low - Decimal("-0.6788989008021774")) < Decimal("1e-15")
    assert "pO2=0.20946*P_total" in condition.inference.relation
    assert "degenerate_air_interval" in decision.interval_notice_reason
    assert decision.state.is_unknown


@pytest.mark.parametrize(
    ("pressure_pa", "reason_part"),
    (
        (None, "missing input"),
        (Decimal("0"), "invalid total pressure"),
        (Decimal("-1"), "invalid total pressure"),
        (Decimal("NaN"), "invalid total pressure"),
    ),
)
def test_ambient_air_without_valid_total_pressure_stays_typed_unknown(
    pressure_pa: Decimal | None, reason_part: str
) -> None:
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"atmosphere_as_published": "equilibrated in ambient air"},
        point_conditions=None,
        scope="row",
        total_pressure_pa=pressure_pa,
    )

    assert decision is not None and decision.kind == "interval"
    assert decision.interval_condition is not None
    assert decision.interval_condition.state.is_unknown
    assert reason_part in decision.interval_condition.state.reason
    assert decision.state.is_unknown
    assert reason_part in decision.state.reason


def test_numeric_buffer_distances_are_exact_point_offsets() -> None:
    for field, offset, phrase, buffer in (
        ("delta_IW", "-2.0", "2 log units below IW", "IW; Frost (1991)"),
        ("delta_IW", "-2.0", "2 below IW", "IW; Frost (1991)"),
        ("delta_NNO", "+5.0", "5 log units above NNO", "NNO; Frost (1991)"),
    ):
        expected = _decision(
            {field: offset, "oxygen_buffer_as_published": buffer}
        )
        decision = _decision(
            {
                "fO2_control": phrase,
                "oxygen_buffer_as_published": buffer,
            }
        )

        assert expected is not None and expected.state.is_value
        assert decision is not None and decision.kind == "buffer"
        assert decision.state.is_value
        assert decision.state.value == expected.state.value


def test_dfmq_row_refusal_must_block_parent_fallback() -> None:
    control = "DFMQ = +1.85"
    decision, inherited = _scoped_oxygen_decision(
        facts=PrintedOxygenFacts(log_fO2=Located(State.of(Decimal("-9")))),
        from_fallback=True,
        raw_item={"fO2_control": control},
        row_point_conditions={"fO2_control": Located(State.of(control))},
        run_temperature_K=Decimal("1588.15"),
        source_id="jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation",
    )
    expected = _decision(
        {"fO2_control": "FMQ = +1.85"},
        temperature_K=Decimal("1588.15"),
        source_id="jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation",
    )

    assert decision is not None and decision.kind == "buffer"
    assert decision.state.is_value
    assert expected is not None and expected.state.is_value
    assert decision.state.value == expected.state.value
    assert decision.state.value != Decimal("0.0001")
    assert not inherited


def test_unresolved_prefixed_buffer_control_keeps_typed_row_refusal() -> None:
    control = "DFMQ = +not-a-number"
    decision, inherited = _scoped_oxygen_decision(
        facts=PrintedOxygenFacts(log_fO2=Located(State.of(Decimal("-9")))),
        from_fallback=True,
        raw_item={"fO2_control": control},
        row_point_conditions={"fO2_control": Located(State.of(control))},
        run_temperature_K=Decimal("1588.15"),
    )

    assert decision is not None and decision.kind == "refusal"
    assert decision.state.is_unknown
    assert "buffer-relative fO2" in decision.state.reason
    assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    assert not inherited


def test_co_co2_missing_ratio_has_typed_refusal() -> None:
    decision = _decision({"fO2_control": "CO-CO2."})

    assert decision is not None and decision.kind == "refusal"
    assert decision.state.is_unknown
    assert decision.state.reason == (
        "CO-CO2. buffer-relative fO2 requires the printed CO/CO2 ratio; "
        "no fO2_Pa assigned"
    )
    assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE


@pytest.mark.parametrize(
    ("printed_buffer", "family"),
    (
        ("IW", "IW"),
        ("FMQ", "QFM"),
        ("NNO", "NNO"),
        ("Mo-MoO2", "Mo-MoO2"),
        ("M-H", "MH"),
        ("MW", "MW"),
        ("CO-CO2", "CO-CO2"),
    ),
)
def test_glued_relative_markers_use_the_canonicalizer_for_every_buffer_family(
    printed_buffer: str, family: str
) -> None:
    canonical = _decision({"fO2_control": f"{printed_buffer} = +1.25"})
    assert canonical is not None

    markers = (
        ("D", True),
        ("d", True),
        ("Δ", True),
        ("∆", True),
        ("delta", True),
        ("delta-", True),
        ("log", True),
        ("dlog", True),
        ("D", False),
        ("d", False),
        ("Δ", False),
        ("∆", False),
        ("delta", False),
        ("log", False),
        ("dlog", False),
    )
    for marker, prefix in markers:
        phrase = (
            f"{marker}{printed_buffer} = +1.25"
            if prefix
            else f"{printed_buffer}{marker} = +1.25"
        )
        parsed_family, offset, _printed, is_prose = _classify_buffer_phrase(phrase)
        decision = _decision({"fO2_control": phrase})

        assert (parsed_family, offset, is_prose) == (
            family,
            Decimal("1.25"),
            False,
        )
        assert decision is not None
        assert decision.state == canonical.state


@pytest.mark.parametrize(
    ("printed_buffer", "family"),
    (
        ("IW", "IW"),
        ("FMQ", "QFM"),
        ("NNO", "NNO"),
        ("Mo-MoO2", "Mo-MoO2"),
        ("MH", "MH"),
        ("MW", "MW"),
        ("CO-CO2", "CO-CO2"),
    ),
)
@pytest.mark.parametrize("marker", ("delta", "dlog", "log", "d", "Δ", "∆"))
@pytest.mark.parametrize("prefix", (True, False))
def test_relative_buffer_label_without_offset_refuses_for_every_marker_and_family(
    printed_buffer: str, family: str, marker: str, prefix: bool
) -> None:
    label = f"{marker}-{printed_buffer}" if prefix else f"{printed_buffer}-{marker}"
    parsed_family, offset, printed, is_prose = _classify_buffer_phrase(label)

    assert (parsed_family, offset, printed, is_prose) == (
        family,
        None,
        None,
        True,
    )
    decision = _decision({"fO2_control": label})

    assert decision is not None and decision.kind == "refusal"
    assert decision.state.is_unknown
    assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    assert "not a point offset" in decision.state.reason
    assert "missing input; no fO2_Pa assigned" in decision.state.reason


@pytest.mark.parametrize(
    ("relative_label", "relative_field", "buffer"),
    (
        ("delta-IW", "delta_IW", "IW"),
        ("delta-FMQ", "delta_FMQ", "FMQ"),
        ("delta-NNO", "delta_NNO", "NNO"),
        ("delta-Mo-MoO2", "delta_Mo-MoO2", "Mo-MoO2"),
        ("delta-MH", "delta_MH", "M-H"),
        ("delta-MW", "delta_MW", "MW"),
        ("delta-CO-CO2", "delta_CO-CO2", "CO-CO2"),
    ),
)
def test_missing_relative_offset_refuses_beside_a_bare_sibling_buffer(
    relative_label: str, relative_field: str, buffer: str
) -> None:
    for relative_value in (
        {"fO2_control": relative_label},
        {"oxygen_fugacity_buffer_relation": relative_label},
        {"log_fO2": {"units": relative_label}},
        {relative_field: None},
    ):
        decision = _decision(
            {**relative_value, "oxygen_buffer_as_published": buffer}
        )

        assert decision is not None and decision.kind == "refusal"
        assert decision.state.is_unknown
        assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
        assert "not a point offset" in decision.state.reason
        assert "no fO2_Pa assigned" in decision.state.reason


def test_reviewed_delta_iw_probe_does_not_become_zero_offset() -> None:
    decision = _decision(
        {"fO2_control": "delta-IW", "oxygen_buffer_as_published": "IW"}
    )

    assert decision is not None and decision.kind == "refusal"
    assert decision.state.is_unknown
    assert decision.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    assert "not a point offset" in decision.state.reason


@pytest.mark.parametrize("field", ("fO2_control", "oxygen_buffer_as_published"))
@pytest.mark.parametrize("buffer", ("IW", "FMQ"))
def test_bare_iw_and_fmq_names_mean_zero_offset(
    field: str, buffer: str
) -> None:
    actual = _decision({field: buffer})
    explicit = _decision({field: f"{buffer}+0"})

    assert actual is not None and explicit is not None
    assert actual.state.is_value
    assert actual.buffer_request is not None
    assert actual.buffer_request.offset == Decimal(0)
    assert actual.state == explicit.state


@pytest.mark.parametrize(
    ("field_name", "family"),
    (
        ("DFMQ", "QFM"),
        ("dFMQ", "QFM"),
        ("ΔFMQ", "QFM"),
        ("∆FMQ", "QFM"),
        ("delta-FMQ", "QFM"),
        ("logFMQ", "QFM"),
        ("dlogFMQ", "QFM"),
        ("FMQD", "QFM"),
        ("FMQd", "QFM"),
        ("FMQΔ", "QFM"),
        ("FMQ∆", "QFM"),
        ("FMQdelta", "QFM"),
        ("FMQlog", "QFM"),
        ("FMQdlog", "QFM"),
        ("fO2_delta_FMQ", "QFM"),
        ("log_fO2_as_delta_IW", "IW"),
    ),
)
def test_glued_delta_field_names_use_the_buffer_canonicalizer(
    field_name: str, family: str
) -> None:
    assert _relative_buffer_for_field(field_name) == family


@pytest.mark.parametrize(
    ("glued_field", "canonical_field", "buffer"),
    (
        ("DIW", "delta_IW", "IW"),
        ("DFMQ", "delta_FMQ", "FMQ"),
        ("DNNO", "delta_NNO", "NNO"),
        ("dMo-MoO2", "delta_Mo-MoO2", "Mo-MoO2"),
        ("dMH", "delta_MH", "MH"),
        ("dMW", "delta_MW", "MW"),
        ("dCO-CO2", "delta_CO-CO2", "CO-CO2"),
    ),
)
def test_glued_delta_fields_match_canonical_fields_beside_bare_buffer_names(
    glued_field: str, canonical_field: str, buffer: str
) -> None:
    glued = {glued_field: "+1.85", "oxygen_buffer_as_published": buffer}
    canonical = {
        canonical_field: "+1.85",
        "oxygen_buffer_as_published": buffer,
    }
    actual = _decision(glued)
    expected = _decision(canonical)
    actual_request = _buffer_request_from_row(glued, None, Decimal("1000"))
    expected_request = _buffer_request_from_row(canonical, None, Decimal("1000"))

    assert actual is not None and expected is not None
    assert actual.state == expected.state
    assert (actual_request is None) == (expected_request is None)
    if actual_request is not None and expected_request is not None:
        assert actual_request.offset == expected_request.offset == Decimal("1.85")


@pytest.mark.parametrize(
    ("field_name", "family", "buffer"),
    (("DIW", "IW", "IW"), ("DFMQ", "QFM", "FMQ"), ("DNNO", "NNO", "NNO")),
)
@pytest.mark.parametrize(
    ("payload", "printed"),
    (
        ({"value": "+1.85"}, "+1.85"),
        ({"delta": "+1.85"}, "+1.85"),
        ({"delta_as_published": "+1.85"}, "+1.85"),
    ),
)
@pytest.mark.parametrize("include_bare_buffer", (False, True))
def test_structured_relative_offsets_use_the_shared_value_decoder(
    field_name: str,
    family: str,
    buffer: str,
    payload: dict[str, str],
    printed: str,
    include_bare_buffer: bool,
) -> None:
    raw = {field_name: payload}
    if include_bare_buffer:
        raw["oxygen_buffer_as_published"] = buffer

    request = _buffer_request_from_row(raw, None, Decimal("1000"))
    expected = _decision({field_name: "+1.85"})
    actual = _decision(raw)

    assert request is not None
    assert request.buffer == family
    assert request.offset == Decimal("1.85")
    assert request.offset_as_printed == printed
    assert expected is not None and expected.state.is_value
    assert actual is not None and actual.state == expected.state


def test_structured_relative_offset_preserves_reference_and_conflict() -> None:
    request = _buffer_request_from_row(
        {
            "DFMQ": {
                "delta_as_published": "+1.85",
                "reference_buffer": "FMQ",
            }
        },
        None,
        Decimal("1000"),
    )
    conflict = _buffer_request_from_row(
        {
            "DFMQ": {
                "delta_as_published": "+1.85",
                "reference_buffer": "IW",
            }
        },
        None,
        Decimal("1000"),
    )

    assert request is not None
    assert request.buffer == "QFM"
    assert request.offset == Decimal("1.85")
    assert request.buffer_as_printed == "FMQ"
    assert conflict is not None
    assert "conflicting offsets" in conflict.reason


@pytest.mark.parametrize(
    ("distance_buffer", "signed_buffer", "family", "calibration"),
    (
        ("IW", "iw", "IW", None),
        ("FMQ", "QFM", "QFM", None),
        ("NNO", "nno", "NNO", None),
        ("Mo-MoO2", "mo-moo2", "Mo-MoO2", None),
        ("M-H", "M-H", "MH", None),
        ("MH", "M-H", "MH", None),
        ("M-H", "magnetite-hematite", "MH", "MH; Frost (1991)"),
        ("magnetite-hematite", "MH", "MH", None),
        ("MW", "mw", "MW", None),
    ),
)
def test_equivalent_buffer_spellings_do_not_conflict(
    distance_buffer: str, signed_buffer: str, family: str, calibration: str | None
) -> None:
    classified_family, offset, _printed, is_prose = _classify_buffer_phrase(
        f"2 log units above {distance_buffer}"
    )
    expected_raw = {"oxygen_buffer_as_published": f"{signed_buffer}+2"}
    actual_raw = {
        "fO2_control": f"2 log units above {distance_buffer}",
        "oxygen_buffer_as_published": f"{signed_buffer}+2",
    }
    if calibration is not None:
        expected_raw["buffer_calibration"] = calibration
        actual_raw["buffer_calibration"] = calibration
    expected = _decision(expected_raw)
    actual = _decision(actual_raw)

    assert expected is not None and expected.state.is_value
    assert actual is not None and actual.state.is_value
    assert actual.state.value == expected.state.value
    assert (classified_family, offset, is_prose) == (
        family,
        Decimal("2"),
        False,
    )


@pytest.mark.parametrize(
    ("relation", "calibration", "operator", "threshold"),
    (
        ("below IW", "IW; Frost (1991)", "<", Decimal("0")),
        ("below IW+1", "IW; Frost (1991)", "<", Decimal("1")),
        ("above NNO-2", "NNO; Frost (1991)", ">", Decimal("-2")),
    ),
)
@pytest.mark.parametrize(
    ("temperature_K", "pressure_pa"),
    (
        (None, None),
        (Decimal("1000"), None),
        (None, Decimal("100000")),
        (Decimal("1000"), Decimal("100000")),
    ),
)
def test_buffer_inequality_preserves_printed_offset_frame(
    relation: str,
    calibration: str,
    operator: str,
    threshold: Decimal,
    temperature_K: Decimal | None,
    pressure_pa: Decimal | None,
) -> None:
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={
            "oxygen_buffer_as_published": calibration,
            "oxygen_fugacity_buffer_relation": relation,
        },
        point_conditions=None,
        scope="row",
        run_temperature_K=temperature_K,
        total_pressure_pa=pressure_pa,
    )

    assert decision is not None and decision.interval_condition is not None
    condition = decision.interval_condition
    assert condition.state.is_value
    value = condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.BOUND
    assert value.bound_operator == operator
    assert value.bound_value == threshold
    assert condition.inference.output_unit == (
        "log10(IW)" if operator == "<" else "log10(NNO)"
    )
    assert "calibration=" not in condition.inference.relation


def test_real_below_iw_numeric_offset_does_not_become_an_inequality() -> None:
    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/llnl-2021-cai-aerodynamic-levitation.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    relation = None
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if "oxygen_fugacity_buffer_relation" in value:
                relation = value["oxygen_fugacity_buffer_relation"]
                break
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert isinstance(relation, str)
    family, offset, _printed, is_prose = _classify_buffer_phrase(relation)
    assert (family, offset, is_prose) == ("IW", Decimal("-0.5"), False)
    raw_item = {"oxygen_fugacity_buffer_relation": relation}
    retained_interval = _printed_fo2_interval(
        raw_item,
        None,
        total_pressure_pa=None,
    )
    assert retained_interval is None

    printed_log = Decimal("-9.1")
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(
            log_fO2=Located(State.of(printed_log)),
            log_fO2_reference_Pa=Decimal("101325"),
        ),
        raw_item={"oxygen_fugacity_buffer_relation": relation},
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal("1873"),
    )

    assert decision is not None and decision.kind == "log"
    assert decision.state.value == _log_fO2_to_pa(printed_log, Decimal("101325"))


def test_real_explicit_fo2_inequality_stays_a_typed_bound() -> None:
    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/volovetskii-2012-iron-redox.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    row = None
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            conditions = value.get("point_conditions")
            if isinstance(conditions, Mapping):
                oxygen = conditions.get("fO2_log")
                state = oxygen.get("state") if isinstance(oxygen, Mapping) else None
                typed = state.get("value") if isinstance(state, Mapping) else None
                if isinstance(typed, Mapping) and typed.get("kind") == "bound":
                    row = value
                    break
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert row is not None
    raw_condition = row["point_conditions"]["fO2_log"]
    condition = _point_condition_from_plain(raw_condition, key="fO2_log")
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"point_conditions": {"fO2_log": raw_condition}},
        point_conditions={"fO2_log": condition},
        scope="row",
    )

    assert decision is not None and decision.interval_condition is not None
    value = decision.interval_condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.BOUND
    assert value.bound_operator == "<"
    assert value.bound_value == Decimal("-11.5")


def test_printed_calibration_is_used_exactly() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "IW; Frost (1991)",
        }
    )

    assert decision is not None and decision.state.is_value
    expected_log = Decimal(str(frost_buffer_log10_fO2_bar("IW", 1000.0))) + Decimal("1.0")
    assert decision.state.value == _log_fO2_to_pa(expected_log)
    assert "calibration=frost_1991_table_1" in decision.notice_reason
    assert "printed calibration used exactly" in decision.notice_reason
    assert "pressure=1 bar" in decision.notice_reason
    assert "pressure correction=none at or below 1 bar" in decision.notice_reason
    assert decision.notice_band == "not reported (printed calibration used exactly)"


def test_printed_absolute_log_wins_over_buffer_relative_offset() -> None:
    facts = PrintedOxygenFacts(
        log_fO2=Located(State.of(Decimal("-9"))),
    )
    decision = printed_oxygen_decision(
        facts,
        raw_item={"delta_IW": "+2.0", "oxygen_buffer_as_published": "IW"},
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal(1000),
    )

    assert decision is not None and decision.kind == "log"
    assert decision.state.value == _log_fO2_to_pa(Decimal("-9"))
    assert decision.notice_reason is None


def test_canonical_iw_conversion_carries_authority_and_calibration_band() -> None:
    decision = _decision({"delta_IW": "+1.0"})

    assert decision is not None and decision.state.is_value
    # O'Neill (1988) eq. 11 first segment gives μ°O2 = -605812 + 1366.718T
    # - 182.7955T ln(T) + 0.10359T² J/mol. At 1000 K, μ°/(R T ln 10) is
    # -20.799980169 log10(fO2/bar), then the printed +1.0 gives this literal.
    # J/mol divided by (J mol-1 K-1)K is dimensionless; ln(10) changes bases.
    expected_log = Decimal("-19.799980169")
    assert abs(decision.state.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert "buffer=IW" in decision.notice_reason
    assert "offset_as_printed=+1.0" in decision.notice_reason
    assert "calibration=oneill_1988_ammin_73_eq11" in decision.notice_reason
    assert "authority=primary_EMF" in decision.notice_reason
    assert "T=1000 K" in decision.notice_reason
    assert "band=±" in decision.notice_reason
    assert decision.notice_band.startswith("±")


def test_short_diw_field_uses_the_iw_calibration() -> None:
    decision = _decision({"dIW": "-0.5"})

    assert decision is not None and decision.state.is_value
    # The same O'Neill (1988) eq. 11 1000 K value, -20.799980169 log10(bar),
    # plus the source's printed -0.5 offset gives -21.299980169 log10(bar).
    expected_log = Decimal("-21.299980169")
    assert abs(decision.state.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert "buffer=IW" in decision.notice_reason
    assert "offset_as_printed=-0.5" in decision.notice_reason


def test_nno_uses_the_interim_compilation_and_names_its_authority() -> None:
    decision = _decision({"delta_NNO": "+0.0"})

    assert decision is not None and decision.state.is_value
    assert decision.notice_kind is NoticeKind.DERIVATION_USES_COMPILATION
    assert "calibration=frost_1991_nno_interim_compilation" in decision.notice_reason
    assert "authority=compilation_calibration" in decision.notice_reason
    assert "band=not available" in decision.notice_reason


def test_buffer_without_cited_canonical_calibration_refuses() -> None:
    decision = _decision({"delta_Mo-MoO2": "+1.0"})

    assert decision is not None and decision.state.is_unknown
    assert "no cited canonical calibration" in decision.state.reason
    assert "fO2_Pa assigned" in decision.state.reason


def test_out_of_range_iw_is_predicted_and_flagged_with_phase_boundary() -> None:
    decision = _decision({"delta_IW": "+1.0"}, temperature_K=1800)

    assert decision is not None and decision.state.is_value
    assert decision.notice_kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert "predict_and_flag_outside_range_833_1450_K" in decision.notice_reason
    assert "phase_boundary_extrapolation_above_1650_K_FeO_melting" in decision.notice_reason
    assert "calibration_range=833–1450 K" in decision.notice_reason
    assert "±" in decision.notice_band
    assert "outside calibration range 833–1450 K" in decision.notice_band


def test_ideal_ratio_definition_gets_the_named_notice() -> None:
    quoted_definition = "ΔIW = 2 log(x_FeO^sil / x_Fe^met)"
    assert _delta_iw_ideal_ratio_notice(quoted_definition) == (
        "delta_iw_from_ideal_feo_fe_ratio; source definition: "
        + quoted_definition
    )


def test_kems_delta_iw_is_a_printed_buffer_reference_not_an_ideal_ratio() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "O’Neill and Pownceby, 1993",
        },
        source_id="kems-137-bischof-2023",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_reference=iron_wustite_buffer" in decision.notice_reason
    assert "IW = iron-wüstite buffer" in decision.notice_reason
    assert "delta_iw_from_ideal_feo_fe_ratio" not in decision.notice_reason

    without_calibration = _decision(
        {"delta_IW": "+1.0"},
        source_id="kems-137-bischof-2023",
    )
    assert without_calibration is not None and without_calibration.state.is_unknown
    assert "no cited buffer calibration" in without_calibration.state.reason


def test_unresolved_year_citation_does_not_suppress_unknown_buffer_refusal() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "Myers and Eugster, 1983",
        }
    )

    assert decision is not None and decision.state.is_unknown
    assert "conflicting offsets" in decision.state.reason
    assert "missing input; no fO2_Pa assigned" in decision.state.reason
    assert "oneill_1988" not in decision.state.reason


def test_mavrogenes_activity_correction_names_the_holzheid_gamma_model() -> None:
    decision = _decision(
        {"log_fO2_as_delta_IW": "-1.56"},
        source_id="mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_activity_corrected_gamma_model=Holzheid1997_FeO_gamma_1.7" in decision.notice_reason


def test_piston_cylinder_pressure_pins_frost_iw_delta_v_term() -> None:
    one_bar = _decision({"delta_IW": "+1.0"}, run_pressure_bar=Decimal("1"))
    high_pressure = _decision(
        {"delta_IW": "+1.0"}, run_pressure_bar=Decimal("10000")
    )

    assert one_bar is not None and high_pressure is not None
    # Frost (1991), Table 1: log10(fO2/bar)=A/T+B+C(P-1)/T. The pressure
    # difference is exactly C(10000-1)/T = 0.055*9999/1000 = 0.549945 dex;
    # (K/bar)*bar/K is dimensionless. Compare logs so Pa/bar cancels.
    expected_delta = Decimal("0.055") * Decimal("9999") / Decimal("1000")
    actual_delta = high_pressure.state.value.log10() - one_bar.state.value.log10()
    assert abs(actual_delta - expected_delta) < Decimal("1e-25")
    assert "pressure correction=Frost 1991 molar-volume ΔV term" in high_pressure.notice_reason


@pytest.mark.parametrize("number", (".5", "1e-3", "1e2", "+.5", "-.5", "+1E-3", "-1E+2", "1."))
@pytest.mark.parametrize("direction,sign", (("above", 1), ("below", -1)))
@pytest.mark.parametrize("units", ("", "log units "))
def test_distance_consumes_complete_number_through_oxygen_decision(number, direction, sign, units):
    expected = _decision({"delta_IW": str(sign * abs(Decimal(number)))})
    actual = _decision({"oxygen_fugacity_buffer_relation": f"{number} {units}{direction} IW"})
    assert expected is not None and expected.state.is_value
    assert actual is not None and actual.state == expected.state


@pytest.mark.parametrize("number", ("+.5", "-.5", "+1e-3", "-1e-3", "+1e2", "-1e2", "+0e0", "-0.00"))
@pytest.mark.parametrize("direction,operator", (("below", "<"), ("under", "<"), ("less than", "<"), ("above", ">"), ("over", ">"), ("greater than", ">")))
def test_bound_consumes_complete_number_through_oxygen_decision(number, direction, operator):
    actual = _decision({"oxygen_fugacity_buffer_relation": f"{direction} IW{number}"})
    assert actual is not None and actual.state.is_unknown
    assert actual.interval_condition is not None and actual.interval_condition.state.is_value
    bound = actual.interval_condition.state.value
    assert bound.bound_operator == operator
    assert bound.bound_value == Decimal(number)


@pytest.mark.parametrize("number", ("+not-a-number", "+NaN", "+", "-bad", "+1e", "+1e-3junk", "+.5junk", "+1.2.3", "+1/2", "++1"))
@pytest.mark.parametrize("shape", ("{} log units above IW", "{} below IW", "IW{}", "below IW{}", "above IW{}"))
@pytest.mark.parametrize("bare_sibling", (False, True))
@pytest.mark.parametrize("field", ("oxygen_fugacity_buffer_relation", "oxygen_buffer_as_published"))
def test_invalid_number_refuses_without_fabricating_zero(number, shape, bare_sibling, field):
    raw = {field: shape.format(number)}
    if bare_sibling:
        raw["fO2_control" if field == "oxygen_buffer_as_published" else "oxygen_buffer_as_published"] = "IW"
    actual = _decision(raw)
    assert actual is not None and actual.state.is_unknown
    assert actual.refusal_code is _PrintedOxygenRefusalCode.BUFFER_RELATIVE
    assert actual.interval_condition is None or actual.interval_condition.state.is_unknown


@pytest.mark.parametrize("family", _GRAMMAR_FAMILIES)
@pytest.mark.parametrize("direction,operator", (("below", "<"), ("above", ">")))
@pytest.mark.parametrize("offset", ("", "+.5", "-1e-3"))
def test_every_family_retains_bound_without_calibration_or_temperature(family, direction, operator, offset):
    actual = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"oxygen_fugacity_buffer_relation": f"{direction} {family}{offset}"},
        point_conditions=None, scope="row",
    )
    assert actual is not None and actual.state.is_unknown
    assert actual.interval_condition is not None and actual.interval_condition.state.is_value
    condition = actual.interval_condition
    assert condition.state.value.bound_operator == operator
    assert condition.state.value.bound_value == Decimal(offset or "0")
    expected_family = _classify_buffer_phrase(family)[0]
    assert condition.inference.output_unit == f"log10({expected_family})"
    assert "calibration=" not in condition.inference.relation


@pytest.mark.parametrize("label", ("FMQ+0.5.", "FMQ+1e-3.", "FMQ+.5."))
def test_terminal_sentence_period_keeps_the_complete_printed_offset(label):
    # The Borisov 2021 supplement prints its buffer control as "FMQ+0.5.".
    expected = _decision({"delta_FMQ": label.removeprefix("FMQ").removesuffix(".")})
    actual = _decision({"fO2_control": label})
    assert expected is not None and expected.state.is_value
    assert actual is not None and actual.state == expected.state
    assert actual.buffer_request.offset_as_printed == label.removeprefix("FMQ").removesuffix(".")
