#!/usr/bin/env python3
"""Harvest FastChem v3.1.3 ln K fits into the compilation home.

Copies the seated gas, condensate, and extended-gas files unchanged and
writes one JSON record per species. Shared extended-file rows are not
stored twice. Coefficients stay the printed tokens.

Usage::

  python tools/harvest_fastchem_compilation.py
  python tools/harvest_fastchem_compilation.py --corpus PATH --output-root PATH
"""

from __future__ import annotations

import argparse
import copy
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compilation_harvest_io import (  # noqa: E402
    copy_if_needed,
    resolve_existing,
    sha256_bytes,
    write_json,
)
from simulator.reference_data.fastchem import (  # noqa: E402
    COMPILATION_ROLE,
    COMPILATION_SOURCE,
    CONDENSATE_SOURCE_NAME,
    CONDENSATE_SOURCE_REL,
    EXTENDED_SOURCE_NAME,
    EXTENDED_SOURCE_REL,
    FIT_BASIS,
    FORM_AS_PUBLISHED,
    GAS_SOURCE_NAME,
    GAS_SOURCE_REL,
    MANIFEST_SCHEMA,
    SOURCE_ID,
    FastChemParseError,
    FastChemRecord,
    assign_extended_ids,
    compilation_readme,
    extended_only_records,
    load_peer_species,
    mark_duplicate_symbols,
    overlap_rows,
    overlap_summary,
    parse_condensate_lines,
    parse_gas_lines,
    shared_gas_differences,
    write_overlap_tsv,
)

CORPUS = Path(
    "/Users/simonrowland/Repos/regolith-corpus/raw/"
    "fastchem-v3-1-3-gas-condensate-coefficients"
)
SIDE_FILES = (
    "README.TXT",
    "SOURCE.md",
    "EXTENDED-SOURCE.md",
    "licence.md",
    "sidecar.yaml",
)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--gas", type=Path)
    parser.add_argument("--condensates", type=Path)
    parser.add_argument("--extended", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "literature" / "compilations" / "fastchem-v3-1-3",
    )
    parser.add_argument(
        "--compilations",
        type=Path,
        default=ROOT / "data" / "literature" / "compilations",
    )
    return parser.parse_args()


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _manifest_entry(record: FastChemRecord, source_sha256: str) -> dict[str, Any]:
    phase_published = record.phase_as_published
    return {
        "record_id": record.record_id,
        "record_group": record.record_group,
        "symbol_as_published": record.symbol,
        "name_as_published": record.name,
        "formula": record.formula,
        "phase": record.phase,
        "phase_as_published": phase_published,
        "charge": record.charge,
        "lineage_citation_as_published": record.lineage_citation,
        "ambiguity_count": len(record.ambiguities),
        "ambiguities": list(record.ambiguities),
        "sha256_source_file": source_sha256,
        "path": (
            f"data/literature/compilations/fastchem-v3-1-3/records/{record.record_id}.json"
        ),
        "source_locator": {
            "path": record.source_rel,
            "header_line": record.header_line,
            "coefficient_lines": list(record.coefficient_lines),
            "end_line": record.end_line,
        },
    }


def _citation_counts(records: list[FastChemRecord]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        record.lineage_citation or "(none)" for record in records
    )
    return dict(counts.most_common())


def build_manifest(
    records: list[FastChemRecord],
    *,
    digests: dict[str, str],
    standard_count: int,
    extended_count: int,
    condensate_count: int,
    disagreements: list[str],
    overlap: dict[str, Any],
) -> dict[str, Any]:
    digest_for = {
        "standard_gas": digests[GAS_SOURCE_NAME],
        "extended_gas": digests[EXTENDED_SOURCE_NAME],
        "condensate": digests[CONDENSATE_SOURCE_NAME],
    }
    entries = [
        _manifest_entry(record, digest_for[record.record_group]) for record in records
    ]
    absent = ("Na4SiO4", "Na6Si2O7")
    symbols = {record.symbol for record in records}
    return {
        "schema_version": MANIFEST_SCHEMA,
        "source_id": SOURCE_ID,
        "compilation_role": dict(COMPILATION_ROLE),
        "source": {
            **COMPILATION_SOURCE,
            "path": GAS_SOURCE_REL,
            "sha256": digests[GAS_SOURCE_NAME],
            "condensate_path": CONDENSATE_SOURCE_REL,
            "condensate_sha256": digests[CONDENSATE_SOURCE_NAME],
            "extended_path": EXTENDED_SOURCE_REL,
            "extended_sha256": digests[EXTENDED_SOURCE_NAME],
            "sidecar_sha256": digests["sidecar.yaml"],
            "readme_sha256": digests["README.TXT"],
            "source_md_sha256": digests["SOURCE.md"],
            "extended_source_md_sha256": digests["EXTENDED-SOURCE.md"],
            "licence_sha256": digests["licence.md"],
            "form_as_published": FORM_AS_PUBLISHED,
            "record_format": (
                "FastChem v3.1.3 logK text: three header lines, then species "
                "lines 'symbol [name] : Elem count ... # citation', one or "
                "more five-token coefficient lines. Gas has one coefficient "
                "line and no per-species temperature bound. Condensates print "
                "a phase flag and successive upper temperature limits, then "
                "that many coefficient lines. Natural log and standard "
                "pressure 1 bar are the method-paper statements in "
                "fit_basis. Printed coefficients are not inverted. "
                "No unit conversion."
            ),
        },
        "corpus_status": {
            "scope": (
                "standard gas file, condensate file, and extended-gas ions "
                "that are not already in the standard gas file"
            ),
            "tag_commit": COMPILATION_SOURCE["tag_commit"],
            "not_current_master": True,
            "logK_wo_ions_dat": "named in README.TXT; not in the seated packet",
            "shared_extended_rows": (
                "logK_extended.dat repeats the standard gas headers and "
                "coefficient tokens; those rows are stored once"
            ),
            "shared_header_or_coefficient_disagreements": disagreements,
            "symbols_absent": [name for name in absent if name not in symbols],
            "named_absent_confirmed": all(name not in symbols for name in absent),
        },
        "summary": {
            "record_count": len(records),
            "standard_gas_count": standard_count,
            "extended_only_gas_count": extended_count,
            "condensate_count": condensate_count,
            "shared_header_or_coefficient_disagreements": len(disagreements),
            "records_with_ambiguity_count": sum(
                1 for record in records if record.ambiguities
            ),
            "parse_ambiguity_count": sum(len(record.ambiguities) for record in records),
            "source_schema_version": "literature_compilation.v1",
        },
        "fit_basis": copy.deepcopy(FIT_BASIS),
        "overlap": overlap,
        "lineage_citation_counts": _citation_counts(records),
        "entries": entries,
    }


def main() -> int:
    args = cli()
    output_root = args.output_root.resolve()
    source_dir = output_root / "source"
    records_dir = output_root / "records"
    source_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)
    corpus = args.corpus or CORPUS

    gas_src = resolve_existing(
        [path for path in (args.gas, corpus / GAS_SOURCE_NAME, source_dir / GAS_SOURCE_NAME) if path],
        GAS_SOURCE_NAME,
    )
    cond_src = resolve_existing(
        [
            path
            for path in (
                args.condensates,
                corpus / CONDENSATE_SOURCE_NAME,
                source_dir / CONDENSATE_SOURCE_NAME,
            )
            if path
        ],
        CONDENSATE_SOURCE_NAME,
    )
    ext_src = resolve_existing(
        [
            path
            for path in (
                args.extended,
                corpus / EXTENDED_SOURCE_NAME,
                source_dir / EXTENDED_SOURCE_NAME,
            )
            if path
        ],
        EXTENDED_SOURCE_NAME,
    )
    copy_if_needed(gas_src, source_dir / GAS_SOURCE_NAME)
    copy_if_needed(cond_src, source_dir / CONDENSATE_SOURCE_NAME)
    copy_if_needed(ext_src, source_dir / EXTENDED_SOURCE_NAME)
    for name in SIDE_FILES:
        src = resolve_existing(
            [path for path in (corpus / name, source_dir / name) if path],
            name,
        )
        copy_if_needed(src, source_dir / name)

    digests = {
        name: sha256_bytes((source_dir / name).read_bytes())
        for name in (GAS_SOURCE_NAME, CONDENSATE_SOURCE_NAME, EXTENDED_SOURCE_NAME, *SIDE_FILES)
    }
    sidecar = yaml.safe_load((source_dir / "sidecar.yaml").read_text(encoding="utf-8"))
    for item in sidecar.get("files") or []:
        name = item.get("name")
        expected = item.get("sha256")
        if name in digests and digests[name] != expected:
            raise FastChemParseError(
                f"{name}: sha256 {digests[name]} does not match sidecar {expected}"
            )

    standard = parse_gas_lines(
        _lines(source_dir / GAS_SOURCE_NAME),
        source_rel=GAS_SOURCE_REL,
        record_prefix="FC-G-",
        record_group="standard_gas",
    )
    extended = parse_gas_lines(
        _lines(source_dir / EXTENDED_SOURCE_NAME),
        source_rel=EXTENDED_SOURCE_REL,
        record_prefix="FC-E-",
        record_group="extended_gas",
    )
    condensates = parse_condensate_lines(
        _lines(source_dir / CONDENSATE_SOURCE_NAME),
        source_rel=CONDENSATE_SOURCE_REL,
    )
    disagreements = shared_gas_differences(standard, extended)
    if disagreements:
        preview = "\n".join(disagreements[:5])
        raise FastChemParseError(
            f"{len(disagreements)} standard/extended disagreements:\n{preview}"
        )
    extra = extended_only_records(standard, extended)
    assign_extended_ids(extra)
    mark_duplicate_symbols(standard)
    mark_duplicate_symbols(extra)
    mark_duplicate_symbols(condensates)
    records = [*standard, *extra, *condensates]

    peers, bare, unparsed = load_peer_species(args.compilations.resolve())
    rows = overlap_rows(records, peers)
    overlap = overlap_summary(
        records,
        rows,
        bare_electron=bare,
        unparsed_peer_formulas=unparsed,
    )
    write_overlap_tsv(output_root / "overlap.tsv", rows)

    manifest = build_manifest(
        records,
        digests=digests,
        standard_count=len(standard),
        extended_count=len(extra),
        condensate_count=len(condensates),
        disagreements=disagreements,
        overlap=overlap,
    )
    for stale in records_dir.glob("FC-*.json"):
        stale.unlink()
    sha_for = {
        "standard_gas": digests[GAS_SOURCE_NAME],
        "extended_gas": digests[EXTENDED_SOURCE_NAME],
        "condensate": digests[CONDENSATE_SOURCE_NAME],
    }
    for record in records:
        write_json(
            records_dir / f"{record.record_id}.json",
            record.to_document(source_sha256=sha_for[record.record_group]),
        )
    manifest_path = output_root / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    readme = compilation_readme(
        {
            "record_count": len(records),
            "standard_gas_count": len(standard),
            "extended_only_gas_count": len(extra),
            "condensate_count": len(condensates),
            "shared_header_or_coefficient_disagreements": 0,
            "overlap": overlap,
        }
    )
    (output_root / "README.md").write_text(readme, encoding="utf-8")
    print(
        f"records={len(records)} gas={len(standard)} extended_only={len(extra)} "
        f"condensates={len(condensates)} overlap_rows={len(rows)} "
        f"with_any={overlap['fastchem_with_any_overlap']} "
        f"by_peer={overlap['matched_species_by_peer']} "
        f"unparsed={unparsed} bare={bare} "
        f"ambiguities={manifest['summary']['parse_ambiguity_count']} "
        f"manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
