# SOURCE — FastChem v3.1.3 thermochemical coefficient carriers

FastChem project, release tag v3.1.3, observed commit 51857a84e2b25d4246624c978cdebe988789a729. Official repository https://github.com/NewStrangeWorlds/FastChem, redirected from the paper-linked exoclime/FastChem repository. This is a frozen version 3 dataset, not current master FastChem 4 or a claim of latest release.

## Original files and custody

All four public raw files returned HTTP 200 after URL-history checks and paced requests; actual GitHub Raw redirects were followed. Root sole-moved unchanged files here, preserving original basenames. No engine, installer, binary or source-code payload executed.

- logK.dat: 109,926 bytes; SHA-256 dbf4d53afe7b68ac9504560843b90d304f04b8570ae4ab89bc88289455b78806. Standard gas compilation, 495 molecular/ionic records. Header last update 2019-04-10.
- logK_condensates.dat: 55,969 bytes; SHA-256 c853a104385d627a5b35e9a0409fb1163d30596a08cca5b1368b9abe37f5635f. 186 solid/liquid condensate records. Header update June 2023.
- README.TXT: 714 bytes; SHA-256 45c88c13e1665d26d6a2fd507f5fd04805fad6a93bce542895d820a5c832b35a.
- licence.md: 34,917 bytes; SHA-256 2e13f31cbd2ca0dbbd35df7d0fb8beb4dc20e2c03e0eedc6b114afe8e82a8cb2. Repository GPL version 3 text.

The header dates are not release dates. FILES.json in /Users/simonrowland/Repos/paper-download-tools/runs/2026-10-30-wave233-fastchem/ preserves exact raw URLs, receipt body paths and intermediate rename custody. Transport receipts retain historical staging paths; canonical files now reside here. Native ADD/TAKE and post-intake hashes are in that run.

## Verification and conventions

Child and parent independently parsed the plain-text carriers: UTF-8, no NUL or HTML, exactly five finite coefficients per fit, recognized condensate phase flags and finite temperature limits. Parent reproduced 495/186 record counts and all file pins. Structural integrity is not independent chemical validation; no PDF qualification applies to DAT files. See DATA-REVIEW.json, ROOT-DATA-REVIEW.json and ROOT-REVIEW.md.

Stored fits use natural logarithms, T in kelvin and standard pressure 1 bar. The file header writes a1/T + a2 ln(T) + a3 + a4 T + a5 T²; documentation uses a0, a1, b0, b1, b2 for the same ordered five columns. Preserve indexing rather than shifting coefficients.

Operational mass action is n_i = K_i product(n_j^nu_ij): association of atomic components into a species, inverse to a conventional dissociation constant. The paper also uses dissociation terminology; that wording/sign ambiguity is explicitly preserved in the revised review. Do not infer reaction direction solely from that prose or read coefficients as elemental-reference formation Gibbs energies. Tagged number-density implementation applies temperature, stoichiometric and cgs pressure scaling. Explicit reaction-direction, charge, standard-state and unit mapping remains required before any Gibbs/MELTS conversion. No such model conversion was authorized or performed.

Gas records have no individual fitted-temperature bounds. Solver tests across 100–6000 K are not universal fit validity. Condensates have explicit phase/upper-temperature limits: Na2SiO3 1362/2500 K, Na2Si2O5 1147/2500 K, Al2O3 2327/4000 K and SiO2 1996/3000 K are examples. Do not extrapolate beyond source bounds or confuse solid/liquid entries with melt-solution activity parameters.

## Lineage, coverage and rights

These are compiled/refitted literature constants, not new independent experiments. Per-record JANAF, Barin and other references remain intact. Repository GPL text is preserved; it does not independently adjudicate or relicense rights in upstream thermochemical compilations. Public data download and no-contact access are the acquisition basis.

Relevant coverage includes major/minor gas species, ions, oxides, halides, hydroxides, sulfides and condensates. No Na4SiO4 or Na6Si2O7, liquid-solution activity or bilateral miscibility coverage is claimed. The literal NaCO3 label versus C1Na2O3 stoichiometry mismatch remains unresolved. The README's extended trace-to-uranium carrier was observed but not acquired; this packet is not represented as that expanded file.

Methods references, not locally acquired paper claims: Stock, Kitzmann and Patzer (2022), FastChem 2, DOI 10.1093/mnras/stac2623, arXiv:2206.08247; condensation method Kitzmann, Stock and Patzer (2024), DOI 10.1093/mnras/stad3515. No corpus seating, code execution, independent numerical reproduction or scientific model promotion occurred.
