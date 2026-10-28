# Yakovlev & Shornikov (2021), Figure 1 extraction audit

The 17 measured Figure 1 symbols are transcribed as typed `figure_read_point` context rows: 4 granite (filled) and 13 basalt (open). The plotted ordinate is the printed K2O/Na2O mass ratio (`mass ratio`). Figure 2 contains calculated curves and is context only. The extract schema has no quantity token for this printed ratio, so these points remain context and produce zero observations; this schema gap is tracked as t-1565. No schema/API change was made.

Two independent calibrated reads agree for all 17 symbols within twice the combined uncertainty. The maximum absolute difference is 1.4 °C in temperature and 0.0095 in ratio. The first-read coordinates and uncertainties are used. Digitisation uncertainty reflects calibration residual plus half-symbol width; it is not a paper-reported experimental uncertainty.

The prose states 10^-9 bar, 30 min, and a temperature range of 1180–1450 °C. Figure symbols around 1026, 1500, and 1600 °C remain included as read. Starting compositions are unknown because none are printed.

Verification basis: controller two-read verification, 2026-10-28.

- Primary source sidecar: `~/Repos/regolith-corpus/raw/yakovlev-2021-chondrule-alkali/sidecar.yaml`.
