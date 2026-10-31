# Extended gas carrier — successor note, 2026-10-30

The original SOURCE.md described the earlier standard-only intake. The standard files and their historical SOURCE remain unchanged. The additional original logK_extended.dat is now attached to the same owner, fastchem-v3-1-3-gas-condensate-coefficients. It is not a second independent dataset for its shared rows.

Official FastChem v3.1.3, tag commit 51857a84e2b25d4246624c978cdebe988789a729. Actual observed raw download: https://raw.githubusercontent.com/NewStrangeWorlds/FastChem/refs/tags/v3.1.3/input/logK/logK_extended.dat . HTTP 200 completed 2026-10-30T21:33:52.427Z. Original 156,574 bytes; SHA-256 9bc4f8dd1d7d4cd29c4b7b33ab7bb00bb3f16d5fe39dedea5465939532064a3d. Root sole-moved unchanged file here, preserving basename. FILES.json in the acquisition run retains intermediate custody paths and receipt URL.

Child and parent independently compared against the canonical standard file: 675 records include all 495 standard records with identical headers and coefficient lines, plus 180 added monatomic-ion fits. No missing or changed standard records; no additional molecules or condensates. Parent reproduced 54 singly positive, 78 doubly positive and 48 negative labels across 78 atomic-component symbols. All 675 records contain five finite coefficients; UTF-8 without HTML/NUL. Structural integrity is not independent chemical validation.

The additions cover major/minor gas ions and trace metals through Th/U. Of the 180 additions, 179 are Saha/Kurucz/NIST-derived; Sc+ cites NASA/TP-1999-208523. The README cites Hoeijmakers et al. (2019), A&A 627, A165. Per-record references, stoichiometry and signed electron powers remain intact. These are compiled/model-derived ionization or attachment fits, not independent melt-redox measurements, ionization cross sections or instrument mass-spectral response data.

Preserve the corrected operational association direction, natural logarithm, T in kelvin, standard pressure and column ordering documented in SOURCE.md. Signed electron stoichiometry matters. Direct Gibbs/MELTS reference-state conversion remains pending. No per-record gas temperature validity supplied; solver test domain is not universal fit validity. Do not extrapolate or use ion labels as evidence for measured slag Cr/Ti valence ratios.

Repository GPL text already held; upstream data rights remain separate. No engine, software installer or solver was executed, and no model import, corpus seating or scientific promotion occurred.

Acquisition/review: paper-download-tools run 2026-10-30-wave234-fastchem-extended . REVIEW.md/JSON, DATA-REVIEW.json, ROOT-COMPARISON.json and ROOT-ION-COUNTS.json preserve verification scope. Native attachment receipts and post-manifest audit reside there.
