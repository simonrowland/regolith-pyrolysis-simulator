# AQ6 — open-access acquisition hunt (final B4 batch)

**Run date:** 2026-09-23 (Toronto / EDT)  
**Input:** remaining `*.sidecar.yaml` files after AQ5 in `from-main-B4-20260923T031628Z/not-obtained/`, lexicographically sorted (AQ5 ended at `kems-191-storms-1985`).  
**Range:** `kems-192-rolinski-1971` through `ta-yamanaka-1997-metsoc` (**27** sidecars confirmed).  
**Policy:** legal OA only (publisher OA, arXiv, NASA NTRS, OSTI, NIST, author/institutional repo, LPI/USRA, J-STAGE, TIB OA, Comptes Rendus / Académie des sciences, Copernicus EJM). No Sci-Hub, shadow libraries, or GitHub PDF dumps were used as acquisition sources.

## Counts

- Sidecars reviewed: **27**
- Exact-work legal OA full-text source located: **13**
- Exact-work legal OA PDF files acquired into `/workspace/ferry-inbox/acquired/`: **13** (12 unique SHA256; `ta-badro-2021` and `llnl-2021-cai-aerodynamic-levitation` are the same CRAS VoR bytes)
- OA HTML/data-only (not counted as PDF acquisition): **0**
- No exact-work legal OA full text verified this pass: **14**
- Sidecars with `present: yes` / `access: held` in input (corpus-recovery metadata only; PDF not in ferry package): **9** (`llnl`, `lpsc-2024`, `senior-1992`, and 6 `ta-*` excluding flemetakis/dacko/badro already counted in the 9) — still hunted for legal OA; 8 of those 9 now have OA PDFs staged (senior remains citation-only)

## Results, in sorted input order

| # | Sidecar | found? | URL | licence / why legal | relevance |
|---:|---|---|---|---|---:|
| 1 | `kems-192-rolinski-1971` | no | Publisher: <https://doi.org/10.1007/bf02814902> (Springer Met. Trans.). Unpaywall: closed. | Springer TDM/VOR; not OA | 3 |
| 2 | `kems-193-choudary-1975-p5` | no | Conf. Int. Thermodyn. Chim. 4th, 3, 5 (1975); no DOI. Crossref/OpenAlex unresolved; no official OA PDF. | No official OA PDF located | 3 |
| 3 | `kems-194-rolinski-1972` | no | Publisher: <https://doi.org/10.1007/bf02643026> (Springer Met. Trans.). Unpaywall: closed. | Springer TDM/VOR; not OA | 3 |
| 4 | `kems-195-szelma-1984` | no | Metall. Odelw. / Metalurgia i Odlewnictwo 10:259 (1984); no DOI. Crossref/OpenAlex empty. | No official OA PDF located | 3 |
| 5 | `kems-196-norman-1964` | no | Publisher: <https://doi.org/10.1063/1.1725649> (AIP J. Chem. Phys.). Unpaywall: closed. | AIP copyright; not OA | 3 |
| 6 | `kems-197-granier-1982` | no | Publisher: <https://doi.org/10.1111/j.1151-2916.1982.tb10334.x> (Wiley J. Am. Ceram. Soc.). Unpaywall: closed. HAL search for Granier+Chatillon Ti-O-N: no deposit. | Wiley VOR; not OA | 3 |
| 7 | `kems-199-nunoue-1989` | no | Publisher: <https://doi.org/10.1007/bf02651663> (Springer Met. Trans. A). Unpaywall: closed. | Springer TDM/VOR; not OA | 3 |
| 8 | `knacke-kubaschewski-hesselmann-1991` | no | Springer / Verlag Stahleisen book (ISBN 9783540540144). Google Books catalog only per brief. | Copyrighted book; catalog only — no download | 2 |
| 9 | `knight-gca2009` | **yes; PDF acquired** | OSTI public full text: <https://www.osti.gov/servlets/purl/965072> (OSTI ID 965072; LLNL-JRNL-414068). Publisher VoR DOI <https://doi.org/10.1016/j.gca.2009.07.008> is Unpaywall-closed; WiscSIMS author PDF URL TLS-failed from this host (not used). | U.S. DOE / LLNL public release via OSTI (legal OA preprint/accepted MS) | 3 |
| 10 | `lamoreaux-hildenbrand-1984` | no | Official NIST JPCRD reprint <https://srd.nist.gov/jpcrdreprint/1.555706.pdf> still HTTP **503**. Unpaywall reports closed (AIP host). Wayback fetch failed. | NIST reprint intended free; host unavailable this pass | 3 |
| 11 | `lamoreaux-hildenbrand-hildenbrand-1987` | no | Official NIST JPCRD reprint <https://srd.nist.gov/jpcrdreprint/1.555799.pdf> still HTTP **503**. Unpaywall closed. | NIST reprint intended free; host unavailable this pass | 3 |
| 12 | `llnl-2021-cai-aerodynamic-levitation` | **yes; PDF acquired** | Comptes Rendus Géoscience gold OA VoR: <https://comptes-rendus.academie-sciences.fr/geoscience/item/10.5802/crgeos.56.pdf> (DOI <https://doi.org/10.5802/crgeos.56>). Same work as `ta-badro-2021` (Badro et al. 2021). Census held SHA (`291c2ed5…`, 1642238 B) differs from CRAS VoR (`9cce11dc…`, 1863247 B) — private held copy was a different encoding; legal OA VoR staged. | Académie des sciences / CR Géoscience gold OA | 3 |
| 13 | `lpsc-2024-bennu-pyrolysis-vandam` | **yes; PDF acquired** | LPI/USRA LPSC 2024 abstract 1219: <https://www.hou.usra.edu/meetings/lpsc2024/pdf/1219.pdf> (Mojarro et al.; filename “vandam” is a misnomer). SHA256 matches census held. | LPI meeting abstract (public) | 2 |
| 14 | `metsoc-2019-6005` | **yes; PDF acquired** | LPI/USRA MetSoc 2019 abstract 6005: <https://www.hou.usra.edu/meetings/metsoc2019/pdf/6005.pdf> (Shornikov & Yakovlev, perovskite Knudsen-cell evaporation). | LPI meeting abstract (public) | 3 |
| 15 | `nist-jpcrd-120` | no | Official NIST JPCRD reprint <https://srd.nist.gov/jpcrdreprint/1.555580.pdf> / legacy `JPCRD/jpcrd120.pdf` still HTTP **503**. Unpaywall closed. | NIST reprint intended free; host unavailable this pass | 2 |
| 16 | `ntrs-19730008085` | **yes; PDF acquired** | NTRS PUBLIC download: <https://ntrs.nasa.gov/api/citations/19730008085/downloads/19730008085.pdf> (`downloadsAvailable: true`). Title: *Noble gases in the moon* (NASA-NGR-26-003-057 annual report, 1972). | NASA NTRS public distribution | 1 |
| 17 | `ntrs-20250004626` | **yes; PDF acquired** | NTRS PUBLIC download: <https://ntrs.nasa.gov/api/citations/20250004626/downloads/MRE_paper_revision_clean.pdf> (`downloadsAvailable: true`). Title: *Improving molten regolith electrolysis with zirconia-based hollow anode technology*. | NASA NTRS public distribution | 2 |
| 18 | `pankratz-1982-usbm-b672` | no | USBM Bulletin 672 (U.S. gov PD). HathiTrust full-view cataloged; CDC stacks / UNT / babel downloads blocked (403/Altcha/Cloudflare) this pass — same as prior vacuum notes. | Public domain intended; no unchallenged PDF this pass | 2 |
| 19 | `senior-1992-vacuum-pyrolysis` | **no (citation-only)** | NTRS accessions **19920035166** (*Lunar oxygen production by pyrolysis of regolith*, Senior 1991 Princeton/AIAA/SSI) and **19920033574** (*Solar heating of common lunar minerals…*, Senior 1991 JBIS 44): both `downloadsAvailable: **false**` (0 downloads). Already recorded in STATUS-reply-senior-1992. Sidecar `present:yes` PDF is the wrong NASA STI bibliography (correction block in sidecar). **No extract invented.** | Public citation records only; no PDF payload | 3 |
| 20 | `shim-banya-1981-feo-mgo-sio2` | no | J-STAGE Tetsu-to-Hagané 67:1735 OA PDF URL still HTTP **500** via `sblogin`. Wayback CDX empty (prior). DOI 10.2355/tetsutohagane1955.67.11_1735 unresolved. | J-STAGE OA intended; sblogin 500 this pass | 3 |
| 21 | `ta-badro-2021` | **yes; PDF acquired** | Same CRAS gold OA VoR as #12: <https://comptes-rendus.academie-sciences.fr/geoscience/item/10.5802/crgeos.56.pdf>. SHA256 matches census held. | Académie des sciences / CR Géoscience gold OA | 3 |
| 22 | `ta-dacko-conradt-low-p-transpiration` | **yes; PDF acquired** | TIB OA: <https://oa.tib.eu/renate/bitstreams/e98546c4-b874-43ae-b9dd-4913c2c1ee65/download> (DOI <https://doi.org/10.34657/13923>; Dacko, Wilsmann, Conradt — low-P transpiration evaporation). SHA256 matches census held. | TIB Open Access / DOI 10.34657/13923 | 3 |
| 23 | `ta-flemetakis-2024` | **yes; PDF acquired** | Copernicus EJM gold OA: <https://ejm.copernicus.org/articles/36/173/2024/ejm-36-173-2024.pdf> (DOI <https://doi.org/10.5194/ejm-36-173-2024>). SHA256 matches census held. | CC BY Copernicus EJM | 3 |
| 24 | `ta-mendybaev-2002-lpsc` | **yes; PDF acquired** | LPI LPSC 2002 abstract 2040: <https://www.lpi.usra.edu/meetings/lpsc2002/pdf/2040.pdf>. SHA256 matches census held. | LPI meeting abstract (public) | 3 |
| 25 | `ta-mendybaev-2020-lpsc` | **yes; PDF acquired** | LPI/USRA LPSC 2020 abstract **2168**: <https://www.hou.usra.edu/meetings/lpsc2020/pdf/2168.pdf> (*Thermodynamics and Evaporation Kinetics of CAI-Like Melts*). SHA256 matches census held. | LPI meeting abstract (public) | 3 |
| 26 | `ta-shirai-2000-lpsc` | **yes; PDF acquired** | LPI LPSC 2000 abstract **1610**: <https://www.lpi.usra.edu/meetings/LPSC2000/pdf/1610.pdf> (*Evaporation Rates of Na from Na2O-SiO2 Melt at 1 atm*). SHA256 matches census held. | LPI meeting abstract (public) | 3 |
| 27 | `ta-yamanaka-1997-metsoc` | **yes; PDF acquired** | LPI MetSoc 1997 abstract **5122**: <https://www.lpi.usra.edu/meetings/metsoc97/pdf/5122.pdf> (*Evaporation Experiments of Na from a Na2O-SiO2 Melt by Thermogravimetry*). SHA256 matches census held. | LPI meeting abstract (public) | 3 |

**Relevance scale:** 1 = peripheral; 2 = related thermochem / ISRU / vaporisation context; 3 = direct KEMS / activity / oxide-melt evaporation / pyrolysis measurement source.

## Valid OA PDFs in `acquired/` (this batch)

```text
knight-gca2009.pdf
llnl-2021-cai-aerodynamic-levitation.pdf
lpsc-2024-bennu-pyrolysis-vandam.pdf
metsoc-2019-6005.pdf
ntrs-19730008085.pdf
ntrs-20250004626.pdf
ta-badro-2021.pdf
ta-dacko-conradt-low-p-transpiration.pdf
ta-flemetakis-2024.pdf
ta-mendybaev-2002-lpsc.pdf
ta-mendybaev-2020-lpsc.pdf
ta-shirai-2000-lpsc.pdf
ta-yamanaka-1997-metsoc.pdf
```

| file | SHA256 | size (B) | source |
|---|---|---:|---|
| `knight-gca2009.pdf` | `8952cf1e0a800c43b7a53c3e3101b6f1017d34e4c384ec905d324fa14489c4dc` | 2584425 | OSTI 965072 |
| `llnl-2021-cai-aerodynamic-levitation.pdf` | `9cce11dc9bbf3e8b2ebfa2281d70c887199d5c2854e406f7838f471df5c28fe2` | 1863247 | CRAS 10.5802/crgeos.56 (same bytes as ta-badro) |
| `lpsc-2024-bennu-pyrolysis-vandam.pdf` | `9444343099d69f6e02b8671e679d20a118e59b03678bcd4d5fe727cbc008d69b` | 137679 | hou.usra.edu LPSC2024/1219 |
| `metsoc-2019-6005.pdf` | `c09c421f62accbed788a44e15e17c42caed290a500c45503d154a44d7efe3c67` | 279293 | hou.usra.edu MetSoc2019/6005 |
| `ntrs-19730008085.pdf` | `e4d432b54a2a59968e3626f091a976bd6a9c7e92123fbf9d025833551ea04b79` | 2228378 | NTRS API download |
| `ntrs-20250004626.pdf` | `ba46da63295be1cf1774bc268b6a63b632a199a61f1f8d663449676bc96b3ab5` | 1965579 | NTRS API download |
| `ta-badro-2021.pdf` | `9cce11dc9bbf3e8b2ebfa2281d70c887199d5c2854e406f7838f471df5c28fe2` | 1863247 | CRAS 10.5802/crgeos.56 |
| `ta-dacko-conradt-low-p-transpiration.pdf` | `6ce8060672079f92e8260dab3280790a90b341761f94a2293fa23212f042680a` | 2841371 | TIB OA bitstream |
| `ta-flemetakis-2024.pdf` | `1e8dc32994df67268491c052b95de619a3d784e2eddc32255e5aa9468b41ef99` | 2890205 | EJM Copernicus |
| `ta-mendybaev-2002-lpsc.pdf` | `7f9ef6f0c2dc2bac8ef12491097aab37c2f98737bc40a88f8d8bc15a07b328fc` | 209588 | lpi.usra.edu LPSC2002/2040 |
| `ta-mendybaev-2020-lpsc.pdf` | `9aedf34339e39ad39729d1c57e362abf42b8171ca8c13df06d01395e7894c23c` | 244168 | hou.usra.edu LPSC2020/2168 |
| `ta-shirai-2000-lpsc.pdf` | `c156af42719bbc71a58893448664ae84fe65cbb2943d33a48b8e02efe54f84e8` | 55033 | lpi.usra.edu LPSC2000/1610 |
| `ta-yamanaka-1997-metsoc.pdf` | `b0ea5696e89bac07b770efef10118a475e24fc2193494a5d11182eeabe4fda8a` | 13863 | lpi.usra.edu MetSoc97/5122 |

## Search notes (methods)

- Unpaywall v2 for all known DOIs in batch → only CRAS `10.5802/crgeos.56` and EJM `10.5194/ejm-36-173-2024` reported `is_oa=true` gold; Knight GCA VoR closed (OSTI preprint used instead).
- NTRS API for `19730008085`, `20250004626` → `downloadsAvailable=true`; PDFs retrieved. Senior accessions `19920035166` / `19920033574` → `downloadsAvailable=false` (reconfirmed; no extract invented).
- LPI/USRA abstracts via `www.lpi.usra.edu` (older meetings) and `www.hou.usra.edu` with Cloudflare IPv4 `--resolve` (box DNS for `hou.usra.edu` failed).
- OSTI servlet for Knight LLNL-JRNL-414068.
- TIB OA bitstream for Dacko/Conradt low-P transpiration.
- NIST `srd.nist.gov` JPCRD reprints still **503** (lamoreaux×2, jpcrd-120); Wayback live fetch failed.
- J-STAGE shim-banya still **500** sblogin.
- Pankratz USBM B672: CDC/UNT/Hathi download paths still blocked; no bot bypass.
- Knacke 1991 book: catalog only (brief).
- No Sci-Hub / shadow / GitHub PDF sources used.

## B4 AQ series close

AQ6 is the **final** batch for BACKLOG 4 `not-obtained/` after AQ1–AQ5. Remaining gaps above are paywalled journals, NIST host 503s, J-STAGE sblogin 500, Pankratz download walls, the Knacke book, and Senior NTRS citation-only records.
