"""Regenerate Su & Zhang's path metadata using the extract path vocabulary."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SID = "su-zhang-2023-lunar-orange-glass-na-k-cu-degassing"
SUPPLEMENT_PATH = f"raw/{SID}/supplement.docx"
SUPPLEMENT_SHA256 = "18afaf45a603291100c46cbb8143ff79fd473fc7e9bc0f81d072334f1f91d818"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
MAPPING_AUDIT = ROOT / "data/literature/extracts/audits" / f"{SID}.mapping.yaml"

EXTRACT_REPLACEMENTS = (
    (
        f"  supplementary_document_asset: {SUPPLEMENT_PATH}\n"
        f"  supplementary_document_sha256: {SUPPLEMENT_SHA256}\n",
        "  supplementary_document:\n"
        f"    corpus_asset: {SUPPLEMENT_PATH}\n"
        f"    sha256: {SUPPLEMENT_SHA256}\n",
    ),
    (
        f"          asset: {SUPPLEMENT_PATH}\n",
        f"          corpus_asset: {SUPPLEMENT_PATH}\n",
    ),
)
MAPPING_REPLACEMENTS = (
    (
        f"supplementary_docx: {SUPPLEMENT_PATH}\n",
        "supplementary_document:\n"
        f"  corpus_asset: {SUPPLEMENT_PATH}\n",
    ),
)


def write_generated(
    path: Path,
    replacements: tuple[tuple[str, str], ...],
    expected_path_count: int,
) -> None:
    original = path.read_text(encoding="utf-8")
    generated = original
    for old, new in replacements:
        count = generated.count(old)
        if count == 1:
            generated = generated.replace(old, new, 1)
        elif count != 0 or new not in generated:
            raise ValueError(f"unexpected generated fragment in {path}")
    document = yaml.safe_load(generated)
    if path == EXTRACT:
        assert document["source"]["supplementary_document"] == {
            "corpus_asset": SUPPLEMENT_PATH,
            "sha256": SUPPLEMENT_SHA256,
        }
    else:
        assert document["supplementary_document"]["corpus_asset"] == SUPPLEMENT_PATH
    assert generated.count(SUPPLEMENT_PATH) == expected_path_count
    if generated != original:
        path.write_text(generated, encoding="utf-8")
    print(f"generated {path.relative_to(ROOT)}")


def main() -> None:
    write_generated(EXTRACT, EXTRACT_REPLACEMENTS, expected_path_count=2)
    write_generated(MAPPING_AUDIT, MAPPING_REPLACEMENTS, expected_path_count=1)


if __name__ == "__main__":
    main()
