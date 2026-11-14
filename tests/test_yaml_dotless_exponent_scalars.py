"""Guard numeric YAML scalars against unsigned/dotless exponent spellings."""

from __future__ import annotations

from pathlib import Path
import re
import yaml

from simulator.yaml_cache import YAML12SafeLoader


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
DOTLESS_EXPONENT = re.compile(r"^[-+]?\d+(?:\.\d+)?[eE][-+]?\d+$")

# These fields intentionally retain printed lexemes or prose as strings. Each
# rule names a schema field and why converting it would lose its contract.
DELIBERATE_PRINTED_STRING_FIELDS = {
    "number_format": "presentation token describing the source cell format",
    "displayed_value": "presentation string; the numeric value is stored separately",
    "printed_cell": "verbatim source-cell token",
    "as_published": "exact published numeric lexeme retained alongside parsed value",
    "value_as_printed": "exact source lexeme retained for printed-value parsing",
    "printed_value": "exact source lexeme retained for printed-value parsing",
}
DELIBERATE_PRINTED_SUFFIXES = {
    "_as_printed": "field name declares an exact printed lexeme",
    "_as_published": "field name declares the exact published lexeme",
}
EXACT_DECIMAL_STRING_FIELDS = {
    (
        "data/literature/extracts/banya-hino-takezoe-1985-na-fe-silicate-redox.yaml",
        "species.Na2O.observations.0.values.activity.bound_value",
    ),
    (
        "data/literature/extracts/oneill-2002-feo-activity-coefficients-cmas.yaml",
        "species.FeO.observations.0.values.oxygen_partial_pressure.value",
    ),
}


def _deliberate_printed_string_reason(relative: str, key_path: str) -> str | None:
    """Explain schema fields whose numeric-looking strings are intentional."""
    if (relative, key_path) in EXACT_DECIMAL_STRING_FIELDS:
        return (
            "model-derived exact decimal consumed through Decimal conversion; "
            "a binary float would discard source precision"
        )
    parts = key_path.split(".")
    leaf = parts[-1]
    if leaf in DELIBERATE_PRINTED_STRING_FIELDS:
        return DELIBERATE_PRINTED_STRING_FIELDS[leaf]
    for suffix, reason in DELIBERATE_PRINTED_SUFFIXES.items():
        if leaf.endswith(suffix):
            return reason
    if "context" in parts:
        return "extract context is retained verbatim and is not migrated as observations"
    if "printed_rows" in parts or "rows_as_printed" in parts:
        return "raw printed table cells are retained as extract evidence"
    if len(parts) >= 2 and parts[-2:] == ["state", "value"]:
        return "scalar State values are serialized as exact decimal strings"
    if "verbatim" in parts:
        return "uncertainty verbatim payload preserves source wording and spelling"
    if (
        relative == "data/battery/pins.yaml"
        and len(parts) == 3
        and parts[0] == "pin_band_records"
        and parts[-1] == "centre"
    ):
        return "pin Decimal centers serialize as strings to preserve Decimal identity"
    if relative.startswith("data/literature/compilations/") and (
        "tokens" in parts or "fixed_width_tokens" in parts
    ):
        return "compilation token is preserved as OCR/fixed-width source text"
    return None


def _find_untyped_exponents(node: yaml.Node, path: tuple[str, ...]):
    if isinstance(node, yaml.ScalarNode):
        if (
            node.tag == "tag:yaml.org,2002:str"
            and DOTLESS_EXPONENT.fullmatch(node.value)
        ):
            yield (
                ".".join(path),
                node.start_mark.line + 1,
                node.value,
                node.start_mark.index,
                node.end_mark.index,
            )
        return
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield from _find_untyped_exponents(
                value_node, path + (str(key_node.value),)
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            yield from _find_untyped_exponents(value_node, path + (str(index),))


def _load_with_source_nodes(source: str):
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        if node is None:
            return []
        return list(_find_untyped_exponents(node, ()))
    finally:
        loader.dispose()


def test_every_data_yaml_has_no_untyped_dotless_exponent_in_numeric_fields():
    """Use production parsing over every data YAML, including flow sequences."""
    findings: list[str] = []
    parse_refusals: list[str] = []
    yaml_paths = sorted(DATA_ROOT.rglob("*.yaml")) + sorted(DATA_ROOT.rglob("*.yml"))
    assert yaml_paths, f"no YAML files found below {DATA_ROOT}"

    for path in yaml_paths:
        source = path.read_text(encoding="utf-8")
        try:
            untyped = _load_with_source_nodes(source)
        except yaml.YAMLError as exc:
            # P3: a strict parser refusal means there are no loaded scalar
            # values to inspect in this document. Keep it visible in failures.
            parse_refusals.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
            continue
        for key_path, line, value, _start, _end in untyped:
            relative = path.relative_to(REPO_ROOT).as_posix()
            if _deliberate_printed_string_reason(relative, key_path):
                continue
            findings.append(f"{relative}:{line} {key_path} = {value!r}")

    assert not findings, (
        "YAML numeric-looking string scalars:\n"
        + "\n".join(findings)
        + "\nStrict parser refusals (P3):\n"
        + "\n".join(parse_refusals)
    )
