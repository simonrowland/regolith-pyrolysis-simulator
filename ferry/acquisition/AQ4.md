# AQ4 — open-access acquisition hunt

**Run date:** 2026-09-22 (Toronto)  
**Input:** next 28 `*.sidecar.yaml` files after AQ3 in `from-main-B4-20260923T031628Z/not-obtained/`, lexicographically sorted (AQ3 ended at `kems-125-nakamura-1970`).  
**Range:** `kems-126-wagner-1971-p481` through `kems-158-storms-1987-p217` (**28** sidecars confirmed).  
**Policy:** legal OA only (publisher OA, arXiv, NASA NTRS, OSTI, NIST, author/institutional repo, LPI, J-STAGE open archive, Gallica/BnF public-domain). No Sci-Hub, shadow libraries, or GitHub PDF dumps were used as acquisition sources.

## Counts

- Sidecars reviewed: **28**
- Exact-work legal OA full-text source located: **3** (`kems-133` Elsevier bronze AM; `kems-136` Gallica domaine public; `kems-140` hybrid CC BY 4.0)
- Exact-work legal OA PDF files acquired into `/workspace/ferry-inbox/acquired/`: **1** (`kems-133-costa-2017.pdf`)
- OA HTML/data-only (not the requested article PDF): **0** (Heck Mendeley/Elsevier mmc xlsx already noted in sidecar; not counted as article PDF)
- No exact-work legal OA full text verified: **25**
- Sidecars with `present: no` / no on-disk PDF in input: **28** (all)

## Results, in sorted input order

| # | Sidecar | found? | URL | licence / why legal | relevance |
|---:|---|---|---|---|---:|
| 1 | `kems-126-wagner-1971-p481` | no | High Temp. Sci. 3:481 (1971); no DOI. Crossref/OpenAlex bibliographic search: no accepted work / no OA PDF. | No official OA PDF located | 3 |
| 2 | `kems-128-martin-garin-1979` | no | Publisher: <https://doi.org/10.1016/0022-5088(79)90217-0> (Elsevier J. Less-Common Met.). Unpaywall/OpenAlex: closed. HAL: no deposit. | Elsevier TDM only; not OA | 3 |
| 3 | `kems-129-alcock-1969` | no | Publisher: <https://doi.org/10.1016/0001-6160(69)90103-5> (Elsevier Acta Metall.). Unpaywall/OpenAlex: closed. | Elsevier TDM only; not OA | 3 |
| 4 | `kems-130-gingerich-1988` | no | Publisher: <https://doi.org/10.1016/0022-5088(88)90059-8> (Elsevier J. Less-Common Met.). Unpaywall/OpenAlex: closed. | Elsevier TDM only; not OA | 3 |
| 5 | `kems-131-raychaudhuri-1971` | no | Northwestern Ph.D. thesis (1971); no DOI. NU Arch keyword hits are **different** modern dissertations; Arch IR previously 403; ProQuest not OA. | Institutional/login wall; no OA thesis PDF | 3 |
| 6 | `kems-133-costa-2017` | **yes; PDF acquired** | Unpaywall bronze AM: Elsevier accepted-manuscript CDN <https://ars.els-cdn.com/content/image/1-s2.0-S0019103516301798-am.pdf> (DOI <https://doi.org/10.1016/j.icarus.2017.02.006>). ScienceDirect AM HTML/PDF paths Cloudflare-403; NTRS DOI search empty; HAL/arXiv/EuropePMC empty. LPSC 2016 abstract is a **different** document (not filed as this source). | Elsevier open-access user license 1.0 on AM (Crossref content-version `am` from 2018-02-23); VoR remains TDM. Legal bronze OA AM PDF retrieved. | 3 |
| 7 | `kems-134-yamaji-1972` | no | Publisher: <https://doi.org/10.1007/bf02647679> (Springer Met. Trans.). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 8 | `kems-135-erdelyi-1979` | no | Crossref DOI located this hunt: <https://doi.org/10.1007/bf02812008> (Metall. Trans. A 10:1437–1443, 1979). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 9 | `kems-136-perakis-1973` | **OA lead; PDF fetch blocked** | Official BnF Gallica CRAS Sér. C 1973 ARK <https://gallica.bnf.fr/ark:/12148/bpt6k6239070z> (issue run containing p.1513). Gallica marks 1973 Sér. C as domaine public. Prior `f1.pdf` → altcha; this hunt: Gallica endpoints unreachable (HTTP 000) from box. HAL: numFound 0. | BnF Gallica public-domain digitization (legal OA source class); PDF not retrieved | 3 |
| 10 | `kems-140-heck-2025` | **OA lead; PDF fetch blocked** | Hybrid OA VoR: <https://doi.org/10.1016/j.gca.2025.05.007> (Crossref license CC BY 4.0; Unpaywall `is_oa=true` hybrid). LSU Digital Commons landing <https://repository.lsu.edu/geo_pubs/2208/> states “This document is currently not available here.” ScienceDirect/CDN PDF paths Cloudflare-403 / IP-blocked interstitial / CDN 400 NONAUTHATTACH. Mendeley Data 10.17632/hpmts74ksr.1 is **tables only** (not the article). | CC BY 4.0 VOR (legal OA); typeset PDF not retrieved | 3 |
| 11 | `kems-141-said-1981` | no | Publisher: <https://doi.org/10.1515/ijmr-1981-720512> (De Gruyter). Unpaywall/OpenAlex: closed. | De Gruyter copyright; not OA | 3 |
| 12 | `kems-142-hino-1987` | no | Publisher: <https://doi.org/10.1007/bf02658443> (Springer Met. Trans. B). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 13 | `kems-143-storms-1987` | no | Publisher: <https://doi.org/10.1016/0022-5088(87)90010-5> (Elsevier). Unpaywall/OpenAlex: closed. | Elsevier TDM only; not OA | 3 |
| 14 | `kems-144-hager-1970` | no | Publisher: <https://doi.org/10.1007/bf02811550> (Springer Met. Trans.). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 15 | `kems-145-neckel-1969` | no | Publisher: <https://doi.org/10.1002/bbpc.19690730221> (Wiley Berichte). Unpaywall/OpenAlex: closed. | Wiley copyright; not OA | 3 |
| 16 | `kems-146-hager-1973` | no | Publisher: <https://doi.org/10.1007/bf02669379> (Springer Met. Trans.). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 17 | `kems-147-neckel-1969-px` | no | First Intern. Conf. on Calorimetry and Thermodynamics, Warsaw (1969); no DOI. Crossref/OpenAlex: unresolved; no official OA proceedings PDF. | No official OA PDF located | 3 |
| 18 | `kems-148-tomiska-laszlo-1977` | no | Publisher: <https://doi.org/10.1515/ijmr-1977-681107> (De Gruyter). Unpaywall/OpenAlex: closed. | De Gruyter copyright; not OA | 3 |
| 19 | `kems-149-bartosik-1971` | no | Northwestern Ph.D. thesis (1971); no DOI. NU Arch/ProQuest not OA; DAI B 32(6):3478 bibliographic only. | Institutional/login wall; no OA thesis PDF | 3 |
| 20 | `kems-150-tomiska-1990` | no | Publisher: <https://doi.org/10.1515/ijmr-1990-811210> (De Gruyter). Unpaywall/OpenAlex: closed. | De Gruyter copyright; not OA | 3 |
| 21 | `kems-151-tomiska-kopecky-1990` | no | Publisher: <https://doi.org/10.1002/bbpc.19900940110> (Wiley Berichte). Unpaywall/OpenAlex: closed. | Wiley copyright; not OA | 3 |
| 22 | `kems-152-jones-1970` | no | Publisher: <https://doi.org/10.1007/bf02811549> (Springer Met. Trans.). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 23 | `kems-153-bergman-1978` | no | High Temp. High Press. 10:581 (1978); no DOI. Old City Publishing HTHP electronic archive returns bot/loader interstitial (not OA full text). | Publisher IP/bot wall; not OA | 3 |
| 24 | `kems-154-howard-1978` | no | Publisher: <https://doi.org/10.1007/bf02673429> (Springer Met. Trans. B). Unpaywall/OpenAlex: closed. | Springer TDM/VOR; not OA | 3 |
| 25 | `kems-155-storms-1978` | no | Publisher: <https://doi.org/10.1021/j100490a014> (ACS J. Phys. Chem.). Unpaywall/OpenAlex: closed. OSTI biblio <https://www.osti.gov/biblio/5253638> has **no** full-text servlet. | ACS copyright; OSTI abstract/biblio only | 3 |
| 26 | `kems-156-storms-1977` | no | Publisher: <https://doi.org/10.1021/j100519a008> (ACS). Unpaywall/OpenAlex: closed. Nearby OSTI PDF `purl/4365011` is **Gilles & Pollock 1953 AECU-2894** (different work; not acquired as substitute). | ACS copyright; not OA | 3 |
| 27 | `kems-157-storms-szklarz-1987` | no | Publisher: <https://doi.org/10.1016/0022-5088(87)90484-x> (Elsevier). Unpaywall/OpenAlex: closed. | Elsevier TDM only; not OA | 3 |
| 28 | `kems-158-storms-1987-p217` | no | Publisher: <https://doi.org/10.1016/0022-5088(87)90483-8> (Elsevier). Unpaywall/OpenAlex: closed. | Elsevier TDM only; not OA | 3 |

**Relevance scale:** 1 = peripheral; 2 = related thermochem/vaporisation; 3 = direct KEMS / activity / oxide-melt or alloy thermochemistry (Kato-table / KEMS-langmuir experimental sources → **3**).

## Valid OA PDFs in `acquired/`

```text
kems-133-costa-2017.pdf
```

- **Source:** Elsevier accepted-manuscript CDN `1-s2.0-S0019103516301798-am.pdf` (bronze OA AM for DOI 10.1016/j.icarus.2017.02.006).
- **SHA256:** `35a212a22bcb8f7978ab3317e89532760e7cd73241d53194910a5f54cfce5a64`
- **Size:** 1498618 bytes
- **Identity check:** `pdftotext` page 1–2 shows Costa/Jacobson/Fegley title + Elsevier user-license banner (“© 2017 published by Elsevier. This manuscript is made available under the Elsevier user license…”).
- **Note:** No Git/GitHub PDF was used as an acquisition source. No PDFs were git-committed.

## Search notes (methods)

- Unpaywall v2 for all 21 known DOIs (+ newly resolved Erdelyi `10.1007/bf02812008`) → only `kems-133` bronze and `kems-140` hybrid CC BY are `is_oa=true`; remainder closed.
- OpenAlex (mailto) → same OA pair; all other DOIs `is_oa=false` / no repository PDF locations.
- Semantic Scholar → rate-limited (429) for several DOIs; sampled closed results consistent with Unpaywall.
- Elsevier CDN → Costa AM PDF HTTP 200 `%PDF-`; Heck main/am CDN 400/404; ScienceDirect HTML/PDF Cloudflare 403 / IP-blocked interstitial.
- LSU Digital Commons → Heck metadata landing only (“not available here”).
- Gallica/BnF → Perakis 1973 Sér. C domaine-public lead; PDF download blocked (altcha / connectivity).
- HAL → Martin-Garin/Perakis queries numFound 0.
- OSTI → Storms La–B biblio 5253638 without full text; Mo–B servlet 4365011 is identity-mismatched 1953 Gilles/Pollock report (not used).
- NTRS → Costa 2017 DOI/title search empty (related 2015 olivine KEMS is different work / already elsewhere).
- Northwestern Arch → Raychaudhuri/Bartosik 1971 theses not present as public OA deposits.
- Old City Publishing HTHP → bot/loader wall for Bergman 1978 volume-10 probes.
- Crossref → resolved Erdelyi 1979 DOI `10.1007/bf02812008`; no DOI for Wagner 1971 High Temp. Sci. / Neckel–Sodeck Warsaw 1969 / Bergman HTHP 1978.
