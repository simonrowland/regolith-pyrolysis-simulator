# E2E harness — Playwright against the live dev server

Drives the app **through a real Chromium browser over HTTP + socket.io**, like an
operator. This is the gap `tests/test_web_optimizer.py` does not cover: that
file calls route functions in-process and never issues an HTTP request.

Stack: **python `playwright.sync_api` + plain pytest** (no `pytest-playwright`).
The browser harness is optional; the physics suite does not require Playwright.

## The one command

Dev server must already be running at `http://127.0.0.1:3000`. This harness
never starts or kills a server.

```bash
# first time only:
.venv/bin/python -m pip install -e ".[e2e]"
.venv/bin/python -m playwright install chromium

.venv/bin/python -m pytest tests/e2e/test_happy_path_journey.py \
    tests/e2e/test_no_feedstock_alert.py \
    tests/e2e/test_run_stall_out_of_domain.py \
    tests/e2e/test_optimizer_bounded.py \
    tests/e2e/test_run_control_journey.py \
    tests/e2e/test_alternate_branch_journey.py -n0 -v
```

`-n0` overrides the repo-wide `-n auto` xdist default. These tests also
carry `@pytest.mark.xdist_group("serial")` so a full-suite run still
serializes them onto one worker. `REGOLITH_E2E_HEADED=1` shows the browser;
`REGOLITH_E2E_BASE_URL` retargets.

When the dev server is absent, browser tests skip with the launch instructions.
Set `REGOLITH_E2E_REQUIRE_SERVER=1` in an e2e lane to turn that condition into a
hard failure so the lane cannot pass vacuously.

Do not collect `tests/e2e/headspace_po2/` with this command — those are
in-process chemistry tests, not browser tests.

## What each test covers

| Test | What | Positive signals |
|---|---|---|
| `test_happy_path_journey.py` | Full operator journey, in order | **1** `/` shows Configuration + ≥1 feedstock option · **2** feedstock card loads from the server · **3** Start emits `start_simulation` and the server answers `simulation_status: started` within 60s · **4** `#status-hour` advances past `Hour: 0` · **5** run reaches `Complete` and `#product-ledger-content` has real numbers · **6** `/optimizer` leaderboard has ≥1 row inside a 120s bound · **7** `/thermal-train` is populated (`data-state != no_data`). Steps 1–4 abort on first failure; 5–7 are collected so one run reports everything reachable |
| `test_no_feedstock_alert.py` | Defect (a): Start with no feedstock | Native `alert("Please select a feedstock first.")` is captured (Playwright auto-dismisses dialogs — that is why this looks dead); `#status-text` stays `Ready`; **no** `start_simulation` leaves the browser |
| `test_run_stall_out_of_domain.py` | Defect (b): run starts then stalls / OUT_OF_DOMAIN | Server must answer within 60s; then no 90s no-progress window across 360s. A true stall dumps the **verbatim socket.io tail**. A terminal `refused`/`error` also fails, with the socket payload as the deliverable |
| `test_optimizer_bounded.py` | Defect (c): `/optimizer` historically ~7 min | Bounded 120s goto; blows up instead of hanging; then asserts leaderboard rows |
| `test_run_control_journey.py` | Pause / resume / cancel / restart in one session | Pause ACK + hour frozen one advance window · resume ACK + hour advances · command-plane cancel hands Start back · second Start is a new `run_id` that ticks |
| `test_alternate_branch_journey.py` | Non-recommended Branch One | PATH_AB stays recommended `A_staged` · BRANCH_ONE_TWO → `one` (skip C4) · campaign chain has `C2A_STAGED`, no `C4` · run to a terminal · product ledger has numbers |

Decision modals are auto-answered with the recommended option by default
(MutationObserver, not a sleep). `test_alternate_branch_journey.py` overrides
that via `decision_auto_answer_js({"BRANCH_ONE_TWO": "one"})`;
unspecified types still take the recommendation. No fixed sleeps anywhere for
synchronisation.

## Evidence captured on every test

Written to `tests/e2e/artifacts/<UTC-timestamp>/` (gitignored):

- `<test>.evidence.json` (or `.json.gz` above 256 KiB) — console, page errors,
  failed requests, HTTP ≥ 400, the tapped socket.io stream both directions,
  auto-answered decisions, step notes. A silent pass that hid a console/page/
  network error **fails the harness**.
- `<test>.png` on failure; the journey also saves `journey-step5-*.png`.

Socket.io is tapped by wrapping `window.io` before the app loads. Tick payloads
are compacted in-page (they are ~0.8–1.3 MB each); `simulation_status` /
start / complete / decision events and any `out_of_domain` excerpt stay
verbatim.

## What it found (live server, 2026-08-28T01:36Z, branch `engine-2026-08-16`)

Authoritative artifacts: `tests/e2e/artifacts/20260828T013656Z/`.
Command: the one-liner above. Result: **2 failed, 2 passed in 445 s**.

Furthest journey step that **succeeded on the run itself**: **step 4 (run
advances)**. Step 5 is where the happy path dies. Steps 6 and 7 still ran and
passed.

### Journey scoreboard

| Step | Result | Detail |
|---|---|---|
| 1 land `/` | PASS | Configuration panel, 25 feedstocks |
| 2 feedstock | PASS | `lunar_mare_low_ti`, card loaded |
| 3 Start | PASS | server answered `started` (`AlphaMELTSBackend`, `ok`) within 60s |
| 4 advances | PASS | `#status-hour` → Hour 1, status `Running` |
| 5 results | **FAIL** | run **REFUSED** at hour 35 / C4 / 1160 °C; product ledger stays `n/a` |
| 6 `/optimizer` | PASS | 11-row leaderboard in **10.3 s** (the ~7 min defect is not reproducing) |
| 7 `/thermal-train` | PASS | `data-state=live`, 12 numbered rows |

Two decision modals were auto-answered with the recommended options
(`PATH_AB` → `A_staged`, `BRANCH_ONE_TWO` → `two`).

### FINDING 1 — happy path dies at C4: `viscous_p_bulk_transport_out_of_domain`

Not a hang and not a crash. The default UI recipe (C4 override `pO2_mbar=0.2`)
drives pyrolysis extraction into the transitional Knudsen band, and the
transport model fail-closes rather than extrapolate. 69 socket events mention
`out_of_domain` (tick diagnostics); the terminal event is `simulation_status`.

Verbatim control-plane payload (nested `silent_zero_notes` / `vapour_batch_*`
omitted here; full 252 KB body is in
`test_run_does_not_stall.evidence.json.gz`):

```
status:  refused
reason:  viscous_p_bulk_transport_out_of_domain
p_bulk_transport_domain: out_of_domain_transitional
ledger_yields_authorized: false
knudsen_number: 0.015438003503065829   (viscous needs Kn < 0.01)
overhead_pressure_mbar: 0.2            (UI default C4 pO2)
pipe_diameter_m: 0.12
gas_temperature_C: 1160
carrier_gas: O2
stage / campaign: C4
affected_species: Ca, CrO, CrO2, CrO3, Fe, K, K2, K2O_gas, Mg,
                  MgO_gas, Mn, Na, Na2, Na2O_gas, SiO, SiO2_gas, TiO2_gas
detail: transitional Kn uses viscous Poiseuille / Bernoulli P_bulk
        outside its validity domain (Kn < 0.01); free-molecular Kn >= 10
        keeps the HKL upper-bound path; t-379 (0.7) supplies
        transitional/molecular conductance
```

Operator-visible consequence: status bar reads
`refused — viscous_p_bulk_transport_out_of_domain`, the product ledger stays
the empty `n/a` shell (screenshot `journey-step5-FAILED.png`). This is why the
owner reports "the run stalled".

### Defect (a) — alert confirmed

`test_start_without_feedstock_alerts` **passed**: exact text
`Please select a feedstock first.`; status stayed `Ready`; no
`start_simulation` escaped. The UX defect (native dialog = looks dead) is
pinned so a regression in that guard is caught.

### Defect (c) — `/optimizer` is currently fast

Rendered in **10.3 s** with 11 rows (winner `mars_perchlorate_rich` /
`mars-perchlorate-rich-objectives-v1`). The 120s bound stays as the
regression guard; it will fail loud if the ~7 min render returns.

### Console / network this run

No console errors, no page errors, no app-origin HTTP ≥ 400. Two same-origin
GET `ERR_ABORTED` (socket.io poll + `/partials/optimizer-jobs`) classified
benign (navigation cancel). One third-party abort of
`unpkg.com/maplibre-gl` CSS — **maplibre is not in this repository**; browser
environment noise, recorded under `third_party_failures`.
)
