# Engine patches

Local fixes and features for the third-party thermodynamic engines, held here **pending
upstream adoption**. The engine checkouts live outside this repo (`../VapoRock`,
`../ThermoEngine`, `../PySulfSat`), so their working trees are not version-controlled by us —
which means an unpatched local edit is invisible drift. This directory is the tracked record.

**Rule: no engine edit lives only in a sibling working tree.** If it matters, it is a patch here.

## Why this exists

On 2026-08-08 an audit found four species added to VapoRock's gas CSVs and a matching
`equil.py` edit sitting uncommitted in `../VapoRock`, with no record anywhere in this repo. A
measurement written against the *upstream* table reported a defect the working tree had already
fixed. That is the failure mode this directory prevents.

## Layout

```
patches/
  <engine>/
    UPSTREAM.pin                 # remote URL + base SHA the patches apply to
    NNNN-<slug>.patch            # ordered, each independently revertible
  scripts/enginepatch.sh         # one tool, three verbs: apply | refresh | verify
```

Patches are ordered and cumulative: `0002` applies on top of `0001`.

## Inventory

### vaporock — `../VapoRock` @ `0159678`

| # | patch | status | ref |
|---|---|---|---|
| 0001 | `mn-ni-co-carriers` | local, not submitted | INVENTORY §2.3 |
| 0002 | `janaf-multi-interval-closure` | local, **submit upstream** | `b-153` |

**0001 — Mn/Ni/Co/NiO gas carriers.** Adds Mn/Ni/Co to `Vapor.atom_mass`, the four gas rows to
all three JANAF CSVs, and MnO/NiO/CoO to `_get_melt_oxides_comp`. MELTS already carries
`MnSi0.5O2`, `NiSi0.5O2`, `CoSi0.5O2` endmembers, so no new mixing parameters were needed —
this is gas-side data plus the melt-oxide basis rows. Verified: atom balance clean across all
three databases (13 oxides, zero unbalanced species). **Both halves are load-bearing** — adding
the CSV rows without the oxide-basis rows makes `_get_rxn_coefs`' `lstsq` return an
atom-unbalanced reaction with residual 1.0, silently, because VapoRock keeps `out[0]` and
discards `out[1]`.

**0002 — multi-interval Shomate closure.** `_calc_gibbs_species_JANAF` built its
`np.piecewise` branches as `lambda T: self._janaf_G(T, icoef)` inside the interval loop. Python
closures capture the variable, so every branch resolved to the **last** interval's
coefficients. Harmless for single-interval `JANAF0`; corrupting for the multi-interval
`JANAF`/`JANAF-full` databases — i.e. exactly the rows carrying coverage above ~2000 K. Fixed
with default-argument capture. Also removes a leftover `Mg2(g)` debug `print`.

Verified on all 11 multi-interval species (`O2, Mg, Mg2, AlO, AlO2, SiO, Si2, K, CrO, CrO2,
CrO3`): 0 of 11 now evaluate to the wrong interval. Pre-fix `Mg2(g)` worst absolute error: **5.838 MJ/mol** (fresh 2026-08-09 re-verification; an earlier note said ~3.5).

### thermoengine — `../ThermoEngine` @ `df7a5f4`

| # | patch | STATUS | note |
|---|---|---|---|
| 0001 | `comp-hashability` | **applied** | local, not submitted |
| 0002 | `mu-lifetime` | **unapplied** | see finding below |

**0001** adds `__hash__` to `chem_library.Atom` and `unsafe_hash=True` to the `Comp` dataclass
family, so compositions can be used as dict keys / set members.

**0002 — found unapplied when this directory was created (2026-08-08).** The first `verify`
run flagged it. Checked against the checkout at `df7a5f4`: `muComponentsWrapper` (the patched
form) appears **0 times**, so the fix is neither applied locally nor adopted upstream. The
patch itself covers **all 11 `.m` files** carrying the unfixed `double *muComponents = [[self
getChemicalPotential…]]` pattern (an earlier revision of this note wrongly called it partial —
verified 2026-08-09 by reading the patch hunks: 11 distinct `+++ b/src/*.m` targets).

Left unapplied deliberately. These are ObjC sources behind a shipped compiled extension, so
applying the patch without rebuilding PhaseObjC changes nothing, and a rebuild is a larger
decision than a patch-directory tidy-up. Recorded rather than silently "fixed". If PhaseObjC
is ever rebuilt, apply this first. (2026-08-09 upstream recon: ObjC rebuilds are a dying path —
ThermoEngineLite is the active successor — so the realistic upstream play is filing the defect
writeup as an issue, keeping this patch as our local record.)

Narrative for both, plus the oxygen-buffer defect, stays in `docs/thermoengine-patches.md` —
that file is the upstream **contribution writeup**; this directory holds the applicable diffs.

### STATUS files

Each engine dir carries a `STATUS` file mapping patch → `applied | unapplied | upstreamed`.
`verify` skips anything marked `unapplied`, so a deliberately-parked patch does not cry wolf
forever — but the state is explicit and reviewable rather than implied by absence.

### pysulfsat — `../PySulfSat` @ `ed7c4a0`

Clean. No local patches.

### alphamelts — bundled 2.3.1 arm64 artifact / upstream `957b8f5`

The bundled executable crashes on gate-passing two-component Na2O-SiO2 and
K2O-SiO2 inputs. `patches/alphamelts/` holds the pinned minimal reproduction
and an owner-gated upstream bug-report draft. No engine patch exists: the
upstream source is public, but no source checkout is present beside this repo;
the local install is the compiled `alphamelts-app-2.3.1-macos-arm64` bundle.
The simulator refuses the reproduced binary boundary before launch and catches
other signal exits as typed diagnostics.

alphaMELTS is AGPL-3.0. If a patched executable or source fork is redistributed,
the unresolved distribution-posture decision is `q-004`; this record does not
decide it.

### sulfliq — `~/Repos/sulfliq` @ `89d345a`

| # | patch | STATUS | note |
|---|---|---|---|
| 0001 | `cmake-python-executable` | **applied** (local checkout) | setup.py must pass `Python_EXECUTABLE` so FindPython does not link newest Homebrew CPython |

**0001** fixes the ABI mismatch class (PyPI wheel / naive `pip install .` on multi-Python macOS hosts produces `SulfLiq.cpython-314` under a 3.12 site-packages tree). Same pain as ThermoEngine issues #9/#12/#27. Submit upstream to ENKI-portal/sulfliq.

Provider status: **staged** — `simulator/melt_backend/sulfliq_matte.py` (a_FeS, r2 reviewed) has
its named consumer in the in-fork S-track (t-549..t-551) and **zero runtime callers until that
lands**; do not read its presence as a wired capability.

## Two-install policy: reference vs production (owner ruling 2026-08-10)

Two VapoRock installs live side by side (t-603 / t-607):

- **production** — the full high-T fork: all patches, the fitted mid-band tables, raised
  temperature bands. This is what the simulator runs; the whole point is useful numbers
  above 1950 K.
- **reference** — upstream at pin + **bugfix patches only**. It exists to backcheck the fork
  over the mainline-supported band (1350–1950 K). A *pristine* reference would be the wrong
  reference: the fork differs from mainline by both correctness fixes and capability
  extensions, and pristine A/B conflates "we fixed a bug" with "we extended the range" —
  the capability delta is the only thing the A/B should isolate.

**Classification rule:** a patch belongs in the REFERENCE install **iff** it corrects
incorrect behaviour in something mainline already claims to do; it stays
**PRODUCTION-ONLY iff** it extends what mainline does.

Current classification (confirm at build time):

| patch | install class | rationale |
|---|---|---|
| vaporock 0002 `janaf-multi-interval-closure` | reference | bugfix — mainline claims multi-interval evaluation |
| vaporock 0003 `tmax-domain-check` (when adopted) | reference | makes mainline honest about its own limits; raises none |
| thermoengine 0001 `comp-hashability` | **NEITHER as-is** (milestone sweep P1-4) | the classic `unsafe_hash=True` form is PROVEN BROKEN — `RecursionError` on `hash(OxideMolComp)`/`hash(ElemMolComp)` via the `elem_comp=self` self-reference (T1/port-status.md, verified live). Reference (and eventually production) needs a `0001b` explicit-`__hash__` variant mirroring the T1 TELite fix (hash the non-zero composition payload only). Classic 0001 stays applied locally as historical record but must not enter the reference build or the upstream bundle |
| thermoengine 0002 `mu-lifetime` | reference, once ObjC rebuild unparked | bugfix |
| thermoengine spinel-fix (W4) | reference | bugfix — silent infinite spin |
| sulfliq 0001 `cmake-python-executable` | reference | build fix |
| vaporock 0001 `mn-ni-co-carriers` | **production-only** | adds species mainline does not have |
| fitted mid-band tables (11 rock-formers) | **production-only** | capability extension |
| `VAPOROCK_T_MAX_K` raises | **production-only** | capability extension |

**A/B invariant — required artifact of every raise:** every H5 band raise (1950→2200→2500→3000)
includes a fork-vs-reference A/B over 1350–1950 K on the rail crosscheck fixtures, proving the
band we have always trusted did not move. Honest consequence of the classification: **Mn/Ni/Co
cannot be A/B'd at all** — the reference has no such rows. The A/B artifact must state that
limit explicitly, never report those species as silently agreeing.

**Identity/isolation (OWNER SIMPLIFICATION 2026-08-10, seq 40 — supersedes two earlier designs):**
register **ONE engine at a time** and diff the A/B **outside the software**: run the fork, capture
artifacts; swap the registration in `engines/engines.local.toml`; run the reference; diff
externally. This *deletes* the isolation problem rather than solving it — no dual registration,
no namespaces, no in-software parity harness. (History, kept so neither error recurs: version
strings were first proposed as isolation — WRONG, the cache-identity contract makes engine version
metadata and FORBIDDEN key material, so they isolate nothing; namespace/store separation was then
proposed — correct but heavier than needed once single-registration retired dual installs.)

**The one surviving constraint (runbook, not architecture — OWNER-FINAL FORM):** because the
engine-result cache key is the melt input vector ONLY, *sequential* A/B legs sharing one cache
store collide — the second leg gets served the first engine's cached rows and the A/B silently
compares an engine against itself. **Owner-final procedure: before each A/B leg, DELETE the engine
result cache by hand** — the store at `CachedRealConfig.db_path` (`simulator/backends.py`;
`reduced_real_cache.db_path` in config, plus optional `read_only_base_db_path` layering) — then
register the leg's engine, run, capture artifacts, and diff externally. (Pointing legs at separate
db_paths achieves the same isolation and is acceptable where hand-deletion is impractical, e.g.
scripted studio runs — but manual deletion is the canonical form.) Holds on the studios too.
THIS SECTION IS THE CANONICAL A/B RUNBOOK; HT-PLAN and the task notes defer to it.

## Working rules

1. **Capture before you measure.** Any script that reproduces engine internals by hand (a
   constant table, a species list) must import the live module instead, or it will measure the
   upstream version and report phantom defects.
2. **Pin the base.** `UPSTREAM.pin` records the SHA the patch applies to. On engine upgrade,
   re-apply, resolve, and re-capture — do not carry a patch forward blind.
3. **Each patch does one thing** and is independently revertible, so a rejected upstream
   submission does not strand the rest.
4. **Record submission status** in the table above. A patch that has been upstreamed and
   released gets deleted here, not left to rot.
5. **Physics-affecting patches need a store task** (`b-`/`t-`) and the usual review gate. `0002`
   is `b-153`.

## Known gap this directory does not close

VapoRock **does not enforce its own declared `T_max`**. Single-interval species extrapolate
their Shomate polynomial silently — `O2(g)`, declared to 2000 K, returns a smooth finite
−3,195,034 J/mol at 10,000 K. This is the mechanism behind the 2026-07-31 probe's
"fabricates, does not refuse" verdict, and it is why `VAPOROCK_T_MIN_K/T_MAX_K = 1350/1950`
exists as an external gate in `simulator/melt_backend/vaporock.py`. A future patch should make
out-of-domain evaluation typed rather than silent; until then the external gate is the only
thing standing between us and confident nonsense.
