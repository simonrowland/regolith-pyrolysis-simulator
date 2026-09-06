# kems-014-drowart-2005 — PDF sidecar

Corpus-ID naming: stem `kems-014-drowart-2005` matches
`data/literature/extracts/kems-014-drowart-2005.yaml`. Hunt id RH-46
(same DOI; do not land `rh46-drowart-2005` as a second source_id).
Topic directory `99-kems-langmuir/` is where this extract's inventory
lives.

## Citation

Drowart, J., Chatillon, C., Hastie, J. & Bonnell, D. (2005).
High-temperature mass spectrometry: Instrumental techniques,
ionization cross-sections, pressure measurements, and thermodynamic
data (IUPAC Technical Report). *Pure and Applied Chemistry* 77:683–737.
https://doi.org/10.1351/pac200577040683

## DOI

`10.1351/pac200577040683`

## Publisher

Walter de Gruyter GmbH, *Pure and Applied Chemistry* (IUPAC Inorganic
Chemistry Division / Commission on High Temperature and Solid State
Chemistry). Print ISSN 0033-4545, electronic ISSN 1365-3075.
Volume 77, issue 4, pages 683–737.

Crossref resource URL:
`https://www.degruyterbrill.com/document/doi/10.1351/pac200577040683/html`

NIST bibliographic record (abstract only, no PDF):
`https://www.nist.gov/publications/high-temperature-mass-spectrometry-accuracy-method-and-influence-ionization-cross`

## Licence

No Creative Commons (or other machine-readable) licence object in
Crossref for this DOI (`license` field absent). Unpaywall
`oa_status: closed`, `is_oa: false`, `has_repository_copy: false`,
`oa_locations: []` (queried 2026-09-06).

Printed footer on the publisher PDF (indexed official URL text):
"© 2005 IUPAC, Pure and Applied Chemistry 77, 683–737".

De Gruyter Brill HTML landing (search index; this seat's GET was WAF
202) labels the article "Publicly Available" and "© 2013 Walter de
Gruyter GmbH, Berlin/Boston".

`licence open` and `PDF on disk` are separate facts. This sidecar does
not treat Unpaywall-closed + missing CC as a licence-open fetch grant.

## Access

`access: unknown`

Publisher HTML claims public availability; Unpaywall reports closed;
Crossref carries no licence URL. Not classified `open` without a
licence object. Not classified `paywalled` solely from the WAF
challenge on a "Publicly Available" landing.

## File

| Field | Value |
|---|---|
| intended_path | `docs/references/pdfs/99-kems-langmuir/kems-014-drowart-2005.pdf` |
| present | no |
| sha256 | *(absent — PDF bytes not obtained)* |
| file_size_bytes | *(absent — PDF bytes not obtained)* |
| retrieved_date | 2026-09-06 |
| retrieved_url | *(none succeeded as PDF bytes)* |

Historical extraction pointer
`docs-private/research/2026-08-27-ocr-repair/kems-014-drowart-2005/`
is absent in this worktree and at the repo `docs-private/research/`
root. INDEX.yaml (`s-2` snapshot) already listed this DOI as
`pdf_status: ABSENT`.

## Retrieval attempts (2026-09-06)

Official OA routes only (publisher, HAL, arXiv, OSTI, IUPAC, NIST).
No paywall circumvention. No Sci-Hub. No ResearchGate file download.
No Wayback. JS/WAF challenges were recorded and not bypassed.

| URL | Result |
|---|---|
| `https://doi.org/10.1351/pac200577040683` | HTTP 302 → De Gruyter Brill HTML; that host HTTP 202, `x-amzn-waf-action: challenge`, 0 bytes |
| `https://www.degruyter.com/document/doi/10.1351/pac200577040683/pdf` | HTTP 301 → Brill PDF URL; HTTP 202 WAF challenge, 0 bytes |
| `https://www.degruyterbrill.com/document/doi/10.1351/pac200577040683/pdf` | HTTP 202 WAF challenge, 0 bytes |
| `https://www.degruyterbrill.com/document/doi/10.1351/pac200577040683/html` | HTTP 202 WAF challenge, 0 bytes |
| `https://old.iupac.org/publications/pac/2005/pdf/7704x0683.pdf` | HTTP 403 Cloudflare JS challenge (`cf-mitigated: challenge`), 5636 B HTML |
| `https://publications.iupac.org/publications/pac/2005/pdf/7704x0683.pdf` | HTTP 403 Cloudflare JS challenge, 5645 B HTML |
| `https://old.iupac.org/publications/pac/2005/7704/7704x0683.html` | HTTP 403 Cloudflare JS challenge |
| `https://publications.iupac.org/pac/77/4/0683/index.html` | HTTP 403 Cloudflare JS challenge |
| `https://api.unpaywall.org/v2/10.1351/pac200577040683` | HTTP 200; `is_oa: false`, no `url_for_pdf` |
| `https://api.crossref.org/works/10.1351/pac200577040683` | HTTP 200; no `license`; PDF link is the Brill URL above |
| `https://api.archives-ouvertes.fr/search/?q=doi:10.1351/pac200577040683` | HTTP 200; no HAL record |
| `https://www.osti.gov/biblio/?q=10.1351/pac200577040683` | HTTP 404 (no OSTI full text) |
| `https://www.nist.gov/publications/high-temperature-mass-spectrometry-accuracy-method-and-influence-ionization-cross` | HTTP 200 HTML abstract only; no PDF |

IUPAC archive pages advertise "Download full text of the report [pdf
file - 567 kB]" / "Full text - pdf 566 kB". Those file URLs are the
Cloudflare-403 rows above.

## Decode substitute

No `pdftotext -layout` (no PDF bytes). Publisher PDF *text* for
Tables 1–5 captions and substantial table bodies, plus Fig. 1–2
captions, was available via the official De Gruyter PDF URL through
the hunt's web-research tooling (not a saved PDF). Transcriptions:
`docs-private/research/2026-09-06-hunts/RH-46/kems-014-drowart-2005/`.
Figure `~` grids were not used.
