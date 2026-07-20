# Boil-to-rump stop diagnosis

## Verdict

Two independent defects explain the demo outcome.

1. **Run-control bug:** a C6 campaign refusal was promoted by both execution
   surfaces into a batch-wide terminal `refused`. Core had already recorded the
   campaign summary and selected the configured next campaign, but
   `simulator/run_executor.py` and `web/events.py` broke their loops on the C6
   summary. The staged fix keeps the refusal diagnostic and continues.
2. **Recipe-selection gap:** the pre-change default executable sequence has no
   high-temperature boil-to-rump leg. It ends at C6, whose static target is
   1400 C. The separate canonical-recipe worker supplies the intended no-MRE
   route, `C0 -> C0B -> C2A_STAGED -> C3_NA -> C4 -> C2A`, with the final C2A
   ramp capped by the dense-alumina catalog limit at 1843 C. This report does
   not duplicate those recipe edits.

The canonical worker's current evidence proves continuation and additional
extraction, but **does not yet prove a mostly-CaO + REE rump**. Its final
residue remains 49.477 wt% SiO2 and 778.494 kg. It is directionally closer than
the 902 kg owner demo, not the Mandate section 5 endpoint.

## Reproduction and root cause

All local runs used the repository's canonical `.venv` symlink and the
`internal-analytical` backend.

### Fresh default runner

Command:

```sh
.venv/bin/python -m simulator.runner \
  --feedstock lunar_mare_low_ti --campaign C0 --hours 300 \
  --backend internal-analytical --output default-full-300h.json
```

This fresh default did not reproduce the owner's 1150 C stall: it reached C6's
1400 C target and ended at Hour 61. It still demonstrated the recipe problem:
the sequence was `C0, C0B, C2A_STAGED, C3_NA, C4, C6`, peak/final T was 1400 C,
and the classified residual was 999.333 kg (0.067% extracted). There is no
post-C6 high-T campaign.

### Exact 1150 C / Hour 161 signature

The owner signature maps deterministically to the C6 acquisition timeout:

- C6 target: 1400 C (`CampaignManager.get_temp_target`).
- Acquisition deadline: 120 completed C6 hours
  (`campaigns.C6.max_target_acquisition_hr`).
- On deadline below target, `CampaignHoldAcquisitionRefusal` records
  `c6_hold_target_not_acquired`, including the binding transport state.
- Global Hour about 161 means entry to C6 near Hour 42 followed by about 120
  stalled hours, matching the reported timeline.
- Temperature ramp becomes zero at transport saturation >=200%. The existing
  regression reproduces the reported 1150 C state with
  `controlled_o2_no_equipment`, 202% saturation, and the same refusal.

The supplied owner symptom does not include the refusal's nested
`binding_transport_state`, so the exact upstream lever cannot be named from the
demo text alone. The code and regression establish that 1150 C is not C6's
recipe target; it is a throttled/unacquired target. Likely binding inputs are
the controlled-O2 transport/equipment boundary or an equivalent explicit
zero-ramp override. The refusal artifact itself is authoritative for choosing
between them.

### Why the entire run stopped

Core treats a recorded C6 refusal as campaign completion, captures the summary,
and asks `CampaignManager` for the next campaign. Before this fix:

- `RunExecutor` detected `campaign_summary.c6_refusal_diagnostic`, set the
  whole run to `refused`, and broke.
- the Socket.IO loop persisted a terminal refused artifact and broke on the
  same summary.

That policy was inconsistent with other campaign-scoped refusals and prevented
any configured downstream campaign from running.

## Fix

- `simulator/run_executor.py`: preserve the C6 refusal in
  `RunExecution.refusal_diagnostic`/runner metadata, but do not change batch
  status or break the driver.
- `web/events.py`: emit the already-existing campaign completion summary,
  record a compact log line, and leave the live run loop active.
- Updated tests pin three properties:
  1. a C6 acquisition refusal followed by enabled C7 produces both C6 and C7
     rows;
  2. CLI/runner output retains the C6 diagnostic without terminal `refused`;
  3. Socket.IO emits the C6 campaign summary, then normal completion, with no
     terminal-refusal event or artifact.

No yield, extraction, composition, thermochemistry, ramp, or endpoint value was
changed. The fix only removes the erroneous batch halt.

## Continued-run evidence

The in-flight canonical-recipe worker's current artifact is:

`/private/tmp/gf-canonical-recipe/docs-private/research/2026-07-19-canonical-recipe/canonical-run.json`

It supplies the recipe half of the split and reports:

| Metric | Continued run |
|---|---:|
| Final hour | 201 |
| Final / peak T | 1843 C |
| Initial charge | 1000.000 kg |
| Classified residual | 778.494 kg |
| Extracted fraction | 22.151% |
| Max absolute mass-balance residual | 1.0232e-13% |
| CaO in residue | 113.226 kg / 14.544 wt% |
| Al2O3 in residue | 138.907 kg / 17.843 wt% |
| TiO2 in residue | 15.440 kg / 1.983 wt% |
| MgO in residue | 60.527 kg / 7.775 wt% |
| FeO in residue | 59.653 kg / 7.663 wt% |
| SiO2 in residue | 385.174 kg / 49.477 wt% |

CaO + Al2O3 + TiO2 is 34.371 wt% of the residue. Na2O and K2O are effectively
zero, and Fe/Mg/SiO extraction improves, so the composition is trending toward
the refractory floor. However, the residue is still silica-dominant and the
artifact exposes no explicit REE species row. Claiming “mostly CaO + REE” from
this result would be false. Closing that remaining recipe/physics gap belongs
to the canonical-recipe workstream, not this run-control patch.

## Verification

Focused canonical-venv checks exercised:

- runner continuation across `C6 -> C7_CA_ALUMINOTHERMIC` after
  `c6_hold_target_not_acquired`;
- runner diagnostic preservation and mass-balance preservation;
- Socket.IO campaign-summary continuation and normal terminal artifact;
- command-plane proof that a C6 campaign refusal alone does not persist a
  terminal artifact.

The continued canonical recipe run independently reports the 1843 C endpoint
and green mass balance above. The internal-analytical backend remains
non-authoritative, so its `partial` certification is expected and must not be
presented as real-backend validation.

## Golden impact

This worker changes no recipe, corpus version, optimizer vocabulary, setpoint,
or golden artifact. Golden impact is **none**. Only run-control source, focused
tests, and this report are staged. The canonical-recipe worker owns its separate
golden updates.
