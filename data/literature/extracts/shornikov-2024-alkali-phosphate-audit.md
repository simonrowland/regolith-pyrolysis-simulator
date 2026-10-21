# Shornikov 2024 K2O–P4O10 melts — extraction audit

Source: Shornikov, S. I. (2024), Thermodynamic properties of the K2O–P4O10 melts, Experiment in Geosciences, vol. 30, no. 1, pp. 70–73. The PDF is a five-page journal excerpt (5 pages, A4, sha256 `b0c1f49a…b7bd`). This extract is only the K2O article.

| PDF page | Published page | Contents |
| --- | --- | --- |
| 1 | cover | Journal masthead. No article. |
| 2 | 70 | Closing paragraph and references of the Na2O–P4O10 article, then the K2O title, abstract and opening. The Na closing has no numeric activity. It is not a second extract. Footer page 70. |
| 3 | 71 | K2O body, Fig. 1, the model paragraph. Footer: Experiment in Geosciences 2024 Volume 30 N 1, page 71. |
| 4 | 72 | Figs. 2 and 3, the 10−6 and 10−14 atm sentences, references. Footer page 72. |
| 5 | 73 | Rest of the K2O references, then the FeO–P4O10 title and opening. The FeO opening is excluded. Footer page 73. |

Duplicate check: not a duplicate. `shornikov-2024-cai-silica-evaporation` and `shornikov-2024-wollastonite-evaporation` are different 2024 articles. No extract cites this title or pages 70–73 of volume 30.

No numeric table is printed. MinerU was not emitted. The pressure sentences match `pdftotext-layout.txt` and the page-72 image. The text layer prints the superscripts as `10–6` and `10–14`; the page image prints 10−6 and 10−14.

## Standard state

Activity rows quote published page 71: the initial values of the standard Gibbs energies of formation of K2O and P4O10 are given in the crystalline and liquid state. The article never names one activity reference phase. Each activity `standard_state` ends with "The activity reference phase is not stated."

`reference_state_from_extract` returns unknown because that sentence contains "not stated". The phase is not inferred. Both crystalline and liquid ΔG° are model inputs. The pressure bounds are gas pressures, not an oxide activity standard.

## Prose kept as context

- Model domain 600–1900 K. Semi-empirical model of Shornikov (2019). Published page 70.
- PO2 and O2 partial pressures over the KPO3 melt at 1300 K do not exceed 10−6 atm. Published page 72. An upper bound for both species, not a point on the line.
- K partial pressure over potassium metaphosphate at 1175 K is no more than 10−14 atm. Published page 72. The Steblevsky et al. (1977) value is called too high and is not printed as a number.
- Mixing-energy minimum lies in the eutectic range between KPO3 and K4P2O7 and is more negative than the Na melt. No numeric minimum is printed. Not read off Fig. 3B.
- Vapor set, with no other printed pressure: K, K2, KO, K2O, K2O2, P, P2, P3, P4, PO, PO2, P2O3, P2O4, P2O5, P3O6, P4O6, P4O7, P4O8, P4O9, P4O10, KPO3, K2P2O6, O, O2, O3, O4.
- Parents (Glushko et al. 1978–1982, Alikhanyan et al. 1975, Rudny et al. 1986, and the phosphate ΔG° "calculated from experimental and theoretical data") are not printed per point. No parent was invented.

Cited melting points 1086, 1376 and 1892 K, and the 916 K dissociation of K5P3O10, are Markina and Voskresenskaya (1969). Not stored.

## Figure decisions

600 dpi render of PDF page 4, published page 72. Axes are labelled. No tick calibration was performed because no coordinate was stored.

Fig. 2 caption: partial pressures over KPO3. Mass spectrometry: Ratkovsky et al. 1975, 1976 (1); Alikhanyan et al. 1975 and Steblevsky et al. 1977 (2–4). Present study (5, 6). Symbols: KPO3 (1, 2), K2P2O6 (3), K (4), PO2 (5), O2 (6).

Legend inside the frame, upper left, off the curves: filled circle 2, open circle 3, half-filled circle 4.

| Marker | Decision | Evidence |
| --- | --- | --- |
| Fig. 2 series 1 | refused | Red line with two error bars. Caption assigns it to Ratkovsky et al., KPO3. Not read. |
| Fig. 2 series 2 | refused | Black line and one filled circle on that line, near 1180 K. Caption assigns it to Alikhanyan et al. and Steblevsky et al., KPO3. Not read. |
| Fig. 2 symbol 3 | refused | One open circle below the KPO3 lines. Caption: K2P2O6, same citations. Not read. |
| Fig. 2 symbol 4 | refused | One half-filled circle below symbol 3. Caption: K. The text says the Steblevsky value is too high and does not print that value. Not read. |
| Fig. 2 legend glyphs 2, 3, 4 | refused | Upper left, with their numerals. Not on the curves. |
| Fig. 2 lines 5 and 6 | refused | Continuous blue calculated PO2 and O2. No discrete calculated marker. The printed 10−6 atm bound is the stored prose value. |
| Fig. 3A curves 1 and 3 | refused | Continuous calculated K2O and P4O10 activities at 1673 K. The 600 dpi interior has no data symbol; the only thick black mark is the panel letter A. |
| Fig. 3A curves 2 and 4 | refused | Continuous Na2O and P4O10 comparison, calculated in the present study. The Na article body is not in this PDF. Not read. |
| Fig. 3B curves 5 and 6 | refused | Continuous mixing energies, K melt (5) and Na melt (6). Red and blue only; no black marker. No numeric minimum is printed. |
| Fig. 1 | refused | Phase diagram according to Xie et al. (2020). |

No point was given a value. Read uncertainty is not stated because nothing was read.
