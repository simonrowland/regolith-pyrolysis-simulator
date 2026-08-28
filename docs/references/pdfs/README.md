# Reference PDFs

Local PDFs live in numbered topic subdirectories:

```text
NN-<topic>/
```

The YAML registry (`docs/references/references.yaml`) remains the source of truth. PDFs are
supporting evidence, not the record.

## Filenames

Two naming conventions are in use, because the archive serves two different registries.

**1. Registry-ID naming**, for a source that has a `REF-NNN` entry in `references.yaml`:

```text
01-redox-fo2/REF-001-kress-carmichael-1991.pdf
```

**2. Corpus-ID naming**, for a source that enters through a literature corpus rather than the
`REF-NNN` registry. The filename stem is the corpus ID, and it must match the stem of the
corresponding extract in `data/literature/extracts/`:

```text
99-kems-langmuir/kems-001-homma-1966.pdf   ->   data/literature/extracts/kems-001-homma-1966.yaml
```

This is what the KEMS/Langmuir corpus uses, and at present it is the only populated topic
directory. No PDF currently on disk uses the `REF-NNN-` form; that convention is the rule for
REF-registered sources, not a description of the current archive.

## Filing can lead extraction

A PDF may be filed before its extract exists — acquiring the paper and digitising its
measurements are separate steps, and a PDF with no extract yet is a normal intermediate state,
not an error. It does mean the archive is larger than the scored corpus. To see the gap:

```bash
./.venv/bin/python - <<'PY'
import pathlib
pdfs = {p.stem for p in pathlib.Path('docs/references/pdfs').rglob('*.pdf')}
yaml = {p.stem for p in pathlib.Path('data/literature/extracts').glob('*.yaml')}
print(f"{len(pdfs)} PDFs, {len(pdfs & yaml)} with an extract, {len(pdfs - yaml)} awaiting one")
print(sorted(pdfs - yaml))
PY
```

## What belongs where

`data/literature/` has two stores and the split is load-bearing:

- `extracts/` holds **experimental measurements** — the engine is validated *against* these.
- `compilations/` holds **assessed thermodynamic functions** (NIST-JANAF and similar) — the
  engine *consumes* these, so they produce no scoring rows. Validating the engine against a
  table it already reads would be circular. See `data/literature/compilations/README.md`.

A PDF of a measurement paper is supporting evidence for an `extracts/` entry. A PDF of a
compilation is supporting evidence for a `compilations/` entry, and must not be turned into an
extract.

The PDF archive mirrors the Propulsion `reference-library-pdfs/` convention.
