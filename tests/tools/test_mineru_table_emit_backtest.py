from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os

import pytest
import yaml

from tools import mineru_table_draft as draft


FIXTURES = Path(__file__).parent / "fixtures" / "mineru-table-emit"
EXTRACTS = Path(__file__).resolve().parents[2] / "data" / "literature" / "extracts"
DEFAULT_CORPUS = Path.home() / "Repos" / "regolith-corpus"
MAPPINGS = sorted(FIXTURES.glob("*.mapping.yaml"))


def _corpus_roots() -> tuple[Path, Path]:
    configured = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS))
    if configured.name == "raw":
        return configured.parent, configured
    if (configured / "raw").is_dir():
        return configured, configured / "raw"
    return configured, configured / "raw"


def _source_dir(corpus_root: Path, raw_root: Path, source_id: str) -> Path:
    candidates = (
        raw_root / source_id / "ocr-mineru",
        corpus_root / "text" / source_id / "mineru",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


def _convert_source(
    source: Path, source_id: str, out_dir: Path, raw_root: Path
) -> Path:
    full_vlm = source / "full" / "vlm"
    if full_vlm.is_dir():
        alias = out_dir / "normalized" / source_id / "ocr-mineru" / "p001-p001" / "vlm"
        alias.mkdir(parents=True)
        for content_list in sorted(full_vlm.glob("*content_list.json")):
            (alias / content_list.name).symlink_to(content_list)
        if not any(alias.iterdir()):
            pytest.fail(f"no full-document MinerU content lists found under {full_vlm}")
        source = alias.parents[1]
    return draft.convert_source(source, out_dir / "converted", corpus_root=raw_root)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip().strip("$").strip()
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"[*†‡§¶|]+$", "", text).strip()
    scientific = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\\times\s*10\^\{?([+-]?\d+)\}?",
        text,
    )
    if scientific:
        return Decimal(scientific.group(1)) * (Decimal(10) ** int(scientific.group(2)))
    text = re.sub(r"\((?:\d+)\)$", "", text)
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _field_values(observation: dict, quantity: str) -> list[object]:
    values = observation.get("values") or {}
    if quantity == "Fe3plus_over_total_Fe":
        return [point.get("value") for point in values.get("points") or []]
    if quantity == "partial_pressure":
        fields = ("pressure_atm", "pressure_bar", "pressure_Pa")
        return [point.get(field) for point in values.get("points") or [] for field in fields if field in point]
    if quantity == "p_partial":
        return [point.get("pressure_bar") for point in values.get("series") or []]
    if quantity == "delta_fG":
        return [values.get("value")]
    if quantity == "elemental_abundance":
        return [values.get("abundance_ppm")]
    if quantity == "residue_composition_vs_time":
        return [point.get("residue_ppm") for point in values.get("series") or []]
    if quantity == "sodium_disilicate_total_weight_loss":
        fields = ("extractive_weight_loss_mg", "gravimetric_weight_loss_mg")
        return [row.get(field) for row in values.get("rows") or [] for field in fields]
    if quantity == "activity":
        if "activity" in values:
            return [values.get("activity")]
        return [point.get("activity") for point in values.get("points") or []]
    if quantity == "mass_loss_rate":
        return [values.get("mass_loss_rate")]
    if quantity == "mass_loss_areal_density":
        return [point.get("mass_loss_areal_density") for point in values.get("series") or []]
    if quantity == "transition_temperature":
        return [point.get("T_C") for point in values.get("series") or []]
    if quantity == "ion_intensity":
        return [values.get("ion_intensity")]
    return []


def _locator_page(locator: dict) -> int | None:
    page_index = locator.get("pdf_page_index")
    if page_index is not None:
        return int(page_index) + 1
    page = locator.get("page")
    return int(page) if page is not None else None


def _locator_key(locator: dict) -> tuple[str, str] | None:
    published_page = locator.get("published_page")
    if published_page is not None:
        return "published_page", str(published_page)
    page = _locator_page(locator)
    return ("pdf_page", str(page)) if page is not None else None


def _extract_targets(
    mapping: dict, extract: dict
) -> dict[tuple[str, str, str, str], Counter[Decimal]]:
    targets: set[tuple[int, object, str]] = {
        (int(table["page"]), table.get("published_page"), str(table["quantity"]))
        for table in mapping.get("tables") or []
    }
    expected: dict[tuple[str, str, str, str], Counter[Decimal]] = defaultdict(Counter)
    for species, block in (extract.get("species") or {}).items():
        for observation in block.get("observations") or []:
            values = observation.get("values") or {}
            quantity = str(values.get("quantity") or "")
            if quantity not in {target[2] for target in targets}:
                continue
            locator = observation.get("locator") or {}
            for page, published_page, target_quantity in targets:
                if quantity != target_quantity:
                    continue
                if published_page is not None:
                    if str(locator.get("published_page")) != str(published_page):
                        continue
                    locator_kind, locator_value = "published_page", str(published_page)
                else:
                    if _locator_page(locator) != page:
                        continue
                    locator_kind, locator_value = "pdf_page", str(page)
                for raw in _field_values(observation, quantity):
                    number = _decimal(raw)
                    if number is not None:
                        expected[
                            (quantity, str(species), locator_kind, locator_value)
                        ][number] += 1
    return expected


def _emitted_cells(document: dict) -> dict[tuple[str, str, str, str], Counter[Decimal]]:
    emitted: dict[tuple[str, str, str, str], Counter[Decimal]] = defaultdict(Counter)
    for species, block in (document.get("species") or {}).items():
        for observation in block.get("observations") or []:
            values = observation.get("values") or {}
            quantity = str(values.get("quantity") or "")
            field = {
                "activity": "activity",
                "activity_coefficient": "gamma",
            }.get(quantity, "value")
            if quantity == "ion_intensity":
                field = "value"
            if quantity == "partial_pressure":
                unit = str(observation.get("units") or "")
                field = f"pressure_{unit}"
            number = _decimal(values.get(field))
            if number is not None:
                locator_key = _locator_key(observation.get("locator") or {})
                if locator_key is not None:
                    emitted[(quantity, str(species), *locator_key)][number] += 1
    return emitted


@pytest.mark.parametrize(
    "mapping_path",
    MAPPINGS,
    ids=lambda path: path.name.removesuffix(".mapping.yaml"),
)
def test_committed_emit_mappings_reproduce_landed_decimal_cells(
    mapping_path: Path, tmp_path: Path
) -> None:
    corpus_root, raw_root = _corpus_roots()
    if not corpus_root.is_dir() or not raw_root.is_dir():
        pytest.skip(f"regolith-corpus checkout is absent: {corpus_root}")

    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    if not source.is_dir():
        pytest.fail(f"MinerU source directory is absent for {source_id}: {source}")
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    output_path = tmp_path / f"{source_id}.yaml"
    if not mapping.get("tables"):
        with pytest.raises(ValueError, match="tables must be a non-empty list"):
            draft.emit_extract(draft_dir, mapping_path, output_path)
        return

    document = draft.emit_extract(draft_dir, mapping_path, output_path)
    extract_path = EXTRACTS / f"{source_id}.yaml"
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    expected = _extract_targets(mapping, extract)
    emitted = _emitted_cells(document)
    assert expected, f"no landed numeric cells found for mapped source {source_id}"

    missing: dict[tuple[str, str, str, str], Counter[Decimal]] = {}
    for key, counts in expected.items():
        absent = counts - emitted.get(key, Counter())
        if absent:
            missing[key] = absent

    assert not missing, f"landed cells absent from emit: {missing}"
    for observation in (
        item
        for block in (document.get("species") or {}).values()
        for item in block.get("observations") or []
    ):
        quantity = observation["values"]["quantity"]
        if quantity == "activity":
            assert "activity" in observation["values"]
            assert "value" not in observation["values"]
