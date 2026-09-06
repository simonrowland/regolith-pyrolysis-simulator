# kems-003-pound-1972 — PDF sidecar

Corpus-ID naming: stem `kems-003-pound-1972` matches
`data/literature/extracts/kems-003-pound-1972.yaml`. Hunt id RH-67
(same DOI; do not land `rh67-pound-1972` as a second source_id).
Aliases already in INDEX: `REF-040`, `pound-1972-cr-langmuir-knudsen`,
`pound-1972-mccabe-cr`. Topic directory `99-kems-langmuir/`.

## Citation

Pound, G. M. (1972). Selected values of evaporation and condensation
coefficients for simple substances. *Journal of Physical and Chemical
Reference Data* **1**:135–146. https://doi.org/10.1063/1.3253096

## DOI

`10.1063/1.3253096`

## Publisher

AIP Publishing, *Journal of Physical and Chemical Reference Data*
(NIST Standard Reference Data program). Print ISSN 0047-2689,
electronic ISSN 1529-7845. Volume 1, issue 1, pages 135–146.

Crossref resource URL:
`https://pubs.aip.org/jpr/article/1/1/135/241015/Selected-Values-of-Evaporation-and-Condensation`

Crossref PDF link:
`https://pubs.aip.org/aip/jpr/article-pdf/1/1/135/19299694/135_1_online.pdf`

## Licence

No Creative Commons (or other machine-readable) licence object in
Crossref for this DOI (`license` field absent). Unpaywall 2026-09-06:
`is_oa: false`, `oa_status: closed`, `has_repository_copy: false`,
`oa_locations: []`.

`licence open` and `PDF on disk` are separate facts. This sidecar does
not treat Unpaywall-closed + missing CC as a licence-open fetch grant.

## Access

`access: held`

PDF bytes were already in this worktree (and corpus `raw/`) from the
prior KEMS staging campaign. Official AIP HTML/PDF routes answered
Cloudflare JS challenge (HTTP 403, `cf-mitigated: challenge`) on
2026-09-06 and were **not** bypassed.

## File

| Field | Value |
|---|---|
| intended_path | `docs/references/pdfs/99-kems-langmuir/kems-003-pound-1972.pdf` |
| present | yes |
| sha256 | `e9d7aa7924e4b6c8982e9dfc75cf3bf75a665d2c95ef55fe2424572120c5e5cb` |
| file_size_bytes | 910487 |
| retrieved_date | 2026-09-04 (bytes already on disk; sidecar filled 2026-09-06) |
| retrieved_url | not re-fetched; AIP PDF URL is Cloudflare-403 |

## Retrieval attempts (2026-09-06)

Official OA routes only. No paywall circumvention. No Sci-Hub.
JS/WAF challenges recorded and not bypassed. Existing on-disk PDF used.

| URL | Result |
|---|---|
| `https://doi.org/10.1063/1.3253096` | HTTP 302 → AIP HTML; that host HTTP 403 Cloudflare JS challenge |
| `https://pubs.aip.org/jpr/article/1/1/135/241015/Selected-Values-of-Evaporation-and-Condensation` | HTTP 403 Cloudflare JS challenge |
| `https://pubs.aip.org/aip/jpr/article-pdf/1/1/135/11976222/135_1_online.pdf` | HTTP 403 Cloudflare JS challenge |
| `https://pubs.aip.org/aip/jpr/article-pdf/1/1/135/19299694/135_1_online.pdf` | Crossref `link` PDF; not fetched (challenge class) |
| `https://api.unpaywall.org/v2/10.1063/1.3253096` | HTTP 200; `is_oa: false`, no `url_for_pdf` |
| `https://api.crossref.org/works/10.1063/1.3253096` | HTTP 200; no `license` |
| `https://api.archives-ouvertes.fr/search/?q=doi:10.1063/1.3253096` | no HAL record |
| `https://www.nist.gov/publications/selected-values-evaporation-and-condensation-coefficients-simple-substances` | HTTP 404 |

## Decode

Image-only scan (CCITT strips, 12 pages, letter). `pdftotext -layout`
empty. Tables 1–3 landscape; digitised from 300 dpi rotate-90 renders.
Transcriptions: `docs-private/research/2026-09-06-hunts/RH-67/kems-003-pound-1972/`
and corpus `tables/kems-003-pound-1972/`.
