# REVIEW — redox design r3 independent confirmation

- **Reviewer:** regolith-empirical (frontier independent confirmation)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `9277ef6ede6d6feea6f7ef4abc676e3fcbdec008` on `empirical/review-redox-design-r3-confirm` (tracks `review/stack2-redox`)
- **Design line numbers:** refer to `0ba4fb42862e58cde05da3a688d493fa30f99582` (chunk 1 shifted `simulator/core.py` by ~2 lines; key symbols at 7103 / 7437 / 5389 still align)
- **Design under review:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/redox-design-r3.md` (1127 lines; author gpt-6.1-sol)
- **Prior findings:** `sol-r2-confirm.md` (REVISE)
- **Date:** 2026-09-30 ~20:05–20:15 ET
- **Mode:** read-only design confirmation; targeted numerical re-derivation only; no origin push of the review tip; slot-z14 left alone (chunk-1 code review of record)

## Scope

Independent confirmation of redox design r3. Same engine that raised the r2 findings wrote r3; this seat is the only independent check on those fixes. Did **not** invent extract/observation data. Did **not** arm Mac listen pools.

## Verdict

**VERDICT: CONVERGED**

Every `sol-r2-confirm.md` finding is addressed with a correct (not merely present) fix. Independent re-derivation of the finite M2 film, \(C_M\), exponential local-Jacobian step, overshoot/refinement acceptance, and 520/529 bound reproduces the design’s numbers. Chunks 2–5 remain coherent and do **not** need to stop.

---

## 1. NOT-FIXED lens on every sol-r2-confirm finding

### P1 — M2 transport (infinite conductance / film) → **FIXED, correct**

**r2 finding:** coexistence does not derive \(P_i=P_m\) / infinite melt conductance; ideal activity gives finite \(dN/d\ln P_m = n/[4(1-a)]\).

**r3 claim (quotes):**

- L63: “Two phases fix one equilibrium fO₂ **at the current activity**. They do not establish an infinite dissolved-FeO inventory derivative or remove transport resistance.”
- L116–160: finite surface-inventory root \(F(u)=\alpha[P_g-P_b(u)]-B(u-N)=0\) with \(N=n_{\mathrm{FeO}}/2\), \(N_T=(n_{\mathrm{FeO}}+n_{\mathrm{Fe}})/2\).
- L195–219: ideal counterexample derivation ending in \(C_M=n/[4(1-a)]\); “r2’s step-capacitance argument is withdrawn.”
- L249: “retain finite melt transport… It does not assume instantaneous bulk equilibration.”
- L180–193: surface endpoint complementarity (\(F(N_T)>0\Rightarrow u=N_T\), \(P_i=P_g-J/\alpha\ge P_b(N_T)\); not a bulk cap).

**Independent check:** re-derived \(d\ln P_b/dn=2(1-a)/n\) and \(C_M=n/[4(1-a)]\). Re-solved the L251 fixture (FeO=metal=1, solvent=9, \(P_{\mathrm{IW}}=1\) Pa, \(P_g=0.02\) Pa, \(k_g=0.01\), \(k_m=10^{-9}\), \(T_g=2000\) K, \(A=1\), \(h=0.1\)):

| Quantity | Independent | Design L253–260 |
|---|---:|---:|
| \(u\) | 0.6762681040239569 | 0.6762681040230643 |
| \(P_i\) | 0.017068850876694148 Pa | 0.017068850876654978 Pa |
| \(J\) | \(1.76268104023957\times10^{-9}\) | \(1.7626810402306426\times10^{-9}\) |
| Surface \(C_M\) | 0.3889494462919959 | 0.3889494462914155 |
| Endpoint \(P_g=0.1\): \(F(N_T)\), \(P_i\), \(P_b(N_T)\) | \(3.5256\times10^{-8}\), 0.091685537382, 0.03305785123966942 | match |

\(P_i\neq P_m\) with both bulk phases present. Stoichiometry \(\Delta n_{\mathrm{Fe}}=2d\), \(\Delta n_{\mathrm{FeO}}=-2d\), \(\Delta n_{\mathrm{Fe_2O_3}}=0\) retained (L79–84). Disproportionation explicitly excluded (L274–280).

**Judgment:** fix is **correct**, not just present. Infinite-conductance premise withdrawn for the right physical reason.

### P1 — hourly accuracy (backward Euler over 3600 s) → **FIXED, correct**

**r2 finding:** stable ≠ accurate; at \(z=1\) BE transfers 0.5 vs exact \(1-e^{-1}\approx0.632\) (20.9% low); shrinking 1-vs-2-vs-4 cannot authorize unconditional hourly BE.

**r3 claim (quotes):**

- L301: BE residual “is no longer the r3 acceptance equation.”
- L311: “The old shrinking 1-vs-2-vs-4 difference cannot authorize an hourly step.”
- L323–334: exponential local-Jacobian step \(d_{j+1}=d_j+\frac{1-e^{-\lambda_j h}}{\lambda_j}f(d_j)\) with `-expm1`.
- L348–373: nonlinear local truncation \(\frac{h^3}{6}f''f^2+O(h^4)\); second-order global; “Exactness for a linear law does not justify one unconditional step for a nonlinear law.”
- L451–504: whole-hour refinement \(N=1,2,\ldots,256\), **two successive** mole+interface comparisons, independent reference tolerances, overshoot/inventory/nonconvergence rejection with pre-hour ledger preserved.

**Independent check:**

- Linear BE error table L317–319 reproduced exactly (rel % 0.4942244 / 20.9011647 / 0.9900990).
- Dimensionless ideal-M2 fixture: \(\lambda_0=0.6819757418311362\) matches design 0.6819757418311361.
- Re-ran the embedded acceptance integrator **including** the equilibrium-overshoot guard (`f\cdot f_{\mathrm{end}}<0`):

| \(z\) | Accepted \(N\) | Evals | Abs transfer err | Iface err (dex) |
|---:|---:|---:|---:|---:|
| 0.01 | 4 | 10 | \(1.876\times10^{-11}\) | \(8.952\times10^{-12}\) |
| 1 | 16 | 36 | \(2.367\times10^{-7}\) | \(1.362\times10^{-7}\) |
| 100 | 128 | 263 | \(\sim10^{-16}\) | \(\sim10^{-16}\) |

Matches design L545–547 exactly (overshoot rejects \(N\le16\) at \(z=100\), forcing acceptance at 128 / 263). Single nonlinear BE relative transfer errors 0.494250% / 20.914672% / 0.984687% match L551. BE-coefficient mutation fails to converge at \(z=1\) through 256 (matches mutation intent).

Error-bound claim for the nonlinear law holds as a local O(\(h^3\)) / global second-order statement on a smooth branch; design correctly does **not** treat that as a license for one unconditional hour — refinement + reference + overshoot rejection are the acceptance.

**Judgment:** fix is **correct**.

### P2 — root bound (164 vs 166 / 520/529) → **FIXED, correct**

**r2 finding:** L89 omitted final accepted-amount eval at `core.py:5389`; without early convergence one amount solve is \(2+80+1=83\); two solves **166**.

**r3 claim (quotes):**

- L587–593: “The r2 amount-root count omitted `core.py:5389`… \(2+80+1=83\)… Two amount solves allow **166**… The corrected count does not authorize accepting an unconverged amount root.”
- L595–612: replacement has no amount roots; \(\sum_{N=1,2,\ldots,256}(N+1)=511+9=520\) interior; with capped successor \(\sum(N+2)=529\).

**Independent check:** at SHA `0ba4fb428`, `core.py:5389` is `_, root = residual_for_amount(amount)` after the bisection when `accepted_root is None` — the omitted +1. Arithmetic \(511+9=520\), \(511+18=529\) verified. Fixture eval counts (10 / 36 / 263) sit inside the bound. Nonconvergence fails acceptance and leaves the ledger unchanged (L504, chunk 7 L883).

**Judgment:** fix is **correct**.

### Minor — refresh metadata → **FIXED, correct**

**r2 finding:** L164 named only three refreshed fields; also refresh `q_kress_at_equality`, authority, certification, reason.

**r3 claim (quotes):**

- L697–705: refresh replaces `melt_regime`, `equality_fO2_log`, `lower_bound_fO2_log`, `q_kress_at_equality` (cleared outside M2 diagnostic), melt authority, certified domain/band, certification status, and reason; does **not** replace exchange-owned fields; no passive/interface root in refresh.
- Chunk 8 L887–889: acceptance M5→M2 updates equality + M2 diagnostic metadata; remove FeO → M3 clears equality/ratio and replaces authority/certification/reason; mutations omit refresh / omit metadata refresh.

**Judgment:** fix is **correct** and complete relative to the minor.

---

## 2. Re-derived physics (summary)

### Finite M2 film
Surface inventory \(u\in[0,N_T]\), \(P_b(u)=10^{\mathrm{IW}+2\log_{10}a_{\mathrm{FeO}}(u,P_b)}\), root \(F(u)=\alpha(P_g-P_b)-B(u-N)\). Unique interior root when \(P_b'>0\) because \(F'<0\). Ideal \(C_M=n/[4(1-a)]\) finite for \(0<a<1\). Endpoint complementarity verified numerically. Jacobian for M2 (L429–447) uses the same \(P_b'(u)\) branch; Kress ferric derivative is not substituted on M2 (L449).

### Exponential hourly step
Exact for linear relaxation. Nonlinear LTE \(\frac{h^3}{6}f''f^2\). Runtime two-successive mole (\(\le0.1(10^{-12}+10^{-3}|d|)\)) and interface (\(\le10^{-4}\) dex) gates plus absolute reference tolerances. Overshoot / inventory / nonconverged root / exhausted refinement reject without publishing; pre-hour ledger preserved. Independently reproduced the design’s runnable fixture including mutation rejection.

### 520 / 529
Worst-case sum over all refinement levels; amount bisections zero. Correct.

---

## 3. Measured \(z_0\sim0.35\) vs old `tau_s`

**r3 (L561–581):** start Jacobian \(z_0=3600\lambda(0)\) from the **same** finite interface law (surface \(q_i\), conductances, active \(\beta=dP_g/dd\)). Lunar hero hours 66/72/78 at 1300 °C: \(z_0\approx0.3518\) while existing `3600/tau_s` was 1083–3240. Floor active \(\Rightarrow\beta=0\); old diagnostic treated tiny headspace-floor capacity as if it were the active gas-pressure derivative — that explains the bogus 256-substep request.

RH03 hours 1–3: \(z_0\approx0.354\) with calculated start transport pressures ~9009 / 8419 / 7891 bar (not the commanded 13 mbar). Design correctly flags this as the separate headspace-pressure defect (round 12), outside redox scope, and does not certify it.

Neither sampled finite case had retained metal — ferric finite law, not implemented r3 M2. Finite-M2 coverage is the ideal fixture above. Jacobian formula is the derivative of the same film residual used for the root. **Agree.**

---

## 4. Chunks 1–5 (stop flags?)

Chunks 1–5 did not change substantively r2→r3; they are being implemented now. Tip `9277ef6ed` already contains chunk 1 (“Align Fe redox noop threshold” + “Move redox threshold to shared module”); provider uses shared `OXYGEN_RESERVOIR_NOOP_MOL` (no separate class `NOOP_MOL` attribute — equivalent intent).

| Chunk | Coherent? | Derived acceptance + failing mutation? | Stop? |
|---|---|---|---|
| 1 One noop | Yes (already landed) | Trace `5e-14` vs `1e-15`; restore `1e-12` | No |
| 2 Unavailable buffer return at 7103–7106 | Yes; leaves 7437–7459 for chunk 4 | Absent equality, not `−8`; remove return → reproduce gas-owned | No |
| 3 Vapour reads committed interface | Yes; independent of M2/hour physics | Zero/nonzero commit; no root increment; restore diagnostic re-entry | No |
| 4 Mol predicates + mole-log inverse; delete 7437–7459 | Yes; order after chunk 2 is stated | `5e-7/0.5` and `1e-14/1000` fixtures; restore ε / q-inversion mutations | No |
| 5 Bounds + absence (option B) with all M3-only guards | Yes; Q1-B default retained | M4 `float(None)` mutation at 8111→8125 | No |

**Nothing in chunks 2–5 should stop implementation.** M2 film and exponential hour are chunks 6–7; their r3 fixes do not retarget 2–5.

---

## 5. Residual notes (non-blocking)

- Author prose correctly labels itself as “not an independent confirmation verdict” (L17); this review is that verdict.
- SciPy DOP853 was unavailable on this VPS seat; reference transfers were cross-checked with a fine exponential trajectory and match the published design references to \(\sim10^{-14}\)–\(10^{-17}\). Overshoot-gated converge counts match without SciPy.
- Chunk 1 acceptance text still names `BuiltinFeRedoxRespeciationProvider.NOOP_MOL`; tip uses the shared constant directly. Documentary only; chunk already landed.
- CPU ceiling after chunk 7 remains a Studio measurement (L899); not in scope for this confirmation.

---

## Deliverables

- VPS: `/workspace/ferry-inbox/from-empirical/REVIEW-redox-design-r3-confirm-2026-09-30.md`
- Dropbox from-empirical: (copied via CopyFromBox)
- Mailbox batch: `ferry/reviews/REVIEW-redox-design-r3-confirm-2026-09-30.md` on `empirical/reviews-batch-zv-2026-09-22` if standing push path used

**VERDICT: CONVERGED**
