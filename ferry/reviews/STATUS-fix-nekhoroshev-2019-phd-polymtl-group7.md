# STATUS: fix nekhoroshev-2019-phd-polymtl — group 7 (narrative values + numbered equations, item 7; ledger gaps)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:31 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `b6f2b46966c2c33da3b1a4b41f694d01884b72e7`
(chain: cfaa7465 → b02bcc7b g1 → 2a9892ad g2 → a99679a4 g3 → 6c47c26b g4 → 9fb4916a g5 → 30349a12 g6 → b6f2b469 g7)

## Groups covered
- g7 (this push), item 7:
  - New `nekhoroshev_2019_narrative_quantities` row: 8 entries / 33 printed values, each with a verbatim sentence and page:
    - p.80: 1995.99 K; 9581.4 with the printed unit J·mol-1·K-1. That unit does not fit an enthalpy; it is carried as printed and flagged, not corrected.
    - p.159: >2100, 2150±100, ~2260, 1910, 1450°C.
    - p.166: 1686±5, >600, 1150±20, 665, 1170 [378], 1150±20 [369], 1055±5 [291]°C.
    - p.172: −3979.26±3.62 (Kiseleva [194]) and −3967.69±3.37 (Robie [384]) kJ/mol. The quantity's name is a typed unknown because the sentence does not name it.
    - p.216: 28.5–53.5±0.5 mol% Na2SiO3, 485°C, 6.49 kJ/mol, 725°C (Moir & Glasser [87]).
    - p.400: 821±4°C (Appleman & Clark [650]).
    - p.408: 700°C, −172.21 [300], −56.62 [664], −121.42 (this work, model_derived) → −21.89 kJ/mol (model_derived).
    - p.431: 629±5°C; 4±1 / 30±8 / 66±8 mol% (Taylor & Owen [688]).
    - The p.302, p.385 and p.400 method values are in `nekhoroshev_2019_per_study_method_facts` (g6).
  - New `nekhoroshev_2019_numbered_equations` row with 18 equations: (97)–(99) p.80, (106)–(109) p.152, (110)–(115) p.161, (122) p.196, (129) p.222, (132) p.247, (136) p.376, (137) p.379.
    - Each is linearised from a 220 dpi page-image crop. (109) is carried with its printed non-subscript "K2O".
    - There is a cross-reference to the 22 equations already in the reduction-lineage row.
    - The remaining equations are a typed `not_carried` entry.
  - Ledger `completeness.gaps` rewritten: what is now carried, and that equations (1)–(96), (100), (101) are untranscribed.

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**. Fidelity validator: OK: 1 extract file(s) valid. `tools/test_ledgers_valid.py`: 674 passed.

## Items done: 10/10 review items addressed; item 7 is partial (equations (1)–(96), (100), (101) remain, see final STATUS)
