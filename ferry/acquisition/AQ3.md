# AQ3 — open-access acquisition hunt

**Run date:** 2026-09-22 (Toronto)  
**Input:** next 28 `*.sidecar.yaml` files after AQ2 in `from-main-B4-20260923T031628Z/not-obtained/`, lexicographically sorted (AQ2 ended at `kems-084-kubaschewski-1977`).  
**Range:** `kems-085-tomiska-1985` through `kems-125-nakamura-1970` (**28** sidecars confirmed).  
**Policy:** legal OA only (publisher OA, arXiv, NASA NTRS, OSTI, NIST, author/institutional repo, LPI, J-STAGE open archive). No Sci-Hub, shadow libraries, or GitHub PDF dumps were used as acquisition sources.

## Counts

- Sidecars reviewed: **28**
- Exact-work legal OA full-text source located: **1** (`kems-103` Persée free archive)
- Exact-work legal OA PDF files acquired into `/workspace/ferry-inbox/acquired/`: **1** (`kems-103-fraser-1983.pdf`)
- OA HTML/data-only (not counted as PDF acquisition): **0** additional beyond the Persée case (HTML was the discovery path; PDF staged from free page renders)
- No exact-work legal OA full text verified: **27**
- Sidecars with `present: no` in input: **28** (all)

## Results, in sorted input order

| # | Sidecar | found? | URL | licence / why legal | relevance |
|---:|---|---|---|---|---:|
| 1 | `kems-085-tomiska-1985` | no | Publisher: <https://doi.org/10.1515/ijmr-1985-760803> (De Gruyter / Z. Metallkd.). Unpaywall/Semantic Scholar: closed. | De Gruyter copyright; not OA | 3 |
| 2 | `kems-086-tomiska-neckel-1985` | no | Publisher: <https://doi.org/10.1002/bbpc.19850891017> (Wiley Berichte). Unpaywall/Semantic Scholar: closed. | Wiley copyright; not OA | 3 |
| 3 | `kems-089-kato-1973` | no | Proc. 4th Int. Conf. Vac. Metall., ISIJ (1973), §1 p.35; no DOI. J-STAGE global search: no matching OA article. CiNii previously WAF-challenged (sidecar). | No official OA PDF located | 3 |
| 4 | `kems-090-levin-1979` | no | Zh. Fiz. Khim. 53:2846 (1979); no DOI. MathNet/RAS chemistry archive does not host this journal. HAL/OSTI: no exact full text. | No official OA PDF located | 3 |
| 5 | `kems-091-jhonston-1980` | no | Old City Publishing HTHP 12(3) 1980, Johnston & Palmer p.261; issue contents <https://www.oldcitypublishing.com/journals/hthp-electronic-archive-home/hthp-electronic-archive-issue-contents/hthp-volume-12-number-3-1980/>; PDF id 5123 returns institutional/bot wall (not OA). | Publisher IP/bot wall; not OA | 3 |
| 6 | `kems-092-zaitsev-korolyov-1991` | no | Publisher: <https://doi.org/10.1016/s0021-9614(05)80053-9> (Elsevier JCT). Unpaywall/Semantic Scholar: closed. | Elsevier TDM only; not OA | 3 |
| 7 | `kems-094-tomiska-1979` | no | Publisher: <https://doi.org/10.1002/bbpc.19790831016> (Wiley Berichte). Unpaywall/Semantic Scholar: closed. | Wiley copyright; not OA | 3 |
| 8 | `kems-096-zaitsev-zemchenko-1990` | no | Publisher: <https://doi.org/10.1016/0021-9614(90)90029-p> (Elsevier JCT). Unpaywall/Semantic Scholar: closed. | Elsevier TDM only; not OA | 3 |
| 9 | `kems-098-wagner-1972` | no | Publisher: <https://doi.org/10.1007/bf02680583> (Springer Met. Trans.). Unpaywall/Semantic Scholar: closed. Nearby NTRS Knudsen-cell method notes are **different works**. | Springer TDM/VOR; not OA | 3 |
| 10 | `kems-099-german-1972` | no | Publisher: <https://doi.org/10.1007/bf02652848> (Springer Met. Trans.). Unpaywall/Semantic Scholar: closed. Shadow-library hits ignored. | Springer TDM/VOR; not OA | 3 |
| 11 | `kems-100-fraser-1982` | no | Publisher: <https://doi.org/10.1016/0016-7037(82)90157-0> (Elsevier GCA). Unpaywall/Semantic Scholar: closed. Oxford ORA record <https://ora.ox.ac.uk/objects/uuid:255e1c1f-f1d6-46c4-b39c-9da29c2bfc4e> is **metadata/abstract only** (no downloadable PDF files; “PDF can now be made available” request form). | Elsevier copyright; ORA not a full-text deposit | 3 |
| 12 | `kems-101-tomiska-1993` | no | Publisher: <https://doi.org/10.1016/0022-3093(93)90211-f> (Elsevier JNCS). Unpaywall/Semantic Scholar: closed. | Elsevier TDM only; not OA | 3 |
| 13 | `kems-102-tomiska-kopecky-1993` | no | Publisher: <https://doi.org/10.1515/ijmr-1993-840907> (De Gruyter). Unpaywall/Semantic Scholar: closed. | De Gruyter copyright; not OA | 3 |
| 14 | `kems-103-fraser-1983` | **yes; PDF acquired** | Persée free article: <https://www.persee.fr/doc/bulmi_0180-9210_1983_act_106_1_7673> (DOI <https://doi.org/10.3406/bulmi.1983.7673>). ORA twin <https://ora.ox.ac.uk/objects/uuid:957191fb-0e1e-40d0-aa53-bb7a66d98eb1> is metadata-only. Official `docAsPDF` URL returns **HTTP 403 altcha** (not bypassed). Acquired PDF assembled from Persée free `renderPage` page images (pp. 111–117). | Persée HTML meta `DC.rights=free`; Persée CGU §5 personal/scientific use. Publisher OA portal (legal). | 3 |
| 15 | `kems-104-wagner-1971` | no | Adv. Mass Spectrom. 5:388 (1971); no DOI. Internet Archive volume query empty; OpenAlex/Crossref unresolved for exact work. | No official OA PDF located | 3 |
| 16 | `kems-106-tomiska-1977` | no | Publisher: <https://doi.org/10.1515/ijmr-1977-680506> (De Gruyter). Unpaywall/Semantic Scholar: closed. | De Gruyter copyright; not OA | 3 |
| 17 | `kems-107-choudary-1975` | no | Publisher: <https://doi.org/10.1007/bf02913824> (Springer Met. Trans. B). Unpaywall/Semantic Scholar: closed. | Springer TDM/VOR; not OA | 3 |
| 18 | `kems-108-timberg-1981` | no | Publisher: <https://doi.org/10.1007/bf02654460> (Springer Met. Trans. B). Unpaywall/Semantic Scholar: closed. | Springer TDM/VOR; not OA | 3 |
| 19 | `kems-109-erdelyi-1978` | no | Crossref DOI located this hunt: <https://doi.org/10.1515/ijmr-1978-690802> (De Gruyter / Z. Metallkd. 69:506). Unpaywall: closed. | De Gruyter copyright; not OA | 3 |
| 20 | `kems-110-nunoue-1988` | no | Publisher: <https://doi.org/10.1007/bf02657752> (Springer Met. Trans. B). Unpaywall/Semantic Scholar: closed. J-STAGE: no OA twin. | Springer TDM/VOR; not OA | 3 |
| 21 | `kems-113-tomiska-1989` | no | Publisher: <https://doi.org/10.1515/ijmr-1989-801207> (De Gruyter). Unpaywall/Semantic Scholar: closed. | De Gruyter copyright; not OA | 3 |
| 22 | `kems-115-tomiska-krajnik-1989` | no | Publisher: <https://doi.org/10.1515/ijmr-1989-800409> (De Gruyter). Unpaywall/Semantic Scholar: closed. | De Gruyter copyright; not OA | 3 |
| 23 | `kems-117-wagner-stpierre-1972` | no | Publisher: <https://doi.org/10.1007/bf02652855> (Springer Met. Trans.). Unpaywall/Semantic Scholar: closed. | Springer TDM/VOR; not OA | 3 |
| 24 | `kems-121-cuthill-1969` | no | Proc. Int. Conf. Mass Spectrometry, Ljubljana (1969); no DOI. No official OA PDF on OSTI/NTRS/OpenAlex. | No official OA PDF located | 3 |
| 25 | `kems-122-cameresi-1967` | no | Ric. Sci. 37:1092 (1967); no DOI. CNR catalogue holds print volumes only; no OA PDF. NTRS cites it as a reference in unrelated Knudsen-cell notes (not this article). | No official OA PDF located | 3 |
| 26 | `kems-123-golonka-1979` | no | Publisher: <https://doi.org/10.1179/030716979803276084> (Taylor & Francis / Metals Technology). Unpaywall/Semantic Scholar: closed. | Copyright IOM3/T&F; not OA | 3 |
| 27 | `kems-124-howard-1989` | no | Publisher: <https://doi.org/10.1007/bf02670189> (Springer Met. Trans. B). Unpaywall/Semantic Scholar: closed. | Springer TDM/VOR; not OA | 3 |
| 28 | `kems-125-nakamura-1970` | no | Publisher: <https://doi.org/10.1007/bf03038401> (Springer Met. Trans.). Unpaywall/Semantic Scholar: closed. J-STAGE: no OA twin. | Springer TDM/VOR; not OA | 3 |

**Relevance scale:** 1 = peripheral; 2 = related thermochem/vaporisation; 3 = direct KEMS / activity / oxide-melt or alloy thermochemistry (all 28 are Kato-table experimental KEMS/activity sources → **3**).

## Valid OA PDFs in `acquired/`

```text
kems-103-fraser-1983.pdf
```

- **Source:** Persée free page images (`renderPage`…`_710.jpg` for Bulletin de Minéralogie 106:111–117), packaged as a 7-page PDF.
- **SHA256:** `74c753b9adae88e1439052a66995eb64ffb928d4db062bbd1183d762beacdc4a`
- **Size:** 838823 bytes
- **Note:** Official Persée `docAsPDF` endpoint was HTTP 403 (altcha bot challenge) and was not bypassed. No Git/GitHub PDF was used as an acquisition source.

## Search notes (methods)

- Unpaywall v2 for all 21 known DOIs (plus newly resolved Erdelyi `10.1515/ijmr-1978-690802`) → all `is_oa=false` / `closed` except Persée work still reported closed by Unpaywall despite publisher `DC.rights=free`.
- Semantic Scholar graph for all 21 DOIs → all closed / empty `openAccessPdf`.
- Oxford ORA → bibliographic records for Fraser 1982 (`kems-100`) and Fraser 1983 (`kems-103`); both lack downloadable file objects.
- Persée → free HTML + free page-image renders for `kems-103`; official PDF altcha-blocked.
- HAL API → no deposits for Fraser/Tomiska/Levin/Cameresi targets.
- Old City Publishing HTHP → IP/bot wall for `kems-091` PDF id 5123.
- J-STAGE global search → no OA twins for Kato–Minami 1973, Nunoue 1988, or Nakamura 1970.
- OSTI / NTRS → no exact full-text hits; NTRS Knudsen-cell configuration papers are identity-mismatched.
- De Gruyter publisher PDF URLs → HTML/challenge (HTTP 202), not `%PDF-` bodies.
- Digizeitschriften → no Z. Metallkunde OA archive hit for Tomiska targets.
- Internet Archive → no freely downloadable *Advances in Mass Spectrometry* vol. 5 for `kems-104`.
