"""Guard numeric YAML scalars against unsigned/dotless exponent spellings."""

from __future__ import annotations

import heapq
from decimal import Decimal, InvalidOperation
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.yaml_cache import YAML12SafeLoader
from tools.validate_literature_extracts import _DOTLESS_EXPONENT_RE


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
YAML_CORPUS_SHARD_COUNT = 16
GREEN_SCALAR_BASE = "be76d28ad"
# Follow-up b-1220 owns reducing green's 139 legacy losses; this round keeps
# that set as a ceiling while preventing rewritten scalars from adding losses.
GREEN_LOSSY_FLOAT_CEILING = 139
NEW_EXTRACTS = (
    "data/literature/extracts/cohen-2006-iron-silicate-reduction-isotopes.yaml",
    "data/literature/extracts/floss-1998-corundum-hibonite-evaporation-residues.yaml",
    "data/literature/extracts/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry.yaml",
    "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml",
    "data/literature/extracts/smythe-2013-cerium-thesis.yaml",
    "data/literature/extracts/van-westrenen-1999-garnet-melt-trace-partitioning.yaml",
)

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


def _balanced_yaml_corpus_shards() -> tuple[tuple[Path, ...], ...]:
    """Distribute the full YAML corpus across xdist workers by source size."""
    yaml_paths = sorted(DATA_ROOT.rglob("*.yaml")) + sorted(
        DATA_ROOT.rglob("*.yml")
    )
    loads = [(0, shard) for shard in range(YAML_CORPUS_SHARD_COUNT)]
    heapq.heapify(loads)
    shards: list[list[Path]] = [[] for _ in range(YAML_CORPUS_SHARD_COUNT)]
    for path in sorted(yaml_paths, key=lambda item: item.stat().st_size, reverse=True):
        size, shard = heapq.heappop(loads)
        shards[shard].append(path)
        heapq.heappush(loads, (size + path.stat().st_size, shard))
    return tuple(tuple(shard) for shard in shards)


YAML_CORPUS_SHARDS = _balanced_yaml_corpus_shards()


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
    if "printed_rows" in parts or "rows_as_printed" in parts or any(
        part.endswith("_row_as_printed") for part in parts
    ):
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
            and node.style in (None, "")
            and _DOTLESS_EXPONENT_RE.fullmatch(node.value)
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


def _find_precision_losing_floats(node: yaml.Node, path: tuple[str, ...] = ()):
    """Find float scalars whose YAML lexeme is not preserved by float text."""
    if isinstance(node, yaml.ScalarNode):
        if node.tag == "tag:yaml.org,2002:float":
            token = node.value.replace("_", "")
            try:
                printed = Decimal(token)
                parsed_float = float(token)
                parsed = Decimal(str(parsed_float))
            except (InvalidOperation, ValueError, OverflowError):
                return
            if printed.is_finite() and printed != parsed:
                yield (path, node.start_mark.line + 1, token)
        return
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield from _find_precision_losing_floats(
                value_node, path + (str(key_node.value),)
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            yield from _find_precision_losing_floats(value_node, path + (str(index),))


def _source_extract(relative: str) -> bool:
    return (
        (
            relative.startswith("data/literature/extracts/")
            or relative.startswith("data/literature/extracts-v2/")
        )
        and "/audits/" not in relative
        and not relative.endswith(".mapping.yaml")
    )


def _source_node(source: str) -> yaml.Node | None:
    loader = YAML12SafeLoader(source)
    try:
        return loader.get_single_node()
    finally:
        loader.dispose()


def _load_with_source_nodes(source: str):
    node = _source_node(source)
    if node is None:
        return [], []
    return (
        list(_find_untyped_exponents(node, ())),
        list(_find_precision_losing_floats(node)),
    )


@pytest.mark.parametrize("relative", NEW_EXTRACTS)
def test_new_extracts_have_no_untyped_dotless_exponents(relative: str) -> None:
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    findings, _precision_losses = _load_with_source_nodes(source)
    assert not findings, f"{relative} has untyped dotless exponents: {findings}"


@pytest.mark.parametrize(
    "yaml_paths",
    YAML_CORPUS_SHARDS,
    ids=[
        f"yaml-corpus-shard-{index:02d}"
        for index in range(YAML_CORPUS_SHARD_COUNT)
    ],
)
def test_every_data_yaml_has_no_untyped_dotless_exponent_in_numeric_fields(yaml_paths):
    """Use the production loader over the complete corpus, including flow sequences."""
    findings: list[str] = []
    precision_findings: list[str] = []
    parse_refusals: list[str] = []
    current_loss_count = 0
    green_loss_count = 0
    assert yaml_paths, f"no YAML files found below {DATA_ROOT}"

    for path in yaml_paths:
        source = path.read_text(encoding="utf-8")
        try:
            untyped, precision_losses = _load_with_source_nodes(source)
        except yaml.YAMLError as exc:
            # P3: a strict parser refusal means there are no loaded scalar
            # values to inspect in this document. Keep it visible in failures.
            parse_refusals.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if _source_extract(relative):
            try:
                green_source = subprocess.check_output(
                    ["git", "show", f"{GREEN_SCALAR_BASE}:{relative}"],
                    cwd=REPO_ROOT,
                    text=True,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as exc:
                try:
                    subprocess.run(
                        ["git", "cat-file", "-e", f"{GREEN_SCALAR_BASE}:{relative}"],
                        cwd=REPO_ROOT,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    subprocess.run(
                        ["git", "cat-file", "-e", f"{GREEN_SCALAR_BASE}^{{commit}}"],
                        cwd=REPO_ROOT,
                        check=True,
                    )
                    green_source = ""
                else:
                    raise exc
            else:
                _green_exponents, green_losses = _load_with_source_nodes(
                    green_source
                )
                green_signatures = {
                    (key_path, value) for key_path, _line, value in green_losses
                }
            current_signatures = {
                (key_path, value) for key_path, _line, value in precision_losses
            }
            new_losses = current_signatures - green_signatures
            current_loss_count += len(current_signatures)
            green_loss_count += len(green_signatures)
            precision_findings.extend(
                f"{relative}:{line} {'.'.join(key_path)} = {value!r}"
                for key_path, line, value in precision_losses
                if (key_path, value) in new_losses
            )
            assert current_signatures <= green_signatures, (
                f"new precision-losing float in {relative}; "
                "the pre-existing source losses are only a ceiling "
                "for follow-up b-1220"
            )
        for key_path, line, value, _start, _end in untyped:
            if _deliberate_printed_string_reason(relative, key_path):
                continue
            findings.append(f"{relative}:{line} {key_path} = {value!r}")

    assert green_loss_count <= GREEN_LOSSY_FLOAT_CEILING, (
        "green shard exceeds the corpus precision-loss ceiling; see follow-up b-1220"
    )
    assert current_loss_count <= green_loss_count, (
        "extract precision-loss count must only fall; see follow-up b-1220"
    )
    assert not precision_findings, (
        "new extract floats lose digits against their YAML lexemes:\n"
        + "\n".join(precision_findings)
    )
    assert not findings, (
        "YAML numeric-looking string scalars:\n"
        + "\n".join(findings)
        + "\nStrict parser refusals (P3):\n"
        + "\n".join(parse_refusals)
    )


def test_precision_guard_rejects_a_new_lossy_float_mutation():
    exact_source = "value: '0.0027299999999999997879474022965951007790863513946533203125'\n"
    mutated_source = (
        "value: 0.0027299999999999997879474022965951007790863513946533203125\n"
    )
    exact_node = _source_node(exact_source)
    mutated_node = _source_node(mutated_source)
    assert exact_node is not None and mutated_node is not None
    prior_signatures = {
        (key_path, value)
        for key_path, _line, value in _find_precision_losing_floats(exact_node)
    }
    mutated_signatures = {
        (key_path, value)
        for key_path, _line, value in _find_precision_losing_floats(mutated_node)
    }

    assert mutated_signatures - prior_signatures == {
        (("value",), "0.0027299999999999997879474022965951007790863513946533203125")
    }


@pytest.mark.parametrize(
    ("source", "expected_path"),
    [
        ("value: 3e-8\n", "value"),
        ("values: [3e-8]\n", "values.0"),
        ("values: {sample: 3e-8}\n", "values.sample"),
    ],
    ids=["block-mapping", "flow-sequence", "flow-mapping"],
)
def test_production_loader_detects_plain_dotless_exponents(source, expected_path):
    """Null-hypothesis: libyaml's empty plain style lets strings evade the guard."""
    root = _source_node(source)
    assert root is not None
    loaded = yaml.load(source, Loader=YAML12SafeLoader)
    assert loaded is not None
    value = loaded["value"] if expected_path == "value" else (
        loaded["values"][0]
        if expected_path == "values.0"
        else loaded["values"]["sample"]
    )
    assert value == "3e-8"
    assert isinstance(value, str)

    findings = list(_find_untyped_exponents(root, ()))
    assert [(path, token) for path, _line, token, _start, _end in findings] == [
        (expected_path, "3e-8")
    ]


def test_oneill_identity_pressure_keeps_its_exact_decimal_lexeme():
    relative = (
        "data/literature/extracts/"
        "oneill-2002-feo-activity-coefficients-cmas.yaml"
    )
    document = yaml.load(
        (REPO_ROOT / relative).read_text(encoding="utf-8"),
        Loader=YAML12SafeLoader,
    )
    value = document["species"]["FeO"]["observations"][0]["values"][
        "oxygen_partial_pressure"
    ]["value"]

    assert isinstance(value, str)
    assert value == "2.454708915685030356082721740674509764216E-13"
    assert _deliberate_printed_string_reason(
        relative, "species.FeO.observations.0.values.oxygen_partial_pressure.value"
    ) is not None
