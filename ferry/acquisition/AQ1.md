# AQ1 — open-access acquisition hunt retry

**Run date:** 2026-09-22 (Toronto)  
**Input:** first 28 `*.sidecar.yaml` files in `from-main-B4-20260923T031628Z/not-obtained/`, lexicographically sorted.  
**Policy:** legal OA only. No Git/GitHub repository PDFs, Sci-Hub, shadow-library copies, or paywalled downloads were used.

## Counts

- Sidecars reviewed: **28**
- Sidecars marked `present: yes`: **21**
- Sidecars marked `present: no`: **7**
- Exact-work legal OA full-text source located: **16**
- Exact-work legal OA PDF files currently present in `/workspace/ferry-inbox/acquired/`: **13**
- OA HTML/data source only (not the requested report/PDF): **1** (`kems-050`)
- No exact-work legal OA full text verified: **9**
- Unrelated OA misfile excluded: **1** (`gal-2018-pnas-calcium-storage`)

For the seven `present: no` records specifically: **1 exact OA PDF found** (Banchorndhevakul), **1 OA HTML/data lead** (Gorokhov), and **5 no exact OA full text verified** (Alcock, Barin, Gurvich, Hultgren, Allibert).

## Results, in sorted input order

| # | Sidecar | Result | Legal source / note |
|---:|---|---|---|
| 1 | `alcock-itkin-horrigan-1984` | No OA full text | Taylor & Francis DOI landing page is paywalled: <https://doi.org/10.1179/cmq.1984.23.3.309>. The `chemicals` package contains derived data, not the article. |
| 2 | `banchor-matsui-naito-1986` | **OA PDF found; acquired** | Official J-STAGE PDF: <https://www.jstage.jst.go.jp/article/jnst1964/23/10/23_10_873/_pdf>. Identity correction: the sidecar DOI `...9735017` is wrong; this paper is DOI `10.1080/18811248.1986.9735071` / J-STAGE DOI `10.3327/jnst.23.873`, pp. 873–882. |
| 3 | `barin-1995` | No OA full text | Wiley book/catalog access only: <https://onlinelibrary.wiley.com/doi/book/10.1002/9783527619825>. |
| 4 | `filiberto-lpsc2011-2064-volatiles` | **OA PDF found; acquired** | Official LPI/NASA conference PDF: <https://www.lpi.usra.edu/meetings/lpsc2011/pdf/2064.pdf>. |
| 5 | `gal-2018-pnas-calcium-storage` | **Excluded: unrelated misfile** | OA PNAS/PMC article: <https://pmc.ncbi.nlm.nih.gov/articles/PMC6205483/>. It is not a KEMS/Langmuir target. |
| 6 | `gurvich-ivtanthermo` | No book PDF; free data interface only | IVTANTHERMO online interface: <https://thermo.jiht.ru/>; not a bulk/open book download. |
| 7 | `hultgren-1973` | No OA full text verified | CERN bibliographic record only: <http://cds.cern.ch/record/231903>. |
| 8 | `kems-001-homma-1966` | **OA PDF found; acquired** | Official J-STAGE PDF: <https://www.jstage.jst.go.jp/article/jinstmet1952/30/6/30_6_515/_pdf>. |
| 9 | `kems-005-fedkin-2006` | **OA PDF found; acquired** | University of Chicago author-hosted PDF: <https://geosci.uchicago.edu/~grossman/Vapor_press_evap_coeff_2006.pdf>. |
| 10 | `kems-006-zhang-2021` | **OA PDF found; acquired** | DOE OSTI public full text: <https://www.osti.gov/servlets/purl/1833818>. |
| 11 | `kems-008-schaefer-fegley-2004` | **OA reprint source found; acquired file retained** | Washington University author publication page lists the reprint: <https://sites.wustl.edu/planetarychemistrylaboratory/publications/io-volcanic-gases-and-more/>. |
| 12 | `kems-010-richter-2007` | **OA PDF found; acquired** | WHOI institutional PDF: <https://website.whoi.edu/gfd/wp-content/uploads/sites/14/2018/10/Richter_2007-2_153764.pdf>. |
| 13 | `kems-011-wetzel-gail-2013` | **OA PDF source verified; not re-staged** | Official A&A/EDP PDF: <https://www.aanda.org/articles/aa/pdf/2013/05/aa20803-12.pdf>. |
| 14 | `kems-014-drowart-2005` | **OA source verified; not re-staged** | Official IUPAC article page: <https://publications.iupac.org/pac/77/4/0683/index.html>. |
| 15 | `kems-019-miller-armatys-2013` | **OA PDF found; acquired** | Bentham Open PDF: <https://benthamopen.com/contents/pdf/TOTHERJ/TOTHERJ-7-2.pdf>. |
| 16 | `kems-026-markova-1984` | No exact OA full text verified | Bibliographic identity only in supplied sidecar; no legal full text found. |
| 17 | `kems-028-yakovlev-1984` | No exact OA full text verified | LPSC/Meteoritika citations located, but no verified legal PDF for the requested record. |
| 18 | `kems-029-yakovlev-shornikov-2011` | No exact OA full text verified | DOI record identified (`10.2205/2011NZ000234`), but no verified legal PDF found. |
| 19 | `kems-033-shornikov-2010` | **OA PDF found; acquired** | Official LPI/NASA PDF (1408): <https://www.lpi.usra.edu/meetings/lpsc2010/pdf/1408.pdf>. |
| 20 | `kems-036-sesko-2024` | **OA full-text source found; not staged** | HAL record/full text: <https://imt-mines-albi.hal.science/hal-04677458v1>. The related TUM thesis is also public: <https://mediatum.ub.tum.de/doc/1730482/1730482.pdf>. |
| 21 | `kems-038-matchett-2006` | Public OA lead; PDF fetch blocked | DTIC record: <http://oai.dtic.mil/oai/oai?identifier=ADA443950&metadataPrefix=html&verb=getRecord>. The direct DTIC PDF URL returned 403 during retry; no copy was substituted. |
| 22 | `kems-040-stolyarova-2015` | **OA PDF found; acquired** | Official SCIRP PDF: <https://www.scirp.net/pdf/msce_2015070111380265.pdf>. |
| 23 | `kems-044-robinot-2026` | No exact OA full text verified | No matching legal OA record identified. |
| 24 | `kems-046-van-limpt-2007` | **OA PDF found; acquired** | TU/e institutional publisher PDF: <https://pure.tue.nl/ws/files/3207578/200702749.pdf>. |
| 25 | `kems-047-turkdogan-1984-isij` | **OA PDF found; acquired** | Official J-STAGE PDF: <https://www.jstage.jst.go.jp/article/isijinternational1966/24/8/24_8_591/_pdf>. |
| 26 | `kems-048-turkdogan-2001-sio2-gamma` | **OA PDF found; acquired** | Official J-STAGE PDF: <https://www.jstage.jst.go.jp/article/isijinternational1989/41/8/41_8_930/_pdf>. |
| 27 | `kems-050-gorokhov-1977` | OA HTML/data only; no report PDF | Official MSU IVTANTHERMO HTML pages: <https://www.chem.msu.ru/rus/tsiv/Cr/allbibs.html>, <https://www.chem.msu.ru/rus/tsiv/Cr/print-CrO.html>, <https://www.chem.msu.ru/rus/tsiv/Cr/print-CrO2.html>. These quote/tabulate data but do not provide IVTAN 43–77 as a PDF. |
| 28 | `kems-051-allibert-1981` | No OA full text verified | DOI/publisher is paywalled; IAS repository record confirms no full text: <https://repository.ias.ac.in/94789/>. |

## Valid OA PDFs in `acquired/`

The following 13 files have a `%PDF-` signature and were fetched from the legal OA URLs listed above (or, for KEMS-008, match the retained institutional-reprint artifact):

```text
banchor-matsui-naito-1986.pdf
filiberto-lpsc2011-2064-volatiles.pdf
kems-001-homma-1966.pdf
kems-005-fedkin-2006.pdf
kems-006-zhang-2021.pdf
kems-008-schaefer-fegley-2004.pdf
kems-010-richter-2007.pdf
kems-019-miller-armatys-2013.pdf
kems-033-shornikov-2010.pdf
kems-040-stolyarova-2015.pdf
kems-046-van-limpt-2007.pdf
kems-047-turkdogan-1984-isij.pdf
kems-048-turkdogan-2001-sio2-gamma.pdf
```

No Git/GitHub PDF was used as an acquisition source.
