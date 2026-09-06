# kems-138-bischof-2023 — PDF sidecar

Corpus-ID naming: stem `kems-138-bischof-2023` matches
`data/literature/extracts/kems-138-bischof-2023.yaml`. Hunt id
W2-kems-138-bischof-2023. Distinct from `kems-137-bischof-2023`
(GCA 10.1016/j.gca.2023.08.027, An-Di melt activities). This
source is the Calphad pure-oxide KEMS paper
DOI 10.1016/j.calphad.2022.102507. Topic directory
`99-kems-langmuir/`.

## Citation

Bischof, L., Sossi, P. A., Sergeev, D., Müller, M. & Schmidt, M. W.
(2023). Quantification of thermodynamic properties for vaporisation
reactions above solid Ga2O3 and In2O3 by Knudsen Effusion Mass
Spectrometry. *Calphad* **80**:102507.
https://doi.org/10.1016/j.calphad.2022.102507

## DOI

`10.1016/j.calphad.2022.102507`

Resolver (`curl -sI` 2026-09-06): HTTP 302 →
`https://linkinghub.elsevier.com/retrieve/pii/S0364591622001109`.
DOI is located; not "none located".

## Publisher

Elsevier BV, *Calphad*. Print ISSN 0364-5916, electronic ISSN
1873-2984. Volume 80, article 102507 (March 2023). PII
S0364591622001109.

## Licence

Crossref license objects:

- Elsevier TDM 1.0 / TDM-rep on the version of record
- Creative Commons Attribution 4.0
  (`content-version: vor`, start 2022-11-25,
  URL `http://creativecommons.org/licenses/by/4.0/`)

Unpaywall 2026-09-06: `is_oa: true`, `oa_status: hybrid`,
`license: cc-by`.

`licence open` (hybrid CC BY) and `PDF on disk` are separate facts.
ScienceDirect returned Cloudflare 403; that is a JS challenge, not
treated as a paywall. The ETH Research Collection bitstream of the
same CC-BY VOR was served without a challenge.

## Access

`access: open`

Hybrid OA VOR (CC BY 4.0). HAL/arXiv/PMC/EuropePMC empty.
Supplement (mmc1) not present on Elsevier CDN.

## File

| Field | Value |
|---|---|
| intended_path | `docs/references/pdfs/99-kems-langmuir/kems-138-bischof-2023.pdf` |
| present | yes |
| sha256 | `cd9ab2b731850400e2562fd4b915e9f9737ecac30fdf4944ca2d1cd724a17485` |
| file_size_bytes | 13983403 |
| retrieved_date | 2026-09-06 |
| retrieved_url | `https://www.research-collection.ethz.ch/server/api/core/bitstreams/3a0c0dca-49dc-4dd9-b643-8f17249b164f/content` |
| version | publishedVersion (ETH CC-BY typeset PDF) |

Accepted manuscript (filed in corpus `raw/` only): Elsevier CDN
`https://ars.els-cdn.com/content/image/1-s2.0-S0364591622001109-am.pdf`
sha256 `b2efe1e1d7e8a323e6f456ef40877f04049fe8ea3bb585fa984ca8105c8f02ba`
(2675325 bytes).

## Retrieval attempts (2026-09-06)

Official OA routes only (publisher, ETH institutional OA of the
CC-BY VOR, HAL, arXiv, PMC). No paywall circumvention. No Sci-Hub.
JS/WAF challenges recorded and not bypassed.

| URL | Result |
|---|---|
| `https://doi.org/10.1016/j.calphad.2022.102507` | HTTP 302 → linkinghub Elsevier PII |
| `https://www.sciencedirect.com/science/article/pii/S0364591622001109` | HTTP 403 Cloudflare JS challenge |
| `https://ars.els-cdn.com/content/image/1-s2.0-S0364591622001109-main.pdf` | HTTP 400 NONAUTHATTACH |
| `https://ars.els-cdn.com/content/image/1-s2.0-S0364591622001109-am.pdf` | HTTP 200 AM PDF 2675325 bytes |
| ETH bitstream (handle 20.500.11850/592445 ORIGINAL) | HTTP 200 VOR PDF 13983403 bytes |
| JuSER `.../1-s2.0-S0364591622001109-main.pdf` | HTTP 200 HTML fast-challenge; not bypassed |
| HAL / arXiv / PMC / EuropePMC | no record |
| Elsevier CDN mmc1.pdf | HTTP 404 |

## Decode

`pdftotext -layout`, `pdfinfo` (21 pages, born-digital),
`pdfimages -list`, `pdftoppm` 150 dpi of table pages.
Tables 1–13 transcribed with provenance. Figures 1–15 figure-only.
