# B13 corpus root output pin (before move)

Captured from the existing test assignment expressions at the pin revision. This is a path and
availability summary for the refactor report, not a test golden or a new assertion. The override
is the non-existent path `$TMPDIR/bugs1020-sc333-b975-corpus-override`.

| Caller | Environment unset: selected path and gate output | Environment overridden: selected path and gate output |
|---|---|---|
| `test_corpus_extract_mirror.py` | `/Users/simonrowland/Repos/regolith-corpus`; `CORPUS_ROOT.is_dir=True` | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; `CORPUS_ROOT.is_dir=False` |
| `test_janaf_compilation.py` | `/Users/simonrowland/Repos/regolith-corpus`; `raw/janaf-nist-txt.is_dir=True` | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; `raw/janaf-nist-txt.is_dir=False` |
| `test_robie-hemingway-fisher-1978-usgs-b1452_compilation.py` | `/Users/simonrowland/Repos/regolith-corpus`; MinerU directory and source PDF exist; `pdftotext` is available | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; MinerU directory and source PDF are absent; `pdftotext` is available |
| `test_hemingway-haas-robinson-1982-usgs-b1544_compilation.py` | `/Users/simonrowland/Repos/regolith-corpus`; B1544 source PDF exists | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; B1544 source PDF is absent |
| `test_pankratz_1987_usbm_b689_compilation.py` | `/Users/simonrowland/Repos/regolith-corpus`; B689 raw source directory exists | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; B689 raw source directory is absent |
| `battery/test_janaf_generator.py` | `/Users/simonrowland/Repos/regolith-corpus/raw/janaf-nist-txt`; source directory exists | `$TMPDIR/bugs1020-sc333-b975-corpus-override/raw/janaf-nist-txt`; source directory is absent |
| `test_literature_index.py` (additional same-rule caller found by `rg`) | `/Users/simonrowland/Repos/regolith-corpus`; `raw/janaf-nist-txt.is_dir=True` | `$TMPDIR/bugs1020-sc333-b975-corpus-override`; `raw/janaf-nist-txt.is_dir=False` |

`Path.home()` was `/Users/simonrowland`, so the B689 default resolved to the same path as the six
hard-coded defaults. Existing skip predicates are identified in the callers and remain unchanged.
