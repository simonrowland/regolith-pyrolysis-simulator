# t-1353 store regeneration impact

No battery store regeneration was run or committed. An isolated, read-only
migration comparison against `28af098ed04b8ca99f569381a2f38330cb66d7cd`
shows 1,121 observation IDs replaced by 1,121 IDs; the expected observation
count delta is zero. The old and replacement IDs are listed in
[`t1353_store_regen_impact.csv`](t1353_store_regen_impact.csv).

The rekeys come from `series_row_extra` hashing row locators when
`include_locator=content_stable_id` is true. Track the root cause in t-1357.
