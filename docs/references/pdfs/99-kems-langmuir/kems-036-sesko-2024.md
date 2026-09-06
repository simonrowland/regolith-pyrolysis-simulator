# kems-036-sesko-2024 — PDF sidecar

Corpus-ID naming: stem `kems-036-sesko-2024` matches
`data/literature/extracts/kems-036-sesko-2024.yaml`. Hunt id RH-50
(same DOI; do not land `rh50-sesko-2024` as a second source_id).
Topic directory `99-kems-langmuir/` is where this extract's inventory
lives.

## Citation

Šeško, R., Lamboley, K., Cutard, T., Grill, L., Reiss, P. & Cowley, A.
(2024). Oxygen production by solar vapor-phase pyrolysis of lunar
regolith simulant. *Acta Astronautica* 224:215–225.
https://doi.org/10.1016/j.actaastro.2024.08.009

## DOI

`10.1016/j.actaastro.2024.08.009`

## Publisher

Elsevier BV, *Acta Astronautica* (International Academy of Astronautics).
PII `S0094576524004399`.

## Licence

CC BY 4.0 (Creative Commons Attribution 4.0 International).

Crossref VOR licence URL: http://creativecommons.org/licenses/by/4.0/
(start 2024-08-09, delay-in-days 0, content-version `vor`).

HAL `licence_s`: https://creativecommons.org/licenses/by/4.0/

Publisher notice (as printed on the article):
"© 2024 The Author(s). Published by Elsevier Ltd on behalf of IAA.
This is an open access article under the CC BY license
(http://creativecommons.org/licenses/by/4.0/)."

## Access

`access: open` (hybrid OA, publishedVersion).
`paywalled: false`

## File

| Field | Value |
|---|---|
| intended_path | `docs/references/pdfs/99-kems-langmuir/kems-036-sesko-2024.pdf` |
| present | no |
| sha256 | *(absent — PDF bytes not obtained)* |
| file_size_bytes | *(absent — PDF bytes not obtained)* |
| retrieved_date | 2026-09-05 |
| retrieved_url | *(none succeeded)* |

## Retrieval attempts (2026-09-05)

Official OA routes only. No paywall circumvention. No Sci-Hub.
`access-status.yaml` acquisition_policy (`paywalls_bypassed: false`,
`bot_protection_bypassed: false`) observed.

| URL | Result |
|---|---|
| `https://doi.org/10.1016/j.actaastro.2024.08.009` | OA landing confirmed via Unpaywall/Crossref/OpenAlex |
| `https://ars.els-cdn.com/content/image/1-s2.0-S0094576524004399-main.pdf` | HTTP 400 |
| `https://www.sciencedirect.com/science/article/pii/S0094576524004399/pdfft?full=true` | HTTP 403 Cloudflare |
| `https://www.sciencedirect.com/science/article/pii/S0094576524004399/pdfft` | HTTP 403 Cloudflare |
| `https://api.elsevier.com/content/article/doi/10.1016/j.actaastro.2024.08.009?httpAccept=application/pdf` | HTTP 406 (API key required) |
| `https://imt-mines-albi.hal.science/hal-04677458v1/file/Oxygen-production-solar-vapor-phase-pyrolysis-lunar-regolith-simulant.pdf` | HTTP 503 / Anubis JS challenge |
| `https://hal.science/hal-04677458v1/file/Oxygen-production-solar-vapor-phase-pyrolysis-lunar-regolith-simulant.pdf` | HTTP 503 |
| `https://insa-toulouse.hal.science/hal-04677458v1/file/Oxygen-production-solar-vapor-phase-pyrolysis-lunar-regolith-simulant.pdf` | Anubis HTML (12 612 B), not PDF |
| Chrome headless HAL file URL | HTTP 503 Service Unavailable |
| Chrome headless ScienceDirect article | Cloudflare challenge HTML (833 175 B), not article/PDF |

Unpaywall `url_for_pdf` for the publisher location is null. Repository
PDF is the HAL file above (Unpaywall labels it `submittedVersion`; HAL
records licence CC BY 4.0 and "Publication funded by an institution").

Prior campaign note (not reused as a download):
`docs-private/research/2026-06-12-vpr-sources/fetch-status.local.txt`
already recorded the same HAL Anubis / ScienceDirect 403 / Elsevier 406
block for this DOI.

## Decode substitute

A prior HAL-markdown conversion of this article is in
`docs-private/research/2026-06-12-vpr-sources/sesko_2024_vapor_phase_pyrolysis.md`
and was copied to
`docs-private/research/2026-09-06-data-pilot/rh50/source-hal-markdown.md`
for the RH-50 pilot. That is **not** the publisher PDF and is **not** a
`pdftotext -layout` dump.
