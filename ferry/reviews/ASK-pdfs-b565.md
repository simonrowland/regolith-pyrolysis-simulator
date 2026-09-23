# ASK PDFs — b-565 pressure note vs stored Pa (1000×)

Lane: `empirical/b565-pressure-note-2026-09-22`
Date: 2026-09-22 (America/Toronto)
Base: `origin/work-v064-green`

## Bug

Two pressure records in one works YAML store `point: '0.1'` Pa beside locator notes that say printed `10^-6 mbar`.

- Correct: `10^-6 mbar = 1e-4 Pa = 0.0001 Pa`
- Stored: `0.1 Pa = 1e-6 bar` (mbar↔bar confusion; 1000× high)

## Source paper (need printed §2.2)

| source_id | work_id | citation | Local PDF |
|---|---|---|---|
| `kems-006-zhang-2021` | `6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4` | Zhang et al. (2021), vacuum evaporation of Na, K and Rb from a basaltic silicate melt | **HAVE** `/workspace/ferry-inbox/acquired/kems-006-zhang-2021.pdf` (SHA256 `c25927f3cebace1ff65afd7d20ad17bbbf1b9d0ecf37a6ce916449688b4f251e`) |

Records (both in `data/literature/works/6ae16616….yaml`, mirrored in `data/literature/extracts/kems-006-zhang-2021.yaml`):

1. `pressure_environment.total_pressure_Pa` — note: `Printed 10^-6 mbar; unit conversion only`
2. `pumping.base_pressure_Pa` — note: `Printed about 10^-6 mbar; unit conversion only`

## Please ship

Only if acquired copy is wrong/stale: `kems-006-zhang-2021.pdf` (Zhang et al. 2021; corpus path `raw/kems-006-zhang-2021/`). Otherwise no Dropbox needed — empirical will fix from the held PDF §2.2 quote.

Drop under ferry `from-main-*/pdfs/` if re-shipping.
