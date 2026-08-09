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
CrO3`): 0 of 11 now evaluate to the wrong interval. Pre-fix `Mg2(g)` was wrong by ~3.5 MJ/mol.

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

### sulfliq — `~/Repos/sulfliq` @ `89d345a`

| # | patch | STATUS | note |
|---|---|---|---|
| 0001 | `cmake-python-executable` | **applied** (local checkout) | setup.py must pass `Python_EXECUTABLE` so FindPython does not link newest Homebrew CPython |

**0001** fixes the ABI mismatch class (PyPI wheel / naive `pip install .` on multi-Python macOS hosts produces `SulfLiq.cpython-314` under a 3.12 site-packages tree). Same pain as ThermoEngine issues #9/#12/#27. Submit upstream to ENKI-portal/sulfliq.

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
