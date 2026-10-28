`tools/mineru_table_draft.py compare` output for this extract and the MinerU draft index:

```text
compare: matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 locator-error=0 unverifiable=0 total=0
```

The CSV has only its header because the compare tool's `_numeric_points` accepts `value`, pressure, activity, gamma, and `T_m_K` fields; it does not inspect this extract's `mass_loss_pct` or context-only schema-gap fields. This is a tool coverage limitation, not evidence that the Table II values matched. The full manual cell audit and render checks are in `niggli-1913-silica-alkali-carbonate-equilibria-audit.md`.
